"""Banc adverse du moteur de preuves : formulations réalistes qui ne doivent PAS être créditées comme une pratique (négation, futur, tiers,
formation, projet personnel, contexte administratif, listes déguisées), et cas TÉMOINS qui doivent rester crédités (pas de sur-correction).

Origine : revue méthodologique indépendante (8 oct. 2026). Chaque ligne reproduit une phrase qui obtenait « confirmé » à tort.
"""
import pytest

from talentlab.domain.enums import Level
from tests.fixtures import briefs as B, cvs as C
from tests.helpers import crit, frozen_grid, score

HDR = "EXPÉRIENCES PROFESSIONNELLES\n\nSociété Exemple — Développeur Java (Mars 2018 – en cours)\n"
ORDER = {Level.NOT_DOCUMENTED.value: 0, Level.DECLARED.value: 1, Level.PARTIAL.value: 2, Level.CONFIRMED.value: 3}
ND, DE, PA, CO = Level.NOT_DOCUMENTED.value, Level.DECLARED.value, Level.PARTIAL.value, Level.CONFIRMED.value


@pytest.fixture(scope="module")
def grids():
    return {"TLJ": frozen_grid(B.TLJ_TITLE, B.TLJ)[0], "WMS": frozen_grid(B.WMS_TITLE, B.WMS)[0],
            "INF": frozen_grid(B.INF_TITLE, B.INF)[0], "GTS": frozen_grid(B.GTS_TITLE, B.GTS)[0]}


def level_of(grids, brief, cv, key):
    a = score(grids[brief], cv)
    return crit(a, key).level


# (brief, critère, texte de la puce, niveau MAXIMAL admis, pourquoi)
CANNOT_EXCEED = [
    # --- négation : l'absence déclarée n'est jamais un crédit
    ("TLJ", "kafka", "- Je n'ai jamais configuré de cluster Kafka ni développé de producer.", ND, "négation"),
    ("TLJ", "kafka", "- Sans expérience Kafka mais souhaite développer des producers et consumers sur des topics.", ND, "négation + intention"),
    ("TLJ", "kafka", "- I have never configured a Kafka cluster nor developed a producer.", ND, "négation EN"),
    ("TLJ", "kafka", "- No prior experience with Kafka, keen to build producers and consumers.", ND, "négation EN"),
    ("TLJ", "run_n3", "- Développement de services Java. Pas d'astreinte, pas de support N3, pas de gestion des incidents : l'exploitation était assurée par une autre équipe.", ND, "RUN nié"),
    # --- futur, intention, apprentissage
    ("TLJ", "kafka", "- Migration vers Kafka planifiée pour 2027 : conception des producers, consumers et topics à venir.", DE, "futur"),
    ("TLJ", "kafka", "- Maintained Java services. Interested in Kafka (producers, consumers, topics).", DE, "intérêt"),
    ("TLJ", "kafka", "- Développement d'un module de facturation en Java ; connaissance de Kafka.", DE, "mention sans verbe dans sa proposition"),
    # --- tiers
    ("TLJ", "kafka", "- Collaboration avec l'équipe plateforme qui administre le cluster Kafka (brokers, partitions, topics).", DE, "tiers"),
    ("TLJ", "kafka", "- Développement d'une application de facturation, Kafka côté plateforme (non utilisé directement).\nEnvironnement technique : Java, Kafka", DE, "tiers + négation postposée"),
    # --- formation, personnel, famille
    ("TLJ", "kafka", "- Formation Kafka (3 jours) : développement de producers et consumers sur 4 topics en TP.", DE, "formation"),
    ("TLJ", "kafka", "- Projet personnel : conception de producers et consumers Kafka (4 topics, partitions) sur Raspberry.", DE, "projet personnel"),
    ("TLJ", "retail_ecom", "- Développement d'un blog. Par ailleurs, la société est un acteur e-commerce majeur avec commandes, paiement, stock, catalogue.", DE, "description de l'entreprise"),
    # --- participation exprimée de différentes façons
    ("TLJ", "kafka", "- Impliqué dans la conception de la plateforme Kafka (12 topics, partitions, brokers, consumer groups).", PA, "participation FR"),
    ("TLJ", "kafka", "- Part of the team that designed the Kafka platform (12 topics, partitions, brokers, consumer groups).", PA, "participation EN"),
    # --- indices comptés deux fois (pluriel) ou nus
    ("TLJ", "kafka", "- Développement Kafka avec topics et partitions.", PA, "topic/topics ne sont qu'un indice"),
    ("TLJ", "run_n3", "- Astreintes. Incidents.", DE, "deux noms sans verbe"),
    ("TLJ", "run_n3", "- Incidents, tickets.", DE, "deux noms sans verbe"),
    # --- corroboration : une technologie non courante ne devient pas « confirmée » par sa seule présence dans l'environnement
    ("TLJ", "kafka", "- Développement d'une application de facturation utilisant Kafka.\nEnvironnement technique : Java, Kafka", PA, "corroboration non transférable à une technologie rare"),
]


