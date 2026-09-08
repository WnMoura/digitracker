from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "guide_ai.py"
README = ROOT / "README.md"
TEST_FILE = ROOT / "tests" / "test_guide_ai_resilience.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: esperado 1 trecho, encontrado {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: início não encontrado")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: fim não encontrado")
    return text[:start_index] + replacement + text[end_index:]


guide = GUIDE.read_text(encoding="utf-8")

guide = replace_once(
    guide,
    "import json\nimport math\nimport re\nimport unicodedata\n",
    "import json\nimport math\nimport random\nimport re\nimport time\nimport unicodedata\n",
    "imports",
)

guide = replace_once(
    guide,
    "ATLAS_BATCH_MAX_CHARS = 18000\nATLAS_BATCH_MAX_BLOCKS = 80\n",
    "ATLAS_BATCH_MAX_CHARS = 18000\nATLAS_BATCH_MAX_BLOCKS = 80\n\n# Retentativas HTTP para APIs de IA. Absorve oscilações transitórias sem\n# transformar um 503/429 em falha imediata para o usuário.\nHTTP_MAX_RETRIES = 4\nHTTP_BACKOFF_BASE = 1.0\nHTTP_BACKOFF_CAP = 30.0\nHTTP_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})\n\n# O fallback só é ativado no Atlas quando o campo Modelo ficou vazio.\n# Uma escolha explícita do usuário nunca é trocada silenciosamente.\nGEMINI_ATLAS_FALLBACK_MODELS = (\n    \"gemini-3.7-flash\",\n    \"gemini-3.6-flash\",\n)\n",
    "constantes de resiliência",
)

guide = replace_once(
    guide,
    '"default_model": "gemini-2.5-pro",',
    '"default_model": "gemini-3.8-flash",',
    "modelo Gemini padrão",
)

gemini_replacement = '''def _gemini_request(cfg: dict, model: str, system: str, user: str, schema: dict):
    """Executa uma chamada GenerateContent para um modelo específico."""
    generation_config = {
        "responseMimeType": "application/json",
        "responseSchema": _gemini_schema(schema),
        "maxOutputTokens": MAX_TOKENS,
    }
    thinking_level = str(cfg.get("thinking_level") or "").strip().lower()
    if thinking_level in {"minimal", "low", "medium", "high"}:
        generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}

    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": generation_config,
    }
    headers = {
        "x-goog-api-key": cfg["api_key"],
        "content-type": "application/json",
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )

    for _ in range(3):
        resp = _post(url, body, headers)
        if resp.status_code != 400:
            return resp
        detail = (resp.text or "").lower()
        changed = False
        if "responseSchema" in generation_config and "schema" in detail:
            generation_config.pop("responseSchema", None)
            changed = True
        if "thinkingConfig" in generation_config and (
                "thinkingconfig" in detail
                or "thinkinglevel" in detail
                or "thinking level" in detail):
            generation_config.pop("thinkingConfig", None)
            changed = True
        if not changed:
            return resp
    return resp


def _call_gemini(cfg: dict, system: str, user: str, schema: dict) -> dict:
    models = []
    for model in [cfg["model"], *(cfg.get("fallback_models") or [])]:
        model = str(model or "").strip()
        if model and model not in models:
            models.append(model)

    for model_index, model in enumerate(models):
        resp = _gemini_request(cfg, model, system, user, schema)
        if (resp.status_code in {500, 502, 503, 504}
                and model_index < len(models) - 1):
            continue

        _raise_for_status(resp, "Gemini")
        try:
            data = resp.json()
            candidate = data["candidates"][0]
            finish_reason = str(candidate.get("finishReason") or "").upper()
            if finish_reason == "MAX_TOKENS":
                raise GuideAIError(
                    "O Gemini cortou a resposta por limite de tokens. "
                    "Tente novamente; as dicas serão processadas em lotes menores."
                )
            if finish_reason and finish_reason not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                raise GuideAIError(f"O Gemini interrompeu a resposta: {finish_reason}.")
            parts = candidate["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except GuideAIError:
            raise
        except (ValueError, KeyError, IndexError) as exc:
            raise GuideAIError("Resposta inesperada do Gemini.") from exc

        cfg["model"] = model
        return _extract_json(text)

    raise GuideAIError("O Gemini não conseguiu concluir a solicitação.")


'''

