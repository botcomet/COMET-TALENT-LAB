"""Lot C/D — matching, preuves, scoring, qualification : tests métier 3 à 14 et cas de référence (Partie IV, §26)."""
import copy
import json

import pytest

from talentlab.domain import boolean as bl
from talentlab.domain import grid as gd
from talentlab.domain import qualification as qa
from talentlab.domain.call_facts import candidate_facts_from, extract_facts, reliability_for, source_for
from talentlab.domain.cv_extract import ExtractionError, extract_text, parse_cv
from talentlab.domain.enums import Category, EvidenceKind, Level, Reliability, SourceKind, Tier
from talentlab.domain.evidence import ExtEvidence
from talentlab.domain.safety import scan_text
from talentlab.domain.scoring import CandidateFacts, assess, compare, diff_assessments
from talentlab.domain.search_plan import build_search_set
from tests.fixtures import briefs as B, cvs as C
from tests.helpers import TODAY, crit, frozen_grid, make_docx, make_encrypted_pdf, make_image_only_pdf, make_pdf, reqs_for, score


def kafka_advanced(reqs):
    for r in reqs:
        if r.key == "skill:kafka":
            r.depth_required = "advanced"


@pytest.fixture(scope="module")
def tlj():
    return frozen_grid(B.TLJ_TITLE, B.TLJ, kafka_advanced)


def _ext(subject, kind, text, *, level=None, validated=False, auto=False, rel=Reliability.EXPLAINED_IN_CALL, id_="e1"):
    return ExtEvidence(id=id_, subject_key=subject, kind=kind, excerpt=text, source=source_for("candidate_call_note"), reliability=rel,
                       level=level, validated=validated, auto_generated=auto)


# =============================================================================== cas STIME Tech Lead Java
@pytest.mark.business
def test_case_tech_lead_java_kafka_is_real_but_modest_not_expert(tlj):
    grid, _ = tlj
    a = score(grid, C.CV_TLJ_A)
    kafka = crit(a, "kafka")
    assert kafka.level == Level.PARTIAL.value, "Kafka réel mais profondeur avancée non démontrée"
    assert kafka.points < kafka.weight
    assert any("Connect" in m or "Avro" in m for m in kafka.advanced_missing), "le manque est nommé"
    # le vécu réel est valorisé : le RUN N3 et l'encadrement sont démontrés
    assert crit(a, "run_n3").level == Level.CONFIRMED.value and crit(a, "tech_mgmt").level == Level.CONFIRMED.value


@pytest.mark.business
def test_case_tech_lead_java_bank_volumetry_is_not_kafka_volumetry():
    grid, _ = frozen_grid(B.TLJ_TITLE, B.TLJ, lambda rs: [setattr(r, "category", Category.IMPERATIF) or setattr(r, "scope_terms", ["kafka"]) for r in rs if r.key == "domain:high_volume"])
    a = score(grid, C.CV_TLJ_A)
    hv = crit(a, "high_volume")
    # la forte volumétrie existe côté banque (plusieurs millions/jour) mais pas dans la fenêtre Kafka (100/semaine)
    assert hv.level == Level.DECLARED.value and "FAIBLE" in hv.justification, hv.justification
    assert any("n'est pas transférée" in n for n in hv.notes)
    # sans portée Kafka, la plateforme bancaire démontre bien une forte volumétrie
    grid2, _ = frozen_grid(B.TLJ_TITLE, B.TLJ, lambda rs: [setattr(r, "category", Category.IMPERATIF) for r in rs if r.key == "domain:high_volume"])
    assert crit(score(grid2, C.CV_TLJ_A), "high_volume").level == Level.CONFIRMED.value
    assert crit(score(grid, C.CV_TLJ_B), "high_volume").level == Level.CONFIRMED.value


@pytest.mark.business
def test_case_tech_lead_java_e_commerce_is_family_site_not_enterprise_backoffice(tlj):
    grid, _ = tlj
    a = score(grid, C.CV_TLJ_A)
    ec = crit(a, "retail_ecom")
    assert ec.level == Level.DECLARED.value and "non professionnel" in ec.justification
    b = score(grid, C.CV_TLJ_B)
    assert crit(b, "retail_ecom").level == Level.CONFIRMED.value


