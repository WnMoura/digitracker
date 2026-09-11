"""Contrato editorial comum para fontes de Guia, Atlas e Itens.

O documento é uma representação segura de HTML/FAQ. O HTML original continua
arquivado separadamente e nunca é renderizado na UI. IDs são estáveis somente
dentro de uma captura, portanto referências de IA podem ser validadas antes de
qualquer publicação.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urljoin


FORMAT = "digitracker-source-v1"
SCHEMA_VERSION = 1
PARSER_VERSION = "source-document-1"
ELEMENT_TYPES = {"heading", "paragraph", "list", "table", "definition_list", "figure", "note", "preformatted", "quote"}


def _clean(value: object, limit: int = 20_000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _stable(prefix: str, *values: object) -> str:
    raw = "|".join(str(value or "") for value in values).encode("utf-8")
    return f"{prefix}-{hashlib.sha1(raw).hexdigest()[:16]}"


def _capture_hash(document: dict) -> str:
    value = deepcopy(document)
    # The logical source id is assigned by the consumer (Guia/Atlas/Itens)
    # and must not make the same editorial capture hash different.
    for key in ("captured_at", "capture_id", "source_id"):
        value.pop(key, None)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cell_value(cell: dict) -> str:
    return str(cell.get("text") or "") if isinstance(cell, dict) else str(cell or "")


def _normalise_table(table: dict, page_id: str, element_id: str) -> dict:
    raw_rows = table.get("rows") if isinstance(table.get("rows"), list) else []
    rows = []
    try:
        max_columns = max(1, int(table.get("columns") or 0))
    except (TypeError, ValueError):
        max_columns = 1
    for ri, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            continue
        cells = []
        for ci, item in enumerate(raw.get("cells") or []):
            if not isinstance(item, dict):
                item = {"text": item}
            try:
                column = max(0, int(item.get("column", ci)))
                rowspan = max(1, int(item.get("rowspan", 1)))
                colspan = max(1, int(item.get("colspan", 1)))
            except (TypeError, ValueError):
                column, rowspan, colspan = ci, 1, 1
            cell_id = _clean(item.get("id"), 160) or f"{element_id}-r{ri + 1:04d}-c{column + 1:03d}"
            cells.append({
                "id": cell_id, "tag": "th" if item.get("tag") == "th" else "td",
                "text": str(item.get("text") or ""), "raw_text": str(item.get("raw_text") or item.get("text") or ""),
                "row": ri, "column": column, "rowspan": rowspan, "colspan": colspan,
                "links": deepcopy(item.get("links") or []), "images": deepcopy(item.get("images") or []),
            })
            max_columns = max(max_columns, column + colspan)
        if cells:
            try:
                row_index = int(raw.get("index", ri))
            except (TypeError, ValueError):
                row_index = ri
            rows.append({"id": _clean(raw.get("id"), 160) or f"{element_id}-r{ri + 1:04d}",
                         "index": row_index, "cells": cells})
    headers = []
    for item in table.get("headers") or []:
        if isinstance(item, dict):
            try:
                # A hand-authored/legacy header may omit ``column``.  Use its
                # position as the fallback instead of putting every such
                # header in column zero and losing the table shape.
                raw_column = item.get("column", len(headers))
                if raw_column in (None, ""):
                    raw_column = len(headers)
                column = max(0, int(raw_column))
            except (TypeError, ValueError):
                column = len(headers)
            try:
                colspan = max(1, int(item.get("colspan", 1) or 1))
            except (TypeError, ValueError):
                colspan = 1
            headers.append({"text": str(item.get("text") or ""), "column": column,
                            "colspan": colspan,
                            "source_cell": _clean(item.get("source_cell"), 160)})
        else:
            headers.append({"text": str(item or ""), "column": len(headers), "colspan": 1})
    # Preserve every editorial header row, including repeated headers in a
    # paginated table and multi-level headers made with row/col spans.  The
    # legacy ``headers`` array remains the first-row projection consumed by
    # older mappers.
    header_rows = []
    raw_header_rows = table.get("header_rows")
    if not isinstance(raw_header_rows, list):
        raw_header_rows = []
        for raw in rows:
            cells = raw.get("cells") or []
            if cells and all(cell.get("tag") == "th" for cell in cells):
                raw_header_rows.append({"id": raw.get("id", ""),
                                        "index": raw.get("index", 0),
                                        "cells": cells})
    for hi, raw in enumerate(raw_header_rows):
        if not isinstance(raw, dict):
            continue
        cells = []
        for ci, item in enumerate(raw.get("cells") or []):
            if not isinstance(item, dict):
                item = {"text": item}
            try:
                column = max(0, int(item.get("column", ci) if item.get("column") not in (None, "") else ci))
            except (TypeError, ValueError):
                column = ci
            try:
                colspan = max(1, int(item.get("colspan", 1) or 1))
            except (TypeError, ValueError):
                colspan = 1
            try:
                rowspan = max(1, int(item.get("rowspan", 1) or 1))
            except (TypeError, ValueError):
                rowspan = 1
            cells.append({"id": _clean(item.get("id"), 160),
                          "text": str(item.get("text") or ""),
                          "column": column, "colspan": colspan,
                          "rowspan": rowspan,
                          "source_cell": _clean(item.get("source_cell") or item.get("id"), 160)})
        if cells:
            try:
                header_index = max(0, int(raw.get("index", hi) or hi))
            except (TypeError, ValueError):
                header_index = hi
            try:
                header_level = max(1, int(raw.get("level", hi + 1) or hi + 1))
            except (TypeError, ValueError):
                header_level = hi + 1
            header_rows.append({"id": _clean(raw.get("id"), 160) or f"{element_id}-header-{hi + 1:03d}",
                                "index": header_index, "level": header_level,
                                "cells": cells})
    return {"columns": max_columns, "headers": headers,
            "header_rows": header_rows,
            "has_header": bool(table.get("has_header") or headers or header_rows),
            "rows": rows, "caption": _clean(table.get("caption"), 2_000),
            "links": deepcopy(table.get("links") or []), "images": deepcopy(table.get("images") or [])}


def normalize_document(value: dict, *, source_id: str = "", capture_id: str = "") -> dict:
    """Valida uma captura já estruturada e completa os IDs faltantes."""
    if not isinstance(value, dict):
        raise ValueError("Documento de fonte inválido.")
    pages = value.get("pages") if isinstance(value.get("pages"), list) else []
    result_pages = []
    seen_pages = set()
    total_tables = total_rows = total_elements = total_images = 0
    for pi, raw_page in enumerate(pages, 1):
        if not isinstance(raw_page, dict):
            continue
        try:
            number = max(1, int(raw_page.get("number") or raw_page.get("page") or pi))
        except (TypeError, ValueError):
            number = pi
        page_id = _clean(raw_page.get("id"), 160) or f"page-{number:04d}"
        if page_id in seen_pages:
            page_id = _stable("page", page_id, pi)
        seen_pages.add(page_id)
        elements = []
        for ei, raw in enumerate(raw_page.get("elements") or [], 1):
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("type") or "paragraph").lower()
            if kind in {"p", "text"}: kind = "paragraph"
            if kind in {"pre", "preformatted"}: kind = "preformatted"
            if kind in {"image", "img"}: kind = "figure"
            if kind in {"dl", "definition-list"}: kind = "definition_list"
            if kind not in ELEMENT_TYPES:
                kind = "paragraph"
            element_id = _clean(raw.get("id"), 160) or f"{page_id}-element-{ei:04d}"
            base = {"id": element_id, "type": kind, "path": [_clean(x, 500) for x in (raw.get("path") or []) if _clean(x)],
                    "anchor": _clean(raw.get("anchor"), 500), "links": deepcopy(raw.get("links") or [])}
            if kind == "table":
                base.update(_normalise_table(raw, page_id, element_id))
                base["title"] = _clean(raw.get("title") or (base["path"][-1] if base["path"] else ""), 500)
                base["table_id"] = _clean(raw.get("table_id"), 160) or element_id
                total_tables += 1; total_rows += len(base["rows"])
                total_images += sum(len(cell.get("images") or []) for row in base["rows"] for cell in row.get("cells") or []) + len(base.get("images") or [])
            elif kind == "list":
                base["ordered"] = bool(raw.get("ordered"))
                base["items"] = [{"id": _clean(item.get("id"), 160) or _stable("item", element_id, index),
                                  "text": str(item.get("text") or ""), "links": deepcopy(item.get("links") or [])}
                                 for index, item in enumerate(raw.get("items") or []) if isinstance(item, dict)]
                base["text"] = "\n".join(item["text"] for item in base["items"])
            elif kind == "figure":
                base["src"] = _clean(raw.get("src") or raw.get("url"), 2_000)
                base["alt"] = _clean(raw.get("alt") or raw.get("title"), 500)
                base["caption"] = _clean(raw.get("caption"), 2_000)
                total_images += 1 if base["src"] else 0
            elif kind == "definition_list":
                entries = []
                for index, item in enumerate(raw.get("items") or []):
                    if not isinstance(item, dict):
                        continue
                    entries.append({
                        "id": _clean(item.get("id"), 160) or _stable("definition", element_id, index),
                        "term": str(item.get("term") or item.get("dt") or ""),
                        "definition": str(item.get("definition") or item.get("dd") or item.get("text") or ""),
                        "links": deepcopy(item.get("links") or []),
                    })
                base["items"] = entries
                base["text"] = "\n".join(
                    f"{item['term']}: {item['definition']}".strip(": ") for item in entries
                )
            else:
                base["text"] = str(raw.get("text") or "")[:20_000]
                if kind == "heading":
                    base["title"] = _clean(raw.get("title") or base["text"], 500)
                    try:
                        level = int(raw.get("level") or 2)
                    except (TypeError, ValueError):
                        level = 2
                    base["level"] = max(1, min(6, level))
            elements.append(base)
        result_pages.append({"id": page_id, "number": number, "requested_url": _clean(raw_page.get("requested_url") or raw_page.get("url"), 2_000),
                             "final_url": _clean(raw_page.get("final_url") or raw_page.get("url"), 2_000),
                             "hash": _clean(raw_page.get("hash"), 128), "elements": elements})
        total_elements += len(elements)
    result_pages.sort(key=lambda page: (page["number"], page["id"]))
    result = {"format": FORMAT, "schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION,
              "source_id": _clean(source_id, 160), "capture_id": _clean(capture_id, 160),
              "title": _clean(value.get("title"), 500), "edition": deepcopy(value.get("edition") or {}),
              "requested_url": _clean(value.get("requested_url"), 2_000), "canonical_url": _clean(value.get("canonical_url"), 2_000),
              "pages": result_pages, "stats": {"pages": len(result_pages), "elements": total_elements,
              "tables": total_tables, "table_rows": total_rows, "images": total_images},
              "completeness": deepcopy(value.get("completeness") or {"status": "complete"})}
    result["capture_id"] = capture_id or _capture_hash(result)
    result["stats"]["pages_with_content"] = sum(bool(page["elements"]) for page in result_pages)
    return result


def from_gamefaqs(faq: dict, *, source_id: str = "") -> dict:
    """Converte o retorno estruturado existente do adapter GameFAQs."""
    pages = []
    for record in faq.get("page_records") or []:
        document = record.get("document") or {}
        page = {"id": f"page-{int(record.get('number') or 1):04d}", "number": record.get("number"),
                "url": record.get("url"), "elements": document.get("elements") or []}
        pages.append(page)
    return normalize_document({"title": faq.get("title"), "requested_url": (faq.get("source") or {}).get("url"),
                               "canonical_url": (faq.get("source") or {}).get("canonical_url"),
                               "edition": {"signals": faq.get("edition_signals") or []}, "pages": pages,
                               "completeness": {"status": "complete" if faq.get("complete") else "partial"}}, source_id=source_id)


def _html_table_grid(table, element_id: str, page_number: int, base_url: str = "") -> dict:
    """Extrai uma tabela HTML sem deslocar colunas por rowspan/colspan."""
    rows = []
    occupied = set()
    max_columns = 0
    # Ignore rows belonging to nested tables.  GameFAQs occasionally nests a
    # formatting table inside a cell; treating those rows as siblings shifts
    # every subsequent column in the outer table.
    outer_rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    for row_index, tr in enumerate(outer_rows):
        cells = []
        column = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while (row_index, column) in occupied:
                column += 1
            try:
                rowspan = max(1, int(cell.get("rowspan", 1) or 1))
            except (TypeError, ValueError):
                rowspan = 1
            try:
                colspan = max(1, int(cell.get("colspan", 1) or 1))
            except (TypeError, ValueError):
                colspan = 1
            cell_id = f"p{page_number:03d}-e{element_id}-r{row_index + 1:04d}-c{column + 1:03d}"
            links = [{"text": anchor.get_text(" ", strip=True),
                      "href": urljoin(base_url, anchor.get("href", ""))}
                     for anchor in cell.find_all("a", href=True)]
            images = [{"src": urljoin(base_url, image.get("src") or image.get("data-src") or ""),
                       "alt": _clean(image.get("alt"), 500)}
                      for image in cell.find_all("img")
                      if image.get("src") or image.get("data-src")]
            cells.append({"id": cell_id, "tag": cell.name,
                          "text": cell.get_text(" ", strip=True),
                          "raw_text": cell.get_text(" ", strip=True),
                          "row": row_index, "column": column,
                          "rowspan": rowspan, "colspan": colspan,
                          "links": links, "images": images})
            for rr in range(row_index, row_index + rowspan):
                for cc in range(column, column + colspan):
                    if rr > row_index:
                        occupied.add((rr, cc))
            column += colspan
        if cells:
            rows.append({"id": f"p{page_number:03d}-e{element_id}-r{row_index + 1:04d}",
                         "index": row_index, "cells": cells})
            max_columns = max(max_columns,
                              max((item["column"] + item["colspan"] for item in cells), default=0))
    first = rows[0]["cells"] if rows else []
    headers = [{"text": item["text"], "column": item["column"],
                "colspan": item["colspan"], "source_cell": item["id"]}
               for item in first if item.get("tag") == "th"]
    header_rows = []
    for row in rows:
        cells = row.get("cells") or []
        # A repeated header is a row made entirely of th cells.  Mixed rows
        # (for example a row-label th followed by data td cells) remain data.
        if cells and all(cell.get("tag") == "th" for cell in cells):
            header_rows.append({"id": row.get("id", ""), "index": row.get("index", 0),
                                "level": len(header_rows) + 1,
                                "cells": [{"id": cell.get("id", ""),
                                           "text": cell.get("text", ""),
                                           "column": cell.get("column", 0),
                                           "colspan": cell.get("colspan", 1),
                                           "rowspan": cell.get("rowspan", 1),
                                           "source_cell": cell.get("id", "")}
                                          for cell in cells]})
    caption = table.find("caption")
    return {"headers": headers, "header_rows": header_rows,
            "has_header": bool(headers or header_rows), "rows": rows,
            "columns": max_columns, "caption": _clean(caption.get_text(" ", strip=True)) if caption else "",
            "links": [{"text": anchor.get_text(" ", strip=True),
                       "href": urljoin(base_url, anchor.get("href", ""))}
                      for anchor in table.find_all("a", href=True)],
            "images": [{"src": urljoin(base_url, image.get("src") or image.get("data-src") or ""),
                        "alt": _clean(image.get("alt"), 500)}
                       for image in table.find_all("img")
                       if image.get("src") or image.get("data-src")]}


def from_html(html: str, *, url: str = "", title: str = "", source_id: str = "", page_number: int = 1) -> dict:
    """Parser genérico seguro para HTML estático; usa a região editorial."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover
        raise ValueError("BeautifulSoup é necessário para importar HTML.") from exc
    soup = BeautifulSoup(html or "", "html.parser")
    for node in soup.select("script,style,noscript,nav,form,.ad,.ads,.advertisement"):
        node.decompose()
    root = soup.select_one("main,article,[role='main'],.faqtext,#faqwrap") or soup.body or soup
    elements = []
    headings = []
    for index, node in enumerate(root.find_all(["h1","h2","h3","h4","h5","h6","p","ul","ol","table","pre","blockquote","figure","img","dl"], recursive=True), 1):
        if node.find_parent(["table","ul","ol"]):
            continue
        # A figure is the editorial element; its child <img> is already
        # preserved in the figure payload and must not become a duplicate
        # standalone card/image in the normalized document.
        if node.name == "img" and node.find_parent("figure"):
            continue
        kind = node.name
        text = node.get_text(" ", strip=True)
        if kind.startswith("h"):
            level = int(kind[1]); headings = [item for item in headings if item[0] < level]; headings.append((level, text))
            elements.append({"id": f"p{page_number:03d}-e{index:04d}", "type": "heading", "level": level,
                             "title": text, "text": text, "path": [item[1] for item in headings]})
        elif kind == "table":
            table_id = f"p{page_number:03d}-e{index:04d}"
            elements.append({"id": table_id, "type": "table",
                             "title": headings[-1][1] if headings else "",
                             "path": [item[1] for item in headings],
                             **_html_table_grid(node, f"{index:04d}", page_number, url)})
        elif kind in {"ul", "ol"}:
            elements.append({"id": f"p{page_number:03d}-e{index:04d}", "type": "list", "ordered": kind == "ol",
                             "items": [{"text": li.get_text(" ", strip=True)} for li in node.find_all("li", recursive=False)], "path": [item[1] for item in headings]})
        elif kind == "img" or kind == "figure":
            image = node if kind == "img" else node.find("img")
            if image:
                elements.append({"id": f"p{page_number:03d}-e{index:04d}", "type": "figure", "src": urljoin(url, image.get("src") or image.get("data-src") or ""),
                                 "alt": _clean(image.get("alt"), 500), "caption": _clean(node.get_text(" ", strip=True)), "path": [item[1] for item in headings]})
        elif kind == "dl":
            items = []
            for term in node.find_all("dt", recursive=False):
                definition = term.find_next_sibling("dd")
                items.append({"term": term.get_text(" ", strip=True),
                              "definition": definition.get_text(" ", strip=True) if definition else ""})
            elements.append({"id": f"p{page_number:03d}-e{index:04d}",
                             "type": "definition_list", "items": items,
                             "path": [item[1] for item in headings]})
        elif text:
            links = [{"text": anchor.get_text(" ", strip=True),
                      "href": urljoin(url, anchor.get("href", ""))}
                     for anchor in node.find_all("a", href=True)]
            elements.append({"id": f"p{page_number:03d}-e{index:04d}", "type": "preformatted" if kind == "pre" else "quote" if kind == "blockquote" else "paragraph",
                             "text": node.get_text("\n" if kind in {"pre","blockquote"} else " ", strip=True),
                             "links": links, "path": [item[1] for item in headings]})
    return normalize_document({"title": title or (soup.title.get_text(" ", strip=True) if soup.title else ""), "requested_url": url,
                               "canonical_url": url, "pages": [{"number": page_number, "url": url, "elements": elements}]}, source_id=source_id)


