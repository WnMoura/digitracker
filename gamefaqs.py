"""Baixa guias/walkthroughs do GameFAQs.

Substitui o passo manual que existia antes (rodar um scraper à parte, jogar o
texto numa IA, montar um PDF e importar): aqui você cola a URL e o app resolve.

A sessão principal usa `curl_cffi` para reproduzir o TLS/HTTP de um Chrome
atual. O `cloudscraper` antigo fica como fallback, pois o desafio moderno do
Cloudflare passou a devolver 403 mesmo com o perfil de navegador configurado.
Não adicionamos headers customizados: a coerência entre TLS e cabeçalhos é o que
evita o falso positivo. Somam-se retentativas com espera aleatória e um
intervalo entre páginas, para não parecer um robô apressado.

Uso responsável: isto é para consumo pessoal, em volume baixo. O texto dos FAQs
é de autoria de quem escreveu — fica no seu `config/games/*.json` local, não é
para redistribuir.

As funções de parsing são puras (recebem HTML, devolvem dados) para poderem ser
testadas sem rede.
"""
from __future__ import annotations

import random
import re
import time
import hashlib
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

HOST = "gamefaqs.gamespot.com"

# Uma página de FAQ formatado é paginada; sem teto, um guia gigante viraria
# dezenas de requisições.
MAX_PAGES = 15
MIN_TEXT_CHARS = 500          # abaixo disso a página não é o guia (erro/captcha)
RETRIES = 3
PAGE_DELAY = (3.0, 7.0)       # respiro entre páginas, como no scraper original

# Formato versionado da captura editorial. O texto simples continua sendo
# retornado por ``fetch_faq`` para compatibilidade, mas o Atlas usa esta
# representação para não perder a relação entre título, tabela e linha.
STRUCTURED_SCHEMA_VERSION = 1


class GameFAQsError(Exception):
    """Falha ao acessar ou interpretar o GameFAQs."""


def create_session():
    """Sessão com fingerprint de Chrome moderno para o Cloudflare.

    ``curl_cffi`` acompanha as versões atuais do Chrome e hoje consegue acessar
    o GameFAQs onde o ``cloudscraper`` 1.2.x recebe o desafio 403. O fallback
    continua útil em plataformas onde o binário nativo não estiver disponível.
    """
    try:
        from curl_cffi import requests as curl_requests
        return curl_requests.Session(impersonate="chrome")
    except (ImportError, RuntimeError):
        try:
            import cloudscraper
            return cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True}
            )
        except ImportError as exc:      # pragma: no cover - depende do ambiente
            raise GameFAQsError(
                "As bibliotecas de acesso ao GameFAQs não estão instaladas "
                "(pip install curl_cffi cloudscraper)."
            ) from exc


# ---------------------------------------------------------------------------- #
# URLs
# ---------------------------------------------------------------------------- #
_FAQ_LINK_RE = re.compile(r"/faqs/(\d+)")


def normalize_url(url: str) -> str:
    """Aceita a URL colada pelo usuário e valida que é do GameFAQs."""
    url = (url or "").strip()
    if not url:
        raise GameFAQsError("Cole a URL do jogo ou do guia no GameFAQs.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if host != HOST and not host.endswith('.' + HOST):
        raise GameFAQsError("Essa URL não é do GameFAQs (gamefaqs.gamespot.com).")
    return url


def is_faq_url(url: str) -> bool:
    """`/faqs/12345` é um guia específico; `/faqs` é a lista de guias."""
    return bool(_FAQ_LINK_RE.search(urlparse(url).path or ""))


# ---------------------------------------------------------------------------- #
# Parsing (puro — testável sem rede)
# ---------------------------------------------------------------------------- #
def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html or "", "html.parser")


