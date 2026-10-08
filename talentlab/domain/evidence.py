"""Détection et classification des preuves d'un critère (§6.5, §6.6).

Règle centrale : une mention n'est pas une démonstration. Le niveau est déterminé
par *où* le terme apparaît et par les signaux *factuels* de son contexte (verbe
d'action, rôle exact, composants précis, volumes chiffrés, résultats, livrables).
La qualité rédactionnelle (adjectifs, superlatifs, « expert reconnu ») n'est
jamais un signal (test 13).
"""
from __future__ import annotations

import re
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import clauses as cl
from . import lexicon as lx
from .cv_extract import Experience, ParsedCV, YM
from .enums import EvidenceKind, EvidenceSource, Level, Reliability, LEVEL_ORDER
from .text import Span, fold, sentences, term_pattern

# ----------------------------------------------------------------- lexiques de signaux (texte replié, sans accent)
_STRONG_ACTION = re.compile(
    r"\b(?:conc(?:u|evoir|eption)|develop(?:pe|pes|per|pement)|implement\w*|(?:mis|mise|mettre)\s+en\s+(?:place|oeuvre|œuvre|production|service)|deploy\w*|deploi\w*|configur\w*|parametr\w*|"
    r"administr\w*|migr(?:e|er|ation|ations)\b|optimis\w*|industrialis\w*|automatis\w*|constru(?:it|ire|isant)|cre(?:e|er|ation)\b|redig\w*|"
    r"pilot\w*|anim(?:e|er|ation)\b|audit(?:e|er)?\b|diagnostiqu\w*|resolu\w*|resolution|supervis\w*|integr(?:e|er|ation)\b|realis\w*|"
    r"defini\w*|elabor\w*|structur(?:e|er)\b|encadr\w*|manag(?:e|er)\b|traitement|analyse|expertise|"
    r"install\w*|programm\w*|maintenance|maintien|maintenir|refactor\w*|upgrade\w*|livr\w*|gestion\s+(?:de|du|des|d')|"
    r"designed?|built|implemented|developed|deployed|configured|administered|migrated|led|architected|authored|set\s+up|maintained|"
    r"created|integrated|operated|optimi[sz]ed|automated|ran|wrote|coded|engineered|installed|provisioned|tuned|upgraded|refactored|shipped|scaled|"
    r"monitored|troubleshot|managed|delivered|owned)\b")
# « expertise », « analyse », « traitement », « audit » : substantifs génériques, pas un geste technique sur un produit
_GENERIC_ONLY = re.compile(r"\b(?:traitement|analyse|expertise|audit(?:e|er)?)\b")
_WEAK_ACTION = re.compile(r"\b(?:utilis\w*|usage|exploit\w*|emploi|travaille\s+(?:avec|sur)|used|using)\b")
_PARTICIPANT = re.compile(r"\b(?:particip\w*|contribu\w*|assist(?:e|er|ance|ant)\b|aid(?:e|er)\b(?!\s+(?:a|au)\s+(?:la\s+)?(?:decision|pilotage|choix))|appui|soutien|membre\s+de\s+l'?equipe|"
                          r"sous\s+la\s+responsabilite|support\s+aupres|implique\w*\s+(?:dans|sur|au|a)\b|intervention\s+(?:sur|dans|au|a)\b|interven\w+\s+(?:sur|dans)\b|"
                          r"collaborat\w*\s+(?:a|au|aux)\b|au\s+sein\s+de\s+l'equipe\s+(?:qui|en\s+charge)|"
                          r"participated|contributed|assisted|part\s+of\s+the\s+team|involved\s+in|helped|helping|exposure\s+to|worked\s+alongside)\b")
_OWNER = re.compile(r"\b(?:en\s+charge|responsable|referent|garant|seul\b|porteur|decisionnaire|owner|initiateur|j'ai\s+(?:conc|defini|mis|pilot|cree|construit)|"
                    r"leader|lead\s+technique|tech\s+lead|responsible\s+for|sole)\b")
_QUANT = re.compile(
    r"\b\d+(?:[\s.,]\d{3})*(?:[.,]\d+)?\s*(?:k|m|millions?|milliards?|milliers?|%|tps|tx|msg|messages?|transactions?|requetes?|utilisateurs?|equipes?|"
    r"developpeurs?|personnes?|pays|sites?|marques?|applications?|microservices?|topics?|partitions?|serveurs?|baies?|clients?|to|go|tb|gb|po|"
    r"projets?|produits?|composants?|connecteurs?|flux|schemas?|pipelines?|jobs?|clusters?|brokers?|lun|instances?|environnements?|regles?|"
    r"scenarios?|incidents?|tickets?|endpoints?|enregistrements?|tables?|vm|jours?|mois|ans)\b|\b(?:plusieurs|des)\s+(?:millions|milliers|centaines)\b")
_RESULT = re.compile(r"\b(?:reduction|reduit|diminu\w*|gain|ameliorat\w*|ameliore\w*|augment\w*|econom\w*|passe\s+de|disponibilite\s+de|taux\s+de|"
                     r"reduced|improved|increased|saved)\b|\b\d+(?:[.,]\d+)?\s*%|99[.,]\d")
_DELIVERABLE = re.compile(r"\b(?:dossier\s+d'?architecture|documentation|documents?|plan\s+de|runbook|cartographie|rapports?|specifications?|cahier|"
                          r"procedures?|guides?|plan\s+d'action|livrables?|referentiels?|catalogue|kpi|tableaux?\s+de\s+bord|regles?|pv\s+de)\b")
# travail d'INGÉNIERIE décrit (règle de pratique courante) : ni « test », ni « support » (un testeur dont Java figure dans l'environnement ne développe pas en Java),
# ni « équipes de développement » (nom d'équipe, pas un geste)
_WORKISH = re.compile(r"(?<!equipes de )(?<!equipe de )(?<!service de )(?<!pole )(?<!departement de )\b(?:develop\w*|conc\w*|maintenance|maintien|evolutions?|corrections?|realis\w*|integr\w*|implement\w*|mise\s+en\s+place|mis\s+en\s+place|"
                      r"administr\w*|exploit\w*|deploy\w*|configur\w*|migr\w*|built|developed|designed|implemented|maintained)\b")
_TEST_PHRASE = re.compile(r"\btests?\s+d(?:e\s+|')\w+")        # « tests d'intégration » : du test, pas de l'intégration
_CLAUSE_BREAK = re.compile(r";|\bet\b|\bpuis\b|\bainsi\s+que\b|\band\b")
_VERSION = re.compile(r"\b[a-z][\w.+#-]*\s*v?\d+(?:\.\d+){0,2}\b")


