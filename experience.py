"""Account-scoped, local activity and search. No credentials are indexed."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def account_id(username):
    return hashlib.sha256(str(username or "local").strip().casefold().encode()).hexdigest()[:24]


class ExperienceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS preferences(account TEXT,key TEXT,value TEXT,
                  PRIMARY KEY(account,key));
                CREATE TABLE IF NOT EXISTS events(account TEXT,id TEXT,slug TEXT,kind TEXT,
                  at REAL,points INTEGER,mode TEXT,rarity REAL,payload TEXT,notified INTEGER,
                  PRIMARY KEY(account,id));
                CREATE INDEX IF NOT EXISTS events_time ON events(account,at);
                CREATE TABLE IF NOT EXISTS sessions(account TEXT,id TEXT,slug TEXT,started REAL,
                  elapsed REAL,resumed REAL,status TEXT,PRIMARY KEY(account,id));
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
                  account UNINDEXED,slug UNINDEXED,kind UNINDEXED,target UNINDEXED,
                  title,body,tokenize='unicode61 remove_diacritics 2');
            """)
            db.execute("UPDATE sessions SET status='paused' WHERE status='running'")

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def get(self, account, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM preferences WHERE account=? AND key=?", (account, key)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, account, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?,?)", (account, key, json.dumps(value, ensure_ascii=False)))
        return value

    def game_preference(self, account, slug, changes=None):
        value = self.get(account, "game:" + slug, {"favorite": False, "status": "playing", "opened_at": 0})
        if changes is not None:
            if "status" in changes:
                if changes["status"] not in {"want", "playing", "paused"}:
                    raise ValueError("Estado de jogo inválido.")
                value["status"] = changes["status"]
            if "favorite" in changes:
                value["favorite"] = bool(changes["favorite"])
            if changes.get("opened"):
                value["opened_at"] = time.time()
            self.put(account, "game:" + slug, value)
        return value

    def index_game(self, account, game, bundle):
        slug = game["slug"]
        records = [(account, slug, "game", "", game["title"], game.get("platform", ""))]
        for row in game.get("achievements") or []:
            records.append((account, slug, "achievement", str(row["id"]), row.get("name", ""), row.get("desc", "")))
        for chapter in (bundle.get("current") or {}).get("chapters") or []:
            for block in chapter.get("blocks") or []:
                if block.get("type") == "spoiler":
                    continue
                records.append((account, slug, "guide", block["id"], block.get("title") or chapter["title"],
                                block.get("text", "") + " " + " ".join(i.get("text", "") for i in block.get("items") or [])))
        for block_id, note in (bundle.get("progress") or {}).get("notes", {}).items():
            records.append((account, slug, "note", block_id, "Anotação · " + game["title"], str(note)))
        digest = hashlib.sha256(json.dumps(records, ensure_ascii=False).encode()).hexdigest()
        if self.get(account, "index:" + slug) == digest:
            return
        with self.db() as db:
            db.execute("DELETE FROM search WHERE account=? AND slug=?", (account, slug))
            db.executemany("INSERT INTO search VALUES(?,?,?,?,?,?)", records)
            db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?,?)", (account, "index:" + slug, json.dumps(digest)))

    def search(self, account, query, streamer=False):
        words = str(query).strip().split()[:12]
        if not words:
            return []
        expression = " AND ".join('"' + word.replace('"', '""') + '"*' for word in words)
        with self.db() as db:
            rows = db.execute("""SELECT slug,kind,target,title,substr(body,1,220) AS snippet
                FROM search WHERE search MATCH ? AND account=? AND (?=0 OR kind<>'note')
                ORDER BY rank LIMIT 50""", (expression, account, int(streamer))).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def timestamp(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
        except (ValueError, TypeError):
            return time.time()

    def ingest(self, account, game):
        slug = game["slug"]
        baseline = self.get(account, "baseline:" + slug, False)
        new = []
        with self.db() as db:
            for row in game.get("achievements") or []:
                if not row.get("earned"):
                    continue
                modes = [("hardcore", row.get("date_hardcore_raw") or row.get("date_raw"))] if row.get("hardcore") else [("softcore", row.get("date_softcore_raw") or row.get("date_raw"))]
                for mode, date in modes:
                    event_id = f"{slug}:{row['id']}:{mode}"
                    at = self.timestamp(date)
                    payload = {"id": event_id, "slug": slug, "game_title": game["title"],
                               "achievement_id": row["id"], "name": row.get("name", ""),
                               "badge_url": row.get("badge_url", ""), "mode": mode,
                               "points": row.get("points", 0), "rarity": row.get("rarity_hardcore") if mode == "hardcore" else row.get("rarity"), "at": at}
                    result = db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (account, event_id, slug, "achievement", at, payload["points"], mode,
                         payload["rarity"], json.dumps(payload, ensure_ascii=False), 0 if baseline else 1))
                    if result.rowcount and baseline:
                        new.append(payload)
            db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?,?)", (account, "baseline:" + slug, "true"))
        return new

    def events(self, account, pending=False, limit=200):
        with self.db() as db:
            rows = db.execute("SELECT payload FROM events WHERE account=? AND (?=0 OR notified=0) ORDER BY at DESC LIMIT ?",
                              (account, int(pending), max(1, min(1000, limit)))).fetchall()
            return [json.loads(row[0]) for row in rows]

    def acknowledge(self, account, event_id):
        with self.db() as db:
            db.execute("UPDATE events SET notified=1 WHERE account=? AND id=?", (account, event_id))

    def session(self, account, action="get", slug=""):
        now = time.time()
        with self.db() as db:
            row = db.execute("SELECT * FROM sessions WHERE account=? AND status<>'ended' ORDER BY started DESC LIMIT 1", (account,)).fetchone()
            value = dict(row) if row else None
            if action == "start" and not value:
                if not slug:
                    raise ValueError("Escolha um jogo para iniciar a sessão.")
                value = {"account": account, "id": uuid.uuid4().hex, "slug": slug, "started": now, "elapsed": 0, "resumed": now, "status": "running"}
                db.execute("INSERT INTO sessions VALUES(:account,:id,:slug,:started,:elapsed,:resumed,:status)", value)
            elif action in {"pause", "end", "resume"} and value:
                if value["status"] == "running":
                    value["elapsed"] += now - value["resumed"]
                value["status"] = {"pause": "paused", "end": "ended", "resume": "running"}[action]
                value["resumed"] = now
                db.execute("UPDATE sessions SET elapsed=:elapsed,resumed=:resumed,status=:status WHERE account=:account AND id=:id", value)
            elif action not in {"get", "start", "pause", "end", "resume"}:
                raise ValueError("Ação de sessão inválida.")
            if action == "get" and value and value["status"] == "running":
                value["elapsed"] += now - value["resumed"]
                value["resumed"] = now
                db.execute("UPDATE sessions SET elapsed=:elapsed,resumed=:resumed WHERE account=:account AND id=:id", value)
            if value:
                value["seconds"] = int(value["elapsed"] + (now - value["resumed"] if value["status"] == "running" else 0))
                value.pop("account", None)
            return value

    def summary(self, account):
        now = datetime.now().astimezone()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.db() as db:
            points = db.execute("SELECT coalesce(sum(points),0) FROM events WHERE account=? AND mode='hardcore' AND at>=?", (account, start)).fetchone()[0]
            rows = db.execute("SELECT started,elapsed,resumed,status FROM sessions WHERE account=?", (account,)).fetchall()
        days = {datetime.fromtimestamp(r["started"]).date() for r in rows
                if r["elapsed"] + (time.time() - r["resumed"] if r["status"] == "running" else 0) >= 60}
        day = now.date()
        if day not in days:
            day -= timedelta(days=1)
        streak = 0
        while day in days:
            streak += 1
            day -= timedelta(days=1)
        session = self.session(account)
        candidates = [e for e in self.events(account, limit=1000) if session and e["at"] >= session["started"] and e.get("rarity") is not None]
        return {"month_points": points, "streak": streak, "session": session,
                "rarest": min(candidates, key=lambda e: e["rarity"]) if candidates else None,
                "goals": self.get(account, "goals", {}), "settings": self.get(account, "settings", {})}
