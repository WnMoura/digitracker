"""Busca de imagens no RAWG — base gigante de jogos, inclui retrô.

Complementa o SteamGridDB: o RAWG cobre bem títulos antigos (PS1/PS2/GameCube)
com screenshots e uma imagem de fundo por jogo. Não tem CAPA em retrato dedicada,
então as imagens do RAWG servem melhor como FUNDO (heroes) da lista de conquistas.

Chave gratuita em https://rawg.io/apidocs — vai como parâmetro de query `?key=`,
não como header. Guardada em `config/secrets.json`.

Parsing puro (recebe o JSON já decodificado) para testar sem rede. O download da
imagem em si é do `image_fetch` (sessão limpa), não daqui.
"""
from __future__ import annotations

import time

API_BASE = "https://api.rawg.io/api"
RETRIES = 3
TIMEOUT = 20
PAGE_SIZE = 8


class RawgError(Exception):
    """Falha ao acessar ou interpretar o RAWG."""


def create_session(api_key: str):
    """Sessão simples + a chave (que vai por query param). Devolve (session, key)."""
    key = (api_key or "").strip()
    if not key:
        raise RawgError("Configure sua chave do RAWG nas Configurações.")
    try:
        import requests
    except ImportError as exc:      # pragma: no cover - depende do ambiente
        raise RawgError("A biblioteca requests não está instalada.") from exc
    return requests.Session(), key


# ---------------------------------------------------------------------------- #
# Parsing (puro — testável sem rede)
# ---------------------------------------------------------------------------- #
def parse_search(payload) -> list[dict]:
    """Jogos casados pela busca: [{id, name, background}]. `background` é a
    imagem de fundo do jogo (pode ser vazia)."""
    out = []
    for item in _results(payload):
        gid, name = item.get("id"), item.get("name")
        if isinstance(gid, int) and name:
            out.append({"id": gid, "name": str(name),
                        "background": item.get("background_image") or ""})
    return out


def parse_screenshots(payload) -> list[dict]:
    """Screenshots de um jogo -> [{id, url, thumb, width, height, style}] (no
    mesmo formato das capas do SteamGridDB, para a grade reusar)."""
    out = []
    for item in _results(payload):
        url = item.get("image")
        if not url:
            continue
        out.append({
            "id": item.get("id"),
            "url": str(url),
            "thumb": str(url),
            "width": item.get("width") or 0,
            "height": item.get("height") or 0,
            "style": "screenshot",
        })
    return out


def _results(payload) -> list:
    """`{results: [...]}` do RAWG, validado."""
    if not isinstance(payload, dict):
        raise RawgError("Resposta inesperada do RAWG.")
    if payload.get("error"):
        raise RawgError(f"RAWG: {payload['error']}")
    data = payload.get("results")
    return data if isinstance(data, list) else []


def hero_from_background(background: str) -> list[dict]:
    """A imagem de fundo do jogo como um 'hero' na grade (se houver)."""
    if not background:
        return []
    return [{"id": "bg", "url": str(background), "thumb": str(background),
             "width": 0, "height": 0, "style": "background"}]


# ---------------------------------------------------------------------------- #
# Rede
# ---------------------------------------------------------------------------- #
def _get_json(session, url: str, params: dict, retries: int = RETRIES):
    last = ""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)
        except Exception as exc:
            last = str(exc)
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise RawgError("Resposta ilegível do RAWG.") from exc
            if resp.status_code in (401, 403):
                raise RawgError("Chave do RAWG inválida ou sem permissão.")
            if resp.status_code == 404:
                raise RawgError("Nada encontrado no RAWG (404).")
            last = f"HTTP {resp.status_code}"
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise RawgError(f"Não consegui falar com o RAWG: {last}")


def search_games(session, key: str, term: str) -> list[dict]:
    term = (term or "").strip()
    if not term:
        return []
    payload = _get_json(session, f"{API_BASE}/games",
                        {"key": key, "search": term, "page_size": PAGE_SIZE})
    return parse_search(payload)


def game_screenshots(session, key: str, game_id: int) -> list[dict]:
    payload = _get_json(session, f"{API_BASE}/games/{int(game_id)}/screenshots",
                        {"key": key})
    return parse_screenshots(payload)