@pytest.mark.business
def test_case_stronger_kafka_profile_ranks_above_modest_one_on_the_same_grid(tlj):
    grid, _ = tlj
    a, b = score(grid, C.CV_TLJ_A), score(grid, C.CV_TLJ_B)
    assert b.score_documented > a.score_documented
    assert b.tier == Tier.VERY_INTERESTING.value or b.tier == Tier.INTERESTING.value
    assert a.tier == Tier.TO_QUALIFY.value, "intéressant mais à qualifier sur Kafka — jamais un rejet automatique"


# =============================================================================== Test 3
@pytest.mark.business
def test_business_3_declared_kafka_is_not_expertise_and_triggers_a_qualification_question(tlj):
    grid, _ = tlj
    a = score(grid, C.CV_TLJ_C_DECLARED)
    k = crit(a, "kafka")
    assert k.level == Level.DECLARED.value and k.factor < 0.5
    q = qa.generate(a.to_dict())
    texts = [x["text"] for x in q["priority"] + q["complementary"]]
    assert any("Kafka" in t for t in texts)
    kq = next(x for x in q["priority"] + q["complementary"] if x["criterion_key"].endswith("kafka"))
    assert "liste" in kq["text"] or "environnement" in kq["text"] or "mentionne" in kq["text"]
    assert kq["proof_elements"] and kq["deepen_if"] and kq["answer_type"]


# =============================================================================== Test 4 (Reflex)
@pytest.mark.business
def test_business_4_imperative_missing_cannot_be_compensated_by_related_skills():
    grid, _ = frozen_grid(B.WMS_TITLE, B.WMS)
    a = score(grid, C.CV_WMS_NOREFLEX)
    reflex = crit(a, "reflex_wms")
    assert reflex.level == Level.NOT_DOCUMENTED.value
    assert a.tier != Tier.VERY_INTERESTING.value and a.score_documented < 60
    assert any("Manque majeur" in al["message"] and "Reflex" in al["message"] for al in a.alerts)
    assert "Reflex WMS" in a.open_mandatory
    assert a.score_potential < 100, "le potentiel ne gonfle pas ce qui n'est documenté nulle part"


@pytest.mark.business
def test_business_4b_explicit_absence_applies_validated_cap_and_major_gap():
    grid, _ = frozen_grid(B.WMS_TITLE, B.WMS)
    ext = {"skill:reflex_wms": [_ext("skill:reflex_wms", EvidenceKind.CONTRADICTS, "Je n'ai jamais travaillé sur Reflex, uniquement AS400.")]}
    a = score(grid, C.CV_WMS_NOREFLEX, ext)
    assert crit(a, "reflex_wms").level == Level.CONTRADICTED.value
    assert a.tier == Tier.MAJOR_GAP.value and a.score_documented <= grid.caps["contradicted_imperative"]
    assert a.caps_applied and any(al["severity"] == "bloquant" for al in a.alerts)


def test_not_documented_is_never_confused_with_contradicted():
    grid, _ = frozen_grid(B.WMS_TITLE, B.WMS)
    a = score(grid, C.CV_WMS_NOREFLEX)
    assert Level.CONTRADICTED.value not in {c.level for c in a.criteria}
    assert not a.caps_applied, "aucun plafond sur simple absence de documentation (§6.8)"


# =============================================================================== Test 5
@pytest.mark.business
def test_business_5_unknown_availability_stays_unknown(tlj):
    grid, _ = tlj
    a = score(grid, C.CV_TLJ_B)
    av = next(c for c in a.constraints if c["key"] == "constraint:availability")
    assert av["status"] == "inconnu" and "inconnue" in av["explanation"] and av["question"]
    assert not any(c["status"] == "incompatible" for c in a.constraints)


def test_explicit_refusal_of_constraints_is_flagged_clearly_and_separate_from_skills(tlj):
    grid, _ = tlj
    facts = CandidateFacts(refuses={"onsite", "astreinte"}, tjm=900)
    a = score(grid, C.CV_TLJ_B, facts=facts)
    st = {c["key"]: c["status"] for c in a.constraints}
    assert st["constraint:presence"] == "incompatible" and st["constraint:astreinte"] == "incompatible"
    assert any(al["severity"] == "contrainte" for al in a.alerts)
    assert a.score_documented == score(grid, C.CV_TLJ_B).score_documented, "une contrainte n'altère jamais le score technique (§15.8)"


