"""DigiTracker — engine principal.

Abre uma janela pywebview always-on-top / sem moldura, serve a UI
(HTML/CSS/JS) por um servidor estático interno e expõe a lógica de backend
para o frontend via `js_api`. Um thread de sincronização consulta a
RetroAchievements a cada 30s e mantém o estado de cada jogo em memória.
"""

from __future__ import annotations

import json
import posixpath
import re
import sys
import threading
import time
import unicodedata
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import webview

from ra_api import RAClient, RAError, fmt_date


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
BADGES_DIR = DATA_DIR / "assets" / "badges"

SYNC_INTERVAL = 30  # segundos

ACCENTS = ["#D62839", "#F5C518", "#2DE2E6", "#27AE60"]


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


def load_game_file(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------------------- #
# API exposta ao frontend (pywebview js_api)
# ---------------------------------------------------------------------------- #
class Api:
    def __init__(self):
        self.window = None
        self.client: RAClient | None = None
        self.state: dict[str, dict] = {}      # slug -> detalhe computado
        self.pending_import: dict | None = None
        self.index_building = False
        self._lock = threading.Lock()

        for d in (GAMES_DIR, CACHE_DIR, BADGES_DIR):
            d.mkdir(parents=True, exist_ok=True)

        secrets = load_secrets()
        if secrets and secrets.get("username") and secrets.get("api_key"):
            self.client = RAClient(secrets["username"], secrets["api_key"], CACHE_DIR)

    # ------------------------ status / configuração ------------------------- #
    def get_app_state(self) -> dict:
        return {
            "configured": self.client is not None,
            "username": self.client.username if self.client else "",
            "index_ready": bool(self.client and self.client.index_is_fresh()),
            "index_building": self.index_building,
        }

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
        self.client = client
        self._kick_sync()
        self._ensure_index_async()
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
                    "accent": g["accent"],
                    "modes": g["modes"],
                }
            )
        summaries.sort(key=lambda s: s["title"].lower())
        return summaries

    def get_game(self, slug: str) -> dict | None:
        with self._lock:
            return self.state.get(slug)

    def delete_game(self, slug: str) -> dict:
        path = GAMES_DIR / f"{slug}.json"
        if path.exists():
            path.unlink()
        with self._lock:
            self.state.pop(slug, None)
        return {"ok": True}

    # --------------------------- busca (wizard p1) -------------------------- #
    def search_games(self, query: str) -> dict:
        if not self.client:
            return {"ready": False, "building": False, "results": [], "error": "Não configurado."}
        if not self.client.index_is_fresh():
            self._ensure_index_async()
            return {"ready": False, "building": True, "results": []}
        return {"ready": True, "building": False, "results": self.client.search_games(query)}

    def _ensure_index_async(self):
        if self.index_building or not self.client:
            return
        if self.client.index_is_fresh():
            return
        self.index_building = True

        def build():
            try:
                self.client.build_games_index()
            except RAError:
                pass
            finally:
                self.index_building = False

        threading.Thread(target=build, daemon=True).start()

    # --------------------------- importar (wizard) -------------------------- #
    def import_game(self, game_id: int) -> dict:
        """Passo 1 -> 2: baixa a lista completa de conquistas e cacheia ícones."""
        if not self.client:
            return {"ok": False, "error": "Não configurado."}
        try:
            progress = self.client.get_game_info_and_user_progress(int(game_id))
        except RAError as exc:
            return {"ok": False, "error": str(exc)}

        title = progress.get("Title", "Jogo")
        platform = progress.get("ConsoleName", "")
        slug = slugify(title)
        achievements = self.client.parse_achievements(progress)

        # baixa badges para o cache local (em ordem de display da API)
        ach_list = sorted(achievements.values(), key=lambda a: a["id"])
        for a in ach_list:
            if a["badge"]:
                self.client.download_badge(
                    a["badge"], BADGES_DIR / slug / f"{a['badge']}.png"
                )

        self.pending_import = {
            "slug": slug,
            "title": title,
            "platform": platform,
            "retroachievements_game_id": int(game_id),
            "achievements_meta": {
                str(a["id"]): {"title": a["title"], "desc": a["desc"], "badge": a["badge"]}
                for a in ach_list
            },
        }
        return {
            "ok": True,
            "slug": slug,
            "title": title,
            "platform": platform,
            "achievements": [
                {
                    "id": a["id"],
                    "title": a["title"],
                    "desc": a["desc"],
                    "badge_url": self._badge_url(slug, a["badge"]),
                    "mode": "normal",
                }
                for a in ach_list
            ],
        }

    def save_game(self, payload: dict) -> dict:
        """Passo 2 final: grava config/games/{slug}.json com o walkthrough."""
        if not self.pending_import:
            return {"ok": False, "error": "Nenhuma importação em andamento."}
        slug = self.pending_import["slug"]
        steps = payload.get("walkthrough", [])
        if not steps:
            return {"ok": False, "error": "Adicione ao menos uma etapa com conquistas."}

        # conta jogos existentes para escolher um accent
        accent = ACCENTS[len(list(GAMES_DIR.glob("*.json"))) % len(ACCENTS)]

        game = {
            "slug": slug,
            "title": self.pending_import["title"],
            "platform": self.pending_import["platform"],
            "accent": payload.get("accent", accent),
            "retroachievements_game_id": self.pending_import["retroachievements_game_id"],
            "walkthrough": steps,
            "achievements_meta": self.pending_import["achievements_meta"],
        }
        (GAMES_DIR / f"{slug}.json").write_text(
            json.dumps(game, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.pending_import = None
        self._sync_game(game)  # popula o estado imediatamente
        return {"ok": True, "slug": slug}

    # ------------------------- controles da janela -------------------------- #
    def minimize(self):
        if self.window:
            self.window.minimize()

    def close(self):
        if self.window:
            self.window.destroy()

    def toggle_on_top(self, value: bool) -> dict:
        if self.window:
            self.window.on_top = bool(value)
        return {"ok": True, "on_top": bool(value)}

    # ----------------------------- helpers ---------------------------------- #
    @staticmethod
    def _badge_url(slug: str, badge: str) -> str:
        if not badge:
            return ""
        return f"/assets/badges/{slug}/{badge}.png"

    # -------------------- cálculo de progresso por jogo --------------------- #
    def _sync_game(self, game: dict):
        slug = game["slug"]
        meta = game.get("achievements_meta", {})

        earned_map: dict[int, dict] = {}
        if self.client:
            try:
                progress = self.client.get_game_info_and_user_progress(
                    game["retroachievements_game_id"]
                )
                earned_map = self.client.parse_achievements(progress)
                # garante badges em cache (jogo pode ter sido editado)
                for a in earned_map.values():
                    if a["badge"]:
                        self.client.download_badge(
                            a["badge"], BADGES_DIR / slug / f"{a['badge']}.png"
                        )
            except RAError:
                earned_map = {}

        ordered = []
        modes = {"normal": {"total": 0, "earned": 0}, "hard": {"total": 0, "earned": 0}}
        last_earned = None

        for step in sorted(game.get("walkthrough", []), key=lambda s: s.get("step", 0)):
            for entry in step.get("achievements", []):
                aid = int(entry["id"])
                mode = entry.get("mode", "normal")
                m = meta.get(str(aid), {})
                live = earned_map.get(aid, {})
                earned = bool(live.get("earned"))
                date = live.get("date", "")
                badge = m.get("badge") or live.get("badge", "")

                row = {
                    "id": aid,
                    "name": m.get("title") or live.get("title") or f"#{aid}",
                    "desc": m.get("desc") or live.get("desc") or "",
                    "mode": mode,
                    "earned": earned,
                    "date": fmt_date(date),
                    "date_raw": date,
                    "badge_url": self._badge_url(slug, badge),
                    "step": step.get("step"),
                    "area": step.get("area", ""),
                }
                ordered.append(row)

                if mode in modes:
                    modes[mode]["total"] += 1
                    if earned:
                        modes[mode]["earned"] += 1

                if earned and date:
                    if last_earned is None or date > last_earned["date_raw"]:
                        last_earned = row

        next_ids = [r["id"] for r in ordered if not r["earned"]][:3]

        detail = {
            "slug": slug,
            "title": game["title"],
            "platform": game["platform"],
            "accent": game.get("accent", ACCENTS[0]),
            "modes": modes,
            "achievements": ordered,
            "next_ids": next_ids,
            "last_earned": last_earned,
        }
        with self._lock:
            self.state[slug] = detail

    def _kick_sync(self):
        """Recarrega todos os jogos do disco e recomputa o estado uma vez."""
        for path in GAMES_DIR.glob("*.json"):
            game = load_game_file(path)
            if game and game.get("slug"):
                self._sync_game(game)

    def sync_loop(self):
        # primeira passada imediata
        self._kick_sync()
        while True:
            time.sleep(SYNC_INTERVAL)
            self._kick_sync()


# ---------------------------------------------------------------------------- #
# Servidor estático interno (serve a UI + badges via http://127.0.0.1)
# ---------------------------------------------------------------------------- #
class _AssetHandler(SimpleHTTPRequestHandler):
    """Serve `ui/` a partir do bundle (read-only) e `assets/` a partir da pasta
    de dados gravável — necessário porque, empacotado, esses caminhos diferem."""

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0].split("#", 1)[0]
        rel = posixpath.normpath(unquote(path)).lstrip("/")
        base = DATA_DIR if rel.startswith("assets/") else BUNDLE_DIR
        return str(base / rel)

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
    api = Api()
    port = start_static_server()
    url = f"http://127.0.0.1:{port}/ui/index.html"

    window = webview.create_window(
        "DigiTracker",
        url=url,
        js_api=api,
        width=1040,
        height=680,
        min_size=(820, 560),
        frameless=True,
        easy_drag=False,      # arrasto via .pywebview-drag-region
        on_top=True,
        background_color="#08080F",
    )
    api.window = window

    threading.Thread(target=api.sync_loop, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
