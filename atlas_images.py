"""Regras compartilhadas para preencher imagens de entidades do Atlas."""
from __future__ import annotations

import re


def entity_query(label: str, query_override: str = "") -> str:
    value = query_override if str(query_override or "").strip() else label
    return re.sub(r"\s+", " ", str(value or "")).strip()[:300]


def valid_candidate(candidate: dict, label: str) -> bool:
    if not isinstance(candidate, dict) or not candidate.get("url"):
        return False
    hay = " ".join(str(candidate.get(key) or "") for key in ("title", "alt", "source", "url")).casefold()
    tokens = [token for token in re.findall(r"[a-z0-9]+", str(label or "").casefold()) if len(token) > 1]
    return not tokens or all(token in hay for token in tokens)


def rank_candidates(candidates: list[dict], label: str) -> list[dict]:
    def score(item):
        hay = " ".join(str(item.get(key) or "") for key in ("title", "alt", "source", "url")).casefold()
        exact = 1 if str(label or "").casefold() in hay else 0
        try: area = int(item.get("width") or 0) * int(item.get("height") or 0)
        except (TypeError, ValueError): area = 0
        return (exact, area, -len(hay))
    return sorted([dict(item) for item in candidates if valid_candidate(item, label)], key=score, reverse=True)

