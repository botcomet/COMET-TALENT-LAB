"""Exigences du poste : classification, provenance et résolution des conflits (§4).

Principe : les exigences du poste viennent du client (ou de ses représentants
autorisés), jamais du candidat ni des résultats de recherche (§5.7).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any

from .enums import Category, SourceKind, SOURCE_RANK, SCORED_CATEGORIES


def new_id() -> str:
    return uuid.uuid4().hex


# Dimensions évaluables (§6.4). « contrainte » n'est jamais notée par défaut (§15.8).
DIMENSIONS = (
    "technique", "responsabilite", "seniorite", "contexte_metier",
    "methodologie", "livrable", "contrainte", "posture",
)
KINDS = ("skill", "activity", "domain", "years", "constraint", "language", "certification", "custom")


@dataclass
class Req:
    id: str
    key: str                                  # clé canonique (ex. "skill:kafka")
    label: str
    category: Category
    dimension: str = "technique"
    kind: str = "skill"
    terms: list[str] = field(default_factory=list)
    scope_terms: list[str] = field(default_factory=list)
    depth_required: str = "practice"          # "practice" | "advanced"
    min_years: float | None = None
    recency_window_years: int | None = None
    recency_sensitive: bool | None = None     # None → défaut de configuration
    params: dict[str, Any] = field(default_factory=dict)   # valeurs structurées (jours sur site…)
    source_kind: SourceKind = SourceKind.OFFICIAL_BRIEF
    source_ref: str = ""                      # identifiant/libellé de la source
    source_date: date | None = None
    source_author: str = ""
    quote: str = ""                           # extrait de la source qui justifie l'exigence
    rationale: str = ""                       # pourquoi cette catégorie a été proposée
    clarification_question: str = ""
    supersedes: str | None = None             # id de l'exigence explicitement remplacée
    validated: bool = False
    validated_by: str = ""
    status: str = "active"                    # active | superseded | rejected
    proposed_by: str = "system"

    @property
    def rank(self) -> int:
        return SOURCE_RANK[self.source_kind]

    @property
    def scored(self) -> bool:
        return self.category in SCORED_CATEGORIES and self.dimension != "contrainte"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        d["source_kind"] = self.source_kind.value
        d["source_date"] = self.source_date.isoformat() if self.source_date else None
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Req":
        d = dict(d)
        d["category"] = Category(d["category"])
        d["source_kind"] = SourceKind(d.get("source_kind", SourceKind.OFFICIAL_BRIEF.value))
        sd = d.get("source_date")
        d["source_date"] = date.fromisoformat(sd) if isinstance(sd, str) and sd else None
        valid = {f for f in Req.__dataclass_fields__}
        return Req(**{k: v for k, v in d.items() if k in valid})


@dataclass
class Conflict:
    key: str
    kept: str                 # id retenu
    overridden: list[str]     # ids écartés (ou en conflit)
    reason: str
    needs_review: bool


def resolve(reqs: list[Req]) -> tuple[list[Req], list[Conflict]]:
    """Détermine les exigences effectives à partir de sources potentiellement divergentes.

    Ordre (§4.3) : impératif client confirmé > dernière précision client validée >
    retour d'entretien > brief officiel > description initiale, en tenant compte
    de la date et d'un remplacement *explicite*. Une précision récente
    n'efface jamais arbitrairement un impératif confirmé plus ancien.
    """
    live = [r for r in reqs if r.status == "active"]
    by_id = {r.id: r for r in live}
    # 1) remplacements explicites — uniquement par une source d'autorité client (rang ≤ 2)
    explicit_over: dict[str, str] = {}
    for r in live:
        if r.supersedes and r.supersedes in by_id and r.rank <= 2:
            explicit_over[r.supersedes] = r.id
    conflicts: list[Conflict] = []
    groups: dict[str, list[Req]] = {}
    for r in live:
        if r.id in explicit_over:
            conflicts.append(Conflict(r.key, explicit_over[r.id], [r.id],
                                      "Remplacée explicitement par une précision de rang client.", False))
            continue
        groups.setdefault(r.key, []).append(r)
    effective: list[Req] = []
    for key, items in groups.items():
        if len(items) == 1:
            effective.append(items[0])
            continue
        items.sort(key=lambda r: (r.rank, -(r.source_date.toordinal() if r.source_date else 0)))
        kept = items[0]
        others = items[1:]
        effective.append(kept)
        divergent = [o for o in others if o.category != kept.category]
        if divergent:
            newer_low_authority = [o for o in divergent
                                   if o.source_date and kept.source_date and o.source_date > kept.source_date]
            conflicts.append(Conflict(
                key, kept.id, [o.id for o in divergent],
                "Sources divergentes : la source de plus haute autorité est conservée"
                + (" ; une source moins prioritaire est plus récente → arbitrage recruteur nécessaire." if newer_low_authority else "."),
                needs_review=bool(newer_low_authority),
            ))
    return effective, conflicts
