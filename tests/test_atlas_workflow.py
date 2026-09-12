from copy import deepcopy
import pytest
import smart_guide
import guide_ai

SECTIONS = [{"title": "Sistema", "blocks": [{"type": "p", "text": "A leva a B quando a condição é cumprida."}]}]


def system():
    ref = [{"section": 1, "block": 1, "page": 1}]
    return {"title": "Classes", "origin": "ai", "status": "suggested", "source_refs": ref,
            "nodes": [{"id": "a", "label": "Inicial", "source_refs": ref},
                      {"id": "b", "label": "Avançado", "source_refs": ref}],
            "edges": [{"from": "a", "to": "b", "source_refs": ref,
                       "requirements": [{"text": "Condição", "source_refs": ref}]}]}


@pytest.fixture
def store(tmp_path):
    result = smart_guide.SmartGuideStore(tmp_path)
    result.ensure_source("game", "Game", SECTIONS)
    return result


def pending(store):
    source = store.add_system_source("game", "Classes", "pdf", SECTIONS)
    store.update_system_source("game", source["id"], status="running", job_id="job1")
    return source["id"]


def test_draft_media_is_isolated_and_survives_approval(store):
    sid = pending(store)
    draft = store.save_atlas_draft('game', sid, system(), 'job1')
    sys_id = draft['system']['id']
    node = draft['system']['nodes'][0]['id']
    before = store.current('game')
    store.set_system_media('game', sys_id, node, 'approved-image', sid)
    assert store.atlas_draft('game', sid)['node_media'][node] == 'approved-image'
    assert store.current('game') == before
    assert not store.system_state('game')['node_media']
    published = store.approve_atlas_draft('game', sid)
    assert store.system_state('game')['node_media'][f"{published['system']['id']}:{node}"] == 'approved-image'


def test_draft_preserves_extraction_diagnostics(store):
    sid = pending(store)
    draft = store.save_atlas_draft(
        'game', sid, system(), 'job1', {"batches": 6, "nodes": 42, "edges": 70})
    assert draft['diagnostics'] == {"batches": 6, "nodes": 42, "edges": 70}


def test_source_estrutura_e_capturas_brutas_ficam_locais(tmp_path):
    local = smart_guide.SmartGuideStore(tmp_path)
    local.ensure_source("game", "Game", SECTIONS)
    table = {"schema_version": 1, "format": "gamefaqs-json-v1", "pages": [], "stats": {"tables": 1}}
    saved = local.add_system_source(
        "game", "FAQ", "gamefaqs", SECTIONS,
        {"url": "https://gamefaqs.gamespot.com/psp/1/faqs/2", "source_format": "gamefaqs-json-v1"},
        structured=table, markdown="# FAQ\n", raw_pages=[{"number": 1, "url": "https://example.test", "html": "<p>local</p>"}],
    )
    full = local.system_source("game", saved["id"], include_sections=True)
    assert full["structured"]["format"] == "gamefaqs-json-v1"
    assert full["markdown"].startswith("# FAQ")
    assert full["raw_files"][0]["path"].endswith("page-1.html")
    assert local.system_source("game", saved["id"])["id"] == saved["id"]


def test_cancelled_draft_cannot_accept_media(store):
    sid = pending(store)
    draft = store.save_atlas_draft('game', sid, system(), 'job1')
    store.update_system_source('game', sid, status='cancelled')
    with pytest.raises(smart_guide.SmartGuideError, match='prévia'):
        store.set_system_media('game', draft['system']['id'], 'a', 'image', sid)


def test_draft_does_not_publish_and_approval_does(store):
    before = store.current("game")
    sid = pending(store)
    store.save_atlas_draft("game", sid, system(), "job1")
    assert store.current("game") == before
    assert store.system_source("game", sid)["status"] == "suggested"
    store.approve_atlas_draft("game", sid)
    assert store.current("game")["systems"][0]["status"] == "approved"


def test_pendencia_de_extracao_bloqueia_aprovacao(store):
    sid = pending(store)
    store.save_atlas_draft("game", sid, system(), "job1", {"pending_table_ids": ["p001-e0001"]})
    with pytest.raises(smart_guide.SmartGuideError, match="pendentes"):
        store.approve_atlas_draft("game", sid)


