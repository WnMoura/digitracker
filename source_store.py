"""Persistência local de capturas editoriais e jobs retomáveis."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from smart_guide import _atomic_bytes, _atomic_json, _read_json
from source_document import normalize_document, to_markdown


class SourceStoreError(ValueError):
    pass


def _safe(value: object, limit: int = 160) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "", str(value or ""))[:limit]


class SourceStore:
    """Coleção independente de fontes; uma captura pode servir Guia/Atlas/Itens."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _game(self, slug: str) -> Path:
        safe = _safe(slug)
        if not safe:
            raise SourceStoreError("Jogo inválido.")
        return self.root / safe / "sources"

    def _path(self, slug: str, *parts: str) -> Path:
        base = self._game(slug).resolve()
        path = (base.joinpath(*parts)).resolve()
        if not path.is_relative_to(base):
            raise SourceStoreError("Caminho da fonte inválido.")
        return path

    def save_capture(self, slug: str, source: dict, *, raw_pages: list[dict] | None = None) -> dict:
        if not isinstance(source, dict):
            raise SourceStoreError("Fonte inválida.")
        normalized = normalize_document(source, source_id=source.get("source_id", ""), capture_id=source.get("capture_id", ""))
        source_id = _safe(normalized.get("source_id")) or "src-" + uuid.uuid4().hex[:12]
        normalized["source_id"] = source_id
        capture_id = normalized["capture_id"]
        folder = self._path(slug, source_id, "captures", capture_id)
        existing = _read_json(folder / "document.json", {})
        if existing:
            return self.get_capture(slug, source_id, capture_id)
        folder.mkdir(parents=True, exist_ok=True)
        _atomic_json(folder / "document.json", normalized)
        (folder / "document.md").write_text(to_markdown(normalized), encoding="utf-8")
        files = []
        for index, page in enumerate(raw_pages or [], 1):
            if not isinstance(page, dict) or not isinstance(page.get("html"), str):
                continue
            payload = page["html"].encode("utf-8")
            if len(payload) > 15 * 1024 * 1024:
                raise SourceStoreError("Página HTML excede 15 MB.")
            name = f"page-{int(page.get('number') or index):04d}.html"
            _atomic_bytes(folder / "raw" / name, payload)
            files.append({"page": page.get("number") or index, "name": name, "url": page.get("url", "")})
        metadata = {"source_id": source_id, "capture_id": capture_id, "title": normalized.get("title", ""),
                    "format": normalized.get("format"), "created_at": int(time.time()), "raw_pages": files}
        _atomic_json(self._path(slug, source_id, "metadata.json"), metadata)
        index = self.index(slug)
        index = [item for item in index if item.get("source_id") != source_id]
        index.append({"source_id": source_id, "capture_id": capture_id, "title": normalized.get("title", ""),
                      "requested_url": normalized.get("requested_url", ""), "status": "awaiting_source_review",
                      "stats": normalized.get("stats", {})})
        _atomic_json(self._path(slug, "index.json"), index)
        return {**metadata, "document": normalized}

    def index(self, slug: str) -> list[dict]:
        value = _read_json(self._path(slug, "index.json"), [])
        return value if isinstance(value, list) else []

    def get_capture(self, slug: str, source_id: str, capture_id: str = "") -> dict:
        source_id, capture_id = _safe(source_id), _safe(capture_id)
        if not capture_id:
            capture_id = next((item.get("capture_id") for item in self.index(slug) if item.get("source_id") == source_id), "")
        document = _read_json(self._path(slug, source_id, "captures", capture_id, "document.json"), {})
        if not document:
            raise SourceStoreError("Captura não encontrada.")
        return {"source_id": source_id, "capture_id": capture_id, "document": document,
                "markdown": (self._path(slug, source_id, "captures", capture_id, "document.md").read_text(encoding="utf-8")
                              if self._path(slug, source_id, "captures", capture_id, "document.md").exists() else to_markdown(document))}

    def create_job(self, slug: str, source_id: str, *, target: str, selection: dict | None = None) -> dict:
        job_id = "job-" + uuid.uuid4().hex
        value = {"job_id": job_id, "source_id": _safe(source_id), "target": _safe(target, 40),
                 "selection": copy.deepcopy(selection or {}), "phase": "awaiting_source_review",
                 "generation": 1, "created_at": int(time.time()), "updated_at": int(time.time()),
                 "completed": 0, "total": 0, "error": ""}
        _atomic_json(self._path(slug, "jobs", f"{job_id}.json"), value)
        return value

    def update_job(self, slug: str, job_id: str, **changes) -> dict:
        path = self._path(slug, "jobs", _safe(job_id) + ".json")
        value = _read_json(path, {})
        if not value:
            raise SourceStoreError("Job não encontrado.")
        for key in ("phase", "error", "target"):
            if key in changes: value[key] = str(changes[key] or "")[:2_000]
        for key in ("completed", "total", "generation"):
            if key in changes:
                try: value[key] = max(0, int(changes[key]))
                except (TypeError, ValueError): pass
        if "selection" in changes and isinstance(changes["selection"], dict): value["selection"] = copy.deepcopy(changes["selection"])
        value["updated_at"] = int(time.time())
        _atomic_json(path, value)
        return value

    def job(self, slug: str, job_id: str) -> dict:
        return _read_json(self._path(slug, "jobs", _safe(job_id) + ".json"), {})

