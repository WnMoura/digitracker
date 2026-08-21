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

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

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

# Executáveis conhecidos resolvem o caso de janelas de renderização cujo título
# não contém o nome do emulador (comum no PCSX2 Qt).
DEFAULT_EXECUTABLES = {
    "dolphin.exe", "retroarch.exe", "pcsx2.exe", "pcsx2-qt.exe",
    "duckstation.exe", "duckstation-qt.exe", "epsxe.exe", "ppsspp.exe",
    "mgba.exe", "melonds.exe", "cemu.exe", "rpcs3.exe", "project64.exe",
    "desmume.exe", "flycast.exe", "snes9x.exe",
}

# Falsos positivos conhecidos: "dolphin" também é o gerenciador de arquivos do
# KDE, e a própria janela do app não pode se detectar.
_EXCLUDES = ["org.kde.dolphin", "dolphin file manager", "digitracker"]
_AUXILIARY_TITLES = (
    "settings", "properties", "configuration", "controller settings",
    "configurações", "preferências", "about pcsx2",
)

# SHQueryUserNotificationState: 3 = app Direct3D em fullscreen EXCLUSIVO, o
# único caso em que nenhum overlay aparece por cima.
QUNS_RUNNING_D3D_FULL_SCREEN = 3

# Tamanho mínimo para considerar uma janela como jogo/emulador (descarta
# helpers, bandejas e popups minúsculos que aparecem na árvore X11).
_MIN_EMU_W, _MIN_EMU_H = 320, 200


def parse_hotkey(value: str):
    """Converte ``ctrl+alt+g`` em (modificadores, virtual-key).

    Mantém o parser puro para validar configurações sem depender do Windows.
    A hotkey é deliberadamente limitada a combinações com modificador para
    não consumir teclas comuns usadas pelo jogo.
    """
    tokens = [part.strip().lower() for part in str(value or "").split("+") if part.strip()]
    if len(tokens) < 2:
        raise ValueError("A hotkey precisa de pelo menos um modificador e uma tecla.")
    mods = 0
    mod_map = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001,
               "shift": 0x0004, "win": 0x0008, "meta": 0x0008}
    key = None
    for token in tokens:
        if token in mod_map:
            mods |= mod_map[token]
            continue
        if key is not None:
            raise ValueError("A hotkey só pode ter uma tecla principal.")
        if len(token) == 1 and token.isalnum():
            key = ord(token.upper())
        elif token.startswith("f") and token[1:].isdigit() and 1 <= int(token[1:]) <= 12:
            key = 0x70 + int(token[1:]) - 1
        else:
            key = {"space": 0x20, "tab": 0x09, "enter": 0x0D,
                   "escape": 0x1B, "esc": 0x1B}.get(token)
            if key is None:
                raise ValueError("Tecla de hotkey não reconhecida.")
    if not mods or key is None:
        raise ValueError("A hotkey precisa de modificador e tecla principal.")
    return mods, key


def is_emulator(title: str, wm_class: str, patterns=None, process: str = "") -> bool:
    """A janela (título + classe) parece um emulador?"""
    process_name = Path(process or "").name.lower()
    haystack = f"{title or ''} {wm_class or ''} {process_name}".lower()
    if any(x in haystack for x in _EXCLUDES):
        return False
    if not patterns and process_name in DEFAULT_EXECUTABLES:
        return True
    for p in (patterns or DEFAULT_EMULATORS):
        if p and p.lower() in haystack:
            return True
    return False


def choose_window(candidates, foreground=None, previous=None):
    """Escolhe uma janela sem oscilar entre launcher, popup e renderização.

    A janela em primeiro plano ganha prioridade (uma renderização recém-aberta
    do PCSX2 deve substituir o launcher); fora disso, mantemos a escolha anterior
    enquanto válida e só então usamos a maior área cliente.
    """
    candidates = list(candidates or [])
    if not candidates:
        return None
    if foreground:
        for candidate in candidates:
            if candidate.get("hwnd") == foreground:
                return candidate
    if previous:
        for candidate in candidates:
            if candidate.get("hwnd") == previous:
                return candidate
    return max(candidates, key=lambda c: c["rect"][2] * c["rect"][3])


def is_auxiliary_window(title: str, window_class: str = "") -> bool:
    text = (title or "").strip().lower()
    return (window_class or "").strip() == "#32770" or any(
        marker in text for marker in _AUXILIARY_TITLES
    )


