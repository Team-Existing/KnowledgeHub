"""Shared test helpers and fixtures data."""
from __future__ import annotations

from typing import Any, Dict


ARCH_NOTES = (
    "We decided to adopt PostgreSQL for the billing store because the team knows it well. "
    "Best practice: always run database migrations in CI before merging. "
    "Lesson learned: backups must be restore-tested every quarter. "
    "Risk: vendor lock-in with the cloud provider could be costly. "
    "How to deploy: first build the image, then push it, then roll out.\n"
    "- [ ] update the runbook\n"
    "- [ ] notify on-call\n"
)


def ingest_text(client, headers, title: str = "Architecture notes", content: str = ARCH_NOTES,
                tags: tuple = ("arch",)) -> Dict[str, Any]:
    r = client.post("/knowledge/artifacts", headers=headers,
                    json={"title": title, "content": content, "author": "ana", "tags": list(tags)})
    assert r.status_code == 200, r.text
    return r.json()


def upload(client, headers, filename: str, data: bytes, title: str = "Upload", tags: str = ""):
    return client.post("/knowledge/artifacts/upload", headers=headers,
                       files={"file": (filename, data, "application/octet-stream")},
                       data={"title": title, "author": "cy", "tags": tags})


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with a single line of text (no PDF library needed)."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def minimal_docx(*paragraphs: str) -> bytes:
    import io
    import docx
    document = docx.Document()
    for p in paragraphs:
        document.add_paragraph(p)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()
