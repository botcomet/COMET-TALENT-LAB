"""Routes de l'API COMET Talent Lab (toutes sous /api)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..domain import boolean as bl
from ..domain import coach
from ..domain import grid as gd
from ..domain.enums import Category
from ..models import (Assessment, AuditEvent, CallNote, Candidate, Document, Evidence, EvidenceReview, Grid, KnowledgeEntry, Mission, MissionSource, Proposal,
                      Qualification, Requirement, Search, Share, User)
from ..security import (access_level, clear_session, current_user, issue_session, mission_dep, require_admin, require_mission)
from ..enums_app import CANDIDATE_SOURCE_KINDS
from ..services import assistant as asst
from ..services import export as exp
from ..services import knowledge as kn
from ..services import matching as mt
from ..services import missions as ms
from ..services import searches as sr
from . import schemas as S

router = APIRouter(prefix="/api")

Strategy = Depends(mission_dep("strategie"))
Read = Depends(mission_dep("lecture"))
Edit = Depends(mission_dep("edition"))
Owner = Depends(mission_dep("owner"))


# ----------------------------------------------------------------- sérialisation
def user_dict(u: User) -> dict[str, Any]:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role, "team": u.team, "region": u.region}


def req_dict(r) -> dict[str, Any]:
    d = r.to_dict()
    d["rank"] = r.rank
    return d


def grid_dict(g: Grid) -> dict[str, Any]:
    d = dict(g.data)
    d.update({"id": g.id, "mission_id": g.mission_id, "version": g.version, "status": g.status, "content_hash": g.content_hash, "reason": g.reason, "diff": g.diff,
              "validated_by": g.validated_by, "validated_at": g.validated_at.isoformat() if g.validated_at else None, "created_at": g.created_at.isoformat(),
              "total_weight": sum(c["weight"] for c in g.data["criteria"] if c.get("scored"))})
    return d


def assessment_dict(a: Assessment, full: bool = True) -> dict[str, Any]:
    d = {"id": a.id, "version": a.version, "trigger": a.trigger, "reason": a.reason, "grid_id": a.grid_id, "created_at": a.created_at.isoformat(),
         "score_documented": a.score_documented, "score_potential": a.score_potential, "tier": a.tier, "prev_id": a.prev_id}
    if full:
        d["result"], d["diff"] = a.result, a.diff
    return d


def mission_summary(db: Session, m: Mission, level: str) -> dict[str, Any]:
    owner = db.get(User, m.owner_id)
    frozen = ms.latest_frozen(db, m.id)
    return {"id": m.id, "title": m.title, "client": m.client, "status": m.status, "access": level, "owner": owner.display_name if owner else "",
            "created_at": m.created_at.isoformat(), "updated_at": m.updated_at.isoformat(), "grid_version": frozen.version if frozen else None}


# ----------------------------------------------------------------- santé & authentification
@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@router.post("/auth/dev-login")
def dev_login(body: S.DevLogin, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    if s.auth_mode != "dev" or s.env == "prod":
        raise HTTPException(404, "Introuvable.")
    u = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.active.is_(True)))
    if u is None:
        raise HTTPException(401, "Compte inconnu.")
    issue_session(response, u, s)
    audit.log(db, u.id, "auth.login", "user", u.id, mode="dev")
    return user_dict(u)


@router.post("/auth/logout")
def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    s = get_settings()
    return {**user_dict(user), "auth_mode": s.auth_mode, "env": s.env}


@router.get("/auth/dev-users")
def dev_users(db: Session = Depends(get_db)):
    """Liste des comptes FICTIFS de démonstration — disponible uniquement en mode développement."""
    s = get_settings()
    if s.auth_mode != "dev" or s.env == "prod":
        raise HTTPException(404, "Introuvable.")
    return [user_dict(u) for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.display_name))]


@router.get("/users")
def users(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [{"id": u.id, "display_name": u.display_name, "email": u.email, "region": u.region} for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.display_name))]


@router.get("/config")
def config(user: User = Depends(current_user)):
    s = get_settings()
    return {"max_batch_size": s.max_batch_size, "max_file_mb": s.max_file_mb, "platform": s.platform, "platform_max_length": s.platform_max_length,
            "result_target_min": s.result_target_min, "result_target_max": s.result_target_max, "result_too_broad": s.result_too_broad,
            "very_interesting_min": s.very_interesting_min, "work_view_min": s.work_view_min, "retention_days": s.retention_days,
            "retention_note": "Durée de départ — à valider avec le DPO.", "llm_provider": s.llm_provider,
            "syntax_confirmed": False, "syntax_note": bl.TURNOVER.notes}


# ----------------------------------------------------------------- missions
@router.get("/missions")
def list_missions(scope: str = Query("all", pattern="^(mine|shared|all)$"), db: Session = Depends(get_db), user: User = Depends(current_user)):
    out = []
    shared_ids = {s.mission_id: s.scope for s in db.scalars(select(Share).where(Share.user_id == user.id))}
    q = select(Mission).where(or_(Mission.owner_id == user.id, Mission.id.in_(list(shared_ids) or [""]))).order_by(Mission.updated_at.desc())
    for m in db.scalars(q):
        lvl = "owner" if m.owner_id == user.id else shared_ids[m.id]
        if scope == "mine" and lvl != "owner":
            continue
        if scope == "shared" and lvl == "owner":
            continue
        out.append(mission_summary(db, m, lvl))
    return out


@router.post("/missions", status_code=201)
def create_mission(body: S.MissionIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    m = ms.create_mission(db, user, body.client, body.title, body.brief, source_date=body.source_date, author=body.author)
    db.flush()
    return mission_view(db, m, "owner", user)


def grid_drift(db: Session, m: Mission, reqs) -> dict[str, Any] | None:
    """Le besoin a-t-il changé depuis la grille figée ? Les scores restent calculés sur la grille figée (jamais modifiée en silence) :
    l'écart est SIGNALÉ pour qu'une nouvelle version, motivée et tracée, soit créée."""
    frozen = ms.latest_frozen(db, m.id)
    if frozen is None:
        return None
    try:
        d = gd.diff(ms.grid_from_row(frozen), gd.propose_grid(m.id, reqs, version=frozen.version + 1))
    except Exception:                      # noqa: BLE001 — pas d'exigence notable : rien à comparer
        return None
    changed = [c for c in d["changed"] if c["from"]["category"] != c["to"]["category"] or c["from"]["depth"] != c["to"]["depth"]]
    if not (d["added"] or d["removed"] or changed):
        return None
    return {"from_version": frozen.version, "added": d["added"], "removed": d["removed"], "changed": changed}


def mission_view(db: Session, m: Mission, level: str, user: User) -> dict[str, Any]:
    reqs, conflicts = ms.effective(db, m.id)
    base = mission_summary(db, m, level)
    if level == "strategie":      # stratégie de sourcing seule : exigences (sans extraits) et recherches, jamais de données candidat ni de sources brutes
        base["requirements"] = [{"label": r.label, "category": r.category.value, "dimension": r.dimension, "depth_required": r.depth_required} for r in reqs
                                if r.category != Category.CONTEXTUEL]
        return base
    srcs = db.scalars(select(MissionSource).where(MissionSource.mission_id == m.id).order_by(MissionSource.created_at)).all()
    allr = ms.all_reqs(db, m.id)
    base.update({
        "analysis": m.analysis,
        "sources": [{"id": s.id, "kind": s.kind, "label": s.label, "author": s.author, "source_date": s.source_date.isoformat() if s.source_date else None,
                     "text": s.text, "created_at": s.created_at.isoformat()} for s in srcs],
        "requirements": [req_dict(r) for r in allr],
        "effective_requirement_ids": [r.id for r in reqs],
        "conflicts": [c.__dict__ for c in conflicts],
        "grids": [grid_dict(g) for g in db.scalars(select(Grid).where(Grid.mission_id == m.id).order_by(Grid.version))],
        "grid_drift": grid_drift(db, m, reqs),
        "knowledge_suggestions": kn.suggest_for_mission(db, m, [r.key for r in reqs]),
    })
    return base


@router.get("/missions/{mission_id}")
def get_mission(ctx=Strategy, db: Session = Depends(get_db)):
    m, level, user = ctx
    return mission_view(db, m, level, user)


@router.delete("/missions/{mission_id}", status_code=204)
def delete_mission(ctx=Owner, db: Session = Depends(get_db)):
    m, _, user = ctx
    audit.log(db, user.id, "mission.delete", "mission", m.id, m.id, title=m.title[:100])
    db.delete(m)
    return Response(status_code=204)


@router.post("/missions/{mission_id}/sources", status_code=201)
def add_source(body: S.SourceIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    src = ms.add_source(db, user, m, body.kind, body.text, source_date=body.source_date, author=body.author, label=body.label)
    return {"id": src.id, "mission": mission_view(db, m, "owner", user)}


@router.post("/missions/{mission_id}/brief-notes", status_code=201)
def brief_notes(body: S.BriefNoteIn, ctx=Edit, db: Session = Depends(get_db)):
    """Notes de brief client / feedback : les exigences détectées sont PROPOSÉES (jamais appliquées sans confirmation)."""
    m, _, user = ctx
    props = ms.propose_from_text(db, user, m, body.text, kind=body.kind, author=body.author or user.display_name, speaker_map=body.speaker_map)
    return {"proposals": [proposal_dict(p) for p in props],
            "note": "Ces éléments décrivent le besoin du client, pas des compétences de candidat. Rien n'est modifié tant que tu n'as pas confirmé."}


# ----------------------------------------------------------------- exigences & grille
@router.post("/missions/{mission_id}/requirements", status_code=201)
def add_requirement(body: S.RequirementIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    row = ms.add_requirement(db, user, m, body.model_dump())
    return req_dict(ms.req_from_row(row))


@router.patch("/missions/{mission_id}/requirements/{rid}")
def patch_requirement(rid: str, body: S.RequirementPatch, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    row = ms.update_requirement(db, user, m, rid, body.model_dump(exclude_unset=True))
    return req_dict(ms.req_from_row(row))


@router.post("/missions/{mission_id}/requirements/validate")
def validate_reqs(body: S.ValidateReqs, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    return {"validated": ms.validate_requirements(db, user, m, body.ids)}


@router.post("/missions/{mission_id}/grid/propose", status_code=201)
def propose_grid(ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    return grid_dict(ms.propose_grid(db, user, m))


@router.put("/missions/{mission_id}/grid/{gid}")
def put_grid(gid: str, body: S.GridPatch, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    return grid_dict(ms.update_grid(db, user, m, gid, body.weights, body.caps, body.thresholds, body.recency_window_years))


@router.get("/missions/{mission_id}/grid/{gid}/check")
def check_grid(gid: str, ctx=Read, db: Session = Depends(get_db)):
    m, _, _ = ctx
    row = db.get(Grid, gid)
    if row is None or row.mission_id != m.id:
        raise HTTPException(404, "Grille introuvable.")
    reqs, _ = ms.effective(db, m.id)
    errors, warnings = gd.validate(ms.grid_from_row(row), reqs)
    return {"errors": errors, "warnings": warnings}


@router.post("/missions/{mission_id}/grid/{gid}/validate")
def freeze_grid(gid: str, body: S.GridFreeze, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    return grid_dict(ms.freeze_grid(db, user, m, gid, body.allow_unresolved_clarifications))


@router.post("/missions/{mission_id}/grid/new-version", status_code=201)
def new_version(body: S.GridNewVersion, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    row, diff = ms.new_grid_version(db, user, m, body.reason)
    n = len({a.candidate_id for a in db.scalars(select(Assessment).where(Assessment.mission_id == m.id))})
    return {"grid": grid_dict(row), "diff": diff, "candidates_to_reassess": n,
            "note": "Valider la nouvelle grille pour réévaluer tous les candidats de façon cohérente (comparaison avant/après conservée)."}


# ----------------------------------------------------------------- recherches booléennes
@router.get("/missions/{mission_id}/searches")
def list_searches(ctx=Strategy, db: Session = Depends(get_db)):
    m, _, _ = ctx
    return sr.list_for_mission(db, m)


@router.post("/missions/{mission_id}/searches/generate", status_code=201)
def generate_searches(ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    return [sr.search_dict(s) for s in sr.generate(db, user, m)]


@router.post("/missions/{mission_id}/searches/custom", status_code=201)
def custom_search(body: S.CustomSearch, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    row, issues = sr.custom(db, user, m, body.query, body.strategy)
    return {"search": sr.search_dict(row), "issues": issues}


@router.post("/missions/{mission_id}/searches/{sid}/feedback", status_code=201)
def search_feedback(sid: str, body: S.FeedbackIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    s = db.get(Search, sid)
    if s is None or s.mission_id != m.id:
        raise HTTPException(404, "Recherche introuvable.")
    out = sr.record_feedback(db, user, m, s, body.model_dump())
    out["new_search"] = sr.search_dict(out["new_search"]) if out["new_search"] else None
    return out


@router.post("/missions/{mission_id}/searches/{sid}/save")
def save_search(sid: str, body: S.SaveSearch, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    s = db.get(Search, sid)
    if s is None or s.mission_id != m.id:
        raise HTTPException(404, "Recherche introuvable.")
    return sr.search_dict(sr.save(db, user, m, s, body.status, body.note))


@router.post("/tools/boolean/validate")
def boolean_validate(body: S.BooleanCheck, user: User = Depends(current_user)):
    s = get_settings()
    issues = bl.validate(body.query, sr.platform())
    return {"valid": not any(i.severity == "error" for i in issues), "length": len(body.query), "max_length": s.platform_max_length,
            "issues": [i.to_dict() for i in issues], "syntax_confirmed": False, "note": bl.TURNOVER.notes}


# ----------------------------------------------------------------- CV : import, candidats, évaluations
@router.post("/missions/{mission_id}/cvs", status_code=202)
async def upload_cvs(files: list[UploadFile] = File(...), ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    s = get_settings()
    if len(files) > s.max_batch_size:
        raise HTTPException(422, f"Un lot est limité à {s.max_batch_size} CV : {len(files)} reçus.")
    limit = s.max_file_mb * 1024 * 1024
    data: list[tuple[str, bytes]] = []
    for f in files:
        buf = bytearray()
        while chunk := await f.read(1024 * 1024):
            buf += chunk
            if len(buf) > limit + 1:
                break
        data.append((f.filename or "document", bytes(buf)))
    docs = mt.ingest_files(db, user, m, data)
    return {"documents": [mt.doc_dict(d) for d in docs], "grid_frozen": ms.latest_frozen(db, m.id) is not None}


@router.get("/missions/{mission_id}/documents")
def list_documents(ctx=Read, db: Session = Depends(get_db)):
    m, _, _ = ctx
    db.expire_all()
    return [mt.doc_dict(d) for d in db.scalars(select(Document).where(Document.mission_id == m.id).order_by(Document.created_at))]


@router.get("/missions/{mission_id}/candidates")
def list_candidates(view: str = Query("all", pattern="^(all|work)$"), ctx=Read, db: Session = Depends(get_db)):
    m, _, _ = ctx
    s = get_settings()
    out = []
    for c in db.scalars(select(Candidate).where(Candidate.mission_id == m.id).order_by(Candidate.created_at)):
        b = mt.candidate_brief(db, c)
        a = b.get("assessment")
        b["hidden_in_work_view"] = bool(a and a["score_documented"] < s.work_view_min)      # masqué seulement dans la vue exigeante ; jamais supprimé
        if view == "work" and b["hidden_in_work_view"]:
            continue
        out.append(b)
    from ..domain.scoring import rank_key
    def key(b):
        a = b.get("assessment")
        if not a:
            return (1, 0, 0, 0, 0)               # sans évaluation : en fin de liste
        contra = 1 if a["tier"] == "ecart_majeur" else 0
        return (0, *rank_key(a["open_mandatory"], contra, a["score_documented"], a["score_potential"]))
    out.sort(key=key)
    return out


@router.get("/missions/{mission_id}/candidates/{cid}")
def get_candidate(cid: str, ctx=Read, db: Session = Depends(get_db)):
    m, _, _ = ctx
    c = _cand(db, m, cid)
    b = mt.candidate_brief(db, c)
    a = mt.latest_assessment(db, c.id)
    hist = [assessment_dict(x, full=False) for x in db.scalars(select(Assessment).where(Assessment.candidate_id == c.id).order_by(Assessment.created_at, Assessment.version))]
    evs = []
    reviews = {r.evidence_id: r for r in db.scalars(select(EvidenceReview).where(EvidenceReview.evidence_id.in_(
        [e.id for e in db.scalars(select(Evidence).where(Evidence.candidate_id == c.id))] or [""])).order_by(EvidenceReview.at))}
    for e in db.scalars(select(Evidence).where(Evidence.candidate_id == c.id).order_by(Evidence.created_at)):
        rv = reviews.get(e.id)
        evs.append({"id": e.id, "subject_key": e.subject_key, "kind": e.kind, "source": e.source, "reliability": e.reliability, "level": e.level, "excerpt": e.excerpt,
                    "speaker": e.speaker, "speaker_role": e.speaker_role, "certainty": e.certainty, "auto_generated": e.auto_generated, "needs_verification": e.needs_verification,
                    "why_verify": e.why_verify, "date": e.source_date.isoformat() if e.source_date else None, "review": rv.decision if rv else None,
                    "created_at": e.created_at.isoformat(), "constraint": e.data})
    notes = [{"id": n.id, "kind": n.kind, "auto_generated": n.auto_generated, "note_date": n.note_date.isoformat() if n.note_date else None, "text": n.text,
              "created_at": n.created_at.isoformat()} for n in db.scalars(select(CallNote).where(CallNote.candidate_id == c.id).order_by(CallNote.created_at))]
    q = db.scalar(select(Qualification).where(Qualification.candidate_id == c.id).order_by(Qualification.created_at.desc()).limit(1))
    b.update({"assessment_full": assessment_dict(a) if a else None, "history": hist, "evidence": evs, "notes": notes, "questions": q.questions if q else None})
    return b


def _cand(db: Session, m: Mission, cid: str) -> Candidate:
    c = db.get(Candidate, cid)
    if c is None or c.mission_id != m.id:
        raise HTTPException(404, "Candidat introuvable.")
    return c


@router.patch("/missions/{mission_id}/candidates/{cid}")
def patch_candidate(cid: str, body: S.CandidatePatch, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    c = _cand(db, m, cid)
    if body.acronym is not None:
        c.acronym = body.acronym.upper()
    if body.label is not None:
        c.label = body.label
    audit.log(db, user.id, "candidate.update", "candidate", c.id, m.id, acronym_set=bool(c.acronym))
    return mt.candidate_brief(db, c)


@router.post("/missions/{mission_id}/candidates/{cid}/status")
def candidate_status(cid: str, body: S.StatusIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    return mt.candidate_brief(db, mt.set_status(db, user, m, _cand(db, m, cid), body.status, body.comment))


@router.delete("/missions/{mission_id}/candidates/{cid}", status_code=204)
def delete_candidate(cid: str, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    mt.delete_candidate(db, user, m, _cand(db, m, cid))
    return Response(status_code=204)


@router.post("/missions/{mission_id}/candidates/{cid}/notes", status_code=201)
def add_note(cid: str, body: S.NoteIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    c = _cand(db, m, cid)
    r = mt.add_candidate_note(db, user, m, c, body.kind, body.text, auto_generated=body.auto_generated, speaker_map=body.speaker_map, note_date=body.note_date)
    return {"note_id": r["note_id"], "facts": [{"id": e.id, "subject_key": e.subject_key, "kind": e.kind, "level": e.level, "excerpt": e.excerpt, "certainty": e.certainty,
                                                "needs_verification": e.needs_verification, "why_verify": e.why_verify} for e in r["evidence"]],
            "proposals": [proposal_dict(p) for p in r["proposals"]],
            "assessment": assessment_dict(r["assessment"]) if r["assessment"] else None}


@router.post("/missions/{mission_id}/candidates/{cid}/questions")
def candidate_questions(cid: str, ctx=Read, db: Session = Depends(get_db)):
    m, _, user = ctx
    return mt.questions(db, user, m, _cand(db, m, cid))


@router.post("/missions/{mission_id}/candidates/{cid}/reassess")
def reassess_one(cid: str, body: S.ReassessIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    return assessment_dict(mt.reassess(db, user, m, _cand(db, m, cid), "manuel", body.reason))


@router.post("/missions/{mission_id}/candidates/{cid}/correction", status_code=201)
def correction(cid: str, body: S.CorrectionIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    c = _cand(db, m, cid)
    a = db.get(Assessment, body.assessment_id)
    if a is None or a.candidate_id != c.id:
        raise HTTPException(404, "Évaluation introuvable.")
    r = mt.correct_criterion(db, user, m, a, body.criterion_key, body.new_level, body.error_nature, body.comment)
    return {"assessment": assessment_dict(r["assessment"]), "regression_case": r["regression_case"]}


def llm_provider():
    from ..llm.provider import get_provider
    return get_provider()


@router.post("/missions/{mission_id}/candidates/{cid}/llm-assist")
def llm_assist(cid: str, ctx=Edit, db: Session = Depends(get_db), provider=Depends(llm_provider)):
    """IA optionnelle : désigne des passages, vérifiés par citation ; sans fournisseur configuré, 503 explicite (rien n'est simulé)."""
    m, _, user = ctx
    if getattr(provider, "name", "none") == "none":
        raise HTTPException(503, "Aucun fournisseur IA configuré (TALENTLAB_LLM_PROVIDER). Les fonctions déterministes restent disponibles.")
    r = mt.llm_assist(db, user, m, _cand(db, m, cid), provider)
    return {"claims": r["claims"], "assessment": assessment_dict(r["assessment"])}


@router.post("/missions/{mission_id}/evidence/{eid}/review")
def review_evidence(eid: str, body: S.ReviewIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    ev = db.get(Evidence, eid)
    if ev is None or ev.mission_id != m.id:
        raise HTTPException(404, "Information introuvable.")
    r = mt.review_evidence(db, user, m, ev, body.decision, body.note)
    return {"assessment": assessment_dict(r["assessment"]) if r["assessment"] else None}


@router.post("/missions/{mission_id}/compare")
def compare(body: S.CompareIn, ctx=Read, db: Session = Depends(get_db)):
    m, _, _ = ctx
    return mt.compare(db, m, body.candidate_ids)


@router.post("/missions/{mission_id}/reassess-all")
def reassess_all(body: S.ReassessIn, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    res = mt.reassess_all(db, user, m, "nouvelle_grille", body.reason)
    return {"reassessed": len(res), "assessments": [assessment_dict(a, full=False) | {"candidate_id": a.candidate_id, "diff": (a.diff or {}).get("score_delta")} for a in res]}


@router.get("/missions/{mission_id}/candidates/{cid}/export/dt")
def export_dt(cid: str, relevant_only: bool = True, ctx=Read, db: Session = Depends(get_db)):
    m, _, user = ctx
    c = _cand(db, m, cid)
    out = exp.dt_export(db, m, c, relevant_only=relevant_only)
    audit.log(db, user.id, "export.dt", "candidate", c.id, m.id)
    return out


@router.get("/missions/{mission_id}/candidate-description")
def candidate_description(include_client: bool = False, ctx=Read, db: Session = Depends(get_db)):
    m, _, _ = ctx
    return exp.candidate_description(db, m, include_client=include_client)


# ----------------------------------------------------------------- collaboration
@router.get("/missions/{mission_id}/shares")
def list_shares(ctx=Owner, db: Session = Depends(get_db)):
    m, _, _ = ctx
    out = []
    for s in db.scalars(select(Share).where(Share.mission_id == m.id)):
        u = db.get(User, s.user_id)
        out.append({"user_id": s.user_id, "display_name": u.display_name if u else "", "email": u.email if u else "", "scope": s.scope})
    return out


@router.post("/missions/{mission_id}/shares", status_code=201)
def add_share(body: S.ShareIn, ctx=Owner, db: Session = Depends(get_db)):
    m, _, user = ctx
    target = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.active.is_(True)))
    if target is None:
        raise HTTPException(404, "Collègue introuvable.")
    if target.id == m.owner_id:
        raise HTTPException(422, "Le propriétaire a déjà tous les droits.")
    sh = db.scalar(select(Share).where(Share.mission_id == m.id, Share.user_id == target.id))
    if sh:
        sh.scope = body.scope
    else:
        db.add(Share(mission_id=m.id, user_id=target.id, scope=body.scope, granted_by=user.id))
    audit.log(db, user.id, "share.grant", "mission", m.id, m.id, to=target.id, scope=body.scope)
    return {"user_id": target.id, "scope": body.scope}


@router.delete("/missions/{mission_id}/shares/{uid_}", status_code=204)
def remove_share(uid_: str, ctx=Owner, db: Session = Depends(get_db)):
    m, _, user = ctx
    sh = db.scalar(select(Share).where(Share.mission_id == m.id, Share.user_id == uid_))
    if sh:
        db.delete(sh)
        audit.log(db, user.id, "share.revoke", "mission", m.id, m.id, to=uid_)
    return Response(status_code=204)


@router.get("/missions/{mission_id}/history")
def history(ctx=Read, db: Session = Depends(get_db)):
    m, _, _ = ctx
    names = {u.id: u.display_name for u in db.scalars(select(User))}
    evs = db.scalars(select(AuditEvent).where(AuditEvent.mission_id == m.id).order_by(AuditEvent.seq.desc()).limit(500))
    return {"events": [{"seq": e.seq, "at": e.at.isoformat(), "user": names.get(e.user_id, ""), "action": e.action, "entity_type": e.entity_type,
                        "entity_id": e.entity_id, "detail": e.detail} for e in evs],
            "searches": sr.list_for_mission(db, m),
            "grids": [grid_dict(g) for g in db.scalars(select(Grid).where(Grid.mission_id == m.id).order_by(Grid.version))]}


def proposal_dict(p: Proposal) -> dict[str, Any]:
    return {"id": p.id, "mission_id": p.mission_id, "kind": p.kind, "payload": {k: v for k, v in p.payload.items() if k != "variant"} | ({"query": p.payload["variant"]["query"]} if "variant" in p.payload else {}),
            "explanation": p.explanation, "consequences": p.consequences, "status": p.status, "created_at": p.created_at.isoformat()}


@router.get("/missions/{mission_id}/proposals")
def list_proposals(ctx=Read, db: Session = Depends(get_db)):
    m, _, _ = ctx
    return [proposal_dict(p) for p in db.scalars(select(Proposal).where(Proposal.mission_id == m.id).order_by(Proposal.created_at.desc()))]


@router.post("/missions/{mission_id}/proposals/{pid}/confirm")
def confirm_proposal(pid: str, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    p = db.get(Proposal, pid)
    if p is None or p.mission_id != m.id:
        raise HTTPException(404, "Proposition introuvable.")
    return asst.confirm(db, user, m, p)


@router.post("/missions/{mission_id}/proposals/{pid}/reject")
def reject_proposal(pid: str, ctx=Edit, db: Session = Depends(get_db)):
    m, _, user = ctx
    p = db.get(Proposal, pid)
    if p is None or p.mission_id != m.id:
        raise HTTPException(404, "Proposition introuvable.")
    asst.reject(db, user, m, p)
    return {"ok": True}


@router.post("/missions/{mission_id}/assistant")
def assistant(body: S.AssistantIn, ctx=Read, db: Session = Depends(get_db)):
    m, level, user = ctx
    r = asst.handle(db, user, m, level, body.message, candidate_ids=body.candidate_ids, search_id=body.search_id)
    r["proposals"] = [proposal_dict(p) for p in r["proposals"]]
    return r


@router.post("/coach")
def coach_api(body: S.CoachIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    search = criterion = None
    if body.mission_id:
        m, level = require_mission(db, user, body.mission_id, "lecture" if body.candidate_id else "strategie")
        if body.search_id:
            s = db.get(Search, body.search_id)
            if s and s.mission_id == m.id:
                search = sr.search_dict(s)
        if body.candidate_id and body.criterion_key:
            a = mt.latest_assessment(db, body.candidate_id)
            if a and a.mission_id == m.id:
                criterion = next((c for c in a.result["criteria"] if c["key"] == body.criterion_key), None)
    return coach.answer(body.question, search=search, criterion=criterion)


# ----------------------------------------------------------------- bibliothèque
@router.get("/library")
def library(status: str | None = None, role_family: str | None = None, q: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return kn.list_entries(db, user, status=status, role_family=role_family, q=q)


@router.post("/library", status_code=201)
def library_create(body: S.KnowledgeIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.mission_id:
        require_mission(db, user, body.mission_id, "lecture")
    e = kn.create(db, user, body.kind, body.title, body.body, body.role_family, body.tags, body.mission_id)
    return kn.entry_dict(e, user.display_name)


def _entry(db: Session, eid: str, user: User) -> KnowledgeEntry:
    e = db.get(KnowledgeEntry, eid)
    if e is None or (e.status != "published" and e.author_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Entrée introuvable.")
    return e


@router.patch("/library/{eid}")
def library_update(eid: str, body: S.KnowledgePatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return kn.entry_dict(kn.update(db, user, _entry(db, eid, user), **body.model_dump(exclude_unset=True)))


@router.post("/library/{eid}/publish")
def library_publish(eid: str, body: S.PublishIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return kn.entry_dict(kn.publish(db, user, _entry(db, eid, user), generalize=body.generalize))


@router.post("/library/{eid}/retire")
def library_retire(eid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return kn.entry_dict(kn.retire(db, user, _entry(db, eid, user)))


@router.get("/library/regression-cases")
def regression_cases(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return kn.regression_cases(db, user)


# ----------------------------------------------------------------- administration
@router.post("/admin/users", status_code=201)
def admin_create_user(body: S.UserIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Compte déjà existant.")
    u = User(email=email, display_name=body.display_name, role=body.role, region=body.region)
    db.add(u)
    db.flush()
    audit.log(db, admin.id, "user.create", "user", u.id, role=body.role)
    return user_dict(u)


@router.post("/admin/purge")
def admin_purge(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return mt.purge_expired(db, admin)


@router.get("/admin/audit/verify")
def admin_verify(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return audit.verify_chain(db)