def _faq_content_node(html: str):
    """Retorna ``(soup, nó editorial)`` sem misturar menus ao FAQ."""
    soup = _soup(html)
    node = (
        soup.select_one(".faqtext")
        or soup.select_one("#faqwrap")
        or soup.select_one("[data-faq-content]")
        or soup.find("pre")
        or soup.body
    )
    return soup, node


def _clean_faq_content(node):
    """Remove controles não editoriais in-place e devolve o mesmo nó."""
    if node is None:
        return None
    for unwanted in node.select(
        "script, style, noscript, nav, form, .ad, .ads, .advertisement, "
        ".pagination, [aria-label='pagination'], [aria-label='Pagination']"
    ):
        unwanted.decompose()
    return node


def _normalise_text(value: str) -> str:
    return " ".join((value or "").split())


def _node_links(node, base_url: str = "") -> list[dict]:
    links = []
    for anchor in node.find_all("a", href=True):
        label = _normalise_text(anchor.get_text(" ", strip=True))
        href = urljoin(base_url, anchor.get("href", "")) if base_url else anchor.get("href", "")
        if href or label:
            links.append({"text": label, "href": href})
    return links


def _node_images(node, base_url: str = "") -> list[dict]:
    images = []
    for image in node.find_all("img"):
        src = image.get("src") or image.get("data-src") or ""
        if not src:
            continue
        images.append({"src": urljoin(base_url, src) if base_url else src,
                       "alt": _normalise_text(image.get("alt", ""))})
    return images


def _anchor_for(node) -> str:
    if not node:
        return ""
    if node.get("id"):
        return str(node.get("id"))
    anchor = node.find("a", id=True)
    return str(anchor.get("id")) if anchor else ""


def _attr_int(node, name: str, default: int = 1) -> int:
    try:
        return max(1, int(node.get(name, default)))
    except (TypeError, ValueError):
        return default


def _table_grid(table, element_id: str, page_number: int, base_url: str = "") -> dict:
    """Converte uma tabela HTML em células com coordenadas lógicas.

    A lista de células mantém somente as células declaradas no HTML, mas cada
    célula recebe linha/coluna e ``rowspan``/``colspan``. Assim uma célula
    mesclada nunca desloca as colunas seguintes e o consumidor pode expandi-la
    quando precisar de uma matriz completa.
    """
    rows = []
    occupied: dict[tuple[int, int], bool] = {}
    max_columns = 0
    html_rows = table.find_all("tr")
    for row_index, tr in enumerate(html_rows):
        cells = []
        column = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while occupied.get((row_index, column)):
                column += 1
            rowspan = _attr_int(cell, "rowspan")
            colspan = _attr_int(cell, "colspan")
            cell_id = f"{element_id}-r{row_index + 1:04d}-c{column + 1:03d}"
            text = _normalise_text(cell.get_text(" ", strip=True))
            entry = {
                "id": cell_id,
                "tag": cell.name,
                "text": text,
                "raw_text": cell.get_text(" ", strip=True),
                "row": row_index,
                "column": column,
                "rowspan": rowspan,
                "colspan": colspan,
                "links": _node_links(cell, base_url),
                "images": _node_images(cell, base_url),
            }
            cells.append(entry)
            for rr in range(row_index, row_index + rowspan):
                for cc in range(column, column + colspan):
                    if rr > row_index:
                        occupied[(rr, cc)] = True
            column += colspan
        if cells:
            rows.append({
                "id": f"{element_id}-r{row_index + 1:04d}",
                "index": row_index,
                "cells": cells,
            })
            max_columns = max(max_columns, max((c["column"] + c["colspan"] for c in cells), default=0))

    first = rows[0]["cells"] if rows else []
    has_header = any(cell["tag"] == "th" for cell in first)
    headers = []
    if has_header:
        for cell in first:
            headers.append({
                "text": cell["text"],
                "column": cell["column"],
                "colspan": cell["colspan"],
                "source_cell": cell["id"],
            })
    caption = table.find("caption")
    return {
        "headers": headers,
        "has_header": has_header,
        "rows": rows,
        "columns": max_columns,
        "caption": _normalise_text(caption.get_text(" ", strip=True)) if caption else "",
        "links": _node_links(table, base_url),
        "images": _node_images(table, base_url),
    }


