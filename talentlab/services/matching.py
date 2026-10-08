"""Matching : import multi-CV, évaluation, enrichissement par appels, corrections, comparaison (§21, §7, §17.3)."""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import session_scope
from ..domain import qualification as qa
from ..domain.call_facts import candidate_facts_from, extract_facts, reliability_for, source_for
from ..domain.cv_extract import ExtractionError, extract_text, parse_cv
from ..domain.enums import EvidenceKind, EvidenceSource, Level, Reliability
from ..domain.evidence import ExtEvidence
from ..domain.safety import scan_text
from ..domain.scoring import CandidateFacts, assess, compare as compare_assessments, diff_assessments
from ..domain.text import fold
from ..models import (Assessment, CallNote, Candidate, Document, Evidence, EvidenceReview, Grid, Mission, Proposal, Qualification, ScoringCorrection, User)
from ..enums_app import CANDIDATE_STATUSES
from . import missions as ms

_executor: ThreadPoolExecutor | None = None


def executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=get_settings().worker_threads, thread_name_prefix="talentlab-cv")
    return _executor


# ----------------------------------------------------------------- import
def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ()À-ÿ]", "_", (name or "document").replace("\\", "/").split("/")[-1])[:200]
    return name or "document"


def ingest_files(db: Session, user: User, m: Mission, files: list[tuple[str, bytes]]) -> list[Document]:
    s = get_settings()
    if not files:
        raise HTTPException(422, "Aucun fichier reçu.")
    if len(files) > s.max_batch_size:
        raise HTTPException(422, f"Un lot est limité à {s.max_batch_size} CV (limite configurable) : {len(files)} reçus.")
    docs: list[Document] = []
    seen_in_batch: dict[str, str] = {}
    exp = date.today() + timedelta(days=s.retention_days)
    for name, data in files:
        h = hashlib.sha256(data).hexdigest()
        d = Document(mission_id=m.id, filename=_safe_name(name), sha256_file=h, size=len(data), created_by=user.id, expires_at=exp)
        if len(data) > s.max_file_mb * 1024 * 1024:
            d.status, d.error_code, d.error_message, d.progress, d.stage = "failed", "too_large", f"Le fichier dépasse {s.max_file_mb} Mo.", 100, "refusé"
        elif h in seen_in_batch or db.scalar(select(Document).where(Document.mission_id == m.id, Document.sha256_file == h, Document.status.in_(("done", "processing", "queued")))):
            d.status, d.stage, d.progress = "duplicate", "doublon (même fichier déjà importé)", 100
            d.duplicate_of = seen_in_batch.get(h)
        else:
            d.blob = data
            seen_in_batch[h] = d.id
        db.add(d)
        docs.append(d)
    db.flush()
    for d in docs:
        if d.status == "duplicate" and d.duplicate_of is None:
            first = db.scalar(select(Document).where(Document.mission_id == m.id, Document.sha256_file == d.sha256_file, Document.id != d.id, Document.status != "duplicate"))
            d.duplicate_of = first.id if first else None
    audit.log(db, user.id, "cv.import", "mission", m.id, m.id, files=len(files), queued=sum(1 for d in docs if d.status == "queued"))
    db.commit()
    for d in docs:
        if d.status == "queued":
            if s.processing_mode == "inline":
                process_document(d.id, user.id)
            else:
                executor().submit(process_document, d.id, user.id)
    for d in docs:
        db.refresh(d)
    return docs


def requeue_stale() -> int:
    """Au démarrage : reprend les documents restés en file ou en cours (redémarrage du service)."""
    n = 0
    with session_scope() as db:
        for d in db.scalars(select(Document).where(Document.status.in_(("queued", "processing")))):
            d.status = "queued"
            executor().submit(process_document, d.id, d.created_by)
            n += 1
    return n


def _stage(db: Session, d: Document, status: str, progress: int, stage: str) -> None:
    d.status, d.progress, d.stage = status, progress, stage
    db.commit()


