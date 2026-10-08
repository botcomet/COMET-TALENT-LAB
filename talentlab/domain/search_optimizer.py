"""Boucle d'amélioration d'une recherche après retour du recruteur (§5.6, §5.7).

Règles :
- le nombre de résultats vient UNIQUEMENT du recruteur (jamais simulé) ;
- chaque nouvelle version modifie *une* chose et l'explique ;
- une requête déjà essayée n'est jamais rejouée (forme canonique) ;
- on élargit/resserre la *découverte* ; la grille de matching n'est jamais touchée.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from . import boolean as bl
from . import lexicon as lx
from .boolean import PlatformProfile, TURNOVER
from .enums import Category
from .requirements import Req
from .search_plan import SearchGroup, SearchVariant, boolean_terms, explain, fit, rank_requirements, _rarity, _CAT_STRENGTH
from .text import fold

TAGS = ("trop_juniors", "mauvaise_expertise", "competence_absente", "faux_positifs_recurrents", "bons_profils_manquants", "autre")


@dataclass
class Feedback:
    result_count: int | None = None              # saisi par le recruteur après exécution réelle
    relevance: str | None = None                 # "bonne" | "partielle" | "mauvaise"
    tags: list[str] = field(default_factory=list)
    false_positive_terms: list[str] = field(default_factory=list)   # termes qui caractérisent les faux positifs
    missing_skill: str = ""
    missing_profiles_note: str = ""
    notes: str = ""


@dataclass
class Diagnosis:
    problem: str                                  # zero | too_broad | too_narrow | low_relevance | acceptable | unknown
    causes: list[str]
    advice: list[str]
    suspects: list[str] = field(default_factory=list)   # groupes secondaires candidats au retrait

    def to_dict(self):
        return {"problem": self.problem, "causes": self.causes, "advice": self.advice, "suspects": self.suspects}


@dataclass
class Proposal:
    variant: SearchVariant | None
    diagnosis: Diagnosis
    modification: str                              # ce qui change et pourquoi (obligatoire si variant)
    tradeoffs: list[str]
    native_filters: list[str]
    exhausted: bool = False
    needs_human: str = ""

    def to_dict(self):
        return {"variant": self.variant.to_dict() if self.variant else None, "diagnosis": self.diagnosis.to_dict(),
                "modification": self.modification, "tradeoffs": self.tradeoffs, "native_filters": self.native_filters,
                "exhausted": self.exhausted, "needs_human": self.needs_human}


def variant_from_dict(d: dict[str, Any]) -> SearchVariant:
    groups = [SearchGroup(**g) for g in d["groups"]]
    v = SearchVariant(strategy=d["strategy"], groups=groups, negatives=list(d.get("negatives", [])),
                      extra_queries=list(d.get("extra_queries", [])), trimmed=list(d.get("trimmed", [])))
    return v.refresh()


# ----------------------------------------------------------------- diagnostic
def diagnose(v: SearchVariant, fb: Feedback, profile: PlatformProfile = TURNOVER) -> Diagnosis:
    causes: list[str] = []
    advice: list[str] = []
    suspects = [g.label for g in sorted((g for g in v.groups if not g.protected), key=lambda g: (-g.priority, -g.rarity))]
    issues = bl.validate(v.query, profile)
    errs = [i for i in issues if i.severity == "error" and i.code != "TOO_LONG"]
    n_and = sum(1 for g in v.groups if g.kind != "role")
    c = fb.result_count

    if c is None:
        problem = "unknown"
        advice.append("Exécuter la recherche sur la plateforme puis renseigner le nombre de résultats : aucune estimation n'est faite avant.")
    elif c == 0:
        problem = "zero"
        if errs:
            causes.append("Erreur de syntaxe : " + " ; ".join(i.message for i in errs))
        if n_and >= 4:
            causes.append(f"{n_and} groupes imposés simultanément : peu de CV les contiennent tous.")
        long_phr = [t for g in v.groups for t in g.terms if len(t.split()) >= 3]
        if long_phr:
            causes.append("Expression(s) exacte(s) longue(s), rarement écrite(s) à l'identique dans un CV : " + ", ".join(f"« {t} »" for t in long_phr[:3]))
        titles = [g for g in v.groups if g.kind == "role"]
        if titles and len(titles[0].terms) <= 2:
            causes.append("Peu d'intitulés alternatifs : les CV peuvent employer une autre formulation du métier.")
        if v.negatives:
            causes.append("Des exclusions (NOT) peuvent retirer des CV pertinents.")
        if not causes:
            causes.append("Aucune erreur détectable dans la requête : la rareté du besoin ou une contrainte secondaire peut bloquer la recherche.")
    elif c > profile.too_broad:
        problem = "too_broad"
        causes.append(f"{c} résultats : au-delà de {profile.too_broad}, la recherche est généralement trop large.")
        if n_and <= 1:
            causes.append("Une seule compétence discriminante imposée : le métier est trop peu distingué des métiers voisins.")
        if any(g.kind == "role" and len(g.terms) >= 5 for g in v.groups):
            causes.append("Beaucoup d'intitulés élargis : des métiers voisins ressortent probablement.")
    elif c < profile.target_min:
        problem = "too_narrow"
        causes.append(f"{c} résultat(s) (< {profile.target_min}) : vérifier si la rareté est normale ou si un critère bloque.")
        if n_and >= 3:
            causes.append(f"{n_and} groupes imposés : le plus secondaire peut être le facteur bloquant.")
    elif c > profile.target_max and fb.relevance in (None, "bonne"):
        problem = "acceptable"
        advice.append(f"{c} résultats (> {profile.target_max}) : examiner la pertinence des premiers CV avant de resserrer.")
    elif fb.relevance in ("partielle", "mauvaise") or fb.tags:
        problem = "low_relevance"
        causes.append("Volume exploitable mais pertinence insuffisante : analyser la nature des faux positifs.")
    else:
        problem = "acceptable"
        advice.append("Volume et pertinence dans la plage visée : sauvegarder cette version comme utile.")
    if "trop_juniors" in fb.tags:
        causes.append("Profils trop juniors : les mots-clés ne portent pas l'ancienneté.")
        advice.append("Utiliser le filtre natif d'années d'expérience de la plateforme plutôt qu'un mot-clé de séniorité (risque d'exclure de bons profils).")
    if "mauvaise_expertise" in fb.tags:
        causes.append("Mauvaise expertise : la recherche ne distingue pas assez le métier recherché d'un métier voisin.")
    return Diagnosis(problem, causes, advice, suspects)


# ----------------------------------------------------------------- transformations élémentaires
Step = tuple[str, SearchVariant, list[str]]       # (explication, variante, compromis)


def _clone(v: SearchVariant) -> SearchVariant:
    c = SearchVariant(strategy=v.strategy, groups=copy.deepcopy(v.groups), negatives=list(v.negatives),
                      extra_queries=list(v.extra_queries), trimmed=list(v.trimmed))
    return c


def _split_long_phrase(v: SearchVariant) -> Iterator[Step]:
    """« A B C » exact → « A B » AND « C » : plus large (mots non adjacents).

    Valable seulement pour un groupe à terme unique : sinon le AND ajouté
    s'imposerait aussi aux autres alternatives du OR et resserrerait la recherche.
    """
    for gi, g in enumerate(v.groups):
        if len(g.terms) != 1:
            continue
        t = g.terms[0]
        words = t.split()
        if len(words) >= 3:
            head, tail = " ".join(words[:2]), " ".join(words[2:])
            c = _clone(v)
            c.groups[gi].terms = [head]
            c.groups.append(SearchGroup(id=f"split:{tail}", kind="skill", label=tail, terms=[tail], priority=g.priority + 1,
                                        protected=False, why="Fragment d'une expression exacte longue, cherché séparément."))
            yield (f"L'expression exacte « {t} » est rarement écrite à l'identique : elle est scindée en « {head} » et « {tail} » (mots non nécessairement adjacents).",
                   c.refresh(), [f"Moins précis : « {head} » et « {tail} » peuvent apparaître dans des contextes différents."])


def _widen_titles(v: SearchVariant, reqs: list[Req], title: str) -> Iterator[Step]:
    """Ajoute d'un coup tous les intitulés alternatifs de la famille (une modification substantielle)."""
    fam = lx.detect_role_family(title)
    if not fam:
        return
    for gi, g in enumerate(v.groups):
        if g.kind != "role":
            continue
        have = {fold(x) for x in g.terms}
        pool = [t for t in (*fam.titles, *fam.wide_titles) if fold(t) not in have]
        if pool:
            c = _clone(v)
            add = pool
            c.groups[gi].terms = c.groups[gi].terms + add
            yield ("Intitulés alternatifs de la famille « " + fam.label + " » ajoutés pour couvrir d'autres formulations du même métier : "
                   + ", ".join(f"« {a} »" for a in add) + ".",
                   c.refresh(), ["Des métiers voisins peuvent ressortir : à vérifier sur les premiers CV."])


