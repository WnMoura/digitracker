"""DigiTracker — engine principal.

Abre uma janela pywebview always-on-top / sem moldura, serve a UI
(HTML/CSS/JS) por um servidor estático interno e expõe a lógica de backend
para o frontend via `js_api`. Um thread de sincronização consulta a
RetroAchievements a cada 30s e mantém o estado de cada jogo em memória.
"""

from __future__ import annotations

import base64
import copy
import ctypes
import io
import json
import os
import posixpath
import re
import sys
import threading
import time
import unicodedata
import uuid
import webbrowser
import zipfile
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# No GNOME/Wayland o backend nativo do GTK não honra bem uma janela sem moldura
# (o compositor ainda desenha decoração com fechar/maximizar) e o arraste
# programático do overlay não funciona. Rodar via XWayland (GDK_BACKEND=x11)
# resolve os dois: janela realmente sem moldura e overlay arrastável/posicionável.
# `setdefault` deixa o usuário sobrescrever, e é inócuo fora do Linux/GTK.
if sys.platform.startswith("linux"):
    os.environ.setdefault("GDK_BACKEND", "x11")

import webview

import emulator_tracker
import gamefaqs
import guide_ai
import guide_media
import guide_parser
import igdb
import image_fetch
import platform_providers
import rawg
import smart_guide
import steamgriddb
import updater
import web_image_search
from experience_api import ExperienceApi
from data_tools import DataToolsApi
from ra_api import RAClient, RAError, RARateLimited, fmt_date
from version import APP_VERSION


def _bundle_dir() -> Path:
    """Recursos read-only (ui/). Quando empacotado pelo PyInstaller, ficam na
    pasta temporária de extração (sys._MEIPASS)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _data_dir() -> Path:
    """Dados graváveis e persistentes (config/, assets/). Quando empacotado,
    ficam ao lado do executável — não na pasta temporária."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BUNDLE_DIR = _bundle_dir()
DATA_DIR = _data_dir()

UI_DIR = BUNDLE_DIR / "ui"
CONFIG_DIR = DATA_DIR / "config"
GAMES_DIR = CONFIG_DIR / "games"
CACHE_DIR = CONFIG_DIR / "cache"
SECRETS_PATH = CONFIG_DIR / "secrets.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
BADGES_DIR = DATA_DIR / "assets" / "badges"
ICONS_DIR = DATA_DIR / "assets" / "icons"   # ícones dos jogos (RA ImageIcon)
ART_DIR = DATA_DIR / "assets" / "art"       # capa, tela de título e screenshot
GUIDES_DIR = CONFIG_DIR / "guides"           # fontes, revisões e progresso do guia
GUIDE_MEDIA_DIR = DATA_DIR / "assets" / "guides"

# Artes que a RetroAchievements devolve por jogo, além do ícone. A capa
# identifica o jogo na biblioteca; a tela de título vira o cabeçalho; o
# screenshot vira o fundo ambiente do painel.
GAME_ART = {"box": "ImageBoxArt", "title": "ImageTitle", "ingame": "ImageIngame"}

SYNC_INTERVAL = 30      # segundos entre ciclos de sincronização
SYNC_MAX_INTERVAL = 600  # teto do backoff quando a API está limitando
SYNC_SPACING = 0.4       # pausa entre jogos, para não disparar tudo de uma vez

# De quanto em quanto tempo procuramos jogos novos na conta (uma chamada só).
AUTO_IMPORT_INTERVAL = 300

# De quanto em quanto tempo procuramos/seguimos a janela do emulador. A detecção
# é uma chamada só de xwininfo, barata — um intervalo curto deixa o overlay
# acompanhar o emulador de forma natural quando ele é movido/redimensionado.
OVERLAY_INTERVAL = 1.2

DEFAULT_SETTINGS = {
    "auto_import": True,   # espelhar a conta: jogo novo entra sozinho
    "auto_overlay": True,           # grudar no emulador quando ele abrir
    "overlay_exit_fullscreen": False,  # mandar Alt+Enter ao ver fullscreen exclusivo
    "overlay_second_screen": False,    # levar o overlay para o monitor livre
    "overlay_fit_emulator": True,      # dimensionar o overlay conforme a janela do emulador
    "auto_check_updates": True,        # procurar releases estáveis ao iniciar
    "update_remind_until": 0,          # epoch: "Lembrar depois" adia o aviso
    "dismissed": [],       # slugs removidos à mão — não voltam sozinhos
    "emulators": [],       # sobrescreve a lista padrão de emuladores (opcional)
    "ai_provider": "",     # provedor do refino por IA (vazio = padrão)
    "ai_model": "",        # modelo específico (vazio = o padrão do provedor)
    "ai_base_url": "",     # endpoint próprio, para provedores compatíveis
    "smart_guide_auto": True,       # organizar novos guias após importar
    "smart_guide_consent": False,   # confirmação única de envio/custo da IA
    "guide_density": "comfortable", # comfortable | compact
    "ui_scale": 100,
    "reduced_motion": False,
    "compact_size_mode": "auto",  # auto | manual
    "compact_width": 320,   # tamanho manual do HUD mínimo
    "compact_height": 110,
    "compact_expanded_width": 420,
    "compact_expanded_height": 300,
    "compact_last": 2,      # quantas conquistas OBTIDAS mostrar no compacto
    "compact_next": 0,      # quantas PRÓXIMAS mostrar (0 = cabe o que couber)
    "compact_tab": "achievements",  # aba escolhida no HUD expandido
    "compact_view": "minimal",       # minimal | expanded | both
    "compact_corner": "auto",        # auto | top-right | bottom-right | top-left | bottom-left
    "compact_surfaces": {
        "version": 1,
        "summary": {"enabled": True, "corner": "top-right"},
        "details": {"enabled": False, "corner": "bottom-right"},
    },
    "compact_background_mode": "background",  # background | cover | title | solid
    "compact_hotkey": "ctrl+alt+g",
    "compact_edit_hotkey": "ctrl+alt+e",
    "compact_geometries": {},  # processo -> variante -> tamanho/offset relativo
    "compact_auto_expand": False,
    "compact_auto_collapse_seconds": 0,
}

# Limites do overlay compacto: não deixa encolher a ponto de nada caber, nem
# crescer tanto que deixe de ser um overlay.
COMPACT_MIN = (220, 64)
COMPACT_MAX = (520, 360)
COMPACT_MINIMAL_MIN = (260, 90)
COMPACT_MINIMAL_MAX = (380, 130)
COMPACT_EXPANDED_MIN = (340, 220)
COMPACT_EXPANDED_MAX = (560, 480)
COMPACT_BOTH_MIN = (340, 320)
COMPACT_BOTH_MAX = (560, 620)

# Quando "ajustar ao emulador" está ligado, o overlay ocupa esta fração da
# janela do emulador (preso a COMPACT_MIN/MAX) — cresce em jogo grande, encolhe
# em janela pequena.
OVERLAY_FIT_W = 0.22
OVERLAY_FIT_H = 0.14
OVERLAY_EXPANDED_W = 0.32
OVERLAY_EXPANDED_H = 0.40

ACCENTS = ["#D62839", "#F5C518", "#2DE2E6", "#27AE60"]

# Como a RetroAchievements classifica um desbloqueio. Não é dificuldade do jogo:
# hardcore = sem savestate/rewind/cheat (o único que conta para o Mastery);
# softcore = destravada no modo casual. Uma conquista cai em exatamente um dos
# dois, então os contadores somam o total.
DEFAULT_MODES = ("hardcore", "softcore")

NORMAL_SIZE = (1040, 680)
COMPACT_SIZE = (320, 110)    # fallback do HUD mínimo
COMPACT_EXPANDED_SIZE = (420, 300)
COMPACT_MARGIN = 16         # distância do canto da área cliente


# ---------------------------------------------------------------------------- #
# Utilidades
# ---------------------------------------------------------------------------- #
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", text) or "jogo"


def adaptive_normal_size() -> tuple[int, int]:
    """Usa 90% da área útil do monitor principal, limitado ao canvas console.

    O fallback mantém os testes e plataformas não-Windows independentes das
    APIs de monitor. A DPI awareness é ativada antes desta consulta no boot.
    """
    usable_w, usable_h = 1440, 900
    if sys.platform == "win32":
        try:
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                usable_w = max(1, rect.right - rect.left)
                usable_h = max(1, rect.bottom - rect.top)
        except (AttributeError, OSError):
            pass
    return min(1600, round(usable_w * .90)), min(960, round(usable_h * .90))


