"""Independent, non-activating achievement notification surface."""
from __future__ import annotations

import copy
import queue
import threading
import time

import emulator_tracker


class NotificationBridge:
    def __init__(self, service):
        self._service = service

    def get_notification(self):
        with self._service.lock:
            return copy.deepcopy(self._service.current)

    def notification_rendered(self, event_id):
        if event_id == self._service.current.get("id"):
            self._service.rendered.set()
        return {"ok": True}


class Notifications:
    def __init__(self, api):
        self.api, self.window, self.hwnd = api, None, None
        self.native = emulator_tracker.create_overlay_input()
        self.queue = queue.Queue(maxsize=500)
        self.lock = threading.Lock()
        self.current = {}
        self.rendered = threading.Event()
        self.stopped = threading.Event()
        self.error = ""
        self.ready = False
        self.bridge = NotificationBridge(self)

    def attach(self, window):
        self.window = window
        self.hwnd = self.api._tracker.own_window_handle(window, "DigiTracker achievement")
        # Fail closed: no fallback that might activate the dashboard/game.
        if not self.hwnd:
            self.error = "Janela de notificação não identificada. Consulte Atividade."
            return
        self.ready = True
        threading.Thread(target=self._run, daemon=True).start()

    def enqueue(self, event, account):
        try:
            self.queue.put_nowait((copy.deepcopy(event), account))
        except queue.Full:
            self.error = "Fila de notificações cheia. Consulte Atividade."

    def _place(self):
        rect = self.api._current_overlay_rect() or self.api._fallback_overlay_rect()
        settings = self.api._experience.get(self.api._experience_account(), "settings", {})
        corners = settings.get("notification_corners") or {}
        corner = corners.get(self.api._compact_emulator_key(), corners.get("default", "bottom-left"))
        scale = self.native.dpi_scale(self.hwnd)
        size = round(360 * scale), round(112 * scale)
        x, y = emulator_tracker.dock_position(rect, size, 18, corner)
        self.native.move_no_activate(self.hwnd, x, y, *size)

    def _run(self):
        while not self.stopped.is_set():
            try:
                event, account = self.queue.get(timeout=.3)
            except queue.Empty:
                continue
            if account != self.api._experience_account():
                continue
            settings = self.api._experience.get(account, "settings", {})
            if settings.get("silent") and not event.get("test"):
                self.api._experience.acknowledge(account, event["id"])
                continue
            self.rendered.clear()
            with self.lock:
                self.current = event
            if not self.rendered.wait(3):
                self.error = "A interface da notificação não respondeu. Consulte Atividade."
                continue
            try:
                self._place()
                result = self.native.apply_passive(self.hwnd, expected_size=(360, 112), opacity=100, click_through=True)
                if not result.get("ok"):
                    self.error = result.get("error", "Modo passivo indisponível.")
                    continue
                self.native.show_no_activate(self.hwnd)
                if settings.get("sound"):
                    try:
                        import winsound
                        winsound.MessageBeep(winsound.MB_OK)
                    except (ImportError, RuntimeError):
                        pass
                until = time.monotonic() + 6
                while not self.stopped.wait(.3) and time.monotonic() < until:
                    if account != self.api._experience_account():
                        break
                    self._place()
                self.native.hide_window(self.hwnd)
                self.api._experience.acknowledge(account, event["id"])
            except Exception as exc:
                self.error = type(exc).__name__
                self.native.hide_window(self.hwnd)

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.native.close()
        if self.hwnd:
            self.native.hide_window(self.hwnd)
        if self.window:
            self.api._window_op(self.window.destroy)