def process_document(doc_id: str, user_id: str) -> None:
    """Traite UN document ; une erreur sur ce document n'empêche jamais les autres (§21)."""
    s = get_settings()
    try:
        with session_scope() as db:
            d = db.get(Document, doc_id)
            if d is None or d.status not in ("queued", "processing"):
                return
            m = db.get(Mission, d.mission_id)
            if m is None:
                return
            _stage(db, d, "processing", 10, "lecture du document")
            try:
                ex = extract_text(d.blob or b"", d.filename, max_bytes=s.max_file_mb * 1024 * 1024, max_pages=s.max_pages, min_chars=s.min_chars)
            except ExtractionError as e:
                d.status, d.progress, d.stage, d.error_code, d.error_message = "failed", 100, "échec de l'extraction", e.code, e.message
                audit.log(db, user_id, "cv.failed", "document", d.id, m.id, code=e.code)
                return
            except Exception:                                   # noqa: BLE001 — jamais de trace interne dans la réponse
                d.status, d.progress, d.stage, d.error_code, d.error_message = "failed", 100, "échec de l'extraction", "internal", "Erreur interne lors de la lecture du document."
                audit.log(db, user_id, "cv.failed", "document", d.id, m.id, code="internal")
                return
            _stage(db, d, "processing", 40, "structuration du CV")
            d.pages, d.mime, d.extraction_quality, d.warnings = ex.pages, ex.mime, ex.quality, ex.warnings
            sha_text = hashlib.sha256(re.sub(r"\s+", " ", fold(ex.text)).strip().encode()).hexdigest()
            d.sha256_text = sha_text
            dup = db.scalar(select(Document).where(Document.mission_id == m.id, Document.sha256_text == sha_text, Document.status == "done", Document.id != d.id))
            if dup:
                d.status, d.progress, d.stage, d.duplicate_of = "duplicate", 100, "doublon (même contenu qu'un CV déjà analysé)", dup.id
                d.blob = None
                return
            d.text = ex.text
            d.security_flags = scan_text(ex.text)
            n = (db.scalar(select(func.count(Candidate.id)).where(Candidate.mission_id == m.id)) or 0) + 1
            cand = Candidate(mission_id=m.id, ref=f"C-{n:04d}", label=re.sub(r"\.[A-Za-z0-9]{2,4}$", "", d.filename)[:120], created_by=user_id)
            db.add(cand)
            db.flush()
            d.candidate_id = cand.id
            _stage(db, d, "processing", 70, "évaluation sur la grille figée")
            grid = ms.latest_frozen(db, m.id)
            if grid is not None:
                assess_candidate(db, user_id, m, cand, grid, "import", "Analyse initiale du CV", doc=d)
                d.stage = "analysé"
            else:
                d.stage = "extrait — évaluation en attente de la grille figée"
            d.status, d.progress = "done", 100
            audit.log(db, user_id, "cv.processed", "document", d.id, m.id, candidate=cand.id, quality=ex.quality, flags=len(d.security_flags))
    except Exception:                                           # noqa: BLE001
        try:
            with session_scope() as db:
                d = db.get(Document, doc_id)
                if d and d.status != "done":
                    d.status, d.progress, d.stage, d.error_code, d.error_message = "failed", 100, "échec du traitement", "internal", "Erreur interne lors du traitement."
        except Exception:                                       # noqa: BLE001
            pass


# ----------------------------------------------------------------- évaluations
def load_external(db: Session, candidate_id: str) -> tuple[dict[str, list[ExtEvidence]], CandidateFacts]:
    rows = list(db.scalars(select(Evidence).where(Evidence.candidate_id == candidate_id).order_by(Evidence.created_at)))
    reviews: dict[str, str] = {}
    for rv in db.scalars(select(EvidenceReview).where(EvidenceReview.evidence_id.in_([r.id for r in rows] or [""])).order_by(EvidenceReview.at)):
        reviews[rv.evidence_id] = rv.decision
    ext: dict[str, list[ExtEvidence]] = {}
    facts = CandidateFacts()
    for r in rows:
        decision = reviews.get(r.id)
        if r.subject_key.startswith("constraint:"):
            if decision == "rejected" or (r.auto_generated and decision != "validated"):
                continue
            d = r.data or {}
            fld, val = d.get("field"), d.get("value")
            if fld == "refuses":
                facts.refuses.add(val)
            elif fld == "accepts_astreinte":
                facts.accepts_astreinte = bool(val)
            elif fld in ("availability", "tjm", "onsite_max_days", "city", "timezone_ok", "english_level"):
                setattr(facts, fld, val)
            continue
        ext.setdefault(r.subject_key, []).append(ExtEvidence(
            id=r.id, subject_key=r.subject_key, kind=EvidenceKind(r.kind), excerpt=r.excerpt, source=EvidenceSource(r.source), reliability=Reliability(r.reliability),
            level=Level(r.level) if r.level else None, validated=(decision == "validated") or r.source == EvidenceSource.RECRUITER_INPUT.value,
            auto_generated=r.auto_generated, date=r.source_date, speaker=r.speaker, superseded=(decision == "rejected")))
    return ext, facts