def validate_references(document: dict, refs: list[dict]) -> list[dict]:
    """Retorna somente referências apontando para a captura informada."""
    valid = []
    page_map = {page["id"]: page for page in document.get("pages") or []}
    element_map = {element.get("id"): (page, element)
                   for page in page_map.values()
                   for element in page.get("elements") or []
                   if element.get("id")}
    table_map = {}
    for page in page_map.values():
        for element in page.get("elements") or []:
            if element.get("type") != "table":
                continue
            target = (page, element)
            for key in (element.get("table_id"), element.get("id")):
                if key:
                    table_map[str(key)] = target
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        page = page_map.get(ref.get("page_id")) if ref.get("page_id") else None
        if ref.get("page_id") and page is None:
            continue
        target = None
        if ref.get("table_id"):
            target = table_map.get(str(ref.get("table_id")))
            if target is None:
                continue
        elif ref.get("element_id"):
            target = element_map.get(ref.get("element_id"))
            if target is None:
                continue
        if page is not None and target is not None and target[0].get("id") != page.get("id"):
            continue
        if ref.get("row_id"):
            if target is None or target[1].get("type") != "table" or not any(
                    str(row.get("id")) == str(ref.get("row_id"))
                    for row in target[1].get("rows") or [] if isinstance(row, dict)):
                continue
        if ref.get("cell_id"):
            if target is None or target[1].get("type") != "table" or not any(
                    str(cell.get("id")) == str(ref.get("cell_id"))
                    for row in target[1].get("rows") or [] if isinstance(row, dict)
                    for cell in row.get("cells") or []
                    if isinstance(cell, dict)):
                continue
        if ref.get("page_number"):
            try:
                if not any(int(item.get("number") or 0) == int(ref.get("page_number"))
                           for item in page_map.values()):
                    continue
            except (TypeError, ValueError):
                continue
        if ref.get("table_id") and target is None:
            continue
        valid.append(deepcopy(ref))
    return valid


