"""Vérification des affirmations d'une couche IA (test 10, §22, §25).

Le modèle n'est jamais une source de vérité ni un évaluateur : il peut proposer des passages (« désigner »).
Toute affirmation doit citer un extrait **verbatim** du document ; sinon elle est rétrogradée en hypothèse et
n'entre pas dans le score. Le niveau retenu est RECALCULÉ par le classifieur déterministe sur la citation,
plafonné par ce que le modèle affirmait.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import lexicon as lx
from .enums import Level, LEVEL_ORDER
from .evidence import analyze_window, classify, skill_for
from .text import fold


@dataclass
class Claim:
    criterion_key: str
    label: str
    quote: str
    asserted_level: Level


@dataclass
class VerifiedClaim:
    claim: Claim
    status: str                 # verified | downgraded
    level: Level | None         # None : n'entre pas dans le score
    reason: str
    start: int = -1
    end: int = -1

    def to_dict(self) -> dict[str, Any]:
        return {"criterion_key": self.claim.criterion_key, "label": self.claim.label, "quote": self.claim.quote, "asserted_level": self.claim.asserted_level.value,
                "status": self.status, "level": self.level.value if self.level else None, "reason": self.reason, "start": self.start, "end": self.end}


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """Position de la citation dans le texte source (insensible aux espaces et à la casse/accents, jamais approximative)."""
    q = _squash(fold(quote))
    if len(q) < 12:
        return None
    # construit une version repliée du texte avec correspondance d'offsets
    folded = fold(text)
    compact: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(folded):
        if ch.isspace():
            if not prev_space:
                compact.append(" ")
                idx.append(i)
            prev_space = True
        else:
            compact.append(ch)
            idx.append(i)
            prev_space = False
    flat = "".join(compact)
    pos = flat.find(q)
    if pos < 0:
        return None
    return idx[pos], idx[min(len(idx) - 1, pos + len(q) - 1)] + 1


def verify_claims(claims: list[Claim], text: str) -> list[VerifiedClaim]:
    out: list[VerifiedClaim] = []
    for c in claims:
        if not c.quote.strip():
            out.append(VerifiedClaim(c, "downgraded", None, "Aucune citation : affirmation non étayée, rétrogradée en hypothèse (hors score)."))
            continue
        loc = locate(text, c.quote)
        if loc is None:
            out.append(VerifiedClaim(c, "downgraded", None, "Citation introuvable dans le document source : affirmation rétrogradée en hypothèse (hors score)."))
            continue
        sk = skill_for(c.criterion_key, [], c.label)
        a = max(0, text.rfind("\n", 0, loc[0]) + 1)
        b = text.find("\n", loc[1])
        b = len(text) if b < 0 else b
        window = text[a:b]
        if not lx.mentions(sk, window):
            out.append(VerifiedClaim(c, "downgraded", None, f"La citation ne mentionne pas « {c.label} » : le lien avec le critère n'est pas établi.", loc[0], loc[1]))
            continue
        kind = "activity" if sk.kind == "activity" else ("domain" if sk.kind == "domain" else "skill")
        lvl, why = classify(analyze_window(window, sk, own_sentence=window), sk, kind)
        eff = lvl if LEVEL_ORDER[lvl] <= LEVEL_ORDER[c.asserted_level] else c.asserted_level
        note = f"Niveau recalculé sur la citation : {why}." + (" Plafonné au niveau affirmé." if eff != lvl else "")
        out.append(VerifiedClaim(c, "verified", eff, note, loc[0], loc[1]))
    return out