guide = replace_between(
    guide,
    "def _call_gemini(cfg: dict, system: str, user: str, schema: dict) -> dict:\n",
    "# ---------------------------------------------------------------------------- #\n# Provedor: OpenAI e compatíveis (REST)\n",
    gemini_replacement,
    "chamada Gemini",
)

http_replacement = '''def _retry_after(resp) -> float:
    """Lê Retry-After em segundos; valores inválidos são ignorados."""
    try:
        return max(0.0, float(resp.headers.get("Retry-After", "")))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _post(url: str, body: dict, headers: dict):
    """POST com exponential backoff para rede, 429 e 5xx transitórios."""
    wait = 0.0
    for attempt in range(HTTP_MAX_RETRIES + 1):
        if attempt and wait > 0:
            jitter = random.uniform(0.0, min(1.0, max(0.1, wait * .25)))
            time.sleep(min(HTTP_BACKOFF_CAP, wait + jitter))
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if attempt >= HTTP_MAX_RETRIES:
                raise GuideAIError(
                    "Não foi possível falar com o provedor de IA após novas tentativas."
                ) from exc
            wait = min(HTTP_BACKOFF_CAP, HTTP_BACKOFF_BASE * (2 ** attempt))
            continue

        if resp.status_code not in HTTP_RETRYABLE_STATUS:
            return resp
        if attempt >= HTTP_MAX_RETRIES:
            return resp

        hint = _retry_after(resp)
        wait = min(
            HTTP_BACKOFF_CAP,
            hint if hint > 0 else HTTP_BACKOFF_BASE * (2 ** attempt),
        )

    raise GuideAIError("Não foi possível completar a chamada ao provedor de IA.")


def _raise_for_status(resp, nome: str) -> None:
    if resp.status_code == 200:
        return
    if resp.status_code in (401, 403):
        raise GuideAIError(f"Chave do {nome} inválida ou sem permissão.")
    if resp.status_code == 404:
        raise GuideAIError(
            f"Modelo ou endpoint do {nome} não encontrado. Confira o id configurado."
        )
    if resp.status_code == 429:
        raise GuideAIError(
            f"Limite de uso do {nome} atingido mesmo após novas tentativas. "
            "Aguarde a cota liberar ou escolha outro modelo."
        )
    if resp.status_code in (408, 504):
        raise GuideAIError(
            f"O {nome} excedeu o tempo de resposta mesmo após novas tentativas."
        )
    if resp.status_code in (500, 502, 503):
        raise GuideAIError(
            f"O {nome} está temporariamente indisponível ou com alta demanda. "
            "O DigiTracker tentou novamente automaticamente; tente mais tarde "
            "ou escolha outro modelo."
        )
    detalhe = (resp.text or "")[:200]
    raise GuideAIError(f"O {nome} respondeu {resp.status_code}: {detalhe}")


'''

guide = replace_between(
    guide,
    "def _post(url: str, body: dict, headers: dict):\n",
    "_CALLERS = {\n",
    http_replacement,
    "HTTP compartilhado",
)

