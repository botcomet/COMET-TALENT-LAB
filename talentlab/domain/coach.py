"""Talent Coach (§18) : expliquer le raisonnement professionnel, pas seulement reformuler un score.

Les réponses s'appuient sur les données de la mission quand elles existent (recherche, critère, preuves) et sur des
explications métier relues. Aucun classement ni statistique d'erreurs par recruteur n'est produit ni stocké.
"""
from __future__ import annotations

import re
from typing import Any

from . import lexicon as lx
from .qualification import TEMPLATES
from .text import fold

CONCEPTS: dict[str, dict[str, str]] = {
    "amoa_moe": {
        "title": "AMOA et MOE sur un projet SAP",
        "body": ("L'AMOA (assistance à maîtrise d'ouvrage) exprime et défend le besoin métier : ateliers, spécifications fonctionnelles, recette, coordination avec l'intégrateur. "
                 "La MOE (maîtrise d'œuvre) réalise : paramétrage (SPRO/customizing), développements, tests unitaires, transports. "
                 "Un consultant AMOA peut être excellent sans jamais avoir configuré le système : ne pas le présenter comme expert technique hands-on sans preuve. "
                 "Question de vérité : « étais-tu responsable du paramétrage, ou de l'expression du besoin et de la coordination avec l'intégrateur ? »"),
    },
    "data_roles": {
        "title": "Analyse, Data Quality, Data Engineering, MDM, migration : des métiers voisins, pas interchangeables",
        "body": ("Un Data Analyst orienté reporting construit des tableaux de bord ; il ne réconcilie pas forcément source et cible, ne définit pas de règles de qualité et ne suit pas la correction des anomalies. "
                 "Le Data Quality traite les écarts : rapprochement, règles de qualité, KPIs de complétude/unicité, fiabilisation post-migration. "
                 "Le Data Engineer construit des pipelines ; le profil MDM/migration travaille mapping, référentiels, harmonisation et gouvernance. "
                 "Pour une mission de qualité post-migration, chercher d'abord la preuve de réconciliation et de contrôles de cohérence, pas la maîtrise d'un outil de BI."),
    },
    "design_system": {
        "title": "Utiliser un Design System ≠ en concevoir un",
        "body": ("Consommer une bibliothèque de composants montre de la dextérité front-end. Concevoir un Design System suppose : composants réutilisables, tokens et variants, gouvernance et processus de contribution, "
                 "documentation (Storybook), accessibilité, packaging et diffusion, adoption multi-équipes ou multi-marques, accompagnement des développeurs. "
                 "Pour vérifier : « sur quel DS as-tu personnellement travaillé, qu'as-tu conçu, comment était-il distribué, combien d'équipes l'utilisaient, quel était ton rôle dans la gouvernance ? »"),
    },
    "run_vs_dev": {
        "title": "Développer une application en production ≠ avoir fait du RUN",
        "body": ("Le RUN/N3, c'est la gestion des incidents, l'analyse des causes, les astreintes, la supervision, la gestion des problèmes et des changements, la documentation d'exploitation. "
                 "Une application déployée en production ne prouve rien de tout cela. Chercher un incident vécu de bout en bout et la part d'astreinte réellement assurée."),
    },
    "architecture": {
        "title": "Architecte décisionnaire ou contributeur ?",
        "body": ("Rédiger des spécifications fonctionnelles n'est pas porter l'architecture. Vérifier : qui concevait l'architecture cible, qui prenait les décisions, qui préparait les dossiers, "
                 "qui siégeait en Design Authority, qui définissait flux et intégrations. « J'ai participé aux choix d'architecture » plafonne à « partiellement démontré »."),
    },
    "mention_vs_proof": {
        "title": "Mention ≠ démonstration",
        "body": ("Un mot-clé dans une liste de compétences est une déclaration. Il devient une preuve quand on retrouve : le contexte, le projet, le rôle exact, les actions réellement effectuées, "
                 "les composants, les versions, la complexité, la durée, le résultat. « Kafka figure dans le CV » ne dit ni producteur/consommateur, ni volumétrie, ni exploitation."),
    },
    "boolean_basics": {
        "title": "Rappel et précision d'une recherche booléenne",
        "body": ("Chaque groupe imposé en AND réduit le vivier : plus il y en a, plus le risque de zéro résultat augmente (faux négatifs). Les groupes OR élargissent à des alternatives équivalentes. "
                 "Le booléen sert à DÉCOUVRIR ; le scoring vérifie ensuite strictement. Ajouter un NOT sans faux positif identifié peut exclure de bons CV. "
                 "Les filtres natifs (localisation, expérience, disponibilité) sont souvent plus fiables qu'un mot-clé de séniorité."),
    },
    "infra_presence": {
        "title": "Technologie présente dans l'environnement ≠ technologie administrée",
        "body": ("Travailler dans une entreprise qui utilise Huawei, Dell EMC ou Rubrik ne démontre pas une expertise opérationnelle. "
                 "Demander les opérations réalisées personnellement (provisioning, zoning, réplication, politiques de sauvegarde) et la volumétrie administrée."),
    },
    "kafka_depth": {
        "title": "Ce qui distingue un usage Kafka d'une maîtrise Kafka",
        "body": ("Producteur/consommateur, topics, retry et dead-letter : pratique de base. La profondeur se lit dans Kafka Connect, Avro/Schema Registry, partitions et rebalancing, exploitation d'un cluster, "
                 "volumétrie chiffrée et décisions d'architecture portées. La volumétrie d'un autre système du même CV (ex. paiements bancaires) ne se transfère pas à Kafka."),
    },
    "sap_gts": {
        "title": "SAP GTS général ≠ pratique d'E4H",
        "body": ("Une expertise GTS 11 ne démontre pas une pratique de GTS Edition for HANA : demander les différences rencontrées, les éléments migrés ou configurés, la phase du projet et la date de dernière pratique."),
    },
}

