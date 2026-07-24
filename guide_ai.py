"""Refinamento opcional do guia por IA — com provedor trocável.

Substitui o passo que antes era manual (jogar o FAQ numa IA e pedir o guia
estruturado). É **opcional**: sem chave, a heurística de `guide_parser` já
ordena as conquistas e monta as seções de dicas — a IA melhora a curadoria.

Provedores suportados:

  - `anthropic` — SDK oficial (`anthropic`), modelo padrão Claude Opus 4.8.
  - `gemini`    — API REST do Google AI Studio.
  - `openai`    — qualquer endpoint compatível com a API da OpenAI
                  (OpenAI, OpenRouter, DeepSeek, Ollama, LM Studio…), via
                  `base_url` configurável.

Só a CHAMADA muda entre provedores. O contrato de resposta é o mesmo para
todos, e a validação (`_apply`) é compartilhada: a IA devolve *nomes* de
conquista, que são casados com o set real do RetroAchievements pelo mesmo
algoritmo do fluxo de PDF. Nenhum provedor consegue fabricar um id.
"""
from __future__ import annotations

import json

import requests

import guide_parser

MAX_TOKENS = 16000
TIMEOUT = 300           # guias longos levam minutos

# Metadados de cada provedor para a interface montar o formulário sozinha.
PROVIDERS = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "default_model": "claude-opus-4-8",
        "key_url": "https://console.anthropic.com/settings/keys",
        "needs_base_url": False,
    },
    "gemini": {
        "label": "Google (Gemini)",
        "default_model": "gemini-2.5-pro",
        "key_url": "https://aistudio.google.com/apikey",
        "needs_base_url": False,
    },
    "openai": {
        "label": "OpenAI ou compatível",
        "default_model": "gpt-4o",
        "key_url": "https://platform.openai.com/api-keys",
        "needs_base_url": True,
        "default_base_url": "https://api.openai.com/v1",
    },
}

DEFAULT_PROVIDER = "anthropic"

# Contrato único de resposta. Cada provedor recebe a variação que entende.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {
            "type": "array",
            "description": "Nomes das conquistas na ordem em que o guia manda obtê-las.",
            "items": {"type": "string"},
        },
        "sections": {
            "type": "array",
            "description": "Seções de dicas e tutoriais extraídas do guia.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "blocks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["p", "li", "note", "boss", "step"],
                                },
                                "text": {"type": "string"},
                            },
                            "required": ["type", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "blocks"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["order", "sections"],
    "additionalProperties": False,
}

SYSTEM = """Você organiza guias de videogame para um app que acompanha conquistas \
do RetroAchievements.

Recebe o texto bruto de um guia (FAQ do GameFAQs) e a lista de conquistas do jogo.
Devolve:

1. `order`: os nomes das conquistas na ordem em que um jogador as obteria seguindo \
o guia do começo ao fim. Use EXATAMENTE os nomes da lista fornecida. Inclua só as \
conquistas que o guia permite posicionar com confiança; omita as demais.

2. `sections`: as dicas e tutoriais úteis do guia, reorganizados em seções limpas \
(mecânicas, chefes, áreas, side quests, grinding). Descarte o que não ajuda a jogar: \
índice, changelog, direitos autorais, agradecimentos, e-mails. Preserve o conteúdo \
prático — estratégias de chefe, localizações, requisitos — reescrevendo apenas para \
ficar legível. Use `boss` para estratégia de chefe, `step` para passo de walkthrough, \
`note` para avisos importantes, `li` para itens de lista e `p` para o resto.

Atenção: os nomes das conquistas foram criados pela comunidade do RetroAchievements \
e quase nunca aparecem literalmente no guia — posicione cada uma pelo que a \
DESCRIÇÃO dela exige (um lugar, um chefe, uma tarefa) e onde isso acontece no guia.

Responda em português quando o texto de origem estiver em português; caso contrário \
mantenha o idioma do guia."""

JSON_INSTRUCTION = (
    "\n\nResponda SOMENTE com um objeto JSON válido, sem cercas de código, "
    'no formato: {"order": ["nome da conquista", ...], '
    '"sections": [{"title": "...", "blocks": [{"type": "p|li|note|boss|step", '
    '"text": "..."}]}]}'
)


class GuideAIError(Exception):
    """Falha ao refinar o guia com a IA."""


# ---------------------------------------------------------------------------- #
# Configuração
# ---------------------------------------------------------------------------- #
def provider_info(provider: str) -> dict:
    info = PROVIDERS.get(provider)
    if not info:
        raise GuideAIError(f"Provedor de IA desconhecido: {provider}")
    return info


