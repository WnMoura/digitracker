# -*- mode: python ; coding: utf-8 -*-
"""
Spec do PyInstaller para o DigiTracker.

Gera um único executável (onefile) sem console:
  - Windows:  DigiTracker.exe   (backend EdgeChromium / WebView2)
  - macOS:    DigiTracker       (backend WKWebView)
  - Linux:    DigiTracker       (backend GTK/WebKit2)

PyInstaller NÃO faz cross-compile: rode o build no MESMO sistema operacional
que você quer empacotar. Para o `.exe`, rode em uma máquina Windows.

Build:
    pip install -r requirements.txt -r requirements-build.txt
    pyinstaller digitracker.spec --noconfirm

A pasta `ui/` é embutida no executável. Já `config/` e `assets/` são criadas
ao lado do executável na primeira execução (dados graváveis e persistentes).
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Recursos read-only embutidos (a interface).
datas = [("ui", "ui")]
binaries = []
hiddenimports = collect_submodules("webview")

# Coleta tudo do pywebview: submódulos de plataforma + DLLs do WebView2
# (Microsoft.Web.WebView2.Core.dll, WebView2Loader.dll) no Windows.
for pkg in ("webview",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# pythonnet (clr) é usado pelo backend EdgeChromium no Windows.
hiddenimports += ["clr", "clr_loader", "pythonnet", "proxy_tools"]

# pypdf: leitura do PDF do guia (ordenar walkthrough). Garante os submódulos.
hiddenimports += collect_submodules("pypdf")


a = Analysis(
    ["engine.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DigiTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # app GUI — sem janela de terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ui/icon.ico",       # ícone do app (Windows)
)
