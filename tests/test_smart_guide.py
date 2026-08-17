from __future__ import annotations

import base64
import json
import zipfile
from io import BytesIO

import pytest

import smart_guide


SECTIONS = [{
    "num": "1", "title": "Primeira área", "blocks": [
        {"type": "step", "text": "Siga pela porta azul."},
        {"type": "note", "text": "Salve antes de entrar."},
        {"type": "li", "text": "Leve três itens de cura."},
    ],
}]


def test_fallback_generico_nao_altera_fonte():
    original = json.loads(json.dumps(SECTIONS))
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    assert SECTIONS == original
    assert {b["type"] for b in doc["chapters"][0]["blocks"]} == {
        "objective", "warning", "checklist",
    }


def test_schema_recusa_tipo_especifico_de_franquia():
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    doc["chapters"][0]["blocks"][0]["type"] = "digivolution"
    with pytest.raises(smart_guide.SmartGuideError, match="não permitido"):
        smart_guide.validate_document(doc)


def test_store_preserva_fontes_e_limita_revisoes(tmp_path):
    store = smart_guide.SmartGuideStore(tmp_path)
    first = store.ensure_source("jogo", "Jogo", SECTIONS, {"source": "pdf"})
    store.ensure_source("jogo", "Jogo", [{"title": "Nova", "blocks": [{"type": "p", "text": "novo"}]}])
    assert (tmp_path / "jogo" / "sources" / f"{first['hash']}.json").exists()
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    for index in range(14):
        doc["summary"] = str(index)
        store.publish("jogo", doc, first["hash"], "teste", "modelo")
    assert len(store.revisions("jogo")) == smart_guide.MAX_REVISIONS


def test_progresso_next_objective_e_restauracao(tmp_path):
    store = smart_guide.SmartGuideStore(tmp_path)
    source = store.ensure_source("jogo", "Jogo", SECTIONS)
    current = store.current("jogo")
    first_id = current["chapters"][0]["blocks"][0]["id"]
    progress = store.update_progress("jogo", "complete", first_id, True)
    assert first_id in progress["completed"]
    assert store.next_objective(current, progress)["block_id"] != first_id
    old_id = current["revision_id"]
    restored = store.restore("jogo", old_id)
    assert restored["restored_from"] == old_id
    assert restored["source_hash"] == source["hash"]


def test_pacote_portatil_ignora_path_traversal(tmp_path):
    source_store = smart_guide.SmartGuideStore(tmp_path / "a")
    source_store.ensure_source("jogo", "Jogo", SECTIONS)
    name, encoded = source_store.export_pack("jogo")
    assert name.endswith(".dtguide")
    raw = BytesIO(base64.b64decode(encoded))
    rewritten = BytesIO()
    with zipfile.ZipFile(raw) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info))
        target.writestr("../../escape.json", "{}")
    target_store = smart_guide.SmartGuideStore(tmp_path / "b")
    result = target_store.import_pack("destino", base64.b64encode(rewritten.getvalue()).decode())
    assert result["ok"] is True
    assert not (tmp_path / "escape.json").exists()


def test_pacote_portatil_inclui_e_restaura_midia(tmp_path):
    source_store = smart_guide.SmartGuideStore(tmp_path / "a")
    source_store.ensure_source("jogo", "Jogo", SECTIONS)
    source_media = tmp_path / "media-a"
    source_media.mkdir()
    (source_media / "mapa.png").write_bytes(b"imagem-local")

    _, encoded = source_store.export_pack("jogo", media_dir=source_media)

    target_store = smart_guide.SmartGuideStore(tmp_path / "b")
    target_media = tmp_path / "media-b"
    result = target_store.import_pack("destino", encoded, media_dir=target_media)
    assert result["ok"] is True
    assert (target_media / "mapa.png").read_bytes() == b"imagem-local"