def test_unknown_tjm_is_not_incompatible():
    grid, _ = frozen_grid("Consultant", "Compétences requises\n- Python\nTJM maximum : 600 €\n")
    a = score(grid, "Jean Fictif\nDéveloppeur Python\n\nEXPÉRIENCES PROFESSIONNELLES\nSociété — Développeur (Janvier 2018 – en cours)\n- Développement d'API REST en Python avec tests unitaires et revues de code.\nEnvironnement technique : Python, PostgreSQL\n" * 3)
    tj = next(c for c in a.constraints if c["key"] == "constraint:tjm")
    assert tj["status"] == "inconnu"
    a2 = score(grid, "Jean Fictif\nDéveloppeur Python\n\nEXPÉRIENCES PROFESSIONNELLES\nSociété — Développeur (Janvier 2018 – en cours)\n- Développement d'API REST en Python avec tests unitaires et revues de code.\nEnvironnement technique : Python, PostgreSQL\n" * 3, facts=CandidateFacts(tjm=800))
    assert next(c for c in a2.constraints if c["key"] == "constraint:tjm")["status"] == "a_valider"


# =============================================================================== Test 6 / enrichissement
@pytest.mark.business
def test_business_6_call_confirms_unknown_experience_new_evidence_updated_score_and_history(tlj):
    grid, _ = tlj
    before = score(grid, C.CV_TLJ_A)
    note = ("Il a mis en place des connecteurs Kafka Connect vers PostgreSQL, avec transformations et gestion des erreurs ; "
            "il a conçu les schémas Avro avec Schema Registry et exploite le cluster Kafka en production.")
    facts = extract_facts(note, "candidate_call_note")
    ext: dict[str, list[ExtEvidence]] = {}
    for i, f in enumerate(facts):
        if f.kind == "candidate_experience" and f.evidence_kind == EvidenceKind.SUPPORTS:
            ext.setdefault(f.topic_key, []).append(ExtEvidence(id=f"c{i}", subject_key=f.topic_key, kind=f.evidence_kind, excerpt=f.statement,
                                                               source=source_for("candidate_call_note"), reliability=reliability_for("candidate_call_note"),
                                                               level=f.level, validated=True))
    after = assess(grid, parse_cv(C.CV_TLJ_A, today=TODAY), ext, today=TODAY)
    snapshot_before = copy.deepcopy(before.to_dict())
    assert after.score_documented > before.score_documented
    d = diff_assessments(before.to_dict(), after.to_dict())
    assert d["score_delta"] > 0 and d["changes"]
    changed = {c["key"]: c for c in d["changes"]}
    assert "skill:kafka_connect" in changed and changed["skill:kafka_connect"]["because"], "la raison de chaque modification est citée"
    assert before.to_dict() == snapshot_before, "l'analyse initiale n'est jamais modifiée rétroactivement"
    assert not any(e["source"] == "cv" and e["location"] == "call" for c in before.criteria for e in c.evidence)


def test_unvalidated_automatic_transcript_is_capped_until_validated(tlj):
    grid, _ = tlj
    txt = ("Recruteur : Parle-moi de Kafka Connect.\n"
           "Candidat : J'ai conçu et mis en place des connecteurs Kafka Connect avec Avro et Schema Registry sur le cluster de production, 20 connecteurs.")
    facts = extract_facts(txt, "transcript")
    f = next(x for x in facts if x.topic_key == "skill:kafka_connect")
    assert f.needs_verification and "transcription" in f.why_verify.lower()
    def mk(validated):
        return {"skill:kafka_connect": [ExtEvidence(id="t1", subject_key="skill:kafka_connect", kind=EvidenceKind.SUPPORTS, excerpt=f.statement,
                                                    source=source_for("transcript"), reliability=Reliability.EXPLAINED_IN_CALL, level=Level.CONFIRMED,
                                                    validated=validated, auto_generated=True)]}
    unv = crit(assess(grid, parse_cv(C.CV_TLJ_C_DECLARED, today=TODAY), mk(False), today=TODAY), "kafka_connect")
    val = crit(assess(grid, parse_cv(C.CV_TLJ_C_DECLARED, today=TODAY), mk(True), today=TODAY), "kafka_connect")
    assert unv.level == Level.PARTIAL.value and val.level == Level.CONFIRMED.value