def _edition_signals(text: str, title: str = "") -> list[str]:
    haystack = f"{title} {text}".lower()
    signals = []
    for label, terms in (
        ("3DS/Decode", ("3ds", "decode")),
        ("PSP", ("psp",)),
        ("Re:Digitize", ("re:digitize", "redigitize")),
    ):
        if any(term in haystack for term in terms):
            signals.append(label)
    return signals


def parse_faq_document(html: str, *, page_number: int = 1, url: str = "") -> dict:
    """Captura a ordem editorial de uma página do GameFAQs.

    O resultado é JSON serializável e não contém HTML executável. A captura
    original continua sendo responsabilidade do armazenamento de fontes; este
    objeto é a visão segura usada pela revisão e pela IA.
    """
    soup, selected = _faq_content_node(html)
    node = _clean_faq_content(selected)
    page = max(1, int(page_number or 1))
    elements = []
    heading_stack: list[dict] = []
    skip_toc_level = None
    table_index = 0
    source_nodes = []
    if node is not None:
        # ``recursive=False`` no contêiner real preserva a sequência editorial;
        # para wrappers com uma única div, descemos até os filhos úteis.
        source_nodes = list(node.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "table", "ul", "ol", "pre", "blockquote", "img"], recursive=True))
        source_nodes = [item for item in source_nodes if not item.find_parent(["table", "ul", "ol"], recursive=False)]

    for ordinal, item in enumerate(source_nodes, start=1):
        # Itens internos de uma lista/tabela não são elementos editoriais
        # independentes; a informação será preservada na própria estrutura.
        if item.find_parent(["table", "ul", "ol"]):
            continue
        tag = item.name.lower()
        text = _normalise_text(item.get_text(" ", strip=True))

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            if skip_toc_level is not None and level <= skip_toc_level:
                skip_toc_level = None
            if text.lower().rstrip(":") in {"table of contents", "contents", "índice", "indice"}:
                skip_toc_level = level
                heading_stack = [h for h in heading_stack if h["level"] < level]
                continue
            if skip_toc_level is not None:
                continue
            while heading_stack and heading_stack[-1]["level"] >= level:
                heading_stack.pop()
            heading_stack.append({"level": level, "title": text})
            path = [h["title"] for h in heading_stack]
            element_id = f"p{page:03d}-e{ordinal:04d}"
            elements.append({
                "id": element_id,
                "type": "heading",
                "level": level,
                "title": text,
                "text": text,
                "path": path,
                "anchor": _anchor_for(item),
                "links": _node_links(item, url),
            })
            continue

        if skip_toc_level is not None:
            continue
        if not text and tag != "img":
            continue
        element_id = f"p{page:03d}-e{ordinal:04d}"
        path = [h["title"] for h in heading_stack]

        if tag == "table":
            table_index += 1
            grid = _table_grid(item, element_id, page, url)
            elements.append({
                "id": element_id,
                "type": "table",
                "table_index": table_index,
                "title": path[-1] if path else "",
                "path": path,
                "anchor": _anchor_for(item),
                **grid,
            })
        elif tag in {"ul", "ol"}:
            items = []
            for li in item.find_all("li", recursive=False):
                items.append({
                    "text": _normalise_text(li.get_text(" ", strip=True)),
                    "links": _node_links(li, url),
                })
            elements.append({
                "id": element_id,
                "type": "list",
                "ordered": tag == "ol",
                "items": items,
                "text": "\n".join(i["text"] for i in items),
                "path": path,
                "anchor": _anchor_for(item),
            })
        elif tag in {"pre", "blockquote"}:
            elements.append({
                "id": element_id,
                "type": "pre" if tag == "pre" else "quote",
                "text": item.get_text("\n", strip=True),
                "path": path,
                "anchor": _anchor_for(item),
                "links": _node_links(item, url),
            })
        elif tag == "img":
            src = item.get("src") or item.get("data-src") or ""
            elements.append({
                "id": element_id,
                "type": "image",
                "src": urljoin(url, src) if url else src,
                "alt": _normalise_text(item.get("alt", "")),
                "path": path,
                "anchor": _anchor_for(item),
            })
        else:
            elements.append({
                "id": element_id,
                "type": "paragraph",
                "text": item.get_text("\n", strip=True),
                "path": path,
                "anchor": _anchor_for(item),
                "links": _node_links(item, url),
            })

    plain_text = "\n".join(
        element.get("text", "")
        for element in elements
        if element.get("text")
    ).strip()
    tables = [element for element in elements if element.get("type") == "table"]
    row_count = sum(len(table.get("rows", [])) for table in tables)
    images = [element for element in elements if element.get("type") == "image"]
    nested_image_count = sum(
        len(element.get("images") or [])
        + sum(len(cell.get("images") or []) for row in element.get("rows") or [] for cell in row.get("cells") or [])
        for element in elements if element.get("type") == "table"
    )
    title = parse_faq_title(html)
    return {
        "schema_version": STRUCTURED_SCHEMA_VERSION,
        "page": page,
        "url": url,
        "title": title,
        "elements": elements,
        "stats": {
            "elements": len(elements),
            "headings": sum(1 for e in elements if e.get("type") == "heading"),
            "tables": len(tables),
            "table_rows": row_count,
            "images": len(images) + nested_image_count,
        },
        "edition_signals": _edition_signals(plain_text, title),
    }


