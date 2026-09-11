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


def test_gravacao_atomica_reintenta_bloqueio_transitorio_do_windows(tmp_path, monkeypatch):
    destino = tmp_path / "system_sources" / "fonte.json"
    real_replace = smart_guide.os.replace
    chamadas = []

    def bloqueia_duas_vezes(origem, alvo):
        chamadas.append((origem, alvo))
        if len(chamadas) < 3:
            raise PermissionError(5, "Acesso negado")
        return real_replace(origem, alvo)

    monkeypatch.setattr(smart_guide.os, "replace", bloqueia_duas_vezes)
    monkeypatch.setattr(smart_guide.time, "sleep", lambda *_: None)
    smart_guide._atomic_json(destino, {"salvo": True})
    assert len(chamadas) == 3
    assert json.loads(destino.read_text(encoding="utf-8")) == {"salvo": True}


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


def _system(title="Progressão", origin="manual", status="approved"):
    refs = [{"section": 1, "block": 1, "page": 0}]
    return {
        "id": "", "title": title, "description": "Estrutura documentada.",
        "group_label": "Categoria", "layout": "layered", "origin": origin,
        "status": status, "source_refs": refs if origin == "ai" else [],
        "nodes": [
            {"id": "a", "label": "Origem", "subtitle": "", "stage": "I",
             "group": "Base", "tags": ["inicial"], "attributes": {},
             "media_query": "origem artwork", "spoiler": False,
             "source_refs": refs if origin == "ai" else []},
            {"id": "b", "label": "Destino", "subtitle": "", "stage": "II",
             "group": "Final", "tags": [], "attributes": {"nível": "10"},
             "media_query": "destino artwork", "spoiler": False,
             "source_refs": refs if origin == "ai" else []},
        ],
        "edges": [{
            "id": "", "from": "a", "to": "b", "label": "Avançar",
            "path_kind": "normal", "missable": False, "spoiler": False,
            "source_refs": refs if origin == "ai" else [],
            "requirements": [{"id": "", "text": "Cumprir condição",
                              "source_refs": refs if origin == "ai" else []}],
        }],
    }


@pytest.mark.parametrize("title", ["Evolução de criaturas", "Árvore de habilidades", "Receitas de crafting"])
def test_mesmo_schema_representa_sistemas_distintos(title):
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    doc["systems"] = [_system(title)]
    clean = smart_guide.validate_document(doc)
    assert clean["systems"][0]["title"] == title
    assert "digivolution" not in json.dumps(clean["systems"][0]).lower()


def test_ia_sem_referencia_ou_com_referencia_invalida_e_rejeitada():
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    system = _system(origin="ai", status="suggested")
    system["source_refs"] = [{"section": 0, "block": 0, "page": 0}]
    system["nodes"][0]["source_refs"] = []
    with pytest.raises(smart_guide.SmartGuideError, match="referência"):
        smart_guide.validate_document({**doc, "systems": [system]})


def test_referencia_da_ia_precisa_existir_na_fonte():
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    system = _system(origin="ai", status="suggested")
    system["nodes"][1]["source_refs"] = [{"section": 9, "block": 1, "page": 0}]
    clean = smart_guide.validate_document({**doc, "systems": [system]})
    with pytest.raises(smart_guide.SmartGuideError, match="fora da fonte"):
        smart_guide.validate_system_references(clean, SECTIONS)


def test_ids_visuais_normalizam_caixa_acentos_e_espacos():
    first = smart_guide._stable_system_id("node", "", "Evolução  Final", [{"section": 1}])
    second = smart_guide._stable_system_id("node", "", "evolucao final", [{"section": 1}])
    assert first == second


