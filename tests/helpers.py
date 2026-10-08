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


def make_heavy_pdf(pages: int = 40, ops: int = 60_000) -> bytes:
    """PDF valide mais coûteux à lire : un flux compressé de ``ops`` opérateurs « (a) Tj » partagé par ``pages`` pages (petit fichier, énorme travail)."""
    import zlib
    content = zlib.compress(b"BT /F1 9 Tf 10 700 Td " + b"(a) Tj " * ops + b"ET")
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{5 + i} 0 R" for i in range(pages))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content + b"\nendstream")
    for _ in range(pages):
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 3 0 R >> >> >>")
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, start=1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def make_docx_bomb(uncompressed_mb: int = 20) -> bytes:
    """DOCX valide de quelques dizaines de Ko dont document.xml fait ``uncompressed_mb`` Mo une fois décompressé."""
    import io
    import zipfile
    para = b"<w:p><w:r><w:t>Developpeur Java Kafka Spring Boot experience</w:t></w:r></w:p>"
    xml = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + para * (uncompressed_mb * 1024 * 1024 // len(para)) + b"</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", xml)
    return buf.getvalue()