def test_client_requirement_is_not_a_candidate_skill():
    facts = extract_facts("Le client veut quelqu'un qui a déjà travaillé sur Kafka Connect, c'est impératif.", "client_brief_note")
    f = facts[0]
    assert f.kind == "client_requirement" and f.requirement_hint["category_hint"] == "imperatif" and f.requirement_hint["relayed"]
    assert f.evidence_kind is None and "pas une compétence du candidat" in f.requirement_hint["note"]
    assert "confirmer par le client" in f.why_verify


def test_candidate_statement_limits_experience_but_does_not_change_the_requirement():
    facts = extract_facts("Candidat : Je n'ai utilisé Kafka que sur deux topics.", "transcript")
    f = next(x for x in facts if x.topic_key == "skill:kafka")
    assert f.kind == "candidate_experience" and f.evidence_kind == EvidenceKind.LIMITS and f.requirement_hint is None


def test_speakers_roles_and_hedges_are_tracked():
    txt = "Marc (client) : Il nous faut Kafka Connect.\nCamille : Je crois que j'ai fait du Kafka Streams, environ deux ans.\nRecruteur : Très bien."
    facts = extract_facts(txt, "transcript", speaker_map={"Camille": "candidate", "Marc (client)": "client"})
    req = next(f for f in facts if f.kind == "client_requirement")
    assert req.speaker_role == "client" and not req.requirement_hint["relayed"]
    exp = next(f for f in facts if f.kind == "candidate_experience")
    assert exp.certainty == "hedged" and exp.needs_verification and exp.level != Level.CONFIRMED


def test_candidate_constraints_are_extracted_without_inventing():
    f = extract_facts("Candidat : Je suis disponible à partir du 3 novembre, mon TJM est de 650 euros. Je refuse les astreintes.", "transcript")
    cf = candidate_facts_from(f)
    assert cf.tjm == 650 and "astreinte" in cf.refuses and cf.availability and "novembre" in cf.availability
    assert candidate_facts_from(extract_facts("Candidat : J'ai travaillé sur Java.", "transcript")).availability is None


# =============================================================================== Test 7 / versions de grille
@pytest.mark.business
def test_business_7_client_feedback_creates_new_grid_version_and_consistent_reassessment():
    def base(rs):
        for r in rs:
            if r.key == "skill:design_system":
                r.depth_required = "practice"
    grid1, reqs = frozen_grid(B.DSF_TITLE, B.DSF, base)
    a1, b1 = score(grid1, C.CV_DS_A), score(grid1, C.CV_DS_B)
    # retour client : exige un Design System déployé à grande échelle (gouvernance, adoption multi-équipes)
    ds = next(r for r in reqs if r.key == "skill:design_system")
    ds.depth_required, ds.source_kind = "advanced", SourceKind.CLIENT_CLARIFICATION
    grid2_draft, d = gd.new_version(grid1, reqs, "Retour client : Design System conçu et déployé à grande échelle exigé")
    grid2 = gd.freeze(grid2_draft, reqs, "Recruteur Test", allow_unresolved_clarifications=True)
    assert grid2.version == 2 and grid2.content_hash != grid1.content_hash and d["changed"]
    a2, b2 = score(grid2, C.CV_DS_A), score(grid2, C.CV_DS_B)
    assert a2.score_documented <= a1.score_documented and b2.grid_version == a2.grid_version == 2
    assert crit(b2, "design_system").level == Level.CONFIRMED.value
    assert crit(a2, "design_system").level != Level.CONFIRMED.value
    # recherches adaptées : la stricte cherche désormais les indices de pratique avancée
    strict = build_search_set(B.DSF_TITLE, reqs).variants["strict"]
    assert any(g.kind == "narrower" for g in strict.groups) or "Storybook" in strict.query
    # les assessments v1 sont conservés tels quels
    assert a1.grid_version == 1


@pytest.mark.business
def test_case_design_system_profile_b_beats_profile_a_despite_fewer_react_years():
    grid, reqs = frozen_grid(B.DSF_TITLE, B.DSF)
    a, b = score(grid, C.CV_DS_A), score(grid, C.CV_DS_B)
    assert crit(a, "react").level == Level.CONFIRMED.value
    assert crit(a, "design_system").level != Level.CONFIRMED.value, "utiliser un DS ≠ en concevoir un"
    assert crit(b, "design_system").level == Level.CONFIRMED.value
    assert b.score_documented > a.score_documented
    assert compare({"A": a, "B": b})["why"]["leader"] == "B"


