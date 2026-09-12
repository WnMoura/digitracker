"""Leitura conservadora de listas de entidades, sem regras de uma franquia."""
from __future__ import annotations

import re
import unicodedata


def clean_label(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    # Footnotes remain in the original source cell, not in the entity name.
    return re.sub(r"(?:\s*(?:\*+|[†‡]+|\[\d+\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+))+$", "", text).strip()


def identity(value: object) -> str:
    text = unicodedata.normalize("NFKD", clean_label(value).casefold())
    return re.sub(r"[^a-z0-9]+", " ", text.encode("ascii", "ignore").decode()).strip()


def split_entities(value: object, *, cell: dict | None = None,
                   known_labels: list[str] | None = None) -> tuple[list[str], str]:
    """Read alternatives without turning a fusion into independent routes.

    Exact editorial names win over punctuation. List delimiters are considered
    only outside parentheses/quotes. Original text stays in the source JSON.
    """
    original = str(value or "").strip()
    label = clean_label(original)
    if not label or re.fullmatch(r"[-‐‑‒–—―]+", label):
        return [], "A célula não informa uma entidade."
    known = {identity(item): clean_label(item) for item in known_labels or [] if item}
    links = [clean_label(link.get("text")) for link in (cell or {}).get("links") or []
             if isinstance(link, dict) and clean_label(link.get("text"))]
    if identity(label) in known:
        return [known[identity(label)]], ""
    if any(identity(item) == identity(label) for item in links):
        return [label], ""
    chunks, current, depth, quote = [], [], 0, ""
    tokens = re.split(r"(\bor\b|\bou\b|\band\b|\be\b|[,;\n+&/()\[\]\"“”])", original, flags=re.I)
    ambiguous = False
    for token in tokens:
        if token in {'"', '“', '”'}:
            quote = "" if quote else token
            current.append(token)
        elif not quote and token in {"(", "["}:
            depth += 1
            current.append(token)
        elif not quote and token in {")", "]"}:
            depth = max(0, depth - 1)
            current.append(token)
        elif not quote and not depth and token.casefold() in {",", ";", "\n", "or", "ou"}:
            chunks.append("".join(current))
            current = []
        else:
            if not quote and not depth and token.casefold() in {"+", "&", "/", "and", "e"}:
                ambiguous = True
            current.append(token)
    chunks.append("".join(current))
    if ambiguous:
        return [], "A célula combina entidades; confirme se são alternativas, uma forma ou participantes simultâneos."
    labels = []
    for chunk in chunks:
        item = clean_label(chunk).strip('"“”')
        if not item or not re.search(r"\w", item):
            continue
        item = known.get(identity(item), item)
        if identity(item) not in {identity(existing) for existing in labels}:
            labels.append(item)
    return labels, "" if labels else "A célula não informa uma entidade."
