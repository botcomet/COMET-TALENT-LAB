"""Construction des trois recherches booléennes (§5.3, §5.4, §5.5).

Le booléen sert à *découvrir* ; le scoring vérifie ensuite strictement. Les
exigences client ne sont jamais modifiées par la stratégie de découverte (§5.7).
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from . import boolean as bl
from . import lexicon as lx
from .boolean import AND, OR, Group, Not, Term, PlatformProfile, TURNOVER
from .enums import Category
from .requirements import Req
from .text import fold

STRATEGIES = ("exploratory", "balanced", "strict")
STRATEGY_LABELS = {"exploratory": "Exploratoire", "balanced": "Équilibrée", "strict": "Stricte"}

_CAT_STRENGTH = {Category.ELIMINATOIRE.value: 4, Category.IMPERATIF.value: 3, Category.DIFFERENCIANT.value: 2,
                 Category.SOUHAITABLE.value: 1}


@dataclass
class SearchGroup:
    id: str
    kind: str                       # role | skill | narrower | combo
    label: str
    terms: list[str]
    priority: int                   # 0 = indispensable à la logique ; croît avec le caractère secondaire
    protected: bool = False         # jamais retiré par l'optimisation
    req_key: str | None = None
    category: str | None = None
    rarity: int = 3
    why: str = ""
    combo_text: str = ""            # sous-arbre « k parmi n » déjà rendu (kind == "combo")
    alternatives: list[str] = field(default_factory=list)       # k parmi n réparti : sous-arbres des requêtes complémentaires (rendus)

    def node(self):
        if self.combo_text:
            return bl.parse(self.combo_text)[0]
        return OR(*[Term(t) for t in self.terms]) if self.terms else None


@dataclass
class SearchVariant:
    strategy: str
    groups: list[SearchGroup]
    negatives: list[str] = field(default_factory=list)
    query: str = ""
    extra_queries: list[str] = field(default_factory=list)      # variantes complémentaires (k parmi n)
    explanation: dict[str, Any] = field(default_factory=dict)
    trimmed: list[str] = field(default_factory=list)
    fits: bool = True

    def node(self):
        parts = [g.node() for g in self.groups if g.node() is not None]
        parts += [Not(OR(*[Term(n) for n in self.negatives]))] if self.negatives else []
        return AND(*parts) if parts else None

    def refresh(self) -> "SearchVariant":
        n = self.node()
        self.query = bl.render(n) if n is not None else ""
        combo = next((g for g in self.groups if g.kind == "combo" and g.alternatives), None)
        if combo is not None:           # les requêtes complémentaires (k parmi n) suivent TOUTE modification des autres groupes
            others = [g.node() for g in self.groups if g is not combo and g.node() is not None]
            others += [Not(OR(*[Term(x) for x in self.negatives]))] if self.negatives else []
            self.extra_queries = [bl.render(AND(*others, bl.parse(alt)[0])) for alt in combo.alternatives if bl.parse(alt)[0] is not None]
        return self

    def longest(self) -> int:
        return max([len(self.query), *[len(q) for q in self.extra_queries]])

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["length"] = len(self.query)
        return d


@dataclass
class SearchSet:
    title: str
    role_family: str | None
    variants: dict[str, SearchVariant]
    notes: list[str]
    excluded: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "role_family": self.role_family, "notes": self.notes, "excluded": self.excluded,
                "variants": {k: v.to_dict() for k, v in self.variants.items()}}


# ----------------------------------------------------------------- termes
def boolean_terms(r: Req, limit: int) -> list[str]:
    """Alias utilisables dans un groupe OR : équivalents réels, non ambigus."""
    sk = lx.skill(r.key.split(":", 1)[1]) if ":" in r.key else None
    cs = set(sk.case_sensitive) if sk else set()
    out: list[str] = []
    label_f = fold(r.label)
    for t in r.terms or ([r.label] if r.label else []):
        tf = fold(t)
        if sk and not sk.label_is_term and t == sk.label:
            continue          # libellé descriptif, pas une expression présente dans un CV
        compact = tf.replace(" ", "")
        if t in cs or (len(compact) <= 2 and not any(ch.isdigit() for ch in compact)):
            continue
        if tf != label_f and tf.startswith(label_f + " ") and re.fullmatch(r"[\w\s./+-]*", tf[len(label_f):]):
            continue          # « Java 17 », « Dell EMC PowerStore » : qualificatifs du libellé, pas des synonymes
        if sk and sk.context_needed and sk.rarity >= 4 and " " not in tf:
            continue          # mot nu rare et ambigu (« Unity », « GTS », « Reflex ») : le booléen ne peut pas garantir le contexte
        if t not in out:
            out.append(t)
    # « Spring Boot » ⊃ « Spring » : un CV qui contient le long contient le court → le long n'ajoute aucun rappel
    out = prune_redundant_aliases(out)
    return out[:limit]


def _word_set(t: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9+#]+", fold(t)))


def _contains_phrase(longer: str, shorter: str) -> bool:
    a, b = _word_set(longer), _word_set(shorter)
    if not b or len(b) >= len(a):
        return False
    return any(a[i:i + len(b)] == b for i in range(len(a) - len(b) + 1))


def prune_redundant_aliases(terms: list[str]) -> list[str]:
    return [t for t in terms if not any(o is not t and _contains_phrase(t, o) for o in terms)]


def usable_in_boolean(r: Req) -> tuple[bool, str]:
    sk = lx.skill(r.key.split(":", 1)[1]) if ":" in r.key else None
    if r.kind in ("years", "constraint", "language"):
        return False, "Filtre natif de la plateforme (expérience, localisation, disponibilité) plus fiable qu'un mot-clé."
    if r.dimension == "methodologie" or (sk and sk.boolean_generic):
        return False, "Trop générique pour discriminer : présent dans la plupart des CV du métier."
    if r.category in (Category.CONTEXTUEL, Category.A_CLARIFIER):
        return False, "Information contextuelle ou à clarifier : non imposée pour préserver le rappel."
    if not boolean_terms(r, 3):
        return False, "Alias ambigus (mot courant ou sensible à la casse) : non cherchable par mot-clé sans bruit, vérifié au matching."
    return True, ""


def rank_requirements(reqs: list[Req]) -> tuple[list[Req], list[dict[str, str]]]:
    cands, excl = [], []
    for r in reqs:
        if r.status != "active" or r.key.startswith("group:"):
            continue
        ok, why = usable_in_boolean(r)
        if not ok:
            excl.append({"skill": r.label, "reason": why})
            continue
        cands.append(r)

    def rar(r: Req) -> int:
        sk = lx.skill(r.key.split(":", 1)[1]) if ":" in r.key else None
        return sk.rarity if sk else 3

    cands.sort(key=lambda r: (-_CAT_STRENGTH.get(r.category.value, 0), -rar(r), r.label))
    return cands, excl


def _rarity(r: Req) -> int:
    sk = lx.skill(r.key.split(":", 1)[1]) if ":" in r.key else None
    return sk.rarity if sk else 3


# ----------------------------------------------------------------- titres
def role_titles(title: str, reqs: list[Req], extra: list[str]) -> tuple[list[str], list[str], str | None, list[str]]:
    """(intitulés sûrs, variantes élargies, famille, notes)."""
    notes: list[str] = []
    fam = lx.detect_role_family(title)
    core: list[str] = list(extra)
    wide: list[str] = []
    mand = {r.key.split(":", 1)[-1] for r in reqs if r.category in (Category.IMPERATIF, Category.ELIMINATOIRE)}
    if fam:
        core += list(fam.titles)
        wide += list(fam.wide_titles)
        # le titre ne suffit pas : un métier voisin peut mieux décrire le travail réel (§4)
        derived: list[str] = []
        for other in lx.role_families():
            d0 = other.discriminants[0] if other.discriminants else None
            sk0 = lx.skill(d0) if d0 else None
            if other.key != fam.key and sk0 and sk0.kind == "activity" and sk0.rarity >= 4 and d0 in mand and d0 not in fam.discriminants:
                derived += list(other.titles[:2])
                wide += list(other.wide_titles[:2])
                notes.append(f"Intitulés « {other.label} » placés en tête : le travail décrit (« {sk0.label} ») correspond mieux à ce métier que le seul titre du brief.")
        core = derived + core
    else:
        core.append(title)
        notes.append("Famille de métier non reconnue : seul l'intitulé du brief est utilisé ; ajouter manuellement des synonymes.")
    seen: set[str] = set()
    c2 = [t for t in core if not (fold(t) in seen or seen.add(fold(t)))]
    w2 = [t for t in wide if fold(t) not in seen and not seen.add(fold(t))]
    return c2, w2, fam.key if fam else None, notes


# ----------------------------------------------------------------- k parmi n
def k_of_n(groups: list[Node], k: int) -> list[Node]:  # type: ignore[name-defined]
    """Requêtes dont l'union couvre toutes les combinaisons de k éléments : g_i AND (k-1 parmi la suite)."""
    n = len(groups)
    if k <= 1 or n < k:
        return [OR(*groups)] if groups else []
    out = []
    for i in range(n - k + 1):
        rest = groups[i + 1:]
        inner = OR(*rest) if k == 2 else OR(*k_of_n(rest, k - 1))
        out.append(AND(groups[i], inner))
    return out


