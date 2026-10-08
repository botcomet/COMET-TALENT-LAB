"""Lots D/E — assistant, coach, bibliothèque, apprentissage, corrections, IA vérifiée."""
import json

import pytest

from talentlab.domain.claims import Claim, locate, verify_claims
from talentlab.domain.enums import Level
from talentlab.llm.provider import NullProvider, build_prompt, propose_claims
from tests.conftest import Session, create_mission, freeze, upload
from tests.fixtures import briefs as B, cvs as C


def setup_tlj(s):
    m = create_mission(s)
    freeze(s, m["id"], mutate={"skill:kafka": {"depth_required": "advanced"}})
    return m["id"]


def reqs(s, mid):
    return [{k: r[k] for k in ("id", "key", "category", "depth_required", "status")} for r in s.get(f"/api/missions/{mid}").json()["requirements"]]


# =============================================================================== assistant : propose, puis confirme
def test_assistant_proposes_requirement_change_and_applies_nothing_until_confirmed(tm):
    s = tm(1)
    mid = setup_tlj(s)
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode())])
    before = reqs(s, mid)
    r = s.post(f"/api/missions/{mid}/assistant", json={"message": "Le client vient de préciser que Kafka Connect est impératif"}).json()
    assert r["proposals"] and r["proposals"][0]["kind"] == "requirement_change"
    p = r["proposals"][0]
    assert p["payload"]["category_hint"] == "imperatif" and "pas une compétence du candidat" in p["explanation"]
    assert any("réévalué" in c for c in p["consequences"]) and any("grille" in c for c in p["consequences"])
    assert reqs(s, mid) == before, "rien n'est modifié avant confirmation"
    grids_before = len(s.get(f"/api/missions/{mid}").json()["grids"])
    ok = s.post(f"/api/missions/{mid}/proposals/{p['id']}/confirm")
    assert ok.status_code == 200 and ok.json()["requirement_id"]
    after = reqs(s, mid)
    kc = [x for x in after if x["key"] == "skill:kafka_connect"]
    assert any(x["category"] == "imperatif" for x in kc)
    assert len(s.get(f"/api/missions/{mid}").json()["grids"]) == grids_before, "la grille figée n'est pas modifiée : nouvelle version nécessaire"
    assert s.post(f"/api/missions/{mid}/proposals/{p['id']}/confirm").status_code == 409, "une proposition ne s'applique qu'une fois"
    # « actualise le scoring » → proposition de nouvelle version, pas de réévaluation silencieuse
    r2 = s.post(f"/api/missions/{mid}/assistant", json={"message": "Actualise le scoring selon cette précision"}).json()
    assert r2["proposals"] and r2["proposals"][0]["kind"] == "new_grid_version"
    done = s.post(f"/api/missions/{mid}/proposals/{r2['proposals'][0]['id']}/confirm").json()
    assert done["version"] == 2 and done["diff"]["added"] or done["diff"]["changed"]
    cands = s.get(f"/api/missions/{mid}/candidates").json()
    assert {c["assessment"]["grid_version"] for c in cands} == {1}, "les scores ne changent pas avant validation de la grille v2"


def test_assistant_loosens_a_search_as_a_confirmable_proposal(tm):
    s = tm(1)
    mid = setup_tlj(s)
    ss = s.post(f"/api/missions/{mid}/searches/generate").json()
    strict = next(x for x in ss if x["strategy"] == "strict")
    r = s.post(f"/api/missions/{mid}/assistant", json={"message": "Rends la recherche moins stricte", "search_id": strict["id"]}).json()
    assert r["proposals"] and r["proposals"][0]["kind"] == "search_variant"
    n_before = len(s.get(f"/api/missions/{mid}/searches").json())
    assert r["proposals"][0]["payload"]["query"] != strict["query"] and "Conséquences" in r["reply"]
    s.post(f"/api/missions/{mid}/proposals/{r['proposals'][0]['id']}/confirm")
    assert len(s.get(f"/api/missions/{mid}/searches").json()) == n_before + 1
    # refus : rien ne change
    r2 = s.post(f"/api/missions/{mid}/assistant", json={"message": "élargis la recherche", "search_id": strict["id"]}).json()
    if r2["proposals"]:
        assert s.post(f"/api/missions/{mid}/proposals/{r2['proposals'][0]['id']}/reject").status_code == 200
        assert len(s.get(f"/api/missions/{mid}/searches").json()) == n_before + 1


