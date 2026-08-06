"""Busca de imagens no IGDB — base grande e bem curada, ótima para retrô.

O IGDB dá capas de qualidade (retrato) e screenshots. O acesso é pela API da
Twitch: um app criado em https://dev.twitch.tv/console/apps gera um Client ID +
Client Secret, que trocamos por um token OAuth (client-credentials). O token
dura ~60 dias; quem cacheia é o engine.

A query da API é em "Apicalypse" (texto solto no corpo do POST), com os headers
`Client-ID` e `Authorization: Bearer <token>`.

Parsing puro (recebe JSON já decodificado) e montagem de URL são testáveis sem
rede. O download da imagem em si é do `image_fetch`.
"""
from __future__ import annotations

import re

API_BASE = "https://api.igdb.com/v4"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IMG_BASE = "https://images.igdb.com/igdb/image/upload"
TIMEOUT = 20


class IgdbError(Exception):
    """Falha ao acessar ou interpretar o IGDB."""


def create_session():
    try:
        import requests
    except ImportError as exc:      # pragma: no cover - depende do ambiente
        raise IgdbError("A biblioteca requests não está instalada.") from exc
    return requests.Session()


# ---------------------------------------------------------------------------- #
# URLs de imagem
# ---------------------------------------------------------------------------- #
def cover_url(image_id: str, size: str = "t_cover_big") -> str:
    return f"{IMG_BASE}/{size}/{image_id}.jpg"


def screenshot_url(image_id: str, size: str = "t_screenshot_huge") -> str:
    return f"{IMG_BASE}/{size}/{image_id}.jpg"


# ---------------------------------------------------------------------------- #
# Parsing (puro — testável sem rede)
# ---------------------------------------------------------------------------- #
def parse_token(payload) -> dict:
    """Token OAuth do Twitch: {token, expires_in}."""
    if not isinstance(payload, dict) or not payload.get("access_token"):
        msg = (payload or {}).get("message") if isinstance(payload, dict) else ""
        raise IgdbError(f"Não consegui autenticar no IGDB/Twitch. {msg or ''}".strip())
    return {"token": str(payload["access_token"]),
            "expires_in": int(payload.get("expires_in") or 0)}


def parse_games(payload) -> list[dict]:
    """Jogos do IGDB: [{id, name, cover, shots}]. `cover` é o image_id da capa
    (ou ''), `shots` a lista de image_ids de screenshots."""
    if not isinstance(payload, list):
        raise IgdbError("Resposta inesperada do IGDB.")
    out = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        gid, name = item.get("id"), item.get("name")
        if not (isinstance(gid, int) and name):
            continue
        cover = ""
        if isinstance(item.get("cover"), dict):
            cover = str(item["cover"].get("image_id") or "")
        shots = [str(s.get("image_id")) for s in item.get("screenshots", [])
                 if isinstance(s, dict) and s.get("image_id")]
        out.append({"id": gid, "name": str(name), "cover": cover, "shots": shots})
    return out


def covers_of(game: dict) -> list[dict]:
    """Capa (retrato) de um jogo no formato de célula da grade."""
    cid = game.get("cover")
    if not cid:
        return []
    return [{"id": cid, "url": cover_url(cid), "thumb": cover_url(cid, "t_cover_small"),
             "width": 0, "height": 0, "style": "cover"}]


def heroes_of(game: dict) -> list[dict]:
    """Screenshots (paisagem) de um jogo no formato de célula da grade."""
    return [{"id": sid, "url": screenshot_url(sid),
             "thumb": screenshot_url(sid, "t_thumb"),
             "width": 0, "height": 0, "style": "screenshot"}
            for sid in game.get("shots", [])]


def _clean_term(term: str) -> str:
    """Remove aspas do termo — a busca Apicalypse é cercada por aspas."""
    return re.sub(r'["\\]', " ", (term or "")).strip()


# ---------------------------------------------------------------------------- #
# Rede
# ---------------------------------------------------------------------------- #
def get_token(session, client_id: str, client_secret: str) -> dict:
    """Troca client id/secret por um token OAuth (client-credentials)."""
    try:
        resp = session.post(TOKEN_URL, params={
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "client_credentials",
        }, timeout=TIMEOUT)
    except Exception as exc:
        raise IgdbError(f"Falha de rede ao autenticar no Twitch: {exc}") from exc
    if resp.status_code in (400, 401, 403):
        raise IgdbError("Client ID/Secret do IGDB (Twitch) inválidos.")
    try:
        return parse_token(resp.json())
    except ValueError as exc:
        raise IgdbError("Resposta ilegível do Twitch.") from exc


def _query(session, client_id: str, token: str, body: str) -> list:
    headers = {"Client-ID": client_id, "Authorization": f"Bearer {token}"}
    try:
        resp = session.post(f"{API_BASE}/games", headers=headers, data=body, timeout=TIMEOUT)
    except Exception as exc:
        raise IgdbError(f"Falha de rede ao falar com o IGDB: {exc}") from exc
    if resp.status_code in (401, 403):
        raise IgdbError("Token do IGDB inválido ou expirado.")
    if resp.status_code != 200:
        raise IgdbError(f"O IGDB respondeu {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise IgdbError("Resposta ilegível do IGDB.") from exc


_FIELDS = "fields id,name,cover.image_id,screenshots.image_id"


def search_games(session, client_id: str, token: str, term: str) -> list[dict]:
    term = _clean_term(term)
    if not term:
        return []
    body = f'{_FIELDS}; search "{term}"; where version_parent = null; limit 8;'
    return parse_games(_query(session, client_id, token, body))


def game_by_id(session, client_id: str, token: str, game_id: int) -> dict | None:
    body = f"{_FIELDS}; where id = {int(game_id)}; limit 1;"
    games = parse_games(_query(session, client_id, token, body))
    return games[0] if games else None
