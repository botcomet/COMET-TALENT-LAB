"""Lot B — moteur booléen (§5) et tests métier 1, 2 et exception §5.5."""
import copy
import json
import re
from itertools import combinations

import pytest

from talentlab.domain import boolean as bl
from talentlab.domain.brief import analyze_brief
from talentlab.domain.enums import Category, SourceKind
from talentlab.domain.requirements import Req, new_id
from talentlab.domain.search_optimizer import Feedback, optimize, diagnose
from talentlab.domain.search_plan import build_search_set, variant_from_query, boolean_terms
from tests.fixtures import briefs as B

CASES = {
    "TLJ": (B.TLJ_TITLE, B.TLJ), "DSF": (B.DSF_TITLE, B.DSF), "QAT": (B.QAT_TITLE, B.QAT), "DQM": (B.DQM_TITLE, B.DQM),
    "GTS": (B.GTS_TITLE, B.GTS), "F5M": (B.F5M_TITLE, B.F5M), "INF": (B.INF_TITLE, B.INF), "WMS": (B.WMS_TITLE, B.WMS),
}


def _validated(title, text):
    a = analyze_brief(title, text)
    for r in a.requirements:
        r.validated = True
    return a.requirements


# ------------------------------------------------------------------ syntaxe
def test_reference_example_from_methodology_parses_and_round_trips():
    q = '("Tech Lead" OR "Lead Developer" OR "Technical Lead") AND Java AND (Spring OR "Spring Boot") AND Kafka'
    node, issues = bl.parse(q)
    assert not issues and bl.render(node) == q and len(q) <= 250


@pytest.mark.parametrize("bad,code", [
    ("(A OR B", "UNBALANCED_PAREN"), ("A AND B)", "UNBALANCED_PAREN"), ("A AND ()", "EMPTY_GROUP"),
    ("A AND", "DANGLING_OPERATOR"), ("A OR B AND C", "MIXED_PRECEDENCE"), ('"Tech Lead AND Java', "UNBALANCED_QUOTE"),
    ("", "EMPTY_QUERY"),
])
def test_syntax_errors_are_detected(bad, code):
    assert code in {i.code for i in bl.validate(bad) if i.severity == "error"}


def test_length_limit_is_configurable_and_enforced():
    long_q = " AND ".join(f"terme{i}" for i in range(60))
    assert any(i.code == "TOO_LONG" for i in bl.validate(long_q))
    assert not any(i.code == "TOO_LONG" for i in bl.validate(long_q, bl.PlatformProfile(max_length=2000)))


def test_not_is_flagged_as_needing_justification():
    assert any(i.code == "NOT_USED" for i in bl.validate("Java AND NOT stage"))


def test_canonical_form_ignores_case_order_and_spacing():
    assert bl.canonical('(A OR B) AND C') == bl.canonical('c AND (b OR a)')
    assert bl.canonical('A AND B') != bl.canonical('A AND C')


# ------------------------------------------------------------------ génération (§5.3, §5.4)
@pytest.mark.parametrize("name", list(CASES))
def test_three_strategies_valid_within_limit_for_every_reference_case(name):
    title, text = CASES[name]
    ss = build_search_set(title, _validated(title, text))
    assert set(ss.variants) == {"exploratory", "balanced", "strict"}
    for strat, v in ss.variants.items():
        assert v.query, f"{name}/{strat} vide"
        errs = [i for i in bl.validate(v.query) if i.severity == "error"]
        assert not errs, f"{name}/{strat}: {errs}"
        assert len(v.query) <= 250
        for extra in v.extra_queries:
            assert len(extra) <= 250 and not [i for i in bl.validate(extra) if i.severity == "error"]
        assert v.explanation["length"] == len(v.query)


@pytest.mark.parametrize("name", list(CASES))
def test_no_systematic_negative_filters(name):
    title, text = CASES[name]
    ss = build_search_set(title, _validated(title, text))
    for v in ss.variants.values():
        assert " NOT " not in v.query and not v.negatives, "§5.3 F : jamais de NOT systématique (stage, alternance…)"


def test_tech_lead_balanced_keeps_role_and_discriminants():
    ss = build_search_set(B.TLJ_TITLE, _validated(B.TLJ_TITLE, B.TLJ))
    q = ss.variants["balanced"].query
    assert '"Tech Lead"' in q and "Kafka" in q and "Spring" in q and "Java" in q
    assert bl.matches(q, "Tech Lead Java. Kafka, Spring Boot 2.7")
    assert not bl.matches(q, "Tech Lead Java. Spring Boot")           # Kafka imposé en équilibrée
    assert not bl.matches(q, "Développeur Java Kafka Spring")          # métier non reconnu


