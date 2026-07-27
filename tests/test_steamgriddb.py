"""Testes do cliente do SteamGridDB (busca de capas, estilo Playnite).

As funções de parsing são puras: recebem o JSON já decodificado e devolvem
listas limpas. A rede é exercitada com uma sessão falsa, sem tocar na internet.

Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import steamgriddb as sgdb  # noqa: E402


# ---------------------------------------------------------------------------- #
# Sessão falsa (sem rede)
# ---------------------------------------------------------------------------- #
class FakeResp:
    def __init__(self, status=200, payload=None, raise_json=False):
        self.status_code = status
        self._payload = payload
        self._raise = raise_json

    def json(self):
        if self._raise:
            raise ValueError("json ilegível")
        return self._payload


class FakeSession:
    """Devolve respostas pré-programadas e registra as URLs pedidas."""
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if isinstance(self._responses[0], Exception):
            raise self._responses.pop(0)
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


# ---------------------------------------------------------------------------- #
# Envelope success/errors
# ---------------------------------------------------------------------------- #
class TestEnvelope:
    def test_erro_da_api_vira_excecao(self):
        with pytest.raises(sgdb.SteamGridDBError, match="chave|Chave|recus|invalid|Games"):
            sgdb.parse_search({"success": False, "errors": ["Games Not Found"]})

    def test_sucesso_sem_data_vira_lista_vazia(self):
        assert sgdb.parse_search({"success": True}) == []

    def test_payload_nao_dict_quebra_claro(self):
        with pytest.raises(sgdb.SteamGridDBError):
            sgdb.parse_grids("não é json")


# ---------------------------------------------------------------------------- #
# Busca por nome
# ---------------------------------------------------------------------------- #
class TestBusca:
    def test_extrai_id_e_nome(self):
        payload = {"success": True, "data": [
            {"id": 1234, "name": "Digimon World 4", "verified": True},
            {"id": 5678, "name": "Digimon World"},
        ]}
        assert sgdb.parse_search(payload) == [
            {"id": 1234, "name": "Digimon World 4"},
            {"id": 5678, "name": "Digimon World"},
        ]

    def test_ignora_entradas_sem_id_ou_nome(self):
        payload = {"success": True, "data": [
            {"name": "sem id"}, {"id": 9}, {"id": 7, "name": "ok"}, "lixo",
        ]}
        assert sgdb.parse_search(payload) == [{"id": 7, "name": "ok"}]


# ---------------------------------------------------------------------------- #
# Capas (grids)
# ---------------------------------------------------------------------------- #
class TestCapas:
    def test_extrai_capas_com_thumb(self):
        payload = {"success": True, "data": [{
            "id": 1, "url": "https://cdn/full.png", "thumb": "https://cdn/thumb.png",
            "width": 600, "height": 900, "style": "alternate",
        }]}
        assert sgdb.parse_grids(payload) == [{
            "id": 1, "url": "https://cdn/full.png", "thumb": "https://cdn/thumb.png",
            "width": 600, "height": 900, "style": "alternate",
        }]

    def test_thumb_ausente_cai_para_a_url(self):
        payload = {"success": True, "data": [{"id": 2, "url": "https://cdn/x.png"}]}
        out = sgdb.parse_grids(payload)
        assert out[0]["thumb"] == "https://cdn/x.png"

    def test_capa_sem_url_e_descartada(self):
        payload = {"success": True, "data": [{"id": 3, "thumb": "só thumb"}]}
        assert sgdb.parse_grids(payload) == []


# ---------------------------------------------------------------------------- #
# Rede (com sessão falsa)
# ---------------------------------------------------------------------------- #
class TestRede:
    def test_search_games_monta_a_url(self):
        sess = FakeSession(FakeResp(200, {"success": True, "data": [
            {"id": 1, "name": "Jogo"}]}))
        out = sgdb.search_games(sess, "digimon")
        assert out == [{"id": 1, "name": "Jogo"}]
        assert sess.calls[0][0].endswith("/search/autocomplete/digimon")

    def test_busca_vazia_nao_chama_rede(self):
        sess = FakeSession(FakeResp(200, {"success": True, "data": []}))
        assert sgdb.search_games(sess, "   ") == []
        assert sess.calls == []

    def test_game_covers_passa_dimensoes_de_capa(self):
        sess = FakeSession(FakeResp(200, {"success": True, "data": [
            {"id": 9, "url": "https://cdn/c.png"}]}))
        out = sgdb.game_covers(sess, 42)
        assert out[0]["url"] == "https://cdn/c.png"
        url, params = sess.calls[0]
        assert url.endswith("/grids/game/42")
        assert params["dimensions"] == sgdb.COVER_DIMENSIONS
        assert params["nsfw"] == "false"

    def test_game_heroes_bate_no_endpoint_certo(self):
        sess = FakeSession(FakeResp(200, {"success": True, "data": [
            {"id": 5, "url": "https://cdn/hero.png"}]}))
        out = sgdb.game_heroes(sess, 42)
        assert out[0]["url"] == "https://cdn/hero.png"
        url, params = sess.calls[0]
        assert url.endswith("/heroes/game/42")
        assert params["nsfw"] == "false"

    def test_chave_invalida_da_mensagem_clara(self):
        sess = FakeSession(FakeResp(401, None))
        with pytest.raises(sgdb.SteamGridDBError, match="Chave|inválida"):
            sgdb.search_games(sess, "x")

    def test_json_ilegivel_vira_erro(self):
        sess = FakeSession(FakeResp(200, None, raise_json=True))
        with pytest.raises(sgdb.SteamGridDBError, match="ilegível"):
            sgdb.search_games(sess, "x")


# ---------------------------------------------------------------------------- #
# Sessão
# ---------------------------------------------------------------------------- #
class TestSessao:
    def test_sem_chave_nao_cria_sessao(self):
        with pytest.raises(sgdb.SteamGridDBError, match="Configure"):
            sgdb.create_session("   ")
