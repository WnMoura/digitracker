"""Testes do cliente do GameFAQs.

O parsing é puro (HTML -> dados) e a rede é substituída por uma sessão falsa,
então nada aqui toca o site de verdade.

Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gamefaqs  # noqa: E402

BASE = "https://gamefaqs.gamespot.com/ps2/580782-digimon-world-4/faqs"

LISTAGEM = """
<html><body>
  <a href="/ps2/580782-digimon-world-4/faqs/38057">Boss Guide</a>
  <a href="/ps2/580782-digimon-world-4/faqs/37283">Guide and Walkthrough</a>
  <a href="/ps2/580782-digimon-world-4/faqs/37283">Guide</a>
  <a href="/ps2/580782-digimon-world-4/faqs/35295?print=1">Unlocking FAQ</a>
  <a href="/ps2/580782-digimon-world-4">Voltar ao jogo</a>
  <a href="/ps2/580782-digimon-world-4/faqs/38057">FAQ</a>
</body></html>
"""

CONTEUDO = """
<html><head><title>Digimon World 4 – Boss Guide</title></head><body>
  <h1 class="page-title">Digimon World 4 – Boss Guide</h1>
  <div class="faqtext">1)Intro

Bem-vindo ao guia de chefes.</div>
</body></html>
"""

PAGINADO = """
<html><body>
  <div class="faqtext">pagina 1</div>
  <a href="/ps2/580782-digimon-world-4/faqs/37854?page=1">2</a>
  <a href="/ps2/580782-digimon-world-4/faqs/37854?page=2">3</a>