def resolve_model(provider: str, model: str = "") -> str:
    return (model or "").strip() or provider_info(provider)["default_model"]


def resolve_base_url(provider: str, base_url: str = "") -> str:
    info = provider_info(provider)
    return (base_url or "").strip().rstrip("/") or info.get("default_base_url", "")


# ---------------------------------------------------------------------------- #
# Prompt
# ---------------------------------------------------------------------------- #
def _prompt(faq_text: str, achievements_meta: dict) -> str:
    linhas = []
    for meta in achievements_meta.values():
        titulo = meta.get("title", "")
        desc = meta.get("desc", "")
        pts = meta.get("points", 0)
        linhas.append(f"- {titulo} ({pts} pts): {desc}" if desc else f"- {titulo} ({pts} pts)")
    return (
        "CONQUISTAS DO JOGO:\n" + "\n".join(linhas)
        + "\n\nTEXTO DO GUIA:\n" + faq_text
    )


def _extract_json(text: str) -> dict:
    """Lê o JSON da resposta, tolerando cercas de código que alguns modelos
    insistem em colocar mesmo quando pedimos JSON puro."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1] if "```" in raw[3:] else raw[3:]
        raw = raw.lstrip()
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise GuideAIError("A IA não devolveu JSON válido.") from exc
    if not isinstance(data, dict):
        raise GuideAIError("A IA devolveu um JSON que não é um objeto.")
    return data


# ---------------------------------------------------------------------------- #
# Provedor: Anthropic (SDK oficial)
# ---------------------------------------------------------------------------- #
def _call_anthropic(faq_text: str, meta: dict, cfg: dict) -> dict:
    try:
        import anthropic
    except ImportError as exc:      # pragma: no cover - depende do ambiente
        raise GuideAIError(
            "A biblioteca anthropic não está instalada (pip install anthropic)."
        ) from exc

    client = anthropic.Anthropic(api_key=cfg["api_key"])
    try:
        # Entrada longa (um FAQ pode ter centenas de milhares de tokens):
        # streaming evita estourar o tempo limite da requisição.
        with client.messages.stream(
            model=cfg["model"],
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
            messages=[{"role": "user", "content": _prompt(faq_text, meta)}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise GuideAIError("Chave da Anthropic inválida.") from exc
    except anthropic.RateLimitError as exc:
        raise GuideAIError("Limite de uso da API atingido. Tente mais tarde.") from exc
    except anthropic.APIStatusError as exc:
        raise GuideAIError(f"A API da Anthropic respondeu {exc.status_code}.") from exc
    except anthropic.APIConnectionError as exc:
        raise GuideAIError("Não foi possível falar com a API da Anthropic.") from exc

    if message.stop_reason == "refusal":
        raise GuideAIError("A IA recusou processar este conteúdo.")
    return _extract_json(next((b.text for b in message.content if b.type == "text"), ""))


# ---------------------------------------------------------------------------- #
# Provedor: Google Gemini (REST)
# ---------------------------------------------------------------------------- #
def _gemini_schema(schema: dict) -> dict:
    """O `responseSchema` do Gemini é um subconjunto do OpenAPI: tipos em caixa
    alta e sem `additionalProperties`."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "type" and isinstance(value, str):
            out[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            out[key] = {k: _gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = _gemini_schema(value)
        else:
            out[key] = value
    return out


def _call_gemini(faq_text: str, meta: dict, cfg: dict) -> dict:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{cfg['model']}:generateContent"
    )
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": _prompt(faq_text, meta)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(RESPONSE_SCHEMA),
            "maxOutputTokens": MAX_TOKENS,
        },
    }
    headers = {"x-goog-api-key": cfg["api_key"], "content-type": "application/json"}

    resp = _post(url, body, headers)
    if resp.status_code == 400 and "schema" in resp.text.lower():
        # Modelos mais antigos recusam o responseSchema; o mime type sozinho já
        # garante JSON e a validação é nossa de qualquer forma.
        body["generationConfig"].pop("responseSchema", None)
        resp = _post(url, body, headers)

    _raise_for_status(resp, "Gemini")
    try:
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
    except (ValueError, KeyError, IndexError) as exc:
        raise GuideAIError("Resposta inesperada do Gemini.") from exc
    return _extract_json(text)


