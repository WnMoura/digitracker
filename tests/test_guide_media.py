from __future__ import annotations

import base64
import json

import pytest
from pypdf import PdfWriter

import guide_media


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def library(tmp_path):
    return guide_media.GuideMediaLibrary(tmp_path / "guides", tmp_path / "assets")


def test_imagem_local_e_deduplicada(library):
    encoded = base64.b64encode(PNG_1PX).decode()
    first = library.add_local("jogo", encoded, "mapa.png")
    second = library.add_local("jogo", encoded, "outra.png")
    assert first["id"] == second["id"]
    assert len(library.list("jogo")) == 1


def test_diagrama_svg_e_gerado_por_dados_estruturados(library):
    item = library.create_diagram("jogo", {
        "title": "Rota", "nodes": [{"id": "a", "label": "Início <script>"},
                                      {"id": "b", "label": "Fim"}],
        "edges": [{"from": "a", "to": "b"}],
    })
    svg_path = library.assets_root / "jogo" / item["url"].rsplit("/", 1)[-1]
    svg = svg_path.read_text(encoding="utf-8")
    assert "<script>" not in svg and "&lt;script&gt;" in svg


def test_url_privada_e_recusada(monkeypatch):
    monkeypatch.setattr(guide_media.socket, "getaddrinfo", lambda *a: [(2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(guide_media.GuideMediaError, match="privados"):
        guide_media._safe_remote_url("http://example.test/image.png")


def test_pdf_sem_texto_recebe_diagnostico_scanned(tmp_path, library):
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=320, height=240)
    with path.open("wb") as stream:
        writer.write(stream)
    result = library.extract_pdf("jogo", path.read_bytes(), "blank.pdf")
    assert result["pages"] == 1 and result["scanned"] is True


def test_openverse_preserva_atribuicao(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"results": [{"id": "1", "title": "Mapa", "url": "https://x/img.png",
                                  "thumbnail": "https://x/thumb.png", "creator": "Autor",
                                  "license": "cc0", "license_url": "https://license",
                                  "foreign_landing_url": "https://source", "attribution": "Autor / CC0"}]}
    monkeypatch.setattr(guide_media.requests, "get", lambda *a, **k: Response())
    result = guide_media.GuideMediaLibrary.search("mapa", "openverse")
    assert result[0]["creator"] == "Autor" and result[0]["license"] == "cc0"

