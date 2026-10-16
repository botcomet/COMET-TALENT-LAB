"""Format de structuration Comet (§19.1) : les phrases du CV sont RANGÉES, jamais réécrites ni inventées (§19.3)."""
import re

import pytest

from talentlab.domain import dossier as dz
from talentlab.domain.cv_extract import parse_cv
from tests.conftest import Session, create_mission, freeze, upload
from tests.fixtures import briefs as B, cvs as C
from tests.helpers import TODAY


# ---------------------------------------------------------------- unités
def test_periods_are_french_and_never_invented():
    assert dz.french_period("2019-02", None, True, True) == "Février 2019 – en cours"
    assert dz.french_period("2013", "2019-01", False, True) == "2013 – Janvier 2019"
    assert dz.french_period("2018-03", None, False, True) == "Mars 2018 – date de fin non précisée"
    assert dz.french_period(None, None, False, False) == "dates non précisées"
    assert dz.french_period("2018-03", "2020-01", False, False) == "dates non précisées", "dates non établies par le CV : jamais déduites"


def test_a_sentence_keeps_its_words_and_only_loses_its_bullet():
    assert dz.sentence("- Mise en place d'un cluster Kafka de 6 brokers") == "Mise en place d'un cluster Kafka de 6 brokers."
    assert dz.sentence("• Support de niveau 3 !") == "Support de niveau 3 !"


def test_realisations_are_grouped_by_a_closed_vocabulary_of_business_titles():
    parts = dz.theme_parts([
        "- Conception et développement du back-office e-commerce en Java 17.",
        "- Mise en place et exploitation d'un cluster Kafka de 6 brokers.",
        "- Support de niveau 3 de la production : gestion des incidents, astreintes, post-mortem.",
        "- Encadrement technique de 4 développeurs, revues de code et mentorat.",
        "- Migration vers Kubernetes et automatisation des déploiements.",
        "- Rédaction de la documentation d'architecture et des spécifications.",
    ])
    titles = [p["title"] for p in parts]
    allowed = {t for t, _ in dz.THEMES} | {dz.OTHER}
    assert set(titles) <= allowed, "aucun titre libre : vocabulaire fermé"
    assert len([t for t in titles if t != dz.OTHER]) <= dz.MAX_NAMED_PARTS
    placed = [s for p in parts for s in p["sentences"]]
    assert len(placed) == 6 and len(set(placed)) == 6, "chaque phrase est placée une fois, aucune n'est perdue ni dupliquée"


def test_with_one_sentence_there_is_one_part_not_three():
    parts = dz.theme_parts(["- Développement d'un connecteur de paiement."])
    assert len(parts) == 1 and parts[0]["sentences"] == ["Développement d'un connecteur de paiement."]


def test_an_unclassifiable_sentence_goes_to_other_realisations_not_to_a_guessed_theme():
    parts = dz.theme_parts(["- Voyages fréquents chez le client."])
    assert [p["title"] for p in parts] == [dz.OTHER]


def test_the_environment_is_the_cv_line_as_is_or_not_stated():
    assert dz.environment_sentence({"environnement_technique_phrase": "Java 17, Spring Boot 2.7; Kafka"}) == "Java 17, Spring Boot 2.7, Kafka."
    assert dz.environment_sentence({"environnement_technique_phrase": None}) == dz.NOT_STATED


