from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import BinaryIO, Optional

import httpx
from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

# Applies to uploaded files and fetched URLs. The whole request body is also
# capped (BodySizeLimitMiddleware) at this plus a little multipart overhead.
MAX_UPLOAD_BYTES = int(float(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024)


def _too_large(what: str) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"{what} exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
    )


async def extract_text_from_upload(file: UploadFile) -> str:
    """
    Starlette has already spooled the upload (to disk above 1 MB), so PDF and
    DOCX are parsed straight from that file instead of being read into memory,
    in a worker thread so parsing doesn't block the event loop.
    """
    size = file.size
    if size is None:
        file.file.seek(0, os.SEEK_END)
        size = file.file.tell()
    if size > MAX_UPLOAD_BYTES:
        raise _too_large("File")
    file.file.seek(0)
    filename = (file.filename or "").lower()

    if filename.endswith(".pdf"):
        return await run_in_threadpool(_extract_pdf, file.file)
    if filename.endswith((".txt", ".md")):
        return (await file.read()).decode("utf-8", errors="replace")
    if filename.endswith(".docx"):
        return await run_in_threadpool(_extract_docx, file.file)
    if filename.endswith(".doc"):
        # legacy binary Word format — python-docx only reads OOXML (.docx)
        raise HTTPException(
            status_code=415,
            detail="Legacy .doc files are not supported. Save the document as .docx (or PDF) and upload again.",
        )

    raise HTTPException(status_code=415, detail=f"Unsupported file type: {file.filename}. Supported: PDF, TXT, MD, DOCX")


# Pages / paragraphs are separated by blank lines so document extraction can
# chunk on them (see llm_extraction.chunk_text).

def _extract_pdf(stream: BinaryIO) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(stream)
        return "\n\n".join(
            page.extract_text() or "" for page in reader.pages
        ).strip()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse PDF: {exc}")


def _extract_docx(stream: BinaryIO) -> str:
    try:
        import docx
        doc = docx.Document(stream)
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse DOCX: {exc}")


MAX_REDIRECTS = 5


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) must be judged as the IPv4 address
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global excludes private, loopback, link-local (incl. 169.254.169.254
    # cloud metadata), CGNAT, reserved, unspecified and multicast ranges
    return ip.is_global and not ip.is_multicast


async def _assert_public_host(url: httpx.URL) -> None:
    """Reject URLs whose host is, or resolves to, a non-public address."""
    if url.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    host = url.host
    if not host:
        raise HTTPException(status_code=400, detail="URL has no host")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, url.port or (443 if url.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"Could not resolve host: {host}")
    # every resolved address must be public, not just the first
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not _is_public_ip(ip):
            raise HTTPException(status_code=400, detail="URL resolves to a private or reserved address")


async def fetch_url(url: str) -> str:
    try:
        target = httpx.URL(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL")
    try:
        # redirects are followed manually so every hop is re-validated
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                await _assert_public_host(target)
                async with client.stream("GET", target) as response:
                    if response.is_redirect:
                        target = response.next_request.url
                        continue
                    response.raise_for_status()
                    html = (await _read_capped(response)).decode(
                        response.encoding or "utf-8", errors="replace"
                    )
                return _extract_main_content(html) or html
            raise HTTPException(status_code=502, detail="Too many redirects")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch URL: {exc}")


async def _read_capped(response: httpx.Response) -> bytes:
    """Read a streamed response body, stopping as soon as it passes the limit."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise _too_large("Fetched page")
    chunks, total = [], 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise _too_large("Fetched page")
        chunks.append(chunk)
    return b"".join(chunks)


def _extract_main_content(html: str) -> Optional[str]:
    """Strip HTML to main article text using trafilatura; returns None if extraction fails."""
    try:
        import trafilatura
        return trafilatura.extract(html, include_comments=False, include_tables=True) or None
    except Exception:
        return None
