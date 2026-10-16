# Intégrations : ce qui est fait, ce qui est proposé, ce qui n'est pas vérifié

Principe : Talent Lab **ne reconstruit pas** ce qui existe déjà chez Comet. Il apporte le raisonnement (grille, preuves, booléens, qualification) ; les outils existants gardent leur rôle.

| Système | Statut réel dans cette version | Ce qui reste à faire |
|---|---|---|
| Identité / SSO Comet | **Contrat implémenté, testé contre une passerelle simulée** (en-tête d'identité + secret partagé) ; **jamais testé contre le vrai SSO** | Obtenir de Tech l'en-tête réel et le mode de transmission du secret ; provisionner les 6 utilisateurs |
| `comet-ai-office` | **Non branché.** Audité (voir AUDIT_COMET.md) | Décision Tech : remplacer son scoring/booléens par des appels à l'API Talent Lab |
| DT Editor | **Introuvable** dans les dépôts accessibles ; export JSON prêt (`comet.talentlab.dt-export/v1`), **non consommé par un vrai DT Editor** | Valider le format avec les concepteurs de DT Editor |
| Turnover | **Aucune connexion.** Profil de plateforme 250 caractères, `syntax_confirmed=False` | Tester les requêtes générées sur Turnover avec un recruteur ; confirmer opérateurs, guillemets, jokers, limite réelle |
| Boond (besoins) | **Branché en lecture seule, désactivé par défaut.** Adaptateur BigQuery écrit et testé avec un exécuteur simulé ; **jamais exécuté par l'application contre l'entrepôt réel** (aucun identifiant applicatif dans l'environnement de développement) ; adaptateur « fichier » pour le développement. Rien n'est jamais écrit dans Boond | Compte de service applicatif en lecture sur 3 tables, essai sur la copie de test, validation par la sécurité et le DPO avant tout usage réel (voir §5) |
| Granola / Noota (transcriptions) | **Non connectés.** Le texte collé est accepté (`transcription`, marqué « à vérifier ») | Importer automatiquement ; garder le plafond « partiel » tant que non validé |
| Constellation (identité visuelle) | Conventions de couleurs/typographie reprises à la main ; **aucune dépendance** | Remplacer par la bibliothèque commune si Tech le souhaite |
| Fournisseur d'IA | Interface + fournisseur Anthropic **écrits, jamais appelés en réel** ; désactivé par défaut | Autorisation de coût et de traitement de données ; contrat de sous-traitance ; essai sur données fictives |

## 1. Authentification par passerelle (production)

```
TALENTLAB_ENV=prod
TALENTLAB_AUTH_MODE=gateway
TALENTLAB_GATEWAY_EMAIL_HEADER=X-Comet-User-Email      # nom d'en-tête à confirmer avec Tech
TALENTLAB_GATEWAY_SECRET_HEADER=X-Gateway-Secret
TALENTLAB_GATEWAY_SECRET=…        # injecté par le gestionnaire de secrets, jamais dans Git
TALENTLAB_SESSION_SECRET=…
TALENTLAB_ENCRYPTION_KEY=…        # clé Fernet ; sa perte rend les données chiffrées illisibles
TALENTLAB_DATABASE_URL=postgresql+psycopg://…
```

Règles appliquées : **un en-tête d'identité ou de secret présent plusieurs fois est refusé (401)** — la passerelle doit néanmoins supprimer tout en-tête d'identité fourni par le client avant d'ajouter le sien ; un en-tête d'identité n'est jamais cru seul — le secret est comparé en temps constant ; l'utilisateur doit exister et être actif (`POST /api/admin/users` pour le provisionner) ; la production refuse de démarrer si une de ces conditions manque (`Settings._guard`). L'application doit être **inaccessible hors de la passerelle** (réseau privé / liste d'accès) : sinon le secret partagé est la seule protection.

## 2. Contrat d'export `comet.talentlab.dt-export/v1`

`GET /api/missions/{id}/candidates/{cid}/export/dt` (droit « lecture » sur la mission ; **acronyme autorisé du consultant obligatoire**, nom et prénom jamais exportés) renvoie :

```jsonc
{
  "schema": "comet.talentlab.dt-export/v1",
  "generated_at": "…",
  "mission": { "title", "context", "objectives", "modalities", "requirements": [{label, category}] },
  "candidate": { "acronym", "reference" },
  "dossier_material": {
    "experiences": [{ "company","title","start","end","is_current","dates_known","months","relevant",
                      "context","realisations":[{statement, evidences_criteria, source:"CV", verbatim:true}],
                      "environnement_technique","environnement_technique_phrase" }],
    "demonstrated_skills": [{ "skill","level","last_used","evidence":[{excerpt,company,period}] }],
    "responsibilities": [...], "deliverables": [...],
    "validated_call_information": [{ "statement","topic","kind","source","date","evidence_id" }],
    "note": "…"
  },
  "internal_only": { "assessment", "to_verify", "unvalidated_call_information", "constraints", "date_flags", "provenance_note" },
  "markdown": "…"
}
```

Garanties vérifiées par test (`test_acceptance_export_for_dt_editor_is_anonymised_faithful_and_sourced`) : coordonnées directes retirées (email, téléphone, liens) ; réalisations reprises **à l'identique** du CV ; aucune technologie ajoutée ; aucune compétence déplacée d'une expérience à une autre ; informations d'appel seulement si **validées** ; provenance et éléments « à vérifier » dans `internal_only`, à ne pas transmettre au client. `scrub` est une défense en profondeur : il n'est pas garanti qu'un CV ne contienne jamais une autre donnée directement identifiante (adresse, employeur très reconnaissable) ; relecture humaine obligatoire avant tout envoi.

`GET /api/missions/{id}/candidate-description` produit le descriptif de mission pour un échange candidat : « Non communiqué » pour toute condition absente, client masqué par défaut. **Aucun email n'est envoyé.**

## 3. Brancher Talent Lab sur `comet-ai-office` (proposition)

1. `analyzeCandidate` / `buildBooleanStrategies` appellent `POST /api/missions/{id}/searches/generate` et `…/candidates/{cid}` de Talent Lab (service à service, secret partagé).
2. Les missions peuvent être créées côté Talent Lab à partir d'un besoin Boond **en lecture seule** (voir §5) ; l'identifiant Boond est stocké comme référence externe (`missions.external_source`, `external_id`). Une écriture dans Boond n'est pas implémentée.
3. Les recherches Turn restent exécutées par `comet-ai-office` ; Talent Lab ne reçoit que le retour (volume, pertinence).
4. Prérequis : recette sur données autorisées, décision de Tech sur la propriété du code, adaptateur Python dans la CI de sécurité (voir AUDIT_COMET.md §5).

## 4. Points techniques à traiter avant une exploitation multi-utilisateurs

- **File de tâches durable** pour l'analyse des CV (aujourd'hui : pool de threads du processus + reprise au redémarrage).
- **Stockage des fichiers** : les CV sont stockés chiffrés en base ; un coffre objet chiffré serait préférable au-delà de quelques centaines de documents.
- **Sauvegardes** de la base *et* de la clé de chiffrement, séparément.
- **Migrations** : `python -m talentlab.manage migrate` (Alembic, baseline `0001` = les 19 tables) ; en production l'application **refuse de démarrer** si le schéma n'est pas à la dernière révision. Les migrations sont testées sur SQLite et sur PostgreSQL 16 ; toute évolution du modèle exige une nouvelle révision.
- **Maintenance planifiée** (désactivée par défaut) : `TALENTLAB_MAINTENANCE_INTERVAL_HOURS` > 0 avec `TALENTLAB_RETENTION_AUTO_PURGE` et/ou `TALENTLAB_AUDIT_HEAD_PATH`. La purge est **irréversible** et ne doit être activée qu'après validation du DPO ; le fichier de tête d'audit doit être répliqué **hors de la base**. Les mêmes opérations sont disponibles à la main (écran Administration, ligne de commande `purge`, `audit-head`).
- **Limitation de débit** par utilisateur (600 requêtes/min, 20/min pour les opérations lourdes) : garde-fou applicatif en plus de celui de la passerelle ; état en mémoire du processus (non partagé entre instances).
- **Journalisation** applicative vers la pile d'observabilité Comet, sans contenu de CV (déjà garanti pour le journal d'audit ; à vérifier pour les journaux du serveur HTTP).
- **Empaquetage** (image conteneur, CI, scan de dépendances Python) : **non réalisé et non vérifié** dans cette version.

