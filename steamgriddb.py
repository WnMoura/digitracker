"""Busca de capas no SteamGridDB — a mesma fonte que o Playnite usa.

A RetroAchievements só entrega UMA arte de cada tipo por jogo. Para o seletor
de capas (buscar pelo nome e escolher entre várias, estilo Playnite) é preciso
uma fonte que devolva um catálogo — o SteamGridDB faz isso e tem uma API limpa.

Precisa de uma chave gratuita (https://www.steamgriddb.com/profile/preferences/api),
guardada localmente em `config/secrets.json`, no mesmo esquema das chaves de IA.
A chave vai no header `Authorization: Bearer <key>` — e só; nada de headers
inventados.

As funções de parsing são puras (recebem o JSON já decodificado, devolvem
dados) para poderem ser testadas sem rede.
"""
from __future__ import annotations

import time
from pathlib import Path

API_BASE = "https://www.steamgriddb.com/api/v2"

# Dimensões de CAPA (retrato). O SteamGridDB chama qualquer arte retangular de
# "grid"; filtrando por essas medidas ficamos com as capas verticais, que é o
# que se espera de uma "capa de jogo". As paisagens (hero/banner) ficam de fora.
COVER_DIMENSIONS = "600x900,342x482,660x930,512x512"

RETRIES = 3
TIMEOUT = 20


class SteamGridDBError(Exception):
    """Falha ao acessar ou interpretar o SteamGridDB."""


def create_session(api_key: str):
    """Sessão autenticada. A chave vai só no header Bearer — não acrescente
    outros headers."""
    key = (api_key or "").strip()
    if not key:
        raise SteamGridDBError("Configure sua chave do SteamGridDB nas Configurações.")
    try:
        import requests
    except ImportError as exc:      # pragma: no cover - depende do ambiente
        raise SteamGridDBError("A biblioteca requests não está instalada.") from exc
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {key}"
    return session


# ---------------------------------------------------------------------------- #
# Parsing (puro — testável sem rede)
# ---------------------------------------------------------------------------- #
def _payload_data(payload) -> list:
    """Valida o envelope `{success, data|errors}` e devolve `data`."""
    if not isinstance(payload, dict):
        raise SteamGridDBError("Resposta inesperada do SteamGridDB.")
    if not payload.get("success", False):
        errors = payload.get("errors") or []
        msg = "; ".join(str(e) for e in errors) if errors else "requisição recusada."
        raise SteamGridDBError(f"SteamGridDB: {msg}")
    data = payload.get("data")
    return data if isinstance(data, list) else []


def parse_search(payload) -> list[dict]:
    """Jogos casados pela busca por nome: [{id, name}]."""
    out = []
    for item in _payload_data(payload):
        if not isinstance(item, dict):
            continue
        gid, name = item.get("id"), item.get("name")
        if isinstance(gid, int) and name:
            out.append({"id": gid, "name": str(name)})
    return out


def parse_grids(payload) -> list[dict]:
    """Capas de um jogo: [{id, url, thumb, width, height, style}].

    Só entram as que têm URL de imagem; a miniatura (`thumb`) cai para a `url`
    quando ausente, para a grade sempre ter o que mostrar."""
    out = []
    for item in _payload_data(payload):
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        out.append({
            "id": item.get("id"),
            "url": str(url),
            "thumb": str(item.get("thumb") or url),
            "width": item.get("width") or 0,
            "height": item.get("height") or 0,
            "style": str(item.get("style") or ""),
        })
    return out


# ---------------------------------------------------------------------------- #
# Rede
# ---------------------------------------------------------------------------- #
def _get_json(session, url: str, params: dict | None = None, retries: int = RETRIES):
    """GET com retentativa e mensagens de erro específicas do SteamGridDB."""
    last = ""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)
        except Exception as exc:                 # rede, TLS…
            last = str(exc)
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise SteamGridDBError("Resposta ilegível do SteamGridDB.") from exc
            if resp.status_code in (401, 403):
                raise SteamGridDBError(
                    "Chave do SteamGridDB inválida ou sem permissão."
                )
            if resp.status_code == 404:
                raise SteamGridDBError("Nada encontrado no SteamGridDB (404).")
            last = f"HTTP {resp.status_code}"
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise SteamGridDBError(f"Não consegui falar com o SteamGridDB: {last}")


def search_games(session, term: str) -> list[dict]:
    """Busca jogos pelo nome (autocomplete)."""
    term = (term or "").strip()
    if not term:
        return []
    return parse_search(_get_json(session, f"{API_BASE}/search/autocomplete/{term}"))


def game_covers(session, game_id: int, dimensions: str = COVER_DIMENSIONS) -> list[dict]:
    """Capas de um jogo pelo id do SteamGridDB (só estáticas, sem NSFW)."""
    params = {"dimensions": dimensions, "types": "static", "nsfw": "false"}
    return parse_grids(_get_json(session, f"{API_BASE}/grids/game/{game_id}", params))


def game_heroes(session, game_id: int) -> list[dict]:
    """Heroes (wallpapers horizontais) de um jogo — usados como fundo da lista de
    conquistas. Mesmo formato das capas; sem NSFW e só estáticos."""
    params = {"types": "static", "nsfw": "false"}
    return parse_grids(_get_json(session, f"{API_BASE}/heroes/game/{game_id}", params))


def download_cover(session, url: str, dest_path) -> bool:
    """Baixa a imagem da capa escolhida para o disco (cache local)."""
    if not url:
        return False
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = session.get(url, timeout=TIMEOUT)
    except Exception:
        return False
    if resp.status_code == 200 and resp.content:
        dest_path.write_bytes(resp.content)
        return True
    return False
