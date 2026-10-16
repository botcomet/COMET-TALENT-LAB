"""V1.2 — lien Boond en LECTURE SEULE : adaptateurs, import de besoins, déduplication, révisions, minimisation, garde-fous.

Aucune donnée réelle : l'instantané de test est entièrement fictif (noms de code, textes de fixtures)."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from talentlab.integrations import boond as bd
from tests.conftest import Session, freeze
from tests.fixtures import briefs as B

SCHEMA = bd.SNAPSHOT_SCHEMA


def html(text: str) -> str:
    return "<p>" + text.replace("\n", "<br>") + "</p>"


NEEDS = [
    {"external_id": "9001", "reference": "AO9001", "title": "Tech Lead Java (fictif)", "company": "Client Fictif A", "state": "En cours",
     "updated_at": "2026-09-03 10:00:00", "created_at": "2026-09-01 09:00:00", "description": html(B.TLJ), "criteria": "", "place": "Paris", "start": "2026-11-02", "end": ""},
    {"external_id": "9002", "reference": "AO9002", "title": "Besoin sans descriptif", "company": "Client Fictif B", "state": "En cours",
     "updated_at": "2026-09-04 10:00:00", "description": "", "criteria": ""},
    {"external_id": "9003", "reference": "AO9003", "title": "Architecte SI (fictif)", "company": "Client Fictif C", "state": "Gagné", "updated_at": "2026-09-02 10:00:00",
     "description": "Définir l'architecture cible du SI de gestion.", "criteria": "Architecture SI : décisionnaire.\nUrbanisation : pratique réelle sur au moins deux projets."},
    {"external_id": "9004", "reference": "AO9004", "title": "Data Engineer (fictif)", "company": "Client Fictif A", "state": "En cours", "updated_at": "2026-09-05 10:00:00",
     "description": "Construire des pipelines de données batch et temps réel avec Kafka et Spark ; industrialiser la qualité des données."},
]


def write_snapshot(path: Path, needs=NEEDS) -> None:
    path.write_text(json.dumps({"schema": SCHEMA, "exported_at": "2026-10-16T00:00:00Z", "needs": needs}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def snapshot(app_env, monkeypatch):
    path = app_env / "needs.json"
    write_snapshot(path)
    monkeypatch.setenv("TALENTLAB_BOOND_SOURCE", "file")
    monkeypatch.setenv("TALENTLAB_BOOND_SNAPSHOT_PATH", str(path))
    from talentlab import config
    config.get_settings.cache_clear()
    return path


def sources_of(s, mid):
    return s.get(f"/api/missions/{mid}").json()["sources"]


# =============================================================================== désactivé par défaut
def test_boond_is_disabled_by_default_and_exposes_nothing(tm):
    s = tm(1)
    assert s.get("/api/config").json()["boond_import"] is False
    st = s.get("/api/integrations/boond/status").json()
    assert st["enabled"] is False and st["read_only"] is True and st["writes_to_boond"] is False
    assert s.get("/api/integrations/boond/needs").status_code == 404
    assert s.post("/api/integrations/boond/import", json={"external_id": "1"}).status_code == 404


def test_boond_endpoints_require_authentication(snapshot, app):
    from fastapi.testclient import TestClient
    from tests.conftest import HDR
    with TestClient(app, headers=HDR) as c:
        assert c.get("/api/integrations/boond/needs").status_code == 401
        assert c.post("/api/integrations/boond/import", json={"external_id": "9001"}).status_code == 401


# =============================================================================== liste et recherche
def test_listing_searches_by_reference_title_or_company_and_flags_needs_without_text(snapshot, tm):
    s = tm(1)
    assert s.get("/api/config").json()["boond_import"] is True
    r = s.get("/api/integrations/boond/needs").json()
    assert r["read_only"] is True
    assert [n["external_id"] for n in r["needs"]] == ["9004", "9002", "9001", "9003"], "du plus récemment modifié au plus ancien"
    assert {n["external_id"]: n["has_text"] for n in r["needs"]} == {"9001": True, "9002": False, "9003": True, "9004": True}
    assert all(n["mission_id"] is None for n in r["needs"])
    assert [n["external_id"] for n in s.get("/api/integrations/boond/needs", params={"q": "JAVA"}).json()["needs"]] == ["9001"]
    assert [n["external_id"] for n in s.get("/api/integrations/boond/needs", params={"q": "data engineer"}).json()["needs"]] == ["9004"]
    assert [n["external_id"] for n in s.get("/api/integrations/boond/needs", params={"q": "fictif b"}).json()["needs"]] == ["9002"], "la société fait partie des champs cherchés"
    assert [n["external_id"] for n in s.get("/api/integrations/boond/needs", params={"q": "ao9003"}).json()["needs"]] == ["9003"]
    assert len(s.get("/api/integrations/boond/needs", params={"limit": 2}).json()["needs"]) == 2
    assert [n["external_id"] for n in s.get("/api/integrations/boond/needs", params={"limit": 2, "offset": 2}).json()["needs"]] == ["9001", "9003"]
    assert s.get("/api/integrations/boond/needs", params={"limit": 500}).status_code == 422
    assert s.get("/api/integrations/boond/needs", params={"q": "x" * 101}).status_code == 422
    # aucun champ sensible dans la liste
    assert set(r["needs"][0]) == {"external_id", "reference", "title", "company", "state", "updated_at", "has_text", "mission_id"}


# =============================================================================== import
def test_import_creates_a_private_mission_with_cleaned_text_and_a_traceable_source(snapshot, tm):
    owner, other = tm(1), tm(2)
    r = owner.post("/api/integrations/boond/import", json={"external_id": "9001"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "created" and j["mission"]["title"] == "Tech Lead Java (fictif)" and j["mission"]["client"] == "Client Fictif A"
    mid = j["mission"]["id"]
    srcs = sources_of(owner, mid)
    assert len(srcs) == 1 and "Boond AO9001" in srcs[0]["label"] and "lecture seule" in srcs[0]["label"] and srcs[0]["author"].startswith("Boond")
    assert "<p>" not in srcs[0]["text"] and "<br" not in srcs[0]["text"] and "Kafka" in srcs[0]["text"], "balises retirées, contenu conservé"
    assert "Lieu : Paris" in srcs[0]["text"]
    reqs = owner.get(f"/api/missions/{mid}").json()["requirements"]
    assert any(x["key"] == "skill:kafka" for x in reqs), "l'analyse existante lit le besoin importé comme un brief collé"
    # le besoin importé est privé : un collègue ne le voit ni ne l'atteint
    assert other.get(f"/api/missions/{mid}").status_code == 404
    # la liste l'indique pour l'importateur seulement
    assert next(n for n in owner.get("/api/integrations/boond/needs").json()["needs"] if n["external_id"] == "9001")["mission_id"] == mid
    assert next(n for n in other.get("/api/integrations/boond/needs").json()["needs"] if n["external_id"] == "9001")["mission_id"] is None
    assert owner.get("/api/integrations/boond/status").json()["imported_by_you"] == 1


def test_importing_twice_never_duplicates_and_each_user_has_their_own_copy(snapshot, tm):
    a, b = tm(1), tm(2)
    first = a.post("/api/integrations/boond/import", json={"external_id": "9003"}).json()
    again = a.post("/api/integrations/boond/import", json={"external_id": "9003"}).json()
    assert first["status"] == "created" and again["status"] == "unchanged" and again["mission"]["id"] == first["mission"]["id"]
    assert len(sources_of(a, first["mission"]["id"])) == 1, "un besoin inchangé n'ajoute aucune source"
    mine = [m for m in a.get("/api/missions").json() if m["title"].startswith("Architecte")]
    assert len(mine) == 1
    other = b.post("/api/integrations/boond/import", json={"external_id": "9003"}).json()
    assert other["status"] == "created" and other["mission"]["id"] != first["mission"]["id"]


def test_a_need_modified_in_boond_adds_a_new_source_and_never_touches_a_frozen_grid(snapshot, tm):
    s = tm(1)
    mid = s.post("/api/integrations/boond/import", json={"external_id": "9001"}).json()["mission"]["id"]
    freeze(s, mid)
    grids = s.get(f"/api/missions/{mid}").json()["grids"]
    changed = [dict(n) for n in NEEDS]
    changed[0]["description"] = html(B.TLJ + "\nAjout en révision : MARQUEURREVISION.")
    write_snapshot(snapshot, changed)
    os.utime(snapshot, (time.time() + 5, time.time() + 5))                              # le cache de l'instantané repose sur la date de modification
    r = s.post("/api/integrations/boond/import", json={"external_id": "9001"}).json()
    assert r["status"] == "updated" and r["mission"]["id"] == mid and "nouvelle source" in r["message"]
    srcs = sources_of(s, mid)
    assert len(srcs) == 2 and "révision" in srcs[1]["label"] and "MARQUEURREVISION" in srcs[1]["text"] and "MARQUEURREVISION" not in srcs[0]["text"], "l'historique est conservé"
    after = s.get(f"/api/missions/{mid}").json()
    assert [g["id"] for g in after["grids"]] == [g["id"] for g in grids] and after["grids"][-1]["status"] == "frozen", "la grille figée n'est pas modifiée"
    assert s.post("/api/integrations/boond/import", json={"external_id": "9001"}).json()["status"] == "unchanged", "la révision déjà importée n'est pas rejouée"


def test_a_need_without_usable_text_is_refused_rather_than_invented(snapshot, tm):
    s = tm(1)
    before = len(s.get("/api/missions").json())
    r = s.post("/api/integrations/boond/import", json={"external_id": "9002"})
    assert r.status_code == 422 and "descriptif exploitable" in r.text
    assert len(s.get("/api/missions").json()) == before
    assert s.post("/api/integrations/boond/import", json={"external_id": "424242"}).status_code == 404
    for bad in ("", "a b", "x" * 65, "1;DROP", "../etc"):
        assert s.post("/api/integrations/boond/import", json={"external_id": bad}).status_code == 422, bad


def test_two_simultaneous_imports_create_exactly_one_mission(snapshot, tm, app):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    s = tm(1)
    cookie = s.c.cookies.get("talentlab_session")

    def go(_):
        with TestClient(app, headers={"X-Requested-With": "talentlab"}, cookies={"talentlab_session": cookie}) as c:
            r = c.post("/api/integrations/boond/import", json={"external_id": "9004"})
            return r.status_code, (r.json().get("status") if r.status_code == 200 else None)

    with ThreadPoolExecutor(6) as ex:
        out = list(ex.map(go, range(6)))
    assert all(code in (200, 409, 503) for code, _ in out), out
    assert [st for code, st in out if code == 200].count("created") == 1, out
    assert len([m for m in s.get("/api/missions").json() if m["title"].startswith("Data Engineer")]) == 1


# =============================================================================== minimisation et lecture seule
def test_a_snapshot_with_personal_or_commercial_fields_is_refused(snapshot, tm):
    s = tm(1)
    bad = [dict(NEEDS[0], contact="Prénom Nom", budget="650")]
    write_snapshot(snapshot, bad)
    os.utime(snapshot, (time.time() + 5, time.time() + 5))
    r = s.get("/api/integrations/boond/needs")
    assert r.status_code == 502 and "invalide" in r.text and "Prénom" not in r.text
    write_snapshot(snapshot, [NEEDS[0], NEEDS[0]])
    os.utime(snapshot, (time.time() + 10, time.time() + 10))
    assert s.get("/api/integrations/boond/needs").status_code == 502, "identifiant dupliqué"
    snapshot.write_text("{pas du json", encoding="utf-8")
    os.utime(snapshot, (time.time() + 15, time.time() + 15))
    assert s.get("/api/integrations/boond/needs").status_code == 502


def _capture():
    seen: list[tuple[str, dict]] = []

    def runner(sql, params):
        seen.append((sql, params))
        return []
    return seen, runner


FORBIDDEN = ("AO_BUDGET", "AO_CA", "AO_TAUXHORAIRE", "AO_TARIF", "AO_PROBA", "ID_PROFIL", "ID_CRMCONTACT", "ID_CREATOR", "CSOC_TEL", "CSOC_ADR", "CSOC_SIREN", "CSOC_TVA",
             "TAB_CV", "TAB_PROFIL", "TAB_USER", "TAB_BANKING", "TAB_RIB", "TAB_CONTRAT", "TAB_POSITIONNEMENT", "TAB_CRMCONTACT", "AO_AI_MATCHING_BRIEF", "AO_OUTILS")


def test_the_bigquery_adapter_reads_only_allowlisted_tables_and_columns_with_bound_parameters():
    seen, runner = _capture()
    src = bd.BigQueryBoondSource("fictif-projet-test", "fictif_dataset", runner=runner)
    evil = "x'); DROP TABLE y; -- \\ %"
    src.list_needs(evil, 20, 0)
    src.get_need("215")
    assert len(seen) == 2
    for sql, params in seen:
        bd.assert_read_only(sql)
        assert sql.lstrip().upper().startswith("SELECT")
        assert evil not in sql and "DROP" not in sql.upper(), "le texte de l'utilisateur n'est jamais dans la requête : il est lié en paramètre"
        for bad in FORBIDDEN:
            assert bad not in sql, bad
        tables = set(re.findall(r"`fictif-projet-test\.fictif_dataset\.([A-Za-z_]+)`", sql))
        assert tables and tables <= set(bd.ALLOWED_TABLES)
        alias = {"a": "TAB_AO", "c": "TAB_CRMSOCIETE", "e": "ref_etat_besoin"}
        for a, col in re.findall(r"\b([ace])\.([A-Za-z_]+)", sql):
            assert col in bd.ALLOWED_COLUMNS[alias[a]], (a, col)
    assert seen[0][1]["needle"] == "x'); drop table y; -- \\ %"[:100], "terme normalisé, transmis comme paramètre"
    assert seen[0][1]["max_rows"] == 20 and seen[1][1] == {"need_id": 215}
    # bornes : pas de page démesurée, pas de décalage négatif
    src.list_needs("", 10_000, -5)
    assert seen[-1][1]["max_rows"] == bd.MAX_LIST and seen[-1][1]["skip"] == 0
    # identifiant non numérique : aucune requête n'est émise
    n = len(seen)
    assert src.get_need("1 OR 1=1") is None and len(seen) == n


def test_write_statements_are_refused_before_any_execution():
    for sql in ("INSERT INTO t VALUES (1)", "UPDATE t SET a=1", "DELETE FROM t", "DROP TABLE t", "SELECT 1; DROP TABLE t", "MERGE t USING s ON 1=1",
                "CREATE TABLE t AS SELECT 1", "CALL proc()", "EXPORT DATA OPTIONS() AS SELECT 1", "  select * from t where a=1 and b=2; delete from t"):
        with pytest.raises(bd.BoondError):
            bd.assert_read_only(sql)
    bd.assert_read_only("SELECT a.ID_AO FROM `p.d.TAB_AO` a WHERE a.AO_DATEUPDATE > @x")


def test_project_and_dataset_identifiers_are_validated_and_infrastructure_errors_stay_generic():
    for p, d in (("Fictif-Projet-Test", "ds"), ("p", "ds"), ("ok-project-1", "d`s"), ("ok-project-1", "d s"), ("ok-project-1", ""), ("a;b-project", "ds")):
        with pytest.raises(bd.BoondError):
            bd.BigQueryBoondSource(p, d)

    def boom(sql, params):
        raise RuntimeError("403 secret-detail-compte-de-service@projet")
    with pytest.raises(bd.BoondError) as e:
        bd.BigQueryBoondSource("fictif-projet-test", "fictif_dataset", runner=boom).list_needs()
    assert "secret-detail" not in str(e.value) and "indisponible" in str(e.value)


def test_bigquery_rows_are_mapped_and_cleaned_without_inventing_anything():
    rows = [{"external_id": "77", "reference": "AO77", "title": "Titre fictif", "company": "Société fictive", "state": "En cours", "updated_at": "2026-10-01 10:00:00",
             "created_at": "2026-09-30 10:00:00", "description": "<p>Un descriptif fictif d'au moins quarante caractères, avec <b>Kafka</b>.</p>", "criteria": None,
             "place": "Lille", "start_date": None, "end_date": "2027-01-31", "has_text": True}]
    src = bd.BigQueryBoondSource("fictif-projet-test", "fictif_dataset", runner=lambda sql, params: rows)
    n = src.get_need("77")
    assert n and n.description.startswith("Un descriptif fictif") and "<" not in n.description and n.criteria == "" and n.start == "" and n.end == "2027-01-31"
    assert src.list_needs()[0].has_text is True
    empty = bd.BigQueryBoondSource("fictif-projet-test", "fictif_dataset", runner=lambda sql, params: [])
    assert empty.get_need("1") is None


def test_the_bigquery_source_reports_an_unavailable_source_as_a_502_without_detail(app_env, monkeypatch, tm):
    monkeypatch.setenv("TALENTLAB_BOOND_SOURCE", "bigquery")
    monkeypatch.setenv("TALENTLAB_BOOND_BQ_PROJECT", "fictif-projet-test")
    monkeypatch.setenv("TALENTLAB_BOOND_BQ_DATASET", "fictif_dataset")
    from talentlab import config
    config.get_settings.cache_clear()

    def boom(sql, params):
        raise RuntimeError("Access Denied: compte-de-service-secret")
    monkeypatch.setattr("talentlab.api.routes.bd.build_source", lambda s: bd.BigQueryBoondSource("fictif-projet-test", "fictif_dataset", runner=boom))
    s = tm(1)
    for r in (s.get("/api/integrations/boond/needs"), s.post("/api/integrations/boond/import", json={"external_id": "5"})):
        assert r.status_code == 502 and "indisponible" in r.text and "secret" not in r.text


# =============================================================================== configuration
def test_boond_configuration_is_closed_and_the_file_source_is_refused_in_production():
    from talentlab.config import Settings
    prod = dict(env="prod", auth_mode="gateway", gateway_secret="x", session_secret="y", encryption_key="z", database_url="postgresql+psycopg://u:p@h/db")
    assert Settings(env="dev").boond_source == "none"
    with pytest.raises(ValueError, match="réservé au développement"):
        Settings(**prod, boond_source="file", boond_snapshot_path="/x.json")
    with pytest.raises(ValueError, match="BOND|BOOND|SOURCE"):
        Settings(env="dev", boond_source="sandbox")
    with pytest.raises(ValueError, match="SNAPSHOT_PATH"):
        Settings(env="dev", boond_source="file")
    with pytest.raises(ValueError, match="BQ_PROJECT"):
        Settings(env="dev", boond_source="bigquery")
    with pytest.raises(ValueError, match="BQ_PROJECT"):
        Settings(env="dev", boond_source="bigquery", boond_bq_project="Fictif-Projet-Test", boond_bq_dataset="ds")
    with pytest.raises(ValueError, match="MAX_BYTES"):
        Settings(env="dev", boond_bq_max_bytes=5)
    ok = Settings(**prod, boond_source="bigquery", boond_bq_project="fictif-projet-test", boond_bq_dataset="fictif_dataset")
    assert ok.boond_source == "bigquery"


def test_the_repository_contains_no_real_boond_identifier():
    """Le dépôt est public : ni identifiant de projet, ni nom de dataset, ni compte de service, ni compte d'agent réels ne doivent y figurer.
    Le test cherche des MOTIFS génériques (jamais les valeurs réelles, qui ne doivent figurer nulle part, pas même ici)."""
    root = Path(__file__).resolve().parent.parent
    patterns = [re.compile(x, re.I) for x in (
        r"[\w.+-]+@[\w-]+\.iam\.gserviceaccount\.com",             # comptes de service
        r"\bcomet[-_](?:dev|prod|sandbox|playground)[-_\w]*",           # noms de projets ou de clients techniques internes
        r"\b\w*boond[-_]sync\w*",                                     # nom du chargeur / dataset de la copie
        r"\bsaas\+\w+@",                                              # compte d'agent
    )]
    files = list(root.glob("talentlab/**/*.py")) + list(root.glob("frontend/*")) + list(root.glob("docs/*.md")) + [root / "README.md", root / ".env.example", root / "pyproject.toml"]
    hits = [(str(p.relative_to(root)), rx.pattern) for p in files for rx in patterns if rx.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not hits, hits