@pytest.mark.parametrize("brief,key,bullet,maxlvl,why", CANNOT_EXCEED, ids=[f"{c[4]}-{i}" for i, c in enumerate(CANNOT_EXCEED)])
def test_phrase_is_never_credited_above_the_cap(grids, brief, key, bullet, maxlvl, why):
    cv = ("Consultant WMS\n\n" if brief == "WMS" else "") + HDR + bullet
    got = level_of(grids, brief, cv, key)
    assert ORDER[got] <= ORDER[maxlvl], f"{why} : « {bullet[:70]} » → {got} (plafond {maxlvl})"


def test_wms_explicit_absence_of_the_imposed_product_is_not_compensated_by_a_neighbour_product(grids):
    cv = "Consultant WMS\n\n" + HDR + "- Pas d'expérience sur Reflex WMS ; j'ai paramétré Manhattan WMS : réception, picking, expédition, inventaire."
    a = score(grids["WMS"], cv)
    assert crit(a, "reflex_wms").level == ND
    assert crit(a, "reflex_wms").contradictions and crit(a, "reflex_wms").contradictions[0]["type"] == "absence_declaree_cv"
    assert a.tier != "tres_interessant"


# cas témoins : ce qui est réellement décrit DOIT rester crédité (pas de sur-correction)
MUST_CREDIT = [
    ("TLJ", "kafka", "- Created Kafka producers and consumers on 12 topics with 3 partitions each.", PA),
    ("TLJ", "kafka", "- Déploiement de producers et consumers Kafka sur 12 topics avec 3 partitions chacun.", PA),
    ("TLJ", "kafka", "- Designed and supported Kafka consumers (12 topics, partitions) in production.", CO),
    ("TLJ", "kafka", "- Sans interruption de service, mise en place d'un cluster Kafka (12 topics, 3 partitions, 6 brokers).", CO),
    ("TLJ", "kafka", "- Je n'ai jamais eu de problème en production : exploitation d'un cluster Kafka de 6 brokers, 12 topics, partitions répliquées.", PA),
    ("TLJ", "kafka", "- Mise en œuvre de Kafka : conception des topics et des partitions, producers et consumers Java, 6 brokers.", CO),
    ("TLJ", "kafka", "- Animation de formations Kafka : conception et déploiement d'un cluster pédagogique de 3 brokers.", PA),
]


@pytest.mark.parametrize("brief,key,bullet,minlvl", MUST_CREDIT, ids=[f"temoin-{i}" for i in range(len(MUST_CREDIT))])
def test_control_phrases_that_describe_real_work_stay_credited(grids, brief, key, bullet, minlvl):
    got = level_of(grids, brief, HDR + bullet, key)
    assert ORDER[got] >= ORDER[minlvl], f"sur-correction : « {bullet[:70]} » → {got} (attendu ≥ {minlvl})"


# ---------------------------------------------------------------- C1 : sans titre d'expérience reconnu, une page de compétences n'est pas une expérience
def test_skills_page_without_experience_section_is_declared_not_demonstrated(grids):
    cv = "Ingénieur stockage\n\nCOMPÉTENCES\nAdministration et configuration : Huawei OceanStor, Dell EMC PowerStore, Rubrik, SAN (zoning, LUN, réplication)\n"
    a = score(grids["INF"], cv)
    assert a.tier != "tres_interessant" and a.score_documented < 50
    grp = next(c for c in a.criteria if c.key.startswith("group:"))
    assert grp.level in (DE, PA) and all(m["level"] in (DE, ND) for m in grp.members)


def test_unknown_heading_with_a_category_label_enumeration_is_not_a_realisation(grids):
    cv = "Ingénieur stockage\n\nHISTORIQUE\nAdministration et configuration : Huawei OceanStor, Dell EMC PowerStore, Rubrik, SAN (zoning, LUN, réplication)\n"
    a = score(grids["INF"], cv)
    assert a.tier != "tres_interessant" and a.score_documented < 50


