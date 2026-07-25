"""Testes do rastreio de emulador (modo overlay).

A lógica de decisão e os parsers são puros, então dá para exercitar tudo sem
abrir janela, sem X11 e sem Windows. As fixtures de `xprop`/`xwininfo` são
saídas reais capturadas nesta máquina.

Rodar:  python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import emulator_tracker as et  # noqa: E402


# ---------------------------------------------------------------------------- #
# Reconhecer emuladores
# ---------------------------------------------------------------------------- #
class TestIsEmulator:
    @pytest.mark.parametrize("title,cls", [
        ("Dolphin 5.0-21460 | JIT64 | Digimon World 4", "dolphin-emu"),
        ("ePSXe 2.0.5", "ePSXe"),
        ("RetroArch", "retroarch"),
        ("PCSX2 1.7", "pcsx2-qt"),
        ("DuckStation", "duckstation-qt"),
        ("PPSSPP v1.17", "ppsspp"),
    ])
    def test_reconhece_emuladores(self, title, cls):
        assert et.is_emulator(title, cls)

    @pytest.mark.parametrize("title,cls", [
        ("Firefox", "firefox"),
        ("Terminal", "gnome-terminal-server"),
        ("Documento.odt — LibreOffice", "libreoffice"),
    ])
    def test_ignora_janelas_comuns(self, title, cls):
        assert not et.is_emulator(title, cls)

    def test_nao_confunde_o_gerenciador_de_arquivos_do_kde(self):
        """'dolphin' também é o file manager do KDE — grudar nele seria errado."""
        assert not et.is_emulator("Início — Dolphin", "org.kde.dolphin")

    def test_nao_gruda_em_si_mesmo(self):
        assert not et.is_emulator("DigiTracker", "digitracker")

    def test_casa_pelo_titulo_mesmo_sem_classe(self):
        assert et.is_emulator("Dolphin 5.0", "")

    def test_casa_pela_classe_mesmo_sem_titulo(self):
        assert et.is_emulator("", "ePSXe")

    def test_lista_personalizada_de_emuladores(self):
        assert et.is_emulator("MeuEmu 1.0", "", patterns=["meuemu"])
        assert not et.is_emulator("Dolphin", "", patterns=["meuemu"])

    def test_campos_vazios_nao_quebram(self):
        assert not et.is_emulator("", "")
        assert not et.is_emulator(None, None)


# ---------------------------------------------------------------------------- #
# Onde o overlay encosta
# ---------------------------------------------------------------------------- #
class TestDockPosition:
    def test_canto_superior_direito_de_dentro(self):
        # emulador 1280x720 em (100, 50); overlay 300x232
        assert et.dock_position((100, 50, 1280, 720), (300, 232)) == (1064, 66)

    def test_respeita_a_margem(self):
        x, y = et.dock_position((0, 0, 1000, 800), (300, 232), margin=40)
        assert (x, y) == (660, 40)

    def test_janela_menor_que_o_overlay_encosta_na_esquerda(self):
        """Não deixa o overlay vazar para fora da janela do emulador."""
        x, _y = et.dock_position((500, 500, 200, 150), (300, 232))
        assert x == 500

    def test_acompanha_emulador_em_monitor_a_esquerda(self):
        x, y = et.dock_position((-1920, 0, 1920, 1080), (300, 232))
        assert (x, y) == (-316, 16)


# ---------------------------------------------------------------------------- #
# Máquina de estados
# ---------------------------------------------------------------------------- #
class FakeActions(dict):
    """Registra o que o watcher mandou fazer."""

    def __init__(self):
        self.calls = []
        super().__init__(
            enter=lambda rect: self.calls.append(("enter", rect)),
            follow=lambda rect: self.calls.append(("follow", rect)),
            exit=lambda: self.calls.append(("exit",)),
            assert_top=lambda: self.calls.append(("top",)),
        )

    @property
    def kinds(self):
        return [c[0] for c in self.calls]


def watcher(*janelas):
    """Watcher cujo localizador devolve `janelas` em sequência (repete a última)."""
    seq = list(janelas)
    acts = FakeActions()

    def find():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return et.OverlayWatcher(find, acts), acts


WIN = {"title": "Dolphin", "rect": (0, 0, 1280, 720)}
WIN_MOVIDO = {"title": "Dolphin", "rect": (200, 100, 1280, 720)}


class TestOverlayWatcher:
    def test_gruda_quando_o_emulador_aparece(self):
        w, acts = watcher(WIN)
        w.step()
        assert acts.calls[0] == ("enter", (0, 0, 1280, 720))
        assert w.active

    def test_nao_regruda_a_cada_ciclo(self):
        w, acts = watcher(WIN)
        w.step()
        w.step()
        w.step()
        assert acts.kinds.count("enter") == 1

    def test_segue_a_janela_quando_ela_move(self):
        w, acts = watcher(WIN, WIN_MOVIDO)
        w.step()
        w.step()
        assert ("follow", (200, 100, 1280, 720)) in acts.calls

    def test_nao_reposiciona_se_a_janela_ficou_parada(self):
        w, acts = watcher(WIN)
        w.step()
        w.step()
        assert "follow" not in acts.kinds

    def test_reaplica_o_topmost_enquanto_ativo(self):
        """É isso que impede o overlay de sumir atrás do Dolphin."""
        w, acts = watcher(WIN)
        w.step()
        w.step()
        assert "top" in acts.kinds

    def test_restaura_quando_o_emulador_fecha(self):
        w, acts = watcher(WIN, None)
        w.step()
        w.step()
        assert acts.calls[-1] == ("exit",)
        assert not w.active

    def test_sem_emulador_nao_faz_nada(self):
        w, acts = watcher(None)
        w.step()
        assert acts.calls == []

    def test_desligado_nas_preferencias_nao_gruda(self):
        w, acts = watcher(WIN)
        w.step(enabled=False)
        assert acts.calls == []

    def test_nao_interfere_se_o_usuario_ja_esta_em_compacto(self):
        w, acts = watcher(WIN)
        w.step(user_compact=True)
        assert acts.calls == []

    def test_saida_manual_silencia_ate_o_emulador_fechar(self):
        """Se o usuário sai do compacto com o jogo aberto, o app não insiste."""
        w, acts = watcher(WIN)
        w.step()
        w.notify_manual_exit()
        w.step()
        w.step()
        assert acts.kinds.count("enter") == 1

    def test_fechar_o_emulador_desfaz_o_silencio(self):
        seq = [WIN, WIN, None, WIN]
        acts = FakeActions()
        w = et.OverlayWatcher(lambda: seq.pop(0), acts)
        w.step()                 # gruda
        w.notify_manual_exit()   # usuário dispensa
        w.step()                 # silenciado
        w.step()                 # emulador fechou -> desmuta
        w.step()                 # novo emulador -> gruda de novo
        assert acts.kinds.count("enter") == 2

    def test_saida_manual_sem_overlay_ativo_e_inocua(self):
        w, _acts = watcher(WIN)
        w.notify_manual_exit()
        assert not w.muted


# ---------------------------------------------------------------------------- #
# Parsers do backend Linux (saídas reais capturadas nesta máquina)
# ---------------------------------------------------------------------------- #
CLIENT_LIST = "_NET_CLIENT_LIST(WINDOW): window id # 0x280000a, 0x2a00003, 0x3000005\n"

PROPS_TERMINAL = '''WM_CLASS(STRING) = "gnome-terminal-server", "Gnome-terminal"
_NET_WM_NAME(UTF8_STRING) = "⠂ Analisar repositório digitracker"
WM_NAME(COMPOUND_TEXT) = "⠂ Analisar repositório digitracker"
'''

PROPS_EPSXE = '''WM_CLASS(STRING) = "epsxe", "ePSXe"
_NET_WM_NAME(UTF8_STRING) = "ePSXe 2.0.5"
WM_NAME(COMPOUND_TEXT) = "ePSXe 2.0.5"
'''

GEOM = '''
xwininfo: Window id: 0x280000a "ePSXe 2.0.5"

  Absolute upper-left X:  96
  Absolute upper-left Y:  159
  Relative upper-left X:  96
  Relative upper-left Y:  159
  Width: 908
  Height: 663
  Depth: 32
  Map State: IsViewable
  Override Redirect State: no
'''

GEOM_MINIMIZADA = GEOM.replace("IsViewable", "IsUnviewable")


class TestParsersLinux:
    def test_le_a_lista_de_janelas(self):
        assert et.parse_client_list(CLIENT_LIST) == ["0x280000a", "0x2a00003", "0x3000005"]

    def test_lista_vazia_quando_nao_ha_propriedade(self):
        assert et.parse_client_list("") == []

    def test_extrai_titulo_e_classe(self):
        titulo, cls = et.parse_window_props(PROPS_EPSXE)
        assert titulo == "ePSXe 2.0.5"
        assert "epsxe" in cls.lower()

    def test_prefere_o_nome_utf8(self):
        titulo, _cls = et.parse_window_props(PROPS_TERMINAL)
        assert titulo == "⠂ Analisar repositório digitracker"

    def test_props_vazias_nao_quebram(self):
        assert et.parse_window_props("") == ("", "")

    def test_extrai_a_geometria(self):
        assert et.parse_geometry(GEOM) == (96, 159, 908, 663)

    def test_janela_minimizada_e_ignorada(self):
        assert et.parse_geometry(GEOM_MINIMIZADA) is None

    def test_geometria_incompleta_e_ignorada(self):
        assert et.parse_geometry("Map State: IsViewable\n") is None


class TestLinuxTrackerIntegracao:
    """O tracker inteiro, com os comandos externos substituídos."""

    @pytest.fixture
    def tracker(self, monkeypatch):
        t = et.LinuxTracker()
        respostas = {
            ("xprop", "-root"): CLIENT_LIST,
            ("xprop", "0x280000a"): PROPS_TERMINAL,
            ("xprop", "0x2a00003"): PROPS_EPSXE,
            ("xwininfo", "0x2a00003"): GEOM,
        }

        def fake_run(cmd):
            if cmd[0] == "xprop" and cmd[1] == "-root":
                return respostas[("xprop", "-root")]
            return respostas.get((cmd[0], cmd[2]), "")

        monkeypatch.setattr(t, "_run", fake_run)
        return t

    def test_acha_o_emulador_entre_as_janelas(self, tracker):
        win = tracker.find_emulator_window()
        assert win["title"] == "ePSXe 2.0.5"
        assert win["rect"] == (96, 159, 908, 663)

    def test_sem_emulador_devolve_none(self, monkeypatch):
        t = et.LinuxTracker()
        monkeypatch.setattr(
            t, "_run",
            lambda cmd: CLIENT_LIST if cmd[1] == "-root" else PROPS_TERMINAL,
        )
        assert t.find_emulator_window() is None

    def test_comando_indisponivel_nao_quebra(self, monkeypatch):
        t = et.LinuxTracker()
        monkeypatch.setattr(t, "_run", lambda cmd: "")
        assert t.find_emulator_window() is None


class TestEscolhaDeMonitor:
    """Fullscreen exclusivo captura UMA saída de vídeo; num segundo monitor o
    overlay continua visível. É a única forma de conviver com o exclusivo."""

    TELA_1 = (0, 0, 1920, 1080)
    TELA_2 = (1920, 0, 1920, 1080)

    def test_devolve_o_monitor_livre(self):
        jogo = (0, 0, 1920, 1080)          # ocupa a tela 1 inteira
        assert et.pick_free_screen(jogo, [self.TELA_1, self.TELA_2]) == self.TELA_2

    def test_monitor_livre_do_outro_lado(self):
        jogo = (1920, 0, 1920, 1080)
        assert et.pick_free_screen(jogo, [self.TELA_1, self.TELA_2]) == self.TELA_1

    def test_sem_segundo_monitor_devolve_none(self):
        assert et.pick_free_screen(self.TELA_1, [self.TELA_1]) is None

    def test_jogo_em_janela_ainda_ocupa_a_tela(self):
        """Janela pequena dentro do monitor: aquele monitor não está livre."""
        jogo = (100, 100, 800, 600)
        assert et.pick_free_screen(jogo, [self.TELA_1, self.TELA_2]) == self.TELA_2

    def test_escolhe_a_maior_entre_as_livres(self):
        pequena = (1920, 0, 1280, 720)
        grande = (3200, 0, 2560, 1440)
        achada = et.pick_free_screen(self.TELA_1, [self.TELA_1, pequena, grande])
        assert achada == grande

    def test_lista_vazia_nao_quebra(self):
        assert et.pick_free_screen(self.TELA_1, []) is None
        assert et.pick_free_screen(self.TELA_1, None) is None

    def test_area_de_sobreposicao(self):
        assert et._overlap((0, 0, 10, 10), (5, 5, 10, 10)) == 25
        assert et._overlap((0, 0, 10, 10), (20, 20, 5, 5)) == 0
        assert et._overlap((0, 0, 10, 10), (10, 0, 10, 10)) == 0   # encostados


class TestFullscreenExclusivo:
    """A reação ao exclusivo acontece UMA vez por sessão de fullscreen —
    injetar tecla ou pular de monitor em laço seria inaceitável."""

    def test_dispara_o_tratamento_uma_unica_vez(self):
        acts = FakeActions()
        chamadas = []
        acts["on_exclusive"] = lambda rect, win: chamadas.append(rect)
        w = et.OverlayWatcher(lambda: WIN, acts)
        for _ in range(4):
            w.step(exclusive=True)
        assert len(chamadas) == 1

    def test_nao_gruda_enquanto_em_exclusivo(self):
        """Grudar seria inútil: o overlay não apareceria mesmo."""
        acts = FakeActions()
        acts["on_exclusive"] = lambda rect, win: None
        w = et.OverlayWatcher(lambda: WIN, acts)
        w.step(exclusive=True)
        assert "enter" not in acts.kinds
        assert not w.active

    def test_recebe_o_retangulo_e_a_janela(self):
        acts = FakeActions()
        recebido = {}
        acts["on_exclusive"] = lambda rect, win: recebido.update(rect=rect, win=win)
        et.OverlayWatcher(lambda: WIN, acts).step(exclusive=True)
        assert recebido["rect"] == (0, 0, 1280, 720)
        assert recebido["win"]["title"] == "Dolphin"

    def test_sair_do_exclusivo_rearma(self):
        acts = FakeActions()
        chamadas = []
        acts["on_exclusive"] = lambda rect, win: chamadas.append(1)
        w = et.OverlayWatcher(lambda: WIN, acts)
        w.step(exclusive=True)
        w.step(exclusive=False)          # voltou para janela: gruda normalmente
        w.step(exclusive=True)           # entrou de novo: pode reagir
        assert len(chamadas) == 2
        assert "enter" in acts.kinds

    def test_fechar_o_emulador_rearma(self):
        seq = [WIN, None, WIN]
        acts = FakeActions()
        chamadas = []
        acts["on_exclusive"] = lambda rect, win: chamadas.append(1)
        w = et.OverlayWatcher(lambda: seq.pop(0), acts)
        w.step(exclusive=True)
        w.step()                          # emulador fechou
        w.step(exclusive=True)
        assert len(chamadas) == 2

    def test_sem_tratador_nao_quebra(self):
        w = et.OverlayWatcher(lambda: WIN, FakeActions())
        w.step(exclusive=True)            # sem "on_exclusive" registrado

    def test_linux_nunca_reporta_exclusivo(self):
        """O X11 não tem o modo exclusivo do Direct3D."""
        assert et.LinuxTracker().is_exclusive_fullscreen() is False

    def test_linux_nao_manda_alt_enter(self):
        assert et.LinuxTracker().send_fullscreen_toggle(None) is False


class TestMonitoresLinux:
    SAIDA = (
        "Monitors: 2\n"
        " 0: +*DP-4 1920/600x1080/340+0+0  DP-4\n"
        " 1: +HDMI-1 2560/700x1440/390+1920+0  HDMI-1\n"
    )

    def test_le_os_monitores(self, monkeypatch):
        t = et.LinuxTracker()
        monkeypatch.setattr(t, "_run", lambda cmd: self.SAIDA)
        assert t.screens() == [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)]

    def test_xrandr_ausente_nao_quebra(self, monkeypatch):
        t = et.LinuxTracker()
        monkeypatch.setattr(t, "_run", lambda cmd: "")
        assert t.screens() == []


def test_create_tracker_escolhe_pela_plataforma():
    t = et.create_tracker()
    esperado = et.WindowsTracker if sys.platform == "win32" else et.LinuxTracker
    assert isinstance(t, esperado)