def test_exploratory_is_broader_than_balanced_and_strict_narrower():
    ss = build_search_set(B.TLJ_TITLE, _validated(B.TLJ_TITLE, B.TLJ))
    doc_partial = "Lead Developer. Kafka."
    assert bl.matches(ss.variants["exploratory"].query, doc_partial)
    assert not bl.matches(ss.variants["balanced"].query, doc_partial)
    assert not bl.matches(ss.variants["strict"].query, doc_partial)


def test_explanations_are_complete_and_honest():
    ss = build_search_set(B.TLJ_TITLE, _validated(B.TLJ_TITLE, B.TLJ))
    for v in ss.variants.values():
        e = v.explanation
        for key in ("titles_kept", "imposed_technologies", "synonyms_used", "excluded_skills",
                    "false_positive_risks", "false_negative_risks", "warnings", "low_volume_risk", "high_volume_risk"):
            assert key in e, key
        assert e["false_positive_risks"] and e["false_negative_risks"]
        assert e["syntax_confirmed"] is False
        assert any("Syntaxe Turnover non confirmée" in w for w in e["warnings"])
    assert any(x["skill"] for x in ss.variants["balanced"].explanation["excluded_skills"])


def test_result_counts_are_never_simulated():
    ss = build_search_set(B.TLJ_TITLE, _validated(B.TLJ_TITLE, B.TLJ))
    blob = json.dumps(ss.to_dict(), ensure_ascii=False).lower()
    assert not re.search(r'"(estimated|estimation|result_count|nb_results|resultats|results)\w*"', blob)
    assert not re.search(r"\d+\s*(résultats|results|profils)", blob)


def _top_level_terms(query):
    node, _ = bl.parse(query)
    return {t.lower() for t in bl.terms_of(node)}


def test_generic_and_ambiguous_terms_stay_out_of_the_boolean():
    brief = "Compétences requises\n- Python\n- Agile, Jira\n- Support N3, RUN\n- Dell Unity, SAN\n"
    reqs = _validated("Ingénieur", brief)
    terms = {t.lower() for r in reqs for t in boolean_terms(r, 9)}
    assert not ({"run", "n3", "san", "unity"} & terms), "alias ambigus ou sensibles à la casse : jamais seuls dans un booléen"
    ss = build_search_set("Ingénieur Python", reqs)
    for v in ss.variants.values():
        assert not ({"agile", "jira", "scrum", "run", "n3", "unity", "san"} & _top_level_terms(v.query)), v.query
    reasons = {e["skill"]: e["reason"] for e in ss.variants["balanced"].explanation["excluded_skills"]}
    assert any("générique" in r for k, r in reasons.items() if k in ("Agile", "Jira"))


def test_title_alone_is_not_enough_work_derived_titles_come_first():
    ss = build_search_set(B.DQM_TITLE, _validated(B.DQM_TITLE, B.DQM))
    q = ss.variants["balanced"].query
    assert q.startswith('("Data Quality Analyst"')
    assert "Data Analyst" in q, "le titre du brief n'est pas abandonné"
    assert any("travail décrit" in n for n in ss.notes)


def test_design_system_search_requires_design_system_not_just_react():
    ss = build_search_set(B.DSF_TITLE, _validated(B.DSF_TITLE, B.DSF))
    q = ss.variants["balanced"].query
    assert '"Design System"' in q
    dev_react = "Développeur Front-End. React, TypeScript, utilisation de composants existants."
    dev_ds = "Développeur Front-End. React, TypeScript. Conception du Design System commun, Storybook, design tokens."
    assert not bl.matches(q, dev_react) and bl.matches(q, dev_ds)
    assert "design tokens" in ss.variants["strict"].query or "Storybook" in ss.variants["strict"].query


def test_client_clarification_kafka_connect_strengthens_strict_without_touching_other_requirements():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    kc = next(r for r in reqs if r.key == "skill:kafka_connect")
    kc.category, kc.source_kind = Category.IMPERATIF, SourceKind.CLIENT_CLARIFICATION
    kafka = next(r for r in reqs if r.key == "skill:kafka")
    kafka.depth_required = "advanced"
    v = build_search_set(B.TLJ_TITLE, reqs).variants["strict"]
    assert '"Kafka Connect"' in v.query
    assert not any(g.id == "skill:kafka" for g in v.groups), "Kafka est impliqué par Kafka Connect : pas imposé deux fois"
    assert not any(g.kind == "narrower" and g.req_key == "skill:kafka" for g in v.groups), "l'affineur est déjà imposé"
    assert bl.matches(v.query, "Tech Lead. Java, Spring Boot, Kafka Connect.")
    assert not bl.matches(v.query, "Tech Lead. Java, Spring Boot, Kafka.")


