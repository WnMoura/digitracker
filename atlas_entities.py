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


def _linked_labels(cell: dict | None, known: dict[str, str]) -> list[str]:
    """Return distinct entity names explicitly linked by the source cell.

    Editorial guides frequently add an aside after a linked entity (for
    example, a joke in an ``Evolves from`` cell).  Links are stronger evidence
    than that prose, but they must not hide a second named entity that was not
    linked.  The caller applies the conservative tail check below.
    """
    result = []
    for link in (cell or {}).get("links") or []:
        if not isinstance(link, dict):
            continue
        label = clean_label(link.get("text"))
        if not label:
            continue
        label = known.get(identity(label), label)
        if identity(label) and identity(label) not in {identity(item) for item in result}:
            result.append(label)
    return result


def _has_unlinked_named_entity(tail: str) -> bool:
    """Detect a likely second proper name in text left beside one link.

    This intentionally does not maintain a franchise dictionary.  A capital
    word in the unlinked tail is enough to keep the cell pending; lowercase
    editorial prose (including conjunctions and possessives) is safe to keep
    as a note while the linked name becomes the endpoint.
    """
    tail = re.sub(r"\s+", " ", str(tail or "")).strip()
    if not tail:
        return False
    # Delimiters are a stronger signal of an alternative/fusion than prose.
    if re.search(r"[,;|/\\+&]", tail) or re.search(r"\b(?:or|ou)\b", tail, re.I):
        return True
    words = re.findall(r"\b[A-Za-z][A-Za-z'’-]*\b", tail)
    return any(word[:1].isupper() for word in words)


def _has_explicit_alternative_delimiter(value: str) -> bool:
    """Return true only for punctuation/prose that explicitly means OR.

    Hyperlinks are editorial navigation, not semantic evidence.  A cell with
    two links joined by ``+``/``and`` may describe a fusion and must therefore
    remain pending.  Commas/semicolons/pipes and the words ``or``/``ou`` are
    the conservative alternatives used by the structured GameFAQs parser.
    Delimiters inside parentheses or quoted names are ignored.
    """
    text = str(value or "")
    comma_flags = _comma_alternative_flags(text)
    comma_index = 0
    depth = 0
    quote = ""
    for char in text:
        if char in {'"', "“", "”"}:
            quote = "" if quote else char
            continue
        if quote:
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and char in ",;|":
            if char == ",":
                explicit = comma_flags[comma_index] if comma_index < len(comma_flags) else False
                comma_index += 1
                if not explicit:
                    continue
            return True
    return bool(re.search(r"\b(?:or|ou)\b", text, re.I))


def _comma_alternative_flags(value: str) -> list[bool]:
    """Classify top-level commas without treating ``Name, the ...`` as a list."""
    text = str(value or "")
    flags = []
    depth = 0
    quote = ""
    for index, char in enumerate(text):
        if char in {'"', "“", "”"}:
            quote = "" if quote else char
            continue
        if quote:
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            cursor = index + 1
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            next_char = text[cursor] if cursor < len(text) else ""
            flags.append(next_char.isupper() or next_char.isdigit())
    return flags


def split_entities(value: object, *, cell: dict | None = None,
                   known_labels: list[str] | None = None,
                   endpoint_semantics: str = "") -> tuple[list[str], str]:
    """Read alternatives without turning a fusion into independent routes.

    Exact editorial names win over punctuation. List delimiters are considered
    only outside parentheses/quotes. Original text stays in the source JSON.
    """
    original = str(value or "").strip()
    semantics = str(endpoint_semantics or "").strip().casefold()
    label = clean_label(original)
    if not label or re.fullmatch(r"[-‐‑‒–—―]+", label):
        return [], "A célula não informa uma entidade."
    known = {identity(item): clean_label(item) for item in known_labels or [] if item}
    links = _linked_labels(cell, known)
    if identity(label) in known:
        return [known[identity(label)]], ""
    if any(identity(item) == identity(label) for item in links):
        return [label], ""
    if links:
        # Links identify editorial entities, but do not by themselves tell us
        # whether the row means alternatives or a simultaneous combination.
        # Only a visible OR/list delimiter is sufficient to split them.  This
        # prevents ``Knight + Dragon`` with two hyperlinks from becoming two
        # independent routes.
        if len(links) > 1:
            if semantics == "fusion":
                return [], "A célula combina entidades; a fonte marcou uma combinação simultânea."
            if semantics == "alternatives" or _has_explicit_alternative_delimiter(original):
                return links, ""
            return [], "A célula combina entidades; confirme se são alternativas, uma forma ou participantes simultâneos."
        # A single link may be followed by an editorial aside.  Use the link
        # as the endpoint only when the remaining text has no second proper
        # name or list delimiter.  The original cell remains in the source
        # JSON and is reachable through its stable row reference.
        linked = links[0]
        remainder = re.sub(re.escape(linked), " ", original, count=1, flags=re.I)
        if not _has_unlinked_named_entity(remainder):
            return [linked], ""
    chunks, current, depth, quote = [], [], 0, ""
    tokens = re.split(r"(\bor\b|\bou\b|\band\b|\be\b|[,;\n+&/()\[\]\"“”])", original, flags=re.I)
    comma_flags = _comma_alternative_flags(original)
    comma_index = 0
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
            if token == ",":
                explicit = comma_flags[comma_index] if comma_index < len(comma_flags) else False
                comma_index += 1
                if not explicit:
                    current.append(token)
                    continue
            chunks.append("".join(current))
            current = []
        else:
            if not quote and not depth and token.casefold() in {"+", "&", "/", "and", "e"}:
                ambiguous = True
            current.append(token)
    chunks.append("".join(current))
    if ambiguous and semantics != "alternatives":
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
