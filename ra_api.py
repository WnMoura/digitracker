"""Camada de acesso à API REST da RetroAchievements.

Não existe biblioteca oficial em Python, então falamos direto com a API REST
via `requests`. Autenticação: a Web API Key vai no parâmetro de query `y`; o
usuário alvo dos endpoints de progresso vai em `u`.

Docs: https://api-docs.retroachievements.org/
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import requests

API_BASE = "https://retroachievements.org/API/"
MEDIA_BASE = "https://media.retroachievements.org"

# Tempo de validade do índice de jogos (usado na busca do wizard).
INDEX_TTL_SECONDS = 7 * 24 * 3600


class RAError(Exception):
    """Erro de comunicação ou autenticação com a RetroAchievements."""


class RAClient:
    def __init__(self, username: str, api_key: str, cache_dir: Path):
        self.username = username.strip()
        self.api_key = api_key.strip()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "DigiTracker/1.0"})

    # ------------------------------------------------------------------ #
    # Núcleo HTTP
    # ------------------------------------------------------------------ #
    def _get(self, php: str, params: dict, timeout: int = 20) -> dict | list:
        q = {"y": self.api_key, **params}
        url = API_BASE + php
        try:
            resp = self.session.get(url, params=q, timeout=timeout)
        except requests.RequestException as exc:
            raise RAError(f"Falha de rede ao chamar {php}: {exc}") from exc

        if resp.status_code == 401:
            raise RAError("Não autorizado (401) — verifique username e Web API Key.")
        if resp.status_code != 200:
            raise RAError(f"{php} retornou HTTP {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise RAError(f"{php} não retornou JSON válido.") from exc

    # ------------------------------------------------------------------ #
    # Validação de credenciais
    # ------------------------------------------------------------------ #
    def validate(self) -> bool:
        """Faz uma chamada barata e autenticada para validar as credenciais."""
        data = self._get("API_GetConsoleIDs.php", {})
        if isinstance(data, list):
            return True
        raise RAError("Resposta inesperada ao validar credenciais.")

    # ------------------------------------------------------------------ #
    # Consoles / sistemas
    # ------------------------------------------------------------------ #
    def get_console_ids(self, only_game_systems: bool = True) -> list[dict]:
        params = {}
        if only_game_systems:
            # g=1 -> só sistemas de jogos (exclui hubs/eventos); a=1 -> só ativos
            params.update({"g": 1, "a": 1})
        data = self._get("API_GetConsoleIDs.php", params)
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ #
    # Lista de jogos por console + índice de busca
    # ------------------------------------------------------------------ #
    def get_game_list(self, system_id: int, only_with_achievements: bool = True) -> list[dict]:
        params = {"i": system_id}
        if only_with_achievements:
            params["f"] = 1
        data = self._get("API_GetGameList.php", params)
        return data if isinstance(data, list) else []

    @property
    def _index_path(self) -> Path:
        return self.cache_dir / "games_index.json"

    def index_is_fresh(self) -> bool:
        p = self._index_path
        if not p.exists():
            return False
        return (time.time() - p.stat().st_mtime) < INDEX_TTL_SECONDS

    def build_games_index(self, progress_cb: Optional[Callable[[int, int, str], None]] = None) -> int:
        """Monta o índice local de jogos (id/título/console/contagem).

        A API não tem busca por nome, então agregamos a lista de jogos *com
        conquistas* de cada console e cacheamos. Operação cara — feita uma vez
        e revalidada conforme INDEX_TTL_SECONDS.
        """
        consoles = self.get_console_ids(only_game_systems=True)
        total = len(consoles)
        index: list[dict] = []
        for i, console in enumerate(consoles, start=1):
            cid = console.get("ID") or console.get("id")
            cname = console.get("Name") or console.get("name") or ""
            if progress_cb:
                progress_cb(i, total, cname)
            try:
                games = self.get_game_list(int(cid), only_with_achievements=True)
            except RAError:
                continue
            for g in games:
                index.append(
                    {
                        "id": g.get("ID") or g.get("id"),
                        "title": g.get("Title") or g.get("title") or "",
                        "console_id": cid,
                        "console": cname,
                        "count": g.get("NumAchievements") or g.get("numAchievements") or 0,
                        "icon": g.get("ImageIcon") or g.get("imageIcon") or "",
                    }
                )
            time.sleep(0.3)  # gentileza com o rate limit da API

        self._index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        return len(index)

    def search_games(self, query: str, limit: int = 25) -> list[dict]:
        """Busca por substring (case-insensitive) no índice local."""
        if not self._index_path.exists():
            return []
        try:
            index = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
        q = query.strip().lower()
        if not q:
            return []
        hits = [g for g in index if q in (g.get("title") or "").lower()]
        hits.sort(key=lambda g: (len(g.get("title") or ""), g.get("title") or ""))
        return hits[:limit]

    # ------------------------------------------------------------------ #
    # Conquistas + progresso do usuário
    # ------------------------------------------------------------------ #
    def get_game_info_and_user_progress(self, game_id: int, target_user: str | None = None) -> dict:
        user = (target_user or self.username).strip()
        data = self._get(
            "API_GetGameInfoAndUserProgress.php",
            {"g": game_id, "u": user, "a": 1},
        )
        if not isinstance(data, dict):
            raise RAError("Resposta inesperada de GetGameInfoAndUserProgress.")
        return data

    @staticmethod
    def parse_achievements(progress: dict) -> dict[int, dict]:
        """Normaliza o dicionário Achievements da resposta da API.

        Retorna {achievement_id: {title, desc, badge, points, earned, date}}.
        Uma conquista é considerada destravada se tiver DateEarned ou
        DateEarnedHardcore.
        """
        out: dict[int, dict] = {}
        ach = progress.get("Achievements") or {}
        for raw_id, a in ach.items():
            try:
                aid = int(a.get("ID") or raw_id)
            except (TypeError, ValueError):
                continue
            date_hc = a.get("DateEarnedHardcore")
            date_sc = a.get("DateEarned")
            earned_date = date_hc or date_sc
            try:
                display_order = int(a.get("DisplayOrder"))
            except (TypeError, ValueError):
                display_order = 10_000_000   # sem ordem definida -> vai para o fim
            out[aid] = {
                "id": aid,
                "title": a.get("Title") or "",
                "desc": a.get("Description") or "",
                "badge": a.get("BadgeName") or "",
                "points": a.get("Points") or 0,
                "display_order": display_order,   # ordem canônica/lógica do set no RA
                "earned": bool(earned_date),
                "hardcore": bool(date_hc),
                "date": earned_date or "",
            }
        return out

    # ------------------------------------------------------------------ #
    # Cache de badges
    # ------------------------------------------------------------------ #
    def download_badge(self, badge_name: str, dest_path: Path) -> bool:
        """Baixa o ícone da conquista uma única vez (cache local)."""
        if not badge_name:
            return False
        dest_path = Path(dest_path)
        if dest_path.exists() and dest_path.stat().st_size > 0:
            return True
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"{MEDIA_BASE}/Badge/{badge_name}.png"
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code == 200 and resp.content:
                dest_path.write_bytes(resp.content)
                return True
        except requests.RequestException:
            return False
        return False

    def download_image(self, image_path: str, dest_path: Path) -> bool:
        """Baixa uma imagem arbitrária do RA (ex.: ícone do jogo `ImageIcon`),
        cacheando localmente. `image_path` costuma vir como '/Images/123.png'."""
        if not image_path:
            return False
        dest_path = Path(dest_path)
        if dest_path.exists() and dest_path.stat().st_size > 0:
            return True
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        url = image_path if image_path.startswith("http") else MEDIA_BASE + image_path
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code == 200 and resp.content:
                dest_path.write_bytes(resp.content)
                return True
        except requests.RequestException:
            return False
        return False


def fmt_date(raw: str) -> str:
    """Formata '2026-03-04 22:08:11' -> '04/03/2026 · 22:08'."""
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%d/%m/%Y · %H:%M")
        except ValueError:
            continue
    return raw
