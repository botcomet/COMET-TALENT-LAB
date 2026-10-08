"""Portée d'une mention : à quelle proposition appartient-elle et que dit cette proposition ?

Une mention de « Kafka » n'établit une pratique que si la proposition qui la porte AFFIRME un travail réel
sur cette technologie. Elle ne l'établit pas si la proposition :

- la nie (« je n'ai jamais configuré Kafka ») — l'absence déclarée n'est jamais un crédit ;
- l'annonce (« migration vers Kafka planifiée ») ou l'espère (« souhaite développer ») ;
- l'attribue à un tiers (« l'équipe plateforme qui administre le cluster ») ;
- la place dans une formation, un projet personnel ou familial ;
- la place dans un contexte administratif ou commercial (audit de contrat, coûts, licences) ;
- la réduit à du support utilisateur.

Toutes ces règles sont des motifs explicites, lisibles et testés (FR + EN), appliqués sur le texte replié
(sans accents, même longueur que l'original : les positions restent valides pour citer l'extrait exact).
Elles ne font que PLAFONNER ou ÉCARTER : aucune ne crée de crédit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .enums import Level

# ----------------------------------------------------------------- segmentation
_CONTRAST = r"\b(?:mais|but|however|toutefois|cependant|alors\s+que|whereas|en\s+revanche)\b"
_CLAUSE_BOUNDARY = re.compile(rf";|(?<=[.!?])\s+(?=[a-z0-9\"'(])|{_CONTRAST}")


def flatten(text: str) -> str:
    """Un élément d'expérience enveloppé sur plusieurs lignes est UNE réalisation : retours à la ligne → espaces (offsets inchangés)."""
    return text.replace("\n", " ")


def clause_bounds(f: str) -> list[tuple[int, int]]:
    """Propositions d'un texte replié et aplati : coupures sur « ; », fin de phrase, mots de contraste."""
    out: list[tuple[int, int]] = []
    pos = 0
    for m in _CLAUSE_BOUNDARY.finditer(f):
        if m.start() > pos:
            out.append((pos, m.start()))
        pos = m.end()
    if pos < len(f):
        out.append((pos, len(f)))
    return out or [(0, len(f))]


