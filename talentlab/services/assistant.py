"""Assistant conversationnel contextuel (§22).

Il comprend le contexte de la mission, explique ses conséquences et PROPOSE : aucun changement de critère, de
score ou d'information validée n'est appliqué sans confirmation d'un utilisateur autorisé (voir ``confirm``).
Le routage d'intention est déterministe ; une couche LLM optionnelle peut enrichir la reformulation (voir ``llm``).
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..domain import coach
from ..domain.call_facts import extract_facts
from ..domain.search_optimizer import Feedback, optimize, variant_from_dict
from ..domain.text import fold
from ..models import Candidate, Mission, Proposal, Search, User, now
from . import matching as mt
from . import missions as ms
from . import searches as sr

_REF = re.compile(r"\bc-?\d{3,4}\b", re.I)


def _candidates_in(db: Session, m: Mission, msg: str, explicit: list[str] | None) -> list[Candidate]:
    refs = {r.upper().replace("C", "C-", 1) if "-" not in r else r.upper() for r in _REF.findall(msg)}
    out: list[Candidate] = []
    allc = list(db.scalars(select(Candidate).where(Candidate.mission_id == m.id).order_by(Candidate.created_at)))
    for c in allc:
        if c.id in (explicit or []) or c.ref in refs:
            out.append(c)
    return out


def _latest_search(db: Session, m: Mission, search_id: str | None) -> Search | None:
    if search_id:
        s = db.get(Search, search_id)
        return s if s and s.mission_id == m.id else None
    return db.scalar(select(Search).where(Search.mission_id == m.id, Search.strategy.in_(("balanced", "strict"))).order_by(Search.created_at.desc()).limit(1))


def handle(db: Session, user: User, m: Mission, level: str, message: str, *, candidate_ids: list[str] | None = None, search_id: str | None = None) -> dict[str, Any]:
    f = fold(message)
    cands = _candidates_in(db, m, message, candidate_ids)
    editor = level in ("edition", "owner")
    reply: dict[str, Any] = {"reply": "", "proposals": [], "data": {}}

    # 1. « Rends la recherche moins stricte / plus stricte »
    if re.search(r"moins (stricte|restrictive)|elargi|assouplis|plus large|trop restrictive", f):
        s = _latest_search(db, m, search_id)
        if s is None:
            reply["reply"] = "Aucune recherche à assouplir : génère d'abord les trois recherches de la mission."
            return reply
        reqs, _ = ms.effective(db, m.id)
        v = variant_from_dict(s.variant)
        prop = optimize(v, Feedback(result_count=0), reqs, title=m.title, history=sr.lineage_queries(db, s), profile=sr.platform())
        if prop.variant is None:
            reply["reply"] = "Toutes les transformations automatiques ont déjà été essayées sur cette recherche : " + prop.needs_human
            return reply
        p = Proposal(mission_id=m.id, kind="search_variant", created_by=user.id,
                     payload={"parent_search_id": s.id, "variant": prop.variant.to_dict(), "modification": prop.modification},
                     explanation=prop.modification, consequences=prop.tradeoffs + ["Les exigences client et la grille de matching ne changent pas."])
        db.add(p)
        db.flush()
        reply["reply"] = (f"Proposition d'assouplissement (à la demande, sans retour d'exécution réel) :\n{prop.variant.query}\n\n{prop.modification}\n"
                          "Conséquences : " + " ".join(prop.tradeoffs) + "\nConfirmer pour l'ajouter à l'historique des recherches.")
        reply["proposals"].append(p)
        return reply

    # 2. « Pourquoi as-tu donné 85 à ce candidat ? »
    if re.search(r"pourquoi", f) and (cands or re.search(r"\b\d{2,3}\b", f)) and re.search(r"note|score|donne|\d{2,3}", f):
        if not cands:
            reply["reply"] = "Précise le candidat (référence du type C-0001) dont tu veux expliquer le score."
            return reply
        c = cands[0]
        a = mt.latest_assessment(db, c.id)
        if a is None:
            reply["reply"] = f"{c.ref} n'a pas encore d'évaluation sur une grille figée."
            return reply
        r = a.result
        gaps = [x for x in r["criteria"] if x["level"] != "confirme_demontre"]
        gaps.sort(key=lambda x: -(x["weight"] - x["points"]))
        top = ", ".join(f"« {x['label']} » ({x['points']}/{x['weight']}, {x['level'].replace('_', ' ')})" for x in gaps[:3])
        reply["reply"] = (f"{c.ref} — score documenté {r['score_documented']}/100 (potentiel à confirmer {r['score_potential']}) sur la grille v{r['grid_version']}. {r['summary']}\n"
                          f"Ce qui coûte le plus de points : {top}.\n" + "\n".join(coach.explain_criterion(x) for x in gaps[:2]))
        reply["data"] = {"candidate": c.ref, "assessment_id": a.id}
        return reply

    # 3. « Compare A et B »
    if re.search(r"compar", f):
        if len(cands) < 2:
            reply["reply"] = "Indique au moins deux candidats par leur référence (ex. C-0001 et C-0002)."
            return reply
        cmp_ = mt.compare(db, m, [c.id for c in cands[:5]])
        w = cmp_["why"]
        lines = [f"Classement sur la grille v{cmp_['grid_version']} : " + " > ".join(f"{n} ({cmp_['summary'][n]['score']})" for n in cmp_["ranking"])]
        for d in w.get("main_differences", []):
            lines.append(f"- {d['criterion']} : {w['leader']} {d['leader_points']} pts ({d['leader_level'].replace('_', ' ')}) vs {w['second']} {d['second_points']} pts ({d['second_level'].replace('_', ' ')})")
        reply["reply"] = "\n".join(lines)
        reply["data"] = cmp_
        return reply

    # 4. « Quels points vérifier pendant l'appel ? »
    if re.search(r"verifier|appel|qualifi|questions?", f) and cands:
        q = mt.questions(db, user, m, cands[0])
        reply["reply"] = f"Questions prioritaires pour {cands[0].ref} :\n" + "\n".join(f"{i + 1}. {x['text']}" for i, x in enumerate(q["priority"]))
        reply["data"] = q
        return reply

    # 5. « Le client vient de préciser que Kafka Connect est impératif »
    if re.search(r"client|il (veut|exige)|precis", f) and re.search(r"imperatif|indispensable|obligatoire|exige|veut|souhait|important", f):
        if not editor:
            raise HTTPException(403, "Modifier le besoin exige des droits d'édition sur la mission.")
        facts = [x for x in extract_facts(message, "client_brief_note") if x.kind == "client_requirement" and x.topic_key]
        if not facts:
            reply["reply"] = "Je n'ai pas identifié de compétence ou d'activité du référentiel dans cette précision : la saisir comme source « précision client » ou ajouter l'exigence manuellement."
            return reply
        n_assessed = len({a.candidate_id for a in db.scalars(select(mt.Assessment).where(mt.Assessment.mission_id == m.id))})
        for fct in facts:
            p = Proposal(mission_id=m.id, kind="requirement_change", created_by=user.id,
                         payload={"topic_key": fct.topic_key, "topic_label": fct.topic_label, "category_hint": fct.requirement_hint["category_hint"], "relayed": True,
                                  "statement": message, "author": user.display_name, "source_kind": "assistant"},
                         explanation=f"Exigence du poste : « {fct.topic_label} » ({fct.requirement_hint['category_hint'].replace('_', ' ')}). Ce n'est pas une compétence du candidat.",
                         consequences=["Nouvelle exigence validée par toi (provenance : précision relayée, à dater et à faire confirmer par le client).",
                                       "Une nouvelle version de grille devra être créée puis validée ; les poids changent.",
                                       f"{n_assessed} candidat(s) déjà évalué(s) devront être réévalués sur la nouvelle version, avec comparaison avant/après."])
            db.add(p)
            reply["proposals"].append(p)
        db.flush()
        reply["reply"] = ("Je propose de modifier le besoin :\n" + "\n".join(f"- {p.explanation}" for p in reply["proposals"])
                          + "\nConséquences : " + " ".join(reply["proposals"][0].consequences) + "\nRien n'est appliqué tant que tu n'as pas confirmé.")
        return reply

    # 6. « Actualise le scoring selon cette précision »
    if re.search(r"actualis|reevalu|recalcul|mets a jour", f):
        if not editor:
            raise HTTPException(403, "Réévaluer exige des droits d'édition sur la mission.")
        frozen = ms.latest_frozen(db, m.id)
        draft = ms.current_draft(db, m.id)
        if draft is not None and (frozen is None or draft.version > frozen.version):
            reply["reply"] = (f"Une version de grille v{draft.version} est en brouillon : la valider (poids = 100, exigences validées) avant de réévaluer. "
                              "Les candidats gardent leur évaluation sur la v" + str(frozen.version if frozen else "—") + " jusque-là.")
            return reply
        p = Proposal(mission_id=m.id, kind="new_grid_version", created_by=user.id,
                     payload={"reason": "Actualisation demandée à l'assistant suite à une précision du besoin"},
                     explanation="Créer une nouvelle version de grille (brouillon) à partir des exigences effectives actuelles ; le recruteur la valide ensuite, puis les candidats sont réévalués avec diff.",
                     consequences=["La grille actuelle n'est pas modifiée (elle reste figée).", "Aucun score ne change avant validation de la nouvelle grille."])
        db.add(p)
        db.flush()
        reply["reply"] = p.explanation + "\nConséquences : " + " ".join(p.consequences)
        reply["proposals"].append(p)
        return reply

    # 7. « Quels profils restent intéressants ? »
    if re.search(r"restent? interessant|quels profils|meilleurs? (profils|candidats)|qui (garder|presenter)", f):
        rows = [mt.candidate_brief(db, c) for c in db.scalars(select(Candidate).where(Candidate.mission_id == m.id))]
        rows = [r for r in rows if r.get("assessment")]
        keep = [r for r in rows if r["assessment"]["tier"] in ("tres_interessant", "interessant") or (r["assessment"]["tier"] == "a_qualifier" and r["assessment"]["score_potential"] >= 80)]
        keep.sort(key=lambda r: -r["assessment"]["score_documented"])
        if not keep:
            reply["reply"] = "Aucun profil n'atteint le niveau « intéressant » ou « à qualifier avec potentiel ». Aucun n'est rejeté : tous restent consultables et réévaluables."
        else:
            reply["reply"] = "Profils à garder (jamais un rejet automatique) :\n" + "\n".join(
                f"- {r['ref']} : {r['assessment']['score_documented']}/100 (potentiel {r['assessment']['score_potential']}), {r['assessment']['tier'].replace('_', ' ')}"
                + (f" — impératifs à vérifier : {', '.join(r['assessment']['open_mandatory'])}" if r["assessment"]["open_mandatory"] else "") for r in keep)
        reply["data"] = {"profiles": [r["ref"] for r in keep]}
        return reply

    # 8. repli pédagogique
    c = coach.answer(message)
    if c["answers"] or c["related"]:
        reply["reply"] = "\n\n".join(f"{x['title']} — {x['body']}" for x in (c["answers"] + c["related"])[:3])
    else:
        reply["reply"] = ("Je peux : assouplir une recherche, expliquer un score, comparer des candidats, préparer les questions d'un appel, proposer une modification du besoin "
                          "(« le client vient de préciser que X est impératif ») et actualiser le scoring. Les changements importants sont proposés puis confirmés par toi.")
    return reply


def confirm(db: Session, user: User, m: Mission, p: Proposal) -> dict[str, Any]:
    if p.status != "pending":
        raise HTTPException(409, "Proposition déjà traitée.")
    result: dict[str, Any] = {}
    if p.kind == "requirement_change":
        row = ms.apply_requirement_proposal(db, user, m, p)
        result = {"requirement_id": row.id, "next": "Créer une nouvelle version de grille (POST /grid/new-version) puis la valider avant de réévaluer."}
    elif p.kind == "search_variant":
        parent = db.get(Search, p.payload["parent_search_id"])
        from ..domain.search_optimizer import variant_from_dict as vfd
        v = vfd(p.payload["variant"])
        v.explanation = p.payload["variant"].get("explanation", {})
        top = max(s.version for s in db.scalars(select(Search).where(Search.mission_id == m.id, Search.lineage == parent.lineage)))
        new = sr._store(db, user, m, v, lineage=parent.lineage, version=top + 1, parent=parent.id, modification=p.payload["modification"] + " (à la demande de l'assistant)", grid_version=parent.grid_version)
        result = {"search_id": new.id}
    elif p.kind == "new_grid_version":
        row, diff = ms.new_grid_version(db, user, m, p.payload["reason"])
        result = {"grid_id": row.id, "version": row.version, "diff": diff}
    else:
        raise HTTPException(422, "Type de proposition inconnu.")
    p.status, p.decided_by, p.decided_at = "confirmed", user.id, now()
    audit.log(db, user.id, "proposal.confirm", "proposal", p.id, m.id, kind=p.kind)
    return result


def reject(db: Session, user: User, m: Mission, p: Proposal) -> None:
    if p.status != "pending":
        raise HTTPException(409, "Proposition déjà traitée.")
    p.status, p.decided_by, p.decided_at = "rejected", user.id, now()
    audit.log(db, user.id, "proposal.reject", "proposal", p.id, m.id, kind=p.kind)
