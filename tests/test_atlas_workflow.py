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
