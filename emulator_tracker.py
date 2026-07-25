"""Rastreio de janelas de emulador para o modo overlay do DigiTracker.

Quando um emulador abre, o app entra sozinho no modo compacto e gruda no canto
superior-direito DE DENTRO da janela do emulador, seguindo-a se ela mover ou
redimensionar; quando o emulador fecha, o app restaura tamanho e posição.

Dois backends com a mesma interface `find_emulator_window()`:

  - Windows (o alvo real — é onde o Dolphin roda via DigiTracker.exe):
    ctypes/user32 puro, sem dependência nova. Também re-aplica
    SetWindowPos(HWND_TOPMOST) a cada verificação — é isso que corrige o
    always-on-top se perder quando o jogo rouba o foco.
  - Linux/X11 (validação local com o ePSXe): subprocess `xprop`/`xwininfo`,
    evitando rodar Wnck fora do main loop GTK.

Limite físico documentado: sobre fullscreen EXCLUSIVO nenhum overlay fica por
cima — no emulador, use janela ou borderless.

A lógica de decisão (quando entrar/seguir/sair) fica em `OverlayWatcher`,
separada dos backends para ser testável com um localizador falso.
"""
from __future__ import annotations

import re
import subprocess
import sys

# ---------------------------------------------------------------------------- #
# Que janelas são emuladores
# ---------------------------------------------------------------------------- #
# Casados (case-insensitive) contra o TÍTULO e a CLASSE da janela.
DEFAULT_EMULATORS = [
    "dolphin", "retroarch", "pcsx2", "duckstation", "epsxe", "ppsspp",
    "snes9x", "mgba", "melonds", "cemu", "rpcs3", "xemu", "mupen",
    "project64", "desmume", "flycast", "ares", "bsnes", "nestopia",
    "fceux", "mesen", "yuzu", "ryujinx",
]

# Falsos positivos conhecidos: "dolphin" também é o gerenciador de arquivos do
# KDE, e a própria janela do app não pode se detectar.
_EXCLUDES = ["org.kde.dolphin", "dolphin file manager", "digitracker"]

# SHQueryUserNotificationState: 3 = app Direct3D em fullscreen EXCLUSIVO, o
# único caso em que nenhum overlay aparece por cima.
QUNS_RUNNING_D3D_FULL_SCREEN = 3


def is_emulator(title: str, wm_class: str, patterns=None) -> bool:
    """A janela (título + classe) parece um emulador?"""
    haystack = f"{title or ''} {wm_class or ''}".lower()
    if any(x in haystack for x in _EXCLUDES):
        return False
    for p in (patterns or DEFAULT_EMULATORS):
        if p and p.lower() in haystack:
            return True
    return False


# ---------------------------------------------------------------------------- #
# Onde grudar o overlay
# ---------------------------------------------------------------------------- #
def dock_position(rect, size, margin: int = 16):
    """Canto superior-direito de DENTRO da janela do emulador.

    `rect` = (x, y, w, h) do emulador; `size` = (w, h) do overlay.
    Se o emulador for menor que o overlay + margem, encosta na borda esquerda
    em vez de deixar o overlay sair da janela.
    """
    ex, ey, ew, _eh = rect
    ow, _oh = size
    x = ex + ew - ow - margin
    if x < ex:
        x = ex
    return x, ey + margin