## 5. Boond — lecture seule des besoins

**Ce qui est lu** (liste fermée, vérifiée par test) : table des besoins (`TAB_AO` : référence, intitulé, description, critères, lieu, dates, état, visibilité), nom de la société cliente (`TAB_CRMSOCIETE`), libellé d'état (`ref_etat_besoin`). **Ce qui n'est jamais lu** : contacts, responsables, budgets, chiffres d'affaires, tarifs, CV, profils, candidats, positionnements, données bancaires ou contractuelles. **Rien n'est écrit dans Boond** : l'adaptateur n'émet que des `SELECT` (refus de tout mot d'écriture), le texte saisi par l'utilisateur est lié en paramètre, le coût de chaque requête est plafonné (`TALENTLAB_BOOND_BQ_MAX_BYTES`).

**Configuration** (aucune valeur réelle dans le dépôt, qui est public) :

```
TALENTLAB_BOOND_SOURCE=bigquery            # none (défaut) | file (développement/tests, refusé en production) | bigquery
TALENTLAB_BOOND_BQ_PROJECT=<projet de la copie Boond>
TALENTLAB_BOOND_BQ_DATASET=<dataset de la copie Boond>
```

**Identifiants** : l'application utilise le compte de service de **son** environnement d'exécution, avec le rôle de lecture sur ces trois tables et le droit d'exécuter des requêtes. Les connexions mises à disposition de l'assistant de développement ne sont **pas** des identifiants applicatifs et ne doivent pas être réutilisées.

**Comportement** : l'écran « Nouvelle mission » propose de rechercher un besoin et de l'importer. La mission créée est **privée** à son importateur ; la référence Boond sert de clé de déduplication par utilisateur (contrainte d'unicité en base : deux imports simultanés ne créent qu'une mission). Un besoin inchangé n'est pas réimporté ; un besoin modifié dans Boond ajoute une **nouvelle source** (les sources sont immuables, l'historique est conservé) et **ne modifie jamais une grille figée**. Un besoin **sans descriptif exploitable** (moins de 40 caractères de description et de critères) est **refusé** plutôt que complété : rien n'est inventé. Les « critères » saisis dans Boond sont rangés sous « Profil recherché » et ne sont jamais promus d'office en impératifs.

