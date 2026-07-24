"""Testes do cliente da RetroAchievements — retentativa, backoff e parsing.

Nenhuma chamada real de rede: a sessão HTTP é substituída por uma dublê que
devolve respostas roteirizadas.

Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ra_api  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.headers = headers or {}
        self._bad_json = bad_json
        self.content = b"x"

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Devolve, em ordem, as respostas roteirizadas; repete a última se acabar."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.chamadas.append((url, params))
        r = self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def sleeps(monkeypatch):
    """Captura os tempos de espera em vez de realmente dormir."""
    registrados = []
    monkeypatch.setattr(ra_api.time, "sleep", registrados.append)
    return registrados


def client(tmp_path, respostas):
    c = ra_api.RAClient("wanderson", "chave", tmp_path)
    c.session = FakeSession(respostas)
    return c


class TestRetryEBackoff:
    def test_sucesso_direto_nao_repete(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(200, [{"ID": 1}])])
        assert c._get("X.php", {}) == [{"ID": 1}]
        assert len(c.session.chamadas) == 1
        assert sleeps == []

    def test_repete_apos_429_e_devolve_o_sucesso(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(429), FakeResponse(200, {"ok": True})])
        assert c._get("X.php", {}) == {"ok": True}
        assert len(c.session.chamadas) == 2
        assert len(sleeps) == 1

    def test_repete_em_erro_5xx(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(503), FakeResponse(200, {"ok": True})])
        assert c._get("X.php", {}) == {"ok": True}

    def test_repete_em_falha_de_rede(self, tmp_path, sleeps):
        c = client(tmp_path, [requests.ConnectionError("sem rede"), FakeResponse(200, {})])
        assert c._get("X.php", {}) == {}

    def test_backoff_e_crescente(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(429)])
        with pytest.raises(ra_api.RARateLimited):
            c._get("X.php", {})
        assert sleeps == sorted(sleeps) and len(set(sleeps)) > 1

    def test_respeita_retry_after(self, tmp_path, sleeps):
        c = client(tmp_path, [
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(200, {}),
        ])
        c._get("X.php", {})
        assert sleeps == [7.0]

    def test_retry_after_invalido_cai_no_backoff(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(429, headers={"Retry-After": "amanhã"}),
                              FakeResponse(200, {})])
        c._get("X.php", {})
        assert sleeps and sleeps[0] > 0

    def test_pausa_longa_nao_bloqueia_a_thread(self, tmp_path, sleeps):
        """Com um Retry-After grande, desistimos na hora e repassamos o prazo —
        dormir 99999s congelaria a sincronização (e o app fechado no tray)."""
        c = client(tmp_path, [FakeResponse(429, headers={"Retry-After": "99999"})])
        with pytest.raises(ra_api.RARateLimited) as exc:
            c._get("X.php", {})
        assert exc.value.retry_after == 99999.0
        assert sleeps == []
        assert len(c.session.chamadas) == 1

    def test_backoff_calculado_respeita_o_teto(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(429)])
        with pytest.raises(ra_api.RARateLimited):
            c._get("X.php", {})
        assert all(s <= ra_api.BACKOFF_CAP for s in sleeps)

    def test_desiste_apos_o_limite_de_tentativas(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(429)])
        with pytest.raises(ra_api.RARateLimited):
            c._get("X.php", {})
        assert len(c.session.chamadas) == ra_api.MAX_RETRIES + 1

    def test_rate_limited_carrega_o_retry_after(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(429)])
        with pytest.raises(ra_api.RARateLimited) as exc:
            c._get("X.php", {})
        assert exc.value.retry_after > 0

    def test_rate_limited_e_um_raerror(self):
        assert issubclass(ra_api.RARateLimited, ra_api.RAError)

    def test_5xx_persistente_vira_raerror_simples(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(502)])
        with pytest.raises(ra_api.RAError) as exc:
            c._get("X.php", {})
        assert not isinstance(exc.value, ra_api.RARateLimited)


class TestErrosDefinitivos:
    def test_401_nao_repete(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(401)])
        with pytest.raises(ra_api.RAError, match="401"):
            c._get("X.php", {})
        assert len(c.session.chamadas) == 1

    def test_404_nao_repete(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(404)])
        with pytest.raises(ra_api.RAError):
            c._get("X.php", {})
        assert len(c.session.chamadas) == 1

    def test_json_invalido_nao_repete(self, tmp_path, sleeps):
        c = client(tmp_path, [FakeResponse(200, bad_json=True)])
        with pytest.raises(ra_api.RAError, match="JSON"):
            c._get("X.php", {})
        assert len(c.session.chamadas) == 1


class TestParseAchievements:
    def test_hardcore_conta_como_obtida(self):
        out = ra_api.RAClient.parse_achievements({
            "Achievements": {"1": {"ID": 1, "Title": "A", "DateEarnedHardcore": "2026-03-04 22:08:11"}}
        })
        assert out[1]["earned"] and out[1]["hardcore"]

    def test_softcore_conta_como_obtida(self):
        out = ra_api.RAClient.parse_achievements({
            "Achievements": {"1": {"ID": 1, "DateEarned": "2026-03-04 22:08:11"}}
        })
        assert out[1]["earned"] and not out[1]["hardcore"]

    def test_sem_data_nao_e_obtida(self):
        out = ra_api.RAClient.parse_achievements({"Achievements": {"1": {"ID": 1}}})
        assert not out[1]["earned"]

    def test_sem_display_order_vai_para_o_fim(self):
        out = ra_api.RAClient.parse_achievements({"Achievements": {"1": {"ID": 1}}})
        assert out[1]["display_order"] == 10_000_000

    def test_resposta_vazia_nao_quebra(self):
        assert ra_api.RAClient.parse_achievements({}) == {}


class TestFmtDate:
    @pytest.mark.parametrize("bruto,esperado", [
        ("2026-03-04 22:08:11", "04/03/2026 · 22:08"),
        ("2026-03-04", "04/03/2026 · 00:00"),
        ("", ""),
        ("formato estranho", "formato estranho"),
    ])
    def test_formatos(self, bruto, esperado):
        assert ra_api.fmt_date(bruto) == esperado
