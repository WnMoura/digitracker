"""Testes do cliente do IGDB (busca de capas/screenshots via Twitch OAuth).

Parsing puro + montagem de URL + rede com sessão falsa, sem tocar na internet.
Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import igdb  # noqa: E402


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

    def post(self, url, params=None, headers=None, data=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "data": data})
        return self._responses[0] if len(self._responses) == 1 else self._responses.pop(0)


class TestUrls:
    def test_cover_e_screenshot(self):
        assert igdb.cover_url("abc").endswith("/t_cover_big/abc.jpg")
        assert igdb.cover_url("abc", "t_cover_small").endswith("/t_cover_small/abc.jpg")
        assert igdb.screenshot_url("xy").endswith("/t_screenshot_huge/xy.jpg")


class TestToken:
    def test_extrai_token(self):
        assert igdb.parse_token({"access_token": "TK", "expires_in": 5000}) == {
            "token": "TK", "expires_in": 5000}

    def test_sem_token_avisa(self):
        with pytest.raises(igdb.IgdbError):
            igdb.parse_token({"message": "invalid client"})


class TestParseGames:
    def test_extrai_capa_e_shots(self):
        payload = [
            {"id": 1, "name": "Digimon World 4",
             "cover": {"image_id": "cov1"},
             "screenshots": [{"image_id": "s1"}, {"image_id": "s2"}]},
            {"id": 2, "name": "Sem capa"},
        ]
        out = igdb.parse_games(payload)
        assert out[0] == {"id": 1, "name": "Digimon World 4", "cover": "cov1",
                          "shots": ["s1", "s2"]}
        assert out[1]["cover"] == "" and out[1]["shots"] == []

    def test_ignora_sem_id_ou_nome(self):
        assert igdb.parse_games([{"name": "x"}, {"id": 5}]) == []

    def test_payload_nao_lista_quebra_claro(self):
        with pytest.raises(igdb.IgdbError):
            igdb.parse_games({"nope": 1})

    def test_covers_e_heroes_de(self):
        g = {"cover": "cov1", "shots": ["s1", "s2"]}
        assert igdb.covers_of(g)[0]["url"].endswith("/t_cover_big/cov1.jpg")
        assert igdb.covers_of({"cover": ""}) == []
        heroes = igdb.heroes_of(g)
        assert [h["id"] for h in heroes] == ["s1", "s2"]
        assert heroes[0]["thumb"].endswith("/t_thumb/s1.jpg")


class TestRede:
    def test_get_token(self):
        sess = FakeSession(FakeResp(200, {"access_token": "TK", "expires_in": 100}))
        out = igdb.get_token(sess, "cid", "secret")
        assert out["token"] == "TK"
        assert sess.calls[0]["params"]["grant_type"] == "client_credentials"

    def test_token_credenciais_invalidas(self):
        sess = FakeSession(FakeResp(403, None))
        with pytest.raises(igdb.IgdbError, match="inválid"):
            igdb.get_token(sess, "cid", "secret")

    def test_search_monta_query_e_headers(self):
        sess = FakeSession(FakeResp(200, [{"id": 9, "name": "Jogo"}]))
        out = igdb.search_games(sess, "cid", "TK", "digimon")
        assert out[0]["name"] == "Jogo"
        call = sess.calls[0]
        assert call["url"].endswith("/games")
        assert call["headers"]["Client-ID"] == "cid"
        assert call["headers"]["Authorization"] == "Bearer TK"
        assert 'search "digimon";' in call["data"]

    def test_busca_vazia_nao_chama_rede(self):
        sess = FakeSession(FakeResp(200, []))
        assert igdb.search_games(sess, "cid", "TK", '  ""  ') == []
        assert sess.calls == []

    def test_token_expirado(self):
        sess = FakeSession(FakeResp(401, None))
        with pytest.raises(igdb.IgdbError, match="expirado|inválido"):
            igdb.search_games(sess, "cid", "TK", "x")

    def test_game_by_id(self):
        sess = FakeSession(FakeResp(200, [{"id": 42, "name": "X", "cover": {"image_id": "c"}}]))
        g = igdb.game_by_id(sess, "cid", "TK", 42)
        assert g["id"] == 42 and g["cover"] == "c"
        assert "where id = 42;" in sess.calls[0]["data"]