def test_reimportacao_estruturada_bloqueia_card_combinado(store):
    structured = {"format": "gamefaqs-json-v1", "pages": []}
    source = store.add_system_source(
        "game", "FAQ estruturado", "gamefaqs", SECTIONS,
        {"source_format": "gamefaqs-json-v1"}, structured=structured,
    )
    store.update_system_source("game", source["id"], status="running", job_id="job1")
    candidate = system()
    candidate["nodes"][0]["label"] = "Agumon, Guilmon"
    draft = store.save_atlas_draft("game", source["id"], candidate, "job1")
    assert draft["approval_blocked"] is True
    assert draft["diagnostics"]["pending_composite_cards"] == [
        {"card_number": 1, "label": "Agumon, Guilmon"}
    ]
    assert draft["diagnostics"]["pending_items"][0]["kind"] == "combined_entity_card"
    with pytest.raises(smart_guide.SmartGuideError, match="pendentes"):
        store.approve_atlas_draft("game", source["id"])


def test_delete_system_archives_its_source_but_keeps_capture(store):
    sid = pending(store)
    draft = store.save_atlas_draft("game", sid, system(), "job1")
    published = store.approve_atlas_draft("game", sid)
    system_id = published["system"]["id"]
    assert store.system_source("game", sid)["system_id"] == system_id
    store.delete_system("game", system_id)
    assert not store.current("game")["systems"]
    archived = store.system_source("game", sid)
    assert archived["status"] == "archived"
    assert "reimportação" in archived["message"]
    assert not store.atlas_draft("game", sid)
    assert store.system_source("game", sid, include_sections=True)["sections"]


def test_cancelled_and_old_workers_cannot_publish(store):
    sid = pending(store)
    store.update_system_source("game", sid, status="cancelled", job_id="")
    with pytest.raises(smart_guide.SmartGuideError):
        store.save_atlas_draft("game", sid, system(), "job1")
    store.update_system_source("game", sid, status="running", job_id="job2")
    with pytest.raises(smart_guide.SmartGuideError):
        store.save_atlas_draft("game", sid, system(), "job1")
    assert not store.current("game")["systems"]


def test_merge_and_legacy_import_preserve_atlas(store):
    saved = store.save_system("game", system())["system"]
    doc = smart_guide.from_legacy_sections("Novo guia", SECTIONS)
    store.save_merge_preview("game", doc, [], [])
    store.publish_merge_preview("game", "test", "test")
    assert store.current("game")["systems"] == [saved]
    store.ensure_source("game", "Outra fonte", SECTIONS)
    assert store.current("game")["systems"] == [saved]


def test_removed_last_source_does_not_reappear(store):
    source = store.walkthrough_sources("game")[0]
    store.remove_walkthrough_source("game", source["id"])
    assert store.walkthrough_sources("game") == []


def test_invalid_source_reference_preserves_current(store):
    sid = pending(store)
    candidate = system()
    candidate["nodes"][1]["source_refs"] = [{"section": 99, "block": 1}]
    before = store.current("game")
    with pytest.raises(smart_guide.SmartGuideError, match="fora da fonte"):
        store.save_atlas_draft("game", sid, candidate, "job1")
    assert store.current("game") == before


def test_paths_cannot_escape(store):
    with pytest.raises(smart_guide.SmartGuideError):
        store.system_source("game", "../../outside")


def test_system_only_ai_response_is_supported(monkeypatch):
    calls = []
    def fake(*args):
        calls.append(args[-1])
        return system()
    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setitem(guide_ai._CALLERS, provider, fake)
    result = guide_ai.generate_system_from_source(
        {"id": "source", "sections": SECTIONS}, "Classes", {}, {"provider": provider, "api_key": "test"})
    assert len(result["nodes"]) == 2
    assert calls == [guide_ai.ATLAS_EXTRACTION_SCHEMA]


def test_long_source_is_extracted_in_batches_and_all_paths_are_merged(monkeypatch):
    sections = [{"title": "Tabela", "page": 1, "blocks": [
        {"type": "p", "text": f"Origin | Target {index} | Requirement {index}" + " detail" * 14,
         "page": 1}
        for index in range(1, 5)
    ]}]
    calls = []

    def fake(_cfg, _system, payload, _schema):
        data = __import__('json').loads(payload)
        rows = []
        for row in data['rows_to_audit']:
            index = int(row['ref_id'].split('-b')[1].split('-')[0])
            calls.append(index)
            rows.append({
                "ref_id": row["ref_id"], "kind": "relation", "note": "",
                "relations": [{
                    "from_label": "Origin", "to_label": f"Target {index}",
                    "from_stage": "Base", "to_stage": "Next",
                    "from_group": "", "to_group": "", "from_tags": [],
                    "to_tags": [], "from_attributes": [], "to_attributes": [],
                    "label": "", "path_kind": "alternative", "requirements": [],
                    "missable": False, "spoiler": False,
                }],
            })
        return {"description": "", "group_label": "Estágio",
                "layout": "layered", "rows": rows}

    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setitem(guide_ai._CALLERS, provider, fake)
    monkeypatch.setattr(guide_ai, 'ATLAS_BATCH_MAX_CHARS', 300)
    seen_progress = []
    result = guide_ai.generate_system_from_source(
        {"id": "source", "sections": sections}, "Evoluções", {},
        {"provider": provider, "api_key": "test"},
        lambda done, total: seen_progress.append((done, total)))
    assert len(calls) == 4
    assert len(result['nodes']) == 5
    assert len(result['edges']) == 4
    assert result['_analysis']['batches'] == 4
    assert seen_progress[0] == (0, 4) and seen_progress[-1] == (4, 4)