def faq_documents_to_markdown(documents: list[dict], title: str = "") -> str:
    """Gera Markdown legível da captura estruturada, sem reparsear tabelas."""
    output = []
    if title:
        output.append(f"# {title}")
    for document in documents or []:
        page = document.get("page", 1)
        if output:
            output.append("")
        output.append(f"## Página {page}")
        for element in document.get("elements", []):
            kind = element.get("type")
            if kind == "heading":
                level = min(6, max(1, int(element.get("level", 2))))
                output.extend(["", f"{'#' * level} {element.get('title', '')}"])
            elif kind == "table":
                headers = element.get("headers") or []
                rows = element.get("rows") or []
                columns = max(1, int(element.get("columns", 1)))
                def row_values(row):
                    values = [""] * columns
                    for cell in row.get("cells", []):
                        start = int(cell.get("column", 0))
                        span = max(1, int(cell.get("colspan", 1)))
                        for index in range(start, min(columns, start + span)):
                            values[index] = cell.get("text", "")
                    return values
                if headers:
                    header_values = [""] * columns
                    for header in headers:
                        start = int(header.get("column", 0))
                        span = max(1, int(header.get("colspan", 1)))
                        for index in range(start, min(columns, start + span)):
                            header_values[index] = header.get("text", "")
                    output.append("| " + " | ".join(header_values) + " |")
                    output.append("| " + " | ".join("---" for _ in header_values) + " |")
                    rows = rows[1:] if rows and any(c.get("tag") == "th" for c in rows[0].get("cells", [])) else rows
                for row in rows:
                    output.append("| " + " | ".join(row_values(row)) + " |")
            elif kind == "list":
                for item in element.get("items", []):
                    output.append(f"{'1.' if element.get('ordered') else '-'} {item.get('text', '')}")
            elif kind == "pre":
                output.extend(["", "```", element.get("text", ""), "```"])
            elif kind == "quote":
                output.extend(f"> {line}" for line in element.get("text", "").splitlines())
            elif kind == "image":
                output.append(f"![{element.get('alt', '')}]({element.get('src', '')})")
            elif element.get("text"):
                output.extend(["", element.get("text", "")])
    return "\n".join(output).strip() + ("\n" if output else "")


