# Recette : état exact au 8 octobre 2026

Ce document répond à la consigne « si tu ne peux pas tout réaliser, donne l'état exact : code réalisé, tests exécutés, travail restant ». Tout ce qui est marqué **testé** a été exécuté dans l'environnement de développement avec des données **fictives**. Ce qui n'a pas été exécuté est dit.

## 1. Résultat des tests

| Suite | Fichier | Tests | Environnement |
|---|---|---|---|
| Exigences, sources, grille | `tests/test_requirements_grid.py` | 21 | SQLite (domaine pur) |
| Moteur booléen | `tests/test_boolean_engine.py` | 48 | domaine pur |
| Matching, preuves, scoring, qualification | `tests/test_matching_business.py` | 50 | domaine pur |
| API de bout en bout, sécurité, import, export | `tests/test_api_flow.py` | 21 | SQLite ; rejouée sur PostgreSQL 16 (sauf 1 test propre au fichier SQLite) |
| Collaboration, bibliothèque, assistant, coach, IA vérifiée | `tests/test_collab_assistant.py` | 22 | SQLite ; rejouée sur PostgreSQL 16 |
| Parcours navigateur réel (Chromium via Playwright) | `tests/test_e2e_browser.py` | 3 | SQLite, serveur uvicorn réel |
| **Total** | | **165** | **165 réussis, 0 échec, 0 ignoré** (exécution complète : 47 s) |

37 de ces tests portent le marqueur `business` (cas métier issus du référentiel).

**Non exécuté :** voir §4.

## 2. Critères d'acceptation (§28) — « je dois pouvoir »

| # | Critère | Preuve |
|---|---|---|
| 1 | Créer une mission | `test_acceptance_mission_requirements_grid_and_searches` ; navigateur |
| 2 | Importer le descriptif client | idem (brief collé, source immuable) |
| 3 | Ajouter des précisions issues d'un appel | idem (source tracée, remplacement jamais implicite) |
| 4 | Consulter et corriger les critères extraits | idem ; navigateur |
| 5 | Valider une grille de scoring | idem (100 points, empreinte, gel) |
| 6 | Générer trois booléens | idem ; `test_three_strategies_valid_within_limit_for_every_reference_case` (8 cas) |
| 7 | Comprendre pourquoi | `test_explanations_are_complete_and_honest` ; navigateur |
| 8 | Copier la recherche choisie | navigateur (bouton « Copier », presse-papiers) |
| 9 | Renseigner les résultats Turnover | acceptation API ; navigateur |
| 10 | Obtenir une nouvelle recherche adaptée | `test_loop_never_replays_a_query_and_ends_by_handing_over_to_a_human` + acceptation API |
| 11 | Sauvegarder la stratégie | acceptation API |
| 12 | Importer dix ou vingt CV | `test_twenty_cvs_in_one_batch_are_analysed_independently`, `test_batch_limit_is_enforced_and_configurable` |
| 13 | Une analyse par CV exploitable | `test_acceptance_batch_cv_progress_comparison_evidence_notes_and_history` (PDF, DOCX, TXT ; échecs isolés) |
| 14 | Comparer à grille identique | idem ; `test_business_8_…` (10 CV) |
| 15 | Preuves et manques | idem ; navigateur |
| 16 | Questions de qualification | idem ; `test_questions_are_specific_personalised_and_structured` |
| 17 | Notes d'appel candidat | idem ; `test_business_6_…`, `test_business_9_…` |
| 18 | Actualiser les évaluations | idem (réévaluation, ancienne version conservée) |
| 19 | Changements et raisons | idem (diff critère par critère) |
| 20 | Historique de la mission | idem (`/history`, audit chaîné) ; navigateur |
| 21 | Partager une recherche avec un collègue | `test_business_15_…` ; navigateur à deux utilisateurs |
| 22 | Exporter une synthèse pour DT Editor | `test_acceptance_export_for_dt_editor_is_anonymised_faithful_and_sourced` — **format** vérifié ; **consommation par DT Editor non vérifiée** (introuvable) |

> « Le produit doit fonctionner de bout en bout dans un environnement autorisé » : il fonctionne de bout en bout **en développement, sur données fictives**. **Aucun environnement Comet autorisé n'a été utilisé** ; il n'y a eu aucun déploiement.

## 3. Les 15 tests métier obligatoires (§26)

Les 15 sont implantés et passent. Tableau détaillé dans [METHODOLOGIE_MAPPING.md](METHODOLOGIE_MAPPING.md#26--les-15-tests-métier-obligatoires).

## 4. Ce qui n'a PAS été fait, testé ou vérifié

| Sujet | État |
|---|---|
| Données réelles de candidats, de clients, de missions | **Aucune utilisée.** Fixtures fictives en noms de code |
| Déploiement, conteneur, CI | **Aucun.** Pas de Dockerfile ni de workflow ; l'adaptateur Python de la CI de sécurité Comet n'existe pas |
| Syntaxe réelle de Turnover | **Non confirmée** (`syntax_confirmed=False`) ; les requêtes n'ont jamais été exécutées sur Turnover. Le jugement « trop large / trop restrictif » vient du retour saisi par le recruteur, jamais d'une estimation |
| Fournisseur d'IA (Anthropic) | Code écrit, **jamais appelé en réel** (aucune clé, aucune autorisation de coût) ; la logique de vérification par citation est testée avec un faux fournisseur |
| SSO Comet réel | Contrat de passerelle testé avec une passerelle **simulée** |
| Boond / BigQuery / Drive / Notion / Granola / Noota / DT Editor / Turn | **Aucun accès utilisé** (non connectés ou introuvables) |
| OCR (CV scannés), audio | **Non supportés** : échec explicite pour un PDF sans couche texte |
| Langues autres que le français et l'anglais dans les CV | Non traitées |
| Performance en charge | Mesures ponctuelles seulement (voir §5) ; pas de test de charge |
| Accessibilité | Éléments de base (libellés, focus visible, rôles ARIA, aller au contenu, contrastes clair/sombre) ; **aucun audit RGAA/WCAG** |
| Pertinence métier sur de vraies populations | **Non mesurée** : ni précision, ni rappel. Les seuils (96/95, 7 ans, facteurs 1,0/0,5/0,15) sont des valeurs de départ défendues par des cas, pas calibrées statistiquement |
| Cas de référence cybersécurité O+O et QA e-commerce | Brief et lexique couverts ; **pas de CV de référence** dédié |
| Procédure RGPD, AIPD, AI Act, durée de conservation | **Ouvertes** (voir SECURITE_RGPD.md) |

## 5. Constats de performance (ponctuels, non représentatifs)

- 20 CV fictifs courts (≈ 1 300 caractères) importés et analysés par l'API, SQLite, traitement synchrone : **0,9 s** au total (`test_twenty_cvs_in_one_batch_are_analysed_independently`).
- Structuration d'un texte de 64 000 caractères (`parse_cv` seul) : **0,1 s**, croissance linéaire sur 1 300 → 64 000 caractères.
- **Non mesuré à ce stade** : l'évaluation complète des preuves sur un CV très long ou adversarial (texte répétitif, 100 000 caractères). Une revue indépendante teste ce point ; son résultat sera consigné ici, et un correctif ajouté si une lenteur est confirmée. Tant que ce n'est pas fait, aucune garantie de temps de traitement n'est donnée au-delà du plafond de taille de fichier (10 Mo) et de pages (40).