def _add_related_synonyms(v: SearchVariant, reqs: list[Req]) -> Iterator[Step]:
    """Synonymes équivalents non encore utilisés, tous groupes à la fois ; puis termes voisins (compétences rares seulement)."""
    by_key = {r.key: r for r in reqs}
    cand_groups = [g for g in v.groups if g.kind == "skill" and g.req_key in by_key and not g.id.startswith(("union", "split", "free"))]
    added: list[tuple[str, str]] = []
    c = _clone(v)
    for g in cand_groups:
        r = by_key[g.req_key]
        have = {fold(x) for x in g.terms}
        eq = [t for t in boolean_terms(r, 99) if fold(t) not in have]
        if eq:
            for cg in c.groups:
                if cg.id == g.id:
                    cg.terms = cg.terms + eq[:2]
            added += [(g.label, t) for t in eq[:2]]
    if added:
        yield ("Synonymes équivalents testés : " + "; ".join(f"« {t} » pour {lab}" for lab, t in added) + ".", c.refresh(),
               ["Aucun compromis majeur : formulations alternatives équivalentes."])
    for g in sorted(cand_groups, key=lambda g: -lx.skill(g.req_key.split(":", 1)[1]).rarity if lx.skill(g.req_key.split(":", 1)[1]) else 0):
        sk = lx.skill(g.req_key.split(":", 1)[1])
        have = {fold(x) for x in g.terms}
        related = [t for t in (sk.related if sk else ()) if fold(t) not in have and len(t) > 2]
        if sk and sk.rarity >= 4 and related:
            c2 = _clone(v)
            for cg in c2.groups:
                if cg.id == g.id:
                    cg.terms = cg.terms + related[:1]
            yield (f"Terme voisin « {related[0]} » ajouté au groupe « {g.label} » (proche mais non équivalent).", c2.refresh(),
                   [f"« {related[0]} » n'est pas équivalent à « {g.label} » : faux positifs possibles, à contrôler sur les premiers CV."])