def test_valida_duplicados_referencias_e_limites():
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    duplicated = _system()
    duplicated["nodes"][1]["id"] = "a"
    with pytest.raises(smart_guide.SmartGuideError, match="duplicado"):
        smart_guide.validate_document({**doc, "systems": [duplicated]})
    missing = _system()
    missing["edges"][0]["to"] = "nao-existe"
    with pytest.raises(smart_guide.SmartGuideError, match="inexistente"):
        smart_guide.validate_document({**doc, "systems": [missing]})
    oversized = _system()
    oversized["nodes"] = [
        {**oversized["nodes"][0], "id": f"n{i}", "label": f"Nó {i}"}
        for i in range(smart_guide.MAX_SYSTEM_NODES + 1)
    ]
    with pytest.raises(smart_guide.SmartGuideError, match="limite"):
        smart_guide.validate_document({**doc, "systems": [oversized]})


def test_aceita_rotas_alternativas_entre_os_mesmos_nos():
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    system = _system()
    system["edges"].append({
        **system["edges"][0],
        "id": "",
        "label": "Avançar por outra rota",
        "path_kind": "alternative",
        "requirements": [{"id": "", "text": "Usar a chave especial"}],
    })
    clean = smart_guide.validate_document({**doc, "systems": [system]})["systems"][0]
    assert len(clean["edges"]) == 2
    assert {edge["path_kind"] for edge in clean["edges"]} == {"normal", "alternative"}

    duplicate = _system()
    duplicate["edges"].append({**duplicate["edges"][0], "id": ""})
    with pytest.raises(smart_guide.SmartGuideError, match="Relação duplicada"):
        smart_guide.validate_document({**doc, "systems": [duplicate]})


def test_aceita_ciclo_multiplas_raizes_e_no_orfao():
    doc = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    system = _system()
    system["nodes"].extend([
        {**system["nodes"][0], "id": "c", "label": "Outra raiz"},
        {**system["nodes"][0], "id": "d", "label": "Órfão"},
    ])
    system["edges"].extend([
        {**system["edges"][0], "id": "", "from": "b", "to": "a", "requirements": []},
        {**system["edges"][0], "id": "", "from": "c", "to": "b", "requirements": []},
    ])
    clean = smart_guide.validate_document({**doc, "systems": [system]})["systems"][0]
    assert len(clean["nodes"]) == 4 and len(clean["edges"]) == 3


def test_migra_v1_em_memoria_sem_regravar_documento(tmp_path):
    store = smart_guide.SmartGuideStore(tmp_path)
    legacy = smart_guide.from_legacy_sections("Jogo", SECTIONS)
    legacy["schema_version"] = 1
    legacy.pop("systems", None)
    legacy["visual_suggestions"] = [{
        "id": "graph-old", "type": "graph", "chapter_id": "", "title": "Fluxo",
        "reason": "Relação antiga", "query": "", "nodes": [
            {"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "edges": [{"from": "a", "to": "b", "label": "segue"}],
    }]
    path = store._path("jogo", "current.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    migrated = store.current("jogo")
    assert migrated["schema_version"] == 3 and len(migrated["systems"]) == 1
    assert path.read_text(encoding="utf-8") == before


def test_estado_visual_sobrevive_a_nova_revisao_e_restauracao(tmp_path):
    store = smart_guide.SmartGuideStore(tmp_path)
    source = store.ensure_source("jogo", "Jogo", SECTIONS)
    saved = store.save_system("jogo", _system())
    system = saved["system"]
    node = system["nodes"][1]
    edge = system["edges"][0]
    requirement = edge["requirements"][0]
    store.set_system_goal("jogo", system["id"], node["id"])
    store.update_requirement("jogo", system["id"], edge["id"], requirement["id"], True)
    store.set_system_media("jogo", system["id"], node["id"], "media_demo")
    revision_id = saved["revision"]["revision_id"]
    current = store.current("jogo")
    current["summary"] = "Nova revisão"
    store.publish("jogo", current, source["hash"], "manual", "")
    store.restore("jogo", revision_id)
    state = store.system_state("jogo")
    assert state["goals"][system["id"]] == node["id"]
    assert requirement["id"] in state["completed_requirements"]
    assert state["node_media"][f"{system['id']}:{node['id']}"] == "media_demo"
    assert store.system_objective(store.current("jogo"), state)["next_requirement"] == {}