_TOPICS: list[tuple[str, str]] = [
    (r"amoa|moe|maitrise d'(ouvrage|oeuvre)", "amoa_moe"),
    (r"data (analyst|quality)|qualite des donnees|data quality|mdm|data engineer", "data_roles"),
    (r"design system|composants?", "design_system"),
    (r"\brun\b|support n3|astreinte|production", "run_vs_dev"),
    (r"architect", "architecture"),
    (r"mention|liste de competences|declare|prouve|preuve|demontre", "mention_vs_proof"),
    (r"restrictive|trop large|booleen|boolean|zero resultat|rappel|precision|\bnot\b|and|or ", "boolean_basics"),
    (r"huawei|dell|rubrik|infra|stockage|environnement", "infra_presence"),
    (r"kafka", "kafka_depth"),
    (r"e4h|gts", "sap_gts"),
]


def explain_search(search: dict[str, Any]) -> str:
    groups = search.get("groups", [])
    ands = [g for g in groups if g["kind"] != "role"]
    parts = [f"Cette recherche combine {len(ands)} groupe(s) imposé(s) en AND en plus du métier : "
             + ", ".join(f"« {g['label']} »" for g in ands) + "."]
    if len(ands) >= 4:
        parts.append(f"Avec {len(ands)} groupes obligatoires simultanés, un CV doit contenir tous ces termes : le risque de zéro résultat ou de bons profils manqués est élevé.")
    if len(ands) >= 2:
        parts.append("Pour l'élargir sans toucher au besoin client : retirer d'abord les critères secondaires (souhaitables), puis élargir les intitulés, puis passer en exploratoire — le matching continue de vérifier toutes les exigences.")
    risks = search.get("explanation", {}).get("false_negative_risks", [])
    if risks:
        parts.append("Risques de faux négatifs identifiés : " + " ".join(risks[:2]))
    return " ".join(parts)


def explain_criterion(c: dict[str, Any]) -> str:
    sk = lx.skill(c["key"].split(":", 1)[-1])
    bits = [f"« {c['label']} » : niveau « {c['level'].replace('_', ' ')} » ({c['points']}/{c['weight']} points). {c['justification']}"]
    if c.get("advanced_missing"):
        bits.append("Ce qui manque pour conclure à une pratique avancée : " + ", ".join(c["advanced_missing"][:5]) + ".")
    if sk and sk.note:
        bits.append("Point de vigilance métier : " + sk.note + ".")
    tpl = TEMPLATES.get(c["key"].split(":", 1)[-1])
    if tpl:
        bits.append("Ce qui constituerait une preuve : " + " ; ".join(tpl["proof"]) + ".")
    return " ".join(bits)


def answer(question: str, *, search: dict[str, Any] | None = None, criterion: dict[str, Any] | None = None) -> dict[str, Any]:
    f = fold(question)
    out: dict[str, Any] = {"answers": [], "related": []}
    if search and re.search(r"restrictive|zero|trop (peu|large)|booleen|recherche", f):
        out["answers"].append({"title": "Analyse de cette recherche", "body": explain_search(search)})
    if criterion:
        out["answers"].append({"title": f"Pourquoi ce niveau sur « {criterion['label']} »", "body": explain_criterion(criterion)})
    for pat, key in _TOPICS:
        if re.search(pat, f) and CONCEPTS[key] not in out["answers"]:
            out["related"].append({"key": key, **CONCEPTS[key]})
    for sk in lx.detect_skills(question, prune_nested=True)[:3]:
        if sk.note and not any(sk.note in a.get("body", "") for a in out["answers"] + out["related"]):
            out["related"].append({"key": sk.key, "title": sk.label, "body": sk.note + "."})
        tpl = TEMPLATES.get(sk.key)
        if tpl and not criterion:
            out["related"].append({"key": f"verifier_{sk.key}", "title": f"Comment vérifier « {sk.label} »",
                                   "body": "Question à poser : " + tpl["ask"].format(ctx="") + " Preuves attendues : " + " ; ".join(tpl["proof"]) + ". À approfondir si : " + " ; ".join(tpl["deepen"]) + "."})
    out["related"] = out["related"][:4]
    if not out["answers"] and not out["related"]:
        out["answers"].append({"title": "Je n'ai pas d'explication métier relue sur ce point",
                               "body": "Reformule en citant la compétence, le métier ou le critère concerné (par ex. « pourquoi ce profil a une faible note sur Kafka ? »). "
                                       "Les explications disponibles couvrent la recherche booléenne, la différence mention/preuve, AMOA/MOE, Data Quality, Design System, RUN, architecture et infrastructure."})
    out["note"] = "Explications pédagogiques : aucune statistique par recruteur n'est produite ni conservée."
    return out