def _drop_secondary(v: SearchVariant) -> Iterator[Step]:
    """Retire d'un coup les critères secondaires de même rang (jamais un impératif ni un éliminatoire)."""
    secondary = [g for g in v.groups if not g.protected and g.kind != "role"]
    for p in sorted({round(g.priority) for g in secondary}, reverse=True):
        batch = [g for g in secondary if round(g.priority) == p]
        c = _clone(v)
        gone = {g.id for g in batch}
        c.groups = [x for x in c.groups if x.id not in gone]
        if not any(x.kind != "role" for x in c.groups):
            continue
        labels = ", ".join(f"« {g.label} »" for g in batch)
        cats = sorted({("indice de profondeur — l'exigence elle-même reste imposée" if g.kind == "narrower" else (g.category or "non rattaché au brief").replace("_", " ")) for g in batch})
        yield (f"Critère(s) secondaire(s) {labels} ({' / '.join(cats)}) retiré(s) de la RECHERCHE — pas de la grille. "
               "Ils sont vérifiés au matching et l'exigence client reste inchangée.",
               c.refresh(), [f"Les profils sans {labels} ressortiront : le scoring les départagera.", "Le besoin client n'est pas modifié (grille intacte)."])


def _loosen_strategy(v: SearchVariant, reqs: list[Req], title: str) -> Iterator[Step]:
    """Variante plus ouverte, dérivée de la version COURANTE (jamais de retour en arrière)."""
    skills = [g for g in v.groups if g.kind in ("skill", "narrower", "combo") and g.id != "union"]
    narrow = [g for g in v.groups if g.kind == "narrower"]
    if v.strategy == "strict" and (narrow or len([g for g in skills if g.kind != "narrower"]) > 3):
        c = _clone(v)
        keep = [g for g in skills if g.kind == "skill"][:3]
        keep_ids = {g.id for g in keep}
        c.groups = [g for g in c.groups if g.kind == "role" or g.id in keep_ids]
        c.strategy = "balanced"
        yield ("Passage en stratégie équilibrée : les indices de pratique avancée et les groupes au-delà des trois plus discriminants sont retirés de la recherche.",
               c.refresh(), ["Plus de volume et de bruit : le scoring vérifie la profondeur réelle.", "Les exigences client restent intégralement dans la grille."])
    base_skills = [g for g in v.groups if g.kind in ("skill", "combo", "narrower") and g.id != "union"]
    if base_skills and v.strategy != "exploratory":
        c = _clone(v)
        terms: list[str] = []
        for g in base_skills[:3]:
            terms += g.terms[:2] if g.terms else []
            if g.combo_text:
                terms += [t for t in bl.terms_of(bl.parse(g.combo_text)[0])][:3]
        fam = lx.detect_role_family(title)
        for gi, g in enumerate(c.groups):
            if g.kind == "role" and fam:
                have = {fold(x) for x in g.terms}
                g.terms = g.terms + [t for t in fam.wide_titles if fold(t) not in have][:3]
        c.groups = [g for g in c.groups if g.kind == "role"] + [SearchGroup(
            id="union", kind="skill", label="Au moins une compétence discriminante", terms=list(dict.fromkeys(terms)), priority=1, protected=True,
            rarity=max((g.rarity for g in base_skills), default=3),
            why="Union (OR) des compétences discriminantes : maximise la découverte.")]
        c.strategy = "exploratory"
        c.extra_queries = []
        yield ("Passage en stratégie exploratoire : les compétences imposées deviennent des alternatives (au moins une) et les intitulés sont élargis. "
               "Les exigences client restent dans la grille ; seule la découverte est élargie.",
               c.refresh(), ["Volume potentiellement très élevé : le scoring strict doit trier.", "Aucune exigence client n'est supprimée."])


