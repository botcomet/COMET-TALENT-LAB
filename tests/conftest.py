"""Fixtures d'intégration : application réelle, base SQLite temporaire, traitement des CV synchrone."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import briefs as B, cvs as C
from tests.helpers import make_docx, make_pdf

HDR = {"X-Requested-With": "talentlab"}


def pytest_configure(config):
    config.addinivalue_line("markers", "business: tests métier")


PG_URL = os.environ.get("TALENTLAB_TEST_DATABASE_URL")      # rejoue la suite sur PostgreSQL si défini
sqlite_only = pytest.mark.skipif(bool(PG_URL), reason="vérification propre au fichier SQLite (couverte séparément sur PostgreSQL)")


def _reset_pg() -> None:
    from sqlalchemy import create_engine
    from talentlab import models  # noqa: F401
    from talentlab.db import Base
    eng = create_engine(PG_URL)
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENTLAB_ENV", "test")
    monkeypatch.setenv("TALENTLAB_DATABASE_URL", PG_URL or f"sqlite:///{tmp_path}/test.db")
    if PG_URL:
        _reset_pg()
    monkeypatch.setenv("TALENTLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TALENTLAB_PROCESSING_MODE", "inline")
    monkeypatch.setenv("TALENTLAB_AUTH_MODE", "dev")
    from talentlab import config, db
    config.get_settings.cache_clear()
    db._engine = None
    db._SessionLocal = None
    yield tmp_path
    config.get_settings.cache_clear()
    if db._engine is not None:
        db._engine.dispose()
    db._engine = None
    db._SessionLocal = None
    if PG_URL:
        _reset_pg()


class Session:
    """Un client HTTP connecté en tant qu'un utilisateur fictif donné."""

    def __init__(self, app, email: str):
        self.c = TestClient(app, headers=HDR)
        self.c.__enter__()
        r = self.c.post("/api/auth/dev-login", json={"email": email})
        assert r.status_code == 200, r.text
        self.user = r.json()

    def close(self):
        self.c.__exit__(None, None, None)

    def __getattr__(self, name):
        return getattr(self.c, name)


@pytest.fixture()
def app(app_env):
    from talentlab.main import create_app
    return create_app()


@pytest.fixture()
def tm(app):
    """Sessions pour plusieurs Talent Managers fictifs ; la première ouvre le cycle de vie de l'application."""
    opened: list[Session] = []

    def make(n: int = 1):
        email = f"tm{n}.demo@example.invalid"
        if not opened:
            # le premier client déclenche le lifespan (création du moteur + comptes de démonstration)
            s = Session(app, email)
        else:
            s = Session(app, email)
        opened.append(s)
        return s

    yield make
    for s in opened:
        s.close()


def create_mission(s: Session, title=B.TLJ_TITLE, brief=B.TLJ, client="Client A (fictif)") -> dict:
    r = s.post("/api/missions", json={"client": client, "title": title, "brief": brief})
    assert r.status_code == 201, r.text
    return r.json()


def freeze(s: Session, mission_id: str, *, mutate=None, allow=True) -> dict:
    """Valide les exigences puis propose et fige la grille ; ``mutate`` : {key: patch}."""
    for key, patch in (mutate or {}).items():
        m = s.get(f"/api/missions/{mission_id}").json()
        rid = next(r["id"] for r in m["requirements"] if r["key"] == key and r["status"] == "active")
        rr = s.patch(f"/api/missions/{mission_id}/requirements/{rid}", json=patch)
        assert rr.status_code == 200, rr.text
    assert s.post(f"/api/missions/{mission_id}/requirements/validate", json={}).status_code == 200
    g = s.post(f"/api/missions/{mission_id}/grid/propose").json()
    r = s.post(f"/api/missions/{mission_id}/grid/{g['id']}/validate", json={"allow_unresolved_clarifications": allow})
    assert r.status_code == 200, r.text
    return r.json()


def upload(s: Session, mission_id: str, docs: list[tuple[str, bytes]]):
    return s.post(f"/api/missions/{mission_id}/cvs", files=[("files", (n, d, "application/octet-stream")) for n, d in docs])


def pdf(text: str) -> bytes:
    return make_pdf(text)
