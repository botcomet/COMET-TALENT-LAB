"""Parcours complet par l'API (critères d'acceptation 1 à 22) et garde-fous d'accès."""
import json
import re
import sqlite3

import pytest

from tests.conftest import HDR, Session, create_mission, freeze, pdf, sqlite_only, upload
from tests.fixtures import briefs as B, cvs as C
from tests.helpers import make_docx, make_encrypted_pdf, make_image_only_pdf


# =============================================================================== authentification & garde-fous
def test_api_requires_authentication_and_csrf_header(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as anon:
        assert anon.get("/api/missions").status_code == 401
        assert anon.post("/api/missions", json={}).status_code in (401, 403)
        assert anon.get("/api/health").status_code == 200
        r = anon.post("/api/auth/dev-login", json={"email": "tm1.demo@example.invalid"})
        assert r.status_code == 403, "sans en-tête de protection CSRF, une écriture est refusée"
        r = anon.post("/api/auth/dev-login", json={"email": "tm1.demo@example.invalid"}, headers=HDR)
        assert r.status_code == 200
        assert anon.get("/api/missions").status_code == 200


def test_security_headers_present(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        h = c.get("/api/health").headers
        assert "default-src 'self'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"          # l'API ne se met jamais en cache
        csp = h["content-security-policy"]
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_session_cookie_flags_forged_cookie_and_secure_flag_in_production(app):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from starlette.responses import Response
    from talentlab.config import Settings
    from talentlab.models import User
    from talentlab.security import issue_session
    with TestClient(app, headers=HDR) as c:
        r = c.post("/api/auth/dev-login", json={"email": "tm1.demo@example.invalid"})
        sc = r.headers["set-cookie"].lower()
        assert "httponly" in sc and "samesite=lax" in sc and "path=/" in sc
        c.cookies.clear()
        c.cookies.set("talentlab_session", "cookie-forge-sans-signature")
        assert c.get("/api/missions").status_code == 401, "un cookie non signé par le serveur n'ouvre aucune session"
    prod = Settings(env="prod", auth_mode="gateway", gateway_secret="g", session_secret="s", encryption_key=Fernet.generate_key().decode(), database_url="postgresql://x/y")
    resp = Response()
    issue_session(resp, User(id="u1", email="x@example.invalid", display_name="X"), prod)
    assert "secure" in resp.headers["set-cookie"].lower()


def test_gateway_mode_requires_shared_secret_and_provisioned_user(app_env, monkeypatch):
    monkeypatch.setenv("TALENTLAB_AUTH_MODE", "gateway")
    monkeypatch.setenv("TALENTLAB_GATEWAY_SECRET", "s3cr3t-gateway-test")
    from talentlab import config
    config.get_settings.cache_clear()
    from talentlab.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app()) as c:
        # le compte de démonstration n'est pas créé en mode passerelle : on provisionne un utilisateur
        from talentlab.db import session_scope
        from talentlab.models import User
        with session_scope() as db:
            db.add(User(email="vrai.utilisateur@example.invalid", display_name="Utilisateur Test", role="talent_manager"))
        h = {"X-Comet-User-Email": "vrai.utilisateur@example.invalid"}
        assert c.get("/api/auth/me", headers=h).status_code == 401, "en-tête d'identité seul : refusé"
        assert c.get("/api/auth/me", headers={**h, "X-Gateway-Secret": "mauvais"}).status_code == 401
        assert c.get("/api/auth/me", headers={"X-Comet-User-Email": "inconnu@example.invalid", "X-Gateway-Secret": "s3cr3t-gateway-test"}).status_code == 401
        assert c.get("/api/auth/me", headers={**h, "X-Gateway-Secret": "s3cr3t-gateway-test"}).status_code == 200
        assert c.post("/api/auth/dev-login", json={"email": "x@example.invalid"}, headers=HDR).status_code == 404, "pas de connexion de développement en mode passerelle"


def test_production_config_refuses_insecure_settings(monkeypatch):
    from pydantic import ValidationError
    from talentlab.config import Settings
    with pytest.raises(ValidationError):
        Settings(env="prod", auth_mode="dev")
    with pytest.raises(ValidationError):
        Settings(env="prod", auth_mode="gateway", gateway_secret="a", session_secret="b", encryption_key="c", database_url="sqlite:///x.db")
    Settings(env="prod", auth_mode="gateway", gateway_secret="a", session_secret="b", encryption_key="c", database_url="postgresql://u:p@h/db")


def test_unreachable_mission_is_404_not_403(tm):
    a, b = tm(1), tm(2)
    m = create_mission(a)
    assert b.get(f"/api/missions/{m['id']}").status_code == 404
    assert b.get("/api/missions").json() == []


def test_unknown_fields_are_rejected(tm):
    a = tm(1)
    r = a.post("/api/missions", json={"title": "X", "brief": "y" * 60, "owner_id": "hack"})
    assert r.status_code == 422


# =============================================================================== critères 1 → 11 : mission, besoin, grille, booléens
def test_acceptance_mission_requirements_grid_and_searches(tm):
    s = tm(1)
    m = create_mission(s)
    mid = m["id"]
    # 1-2 mission + descriptif ; 4 critères extraits et corrigeables
    assert m["analysis"]["modalities"]["onsite_days_per_week"] == 3
    assert any(w for w in m["analysis"]["warnings"]), "alerte titre vs travail réel"
    assert all(not r["validated"] for r in m["requirements"])
    # 3 précision issue d'un appel client : source tracée, remplacement jamais implicite
    r = s.post(f"/api/missions/{mid}/sources", json={"kind": "client_precision_validee", "text": "Le client précise que Kafka Connect est impératif et que Kafka doit être maîtrisé en profondeur.",
                                                      "author": "DSI client (fictif)", "source_date": "2026-09-30"})
    assert r.status_code == 201
    assert s.post(f"/api/missions/{mid}/sources", json={"kind": "client_precision_validee", "text": "Kafka Connect impératif", "author": ""}).status_code == 422, "provenance obligatoire"
    m2 = s.get(f"/api/missions/{mid}").json()
    kc = [x for x in m2["requirements"] if x["key"] == "skill:kafka_connect"]
    assert {x["source_kind"] for x in kc} >= {"client_precision_validee"}
    assert m2["conflicts"], "la divergence entre sources est exposée, jamais résolue en silence"
    # éliminatoire : impossible sans confirmation client tracée
    rid = next(x["id"] for x in m2["requirements"] if x["key"] == "skill:avro")
    bad = s.patch(f"/api/missions/{mid}/requirements/{rid}", json={"category": "eliminatoire_confirme"})
    assert bad.status_code == 422 and "confirmé par le client" in bad.text
    # 5 grille : brouillon → poids → validation
    assert s.post(f"/api/missions/{mid}/requirements/validate", json={}).status_code == 200
    g = s.post(f"/api/missions/{mid}/grid/propose").json()
    assert g["status"] == "draft" and g["total_weight"] == 100
    bad_w = {c["key"]: c["weight"] for c in g["criteria"] if c["scored"]}
    first = next(iter(bad_w))
    s.put(f"/api/missions/{mid}/grid/{g['id']}", json={"weights": {first: bad_w[first] + 5}})
    chk = s.get(f"/api/missions/{mid}/grid/{g['id']}/check").json()
    assert any("100" in e for e in chk["errors"])
    assert s.post(f"/api/missions/{mid}/grid/{g['id']}/validate", json={"allow_unresolved_clarifications": True}).status_code == 422
    s.put(f"/api/missions/{mid}/grid/{g['id']}", json={"weights": {first: bad_w[first]}})
    frozen = s.post(f"/api/missions/{mid}/grid/{g['id']}/validate", json={"allow_unresolved_clarifications": True}).json()
    assert frozen["status"] == "frozen" and frozen["content_hash"]
    assert s.put(f"/api/missions/{mid}/grid/{g['id']}", json={"weights": {first: 1}}).status_code == 409, "une grille figée ne se modifie plus"
    # 6-8 trois booléens + explications + copie
    ss = s.post(f"/api/missions/{mid}/searches/generate").json()
    assert {x["strategy"] for x in ss} == {"exploratory", "balanced", "strict"}
    for x in ss:
        assert len(x["query"]) <= 250 and x["explanation"]["false_positive_risks"] and x["explanation"]["excluded_skills"] is not None
    # 9-11 résultat Turnover → nouvelle recherche adaptée, sauvegarde
    strict = next(x for x in ss if x["strategy"] == "strict")
    fb = s.post(f"/api/missions/{mid}/searches/{strict['id']}/feedback", json={"result_count": 0, "notes": "aucun résultat"}).json()
    assert fb["diagnosis"]["problem"] == "zero"
    if fb["new_search"]:
        assert fb["modification"] and fb["new_search"]["parent_id"] == strict["id"] and fb["new_search"]["query"] != strict["query"]
        saved = s.post(f"/api/missions/{mid}/searches/{fb['new_search']['id']}/save", json={"status": "useful", "note": "vivier exploitable"}).json()
        assert saved["status"] == "useful"
    reqs_after = s.get(f"/api/missions/{mid}").json()["requirements"]
    assert all(r["category"] == "imperatif" for r in reqs_after if r["key"] in ("skill:java", "skill:kafka", "skill:spring") and r["status"] == "active"), \
        "l'optimisation de recherche ne modifie jamais les exigences client"
    assert s.post("/api/tools/boolean/validate", json={"query": "(A OR B AND C"}).json()["valid"] is False


# =============================================================================== critères 12 → 20 : CV, matching, preuves, enrichissement
def _setup_tlj(s):
    m = create_mission(s)
    freeze(s, m["id"], mutate={"skill:kafka": {"depth_required": "advanced"}})
    return m["id"]


def test_acceptance_batch_cv_progress_comparison_evidence_notes_and_history(tm):
    s = tm(1)
    mid = _setup_tlj(s)
    docs = [("a.pdf", pdf(C.CV_TLJ_A)), ("b.docx", make_docx(C.CV_TLJ_B)), ("c.txt", C.CV_TLJ_C_DECLARED.encode()),
            ("corrompu.pdf", b"%PDF-1.4 casse"), ("scanne.pdf", make_image_only_pdf()), ("chiffre.pdf", make_encrypted_pdf(C.CV_TLJ_B)),
            ("doublon.pdf", pdf(C.CV_TLJ_A)), ("vide.txt", b"")]
    r = upload(s, mid, docs)
    assert r.status_code == 202
    by = {d["filename"]: d for d in s.get(f"/api/missions/{mid}/documents").json()}
    assert by["a.pdf"]["status"] == "done" and by["b.docx"]["status"] == "done" and by["c.txt"]["status"] == "done"
    for bad, code in (("corrompu.pdf", "corrupt"), ("scanne.pdf", "no_text_layer"), ("chiffre.pdf", "encrypted"), ("vide.txt", "empty")):
        assert by[bad]["status"] == "failed" and by[bad]["error_code"] == code and by[bad]["error_message"], bad
        assert by[bad]["candidate_id"] is None, "un document en échec ne produit ni candidat ni score"
    assert by["doublon.pdf"]["status"] == "duplicate" and by["doublon.pdf"]["duplicate_of"], "doublon détecté sans bloquer les autres"
    cands = s.get(f"/api/missions/{mid}/candidates").json()
    assert len(cands) == 3 and all(c["assessment"] for c in cands)
    assert len({c["assessment"]["grid_version"] for c in cands}) == 1
    # 14 comparaison à grille identique ; 15 preuves et manques
    refs = {c["label"]: c for c in cands}
    a, b, c_ = refs["a"], refs["b"], refs["c"]
    cmp_ = s.post(f"/api/missions/{mid}/compare", json={"candidate_ids": [a["id"], b["id"], c_["id"]]}).json()
    assert cmp_["ranking"][0] == b["ref"] and cmp_["why"]["leader"] == b["ref"]
    detail = s.get(f"/api/missions/{mid}/candidates/{a['id']}").json()
    kafka = next(x for x in detail["assessment_full"]["result"]["criteria"] if x["key"] == "skill:kafka")
    assert kafka["level"] == "partiellement_demontre" and kafka["evidence"] and kafka["advanced_missing"]
    # 16 questions de qualification
    q = s.post(f"/api/missions/{mid}/candidates/{a['id']}/questions").json()
    assert len(q["priority"]) == 3 and all(x["proof_elements"] for x in q["priority"])
    # 17-19 notes d'appel → actualisation, diff et raisons
    before = detail["assessment_full"]["result"]["score_documented"]
    note = ("Il a mis en place des connecteurs Kafka Connect vers PostgreSQL avec transformations, il a conçu les schémas Avro avec Schema Registry "
            "et il exploite le cluster Kafka en production.")
    nr = s.post(f"/api/missions/{mid}/candidates/{a['id']}/notes", json={"kind": "candidate_call_note", "text": note, "note_date": "2026-10-05"})
    assert nr.status_code == 201
    n = nr.json()
    assert n["facts"] and n["assessment"]["result"]["score_documented"] > before
    assert n["assessment"]["diff"]["changes"] and n["assessment"]["diff"]["score_delta"] > 0
    d2 = s.get(f"/api/missions/{mid}/candidates/{a['id']}").json()
    assert len(d2["history"]) == 2 and d2["history"][0]["version"] == 1, "toutes les versions d'analyse sont conservées"
    first = s.get(f"/api/missions/{mid}/candidates/{a['id']}").json()["history"][0]
    assert first["score_documented"] == before, "l'analyse initiale n'est pas modifiée rétroactivement"
    # 20 historique de la mission
    h = s.get(f"/api/missions/{mid}/history").json()
    actions = {e["action"] for e in h["events"]}
    assert {"mission.create", "grid.freeze", "cv.import", "assessment.create", "note.add"} <= actions
    assert h["grids"] and h["searches"] == []


def test_batch_limit_is_enforced_and_configurable(tm, monkeypatch):
    s = tm(1)
    mid = _setup_tlj(s)
    many = [(f"cv{i}.txt", C.CV_TLJ_B.replace("Dominique", f"Prénom{i}").encode()) for i in range(21)]
    r = upload(s, mid, many)
    assert r.status_code == 422 and "20" in r.text


def test_twenty_cvs_in_one_batch_are_analysed_independently(tm):
    s = tm(1)
    mid = _setup_tlj(s)
    docs = []
    for i in range(20):
        base = [C.CV_TLJ_A, C.CV_TLJ_B, C.CV_TLJ_C_DECLARED, C.CV_KAFKA_OLD][i % 4]
        docs.append((f"cv{i}.txt", (base + f"\n\nRéférence interne fictive {i}\n").encode()))
    r = upload(s, mid, docs)
    assert r.status_code == 202
    st = s.get(f"/api/missions/{mid}/documents").json()
    assert sum(d["status"] == "done" for d in st) == 20
    cands = s.get(f"/api/missions/{mid}/candidates").json()
    assert len(cands) == 20 and len({c["assessment"]["grid_version"] for c in cands}) == 1


def test_work_view_hides_but_never_deletes_low_scores(tm):
    s = tm(1)
    mid = _setup_tlj(s)
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode()), ("b.txt", C.CV_TLJ_B.encode())])
    allc = s.get(f"/api/missions/{mid}/candidates?view=all").json()
    work = s.get(f"/api/missions/{mid}/candidates?view=work").json()
    assert len(allc) == 2 and len(work) < len(allc)
    assert any(c["hidden_in_work_view"] for c in allc), "masqué dans la vue exigeante mais consultable"
    hidden = next(c for c in allc if c["hidden_in_work_view"])
    assert s.get(f"/api/missions/{mid}/candidates/{hidden['id']}").status_code == 200