def test_atlas_payload_is_small_and_does_not_send_achievements():
    batch = guide_ai._atlas_batches([{"title": "Tabela", "blocks": [
        {"text": "Agumon | Greymon | Weight 30", "page": 2}
    ], "page": 2, "_source_id": "src", "_source_section": 7}])[0]
    payload = __import__('json').loads(guide_ai._atlas_rows_payload(
        batch, "Evoluções", {"title": "Game", "achievements_meta": {
            "1": {"title": "Não deve ser enviado"}}}))
    assert "real_achievements" not in payload
    assert payload["rows_to_audit"][0]["ref_id"].startswith("r-s0007-b0001")


def test_row_protocol_attaches_canonical_reference_locally():
    batch = guide_ai._atlas_batches([{"title": "Tabela", "blocks": [
        {"text": "Agumon | Greymon", "page": 3}
    ], "page": 3, "_source_id": "src", "_source_section": 4}])[0]
    ref_id = guide_ai._atlas_input_rows(batch)[0]["ref_id"]
    raw = {"rows": [{"ref_id": ref_id, "kind": "relation", "relations": [{
        "from_label": "Agumon", "to_label": "Greymon", "requirements": ["Level 15"]
    }]}]}
    fragment, quality = guide_ai._atlas_fragment_from_rows(raw, batch, "Evoluções")
    assert quality["complete"]
    assert fragment["edges"][0]["source_refs"] == [{
        "source_id": "src", "section": 4, "block": 1, "page": 3,
    }]
    assert fragment["edges"][0]["requirements"][0]["source_refs"] == \
        fragment["edges"][0]["source_refs"]


def test_table_coverage_rejects_a_single_representative_path():
    batch = [{"title": "Tabela", "_source_section": 1, "blocks": [
        {"text": f"A{i} | B{i}", "_source_block": i} for i in range(1, 11)
    ]}]
    candidate = system()
    quality = guide_ai._atlas_batch_quality(batch, candidate)
    assert quality['table_blocks'] == 10
    assert quality['covered_table_blocks'] == 1
    assert not quality['complete']


def test_merge_preserves_alternative_conditions_for_same_endpoints():
    ref = [{"section": 1, "block": 1, "page": 1}]
    base = system()
    base['edges'][0]['requirements'] = [{"text": "Condition A", "source_refs": ref}]
    other = deepcopy(base)
    other['edges'][0]['requirements'] = [{"text": "Condition B", "source_refs": ref}]
    merged = guide_ai._merge_atlas_fragments([base, other], 'Paths')
    assert len(merged['nodes']) == 2
    assert len(merged['edges']) == 2


def test_alternative_paths_not_combined_as_required(store):
    candidate = system()
    candidate["nodes"].append({"id": "c", "label": "Outra origem", "source_refs": candidate["source_refs"]})
    candidate["edges"].append({"from": "c", "to": "b", "requirements": [], "source_refs": candidate["source_refs"]})
    saved = store.save_system("game", candidate)["system"]
    state = store.set_system_goal("game", saved["id"], saved["nodes"][1]["id"])
    objective = store.system_objective(store.current("game"), state)
    assert objective["alternative_paths"] == 2
    assert objective["requirements_total"] == 0


def _row_protocol_response(payload):
    data = __import__('json').loads(payload)
    rows = []
    for row in data['rows_to_audit']:
        suffix = row['ref_id'].split('-b')[1].split('-')[0]
        rows.append({
            "ref_id": row["ref_id"], "kind": "relation", "note": "",
            "relations": [{
                "from_label": "Origin", "to_label": f"Target {int(suffix)}",
                "requirements": [], "missable": False, "spoiler": False,
            }],
        })
    return {"description": "", "group_label": "Estágio",
            "layout": "layered", "rows": rows}


