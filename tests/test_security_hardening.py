"""Durcissement issu de la revue de sécurité indépendante (8 oct. 2026) : chaque test reproduit un constat."""
import time

import pytest

from talentlab.domain.cv_extract import ExtractionError, extract_text
from tests.conftest import Session, create_mission, freeze, upload
from tests.fixtures import briefs as B, cvs as C
from tests.helpers import make_docx, make_docx_bomb, make_heavy_pdf, make_pdf


# ---------------------------------------------------------------- F6 : bombe DOCX
def test_a_docx_decompression_bomb_is_refused_instantly_before_anything_is_decompressed():
    bomb = make_docx_bomb(20)
    assert len(bomb) < 200_000, "précondition : un petit fichier"
    t0 = time.perf_counter()
    with pytest.raises(ExtractionError) as e:
        extract_text(bomb, "cv.docx")
    assert e.value.code == "too_large" and time.perf_counter() - t0 < 1.0


def test_a_normal_docx_is_still_read():
    assert "Kafka" in extract_text(make_docx(C.CV_TLJ_B), "cv.docx").text


# ---------------------------------------------------------------- F6/F7/F8 : extraction isolée
def test_isolated_extraction_returns_the_same_text_as_direct_extraction():
    from talentlab.services.extraction import extract_isolated
    for name, data in (("cv.pdf", make_pdf(C.CV_TLJ_B)), ("cv.docx", make_docx(C.CV_TLJ_B))):
        assert extract_isolated(data, name, timeout_s=30).text == extract_text(data, name).text


def test_isolated_extraction_kills_a_document_that_exceeds_its_time_budget():
    from talentlab.services.extraction import extract_isolated
    heavy = make_heavy_pdf(pages=40, ops=60_000)
    assert len(heavy) < 300_000
    t0 = time.perf_counter()
    with pytest.raises(ExtractionError) as e:
        extract_isolated(heavy, "lourd.pdf", timeout_s=2, memory_mb=1536)
    assert e.value.code in ("timeout", "too_long", "too_large") and time.perf_counter() - t0 < 15


def test_isolated_extraction_reports_resource_exhaustion_as_an_explicit_error_not_a_crash():
    from talentlab.services.extraction import extract_isolated
    with pytest.raises(ExtractionError) as e:
        extract_isolated(make_pdf(C.CV_TLJ_B), "cv.pdf", timeout_s=30, memory_mb=8)        # 8 Mo : plus rien ne tient, le processus échoue proprement
    assert e.value.code in ("too_large", "corrupt")


# ---------------------------------------------------------------- F8 : un pool par utilisateur
def test_each_user_has_their_own_small_pool_so_one_user_cannot_starve_the_others(app_env):
    from talentlab.services.matching import executor_for
    a, b = executor_for("user-a"), executor_for("user-b")
    assert a is not b and a is executor_for("user-a") and a._max_workers == 2


# ---------------------------------------------------------------- F9 : la route d'import ne bloque pas la boucle d'événements
def test_the_upload_route_is_synchronous_so_it_runs_in_the_thread_pool_not_on_the_event_loop():
    import asyncio
    from talentlab.api.routes import upload_cvs
    assert not asyncio.iscoroutinefunction(upload_cvs)


# ---------------------------------------------------------------- F10 : taille de requête, fichiers refusés non conservés
def test_oversized_requests_are_refused_with_413_before_being_read(tm, monkeypatch):
    s = tm(1)
    mid = create_mission(s)["id"]
    from talentlab import config
    monkeypatch.setenv("TALENTLAB_MAX_BATCH_SIZE", "1")
    monkeypatch.setenv("TALENTLAB_MAX_FILE_MB", "1")
    config.get_settings.cache_clear()
    big = b"x" * (3 * 1024 * 1024)
    assert s.post(f"/api/missions/{mid}/cvs", files=[("files", ("a.txt", big, "text/plain"))]).status_code == 413
    assert s.post("/api/missions", content=b'{"title":"' + b"a" * (3 * 1024 * 1024) + b'"}', headers={"Content-Type": "application/json"}).status_code == 413


