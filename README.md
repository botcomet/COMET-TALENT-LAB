# COMET Talent Lab

Copilote métier des Talent Managers Comet : **analyse de mission → sourcing booléen → matching multi-CV explicable → qualification → bibliothèque d'apprentissage**, avec des règles de recrutement Comet traduites en code, en vérifications et en tests.

> **État : première version fonctionnelle, non déployée, jamais exécutée sur des données réelles.**
> Tout ce qui est annoncé ci-dessous a été exécuté dans l'environnement de développement (voir [docs/RECETTE.md](docs/RECETTE.md) pour l'état exact, y compris ce qui n'est **pas** testé).
> Ce dépôt est **public** : il ne contient ni donnée candidat réelle, ni nom de client, ni secret. Les fixtures sont fictives et en noms de code.

## Ce que fait l'application

| Parcours | Ce qui est livré |
|---|---|
| **Mission** | Analyse d'un brief : travail réel vs titre, exigences classées en 6 catégories avec source/date/extrait, informations manquantes → questions (jamais inventées), exigence « k parmi n ». « Éliminatoire » exige une confirmation client tracée. |
| **Grille** | Grille propre à la mission (100 points), validée puis **figée** (empreinte SHA-256, infalsifiable) ; nouvelle version = motif + diff ; un impératif ne peut pas disparaître. |
| **Booléens** | Trois stratégies (exploratoire / équilibrée / stricte) ≤ 250 caractères (configurable), validation syntaxique, explications (titres, synonymes, exclusions, risques faux positifs/négatifs), boucle d'amélioration sur retour du recruteur — jamais la même requête, jamais de résultat simulé. |
| **Matching** | Import de 1 à 20 CV (PDF/DOCX/TXT), extraction avec échecs explicites, preuves localisées dans le texte, niveaux confirmé / partiel / déclaré / non documenté / contredit, score documenté + potentiel (borne haute), contraintes séparées, qualité d'information distincte de l'adéquation, aucun rejet automatique. |
| **Qualification** | Questions précises par critère et par CV, notes d'appel et transcriptions (faits extraits, exigence client ≠ compétence candidat), validation des preuves, réévaluation avec diff et raison de chaque changement. |
| **Bibliothèque / Coach** | Enseignements, spécifiques au client tant qu'ils ne sont pas validés ; corrections de recruteur → cas de non-régression ; coach qui explique le raisonnement sans classer les recruteurs. |
| **Collaboration** | Missions privées par défaut, partage à trois niveaux (stratégie / lecture / édition), historique, assistant qui **propose** et n'applique qu'après confirmation. |
| **Export** | Matière pour DT Editor (`comet.talentlab.dt-export/v1`) : anonymisée, fidèle au CV, sans technologie ajoutée, provenance interne séparée ; descriptif pour échange candidat (« Non communiqué » plutôt qu'inventé). |

L'application **n'exécute aucune recherche** (le recruteur copie la requête dans Turnover), **n'envoie aucun email**, et **ne rejette personne**.

## Démarrage (développement, données fictives uniquement)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                # + ".[postgres]" pour PostgreSQL
cp .env.example .env                       # facultatif : les valeurs par défaut conviennent au développement
uvicorn talentlab.main:app --reload        # http://127.0.0.1:8000  (docs API : /api/docs hors production)
```

En mode `dev`, l'écran de connexion propose des comptes **fictifs** (`*.demo@example.invalid`), sans mot de passe. Ce mode est **refusé par la configuration en production**.

**Ne téléversez pas de CV réels dans cet environnement.** Voir [docs/SECURITE_RGPD.md](docs/SECURITE_RGPD.md) pour les validations requises avant tout usage réel.

## Tests

```bash
pytest                                      # 161 tests (≈ 50 s) — SQLite
pytest -m business                          # 37 tests métier issus du référentiel
TALENTLAB_TEST_DATABASE_URL=postgresql+psycopg://… pytest tests/test_api_flow.py tests/test_collab_assistant.py   # rejoue l'API sur PostgreSQL
TALENTLAB_E2E_CHROME=/chemin/chrome pytest tests/test_e2e_browser.py                                              # parcours navigateur
```

Les tests de navigateur sont **ignorés avec message explicite** si aucun Chromium n'est trouvé. L'IA optionnelle n'est exercée qu'avec un faux fournisseur ; **le fournisseur Anthropic n'a jamais été appelé pour de vrai**.

## Organisation

```
talentlab/domain/    règles métier pures, sans E/S (booléens, grille, preuves, scoring, qualification…)
talentlab/services/  orchestration (missions, recherches, matching, export, bibliothèque, assistant)
talentlab/api/       FastAPI (schémas stricts, extra=forbid)
talentlab/llm/       fournisseur d'IA optionnel (désigne des passages ; ne note jamais)
frontend/            interface sans build (JS natif), contenu dynamique inséré en nœuds texte
tests/               tests de domaine, d'API, de collaboration et de navigateur ; fixtures fictives
docs/                audit, traçabilité méthodologique, architecture, intégration, sécurité, recette
```

## Documentation

- [docs/AUDIT_COMET.md](docs/AUDIT_COMET.md) — ressources Comet examinées, réutilisées, écartées, non vérifiables
- [docs/METHODOLOGIE_MAPPING.md](docs/METHODOLOGIE_MAPPING.md) — chaque règle du référentiel → code → test nommé
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — modèle de données, pipelines, décisions de conception
- [docs/INTEGRATION.md](docs/INTEGRATION.md) — branchements possibles (comet-ai-office, DT Editor, Turnover, Boond, SSO) et leur statut réel
- [docs/SECURITE_RGPD.md](docs/SECURITE_RGPD.md) — contrôles en place, points ouverts DPO / RGPD / AI Act
- [docs/RECETTE.md](docs/RECETTE.md) — état exact : ce qui est testé, ce qui ne l'est pas, critères d'acceptation

## Principes non négociables (appliqués en code)

1. Une mention n'est pas une démonstration ; l'absence de preuve n'est pas une compétence absente.
2. Le besoin client est la référence : un candidat ne le modifie pas, une technologie listée n'est pas éliminatoire par défaut.
3. Même grille, mêmes règles pour tous les candidats d'une mission ; changer la grille = nouvelle version tracée.
4. Aucun rejet automatique : un humain décide, avec un motif.
5. Toute justification est citable (extrait vérifiable) ou rétrogradée en hypothèse.