def _add_discriminant(v: SearchVariant, reqs: list[Req]) -> Iterator[Step]:
    """Resserre en imposant la prochaine compétence discriminante (ou en la sortant de l'union « au moins une »)."""
    ranked, _ = rank_requirements(reqs)
    used = {g.req_key for g in v.groups if g.req_key and g.id != "union" and not g.id.startswith("union:")}
    members = {m for r in reqs if r.key.startswith("group:") for m in r.params.get("members", [])}
    union = next((g for g in v.groups if g.id == "union"), None)
    union_f = {fold(t) for t in union.terms} if union else set()
    for r in ranked:
        if r.key in used or r.key in members or _CAT_STRENGTH.get(r.category.value, 0) < 2:
            continue
        terms = boolean_terms(r, 2)
        if union and union_f & {fold(t) for t in terms}:
            c = _clone(v)
            cu = next(g for g in c.groups if g.id == "union")
            cu.terms = [t for t in cu.terms if fold(t) not in {fold(x) for x in terms}]
            pr = max(g.priority for g in c.groups) + 1
            c.groups.append(SearchGroup(id=r.key, kind="skill", label=r.label, terms=terms, priority=pr,
                                        protected=r.category in (Category.IMPERATIF, Category.ELIMINATOIRE), req_key=r.key,
                                        category=r.category.value, rarity=_rarity(r),
                                        why="Compétence discriminante désormais imposée (sortie de l'union « au moins une »)."))
            if len(cu.terms) <= 1:
                cu.id = "union_rest"
            yield (f"« {r.label} » ({r.category.value.replace('_', ' ')}, rareté {_rarity(r)}/5) n'est plus une alternative mais imposée : elle distingue le métier recherché des métiers voisins.",
                   c.refresh(), [f"Les CV qui n'écrivent pas « {r.label} » seront exclus : risque de faux négatifs."])
            continue
        if any(fold(t) in {fold(x) for g in v.groups for x in g.terms} for t in terms):
            continue
        c = _clone(v)
        pr = max((g.priority for g in c.groups), default=0) + 1
        c.groups.append(SearchGroup(id=r.key, kind="skill", label=r.label, terms=terms, priority=pr,
                                    protected=r.category in (Category.IMPERATIF, Category.ELIMINATOIRE), req_key=r.key,
                                    category=r.category.value, rarity=_rarity(r),
                                    why="Compétence discriminante ajoutée pour séparer le métier recherché des métiers voisins."))
        yield (f"Compétence discriminante « {r.label} » ajoutée ({r.category.value.replace('_', ' ')}, rareté {_rarity(r)}/5) pour écarter les profils voisins.",
               c.refresh(), [f"Les CV qui n'écrivent pas « {r.label} » seront exclus : risque de faux négatifs."])