def test_a_rejected_file_is_not_kept_in_the_database(tm):
    s = tm(1)
    mid = create_mission(s)["id"]
    freeze(s, mid)
    upload(s, mid, [("faux.bin", b"\x00\x01\x02 pas un CV " * 50), ("vide.pdf", b"%PDF-1.4 casse " * 20)])
    from talentlab.db import session_scope
    from talentlab.models import Document
    with session_scope() as db:
        for d in db.query(Document).all():
            assert d.status == "failed" and d.blob is None, (d.filename, d.status)


def test_listing_documents_does_not_load_the_encrypted_files(tm):
    s = tm(1)
    mid = create_mission(s)["id"]
    freeze(s, mid)
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode())])
    from sqlalchemy import inspect
    from talentlab.db import session_scope
    from talentlab.models import Document
    with session_scope() as db:
        d = db.query(Document).first()
        assert "blob" in inspect(d).unloaded and "text" in inspect(d).unloaded


# ---------------------------------------------------------------- boucle de plantage au redémarrage
def test_a_document_that_already_crashed_the_service_twice_is_not_requeued(tm):
    s = tm(1)
    mid = create_mission(s)["id"]
    freeze(s, mid)
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode())])
    from talentlab.db import session_scope
    from talentlab.models import Document
    from talentlab.services.matching import requeue_stale
    with session_scope() as db:
        d = db.query(Document).first()
        d.status, d.attempts = "processing", 2
    assert requeue_stale() == 0
    with session_scope() as db:
        d = db.query(Document).first()
        assert d.status == "failed" and d.error_code == "crash"


# ---------------------------------------------------------------- F1 / F2 / F5 : configuration, identité, session
@pytest.mark.parametrize("env", ["production", "PROD", "Prod", " prod "])
def test_every_spelling_of_production_gets_the_production_guards(env):
    from talentlab.config import Settings
    with pytest.raises(ValueError, match="production invalide"):
        Settings(env=env, auth_mode="dev")


@pytest.mark.parametrize("env", ["staging", "preprod", "prd2", ""])
def test_an_unknown_environment_is_refused_instead_of_falling_back_to_dev(env):
    from talentlab.config import Settings
    with pytest.raises(ValueError, match="inconnu"):
        Settings(env=env)


def test_dev_login_is_refused_behind_a_proxy_or_from_a_remote_address(app):
    from fastapi.testclient import TestClient
    with TestClient(app, headers={"X-Requested-With": "talentlab"}) as c:
        assert c.post("/api/auth/dev-login", json={"email": "admin.demo@example.invalid"}).status_code == 200
    with TestClient(app, headers={"X-Requested-With": "talentlab", "X-Forwarded-For": "203.0.113.9"}) as c:
        assert c.post("/api/auth/dev-login", json={"email": "admin.demo@example.invalid"}).status_code == 404
        assert c.get("/api/auth/dev-users").status_code == 404
    with TestClient(app, headers={"X-Requested-With": "talentlab"}, client=("203.0.113.9", 4000)) as c:
        assert c.post("/api/auth/dev-login", json={"email": "admin.demo@example.invalid"}).status_code == 404