def latest_assessment(db: Session, candidate_id: str, grid_id: str | None = None) -> Assessment | None:
    q = select(Assessment).where(Assessment.candidate_id == candidate_id)
    if grid_id:
        q = q.where(Assessment.grid_id == grid_id)
    return db.scalar(q.order_by(Assessment.created_at.desc(), Assessment.version.desc()).limit(1))


def assess_candidate(db: Session, user_id: str, m: Mission, cand: Candidate, grid_row: Grid, trigger: str, reason: str = "",
                     doc: Document | None = None) -> Assessment:
    doc = doc or db.scalar(select(Document).where(Document.candidate_id == cand.id, Document.status == "done").order_by(Document.created_at.desc()).limit(1))
    if doc is None or doc.text is None:
        raise HTTPException(409, "Aucun document exploitable pour ce candidat : aucune évaluation n'est produite.")
    grid = ms.grid_from_row(grid_row)
    parsed = parse_cv(doc.text, today=date.today(), extraction_quality=doc.extraction_quality or "ok")
    ext, facts = load_external(db, cand.id)
    asm = assess(grid, parsed, ext, facts, today=date.today(), security_flags=doc.security_flags)
    res = asm.to_dict()
    prev = latest_assessment(db, cand.id)
    diff = diff_assessments(prev.result, res) if prev else None
    row = Assessment(mission_id=m.id, candidate_id=cand.id, grid_id=grid_row.id, version=(prev.version + 1 if prev else 1), trigger=trigger, reason=reason,
                     result=res, diff=diff, score_documented=asm.score_documented, score_potential=asm.score_potential, tier=asm.tier,
                     prev_id=prev.id if prev else None, created_by=user_id)
    db.add(row)
    db.flush()
    if cand.status == "a_evaluer":
        cand.status = "a_qualifier"
    audit.log(db, user_id, "assessment.create", "assessment", row.id, m.id, candidate=cand.id, trigger=trigger, grid_version=grid_row.version,
              score=asm.score_documented, tier=asm.tier)
    return row


def reassess(db: Session, user: User, m: Mission, cand: Candidate, trigger: str, reason: str) -> Assessment:
    grid = ms.latest_frozen(db, m.id)
    if grid is None:
        raise HTTPException(409, "Aucune grille figée : valider la grille avant toute évaluation.")
    return assess_candidate(db, user.id, m, cand, grid, trigger, reason)


def reassess_all(db: Session, user: User, m: Mission, trigger: str, reason: str) -> list[Assessment]:
    out = []
    for cand in db.scalars(select(Candidate).where(Candidate.mission_id == m.id)):
        has_doc = db.scalar(select(Document.id).where(Document.candidate_id == cand.id, Document.status == "done").limit(1))
        if has_doc:
            out.append(reassess(db, user, m, cand, trigger, reason))
    return out


