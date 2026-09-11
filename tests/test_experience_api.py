import json
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

import engine
import experience
import smart_guide
from experience_api import ExperienceApi
from tests.test_atlas_workflow import SECTIONS, system


@pytest.fixture
def api(tmp_path, monkeypatch):
    for name, sub in [("GAMES_DIR", "config/games"), ("CACHE_DIR", "config/cache"),
                      ("BADGES_DIR", "assets/badges"), ("ICONS_DIR", "assets/icons"),
                      ("ART_DIR", "assets/art"), ("GUIDES_DIR", "config/guides"),
                      ("GUIDE_MEDIA_DIR", "assets/guides")]:
        monkeypatch.setattr(engine, name, tmp_path / sub)
    monkeypatch.setattr(engine, "DATA_DIR", tmp_path)
    monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "config/secrets.json")
    monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "config/settings.json")
    value = engine.Api()
    game = {"slug":"game","title":"Aventura","platform":"Console","retroachievements_game_id":1,"art":{},"achievements":[]}
    engine.GAMES_DIR.mkdir(parents=True, exist_ok=True)
    smart_guide._atomic_json(engine.GAMES_DIR / "game.json", game)
    value.state["game"] = deepcopy(game)
    value._active_slug = "game"
    value._guides.ensure_source("game", "Fonte", SECTIONS)
    return value


def test_draft_media_search_keeps_custom_query(api, monkeypatch):
    source = api._guides.add_system_source('game', 'Atlas', 'pdf', SECTIONS)
    sid = source['id']
    api._guides.update_system_source('game', sid, status='running', job_id='j')
    draft = api._guides.save_atlas_draft('game', sid, system(), 'j')['system']
    calls = []
    monkeypatch.setattr(api, 'search_web_images', lambda *args: calls.append(args) or {'ok': True, 'results': []})
    result = api.search_guide_system_media('game', draft['id'], draft['nodes'][0]['id'], 'Entity artwork', 0, sid)
    assert result['ok']
    assert calls[0][1] == 'Entity artwork'
    assert not api.search_guide_system_media('game', draft['id'], 'missing', '', 0, sid)['ok']


def test_atlas_faq_sections_keep_page_numbers():
    faq = {'page_records': [{'number': i, 'url': f'https://example.test/{i}', 'text': f'1. Chapter {i}\n\nDocumented condition {i}.'} for i in range(1, 7)]}
    sections = engine.Api._atlas_faq_sections(faq)
    assert {section['page'] for section in sections} == set(range(1, 7))


def test_companion_progress_returns_no_source_or_private_notes(api):
    bundle = api.get_smart_guide("game")
    block = bundle["current"]["chapters"][0]["blocks"][0]
    api._guides.update_progress("game", "note", block["id"], "private-note")
    before = api._companion_snapshot()
    assert "private-note" not in json.dumps(before)
    result = api._companion_command({"kind":"progress","slug":"game","version":before["version"],"action":"complete","block_id":block["id"],"value":True})
    assert result == {"ok": True}
    assert api._companion_command({"kind":"progress","slug":"game","version":before["version"],"action":"complete","block_id":block["id"],"value":False})["conflict"]
    assert block["id"] in api._guides.progress("game")["completed"]


def test_companion_v2_is_target_scoped_and_idempotent(api):
    block = api.get_smart_guide("game")["current"]["chapters"][0]["blocks"][0]
    snapshot = api._companion_snapshot("game")
    body = {
        "api_version": 2, "request_id": "mobile-request-1", "kind": "progress",
        "slug": "game", "action": "complete", "value": True,
        "target": {"block_id": block["id"]},
        "expected_definition_revision": snapshot["definition_revision"],
        "expected_value_version": 0,
    }
    first = api._companion_command(body)
    second = api._companion_command(body)
    assert first["ok"] and not first["idempotent"]
    assert second["ok"] and second["idempotent"]
    stale = {**body, "request_id": "mobile-request-2", "value": False, "expected_value_version": 0}
    assert api._companion_command(stale)["conflict"]


