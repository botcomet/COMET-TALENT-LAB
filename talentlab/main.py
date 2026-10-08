"""Application FastAPI COMET Talent Lab."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .api.routes import router
from .config import get_settings
from .db import init_engine, session_scope
from .models import User
from .security import csrf_guard

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# Comptes FICTIFS de démonstration (jamais de vraies identités) — créés uniquement en mode développement.
DEMO_USERS = [
    ("admin.demo@example.invalid", "Admin Démo", "admin", "Paris"),
    ("pilote.demo@example.invalid", "Pilote Démo", "pilote", "Paris"),
    ("tm1.demo@example.invalid", "TM Démo 1", "talent_manager", "Paris"),
    ("tm2.demo@example.invalid", "TM Démo 2", "talent_manager", "Paris"),
    ("tm3.demo@example.invalid", "TM Démo 3", "talent_manager", "Lille"),
    ("tm4.demo@example.invalid", "TM Démo 4", "talent_manager", "Lyon"),
]

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


def seed_demo_users() -> None:
    with session_scope() as db:
        for email, name, role, region in DEMO_USERS:
            if db.scalar(select(User).where(User.email == email)) is None:
                db.add(User(email=email, display_name=name, role=role, region=region))


def create_app() -> FastAPI:
    s = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_engine(s)
        if s.env != "prod" and s.auth_mode == "dev":
            seed_demo_users()
        from .services.matching import requeue_stale
        requeue_stale()
        yield

    app = FastAPI(title="COMET Talent Lab", version="0.1.0", lifespan=lifespan, docs_url="/api/docs" if s.env != "prod" else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if s.env != "prod" else None, dependencies=[Depends(csrf_guard)])
    app.include_router(router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):        # jamais de trace interne ni de donnée candidat dans une réponse d'erreur
        import logging
        logging.getLogger("talentlab").exception("Erreur non gérée sur %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Erreur interne. L'incident a été journalisé."}, status_code=500)

    if FRONTEND.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
    return app


app = create_app()