def _overlap(a, b) -> int:
    """Área de interseção entre dois retângulos (x, y, w, h)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dx = min(ax + aw, bx + bw) - max(ax, bx)
    dy = min(ay + ah, by + bh) - max(ay, by)
    return dx * dy if dx > 0 and dy > 0 else 0


def pick_free_screen(game_rect, screens):
    """A tela que o jogo NÃO está ocupando.

    Fullscreen exclusivo captura uma saída de vídeo só; num segundo monitor o
    overlay continua visível. `screens` é uma lista de (x, y, w, h). Devolve a
    tela livre com mais área, ou None se o jogo cobre todas.
    """
    livres = [s for s in (screens or []) if _overlap(game_rect, s) == 0]
    if not livres:
        return None
    return max(livres, key=lambda s: s[2] * s[3])


# ---------------------------------------------------------------------------- #
# Máquina de estados (pura — testável com um localizador falso)
# ---------------------------------------------------------------------------- #
class OverlayWatcher:
    """Decide quando entrar/seguir/sair do overlay.

    `find_window` -> {"title", "rect": (x,y,w,h)} | None.
    `actions` -> dict com `enter(rect)`, `follow(rect)`, `exit()` e, opcional,
    `assert_top()` (re-aplicado a cada passo enquanto ativo — Windows).

    Regras:
      - só entra sozinho se o usuário já não estiver em compacto manual;
      - se o usuário SAIR do compacto com o emulador aberto
        (`notify_manual_exit`), fica mudo até esse emulador fechar — o app não
        briga com o usuário;
      - emulador sumiu -> restaura e desmuta.
    """

    def __init__(self, find_window, actions):
        self.find_window = find_window
        self.actions = actions
        self.active = False        # o overlay atual foi iniciado por nós
        self.muted = False         # usuário dispensou até o emulador fechar
        self._last_rect = None
        self._fs_tratado = False   # já reagimos a ESTA sessão de fullscreen

    def notify_manual_exit(self):
        """Usuário desligou o compacto na mão. Se fomos nós que entramos,
        respeita: silencia até o emulador atual fechar."""
        if self.active:
            self.active = False
            self.muted = True
            self._last_rect = None

    def step(self, enabled: bool = True, user_compact: bool = False,
             exclusive: bool = False):
        """Um ciclo de verificação. Chamado pelo laço de polling do engine.

        `exclusive` avisa que há um jogo em fullscreen exclusivo: nesse caso
        nenhum overlay aparece, e quem decide o que fazer é `on_exclusive`
        (mover para outra tela, mandar Alt+Enter ou só avisar) — uma única vez
        por sessão de fullscreen, nunca em laço."""
        win = self.find_window() if enabled else None

        if win is None:
            if self.active:
                self.actions["exit"]()
                self.active = False
            self.muted = False          # emulador fechou: o próximo pode grudar
            self._last_rect = None
            self._fs_tratado = False
            return

        if exclusive:
            if not self._fs_tratado:
                self._fs_tratado = True
                trata = self.actions.get("on_exclusive")
                if trata:
                    trata(tuple(win["rect"]), win)
            return
        self._fs_tratado = False        # saiu do exclusivo: pode reagir de novo

        if self.active:
            rect = tuple(win["rect"])
            if rect != self._last_rect:
                self.actions["follow"](rect)
                self._last_rect = rect
            top = self.actions.get("assert_top")
            if top:
                top()
            return

        # compacto manual do usuário (não iniciado por nós): não interfere
        if self.muted or user_compact:
            return

        rect = tuple(win["rect"])
        self.actions["enter"](rect)
        self.active = True
        self._last_rect = rect


# ---------------------------------------------------------------------------- #
# Backend Windows (ctypes/user32) — plataforma-alvo
# ---------------------------------------------------------------------------- #
class WindowsTracker:
    """Enumera janelas visíveis via user32 e devolve o primeiro emulador."""

    def __init__(self, patterns=None):
        self.patterns = patterns
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self.user32 = ctypes.windll.user32

    def find_emulator_window(self):
        ctypes, wintypes = self._ctypes, self._wintypes
        user32 = self.user32
        found: list[dict] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if is_emulator(title.value, cls.value, self.patterns):
                r = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                    found.append({
                        "title": title.value,
                        "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
                        "hwnd": hwnd,
                    })
                    return False        # o primeiro já basta
            return True

        user32.EnumWindows(enum_cb, 0)
        return found[0] if found else None

    def make_topmost(self, hwnd):
        """Re-aplica always-on-top. O TopMost do pywebview se perde quando o
        jogo rouba o foco — re-afirmar a cada ciclo é o que impede o overlay de
        sumir atrás do Dolphin."""
        if not hwnd:
            return
        HWND_TOPMOST = -1
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        self.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        )

    def own_window_handle(self, window, title: str = "DigiTracker"):
        """HWND da janela do app. O backend winforms do pywebview expõe
        `native.Handle`; se não expuser, acha pela barra de título."""
        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle:
            try:
                return int(handle.ToInt64())
            except AttributeError:
                return int(handle)
        return self.user32.FindWindowW(None, title) or None

    def is_exclusive_fullscreen(self) -> bool:
        """Há um app Direct3D em fullscreen EXCLUSIVO?

        Nesse modo o emulador toma a saída de vídeo e o compositor sai do
        caminho: nenhuma janela comum é desenhada por cima, por mais TOPMOST
        que seja. `SHQueryUserNotificationState` responde exatamente isso —
        `QUNS_RUNNING_D3D_FULL_SCREEN` (3). Detectar permite avisar ou agir em
        vez de o overlay simplesmente sumir sem explicação.
        """
        ctypes = self._ctypes
        estado = ctypes.c_int(0)
        try:
            hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(estado))
        except Exception:
            return False
        return hr == 0 and estado.value == QUNS_RUNNING_D3D_FULL_SCREEN

    def send_fullscreen_toggle(self, hwnd) -> bool:
        """Manda Alt+Enter para o emulador, tirando-o do fullscreen exclusivo.

        Só é chamado com o interruptor ligado e **uma vez por sessão** de
        fullscreen — injetar tecla em outro programa em laço seria inaceitável.
        Traz a janela para frente antes, senão a tecla vai para quem tem foco.
        """
        if not hwnd:
            return False
        ctypes, wintypes = self._ctypes, self._wintypes
        user32 = self.user32

        VK_MENU, VK_RETURN = 0x12, 0x0D
        KEYEVENTF_KEYUP, INPUT_KEYBOARD = 0x0002, 1

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        def tecla(vk, up=False):
            return INPUT(type=INPUT_KEYBOARD,
                         ki=KEYBDINPUT(wVk=vk, wScan=0,
                                       dwFlags=KEYEVENTF_KEYUP if up else 0,
                                       time=0, dwExtraInfo=None))

        try:
            user32.SetForegroundWindow(hwnd)
            eventos = (INPUT * 4)(tecla(VK_MENU), tecla(VK_RETURN),
                                  tecla(VK_RETURN, True), tecla(VK_MENU, True))
            enviados = user32.SendInput(4, ctypes.byref(eventos), ctypes.sizeof(INPUT))
            return enviados == 4
        except Exception:
            return False

    def screens(self):
        """Retângulos (x, y, w, h) de cada monitor."""
        ctypes, wintypes = self._ctypes, self._wintypes
        achadas = []

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        @MONITORENUMPROC
        def cb(_hmon, _hdc, lprect, _lparam):
            r = lprect.contents
            achadas.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
            return True

        try:
            self.user32.EnumDisplayMonitors(None, None, cb, 0)
        except Exception:
            return []
        return achadas


# ---------------------------------------------------------------------------- #
# Backend Linux/X11 (xprop + xwininfo) — validação local
# ---------------------------------------------------------------------------- #
_CLIENT_LIST_RE = re.compile(r"window id #\s*(.*)$", re.M)
_WM_CLASS_RE = re.compile(r'^WM_CLASS\(STRING\)\s*=\s*(.+)$', re.M)
_WM_NAME_RE = re.compile(r'^_NET_WM_NAME\(UTF8_STRING\)\s*=\s*"(.*)"', re.M)
_WM_NAME_FALLBACK_RE = re.compile(
    r'^WM_NAME\((?:STRING|COMPOUND_TEXT|UTF8_STRING)\)\s*=\s*"(.*)"', re.M
)
_GEOM_RES = {
    "x": re.compile(r"Absolute upper-left X:\s*(-?\d+)"),
    "y": re.compile(r"Absolute upper-left Y:\s*(-?\d+)"),
    "w": re.compile(r"Width:\s*(\d+)"),
    "h": re.compile(r"Height:\s*(\d+)"),
}


def parse_client_list(output: str) -> list[str]:
    """Ids de janela do `xprop -root _NET_CLIENT_LIST`."""
    m = _CLIENT_LIST_RE.search(output)
    if not m:
        return []
    return [w.strip() for w in m.group(1).split(",") if w.strip().startswith("0x")]


def parse_window_props(output: str) -> tuple[str, str]:
    """(título, classe) do `xprop -id <id> WM_CLASS _NET_WM_NAME WM_NAME`."""
    cls = ""
    m = _WM_CLASS_RE.search(output)
    if m:
        cls = " ".join(re.findall(r'"([^"]*)"', m.group(1)))
    name = ""
    m = _WM_NAME_RE.search(output) or _WM_NAME_FALLBACK_RE.search(output)
    if m:
        name = m.group(1)
    return name, cls


def parse_geometry(output: str):
    """(x, y, w, h) do `xwininfo -id <id> -stats`, ou None se não visível."""
    if "IsViewable" not in output:
        return None                     # minimizada/oculta
    vals = {}
    for key, rex in _GEOM_RES.items():
        m = rex.search(output)
        if not m:
            return None
        vals[key] = int(m.group(1))
    return vals["x"], vals["y"], vals["w"], vals["h"]


class LinuxTracker:
    """Consulta o X11 via `xprop`/`xwininfo` (presentes por padrão no Ubuntu)."""

    def __init__(self, patterns=None):
        self.patterns = patterns

    @staticmethod
    def _run(cmd) -> str:
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=3
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def find_emulator_window(self):
        for wid in parse_client_list(self._run(["xprop", "-root", "_NET_CLIENT_LIST"])):
            props = self._run(["xprop", "-id", wid, "WM_CLASS", "_NET_WM_NAME", "WM_NAME"])
            title, cls = parse_window_props(props)
            if not is_emulator(title, cls, self.patterns):
                continue
            rect = parse_geometry(self._run(["xwininfo", "-id", wid, "-stats"]))
            if rect:
                return {"title": title, "rect": rect, "hwnd": None}
        return None

    def make_topmost(self, _hwnd):
        pass                            # no GTK o on_top do pywebview se sustenta

    def own_window_handle(self, _window, _title="DigiTracker"):
        return None

    def is_exclusive_fullscreen(self) -> bool:
        """No X11 não existe o modo exclusivo do Direct3D: mesmo em tela cheia
        o compositor continua desenhando por cima."""
        return False

    def send_fullscreen_toggle(self, _hwnd) -> bool:
        return False

    def screens(self):
        """Retângulos dos monitores, via `xrandr --listmonitors`."""
        out = self._run(["xrandr", "--listmonitors"])
        achadas = []
        for m in re.finditer(r"(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", out):
            w, h, x, y = (int(g) for g in m.groups())
            achadas.append((x, y, w, h))
        return achadas


def create_tracker(patterns=None):
    """O backend certo para a plataforma atual."""
    if sys.platform == "win32":
        return WindowsTracker(patterns)
    return LinuxTracker(patterns)