atlas_quality_replacement = r'''def _normalize_atlas_candidate_refs(batch: list[dict], candidate: dict) -> dict:
    """Corrige apenas numeração local de source_refs quando ela é inequívoca."""
    section_order = []
    section_map = {}
    for fallback_section, section in enumerate(batch, 1):
        actual_section = int(section.get("_source_section") or fallback_section)
        if actual_section not in section_map:
            section_order.append(actual_section)
            section_map[actual_section] = {
                "blocks": [],
                "page": int(section.get("page") or 0),
            }
        entry = section_map[actual_section]
        for fallback_block, block in enumerate(section.get("blocks") or [], 1):
            actual_block = int(block.get("_source_block") or fallback_block)
            if actual_block not in entry["blocks"]:
                entry["blocks"].append(actual_block)
            if not entry["page"]:
                entry["page"] = int(block.get("page") or 0)

    for ref in _atlas_refs(candidate):
        if not isinstance(ref, dict):
            continue
        try:
            section_number = int(ref.get("section") or 0)
        except (TypeError, ValueError):
            section_number = 0
        if (section_number not in section_map
                and 1 <= section_number <= len(section_order)):
            section_number = section_order[section_number - 1]
            ref["section"] = section_number

        entry = section_map.get(section_number)
        if not entry:
            continue
        try:
            block_number = int(ref.get("block") or 0)
        except (TypeError, ValueError):
            block_number = 0
        if (block_number not in entry["blocks"]
                and 1 <= block_number <= len(entry["blocks"])):
            ref["block"] = entry["blocks"][block_number - 1]
        try:
            page_number = int(ref.get("page") or 0)
        except (TypeError, ValueError):
            page_number = 0
        if page_number <= 0 and entry["page"] > 0:
            ref["page"] = entry["page"]
    return candidate


_ATLAS_TABLE_DIVIDER_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


def _atlas_is_table_divider(text: object) -> bool:
    lines = [
        line.strip() for line in str(text or "").splitlines()
        if "|" in line and line.strip()
    ]
    return bool(lines) and all(_ATLAS_TABLE_DIVIDER_RE.match(line) for line in lines)


def _atlas_has_table_data(text: object) -> bool:
    """True para bloco com dados de tabela; separadores puros não contam."""
    lines = [
        line.strip() for line in str(text or "").splitlines()
        if "|" in line and line.strip()
    ]
    if not lines:
        return False
    divider_positions = [
        index for index, line in enumerate(lines)
        if _ATLAS_TABLE_DIVIDER_RE.match(line)
    ]
    if divider_positions:
        last_divider = divider_positions[-1]
        return any(
            not _ATLAS_TABLE_DIVIDER_RE.match(line)
            for line in lines[last_divider + 1:]
        )
    return any(not _ATLAS_TABLE_DIVIDER_RE.match(line) for line in lines)


def _atlas_batch_quality(batch: list[dict], candidate: dict) -> dict:
    """Reject the common failure mode: one sample path from a large table."""
    table_blocks = set()
    for fallback_section, section in enumerate(batch, 1):
        section_number = int(section.get("_source_section") or fallback_section)
        blocks = list(section.get("blocks") or [])
        header_indexes = set()
        for index, block in enumerate(blocks):
            if not _atlas_is_table_divider(block.get("text", "")) or index <= 0:
                continue
            if "|" in str(blocks[index - 1].get("text") or ""):
                header_indexes.add(index - 1)
        for fallback_block, block in enumerate(blocks, 1):
            if fallback_block - 1 in header_indexes:
                continue
            if _atlas_has_table_data(block.get("text", "")):
                table_blocks.add((
                    section_number,
                    int(block.get("_source_block") or fallback_block),
                ))
    cited = {(int(ref.get("section") or 0), int(ref.get("block") or 0))
             for ref in _atlas_refs(candidate) if isinstance(ref, dict)}
    covered = len(table_blocks & cited)
    required = math.ceil(len(table_blocks) * .60) if len(table_blocks) >= 4 else 0
    return {
        "table_blocks": len(table_blocks), "covered_table_blocks": covered,
        "required_table_blocks": required, "complete": not required or covered >= required,
    }


'''

guide = replace_between(
    guide,
    "def _atlas_batch_quality(batch: list[dict], candidate: dict) -> dict:\n",
    "def _atlas_identity(value: object) -> str:\n",
    atlas_quality_replacement,
    "qualidade Atlas",
)