def test_interrupted_atlas_resumes_only_unfinished_batches(monkeypatch):
    sections = [{"title": "Tabela", "blocks": [
        {"type": "p", "text": f"Origin | Target {index} | Requirement {index}" + " detail" * 14}
        for index in range(1, 5)
    ]}]
    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setattr(guide_ai, 'ATLAS_BATCH_MAX_CHARS', 300)
    checkpoint = {}
    first_calls = []

    def interrupted(_cfg, _system, payload, _schema):
        first_calls.append(__import__('json').loads(payload)['rows_to_audit'][0]['ref_id'])
        if len(first_calls) == 2:
            raise guide_ai.GuideAIError(
                "Serviço temporariamente indisponível.", code="service_unavailable",
                provider=provider, retryable=True)
        return _row_protocol_response(payload)

    monkeypatch.setitem(guide_ai._CALLERS, provider, interrupted)
    with pytest.raises(guide_ai.GuideAIError, match="indisponível"):
        guide_ai.generate_system_from_source(
            {"id": "source", "sections": sections}, "Evoluções", {},
            {"provider": provider, "api_key": "test", "model": "model-a"},
            checkpoint_callback=lambda value: checkpoint.update(deepcopy(value)))
    assert checkpoint["completed"] == 1

    resumed_calls = []
    def resumed(_cfg, _system, payload, _schema):
        resumed_calls.append(__import__('json').loads(payload)['rows_to_audit'][0]['ref_id'])
        return _row_protocol_response(payload)

    monkeypatch.setitem(guide_ai._CALLERS, provider, resumed)
    result = guide_ai.generate_system_from_source(
        {"id": "source", "sections": sections}, "Evoluções", {},
        {"provider": provider, "api_key": "test", "model": "model-a"},
        checkpoint=checkpoint)
    assert len(resumed_calls) == result['_analysis']['batches'] - 1
    assert result['_analysis']['resumed_batches'] == 1
    assert first_calls[0] not in resumed_calls


def test_checkpoint_is_invalidated_when_model_changes(monkeypatch):
    sections = [{"title": "Tabela", "blocks": [
        {"type": "p", "text": "Origin | Target | Requirement"}
    ]}]
    provider = guide_ai.DEFAULT_PROVIDER
    checkpoint = {}
    monkeypatch.setitem(guide_ai._CALLERS, provider,
                        lambda _c, _s, payload, _j: _row_protocol_response(payload))
    guide_ai.generate_system_from_source(
        {"id": "source", "sections": sections}, "Evoluções", {},
        {"provider": provider, "api_key": "test", "model": "model-a"},
        checkpoint_callback=lambda value: checkpoint.update(deepcopy(value)))
    calls = []
    monkeypatch.setitem(guide_ai._CALLERS, provider,
                        lambda _c, _s, payload, _j: calls.append(payload) or
                        _row_protocol_response(payload))
    result = guide_ai.generate_system_from_source(
        {"id": "source", "sections": sections}, "Evoluções", {},
        {"provider": provider, "api_key": "test", "model": "model-b"},
        checkpoint=checkpoint)
    assert len(calls) == result['_analysis']['batches']
    assert result['_analysis']['resumed_batches'] == 0