def test_realisation_followed_by_a_detail_list_is_still_a_realisation(grids):
    """Témoin : « Conception du Design System… : tokens, variants… » n'est pas une liste de compétences."""
    g = frozen_grid(B.DSF_TITLE, B.DSF)[0]
    assert crit(score(g, C.CV_DS_B), "design_system").level == CO


# ---------------------------------------------------------------- C4 : une mention récente dans l'environnement ne rafraîchit pas une pratique ancienne
def test_recent_environment_mention_does_not_refresh_an_old_practice(grids):
    old = score(grids["TLJ"], C.CV_KAFKA_OLD)
    refreshed = score(grids["TLJ"], C.CV_KAFKA_OLD.replace("Environnement technique : Python, PostgreSQL, Docker", "Environnement technique : Python, PostgreSQL, Docker, Kafka"))
    assert crit(old, "kafka").level == PA
    assert crit(refreshed, "kafka").level == PA, "la mention d'un environnement récent ne prouve pas une pratique récente"
    assert crit(refreshed, "kafka").last_used != "en cours"


# ---------------------------------------------------------------- E2 / E4 : audit de contrat, support utilisateur, projet familial
@pytest.mark.parametrize("bullet", [
    "- Audit de contrat de maintenance Huawei et Dell EMC : analyse des coûts, rédaction de rapports, 12 baies.",
    "- Support des utilisateurs sur un environnement Huawei, Dell EMC PowerStore, Rubrik ; traitement des tickets.",
])
def test_infrastructure_products_in_administrative_or_user_support_context_are_declared_only(grids, bullet):
    a = score(grids["INF"], "Ingénieur\n\n" + HDR + bullet)
    grp = next(c for c in a.criteria if c.key.startswith("group:"))
    assert all(m["level"] in (DE, ND) for m in grp.members), grp.members
    assert a.tier != "tres_interessant"


def test_family_site_is_not_enterprise_experience(grids):
    got = level_of(grids, "TLJ", HDR + "- Création du site e-commerce de ma famille : catalogue, panier, paiement, commandes, promotions.", "retail_ecom")
    assert ORDER[got] <= ORDER[DE]


# ---------------------------------------------------------------- E7 : version exigée
def test_required_java_version_is_not_matched_by_a_far_older_version_only(grids):
    old = "Vieux\n\nEXPÉRIENCES\n\nAncienne — Dév (Mars 2010 – Décembre 2016)\n- Développement en Java 6 avec 4 développeurs.\nEnvironnement technique : Java 6"
    new = "Récent\n\nEXPÉRIENCES\n\nActuelle — Dév (Mars 2018 – en cours)\n- Développement en Java 17 avec 4 développeurs, microservices et tests unitaires.\nEnvironnement technique : Java 8, Java 17"
    a, b = score(grids["TLJ"], old), score(grids["TLJ"], new)
    assert ORDER[crit(a, "java").level] <= ORDER[PA] and any("Version exigée" in n for n in crit(a, "java").notes)
    assert ORDER[crit(b, "java").level] >= ORDER[PA] and not any("Version exigée" in n for n in crit(b, "java").notes)


# ================================================================ C5 : faits d'appel — fausses contradictions, limites perdues, exigence client prise pour une compétence
from talentlab.domain.call_facts import extract_facts
from talentlab.domain.enums import EvidenceKind


def _kinds(text, topic="skill:kafka", kind="candidate_call_note"):
    return [f.evidence_kind for f in extract_facts(text, kind) if f.kind == "candidate_experience" and f.topic_key == topic]


@pytest.mark.parametrize("note", [
    "Je n'ai jamais eu de problème avec Kafka en production.",
    "Je n'ai pas eu de difficulté à déployer Kafka.",
    "Je n'ai jamais rencontré d'incident majeur sur Kafka.",
])
def test_a_negated_problem_is_good_news_never_a_contradiction(note):
    ks = _kinds(note)
    assert ks and EvidenceKind.CONTRADICTS not in ks and EvidenceKind.LIMITS not in ks


