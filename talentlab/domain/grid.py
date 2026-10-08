"""Grille de scoring propre à chaque mission, figée pendant la comparaison (§6.2, §6.3).

- Aucune grille universelle : les critères découlent des exigences réelles.
- Les poids des critères notés totalisent exactement 100 points et sont validés
  par un recruteur.
- Une fois figée, une grille est immuable : tout changement du besoin crée une
  *nouvelle version* (jamais une modification silencieuse entre deux candidats).
- Une exigence impérative / éliminatoire ne peut pas disparaître de la grille
  parce que peu de profils la satisfont (§5.7).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any

from .enums import Category, Level, MANDATORY_CATEGORIES, SCORED_CATEGORIES, SourceKind
from .requirements import Req

# Paramètres par défaut — copiés dans chaque grille, donc figés avec elle (§6.9 : configurables).
DEFAULT_FACTORS: dict[str, float] = {
    Level.CONFIRMED.value: 1.0,
    Level.PARTIAL.value: 0.5,
    Level.DECLARED.value: 0.15,       # mention sans preuve : crédit symbolique, jamais un acquis
    Level.NOT_DOCUMENTED.value: 0.0,
    Level.CONTRADICTED.value: 0.0,
}
DEFAULT_CAPS: dict[str, int | None] = {
    # plafonds appliqués seulement sur une ABSENCE EXPLICITE (§6.8) ; un manque « non documenté »
    # déclenche une qualification prioritaire, pas un faux verdict.
    "contradicted_imperative": 60,
    "contradicted_eliminatory": 35,
    "undocumented_imperative": None,
}
DEFAULT_THRESHOLDS: dict[str, int] = {
    "very_interesting": 96,
    "work_view_min": 95,             # vue de travail exigeante ; les autres profils restent consultables
    "interesting": 80,
    "low_fit": 50,
}
DEFAULT_BASE_WEIGHTS = {
    Category.ELIMINATOIRE.value: 6,
    Category.IMPERATIF.value: 5,
    Category.DIFFERENCIANT.value: 3,
    Category.SOUHAITABLE.value: 1,
}
DEFAULT_RECENCY_WINDOW_YEARS = 7


@dataclass
class Criterion:
    req_id: str
    key: str
    label: str
    category: str
    dimension: str
    kind: str
    weight: int = 0
    scored: bool = True
    terms: list[str] = field(default_factory=list)
    scope_terms: list[str] = field(default_factory=list)
    depth_required: str = "practice"
    min_years: float | None = None
    recency_window_years: int | None = None
    recency_sensitive: bool | None = None
    params: dict[str, Any] = field(default_factory=dict)
    members: list[str] = field(default_factory=list)       # clés des membres d'un groupe
    source_kind: str = ""
    quote: str = ""

    @property
    def mandatory(self) -> bool:
        return self.category in {c.value for c in MANDATORY_CATEGORIES}


@dataclass
class Grid:
    mission_id: str
    version: int
    criteria: list[Criterion]
    factors: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FACTORS))
    caps: dict[str, int | None] = field(default_factory=lambda: dict(DEFAULT_CAPS))
    thresholds: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    recency_window_years: int = DEFAULT_RECENCY_WINDOW_YEARS
    reason: str = ""
    status: str = "draft"          # draft | frozen
    validated_by: str = ""
    content_hash: str = ""

    # --- accès
    def scored(self) -> list[Criterion]:
        return [c for c in self.criteria if c.scored]

    def constraints(self) -> list[Criterion]:
        return [c for c in self.criteria if c.dimension == "contrainte"]

    def by_key(self, key: str) -> Criterion | None:
        return next((c for c in self.criteria if c.key == key), None)

    def total_weight(self) -> int:
        return sum(c.weight for c in self.scored())

    # --- sérialisation canonique
    def canonical(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "criteria": [asdict(c) for c in self.criteria],
            "factors": self.factors, "caps": self.caps, "thresholds": self.thresholds,
            "recency_window_years": self.recency_window_years,
        }

    def compute_hash(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = self.canonical()
        d.update({"mission_id": self.mission_id, "reason": self.reason, "status": self.status,
                  "validated_by": self.validated_by, "content_hash": self.content_hash,
                  "total_weight": self.total_weight()})
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Grid":
        crit = [Criterion(**{k: v for k, v in c.items() if k in Criterion.__dataclass_fields__}) for c in d["criteria"]]
        return Grid(
            mission_id=d.get("mission_id", ""), version=d["version"], criteria=crit,
            factors=d.get("factors") or dict(DEFAULT_FACTORS), caps=d.get("caps") or dict(DEFAULT_CAPS),
            thresholds=d.get("thresholds") or dict(DEFAULT_THRESHOLDS),
            recency_window_years=d.get("recency_window_years", DEFAULT_RECENCY_WINDOW_YEARS),
            reason=d.get("reason", ""), status=d.get("status", "draft"),
            validated_by=d.get("validated_by", ""), content_hash=d.get("content_hash", ""),
        )


class GridError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _largest_remainder(raw: list[float], total: int = 100, floor: int = 1) -> list[int]:
    if not raw:
        return []
    s = sum(raw) or 1.0
    exact = [r / s * total for r in raw]
    base = [max(floor, int(e)) for e in exact]
    diff = total - sum(base)
    order = sorted(range(len(raw)), key=lambda i: exact[i] - int(exact[i]), reverse=True)
    i = 0
    while diff != 0 and order:
        idx = order[i % len(order)] if diff > 0 else order[-1 - (i % len(order))]
        if diff > 0:
            base[idx] += 1
            diff -= 1
        elif base[idx] > floor:
            base[idx] -= 1
            diff += 1
        i += 1
        if i > 10_000:
            break
    return base


def criterion_from_req(r: Req) -> Criterion:
    return Criterion(
        req_id=r.id, key=r.key, label=r.label, category=r.category.value, dimension=r.dimension, kind=r.kind,
        terms=list(r.terms), scope_terms=list(r.scope_terms), depth_required=r.depth_required, min_years=r.min_years,
        recency_window_years=r.recency_window_years, recency_sensitive=r.recency_sensitive, params=dict(r.params),
        members=list(r.params.get("members", [])), source_kind=r.source_kind.value, quote=r.quote,
    )


def propose_grid(mission_id: str, effective: list[Req], *, version: int = 1, reason: str = "Grille initiale proposée à partir des exigences effectives") -> Grid:
    """Propose une répartition des poids ; le recruteur ajuste puis valide.

    Les contraintes (présence, TJM, astreintes…) et les éléments contextuels /
    à clarifier ne sont pas notés (§4.2, §15.8). Les membres d'un groupe imposé
    sont évalués individuellement mais notés via le groupe.
    """
    crits: list[Criterion] = []
    for r in effective:
        c = criterion_from_req(r)
        member_of_group = bool(r.params.get("group"))
        c.scored = r.category in SCORED_CATEGORIES and r.dimension != "contrainte" and not member_of_group
        crits.append(c)
    scored = [c for c in crits if c.scored]
    ws = _largest_remainder([float(DEFAULT_BASE_WEIGHTS[c.category]) for c in scored])
    for c, w in zip(scored, ws):
        c.weight = w
    return Grid(mission_id=mission_id, version=version, criteria=crits, reason=reason)


def validate(grid: Grid, effective: list[Req], *, allow_unresolved_clarifications: bool = False) -> tuple[list[str], list[str]]:
    """(erreurs bloquantes, avertissements). Une grille n'est figeable que sans erreur."""
    errors: list[str] = []
    warnings: list[str] = []
    eff_by_id = {r.id: r for r in effective}
    in_grid = {c.req_id for c in grid.criteria}

    if grid.total_weight() != 100:
        errors.append(f"Les poids des critères notés totalisent {grid.total_weight()} points au lieu de 100.")
    for c in grid.scored():
        if c.weight <= 0:
            errors.append(f"Le critère « {c.label} » est noté mais son poids est nul.")
    for c in grid.criteria:
        if c.req_id not in eff_by_id:
            errors.append(f"Le critère « {c.label} » ne correspond à aucune exigence effective (remplacée ou supprimée).")
    # §5.7 — on ne retire jamais une exigence impérative/éliminatoire de la grille
    for r in effective:
        if r.category in MANDATORY_CATEGORIES and r.id not in in_grid:
            errors.append(f"L'exigence {r.category.value} « {r.label} » est absente de la grille : elle ne peut pas être retirée parce que peu de profils la satisfont.")
        if r.category in MANDATORY_CATEGORIES and r.dimension != "contrainte":
            c = grid.by_key(r.key)
            if c is not None and not c.scored and not r.params.get("group"):
                errors.append(f"L'exigence {r.category.value} « {r.label} » ne peut pas être exclue du score.")
    for r in effective:
        if r.id in in_grid and r.category in SCORED_CATEGORIES and not r.validated:
            errors.append(f"L'exigence « {r.label} » n'a pas été validée par un recruteur.")
        if r.category == Category.ELIMINATOIRE:
            if r.source_kind not in (SourceKind.CLIENT_CONFIRMED_IMPERATIVE, SourceKind.CLIENT_CLARIFICATION):
                errors.append(f"« {r.label} » est classée éliminatoire sans confirmation client tracée : requalifier en impératif ou renseigner la source client.")
            elif not r.quote.strip():
                errors.append(f"« {r.label} » est éliminatoire mais ne cite aucun extrait de la confirmation client.")
    # un critère « groupe » doit référencer des membres présents
    keys = {c.key for c in grid.criteria}
    for c in grid.criteria:
        for m in c.members:
            if m not in keys:
                errors.append(f"Le groupe « {c.label} » référence un membre absent de la grille ({m}).")
        if c.members and c.params.get("at_least", 0) > len(c.members):
            errors.append(f"Le groupe « {c.label} » exige plus de technologies qu'il n'en contient.")
    unresolved = [r for r in effective if r.category == Category.A_CLARIFIER]
    if unresolved and not allow_unresolved_clarifications:
        errors.append("Des exigences restent « à clarifier » : les reclasser ou confirmer explicitement le gel avec ces points ouverts : "
                      + ", ".join(f"« {r.label} »" for r in unresolved[:6]))
    elif unresolved:
        warnings.append(f"{len(unresolved)} exigence(s) à clarifier restent hors score : " + ", ".join(r.label for r in unresolved[:6]))
    mand = sum(c.weight for c in grid.scored() if c.mandatory)
    if grid.total_weight() == 100 and mand and mand < 50:
        warnings.append(f"Les exigences impératives ne pèsent que {mand}/100 : un candidat pourrait compenser un impératif manquant avec des critères secondaires.")
    big = [c for c in grid.scored() if c.weight > 40]
    if big:
        warnings.append("Un critère pèse plus de 40 points : " + ", ".join(c.label for c in big))
    if not grid.scored():
        errors.append("La grille ne contient aucun critère noté.")
    return errors, warnings