# ----------------------------------------------------------------- structures
@dataclass
class EvItem:
    source: str                          # "cv" | "call" | …
    excerpt: str
    start: int
    end: int
    location: str                        # experience | skills_list | env_list | header | summary | training | other | call
    level: Level
    reliability: Reliability = Reliability.DOCUMENTED_CV
    experience_idx: int | None = None
    company: str = ""
    period: str = ""
    page: int | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    evidence_id: str = ""
    validated: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["level"] = self.level.value
        d["reliability"] = self.reliability.value
        return d


@dataclass
class ExtEvidence:
    """Preuve issue d'un appel, d'un entretien, d'un document complémentaire ou d'une saisie du recruteur."""
    id: str
    subject_key: str
    kind: EvidenceKind
    excerpt: str
    source: EvidenceSource
    reliability: Reliability
    level: Level | None = None           # niveau local déterminé à l'extraction (supporte)
    validated: bool = False
    auto_generated: bool = False
    date: date | None = None
    speaker: str = ""
    superseded: bool = False


@dataclass
class CritEvidence:
    level: Level
    items: list[EvItem]
    justification: str
    notes: list[str] = field(default_factory=list)
    last_used: str | None = None
    months_practice: int | None = None
    advanced_found: list[str] = field(default_factory=list)
    advanced_missing: list[str] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    recency_downgraded: bool = False
    capped_by: list[str] = field(default_factory=list)
    experience_idxs: list[int] = field(default_factory=list)


class AnalysisTimeout(Exception):
    """L'analyse d'un document a dépassé son budget de temps (contenu anormal : répétitif, démesuré…)."""


@dataclass
class EvalConfig:
    deadline: float | None = None          # échéance monotone (time.monotonic) ; vérifiée entre les critères et toutes les 16 mentions
    today: date = field(default_factory=date.today)
    recency_window_years: int = 7
    advanced_min_terms: int = 2
    commodity_rarity: int = 3
    commodity_min_experiences: int = 2


# ----------------------------------------------------------------- éléments structurels
def _items(text: str, a: int, b: int) -> list[tuple[int, int]]:
    """Découpe une zone en éléments (puces / paragraphes), en recollant les lignes enveloppées."""
    out: list[tuple[int, int]] = []
    pos = a
    cur_s, cur_e = None, None
    prev_line = ""
    for raw in text[a:b].split("\n"):
        ls, le = pos, pos + len(raw)
        pos = le + 1
        line = raw.strip()
        if not line:
            if cur_s is not None:
                out.append((cur_s, cur_e))  # type: ignore[arg-type]
            cur_s = cur_e = None
            prev_line = ""
            continue
        bullet = bool(re.match(r"^\s*[-•*·▪►→]", raw))
        cont = (cur_s is not None and not bullet and not prev_line.rstrip().endswith((".", ";", ":", "!", "?"))
                and re.match(r"^[a-zà-ÿ0-9(&,]", line) is not None)
        if cont:
            cur_e = le
        else:
            if cur_s is not None:
                out.append((cur_s, cur_e))  # type: ignore[arg-type]
            cur_s, cur_e = ls, le
        prev_line = line
    if cur_s is not None:
        out.append((cur_s, cur_e))  # type: ignore[arg-type]
    return out


_CANON = {"producteur": "producer", "consommateur": "consumer", "brokers": "broker", "debit": "throughput", "volumetrie": "throughput"}


def _stem(term: str) -> str:
    """Forme canonique d'un indice : « topic »/« topics », « partition »/« partitions », « producteur »/« producer » comptent UNE fois."""
    out = []
    for w in re.split(r"[\s\-_]+", fold(term).strip()):
        w = _CANON.get(w, w)
        if len(w) > 3 and w.endswith(("s", "x")):
            w = w[:-1]
        out.append(_CANON.get(w, w))
    return " ".join(out)


def _dedupe_stems(terms: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for t in terms:
        seen.setdefault(_stem(t), t)
    return sorted(seen.values(), key=str.lower)


def _distinct(rx_or_terms: Any, folded: str) -> list[str]:
    if isinstance(rx_or_terms, re.Pattern):
        return sorted({m.group(0).strip() for m in rx_or_terms.finditer(folded)})
    return _dedupe_stems([t for t in rx_or_terms if re.search(term_pattern(t), folded)])


@dataclass
class Signals:
    action: str                      # strong | weak | none
    verbs: list[str]
    role: str                        # owner | participant | neutral
    depth: list[str]
    quant: list[str]
    results: list[str]
    deliverables: list[str]
    non_pro: bool
    negated: bool = False            # la proposition qui porte la mention la NIE : aucun crédit
    caps: list[cl.Cap] = field(default_factory=list)
    stack_list: bool = False         # énumération de technologies sans réalisation

    def points(self, kind: str) -> int:
        p = min(3, len(self.depth)) + (1 if self.quant else 0) + (1 if self.results else 0) + (1 if self.deliverables else 0)
        p += 1 if self.role == "owner" else 0
        p += 1 if len(set(self.verbs)) >= 2 else 0
        return p

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "verbs": self.verbs[:4], "role": self.role, "depth_terms": self.depth[:8],
                "quantified": self.quant[:3], "results": self.results[:3], "deliverables": self.deliverables[:3], "non_professional": self.non_pro,
                "scope": [c.code for c in self.caps], "negated": self.negated, "stack_list": self.stack_list}


