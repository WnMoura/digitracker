"""DigiTracker — engine principal.

Abre uma janela pywebview always-on-top / sem moldura, serve a UI
(HTML/CSS/JS) por um servidor estático interno e expõe a lógica de backend
para o frontend via `js_api`. Um thread de sincronização consulta a
RetroAchievements a cada 30s e mantém o estado de cada jogo em memória.
"""

from __future__ import annotations

import base64
import io
import json
import os
import posixpath
import re
import sys
import threading
import time
import unicodedata
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

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
import guide_parser
import igdb
import image_fetch
import rawg
import steamgriddb
import updater
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
    "compact_width": 300,   # tamanho do overlay compacto (ajustável pelo usuário)
    "compact_height": 232,
    "compact_last": 2,      # quantas conquistas OBTIDAS mostrar no compacto
    "compact_next": 0,      # quantas PRÓXIMAS mostrar (0 = cabe o que couber)
}

# Limites do overlay compacto: não deixa encolher a ponto de nada caber, nem
# crescer tanto que deixe de ser um overlay.
COMPACT_MIN = (240, 150)
COMPACT_MAX = (640, 900)

# Quando "ajustar ao emulador" está ligado, o overlay ocupa esta fração da
# janela do emulador (preso a COMPACT_MIN/MAX) — cresce em jogo grande, encolhe
# em janela pequena.
OVERLAY_FIT_W = 0.26
OVERLAY_FIT_H = 0.44

ACCENTS = ["#D62839", "#F5C518", "#2DE2E6", "#27AE60"]

# Como a RetroAchievements classifica um desbloqueio. Não é dificuldade do jogo:
# hardcore = sem savestate/rewind/cheat (o único que conta para o Mastery);
# softcore = destravada no modo casual. Uma conquista cai em exatamente um dos
# dois, então os contadores somam o total.
DEFAULT_MODES = ("hardcore", "softcore")

NORMAL_SIZE = (1040, 680)
COMPACT_SIZE = (300, 232)   # janela mini/overlay com o progresso do jogo
COMPACT_MARGIN = 24         # distância do canto da tela ao entrar em modo compacto


# ---------------------------------------------------------------------------- #
# Utilidades
# ---------------------------------------------------------------------------- #
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", text) or "jogo"


def load_secrets() -> dict | None:
    if not SECRETS_PATH.exists():
        return None
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def load_settings() -> dict:
    """Preferências do app. Sempre devolve as chaves padrão preenchidas."""
    settings = dict(DEFAULT_SETTINGS)
    settings["dismissed"] = []
    settings["emulators"] = []
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return settings
    if isinstance(saved, dict):
        settings["auto_import"] = bool(saved.get("auto_import", True))
        settings["auto_overlay"] = bool(saved.get("auto_overlay", True))
        for chave in ("overlay_exit_fullscreen", "overlay_second_screen"):
            settings[chave] = bool(saved.get(chave, False))
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
        for key in ("compact_width", "compact_height", "compact_last", "compact_next"):
            value = saved.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                settings[key] = int(value)
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
    return "\n".join((page.extract_text() or "") for page in reader.pages)


