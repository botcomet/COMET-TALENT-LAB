# Recette : état exact au 8 octobre 2026

Ce document répond à la consigne « si tu ne peux pas tout réaliser, donne l'état exact : code réalisé, tests exécutés, travail restant ». Tout ce qui est marqué **testé** a été exécuté dans l'environnement de développement avec des données **fictives**. Ce qui n'a pas été exécuté est dit.

## 1. Résultat des tests

| Suite | Fichier | Tests | Environnement |
|---|---|---|---|
| Exigences, sources, grille | `tests/test_requirements_grid.py` | 21 | domaine pur |
| Moteur booléen (+ revue adverse) | `tests/test_boolean_engine.py` | 57 | domaine pur |
| Matching, preuves, scoring, qualification | `tests/test_matching_business.py` | 51 | domaine pur |
| **Banc adverse du moteur de preuves** (revue méthodologique) | `tests/test_adversarial_evidence.py` | 115 | domaine pur |
| API de bout en bout, chiffrement, audit, import, export | `tests/test_api_flow.py` | 29 | SQLite ; rejouée sur PostgreSQL 16 |
| Collaboration, bibliothèque, assistant, coach, IA vérifiée | `tests/test_collab_assistant.py` | 26 | SQLite ; rejouée sur PostgreSQL 16 |
| **Durcissement de sécurité** (revue de sécurité) | `tests/test_security_hardening.py` | 29 | SQLite ; rejouée sur PostgreSQL 16 |
| Parcours navigateur réel (Chromium via Playwright) | `tests/test_e2e_browser.py` | 3 | SQLite, serveur uvicorn réel |
| **Total** | | **331** | **331 réussis, 0 échec** (≈ 60 s) |

Sur PostgreSQL 16 : `test_api_flow`, `test_collab_assistant`, `test_security_hardening` rejoués (82 réussis, 1 ignoré car propre au fichier SQLite) ; le test « aucune donnée en clair dans aucune ligne » tourne sur les deux moteurs. 37 tests portent le marqueur `business` (cas métier issus du référentiel).

## 2. Deux revues indépendantes — et ce qu'elles ont changé

Deux relecteurs sans contexte sur le développement (agents distincts, instructions « sceptique », reproduction exigée) ont attaqué le produit **après** que ma propre suite était verte. **Ma suite verte ne prouvait pas ce que je croyais** : elle ne couvrait que des formulations « propres ». Chaque constat a été **reproduit par moi** avant d'être corrigé, puis fixé par un test qui échoue sans le correctif.

