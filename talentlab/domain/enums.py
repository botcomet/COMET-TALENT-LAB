"""Vocabulaire métier de COMET Talent Lab.

Chaque énumération reprend, mot pour mot, une classification du référentiel
Comet (sections 4.2, 4.3, 6.6 et 7). Ne pas en ajouter sans validation métier.
"""
from __future__ import annotations

from enum import Enum


class Category(str, Enum):
    """§4.2 — classification obligatoire de chaque élément du besoin."""

    ELIMINATOIRE = "eliminatoire_confirme"   # explicitement confirmé par le client
    IMPERATIF = "imperatif"
    DIFFERENCIANT = "fortement_differenciant"
    SOUHAITABLE = "souhaitable"
    CONTEXTUEL = "contextuel"                # information contextuelle, non notée
    A_CLARIFIER = "a_clarifier"              # à clarifier avec le client, non noté


# Catégories qui portent un poids dans la grille de scoring.
SCORED_CATEGORIES = (
    Category.ELIMINATOIRE,
    Category.IMPERATIF,
    Category.DIFFERENCIANT,
    Category.SOUHAITABLE,
)
# Catégories dont l'absence doit empêcher de présenter le candidat comme satisfaisant.
MANDATORY_CATEGORIES = (Category.ELIMINATOIRE, Category.IMPERATIF)


class SourceKind(str, Enum):
    """§4.3 — provenance d'une exigence du poste, du plus au moins prioritaire."""

    CLIENT_CONFIRMED_IMPERATIVE = "client_imperatif_confirme"   # rang 1
    CLIENT_CLARIFICATION = "client_precision_validee"           # rang 2
    INTERVIEW_FEEDBACK = "retour_entretien"                      # rang 3
    OFFICIAL_BRIEF = "brief_officiel"                            # rang 4 (AO / brief)
    INITIAL_DESCRIPTION = "description_initiale"                 # rang 5


SOURCE_RANK = {
    SourceKind.CLIENT_CONFIRMED_IMPERATIVE: 1,
    SourceKind.CLIENT_CLARIFICATION: 2,
    SourceKind.INTERVIEW_FEEDBACK: 3,
    SourceKind.OFFICIAL_BRIEF: 4,
    SourceKind.INITIAL_DESCRIPTION: 5,
}


class Level(str, Enum):
    """§6.6 — niveau de preuve d'un critère. « non documenté » ≠ « contredit »."""

    CONFIRMED = "confirme_demontre"
    PARTIAL = "partiellement_demontre"
    DECLARED = "declare_sans_preuve"
    NOT_DOCUMENTED = "non_documente"
    CONTRADICTED = "contredit"


LEVEL_ORDER = {
    Level.CONTRADICTED: 0,
    Level.NOT_DOCUMENTED: 1,
    Level.DECLARED: 2,
    Level.PARTIAL: 3,
    Level.CONFIRMED: 4,
}


class EvidenceSource(str, Enum):
    CV_DOCUMENT = "cv"
    CALL_NOTE = "note_appel_candidat"
    CLIENT_BRIEF_NOTE = "note_brief_client"
    TRANSCRIPT = "transcription"
    INTERVIEW_REPORT = "compte_rendu_entretien"
    CLIENT_FEEDBACK = "feedback_client"
    COMPLEMENTARY_DOC = "document_complementaire"
    RECRUITER_INPUT = "saisie_recruteur"


class Reliability(str, Enum):
    """§4.3 — nature de l'information sur les compétences du candidat."""

    DOCUMENTED_CV = "documente_cv"
    EXPLAINED_IN_CALL = "explique_en_appel"
    CONFIRMED_IN_INTERVIEW = "confirme_en_entretien"
    COMPLEMENTARY_DOC = "document_complementaire"
    UNSUPPORTED_CLAIM = "declaration_non_etayee"
    HYPOTHESIS = "hypothese"
    CONTRADICTION = "contradiction"


class EvidenceKind(str, Enum):
    SUPPORTS = "supporte"        # étaye le critère
    LIMITS = "limite"            # borne l'expérience (« que sur deux topics »)
    CONTRADICTS = "contredit"    # démontre l'absence du niveau attendu


class Tier(str, Enum):
    """Présentation d'un candidat — jamais un verdict de rejet (§6.9, §25)."""

    VERY_INTERESTING = "tres_interessant"
    INTERESTING = "interessant"
    TO_QUALIFY = "a_qualifier"
    MAJOR_GAP = "ecart_majeur"
    LOW_FIT = "adequation_faible"
    NOT_ASSESSABLE = "non_evaluable"


class ConstraintStatus(str, Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"       # refus explicite uniquement
    UNKNOWN = "inconnu"                 # → question, jamais « incompatible »
    TO_VALIDATE = "a_valider"


class Role(str, Enum):
    TALENT_MANAGER = "talent_manager"
    LEAD = "pilote"
    ADMIN = "admin"


class ShareScope(str, Enum):
    STRATEGY = "strategie"      # recherches + explications, aucune donnée candidat
    READ = "lecture"            # mission, grille, évaluations
    EDIT = "edition"