def test_gamefaqs_json_mapeia_tabela_e_materializa_todas_as_linhas(monkeypatch):
    table_id = "p001-e0001"
    rows = [
        {"id": f"{table_id}-r0001", "index": 0, "cells": [
            {"tag": "th", "column": 0, "text": "Evolution", "colspan": 1, "rowspan": 1},
            {"tag": "th", "column": 1, "text": "Weight", "colspan": 1, "rowspan": 1},
            {"tag": "th", "column": 2, "text": "Quota", "colspan": 1, "rowspan": 1},
        ]},
        {"id": f"{table_id}-r0002", "index": 1, "cells": [
            {"tag": "td", "column": 0, "text": "Greymon", "colspan": 1, "rowspan": 1},
            {"tag": "td", "column": 1, "text": "25 or more", "colspan": 1, "rowspan": 1},
            {"tag": "td", "column": 2, "text": "3", "colspan": 1, "rowspan": 1},
        ]},
        {"id": f"{table_id}-r0003", "index": 2, "cells": [
            {"tag": "td", "column": 0, "text": "Tyrannomon", "colspan": 1, "rowspan": 1},
            {"tag": "td", "column": 1, "text": "25 or more", "colspan": 1, "rowspan": 1},
            {"tag": "td", "column": 2, "text": "3", "colspan": 1, "rowspan": 1},
        ]},
    ]
    source = {
        "id": "source-structured", "hash": "hash", "source_format": "gamefaqs-json-v1",
        "selection": {"table_ids": [table_id]},
        "sections": [{"title": "Página 1", "page": 1, "blocks": [
            {"table_id": table_id, "row_id": row["id"], "text": " | ".join(c["text"] for c in row["cells"]), "page": 1}
            for row in rows
        ]}],
        "structured": {"format": "gamefaqs-json-v1", "pages": [{"page": 1, "elements": [{
            "id": table_id, "type": "table", "title": "Agumon", "path": ["Rookie Digimon", "Agumon"],
            "headers": [{"text": "Evolution", "column": 0}, {"text": "Weight", "column": 1}, {"text": "Quota", "column": 2}],
            "columns": 3, "rows": rows,
        }]}]},
    }
    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setitem(guide_ai._CALLERS, provider, lambda *_args: {"tables": [{
        "table_id": table_id, "kind": "evolution", "source_column": -1, "target_column": 0,
        "condition_columns": [1, 2], "source_from_context": True, "target_from_context": False,
        "confidence": 1, "note": "",
    }]})
    result = guide_ai.generate_system_from_source(
        source, "Evoluções", {"title": "Digimon"},
        {"provider": provider, "api_key": "test", "model": "model-a"})
    assert {node["label"] for node in result["nodes"]} == {"Agumon", "Greymon", "Tyrannomon"}
    assert [node["card_number"] for node in result["nodes"]] == [1, 2, 3]
    assert len(result["edges"]) == 2
    requirements = result["edges"][0]["requirements"]
    assert {item["field"] for item in requirements} == {"Weight", "Quota"}
    assert next(item for item in requirements if item["field"] == "Quota")["operator"] == "unknown"
    assert any(item["kind"] == "unknown_requirement" for item in result["_analysis"]["warning_items"])
    assert result["_analysis"]["audited_rows"] == 2
    assert result["_analysis"]["selected_tables"] == 1
    assert result["_analysis"]["table_blocks"] == 2
    assert result["_analysis"]["covered_table_blocks"] == 2
    assert result["_analysis"]["source_pages"] == 1
    assert result["_analysis"]["referenced_pages"] == 1


def _structured_atlas_source(table_specs):
    """Monta uma captura mínima com referências estáveis para os testes do Atlas."""
    elements, blocks = [], []
    for table_id, path, headers, values in table_specs:
        rows = [{
            "id": f"{table_id}-r0001", "index": 0,
            "cells": [{"tag": "th", "column": index, "text": header,
                       "colspan": 1, "rowspan": 1}
                      for index, header in enumerate(headers)],
        }]
        for row_index, row_values in enumerate(values, 2):
            row_id = f"{table_id}-r{row_index:04d}"
            rows.append({
                "id": row_id, "index": row_index - 1,
                "cells": [{"tag": "td", "column": index, "text": value,
                           "colspan": 1, "rowspan": 1}
                          for index, value in enumerate(row_values)],
            })
        elements.append({
            "id": table_id, "type": "table", "title": path[-1] if path else "",
            "path": path, "headers": [{"text": header, "column": index}
                                       for index, header in enumerate(headers)],
            "columns": len(headers), "rows": rows,
        })
        blocks.extend({"table_id": table_id, "row_id": row["id"],
                       "text": " | ".join(cell["text"] for cell in row["cells"]),
                       "page": 1} for row in rows)
    return {
        "id": "source-item-rules", "hash": "item-rules", "source_format": "gamefaqs-json-v1",
        "selection": {"table_ids": [spec[0] for spec in table_specs]},
        "sections": [{"title": "Página 1", "page": 1, "blocks": blocks}],
        "structured": {"format": "gamefaqs-json-v1", "pages": [{
            "page": 1, "elements": elements,
        }]},
    }


def _structured_mapping_caller(payload):
    import json

    result = []
    for table in json.loads(payload)["tables_to_map"]:
        table_id = table["table_id"]
        if table_id.startswith("evo"):
            result.append({
                "table_id": table_id, "kind": "evolution", "source_column": -1,
                "target_column": 0, "condition_columns": [1],
                "source_from_context": True, "target_from_context": False,
                "confidence": 1, "note": "",
            })
        elif table_id.startswith("ref"):
            result.append({
                "table_id": table_id, "kind": "unknown", "source_column": -1,
                "target_column": -1, "condition_columns": [],
                "source_from_context": False, "target_from_context": False,
                "confidence": 1, "note": "",
            })
        else:
            # Simula uma resposta antiga que classificava Requirements como
            # evolução: a guarda local deve corrigir isso antes de criar cards.
            result.append({
                "table_id": table_id, "kind": "evolution", "source_column": -1,
                "target_column": -1, "condition_columns": [],
                "source_from_context": False, "target_from_context": False,
                "confidence": .4, "note": "",
            })
    return {"tables": result}