generate_system_replacement = '''def generate_system_from_source(source: dict, system_title: str, game: dict,
                                config: dict, progress=None) -> dict:
    """Extrai um sistema completo em lotes e combina os caminhos localmente."""
    provider = (config.get("provider") or DEFAULT_PROVIDER).strip()
    info = provider_info(provider)
    configured_model = str(config.get("model") or "").strip()
    cfg = {"api_key": (config.get("api_key") or "").strip(),
           "model": resolve_model(provider, config.get("model", "")),
           "base_url": resolve_base_url(provider, config.get("base_url", ""))}
    if not cfg["api_key"]:
        raise GuideAIError(f"Nenhuma chave configurada para {info['label']}.")
    if provider == "gemini":
        cfg["thinking_level"] = "high"
        if not configured_model:
            cfg["fallback_models"] = [
                model for model in GEMINI_ATLAS_FALLBACK_MODELS
                if model != cfg["model"]
            ]

    sections = [{**section, "_source_id": source.get("id", ""),
                 "_source_section": index}
                for index, section in enumerate(source.get("sections") or [], 1)]
    batches = _atlas_batches(sections)
    if not batches:
        raise GuideAIError("A fonte do Atlas não contém trechos utilizáveis.")
    system_prompt = SMART_GUIDE_SYSTEM + f"""

Esta é uma parte de uma fonte exclusiva do Atlas chamada
{json.dumps(system_title, ensure_ascii=False)}. Extraia TODOS os nós e TODAS as
relações explicitamente documentadas neste lote, não apenas uma rota, exemplo ou
linhagem representativa. Em tabelas, percorra todas as linhas e todos os destinos
informados. Inclua em cada fragmento os dois nós usados por cada relação.
Retorne um único objeto no schema do sistema, sem capítulos. Use nodes=[] e
edges=[] somente quando o lote realmente não documentar relação alguma. Cada nó,
relação e requisito deve citar seu próprio section/block/page recebido.
Em source_refs, COPIE os números escritos nos campos `section`, `block` e `page`
da entrada. Não renumere seções ou blocos a partir de 1 dentro deste lote."""
    fragments, qualities = [], []
    if progress:
        progress(0, len(batches))
    for index, batch in enumerate(batches, 1):
        raw = _CALLERS[provider](cfg, system_prompt, _smart_payload(batch, game),
                                 GUIDE_SYSTEM_SCHEMA)
        candidate = _normalize_atlas_candidate_refs(batch, _atlas_candidate(raw))
        quality = _atlas_batch_quality(batch, candidate)
        if not quality["complete"]:
            retry_prompt = system_prompt + (
                "\n\nA tentativa anterior cobriu somente "
                f"{quality['covered_table_blocks']} de {quality['table_blocks']} linhas de dados da tabela "
                "deste lote. Refaça a extração completa, linha por linha. Não resuma nem escolha "
                "um único caminho."
            )
            raw = _CALLERS[provider](cfg, retry_prompt, _smart_payload(batch, game),
                                     GUIDE_SYSTEM_SCHEMA)
            candidate = _normalize_atlas_candidate_refs(batch, _atlas_candidate(raw))
            quality = _atlas_batch_quality(batch, candidate)
            if not quality["complete"]:
                raise GuideAIError(
                    "A IA não atingiu a cobertura mínima da tabela no lote "
                    f"{index}/{len(batches)} ({quality['covered_table_blocks']} de "
                    f"{quality['table_blocks']} linhas de dados referenciadas). Nenhuma prévia parcial "
                    "foi salva. Tente um modelo mais capaz ou divida a fonte."
                )
        fragments.append(candidate)
        qualities.append(quality)
        if progress:
            progress(index, len(batches))
    table_blocks = sum(item["table_blocks"] for item in qualities)
    covered_table_blocks = sum(item["covered_table_blocks"] for item in qualities)
    if table_blocks >= 4 and covered_table_blocks < math.ceil(table_blocks * .60):
        raise GuideAIError(
            "A extração não cobriu a fonte inteira "
            f"({covered_table_blocks} de {table_blocks} linhas de dados da tabela referenciadas). "
            "Nenhuma prévia parcial foi salva."
        )
    raw = _merge_atlas_fragments(fragments, system_title)
    try:
        candidates = [raw]
        for item in candidates or []:
            if not isinstance(item, dict):
                raise smart_guide.SmartGuideError("Sistema inválido.")
            parts = list(item.get("nodes") or []) + list(item.get("edges") or [])
            parts += [r for edge in item.get("edges") or [] if isinstance(edge, dict) for r in edge.get("requirements") or []]
            if any(not isinstance(part, dict) or not smart_guide._has_source(smart_guide._source_refs(part.get("source_refs"))) for part in parts):
                raise smart_guide.SmartGuideError("Cada nó, caminho e requisito precisa citar seu próprio trecho da fonte.")
        candidates = [dict(item, origin="ai") for item in (candidates or [])]
        document = {"systems": smart_guide._validate_systems({"systems": candidates})}
        smart_guide.validate_system_references(document, sections)
    except smart_guide.SmartGuideError as exc:
        raise GuideAIError(f"A IA devolveu um Atlas inválido: {exc}") from exc
    systems = document.get("systems") or []
    if len(systems) != 1:
        raise GuideAIError("A fonte exclusiva deve produzir exatamente um sistema visual.")
    system = systems[0]
    system["title"] = str(system_title or system.get("title") or "Sistema visual")[:500]
    system["source_id"] = source.get("id", "")
    system["status"] = "suggested"
    for refs in [system.get("source_refs") or []] + [
            node.get("source_refs") or [] for node in system.get("nodes") or []] + [
            edge.get("source_refs") or [] for edge in system.get("edges") or []]:
        for ref in refs:
            ref["source_id"] = source.get("id", "")
    for edge in system.get("edges") or []:
        for requirement in edge.get("requirements") or []:
            for ref in requirement.get("source_refs") or []:
                ref["source_id"] = source.get("id", "")
    referenced_pages = {int(ref.get("page") or 0) for refs in
                        [node.get("source_refs") or [] for node in system.get("nodes") or []] +
                        [edge.get("source_refs") or [] for edge in system.get("edges") or []]
                        for ref in refs if int(ref.get("page") or 0) > 0}
    source_pages = {int(section.get("page") or 0) for section in sections
                    if int(section.get("page") or 0) > 0}
    system["_analysis"] = {
        "batches": len(batches), "nodes": len(system.get("nodes") or []),
        "edges": len(system.get("edges") or []),
        "referenced_pages": len(referenced_pages), "source_pages": len(source_pages),
        "table_blocks": table_blocks,
        "covered_table_blocks": covered_table_blocks,
        "provider": provider, "model": cfg.get("model", ""),
    }
    return system


'''

