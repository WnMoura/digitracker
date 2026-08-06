"""Download de assets de imagem (capas, wallpapers) para o disco.

Separado das chamadas de API de cada fonte de propósito: o asset mora num CDN
público e NÃO deve carregar o token da API. Mandar o `Authorization: Bearer` do
SteamGridDB (ou de qualquer fonte) para o CDN vaza a chave e costuma tomar 403 —
era essa a causa do download falhar. Aqui a sessão é limpa (sem auth) e com
`User-Agent` de navegador; sem UA, CDNs atrás de Cloudflare recusam a requisição.

Serve todas as fontes (SteamGridDB, RAWG, IGDB) e o modo "colar URL".
"""
from __future__ import annotations

from pathlib import Path

# UA de navegador: sem ele, alguns CDNs (Cloudflare) devolvem 403.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 25


def download_image(url: str, dest_path) -> bool:
    """Baixa a imagem de `url` para `dest_path`. Sessão limpa (sem header de
    auth), com User-Agent, seguindo redirects. Devolve True se gravou os bytes
    de uma resposta 200 que não seja HTML/JSON (uma página de erro)."""
    if not url:
        return False
    dest_path = Path(dest_path)
    try:
        import requests
    except ImportError:      # pragma: no cover - depende do ambiente
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, timeout=TIMEOUT, allow_redirects=True,
                            headers={"User-Agent": _UA})
    except Exception:
        return False
    if resp.status_code != 200 or not resp.content:
        return False
    ctype = resp.headers.get("content-type", "").lower()
    if "text/html" in ctype or "application/json" in ctype:
        return False        # página de erro, não uma imagem
    dest_path.write_bytes(resp.content)
    return True
