"""Besoins Boond en LECTURE SEULE.

Deux sources interchangeables derrière la même interface :

- ``file`` : instantané JSON local, pour le développement et les tests (refusé en production) ;
- ``bigquery`` : copie Boond de l'entrepôt de Comet (tables ``TAB_AO``, ``TAB_CRMSOCIETE``, ``ref_etat_besoin``).

Garde-fous, vérifiés par test :

- **aucune écriture** : seules des requêtes ``SELECT`` sur une liste FERMÉE de tables et de colonnes sont émises ; rien n'est jamais renvoyé à Boond ;
- **minimisation** : ne sont lus ni contacts, ni responsables, ni budgets, ni chiffres d'affaires, ni tarifs, ni CV, ni candidats, ni données bancaires ou contractuelles ;
- le texte recherché par l'utilisateur n'est jamais inséré dans la requête (paramètres liés), les noms de projet et de dataset sont validés ;
- coût borné (``maximum_bytes_billed``) et délai borné ;
- aucune valeur d'identification (projet, dataset, compte) n'est écrite dans le code : tout vient de la configuration.

Les identifiants d'accès à BigQuery ne sont PAS ceux du connecteur utilisé pendant le développement : l'application utilise les identifiants applicatifs
de son environnement d'exécution (compte de service à droits de lecture limités à ces trois tables).
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

log = logging.getLogger("talentlab.boond")

SOURCE_NAME = "boond"
SNAPSHOT_SCHEMA = "comet.talentlab.boond-needs-snapshot/v1"
MAX_SNAPSHOT_BYTES = 20_000_000
MAX_DESCRIPTION = 20_000
MAX_LIST = 50

# Tables et colonnes que l'adaptateur BigQuery a le droit de lire (liste fermée, testée).
ALLOWED_TABLES = ("TAB_AO", "TAB_CRMSOCIETE", "ref_etat_besoin")
ALLOWED_COLUMNS = {
    "TAB_AO": ("ID_AO", "AO_REF", "AO_TITLE", "AO_DESCRIPTION", "AO_CRITERES", "AO_LIEU", "AO_DEBUT", "AO_FIN", "AO_DEPOT", "AO_DATEUPDATE", "AO_ETAT",
               "AO_VISIBILITE", "ID_CRMSOCIETE"),
    "TAB_CRMSOCIETE": ("ID_CRMSOCIETE", "CSOC_SOCIETE"),
    "ref_etat_besoin": ("id", "libelle"),
}


class BoondError(Exception):
    """Source indisponible ou donnée inexploitable (message sûr, sans détail d'infrastructure)."""


@dataclass(frozen=True)
class NeedSummary:
    external_id: str
    reference: str
    title: str
    company: str
    state: str
    updated_at: str
    has_text: bool = True                      # le besoin porte-t-il un descriptif exploitable ? (indicatif dans la liste ; l'import revérifie sur le texte nettoyé)


@dataclass(frozen=True)
class Need(NeedSummary):
    description: str = ""
    criteria: str = ""
    place: str = ""
    start: str = ""
    end: str = ""
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class BoondSource(Protocol):
    name: str

    def list_needs(self, q: str = "", limit: int = 20, offset: int = 0) -> list[NeedSummary]: ...

    def get_need(self, external_id: str) -> Need | None: ...


# ------------------------------------------------------------------ texte
_TAG = re.compile(r"<[^>]{0,500}>")
_BR = re.compile(r"(?i)<\s*(?:br\s*/?|/p|/div|/h[1-6])\s*>")
_LI = re.compile(r"(?i)<\s*li[^>]*>")
_WS_LINES = re.compile(r"[ \t ]+")


def clean_text(raw: Any, limit: int = MAX_DESCRIPTION) -> str:
    """Texte brut lisible : balises retirées (les sauts de ligne et les puces sont conservés), entités décodées, espaces normalisés, longueur bornée."""
    if raw is None:
        return ""
    t = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    t = _BR.sub("\n", t)
    t = _LI.sub("\n- ", t)
    t = _TAG.sub("", t)
    t = html.unescape(t).replace("\x00", "")
    lines = [_WS_LINES.sub(" ", ln).strip() for ln in t.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()[:limit]


def compose_brief(n: Need) -> str:
    """Descriptif lisible par l'analyse de brief existante. Rien n'est ajouté ni déduit : seuls les champs de Boond sont recopiés, sous des intitulés neutres.

    Les « critères » saisis dans Boond sont rangés sous « Profil recherché » (section neutre) : l'application ne les promeut jamais d'office en impératifs."""
    parts = [n.title.strip()]
    meta = []
    if n.place:
        meta.append(f"Lieu : {n.place}")
    if n.start:
        meta.append(f"Démarrage : {n.start}")
    if n.end:
        meta.append(f"Fin : {n.end}")
    if meta:
        parts.append("\n".join(meta))
    if n.description:
        parts.append("Description du besoin\n" + n.description)
    if n.criteria:
        parts.append("Profil recherché\n" + n.criteria)
    return "\n\n".join(p for p in parts if p).strip()


def content_hash(n: Need) -> str:
    return hashlib.sha256(compose_brief(n).encode("utf-8")).hexdigest()


def has_usable_text(n: Need, minimum: int = 40) -> bool:
    return len((n.description + " " + n.criteria).strip()) >= minimum


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


# ------------------------------------------------------------------ source fichier (développement / tests)
_SNAPSHOT_FIELDS = {"external_id", "reference", "title", "company", "state", "updated_at", "created_at", "description", "criteria", "place", "start", "end"}


class FileBoondSource:
    """Instantané JSON local. Jamais utilisé en production (refusé par la configuration)."""
    name = "file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._cache: tuple[float, list[Need]] | None = None

    def _load(self) -> list[Need]:
        try:
            st = self.path.stat()
        except OSError as e:
            raise BoondError("Instantané Boond introuvable.") from e
        if st.st_size > MAX_SNAPSHOT_BYTES:
            raise BoondError("Instantané Boond trop volumineux.")
        if self._cache and self._cache[0] == st.st_mtime:
            return self._cache[1]
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise BoondError("Instantané Boond illisible.") from e
        if not isinstance(doc, dict) or doc.get("schema") != SNAPSHOT_SCHEMA or not isinstance(doc.get("needs"), list):
            raise BoondError(f"Instantané Boond invalide (schéma attendu : {SNAPSHOT_SCHEMA}).")
        needs: list[Need] = []
        seen: set[str] = set()
        for raw in doc["needs"]:
            if not isinstance(raw, dict) or set(raw) - _SNAPSHOT_FIELDS or not _s(raw.get("external_id")):
                raise BoondError("Instantané Boond invalide : champ inconnu ou identifiant manquant (aucune donnée personnelle ne doit y figurer).")
            ext = _s(raw["external_id"])
            if ext in seen:
                raise BoondError("Instantané Boond invalide : identifiant dupliqué.")
            seen.add(ext)
            needs.append(Need(external_id=ext, reference=_s(raw.get("reference")), title=_s(raw.get("title")), company=_s(raw.get("company")), state=_s(raw.get("state")),
                              updated_at=_s(raw.get("updated_at")), created_at=_s(raw.get("created_at")), description=clean_text(raw.get("description")),
                              criteria=clean_text(raw.get("criteria")), place=_s(raw.get("place")), start=_s(raw.get("start")), end=_s(raw.get("end"))))
        self._cache = (st.st_mtime, needs)
        return needs

    def list_needs(self, q: str = "", limit: int = 20, offset: int = 0) -> list[NeedSummary]:
        from ..domain.text import fold
        terms = [t for t in fold(q).split() if t][:6]
        rows = [n for n in self._load() if all(t in fold(f"{n.reference} {n.title} {n.company}") for t in terms)]
        rows.sort(key=lambda n: (n.updated_at, n.external_id), reverse=True)
        limit = max(1, min(int(limit), MAX_LIST))
        return [NeedSummary(n.external_id, n.reference, n.title, n.company, n.state, n.updated_at, has_usable_text(n)) for n in rows[max(0, offset):max(0, offset) + limit]]

    def get_need(self, external_id: str) -> Need | None:
        return next((n for n in self._load() if n.external_id == _s(external_id)), None)


# ------------------------------------------------------------------ source BigQuery (lecture seule)
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,200}$")
_WRITE_WORDS = re.compile(r"\b(insert|update|delete|merge|create|drop|alter|truncate|grant|revoke|call|export|load|execute)\b", re.I)


def valid_project(p: str) -> bool:
    return bool(_PROJECT_RE.match(p or ""))


def valid_dataset(d: str) -> bool:
    return bool(_DATASET_RE.match(d or ""))


def assert_read_only(sql: str) -> None:
    """Défense en profondeur : la requête doit être un SELECT unique, sans mot d'écriture ni point-virgule."""
    body = sql.strip()
    if not body.upper().startswith("SELECT") or ";" in body or _WRITE_WORDS.search(body):
        raise BoondError("Requête refusée : seule la lecture est autorisée.")


Runner = Callable[[str, dict[str, Any]], Iterable[dict[str, Any]]]


class BigQueryBoondSource:
    """Lecture de la copie Boond dans BigQuery. ``runner`` est injectable (tests) ; par défaut, le client officiel avec les identifiants applicatifs de l'environnement."""
    name = "bigquery"

    def __init__(self, project: str, dataset: str, *, max_bytes: int = 100_000_000, timeout_s: float = 30.0, runner: Runner | None = None) -> None:
        if not valid_project(project) or not valid_dataset(dataset):
            raise BoondError("Configuration BigQuery invalide (projet ou dataset).")
        self.project, self.dataset, self.max_bytes, self.timeout_s = project, dataset, int(max_bytes), timeout_s
        self._runner = runner or self._default_runner

    def _default_runner(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            from google.cloud import bigquery  # type: ignore
        except ImportError as e:                                      # pragma: no cover - dépend de l'environnement
            raise BoondError("La bibliothèque google-cloud-bigquery n'est pas installée (extra « bigquery »).") from e
        client = bigquery.Client(project=self.project)
        qp = [bigquery.ScalarQueryParameter(k, "INT64" if isinstance(v, int) else "STRING", v) for k, v in params.items()]
        cfg = bigquery.QueryJobConfig(query_parameters=qp, maximum_bytes_billed=self.max_bytes, labels={"app": "talentlab", "purpose": "needs-readonly"})
        return [dict(r.items()) for r in client.query(sql, job_config=cfg).result(timeout=self.timeout_s)]

    def _t(self, table: str) -> str:
        if table not in ALLOWED_TABLES:
            raise BoondError("Table non autorisée.")
        return f"`{self.project}.{self.dataset}.{table}`"

    def _run(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert_read_only(sql)
        try:
            return list(self._runner(sql, params))
        except BoondError:
            raise
        except Exception as e:                                        # noqa: BLE001 — jamais de détail d'infrastructure vers l'utilisateur
            log.error("Lecture BigQuery impossible : %s", type(e).__name__)
            raise BoondError("La source Boond est indisponible pour le moment.") from e

    def list_needs(self, q: str = "", limit: int = 20, offset: int = 0) -> list[NeedSummary]:
        from ..domain.text import fold
        sql = (
            "SELECT CAST(a.ID_AO AS STRING) AS external_id, a.AO_REF AS reference, a.AO_TITLE AS title, COALESCE(c.CSOC_SOCIETE, '') AS company, "
            "COALESCE(e.libelle, '') AS state, CAST(a.AO_DATEUPDATE AS STRING) AS updated_at, "
            "(LENGTH(IFNULL(a.AO_DESCRIPTION, '')) + LENGTH(IFNULL(a.AO_CRITERES, ''))) >= 40 AS has_text "
            f"FROM {self._t('TAB_AO')} a "
            f"LEFT JOIN {self._t('TAB_CRMSOCIETE')} c ON c.ID_CRMSOCIETE = a.ID_CRMSOCIETE "
            f"LEFT JOIN {self._t('ref_etat_besoin')} e ON e.id = a.AO_ETAT "
            "WHERE a.AO_VISIBILITE = 1 "
            "AND (@needle = '' OR STRPOS(LOWER(CONCAT(IFNULL(a.AO_REF, ''), ' ', IFNULL(a.AO_TITLE, ''), ' ', IFNULL(c.CSOC_SOCIETE, ''))), @needle) > 0) "
            "ORDER BY a.AO_DATEUPDATE DESC, a.ID_AO DESC LIMIT @max_rows OFFSET @skip")
        term = " ".join(fold(q).split())[:100]
        rows = self._run(sql, {"needle": term, "max_rows": max(1, min(int(limit), MAX_LIST)), "skip": max(0, int(offset))})
        return [NeedSummary(_s(r["external_id"]), _s(r["reference"]), _s(r["title"]), _s(r["company"]), _s(r["state"]), _s(r["updated_at"]), bool(r.get("has_text", True)))
                for r in rows]

    def get_need(self, external_id: str) -> Need | None:
        if not re.fullmatch(r"\d{1,12}", _s(external_id)):
            return None
        sql = (
            "SELECT CAST(a.ID_AO AS STRING) AS external_id, a.AO_REF AS reference, a.AO_TITLE AS title, COALESCE(c.CSOC_SOCIETE, '') AS company, "
            "COALESCE(e.libelle, '') AS state, CAST(a.AO_DATEUPDATE AS STRING) AS updated_at, CAST(a.AO_DEPOT AS STRING) AS created_at, "
            "a.AO_DESCRIPTION AS description, a.AO_CRITERES AS criteria, a.AO_LIEU AS place, CAST(a.AO_DEBUT AS STRING) AS start_date, CAST(a.AO_FIN AS STRING) AS end_date "
            f"FROM {self._t('TAB_AO')} a "
            f"LEFT JOIN {self._t('TAB_CRMSOCIETE')} c ON c.ID_CRMSOCIETE = a.ID_CRMSOCIETE "
            f"LEFT JOIN {self._t('ref_etat_besoin')} e ON e.id = a.AO_ETAT "
            "WHERE a.AO_VISIBILITE = 1 AND a.ID_AO = @need_id LIMIT 1")
        rows = self._run(sql, {"need_id": int(external_id)})
        if not rows:
            return None
        r = rows[0]
        return Need(external_id=_s(r["external_id"]), reference=_s(r["reference"]), title=_s(r["title"]), company=_s(r["company"]), state=_s(r["state"]),
                    updated_at=_s(r["updated_at"]), created_at=_s(r.get("created_at")), description=clean_text(r.get("description")), criteria=clean_text(r.get("criteria")),
                    place=_s(r.get("place")), start=_s(r.get("start_date")), end=_s(r.get("end_date")))


def build_source(settings: Any) -> BoondSource | None:
    """Source configurée, ou ``None`` si l'intégration est désactivée (valeur par défaut)."""
    kind = settings.boond_source
    if kind == "none":
        return None
    if kind == "file":
        return FileBoondSource(settings.boond_snapshot_path)
    if kind == "bigquery":
        return BigQueryBoondSource(settings.boond_bq_project, settings.boond_bq_dataset, max_bytes=settings.boond_bq_max_bytes)
    raise BoondError("Source Boond inconnue.")
