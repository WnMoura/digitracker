"""Testes do parser de guias em texto livre (FAQs do GameFAQs).

Diferente dos PDFs (que têm `★`, `N. TÍTULO` maiúsculo), um FAQ é ASCII solto:
títulos cercados por réguas ou entradas numeradas isoladas. A fixture é um
excerto real de um guia do Digimon World 4.

Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guide_parser as gp  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "gamefaqs_excerpt.txt"


@pytest.fixture(scope="module")
def faq_real():
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed_real(faq_real):
    return gp.parse_freeform(faq_real)


# ---------------------------------------------------------------------------- #
# Detecção de cabeçalho
# ---------------------------------------------------------------------------- #
class TestCabecalhos:
    def test_titulo_ensanduichado_entre_reguas(self):
        out = gp.parse_freeform(
            "======\n---Starter Digimon--\n======\nAgumon é forte.\n"
        )
        assert [s["title"] for s in out["sections"]] == ["Starter Digimon"]

    def test_primeira_linha_do_corpo_nao_vira_secao(self, parsed_real):
        """A régua de FECHAMENTO fica logo antes do corpo — sem exigir régua
        abaixo do texto, 'Agumon: ...' virava um cabeçalho."""
        titulos = [s["title"] for s in parsed_real["sections"]]
        assert not any(t.startswith("Agumon:") for t in titulos)
        assert not any(t.startswith("Goburimon:") for t in titulos)

    def test_titulo_sublinhado(self):
        out = gp.parse_freeform("Bosses\n=========\nMammothmon é o primeiro.\n")
        assert [s["title"] for s in out["sections"]] == ["Bosses"]

    def test_entrada_numerada_isolada_vira_secao(self):
        out = gp.parse_freeform("1)Intro\n\nBem-vindo ao guia.\n")
        assert [s["title"] for s in out["sections"]] == ["1)Intro"]

    def test_indice_no_topo_nao_vira_secao(self):
        """No índice as entradas são consecutivas; cabeçalhos reais ficam
        isolados por linhas em branco."""
        texto = (
            "Table of Contents\n1)Intro\n2)Controls\n3)Bosses\n"
            "\n1)Intro\n\nBem-vindo ao guia.\n"
        )
        titulos = [s["title"] for s in gp.parse_freeform(texto)["sections"]]
        assert titulos.count("1)Intro") == 1
        assert "2)Controls" not in titulos

    def test_titulo_numerado_com_texto_logo_abaixo(self):
        """`4) Copyright info.` tem o corpo colado embaixo — ainda é seção."""
        out = gp.parse_freeform("1)Intro\n\nbla\n\n4) Copyright info.\nEste FAQ é c2005.\n")
        assert "4) Copyright info." in [s["title"] for s in out["sections"]]

    def test_item_de_lista_numerado_nao_vira_secao(self):
        """Guias numeram itens de lista; frases longas seguidas de continuação
        são conteúdo, não cabeçalho."""
        texto = (
            "6.Tips\n=======\n"
            "1. In the beginning of the game, avoid large groups of Digimon\n"
            "because they will overwhelm you quickly.\n\n"
            "2. It is ok to run away from a fight! Running away to heal is\n"
            "a valid tactic.\n"
        )
        out = gp.parse_freeform(texto)
        assert [s["title"] for s in out["sections"]] == ["6.Tips"]
        assert len(out["sections"][0]["blocks"]) == 2

    def test_titulo_em_maiusculas_isolado(self):
        out = gp.parse_freeform("Intro\n\nDICAS AVANCADAS\n\nFarme no Goblin Pass.\n")
        assert "DICAS AVANCADAS" in [s["title"] for s in out["sections"]]

    def test_linha_longa_nao_vira_titulo(self):
        longa = "X" * 120
        out = gp.parse_freeform(f"Secao\n=====\n\n{longa}\n\ntexto\n")
        assert longa not in [s["title"] for s in out["sections"]]

    def test_regua_interna_sai_do_titulo(self):
        out = gp.parse_freeform("---4.Side-Quests---  (sqst)---\n=====\ntexto\n")
        assert out["sections"][0]["title"] == "4.Side-Quests (sqst)"

    def test_hifen_de_palavra_composta_sobrevive(self):
        out = gp.parse_freeform("Side-Quests\n=====\ntexto\n")
        assert out["sections"][0]["title"] == "Side-Quests"


# ---------------------------------------------------------------------------- #
# Corpo
# ---------------------------------------------------------------------------- #
class TestBlocos:
    def test_junta_linhas_quebradas_em_paragrafo(self):
        """FAQs quebram em ~78 colunas; uma linha por bloco ficaria ilegível."""
        out = gp.parse_freeform(
            "Secao\n=====\nEsta frase foi quebrada\nem duas linhas.\n"
        )
        blocos = out["sections"][0]["blocks"]
        assert len(blocos) == 1
        assert blocos[0]["text"] == "Esta frase foi quebrada em duas linhas."

    def test_linha_em_branco_separa_paragrafos(self):
        out = gp.parse_freeform("Secao\n=====\nPrimeiro.\n\nSegundo.\n")
        assert len(out["sections"][0]["blocks"]) == 2

    def test_linha_indentada_vira_item(self):
        out = gp.parse_freeform("Secao\n=====\n  item recuado\n")
        assert out["sections"][0]["blocks"][0]["type"] == "li"

    def test_reguas_nao_viram_bloco(self, parsed_real):
        textos = [b["text"] for s in parsed_real["sections"] for b in s["blocks"]]
        assert not any(set(t) <= set("-=*_~#+ ") for t in textos if t)

    def test_conteudo_antes_do_primeiro_titulo_e_ignorado(self):
        out = gp.parse_freeform("lixo de capa\n\nSecao\n=====\ntexto\n")
        assert len(out["sections"]) == 1
        assert out["sections"][0]["blocks"][0]["text"] == "texto"

    def test_secao_sem_conteudo_e_descartada(self):
        out = gp.parse_freeform("Vazia\n=====\n\nCheia\n=====\ntexto\n")
        assert [s["title"] for s in out["sections"]] == ["Cheia"]

    def test_numeracao_e_sequencial(self, parsed_real):
        nums = [s["num"] for s in parsed_real["sections"]]
        assert nums == [str(i) for i in range(1, len(nums) + 1)]


class TestTruncamento:
    def test_respeita_o_limite_de_blocos(self):
        texto = "Secao\n=====\n" + "".join(f"paragrafo sobre o chefe {chr(65 + i % 26)}{i}\n\n" for i in range(50))
        out = gp.parse_freeform(texto, max_blocks=10)
        blocos = out["sections"][0]["blocks"]
        assert len([b for b in blocos if b["type"] != "note"]) == 10

    def test_avisa_que_truncou(self):
        texto = "Secao\n=====\n" + "".join(f"paragrafo sobre o chefe {chr(65 + i % 26)}{i}\n\n" for i in range(50))
        out = gp.parse_freeform(texto, max_blocks=5)
        assert out["sections"][-1]["blocks"][-1]["type"] == "note"
        assert "truncado" in out["sections"][-1]["blocks"][-1]["text"]

    def test_guia_curto_nao_ganha_aviso(self, parsed_real):
        tipos = [b["type"] for s in parsed_real["sections"] for b in s["blocks"]]
        assert "note" not in tipos


# ---------------------------------------------------------------------------- #
# Formato compatível com o resto do app
# ---------------------------------------------------------------------------- #
class TestFormatoCompativel:
    def test_mesmo_shape_do_parse_guide(self, parsed_real):
        """A aba de Dicas renderiza os dois sem saber a origem."""
        assert set(parsed_real) == {"sections", "achievements"}
        for sec in parsed_real["sections"]:
            assert set(sec) >= {"num", "title", "blocks", "is_achievements"}

    def test_nao_marca_secao_de_conquistas(self, parsed_real):
        """Um FAQ não traz a lista de conquistas do RA."""
        assert parsed_real["achievements"] == []
        assert all(not s["is_achievements"] for s in parsed_real["sections"])

    def test_crlf_do_gamefaqs_nao_atrapalha(self):
        out = gp.parse_freeform("Secao\r\n=====\r\nprimeira\r\nsegunda\r\n")
        assert out["sections"][0]["blocks"][0]["text"] == "primeira segunda"

    def test_texto_vazio_nao_quebra(self):
        assert gp.parse_freeform("") == {"sections": [], "achievements": []}

    def test_texto_sem_estrutura_nao_quebra(self):
        assert gp.parse_freeform("só um texto solto\nsem títulos")["sections"] == []


# ---------------------------------------------------------------------------- #
# O que interessa de verdade: ordenar as conquistas pelo FAQ
# ---------------------------------------------------------------------------- #
class TestOrdenacaoPeloFaq:
    """`order_from_guide` casa pelo nome e, quando não casa, ordena pela posição
    do título no texto — é isso que faz o FAQ bruto servir de walkthrough."""

    def test_ordena_pela_posicao_no_texto(self, faq_real, parsed_real):
        # Nomes que aparecem no excerto, propositalmente fora de ordem no "RA"
        meta = {
            "10": {"title": "Numemon", "points": 5},
            "20": {"title": "Agumon", "points": 5},
            "30": {"title": "Goburimon", "points": 5},
        }
        out = gp.order_from_guide(parsed_real, meta, faq_real)
        assert out["ordered_ids"] == [20, 30, 10]     # ordem de aparição

    def test_conquista_ausente_do_faq_vai_para_missing(self, faq_real, parsed_real):
        meta = {
            "20": {"title": "Agumon", "points": 5},
            "99": {"title": "Zzz Inexistente", "points": 5},
        }
        out = gp.order_from_guide(parsed_real, meta, faq_real)
        assert out["missing_ids"] == [99]
        assert 20 in out["ordered_ids"]

    def test_posiciona_pelo_lugar_citado_na_descricao(self):
        """Guias antigos não têm os nomes das conquistas — elas foram criadas
        pela comunidade do RA muito depois. A descrição cita o lugar, e é por
        ele que a conquista é localizada no texto."""
        faq = (
            "Walkthrough\n===========\n"
            "Primeiro vá para a Humid Cave e derrote tudo.\n\n"
            "Depois siga para o Cliff Dungeon.\n"
        )
        parsed = gp.parse_freeform(faq)
        meta = {
            "2": {"title": "Metal Defenses", "desc": "Complete Cliff Dungeon on Normal difficulty.", "points": 10},
            "1": {"title": "Extravagant Petals", "desc": "Complete Humid Cave on Normal difficulty.", "points": 10},
        }
        out = gp.order_from_guide(parsed, meta, faq)
        assert out["ordered_ids"] == [1, 2]      # ordem em que o guia cita

    def test_verbo_inicial_nao_atrapalha_a_ancora(self):
        assert "humid cave" in gp._desc_anchors("Complete Humid Cave on Normal difficulty.")

    def test_ancora_ignora_frase_curta_demais(self):
        assert gp._desc_anchors("Go Up now.") == []

    def test_sem_descricao_nao_quebra(self):
        assert gp._desc_anchors("") == []
        assert gp._desc_anchors(None) == []

    def test_nada_e_perdido(self, faq_real, parsed_real):
        meta = {str(i): {"title": t, "points": 5} for i, t in
                enumerate(["Agumon", "Goburimon", "Zzz Nao Existe"], start=1)}
        out = gp.order_from_guide(parsed_real, meta, faq_real)
        assert len(out["ordered_ids"]) + len(out["missing_ids"]) == 3
