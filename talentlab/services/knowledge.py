"""Bibliothèque collective (§17) : enseignements validés, jamais des règles imposées automatiquement."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..domain import lexicon as lx
from ..models import KnowledgeEntry, Mission, ScoringCorrection, User

KINDS = ("methode_recherche", "booleen", "enseignement_retour_client", "faux_positif", "question_qualification", "erreur_matching", "exemple_preuve", "note_metier")


def entry_dict(e: KnowledgeEntry, author: str = "") -> dict[str, Any]:
    return {"id": e.id, "kind": e.kind, "title": e.title, "body": e.body, "role_family": e.role_family, "tags": e.tags, "client_specific": e.client_specific,
            "status": e.status, "origin_mission_id": e.origin_mission_id, "author_id": e.author_id, "author": author, "validated_by": e.validated_by,
            "version": e.version, "created_at": e.created_at.isoformat(), "updated_at": e.updated_at.isoformat()}


def create(db: Session, user: User, kind: str, title: str, body: str, role_family: str = "", tags: list[str] | None = None, mission_id: str | None = None) -> KnowledgeEntry:
    if kind not in KINDS:
        raise HTTPException(422, f"Type inconnu : {', '.join(KINDS)}")
    if len(title.strip()) < 5 or len(body.strip()) < 20:
        raise HTTPException(422, "Titre et contenu sont obligatoires (contenu : 20 caractères minimum).")
    e = KnowledgeEntry(kind=kind, title=title.strip(), body=body.strip(), role_family=role_family, tags=tags or [], client_specific=True, status="proposed",
                       origin_mission_id=mission_id, author_id=user.id)
    db.add(e)
    db.flush()
    audit.log(db, user.id, "knowledge.create", "knowledge", e.id, mission_id or "", kind=kind)
    return e


def update(db: Session, user: User, e: KnowledgeEntry, **changes: Any) -> KnowledgeEntry:
    if e.author_id != user.id and user.role != "admin":
        raise HTTPException(403, "Seul l'auteur (ou un administrateur) modifie une entrée ; proposer une nouvelle version sinon.")
    for k in ("title", "body", "role_family", "tags"):
        if k in changes and changes[k] is not None:
            setattr(e, k, changes[k])
    e.version += 1
    e.status = "proposed"                      # toute modification repasse en validation
    e.validated_by = ""
    audit.log(db, user.id, "knowledge.update", "knowledge", e.id, e.origin_mission_id or "", version=e.version)
    return e


def publish(db: Session, user: User, e: KnowledgeEntry, *, generalize: bool = False) -> KnowledgeEntry:
    """Un Talent Manager valide avant publication. La généralisation (retirer « spécifique client ») est un acte explicite et séparé."""
    if e.author_id == user.id and e.kind == "enseignement_retour_client" and generalize:
        pass          # l'auteur peut généraliser son propre enseignement ; la traçabilité est assurée par l'audit
    e.status, e.validated_by = "published", user.id
    if generalize:
        e.client_specific = False
    audit.log(db, user.id, "knowledge.publish", "knowledge", e.id, e.origin_mission_id or "", generalized=generalize)
    return e


def retire(db: Session, user: User, e: KnowledgeEntry) -> KnowledgeEntry:
    e.status = "retired"
    audit.log(db, user.id, "knowledge.retire", "knowledge", e.id, "")
    return e


def list_entries(db: Session, user: User, *, status: str | None = None, role_family: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
    stmt = select(KnowledgeEntry)
    stmt = stmt.where(or_(KnowledgeEntry.status == "published", KnowledgeEntry.author_id == user.id))      # un brouillon d'autrui reste privé
    if status:
        stmt = stmt.where(KnowledgeEntry.status == status)
    if role_family:
        stmt = stmt.where(KnowledgeEntry.role_family == role_family)
    out = []
    for e in db.scalars(stmt.order_by(KnowledgeEntry.updated_at.desc())):
        if q and q.lower() not in (e.title + " " + e.body).lower():
            continue
        a = db.get(User, e.author_id)
        out.append(entry_dict(e, a.display_name if a else ""))
    return out


def suggest_for_mission(db: Session, m: Mission, reqs_keys: list[str]) -> list[dict[str, Any]]:
    """Propose (sans rien imposer) les enseignements publiés sur des missions comparables. Une entrée propre à un client reste signalée comme telle."""
    fam = lx.detect_role_family(m.title)
    skills = {k.split(":", 1)[-1] for k in reqs_keys}
    out = []
    for e in db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.status == "published")):
        if e.origin_mission_id == m.id:
            continue
        score = (2 if fam and e.role_family == fam.key else 0) + len(skills & set(e.tags))
        if score:
            d = entry_dict(e)
            d["relevance"] = score
            d["caution"] = "Enseignement propre à un client : à adapter, jamais une règle impérative universelle." if e.client_specific else ""
            out.append(d)
    return sorted(out, key=lambda d: -d["relevance"])[:8]


def regression_cases(db: Session, user: User) -> list[dict[str, Any]]:
    """Cas de test issus des corrections de scoring (§17.3). Lecture seule : aucune règle n'est modifiée automatiquement."""
    from ..models import Mission as M
    out = []
    for c in db.scalars(select(ScoringCorrection).order_by(ScoringCorrection.created_at.desc())):
        mm = db.get(M, c.mission_id)
        if mm and mm.owner_id != user.id and user.role != "admin":
            continue
        out.append({"id": c.id, "criterion": c.criterion_key, "error_nature": c.error_nature, "old_level": c.old_level, "new_level": c.new_level,
                    "case": c.regression_case, "created_at": c.created_at.isoformat()})
    return out