def freeze(grid: Grid, effective: list[Req], validated_by: str, *, allow_unresolved_clarifications: bool = False) -> Grid:
    errors, _ = validate(grid, effective, allow_unresolved_clarifications=allow_unresolved_clarifications)
    if errors:
        raise GridError(errors)
    if not validated_by.strip():
        raise GridError(["La validation d'une grille doit être attribuée à un recruteur."])
    grid.status = "frozen"
    grid.validated_by = validated_by
    grid.content_hash = grid.compute_hash()
    return grid


def assert_unchanged(grid: Grid) -> None:
    """Détecte toute altération d'une grille figée (garde-fou contre la dérive des critères)."""
    if grid.status == "frozen" and grid.compute_hash() != grid.content_hash:
        raise GridError(["La grille figée a été modifiée après validation : créer une nouvelle version."])


def new_version(prev: Grid, effective: list[Req], reason: str, *, reweight: bool = True) -> tuple[Grid, dict[str, Any]]:
    """Nouvelle version (brouillon) après un changement du besoin ; renvoie aussi le diff."""
    if not reason.strip():
        raise GridError(["Une nouvelle version de grille doit expliquer le changement du besoin."])
    fresh = propose_grid(prev.mission_id, effective, version=prev.version + 1, reason=reason)
    fresh.factors, fresh.caps, fresh.thresholds = dict(prev.factors), dict(prev.caps), dict(prev.thresholds)
    fresh.recency_window_years = prev.recency_window_years
    if not reweight:
        old = {c.key: c.weight for c in prev.criteria if c.scored}
        sc = [c for c in fresh.scored()]
        if all(c.key in old for c in sc) and sum(old[c.key] for c in sc) == 100:
            for c in sc:
                c.weight = old[c.key]
    return fresh, diff(prev, fresh)


def diff(a: Grid, b: Grid) -> dict[str, Any]:
    ak = {c.key: c for c in a.criteria}
    bk = {c.key: c for c in b.criteria}
    return {
        "from_version": a.version, "to_version": b.version,
        "added": [k for k in bk if k not in ak],
        "removed": [k for k in ak if k not in bk],
        "changed": [
            {"key": k, "from": {"weight": ak[k].weight, "category": ak[k].category, "depth": ak[k].depth_required},
             "to": {"weight": bk[k].weight, "category": bk[k].category, "depth": bk[k].depth_required}}
            for k in bk if k in ak and (ak[k].weight, ak[k].category, ak[k].depth_required) != (bk[k].weight, bk[k].category, bk[k].depth_required)
        ],
    }
