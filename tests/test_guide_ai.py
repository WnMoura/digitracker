"""Testes do refinamento de guia por IA.

A chamada à API é substituída — o que importa testar aqui é a **validação da
resposta**: a IA sugere nomes de conquista, e nada do que ela devolve pode virar
id sem passar pelo casamento com o set real.

Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import json as json_mod
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guide_ai  # noqa: E402

META = {
    "1": {"title": "Extravagant Petals", "desc": "Complete Humid Cave.", "points": 10},
    "2": {"title": "Tusks of Ash", "desc": "Defeat Mammothmon.", "points": 25},
    "3": {"title": "Death Valley, Twice Over", "desc": "Clear on Hard.", "points": 50},
}

FAQ = """
Death Valley
============
Comece pegando Extravagant Petals na Humid Cave.
Depois derrote Mammothmon para Tusks of Ash.
Por fim, Death Valley, Twice Over no modo Hard.
"""


def resposta(order, sections=None):
    return {"order": order, "sections": sections or []}


class TestAplicarResposta:
    def test_converte_nomes_em_ids_reais(self):
        out = guide_ai._apply(resposta(["Tusks of Ash", "Extravagant Petals"]), META, FAQ)
        assert out["ordered_ids"][:2] == [2, 1]

    def test_nome_inventado_pela_ia_nao_vira_id(self):
        """A IA não pode criar conquista que não existe no set do RA."""
        out = guide_ai._apply(resposta(["Conquista Que Nao Existe"]), META, FAQ)
        assert out["matched_by_name"] == 0

    def test_conquista_nao_citada_pela_ia_nao_some(self):
        """O que a IA omitir cai no fallback por posição ou em missing."""
        out = guide_ai._apply(resposta(["Extravagant Petals"]), META, FAQ)
        assert len(out["ordered_ids"]) + len(out["missing_ids"]) == 3

    def test_casa_nome_com_grafia_ligeiramente_diferente(self):
        out = guide_ai._apply(resposta(["Extravagant Petal"]), META, FAQ)
        assert 1 in out["ordered_ids"]

    def test_ignora_entradas_que_nao_sao_texto(self):
        out = guide_ai._apply({"order": ["Tusks of Ash", 42, None], "sections": []}, META, FAQ)
        assert 2 in out["ordered_ids"]

    def test_ordem_ausente_nao_quebra(self):
        out = guide_ai._apply({}, META, FAQ)
        assert out["matched_by_name"] == 0


class TestSecoes:
    def test_monta_as_secoes_no_formato_do_app(self):
        out = guide_ai._apply(resposta([], [
            {"title": "Chefes", "blocks": [{"type": "boss", "text": "Mammothmon: use fogo."}]}
        ]), META, FAQ)
        sec = out["sections"][0]
        assert set(sec) >= {"num", "title", "blocks", "is_achievements"}
        assert sec["blocks"][0]["type"] == "boss"

    def test_numera_as_secoes_em_sequencia(self):
        out = guide_ai._apply(resposta([], [
            {"title": "A", "blocks": [{"type": "p", "text": "x"}]},
            {"title": "B", "blocks": [{"type": "p", "text": "y"}]},
        ]), META, FAQ)
        assert [s["num"] for s in out["sections"]] == ["1", "2"]

    def test_descarta_secao_sem_conteudo(self):
        out = guide_ai._apply(resposta([], [
            {"title": "Vazia", "blocks": []},
            {"title": "Cheia", "blocks": [{"type": "p", "text": "algo"}]},
        ]), META, FAQ)
        assert [s["title"] for s in out["sections"]] == ["Cheia"]

    def test_descarta_bloco_sem_texto(self):
        out = guide_ai._apply(resposta([], [
            {"title": "X", "blocks": [{"type": "p", "text": ""}, {"type": "p", "text": "ok"}]},
        ]), META, FAQ)
        assert len(out["sections"][0]["blocks"]) == 1

    def test_secao_sem_titulo_ganha_um(self):
        out = guide_ai._apply(resposta([], [
            {"blocks": [{"type": "p", "text": "algo"}]},
        ]), META, FAQ)
        assert out["sections"][0]["title"]


class TestPreCondicoes:
    def test_sem_chave_avisa(self):
        with pytest.raises(guide_ai.GuideAIError, match="chave"):
            guide_ai.refine(FAQ, META, {"provider": "anthropic", "api_key": ""})

    def test_sem_conquistas_avisa(self):
        with pytest.raises(guide_ai.GuideAIError, match="Nenhuma conquista"):
            guide_ai.refine(FAQ, {}, {"provider": "anthropic", "api_key": "sk-teste"})

    def test_provedor_desconhecido_avisa(self):
        with pytest.raises(guide_ai.GuideAIError, match="desconhecido"):
            guide_ai.refine(FAQ, META, {"provider": "skynet", "api_key": "x"})


class FakeResp:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or (json_mod.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("sem json")
        return self._payload


def gemini_ok(conteudo):
    return FakeResp({"candidates": [{"content": {"parts": [{"text": conteudo}]}}]})


def openai_ok(conteudo):
    return FakeResp({"choices": [{"message": {"content": conteudo}}]})


RESPOSTA_IA = json_mod.dumps({
    "order": ["Tusks of Ash", "Extravagant Petals"],
    "sections": [{"title": "Chefes", "blocks": [{"type": "boss", "text": "Use fogo."}]}],
})


def com_respostas(monkeypatch, *respostas):
    """Substitui `requests.post` por uma sequência de respostas."""
    chamadas = []
    fila = list(respostas)

    def fake_post(url, json=None, headers=None, timeout=None):
        chamadas.append({"url": url, "body": json, "headers": headers})
        return fila.pop(0) if len(fila) > 1 else fila[0]

    monkeypatch.setattr(guide_ai.requests, "post", fake_post)
    return chamadas


# ---- IA sobre as dicas já parseadas (refinar / traduzir) ------------------- #
SECOES = [{"num": "1", "title": "Bosses", "blocks": [{"type": "boss", "text": "Use fire."}]}]
SECOES_IA = json_mod.dumps({"sections": [
    {"title": "Chefes", "blocks": [{"type": "boss", "text": "Use fogo."}]}]})


class TestDicasIA:
    def test_apply_sections_vira_formato_do_app(self):
        data = {"sections": [{"title": "T", "blocks": [{"type": "boss", "text": "x"}]}]}
        out = guide_ai._apply_sections(data, SECOES)
        assert out[0]["num"] == "1" and out[0]["title"] == "T"
        assert out[0]["blocks"][0] == {"type": "boss", "text": "x"}
        assert out[0]["is_achievements"] is False

    @pytest.mark.parametrize("data", [{"sections": []}, {"nada": 1}])
    def test_apply_sections_invalido_falha_explicitamente(self, data):
        with pytest.raises(guide_ai.GuideAIError, match="originais"):
            guide_ai._apply_sections(data, SECOES)

    def test_apply_sections_nao_deixa_ia_mudar_tipo(self):
        data = {"sections": [{"title": "T", "blocks": [{"type": "xyz", "text": "y"}]}]}
        with pytest.raises(guide_ai.GuideAIError, match="estrutura"):
            guide_ai._apply_sections(data, SECOES)

    def test_apply_sections_preserva_metadados_locais(self):
        source = [{"num": "9", "title": "T", "extra": 1,
                   "blocks": [{"type": "step", "text": "old", "n": 7}]}]
        data = {"sections": [{"title": "Novo", "blocks": [{"type": "step", "text": "new"}]}]}
        out = guide_ai._apply_sections(data, source)
        assert out[0]["extra"] == 1 and out[0]["blocks"][0]["n"] == 7

    def test_refine_tips_pelo_gemini(self, monkeypatch):
        com_respostas(monkeypatch, gemini_ok(SECOES_IA))
        out = guide_ai.refine_tips(SECOES, {"provider": "gemini", "api_key": "k"})
        assert out[0]["title"] == "Chefes"
        assert out[0]["blocks"][0]["text"] == "Use fogo."

    def test_translate_pelo_gemini(self, monkeypatch):
        chamadas = com_respostas(monkeypatch, gemini_ok(SECOES_IA))
        out = guide_ai.translate(SECOES, {"provider": "gemini", "api_key": "k"})
        assert out[0]["blocks"][0]["text"] == "Use fogo."
        assert "portug" in chamadas[0]["body"]["systemInstruction"]["parts"][0]["text"].lower()

    def test_guia_grande_e_dividido_em_lotes_com_progresso(self, monkeypatch):
        sections = [
            {"num": str(i), "title": f"S{i}",
             "blocks": [{"type": "p", "text": "x" * 10000}]}
            for i in range(3)
        ]
        responses = [gemini_ok(json_mod.dumps({"sections": [{
            "title": f"Traduzida {i}",
            "blocks": [{"type": "p", "text": "y" * 10000}],
        }]})) for i in range(3)]
        calls = com_respostas(monkeypatch, *responses)
        progress = []
        out = guide_ai.translate(
            sections, {"provider": "gemini", "api_key": "k"},
            progress=lambda done, total: progress.append((done, total)),
        )
        assert len(calls) == 3
        assert progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
        assert [s["title"] for s in out] == ["Traduzida 0", "Traduzida 1", "Traduzida 2"]

    def test_gemini_avisa_quando_resposta_e_cortada(self, monkeypatch):
        response = FakeResp({"candidates": [{
            "finishReason": "MAX_TOKENS",
            "content": {"parts": [{"text": "{incompleto"}]},
        }]})
        com_respostas(monkeypatch, response)
        with pytest.raises(guide_ai.GuideAIError, match="limite de tokens"):
            guide_ai.translate(SECOES, {"provider": "gemini", "api_key": "k"})

    def test_sem_chave_avisa(self):
        with pytest.raises(guide_ai.GuideAIError, match="chave"):
            guide_ai.refine_tips(SECOES, {"provider": "anthropic", "api_key": ""})

    def test_sem_secoes_avisa(self):
        with pytest.raises(guide_ai.GuideAIError, match="dicas"):
            guide_ai.translate([], {"provider": "anthropic", "api_key": "k"})


class TestGemini:
    def test_refina_pelo_gemini(self, monkeypatch):
        com_respostas(monkeypatch, gemini_ok(RESPOSTA_IA))
        out = guide_ai.refine(FAQ, META, {"provider": "gemini", "api_key": "k"})
        assert out["ordered_ids"][:2] == [2, 1]
        assert out["provider"] == "gemini"

    def test_manda_a_chave_no_header(self, monkeypatch):
        chamadas = com_respostas(monkeypatch, gemini_ok(RESPOSTA_IA))
        guide_ai.refine(FAQ, META, {"provider": "gemini", "api_key": "minha-chave"})
        assert chamadas[0]["headers"]["x-goog-api-key"] == "minha-chave"

    def test_usa_o_modelo_configurado_na_url(self, monkeypatch):
        chamadas = com_respostas(monkeypatch, gemini_ok(RESPOSTA_IA))
        guide_ai.refine(FAQ, META, {"provider": "gemini", "api_key": "k",
                                    "model": "gemini-2.5-flash"})
        assert "gemini-2.5-flash:generateContent" in chamadas[0]["url"]

    def test_pede_json(self, monkeypatch):
        chamadas = com_respostas(monkeypatch, gemini_ok(RESPOSTA_IA))
        guide_ai.refine(FAQ, META, {"provider": "gemini", "api_key": "k"})
        assert chamadas[0]["body"]["generationConfig"]["responseMimeType"] == "application/json"

    def test_repete_sem_schema_se_o_modelo_recusar(self, monkeypatch):
        """Modelos antigos rejeitam responseSchema; o mime type já garante JSON."""
        chamadas = com_respostas(
            monkeypatch,
            FakeResp(status_code=400, text="Invalid JSON payload: unknown responseSchema"),
            gemini_ok(RESPOSTA_IA),
        )
        out = guide_ai.refine(FAQ, META, {"provider": "gemini", "api_key": "k"})
        assert len(chamadas) == 2
        assert "responseSchema" not in chamadas[1]["body"]["generationConfig"]
        assert out["matched_by_name"] == 2

    def test_schema_convertido_para_o_formato_do_gemini(self):
        s = guide_ai._gemini_schema(guide_ai.RESPONSE_SCHEMA)
        assert s["type"] == "OBJECT"
        assert "additionalProperties" not in s
        assert s["properties"]["order"]["type"] == "ARRAY"

    def test_resposta_estranha_avisa(self, monkeypatch):
        com_respostas(monkeypatch, FakeResp({"nada": 1}))
        with pytest.raises(guide_ai.GuideAIError, match="inesperada"):
            guide_ai.refine(FAQ, META, {"provider": "gemini", "api_key": "k"})


class TestOpenAICompativel:
    def test_refina_pela_openai(self, monkeypatch):
        com_respostas(monkeypatch, openai_ok(RESPOSTA_IA))
        out = guide_ai.refine(FAQ, META, {"provider": "openai", "api_key": "k"})
        assert out["matched_by_name"] == 2

    def test_usa_endpoint_proprio(self, monkeypatch):
        """Permite OpenRouter, Ollama, LM Studio…"""
        chamadas = com_respostas(monkeypatch, openai_ok(RESPOSTA_IA))
        guide_ai.refine(FAQ, META, {"provider": "openai", "api_key": "k",
                                    "base_url": "http://localhost:11434/v1"})
        assert chamadas[0]["url"] == "http://localhost:11434/v1/chat/completions"

    def test_manda_bearer(self, monkeypatch):
        chamadas = com_respostas(monkeypatch, openai_ok(RESPOSTA_IA))
        guide_ai.refine(FAQ, META, {"provider": "openai", "api_key": "sk-abc"})
        assert chamadas[0]["headers"]["authorization"] == "Bearer sk-abc"

    def test_cai_para_json_object_se_nao_suportar_schema(self, monkeypatch):
        chamadas = com_respostas(
            monkeypatch,
            FakeResp(status_code=400, text="response_format json_schema not supported"),
            openai_ok(RESPOSTA_IA),
        )
        guide_ai.refine(FAQ, META, {"provider": "openai", "api_key": "k"})
        assert chamadas[1]["body"]["response_format"] == {"type": "json_object"}

    def test_aceita_json_dentro_de_cerca_de_codigo(self, monkeypatch):
        com_respostas(monkeypatch, openai_ok(f"```json\n{RESPOSTA_IA}\n```"))
        out = guide_ai.refine(FAQ, META, {"provider": "openai", "api_key": "k"})
        assert out["matched_by_name"] == 2


class TestErrosDeProvedor:
    @pytest.mark.parametrize("provider", ["gemini", "openai"])
    def test_chave_invalida(self, monkeypatch, provider):
        com_respostas(monkeypatch, FakeResp(status_code=401, text="unauthorized"))
        with pytest.raises(guide_ai.GuideAIError, match="inválida"):
            guide_ai.refine(FAQ, META, {"provider": provider, "api_key": "k"})

    @pytest.mark.parametrize("provider", ["gemini", "openai"])
    def test_limite_de_uso(self, monkeypatch, provider):
        com_respostas(monkeypatch, FakeResp(status_code=429, text="rate limited"))
        with pytest.raises(guide_ai.GuideAIError, match="[Ll]imite"):
            guide_ai.refine(FAQ, META, {"provider": provider, "api_key": "k"})

    def test_falha_de_rede(self, monkeypatch):
        def explode(*a, **k):
            raise guide_ai.requests.RequestException("sem rede")
        monkeypatch.setattr(guide_ai.requests, "post", explode)
        with pytest.raises(guide_ai.GuideAIError, match="rede"):
            guide_ai.refine(FAQ, META, {"provider": "gemini", "api_key": "k"})

    def test_texto_que_nao_e_json(self, monkeypatch):
        com_respostas(monkeypatch, openai_ok("desculpe, não consigo"))
        with pytest.raises(guide_ai.GuideAIError, match="JSON"):
            guide_ai.refine(FAQ, META, {"provider": "openai", "api_key": "k"})


class TestPrompt:
    def test_prompt_lista_conquistas_e_guia(self):
        p = guide_ai._prompt(FAQ, META)
        assert "Extravagant Petals" in p and "Death Valley" in p

    def test_prompt_inclui_pontos_e_descricao(self):
        p = guide_ai._prompt(FAQ, META)
        assert "(10 pts)" in p and "Complete Humid Cave." in p


class TestConfiguracao:
    def test_anthropic_e_o_provedor_padrao(self):
        assert guide_ai.DEFAULT_PROVIDER == "anthropic"
        assert guide_ai.PROVIDERS["anthropic"]["default_model"] == "claude-opus-4-8"

    def test_todo_provedor_tem_rotulo_e_modelo_padrao(self):
        for pid, info in guide_ai.PROVIDERS.items():
            assert info["label"] and info["default_model"], pid

    def test_modelo_vazio_cai_no_padrao_do_provedor(self):
        assert guide_ai.resolve_model("gemini", "") == guide_ai.PROVIDERS["gemini"]["default_model"]

    def test_modelo_escolhido_prevalece(self):
        assert guide_ai.resolve_model("gemini", "gemini-2.5-flash") == "gemini-2.5-flash"

    def test_endpoint_proprio_para_compativeis(self):
        assert guide_ai.resolve_base_url("openai", "http://localhost:1234/v1/") == \
            "http://localhost:1234/v1"

    def test_endpoint_vazio_cai_no_da_openai(self):
        assert guide_ai.resolve_base_url("openai", "") == "https://api.openai.com/v1"

    def test_schema_exige_ordem_e_secoes(self):
        assert set(guide_ai.RESPONSE_SCHEMA["required"]) == {"order", "sections"}

    def test_schema_nao_aceita_campos_extras(self):
        """Campos livres abririam espaço para a IA inventar estrutura."""
        assert guide_ai.RESPONSE_SCHEMA["additionalProperties"] is False
