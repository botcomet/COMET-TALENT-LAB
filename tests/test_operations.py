"""Exploitation : migrations, rétention planifiée, consignation de la tête d'audit, limitation de débit."""
import json
import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from talentlab import manage
from talentlab.db import Base


def _url(tmp_path):
    return f"sqlite:///{tmp_path}/mig.db"


def test_migrations_build_exactly_the_schema_of_the_models(tmp_path):
    url = _url(tmp_path)
    rev = manage.migrate(url)
    assert rev == "0001" and manage.schema_is_current(url)
    eng = create_engine(url)
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"les migrations et les modèles divergent : {diff[:3]}"
    assert {"users", "missions", "audit_events", "alembic_version"} <= set(inspect(eng).get_table_names())
    eng.dispose()


def test_migrations_can_be_rolled_back_and_replayed(tmp_path):
    url = _url(tmp_path)
    manage.migrate(url)
    cfg = manage.alembic_config(url)
    command.downgrade(cfg, "base")
    eng = create_engine(url)
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}
    eng.dispose()
    manage.migrate(url)
    assert manage.schema_is_current(url)


def test_a_development_database_without_migration_history_is_never_silently_stamped(tmp_path):
    url = _url(tmp_path)
    eng = create_engine(url)
    from talentlab import models  # noqa: F401
    Base.metadata.create_all(eng)
    eng.dispose()
    with pytest.raises(SystemExit, match="sans historique de migrations"):
        manage.migrate(url)


@pytest.mark.skipif(not os.environ.get("TALENTLAB_TEST_DATABASE_URL"), reason="PostgreSQL non configuré")
def test_migrations_also_match_the_models_on_postgresql():
    url = os.environ["TALENTLAB_TEST_DATABASE_URL"]
    eng = create_engine(url)
    Base.metadata.drop_all(eng)
    with eng.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    eng.dispose()
    manage.migrate(url)
    eng = create_engine(url)
    with eng.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff[:3]
    Base.metadata.drop_all(eng)
    with eng.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    eng.dispose()


def test_production_refuses_to_start_on_a_database_that_is_not_migrated(tmp_path):
    from cryptography.fernet import Fernet
    from talentlab import db
    from talentlab.config import Settings
    s = Settings(env="dev", database_url=_url(tmp_path), data_dir=tmp_path / "d", encryption_key=Fernet.generate_key().decode())
    s.env = "prod"                  # (la garde de configuration exige PostgreSQL ; ici on teste la garde de SCHÉMA, indépendante du moteur)
    with pytest.raises(RuntimeError, match="manage migrate"):
        db.init_engine(s)
    manage.migrate(_url(tmp_path))
    db.init_engine(s)               # base migrée : démarrage accepté, aucune table créée implicitement
    db._engine.dispose()
    db._engine = None
    db._SessionLocal = None


def test_manage_check_reports_the_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TALENTLAB_DATABASE_URL", _url(tmp_path))
    from talentlab import config
    config.get_settings.cache_clear()
    assert manage.main(["check"]) == 1
    manage.migrate()
    assert manage.main(["check"]) == 0
    assert manage.main(["current"]) == 0 and json.loads(capsys.readouterr().out.splitlines()[-1])["a_jour"] is True
    config.get_settings.cache_clear()


# ---------------------------------------------------------------- maintenance planifiée
def _expired_setup(tm):
    from datetime import date, timedelta
    from tests.conftest import create_mission, freeze, upload
    from tests.fixtures import cvs as C
    s = tm(1)
    mid = create_mission(s)["id"]
    freeze(s, mid)
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode()), ("b.txt", C.CV_TLJ_B.encode())])
    from talentlab.db import session_scope
    from talentlab.models import Document
    with session_scope() as db:
        d = next(x for x in db.query(Document).all() if x.filename == "a.txt")
        d.expires_at = date.today() - timedelta(days=1)
    return s, mid


def test_scheduled_purge_is_off_by_default_and_never_runs_unless_explicitly_enabled(tm, monkeypatch):
    from talentlab import config
    from talentlab.services.maintenance import Scheduler, run_once
    s, mid = _expired_setup(tm)
    assert Scheduler(config.get_settings()).enabled is False
    assert run_once() == {}, "aucune purge sans activation explicite"
    assert len(s.get(f"/api/missions/{mid}/candidates").json()) == 2


def test_scheduled_purge_when_enabled_deletes_only_expired_data_and_is_audited_as_system(tm, monkeypatch):
    from talentlab import config
    from talentlab.db import session_scope
    from talentlab.models import AuditEvent
    from talentlab.services.maintenance import run_once
    s, mid = _expired_setup(tm)
    monkeypatch.setenv("TALENTLAB_RETENTION_AUTO_PURGE", "true")
    config.get_settings.cache_clear()
    res = run_once()
    assert res["purge"] == {"documents": 1, "candidates": 1}
    assert len(s.get(f"/api/missions/{mid}/candidates").json()) == 1, "le CV non expiré est conservé"
    with session_scope() as db:
        ev = db.query(AuditEvent).filter(AuditEvent.action == "retention.purge").one()
        assert ev.user_id == "system"
    assert run_once()["purge"] == {"documents": 0, "candidates": 0}, "idempotent"


