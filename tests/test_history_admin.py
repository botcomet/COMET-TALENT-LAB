"""V1.1-D — historique (recherche transverse) et écran d'administration : droits, niveaux de partage, bornes."""
from __future__ import annotations

from tests.conftest import Session, create_mission, freeze, upload
from tests.fixtures import briefs as B, cvs as C


def _hits(r):
    return {(h["kind"], h["text"]) for res in r["results"] for h in res["hits"]}


# =============================================================================== historique
def test_history_finds_missions_requirements_and_saved_queries_the_user_can_access(tm):
    s = tm(1)
    m = create_mission(s, client="Client Alpha (fictif)")
    freeze(s, m["id"])
    sr = s.post(f"/api/missions/{m['id']}/searches/generate").json()
    s.post(f"/api/missions/{m['id']}/searches/{sr[0]['id']}/save", json={"status": "saved", "note": "Bonne piste sur les profils Confluent"})
    r = s.get("/api/history/search", params={"q": "kafka"}).json()
    assert [x["mission"]["id"] for x in r["results"]] == [m["id"]]
    kinds = {h["kind"] for h in r["results"][0]["hits"]}
    assert {"exigence", "recherche"} <= kinds
    assert r["results"][0]["mission"]["access"] == "owner"
    # intitulé et client, sans accent ni casse
    assert _hits(s.get("/api/history/search", params={"q": "TECH lead"}).json())
    assert any(h["label"] == "Client" for res in s.get("/api/history/search", params={"q": "alpha"}).json()["results"] for h in res["hits"])
    # tous les mots sont requis (ET) : un mot absent ne ramène rien
    assert s.get("/api/history/search", params={"q": "kafka cobol"}).json()["results"] == []
    assert s.get("/api/history/search", params={"q": "piste confluent"}).json()["results"], "la note de la requête est cherchée pour le propriétaire"


def test_history_never_reaches_missions_that_are_not_shared_with_the_user(tm):
    owner, other = tm(1), tm(2)
    m = create_mission(owner, client="Client Secret (fictif)")
    assert other.get("/api/history/search", params={"q": "kafka"}).json()["results"] == []
    assert other.get("/api/history/search", params={"q": "secret"}).json()["results"] == []
    owner.post(f"/api/missions/{m['id']}/shares", json={"email": other.user["email"], "scope": "lecture"})
    assert [x["mission"]["id"] for x in other.get("/api/history/search", params={"q": "kafka"}).json()["results"]] == [m["id"]]
    other_access = other.get("/api/history/search", params={"q": "kafka"}).json()["results"][0]["mission"]["access"]
    assert other_access == "lecture"
    # retrait du partage : la mission disparaît immédiatement des résultats
    assert owner.delete(f"/api/missions/{m['id']}/shares/{other.user['id']}").status_code == 204
    assert other.get("/api/history/search", params={"q": "kafka"}).json()["results"] == []


def test_history_at_strategy_level_shows_no_notes_no_context_and_never_any_candidate_data(tm):
    owner, other = tm(1), tm(2)
    m = create_mission(owner)
    freeze(owner, m["id"])
    upload(owner, m["id"], [("a.txt", C.CV_TLJ_A.encode())])
    sr = owner.post(f"/api/missions/{m['id']}/searches/generate").json()
    owner.post(f"/api/missions/{m['id']}/searches/{sr[0]['id']}/save", json={"status": "saved", "note": "Le candidat NOTEPRIVEE a répondu"})
    cand = owner.get(f"/api/missions/{m['id']}/candidates").json()[0]
    owner.post(f"/api/missions/{m['id']}/shares", json={"email": other.user["email"], "scope": "strategie"})
    # note libre : jamais cherchée au niveau stratégie
    assert other.get("/api/history/search", params={"q": "NOTEPRIVEE"}).json()["results"] == []
    # le propriétaire, lui, la retrouve
    assert owner.get("/api/history/search", params={"q": "NOTEPRIVEE"}).json()["results"]
    # contenu d'un CV et références de candidats : jamais cherchés, pour personne
    for who in (owner, other):
        assert who.get("/api/history/search", params={"q": "Kafka Connect Avro"}).json() is not None
        res = who.get("/api/history/search", params={"q": cand["ref"]}).json()
        assert res["results"] == []
        blob = str(who.get("/api/history/search", params={"q": "kafka"}).json())
        assert cand["ref"] not in blob and "a.txt" not in blob
    # au niveau stratégie, les exigences contextuelles ne sont pas exposées (comme dans la vue mission)
    view = other.get(f"/api/missions/{m['id']}").json()
    contextual = [r for r in owner.get(f"/api/missions/{m['id']}").json()["requirements"] if r["category"] == "contextuel" and r["status"] == "active"]
    seen_by_other = {r["label"] for r in view["requirements"]}
    for r in contextual:
        if r["label"] not in seen_by_other:
            hits = other.get("/api/history/search", params={"q": r["label"]}).json()["results"]
            assert not [h for res in hits for h in res["hits"] if h["kind"] == "exigence" and h["text"] == r["label"]]


