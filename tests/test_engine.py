"""Testes do engine — servidor estático e escolha de cor de destaque.

Cobrem as partes de `engine.py` que dá para exercitar sem abrir janela nem
tocar a rede.

Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine  # noqa: E402


@pytest.fixture
def handler():
    """`translate_path` não usa estado da conexão, então dá para exercitá-lo
    numa instância sem socket."""
    return engine._AssetHandler.__new__(engine._AssetHandler)


class TestTranslatePath:
    def test_serve_a_ui_do_bundle(self, handler):
        assert handler.translate_path("/ui/app.js") == str(engine.BUNDLE_DIR / "ui" / "app.js")

    def test_serve_assets_da_pasta_de_dados(self, handler):
        got = handler.translate_path("/assets/badges/jogo/12345.png")
        assert got == str(engine.DATA_DIR / "assets" / "badges" / "jogo" / "12345.png")

    def test_ignora_query_e_fragmento(self, handler):
        assert handler.translate_path("/ui/style.css?v=2#top") == str(
            engine.BUNDLE_DIR / "ui" / "style.css"
        )

    @pytest.mark.parametrize("ataque", [
        "/../../../../etc/passwd",
        "/ui/../../../../etc/passwd",
        "/assets/../../../../etc/passwd",
        "/%2e%2e/%2e%2e/etc/passwd",
        "/ui/%2E%2E%2F%2E%2E%2Fetc/passwd",
        "/./../../etc/shadow",
        "//../../etc/passwd",
    ])
    def test_nao_escapa_das_pastas_servidas(self, handler, ataque):
        """Antes, `normpath` preservava os `..` iniciais e o caminho saía da
        árvore servida — qualquer arquivo do usuário ficava legível."""
        resolvido = Path(handler.translate_path(ataque)).resolve()
        bases = (engine.BUNDLE_DIR.resolve(), engine.DATA_DIR.resolve())
        assert any(resolvido == b or b in resolvido.parents for b in bases)

    def test_raiz_nao_quebra(self, handler):
        assert handler.translate_path("/") == str(engine.BUNDLE_DIR.resolve())


class TestPickAccent:
    @pytest.fixture(autouse=True)
    def games_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "GAMES_DIR", tmp_path)
        return tmp_path

    @staticmethod
    def _write(dirpath: Path, slug: str, accent: str):
        (dirpath / f"{slug}.json").write_text(
            json.dumps({"slug": slug, "accent": accent}), encoding="utf-8"
        )

    def test_biblioteca_vazia_devolve_cor_da_paleta(self):
        assert engine.Api._pick_accent("digimon_world_4") in engine.ACCENTS

    def test_escolhe_uma_cor_ainda_nao_usada(self, games_dir):
        for i, cor in enumerate(engine.ACCENTS[:3]):
            self._write(games_dir, f"jogo{i}", cor)
        assert engine.Api._pick_accent("novo_jogo") == engine.ACCENTS[3]

    def test_nao_repete_apos_remover_um_jogo(self, games_dir):
        """Contar arquivos dava a cor errada aqui: 3 arquivos no disco fariam o
        próximo jogo receber ACCENTS[3], que já está em uso."""
        self._write(games_dir, "a", engine.ACCENTS[0])
        self._write(games_dir, "b", engine.ACCENTS[1])
        self._write(games_dir, "c", engine.ACCENTS[3])
        assert engine.Api._pick_accent("novo_jogo") == engine.ACCENTS[2]

    def test_estavel_entre_execucoes(self):
        """Sem depender do hash aleatório do Python."""
        primeira = engine.Api._pick_accent("digimon_survive")
        assert all(engine.Api._pick_accent("digimon_survive") == primeira for _ in range(5))

    def test_reveza_quando_todas_estao_igualmente_usadas(self, games_dir):
        for i, cor in enumerate(engine.ACCENTS):
            self._write(games_dir, f"jogo{i}", cor)
        escolhas = {engine.Api._pick_accent(f"slug_{c}") for c in "abcdefghij"}
        assert len(escolhas) > 1        # não fixa sempre a mesma cor

    def test_ignora_json_corrompido(self, games_dir):
        (games_dir / "quebrado.json").write_text("{isso não é json", encoding="utf-8")
        assert engine.Api._pick_accent("novo") in engine.ACCENTS


class TestConsoleWindowAndArt:
    def test_tamanho_adaptativo_tem_fallback_previsivel(self, monkeypatch):
        monkeypatch.setattr(engine.sys, "platform", "linux")
        assert engine.adaptive_normal_size() == (1296, 810)

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        for nome, sub in [
            ("GAMES_DIR", "games"), ("CACHE_DIR", "cache"),
            ("BADGES_DIR", "badges"), ("ICONS_DIR", "icons"),
            ("ART_DIR", "art"), ("GUIDES_DIR", "guides"),
            ("GUIDE_MEDIA_DIR", "guide-media"),
        ]:
            monkeypatch.setattr(engine, nome, tmp_path / sub)
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        return engine.Api()

    @staticmethod
    def _write_game(api, payload):
        path = engine.GAMES_DIR / f"{payload['slug']}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_arte_manual_nunca_e_substituida(self, api, monkeypatch):
        self._write_game(api, {
            "slug": "jogo", "title": "Jogo", "art": {"background": "/manual.png"}
        })
        called = []
        monkeypatch.setattr(api, "_search_source", lambda *args: called.append(args))
        out = api.refresh_game_art("jogo", True)
        assert out["status"] == "ready" and out["source"] == "manual"
        assert called == []

    def test_busca_automatica_persiste_origem_e_cache(self, api, monkeypatch):
        path = self._write_game(api, {
            "slug": "jogo", "title": "Jogo", "art": {"box": "/box.png"}
        })
        monkeypatch.setattr(api, "_source_ready", lambda source: source == "steamgriddb")
        monkeypatch.setattr(api, "_search_source", lambda source, term: {
            "ok": True, "heroes": [{"url": "https://cdn/hero.png"}]
        })

        def fake_download(_url, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"image")
            return True

        monkeypatch.setattr(engine.image_fetch, "download_image", fake_download)
        api._enrich_game_art("jogo")
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["art"]["background"].startswith("/assets/art/jogo/background-auto.png")
        assert saved["art_meta"]["background"]["origin"] == "steamgriddb"
        assert saved["art_meta"]["background"]["manual"] is False

    def test_sem_fonte_mantem_fallback_da_capa(self, api, monkeypatch):
        path = self._write_game(api, {
            "slug": "jogo", "title": "Jogo", "art": {"box": "/box.png"}
        })
        monkeypatch.setattr(api, "_source_ready", lambda _source: False)
        api._enrich_game_art("jogo")
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["art"] == {"box": "/box.png"}
        assert saved["art_meta"]["auto_status"] == "fallback"


class TestSyncGameMastery:
    """O progresso deixou de ser curadoria Normal/Hard: hardcore e softcore vêm
    da RetroAchievements e são exclusivos entre si."""

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "GAMES_DIR", tmp_path / "games")
        monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr(engine, "BADGES_DIR", tmp_path / "badges")
        monkeypatch.setattr(engine, "ICONS_DIR", tmp_path / "icons")
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        return engine.Api()

    @staticmethod
    def _game(ids):
        return {
            "slug": "jogo", "title": "Jogo", "platform": "GameCube",
            "retroachievements_game_id": 1,
            "walkthrough": [{"step": 1, "area": "A", "achievements": [{"id": i} for i in ids]}],
            "achievements_meta": {str(i): {"title": f"Ach {i}", "desc": "", "badge": ""} for i in ids},
        }

    def _sync(self, api, estados):
        """`estados`: {id: None | 'softcore' | 'hardcore'}. Roda o cálculo real
        de progresso com um cliente falso no lugar da RetroAchievements."""
        earned = {
            aid: {
                "id": aid, "title": f"Ach {aid}", "desc": "", "badge": "",
                "earned": estado is not None,
                "hardcore": estado == "hardcore",
                "date": "2026-03-04 22:08:11" if estado else "",
            }
            for aid, estado in estados.items()
        }

        class FakeClient:
            username = "u"
            get_game_info_and_user_progress = staticmethod(lambda gid: {})
            parse_achievements = staticmethod(lambda progress: earned)
            download_badge = staticmethod(lambda *a: True)

        api._client = FakeClient()
        api._sync_game(self._game(list(estados)))
        return api.state["jogo"]

    def test_hardcore_e_softcore_sao_exclusivos(self, api):
        d = self._sync(api, {1: "hardcore", 2: "softcore", 3: None})
        assert d["modes"]["hardcore"]["earned"] == 1
        assert d["modes"]["softcore"]["earned"] == 1

    def test_os_dois_modos_compartilham_o_denominador(self, api):
        d = self._sync(api, {1: "hardcore", 2: "softcore", 3: None})
        assert d["modes"]["hardcore"]["total"] == 3
        assert d["modes"]["softcore"]["total"] == 3

    def test_earned_total_soma_os_dois_modos(self, api):
        d = self._sync(api, {1: "hardcore", 2: "softcore", 3: None})
        m = d["mastery"]
        assert m["earned"] == d["modes"]["hardcore"]["earned"] + d["modes"]["softcore"]["earned"]
        assert m["earned"] == 2

    def test_percentual_de_mastery_conta_so_hardcore(self, api):
        d = self._sync(api, {1: "hardcore", 2: "softcore", 3: None, 4: None})
        assert d["mastery"]["percent"] == 25      # 1 de 4, não 2 de 4

    def test_remaining_inclui_as_presas_em_softcore(self, api):
        d = self._sync(api, {1: "hardcore", 2: "softcore", 3: None})
        assert d["mastery"]["remaining"] == 2     # a softcore ainda precisa ser refeita

    def test_lista_as_conquistas_so_em_softcore(self, api):
        d = self._sync(api, {1: "hardcore", 2: "softcore", 5: "softcore"})
        assert d["mastery"]["softcore_ids"] == [2, 5]
        assert d["mastery"]["softcore_only"] == 2

    def test_mastery_completo(self, api):
        d = self._sync(api, {1: "hardcore", 2: "hardcore"})
        assert d["mastery"]["complete"] is True
        assert d["mastery"]["percent"] == 100
        assert d["mastery"]["remaining"] == 0

    def test_tudo_softcore_nao_e_mastery(self, api):
        d = self._sync(api, {1: "softcore", 2: "softcore"})
        assert d["mastery"]["complete"] is False
        assert d["mastery"]["percent"] == 0
        assert d["mastery"]["earned"] == 2        # mas o jogo está 100% "obtido"

    def test_cada_conquista_carrega_seu_modo(self, api):
        d = self._sync(api, {1: "hardcore", 2: "softcore", 3: None})
        por_id = {a["id"]: a for a in d["achievements"]}
        assert por_id[1]["mode"] == "hardcore" and por_id[1]["hardcore"] is True
        assert por_id[2]["mode"] == "softcore" and por_id[2]["hardcore"] is False
        assert por_id[3]["earned"] is False

    def test_jogo_sem_conquistas_nao_divide_por_zero(self, api):
        d = self._sync(api, {})
        assert d["mastery"]["percent"] == 0
        assert d["mastery"]["complete"] is False


class TestAutoImport:
    """A biblioteca espelha a conta: jogo começado entra sozinho, jogo removido
    à mão não volta."""

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        for nome, sub in [("GAMES_DIR", "games"), ("CACHE_DIR", "cache"),
                          ("BADGES_DIR", "badges"), ("ICONS_DIR", "icons")]:
            monkeypatch.setattr(engine, nome, tmp_path / sub)
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        api = engine.Api()
        api._client = object()          # só precisa não ser None
        return api

    @staticmethod
    def _played(*jogos):
        """jogos: (titulo, num_awarded)"""
        return [
            {"GameID": 100 + i, "Title": t, "NumAwarded": n, "MaxPossible": 50}
            for i, (t, n) in enumerate(jogos)
        ]

    @pytest.fixture
    def capturado(self, api, monkeypatch):
        """Substitui a chamada de rede e a importação real; devolve os ids que
        teriam sido importados."""
        chamados = {}

        def fake_start(ids, auto=False):
            chamados["ids"] = list(ids)
            chamados["auto"] = auto
            return {"ok": True}

        monkeypatch.setattr(api, "start_bulk_import", fake_start)
        return chamados

    def _com_jogos(self, api, monkeypatch, jogos):
        monkeypatch.setattr(
            api._client, "get_user_completion_progress",
            lambda: self._played(*jogos), raising=False,
        )

    def test_importa_jogo_que_nao_esta_na_biblioteca(self, api, capturado, monkeypatch):
        api._client = type("C", (), {"get_user_completion_progress": lambda s: self._played(("Digimon World", 10))})()
        assert api._auto_import_scan() == 1
        assert capturado["ids"] == [100]
        assert capturado["auto"] is True

    def test_ignora_jogo_sem_nenhuma_conquista(self, api, capturado):
        api._client = type("C", (), {"get_user_completion_progress": lambda s: self._played(("Só Aberto", 0))})()
        assert api._auto_import_scan() == 0
        assert "ids" not in capturado

    def test_ignora_jogo_ja_na_biblioteca(self, api, capturado):
        engine.GAMES_DIR.mkdir(parents=True, exist_ok=True)
        (engine.GAMES_DIR / "digimon_world.json").write_text("{}", encoding="utf-8")
        api._client = type("C", (), {"get_user_completion_progress": lambda s: self._played(("Digimon World", 10))})()
        assert api._auto_import_scan() == 0

    def test_nao_ressuscita_jogo_removido_a_mao(self, api, capturado):
        api.settings["dismissed"] = ["digimon_world"]
        api._client = type("C", (), {"get_user_completion_progress": lambda s: self._played(("Digimon World", 10))})()
        assert api._auto_import_scan() == 0
        assert "ids" not in capturado

    def test_respeita_a_preferencia_desligada(self, api, capturado):
        api.settings["auto_import"] = False
        api._client = type("C", (), {"get_user_completion_progress": lambda s: self._played(("Digimon World", 10))})()
        assert api._auto_import_scan() == 0

    def test_nao_atropela_importacao_em_andamento(self, api, capturado):
        api.bulk = {"running": True}
        api._client = type("C", (), {"get_user_completion_progress": lambda s: self._played(("Digimon World", 10))})()
        assert api._auto_import_scan() == 0

    def test_erro_de_api_nao_propaga(self, api, capturado):
        def explode(self_):
            raise engine.RAError("sem rede")
        api._client = type("C", (), {"get_user_completion_progress": explode})()
        assert api._auto_import_scan() == 0

    def test_marca_o_horario_da_varredura(self, api, capturado):
        api._client = type("C", (), {"get_user_completion_progress": lambda s: []})()
        assert api.last_auto_import == 0.0
        api._auto_import_scan()
        assert api.last_auto_import > 0


class TestDismissed:
    @pytest.fixture(autouse=True)
    def paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "GAMES_DIR", tmp_path / "games")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr(engine, "BADGES_DIR", tmp_path / "badges")
        monkeypatch.setattr(engine, "ICONS_DIR", tmp_path / "icons")
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        return tmp_path

    def test_apagar_registra_como_dispensado(self):
        api = engine.Api()
        api.delete_game("digimon_world")
        assert "digimon_world" in api.settings["dismissed"]

    def test_dispensado_persiste_em_disco(self):
        engine.Api().delete_game("digimon_world")
        assert "digimon_world" in engine.load_settings()["dismissed"]

    def test_restaurar_remove_da_lista(self):
        api = engine.Api()
        api.delete_game("digimon_world")
        api.restore_game("digimon_world")
        assert api.settings["dismissed"] == []

    def test_apagar_duas_vezes_nao_duplica(self):
        api = engine.Api()
        api.delete_game("x")
        api.delete_game("x")
        assert api.settings["dismissed"].count("x") == 1

    def test_auto_import_ligado_por_padrao(self):
        assert engine.load_settings()["auto_import"] is True

    def test_settings_corrompido_cai_no_padrao(self, paths):
        engine.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        engine.SETTINGS_PATH.write_text("{quebrado", encoding="utf-8")
        s = engine.load_settings()
        assert s["auto_import"] is True and s["dismissed"] == []

    def test_toggle_persiste(self):
        api = engine.Api()
        api.set_auto_import(False)
        assert engine.load_settings()["auto_import"] is False


class TestConfigDeIA:
    """O provedor é trocável (Claude, Gemini, OpenAI-compatível) e cada um
    guarda a sua própria chave."""

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        for nome, sub in [("GAMES_DIR", "games"), ("CACHE_DIR", "cache"),
                          ("BADGES_DIR", "badges"), ("ICONS_DIR", "icons")]:
            monkeypatch.setattr(engine, nome, tmp_path / sub)
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        return engine.Api()

    def test_anthropic_e_o_padrao(self, api):
        assert api.get_ai_config()["provider"] == "anthropic"

    def test_lista_os_provedores_para_a_ui(self, api):
        ids = {p["id"] for p in api.get_ai_config()["providers"]}
        assert {"anthropic", "gemini", "openai"} <= ids

    def test_salva_chave_e_libera_a_ia(self, api):
        cfg = api.set_ai_config("gemini", "chave-gemini")
        assert cfg["provider"] == "gemini" and cfg["ai_ready"] is True

    def test_nunca_devolve_a_chave(self, api):
        api.set_ai_config("gemini", "chave-secreta")
        assert "chave-secreta" not in json.dumps(api.get_ai_config())

    def test_cada_provedor_guarda_a_propria_chave(self, api):
        """Trocar de provedor não pode apagar a chave do anterior."""
        api.set_ai_config("anthropic", "chave-claude")
        api.set_ai_config("gemini", "chave-gemini")
        api.set_ai_config("anthropic", None)          # volta sem reenviar a chave
        assert api.get_ai_config()["ai_ready"] is True
        por_id = {p["id"]: p for p in api.get_ai_config()["providers"]}
        assert por_id["gemini"]["has_key"] and por_id["anthropic"]["has_key"]

    def test_chave_nula_mantem_a_salva(self, api):
        api.set_ai_config("gemini", "chave")
        assert api.set_ai_config("gemini", None, "gemini-2.5-flash")["ai_ready"] is True

    def test_chave_vazia_apaga(self, api):
        api.set_ai_config("gemini", "chave")
        assert api.set_ai_config("gemini", "")["ai_ready"] is False

    def test_guarda_modelo_e_endpoint(self, api):
        api.set_ai_config("openai", "k", "llama3", "http://localhost:11434/v1")
        cfg = api.get_ai_config()
        assert cfg["model"] == "llama3"
        assert cfg["base_url"] == "http://localhost:11434/v1"

    def test_provedor_invalido_e_recusado(self, api):
        assert api.set_ai_config("skynet", "k")["ok"] is False

    def test_configuracao_persiste_em_disco(self, api):
        api.set_ai_config("gemini", "k", "gemini-2.5-flash")
        salvo = engine.load_settings()
        assert salvo["ai_provider"] == "gemini" and salvo["ai_model"] == "gemini-2.5-flash"

    def test_refino_sem_chave_avisa_o_provedor_certo(self, api):
        api.set_ai_config("gemini", "")
        api.pending_import = {"achievements_meta": {"1": {"title": "X"}}}
        api.last_faq = {"text": "texto"}
        res = api.refine_guide_ai()
        assert res["ok"] is False and "Gemini" in res["error"]

    def test_refino_sem_guia_avisa(self, api):
        api.set_ai_config("anthropic", "k")
        api.pending_import = {"achievements_meta": {"1": {"title": "X"}}}
        assert api.refine_guide_ai()["ok"] is False

    def test_melhorar_dicas_nao_altera_walkthrough(self, api, monkeypatch):
        api.set_ai_config("anthropic", "k")
        original = {"title": "Jogo", "walkthrough": [{"step": 1, "achievements": [{"id": 7}]}],
                    "guide": [{"title": "Antes", "blocks": []}]}
        (engine.GAMES_DIR / "jogo.json").write_text(json.dumps(original), encoding="utf-8")
        monkeypatch.setattr(engine.guide_ai, "refine_tips",
                            lambda sections, config: [{"title": "Depois", "blocks": []}])
        assert api.refine_game_tips("jogo")["ok"] is True
        saved = json.loads((engine.GAMES_DIR / "jogo.json").read_text(encoding="utf-8"))
        assert saved["walkthrough"] == original["walkthrough"]
        assert saved["guide"][0]["title"] == "Depois"

    def test_falha_da_ia_preserva_dicas_originais(self, api, monkeypatch):
        api.set_ai_config("anthropic", "k")
        original = {"title": "Jogo", "guide": [{"title": "Original", "blocks": []}]}
        path = engine.GAMES_DIR / "jogo.json"
        path.write_text(json.dumps(original), encoding="utf-8")

        def falha(*_args):
            raise engine.guide_ai.GuideAIError("sem rede")

        monkeypatch.setattr(engine.guide_ai, "refine_tips", falha)
        assert api.refine_game_tips("jogo")["ok"] is False
        assert json.loads(path.read_text(encoding="utf-8")) == original

    def test_traducao_em_background_reporta_progresso_e_salva_no_final(self, api, monkeypatch):
        api.set_ai_config("gemini", "k")
        original = {"title": "Jogo", "guide": [{
            "title": "Before", "blocks": [{"type": "p", "text": "Old"}],
        }]}
        path = engine.GAMES_DIR / "jogo.json"
        path.write_text(json.dumps(original), encoding="utf-8")

        def traduz(sections, _config, progress=None):
            progress(0, 2)
            progress(1, 2)
            progress(2, 2)
            return [{"title": "Depois", "blocks": [{"type": "p", "text": "Novo"}]}]

        monkeypatch.setattr(engine.guide_ai, "translate", traduz)
        started = api.start_game_tips_ai("jogo", "translate")
        assert started["phase"] in ("running", "success")
        limit = time.time() + 2
        status = api.get_game_tips_ai_status()
        while status["phase"] == "running" and time.time() < limit:
            time.sleep(0.01)
            status = api.get_game_tips_ai_status()
        assert status["phase"] == "success" and status["completed"] == 2
        assert json.loads(path.read_text(encoding="utf-8"))["guide"][0]["title"] == "Depois"

    def test_background_impede_operacoes_duplicadas(self, api, monkeypatch):
        api.set_ai_config("gemini", "k")
        path = engine.GAMES_DIR / "jogo.json"
        path.write_text(json.dumps({"title": "J", "guide": [{"title": "T", "blocks": []}]}),
                        encoding="utf-8")

        def lento(sections, _config, progress=None):
            progress(0, 1)
            time.sleep(0.08)
            progress(1, 1)
            return sections

        monkeypatch.setattr(engine.guide_ai, "translate", lento)
        assert api.start_game_tips_ai("jogo", "translate")["ok"] is True
        duplicate = api.start_game_tips_ai("jogo", "refine")
        assert duplicate["ok"] is False and "andamento" in duplicate["error"]
        limit = time.time() + 2
        while api.get_game_tips_ai_status()["phase"] == "running" and time.time() < limit:
            time.sleep(0.01)

    def test_background_mantem_arquivo_inteiro_se_um_lote_falha(self, api, monkeypatch):
        api.set_ai_config("gemini", "k")
        original = {"title": "J", "guide": [{"title": "Original", "blocks": []}]}
        path = engine.GAMES_DIR / "jogo.json"
        path.write_text(json.dumps(original), encoding="utf-8")

        def falha(_sections, _config, progress=None):
            progress(0, 3)
            progress(1, 3)
            raise engine.guide_ai.GuideAIError("lote 2 inválido")

        monkeypatch.setattr(engine.guide_ai, "refine_tips", falha)
        api.start_game_tips_ai("jogo", "refine")
        limit = time.time() + 2
        status = api.get_game_tips_ai_status()
        while status["phase"] == "running" and time.time() < limit:
            time.sleep(0.01)
            status = api.get_game_tips_ai_status()
        assert status["phase"] == "error" and "lote 2" in status["error"]
        assert json.loads(path.read_text(encoding="utf-8")) == original


class TestArtesDoJogo:
    """Capa, tela de título e screenshot: a API devolve as três, e elas dão
    identidade a cada jogo na interface."""

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        for nome, sub in [("GAMES_DIR", "games"), ("CACHE_DIR", "cache"),
                          ("BADGES_DIR", "badges"), ("ICONS_DIR", "icons"),
                          ("ART_DIR", "art")]:
            monkeypatch.setattr(engine, nome, tmp_path / sub)
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        api = engine.Api()

        baixadas = []

        class FakeClient:
            @staticmethod
            def download_image(caminho, destino):
                baixadas.append((caminho, destino))
                return True

        api._client = FakeClient()
        api._baixadas = baixadas
        return api

    PROGRESSO = {
        "ImageBoxArt": "/Images/1.png",
        "ImageTitle": "/Images/2.png",
        "ImageIngame": "/Images/3.png",
    }

    def test_baixa_as_tres_artes(self, api):
        art = api._download_art("jogo", self.PROGRESSO)
        assert set(art) == {"box", "title", "ingame"}

    def test_urls_apontam_para_a_pasta_servida(self, api):
        art = api._download_art("digimon_world_4", self.PROGRESSO)
        assert art["box"] == "/assets/art/digimon_world_4/box.png"

    def test_arte_ausente_e_normal(self, api):
        """Nem todo jogo na RetroAchievements tem as três."""
        art = api._download_art("jogo", {"ImageBoxArt": "/Images/1.png"})
        assert set(art) == {"box"}

    def test_jogo_sem_arte_nenhuma(self, api):
        assert api._download_art("jogo", {}) == {}

    def test_download_que_falha_nao_entra_no_resultado(self, api):
        api._client.download_image = staticmethod(lambda c, d: False)
        assert api._download_art("jogo", self.PROGRESSO) == {}

    def test_sem_cliente_nao_quebra(self, api):
        api._client = None
        assert api._download_art("jogo", self.PROGRESSO) == {}


class TestOpcoesDeOverlay:
    """Os dois interruptores do fullscreen exclusivo vêm DESLIGADOS: o app não
    injeta tecla nem pula de monitor sem o aval do usuário."""

    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        for nome, sub in [("GAMES_DIR", "games"), ("CACHE_DIR", "cache"),
                          ("BADGES_DIR", "badges"), ("ICONS_DIR", "icons"),
                          ("ART_DIR", "art")]:
            monkeypatch.setattr(engine, nome, tmp_path / sub)
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        return engine.Api()

    def test_desligados_por_padrao(self, api):
        assert api.settings["overlay_exit_fullscreen"] is False
        assert api.settings["overlay_second_screen"] is False

    def test_liga_e_persiste(self, api):
        api.set_overlay_option("overlay_exit_fullscreen", True)
        assert engine.load_settings()["overlay_exit_fullscreen"] is True

    def test_recusa_chave_desconhecida(self, api):
        assert api.set_overlay_option("formatar_hd", True)["ok"] is False

    def test_aparecem_no_estado_do_app(self, api):
        api.set_overlay_option("overlay_second_screen", True)
        assert api.get_app_state()["overlay_second_screen"] is True

    def test_aviso_do_overlay_e_entregue_uma_vez(self, api):
        """O aviso de tela cheia exclusiva não pode repetir a cada consulta."""
        api._warn_ui("emulador em tela cheia exclusiva")
        assert "exclusiva" in api.get_bulk_status()["overlay_notice"]
        assert api.get_bulk_status()["overlay_notice"] == ""

    def test_diagnostico_calcula_tamanho_e_dock(self, api):
        class Tracker:
            @staticmethod
            def status():
                return {"detected": True, "title": "Jogo", "process": "pcsx2-qt.exe",
                        "rect": (100, 50, 1280, 720), "last_check": 1, "error": ""}

        api._tracker = Tracker()
        status = api.get_overlay_status()
        assert status["detected"] is True
        assert status["process"] == "pcsx2-qt.exe"
        assert len(status["overlay_size"]) == 2 and len(status["dock"]) == 2


class TestAtualizacoes:
    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        for nome, sub in [("GAMES_DIR", "games"), ("CACHE_DIR", "cache"),
                          ("BADGES_DIR", "badges"), ("ICONS_DIR", "icons"),
                          ("ART_DIR", "art")]:
            monkeypatch.setattr(engine, nome, tmp_path / sub)
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        return engine.Api()

    def test_versao_aparece_no_estado(self, api):
        assert api.get_app_state()["version"] == engine.APP_VERSION == "0.8.4"

    def test_verificacao_automatica_ligada_por_padrao(self, api):
        assert api.get_app_state()["auto_check_updates"] is True

    def test_toggle_de_update_persiste(self, api):
        api.set_auto_check_updates(False)
        assert engine.load_settings()["auto_check_updates"] is False

    def test_lembrar_depois_persiste_timestamp(self, api):
        result = api.defer_update(24)
        assert result["remind_until"] > time.time()
        assert engine.load_settings()["update_remind_until"] == result["remind_until"]


class TestConfiguracoesPorSessao:
    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        for nome, sub in [("GAMES_DIR", "games"), ("CACHE_DIR", "cache"),
                          ("BADGES_DIR", "badges"), ("ICONS_DIR", "icons"),
                          ("ART_DIR", "art")]:
            monkeypatch.setattr(engine, nome, tmp_path / sub)
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        return engine.Api()

    def test_salva_sessao_de_experiencia_de_uma_vez(self, api):
        result = api.set_settings_session("experience", {
            "smart_guide_auto": False, "smart_guide_consent": True,
            "guide_density": "compact", "ui_scale": 125,
            "reduced_motion": True,
        })
        assert result["ok"] is True
        settings = engine.load_settings()
        assert settings["smart_guide_auto"] is False
        assert settings["guide_density"] == "compact"
        assert settings["ui_scale"] == 125

    def test_recusa_campos_fora_da_sessao(self, api):
        result = api.set_settings_session("library", {"ui_scale": 120})
        assert result["ok"] is False

    def test_limita_valores_do_modo_compacto(self, api):
        result = api.set_settings_session("compact", {
            "compact_width": 9999, "compact_height": 1,
            "compact_last": -5, "compact_next": 99,
        })
        assert result["ok"] is True
        assert result["compact_width"] == 520
        assert result["compact_height"] == 64
        assert result["compact_last"] == 0
        assert result["compact_next"] == 10

    def test_salva_preferencias_do_hud_passivo(self, api):
        result = api.set_settings_session("compact", {
            "compact_size_mode": "auto", "compact_content": "guide",
            "compact_corner": "bottom-right", "compact_opacity": 31,
            "compact_hotkey": "ctrl+alt+g", "compact_auto_expand": True,
            "compact_auto_collapse_seconds": 12,
        })
        assert result["ok"] is True
        settings = engine.load_settings()
        assert settings["compact_content"] == "guide"
        assert settings["compact_corner"] == "bottom-right"
        assert settings["compact_opacity"] == 31
        assert settings["compact_auto_collapse_seconds"] == 12

    def test_recusa_hotkey_sem_modificador(self, api):
        result = api.set_settings_session("compact", {"compact_hotkey": "f2"})
        assert result["ok"] is False

    def test_tamanho_auto_minimo_e_expandido_respeita_area_cliente(self, api):
        rect = (0, 0, 1280, 720)
        assert api._compact_size(rect, expanded=False) == (256, 86)
        assert api._compact_size(rect, expanded=True) == (384, 230)

    def test_config_compacto_expoe_hud_passivo(self, api):
        cfg = api.get_compact_config()
        assert cfg["size_mode"] == "auto"
        assert cfg["content"] == "objective"
        assert cfg["opacity"] == 42
        assert cfg["hotkey"] == "ctrl+alt+g"


class TestCleanWalkthrough:
    def test_descarta_o_mode_legado(self):
        steps = [{"step": 1, "area": "A", "achievements": [{"id": 7, "mode": "hard"}]}]
        out = engine.Api._clean_walkthrough(steps)
        assert out == [{"step": 1, "area": "A", "achievements": [{"id": 7}]}]

    def test_ignora_etapa_vazia(self):
        steps = [{"step": 1, "area": "A", "achievements": []},
                 {"step": 2, "area": "B", "achievements": [{"id": 1}]}]
        assert len(engine.Api._clean_walkthrough(steps)) == 1

    def test_nomeia_etapa_sem_area(self):
        out = engine.Api._clean_walkthrough([{"achievements": [{"id": 1}]}])
        assert out[0]["area"] == "Etapa 1"

    def test_ignora_entrada_invalida(self):
        steps = [{"achievements": [{"id": "abc"}, {"sem_id": 1}, {"id": "5"}]}]
        assert engine.Api._clean_walkthrough(steps)[0]["achievements"] == [{"id": 5}]

    def test_lista_vazia(self):
        assert engine.Api._clean_walkthrough([]) == []


class TestSlugify:
    @pytest.mark.parametrize("titulo,esperado", [
        ("Digimon World 4", "digimon_world_4"),
        ("Digimon Story: Cyber Sleuth", "digimon_story_cyber_sleuth"),
        ("Pokémon Ruby & Sapphire", "pokemon_ruby_sapphire"),
        ("   ", "jogo"),
        ("!!!", "jogo"),
    ])
    def test_slugify(self, titulo, esperado):
        assert engine.slugify(titulo) == esperado


class TestGuiaInteligenteApi:
    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        paths = {
            "GAMES_DIR": tmp_path / "games", "CACHE_DIR": tmp_path / "cache",
            "BADGES_DIR": tmp_path / "badges", "ICONS_DIR": tmp_path / "icons",
            "ART_DIR": tmp_path / "art", "GUIDES_DIR": tmp_path / "guides",
            "GUIDE_MEDIA_DIR": tmp_path / "guide-media",
        }
        for key, value in paths.items():
            monkeypatch.setattr(engine, key, value)
        monkeypatch.setattr(engine, "SECRETS_PATH", tmp_path / "secrets.json")
        monkeypatch.setattr(engine, "SETTINGS_PATH", tmp_path / "settings.json")
        api = engine.Api()
        game = {
            "slug": "jogo", "title": "Jogo", "platform": "Console", "accent": "#fff",
            "retroachievements_game_id": 1, "walkthrough": [], "achievements_meta": {},
            "guide": [{"num": "1", "title": "Começo", "blocks": [
                {"type": "step", "text": "Abra a porta."},
                {"type": "note", "text": "Salve antes."},
            ]}],
        }
        paths["GAMES_DIR"].mkdir(parents=True, exist_ok=True)
        (paths["GAMES_DIR"] / "jogo.json").write_text(
            json.dumps(game, ensure_ascii=False), encoding="utf-8"
        )
        return api

    def test_migra_sem_tocar_no_json_original(self, api):
        before = (engine.GAMES_DIR / "jogo.json").read_text(encoding="utf-8")
        bundle = api.get_smart_guide("jogo")
        after = (engine.GAMES_DIR / "jogo.json").read_text(encoding="utf-8")
        assert bundle["ok"] is True
        assert bundle["current"]["provider"] == "local"
        assert before == after
        assert bundle["status"]["phase"] == "awaiting_consent"

    def test_progresso_e_preferencias_persistem(self, api):
        bundle = api.get_smart_guide("jogo")
        block_id = bundle["current"]["chapters"][0]["blocks"][0]["id"]
        result = api.update_guide_progress("jogo", "complete", block_id, True)
        assert block_id in result["progress"]["completed"]
        prefs = api.set_experience_preferences(consent=True, density="compact", ui_scale=125)
        assert prefs["smart_guide_consent"] is True
        assert prefs["guide_density"] == "compact" and prefs["ui_scale"] == 125