def test_assistant_explains_a_score_compares_candidates_and_prepares_the_call(tm):
    s = tm(1)
    mid = setup_tlj(s)
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode()), ("b.txt", C.CV_TLJ_B.encode())])
    c = {x["label"]: x for x in s.get(f"/api/missions/{mid}/candidates").json()}
    a, b = c["a"], c["b"]
    r = s.post(f"/api/missions/{mid}/assistant", json={"message": f"Pourquoi as-tu donné ce score à {a['ref']} ?"}).json()
    assert a["ref"] in r["reply"] and "Kafka" in r["reply"] and "potentiel" in r["reply"]
    r = s.post(f"/api/missions/{mid}/assistant", json={"message": f"Compare {a['ref']} et {b['ref']}"}).json()
    assert b["ref"] in r["reply"].split(">")[0] and r["data"]["grid_hash"]
    r = s.post(f"/api/missions/{mid}/assistant", json={"message": f"Quels points dois-je vérifier pendant l'appel avec {a['ref']} ?"}).json()
    assert "Kafka" in r["reply"] and len(r["data"]["priority"]) == 3
    r = s.post(f"/api/missions/{mid}/assistant", json={"message": "Quels profils restent intéressants ?"}).json()
    assert b["ref"] in r["data"]["profiles"]
    assert "jamais un rejet automatique" in r["reply"]


def test_assistant_changes_require_edit_rights(tm):
    owner, reader = tm(1), tm(2)
    mid = setup_tlj(owner)
    owner.post(f"/api/missions/{mid}/shares", json={"email": reader.user["email"], "scope": "lecture"})
    r = reader.post(f"/api/missions/{mid}/assistant", json={"message": "Le client vient de préciser que Avro est impératif"})
    assert r.status_code == 403
    p = owner.post(f"/api/missions/{mid}/assistant", json={"message": "Le client a précisé que Avro est indispensable"}).json()["proposals"][0]
    assert reader.post(f"/api/missions/{mid}/proposals/{p['id']}/confirm").status_code == 403


def test_brief_notes_become_proposals_not_changes(tm):
    s = tm(1)
    mid = setup_tlj(s)
    before = reqs(s, mid)
    r = s.post(f"/api/missions/{mid}/brief-notes", json={"kind": "client_brief_note", "text": "Le client veut quelqu'un qui a déjà travaillé sur Kafka Connect.", "author": "Commercial (fictif)"}).json()
    assert r["proposals"][0]["payload"]["relayed"] is True and "pas des compétences de candidat" in r["note"]
    assert reqs(s, mid) == before


# =============================================================================== tests 9/6 via l'API (appels)
@pytest.mark.business
def test_business_9_api_call_limits_claim_and_keeps_history(tm):
    s = tm(1)
    m = create_mission(s, "Architecte SI", "Missions\n- Définir l'architecture cible\n\nCompétences requises\n- Architecture SI : décisionnaire\n")
    mid = m["id"]
    freeze(s, mid)
    upload(s, mid, [("arch.txt", C.CV_ARCH_CLAIM.encode())])
    c = s.get(f"/api/missions/{mid}/candidates").json()[0]
    first = s.get(f"/api/missions/{mid}/candidates/{c['id']}").json()["assessment_full"]["result"]
    arch1 = next(x for x in first["criteria"] if x["key"].endswith("architecture"))
    assert arch1["level"] == "confirme_demontre"
    n = s.post(f"/api/missions/{mid}/candidates/{c['id']}/notes", json={"kind": "candidate_call_note", "text": "Il était uniquement contributeur sur l'architecture, il ne décidait pas."}).json()
    arch2 = next(x for x in n["assessment"]["result"]["criteria"] if x["key"].endswith("architecture"))
    assert arch2["level"] == "partiellement_demontre" and arch2["contradictions"][0]["type"] == "limite"
    ev = s.get(f"/api/missions/{mid}/candidates/{c['id']}").json()["evidence"]
    assert ev and ev[0]["kind"] == "limite"
    h = s.get(f"/api/missions/{mid}/candidates/{c['id']}").json()["history"]
    assert [x["version"] for x in h] == [1, 2] and h[0]["score_documented"] > h[1]["score_documented"]
    # rejeter l'information → retour au niveau du CV, traçable
    rv = s.post(f"/api/missions/{mid}/evidence/{ev[0]['id']}/review", json={"decision": "rejected", "note": "erreur de transcription"}).json()
    arch3 = next(x for x in rv["assessment"]["result"]["criteria"] if x["key"].endswith("architecture"))
    assert arch3["level"] == "confirme_demontre"
    assert s.get(f"/api/missions/{mid}/candidates/{c['id']}").json()["evidence"][0]["review"] == "rejected", "le rejet est un avis ajouté, la preuve initiale est conservée"


