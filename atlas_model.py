"""Modelo canônico e avaliador puro do Atlas.

Este módulo não conhece Digimon nem qualquer franquia. Ele recebe regras
normalizadas pelo extrator local e devolve somente estado de avaliação. A IA
fica fora daqui: nenhum texto recebido é executado como código.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from copy import deepcopy


ENTITY_TYPES = {"creature", "item", "skill", "quest", "location", "state", "other"}
RULE_KINDS = {"transformation", "crafting", "unlock", "acquisition", "progression", "other"}
CONDITION_OPS = {"all", "any", "at_least", "sequence", "compare", "item", "event", "unknown"}
COMPARISON_OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "in"}


def _text(value: object, limit: int = 2_000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _identity(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return re.sub(r"[^a-z0-9]+", " ", text.encode("ascii", "ignore").decode()).strip()


def _refs(value: object) -> list[dict]:
    return [deepcopy(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def canonical_condition(condition: object, *, fallback_id: str = "condition") -> dict:
    """Normaliza uma árvore de condição sem perder o texto original."""
    if not isinstance(condition, dict):
        return {"id": fallback_id, "op": "unknown", "original": _text(condition),
                "reason": "Condição não estruturada", "source_refs": []}
    op = _text(condition.get("op"), 40).lower()
    if op not in CONDITION_OPS:
        op = "unknown"
    result = {"id": _text(condition.get("id"), 120) or fallback_id,
              "op": op}
    # Keep the old persisted shape stable: an absent reference list stays
    # absent, while a source-aware condition keeps its explicit references.
    # This matters when a legacy document is revalidated and compared with
    # the version already published.
    if "source_refs" in condition:
        result["source_refs"] = _refs(condition.get("source_refs"))
    if op in {"all", "any", "sequence", "at_least"}:
        children = condition.get("children")
        result["children"] = [canonical_condition(item, fallback_id=f"{result['id']}-{index + 1}")
                               for index, item in enumerate(children if isinstance(children, list) else [])]
        if op == "at_least":
            try:
                result["count"] = max(1, int(condition.get("count")))
            except (TypeError, ValueError):
                result["count"] = 1
    elif op == "compare":
        result.update({"field": _text(condition.get("field"), 120),
                       "operator": _text(condition.get("operator"), 10),
                       "value": condition.get("value"),
                       "unit": _text(condition.get("unit"), 40)})
        if result["operator"] not in COMPARISON_OPERATORS:
            result["operator"] = "unknown"
    elif op == "item":
        raw_quantity = condition.get("quantity")
        if raw_quantity in ("", None):
            quantity = None
        else:
            try:
                quantity = max(0, int(raw_quantity))
            except (TypeError, ValueError):
                quantity = None
        result.update({"item_id": _text(condition.get("item_id"), 160),
                       "item_name": _text(condition.get("item_name"), 300),
                       "action": _text(condition.get("action"), 30) or "possess",
                       "quantity": quantity,
                       "subject_id": _text(condition.get("subject_id"), 120),
                       "scope": _text(condition.get("scope"), 200)})
        if "original" in condition:
            result["original"] = _text(condition.get("original"))
        if "reason" in condition:
            result["reason"] = _text(condition.get("reason"), 500)
        if result["action"] not in {"possess", "use", "consume", "equip"}:
            result["action"] = "possess"
    elif op == "event":
        result.update({"event": _text(condition.get("event"), 300),
                       "event_id": _text(condition.get("event_id"), 120),
                       "subject_id": _text(condition.get("subject_id"), 120)})
    else:
        result.update({"original": _text(condition.get("original") or condition.get("text")),
                       "reason": _text(condition.get("reason"), 500)})
    return result


def canonical_rule(rule: object) -> dict:
    raw = rule if isinstance(rule, dict) else {}
    inputs = []
    for item in raw.get("inputs") or []:
        if not isinstance(item, dict) or not _text(item.get("entity_id")):
            continue
        inputs.append({"entity_id": _text(item.get("entity_id"), 120),
                       "role": _text(item.get("role"), 60) or "subject",
                       "quantity": item.get("quantity")})
    outputs = []
    for item in raw.get("outputs") or []:
        if not isinstance(item, dict) or not _text(item.get("entity_id")):
            continue
        outputs.append({"entity_id": _text(item.get("entity_id"), 120),
                        "role": _text(item.get("role"), 60) or "result",
                        "quantity": item.get("quantity")})
    kind = _text(raw.get("kind"), 40).lower()
    if kind not in RULE_KINDS:
        kind = "other"
    result = {"id": _text(raw.get("id"), 120), "kind": kind,
              "label": _text(raw.get("label"), 300), "inputs": inputs,
              "outputs": outputs, "condition": canonical_condition(raw.get("condition"), fallback_id="condition-root"),
              "source_refs": _refs(raw.get("source_refs"))}
    return result


def canonical_rule_identity(rule: object) -> str:
    """Identidade semântica; operadores, papéis e quantidades permanecem."""
    value = canonical_rule(rule)
    value.pop("id", None)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def requirement_identity(requirement: object) -> str:
    """Stable semantics for deduplication and progress-safe reprocessing."""
    if not isinstance(requirement, dict):
        return _identity(requirement)

    def without_provenance(value):
        if isinstance(value, dict):
            return {key: without_provenance(item) for key, item in value.items()
                    if key not in {"id", "source_refs"}}
        if isinstance(value, list):
            return [without_provenance(item) for item in value]
        return value

    condition = requirement.get("condition")
    condition = without_provenance(canonical_condition(condition)) if isinstance(condition, dict) else None
    numeric = (condition or {}).get("op") == "compare" and isinstance((condition or {}).get("value"), (int, float))
    value = {"field": _identity(requirement.get("field")), "operator": requirement.get("operator") or "=",
             "value": requirement.get("value"), "mode": requirement.get("mode") or "",
             "group": requirement.get("group") or "all", "condition": condition,
             "text": "" if numeric else _identity(requirement.get("text")),
             "scope": requirement.get("scope") or "", "location": requirement.get("location") or "",
             "applies_to": sorted(_identity(item) for item in requirement.get("applies_to") or [])}
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _bool_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _lookup(context: dict, key: str, default=None):
    current = context
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _evaluate_leaf(condition: dict, context: dict) -> dict:
    op = condition.get("op")
    cid = condition.get("id", "condition")
    manual = context.get("conditions") if isinstance(context.get("conditions"), dict) else {}
    if cid in manual:
        value = _bool_value(manual[cid])
        if value is not None:
            return {"id": cid, "state": "satisfied" if value else "unsatisfied",
                    "next_action": "" if value else condition.get("original") or condition.get("text", "")}
    if op == "item":
        item_id = condition.get("item_id") or _identity(condition.get("item_name"))
        inventory = context.get("items") if isinstance(context.get("items"), dict) else {}
        actual = inventory.get(item_id)
        if actual is None and condition.get("item_name"):
            actual = inventory.get(_identity(condition.get("item_name")))
        if condition.get("quantity") in (None, ""):
            return {"id": cid, "state": "unknown",
                    "next_action": "A fonte não informa a quantidade deste item."}
        try:
            needed = max(0, int(condition.get("quantity")))
            have = int(actual)
        except (TypeError, ValueError):
            return {"id": cid, "state": "unknown", "next_action": "Informe a quantidade do item."}
        action = condition.get("action") or "possess"
        # Possession is deliberately distinct from using/equipping/consuming.
        if action == "possess":
            ok = have >= needed
        else:
            used = context.get("used_items") if isinstance(context.get("used_items"), dict) else {}
            ok = int(used.get(item_id, 0) or 0) >= needed
        return {"id": cid, "state": "satisfied" if ok else "unsatisfied",
                "next_action": "" if ok else condition.get("original") or condition.get("item_name", "")}
    if op == "compare":
        actual = _lookup(context, str(condition.get("field") or ""))
        if actual is None:
            return {"id": cid, "state": "unknown", "next_action": "Informe o valor de " + str(condition.get("field") or "campo")}
        expected, operator = condition.get("value"), condition.get("operator")
        try:
            if operator == "=": ok = actual == expected
            elif operator == "!=": ok = actual != expected
            elif operator == ">": ok = actual > expected
            elif operator == ">=": ok = actual >= expected
            elif operator == "<": ok = actual < expected
            elif operator == "<=": ok = actual <= expected
            elif operator == "in": ok = actual in (expected if isinstance(expected, (list, tuple, set)) else [expected])
            else: return {"id": cid, "state": "unknown", "next_action": condition.get("original", "")}
        except TypeError:
            return {"id": cid, "state": "unknown", "next_action": condition.get("original", "")}
        return {"id": cid, "state": "satisfied" if ok else "unsatisfied",
                "next_action": "" if ok else condition.get("original", "")}
    if op == "event":
        events = context.get("events") if isinstance(context.get("events"), (set, list, tuple)) else []
        key = condition.get("event_id") or condition.get("event")
        ok = key in events
        return {"id": cid, "state": "satisfied" if ok else "unsatisfied",
                "next_action": "" if ok else condition.get("event", "")}
    return {"id": cid, "state": "unknown", "next_action": condition.get("original") or "Interpretação pendente."}


def evaluate_condition(condition: object, progress_context: dict | None = None) -> dict:
    context = progress_context if isinstance(progress_context, dict) else {}
    node = canonical_condition(condition)
    op = node.get("op")
    children = node.get("children") or []
    if op in {"all", "any", "sequence", "at_least"}:
        results = [evaluate_condition(child, context) for child in children]
        states = [item["state"] for item in results]
        if op == "all":
            state = "unsatisfied" if "unsatisfied" in states else ("unknown" if "unknown" in states else "satisfied")
        elif op == "any":
            state = "satisfied" if "satisfied" in states else ("unknown" if "unknown" in states else "unsatisfied")
        elif op == "at_least":
            count = int(node.get("count") or 1)
            satisfied = states.count("satisfied")
            possible = satisfied + states.count("unknown")
            state = "satisfied" if satisfied >= count else ("unsatisfied" if possible < count else "unknown")
        else:  # sequence
            state = "satisfied"
            for item in results:
                if item["state"] == "unsatisfied":
                    state = "unsatisfied"; break
                if item["state"] == "unknown":
                    state = "unknown"; break
        next_action = next((item.get("next_action") for item in results if item["state"] != "satisfied" and item.get("next_action")), "")
        return {"id": node.get("id"), "state": state, "children": results, "next_action": next_action}
    return _evaluate_leaf(node, context)


def evaluate_rule(rule: object, progress_context: dict | None = None) -> dict:
    normalized = canonical_rule(rule)
    result = evaluate_condition(normalized["condition"], progress_context or {})
    return {"rule_id": normalized.get("id", ""), "state": result["state"],
            "available": result["state"] == "satisfied", "condition": result,
            "next_action": result.get("next_action", ""),
            "source_refs": normalized.get("source_refs", [])}


def parse_item_condition(text: object, *, source_refs: list[dict] | None = None) -> dict | None:
    """Extrai apenas a forma segura 'use/possess ITEM'.

    Frases ambíguas permanecem None e devem ser exibidas como pendência pelo
    chamador; não viram uma criatura ou uma regra inventada.
    """
    original = _text(text)
    match = re.search(r"\b(use|possess|have|equip|consume)\s+(.+?)(?:\s+(?:on|to\s+evolve|for)\s+|$)", original, re.I)
    if match:
        action = {"use": "use", "possess": "possess", "have": "possess", "equip": "equip", "consume": "consume"}[match.group(1).casefold()]
        name = match.group(2)
    else:
        # Tables often put the item in a dedicated ``Item`` or ``Requires``
        # cell without a verb.  Accept only that explicit label; a free-form
        # sentence remains ambiguous and is left for source review.
        labeled = re.match(r"^(?:(?:evolution\s+)?item|requires?)\s*[:=-]?\s*(.+)$", original, re.I)
        if not labeled:
            return None
        action, name = "possess", labeled.group(1)
    name = re.sub(r"\s+", " ", name).strip(" .,:;()[]")
    # A bare item name does not prove how many units the source requires.
    # Keep ``None`` until an explicit multiplier (or singular unit) is
    # documented; the evaluator then reports ``unknown`` instead of silently
    # inventing a quantity.
    quantity = None
    quantity_match = re.match(r"^(\d+)\s*[x×]\s*(.+)$", name, re.I)
    if quantity_match:
        quantity = max(0, int(quantity_match.group(1)))
        name = quantity_match.group(2).strip(" .,:;()[]")
    elif re.match(r"^(?:a|an|one)\s+", name, re.I):
        quantity = 1
        name = re.sub(r"^(?:a|an|one)\s+", "", name, flags=re.I).strip(" .,:;()[]")
    if not name or _identity(name) in {"any", "all", "a", "an"}:
        return None
    return {"id": "", "op": "item", "item_name": name, "action": action,
            "quantity": quantity, "original": original, "source_refs": _refs(source_refs)}
