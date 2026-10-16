"""Historique (§20) : recherche TRANSVERSE dans les missions auxquelles l'utilisateur a accès.

Objectif : retrouver une mission passée, une exigence déjà rencontrée, une requête déjà écrite — pour s'en inspirer, jamais pour copier un candidat.

Garde-fous (testés) :
- seules les missions possédées par l'utilisateur ou partagées avec lui sont parcourues ; le niveau de partage borne ce qui est cherché ;
- au niveau « stratégie », on cherche uniquement ce que ce niveau donne à voir : titre, client, exigences non contextuelles, requêtes — jamais
  les notes libres, les sources brutes, les candidats ni le contenu des CV ;
- les CV et les candidats ne sont JAMAIS cherchés, quel que soit le niveau : cet écran sert à capitaliser sur la méthode, pas à fouiller des personnes ;
- le texte recherché n'est pas consigné dans le journal d'audit (il peut contenir une information sensible) ;
- résultats bornés (missions, accroches par mission, longueur de la requête) : un terme très courant ne produit pas une réponse démesurée.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain.enums import Category
from ..domain.text import fold
from ..models import Mission, Search, Share, User
from . import missions as ms

MIN_QUERY = 2
MAX_QUERY = 100
MAX_TERMS = 6
MAX_MISSIONS = 25
MAX_HITS_PER_MISSION = 8
SNIPPET = 160


def _terms(q: str) -> list[str]:
    t = [x for x in fold(q).split() if x]
    return t[:MAX_TERMS]


def _match(terms: list[str], text: str) -> bool:
    f = fold(text)
    return bool(terms) and all(t in f for t in terms)


def _snippet(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= SNIPPET else t[: SNIPPET - 1].rstrip() + "…"


def search(db: Session, user: User, q: str) -> dict[str, Any]:
    """``q`` doit avoir été validée par l'appelant (longueur) ; les termes sont tous requis (ET) dans un même champ."""
    terms = _terms(q)
    shared = {s.mission_id: s.scope for s in db.scalars(select(Share).where(Share.user_id == user.id))}
    missions = db.scalars(select(Mission).where(or_(Mission.owner_id == user.id, Mission.id.in_(list(shared) or [""]))).order_by(Mission.updated_at.desc()))
    results: list[dict[str, Any]] = []
    truncated = False
    for m in missions:
        level = "owner" if m.owner_id == user.id else shared[m.id]
        hits: list[dict[str, str]] = []
        if _match(terms, m.title):
            hits.append({"kind": "mission", "label": "Intitulé", "text": _snippet(m.title)})
        if m.client and _match(terms, m.client):
            hits.append({"kind": "mission", "label": "Client", "text": _snippet(m.client)})
        reqs, _conflicts = ms.effective(db, m.id)
        for r in reqs:
            if level == "strategie" and r.category == Category.CONTEXTUEL:
                continue                                          # le niveau « stratégie » ne montre pas les informations contextuelles
            if _match(terms, r.label):
                hits.append({"kind": "exigence", "label": f"Exigence ({r.category.value.replace('_', ' ')})", "text": _snippet(r.label)})
        for s in db.scalars(select(Search).where(Search.mission_id == m.id).order_by(Search.created_at)):
            if _match(terms, s.query):
                hits.append({"kind": "recherche", "label": f"Requête {s.strategy} v{s.version}", "text": _snippet(s.query), "search_id": s.id})
            elif level != "strategie" and s.note and _match(terms, s.note):
                hits.append({"kind": "recherche", "label": f"Note de la requête {s.strategy} v{s.version}", "text": _snippet(s.note), "search_id": s.id})
        if not hits:
            continue
        if len(results) >= MAX_MISSIONS:
            truncated = True
            break
        results.append({"mission": {"id": m.id, "title": m.title, "client": m.client, "access": level, "status": m.status, "updated_at": m.updated_at.isoformat()},
                        "hits": hits[:MAX_HITS_PER_MISSION], "hit_count": len(hits)})
    return {"query": q.strip(), "results": results, "truncated": truncated,
            "scope_note": "Recherche dans vos missions et celles partagées avec vous : titres, clients, exigences et requêtes. "
                          "Les candidats et le contenu des CV ne sont jamais cherchés ; au niveau « stratégie », les notes libres ne le sont pas non plus."}
