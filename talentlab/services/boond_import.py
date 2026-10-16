"""Import d'un besoin Boond (LECTURE SEULE) vers une mission Talent Lab.

- une mission importée est PRIVÉE à son importateur, comme toute mission ;
- la référence Boond sert de clé de déduplication par utilisateur (contrainte d'unicité en base : deux imports simultanés ne créent qu'une mission) ;
- un second import d'un besoin inchangé ne fait rien ; un besoin modifié dans Boond ajoute une NOUVELLE source (les sources sont immuables) et l'historique
  de la mission est conservé ; la grille figée n'est jamais modifiée silencieusement (la bannière de dérive existante signale l'écart) ;
- seuls le titre, la société, le lieu, les dates, la description et les critères sont recopiés : jamais de contact, budget, tarif, responsable ni candidat ;
- rien n'est écrit dans Boond.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..integrations.boond import SOURCE_NAME, BoondError, BoondSource, Need, compose_brief, content_hash, has_usable_text
from ..models import Mission, User
from . import missions as ms


def _iso_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def list_needs(db: Session, user: User, src: BoondSource, q: str, limit: int, offset: int) -> dict[str, Any]:
    try:
        rows = src.list_needs(q, limit, offset)
    except BoondError as e:
        raise HTTPException(502, str(e))
    ids = [r.external_id for r in rows]
    imported = {m.external_id: m.id for m in db.scalars(select(Mission).where(Mission.owner_id == user.id, Mission.external_source == SOURCE_NAME, Mission.external_id.in_(ids or [""])))}
    return {"needs": [{**asdict(r), "mission_id": imported.get(r.external_id)} for r in rows], "limit": limit, "offset": offset, "read_only": True}


def _existing(db: Session, user: User, external_id: str) -> Mission | None:
    return db.scalar(select(Mission).where(Mission.owner_id == user.id, Mission.external_source == SOURCE_NAME, Mission.external_id == external_id))


def import_need(db: Session, user: User, src: BoondSource, external_id: str) -> dict[str, Any]:
    try:
        need: Need | None = src.get_need(external_id)
    except BoondError as e:
        raise HTTPException(502, str(e))
    if need is None:
        raise HTTPException(404, "Besoin introuvable dans Boond (ou non visible).")
    if not (need.title or need.reference):
        raise HTTPException(422, "Le besoin Boond n'a ni intitulé ni référence : rien n'est importé.")
    if not has_usable_text(need):
        raise HTTPException(422, "Le besoin Boond ne contient pas de descriptif exploitable (moins de 40 caractères de description et de critères) : "
                                 "rien n'est importé plutôt que d'inventer un besoin. Compléter la fiche dans Boond, ou coller le descriptif manuellement.")
    brief, digest = compose_brief(need), content_hash(need)
    when = _iso_date(need.updated_at) or _iso_date(need.created_at)
    label = f"Boond {need.reference or need.external_id} (lecture seule)"
    m = _existing(db, user, need.external_id)
    if m is None:
        try:
            with db.begin_nested():
                m = ms.create_mission(db, user, need.company, need.title or need.reference, brief, source_date=when, author="Boond (lecture seule)", label=label,
                                      external=(SOURCE_NAME, need.external_id, digest))
        except IntegrityError:                                    # import simultané du même besoin : l'autre l'a emporté
            m = _existing(db, user, need.external_id)
            if m is None:
                raise
            return _result(db, m, "unchanged", "Ce besoin était déjà importé.")
        audit.log(db, user.id, "boond.import", "mission", m.id, m.id, external_id=need.external_id, status="created")
        return _result(db, m, "created", "Mission créée à partir du besoin Boond. Vérifiez et validez les exigences proposées : rien n'a été noté ni figé.")
    if m.external_hash == digest:
        return _result(db, m, "unchanged", "Le besoin n'a pas changé dans Boond depuis l'import : rien à mettre à jour.")
    ms.add_source(db, user, m, "brief_officiel", brief, source_date=when, author="Boond (lecture seule)", label=f"{label} — révision")
    m.external_hash = digest
    audit.log(db, user.id, "boond.import", "mission", m.id, m.id, external_id=need.external_id, status="updated")
    return _result(db, m, "updated", "Le besoin a changé dans Boond : une nouvelle source a été ajoutée (l'historique est conservé). "
                                     "Relisez les exigences proposées ; une grille déjà figée n'est pas modifiée.")


def _result(db: Session, m: Mission, status: str, message: str) -> dict[str, Any]:
    from ..api.routes import mission_summary
    return {"status": status, "message": message, "mission": mission_summary(db, m, "owner")}