@pytest.mark.business
def test_business_6_api_unvalidated_transcript_then_validated(tm):
    s = tm(1)
    mid = setup_tlj(s)
    upload(s, mid, [("c.txt", C.CV_TLJ_C_DECLARED.encode())])
    c = s.get(f"/api/missions/{mid}/candidates").json()[0]
    txt = "Recruteur : Parle-moi de Kafka Connect.\nCandidat : J'ai conçu et mis en place des connecteurs Kafka Connect avec Avro et Schema Registry sur le cluster de production, 20 connecteurs."
    n = s.post(f"/api/missions/{mid}/candidates/{c['id']}/notes", json={"kind": "transcript", "text": txt, "auto_generated": True,
                                                                            "speaker_map": {"Candidat": "candidate", "Recruteur": "recruiter"}}).json()
    kc = next(x for x in n["assessment"]["result"]["criteria"] if x["key"].endswith("kafka_connect"))
    assert kc["level"] == "partiellement_demontre", "transcription automatique non validée : plafonnée"
    fact = next(f for f in n["facts"] if f["subject_key"].endswith("kafka_connect"))
    assert fact["needs_verification"] and "transcription" in fact["why_verify"].lower()
    v = s.post(f"/api/missions/{mid}/evidence/{fact['id']}/review", json={"decision": "validated"}).json()
    kc2 = next(x for x in v["assessment"]["result"]["criteria"] if x["key"].endswith("kafka_connect"))
    assert kc2["level"] == "confirme_demontre" and v["assessment"]["result"]["score_documented"] > n["assessment"]["result"]["score_documented"]


# =============================================================================== corrections du recruteur (§17.3)
def test_recruiter_correction_lowers_level_records_error_nature_and_changes_no_global_rule(tm):
    s = tm(1)
    mid = setup_tlj(s)
    upload(s, mid, [("b.txt", C.CV_TLJ_B.encode()), ("a.txt", C.CV_TLJ_A.encode())])
    cs = {c["label"]: c for c in s.get(f"/api/missions/{mid}/candidates").json()}
    b, a = cs["b"], cs["a"]
    a_score = a["assessment"]["score_documented"]
    det = s.get(f"/api/missions/{mid}/candidates/{b['id']}").json()
    asm_id = det["assessment_full"]["id"]
    bad = s.post(f"/api/missions/{mid}/candidates/{b['id']}/correction", json={"assessment_id": asm_id, "criterion_key": "skill:spring", "new_level": "declare_sans_preuve",
                                                                             "error_nature": "n_importe_quoi", "comment": "Spring seulement cité"})
    assert bad.status_code == 422
    r = s.post(f"/api/missions/{mid}/candidates/{b['id']}/correction", json={"assessment_id": asm_id, "criterion_key": "skill:spring", "new_level": "declare_sans_preuve",
                                                                           "error_nature": "mention_surevaluee", "comment": "Spring n'est qu'en environnement, pas de réalisation décrite"})
    assert r.status_code == 201
    new = r.json()["assessment"]["result"]
    sp = next(x for x in new["criteria"] if x["key"] == "skill:spring")
    assert sp["level"] == "declare_sans_preuve" and sp["contradictions"][0]["type"] == "correction_recruteur"
    assert r.json()["regression_case"]["rule_description"] == "mention_surevaluee"
    cases = s.get("/api/library/regression-cases").json()
    assert cases and cases[0]["error_nature"] == "mention_surevaluee" and cases[0]["case"]["note"].startswith("Cas anonymisé")
    # aucune règle globale modifiée : un autre candidat garde exactement son score
    assert s.get(f"/api/missions/{mid}/candidates/{a['id']}").json()["assessment"]["score_documented"] == a_score


