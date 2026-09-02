import html as html_lib
import json

import pytest

import web_image_search


def test_parseia_grade_com_resolucao_e_origem():
    metadata = json.dumps({
        "murl": "https://cdn.example/game.jpg", "turl": "https://cdn.example/thumb.jpg",
        "purl": "https://example.com/page", "mw": 1920, "mh": 1080, "t": "Game",
    }).replace('"', '&quot;')
    html = f'<a class="iusc" m="{metadata}"></a>'
    result = web_image_search.parse_results(html)
    assert result == [{
        "url": "https://cdn.example/game.jpg", "thumb": "https://cdn.example/thumb.jpg",
        "width": 1920, "height": 1080, "source": "https://example.com/page", "title": "Game",
    }]


def test_descarta_resultado_sem_url_http():
    metadata = json.dumps({"murl": "javascript:alert(1)"}).replace('"', '&quot;')
    assert web_image_search.parse_results(f'<a class="iusc" m="{metadata}"></a>') == []


def test_url_publica_preserva_consulta_e_safesearch():
    url = web_image_search.public_search_url("Jogo PS2", "strict")
    assert "google.com/search" in url
    assert "Jogo+PS2" in url and "safe=active" in url and "tbm=isch" in url


def test_parseia_resultado_google_basico_com_origem():
    html = '''
    <a href="/imgres?imgurl=https%3A%2F%2Fcdn.example%2Fcover.jpg&amp;imgrefurl=https%3A%2F%2Fexample.com%2Fgame">
      <img src="https://thumb.example/cover.jpg" alt="Game cover">
    </a>'''
    result = web_image_search.parse_google_results(html)
    assert result == [{
        "url": "https://cdn.example/cover.jpg",
        "thumb": "https://thumb.example/cover.jpg",
        "width": 0, "height": 0,
        "source": "https://example.com/game",
        "title": "Game cover", "provider": "google",
    }]


def test_google_bloqueado_usa_fallback_yandex(monkeypatch):
    web_image_search._CACHE.clear()
    monkeypatch.setattr(web_image_search, "_request_google", lambda *args: {
        "ok": False, "blocked": True, "reason": "captcha", "results": [],
    })
    monkeypatch.setattr(web_image_search, "_request_yandex", lambda *args: {
        "ok": True, "blocked": False, "reason": "", "results": [{
            "url": "https://cdn.example/game.jpg", "thumb": "https://cdn.example/thumb.jpg",
            "width": 800, "height": 1200, "source": "https://example.com/game",
            "title": "Game", "provider": "yandex",
        }],
    })
    monkeypatch.setattr(web_image_search, "_request_bing", lambda *args: pytest.fail(
        "Bing não deve ser consultado quando o Yandex encontrou resultados relevantes."
    ))
    result = web_image_search.search("Game PS2 cover", provider="google")
    assert result["ok"] is True
    assert result["provider"] == "yandex"
    assert result["fallback_used"] is True
    assert result["blocked"] is True
    assert "google.com/search" in result["open_google_url"]


def test_parseia_yandex_com_original_miniatura_e_origem():
    payload = {
        "initialState": {"serpList": {"items": {
            "keys": ["cover"],
            "entities": {"cover": {
                "origUrl": "https://cdn.example/digimon-rumble-arena-cover.jpg",
                "origWidth": 800, "origHeight": 1200,
                "image": "//thumb.example/cover.jpg",
                "snippet": {
                    "title": "Digimon Rumble Arena PlayStation cover",
                    "url": "https://example.com/digimon-rumble-arena",
                },
            }},
        }}}}
    encoded = html_lib.escape(json.dumps(payload), quote=True)
    result = web_image_search.parse_yandex_results(
        f'<div class="serp-list" data-state="{encoded}"></div>'
    )
    assert result == [{
        "url": "https://cdn.example/digimon-rumble-arena-cover.jpg",
        "thumb": "https://thumb.example/cover.jpg",
        "width": 800, "height": 1200,
        "source": "https://example.com/digimon-rumble-arena",
        "title": "Digimon Rumble Arena PlayStation cover",
        "provider": "yandex",
    }]


def test_filtro_remove_digimon_generico_e_preserva_titulo_completo():
    results = [{
        "url": "https://example.com/digimon-anime.jpg", "thumb": "",
        "source": "https://example.com/digimon", "title": "Digimon anime",
    }, {
        "url": "https://example.com/digimon-rumble-arena-ps1-cover.jpg", "thumb": "",
        "source": "https://example.com/rumble-arena", "title": "Digimon Rumble Arena cover",
    }]
    ranked = web_image_search.rank_relevant(
        results, "Digimon Rumble Arena PlayStation box art cover"
    )
    assert [item["url"] for item in ranked] == [
        "https://example.com/digimon-rumble-arena-ps1-cover.jpg"
    ]


def test_yandex_sem_resultado_relevante_usa_bing(monkeypatch):
    web_image_search._CACHE.clear()
    monkeypatch.setattr(web_image_search, "_request_google", lambda *args: {
        "ok": False, "blocked": True, "reason": "captcha", "results": [],
    })
    monkeypatch.setattr(web_image_search, "_request_yandex", lambda *args: {
        "ok": False, "blocked": False, "reason": "sem resultados relevantes", "results": [],
    })
    monkeypatch.setattr(web_image_search, "_request_bing", lambda *args: {
        "ok": True, "blocked": False, "reason": "", "results": [{
            "url": "https://cdn.example/game.jpg", "thumb": "",
            "width": 800, "height": 1200, "source": "", "title": "Game",
            "provider": "bing",
        }],
    })
    result = web_image_search.search("Game cover")
    assert result["ok"] is True
    assert result["provider"] == "bing"


def test_resultados_repetidos_sao_removidos():
    html = '<a href="/imgres?imgurl=https%3A%2F%2Fcdn.example%2Fa.jpg"><img></a>' * 2
    assert len(web_image_search.parse_google_results(html)) == 1


@pytest.mark.parametrize("status,text", [
    (403, "forbidden"), (429, "too many"), (200, "unusual traffic captcha"),
    (200, '<form action="/httpservice/retry/enablejs">'),
])
def test_detecta_bloqueios_do_google(status, text):
    response = type("Response", (), {"status_code": status, "text": text})()
    assert web_image_search._blocked(response) is True


def test_paginacao_e_cache_curto(monkeypatch):
    web_image_search._CACHE.clear()
    calls = []

    def google(query, page, safe, timeout):
        calls.append((query, page, safe, timeout))
        return {"ok": True, "blocked": False, "reason": "", "results": [{
            "url": "https://cdn.example/page.jpg", "thumb": "", "width": 0,
            "height": 0, "source": "", "title": "", "provider": "google",
        }]}

    monkeypatch.setattr(web_image_search, "_request_google", google)
    first = web_image_search.search("Game", page=3, safe="off")
    second = web_image_search.search("Game", page=3, safe="off")
    assert calls == [("Game", 3, "off", 20)]
    assert second["cached"] is True
    assert first["page"] == 3