def enable_dpi_awareness() -> bool:
    """Põe processo e WinForms no mesmo espaço de coordenadas físicas.

    Tenta Per-Monitor v2 e recua para APIs disponíveis em versões antigas do
    Windows. Deve ser chamado antes de criar a janela pywebview.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        fn = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if fn:
            fn.argtypes = [ctypes.c_void_p]
            if fn(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
                return True
        shcore = getattr(ctypes.windll, "shcore", None)
        if shcore and shcore.SetProcessDpiAwareness(2) in (0, 0x80070005):
            return True
        return bool(user32.SetProcessDPIAware())
    except Exception:
        return False


# ---------------------------------------------------------------------------- #
# Onde grudar o overlay
# ---------------------------------------------------------------------------- #
def dock_position(rect, size, margin: int = 16, corner: str = "top-right"):
    """Posição de um canto DENTRO da janela do emulador.

    `rect` = (x, y, w, h) do emulador; `size` = (w, h) do overlay.
    Se o emulador for menor que o overlay + margem, encosta na borda esquerda
    em vez de deixar o overlay sair da janela.
    """
    ex, ey, ew, eh = rect
    ow, oh = size
    right = ex + max(0, ew - ow - margin)
    bottom = ey + max(0, eh - oh - margin)
    x = right if corner.endswith("right") else ex + margin
    y = bottom if corner.startswith("bottom") else ey + margin
    return max(ex, min(x, ex + max(0, ew - ow))), max(ey, min(y, ey + max(0, eh - oh)))


def dock_candidates(rect, size, margin: int = 16):
    """Cantos em ordem de menor intrusão: superior-direito primeiro."""
    return [(corner, dock_position(rect, size, margin, corner)) for corner in (
        "top-right", "bottom-right", "top-left", "bottom-left")]


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
        self._last_identity = None
        self._fs_tratado = False   # já reagimos a ESTA sessão de fullscreen

    def notify_manual_exit(self):
        """Usuário desligou o compacto na mão. Se fomos nós que entramos,
        respeita: silencia até o emulador atual fechar."""
        if self.active:
            self.active = False
            self.muted = True
            self._last_rect = None
            self._last_identity = None

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
            self._last_identity = None
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
            identity = win.get("hwnd") or (win.get("process"), win.get("title"))
            if rect != self._last_rect or identity != self._last_identity:
                self.actions["follow"](rect)
                self._last_rect = rect
                self._last_identity = identity
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
        self._last_identity = win.get("hwnd") or (win.get("process"), win.get("title"))


# ---------------------------------------------------------------------------- #
# Backend Windows (ctypes/user32) — plataforma-alvo
# ---------------------------------------------------------------------------- #
class WindowsTracker:
    """Enumera janelas visíveis e escolhe a área de jogo mais provável."""

    def __init__(self, patterns=None):
        self.patterns = patterns
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self._selected_hwnd = None
        self.last_status = {"detected": False, "error": "Ainda não verificado."}

        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        query = getattr(self.kernel32, "QueryFullProcessImageNameW", None)
        if query:
            query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                              ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL

    def _process_name(self, hwnd) -> tuple[int, str]:
        ctypes, wintypes = self._ctypes, self._wintypes
        pid = wintypes.DWORD(0)
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return 0, ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return pid.value, ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            query = getattr(self.kernel32, "QueryFullProcessImageNameW", None)
            if query and query(handle, 0, buf, ctypes.byref(size)):
                return pid.value, Path(buf.value).name.lower()
            return pid.value, ""
        finally:
            self.kernel32.CloseHandle(handle)

    def _client_rect(self, hwnd):
        """Área interna em coordenadas da tela, sem borda/barra de título."""
        ctypes, wintypes = self._ctypes, self._wintypes
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if (self.user32.GetClientRect(hwnd, ctypes.byref(rect))
                and self.user32.ClientToScreen(hwnd, ctypes.byref(origin))):
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width > 0 and height > 0:
                return origin.x, origin.y, width, height
        outer = wintypes.RECT()
        if self.user32.GetWindowRect(hwnd, ctypes.byref(outer)):
            return outer.left, outer.top, outer.right - outer.left, outer.bottom - outer.top
        return None

    def find_emulator_window(self):
        ctypes, wintypes = self._ctypes, self._wintypes
        user32 = self.user32
        found: list[dict] = []
        checked = 0
        own_pid = os.getpid()
        foreground = user32.GetForegroundWindow()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            # Tool windows são normalmente popups, barras flutuantes e helpers.
            GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x00000080
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if is_auxiliary_window(title.value, cls.value):
                return True
            pid, process = self._process_name(hwnd)
            if pid == own_pid:
                return True
            nonlocal checked
            checked += 1
            if is_emulator(title.value, cls.value, self.patterns, process):
                rect = self._client_rect(hwnd)
                if rect and rect[2] >= _MIN_EMU_W and rect[3] >= _MIN_EMU_H:
                    found.append({
                        "title": title.value,
                        "class": cls.value,
                        "process": process,
                        "pid": pid,
                        "rect": rect,
                        "hwnd": hwnd,
                    })
            return True

        user32.EnumWindows(enum_cb, 0)
        selected = choose_window(found, foreground=foreground, previous=self._selected_hwnd)
        if selected:
            self._selected_hwnd = selected["hwnd"]
            self.last_status = {**selected, "detected": True, "error": "",
                                "checked_windows": checked, "last_check": time.time()}
        else:
            self._selected_hwnd = None
            self.last_status = {"detected": False, "error": "Nenhuma janela de emulador encontrada.",
                                "checked_windows": checked, "last_check": time.time()}
        return selected

    def status(self) -> dict:
        return dict(self.last_status)

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
        `native.Handle`; em algumas versões esse Handle pertence ao controle
        WebView2 interno, então sempre o normalizamos para a janela raiz."""
        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle:
            try:
                handle = int(handle.ToInt64())
            except AttributeError:
                handle = int(handle)
            root = self.user32.GetAncestor(handle, 2)  # GA_ROOT
            return int(root or handle)
        found = self.user32.FindWindowW(None, title) or None
        if not found:
            return None
        root = self.user32.GetAncestor(found, 2)
        return int(root or found)

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


