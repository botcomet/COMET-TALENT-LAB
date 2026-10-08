"""Authentification, autorisations et défenses applicatives (§25).

Principes :
- les en-têtes d'identité d'une passerelle ne sont jamais crus « librement » : ils exigent un secret
  partagé comparé en temps constant, et l'utilisateur doit être provisionné et actif ;
- le mode de développement (connexion sans mot de passe) est refusé en production par la configuration ;
- missions privées par défaut ; partage explicite à trois niveaux ; l'existence d'une mission non
  accessible n'est jamais révélée (404) ;
- toute requête modifiante exige un en-tête personnalisé (défense CSRF).
"""
from __future__ import annotations

import hmac
from typing import Callable

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .enums_app import ACCESS_RANK
from .models import Mission, Share, User

COOKIE = "talentlab_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "talentlab"


def _serializer(s: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(s.resolved_session_secret(), salt="talentlab-session")


def issue_session(response: Response, user: User, s: Settings | None = None) -> None:
    s = s or get_settings()
    token = _serializer(s).dumps({"uid": user.id})
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=(s.env == "prod"), max_age=s.session_hours * 3600, path="/")


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    s = get_settings()
    user: User | None = None
    if s.auth_mode == "gateway":
        email = request.headers.get(s.gateway_email_header, "").strip().lower()
        given = request.headers.get(s.gateway_secret_header, "")
        if not email or not s.gateway_secret or not hmac.compare_digest(given.encode(), s.gateway_secret.encode()):
            raise HTTPException(401, "Authentification requise.")
        user = db.scalar(select(User).where(User.email == email, User.active.is_(True)))
    else:
        tok = request.cookies.get(COOKIE)
        if tok:
            try:
                data = _serializer(s).loads(tok, max_age=s.session_hours * 3600)
                user = db.get(User, data["uid"])
            except (BadSignature, SignatureExpired, KeyError):
                user = None
    if user is None or not user.active:
        raise HTTPException(401, "Authentification requise.")
    request.state.user = user
    return user


async def csrf_guard(request: Request) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise HTTPException(403, "En-tête de protection CSRF manquant.")


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Réservé aux administrateurs.")
    return user


# ----------------------------------------------------------------- accès aux missions
def access_level(db: Session, user: User, mission: Mission) -> str | None:
    if mission.owner_id == user.id:
        return "owner"
    sh = db.scalar(select(Share).where(Share.mission_id == mission.id, Share.user_id == user.id))
    return sh.scope if sh else None


def require_mission(db: Session, user: User, mission_id: str, minimum: str = "lecture") -> tuple[Mission, str]:
    m = db.get(Mission, mission_id)
    level = access_level(db, user, m) if m else None
    if m is None or level is None:
        raise HTTPException(404, "Mission introuvable.")          # n'indique pas l'existence d'une mission non partagée
    if ACCESS_RANK[level] < ACCESS_RANK[minimum]:
        raise HTTPException(403, "Droits insuffisants pour cette action sur cette mission.")
    return m, level


def mission_dep(minimum: str) -> Callable:
    def dep(mission_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> tuple[Mission, str, User]:
        m, lvl = require_mission(db, user, mission_id, minimum)
        return m, lvl, user
    return dep