def parse_faq_listing(html: str, base_url: str) -> list[dict]:
    """Guias disponíveis na página `/faqs` de um jogo: [{title, url}].

    A página repete o mesmo link em vários lugares (capa, tabela, destaques),
    então deduplicamos pelo id do FAQ, ficando com o título mais descritivo.
    """
    found: dict[str, dict] = {}
    for a in _soup(html).find_all("a", href=True):
        m = _FAQ_LINK_RE.search(a["href"])
        if not m:
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if not title:
            continue
        # "Walkthrough" e "Guide" também podem ser o título real da entrada.
        # Links repetidos continuam seguros: a deduplicação abaixo conserva o
        # rótulo mais descritivo quando o mesmo id aparece em outro ponto.
        faq_id = m.group(1)
        prev = found.get(faq_id)
        if prev is None or len(title) > len(prev["title"]):
            found[faq_id] = {
                "id": faq_id,
                "title": title,
                "url": urljoin(base_url, a["href"].split("?")[0]),
            }
    return sorted(found.values(), key=lambda f: f["title"].lower())


def parse_faq_content(html: str) -> str:
    """Extrai somente o texto do guia formatado.

    O GameFAQs historicamente usava ``.faqtext``. A versão atual da página
    envolve o conteúdo em ``#faqwrap``; cair direto para ``body`` nessa versão
    mistura menus, rodapé e navegação ao guia e faz a IA analisar centenas de
    blocos irrelevantes. Mantemos os seletores antigos como compatibilidade,
    mas sempre preferimos um contêiner explícito do FAQ.
    """
    soup, node = _faq_content_node(html)
    node = _clean_faq_content(node)
    if node is None:
        return ""
    # Preserve column/value association: plain get_text loses table structure.
    for table in node.find_all('table'):
        rows = []
        for tr in table.find_all('tr'):
            cells = [' '.join(cell.get_text(' ', strip=True).split())
                     for cell in tr.find_all(['th', 'td'], recursive=False)]
            if cells:
                rows.append(' | '.join(cells))
        # Blank lines make each row an independent guide block. Besides keeping
        # columns readable, this lets Atlas cite and audit every relationship
        # instead of treating a whole multi-page table as one giant paragraph.
        table.replace_with('\n' + '\n\n'.join(rows) + '\n')
    return node.get_text("\n")


def parse_page_count(html: str, *, strict: bool = False) -> int:
    """Quantas páginas tem o guia formatado (1 se não for paginado)."""
    pages = {1}
    for a in _soup(html).find_all("a", href=True):
        m = re.search(r"[?&]page=(\d+)", a["href"])
        if m:
            pages.add(int(m.group(1)) + 1)   # o parâmetro é 0-indexado
    label = re.search(r'Page\s+\d+\s+of\s+(\d+)', _soup(html).get_text(' '), re.I)
    if label:
        pages.add(int(label.group(1)))
    if strict and max(pages) > MAX_PAGES:
        raise GameFAQsError(f'O guia excede o limite de {MAX_PAGES} páginas. Importe um PDF completo; nenhuma fonte parcial foi salva.')
    return min(max(pages), MAX_PAGES)


def parse_faq_title(html: str) -> str:
    soup = _soup(html)
    for sel in ("h1.page-title", "h1", "title"):
        node = soup.select_one(sel)
        if node:
            text = " ".join(node.get_text(" ", strip=True).split())
            if text:
                return text
    return ""


# ---------------------------------------------------------------------------- #
# Rede
# ---------------------------------------------------------------------------- #
def _get(session, url: str, retries: int = RETRIES) -> str:
    """GET com retentativa e espera crescente/aleatória."""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=30)
        except Exception as exc:                 # rede, TLS, challenge…
            last = str(exc)
        else:
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 404:
                raise GameFAQsError("Página não encontrada no GameFAQs (404).")
            if resp.status_code == 403:
                last = "403 — o Cloudflare bloqueou o acesso."
            else:
                last = f"HTTP {resp.status_code}"
        if attempt < retries:
            time.sleep(random.uniform(3, 6) * attempt)
    raise GameFAQsError(f"Não consegui baixar do GameFAQs: {last}")