def analyze_window(win: str, sk: lx.Skill | None, *, own_sentence: str | None = None, mention_pos: int | None = None,
                   item_text: str | None = None, today: date | None = None, kind: str = "skill") -> Signals:
    """Signaux factuels d'une fenêtre autour d'une mention.

    L'ACTION se lit dans la proposition qui porte la mention (jamais dans la phrase voisine) ; la profondeur, les chiffres, les résultats et
    les livrables se lisent dans la fenêtre, MOINS les propositions niées, futures, tierces, de formation ou personnelles
    (leurs verbes et leurs chiffres ne comptent pas)."""
    flat = cl.flatten(win)
    f = fold(flat)
    bounds = cl.clause_bounds(f)
    if mention_pos is None:
        o0, o1 = 0, len(f)
        own_text = fold(cl.flatten(own_sentence)) if own_sentence is not None else f
    else:
        o0, o1 = cl.clause_of(bounds, max(0, min(mention_pos, len(f) - 1)))
        own_text = f[o0:o1]
    neg = cl.negated_regions(f)
    negated = mention_pos is not None and any(a <= mention_pos < b for a, b in neg)
    caps = cl.caps_in(own_text, today=today) + cl.item_caps(fold(cl.flatten(item_text)) if item_text else f, today=today)
    regions = list(neg)
    for a, b in bounds:
        if (a, b) != (o0, o1) and cl.caps_in(f[a:b], today=today):
            regions.append((a, b))
    fm = cl.mask(f, regions)
    own_m = fm[o0:o1] if mention_pos is not None else cl.mask(own_text, [])
    strong_rx = _STRONG_ACTION
    verbs = _distinct(strong_rx, fm)
    own_strong = strong_rx.search(own_m)
    if own_strong and kind == "skill" and _GENERIC_ONLY.fullmatch(own_strong.group(0).strip()) and len(verbs) == 1:
        own_strong = None                         # « audit », « analyse », « expertise » seuls ne sont pas un geste technique
    carried = False
    if not own_strong and mention_pos is not None and re.match(r"\s*(?:technolog\w*|environnement\w*|stack\w*|outils?|avec|using|via|built\s+with|langages?)\b", own_m):
        carried = bool(_STRONG_ACTION.search(fm[:o0]))   # « Conception du pipeline. Technologies : Kafka » : rattaché à la réalisation, sans rôle exprimé
    if own_strong:
        action = "strong"
    elif carried:
        action = "weak"
    else:
        action = "weak" if _WEAK_ACTION.search(own_m) else "none"
    if action == "none" and kind == "skill" and mention_pos is not None and not stack_early(flat, f, o0, o1) and not caps:
        # style nominal (« Streaming temps réel avec Kafka Streams : agrégations sur 5 topics, 100 000 événements par jour ») : sans verbe, mais chiffré et détaillé
        if sk and len(_distinct(sk.depth_terms, own_m)) >= 2 and _distinct(_QUANT, own_m):
            action = "weak"
    role = "participant" if _PARTICIPANT.search(own_m) else ("owner" if _OWNER.search(fm) else "neutral")
    depth = _distinct(sk.depth_terms, fm) if sk else []
    stack = bool(mention_pos is not None and cl.is_stack_list(flat[o0:o1], f[o0:o1]))
    return Signals(action=action, verbs=verbs, role=role, depth=depth, quant=_distinct(_QUANT, fm), results=_distinct(_RESULT, fm),
                   deliverables=_distinct(_DELIVERABLE, fm), non_pro=any(c.code == "personal" for c in caps), negated=negated, caps=caps, stack_list=stack)


def stack_early(flat: str, f: str, o0: int, o1: int) -> bool:
    return cl.is_stack_list(flat[o0:o1], f[o0:o1])


def classify(sg: Signals, sk: lx.Skill | None, kind: str, *, role_sensitive: bool = True) -> tuple[Level, str]:
    """Niveau local d'une fenêtre. ``kind`` : skill | activity | domain."""
    if sg.negated:
        return Level.NOT_DOCUMENTED, "le texte écarte explicitement cette expérience (négation) : aucun crédit"
    lvl, why = _classify_core(sg, sk, kind, role_sensitive=role_sensitive)
    cap = cl.strongest(sg.caps)
    if cap and LEVEL_ORDER[cap.level] <= LEVEL_ORDER[lvl]:      # à niveau égal, la raison de portée est la plus informative
        return cap.level, cap.reason
    if sg.stack_list and LEVEL_ORDER[Level.DECLARED] < LEVEL_ORDER[lvl]:
        return Level.DECLARED, "énumération de technologies sans réalisation décrite (liste de compétences présentée sous une étiquette)"
    return lvl, why


def _classify_core(sg: Signals, sk: lx.Skill | None, kind: str, *, role_sensitive: bool = True) -> tuple[Level, str]:
    pts = sg.points(kind)
    part_cap = role_sensitive and sg.role == "participant"
    if kind == "activity":
        n = len(sg.depth)
        if sg.action == "none":
            return Level.DECLARED, "activité nommée sans verbe d'action ni réalisation décrite"
        if n >= 3 or (n >= 2 and pts >= 3):
            return (Level.PARTIAL, "participation seulement (rôle exact à préciser)") if part_cap else (Level.CONFIRMED, f"{n} éléments d'activité précis ({', '.join(sg.depth[:4])})")
        if n == 2 or (n == 1 and pts >= 3):
            return Level.PARTIAL, f"activité partiellement décrite ({', '.join(sg.depth[:3])})"
        return Level.DECLARED, "activité nommée sans réalisation précise"
    if kind == "domain":
        if sg.action in ("strong",) and sg.depth:
            return (Level.PARTIAL, "participation à une réalisation du domaine") if part_cap else ((Level.CONFIRMED, "réalisation précise dans le domaine") if pts >= 3 else (Level.PARTIAL, "réalisation du domaine peu détaillée"))
        return Level.DECLARED, "domaine mentionné (entreprise, secteur ou liste) sans réalisation applicative décrite"
    # technologies / produits
    if sg.action == "strong":
        if part_cap:
            return Level.PARTIAL, "participation (rôle exact non établi) — « participer » n'est pas « concevoir / piloter »"
        if pts >= 3:
            return Level.CONFIRMED, f"réalisation précise : {_why(sg)}"
        return Level.PARTIAL, "pratique décrite mais peu détaillée" + (f" ({_why(sg)})" if pts else "")
    if sg.action == "weak":
        return (Level.PARTIAL, f"usage décrit avec précisions ({_why(sg)})") if pts >= 2 else (Level.DECLARED, "simple usage, sans réalisation décrite")
    return Level.DECLARED, "mention sans verbe d'action ni réalisation décrite"


def _why(sg: Signals) -> str:
    bits = []
    if sg.depth:
        bits.append("composants : " + ", ".join(sg.depth[:4]))
    if sg.quant:
        bits.append("chiffres : " + ", ".join(sg.quant[:2]))
    if sg.results:
        bits.append("résultats : " + ", ".join(sg.results[:2]))
    if sg.deliverables:
        bits.append("livrables : " + ", ".join(sg.deliverables[:2]))
    if sg.role == "owner":
        bits.append("responsabilité explicite")
    return " ; ".join(bits)


def _adhoc_skill(terms: list[str], label: str) -> lx.Skill:
    al = tuple(dict.fromkeys(terms or [label]))
    return lx.Skill(key="adhoc", label=label or (terms[0] if terms else ""), kind="tech", aliases=al)


def skill_for(key: str, terms: list[str], label: str) -> lx.Skill:
    sk = lx.skill(key.split(":", 1)[1]) if ":" in key else None
    if sk is None:
        return _adhoc_skill(terms, label)
    covered = {a for ck in sk.umbrella_of if (child := lx.skill(ck)) is not None for a in child.aliases}      # les gammes gardent leurs propres garde-fous d'homonymie
    extra = [t for t in terms if t not in sk.aliases and t not in covered]
    if extra:       # alias complémentaires (gammes repliées dans leur constructeur…)
        from dataclasses import replace
        return replace(sk, aliases=tuple(dict.fromkeys([*sk.aliases, *extra])))
    return sk