def _narrow_with_narrower(v: SearchVariant) -> Iterator[Step]:
    for g in sorted((g for g in v.groups if g.kind == "skill" and g.req_key), key=lambda g: g.priority):
        sk = lx.skill(g.req_key.split(":", 1)[1]) if ":" in g.req_key else None
        if sk and sk.narrowers and f"narrow:{g.req_key}" not in {x.id for x in v.groups}:
            c = _clone(v)
            c.groups.append(SearchGroup(id=f"narrow:{g.req_key}", kind="narrower", label=f"{g.label} — pratique avancée",
                                        terms=list(sk.narrowers[:3]), priority=max(x.priority for x in c.groups) + 1, protected=False,
                                        req_key=g.req_key, category=g.category, rarity=5,
                                        why="Indices de pratique avancée pour distinguer l'usage réel de la simple mention."))
            yield (f"Indices de pratique avancée ajoutés pour « {g.label} » : " + ", ".join(sk.narrowers[:3]) + ".", c.refresh(),
                   ["Profils plus pointus mais moins nombreux ; la présence d'un terme reste à confirmer par une preuve."])


def _narrow_titles(v: SearchVariant) -> Iterator[Step]:
    for gi, g in enumerate(v.groups):
        if g.kind == "role" and len(g.terms) >= 4:
            c = _clone(v)
            dropped = c.groups[gi].terms[-1]
            c.groups[gi].terms = c.groups[gi].terms[:-1]
            yield (f"Intitulé le plus large « {dropped} » retiré pour réduire les métiers voisins.", c.refresh(), ["Un profil qui porte ce seul intitulé sera manqué."])


def _add_not(v: SearchVariant, terms: list[str], reqs: list[Req] | None = None, refused: list[str] | None = None) -> Iterator[Step]:
    """NOT seulement sur un faux positif identifié ET jamais sur un terme que la requête exige ou que le besoin client impose
    (« AND Kafka … AND NOT Kafka » serait insatisfiable)."""
    positive = {fold(t) for g in v.groups for t in (g.terms or [])}
    for g in v.groups:
        if g.combo_text and (node := bl.parse(g.combo_text)[0]) is not None:
            positive |= {fold(t) for t in bl.terms_of(node)}
    required = {fold(t) for r in (reqs or []) if r.status == "active" for t in (*r.terms, r.label)}
    def conflicts(t: str) -> bool:
        tf = fold(t)
        return any(tf == p or tf in p.split() or p in tf.split() or tf in p or p in tf for p in (positive | required) if p)
    new, bad = [], []
    for t in terms:
        if fold(t) in {fold(n) for n in v.negatives}:
            continue
        (bad if conflicts(t) else new).append(t)
    if refused is not None:
        refused.extend(bad)
    if new:
        c = _clone(v)
        c.negatives = c.negatives + new[:2]
        yield (f"Exclusion (NOT) de {', '.join(f'« {t}' + '»' for t in new[:2])} : faux positif récurrent signalé par le recruteur.", c.refresh(),
               ["Risque : un CV pertinent qui mentionne ce terme dans un autre contexte sera exclu. À valider par le recruteur avant usage."])


def _move_missing_skill(v: SearchVariant, reqs: list[Req], skill_text: str) -> Iterator[Step]:
    f = fold(skill_text)
    for r in reqs:
        if r.status != "active" or r.dimension == "contrainte":
            continue
        if fold(r.label) in f or f in fold(r.label) or any(fold(t) == f for t in r.terms):
            if any(g.id == r.key for g in v.groups):
                continue
            c = _clone(v)
            pr = max((g.priority for g in c.groups), default=0) + 1
            c.groups.append(SearchGroup(id=r.key, kind="skill", label=r.label, terms=boolean_terms(r, 2), priority=pr, protected=False,
                                        req_key=r.key, category=r.category.value, rarity=_rarity(r),
                                        why="Compétence signalée comme absente des premiers CV : désormais imposée."))
            trade = ["Moins de résultats ; risque de manquer un profil qui l'écrit autrement."]
            if r.category.value in ("souhaitable", "contextuel", "fortement_differenciant", "a_clarifier"):
                trade.append(f"« {r.label} » n'est pas un impératif du client ({r.category.value.replace('_', ' ')}) : l'imposer en recherche est un choix de sourcing, "
                             "pas une exigence ; le besoin et la grille ne sont pas modifiés.")
            yield (f"« {r.label} » signalée absente des premiers CV : imposée en AND (les groupes déjà imposés sont conservés).", c.refresh(), trade)
            return


