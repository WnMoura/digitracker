"""Testes do cliente do RAWG (busca de imagens de jogos, inclui retrô).

Parsing puro + rede com sessão falsa, sem tocar na internet.
Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rawg  # noqa: E402


class FakeResp:
    def __init__(self, status=200, payload=None, raise_json=False):
        self.status_code = status
        self._payload = payload
        self._raise = raise_json

    def json(self):
        if self._raise:
            raise ValueError("ilegível")
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self._responses[0] if len(self._responses) == 1 else self._responses.pop(0)


class TestBusca:
    def test_extrai_id_nome_e_fundo(self):
        payload = {"results": [
            {"id": 1, "name": "Digimon World 4", "background_image": "https://x/bg.jpg"},
            {"id": 2, "name": "Digimon World"},
        ]}
        assert rawg.parse_search(payload) == [
            {"id": 1, "name": "Digimon World 4", "background": "https://x/bg.jpg"},
            {"id": 2, "name": "Digimon World", "background": ""},
        ]

    def test_ignora_sem_id_ou_nome(self):
        payload = {"results": [{"name": "x"}, {"id": 5}, {"id": 7, "name": "ok"}]}
        assert rawg.parse_search(payload) == [{"id": 7, "name": "ok", "background": ""}]


class TestScreenshots:
    def test_extrai_screenshots(self):
        payload = {"results": [
            {"id": 10, "image": "https://x/1.jpg", "width": 1920, "height": 1080},
            {"id": 11, "image": "https://x/2.jpg"},
            {"id": 12},  # sem image -> descartado
        ]}
        out = rawg.parse_screenshots(payload)
        assert [s["url"] for s in out] == ["https://x/1.jpg", "https://x/2.jpg"]
        assert out[0]["thumb"] == "https://x/1.jpg" and out[0]["style"] == "screenshot"

    def test_fundo_vira_hero(self):
        assert rawg.hero_from_background("") == []
        h = rawg.hero_from_background("https://x/bg.jpg")
        assert h[0]["url"] == "https://x/bg.jpg" and h[0]["style"] == "background"


class TestEnvelope:
    def test_erro_da_api(self):
        with pytest.raises(rawg.RawgError):
            rawg._results({"error": "Invalid API key"})

    def test_sem_results(self):
        assert rawg.parse_search({}) == []

    def test_payload_nao_dict(self):
        with pytest.raises(rawg.RawgError):
            rawg.parse_screenshots("nope")


class TestRede:
    def test_search_monta_url_e_key(self):
        sess = FakeSession(FakeResp(200, {"results": [{"id": 1, "name": "Jogo"}]}))
        out = rawg.search_games(sess, "MINHACHAVE", "digimon")
        assert out[0]["name"] == "Jogo"
        url, params = sess.calls[0]
        assert url.endswith("/games")
        assert params["key"] == "MINHACHAVE" and params["search"] == "digimon"

    def test_busca_vazia_nao_chama_rede(self):
        sess = FakeSession(FakeResp(200, {"results": []}))
        assert rawg.search_games(sess, "k", "  ") == []
        assert sess.calls == []

    def test_screenshots_url(self):
        sess = FakeSession(FakeResp(200, {"results": [{"id": 9, "image": "https://x/s.jpg"}]}))
        out = rawg.game_screenshots(sess, "k", 42)
        assert out[0]["url"] == "https://x/s.jpg"
        assert sess.calls[0][0].endswith("/games/42/screenshots")

    def test_chave_invalida(self):
        sess = FakeSession(FakeResp(401, None))
        with pytest.raises(rawg.RawgError, match="Chave|inválida"):
            rawg.search_games(sess, "k", "x")


class TestSessao:
    def test_sem_chave(self):
        with pytest.raises(rawg.RawgError, match="Configure"):
            rawg.create_session("  ")