# =============================================================================== Test 8
@pytest.mark.business
def test_business_8_ten_cvs_same_brief_same_criteria_same_weights_same_logic(tlj):
    grid, _ = tlj
    cvs = [C.CV_TLJ_A, C.CV_TLJ_B, C.CV_TLJ_C_DECLARED, C.CV_KAFKA_OLD, C.CV_DEV_NO_RUN, C.CV_ARCH_CLAIM, C.CV_DS_A, C.CV_DS_B, C.CV_WMS_NOREFLEX, C.CV_DQ_REAL]
    results = [score(grid, cv) for cv in cvs]
    assert {a.grid_hash for a in results} == {grid.content_hash}
    assert len({tuple((c.key, c.weight) for c in a.criteria) for a in results}) == 1
    # la grille n'a pas bougé pendant la comparaison
    gd.assert_unchanged(grid)
    assert compare(dict(enumerate(results)))["grid_hash"] == grid.content_hash
    # déterminisme : même entrée → même score
    assert score(grid, C.CV_TLJ_A).score_documented == results[0].score_documented


def test_assessment_requires_a_frozen_grid():
    reqs = reqs_for(B.TLJ_TITLE, B.TLJ)
    draft = gd.propose_grid("m1", reqs)
    with pytest.raises(gd.GridError):
        assess(draft, parse_cv(C.CV_TLJ_A, today=TODAY), today=TODAY)


def test_compare_refuses_assessments_from_different_grid_versions(tlj):
    grid, reqs = tlj
    v2, _ = gd.new_version(grid, reqs, "Changement du besoin")
    v2 = gd.freeze(v2, reqs, "TM", allow_unresolved_clarifications=True)
    with pytest.raises(gd.GridError):
        compare({"A": score(grid, C.CV_TLJ_A), "B": score(v2, C.CV_TLJ_B)})


# =============================================================================== Test 9
@pytest.mark.business
def test_business_9_cv_suggests_architect_call_says_contributor_only_assessment_corrected_contradiction_kept():
    grid, _ = frozen_grid("Architecte SI", "Missions\n- Définir l'architecture cible\n\nCompétences requises\n- Architecture SI : décisionnaire et conception de l'architecture cible\n")
    before = score(grid, C.CV_ARCH_CLAIM)
    arch = crit(before, "architecture")
    assert arch.level == Level.CONFIRMED.value
    ext = {arch.key: [_ext(arch.key, EvidenceKind.LIMITS, "Il était uniquement contributeur sur l'architecture, ce n'est pas lui qui décidait.", validated=True)]}
    after = assess(grid, parse_cv(C.CV_ARCH_CLAIM, today=TODAY), ext, today=TODAY)
    arch2 = crit(after, "architecture")
    assert arch2.level == Level.PARTIAL.value and after.score_documented < before.score_documented
    assert arch2.contradictions and arch2.contradictions[0]["type"] == "limite"
    assert before.to_dict()["criteria"][0]["contradictions"] == [], "l'historique garde l'état antérieur"
    assert diff_assessments(before.to_dict(), after.to_dict())["changes"]


def test_participation_is_not_architecture_ownership():
    grid, _ = frozen_grid("Architecte SI", "Compétences requises\n- Architecture SI\n")
    a = score(grid, C.CV_TLJ_A)
    assert crit(a, "architecture").level != Level.CONFIRMED.value


# =============================================================================== Test 11
@pytest.mark.business
def test_business_11_broad_search_discovers_candidate_but_strict_matching_keeps_score_low(tlj):
    grid, reqs = tlj
    ss = build_search_set(B.TLJ_TITLE, reqs)
    cv_text = C.CV_TLJ_C_DECLARED.replace("Développeur Java senior", "Lead Developer — Développeur Java senior")
    assert bl.matches(ss.variants["exploratory"].query, cv_text), "découvert par la recherche élargie"
    a = score(grid, cv_text)
    assert a.score_documented < 60 and a.tier != Tier.VERY_INTERESTING.value
    assert a.open_mandatory