# ---------------------------------------------------------------------------- #
# Provedor: OpenAI e compatíveis (REST)
# ---------------------------------------------------------------------------- #
def _call_openai(faq_text: str, meta: dict, cfg: dict) -> dict:
    base = cfg["base_url"] or PROVIDERS["openai"]["default_base_url"]
    body = {
        "model": cfg["model"],
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM + JSON_INSTRUCTION},
            {"role": "user", "content": _prompt(faq_text, meta)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "guide", "strict": True, "schema": RESPONSE_SCHEMA},
        },
    }
    headers = {"authorization": f"Bearer {cfg['api_key']}", "content-type": "application/json"}

    resp = _post(f"{base}/chat/completions", body, headers)
    if resp.status_code == 400 and "response_format" in resp.text.lower():
        # Endpoints compatíveis nem sempre suportam json_schema; caímos para o
        # modo JSON simples, que é praticamente universal.
        body["response_format"] = {"type": "json_object"}
        resp = _post(f"{base}/chat/completions", body, headers)

    _raise_for_status(resp, "OpenAI")
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError) as exc:
        raise GuideAIError("Resposta inesperada do provedor OpenAI-compatível.") from exc
    return _extract_json(text)


# ---------------------------------------------------------------------------- #
# HTTP compartilhado
# ---------------------------------------------------------------------------- #
def _post(url: str, body: dict, headers: dict):
    try:
        return requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise GuideAIError(f"Falha de rede ao chamar a IA: {exc}") from exc


def _raise_for_status(resp, nome: str) -> None:
    if resp.status_code == 200:
        return
    if resp.status_code in (401, 403):
        raise GuideAIError(f"Chave do {nome} inválida ou sem permissão.")
    if resp.status_code == 429:
        raise GuideAIError(f"Limite de uso do {nome} atingido. Tente mais tarde.")
    detalhe = (resp.text or "")[:200]
    raise GuideAIError(f"O {nome} respondeu {resp.status_code}: {detalhe}")


_CALLERS = {
    "anthropic": _call_anthropic,
    "gemini": _call_gemini,
    "openai": _call_openai,
}


# ---------------------------------------------------------------------------- #
# Entrada pública
# ---------------------------------------------------------------------------- #
def refine(faq_text: str, achievements_meta: dict, config: dict) -> dict:
    """Pede à IA a ordem das conquistas + as dicas curadas.

    `config` = {provider, api_key, model, base_url}. Devolve
    `{"ordered_ids", "missing_ids", "sections", ...}` — sempre passando pelo
    casamento com o set real, seja qual for o provedor.
    """
    provider = (config.get("provider") or DEFAULT_PROVIDER).strip()
    info = provider_info(provider)
    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        raise GuideAIError(f"Nenhuma chave configurada para {info['label']}.")
    if not achievements_meta:
        raise GuideAIError("Nenhuma conquista para ordenar.")

    cfg = {
        "api_key": api_key,
        "model": resolve_model(provider, config.get("model", "")),
        "base_url": resolve_base_url(provider, config.get("base_url", "")),
    }
    data = _CALLERS[provider](faq_text, achievements_meta, cfg)
    out = _apply(data, achievements_meta, faq_text)
    out["provider"] = provider
    out["model"] = cfg["model"]
    return out


def _apply(data: dict, achievements_meta: dict, faq_text: str) -> dict:
    """Converte a resposta da IA em ids reais.

    Os nomes viram um `parsed` sintético e passam pelo `order_from_guide`, que
    já sabe casar nome exato → sem sufixo → aproximado. Se a IA inventar um
    nome, ele simplesmente não casa.
    """
    nomes = [n for n in (data.get("order") or []) if isinstance(n, str)]
    parsed = {"achievements": [{"name": n, "pts": 0} for n in nomes], "sections": []}
    order = guide_parser.order_from_guide(parsed, achievements_meta, faq_text)

    sections = []
    for i, sec in enumerate(data.get("sections") or [], start=1):
        if not isinstance(sec, dict):
            continue
        blocks = [
            {"type": b.get("type", "p"), "text": b.get("text", "")}
            for b in (sec.get("blocks") or [])
            if isinstance(b, dict) and b.get("text")
        ]
        if blocks:
            sections.append({
                "num": str(len(sections) + 1),
                "title": sec.get("title") or f"Seção {i}",
                "blocks": blocks,
                "is_achievements": False,
            })

    return {
        "ordered_ids": order["ordered_ids"],
        "missing_ids": order["missing_ids"],
        "matched_by_name": order["matched_by_name"],
        "total": order["total"],
        "found": order["found"],
        "sections": sections,
        "ai_names": len(nomes),
    }