# ----------------------------------------------------------------- notes d'appel / enrichissement (§7)
def add_candidate_note(db: Session, user: User, m: Mission, cand: Candidate, kind: str, text: str, *, auto_generated: bool,
                       speaker_map: dict[str, str] | None, note_date: date | None) -> dict[str, Any]:
    if kind not in ("candidate_call_note", "transcript", "interview_report", "complementary_doc"):
        raise HTTPException(422, "Type de note inconnu.")
    if len(text.strip()) < 15:
        raise HTTPException(422, "Le texte est trop court pour en extraire des faits.")
    note = CallNote(mission_id=m.id, candidate_id=cand.id, kind=kind, text=text, speaker_map=speaker_map or {}, auto_generated=auto_generated or kind == "transcript",
                    note_date=note_date or date.today(), created_by=user.id)
    db.add(note)
    db.flush()
    facts = extract_facts(text, kind, speaker_map=speaker_map, auto_generated=auto_generated)
    ev_ids, props = [], []
    for f in facts:
        if f.kind == "candidate_experience" and f.topic_key:
            e = Evidence(mission_id=m.id, candidate_id=cand.id, note_id=note.id, subject_key=f.topic_key, kind=(f.evidence_kind or EvidenceKind.SUPPORTS).value,
                         source=source_for(kind).value, reliability=(Reliability.UNSUPPORTED_CLAIM if f.certainty == "hedged" and f.level == Level.DECLARED else reliability_for(kind)).value,
                         level=f.level.value if f.level else None, excerpt=f.statement, speaker=f.speaker, speaker_role=f.speaker_role, certainty=f.certainty,
                         auto_generated=note.auto_generated, needs_verification=f.needs_verification, why_verify=f.why_verify, source_date=note.note_date, created_by=user.id)
        elif f.kind == "candidate_constraint" and f.constraint:
            e = Evidence(mission_id=m.id, candidate_id=cand.id, note_id=note.id, subject_key=f"constraint:{f.constraint['field']}", kind=EvidenceKind.SUPPORTS.value,
                         source=source_for(kind).value, reliability=reliability_for(kind).value, excerpt=f.statement, speaker=f.speaker, speaker_role=f.speaker_role,
                         certainty=f.certainty, auto_generated=note.auto_generated, needs_verification=f.needs_verification, why_verify=f.why_verify,
                         source_date=note.note_date, data=f.constraint, created_by=user.id)
        elif f.kind == "client_requirement" and f.topic_key:
            p = Proposal(mission_id=m.id, kind="requirement_change", created_by=user.id,
                         payload={"topic_key": f.topic_key, "topic_label": f.topic_label, "category_hint": f.requirement_hint["category_hint"], "relayed": True,
                                  "statement": f.statement, "author": user.display_name, "source_kind": kind},
                         explanation=f"« {f.statement} » — exigence du poste relayée dans un échange candidat : pas une compétence du candidat. {f.why_verify}",
                         consequences=["Nouvelle version de grille à valider.", "Réévaluation cohérente de tous les candidats concernés."])
            db.add(p)
            props.append(p)
            continue
        else:
            continue
        db.add(e)
        ev_ids.append(e)
    db.flush()
    audit.log(db, user.id, "note.add", "call_note", note.id, m.id, candidate=cand.id, kind=kind, facts=len(ev_ids), proposals=len(props))
    asm = None
    if ms.latest_frozen(db, m.id) and db.scalar(select(Document.id).where(Document.candidate_id == cand.id, Document.status == "done").limit(1)):
        asm = reassess(db, user, m, cand, "nouvelle_information", f"Nouvelle information : {kind.replace('_', ' ')} du {note.note_date}")
    return {"note_id": note.id, "evidence": ev_ids, "proposals": props, "assessment": asm}


def review_evidence(db: Session, user: User, m: Mission, ev: Evidence, decision: str, note: str) -> dict[str, Any]:
    if decision not in ("validated", "rejected"):
        raise HTTPException(422, "Décision inconnue (validated | rejected).")
    db.add(EvidenceReview(evidence_id=ev.id, decision=decision, note=note, reviewer_id=user.id))
    db.flush()
    audit.log(db, user.id, "evidence.review", "evidence", ev.id, m.id, decision=decision)
    cand = db.get(Candidate, ev.candidate_id)
    asm = None
    if cand and ms.latest_frozen(db, m.id):
        asm = reassess(db, user, m, cand, "validation_preuve", f"Information {'validée' if decision == 'validated' else 'rejetée'} par {user.display_name}")
    return {"assessment": asm}


