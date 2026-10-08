# Architecture

## 1. Principes de conception

1. **Le métier est du code pur et testable.** `talentlab/domain/` n'a aucune entrée/sortie : brief, exigences, grille, booléens, preuves, scoring, qualification, faits d'appel. Il s'exécute sans base ni réseau, ce qui permet de rejouer à l'identique une évaluation (tests de non-régression).
2. **Le scoring est déterministe et explicable.** Aucun nombre ne sort d'un modèle de langage. Chaque point vient d'un poids de grille × un facteur de couverture lié à un niveau de preuve, lui-même adossé à un extrait localisé du document.
3. **L'IA est optionnelle et bornée.** Elle ne fait que *désigner des passages* ; chaque affirmation doit citer le texte mot pour mot (`claims.locate`), puis le niveau est recalculé par le moteur. Une affirmation invérifiable devient une *hypothèse*, hors score. Les documents sont enveloppés comme données non fiables (`safety.wrap_untrusted`).
4. **Aucune décision automatique sur une personne.** Aucun rejet ; les vues « de travail » masquent sans supprimer ; écarter un candidat exige un motif humain.
5. **Historique inaltérable.** Sources, preuves et audit sont ajoutés, jamais réécrits ; une correction crée une nouvelle ligne.

## 2. Couches

```
Navigateur (frontend/, JS natif, aucun build)
   │  JSON + cookie signé (dev) / en-têtes de passerelle (prod) + en-tête CSRF
FastAPI  talentlab/api/routes.py ── schémas pydantic stricts (extra=forbid)
   │
services/   missions · searches · matching · export · knowledge · assistant      (orchestration, droits, audit, transactions)
   │
domain/     brief · requirements · grid · boolean · search_plan · search_optimizer
            cv_extract · evidence · scoring · qualification · call_facts · claims · safety · coach · lexicon
   │
SQLAlchemy 2  models.py (19 tables)  ──  SQLite (dev/tests) | PostgreSQL 16 (vérifié)
                chiffrement applicatif Fernet sur les champs sensibles (EncText / EncJSON / EncBytes)
```

## 3. Modèle de données (19 tables)

| Groupe | Tables | Remarques |
|---|---|---|
| Identité et droits | `users`, `shares` | rôles `admin` / `pilote` / `talent_manager` ; partage `strategie` < `lecture` < `edition` |
| Besoin | `missions`, `mission_sources`, `requirements`, `grids` | sources immuables ; exigences versionnées avec provenance (source, date, extrait, validation) ; grille `draft` → `frozen` avec empreinte SHA-256 |
| Sourcing | `searches`, `search_feedbacks` | une requête = variante + stratégie + plateforme + explication + historique de versions ; retours du recruteur (volume, pertinence, faux positifs) |
| Candidats | `candidates`, `documents`, `call_notes` | texte de CV et fichier source chiffrés ; statut de traitement par document (file, extraction, analyse) |
| Preuves | `evidence`, `evidence_reviews` | une preuve = critère + extrait localisé + source + fiabilité + type (soutient / limite / contredit / plafonne) ; les revues (validée, rejetée, corrigée) sont des lignes à part |
| Évaluation | `assessments`, `qualifications` | une évaluation = version de grille + empreinte + résultat complet ; l'ancienne reste consultable (`prev_id`) |
| Apprentissage | `knowledge_entries`, `scoring_corrections`, `proposals` | enseignements à portée client tant que non validés ; corrections = cas de non-régression ; propositions de l'assistant en attente de confirmation |
| Traçabilité | `audit_events` | chaîne de hachage ; ne contient jamais de contenu de CV ni de note |

## 4. Pipelines

### 4.1 Mission → grille
`analyze_brief` extrait le travail réel, les modalités et les exigences (6 catégories) avec provenance, détecte l'écart titre ↔ travail et liste les informations manquantes sous forme de questions. `requirements.resolve` applique la hiérarchie des sources (impératif confirmé > précision validée > retour d'entretien > brief > description initiale ; une précision récente n'efface pas un impératif confirmé plus ancien sans remplacement explicite). `grid.propose_grid` répartit 100 points ; `grid.validate` refuse : somme ≠ 100, impératif retiré, éliminatoire sans confirmation client tracée, point « à clarifier » non reconnu. `freeze` calcule l'empreinte ; toute évaluation vérifie que la grille n'a pas changé (`assert_unchanged`).

### 4.2 Booléens
`search_plan.build_search_set` produit trois variantes d'un même besoin :

| Stratégie | Construction |
|---|---|
| Exploratoire | titres élargis ET union des trois technologies les plus discriminantes |
| Équilibrée | titres ET les K critères les plus discriminants |
| Stricte | tous les impératifs + précisions, au plus 5 groupes ; exigence « k parmi n » → requêtes complémentaires |

`fit` ramène chaque requête sous la limite de la plateforme dans cet ordre : synonymes → groupes secondaires → impératif le plus faible → titres en dernier ; l'explication dit ce qui a été sacrifié. `boolean.parse/validate` est un vrai parseur (AST) : parenthèses, priorité AND/OR (erreur `MIXED_PRECEDENCE`), opérateur orphelin, guillemets. `search_optimizer` reçoit le retour du recruteur (trop peu, trop de résultats, faux positifs, hors-sujet), pose un diagnostic, applique **une** modification expliquée selon une échelle fixe, et ne rejoue jamais une requête déjà utilisée ; quand l'échelle est épuisée il rend la main à un humain. Les exigences client ne sont jamais modifiées par cette boucle.

