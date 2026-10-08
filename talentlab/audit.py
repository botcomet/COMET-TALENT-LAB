"""Journal d'audit chaîné par hachage (§25 : traçabilité des modifications)."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, now

_SENSITIVE = {"text", "excerpt", "statement", "blob", "password", "secret", "token", "email_body"}


def _scrub(detail: dict[str, Any]) -> dict[str, Any]:
    """Le journal ne contient jamais de contenu de CV ni de note : seulement des identifiants et des métadonnées."""
    out: dict[str, Any] = {}
    for k, v in detail.items():
        if k in _SENSITIVE:
            out[k] = f"[{len(str(v))} caractères non journalisés]"
        elif isinstance(v, dict):
            out[k] = _scrub(v)
        elif isinstance(v, str) and len(v) > 300:
            out[k] = v[:300] + "…"
        else:
            out[k] = v
    return out


def _utc_naive(dt):
    """Date canonique : UTC sans fuseau. SQLite relit les dates sans fuseau ; le hachage doit être identique avant et après stockage."""
    from datetime import timezone
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt).replace(tzinfo=None)


def _key() -> bytes:
    """Clé de la chaîne : dérivée de la clé de chiffrement de l'application. Sans elle, recalculer les empreintes après avoir modifié le journal est impossible."""
    from .config import get_settings
    return hashlib.sha256(b"talentlab-audit-chain:" + get_settings().resolved_encryption_key().encode()).digest()


def _digest(prev: str, ev: AuditEvent) -> str:
    blob = json.dumps({"p": prev, "at": _utc_naive(ev.at).isoformat(), "u": ev.user_id, "a": ev.action, "t": ev.entity_type, "e": ev.entity_id,
                       "m": ev.mission_id, "d": ev.detail}, sort_keys=True, ensure_ascii=False, default=str)
    return hmac.new(_key(), blob.encode("utf-8"), hashlib.sha256).hexdigest()


def log(db: Session, user_id: str, action: str, entity_type: str = "", entity_id: str = "", mission_id: str = "", **detail: Any) -> AuditEvent:
    last = db.scalar(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1).with_for_update())
    ev = AuditEvent(at=now(), user_id=user_id, action=action, entity_type=entity_type, entity_id=entity_id, mission_id=mission_id,
                    detail=_scrub(detail), prev_hash=last.hash if last else "")
    ev.hash = _digest(ev.prev_hash, ev)
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session) -> dict[str, Any]:
    """Vérifie la chaîne (HMAC) et renvoie la TÊTE (dernier numéro et empreinte) : à consigner HORS de la base (journal d'infrastructure, coffre) pour détecter aussi une
    SUPPRESSION des derniers événements, que la chaîne seule ne peut pas voir."""
    prev = ""
    n = 0
    head_seq = 0
    for ev in db.scalars(select(AuditEvent).order_by(AuditEvent.seq)):
        if ev.prev_hash != prev or _digest(prev, ev) != ev.hash:
            return {"ok": False, "broken_at_seq": ev.seq, "checked": n}
        prev = ev.hash
        head_seq = ev.seq
        n += 1
    return {"ok": True, "checked": n, "head_seq": head_seq, "head_hash": prev,
            "note": "Consigner head_seq et head_hash hors de la base : la chaîne seule ne détecte pas la suppression des derniers événements."}
