"""Lot A — exigences, hiérarchie des sources, grille figée (§4, §6.2, §6.3, §5.7)."""
from datetime import date

import pytest

from talentlab.domain import grid as g
from talentlab.domain.brief import analyze_brief
from talentlab.domain.enums import Category, SourceKind
from talentlab.domain.requirements import Req, new_id, resolve
from tests.fixtures import briefs as B


def _req(key="skill:kafka", cat=Category.IMPERATIF, src=SourceKind.OFFICIAL_BRIEF, d=None, **kw):
    return Req(id=new_id(), key=key, label=key.split(":")[1], category=cat, source_kind=src, source_date=d, validated=True,
               quote="extrait", **kw)


# ------------------------------------------------------------------ classification du brief
def test_technology_in_a_list_is_never_eliminatory():
    a = analyze_brief(B.TLJ_TITLE, B.TLJ)
    assert a.requirements, "des exigences doivent être proposées"
    assert all(r.category != Category.ELIMINATOIRE for r in a.requirements)
    assert all(not r.validated for r in a.requirements), "le système propose, le recruteur valide"


def test_eliminatory_only_when_text_says_so_and_group_is_modelled():
    a = analyze_brief(B.INF_TITLE, B.INF)
    grp = next(r for r in a.requirements if r.key.startswith("group:"))
    assert grp.category == Category.ELIMINATOIRE
    assert grp.source_kind == SourceKind.CLIENT_CONFIRMED_IMPERATIVE
    assert grp.params["at_least"] == 2
    assert sorted(grp.params["members"]) == ["skill:dell_emc", "skill:huawei", "skill:rubrik", "skill:san"], \
        "les gammes d'un même constructeur ne comptent pas comme autant de technologies distinctes"
    dell = next(r for r in a.requirements if r.key == "skill:dell_emc")
    assert any("PowerStore" in t for t in dell.terms) and any("Unity" in t for t in dell.terms)
    members = [r for r in a.requirements if r.params.get("group") == grp.key]
    assert members and all(m.category == Category.DIFFERENCIANT for m in members), \
        "chaque techno du groupe n'est pas impérative isolément : seule la combinaison l'est"


def test_negated_necessity_becomes_contextual_not_scored():
    a = analyze_brief(B.DSF_TITLE, B.DSF)
    ecom = next(r for r in a.requirements if r.key == "domain:retail_ecom")
    assert ecom.category == Category.CONTEXTUEL


def test_design_system_conception_requires_advanced_depth():
    a = analyze_brief(B.DSF_TITLE, B.DSF)
    ds = next(r for r in a.requirements if r.key == "skill:design_system")
    assert ds.depth_required == "advanced"


def test_missing_information_is_a_question_never_invented():
    a = analyze_brief("Consultant X", "Compétences requises\n- Python\n")
    assert a.modalities == {}
    fields = {m["field"] for m in a.missing_info}
    assert {"tjm_max", "start", "duration", "city"} <= fields


def test_modalities_extracted_exactly():
    a = analyze_brief(B.TLJ_TITLE, B.TLJ)
    assert a.modalities["onsite_days_per_week"] == 3
    assert a.modalities["city"] == "Châtillon"
    assert a.modalities["astreinte"] is True
    assert a.modalities["team_size_managed"] == 3
    assert a.modalities["start"] == "ASAP"


def test_title_alone_is_not_enough_title_vs_work_warning():
    a = analyze_brief(B.DQM_TITLE, B.DQM)
    assert any("Data Quality" in w for w in a.warnings), "un titre « Data Analyst » cache un besoin de Data Quality"
    assert {x["key"] for x in a.activities} >= {"data_quality", "reconciliation"}


def test_french_word_est_is_not_a_timezone():
    a = analyze_brief(B.INF_TITLE, B.INF)
    assert "timezone_constraint" not in a.modalities
    b = analyze_brief(B.GTS_TITLE, B.GTS)
    assert b.modalities.get("timezone_constraint") is True


# ------------------------------------------------------------------ hiérarchie des sources (§4.3)
def test_recent_clarification_does_not_erase_older_confirmed_imperative():
    old_imp = _req(cat=Category.IMPERATIF, src=SourceKind.CLIENT_CONFIRMED_IMPERATIVE, d=date(2026, 1, 5))
    new_clar = _req(cat=Category.SOUHAITABLE, src=SourceKind.CLIENT_CLARIFICATION, d=date(2026, 9, 1))
    eff, conflicts = resolve([old_imp, new_clar])
    assert [r.id for r in eff] == [old_imp.id]
    assert conflicts and conflicts[0].kept == old_imp.id


def test_explicit_supersession_by_client_authority_replaces_the_requirement():
    old_imp = _req(cat=Category.IMPERATIF, src=SourceKind.CLIENT_CONFIRMED_IMPERATIVE, d=date(2026, 1, 5))
    new_clar = _req(cat=Category.SOUHAITABLE, src=SourceKind.CLIENT_CLARIFICATION, d=date(2026, 9, 1), supersedes=old_imp.id)
    eff, _ = resolve([old_imp, new_clar])
    assert [r.id for r in eff] == [new_clar.id]