# ----------------------------------------------------------------- ajustement à la longueur
def fit(variant: SearchVariant, profile: PlatformProfile) -> SearchVariant:
    """Retire d'abord le redondant, puis le secondaire, sans casser la logique (§5.3 G)."""
    variant.refresh()
    guard = 0
    while variant.longest() > profile.max_length and guard < 200:
        guard += 1
        # 1) alias redondants (le terme court inclut le long)
        done = False
        for g in sorted(variant.groups, key=lambda g: -g.priority):
            pr = prune_redundant_aliases(g.terms)
            if len(pr) < len(g.terms):
                variant.trimmed.append(f"Alias redondant(s) retiré(s) de « {g.label} » : " + ", ".join(t for t in g.terms if t not in pr))
                g.terms = pr
                done = True
                break
        if done:
            variant.refresh()
            continue
        # 2) synonymes des groupes de compétences (secondaires d'abord) — jamais ceux du métier à ce stade
        for protected in (False, True):
            for g in sorted(variant.groups, key=lambda g: -g.priority):
                if g.kind != "role" and g.protected == protected and len(g.terms) > 1:
                    gone = g.terms.pop()
                    variant.trimmed.append(f"Synonyme « {gone} » retiré de « {g.label} » (longueur).")
                    done = True
                    break
            if done:
                break
        if done:
            variant.refresh()
            continue
        # 3) groupe secondaire entier
        removable = [g for g in variant.groups if not g.protected]
        if removable:
            g = max(removable, key=lambda g: (g.priority, -g.rarity))
            variant.groups.remove(g)
            variant.trimmed.append(f"Critère « {g.label} » retiré de la recherche (secondaire, longueur) : il reste dans la grille de matching.")
            variant.refresh()
            continue
        # 4) dernier recours : un IMPÉRATIF peu discriminant (jamais un éliminatoire, un combo imposé ou le métier).
        #    Il quitte la RECHERCHE seulement ; la grille et le scoring le conservent intégralement (§5.7).
        weak = [g for g in variant.groups if g.kind == "skill" and g.category == Category.IMPERATIF.value and g.id != "union"]
        if len([g for g in variant.groups if g.kind != "role"]) > 1 and weak:
            g = min(weak, key=lambda g: (g.rarity, -g.priority))
            variant.groups.remove(g)
            variant.trimmed.append(
                f"Impératif « {g.label} » non cherché (faible pouvoir discriminant, rareté {g.rarity}/5, limite de {profile.max_length} caractères) : "
                "il reste exigé par la grille et vérifié au matching.")
            variant.refresh()
            continue
        # 5) en tout dernier lieu, les synonymes du groupe métier (jusqu'à deux intitulés)
        role = next((g for g in variant.groups if g.kind == "role" and len(g.terms) > 2), None)
        if role is not None:
            gone = role.terms.pop()
            variant.trimmed.append(f"Intitulé « {gone} » retiré du groupe métier (longueur).")
            variant.refresh()
            continue
        break
    variant.fits = variant.longest() <= profile.max_length
    return variant


