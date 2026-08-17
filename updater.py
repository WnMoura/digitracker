"""Atualizador portátil do DigiTracker para GitHub Releases.

O módulo não conhece a UI. Ele consulta a release estável, baixa o executável e
seu SHA-256 em background e prepara um helper ``.cmd`` que substitui o onefile
somente depois que o processo atual termina. Em qualquer falha de substituição,
o helper restaura o executável anterior.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import requests


REPO = "WnMoura/digitracker"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
EXE_ASSET = "DigiTracker.exe"
SHA_ASSET = "DigiTracker.exe.sha256"
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SHA_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")


class UpdateError(RuntimeError):
    """Falha esperada ao consultar, baixar ou preparar uma atualização."""


def parse_version(value: str):
    """Converte apenas versões estáveis ``X.Y.Z``/``vX.Y.Z`` para uma tupla."""
    match = _VERSION_RE.fullmatch((value or "").strip())
    return tuple(map(int, match.groups())) if match else None


def release_info(payload: dict, current_version: str) -> dict:
    """Valida e normaliza a resposta de ``releases/latest``.

    Releases incompletas continuam visíveis para download manual, mas somente
    as que contêm o executável e seu checksum podem ser instaladas pelo app.
    """
    if not isinstance(payload, dict):
        raise UpdateError("O GitHub devolveu uma resposta inválida.")
    if payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("A release mais recente não é uma versão estável.")

    current = parse_version(current_version)
    latest_text = str(payload.get("tag_name") or "")
    latest = parse_version(latest_text)
    if current is None or latest is None:
        raise UpdateError("A versão publicada não segue o formato vX.Y.Z.")

    assets = {
        str(asset.get("name")): asset
        for asset in (payload.get("assets") or [])
        if isinstance(asset, dict)
    }
    exe = assets.get(EXE_ASSET) or {}
    checksum = assets.get(SHA_ASSET) or {}
    available = latest > current
    installable = bool(
        available
        and exe.get("browser_download_url")
        and checksum.get("browser_download_url")
    )
    return {
        "ok": True,
        "current_version": current_version,
        "latest_version": latest_text.lstrip("v"),
        "update_available": available,
        "installable": installable,
        "notes": str(payload.get("body") or ""),
        "published_at": str(payload.get("published_at") or ""),
        "release_url": str(payload.get("html_url") or f"https://github.com/{REPO}/releases/latest"),
        "download_size": int(exe.get("size") or 0),
        "exe_url": str(exe.get("browser_download_url") or ""),
        "sha_url": str(checksum.get("browser_download_url") or ""),
    }


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_is_writable(path: Path) -> bool:
    """Testa escrita real na pasta do executável sem tocar em dados do usuário."""
    probe = path / f".digitracker-write-{os.getpid()}.tmp"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _cmd_value(value: str) -> str:
    """Escapa expansão de ``%VAR%`` em valores inseridos no helper batch."""
    return str(value).replace("%", "%%")


def build_helper_script(target: Path, new_exe: Path, pid: int) -> str:
    """Gera o helper que espera o app fechar, troca o EXE e reinicia.

    O ``.bak`` só é apagado pela nova versão após ela chegar ao bootstrap, de
    modo que uma atualização que nem inicia ainda pode ser recuperada.
    """
    target_text = _cmd_value(str(target.resolve()))
    new_text = _cmd_value(str(new_exe.resolve()))
    backup_text = _cmd_value(str(target.resolve()) + ".bak")
    return f"""@echo off
setlocal
chcp 65001 >nul
set "TARGET={target_text}"
set "NEW_EXE={new_text}"
set "BACKUP={backup_text}"
set "APP_PID={int(pid)}"

:wait_for_exit
tasklist /FI "PID eq %APP_PID%" /FO CSV /NH 2>nul | findstr /C:"%APP_PID%" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait_for_exit
)

if exist "%BACKUP%" del /Q "%BACKUP%" >nul 2>&1
move /Y "%TARGET%" "%BACKUP%" >nul
if errorlevel 1 exit /b 10

move /Y "%NEW_EXE%" "%TARGET%" >nul
if errorlevel 1 (
  move /Y "%BACKUP%" "%TARGET%" >nul
  exit /b 11
)