def test_cv_before_grid_is_extracted_but_not_scored_until_frozen(tm):
    s = tm(1)
    m = create_mission(s)
    upload(s, m["id"], [("a.txt", C.CV_TLJ_A.encode())])
    c = s.get(f"/api/missions/{m['id']}/candidates").json()[0]
    assert c["assessment"] is None and "grille figée" in c["document"]["stage"]
    freeze(s, m["id"])
    r = s.post(f"/api/missions/{m['id']}/reassess-all", json={"reason": "grille validée"}).json()
    assert r["reassessed"] == 1
    assert s.get(f"/api/missions/{m['id']}/candidates").json()[0]["assessment"]["score"] is not None


# =============================================================================== test 7 via l'API : nouvelle version de grille
@pytest.mark.business
def test_business_7_api_client_precision_creates_new_grid_version_and_reassessment(tm):
    s = tm(1)
    m = create_mission(s, B.DSF_TITLE, B.DSF)
    mid = m["id"]
    freeze(s, mid)
    upload(s, mid, [("a.txt", C.CV_DS_A.encode()), ("b.txt", C.CV_DS_B.encode())])
    v1 = {c["label"]: c["assessment"] for c in s.get(f"/api/missions/{mid}/candidates").json()}
    s.post(f"/api/missions/{mid}/sources", json={"kind": "client_precision_validee", "author": "Responsable front (fictif)", "source_date": "2026-10-02",
                                                  "text": "Le client exige une réelle expérience de conception d'un Design System déployé à grande échelle, avec gouvernance et adoption multi-équipes."})
    r = s.post(f"/api/missions/{mid}/grid/new-version", json={"reason": "Retour client : Design System conçu et déployé à grande échelle exigé"})
    assert r.status_code == 201, r.text
    nv = r.json()
    assert nv["grid"]["version"] == 2 and nv["grid"]["status"] == "draft" and nv["candidates_to_reassess"] == 2
    assert s.post(f"/api/missions/{mid}/reassess-all", json={"reason": "x"}).status_code == 200, "réévaluation possible tant que la v2 n'est pas figée : v1 reste la référence"
    cur = {c["label"]: c["assessment"]["grid_version"] for c in s.get(f"/api/missions/{mid}/candidates").json()}
    assert set(cur.values()) == {1}
    s.post(f"/api/missions/{mid}/requirements/validate", json={})
    done = s.post(f"/api/missions/{mid}/grid/{nv['grid']['id']}/validate", json={"allow_unresolved_clarifications": True})
    assert done.status_code == 200, done.text
    s.post(f"/api/missions/{mid}/reassess-all", json={"reason": "nouvelle grille v2"})
    v2 = {c["label"]: c for c in s.get(f"/api/missions/{mid}/candidates").json()}
    assert {c["assessment"]["grid_version"] for c in v2.values()} == {2}
    assert v2["b"]["assessment"]["score_documented"] > v2["a"]["assessment"]["score_documented"]
    d = s.get(f"/api/missions/{mid}/candidates/{v2['a']['id']}").json()
    assert len(d["history"]) >= 2 and d["assessment_full"]["diff"] and d["assessment_full"]["result"]["grid_version"] == 2, "avant/après conservés et expliqués"
    assert [h["grid_id"] for h in d["history"]][0] != d["assessment_full"]["grid_id"]