@pytest.mark.parametrize("note", [
    "Je n'ai jamais administré le cluster Kafka, j'étais développeur sur les producers.",
    "Je n'ai jamais utilisé Kafka Connect ni Avro, uniquement des producers et consumers Kafka.",
])
def test_negating_a_component_of_a_technology_limits_it_but_never_contradicts_it(note):
    ks = _kinds(note)
    assert ks and EvidenceKind.CONTRADICTS not in ks


def test_a_negated_component_next_to_an_affirmed_technology_does_not_cancel_it():
    ks = _kinds("Je n'ai pas travaillé sur Kafka Streams, mais sur Kafka oui, 6 brokers.")
    assert ks == [EvidenceKind.SUPPORTS]


def test_direct_absence_is_extracted_as_a_contradiction_that_still_needs_human_validation(grids):
    assert _kinds("Je n'ai jamais utilisé Kafka.") == [EvidenceKind.CONTRADICTS]
    # le plafond « écart majeur » n'est jamais appliqué sur une interprétation non validée (voir test_matching_business)


@pytest.mark.parametrize("note", [
    "I only used Kafka on two topics.",
    "Mon expérience Kafka se limite à deux topics.",
    "Je n'ai fait de Kafka que sur 2 topics, rien de plus.",
    "Kafka : uniquement 2 topics, jamais de cluster.",
    "J'ai juste touché à Kafka sur un petit projet.",
])
def test_limits_are_recognised_in_many_formulations_fr_and_en(note):
    assert EvidenceKind.LIMITS in _kinds(note)


def test_field_labels_are_not_speakers():
    fs = extract_facts("TJM : 650 € / Disponibilité : immédiate", "candidate_call_note")
    got = {(f.constraint["field"], f.constraint["value"]) for f in fs if f.kind == "candidate_constraint"}
    assert ("tjm", 650) in got and ("availability", "immediate") in got
    fs = extract_facts("Kafka : uniquement 2 topics, jamais de cluster.", "candidate_call_note")
    assert any(f.kind == "candidate_experience" and f.evidence_kind == EvidenceKind.LIMITS for f in fs), "« Kafka » n'est pas un locuteur"


@pytest.mark.parametrize("note,kind", [
    ("Client veut Kafka Connect et Avro (indispensable).", "candidate_call_note"),
    ("Speaker 1 : Le client veut absolument Kafka Connect", "transcript"),
    ("Le client exige Kafka Connect, c'est obligatoire.", "candidate_call_note"),
])
def test_an_unlabelled_client_requirement_is_never_a_candidate_skill(note, kind):
    fs = extract_facts(note, kind)
    assert any(f.kind == "client_requirement" for f in fs)
    assert not any(f.kind == "candidate_experience" for f in fs), [f.to_dict() for f in fs if f.kind == "candidate_experience"]


# ================================================================ E1 : les impératifs ne se compensent pas, y compris dans le classement
def test_ranking_puts_confirmed_imperatives_before_a_higher_score_with_an_open_imperative(grids):
    from talentlab.domain.scoring import compare
    a_cv = C.CV_TLJ_B.replace("Kafka Connect", "RabbitMQ").replace("Kafka", "RabbitMQ").replace("Avro", "JSON")      # tout, sauf Kafka (impératif)
    b_cv = ("Dev Fictif\nDéveloppeur Java\n\nEXPÉRIENCES\n\nBoite Exemple — Développeur Java (Mars 2019 – en cours)\n"
            "- Conception et développement de services Java 17 et Spring Boot ; mise en place d'un cluster Kafka de 6 brokers, conception de topics et partitions, 40 000 messages par seconde.\n"
            "Environnement technique : Java 17, Spring Boot, Kafka\n")
    a, b = score(grids["TLJ"], a_cv), score(grids["TLJ"], b_cv)
    assert a.score_documented > b.score_documented and a.open_mandatory and not b.open_mandatory, "précondition : A score plus haut mais a un impératif ouvert"
    assert compare({"A": a, "B": b})["ranking"] == ["B", "A"]
    assert compare({"A": a, "B": b})["ranking"] == compare({"B": b, "A": a})["ranking"], "indépendant de l'ordre d'entrée"


# ================================================================ E6 : volumétrie — portée, durée, unités
@pytest.fixture(scope="module")
def vol_grids():
    from talentlab.domain.enums import Category

    def scoped(rs):
        for r in rs:
            if r.key == "domain:high_volume":
                r.category, r.scope_terms = Category.IMPERATIF, ["kafka"]

    def unscoped(rs):
        for r in rs:
            if r.key == "domain:high_volume":
                r.category = Category.IMPERATIF
    return frozen_grid(B.TLJ_TITLE, B.TLJ, scoped)[0], frozen_grid(B.TLJ_TITLE, B.TLJ, unscoped)[0]


