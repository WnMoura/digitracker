import json
import time
from copy import deepcopy
from pathlib import Path

import pytest

import companion
import experience
from data_tools import validate_library, write_backup


@pytest.fixture
def store(tmp_path):
    return experience.ExperienceStore(tmp_path / "experience.sqlite3")


def game(earned=True, hardcore=False):
    return {"slug": "sample", "title": "Aventura", "platform": "Console", "achievements": [
        {"id": 42, "name": "Primeira conquista", "desc": "Encontre o mapa", "points": 5,
         "earned": earned, "hardcore": hardcore, "rarity": 20, "rarity_hardcore": 10,
         "date_raw": "2026-09-05T12:00:00Z"}]}


def test_first_sync_silent_and_hardcore_promotion_once(store):
    assert store.ingest("a", game()) == []
    assert store.events("a", True) == []
    event = store.ingest("a", game(hardcore=True))[0]
    assert event["mode"] == "hardcore" and event["rarity"] == 10
    assert store.ingest("a", game(hardcore=True)) == []
    assert len(store.events("a", True)) == 1
    store.acknowledge("a", event["id"])
    assert store.events("a", True) == []


def test_new_unlock_notification(store):
    store.ingest("a", game(False))
    assert len(store.ingest("a", game(True))) == 1
    assert store.events("b") == []


def test_search_scoped_accents_and_spoilers(store):
    bundle = {"current": {"chapters": [{"title": "Capítulo", "blocks": [
        {"id": "one", "title": "Evolução", "text": "Obtenha uma pedra", "type": "objective"},
        {"id": "hidden", "title": "Segredo proibido", "text": "Spoiler", "type": "spoiler"}]}]},
        "progress": {"notes": {"one": "Anotação particular"}}}
    store.index_game("a", game(), bundle)
    assert store.search("a", "evolucao")[0]["kind"] == "guide"
    assert store.search("a", "conquista")[0]["kind"] == "achievement"
    assert not store.search("b", "evolução")
    assert not store.search("a", "proibido")
    assert store.search("a", "particular")
    assert not store.search("a", "particular", streamer=True)
    assert not store.search("a", '" OR *')


def test_game_preferences_persist_per_account(store):
    store.game_preference("a", "sample", {"favorite": True, "status": "paused", "opened": True})
    assert store.game_preference("a", "sample")["favorite"]
    assert not store.game_preference("b", "sample")["favorite"]
    with pytest.raises(ValueError):
        store.game_preference("a", "sample", {"status": "mastered"})


