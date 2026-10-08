"""Schémas d'entrée stricts : tout champ inconnu est refusé, toutes les longueurs sont bornées."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DevLogin(Strict):
    email: str = Field(max_length=255)


class MissionIn(Strict):
    client: str = Field(default="", max_length=200)
    title: str = Field(min_length=2, max_length=300)
    brief: str = Field(min_length=1, max_length=30_000)
    source_date: date | None = None
    author: str = Field(default="", max_length=200)


class SourceIn(Strict):
    kind: str = Field(max_length=40)
    text: str = Field(min_length=1, max_length=60_000)
    author: str = Field(default="", max_length=200)
    source_date: date | None = None
    label: str = Field(default="", max_length=200)


class RequirementIn(Strict):
    label: str = Field(min_length=1, max_length=200)
    category: Literal["eliminatoire_confirme", "imperatif", "fortement_differenciant", "souhaitable", "contextuel", "a_clarifier"] = "souhaitable"
    skill_key: str | None = Field(default=None, max_length=60)
    kind: str | None = Field(default=None, max_length=20)
    dimension: str | None = Field(default=None, max_length=30)
    terms: list[str] | None = Field(default=None, max_length=30)
    scope_terms: list[str] | None = Field(default=None, max_length=10)
    depth_required: Literal["practice", "advanced"] = "practice"
    min_years: float | None = Field(default=None, ge=0, le=50)
    source_kind: str = "brief_officiel"
    quote: str = Field(default="", max_length=400)


class RequirementPatch(Strict):
    label: str | None = Field(default=None, max_length=200)
    category: Literal["eliminatoire_confirme", "imperatif", "fortement_differenciant", "souhaitable", "contextuel", "a_clarifier"] | None = None
    dimension: str | None = Field(default=None, max_length=30)
    terms: list[str] | None = Field(default=None, max_length=30)
    scope_terms: list[str] | None = Field(default=None, max_length=10)
    depth_required: Literal["practice", "advanced"] | None = None
    min_years: float | None = Field(default=None, ge=0, le=50)
    recency_window_years: int | None = Field(default=None, ge=1, le=30)
    recency_sensitive: bool | None = None
    source_kind: str | None = Field(default=None, max_length=40)
    quote: str | None = Field(default=None, max_length=400)
    rationale: str | None = Field(default=None, max_length=500)
    status: Literal["active", "rejected"] | None = None


class ValidateReqs(Strict):
    ids: list[str] | None = Field(default=None, max_length=200)


class GridPatch(Strict):
    weights: dict[str, int] | None = None
    caps: dict[str, int | None] | None = None
    thresholds: dict[str, int] | None = None
    recency_window_years: int | None = Field(default=None, ge=1, le=30)


class GridFreeze(Strict):
    allow_unresolved_clarifications: bool = False


class GridNewVersion(Strict):
    reason: str = Field(min_length=5, max_length=500)


class CustomSearch(Strict):
    query: str = Field(min_length=1, max_length=2000)
    strategy: Literal["exploratory", "balanced", "strict"] = "balanced"


class FeedbackIn(Strict):
    result_count: int | None = Field(default=None, ge=0, le=10_000_000)
    relevance: Literal["bonne", "partielle", "mauvaise"] | None = None
    tags: list[Literal["trop_juniors", "mauvaise_expertise", "competence_absente", "faux_positifs_recurrents", "bons_profils_manquants", "autre"]] = Field(default_factory=list, max_length=6)
    false_positive_terms: list[str] = Field(default_factory=list, max_length=10)
    missing_skill: str = Field(default="", max_length=200)
    missing_profiles_note: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=2000)

    @field_validator("false_positive_terms")
    @classmethod
    def _short(cls, v: list[str]) -> list[str]:
        return [t[:60] for t in v]


class SaveSearch(Strict):
    status: Literal["draft", "saved", "useful"] = "saved"
    note: str = Field(default="", max_length=2000)


class BooleanCheck(Strict):
    query: str = Field(max_length=5000)


class CandidatePatch(Strict):
    acronym: str | None = Field(default=None, max_length=10, pattern=r"^[A-Za-zÀ-ÿ]{0,10}$")
    label: str | None = Field(default=None, max_length=120)


class StatusIn(Strict):
    status: str = Field(max_length=20)
    comment: str = Field(default="", max_length=1000)


class NoteIn(Strict):
    kind: Literal["candidate_call_note", "transcript", "interview_report", "complementary_doc"] = "candidate_call_note"
    text: str = Field(min_length=1, max_length=100_000)
    auto_generated: bool = False
    speaker_map: dict[str, str] = Field(default_factory=dict)
    note_date: date | None = None


class BriefNoteIn(Strict):
    kind: Literal["client_brief_note", "client_feedback"] = "client_brief_note"
    text: str = Field(min_length=1, max_length=100_000)
    author: str = Field(default="", max_length=200)
    speaker_map: dict[str, str] = Field(default_factory=dict)


class ReviewIn(Strict):
    decision: Literal["validated", "rejected"]
    note: str = Field(default="", max_length=1000)


class CorrectionIn(Strict):
    assessment_id: str = Field(max_length=32)
    criterion_key: str = Field(max_length=120)
    new_level: Literal["confirme_demontre", "partiellement_demontre", "declare_sans_preuve", "non_documente", "contredit"]
    error_nature: str = Field(max_length=40)
    comment: str = Field(min_length=5, max_length=1000)


class CompareIn(Strict):
    candidate_ids: list[str] = Field(min_length=2, max_length=8)


class ReassessIn(Strict):
    reason: str = Field(default="Réévaluation demandée", max_length=300)


class ShareIn(Strict):
    email: str = Field(max_length=255)
    scope: Literal["strategie", "lecture", "edition"]


class AssistantIn(Strict):
    message: str = Field(min_length=1, max_length=3000)
    candidate_ids: list[str] = Field(default_factory=list, max_length=8)
    search_id: str | None = Field(default=None, max_length=32)


class CoachIn(Strict):
    question: str = Field(min_length=3, max_length=2000)
    mission_id: str | None = Field(default=None, max_length=32)
    search_id: str | None = Field(default=None, max_length=32)
    candidate_id: str | None = Field(default=None, max_length=32)
    criterion_key: str | None = Field(default=None, max_length=120)


class KnowledgeIn(Strict):
    kind: str = Field(max_length=30)
    title: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=10, max_length=8000)
    role_family: str = Field(default="", max_length=60)
    tags: list[str] = Field(default_factory=list, max_length=15)
    mission_id: str | None = Field(default=None, max_length=32)


class KnowledgePatch(Strict):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=8000)
    role_family: str | None = Field(default=None, max_length=60)
    tags: list[str] | None = Field(default=None, max_length=15)


class PublishIn(Strict):
    generalize: bool = False


class UserIn(Strict):
    email: str = Field(max_length=255)
    display_name: str = Field(min_length=2, max_length=120)
    role: Literal["talent_manager", "pilote", "admin"] = "talent_manager"
    region: str = Field(default="", max_length=80)