def test_history_search_is_bounded_validated_and_not_written_to_the_audit_log(tm, app):
    s = tm(1)
    create_mission(s)
    assert s.get("/api/history/search", params={"q": "a"}).status_code == 422, "trop court"
    assert s.get("/api/history/search", params={"q": "x" * 101}).status_code == 422, "trop long"
    assert s.get("/api/history/search", params={"q": "   "}).status_code == 422, "uniquement des espaces"
    assert s.get("/api/history/search").status_code == 422
    # beaucoup de missions identiques : résultats plafonnés, signalés
    for i in range(27):
        create_mission(s, title=f"Mission fictive numéro {i}", client="Client Bulk (fictif)")
    r = s.get("/api/history/search", params={"q": "bulk"}).json()
    assert len(r["results"]) == 25 and r["truncated"] is True
    # le texte cherché n'entre pas dans le journal
    s.get("/api/history/search", params={"q": "TEXTECHERCHEUNIQUE"})
    admin = Session(app, "admin.demo@example.invalid")
    from talentlab.db import session_scope
    from talentlab.models import AuditEvent
    with session_scope() as db:
        assert not any("TEXTECHERCHEUNIQUE" in str(ev.detail) for ev in db.query(AuditEvent).all())
        assert not any(ev.action.startswith("history") for ev in db.query(AuditEvent).all())
    admin.close()


def test_history_requires_authentication(app):
    from fastapi.testclient import TestClient
    from tests.conftest import HDR
    with TestClient(app, headers=HDR) as c:
        assert c.get("/api/history/search", params={"q": "kafka"}).status_code == 401


# =============================================================================== administration
def test_admin_screen_endpoints_are_admin_only_and_expose_no_secret(tm, app):
    s = tm(1)
    admin = Session(app, "admin.demo@example.invalid")
    for path in ("/api/admin/users", "/api/admin/status"):
        assert s.get(path).status_code == 403, path
        assert admin.get(path).status_code == 200, path
    users = admin.get("/api/admin/users").json()
    assert {"id", "email", "display_name", "role", "active", "missions_owned"} <= set(users[0])
    st = admin.get("/api/admin/status").json()
    blob = str(st).lower()
    for forbidden in ("secret", "password", "encryption_key", "database_url", "sqlite:", "postgresql"):
        assert forbidden not in blob, forbidden
    assert st["retention_auto_purge"] is False and st["maintenance_enabled"] is False, "tout est désactivé par défaut"
    assert st["purge_pending"] == {"documents": 0, "candidates": 0}
    assert st["counts"]["users_active"] >= 2
    admin.close()


def test_admin_status_previews_exactly_what_a_purge_would_delete(tm, app):
    s = tm(1)
    m = create_mission(s)
    freeze(s, m["id"])
    upload(s, m["id"], [("a.txt", C.CV_TLJ_A.encode()), ("b.txt", C.CV_TLJ_B.encode())])
    admin = Session(app, "admin.demo@example.invalid")
    assert admin.get("/api/admin/status").json()["purge_pending"] == {"documents": 0, "candidates": 0}
    from datetime import date, timedelta
    from talentlab.db import session_scope
    from talentlab.models import Document
    with session_scope() as db:
        d = db.query(Document).order_by(Document.created_at).first()
        d.expires_at = date.today() - timedelta(days=1)
    assert admin.get("/api/admin/status").json()["purge_pending"] == {"documents": 1, "candidates": 1}
    done = admin.post("/api/admin/purge").json()
    assert done == {"documents": 1, "candidates": 1}, "la prévisualisation annonce exactement ce que la purge supprime"
    assert admin.get("/api/admin/status").json()["purge_pending"] == {"documents": 0, "candidates": 0}
    admin.close()


def test_admin_creates_updates_and_lists_accounts_with_audit_and_last_admin_protection(tm, app):
    tm(1)
    admin = Session(app, "admin.demo@example.invalid")
    r = admin.post("/api/admin/users", json={"email": "Nouvelle.Recrue@example.invalid", "display_name": "Nouvelle Recrue", "role": "talent_manager"})
    assert r.status_code == 201 and r.json()["email"] == "nouvelle.recrue@example.invalid" and r.json()["active"] is True
    assert admin.post("/api/admin/users", json={"email": "nouvelle.recrue@example.invalid", "display_name": "Doublon"}).status_code == 409
    uid = r.json()["id"]
    assert admin.patch(f"/api/admin/users/{uid}", json={"role": "pilote"}).json()["role"] == "pilote"
    assert admin.patch(f"/api/admin/users/{uid}", json={"active": False}).json()["active"] is False
    listing = admin.get("/api/admin/users").json()
    assert [u["active"] for u in listing] == sorted((u["active"] for u in listing), reverse=True), "comptes actifs d'abord"
    assert next(u for u in listing if u["id"] == uid)["active"] is False
    me = next(u for u in listing if u["email"] == "admin.demo@example.invalid")
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"role": "talent_manager"}).status_code == 409
    from talentlab.db import session_scope
    from talentlab.models import AuditEvent
    with session_scope() as db:
        actions = [ev.action for ev in db.query(AuditEvent).order_by(AuditEvent.seq)]
    assert actions.count("user.create") >= 1 and actions.count("user.update") >= 2
    admin.close()


def test_admin_account_creation_refuses_an_implausible_address(app, tm):
    tm(1)
    admin = Session(app, "admin.demo@example.invalid")
    for bad in ("sans-arobase", "a@b", "deux@@example.invalid", "espace @example.invalid", ""):
        assert admin.post("/api/admin/users", json={"email": bad, "display_name": "Test Compte"}).status_code == 422, bad
    admin.close()


def test_an_admin_does_not_see_other_peoples_missions_in_the_history(tm, app):
    """Le rôle d'administrateur gère les comptes ; il ne donne aucun accès aux missions d'un collègue (comme dans la liste des missions)."""
    owner = tm(1)
    create_mission(owner, client="Client Prive (fictif)")
    admin = Session(app, "admin.demo@example.invalid")
    assert admin.get("/api/history/search", params={"q": "kafka"}).json()["results"] == []
    assert admin.get("/api/history/search", params={"q": "prive"}).json()["results"] == []
    admin.close()
