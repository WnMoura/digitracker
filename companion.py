"""Opt-in LAN companion. Deliberately does NOT expose the pywebview bridge."""
from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import secrets
import socket
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from waitress import create_server

try:  # Optional at runtime; the LAN server remains usable when discovery is unavailable.
    from zeroconf import ServiceInfo, Zeroconf
except ImportError:  # pragma: no cover - exercised by the packaged dependency.
    ServiceInfo = Zeroconf = None


def local_addresses():
    values = set()
    try:
        interfaces = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        interfaces = []
    for item in interfaces:
        address = ipaddress.ip_address(item[4][0])
        if address.is_private and not address.is_loopback and not address.is_link_local:
            values.add(str(address))
    return sorted(values)


def _digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


class DeviceRegistry:
    """Durable records for remembered companion devices.

    Browser cookies hold the random tokens.  The PC keeps only token hashes, so
    a copied local database cannot be used to create a browser session.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS companion_devices (
                    device_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    seen_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_companion_device_token ON companion_devices(token_hash)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS companion_meta (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

    def _connection(self):
        return sqlite3.connect(self.path, timeout=5)

    def installation_id(self):
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM companion_meta WHERE name = 'installation_id'").fetchone()
            if row:
                return row[0]
            value = secrets.token_urlsafe(12)
            conn.execute("INSERT INTO companion_meta(name, value) VALUES ('installation_id', ?)", (value,))
            return value

    def remember(self, account_id, name, token, now=None):
        now = time.time() if now is None else now
        device_id = secrets.token_urlsafe(18)
        item = {
            "id": device_id, "account_id": str(account_id), "name": str(name)[:80] or "Navegador móvel",
            "created_at": now, "seen_at": now, "expires_at": now + 90 * 86400, "revoked_at": None,
        }
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO companion_devices
                    (device_id, account_id, token_hash, name, created_at, seen_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """, (device_id, item["account_id"], _digest(token), item["name"], now, now, item["expires_at"]))
        return item

    def restore(self, token, account_id, now=None):
        now = time.time() if now is None else now
        with self._connection() as conn:
            row = conn.execute("""
                SELECT device_id, account_id, name, created_at, seen_at, expires_at, revoked_at
                FROM companion_devices WHERE token_hash = ?
            """, (_digest(token),)).fetchone()
            if not row:
                return None
            item = dict(zip(("id", "account_id", "name", "created_at", "seen_at", "expires_at", "revoked_at"), row))
            if item["account_id"] != str(account_id) or item["revoked_at"] is not None or item["expires_at"] < now:
                return None
            expires_at = now + 90 * 86400
            conn.execute("UPDATE companion_devices SET seen_at = ?, expires_at = ? WHERE device_id = ?",
                         (now, expires_at, item["id"]))
            item.update({"seen_at": now, "expires_at": expires_at})
            return item

    def devices(self, account_id, now=None):
        now = time.time() if now is None else now
        with self._connection() as conn:
            rows = conn.execute("""
                SELECT device_id, name, created_at, seen_at, expires_at, revoked_at
                FROM companion_devices
                WHERE account_id = ? AND revoked_at IS NULL AND expires_at >= ?
                ORDER BY seen_at DESC
            """, (str(account_id), now)).fetchall()
        return [dict(zip(("id", "name", "created_at", "seen_at", "expires_at", "revoked_at"), row)) for row in rows]

    def rename(self, device_id, account_id, name):
        value = str(name or "").strip()[:80]
        if not value:
            raise ValueError("Informe um nome para o aparelho.")
        with self._connection() as conn:
            changed = conn.execute("""
                UPDATE companion_devices SET name = ?
                WHERE device_id = ? AND account_id = ? AND revoked_at IS NULL
            """, (value, str(device_id), str(account_id))).rowcount
        if not changed:
            raise ValueError("Aparelho não encontrado.")
        return value

    def revoke(self, device_id, account_id):
        with self._connection() as conn:
            conn.execute("""
                UPDATE companion_devices SET revoked_at = ?
                WHERE device_id = ? AND account_id = ?
            """, (time.time(), str(device_id), str(account_id)))


