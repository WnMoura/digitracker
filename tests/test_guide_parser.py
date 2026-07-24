"""Testes do parser de guias em PDF.

`guide_parser` só recebe texto e devolve dados — nenhuma I/O, nenhuma rede —,
então dá para exercitar todo o comportamento com fixtures de texto puro.

Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guide_parser as gp  # noqa: E402


# ---------------------------------------------------------------------------- #
# Fixtures de texto
# ---------------------------------------------------------------------------- #
GUIDE = """\
DIGIMON WORLD 4 — GUIA COMPLETO
Página 1
1. INTRODUÇÃO & MECÂNICAS BÁSICAS
Action-RPG da Bandai para GameCube e PS2.
 Toque em inimigos causa dano.
■ Nunca saia de um dungeon sem salvar!
DIGIMON WORLD 4 — GUIA COMPLETO
Página 2
4. WALKTHROUGH — SEQUÊNCIA TEMPORAL
4.1 Death Valley
#3 Goblin Pass
Objetivo: Atravesse as pontes e siga ao sul.
Dica: Entre e saia para respawnar itens.
■ BOSS: BLOSSOMOM
Use arma de ataque a distância.
DIGIMON WORLD 4 — GUIA COMPLETO
Página 3
8. LISTA DE CONQUISTAS
8.1 Modo Normal
★ Extravagant Petals
10 pts
Complete Humid Cave on Normal difficulty.
■ ~
★ Tusks of Ash
25 pts
Defeat Mammothmon in Cliff Dungeon.
■ ~
8.2 Modo Hard
★ Death Valley, Twice Over
50 pts
Complete Death Valley on Hard difficulty.
■ ~
DIGIMON WORLD 4 — GUIA COMPLETO
Página 4
9. DICAS AVANÇADAS & GRINDING
 Goblin Pass: entre e saia para farmar.
