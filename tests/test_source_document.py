import source_document
import source_import
import guide_ai
from item_catalog import ItemCatalog, item_id


def test_html_source_preserves_headings_tables_empty_cells_and_spans():
    html = """
    <html><body><main>
      <h1>Evolution</h1><p>Use o item indicado.</p>
      <table><tr><th>Destino</th><th>Requisito</th><th>Extra</th></tr>
        <tr><td rowspan='2'>Greymon</td><td>25+</td><td></td></tr>
        <tr><td colspan='2'>Pedra</td></tr>
      </table>
    </main></body></html>
    """
    document = source_document.from_html(html, url="https://example.com/guide")
    table = next(element for page in document["pages"] for element in page["elements"] if element["type"] == "table")
    assert table["columns"] == 3
    assert table["rows"][1]["cells"][0]["rowspan"] == 2
    assert table["rows"][2]["cells"][0]["colspan"] == 2
    markdown = source_document.to_markdown(document)
    assert "|  |  |" in markdown or "| Pedra | Pedra |" in markdown
    refs = source_document.validate_references(document, [{"page_id": "page-0001", "table_id": table["table_id"]}, {"page_id": "missing"}])
    assert len(refs) == 1


def test_html_source_keeps_repeated_headers_and_ignores_nested_table_rows():
    html = """
    <main><table>
      <tr><th colspan='2'>Evolution</th></tr>
      <tr><th>Destination</th><th>Requirement</th></tr>
      <tr><td>Greymon</td><td>25+</td></tr>
      <tr><th>Destination</th><th>Requirement</th></tr>
      <tr><td>MetalGreymon</td><td>50+</td></tr>
      <tr><td colspan='2'><table><tr><td>layout only</td></tr></table>Final</td></tr>
    </table></main>
    """
    document = source_document.from_html(html, url="https://example.test/guide")
    table = next(element for page in document["pages"] for element in page["elements"]
                 if element["type"] == "table")
    assert len(table["header_rows"]) == 3
    assert [row["cells"][0]["text"] for row in table["header_rows"]] == [
        "Evolution", "Destination", "Destination"]
    # The nested row is not emitted as an outer row.  Its text may still be
    # part of the containing cell, which is the faithful editorial behavior.
    assert not any(cell["text"] == "layout only" for row in table["rows"]
                   for cell in row["cells"])


def test_gamefaqs_projection_keeps_page_and_table_row_references():
    faq = {"title": "FAQ", "complete": True, "page_records": [{"number": 4, "url": "https://example.com/4", "document": {
        "elements": [{"type": "heading", "level": 2, "title": "Agumon", "path": ["Agumon"]},
                     {"type": "table", "table_id": "t1", "columns": 2, "headers": [{"text": "Destino", "column": 0}], "rows": [{"id": "r1", "cells": [{"text": "Greymon", "column": 0}, {"text": "25+", "column": 1}]}]}]}}]}
    document = source_document.from_gamefaqs(faq)
    sections = source_document.to_guide_sections(document, source_id="src")
    table_block = next(block for section in sections for block in section["blocks"] if block["type"] == "table")
    assert table_block["page"] == 4
    assert table_block["source_refs"][0]["table_id"] == "t1"
    assert table_block["source_refs"][0]["row_id"] == "r1"


def test_item_catalog_keeps_possession_separate_from_entities(tmp_path):
    catalog = ItemCatalog(tmp_path)
    item = catalog.upsert("game", "Sacred Wings", category="evolution", item_kind="evolution")
    assert item["id"] == item_id("Sacred Wings")
    saved = catalog.set_quantity("game", item["id"], 2, expected_revision=0)
    assert catalog.list("game")[0]["quantity"] == 2
    assert saved["revision"] == 1


def test_static_capture_with_table_is_not_classified_as_empty(monkeypatch):
    class Response:
        status_code = 200
        url = "https://example.test/table"
        text = "<main><table><tr><th>Evolution</th></tr><tr><td>Greymon</td></tr></table></main>"

    class Client:
        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(source_import, "validate_public_url", lambda value: value)
    result = source_import.capture_http("https://example.test/table", session=Client())
    assert result["document"]["completeness"]["status"] == "complete"
    assert result["document"]["stats"]["tables"] == 1


def test_atlas_fragment_merge_does_not_collapse_same_text_different_operator():
    fragment = {
        "nodes": [{"id": "a", "label": "A", "source_refs": [{"page": 1}]},
                  {"id": "b", "label": "B", "source_refs": [{"page": 1}]}],
        "edges": [
            {"id": "e1", "from": "a", "to": "b", "label": "", "path_kind": "normal",
             "requirements": [{"text": "Weight", "field": "Weight", "operator": ">=", "value": "25", "source_refs": [{"page": 1}]}], "source_refs": [{"page": 1}]},
            {"id": "e2", "from": "a", "to": "b", "label": "", "path_kind": "normal",
             "requirements": [{"text": "Weight", "field": "Weight", "operator": "<=", "value": "25", "source_refs": [{"page": 2}]}], "source_refs": [{"page": 2}]},
        ], "source_refs": [{"page": 1}, {"page": 2}],
    }
    merged = guide_ai._merge_atlas_fragments([fragment], "Rules")
    assert len(merged["edges"]) == 2