@pytest.mark.parametrize("bullet,maxlvl", [
    ("- Intégration d'un flux Kafka (50 messages par jour) au SI bancaire qui traite plusieurs millions de transactions par jour.", DE),
    ("- Mise en place de Kafka : 120 000 messages.", DE),                                     # un total, pas un débit
    ("- Mise en place de Kafka avec 30 millions de transactions par an.", PA),                # ≈ 82 000 / jour : moyenne, pas forte
])
def test_volume_is_attributed_to_its_own_clause_and_needs_a_duration(vol_grids, bullet, maxlvl):
    got = crit(score(vol_grids[0], HDR + bullet), "high_volume").level
    assert ORDER[got] <= ORDER[maxlvl], f"{bullet} → {got}"


@pytest.mark.parametrize("bullet", [
    "- Mise en place de Kafka : 5,000 transactions per second.",
    "- Mise en place de Kafka : 10 000 requests per minute.",
    "- Mise en place de Kafka : débit soutenu de 40 000 messages par seconde.",
])
def test_real_high_throughput_in_english_and_french_formats_is_still_recognised(vol_grids, bullet):
    assert crit(score(vol_grids[0], HDR + bullet), "high_volume").level == CO


def test_a_very_long_digit_string_does_not_stall_the_analysis(vol_grids):
    import time
    t0 = time.perf_counter()
    score(vol_grids[0], HDR + "- Plateforme Kafka : " + "1" * 16000 + " messages par jour.")
    assert time.perf_counter() - t0 < 3, "complexité linéaire attendue (le temps quadratique bloquait 35 s sur 16 000 chiffres)"


# ================================================================ M2 : un CV hostile ne bloque pas l'analyse (budget de temps par CV)
@pytest.mark.parametrize("name,payload", [
    ("espaces", "- Kafka" + " " * 100_000 + "Java"),
    ("espaces + nombre", "- Kafka 5" + " " * 100_000 + "messages"),
    ("chiffres", "- Kafka " + "1" * 16_000 + " messages par jour"),
    ("répétition", "- " + "Kafka, " * 20_000),
    ("mots répétés", "- " + "Développement Kafka topics partitions " * 5_000),
    ("négations répétées", "- n'ai jamais " + "ni a " * 20_000),
    ("ligne sans espace", "- " + "a" * 200_000),
    ("tirets", "- Kafka " + "-" * 50_000 + " Java"),
])
def test_hostile_input_stays_within_a_time_budget(grids, name, payload):
    import time
    t0 = time.perf_counter()
    a = score(grids["TLJ"], HDR + payload)
    assert time.perf_counter() - t0 < 6, f"{name} : analyse trop lente"
    assert 0 <= a.score_documented <= 100


def test_text_beyond_the_length_limit_is_refused_with_an_explicit_reason():
    from talentlab.domain.cv_extract import ExtractionError, extract_text
    huge = ("Développeur Java — Kafka, Spring Boot. " * 6000).encode()
    with pytest.raises(ExtractionError) as e:
        extract_text(huge, "gros.txt", max_chars=100_000)
    assert e.value.code == "too_long"


# ================================================================ M1 : dates — plages plausibles seulement, formats courants lus
from datetime import date as _date

from talentlab.domain.cv_extract import parse_cv
from tests.helpers import TODAY


def _exps(body_line, header="Soc — Dév (Mars 2018 – Mars 2022)"):
    cv = f"Dev\n\nEXPÉRIENCES\n\n{header}\n- {body_line}\nEnvironnement : Kafka\n\nAutre — Dév (Janvier 2012 – Février 2018)\n- x\n"
    p = parse_cv(cv, today=TODAY)
    return [(e.start, e.end) for e in p.experiences], p.flags


@pytest.mark.parametrize("line", [
    "Débit de 5000 - 8000 messages par seconde",
    "Budget 1500 - 2000 euros",
    "Migration (2019 - 2020) vers Kafka",           # période de projet dans une expérience plus large
])
def test_a_quantity_or_project_period_never_opens_a_phantom_experience(line):
    exps, _ = _exps(line)
    assert exps == [("2018-03", "2022-03"), ("2012-01", "2018-02")]


