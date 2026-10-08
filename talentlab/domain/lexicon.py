"""Référentiel de compétences, rôles et activités.

Ce n'est pas un moteur de décision : c'est la connaissance métier sur laquelle
s'appuient le moteur booléen, le détecteur de preuves et les questions de
qualification. Il est extensible (``register``) — les enseignements validés de
la bibliothèque collective peuvent l'enrichir sans toucher au code.

Distinctions essentielles (§5.3) :
- ``aliases``  : formes *réellement équivalentes* → utilisables dans un groupe OR.
- ``related``  : termes voisins mais non équivalents → jamais mélangés aux alias,
                 réservés à l'élargissement exploratoire.
- ``depth_terms`` / ``advanced_terms`` : ce qui distingue une réalisation
  démontrée d'une simple mention (§6.5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .text import fold, term_pattern
import re


@dataclass(frozen=True)
class Skill:
    key: str
    label: str
    kind: str                                   # tech | product | domain | activity | method | soft
    aliases: tuple[str, ...]
    related: tuple[str, ...] = ()
    family: str = ""
    rarity: int = 3                             # 1 ubiquitaire … 5 très spécifique
    depth_terms: tuple[str, ...] = ()
    advanced_terms: tuple[str, ...] = ()
    context_needed: tuple[str, ...] = ()        # garde-fou d'ambiguïté (alias nus)
    case_sensitive: tuple[str, ...] = ()        # alias à comparer en respectant la casse
    volatile: bool = False                      # la récence compte par défaut
    boolean_generic: bool = False               # trop générique pour un booléen
    confusable_with: tuple[str, ...] = ()
    narrowers: tuple[str, ...] = ()             # termes qui resserrent vers une pratique plus avancée (booléen strict)
    label_is_term: bool = True                  # faux si le libellé est descriptif (« RUN / Support N3 ») et non une expression de CV
    umbrella_of: tuple[str, ...] = ()           # clés de gammes rattachées à ce constructeur / produit-mère
    note: str = ""


@dataclass(frozen=True)
class RoleFamily:
    key: str
    label: str
    titles: tuple[str, ...]                     # intitulés équivalents (FR/EN)
    wide_titles: tuple[str, ...] = ()           # variantes moins sûres, exploratoire seulement
    discriminants: tuple[str, ...] = ()         # clés de compétences qui distinguent le métier
    neighbors: tuple[str, ...] = ()             # (métier voisin, ce qui le distingue)
    triggers: tuple[str, ...] = ()              # marqueurs de détection dans un titre de mission


_REGISTRY: dict[str, Skill] = {}
_ROLES: dict[str, RoleFamily] = {}


def S(key: str, label: str, kind: str, aliases: list[str] | None = None, **kw) -> Skill:
    if "note" in kw:
        kw["note"] = re.sub(r"\s*\(§[\d.]+\)", "", kw["note"])        # les notes sont lues par les recruteurs : pas de renvoi interne
    al = tuple(dict.fromkeys([label, *(aliases or [])]))
    kw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in kw.items()}
    sk = Skill(key=key, label=label, kind=kind, aliases=al, **kw)
    _REGISTRY[key] = sk
    return sk


def R(key: str, label: str, titles: list[str], **kw) -> RoleFamily:
    kw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in kw.items()}
    rf = RoleFamily(key=key, label=label, titles=tuple(titles), **kw)
    _ROLES[key] = rf
    return rf


def register(skill: Skill) -> None:
    _REGISTRY[skill.key] = skill


def skill(key: str) -> Skill | None:
    return _REGISTRY.get(key)


def all_skills() -> list[Skill]:
    return list(_REGISTRY.values())


def role_families() -> list[RoleFamily]:
    return list(_ROLES.values())


def role(key: str) -> RoleFamily | None:
    return _ROLES.get(key)


# ------------------------------------------------------------------ détection

def _alias_regex(alias: str, cs: bool) -> re.Pattern[str]:
    if cs:
        parts = [re.escape(p) for p in re.split(r"[\s\-_]+", alias.strip()) if p]
        return re.compile(rf"(?<![A-Za-z0-9+#]){r'[\s\-_/]*'.join(parts)}(?![A-Za-z0-9+#])")
    return re.compile(term_pattern(alias))


_ALIAS_CACHE: dict[tuple[str, str], list[re.Pattern[str]]] = {}


def _patterns(sk: Skill) -> list[tuple[str, bool, re.Pattern[str]]]:
    out = []
    for a in sk.aliases:
        cs = a in sk.case_sensitive
        out.append((a, cs, _alias_regex(a, cs)))
    return out


def mentions(sk: Skill, text: str, folded: str | None = None, window: int = 160) -> list[tuple[int, int, str]]:
    """Occurrences (start, end, alias) d'une compétence dans ``text``.

    Les alias « nus » ambigus (``context_needed``) ne comptent que si un mot de
    contexte figure dans les ``window`` caractères voisins ; un alias composé
    (« Spring Boot ») n'a pas besoin de contexte.
    """
    folded = folded if folded is not None else fold(text)
    found: list[tuple[int, int, str]] = []
    for alias, cs, rx in _patterns(sk):
        target = text if cs else folded
        for m in rx.finditer(target):
            if sk.context_needed and " " not in alias and "-" not in alias:
                lo, hi = max(0, m.start() - window), min(len(folded), m.end() + window)
                around = folded[lo:hi]
                if not any(re.search(term_pattern(c), around) for c in sk.context_needed):
                    continue
            found.append((m.start(), m.end(), alias))
    # supprime les occurrences incluses dans une plus longue (Kafka ⊂ Kafka Connect)
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    pruned: list[tuple[int, int, str]] = []
    for f in found:
        if pruned and f[0] >= pruned[-1][0] and f[1] <= pruned[-1][1]:
            continue
        pruned.append(f)
    return pruned


def detect_skills(text: str, kinds: tuple[str, ...] | None = None, prune_nested: bool = False) -> list[Skill]:
    """Compétences mentionnées dans ``text``.

    ``prune_nested`` écarte une compétence dont *toutes* les occurrences sont incluses
    dans l'occurrence plus longue d'une autre (« WMS » dans « Reflex WMS ») : utile pour
    analyser un brief sans compter deux fois la même exigence.
    """
    folded = fold(text)
    hits: list[tuple[Skill, list[tuple[int, int, str]]]] = []
    for sk in _REGISTRY.values():
        if kinds and sk.kind not in kinds:
            continue
        ms = mentions(sk, text, folded)
        if ms:
            hits.append((sk, ms))
    if not prune_nested:
        return [sk for sk, _ in hits]
    kept = []
    for sk, ms in hits:
        covered = all(any(o_sk is not sk and (o[0] <= m[0] and m[1] <= o[1]) and (o[1] - o[0]) > (m[1] - m[0])
                          for o_sk, oms in hits for o in oms) for m in ms)
        if not covered:
            kept.append(sk)
    return kept


def detect_role_family(title: str) -> RoleFamily | None:
    f = fold(title)
    best, best_len = None, 0
    for rf in _ROLES.values():
        for t in (*rf.titles, *rf.triggers):
            if re.search(term_pattern(t), f) and len(t) > best_len:
                best, best_len = rf, len(t)
    return best


# ============================================================ DONNÉES MÉTIER
# ---------- Java / back-end
S("java", "Java", "tech", [],
  family="jvm", rarity=2, volatile=False, narrowers=["Java 17", "Java 21"],
  depth_terms=["microservices", "api rest", "jvm", "multithread", "concurrence", "performance", "architecture", "tests unitaires", "junit"],
  related=["JEE", "J2EE", "Java EE"])
S("spring", "Spring Boot", "tech", ["Spring", "Spring Framework", "Spring MVC", "Spring Data", "Spring Cloud", "Spring Batch", "Spring Security"],
  family="jvm", rarity=3, context_needed=["boot", "java", "mvc", "data", "security", "cloud", "batch", "framework", "microservice", "jpa", "hibernate", "api"],
  depth_terms=["microservices", "rest", "jpa", "hibernate", "batch", "security", "actuator", "tests"])
S("kafka", "Kafka", "tech", ["Apache Kafka"],
  related=["Confluent", "Kafka Connect", "Kafka Streams", "Schema Registry", "Avro", "RabbitMQ", "Pulsar"],
  family="messaging", rarity=4, volatile=True,
  depth_terms=["producer", "producteur", "producteurs", "consumer", "consommateur", "consommateurs", "topic", "topics", "partition",
               "partitions", "offset", "broker", "cluster", "kafka connect", "connect", "avro", "schema registry", "dead letter",
               "dead-letter", "dlq", "retry", "rebalance", "kafka streams", "ksql", "consumer group", "exactly once", "idempotence",
               "debit", "throughput", "lag", "monitoring", "supervision", "exploitation", "administration"],
  advanced_terms=["kafka connect", "connect", "avro", "schema registry", "partition", "partitions", "rebalance", "kafka streams",
                  "ksql", "exactly once", "cluster", "brokers", "volumetrie", "throughput", "debit", "architecture", "exploitation",
                  "administration", "consumer group"],
  confusable_with=["rabbitmq"], narrowers=["Kafka Connect", "Avro", "Schema Registry", "Kafka Streams"])
S("kafka_connect", "Kafka Connect", "tech", [], family="messaging", rarity=5,
  depth_terms=["connector", "connecteur", "source connector", "sink", "source", "debezium", "jdbc", "transformations", "smt", "avro", "schema registry",
               "cluster", "topics", "dead letter"])
S("avro", "Avro", "tech", ["Apache Avro"], family="messaging", rarity=4, depth_terms=["schema", "schéma", "registry", "evolution", "compatibilite"])
S("rabbitmq", "RabbitMQ", "tech", ["Rabbit MQ"], family="messaging", rarity=3, confusable_with=["kafka"])
S("aws", "AWS", "tech", ["Amazon Web Services"], family="cloud", rarity=2, volatile=True,
  depth_terms=["ec2", "s3", "lambda", "iam", "vpc", "cloudformation", "terraform", "rds", "eks", "ecs"],
  related=["Azure", "GCP"], note="Utiliser AWS ≠ administrer toute l'infrastructure cloud (§6.5)")
S("aws_s3", "AWS S3", "tech", ["S3", "Amazon S3"], family="cloud", rarity=3, case_sensitive=["S3"])
S("docker", "Docker", "tech", [], family="devops", rarity=2, volatile=True)
S("kubernetes", "Kubernetes", "tech", ["K8s", "OpenShift"], family="devops", rarity=3, volatile=True,
  depth_terms=["helm", "ingress", "operator", "cluster", "deployment", "pods", "hpa"])
S("terraform", "Terraform", "tech", [], family="devops", rarity=3, volatile=True)
S("python", "Python", "tech", [], family="languages", rarity=2, volatile=False)
S("sql", "SQL", "tech", ["PL/SQL", "T-SQL", "requêtes SQL"], family="data", rarity=1,
  depth_terms=["jointures", "optimisation", "index", "procedures stockees", "window functions", "cte", "plan d'execution", "requetes complexes"])
S("postgresql", "PostgreSQL", "tech", ["Postgres"], family="data", rarity=3)
S("oracle", "Oracle", "tech", ["Oracle Database", "Oracle DB"], family="data", rarity=3, context_needed=["sql", "base", "database", "pl/sql", "dba", "plsql", "db"])
S("git", "Git", "method", ["GitLab", "GitHub"], family="devops", rarity=1, boolean_generic=True)
S("ci_cd", "CI/CD", "method", ["Jenkins", "GitLab CI", "GitHub Actions", "intégration continue"], family="devops", rarity=2)

# ---------- Front-end / Design System
S("react", "React", "tech", ["ReactJS", "React.js"], family="frontend", rarity=2, volatile=True,
  depth_terms=["hooks", "redux", "context", "performance", "ssr", "next.js", "tests"], related=["Vue", "Angular"])
S("typescript", "TypeScript", "tech", [], family="frontend", rarity=2, volatile=True)
S("storybook", "Storybook", "tech", [], family="frontend", rarity=4,
  depth_terms=["documentation", "composants", "addons", "chromatic", "visual regression", "accessibilite"],
  note="Connaître Storybook ≠ concevoir et déployer un Design System à grande échelle (§6.5)")
S("tailwind", "Tailwind CSS", "tech", ["Tailwind", "TailwindCSS"], family="frontend", rarity=3, volatile=True)
S("design_system", "Design System", "tech", ["Design Systems", "système de design", "design-system", "DS", "bibliothèque de composants"],
  family="frontend", rarity=5, case_sensitive=["DS"],
  context_needed=["composant", "component", "token", "storybook", "ui", "front", "design", "marque", "brand", "accessib"],
  note="Utiliser une bibliothèque de composants ne démontre pas la conception, la gouvernance et l'adoption à grande échelle d'un Design System",
  depth_terms=["composants reutilisables", "reusable components", "design tokens", "tokens", "variants", "variantes", "gouvernance",
               "governance", "contribution", "maintenance", "documentation", "storybook", "accessibilite", "a11y", "wcag", "rgaa",
               "packaging", "npm", "diffusion", "versioning", "semver", "multi-marques", "multi-produits", "multi-equipes",
               "adoption", "accompagnement", "evangelisation", "migration"],
  advanced_terms=["gouvernance", "governance", "adoption", "multi-equipes", "multi-marques", "multi-produits", "tokens",
                  "design tokens", "packaging", "diffusion", "versioning", "contribution", "accompagnement", "evangelisation"],
  confusable_with=["react"], narrowers=["Storybook", "design tokens", "component library"])
S("accessibility", "Accessibilité", "domain", ["WCAG", "RGAA", "a11y", "accessibility"], family="frontend", rarity=3,
  depth_terms=["audit", "aria", "lecteur d'ecran", "screen reader", "conformite", "niveau aa", "axe"])

# ---------- QA / test
S("postman", "Postman", "tech", ["Newman"], family="qa", rarity=3,
  depth_terms=["collections", "environnements", "scripts", "tests automatises", "newman", "assertions", "variables", "ci"])
S("xray", "Xray", "tech", ["Jira Xray", "Xray for Jira"], family="qa", rarity=4)
S("jira", "Jira", "method", [], family="tooling", rarity=1, boolean_generic=True)
S("api_management", "API Management", "tech", ["APIM", "API Gateway", "Apigee", "Azure API Management", "gestion des API", "Kong"],
  family="api", rarity=4, depth_terms=["politiques", "policies", "quotas", "oauth", "portail developpeur", "versioning", "swagger", "openapi", "throttling"])
S("test_design", "Rédaction de cas de test", "activity", ["cas de test", "plans de test", "conception de tests", "stratégie de test"],
  family="qa", rarity=2, depth_terms=["scenarios", "couverture", "jeux de donnees", "non-regression", "regression", "exigences", "tracabilite"])
S("recette", "Recette", "activity", ["recette fonctionnelle", "UAT", "tests d'acceptation", "VABF", "VSR"], family="qa", rarity=2,
  depth_terms=["anomalies", "campagne", "go/no go", "pv de recette", "cahier de recette", "defects", "bugs", "rapport"])
S("defect_mgmt", "Gestion des anomalies", "activity", ["gestion des défauts", "suivi des anomalies", "bug tracking"], family="qa", rarity=2,
  depth_terms=["priorisation", "criticite", "comite", "reporting", "triage", "cycle de vie"])

# ---------- Data
S("data_quality", "Data Quality", "activity", ["qualité des données", "qualité de données", "DQ", "Data Quality Management"],
  family="data", rarity=4, case_sensitive=["DQ"],
  depth_terms=["reconciliation", "rapprochement", "regles de qualite", "data quality rules", "kpi", "indicateurs", "ecarts", "anomalies",
               "fiabilisation", "controles de coherence", "profiling", "cleansing", "nettoyage", "dedoublonnage", "completude", "unicite"],
  advanced_terms=["reconciliation", "rapprochement", "regles de qualite", "data quality rules", "kpi", "ecarts", "fiabilisation", "plan de remediation"],
  confusable_with=["data_analysis"], narrowers=["réconciliation", "règles de qualité", "fiabilisation"],
  note="Un Data Analyst orienté reporting n'est pas un spécialiste de la qualité post-migration (§11)")
S("data_migration", "Data Migration", "activity", ["migration de données", "reprise de données", "migration des données"], family="data", rarity=4,
  depth_terms=["mapping", "extraction", "transformation", "chargement", "etl", "cutover", "bascule", "recette de migration", "volumetrie", "pays"])
S("reconciliation", "Réconciliation source/cible", "activity", ["réconciliation", "rapprochement source cible", "réconciliation de données", "data reconciliation"],
  label_is_term=False, family="data", rarity=5, depth_terms=["source", "cible", "ecarts", "controles", "requetes", "sql", "tableau de bord"])
S("mdm", "MDM", "domain", ["Master Data Management", "master data", "données de référence", "référentiels"], family="data", rarity=4,
  depth_terms=["golden record", "harmonisation", "gouvernance", "dedoublonnage", "data steward", "referentiel client"])
S("data_analysis", "Data Analyst", "activity", ["analyse de données", "data analysis", "tableaux de bord", "reporting BI"], family="data", rarity=1, confusable_with=["data_quality"])
S("powerbi", "Power BI", "tech", ["PowerBI"], family="data", rarity=2)
S("salesforce", "Salesforce", "product", ["SFDC"], family="crm", rarity=3, depth_terms=["migration", "apex", "objets", "flows", "mapping"])
S("siebel", "Siebel", "product", [], family="crm", rarity=4)
S("informatica", "Informatica", "product", ["Informatica PowerCenter", "IDQ"], family="data", rarity=4)
S("talend", "Talend", "product", [], family="data", rarity=3)

# ---------- SAP
S("sap_gts", "SAP GTS", "product", ["Global Trade Services", "SAP Global Trade Services", "GTS"], family="sap", rarity=5,
  context_needed=["sap", "douane", "customs", "trade", "compliance", "hana"], volatile=True,
  depth_terms=["compliance management", "customs management", "risk management", "sanctioned party", "licence", "licenses", "classification",
               "preferences", "declarations", "edi", "feeder system", "gts 11", "e4h"])
S("sap_gts_e4h", "SAP GTS E4H", "product", ["GTS E4H", "E4H", "GTS Edition for HANA", "SAP GTS Edition for HANA", "Edition for HANA"],
  family="sap", rarity=5, volatile=True,
  note="Une expertise SAP GTS générale ne démontre pas une pratique d'E4H (§12)")
S("sap_s4", "SAP S/4HANA", "product", ["S/4HANA", "S4HANA", "S/4", "SAP S4"], family="sap", rarity=3, volatile=True)
S("sap_amoa", "SAP AMOA", "activity", ["AMOA", "assistance à maîtrise d'ouvrage", "Business Analyst SAP", "consultant fonctionnel"], family="sap", rarity=3,
  depth_terms=["expression de besoin", "specifications fonctionnelles", "ateliers", "recette", "coordination", "integrateur"],
  confusable_with=["sap_config"])
S("sap_config", "Paramétrage SAP", "activity", ["customizing", "paramétrage", "SPRO", "configuration SAP", "customisation"], family="sap", rarity=4,
  depth_terms=["spro", "customizing", "parametrage", "configuration", "enhancement", "badi", "abap", "tests unitaires", "transport", "tables de configuration"],
  advanced_terms=["spro", "customizing", "badi", "abap", "enhancement", "transport"], confusable_with=["sap_amoa"],
  narrowers=["SPRO", "customizing"])
S("abap", "ABAP", "tech", [], family="sap", rarity=4)

# ---------- Réseau / infra / stockage
S("f5", "F5", "product", ["F5 BIG-IP", "BIG-IP", "BIG IP", "LTM", "iRules", "F5 LTM", "F5 GTM"], family="network", rarity=4, case_sensitive=["LTM"],
  depth_terms=["irules", "ltm", "gtm", "asm", "apm", "virtual server", "pool", "monitor", "persistance", "ssl offload", "certificats", "vip", "policies", "as3"],
  advanced_terms=["irules", "as3", "gtm", "asm", "apm", "persistance", "ssl offload", "policies"],
  narrowers=["iRules", "AS3", "GTM"])
S("netscaler", "Citrix NetScaler", "product", ["NetScaler", "Citrix ADC", "NetScaler ADC"], family="network", rarity=4,
  depth_terms=["policies", "gateway", "load balancing", "content switching", "rewrite", "responder", "vserver"])
S("load_balancing", "Load balancing", "tech", ["répartition de charge", "ADC", "load balancer", "équilibrage de charge"], family="network", rarity=3,
  case_sensitive=["ADC"])
S("network_migration", "Migration réseau", "activity", ["migration NetScaler", "migration de load balancers", "migration F5"], family="network", rarity=4, label_is_term=False,
  depth_terms=["audit", "inventaire", "mapping", "bascule", "rollback", "plan de migration", "recette", "tests", "documentation", "transfert de competences"])
S("huawei", "Huawei", "product", ["Huawei OceanStor", "OceanStor"], family="infra-stockage", rarity=4,
  depth_terms=["oceanstor", "dorado", "fusionstorage", "administre", "configure", "provisioning", "lun", "replication"],
  note="Travailler dans une entreprise utilisant Huawei ≠ expertise opérationnelle Huawei (§15.3)")
S("dell_emc", "Dell EMC", "product", ["DellEMC", "EMC", "Dell EMC PowerStore", "Dell EMC PowerMax", "Dell EMC Unity"], family="infra-stockage", rarity=4,
  case_sensitive=["EMC"], umbrella_of=["powerstore", "powermax", "unity"])
S("powerstore", "PowerStore", "product", ["Dell PowerStore"], family="infra-stockage", rarity=5)
S("powermax", "PowerMax", "product", ["Dell PowerMax", "VMAX"], family="infra-stockage", rarity=5)
S("unity", "Unity", "product", ["Dell Unity", "EMC Unity", "Unity XT"], family="infra-stockage", rarity=5,
  context_needed=["dell", "emc", "stockage", "storage", "baie", "san", "nas", "lun", "array"])
S("rubrik", "Rubrik", "product", [], family="infra-sauvegarde", rarity=5,
  depth_terms=["sauvegarde", "backup", "restauration", "politiques", "sla domain", "replication", "archivage", "cdm"])
S("san", "SAN", "tech", ["Storage Area Network", "stockage SAN", "réseau SAN", "fibre channel", "FC SAN"], family="infra-stockage", rarity=4, case_sensitive=["SAN"],
  depth_terms=["zoning", "lun", "multipath", "fibre channel", "switch", "brocade", "mds", "masking", "replication"])
S("vmware", "VMware", "product", ["vSphere", "ESXi", "vCenter"], family="infra", rarity=3)
S("veeam", "Veeam", "product", [], family="infra-sauvegarde", rarity=3)

# ---------- WMS / logistique / legacy
S("reflex_wms", "Reflex WMS", "product", ["Reflex", "Hardis Reflex", "Reflex Hardis", "WMS Reflex"], family="logistique", rarity=5,
  context_needed=["wms", "hardis", "entrepot", "logisti", "stock", "picking", "preparation", "expedition"],
  depth_terms=["parametrage", "flux", "inventaire", "picking", "reception", "expedition", "tma", "run", "incidents", "recette"])
S("wms", "WMS", "domain", ["Warehouse Management System", "gestion d'entrepôt"], family="logistique", rarity=3)
S("as400", "AS400", "tech", ["AS/400", "IBM i", "iSeries", "RPG"], family="legacy", rarity=4)
S("adelia", "Adelia", "tech", ["Adélia"], family="legacy", rarity=5)

# ---------- Cybersécurité
S("risk_mgmt", "Gestion des risques", "activity", ["analyse de risques", "EBIOS", "EBIOS RM", "risk management", "ISO 27005"], family="cyber", rarity=4,
  depth_terms=["registre des risques", "cartographie", "ebios", "plan de traitement", "risque residuel", "scenarios", "comite", "appetence", "mesures"],
  advanced_terms=["registre des risques", "plan de traitement", "risque residuel", "ebios", "comite", "mesures"])
S("tprm", "Gestion des tiers", "activity", ["third-party risk", "TPRM", "sécurité des tiers", "évaluation des fournisseurs", "gestion des fournisseurs"], family="cyber", rarity=5,
  depth_terms=["questionnaires", "evaluation", "audit fournisseur", "clauses", "contrats", "achats", "juridique", "plan d'action", "suivi"])
S("csirt", "CSIRT", "domain", ["CERT", "SOC", "gestion des incidents de sécurité"], family="cyber", rarity=4, case_sensitive=["SOC", "CERT"])
S("iso27001", "ISO 27001", "domain", ["ISO27001", "SMSI"], family="cyber", rarity=3)
S("security_by_design", "Security by design", "activity", ["sécurité des projets", "security review", "revue de sécurité", "sécurité by design"], family="cyber", rarity=4,
  depth_terms=["revue d'architecture", "exigences de securite", "pia", "analyse de risques", "go live", "comite", "validation securite"])

# ---------- Domaines métier
S("retail_ecom", "Retail / e-commerce", "domain",
  ["e-commerce", "ecommerce", "retail", "omnicanal", "omnichannel", "parcours d'achat", "OMS", "Order Management", "click and collect", "grande distribution"],
  label_is_term=False, family="metier", rarity=3, case_sensitive=["OMS"],
  depth_terms=["commandes", "paiement", "stock", "catalogue", "panier", "magasins", "sites web", "back-office", "back office", "promotions", "livraison", "retours"],
  note="Un environnement e-commerce ≠ avoir développé une plateforme e-commerce (§6.5)")
S("high_volume", "Forte volumétrie", "domain", ["haute volumétrie", "volumétrie importante", "fortes volumétries", "high volume", "forte charge"],
  family="metier", rarity=3,
  depth_terms=["transactions", "tps", "millions", "par jour", "par seconde", "par heure", "pic de charge", "montee en charge", "performance", "throughput", "debit"],
  note="La volumétrie d'une technologie ne se déduit pas de celle d'un autre projet du même CV (§8)")
S("banking_payments", "Paiements bancaires", "domain", ["plateforme de paiements", "paiements", "monétique", "SEPA", "virements"], family="metier", rarity=3,
  depth_terms=["transactions", "volumetrie", "haute disponibilite", "incidents", "reglementaire"])

# ---------- Activités transverses (preuves de responsabilité réelle)
S("run_n3", "RUN / Support N3", "activity",
  ["RUN", "support N3", "N3", "support de niveau 3", "MCO", "maintien en conditions opérationnelles", "TMA", "support production", "support applicatif"],
  label_is_term=False, family="run", rarity=3, case_sensitive=["RUN", "N3", "MCO", "TMA"],
  depth_terms=["incident", "incidents", "gestion des incidents", "astreinte", "astreintes", "analyse des causes", "root cause", "rca", "post-mortem",
               "postmortem", "gestion des problemes", "supervision", "monitoring", "sla", "ticket", "tickets", "hotfix", "correctif", "runbook",
               "itil", "diagnostic", "resolution", "support production", "support applicatif", "escalade"],
  advanced_terms=["astreinte", "astreintes", "analyse des causes", "root cause", "rca", "post-mortem", "gestion des problemes", "supervision", "escalade", "sla"],
  narrowers=["astreinte", "post-mortem", "gestion des incidents"],
  note="Développer une application déployée en production ≠ expérience RUN (§15.2)")
S("architecture", "Architecture", "activity",
  ["architecte", "architecture applicative", "architecture technique", "architecture SI", "Design Authority", "architecture cible"],
  family="archi", rarity=4,
  depth_terms=["design authority", "dossier d'architecture", "architecture cible", "choix d'architecture", "decision", "decisionnaire", "adr", "flux",
               "integrations", "cartographie", "urbanisation", "structurant", "trajectoire", "poc"],
  advanced_terms=["design authority", "dossier d'architecture", "decisionnaire", "architecture cible", "adr", "structurant", "trajectoire"],
  note="Rédiger des spécifications fonctionnelles ≠ responsabilité d'architecture (§15.1)")
S("tech_mgmt", "Encadrement technique", "activity",
  ["tech lead", "lead technique", "lead développeur", "encadrement technique", "encadrement d'équipe", "management technique", "chef d'équipe technique", "mentorat"],
  family="management", rarity=3,
  depth_terms=["encadre", "encadrement", "equipe de", "developpeurs", "revues de code", "code review", "mentorat", "coaching", "onboarding", "arbitrage", "planification", "standards"],
  advanced_terms=["revues de code", "code review", "arbitrage", "planification", "standards", "mentorat"])
S("migration", "Migration / transformation", "activity", ["migration", "transformation", "modernisation", "replatforming"], family="projet", rarity=2, label_is_term=False,
  depth_terms=["cutover", "bascule", "rollback", "plan de migration", "inventaire", "mapping", "recette", "deploiement", "go live", "perimetre"])
S("agile", "Agile", "method", ["Scrum", "Kanban", "SAFe"], family="methodes", rarity=1, boolean_generic=True)
S("itil", "ITIL", "method", [], family="methodes", rarity=2)
S("english", "Anglais", "soft", ["English", "anglais professionnel", "fluent English", "anglais courant", "anglais fluide"], family="langues", rarity=1)

# ============================================================ FAMILLES DE RÔLES
R("tech_lead_java", "Tech Lead Java",
  ["Tech Lead", "Lead Developer", "Technical Lead", "Lead Dev", "Lead Développeur"],
  wide_titles=["Lead Technique", "Senior Java Developer", "Développeur Java Senior", "Software Engineer Lead", "Principal Engineer"],
  discriminants=["spring", "tech_mgmt"], triggers=["tech lead java", "lead developer java", "lead java"],
  neighbors=[("Développeur Java", "Pas d'encadrement ni de responsabilité technique d'équipe"),
             ("Architecte Java", "Décide et conçoit l'architecture cible plutôt que de livrer en hands-on")])
R("dev_java", "Développeur Java", ["Développeur Java", "Java Developer", "Ingénieur Java", "Java Engineer", "Développeur Back-End Java"],
  wide_titles=["Ingénieur Développement Java", "Software Engineer"], discriminants=["spring"], triggers=["developpeur java", "dev java"])
R("dev_front", "Développeur Front-End", ["Développeur Front-End", "Front-End Developer", "Frontend Developer", "Développeur Frontend", "Ingénieur Front"],
  wide_titles=["Développeur React", "UI Engineer", "Développeur JavaScript", "Front End Engineer"], discriminants=["design_system", "react"],
  triggers=["front-end", "frontend", "front end", "developpeur react"],
  neighbors=[("Développeur Design System", "Conçoit et gouverne un système de composants plutôt que de consommer une bibliothèque")])
R("architecte_si", "Architecte SI", ["Architecte SI", "Architecte Solutions", "Solution Architect", "Architecte Applicatif", "Enterprise Architect", "Architecte Technique"],
  wide_titles=["Architecte Logiciel", "Software Architect", "Architecte d'Entreprise", "Architecte Fonctionnel"], discriminants=["architecture"],
  triggers=["architecte"], neighbors=[("Consultant WMS/OMS", "Expertise produit ≠ responsabilité d'architecture SI")])
R("data_quality", "Data Quality Analyst", ["Data Quality Analyst", "Data Quality Engineer", "Data Quality Manager", "Analyste Qualité des Données", "Data Steward"],
  wide_titles=["Data Analyst", "Data Governance Analyst", "Consultant Data Quality", "Data Migration Analyst", "MDM Analyst"],
  discriminants=["data_quality", "reconciliation", "data_migration"], triggers=["data quality", "qualite des donnees", "qualité des données"],
  neighbors=[("Data Analyst reporting", "Produit des tableaux de bord mais ne réconcilie ni ne fiabilise après migration"),
             ("Data Engineer", "Construit des pipelines plutôt que des règles de qualité")])
R("data_analyst", "Data Analyst", ["Data Analyst", "Analyste de données", "Business Data Analyst", "Analyste Data"],
  wide_titles=["BI Analyst", "Analyste BI", "Data Consultant"], discriminants=["sql", "powerbi"], triggers=["data analyst"])
R("sap_gts", "Consultant SAP GTS", ["Consultant SAP", "SAP Consultant", "Consultant SAP GTS", "SAP GTS Consultant", "Expert SAP GTS"],
  wide_titles=["Consultant SAP Douane", "SAP Trade Compliance Consultant", "SAP GTS Functional Consultant", "Consultant SAP SD"],
  discriminants=["sap_gts", "sap_gts_e4h", "sap_config"], triggers=["sap gts", "global trade services"],
  neighbors=[("Consultant SAP AMOA", "Exprime le besoin et coordonne plutôt que configurer")])
R("sap_consultant", "Consultant SAP", ["Consultant SAP", "SAP Consultant", "Consultant Fonctionnel SAP", "SAP Functional Consultant"],
  wide_titles=["Consultant SAP Senior", "Expert SAP", "Business Analyst SAP"], discriminants=["sap_config"], triggers=["sap"])
R("qa_test", "QA / Analyste de test", ["Analyste de Test", "QA Analyst", "Testeur", "Ingénieur QA", "Test Analyst", "Analyste Test et Validation", "QA Engineer"],
  wide_titles=["Ingénieur de Test", "Testeur Fonctionnel", "Consultant Test", "Test Manager", "Responsable Recette"],
  discriminants=["postman", "xray", "api_management", "test_design"], triggers=["analyste de test", "qa", "testeur", "test et validation", "recette"])
R("expert_f5", "Expert F5 / Load balancing", ["Ingénieur Réseau", "Network Engineer", "Expert F5", "Ingénieur F5", "F5 Engineer", "Consultant F5"],
  wide_titles=["Ingénieur Load Balancing", "Ingénieur ADC", "Architecte Réseau", "Consultant Réseau"], discriminants=["f5", "netscaler", "load_balancing"],
  triggers=["f5", "netscaler"], neighbors=[("Administrateur réseau généraliste", "Connaît F5 de nom mais n'a pas piloté de migration NetScaler → F5")])
R("infra_stockage", "Ingénieur Infrastructure / Stockage", ["Ingénieur Stockage", "Storage Engineer", "Ingénieur Infrastructure Stockage", "Administrateur Stockage", "Storage Administrator"],
  wide_titles=["Ingénieur Infrastructure", "Ingénieur Systèmes", "Ingénieur Sauvegarde", "Architecte Infrastructure"],
  discriminants=["san", "dell_emc", "huawei", "rubrik"], triggers=["stockage", "infrastructure", "storage"])
R("cyber", "Consultant Cybersécurité", ["Consultant Cybersécurité", "Cybersecurity Consultant", "Consultant Sécurité", "Security Officer", "Chef de Projet Sécurité", "Security Project Manager"],
  wide_titles=["Analyste GRC", "Responsable Sécurité", "RSSI Adjoint", "Security Analyst"], discriminants=["risk_mgmt", "tprm", "security_by_design"], triggers=["cybersecurite", "cybersécurité", "securite", "sécurité"])
R("wms_consultant", "Consultant WMS / Logistique", ["Consultant WMS", "WMS Consultant", "Expert WMS", "Consultant Reflex", "Analyste Applicatif WMS"],
  wide_titles=["Consultant Supply Chain", "Chef de Projet WMS", "Analyste Applicatif Logistique"], discriminants=["reflex_wms", "run_n3"], triggers=["wms", "reflex"])
R("chef_projet", "Chef de projet IT", ["Chef de Projet", "Project Manager", "Chef de Projet IT", "IT Project Manager", "Responsable de Projet"],
  wide_titles=["Project Lead", "Délivery Manager", "PMO"], discriminants=["migration", "agile"], triggers=["chef de projet", "project manager"])
R("devops", "DevOps / SRE", ["Ingénieur DevOps", "DevOps Engineer", "SRE", "Site Reliability Engineer", "Ingénieur Cloud"],
  wide_titles=["Platform Engineer", "Ingénieur Infrastructure Cloud", "Cloud Engineer"], discriminants=["kubernetes", "terraform", "ci_cd"], triggers=["devops", "sre"])