</body></html>
"""


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    """Devolve respostas por URL; registra o que foi pedido."""

    def __init__(self, mapa, default=None):
        self.mapa = mapa
        self.default = default if default is not None else FakeResponse("", 404)
        self.pedidos = []

    def get(self, url, timeout=None):
        self.pedidos.append(url)
        for chave, resp in self.mapa.items():
            if chave in url:
                return resp
        return self.default


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch):
    """Os delays existem para não martelar o site — nos testes, não."""
    monkeypatch.setattr(gamefaqs.time, "sleep", lambda *_: None)


# ---------------------------------------------------------------------------- #
# Sessão / Cloudflare
# ---------------------------------------------------------------------------- #
class TestSession:
    def test_prefere_fingerprint_de_chrome_moderno(self, monkeypatch):
        from curl_cffi import requests as curl_requests

        marker, seen = object(), {}

        def fake_session(**kwargs):
            seen.update(kwargs)
            return marker

        monkeypatch.setattr(curl_requests, "Session", fake_session)
        assert gamefaqs.create_session() is marker
        assert seen == {"impersonate": "chrome"}

    def test_cloudscraper_continua_como_fallback(self, monkeypatch):
        import cloudscraper
        from curl_cffi import requests as curl_requests

        marker = object()
        monkeypatch.setattr(curl_requests, "Session",
                            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("sem libcurl")))
        monkeypatch.setattr(cloudscraper, "create_scraper", lambda **_kwargs: marker)
        assert gamefaqs.create_session() is marker


# ---------------------------------------------------------------------------- #
# URLs
# ---------------------------------------------------------------------------- #
class TestUrls:
    def test_aceita_url_completa(self):
        assert gamefaqs.normalize_url(BASE) == BASE

    def test_completa_o_esquema(self):
        url = gamefaqs.normalize_url("gamefaqs.gamespot.com/ps2/580782-x/faqs")
        assert url.startswith("https://")

    def test_recusa_outro_site(self):
        with pytest.raises(gamefaqs.GameFAQsError, match="não é do GameFAQs"):
            gamefaqs.normalize_url("https://example.com/faqs")

    def test_recusa_url_vazia(self):
        with pytest.raises(gamefaqs.GameFAQsError):
            gamefaqs.normalize_url("   ")

    def test_nao_se_engana_com_dominio_parecido(self):
        with pytest.raises(gamefaqs.GameFAQsError):
            gamefaqs.normalize_url("https://gamefaqs.gamespot.com.evil.net/faqs")

    def test_distingue_guia_de_listagem(self):
        assert gamefaqs.is_faq_url(f"{BASE}/38057")
        assert not gamefaqs.is_faq_url(BASE)


# ---------------------------------------------------------------------------- #
# Parsing
# ---------------------------------------------------------------------------- #
class TestParsingListagem:
    def test_extrai_os_guias(self):
        faqs = gamefaqs.parse_faq_listing(LISTAGEM, BASE)
        assert {f["id"] for f in faqs} == {"38057", "37283", "35295"}

    def test_deduplica_o_mesmo_guia(self):
        faqs = gamefaqs.parse_faq_listing(LISTAGEM, BASE)
        assert len(faqs) == 3

    def test_titulo_generico_pode_ser_o_nome_real_do_guia(self):
        html = '<a href="/ps2/580782-x/faqs/50899">Walkthrough</a>'
        assert gamefaqs.parse_faq_listing(html, BASE) == [{
            "id": "50899", "title": "Walkthrough",
            "url": "https://gamefaqs.gamespot.com/ps2/580782-x/faqs/50899",
        }]

    def test_prefere_o_titulo_mais_descritivo(self):
        faqs = {f["id"]: f["title"] for f in gamefaqs.parse_faq_listing(LISTAGEM, BASE)}
        assert faqs["37283"] == "Guide and Walkthrough"   # não o genérico "Guide"

    def test_monta_url_absoluta_sem_query(self):
        faqs = {f["id"]: f["url"] for f in gamefaqs.parse_faq_listing(LISTAGEM, BASE)}
        assert faqs["35295"].startswith("https://gamefaqs.gamespot.com/")
        assert "?" not in faqs["35295"]

    def test_ignora_link_que_nao_e_guia(self):
        urls = [f["url"] for f in gamefaqs.parse_faq_listing(LISTAGEM, BASE)]
        assert all("/faqs/" in u for u in urls)

    def test_pagina_sem_guias(self):
        assert gamefaqs.parse_faq_listing("<html><body>nada</body></html>", BASE) == []


class TestParsingConteudo:
    def test_pega_o_texto_da_div_do_faq(self):
        texto = gamefaqs.parse_faq_content(CONTEUDO)
        assert "Bem-vindo ao guia de chefes." in texto

    def test_cai_para_pre_quando_nao_ha_div(self):
        assert "conteudo" in gamefaqs.parse_faq_content(
            "<html><body><pre>conteudo</pre></body></html>"
        )

    def test_cai_para_o_body_como_ultimo_recurso(self):
        assert "solto" in gamefaqs.parse_faq_content("<html><body>texto solto</body></html>")

    def test_html_vazio_nao_quebra(self):
        assert gamefaqs.parse_faq_content("") == ""

    def test_extrai_o_titulo(self):
        assert gamefaqs.parse_faq_title(CONTEUDO) == "Digimon World 4 – Boss Guide"

    def test_titulo_ausente_devolve_vazio(self):
        assert gamefaqs.parse_faq_title("<html><body>x</body></html>") == ""


class TestPaginacao:
    def test_conta_as_paginas(self):
        assert gamefaqs.parse_page_count(PAGINADO) == 3

    def test_guia_de_pagina_unica(self):
        assert gamefaqs.parse_page_count(CONTEUDO) == 1

    def test_respeita_o_teto_de_paginas(self):
        html = "".join(f'<a href="?page={i}">x</a>' for i in range(50))
        assert gamefaqs.parse_page_count(html) == gamefaqs.MAX_PAGES


# ---------------------------------------------------------------------------- #
# Rede (com sessão falsa)
# ---------------------------------------------------------------------------- #
class TestListFaqs:
    def test_lista_os_guias_do_jogo(self):
        s = FakeSession({BASE: FakeResponse(LISTAGEM)})
        assert len(gamefaqs.list_faqs(s, BASE)) == 3

    def test_url_de_guia_devolve_so_ele(self):
        s = FakeSession({"38057": FakeResponse(CONTEUDO)})
        faqs = gamefaqs.list_faqs(s, f"{BASE}/38057")
        assert len(faqs) == 1 and faqs[0]["id"] == "38057"

    def test_pagina_sem_guias_avisa(self):
        s = FakeSession({BASE: FakeResponse("<html><body>vazio</body></html>")})
        with pytest.raises(gamefaqs.GameFAQsError, match="Nenhum guia"):
            gamefaqs.list_faqs(s, BASE)


class TestFetchFaq:
    def test_baixa_o_guia(self):
        texto = "1)Intro\n\n" + "conteudo do guia. " * 50
        s = FakeSession({"38057": FakeResponse(f'<div class="faqtext">{texto}</div>')})
        faq = gamefaqs.fetch_faq(s, f"{BASE}/38057")
        assert "conteudo do guia" in faq["text"]

    def test_junta_as_paginas(self):
        corpo = "texto suficiente para passar do minimo. " * 20
        s = FakeSession({
            "page=1": FakeResponse(f'<div class="faqtext">PAGINA2 {corpo}</div>'),
            "page=2": FakeResponse(f'<div class="faqtext">PAGINA3 {corpo}</div>'),
            "37854": FakeResponse(PAGINADO.replace("pagina 1", f"PAGINA1 {corpo}")),
        })
        faq = gamefaqs.fetch_faq(s, f"{BASE}/37854")
        assert faq["pages"] == 3
        for marca in ("PAGINA1", "PAGINA2", "PAGINA3"):
            assert marca in faq["text"]

    def test_avisa_o_progresso_das_paginas(self):
        corpo = "texto suficiente para passar do minimo. " * 20
        s = FakeSession({
            "page=": FakeResponse(f'<div class="faqtext">{corpo}</div>'),
            "37854": FakeResponse(PAGINADO.replace("pagina 1", corpo)),
        })
        vistos = []
        gamefaqs.fetch_faq(s, f"{BASE}/37854", on_progress=lambda p, t: vistos.append((p, t)))
        assert vistos == [(2, 3), (3, 3)]

    def test_pagina_curta_e_tratada_como_bloqueio(self):
        s = FakeSession({"38057": FakeResponse('<div class="faqtext">oi</div>')})
        with pytest.raises(gamefaqs.GameFAQsError, match="sem o texto do guia"):
            gamefaqs.fetch_faq(s, f"{BASE}/38057")


class TestErrosDeRede:
    def test_404_nao_repete(self):
        s = FakeSession({}, default=FakeResponse("", 404))
        with pytest.raises(gamefaqs.GameFAQsError, match="404"):
            gamefaqs.list_faqs(s, BASE)
        assert len(s.pedidos) == 1

    def test_403_do_cloudflare_repete_e_avisa(self):
        s = FakeSession({}, default=FakeResponse("", 403))
        with pytest.raises(gamefaqs.GameFAQsError, match="Cloudflare"):
            gamefaqs.list_faqs(s, BASE)
        assert len(s.pedidos) == gamefaqs.RETRIES

    def test_sucesso_depois_de_uma_falha(self):
        class Instavel(FakeSession):
            def get(self, url, timeout=None):
                self.pedidos.append(url)
                if len(self.pedidos) == 1:
                    raise OSError("conexão caiu")
                return FakeResponse(LISTAGEM)

        s = Instavel({})
        assert len(gamefaqs.list_faqs(s, BASE)) == 3
        assert len(s.pedidos) == 2

    def test_erro_de_rede_persistente(self):
        class Morta(FakeSession):
            def get(self, url, timeout=None):
                self.pedidos.append(url)
                raise OSError("sem rede")

        with pytest.raises(gamefaqs.GameFAQsError, match="sem rede"):
            gamefaqs.list_faqs(Morta({}), BASE)
