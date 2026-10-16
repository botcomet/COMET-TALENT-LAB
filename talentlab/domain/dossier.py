"""Matière de dossier au format de structuration Comet (§19.1) — sans réécriture.

    Entreprise — Intitulé — Dates
    Contexte
    Réalisations         (parties thématiques à titre métier explicite, phrases sans tirets ni puces)
    Environnement technique   (une phrase : technologies séparées par des virgules)

Talent Lab ne rédige pas le dossier (§19) : il **range** des phrases du CV, mot pour mot, dans cette structure.

Règles de fidélité (§19.3), appliquées ici par construction :
- aucune phrase n'est créée ni reformulée : chaque réalisation est une phrase du CV (seuls la puce de tête et la ponctuation finale sont normalisées) ;
- les titres de parties viennent d'un vocabulaire FERMÉ de familles d'activité (``THEMES``), jamais d'un texte libre : ils classent, ils n'affirment rien ;
- une phrase ne change jamais d'expérience ; une technologie n'est jamais ajoutée à l'environnement (il est repris tel quel de la ligne « Environnement technique » du CV) ;
- une date absente reste « dates non précisées » ; un contexte ou un environnement absent reste « Non précisé dans le CV » ;
- l'export CLIENT ne contient ni niveau de preuve, ni provenance, ni point à vérifier (ils restent dans l'export INTERNE) ;
- seules les compétences **confirmées** figurent dans la liste des compétences démontrées (jamais « partiel » ni « déclaré »).
"""
from __future__ import annotations

import re
from typing import Any

from .text import fold

MONTHS_FR = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
NOT_STATED = "Non précisé dans le CV"

# Familles d'activité : titre de partie (explicite, métier) + marqueurs de phrase. Vocabulaire fermé, ordre = priorité en cas d'égalité.
THEMES: list[tuple[str, re.Pattern[str]]] = [
    ("Conception et développement", re.compile(
        r"\b(?:conc(?:u|oi\w*|eption)|develop\w*|implement\w*|realis\w*|construi\w*|cre(?:e|er|ation)\b|programm\w*|coded?\b|coding|built|designed|developed|implemented|created|engineered)")),
    ("Exploitation, support et fiabilité", re.compile(
        r"\b(?:exploit\w*|support\w*|incident\w*|astreinte\w*|supervis\w*|monitor\w*|maintenance|maintien|post-?mortem|causes?\s+racines?|resolution|disponibilite|fiabilit\w*|"
        r"operated|maintained|troubleshot|on-?call)")),
    ("Migration, déploiement et automatisation", re.compile(
        r"\b(?:migr\w*|deploi\w*|deploy\w*|automatis\w*|industrialis\w*|mise\s+en\s+(?:production|service)|ci/?cd|packag\w*|bascule|cutover|upgrade\w*|provision\w*|installed|migrated|automated)")),
    ("Encadrement et transmission", re.compile(
        r"\b(?:encadr\w*|mentor\w*|coach\w*|revues?\s+de\s+code|code\s+reviews?|formation|accompagn\w*|onboarding|manag\w*|led\b|team\s+lead)")),
    ("Analyse, spécification et documentation", re.compile(
        r"\b(?:analys\w*|specif\w*|redig\w*|document\w*|cahier\s+des\s+charges|etude|audit\w*|cartograph\w*|recette|expression\s+de\s+besoin)")),
    ("Pilotage et coordination", re.compile(
        r"\b(?:pilot\w*|coordin\w*|planif\w*|animation|animer|suivi|gouvernance|arbitrage|reporting|comit\w*)")),
]
OTHER = "Autres réalisations"
MAX_NAMED_PARTS = 3


def french_period(start: str | None, end: str | None, is_current: bool, dates_known: bool) -> str:
    """« Février 2019 – en cours », « 2013 – Janvier 2019 » ; « dates non précisées » si le CV ne permet pas de les établir (jamais déduites)."""
    if not dates_known or not start:
        return "dates non précisées"

    def fmt(iso: str) -> str:
        y, _, m = iso.partition("-")
        return f"{MONTHS_FR[int(m) - 1]} {y}" if m and m.isdigit() and 1 <= int(m) <= 12 else y
    return f"{fmt(start)} – {'en cours' if is_current else (fmt(end) if end else 'date de fin non précisée')}"


def sentence(statement: str) -> str:
    """Phrase du CV sans puce de tête ; ponctuation finale ajoutée si absente (seule normalisation autorisée)."""
    t = re.sub(r"^[\s\-–—•*·▪►→]+", "", statement).strip()
    return t if not t or t[-1] in ".!?…" else t + "."