def to_markdown(document: dict) -> str:
    lines = [f"# {_clean(document.get('title') or 'Fonte')}"]
    for page in document.get("pages") or []:
        lines += ["", f"## Página {page.get('number', 1)}"]
        for element in page.get("elements") or []:
            kind = element.get("type")
            if kind == "heading": lines += ["", f"{'#' * max(1, min(6, int(element.get('level') or 2)))} {_clean(element.get('title') or element.get('text'))}"]
            elif kind == "table":
                columns = max(1, int(element.get("columns") or 1))
                def values(row):
                    result = [""] * columns
                    for cell in row.get("cells") or []:
                        start = max(0, int(cell.get("column") or 0)); span = max(1, int(cell.get("colspan") or 1))
                        for idx in range(start, min(columns, start + span)): result[idx] = _cell_value(cell).replace("|", "\\|")
                    return result
                rows = element.get("rows") or []
                header_ids = {str(item.get("id")) for item in element.get("header_rows") or []
                              if isinstance(item, dict) and item.get("id")}
                header_indices = {int(item.get("index")) for item in element.get("header_rows") or []
                                  if isinstance(item, dict) and str(item.get("index", "")).lstrip("-").isdigit()}
                header_rows = [row for row in rows if str(row.get("id")) in header_ids
                               or row.get("index") in header_indices]
                if not header_rows and rows and any(cell.get("tag") == "th" for cell in rows[0].get("cells") or []):
                    header_rows = [rows[0]]
                if header_rows:
                    lines.extend("| " + " | ".join(values(row)) + " |" for row in header_rows)
                    lines.append("| " + " | ".join("---" for _ in range(columns)) + " |")
                    header_keys = {str(row.get("id")) for row in header_rows}
                    rows = [row for row in rows if str(row.get("id")) not in header_keys]
                lines.extend("| " + " | ".join(values(row)) + " |" for row in rows)
            elif kind == "list": lines.extend(("1." if element.get("ordered") else "-") + " " + _clean(item.get("text")) for item in element.get("items") or [])
            elif kind == "definition_list":
                lines.extend(f"**{_clean(item.get('term'))}**: {_clean(item.get('definition'))}" for item in element.get("items") or [])
            elif kind in {"preformatted", "quote"}: lines += ["", "```" if kind == "preformatted" else ">", str(element.get("text") or ""), "```" if kind == "preformatted" else ""]
            elif kind == "figure": lines.append(f"![{_clean(element.get('alt'))}]({element.get('src') or ''})")
            elif element.get("text"): lines += ["", str(element.get("text"))]
    return "\n".join(lines).strip() + "\n"