def test_session_pause_resume_restart_does_not_count_offline(store, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(experience.time, "time", lambda: clock[0])
    assert store.session("a", "start", "sample")["seconds"] == 0
    clock[0] += 60
    assert store.session("a", "pause")["seconds"] == 60
    clock[0] += 600
    assert store.session("a")["seconds"] == 60
    store.session("a", "resume")
    clock[0] += 20
    assert store.session("a")["seconds"] == 80
    restarted = experience.ExperienceStore(store.path)
    clock[0] += 6000
    assert restarted.session("a")["seconds"] == 80
    assert restarted.session("a")["status"] == "paused"


@pytest.fixture
def server(tmp_path):
    service = companion.CompanionServer(tmp_path / "ui", tmp_path / "assets", lambda slug: {"ok": True, "slug": slug}, lambda body: {"ok": True})
    service.host, service.port = "127.0.0.1", 8766
    return service


def client_for(server):
    client = server.app.test_client()
    client.environ_base["HTTP_HOST"] = "127.0.0.1:8766"
    return client


def request(client, path, body=None, **kwargs):
    options = {"base_url": "http://127.0.0.1:8766", **kwargs}
    if body is None:
        return client.get(path, **options)
    return client.post(path, json=body, headers={"Origin": "http://127.0.0.1:8766"}, **options)


def pair(server, client):
    server.pair_code, server.pair_expires = "temporary-code", time.time() + 120
    assert request(client, "/pair", {"code": "temporary-code", "name": "Telefone"}).status_code == 200
    assert request(client, "/api/state").status_code == 401
    assert request(client, "/pair/status", {}).json["pending"]
    request_id = next(iter(server.pending))
    server.approve(request_id)
    assert not request(client, "/pair/status", {}).json["pending"]


def test_pair_requires_pc_approval_and_revocation(server):
    client = client_for(server)
    pair(server, client)
    assert request(client, "/api/state").status_code == 200
    server.revoke(next(iter(server.sessions)))
    assert request(client, "/api/state").status_code == 401


def test_pair_single_use_expired_and_invalid_body(server):
    client = client_for(server)
    pair(server, client)
    assert request(client, "/pair", {"code": "temporary-code"}).status_code == 403
    assert request(client, "/pair", []).status_code == 400
    server.pair_code, server.pair_expires = "expired", 0
    assert request(client, "/pair", {"code": "expired"}).status_code == 403


def test_companion_blocks_host_csrf_arbitrary_paths(server):
    client = client_for(server)
    assert client.get("/api/state", base_url="http://attacker.example").status_code == 403
    assert client.post("/pair", base_url="http://127.0.0.1:8766", json={"code": "x"}).status_code == 403
    pair(server, client)
    assert request(client, "/assets/../config/secrets.json").status_code == 404
    assert request(client, "/api/save_secrets", {}).status_code == 404
    assert "frame-ancestors 'none'" in request(client, "/api/state").headers["Content-Security-Policy"]


def test_companion_static_files_and_only_approved_assets(server, tmp_path):
    server.ui_root.mkdir()
    (server.ui_root / "index.html").write_text("Companion")
    server.assets.mkdir()
    (server.assets / "map.png").write_bytes(b"test")
    client = client_for(server)
    assert request(client, "/").status_code == 200
    assert request(client, "/assets/map.png").status_code == 401
    pair(server, client)
    assert request(client, "/assets/map.png").status_code == 200
    server.stop()
    assert request(client, "/api/state").status_code == 401


def test_backup_without_secrets_has_database_snapshot(tmp_path, store):
    import zipfile
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config" / "secrets.json").write_text('{"api_key":"never-cleartext"}')
    (data / "config" / "settings.json").write_text('{"density":"compact"}')
    source = experience.ExperienceStore(data / "config" / "experience.sqlite3")
    source.put("a", "goals", {"points": 100})
    result = write_backup(data, tmp_path / "backup.zip")
    assert result["files"] == 2
    with zipfile.ZipFile(result["path"]) as archive:
        assert "config/secrets.json" not in archive.namelist()
        assert "config/experience.sqlite3" in archive.namelist()


def test_backup_secrets_require_password_and_encrypt(tmp_path):
    import pyzipper
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config" / "secrets.json").write_text('{"api_key":"secret-value"}')
    with pytest.raises(ValueError):
        write_backup(data, tmp_path / "bad.zip", True, "short")
    result = write_backup(data, tmp_path / "good.zip", True, "a-secure-test-password")
    assert b"secret-value" not in Path(result["path"]).read_bytes()
    with pyzipper.AESZipFile(result["path"]) as archive:
        archive.setpassword(b"wrong")
        with pytest.raises(RuntimeError):
            archive.read("config/secrets.json")
        archive.setpassword(b"a-secure-test-password")
        assert b"secret-value" in archive.read("config/secrets.json")


@pytest.mark.parametrize("value", [None, {}, {"format":"digitracker-library","version":1,"provider":"retroachievements","games":[{"id":"../../x"}]}])
def test_library_invalid(value):
    with pytest.raises(ValueError):
        validate_library(value)


def test_library_deduplication():
    value = {"format": "digitracker-library", "version": 1, "provider": "retroachievements", "games": [{"id": 1}, {"id": 1}, {"id": 2}]}
    assert validate_library(value) == [1, 2]