# ---------------------------------------------------------------------------- #
# API exposta ao frontend (pywebview js_api)
# ---------------------------------------------------------------------------- #
class Api:
    def __init__(self):
        self._window = None
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
        self._pre_compact_pos: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self._updates = updater.UpdateManager(APP_VERSION, sys.executable)

        for d in (GAMES_DIR, CACHE_DIR, BADGES_DIR, ICONS_DIR, ART_DIR):
            d.mkdir(parents=True, exist_ok=True)

        secrets = load_secrets()
        if secrets and secrets.get("username") and secrets.get("api_key"):
            self._client = RAClient(secrets["username"], secrets["api_key"], CACHE_DIR)

    # ------------------------ status / configuração ------------------------- #
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
        }

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
        self._client = client
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
                    "icon": g.get("icon", ""),
                    "art": g.get("art", {}),
                    "accent": g["accent"],
                    "modes": g["modes"],
                    "mastery": g["mastery"],
                }
            )
        summaries.sort(key=lambda s: s["title"].lower())
        return summaries

    def get_game(self, slug: str) -> dict | None:
        with self._lock:
            return self.state.get(slug)

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
        progress = self._client.get_game_info_and_user_progress(int(game_id))
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
                }
                for a in ach_list
            },
            "guide": [],
        }
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # já popula o estado com o progresso que acabou de vir (sem nova chamada)
        self._apply_progress(game, achievements)
        return slug, [(slug, a["badge"]) for a in ach_list if a["badge"]]

    # --------------------------- importar (wizard) -------------------------- #
    def import_game(self, game_id: int) -> dict:
        """Passo 1 -> 2: baixa a lista completa de conquistas e cacheia ícones."""
        if not self._client:
            return {"ok": False, "error": "Não configurado."}
        try:
            progress = self._client.get_game_info_and_user_progress(int(game_id))
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
            "achievements_meta": {
                str(a["id"]): {
                    "title": a["title"],
                    "desc": a["desc"],
                    "badge": a["badge"],
                    "points": a.get("points", 0),   # usado p/ casar com o PDF do guia
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
            "walkthrough": steps,
            "achievements_meta": self.pending_import["achievements_meta"],
            "guide": payload.get("guide") or [],   # dicas/tutoriais extraídos do PDF
        }
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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
                "error": "O PDF não tem texto extraível (parece ser digitalizado/imagem).",
            }

        parsed = guide_parser.parse_guide(text)
        order = guide_parser.order_from_guide(
            parsed, self.pending_import["achievements_meta"], text
        )
        # Seções de dicas/tutoriais (exclui a lista de conquistas, já mostrada
        # no walkthrough).
        guide_sections = [s for s in parsed["sections"] if not s.get("is_achievements")]

        order["ok"] = True
        order["filename"] = filename
        order["guide"] = guide_sections
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

    def _process_game_tips(self, slug: str, op) -> dict:
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
            novas = op(sections, config)
        except guide_ai.GuideAIError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"Falha na IA: {exc}"}
        game["guide"] = novas
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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

    def _read_guide_sections(self, b64: str):
        """(ok, erro, seções) — extrai do PDF só as seções de dicas/tutoriais
        (remove a seção que lista as conquistas)."""
        try:
            raw = base64.b64decode((b64 or "").split(",", 1)[-1])
            text = extract_pdf_text(io.BytesIO(raw))
        except Exception as exc:
            return False, f"Não foi possível ler o PDF: {exc}", []
        if not _normalize_text(text):
            return False, "O PDF não tem texto extraível (parece ser digitalizado/imagem).", []
        parsed = guide_parser.parse_guide(text)
        guide = [s for s in parsed["sections"] if not s.get("is_achievements")]
        if not guide:
            return False, "Não encontrei seções de dicas/tutoriais neste PDF.", []
        return True, "", guide

    def extract_guide_pdf(self, b64: str, filename: str = "") -> dict:
        """Lê um PDF e devolve SÓ as dicas/tutoriais (não mexe em conquistas).
        Usado no wizard para anexar o guia sem reordenar nada."""
        ok, err, guide = self._read_guide_sections(b64)
        if not ok:
            return {"ok": False, "error": err}
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
        return {"ok": True, "sections": len(guide), "filename": filename}

    # --------------------- seletor de artes (multi-fonte) ------------------- #
    # A arte escolhida vira art.cover (retrato: overlay/lateral) e/ou
    # art.background (paisagem: fundo da lista) — separadas da arte da RA e
    # reversíveis. Cada fonte guarda a própria chave em secrets.json.
    _ROLE_FILE = {"cover": "cover.png", "background": "background.png"}
    _ROLE_ART = {"cover": "cover", "background": "background"}
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

    def set_game_cover(self, slug: str, url: str, role: str = "cover") -> dict:
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
        stamp = int(time.time())
        for r in roles:
            dest = ART_DIR / slug / self._ROLE_FILE[r]
            if not image_fetch.download_image(url, dest):
                return {"ok": False,
                        "error": "Não consegui baixar a imagem (o servidor recusou ou não é uma imagem)."}
            art[self._ROLE_ART[r]] = f"/assets/art/{slug}/{self._ROLE_FILE[r]}?v={stamp}"
        self._persist_art(slug, game, art)
        return {"ok": True, "art": art}

    def clear_game_cover(self, slug: str, role: str = "cover") -> dict:
        """Volta à arte padrão da RA no papel pedido (`cover`, `background` ou
        `both`): remove o campo e o arquivo baixado."""
        game = load_game_file(GAMES_DIR / f"{slug}.json")
        if not game:
            return {"ok": False, "error": "Jogo não encontrado."}
        roles = ["cover", "background"] if role == "both" else [role]
        art = dict(game.get("art") or {})
        for r in roles:
            if r not in self._ROLE_FILE:
                continue
            art.pop(self._ROLE_ART[r], None)
            try:
                (ART_DIR / slug / self._ROLE_FILE[r]).unlink(missing_ok=True)
            except OSError:
                pass
        self._persist_art(slug, game, art)
        return {"ok": True, "art": art}

    def _persist_art(self, slug: str, game: dict, art: dict) -> None:
        """Grava o `art` no config do jogo e reflete no estado ao vivo."""
        game["art"] = art
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self._lock:
            detail = self.state.get(slug)
            if detail is not None:
                detail["art"] = art

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
        self._compact = value
        win = self._window

        def op():
            if value:
                if not self._pre_compact_pos:
                    self._pre_compact_pos = (win.x, win.y)
                w, h = size or self._compact_size()
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
                win.resize(*NORMAL_SIZE)
                if self._pre_compact_pos:
                    _move_after_resize(win, self._pre_compact_pos)
                    self._pre_compact_pos = None

        if win:
            self._window_op(op)
        if was_auto and self._overlay:
            self._overlay.notify_manual_exit()
        return {"ok": True, "compact": value}

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
            size = self._overlay_size_for(rect)
            self._overlay_size = size
            self.set_compact(True, dock=self._dock_for(rect, size), from_user=False, size=size)
            self._notify_ui_compact(True)

        def follow(rect):
            win = self._window
            if not win:
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
            if tracker and self._own_hwnd:
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

    def _compact_size(self) -> tuple[int, int]:
        """Tamanho do overlay compacto, do jeito que o usuário ajustou nas
        Configurações, preso a limites sensatos."""
        with self._lock:
            w = int(self.settings.get("compact_width") or COMPACT_SIZE[0])
            h = int(self.settings.get("compact_height") or COMPACT_SIZE[1])
        w = max(COMPACT_MIN[0], min(COMPACT_MAX[0], w))
        h = max(COMPACT_MIN[1], min(COMPACT_MAX[1], h))
        return w, h

    def _overlay_size_for(self, rect=None) -> tuple[int, int]:
        """Tamanho do overlay ao grudar: proporcional à janela do emulador quando
        'ajustar ao emulador' está ligado (preso a COMPACT_MIN/MAX); senão, o
        tamanho manual das Configurações."""
        with self._lock:
            fit = self.settings.get("overlay_fit_emulator", True)
        if not (fit and rect):
            return self._compact_size()
        _ex, _ey, ew, eh = rect
        w = max(COMPACT_MIN[0], min(COMPACT_MAX[0], round(ew * OVERLAY_FIT_W)))
        h = max(COMPACT_MIN[1], min(COMPACT_MAX[1], round(eh * OVERLAY_FIT_H)))
        return w, h

    def _dock_for(self, rect, size=None):
        return emulator_tracker.dock_position(
            rect, size or self._compact_size(), COMPACT_MARGIN)

    def get_compact_config(self) -> dict:
        """Tamanho e contagens do overlay compacto para a UI."""
        w, h = self._compact_size()
        with self._lock:
            last = int(self.settings.get("compact_last", 2))
            nxt = int(self.settings.get("compact_next", 0))
        return {"ok": True, "width": w, "height": h,
                "last": max(0, last), "next": max(0, nxt)}

    def set_compact_config(self, width=None, height=None, last=None, next=None) -> dict:
        """Salva o tamanho/contagens do compacto. Se já estiver em modo compacto,
        redimensiona a janela na hora."""
        with self._lock:
            if width is not None:
                self.settings["compact_width"] = int(width)
            if height is not None:
                self.settings["compact_height"] = int(height)
            if last is not None:
                self.settings["compact_last"] = max(0, int(last))
            if next is not None:
                self.settings["compact_next"] = max(0, int(next))
            save_settings(self.settings)
        win = self._window
        if win and self._compact and (width is not None or height is not None):
            w, h = self._compact_size()
            self._window_op(lambda: win.resize(w, h))
        return self.get_compact_config()

    def _notify_ui_compact(self, compact: bool):
        """O backend mudou o modo sozinho — o JS precisa saber para re-renderizar
        (o polling de 5s do dashboard só perceberia depois)."""
        win = self._window
        if not win:
            return
        js = f"window.onOverlayChanged && window.onOverlayChanged({str(bool(compact)).lower()})"
        try:
            win.evaluate_js(js)
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
        }
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

        while True:
            time.sleep(OVERLAY_INTERVAL)
            try:
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

    # -------------------- cálculo de progresso por jogo --------------------- #
    def _sync_game(self, game: dict) -> bool:
        """Consulta a API e recalcula o estado de um jogo. Devolve False se a API
        nos limitou — o laço de sincronização usa isso para recuar."""
        slug = game["slug"]

        earned_map: dict[int, dict] = {}
        limited = False
        if self._client:
            try:
                progress = self._client.get_game_info_and_user_progress(
                    game["retroachievements_game_id"]
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

        self._apply_progress(game, earned_map)
        return not limited

    def _apply_progress(self, game: dict, earned_map: dict) -> dict:
        """Parte pura do cálculo: cruza o walkthrough salvo com o que a API disse
        estar destravado e publica o resultado em `self.state`. Separado de
        `_sync_game` para a importação em lote reaproveitar o progresso que já
        veio na mesma resposta, sem uma segunda chamada de rede."""
        slug = game["slug"]
        meta = game.get("achievements_meta", {})

        ordered = []
        # Os "modos" agora vêm da RetroAchievements, não de curadoria: uma
        # conquista destravada sem savestate é HARDCORE; destravada só no modo
        # casual é SOFTCORE. Os dois são exclusivos, então somam o total obtido.
        modes = {m: {"total": 0, "earned": 0} for m in DEFAULT_MODES}
        last_earned = None

        for step in sorted(game.get("walkthrough", []), key=lambda s: s.get("step", 0)):
            for entry in step.get("achievements", []):
                aid = int(entry["id"])
                m = meta.get(str(aid), {})
                live = earned_map.get(aid, {})
                earned = bool(live.get("earned"))
                hardcore = bool(live.get("hardcore"))
                date = live.get("date", "")
                badge = m.get("badge") or live.get("badge", "")

                row = {
                    "id": aid,
                    "name": m.get("title") or live.get("title") or f"#{aid}",
                    "desc": m.get("desc") or live.get("desc") or "",
                    "earned": earned,
                    "hardcore": hardcore,
                    "mode": "hardcore" if hardcore else "softcore",
                    "date": fmt_date(date),
                    "date_raw": date,
                    "badge_url": self._badge_url(slug, badge),
                    "step": step.get("step"),
                    "area": step.get("area", ""),
                }
                ordered.append(row)

                for key in modes:
                    modes[key]["total"] += 1
                if earned:
                    modes["hardcore" if hardcore else "softcore"]["earned"] += 1

                if earned and date:
                    if last_earned is None or date > last_earned["date_raw"]:
                        last_earned = row

        # Guarda várias próximas: o overlay compacto escolhe quantas mostrar
        # conforme o tamanho ajustado da janela.
        next_ids = [r["id"] for r in ordered if not r["earned"]][:12]
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

        detail = {
            "slug": slug,
            "title": game["title"],
            "platform": game["platform"],
            "icon": game.get("icon", ""),
            "art": game.get("art", {}),
            "accent": game.get("accent", ACCENTS[0]),
            "modes": modes,
            "mastery": mastery,
            "achievements": ordered,
            "next_ids": next_ids,
            "last_earned": last_earned,
            "guide": game.get("guide", []),   # seções de dicas/tutoriais do PDF
        }
        with self._lock:
            self.state[slug] = detail
        return detail

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
    port = start_static_server()
    url = f"http://127.0.0.1:{port}/ui/index.html"

    window = webview.create_window(
        "DigiTracker",
        url=url,
        js_api=api,
        width=NORMAL_SIZE[0],
        height=NORMAL_SIZE[1],
        min_size=COMPACT_MIN,    # permite encolher até o menor overlay ajustável
        frameless=True,
        easy_drag=False,      # arrasto via .pywebview-drag-region
        on_top=True,
        background_color="#08080F",
    )
    api._window = window

    threading.Thread(target=api.sync_loop, daemon=True).start()
    threading.Thread(target=api.overlay_loop, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