### 2.1 Revue méthodologique du moteur (≈ 50 scénarios)
Constat central : des phrases **niées** (« je n'ai jamais configuré Kafka »), **futures**, attribuées à un **tiers**, issues d'une **formation** ou d'un projet personnel obtenaient « confirmé » avec tous les points. Corrigé : analyse par proposition (`domain/clauses.py`), récence ancrée sur la pratique, faits d'appel prudents (une absence extraite ne plafonne rien avant validation humaine), classement impératifs d'abord, volumétrie avec durée, dates plausibles, homonymes, booléens (validation stricte, optimisation sans NOT contradictoire). Détail : [METHODOLOGIE_MAPPING.md](METHODOLOGIE_MAPPING.md#revue-adverse-indépendante-8-octobre-2026--règles-ajoutées).

### 2.2 Revue de sécurité
La première tentative a échoué sur une limite de débit ; je l'ai relancée sur un instantané figé du dépôt. Elle a produit 15 constats, tous reproduits puis corrigés (sauf ce qui est listé en §4) :

| # | Gravité | Constat (observé) | Correction |
|---|---|---|---|
| F6 | Critique | DOCX de 0,6 Mo → 4,2 Go de RAM, 232 s de CPU (bombe de décompression) | Bornes de taille décompressée et de ratio **avant** lecture ; extraction PDF/DOCX dans un **processus isolé** (mémoire, CPU, temps) |
| F1 | Élevée | Toute valeur d'environnement ≠ exactement `prod` réactivait la connexion de démonstration en administrateur sans mot de passe | Environnement **fermé** (dev/test/prod, « production » normalisé, valeur inconnue refusée) ; démonstration limitée à la boucle locale, jamais derrière un relais |
| F3 | Élevée | Données candidat/client en clair en base (noms de fichier, libellés, commentaires, extraits dans les propositions, contacts client…) ; l'affirmation « 0 occurrence en clair » était fausse | Colonnes chiffrées ; test de marqueurs sur **toutes les lignes** (2 moteurs) et sur le **fichier + WAL** ; liste blanche des colonnes restées en clair |
| F7 | Élevée | PDF à flux compressés : 99,5 s pour 94 Ko | Isolation + sortie de décompression bornée + arrêt dès que le texte dépasse la limite |
| F8 | Élevée | 4 PDF piégés d'un utilisateur bloquaient le CV normal d'un autre pendant 128 s | Un pool **par utilisateur** : le CV normal est traité en 0,6 s dans la même attaque (mesuré en serveur réel) |
| F9 | Élevée | Route d'import `async` synchrone : tout le serveur figé (11 s de latence) | Route synchrone exécutée dans le pool de threads ; latence `/health` mesurée à 68 ms pendant l'attaque |
| F2 | Moyenne | En-tête d'identité dupliqué : le premier gagnait | Refus (401) de tout en-tête d'identité ou de secret dupliqué |
| F4 | Moyenne | Extraits de transcription conservés dans `proposals` après suppression et purge | Effacés avec le candidat |
| F10 | Moyenne | Pas de limite de taille de requête ; fichiers refusés conservés (266 Mo) | 413 avant lecture ; fichier refusé non conservé |
| F11 | Moyenne | Journal d'audit modifiable puis « réparable » par recalcul | Chaîne **signée (HMAC)** ; tête de chaîne exposée pour consignation externe |
| F12 | Moyenne | Le niveau « stratégie » voyait les notes libres des recherches | Notes masquées à ce niveau |
| F15 | Moyenne | `GET /documents` déchiffrait tous les fichiers (2 s, 611 Mo) | Colonnes différées |
| F13 | Faible | Un lecteur créait des propositions et des qualifications | Écriture refusée (lecture seule) |
| F14 | Faible | Bornes incomplètes, erreur 500 sur une énumération invalide | Bornes par élément, énumérations fermées (422) |
| F5 | Faible | Déconnexion sans révocation du cookie | Époque de session : cookie copié invalide après déconnexion |

Également corrigés (trouvés avec l'aide des journaux de la première tentative, puis reproduits) : regex quadratiques sur espaces Unicode, budget de temps par document, profondeur booléenne bornée, course sur la confirmation d'une proposition (appliquée 10 fois sous 10 requêtes simultanées), citation « éliminatoire » non traçable, seuils de grille non bornés, suppression d'un candidat laissant son document, gouvernance de la bibliothèque, visibilité des cas de non-régression, nettoyage des coordonnées à l'export.

## 3. Critères d'acceptation (§28) — « je dois pouvoir »

| # | Critère | Preuve |
|---|---|---|
| 1 | Créer une mission | `test_acceptance_mission_requirements_grid_and_searches` ; navigateur |
| 2 | Importer le descriptif client | idem (brief collé, source immuable) |
| 3 | Ajouter des précisions issues d'un appel | idem (source tracée, remplacement jamais implicite) |
| 4 | Consulter et corriger les critères extraits | idem ; navigateur |
| 5 | Valider une grille de scoring | idem (100 points, empreinte, gel ; paramètres bornés) |
| 6 | Générer trois booléens | idem ; `test_three_strategies_valid_within_limit_for_every_reference_case` (8 cas) |
| 7 | Comprendre pourquoi | `test_explanations_are_complete_and_honest` ; navigateur |
| 8 | Copier la recherche choisie | navigateur (bouton « Copier », presse-papiers) |
| 9 | Renseigner les résultats Turnover | acceptation API ; navigateur |
| 10 | Obtenir une nouvelle recherche adaptée | `test_loop_never_replays_a_query_and_ends_by_handing_over_to_a_human` + acceptation API |
| 11 | Sauvegarder la stratégie | acceptation API |
| 12 | Importer dix ou vingt CV | `test_twenty_cvs_in_one_batch_are_analysed_independently`, `test_batch_limit_is_enforced_and_configurable` |
| 13 | Une analyse par CV exploitable | `test_acceptance_batch_cv_progress_comparison_evidence_notes_and_history` (PDF, DOCX, TXT ; échecs isolés) |
| 14 | Comparer à grille identique | idem ; `test_business_8_…` ; `test_ranking_puts_confirmed_imperatives_before_…` |
| 15 | Preuves et manques | idem ; navigateur |
| 16 | Questions de qualification | idem ; `test_questions_are_specific_personalised_and_structured` |
| 17 | Notes d'appel candidat | idem ; `test_business_6_…`, `test_business_9_…` |
| 18 | Actualiser les évaluations | idem (réévaluation, ancienne version conservée) |
| 19 | Changements et raisons | idem (diff critère par critère) |
| 20 | Historique de la mission | idem (`/history`, audit chaîné et signé) ; navigateur |
| 21 | Partager une recherche avec un collègue | `test_business_15_…` ; navigateur à deux utilisateurs ; notes masquées au niveau « stratégie » |
| 22 | Exporter une synthèse pour DT Editor | `test_acceptance_export_for_dt_editor_is_anonymised_faithful_and_sourced` — **format** vérifié ; **consommation par DT Editor non vérifiée** (introuvable) |

> « Le produit doit fonctionner de bout en bout dans un environnement autorisé » : il fonctionne de bout en bout **en développement, sur données fictives**. **Aucun environnement Comet autorisé n'a été utilisé** ; il n'y a eu aucun déploiement.

## 4. Les 15 tests métier obligatoires (§26)
Les 15 sont implantés et passent. Tableau détaillé dans [METHODOLOGIE_MAPPING.md](METHODOLOGIE_MAPPING.md#26--les-15-tests-métier-obligatoires).

## 5. Ce qui n'a PAS été fait, testé ou vérifié (honnêtement)

| Sujet | État |
|---|---|
| Données réelles de candidats, de clients, de missions | **Aucune utilisée.** Fixtures fictives en noms de code |
| Déploiement, conteneur, CI | **Aucun.** Pas de Dockerfile ni de workflow ; l'adaptateur Python de la CI de sécurité Comet n'existe pas ; aucun scan de dépendances exécuté |
| **Migrations de base** | **Aucun outil de migration** (pas d'Alembic) : les colonnes ajoutées pendant les revues (chiffrement, `session_epoch`, `attempts`, `candidate_id`) imposent de **recréer la base de développement**. À traiter avant toute base à conserver |
| Syntaxe réelle de Turnover | **Non confirmée** (`syntax_confirmed=False`) ; requêtes jamais exécutées sur Turnover. « Trop large / trop restrictif » vient du retour saisi par le recruteur, jamais d'une estimation |
| Fournisseur d'IA (Anthropic) | Code écrit, **jamais appelé en réel** ; la vérification par citation est testée avec un faux fournisseur |
| SSO Comet réel | Contrat de passerelle testé avec une passerelle **simulée** ; comportement réel sur en-têtes dupliqués, limite de corps, joignabilité directe : **non vérifiable** ici |
| Boond / BigQuery / Drive / Notion / Granola / Noota / DT Editor / Turn | **Aucun accès utilisé** |
| OCR, audio | **Non supportés** : échec explicite pour un PDF sans couche texte |
| Pertinence métier sur de vraies populations | **Non mesurée** (ni précision, ni rappel). Les règles de portée sont des motifs lexicaux FR/EN ; seuils et facteurs = valeurs de départ défendues par des cas, **non calibrées** |
| Extrapolation des attaques par fichier | Mesures faites à petite échelle sous plafond mémoire ; le cas qui saturerait 16 Go n'a pas été exécuté |
| File de tâches durable multi-instances | **Non réalisée** : pools de threads par utilisateur dans le processus ; reprise au redémarrage limitée à 2 tentatives par document |
| Limitation de débit (requêtes/minute) | **Non réalisée** : à placer sur la passerelle |
| Gestion/rotation de la clé de chiffrement, sauvegardes | **Non réalisées** (clé unique ; une rotation de clé invalide aussi la chaîne d'audit signée) |
| Détection d'injection de prompt | Détection **consultative** : une formulation inédite ou un texte blanc passent (le scoring déterministe n'« obéit » pas au document, mais l'alerte peut manquer) |
| Cas de référence cybersécurité O+O, QA e-commerce | Brief et lexique couverts ; pas de CV de référence dédié |
| Accessibilité | Éléments de base ; **aucun audit RGAA/WCAG** |
| Procédure RGPD, AIPD, AI Act, durée de conservation | **Ouvertes** (voir SECURITE_RGPD.md) |

## 6. Performance (mesures ponctuelles, non représentatives d'une charge réelle)
- 20 CV fictifs courts importés et analysés par l'API : < 1 s (SQLite, synchrone).
- Entrées hostiles : espaces Unicode, nombres de 16 000 chiffres, 20 000 répétitions, 100 000 espaces → < 1,5 s chacune (budget testé à 6 s). Un CV à contenu répétitif de 150 000 caractères est refusé (`too_long`).
- Attaque par documents piégés (4 PDF de 94 Ko) : l'import répond en 0,04 s, le CV normal d'un autre utilisateur est traité en 0,6 s, `/health` ≤ 68 ms ; les documents piégés échouent avec un motif explicite.
