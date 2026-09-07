"""Explicit local exports and reversible artwork operations."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

import smart_guide


def write_backup(data_dir, destination, include_secrets=False, password=""):
    """Create outside config/assets, snapshot SQLite, never follow external links."""
    import pyzipper
    data_dir, destination = Path(data_dir).resolve(), Path(destination).resolve()
    if include_secrets and len(password) < 12:
        raise ValueError("Use uma senha de pelo menos 12 caracteres para incluir credenciais.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.partial")
    count = size = 0
    allowed = {".json", ".sqlite3", ".db", ".pdf", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
    sensitive = {"secrets.json", "credentials.json", "tokens.json"}
    try:
        with pyzipper.AESZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if password:
                archive.setpassword(password.encode("utf-8"))
                archive.setencryption(pyzipper.WZ_AES, nbits=256)
            for folder in ("config", "assets"):
                base = data_dir / folder
                for path in sorted(base.rglob("*")) if base.exists() else []:
                    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
                        continue
                    relative = path.relative_to(data_dir)
                    if any(part in {"cache", "_downloads", "logs"} for part in relative.parts):
                        continue
                    if path.suffix.lower() not in allowed or (not include_secrets and path.name.lower() in sensitive):
                        continue
                    if path.suffix in {".sqlite3", ".db"}:
                        with tempfile.TemporaryDirectory(prefix="digitracker-db-") as temp:
                            target = Path(temp) / "snapshot.sqlite3"
                            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as source, closing(sqlite3.connect(target)) as out:
                                source.backup(out)
                            archive.write(target, str(relative).replace("\\", "/"))
                    else:
                        archive.write(path, str(relative).replace("\\", "/"))
                    count += 1
                    size += path.stat().st_size
            archive.writestr("manifest.json", json.dumps({"format": "digitracker-backup", "version": 1,
                              "created_at": time.time(), "files": count, "includes_secrets": include_secrets}))
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": str(destination), "files": count, "bytes": size, "encrypted": bool(password)}


def library_manifest(games):
    rows = []
    for game in games:
        game_id = game.get("retroachievements_game_id") or game.get("external_game_id")
        if not str(game_id or "").isdigit():
            continue
        rows.append({"id": int(game_id), "title": game.get("title", ""), "platform": game.get("platform", "")})
    return {"format": "digitracker-library", "version": 1, "provider": "retroachievements", "games": rows}


def validate_library(value):
    if not isinstance(value, dict) or value.get("format") != "digitracker-library" or value.get("version") != 1 or value.get("provider") != "retroachievements":
        raise ValueError("Este arquivo não é uma biblioteca DigiTracker compatível.")
    rows = value.get("games")
    if not isinstance(rows, list) or not 0 < len(rows) <= 1000:
        raise ValueError("A biblioteca deve conter entre 1 e 1.000 jogos.")
    ids = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int or not 0 < row["id"] < 2**31:
            raise ValueError("Identificador de jogo inválido.")
        if row["id"] not in ids:
            ids.append(row["id"])
    return ids


class DataToolsApi:
    def create_local_backup(self, include_secrets=False, password=""):
        import engine
        try:
            destination = engine.DATA_DIR / "backups" / f"DigiTracker-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.zip"
            with self._guides._write_lock, self._lock, self._experience.lock:
                result = write_backup(engine.DATA_DIR, destination, bool(include_secrets), str(password))
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def export_library_manifest(self):
        import engine
        games = [engine.load_game_file(p) or {} for p in engine.GAMES_DIR.glob("*.json")]
        return {"ok": True, "document": library_manifest(games)}

    def import_library_manifest(self, document, confirm=False):
        try:
            ids = validate_library(document)
            existing = {int(g.get("retroachievements_game_id") or g.get("external_game_id") or 0) for g in self.state.values()}
            ids = [i for i in ids if i not in existing]
            if not confirm:
                return {"ok": True, "count": len(ids), "existing": len(validate_library(document)) - len(ids)}
            return self.start_bulk_import(ids) if ids else {"ok": True, "total": 0}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    def _remember_art(self, slug, game):
        import engine
        if not hasattr(self, "_experience"):
            return
        value = {k: copy.deepcopy(game.get(k)) for k in ("art", "art_meta", "palette", "accent", "icon", "icon_original")}
        key = "art-history:" + slug
        history = self._experience.get(self._experience_account(), key, [])
        if not history or history[-1] != value:
            self._experience.put(self._experience_account(), key, (history + [value])[-10:])

    def undo_game_art(self, slug):
        import engine
        if slug not in self.state:
            return {"ok": False, "error": "Jogo não encontrado."}
        account, key = self._experience_account(), "art-history:" + slug
        with self._lock:
            history = self._experience.get(account, key, [])
            if not history:
                return {"ok": False, "error": "Nenhuma alteração de arte para desfazer."}
            game = engine.load_game_file(engine.GAMES_DIR / f"{slug}.json")
            if not game:
                return {"ok": False, "error": "Jogo indisponível."}
            for field, value in history[-1].items():
                if value is None:
                    game.pop(field, None)
                else:
                    game[field] = value
            smart_guide._atomic_json(engine.GAMES_DIR / f"{slug}.json", game)
            for field in history[-1]:
                self.state[slug][field] = game.get(field)
            self._experience.put(account, key, history[:-1])
        return {"ok": True, "art": game.get("art", {}), "palette": game.get("palette", {})}

    def prepare_art_preview(self, slug, url, metadata=None):
        import engine
        if slug not in self.state:
            return {"ok": False, "error": "Jogo não encontrado."}
        token = uuid.uuid4().hex
        temporary = engine.ART_DIR / "_downloads" / f"preview-{token}.png"
        result = engine.image_fetch.download_image_validated(str(url), temporary)
        if not result.get("ok"):
            return result
        cached = engine.ART_DIR / "_cache" / f"{result['sha256']}.png"
        cached.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, cached)
        if not hasattr(self, "_art_previews"):
            self._art_previews = {}
        with self._lock:
            self._art_previews = {k: v for k, v in self._art_previews.items() if v["expires"] > time.time()}
            self._art_previews[token] = {"slug": slug, "path": cached, "metadata": metadata or {}, "url": str(url), "expires": time.time() + 1800}
        return {"ok": True, "token": token, "url": f"/assets/art/_cache/{cached.name}", "width": result.get("width"), "height": result.get("height")}

    def apply_art_preview(self, slug, token, role, crop=None):
        import engine
        from PIL import Image
        preview = getattr(self, "_art_previews", {}).get(str(token))
        if not preview or preview["slug"] != slug or preview["expires"] < time.time():
            return {"ok": False, "error": "Prévia expirada. Selecione a imagem novamente."}
        roles = ["cover", "background"] if role == "both" else [role]
        if any(r not in self._ROLE_ART for r in roles):
            return {"ok": False, "error": "Tipo de arte inválido."}
        try:
            with Image.open(preview["path"]) as original:
                image = original.convert("RGBA")
                if crop:
                    x, y, w, h = (float(crop[k]) for k in ("x", "y", "width", "height"))
                    if not all(math.isfinite(v) for v in (x, y, w, h)) or min(x, y) < 0 or min(w, h) <= 0 or x+w > 1.00001 or y+h > 1.00001:
                        raise ValueError("Seleção de recorte inválida.")
                    image = image.crop((round(x*image.width), round(y*image.height), round((x+w)*image.width), round((y+h)*image.height)))
                if min(image.size) < 32:
                    raise ValueError("O recorte deve ter pelo menos 32 pixels de cada lado.")
                stream = io.BytesIO()
                image.save(stream, format="PNG")
                data = stream.getvalue()
                digest = hashlib.sha256(data).hexdigest()
                target = engine.ART_DIR / "_cache" / f"{digest}.png"
                smart_guide._atomic_bytes(target, data)
                dimensions = image.size
            with self._lock:
                game = engine.load_game_file(engine.GAMES_DIR / f"{slug}.json")
                if not game:
                    raise ValueError("Jogo não encontrado.")
                self._remember_art(slug, game)
                art, meta = dict(game.get("art") or {}), dict(game.get("art_meta") or {})
                metadata = preview["metadata"] if isinstance(preview["metadata"], dict) else {}
                for r in roles:
                    art[self._ROLE_ART[r]] = f"/assets/art/_cache/{digest}.png"
                    meta[r] = {"manual": True, "origin": str(metadata.get("host") or "web")[:200],
                               "mechanism": str(metadata.get("provider") or "manual")[:30],
                               "query": str(metadata.get("query") or "")[:300], "source_url": preview["url"],
                               "source_page": str(metadata.get("source_page") or metadata.get("source") or "")[:2000],
                               "sha256": digest, "width": dimensions[0], "height": dimensions[1],
                               "crop": crop, "updated_at": time.time()}
                    if r == "icon":
                        game.setdefault("icon_original", game.get("icon", ""))
                        game["icon"] = art["icon"]
                meta["auto_status"] = "ready"
                self._persist_art(slug, game, art, meta)
                if not (game.get("palette") or {}).get("manual"):
                    self.recalculate_game_palette(slug)
                self._art_previews.pop(str(token), None)
            return {"ok": True, "art": art}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
