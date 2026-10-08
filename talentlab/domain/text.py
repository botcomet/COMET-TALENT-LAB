"""Utilitaires de texte à offsets préservés.

``fold`` produit une version sans accents et en minuscules **de même longueur**
que l'original : toute position trouvée dans le texte replié est donc valide dans
le texte d'origine. C'est ce qui permet de citer un extrait exact (§6.5, test 10).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _fold_char(c: str) -> str:
    d = unicodedata.normalize("NFD", c)
    base = d[0] if d else c
    low = base.lower()
    return low if len(low) == 1 else c


def fold(text: str) -> str:
    """Minuscules sans accents, longueur identique à l'entrée."""
    return "".join(_fold_char(c) for c in text)


_WS = re.compile(r"[ \t ]+")


def normalize_text(text: str) -> str:
    """Nettoyage non destructif : retire NUL, uniformise espaces et fins de ligne."""
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("’", "'").replace("‘", "'")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip()


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str


_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-ÖØ-Þ0-9\"'(])|\n+")


def sentences(text: str, base: int = 0) -> list[Span]:
    """Découpe en phrases/puces avec offsets absolus dans ``text``."""
    spans: list[Span] = []
    pos = 0
    for m in _SENT_SPLIT.finditer(text):
        seg = text[pos:m.start()]
        _push(spans, seg, base + pos)
        pos = m.end()
    _push(spans, text[pos:], base + pos)
    return spans


def _push(spans: list[Span], seg: str, start: int) -> None:
    stripped = seg.strip(" \t•-–—*·▪►→")
    if not stripped:
        return
    lead = len(seg) - len(seg.lstrip(" \t•-–—*·▪►→"))
    s = start + lead
    spans.append(Span(s, s + len(stripped), stripped))


_SEP = r"[\s\-_/]*"


def term_pattern(term: str) -> str:
    """Motif regex (sur texte replié) pour un terme, avec frontières de mots.

    Les séparateurs internes (espace, tiret, souligné, slash) sont interchangeables :
    « design system », « design-system » et « design_system » sont un même terme.
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-_]+", fold(term.strip())) if p]
    plural = "(?:s|x)?" if parts and parts[-1][-1:].isalpha() and not parts[-1].endswith(("s", "x")) and len(parts[-1]) > 3 else ""
    return rf"(?<![a-z0-9+#]){_SEP.join(parts)}{plural}(?![a-z0-9+#])"


def find_term(folded: str, term: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(term_pattern(term), folded)]


def contains_any(folded: str, terms: list[str]) -> bool:
    return any(re.search(term_pattern(t), folded) for t in terms)


def excerpt(text: str, start: int, end: int, max_len: int = 400) -> str:
    """Tranche exacte du document source (jamais reformulée ni suffixée), pour
    que toute citation reste vérifiable par simple inclusion de chaîne."""
    return text[start:end].strip()[:max_len]