# ----------------------------------------------------------------- période d'une expérience
def _period(e: Experience | None) -> str:
    if not e:
        return ""
    if e.start and e.is_current:
        return f"{e.start} → en cours"
    if e.start and e.end:
        return f"{e.start} → {e.end}"
    return "dates non précisées"


def _ym_ord(iso: str | None, default_month: int = 7) -> int | None:
    if not iso:
        return None
    y, _, m = iso.partition("-")
    return int(y) * 12 + (int(m) if m else default_month) - 1


def _years_since_end(e: Experience | None, today: date) -> float | None:
    if e is None or e.dates_unknown:
        return None
    if e.is_current:
        return 0.0
    o = _ym_ord(e.end)
    return None if o is None else max(0.0, (today.year * 12 + today.month - 1 - o) / 12)


def advanced_terms_in(sk: lx.Skill, excerpt: str, *, participant: bool = False) -> set[str]:
    """Indices de profondeur avancée présents dans un extrait, avec les garde-fous du référentiel :
    « volumétrie » ne compte que si le débit chiffré est élevé ; « architecture » ne compte pas si le rôle n'était que participant."""
    f_it = fold(excerpt)
    found: set[str] = set()
    for t in sk.advanced_terms:
        if not re.search(term_pattern(t), f_it):
            continue
        tf = fold(t)
        if tf in _VOLUME_TERMS and not ((volume_per_day(excerpt) or 0) >= HIGH_VOLUME_PER_DAY):
            continue
        if tf in _OWNERSHIP_TERMS and participant:
            continue
        found.add(t)
    return found


# ----------------------------------------------------------------- évaluation d'un critère
def _location(parsed: ParsedCV, pos: int, exp: Experience | None) -> str:
    if exp is not None:
        if any(a <= pos < b for a, b in exp.env_spans):
            return "env_list"
        head_end = exp.body_span[0]
        if exp.span[0] <= pos < head_end:
            return "header"
        return "experience"
    sec = parsed.section_of(pos)
    return {"skills": "skills_list", "summary": "summary", "education": "training", "certifications": "training", "interests": "other",
            "languages": "other", "experience": "experience"}.get(sec or "", "other")