def test_non_client_source_cannot_supersede_even_if_it_claims_to():
    brief = _req(cat=Category.IMPERATIF, src=SourceKind.OFFICIAL_BRIEF, d=date(2026, 1, 5))
    desc = _req(cat=Category.SOUHAITABLE, src=SourceKind.INITIAL_DESCRIPTION, d=date(2026, 9, 1), supersedes=brief.id)
    eff, conflicts = resolve([brief, desc])
    assert [r.id for r in eff] == [brief.id]
    assert conflicts[0].needs_review, "une source moins prioritaire mais plus récente doit être arbitrée par un humain"


def test_clarification_outranks_official_brief():
    brief = _req(cat=Category.SOUHAITABLE, src=SourceKind.OFFICIAL_BRIEF, d=date(2026, 1, 5))
    clar = _req(cat=Category.IMPERATIF, src=SourceKind.CLIENT_CLARIFICATION, d=date(2026, 6, 1))
    eff, _ = resolve([brief, clar])
    assert eff[0].category == Category.IMPERATIF


# ------------------------------------------------------------------ grille
def _validated(reqs):
    for r in reqs:
        r.validated = True
    return reqs


def test_proposed_grid_sums_to_100_and_excludes_constraints_and_context():
    a = analyze_brief(B.TLJ_TITLE, B.TLJ)
    reqs = _validated(a.requirements)
    grid = g.propose_grid("m1", reqs)
    assert grid.total_weight() == 100
    assert all(c.dimension != "contrainte" for c in grid.scored())
    assert all(c.category not in ("contextuel", "a_clarifier") for c in grid.scored())
    assert grid.scored()


def test_grid_is_mission_specific_not_universal():
    ga = g.propose_grid("m1", _validated(analyze_brief(B.TLJ_TITLE, B.TLJ).requirements))
    gb = g.propose_grid("m2", _validated(analyze_brief(B.DQM_TITLE, B.DQM).requirements))
    assert {c.key for c in ga.scored()} != {c.key for c in gb.scored()}


def test_cannot_freeze_unvalidated_requirements():
    reqs = analyze_brief(B.TLJ_TITLE, B.TLJ).requirements  # non validées
    grid = g.propose_grid("m1", reqs)
    with pytest.raises(g.GridError) as e:
        g.freeze(grid, reqs, "TM")
    assert any("validée" in m for m in e.value.errors)


def test_cannot_freeze_with_weights_not_100():
    reqs = _validated(analyze_brief(B.TLJ_TITLE, B.TLJ).requirements)
    grid = g.propose_grid("m1", reqs)
    grid.scored()[0].weight += 7
    with pytest.raises(g.GridError) as e:
        g.freeze(grid, reqs, "TM")
    assert any("100" in m for m in e.value.errors)


def test_imperative_requirement_cannot_be_dropped_from_grid():
    reqs = _validated(analyze_brief(B.TLJ_TITLE, B.TLJ).requirements)
    grid = g.propose_grid("m1", reqs)
    victim = next(c for c in grid.criteria if c.key == "skill:kafka")
    grid.criteria.remove(victim)
    errs, _ = g.validate(grid, reqs)
    assert any("Kafka" in m and "retir" in m for m in errs), "§5.7 : on ne supprime pas un impératif parce que peu de profils l'ont"


def test_eliminatory_without_traced_client_confirmation_is_refused():
    r = _req(cat=Category.ELIMINATOIRE, src=SourceKind.OFFICIAL_BRIEF)
    grid = g.propose_grid("m1", [r, _req("skill:java")])
    errs, _ = g.validate(grid, [r, _req("skill:java")])
    assert any("confirmation client" in m for m in errs)


def test_unresolved_clarifications_block_freeze_unless_acknowledged():
    base = _validated([_req("skill:java"), _req("skill:sql", Category.DIFFERENCIANT)])
    unk = _req("skill:docker", Category.A_CLARIFIER)
    reqs = base + [unk]
    grid = g.propose_grid("m1", reqs)
    with pytest.raises(g.GridError):
        g.freeze(grid, reqs, "TM")
    frozen = g.freeze(grid, reqs, "TM", allow_unresolved_clarifications=True)
    assert frozen.status == "frozen"


def test_frozen_grid_is_tamper_evident_and_new_version_requires_reason():
    reqs = _validated(analyze_brief(B.TLJ_TITLE, B.TLJ).requirements)
    grid = g.freeze(g.propose_grid("m1", reqs), reqs, "Bailey", allow_unresolved_clarifications=True)
    g.assert_unchanged(grid)
    grid.scored()[0].weight += 1                       # altération
    with pytest.raises(g.GridError):
        g.assert_unchanged(grid)
    grid.scored()[0].weight -= 1
    g.assert_unchanged(grid)
    with pytest.raises(g.GridError):
        g.new_version(grid, reqs, "  ")
    v2, d = g.new_version(grid, reqs, "Le client précise que Kafka Connect est impératif")
    assert v2.version == 2 and v2.status == "draft" and d["to_version"] == 2


def test_group_criterion_scores_via_group_members_unscored():
    reqs = _validated(analyze_brief(B.INF_TITLE, B.INF).requirements)
    grid = g.propose_grid("m1", reqs)
    grp = next(c for c in grid.criteria if c.key.startswith("group:"))
    assert grp.scored and grp.params["at_least"] == 2
    assert all(not c.scored for c in grid.criteria if c.params.get("group"))
    assert grid.total_weight() == 100
