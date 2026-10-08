"""Recherches booléennes : génération, retours d'exécution, optimisation, historique (§5, §17.2)."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..domain import boolean as bl
from ..domain.enums import Category
from ..domain.search_optimizer import Feedback, optimize, variant_from_dict
from ..domain.search_plan import STRATEGIES, build_search_set, variant_from_query
from ..models import KnowledgeEntry, Mission, Search, SearchFeedback, User, uid
from . import missions as ms


def platform() -> bl.PlatformProfile:
    s = get_settings()
    return bl.PlatformProfile(key=s.platform, label=s.platform.capitalize(), max_length=s.platform_max_length, target_min=s.result_target_min,
                              target_max=s.result_target_max, too_broad=s.result_too_broad)


def _store(db: Session, user: User, m: Mission, v, *, lineage: str | None, version: int, parent: str | None, modification: str, grid_version: int | None) -> Search:
    row = Search(id=uid(), mission_id=m.id, strategy=v.strategy, lineage=lineage or "", version=version, platform=platform().key, query=v.query,
                 variant=v.to_dict(), explanation=v.explanation, grid_version=grid_version, parent_id=parent, modification=modification, author_id=user.id)
    row.lineage = lineage or row.id
    db.add(row)
    db.flush()
    return row


def generate(db: Session, user: User, m: Mission) -> list[Search]:
    reqs, _ = ms.effective(db, m.id)
    if not [r for r in reqs if r.category != Category.CONTEXTUEL]:
        raise HTTPException(422, "Aucune exigence exploitable : analyser le brief et valider les critères d'abord.")
    ss = build_search_set(m.title, reqs, profile=platform())
    frozen = ms.latest_frozen(db, m.id)
    out: list[Search] = []
    for strat in STRATEGIES:
        v = ss.variants[strat]
        dup = db.scalar(select(Search).where(Search.mission_id == m.id, Search.strategy == strat, Search.query == v.query))
        if dup:
            out.append(dup)
            continue
        out.append(_store(db, user, m, v, lineage=None, version=1, parent=None, modification="Génération initiale à partir des exigences effectives.",
                          grid_version=frozen.version if frozen else None))
    audit.log(db, user.id, "search.generate", "mission", m.id, m.id, count=len(out))
    return out


def custom(db: Session, user: User, m: Mission, query: str, strategy: str = "balanced") -> tuple[Search, list[dict[str, Any]]]:
    issues = bl.validate(query, platform())
    errs = [i for i in issues if i.severity == "error"]
    if errs:
        raise HTTPException(422, {"message": "Requête invalide.", "issues": [i.to_dict() for i in issues]})
    reqs, _ = ms.effective(db, m.id)
    v = variant_from_query(query, reqs, m.title, strategy=strategy if strategy in STRATEGIES else "balanced")
    from ..domain.search_plan import explain
    v.explanation = explain(v, title=m.title, reqs=reqs, notes=["Requête saisie ou modifiée manuellement : groupes rattachés aux exigences quand c'est possible."],
                            excluded=[], core=[t for g in v.groups if g.kind == "role" for t in g.terms], profile=platform())
    row = _store(db, user, m, v, lineage=None, version=1, parent=None, modification="Requête saisie manuellement par un recruteur.", grid_version=None)
    audit.log(db, user.id, "search.custom", "search", row.id, m.id)
    return row, [i.to_dict() for i in issues]


def lineage_queries(db: Session, search: Search) -> list[str]:
    """Toutes les requêtes déjà proposées pour la MISSION (toutes lignées, requêtes complémentaires comprises) : une requête déjà vue n'est jamais rejouée."""
    out: list[str] = []
    for s in db.scalars(select(Search).where(Search.mission_id == search.mission_id)):
        out.append(s.query)
        out.extend((s.variant or {}).get("extra_queries", []))
    return out


def record_feedback(db: Session, user: User, m: Mission, search: Search, payload: dict[str, Any]) -> dict[str, Any]:
    fb = Feedback(result_count=payload.get("result_count"), relevance=payload.get("relevance"), tags=list(payload.get("tags") or []),
                  false_positive_terms=[t.strip() for t in payload.get("false_positive_terms") or [] if t.strip()], missing_skill=(payload.get("missing_skill") or "").strip(),
                  missing_profiles_note=payload.get("missing_profiles_note") or "", notes=payload.get("notes") or "")
    reqs, _ = ms.effective(db, m.id)
    v = variant_from_dict(search.variant)
    v.explanation = search.explanation
    prop = optimize(v, fb, reqs, title=m.title, history=lineage_queries(db, search), profile=platform())
    row = SearchFeedback(search_id=search.id, result_count=fb.result_count, relevance=fb.relevance, tags=fb.tags, false_positive_terms=fb.false_positive_terms,
                         missing_skill=fb.missing_skill, missing_note=fb.missing_profiles_note, notes=fb.notes, diagnosis=prop.diagnosis.to_dict(), created_by=user.id)
    db.add(row)
    new_search = None
    if prop.variant is not None:
        top = max(s.version for s in db.scalars(select(Search).where(Search.mission_id == m.id, Search.lineage == search.lineage)))
        new_search = _store(db, user, m, prop.variant, lineage=search.lineage, version=top + 1, parent=search.id, modification=prop.modification,
                            grid_version=search.grid_version)
        row.resulting_search_id = new_search.id
    db.flush()
    audit.log(db, user.id, "search.feedback", "search", search.id, m.id, result_count=fb.result_count, problem=prop.diagnosis.problem,
              resulting=row.resulting_search_id or "")
    _maybe_learn(db, user, m, search, row)
    return {"feedback_id": row.id, "diagnosis": prop.diagnosis.to_dict(), "modification": prop.modification, "tradeoffs": prop.tradeoffs,
            "native_filters": prop.native_filters, "exhausted": prop.exhausted, "needs_human": prop.needs_human,
            "new_search": new_search}