# =============================================================================== test 15 : collaboration
@pytest.mark.business
def test_business_15_sharing_a_search_strategy_never_exposes_candidate_data(tm):
    owner, colleague = tm(1), tm(2)
    mid = _setup_tlj(owner)
    upload(owner, mid, [("a.txt", C.CV_TLJ_A.encode())])
    owner.post(f"/api/missions/{mid}/searches/generate")
    owner.post(f"/api/missions/{mid}/candidates/{owner.get(f'/api/missions/{mid}/candidates').json()[0]['id']}/notes",
               json={"kind": "candidate_call_note", "text": "Il a travaillé sur Kafka et il est disponible à partir du 3 novembre, TJM 650 euros."})
    sh = owner.post(f"/api/missions/{mid}/shares", json={"email": colleague.user["email"], "scope": "strategie"})
    assert sh.status_code == 201
    # accès à la recherche et à ses explications
    ss = colleague.get(f"/api/missions/{mid}/searches")
    assert ss.status_code == 200 and ss.json() and ss.json()[0]["explanation"]
    mv = colleague.get(f"/api/missions/{mid}").json()
    assert mv["access"] == "strategie" and "sources" not in mv and "analysis" not in mv
    # aucune donnée candidat, aucune écriture
    for path in ("candidates", "documents", "history", "proposals", "candidate-description"):
        assert colleague.get(f"/api/missions/{mid}/{path}").status_code == 403, path
    assert colleague.post(f"/api/missions/{mid}/searches/generate").status_code == 403
    assert colleague.post(f"/api/missions/{mid}/cvs", files=[("files", ("x.txt", b"x" * 400, "text/plain"))]).status_code == 403
    blob = json.dumps(ss.json() + [mv])
    assert "Kafka" in blob and not re.search(r"C-\d{4}|Banque Exemple|\b650\b|novembre", blob)
    # lecture : voit les évaluations mais ne modifie rien ; la révocation est immédiate
    owner.post(f"/api/missions/{mid}/shares", json={"email": colleague.user["email"], "scope": "lecture"})
    assert colleague.get(f"/api/missions/{mid}/candidates").status_code == 200
    assert colleague.post(f"/api/missions/{mid}/requirements/validate", json={}).status_code == 403
    assert colleague.delete(f"/api/missions/{mid}").status_code == 403
    assert colleague.post(f"/api/missions/{mid}/shares", json={"email": "tm3.demo@example.invalid", "scope": "lecture"}).status_code == 403, "seul le propriétaire partage"
    owner.delete(f"/api/missions/{mid}/shares/{colleague.user['id']}")
    assert colleague.get(f"/api/missions/{mid}").status_code == 404