# =============================================================================== Test 12
@pytest.mark.business
def test_business_12_old_technology_use_is_penalised_when_recency_matters():
    grid, _ = frozen_grid(B.TLJ_TITLE, B.TLJ)          # Kafka est « volatile » : la récence compte
    a = score(grid, C.CV_KAFKA_OLD)
    k = crit(a, "kafka")
    assert k.level in (Level.PARTIAL.value, Level.DECLARED.value) and any("Dernière pratique" in n for n in k.notes)
    assert k.last_used == "2015-12"
    # sans exigence de récence (compétence stable), l'ancienneté n'est pas pénalisée
    grid2, _ = frozen_grid("Consultant", "Compétences requises\n- Kafka\n", lambda rs: [setattr(r, "recency_sensitive", False) for r in rs])
    assert crit(score(grid2, C.CV_KAFKA_OLD), "kafka").level == Level.CONFIRMED.value


def test_explicit_client_recency_requirement_sets_a_short_window():
    grid, reqs = frozen_grid("Consultant SAP GTS", "Compétences requises\n- Pratique récente de SAP GTS 11 sur les 3 dernières années\n")
    c = next(c for c in grid.criteria if c.key == "skill:sap_gts")
    assert c.recency_sensitive is True and c.recency_window_years == 3


# =============================================================================== Test 13
@pytest.mark.business
def test_business_13_better_written_description_does_not_raise_the_skill_score():
    grid, _ = frozen_grid("Développeur", "Compétences requises\n- Kafka\n- Java\n")
    plain, fancy = score(grid, C.KAFKA_PLAIN), score(grid, C.KAFKA_FANCY)
    for k in ("kafka", "java"):
        assert crit(plain, k).level == crit(fancy, k).level and crit(plain, k).points == crit(fancy, k).points
    assert plain.score_documented == fancy.score_documented


# =============================================================================== Test 14
@pytest.mark.business
@pytest.mark.parametrize("maker,code", [
    (lambda: b"%PDF-1.4\n1 0 obj\n<< /Broken", "corrupt"),
    (lambda: make_image_only_pdf(), "no_text_layer"),
    (lambda: make_encrypted_pdf(C.CV_TLJ_B), "encrypted"),
    (lambda: b"", "empty"),
    (lambda: b"\x00\x01\x02\x03" * 500, "unsupported_type"),
    (lambda: make_pdf("Jean Fictif, développeur Java, quelques années d'expérience dans la banque et l'assurance."), "insufficient_content"),
    (lambda: "".join(chr(0xFFFD) if i % 3 else "x" for i in range(900)).encode(), "garbled"),
])
def test_business_14_unreadable_documents_fail_explicitly_and_never_produce_a_score(maker, code):
    with pytest.raises(ExtractionError) as e:
        extract_text(maker(), "cv.pdf")
    assert e.value.code == code and e.value.message


def test_readable_formats_extract_exactly_what_the_source_says():
    for data, name in ((make_pdf(C.CV_TLJ_B), "cv.pdf"), (make_docx(C.CV_TLJ_B), "cv.docx"), (C.CV_TLJ_B.encode(), "cv.txt")):
        ex = extract_text(data, name)
        assert "Kafka Connect" in ex.text and ex.pages >= 1
        p = parse_cv(ex.text, today=TODAY)
        assert len(p.experiences) == 2 and p.experiences[0].is_current


def test_long_multipage_pdf_is_supported_and_page_limit_enforced():
    long_cv = C.CV_TLJ_B + "\n".join(f"- Développement de la fonctionnalité numéro {i} en Java avec tests unitaires." for i in range(300))
    ex = extract_text(make_pdf(long_cv), "long.pdf")
    assert ex.pages >= 3
    with pytest.raises(ExtractionError) as e:
        extract_text(make_pdf(long_cv), "long.pdf", max_pages=2)
    assert e.value.code == "too_many_pages"


# =============================================================================== Test 10 (preuves citables)
@pytest.mark.business
def test_business_10_every_claim_has_a_citable_source_excerpt(tlj):
    grid, _ = tlj
    text = C.CV_TLJ_B
    p = parse_cv(text, today=TODAY)
    a = assess(grid, p, today=TODAY)
    n = 0
    for c in a.criteria:
        for e in c.evidence:
            if e["source"] == "cv" and e["location"] in ("experience", "skills_list", "env_list"):
                assert e["excerpt"] in text, f"extrait non verbatim pour {c.label}"
                n += 1
    assert n >= 8
    for c in a.criteria:
        if c.level in (Level.CONFIRMED.value, Level.PARTIAL.value):
            assert c.evidence, f"{c.label} : niveau {c.level} sans preuve citée"


