"""Exports : informations de qualification validées pour DT Editor (§19) et descriptif complet pour les échanges candidats (§19.4).

Talent Lab ne rédige pas le dossier de compétences : il livre une matière fidèle, anonymisée et sourcée.
Règles de fidélité (§19.3) : aucune responsabilité, technologie, date ou résultat inventé ; aucune compétence
déplacée d'un projet à un autre ; ce qui n'est pas validé reste dans « à vérifier ».
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.cv_extract import parse_cv
from ..domain.enums import Category, Level
from ..domain.evidence import _items
from ..domain.text import fold
from ..models import Candidate, Document, Evidence, EvidenceReview, Mission, MissionSource
from . import matching as mt
from . import missions as ms

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?0?\d\)?[\s.-]?){4,6}\d{2}(?!\d)")
_URL = re.compile(r"(?:https?://|www\.)\S+|linkedin\.com/\S+", re.I)


def scrub(text: str) -> str:
    """Retire les coordonnées directes : un dossier présenté au client ne les expose jamais (§19.2)."""
    t = _URL.sub("[lien retiré]", text)
    t = _EMAIL.sub("[email retiré]", t)
    return _PHONE.sub(lambda m: "[numéro retiré]" if len(re.sub(r"\D", "", m.group(0))) >= 9 else m.group(0), t)


def _bullets(text: str, a: int, b: int) -> list[tuple[int, int, str]]:
    out = []
    for ia, ib in _items(text, a, b):
        out.append((ia, ib, text[ia:ib].strip(" \t•-–—*·▪►→")))
    return out


def dt_export(db: Session, m: Mission, cand: Candidate, *, relevant_only: bool = True) -> dict[str, Any]:
    if not cand.acronym.strip():
        raise HTTPException(409, "Renseigner l'acronyme autorisé du consultant avant l'export : le nom et le prénom ne sont jamais exportés.")
    a = mt.latest_assessment(db, cand.id)
    doc = db.scalar(select(Document).where(Document.candidate_id == cand.id, Document.status == "done").order_by(Document.created_at.desc()).limit(1))
    if a is None or doc is None or doc.text is None:
        raise HTTPException(409, "Aucune évaluation exportable : le CV doit d'abord être analysé sur la grille figée.")
    res = a.result
    text = doc.text
    parsed = parse_cv(text, today=date.today(), extraction_quality=doc.extraction_quality or "ok")
    # preuves CV localisées → rattachement des réalisations aux critères (sans rien déplacer d'une expérience à l'autre)
    ev_spans: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    for c in res["criteria"]:
        for e in c["evidence"]:
            if e["source"] == "cv" and e.get("end", 0) > e.get("start", 0):
                ev_spans.append((e["start"], e["end"], c, e))
    experiences = []
    for exp in parsed.experiences:
        items = _bullets(text, exp.body_span[0], exp.body_span[1])
        env = [text[a_:b_].split(":", 1)[-1].strip() for a_, b_ in exp.env_spans]
        ctx = next((s for _, _, s in items if re.match(r"^contexte\s*[:：]", s, re.I)), None)
        achievements = []
        for ia, ib, s in items:
            if re.match(r"^(contexte|environnement|stack|technologies|outils)\b.*[:：]", s, re.I) and not re.match(r"^\s*[-•]", text[ia:ib]):
                continue
            links = [c["label"] for st, en, c, e in ev_spans if st < ib and en > ia and c["level"] in (Level.CONFIRMED.value, Level.PARTIAL.value, Level.DECLARED.value)]
            achievements.append({"statement": scrub(s), "evidences_criteria": sorted(set(links)), "source": "CV", "verbatim": True})
        relevant = any(x["evidences_criteria"] for x in achievements) or any(exp.span[0] <= st < exp.span[1] for st, _, _, _ in ev_spans)
        if relevant_only and not relevant:
            continue
        experiences.append({
            "company": exp.company, "title": exp.title, "start": exp.start, "end": exp.end, "is_current": exp.is_current,
            "dates_known": not exp.dates_unknown, "months": exp.months, "relevant": relevant,
            "context": scrub(ctx.split(":", 1)[-1].strip()) if ctx else None,
            "realisations": achievements,
            "environnement_technique": [t.strip() for t in re.split(r"[,;]", scrub(env[0])) if t.strip()] if env else [],
            "environnement_technique_phrase": scrub(env[0]) if env else None,
        })
    demonstrated = [{"skill": c["label"], "level": c["level"], "last_used": c.get("last_used"),
                     "evidence": [{"excerpt": scrub(e["excerpt"]), "company": e.get("company", ""), "period": e.get("period", "")} for e in c["evidence"][:2] if e["source"] == "cv"]}
                    for c in res["criteria"] if c["level"] in (Level.CONFIRMED.value, Level.PARTIAL.value)]
    # informations issues des échanges : seules les informations VALIDÉES entrent dans la matière du dossier
    rows = list(db.scalars(select(Evidence).where(Evidence.candidate_id == cand.id, ~Evidence.subject_key.like("constraint:%")).order_by(Evidence.created_at)))
    decisions = {rv.evidence_id: rv.decision for rv in db.scalars(select(EvidenceReview).where(EvidenceReview.evidence_id.in_([r.id for r in rows] or [""])).order_by(EvidenceReview.at))}
    validated_calls, unverified_calls = [], []
    for r in rows:
        d = decisions.get(r.id)
        item = {"statement": scrub(r.excerpt), "topic": r.subject_key.split(":", 1)[-1], "kind": r.kind, "source": r.source, "date": r.source_date.isoformat() if r.source_date else None,
                "evidence_id": r.id}
        if d == "rejected":
            continue
        (validated_calls if (d == "validated" or r.source == "saisie_recruteur") else unverified_calls).append(item)
    todo = [{"criterion": c["label"], "level": c["level"], "why": c["justification"][:240], "open_questions": c.get("open_questions", [])}
            for c in res["criteria"] if c["level"] != Level.CONFIRMED.value]
    resp = [{"responsibility": c["label"], "level": c["level"], "evidence": scrub(c["evidence"][0]["excerpt"]) if c["evidence"] else ""}
            for c in res["criteria"] if c["dimension"] == "responsabilite" and c["level"] in (Level.CONFIRMED.value, Level.PARTIAL.value)]
    deliverables = sorted({d for c in res["criteria"] for e in c["evidence"] for d in (e.get("signals") or {}).get("deliverables", [])})
    mission_ctx = (m.analysis or {})
    reqs, _ = ms.effective(db, m.id)
    out = {
        "schema": "comet.talentlab.dt-export/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mission": {"title": m.title, "context": [scrub(x) for x in mission_ctx.get("context", [])], "objectives": [scrub(x) for x in mission_ctx.get("objectives", [])],
                    "modalities": mission_ctx.get("modalities", {}),
                    "requirements": [{"label": r.label, "category": r.category.value} for r in reqs if r.category in (Category.ELIMINATOIRE, Category.IMPERATIF, Category.DIFFERENCIANT, Category.SOUHAITABLE)]},
        "candidate": {"acronym": cand.acronym.strip().upper(), "reference": cand.ref},
        "dossier_material": {
            "experiences": experiences,
            "demonstrated_skills": demonstrated,
            "responsibilities": resp,
            "deliverables": deliverables,
            "validated_call_information": validated_calls,
            "note": "Matière fidèle au CV et aux informations validées : rien n'a été ajouté, déplacé ou reformulé. Les informations d'échange ne sont pas rattachées d'office à une expérience : à intégrer par le recruteur dans l'expérience concernée.",
        },
        "internal_only": {
            "assessment": {"id": a.id, "version": a.version, "grid_version": res["grid_version"], "grid_hash": res["grid_hash"],
                           "score_documented": res["score_documented"], "tier": res["tier"], "uncertainty": res["uncertainty"]},
            "to_verify": todo, "unvalidated_call_information": unverified_calls,
            "constraints": res["constraints"], "date_flags": res["flags"],
            "provenance_note": "Provenance, niveaux de preuve et éléments à vérifier restent dans Talent Lab ; ils n'ont pas vocation à figurer dans le dossier client.",
        },
    }
    out["markdown"] = _markdown(out)
    return out


def _markdown(x: dict[str, Any]) -> str:
    L = [f"# Matière de dossier — {x['candidate']['acronym']}", f"Mission : {x['mission']['title']}", ""]
    dm = x["dossier_material"]
    if dm["demonstrated_skills"]:
        L += ["## Compétences démontrées"] + [f"- {s['skill']} ({s['level'].replace('_', ' ')})" for s in dm["demonstrated_skills"]] + [""]
    L.append("## Expériences")
    for e in dm["experiences"]:
        dates = f"{e['start'] or '?'} → {'en cours' if e['is_current'] else (e['end'] or '?')}" if e["dates_known"] else "dates non précisées"
        L += [f"### {e['company'] or '(entreprise non précisée)'} — {e['title'] or '(intitulé non précisé)'} — {dates}"]
        if e["context"]:
            L += ["**Contexte**", e["context"]]
        L += ["**Réalisations**"] + [f"- {r['statement']}" for r in e["realisations"]]
        if e["environnement_technique_phrase"]:
            L += ["**Environnement technique**", e["environnement_technique_phrase"]]
        L.append("")
    if dm["validated_call_information"]:
        L += ["## Informations validées issues des échanges"] + [f"- {i['statement']} ({i['topic']})" for i in dm["validated_call_information"]] + [""]
    io = x["internal_only"]["to_verify"]
    if io:
        L += ["## À vérifier (interne — ne pas transmettre au client)"] + [f"- {t['criterion']} : {t['level'].replace('_', ' ')}" for t in io]
    return "\n".join(L)


# ----------------------------------------------------------------- descriptif complet pour candidats (§19.4)
def candidate_description(db: Session, m: Mission, *, include_client: bool = False) -> dict[str, Any]:
    an = m.analysis or {}
    mods = an.get("modalities", {})
    reqs, _ = ms.effective(db, m.id)
    NC = "Non communiqué"
    def lst(cat: tuple[Category, ...]) -> list[str]:
        return [r.label for r in reqs if r.category in cat and r.dimension != "contrainte" and r.status == "active"]
    clar = [s for s in db.scalars(select(MissionSource).where(MissionSource.mission_id == m.id, MissionSource.kind.in_(("client_precision_validee", "client_imperatif_confirme"))).order_by(MissionSource.created_at))]
    pres = f"{mods['onsite_days_per_week']} jour(s) par semaine sur site" if "onsite_days_per_week" in mods else NC
    fields = {
        "titre": m.title, "client": m.client if include_client else "Client confidentiel",
        "contexte": an.get("context") or [NC], "objectifs": an.get("objectives") or [NC],
        "responsabilites": [a["label"] for a in an.get("activities", [])] or [NC],
        "exigences_imperatives": lst((Category.ELIMINATOIRE, Category.IMPERATIF)) or [NC],
        "exigences_souhaitees": lst((Category.DIFFERENCIANT, Category.SOUHAITABLE)) or [NC],
        "localisation": mods.get("city") or NC, "presence": pres, "teletravail": (f"{mods['remote_days_per_week']} jour(s) par semaine" if "remote_days_per_week" in mods else ("Full remote" if mods.get("remote") == "full" else NC)),
        "duree": mods.get("duration") or NC, "demarrage": mods.get("start") or NC,
        "astreintes": ("Oui" if mods.get("astreinte") is True else ("Non" if mods.get("astreinte") is False else NC)),
        "tjm": (f"{mods['tjm_max']} € (maximum communiqué)" if "tjm_max" in mods else NC), "charge": NC,
        "langues": mods.get("english") or NC,
        "precisions_client": [f"{s.source_date or ''} — {scrub(s.text)[:400]}" for s in clar] or [NC],
    }
    def blk(title: str, v: Any) -> str:
        return f"{title}\n" + ("\n".join(f"- {i}" for i in v) if isinstance(v, list) else str(v))
    text = "\n\n".join([
        f"{fields['titre']} — {fields['client']}",
        blk("Contexte", fields["contexte"]), blk("Objectifs", fields["objectifs"]), blk("Responsabilités", fields["responsabilites"]),
        blk("Exigences impératives", fields["exigences_imperatives"]), blk("Souhaité / apprécié", fields["exigences_souhaitees"]),
        "Modalités\n" + "\n".join(f"- {k} : {fields[k]}" for k in ("localisation", "presence", "teletravail", "duree", "demarrage", "astreintes", "tjm", "charge", "langues")),
        blk("Précisions client", fields["precisions_client"]),
    ])
    return {"fields": fields, "text": text, "missing": [k for k, v in fields.items() if v == NC or v == [NC]],
            "note": "Document préparé pour un échange : aucun email n'est envoyé automatiquement ; aucune condition absente n'est inventée (« Non communiqué »)."}