# =============================================================================== export DT Editor (critère 22)
def test_acceptance_export_for_dt_editor_is_anonymised_faithful_and_sourced(tm):
    s = tm(1)
    mid = _setup_tlj(s)
    cv = C.CV_TLJ_B + "\nContact : camille.fictif@example.invalid — 06 12 34 56 78 — https://linkedin.com/in/fictif\n"
    upload(s, mid, [("b.txt", cv.encode())])
    c = s.get(f"/api/missions/{mid}/candidates").json()[0]
    assert s.get(f"/api/missions/{mid}/candidates/{c['id']}/export/dt").status_code == 409, "acronyme autorisé obligatoire avant export"
    s.patch(f"/api/missions/{mid}/candidates/{c['id']}", json={"acronym": "dfi"})
    ex = s.get(f"/api/missions/{mid}/candidates/{c['id']}/export/dt").json()
    assert ex["schema"] == "comet.talentlab.dt-export/v1" and ex["candidate"]["acronym"] == "DFI"
    blob = json.dumps(ex, ensure_ascii=False)
    assert "camille.fictif@example.invalid" not in blob and "06 12 34 56 78" not in blob and "linkedin" not in blob.lower() or "[lien retiré]" in blob
    assert "Dominique" not in ex["markdown"].split("##")[0], "ni nom ni prénom dans l'en-tête"
    cv_lines = {ln.strip(" -•") for ln in C.CV_TLJ_B.split("\n")}
    exps = ex["dossier_material"]["experiences"]
    assert exps and exps[0]["company"] == "Marchand Exemple" and exps[0]["start"] == "2019-02" and exps[0]["is_current"]
    for e in exps:
        for r in e["realisations"]:
            assert r["statement"] in cv_lines or any(r["statement"] in ln for ln in cv_lines), "chaque réalisation est une phrase du CV, non reformulée"
        assert e["environnement_technique"], "environnement technique repris de la ligne du CV"
    techs = {t for e in exps for t in e["environnement_technique"]}
    assert "Kafka 3.4" in techs and "Oracle" not in techs, "aucune technologie ajoutée à une expérience"
    assert ex["internal_only"]["assessment"]["grid_hash"] and "to_verify" in ex["internal_only"]
    assert "provenance" in ex["internal_only"]["provenance_note"].lower()


