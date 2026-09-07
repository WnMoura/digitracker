"""Application services shared by dashboard and the restricted companion API."""
from __future__ import annotations

import copy
import json
import re
import threading
from datetime import datetime

import companion
import experience
import smart_guide


class ExperienceApi:
    def _init_experience(self, settings_path, data_dir, ui_dir):
        self._experience = experience.ExperienceStore(settings_path.parent / "experience.sqlite3")
        self._companion_service = companion.CompanionServer(
            ui_dir / "companion", data_dir / "assets", self._companion_snapshot, self._companion_command)
        self._experience_error = ""
        self._companion_ai = {}
        self._companion_ai_lock = threading.Lock()
        self._companion_generation = 0

    def _experience_account(self):
        return experience.account_id(getattr(self._client, "username", "local"))

    def _record_experience(self, game):
        if not hasattr(self, "_experience"):
            return
        try:
            account = self._experience_account()
            events = self._experience.ingest(account, game)
            notifier = getattr(self, "_notifications", None)
            if notifier:
                for event in events:
                    notifier.enqueue(event, account)
            self._experience.index_game(account, game, game.get("smart_guide") or {})
        except Exception as exc:
            self._experience_error = type(exc).__name__  # never include URLs/keys in diagnostics

    def get_journey_hub(self):
        account = self._experience_account()
        summary = self._experience.summary(account)
        fields = ("slug", "title", "platform", "art", "accent", "palette", "mastery", "score", "playtime", "completion")
        with self._lock:
            games = [{key: copy.deepcopy(game.get(key)) for key in fields} for game in self.state.values()]
        for game in games:
            game["preferences"] = self._experience.game_preference(account, game["slug"])
        games.sort(key=lambda game: (not game["preferences"].get("favorite"), -game["preferences"].get("opened_at", 0), game["title"].casefold()))
        month = datetime.now().strftime("%Y-%m")
        summary["month_masteries"] = sum(1 for g in games if (g.get("mastery") or {}).get("complete")
                                         and str((g.get("completion") or {}).get("mastery_date_raw") or "").startswith(month))
        return {"ok": True, "games": games, **summary}

    def set_game_preferences(self, slug, changes):
        if slug not in self.state or not isinstance(changes, dict):
            return {"ok": False, "error": "Jogo ou preferências inválidos."}
        try:
            value = self._experience.game_preference(self._experience_account(), slug, changes)
            return {"ok": True, "preferences": value}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def search_everywhere(self, query):
        account = self._experience_account()
        with self._lock:
            games = list(self.state.values())
        for game in games:
            self._experience.index_game(account, game, self.get_smart_guide(game["slug"]))
        streamer = self._experience.get(account, "settings", {}).get("streamer", False)
        slugs = {game["slug"] for game in games}
        return {"ok": True, "results": [r for r in self._experience.search(account, str(query)[:250], streamer) if r["slug"] in slugs]}

    def set_journey_goals(self, values):
        try:
            value = {"points": max(0, min(1000000, int(values.get("points") or 0))),
                     "masteries": max(0, min(1000, int(values.get("masteries") or 0))),
                     "challenge": str(values.get("challenge") or "")[:500]}
            return {"ok": True, "goals": self._experience.put(self._experience_account(), "goals", value)}
        except (ValueError, TypeError, AttributeError):
            return {"ok": False, "error": "Metas inválidas."}

    def set_productivity_settings(self, values):
        if not isinstance(values, dict):
            return {"ok": False, "error": "Preferências inválidas."}
        account = self._experience_account()
        current = self._experience.get(account, "settings", {})
        for key in ("silent", "sound", "streamer", "platform_theme", "auto_session"):
            if key in values:
                current[key] = bool(values[key])
        return {"ok": True, "settings": self._experience.put(account, "settings", current)}

    def get_activity(self, pending=False):
        account = self._experience_account()
        settings = self._experience.get(account, "settings", {})
        corners = settings.get("notification_corners") or {}
        return {"ok": True, "events": self._experience.events(account, bool(pending)),
                "native_notifications": bool(getattr(getattr(self, "_notifications", None), "ready", False)),
                "notification_corner": corners.get(self._compact_emulator_key(), corners.get("default", "bottom-left")),
                **self._experience.summary(account)}

    def set_notification_corner(self, corner):
        if corner not in {"top-left", "top-right", "bottom-left", "bottom-right"}:
            return {"ok": False, "error": "Canto inválido."}
        account = self._experience_account()
        settings = self._experience.get(account, "settings", {})
        emulator = self._compact_emulator_key()
        settings.setdefault("notification_corners", {})[emulator] = corner
        self._experience.put(account, "settings", settings)
        return {"ok": True, "emulator": emulator, "corner": corner}

    def acknowledge_notification(self, event_id):
        self._experience.acknowledge(self._experience_account(), str(event_id))
        return {"ok": True}

    def test_achievement_notification(self):
        import uuid
        notifier = getattr(self, "_notifications", None)
        if not notifier or not notifier.ready:
            return {"ok": True, "native": False}
        notifier.enqueue({"id": "test-" + uuid.uuid4().hex, "name": "Conquista de demonstração",
                          "game_title": "Teste de notificação", "mode": "hardcore", "points": 10,
                          "rarity": 5, "test": True}, self._experience_account())
        return {"ok": True, "native": True}

    def control_session(self, action, slug=""):
        if action == "start" and slug not in self.state:
            return {"ok": False, "error": "Escolha um jogo."}
        try:
            return {"ok": True, "session": self._experience.session(self._experience_account(), action, slug)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def get_companion_status(self):
        return self._companion_service.status()

    def set_guide_system_path(self, slug, system_id, edge_id):
        try:
            state = self._guides.set_system_path(slug, system_id, edge_id)
            self._refresh_smart_bundle(slug)
            return {"ok": True, "state": state}
        except smart_guide.SmartGuideError as exc:
            return {"ok": False, "error": str(exc)}

    def start_companion(self, address):
        try:
            return self._companion_service.start(str(address))
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": f"Não foi possível iniciar: {exc}. Verifique a rede privada e o firewall."}

    def stop_companion(self):
        return self._companion_service.stop()

    def new_companion_pairing(self):
        try:
            return self._companion_service.new_pairing()
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def approve_companion(self, request_id, approved=True):
        try:
            return self._companion_service.approve(str(request_id), bool(approved))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def revoke_companion(self, device_id):
        return self._companion_service.revoke(str(device_id))

    def _companion_snapshot(self, slug=""):
        slug = slug or self._active_slug
        with self._lock:
            game = copy.deepcopy(self.state.get(slug) or {})
        if not game:
            return {"ok": False, "error": "Abra um jogo no DigiTracker do PC."}
        bundle = self.get_smart_guide(slug)
        document = copy.deepcopy(bundle.get("current") or {})
        progress = bundle.get("effective_progress") or bundle.get("progress") or {}
        system_state = bundle.get("system_state") or {}
        version = smart_guide._json_hash([document.get("revision_id"), progress, system_state])
        revealed = set(progress.get("revealed_spoilers") or [])
        for chapter in document.get("chapters") or []:
            for index, block in enumerate(chapter.get("blocks") or []):
                if block.get("type") == "spoiler" and block["id"] not in revealed:
                    chapter["blocks"][index] = {"id": block["id"], "type": "spoiler", "title": "Spoiler oculto", "hidden": True}
        # Unapproved drafts, original documents and private notes never leave the PC.
        systems = []
        for original in document.get("systems") or []:
            if original.get("status") != "approved":
                continue
            system = copy.deepcopy(original)
            system["nodes"] = [node for node in system.get("nodes") or [] if not node.get("spoiler")]
            ids = {node["id"] for node in system["nodes"]}
            system["edges"] = [edge for edge in system.get("edges") or [] if edge["from"] in ids and edge["to"] in ids and not edge.get("spoiler")]
            systems.append(system)
        next_objective = bundle.get("next_objective") or {}
        if next_objective.get("block_id") not in {b["id"] for c in document.get("chapters") or [] for b in c.get("blocks") or [] if not b.get("hidden")}:
            next_objective = {}
        with self._companion_ai_lock:
            answer = copy.deepcopy(self._companion_ai.get(slug) or {})
        return {"ok": True, "version": version, "game": {key: game.get(key) for key in ("slug", "title", "platform", "art", "mastery")},
                "chapters": document.get("chapters") or [], "systems": systems,
                "media": [{"id": item.get("id"), "url": item.get("url"), "title": item.get("title")} for item in bundle.get("media") or [] if item.get("status") != "rejected"],
                "progress": {key: progress.get(key) for key in ("completed", "checkpoint", "revealed_spoilers")},
                "system_state": system_state, "objective": next_objective,
                "missables": game.get("pending_missables") or [], "achievements": game.get("achievements") or [], "answer": answer}

    def _companion_command(self, body):
        kind, slug = body.get("kind"), str(body.get("slug") or "")
        if slug not in self.state:
            return {"ok": False, "error": "Jogo não encontrado."}
        if kind == "ask":
            return self._start_companion_question(slug, str(body.get("block_id") or ""), str(body.get("question") or ""))
        with self._guides._write_lock:
            snapshot = self._companion_snapshot(slug)
            if body.get("version") != snapshot["version"]:
                return {"ok": False, "conflict": True, "error": "O progresso mudou no PC. Atualize e tente novamente."}
            if kind == "progress":
                block_id = str(body.get("block_id") or "")
                if block_id not in {b["id"] for c in snapshot["chapters"] for b in c.get("blocks") or []}:
                    return {"ok": False, "error": "Etapa não encontrada."}
                action = body.get("action")
                if action not in {"complete", "checkpoint", "reveal"}:
                    return {"ok": False, "error": "Ação não permitida."}
                result = self.update_guide_progress(slug, action, block_id, bool(body.get("value")))
                return {key: result[key] for key in ("ok", "error") if key in result}
            if kind == "requirement":
                if not isinstance(body.get("completed"), bool):
                    return {"ok": False, "error": "Valor inválido."}
                system = next((s for s in snapshot["systems"] if s["id"] == body.get("system_id")), {})
                edge = next((e for e in system.get("edges", []) if e["id"] == body.get("edge_id")), {})
                if body.get("requirement_id") not in {r["id"] for r in edge.get("requirements", [])}:
                    return {"ok": False, "error": "Requisito indisponível."}
                result = self.update_guide_requirement(slug, body.get("system_id"), body.get("edge_id"), body.get("requirement_id"), body["completed"])
                return {key: result[key] for key in ("ok", "error") if key in result}
            if kind == "goal":
                system = next((s for s in snapshot["systems"] if s["id"] == body.get("system_id")), {})
                if body.get("node_id") not in {n["id"] for n in system.get("nodes", [])}:
                    return {"ok": False, "error": "Objetivo indisponível."}
                result = self.set_guide_system_goal(slug, body.get("system_id"), body.get("node_id"))
                return {key: result[key] for key in ("ok", "error") if key in result}
        return {"ok": False, "error": "Comando não permitido no companion."}

    def _start_companion_question(self, slug, block_id, question):
        import guide_ai
        if not self.settings.get("smart_guide_consent") or not self._ai_key():
            return {"ok": False, "error": "Configure a IA e autorize o envio em Configurações no PC."}
        if not question.strip() or len(question) > 2000:
            return {"ok": False, "error": "Escreva uma pergunta de até 2.000 caracteres."}
        snapshot = self._companion_snapshot(slug)
        block = next((b for c in snapshot["chapters"] for b in c.get("blocks") or [] if b["id"] == block_id and not b.get("hidden")), None)
        if not block:
            return {"ok": False, "error": "Abra um trecho não oculto do guia primeiro."}
        with self._companion_ai_lock:
            if (self._companion_ai.get(slug) or {}).get("running"):
                return {"ok": False, "error": "Já existe uma pergunta em andamento."}
            self._companion_ai[slug] = {"running": True}
            generation = self._companion_generation
        config = self._ai_config()
        def worker():
            try:
                result = guide_ai.answer_guide_question(block, question, config)
            except Exception:
                result = {"error": "Não foi possível consultar a IA. Verifique o provedor no PC e tente novamente."}
            with self._companion_ai_lock:
                if generation == self._companion_generation:
                    self._companion_ai[slug] = {"running": False, **result}
        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "running": True}

    def get_diagnostics_report(self):
        from version import APP_VERSION
        return {"ok": True, "report": {"version": APP_VERSION, "generated_at": datetime.now().isoformat(),
                "games": len(self.state), "account_configured": bool(self._client),
                "ai_configured": bool(self._ai_key()), "atlas_jobs": [
                    {"status": job.get("status"), "has_error": bool(job.get("error"))}
                    for slug in self.state for job in self._guides.system_sources(slug)],
                "companion_running": bool(self._companion_service.server),
                "paired_devices": len(self._companion_service.sessions),
                "overlay_error_present": bool(getattr(self, "_overlay_error", "")),
                "notifications_error": getattr(getattr(self, "_notifications", None), "error", ""),
                "experience_error": self._experience_error}}
