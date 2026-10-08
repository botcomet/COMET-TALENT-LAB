# Sécurité, RGPD et conformité

> **Cette version n'a pas été validée par la sécurité, le DPO ni le juridique de Comet, et n'est pas autorisée à traiter des données de candidats réels.** Ce document décrit ce qui est en place, **comment ce qui est affirmé a été vérifié**, et ce qui reste ouvert. Rien ici ne vaut avis juridique.

## 1. Contrôles en place (chacun relié à un test ou à une vérification faite)

| Domaine | Contrôle | Vérification |
|---|---|---|
| Authentification | Mode `dev` (comptes fictifs sans mot de passe) **refusé en production** par la configuration ; en production : en-tête d'identité + secret partagé comparé en temps constant (`hmac.compare_digest`) + utilisateur pré-provisionné et actif | `test_gateway_mode_requires_shared_secret_and_provisioned_user`, `test_production_config_refuses_insecure_settings` |
| Session | Cookie signé, `HttpOnly`, `SameSite=Lax`, `Secure` en production, durée 10 h ; un cookie forgé n'ouvre aucune session | `test_session_cookie_flags_forged_cookie_and_secure_flag_in_production` |
| CSRF | Toute requête modifiante exige l'en-tête `X-Requested-With: talentlab` | `test_api_requires_authentication_and_csrf_header` |
| Autorisations | Missions **privées par défaut** ; partage `stratégie` / `lecture` / `édition` ; mission non accessible → **404** (son existence n'est pas révélée) ; la stratégie de sourcing peut être partagée **sans** exposer CV, notes ni évaluations | `test_business_15_sharing_a_search_strategy_never_exposes_candidate_data`, `test_unreachable_mission_is_404_not_403` + parcours navigateur à deux utilisateurs |
| Validation d'entrée | Schémas pydantic stricts (`extra=forbid`, bornes de longueur) ; noms de fichier assainis ; limites de lot, de taille, de pages | `test_unknown_fields_are_rejected`, `test_batch_limit_is_enforced_and_configurable` |
| Fichiers | Extraction sans exécution de contenu actif ; PDF corrompus, chiffrés, scannés, illisibles → échec explicite sans arrêter le lot | `test_business_14_…` (7 cas) |
| Injection de prompt | Le scoring est déterministe et n'« obéit » pas au document ; consignes détectées → alerte au recruteur ; couche IA : document enveloppé comme donnée non fiable | `test_prompt_injection_inside_a_cv_is_wrapped_as_untrusted_data` + tests de scoring |
| Réponses du navigateur | CSP `default-src 'self'` (aucun script/style en ligne, aucune ressource externe), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` sur l'API ; contenu dynamique inséré en **nœuds texte** | `test_security_headers_present` ; `test_html_injection_in_cv_and_notes_is_rendered_as_text` (navigateur réel) |
| Chiffrement | Texte et fichier de CV, extraits de preuves, évaluations, notes d'appel, sources de mission, cas de non-régression : chiffrés par l'application (Fernet) **avant** l'écriture en base | vérifié sur SQLite et sur **PostgreSQL 16** : requêtes SQL directes → 0 occurrence de texte en clair |
| Journal d'audit | Chaîné par hachage (toute altération est détectable via `GET /api/admin/audit/verify`) ; **ne contient jamais de contenu de CV ni de note** (seulement identifiants et métadonnées) | `test_audit_log_is_hash_chained_and_tamper_evident`, `test_candidate_data_is_encrypted_at_rest_and_audit_has_no_cv_content` |
| Minimisation à l'export | Coordonnées directes retirées des exports DT ; acronyme autorisé obligatoire, nom/prénom jamais exportés | `test_acceptance_export_for_dt_editor_is_anonymised_faithful_and_sourced` |
| Conservation | Date d'expiration posée à l'import ; purge **manuelle** par un administrateur (`POST /api/admin/purge`) qui supprime documents, candidats, évaluations, preuves et notes, **et efface les extraits de CV des cas de non-régression** | `test_retention_purge_*` |
| Bibliothèque partagée | Refuse email, téléphone, lien (la bibliothèque est lue par toute l'équipe) | `test_library_refuses_contact_details_…` |
| Robustesse | Texte extrait borné ; espaces Unicode repliées ; motifs sans retour arrière quadratique ; budget de temps par document (échec explicite, les autres CV ne sont pas bloqués) ; profondeur de requête booléenne bornée | `test_hostile_input_stays_within_a_time_budget`, `test_long_runs_of_unicode_whitespace_…`, `test_a_document_that_exhausts_its_time_budget_fails_explicitly…`, `test_deeply_nested_parentheses_…` |
| Intégrité | Confirmation d'une proposition : réservation atomique (une seule application sous requêtes simultanées) ; « éliminatoire » : extrait retrouvé mot pour mot dans une source enregistrée ; seuils/plafonds/facteurs bornés ; bibliothèque : retrait et généralisation réservés à l'auteur, au pilote, à l'admin | `test_simultaneous_confirmations_…`, `test_eliminatory_needs_a_quote_…`, `test_grid_parameters_are_bounded_…`, `test_only_the_author_or_a_lead_…` |
| Effacement | Supprimer un candidat supprime aussi ses documents (fichier et texte chiffrés) ; les extraits de CV des cas de non-régression sont effacés ; un administrateur sans accès à la mission ne voit pas ces extraits | `test_deleting_a_candidate_erases_its_documents_…`, `test_an_admin_without_access_…` |
| Aucune décision automatisée | Aucun rejet automatique ; écarter un candidat = acte humain motivé, réversible, tracé | `test_candidate_status_discard_requires_a_human_reason` |

## 2. Défauts trouvés et corrigés pendant la construction (transparence)

- Les « cas de non-régression » issus des corrections étaient décrits comme *anonymisés* alors qu'ils contenaient des extraits de CV non nettoyés, qui survivaient à la suppression du candidat. **Corrigé** : coordonnées retirées, note rectifiée (« dépersonnalisé »), extraits effacés avec le candidat ou à la purge ; trois tests de régression ajoutés, validés par mutation (ils échouent sans le correctif).
- Les commentaires de correction étaient stockés en clair. **Corrigé** : chiffrés et nettoyés.

## 3. Ce qui reste ouvert — à traiter avant tout usage réel

### 3.1 Décisions qui ne sont pas les miennes
- **Base légale et information des personnes** (intérêt légitime vs consentement ; information des candidats sur l'analyse automatisée de leur CV).
- **Durée de conservation.** `180 jours` est une valeur de départ **non validée** par le DPO. La purge n'est pas planifiée automatiquement (déclenchement manuel).
- **Registre de traitement et analyse d'impact (AIPD)** : à produire par le DPO ; le traitement évalue des personnes à grande échelle potentielle.
- **Classification au titre du règlement européen sur l'IA** : les systèmes d'IA utilisés pour le recrutement ou la sélection de personnes sont en principe classés « à haut risque ». Talent Lab limite volontairement l'automatisation (scoring déterministe et explicable, supervision humaine obligatoire, aucun rejet automatique, journalisation), mais **la qualification juridique et les obligations associées (documentation technique, supervision, information) doivent être établies avec le juridique/DPO**. Je ne les déclare pas remplies.
- **Droit d'accès, d'effacement, d'opposition** : la suppression d'un candidat existe (`DELETE …/candidates/{cid}`), mais **la procédure de traitement d'une demande de personne** (qui, délai, preuves) n'est pas définie.
- **Sous-traitance IA** : si le fournisseur d'IA optionnel est activé, contrat de sous-traitance, localisation des données, absence d'entraînement sur les données : à vérifier. **Désactivé par défaut.**
- **Candidats d'anciens CV (« vivier »)** : hors périmètre de cette version.

### 3.2 Limites techniques connues
- **Gestion de clé** : `TALENTLAB_ENCRYPTION_KEY` unique, sans rotation automatisée ; sa perte rend les données illisibles ; elle ne doit être stockée ni dans Git ni avec les sauvegardes de la base. Pas de HSM/KMS.
- **Détection des données nominatives** : le nettoyage retire email, téléphone et liens ; il **ne détecte ni les noms, ni les adresses, ni les employeurs**. Les exports exigent une relecture humaine.
- **Bibliothèque collective** : texte libre ; seul un contrôle des coordonnées directes est automatisé ; la validation humaine avant publication reste la protection principale.
- **Journal du serveur HTTP** : non audité pour l'absence de contenu de CV dans les journaux d'infrastructure (le journal d'audit applicatif, lui, est vérifié).
- **Limitation de débit / verrouillage** : pas de limitation de débit applicative (à placer sur la passerelle).
- **Chaîne de hachage de l'audit** : l'écriture sérialise sur la dernière ligne ; la première insertion concurrente sur une base vide n'est pas protégée par un verrou.
- **Défense en profondeur réseau** : l'application ne doit être joignable que par la passerelle ; non vérifié (aucun déploiement).
- **Analyse de dépendances / SAST** (Gitleaks, Semgrep, audit des paquets Python) : **non exécutés**, l'adaptateur Python de la CI Comet n'existe pas encore (voir AUDIT_COMET.md).
- **Revue externe** : deux revues adverses indépendantes (sécurité, méthodologie) ont été menées pendant la construction ; leurs constats sont résumés dans RECETTE.md. Elles ne remplacent ni un test d'intrusion ni la revue de la sécurité Comet.

## 4. Règles de conduite pour les premiers essais

1. Données **fictives ou explicitement autorisées** uniquement ; aucun CV réel dans un environnement non validé.
2. Dépôt public : ne jamais y committer de donnée, de nom de client ni de secret.
3. Aucun déploiement sur l'infrastructure Comet sans validation de Tech et de la sécurité.
4. Toute évaluation produite par l'outil est une **aide à la décision** : le recruteur relit l'extrait cité avant de s'y fier.