# =============================================================================== autres cas de référence
@pytest.mark.business
def test_case_run_is_not_inferred_from_an_app_deployed_in_production():
    grid, _ = frozen_grid(B.TLJ_TITLE, B.TLJ)
    a = score(grid, C.CV_DEV_NO_RUN)
    assert crit(a, "run_n3").level == Level.NOT_DOCUMENTED.value
    assert crit(score(grid, C.CV_WMS_NOREFLEX), "run_n3").level == Level.CONFIRMED.value


@pytest.mark.business
def test_case_infrastructure_technology_present_is_not_technology_administered():
    grid, _ = frozen_grid(B.INF_TITLE, B.INF)
    env_only = score(grid, C.CV_INFRA_ENV)
    hands_on = score(grid, C.CV_INFRA_HANDS_ON)
    grp_env = next(c for c in env_only.criteria if c.key.startswith("group:"))
    grp_hands = next(c for c in hands_on.criteria if c.key.startswith("group:"))
    assert grp_env.level != Level.CONFIRMED.value, "travailler dans une entreprise qui utilise Huawei ne démontre rien"
    assert grp_hands.level == Level.CONFIRMED.value and hands_on.score_documented > env_only.score_documented
    assert len(grp_hands.members) == 4 and any(m["level"] == Level.CONFIRMED.value for m in grp_hands.members)


@pytest.mark.business
def test_case_data_quality_vs_data_analyst_reporting():
    grid, _ = frozen_grid(B.DQM_TITLE, B.DQM)
    real, reporting = score(grid, C.CV_DQ_REAL), score(grid, C.CV_DA_REPORTING)
    assert crit(real, "data_quality").level == Level.CONFIRMED.value
    assert crit(reporting, "data_quality").level in (Level.NOT_DOCUMENTED.value, Level.DECLARED.value)
    assert real.score_documented > reporting.score_documented + 25
    # aucun malus pour des outils non confirmés comme impératifs
    assert not any(c.label in ("AWS", "Salesforce", "Siebel") for c in real.criteria)


@pytest.mark.business
def test_case_sap_gts_amoa_profile_is_not_presented_as_technical_hands_on():
    grid, _ = frozen_grid(B.GTS_TITLE, B.GTS)
    amoa, cfg = score(grid, C.CV_SAP_AMOA), score(grid, C.CV_SAP_CONFIG)
    assert crit(amoa, "sap_config").level in (Level.DECLARED.value, Level.NOT_DOCUMENTED.value, Level.PARTIAL.value)
    assert crit(cfg, "sap_config").level == Level.CONFIRMED.value
    assert crit(amoa, "sap_gts_e4h").level in (Level.NOT_DOCUMENTED.value, Level.DECLARED.value), "GTS général ≠ pratique d'E4H"
    assert crit(cfg, "sap_gts_e4h").level in (Level.CONFIRMED.value, Level.PARTIAL.value)
    tz = next(c for c in amoa.constraints if c["key"] == "constraint:timezone")
    assert tz["status"] == "inconnu", "le fuseau horaire est une contrainte distincte, jamais confondue avec la compétence"


# =============================================================================== sécurité du moteur
@pytest.mark.business
def test_prompt_injection_and_keyword_stuffing_have_no_effect_on_the_score_and_are_flagged(tlj):
    grid, _ = tlj
    flags = scan_text(C.CV_INJECTION)
    assert {f["type"] for f in flags} >= {"prompt_injection", "keyword_stuffing"}
    a = assess(grid, parse_cv(C.CV_INJECTION, today=TODAY), today=TODAY, security_flags=flags)
    assert a.score_documented < 40 and crit(a, "kafka").level == Level.DECLARED.value
    assert a.security_flags and a.tier != Tier.VERY_INTERESTING.value


# =============================================================================== questions de qualification (§16)
def test_questions_are_specific_personalised_and_structured(tlj):
    grid, _ = tlj
    a = score(grid, C.CV_TLJ_A).to_dict()
    q = qa.generate(a)
    assert len(q["priority"]) == 3 and 0 < len(q["complementary"]) <= 5
    for x in q["priority"] + q["complementary"]:
        if not x["criterion_key"].startswith("constraint:"):      # les questions de contrainte sont volontairement factuelles et courtes
            assert not qa.is_generic(x["text"]), x["text"]
        assert x["criterion_key"] and x["answer_type"] and x["proof_elements"] and x["rationale"]
    kq = next(x for x in q["priority"] if x["criterion_key"].endswith("kafka"))
    assert "Banque Exemple" in kq["text"] and "volumes" in kq["text"], "adaptée au CV particulier du candidat"
    assert any("Connect" in p or "Avro" in p for p in kq["proof_elements"])
    assert q["priority"][0]["rationale"].startswith("IMPÉRATIF")


