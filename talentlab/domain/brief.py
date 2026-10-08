"""Analyse d'un brief client : propose des exigences classées, ne valide jamais.

Règles appliquées (§4) :
- le titre ne suffit pas : on cherche le travail réellement demandé (activités) ;
- aucune compétence n'est « éliminatoire » parce qu'elle figure dans une liste ;
- « éliminatoire » n'est proposé que si le texte le dit explicitement, et reste à
  confirmer par le recruteur avec sa source ;
- une information absente n'est jamais inventée : elle devient une question.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import lexicon as lx
from .enums import Category, SourceKind
from .requirements import Req, new_id
from .text import fold, find_term, normalize_text, fold_compact

_NUM_WORDS = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10}

_SECTION_RULES: list[tuple[str, str]] = [
    ("mandatory", r"imperatifs?|obligatoires?|indispensables?|requis(?:e|es)?\b|prerequis|must[- ]have|incontournables?|competences requises|exigences"),
    ("desired", r"souhait|apprecie|nice[- ]to[- ]have|atouts?\b|bonus|\bplus\b"),
    ("modalities", r"modalites|conditions|localisation|lieu|duree|demarrage|teletravail|tjm|budget|presence|astreintes?"),
    ("tasks", r"missions?\b|responsabilit|activites|\brole\b|livrables?|objectifs?|perimetre|vos missions"),
    ("context", r"contexte|presentation|environnement|\bstack\b|enjeux|\bprojet\b"),
    ("profile", r"profil|competences|experience|technologies"),
]

_ELIM = re.compile(r"eliminatoire|redhibitoire|no[- ]go|non negociable|sans cela pas|sans quoi pas")
_IMPER = re.compile(r"imperatif|indispensable|obligatoire|incontournable|\bexige|\brequis|doit (?:imperativement )?(?:avoir|maitriser|justifier)|must[- ]have|mandatory|necessaire|absolument|imperativement")
_DIFF = re.compile(r"fortement (?:apprecie|souhaite|differenciant|recommande)|tres important|differenciant|forte valeur ajoutee|atout majeur|prioritaire|\bcle\b|determinant")
_DESIR = re.compile(r"souhait|apprecie|serait un plus|\bun plus\b|ideal|nice[- ]to[- ]have|\batout\b|bonus|de preference|\bplus\b")
_NEG_NEED = re.compile(r"(?:pas|aucune?|non)\s+(?:de\s+|d')?(?:necessite|obligation|exigence)|pas\s+(?:absolument\s+)?(?:necessaire|obligatoire|indispensable|requis)|n'est pas\s+(?:une\s+)?(?:exigence|obligatoire|necessaire|requis)")
_ADVANCED = re.compile(r"avance|approfondi|expert|expertise|solide|forte? |confirme|poussee|significative|reelle experience|conception")
_CLIENT_CONFIRMED = re.compile(r"confirme(?:e|s)? par le client|valide(?:e|s)? par le client|le client (?:a )?confirme")
_GROUP = re.compile(r"(plusieurs|au moins (\d|deux|trois|quatre|cinq)|l'une|une partie)\s+de\s+(?:ces|cette|ses)\s+(?:technologies|outils|solutions|produits|competences|plateformes)")
_RECENT = re.compile(r"recent|dernieres? annees|actuel|derniers? (\d{1,2}) ans")

_VERSION = re.compile(r"\b(java|spring boot|spring|kafka|react|angular|python|node(?:\.js)?|\.net|gts|s/4hana|postgres(?:ql)?|kubernetes)\s*v?(\d+(?:\.\d+)?)\b")


@dataclass
class BriefAnalysis:
    title: str
    role_family: str | None
    role_label: str | None
    context: list[str]
    objectives: list[str]
    activities: list[dict[str, Any]]
    technologies: list[str]
    modalities: dict[str, Any]
    requirements: list[Req]
    missing_info: list[dict[str, str]]
    warnings: list[str]
    real_work_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "role_family": self.role_family, "role_label": self.role_label,
            "context": self.context, "objectives": self.objectives, "activities": self.activities,
            "technologies": self.technologies, "modalities": self.modalities,
            "requirements": [r.to_dict() for r in self.requirements],
            "missing_info": self.missing_info, "warnings": self.warnings,
            "real_work_summary": self.real_work_summary,
        }


_STRENGTH = {
    Category.ELIMINATOIRE: 6, Category.IMPERATIF: 5, Category.DIFFERENCIANT: 4,
    Category.SOUHAITABLE: 3, Category.A_CLARIFIER: 2, Category.CONTEXTUEL: 1,
}


def _section_of(line: str) -> str | None:
    f = fold(line).strip(" :#*-—\t")
    if not f or len(f) > 70 or f.endswith("."):
        return None
    if not (line.rstrip().endswith(":") or line.isupper() or line.lstrip().startswith("#") or len(f.split()) <= 5):
        return None
    for name, rx in _SECTION_RULES:
        if re.search(rx, f):
            return name
    return None


def _split_blocks(text: str) -> list[tuple[str, str, int]]:
    """[(section, phrase, offset)] — chaque puce / phrase rattachée à sa section."""
    out: list[tuple[str, str, int]] = []
    section = "none"
    pos = 0
    for raw in text.split("\n"):
        start = pos
        pos += len(raw) + 1
        line = raw.strip()
        if not line:
            continue
        head = _section_of(line)
        if head and (line.endswith(":") or line.isupper() or len(line) < 45):
            section = head
            # « Impératifs : Java, Kafka » → conserve le contenu après les deux-points
            if ":" in line and len(line.split(":", 1)[1].strip()) > 2:
                line = line.split(":", 1)[1].strip()
            else:
                continue
        for sent in re.split(r"(?<=[.!?;])\s+", line):
            sent = sent.strip(" -–—•*·▪►→\t")
            if sent:
                out.append((section, sent, start + raw.find(sent) if sent in raw else start))
    return out


def _category_for(section: str, f: str) -> tuple[Category, str]:
    if _NEG_NEED.search(f):
        return Category.CONTEXTUEL, "Le client précise que ce n'est pas nécessaire : information contextuelle, non notée."
    if _ELIM.search(f):
        return Category.ELIMINATOIRE, "Le texte emploie un terme éliminatoire : à confirmer avec sa source client avant validation."
    if _IMPER.search(f):
        return Category.IMPERATIF, "Marqueur d'obligation explicite dans le texte."
    if _DIFF.search(f):
        return Category.DIFFERENCIANT, "Marqueur de forte importance dans le texte."
    if _DESIR.search(f):
        return Category.SOUHAITABLE, "Marqueur de souhait dans le texte."
    if section == "mandatory":
        return Category.IMPERATIF, "Figure dans une section d'exigences obligatoires."
    if section == "desired":
        return Category.SOUHAITABLE, "Figure dans une section de compétences souhaitées."
    if section in ("context",):
        return Category.CONTEXTUEL, "Figure dans le contexte / l'environnement : non noté tant que le client n'en fait pas une exigence."
    if section == "tasks":
        return Category.DIFFERENCIANT, "Activité décrite parmi les missions : travail attendu, à promouvoir en impératif si le client le confirme."
    return Category.A_CLARIFIER, "Aucun marqueur de priorité : à clarifier avec le client (impératif, souhaitable ou contextuel ?)."


def _dimension_for(sk: lx.Skill) -> tuple[str, str]:
    if sk.kind in ("tech", "product"):
        return "technique", "skill"
    if sk.kind == "activity":
        return "responsabilite", "activity"
    if sk.kind == "domain":
        return "contexte_metier", "domain"
    if sk.kind == "method":
        return "methodologie", "skill"
    if sk.kind == "soft":
        return "contrainte", "language"
    return "technique", "skill"


def _num(s: str) -> int | None:
    s = s.strip().lower()
    return int(s) if s.isdigit() else _NUM_WORDS.get(s)


def extract_modalities(text: str) -> dict[str, Any]:
    f = fold_compact(text)
    m: dict[str, Any] = {}
    if x := re.search(r"(\d)\s*jours?\s*(?:par semaine\s*|/\s*semaine\s*)?(?:sur site|sur place|en presentiel|de presence)", f):
        m["onsite_days_per_week"] = int(x.group(1))
    if x := re.search(r"(\d)\s*jours?\s*(?:de\s*)?teletravail", f):
        m["remote_days_per_week"] = int(x.group(1))
    if re.search(r"full remote|100\s*%\s*remote|100\s*%\s*teletravail|teletravail complet", f):
        m["remote"] = "full"
    for rx in (r"(?:sur site|sur place|en pr[ée]sentiel)\s*(?:[aàâ]|en|:)\s*([A-ZÀ-Ý][\wÀ-ÿ'’\- ]{2,30})",
               r"(?:localisation|lieu(?: de mission)?|ville)\s*[:=]\s*([A-ZÀ-Ý][\wÀ-ÿ'’\- ]{2,30})"):
        if x := re.search(rx, text):
            city = re.split(r"[,.;\n(]| et | - ", x.group(1))[0].strip()
            if city:
                m["city"] = city
                break
    if re.search(r"(?:pas d'|sans |aucune )astreintes?", f):
        m["astreinte"] = False
    elif re.search(r"astreintes?", f):
        m["astreinte"] = True
    if x := re.search(r"(?:duree|mission de|renouvelable|initiale(?:ment)? de|pour)\s*[:=]?\s*(\d{1,2})\s*(mois|ans|semaines)", f):
        m["duration"] = f"{x.group(1)} {x.group(2)}"
    if re.search(r"d[ée]marrage\s*[:=]?\s*(asap|des que possible)|asap", f):
        m["start"] = "ASAP"
    elif x := re.search(r"(?:demarrage|debut|start)\s*(?:de la mission)?\s*[:=]?\s*((?:le\s*)?\d{1,2}[/.]\d{1,2}[/.]\d{2,4}|[a-zéûô]+\s+\d{4})", f):
        m["start"] = x.group(1).strip()
    if x := re.search(r"(?:tjm|budget|taux journalier)\s*(?:max(?:imum)?|cible|client|propose)?\s*[:=]?\s*(?:jusqu'a|<=|max)?\s*(\d{3,4})\s*(?:€|eur|euros)?", f):
        m["tjm_max"] = int(x.group(1))
    if x := re.search(r"anglais\s*(courant|professionnel|bilingue|fluent|operationnel|b2|c1|c2)?", f):
        m["english"] = x.group(1) or "demandé (niveau non précisé)"
    elif re.search(r"\benglish\b", f):
        m["english"] = "demandé (niveau non précisé)"
    if x := re.search(r"(?:encadrement|encadrer|manag\w+|animation)\s*(?:technique\s*)?(?:de|d')\s*(\d+|un|une|deux|trois|quatre|cinq|six|sept|huit)\s*(?:developpeurs?|personnes|collaborateurs|ingenieurs|devs?)", f):
        n = _num(x.group(1))
        if n:
            m["team_size_managed"] = n
    # codes de fuseau en MAJUSCULES uniquement : « est » est un mot français courant
    if re.search(r"fuseau horaire|timezone|time zone|decalage horaire", f) or re.search(r"\b(?:GMT|UTC|CEST|CET|EST|PST|IST)\b", text):
        m["timezone_constraint"] = True
    return m


_MISSING_Q = {
    "onsite_days_per_week": "Quel rythme de présence sur site est attendu (jours/semaine) et quelle part de télétravail est possible ?",
    "city": "Quel est le lieu précis de mission ?",
    "duration": "Quelle est la durée initiale de la mission et son éventuel renouvellement ?",
    "start": "Quelle est la date de démarrage souhaitée (date précise ou ASAP) ?",
    "tjm_max": "Quel est le budget / TJM maximum que le client accepte ?",
    "astreinte": "Des astreintes sont-elles prévues (fréquence, périmètre, compensation) ?",
    "english": "Quel niveau d'anglais est réellement exigé (échanges quotidiens, rédaction, réunions) ?",
}


def analyze_brief(title: str, brief: str, *, source_ref: str = "brief", source_kind: SourceKind = SourceKind.OFFICIAL_BRIEF,
                  source_date: date | None = None, source_author: str = "") -> BriefAnalysis:
    text = normalize_text(brief)
    blocks = _split_blocks(text)
    reqs: dict[str, Req] = {}
    activity_counts: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    ctx_lines: list[str] = []
    obj_lines: list[str] = []
    negated: set[str] = set()
    positive: set[str] = set()

    section_keys: dict[str, list[str]] = {}
    for section, sent, _off in blocks:
        f = fold_compact(sent)
        sk_kind = source_kind
        if _CLIENT_CONFIRMED.search(f):
            sk_kind = SourceKind.CLIENT_CONFIRMED_IMPERATIVE
        if g := _GROUP.search(f):
            members = [k for k in section_keys.get(section, []) if reqs.get(k) and reqs[k].kind == "skill" and reqs[k].dimension == "technique"]
            if len(members) >= 2:
                n = 2 if g.group(1) == "plusieurs" else (_num(g.group(2) or "") or 1)
                gcat = Category.ELIMINATOIRE if _ELIM.search(f) else (Category.IMPERATIF if _IMPER.search(f) or section == "mandatory" else Category.DIFFERENCIANT)
                gkey = f"group:{section}"
                reqs[gkey] = Req(
                    id=new_id(), key=gkey, kind="custom", dimension="technique", category=gcat,
                    label=f"Expérience effective sur au moins {n} technologies parmi : " + ", ".join(reqs[k].label for k in members),
                    params={"at_least": n, "members": members}, source_kind=sk_kind, source_ref=source_ref,
                    source_date=source_date, source_author=source_author, quote=sent[:300],
                    rationale="Exigence de groupe (§5.5) : la combinaison est imposée, pas chaque technologie prise isolément.",
                )
                for k in members:   # les membres sont évalués individuellement mais notés via le groupe
                    reqs[k].params = {**reqs[k].params, "group": gkey}
                    if reqs[k].category in (Category.IMPERATIF, Category.ELIMINATOIRE):
                        reqs[k].category = Category.DIFFERENCIANT
                        reqs[k].rationale = "Membre d'un groupe imposé : seule la combinaison est exigée (cf. exigence de groupe)."
                continue
        if section == "context" and not lx.detect_skills(sent):
            ctx_lines.append(sent)
        elif section == "context" or re.search(r"\bcontexte\b|dans le cadre|projet de", f):
            ctx_lines.append(sent)
        if section == "tasks" or re.search(r"objectif|afin de|livrable|mission", f):
            obj_lines.append(sent)
        cat, why = _category_for(section, f)
        detected = lx.detect_skills(sent, prune_nested=True)
        folded_children: dict[str, list[str]] = {}
        for parent in detected:                     # « Dell EMC (PowerStore, PowerMax) » : un seul constructeur
            for ck in parent.umbrella_of:
                if (child := lx.skill(ck)) is not None and child in detected:
                    folded_children.setdefault(parent.key, []).extend(child.aliases)
        child_keys = {ck for p_ in detected for ck in p_.umbrella_of if p_.key in folded_children}
        for sk in [d for d in detected if d.key not in child_keys]:
            dim, kind = _dimension_for(sk)
            key = f"{kind}:{sk.key}"
            if sk.kind == "method" and sk.boolean_generic and cat in (Category.A_CLARIFIER, Category.SOUHAITABLE, Category.DIFFERENCIANT) and not _IMPER.search(f):
                cat_sk, why_sk = (Category.CONTEXTUEL, "Méthode/outil générique : non discriminant, contextuel sauf précision du client.")
            else:
                cat_sk, why_sk = cat, why
            if _NEG_NEED.search(f):
                negated.add(key)
            elif cat_sk in (Category.IMPERATIF, Category.ELIMINATOIRE):
                positive.add(key)
            depth = "advanced" if (_ADVANCED.search(f) and sk.advanced_terms) else "practice"
            recent = bool(_RECENT.search(f))
            win = None
            if m := re.search(r"derniers? (\d{1,2}) ans|(\d{1,2}) dernieres? annees", f):
                win = int(m.group(1) or m.group(2))
            versions = sorted({v for n, v in _VERSION.findall(f) if fold(n) in fold(sk.label) or fold(sk.label) in fold(n) or any(fold(a) == fold(n) for a in sk.aliases)})
            r = Req(
                id=new_id(), key=key, label=sk.label, category=cat_sk, dimension=dim, kind=kind,
                terms=list(dict.fromkeys([*sk.aliases, *folded_children.get(sk.key, [])])), depth_required=depth,
                recency_sensitive=True if recent else None, recency_window_years=(win or (3 if recent else None)),
                params={"versions": versions} if versions else {},
                source_kind=sk_kind, source_ref=source_ref, source_date=source_date, source_author=source_author,
                quote=sent[:300], rationale=why_sk,
                clarification_question=(f"« {sk.label} » est-il impératif, souhaitable ou simplement contextuel pour ce poste ?" if cat_sk == Category.A_CLARIFIER else ""),
            )
            if sk.kind == "soft":
                r.params["level"] = (extract_modalities(sent).get("english") or "non précisé")
            section_keys.setdefault(section, [])
            if key not in section_keys[section]:
                section_keys[section].append(key)
            prev = reqs.get(key)
            if prev is None or _STRENGTH[r.category] > _STRENGTH[prev.category]:
                if prev is not None and prev.depth_required == "advanced":
                    r.depth_required = "advanced"
                reqs[key] = r
            elif r.depth_required == "advanced":
                prev.depth_required = "advanced"
            if sk.kind == "activity":
                a = activity_counts.setdefault(sk.key, {"key": sk.key, "label": sk.label, "mentions": 0, "in_tasks": False, "quote": sent[:200]})
                a["mentions"] += 1
                a["in_tasks"] = a["in_tasks"] or section == "tasks"

    # contradiction interne du brief : même compétence niée puis imposée
    for key in negated & positive:
        r = reqs[key]
        r.category = Category.A_CLARIFIER
        r.rationale = "Le brief présente cette compétence à la fois comme nécessaire et comme non nécessaire."
        r.clarification_question = f"Le brief est ambigu sur « {r.label} » : est-ce une exigence ou non ?"
        warnings.append(f"Ambiguïté interne du brief sur « {r.label} » : à clarifier avec le client.")

    # ancienneté demandée
    for sent_section, sent, _ in blocks:
        f = fold_compact(sent)
        if m := re.search(r"(\d{1,2})\s*(?:a\s*\d{1,2}\s*)?\+?\s*(?:ans|annees|years)", f):
            if re.search(r"experience|exp\b|minimum|au moins|seniorite|years", f) and not re.search(r"duree|mission de|renouvel", f):
                yrs = float(m.group(1))
                cat, why = _category_for(sent_section, f)
                if cat == Category.CONTEXTUEL and not _NEG_NEED.search(f):
                    cat = Category.A_CLARIFIER
                skills_here = [s for s in lx.detect_skills(sent) if s.kind in ("tech", "product", "activity")]
                key = f"years:{skills_here[0].key}" if len(skills_here) == 1 else "years:global"
                label = (f"{int(yrs)} ans d'expérience sur {skills_here[0].label}" if len(skills_here) == 1 else f"{int(yrs)} ans d'expérience pertinente")
                reqs.setdefault(key, Req(
                    id=new_id(), key=key, label=label, category=cat, dimension="seniorite", kind="years",
                    terms=list(skills_here[0].aliases) if len(skills_here) == 1 else [], min_years=yrs,
                    source_kind=source_kind, source_ref=source_ref, source_date=source_date, source_author=source_author,
                    quote=sent[:300], rationale=why + " (l'ancienneté se mesure sur les missions réellement exercées, pas sur le nombre d'années brut)",
                    clarification_question=("Cette ancienneté est-elle exigée ou indicative ?" if cat == Category.A_CLARIFIER else ""),
                ))

    mods = extract_modalities(text)
    cons: list[tuple[str, str, dict[str, Any], str]] = []
    if "onsite_days_per_week" in mods:
        cons.append(("constraint:presence", f"Présence sur site : {mods['onsite_days_per_week']} j/semaine" + (f" à {mods['city']}" if mods.get("city") else ""),
                     {"onsite_days": mods["onsite_days_per_week"], "city": mods.get("city")}, "onsite"))
    elif mods.get("city"):
        cons.append(("constraint:location", f"Localisation : {mods['city']}", {"city": mods["city"]}, "city"))
    if mods.get("astreinte") is True:
        cons.append(("constraint:astreinte", "Astreintes prévues", {"astreinte": True}, "astreinte"))
    if "tjm_max" in mods:
        cons.append(("constraint:tjm", f"TJM maximum communiqué : {mods['tjm_max']} €", {"tjm_max": mods["tjm_max"]}, "tjm"))
    if mods.get("timezone_constraint"):
        cons.append(("constraint:timezone", "Contrainte de fuseau horaire mentionnée", {"timezone": True}, "tz"))
    for key, label, params, tag in cons:
        reqs[key] = Req(
            id=new_id(), key=key, label=label, category=Category.IMPERATIF, dimension="contrainte", kind="constraint",
            params=params, source_kind=source_kind, source_ref=source_ref, source_date=source_date, source_author=source_author,
            quote=label, rationale="Modalité annoncée dans le brief : évaluée séparément des compétences (§15.8), jamais dans le score technique.",
        )

    role = lx.detect_role_family(title)
    acts = sorted(activity_counts.values(), key=lambda a: (-int(a["in_tasks"]), -a["mentions"]))
    # Le titre ne suffit pas (§4) : compare métier annoncé et travail décrit.
    if role and acts:
        title_f = fold(title)
        for a in acts:
            sk_a = lx.skill(a["key"])
            if a["in_tasks"] and sk_a and sk_a.rarity >= 3 and a["key"] not in role.discriminants and not re.search(re.escape(fold(a["label"])), title_f):
                warnings.append(
                    f"Le titre évoque « {role.label} » mais le brief décrit aussi un travail de type « {a['label']} » : "
                    "vérifier avec le client la part réelle de cette activité avant de construire la recherche.")
    if role is None:
        warnings.append("Famille de métier non reconnue à partir du titre : les synonymes d'intitulés devront être saisis ou validés manuellement.")

    missing = [{"field": k, "question": q} for k, q in _MISSING_Q.items() if k not in mods]
    if mods.get("remote") == "full":
        missing = [m for m in missing if m["field"] != "onsite_days_per_week"]

    techs = [r.label for r in reqs.values() if r.kind == "skill" and r.dimension == "technique"]
    work = ", ".join(a["label"] for a in acts[:4]) or "non explicité dans le brief"
    summary = f"Travail attendu (d'après les missions décrites) : {work}. Intitulé annoncé : {title}."
    return BriefAnalysis(
        title=title, role_family=role.key if role else None, role_label=role.label if role else None,
        context=ctx_lines[:6], objectives=obj_lines[:8], activities=acts, technologies=techs,
        modalities=mods, requirements=sorted(reqs.values(), key=lambda r: -_STRENGTH[r.category]),
        missing_info=missing, warnings=warnings, real_work_summary=summary,
    )