def test_companion_v2_rejects_string_boolean(api):
    block = api.get_smart_guide("game")["current"]["chapters"][0]["blocks"][0]
    snapshot = api._companion_snapshot("game")
    result = api._companion_command({
        "api_version": 2, "request_id": "mobile-request-bad", "kind": "progress",
        "slug": "game", "action": "complete", "value": "true",
        "target": {"block_id": block["id"]},
        "expected_definition_revision": snapshot["definition_revision"],
        "expected_value_version": 0,
    })
    assert not result["ok"] and result["code"] == "validation_error"


def test_documented_item_requirement_is_cataloged_without_new_atlas_node(api):
    saved = api._guides.save_system("game", {
        "title": "Evoluções", "origin": "manual", "status": "approved",
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "edges": [{"id": "edge", "from": "a", "to": "b", "requirements": [{
            "id": "req", "text": "Use Sacred Wings on A", "mode": "item",
            "condition": {"op": "item", "item_name": "Sacred Wings", "action": "use"},
            "source_refs": [{"section": 1, "block": 1}],
        }], "source_refs": [{"section": 1, "block": 1}]}],
    })["system"]
    api._refresh_smart_bundle("game")
    items = api.get_guide_items("game")["items"]
    assert [item["name"] for item in items] == ["Sacred Wings"]
    assert all(node["label"] != "Sacred Wings" for node in saved["nodes"])


def test_companion_does_not_publish_drafts_or_expose_spoilers(api):
    saved = api._guides.save_system("game", system())["system"]
    assert not api._companion_snapshot()["systems"]
    saved["status"] = "approved"
    saved["nodes"][1]["spoiler"] = True
    api._guides.save_system("game", saved)
    snapshot = api._companion_snapshot()
    assert len(snapshot["systems"][0]["nodes"]) == 1
    assert not snapshot["systems"][0]["edges"]
    result=api._companion_command({"kind":"goal","slug":"game","version":snapshot["version"],"system_id":saved["id"],"node_id":saved["nodes"][1]["id"]})
    assert not result["ok"]


def test_art_preview_crop_and_undo_preserves_original(api, monkeypatch):
    def download(url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 160), "blue").save(path)
        return {"ok":True,"sha256":"sample","width":200,"height":160}
    monkeypatch.setattr(engine.image_fetch, "download_image_validated", download)
    before = engine.load_game_file(engine.GAMES_DIR / "game.json")
    preview = api.prepare_art_preview("game", "https://images.example/image.png")
    assert preview["ok"]
    assert engine.load_game_file(engine.GAMES_DIR / "game.json") == before
    result = api.apply_art_preview("game", preview["token"], "cover", {"x":0,"y":0,"width":.5,"height":1})
    assert result["ok"]
    game = engine.load_game_file(engine.GAMES_DIR / "game.json")
    assert game["art_meta"]["cover"]["width"] == 100
    assert api.undo_game_art("game")["ok"]
    assert engine.load_game_file(engine.GAMES_DIR / "game.json")["art"] == before["art"]


def test_art_failed_download_keeps_current_and_rejects_bad_crop(api, monkeypatch):
    monkeypatch.setattr(engine.image_fetch, "download_image_validated", lambda *args:{"ok":False,"error":"blocked"})
    assert not api.prepare_art_preview("game", "https://images.example/image.png")["ok"]
    assert api.state["game"]["art"] == {}
    assert not api.apply_art_preview("game", "unknown", "cover", {})["ok"]


def test_palette_undo_and_diagnostics_no_credentials(api):
    assert api.set_game_palette("game", "#123456", "#789ABC")["ok"]
    assert api.undo_game_art("game")["ok"]
    report = json.dumps(api.get_diagnostics_report())
    assert "api_key" not in report and "username" not in report


def test_import_manifest_preview_does_not_start_network(api, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "start_bulk_import", lambda ids: calls.append(ids) or {"ok":True,"total":len(ids)})
    doc={"format":"digitracker-library","version":1,"provider":"retroachievements","games":[{"id":1},{"id":2}]}
    assert api.import_library_manifest(doc)["count"] == 1
    assert calls == []
    api.import_library_manifest(doc, True)
    assert calls == [[2]]