class WindowsOverlayInput:
    """Torna a janela do overlay passiva e registra a hotkey global.

    O modo passivo usa somente estilos nativos. Subclassificar o WndProc do
    WinForms com um callback Python parece simples, mas pode bloquear a thread
    da interface quando o WebView2 e o Python disputam o GIL. ``LAYERED`` mais
    ``TRANSPARENT`` mantém o click-through sem executar Python a cada mensagem.
    A hotkey usa uma thread com fila própria e confirma o registro antes de o
    aplicativo tornar a janela não interativa.
    """

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    HWND_TOPMOST = -1
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    LWA_ALPHA = 0x00000002

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._wintypes = wintypes
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.SetLayeredWindowAttributes.argtypes = [
            wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD,
        ]
        self.user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        self._hwnd = None
        self._original_exstyle = None
        self._passive = False
        self._hotkey_stop = threading.Event()
        self._hotkey_thread = None
        self._hotkey_thread_id = None
        self._hotkey_id = 0xD17
        self._hotkey_status = {"registered": False, "hotkey": "", "error": ""}
        self._hotkey_lock = threading.Lock()

    def _get_long(self, hwnd, index):
        fn = getattr(self.user32, "GetWindowLongPtrW", self.user32.GetWindowLongW)
        fn.argtypes = [self._wintypes.HWND, self._wintypes.INT]
        fn.restype = self._ctypes.c_ssize_t
        return int(fn(hwnd, index))

    def _set_long(self, hwnd, index, value):
        fn = getattr(self.user32, "SetWindowLongPtrW", self.user32.SetWindowLongW)
        fn.argtypes = [self._wintypes.HWND, self._wintypes.INT, self._ctypes.c_ssize_t]
        fn.restype = self._ctypes.c_ssize_t
        return int(fn(hwnd, index, value))

    def apply_passive(self, hwnd, expected_size=None, opacity=75):
        if not hwnd:
            return {"ok": False, "error": "HWND do overlay não encontrado."}
        try:
            # Defesa adicional: nunca altere um controle filho do WebView2.
            # WS_EX_TRANSPARENT nesse filho deixa a interface inteira visível,
            # mas todos os cliques atravessam o conteúdo.
            root = self.user32.GetAncestor(hwnd, 2)  # GA_ROOT
            hwnd = int(root or hwnd)
            # O resize do pywebview acontece de forma assíncrona. Nunca aplique
            # WS_EX_TRANSPARENT enquanto a janela ainda estiver no tamanho do
            # aplicativo principal: se o resize falhar, toda a interface fica
            # visível, porém nenhum campo recebe clique.
            if expected_size:
                rect = self._wintypes.RECT()
                if not self.user32.GetWindowRect(hwnd, self._ctypes.byref(rect)):
                    raise self._ctypes.WinError()
                width = max(0, int(rect.right - rect.left))
                height = max(0, int(rect.bottom - rect.top))
                get_dpi = getattr(self.user32, "GetDpiForWindow", None)
                dpi = int(get_dpi(hwnd)) if get_dpi else 96
                scale = max(1.0, dpi / 96.0)
                expected_w, expected_h = expected_size
                max_w = int(expected_w * scale) + 48
                max_h = int(expected_h * scale) + 48
                if width > max_w or height > max_h:
                    self.restore()
                    return {
                        "ok": False,
                        "passive": False,
                        "pending": True,
                        "error": "Aguardando a janela entrar no tamanho compacto.",
                    }
            if self._hwnd == int(hwnd) and self._passive:
                return {"ok": True, "passive": True}
            if self._hwnd != hwnd:
                self.restore()
                self._hwnd = int(hwnd)
            if self._original_exstyle is None:
                self._original_exstyle = self._get_long(hwnd, self.GWL_EXSTYLE)
            style = (self._original_exstyle | self.WS_EX_LAYERED |
                     self.WS_EX_TRANSPARENT | self.WS_EX_TOOLWINDOW |
                     self.WS_EX_NOACTIVATE)
            self._set_long(hwnd, self.GWL_EXSTYLE, style)
            applied = self._get_long(hwnd, self.GWL_EXSTYLE)
            required = self.WS_EX_TRANSPARENT | self.WS_EX_NOACTIVATE
            if (applied & required) != required:
                raise OSError("O Windows recusou os estilos passivos do overlay.")
            self.user32.SetWindowPos(
                hwnd, self.HWND_TOPMOST, 0, 0, 0, 0,
                self.SWP_NOSIZE | self.SWP_NOMOVE | self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW)
            # A janela principal permanece opaca e interativa. A transparência
            # real passa a existir somente no HUD compacto, evitando o bug do
            # WebView2 em que `transparent=True` remove toda a janela do hit-test.
            requested = max(30, min(85, int(opacity)))
            alpha_percent = 55 + round(requested * 0.47)  # 69% .. 95%
            alpha = max(1, min(255, round(255 * alpha_percent / 100)))
            if not self.user32.SetLayeredWindowAttributes(hwnd, 0, alpha, self.LWA_ALPHA):
                raise self._ctypes.WinError()
            self._passive = True
            return {"ok": True, "passive": True}
        except Exception as exc:
            self.restore()
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def restore(self):
        hwnd = self._hwnd
        if not hwnd:
            return
        try:
            if self._original_exstyle is not None:
                self._set_long(hwnd, self.GWL_EXSTYLE, self._original_exstyle)
                self.user32.SetWindowPos(
                    hwnd, self.HWND_TOPMOST, 0, 0, 0, 0,
                    self.SWP_NOSIZE | self.SWP_NOMOVE | self.SWP_NOACTIVATE |
                    self.SWP_SHOWWINDOW)
        except Exception:
            pass
        self._original_exstyle = None
        self._passive = False
        self._hwnd = None

    def stop_hotkey(self):
        self._hotkey_stop.set()
        thread = self._hotkey_thread
        thread_id = self._hotkey_thread_id
        if thread and thread_id:
            try:
                self.user32.PostThreadMessageW(thread_id, self.WM_QUIT, 0, 0)
            except Exception:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._hotkey_thread = None
        self._hotkey_thread_id = None
        with self._hotkey_lock:
            self._hotkey_status = {"registered": False, "hotkey": "", "error": ""}

    def start_hotkey(self, value, callback):
        self.stop_hotkey()
        try:
            modifiers, key = parse_hotkey(value)
        except ValueError as exc:
            with self._hotkey_lock:
                self._hotkey_status = {"registered": False, "hotkey": str(value or ""), "error": str(exc)}
            return {"ok": False, "error": str(exc)}

        self._hotkey_stop.clear()
        ready = threading.Event()

        def loop():
            self._hotkey_thread_id = int(self.kernel32.GetCurrentThreadId())
            ok = bool(self.user32.RegisterHotKey(None, self._hotkey_id, modifiers, key))
            if not ok:
                error = self._ctypes.WinError().strerror or "A combinação já está em uso."
                with self._hotkey_lock:
                    self._hotkey_status = {"registered": False, "hotkey": str(value), "error": error}
                ready.set()
                return
            with self._hotkey_lock:
                self._hotkey_status = {"registered": True, "hotkey": str(value), "error": ""}
            ready.set()
            msg = self._wintypes.MSG()
            try:
                while not self._hotkey_stop.is_set():
                    result = self.user32.GetMessageW(self._ctypes.byref(msg), None, 0, 0)
                    if result <= 0:
                        break
                    if msg.message == self.WM_HOTKEY and msg.wParam == self._hotkey_id:
                        try:
                            callback()
                        except Exception:
                            pass
            finally:
                self.user32.UnregisterHotKey(None, self._hotkey_id)

        self._hotkey_thread = threading.Thread(target=loop, daemon=True, name="DigiTrackerHotkey")
        self._hotkey_thread.start()
        if not ready.wait(timeout=1.5):
            self.stop_hotkey()
            error = "O Windows não respondeu ao registro da hotkey."
            with self._hotkey_lock:
                self._hotkey_status = {"registered": False, "hotkey": str(value), "error": error}
            return {"ok": False, "error": error}
        status = self.status()
        return ({"ok": True, "hotkey": str(value)} if status.get("registered")
                else {"ok": False, "error": status.get("error") or
                      "A combinação de hotkey já está em uso."})

    def status(self):
        with self._hotkey_lock:
            return dict(self._hotkey_status)

    def close(self):
        self.stop_hotkey()
        self.restore()


