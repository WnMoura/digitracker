"""Busca manual de imagens na web, com Google direto e fallback explícito.

O Google Imagens não oferece mais uma API pública adequada a novos clientes.
Esta integração consulta a página pública, detecta CAPTCHA/mudanças de HTML e
recorre ao Yandex e, por último, ao Bing. Resultados sem relação suficiente com
o nome completo são descartados. Nenhuma imagem é aplicada sem aprovação.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import threading
import time
import unicodedata
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
GOOGLE_SEARCH_URL = "https://www.google.com/search"
BING_SEARCH_URL = "https://www.bing.com/images/async"
BING_PUBLIC_URL = "https://www.bing.com/images/search"
YANDEX_SEARCH_URL = "https://yandex.com/images/search"
CACHE_TTL = 15 * 60
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


class WebImageSearchError(RuntimeError):
    pass


def google_public_search_url(query: str, safe: str = "moderate") -> str:
    params = {"tbm": "isch", "q": query, "hl": "pt-BR",
              "safe": "off" if safe == "off" else "active"}
    return f"{GOOGLE_SEARCH_URL}?{urlencode(params)}"


def bing_public_search_url(query: str, safe: str = "moderate") -> str:
    safe_map = {"strict": "strict", "moderate": "moderate", "off": "off"}
    return f"{BING_PUBLIC_URL}?{urlencode({'q': query, 'adlt': safe_map.get(safe, 'moderate')})}"


def public_search_url(query: str, safe: str = "moderate") -> str:
    """Compatibilidade: a ação pública principal agora abre o Google."""
    return google_public_search_url(query, safe)


def _http_url(value: str) -> str:
    value = html_lib.unescape(str(value or "")).replace(r"\u003d", "=") \
        .replace(r"\u0026", "&").replace(r"\/", "/")
    if value.startswith("//"):
        value = "https:" + value
    return value if value.startswith(("https://", "http://")) else ""


def _dedupe(results: list[dict]) -> list[dict]:
    seen = set()
    output = []
    for item in results:
        url = _http_url(item.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        item = dict(item)
        item["url"] = url
        item["thumb"] = _http_url(item.get("thumb", "")) or url
        output.append(item)
    return output


def parse_google_results(document: str) -> list[dict]:
    """Extrai resultados dos formatos básico e atual do Google Imagens."""
    document = document or ""
    soup = BeautifulSoup(document, "html.parser")
    results = []
    for anchor in soup.select('a[href*="imgurl="]'):
        href = anchor.get("href") or ""
        query = parse_qs(urlparse(href).query)
        url = _http_url((query.get("imgurl") or [""])[0])
        if not url:
            continue
        source = _http_url((query.get("imgrefurl") or [""])[0])
        image = anchor.find("img")
        thumb = _http_url((image or {}).get("data-src") or (image or {}).get("src") or "")
        results.append({
            "url": url, "thumb": thumb or url, "width": 0, "height": 0,
            "source": source, "title": (image or {}).get("alt") or anchor.get_text(" ", strip=True),
            "provider": "google",
        })

    # A página moderna serializa os originais como [url, altura, largura].
    pattern = re.compile(
        r'\["((?:https?:)?\\?/\\?/[^"\\]+(?:\\.[^"\\]*)?)",(\d{2,5}),(\d{2,5})\]'
    )
    for match in pattern.finditer(document):
        raw, height, width = match.groups()
        url = _http_url(raw)
        if not url:
            continue
        host = (urlparse(url).hostname or "").lower()
        if host.endswith(("gstatic.com", "googleusercontent.com", "google.com")):
            continue
        results.append({
            "url": url, "thumb": url, "width": int(width), "height": int(height),
            "source": "", "title": "", "provider": "google",
        })
    return _dedupe(results)


def parse_results(document: str) -> list[dict]:
    """Parser do fallback Bing mantido como contrato público legado."""
    results = []
    soup = BeautifulSoup(document or "", "html.parser")
    for node in soup.select("a.iusc[m]"):
        try:
            data = json.loads(node.get("m") or "{}")
        except (TypeError, ValueError):
            continue
        url = data.get("murl") or ""
        thumb = data.get("turl") or url
        if not url.startswith(("https://", "http://")):
            continue
        try:
            width, height = int(data.get("mw") or 0), int(data.get("mh") or 0)
        except (TypeError, ValueError):
            width, height = 0, 0
        results.append({
            "url": url, "thumb": thumb, "width": width, "height": height,
            "source": data.get("purl") or "", "title": data.get("t") or "",
        })
    return _dedupe(results)


def parse_yandex_results(document: str) -> list[dict]:
    """Lê o estado JSON inicial do Yandex preservando a ordem da grade."""
    soup = BeautifulSoup(document or "", "html.parser")
    state = None
    for node in soup.select("[data-state]"):
        raw = node.get("data-state") or ""
        if '"initialState"' not in raw:
            continue
        try:
            candidate = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(candidate.get("initialState"), dict):
            state = candidate["initialState"]
            break
    try:
        items = state["serpList"]["items"]
        keys = items.get("keys") or []
        entities = items.get("entities") or {}
    except (KeyError, TypeError):
        return []
    results = []
    for key in keys:
        item = entities.get(key) or {}
        snippet = item.get("snippet") or {}
        preview = ((item.get("viewerData") or {}).get("preview") or [{}])[0]
        url = _http_url(item.get("origUrl") or preview.get("url") or "")
        if not url:
            continue
        try:
            width = int(item.get("origWidth") or preview.get("w") or item.get("width") or 0)
            height = int(item.get("origHeight") or preview.get("h") or item.get("height") or 0)
        except (TypeError, ValueError):
            width, height = 0, 0
        results.append({
            "url": url,
            "thumb": _http_url(((item.get("viewerData") or {}).get("thumb") or {}).get("url")
                               or item.get("image") or "") or url,
            "width": width, "height": height,
            "source": _http_url(snippet.get("url") or ""),
            "title": snippet.get("title") or item.get("alt") or "",
            "provider": "yandex",
        })
    return _dedupe(results)


_QUERY_NOISE = {
    "art", "box", "cover", "covers", "wallpaper", "icon", "icons", "png",
    "front", "back", "official", "image", "images", "game", "games",
    "playstation", "ps", "psx", "ps1", "ps2", "ps3", "ps4", "ps5",
    "xbox", "nintendo", "gamecube", "switch", "windows", "steam", "pc",
    "1920x1080", "1080p", "4k",
}


def _search_terms(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(query or "")) \
        .encode("ascii", "ignore").decode().lower()
    return [token for token in re.findall(r"[a-z0-9]+", normalized)
            if len(token) > 1 and token not in _QUERY_NOISE]


def rank_relevant(results: list[dict], query: str) -> list[dict]:
    """Descarta coincidências genéricas que ignoram parte do título do jogo."""
    terms = list(dict.fromkeys(_search_terms(query)))
    if len(terms) < 2:
        return _dedupe(results)
    required = max(2, (len(terms) * 2 + 2) // 3)  # teto de 2/3 dos termos
    phrase = " ".join(terms)
    ranked = []
    for index, item in enumerate(_dedupe(results)):
        raw = " ".join(str(item.get(key) or "")
                       for key in ("title", "source", "url"))
        haystack = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore") \
            .decode().lower()
        words = set(re.findall(r"[a-z0-9]+", haystack))
        matches = sum(term in words for term in terms)
        if matches < required:
            continue
        score = matches * 10 + (20 if phrase in haystack else 0)
        ranked.append((-score, index, item))
    ranked.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in ranked]


def _blocked(response) -> bool:
    text = (getattr(response, "text", "") or "").lower()
    markers = ("captcha", "unusual traffic", "detected unusual traffic",
               "nossos sistemas detectaram tráfego incomum", "/sorry/",
               "httpservice/retry/enablejs")
    return getattr(response, "status_code", 0) in (403, 429) or any(item in text for item in markers)


def _request_google(query: str, page: int, safe: str, timeout: int) -> dict:
    params = {"tbm": "isch", "q": query, "start": page * 20,
              "safe": "off" if safe == "off" else "active", "hl": "pt-BR", "gl": "br"}
    response = requests.get(
        GOOGLE_SEARCH_URL, params=params, timeout=timeout,
        headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=.9,en;q=.7"},
    )
    if _blocked(response):
        return {"ok": False, "blocked": True, "reason": "captcha", "results": []}
    if response.status_code != 200:
        return {"ok": False, "blocked": response.status_code in (403, 429),
                "reason": f"HTTP {response.status_code}", "results": []}
    results = parse_google_results(response.text)
    return {"ok": bool(results), "blocked": False,
            "reason": "" if results else "formato incompatível", "results": results}


def _request_bing(query: str, page: int, safe: str, timeout: int) -> dict:
    params = {"q": query, "first": page * 35 + 1, "count": 35, "adlt": safe}
    response = requests.get(
        BING_SEARCH_URL, params=params, timeout=timeout,
        headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=.9,en;q=.7"},
    )
    if _blocked(response):
        return {"ok": False, "blocked": True, "reason": "captcha", "results": []}
    if response.status_code != 200:
        return {"ok": False, "blocked": response.status_code in (403, 429),
                "reason": f"HTTP {response.status_code}", "results": []}
    results = rank_relevant(parse_results(response.text), query)
    for item in results:
        item["provider"] = "bing"
    return {"ok": bool(results), "blocked": False,
            "reason": "" if results else "sem resultados relevantes", "results": results}


def _request_yandex(query: str, page: int, safe: str, timeout: int) -> dict:
    params = {"text": query, "p": page,
              "family": "no" if safe == "off" else "yes"}
    response = requests.get(
        YANDEX_SEARCH_URL, params=params, timeout=timeout,
        headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=.9,en;q=.7"},
    )
    if _blocked(response):
        return {"ok": False, "blocked": True, "reason": "captcha", "results": []}
    if response.status_code != 200:
        return {"ok": False, "blocked": response.status_code in (403, 429),
                "reason": f"HTTP {response.status_code}", "results": []}
    results = rank_relevant(parse_yandex_results(response.text), query)
    return {"ok": bool(results), "blocked": False,
            "reason": "" if results else "sem resultados relevantes", "results": results}


def search(query: str, page: int = 0, safe: str = "moderate", timeout: int = 20,
           provider: str = "google") -> dict:
    query = str(query or "").strip()
    if not query:
        raise WebImageSearchError("Informe o nome do jogo para buscar imagens.")
    page = max(0, min(10, int(page or 0)))
    safe = safe if safe in ("strict", "moderate", "off") else "moderate"
    provider = provider if provider in ("google", "yandex", "bing") else "google"
    key = (query.casefold(), page, safe, provider)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return copy_result(cached[1], cached=True)

    google_url = google_public_search_url(query, safe)
    requesters = {
        "google": _request_google, "yandex": _request_yandex, "bing": _request_bing,
    }
    chain = (["google", "yandex", "bing"] if provider == "google" else
             ["yandex", "bing"] if provider == "yandex" else ["bing"])
    attempts = []
    used, result = chain[0], {"ok": False, "blocked": False,
                              "reason": "sem resultados", "results": []}
    for candidate_provider in chain:
        try:
            attempt = requesters[candidate_provider](query, page, safe, timeout)
        except requests.RequestException as exc:
            attempt = {"ok": False, "blocked": False,
                       "reason": str(exc), "results": []}
        attempts.append((candidate_provider, attempt))
        used, result = candidate_provider, attempt
        if attempt.get("ok"):
            break
    primary = attempts[0][1]
    fallback_used = used != provider
    any_blocked = any(attempt.get("blocked") for _name, attempt in attempts)

    if not result.get("ok"):
        error = ("Os mecanismos pediram uma verificação no navegador."
                 if any_blocked else
                 f"Busca web indisponível: {result.get('reason') or primary.get('reason') or 'sem resultados'}.")
        payload = {
            "ok": False, "blocked": any_blocked,
            "provider": used, "fallback_used": fallback_used, "error": error,
            "query": query, "page": page, "results": [], "open_url": google_url,
            "open_google_url": google_url,
        }
    else:
        payload = {
            "ok": True, "blocked": any_blocked,
            "provider": used, "fallback_used": fallback_used,
            "fallback_reason": primary.get("reason", "") if fallback_used else "",
            "query": query, "page": page, "results": result["results"],
            "open_url": google_url, "open_google_url": google_url,
        }
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), payload)
    return copy_result(payload)


def copy_result(payload: dict, cached: bool = False) -> dict:
    result = dict(payload)
    result["results"] = [dict(item) for item in payload.get("results", [])]
    if cached:
        result["cached"] = True
    return result
