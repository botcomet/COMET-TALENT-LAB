"""Bibliothèque collective (§17) : enseignements validés, jamais des règles imposées automatiquement."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..domain import lexicon as lx
from ..domain.safety import has_contact_details
from ..models import KnowledgeEntry, Mission, ScoringCorrection, User

KINDS = ("methode_recherche", "booleen", "enseignement_retour_client", "faux_positif", "question_qualification", "erreur_matching", "exemple_preuve", "note_metier")


def entry_dict(e: KnowledgeEntry, author: str = "") -> dict[str, Any]:
    return {"id": e.id, "kind": e.kind, "title": e.title, "body": e.body, "role_family": e.role_family, "tags": e.tags, "client_specific": e.client_specific,
            "status": e.status, "origin_mission_id": e.origin_mission_id, "author_id": e.author_id, "author": author, "validated_by": e.validated_by,
            "version": e.version, "created_at": e.created_at.isoformat(), "updated_at": e.updated_at.isoformat()}


def _no_contact_details(*texts: str) -> None:
    """La bibliothèque est lue par toute l'équipe : aucune coordonnée directe de candidat ou de contact client (RGPD : minimisation)."""
    if any(has_contact_details(t) for t in texts):
        raise HTTPException(422, "La bibliothèque est partagée : ne pas y inscrire d'email, de téléphone ni de lien. Décrire la situation sans donnée nominative.")


def create(db: Session, user: User, kind: str, title: str, body: str, role_family: str = "", tags: list[str] | None = None, mission_id: str | None = None) -> KnowledgeEntry:
    if kind not in KINDS:
        raise HTTPException(422, f"Type inconnu : {', '.join(KINDS)}")
    if len(title.strip()) < 5 or len(body.strip()) < 20:
        raise HTTPException(422, "Titre et contenu sont obligatoires (contenu : 20 caractères minimum).")
    _no_contact_details(title, body)
    e = KnowledgeEntry(kind=kind, title=title.strip(), body=body.strip(), role_family=role_family, tags=tags or [], client_specific=True, status="proposed",
                       origin_mission_id=mission_id, author_id=user.id)
    db.add(e)
    db.flush()
    audit.log(db, user.id, "knowledge.create", "knowledge", e.id, mission_id or "", kind=kind)
    return e


def update(db: Session, user: User, e: KnowledgeEntry, **changes: Any) -> KnowledgeEntry:
    if e.author_id != user.id and user.role != "admin":
        raise HTTPException(403, "Seul l'auteur (ou un administrateur) modifie une entrée ; proposer une nouvelle version sinon.")
    _no_contact_details(changes.get("title") or "", changes.get("body") or "")
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
    if generalize and not _can_govern(user, e):
        raise HTTPException(403, "Seul l'auteur, un pilote ou un administrateur peut généraliser un enseignement (retirer « spécifique client »).")
    e.status, e.validated_by = "published", user.id
    if generalize:
        e.client_specific = False
    audit.log(db, user.id, "knowledge.publish", "knowledge", e.id, e.origin_mission_id or "", generalized=generalize)
    return e


def _can_govern(user: User, e: KnowledgeEntry) -> bool:
    return e.author_id == user.id or user.role in ("admin", "pilote")


def retire(db: Session, user: User, e: KnowledgeEntry) -> KnowledgeEntry:
    if not _can_govern(user, e):
        raise HTTPException(403, "Seul l'auteur, un pilote ou un administrateur peut retirer une entrée de la bibliothèque collective.")
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
    """Cas de test issus des corrections de scoring (§17.3). Lecture seule : aucune règle n'est modifiée automatiquement.
    Les extraits de CV ne sont montrés qu'à qui a un droit de lecture sur la MISSION ; un administrateur sans accès voit le cas pédagogique
    (critère, niveaux, nature de l'erreur) mais jamais les extraits."""
    from ..models import Mission as M
    from ..security import access_level
    out = []
    for c in db.scalars(select(ScoringCorrection).order_by(ScoringCorrection.created_at.desc())):
        mm = db.get(M, c.mission_id)
        if mm is None:
            continue
        level = access_level(db, user, mm)
        can_read = level in ("owner", "edition", "lecture")
        if not can_read and user.role != "admin":
            continue
        case = dict(c.regression_case or {})
        if not can_read:
            case["evidence_excerpts"] = []
            case["justification_observed"] = "[masqué : pas de droit de lecture sur la mission]"
            case["redacted_for_viewer"] = True
        out.append({"id": c.id, "criterion": c.criterion_key, "error_nature": c.error_nature, "old_level": c.old_level, "new_level": c.new_level,
                    "case": case, "created_at": c.created_at.isoformat()})
    return out