ERROR_NATURES = ("mention_surevaluee", "contexte_deduit", "role_surestime", "confusion_portee", "recence_ignoree", "profondeur_surestimee",
                 "preuve_manquante", "sous_evaluation", "autre")


def correct_criterion(db: Session, user: User, m: Mission, asm_row: Assessment, criterion_key: str, new_level: str, error_nature: str, comment: str) -> dict[str, Any]:
    """Correction humaine d'un critère (§17.3) : preuve « recruteur », réévaluation, cas de non-régression — aucune règle globale modifiée."""
    if error_nature not in ERROR_NATURES:
        raise HTTPException(422, f"Nature de l'erreur inconnue : {', '.join(ERROR_NATURES)}")
    if len(comment.strip()) < 5:
        raise HTTPException(422, "Un commentaire explicatif est obligatoire.")
    crit = next((c for c in asm_row.result["criteria"] if c["key"] == criterion_key), None)
    if crit is None:
        raise HTTPException(404, "Critère introuvable dans cette évaluation.")
    lvl = Level(new_level)
    old = Level(crit["level"])
    from ..domain.enums import LEVEL_ORDER
    kind = EvidenceKind.CONTRADICTS if lvl == Level.CONTRADICTED else (EvidenceKind.CAPS if LEVEL_ORDER[lvl] < LEVEL_ORDER[old] else EvidenceKind.SUPPORTS)
    e = Evidence(mission_id=m.id, candidate_id=asm_row.candidate_id, subject_key=criterion_key, kind=kind.value, source=EvidenceSource.RECRUITER_INPUT.value,
                 reliability=Reliability.CONFIRMED_IN_INTERVIEW.value if kind == EvidenceKind.SUPPORTS else Reliability.CONTRADICTION.value,
                 level=lvl.value, excerpt=f"Correction du recruteur ({user.display_name}) : {comment}", speaker=user.display_name, speaker_role="recruiter",
                 auto_generated=False, needs_verification=False, source_date=date.today(), created_by=user.id)
    db.add(e)
    db.flush()
    db.add(EvidenceReview(evidence_id=e.id, decision="validated", note="Saisie directe du recruteur", reviewer_id=user.id))
    cand = db.get(Candidate, asm_row.candidate_id)
    case = {"criterion": criterion_key, "label": crit["label"], "rule_description": error_nature, "observed_level": old.value, "expected_level": lvl.value,
            "justification_observed": crit["justification"][:300], "evidence_excerpts": [x["excerpt"][:200] for x in crit["evidence"][:3]],
            "note": "Cas anonymisé de non-régression : à rejouer sur le moteur ; ne modifie aucune règle automatiquement."}
    db.add(ScoringCorrection(mission_id=m.id, assessment_id=asm_row.id, criterion_key=criterion_key, old_level=old.value, new_level=lvl.value,
                             error_nature=error_nature, comment=comment, regression_case=case, created_by=user.id))
    audit.log(db, user.id, "assessment.correction", "assessment", asm_row.id, m.id, criterion=criterion_key, old=old.value, new=lvl.value, nature=error_nature)
    new = reassess(db, user, m, cand, "correction_recruteur", f"Correction humaine du critère « {crit['label']} » : {error_nature}")
    return {"assessment": new, "regression_case": case}


# ----------------------------------------------------------------- consultation
def candidate_brief(db: Session, c: Candidate) -> dict[str, Any]:
    a = latest_assessment(db, c.id)
    doc = db.scalar(select(Document).where(Document.candidate_id == c.id).order_by(Document.created_at.desc()).limit(1))
    d: dict[str, Any] = {"id": c.id, "ref": c.ref, "label": c.label, "acronym": c.acronym, "status": c.status, "status_comment": c.status_comment,
                         "document": doc_dict(doc) if doc else None, "assessment": None}
    if a:
        r = a.result
        d["assessment"] = {"id": a.id, "version": a.version, "grid_version": r["grid_version"], "score": r["score_displayed"], "score_documented": r["score_documented"],
                           "score_potential": r["score_potential"], "tier": r["tier"], "uncertainty": r["uncertainty"], "alerts": r["alerts"],
                           "open_mandatory": r["open_mandatory"], "information_quality": r["information_quality"]["score"], "trigger": a.trigger,
                           "created_at": a.created_at.isoformat(), "security_flags": r.get("security_flags", [])}
    return d