def evaluate_text_criterion(parsed: ParsedCV, key: str, label: str, terms: list[str], *, kind: str = "skill",
                            depth_required: str = "practice", scope_terms: list[str] | None = None,
                            recency_sensitive: bool | None = None, recency_window_years: int | None = None,
                            cfg: EvalConfig | None = None, versions: list[str] | None = None) -> CritEvidence:
    cfg = cfg or EvalConfig()
    sk = skill_for(key, terms, label)
    if sk.key == "high_volume":
        return _evaluate_volume(parsed, label, scope_terms or [], cfg)
    text = parsed.text
    folded = fold(text)
    scope_terms = scope_terms or []
    role_sensitive = sk.key != "run_n3"
    hits: list[tuple[int, int, str]] = list(lx.mentions(sk, text, folded))
    for ck in sk.umbrella_of:                      # « Dell EMC (PowerStore, PowerMax, Unity) » : chaque gamme est cherchée avec SES garde-fous
        if (child := lx.skill(ck)) is not None:
            hits += list(lx.mentions(child, text, folded))
    if sk.umbrella_of:
        hits.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        pruned: list[tuple[int, int, str]] = []
        for h in hits:
            if pruned and h[0] >= pruned[-1][0] and h[1] <= pruned[-1][1]:
                continue
            pruned.append(h)
        hits = pruned
    items: list[EvItem] = []
    if kind == "activity" and sk.depth_terms:      # une activité se prouve aussi par ses composants, sans que son nom apparaisse
        for exp in parsed.experiences:
            for a, b in _items(text, exp.body_span[0], exp.body_span[1]):
                if len(_distinct(sk.depth_terms, folded[a:b])) >= 2 and not any(a <= h[0] < b for h in hits):
                    hits.append((a, a + 1, "(activité décrite)"))
    seen_clauses: set[tuple[int, int]] = set()
    negated_items: list[tuple[int, int, str]] = []
    blocks_cache: dict[tuple[int, int], list[tuple[int, int]]] = {}
    block_info: dict[tuple[int, int], tuple[str, str, list[tuple[int, int]], list[tuple[int, int]]]] = {}
    processed = 0
    truncated = False
    for hs, he, alias in sorted(hits):
        exp = parsed.experience_at(hs)
        loc = _location(parsed, hs, exp)
        if loc in ("skills_list", "env_list", "summary", "training", "other", "header"):
            sent = _sentence_at(text, hs)
            note = {"skills_list": "mentionné dans la liste de compétences, sans projet associé",
                    "env_list": "mentionné dans l'environnement technique d'une mission, sans réalisation décrite",
                    "summary": "mentionné dans le profil / résumé (déclaration)",
                    "training": "formation ou certification : connaissance déclarée, pas une expérience en mission",
                    "header": "présent dans l'en-tête (entreprise / intitulé), sans réalisation décrite",
                    "other": "mention hors expérience"}[loc]
            if not any(i.start == sent.start and i.end == sent.end for i in items):
                items.append(EvItem("cv", sent.text, sent.start, sent.end, loc, Level.DECLARED, experience_idx=exp.idx if exp else None,
                                    company=exp.company if exp else "", period=_period(exp), note=note, signals={}))
            continue
        if processed >= MAX_MENTIONS_ANALYSED:
            truncated = True
            break
        if cfg.deadline is not None and processed % 16 == 0 and time.monotonic() > cfg.deadline:
            raise AnalysisTimeout()
        # réalisation : l'élément (puce / paragraphe) est l'unité ; fenêtre = phrase de la mention ± 1 dans cet élément
        bkey = (exp.body_span[0], exp.span[1]) if exp else (0, len(text))
        if bkey not in blocks_cache:
            blocks_cache[bkey] = _items(text, bkey[0], bkey[1])
        bl = blocks_cache[bkey]
        bi = bisect_right([x[0] for x in bl], hs) - 1
        blk = bl[bi] if bi >= 0 and bl[bi][0] <= hs < bl[bi][1] else (hs, max(he, hs + 1))
        if blk not in block_info:
            flat = cl.flatten(text[blk[0]:blk[1]])
            ff = fold(flat)
            block_info[blk] = (flat, ff, _sentence_bounds(flat), cl.clause_bounds(ff))
        flat, ff, sb, cb = block_info[blk]
        rel = hs - blk[0]
        si = next((i for i, (x, y) in enumerate(sb) if x <= rel < y + 1), 0)
        lo, hi = max(0, si - 1), min(len(sb), si + 2)
        w0, w1 = sb[lo][0], sb[hi - 1][1]
        c0, c1 = cl.clause_of(cb, rel)
        if (blk[0] + c0, blk[0] + c1) in seen_clauses:
            continue
        seen_clauses.add((blk[0] + c0, blk[0] + c1))
        processed += 1
        win = (blk[0] + w0, blk[0] + w1)
        sg = analyze_window(flat[w0:w1], sk, mention_pos=rel - w0, item_text=flat, today=cfg.today, kind=kind)
        lvl, why = classify(sg, sk, kind, role_sensitive=role_sensitive)
        if sg.negated:
            negated_items.append((win[0], win[1], text[win[0]:win[1]].strip()))
            continue
        if scope_terms and not any(re.search(term_pattern(t), fold(text[win[0]:win[1]])) for t in scope_terms):
            lvl = min(lvl, Level.PARTIAL, key=lambda l: LEVEL_ORDER[l])
            why += f" ; contexte demandé ({', '.join(scope_terms[:3])}) non retrouvé dans la réalisation"
        items.append(EvItem("cv", text[win[0]:win[1]].strip(), win[0], win[1], "experience", lvl, experience_idx=exp.idx if exp else None,
                            company=exp.company if exp else "", period=_period(exp), signals=sg.to_dict(), note=why))
    for it in items:
        it.page = None
    neg_notes: list[str] = []
    neg_contra: list[dict[str, Any]] = []
    for ns, ne, nt in negated_items[:3]:
        neg_notes.append(f"Le CV écarte explicitement cette expérience : « {nt[:160]} » (aucun crédit).")
        neg_contra.append({"type": "absence_declaree_cv", "statement": nt[:200], "resolution": "Absence déclarée par le CV lui-même : aucun crédit ; à confirmer en entretien."})
    if truncated:
        neg_notes.append(f"Plus de {MAX_MENTIONS_ANALYSED} mentions : seules les premières ont été analysées (un grand nombre d'occurrences n'est pas une preuve).")
    if not items:
        if negated_items:
            return CritEvidence(Level.NOT_DOCUMENTED, [], f"« {label} » : le CV indique explicitement ne pas avoir cette expérience — « {negated_items[0][2][:140]} ».", notes=neg_notes,
                                contradictions=neg_contra, open_questions=[f"Le CV écarte explicitement « {label} » : confirmer en entretien (aucune pratique à ce jour ?)."])
        return CritEvidence(Level.NOT_DOCUMENTED, [], f"Aucune mention de « {label} » dans le CV.", open_questions=[f"Le CV ne mentionne pas « {label} » : quelle expérience concrète en as-tu ?"])
    best = max(items, key=lambda i: (LEVEL_ORDER[i.level], -(_years_since_end(parsed.experiences[i.experience_idx], cfg.today) or 99) if i.experience_idx is not None else -99))
    level = best.level
    notes: list[str] = list(neg_notes)
    capped: list[str] = []
    # ---- pratique répétée d'une compétence banale (Java…) : la profondeur chiffrée n'est pas exigée à chaque mission
    practice_exps = sorted({i.experience_idx for i in items if i.location == "experience" and LEVEL_ORDER[i.level] >= LEVEL_ORDER[Level.PARTIAL] and i.experience_idx is not None})
    if (sk.rarity <= cfg.commodity_rarity and kind == "skill" and len(practice_exps) >= cfg.commodity_min_experiences and level == Level.PARTIAL
            and not any(i.signals.get("role") == "participant" for i in items)):
        level = Level.CONFIRMED
        notes.append(f"Pratique répétée sur {len(practice_exps)} missions datées (compétence courante : la profondeur chiffrée n'est pas exigée à chaque mission).")
    # ---- compétence banale (rareté ≤ 2) : intitulé / environnement + travail réel dans ≥ 2 missions datées
    if sk.rarity <= 2 and kind == "skill" and not any(i.signals.get("role") == "participant" for i in items):
        support: set[int] = set()
        for e in parsed.experiences:
            mentioned = any(i.experience_idx == e.idx for i in items) or bool(e.title and lx.mentions(sk, e.title))
            if mentioned and e.months and _WORKISH.search(_TEST_PHRASE.sub(" ", fold(text[e.body_span[0]:e.body_span[1]]))):
                support.add(e.idx)
        if len(support) >= 2 and LEVEL_ORDER[level] < LEVEL_ORDER[Level.CONFIRMED]:
            level = Level.CONFIRMED
            notes.append(f"Pratique sur {len(support)} missions datées (intitulé, environnement et travail de développement) : compétence courante, profondeur chiffrée non exigée à chaque mission.")
            practice_exps = sorted(set(practice_exps) | support)
        elif len(support) == 1 and level == Level.DECLARED:
            level = Level.PARTIAL
            notes.append("Intitulé ou environnement technique cohérent avec un travail réel sur une mission datée.")
            practice_exps = sorted(set(practice_exps) | support)
    # ---- corroboration : réalisation décrite + environnement technique, sur une mission longue
    if (sk.rarity <= cfg.commodity_rarity and best.location == "experience" and level == Level.PARTIAL and best.signals.get("action") == "strong"
            and best.signals.get("role") != "participant" and not best.signals.get("scope")):
        bexp = parsed.experiences[best.experience_idx] if best.experience_idx is not None else None
        if bexp and (bexp.months or 0) >= 24 and any(i.location == "env_list" and i.experience_idx == bexp.idx for i in items) and depth_required != "advanced":
            level = Level.CONFIRMED
            notes.append(f"Réalisation décrite et confirmée par l'environnement technique sur une mission de {bexp.months} mois.")
    # ---- profondeur avancée (§6.5) : la mention d'un composant avancé doit être retrouvée dans les réalisations
    adv_found: list[str] = []
    adv_missing: list[str] = []
    if depth_required == "advanced" and sk.advanced_terms:
        found: set[str] = set()
        for it in items:
            if it.location == "experience":
                found |= advanced_terms_in(sk, it.excerpt, participant=it.signals.get("role") == "participant")
        adv_found = _dedupe_stems(sorted(found, key=str.lower))
        uniq_found = {_stem(t) for t in adv_found}
        adv_missing = [t for t in sk.narrowers or sk.advanced_terms if _stem(t) not in uniq_found][:6]
        if len(uniq_found) < cfg.advanced_min_terms and LEVEL_ORDER[level] > LEVEL_ORDER[Level.PARTIAL]:
            level = Level.PARTIAL
            capped.append("profondeur avancée non démontrée")
            notes.append(f"Profondeur avancée exigée : {len(uniq_found)} indice(s) retrouvé(s) ({', '.join(adv_found) or 'aucun'}) ; manquent notamment {', '.join(adv_missing[:4])}.")
    # ---- version exigée par le besoin (« Java 17 ») : si le CV ne cite QUE des versions antérieures, le niveau est borné et l'écart nommé
    if versions and LEVEL_ORDER[level] > LEVEL_ORDER[Level.PARTIAL]:
        need = [int(re.match(r"\d+", v).group(0)) for v in versions if re.match(r"\d+", v)]      # type: ignore[union-attr]
        seen_v = {v for _hs, he_, _a in hits for v in _versions_after(folded, he_)}
        if need and seen_v and max(seen_v) < min(need):
            level = Level.PARTIAL
            capped.append("version exigée non retrouvée")
            notes.append(f"Version exigée par le besoin : {', '.join(versions)} ; versions citées dans le CV : {', '.join(str(v) for v in sorted(seen_v))} — écart à vérifier.")
    # ---- récence (test 12)
    exp_best = parsed.experiences[best.experience_idx] if best.experience_idx is not None and best.experience_idx < len(parsed.experiences) else None
    # la date de dernière pratique vient des éléments de PRATIQUE (au niveau retenu) : une mention récente dans un environnement technique
    # ne « rafraîchit » pas une pratique ancienne (elle ne prouve pas qu'on a continué à pratiquer)
    anchor = LEVEL_ORDER[Level.PARTIAL] if LEVEL_ORDER[level] >= LEVEL_ORDER[Level.PARTIAL] else LEVEL_ORDER[Level.DECLARED]
    candidates = [parsed.experiences[i.experience_idx] for i in items if i.experience_idx is not None and LEVEL_ORDER[i.level] >= anchor
                  and i.location in ("experience", "env_list") and i.experience_idx < len(parsed.experiences)]
    candidates += [parsed.experiences[i] for i in practice_exps if i < len(parsed.experiences)]      # missions où la règle de pratique courante a joué
    last = None
    for e in candidates:
        o = _ym_ord(e.end) if not e.is_current else cfg.today.year * 12 + cfg.today.month - 1
        if o is not None and (last is None or o > last[0]):
            last = (o, e)
    last_used = None
    downgraded = False
    if last:
        last_used = "en cours" if last[1].is_current else (last[1].end or None)
        sensitive = recency_sensitive if recency_sensitive is not None else sk.volatile
        window = recency_window_years or cfg.recency_window_years
        yrs = _years_since_end(last[1], cfg.today)
        if sensitive and yrs is not None and yrs > window and LEVEL_ORDER[level] >= LEVEL_ORDER[Level.PARTIAL]:
            level = Level.PARTIAL if level == Level.CONFIRMED else Level.DECLARED
            downgraded = True
            notes.append(f"Dernière pratique documentée : {last_used} (il y a {yrs:.0f} ans, fenêtre de récence {window} ans) : niveau abaissé — la récence compte pour ce besoin.")
    elif sk.volatile or recency_sensitive:
        notes.append("Récence non évaluable : aucune expérience datée associée à ce critère.")
    months = sum(parsed.experiences[i].months or 0 for i in practice_exps) or None
    just = _justify(level, best, label, notes)
    questions: list[str] = []
    return CritEvidence(level=level, items=sorted(items, key=lambda i: -LEVEL_ORDER[i.level])[:6], justification=just, notes=notes, last_used=last_used,
                        months_practice=months, advanced_found=adv_found, advanced_missing=adv_missing, open_questions=questions,
                        recency_downgraded=downgraded, capped_by=capped, experience_idxs=practice_exps)


