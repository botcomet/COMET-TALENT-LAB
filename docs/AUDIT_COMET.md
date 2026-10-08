# Audit des ressources Comet — 8 octobre 2026

Ce document dit **ce qui a réellement été examiné**, **ce qui a été réutilisé**, **ce qui ne l'a pas été et pourquoi**, et **ce qui n'a pas pu être vérifié**. Aucune intégration n'est présentée comme opérationnelle sans l'avoir été.

## 1. Accès réellement disponibles dans la session

| Ressource | État constaté | Utilisé ? |
|---|---|---|
| Dépôt cible `botcomet/COMET-TALENT-LAB` | Vide à l'origine ; **public** | Oui (livraison) |
| `commando-ai-comet/comet-ai-office` | Lisible (clone en lecture seule, profondeur 1) | Oui — audit du code et de la doc |
| `commando-ai-comet/constellation` | Lisible (clone en lecture seule) | Oui — conventions d'identité visuelle et de passerelle |
| Autres dépôts `commando-ai-comet/*` (pilotage_FA, comet-pulse, deal-desk, boond-calculator, ci-workflows, security-audit-pilot…) | Listés par la session, **non ouverts** : leur nom ne désigne pas un besoin de cette mission | Non |
| Connecteur BigQuery (réplica Boond ?) | Connecté, mais **aucun `projectId` configuré** (`gcloud config` vide) : je n'en ai pas deviné un | Non — aucune requête |
| Connecteurs Drive, Notion, Slack, Granola, Noota, Boond sandbox | **Non connectés** | Non |
| `commando-ai-comet/starter-pack` (cité par AGENTS.md) | **Non visible** dans la session | Non |
| DT Editor | **Introuvable** dans les dépôts accessibles | Non |
| Comptes rendus « Vie ma vie » (Marine) | **Inaccessibles** (Drive/Notion non connectés) | Non |
| SSO / annuaire Comet réel | **Non vérifiable** depuis la session | Non (passerelle prévue, non testée contre le vrai SSO) |

Le dépôt de travail étant public, **aucun code, aucune donnée et aucun nom de client** issus des dépôts privés n'a été copié. Les fixtures de test utilisent des noms de code (TLJ, DSF, QAT, DQM, GTS, F5M, CYB, INF, WMS) et des candidats entièrement fictifs.

## 2. Ce que `comet-ai-office` fait déjà

Application React 19 / Three.js / Vinext sur Cloudflare Workers (D1, R2), IA via OpenAI Responses. Parcours : brief → recherche Turn → score « Paola » → qualification → dossier → entretiens. Elle contient notamment :

- un **connecteur Boond** direct (API) et la prise en compte de l'identité de mission ;
- un **adaptateur Turn préparé mais désactivé** (`TURN_API_NOT_CONFIGURED`), avec réservation atomique de crédits ;
- une extraction PDF/DOCX et un coffre de secrets chiffrés (AES-GCM) ;
- des garde-fous métier documentés dans son `CLAUDE.md` (« ne jamais transformer une absence de preuve en compétence confirmée », TJM/localisation séparés de l'adéquation technique, etc.).

Sa propre doc de reprise indique que Boond/BigQuery et Turn restent à brancher, que l'orchestration serveur durable est à faire, et que la recette historique utilise des fournisseurs simulés.

## 3. Pourquoi son moteur de scoring et de booléens n'a pas été réutilisé

Lecture de `lib/recruiting.ts` (`analyzeCandidate`, `buildBooleanStrategies`) :

| Exigence du référentiel Comet | Constat dans le code existant |
|---|---|
| Grille propre à chaque mission, jamais universelle (§6.2) | Grille **fixe** : 15 / 15 / 25 / 22 / 8 / 8 / 7 points pour toutes les missions |
| Mention ≠ démonstration (§6.5) | Une compétence est « prouvée » dès qu'un alias figure dans le texte ; la confiance de la preuve est codée en dur à `"high"` |
| Niveaux de preuve explicites (§6.6) | Absents ; une seule valeur : correspondance de mot-clé |
| Impératifs non compensables, plafond configurable (§6.8) | Plafond **codé en dur** à 89 si un impératif manque |
| Pas de NOT systématique (§5.3 F) | `NOT ("helpdesk" OR "support N1")` ajouté à chaque requête |
| Trois stratégies distinctes (§5.4) | Trois variantes nommées « Précise A/B/C », toutes en AND de quatre compétences |
| Explication des choix, risques de faux positifs/négatifs (§5.3 H) | Absente |
| Boucle d'amélioration sans répétition (§5.6) | Rotation de mots-clés ; pas de diagnostic ni de contrôle de répétition |
| Provenance et version des informations (§4.3) | Faits « validés » en texte libre |

**Conclusion** : le cœur méthodologique ne pouvait pas être réutilisé sans contredire le référentiel ; il a été reconstruit comme un moteur indépendant, déterministe et testé. En revanche, **rien de ce qu'il fait bien n'a été reconstruit** :

- l'exécution des recherches sur Turn, la gestion des crédits Turn, la lecture de Boond, la rédaction des dossiers (DT Editor), l'expérience 3D et l'envoi de mails restent **dans leurs outils** ;
- Talent Lab n'exécute **aucune** recherche (le recruteur copie la requête, l'exécute, saisit le résultat) et n'envoie **aucun** email (§3, §19.4).

## 4. Articulation proposée (non réalisée en V1)

Voir [INTEGRATION.md](INTEGRATION.md). En bref : Talent Lab peut devenir le moteur de scoring et de requêtes de `comet-ai-office` (remplacement de `analyzeCandidate` / `buildBooleanStrategies` par appels API), et alimenter DT Editor via l'export JSON `comet.talentlab.dt-export/v1`. Ces deux branchements demandent une décision du responsable technique et une recette sur données autorisées.

## 5. Points d'attention relevés dans l'écosystème

- `AGENTS.md` des dépôts Comet impose branches courtes, PR vers `main`, contrôle `security / Security gate` (Gitleaks, npm audit, Semgrep) et propriété Tech des workflows. **Talent Lab (Python) n'est pas couvert** par l'adaptateur de dépendances npm décrit dans `SECURITE-CI.md` : un adaptateur Python devra être demandé à Tech.
- L'en-tête d'identité injecté par la plateforme d'origine de `comet-ai-office` n'est fiable que derrière sa frontière d'authentification. Talent Lab applique le même principe : en-tête d'identité **+ secret partagé** comparé en temps constant, utilisateur pré-provisionné.