class NullOverlayInput:
    """Fallback não-Windows: mantém o overlay funcional sem APIs nativas."""

    def apply_passive(self, _hwnd, expected_size=None, opacity=75):
        return {"ok": True, "passive": False, "fallback": True}

    def restore(self):
        pass

    def start_hotkey(self, _value, _callback):
        return {"ok": False, "error": "Hotkey global disponível somente no Windows."}

    def stop_hotkey(self):
        pass

    def status(self):
        return {"registered": False, "hotkey": "", "error": ""}

    def close(self):
        pass


def create_overlay_input():
    return WindowsOverlayInput() if sys.platform == "win32" else NullOverlayInput()


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


# Uma linha de `xwininfo -root -tree`:
#   0x1400007 "Dolphin 5.0 | JIT64": ("dolphin-emu" "dolphin-emu")  1280x720+320+140  +320+140
# A PRIMEIRA geometria é relativa ao pai (pode ser um frame do mutter); a
# SEGUNDA (+absX+absY) é a absoluta na tela — é a que interessa para grudar.
_TREE_RE = re.compile(
    r'(0x[0-9a-f]+)\s+"([^"]*)":\s+\(([^)]*)\)\s+'
    r'(\d+)x(\d+)[+-]-?\d+[+-]-?\d+\s+\+(-?\d+)\+(-?\d+)'
)