def test_implausible_year_ranges_are_ignored_and_reported_not_computed():
    exps, flags = _exps("Janvier 2030 – Décembre 2038")
    assert exps == [("2018-03", "2022-03"), ("2012-01", "2018-02")] and any("invraisemblable" in f for f in flags)
    p = parse_cv("Dev\n\nEXPÉRIENCES\n\nSoc — Dév (1990 – 2095)\n- travail\n", today=TODAY)
    assert p.computed_years is None and any("invraisemblable" in f for f in p.flags)


@pytest.mark.parametrize("header,start,end,current", [
    ("Soc — Dév (03.2019 – 05.2021)", "2019-03", "2021-05", False),
    ("Soc — Dév (2019/03 – 2021/05)", "2019-03", "2021-05", False),
    ("Soc — Dév (mars 2019 – maintenant)", "2019-03", None, True),
    ("Soc — Dév (Mars 2019 – en cours)", "2019-03", None, True),
])
def test_common_date_formats_are_read(header, start, end, current):
    p = parse_cv(f"Dev\n\nEXPÉRIENCES\n\n{header}\n- travail\n", today=TODAY)
    e = p.experiences[0]
    assert (e.start, e.end, e.is_current) == (start, end, current)


def test_a_phantom_range_no_longer_inflates_years_of_experience(grids):
    cv = HDR + "- Kafka : débit de 5000 - 8000 messages par seconde, conception des topics et partitions.\n"
    a = score(grids["TLJ"], cv)
    assert not any("3000" in str(c.justification) for c in a.criteria)


# ================================================================ E8 : homonymes
@pytest.mark.parametrize("brief,key,bullet", [
    ("INF", "group", "- Développement d'un jeu vidéo en Unity (C#) : stockage des scores, sauvegarde cloud des parties."),
    ("INF", "group", "- ADMINISTRATION DES SERVEURS DE JEU, VOYAGE A SAN FRANCISCO, CONFIGURATION DES POSTES."),
    ("TLJ", "spring", "- Livraison de la release Spring 2021 de l'API data de reporting (développement, tests, mise en production)."),
    ("WMS", "reflex_wms", "- Développement d'applications web avec le framework Python Reflex ; gestion de stock des composants UI ; mise en production."),
])
def test_homonyms_are_not_mistaken_for_the_requested_product(grids, brief, key, bullet):
    # en-tête neutre : le garde-fou d'homonymie est une fenêtre lexicale (un en-tête « Java » à proximité suffirait à valider « Spring »)
    hdr = "EXPÉRIENCES PROFESSIONNELLES\n\nSociété Exemple — Analyste (Mars 2018 – en cours)\n"
    a = score(grids[brief], "Consultant\n\n" + hdr + bullet)
    c = next(x for x in a.criteria if x.key.startswith("group:")) if key == "group" else crit(a, key)
    assert c.level == ND, f"homonyme crédité : {c.level} — {c.justification[:100]}"


def test_a_real_unity_array_still_counts_for_dell_emc(grids):
    a = score(grids["INF"], "Ingénieur\n\n" + HDR + "- Administration des baies Dell EMC Unity : provisioning des LUN, réplication, zoning SAN, 12 baies.")
    grp = next(c for c in a.criteria if c.key.startswith("group:"))
    assert any(m["label"] == "Dell EMC" and m["level"] in (PA, CO) for m in grp.members)


# ================================================================ M3 : sous-crédits flagrants (profils anglophones, formulations courantes)
@pytest.mark.parametrize("brief,key,bullet,minlvl", [
    ("TLJ", "tech_mgmt", "- Led a team of 4 developers: code reviews, mentoring and sprint planning.", CO),
    ("TLJ", "kafka", "- Streaming temps réel avec Kafka Streams : agrégations sur 5 topics, 100 000 événements par jour.", PA),
    ("TLJ", "retail_ecom", "- Développement d'un site marchand : catalogue, panier, paiement, commandes, promotions.", CO),
    ("TLJ", "retail_ecom", "- Développement d'une boutique en ligne omnicanale : catalogue, panier, paiement, commandes, promotions.", CO),
    ("TLJ", "retail_ecom", "- Développement d'une plateforme e‑commerce : catalogue, panier, paiement, commandes, promotions.", CO),   # trait d'union insécable
    ("TLJ", "kafka", "- Operated a Confluent Platform cluster: 12 topics, 3 partitions each, 6 brokers.", PA),
    ("GTS", "sap_config", "- Configuration de SAP GTS Edition for HANA (E4H) : customs management, compliance management, sanctioned party list screening, 5 pays.", CO),
])
def test_real_work_in_common_formulations_is_credited(grids, brief, key, bullet, minlvl):
    cv = ("Consultant\n\nEXPÉRIENCES\n\nCabinet — Consultant SAP GTS (Janvier 2022 – en cours)\n" if brief == "GTS" else HDR) + bullet
    got = crit(score(grids[brief], cv), key).level
    assert ORDER[got] >= ORDER[minlvl], f"sous-crédit : {bullet[:60]} → {got}"


