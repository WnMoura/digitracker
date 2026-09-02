"""Download de assets de imagem (capas, wallpapers) para o disco.

Separado das chamadas de API de cada fonte de propósito: o asset mora num CDN
público e NÃO deve carregar o token da API. Mandar o `Authorization: Bearer` do
SteamGridDB (ou de qualquer fonte) para o CDN vaza a chave e costuma tomar 403 —
era essa a causa do download falhar. Aqui a sessão é limpa (sem auth) e com
`User-Agent` de navegador; sem UA, CDNs atrás de Cloudflare recusam a requisição.

Serve todas as fontes (SteamGridDB, RAWG, IGDB) e o modo "colar URL".
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

# UA de navegador: sem ele, alguns CDNs (Cloudflare) devolvem 403.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 25
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _public_host(host: str) -> tuple[bool, str]:
    """Valida literal e resolucao DNS para impedir downloads da rede local."""
    host = str(host or "").strip("[]").lower()
    if not host or host in ("localhost", "localhost.localdomain"):
        return False, "Endereços locais não são permitidos."
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            return False, "Não foi possível resolver o endereço da imagem."
    if not addresses or any(not address.is_global for address in addresses):
        return False, "Endereços de rede privada não são permitidos."
    return True, ""


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


def download_image_validated(url: str, dest_path, max_bytes: int = MAX_IMAGE_BYTES) -> dict:
    """Baixa, valida e converte uma imagem para PNG de forma atômica."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "URL de imagem inválida."}
    public, error = _public_host(parsed.hostname)
    if not public:
        return {"ok": False, "error": error}
    try:
        response = __import__("requests").get(
            url, timeout=TIMEOUT, allow_redirects=True, stream=True,
            headers={"User-Agent": _UA, "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=.5"},
        )
    except Exception as exc:
        return {"ok": False, "error": f"Falha no download: {exc}"}
    if response.status_code != 200:
        response.close()
        return {"ok": False, "error": f"O servidor respondeu HTTP {response.status_code}."}
    final_url = str(getattr(response, "url", "") or url)
    final_host = urlparse(final_url).hostname
    final_public, final_error = _public_host(final_host)
    if not final_public:
        response.close()
        return {"ok": False, "error": final_error}
    ctype = (response.headers.get("content-type") or "").split(";", 1)[0].lower()
    if not ctype.startswith("image/"):
        response.close()
        return {"ok": False, "error": "A URL não retornou uma imagem."}
    try:
        advertised = int(response.headers.get("content-length") or 0)
    except (TypeError, ValueError):
        advertised = 0
    if advertised > max_bytes:
        response.close()
        return {"ok": False, "error": "A imagem excede o limite de 20 MB."}
    raw = bytearray()
    try:
        for chunk in response.iter_content(64 * 1024):
            raw.extend(chunk)
            if len(raw) > max_bytes:
                return {"ok": False, "error": "A imagem excede o limite de 20 MB."}
    finally:
        response.close()
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            width, height = image.size
            if width < 64 or height < 64 or width > 12000 or height > 12000:
                return {"ok": False, "error": "Dimensões da imagem fora do limite permitido."}
            converted = image.convert("RGBA" if image.mode in ("RGBA", "LA") else "RGB")
            output = io.BytesIO()
            converted.save(output, format="PNG", optimize=True)
            png = output.getvalue()
    except Exception:
        return {"ok": False, "error": "O arquivo recebido está corrompido ou não é uma imagem válida."}
    digest = hashlib.sha256(png).hexdigest()
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp = dest_path.with_suffix(dest_path.suffix + ".tmp")
    temp.write_bytes(png)
    temp.replace(dest_path)
    return {"ok": True, "width": width, "height": height, "mime": "image/png",
            "bytes": len(png), "sha256": digest}
