"""Mídia segura e rastreável para os Guias Inteligentes."""
from __future__ import annotations

import base64
import hashlib
import html
import io
import ipaddress
import json
import os
import re
import socket
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

MAX_IMAGE_BYTES = 20 * 1024 * 1024
UA = "DigiTracker/0.8 (+https://github.com/WnMoura/digitracker)"


class GuideMediaError(ValueError):
    pass


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _safe_slug(value: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9_-]", "", str(value or ""))
    if not out:
        raise GuideMediaError("Identificador de jogo inválido.")
    return out


def _safe_remote_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GuideMediaError("URL de imagem inválida.")
    if parsed.username or parsed.password:
        raise GuideMediaError("URLs com credenciais não são permitidas.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except OSError as exc:
        raise GuideMediaError("Não foi possível localizar o servidor da imagem.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise GuideMediaError("Endereços locais ou privados não são permitidos.")
    return parsed.geturl()


def _extension(name: str = "", mime: str = "") -> str:
    suffix = Path(name or "").suffix.lower().lstrip(".")
    by_mime = {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
        "image/gif": "gif", "image/svg+xml": "svg", "image/bmp": "bmp",
        "image/tiff": "tiff",
    }
    ext = by_mime.get((mime or "").split(";", 1)[0].lower(), suffix)
    return ext if ext in {"jpg", "jpeg", "png", "webp", "gif", "svg", "bmp", "tiff"} else "img"


class GuideMediaLibrary:
    def __init__(self, guide_root: Path, assets_root: Path):
        self.guide_root = Path(guide_root)
        self.assets_root = Path(assets_root)

    def _index_path(self, slug: str) -> Path:
        return self.guide_root / _safe_slug(slug) / "media.json"

    def _asset_dir(self, slug: str) -> Path:
        return self.assets_root / _safe_slug(slug)

    def list(self, slug: str) -> list[dict]:
        try:
            value = json.loads(self._index_path(slug).read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, ValueError):
            return []

    def _save_item(self, slug: str, data: bytes, extension: str, metadata: dict) -> dict:
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise GuideMediaError("Imagem vazia ou maior que 20 MB.")
        digest = hashlib.sha256(data).hexdigest()
        ext = re.sub(r"[^a-z0-9]", "", extension.lower()) or "img"
        asset = self._asset_dir(slug) / f"{digest}.{ext}"
        asset.parent.mkdir(parents=True, exist_ok=True)
        if not asset.exists():
            tmp = asset.with_name(f".{asset.name}.tmp")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, asset)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        item = {
            "id": f"media_{digest[:16]}", "sha256": digest,
            "url": f"/assets/guides/{_safe_slug(slug)}/{asset.name}",
            "status": "approved", **metadata,
        }
        items = self.list(slug)
        existing = next((entry for entry in items if entry.get("sha256") == digest), None)
        if existing:
            return existing
        items.append(item)
        _atomic_json(self._index_path(slug), items)
        return item

    def extract_pdf(self, slug: str, raw: bytes, filename: str = "") -> dict:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
        except Exception as exc:
            raise GuideMediaError(f"PDF inválido: {exc}") from exc
        extracted = []
        text_chars = 0
        for page_number, page in enumerate(reader.pages, 1):
            try:
                text_chars += len((page.extract_text() or "").strip())
            except Exception:
                pass
            try:
                images = list(page.images)
            except Exception:
                images = []
            for position, image in enumerate(images, 1):
                data = bytes(image.data)
                if not data or len(data) > MAX_IMAGE_BYTES:
                    continue
                ext = _extension(getattr(image, "name", ""))
                try:
                    item = self._save_item(slug, data, ext, {
                        "type": "image", "title": f"Imagem da página {page_number}",
                        "source": "pdf", "source_name": str(filename or "PDF importado"),
                        "source_url": "", "creator": "", "license": "Fonte fornecida pelo usuário",
                        "license_url": "", "attribution": f"{filename or 'PDF'}, página {page_number}",
                        "page": page_number, "position": position,
                    })
                    extracted.append(item)
                except GuideMediaError:
                    continue
        return {
            "images": extracted, "image_count": len(extracted),
            "pages": len(reader.pages), "scanned": text_chars < max(40, len(reader.pages) * 12),
            "text_chars": text_chars,
        }

    def add_local(self, slug: str, encoded: str, filename: str, title: str = "") -> dict:
        try:
            data = base64.b64decode((encoded or "").split(",", 1)[-1], validate=True)
        except ValueError as exc:
            raise GuideMediaError("Arquivo de imagem inválido.") from exc
        return self._save_item(slug, data, _extension(filename), {
            "type": "image", "title": str(title or filename or "Imagem local")[:500],
            "source": "local", "source_name": str(filename or "Arquivo local"),
            "source_url": "", "creator": "", "license": "Fornecida pelo usuário",
            "license_url": "", "attribution": str(filename or "Arquivo local"),
            "page": 0, "position": 0,
        })

    @staticmethod
    def search(query: str, source: str = "openverse") -> list[dict]:
        query = re.sub(r"\s+", " ", str(query or "")).strip()[:300]
        if len(query) < 2:
            raise GuideMediaError("Digite ao menos dois caracteres para buscar.")
        try:
            if source == "openverse":
                response = requests.get(
                    "https://api.openverse.org/v1/images/",
                    params={"q": query, "page_size": 24, "mature": "false"},
                    headers={"User-Agent": UA}, timeout=25,
                )
                response.raise_for_status()
                results = response.json().get("results") or []
                return [{
                    "id": str(item.get("id") or ""), "source": "openverse",
                    "title": item.get("title") or "Imagem sem título",
                    "thumbnail": item.get("thumbnail") or "", "url": item.get("url") or "",
                    "landing_url": item.get("foreign_landing_url") or "",
                    "creator": item.get("creator") or "Autor desconhecido",
                    "license": " ".join(filter(None, [item.get("license"), item.get("license_version")])),
                    "license_url": item.get("license_url") or "",
                    "attribution": item.get("attribution") or "",
                    "provider": item.get("provider") or item.get("source") or "Openverse",
                } for item in results if item.get("url") and item.get("thumbnail")]
            if source == "wikimedia":
                response = requests.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query", "format": "json", "generator": "search",
                        "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": 24,
                        "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 480,
                    }, headers={"User-Agent": UA}, timeout=25,
                )
                response.raise_for_status()
                pages = (response.json().get("query") or {}).get("pages") or {}
                output = []
                for page in pages.values():
                    info = (page.get("imageinfo") or [{}])[0]
                    meta = info.get("extmetadata") or {}
                    plain = lambda key: re.sub(r"<[^>]+>", "", str((meta.get(key) or {}).get("value") or ""))
                    if info.get("url") and info.get("thumburl"):
                        output.append({
                            "id": str(page.get("pageid") or ""), "source": "wikimedia",
                            "title": str(page.get("title") or "").removeprefix("File:"),
                            "thumbnail": info.get("thumburl"), "url": info.get("url"),
                            "landing_url": info.get("descriptionurl") or "",
                            "creator": plain("Artist") or "Autor desconhecido",
                            "license": plain("LicenseShortName"),
                            "license_url": plain("LicenseUrl"), "attribution": plain("Credit"),
                            "provider": "Wikimedia Commons",
                        })
                return output
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise GuideMediaError(f"Falha ao buscar imagens: {exc}") from exc
        raise GuideMediaError("Fonte de mídia desconhecida.")

    def approve_remote(self, slug: str, candidate: dict, rights_confirmed: bool = False) -> dict:
        if not isinstance(candidate, dict):
            raise GuideMediaError("Resultado de mídia inválido.")
        source = str(candidate.get("source") or "manual")
        license_name = str(candidate.get("license") or "").strip()
        if source not in {"openverse", "wikimedia"} and not rights_confirmed:
            raise GuideMediaError("Confirme que você pode usar esta imagem antes de salvá-la.")
        url = _safe_remote_url(candidate.get("url") or "")
        try:
            response = None
            final_url = url
            # Redirecionamentos são validados um a um para impedir que um CDN
            # público redirecione o downloader para localhost/rede privada.
            for _ in range(6):
                response = requests.get(final_url, headers={"User-Agent": UA}, timeout=30,
                                        allow_redirects=False, stream=True)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location") or ""
                response.close()
                final_url = _safe_remote_url(urljoin(final_url, location))
            if response is None or response.status_code in {301, 302, 303, 307, 308}:
                raise GuideMediaError("A imagem excedeu o limite de redirecionamentos.")
            with response:
                response.raise_for_status()
                mime = (response.headers.get("content-type") or "").split(";", 1)[0].lower()
                if not mime.startswith("image/"):
                    raise GuideMediaError("O endereço não devolveu uma imagem.")
                chunks, size = [], 0
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise GuideMediaError("Imagem maior que 20 MB.")
                    chunks.append(chunk)
                data = b"".join(chunks)
        except requests.RequestException as exc:
            raise GuideMediaError(f"Não foi possível baixar a imagem: {exc}") from exc
        return self._save_item(slug, data, _extension(final_url, mime), {
            "type": "image", "title": str(candidate.get("title") or "Imagem")[:500],
            "source": source, "source_name": str(candidate.get("provider") or source),
            "source_url": str(candidate.get("landing_url") or final_url),
            "creator": str(candidate.get("creator") or ""), "license": license_name,
            "license_url": str(candidate.get("license_url") or ""),
            "attribution": str(candidate.get("attribution") or ""), "page": 0, "position": 0,
        })

    def remove(self, slug: str, media_id: str) -> bool:
        items = self.list(slug)
        removed = next((item for item in items if item.get("id") == media_id), None)
        kept = [item for item in items if item.get("id") != media_id]
        if len(kept) == len(items):
            return False
        _atomic_json(self._index_path(slug), kept)
        if removed and not any(item.get("sha256") == removed.get("sha256") for item in kept):
            name = Path(str(removed.get("url") or "")).name
            target = (self._asset_dir(slug) / name).resolve()
            try:
                target.relative_to(self._asset_dir(slug).resolve())
                target.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        return True

    def create_diagram(self, slug: str, spec: dict) -> dict:
        """Renderiza um grafo simples. Nenhum SVG/HTML arbitrário entra aqui."""
        if not isinstance(spec, dict):
            raise GuideMediaError("Diagrama inválido.")
        nodes = [node for node in (spec.get("nodes") or []) if isinstance(node, dict)][:30]
        edges = [edge for edge in (spec.get("edges") or []) if isinstance(edge, dict)][:60]
        if not nodes:
            raise GuideMediaError("O diagrama precisa de ao menos um nó.")
        width = 900
        cols = max(1, min(4, int(len(nodes) ** 0.5 + 0.999)))
        rows = (len(nodes) + cols - 1) // cols
        height = max(240, rows * 150 + 90)
        positions = {}
        for index, node in enumerate(nodes):
            positions[str(node.get("id") or index)] = (90 + (index % cols) * (720 / max(1, cols - 1)), 90 + (index // cols) * 150)
        svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
               '<rect width="100%" height="100%" rx="24" fill="#0b141d"/>']
        for edge in edges:
            start, end = positions.get(str(edge.get("from"))), positions.get(str(edge.get("to")))
            if not start or not end:
                continue
            svg.append(f'<path d="M {start[0]} {start[1]} L {end[0]} {end[1]}" stroke="#3f8cff" stroke-width="3" opacity=".75"/>')
        for index, node in enumerate(nodes):
            x, y = positions[str(node.get("id") or index)]
            label = html.escape(re.sub(r"\s+", " ", str(node.get("label") or "Etapa"))[:42])
            svg.extend([
                f'<rect x="{x-76}" y="{y-34}" width="152" height="68" rx="16" fill="#172838" stroke="#5cb8ff"/>',
                f'<text x="{x}" y="{y+5}" text-anchor="middle" fill="#f5f8fc" font-family="Arial,sans-serif" font-size="15">{label}</text>',
            ])
        svg.append("</svg>")
        data = "".join(svg).encode("utf-8")
        return self._save_item(slug, data, "svg", {
            "type": str(spec.get("type") or "graph"),
            "title": str(spec.get("title") or "Diagrama")[:500],
            "source": "generated", "source_name": "DigiTracker",
            "source_url": "", "creator": "DigiTracker", "license": "Gerado localmente",
            "license_url": "", "attribution": "Gerado a partir do guia", "page": 0, "position": 0,
        })