def clause_of(bounds: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    for a, b in bounds:
        if a <= pos < b:
            return a, b
    return bounds[-1]


# ----------------------------------------------------------------- négation (absence déclarée)
_NEG_CUE = re.compile(
    r"\bn'(?:ai|a|avons|etais|etait|y\s+ai)\s+(?:\w+\s+){0,2}?(?:jamais|pas|plus|aucun\w*)\b"
    r"|\bne\s+(?:\w+\s+){1,2}?(?:jamais|pas|plus)\b"
    r"|\bjamais\b|\bpas\s+(?:d'|de\s|du\s|des\s|encore\s|vraiment\s)"
    r"|\bsans\s+(?:aucune?\s+|reelle?\s+|vraie?\s+)?(?:experience|pratique|connaissance|expertise|competence|exposition)\b"
    r"|\baucune?\s+(?:experience|pratique|connaissance|expertise|competence)\b"
    r"|\bnon\s+(?:utilise\w*|maitrise\w*|pratique\w*|applicable|realise\w*)\b|\bn'est\s+pas\b|\bnul(?:le)?\s+experience\b"
    r"|\bno\s+(?:prior\s+|direct\s+|hands-on\s+|professional\s+|real\s+)?(?:experience|exposure|knowledge|expertise)\b"
    r"|\bnever\b|\b(?:did|do|does|have|has|had|was|were|am|is)\s*(?:not|n't)\b|\b(?:didn't|haven't|hasn't|hadn't|wasn't|weren't)\b"
    r"|\bwithout\s+(?:any\s+|prior\s+|real\s+)?(?:experience|exposure|knowledge)\b|\bnot\s+(?:used|familiar|experienced|involved)\b"
    r"|\b(?:lack|lacks|lacking)\b|\bnon\s+utilise\b")
_NEG_BREAK = re.compile(r"[,:;()!?]|\.\s|\s-\s")
_NI = re.compile(r",?\s*\b(?:ni|nor|or\s+neither)\b\s*")


_POST_NEG = re.compile(r"\bnon\s+(?:utilise|maitrise|pratique|realise)\w*|\bnot\s+(?:directly\s+)?used\b|\bn'(?:est|etait)\s+pas\s+(?:utilise|pratique)\w*")


def negated_regions(f: str) -> list[tuple[int, int]]:
    """Régions niées : de l'indice de négation jusqu'à la première ponctuation de portée (« , » ; « : » ; parenthèse), « ni » prolongeant la portée.
    Une négation postposée (« Kafka non utilisé directement ») nie toute sa proposition."""
    regions: list[tuple[int, int]] = []
    bounds = clause_bounds(f)
    for m in _POST_NEG.finditer(f):
        regions.append(clause_of(bounds, m.start()))
    for m in _NEG_CUE.finditer(f):
        end = m.end()
        # portée : jusqu'à la prochaine coupure, en suivant les énumérations « ni A ni B »
        while True:
            nb = _NEG_BREAK.search(f, end)
            stop = nb.start() if nb else len(f)
            if nb:
                ni = _NI.match(f, nb.start())
                if ni and ni.start() == nb.start() and f[nb.start()] == ",":
                    end = ni.end()
                    continue
            end = stop
            break
        # un « pas / jamais » suivi d'un problème nié est POSITIF (« jamais de problème avec Kafka »)
        if re.match(r"\s*(?:\w{1,10}\s+){0,3}?(?:de\s+|d')?(?:probleme|difficulte|souci|incident|erreur|panne|bug|perte|blocage|retard|regression|trouble|issue|outage|problem)", f[m.end():m.end() + 45]):
            continue
        regions.append((m.start(), end))
    return _merge(regions)


def _merge(regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(regions):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# ----------------------------------------------------------------- plafonds de portée
@dataclass(frozen=True)
class Cap:
    code: str
    level: Level
    reason: str


_CAPS: list[tuple[str, Level, str, re.Pattern[str]]] = [
    ("future", Level.DECLARED, "projet ou intention (futur, planifié, souhaité, appris) : n'établit pas une pratique réalisée", re.compile(
        r"\b(?:planifi\w*|prevu\w*|a\s+venir|envisag\w*|souhait(?:e|ons|er|ant)\b|aspire\w*|aimerais|aimerai\w*|desire\w*|en\s+cours\s+d'(?:apprentissage|acquisition)|"
        r"autoformation|auto-formation|auto-didacte|autodidacte|veille\s+technologique|curieu\w*|interesse\w*\s+(?:par|a)|motive\w*\s+par|"
        r"planned|upcoming|intend\w*|interested|would\s+like|looking\s+to|eager|keen|self-taught|self-study|currently\s+learning|learning\s+(?:kafka|about|how)|"
        r"plan\s+to|plans\s+to|will\s+(?:be|start|use|develop|implement|migrate)|to\s+be\s+(?:developed|implemented|migrated|deployed))\b")),
    ("third_party", Level.DECLARED, "pratique attribuée à un tiers (autre équipe, prestataire, plateforme) : n'établit pas le rôle du candidat", re.compile(
        r"\b(?:equipes?|services?|poles?|prestataires?|partenaires?|editeurs?|fournisseurs?)\s+(?:\w+\s+){0,3}?qui\s+(?:\w+\s+)?"
        r"(?:administr\w*|ger\w*|gere\w*|exploit\w*|oper\w*|maintien\w*|maintain\w*|deploi\w*|assur\w*|fourni\w*|heberge\w*|livr\w*|support\w*)|"
        r"\b(?:equipes?|services?|poles?|prestataires?|partenaires?|editeurs?|fournisseurs?)\s+(?:\w+\s+){0,3}?(?:en\s+charge|charge\s+de)\b|"
        r"\b(?:assure|assuree|assures|assurees|gere|geree|geres|gerees|realise|realisee|realises|opere|operee|exploite|exploitee|administre|administree|fourni|fournie|"
        r"maintenu|maintenue|pilote|pilotee)\s+par\s+(?!moi\b|nous\b|mon\b|notre\b)|\bcote\s+(?:plateforme|infra\w*|client|editeur|fournisseur|equipe|ops)\b|"
        r"\b(?:par|de)\s+(?:une|l')\s*autre\s+equipe\b|\bautre\s+equipe\b|"
        r"\b(?:managed|run|operated|handled|owned|provided|maintained|administered)\s+by\s+(?!me\b|my\b|myself\b|us\b|our\b)|\b(?:platform|infra\w*|ops|devops|sre|vendor)\s+team\s+(?:that|who|which)\b|"
        r"\bby\s+(?:another|a\s+separate)\s+team\b|\bnot\s+(?:our|my)\s+(?:scope|responsibility|perimeter)\b|\bhors\s+(?:de\s+)?(?:mon\s+)?perimetre\b|"
        r"\b(?:collaborat\w*|travaill\w*|interag\w*|echang\w*|coordination|liaison)\s+(?:avec|a)\s+(?:l'|les?\s+|la\s+)?(?:equipes?|services?|poles?|prestataires?|partenaires?|editeurs?)\b|"
        r"\b(?:work\w*|collaborat\w*|liais\w*|interact\w*)\s+with\s+(?:the\s+|our\s+)?(?:\w+\s+)?(?:team|vendor|provider)s?\b")),
    ("personal", Level.DECLARED, "contexte non professionnel (projet personnel, familial, scolaire ou associatif) : n'établit pas une expérience en mission", re.compile(
        r"\bprojets?\s+(?:personnels?|perso|annexes?|open[- ]?source|etudiants?|scolaires?|de\s+fin\s+d'etudes?)\b|\bpersonal\s+projects?\b|\bside[- ]?projects?\b|\bpet\s+projects?\b|"
        r"\bhobby\b|\bhobbies\b|\bloisirs?\b|\ba\s+titre\s+(?:personnel|perso|benevole)\b|\busage\s+personnel\b|\bma\s+famille\b|\bfamilial\w*\b|"
        r"\b(?:mon|pour\s+mon)\s+(?:frere|pere|cousin|oncle|ami\w*|voisin|beau-frere)\b|\bmy\s+(?:family|brother|father|friend)\b|\bfamily\s+(?:business|site|shop|project)\b|"
        r"\bbenevol\w*|\bassociat(?:ion|if)\b|\bhomelab\b|\bhome\s+lab\b|\braspberry\b|\bcours\s+du\s+soir\b|\bprojets?\s+d'ecole\b|\bschool\s+projects?\b|"
        r"\b(?:en|a\s+l')\s*(?:universite|ecole)\b|\buniversity\s+projects?\b")),
    ("training", Level.DECLARED, "formation, atelier ou travaux pratiques : connaissance acquise, pas une expérience en mission", re.compile(
        r"\b(?:formation|formations|stage\s+de\s+formation|bootcamp|mooc|coursera|udemy|openclassrooms|pluralsight|tutoriel|tutorial|travaux\s+pratiques|tp|"
        r"atelier\s+de\s+formation|workshop\s+(?:on|training)|training\s+(?:course|session|program\w*|on)|trained\s+(?:on|in)|certification\s+(?:en\s+cours|visee|preparee)|webinaire|webinar|hackathon|meetup)\b")),
    ("admin", Level.DECLARED, "contexte administratif ou commercial (contrat, coûts, licences, achats) : n'établit pas la pratique technique", re.compile(
        r"\b(?:contrats?|couts?|budgets?|achats?|devis|tco|appels?\s+d'offres?|renouvellement\s+de|procurement|vendor\s+management|negociation\s+(?:commerciale|fournisseur)|"
        r"licences?\s+(?:et|ou)\s+(?:contrats?|maintenance)|facturation\s+des\s+(?:licences|produits)|cost\s+(?:analysis|optimi\w*)|invoice\w*)\b")),
    ("user_support", Level.DECLARED, "support utilisateur : utilisation de l'environnement, pas son administration ni sa conception", re.compile(
        r"\b(?:support\s+(?:aux?\s+|des\s+|a\s+l'|de\s+l')?utilisateurs?|support\s+utilisateurs?|assistance\s+(?:aux\s+)?utilisateurs?|help\s*desk|service\s+desk|"
        r"end[- ]user\s+support|user\s+support|hotline|support\s+bureautique|support\s+de\s+niveau\s+1|support\s+n1)\b")),
]
_TRAINER = re.compile(r"\b(?:animation|anime|animer|dispense\w*|conduite|delivre\w*|formateur|trainer|taught|delivered|animated|led)\b")
_TRAINER_TARGET = re.compile(r"\bformations?\s+(?:de|des|aux?|pour)\s+(?:\d+\s+)?(?:equipes?|utilisateurs?|developpeurs?|collaborateurs?|juniors?|clients?|nouveaux?|\w+)\b")
_FUTURE_YEAR = re.compile(r"\b(20[3-9]\d|20[2-9]\d)\b")


def caps_in(f_clause: str, *, today: date | None = None) -> list[Cap]:
    """Plafonds applicables à une proposition (texte replié)."""
    today = today or date.today()
    out: list[Cap] = []
    for code, level, reason, rx in _CAPS:
        if not rx.search(f_clause):
            continue
        if code == "training" and (_TRAINER.search(f_clause) or _TRAINER_TARGET.search(f_clause)):
            out.append(Cap("trainer", Level.PARTIAL, "formation dispensée : connaissance démontrée, pas une pratique en mission"))
            continue
        out.append(Cap(code, level, reason))
    for m in _FUTURE_YEAR.finditer(f_clause):          # « pour 2027 » : une date postérieure à aujourd'hui annonce, ne démontre pas
        if int(m.group(1)) > today.year:
            out.append(Cap("future", Level.DECLARED, _CAPS[0][2]))
            break
    return out


_ITEM_PREFIX = 70


def item_caps(f_item: str, *, today: date | None = None) -> list[Cap]:
    """Marqueurs en tête d'élément (« Projet personnel : … », « Formation Kafka : … ») : ils qualifient TOUT l'élément, quelle que soit la ponctuation."""
    head = f_item.lstrip(" -•*·▪►→")[:_ITEM_PREFIX]
    out = []
    for c in caps_in(head, today=today):
        if c.code in ("personal", "training", "trainer", "future", "admin"):
            out.append(c)
    return out


def strongest(caps: list[Cap]) -> Cap | None:
    order = {Level.NOT_DOCUMENTED: 0, Level.DECLARED: 1, Level.PARTIAL: 2, Level.CONFIRMED: 3, Level.CONTRADICTED: 0}
    return min(caps, key=lambda c: order[c.level]) if caps else None


def mask(text: str, regions: list[tuple[int, int]]) -> str:
    """Remplace par des espaces les régions écartées (mêmes offsets) : leurs verbes, chiffres et indices ne comptent pas."""
    if not regions:
        return text
    chars = list(text)
    for a, b in regions:
        for i in range(max(0, a), min(len(chars), b)):
            chars[i] = " "
    return "".join(chars)


# ----------------------------------------------------------------- énumération de technologies (liste de compétences déguisée)
_VERBISH = re.compile(r"\b(?:conc\w+|develop\w+|implement\w*|mise\s+en|mis\s+en|deploi\w*|deploy\w*|configur\w*|parametr\w*|administr\w*|migr\w+|optimis\w*|"
                      r"automatis\w*|constru\w+|cre(?:e|er|ation)\b|pilot\w*|encadr\w*|exploit\w*|utilis\w*|gestion|gerer|realis\w*|built|designed|developed|implemented|"
                      r"deployed|configured|managed|led|created|integrated|operated|optimi[sz]ed|automated|used|using|maintained)\b")


def is_stack_list(flat_clause: str, folded_clause: str) -> bool:
    """« Administration et configuration : A, B, C, D » : une étiquette suivie d'une énumération de noms, sans verbe, est une LISTE de technologies,
    pas une réalisation. Quatre éléments ou plus, ou trois courts, sans verbe d'action dans l'énumération."""
    if ":" not in folded_clause:
        return False
    head, tail = folded_clause.split(":", 1)
    # une ÉTIQUETTE de catégorie est courte, sans article, sans préposition ni chiffre (« Administration et configuration ») ;
    # « Conception du Design System commun à 6 marques : … » est une réalisation suivie d'un détail, pas une liste
    words = head.split()
    if len(words) > 5 or re.search(r"\d|\b(?:le|la|les|l'|un|une|du|des|de|d'|au|aux|a|en|sur|pour|avec|dans|par|the|of|for|with|in|on)\b", head):
        return False
    items = [t.strip() for t in re.split(r",|/|\bet\b|\band\b", tail) if t.strip()]
    if len(items) < 3 or _VERBISH.search(tail):
        return False
    return all(len(t.split()) <= 5 for t in items)