# ----------------------------------------------------------------- construction
def _role_group(titles: list[str], why: str, cap: int) -> SearchGroup:
    return SearchGroup(id="role", kind="role", label="Intitulés du métier", terms=titles[:cap], priority=0, protected=True, why=why)


def _skill_group(r: Req, limit: int, priority: int, protected: bool, why: str) -> SearchGroup:
    # un impératif / éliminatoire n'est jamais « secondaire » : l'optimisation ne le retire pas (§5.7)
    protected = protected or r.category in (Category.IMPERATIF, Category.ELIMINATOIRE)
    return SearchGroup(id=r.key, kind="skill", label=r.label, terms=boolean_terms(r, limit), priority=priority,
                       protected=protected, req_key=r.key, category=r.category.value, rarity=_rarity(r), why=why)


CATEGORY_PRIORITY = {Category.ELIMINATOIRE.value: 1, Category.IMPERATIF.value: 2, Category.DIFFERENCIANT.value: 4,
                     Category.SOUHAITABLE.value: 6}


def variant_from_query(query: str, reqs: list[Req], title: str, strategy: str = "balanced") -> SearchVariant:
    """Reconstruit des groupes priorisés depuis une requête (collée/éditée à la main).

    Chaque terme est rattaché à l'exigence qu'il désigne ; un groupe sans exigence connue est
    traité comme secondaire (non protégé). Le premier groupe qui désigne le métier est protégé.
    """
    node, _ = bl.parse(query)
    items = list(node.items) if isinstance(node, Group) and node.op == "AND" else ([node] if node is not None else [])
    fam = lx.detect_role_family(title)
    role_terms = {fold(t) for t in ((*fam.titles, *fam.wide_titles) if fam else (title,))}
    groups: list[SearchGroup] = []
    negatives: list[str] = []
    for i, it in enumerate(items):
        if isinstance(it, Not):
            negatives += bl.terms_of(it)
            continue
        terms = bl.terms_of(it)
        folded = {fold(t) for t in terms}
        if folded & role_terms and not any(g.kind == "role" for g in groups):
            groups.append(SearchGroup(id="role", kind="role", label="Intitulés du métier", terms=terms, priority=0, protected=True,
                                      why="Groupe reconnu comme désignant le métier."))
            continue
        match = next((r for r in reqs if r.status == "active" and (folded & {fold(t) for t in r.terms} or fold(r.label) in folded)), None)
        if match:
            cat = match.category.value
            groups.append(SearchGroup(id=match.key, kind="skill", label=match.label, terms=terms,
                                      priority=CATEGORY_PRIORITY.get(cat, 5) + i * 0.01, protected=cat in (Category.IMPERATIF.value, Category.ELIMINATOIRE.value),
                                      req_key=match.key, category=cat, rarity=_rarity(match), why="Terme rattaché à l'exigence du brief."))
        else:
            groups.append(SearchGroup(id=f"free:{i}", kind="skill", label=" / ".join(terms[:2]), terms=terms, priority=7 + i * 0.01,
                                      protected=False, why="Terme ajouté manuellement, rattaché à aucune exigence du brief : traité comme secondaire."))
    if not any(g.kind == "role" for g in groups) and groups:
        groups[0].kind, groups[0].protected, groups[0].priority = "role", True, 0
    v = SearchVariant(strategy=strategy, groups=groups, negatives=negatives)
    return v.refresh()


