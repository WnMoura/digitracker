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
