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
