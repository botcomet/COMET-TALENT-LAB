"""Booléens : AST, rendu, analyse syntaxique et contrôle (§5.3 étapes E, F, G).

Le moteur ne suppose rien sur le nombre de résultats : aucune estimation de
volume n'est produite (§5.2) — seulement des risques qualitatifs.
Le comportement exact de Turnover (casse, accents, caractères génériques,
priorité des opérateurs) n'est PAS confirmé : ``PlatformProfile.syntax_confirmed``
reste faux tant qu'un test réel sur la plateforme n'a pas eu lieu.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

from .text import fold


# ----------------------------------------------------------------- plateforme
@dataclass(frozen=True)
class PlatformProfile:
    key: str = "turnover"
    label: str = "Turnover"
    max_length: int = 250
    target_min: int = 30          # plage opérationnelle privilégiée (configurable, pas universelle)
    target_max: int = 100
    too_broad: int = 10_000
    syntax_confirmed: bool = False
    notes: str = ("Syntaxe Turnover non confirmée : tester la requête sur la plateforme (casse, accents, "
                  "guillemets, parenthèses imbriquées) avant d'en tirer des conclusions.")


TURNOVER = PlatformProfile()


# ----------------------------------------------------------------- AST
@dataclass(frozen=True)
class Term:
    text: str


@dataclass(frozen=True)
class Group:
    op: str                       # "AND" | "OR"
    items: tuple["Node", ...]


@dataclass(frozen=True)
class Not:
    item: "Node"


Node = Union[Term, Group, Not]

_PLAIN = re.compile(r"^[A-Za-zÀ-ÿ0-9.+#]+$")


def _term_text(t: str) -> str:
    t = t.strip()
    if _PLAIN.match(t) and t.upper() not in ("AND", "OR", "NOT"):
        return t
    return '"' + t.replace('"', "") + '"'


def render(node: Node, *, top: bool = True) -> str:
    if isinstance(node, Term):
        return _term_text(node.text)
    if isinstance(node, Not):
        inner = render(node.item, top=False)
        if isinstance(node.item, Group) and not inner.startswith("("):
            inner = f"({inner})"
        return f"NOT {inner}"
    items = [render(i, top=False) for i in node.items]
    if len(items) == 1:
        return items[0]
    body = f" {node.op} ".join(items)
    return body if top else f"({body})"


def AND(*items: Node) -> Node:
    flat: list[Node] = []
    for i in items:
        if isinstance(i, Group) and i.op == "AND":
            flat.extend(i.items)
        else:
            flat.append(i)
    return flat[0] if len(flat) == 1 else Group("AND", tuple(flat))


def OR(*items: Node) -> Node:
    flat: list[Node] = []
    for i in items:
        if isinstance(i, Group) and i.op == "OR":
            flat.extend(i.items)
        else:
            flat.append(i)
    seen, uniq = set(), []
    for f in flat:
        k = render(f)
        if k.lower() not in seen:
            seen.add(k.lower())
            uniq.append(f)
    return uniq[0] if len(uniq) == 1 else Group("OR", tuple(uniq))


def terms_of(node: Node) -> list[str]:
    if isinstance(node, Term):
        return [node.text]
    if isinstance(node, Not):
        return terms_of(node.item)
    return [t for i in node.items for t in terms_of(i)]


# ----------------------------------------------------------------- analyse
@dataclass
class Issue:
    code: str
    severity: str        # "error" | "warning"
    message: str
    position: int | None = None

    def to_dict(self):
        return {"code": self.code, "severity": self.severity, "message": self.message, "position": self.position}


_TOKEN = re.compile(r'\s*(?:(?P<lp>\()|(?P<rp>\))|"(?P<q>[^"]*)"|(?P<w>[^\s()"]+))')


def tokenize(q: str) -> tuple[list[tuple[str, str, int]], list[Issue]]:
    toks: list[tuple[str, str, int]] = []
    issues: list[Issue] = []
    if q.count('"') % 2 == 1:
        issues.append(Issue("UNBALANCED_QUOTE", "error", "Guillemet non refermé : l'expression exacte est mal délimitée.", q.rindex('"')))
        i = q.rindex('"')
        q = q[:i] + q[i + 1:]
    pos = 0
    while pos < len(q):
        if q[pos:].strip() == "":
            break
        m = _TOKEN.match(q, pos)
        if not m:
            issues.append(Issue("SYNTAX", "error", "Caractère inattendu dans la requête.", pos))
            break
        pos = m.end()
        if m.group("lp"):
            toks.append(("(", "(", m.start()))
        elif m.group("rp"):
            toks.append((")", ")", m.start()))
        elif m.group("q") is not None:
            toks.append(("T", m.group("q"), m.start()))
        else:
            w = m.group("w")
            toks.append((w, w, m.start()) if w in ("AND", "OR", "NOT") else ("T", w, m.start()))
    return toks, issues


class _Parser:
    def __init__(self, toks, issues):
        self.t, self.i, self.issues = toks, 0, issues
        self.mixed = False

    def peek(self):
        return self.t[self.i][0] if self.i < len(self.t) else None

    def eat(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def parse_or(self) -> Node | None:
        left = self.parse_and()
        items = [left] if left is not None else []
        while self.peek() == "OR":
            self.eat()
            right = self.parse_and()
            if right is None:
                self.issues.append(Issue("DANGLING_OPERATOR", "error", "OR sans opérande à droite."))
            else:
                items.append(right)
        if len(items) > 1:
            return OR(*items)
        return items[0] if items else None

    def parse_and(self) -> Node | None:
        left = self.parse_not()
        items = [left] if left is not None else []
        while self.peek() in ("AND", "T", "(", "NOT"):
            if self.peek() == "AND":
                self.eat()
            else:
                self.issues.append(Issue("MISSING_OPERATOR", "warning", "Deux termes consécutifs sans opérateur : interprétés comme AND ; l'écrire explicitement."))
            right = self.parse_not()
            if right is None:
                self.issues.append(Issue("DANGLING_OPERATOR", "error", "AND sans opérande à droite."))
                break
            items.append(right)
        if len(items) > 1:
            return AND(*items)
        return items[0] if items else None

    def parse_not(self) -> Node | None:
        if self.peek() == "NOT":
            self.eat()
            inner = self.parse_atom()
            if inner is None:
                self.issues.append(Issue("DANGLING_OPERATOR", "error", "NOT sans opérande."))
                return None
            return Not(inner)
        return self.parse_atom()

    def parse_atom(self) -> Node | None:
        p = self.peek()
        if p == "T":
            return Term(self.eat()[1])
        if p == "(":
            start = self.eat()[2]
            if self.peek() == ")":
                self.eat()
                self.issues.append(Issue("EMPTY_GROUP", "error", "Parenthèses vides.", start))
                return None
            inner = self.parse_or()
            if self.peek() == ")":
                self.eat()
            else:
                self.issues.append(Issue("UNBALANCED_PAREN", "error", "Parenthèse ouvrante sans fermeture.", start))
            return inner
        return None


def _mixed_precedence(toks: list[tuple[str, str, int]]) -> bool:
    """A OR B AND C sans parenthèses : la priorité réelle dépend de la plateforme."""
    depth_ops: list[set[str]] = [set()]
    for kind, _v, _p in toks:
        if kind == "(":
            depth_ops.append(set())
        elif kind == ")":
            if len(depth_ops) > 1:
                depth_ops.pop()
        elif kind in ("AND", "OR"):
            depth_ops[-1].add(kind)
            if len(depth_ops[-1]) > 1:
                return True
    return False


def parse(query: str) -> tuple[Node | None, list[Issue]]:
    toks, issues = tokenize(query)
    if not toks:
        issues.append(Issue("EMPTY_QUERY", "error", "Requête vide."))
        return None, issues
    opens = sum(1 for k, _, _ in toks if k == "(")
    closes = sum(1 for k, _, _ in toks if k == ")")
    p = _Parser(toks, issues)
    node = p.parse_or()
    if p.i < len(toks):
        if opens < closes:
            issues.append(Issue("UNBALANCED_PAREN", "error", "Parenthèse fermante en trop.", toks[p.i][2]))
        else:
            issues.append(Issue("SYNTAX", "error", "Contenu inattendu après la fin de l'expression.", toks[p.i][2]))
    if _mixed_precedence(toks):
        issues.append(Issue("MIXED_PRECEDENCE", "error",
                            "AND et OR mélangés au même niveau sans parenthèses : la priorité logique est ambiguë. Parenthéser chaque groupe OR."))
    # opérateurs écrits en minuscules : traités comme des mots par la plupart des plateformes
    for kind, val, pos in toks:
        if kind == "T" and val in ("and", "or", "not") :
            issues.append(Issue("LOWERCASE_OPERATOR", "warning", f"« {val} » en minuscules est lu comme un mot-clé, pas comme un opérateur.", pos))
    if any(k == "NOT" for k, _, _ in toks):
        issues.append(Issue("NOT_USED", "warning",
                            "Un filtre négatif peut exclure des CV pertinents : n'utiliser NOT que pour un faux positif identifié et justifié (§5.3 F)."))
    return node, issues


def validate(query: str, profile: PlatformProfile = TURNOVER) -> list[Issue]:
    _, issues = parse(query)
    if len(query) > profile.max_length:
        issues.append(Issue("TOO_LONG", "error", f"{len(query)} caractères : la limite {profile.label} configurée est {profile.max_length}."))
    return issues


def is_valid(query: str, profile: PlatformProfile = TURNOVER) -> bool:
    return not any(i.severity == "error" for i in validate(query, profile))


def canonical(node: Node | str | None) -> str:
    """Forme canonique (insensible à la casse, aux espaces et à l'ordre) pour détecter les requêtes identiques."""
    if node is None:
        return ""
    if isinstance(node, str):
        n, _ = parse(node)
        return canonical(n)

    def c(n: Node) -> str:
        if isinstance(n, Term):
            return fold(n.text).strip()
        if isinstance(n, Not):
            return "NOT(" + c(n.item) + ")"
        return n.op + "(" + "|".join(sorted(c(i) for i in n.items)) + ")"
    return c(node)


def term_set(node: Node | str | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, str):
        node, _ = parse(node)
        if node is None:
            return set()
    return {fold(t).strip() for t in terms_of(node)}


def similarity(a: Node | str, b: Node | str) -> float:
    sa, sb = term_set(a), term_set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


# ----------------------------------------------------------------- évaluation sur un texte connu
def _term_matches(term: str, folded: str) -> bool:
    from .text import term_pattern
    return re.search(term_pattern(term), folded) is not None


def matches(node: Node | str, text: str) -> bool:
    """La requête sélectionne-t-elle CE texte ? (évaluation locale — ne dit rien du nombre de résultats sur la plateforme.)"""
    if isinstance(node, str):
        n, issues = parse(node)
        if n is None or any(i.severity == "error" for i in issues):
            return False
        node = n
    folded = fold(text)

    def ev(n: Node) -> bool:
        if isinstance(n, Term):
            return _term_matches(n.text, folded)
        if isinstance(n, Not):
            return not ev(n.item)
        return all(ev(i) for i in n.items) if n.op == "AND" else any(ev(i) for i in n.items)
    return ev(node)


def failing_groups(node: Node | str, text: str) -> list[str]:
    """Pour un profil connu comme pertinent : quels groupes de premier niveau de la requête ne le reconnaissent pas ?"""
    if isinstance(node, str):
        n, _ = parse(node)
    else:
        n = node
    if n is None:
        return []
    items = list(n.items) if isinstance(n, Group) and n.op == "AND" else [n]
    return [render(i, top=False) for i in items if not matches(i, text)]
