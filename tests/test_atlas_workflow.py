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
    assert calls == [guide_ai.GUIDE_SYSTEM_SCHEMA]


def test_alternative_paths_not_combined_as_required(store):
    candidate = system()
    candidate["nodes"].append({"id": "c", "label": "Outra origem", "source_refs": candidate["source_refs"]})
    candidate["edges"].append({"from": "c", "to": "b", "requirements": [], "source_refs": candidate["source_refs"]})
    saved = store.save_system("game", candidate)["system"]
    state = store.set_system_goal("game", saved["id"], saved["nodes"][1]["id"])
    objective = store.system_objective(store.current("game"), state)
    assert objective["alternative_paths"] == 2
    assert objective["requirements_total"] == 0