def list_faqs(session, url: str) -> list[dict]:
    """Guias de um jogo. Se a URL já for de um guia, devolve só ele."""
    url = normalize_url(url)
    if is_faq_url(url):
        html = _get(session, url)
        return [{"id": _FAQ_LINK_RE.search(url).group(1),
                 "title": parse_faq_title(html) or "Guia", "url": url}]
    html = _get(session, url)
    faqs = parse_faq_listing(html, url)
    if not faqs:
        raise GameFAQsError(
            "Nenhum guia encontrado nessa página. Confira se a URL é a aba "
            "'FAQs/Guides' do jogo."
        )
    return faqs


def fetch_faq(session, url: str, on_progress=None) -> dict:
    """Baixa o guia inteiro e preserva uma captura estruturada por página."""
    url = normalize_url(url)
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k != 'page']
    url = urlunparse(parsed._replace(query=urlencode(query), fragment=''))
    def page_url(page):
        return urlunparse(parsed._replace(query=urlencode(query + [('page', str(page))]), fragment=''))
    first = _get(session, url)
    title = parse_faq_title(first)
    parts = [parse_faq_content(first)]
    total = parse_page_count(first, strict=True)
    first_document = parse_faq_document(first, page_number=1, url=url)
    page_records = [{
        "number": 1,
        "url": url,
        "text": parts[0],
        "html": first,
        "document": first_document,
    }]

    page = 1
    while page < total:
        if on_progress:
            on_progress(page + 1, total)
        time.sleep(random.uniform(*PAGE_DELAY))
        address = page_url(page)
        html = _get(session, address)
        content = parse_faq_content(html)
        if len(content.strip()) < 80:
            raise GameFAQsError(f'Página {page + 1} sem texto suficiente. A importação completa foi interrompida; tente novamente.')
        parts.append(content)
        page_records.append({
            "number": page + 1,
            "url": address,
            "text": content,
            "html": html,
            "document": parse_faq_document(html, page_number=page + 1, url=address),
        })
        total = max(total, parse_page_count(html, strict=True))
        page += 1

    text = "\n".join(p for p in parts if p).strip()
    if len(text) < MIN_TEXT_CHARS:
        raise GameFAQsError(
            "A página veio sem o texto do guia — o GameFAQs pode ter pedido "
            "verificação. Tente de novo em alguns minutos."
        )
    documents = [record["document"] for record in page_records]
    expected_pages = list(range(1, total + 1))
    captured_pages = [record["number"] for record in page_records]
    if captured_pages != expected_pages:
        raise GameFAQsError("A paginação do guia ficou incompleta; nenhuma fonte parcial foi salva.")
    fingerprints = set()
    for record in page_records:
        normalized = re.sub(r"\s+", " ", str(record.get("text") or "").casefold()).strip()
        if len(normalized) < 80:
            continue
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if fingerprint in fingerprints:
            raise GameFAQsError("O GameFAQs repetiu uma página durante a captura; nenhuma fonte parcial foi salva.")
        fingerprints.add(fingerprint)
    return {
        "title": title,
        "text": text,
        "pages": total,
        "page_records": page_records,
        "structured_pages": documents,
        "markdown": faq_documents_to_markdown(documents, title),
        "stats": {
            "tables": sum(d.get("stats", {}).get("tables", 0) for d in documents),
            "table_rows": sum(d.get("stats", {}).get("table_rows", 0) for d in documents),
            "images": sum(d.get("stats", {}).get("images", 0) for d in documents),
        },
        "edition_signals": sorted({
            signal
            for document in documents
            for signal in document.get("edition_signals", [])
        }),
        "complete": True,
    }