def test_duplicated_identity_headers_are_never_resolved_by_picking_the_first(app_env, monkeypatch):
    monkeypatch.setenv("TALENTLAB_AUTH_MODE", "gateway")
    monkeypatch.setenv("TALENTLAB_GATEWAY_SECRET", "s3cr3t-gateway-test")
    from talentlab import config
    config.get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from talentlab.main import create_app
    from talentlab.db import session_scope
    from talentlab.models import User
    app = create_app()
    with TestClient(app, headers={"X-Requested-With": "talentlab"}) as c:
        with session_scope() as db:
            db.add(User(email="boss@example.invalid", display_name="Boss", role="admin"))
            db.add(User(email="mallory@example.invalid", display_name="Mallory", role="talent_manager"))
        ok = c.get("/api/auth/me", headers={"X-Comet-User-Email": "mallory@example.invalid", "X-Gateway-Secret": "s3cr3t-gateway-test"})
        assert ok.status_code == 200 and ok.json()["role"] == "talent_manager"
        dup = c.get("/api/auth/me", headers=[("X-Comet-User-Email", "boss@example.invalid"), ("X-Comet-User-Email", "mallory@example.invalid"),
                                              ("X-Gateway-Secret", "s3cr3t-gateway-test")])
        assert dup.status_code == 401
        dup2 = c.get("/api/auth/me", headers=[("X-Comet-User-Email", "mallory@example.invalid"), ("X-Gateway-Secret", "s3cr3t-gateway-test"), ("X-Gateway-Secret", "s3cr3t-gateway-test")])
        assert dup2.status_code == 401


def test_logout_revokes_the_session_cookie_even_if_it_was_copied(tm, app):
    from fastapi.testclient import TestClient
    s = tm(1)
    cookie = s.c.cookies.get("talentlab_session")
    assert s.get("/api/missions").status_code == 200
    assert s.post("/api/auth/logout").status_code == 200
    with TestClient(app, headers={"X-Requested-With": "talentlab"}, cookies={"talentlab_session": cookie}) as replay:
        assert replay.get("/api/missions").status_code == 401, "un cookie copié avant la déconnexion ne doit plus ouvrir de session"


# ---------------------------------------------------------------- F4 : la suppression efface aussi les extraits des propositions
def _note_with_client_requirement(s, mid, secret):
    c = s.get(f"/api/missions/{mid}/candidates").json()[0]
    s.post(f"/api/missions/{mid}/candidates/{c['id']}/notes", json={
        "kind": "candidate_call_note", "text": f"Le client veut Kafka Connect, {secret}, c'est impératif."})
    return c


def test_deleting_a_candidate_erases_the_transcript_excerpts_kept_in_proposals(tm):
    s = tm(1)
    mid = create_mission(s)["id"]
    freeze(s, mid)
    upload(s, mid, [("a.txt", C.CV_TLJ_B.encode())])
    c = _note_with_client_requirement(s, mid, "QRSECRETANCIENCHEF")
    props = s.get(f"/api/missions/{mid}/proposals").json()
    assert any("QRSECRETANCIENCHEF" in str(p) for p in props), "précondition : l'extrait est dans une proposition"
    assert s.delete(f"/api/missions/{mid}/candidates/{c['id']}").status_code == 204
    props = s.get(f"/api/missions/{mid}/proposals").json()
    assert props and not any("QRSECRETANCIENCHEF" in str(p) for p in props), "l'extrait a disparu avec le candidat (la proposition reste, vidée)"


# ---------------------------------------------------------------- F12 : le niveau « stratégie » ne voit aucune note libre
def test_strategy_level_never_sees_free_text_notes_attached_to_searches(tm):
    owner, colleague = tm(1), tm(2)
    mid = create_mission(owner)["id"]
    freeze(owner, mid)
    sid = owner.post(f"/api/missions/{mid}/searches/generate").json()[0]["id"]
    owner.post(f"/api/missions/{mid}/searches/{sid}/feedback", json={"result_count": 12, "notes": "C-0001 Jean Dupont : TJM 650, refuse les astreintes"})
    owner.post(f"/api/missions/{mid}/searches/{sid}/save", json={"status": "saved", "note": "Jean Dupont est un bon vivier"})
    assert owner.post(f"/api/missions/{mid}/shares", json={"email": colleague.user["email"], "scope": "strategie"}).status_code == 201
    strat = colleague.get(f"/api/missions/{mid}/searches").json()
    assert strat and "Jean Dupont" not in str(strat) and all(s_["note"] == "" for s_ in strat)
    assert all(f["notes"] == "" for s_ in strat for f in s_["feedbacks"])
    assert "Jean Dupont" in str(owner.get(f"/api/missions/{mid}/searches").json()), "le propriétaire, lui, voit ses notes"