def test_requirement_item_table_is_attached_without_item_cards(monkeypatch):
    source = _structured_atlas_source([
        ("evo-agumon", ["Rookie Digimon", "Agumon"],
         ["Evolution", "Requirements"],
         [["Examon", "Special Digivolution (via Evolution Item)"]]),
        ("req-examon", ["Special Digivolution (via Evolution Item)", "Examon"],
         ["Requirements"],
         [["Use Emperor Dragon's Great Spear on ANY Mega"]]),
    ])
    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setitem(guide_ai._CALLERS, provider,
                        lambda _cfg, _system, payload, _schema:
                        _structured_mapping_caller(payload))
    result = guide_ai.generate_system_from_source(
        source, "Evoluções", {"title": "Digimon"},
        {"provider": provider, "api_key": "test", "model": "model-a"})
    assert {node["label"] for node in result["nodes"]} == {"Agumon", "Examon"}
    assert len(result["edges"]) == 1
    requirement = result["edges"][0]["requirements"][-1]
    assert requirement["mode"] == "item"
    assert requirement["field"] == "Requirements"
    assert requirement["original"] == "Use Emperor Dragon's Great Spear on ANY Mega"
    assert "Emperor Dragon" in requirement["text"]
    assert result["_analysis"]["pending_items"] == []
    assert result["_analysis"]["covered_table_blocks"] == result["_analysis"]["table_blocks"] == 2
    assert result["edges"][0]["label"] == "Especial · Item"


def test_jogress_requirement_keeps_partner_as_condition(monkeypatch):
    source = _structured_atlas_source([
        ("evo-bancho", ["Mega", "BanchoLeomon"],
         ["Evolution", "Requirements"],
         [["Chaosmon", "Special Digivolution (via Jogress/DigiMemory)"]]),
        ("req-chaosmon", ["Special Digivolution (via Jogress/DigiMemory)", "Chaosmon"],
         ["Digimemory", "+", "Partner Digimon"],
         [["BanchoLeomon", "+", "Darkdramon"],
          ["Darkdramon", "+", "BanchoLeomon"]]),
    ])
    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setitem(guide_ai._CALLERS, provider,
                        lambda _cfg, _system, payload, _schema:
                        _structured_mapping_caller(payload))
    result = guide_ai.generate_system_from_source(
        source, "Evoluções", {"title": "Digimon"},
        {"provider": provider, "api_key": "test", "model": "model-a"})
    assert {node["label"] for node in result["nodes"]} == {"BanchoLeomon", "Chaosmon"}
    edge = result["edges"][0]
    assert edge["label"] == "Especial · Jogress/DigiMemory"
    requirement = next(item for item in edge["requirements"] if "Darkdramon" in item.get("text", ""))
    assert "BanchoLeomon" in requirement["text"]
    assert "Darkdramon" in requirement["text"]
    assert sum(item.get("mode") == "jogress" and "Darkdramon" in item.get("text", "")
               for item in edge["requirements"]) == 1
    assert "+" not in {node["label"] for node in result["nodes"]}
    assert result["_analysis"]["pending_items"] == []


def test_item_location_is_reference_not_an_atlas_endpoint(monkeypatch):
    source = _structured_atlas_source([
        ("evo-agumon", ["Rookie Digimon", "Agumon"],
         ["Evolution", "Requirements"], [["Greymon", "25 or more"]]),
        ("ref-location", ["Evolution Item Location"], ["Item", "Location"],
         [["Sacred wings", "File City Shop"]]),
    ])
    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setitem(guide_ai._CALLERS, provider,
                        lambda _cfg, _system, payload, _schema:
                        _structured_mapping_caller(payload))
    result = guide_ai.generate_system_from_source(
        source, "Evoluções", {"title": "Digimon"},
        {"provider": provider, "api_key": "test", "model": "model-a"})
    assert {node["label"] for node in result["nodes"]} == {"Agumon", "Greymon"}
    assert result["_analysis"]["pending_items"] == []
    assert result["_analysis"]["excluded_rows"] == 1