_VER_LIST = re.compile(r"\s*(?:v|version\s*)?(\d{1,2}(?:\.\d+)*(?:\s*(?:,|/|et|and|ou|or|\+|->|→|vers|to)\s*\d{1,2}(?:\.\d+)*)*)(?![\d.]*\s*(?:ans|an|years?|mois|brokers?|topics?|partitions?|serveurs?|baies?|%))")


def _versions_after(folded: str, end: int) -> set[int]:
    """Versions majeures citées juste après un nom de produit : « Java 8, 17 », « Spring Boot 2.7 », « Java 8 → 17 »."""
    m = _VER_LIST.match(folded[end:end + 40])
    return {int(x) for x in re.findall(r"(?<![\d.])(\d{1,2})(?:\.\d+)*", m.group(1))} if m else set()


MAX_MENTIONS_ANALYSED = 300            # garde-fou de performance : un CV bourré d'occurrences ne ralentit pas l'analyse (et ne gagne aucun point)
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ0-9\"'(])")


def _sentence_bounds(flat: str) -> list[tuple[int, int]]:
    """Phrases d'un élément aplati (offsets relatifs), sans les puces de tête."""
    out: list[tuple[int, int]] = []
    pos = 0
    for m in list(_SENT_END.finditer(flat)) + [None]:
        end = m.start() if m else len(flat)
        seg = flat[pos:end]
        lead = len(seg) - len(seg.lstrip(" \t•-–—*·▪►→"))
        trail = len(seg.rstrip())
        if seg.strip(" \t•-–—*·▪►→"):
            out.append((pos + lead, pos + max(trail, lead + 1)))
        pos = m.end() if m else end
    return out or [(0, max(1, len(flat)))]


def _sentence_at(text: str, pos: int) -> Span:
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    b = len(text) if b < 0 else b
    line = text[a:b]
    for s in sentences(line, a):
        if s.start <= pos <= s.end:
            return s
    return Span(a, b, line.strip())


def _justify(level: Level, best: EvItem, label: str, notes: list[str]) -> str:
    where = f" chez {best.company} ({best.period})" if best.company and best.period else (f" ({best.period})" if best.period else "")
    base = {
        Level.CONFIRMED: f"« {label} » démontré{where} : {best.note}.",
        Level.PARTIAL: f"« {label} » partiellement démontré{where} : {best.note}.",
        Level.DECLARED: f"« {label} » déclaré sans preuve suffisante : {best.note}.",
        Level.NOT_DOCUMENTED: f"« {label} » non documenté.",
        Level.CONTRADICTED: f"« {label} » contredit.",
    }[level]
    return base + ((" " + " ".join(notes)) if notes else "")


# ----------------------------------------------------------------- preuves externes (appels, entretiens, saisies)
_LEVEL_CAP_BY_REL = {Reliability.UNSUPPORTED_CLAIM: Level.DECLARED}


