# Intégrations : ce qui est fait, ce qui est proposé, ce qui n'est pas vérifié

Principe : Talent Lab **ne reconstruit pas** ce qui existe déjà chez Comet. Il apporte le raisonnement (grille, preuves, booléens, qualification) ; les outils existants gardent leur rôle.

| Système | Statut réel dans cette version | Ce qui reste à faire |
|---|---|---|
| Identité / SSO Comet | **Contrat implémenté, testé contre une passerelle simulée** (en-tête d'identité + secret partagé) ; **jamais testé contre le vrai SSO** | Obtenir de Tech l'en-tête réel et le mode de transmission du secret ; provisionner les 6 utilisateurs |
| `comet-ai-office` | **Non branché.** Audité (voir AUDIT_COMET.md) | Décision Tech : remplacer son scoring/booléens par des appels à l'API Talent Lab |
| DT Editor | **Introuvable** dans les dépôts accessibles ; export JSON prêt (`comet.talentlab.dt-export/v1`), **non consommé par un vrai DT Editor** | Valider le format avec les concepteurs de DT Editor |
| Turnover | **Aucune connexion.** Profil de plateforme 250 caractères, `syntax_confirmed=False` | Tester les requêtes générées sur Turnover avec un recruteur ; confirmer opérateurs, guillemets, jokers, limite réelle |
| Boond / BigQuery | **Non utilisé** (aucun projet BigQuery configuré dans la session, Boond sandbox non connecté) | Définir si la mission est lue depuis Boond ; aujourd'hui le brief est collé ou téléversé |
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

Règles appliquées : un en-tête d'identité n'est jamais cru seul — le secret est comparé en temps constant ; l'utilisateur doit exister et être actif (`POST /api/admin/users` pour le provisionner) ; la production refuse de démarrer si une de ces conditions manque (`Settings._guard`). L'application doit être **inaccessible hors de la passerelle** (réseau privé / liste d'accès) : sinon le secret partagé est la seule protection.

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
2. Les missions sont créées côté Talent Lab à partir du brief Boond ; l'identifiant Boond est stocké comme référence externe (champ à ajouter, absent aujourd'hui).
3. Les recherches Turn restent exécutées par `comet-ai-office` ; Talent Lab ne reçoit que le retour (volume, pertinence).
4. Prérequis : recette sur données autorisées, décision de Tech sur la propriété du code, adaptateur Python dans la CI de sécurité (voir AUDIT_COMET.md §5).

## 4. Points techniques à traiter avant une exploitation multi-utilisateurs

- **File de tâches durable** pour l'analyse des CV (aujourd'hui : pool de threads du processus + reprise au redémarrage).
- **Stockage des fichiers** : les CV sont stockés chiffrés en base ; un coffre objet chiffré serait préférable au-delà de quelques centaines de documents.
- **Sauvegardes** de la base *et* de la clé de chiffrement, séparément.
- **Journalisation** applicative vers la pile d'observabilité Comet, sans contenu de CV (déjà garanti pour le journal d'audit ; à vérifier pour les journaux du serveur HTTP).
- **Empaquetage** (image conteneur, CI, scan de dépendances Python) : **non réalisé et non vérifié** dans cette version.