def test_item_and_location_columns_keep_item_identity_separate(monkeypatch):
    source = _structured_atlas_source([
        ("evo-item", ["Special Digivolution", "Examon"],
         ["Evolution", "Requirements"], [["Examon X", "Evolution Item"]]),
        ("req-item", ["Special Digivolution", "Examon X"],
         ["Item", "Location"], [["3x Sacred Wings", "File City Shop"]]),
    ])
    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setitem(guide_ai._CALLERS, provider,
                        lambda _cfg, _system, payload, _schema:
                        _structured_mapping_caller(payload))
    result = guide_ai.generate_system_from_source(
        source, "Evoluções", {"title": "Digimon"},
        {"provider": provider, "api_key": "test", "model": "model-a"})
    edge = result["edges"][0]
    requirement = next(item for item in edge["requirements"]
                       if item.get("mode") == "item" and item.get("condition"))
    assert requirement["condition"]["item_name"] == "Sacred Wings"
    assert requirement["condition"]["quantity"] == 3
    assert requirement["location"] == "File City Shop"
    assert "File City Shop" in requirement["text"]
    assert all(item.get("text") != "Requirements: Evolution Item"
               for item in edge["requirements"])


def test_structured_numeric_requirement_is_typed_and_evaluable(monkeypatch):
    source = _structured_atlas_source([
        ("evo-numeric", ["Rookie Digimon", "Agumon"],
         ["Evolution", "Weight"], [["Greymon", "25 or more"]]),
    ])
    provider = guide_ai.DEFAULT_PROVIDER
    monkeypatch.setitem(guide_ai._CALLERS, provider,
                        lambda _cfg, _system, payload, _schema:
                        _structured_mapping_caller(payload))
    result = guide_ai.generate_system_from_source(
        source, "Evoluções", {"title": "Digimon"},
        {"provider": provider, "api_key": "test", "model": "model-a"})
    requirement = result["edges"][0]["requirements"][0]
    assert requirement["value"] == 25
    assert requirement["operator"] == ">="
    assert requirement["condition"]["op"] == "compare"
    assert requirement["condition"]["value"] == 25


def test_requirement_without_relation_has_specific_pending_issue():
    source = _structured_atlas_source([
        ("req-orphan", ["Special Digivolution (via Evolution Item)", "Missingmon"],
         ["Requirements"], [["Use X-Program on Missingmon"]]),
    ])
    fragment, quality = guide_ai._materialize_structured_atlas(
        source, "Evoluções", {"title": "Digimon"}, {
            "req-orphan": {"kind": "requirement", "source_column": -1,
                            "target_column": -1, "condition_columns": [],
                            "source_from_context": False, "target_from_context": False},
        })
    assert fragment["nodes"] == []
    assert quality["pending_items"][0]["kind"] == "requirement_without_relation"
    assert quality["pending_items"][0]["kind"] != "missing_endpoint"


def test_attribute_table_and_dash_placeholders_are_not_requirements():
    table = {
        "title": "Examon", "path": ["Special Digivolution (via Evolution Item)", "Examon"],
        "headers": [], "rows": [
            {"index": 0, "cells": [{"column": 0, "text": "Attribute"}, {"column": 1, "text": "Vaccine"}]},
            {"index": 1, "cells": [{"column": 0, "text": "Nature"}, {"column": 1, "text": "Air, Fighting"}]},
        ],
    }
    assert guide_ai._structured_table_semantic_role(table, "evolution") == "reference"
    assert guide_ai._structured_value_is_empty("---")


def test_atlas_checkpoint_is_atomic_and_owned_by_active_worker(tmp_path):
    store = smart_guide.SmartGuideStore(tmp_path)
    store.ensure_source("game", "Game", SECTIONS)
    source = store.add_system_source("game", "Classes", "pdf", SECTIONS)
    source_id = source["id"]
    store.update_system_source("game", source_id, status="running", job_id="job-a")
    saved = store.save_atlas_checkpoint(
        "game", source_id, {"fingerprint": "fp", "batches": {"1": {}}}, "job-a")
    assert saved["source_id"] == source_id
    assert smart_guide.SmartGuideStore(tmp_path).atlas_checkpoint(
        "game", source_id)["fingerprint"] == "fp"
    with pytest.raises(smart_guide.SmartGuideError, match="substituída"):
        store.save_atlas_checkpoint("game", source_id, {}, "job-antigo")
    assert store.clear_atlas_checkpoint("game", source_id, "job-antigo") is False
    assert store.clear_atlas_checkpoint("game", source_id, "job-a") is True
    assert store.atlas_checkpoint("game", source_id) == {}