def merge_external(ev: CritEvidence, ext: list[ExtEvidence], label: str, *, kind_notes: list[str] | None = None,
                   depth_required: str = "practice", key: str = "", terms: list[str] | None = None, min_advanced: int = 2) -> CritEvidence:
    """Combine la preuve CV et les preuves d'appels/entretiens pour UN critère.

    - une transcription automatique non validée est une source à vérifier : plafonnée à « partiel » ;
    - une déclaration non étayée plafonne à « déclaré » ; une hypothèse n'entre pas dans le score ;
    - « limite » borne le niveau (contributeur seulement, deux topics…) ; la contradiction du CV est conservée ;
    - « contredit » (absence explicite) fixe le niveau à « contredit » si la source est fiable ou validée.
    """
    live = [e for e in ext if not e.superseded]
    if not live:
        return ev
    level = ev.level
    items = list(ev.items)
    contradictions = list(ev.contradictions)
    notes = list(ev.notes)
    for e in live:
        if e.reliability == Reliability.HYPOTHESIS:
            notes.append(f"Hypothèse (non retenue dans le score) : « {e.excerpt[:120]} »")
            continue
        loc_note = f"{e.source.value.replace('_', ' ')}{' (auto)' if e.auto_generated else ''}{' — validé' if e.validated else ''}"
        if e.kind == EvidenceKind.SUPPORTS:
            lv = e.level or Level.PARTIAL
            lv = min(lv, _LEVEL_CAP_BY_REL.get(e.reliability, Level.CONFIRMED), key=lambda l: LEVEL_ORDER[l])
            if depth_required == "advanced" and key and LEVEL_ORDER[lv] > LEVEL_ORDER[Level.PARTIAL]:
                # la profondeur avancée exigée vaut aussi pour les échanges : indices du CV + indices de cet extrait
                sk = skill_for(key, terms or [], label)
                have = {_stem(t) for t in ev.advanced_found} | {_stem(t) for t in advanced_terms_in(sk, e.excerpt)}
                if len(have) < min_advanced:
                    lv = Level.PARTIAL
                    loc_note_extra = f" — profondeur avancée non démontrée par cet échange ({len(have)} indice(s) sur {min_advanced})"
                    notes.append(f"Échange du {e.date or 'jour'} : pratique décrite mais profondeur avancée non démontrée ({len(have)} indice(s)).")
                else:
                    loc_note_extra = ""
            else:
                loc_note_extra = ""
            loc_note += loc_note_extra
            if e.auto_generated and not e.validated:
                lv = min(lv, Level.PARTIAL, key=lambda l: LEVEL_ORDER[l])
                loc_note += " — transcription à vérifier : niveau plafonné à « partiel » tant que non validée"
            items.append(EvItem("call", e.excerpt, 0, 0, "call", lv, reliability=e.reliability, note=loc_note, evidence_id=e.id, validated=e.validated))
            if LEVEL_ORDER[lv] > LEVEL_ORDER[level] and level != Level.CONTRADICTED:
                level = lv
        elif e.kind == EvidenceKind.LIMITS:
            items.append(EvItem("call", e.excerpt, 0, 0, "call", Level.PARTIAL, reliability=e.reliability, note="limite l'expérience : " + loc_note, evidence_id=e.id, validated=e.validated))
            if LEVEL_ORDER[level] > LEVEL_ORDER[Level.PARTIAL]:
                contradictions.append({"type": "limite", "cv_level": level.value, "evidence_id": e.id, "statement": e.excerpt,
                                       "resolution": "Le niveau est ramené à « partiellement démontré » : l'échange précise un périmètre plus restreint que le CV."})
                level = Level.PARTIAL
        elif e.kind == EvidenceKind.CAPS:
            cap = e.level or Level.DECLARED
            items.append(EvItem("call", e.excerpt, 0, 0, "call", cap, reliability=e.reliability, note="correction du recruteur : niveau plafonné — " + loc_note,
                                evidence_id=e.id, validated=True))
            if LEVEL_ORDER[level] > LEVEL_ORDER[cap]:
                contradictions.append({"type": "correction_recruteur", "cv_level": level.value, "evidence_id": e.id, "statement": e.excerpt,
                                       "resolution": f"Niveau ramené à « {cap.value.replace('_', ' ')} » par le recruteur."})
                level = cap
        elif e.kind == EvidenceKind.CONTRADICTS:
            # « contredit » déclenche un plafond et une alerte bloquante : il exige un humain. Une absence EXTRAITE d'une note ou d'une transcription
            # est une interprétation (négations, tiers, composantes) : tant qu'un recruteur ne l'a pas validée, elle plafonne à « déclaré » et demande confirmation.
            reliable = e.validated or e.source == EvidenceSource.RECRUITER_INPUT
            items.append(EvItem("call", e.excerpt, 0, 0, "call", Level.CONTRADICTED if reliable else Level.DECLARED, reliability=Reliability.CONTRADICTION,
                                note=("contredit : " if reliable else "absence déclarée à confirmer : ") + loc_note, evidence_id=e.id, validated=e.validated))
            contradictions.append({"type": "contradiction" if reliable else "contradiction_a_confirmer", "cv_level": ev.level.value, "evidence_id": e.id, "statement": e.excerpt,
                                   "resolution": "Niveau « contredit »" if reliable else "Absence déclarée dans un échange, non validée : le niveau est plafonné à « déclaré » ; "
                                                                                     "valider l'information pour appliquer le plafond d'écart majeur, ou la rejeter si l'extraction est erronée."})
            if reliable:
                level = Level.CONTRADICTED
            else:
                level = min(level, Level.DECLARED, key=lambda l: LEVEL_ORDER[l])
    just = ev.justification if level == ev.level else _justify(level, items[0] if items else EvItem("cv", "", 0, 0, "other", level), label, notes)
    if level != ev.level:
        just = f"Après prise en compte des échanges : {level.value.replace('_', ' ')}. " + (ev.justification or "")
    return CritEvidence(level=level, items=sorted(items, key=lambda i: -LEVEL_ORDER[i.level])[:8], justification=just, notes=notes,
                        last_used=ev.last_used, months_practice=ev.months_practice, advanced_found=ev.advanced_found,
                        advanced_missing=ev.advanced_missing, contradictions=contradictions, open_questions=ev.open_questions,
                        recency_downgraded=ev.recency_downgraded, capped_by=ev.capped_by, experience_idxs=ev.experience_idxs)


HIGH_VOLUME_PER_DAY = 100_000          # seuil configurable de « forte volumétrie »
_VOLUME_TERMS = {"volumetrie", "throughput", "debit"}
_OWNERSHIP_TERMS = {"architecture", "decisionnaire", "design authority"}


# ----------------------------------------------------------------- volumétrie (distinguer la volumétrie Kafka de la volumétrie bancaire)
# nombre : groupes de milliers (« 5 000 », « 5,000 », « 5.000 ») ou entier court + décimale éventuelle ; ancré (pas de départ au milieu d'un nombre)
_NUM = r"(?<![\d.,])(\d{1,3}(?:[ .,\u00a0]\d{3})+|\d{1,12}(?:[.,]\d{1,2})?)(?![\d])"
_UNIT = (r"(?:transactions?|tx|tps|msgs?|messages?|requetes?|requests?|evenements?|events?|fichiers?|files|appels?|calls?|operations?|ops|"
         r"commandes?|orders?|paiements?|payments?|enregistrements?|records?)")