def doc_dict(d: Document) -> dict[str, Any]:
    return {"id": d.id, "filename": d.filename, "status": d.status, "progress": d.progress, "stage": d.stage, "error_code": d.error_code,
            "error_message": d.error_message, "pages": d.pages, "quality": d.extraction_quality, "warnings": d.warnings, "duplicate_of": d.duplicate_of,
            "candidate_id": d.candidate_id, "security_flags": d.security_flags, "size": d.size}


def compare(db: Session, m: Mission, candidate_ids: list[str]) -> dict[str, Any]:
    from ..domain.scoring import Assessment as DA, CriterionResult
    asms: dict[str, Any] = {}
    for cid in candidate_ids:
        c = db.get(Candidate, cid)
        if c is None or c.mission_id != m.id:
            raise HTTPException(404, "Candidat introuvable dans cette mission.")
        a = latest_assessment(db, cid)
        if a is None:
            raise HTTPException(409, f"{c.ref} n'a pas encore d'évaluation.")
        asms[c.ref] = a
    hashes = {a.result["grid_hash"] for a in asms.values()}
    if len(hashes) > 1:
        raise HTTPException(409, "Les candidats sont évalués sur des versions de grille différentes : réévaluer avant de comparer (critères et poids figés).")
    names = list(asms)
    first = asms[names[0]].result
    rows = []
    for c in first["criteria"]:
        row = {"key": c["key"], "label": c["label"], "weight": c["weight"], "category": c["category"], "cells": {}}
        for n in names:
            r = next(x for x in asms[n].result["criteria"] if x["key"] == c["key"])
            row["cells"][n] = {"level": r["level"], "points": r["points"], "justification": r["justification"]}
        pts = [row["cells"][n]["points"] for n in names]
        row["spread"] = round(max(pts) - min(pts), 2)
        rows.append(row)
    ranking = sorted(names, key=lambda n: -asms[n].score_documented)
    why = {}
    if len(ranking) >= 2:
        a, b = ranking[0], ranking[1]
        diffs = sorted(rows, key=lambda r: -abs(r["cells"][a]["points"] - r["cells"][b]["points"]))[:3]
        why = {"leader": a, "second": b, "main_differences": [{"criterion": r["label"], "leader_points": r["cells"][a]["points"], "second_points": r["cells"][b]["points"],
                                                              "leader_level": r["cells"][a]["level"], "second_level": r["cells"][b]["level"]} for r in diffs if r["spread"] > 0]}
    return {"grid_hash": next(iter(hashes)), "grid_version": first["grid_version"], "candidates": names, "ranking": ranking, "rows": rows, "why": why,
            "summary": {n: {"score": asms[n].score_documented, "potential": asms[n].score_potential, "tier": asms[n].tier,
                            "open_mandatory": asms[n].result["open_mandatory"], "alerts": asms[n].result["alerts"]} for n in names}}


def questions(db: Session, user: User, m: Mission, cand: Candidate) -> dict[str, Any]:
    a = latest_assessment(db, cand.id)
    if a is None:
        raise HTTPException(409, "Aucune évaluation : pas de questions ciblées tant que le CV n'est pas évalué sur la grille figée.")
    q = qa.generate(a.result)
    db.add(Qualification(mission_id=m.id, candidate_id=cand.id, assessment_id=a.id, questions=q, created_by=user.id))
    audit.log(db, user.id, "qualification.generate", "candidate", cand.id, m.id, assessment=a.id)
    return {"assessment_id": a.id, **q}


def set_status(db: Session, user: User, m: Mission, cand: Candidate, status: str, comment: str) -> Candidate:
    if status not in CANDIDATE_STATUSES:
        raise HTTPException(422, "Statut inconnu.")
    if status == "ecarte" and len(comment.strip()) < 5:
        raise HTTPException(422, "Écarter un profil est une décision humaine : un motif est obligatoire (aucun rejet automatique).")
    cand.status, cand.status_comment, cand.status_by = status, comment, user.id
    audit.log(db, user.id, "candidate.status", "candidate", cand.id, m.id, status=status)
    return cand


