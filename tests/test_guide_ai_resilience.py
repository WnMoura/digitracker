import json

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
