from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import BinaryIO, Optional

import httpcore
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


async def _lookup(host: str, port: int) -> list[str]:
    """Every address `host` resolves to."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"Could not resolve host: {host}")
    return [info[4][0].split("%", 1)[0] for info in infos]


async def _resolve_public(host: str, port: int) -> str:
    """Resolve `host`; every address must be public, not just the first. Returns one to connect to."""
    addresses = await _lookup(host, port)
    if not addresses:
        raise HTTPException(status_code=400, detail=f"Could not resolve host: {host}")
    for address in addresses:
        if not _is_public_ip(ipaddress.ip_address(address)):
            raise HTTPException(status_code=400, detail="URL resolves to a private or reserved address")
    return addresses[0]


async def _assert_public_host(url: httpx.URL) -> None:
    """Reject URLs whose host is, or resolves to, a non-public address (a clear error up front)."""
    if url.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    if not url.host:
        raise HTTPException(status_code=400, detail="URL has no host")
    await _resolve_public(url.host, url.port or (443 if url.scheme == "https" else 80))


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """
    Checks the address at the moment of connecting, and connects to exactly
    the address it checked. Checking a URL and then letting the HTTP client
    resolve the host again would let a hostile DNS server answer with a public
    address the first time and a private one the second (DNS rebinding).
    TLS is unaffected: the certificate is still verified against the host
    name, which httpcore passes to start_tls separately.
    """

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        address = await _resolve_public(host, port)
        return await self._inner.connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                             socket_options=socket_options)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise HTTPException(status_code=400, detail="Unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _public_only_transport() -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport()
    # httpx has no public hook for the network backend; its pool is rebuilt with ours
    # (httpx is pinned in requirements.txt; test_ingestion covers this)
    transport._pool = httpcore.AsyncConnectionPool(ssl_context=httpx.create_ssl_context(),
                                                   network_backend=_PublicOnlyBackend())
    return transport


async def fetch_url(url: str) -> str:
    try:
        target = httpx.URL(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL")
    try:
        # redirects are followed manually so every hop is re-validated; the transport
        # checks the address again when it connects. Passing a transport also means
        # proxy settings from the environment are ignored: a proxy would connect for us.
        async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                     transport=_public_only_transport()) as client:
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