def _context(text: str | None) -> str:
    """Contexte repris du CV ; seules la majuscule initiale et la ponctuation finale sont normalisées (le préfixe « Contexte : » a été retiré)."""
    t = sentence(text or "")
    return NOT_STATED if not t else t[0].upper() + t[1:]


def _theme_of(statement: str) -> str | None:
    f = fold(statement)
    head = f[:80]                                   # le verbe d'ouverture dit le plus sur l'activité décrite
    best, best_score = None, 0
    for title, rx in THEMES:
        score = 2 * len(rx.findall(head)) + len(rx.findall(f))
        if score > best_score:
            best, best_score = title, score
    return best


def theme_parts(statements: list[str]) -> list[dict[str, Any]]:
    """Range les phrases d'UNE expérience en parties thématiques (au plus 3 + « Autres réalisations ») ; l'ordre des phrases est conservé dans chaque partie."""
    by_theme: dict[str, list[str]] = {}
    for st in statements:
        by_theme.setdefault(_theme_of(st) or OTHER, []).append(sentence(st))
    named = {t: v for t, v in by_theme.items() if t != OTHER}
    ranked = sorted(named, key=lambda t: (-len(named[t]), [x[0] for x in THEMES].index(t)))
    keep = ranked[:MAX_NAMED_PARTS]
    other = by_theme.get(OTHER, []) + [s for t in ranked[MAX_NAMED_PARTS:] for s in named[t]]
    order = [x[0] for x in THEMES]
    parts = [{"title": t, "sentences": named[t]} for t in sorted(keep, key=order.index)]
    if other:
        parts.append({"title": OTHER, "sentences": other})
    return parts


def environment_sentence(experience: dict[str, Any]) -> str:
    phrase = (experience.get("environnement_technique_phrase") or "").strip().rstrip(".")
    if not phrase:
        return NOT_STATED
    items = [t.strip() for t in re.split(r"[,;]", phrase) if t.strip()]
    return ", ".join(items) + "."


def render_client(material: dict[str, Any], *, acronym: str, mission_title: str, demonstrated: list[str], validated_calls: list[dict[str, Any]]) -> str:
    """Texte prêt à relire puis à transposer dans DT Editor. Aucune information interne : ni niveau, ni provenance, ni point à vérifier."""
    L = [f"# Matière de dossier de compétences — {acronym}", f"Mission visée : {mission_title}", ""]
    if demonstrated:
        L += ["## Compétences démontrées sur pièces", ", ".join(demonstrated) + ".", ""]
    L.append("## Expériences")
    for e in material.get("experiences", []):
        L += ["", f"### {e.get('company') or '(entreprise non précisée)'} — {e.get('title') or '(intitulé non précisé)'} — "
                  f"{french_period(e.get('start'), e.get('end'), e.get('is_current', False), e.get('dates_known', False))}",
              "", "**Contexte**", _context(e.get("context")), "", "**Réalisations**"]
        parts = theme_parts([r["statement"] for r in e.get("realisations", [])])
        if not parts:
            L.append(NOT_STATED)
        for i, part in enumerate(parts, start=1):
            L += ["", f"*Partie {i} — {part['title']}*", " ".join(part["sentences"])]
        L += ["", "**Environnement technique**", environment_sentence(e)]
    if validated_calls:
        L += ["", "## Précisions validées lors des échanges",
              "(À intégrer par le recruteur dans l'expérience concernée : elles ne sont rattachées d'office à aucune expérience.)"]
        L += [f"- {c['statement']}" for c in validated_calls]
    return "\n".join(L).strip() + "\n"


def render_internal(out: dict[str, Any]) -> str:
    """Pour le recruteur seulement : provenance, niveaux, points à vérifier, informations d'échange non validées. Ne pas transmettre au client."""
    io = out["internal_only"]
    L = ["# INTERNE — ne pas transmettre au client", f"Candidat : {out['candidate']['acronym']} ({out['candidate']['reference']})",
         f"Évaluation v{io['assessment']['version']} sur la grille v{io['assessment']['grid_version']} (empreinte {str(io['assessment']['grid_hash'])[:12]}…)", ""]
    L.append("## Éléments restant à vérifier")
    L += [f"- {t['criterion']} : {t['level'].replace('_', ' ')} — {t['why']}" for t in io["to_verify"]] or ["- Aucun."]
    if io["unvalidated_call_information"]:
        L += ["", "## Informations d'échange NON validées (exclues du dossier)"] + [f"- {i['statement']} ({i['topic']}, {i['source']})" for i in io["unvalidated_call_information"]]
    if io["date_flags"]:
        L += ["", "## Alertes sur les dates"] + [f"- {f}" for f in io["date_flags"]]
    return "\n".join(L).strip() + "\n"