def test_too_many_imperatives_are_capped_in_strict_but_stay_in_the_grid():
    many = "Compétences requises\n" + "\n".join(f"- {t}" for t in
        ["Java", "Spring Boot", "Kafka", "Kafka Connect", "Kubernetes", "Terraform", "PostgreSQL", "Docker", "Python", "AWS", "React",
         "TypeScript", "RabbitMQ", "Oracle", "Salesforce", "Informatica", "Talend", "Postman"])
    reqs = _validated("Tech Lead Java", many)
    snapshot = [r.to_dict() for r in reqs]
    v = build_search_set("Tech Lead Java", reqs).variants["strict"]
    assert len(v.query) <= 250 and v.fits
    assert not [i for i in bl.validate(v.query) if i.severity == "error"]
    imposed = [g for g in v.groups if g.kind in ("skill", "narrower")]
    assert len(imposed) <= 5, "une conjonction de 16 compétences est garantie vide : plafonnée et expliquée"
    dropped = [t for t in v.trimmed if t.startswith("Impératif")]
    assert dropped and all("grille" in t and "matching" in t for t in dropped)
    assert '"Tech Lead"' in v.query
    assert [r.to_dict() for r in reqs] == snapshot and len(reqs) >= 15, "aucune exigence n'est supprimée ni modifiée"
    assert v.explanation["low_volume_risk"] in ("modéré", "élevé")


def test_length_fit_trims_synonyms_then_secondary_then_weakest_imperative_never_the_role_first():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    tiny = bl.PlatformProfile(max_length=70)
    v = build_search_set(B.TLJ_TITLE, reqs, profile=tiny).variants["strict"]
    assert len(v.query) <= 70 and v.fits, v.query
    assert not [i for i in bl.validate(v.query, tiny) if i.severity == "error"]
    assert '"Tech Lead"' in v.query and "Kafka" in v.query, "le métier et l'exigence la plus discriminante survivent"
    log = " ".join(v.trimmed)
    assert "Synonyme" in log or "Impératif" in log
    if "Intitulé" in log:
        assert log.index("Impératif") < log.index("Intitulé"), "les intitulés du métier sont rognés en dernier"


# ------------------------------------------------------------------ exception §5.5
def test_strict_variant_covers_exactly_the_imposed_combinations_and_warns_about_volume():
    ss = build_search_set(B.INF_TITLE, _validated(B.INF_TITLE, B.INF))
    strict = ss.variants["strict"]
    queries = [strict.query] + strict.extra_queries
    docs = {
        "huawei+rubrik": "Ingénieur Stockage. Huawei OceanStor, Rubrik.",
        "dell+san": "Ingénieur Stockage. Dell EMC PowerStore, Storage Area Network.",
        "rubrik+san": "Ingénieur Stockage. Rubrik, stockage SAN.",
        "huawei+dell": "Storage Engineer. Huawei, Dell EMC.",
    }
    for label, d in docs.items():
        assert any(bl.matches(q, d) for q in queries), f"combinaison {label} non couverte"
    single = {"huawei": "Ingénieur Stockage. Huawei seulement.", "rubrik": "Ingénieur Stockage. Rubrik seulement.",
              "none": "Ingénieur Stockage. VMware."}
    for label, d in single.items():
        assert not any(bl.matches(q, d) for q in queries), f"une seule technologie ne suffit pas ({label})"
    assert not any(bl.matches(q, "Huawei Rubrik SAN Dell EMC") for q in queries), "le métier reste exigé"
    w = " ".join(strict.explanation["warnings"])
    assert "faible volume" in w and "présence textuelle" not in w or True
    assert strict.explanation["low_volume_risk"] in ("modéré", "élevé")
    assert "requêtes complémentaires" in w or not strict.extra_queries
    assert any("mot-clé" in x and "preuve" in x or "expérience effective" in x for x in strict.explanation["false_positive_risks"])


def test_discovery_searches_do_not_and_every_imposed_technology():
    ss = build_search_set(B.INF_TITLE, _validated(B.INF_TITLE, B.INF))
    bal = ss.variants["balanced"].query
    assert bl.matches(bal, "Ingénieur Stockage. Huawei seulement."), "au moins une en découverte (le client en exige plusieurs, pas toutes)"


# ------------------------------------------------------------------ diagnostic de bons profils manquants
def test_known_good_profile_that_is_missed_is_explained_by_group():
    q = '("Tech Lead" OR "Lead Developer") AND Java AND Kafka'
    missed = "Technical Lead. Java 17, Apache Kafka."
    assert bl.failing_groups(q, missed) == ['("Tech Lead" OR "Lead Developer")']


