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


class BodyLimit:
    """Refuse (413) toute requête dont le corps dépasse la limite : ``Content-Length`` déclaré, ou octets réellement reçus (transfert fragmenté).
    Import de CV : lot × taille maximale ; toute autre requête : 2 Mo."""

    def __init__(self, app, upload_limit, default_limit: int = 2 * 1024 * 1024):
        self.app, self.upload_limit, self.default_limit = app, upload_limit, default_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        limit = self.upload_limit() if scope["path"].endswith("/cvs") else self.default_limit
        declared = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if declared is not None and declared.isdigit() and int(declared) > limit:
            return await self._reject(send)
        received = 0

        async def counted():
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > limit:
                    raise _TooLarge()
            return msg
        try:
            await self.app(scope, counted, send)
        except _TooLarge:
            await self._reject(send)

    @staticmethod
    async def _reject(send):
        body = b'{"detail":"Requ\xc3\xaate trop volumineuse."}'
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


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
        if s.env in ("dev", "test") and s.auth_mode == "dev":
            seed_demo_users()
        from .services.matching import requeue_stale
        requeue_stale()
        yield

    app = FastAPI(title="COMET Talent Lab", version="0.1.0", lifespan=lifespan, docs_url="/api/docs" if s.env in ("dev", "test") else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if s.env in ("dev", "test") else None, dependencies=[Depends(csrf_guard)])
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

    app.add_middleware(BodyLimit, upload_limit=lambda: get_settings().max_batch_size * get_settings().max_file_mb * 1024 * 1024 + 1024 * 1024)
    if FRONTEND.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
    return app


app = create_app()