def parse_tree(output: str) -> list[dict]:
    """Janelas do `xwininfo -root -tree`: [{id, title, cls, rect}] com `rect` =
    (absX, absY, w, h). Funciona no GNOME/Wayland, onde `_NET_CLIENT_LIST` vem
    vazio, e pega janelas reparentadas (emuladores com decoração)."""
    out = []
    for m in _TREE_RE.finditer(output or ""):
        wid, title, cls_raw, w, h, ax, ay = m.groups()
        cls = " ".join(re.findall(r'"([^"]*)"', cls_raw))
        out.append({"id": wid, "title": title, "cls": cls,
                    "rect": (int(ax), int(ay), int(w), int(h))})
    return out


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
        self.last_status = {"detected": False, "error": "Ainda não verificado."}

    @staticmethod
    def _run(cmd) -> str:
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=3
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def find_emulator_window(self):
        # `xwininfo -root -tree` numa chamada só: funciona no GNOME/Wayland (onde
        # _NET_CLIENT_LIST vem vazio) e pega emuladores decorados (reparentados
        # sob o frame do mutter). Filtra janelas pequenas — helper/popup/tray.
        for w in parse_tree(self._run(["xwininfo", "-root", "-tree"])):
            _ax, _ay, ww, hh = w["rect"]
            if ww < _MIN_EMU_W or hh < _MIN_EMU_H:
                continue
            if is_emulator(w["title"], w["cls"], self.patterns):
                found = {"title": w["title"], "class": w["cls"], "process": "",
                         "rect": w["rect"], "hwnd": None}
                self.last_status = {**found, "detected": True, "error": "",
                                    "last_check": time.time()}
                return found
        self.last_status = {"detected": False, "error": "Nenhuma janela de emulador encontrada.",
                            "last_check": time.time()}
        return None

    def status(self) -> dict:
        return dict(self.last_status)

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