def _drop_implied_parents(groups: list[SearchGroup]) -> tuple[list[SearchGroup], list[str]]:
    """« Kafka » est impliqué par « Kafka Connect » : l'imposer séparément est redondant.
    Un affineur dont un terme est déjà imposé ailleurs est lui aussi subsumé."""
    notes, out = [], list(groups)
    for n in [g for g in groups if g.kind == "narrower"]:
        for g in groups:
            if g is not n and g.kind == "skill" and {fold(t) for t in g.terms} & {fold(t) for t in n.terms}:
                if n in out:
                    out.remove(n)
                    notes.append(f"Affineur « {n.label} » non repris : « {g.label} » est déjà imposé.")
    for a, b in itertools.permutations([g for g in groups if g.kind == "skill"], 2):
        if a in out and b in out and a.terms and b.terms and all(any(_contains_phrase(tb, ta) or fold(tb) == fold(ta) for ta in a.terms) for tb in b.terms):
            if _CAT_STRENGTH.get(b.category or "", 0) >= _CAT_STRENGTH.get(a.category or "", 0):
                out.remove(a)
                notes.append(f"« {a.label} » n'est pas imposé séparément : « {b.label} » le contient déjà.")
    return out, notes


MAX_STRICT_GROUPS = 5      # au-delà, une conjonction est quasi certainement vide : configurable


