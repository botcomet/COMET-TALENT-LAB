"""Modèle de données (§23). Les structures riches sont stockées en JSON ; les clés de recherche restent relationnelles."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, EncBytes, EncJSON, EncText


def uid() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="talent_manager")
    team: Mapped[str] = mapped_column(String(80), default="Talent Management")
    region: Mapped[str] = mapped_column(String(80), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_epoch: Mapped[int] = mapped_column(Integer, default=0)       # incrémenté à la déconnexion : les anciens cookies deviennent invalides
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Mission(Base):
    __tablename__ = "missions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="active")
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)       # modalités, alertes, informations manquantes
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    retention_until: Mapped[date | None] = mapped_column(Date, nullable=True)


class MissionSource(Base):
    """Source immuable du besoin : brief, précision client, note de brief, retour d'entretien…"""
    __tablename__ = "mission_sources"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(EncText, default="")
    author: Mapped[str] = mapped_column(EncText, default="")                   # nom d'un contact client : chiffré
    source_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    text: Mapped[str] = mapped_column(EncText)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    data: Mapped[dict[str, Any]] = mapped_column(EncJSON)                       # extraits du brief et nom du contact client : chiffrés
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Grid(Base):
    __tablename__ = "grids"
    __table_args__ = (UniqueConstraint("mission_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="draft")
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validated_by: Mapped[str] = mapped_column(String(32), default="")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Share(Base):
    __tablename__ = "shares"
    __table_args__ = (UniqueConstraint("mission_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(20))
    granted_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Search(Base):
    __tablename__ = "searches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(20))
    lineage: Mapped[str] = mapped_column(String(32), index=True)            # suite de versions d'une même recherche
    version: Mapped[int] = mapped_column(Integer, default=1)
    platform: Mapped[str] = mapped_column(String(40), default="turnover")
    query: Mapped[str] = mapped_column(Text)
    variant: Mapped[dict[str, Any]] = mapped_column(JSON)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON)
    grid_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    modification: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(12), default="draft")          # draft | saved | useful
    note: Mapped[str] = mapped_column(EncText, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SearchFeedback(Base):
    __tablename__ = "search_feedbacks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    search_id: Mapped[str] = mapped_column(ForeignKey("searches.id", ondelete="CASCADE"), index=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relevance: Mapped[str | None] = mapped_column(String(12), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    false_positive_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_skill: Mapped[str] = mapped_column(String(200), default="")
    missing_note: Mapped[str] = mapped_column(EncText, default="")
    notes: Mapped[str] = mapped_column(EncText, default="")
    diagnosis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resulting_search_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(20))
    acronym: Mapped[str] = mapped_column(String(10), default="")                # acronyme autorisé pour les dossiers (jamais le nom)
    label: Mapped[str] = mapped_column(EncText, default="")                     # libellé interne (dérivé du nom de fichier) : chiffré
    status: Mapped[str] = mapped_column(String(20), default="a_evaluer")
    status_comment: Mapped[str] = mapped_column(EncText, default="")
    status_by: Mapped[str] = mapped_column(String(32), default="")
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_mission_text", "mission_id", "sha256_text"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(EncText)                              # un nom de fichier contient souvent le nom du candidat : chiffré
    sha256_file: Mapped[str] = mapped_column(String(64))
    sha256_text: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(100), default="")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(14), default="queued")           # queued | processing | done | failed | duplicate
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(60), default="en attente")
    error_code: Mapped[str] = mapped_column(String(40), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    extraction_quality: Mapped[str] = mapped_column(String(10), default="")
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    security_flags: Mapped[list[dict[str, Any]]] = mapped_column(EncJSON, default=list)     # contient des extraits du CV : chiffré
    duplicate_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    blob: Mapped[bytes | None] = mapped_column(EncBytes, nullable=True, deferred=True)       # différés : lister les documents ne lit ni ne déchiffre les fichiers
    text: Mapped[str | None] = mapped_column(EncText, nullable=True, deferred=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)                                # tentatives de traitement (boucle de plantage au redémarrage)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class CallNote(Base):
    __tablename__ = "call_notes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(EncText)
    speaker_map: Mapped[dict[str, str]] = mapped_column(EncJSON, default=dict)
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    note_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Evidence(Base):
    """Preuve append-only : son contenu ne change jamais ; sa validation vit dans EvidenceReview."""
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    note_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_key: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    source: Mapped[str] = mapped_column(String(40))
    reliability: Mapped[str] = mapped_column(String(30))
    level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    excerpt: Mapped[str] = mapped_column(EncText)
    speaker: Mapped[str] = mapped_column(EncText, default="")
    speaker_role: Mapped[str] = mapped_column(String(30), default="")
    certainty: Mapped[str] = mapped_column(String(10), default="certain")
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    why_verify: Mapped[str] = mapped_column(Text, default="")
    source_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    corrects_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(EncJSON, nullable=True)      # valeur structurée d'une contrainte (TJM, disponibilité…)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvidenceReview(Base):
    __tablename__ = "evidence_reviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(12))                       # validated | rejected
    note: Mapped[str] = mapped_column(EncText, default="")
    reviewer_id: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    grid_id: Mapped[str] = mapped_column(ForeignKey("grids.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(EncText, default="")
    result: Mapped[dict[str, Any]] = mapped_column(EncJSON)
    diff: Mapped[dict[str, Any] | None] = mapped_column(EncJSON, nullable=True)
    score_documented: Mapped[float] = mapped_column(Float)
    score_potential: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(24))
    prev_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Qualification(Base):
    __tablename__ = "qualifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    assessment_id: Mapped[str] = mapped_column(String(32))
    questions: Mapped[dict[str, Any]] = mapped_column(EncJSON)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Proposal(Base):
    """Modification proposée (assistant, apprentissage) : rien n'est appliqué avant confirmation d'un utilisateur autorisé (§22)."""
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(EncJSON)                    # contient un extrait de compte rendu : chiffré, effacé avec le candidat
    explanation: Mapped[str] = mapped_column(EncText)
    candidate_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)      # candidat dont la note a produit la proposition (sans FK : effacement ciblé)
    consequences: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="pending")      # pending | confirmed | rejected
    created_by: Mapped[str] = mapped_column(String(32))
    decided_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    role_family: Mapped[str] = mapped_column(String(60), default="", index=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    client_specific: Mapped[bool] = mapped_column(Boolean, default=True)    # une exigence propre à un client n'est jamais une règle universelle
    status: Mapped[str] = mapped_column(String(12), default="proposed")     # proposed | published | retired
    origin_mission_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    author_id: Mapped[str] = mapped_column(String(32))
    validated_by: Mapped[str] = mapped_column(String(32), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ScoringCorrection(Base):
    __tablename__ = "scoring_corrections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    assessment_id: Mapped[str] = mapped_column(String(32))
    candidate_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)       # sans FK : le cas survit, ses extraits non (voir redact_corrections)
    criterion_key: Mapped[str] = mapped_column(String(120))
    old_level: Mapped[str] = mapped_column(String(30))
    new_level: Mapped[str] = mapped_column(String(30))
    error_nature: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str] = mapped_column(EncText, default="")
    regression_case: Mapped[dict[str, Any]] = mapped_column(EncJSON)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    """Journal chaîné par hachage : toute altération a posteriori est détectable."""
    __tablename__ = "audit_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user_id: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    mission_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")
