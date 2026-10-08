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