# =============================================================================== bibliothèque & apprentissage (§17)
def test_client_feedback_proposes_a_lesson_that_stays_client_specific_until_validated(tm):
    s = tm(1)
    m = create_mission(s, B.DSF_TITLE, B.DSF)
    mid = m["id"]
    reqs_before = reqs(s, mid)
    s.post(f"/api/missions/{mid}/sources", json={"kind": "retour_entretien", "author": "Responsable front (fictif)", "source_date": "2026-10-03",
                                                  "text": "Le profil présenté est insuffisant sur le Design System à grande échelle : il utilise des composants mais n'a pas conçu le Design System."})
    lib = s.get("/api/library").json()
    lesson = next(e for e in lib if e["kind"] == "enseignement_retour_client")
    assert lesson["status"] == "proposed" and lesson["client_specific"] is True
    assert "composants" in lesson["body"] and "conception" in lesson["body"].lower()
    assert [x for x in reqs(s, mid) if x["key"] == "skill:design_system"] != []
    # publication : validation humaine ; la généralisation est un acte distinct
    pub = s.post(f"/api/library/{lesson['id']}/publish", json={"generalize": False}).json()
    assert pub["status"] == "published" and pub["client_specific"] is True and pub["validated_by"]
    # missions comparables : suggestion avec mise en garde, sans aucun effet sur leurs exigences
    other = tm(2)
    m2 = create_mission(other, "Développeur Front-End Design System", B.DSF, client="Client B (fictif)")
    sug = other.get(f"/api/missions/{m2['id']}").json()["knowledge_suggestions"]
    assert sug and "spécifique à un client" in sug[0]["caution"].replace("propre à un client", "spécifique à un client")
    assert all(r["category"] != "eliminatoire_confirme" for r in other.get(f"/api/missions/{m2['id']}").json()["requirements"])
    gen = s.post(f"/api/library/{lesson['id']}/publish", json={"generalize": True}).json()
    assert gen["client_specific"] is False


def test_library_drafts_are_private_and_edits_require_revalidation(tm):
    a, b = tm(1), tm(2)
    e = a.post("/api/library", json={"kind": "note_metier", "title": "Distinguer AMOA et MOE", "body": "Toujours demander qui paramétrait et qui coordonnait l'intégrateur."}).json()
    assert e["status"] == "proposed"
    assert all(x["id"] != e["id"] for x in b.get("/api/library").json())
    assert b.patch(f"/api/library/{e['id']}", json={"title": "x" * 10}).status_code == 404
    a.post(f"/api/library/{e['id']}/publish", json={})
    assert any(x["id"] == e["id"] for x in b.get("/api/library").json())
    assert b.patch(f"/api/library/{e['id']}", json={"title": "Autre titre"}).status_code == 403, "seul l'auteur modifie"
    upd = a.patch(f"/api/library/{e['id']}", json={"body": "Version corrigée : demander aussi quels livrables étaient produits."}).json()
    assert upd["status"] == "proposed" and upd["version"] == 2, "toute modification repasse en validation"


