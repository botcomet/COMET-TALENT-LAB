"""Missions, exigences et grilles : orchestration des règles métier du domaine avec la persistance."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..domain import grid as gd
from ..domain import lexicon as lx
from ..domain.brief import analyze_brief
from ..domain.call_facts import extract_facts
from ..domain.enums import Category, SourceKind, SOURCE_RANK
from ..domain.requirements import Conflict, Req, new_id, resolve
from ..models import Grid, KnowledgeEntry, Mission, MissionSource, Proposal, Requirement, User, now

SOURCE_KIND_MAP = {k.value: k for k in SourceKind}
SOURCE_KIND_MAP["note_brief_client"] = SourceKind.OFFICIAL_BRIEF           # note du recruteur sur un brief : rang du brief tant que le client ne l'a pas validé
EDITABLE_FIELDS = ("label", "category", "dimension", "kind", "terms", "scope_terms", "depth_required", "min_years", "recency_window_years",
                   "recency_sensitive", "rationale", "clarification_question", "quote", "source_kind", "params")


def _bad(msg: str, code: int = 422) -> HTTPException:
    return HTTPException(code, msg)


# ----------------------------------------------------------------- exigences
def req_from_row(r: Requirement) -> Req:
    return Req.from_dict(r.data)


def _save_req(db: Session, mission_id: str, r: Req, *, row: Requirement | None = None) -> Requirement:
    if row is None:
        row = Requirement(id=r.id, mission_id=mission_id, key=r.key, status=r.status, data=r.to_dict())
        db.add(row)
    else:
        row.key, row.status, row.data = r.key, r.status, r.to_dict()
        row.version += 1
    db.flush()
    return row


def all_reqs(db: Session, mission_id: str) -> list[Req]:
    return [req_from_row(r) for r in db.scalars(select(Requirement).where(Requirement.mission_id == mission_id).order_by(Requirement.created_at))]


def effective(db: Session, mission_id: str) -> tuple[list[Req], list[Conflict]]:
    return resolve(all_reqs(db, mission_id))


def create_mission(db: Session, user: User, client: str, title: str, brief_text: str, *, source_date: date | None = None, author: str = "") -> Mission:
    if not title.strip():
        raise _bad("Le titre de la mission est obligatoire.")
    if len(brief_text.strip()) < 40:
        raise _bad("Le descriptif est trop court pour être analysé (40 caractères minimum) : coller le brief complet.")
    m = Mission(owner_id=user.id, client=client.strip(), title=title.strip())
    db.add(m)
    db.flush()
    audit.log(db, user.id, "mission.create", "mission", m.id, m.id, title=title[:120])
    _ingest_source(db, user, m, "brief_officiel", brief_text, source_date=source_date, author=author or user.display_name, label="Brief initial")
    return m


def _ingest_source(db: Session, user: User, m: Mission, kind: str, text: str, *, source_date: date | None, author: str, label: str = "") -> MissionSource:
    src = MissionSource(mission_id=m.id, kind=kind, label=label or kind.replace("_", " "), author=author, source_date=source_date, text=text, created_by=user.id)
    db.add(src)
    db.flush()
    audit.log(db, user.id, "source.add", "mission_source", src.id, m.id, kind=kind, characters=len(text))
    sk = SOURCE_KIND_MAP.get(kind, SourceKind.OFFICIAL_BRIEF)
    existing = {(r.key): r for r in all_reqs(db, m.id) if r.status == "active"}
    analysis = analyze_brief(m.title, text, source_ref=src.id, source_kind=sk, source_date=source_date or date.today(), source_author=author)
    created = 0
    for r in analysis.requirements:
        same = existing.get(r.key)
        if same and same.category == r.category and same.depth_required == r.depth_required and same.source_kind == r.source_kind:
            continue                                                     # information déjà connue : pas de doublon
        r.proposed_by = "system"
        if same and r.rank <= 2 and r.category != same.category:        # une précision client plus prioritaire : proposition de remplacement à valider
            r.supersedes = None                                          # le remplacement explicite est une décision humaine (jamais implicite)
        _save_req(db, m.id, r)
        created += 1
    if kind == "brief_officiel" and m.analysis is None:
        m.analysis = {"role_family": analysis.role_family, "role_label": analysis.role_label, "context": analysis.context, "objectives": analysis.objectives,
                      "activities": analysis.activities, "technologies": analysis.technologies, "modalities": analysis.modalities,
                      "missing_info": analysis.missing_info, "warnings": analysis.warnings, "real_work_summary": analysis.real_work_summary}
    elif kind != "brief_officiel":
        a = dict(m.analysis or {})
        a["modalities"] = {**a.get("modalities", {}), **{k: v for k, v in analysis.modalities.items() if k not in a.get("modalities", {})}}
        have = set(a["modalities"])
        a["missing_info"] = [x for x in a.get("missing_info", []) if x["field"] not in have]
        a["warnings"] = list(dict.fromkeys([*a.get("warnings", []), *analysis.warnings]))
        m.analysis = a
    # propositions issues d'un retour client / d'entretien (apprentissage — jamais appliquées automatiquement)
    if kind in ("retour_entretien", "client_precision_validee"):
        _propose_lesson(db, user, m, text)
    audit.log(db, user.id, "requirements.proposed", "mission", m.id, m.id, source=src.id, created=created)
    return src


_NEG_FEEDBACK = ("insuffisant", "pas assez", "manque", "trop faible", "refuse", "refusé", "ne correspond pas", "un peu juste", "pas suffisamment")


def _propose_lesson(db: Session, user: User, m: Mission, text: str) -> None:
    from ..domain.text import fold
    f = fold(text)
    if not any(fold(w) in f for w in _NEG_FEEDBACK):
        return
    for sk in lx.detect_skills(text, prune_nested=True):
        if sk.note:
            fam = lx.detect_role_family(m.title)
            exists = db.scalar(select(KnowledgeEntry).where(KnowledgeEntry.origin_mission_id == m.id, KnowledgeEntry.title.like(f"%{sk.label}%")))
            if exists:
                continue
            db.add(KnowledgeEntry(
                kind="enseignement_retour_client", title=f"{sk.label} : distinguer la mention de la pratique réelle",
                body=f"{sk.note}\n\nOrigine : retour client sur la mission « {m.title} ». Enseignement proposé, spécifique à ce client tant qu'il n'est pas généralisé et validé par un Talent Manager.",
                role_family=fam.key if fam else "", tags=[sk.key], client_specific=True, status="proposed", origin_mission_id=m.id, author_id=user.id))
            audit.log(db, user.id, "knowledge.proposed", "mission", m.id, m.id, skill=sk.key)


def add_source(db: Session, user: User, m: Mission, kind: str, text: str, *, source_date: date | None, author: str, label: str = "") -> MissionSource:
    if kind not in SOURCE_KIND_MAP:
        raise _bad("Type de source inconnu pour une exigence du poste.")
    if len(text.strip()) < 10:
        raise _bad("Le texte de la source est vide ou trop court.")
    if kind in ("client_imperatif_confirme", "client_precision_validee") and not author.strip():
        raise _bad("Indiquer l'auteur de la précision (représentant client autorisé) : la provenance est obligatoire.")
    return _ingest_source(db, user, m, kind, text, source_date=source_date or date.today(), author=author, label=label)


def propose_from_text(db: Session, user: User, m: Mission, text: str, *, kind: str, author: str, speaker_map: dict[str, str] | None = None) -> list[Proposal]:
    """Notes de brief client / feedback : l'IA ne modifie rien, elle PROPOSE des changements d'exigence à confirmer."""
    facts = [f for f in extract_facts(text, kind, speaker_map=speaker_map) if f.kind == "client_requirement" and f.topic_key]
    props: list[Proposal] = []
    for f in facts:
        p = Proposal(mission_id=m.id, kind="requirement_change", created_by=user.id,
                     payload={"topic_key": f.topic_key, "topic_label": f.topic_label, "category_hint": f.requirement_hint["category_hint"],
                              "relayed": f.requirement_hint["relayed"], "statement": f.statement, "author": author, "source_kind": kind},
                     explanation=f"« {f.statement} » — exigence du poste (pas une compétence du candidat). {f.why_verify}",
                     consequences=["Création d'une nouvelle version de grille à valider.", "Les candidats déjà évalués devront être réévalués sur la nouvelle version."])
        db.add(p)
        props.append(p)
    db.flush()
    if props:
        audit.log(db, user.id, "proposals.created", "mission", m.id, m.id, count=len(props))
    return props


def apply_requirement_proposal(db: Session, user: User, m: Mission, prop: Proposal) -> Requirement:
    pl = prop.payload
    key = pl["topic_key"]
    sk = lx.skill(key.split(":", 1)[1])
    cat = {"imperatif": Category.IMPERATIF, "fortement_differenciant": Category.DIFFERENCIANT, "souhaitable": Category.SOUHAITABLE,
           "a_clarifier": Category.A_CLARIFIER}[pl["category_hint"]]
    # confirmée par un recruteur : « précision client validée » (rang 2) ; la provenance « relayée » reste tracée dans l'auteur et l'extrait
    src_kind = SourceKind.CLIENT_CLARIFICATION
    cur = next((r for r in all_reqs(db, m.id) if r.key == key and r.status == "active"), None)
    dim, kind = ("technique", "skill") if sk and sk.kind in ("tech", "product") else (("responsabilite", "activity") if sk and sk.kind == "activity" else ("contexte_metier", "domain"))
    r = Req(id=new_id(), key=key, label=pl["topic_label"] or (sk.label if sk else key), category=cat, dimension=dim, kind=kind,
            terms=list(sk.aliases) if sk else [pl["topic_label"]], source_kind=src_kind, source_ref=prop.id, source_date=date.today(),
            source_author=("relayé par " if pl.get("relayed") else "") + pl.get("author", ""), quote=pl["statement"][:300],
            rationale="Précision issue d'un échange" + (" relayée par un recruteur" if pl.get("relayed") else "") + ", confirmée par le recruteur ; à faire confirmer par le client si ce n'est pas déjà fait.",
            validated=True, validated_by=user.id, proposed_by="assistant")
    if cur and cur.depth_required == "advanced":
        r.depth_required = "advanced"
    if cur and cur.category != r.category and cur.rank > 1:
        r.supersedes = cur.id            # remplacement explicite décidé par le recruteur ; jamais d'un impératif client CONFIRMÉ (rang 1) : conflit signalé à arbitrer
    return _save_req(db, m.id, r)


def update_requirement(db: Session, user: User, m: Mission, req_id: str, changes: dict[str, Any]) -> Requirement:
    row = db.get(Requirement, req_id)
    if row is None or row.mission_id != m.id:
        raise HTTPException(404, "Exigence introuvable.")
    r = req_from_row(row)
    before = {k: getattr(r, k) for k in changes if k in EDITABLE_FIELDS}
    for k, v in changes.items():
        if k not in EDITABLE_FIELDS and k != "status":
            continue
        if k == "category":
            v = Category(v)
        if k == "source_kind":
            v = SourceKind(v)
        if k == "status":
            if v not in ("active", "rejected"):
                raise _bad("Statut invalide.")
        setattr(r, k, v)
    if r.category == Category.ELIMINATOIRE:
        if r.source_kind not in (SourceKind.CLIENT_CONFIRMED_IMPERATIVE, SourceKind.CLIENT_CLARIFICATION):
            raise _bad("Un critère éliminatoire doit être explicitement confirmé par le client : renseigner la source « impératif client confirmé » ou « précision client validée ».")
        if not r.quote.strip():
            raise _bad("Un critère éliminatoire doit citer l'extrait de la confirmation client.")
    if r.depth_required not in ("practice", "advanced"):
        raise _bad("Profondeur invalide.")
    r.validated, r.validated_by = True, user.id
    _save_req(db, m.id, r, row=row)
    audit.log(db, user.id, "requirement.update", "requirement", r.id, m.id, key=r.key,
              before={k: (v.value if hasattr(v, "value") else v) for k, v in before.items()},
              after={k: (getattr(r, k).value if hasattr(getattr(r, k), "value") else getattr(r, k)) for k in before})
    return row


def add_requirement(db: Session, user: User, m: Mission, data: dict[str, Any]) -> Requirement:
    label = str(data.get("label", "")).strip()
    if not label:
        raise _bad("Libellé obligatoire.")
    sk = lx.skill(str(data.get("skill_key", ""))) if data.get("skill_key") else None
    if sk is None:
        for cand in lx.detect_skills(label, prune_nested=True)[:1]:
            sk = cand
    kind = data.get("kind") or ("skill" if not sk else {"activity": "activity", "domain": "domain"}.get(sk.kind, "skill"))
    key = f"{kind}:{sk.key}" if sk else f"custom:{label.lower().replace(' ', '_')[:60]}"
    r = Req(id=new_id(), key=key, label=label, category=Category(data.get("category", "souhaitable")), kind=kind,
            dimension=data.get("dimension") or ("technique" if kind == "skill" else "responsabilite" if kind == "activity" else "contexte_metier"),
            terms=list(data.get("terms") or (sk.aliases if sk else [label])), scope_terms=list(data.get("scope_terms") or []),
            depth_required=data.get("depth_required", "practice"), min_years=data.get("min_years"),
            source_kind=SourceKind(data.get("source_kind", SourceKind.OFFICIAL_BRIEF.value)), source_ref="saisie", source_date=date.today(),
            source_author=user.display_name, quote=str(data.get("quote", ""))[:300], rationale="Ajout manuel par un recruteur.",
            validated=True, validated_by=user.id, proposed_by="user")
    if r.category == Category.ELIMINATOIRE and (r.source_kind not in (SourceKind.CLIENT_CONFIRMED_IMPERATIVE, SourceKind.CLIENT_CLARIFICATION) or not r.quote):
        raise _bad("Un critère éliminatoire doit être confirmé par le client (source et extrait obligatoires).")
    row = _save_req(db, m.id, r)
    audit.log(db, user.id, "requirement.add", "requirement", r.id, m.id, key=key, category=r.category.value)
    return row


def validate_requirements(db: Session, user: User, m: Mission, ids: list[str] | None) -> int:
    n = 0
    for row in db.scalars(select(Requirement).where(Requirement.mission_id == m.id)):
        r = req_from_row(row)
        if r.status != "active" or r.validated or (ids is not None and r.id not in ids):
            continue
        if r.category == Category.ELIMINATOIRE and (r.source_kind not in (SourceKind.CLIENT_CONFIRMED_IMPERATIVE, SourceKind.CLIENT_CLARIFICATION) or not r.quote):
            continue          # jamais validé en bloc : exige la confirmation client tracée
        r.validated, r.validated_by = True, user.id
        _save_req(db, m.id, r, row=row)
        n += 1
    audit.log(db, user.id, "requirements.validate", "mission", m.id, m.id, count=n)
    return n


# ----------------------------------------------------------------- grilles
def grid_from_row(g: Grid) -> gd.Grid:
    d = dict(g.data)
    d.update({"mission_id": g.mission_id, "status": g.status, "content_hash": g.content_hash, "validated_by": g.validated_by, "reason": g.reason})
    return gd.Grid.from_dict(d)


def latest_frozen(db: Session, mission_id: str) -> Grid | None:
    return db.scalar(select(Grid).where(Grid.mission_id == mission_id, Grid.status == "frozen").order_by(Grid.version.desc()).limit(1))


def current_draft(db: Session, mission_id: str) -> Grid | None:
    return db.scalar(select(Grid).where(Grid.mission_id == mission_id, Grid.status == "draft").order_by(Grid.version.desc()).limit(1))


def propose_grid(db: Session, user: User, m: Mission) -> Grid:
    reqs, _ = effective(db, m.id)
    if not any(r.category in (Category.ELIMINATOIRE, Category.IMPERATIF, Category.DIFFERENCIANT, Category.SOUHAITABLE) for r in reqs):
        raise _bad("Aucune exigence notable à noter : valider ou ajouter des exigences d'abord.")
    last = db.scalar(select(func.max(Grid.version)).where(Grid.mission_id == m.id)) or 0
    draft = current_draft(db, m.id)
    frozen = latest_frozen(db, m.id)
    if frozen and (draft is None or draft.version <= frozen.version):
        raise _bad("Une grille figée existe déjà : créer une nouvelle version en expliquant le changement du besoin.", 409)
    new = gd.propose_grid(m.id, reqs, version=draft.version if draft else last + 1)
    if draft:
        draft.data = new.canonical()
        draft.reason = new.reason
        row = draft
    else:
        row = Grid(mission_id=m.id, version=new.version, status="draft", data=new.canonical(), reason=new.reason, created_by=user.id)
        db.add(row)
    db.flush()
    audit.log(db, user.id, "grid.propose", "grid", row.id, m.id, version=row.version)
    return row


def update_grid(db: Session, user: User, m: Mission, grid_id: str, weights: dict[str, int] | None, caps: dict[str, Any] | None,
                thresholds: dict[str, int] | None, recency_window_years: int | None) -> Grid:
    row = db.get(Grid, grid_id)
    if row is None or row.mission_id != m.id:
        raise HTTPException(404, "Grille introuvable.")
    if row.status != "draft":
        raise _bad("Une grille figée ne se modifie pas : créer une nouvelle version.", 409)
    g = grid_from_row(row)
    before = {c.key: c.weight for c in g.scored()}
    if weights:
        for k, w in weights.items():
            c = g.by_key(k)
            if c is None or not c.scored:
                raise _bad(f"Critère noté inconnu : {k}")
            if not isinstance(w, int) or w < 0 or w > 100:
                raise _bad("Un poids est un entier de 0 à 100.")
            c.weight = w
    if caps is not None:
        for k, v in caps.items():
            if k not in g.caps:
                raise _bad(f"Plafond inconnu : {k}")
            if v is not None and not (0 <= int(v) <= 100):
                raise _bad("Un plafond est compris entre 0 et 100.")
            g.caps[k] = None if v is None else int(v)
    if thresholds:
        for k, v in thresholds.items():
            if k not in g.thresholds:
                raise _bad(f"Seuil inconnu : {k}")
            g.thresholds[k] = int(v)
    if recency_window_years is not None:
        g.recency_window_years = int(recency_window_years)
    row.data = g.canonical()
    db.flush()
    audit.log(db, user.id, "grid.update", "grid", row.id, m.id, before=before, after={c.key: c.weight for c in g.scored()})
    return row


def freeze_grid(db: Session, user: User, m: Mission, grid_id: str, allow_unresolved: bool) -> Grid:
    row = db.get(Grid, grid_id)
    if row is None or row.mission_id != m.id:
        raise HTTPException(404, "Grille introuvable.")
    if row.status == "frozen":
        raise _bad("Cette grille est déjà figée.", 409)
    reqs, _ = effective(db, m.id)
    g = grid_from_row(row)
    try:
        gd.freeze(g, reqs, user.display_name, allow_unresolved_clarifications=allow_unresolved)
    except gd.GridError as e:
        raise HTTPException(422, {"message": "La grille ne peut pas être validée.", "errors": e.errors})
    row.data, row.status, row.content_hash = g.canonical(), "frozen", g.content_hash
    row.validated_by, row.validated_at = user.id, now()
    db.flush()
    audit.log(db, user.id, "grid.freeze", "grid", row.id, m.id, version=row.version, hash=g.content_hash)
    return row


def new_grid_version(db: Session, user: User, m: Mission, reason: str) -> tuple[Grid, dict[str, Any]]:
    frozen = latest_frozen(db, m.id)
    if frozen is None:
        raise _bad("Aucune grille figée : valider d'abord la grille initiale.", 409)
    if current_draft(db, m.id) and current_draft(db, m.id).version > frozen.version:        # type: ignore[union-attr]
        raise _bad("Un brouillon de nouvelle version existe déjà : le modifier ou le valider.", 409)
    reqs, _ = effective(db, m.id)
    try:
        new, diff = gd.new_version(grid_from_row(frozen), reqs, reason)
    except gd.GridError as e:
        raise HTTPException(422, {"message": "Version impossible.", "errors": e.errors})
    row = Grid(mission_id=m.id, version=new.version, status="draft", data=new.canonical(), reason=reason, diff=diff, created_by=user.id)
    db.add(row)
    db.flush()
    audit.log(db, user.id, "grid.new_version", "grid", row.id, m.id, version=new.version, reason=reason[:200])
    return row, diff
