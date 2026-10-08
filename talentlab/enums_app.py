"""Constantes applicatives partagées (niveaux d'accès, statuts)."""
ACCESS_RANK = {"strategie": 1, "lecture": 2, "edition": 3, "owner": 4}

CANDIDATE_STATUSES = ("a_evaluer", "a_qualifier", "qualifie", "positionne", "ecarte")
# « écarté » est une décision HUMAINE motivée : le moteur ne rejette jamais automatiquement (§25).

# Sources du besoin (rang d'autorité, voir domain.enums.SOURCE_RANK)
MISSION_SOURCE_KINDS = (
    "brief_officiel", "description_initiale", "client_precision_validee", "client_imperatif_confirme", "retour_entretien",
    "note_brief_client",
)
# Sources relatives aux candidats : invisibles d'un collègue qui n'a reçu que la stratégie de sourcing (test 15).
CANDIDATE_SOURCE_KINDS = ("note_appel_candidat", "transcription", "compte_rendu_entretien")