# ================================================================ DoS : espaces Unicode, budget de temps, récursion
@pytest.mark.parametrize("name,fn", [
    ("brief tjm + U+2003", lambda: __import__("talentlab.domain.brief", fromlist=["x"]).analyze_brief("Tech Lead Java", "tjm" + " " * 480 + "x")),
    ("langues + U+2003", lambda: parse_cv("Dev\nanglais" + " " * 20_000 + "x", today=TODAY)),
    ("note tjm + espaces", lambda: __import__("talentlab.domain.call_facts", fromlist=["x"]).extract_facts("tjm" + " " * 40_000 + "x", "candidate_call_note")),
    ("note tjm + U+2003", lambda: __import__("talentlab.domain.call_facts", fromlist=["x"]).extract_facts("tjm" + " " * 40_000 + "x", "candidate_call_note")),
])
def test_long_runs_of_unicode_whitespace_never_cause_quadratic_time(name, fn):
    import time
    t0 = time.perf_counter()
    fn()
    assert time.perf_counter() - t0 < 3, name


def test_deeply_nested_parentheses_are_a_validation_error_not_a_crash():
    from talentlab.domain import boolean as bl
    issues = bl.validate("(" * 20_000 + "a" + ")" * 20_000)
    assert any(i.code == "TOO_DEEP" and i.severity == "error" for i in issues)


def test_assessment_stops_with_a_timeout_when_its_budget_is_exhausted(grids):
    import time
    from talentlab.domain.evidence import AnalysisTimeout
    from talentlab.domain.scoring import assess
    with pytest.raises(AnalysisTimeout):
        assess(grids["TLJ"], parse_cv(C.CV_TLJ_B, today=TODAY), today=TODAY, deadline=time.monotonic() - 1)


def test_fold_turns_every_unicode_space_into_a_plain_space_without_changing_length():
    from talentlab.domain.text import fold, fold_compact
    t = "a b c d\te　f"
    assert fold(t) == "a b c d e f" and len(fold(t)) == len(t) and fold_compact("a" + " " * 50 + "b") == "a b"


# ================================================================ export : nettoyage des coordonnées (revue de sécurité)
@pytest.mark.parametrize("raw", [
    "Contact: camille.fictif@example.invalid", "Mail : camille [at] example [dot] invalid", "Tel +33 6 12 34 56 78", "Tel 06.12.34.56.78", "Tel 0612345678",
    "Tel (+33)6 12 34 56 78", "Tel +44 7911 123456", "Tel 0033 6 12 34 56 78", "Tel +33 (0)6 12 34 56 78", "linkedin: linkedin.com/in/camille-fictif", "github.com/camillefictif",
])
def test_scrub_removes_direct_contact_details_in_common_formats(raw):
    from talentlab.domain.safety import has_contact_details, scrub
    out = scrub(raw)
    assert "[" in out and has_contact_details(raw)
    assert not any(ch.isdigit() for ch in out.replace("33", "").replace("44", "")) or "retiré" in out
    assert "camille" not in out.lower().replace("camille fictif", "") or "retiré" in out


@pytest.mark.parametrize("ok", [
    "Débit de 40 000 messages par seconde sur 6 brokers", "Entre 2019 et 2023, puis 2024-2026", "Volume de 100 000 000 messages par jour", "Budget 650 € / jour",
    "Kafka 3.4.1 et Java 17.0.2 en 2022", "Du 2019-03-12 au 2020-03-12", "ISO 27001:2022 et RFC 7231",
])
def test_scrub_leaves_technical_figures_and_dates_alone(ok):
    from talentlab.domain.safety import has_contact_details, scrub
    assert scrub(ok) == ok and not has_contact_details(ok)