def test_candidate_description_never_invents_missing_conditions(tm):
    s = tm(1)
    m = create_mission(s, "Consultant Python", "Compétences requises\n- Python\n- SQL\nContexte\nProjet de refonte d'un outil interne de gestion.\n")
    d = s.get(f"/api/missions/{m['id']}/candidate-description").json()
    f = d["fields"]
    assert f["tjm"] == "Non communiqué" and f["duree"] == "Non communiqué" and f["astreintes"] == "Non communiqué"
    assert f["client"] == "Client confidentiel" and set(["tjm", "duree", "demarrage"]) <= set(d["missing"])
    assert "Python" in d["text"] and "@" not in d["text"]


# =============================================================================== journal d'audit, chiffrement, conservation
def test_audit_log_is_hash_chained_and_tamper_evident(tm, app):
    s = tm(1)
    _setup_tlj(s)
    admin = Session(app, "admin.demo@example.invalid")
    assert admin.get("/api/admin/audit/verify").json()["ok"] is True
    assert s.get("/api/admin/audit/verify").status_code == 403
    from talentlab.db import session_scope
    from talentlab.models import AuditEvent
    with session_scope() as db:
        ev = db.query(AuditEvent).order_by(AuditEvent.seq).offset(2).first()
        ev.action = "falsifie"
    res = admin.get("/api/admin/audit/verify").json()
    assert res["ok"] is False and res["broken_at_seq"] >= 1
    admin.close()