def build_search_set(title: str, reqs: list[Req], *, extra_titles: list[str] | None = None,
                     profile: PlatformProfile = TURNOVER, k_balanced: int = 3, max_strict_groups: int = MAX_STRICT_GROUPS) -> SearchSet:
    active = [r for r in reqs if r.status == "active"]
    core, wide, fam_key, notes = role_titles(title, active, list(extra_titles or []))
    ranked, excluded = rank_requirements(active)
    mandatory = [r for r in ranked if r.category in (Category.IMPERATIF, Category.ELIMINATOIRE)]
    groups_req = [r for r in active if r.key.startswith("group:") and r.params.get("members")]
    member_keys_all = {m for gr in groups_req for m in gr.params["members"]}
    # Membres d'une combinaison imposée : jamais ANDés un à un en découverte (le client n'exige pas chacun) ;
    # ils forment un groupe « au moins une » en exploratoire/équilibrée, et la combinaison k/n n'est imposée qu'en stricte.
    strong_all = [r for r in ranked if r.category in (Category.IMPERATIF, Category.ELIMINATOIRE, Category.DIFFERENCIANT)]
    strong = [r for r in strong_all if r.key not in member_keys_all]
    union_groups: list[SearchGroup] = []
    for gr in groups_req:
        mem = [r for r in active if r.key in gr.params["members"] and boolean_terms(r, 1)]
        if mem:
            union_groups.append(SearchGroup(
                id=f"union:{gr.key}", kind="skill", label="Au moins une des technologies imposées en combinaison",
                terms=[t for r in mem for t in boolean_terms(r, 2)], priority=1, protected=True, req_key=gr.key,
                category=gr.category.value, rarity=max(_rarity(r) for r in mem),
                why=f"Le client impose l'expérience sur {gr.params.get('at_least', 1)} technologies parmi {len(mem)} : en découverte, au moins une doit apparaître ; la combinaison est vérifiée en stricte et au matching."))

    variants: dict[str, SearchVariant] = {}

    # ---- exploratoire : intitulés élargis + au moins UNE des compétences discriminantes
    top = (strong or ranked)[:3]
    ex_groups = [_role_group(core + wide, "Intitulés équivalents et variantes élargies de la famille de métier", 6)]
    union_terms: list[str] = []
    for r in top:
        union_terms += boolean_terms(r, 1)
    for ug in union_groups:
        union_terms += ug.terms[:3]
    if union_terms:
        ex_groups.append(SearchGroup(id="union", kind="skill", label="Au moins une compétence discriminante", terms=list(dict.fromkeys(union_terms)),
                                     priority=1, protected=True, rarity=max([_rarity(r) for r in top] + [ug.rarity for ug in union_groups] + [3]),
                                     why="Union (OR) des compétences les plus discriminantes : maximise la découverte ; le scoring vérifiera ensuite chaque exigence."))
    v = SearchVariant("exploratory", ex_groups)
    variants["exploratory"] = fit(v, profile)

    # ---- équilibrée : métier + K compétences discriminantes (AND), synonymes équivalents (OR)
    base_pool = strong or [r for r in ranked if r.key not in member_keys_all]
    chosen = base_pool[:max(1, k_balanced - len(union_groups))]
    if len(chosen) + len(union_groups) < 2:
        chosen = [r for r in ranked if r.key not in member_keys_all][:max(2 - len(union_groups), len(chosen))]
    bg = [_role_group(core, "Intitulés équivalents de la famille de métier", 4)]
    bg += [SearchGroup(**{**ug.__dict__, "terms": ug.terms[:4]}) for ug in union_groups]
    for i, r in enumerate(chosen):
        bg.append(_skill_group(r, 3 if i == 0 else 2, priority=i + 2, protected=False,
                               why=f"{r.category.value.replace('_', ' ')} et discriminant (rareté {_rarity(r)}/5) : distingue le métier recherché d'un métier voisin."))
    bg, n2 = _drop_implied_parents(bg)
    notes += n2
    variants["balanced"] = fit(SearchVariant("balanced", bg), profile)

    # ---- stricte : toutes les exigences impératives/éliminatoires + affineurs ; groupes k parmi n
    sg = [_role_group(core, "Intitulés équivalents de la famille de métier", 3)]
    pr = 1
    member_keys = {m for gr in groups_req for m in gr.params["members"]}
    for r in mandatory:
        if r.key in member_keys or r.params.get("group"):
            continue
        sg.append(_skill_group(r, 2, priority=pr, protected=True, why="Exigence impérative : imposée en recherche stricte."))
        pr += 1
        sk = lx.skill(r.key.split(":", 1)[1]) if ":" in r.key else None
        if r.depth_required == "advanced" and sk and sk.narrowers:
            sg.append(SearchGroup(id=f"narrow:{r.key}", kind="narrower", label=f"{r.label} — pratique avancée",
                                  terms=list(sk.narrowers[:3]), priority=pr, protected=False, req_key=r.key, category=r.category.value,
                                  rarity=5, why="Profondeur avancée exigée : au moins un indice de pratique poussée (le mot-clé seul ne la prouve pas)."))
            pr += 1
    sg, n3 = _drop_implied_parents(sg)
    notes += n3
    capped: list[str] = []
    imposed = [g for g in sg if g.kind in ("skill", "narrower")]
    if len(imposed) > max_strict_groups:
        keep = sorted(imposed, key=lambda g: (-_CAT_STRENGTH.get(g.category or "", 0), -g.rarity, g.priority))[:max_strict_groups]
        keep_ids = {g.id for g in keep}
        for g in imposed:
            if g.id not in keep_ids:
                capped.append(f"Impératif « {g.label} » non cherché en stricte (limite de {max_strict_groups} groupes imposés, rareté {g.rarity}/5) : "
                              "il reste exigé par la grille et vérifié au matching.")
        sg = [g for g in sg if g.kind not in ("skill", "narrower") or g.id in keep_ids]
    strict = SearchVariant("strict", sg, trimmed=capped)
    for gr in groups_req:
        members = [r for r in active if r.key in gr.params["members"]]
        member_nodes = [OR(*[Term(t) for t in boolean_terms(m, 2)]) for m in members if boolean_terms(m, 2)]
        k = int(gr.params.get("at_least", 1))
        if len(member_nodes) < k or k < 1:
            continue
        qs = k_of_n(member_nodes, k)
        base_nodes = [g.node() for g in sg if g.node() is not None]
        single_text = bl.render(OR(*qs))
        if len(bl.render(AND(*base_nodes, OR(*qs)))) <= profile.max_length:
            combo_text, split = single_text, False
        else:                                       # une requête par ancre ; la 1re reste la requête principale
            combo_text, split = bl.render(qs[0]), True
        strict.groups.append(SearchGroup(
            id=gr.key, kind="combo", label=gr.label, terms=[], priority=1, protected=True, req_key=gr.key,
            category=gr.category.value, rarity=5, combo_text=combo_text, alternatives=[bl.render(q) for q in qs[1:]] if split else [],
            why="Combinaison imposée par le client (§5.5)" + (", répartie en plusieurs requêtes complémentaires." if split else "."),
        ))
    variants["strict"] = fit(strict, profile)

    for strat, var in variants.items():
        var.explanation = explain(var, title=title, reqs=active, notes=notes, excluded=excluded, core=core, profile=profile)
    return SearchSet(title=title, role_family=fam_key, variants=variants, notes=notes, excluded=excluded)