class CompanionServer:
    def __init__(self, ui_root: Path, assets: Path, snapshot, command, *, store_path: Path | None = None,
                 account_provider=None, preferred_port: int = 47831):
        self.ui_root, self.assets = Path(ui_root), Path(assets).resolve()
        self.snapshot, self.command = snapshot, command
        self.server = None
        self.host, self.port, self.preferred_port = "127.0.0.1", 0, int(preferred_port)
        self.pending, self.sessions = {}, {}
        self.pair_code, self.pair_expires = "", 0
        self.limits = defaultdict(deque)
        self.lock = threading.RLock()
        self.account_provider = account_provider or (lambda: "local")
        self.registry = DeviceRegistry(store_path or self.assets.parent / "companion.sqlite3")
        self.installation_id = self.registry.installation_id()
        self.zeroconf = self.mdns_info = None
        self.app = Flask(__name__, static_folder=None)
        self.app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
        self._routes()

    def _account(self):
        return str(self.account_provider() or "local")[:200]

    def _allowed_hosts(self):
        values = {f"{self.host}:{self.port}"}
        if self.mdns_info:
            values.add(f"{self.mdns_name}:{self.port}")
        return values

    @property
    def mdns_name(self):
        return f"digitracker-{self.installation_id[:8]}.local"

    def _new_session(self, item):
        token = secrets.token_urlsafe(32)
        self.sessions[_digest(token)] = {
            "id": item.get("id") or _digest(token), "device_id": item.get("id", ""),
            "account_id": self._account(), "name": item.get("name") or "Navegador móvel",
            "expires": time.time() + 86400, "seen_at": time.time(),
        }
        return token

    def _start_mdns(self):
        if not Zeroconf or self.zeroconf:
            return
        try:
            info = ServiceInfo(
                "_digitracker._tcp.local.",
                f"DigiTracker-{self.installation_id[:8]}._digitracker._tcp.local.",
                addresses=[socket.inet_aton(self.host)], port=self.port,
                properties={"installation": self.installation_id[:16]},
                server=f"{self.mdns_name}.",
            )
            self.zeroconf = Zeroconf()
            self.zeroconf.register_service(info)
            self.mdns_info = info
        except Exception:
            if self.zeroconf:
                self.zeroconf.close()
            self.zeroconf = self.mdns_info = None

    def _stop_mdns(self):
        if self.zeroconf:
            try:
                if self.mdns_info:
                    self.zeroconf.unregister_service(self.mdns_info)
                self.zeroconf.close()
            except Exception:
                pass
        self.zeroconf = self.mdns_info = None

    def _routes(self):
        app = self.app

        @app.before_request
        def guard():
            if request.host not in self._allowed_hosts():
                return jsonify(ok=False, error="Host não autorizado."), 403
            if request.method != "GET":
                if request.headers.get("Origin") != f"http://{request.host}" or not request.is_json:
                    return jsonify(ok=False, error="Origem não autorizada."), 403
            now = time.time()
            key = (request.remote_addr, "pair" if request.path.startswith("/pair") else "api")
            with self.lock:
                queue = self.limits[key]
                while queue and queue[0] < now - 60:
                    queue.popleft()
                if len(queue) >= (90 if key[1] == "pair" else 300):
                    return jsonify(ok=False, error="Aguarde antes de tentar novamente."), 429
                queue.append(now)
            if request.path.startswith(("/api/", "/assets/")):
                token = _digest(request.cookies.get("dt_session", ""))
                with self.lock:
                    session = self.sessions.get(token)
                    if not session or session["expires"] < now or session.get("account_id") != self._account():
                        return jsonify(ok=False, error="Conexão encerrada. Faça o pareamento novamente."), 401
                    session["seen_at"] = now

        @app.after_request
        def headers(response):
            response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"})
            return response

        @app.get("/")
        def index():
            return send_from_directory(self.ui_root, "index.html")

        @app.get("/app.js")
        def script():
            return send_from_directory(self.ui_root, "app.js")

        @app.get("/style.css")
        def style():
            return send_from_directory(self.ui_root, "style.css")

        @app.route("/<path:relative>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        def companion_static(relative):
            if request.method != "GET" or relative not in {"app.js", "state.js", "storage.js"}:
                return jsonify(ok=False, error="Arquivo não encontrado."), 404
            return send_from_directory(self.ui_root, relative)

        @app.post("/pair")
        def pair():
            body = request.get_json()
            if not isinstance(body, dict):
                return jsonify(ok=False, error="Pedido inválido."), 400
            with self.lock:
                if not self.pair_code or self.pair_expires < time.time() or not secrets.compare_digest(str(body.get("code", "")), self.pair_code):
                    return jsonify(ok=False, error="QR expirado. Gere outro no PC."), 403
                self.pair_code = ""  # one use
                token = secrets.token_urlsafe(32)
                key = _digest(token)
                self.pending[key] = {
                    "id": key, "name": str(body.get("name") or "Navegador móvel")[:80],
                    "expires": time.time() + 120, "approved": False,
                    "remember": body.get("remember") is not False,
                }
            response = jsonify(ok=True, pending=True)
            response.set_cookie("dt_pair", token, httponly=True, samesite="Strict", max_age=120)
            return response

        @app.post("/pair/status")
        def pair_status():
            key = _digest(request.cookies.get("dt_pair", ""))
            with self.lock:
                item = self.pending.get(key)
                if not item or item["expires"] < time.time():
                    return jsonify(ok=False, error="Pareamento expirado ou recusado."), 403
                if not item["approved"]:
                    return jsonify(ok=True, pending=True)
                device_token = ""
                device = {"id": "", "name": item["name"]}
                if item.get("remember", True):
                    device_token = secrets.token_urlsafe(32)
                    device = self.registry.remember(self._account(), item["name"], device_token)
                token = self._new_session(device)
                self.pending.pop(key)
            response = jsonify(ok=True, pending=False)
            response.set_cookie("dt_session", token, httponly=True, samesite="Strict", max_age=86400)
            if device_token:
                response.set_cookie("dt_device", device_token, httponly=True, samesite="Strict", max_age=90 * 86400)
            response.delete_cookie("dt_pair")
            return response

        @app.post("/session/restore")
        def restore_session():
            device_token = request.cookies.get("dt_device", "")
            item = self.registry.restore(device_token, self._account()) if device_token else None
            if not item:
                response = jsonify(ok=False, needs_pairing=True, error="Aparelho não autorizado. Leia um novo QR Code.")
                response.delete_cookie("dt_session")
                response.delete_cookie("dt_device")
                return response, 401
            with self.lock:
                token = self._new_session(item)
            response = jsonify(ok=True, restored=True, device={"id": item["id"], "name": item["name"]})
            response.set_cookie("dt_session", token, httponly=True, samesite="Strict", max_age=86400)
            return response

        def snapshot_for_request(slug=""):
            value = self.snapshot(slug)
            if not isinstance(value, dict):
                return value
            value = dict(value)
            value["companion"] = {
                "installation_id": self.installation_id,
                "account_scope": _digest(self._account())[:16],
                "server_url": f"http://{request.host}",
            }
            return value

        @app.get("/api/state")
        def state():
            return jsonify(snapshot_for_request(request.args.get("slug", "")))

        # Read-only projections for the detailed mobile experience.  The
        # callback intentionally returns the already-sanitised companion
        # snapshot: no route ever reads the guide/source files directly or
        # exposes raw HTML, private notes, drafts or session credentials.
        def public_state():
            value = snapshot_for_request(request.args.get("slug", ""))
            if not isinstance(value, dict) or not value.get("ok"):
                return None, (jsonify(value if isinstance(value, dict) else
                                      {"ok": False, "error": "Estado indisponível."}), 404)
            return value, None

        def bounded_int(name, default, minimum, maximum):
            try:
                return max(minimum, min(maximum, int(request.args.get(name, default))))
            except (TypeError, ValueError):
                return default

        @app.get("/api/games")
        def games():
            value, error = public_state()
            if error:
                return error
            # A companion session is scoped to the PC's active library.  A
            # slug query may select another permitted game for consultation,
            # but it never changes the compact game's active session.
            return jsonify(ok=True, games=[value.get("game") or {}],
                           active_slug=(value.get("game") or {}).get("slug", ""))

        @app.get("/api/guide")
        def guide():
            value, error = public_state()
            if error:
                return error
            chapters = value.get("chapters") or []
            return jsonify(ok=True, chapters=chapters,
                           progress=value.get("progress") or {},
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))

        @app.get("/api/guide/chapter")
        def guide_chapter():
            value, error = public_state()
            if error:
                return error
            chapters = value.get("chapters") or []
            requested = str(request.args.get("chapter", "")).strip()
            chapter = None
            if requested:
                chapter = next((item for item in chapters if str(item.get("id")) == requested), None)
                if chapter is None:
                    try:
                        index = int(requested)
                    except (TypeError, ValueError):
                        index = -1
                    if 0 <= index < len(chapters):
                        chapter = chapters[index]
            if chapter is None and chapters:
                chapter = chapters[0]
            if chapter is None:
                return jsonify(ok=False, error="Capítulo não encontrado."), 404
            return jsonify(ok=True, chapter=chapter,
                           progress=value.get("progress") or {},
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))

        @app.get("/api/atlas/systems")
        def atlas_systems():
            value, error = public_state()
            if error:
                return error
            return jsonify(ok=True, systems=value.get("systems") or [],
                           system_state=value.get("system_state") or {},
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))

        @app.get("/api/atlas/nodes")
        def atlas_nodes():
            value, error = public_state()
            if error:
                return error
            system_id = str(request.args.get("system_id", "")).strip()
            nodes = []
            for system in value.get("systems") or []:
                if system_id and system.get("id") != system_id:
                    continue
                for node in system.get("nodes") or []:
                    nodes.append({**node, "system_id": system.get("id", ""),
                                  "system_title": system.get("title", "")})
            return jsonify(ok=True, nodes=nodes,
                           content_revision=value.get("content_revision", ""))

        @app.get("/api/atlas/node")
        def atlas_node():
            value, error = public_state()
            if error:
                return error
            system_id = str(request.args.get("system_id", "")).strip()
            node_id = str(request.args.get("node_id", "")).strip()
            system = next((item for item in value.get("systems") or []
                           if item.get("id") == system_id), None)
            node = next((item for item in (system or {}).get("nodes") or []
                         if item.get("id") == node_id), None)
            if not system or not node:
                return jsonify(ok=False, error="Nó do Atlas não encontrado."), 404
            incoming = [edge for edge in system.get("edges") or [] if edge.get("to") == node_id]
            outgoing = [edge for edge in system.get("edges") or [] if edge.get("from") == node_id]
            return jsonify(ok=True, system={"id": system.get("id", ""),
                                             "title": system.get("title", "")},
                           node=node, incoming=incoming, outgoing=outgoing,
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))

        @app.get("/api/atlas/graph")
        def atlas_graph():
            value, error = public_state()
            if error:
                return error
            system_id = str(request.args.get("system_id", "")).strip()
            systems = [item for item in value.get("systems") or []
                       if not system_id or item.get("id") == system_id]
            if system_id and not systems:
                return jsonify(ok=False, error="Sistema do Atlas não encontrado."), 404
            return jsonify(ok=True, systems=systems,
                           content_revision=value.get("content_revision", ""),
                           progress_revision=value.get("progress_revision", ""))

        @app.get("/api/items")
        def items():
            value, error = public_state()
            if error:
                return error
            allowed = {"all", "owned", "missing"}
            possessed = str(request.args.get("possessed", "all")).strip().lower()
            if possessed not in allowed:
                return jsonify(ok=False, error="Filtro de posse inválido."), 400
            query = str(request.args.get("q", request.args.get("query", "")))[:250].casefold()
            rows = value.get("items") or []
            if query:
                rows = [item for item in rows if query in " ".join(
                    [str(item.get("name") or ""), str(item.get("id") or ""),
                     *[str(alias) for alias in item.get("aliases") or []]]).casefold()]
            if possessed == "owned":
                rows = [item for item in rows if item.get("quantity") is not None and item.get("quantity", 0) > 0]
            elif possessed == "missing":
                rows = [item for item in rows if item.get("quantity") in (None, 0)]
            offset = bounded_int("offset", 0, 0, 100_000)
            limit = bounded_int("limit", 50, 1, 100)
            return jsonify(ok=True, items=rows[offset:offset + limit], total=len(rows),
                           offset=offset, limit=limit,
                           items_revision=value.get("items_revision", 0))

        @app.get("/api/items/detail")
        def item_detail():
            value, error = public_state()
            if error:
                return error
            item_id = str(request.args.get("item_id", "")).strip()
            item = next((row for row in value.get("items") or [] if row.get("id") == item_id), None)
            if not item:
                return jsonify(ok=False, error="Item não encontrado."), 404
            return jsonify(ok=True, item=item, items_revision=value.get("items_revision", 0))

        @app.get("/api/achievements")
        def achievements():
            value, error = public_state()
            if error:
                return error
            rows = list(value.get("achievements") or [])
            query = str(request.args.get("q", ""))[:250].casefold()
            status = str(request.args.get("status", "all")).strip().lower()
            if query:
                rows = [item for item in rows if query in " ".join(
                    [str(item.get("name") or ""), str(item.get("desc") or ""),
                     str(item.get("id") or "")]).casefold()]
            if status == "pending":
                rows = [item for item in rows if not item.get("earned")]
            elif status == "earned":
                rows = [item for item in rows if item.get("earned")]
            elif status not in {"all", "pending", "earned", "missable"}:
                return jsonify(ok=False, error="Filtro de conquista inválido."), 400
            if status == "missable":
                rows = [item for item in rows if item.get("achievement_type") == "missable"]
            offset = bounded_int("offset", 0, 0, 100_000)
            limit = bounded_int("limit", 50, 1, 100)
            return jsonify(ok=True, achievements=rows[offset:offset + limit], total=len(rows),
                           offset=offset, limit=limit,
                           content_revision=value.get("content_revision", ""))

        @app.get("/api/reference")
        def reference():
            value, error = public_state()
            if error:
                return error
            ref_id = str(request.args.get("id", request.args.get("block_id", ""))).strip()
            page = str(request.args.get("page", "")).strip()
            matches = []
            for chapter in value.get("chapters") or []:
                for block in chapter.get("blocks") or []:
                    if ref_id and ref_id not in {str(block.get("id", "")), str(block.get("element_id", ""))}:
                        continue
                    refs = block.get("source_refs") or []
                    if page and not any(str(ref.get("page", "")) == page for ref in refs if isinstance(ref, dict)):
                        continue
                    matches.append({"chapter_id": chapter.get("id", ""),
                                   "chapter_title": chapter.get("title", ""),
                                   "block": block})
            if ref_id and not matches:
                for system in value.get("systems") or []:
                    for node in system.get("nodes") or []:
                        if ref_id in {str(node.get("id", "")), str(node.get("entity_id", ""))}:
                            matches.append({"system_id": system.get("id", ""), "system_title": system.get("title", ""), "node": node})
            return jsonify(ok=True, references=matches[:100], total=len(matches),
                           content_revision=value.get("content_revision", ""))

        @app.post("/api/action")
        def action():
            body = request.get_json()
            if not isinstance(body, dict):
                return jsonify(ok=False, error="Comando inválido."), 400
            try:
                result = self.command(body)
                return jsonify(result), (409 if result.get("conflict") else 200)
            except (ValueError, KeyError, TypeError) as exc:
                return jsonify(ok=False, error=str(exc)), 400

        @app.post("/api/device/forget")
        def forget_device():
            token_key = _digest(request.cookies.get("dt_session", ""))
            with self.lock:
                session = self.sessions.pop(token_key, None)
            if session and session.get("device_id"):
                self.registry.revoke(session["device_id"], self._account())
                with self.lock:
                    for key, value in list(self.sessions.items()):
                        if value.get("device_id") == session["device_id"]:
                            self.sessions.pop(key, None)
            response = jsonify(ok=True)
            response.delete_cookie("dt_session")
            response.delete_cookie("dt_device")
            return response

        @app.get("/assets/<path:relative>")
        def media(relative):
            path = (self.assets / relative).resolve()
            if not path.is_relative_to(self.assets) or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"} or not path.is_file():
                return jsonify(ok=False, error="Imagem não encontrada."), 404
            return send_file(path)

    def start(self, host, port=None):
        if self.server:
            return self.status()
        address = ipaddress.ip_address(host)
        if host not in local_addresses() or not address.is_private or address.is_loopback:
            raise ValueError("Escolha um endereço da rede local deste PC.")
        selected_port = self.preferred_port if port in (None, "") else int(port)
        if not 1024 <= selected_port <= 65535:
            raise ValueError("Escolha uma porta entre 1024 e 65535.")
        self.host = host
        try:
            self.server = create_server(self.app, host=host, port=selected_port, threads=6,
                                        clear_untrusted_proxy_headers=True, max_request_body_size=65536)
        except OSError as exc:
            raise ValueError(f"A porta {selected_port} já está em uso. Escolha outra porta.") from exc
        self.port = self.server.effective_port
        threading.Thread(target=self.server.run, daemon=True).start()
        self._start_mdns()
        return self.new_pairing()

    def new_pairing(self):
        if not self.server:
            raise ValueError("Inicie o companion primeiro.")
        with self.lock:
            self.pair_code, self.pair_expires = secrets.token_urlsafe(32), time.time() + 120
        return self.status(include_qr=True)

    def status(self, include_qr=False):
        result = {"ok": True, "running": bool(self.server), "addresses": local_addresses(),
                  "url": f"http://{self.host}:{self.port}" if self.server else "",
                  "canonical_url": f"http://{self.mdns_name}:{self.port}" if self.server and self.mdns_info else "",
                  "port": self.port if self.server else self.preferred_port,
                  "preferred_port": self.preferred_port,
                  "mdns": bool(self.mdns_info), "pending": [], "devices": []}
        with self.lock:
            result["pending"] = [dict(v) for v in self.pending.values() if v["expires"] > time.time()]
            connected = {
                value.get("device_id"): value for value in self.sessions.values()
                if value["expires"] > time.time() and value.get("account_id") == self._account()
            }
            result["devices"] = []
            for item in self.registry.devices(self._account()):
                result["devices"].append({
                    **item, "status": "connected" if item["id"] in connected else "remembered",
                    "session_expires": connected.get(item["id"], {}).get("expires", 0),
                })
            for value in self.sessions.values():
                if value["expires"] > time.time() and not value.get("device_id") and value.get("account_id") == self._account():
                    result["devices"].append({**value, "status": "temporary"})
            if include_qr and self.pair_code:
                import qrcode
                stream = io.BytesIO()
                qrcode.make(result["url"] + "/#pair=" + self.pair_code).save(stream, format="PNG")
                result["qr"] = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
        return result

    def approve(self, request_id, approved=True, remember=True):
        with self.lock:
            item = self.pending.get(request_id)
            if not item or item["expires"] < time.time():
                raise ValueError("Pedido expirado.")
            if approved:
                item["approved"] = True
                item["remember"] = bool(remember)
            else:
                self.pending.pop(request_id)
        return self.status()

    def revoke(self, device_id):
        with self.lock:
            session = self.sessions.pop(device_id, None)
            actual_id = (session or {}).get("device_id") or str(device_id)
            for key, item in list(self.sessions.items()):
                if item.get("device_id") == actual_id:
                    self.sessions.pop(key, None)
        if actual_id:
            self.registry.revoke(actual_id, self._account())
        return self.status()

    def rename(self, device_id, name):
        value = self.registry.rename(device_id, self._account(), name)
        with self.lock:
            for item in self.sessions.values():
                if item.get("device_id") == str(device_id):
                    item["name"] = value
        return self.status()

    def stop(self):
        if self.server:
            self.server.close()
            self.server = None
        self._stop_mdns()
        with self.lock:
            self.pending.clear()
            self.sessions.clear()
            self.pair_code = ""
        return {"ok": True, "running": False}
