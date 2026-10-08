"""Préparation de la qualification : questions utiles, adaptées au CV (§16).

Pas de « Connaissez-vous Java ? » : chaque question demande un récit précis
(rôle exact, volumes, composants, résultat) et indique ce qui constitue une
preuve et ce qui impose d'approfondir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import lexicon as lx
from .enums import Level
from .text import fold

OPENERS_FORBIDDEN = re.compile(r"^\s*(connais[- ]?tu|connaissez[- ]vous|as[- ]tu d[ée]j[àa]|avez[- ]vous d[ée]j[àa]|es[- ]tu|[êe]tes[- ]vous|"
                               r"sais[- ]tu|savez[- ]vous|ma[iî]trises?[- ]tu|ma[iî]trisez[- ]vous|as[- ]tu utilis[ée]|avez[- ]vous utilis[ée])\b", re.I)
DETAIL_WORDS = ("rôle", "role", "décris", "decris", "explique", "précise", "precise", "comment", "combien", "quels", "quelles", "quel ", "quelle ",
                "décrire", "decrire", "exemple", "donne", "détaille", "detaille", "cite", "lesquel", "dans quel", "qu'as-tu", "qu'avais", "distingue", "qui ")


@dataclass
class Question:
    id: str
    criterion_key: str
    criterion_label: str
    priority: str                     # "prioritaire" | "complementaire"
    text: str
    answer_type: str
    proof_elements: list[str]
    deepen_if: list[str]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def is_generic(text: str) -> bool:
    """Détecte une question fermée / générique (« Connaissez-vous X ? », « Es-tu autonome ? »)."""
    t = text.strip()
    if OPENERS_FORBIDDEN.match(t) and len(t) < 160:
        return True
    return len(t) < 90 or not any(w in t.lower() for w in DETAIL_WORDS)


T = lambda ask, typ, proof, deepen: {"ask": ask, "type": typ, "proof": proof, "deepen": deepen}   # noqa: E731

TEMPLATES: dict[str, dict[str, Any]] = {
    "kafka": T("Peux-tu me décrire une architecture Kafka sur laquelle tu as travaillé{ctx}, ton rôle dans sa conception, les producteurs et consommateurs développés, les volumes traités et les mécanismes de gestion des erreurs ?",
               "récit détaillé d'un projet",
               ["topics / partitions nommés et leur rôle", "volumes chiffrés (messages par seconde ou par jour)", "retry / dead letter queue / idempotence", "ce qu'il a conçu lui-même vs ce qu'il a consommé"],
               ["ne cite que producteurs/consommateurs sans volumétrie", "dit « j'ai participé » sans préciser ses actions", "ne sait pas décrire un cas d'erreur ou un rebalancing"]),
    "kafka_connect": T("Décris les connecteurs Kafka Connect que tu as mis en place{ctx} : la source et la cible, les transformations, la gestion des erreurs et le suivi opérationnel.",
                       "exemple technique précis", ["connecteur source/sink nommé", "configuration (converters, SMT, tasks)", "gestion des erreurs et reprises"],
                       ["décrit Connect de façon théorique", "n'a jamais exploité de connecteur en production"]),
    "avro": T("Sur quels schémas Avro as-tu travaillé{ctx} ? Explique comment tu gérais l'évolution de schéma et la compatibilité avec le Schema Registry.",
              "exemple technique précis", ["règles de compatibilité (backward/forward)", "cas d'évolution réel", "outillage de registry"], ["ne connaît pas les règles de compatibilité"]),
    "spring": T("Sur quelle application Spring Boot as-tu travaillé{ctx} : quelle version, quels modules (Data, Security, Batch…), quelle était ta contribution personnelle et quelles difficultés de production as-tu rencontrées ?",
                "récit détaillé d'un projet", ["version et modules précis", "choix techniques portés personnellement", "incident ou optimisation concret"], ["reste au niveau « j'utilisais Spring »"]),
    "java": T("Décris le projet Java le plus complexe que tu as mené{ctx} : version du JDK, architecture, ta responsabilité exacte, les contraintes de performance ou de volumétrie et les arbitrages techniques que tu as faits.",
              "récit détaillé d'un projet", ["version et contexte précis", "décisions techniques personnelles", "indicateurs de performance"], ["ne distingue pas ce qu'il a conçu de ce qu'il a maintenu"]),
    "design_system": T("Sur quel Design System as-tu personnellement travaillé{ctx} ? Qu'as-tu conçu, comment était-il distribué, combien d'équipes l'utilisaient et quel rôle avais-tu dans la gouvernance et l'adoption ?",
                       "récit détaillé d'un projet", ["composants et tokens conçus par lui", "mode de distribution (package versionné)", "nombre d'équipes / marques et mesure de l'adoption", "processus de contribution et de gouvernance"],
                       ["consomme un Design System existant sans l'avoir conçu", "ne cite ni gouvernance ni adoption", "pas de volumétrie d'équipes"]),
    "storybook": T("Comment as-tu utilisé Storybook{ctx} : documentation des composants, tests visuels, publication ? Qui l'alimentait et à quelle échelle ?",
                   "exemple précis", ["rôle dans la documentation", "intégration à la CI", "échelle (nombre de composants/équipes)"], ["simple consommateur de Storybook"]),
    "react": T("Sur quel produit React as-tu travaillé{ctx} : taille de l'application, architecture de l'état, performances et ta responsabilité exacte dans les choix techniques ?",
               "récit détaillé d'un projet", ["architecture décrite", "optimisations mesurées", "choix personnels"], ["reste générique"]),
    "sap_config": T("Sur ce projet SAP{ctx}, étais-tu responsable du paramétrage et de la configuration (SPRO, customizing), ou principalement de l'expression du besoin et de la coordination avec l'intégrateur ? Donne un exemple de paramétrage que tu as réalisé toi-même.",
                    "récit détaillé d'un projet", ["chemins SPRO / tables de configuration cités", "objets développés ou paramétrés par lui", "tests unitaires et transport réalisés"], ["décrit uniquement des ateliers et des spécifications", "ne peut citer aucun paramétrage précis"]),
    "sap_amoa": T("Quel était ton rôle exact côté AMOA{ctx} : ateliers, spécifications fonctionnelles, recette, coordination avec l'intégrateur ? Quelle partie as-tu portée seul ?",
                  "récit détaillé d'un projet", ["livrables AMOA nommés", "interlocuteurs", "périmètre de décision"], ["confond AMOA et configuration technique"]),
    "sap_gts": T("Peux-tu décrire ton dernier projet SAP GTS{ctx} : modules concernés (compliance, customs, risk), système amont, volumes de documents et ton niveau d'intervention (conception, configuration, support) ?",
                 "récit détaillé d'un projet", ["modules GTS précis", "intégration avec les systèmes feeder", "date et durée de la dernière pratique"], ["pratique ancienne", "uniquement du support ponctuel"]),
    "sap_gts_e4h": T("Décris ta pratique concrète de SAP GTS Edition for HANA (E4H){ctx} : phase du projet, différences avec GTS 11 que tu as rencontrées, éléments que tu as toi-même configurés ou migrés.",
                     "exemple technique précis", ["différences fonctionnelles E4H vs GTS 11 citées", "éléments migrés / configurés", "version et phase"], ["expertise GTS générale sans pratique d'E4H"]),
    "data_quality": T("Comment procédais-tu pour comparer les données source et cible, identifier les écarts, définir les règles de qualité et vérifier la correction des anomalies{ctx} ? Donne les volumes, les outils et le nombre de règles.",
                      "récit détaillé d'un projet", ["méthode de rapprochement source/cible", "nombre et type de règles de qualité", "KPIs suivis", "boucle de correction avec les équipes"],
                      ["décrit du reporting sans contrôle de cohérence", "aucune règle de qualité formalisée"]),
    "reconciliation": T("Décris une réconciliation source/cible que tu as menée{ctx} : périmètre, requêtes ou outils, typologie des écarts trouvés et comment ils ont été résorbés.",
                        "exemple technique précis", ["requêtes / outils nommés", "taux d'écart avant/après", "pays ou entités concernés"], ["pas de chiffres sur les écarts"]),
    "data_migration": T("Sur quelle migration de données as-tu travaillé{ctx} : volumes, mapping, extraction/transformation/chargement, cutover et ta responsabilité exacte ?",
                        "récit détaillé d'un projet", ["mapping réalisé par lui", "volumétrie", "plan de bascule"], ["participation sans responsabilité sur le mapping"]),
    "mdm": T("Sur quel référentiel / MDM as-tu travaillé{ctx} : domaines de données, règles d'harmonisation, gouvernance et ton rôle exact ?",
             "récit détaillé d'un projet", ["domaine de données précis", "règles de dédoublonnage", "gouvernance"], ["notions théoriques"]),
    "f5": T("Quelle a été ta pratique de F5{ctx} : modules (LTM, GTM, ASM), nombre de virtual servers gérés, iRules ou AS3 écrits par toi, certificats et politiques de persistance ?",
            "exemple technique précis", ["iRules / AS3 écrits", "volumétrie de virtual servers", "dépannage concret"], ["connaissance de nom seulement", "administration sans conception"]),
    "netscaler": T("Quelle expérience as-tu des politiques et configurations NetScaler{ctx} ? Décris un audit de configuration que tu as mené et ce que tu as dû traduire.",
                   "récit détaillé d'un projet", ["politiques précises citées", "audit réalisé", "points difficiles de traduction"], ["pas d'audit réel"]),
    "network_migration": T("Décris la migration de load balancers que tu as pilotée ou réalisée{ctx} : l'audit, le mapping des politiques, les tests, le plan de rollback et ce que tu as transmis à l'équipe d'exploitation.",
                           "récit détaillé d'un projet", ["audit et inventaire", "plan de rollback", "tests de validation", "transfert de compétences"], ["a seulement participé", "pas de responsabilité sur la bascule"]),
    "load_balancing": T("Décris une architecture de répartition de charge que tu as conçue ou fait évoluer{ctx} : algorithmes, persistance, supervision des membres et incident marquant.",
                        "exemple technique précis", ["choix d'algorithme justifiés", "supervision", "incident résolu"], ["reste théorique"]),
    "run_n3": T("Décris ton rôle sur le support de niveau 3{ctx} : types d'incidents traités, comment tu menais l'analyse des causes, ta participation aux astreintes, la gestion des problèmes et des changements, et ce que tu documentais.",
                "récit détaillé d'un projet", ["incident réel décrit de bout en bout", "astreintes (fréquence, périmètre)", "post-mortem / gestion des problèmes", "documentation produite"],
                ["application développée puis passée à l'exploitation sans responsabilité de run", "aucun incident marquant cité"]),
    "architecture": T("Sur cette architecture{ctx}, qui concevait l'architecture cible, qui prenait les décisions, qui préparait les dossiers et as-tu participé à la Design Authority ? Quel choix structurant as-tu porté personnellement ?",
                      "récit détaillé d'un projet", ["décision personnelle documentée", "dossier d'architecture rédigé par lui", "passage en comité d'architecture"],
                      ["rédige des spécifications sans décider de l'architecture", "contributeur sur les choix"]),
    "tech_mgmt": T("Comment encadrais-tu techniquement l'équipe{ctx} : taille, revues de code, arbitrages, standards posés, accompagnement des juniors ? Reste-t-il du développement hands-on dans ton quotidien ?",
                   "récit détaillé d'un projet", ["taille d'équipe", "revues de code et standards", "part de développement"], ["management sans pratique technique"]),
    "migration": T("Sur ce projet de migration{ctx}, quel était ton périmètre, ta responsabilité sur le plan de bascule et le retour arrière, et qu'as-tu livré ?",
                   "récit détaillé d'un projet", ["plan de migration livré", "rollback", "volumétrie"], ["participation sans responsabilité de réalisation"]),
    "huawei": T("Quelle est ta pratique opérationnelle des baies Huawei{ctx} : modèles, opérations réalisées toi-même (provisioning, réplication, supervision) et volumétrie administrée ? Distingue ce que tu administrais de ce qui existait dans l'environnement.",
                "exemple technique précis", ["modèles et opérations nommés", "volumétrie administrée", "incident traité"], ["technologie simplement présente dans l'environnement"]),
    "dell_emc": T("Quelle est ta pratique de la gamme Dell EMC (PowerStore, PowerMax, Unity){ctx} : modèles administrés, opérations réalisées toi-même et migrations menées ?",
                  "exemple technique précis", ["modèles et opérations nommés", "migrations réalisées"], ["présence dans l'environnement sans administration"]),
    "rubrik": T("Comment as-tu mis en œuvre Rubrik{ctx} : politiques SLA, restaurations réelles, tests de reprise, volumétrie sauvegardée ?",
                "exemple technique précis", ["politiques définies", "restauration réelle", "volumétrie"], ["simple utilisateur de la console"]),
    "san": T("Décris ta pratique des réseaux SAN{ctx} : zoning, masking, multipathing, équipements et volumétrie ; quelles opérations as-tu réalisées toi-même ?",
             "exemple technique précis", ["zoning / masking réalisés", "équipements nommés", "dépannage"], ["connaissance théorique"]),
    "risk_mgmt": T("Quelle était ta responsabilité réelle dans la gestion des risques{ctx} : méthode (EBIOS RM…), registre tenu, plans de traitement, comités et risques résiduels que tu as portés ?",
                   "récit détaillé d'un projet", ["registre ou cartographie produits", "plan de traitement suivi", "comité"], ["connaissance théorique de la méthode"]),
    "tprm": T("Comment as-tu géré la sécurité des tiers{ctx} : questionnaires, évaluation des fournisseurs, clauses avec les achats et le juridique, suivi des plans d'action ?",
              "récit détaillé d'un projet", ["processus d'évaluation en place", "collaboration achats / juridique", "suivi des écarts"], ["collaboration non décrite"]),
    "security_by_design": T("Comment intégrais-tu la sécurité dès la conception des projets{ctx} : revues d'architecture, exigences, analyses de risques, validation avant mise en production ?",
                            "récit détaillé d'un projet", ["revues réalisées", "exigences produites", "go/no-go sécurité"], ["audit après coup uniquement"]),
    "retail_ecom": T("Sur quelle plateforme e-commerce ou retail as-tu réellement développé ou testé{ctx} : parcours d'achat, commandes, OMS, paiement, stock, catalogue ? Qu'as-tu réalisé personnellement ?",
                     "récit détaillé d'un projet", ["modules métier nommés", "flux magasins / sites", "volumes de commandes"], ["environnement e-commerce sans réalisation applicative", "infrastructure d'un site personnel"]),
    "high_volume": T("Quels volumes traitait précisément le système sur lequel tu as travaillé{ctx} (transactions par jour ou par seconde, pics) et quelle part de cette charge relevait de ton périmètre ?",
                     "chiffres précis", ["débit chiffré", "pics et dimensionnement", "périmètre personnel"], ["chiffres globaux de l'entreprise sans lien avec son périmètre"]),
    "postman": T("Comment as-tu structuré tes tests d'API avec Postman{ctx} : collections, environnements, assertions, exécution automatisée, anomalies détectées ?",
                 "exemple technique précis", ["collections / assertions", "automatisation", "anomalies trouvées"], ["usage manuel basique"]),
    "api_management": T("Sur quelle plateforme d'API Management as-tu travaillé{ctx} : politiques, quotas, OAuth, versioning ; qu'as-tu testé ou configuré toi-même ?",
                        "exemple technique précis", ["politiques nommées", "scénarios de test API"], ["connaissance générale"]),
    "test_design": T("Comment rédigeais-tu tes cas de test{ctx} : à partir de quelles exigences, quelle couverture, quels jeux de données, quelle traçabilité ? Donne un exemple chiffré.",
                     "exemple chiffré", ["nombre de cas et couverture", "traçabilité exigences ↔ tests"], ["pas de méthode explicite"]),
    "recette": T("Décris une campagne de recette que tu as conduite{ctx} : périmètre, nombre de cas, gestion des anomalies, reporting et décision de go/no-go.",
                 "récit détaillé d'un projet", ["campagne chiffrée", "go/no-go", "reporting livré"], ["exécution seulement"]),
    "defect_mgmt": T("Comment gérais-tu les anomalies{ctx} : criticité, circuit de traitement, comités, reporting et cycle de vie dans l'outil ?",
                     "exemple précis", ["circuit défini", "indicateurs suivis"], ["saisie d'anomalies seulement"]),
    "sql": T("Donne un exemple de requête SQL complexe que tu as écrite{ctx} : tables concernées, jointures, optimisation et gain obtenu ; quelle autonomie avais-tu sur la base ?",
             "exemple technique précis", ["jointures / fenêtrage", "plan d'exécution et gain"], ["requêtes simples uniquement"]),
    "reflex_wms": T("Quelle est ta pratique du WMS Reflex{ctx} : modules, flux paramétrés, incidents de run traités, évolutions réalisées ? Décris une situation de support concrète.",
                    "récit détaillé d'un projet", ["modules Reflex nommés", "paramétrage réalisé", "incident de run résolu"], ["expérience WMS d'un autre éditeur", "aucune pratique Reflex citée"]),
    "kubernetes": T("Sur quel cluster Kubernetes as-tu travaillé{ctx} : taille, déploiements, ingress, observabilité, incident marquant et ce que tu as administré toi-même ?",
                    "exemple technique précis", ["taille du cluster", "opérations réalisées", "incident"], ["utilisateur de pods déployés par d'autres"]),
    "aws": T("Quels services AWS as-tu mis en œuvre toi-même{ctx} (pas seulement utilisés) : architecture, IAM, coûts, incident ? Distingue ce que tu administrais de ce que tu consommais.",
             "exemple technique précis", ["services nommés avec usage précis", "responsabilité réelle"], ["utilisation sans administration"]),
}

_GENERIC = ("Décris une mission où tu as concrètement mis en œuvre « {label} »{ctx} : le contexte, ton rôle exact (conçu, réalisé ou seulement participé), "
            "les composants concernés{depth}, la taille ou les volumes de l'environnement et le résultat obtenu.")


def _context_for(result: dict[str, Any]) -> str:
    for e in result.get("evidence", []):
        if e.get("source") == "cv" and e.get("company"):
            return f" (ton CV le mentionne chez {e['company']}, {e.get('period', 'dates non précisées')})"
        if e.get("source") == "cv" and e.get("location") == "skills_list":
            return " (cité dans ta liste de compétences, sans projet associé sur ton CV)"
    return ""


def _skill_key(criterion_key: str) -> str:
    return criterion_key.split(":", 1)[1] if ":" in criterion_key else criterion_key


def _make(res: dict[str, Any], priority: str, idx: int, rationale: str) -> Question:
    sk_key = _skill_key(res["key"])
    tpl = TEMPLATES.get(sk_key)
    ctx = _context_for(res)
    if tpl is None:
        sk = lx.skill(sk_key)
        depth = (" (" + ", ".join(sk.depth_terms[:4]) + ")") if sk and sk.depth_terms else ""
        text = _GENERIC.format(label=res["label"], ctx=ctx, depth=depth)
        proof = ["exemple précis et vérifiable", "rôle personnel distinct du rôle de l'équipe", "chiffres ou résultats"]
        deepen = ["réponse générique ou théorique", "reste au niveau « on faisait »"]
        atype = "récit détaillé d'un projet"
    else:
        text = tpl["ask"].format(ctx=ctx)
        proof, deepen, atype = list(tpl["proof"]), list(tpl["deepen"]), tpl["type"]
    if res.get("advanced_missing") and res.get("level") != Level.CONFIRMED.value:
        proof = proof + [f"indices de profondeur attendus : {', '.join(res['advanced_missing'][:4])}"]
    if res.get("contradictions"):
        deepen = ["lever la contradiction entre le CV et l'échange : " + res["contradictions"][0]["statement"][:120]] + deepen
    return Question(id=f"q{idx}", criterion_key=res["key"], criterion_label=res["label"], priority=priority, text=text, answer_type=atype,
                    proof_elements=proof, deepen_if=deepen, rationale=rationale)


def generate(assessment: dict[str, Any], *, max_priority: int = 3, max_complementary: int = 5) -> dict[str, Any]:
    """Trois questions prioritaires + jusqu'à cinq complémentaires, ciblées sur les critères non démontrés les plus pesants."""
    crits = [c for c in assessment["criteria"] if c["level"] != Level.CONFIRMED.value]
    def gap(c: dict[str, Any]) -> float:
        base = c["weight"] * (1.0 - c["factor"])
        return base + (1000 if c.get("mandatory") else 0) + (500 if c["level"] == Level.NOT_DOCUMENTED.value and c.get("mandatory") else 0)
    crits.sort(key=lambda c: -gap(c))
    out_p: list[Question] = []
    out_c: list[Question] = []
    n = 0
    for c in crits:
        if c["level"] == Level.CONTRADICTED.value and not c.get("contradictions"):
            continue
        n += 1
        why = {Level.NOT_DOCUMENTED.value: "Aucune information dans le CV : seule une réponse du candidat peut conclure (pas de verdict définitif).",
               Level.DECLARED.value: "Compétence citée sans réalisation décrite : demander un exemple concret.",
               Level.PARTIAL.value: "Expérience pertinente mais périmètre incomplet : préciser le rôle et la profondeur.",
               Level.CONTRADICTED.value: "Contradiction entre sources : à clarifier avec le candidat."}[c["level"]]
        if c.get("mandatory"):
            why = "IMPÉRATIF — " + why
        q = _make(c, "prioritaire" if len(out_p) < max_priority else "complementaire", n, why)
        (out_p if q.priority == "prioritaire" else out_c).append(q)
    # contraintes inconnues : questions courtes mais jamais génériques
    for k in assessment.get("constraints", []):
        if k.get("question") and len(out_c) < max_complementary:
            n += 1
            out_c.append(Question(id=f"q{n}", criterion_key=k["key"], criterion_label=k["label"], priority="complementaire", text=k["question"],
                                  answer_type="information factuelle", proof_elements=["réponse précise datée / chiffrée"],
                                  deepen_if=["réponse évasive : ne pas supposer, relancer"], rationale="Contrainte distincte de l'adéquation technique : information inconnue, jamais supposée."))
    return {"priority": [q.to_dict() for q in out_p[:max_priority]], "complementary": [q.to_dict() for q in out_c[:max_complementary]]}