**Lancer l'application sur un instantané de développement** (source `file`, jamais en production). L'instantané est un fichier JSON **hors Git** (le dossier `data/` est ignoré) :

```json
{"schema": "comet.talentlab.boond-needs-snapshot/v1", "exported_at": "AAAA-MM-JJ",
 "needs": [{"external_id": "…", "reference": "…", "title": "…", "company": "…", "state": "…", "updated_at": "…", "created_at": "…",
            "description": "…", "criteria": "…", "place": "…", "start": "…", "end": "…"}]}
```

```bash
TALENTLAB_BOOND_SOURCE=file TALENTLAB_BOOND_SNAPSHOT_PATH=./data/boond-needs-snapshot.json \
TALENTLAB_DATABASE_URL=sqlite:///./data/talentlab-demo.db uvicorn talentlab.main:app --port 8765
```

Tout champ non prévu (contact, budget, responsable…) fait refuser le fichier. Utiliser une base de développement neuve : en développement les tables sont créées sans migration.

**Vérifié par test** (`tests/test_boond_link.py`, navigateur) : désactivé par défaut ; liste, recherche, pagination ; import, déduplication, révision ; confidentialité entre utilisateurs ; liste fermée de tables et de colonnes ; paramètres liés ; refus des écritures ; erreurs d'infrastructure génériques (502 sans détail) ; configuration fermée (source `file` refusée en production) ; absence d'identifiant réel dans le dépôt.

**Non vérifié** : exécution de l'adaptateur BigQuery contre l'entrepôt réel par l'application ; droits exacts du compte de service ; volumétrie et coût réels ; comportement avec des descriptions longues ou riches en HTML réel ; fraîcheur de la copie (rechargée par un processus tiers, sans garantie documentée). Le champ « société » est repris tel quel de Boond.
