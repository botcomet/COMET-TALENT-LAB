# Du référentiel Comet au code : traçabilité

Chaque règle du référentiel (Partie III et IV du prompt maître) est traduite en règle vérifiable, implantée dans un module et couverte par un test nommé. `tests/` est la source de vérité : un test qui échoue signale une règle métier violée.

## §4 — Le besoin client est la référence

| Règle | Implémentation | Test |
|---|---|---|
| Le titre ne suffit pas : identifier le travail réel | `domain/brief.py` (activités, alerte titre ↔ travail) | `test_title_alone_is_not_enough_title_vs_work_warning` |
| 6 catégories obligatoires ; jamais « éliminatoire » parce qu'une techno est listée | `domain/enums.Category`, `brief._category_for` | `test_technology_in_a_list_is_never_eliminatory` |
| « Éliminatoire » : confirmation client tracée (source + extrait) | `grid.validate`, `services/missions.update_requirement` | `test_eliminatory_without_traced_client_confirmation_is_refused`, `test_acceptance_mission_requirements_grid_and_searches` |
| Information absente : jamais inventée → question | `brief.extract_modalities`, `missing_info` | `test_missing_information_is_a_question_never_invented` |
| Hiérarchie des sources (impératif confirmé > précision validée > retour d'entretien > brief > initial), date, remplacement explicite | `domain/requirements.resolve` | `test_recent_clarification_does_not_erase_older_confirmed_imperative` et 3 voisins |
| Provenance, date, validation, version | `Requirement.source_*`, `MissionSource` (immuable), `AuditEvent` | `test_acceptance_mission_requirements_grid_and_searches` |
| Exigence « k parmi n » (cas infrastructure) | `brief._GROUP`, `grid` (critère de groupe) | `test_eliminatory_only_when_text_says_so_and_group_is_modelled` |

## §5 — Booléens

| Règle | Implémentation | Test |
|---|---|---|
| Trois stratégies (exploratoire, équilibrée, stricte) | `domain/search_plan.build_search_set` | `test_three_strategies_valid_within_limit_for_every_reference_case` (8 cas) |
| ≤ 250 caractères configurable ; retrait du redondant puis du secondaire sans casser la logique | `search_plan.fit`, `PlatformProfile.max_length` | `test_length_fit_trims_synonyms_then_secondary_then_weakest_imperative_never_the_role_first` |
| Parenthèses, priorité AND/OR, opérateur orphelin | `domain/boolean.parse/validate` | `test_syntax_errors_are_detected` (7 cas), `test_reference_example_from_methodology_parses_and_round_trips` |
| Pas de NOT systématique | builder : aucun NOT ; optimiseur : seulement sur faux positif signalé, risque affiché | `test_no_systematic_negative_filters`, `test_not_filter_only_for_an_identified_false_positive_and_with_its_risk_stated` |
| Alias équivalents (OR) ≠ termes voisins ; pas de terme ambigu | `lexicon.Skill.aliases/related`, `boolean_terms` | `test_generic_and_ambiguous_terms_stay_out_of_the_boolean` |
| Explications : titres, technologies, synonymes, exclusions, risques FP/FN | `search_plan.explain` | `test_explanations_are_complete_and_honest` |
| **Exception §5.5** (plusieurs technologies imposées) : variante stricte, avertissement faible volume, présence textuelle ≠ expérience | `search_plan.k_of_n`, `explain` | `test_strict_variant_covers_exactly_the_imposed_combinations_and_warns_about_volume` (couverture combinatoire prouvée par évaluation locale) |
| Aucun nombre de résultats simulé | aucune estimation dans les sorties | `test_result_counts_are_never_simulated` |
| Boucle d'amélioration : diagnostic, une modification expliquée, jamais la même requête | `domain/search_optimizer` | `test_loop_never_replays_a_query_and_ends_by_handing_over_to_a_human` |
| §5.7 : la découverte évolue, le besoin ne change pas | grille et exigences jamais touchées par l'optimiseur | `test_business_1…` (comparaison avant/après des exigences) |

## §6 — Scoring

| Règle | Implémentation | Test |
|---|---|---|
| Grille propre à la mission, 100 points, validée | `domain/grid.propose_grid/validate/freeze` | `test_grid_is_mission_specific_not_universal`, `test_cannot_freeze_with_weights_not_100` |
| Grille figée pendant la comparaison ; nouvelle version avec motif et diff | `grid.freeze/assert_unchanged/new_version`, empreinte SHA-256 | `test_frozen_grid_is_tamper_evident_and_new_version_requires_reason`, `test_assessment_requires_a_frozen_grid` |
| Un impératif ne peut pas être retiré de la grille | `grid.validate` | `test_imperative_requirement_cannot_be_dropped_from_grid` |
| Mention ≠ démonstration | `domain/evidence` (lieu de la mention + signaux factuels) | `test_case_tech_lead_java_kafka_is_real_but_modest_not_expert`, `test_business_3…` |
| Niveaux : confirmé / partiel / déclaré / non documenté / contredit | `enums.Level` | `test_not_documented_is_never_confused_with_contradicted` |
| Profondeur « avancée » exigée | `Req.depth_required`, `evidence.advanced_terms_in`, `merge_external` | `test_advanced_depth_requirement_applies_to_call_evidence_too` |
| Volumétrie : débit chiffré, par portée (Kafka ≠ banque) | `evidence._evaluate_volume`, `volume_per_day` | `test_case_tech_lead_java_bank_volumetry_is_not_kafka_volumetry` |
| Impératifs non compensables ; plafond **seulement** sur absence explicite | `scoring.assess`, `grid.caps` | `test_business_4_…`, `test_business_4b_…` |
| Contraintes (TJM, présence, dispo, fuseau) séparées de la technique ; inconnu ≠ incompatible | `scoring.evaluate_constraints` | `test_business_5_…`, `test_explicit_refusal_of_constraints_is_flagged_clearly_and_separate_from_skills` |
| Potentiel = borne haute, jamais une compétence acquise | `scoring.assess` | `test_business_4_…` (`score_potential < 100`) |
| Trois évaluations distinctes (adéquation / qualité d'information / qualité du dossier) | `Assessment.score_*`, `information_quality`, `dossier_quality=None` | `test_information_quality_is_separate_from_fit_and_ignores_prose_style` |
| Seuils 96 / 95 configurables ; aucun rejet automatique | `grid.thresholds`, `Tier`, `set_status` | `test_work_view_hides_but_never_deletes_low_scores`, `test_candidate_status_discard_requires_a_human_reason` |
| Récence | `evidence` (fenêtre, volatilité) | `test_business_12_…`, `test_explicit_client_recency_requirement_sets_a_short_window` |
| Cohérence des dates ; aucune date inventée | `cv_extract.parse_cv` | `test_date_discrepancy_is_reported_not_silently_fixed` |

## §7 — Appels et transcriptions

| Règle | Implémentation | Test |
|---|---|---|
| Exigence client ≠ compétence candidat | `call_facts` (`client_requirement` vs `candidate_experience`) | `test_client_requirement_is_not_a_candidate_skill` |
| « Je n'ai utilisé Kafka que sur deux topics » limite, ne change pas le besoin | `EvidenceKind.LIMITS` | `test_candidate_statement_limits_experience_but_does_not_change_the_requirement` |
| Transcription automatique = source à vérifier | plafond « partiel » tant que non validée | `test_unvalidated_automatic_transcript_is_capped_until_validated` |
| Locuteur, certitude, contradiction, historique intact | `Evidence` append-only + `EvidenceReview` | `test_business_9_api_…`, `test_business_6_call_confirms_…` |
| Analyse initiale vs enrichie, raisons des changements | `scoring.diff_assessments`, `Assessment.prev_id` | `test_acceptance_batch_cv_progress_comparison_evidence_notes_and_history` |

## §16, §17, §18, §22, §19 — Qualification, bibliothèque, coach, assistant, export

| Règle | Test |
|---|---|
| Questions spécifiques, personnalisées au CV, avec preuves et signaux d'approfondissement | `test_questions_are_specific_personalised_and_structured`, `test_every_template_is_non_generic` |
| Enseignement client-spécifique jamais universel automatiquement ; validation humaine | `test_client_feedback_proposes_a_lesson_that_stays_client_specific_until_validated` |
| Correction de scoring → cas de non-régression, aucune règle globale modifiée | `test_recruiter_correction_lowers_level_records_error_nature_and_changes_no_global_rule` |
| Pas de classement des recruteurs | `test_coach_explains_professional_reasoning_without_ranking_recruiters` |
| L'assistant propose, un utilisateur autorisé confirme | `test_assistant_proposes_requirement_change_and_applies_nothing_until_confirmed` |
| Export DT : anonymisé, verbatim, sans technologie ajoutée, provenance interne séparée | `test_acceptance_export_for_dt_editor_is_anonymised_faithful_and_sourced` |
| Descriptif candidat : « Non communiqué », jamais inventé | `test_candidate_description_never_invents_missing_conditions` |

## §26 — Les 15 tests métier obligatoires

| # | Test | Fichier |
|---|---|---|
| 1 | Booléen trop restrictif (7 AND → 0) | `test_boolean_engine::test_business_1_…` |
| 2 | Recherche trop large (> 10 000) | `test_boolean_engine::test_business_2_…` |
| 3 | Expertise déclarée non démontrée | `test_matching_business::test_business_3_…` |
| 4 | Impératif explicitement absent / non documenté | `test_business_4_…`, `test_business_4b_…` |
| 5 | Information absente (disponibilité) | `test_business_5_…` |
| 6 | Nouvelle information candidat | `test_business_6_…` (domaine + API) |
| 7 | Retour client → nouvelle version de grille | `test_business_7_…` (domaine + API) |
| 8 | Cohérence inter-candidats (10 CV) | `test_business_8_…` |
| 9 | Contradiction CV ↔ appel | `test_business_9_…` (domaine + API) |
| 10 | Justification citable ou rétrogradation | `test_business_10_…` (CV + IA) |
| 11 | Recherche large, matching strict | `test_business_11_…` |
| 12 | Compétence ancienne | `test_business_12_…` |
| 13 | Qualité d'écriture ≠ compétence | `test_business_13_…` |
| 14 | Échec d'extraction documentaire (7 cas) | `test_business_14_…` |
| 15 | Travail collaboratif sans fuite de données candidat | `test_business_15_…` (API) et test navigateur |

## Cas de référence (Partie IV) couverts par des tests

Tech Lead Java (Kafka réel mais modeste, volumétrie bancaire ≠ Kafka, e-commerce familial) · Design System (profil A vs B, retour client) · QA (via lexique et brief) · Data Quality vs Data Analyst reporting · SAP GTS E4H (AMOA vs configuration, fuseau horaire séparé) · F5 (brief et booléens) · Infrastructure (Huawei présent ≠ administré, exigence 2-parmi-4) · WMS/Reflex (impératif non compensable) · RUN (application en production ≠ RUN) · architecture (participation ≠ décision).
**Non couvert par un test dédié** : cas cybersécurité O+O (présent dans le lexique et le catalogue de questions, pas de CV de référence) et cas QA e-commerce de bout en bout (brief testé, pas de CV QA).