# ---------------------------------------------------------------- bout en bout (API)
def _export(s, mid, cid):
    assert s.patch(f"/api/missions/{mid}/candidates/{cid}", json={"acronym": "TL"}).status_code == 200
    r = s.get(f"/api/missions/{mid}/candidates/{cid}/export/dt?relevant_only=false")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_exported_dossier_follows_the_comet_structure_and_stays_faithful_to_the_cv(tm):
    s = tm(1)
    m = create_mission(s)
    freeze(s, m["id"])
    upload(s, m["id"], [("b.txt", C.CV_TLJ_B.encode())])
    c = s.get(f"/api/missions/{m['id']}/candidates").json()[0]
    out = _export(s, m["id"], c["id"])
    md = out["dossier_comet"]
    # structure Comet
    assert "### Marchand Exemple — Tech Lead Java — Février 2019 – en cours" in md
    assert "### Retailer Exemple — Développeur Java — Mars 2013 – Janvier 2019" in md
    assert md.count("**Contexte**") == 2 and md.count("**Réalisations**") == 2 and md.count("**Environnement technique**") == 2
    assert "Java 17, Spring Boot 2.7, Kafka 3.4, Kafka Connect, Avro, Schema Registry, PostgreSQL, Kubernetes." in md
    # sans tirets ni puces dans les réalisations
    realisations = md.split("**Réalisations**")[1].split("**Environnement technique**")[0]
    assert not re.search(r"^\s*[-•*]\s", realisations, re.M)
    assert re.search(r"\*Partie 1 — ", realisations)
    # fidélité : chaque phrase de réalisation existe, mot pour mot, dans le CV
    flat_cv = re.sub(r"\s+", " ", C.CV_TLJ_B)
    for part in re.findall(r"^\*Partie \d+ — [^\n]+\*\n([^\n]+)$", md, re.M):
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])", part):
            assert sent.rstrip(".") in flat_cv, f"phrase absente du CV : {sent!r}"
    # aucune technologie ajoutée à l'environnement
    env_cv = {t.strip() for t in re.findall(r"Environnement technique : ([^\n]+)", C.CV_TLJ_B)[0].split(",")}
    env_out = {t.strip() for t in re.search(r"\*\*Environnement technique\*\*\n([^\n]+)", md).group(1).rstrip(".").split(",")}
    assert env_out <= env_cv


def test_the_client_dossier_contains_no_internal_information_while_the_internal_export_keeps_it(tm):
    s = tm(1)
    m = create_mission(s)
    freeze(s, m["id"])
    upload(s, m["id"], [("c.txt", C.CV_TLJ_C_DECLARED.encode())])
    c = s.get(f"/api/missions/{m['id']}/candidates").json()[0]
    out = _export(s, m["id"], c["id"])
    client = out["dossier_comet"].lower()
    for forbidden in ("à vérifier", "partiellement", "déclaré sans preuve", "non documenté", "grille v", "score", "empreinte", "interne", "niveau de fiabilité"):
        assert forbidden not in client, f"information interne dans le dossier client : {forbidden}"
    internal = out["dossier_internal"]
    assert internal.startswith("# INTERNE") and "Éléments restant à vérifier" in internal and "grille v1" in internal
    # seules les compétences CONFIRMÉES sont listées comme démontrées
    shown = re.search(r"## Compétences démontrées sur pièces\n([^\n]+)", out["dossier_comet"])
    confirmed = {d["skill"] for d in out["dossier_material"]["demonstrated_skills"] if d["level"] == "confirme_demontre"}
    assert (set(x.strip() for x in shown.group(1).rstrip(".").split(",")) if shown else set()) == confirmed


def test_only_validated_call_information_reaches_the_client_dossier(tm):
    s = tm(1)
    m = create_mission(s)
    freeze(s, m["id"])
    upload(s, m["id"], [("b.txt", C.CV_TLJ_B.encode())])
    c = s.get(f"/api/missions/{m['id']}/candidates").json()[0]
    base = f"/api/missions/{m['id']}/candidates/{c['id']}"
    s.patch(base, json={"acronym": "TL"})
    s.post(f"{base}/notes", json={"kind": "transcript", "text": "Candidat : Sur Kafka, j'ai rédigé un runbook d'astreinte et mis en place un dead letter queue pour 8 consommateurs."})
    before = s.get(f"{base}/export/dt").json()
    assert "runbook d'astreinte" not in before["dossier_comet"], "transcription automatique non validée : hors du dossier client"
    assert "runbook d'astreinte" in before["dossier_internal"]
    ev = s.get(base).json()["evidence"]
    for e in ev:
        s.post(f"/api/missions/{m['id']}/evidence/{e['id']}/review", json={"decision": "validated"})
    after = s.get(f"{base}/export/dt").json()
    assert "Précisions validées lors des échanges" in after["dossier_comet"] and "runbook d'astreinte" in after["dossier_comet"]


def test_the_acronym_remains_mandatory_and_the_name_never_appears(tm):
    s = tm(1)
    m = create_mission(s)
    freeze(s, m["id"])
    upload(s, m["id"], [("b.txt", C.CV_TLJ_B.encode())])
    c = s.get(f"/api/missions/{m['id']}/candidates").json()[0]
    assert s.get(f"/api/missions/{m['id']}/candidates/{c['id']}/export/dt").status_code == 409
    out = _export(s, m["id"], c["id"])
    assert "Dominique" not in out["dossier_comet"] and "Fictif" not in out["dossier_comet"] and "# Matière de dossier de compétences — TL" in out["dossier_comet"]
