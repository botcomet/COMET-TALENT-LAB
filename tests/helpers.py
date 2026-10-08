"""Aides de test : grilles figées, génération de PDF/DOCX réels (fictifs), fichiers défectueux."""
from __future__ import annotations

import io
from datetime import date

from talentlab.domain import grid as gd
from talentlab.domain.brief import analyze_brief
from talentlab.domain.cv_extract import parse_cv
from talentlab.domain.scoring import assess

TODAY = date(2026, 10, 8)


def reqs_for(title: str, brief: str, mutate=None):
    a = analyze_brief(title, brief)
    for r in a.requirements:
        r.validated = True
    if mutate:
        mutate(a.requirements)
    return a.requirements


def frozen_grid(title: str, brief: str, mutate=None, *, mission_id="m1", version=1):
    reqs = reqs_for(title, brief, mutate)
    grid = gd.propose_grid(mission_id, reqs, version=version)
    return gd.freeze(grid, reqs, "Recruteur Test", allow_unresolved_clarifications=True), reqs


def score(grid, cv_text: str, ext=None, facts=None):
    return assess(grid, parse_cv(cv_text, today=TODAY), ext, facts, today=TODAY)


def crit(asm, key_suffix: str):
    return next(c for c in asm.criteria if c.key.endswith(key_suffix))


# ---------------------------------------------------------------- fichiers
def make_pdf(text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in text.split("\n"):
        if y < 60:
            c.showPage()
            y = 800
        c.setFont("Helvetica", 9)
        c.drawString(40, y, line[:130])
        y -= 12
    c.save()
    return buf.getvalue()


def make_docx(text: str) -> bytes:
    import docx
    d = docx.Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_encrypted_pdf(text: str, password: str = "secret") -> bytes:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for p in PdfReader(io.BytesIO(make_pdf(text))).pages:
        w.add_page(p)
    w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def make_image_only_pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.rect(50, 50, 300, 300, fill=1)       # aucune couche de texte
    c.save()
    return buf.getvalue()
