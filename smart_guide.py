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
import threading
import unicodedata
import uuid
import zipfile
from copy import deepcopy
from functools import wraps
from pathlib import Path

SCHEMA_VERSION = 3
MAX_REVISIONS = 10
MAX_WALKTHROUGH_SOURCES = 10
# A fonte continua sendo dividida em lotes pequenos para a IA. O limite maior
# vale somente para o resultado já consolidado e permite Atlas extensos, com
# grupos/filtros, sem descartar silenciosamente criaturas ou caminhos.
MAX_SYSTEM_NODES = 240
MAX_SYSTEM_EDGES = 720
ATOMIC_REPLACE_ATTEMPTS = 7
BLOCK_TYPES = {
    "text", "objective", "checklist", "warning", "missable", "achievement",
    "challenge", "table", "comparison", "image", "route", "graph", "note",
    "spoiler", "resource", "checkpoint",
}


class SmartGuideError(ValueError):
    pass


def _serialized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._write_lock:
            return method(self, *args, **kwargs)
    return guarded


def _now() -> int:
    return int(time.time())


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha1(raw).hexdigest()[:12]}"


def _json_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _replace_atomic(temp: Path, path: Path) -> None:
    """Substitui um arquivo com tolerância a bloqueios transitórios do Windows.

    O Defender/indexador pode manter o destino aberto por alguns instantes
    depois de uma leitura. ``os.replace`` é a operação correta para preservar
    a gravação atômica, mas no Windows ela retorna WinError 5/32 nesse caso.
    Retentar no mesmo diretório evita perder lotes já processados; o último
    erro continua sendo propagado para que a interface mostre diagnóstico.
    """
    last_error = None
    for attempt in range(ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(temp, path)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            transient = isinstance(exc, PermissionError) or winerror in {5, 32}
            if not transient or attempt >= ATOMIC_REPLACE_ATTEMPTS - 1:
                raise
            last_error = exc
            # Arquivos trazidos de ZIP/backup podem carregar o atributo
            # somente-leitura. Isso não resolve compartilhamento aberto, mas
            # permite que a próxima tentativa funcione sem intervenção.
            try:
                if path.exists():
                    path.chmod(0o666)
            except OSError:
                pass
            time.sleep(0.08 * (attempt + 1))
    if last_error:  # pragma: no cover - o laço sempre retorna ou lança
        raise last_error


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        _replace_atomic(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes(value)
        _replace_atomic(temp, path)
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


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _source_refs(value: object, limit: int = 20) -> list[dict]:
    refs = []
    for ref in value if isinstance(value, list) else []:
        if not isinstance(ref, dict):
            continue
        try:
            item = {
                "section": max(0, _safe_int(ref.get("section"))),
                "block": max(0, _safe_int(ref.get("block"))),
                "page": max(0, _safe_int(ref.get("page"))),
            }
            source_id = _clean_text(ref.get("source_id"), 100)
            if source_id:
                item["source_id"] = source_id
            refs.append(item)
        except (TypeError, ValueError):
            continue
    return refs[:limit]


def _has_source(refs: list[dict]) -> bool:
    return any(int(ref.get("section") or 0) > 0 and int(ref.get("block") or 0) > 0
               for ref in refs)


def _stable_system_id(prefix: str, provided: object, *parts: object) -> str:
    value = _clean_text(provided, 80)
    if re.fullmatch(rf"{prefix}_[a-f0-9]{{12}}", value):
        return value
    normalized = []
    for part in parts:
        if isinstance(part, (dict, list, tuple)):
            normalized.append(json.dumps(part, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        else:
            text = unicodedata.normalize("NFKD", str(part or "").casefold())
            normalized.append(re.sub(r"[^a-z0-9]+", " ", text.encode("ascii", "ignore").decode()).strip())
    return _stable_id(prefix, *normalized)


def _migrated_systems(document: dict) -> list[dict]:
    """Promove grafos v1 para sugestões do Atlas sem tocar no arquivo antigo."""
    systems = document.get("systems")
    if isinstance(systems, list):
        return systems
    output = []
    for suggestion in document.get("visual_suggestions") or []:
        if not isinstance(suggestion, dict) or suggestion.get("type") not in {"graph", "route"}:
            continue
        nodes = suggestion.get("nodes") or []
        edges = suggestion.get("edges") or []
        if len(nodes) < 2 or not edges:
            continue
        refs = _source_refs(suggestion.get("source_refs"))
        output.append({
            "id": suggestion.get("id"), "title": suggestion.get("title") or "Sistema visual",
            "description": suggestion.get("reason") or "Convertido de uma sugestão visual anterior.",
            "group_label": "Grupo", "layout": "layered", "origin": "migrated",
            "status": "suggested", "source_id": "legacy-main", "source_refs": refs,
            "nodes": [{
                "id": node.get("id"), "label": node.get("label"), "subtitle": "",
                "stage": "", "group": "", "tags": [], "attributes": {},
                "media_query": suggestion.get("query") or node.get("label") or "",
                "spoiler": False, "source_refs": refs,
            } for node in nodes if isinstance(node, dict)],
            "edges": [{
                "id": "", "from": edge.get("from"), "to": edge.get("to"),
                "label": edge.get("label") or "", "path_kind": "normal",
                "requirements": [], "missable": False, "spoiler": False,
                "source_refs": refs,
            } for edge in edges if isinstance(edge, dict)],
        })
    return output


def _validate_systems(document: dict) -> list[dict]:
    clean_systems = []
    system_ids = set()
    for si, raw_system in enumerate(_migrated_systems(document)[:100]):
        if not isinstance(raw_system, dict):
            continue
        title = _clean_text(raw_system.get("title") or f"Sistema {si + 1}", 500)
        system_refs = _source_refs(raw_system.get("source_refs"))
        origin = str(raw_system.get("origin") or "manual").lower()
        origin = origin if origin in {"ai", "manual", "migrated"} else "manual"
        status = str(raw_system.get("status") or ("suggested" if origin in {"ai", "migrated"} else "approved")).lower()
        status = status if status in {"suggested", "approved", "rejected"} else "suggested"
        if origin == "ai" and not _has_source(system_refs):
            raise SmartGuideError(f"Sistema visual sem referência de origem: {title}")
        system_id = _stable_system_id("sys", raw_system.get("id"), title, system_refs)
        if system_id in system_ids:
            raise SmartGuideError(f"Sistema visual duplicado: {title}")
        system_ids.add(system_id)
        raw_nodes = raw_system.get("nodes") or []
        raw_edges = raw_system.get("edges") or []
        if len(raw_nodes) > MAX_SYSTEM_NODES:
            raise SmartGuideError(f"{title} excede o limite de {MAX_SYSTEM_NODES} nós.")
        if len(raw_edges) > MAX_SYSTEM_EDGES:
            raise SmartGuideError(f"{title} excede o limite de {MAX_SYSTEM_EDGES} relações.")
        nodes, raw_to_stable, seen_raw = [], {}, set()
        for ni, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, dict):
                continue
            label = _clean_text(raw_node.get("label"), 300)
            if not label:
                continue
            raw_id = _clean_text(raw_node.get("id"), 80) or f"node-{ni}"
            if raw_id in seen_raw:
                raise SmartGuideError(f"Nó duplicado em {title}: {raw_id}")
            seen_raw.add(raw_id)
            refs = _source_refs(raw_node.get("source_refs")) or system_refs
            if origin == "ai" and not _has_source(refs):
                raise SmartGuideError(f"Nó sem referência de origem: {label}")
            node_id = _stable_system_id(
                "node", raw_node.get("id"), system_id, label,
                _clean_text(raw_node.get("stage"), 100), _clean_text(raw_node.get("group"), 100), refs,
            )
            if node_id in {node["id"] for node in nodes}:
                raise SmartGuideError(f"Nó visual duplicado em {title}: {label}")
            raw_to_stable[raw_id] = node_id
            raw_attributes = raw_node.get("attributes")
            if isinstance(raw_attributes, list):
                attributes = {
                    _clean_text(item.get("key"), 80): _clean_text(item.get("value"), 300)
                    for item in raw_attributes[:20] if isinstance(item, dict) and _clean_text(item.get("key"), 80)
                }
            else:
                attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
            nodes.append({
                "id": node_id, "label": label,
                # Derived from the normalized order so every Atlas card has a
                # compact numeric reference for review diagnostics. The
                # stable string id remains the identity across revisions.
                "card_number": len(nodes) + 1,
                "subtitle": _clean_text(raw_node.get("subtitle"), 500),
                "stage": _clean_text(raw_node.get("stage"), 100),
                "group": _clean_text(raw_node.get("group"), 150),
                "tags": [_clean_text(tag, 80) for tag in (raw_node.get("tags") or []) if _clean_text(tag, 80)][:20],
                "attributes": {_clean_text(key, 80): _clean_text(value, 300)
                               for key, value in list(attributes.items())[:20] if _clean_text(key, 80)},
                "media_query": _clean_text(raw_node.get("media_query"), 500),
                "spoiler": bool(raw_node.get("spoiler", False)),
                "source_refs": refs,
            })
        if len(nodes) < 2:
            continue
        edges, edge_ids = [], set()
        for ei, raw_edge in enumerate(raw_edges):
            if not isinstance(raw_edge, dict):
                continue
            raw_from = _clean_text(raw_edge.get("from"), 80)
            raw_to = _clean_text(raw_edge.get("to"), 80)
            from_id = raw_to_stable.get(raw_from, raw_from if raw_from in raw_to_stable.values() else "")
            to_id = raw_to_stable.get(raw_to, raw_to if raw_to in raw_to_stable.values() else "")
            if not from_id or not to_id:
                raise SmartGuideError(f"Relação aponta para nó inexistente em {title}.")
            refs = _source_refs(raw_edge.get("source_refs")) or system_refs
            if origin == "ai" and not _has_source(refs):
                raise SmartGuideError(f"Relação sem referência de origem em {title}.")
            # Two documented paths may connect the same pair of nodes.  This
            # is common for evolution/skill tables where the requirement (or
            # the path label) changes, and it must not be mistaken for the
            # same relation.  Keep the endpoint/source identity, but include
            # the relation semantics in the generated id so only an actual
            # duplicate is rejected.
            edge_label = _clean_text(raw_edge.get("label"), 300)
            edge_path_kind = _clean_text(raw_edge.get("path_kind"), 40)
            if edge_path_kind not in {"normal", "alternative", "optional"}:
                edge_path_kind = "normal"
            requirement_texts = []
            for item in raw_edge.get("requirements") or []:
                value = ((item.get("text") or item.get("label"))
                         if isinstance(item, dict) else item)
                text = _clean_text(value, 1_000)
                if text:
                    requirement_texts.append(text)
            requirement_signature = " | ".join(sorted(requirement_texts))
            edge_id = _stable_system_id(
                "edge", raw_edge.get("id"), system_id, from_id, to_id,
                edge_label, edge_path_kind, requirement_signature, refs,
            )
            if edge_id in edge_ids:
                raise SmartGuideError(f"Relação duplicada em {title}.")
            edge_ids.add(edge_id)
            requirements = []
            for ri, raw_req in enumerate(raw_edge.get("requirements") or []):
                if not isinstance(raw_req, dict):
                    raw_req = {"text": raw_req}
                text = _clean_text(raw_req.get("text") or raw_req.get("label"), 1_000)
                if not text:
                    continue
                req_refs = _source_refs(raw_req.get("source_refs")) or refs
                if origin == "ai" and not _has_source(req_refs):
                    raise SmartGuideError(f"Requisito sem referência de origem: {text}")
                requirement = {
                    "id": _stable_system_id("req", raw_req.get("id"), edge_id, ri, text, req_refs),
                    "text": text, "source_refs": req_refs,
                }
                for field in ("field", "operator", "value", "original", "group", "mode"):
                    if isinstance(raw_req, dict) and raw_req.get(field) not in (None, ""):
                        requirement[field] = _clean_text(raw_req.get(field), 300)
                requirements.append(requirement)
            edges.append({
                "id": edge_id, "from": from_id, "to": to_id,
                "label": edge_label,
                "path_kind": edge_path_kind,
                "requirements": requirements[:30],
                "missable": bool(raw_edge.get("missable", False)),
                "spoiler": bool(raw_edge.get("spoiler", False)),
                "source_refs": refs,
            })
        if not edges:
            continue
        clean_systems.append({
            "id": system_id, "title": title,
            "description": _clean_text(raw_system.get("description"), 2_000),
            "group_label": _clean_text(raw_system.get("group_label") or "Grupo", 100),
            "layout": (raw_system.get("layout") if raw_system.get("layout") in {"layered", "vertical", "radial"} else "layered"),
            "origin": origin, "status": status,
            "source_id": _clean_text(raw_system.get("source_id"), 100) or "legacy-main",
            "source_refs": system_refs,
            "nodes": nodes, "edges": edges,
        })
    return clean_systems


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
        "systems": [],
        "source_ids": sorted({_clean_text(item, 100) for item in
                              (document.get("source_ids") or []) if _clean_text(item, 100)}),
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
            refs = _source_refs(block.get("source_refs"))
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
                "achievement_id": max(0, _safe_int(block.get("achievement_id"))),
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
    clean["systems"] = _validate_systems(document)
    return clean


def validate_system_references(document: dict, sections: list) -> dict:
    """Recusa referências de IA fora da fonte que foi realmente enviada."""
    for system in document.get("systems") or []:
        if system.get("origin") != "ai":
            continue
        groups = [system.get("source_refs") or []]
        groups.extend(node.get("source_refs") or [] for node in system.get("nodes") or [])
        for edge in system.get("edges") or []:
            groups.append(edge.get("source_refs") or [])
            groups.extend(req.get("source_refs") or [] for req in edge.get("requirements") or [])
        for refs in groups:
            for ref in refs:
                section_index = int(ref.get("section") or 0) - 1
                block_index = int(ref.get("block") or 0) - 1
                if section_index < 0 or section_index >= len(sections or []):
                    raise SmartGuideError(
                        f"Referência de sistema fora da fonte: seção {section_index + 1}."
                    )
                blocks = (sections[section_index] or {}).get("blocks") or []
                if block_index < 0 or block_index >= len(blocks):
                    raise SmartGuideError(
                        f"Referência de sistema fora da fonte: bloco {block_index + 1} da seção {section_index + 1}."
                    )
    return document


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


def default_system_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "active_system": "", "goals": {},
        "completed_requirements": [], "node_media": {}, "preferences": {},
        "updated_at": 0,
    }


class SmartGuideStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._write_lock = threading.RLock()

    @staticmethod
    def _slug(slug: str) -> str:
        value = re.sub(r"[^a-zA-Z0-9_-]", "", str(slug or ""))
        if not value:
            raise SmartGuideError("Identificador de jogo inválido.")
        return value

    def directory(self, slug: str) -> Path:
        return self.root / self._slug(slug)

    def _path(self, slug: str, name: str) -> Path:
        base = self.directory(slug).resolve()
        path = (base / name).resolve()
        if not path.is_relative_to(base):
            raise SmartGuideError("Caminho fora do armazenamento do guia.")
        return path

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
            fallback["systems"] = deepcopy(current.get("systems") or [])
            self.publish(slug, fallback, source_hash, "local", "structured-fallback")
            self.set_status(slug, "ready", message="Versão compacta local criada; IA pode aprimorá-la.")
        return source

    def _source_index(self, slug: str) -> list[dict]:
        value = _read_json(self._path(slug, "walkthrough_sources/index.json"), [])
        return [item for item in value if isinstance(item, dict)]

    def walkthrough_sources(self, slug: str, include_sections: bool = False) -> list[dict]:
        """Fontes independentes usadas para consolidar a Jornada.

        O source.json legado é promovido como legacy-main somente quando ainda
        não existe uma coleção. Assim a migração não substitui nem duplica uma
        fonte importada pelo usuário.
        """
        index = self._source_index(slug)
        if not index and not self._path(slug, "walkthrough_sources/index.json").exists():
            legacy = self.source(slug)
            if legacy.get("sections"):
                item = self.add_walkthrough_source(
                    slug, legacy.get("title") or "Guia importado", "legacy",
                    legacy.get("sections") or [], legacy.get("metadata") or {},
                    source_id="legacy-main",
                )
                index = [item]
        output = []
        for summary in index:
            data = _read_json(self._path(slug, f"walkthrough_sources/{summary.get('id')}.json"), summary)
            if not include_sections:
                data.pop("sections", None)
                data.pop("text", None)
            output.append(data)
        return output

    def add_walkthrough_source(self, slug: str, title: str, kind: str,
                               sections: list, metadata: dict | None = None,
                               raw: bytes | None = None, text: str = "",
                               source_id: str = "") -> dict:
        sections = deepcopy(sections or [])
        if not sections:
            raise SmartGuideError("A fonte não contém conteúdo utilizável.")
        kind = str(kind or "text").lower()
        if kind not in {"pdf", "gamefaqs", "text", "legacy"}:
            raise SmartGuideError("Tipo de fonte não permitido.")
        digest = hashlib.sha256(raw if raw is not None else
                                json.dumps(sections, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        index = self._source_index(slug)
        duplicate = next((item for item in index if item.get("hash") == digest), None)
        if duplicate:
            return {**duplicate, "duplicate": True}
        if len(index) >= MAX_WALKTHROUGH_SOURCES:
            raise SmartGuideError(f"Cada jogo aceita até {MAX_WALKTHROUGH_SOURCES} fontes de walkthrough.")
        sid = _clean_text(source_id, 100) or _stable_id("src", digest)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", sid):
            raise SmartGuideError("Identificador de fonte inválido.")
        captured = _now()
        data = {
            "schema_version": SCHEMA_VERSION, "id": sid,
            "title": _clean_text(title or "Guia sem título", 500), "kind": kind,
            "hash": digest, "captured_at": captured, "enabled": True,
            "language": _clean_text((metadata or {}).get("language") or "", 40),
            "url": _clean_text((metadata or {}).get("url") or "", 2_000),
            "filename": _clean_text((metadata or {}).get("filename") or "", 500),
            "metadata": deepcopy(metadata or {}), "sections": sections,
            "text": str(text or "")[:5_000_000],
            "section_count": len(sections),
            "character_count": sum(len(str(block.get("text") or ""))
                                   for section in sections
                                   for block in (section.get("blocks") or [])),
        }
        if raw is not None:
            if len(raw) > 50 * 1024 * 1024:
                raise SmartGuideError("O arquivo excede o limite de 50 MB.")
            suffix = ".pdf" if kind == "pdf" else ".bin"
            raw_name = f"walkthrough_sources/files/{sid}{suffix}"
            _atomic_bytes(self._path(slug, raw_name), raw)
            data["raw_file"] = raw_name
        _atomic_json(self._path(slug, f"walkthrough_sources/{sid}.json"), data)
        summary = {key: data.get(key) for key in (
            "schema_version", "id", "title", "kind", "hash", "captured_at",
            "enabled", "language", "url", "filename", "raw_file",
            "section_count", "character_count",
        )}
        index.append(summary)
        _atomic_json(self._path(slug, "walkthrough_sources/index.json"), index)
        return summary

    def update_walkthrough_source(self, slug: str, source_id: str, **changes) -> dict:
        source_id = _clean_text(source_id, 100)
        index = self._source_index(slug)
        summary = next((item for item in index if item.get("id") == source_id), None)
        if not summary:
            raise SmartGuideError("Fonte não encontrada.")
        data_path = self._path(slug, f"walkthrough_sources/{source_id}.json")
        data = _read_json(data_path, summary)
        if "title" in changes:
            data["title"] = _clean_text(changes["title"], 500) or data.get("title")
        if "enabled" in changes:
            data["enabled"] = bool(changes["enabled"])
        _atomic_json(data_path, data)
        for key in ("title", "enabled"):
            summary[key] = data.get(key)
        _atomic_json(self._path(slug, "walkthrough_sources/index.json"), index)
        return summary

    def remove_walkthrough_source(self, slug: str, source_id: str) -> dict:
        source_id = _clean_text(source_id, 100)
        index = self._source_index(slug)
        removed = next((item for item in index if item.get("id") == source_id), None)
        if not removed:
            raise SmartGuideError("Fonte não encontrada.")
        _atomic_json(self._path(slug, "walkthrough_sources/index.json"),
                     [item for item in index if item.get("id") != source_id])
        for path in [self._path(slug, f"walkthrough_sources/{source_id}.json"),
                     self._path(slug, removed.get("raw_file") or "")]:
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass
        return {"removed": source_id}

    def set_merge_status(self, slug: str, phase: str, **extra) -> dict:
        value = {"schema_version": SCHEMA_VERSION, "phase": phase,
                 "updated_at": _now(), **extra}
        _atomic_json(self._path(slug, "merge_status.json"), value)
        return value

    def merge_status(self, slug: str) -> dict:
        return _read_json(self._path(slug, "merge_status.json"),
                          {"phase": "idle", "updated_at": 0})

    def save_merge_preview(self, slug: str, document: dict, source_ids: list[str],
                           conflicts: list[dict]) -> dict:
        clean = validate_document({**document, "source_ids": source_ids})
        preview = {"schema_version": SCHEMA_VERSION, "document": clean,
                   "source_ids": list(source_ids), "conflicts": deepcopy(conflicts),
                   "created_at": _now()}
        _atomic_json(self._path(slug, "merge_preview.json"), preview)
        return preview

    def merge_preview(self, slug: str) -> dict:
        return _read_json(self._path(slug, "merge_preview.json"), {})

    def resolve_merge_conflict(self, slug: str, conflict_id: str, choice: str) -> dict:
        preview = self.merge_preview(slug)
        conflict = next((item for item in preview.get("conflicts") or []
                         if item.get("id") == conflict_id), None)
        if not conflict:
            raise SmartGuideError("Conflito não encontrado.")
        choices = {item.get("id") for item in conflict.get("alternatives") or []}
        if choice not in choices:
            raise SmartGuideError("Alternativa de conflito inválida.")
        conflict["resolution"] = choice
        selected = next((item for item in conflict.get("alternatives") or []
                         if item.get("id") == choice), {})
        block_id = _clean_text(conflict.get("block_id"), 100)
        if block_id and selected.get("text"):
            for chapter in (preview.get("document") or {}).get("chapters") or []:
                block = next((item for item in chapter.get("blocks") or []
                              if item.get("id") == block_id), None)
                if not block:
                    continue
                block["text"] = _clean_text(selected.get("text"), 10_000)
                selected_source = _clean_text(selected.get("source_id"), 100)
                source_refs = [ref for ref in block.get("source_refs") or []
                               if ref.get("source_id") == selected_source]
                if source_refs:
                    block["source_refs"] = source_refs
                break
        _atomic_json(self._path(slug, "merge_preview.json"), preview)
        return preview

    @_serialized
    def publish_merge_preview(self, slug: str, provider: str, model: str) -> dict:
        preview = self.merge_preview(slug)
        if not preview.get("document"):
            raise SmartGuideError("Nenhuma consolidação pronta para publicar.")
        blocking = [item for item in preview.get("conflicts") or []
                    if item.get("blocking") and not item.get("resolution")]
        if blocking:
            raise SmartGuideError("Resolva os conflitos importantes antes de publicar.")
        source_hash = _json_hash(preview.get("source_ids") or [])
        document = deepcopy(preview["document"])
        document["systems"] = deepcopy(self.current(slug).get("systems") or [])
        revision = self.publish(slug, document, source_hash, provider, model)
        self.set_merge_status(slug, "published", revision_id=revision["revision_id"],
                              message="Walkthrough consolidado publicado.")
        return revision

    def add_system_source(self, slug: str, title: str, kind: str, sections: list,
                          metadata: dict | None = None, raw: bytes | None = None,
                          text: str = "", structured: dict | list | None = None,
                          markdown: str = "", raw_pages: list[dict] | None = None) -> dict:
        """Registra a fonte exclusiva de um futuro sistema do Atlas."""
        sections = deepcopy(sections or [])
        if not sections:
            raise SmartGuideError("A fonte do Atlas não contém conteúdo utilizável.")
        kind = str(kind or "text").lower()
        if kind not in {"pdf", "gamefaqs", "legacy"}:
            raise SmartGuideError("O Atlas aceita PDF ou GameFAQs.")
        if raw is not None:
            raw_for_hash = raw
        elif structured is None:
            # Preserve ids of pre-structured sources when they are reimported.
            raw_for_hash = json.dumps(sections, ensure_ascii=False,
                                      sort_keys=True).encode("utf-8")
        else:
            raw_for_hash = json.dumps(
                {"sections": sections, "structured": structured}, ensure_ascii=False,
                sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(raw_for_hash).hexdigest()
        source_id = _stable_id("atlas_src", digest, title)
        path = self._path(slug, f"system_sources/{source_id}.json")
        existing = _read_json(path, {})
        if existing:
            existing.pop("sections", None)
            existing.pop("text", None)
            existing.pop("structured", None)
            existing.pop("markdown", None)
            return {**existing, "duplicate": True}
        metadata = deepcopy(metadata or {})
        source_format = _clean_text(metadata.get("source_format") or "", 100)
        value = {
            "schema_version": SCHEMA_VERSION, "id": source_id,
            "title": _clean_text(title or "Fonte do Atlas", 500), "kind": kind,
            "hash": digest, "captured_at": _now(), "url": _clean_text(
                metadata.get("url") or "", 2_000),
            "filename": _clean_text(metadata.get("filename") or "", 500),
            "metadata": metadata, "sections": sections,
            "text": str(text or "")[:5_000_000], "system_id": "",
        }
        if source_format:
            value["source_format"] = source_format
        if structured is not None:
            value["structured"] = deepcopy(structured)
        if markdown:
            value["markdown"] = str(markdown)[:10_000_000]
        if raw is not None:
            if len(raw) > 50 * 1024 * 1024:
                raise SmartGuideError("O arquivo excede o limite de 50 MB.")
            raw_name = f"system_sources/files/{source_id}.pdf"
            _atomic_bytes(self._path(slug, raw_name), raw)
            value["raw_file"] = raw_name
        if raw_pages:
            raw_files = []
            for page in raw_pages:
                if not isinstance(page, dict):
                    continue
                number = max(1, _safe_int(page.get("number"), len(raw_files) + 1))
                html = page.get("html")
                if not isinstance(html, str) or not html:
                    continue
                if len(html.encode("utf-8")) > 15 * 1024 * 1024:
                    raise SmartGuideError("Uma página do FAQ excede o limite de 15 MB.")
                raw_name = f"system_sources/files/{source_id}-page-{number}.html"
                _atomic_bytes(self._path(slug, raw_name), html.encode("utf-8"))
                raw_files.append({"page": number, "path": raw_name,
                                  "url": _clean_text(page.get("url") or "", 2_000)})
            if raw_files:
                value["raw_files"] = raw_files
        _atomic_json(path, value)
        return {key: value.get(key) for key in (
            "schema_version", "id", "title", "kind", "hash", "captured_at",
            "url", "filename", "raw_file", "raw_files", "source_format", "system_id",
        )}

    def system_source(self, slug: str, source_id: str, include_sections: bool = False) -> dict:
        source_id = _clean_text(source_id, 100)
        value = _read_json(self._path(slug, f"system_sources/{source_id}.json"), {})
        if not include_sections:
            value.pop("sections", None)
            value.pop("text", None)
            value.pop("structured", None)
            value.pop("markdown", None)
        return value

    @_serialized
    def update_system_source(self, slug: str, source_id: str, **changes) -> dict:
        source_id = _clean_text(source_id, 100)
        path = self._path(slug, f"system_sources/{source_id}.json")
        value = _read_json(path, {})
        if not value:
            raise SmartGuideError("Fonte exclusiva do Atlas não encontrada.")
        for key in ("status", "stage", "message", "error", "error_kind",
                    "error_code", "system_id", "replace_system_id", "job_id",
                    "provider", "model", "source_format", "edition_warning"):
            if key in changes:
                limit = 2_000 if key in {"error", "message"} else 200
                value[key] = _clean_text(changes[key], limit)
        if "error_details" in changes:
            details = changes.get("error_details")
            value["error_details"] = deepcopy(details) if isinstance(details, dict) else {}
        for key in ("selection", "source_review"):
            if key in changes:
                value[key] = deepcopy(changes[key]) if isinstance(changes[key], (dict, list)) else {}
        for key in ("analysis_done", "analysis_total", "checkpoint_count"):
            if key in changes:
                value[key] = max(0, _safe_int(changes[key]))
        _atomic_json(path, value)
        return self.system_source(slug, source_id)

    def atlas_draft(self, slug: str, source_id: str) -> dict:
        return _read_json(self._path(slug, f"atlas_drafts/{source_id}.json"), {})

    def atlas_checkpoint(self, slug: str, source_id: str) -> dict:
        """Return the last complete Atlas batches for an interrupted job.

        Checkpoints are deliberately separate from drafts: a draft is user
        reviewable only after the whole source has passed validation, whereas
        a checkpoint is private worker state that may contain only a prefix of
        the source.
        """
        source_id = _clean_text(source_id, 100)
        return _read_json(self._path(slug, f"atlas_checkpoints/{source_id}.json"), {})

    @_serialized
    def save_atlas_checkpoint(self, slug: str, source_id: str, checkpoint: dict,
                              job_id: str) -> dict:
        """Atomically persist progress, but only for the active Atlas worker."""
        source_id = _clean_text(source_id, 100)
        source = self.system_source(slug, source_id)
        if not source or source.get("job_id") != job_id or source.get("status") != "running":
            raise SmartGuideError("Análise cancelada ou substituída.")
        if not isinstance(checkpoint, dict):
            raise SmartGuideError("Checkpoint do Atlas inválido.")
        value = deepcopy(checkpoint)
        value.update(source_id=source_id, job_id=_clean_text(job_id, 100), updated_at=_now())
        _atomic_json(self._path(slug, f"atlas_checkpoints/{source_id}.json"), value)
        return deepcopy(value)

    @_serialized
    def clear_atlas_checkpoint(self, slug: str, source_id: str,
                               job_id: str = "") -> bool:
        """Remove completed worker state without deleting a newer job's data."""
        source_id = _clean_text(source_id, 100)
        path = self._path(slug, f"atlas_checkpoints/{source_id}.json")
        current = _read_json(path, {})
        if job_id and current and current.get("job_id") != job_id:
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise SmartGuideError(f"Não foi possível limpar o checkpoint do Atlas: {exc}") from exc
        return True

    @_serialized
    def save_atlas_draft(self, slug: str, source_id: str, system: dict, job_id: str,
                         diagnostics: dict | None = None) -> dict:
        source = self.system_source(slug, source_id, include_sections=True)
        if not source or source.get("job_id") != job_id or source.get("status") != "running":
            raise SmartGuideError("Análise cancelada ou substituída.")
        candidate = deepcopy(system)
        candidate.update(source_id=source_id, origin="ai", status="suggested")
        normalized = _validate_systems({"systems": [candidate]})
        if len(normalized) != 1:
            raise SmartGuideError("A fonte precisa documentar ao menos dois nós e uma relação.")
        validate_system_references({"systems": normalized}, source.get("sections") or [])
        draft = {"system": normalized[0], "source_id": source_id, "job_id": job_id,
                 "created_at": _now(), "base_revision": self.current(slug).get("revision_id", ""),
                 "diagnostics": deepcopy(diagnostics or {}),
                 "approval_blocked": bool((diagnostics or {}).get("pending_table_ids")),}
        _atomic_json(self._path(slug, f"atlas_drafts/{source_id}.json"), draft)
        self.update_system_source(slug, source_id, status="suggested", error="")
        return draft

    @_serialized
    def approve_atlas_draft(self, slug: str, source_id: str, candidate: dict | None = None) -> dict:
        source = self.system_source(slug, source_id, include_sections=True)
        draft = self.atlas_draft(slug, source_id)
        if source.get("status") != "suggested" or not draft:
            raise SmartGuideError("Não há prévia pronta para aprovação.")
        if draft.get("approval_blocked"):
            raise SmartGuideError(
                "Há tabelas ou linhas pendentes de interpretação. Resolva as pendências "
                "ou exclua explicitamente essas tabelas na revisão da fonte antes de publicar."
            )
        system = deepcopy(candidate or draft["system"])
        system.update(source_id=source_id, status="approved")
        if source.get("replace_system_id"):
            system["id"] = source["replace_system_id"]
        validate_system_references({"systems": [system]}, source.get("sections") or [])
        result = self.save_system(slug, system)
        nodes = {node['id'] for node in result['system'].get('nodes') or []}
        for node_id, media_id in (draft.get('node_media') or {}).items():
            if node_id in nodes:
                self.set_system_media(slug, result['system']['id'], node_id, media_id)
        self.update_system_source(slug, source_id, status="published", system_id=result["system"]["id"])
        self.link_system_source(slug, source_id, result["system"]["id"])
        return result

    def system_sources(self, slug: str) -> list[dict]:
        folder = self._path(slug, "system_sources")
        output = []
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            value = _read_json(path, {})
            if value:
                value.pop("sections", None)
                value.pop("text", None)
                value.pop("structured", None)
                value.pop("markdown", None)
                output.append(value)
        return output

    def link_system_source(self, slug: str, source_id: str, system_id: str) -> dict:
        source_id = _clean_text(source_id, 100)
        path = self._path(slug, f"system_sources/{source_id}.json")
        value = _read_json(path, {})
        if not value:
            raise SmartGuideError("Fonte exclusiva do Atlas não encontrada.")
        value["system_id"] = _clean_text(system_id, 100)
        _atomic_json(path, value)
        return self.system_source(slug, source_id)

    def source(self, slug: str) -> dict:
        return _read_json(self._path(slug, "source.json"), {})

    @staticmethod
    def _migrate_document(value: dict) -> dict:
        """Entrega schema v2 em memória preservando metadados da revisão.

        A migração é deliberadamente somente-leitura: documentos v1 continuam
        intactos no disco até que uma nova revisão seja publicada.
        """
        if not isinstance(value, dict) or not value:
            return {}
        clean = validate_document(value)
        for key in (
            "revision_id", "created_at", "source_hash", "provider", "model",
            "restored_from",
        ):
            if key in value:
                clean[key] = value.get(key)
        return clean

    def current(self, slug: str) -> dict:
        value = _read_json(self._path(slug, "current.json"), {})
        return self._migrate_document(value)

    def progress(self, slug: str) -> dict:
        value = _read_json(self._path(slug, "progress.json"), default_progress())
        base = default_progress()
        if isinstance(value, dict):
            base.update(value)
        return base

    def system_state(self, slug: str) -> dict:
        value = _read_json(self._path(slug, "systems_state.json"), default_system_state())
        base = default_system_state()
        if isinstance(value, dict):
            base.update(value)
        base["goals"] = dict(base.get("goals") or {})
        base["completed_requirements"] = sorted({
            _clean_text(item, 100) for item in (base.get("completed_requirements") or [])
            if _clean_text(item, 100)
        })
        base["node_media"] = {
            _clean_text(key, 100): _clean_text(media_id, 100)
            for key, media_id in dict(base.get("node_media") or {}).items()
            if _clean_text(key, 100) and _clean_text(media_id, 100)
        }
        base["preferences"] = dict(base.get("preferences") or {})
        return base

    def _save_system_state(self, slug: str, state: dict) -> dict:
        state = {**default_system_state(), **dict(state or {})}
        state["updated_at"] = _now()
        _atomic_json(self._path(slug, "systems_state.json"), state)
        return state

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

    def revision(self, slug: str, revision_id: str) -> dict:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", str(revision_id or ""))
        if not safe:
            return self.current(slug)
        value = _read_json(self._path(slug, f"revisions/{safe}.json"), {})
        return self._migrate_document(value)

    @_serialized
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
        revision = self.revision(slug, safe)
        if not revision:
            raise SmartGuideError("Revisão não encontrada.")
        return self.publish(
            slug, revision, revision.get("source_hash", ""),
            revision.get("provider", "restored"), revision.get("model", ""), safe,
        )

    @_serialized
    def save_system(self, slug: str, system: dict) -> dict:
        current = self.current(slug)
        if not current:
            raise SmartGuideError("Este jogo ainda não possui Guia Inteligente.")
        candidate = deepcopy(system) if isinstance(system, dict) else {}
        candidate["origin"] = candidate.get("origin") if candidate.get("origin") in {"ai", "migrated"} else "manual"
        candidate["status"] = candidate.get("status") if candidate.get("status") in {"suggested", "approved", "rejected"} else "approved"
        # Valida o sistema isoladamente e usa seu id normalizado para substituir
        # a versão anterior sem duplicá-la.
        probe = {**current, "systems": [candidate]}
        normalized = validate_document(probe).get("systems") or []
        if not normalized:
            raise SmartGuideError("O sistema visual precisa de ao menos dois nós e uma relação.")
        saved = normalized[0]
        systems = [item for item in (current.get("systems") or []) if item.get("id") != saved["id"]]
        systems.append(saved)
        revision = self.publish(
            slug, {**current, "systems": systems}, current.get("source_hash", ""),
            current.get("provider", "manual"), current.get("model", ""),
        )
        return {"revision": revision, "system": saved}

    @_serialized
    def delete_system(self, slug: str, system_id: str) -> dict:
        current = self.current(slug)
        system_id = _clean_text(system_id, 100)
        removed = next((item for item in (current.get("systems") or []) if item.get("id") == system_id), None)
        systems = [item for item in (current.get("systems") or []) if item.get("id") != system_id]
        if len(systems) == len(current.get("systems") or []):
            raise SmartGuideError("Sistema visual não encontrado.")
        revision = self.publish(
            slug, {**current, "systems": systems}, current.get("source_hash", ""),
            current.get("provider", "manual"), current.get("model", ""),
        )
        state = self.system_state(slug)
        state["goals"].pop(system_id, None)
        if state.get("active_system") == system_id:
            state["active_system"] = ""
        removed_requirements = {
            requirement.get("id") for edge in (removed or {}).get("edges") or []
            for requirement in edge.get("requirements") or []
        }
        state["completed_requirements"] = [
            item for item in state.get("completed_requirements") or []
            if item not in removed_requirements
        ]
        state["node_media"] = {
            key: value for key, value in (state.get("node_media") or {}).items()
            if not key.startswith(f"{system_id}:")
        }
        state["preferences"].pop(system_id, None)
        self._save_system_state(slug, state)
        # Preserve the captured source and raw HTML for audit/reimport, but
        # hide completed Atlas jobs after their materialized system is deleted.
        # This is archival at the source level, not destructive file removal.
        source_folder = self._path(slug, "system_sources")
        for source_path in source_folder.glob("*.json") if source_folder.exists() else []:
            source = _read_json(source_path, {})
            if not isinstance(source, dict):
                continue
            if source.get("system_id") != system_id and source.get("replace_system_id") != system_id:
                continue
            source["status"] = "archived"
            source["stage"] = "archived"
            source["message"] = (
                "Sistema removido pelo usuário; fonte preservada para reimportação."
            )
            _atomic_json(source_path, source)
            # A draft/checkpoint is derived work, so it must not leave a stale
            # preview that can be reopened after the materialized system is gone.
            for artifact in (
                self._path(slug, f"atlas_drafts/{source_path.stem}.json"),
                self._path(slug, f"atlas_checkpoints/{source_path.stem}.json"),
            ):
                try:
                    artifact.unlink(missing_ok=True)
                except OSError:
                    pass
        return revision

    @_serialized
    def set_system_goal(self, slug: str, system_id: str, node_id: str) -> dict:
        current = self.current(slug)
        system = next((item for item in current.get("systems") or [] if item.get("id") == system_id), None)
        if not system:
            raise SmartGuideError("Sistema visual não encontrado.")
        node_id = _clean_text(node_id, 100)
        if node_id and node_id not in {item.get("id") for item in system.get("nodes") or []}:
            raise SmartGuideError("Nó do sistema não encontrado.")
        state = self.system_state(slug)
        state["active_system"] = system_id if node_id else ""
        if node_id:
            state["goals"][system_id] = node_id
        else:
            state["goals"].pop(system_id, None)
        return self._save_system_state(slug, state)

    @_serialized
    def set_system_path(self, slug: str, system_id: str, edge_id: str) -> dict:
        system = next((s for s in self.current(slug).get("systems", []) if s["id"] == system_id), None)
        if not system or edge_id not in {e["id"] for e in system["edges"]}:
            raise SmartGuideError("Caminho não encontrado.")
        state = self.system_state(slug)
        state.setdefault("preferences", {}).setdefault(system_id, {})["edge_id"] = edge_id
        return self._save_system_state(slug, state)

    @_serialized
    def update_requirement(self, slug: str, system_id: str, edge_id: str,
                           requirement_id: str, completed: bool) -> dict:
        current = self.current(slug)
        system = next((item for item in current.get("systems") or [] if item.get("id") == system_id), None)
        edge = next((item for item in (system or {}).get("edges") or [] if item.get("id") == edge_id), None)
        requirement = next((item for item in (edge or {}).get("requirements") or []
                            if item.get("id") == requirement_id), None)
        if not requirement:
            raise SmartGuideError("Requisito visual não encontrado.")
        state = self.system_state(slug)
        items = set(state.get("completed_requirements") or [])
        if completed:
            items.add(requirement_id)
        else:
            items.discard(requirement_id)
        state["completed_requirements"] = sorted(items)
        return self._save_system_state(slug, state)

    def media_system(self, slug: str, system_id: str, source_id: str = '') -> dict:
        if source_id:
            source = self.system_source(slug, source_id)
            draft = self.atlas_draft(slug, source_id)
            system = draft.get('system') or {}
            if source.get('status') != 'suggested' or draft.get('job_id') != source.get('job_id') or system.get('id') != system_id:
                raise SmartGuideError('Esta prévia foi alterada ou encerrada. Reabra a revisão do Atlas.')
            return system
        return next((s for s in self.current(slug).get('systems') or [] if s.get('id') == system_id), {})

    @_serialized
    def set_system_media(self, slug: str, system_id: str, node_id: str,
                         media_id: str, source_id: str = '') -> dict:
        system = self.media_system(slug, system_id, source_id)
        if not system or node_id not in {item.get("id") for item in system.get("nodes") or []}:
            raise SmartGuideError("Nó do sistema não encontrado.")
        if source_id:
            draft = self.atlas_draft(slug, source_id)
            draft.setdefault('node_media', {})[node_id] = _clean_text(media_id, 100)
            _atomic_json(self._path(slug, f'atlas_drafts/{source_id}.json'), draft)
            return self.system_state(slug)
        state = self.system_state(slug)
        key = f"{system_id}:{node_id}"
        media_id = _clean_text(media_id, 100)
        if media_id:
            state["node_media"][key] = media_id
        else:
            state["node_media"].pop(key, None)
        return self._save_system_state(slug, state)

    @staticmethod
    def system_objective(document: dict, state: dict) -> dict:
        system_id = state.get("active_system") or ""
        system = next((item for item in document.get("systems") or [] if item.get("id") == system_id), None)
        if not system:
            return {}
        node_id = (state.get("goals") or {}).get(system_id) or ""
        node = next((item for item in system.get("nodes") or [] if item.get("id") == node_id), None)
        if not node:
            return {}
        completed = set(state.get("completed_requirements") or [])
        incoming = [edge for edge in system.get("edges") or [] if edge.get("to") == node_id]
        # Incoming edges are alternative paths, not one giant AND condition.
        chosen = state.get("preferences", {}).get(system_id, {}).get("edge_id")
        edge = next((item for item in incoming if item.get("id") == chosen), None)
        if edge is None and incoming:
            edge = min(incoming, key=lambda item: sum(
                req.get("id") not in completed for req in item.get("requirements") or []))
        requirements = (edge or {}).get("requirements") or []
        pending = next((req for req in requirements if req.get("id") not in completed), None)
        return {
            "system_id": system_id, "system_title": system.get("title", ""),
            "node_id": node_id, "title": node.get("label", ""),
            "edge_id": (edge or {}).get("id", ""), "alternative_paths": len(incoming),
            "subtitle": node.get("subtitle", ""), "next_requirement": pending or {},
            "requirements_total": len(requirements),
            "requirements_completed": sum(1 for req in requirements if req.get("id") in completed),
        }

    @_serialized
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
        system_state = self.system_state(slug)
        return {
            "ok": True, "source": self.source(slug), "current": current,
            "progress": progress, "status": self.status(slug),
            "revisions": self.revisions(slug),
            "next_objective": self.next_objective(current, progress) if current else {},
            "system_state": system_state,
            "system_objective": self.system_objective(current, system_state) if current else {},
        }

    def export_pack(self, slug: str, include_progress: bool = True,
                    media_dir: Path | None = None) -> tuple[str, str]:
        memory = io.BytesIO()
        directory = self.directory(slug)
        if not directory.exists():
            raise SmartGuideError("Este jogo ainda não possui Guia Inteligente.")
        manifest = {"format": "digitracker-guide-pack", "version": 2, "slug": self._slug(slug)}
        with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for path in directory.rglob("*"):
                if not path.is_file() or (not include_progress and path.name in {"progress.json", "systems_state.json"}):
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
