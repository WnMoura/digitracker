"""Opt-in LAN companion. Deliberately does NOT expose the pywebview bridge."""
from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import secrets
import socket
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from waitress import create_server


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


class CompanionServer:
    def __init__(self, ui_root: Path, assets: Path, snapshot, command):
        self.ui_root, self.assets = Path(ui_root), Path(assets).resolve()
        self.snapshot, self.command = snapshot, command
        self.server = None
        self.host, self.port = "127.0.0.1", 0
        self.pending, self.sessions = {}, {}
        self.pair_code, self.pair_expires = "", 0
        self.limits = defaultdict(deque)
        self.lock = threading.RLock()
        self.app = Flask(__name__, static_folder=None)
        self.app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
        self._routes()

    def _routes(self):
        app = self.app

        @app.before_request
        def guard():
            expected = f"{self.host}:{self.port}"
            if request.host != expected:
                return jsonify(ok=False, error="Host não autorizado."), 403
            if request.method != "GET":
                if request.headers.get("Origin") != f"http://{expected}" or not request.is_json:
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
                    if not session or session["expires"] < now:
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
                self.pending[key] = {"id": key, "name": str(body.get("name") or "Navegador móvel")[:80],
                                     "expires": time.time() + 120, "approved": False}
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
                token = secrets.token_urlsafe(32)
                self.sessions[_digest(token)] = {"id": _digest(token), "name": item["name"],
                                                "expires": time.time() + 86400, "seen_at": time.time()}
                self.pending.pop(key)
            response = jsonify(ok=True, pending=False)
            response.set_cookie("dt_session", token, httponly=True, samesite="Strict", max_age=86400)
            response.delete_cookie("dt_pair")
            return response

        @app.get("/api/state")
        def state():
            return jsonify(self.snapshot(request.args.get("slug", "")))

        # Read-only projections for the detailed mobile experience.  The
        # callback intentionally returns the already-sanitised companion
        # snapshot: no route ever reads the guide/source files directly or
        # exposes raw HTML, private notes, drafts or session credentials.
        def public_state():
            value = self.snapshot(request.args.get("slug", ""))
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

        @app.get("/assets/<path:relative>")
        def media(relative):
            path = (self.assets / relative).resolve()
            if not path.is_relative_to(self.assets) or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"} or not path.is_file():
                return jsonify(ok=False, error="Imagem não encontrada."), 404
            return send_file(path)

    def start(self, host):
        if self.server:
            return self.status()
        address = ipaddress.ip_address(host)
        if host not in local_addresses() or not address.is_private or address.is_loopback:
            raise ValueError("Escolha um endereço da rede local deste PC.")
        self.host = host
        self.server = create_server(self.app, host=host, port=0, threads=6,
                                    clear_untrusted_proxy_headers=True, max_request_body_size=65536)
        self.port = self.server.effective_port
        threading.Thread(target=self.server.run, daemon=True).start()
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
                  "pending": [], "devices": []}
        with self.lock:
            result["pending"] = [dict(v) for v in self.pending.values() if v["expires"] > time.time()]
            result["devices"] = [dict(v) for v in self.sessions.values() if v["expires"] > time.time()]
            if include_qr and self.pair_code:
                import qrcode
                stream = io.BytesIO()
                qrcode.make(result["url"] + "/#pair=" + self.pair_code).save(stream, format="PNG")
                result["qr"] = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
        return result

    def approve(self, request_id, approved=True):
        with self.lock:
            item = self.pending.get(request_id)
            if not item or item["expires"] < time.time():
                raise ValueError("Pedido expirado.")
            if approved:
                item["approved"] = True
            else:
                self.pending.pop(request_id)
        return self.status()

    def revoke(self, device_id):
        with self.lock:
            self.sessions.pop(device_id, None)
        return self.status()

    def stop(self):
        if self.server:
            self.server.close()
            self.server = None
        with self.lock:
            self.pending.clear()
            self.sessions.clear()
            self.pair_code = ""
        return {"ok": True, "running": False}