def _maybe_learn(db: Session, user: User, m: Mission, search: Search, fb: SearchFeedback) -> None:
    """Apprentissage à partir d'un booléen (§17.2) : un vivier exploitable obtenu après un zéro résultat devient une proposition d'enseignement."""
    s = get_settings()
    if fb.result_count is None or fb.result_count < s.result_target_min or fb.result_count > s.result_too_broad:
        return
    chain = list(db.scalars(select(Search).where(Search.mission_id == m.id, Search.lineage == search.lineage).order_by(Search.version)))
    zero = None
    for st in chain:
        f0 = db.scalar(select(SearchFeedback).where(SearchFeedback.search_id == st.id, SearchFeedback.result_count == 0))
        if f0:
            zero = st
            break
    if zero is None or zero.id == search.id:
        return
    steps = [st.modification for st in chain if st.version > zero.version and st.modification and st.version <= search.version + 1]
    title = f"Recherche débloquée après zéro résultat — {m.title}"
    if db.scalar(select(KnowledgeEntry).where(KnowledgeEntry.origin_mission_id == m.id, KnowledgeEntry.title == title)):
        return
    from ..domain import lexicon as lx
    fam = lx.detect_role_family(m.title)
    db.add(KnowledgeEntry(kind="booleen", title=title, role_family=fam.key if fam else "", tags=["booleen", "zero-resultat"], client_specific=True, status="proposed",
                          origin_mission_id=m.id, author_id=user.id,
                          body=f"La requête « {zero.query} » renvoyait zéro résultat ; après les modifications suivantes, {fb.result_count} résultats exploitables ont été obtenus.\n- "
                               + "\n- ".join(steps) + f"\n\nRequête utile : {search.query}\nPlateforme : {search.platform}. Les exigences client n'ont pas été modifiées."))
    audit.log(db, user.id, "knowledge.proposed", "search", search.id, m.id, kind="booleen")


def save(db: Session, user: User, m: Mission, search: Search, status: str, note: str) -> Search:
    if status not in ("draft", "saved", "useful"):
        raise HTTPException(422, "Statut inconnu.")
    search.status, search.note = status, note
    audit.log(db, user.id, "search.save", "search", search.id, m.id, status=status)
    return search


def list_for_mission(db: Session, m: Mission) -> list[dict[str, Any]]:
    out = []
    for s in db.scalars(select(Search).where(Search.mission_id == m.id).order_by(Search.created_at)):
        fbs = list(db.scalars(select(SearchFeedback).where(SearchFeedback.search_id == s.id).order_by(SearchFeedback.created_at)))
        out.append(search_dict(s, fbs))
    return out


def search_dict(s: Search, fbs: list[SearchFeedback] | None = None) -> dict[str, Any]:
    d = {"id": s.id, "mission_id": s.mission_id, "strategy": s.strategy, "lineage": s.lineage, "version": s.version, "platform": s.platform,
         "query": s.query, "length": len(s.query), "extra_queries": s.variant.get("extra_queries", []), "explanation": s.explanation,
         "groups": [{"id": g["id"], "label": g["label"], "terms": g["terms"], "kind": g["kind"], "protected": g["protected"]} for g in s.variant.get("groups", [])],
         "parent_id": s.parent_id, "modification": s.modification, "status": s.status, "note": s.note, "grid_version": s.grid_version,
         "author_id": s.author_id, "created_at": s.created_at.isoformat()}
    if fbs is not None:
        d["feedbacks"] = [{"id": f.id, "result_count": f.result_count, "relevance": f.relevance, "tags": f.tags, "false_positive_terms": f.false_positive_terms,
                           "missing_skill": f.missing_skill, "notes": f.notes, "diagnosis": f.diagnosis, "resulting_search_id": f.resulting_search_id,
                           "created_at": f.created_at.isoformat()} for f in fbs]
    return d