def to_guide_sections(document: dict, *, source_id: str = "") -> list[dict]:
    """Projeção legível para a Jornada, sem destruir a captura estruturada."""
    sections = []
    for page in document.get("pages") or []:
        current = None
        for index, element in enumerate(page.get("elements") or [], 1):
            kind = element.get("type")
            path = list(element.get("path") or [])
            title = _clean(element.get("title") or (path[-1] if path else ""), 500)
            if kind == "heading" or current is None:
                section_title = title or (path[-1] if path else f"Página {page.get('number', 1)}")
                current = {"title": section_title, "page": page.get("number", 1),
                           "page_id": page.get("id", ""), "blocks": []}
                sections.append(current)
            ref = {"source_id": source_id, "page": page.get("number", 1),
                   "page_id": page.get("id", ""), "element_id": element.get("id", "")}
            if kind == "heading":
                current["blocks"].append({"type": "subhead", "title": title, "text": title, "page": page.get("number", 1), "source_refs": [ref], "element_id": element.get("id", "")})
            elif kind == "table":
                for row in element.get("rows") or []:
                    values = [""] * max(1, int(element.get("columns") or 1))
                    for cell in row.get("cells") or []:
                        start = max(0, int(cell.get("column") or 0)); span = max(1, int(cell.get("colspan") or 1))
                        for col in range(start, min(len(values), start + span)): values[col] = str(cell.get("text") or "")
                    current["blocks"].append({"type": "table", "title": title, "text": " | ".join(values), "page": page.get("number", 1),
                                               "rows": [values], "table_id": element.get("table_id") or element.get("id", ""), "row_id": row.get("id", ""),
                                               "element_id": element.get("id", ""),
                                               "header_rows": deepcopy(element.get("header_rows") or []),
                                               "source_refs": [{**ref, "table_id": element.get("table_id") or element.get("id", ""), "row_id": row.get("id", "")}], "path": path})
            elif kind == "list":
                current["blocks"].append({"type": "checklist", "title": title, "text": "\n".join(item.get("text", "") for item in element.get("items") or []),
                                           "items": deepcopy(element.get("items") or []), "page": page.get("number", 1), "source_refs": [ref], "element_id": element.get("id", "")})
            elif kind == "definition_list":
                current["blocks"].append({"type": "definitions", "title": title,
                                           "text": "\n".join(f"{item.get('term', '')}: {item.get('definition', '')}" for item in element.get("items") or []),
                                           "items": deepcopy(element.get("items") or []),
                                           "page": page.get("number", 1), "source_refs": [ref],
                                           "element_id": element.get("id", "")})
            elif kind in {"figure"}:
                current["blocks"].append({"type": "image", "title": element.get("alt") or title, "text": element.get("caption", ""),
                                           "image": {"src": element.get("src", ""), "alt": element.get("alt", "")}, "page": page.get("number", 1), "source_refs": [ref], "element_id": element.get("id", "")})
            elif kind != "heading" and element.get("text"):
                current["blocks"].append({"type": "text", "title": title, "text": str(element.get("text")), "page": page.get("number", 1), "source_refs": [ref], "element_id": element.get("id", "")})
    return [section for section in sections if section.get("blocks")]