def load_secrets() -> dict | None:
    if not SECRETS_PATH.exists():
        return None
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def load_settings() -> dict:
    """Preferências do app. Sempre devolve as chaves padrão preenchidas."""
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["dismissed"] = []
    settings["emulators"] = []
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return settings
    if isinstance(saved, dict):
        settings["auto_import"] = bool(saved.get("auto_import", True))
        settings["auto_overlay"] = bool(saved.get("auto_overlay", True))
        for chave in ("overlay_exit_fullscreen", "overlay_second_screen", "smart_guide_auto",
                      "smart_guide_consent", "reduced_motion"):
            settings[chave] = bool(saved.get(chave, settings[chave]))
        settings["overlay_fit_emulator"] = bool(saved.get("overlay_fit_emulator", True))
        settings["auto_check_updates"] = bool(saved.get("auto_check_updates", True))
        remind = saved.get("update_remind_until", 0)
        if isinstance(remind, (int, float)) and not isinstance(remind, bool):
            settings["update_remind_until"] = float(remind)
        for key in ("dismissed", "emulators"):
            value = saved.get(key)
            if isinstance(value, list):
                settings[key] = [str(s) for s in value]
        for key in ("ai_provider", "ai_model", "ai_base_url"):
            value = saved.get(key)
            if isinstance(value, str):
                settings[key] = value
        density = saved.get("guide_density")
        if density in ("comfortable", "compact"):
            settings["guide_density"] = density
        scale = saved.get("ui_scale")
        if isinstance(scale, (int, float)) and not isinstance(scale, bool):
            settings["ui_scale"] = max(80, min(140, int(scale)))
        for key in ("compact_width", "compact_height", "compact_expanded_width",
                    "compact_expanded_height", "compact_last", "compact_next"):
            value = saved.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                settings[key] = int(value)
        settings["compact_width"] = max(COMPACT_MINIMAL_MIN[0], min(COMPACT_MINIMAL_MAX[0], settings["compact_width"]))
        settings["compact_height"] = max(COMPACT_MINIMAL_MIN[1], min(COMPACT_MINIMAL_MAX[1], settings["compact_height"]))
        settings["compact_expanded_width"] = max(COMPACT_EXPANDED_MIN[0], min(COMPACT_EXPANDED_MAX[0], settings["compact_expanded_width"]))
        settings["compact_expanded_height"] = max(COMPACT_EXPANDED_MIN[1], min(COMPACT_EXPANDED_MAX[1], settings["compact_expanded_height"]))
        size_mode = saved.get("compact_size_mode")
        if size_mode in ("auto", "manual"):
            settings["compact_size_mode"] = size_mode
        tab = saved.get("compact_tab") or saved.get("compact_content")
        if tab in ("achievements", "guide"):
            settings["compact_tab"] = tab
        view = saved.get("compact_view")
        if view in ("minimal", "expanded", "both"):
            settings["compact_view"] = view
        corner = saved.get("compact_corner")
        if corner in ("auto", "top-right", "bottom-right", "top-left", "bottom-left"):
            settings["compact_corner"] = corner
        background_mode = saved.get("compact_background_mode")
        if background_mode in ("background", "cover", "title", "solid"):
            settings["compact_background_mode"] = background_mode
        hotkey = saved.get("compact_hotkey")
        if isinstance(hotkey, str) and hotkey.strip():
            settings["compact_hotkey"] = hotkey.strip().lower()
        edit_hotkey = saved.get("compact_edit_hotkey")
        if isinstance(edit_hotkey, str) and edit_hotkey.strip():
            settings["compact_edit_hotkey"] = edit_hotkey.strip().lower()
        geometries = saved.get("compact_geometries")
        if isinstance(geometries, dict):
            settings["compact_geometries"] = geometries
        # v0.9: os dois HUDs passaram a ser janelas independentes. A estrutura
        # nova é derivada do visual antigo quando ainda não existir, de modo
        # que uma atualização preserve exatamente o que estava habilitado.
        surfaces = saved.get("compact_surfaces")
        if not isinstance(surfaces, dict):
            surfaces = {
                "version": 1,
                "summary": {
                    "enabled": settings["compact_view"] in ("minimal", "both"),
                    "corner": (settings["compact_corner"]
                               if settings["compact_corner"] != "auto" else "top-right"),
                },
                "details": {
                    "enabled": settings["compact_view"] in ("expanded", "both"),
                    "corner": "bottom-right",
                },
            }
        normalized = {"version": 1}
        for surface, default_corner in (("summary", "top-right"),
                                        ("details", "bottom-right")):
            raw = surfaces.get(surface) if isinstance(surfaces.get(surface), dict) else {}
            corner = raw.get("corner", default_corner)
            if corner not in ("auto", "top-right", "bottom-right", "top-left", "bottom-left"):
                corner = default_corner
            normalized[surface] = {
                "enabled": bool(raw.get("enabled", surface == "summary")),
                "corner": corner,
            }
        # Nunca grave uma configuração impossível de recuperar pela hotkey.
        if not (normalized["summary"]["enabled"] or normalized["details"]["enabled"]):
            normalized["summary"]["enabled"] = True
        settings["compact_surfaces"] = normalized
        settings["compact_auto_expand"] = bool(saved.get("compact_auto_expand", False))
        collapse = saved.get("compact_auto_collapse_seconds")
        if isinstance(collapse, (int, float)) and not isinstance(collapse, bool):
            settings["compact_auto_collapse_seconds"] = max(0, min(60, int(collapse)))
    return settings


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_game_file(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _normalize_text(text: str) -> str:
    """Normaliza para casamento de texto: sem acentos, minúsculo, espaços
    colapsados. Usado para achar títulos de conquistas dentro do PDF."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


def _move_after_resize(win, pos, settle: float = 0.1, tries: int = 4) -> None:
    """Move a janela para `pos`, reconferindo até assentar.

    O resize do GTK é assíncrono: se pedirmos a posição enquanto a janela ainda
    tem o tamanho antigo, o gerenciador limita o x para a janela não sair da
    tela (com 1920px de largura, um pedido de x=921 numa janela de 1040px vira
    x=880). Movemos JÁ e só esperamos+repetimos se a posição não bateu — assim,
    no caso comum (o move pega de primeira), não há atraso perceptível.
    """
    x, y = pos
    for _ in range(tries):
        win.move(x, y)
        try:
            if (round(win.x), round(win.y)) == (round(x), round(y)):
                return
        except Exception:
            return                      # sem leitura confiável: um move já basta
        time.sleep(settle)


def extract_pdf_text(source) -> str:
    """Extrai todo o texto de um PDF (página a página). `source` pode ser um
    caminho ou um stream binário (ex.: io.BytesIO). Requer `pypdf`."""
    from pypdf import PdfReader  # import tardio: só carrega ao usar o recurso

    reader = PdfReader(source)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    if _normalize_text(text) or not pdf_ocr_available():
        return text
    try:
        source.seek(0)
        return extract_pdf_text_ocr(source.read())
    except (AttributeError, OSError):
        return text


@lru_cache(maxsize=1)
def pdf_ocr_available() -> bool:
    """OCR é uma capacidade opcional: evita inflar o executável para quem não usa."""
    try:
        import fitz  # PyMuPDF
        import pytesseract
        pytesseract.get_tesseract_version()
        return bool(fitz and pytesseract)
    except Exception:
        return False


def extract_pdf_text_ocr(raw: bytes) -> str:
    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("OCR não está instalado neste ambiente.") from exc
    pages = []
    with fitz.open(stream=raw, filetype="pdf") as document:
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            pages.append(pytesseract.image_to_string(image, lang="por+eng"))
    return "\n".join(pages)


def annotate_pdf_pages(sections: list, raw: bytes) -> list:
    """Associa seções/blocos à página em que seu texto aparece, sem reescrever."""
    try:
        from pypdf import PdfReader
        page_texts = [_normalize_text(page.extract_text() or "")
                      for page in PdfReader(io.BytesIO(raw)).pages]
    except Exception:
        return sections
    last_page = 1
    for section in sections:
        title = _normalize_text(section.get("title", ""))
        match = next((index + 1 for index, text in enumerate(page_texts)
                      if title and title[:80] in text), last_page)
        section["page"] = match
        last_page = match
        for block in section.get("blocks") or []:
            needle = _normalize_text(block.get("text", ""))[:100]
            block_page = next((index + 1 for index, text in enumerate(page_texts)
                               if needle and needle in text), match)
            block["page"] = block_page
    return sections


# ---------------------------------------------------------------------------- #
# API exposta ao frontend (pywebview js_api)
# ---------------------------------------------------------------------------- #
class Api(ExperienceApi, DataToolsApi):
    def __init__(self):
        self._window = None
        self._overlay_windows: dict[str, object] = {}
        self._overlay_loaded: set[str] = set()
        self._overlay_hwnds: dict[str, int] = {}
        self._overlay_expected_sizes: dict[str, tuple[int, int]] = {}
        self._overlay_native_by_surface: dict[str, dict] = {}
        self._active_slug = ""
        self._client: RAClient | None = None
        self.state: dict[str, dict] = {}      # slug -> detalhe computado
        self.pending_import: dict | None = None
        self.bulk: dict | None = None         # progresso da importação em lote
        self.settings = load_settings()
        self.last_auto_import = 0.0           # timestamp da última varredura
        self.auto_imported: list[str] = []    # jogos trazidos sozinhos (avisa a UI)
        self.last_faq: dict | None = None     # último guia baixado (p/ refino IA)
        self._overlay = None                  # OverlayWatcher (grudar no emulador)
        self._overlay_size = None             # último tamanho aplicado ao overlay grudado
        self._igdb_token = None               # cache do token OAuth do IGDB (Twitch)
        self._tracker = None
        self._own_hwnd = None
        self.overlay_notice = ""              # aviso pendente para a interface
        self._overlay_error = ""
        self._overlay_last_check = 0.0
        self.index_building = False
        self._compact = False
        self._compact_state = "hidden"  # hidden | minimal | expanded | both
        self._compact_variant = self.settings.get("compact_view", "minimal")
        self._compact_edit_mode = False
        self._compact_edit_timer = None
        self._compact_expected_size: tuple[int, int] | None = None
        self._pre_compact_pos: tuple[int, int] | None = None
        self._pre_compact_size: tuple[int, int] | None = None
        self._normal_size = NORMAL_SIZE
        self._overlay_input = emulator_tracker.create_overlay_input()
        self._overlay_native_status = {"passive": False, "error": ""}
        self._compact_corner_actual = "top-right"
        self._hotkey_value = ""
        self._edit_hotkey_value = ""
        self._auto_collapse_timer = None
        self._art_status: dict[str, dict] = {}
        self._art_lock = threading.Lock()
        self._lock = threading.RLock()
        self._tips_ai_lock = threading.Lock()
        self._tips_ai_status = {
            "ok": True, "phase": "idle", "operation": "",
            "completed": 0, "total": 0, "message": "", "error": "",
        }
        self._smart_ai_lock = threading.Lock()
        self._smart_ai_status: dict[str, dict] = {}
        self._smart_ai_queue: list[str] = []
        self._merge_ai_status: dict[str, dict] = {}
        self._atlas_ai_status: dict[str, dict] = {}
        self._atlas_job_lock = threading.RLock()
        self._updates = updater.UpdateManager(APP_VERSION, sys.executable)
        self._guides = smart_guide.SmartGuideStore(GUIDES_DIR)
        self._guide_media = guide_media.GuideMediaLibrary(GUIDES_DIR, GUIDE_MEDIA_DIR)
        self._platforms = platform_providers.ProviderRegistry()

        for d in (GAMES_DIR, CACHE_DIR, BADGES_DIR, ICONS_DIR, ART_DIR):
            d.mkdir(parents=True, exist_ok=True)

        for game_path in GAMES_DIR.glob("*.json"):
            for source in self._guides.system_sources(game_path.stem):
                if source.get("status") == "running":
                    self._guides.update_system_source(
                        game_path.stem, source["id"], status="interrupted",
                        error="O aplicativo encerrou durante a análise. Tente novamente.")

        secrets = load_secrets()
        if secrets and secrets.get("username") and secrets.get("api_key"):
            self._client = RAClient(secrets["username"], secrets["api_key"], CACHE_DIR)
            self._platforms.register(platform_providers.RetroAchievementsProvider(self._client))
        self._init_experience(SETTINGS_PATH, DATA_DIR, UI_DIR)

    # ------------------------ status / configuração ------------------------- #
    def _platform_progress(self, game_id: int) -> dict:
        provider = self._platforms.get("retroachievements")
        if provider:
            return provider.game_progress(str(game_id))
        if self._client:  # compatibilidade com clientes falsos nos testes
            return self._client.get_game_info_and_user_progress(int(game_id))
        raise RAError("RetroAchievements não configurado.")

    @staticmethod
    def _surfaces_for_variant(variant: str) -> tuple[str, ...]:
        if variant == "expanded":
            return ("details",)
        if variant == "both":
            return ("summary", "details")
        return ("summary",)

    def _compact_surfaces_config(self) -> dict:
        raw = self.settings.get("compact_surfaces") or {}
        result = {"version": 1}
        for surface, default_corner in (("summary", "top-right"),
                                        ("details", "bottom-right")):
            item = raw.get(surface) if isinstance(raw.get(surface), dict) else {}
            result[surface] = {
                "enabled": bool(item.get("enabled", surface == "summary")),
                "corner": item.get("corner", default_corner),
            }
        if not (result["summary"]["enabled"] or result["details"]["enabled"]):
            result["summary"]["enabled"] = True
        return result

    def _enabled_compact_surfaces(self) -> tuple[str, ...]:
        config = self._compact_surfaces_config()
        return tuple(surface for surface in ("summary", "details")
                     if config[surface]["enabled"])

    def _legacy_variant_from_surfaces(self) -> str:
        enabled = set(self._enabled_compact_surfaces())
        if enabled == {"summary", "details"}:
            return "both"
        return "expanded" if enabled == {"details"} else "minimal"

    def set_active_game(self, slug: str) -> dict:
        slug = str(slug or "").strip()
        with self._lock:
            if slug and slug not in self.state:
                return {"ok": False, "error": "Jogo não encontrado."}
            self._active_slug = slug
        if slug and hasattr(self, "_experience"):
            self._experience.game_preference(self._experience_account(), slug, {"opened": True})
        self._notify_overlay_surfaces()
        return {"ok": True, "slug": slug}

    def get_compact_surface_state(self, surface: str) -> dict:
        surface = str(surface or "").lower()
        if surface not in ("summary", "details"):
            return {"ok": False, "error": "Superfície de overlay inválida."}
        with self._lock:
            slug = self._active_slug
            game = self.state.get(slug) if slug else None
            if game is None and self.state:
                game = next((item for item in self.state.values()
                             if not (item.get("mastery") or {}).get("complete")),
                            next(iter(self.state.values())))
                slug = game.get("slug", "")
                self._active_slug = slug
        config = self.get_compact_config()
        return {
            "ok": True,
            "surface": surface,
            "visible": bool(self._compact and surface in self._enabled_compact_surfaces()),
            "editing": bool(self._compact_edit_mode),
            "game": game,
            "config": config,
        }

    def get_app_state(self) -> dict:
        ai_provider, ai_label, ai_model = self._ai_summary()
        return {
            "configured": self._client is not None,
            "username": self._client.username if self._client else "",
            "index_ready": bool(self._client and self._client.index_is_fresh()),
            "index_building": self.index_building,
            "auto_import": self.settings["auto_import"],
            "auto_overlay": self.settings["auto_overlay"],
            "overlay_exit_fullscreen": self.settings["overlay_exit_fullscreen"],
            "overlay_second_screen": self.settings["overlay_second_screen"],
            "overlay_fit_emulator": self.settings["overlay_fit_emulator"],
            "auto_check_updates": self.settings["auto_check_updates"],
            "version": APP_VERSION,
            "compact": self._compact,
            "ai_ready": bool(self._ai_key()),   # só o fato, nunca a chave
            "ai_provider": ai_provider,
            "ai_provider_label": ai_label,
            "ai_model": ai_model,
            "covers_ready": bool(self._covers_key()),  # idem: só se há chave
            "smart_guide_auto": bool(self.settings.get("smart_guide_auto", True)),
            "smart_guide_consent": bool(self.settings.get("smart_guide_consent", False)),
            "guide_density": self.settings.get("guide_density", "comfortable"),
            "ui_scale": self.settings.get("ui_scale", 100),
            "reduced_motion": bool(self.settings.get("reduced_motion", False)),
            "compact_state": self._compact_state,
            "compact_tab": self.settings.get("compact_tab", "achievements"),
            "compact_view": self.settings.get("compact_view", "minimal"),
            "compact_surfaces": self._compact_surfaces_config(),
            "compact_editing": self._compact_edit_mode,
            "platform_providers": self._platforms.describe(),
            "pdf_ocr_available": pdf_ocr_available(),
        }

    def get_pdf_capabilities(self) -> dict:
        return {"ok": True, "text": True, "embedded_images": True,
                "ocr": pdf_ocr_available(),
                "ocr_hint": ("OCR local pronto." if pdf_ocr_available() else
                             "Para PDFs digitalizados, instale Tesseract, PyMuPDF e pytesseract.")}

    def _ai_summary(self) -> tuple[str, str, str]:
        provider = self.settings.get("ai_provider") or guide_ai.DEFAULT_PROVIDER
        info = guide_ai.PROVIDERS.get(provider) or guide_ai.PROVIDERS[guide_ai.DEFAULT_PROVIDER]
        model = self.settings.get("ai_model") or info["default_model"]
        return provider, info["label"], model

    # --------------------------- atualizações ------------------------------ #
    def set_auto_check_updates(self, value: bool) -> dict:
        value = bool(value)
        with self._lock:
            self.settings["auto_check_updates"] = value
            save_settings(self.settings)
        return {"ok": True, "auto_check_updates": value}

    def check_for_updates(self, force: bool = False) -> dict:
        """Consulta a release estável. A checagem automática respeita o toggle e
        o adiamento; a ação manual (`force=True`) ignora ambos."""
        force = bool(force)
        with self._lock:
            enabled = self.settings.get("auto_check_updates", True)
            remind_until = float(self.settings.get("update_remind_until") or 0)
        if not force and not getattr(sys, "frozen", False):
            return {"ok": True, "phase": "source", "current_version": APP_VERSION,
                    "update_available": False, "source_mode": True}
        if not force and (not enabled or time.time() < remind_until):
            return {
                "ok": True,
                "phase": "deferred" if enabled else "disabled",
                "current_version": APP_VERSION,
                "update_available": False,
                "source_mode": not getattr(sys, "frozen", False),
            }
        return self._updates.check()

    def defer_update(self, hours: int = 24) -> dict:
        try:
            hours = max(1, min(24 * 30, int(hours)))
        except (TypeError, ValueError):
            hours = 24
        until = time.time() + hours * 3600
        with self._lock:
            self.settings["update_remind_until"] = until
            save_settings(self.settings)
        return {"ok": True, "remind_until": until}

    def start_update_download(self) -> dict:
        return self._updates.start_download()

    def get_update_status(self) -> dict:
        return self._updates.status()

    def install_downloaded_update(self) -> dict:
        result = self._updates.prepare_install(os.getpid())
        if result.get("ok") and self._window:
            def close_after_reply():
                time.sleep(0.35)
                try:
                    self._window.destroy()
                except Exception:
                    pass
            threading.Thread(target=close_after_reply, daemon=True).start()
        return result

    def open_update_release(self) -> dict:
        status = self._updates.status()
        url = status.get("release_url") or "https://github.com/WnMoura/digitracker/releases/latest"
        if not str(url).startswith("https://github.com/WnMoura/digitracker/"):
            return {"ok": False, "error": "Endereço de release inválido."}
        try:
            webbrowser.open(str(url))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def confirm_update_boot(self) -> dict:
        """A UI e a ponte JS carregaram; agora é seguro remover o `.bak`."""
        updater.cleanup_backup(sys.executable)
        return {"ok": True}

    def save_secrets(self, username: str, api_key: str) -> dict:
        username = (username or "").strip()
        api_key = (api_key or "").strip()
        if not username or not api_key:
            return {"ok": False, "error": "Preencha username e API key."}
        client = RAClient(username, api_key, CACHE_DIR)
        try:
            client.validate()
        except RAError as exc:
            return {"ok": False, "error": str(exc)}
        SECRETS_PATH.write_text(
            json.dumps({"username": username, "api_key": api_key}, indent=2),
            encoding="utf-8",
        )
        if hasattr(self, "_companion_service"):
            self._companion_service.stop()
            with self._companion_ai_lock:
                self._companion_ai.clear()
                self._companion_generation += 1
        self._client = client
        self._platforms.register(platform_providers.RetroAchievementsProvider(client))
        self._kick_sync()
        self._ensure_index_async()
        self._schedule_auto_import()   # já traz a biblioteca inteira no login
        return {"ok": True}

    # ------------------------------ biblioteca ------------------------------ #
    def get_library(self) -> list[dict]:
        with self._lock:
            games = list(self.state.values())
        summaries = []
        for g in games:
            summaries.append(
                {
                    "slug": g["slug"],
                    "title": g["title"],
                    "platform": g["platform"],
                    "genre": g.get("genre", ""),
                    "year": g.get("year", ""),
                    "players": g.get("players", ""),
                    "provider_id": g.get("provider_id", "retroachievements"),
                    "icon": g.get("icon", ""),
                    "art": g.get("art", {}),
                    "art_meta": g.get("art_meta", {}),
                    "accent": g["accent"],
                    "palette": g.get("palette", {}),
                    "modes": g["modes"],
                    "mastery": g["mastery"],
                    "score": g.get("score", {}),
                    "playtime": g.get("playtime"),
                    "completion": g.get("completion", {}),
                    "completion_events": g.get("completion_events", {}),
                }
            )
        summaries.sort(key=lambda s: s["title"].lower())
        for summary in summaries:
            summary["preferences"] = self._experience.game_preference(self._experience_account(), summary["slug"])
            self._schedule_art_enrichment(summary["slug"])
        return summaries

    def get_game(self, slug: str) -> dict | None:
        self._schedule_art_enrichment(slug)
        with self._lock:
            return self.state.get(slug)

    def get_completion_events(self, slug: str = "", pending_only: bool = True) -> dict:
        with self._lock:
            games = ([self.state.get(slug)] if slug else list(self.state.values()))
            events = []
            for game in games:
                if not game:
                    continue
                for event in (game.get("completion_events") or {}).get("events", []):
                    if pending_only and not event.get("pending"):
                        continue
                    events.append({**event, "slug": game["slug"], "title": game["title"],
                                   "platform": game.get("platform", ""),
                                   "art": game.get("art", {}),
                                   "palette": game.get("palette", {})})
        events.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return {"ok": True, "events": events}

    def confirm_completion_event(self, slug: str, event_id: str) -> dict:
        path = GAMES_DIR / f"{slug}.json"
        game = load_game_file(path)
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        found = False
        for event in (game.get("completion_events") or {}).get("events", []):
            if event.get("id") == event_id:
                event["pending"] = False
                found = True
                break
        if not found:
            return {"ok": False, "error": "Celebração não encontrada."}
        path.write_text(json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._lock:
            detail = self.state.get(slug)
            if detail:
                for event in (detail.get("completion_events") or {}).get("events", []):
                    if event.get("id") == event_id:
                        event["pending"] = False
        return {"ok": True}

    def reopen_completion_event(self, slug: str, event_id: str = "") -> dict:
        with self._lock:
            game = self.state.get(slug)
            events = list((game.get("completion_events") or {}).get("events", [])) if game else []
        if not game:
            return {"ok": False, "event": {}, "game": {},
                    "error": "Jogo não encontrado."}
        event = next((item for item in events if item.get("id") == event_id), None) \
            if event_id else (events[-1] if events else None)
        if not event:
            # Saves muito antigos podem já estar 100% sem possuir o histórico
            # introduzido na v0.9. O replay é uma visualização: sintetizá-lo
            # não cria evento pendente nem modifica o arquivo do jogo.
            completion = game.get("completion") or {}
            mastery = game.get("mastery") or {}
            score = game.get("score") or {}
            kind = "mastery" if completion.get("mastery_complete") else "softcore"
            complete = (completion.get("mastery_complete") or
                        completion.get("softcore_complete"))
            if complete:
                event = {
                    "id": "", "kind": kind, "pending": False, "synthetic": True,
                    "achievements": (completion.get("hardcore") if kind == "mastery"
                                     else completion.get("earned")) or mastery.get("total", 0),
                    "points": (score.get("hardcore") if kind == "mastery"
                               else score.get("earned")) or 0,
                    "playtime": game.get("playtime"),
                    "date": (completion.get("mastery_date") if kind == "mastery"
                             else completion.get("softcore_date")) or "",
                }
        return {"ok": bool(event), "event": event or {}, "game": game,
                "error": "Celebração não encontrada." if not event else ""}

    def get_mastery_hall(self) -> dict:
        with self._lock:
            games = list(self.state.values())
        mastered = [g for g in games if (g.get("completion") or {}).get("mastery_complete")]
        completed = [g for g in games if (g.get("completion") or {}).get("softcore_complete")
                     and not (g.get("completion") or {}).get("mastery_complete")]
        def date_key(game):
            completion = game.get("completion") or {}
            return completion.get("mastery_date_raw") or completion.get("softcore_date_raw") or ""
        mastered.sort(key=date_key, reverse=True)
        completed.sort(key=date_key, reverse=True)
        return {"ok": True, "mastery": mastered, "softcore": completed}

    def set_compact_tab(self, slug: str, tab: str) -> dict:
        tab = str(tab or "").lower()
        if tab not in ("achievements", "guide"):
            return {"ok": False, "error": "Aba inválida."}
        path = GAMES_DIR / f"{slug}.json"
        game = load_game_file(path)
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        game.setdefault("compact_ui", {})["tab"] = tab
        path.write_text(json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._lock:
            if slug in self.state:
                self.state[slug]["compact_tab"] = tab
        return {"ok": True, "tab": tab}

    def delete_game(self, slug: str) -> dict:
        """Remove o jogo da biblioteca e o marca como dispensado, senão a
        importação automática o traria de volta no ciclo seguinte."""
        path = GAMES_DIR / f"{slug}.json"
        if path.exists():
            path.unlink()
        with self._lock:
            self.state.pop(slug, None)
            if slug not in self.settings["dismissed"]:
                self.settings["dismissed"].append(slug)
                save_settings(self.settings)
        return {"ok": True}

    def restore_game(self, slug: str) -> dict:
        """Desfaz o dispensar: o jogo volta a ser elegível para a importação
        automática (e reaparece na lista de importação)."""
        with self._lock:
            if slug in self.settings["dismissed"]:
                self.settings["dismissed"].remove(slug)
                save_settings(self.settings)
        return {"ok": True}

    # -------------------------- preferências -------------------------------- #
    def get_settings(self) -> dict:
        with self._lock:
            return dict(self.settings)

    def set_auto_import(self, value: bool) -> dict:
        value = bool(value)
        with self._lock:
            self.settings["auto_import"] = value
            save_settings(self.settings)
        if value:
            self._schedule_auto_import()
        return {"ok": True, "auto_import": value}

    def set_experience_preferences(self, smart_auto=None, consent=None,
                                   density: str = "", ui_scale=None,
                                   reduced_motion=None) -> dict:
        """Preferências do Guia Inteligente e da interface responsiva."""
        with self._lock:
            if smart_auto is not None:
                self.settings["smart_guide_auto"] = bool(smart_auto)
            if consent is not None:
                self.settings["smart_guide_consent"] = bool(consent)
            if density in ("comfortable", "compact"):
                self.settings["guide_density"] = density
            if ui_scale is not None:
                try:
                    self.settings["ui_scale"] = max(80, min(140, int(ui_scale)))
                except (TypeError, ValueError):
                    pass
            if reduced_motion is not None:
                self.settings["reduced_motion"] = bool(reduced_motion)
            save_settings(self.settings)
            result = {
                key: self.settings[key] for key in (
                    "smart_guide_auto", "smart_guide_consent", "guide_density",
                    "ui_scale", "reduced_motion",
                )
            }
        if result["smart_guide_auto"] and result["smart_guide_consent"] and self._ai_key():
            self._resume_pending_smart_guides()
        return {"ok": True, **result}

    def set_settings_session(self, section: str, payload: dict | None = None) -> dict:
        """Persiste preferências não sensíveis de uma sessão de Configurações.

        A UI mantém um rascunho por painel e só chama esta ponte ao confirmar
        "Salvar alterações". Credenciais continuam fora deste método e passam
        pelos setters protegidos de IA/fontes de imagem.
        """
        section = str(section or "").strip().lower()
        payload = payload if isinstance(payload, dict) else {}
        allowed = {
            "account": {"auto_check_updates"},
            "experience": {"smart_guide_auto", "smart_guide_consent",
                           "guide_density", "ui_scale", "reduced_motion"},
            "library": {"auto_import"},
            "overlay": {"auto_overlay", "overlay_exit_fullscreen",
                        "overlay_second_screen", "overlay_fit_emulator"},
            "compact": {"compact_width", "compact_height",
                        "compact_expanded_width", "compact_expanded_height",
                        "compact_last", "compact_next", "compact_size_mode",
                        "compact_corner", "compact_background_mode", "compact_view",
                        "compact_surfaces",
                        "compact_hotkey", "compact_edit_hotkey",
                        "compact_auto_expand", "compact_auto_collapse_seconds"},
        }
        if section not in allowed:
            return {"ok": False, "error": "Sessão de configurações desconhecida."}
        unknown = set(payload) - allowed[section]
        if unknown:
            return {"ok": False, "error": "Campo inválido na sessão."}

        with self._lock:
            for key, value in payload.items():
                if key in {
                    "auto_check_updates", "auto_import", "auto_overlay",
                    "overlay_exit_fullscreen", "overlay_second_screen",
                    "overlay_fit_emulator", "smart_guide_auto",
                    "smart_guide_consent", "reduced_motion",
                }:
                    self.settings[key] = bool(value)
                elif key == "guide_density":
                    if value not in ("comfortable", "compact"):
                        return {"ok": False, "error": "Densidade inválida."}
                    self.settings[key] = value
                elif key == "ui_scale":
                    try:
                        self.settings[key] = max(80, min(140, int(value)))
                    except (TypeError, ValueError):
                        return {"ok": False, "error": "Escala inválida."}
                elif key == "compact_size_mode":
                    if value not in ("auto", "manual"):
                        return {"ok": False, "error": "Modo de tamanho inválido."}
                    self.settings[key] = value
                elif key == "compact_corner":
                    if value not in ("auto", "top-right", "bottom-right", "top-left", "bottom-left"):
                        return {"ok": False, "error": "Canto do overlay inválido."}
                    self.settings[key] = value
                elif key == "compact_background_mode":
                    if value not in ("background", "cover", "title", "solid"):
                        return {"ok": False, "error": "Fundo do overlay inválido."}
                    self.settings[key] = value
                elif key == "compact_view":
                    if value not in ("minimal", "expanded", "both"):
                        return {"ok": False, "error": "Visual do overlay inválido."}
                    self.settings[key] = value
                    self._compact_variant = value
                    enabled = set(self._surfaces_for_variant(value))
                    surfaces = self._compact_surfaces_config()
                    surfaces["summary"]["enabled"] = "summary" in enabled
                    surfaces["details"]["enabled"] = "details" in enabled
                    self.settings["compact_surfaces"] = surfaces
                    if self._compact:
                        self._compact_state = value
                elif key == "compact_surfaces":
                    if not isinstance(value, dict):
                        return {"ok": False, "error": "Configuração dos HUDs inválida."}
                    normalized = {"version": 1}
                    for surface, default_corner in (("summary", "top-right"),
                                                    ("details", "bottom-right")):
                        raw = value.get(surface)
                        if not isinstance(raw, dict):
                            return {"ok": False, "error": f"Configuração do HUD {surface} inválida."}
                        corner = raw.get("corner", default_corner)
                        if corner not in ("auto", "top-right", "bottom-right", "top-left", "bottom-left"):
                            return {"ok": False, "error": "Canto do overlay inválido."}
                        normalized[surface] = {
                            "enabled": bool(raw.get("enabled")),
                            "corner": corner,
                        }
                    if not (normalized["summary"]["enabled"] or
                            normalized["details"]["enabled"]):
                        return {"ok": False, "error": "Ative pelo menos um HUD."}
                    self.settings["compact_surfaces"] = normalized
                    self.settings["compact_view"] = self._legacy_variant_from_surfaces()
                    self._compact_variant = self.settings["compact_view"]
                    if self._compact:
                        self._compact_state = self._compact_variant
                elif key in ("compact_hotkey", "compact_edit_hotkey"):
                    if not isinstance(value, str) or not value.strip():
                        return {"ok": False, "error": "Hotkey inválida."}
                    try:
                        emulator_tracker.parse_hotkey(value)
                    except ValueError as exc:
                        return {"ok": False, "error": str(exc)}
                    self.settings[key] = value.strip().lower()
                elif key == "compact_auto_expand":
                    self.settings[key] = bool(value)
                elif key == "compact_auto_collapse_seconds":
                    try:
                        self.settings[key] = max(0, min(60, int(value)))
                    except (TypeError, ValueError):
                        return {"ok": False, "error": "Tempo de recolhimento inválido."}
                elif key in {"compact_width", "compact_height", "compact_expanded_width",
                             "compact_expanded_height", "compact_last", "compact_next"}:
                    try:
                        number = int(value)
                    except (TypeError, ValueError):
                        return {"ok": False, "error": "Valor numérico inválido."}
                    limits = {
                        "compact_width": COMPACT_MINIMAL_MIN,
                        "compact_height": COMPACT_MINIMAL_MIN,
                        "compact_expanded_width": COMPACT_EXPANDED_MIN,
                        "compact_expanded_height": COMPACT_EXPANDED_MIN,
                        "compact_last": (0, 3),
                        "compact_next": (0, 10),
                    }[key]
                    if key == "compact_width":
                        self.settings[key] = max(COMPACT_MINIMAL_MIN[0], min(COMPACT_MINIMAL_MAX[0], number))
                    elif key == "compact_height":
                        self.settings[key] = max(COMPACT_MINIMAL_MIN[1], min(COMPACT_MINIMAL_MAX[1], number))
                    elif key == "compact_expanded_width":
                        self.settings[key] = max(COMPACT_EXPANDED_MIN[0], min(COMPACT_EXPANDED_MAX[0], number))
                    elif key == "compact_expanded_height":
                        self.settings[key] = max(COMPACT_EXPANDED_MIN[1], min(COMPACT_EXPANDED_MAX[1], number))
                    else:
                        self.settings[key] = max(limits[0], min(limits[1], number))
            save_settings(self.settings)
            result = {key: self.settings[key] for key in allowed[section]
                      if key in self.settings}

        if section == "library" and result.get("auto_import"):
            self._schedule_auto_import()
        if section == "experience" and result.get("smart_guide_auto") \
                and result.get("smart_guide_consent") and self._ai_key():
            self._resume_pending_smart_guides()
        if section == "compact" and self._compact:
            self._layout_overlay_surfaces(self._current_overlay_rect(), resize=True)
            self._configure_native_overlay()
            self._notify_ui_compact(True, self._compact_state)
        if section == "compact":
            self._start_overlay_hotkeys()
        return {"ok": True, "section": section, **result}

    # --------------------------- busca (wizard p1) -------------------------- #
    def search_games(self, query: str) -> dict:
        if not self._client:
            return {"ready": False, "building": False, "results": [], "error": "Não configurado."}
        if not self._client.index_is_fresh():
            self._ensure_index_async()
            return {"ready": False, "building": True, "results": []}
        return {"ready": True, "building": False, "results": self._client.search_games(query)}

    def _ensure_index_async(self):
        if self.index_building or not self._client:
            return
        if self._client.index_is_fresh():
            return
        self.index_building = True

        def build():
            try:
                self._client.build_games_index()
            except RAError:
                pass
            finally:
                self.index_building = False

        threading.Thread(target=build, daemon=True).start()

    # ----------------- importar a biblioteca inteira (lote) ----------------- #
    def list_played_games(self) -> dict:
        """Todos os jogos em que você já tem conquista na RetroAchievements,
        para escolher quais trazer de uma vez (em vez de um por um no wizard)."""
        if not self._client:
            return {"ok": False, "error": "Não configurado.", "games": []}
        try:
            raw = self._client.get_user_completion_progress()
        except RAError as exc:
            return {"ok": False, "error": str(exc), "games": []}

        existing = {p.stem for p in GAMES_DIR.glob("*.json")}
        games = []
        for g in raw:
            title = g.get("Title") or ""
            max_possible = int(g.get("MaxPossible") or 0)
            awarded = int(g.get("NumAwarded") or 0)
            hardcore = int(g.get("NumAwardedHardcore") or 0)
            games.append({
                "id": int(g.get("GameID") or 0),
                "title": title,
                "console": g.get("ConsoleName") or "",
                "total": max_possible,
                "earned": awarded,
                "hardcore": hardcore,
                # destravadas só no modo casual: é o que precisa ser refeito
                "softcore_only": max(0, awarded - hardcore),
                "award": g.get("HighestAwardKind") or "",
                "last_played": g.get("MostRecentAwardedDate") or "",
                "imported": slugify(title) in existing,
            })
        games.sort(key=lambda g: (-g["earned"], g["title"].lower()))
        return {"ok": True, "games": games}

    def start_bulk_import(self, game_ids: list, auto: bool = False) -> dict:
        """Importa vários jogos de uma vez, em background. Cada jogo entra com o
        walkthrough na ordem nativa do RA (`DisplayOrder`) — a mesma coisa que o
        wizard faria com "Salvar" direto. Os ícones das conquistas ficam para o
        fim, porque são centenas de downloads e não travam o uso do app."""
        if not self._client:
            return {"ok": False, "error": "Não configurado."}
        with self._lock:
            if self.bulk and self.bulk.get("running"):
                return {"ok": False, "error": "Já existe uma importação em andamento."}
            ids = [int(i) for i in (game_ids or [])]
            if not ids:
                return {"ok": False, "error": "Selecione ao menos um jogo."}
            self.bulk = {
                "running": True, "done": 0, "total": len(ids),
                "current": "", "imported": [], "errors": [], "phase": "games",
                "badges_done": 0, "badges_total": 0, "auto": bool(auto),
            }
        threading.Thread(target=self._bulk_worker, args=(ids,), daemon=True).start()
        return {"ok": True, "total": len(ids)}

    def get_bulk_status(self) -> dict:
        with self._lock:
            status = dict(self.bulk) if self.bulk else {"running": False}
            # jogos que entraram sozinhos desde a última consulta da UI
            status["auto_imported"] = self.auto_imported
            self.auto_imported = []
            status["overlay_notice"] = self.overlay_notice   # ex.: fullscreen exclusivo
            self.overlay_notice = ""
        return status

    # ------------------ importação automática (espelha a conta) ------------- #
    def _schedule_auto_import(self):
        """Roda a varredura em background, sem travar quem chamou (login, boot,
        toggle da preferência)."""
        threading.Thread(target=self._auto_import_scan, daemon=True).start()

    def _auto_import_scan(self) -> int:
        """Procura jogos em que você já tem conquistas mas que não estão na
        biblioteca, e os importa — é o que faz o app espelhar a conta em vez de
        exigir cadastro manual. Jogos removidos à mão ficam de fora.

        Devolve quantos entraram. Uma chamada de API por varredura.
        """
        with self._lock:
            if not self.settings["auto_import"] or not self._client:
                return 0
            if self.bulk and self.bulk.get("running"):
                return 0                     # já há importação em andamento
            self.last_auto_import = time.time()
            dismissed = set(self.settings["dismissed"])

        try:
            played = self._client.get_user_completion_progress()
        except RAError:
            return 0

        existing = {p.stem for p in GAMES_DIR.glob("*.json")}
        novos = []
        for g in played:
            title = g.get("Title") or ""
            if not title or int(g.get("NumAwarded") or 0) <= 0:
                continue                     # sem conquista: nem começou
            slug = slugify(title)
            if slug in existing or slug in dismissed:
                continue
            novos.append(int(g.get("GameID") or 0))

        if not novos:
            return 0

        res = self.start_bulk_import(novos, auto=True)
        return len(novos) if res.get("ok") else 0

    def _bulk_worker(self, ids: list):
        pending_badges: list[tuple[str, str]] = []   # (slug, badge)
        for game_id in ids:
            try:
                slug, badges = self._import_one(game_id)
                with self._lock:
                    self.bulk["imported"].append(slug)
                pending_badges.extend(badges)
            except RAError as exc:
                with self._lock:
                    self.bulk["errors"].append(f"#{game_id}: {exc}")
            except Exception as exc:                 # nunca deixa o worker morrer calado
                with self._lock:
                    self.bulk["errors"].append(f"#{game_id}: {exc}")
            finally:
                with self._lock:
                    self.bulk["done"] += 1
            time.sleep(SYNC_SPACING)

        # 2ª fase: ícones das conquistas (centenas de arquivos pequenos)
        with self._lock:
            self.bulk["phase"] = "badges"
            self.bulk["badges_total"] = len(pending_badges)
        for slug, badge in pending_badges:
            self._client.download_badge(badge, BADGES_DIR / slug / f"{badge}.png")
            with self._lock:
                self.bulk["badges_done"] += 1

        with self._lock:
            self.bulk["running"] = False
            self.bulk["phase"] = "done"
            if self.bulk.get("auto"):
                # a UI avisa que estes entraram sozinhos
                self.auto_imported.extend(self.bulk["imported"])

    def _import_one(self, game_id: int) -> tuple[str, list]:
        """Cria config/games/{slug}.json para um jogo. Devolve (slug, badges a
        baixar). Não baixa ícone de conquista — isso fica para a 2ª fase."""
        progress = self._platform_progress(int(game_id))
        title = progress.get("Title", "Jogo")
        slug = slugify(title)
        achievements = self._client.parse_achievements(progress)
        ach_list = sorted(
            achievements.values(),
            key=lambda a: (a.get("display_order", 10_000_000), a["id"]),
        )

        with self._lock:
            self.bulk["current"] = title

        icon_url = ""
        icon_path = progress.get("ImageIcon") or ""
        if icon_path and self._client.download_image(icon_path, ICONS_DIR / f"{slug}.png"):
            icon_url = f"/assets/icons/{slug}.png"

        game = {
            "slug": slug,
            "title": title,
            "platform": progress.get("ConsoleName", ""),
            "icon": icon_url,
            "art": self._download_art(slug, progress),
            "accent": self._pick_accent(slug),
            "retroachievements_game_id": int(game_id),
            "provider_id": "retroachievements",
            "external_game_id": str(int(game_id)),
            "walkthrough": [{
                "step": 1,
                "area": "Ordem RetroAchievements",
                "achievements": [{"id": a["id"]} for a in ach_list],
            }],
            "achievements_meta": {
                str(a["id"]): {
                    "title": a["title"],
                    "desc": a["desc"],
                    "badge": a["badge"],
                    "points": a.get("points", 0),
                    "achievement_type": a.get("achievement_type", ""),
                }
                for a in ach_list
            },
            "guide": [],
        }
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # já popula o estado com o progresso que acabou de vir (sem nova chamada)
        self._apply_progress(game, achievements, progress)
        return slug, [(slug, a["badge"]) for a in ach_list if a["badge"]]

    # --------------------------- importar (wizard) -------------------------- #
    def import_game(self, game_id: int) -> dict:
        """Passo 1 -> 2: baixa a lista completa de conquistas e cacheia ícones."""
        if not self._client:
            return {"ok": False, "error": "Não configurado."}
        try:
            progress = self._platform_progress(int(game_id))
        except RAError as exc:
            return {"ok": False, "error": str(exc)}

        title = progress.get("Title", "Jogo")
        platform = progress.get("ConsoleName", "")
        slug = slugify(title)
        achievements = self._client.parse_achievements(progress)

        # ORDEM NATIVA DO RA (DisplayOrder) — já é uma ordem lógica/curatorial.
        ach_list = sorted(
            achievements.values(),
            key=lambda a: (a.get("display_order", 10_000_000), a["id"]),
        )
        # baixa badges para o cache local
        for a in ach_list:
            if a["badge"]:
                self._client.download_badge(
                    a["badge"], BADGES_DIR / slug / f"{a['badge']}.png"
                )

        # ícone do jogo (cache local) para exibir na lateral
        icon_url = ""
        icon_path = progress.get("ImageIcon") or ""
        if icon_path and self._client.download_image(icon_path, ICONS_DIR / f"{slug}.png"):
            icon_url = f"/assets/icons/{slug}.png"

        self.pending_import = {
            "slug": slug,
            "title": title,
            "platform": platform,
            "icon": icon_url,
            "art": self._download_art(slug, progress),
            "retroachievements_game_id": int(game_id),
            "provider_id": "retroachievements",
            "external_game_id": str(int(game_id)),
            "achievements_meta": {
                str(a["id"]): {
                    "title": a["title"],
                    "desc": a["desc"],
                    "badge": a["badge"],
                    "points": a.get("points", 0),   # usado p/ casar com o PDF do guia
                    "achievement_type": a.get("achievement_type", ""),
                }
                for a in ach_list
            },
        }
        return {
            "ok": True,
            "slug": slug,
            "title": title,
            "platform": platform,
            "icon": icon_url,
            "achievements": [
                {
                    "id": a["id"],
                    "title": a["title"],
                    "desc": a["desc"],
                    "badge_url": self._badge_url(slug, a["badge"]),
                }
                for a in ach_list
            ],
        }

    def save_game(self, payload: dict) -> dict:
        """Passo 2 final: grava config/games/{slug}.json com o walkthrough."""
        if not self.pending_import:
            return {"ok": False, "error": "Nenhuma importação em andamento."}
        slug = self.pending_import["slug"]
        steps = self._clean_walkthrough(payload.get("walkthrough", []))
        if not steps:
            return {"ok": False, "error": "Adicione ao menos uma etapa com conquistas."}

        accent = self._pick_accent(slug)

        game = {
            "slug": slug,
            "title": self.pending_import["title"],
            "platform": self.pending_import["platform"],
            "icon": self.pending_import.get("icon", ""),
            "art": self.pending_import.get("art", {}),
            "accent": payload.get("accent", accent),
            "retroachievements_game_id": self.pending_import["retroachievements_game_id"],
            "provider_id": self.pending_import.get("provider_id", "retroachievements"),
            "external_game_id": self.pending_import.get(
                "external_game_id", str(self.pending_import["retroachievements_game_id"])),
            "walkthrough": steps,
            "achievements_meta": self.pending_import["achievements_meta"],
            "guide": payload.get("guide") or [],   # dicas/tutoriais extraídos do PDF
        }
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        source_meta = dict(self.pending_import.get("guide_source") or {"source": "import"})
        pending_pdf = self.pending_import.get("guide_pdf_raw")
        if pending_pdf:
            try:
                media = self._guide_media.extract_pdf(slug, pending_pdf, source_meta.get("filename", ""))
                source_meta["media_ids"] = [item["id"] for item in media["images"]]
                source_meta["pages"] = media.get("pages", 0)
            except guide_media.GuideMediaError:
                pass
        self._capture_smart_source(game, source_meta)
        self.pending_import = None
        self._sync_game(game)  # popula o estado imediatamente
        return {"ok": True, "slug": slug}

    # --------------------- ordenar walkthrough pelo PDF --------------------- #
    # O PDF chega como base64 vindo de um <input type=file> do frontend — assim
    # quem abre o seletor de arquivos é o próprio WebView2 (na thread de UI).
    # Evitamos o diálogo nativo do pywebview (`create_file_dialog`), que no
    # Windows TRAVA ao ser chamado de dentro do contexto do bridge js_api.
    def order_by_pdf_data(self, b64: str, filename: str = "") -> dict:
        """Recebe o conteúdo do PDF (base64), faz o parsing estruturado do guia e
        devolve as conquistas na ordem do guia (seção de conquistas) + as seções
        de dicas/tutoriais para exibir no app. O que não casar volta em
        `missing_ids` para ajuste manual."""
        if not self.pending_import:
            return {"ok": False, "error": "Nenhuma importação em andamento."}
        try:
            raw = base64.b64decode((b64 or "").split(",", 1)[-1])
            text = extract_pdf_text(io.BytesIO(raw))
        except Exception as exc:  # base64 inválido, pypdf ausente, PDF corrompido…
            return {"ok": False, "error": f"Não foi possível ler o PDF: {exc}"}

        if not _normalize_text(text):
            return {
                "ok": False,
                "error": ("O PDF parece digitalizado e o OCR local não conseguiu extrair texto. "
                          "Instale Tesseract + PyMuPDF/pytesseract ou use um PDF com texto."),
                "diagnostic": "scanned_pdf", "ocr_available": pdf_ocr_available(),
            }

        parsed = guide_parser.parse_guide(text)
        order = guide_parser.order_from_guide(
            parsed, self.pending_import["achievements_meta"], text
        )
        # Seções de dicas/tutoriais (exclui a lista de conquistas, já mostrada
        # no walkthrough).
        guide_sections = [s for s in parsed["sections"] if not s.get("is_achievements")]
        annotate_pdf_pages(guide_sections, raw)

        order["ok"] = True
        order["filename"] = filename
        order["guide"] = guide_sections
        self.pending_import["guide_pdf_raw"] = raw
        self.pending_import["guide_source"] = {"source": "pdf", "filename": filename}
        return order

    # ---------------------- importar guia do GameFAQs ----------------------- #
    def gamefaqs_list(self, url: str) -> dict:
        """Guias disponíveis na URL colada (uma requisição)."""
        try:
            session = gamefaqs.create_session()
            return {"ok": True, "faqs": gamefaqs.list_faqs(session, url)}
        except gamefaqs.GameFAQsError as exc:
            return {"ok": False, "error": str(exc), "faqs": []}
        except Exception as exc:                        # rede/parsing inesperado
            return {"ok": False, "error": f"Falha ao consultar o GameFAQs: {exc}",
                    "faqs": []}

    def gamefaqs_import(self, url: str) -> dict:
        """Baixa o guia e devolve no MESMO formato de `order_by_pdf_data`, para
        o frontend reaproveitar o fluxo que já existe do PDF."""
        if not self.pending_import:
            return {"ok": False, "error": "Nenhuma importação em andamento."}
        try:
            session = gamefaqs.create_session()
            faq = gamefaqs.fetch_faq(session, url)
        except gamefaqs.GameFAQsError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao baixar o guia: {exc}"}

        text = faq["text"]
        parsed = guide_parser.parse_freeform(text)
        order = guide_parser.order_from_guide(
            parsed, self.pending_import["achievements_meta"], text
        )
        # guardado para o refino por IA, que reusa o mesmo texto sem rebaixar
        self.last_faq = {"title": faq["title"], "text": text, "url": url}
        self.pending_import["guide_source"] = {
            "source": "gamefaqs", "filename": faq["title"], "url": url,
        }

        order["ok"] = True
        order["filename"] = faq["title"] or "GameFAQs"
        order["guide"] = parsed["sections"]
        order["pages"] = faq["pages"]
        return order

    def gamefaqs_attach(self, slug: str, url: str) -> dict:
        """Anexa só as dicas de um guia a um jogo já salvo (não toca nas
        conquistas) — espelho de `attach_guide_pdf`."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        try:
            session = gamefaqs.create_session()
            faq = gamefaqs.fetch_faq(session, url)
        except gamefaqs.GameFAQsError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao baixar o guia: {exc}"}

        sections = guide_parser.parse_freeform(faq["text"])["sections"]
        if not sections:
            return {"ok": False, "error": "Não consegui extrair seções desse guia."}

        self.last_faq = {"title": faq["title"], "text": faq["text"], "url": url}
        game["guide"] = sections
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self._lock:
            detail = self.state.get(slug)
            if detail is not None:
                detail["guide"] = sections
        if detail is None:
            self._sync_game(game)
        self._capture_smart_source(game, {
            "source": "gamefaqs", "filename": faq["title"], "url": url,
        })
        return {"ok": True, "sections": len(sections), "filename": faq["title"]}

    # ------------------ refinar o guia com IA (opcional) -------------------- #
    # A chave de cada provedor é guardada separada, para trocar de provedor sem
    # perder a chave do anterior.
    @staticmethod
    def _key_field(provider: str) -> str:
        return f"{provider}_api_key"

    def get_ai_config(self) -> dict:
        """Configuração atual + catálogo de provedores para a UI montar o
        formulário. NUNCA devolve a chave em si, só se ela existe."""
        secrets = load_secrets() or {}
        with self._lock:
            provider = self.settings.get("ai_provider") or guide_ai.DEFAULT_PROVIDER
            model = self.settings.get("ai_model", "")
            base_url = self.settings.get("ai_base_url", "")
        providers = [
            {"id": pid, **{k: v for k, v in info.items()},
             "has_key": bool((secrets.get(self._key_field(pid)) or "").strip())}
            for pid, info in guide_ai.PROVIDERS.items()
        ]
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "providers": providers,
            "ai_ready": bool(self._ai_key(provider)),
        }

    def set_ai_config(self, provider: str, api_key: str = None,
                      model: str = "", base_url: str = "") -> dict:
        """Escolhe o provedor e, opcionalmente, grava a chave dele.

        `api_key=None` mantém a chave já salva (a UI não precisa reenviar);
        string vazia apaga."""
        provider = (provider or guide_ai.DEFAULT_PROVIDER).strip()
        if provider not in guide_ai.PROVIDERS:
            return {"ok": False, "error": f"Provedor desconhecido: {provider}"}

        if api_key is not None:
            secrets = load_secrets() or {}
            key = (api_key or "").strip()
            if key:
                secrets[self._key_field(provider)] = key
            else:
                secrets.pop(self._key_field(provider), None)
            SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SECRETS_PATH.write_text(json.dumps(secrets, indent=2), encoding="utf-8")

        with self._lock:
            self.settings["ai_provider"] = provider
            self.settings["ai_model"] = (model or "").strip()
            self.settings["ai_base_url"] = (base_url or "").strip()
            save_settings(self.settings)

        if self._ai_key(provider):
            self._resume_pending_smart_guides()

        return self.get_ai_config()

    def _ai_key(self, provider: str = "") -> str:
        provider = provider or self.settings.get("ai_provider") or guide_ai.DEFAULT_PROVIDER
        return ((load_secrets() or {}).get(self._key_field(provider)) or "").strip()

    def refine_guide_ai(self) -> dict:
        """Refina o último guia baixado usando a IA: ordem das conquistas mais
        fiel ao walkthrough e dicas curadas. Requer chave configurada."""
        if not self.pending_import:
            return {"ok": False, "error": "Nenhuma importação em andamento."}
        if not self.last_faq:
            return {"ok": False, "error": "Importe um guia do GameFAQs primeiro."}

        with self._lock:
            provider = self.settings.get("ai_provider") or guide_ai.DEFAULT_PROVIDER
            config = {
                "provider": provider,
                "model": self.settings.get("ai_model", ""),
                "base_url": self.settings.get("ai_base_url", ""),
            }
        config["api_key"] = self._ai_key(provider)
        if not config["api_key"]:
            label = guide_ai.PROVIDERS[provider]["label"]
            return {"ok": False, "error": f"Configure sua chave do {label} para usar a IA."}

        try:
            out = guide_ai.refine(
                self.last_faq["text"], self.pending_import["achievements_meta"], config
            )
        except guide_ai.GuideAIError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao refinar com IA: {exc}"}

        out["ok"] = True
        out["guide"] = out.pop("sections")
        return out

    # ---- IA nas dicas de um jogo JÁ SALVO (sem tocar em conquistas/ordem) --- #
    def _ai_config(self) -> dict:
        with self._lock:
            provider = self.settings.get("ai_provider") or guide_ai.DEFAULT_PROVIDER
            config = {
                "provider": provider,
                "model": self.settings.get("ai_model", ""),
                "base_url": self.settings.get("ai_base_url", ""),
            }
        config["api_key"] = self._ai_key(provider)
        return config

    def _process_game_tips(self, slug: str, op, progress=None) -> dict:
        """Aplica uma operação de IA (`op(sections, config) -> sections`) às dicas
        de um jogo salvo, substitui `game['guide']` e atualiza o estado. Comum ao
        refinar e ao traduzir. Não mexe em conquistas nem no walkthrough."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        sections = game.get("guide") or []
        if not sections:
            return {"ok": False, "error": "Este jogo não tem dicas importadas."}
        config = self._ai_config()
        if not config["api_key"]:
            label = guide_ai.PROVIDERS[config["provider"]]["label"]
            return {"ok": False, "error": f"Configure sua chave do {label} para usar a IA."}
        try:
            novas = op(sections, config, progress=progress) if progress else op(sections, config)
        except guide_ai.GuideAIError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Falha na IA: {exc}"}
        game["guide"] = novas
        path = GAMES_DIR / f"{slug}.json"
        temporary = path.with_suffix(".json.ai.tmp")
        try:
            temporary.write_text(
                json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": False, "error": f"Não foi possível salvar as dicas: {exc}"}
        with self._lock:
            detail = self.state.get(slug)
            if detail is not None:
                detail["guide"] = novas
        return {"ok": True, "sections": len(novas)}

    def refine_game_tips(self, slug: str) -> dict:
        """Cura as dicas de um jogo salvo com a IA (não toca em conquistas)."""
        return self._process_game_tips(slug, guide_ai.refine_tips)

    def translate_game_tips(self, slug: str) -> dict:
        """Traduz as dicas de um jogo salvo para português com a IA."""
        return self._process_game_tips(slug, guide_ai.translate)

    def get_game_tips_ai_status(self) -> dict:
        """Progresso da tradução/melhoria em background para a interface."""
        with self._tips_ai_lock:
            return dict(self._tips_ai_status)

    def start_game_tips_ai(self, slug: str, operation: str) -> dict:
        """Inicia tradução ou melhoria sem bloquear a ponte do pywebview."""
        operations = {
            "refine": (guide_ai.refine_tips, "Melhorando"),
            "translate": (guide_ai.translate, "Traduzindo"),
        }
        if operation not in operations:
            return {"ok": False, "error": "Operação de IA desconhecida."}
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        if not (game.get("guide") or []):
            return {"ok": False, "error": "Este jogo não tem dicas importadas."}
        config = self._ai_config()
        if not config["api_key"]:
            label = guide_ai.PROVIDERS[config["provider"]]["label"]
            return {"ok": False, "error": f"Configure sua chave do {label} para usar a IA."}

        with self._tips_ai_lock:
            if self._tips_ai_status.get("phase") == "running":
                return {**self._tips_ai_status, "ok": False,
                        "error": "Já existe uma operação de IA em andamento."}
            self._tips_ai_status = {
                "ok": True, "phase": "running", "operation": operation,
                "slug": slug,
                "completed": 0, "total": 0,
                "message": f"{operations[operation][1]}: preparando lotes…", "error": "",
            }

        threading.Thread(
            target=self._game_tips_ai_worker,
            args=(slug, operation, operations[operation][0], operations[operation][1]),
            daemon=True,
        ).start()
        return self.get_game_tips_ai_status()

    def _game_tips_ai_worker(self, slug: str, operation: str, op, verb: str) -> None:
        def progress(completed: int, total: int) -> None:
            with self._tips_ai_lock:
                self._tips_ai_status.update({
                    "completed": completed,
                    "total": total,
                    "message": (f"{verb} lote {min(completed + 1, total)} de {total}…"
                                if completed < total else f"{verb}: validando resultado…"),
                })

        try:
            result = self._process_game_tips(slug, op, progress=progress)
        except Exception as exc:
            result = {"ok": False, "error": f"Falha inesperada na tarefa de IA: {exc}"}
        with self._tips_ai_lock:
            if result.get("ok"):
                total = self._tips_ai_status.get("total", 0)
                self._tips_ai_status.update({
                    "ok": True, "phase": "success", "completed": total,
                    "sections": result.get("sections", 0),
                    "message": ("Dicas traduzidas com sucesso."
                                if operation == "translate" else "Dicas melhoradas com sucesso."),
                    "error": "",
                })
            else:
                self._tips_ai_status.update({
                    "ok": False, "phase": "error", "message": "",
                    "error": result.get("error") or "Falha ao processar as dicas com IA.",
                })

    # -------------------------- Guia Inteligente --------------------------- #
    def _capture_smart_source(self, game: dict, metadata: dict | None = None) -> dict:
        """Captura a fonte sem substituir o guia legado e agenda a curadoria."""
        sections = game.get("guide") or []
        if not sections:
            return {"ok": False, "error": "Este jogo não tem dicas importadas."}
        try:
            source = self._guides.ensure_source(
                game["slug"], game.get("title", ""), sections, metadata or {},
            )
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}
        self._maybe_schedule_smart_guide(game["slug"])
        return {"ok": True, "source_hash": source["hash"]}

    def _maybe_schedule_smart_guide(self, slug: str, force: bool = False) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game or not (game.get("guide") or []):
            return {"ok": False, "error": "Jogo ou guia não encontrado."}
        source = self._guides.source(slug)
        current = self._guides.current(slug)
        status = self._guides.status(slug)
        if status.get("phase") in {"running", "queued"}:
            return {"ok": True, **status}
        if not force and current.get("source_hash") == source.get("hash") \
                and current.get("provider") not in ("", "local"):
            return {"ok": True, **self._guides.set_status(slug, "ready", message="Guia Inteligente atualizado.")}
        if not force and status.get("phase") == "error":
            return {"ok": False, **status}
        if not self.settings.get("smart_guide_auto", True) and not force:
            return {"ok": True, **self._guides.set_status(slug, "ready", message="Organização automática desativada.")}
        if not self.settings.get("smart_guide_consent", False):
            return {"ok": False, **self._guides.set_status(
                slug, "awaiting_consent",
                message="Confirme o envio do guia e possíveis custos da IA.",
            )}
        config = self._ai_config()
        if not config["api_key"]:
            return {"ok": False, **self._guides.set_status(
                slug, "awaiting_configuration",
                message="Configure um provedor de IA para organizar automaticamente.",
            )}
        return self.start_smart_guide(slug, force=True)

    def _resume_pending_smart_guides(self) -> None:
        if not (self.settings.get("smart_guide_consent", False) and self._ai_key()):
            return
        for path in GAMES_DIR.glob("*.json"):
            game = load_game_file(path)
            if not game:
                continue
            slug = game["slug"]
            if self.settings.get("smart_guide_auto", True) and game.get("guide"):
                status = self._guides.status(game["slug"])
                current = self._guides.current(game["slug"])
                if status.get("phase") in {"awaiting_configuration", "awaiting_consent", "ready", "idle"} \
                        and current.get("provider") in ("", "local"):
                    self._maybe_schedule_smart_guide(slug)
            merge = self._guides.merge_status(slug)
            if merge.get("phase") in {"awaiting_configuration", "awaiting_consent"}:
                self.start_walkthrough_merge(slug, merge.get("source_ids") or [])
            for source in self._guides.system_sources(slug):
                if source.get("status") in {"awaiting_configuration", "awaiting_consent"}:
                    self._queue_atlas_source(slug, source, source.get("title") or "Sistema visual",
                                             source.get("replace_system_id") or "")

    def get_smart_guide(self, slug: str) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        if game.get("guide") and not self._guides.source(slug):
            self._capture_smart_source(game, {"source": "migration"})
        bundle = self._guides.bundle(slug)
        bundle["media"] = self._guide_media.list(slug)
        # Conquistas reais já obtidas funcionam como sinais de progresso sem
        # ler memória ou saves. A associação é conservadora: nome exato no bloco.
        with self._lock:
            state = self.state.get(slug) or {}
            earned_names = [a.get("name", "") for a in state.get("achievements", []) if a.get("earned")]
            earned_ids = {int(a.get("id") or 0) for a in state.get("achievements", [])
                          if a.get("earned")}
        external_completed = []
        normalized = [_normalize_text(name) for name in earned_names if name]
        current = bundle.get("current") or {}
        for chapter in current.get("chapters") or []:
            for block in chapter.get("blocks") or []:
                haystack = _normalize_text(f"{block.get('title', '')} {block.get('text', '')}")
                achievement_id = int(block.get("achievement_id") or 0)
                exact_id = achievement_id > 0 and achievement_id in earned_ids
                legacy_name = achievement_id <= 0 and any(
                    name and name in haystack for name in normalized)
                if block.get("type") == "achievement" and (exact_id or legacy_name):
                    external_completed.append(block.get("id"))
        if external_completed:
            effective = dict(bundle.get("progress") or {})
            effective["completed"] = sorted(set(effective.get("completed") or []) | set(external_completed))
            bundle["effective_progress"] = effective
            bundle["next_objective"] = self._guides.next_objective(current, effective)
        else:
            bundle["effective_progress"] = bundle.get("progress") or {}
        bundle["external_completed"] = external_completed
        bundle["walkthrough_sources"] = self._guides.walkthrough_sources(slug)
        bundle["merge_status"] = self.get_walkthrough_merge_status(slug)
        bundle["atlas_jobs"] = self._guides.system_sources(slug)
        bundle["atlas_drafts"] = [self._guides.atlas_draft(slug, item["id"])
                                  for item in bundle["atlas_jobs"]
                                  if item.get("status") == "suggested"]
        return bundle

    # ---------------- fontes múltiplas / consolidação da Jornada ---------- #
    def list_walkthrough_sources(self, slug: str) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado.", "sources": []}
        try:
            return {"ok": True, "sources": self._guides.walkthrough_sources(slug),
                    "merge": self.get_walkthrough_merge_status(slug)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc), "sources": []}

    def get_walkthrough_source(self, slug: str, source_id: str) -> dict:
        try:
            source = next((item for item in self._guides.walkthrough_sources(
                slug, include_sections=True) if item.get("id") == source_id), None)
            if not source:
                return {"ok": False, "error": "Fonte não encontrada."}
            return {"ok": True, "source": source}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _decode_upload(data: str, limit: int = 50 * 1024 * 1024) -> bytes:
        try:
            raw = base64.b64decode((data or "").split(",", 1)[-1], validate=True)
        except ValueError as exc:
            raise smart_guide.SmartGuideError("Arquivo codificado inválido.") from exc
        if not raw or len(raw) > limit:
            raise smart_guide.SmartGuideError("Arquivo vazio ou maior que 50 MB.")
        return raw

    def add_walkthrough_pdf(self, slug: str, data: str, filename: str = "") -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        try:
            raw = self._decode_upload(data)
            ok, error, sections = self._read_guide_sections(data)
            if not ok:
                return {"ok": False, "error": error}
            source = self._guides.add_walkthrough_source(
                slug, filename or "Guia em PDF", "pdf", sections,
                {"filename": filename}, raw=raw,
            )
            return {"ok": True, "source": source, "duplicate": bool(source.get("duplicate")),
                    "sources": self._guides.walkthrough_sources(slug)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def add_walkthrough_gamefaqs(self, slug: str, url: str) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        try:
            session = gamefaqs.create_session()
            faq = gamefaqs.fetch_faq(session, url)
            parsed = guide_parser.parse_freeform(faq.get("text") or "")
            source = self._guides.add_walkthrough_source(
                slug, faq.get("title") or "GameFAQs", "gamefaqs",
                parsed.get("sections") or [],
                {"filename": faq.get("title") or "", "url": url},
                text=faq.get("text") or "",
            )
            return {"ok": True, "source": source, "duplicate": bool(source.get("duplicate")),
                    "sources": self._guides.walkthrough_sources(slug)}
        except (gamefaqs.GameFAQsError, smart_guide.SmartGuideError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao importar o GameFAQs: {exc}"}

    def add_walkthrough_text(self, slug: str, title: str, text: str) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "Cole algum conteúdo do guia."}
        try:
            parsed = guide_parser.parse_freeform(text)
            source = self._guides.add_walkthrough_source(
                slug, title or "Texto colado", "text", parsed.get("sections") or [],
                {"filename": title or "Texto colado"}, text=text,
            )
            return {"ok": True, "source": source, "duplicate": bool(source.get("duplicate")),
                    "sources": self._guides.walkthrough_sources(slug)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def update_walkthrough_source(self, slug: str, source_id: str,
                                  title: str | None = None,
                                  enabled: bool | None = None) -> dict:
        try:
            changes = {}
            if title is not None:
                changes["title"] = title
            if enabled is not None:
                changes["enabled"] = bool(enabled)
            source = self._guides.update_walkthrough_source(slug, source_id, **changes)
            return {"ok": True, "source": source,
                    "sources": self._guides.walkthrough_sources(slug)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def remove_walkthrough_source(self, slug: str, source_id: str) -> dict:
        try:
            result = self._guides.remove_walkthrough_source(slug, source_id)
            return {"ok": True, **result, "sources": self._guides.walkthrough_sources(slug)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def start_walkthrough_merge(self, slug: str, source_ids: list | None = None) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        sources = self._guides.walkthrough_sources(slug, include_sections=True)
        requested = {str(item) for item in (source_ids or [])}
        selected = [source for source in sources
                    if (not requested and source.get("enabled", True)) or source.get("id") in requested]
        if not selected:
            return {"ok": False, "error": "Selecione ao menos uma fonte."}
        selected_ids = [source["id"] for source in selected]
        if not self.settings.get("smart_guide_consent", False):
            status = self._guides.set_merge_status(
                slug, "awaiting_consent", error="",
                message="Confirme o envio à IA nas Configurações.",
                source_ids=selected_ids)
            return {"ok": False, **status}
        config = self._ai_config()
        if not config.get("api_key"):
            status = self._guides.set_merge_status(
                slug, "awaiting_configuration", error="",
                message="Configure um provedor de IA para consolidar os guias.",
                source_ids=selected_ids)
            return {"ok": False, **status}
        with self._smart_ai_lock:
            running = self._merge_ai_status.get(slug) or {}
            if running.get("phase") == "running":
                return {"ok": False, **running, "error": "A consolidação já está em andamento."}
            task = {"ok": True, "slug": slug, "phase": "running", "completed": 0,
                    "total": 0, "source_ids": [s["id"] for s in selected],
                    "message": "Preparando as fontes…", "error": ""}
            self._merge_ai_status[slug] = task
        self._guides.set_merge_status(slug, "running", **{k: v for k, v in task.items()
                                      if k not in {"phase", "slug"}})
        threading.Thread(target=self._walkthrough_merge_worker,
                         args=(slug, selected), daemon=True).start()
        return dict(task)

    def _walkthrough_merge_worker(self, slug: str, sources: list[dict]) -> None:
        def progress(completed: int, total: int) -> None:
            with self._smart_ai_lock:
                task = self._merge_ai_status.setdefault(slug, {})
                task.update({"completed": completed, "total": total,
                             "message": ("Comparando as fontes…" if completed >= total
                                         else f"Organizando lote {completed + 1} de {total}…")})
                snapshot = dict(task)
            self._guides.set_merge_status(slug, "running", **{k: v for k, v in snapshot.items()
                                          if k not in {"phase", "slug"}})
        game = load_game_file(GAMES_DIR / f"{slug}.json") or {}
        config = self._ai_config()
        try:
            document, conflicts = guide_ai.generate_merged_guide(
                sources, game, config, progress=progress)
            preview = self._guides.save_merge_preview(
                slug, document, [source["id"] for source in sources], conflicts)
            blocking = sum(1 for item in conflicts if item.get("blocking"))
            status = self._guides.set_merge_status(
                slug, "awaiting_review", ok=True, error="",
                message=(f"Prévia pronta · {blocking} conflito(s) importante(s)." if blocking
                         else "Prévia pronta para publicar."),
                conflicts=len(conflicts), blocking=blocking,
                source_ids=preview["source_ids"])
        except Exception as exc:
            status = self._guides.set_merge_status(
                slug, "error", ok=False, message="", error=str(exc))
        with self._smart_ai_lock:
            self._merge_ai_status[slug] = {"slug": slug, **status}
        self._refresh_smart_bundle(slug)

    def get_walkthrough_merge_status(self, slug: str) -> dict:
        with self._smart_ai_lock:
            live = dict(self._merge_ai_status.get(slug) or {})
        status = live or self._guides.merge_status(slug)
        if status.get("phase") == "awaiting_review":
            preview = self._guides.merge_preview(slug)
            status = {**status, "conflicts": preview.get("conflicts") or [],
                      "preview": preview.get("document") or {}}
        return {"ok": status.get("phase") != "error", **status}

    def resolve_walkthrough_conflict(self, slug: str, conflict_id: str,
                                     choice: str) -> dict:
        try:
            preview = self._guides.resolve_merge_conflict(slug, conflict_id, choice)
            unresolved = sum(1 for item in preview.get("conflicts") or []
                             if item.get("blocking") and not item.get("resolution"))
            return {"ok": True, "conflicts": preview.get("conflicts") or [],
                    "unresolved": unresolved}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def publish_walkthrough_merge(self, slug: str) -> dict:
        try:
            config = self._ai_config()
            revision = self._guides.publish_merge_preview(
                slug, config.get("provider") or "ai", config.get("model") or "")
            self._refresh_smart_bundle(slug)
            return {"ok": True, "revision": revision}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def _guide_systems_payload(self, slug: str, document: dict | None = None) -> dict:
        current = document if isinstance(document, dict) else self._guides.current(slug)
        state = self._guides.system_state(slug)
        media = self._guide_media.list(slug)
        media_by_id = {item.get("id"): item for item in media}
        systems = []
        for raw_system in current.get("systems") or []:
            system = copy.deepcopy(raw_system)
            for node in system.get("nodes") or []:
                media_id = (state.get("node_media") or {}).get(
                    f"{system.get('id')}:{node.get('id')}", ""
                )
                node["media_id"] = media_id
                node["media"] = media_by_id.get(media_id) or {}
                node["is_goal"] = (state.get("goals") or {}).get(system.get("id")) == node.get("id")
            systems.append(system)
        return {
            "ok": True, "systems": systems,
            "approved": [item for item in systems if item.get("status") == "approved"],
            "suggested": [item for item in systems if item.get("status") == "suggested"],
            "state": state, "media": media,
            "sources": self._guides.system_sources(slug),
            "objective": self._guides.system_objective(current, state),
        }

    def _refresh_smart_bundle(self, slug: str) -> None:
        """Atualiza dashboard/HUD sem consultar novamente a plataforma."""
        bundle = self.get_smart_guide(slug)
        if not bundle.get("ok"):
            return
        with self._lock:
            if slug in self.state:
                self.state[slug]["smart_guide"] = bundle
        self._notify_overlay_surfaces()

    def get_guide_systems(self, slug: str) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado.", "systems": []}
        return self._guide_systems_payload(slug)

    def preview_guide_systems(self, slug: str, revision_id: str = "") -> dict:
        try:
            document = self._guides.revision(slug, revision_id)
            if not document:
                return {"ok": False, "error": "Revisão não encontrada.", "systems": []}
            result = self._guide_systems_payload(slug, document)
            result["revision_id"] = document.get("revision_id", "")
            return result
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc), "systems": []}

    def save_guide_system(self, slug: str, system: dict) -> dict:
        try:
            system = system if isinstance(system, dict) else {}
            existing_ids = {item.get("id") for item in
                            self._guides.current(slug).get("systems") or []}
            source_id = str(system.get("source_id") or "")
            if not source_id and (self._guides.source(slug).get("sections") or []):
                system = {**system, "source_id": "legacy-main"}
                source_id = "legacy-main"
            if not system.get("id") and not source_id:
                return {"ok": False, "error": "Anexe um PDF ou GameFAQs exclusivo antes de criar o sistema."}
            if source_id and source_id != "legacy-main" \
                    and not self._guides.system_source(slug, source_id):
                return {"ok": False, "error": "A fonte exclusiva deste sistema não foi encontrada."}
            if system.get("id") not in existing_ids and source_id == "legacy-main" \
                    and not (self._guides.source(slug).get("sections") or []):
                return {"ok": False, "error": "Novos sistemas exigem uma fonte exclusiva."}
            result = self._guides.save_system(slug, system)
            source_id = str((result.get("system") or {}).get("source_id") or "")
            if source_id and source_id != "legacy-main":
                self._guides.link_system_source(
                    slug, source_id, (result.get("system") or {}).get("id", ""))
            self._refresh_smart_bundle(slug)
            return {"ok": True, **result, **self._guide_systems_payload(slug)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def _ensure_atlas_document(self, game: dict) -> None:
        if self._guides.current(game["slug"]):
            return
        placeholder = smart_guide.from_legacy_sections(
            f"Jornada - {game.get('title') or 'Jogo'}", [{
                "title": "Jornada", "blocks": [{
                    "type": "note",
                    "text": "Adicione fontes gerais para criar o walkthrough consolidado.",
                }],
            }])
        self._guides.publish(game["slug"], placeholder, "", "local", "atlas-placeholder")

    def _queue_atlas_source(self, slug: str, source: dict, title: str,
                            replace_system_id: str = "") -> dict:
        if not (self.settings.get("smart_guide_consent", False) and self._ai_key()):
            phase = "awaiting_configuration" if not self._ai_key() else "awaiting_consent"
            saved = self._guides.update_system_source(
                slug, source["id"], status=phase, error="",
                replace_system_id=replace_system_id)
            self._refresh_smart_bundle(slug)
            return {"ok": True, "phase": phase, "source": saved,
                    "message": "Fonte salva. Configure a IA ou use o editor manual."}
        with self._atlas_job_lock:
            current = self._guides.system_source(slug, source["id"])
            if current.get("status") == "running":
                return {"ok": False, "error": "Esta fonte já está sendo analisada."}
            job_id = uuid.uuid4().hex
            task = {"ok": True, "phase": "running", "slug": slug,
                    "source_id": source["id"], "title": title, "job_id": job_id,
                    "replace_system_id": replace_system_id,
                    "message": "Analisando a fonte exclusiva do Atlas…", "error": ""}
            self._atlas_ai_status[f"{slug}:{source['id']}"] = task
            self._guides.update_system_source(slug, source["id"], status="running", error="",
                                              job_id=job_id, replace_system_id=replace_system_id)
        self._refresh_smart_bundle(slug)
        threading.Thread(target=self._atlas_source_worker,
                         args=(slug, source["id"], title, replace_system_id, job_id), daemon=True).start()
        return {**task, "source": source}

    def _atlas_source_worker(self, slug: str, source_id: str, title: str,
                             replace_system_id: str = "", job_id: str = "") -> None:
        game = load_game_file(GAMES_DIR / f"{slug}.json") or {}
        try:
            source = self._guides.system_source(slug, source_id, include_sections=True)
            system = guide_ai.generate_system_from_source(
                source, title, game, self._ai_config())
            if replace_system_id:
                system["id"] = replace_system_id
            with self._atlas_job_lock:
                draft = self._guides.save_atlas_draft(slug, source_id, system, job_id)
            system = draft["system"]
            saved = self._guides.system_source(slug, source_id)
            status = {"ok": True, "phase": "suggested", "slug": slug,
                      "source_id": source_id, "system": system, "source": saved,
                      "message": "Sistema pronto para revisão.", "error": ""}
        except Exception as exc:
            with self._atlas_job_lock:
                latest = self._guides.system_source(slug, source_id)
                if latest.get("job_id") != job_id or latest.get("status") != "running":
                    return  # Cancelled/retried work must never overwrite the new job.
                self._guides.update_system_source(slug, source_id, status="error", error=str(exc))
            status = {"ok": False, "phase": "error", "slug": slug,
                      "source_id": source_id, "message": "", "error": str(exc)}
        with self._smart_ai_lock:
            self._atlas_ai_status[f"{slug}:{source_id}"] = status
        self._refresh_smart_bundle(slug)

    def get_atlas_job(self, slug: str, source_id: str) -> dict:
        try:
            source = self._guides.system_source(slug, source_id)
            if not source:
                return {"ok": False, "error": "Fonte não encontrada."}
            return {"ok": True, "source": source, "phase": source.get("status", "idle"),
                    "draft": self._guides.atlas_draft(slug, source_id)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def retry_atlas_job(self, slug: str, source_id: str) -> dict:
        source = self.get_atlas_job(slug, source_id)
        if not source.get("ok"):
            return source
        value = source["source"]
        return self._queue_atlas_source(slug, value, value.get("title") or "Sistema visual",
                                        value.get("replace_system_id") or "")

    def cancel_atlas_job(self, slug: str, source_id: str) -> dict:
        try:
            with self._atlas_job_lock:
                self._guides.update_system_source(slug, source_id, status="cancelled", job_id="", error="")
            self._refresh_smart_bundle(slug)
            return {"ok": True}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def approve_atlas_job(self, slug: str, source_id: str, system: dict | None = None) -> dict:
        try:
            with self._atlas_job_lock:
                result = self._guides.approve_atlas_draft(slug, source_id, system)
            self._refresh_smart_bundle(slug)
            return {"ok": True, **result}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def create_guide_system_from_pdf(self, slug: str, title: str,
                                     data: str, filename: str = "") -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        try:
            raw = self._decode_upload(data)
            ok, error, sections = self._read_guide_sections(data)
            if not ok:
                return {"ok": False, "error": error}
            self._ensure_atlas_document(game)
            source = self._guides.add_system_source(
                slug, title or filename or "Sistema visual", "pdf", sections,
                {"filename": filename}, raw=raw)
            return self._queue_atlas_source(slug, source, title or filename or "Sistema visual")
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _atlas_faq_sections(faq: dict) -> list:
        pages = faq.get('page_records') or [{'number': 1, 'text': faq.get('text', '')}]
        sections = []
        for page in pages:
            parsed = guide_parser.parse_freeform('1. Página do guia\n\n' + page['text'], max_blocks=10000)
            for section in parsed.get('sections') or []:
                section['page'] = page['number']
                for block in section.get('blocks') or []:
                    if '— guia truncado:' in block.get('text', ''):
                        raise gamefaqs.GameFAQsError('A fonte excede o limite de processamento. Nenhuma importação parcial foi salva.')
                    block['page'] = page['number']
                sections.append(section)
        return sections

    def create_guide_system_from_gamefaqs(self, slug: str, title: str,
                                          url: str) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        try:
            session = gamefaqs.create_session()
            faq = gamefaqs.fetch_faq(session, url)
            parsed = guide_parser.parse_freeform(faq.get("text") or "")
            self._ensure_atlas_document(game)
            source = self._guides.add_system_source(
                slug, title or faq.get("title") or "Sistema visual", "gamefaqs",
                self._atlas_faq_sections(faq), {"filename": faq.get("title") or "", "url": url,
                    "pages": faq.get('pages', 1), "page_urls": [p['url'] for p in faq.get('page_records') or []]},
                text=faq.get("text") or "")
            return self._queue_atlas_source(
                slug, source, title or faq.get("title") or "Sistema visual")
        except (gamefaqs.GameFAQsError, smart_guide.SmartGuideError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao importar a fonte do Atlas: {exc}"}

    def get_guide_system_source(self, slug: str, system_id: str) -> dict:
        current = self._guides.current(slug)
        system = next((item for item in current.get("systems") or []
                       if item.get("id") == system_id), None)
        source_id = (system or {}).get("source_id") or system_id
        source = self._guides.system_source(slug, source_id)
        if not source and source_id == "legacy-main":
            legacy = self._guides.source(slug)
            source = {"id": "legacy-main", "title": legacy.get("title", "Guia legado"),
                      "kind": "legacy", "captured_at": legacy.get("captured_at", 0),
                      "system_id": system_id}
        if not source:
            return {"ok": False, "error": "Fonte do sistema não encontrada."}
        with self._smart_ai_lock:
            status = dict(self._atlas_ai_status.get(f"{slug}:{source_id}") or {})
        return {"ok": True, "source": source, "status": status}

    def replace_guide_system_source(self, slug: str, system_id: str,
                                    source: dict) -> dict:
        """Troca a fonte exclusiva sem retirar o sistema atual até a nova
        análise terminar. A prévia substitui o mesmo id em uma revisão única."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        current = self._guides.current(slug)
        system = next((item for item in current.get("systems") or []
                       if item.get("id") == system_id), None)
        if not game or not system:
            return {"ok": False, "error": "Sistema visual não encontrado."}
        source = source if isinstance(source, dict) else {}
        kind = str(source.get("kind") or "").lower()
        title = str(source.get("title") or system.get("title") or "Sistema visual")
        try:
            if kind == "pdf":
                data = str(source.get("data") or "")
                raw = self._decode_upload(data)
                ok, error, sections = self._read_guide_sections(data)
                if not ok:
                    return {"ok": False, "error": error}
                saved = self._guides.add_system_source(
                    slug, title, "pdf", sections,
                    {"filename": source.get("filename") or ""}, raw=raw)
            elif kind == "gamefaqs":
                url = str(source.get("url") or "").strip()
                session = gamefaqs.create_session()
                faq = gamefaqs.fetch_faq(session, url)
                parsed = guide_parser.parse_freeform(faq.get("text") or "")
                saved = self._guides.add_system_source(
                    slug, title, "gamefaqs", self._atlas_faq_sections(faq),
                    {"filename": faq.get("title") or "", "url": url, "pages": faq.get('pages', 1),
                     "page_urls": [p['url'] for p in faq.get('page_records') or []]},
                    text=faq.get("text") or "")
            else:
                return {"ok": False, "error": "Escolha PDF ou GameFAQs."}
            self._guides.update_system_source(
                slug, saved["id"], replace_system_id=system_id)
            return self._queue_atlas_source(slug, saved, title, system_id)
        except (gamefaqs.GameFAQsError, smart_guide.SmartGuideError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Falha ao trocar a fonte: {exc}"}

    def delete_guide_system(self, slug: str, system_id: str) -> dict:
        try:
            revision = self._guides.delete_system(slug, system_id)
            self._refresh_smart_bundle(slug)
            return {"ok": True, "revision": revision, **self._guide_systems_payload(slug)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def set_guide_system_goal(self, slug: str, system_id: str, node_id: str) -> dict:
        try:
            state = self._guides.set_system_goal(slug, system_id, node_id)
            self._refresh_smart_bundle(slug)
            return {"ok": True, "state": state,
                    "objective": self._guides.system_objective(self._guides.current(slug), state)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def update_guide_requirement(self, slug: str, system_id: str, edge_id: str,
                                 requirement_id: str, completed: bool) -> dict:
        try:
            state = self._guides.update_requirement(
                slug, system_id, edge_id, requirement_id, bool(completed),
            )
            self._refresh_smart_bundle(slug)
            return {"ok": True, "state": state,
                    "objective": self._guides.system_objective(self._guides.current(slug), state)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def search_guide_system_media(self, slug: str, system_id: str, node_id: str,
                                  query: str = "", page: int = 0, source_id: str = '') -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        try:
            system = self._guides.media_system(slug, system_id, source_id)
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc), "results": []}
        node = next((item for item in (system or {}).get("nodes") or [] if item.get("id") == node_id), None)
        if not game or not node:
            return {"ok": False, "error": "Nó do sistema não encontrado.", "results": []}
        # An edited query is intentional; don't bury it under game/platform suffixes.
        term = re.sub(r"\s+", " ", query or f"{node.get('label', '')} {game.get('title', '')} artwork").strip()[:300]
        result = self.search_web_images(slug, term, page, "moderate", "icon", "google")
        result["system_id"] = system_id
        result["node_id"] = node_id
        result["query"] = term
        return result

    def set_guide_system_media(self, slug: str, system_id: str, node_id: str,
                               media_id: str, source_id: str = '') -> dict:
        try:
            if media_id and media_id not in {item.get("id") for item in self._guide_media.list(slug)}:
                return {"ok": False, "error": "Imagem aprovada não encontrada."}
            state = self._guides.set_system_media(slug, system_id, node_id, media_id, source_id)
            self._refresh_smart_bundle(slug)
            return {"ok": True, "state": state, **self._guide_systems_payload(slug)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def get_smart_guide_status(self, slug: str = "") -> dict:
        if slug:
            with self._smart_ai_lock:
                running = dict(self._smart_ai_status.get(slug) or {})
            return running or {"ok": True, "slug": slug, **self._guides.status(slug)}
        with self._smart_ai_lock:
            return {"ok": True, "tasks": list(self._smart_ai_status.values())}

    def start_smart_guide(self, slug: str, force: bool = False) -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game or not (game.get("guide") or []):
            return {"ok": False, "error": "Este jogo não tem dicas importadas."}
        if not self.settings.get("smart_guide_consent", False):
            return {"ok": False, **self._guides.set_status(
                slug, "awaiting_consent", message="Confirme o uso da IA nas Configurações.",
            )}
        config = self._ai_config()
        if not config["api_key"]:
            return {"ok": False, **self._guides.set_status(
                slug, "awaiting_configuration", message="Configure uma chave de IA.",
            )}
        with self._smart_ai_lock:
            task = self._smart_ai_status.get(slug) or {}
            if task.get("phase") in {"running", "queued"}:
                return {"ok": False, **task, "error": "Este guia já está sendo organizado."}
            busy = any(item.get("phase") == "running" for item in self._smart_ai_status.values())
            task = {
                "ok": True, "slug": slug, "phase": "queued" if busy else "running", "completed": 0,
                "total": 0, "message": ("Aguardando a organização anterior…" if busy else
                                          "Preparando o Guia Inteligente…"), "error": "",
                "cancel_requested": False,
            }
            self._smart_ai_status[slug] = task
            if busy:
                self._smart_ai_queue.append(slug)
        self._guides.set_status(slug, task["phase"], message=task["message"])
        if not busy:
            threading.Thread(target=self._smart_guide_worker, args=(slug,), daemon=True).start()
        return dict(task)

    def cancel_smart_guide(self, slug: str) -> dict:
        with self._smart_ai_lock:
            task = self._smart_ai_status.get(slug)
            if not task or task.get("phase") not in {"running", "queued"}:
                return {"ok": False, "error": "Nenhuma organização em andamento."}
            if task.get("phase") == "queued":
                self._smart_ai_queue = [item for item in self._smart_ai_queue if item != slug]
                task.update({"ok": False, "phase": "cancelled", "message": "", "error": "Operação cancelada."})
                self._guides.set_status(slug, "cancelled", error=task["error"], message="")
                return dict(task)
            task["cancel_requested"] = True
            task["message"] = "Cancelamento solicitado…"
            return dict(task)

    def _smart_guide_worker(self, slug: str) -> None:
        with self._smart_ai_lock:
            self._smart_ai_status.setdefault(slug, {}).update({
                "phase": "running", "message": "Preparando o Guia Inteligente…",
            })
        def progress(completed: int, total: int) -> None:
            with self._smart_ai_lock:
                task = self._smart_ai_status[slug]
                if task.get("cancel_requested"):
                    raise guide_ai.GuideAIError("Operação cancelada.")
                task.update({
                    "completed": completed, "total": total,
                    "message": (f"Organizando lote {min(completed + 1, total)} de {total}…"
                                if completed < total else "Validando e publicando…"),
                })
                message = task["message"]
            self._guides.set_status(slug, "running", message=message,
                                    completed=completed, total=total)

        game = load_game_file(GAMES_DIR / f"{slug}.json") or {}
        config = self._ai_config()
        try:
            document = guide_ai.generate_smart_guide(
                game.get("guide") or [], game, config, progress=progress,
            )
            source = self._guides.source(slug)
            revision = self._guides.publish(
                slug, document, source.get("hash", ""),
                document.get("provider", config["provider"]), document.get("model", ""),
            )
            status = self._guides.set_status(
                slug, "success", message="Guia Inteligente publicado.",
                revision_id=revision["revision_id"],
            )
            with self._smart_ai_lock:
                self._smart_ai_status[slug].update({"ok": True, **status})
        except Exception as exc:
            cancelled = "cancelada" in str(exc).lower()
            phase = "cancelled" if cancelled else "error"
            status = self._guides.set_status(slug, phase, error=str(exc), message="")
            with self._smart_ai_lock:
                self._smart_ai_status[slug].update({"ok": False, **status})
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if game:
            self._sync_game(game)
        next_slug = ""
        with self._smart_ai_lock:
            while self._smart_ai_queue and not next_slug:
                candidate = self._smart_ai_queue.pop(0)
                if (self._smart_ai_status.get(candidate) or {}).get("phase") == "queued":
                    next_slug = candidate
        if next_slug:
            threading.Thread(target=self._smart_guide_worker, args=(next_slug,), daemon=True).start()

    def update_guide_progress(self, slug: str, action: str, block_id: str = "", value=None) -> dict:
        try:
            progress = self._guides.update_progress(slug, action, block_id, value)
            self._refresh_smart_bundle(slug)
            return {"ok": True, "progress": progress,
                    "next_objective": self._guides.next_objective(self._guides.current(slug), progress)}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def restore_smart_guide_revision(self, slug: str, revision_id: str) -> dict:
        try:
            revision = self._guides.restore(slug, revision_id)
            return {"ok": True, "revision": revision}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def compare_smart_guide_revisions(self, slug: str, older: str, newer: str = "") -> dict:
        def load(revision_id: str) -> dict:
            if not revision_id:
                return self._guides.current(slug)
            safe = re.sub(r"[^a-zA-Z0-9_-]", "", revision_id)
            return load_game_file(self._guides.directory(slug) / "revisions" / f"{safe}.json") or {}
        before, after = load(older), load(newer)
        if not before or not after:
            return {"ok": False, "error": "Revisão não encontrada."}
        count = lambda doc: sum(len(ch.get("blocks") or []) for ch in doc.get("chapters") or [])
        return {"ok": True, "before": {"revision_id": before.get("revision_id"),
                                        "chapters": len(before.get("chapters") or []), "blocks": count(before)},
                "after": {"revision_id": after.get("revision_id"),
                           "chapters": len(after.get("chapters") or []), "blocks": count(after)}}

    def export_guide_pack(self, slug: str, include_progress: bool = True) -> dict:
        try:
            filename, encoded = self._guides.export_pack(
                slug, bool(include_progress), GUIDE_MEDIA_DIR / slug,
            )
            return {"ok": True, "filename": filename, "data": encoded}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def import_guide_pack(self, slug: str, encoded: str) -> dict:
        try:
            return self._guides.import_pack(slug, encoded, GUIDE_MEDIA_DIR / slug)
        except (smart_guide.SmartGuideError, zipfile.BadZipFile) as exc:
            return {"ok": False, "error": str(exc)}

    def search_guide_media(self, query: str, source: str = "openverse") -> dict:
        try:
            return {"ok": True, "results": self._guide_media.search(query, source)}
        except guide_media.GuideMediaError as exc:
            return {"ok": False, "error": str(exc), "results": []}

    def approve_guide_media(self, slug: str, candidate: dict,
                            rights_confirmed: bool = False) -> dict:
        try:
            item = self._guide_media.approve_remote(slug, candidate, bool(rights_confirmed))
            return {"ok": True, "media": item}
        except guide_media.GuideMediaError as exc:
            return {"ok": False, "error": str(exc)}

    def add_guide_media(self, slug: str, encoded: str, filename: str, title: str = "") -> dict:
        try:
            return {"ok": True, "media": self._guide_media.add_local(slug, encoded, filename, title)}
        except guide_media.GuideMediaError as exc:
            return {"ok": False, "error": str(exc)}

    def remove_guide_media(self, slug: str, media_id: str) -> dict:
        return {"ok": self._guide_media.remove(slug, media_id)}

    def create_guide_diagram(self, slug: str, spec: dict) -> dict:
        try:
            return {"ok": True, "media": self._guide_media.create_diagram(slug, spec)}
        except guide_media.GuideMediaError as exc:
            return {"ok": False, "error": str(exc)}

    def open_broad_media_search(self, query: str) -> dict:
        from urllib.parse import quote_plus
        query = re.sub(r"\s+", " ", str(query or "")).strip()[:300]
        if not query:
            return {"ok": False, "error": "Busca vazia."}
        webbrowser.open(f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}")
        return {"ok": True, "requires_rights_confirmation": True}

    def _read_guide_sections(self, b64: str):
        """(ok, erro, seções) — extrai do PDF só as seções de dicas/tutoriais
        (remove a seção que lista as conquistas)."""
        try:
            raw = base64.b64decode((b64 or "").split(",", 1)[-1])
            text = extract_pdf_text(io.BytesIO(raw))
        except Exception as exc:
            return False, f"Não foi possível ler o PDF: {exc}", []
        if not _normalize_text(text):
            return False, ("O PDF parece digitalizado. Instale Tesseract + "
                           "PyMuPDF/pytesseract para habilitar OCR local."), []
        parsed = guide_parser.parse_guide(text)
        guide = [s for s in parsed["sections"] if not s.get("is_achievements")]
        annotate_pdf_pages(guide, raw)
        if not guide:
            return False, "Não encontrei seções de dicas/tutoriais neste PDF.", []
        return True, "", guide

    def extract_guide_pdf(self, b64: str, filename: str = "") -> dict:
        """Lê um PDF e devolve SÓ as dicas/tutoriais (não mexe em conquistas).
        Usado no wizard para anexar o guia sem reordenar nada."""
        ok, err, guide = self._read_guide_sections(b64)
        if not ok:
            return {"ok": False, "error": err}
        if self.pending_import is not None:
            try:
                self.pending_import["guide_pdf_raw"] = base64.b64decode(
                    (b64 or "").split(",", 1)[-1], validate=True
                )
                self.pending_import["guide_source"] = {"source": "pdf", "filename": filename}
            except ValueError:
                pass
        return {"ok": True, "filename": filename, "guide": guide}

    def attach_guide_pdf(self, slug: str, b64: str, filename: str = "") -> dict:
        """Anexa as dicas/tutoriais de um PDF a um jogo JÁ SALVO, sem tocar nas
        conquistas/ordem/modos. Atualiza config/games/{slug}.json e o estado."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        ok, err, guide = self._read_guide_sections(b64)
        if not ok:
            return {"ok": False, "error": err}
        game["guide"] = guide
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # atualiza o estado em memória sem re-sincronizar progresso (sem rede)
        with self._lock:
            detail = self.state.get(slug)
            if detail is not None:
                detail["guide"] = guide
        if detail is None:
            self._sync_game(game)
        media = {"images": [], "image_count": 0, "scanned": False}
        try:
            raw = base64.b64decode((b64 or "").split(",", 1)[-1], validate=True)
            media = self._guide_media.extract_pdf(slug, raw, filename)
        except (ValueError, guide_media.GuideMediaError):
            pass  # o texto já foi importado; falha de imagem não desfaz o guia
        self._capture_smart_source(game, {
            "source": "pdf", "filename": filename,
            "pages": media.get("pages", 0), "media_ids": [m["id"] for m in media["images"]],
        })
        return {"ok": True, "sections": len(guide), "filename": filename, "media": media}

    # --------------------- seletor de artes (multi-fonte) ------------------- #
    # A arte escolhida vira art.cover (retrato: overlay/lateral) e/ou
    # art.background (paisagem: fundo da lista) — separadas da arte da RA e
    # reversíveis. Cada fonte guarda a própria chave em secrets.json.
    _ROLE_FILE = {"cover": "cover.png", "background": "background.png", "icon": "icon.png"}
    _ROLE_ART = {"cover": "cover", "background": "background", "icon": "icon"}
    # Campos de secrets por fonte (todos precisam estar preenchidos p/ "pronta").
    _SOURCE_KEYS = {
        "steamgriddb": ("steamgriddb_api_key",),
        "rawg": ("rawg_api_key",),
        "igdb": ("igdb_client_id", "igdb_client_secret"),
    }
    _SOURCE_LABEL = {"steamgriddb": "SteamGridDB", "rawg": "RAWG", "igdb": "IGDB"}

    def _covers_key(self) -> str:
        return ((load_secrets() or {}).get("steamgriddb_api_key") or "").strip()

    def _source_ready(self, source: str) -> bool:
        secrets = load_secrets() or {}
        fields = self._SOURCE_KEYS.get(source, ())
        return bool(fields) and all((secrets.get(f) or "").strip() for f in fields)

    def get_sources_config(self) -> dict:
        """Quais fontes de imagem têm chave configurada (nunca devolve a chave)."""
        return {"ok": True, "ready": {s: self._source_ready(s) for s in self._SOURCE_KEYS}}

    def get_art_enrichment_status(self, slug: str) -> dict:
        """Estado estável e sem credenciais da busca automática de hero art."""
        with self._art_lock:
            status = dict(self._art_status.get(slug) or {})
        if status:
            return {"ok": status.get("status") != "error", **status}
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "status": "error", "error": "Jogo não encontrado."}
        meta = dict(game.get("art_meta") or {})
        return {"ok": True, "status": meta.get("auto_status", "idle"),
                "art_meta": meta}

    def refresh_game_art(self, slug: str, force: bool = False) -> dict:
        """Enfileira a busca sem bloquear o WebView e nunca troca arte manual."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "status": "error", "error": "Jogo não encontrado."}
        return self._schedule_art_enrichment(slug, bool(force), explicit=True)

    def _set_art_status(self, slug: str, status: str, **extra) -> dict:
        payload = {"status": status, "updated_at": time.time(), **extra}
        with self._art_lock:
            self._art_status[slug] = payload
        return payload

    def _schedule_art_enrichment(self, slug: str, force: bool = False,
                                 explicit: bool = False) -> dict:
        if not slug:
            return {"ok": False, "status": "error", "error": "Jogo inválido."}
        with self._art_lock:
            current = self._art_status.get(slug) or {}
            if current.get("status") in ("queued", "fetching"):
                return {"ok": True, **current}
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "status": "error", "error": "Jogo não encontrado."}
        art = dict(game.get("art") or {})
        meta = dict(game.get("art_meta") or {})
        bg_meta = dict(meta.get("background") or {})
        # Campos background antigos vieram do seletor manual. A ausência de
        # metadados, portanto, é tratada como escolha do usuário.
        if art.get("background") and bg_meta.get("manual", True):
            ready = self._set_art_status(slug, "ready", source="manual")
            return {"ok": True, **ready, "art": art}
        if art.get("background") and not force:
            ready = self._set_art_status(slug, "ready", source=bg_meta.get("origin", "cache"))
            return {"ok": True, **ready, "art": art}
        last_attempt = float(meta.get("last_attempt_at") or 0)
        if not (force or explicit) and last_attempt and time.time() - last_attempt < 86400:
            status = meta.get("auto_status", "fallback")
            cached = self._set_art_status(slug, status, source="cache")
            return {"ok": True, **cached, "art": art}
        queued = self._set_art_status(slug, "queued")
        threading.Thread(target=self._enrich_game_art, args=(slug,), daemon=True).start()
        return {"ok": True, **queued, "art": art}

    def _enrich_game_art(self, slug: str) -> None:
        self._set_art_status(slug, "fetching")
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            self._set_art_status(slug, "error", error="Jogo não encontrado.")
            return
        art = dict(game.get("art") or {})
        meta = dict(game.get("art_meta") or {})
        bg_meta = dict(meta.get("background") or {})
        if art.get("background") and bg_meta.get("manual", True):
            self._set_art_status(slug, "ready", source="manual")
            return
        errors = []
        picked = None
        for source in ("steamgriddb", "rawg", "igdb"):
            if not self._source_ready(source):
                continue
            result = self._search_source(source, game.get("title", ""))
            if not result.get("ok"):
                errors.append(f"{self._SOURCE_LABEL[source]}: {result.get('error', 'falha')}")
                continue
            heroes = result.get("heroes") or []
            if heroes and heroes[0].get("url"):
                picked = (source, heroes[0]["url"])
                break
        now = time.time()
        meta["last_attempt_at"] = now
        if picked:
            source, url = picked
            dest = ART_DIR / slug / "background-auto.png"
            if image_fetch.download_image(url, dest):
                art["background"] = f"/assets/art/{slug}/background-auto.png?v={int(now)}"
                meta["background"] = {"origin": source, "source_url": url,
                                      "manual": False, "updated_at": now}
                meta["auto_status"] = "ready"
                self._persist_art(slug, game, art, meta)
                self._set_art_status(slug, "ready", source=source, art=art)
                return
            errors.append(f"{self._SOURCE_LABEL[source]}: download recusado")
        meta["auto_status"] = "error" if errors else "fallback"
        if errors:
            meta["last_error"] = " · ".join(errors)
        self._persist_art(slug, game, art, meta)
        self._set_art_status(slug, meta["auto_status"],
                             error=meta.get("last_error", ""), art=art)

    def set_source_key(self, source: str, key1: str = None, key2: str = None) -> dict:
        """Grava as credenciais de uma fonte. Fontes de 1 campo usam `key1`; o
        IGDB usa key1=client_id, key2=client_secret. String vazia apaga o campo."""
        fields = self._SOURCE_KEYS.get(source)
        if not fields:
            return {"ok": False, "error": f"Fonte desconhecida: {source}"}
        secrets = load_secrets() or {}
        for field, value in zip(fields, (key1, key2)):
            if value is None:
                continue
            value = (value or "").strip()
            if value:
                secrets[field] = value
            else:
                secrets.pop(field, None)
        SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRETS_PATH.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
        return self.get_sources_config()

    @staticmethod
    def _need_key(label: str) -> dict:
        return {"ok": False, "error": f"Configure a chave do {label} nas Configurações."}

    def covers_search(self, slug: str, query: str = "", source: str = "steamgriddb") -> dict:
        """Busca imagens para um jogo na fonte escolhida. Sem `query`, procura
        pelo título do jogo. Devolve o jogo casado, CAPAS (retrato) e FUNDOS
        (paisagem), e os demais jogos encontrados (para trocar, como no Playnite)."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        term = (query or "").strip() or (game or {}).get("title", "")
        if not term:
            return {"ok": False, "error": "Jogo não encontrado."}
        return self._search_source(source, term)

    @staticmethod
    def _web_art_query(game: dict, query: str = "", role: str = "cover") -> str:
        base = (query or "").strip() or " ".join(filter(None, (
            game.get("title", ""), game.get("platform", "")
        )))
        suffix = {
            "cover": "box art cover",
            "background": "wallpaper 1920x1080",
            "icon": "icon png",
            "both": "box art cover",
        }.get(str(role or "cover").lower(), "box art cover")
        normalized = re.sub(r"\s+", " ", base).strip()
        if not normalized:
            return ""
        if not normalized.casefold().endswith(suffix.casefold()):
            normalized = f"{normalized} {suffix}".strip()
        return normalized[:300]

    def search_web_images(self, slug: str, query: str = "", page: int = 0,
                          safe: str = "moderate", role: str = "cover",
                          provider: str = "google") -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado.", "results": []}
        term = self._web_art_query(game, query, role)
        try:
            result = web_image_search.search(term, page, safe, provider=provider)
            normalized = []
            for index, item in enumerate(result.get("results") or []):
                row = dict(item)
                try:
                    width, height = int(row.get("width") or 0), int(row.get("height") or 0)
                except (TypeError, ValueError):
                    width, height = 0, 0
                row.update({
                    "id": f"{result.get('provider', provider)}-{page}-{index}",
                    "width": width, "height": height,
                    "orientation": ("portrait" if height > width * 1.08 else
                                    "landscape" if width > height * 1.08 else "square"),
                    "source_page": row.get("source") or "",
                    "host": (urlparse(row.get("source") or row.get("url") or "").hostname or ""),
                    "provider": row.get("provider") or result.get("provider") or provider,
                    "query": result.get("query") or term,
                })
                normalized.append(row)
            result["results"] = normalized
            result["role"] = role
            return result
        except web_image_search.WebImageSearchError as exc:
            return {"ok": False, "error": str(exc), "results": [],
                    "open_url": web_image_search.public_search_url(term, safe),
                    "open_google_url": web_image_search.public_search_url(term, safe)}

    def open_web_image_search(self, slug: str, query: str = "",
                              safe: str = "moderate", role: str = "cover") -> dict:
        game = load_game_file(GAMES_DIR / f"{slug}.json") or {}
        term = self._web_art_query(game, query, role)
        if not term:
            return {"ok": False, "error": "Jogo não encontrado."}
        url = web_image_search.public_search_url(term, safe)
        try:
            webbrowser.open(url)
            return {"ok": True, "url": url}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "url": url}

    def open_image_source(self, url: str) -> dict:
        url = str(url or "").strip()
        if not url.startswith(("https://", "http://")):
            return {"ok": False, "error": "Endereço de origem inválido."}
        try:
            webbrowser.open(url)
            return {"ok": True, "url": url}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def covers_for(self, game_id, source: str = "steamgriddb") -> dict:
        """Imagens de um jogo específico da fonte (ao trocar o jogo casado)."""
        try:
            gid = int(game_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Jogo inválido."}
        return self._images_for_source(source, gid)

    _EMPTY_HIT = {"ok": True, "matches": [], "chosen": None, "covers": [], "heroes": []}

    def _search_source(self, source: str, term: str) -> dict:
        secrets = load_secrets() or {}
        try:
            if source == "steamgriddb":
                key = (secrets.get("steamgriddb_api_key") or "").strip()
                if not key:
                    return self._need_key("SteamGridDB")
                sess = steamgriddb.create_session(key)
                matches = steamgriddb.search_games(sess, term)
                if not matches:
                    return dict(self._EMPTY_HIT)
                chosen = matches[0]
                return {"ok": True, "matches": matches, "chosen": chosen,
                        "covers": steamgriddb.game_covers(sess, chosen["id"]),
                        "heroes": self._sgdb_heroes(sess, chosen["id"])}
            if source == "rawg":
                key = (secrets.get("rawg_api_key") or "").strip()
                if not key:
                    return self._need_key("RAWG")
                sess, k = rawg.create_session(key)
                matches = rawg.search_games(sess, k, term)
                if not matches:
                    return dict(self._EMPTY_HIT)
                chosen = matches[0]
                heroes = (rawg.hero_from_background(chosen.get("background", ""))
                          + rawg.game_screenshots(sess, k, chosen["id"]))
                slim = [{"id": m["id"], "name": m["name"]} for m in matches]
                return {"ok": True, "matches": slim,
                        "chosen": {"id": chosen["id"], "name": chosen["name"]},
                        "covers": [], "heroes": heroes}
            if source == "igdb":
                cid = (secrets.get("igdb_client_id") or "").strip()
                secret = (secrets.get("igdb_client_secret") or "").strip()
                if not (cid and secret):
                    return self._need_key("IGDB")
                sess = igdb.create_session()
                token = self._igdb_token_get(sess, cid, secret)
                matches = igdb.search_games(sess, cid, token, term)
                if not matches:
                    return dict(self._EMPTY_HIT)
                chosen = matches[0]
                slim = [{"id": m["id"], "name": m["name"]} for m in matches]
                return {"ok": True, "matches": slim,
                        "chosen": {"id": chosen["id"], "name": chosen["name"]},
                        "covers": igdb.covers_of(chosen), "heroes": igdb.heroes_of(chosen)}
        except (steamgriddb.SteamGridDBError, rawg.RawgError, igdb.IgdbError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"Fonte desconhecida: {source}"}

    def _igdb_token_get(self, session, client_id: str, client_secret: str) -> str:
        """Token OAuth do IGDB (Twitch), cacheado até perto de expirar."""
        cache = self._igdb_token
        now = time.time()
        if cache and cache.get("exp", 0) > now + 60:
            return cache["token"]
        tok = igdb.get_token(session, client_id, client_secret)
        self._igdb_token = {"token": tok["token"],
                            "exp": now + max(60, (tok["expires_in"] or 0) - 60)}
        return tok["token"]

    def _images_for_source(self, source: str, game_id: int) -> dict:
        secrets = load_secrets() or {}
        try:
            if source == "steamgriddb":
                key = (secrets.get("steamgriddb_api_key") or "").strip()
                if not key:
                    return self._need_key("SteamGridDB")
                sess = steamgriddb.create_session(key)
                return {"ok": True, "covers": steamgriddb.game_covers(sess, game_id),
                        "heroes": self._sgdb_heroes(sess, game_id)}
            if source == "rawg":
                key = (secrets.get("rawg_api_key") or "").strip()
                if not key:
                    return self._need_key("RAWG")
                sess, k = rawg.create_session(key)
                return {"ok": True, "covers": [],
                        "heroes": rawg.game_screenshots(sess, k, game_id)}
            if source == "igdb":
                cid = (secrets.get("igdb_client_id") or "").strip()
                secret = (secrets.get("igdb_client_secret") or "").strip()
                if not (cid and secret):
                    return self._need_key("IGDB")
                sess = igdb.create_session()
                token = self._igdb_token_get(sess, cid, secret)
                game = igdb.game_by_id(sess, cid, token, game_id)
                if not game:
                    return {"ok": True, "covers": [], "heroes": []}
                return {"ok": True, "covers": igdb.covers_of(game),
                        "heroes": igdb.heroes_of(game)}
        except (steamgriddb.SteamGridDBError, rawg.RawgError, igdb.IgdbError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"Fonte desconhecida: {source}"}

    @staticmethod
    def _sgdb_heroes(session, game_id: int) -> list:
        """Heroes são um extra: se o jogo não tiver nenhum (404), a busca não
        deve falhar por isso."""
        try:
            return steamgriddb.game_heroes(session, game_id)
        except steamgriddb.SteamGridDBError:
            return []

    def set_game_cover(self, slug: str, url: str, role: str = "cover",
                       metadata: dict | None = None) -> dict:
        """Baixa a imagem escolhida (de qualquer fonte, ou de uma URL colada) e a
        grava no papel pedido — `cover`, `background` ou `both` — sem tocar em
        conquistas/progresso. NÃO exige chave: o asset vem de um CDN público e é
        baixado por `image_fetch` (sessão limpa, com User-Agent, sem token — era
        o token indo pro CDN que fazia o download falhar). A URL ganha um sufixo
        de versão para o webview não reusar a imagem antiga do cache."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "URL de imagem inválida."}
        roles = ["cover", "background"] if role == "both" else [role]
        if any(r not in self._ROLE_FILE for r in roles):
            return {"ok": False, "error": f"Papel inválido: {role}"}
        art = dict(game.get("art") or {})
        art_meta = dict(game.get("art_meta") or {})
        stamp = int(time.time())
        temp = ART_DIR / "_downloads" / f"{slug}-{threading.get_ident()}.png"
        downloaded = image_fetch.download_image_validated(url, temp)
        if not downloaded.get("ok"):
            return {"ok": False, "error": downloaded.get("error") or
                    "Não consegui validar a imagem."}
        digest = downloaded["sha256"]
        cached = ART_DIR / "_cache" / f"{digest}.png"
        cached.parent.mkdir(parents=True, exist_ok=True)
        if cached.exists():
            temp.unlink(missing_ok=True)
        else:
            temp.replace(cached)
        metadata = metadata if isinstance(metadata, dict) else {}
        provider = str(metadata.get("provider") or "manual").lower()
        if provider not in ("google", "yandex", "bing", "steamgriddb", "rawg", "igdb", "manual"):
            provider = "manual"
        source_page = str(metadata.get("source_page") or metadata.get("source") or "")
        if source_page and not source_page.startswith(("http://", "https://")):
            source_page = ""
        origin = str(metadata.get("origin") or
                     (urlparse(source_page).hostname if source_page else "") or provider)[:200]
        for r in roles:
            asset_url = f"/assets/art/_cache/{digest}.png?v={stamp}"
            art[self._ROLE_ART[r]] = asset_url
            if r == "icon":
                game.setdefault("icon_original", game.get("icon", ""))
                game["icon"] = asset_url
            art_meta[r] = {"origin": origin, "mechanism": provider,
                           "source_url": url, "source_page": source_page,
                           "query": str(metadata.get("query") or "")[:300],
                           "manual": True, "updated_at": time.time(),
                           "sha256": digest, "width": downloaded.get("width"),
                           "height": downloaded.get("height"), "mime": downloaded.get("mime")}
        art_meta["auto_status"] = "ready"
        self._remember_art(slug, load_game_file(GAMES_DIR / f"{slug}.json") or {})
        self._persist_art(slug, game, art, art_meta)
        # A escolha manual de arte também atualiza a identidade visual, exceto
        # quando o usuário já fixou uma paleta manual para o jogo.
        if not (game.get("palette") or {}).get("manual"):
            self.recalculate_game_palette(slug)
        return {"ok": True, "art": art, "art_meta": art_meta}

    def apply_web_art(self, slug: str, candidate: dict, role: str = "cover") -> dict:
        """Aplica somente o candidato que o usuário aprovou na grade web."""
        if not isinstance(candidate, dict):
            return {"ok": False, "error": "Resultado de imagem inválido."}
        url = str(candidate.get("url") or "").strip()
        provider = str(candidate.get("provider") or "").lower()
        if provider not in ("google", "yandex", "bing"):
            return {"ok": False, "error": "Mecanismo de imagem inválido."}
        result = self.set_game_cover(slug, url, role, {
            "provider": provider,
            "origin": candidate.get("host") or
                      (urlparse(candidate.get("source_page") or
                                candidate.get("source") or "").hostname or ""),
            "source_page": candidate.get("source_page") or candidate.get("source") or "",
            "query": candidate.get("query") or "",
        })
        if result.get("ok"):
            result["provider"] = provider
        return result

    @staticmethod
    def _safe_hex(value: str) -> str | None:
        value = str(value or "").strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value.upper()
        return None

    @staticmethod
    def _contrast_text(color: str) -> str:
        rgb = tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
                  for c in rgb]
        luminance = .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
        return "#06101D" if luminance > .46 else "#FFFFFF"

    @classmethod
    def _palette_from_path(cls, path: Path) -> dict:
        from PIL import Image
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((96, 96))
            colors = image.quantize(colors=12).convert("RGB").getcolors(96 * 96) or []
        ranked = []
        for count, (r, g, b) in colors:
            high, low = max(r, g, b), min(r, g, b)
            saturation = high - low
            brightness = (r + g + b) / 3
            if brightness < 24 or brightness > 238 or saturation < 18:
                continue
            ranked.append((count * (1 + saturation / 255), (r, g, b)))
        ranked.sort(reverse=True)
        primary_rgb = ranked[0][1] if ranked else (47, 157, 255)
        secondary_rgb = next((rgb for _weight, rgb in ranked[1:]
                              if sum(abs(rgb[i] - primary_rgb[i]) for i in range(3)) > 90),
                             tuple(min(255, int(c * 1.18 + 18)) for c in primary_rgb))
        primary = "#%02X%02X%02X" % primary_rgb
        secondary = "#%02X%02X%02X" % secondary_rgb
        return {"primary": primary, "secondary": secondary,
                "text": cls._contrast_text(primary), "manual": False,
                "updated_at": time.time()}

    def _palette_art_path(self, game: dict) -> Path | None:
        art = game.get("art") or {}
        value = art.get("background") or art.get("ingame") or art.get("cover") or art.get("box")
        if not value or not str(value).startswith("/assets/"):
            return None
        relative = str(value).split("?", 1)[0].lstrip("/")
        if relative.startswith("assets/art/"):
            path = (ART_DIR / relative.removeprefix("assets/art/")).resolve()
            base = ART_DIR.resolve()
        else:
            path = (DATA_DIR / relative).resolve()
            base = (DATA_DIR / "assets").resolve()
        return path if path.exists() and (path == base or base in path.parents) else None

    def recalculate_game_palette(self, slug: str) -> dict:
        path = GAMES_DIR / f"{slug}.json"
        game = load_game_file(path)
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        art_path = self._palette_art_path(game)
        if not art_path:
            return {"ok": False, "error": "Nenhuma arte local disponível para extrair cores."}
        try:
            palette = self._palette_from_path(art_path)
        except Exception as exc:
            return {"ok": False, "error": f"Não foi possível analisar a arte: {exc}"}
        game["palette"] = palette
        game["accent"] = palette["primary"]
        path.write_text(json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._lock:
            if slug in self.state:
                self.state[slug]["palette"] = palette
                self.state[slug]["accent"] = palette["primary"]
        return {"ok": True, "palette": palette}

    def set_game_palette(self, slug: str, primary: str, secondary: str = "") -> dict:
        primary = self._safe_hex(primary)
        secondary = self._safe_hex(secondary) or primary
        if not primary:
            return {"ok": False, "error": "Cor primária inválida."}
        path = GAMES_DIR / f"{slug}.json"
        game = load_game_file(path)
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        palette = {"primary": primary, "secondary": secondary,
                   "text": self._contrast_text(primary), "manual": True,
                   "updated_at": time.time()}
        self._remember_art(slug, game)
        game["palette"] = palette
        game["accent"] = primary
        smart_guide._atomic_json(path, game)
        with self._lock:
            if slug in self.state:
                self.state[slug]["palette"] = palette
                self.state[slug]["accent"] = primary
        return {"ok": True, "palette": palette}

    def clear_game_cover(self, slug: str, role: str = "cover") -> dict:
        """Volta à arte padrão da RA no papel pedido (`cover`, `background` ou
        `both`): remove o campo e o arquivo baixado."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        self._remember_art(slug, game)
        roles = ["cover", "background"] if role == "both" else [role]
        art = dict(game.get("art") or {})
        art_meta = dict(game.get("art_meta") or {})
        for r in roles:
            if r not in self._ROLE_FILE:
                continue
            art.pop(self._ROLE_ART[r], None)
            art_meta.pop(r, None)
            if r == "icon":
                game["icon"] = game.pop("icon_original", game.get("icon", ""))
            # Keep the cached image reachable by the reversible art history.
        art_meta["auto_status"] = "idle"
        art_meta["last_attempt_at"] = 0
        self._persist_art(slug, game, art, art_meta)
        return {"ok": True, "art": art, "art_meta": art_meta}

    def _persist_art(self, slug: str, game: dict, art: dict,
                     art_meta: dict | None = None) -> None:
        """Grava o `art` no config do jogo e reflete no estado ao vivo."""
        game["art"] = art
        if art_meta is not None:
            game["art_meta"] = art_meta
        smart_guide._atomic_json(GAMES_DIR / f"{slug}.json", game)
        with self._lock:
            detail = self.state.get(slug)
            if detail is not None:
                detail["art"] = art
                detail["icon"] = game.get("icon", detail.get("icon", ""))
                if art_meta is not None:
                    detail["art_meta"] = art_meta

    # ------------------------- controles da janela -------------------------- #
    # IMPORTANTE: as operacoes de janela do pywebview (destroy/minimize/on_top)
    # NAO surtem efeito quando executadas de dentro do contexto do bridge
    # js_api — e por isso os botoes Fechar/Minimizar "nao funcionavam". Rodar
    # cada operacao numa thread propria resolve.
    @staticmethod
    def _window_op(fn):
        threading.Thread(target=fn, daemon=True).start()

    def minimize(self):
        if self._window:
            self._window_op(self._window.minimize)

    def close(self):
        if getattr(self, "_notifications", None):
            self._notifications.close()
        if hasattr(self, "_companion_service"):
            self._companion_service.stop()
            self._experience.session(self._experience_account(), "pause")
        try:
            self._overlay_input.close()
        except Exception:
            pass
        for win in self._overlay_windows.values():
            try:
                self._window_op(win.destroy)
            except Exception:
                pass
        if self._window:
            self._window_op(self._window.destroy)

    def toggle_on_top(self, value: bool) -> dict:
        val = bool(value)
        if self._window:
            self._window_op(lambda: setattr(self._window, "on_top", val))
        return {"ok": True, "on_top": val}

    def move_window(self, x, y) -> dict:
        """Move a janela para (x, y) — usado pelo arraste manual do overlay
        compacto. Passa pelo _window_op (thread própria) de propósito: mover a
        janela DIRETO no contexto do bridge js_api não surte efeito no
        Windows/WinForms (mesmo motivo de fechar/minimizar). É exatamente por
        isso que o drag-region nativo do pywebview — que chama window.move na
        thread do bridge — arrasta no Linux/GTK mas não no Windows."""
        win = self._window
        if not win:
            return {"ok": False}
        try:
            ix, iy = int(x), int(y)
        except (TypeError, ValueError):
            return {"ok": False}
        self._window_op(lambda: win.move(ix, iy))
        return {"ok": True}

    def attach_overlay_window(self, surface: str, window) -> None:
        """Registra uma das janelas filhas depois que o WebView terminou de carregar."""
        if surface not in ("summary", "details"):
            return
        self._overlay_windows[surface] = window
        self._overlay_loaded.add(surface)
        if self._tracker:
            try:
                hwnd = self._tracker.own_window_handle(window, f"DigiTracker {surface}")
                if hwnd:
                    self._overlay_hwnds[surface] = hwnd
            except Exception:
                pass
        self._layout_overlay_surfaces(self._current_overlay_rect(), resize=True)
        self._configure_native_overlay()
        self._notify_overlay_surfaces()

    @staticmethod
    def _surface_variant(surface: str) -> str:
        return "expanded" if surface == "details" else "minimal"

    def move_overlay_surface(self, surface: str, x, y) -> dict:
        surface = str(surface or "").lower()
        win = self._overlay_windows.get(surface)
        if not win or not self._compact or not self._compact_edit_mode:
            return {"ok": False, "error": "Ative o modo de edição do overlay."}
        try:
            ix, iy = int(x), int(y)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Posição inválida."}
        self._window_op(lambda: win.move(ix, iy))
        self.touch_overlay_edit_mode()
        return {"ok": True, "surface": surface, "x": ix, "y": iy}

    def resize_overlay_surface(self, surface: str, width, height) -> dict:
        surface = str(surface or "").lower()
        win = self._overlay_windows.get(surface)
        if not win or not self._compact or not self._compact_edit_mode:
            return {"ok": False, "error": "Ative o modo de edição do overlay."}
        variant = self._surface_variant(surface)
        low, high = self._compact_bounds(variant)
        try:
            width = max(low[0], min(high[0], int(width)))
            height = max(low[1], min(high[1], int(height)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Dimensões inválidas."}
        self._overlay_expected_sizes[surface] = (width, height)
        self._window_op(lambda: win.resize(width, height))
        self.touch_overlay_edit_mode()
        return {"ok": True, "surface": surface, "width": width, "height": height}

    @staticmethod
    def _compact_bounds(variant: str) -> tuple[tuple[int, int], tuple[int, int]]:
        if variant == "both":
            return COMPACT_BOTH_MIN, COMPACT_BOTH_MAX
        if variant == "expanded":
            return COMPACT_EXPANDED_MIN, COMPACT_EXPANDED_MAX
        return COMPACT_MINIMAL_MIN, COMPACT_MINIMAL_MAX

    def resize_compact_window(self, width, height) -> dict:
        if not self._window or not self._compact or not self._compact_edit_mode:
            return {"ok": False, "error": "Ative o modo de edição do overlay."}
        low, high = self._compact_bounds(self._compact_variant)
        try:
            width = max(low[0], min(high[0], int(width)))
            height = max(low[1], min(high[1], int(height)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Dimensões inválidas."}
        self._compact_expected_size = (width, height)
        self._overlay_size = (width, height)
        self._window_op(lambda: self._window.resize(width, height))
        self.touch_overlay_edit_mode()
        return {"ok": True, "width": width, "height": height}

    def _compact_emulator_key(self) -> str:
        try:
            process = (self._tracker.status().get("process") or "").strip().lower()
        except Exception:
            process = ""
        return process or "default"

    def save_overlay_geometry(self, variant, x, y, width, height) -> dict:
        """Salva tamanho e posição relativos à área cliente por emulador/HUD."""
        variant = str(variant or self._compact_variant).lower()
        if variant not in ("minimal", "expanded", "both", "summary", "details"):
            return {"ok": False, "error": "Variante inválida."}
        surface = variant if variant in ("summary", "details") else None
        bounds_variant = self._surface_variant(surface) if surface else variant
        low, high = self._compact_bounds(bounds_variant)
        try:
            x, y = int(x), int(y)
            width = max(low[0], min(high[0], int(width)))
            height = max(low[1], min(high[1], int(height)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Geometria inválida."}
        rect = self._current_overlay_rect()
        offset_x = x - int(rect[0]) if rect else x
        offset_y = y - int(rect[1]) if rect else y
        key = self._compact_emulator_key()
        with self._lock:
            geometries = self.settings.setdefault("compact_geometries", {})
            per_emulator = geometries.setdefault(key, {})
            per_emulator[variant] = {
                "width": width, "height": height,
                "offset_x": offset_x, "offset_y": offset_y,
            }
            save_settings(self.settings)
        self.touch_overlay_edit_mode()
        return {"ok": True, "emulator": key, "variant": variant,
                **per_emulator[variant]}

    def _stored_compact_geometry(self, variant=None) -> dict:
        variant = variant or self._compact_variant
        with self._lock:
            geometries = self.settings.get("compact_geometries") or {}
            per_emulator = geometries.get(self._compact_emulator_key()) or {}
            stored = per_emulator.get(variant)
            # Primeira abertura após atualizar: reaproveita a geometria antiga
            # correspondente e passa a salvar pelo nome da superfície.
            if not stored and variant in ("summary", "details"):
                legacy = "minimal" if variant == "summary" else "expanded"
                stored = per_emulator.get(legacy)
            return dict(stored or {})

    def _surface_size(self, surface: str, rect=None) -> tuple[int, int]:
        variant = self._surface_variant(surface)
        stored = self._stored_compact_geometry(surface)
        if stored.get("width") and stored.get("height"):
            low, high = self._compact_bounds(variant)
            return (max(low[0], min(high[0], int(stored["width"]))),
                    max(low[1], min(high[1], int(stored["height"]))))
        return self._compact_size(rect, variant=variant)

    def _surface_dock(self, surface: str, rect, size: tuple[int, int]) -> tuple[int, int]:
        stored = self._stored_compact_geometry(surface)
        if stored and "offset_x" in stored and "offset_y" in stored:
            x = int(rect[0]) + int(stored["offset_x"])
            y = int(rect[1]) + int(stored["offset_y"])
            x = max(int(rect[0]), min(x, int(rect[0] + rect[2] - size[0])))
            y = max(int(rect[1]), min(y, int(rect[1] + rect[3] - size[1])))
            return x, y
        config = self._compact_surfaces_config()[surface]
        corner = config.get("corner") or ("top-right" if surface == "summary" else "bottom-right")
        if corner == "auto":
            corner = "top-right" if surface == "summary" else "bottom-right"
        return emulator_tracker.dock_position(rect, size, COMPACT_MARGIN, corner)

    def _fallback_overlay_rect(self):
        try:
            screen = webview.screens()[0]
            return screen.x, screen.y, screen.width, screen.height
        except Exception:
            return 0, 0, self._normal_size[0], self._normal_size[1]

    def _set_surface_visible(self, surface: str, visible: bool) -> None:
        # Os objetos Window existem antes do evento `loaded`, mas WinForms e
        # WebView2 ainda nao. Ignorar a superficie nesse curto intervalo evita
        # bloquear a thread de UI durante o bootstrap da build onefile.
        if surface not in self._overlay_loaded:
            return
        win = self._overlay_windows.get(surface)
        if not win:
            return
        hwnd = self._overlay_hwnds.get(surface)
        native = (self._overlay_input.show_no_activate(hwnd) if visible and hwnd else
                  self._overlay_input.hide_window(hwnd) if not visible and hwnd else False)
        if not native:
            self._window_op(win.show if visible else win.hide)

    def _layout_overlay_surfaces(self, rect=None, resize: bool = True) -> None:
        if not self._overlay_windows:
            return
        rect = rect or self._fallback_overlay_rect()
        enabled = set(self._enabled_compact_surfaces()) if self._compact else set()
        for surface, win in self._overlay_windows.items():
            if surface not in self._overlay_loaded:
                continue
            if surface not in enabled:
                self._set_surface_visible(surface, False)
                continue
            size = self._surface_size(surface, rect)
            pos = self._surface_dock(surface, rect, size)
            self._overlay_expected_sizes[surface] = size

            def apply_layout(target=win, target_size=size, target_pos=pos):
                if resize:
                    target.resize(*target_size)
                _move_after_resize(target, target_pos)

            self._window_op(apply_layout)
            self._set_surface_visible(surface, True)

    def set_compact(self, value: bool, dock=None, from_user: bool = True, size=None) -> dict:
        """Alterna entre o dashboard completo e um mini-overlay (quadradinho)
        com o progresso do jogo ativo — pensado para ficar por cima do
        emulador enquanto se joga, tipo o popup de conquistas do RetroArch.

        `dock` = (x, y) força a posição (usado ao grudar na janela do emulador);
        sem ele, o overlay vai para o canto da tela, como antes.
        `from_user` distingue o clique no botão do acionamento automático — sair
        na mão com o emulador aberto silencia o overlay automático."""
        value = bool(value)
        was_auto = self._compact and not value and from_user
        if from_user and self._auto_collapse_timer:
            self._auto_collapse_timer.cancel()
            self._auto_collapse_timer = None
        # A versão nova não transforma mais a janela principal em um retângulo
        # combinado. Ela alterna duas janelas filhas do tamanho exato de cada
        # HUD; o caminho antigo permanece abaixo para testes e plataformas que
        # não tenham criado as superfícies dedicadas.
        if self._overlay_windows:
            if not value:
                self._overlay_input.restore()
                self._overlay_native_status = {"passive": False, "error": ""}
                self._overlay_native_by_surface = {}
                self._compact_edit_mode = False
                if self._compact_edit_timer:
                    self._compact_edit_timer.cancel()
                    self._compact_edit_timer = None
            self._compact = value
            self._compact_variant = self._legacy_variant_from_surfaces()
            self._compact_state = self._compact_variant if value else "hidden"
            self._layout_overlay_surfaces(self._current_overlay_rect(), resize=True)
            if value:
                self._configure_native_overlay()
                if self._window:
                    self._window_op(self._window.hide)
            elif self._window:
                self._window_op(self._window.show)
            self._notify_ui_compact(value, self._compact_state)
            if was_auto and self._overlay:
                self._overlay.notify_manual_exit()
            return {"ok": True, "compact": value, "state": self._compact_state,
                    "editing": self._compact_edit_mode,
                    "surfaces": list(self._enabled_compact_surfaces())}
        # Ao voltar para o aplicativo completo, recupere os cliques antes de
        # enfileirar resize/movimentação. Assim a janela nunca fica grande e
        # ainda marcada como passa-clique caso a operação visual demore.
        if not value:
            self._overlay_input.restore()
            self._overlay_native_status = {"passive": False, "error": ""}
            self._compact_expected_size = None
            self._compact_edit_mode = False
            if self._compact_edit_timer:
                self._compact_edit_timer.cancel()
                self._compact_edit_timer = None
        self._compact = value
        self._compact_state = self._compact_variant if value else "hidden"
        win = self._window

        def op():
            if value:
                if not self._pre_compact_pos:
                    self._pre_compact_pos = (win.x, win.y)
                    self._pre_compact_size = (
                        int(getattr(win, "width", None) or self._normal_size[0]),
                        int(getattr(win, "height", None) or self._normal_size[1]),
                    )
                w, h = size or self._compact_size(
                    self._current_overlay_rect(), variant=self._compact_variant
                )
                self._compact_expected_size = (int(w), int(h))
                win.resize(w, h)
                pos = dock
                if pos is None:
                    try:
                        scr = webview.screens()[0]
                        pos = (scr.x + scr.width - w - COMPACT_MARGIN,
                               scr.y + COMPACT_MARGIN)
                    except Exception:
                        pos = None
                if pos:
                    _move_after_resize(win, pos)
            else:
                win.resize(*(self._pre_compact_size or self._normal_size))
                if self._pre_compact_pos:
                    _move_after_resize(win, self._pre_compact_pos)
                    self._pre_compact_pos = None
                self._pre_compact_size = None

        if win:
            self._window_op(op)
        self._configure_native_overlay()
        self._notify_ui_compact(value, self._compact_state)
        if was_auto and self._overlay:
            self._overlay.notify_manual_exit()
        return {"ok": True, "compact": value, "state": self._compact_state,
                "editing": self._compact_edit_mode}

    # ------------- overlay automático: grudar na janela do emulador --------- #
    def set_auto_overlay(self, value: bool) -> dict:
        value = bool(value)
        with self._lock:
            self.settings["auto_overlay"] = value
            save_settings(self.settings)
        return {"ok": True, "auto_overlay": value}

    def set_overlay_option(self, chave: str, value: bool) -> dict:
        """Interruptores do comportamento em fullscreen exclusivo. Ambos vêm
        desligados: o app não injeta tecla nem pula de monitor sem seu aval."""
        if chave not in ("overlay_exit_fullscreen", "overlay_second_screen",
                         "overlay_fit_emulator"):
            return {"ok": False, "error": f"Opção desconhecida: {chave}"}
        value = bool(value)
        with self._lock:
            self.settings[chave] = value
            save_settings(self.settings)
        return {"ok": True, chave: value}

    def _overlay_actions(self) -> dict:
        """Ações que o watcher dispara. Reposicionar é mover a janela para o
        canto de dentro do emulador; o JS é avisado para trocar a tela."""

        def enter(rect):
            if self._overlay_windows:
                self.set_compact(True, from_user=False)
                self._layout_overlay_surfaces(rect, resize=True)
                self._notify_ui_compact(True)
                return
            size = self._overlay_size_for(rect)
            self._overlay_size = size
            self.set_compact(True, dock=self._dock_for(rect, size), from_user=False, size=size)
            self._notify_ui_compact(True)

        def follow(rect):
            if self._overlay_windows:
                if self._compact_edit_mode:
                    return
                self._layout_overlay_surfaces(rect, resize=True)
                self._configure_native_overlay()
                return
            win = self._window
            if not win:
                return
            if self._compact_edit_mode:
                # Não disputar a posição com o arraste/redimensionamento do
                # usuário. Ao encerrar a edição, o watcher usa a geometria
                # relativa recém-salva e volta a acompanhar o emulador.
                return
            size = self._overlay_size_for(rect)
            # O emulador foi redimensionado? Reajusta o overlay antes de reposicionar.
            resize = size != getattr(self, "_overlay_size", None)
            if resize:
                self._overlay_size = size
                self._notify_ui_compact(True)   # o JS reajusta quantas próximas cabem
            pos = self._dock_for(rect, size)

            def resize_and_move():
                if resize:
                    win.resize(*size)
                _move_after_resize(win, pos)

            self._window_op(resize_and_move)

        def leave():
            self._overlay_size = None
            self.set_compact(False, from_user=False)
            self._notify_ui_compact(False)

        def assert_top():
            tracker = self._tracker
            if tracker and self._overlay_windows:
                for hwnd in self._overlay_hwnds.values():
                    tracker.make_topmost(hwnd)
            elif tracker and self._own_hwnd:
                tracker.make_topmost(self._own_hwnd)

        def on_exclusive(rect, win):
            """Jogo em fullscreen exclusivo: nenhum overlay aparece por cima.
            Só agimos com o interruptor ligado — o app não injeta tecla nem
            muda de tela por conta própria."""
            with self._lock:
                usar_2a_tela = self.settings.get("overlay_second_screen", False)
                mandar_alt_enter = self.settings.get("overlay_exit_fullscreen", False)

            if usar_2a_tela:
                livre = emulator_tracker.pick_free_screen(rect, self._tracker.screens())
                if livre:
                    if self._overlay_windows:
                        self.set_compact(True, from_user=False)
                        self._layout_overlay_surfaces(livre, resize=True)
                        self._warn_ui("O jogo está em tela cheia exclusiva; movi os HUDs "
                                      "para o outro monitor.")
                        return
                    size = self._overlay_size_for(livre)
                    self._overlay_size = size
                    self.set_compact(True, dock=self._dock_for(livre, size),
                                     from_user=False, size=size)
                    self._notify_ui_compact(True)
                    self._warn_ui("O jogo está em tela cheia exclusiva; movi o overlay "
                                  "para o outro monitor.")
                    return

            if mandar_alt_enter and self._tracker.send_fullscreen_toggle(win.get("hwnd")):
                self._warn_ui(f"{win.get('title', 'O emulador')} estava em tela cheia "
                              "exclusiva. Mandei Alt+Enter para sair.")
                return

            self._warn_ui(
                f"{win.get('title', 'O emulador')} está em tela cheia exclusiva, e "
                "nenhum overlay aparece por cima disso. Use o modo janela ou "
                "borderless, ou ligue uma das opções de overlay nas configurações."
            )

        return {"enter": enter, "follow": follow, "exit": leave,
                "assert_top": assert_top, "on_exclusive": on_exclusive}

    def _warn_ui(self, mensagem: str):
        """Enfileira um aviso para a interface mostrar no próximo ciclo."""
        with self._lock:
            self.overlay_notice = mensagem

    def _current_overlay_rect(self):
        tracker = self._tracker
        if tracker and hasattr(tracker, "status"):
            return tracker.status().get("rect")
        return None

    def _compact_size(self, rect=None, expanded=None, variant=None) -> tuple[int, int]:
        """Calcula o tamanho do resumo, painel expandido ou visual combinado."""
        with self._lock:
            mode = self.settings.get("compact_size_mode", "auto")
            fit = self.settings.get("overlay_fit_emulator", True)
            manual_w = int(self.settings.get("compact_width") or COMPACT_SIZE[0])
            manual_h = int(self.settings.get("compact_height") or COMPACT_SIZE[1])
            expanded_w = int(self.settings.get("compact_expanded_width") or COMPACT_EXPANDED_SIZE[0])
            expanded_h = int(self.settings.get("compact_expanded_height") or COMPACT_EXPANDED_SIZE[1])
        if variant not in ("minimal", "expanded", "both"):
            if expanded is None:
                variant = self._compact_variant
            else:
                variant = "expanded" if bool(expanded) else "minimal"
        expanded = variant in ("expanded", "both")
        combined = variant == "both"
        stored = self._stored_compact_geometry(variant)
        if stored.get("width") and stored.get("height"):
            w, h = int(stored["width"]), int(stored["height"])
            low, high = self._compact_bounds(variant)
            return max(low[0], min(high[0], w)), max(low[1], min(high[1], h))
        if mode == "manual" or not fit:
            if combined:
                w, h = expanded_w, manual_h + expanded_h + 10
            else:
                w, h = (expanded_w, expanded_h) if expanded else (manual_w, manual_h)
        elif not rect:
            if combined:
                w, h = COMPACT_EXPANDED_SIZE[0], COMPACT_SIZE[1] + COMPACT_EXPANDED_SIZE[1] + 10
            else:
                w, h = (COMPACT_EXPANDED_SIZE if expanded else COMPACT_SIZE)
        else:
            _ex, _ey, ew, eh = rect
            if combined:
                w = round(ew * OVERLAY_EXPANDED_W)
                h = round(eh * OVERLAY_FIT_H) + round(eh * OVERLAY_EXPANDED_H) + 10
            else:
                ratio_w = OVERLAY_EXPANDED_W if expanded else OVERLAY_FIT_W
                ratio_h = OVERLAY_EXPANDED_H if expanded else OVERLAY_FIT_H
                w, h = round(ew * ratio_w), round(eh * ratio_h)
        low, high = self._compact_bounds(variant)
        return max(low[0], min(high[0], w)), max(low[1], min(high[1], h))

    def _overlay_size_for(self, rect=None) -> tuple[int, int]:
        """Tamanho do overlay ao grudar: proporcional à janela do emulador quando
        'ajustar ao emulador' está ligado (preso a COMPACT_MIN/MAX); senão, o
        tamanho manual das Configurações."""
        return self._compact_size(rect, variant=self._compact_variant)

    def _dock_for(self, rect, size=None):
        size = size or self._compact_size(rect)
        stored = self._stored_compact_geometry(self._compact_variant)
        if stored and "offset_x" in stored and "offset_y" in stored:
            x = int(rect[0]) + int(stored["offset_x"])
            y = int(rect[1]) + int(stored["offset_y"])
            x = max(int(rect[0]), min(x, int(rect[0] + rect[2] - size[0])))
            y = max(int(rect[1]), min(y, int(rect[1] + rect[3] - size[1])))
            return x, y
        with self._lock:
            corner = self.settings.get("compact_corner", "auto")
        if corner == "auto":
            # A preferência estável mantém o HUD fora do centro; o fallback
            # para a direita inferior só é usado quando a área é muito baixa.
            corner = "bottom-right" if rect[3] < size[1] * 2.4 else "top-right"
        self._compact_corner_actual = corner
        return emulator_tracker.dock_position(rect, size, COMPACT_MARGIN, corner)

    def _configure_native_overlay(self):
        if self._overlay_windows:
            if self._tracker:
                for surface, win in self._overlay_windows.items():
                    if surface not in self._overlay_loaded:
                        continue
                    try:
                        hwnd = self._tracker.own_window_handle(
                            win, f"DigiTracker {surface}"
                        )
                        if hwnd:
                            self._overlay_hwnds[surface] = hwnd
                    except Exception:
                        pass
            if not self._compact:
                self._overlay_input.restore()
                self._overlay_native_by_surface = {}
                self._overlay_native_status = {"passive": False, "error": ""}
                return
            hotkey = self._overlay_input.status()
            if not hotkey.get("registered"):
                self._overlay_input.restore()
                error = str(hotkey.get("error") or
                            "Hotkey global indisponível; modo de recuperação ativo.")
                self._overlay_native_status = {"passive": False, "error": error}
                return
            enabled = set(self._enabled_compact_surfaces())
            statuses = {}
            for surface, hwnd in list(self._overlay_hwnds.items()):
                if surface not in self._overlay_loaded:
                    continue
                if surface not in enabled:
                    self._overlay_input.restore(hwnd)
                    continue
                expected = self._overlay_expected_sizes.get(surface) or self._surface_size(
                    surface, self._current_overlay_rect()
                )
                result = self._overlay_input.apply_passive(
                    hwnd, expected_size=expected, opacity=100,
                    click_through=not self._compact_edit_mode,
                )
                statuses[surface] = result
            self._overlay_native_by_surface = statuses
            errors = [str(value.get("error")) for value in statuses.values()
                      if value.get("error")]
            self._overlay_native_status = {
                "passive": bool(statuses) and all(value.get("passive") for value in statuses.values()),
                "click_through": bool(statuses) and all(
                    value.get("click_through", True) for value in statuses.values()
                ),
                "error": " · ".join(errors),
            }
            return
        if not self._window or not self._own_hwnd:
            return
        # O backend WinForms pode trocar o controle nativo após o WebView2
        # terminar de carregar. Re-resolver mantém o estilo no HWND raiz e
        # impede que WS_EX_TRANSPARENT seja aplicado ao conteúdo interno.
        if self._tracker:
            try:
                self._own_hwnd = self._tracker.own_window_handle(self._window)
            except Exception:
                pass
        if self._compact:
            hotkey = self._overlay_input.status()
            if not hotkey.get("registered"):
                # Nunca deixe o usuário preso numa janela passa-clique sem uma
                # hotkey funcional para sair. Nesse caso o HUD continua
                # interativo e a interface mostra um botão de recuperação.
                self._overlay_input.restore()
                self._overlay_native_status = {
                    "passive": False,
                    "error": str(hotkey.get("error") or
                                 "Hotkey global indisponível; modo de recuperação ativo."),
                }
                return
            expected = self._compact_expected_size or self._compact_size(
                self._current_overlay_rect(), variant=self._compact_variant
            )
            result = self._overlay_input.apply_passive(
                self._own_hwnd,
                expected_size=expected,
                opacity=100,
                # Os dois HUDs permanecem passivos durante a gameplay. Mouse
                # só entra no modo de edição temporário (Ctrl+Alt+E).
                click_through=not self._compact_edit_mode,
            )
            self._overlay_native_status = {
                "passive": bool(result.get("passive")),
                "click_through": bool(result.get("click_through", True)),
                "error": str(result.get("error") or ""),
            }
        else:
            self._overlay_input.restore()
            self._overlay_native_status = {"passive": False, "error": ""}

    def _toggle_compact_visibility(self):
        """Ctrl+Alt+G apenas mostra/oculta, sem misturar variante e visibilidade."""
        self.set_compact(not self._compact, from_user=True)

    def _cycle_compact_state(self):
        """Alias legado: versões antigas chamavam um ciclo de três estados."""
        self._toggle_compact_visibility()

    def _schedule_edit_timeout(self):
        if self._compact_edit_timer:
            self._compact_edit_timer.cancel()
        self._compact_edit_timer = threading.Timer(
            15.0, lambda: self.set_overlay_edit_mode(False, from_hotkey=False)
        )
        self._compact_edit_timer.daemon = True
        self._compact_edit_timer.start()

    def set_overlay_edit_mode(self, value=None, from_hotkey: bool = False) -> dict:
        """Libera mouse por 15 segundos, sem reativar teclado ou gamepad."""
        if value is None:
            value = not self._compact_edit_mode
        value = bool(value) and self._compact
        self._compact_edit_mode = value
        if value:
            self._schedule_edit_timeout()
        elif self._compact_edit_timer:
            self._compact_edit_timer.cancel()
            self._compact_edit_timer = None
        self._configure_native_overlay()
        self._notify_ui_compact(self._compact, self._compact_state)
        return {"ok": True, "editing": value, "state": self._compact_state,
                "from_hotkey": bool(from_hotkey)}

    def touch_overlay_edit_mode(self) -> dict:
        if self._compact_edit_mode:
            self._schedule_edit_timeout()
        return {"ok": True, "editing": self._compact_edit_mode}

    def set_compact_state(self, state: str, from_user: bool = True) -> dict:
        state = str(state or "").lower()
        if state not in ("minimal", "expanded", "both"):
            return {"ok": False, "error": "Estado compacto inválido."}
        if from_user and self._auto_collapse_timer:
            self._auto_collapse_timer.cancel()
            self._auto_collapse_timer = None
        self._compact_variant = state
        self._compact_state = state
        enabled = set(self._surfaces_for_variant(state))
        surfaces = self._compact_surfaces_config()
        surfaces["summary"]["enabled"] = "summary" in enabled
        surfaces["details"]["enabled"] = "details" in enabled
        self.settings["compact_surfaces"] = surfaces
        if from_user:
            with self._lock:
                self.settings["compact_view"] = state
                save_settings(self.settings)
        if self._overlay_windows:
            if not self._compact:
                self.set_compact(True, from_user=from_user)
            else:
                self._layout_overlay_surfaces(self._current_overlay_rect(), resize=True)
                self._configure_native_overlay()
                self._notify_ui_compact(True, state)
            return {"ok": True, "compact": True, "state": state,
                    "editing": self._compact_edit_mode,
                    "surfaces": list(self._enabled_compact_surfaces())}
        if not self._compact:
            self.set_compact(True, from_user=from_user)
        rect = self._current_overlay_rect()
        size = self._compact_size(rect, variant=state)
        self._compact_expected_size = size
        self._overlay_size = size
        dock = self._dock_for(rect, size) if rect else None
        if self._window:
            def resize_and_dock():
                self._window.resize(*size)
                if dock:
                    _move_after_resize(self._window, dock)
            self._window_op(resize_and_dock)
        self._configure_native_overlay()
        self._notify_ui_compact(True, state)
        return {"ok": True, "compact": True, "state": state,
                "editing": self._compact_edit_mode}

    def get_compact_config(self) -> dict:
        """Tamanho e contagens do overlay compacto para a UI."""
        with self._lock:
            w = int(self.settings.get("compact_width", COMPACT_SIZE[0]))
            h = int(self.settings.get("compact_height", COMPACT_SIZE[1]))
            last = int(self.settings.get("compact_last", 2))
            nxt = int(self.settings.get("compact_next", 0))
            values = {
                "expanded_width": int(self.settings.get("compact_expanded_width", COMPACT_EXPANDED_SIZE[0])),
                "expanded_height": int(self.settings.get("compact_expanded_height", COMPACT_EXPANDED_SIZE[1])),
                "size_mode": self.settings.get("compact_size_mode", "auto"),
                "tab": self.settings.get("compact_tab", "achievements"),
                "view": self.settings.get("compact_view", "minimal"),
                "corner": self.settings.get("compact_corner", "auto"),
                "background_mode": self.settings.get("compact_background_mode", "background"),
                "opacity": 100,
                "hotkey": self.settings.get("compact_hotkey", "ctrl+alt+g"),
                "edit_hotkey": self.settings.get("compact_edit_hotkey", "ctrl+alt+e"),
                "editing": self._compact_edit_mode,
                "variant": self._compact_variant,
                "surfaces": self._compact_surfaces_config(),
                "auto_expand": bool(self.settings.get("compact_auto_expand", False)),
                "auto_collapse_seconds": int(self.settings.get("compact_auto_collapse_seconds", 0)),
            }
        return {"ok": True, "width": w, "height": h,
                "last": max(0, last), "next": max(0, nxt), **values}

    def set_compact_config(self, width=None, height=None, last=None, next=None,
                           size_mode=None, content=None, tab=None, corner=None, opacity=None,
                           hotkey=None, edit_hotkey=None, auto_expand=None, auto_collapse_seconds=None,
                           expanded_width=None, expanded_height=None,
                           background_mode=None, view=None) -> dict:
        """Salva o tamanho/contagens do compacto. Se já estiver em modo compacto,
        redimensiona a janela na hora."""
        with self._lock:
            if width is not None:
                self.settings["compact_width"] = int(width)
            if height is not None:
                self.settings["compact_height"] = int(height)
            if expanded_width is not None:
                self.settings["compact_expanded_width"] = int(expanded_width)
            if expanded_height is not None:
                self.settings["compact_expanded_height"] = int(expanded_height)
            if last is not None:
                self.settings["compact_last"] = max(0, int(last))
            if next is not None:
                self.settings["compact_next"] = max(0, int(next))
            if size_mode in ("auto", "manual"):
                self.settings["compact_size_mode"] = size_mode
            # `content` e aceito apenas como migracao das UIs 0.8.6.
            selected_tab = tab or content
            if selected_tab in ("achievements", "guide"):
                self.settings["compact_tab"] = selected_tab
            if view in ("minimal", "expanded", "both"):
                self.settings["compact_view"] = view
                self._compact_variant = view
                enabled = set(self._surfaces_for_variant(view))
                surfaces = self._compact_surfaces_config()
                surfaces["summary"]["enabled"] = "summary" in enabled
                surfaces["details"]["enabled"] = "details" in enabled
                self.settings["compact_surfaces"] = surfaces
                if self._compact:
                    self._compact_state = view
            if corner in ("auto", "top-right", "bottom-right", "top-left", "bottom-left"):
                self.settings["compact_corner"] = corner
            # Mantido na assinatura para compatibilidade com versões antigas da UI.
            # O HUD agora é sempre opaco para preservar legibilidade.
            if hotkey is not None:
                try:
                    emulator_tracker.parse_hotkey(hotkey)
                except ValueError as exc:
                    return {"ok": False, "error": str(exc)}
                self.settings["compact_hotkey"] = str(hotkey).strip().lower()
            if edit_hotkey is not None:
                try:
                    emulator_tracker.parse_hotkey(edit_hotkey)
                except ValueError as exc:
                    return {"ok": False, "error": str(exc)}
                self.settings["compact_edit_hotkey"] = str(edit_hotkey).strip().lower()
            if auto_expand is not None:
                self.settings["compact_auto_expand"] = bool(auto_expand)
            if auto_collapse_seconds is not None:
                self.settings["compact_auto_collapse_seconds"] = max(0, min(60, int(auto_collapse_seconds)))
            if background_mode in ("background", "cover", "title", "solid"):
                self.settings["compact_background_mode"] = background_mode
            save_settings(self.settings)
        win = self._window
        if self._overlay_windows and self._compact and any(value is not None for value in (
                width, height, expanded_width, expanded_height, size_mode, view)):
            self._layout_overlay_surfaces(self._current_overlay_rect(), resize=True)
        elif win and self._compact and any(value is not None for value in (
                width, height, expanded_width, expanded_height, size_mode, view)):
            w, h = self._compact_size(self._current_overlay_rect())
            self._compact_expected_size = (w, h)
            self._window_op(lambda: win.resize(w, h))
        if hotkey is not None or edit_hotkey is not None:
            self._start_overlay_hotkeys()
        self._configure_native_overlay()
        return self.get_compact_config()

    def _start_overlay_hotkeys(self):
        value = str(self.settings.get("compact_hotkey", "ctrl+alt+g"))
        edit_value = str(self.settings.get("compact_edit_hotkey", "ctrl+alt+e"))
        if (value == self._hotkey_value and edit_value == self._edit_hotkey_value
                and self._overlay_input.status().get("registered")):
            return
        starter = getattr(self._overlay_input, "start_hotkeys", None)
        if starter:
            result = starter({
                "visibility": (value, self._toggle_compact_visibility),
                "edit": (edit_value, lambda: self.set_overlay_edit_mode(None, from_hotkey=True)),
            })
        else:  # doubles antigos e plataformas sem suporte
            result = self._overlay_input.start_hotkey(value, self._toggle_compact_visibility)
        self._hotkey_value = value if result.get("ok") else ""
        self._edit_hotkey_value = edit_value if result.get("ok") else ""
        if not result.get("ok"):
            self._overlay_native_status["error"] = str(result.get("error") or "")

    def _start_overlay_hotkey(self):
        """Compatibilidade com extensoes/testes da versao anterior."""
        return self._start_overlay_hotkeys()

    def _notify_ui_compact(self, compact: bool, state: str | None = None):
        """O backend mudou o modo sozinho — o JS precisa saber para re-renderizar
        (o polling de 5s do dashboard só perceberia depois)."""
        win = self._window
        if not win:
            return
        state = state or (self._compact_state if compact else "hidden")
        js = ("window.onOverlayChanged && window.onOverlayChanged("
              f"{str(bool(compact)).lower()}, {json.dumps(state)}, "
              f"{str(bool(self._compact_edit_mode)).lower()})")
        try:
            win.evaluate_js(js)
        except Exception:
            pass
        self._notify_overlay_surfaces()

    def _notify_overlay_surfaces(self):
        """Atualiza os dois documentos compactos sem reinicializar o dashboard."""
        for surface, win in list(self._overlay_windows.items()):
            if surface not in self._overlay_loaded:
                continue
            try:
                win.evaluate_js(
                    "window.onCompactSurfaceChanged && "
                    f"window.onCompactSurfaceChanged({json.dumps(surface)})"
                )
            except Exception:
                pass

    def get_overlay_status(self) -> dict:
        tracker = self._tracker
        raw = tracker.status() if tracker and hasattr(tracker, "status") else {
            "detected": False,
            "error": "O rastreador ainda não iniciou.",
        }
        rect = raw.get("rect")
        result = {
            "ok": not bool(self._overlay_error),
            "enabled": bool(self.settings.get("auto_overlay", True)),
            "detected": bool(raw.get("detected")),
            "title": str(raw.get("title") or ""),
            "process": str(raw.get("process") or ""),
            "class": str(raw.get("class") or ""),
            "rect": list(rect) if rect else [],
            "last_check": raw.get("last_check") or self._overlay_last_check,
            "error": self._overlay_error or str(raw.get("error") or ""),
            "native_input_mode": ("passive" if self._overlay_native_status.get("click_through", True)
                                  else "interactive") if self._overlay_native_status.get("passive") else "fallback",
            "hotkey_registered": bool(self._overlay_input.status().get("registered")),
            "hotkey": self._overlay_input.status().get("hotkey", ""),
            "hotkeys": self._overlay_input.status().get("bindings", {}),
            "hotkey_error": self._overlay_input.status().get("error", "") or self._overlay_native_status.get("error", ""),
            "compact_state": self._compact_state,
            "compact_variant": self._compact_variant,
            "compact_view": self.settings.get("compact_view", "minimal"),
            "editing": self._compact_edit_mode,
            "edit_hotkey": self.settings.get("compact_edit_hotkey", "ctrl+alt+e"),
            "actual_size": list(self._compact_expected_size or []),
            "corner": self._compact_corner_actual,
        }
        surface_config = self._compact_surfaces_config()
        surface_status = {}
        surface_rect = rect or self._fallback_overlay_rect()
        for surface in ("summary", "details"):
            expected = self._overlay_expected_sizes.get(surface) or self._surface_size(
                surface, surface_rect
            )
            dock = self._surface_dock(surface, surface_rect, expected)
            native = self._overlay_native_by_surface.get(surface) or {}
            surface_status[surface] = {
                "enabled": bool(surface_config[surface].get("enabled")),
                "loaded": surface in self._overlay_loaded,
                "visible": bool(self._compact and surface_config[surface].get("enabled")),
                "native_handle": bool(self._overlay_hwnds.get(surface)),
                "passive": bool(native.get("passive")),
                "click_through": bool(native.get("click_through", True)),
                "size": list(expected),
                "dock": list(dock),
                "corner": surface_config[surface].get("corner"),
                "error": str(native.get("error") or ""),
            }
        result["surfaces"] = surface_status
        if rect:
            size = self._overlay_size_for(rect)
            result["overlay_size"] = list(size)
            result["dock"] = list(self._dock_for(rect, size))
        else:
            result["overlay_size"] = []
            result["dock"] = []
        return result

    def test_overlay_detection(self) -> dict:
        try:
            if self._tracker is None:
                patterns = self.settings.get("emulators") or None
                self._tracker = emulator_tracker.create_tracker(patterns)
            self._tracker.find_emulator_window()
            self._overlay_error = ""
            self._overlay_last_check = time.time()
        except Exception as exc:
            self._overlay_error = f"{type(exc).__name__}: {exc}"
            self._overlay_last_check = time.time()
        return self.get_overlay_status()

    def overlay_loop(self):
        """Verifica a cada OVERLAY_INTERVAL se há emulador aberto."""
        patterns = self.settings.get("emulators") or None
        self._tracker = emulator_tracker.create_tracker(patterns)
        self._overlay = emulator_tracker.OverlayWatcher(
            self._tracker.find_emulator_window, self._overlay_actions()
        )
        # HWND da própria janela (Windows) para re-aplicar o always-on-top
        try:
            self._own_hwnd = self._tracker.own_window_handle(self._window)
        except Exception:
            self._own_hwnd = None
        for surface, win in self._overlay_windows.items():
            if surface not in self._overlay_loaded:
                continue
            try:
                hwnd = self._tracker.own_window_handle(win, f"DigiTracker {surface}")
                if hwnd:
                    self._overlay_hwnds[surface] = hwnd
            except Exception:
                pass
        self._start_overlay_hotkey()
        self._configure_native_overlay()

        while True:
            time.sleep(OVERLAY_INTERVAL)
            try:
                if not self._own_hwnd:
                    self._own_hwnd = self._tracker.own_window_handle(self._window)
                for surface, win in self._overlay_windows.items():
                    if surface not in self._overlay_hwnds:
                        hwnd = self._tracker.own_window_handle(win, f"DigiTracker {surface}")
                        if hwnd:
                            self._overlay_hwnds[surface] = hwnd
                if self._own_hwnd and self._compact:
                    self._configure_native_overlay()
                with self._lock:
                    enabled = self.settings.get("auto_overlay", True)
                exclusivo = enabled and self._tracker.is_exclusive_fullscreen()
                self._overlay.step(enabled=enabled, user_compact=self._compact,
                                   exclusive=exclusivo)
                self._overlay_error = ""
                self._overlay_last_check = time.time()
            except Exception as exc:
                # O laço continua vivo, mas a falha deixa de ser invisível: aparece
                # no diagnóstico das Configurações e pode ser reproduzida na hora.
                self._overlay_error = f"{type(exc).__name__}: {exc}"
                self._overlay_last_check = time.time()

    # ----------------------------- helpers ---------------------------------- #
    def _download_art(self, slug: str, progress: dict) -> dict:
        """Baixa capa, tela de título e screenshot do jogo (o que existir).

        Devolve `{"box": "/assets/art/slug/box.png", ...}` com apenas as artes
        que vieram. Jogo sem alguma delas é normal na RetroAchievements — os
        templates tratam a ausência.
        """
        art = {}
        if not self._client:
            return art
        for nome, campo in GAME_ART.items():
            caminho = progress.get(campo) or ""
            if not caminho:
                continue
            destino = ART_DIR / slug / f"{nome}.png"
            if self._client.download_image(caminho, destino):
                art[nome] = f"/assets/art/{slug}/{nome}.png"
        return art

    @staticmethod
    def _clean_walkthrough(steps) -> list[dict]:
        """Normaliza as etapas vindas do frontend: cada conquista guarda só o
        `id`. O campo `mode` de versões antigas (Normal/Hard curatorial) é
        descartado — hoje o modo vem da RetroAchievements, não do arquivo."""
        clean = []
        for i, step in enumerate(steps or [], start=1):
            ids = []
            for entry in step.get("achievements", []):
                try:
                    ids.append({"id": int(entry["id"] if isinstance(entry, dict) else entry)})
                except (KeyError, TypeError, ValueError):
                    continue
            if ids:
                clean.append({
                    "step": step.get("step", i),
                    "area": step.get("area") or f"Etapa {i}",
                    "achievements": ids,
                })
        return clean

    @staticmethod
    def _pick_accent(slug: str) -> str:
        """Cor de destaque do jogo: a menos usada na biblioteca. Contar arquivos
        repetia cores quando um jogo era apagado (5 criados, 1 removido -> o
        próximo recebia a cor de um jogo existente). Empates são desfeitos pelo
        slug, de forma estável entre execuções."""
        used = {c: 0 for c in ACCENTS}
        for path in GAMES_DIR.glob("*.json"):
            game = load_game_file(path)
            accent = (game or {}).get("accent")
            if accent in used:
                used[accent] += 1
        fewest = min(used.values())
        pool = [c for c in ACCENTS if used[c] == fewest]
        return pool[sum(map(ord, slug)) % len(pool)]

    @staticmethod
    def _badge_url(slug: str, badge: str) -> str:
        if not badge:
            return ""
        return f"/assets/badges/{slug}/{badge}.png"

    @staticmethod
    def _progress_playtime(progress: dict) -> dict | None:
        raw = progress.get("UserTotalPlaytime")
        if raw in (None, "", 0, "0"):
            return None
        seconds = None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            seconds = max(0, int(raw))
        elif isinstance(raw, str) and raw.strip().isdigit():
            seconds = max(0, int(raw.strip()))
        if seconds is not None:
            if seconds <= 0:
                return None
            hours, remainder = divmod(seconds, 3600)
            minutes = remainder // 60
            label = f"{hours}h {minutes:02d}min" if hours else f"{minutes}min"
            return {"seconds": seconds, "label": label, "source": "retroachievements"}
        label = str(raw).strip()
        return {"seconds": None, "label": label, "source": "retroachievements"} if label else None

    def _update_completion_events(self, game: dict, completion: dict, score: dict,
                                  playtime: dict | None) -> dict:
        state = game.setdefault("completion_events", {})
        events = state.setdefault("events", [])
        if not state.get("initialized"):
            # Jogos concluídos antes desta versão não devem abrir um modal na
            # migração, mas a celebração precisa existir para ser reproduzida
            # posteriormente pelo Hall.
            now = time.time()
            if completion.get("softcore_complete"):
                events.append({
                    "id": f"softcore-migrated-{int(now * 1000)}", "kind": "softcore",
                    "created_at": now, "pending": False,
                    "achievements": completion.get("earned", 0),
                    "points": score.get("earned", 0), "playtime": playtime,
                    "date": completion.get("softcore_date", ""),
                })
            if completion.get("mastery_complete"):
                events.append({
                    "id": f"mastery-migrated-{int(now * 1000)}", "kind": "mastery",
                    "created_at": now, "pending": False,
                    "achievements": completion.get("hardcore", 0),
                    "points": score.get("hardcore", 0), "playtime": playtime,
                    "date": completion.get("mastery_date", ""),
                })
            state.update({
                "initialized": True,
                "softcore_seen": bool(completion.get("softcore_complete")),
                "mastery_seen": bool(completion.get("mastery_complete")),
            })
        else:
            new_soft = bool(completion.get("softcore_complete")) and not state.get("softcore_seen")
            new_mastery = bool(completion.get("mastery_complete")) and not state.get("mastery_seen")
            now = time.time()
            if new_soft:
                events.append({
                    "id": f"softcore-{int(now * 1000)}", "kind": "softcore",
                    "created_at": now, "pending": not new_mastery,
                    "achievements": completion.get("earned", 0),
                    "points": score.get("earned", 0), "playtime": playtime,
                    "date": completion.get("softcore_date", ""),
                })
            if new_mastery:
                events.append({
                    "id": f"mastery-{int(now * 1000)}", "kind": "mastery",
                    "created_at": now, "pending": True,
                    "achievements": completion.get("hardcore", 0),
                    "points": score.get("hardcore", 0), "playtime": playtime,
                    "date": completion.get("mastery_date", ""),
                })
            state["softcore_seen"] = bool(state.get("softcore_seen") or new_soft)
            state["mastery_seen"] = bool(state.get("mastery_seen") or new_mastery)
        # histórico limitado, preservando celebrações mais recentes
        state["events"] = events[-50:]
        path = GAMES_DIR / f"{game.get('slug', '')}.json"
        if path.exists():
            try:
                path.write_text(json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        return state

    # -------------------- cálculo de progresso por jogo --------------------- #
    def _sync_game(self, game: dict) -> bool:
        """Consulta a API e recalcula o estado de um jogo. Devolve False se a API
        nos limitou — o laço de sincronização usa isso para recuar."""
        slug = game["slug"]

        earned_map: dict[int, dict] = {}
        progress: dict = {}
        limited = False
        if self._client:
            try:
                progress = self._platform_progress(
                    int(game.get("external_game_id") or game["retroachievements_game_id"])
                )
                earned_map = self._client.parse_achievements(progress)
                # garante badges em cache (jogo pode ter sido editado)
                for a in earned_map.values():
                    if a["badge"]:
                        self._client.download_badge(
                            a["badge"], BADGES_DIR / slug / f"{a['badge']}.png"
                        )
                # jogos salvos antes das artes existirem: completa sem migração
                if not game.get("art"):
                    art = self._download_art(slug, progress)
                    if art:
                        game["art"] = art
                        caminho = GAMES_DIR / f"{slug}.json"
                        if caminho.exists():
                            caminho.write_text(
                                json.dumps(game, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
            except RARateLimited:
                limited = True
                earned_map = {}
            except RAError:
                earned_map = {}

            # Sem resposta da API, recalcular zeraria o progresso já exibido.
            # Mantemos o último estado bom em vez de "desconquistar" tudo.
            if not earned_map:
                with self._lock:
                    if slug in self.state:
                        return not limited

        self._apply_progress(game, earned_map, progress)
        return not limited

    def _apply_progress(self, game: dict, earned_map: dict, progress_meta: dict | None = None) -> dict:
        """Parte pura do cálculo: cruza o walkthrough salvo com o que a API disse
        estar destravado e publica o resultado em `self.state`. Separado de
        `_sync_game` para a importação em lote reaproveitar o progresso que já
        veio na mesma resposta, sem uma segunda chamada de rede."""
        slug = game["slug"]
        meta = game.get("achievements_meta", {})
        progress_meta = progress_meta if isinstance(progress_meta, dict) else {}

        ordered = []
        # Os "modos" agora vêm da RetroAchievements, não de curadoria: uma
        # conquista destravada sem savestate é HARDCORE; destravada só no modo
        # casual é SOFTCORE. Os dois são exclusivos, então somam o total obtido.
        modes = {m: {"total": 0, "earned": 0} for m in DEFAULT_MODES}
        last_earned = None

        # O conjunto canônico vem da RA. A ordem do walkthrough continua sendo
        # usada quando disponível, mas nunca define o denominador de pontos ou
        # 100%: conquistas ausentes do guia são anexadas na ordem oficial.
        ordered_ids = []
        locations = {}
        for step in sorted(game.get("walkthrough", []), key=lambda s: s.get("step", 0)):
            for entry in step.get("achievements", []):
                aid = int(entry["id"])
                if aid not in ordered_ids:
                    ordered_ids.append(aid)
                    locations[aid] = (step.get("step"), step.get("area", ""))
        for aid in sorted(earned_map, key=lambda key: (
                earned_map[key].get("display_order", 10_000_000), key)):
            if aid not in ordered_ids:
                ordered_ids.append(aid)

        score = {"available": 0, "earned": 0, "hardcore": 0,
                 "true_ratio_available": 0, "true_ratio_earned": 0}
        metadata_changed = False
        for aid in ordered_ids:
                m = meta.get(str(aid), {})
                live = earned_map.get(aid, {})
                earned = bool(live.get("earned"))
                hardcore = bool(live.get("hardcore"))
                date = live.get("date", "")
                badge = m.get("badge") or live.get("badge", "")
                points = int(live.get("points") or m.get("points") or 0)
                true_ratio = int(live.get("true_ratio") or 0)
                achievement_type = str(live.get("achievement_type") or
                                       m.get("achievement_type") or "").strip().lower()
                if achievement_type and m.get("achievement_type") != achievement_type:
                    meta.setdefault(str(aid), {})["achievement_type"] = achievement_type
                    metadata_changed = True
                step_number, area = locations.get(aid, (None, "Ordem RetroAchievements"))

                row = {
                    "id": aid,
                    "name": m.get("title") or live.get("title") or f"#{aid}",
                    "desc": m.get("desc") or live.get("desc") or "",
                    "earned": earned,
                    "hardcore": hardcore,
                    "mode": "hardcore" if hardcore else "softcore",
                    "date": fmt_date(date),
                    "date_raw": date,
                    "date_softcore": fmt_date(live.get("date_softcore", "")),
                    "date_softcore_raw": live.get("date_softcore", ""),
                    "date_hardcore": fmt_date(live.get("date_hardcore", "")),
                    "date_hardcore_raw": live.get("date_hardcore", ""),
                    "badge_url": self._badge_url(slug, badge),
                    "step": step_number,
                    "area": area,
                    "points": points,
                    "true_ratio": true_ratio,
                    "rarity": live.get("rarity"),
                    "rarity_hardcore": live.get("rarity_hardcore"),
                    "achievement_type": achievement_type,
                }
                ordered.append(row)
                score["available"] += points
                score["true_ratio_available"] += true_ratio

                for key in modes:
                    modes[key]["total"] += 1
                if earned:
                    modes["hardcore" if hardcore else "softcore"]["earned"] += 1
                    score["earned"] += points
                    score["true_ratio_earned"] += true_ratio
                    if hardcore:
                        score["hardcore"] += points

                if earned and date:
                    if last_earned is None or date > last_earned["date_raw"]:
                        last_earned = row

        # Guarda várias próximas: o overlay compacto escolhe quantas mostrar
        # conforme o tamanho ajustado da janela.
        pending_missables = [r for r in ordered
                             if r.get("achievement_type") == "missable" and not r.get("hardcore")]
        next_ids = [r["id"] for r in pending_missables]
        next_ids.extend(r["id"] for r in ordered if not r["earned"] and r["id"] not in next_ids)
        next_ids = next_ids[:12]
        # Mastery = 100% em hardcore. É o número que a RA usa para o badge
        # dourado; as obtidas só em softcore precisam ser refeitas sem savestate.
        total = len(ordered)
        hardcore_count = modes["hardcore"]["earned"]
        softcore_only = [r for r in ordered if r["earned"] and not r["hardcore"]]
        mastery = {
            "total": total,
            "hardcore": hardcore_count,
            "earned": hardcore_count + len(softcore_only),
            "softcore_only": len(softcore_only),
            "remaining": total - hardcore_count,
            "percent": round(hardcore_count / total * 100) if total else 0,
            "complete": total > 0 and hardcore_count >= total,
            "softcore_ids": [r["id"] for r in softcore_only],
        }
        softcore_complete = total > 0 and mastery["earned"] >= total
        mastery_dates = [r.get("date_hardcore_raw") for r in ordered if r.get("date_hardcore_raw")]
        softcore_dates = [r.get("date_raw") for r in ordered if r.get("date_raw")]
        playtime = self._progress_playtime(progress_meta)
        completion = {
            "softcore_complete": softcore_complete,
            "mastery_complete": mastery["complete"],
            "earned": mastery["earned"],
            "hardcore": hardcore_count,
            "total": total,
            "softcore_date": fmt_date(max(softcore_dates)) if softcore_dates else "",
            "mastery_date": fmt_date(max(mastery_dates)) if mastery_dates else "",
            "softcore_date_raw": max(softcore_dates) if softcore_dates else "",
            "mastery_date_raw": max(mastery_dates) if mastery_dates else "",
            "highest_award_kind": progress_meta.get("HighestAwardKind") or "",
            "highest_award_date": fmt_date(progress_meta.get("HighestAwardDate") or ""),
            "highest_award_date_raw": progress_meta.get("HighestAwardDate") or "",
        }
        game["score"] = score
        game["playtime"] = playtime
        game["completion"] = completion
        if not game.get("palette"):
            try:
                art_path = self._palette_art_path(game)
                if art_path:
                    game["palette"] = self._palette_from_path(art_path)
                    game["accent"] = game["palette"]["primary"]
            except Exception:
                # Paleta é enriquecimento; arte corrompida nunca impede sync.
                pass
        completion_events = self._update_completion_events(
            game, completion, score, playtime
        )

        smart_bundle = {}
        if game.get("guide") or self._guides.current(slug):
            if not self._guides.source(slug):
                self._capture_smart_source(game, {"source": "migration"})
            smart_bundle = self._guides.bundle(slug)
            smart_bundle["media"] = self._guide_media.list(slug)
            document = smart_bundle.get("current") or {}
            effective = dict(smart_bundle.get("progress") or {})
            completed = set(effective.get("completed") or [])
            earned_names = [_normalize_text(row["name"]) for row in ordered if row["earned"]]
            earned_ids = {int(row["id"]) for row in ordered if row["earned"]}
            external = []
            for chapter in document.get("chapters") or []:
                for block in chapter.get("blocks") or []:
                    haystack = _normalize_text(f"{block.get('title', '')} {block.get('text', '')}")
                    achievement_id = int(block.get("achievement_id") or 0)
                    exact_id = achievement_id > 0 and achievement_id in earned_ids
                    legacy_name = achievement_id <= 0 and any(
                        name and name in haystack for name in earned_names)
                    if block.get("type") == "achievement" and (exact_id or legacy_name):
                        completed.add(block.get("id"))
                        external.append(block.get("id"))
            effective["completed"] = sorted(completed)
            smart_bundle["effective_progress"] = effective
            smart_bundle["external_completed"] = external
            smart_bundle["next_objective"] = self._guides.next_objective(document, effective)
            smart_bundle["walkthrough_sources"] = self._guides.walkthrough_sources(slug)
            smart_bundle["merge_status"] = self.get_walkthrough_merge_status(slug)
            smart_bundle["atlas_jobs"] = self._guides.system_sources(slug)
            smart_bundle["atlas_drafts"] = [self._guides.atlas_draft(slug, item["id"])
                                             for item in smart_bundle["atlas_jobs"] if item.get("status") == "suggested"]

        detail = {
            "slug": slug,
            "title": game["title"],
            "platform": game["platform"],
            "genre": game.get("genre", ""),
            "year": game.get("year", ""),
            "players": game.get("players", ""),
            "provider_id": game.get("provider_id", "retroachievements"),
            "icon": game.get("icon", ""),
            "art": game.get("art", {}),
            "art_meta": game.get("art_meta", {}),
            "accent": game.get("accent", ACCENTS[0]),
            "palette": game.get("palette", {}),
            "modes": modes,
            "mastery": mastery,
            "score": score,
            "playtime": playtime,
            "completion": completion,
            "completion_events": completion_events,
            "compact_tab": (game.get("compact_ui") or {}).get(
                "tab", self.settings.get("compact_tab", "achievements")),
            "achievements": ordered,
            "next_ids": next_ids,
            "pending_missables": pending_missables,
            "last_earned": last_earned,
            "guide": game.get("guide", []),   # seções de dicas/tutoriais do PDF
            "smart_guide": smart_bundle,
        }
        with self._lock:
            previous = self.state.get(slug)
            self.state[slug] = detail
        if metadata_changed:
            try:
                game["achievements_meta"] = meta
                path = GAMES_DIR / f"{slug}.json"
                temp = path.with_suffix(".json.tmp")
                temp.write_text(json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temp, path)
            except OSError:
                pass
        self._maybe_expand_for_unlock(previous, detail)
        self._record_experience(detail)
        return detail

    def _maybe_expand_for_unlock(self, previous, current):
        """Expande somente quando habilitado e uma nova conquista entrou."""
        if not self._compact or not self.settings.get("compact_auto_expand", False):
            return
        if not previous:
            return
        before = previous.get("last_earned") or {}
        after = current.get("last_earned") or {}
        if not after.get("date_raw") or after.get("date_raw") == before.get("date_raw"):
            return
        preferred = self.settings.get("compact_view", "minimal")
        # Em Troféus/Ambos a nova conquista já fica visível, então não altere
        # o layout escolhido pelo usuário só para reproduzir a mesma informação.
        if preferred in ("expanded", "both"):
            return
        self.set_compact_state("expanded", from_user=False)
        seconds = int(self.settings.get("compact_auto_collapse_seconds", 0) or 0)
        if seconds > 0:
            if self._auto_collapse_timer:
                self._auto_collapse_timer.cancel()
            self._auto_collapse_timer = threading.Timer(
                seconds, lambda: self.set_compact_state(preferred, from_user=False)
            )
            self._auto_collapse_timer.daemon = True
            self._auto_collapse_timer.start()

    def _kick_sync(self) -> bool:
        """Recarrega todos os jogos do disco e recomputa o estado uma vez.
        Devolve False se a API limitou alguma das consultas."""
        ok = True
        games = [g for g in (load_game_file(p) for p in GAMES_DIR.glob("*.json"))
                 if g and g.get("slug")]
        for i, game in enumerate(games):
            if i and self._client:
                time.sleep(SYNC_SPACING)   # não dispara a biblioteca inteira de uma vez
            if not self._sync_game(game):
                ok = False
        return ok

    def sync_loop(self):
        """Sincroniza a cada SYNC_INTERVAL. Se a API responder 429, dobra o
        intervalo (até SYNC_MAX_INTERVAL) e volta ao normal quando ela liberar.

        A cada AUTO_IMPORT_INTERVAL também procura jogos novos na conta — assim
        um jogo que você acabou de começar aparece sozinho na biblioteca."""
        interval = SYNC_INTERVAL
        # varredura inicial: o app abre já espelhando a conta
        self._schedule_auto_import()
        while True:
            ok = self._kick_sync()
            interval = SYNC_INTERVAL if ok else min(SYNC_MAX_INTERVAL, interval * 2)

            with self._lock:
                due = (time.time() - self.last_auto_import) >= AUTO_IMPORT_INTERVAL
                auto = self.settings["auto_import"]
            if ok and auto and due:
                self._schedule_auto_import()

            time.sleep(interval)


# ---------------------------------------------------------------------------- #
# Servidor estático interno (serve a UI + badges via http://127.0.0.1)
# ---------------------------------------------------------------------------- #
class _AssetHandler(SimpleHTTPRequestHandler):
    """Serve `ui/` a partir do bundle (read-only) e `assets/` a partir da pasta
    de dados gravável — necessário porque, empacotado, esses caminhos diferem."""

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0].split("#", 1)[0]
        rel = posixpath.normpath(unquote(path)).lstrip("/")
        # `normpath` preserva os `..` iniciais ("/../x" -> "../x"), então
        # descartamos qualquer segmento de subida antes de montar o caminho.
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        base = DATA_DIR if parts[:1] == ["assets"] else BUNDLE_DIR
        target = (base / Path(*parts)).resolve() if parts else base.resolve()
        # Cinto e suspensórios: symlinks dentro da árvore não podem escapar dela.
        try:
            target.relative_to(base.resolve())
        except ValueError:
            return str(base.resolve())
        return str(target)

    def log_message(self, *args):
        pass  # silencia o log do servidor estático


def start_static_server() -> int:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _AssetHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port


# ---------------------------------------------------------------------------- #
# Bootstrap
# ---------------------------------------------------------------------------- #
def main():
    emulator_tracker.enable_dpi_awareness()
    api = Api()
    normal_size = adaptive_normal_size()
    api._normal_size = normal_size
    port = start_static_server()
    url = f"http://127.0.0.1:{port}/ui/index.html"
    overlay_url = f"http://127.0.0.1:{port}/ui/overlay.html"

    window = webview.create_window(
        "DigiTracker",
        url=url,
        js_api=api,
        width=normal_size[0],
        height=normal_size[1],
        min_size=COMPACT_MIN,    # permite encolher até o menor overlay ajustável
        frameless=True,
        easy_drag=False,      # arrasto via .pywebview-drag-region
        on_top=True,
        background_color="#050c18",
    )
    api._window = window
    import notifications
    api._notifications = notifications.Notifications(api)
    notification_window = webview.create_window(
        "DigiTracker achievement", url=f"http://127.0.0.1:{port}/ui/notification.html",
        js_api=api._notifications.bridge, width=360, height=112, min_size=(360, 112),
        hidden=True, frameless=True, easy_drag=False, shadow=False, focus=False,
        on_top=True, background_color="#071629")
    notification_window.events.loaded += lambda: api._notifications.attach(notification_window)

    summary_window = webview.create_window(
        "DigiTracker summary",
        url=f"{overlay_url}?surface=summary",
        js_api=api,
        width=COMPACT_SIZE[0],
        height=COMPACT_SIZE[1],
        min_size=COMPACT_MINIMAL_MIN,
        hidden=True,
        frameless=True,
        easy_drag=False,
        shadow=False,
        focus=False,
        on_top=True,
        background_color="#07111f",
    )
    details_window = webview.create_window(
        "DigiTracker details",
        url=f"{overlay_url}?surface=details",
        js_api=api,
        width=COMPACT_EXPANDED_SIZE[0],
        height=COMPACT_EXPANDED_SIZE[1],
        min_size=COMPACT_EXPANDED_MIN,
        hidden=True,
        frameless=True,
        easy_drag=False,
        shadow=False,
        focus=False,
        on_top=True,
        background_color="#07111f",
    )
    # Disponibiliza as referências imediatamente para que uma detecção muito
    # rápida já saiba que deve usar o caminho multi-HUD. Os callbacks loaded
    # resolvem os HWNDs assim que cada WebView estiver pronto.
    api._overlay_windows = {"summary": summary_window, "details": details_window}
    summary_window.events.loaded += lambda: api.attach_overlay_window("summary", summary_window)
    details_window.events.loaded += lambda: api.attach_overlay_window("details", details_window)

    services_started = threading.Event()

    def start_runtime_services():
        # O rastreador pode redimensionar e alterar estilos nativos. Esperar o
        # evento `loaded` evita tocar no WinForms/WebView2 enquanto a janela
        # ainda está sendo construída — condição que podia deixar a build
        # onefile sem responder logo depois de uma atualização.
        if services_started.is_set():
            return
        services_started.set()
        threading.Thread(target=api.sync_loop, daemon=True).start()
        threading.Thread(target=api.overlay_loop, daemon=True).start()

    window.events.loaded += start_runtime_services
    if hasattr(window.events, "closed"):
        def close_background_services():
            api.stop_companion()
            api._notifications.close()
            api._experience.session(api._experience_account(), "pause")
            for overlay in api._overlay_windows.values():
                api._window_op(overlay.destroy)
        window.events.closed += close_background_services
    webview.start()


if __name__ == "__main__":
    main()