def test_the_audit_head_is_written_atomically_to_the_configured_path(tm, monkeypatch, tmp_path):
    from talentlab import config
    from talentlab.services.maintenance import run_once
    s, mid = _expired_setup(tm)
    target = tmp_path / "off-db" / "audit-head.json"
    monkeypatch.setenv("TALENTLAB_AUDIT_HEAD_PATH", str(target))
    config.get_settings.cache_clear()
    res = run_once()
    head = json.loads(target.read_text())
    assert res["audit_head"]["ok"] and head["ok"] and head["head_seq"] >= 5 and len(head["head_hash"]) == 64
    assert not list(target.parent.glob("*.tmp")), "pas de fichier temporaire résiduel"
    # une troncature du journal (suppression des derniers événements) devient visible en comparant à la tête consignée
    from talentlab.db import session_scope
    from talentlab.models import AuditEvent
    with session_scope() as db:
        for ev in db.query(AuditEvent).order_by(AuditEvent.seq.desc()).limit(3).all():
            db.delete(ev)
    after = run_once()["audit_head"]
    assert after["ok"] is True, "la chaîne seule ne voit pas la troncature…"
    assert after["head_seq"] < head["head_seq"], "…mais la tête consignée hors de la base la révèle"


def test_the_scheduler_thread_runs_ticks_and_stops_cleanly(tm, monkeypatch, tmp_path):
    import time
    from talentlab import config
    from talentlab.services.maintenance import Scheduler
    _expired_setup(tm)
    monkeypatch.setenv("TALENTLAB_MAINTENANCE_INTERVAL_HOURS", "0.0001")
    monkeypatch.setenv("TALENTLAB_AUDIT_HEAD_PATH", str(tmp_path / "head.json"))
    config.get_settings.cache_clear()
    sch = Scheduler(config.get_settings(), first_delay_s=0.05)
    assert sch.start() is True and sch.start() is False
    for _ in range(100):
        if (tmp_path / "head.json").exists():
            break
        time.sleep(0.05)
    sch.stop()
    assert (tmp_path / "head.json").exists() and not sch._thread.is_alive()


# ---------------------------------------------------------------- limitation de débit
def test_heavy_endpoints_are_rate_limited_per_user_with_retry_after(tm, monkeypatch):
    from talentlab import config
    from tests.conftest import Session, create_mission
    a, b = tm(1), tm(2)
    mid = create_mission(a)["id"]
    monkeypatch.setenv("TALENTLAB_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("TALENTLAB_RATE_LIMIT_HEAVY_PER_MINUTE", "3")
    config.get_settings.cache_clear()
    f = [("files", ("a.txt", b"x" * 300, "text/plain"))]
    codes = [a.post("/api/tools/extract-text", files=f).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    r = a.post("/api/tools/extract-text", files=f)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert b.post("/api/tools/extract-text", files=f).status_code == 200, "la limite est PAR utilisateur"
    assert a.get(f"/api/missions/{mid}").status_code == 200, "les lectures ordinaires ne sont pas touchées par la limite des opérations lourdes"
    assert a.get("/api/health").status_code == 200


def test_rate_limit_window_slides(monkeypatch):
    import asyncio
    from talentlab.ratelimit import RateLimit
    from types import SimpleNamespace
    now = [0.0]
    sent = []

    async def app(scope, receive, send):
        sent.append("ok")

    async def send(msg):
        if "status" in msg:
            sent.append(msg["status"])
    rl = RateLimit(app, lambda: SimpleNamespace(rate_limit_enabled=True, rate_limit_general_per_minute=2, rate_limit_heavy_per_minute=1), clock=lambda: now[0])
    scope = {"type": "http", "path": "/api/missions", "method": "GET", "headers": [(b"x-comet-user-email", b"a@example.invalid")], "client": ("1.2.3.4", 1)}
    run = lambda: asyncio.run(rl(scope, None, send))
    run(); run(); run()
    assert sent == ["ok", "ok", 429]
    now[0] = 61.0
    run()
    assert sent[-1] == "ok", "la fenêtre est glissante : après 60 s l'accès est rétabli"


def test_every_variable_documented_in_env_example_is_a_real_setting():
    """Le fichier d'exemple ne doit jamais dériver du code : une variable inconnue serait silencieusement ignorée en production."""
    import re
    from pathlib import Path
    from talentlab.config import Settings
    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
    names = set(re.findall(r"^#?\s*TALENTLAB_([A-Z0-9_]+)=", text, flags=re.M))
    fields = {f.upper() for f in Settings.model_fields}
    assert names, "aucune variable détectée"
    assert not names - fields, f"variables inconnues : {sorted(names - fields)}"
    # les réglages d'exploitation introduits en V1.1 sont bien documentés
    assert {"MAINTENANCE_INTERVAL_HOURS", "RETENTION_AUTO_PURGE", "AUDIT_HEAD_PATH", "RATE_LIMIT_ENABLED", "RATE_LIMIT_HEAVY_PER_MINUTE"} <= names