# ---------------------------------------------------------------- F13 : un utilisateur « lecture » n'écrit rien
def test_a_read_only_user_cannot_create_proposals_or_qualifications(tm):
    owner, reader = tm(1), tm(2)
    mid = create_mission(owner)["id"]
    freeze(owner, mid)
    owner.post(f"/api/missions/{mid}/searches/generate")
    upload(owner, mid, [("a.txt", C.CV_TLJ_B.encode())])
    assert owner.post(f"/api/missions/{mid}/shares", json={"email": reader.user["email"], "scope": "lecture"}).status_code == 201
    c = owner.get(f"/api/missions/{mid}/candidates").json()[0]
    n_props = len(owner.get(f"/api/missions/{mid}/proposals").json())
    r = reader.post(f"/api/missions/{mid}/assistant", json={"message": "rends la recherche moins stricte"})
    assert r.status_code == 200 and r.json()["proposals"] == []
    assert len(owner.get(f"/api/missions/{mid}/proposals").json()) == n_props, "aucune proposition enregistrée par un lecteur"
    assert reader.post(f"/api/missions/{mid}/candidates/{c['id']}/questions").status_code == 403


# ---------------------------------------------------------------- F14 : bornes et validations
@pytest.mark.parametrize("path,payload", [
    ("notes", {"kind": "candidate_call_note", "text": "x" * 40, "speaker_map": {f"s{i}": "candidate" for i in range(500)}}),
    ("notes", {"kind": "candidate_call_note", "text": "x" * 40, "speaker_map": {"a": "b" * 5000}}),
])
def test_oversized_collections_and_values_are_rejected(tm, path, payload):
    s = tm(1)
    mid = create_mission(s)["id"]
    freeze(s, mid)
    upload(s, mid, [("a.txt", C.CV_TLJ_B.encode())])
    c = s.get(f"/api/missions/{mid}/candidates").json()[0]
    assert s.post(f"/api/missions/{mid}/candidates/{c['id']}/{path}", json=payload).status_code == 422


def test_invalid_enumerations_are_a_422_never_a_500(tm):
    s = tm(1)
    mid = create_mission(s)["id"]
    assert s.post(f"/api/missions/{mid}/sources", json={"kind": "n_importe_quoi", "text": "x" * 30, "author": "A"}).status_code == 422
    rid = next(x["id"] for x in s.get(f"/api/missions/{mid}").json()["requirements"] if x["status"] == "active")
    assert s.patch(f"/api/missions/{mid}/requirements/{rid}", json={"source_kind": "n_importe_quoi"}).status_code == 422
    assert s.post(f"/api/missions/{mid}/requirements", json={"label": "Kafka", "terms": ["k" * 200_000]}).status_code == 422
    assert s.post("/api/library", json={"kind": "note_metier", "title": "Titre valide", "body": "Un contenu suffisamment long pour passer.", "tags": ["t" * 100_000]}).status_code == 422


# ---------------------------------------------------------------- comptes : désactivation sans passer par la base
def test_an_admin_can_deactivate_a_user_which_revokes_their_sessions_but_never_the_last_admin(tm, app):
    s = tm(1)
    admin = Session(app, "admin.demo@example.invalid")
    users = {u["email"]: u["id"] for u in admin.get("/api/users").json()}
    assert s.get("/api/missions").status_code == 200
    assert s.patch(f"/api/admin/users/{users['tm1.demo@example.invalid']}", json={"active": False}).status_code == 403, "réservé aux administrateurs"
    assert admin.patch(f"/api/admin/users/{users['tm1.demo@example.invalid']}", json={"active": False}).status_code == 200
    assert s.get("/api/missions").status_code == 401, "la session ouverte est révoquée"
    r = admin.patch(f"/api/admin/users/{users['admin.demo@example.invalid']}", json={"active": False})
    assert r.status_code == 409 and "dernier administrateur" in r.text
    admin.close()