"""

# Mesmo formato, outro jogo, e a lista de conquistas NÃO é a seção 8.
OTHER_GUIDE = """\
SUPER METROID — GUIA DEFINITIVO
Page 1
1. VISÃO GERAL
Metroidvania da Nintendo para SNES.
2. ACHIEVEMENT LIST
2.1 Normal Mode
★ First Missile
5 pts
Collect your first missile expansion.
■ ~
★ Kraid Down
25 pts
Defeat Kraid.
■ ~
SUPER METROID — GUIA DEFINITIVO
Page 2
3. ROTAS ALTERNATIVAS
Sequence breaking é possível em Norfair.
"""


@pytest.fixture(scope="module")
def parsed():
    return gp.parse_guide(GUIDE)


# ---------------------------------------------------------------------------- #
# Normalização e utilidades
# ---------------------------------------------------------------------------- #
class TestNormalize:
    def test_remove_acentos_e_caixa(self):
        assert gp.normalize("Evolução  ÁRDUA") == "evolucao ardua"

    def test_colapsa_espacos(self):
        assert gp.normalize("  a   b\tc  ") == "a b c"

    @pytest.mark.parametrize("nome,esperado", [
        ("undead yard (hard)", "undead yard"),
        ("undead yard (vh)", "undead yard"),
        ("undead yard [normal]", "undead yard"),
        ("undead yard (h)", "undead yard"),
        ("undead yard", "undead yard"),
    ])
    def test_strip_diff_remove_sufixo_de_dificuldade(self, nome, esperado):
        assert gp._strip_diff(nome) == esperado

    def test_similaridade_identica_e_maxima(self):
        assert gp._similarity("kraid down", "kraid down") == 1.0

    def test_similaridade_ignora_ordem_das_palavras_parcialmente(self):
        assert gp._similarity("down kraid", "kraid down") > 0.5

    def test_similaridade_com_vazio_e_zero(self):
        assert gp._similarity("", "kraid") == 0.0


class TestSemInferenciaDeDificuldade:
    """A dificuldade do jogo (Normal/Hard) deixou de ser um eixo do app: o que
    separa as conquistas é softcore/hardcore, e isso vem da API, não do PDF."""

    def test_parser_nao_expoe_mais_inferencia_de_modo(self):
        assert not hasattr(gp, "mode_from_difficulty")
        assert not hasattr(gp, "_mode_from")

    def test_conquista_nao_carrega_modo(self, parsed):
        assert all("mode" not in a for a in parsed["achievements"])

    def test_subsecao_vira_rotulo_informativo(self, parsed):
        # A subseção continua registrada como `category`, sem virar dificuldade.
        categorias = [a["category"] for a in parsed["achievements"]]
        assert categorias == ["Modo Normal", "Modo Normal", "Modo Hard"]


# ---------------------------------------------------------------------------- #
# Detecção de cabeçalho/rodapé
# ---------------------------------------------------------------------------- #
class TestDetectFurniture:
    def test_pega_cabecalho_repetido(self):
        lines = GUIDE.splitlines()
        furniture = gp.detect_furniture(lines)
        assert gp._furniture_key("DIGIMON WORLD 4 — GUIA COMPLETO") in furniture

    def test_numero_de_pagina_e_mascarado(self):
        # 'Página 1' e 'Página 4' contam como a mesma linha.
        assert gp._furniture_key("Página 1") == gp._furniture_key("Página 4")

    def test_nao_marca_frase_de_corpo_como_ruido(self):
        lines = ["Use arma de ataque a distância."] * 5
        assert gp.detect_furniture(lines) == set()

    def test_nao_marca_linha_estrutural_como_ruido(self):
        lines = ["★ Kraid Down"] * 5 + ["#3 Goblin Pass"] * 5
        assert gp.detect_furniture(lines) == set()

    def test_linha_longa_nao_e_ruido(self):
        lines = ["x" * 120] * 5
        assert gp.detect_furniture(lines) == set()


# ---------------------------------------------------------------------------- #
# parse_guide
# ---------------------------------------------------------------------------- #
class TestParseGuide:
    def test_encontra_todas_as_secoes(self, parsed):
        assert [s["num"] for s in parsed["sections"]] == ["1", "4", "8", "9"]

    def test_remove_cabecalho_e_rodape(self, parsed):
        textos = [b.get("text", "") for s in parsed["sections"] for b in s["blocks"]]
        assert not any("GUIA COMPLETO" in t for t in textos)
        assert not any(t.startswith("Página") for t in textos)

    def test_marca_a_secao_de_conquistas(self, parsed):
        marcadas = [s["num"] for s in parsed["sections"] if s["is_achievements"]]
        assert marcadas == ["8"]

    def test_extrai_conquistas_na_ordem(self, parsed):
        nomes = [a["name"] for a in parsed["achievements"]]
        assert nomes == ["Extravagant Petals", "Tusks of Ash", "Death Valley, Twice Over"]

    def test_captura_pontos_e_descricao(self, parsed):
        primeira = parsed["achievements"][0]
        assert primeira["pts"] == 10
        assert primeira["desc"] == "Complete Humid Cave on Normal difficulty."

    def test_classifica_blocos_do_walkthrough(self, parsed):
        blocos = next(s for s in parsed["sections"] if s["num"] == "4")["blocks"]
        tipos = {b["type"] for b in blocos}
        assert {"subhead", "step", "label", "boss"} <= tipos

    def test_boss_extrai_o_nome(self, parsed):
        blocos = next(s for s in parsed["sections"] if s["num"] == "4")["blocks"]
        boss = next(b for b in blocos if b["type"] == "boss")
        assert boss["text"] == "BLOSSOMOM"

    def test_label_separa_rotulo_e_conteudo(self, parsed):
        blocos = next(s for s in parsed["sections"] if s["num"] == "4")["blocks"]
        objetivo = next(b for b in blocos if b.get("label") == "Objetivo")
        assert objetivo["text"] == "Atravesse as pontes e siga ao sul."

    def test_nota_perde_o_glifo(self, parsed):
        blocos = next(s for s in parsed["sections"] if s["num"] == "1")["blocks"]
        nota = next(b for b in blocos if b["type"] == "note")
        assert nota["text"] == "Nunca saia de um dungeon sem salvar!"

    def test_texto_vazio_nao_quebra(self):
        assert gp.parse_guide("") == {"sections": [], "achievements": []}

    def test_texto_sem_secoes_nao_quebra(self):
        out = gp.parse_guide("apenas um texto solto\nsem estrutura nenhuma")
        assert out["sections"] == [] and out["achievements"] == []


class TestParseGuideOutroFormato:
    """O parser não pode presumir que a seção 8 é a das conquistas."""

    def test_acha_conquistas_na_secao_2(self):
        out = gp.parse_guide(OTHER_GUIDE)
        marcadas = [s["num"] for s in out["sections"] if s["is_achievements"]]
        assert marcadas == ["2"]

    def test_extrai_as_conquistas(self):
        out = gp.parse_guide(OTHER_GUIDE)
        assert [a["name"] for a in out["achievements"]] == ["First Missile", "Kraid Down"]

    def test_filtra_cabecalho_de_outro_jogo(self):
        out = gp.parse_guide(OTHER_GUIDE)
        textos = [b.get("text", "") for s in out["sections"] for b in s["blocks"]]
        assert not any("GUIA DEFINITIVO" in t for t in textos)


class TestFindAchSection:
    def test_prefere_a_secao_com_mais_estrelas(self):
        secs = [
            {"num": "1", "title": "CONQUISTAS", "lines": ["texto qualquer"]},
            {"num": "2", "title": "OUTRA COISA", "lines": ["★ A", "★ B", "★ C"]},
        ]
        assert gp.find_ach_section(secs) == 1

    def test_cai_para_o_titulo_quando_nao_ha_estrelas(self):
        secs = [
            {"num": "1", "title": "INTRODUÇÃO", "lines": ["texto"]},
            {"num": "2", "title": "LISTA DE CONQUISTAS", "lines": ["texto"]},
        ]
        assert gp.find_ach_section(secs) == 1

    def test_reconhece_titulo_em_ingles(self):
        secs = [{"num": "1", "title": "ACHIEVEMENT LIST", "lines": ["x"]}]
        assert gp.find_ach_section(secs) == 0

    def test_devolve_menos_um_sem_candidato(self):
        secs = [{"num": "1", "title": "INTRODUÇÃO", "lines": ["texto"]}]
        assert gp.find_ach_section(secs) == -1


# ---------------------------------------------------------------------------- #
# order_from_guide
# ---------------------------------------------------------------------------- #
def meta(*pares):
    """{id: {title, points}} a partir de (id, título, pontos)."""
    return {str(i): {"title": t, "points": p} for i, t, p in pares}


class TestOrderFromGuide:
    def test_reordena_pela_ordem_do_guia(self, parsed):
        # No RA os ids vêm em outra ordem — o guia manda.
        m = meta(
            (3, "Death Valley, Twice Over", 50),
            (1, "Extravagant Petals", 10),
            (2, "Tusks of Ash", 25),
        )
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert out["ordered_ids"] == [1, 2, 3]

    def test_nao_devolve_mais_modos(self, parsed):
        m = meta((1, "Extravagant Petals", 10), (3, "Death Valley, Twice Over", 50))
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert "modes" not in out

    def test_casa_ignorando_sufixo_de_dificuldade(self, parsed):
        m = meta((1, "Extravagant Petals (Normal)", 10))
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert out["ordered_ids"][0] == 1
        assert out["report"][0]["how"] == "sem-sufixo"

    def test_casa_com_diferenca_pequena_de_grafia(self, parsed):
        m = meta((1, "Extravagant Petal", 10))     # falta o 's'
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert out["matched_by_name"] == 1

    def test_pontos_desempatam_nomes_iguais(self):
        """Famílias Normal/Hard/VH costumam ter o mesmo nome e pontos diferentes."""
        p = gp.parse_guide(
            "1. CONQUISTAS\n"
            "1.1 Modo Hard\n"
            "★ Undead Yard\n50 pts\nClear it on Hard.\n■ ~\n"
        )
        m = meta((10, "Undead Yard", 10), (20, "Undead Yard", 50))
        out = gp.order_from_guide(p, m, "")
        assert out["ordered_ids"][0] == 20

    def test_nao_reutiliza_a_mesma_conquista(self, parsed):
        m = meta((1, "Extravagant Petals", 10))
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert out["ordered_ids"].count(1) == 1

    def test_nada_e_perdido_no_fallback_por_posicao(self, parsed):
        # 'Goblin Pass' não está na lista de conquistas, mas aparece no texto.
        m = meta((1, "Extravagant Petals", 10), (99, "Goblin Pass", 5))
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert set(out["ordered_ids"]) == {1, 99}
        assert out["missing_ids"] == []

    def test_conquista_ausente_do_texto_vai_para_missing(self, parsed):
        m = meta((1, "Extravagant Petals", 10), (98, "Zzz Inexistente", 5))
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert out["missing_ids"] == [98]

    def test_conquista_do_pdf_sem_par_no_ra_e_reportada(self, parsed):
        m = meta((1, "Extravagant Petals", 10))
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert "Tusks of Ash" in out["unmatched_pdf"]

    def test_contadores(self, parsed):
        m = meta((1, "Extravagant Petals", 10), (2, "Tusks of Ash", 25))
        out = gp.order_from_guide(parsed, m, GUIDE)
        assert out["total"] == 2
        assert out["pdf_total"] == 3
        assert out["matched_by_name"] == 2
        assert out["found"] == 2

    def test_meta_vazia_nao_quebra(self, parsed):
        out = gp.order_from_guide(parsed, {}, GUIDE)
        assert out["ordered_ids"] == [] and out["total"] == 0

    def test_guia_sem_conquistas_devolve_tudo_por_posicao(self):
        p = gp.parse_guide("1. INTRODUÇÃO\nFala sobre Extravagant Petals no começo.")
        m = meta((1, "Extravagant Petals", 10))
        out = gp.order_from_guide(p, m, "Fala sobre Extravagant Petals no começo.")
        assert out["ordered_ids"] == [1]
        assert out["matched_by_name"] == 0
