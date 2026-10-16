# Recette : état exact au 16 octobre 2026 (V1.2 : lien Boond en lecture seule)

Ce document répond à la consigne « si tu ne peux pas tout réaliser, donne l'état exact : code réalisé, tests exécutés, travail restant ». Tout ce qui est marqué **testé** a été exécuté dans l'environnement de développement avec des données **fictives**. Ce qui n'a pas été exécuté est dit.

## 1. Résultat des tests

| Suite | Fichier | Tests | Environnement |
|---|---|---|---|
| Exigences, sources, grille | `tests/test_requirements_grid.py` | 21 | domaine pur |
| Moteur booléen (+ revue adverse) | `tests/test_boolean_engine.py` | 57 | domaine pur |
| Matching, preuves, scoring, qualification | `tests/test_matching_business.py` | 51 | domaine pur |
| **Banc adverse du moteur de preuves** (revue méthodologique) | `tests/test_adversarial_evidence.py` | 115 | domaine pur |
| API de bout en bout, chiffrement, audit, import, export | `tests/test_api_flow.py` | 29 | SQLite ; rejouée sur PostgreSQL 16 |
| Collaboration, bibliothèque, assistant, coach, IA vérifiée | `tests/test_collab_assistant.py` | 32 | SQLite ; rejouée sur PostgreSQL 16 |
| **Durcissement de sécurité** (revue de sécurité) + import de descriptif | `tests/test_security_hardening.py` | 35 | SQLite ; rejouée sur PostgreSQL 16 |
| **Dossier au format Comet** | `tests/test_dossier_comet.py` | 10 | domaine pur + API |
| **Exploitation** : migrations, maintenance planifiée, limitation de débit, `.env.example` | `tests/test_operations.py` | 13 | SQLite ; PostgreSQL 16 pour la migration |
| **Historique transverse et administration** | `tests/test_history_admin.py` | 10 | SQLite ; rejouée sur PostgreSQL 16 |
| **Lien Boond en lecture seule** (adaptateurs, import, déduplication, minimisation, configuration) | `tests/test_boond_link.py` | 16 | SQLite ; rejouée sur PostgreSQL 16 |
| Parcours navigateur réel (Chromium via Playwright) | `tests/test_e2e_browser.py` | 8 | SQLite, serveurs uvicorn réels (dont un avec l'import Boond sur instantané fictif) |
| **Total** | | **397** | **396 réussis, 1 ignoré** (migration PostgreSQL, rejouée à part), 0 échec (≈ 80 s) |

Sur PostgreSQL 16 (cluster local, base jetable) : `test_api_flow`, `test_collab_assistant`, `test_security_hardening`, `test_operations`, `test_history_admin`, `test_boond_link` rejoués — **134 réussis, 1 ignoré** (vérification propre au fichier SQLite) ; la migration est comparée au modèle sur PostgreSQL ; le test « aucune donnée en clair dans aucune ligne » tourne sur les deux moteurs. 37 tests portent le marqueur `business` (cas métier issus du référentiel).

> Le code de sortie de la suite complète est **0** ; il est lu directement (et non via un filtre qui le masquerait : une validation précédente avait été commitée avec un test en échec pour cette raison, corrigée depuis).

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

### 2.3 Évolutions V1.1 (16 octobre 2026) — **aucune liaison Boond, aucune donnée réelle, aucun déploiement**
| Évolution | Vérification |
|---|---|
| Import du descriptif client par fichier (PDF, Word, texte) : le texte revient au navigateur pour relecture, le fichier n'est pas conservé | API + navigateur (fichier Word réel, fichier illisible expliqué) |
| Export du dossier au format de structuration Comet (phrases du CV rangées mot pour mot, aucune technologie ajoutée, client ≠ interne), téléchargeable | `test_dossier_comet.py` + navigateur |
| Migrations Alembic ; démarrage refusé en production sur un schéma non à jour | `test_operations.py` (SQLite, PostgreSQL) |
| Maintenance planifiée (purge, tête d'audit consignée), **désactivée par défaut** ; limitation de débit par utilisateur | `test_operations.py` (horloge et fil simulés) |
| Assistant : résumé de mission, « quels profils écarter ? » (liste sans jamais écarter), alerte quand le score cité ≠ score actuel, ordre « impératifs d'abord » | `test_collab_assistant.py` |
| Écran d'administration (comptes, état, purge avec prévisualisation, vérification d'audit) ; historique transverse | `test_history_admin.py` + 2 parcours navigateur |

Défauts trouvés **par mes tests ou ma propre relecture** pendant cette phase, corrigés : (1) l'entrée « Historique » faisait **déborder la barre de navigation de 84 px sur mobile** (test de bout en bout) ; (2) un menu « Rôle » restait sur une valeur refusée par le serveur (relecture ; vérifié ensuite par un test navigateur) ; (3) l'alerte « score cité » prenait « le 15 octobre » ou « 12 ans » pour un score ; (4) la création de compte acceptait une adresse sans forme d'adresse ; (5) un marqueur de test `sqlite_only` sans effet laissait croire qu'un test était exclu de PostgreSQL. **Je n'ai pas relancé de revue indépendante sur ces évolutions** : seule ma propre relecture adverse a eu lieu.

### 2.4 Lien Boond en lecture seule (V1.2, 16 octobre 2026)
Sur instruction explicite de relier l'application à Boond pour la tester avec ses données (levée de la consigne « sans Boond » du même jour), j'ai construit un **import des besoins en lecture seule** (voir [INTEGRATION.md §5](INTEGRATION.md#5-boond--lecture-seule-des-besoins)) : adaptateur BigQuery (liste fermée de tables et de colonnes, `SELECT` paramétrés, aucun contact/budget/tarif/CV/candidat), adaptateur « fichier » de développement, référence externe sur les missions avec **contrainte d'unicité** (migration `0002`), déduplication, révisions sans réécriture, écran d'import. Désactivé par défaut.

| Ce qui est **vérifié** | Comment |
|---|---|
| Rien n'est écrit dans Boond ; seules des lectures de colonnes autorisées sont émises ; le texte utilisateur est un paramètre lié | requêtes capturées et inspectées ; refus de tous les mots d'écriture |
| Un besoin n'est jamais dupliqué, même sous imports simultanés | test avec 6 requêtes parallèles, sur SQLite **et** PostgreSQL ; **mutation** : sans la contrainte d'unicité, le doublon passe et le test échoue |
| Un besoin modifié ajoute une source sans toucher une grille figée | test d'API |
| Un besoin sans descriptif exploitable est refusé, pas complété | test d'API + interface |
| Les erreurs d'infrastructure ne fuient aucun détail | exécuteur simulé qui lève une erreur contenant un « secret » |
| Le dépôt public ne contient aucun identifiant réel (projet, dataset, compte) | test qui parcourt le code, l'interface et la documentation |

| Ce qui n'est **pas** vérifié | |
|---|---|
| **Exécution de l'adaptateur BigQuery par l'application contre l'entrepôt réel** | Aucun identifiant applicatif dans cet environnement ; les identifiants du connecteur de développement ne sont pas ceux de l'application. La bibliothèque `google-cloud-bigquery` n'est pas installée ici : l'adaptateur n'a été exercé qu'avec un exécuteur simulé |
| **Essai avec les données réelles de la sandbox** | **Non réalisé** : la lecture de lignes de besoins depuis BigQuery a été refusée trois fois par le contrôle automatique des données personnelles (même sans noms de sociétés) ; je ne l'ai pas contournée. Côté BigQuery, seuls des métadonnées et des comptages ont été lus ; côté connecteur de la sandbox Boond, une fiche d'exemple par module (besoins, positionnements, sociétés) pour comparer des totaux. Aucun contenu n'est conservé dans le dépôt |
| Qualité de l'analyse sur de vrais besoins Boond | Inconnue : aucun vrai besoin n'a été analysé |

## 3. Critères d'acceptation (§28) — « je dois pouvoir »

| # | Critère | Preuve |
|---|---|---|
| 1 | Créer une mission | `test_acceptance_mission_requirements_grid_and_searches` ; navigateur |
| 2 | Importer le descriptif client | idem (brief collé, source immuable) ; import PDF / Word / texte : `test_a_brief_can_be_imported_from_pdf_docx_and_text_without_storing_the_file` + navigateur |
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
| 20 | Historique de la mission | idem (`/history`, audit chaîné et signé) ; navigateur ; **historique transverse** : `test_history_*` + navigateur |
| 21 | Partager une recherche avec un collègue | `test_business_15_…` ; navigateur à deux utilisateurs ; notes masquées au niveau « stratégie » |
| 22 | Exporter une synthèse pour DT Editor | `test_acceptance_export_for_dt_editor_is_anonymised_faithful_and_sourced`, `test_dossier_comet.py` — **format** vérifié ; **consommation par DT Editor non vérifiée** (introuvable) |

> « Le produit doit fonctionner de bout en bout dans un environnement autorisé » : il fonctionne de bout en bout **en développement, sur données fictives**. **Aucun environnement Comet autorisé n'a été utilisé** ; il n'y a eu aucun déploiement.

## 4. Les 15 tests métier obligatoires (§26)
Les 15 sont implantés et passent. Tableau détaillé dans [METHODOLOGIE_MAPPING.md](METHODOLOGIE_MAPPING.md#26--les-15-tests-métier-obligatoires).

## 5. Ce qui n'a PAS été fait, testé ou vérifié (honnêtement)

| Sujet | État |
|---|---|
| Données réelles de candidats, de clients, de missions | **Aucune utilisée.** Fixtures fictives en noms de code |
| Déploiement, conteneur, CI | **Aucun.** Pas de Dockerfile ni de workflow ; l'adaptateur Python de la CI de sécurité Comet n'existe pas ; aucun scan de dépendances exécuté |
| **Migrations de base** | **Alembic en place** (baseline `0001`, 19 tables), comparée au modèle sur SQLite et PostgreSQL, retour arrière et rejeu testés. **Jamais jouée sur une base contenant des données réelles** ; aucune migration d'évolution n'a encore été nécessaire ; sauvegarde avant migration à prévoir |
| Syntaxe réelle de Turnover | **Non confirmée** (`syntax_confirmed=False`) ; requêtes jamais exécutées sur Turnover. « Trop large / trop restrictif » vient du retour saisi par le recruteur, jamais d'une estimation |
| Fournisseur d'IA (Anthropic) | Code écrit, **jamais appelé en réel** ; la vérification par citation est testée avec un faux fournisseur |
| SSO Comet réel | Contrat de passerelle testé avec une passerelle **simulée** ; comportement réel sur en-têtes dupliqués, limite de corps, joignabilité directe : **non vérifiable** ici |
| **Boond / BigQuery** | **Reliés en lecture seule (besoins), désactivé par défaut**, code et tests prêts ; **jamais exécuté contre l'entrepôt réel par l'application** (voir §2.4). Aucune écriture dans Boond n'existe |
| Drive / Notion / Granola / Noota / DT Editor / Turn | **Aucun accès utilisé** |
| OCR, audio | **Non supportés** : échec explicite pour un PDF sans couche texte |
| Pertinence métier sur de vraies populations | **Non mesurée** (ni précision, ni rappel). Les règles de portée sont des motifs lexicaux FR/EN ; seuils et facteurs = valeurs de départ défendues par des cas, **non calibrées** |
| Extrapolation des attaques par fichier | Mesures faites à petite échelle sous plafond mémoire ; le cas qui saturerait 16 Go n'a pas été exécuté |
| File de tâches durable multi-instances | **Non réalisée** : pools de threads par utilisateur dans le processus ; reprise au redémarrage limitée à 2 tentatives par document |
| Limitation de débit (requêtes/minute) | **Réalisée dans l'application** (par utilisateur, en mémoire du processus, testée avec horloge simulée) ; **non testée sous charge réelle**, non partagée entre instances ; limitation par adresse à placer sur la passerelle |
| Maintenance planifiée (purge, tête d'audit) | Code et tests avec fil et horloge simulés ; **jamais exécutée en service**, **désactivée par défaut** ; la purge attend la validation de la durée par le DPO ; la **réplication hors base** du fichier de tête d'audit relève de l'infrastructure |
| Écran d'administration, historique transverse | Testés (API + navigateur Chromium). **Aucun audit d'accessibilité** ; l'historique parcourt toutes les missions accessibles à chaque requête (acceptable pour quelques dizaines de missions, **non mesuré** au-delà) |
| Assistant | Questions **fermées** (motifs lexicaux FR), pas un dialogue libre : une formulation hors des intentions prévues reçoit une réponse d'aide, pas une réponse inventée |
| Gestion/rotation de la clé de chiffrement, sauvegardes | **Non réalisées** (clé unique ; une rotation de clé invalide aussi la chaîne d'audit signée) |
| Détection d'injection de prompt | Détection **consultative** : une formulation inédite ou un texte blanc passent (le scoring déterministe n'« obéit » pas au document, mais l'alerte peut manquer) |
| Cas de référence cybersécurité O+O, QA e-commerce | Brief et lexique couverts ; pas de CV de référence dédié |
| Accessibilité | Éléments de base ; **aucun audit RGAA/WCAG** |
| Procédure RGPD, AIPD, AI Act, durée de conservation | **Ouvertes** (voir SECURITE_RGPD.md) |

## 6. Performance (mesures ponctuelles, non représentatives d'une charge réelle)
- 20 CV fictifs courts importés et analysés par l'API : < 1 s (SQLite, synchrone).
- Entrées hostiles : espaces Unicode, nombres de 16 000 chiffres, 20 000 répétitions, 100 000 espaces → < 1,5 s chacune (budget testé à 6 s). Un CV à contenu répétitif de 150 000 caractères est refusé (`too_long`).
- Attaque par documents piégés (4 PDF de 94 Ko) : l'import répond en 0,04 s, le CV normal d'un autre utilisateur est traité en 0,6 s, `/health` ≤ 68 ms ; les documents piégés échouent avec un motif explicite.
