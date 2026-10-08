"""Moteur de scoring explicable (§6).

Le score n'est JAMAIS généré par un modèle : il est calculé à partir de la grille
figée de la mission, du niveau de preuve de chaque critère et de règles de
plafonnement validées. Même grille + mêmes preuves ⇒ même score (test 8).

score documenté = Σ poids × facteur(niveau de preuve), plafonné seulement en cas
d'absence EXPLICITE d'une exigence impérative (jamais sur simple « non documenté »).
Le « potentiel » est une borne haute à confirmer, jamais une compétence acquise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import grid as gd
from .cv_extract import ParsedCV
from .enums import ConstraintStatus, Level, LEVEL_ORDER, Tier
from .evidence import CritEvidence, EvalConfig, ExtEvidence, evaluate_text_criterion, merge_external, skill_for

ENGINE_VERSION = "scoring-1.0"


@dataclass
class CandidateFacts:
    """Informations sur les contraintes du candidat — uniquement ce qui a été réellement établi (jamais supposé)."""
    availability: str | None = None            # "immediate" | date ISO | None = inconnu
    tjm: int | None = None
    onsite_max_days: int | None = None
    refuses: set[str] = field(default_factory=set)        # {"onsite", "astreinte", "travel"}
    accepts_astreinte: bool | None = None
    city: str | None = None
    timezone_ok: bool | None = None
    english_level: str | None = None


_LANG_ORDER = ["notions", "scolaire", "intermédiaire", "professionnel", "courant", "bilingue"]


@dataclass
class CriterionResult:
    key: str
    label: str
    category: str
    dimension: str
    weight: int
    scored: bool
    level: str
    factor: float
    points: float
    justification: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    advanced_found: list[str] = field(default_factory=list)
    advanced_missing: list[str] = field(default_factory=list)
    last_used: str | None = None
    members: list[dict[str, Any]] = field(default_factory=list)
    mandatory: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Assessment:
    engine: str
    grid_version: int
    grid_hash: str
    score_documented: float
    score_potential: float
    score_displayed: int
    caps_applied: list[dict[str, Any]]
    tier: str
    uncertainty: dict[str, Any]
    criteria: list[CriterionResult]
    constraints: list[dict[str, Any]]
    alerts: list[dict[str, str]]
    covered: list[str]
    partial: list[str]
    not_demonstrated: list[str]
    contradicted: list[str]
    open_mandatory: list[str]
    summary: str
    information_quality: dict[str, Any]
    dossier_quality: None = None             # §6.10 : évalué ailleurs (DT Editor) ; jamais mêlé au score d'adéquation
    flags: list[str] = field(default_factory=list)
    security_flags: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["criteria"] = [c.to_dict() for c in self.criteria]
        return d


# ----------------------------------------------------------------- évaluation par critère
def _eval_crit(c: gd.Criterion, parsed: ParsedCV, ext: dict[str, list[ExtEvidence]], cfg: EvalConfig) -> CritEvidence:
    if c.kind == "years":
        return _eval_years(c, parsed, ext, cfg)
    kind = {"skill": "skill", "activity": "activity", "domain": "domain"}.get(c.kind, "skill")
    ev = evaluate_text_criterion(parsed, c.key, c.label, c.terms, kind=kind, depth_required=c.depth_required, scope_terms=c.scope_terms,
                                 recency_sensitive=c.recency_sensitive, recency_window_years=c.recency_window_years, cfg=cfg)
    return merge_external(ev, ext.get(c.key, []), c.label)


def _eval_years(c: gd.Criterion, parsed: ParsedCV, ext: dict[str, list[ExtEvidence]], cfg: EvalConfig) -> CritEvidence:
    need = c.min_years or 0
    if c.terms:      # ancienneté sur une compétence : missions réellement exercées sur cette compétence
        sub = evaluate_text_criterion(parsed, c.key.replace("years:", "skill:"), c.label, c.terms, kind="skill", cfg=cfg)
        exps = [parsed.experiences[i] for i in sub.experience_idxs if i < len(parsed.experiences)]
        basis = "missions où la compétence est réellement pratiquée"
    else:
        exps = [e for e in parsed.experiences if e.months is not None]
        basis = "total des missions datées du CV (pertinence de chaque mission à confirmer)"
    from .cv_extract import _ord, _end_ord
    iv = sorted(((_ord(e.start, 7), _end_ord(e, cfg.today)) for e in exps if e.months is not None and e.start))
    merged: list[list[int]] = []
    for s, e in iv:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    years = round(sum(e - s for s, e in merged) / 12, 1)
    undated = [e for e in (exps if c.terms else parsed.experiences) if e.dates_unknown]
    if not iv:
        return CritEvidence(Level.NOT_DOCUMENTED, [], f"Durée non établissable : aucune période datée exploitable ({basis}).",
                            open_questions=[f"Quelle est ta durée cumulée d'expérience {('sur ' + c.label) if c.terms else 'pertinente'} (dates précises) ?"])
    ratio = years / need if need else 1.0
    level = Level.CONFIRMED if ratio >= 1 else (Level.PARTIAL if ratio >= 0.6 else Level.DECLARED)
    ev = CritEvidence(level, [], f"{years} an(s) calculés sur les périodes datées ({basis}) pour {need:g} an(s) demandés.",
                      notes=[f"Précision des dates : mois/année ; durée recalculée, pas reprise du texte du CV.", ] + ([f"{len(undated)} expérience(s) sans date non comptée(s)."] if undated else []))
    ev.months_practice = int(years * 12)
    return ev


def _group_level(c: gd.Criterion, members: list[tuple[gd.Criterion, CritEvidence]], factors: dict[str, float]) -> tuple[Level, float, str]:
    k = int(c.params.get("at_least", 1))
    cnt = {lv: sum(1 for _, e in members if e.level == lv) for lv in Level}
    alive = len(members) - cnt[Level.CONTRADICTED]
    eff = cnt[Level.CONFIRMED] * factors[Level.CONFIRMED.value] + cnt[Level.PARTIAL] * factors[Level.PARTIAL.value] + cnt[Level.DECLARED] * factors[Level.DECLARED.value]
    factor = min(1.0, eff / k) if k else 1.0
    if alive < k:
        lvl = Level.CONTRADICTED
    elif cnt[Level.CONFIRMED] >= k:
        lvl = Level.CONFIRMED
    elif cnt[Level.CONFIRMED] + cnt[Level.PARTIAL] >= 1:
        lvl = Level.PARTIAL
    elif cnt[Level.DECLARED] >= 1:
        lvl = Level.DECLARED
    else:
        lvl = Level.NOT_DOCUMENTED
    detail = ", ".join(f"{m.label} : {e.level.value.replace('_', ' ')}" for m, e in members)
    return lvl, factor, f"{cnt[Level.CONFIRMED]} technologie(s) démontrée(s) sur {k} exigée(s) parmi {len(members)} ({detail})."


# ----------------------------------------------------------------- contraintes (jamais mêlées au score technique)
def _lang_ge(have: str | None, need: str | None) -> bool | None:
    if not have or not need:
        return None
    h = _LANG_ORDER.index(have) if have in _LANG_ORDER else None
    n = next((i for i, l in enumerate(_LANG_ORDER) if l in need or need in l), None)
    if h is None or n is None:
        return None
    return h >= n


def evaluate_constraints(grid: gd.Grid, facts: CandidateFacts, parsed: ParsedCV | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    en = facts.english_level or (parsed.languages.get("anglais") if parsed else None)
    for c in grid.constraints():
        st, why, q = ConstraintStatus.UNKNOWN, "Information non établie : à demander (jamais supposée).", ""
        p = c.params
        if c.key == "constraint:presence":
            need = p.get("onsite_days")
            if "onsite" in facts.refuses:
                st, why = ConstraintStatus.INCOMPATIBLE, "Refus explicite d'une présence sur site."
            elif facts.onsite_max_days is not None and need is not None:
                st, why = ((ConstraintStatus.COMPATIBLE, f"Accepte {facts.onsite_max_days} j/semaine sur site (≥ {need} demandés).")
                           if facts.onsite_max_days >= need else
                           (ConstraintStatus.INCOMPATIBLE, f"Plafonne la présence à {facts.onsite_max_days} j/semaine pour {need} demandés."))
            else:
                q = f"Quel rythme de présence sur site acceptes-tu ({need} j/semaine{' à ' + p['city'] if p.get('city') else ''} sont demandés) ?"
            if st != ConstraintStatus.INCOMPATIBLE and p.get("city") and facts.city and facts.city.lower() != str(p["city"]).lower():
                st, why = ConstraintStatus.TO_VALIDATE, f"Localisation du candidat : {facts.city} ; mission à {p['city']} : mobilité à valider."
        elif c.key == "constraint:astreinte":
            if "astreinte" in facts.refuses or facts.accepts_astreinte is False:
                st, why = ConstraintStatus.INCOMPATIBLE, "Refus explicite des astreintes."
            elif facts.accepts_astreinte:
                st, why = ConstraintStatus.COMPATIBLE, "Accepte les astreintes."
            else:
                q = "Des astreintes sont prévues : les acceptes-tu, et à quelle fréquence maximale ?"
        elif c.key == "constraint:tjm":
            mx = p.get("tjm_max")
            if facts.tjm is not None and mx is not None:
                st, why = ((ConstraintStatus.COMPATIBLE, f"TJM {facts.tjm} € ≤ budget {mx} €.") if facts.tjm <= mx
                           else (ConstraintStatus.TO_VALIDATE, f"TJM {facts.tjm} € au-dessus du budget {mx} € : négociation à valider."))
            else:
                q = "Quel est ton TJM cible pour cette mission ?"
        elif c.key == "constraint:timezone":
            if facts.timezone_ok is True:
                st, why = ConstraintStatus.COMPATIBLE, "Compatible avec le fuseau horaire demandé."
            elif facts.timezone_ok is False:
                st, why = ConstraintStatus.INCOMPATIBLE, "Fuseau horaire incompatible (contrainte opérationnelle, sans lien avec le niveau de compétence)."
            else:
                q = "Le client travaille dans un autre fuseau horaire : quelles plages horaires peux-tu assurer ?"
        elif c.key.startswith("language:"):
            ok = _lang_ge(en, str(p.get("level", "")))
            if ok is True:
                st, why = ConstraintStatus.COMPATIBLE, f"Niveau d'anglais déclaré : {en}."
            elif ok is False:
                st, why = ConstraintStatus.TO_VALIDATE, f"Niveau d'anglais déclaré ({en}) inférieur au niveau demandé ({p.get('level')}) : à valider en échange."
            else:
                q = "Quel est ton niveau d'anglais réel à l'oral (réunions, ateliers) ?"
        out.append({"key": c.key, "label": c.label, "mandatory": c.mandatory, "status": st.value, "explanation": why, "question": q})
    # disponibilité : toujours évaluée, jamais supposée (test 5)
    av = facts.availability
    out.append({"key": "constraint:availability", "label": "Disponibilité", "mandatory": False,
                "status": ConstraintStatus.UNKNOWN.value if not av else ConstraintStatus.COMPATIBLE.value,
                "explanation": "Disponibilité non renseignée : inconnue, pas indisponible." if not av else f"Disponibilité : {av}.",
                "question": "" if av else "À partir de quelle date es-tu disponible, et quel est ton préavis éventuel ?"})
    return out


# ----------------------------------------------------------------- évaluation complète
def assess(grid: gd.Grid, parsed: ParsedCV, ext: dict[str, list[ExtEvidence]] | None = None, facts: CandidateFacts | None = None,
           *, today: date | None = None, security_flags: list[dict[str, Any]] | None = None) -> Assessment:
    gd.assert_unchanged(grid)
    if grid.status != "frozen":
        raise gd.GridError(["Une évaluation exige une grille figée et validée par un recruteur (critères identiques pour tous les candidats)."])
    ext = ext or {}
    facts = facts or CandidateFacts()
    cfg = EvalConfig(today=today or date.today(), recency_window_years=grid.recency_window_years)
    ev_by_key: dict[str, CritEvidence] = {}
    for c in grid.criteria:
        if c.members or c.key.startswith("group:") or c.dimension == "contrainte" or c.kind in ("language", "constraint"):
            continue
        ev_by_key[c.key] = _eval_crit(c, parsed, ext, cfg)
    results: list[CriterionResult] = []
    factors = grid.factors
    for c in grid.scored():
        members_out: list[dict[str, Any]] = []
        if c.members:
            ms = [(grid.by_key(m), ev_by_key[m]) for m in c.members if grid.by_key(m) and m in ev_by_key]
            lvl, factor, just = _group_level(c, ms, factors)
            members_out = [{"key": m.key, "label": m.label, "level": e.level.value, "justification": e.justification,
                            "evidence": [i.to_dict() for i in e.items[:2]]} for m, e in ms]
            e = CritEvidence(lvl, [], just)
            ev = e
        else:
            ev = ev_by_key.get(c.key) or CritEvidence(Level.NOT_DOCUMENTED, [], "Critère non évalué automatiquement.")
            if c.kind == "years" and ev.level != Level.NOT_DOCUMENTED and c.min_years:
                import re as _re
                m = _re.match(r"([\d.]+) an", ev.justification)
                factor = min(1.0, (float(m.group(1)) / c.min_years)) if m else factors[ev.level.value]
            else:
                factor = factors[ev.level.value]
        points = round(c.weight * factor, 2)
        qs = list(ev.open_questions)
        results.append(CriterionResult(
            key=c.key, label=c.label, category=c.category, dimension=c.dimension, weight=c.weight, scored=True, level=ev.level.value,
            factor=round(factor, 3), points=points, justification=ev.justification, evidence=[i.to_dict() for i in ev.items],
            notes=ev.notes, contradictions=ev.contradictions, open_questions=qs, advanced_found=ev.advanced_found,
            advanced_missing=ev.advanced_missing, last_used=ev.last_used, members=members_out, mandatory=c.mandatory))

    documented = round(sum(r.points for r in results), 2)
    # potentiel : ce que vaudrait le profil si ses éléments PARTIELS ou DÉCLARÉS se confirmaient. Un critère « non documenté » n'a aucun indice :
    # il ne relève pas le potentiel (sinon tout candidat afficherait 100). Une contradiction reste à zéro.
    potential = round(sum(r.weight * (1.0 if r.level in (Level.CONFIRMED.value, Level.PARTIAL.value, Level.DECLARED.value) else 0.0) if r.level not in (Level.NOT_DOCUMENTED.value, Level.CONTRADICTED.value) else 0.0 for r in results), 2)
    # ---- plafonds : uniquement sur ABSENCE EXPLICITE d'une exigence impérative (§6.8)
    caps: list[dict[str, Any]] = []
    score = documented
    for r in results:
        if r.level != Level.CONTRADICTED.value:
            if r.mandatory and r.level == Level.NOT_DOCUMENTED.value and grid.caps.get("undocumented_imperative") is not None:
                cap = int(grid.caps["undocumented_imperative"])
                caps.append({"criterion": r.label, "cap": cap, "reason": "Impératif non documenté (plafond configuré par le recruteur)."})
            continue
        if r.category == "eliminatoire_confirme":
            cap = grid.caps.get("contradicted_eliminatory")
        elif r.mandatory:
            cap = grid.caps.get("contradicted_imperative")
        else:
            cap = None
        if cap is not None:
            caps.append({"criterion": r.label, "cap": int(cap), "reason": f"« {r.label} » explicitement absent ou contredit : l'exigence n'est pas compensable par d'autres critères."})
    for cp in caps:
        score = min(score, cp["cap"])
    potential = min(potential, min([cp["cap"] for cp in caps], default=100)) if caps else potential

    contradicted = [r.label for r in results if r.level == Level.CONTRADICTED.value]
    open_mand = [r.label for r in results if r.mandatory and r.level not in (Level.CONFIRMED.value, Level.CONTRADICTED.value)]
    missing_mand = [r.label for r in results if r.mandatory and r.level == Level.NOT_DOCUMENTED.value]
    th = grid.thresholds
    contradicted_mand = [r.label for r in results if r.mandatory and r.level == Level.CONTRADICTED.value]
    if contradicted_mand:
        tier = Tier.MAJOR_GAP
    elif open_mand:
        tier = Tier.LOW_FIT if potential < th["low_fit"] else Tier.TO_QUALIFY
    elif score >= th["very_interesting"]:
        tier = Tier.VERY_INTERESTING
    elif score >= th["interesting"]:
        tier = Tier.INTERESTING
    elif score >= th["low_fit"]:
        tier = Tier.TO_QUALIFY
    else:
        tier = Tier.LOW_FIT
    alerts: list[dict[str, str]] = []
    for name in contradicted_mand:
        alerts.append({"severity": "bloquant", "message": f"Écart majeur : « {name} » est une exigence impérative explicitement non satisfaite."})
    for name in missing_mand:
        alerts.append({"severity": "majeur", "message": f"Manque majeur à qualifier : « {name} » (impératif) n'est documenté nulle part — ne pas conclure sans question dédiée."})
    for name in [n for n in open_mand if n not in missing_mand]:
        alerts.append({"severity": "à vérifier", "message": f"Impératif « {name} » non confirmé : à qualifier avant tout positionnement."})
    for cp in caps:
        alerts.append({"severity": "plafond", "message": f"Score plafonné à {cp['cap']} : {cp['reason']}"})
    cons = evaluate_constraints(grid, facts, parsed)
    for k in cons:
        if k["status"] == ConstraintStatus.INCOMPATIBLE.value:
            alerts.append({"severity": "contrainte", "message": f"{k['label']} : {k['explanation']} (distinct de l'adéquation technique)."})
    # ---- incertitude
    unsure_w = sum(r.weight for r in results if r.level in (Level.PARTIAL.value, Level.DECLARED.value, Level.NOT_DOCUMENTED.value))
    pct = round(unsure_w / max(1, sum(r.weight for r in results)) * 100)
    unc = {"pct": pct, "label": "faible" if pct < 25 else ("moyenne" if pct < 50 else "élevée")}
    covered = [r.label for r in results if r.level == Level.CONFIRMED.value]
    partial = [r.label for r in results if r.level == Level.PARTIAL.value]
    notdem = [r.label for r in results if r.level in (Level.DECLARED.value, Level.NOT_DOCUMENTED.value)]
    iq = information_quality(parsed, results, ext)
    shown = int(round(score))
    summary = _summary(shown, potential, tier, covered, partial, notdem, contradicted, open_mand, caps, unc)
    flags = list(parsed.flags)
    if parsed.extraction_quality != "ok":
        flags.append("Extraction partielle du document : certaines informations peuvent manquer (le score ne les suppose pas).")
    return Assessment(
        engine=ENGINE_VERSION, grid_version=grid.version, grid_hash=grid.content_hash, score_documented=round(score, 1),
        score_potential=round(potential, 1), score_displayed=shown, caps_applied=caps, tier=tier.value, uncertainty=unc, criteria=results,
        constraints=cons, alerts=alerts, covered=covered, partial=partial, not_demonstrated=notdem, contradicted=contradicted,
        open_mandatory=open_mand, summary=summary, information_quality=iq, flags=flags, security_flags=security_flags or [])


def information_quality(parsed: ParsedCV, results: list[CriterionResult], ext: dict[str, list[ExtEvidence]]) -> dict[str, Any]:
    """Précision, cohérence et vérification des informations — jamais la qualité rédactionnelle (§6.10)."""
    total = sum(r.weight for r in results) or 1
    decided = sum(r.weight for r in results if r.level in (Level.CONFIRMED.value, Level.PARTIAL.value, Level.CONTRADICTED.value))
    exps = [e for e in parsed.experiences if e.header != "(expériences non datées)"]
    dated = (sum(1 for e in exps if not e.dates_unknown) / len(exps)) if exps else 0.0
    allext = [e for lst in ext.values() for e in lst if not e.superseded]
    validated = (sum(1 for e in allext if e.validated) / len(allext)) if allext else None
    components = {
        "extraction": 1.0 if parsed.extraction_quality == "ok" else 0.6,
        "criteres_tranches": round(decided / total, 2),
        "experiences_datees": round(dated, 2),
        "coherence_dates": max(0.0, round(1 - 0.3 * len(parsed.flags), 2)),
    }
    if validated is not None:
        components["informations_validees"] = round(validated, 2)
    score = round(sum(components.values()) / len(components) * 100)
    return {"score": score, "components": components,
            "note": "Mesure la précision, la cohérence et la vérification des informations disponibles ; n'est pas une note d'adéquation ni une note de rédaction."}


def _summary(shown: int, potential: float, tier: Tier, covered: list[str], partial: list[str], notdem: list[str], contradicted: list[str],
             open_mand: list[str], caps: list[dict[str, Any]], unc: dict[str, Any]) -> str:
    parts = [f"Score documenté {shown}/100 (potentiel à confirmer jusqu'à {round(potential)}/100 — borne haute, pas une compétence acquise) ; incertitude {unc['label']}."]
    if covered:
        parts.append("Démontré : " + ", ".join(covered[:6]) + ".")
    if partial:
        parts.append("Partiellement démontré : " + ", ".join(partial[:6]) + ".")
    if notdem:
        parts.append("Non démontré (à qualifier) : " + ", ".join(notdem[:6]) + ".")
    if contradicted:
        parts.append("Contredit : " + ", ".join(contradicted) + ".")
    if open_mand:
        parts.append("Impératifs non confirmés : " + ", ".join(open_mand) + " — aucune présentation sans vérification.")
    if caps:
        parts.append("Plafond appliqué : " + "; ".join(f"{c['criterion']} → {c['cap']}" for c in caps) + ".")
    return " ".join(parts)


# ----------------------------------------------------------------- comparaison & différences
def compare(assessments: dict[str, Assessment]) -> dict[str, Any]:
    """Comparaison de candidats sur une grille IDENTIQUE (refuse de comparer des grilles différentes)."""
    hashes = {a.grid_hash for a in assessments.values()}
    if len(hashes) > 1:
        raise gd.GridError(["Les évaluations proviennent de versions de grille différentes : réévaluer avant de comparer (§6.3)."])
    names = list(assessments)
    if not names:
        return {"grid_hash": "", "candidates": [], "rows": []}
    rows = []
    keys = [r.key for r in assessments[names[0]].criteria]
    for k in keys:
        row = {"key": k, "label": next(r.label for r in assessments[names[0]].criteria if r.key == k),
               "weight": next(r.weight for r in assessments[names[0]].criteria if r.key == k), "cells": {}}
        for n in names:
            r = next(x for x in assessments[n].criteria if x.key == k)
            row["cells"][n] = {"level": r.level, "points": r.points, "justification": r.justification}
        pts = [row["cells"][n]["points"] for n in names]
        row["spread"] = round(max(pts) - min(pts), 2)
        rows.append(row)
    ranking = sorted(names, key=lambda n: -assessments[n].score_documented)
    reasons = {}
    if len(ranking) >= 2:
        a, b = ranking[0], ranking[1]
        diffs = sorted(rows, key=lambda r: -abs(r["cells"][a]["points"] - r["cells"][b]["points"]))[:3]
        reasons = {"leader": a, "second": b, "main_differences": [
            {"criterion": r["label"], "leader_points": r["cells"][a]["points"], "second_points": r["cells"][b]["points"],
             "leader_level": r["cells"][a]["level"], "second_level": r["cells"][b]["level"]} for r in diffs if r["spread"] > 0]}
    return {"grid_hash": next(iter(hashes)), "candidates": names, "ranking": ranking, "rows": rows, "why": reasons}


def diff_assessments(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Différences entre deux versions d'évaluation (analyse initiale vs enrichie)."""
    om = {c["key"]: c for c in old["criteria"]}
    changes = []
    for c in new["criteria"]:
        o = om.get(c["key"])
        if o is None:
            changes.append({"key": c["key"], "label": c["label"], "change": "nouveau critère", "to": c["level"], "points_delta": c["points"]})
            continue
        if o["level"] != c["level"] or abs(o["points"] - c["points"]) > 1e-6 or len(c.get("evidence", [])) != len(o.get("evidence", [])):
            new_ev = [e for e in c.get("evidence", []) if e.get("source") != "cv"]
            changes.append({"key": c["key"], "label": c["label"], "from": o["level"], "to": c["level"],
                            "points_delta": round(c["points"] - o["points"], 2),
                            "because": [e.get("excerpt", "")[:200] for e in new_ev][:3], "justification": c["justification"]})
    return {"score_delta": round(new["score_documented"] - old["score_documented"], 1),
            "potential_delta": round(new["score_potential"] - old["score_potential"], 1),
            "tier": {"from": old["tier"], "to": new["tier"]}, "changes": changes}
