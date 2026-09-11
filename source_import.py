"""Captura genérica de fontes web com revisão antes da IA."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import requests

from source_document import from_html


class SourceImportError(ValueError):
    def __init__(self, message: str, code: str = "source_import", *, retryable: bool = False):
        super().__init__(message); self.code = code; self.retryable = retryable


def _document_content(document: dict) -> tuple[str, bool]:
    """Return a reviewable text projection and whether structure was found.

    A table normally has no top-level ``text`` field in the structured source
    contract.  Looking only at ``element.text`` therefore classified perfectly
    valid table-only captures as empty and prevented the browser fallback from
    being offered consistently.  Keep this projection local to the importer;
    the lossless JSON remains the source of truth.
    """
    parts: list[str] = []
    has_structure = False
    for page in (document or {}).get("pages") or []:
        for element in page.get("elements") or []:
            if not isinstance(element, dict):
                continue
            kind = str(element.get("type") or "")
            for key in ("title", "text", "caption", "alt"):
                value = element.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            if kind == "table":
                header_rows = element.get("header_rows") or []
                rows = element.get("rows") or []
                if header_rows or rows or element.get("headers"):
                    has_structure = True
                for header in element.get("headers") or []:
                    if isinstance(header, dict) and str(header.get("text") or "").strip():
                        parts.append(str(header.get("text")).strip())
                for header_row in header_rows:
                    for cell in (header_row or {}).get("cells") or []:
                        if isinstance(cell, dict) and str(cell.get("text") or "").strip():
                            parts.append(str(cell.get("text")).strip())
                for row in rows:
                    for cell in (row or {}).get("cells") or []:
                        if isinstance(cell, dict) and str(cell.get("text") or "").strip():
                            parts.append(str(cell.get("text")).strip())
            elif kind == "list":
                items = element.get("items") or []
                has_structure = has_structure or bool(items)
                parts.extend(str(item.get("text") or "").strip()
                             for item in items if isinstance(item, dict) and str(item.get("text") or "").strip())
            elif kind == "definition_list":
                items = element.get("items") or []
                has_structure = has_structure or bool(items)
                for item in items:
                    if isinstance(item, dict):
                        parts.extend(value.strip() for value in (
                            str(item.get("term") or ""), str(item.get("definition") or "")) if value.strip())
            elif kind in {"figure", "heading", "paragraph", "preformatted", "quote"}:
                has_structure = has_structure or kind in {"figure", "heading"}
    return " ".join(parts), has_structure


def validate_public_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SourceImportError("Use uma URL HTTP ou HTTPS pública.", "invalid_url")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
        raise SourceImportError("A URL aponta para uma rede local não permitida.", "private_url")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SourceImportError("Não foi possível resolver o domínio da fonte.", "dns_error", retryable=True) from exc
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
            raise SourceImportError("A URL aponta para uma rede privada não permitida.", "private_url")
    return value


def discover_links(html: str, base_url: str, *, limit: int = 500) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    base = validate_public_url(base_url)
    seen = set(); result = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor.get("href", "")).split("#", 1)[0]
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        key = href.rstrip("/")
        if key in seen: continue
        seen.add(key)
        result.append({"url": href, "title": re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))[:500],
                      "same_host": parsed.hostname.casefold() == urlparse(base).hostname.casefold()})
        if len(result) >= limit: break
    return result


def capture_http(url: str, *, session=None, timeout: int = 30, source_id: str = "") -> dict:
    requested = validate_public_url(url)
    client = session or requests.Session()
    try:
        response = client.get(requested, timeout=timeout, headers={"Accept": "text/html,application/xhtml+xml"})
    except requests.RequestException as exc:
        raise SourceImportError("Falha de rede ao capturar a fonte.", "network_error", retryable=True) from exc
    # Requests follows redirects by default.  Validate the effective URL
    # before parsing/saving so a public URL that redirects to loopback,
    # metadata or another private network can never become a stored source.
    final_url = validate_public_url(getattr(response, "url", requested) or requested)
    if response.status_code >= 400:
        raise SourceImportError(f"A fonte respondeu HTTP {response.status_code}.", "http_error", retryable=response.status_code in {408, 429, 500, 502, 503, 504})
    html = response.text or ""
    if len(html.encode("utf-8")) > 15 * 1024 * 1024:
        raise SourceImportError("A página excede 15 MB.", "size_limit")
    document = from_html(html, url=final_url, source_id=source_id)
    # Texto vazio é evidência para oferecer modo navegador, não sucesso falso.
    content, has_structure = _document_content(document)
    document["completeness"] = {"status": "complete" if len(content.strip()) >= 80 or has_structure else "insufficient",
                                 "termination_reason": "http_response", "content_hash": hashlib.sha256(html.encode("utf-8")).hexdigest()}
    return {"requested_url": requested, "final_url": final_url, "html": html, "document": document,
            "discovery": discover_links(html, final_url)}


def capture_browser(url: str, *, source_id: str = "", max_cycles: int = 60, timeout_ms: int = 30_000) -> dict:
    """Captura dinâmica com Edge isolado; importação continua funcional sem Playwright."""
    requested = validate_public_url(url)
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise SourceImportError("Captura dinâmica requer a dependência Playwright.", "browser_missing") from exc
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:
            raise SourceImportError("Captura dinâmica requer Microsoft Edge instalado.", "browser_missing") from exc
        context = browser.new_context(java_script_enabled=True)
        page = context.new_page()
        try:
            page.goto(requested, wait_until="domcontentloaded", timeout=timeout_ms)
            last_hash = ""; stable = 0
            for _ in range(max(1, min(300, max_cycles))):
                page.wait_for_timeout(400)
                html = page.content()
                fingerprint = hashlib.sha256(re.sub(r"\s+", " ", html).encode("utf-8")).hexdigest()
                if fingerprint == last_hash: stable += 1
                else: stable = 0; last_hash = fingerprint
                page.evaluate("window.scrollBy(0, Math.max(300, window.innerHeight * .8));")
                if stable >= 3: break
            html = page.content(); final_url = validate_public_url(page.url)
            document = from_html(html, url=final_url, source_id=source_id)
            content, has_structure = _document_content(document)
            document["completeness"] = {"status": "complete" if len(content.strip()) >= 80 or has_structure else "insufficient", "termination_reason": "stabilized", "dynamic": True,
                                         "content_hash": hashlib.sha256(html.encode("utf-8")).hexdigest()}
            return {"requested_url": requested, "final_url": final_url, "html": html, "document": document,
                    "discovery": discover_links(html, final_url)}
        except PlaywrightTimeoutError as exc:
            raise SourceImportError("A página dinâmica excedeu o tempo limite.", "browser_timeout", retryable=True) from exc
        finally:
            context.close(); browser.close()
