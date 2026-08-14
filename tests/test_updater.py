from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
import requests

import updater


class FakeResponse:
    def __init__(self, *, payload=None, text="", content=b"", status=200):
        self.payload = payload
        self.text = text
        self.content = content
        self.status_code = status
        self.headers = {"Content-Length": str(len(content))} if content else {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **_kwargs):
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def payload(version="v0.6.1", *, with_checksum=True):
    assets = [{"name": updater.EXE_ASSET, "browser_download_url": "https://download/exe", "size": 4}]
    if with_checksum:
        assets.append({"name": updater.SHA_ASSET, "browser_download_url": "https://download/sha", "size": 80})
    return {
        "tag_name": version,
        "draft": False,
        "prerelease": False,
        "body": "Notas",
        "html_url": "https://github.com/WnMoura/digitracker/releases/tag/" + version,
        "published_at": "2026-08-14T00:00:00Z",
        "assets": assets,
    }


@pytest.mark.parametrize("text,expected", [
    ("v0.6.0", (0, 6, 0)),
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3-beta", None),
    ("main", None),
])
def test_parse_version(text, expected):
    assert updater.parse_version(text) == expected


def test_release_mais_nova_e_instalavel():
    info = updater.release_info(payload(), "0.6.0")
    assert info["update_available"] is True
    assert info["installable"] is True
    assert info["latest_version"] == "0.6.1"


def test_release_igual_nao_oferece_update():
    assert updater.release_info(payload("v0.6.0"), "0.6.0")["update_available"] is False


def test_release_sem_checksum_so_permite_download_manual():
    info = updater.release_info(payload(with_checksum=False), "0.6.0")
    assert info["update_available"] is True
    assert info["installable"] is False


def test_prerelease_e_recusada():
    data = payload()
    data["prerelease"] = True
    with pytest.raises(updater.UpdateError):
        updater.release_info(data, "0.6.0")


def manager(tmp_path, binary=b"novo"):
    target = tmp_path / "DigiTracker.exe"
    target.write_bytes(b"antigo")
    digest = hashlib.sha256(binary).hexdigest()
    responses = {
        updater.LATEST_RELEASE_URL: FakeResponse(payload=payload()),
        "https://download/sha": FakeResponse(text=f"{digest}  DigiTracker.exe"),
        "https://download/exe": FakeResponse(content=binary),
    }
    return updater.UpdateManager(
        "0.6.0", target, frozen=True, session=FakeSession(responses),
        staging_root=tmp_path / "stage",
    )


def wait_download(value, timeout=2):
    limit = time.time() + timeout
    status = value.status()
    while status["phase"] == "downloading" and time.time() < limit:
        time.sleep(0.01)
        status = value.status()
    return status


def test_download_valida_hash_e_fica_pronto(tmp_path):
    value = manager(tmp_path)
    assert value.check()["update_available"]
    value.start_download()
    status = wait_download(value)
    assert status["phase"] == "ready"
    assert status["bytes_downloaded"] == 4


def test_hash_incorreto_apaga_parcial_e_mantem_exe(tmp_path):
    value = manager(tmp_path, binary=b"novo")
    value.session.responses["https://download/sha"] = FakeResponse(text="0" * 64)
    value.check()
    value.start_download()
    status = wait_download(value)
    assert status["phase"] == "error"
    assert "SHA-256" in status["error"]
    assert (tmp_path / "DigiTracker.exe").read_bytes() == b"antigo"


def test_falha_do_github_vira_estado_de_erro(tmp_path):
    value = manager(tmp_path)
    value.session.responses[updater.LATEST_RELEASE_URL] = requests.ConnectionError("sem rede")
    status = value.check()
    assert status["ok"] is False and "sem rede" in status["error"]


def test_repositorio_privado_explica_o_404(tmp_path):
    value = manager(tmp_path)
    value.session.responses[updater.LATEST_RELEASE_URL] = FakeResponse(status=404)
    status = value.check()
    assert status["ok"] is False
    assert "publicamente" in status["error"]


def test_helper_tem_backup_rollback_e_reinicio(tmp_path):
    script = updater.build_helper_script(tmp_path / "DigiTracker.exe", tmp_path / "novo.exe", 123)
    assert "move /Y \"%TARGET%\" \"%BACKUP%\"" in script
    assert "move /Y \"%BACKUP%\" \"%TARGET%\"" in script
    assert "start \"\" \"%TARGET%\"" in script
    assert 'findstr /C:"%APP_PID%"' in script


def test_modo_fonte_nunca_instala(tmp_path):
    value = updater.UpdateManager("0.6.0", tmp_path / "engine.py", frozen=False,
                                  session=FakeSession({updater.LATEST_RELEASE_URL: FakeResponse(payload=payload())}))
    status = value.check()
    assert status["update_available"] is True
    assert status["installable"] is False
    assert value.prepare_install(1)["ok"] is False