rem PyInstaller 6.22+ validates the inherited onefile parent environment.
rem The helper is intentionally a fresh launcher, so discard the old bootloader
rem state before starting the replacement executable.
set "PYINSTALLER_RESET_ENVIRONMENT=1"
start "" "%TARGET%"
endlocal
del /Q "%~f0" >nul 2>&1
"""


class UpdateManager:
    """Estado e operações thread-safe do atualizador."""

    def __init__(self, current_version: str, executable: Path | str | None = None,
                 *, frozen: bool | None = None, session=None, staging_root=None):
        self.current_version = current_version
        self.executable = Path(executable or sys.executable).resolve()
        self.frozen = bool(getattr(sys, "frozen", False) if frozen is None else frozen)
        self.session = session or requests.Session()
        self.staging_root = Path(staging_root or (Path(tempfile.gettempdir()) / "DigiTracker-update"))
        self.target_writable = directory_is_writable(self.executable.parent) if self.frozen else False
        self._lock = threading.Lock()
        self._release: dict | None = None
        self._downloaded: Path | None = None
        self._status = {
            "ok": True,
            "phase": "idle",
            "current_version": current_version,
            "bytes_downloaded": 0,
            "bytes_total": 0,
            "error": "",
        }

    def _public(self, data: dict | None = None) -> dict:
        value = dict(data or self._status)
        value.pop("exe_url", None)
        value.pop("sha_url", None)
        value["source_mode"] = not self.frozen
        value["target_writable"] = self.target_writable
        return value

    def status(self) -> dict:
        with self._lock:
            return self._public()

    def check(self) -> dict:
        try:
            response = self.session.get(
                LATEST_RELEASE_URL,
                headers={"Accept": "application/vnd.github+json", "User-Agent": f"DigiTracker/{self.current_version}"},
                timeout=8,
            )
            if response.status_code == 404:
                raise UpdateError(
                    "As releases do DigiTracker não estão acessíveis publicamente. "
                    "Torne o repositório público e publique uma release vX.Y.Z."
                )
            response.raise_for_status()
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise UpdateError("O GitHub devolveu JSON inválido.") from exc
            info = release_info(payload, self.current_version)
            if not self.frozen:
                info["installable"] = False
            elif not self.target_writable:
                info["installable"] = False
                info["error"] = "A pasta do DigiTracker não permite substituir o executável."
            with self._lock:
                self._release = info
                self._status = {**info, "phase": "available" if info["update_available"] else "current",
                                "bytes_downloaded": 0, "bytes_total": info["download_size"],
                                "error": info.get("error", "")}
                return self._public()
        except (requests.RequestException, UpdateError, OSError) as exc:
            with self._lock:
                self._status.update({"ok": False, "phase": "error", "error": str(exc)})
                return self._public()

    def start_download(self) -> dict:
        with self._lock:
            release = dict(self._release or {})
            if self._status.get("phase") == "downloading":
                return self._public()
            if not release.get("update_available"):
                return {"ok": False, "phase": "error", "error": "Nenhuma atualização disponível."}
            if not release.get("installable"):
                return {"ok": False, "phase": "error",
                        "error": release.get("error") or "Esta atualização só pode ser baixada manualmente."}
            self._status.update({"ok": True, "phase": "downloading", "error": "",
                                 "bytes_downloaded": 0, "bytes_total": release.get("download_size", 0)})
        threading.Thread(target=self._download, args=(release,), daemon=True).start()
        return self.status()

    def _download(self, release: dict) -> None:
        version = release["latest_version"]
        stage = self.staging_root / version
        partial = stage / f"{EXE_ASSET}.part"
        ready = stage / f"{EXE_ASSET}.new"
        try:
            stage.mkdir(parents=True, exist_ok=True)
            partial.unlink(missing_ok=True)
            ready.unlink(missing_ok=True)

            checksum_response = self.session.get(release["sha_url"], timeout=15)
            checksum_response.raise_for_status()
            match = _SHA_RE.search(checksum_response.text[:4096])
            if not match:
                raise UpdateError("O checksum publicado é inválido.")
            expected = match.group(1).lower()

            response = self.session.get(release["exe_url"], stream=True, timeout=(10, 60))
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or release.get("download_size") or 0)
            digest = hashlib.sha256()
            downloaded = 0
            with partial.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    stream.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    with self._lock:
                        self._status.update({"bytes_downloaded": downloaded, "bytes_total": total})

            if digest.hexdigest().lower() != expected:
                raise UpdateError("O arquivo baixado não corresponde ao SHA-256 publicado.")
            os.replace(partial, ready)
            with self._lock:
                self._downloaded = ready
                self._status.update({"ok": True, "phase": "ready", "error": "",
                                     "bytes_downloaded": downloaded, "bytes_total": total})
        except (requests.RequestException, UpdateError, OSError) as exc:
            partial.unlink(missing_ok=True)
            with self._lock:
                self._status.update({"ok": False, "phase": "error", "error": str(exc)})

    def prepare_install(self, pid: int) -> dict:
        with self._lock:
            downloaded = self._downloaded
            if self._status.get("phase") != "ready" or not downloaded or not downloaded.exists():
                return {"ok": False, "error": "A atualização ainda não terminou de baixar."}
        if os.name != "nt" or not self.frozen:
            return {"ok": False, "error": "A instalação automática está disponível apenas no executável Windows."}
        if not self.target_writable:
            return {"ok": False, "error": "A pasta do DigiTracker não permite instalar a atualização."}

        helper = downloaded.parent / "install-update.cmd"
        try:
            helper.write_text(build_helper_script(self.executable, downloaded, pid), encoding="utf-8")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["cmd.exe", "/d", "/c", str(helper)],
                close_fds=True,
                creationflags=flags,
                cwd=str(self.executable.parent),
            )
        except OSError as exc:
            return {"ok": False, "error": f"Não foi possível iniciar o instalador: {exc}"}
        with self._lock:
            self._status.update({"ok": True, "phase": "installing", "error": ""})
        return {"ok": True, "phase": "installing"}


def cleanup_backup(executable: Path | str | None = None) -> None:
    """Remove o backup somente depois que a nova versão chegou ao bootstrap."""
    if not getattr(sys, "frozen", False):
        return
    target = Path(executable or sys.executable).resolve()
    try:
        Path(str(target) + ".bak").unlink(missing_ok=True)
        shutil.rmtree(Path(tempfile.gettempdir()) / "DigiTracker-update", ignore_errors=True)
    except OSError:
        pass