def test_boolean_learning_zero_then_usable_creates_a_proposed_entry(tm):
    s = tm(1)
    mid = setup_tlj(s)
    ss = s.post(f"/api/missions/{mid}/searches/generate").json()
    strict = next(x for x in ss if x["strategy"] == "strict")
    f1 = s.post(f"/api/missions/{mid}/searches/{strict['id']}/feedback", json={"result_count": 0}).json()
    assert f1["new_search"]
    f2 = s.post(f"/api/missions/{mid}/searches/{f1['new_search']['id']}/feedback", json={"result_count": 45, "relevance": "bonne"}).json()
    assert f2["diagnosis"]["problem"] == "acceptable"
    entry = next((e for e in s.get("/api/library").json() if e["kind"] == "booleen"), None)
    assert entry and "zéro résultat" in entry["body"].lower() and "n'ont pas été modifiées" in entry["body"] and entry["status"] == "proposed"
    hist = s.get(f"/api/missions/{mid}/history").json()["searches"]
    lin = [x for x in hist if x["lineage"] == strict["lineage"]]
    assert len(lin) == 2 and lin[1]["parent_id"] == lin[0]["id"] and lin[1]["modification"], "version, auteur, résultats, modification, conclusion conservés"


# =============================================================================== coach
def test_coach_explains_professional_reasoning_without_ranking_recruiters(tm, app):
    s = tm(1)
    r = s.post("/api/coach", json={"question": "Quelle différence entre un consultant SAP AMOA et MOE ?"}).json()
    assert "paramétrage" in json.dumps(r, ensure_ascii=False) and "intégrateur" in json.dumps(r, ensure_ascii=False)
    r = s.post("/api/coach", json={"question": "Comment vérifier qu'un développeur a réellement construit un Design System ?"}).json()
    assert "gouvernance" in json.dumps(r, ensure_ascii=False).lower() and "combien d'équipes" in json.dumps(r, ensure_ascii=False)
    r = s.post("/api/coach", json={"question": "Pourquoi ce candidat Data Analyst ne correspond-il pas à une mission Data Quality ?"}).json()
    assert "réconcili" in json.dumps(r, ensure_ascii=False).lower()
    paths = list(app.openapi()["paths"])
    assert not any(w in p for p in paths for w in ("leaderboard", "ranking", "classement", "stats/recruit", "recruiter-score")), "pas de classement des recruteurs"


def test_coach_explains_why_a_search_is_too_restrictive_from_real_data(tm):
    s = tm(1)
    mid = setup_tlj(s)
    ss = s.post(f"/api/missions/{mid}/searches/generate").json()
    strict = next(x for x in ss if x["strategy"] == "strict")
    r = s.post("/api/coach", json={"question": "Pourquoi cette recherche est-elle trop restrictive ?", "mission_id": mid, "search_id": strict["id"]}).json()
    body = json.dumps(r, ensure_ascii=False)
    assert "groupe" in body and "Kafka" in body and "Rappel" in body or "rappel" in body.lower()


def test_coach_explains_a_low_kafka_score_from_the_criterion(tm):
    s = tm(1)
    mid = setup_tlj(s)
    upload(s, mid, [("a.txt", C.CV_TLJ_A.encode())])
    c = s.get(f"/api/missions/{mid}/candidates").json()[0]
    r = s.post("/api/coach", json={"question": "Pourquoi ce profil obtient-il une faible note sur Kafka ?", "mission_id": mid, "candidate_id": c["id"], "criterion_key": "skill:kafka"}).json()
    body = json.dumps(r, ensure_ascii=False)
    assert "Connect" in body and "preuve" in body.lower() and "partiellement" in body


# =============================================================================== test 10 : une IA affirme → preuve citable ou rétrogradation
@pytest.mark.business
def test_business_10_ai_claims_without_verifiable_quote_are_downgraded_to_hypothesis():
    text = C.CV_TLJ_A
    claims = [
        Claim("skill:kafka", "Kafka", "Conception et développement d'un producteur et d'un consommateur Kafka (2 topics)", Level.CONFIRMED),
        Claim("skill:kafka_connect", "Kafka Connect", "Expert Kafka Connect avec 50 connecteurs en production", Level.CONFIRMED),   # citation inventée
        Claim("skill:avro", "Avro", "", Level.CONFIRMED),                                                                         # aucune citation
        Claim("skill:spring", "Spring Boot", "Encadrement technique d'une équipe de 5 développeurs, revues de code", Level.CONFIRMED),   # citation réelle mais autre sujet
    ]
    v = {x.claim.criterion_key: x for x in verify_claims(claims, text)}
    assert v["skill:kafka"].status == "verified" and v["skill:kafka"].level in (Level.CONFIRMED, Level.PARTIAL)
    assert text[v["skill:kafka"].start:v["skill:kafka"].end].startswith("Conception et développement")
    for k in ("skill:kafka_connect", "skill:avro", "skill:spring"):
        assert v[k].status == "downgraded" and v[k].level is None, k
    assert "introuvable" in v["skill:kafka_connect"].reason and "Aucune citation" in v["skill:avro"].reason and "ne mentionne pas" in v["skill:spring"].reason