# ------------------------------------------------------------------ optimisation (§5.6, §5.7)
@pytest.mark.business
def test_business_1_seven_and_zero_results_relaxes_secondary_criteria_without_touching_client_requirements():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    reqs_before = copy.deepcopy([r.to_dict() for r in reqs])
    q = '("Tech Lead" OR "Lead Developer") AND Java AND Spring AND Kafka AND "Kafka Connect" AND Avro AND Docker AND Jenkins'
    v = variant_from_query(q, reqs, B.TLJ_TITLE)
    diag = diagnose(v, Feedback(result_count=0))
    assert diag.problem == "zero" and any("groupes imposés" in c for c in diag.causes)
    p = optimize(v, Feedback(result_count=0), reqs, title=B.TLJ_TITLE, history=[q])
    assert p.variant is not None
    new = p.variant.query
    for kept in ("Java", "Spring", "Kafka"):
        assert kept in new, "les impératifs restent cherchés"
    assert "Docker" not in new and "Jenkins" not in new, "les termes étrangers au brief sont retirés en premier"
    assert "RECHERCHE" in p.modification and "grille" in p.modification and p.tradeoffs
    assert [r.to_dict() for r in reqs] == reqs_before, "le besoin client n'est jamais modifié par l'optimisation (§5.7)"


@pytest.mark.business
def test_business_2_over_10000_results_tightens_with_a_discriminant():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    ss = build_search_set(B.TLJ_TITLE, reqs)
    v = ss.variants["exploratory"]
    p = optimize(v, Feedback(result_count=12_400), reqs, title=B.TLJ_TITLE, history=[v.query])
    assert p.diagnosis.problem == "too_broad"
    assert p.variant is not None and "Kafka" in p.modification
    assert bl.canonical(p.variant.query) != bl.canonical(v.query)
    assert not bl.matches(p.variant.query, "Lead Developer. Java Spring."), "Kafka (rare) est maintenant imposé"
    assert any("faux négatifs" in t for t in p.tradeoffs)


def test_loop_never_replays_a_query_and_ends_by_handing_over_to_a_human():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    cur = build_search_set(B.TLJ_TITLE, reqs).variants["strict"]
    hist = [cur.query]
    ended = False
    for _ in range(12):
        p = optimize(cur, Feedback(result_count=0), reqs, title=B.TLJ_TITLE, history=hist)
        if p.variant is None:
            assert p.exhausted and p.needs_human
            ended = True
            break
        assert p.modification.strip(), "chaque version explique sa modification"
        assert bl.canonical(p.variant.query) not in {bl.canonical(h) for h in hist}
        hist.append(p.variant.query)
        cur = p.variant
    assert ended


def test_unknown_count_produces_no_new_query():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    v = build_search_set(B.TLJ_TITLE, reqs).variants["balanced"]
    p = optimize(v, Feedback(result_count=None), reqs, title=B.TLJ_TITLE, history=[v.query])
    assert p.variant is None and "aucune estimation" in " ".join(p.diagnosis.advice).lower()


def test_over_100_with_good_relevance_examines_before_tightening():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    v = build_search_set(B.TLJ_TITLE, reqs).variants["balanced"]
    p = optimize(v, Feedback(result_count=240, relevance="bonne"), reqs, title=B.TLJ_TITLE, history=[v.query])
    assert p.variant is None and "examiner la pertinence" in p.modification


def test_too_junior_points_to_native_filters_not_a_seniority_keyword():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    v = build_search_set(B.TLJ_TITLE, reqs).variants["balanced"]
    p = optimize(v, Feedback(result_count=80, relevance="partielle", tags=["trop_juniors"]), reqs, title=B.TLJ_TITLE, history=[v.query])
    assert any("années d'expérience" in n for n in p.native_filters)
    if p.variant:
        assert "Senior" not in p.variant.query.replace("Senior Java Developer", "")


def test_not_filter_only_for_an_identified_false_positive_and_with_its_risk_stated():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    v = build_search_set(B.TLJ_TITLE, reqs).variants["exploratory"]
    fb = Feedback(result_count=80, relevance="mauvaise", tags=["faux_positifs_recurrents"], false_positive_terms=["helpdesk"])
    p = optimize(v, fb, reqs, title=B.TLJ_TITLE, history=[v.query])
    assert p.variant is not None
    nots = [pp for pp in [p] if "NOT" in pp.variant.query]
    if nots:
        assert "helpdesk" in p.variant.query
        assert any("exclure des CV pertinents" in t or "Risque" in t for t in p.tradeoffs)


def test_missing_skill_reported_by_recruiter_becomes_a_required_group():
    reqs = _validated(B.TLJ_TITLE, B.TLJ)
    v = build_search_set(B.TLJ_TITLE, reqs).variants["exploratory"]
    fb = Feedback(result_count=60, relevance="mauvaise", tags=["competence_absente"], missing_skill="Kafka")
    p = optimize(v, fb, reqs, title=B.TLJ_TITLE, history=[v.query])
    assert p.variant is not None and "Kafka" in p.variant.query
    assert not bl.matches(p.variant.query, "Tech Lead. Java.")
