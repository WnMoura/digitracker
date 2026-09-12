"""Regras compartilhadas para preencher imagens de entidades do Atlas."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from atlas_entities import clean_label, identity, split_entities


def normalize_options(value: dict | None = None) -> dict:
    raw = value if isinstance(value, dict) else {}
    context = re.sub(r"\s+", " ", str(raw.get("context") or "")).strip()[:180]
    wiki = str(raw.get("wiki") or "").strip()[:400]
    host = ""
    if wiki:
        parsed = urlparse(wiki if "://" in wiki else "https://" + wiki)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("Informe o endereço público da wiki, sem usuário ou senha.")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", host):
            raise ValueError("Informe o domínio ou URL da wiki, por exemplo wikimon.net.")
        if host.endswith((".local", ".localhost", ".internal")):
            raise ValueError("Informe o domínio público da wiki.")
    return {"context": context, "wiki": host,
            "replace_existing": bool(raw.get("replace_existing", False))}


def entity_query(label: str, query_override: str = "", *, options: dict | None = None) -> str:
    value = query_override if str(query_override or "").strip() else clean_label(label)
    settings = normalize_options(options)
    parts = [settings["context"], str(value or "").strip()]
    if settings["wiki"]:
        parts.append("site:" + settings["wiki"])
    return re.sub(r"\s+", " ", " ".join(filter(None, parts))).strip()[:600]


def _hay(candidate: dict) -> str:
    return " ".join(unquote(str(candidate.get(key) or ""))
                    for key in ("title", "alt", "source", "url"))


def _matches_context(hay: str, context: str) -> bool:
    if not context:
        return True
    words = set(identity(hay).split())
    terms = [part for part in identity(context).split() if part not in {"the", "of", "de", "do", "da", "and"}]
    if not terms or all(part in words for part in terms):
        return True
    # File names often use a game's initials, e.g. Digimon World 3 -> DW3.
    # This is evidence from the result, not permission to ignore the context.
    initials = "".join(part if part.isdigit() else part[0] for part in terms)
    return len(terms) >= 2 and len(initials) >= 3 and initials in words


def valid_candidate(candidate: dict, label: str, options: dict | None = None) -> bool:
    if not isinstance(candidate, dict) or not str(candidate.get("url") or "").startswith(("https://", "http://")):
        return False
    settings = normalize_options(options)
    hay = _hay(candidate)
    words = set(identity(hay).split())
    tokens = identity(label).split()
    if not tokens or not all(token in words for token in tokens):
        return False
    if not _matches_context(hay, settings["context"]):
        return False
    if re.search(r"\b(vs\.?|versus|gameplay|walkthrough|let'?s play|tier list)\b", hay, re.I):
        return False
    source_host = (urlparse(str(candidate.get("source") or "")).hostname or "").lower()
    image_host = (urlparse(candidate["url"]).hostname or "").lower()
    if any(host == bad or host.endswith("." + bad) for host in (source_host, image_host)
           for bad in ("youtube.com", "youtu.be", "ytimg.com", "tiktok.com")):
        return False
    wiki = settings["wiki"]
    # A CDN is allowed only with a page reference on the requested wiki.
    if wiki and not any(host == wiki or host.endswith("." + wiki) for host in (source_host, image_host)):
        return False
    return True


def rank_candidates(candidates: list[dict], label: str, options: dict | None = None) -> list[dict]:
    settings = normalize_options(options)

    def score(item):
        hay = identity(_hay(item))
        context_matches = sum(word in hay.split() for word in identity(settings["context"]).split())
        portrait = bool(re.search(r"\b(artwork|render|portrait|official|transparent)\b|\.png(?:\?|$)", _hay(item), re.I))
        try:
            width, height = int(item.get("width") or 0), int(item.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        shape = int(bool(width and height and .45 <= width / height <= 1.6))
        return (context_matches, portrait, shape, min(width * height, 4_000_000))

    unique = {}
    for item in candidates:
        if valid_candidate(item, label, settings):
            unique.setdefault(item["url"], dict(item))
    return sorted(unique.values(), key=score, reverse=True)


def searchable_label(label: str, *, known_labels: list[str] | None = None) -> bool:
    names, reason = split_entities(label, known_labels=known_labels)
    return not reason and len(names) == 1


def source_candidates(source: dict, label: str, options: dict | None = None) -> list[dict]:
    """Prefer the illustration under this entity's own editorial heading.

    The context/wiki filters still apply: a source image must not silently
    override the user's chosen edition or website.
    """
    document = source.get("structured") or {}
    metadata = document.get("metadata") or {}
    candidates = []
    for page in document.get("pages") or []:
        for element in page.get("elements") or []:
            if element.get("type") not in {"figure", "image"}:
                continue
            path = element.get("path") or []
            if not path or identity(path[-1]) != identity(label):
                continue
            url = str(element.get("src") or "")
            title = " · ".join(filter(None, [str(path[-1]), str(element.get("caption") or element.get("alt") or ""),
                                           str(metadata.get("title") or "")]))
            candidates.append({"url": url, "title": title, "source": page.get("url") or source.get("url") or "",
                               "provider": "Fonte importada", "landing_url": page.get("url") or source.get("url") or ""})
    return rank_candidates(candidates, label, options)