def test_origin_list_corroborates_existing_routes_and_keeps_one_card_per_entity():
    source = _structured_atlas_source([
        ("evo-a", ["Rookie Digimon", "Agumon"], ["Evolution", "HP"], [["Greymon", "1000"]]),
        ("evo-b", ["Rookie Digimon", "Guilmon"], ["Evolution", "HP"], [["Greymon", "900"]]),
        ("reverse", ["Champion Digimon", "Greymon"], ["Evolves from"], [["Agumon*, Guilmon, Agumon"]]),
        ("repeat", ["Rookie Digimon", "Agumon"], ["Evolution", "HP"], [["Greymon", "1000"]]),
    ])
    maps = {key: {"kind": "evolution", "source_from_context": True, "target_column": 0, "condition_columns": [1]}
            for key in ("evo-a", "evo-b", "repeat")}
    maps["reverse"] = {"kind": "reverse_origin", "source_column": 0, "target_from_context": True}
    fragment, quality = guide_ai._materialize_structured_atlas(source, "Evoluções", {}, maps)
    assert {node["label"] for node in fragment["nodes"]} == {"Agumon", "Guilmon", "Greymon"}
    assert len(fragment["edges"]) == 2
    assert all(edge["requirements"] for edge in fragment["edges"])
    assert quality["covered_rows"] == quality["selected_rows"] == 4
    assert quality["complete"] and len(quality["row_outcomes"]) == 4
    assert next(node for node in fragment["nodes"] if node["label"] == "Greymon")["stage"] == "Champion"


def test_alternative_endpoints_count_source_row_once_and_keep_operators():
    source = _structured_atlas_source([
        ("evo-list", ["Rookie", "Agumon"], ["Evolution", "Weight"],
         [["Greymon, Birdramon", "25 or more"], ["Greymon", "25 or less"]]),
    ])
    fragment, quality = guide_ai._materialize_structured_atlas(source, "Evoluções", {}, {
        "evo-list": {"kind": "evolution", "source_from_context": True, "target_column": 0, "condition_columns": [1]}})
    assert len(fragment["nodes"]) == 3 and len(fragment["edges"]) == 3
    assert quality["selected_rows"] == quality["covered_rows"] == 2
    assert {edge["requirements"][0]["operator"] for edge in fragment["edges"]} == {">=", "<="}


def test_image_job_survives_store_restart_and_manual_edit_wins(store):
    saved = store.save_system("game", system())["system"]
    sid, node = saved["id"], saved["nodes"][0]["id"]
    store.save_image_fill_job("game", sid, "", {"phase": "waiting_retry", "pending_ids": [node]})
    assert smart_guide.SmartGuideStore(store.root).image_fill_job("game", sid)["pending_ids"] == [node]
    store.set_system_media("game", sid, node, "manual")
    assert store.set_system_media_if_current("game", sid, node, "automatic", "") is False
    assert store.system_state("game")["node_media"][f"{sid}:{node}"] == "manual"


def test_reprocess_reuses_only_same_entity_path_and_progress(store):
    original = system()
    original["status"] = "approved"
    original["nodes"].append({"id": "combined", "label": "Agumon*, Guilmon", "source_refs": original["source_refs"]})
    saved = store.save_system("game", original)["system"]
    sid = saved["id"]
    node_id, combined_id = saved["nodes"][0]["id"], saved["nodes"][2]["id"]
    requirement_id = saved["edges"][0]["requirements"][0]["id"]
    store.set_system_media("game", sid, node_id, "kept-image")
    store.set_system_media("game", sid, combined_id, "wrong-composite-image")
    store.set_system_goal("game", sid, node_id)
    store.update_requirement("game", sid, saved["edges"][0]["id"], requirement_id, True)
    source_id = pending(store)
    store.update_system_source("game", source_id, replace_system_id=sid)
    fresh = system()
    fresh["nodes"].extend({"id": label, "label": label, "source_refs": fresh["source_refs"]}
                          for label in ("Agumon", "Guilmon"))
    draft = store.save_atlas_draft("game", source_id, fresh, "job1")
    assert draft["system"]["nodes"][0]["id"] == node_id
    assert draft["system"]["edges"][0]["requirements"][0]["id"] == requirement_id
    assert len(store.current("game")["systems"][0]["nodes"]) == 3
    result = store.approve_atlas_draft("game", source_id)
    assert result["system"]["id"] == sid
    state = store.system_state("game")
    assert state["node_media"][f"{sid}:{node_id}"] == "kept-image"
    assert state["goals"][sid] == node_id and requirement_id in state["completed_requirements"]
    assert all(f"{sid}:{node['id']}" not in state["node_media"] for node in result["system"]["nodes"][-2:])