def test_quote_location_is_exact_not_fuzzy():
    assert locate(C.CV_TLJ_B, "schémas Avro avec Schema Registry") is not None
    assert locate(C.CV_TLJ_B, "schémas Protobuf avec Schema Registry") is None
    assert locate(C.CV_TLJ_B, "court") is None


class FakeProvider:
    name = "fake"

    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def complete_json(self, system, user, *, max_tokens=2000):
        self.calls.append((system, user))
        return self.payload


def test_llm_assist_endpoint_only_trusts_verified_quotes_and_caps_until_human_validation(tm, app):
    from talentlab.api.routes import llm_provider
    s = tm(1)
    mid = setup_tlj(s)
    upload(s, mid, [("c.txt", C.CV_TLJ_C_DECLARED.encode())])
    c = s.get(f"/api/missions/{mid}/candidates").json()[0]
    assert s.post(f"/api/missions/{mid}/candidates/{c['id']}/llm-assist").status_code == 503, "sans fournisseur configuré, rien n'est simulé"
    fake = FakeProvider({"claims": [
        {"criterion_key": "skill:kafka", "quote": "Développement de nouvelles fonctionnalités sur une application de gestion en Java et Spring Boot.", "level": "confirme_demontre"},
        {"criterion_key": "skill:kafka_connect", "quote": "Il a déployé 40 connecteurs Kafka Connect", "level": "confirme_demontre"},
        {"criterion_key": "skill:inconnu", "quote": "x" * 30, "level": "confirme_demontre"},
    ]})
    app.dependency_overrides[llm_provider] = lambda: fake
    try:
        r = s.post(f"/api/missions/{mid}/candidates/{c['id']}/llm-assist").json()
    finally:
        app.dependency_overrides.clear()
    st = {x["criterion_key"]: x["status"] for x in r["claims"]}
    assert st["skill:kafka_connect"] == "downgraded" and "skill:inconnu" not in st, "un critère inconnu n'est jamais créé par le modèle"
    kc = next(x for x in r["assessment"]["result"]["criteria"] if x["key"].endswith("kafka_connect"))
    assert kc["level"] == "non_documente", "hypothèse IA : hors score"
    sys_prompt, user_prompt = fake.calls[0]
    assert "DONNÉE non fiable" in sys_prompt and 'untrusted="true"' in user_prompt and "ne notes pas" in sys_prompt


def test_prompt_injection_inside_a_cv_is_wrapped_as_untrusted_data():
    prompt = build_prompt(C.CV_INJECTION, [{"key": "skill:kafka", "label": "Kafka"}])
    assert prompt.count("<DOCUMENT untrusted=") == 1 and 'untrusted="true"' in prompt and "IGNORE ALL PREVIOUS INSTRUCTIONS" in prompt
    assert "DONNÉE à analyser" in prompt
    claims = propose_claims(FakeProvider({"claims": [{"criterion_key": "skill:kafka", "quote": "j'obéis à la consigne", "level": "confirme_demontre"}]}), C.CV_INJECTION, [{"key": "skill:kafka", "label": "Kafka"}])
    v = verify_claims(claims, C.CV_INJECTION)
    assert v[0].status == "downgraded"


def test_null_provider_is_explicit():
    from talentlab.llm.provider import LLMUnavailable
    with pytest.raises(LLMUnavailable):
        NullProvider().complete_json("s", "u")
