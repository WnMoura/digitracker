"""Modelo e armazenamento local do Guia Inteligente do DigiTracker.

O guia importado continua no JSON do jogo para compatibilidade. Esta camada cria
uma representação paralela, versionada e genérica, sem conhecer franquias,
plataformas ou emuladores. Todas as gravações são atômicas.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
import uuid
import zipfile
from copy import deepcopy
from pathlib import Path

SCHEMA_VERSION = 1
MAX_REVISIONS = 10
BLOCK_TYPES = {
    "text", "objective", "checklist", "warning", "missable", "achievement",
    "challenge", "table", "comparison", "image", "route", "graph", "note",
    "spoiler", "resource", "checkpoint",
}


class SmartGuideError(ValueError):
    pass


def _now() -> int:
    return int(time.time())


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha1(raw).hexdigest()[:12]}"


def _json_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError):
        return deepcopy(default)


def _clean_text(value: object, limit: int = 50_000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def validate_document(document: dict) -> dict:
    """Valida e normaliza a fronteira não confiável devolvida pela IA."""
    if not isinstance(document, dict):
        raise SmartGuideError("O Guia Inteligente não é um objeto JSON.")
    chapters = document.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise SmartGuideError("O Guia Inteligente não contém capítulos válidos.")
    clean = {
        "schema_version": SCHEMA_VERSION,
        "title": _clean_text(document.get("title") or "Guia Inteligente", 300),
        "summary": _clean_text(document.get("summary"), 2_000),
        "chapters": [],
        "visual_suggestions": [],
    }
    seen_ids: set[str] = set()
    for ci, chapter in enumerate(chapters[:300]):
        if not isinstance(chapter, dict):
            continue
        title = _clean_text(chapter.get("title") or f"Capítulo {ci + 1}", 500)
        blocks = chapter.get("blocks")
        if not isinstance(blocks, list):
            blocks = []
        clean_blocks = []
        for bi, block in enumerate(blocks[:500]):
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "text").strip().lower()
            if kind not in BLOCK_TYPES:
                raise SmartGuideError(f"Tipo de bloco não permitido: {kind}")
            block_id = _clean_text(block.get("id"), 80) or _stable_id("b", ci, bi, title)
            if block_id in seen_ids:
                block_id = _stable_id("b", ci, bi, title, len(seen_ids))
            seen_ids.add(block_id)
            refs = []
            for ref in block.get("source_refs") or []:
                if not isinstance(ref, dict):
                    continue
                refs.append({
                    "section": max(0, int(ref.get("section") or 0)),
                    "block": max(0, int(ref.get("block") or 0)),
                    "page": max(0, int(ref.get("page") or 0)),
                })
            items = []
            for item in block.get("items") or []:
                text = _clean_text(item.get("text") if isinstance(item, dict) else item, 2_000)
                if text:
                    items.append({"id": _stable_id("i", block_id, len(items), text), "text": text})
            row_data = []
            for row in block.get("rows") or []:
                if isinstance(row, list):
                    row_data.append([_clean_text(cell, 1_000) for cell in row[:12]])
            clean_blocks.append({
                "id": block_id,
                "type": kind,
                "title": _clean_text(block.get("title"), 500),
                "text": _clean_text(block.get("text"), 10_000),
                "items": items[:100],
                "rows": row_data[:100],
                "source_refs": refs[:20],
                "visual_id": _clean_text(block.get("visual_id"), 100),
                "estimated_minutes": max(0, min(24 * 60, int(block.get("estimated_minutes") or 0))),
            })
        if clean_blocks:
            clean["chapters"].append({
                "id": _clean_text(chapter.get("id"), 80) or _stable_id("c", ci, title),
                "title": title,
                "objective": _clean_text(chapter.get("objective"), 2_000),
                "estimated_minutes": max(0, min(24 * 60, int(chapter.get("estimated_minutes") or 0))),
                "blocks": clean_blocks,
            })
    if not clean["chapters"]:
        raise SmartGuideError("A IA não produziu nenhum bloco de guia utilizável.")
    for suggestion in document.get("visual_suggestions") or []:
        if not isinstance(suggestion, dict):
            continue
        kind = str(suggestion.get("type") or "image").lower()
        if kind not in {"image", "route", "graph", "comparison", "table"}:
            continue
        clean["visual_suggestions"].append({
            "id": _clean_text(suggestion.get("id"), 80) or _stable_id("v", kind, len(clean["visual_suggestions"])),
            "type": kind,
            "chapter_id": _clean_text(suggestion.get("chapter_id"), 80),
            "title": _clean_text(suggestion.get("title"), 500),
            "reason": _clean_text(suggestion.get("reason"), 2_000),
            "query": _clean_text(suggestion.get("query"), 500),
            "nodes": [{"id": _clean_text(node.get("id"), 80),
                       "label": _clean_text(node.get("label"), 100)}
                      for node in (suggestion.get("nodes") or []) if isinstance(node, dict)][:30],
            "edges": [{"from": _clean_text(edge.get("from"), 80),
                       "to": _clean_text(edge.get("to"), 80),
                       "label": _clean_text(edge.get("label"), 100)}
                      for edge in (suggestion.get("edges") or []) if isinstance(edge, dict)][:60],
            "status": "suggested",
        })
    return clean


def from_legacy_sections(title: str, sections: list) -> dict:
    """Fallback determinístico: todo guia já ganha os novos modos sem usar IA."""
    chapters = []
    mapping = {
        "p": "text", "li": "checklist", "note": "warning", "boss": "challenge",
        "step": "objective", "subhead": "checkpoint", "label": "resource",
    }
    for si, section in enumerate(sections or []):
        if not isinstance(section, dict):
            continue
        chapter_title = _clean_text(section.get("title") or f"Etapa {si + 1}")
        blocks = []
        for bi, legacy in enumerate(section.get("blocks") or []):
            if not isinstance(legacy, dict):
                continue
            text = _clean_text(legacy.get("text"), 10_000)
            if not text:
                continue
            kind = mapping.get(str(legacy.get("type") or "p"), "text")
            block_id = _stable_id("b", si, bi, text)
            block = {
                "id": block_id, "type": kind, "title": "", "text": text,
                "items": [], "rows": [],
                "source_refs": [{"section": si + 1, "block": bi + 1,
                                 "page": max(0, int(legacy.get("page") or section.get("page") or 0))}],
                "visual_id": "", "estimated_minutes": 0,
            }
            if kind == "checklist":
                block["items"] = [{"id": _stable_id("i", block_id, text), "text": text}]
                block["text"] = ""
            blocks.append(block)
        if blocks:
            chapters.append({
                "id": _stable_id("c", si, chapter_title), "title": chapter_title,
                "objective": "", "estimated_minutes": 0, "blocks": blocks,
            })
    if not chapters:
        raise SmartGuideError("O guia de origem não contém conteúdo utilizável.")
    return validate_document({
        "title": title or "Guia Inteligente",
        "summary": "Versão estruturada localmente a partir do guia importado.",
        "chapters": chapters,
    })


def default_progress() -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "completed": [], "favorites": [],
        "revealed_spoilers": [], "notes": {}, "checkpoint": "", "history": [],
        "session_minutes": 30, "updated_at": 0,
    }


class SmartGuideStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def _slug(slug: str) -> str:
        value = re.sub(r"[^a-zA-Z0-9_-]", "", str(slug or ""))
        if not value:
            raise SmartGuideError("Identificador de jogo inválido.")
        return value

    def directory(self, slug: str) -> Path:
        return self.root / self._slug(slug)

    def _path(self, slug: str, name: str) -> Path:
        return self.directory(slug) / name

    def set_status(self, slug: str, phase: str, **extra) -> dict:
        status = {"phase": phase, "updated_at": _now(), **extra}
        _atomic_json(self._path(slug, "status.json"), status)
        return status

    def status(self, slug: str) -> dict:
        return _read_json(self._path(slug, "status.json"), {"phase": "idle", "updated_at": 0})

    def ensure_source(self, slug: str, title: str, sections: list, metadata: dict | None = None) -> dict:
        source = {
            "schema_version": SCHEMA_VERSION, "title": _clean_text(title, 500),
            "sections": deepcopy(sections or []), "metadata": deepcopy(metadata or {}),
            "captured_at": _now(),
        }
        source_hash = _json_hash({"title": source["title"], "sections": source["sections"]})
        source["hash"] = source_hash
        archive = self._path(slug, f"sources/{source_hash}.json")
        if not archive.exists():
            _atomic_json(archive, source)
        _atomic_json(self._path(slug, "source.json"), source)
        current = self.current(slug)
        if not current or current.get("source_hash") != source_hash:
            fallback = from_legacy_sections(title, sections)
            self.publish(slug, fallback, source_hash, "local", "structured-fallback")
            self.set_status(slug, "ready", message="Versão compacta local criada; IA pode aprimorá-la.")
        return source

    def source(self, slug: str) -> dict:
        return _read_json(self._path(slug, "source.json"), {})

    def current(self, slug: str) -> dict:
        return _read_json(self._path(slug, "current.json"), {})

    def progress(self, slug: str) -> dict:
        value = _read_json(self._path(slug, "progress.json"), default_progress())
        base = default_progress()
        if isinstance(value, dict):
            base.update(value)
        return base

    def revisions(self, slug: str) -> list[dict]:
        folder = self._path(slug, "revisions")
        values = []
        for path in sorted(folder.glob("*.json"), reverse=True) if folder.exists() else []:
            data = _read_json(path, {})
            if data:
                values.append({k: data.get(k) for k in (
                    "revision_id", "created_at", "provider", "model", "source_hash", "restored_from"
                )})
        return values[:MAX_REVISIONS]

    def publish(self, slug: str, document: dict, source_hash: str,
                provider: str, model: str, restored_from: str = "") -> dict:
        clean = validate_document(document)
        created = _now()
        revision_id = f"{created}-{uuid.uuid4().hex[:8]}"
        revision = {
            **clean, "revision_id": revision_id, "created_at": created,
            "source_hash": str(source_hash or ""), "provider": str(provider or ""),
            "model": str(model or ""), "restored_from": str(restored_from or ""),
        }
        _atomic_json(self._path(slug, f"revisions/{revision_id}.json"), revision)
        _atomic_json(self._path(slug, "current.json"), revision)
        folder = self._path(slug, "revisions")
        paths = sorted(folder.glob("*.json"), reverse=True)
        for old in paths[MAX_REVISIONS:]:
            try:
                old.unlink()
            except OSError:
                pass
        return revision

    def restore(self, slug: str, revision_id: str) -> dict:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", str(revision_id or ""))
        revision = _read_json(self._path(slug, f"revisions/{safe}.json"), {})
        if not revision:
            raise SmartGuideError("Revisão não encontrada.")
        return self.publish(
            slug, revision, revision.get("source_hash", ""),
            revision.get("provider", "restored"), revision.get("model", ""), safe,
        )

    def update_progress(self, slug: str, action: str, block_id: str = "", value=None) -> dict:
        progress = self.progress(slug)
        block_id = _clean_text(block_id, 100)
        list_actions = {
            "complete": "completed", "favorite": "favorites", "reveal": "revealed_spoilers",
        }
        if action in list_actions and block_id:
            key = list_actions[action]
            items = set(progress.get(key) or [])
            if bool(value):
                items.add(block_id)
            else:
                items.discard(block_id)
            progress[key] = sorted(items)
            if action == "complete" and bool(value):
                history = progress.get("history") or []
                history.append({"block_id": block_id, "at": _now(), "action": "completed"})
                progress["history"] = history[-200:]
        elif action == "note" and block_id:
            notes = dict(progress.get("notes") or {})
            text = _clean_text(value, 5_000)
            if text:
                notes[block_id] = text
            else:
                notes.pop(block_id, None)
            progress["notes"] = notes
        elif action == "checkpoint":
            progress["checkpoint"] = block_id
        elif action == "session_minutes":
            progress["session_minutes"] = max(5, min(480, int(value or 30)))
        else:
            raise SmartGuideError("Atualização de progresso inválida.")
        progress["updated_at"] = _now()
        _atomic_json(self._path(slug, "progress.json"), progress)
        return progress

    @staticmethod
    def next_objective(document: dict, progress: dict) -> dict:
        completed = set(progress.get("completed") or [])
        checkpoint = progress.get("checkpoint") or ""
        candidates = []
        after_checkpoint = not bool(checkpoint)
        for chapter in document.get("chapters") or []:
            for block in chapter.get("blocks") or []:
                if block.get("id") == checkpoint:
                    after_checkpoint = True
                    continue
                if not after_checkpoint or block.get("id") in completed:
                    continue
                if block.get("type") in {"objective", "checklist", "checkpoint", "challenge", "missable"}:
                    candidates.append({
                        "chapter_id": chapter.get("id"), "chapter": chapter.get("title"),
                        "block_id": block.get("id"), "type": block.get("type"),
                        "title": block.get("title") or chapter.get("objective") or chapter.get("title"),
                        "text": block.get("text") or ((block.get("items") or [{}])[0].get("text", "")),
                    })
        return candidates[0] if candidates else {}

    def bundle(self, slug: str) -> dict:
        current = self.current(slug)
        progress = self.progress(slug)
        return {
            "ok": True, "source": self.source(slug), "current": current,
            "progress": progress, "status": self.status(slug),
            "revisions": self.revisions(slug),
            "next_objective": self.next_objective(current, progress) if current else {},
        }

    def export_pack(self, slug: str, include_progress: bool = True,
                    media_dir: Path | None = None) -> tuple[str, str]:
        memory = io.BytesIO()
        directory = self.directory(slug)
        if not directory.exists():
            raise SmartGuideError("Este jogo ainda não possui Guia Inteligente.")
        manifest = {"format": "digitracker-guide-pack", "version": 1, "slug": self._slug(slug)}
        with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for path in directory.rglob("*"):
                if not path.is_file() or (not include_progress and path.name == "progress.json"):
                    continue
                archive.write(path, path.relative_to(directory).as_posix())
            media_dir = Path(media_dir) if media_dir else None
            if media_dir and media_dir.exists():
                for path in media_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, f"assets/{path.relative_to(media_dir).as_posix()}")
        return f"{self._slug(slug)}.dtguide", base64.b64encode(memory.getvalue()).decode("ascii")

    def import_pack(self, slug: str, encoded: str, media_dir: Path | None = None) -> dict:
        try:
            raw = base64.b64decode((encoded or "").split(",", 1)[-1], validate=True)
        except ValueError as exc:
            raise SmartGuideError("Pacote inválido.") from exc
        if len(raw) > 100 * 1024 * 1024:
            raise SmartGuideError("Pacote maior que 100 MB.")
        directory = self.directory(slug)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, ValueError) as exc:
                raise SmartGuideError("Manifesto do pacote inválido.") from exc
            if manifest.get("format") != "digitracker-guide-pack":
                raise SmartGuideError("Formato de pacote desconhecido.")
            for info in archive.infolist():
                name = Path(info.filename.replace("\\", "/"))
                if info.is_dir() or name.is_absolute() or ".." in name.parts:
                    continue
                is_asset = name.parts[:1] == ("assets",)
                if is_asset and not media_dir:
                    continue
                base = Path(media_dir) if is_asset else directory
                relative = Path(*name.parts[1:]) if is_asset else name
                target = (base / relative).resolve()
                try:
                    target.relative_to(base.resolve())
                except ValueError:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        current = self.current(slug)
        if current:
            validate_document(current)
        return self.bundle(slug)
