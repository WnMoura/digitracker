"""Catálogo local de itens e posse manual.

Itens de checklist continuam sendo texto de etapa; este catálogo é uma
estrutura separada e só é criado quando uma fonte documenta o item.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from pathlib import Path

from smart_guide import _atomic_json, _read_json


class ItemCatalogError(ValueError):
    pass


def _slug(value: object) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(value or ""))


def item_id(name: str, edition_key: str = "") -> str:
    normalized = re.sub(r"\s+", " ", str(name or "").strip().casefold())
    raw = f"{edition_key}|{normalized}"
    return "item-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class ItemCatalog:
    def __init__(self, root: Path): self.root = Path(root)

    def _dir(self, game: str) -> Path:
        value = _slug(game)
        if not value: raise ItemCatalogError("Jogo inválido.")
        return self.root / value / "items"

    def _catalog(self, game: str) -> Path: return self._dir(game) / "catalog.json"
    def _possession(self, game: str) -> Path: return self._dir(game) / "possession.json"

    def catalog(self, game: str) -> dict:
        value = _read_json(self._catalog(game), {"schema_version": 1, "revision": 0, "items": []})
        return value if isinstance(value, dict) else {"schema_version": 1, "revision": 0, "items": []}

    def possession(self, game: str) -> dict:
        value = _read_json(self._possession(game), {"schema_version": 1, "revision": 0, "values": {}, "receipts": {}})
        if not isinstance(value, dict):
            return {"schema_version": 1, "revision": 0, "values": {}, "receipts": {}}
        value.setdefault("values", {})
        value.setdefault("receipts", {})
        return value

    def upsert(self, game: str, name: str, *, edition_key: str = "", aliases=None, category: str = "", description: str = "", source_refs=None, item_kind: str = "other") -> dict:
        label = re.sub(r"\s+", " ", str(name or "")).strip()[:300]
        if not label: raise ItemCatalogError("Nome do item vazio.")
        data = self.catalog(game); items = list(data.get("items") or [])
        identifier = item_id(label, edition_key)
        current = next((entry for entry in items if entry.get("id") == identifier), None)
        if current is None:
            current = {"id": identifier, "number": len(items) + 1, "edition_key": edition_key[:120], "name": label,
                       "aliases": [], "category": category[:100], "description": description[:2_000], "acquisitions": [], "source_refs": [], "status": "suggested"}
            items.append(current)
        current["aliases"] = list(dict.fromkeys((current.get("aliases") or []) + [str(alias).strip()[:200] for alias in aliases or [] if str(alias).strip()]))[:30]
        for key, value in (("category", category), ("description", description), ("item_kind", item_kind)):
            if value: current[key] = str(value)[:2_000]
        current["source_refs"] = list({str(ref.get("source_id") or "") + json_key(ref): ref for ref in (current.get("source_refs") or []) + (source_refs or []) if isinstance(ref, dict)}.values())[:50]
        data.update({"schema_version": 1, "revision": int(data.get("revision") or 0) + 1, "items": items, "updated_at": int(time.time())})
        _atomic_json(self._catalog(game), data)
        return copy.deepcopy(current)

    def set_quantity(self, game: str, identifier: str, quantity: int | None, *, expected_revision: int | None = None,
                     request_id: str = "") -> dict:
        identifier = _slug(identifier)
        if not identifier or not any(item.get("id") == identifier for item in self.catalog(game).get("items") or []):
            raise ItemCatalogError("Item não encontrado no catálogo.")
        value = self.possession(game); revision = int(value.get("revision") or 0)
        request_id = _slug(request_id)[:120]
        fingerprint = json.dumps({"item_id": identifier, "quantity": quantity}, ensure_ascii=False, sort_keys=True)
        receipts = dict(value.get("receipts") or {})
        previous = receipts.get(request_id) if request_id else None
        if previous:
            if previous.get("fingerprint") != fingerprint:
                raise ItemCatalogError("request_id já foi usado com outro comando.")
            replay = copy.deepcopy(value)
            replay["idempotent"] = True
            return replay
        if expected_revision is not None and int(expected_revision) != revision:
            raise ItemCatalogError("A posse dos itens mudou. Atualize a lista e tente novamente.")
        if quantity is not None:
            try: quantity = max(0, int(quantity))
            except (TypeError, ValueError) as exc: raise ItemCatalogError("Quantidade inválida.") from exc
        values = dict(value.get("values") or {})
        # ``None`` means explicitly unknown/not informed.  It is different
        # from zero (known not to possess) and from an absent legacy key.
        values[identifier] = quantity
        next_revision = revision + 1
        value.update({"schema_version": 1, "revision": next_revision, "values": values,
                      "updated_at": int(time.time())})
        if request_id:
            receipts[request_id] = {"fingerprint": fingerprint, "item_id": identifier,
                                    "quantity": quantity, "revision": next_revision}
            value["receipts"] = dict(list(receipts.items())[-256:])
        _atomic_json(self._possession(game), value)
        result = copy.deepcopy(value)
        result["idempotent"] = False
        return result

    def list(self, game: str, query: str = "", category: str = "", possessed: str = "all") -> list[dict]:
        values = self.possession(game).get("values") or {}; hay = str(query or "").casefold()
        result = []
        for entry in self.catalog(game).get("items") or []:
            if hay and hay not in " ".join([entry.get("name", ""), *(entry.get("aliases") or []), entry.get("id", "")]).casefold(): continue
            if category and entry.get("category") != category: continue
            quantity = values.get(entry.get("id"))
            if possessed == "owned" and not quantity: continue
            if possessed == "missing" and quantity: continue
            result.append({**copy.deepcopy(entry), "quantity": quantity})
        return result

    def get(self, game: str, identifier: str) -> dict | None:
        """Return one catalog item with its current possession value."""
        key = _slug(identifier)
        if not key:
            return None
        values = self.possession(game).get("values") or {}
        for entry in self.catalog(game).get("items") or []:
            if entry.get("id") == key:
                return {**copy.deepcopy(entry), "quantity": values.get(key)}
        return None


def json_key(ref: dict) -> str:
    return "|".join(str(ref.get(key) or "") for key in ("page", "section", "block", "table_id", "row_id", "cell_id"))