guide = replace_between(
    guide,
    "def generate_system_from_source(source: dict, system_title: str, game: dict,\n",
    "def answer_guide_question(block: dict, question: str, config: dict) -> dict:\n",
    generate_system_replacement,
    "geração do Atlas",
)

GUIDE.write_text(guide, encoding="utf-8")

readme = README.read_text(encoding="utf-8")
readme = replace_once(
    readme,
    "| **Google (Gemini)** | `gemini-2.5-pro` | via API REST do AI Studio |",
    "| **Google (Gemini)** | `gemini-3.8-flash` | via API REST do AI Studio |",
    "README modelo Gemini",
)
readme = replace_once(
    readme,
    "O campo *Modelo* aceita qualquer id (`gemini-2.5-flash`, `llama3`, …) e o\n*Endpoint* permite apontar para um servidor local. Cada provedor guarda a\n**própria chave**, então trocar de um para outro não apaga a anterior.",
    "O campo *Modelo* aceita qualquer id (`gemini-3.7-flash`, `llama3`, …) e o\n*Endpoint* permite apontar para um servidor local. Cada provedor guarda a\n**própria chave**, então trocar de um para outro não apaga a anterior. Chamadas\nREST repetem erros transitórios (429/5xx/rede) com backoff exponencial. No Atlas,\nquando o campo Modelo fica vazio, o Gemini usa raciocínio alto e pode cair para\noutro Flash somente após esgotar as tentativas do modelo principal.",
    "README resiliência",
)
README.write_text(readme, encoding="utf-8")