La limite de 250 caractères et la syntaxe sont des **paramètres de profil de plateforme** (`syntax_confirmed=False` pour Turnover : la syntaxe réelle reste à confirmer par un usage réel).

### 4.3 CV → évaluation
1. **Import** (`ingest_files`) : lot ≤ 20, taille/pages/min. de caractères configurables, détection de doublons par empreinte, noms de fichiers assainis.
2. **Extraction** (`cv_extract.extract_text`) : PDF, DOCX, TXT ; échecs *explicites et distincts* : fichier vide, trop gros, type non pris en charge, corrompu, chiffré, sans couche texte (scan), texte illisible, contenu insuffisant, trop de pages. Pas d'OCR dans cette version.
3. **Structuration** (`parse_cv`) : expériences avec dates, environnement technique, corps ; incohérences de dates signalées sans être corrigées.
4. **Preuves** (`evidence.evaluate_text_criterion`) : pour chaque critère de la grille, repérage des mentions, **lieu** (liste de compétences, ligne d'environnement, en-tête, corps d'expérience) et **signaux** (verbes d'action, rôle, profondeur, volumes chiffrés, résultats, livrables, contexte non professionnel). Règles : mention seule = « déclaré » ; commodité = pratique répétée exigée ; profondeur « avancée » = au moins deux indicateurs avancés ; volumétrie comparée par portée (débit par jour, jamais confondue d'un système à un autre) ; fenêtre de récence (7 ans par défaut, plus courte pour les technologies volatiles).
5. **Scoring** (`scoring.assess`) : points = poids × facteur (confirmé 1,0 · partiel 0,5 · déclaré 0,15 · non documenté 0 · contredit 0). Plafonds **uniquement sur absence explicite** (impératif contredit 60 ; éliminatoire contredit 35). « Potentiel » = borne haute comptant les critères déclarés/partiels, jamais une compétence acquise. Les contraintes (TJM, présence, astreinte, fuseau, langue, disponibilité) sont évaluées à part ; *inconnu ≠ incompatible*. La qualité d'information est une mesure distincte de l'adéquation ; la qualité de rédaction n'entre dans aucun score.
6. **Paliers** : très intéressant (≥ 96, aucun impératif ouvert), intéressant (≥ 80), à qualifier, écart majeur, adéquation faible, non évaluable. Seuils configurables.

### 4.4 Qualification et réévaluation
- `qualification.generate` : questions par critère non confirmé, personnalisées par l'extrait du CV, avec signaux d'approfondissement ; `is_generic` interdit les questions génériques (testé sur chaque modèle).
- `call_facts.extract_facts` : notes ou transcription → faits typés (locuteur, certitude, `client_requirement` vs `candidate_experience`, contraintes). Une transcription automatique est plafonnée à « partiel » tant qu'elle n'est pas validée.
- `reassess` : nouvelle évaluation, ancienne conservée, `diff_assessments` donne la raison de chaque changement de niveau ou de score.
- Une information du candidat qui *limite* son expérience n'altère jamais le besoin client.

### 4.5 Apprentissage
Un retour client propose un enseignement **spécifique au client** ; il ne devient universel qu'après validation humaine. Une correction du recruteur abaisse/relève un niveau, enregistre la nature de l'erreur (faux positif, faux négatif, mauvaise portée…) et devient un cas de non-régression ; aucune règle globale n'est modifiée automatiquement.

## 5. Traitement asynchrone
Les lots de CV sont traités par un pool de threads du processus (`worker_threads`), en mode `inline` pour les tests. Au démarrage, `requeue_stale` relance les documents restés « en cours » après un arrêt. **Limite connue :** ce n'est pas une file durable multi-instance ; pour un déploiement multi-instances il faut une vraie file de tâches (voir INTEGRATION.md).

## 6. Décisions et compromis assumés

| Décision | Raison | Coût |
|---|---|---|
| Moteur reconstruit plutôt que réutilisé depuis `comet-ai-office` | Le moteur existant a une grille fixe et des preuves par simple mot-clé (voir AUDIT_COMET.md) | Duplication à résorber en branchant l'un sur l'autre |
| Lexique de compétences interne (~80 entrées) | Alias, termes voisins, ombrelles « famille de fournisseurs », volumes typiques : indispensables aux booléens et aux preuves | À enrichir et à faire valider par les recruteurs ; une compétence absente du lexique est traitée comme texte libre |
| Pas de modèle de langage dans le scoring | Explicabilité, reproductibilité, absence de compétence inventée | Moins de souplesse sur formulations atypiques ; compensé par la désignation de passages optionnelle |
| JS natif sans build | Surface d'attaque et chaîne d'outils minimales ; CSP stricte `default-src 'self'` | Pas de framework de composants |
| SQLite pour dev/tests, PostgreSQL pour la production | Production refusée sur SQLite par la configuration | La suite complète n'est rejouée sur PostgreSQL que pour l'API et la collaboration |