def _note_refused(diag: "Diagnosis", refused: list[str]) -> None:
    for t in dict.fromkeys(refused):
        msg = (f"NOT « {t} » refusé : ce terme figure dans la requête ou dans le besoin client ; l'exclure rendrait la recherche contradictoire. "
               "Si le faux positif vient d'un contexte précis, le décrire (autre mot du CV) plutôt que d'exclure la technologie.")
        if msg not in diag.advice:
            diag.advice.append(msg)


# ----------------------------------------------------------------- échelle de décision
def _steps(v: SearchVariant, fb: Feedback, diag: Diagnosis, reqs: list[Req], title: str) -> Iterator[Step]:
    """Échelle de décision (§5.6) — une modification substantielle et expliquée par étape."""
    if diag.problem in ("zero", "too_narrow"):
        # 1-3 syntaxe / expressions exactes / parenthèses  → 4 intitulés → 5 synonymes → 6 contraintes secondaires → 7 variante plus ouverte
        yield from _split_long_phrase(v)
        yield from _drop_secondary(v)
        yield from _widen_titles(v, reqs, title)
        yield from _add_related_synonyms(v, reqs)
        yield from _loosen_strategy(v, reqs, title)
    elif diag.problem == "too_broad":
        yield from _add_discriminant(v, reqs)
        yield from _narrow_titles(v)
        yield from _narrow_with_narrower(v)
        if fb.false_positive_terms:
            refused: list[str] = []
            yield from _add_not(v, fb.false_positive_terms, reqs, refused)
            _note_refused(diag, refused)
    elif diag.problem == "low_relevance":
        if fb.missing_skill:
            yield from _move_missing_skill(v, reqs, fb.missing_skill)
        if "mauvaise_expertise" in fb.tags or "competence_absente" in fb.tags:
            yield from _add_discriminant(v, reqs)
            yield from _narrow_with_narrower(v)
        if "faux_positifs_recurrents" in fb.tags:
            if fb.false_positive_terms:
                refused = []
                yield from _add_not(v, fb.false_positive_terms, reqs, refused)
                _note_refused(diag, refused)
            yield from _narrow_titles(v)
        if "bons_profils_manquants" in fb.tags:
            yield from _widen_titles(v, reqs, title)
            yield from _add_related_synonyms(v, reqs)
        if "trop_juniors" in fb.tags:
            yield from _narrow_with_narrower(v)
        yield from _add_discriminant(v, reqs)
    # "acceptable" / "unknown" : aucune transformation automatique


def optimize(v: SearchVariant, fb: Feedback, reqs: list[Req], *, title: str, history: list[str],
             profile: PlatformProfile = TURNOVER) -> Proposal:
    """Propose la prochaine version, explique la modification, ne répète jamais une requête de ``history``."""
    diag = diagnose(v, fb, profile)
    natives: list[str] = []
    if "trop_juniors" in fb.tags:
        natives.append("Filtre natif « années d'expérience » (plutôt qu'un mot-clé de séniorité).")
    if diag.problem in ("too_broad", "low_relevance"):
        natives.append("Filtres natifs de la plateforme : localisation, disponibilité, niveau d'expérience.")
    if diag.problem in ("acceptable", "unknown"):
        return Proposal(None, diag, "Aucune modification automatique : " + (diag.advice[0] if diag.advice else "recherche exploitable."), [], natives)
    seen = {bl.canonical(q) for q in history} | {bl.canonical(v.query)}
    for text, cand, trade in _steps(v, fb, diag, reqs, title):
        fit(cand, profile)
        if not cand.fits or not cand.query:
            continue
        if bl.canonical(cand.query) in seen:
            continue
        if any(i.severity == "error" for i in bl.validate(cand.query, profile)):
            continue
        cand.explanation = explain(cand, title=title, reqs=[r for r in reqs if r.status == "active"], notes=[], excluded=[],
                                   core=[t for g in cand.groups if g.kind == "role" for t in g.terms], profile=profile)
        sim = bl.similarity(cand.query, v.query)
        cand.explanation["modification"] = text
        cand.explanation["similarity_with_previous"] = round(sim, 2)
        return Proposal(cand, diag, text, trade, natives)
    return Proposal(None, diag, "", [], natives, exhausted=True,
                    needs_human="Toutes les transformations automatiques ont déjà été essayées : reformuler le besoin avec le client, "
                                "élargir manuellement les intitulés ou utiliser les filtres natifs. Une requête déjà testée n'est jamais rejouée.")