TEST_FILE.write_text('''import json

import guide_ai


class _Response:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_post_retries_transient_503(monkeypatch):
    calls = []
    responses = [
        _Response(503, text="busy"),
        _Response(503, text="busy"),
        _Response(200, payload={"ok": True}),
    ]

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(guide_ai.requests, "post", fake_post)
    monkeypatch.setattr(guide_ai.time, "sleep", lambda *_: None)
    monkeypatch.setattr(guide_ai.random, "uniform", lambda *_: 0.0)

    response = guide_ai._post("https://example.invalid", {}, {})
    assert response.status_code == 200
    assert len(calls) == 3


def test_gemini_falls_back_after_service_error(monkeypatch):
    used = []

    def fake_request(cfg, model, system, user, schema):
        used.append(model)
        if model == "gemini-3.8-flash":
            return _Response(503, text="busy")
        payload = {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps({"answer": "ok"})}]},
            }]
        }
        return _Response(200, payload=payload)

    monkeypatch.setattr(guide_ai, "_gemini_request", fake_request)
    cfg = {
        "api_key": "test",
        "model": "gemini-3.8-flash",
        "fallback_models": ["gemini-3.7-flash"],
    }
    result = guide_ai._call_gemini(
        cfg,
        "system",
        "user",
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )
    assert result == {"answer": "ok"}
    assert used == ["gemini-3.8-flash", "gemini-3.7-flash"]
    assert cfg["model"] == "gemini-3.7-flash"


def _atlas_candidate(refs):
    return {
        "source_refs": [],
        "nodes": [{
            "id": "a", "label": "A", "subtitle": "", "stage": "", "group": "",
            "tags": [], "attributes": [], "media_query": "", "spoiler": False,
            "source_refs": refs,
        }],
        "edges": [],
    }


def test_atlas_quality_ignores_markdown_header_and_separator():
    batch = [{
        "_source_section": 5,
        "blocks": [
            {"_source_block": 10, "text": "| Origem | Destino |"},
            {"_source_block": 11, "text": "| --- | --- |"},
            {"_source_block": 12, "text": "| A | B |"},
            {"_source_block": 13, "text": "| B | C |"},
            {"_source_block": 14, "text": "| C | D |"},
            {"_source_block": 15, "text": "| D | E |"},
        ],
    }]
    candidate = _atlas_candidate([
        {"section": 5, "block": 12, "page": 1},
        {"section": 5, "block": 13, "page": 1},
        {"section": 5, "block": 14, "page": 1},
    ])
    quality = guide_ai._atlas_batch_quality(batch, candidate)
    assert quality["table_blocks"] == 4
    assert quality["covered_table_blocks"] == 3
    assert quality["complete"] is True


def test_atlas_normalizes_local_section_and_block_numbers():
    batch = [{
        "_source_section": 5,
        "page": 2,
        "blocks": [
            {"_source_block": 12, "text": "| A | B |"},
            {"_source_block": 13, "text": "| B | C |"},
        ],
    }]
    candidate = _atlas_candidate([
        {"section": 1, "block": 1, "page": 0},
    ])
    guide_ai._normalize_atlas_candidate_refs(batch, candidate)
    assert candidate["nodes"][0]["source_refs"][0] == {
        "section": 5,
        "block": 12,
        "page": 2,
    }
''', encoding="utf-8")

print("Correções v0.10.4 aplicadas.")