def test_generic_questions_are_detected():
    for bad in ["Connaissez-vous Java ?", "Avez-vous travaillé en Agile ?", "Es-tu autonome ?", "As-tu déjà utilisé Kafka ?"]:
        assert qa.is_generic(bad)
    assert not qa.is_generic(qa.TEMPLATES["kafka"]["ask"].format(ctx=""))


def test_every_template_is_non_generic():
    for k, t in qa.TEMPLATES.items():
        assert not qa.is_generic(t["ask"].format(ctx="")), k
        assert t["proof"] and t["deepen"], k


def test_no_question_for_what_is_already_demonstrated():
    grid, _ = frozen_grid("Développeur", "Compétences requises\n- Kafka\n- Java\n")
    a = score(grid, C.CV_TLJ_B).to_dict()
    q = qa.generate(a)
    asked = {x["criterion_key"] for x in q["priority"] + q["complementary"]}
    assert not (asked & {c["key"] for c in a["criteria"] if c["level"] == Level.CONFIRMED.value})


# =============================================================================== qualité des informations ≠ adéquation
def test_information_quality_is_separate_from_fit_and_ignores_prose_style():
    grid, _ = frozen_grid("Développeur", "Compétences requises\n- Kafka\n- Java\n")
    plain, fancy = score(grid, C.KAFKA_PLAIN), score(grid, C.KAFKA_FANCY)
    assert plain.information_quality["score"] == fancy.information_quality["score"]
    assert plain.dossier_quality is None and "n'est pas une note d'adéquation" in plain.information_quality["note"]


def test_date_discrepancy_is_reported_not_silently_fixed():
    cv = C.CV_TLJ_A.replace("14 ans d'expérience", "25 ans d'expérience")
    p = parse_cv(cv, today=TODAY)
    assert any("Écart de dates" in f for f in p.flags)
    assert p.declared_years == 25 and p.computed_years and p.computed_years < 20
    cv2 = C.CV_TLJ_A.replace("(Janvier 2016 – Mars 2024)", "(Inconnue)")
    p2 = parse_cv(cv2, today=TODAY)
    assert any(e.dates_unknown for e in p2.experiences), "une date absente reste inconnue, jamais inventée"


# =============================================================================== profondeur avancée : même exigence pour les échanges
@pytest.mark.business
def test_advanced_depth_requirement_applies_to_call_evidence_too(tlj):
    grid, _ = tlj                                      # Kafka exigé en profondeur avancée
    cv = C.CV_TLJ_C_DECLARED                           # Kafka seulement déclaré dans le CV
    basic = "Il a développé un producteur Kafka et un consommateur Kafka sur 2 topics, avec retry."
    advanced = ("Il a mis en place des connecteurs Kafka Connect, conçu les schémas Avro avec Schema Registry "
                "et exploite le cluster Kafka en production.")
    def ext_for(note):
        facts = [f for f in extract_facts(note, "candidate_call_note") if f.topic_key == "skill:kafka"]
        f = facts[0]
        assert f.level == Level.CONFIRMED, "le texte de l'échange est classé confirmé au niveau pratique"
        return {"skill:kafka": [ExtEvidence(id="x", subject_key="skill:kafka", kind=f.evidence_kind, excerpt=f.statement,
                                            source=source_for("candidate_call_note"), reliability=reliability_for("candidate_call_note"),
                                            level=f.level, validated=True)]}
    a_basic = assess(grid, parse_cv(cv, today=TODAY), ext_for(basic), today=TODAY)
    a_adv = assess(grid, parse_cv(cv, today=TODAY), ext_for(advanced), today=TODAY)
    assert crit(a_basic, "kafka").level == Level.PARTIAL.value, "pratique de base décrite en appel ≠ profondeur avancée exigée"
    assert any("profondeur avancée non démontrée" in n for n in crit(a_basic, "kafka").notes)
    assert crit(a_adv, "kafka").level == Level.CONFIRMED.value