# ----------------------------------------------------------------- explications
def _volume_risks(v: SearchVariant) -> tuple[str, str]:
    ands = [g for g in v.groups if g.kind != "role"]
    rar5 = sum(1 for g in ands if g.rarity >= 5)
    n = len(ands) + len(v.extra_queries and [1] or [])
    low = "élevé" if n >= 4 or (n >= 3 and rar5) or (n >= 2 and rar5 >= 2) else ("modéré" if n == 3 or rar5 else "faible")
    high = "élevé" if v.strategy == "exploratory" or n <= 1 else ("modéré" if n == 2 else "faible")
    return low, high


def explain(v: SearchVariant, *, title: str, reqs: list[Req], notes: list[str], excluded: list[dict[str, str]],
            core: list[str], profile: PlatformProfile) -> dict[str, Any]:
    in_search = {g.req_key for g in v.groups if g.req_key} | {m for g in v.groups for m in (next((r.params.get("members", []) for r in reqs if r.key == g.req_key), []))}
    skipped = []
    for r in reqs:
        if r.key in in_search or r.key.startswith("group:"):
            continue
        if any(e["skill"] == r.label for e in excluded):
            continue
        if r.dimension == "contrainte" or r.kind in ("constraint", "years", "language"):
            continue
        why = {"souhaitable": "Souhaitable : non imposé pour préserver le rappel, vérifié au matching.",
               "fortement_differenciant": "Différenciant mais non imposé dans cette stratégie : vérifié au matching.",
               "imperatif": "Impératif non repris dans cette stratégie : couvert par le scoring (impératif non compensable).",
               }.get(r.category.value, "Non imposé dans cette stratégie.")
        skipped.append({"skill": r.label, "reason": why})
    low, high = _volume_risks(v)
    imposed = [{"term": g.label, "terms": g.terms, "why": g.why, "category": g.category} for g in v.groups if g.kind in ("skill", "narrower", "combo") and g.id != "union"]
    synonyms = [{"group": g.label, "terms": g.terms, "why": "Alternatives réellement équivalentes (OR)."} for g in v.groups if len(g.terms) > 1]
    fp = ["Un mot-clé présent dans un CV ne prouve pas une expérience effective : la présence textuelle doit être confirmée par le matching (preuves et niveau de preuve)."]
    fn = ["Un bon profil dont le CV formule autrement (accents, anglais/français, abréviation, intitulé différent) peut être manqué."]
    if v.strategy == "exploratory":
        fp.append("Tout profil du métier mentionnant au moins une des compétences ressortira : volume potentiellement élevé, à trier par le scoring.")
    if any(g.kind == "role" and len(g.terms) >= 5 for g in v.groups):
        fp.append("Des intitulés élargis peuvent ramener des métiers voisins (voir les métiers voisins de la famille).")
    for g in v.groups:
        sk = lx.skill(g.req_key.split(":", 1)[1]) if g.req_key and ":" in g.req_key else None
        if sk and sk.confusable_with:
            conf = ", ".join(lx.skill(c).label for c in sk.confusable_with if lx.skill(c))
            if conf:
                fp.append(f"« {sk.label} » peut être confondu avec : {conf}.")
        if sk and sk.note:
            fp.append(sk.note)
    n_and = sum(1 for g in v.groups if g.kind != "role")
    if n_and >= 3:
        fn.append(f"{n_and} groupes imposés simultanément : un CV pertinent qui omet l'un d'eux est exclu.")
    if v.negatives:
        fn.append("Les exclusions (NOT) peuvent retirer des CV pertinents mentionnant ces termes dans un autre contexte.")
    warnings = list(v.trimmed)
    if not v.fits:
        warnings.append(f"Longueur {len(v.query)} > {profile.max_length} caractères.")
    if low == "élevé":
        warnings.append("Risque de faible volume : prévoir les variantes plus ouvertes (équilibrée / exploratoire) ; ne pas retirer l'exigence client de la grille.")
    if v.strategy == "strict":
        warnings.append("Une recherche stricte n'est pas nécessairement la meilleure : à utiliser quand la présence des technologies imposées est indispensable.")
        if v.extra_queries:
            warnings.append(f"La combinaison imposée est répartie en {1 + len(v.extra_queries)} requêtes complémentaires à exécuter toutes.")
    warnings.append(profile.notes)
    return {
        "titles_kept": [{"term": t, "why": "Intitulé équivalent" if i < len(core) else "Variante élargie"}
                        for g in v.groups if g.kind == "role" for i, t in enumerate(g.terms)],
        "imposed_technologies": imposed,
        "synonyms_used": synonyms,
        "excluded_skills": excluded + skipped,
        "false_positive_risks": fp,
        "false_negative_risks": fn,
        "low_volume_risk": low,        # qualitatif : aucune estimation du nombre de résultats (§5.2)
        "high_volume_risk": high,
        "warnings": warnings,
        "notes": notes,
        "length": len(v.query), "max_length": profile.max_length,
        "syntax_confirmed": profile.syntax_confirmed,
    }