def delete_candidate(db: Session, user: User, m: Mission, cand: Candidate) -> None:
    audit.log(db, user.id, "candidate.delete", "candidate", cand.id, m.id, ref=cand.ref)
    db.delete(cand)


def purge_expired(db: Session, user: User) -> dict[str, int]:
    """Suppression selon la politique de conservation configurée (valeur à valider avec le DPO)."""
    today = date.today()
    docs = list(db.scalars(select(Document).where(Document.expires_at.is_not(None), Document.expires_at < today)))
    cands = set()
    for d in docs:
        if d.candidate_id:
            cands.add(d.candidate_id)
    n_c = 0
    for cid in cands:
        c = db.get(Candidate, cid)
        if c:
            db.delete(c)
            n_c += 1
    n_d = 0
    for d in docs:
        db.delete(d)
        n_d += 1
    audit.log(db, user.id, "retention.purge", "system", "", "", documents=n_d, candidates=n_c)
    return {"documents": n_d, "candidates": n_c}


# ----------------------------------------------------------------- IA assistée (optionnelle, vérifiée par citation)
def llm_assist(db: Session, user: User, m: Mission, cand: Candidate, provider) -> dict[str, Any]:
    """Le modèle DÉSIGNE des passages ; chaque citation est vérifiée dans le document et re-classée par le moteur déterministe.
    Les passages vérifiés entrent comme preuves « à valider » (plafonnées à partiel) ; les autres sont des hypothèses hors score."""
    from ..domain.claims import verify_claims
    from ..llm.provider import LLMUnavailable, propose_claims
    grid_row = ms.latest_frozen(db, m.id)
    if grid_row is None:
        raise HTTPException(409, "Aucune grille figée : l'IA n'est utilisée que pour documenter des critères validés.")
    doc = db.scalar(select(Document).where(Document.candidate_id == cand.id, Document.status == "done").order_by(Document.created_at.desc()).limit(1))
    if doc is None or not doc.text:
        raise HTTPException(409, "Aucun document exploitable pour ce candidat.")
    grid = ms.grid_from_row(grid_row)
    criteria = [{"key": c.key, "label": c.label} for c in grid.scored() if c.kind in ("skill", "activity", "domain") and not c.members]
    try:
        claims = propose_claims(provider, doc.text, criteria)
    except LLMUnavailable as e:
        raise HTTPException(503, str(e))
    verified = verify_claims(claims, doc.text)
    stored = 0
    for v in verified:
        if v.status == "verified" and v.level is not None:
            db.add(Evidence(mission_id=m.id, candidate_id=cand.id, subject_key=v.claim.criterion_key, kind=EvidenceKind.SUPPORTS.value, source=EvidenceSource.CV_DOCUMENT.value,
                            reliability=Reliability.DOCUMENTED_CV.value, level=v.level.value, excerpt=doc.text[v.start:v.end], auto_generated=True, needs_verification=True,
                            why_verify="Passage désigné par l'IA, vérifié par citation dans le document : à valider par un recruteur.", created_by=user.id))
            stored += 1
        else:
            db.add(Evidence(mission_id=m.id, candidate_id=cand.id, subject_key=v.claim.criterion_key, kind=EvidenceKind.SUPPORTS.value, source=EvidenceSource.CV_DOCUMENT.value,
                            reliability=Reliability.HYPOTHESIS.value, level=None, excerpt=(v.claim.quote or "(aucune citation)")[:400], auto_generated=True, needs_verification=True,
                            why_verify=v.reason, created_by=user.id))
    db.flush()
    audit.log(db, user.id, "llm.assist", "candidate", cand.id, m.id, provider=getattr(provider, "name", "?"), claims=len(claims), verified=stored, downgraded=len(verified) - stored)
    asm = reassess(db, user, m, cand, "ia_assistee", "Passages désignés par l'IA et vérifiés par citation")
    return {"claims": [v.to_dict() for v in verified], "assessment": asm}