_VOL = re.compile(rf"{_NUM} ?(k|m|millions?|milliards?|milliers?|billions?)? ?(?:de |d'|of )?{_UNIT}\b"
                  rf"(?: ?(?:/|par |per |a l'|each |by )? ?(seconde|second|sec|minute|min|heure|hour|jour|day|semaine|week|mois|month|an|annee|year|s|h|j|d)\b)?", re.I)
_PER_DAY = {"seconde": 86400, "second": 86400, "sec": 86400, "s": 86400, "minute": 1440, "min": 1440, "heure": 24, "hour": 24, "h": 24, "jour": 1, "day": 1, "j": 1, "d": 1,
            "semaine": 1 / 7, "week": 1 / 7, "mois": 1 / 30, "month": 1 / 30, "an": 1 / 365, "annee": 1 / 365, "year": 1 / 365}
_MULT = {"k": 1e3, "m": 1e6, "million": 1e6, "millions": 1e6, "milliard": 1e9, "milliards": 1e9, "billion": 1e9, "billions": 1e9, "millier": 1e3, "milliers": 1e3}
_RATE_UNIT = re.compile(r"^(?:tps)$", re.I)


def _to_number(raw: str) -> float | None:
    t = raw.replace("\u00a0", " ")
    if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", t):
        t = re.sub(r"[ .,]", "", t)                       # séparateurs de milliers
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def volume_per_day(text: str) -> float | None:
    """Plus grand débit chiffré trouvé, ramené par jour ; None si rien de chiffré AVEC UNE DURÉE.

    Un volume sans durée (« 120 000 messages ») est un total, pas un débit : il n'établit aucune volumétrie. Un débit « par an » vaut 1/365 par jour."""
    best: float | None = None
    f = re.sub(r"\s+", " ", fold(text))               # espaces multiples : jamais de retour arrière quadratique
    if re.search(r"plusieurs\s+millions?\s+(?:de\s+)?(?:transactions?|messages?|requetes?|evenements?|operations?)\s+(?:par|/)\s+jour", f):
        best = 2e6
    for m in _VOL.finditer(f):
        n = _to_number(m.group(1))
        if n is None:
            continue
        per_key = (m.group(3) or "").lower()
        if per_key:
            per = _PER_DAY.get(per_key)
        elif re.search(r"\btps\b", m.group(0)):
            per = 86400                                   # « 5 000 tps » = par seconde
        else:
            per = None
        if per is None:
            continue
        v = n * _MULT.get((m.group(2) or "").lower(), 1) * per
        best = v if best is None else max(best, v)
    return best


_VOL_SPLIT = re.compile(r"(?<=[.!?]) |(?<= )(?:qui|dont|which|that|where|ou) ")


MEDIUM_VOLUME_PER_DAY = 10_000


def _evaluate_volume(parsed: ParsedCV, label: str, scope_terms: list[str], cfg: EvalConfig) -> CritEvidence:
    """« Forte volumétrie » : comparer des débits CHIFFRÉS à un seuil, dans la portée demandée.

    Une volumétrie ne se transfère pas d'un contexte à l'autre : 100 transactions/semaine sur un flux Kafka
    n'est pas la volumétrie d'une plateforme de paiements traitant des millions d'opérations par jour (§8).
    """
    text = parsed.text
    items: list[EvItem] = []
    out_of_scope: list[tuple[float, str]] = []
    zones = [(e.span[0], e.span[1], e) for e in parsed.experiences] or [(0, len(text), None)]
    for a, b, exp in zones:
        for ia, ib in _items(text, a, b):
            chunk = text[ia:ib]
            flat_chunk = re.sub(r"\s+", " ", cl.flatten(chunk))
            # une volumétrie appartient à la proposition qui la porte : « flux Kafka (50 messages par jour) … SI bancaire qui traite des millions de transactions »
            # n'attribue pas les millions de la relative à Kafka
            segs, pos0 = [], 0
            for sm in list(_VOL_SPLIT.finditer(fold(flat_chunk))) + [None]:
                e0 = sm.start() if sm else len(flat_chunk)
                segs.append(flat_chunk[pos0:e0])
                pos0 = sm.end() if sm else e0
            vols = [(v, sg) for sg in segs if (v := volume_per_day(sg)) is not None]
            if not vols:
                continue
            if scope_terms:
                in_scope = [(v, sg) for v, sg in vols if any(re.search(term_pattern(t), fold(sg)) for t in scope_terms)]
                for v, sg in vols:
                    if (v, sg) not in in_scope:
                        out_of_scope.append((v, chunk))
                if not in_scope:
                    continue
                vols = in_scope
            vol = max(v for v, _sg in vols)
            lvl = Level.CONFIRMED if vol >= HIGH_VOLUME_PER_DAY else (Level.PARTIAL if vol >= MEDIUM_VOLUME_PER_DAY else Level.DECLARED)
            note = (f"débit chiffré ≈ {vol:,.0f} opérations/jour".replace(",", " ")
                    + (" (≥ seuil de forte volumétrie)" if lvl == Level.CONFIRMED else (" (volumétrie moyenne)" if lvl == Level.PARTIAL else " : volumétrie FAIBLE, ne démontre pas une forte volumétrie")))
            items.append(EvItem("cv", chunk.strip(), ia, ib, "experience" if exp else "other", lvl, experience_idx=exp.idx if exp else None,
                                company=exp.company if exp else "", period=_period(exp), note=note, signals={"volume_per_day": vol}))
    notes: list[str] = []
    for vol, chunk in out_of_scope[:1]:
        notes.append(f"Une volumétrie élevée (≈ {vol:,.0f}/jour) existe ailleurs dans le CV mais hors de la portée demandée ({', '.join(scope_terms)}) : elle n'est pas transférée.".replace(",", " "))
    if not items:
        return CritEvidence(Level.NOT_DOCUMENTED, [], f"Aucune volumétrie chiffrée retrouvée" + (f" pour {', '.join(scope_terms)}" if scope_terms else "") + ".", notes=notes,
                            open_questions=["Quels volumes (transactions par jour/seconde, pics) traitait ce système et quelle part relevait de ton périmètre ?"])
    best = max(items, key=lambda i: (LEVEL_ORDER[i.level], i.signals["volume_per_day"]))
    return CritEvidence(best.level, sorted(items, key=lambda i: -LEVEL_ORDER[i.level])[:4], f"« {label} » : {best.note}.", notes=notes,
                        experience_idxs=[best.experience_idx] if best.experience_idx is not None else [])