@sqlite_only
def test_candidate_data_is_encrypted_at_rest_and_audit_has_no_cv_content(tm, app_env):
    s = tm(1)
    mid = _setup_tlj(s)
    secret = "Banque Exemple — Tech Lead Java"
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode())])
    s.post(f"/api/missions/{mid}/candidates/{s.get(f'/api/missions/{mid}/candidates').json()[0]['id']}/notes",
           json={"kind": "candidate_call_note", "text": "Il a conçu un producteur Kafka avec gestion des erreurs sur 3 topics."})
    raw = open(app_env / "test.db", "rb").read()
    for token in (b"Banque Exemple", b"producteur Kafka", b"Tech Lead Java \xe2\x80\x94", b"dead letter"):
        assert token not in raw, f"donnée candidat en clair dans la base : {token!r}"
    con = sqlite3.connect(app_env / "test.db")
    details = " ".join(str(r[0]) for r in con.execute("select detail from audit_events"))
    assert "Banque" not in details and "producteur" not in details
    con.close()


def test_retention_purge_deletes_expired_candidate_data_and_is_audited(tm, app):
    s = tm(1)
    mid = _setup_tlj(s)
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode()), ("b.txt", C.CV_TLJ_B.encode())])
    from datetime import date, timedelta
    from talentlab.db import session_scope
    from talentlab.models import Document
    with session_scope() as db:
        d = db.query(Document).filter(Document.filename == "a.txt").one()
        d.expires_at = date.today() - timedelta(days=1)
    admin = Session(app, "admin.demo@example.invalid")
    r = admin.post("/api/admin/purge").json()
    assert r["documents"] == 1 and r["candidates"] == 1
    assert len(s.get(f"/api/missions/{mid}/candidates").json()) == 1
    admin.close()


def test_candidate_status_discard_requires_a_human_reason(tm):
    s = tm(1)
    mid = _setup_tlj(s)
    upload(s, mid, [("a.txt", C.CV_TLJ_C_DECLARED.encode())])
    c = s.get(f"/api/missions/{mid}/candidates").json()[0]
    assert s.post(f"/api/missions/{mid}/candidates/{c['id']}/status", json={"status": "ecarte"}).status_code == 422
    ok = s.post(f"/api/missions/{mid}/candidates/{c['id']}/status", json={"status": "ecarte", "comment": "Kafka insuffisant pour ce client"})
    assert ok.status_code == 200 and ok.json()["status"] == "ecarte"
    # l'écart n'efface rien : le profil reste consultable et réévaluable
    assert s.get(f"/api/missions/{mid}/candidates/{c['id']}").status_code == 200
    assert s.post(f"/api/missions/{mid}/candidates/{c['id']}/reassess", json={"reason": "nouvelle info"}).status_code == 200
