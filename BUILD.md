# Empacotamento (PyInstaller)

Gera um executável standalone do DigiTracker. A pasta `ui/` é embutida no
binário; `config/` e `assets/` são criadas ao lado do executável na primeira
execução (dados graváveis e persistentes — suas credenciais e jogos ficam lá).

> **PyInstaller não faz cross-compile.** O build precisa rodar no mesmo SO de
> destino: para gerar `DigiTracker.exe`, rode em uma máquina **Windows**.

---

## Windows → `DigiTracker.exe`

Pré-requisitos:
- Python 3.12 (64 bits, versão usada pelo CI) — <https://www.python.org/downloads/windows/>
- Runtime do **WebView2** (já vem no Windows 11 e na maioria dos Windows 10;
  se faltar: <https://developer.microsoft.com/microsoft-edge/webview2/>)

No PowerShell, dentro da pasta `digitracker`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-build.txt
pyinstaller digitracker.spec --noconfirm
```

O executável sai em `dist\DigiTracker.exe`. É só distribuir esse arquivo único;
ao rodar, ele cria `config\` e `assets\` na mesma pasta.

O Guia Inteligente lê texto e imagens incorporadas com `pypdf`/Pillow. OCR de
PDF digitalizado é opcional: em execução por fonte, instale `PyMuPDF`,
`pytesseract` e o Tesseract no Windows. Sem esse conjunto o app mantém a
importação normal e mostra um diagnóstico acionável para PDFs sem texto.

Para uma release atualizável, ajuste `APP_VERSION` em `version.py` e crie a tag
correspondente (`vX.Y.Z`). O workflow executa os testes, gera o EXE e publica
também `DigiTracker.exe.sha256`, exigido pela instalação automática. Não publique
manualmente um EXE sem o checksum: o app oferecerá apenas abrir a release.

## macOS → `DigiTracker`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-build.txt
pyinstaller digitracker.spec --noconfirm
```

Sai em `dist/DigiTracker`. (O WKWebView é nativo, sem dependências extras.)

## Linux → `DigiTracker`

O pywebview usa o WebKit2GTK do sistema, que **não** é embutido pelo PyInstaller.
A máquina que rodar o binário precisa ter `gir1.2-webkit2-4.1` e `python3-gi`
instalados (no Ubuntu já costumam vir). Crie o venv com `--system-site-packages`
para o PyInstaller enxergar o `gi`:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-build.txt
pyinstaller digitracker.spec --noconfirm
```

Sai em `dist/DigiTracker`.

---

## Ícone (opcional)

Coloque um `ui/icon.ico` (Windows) ou `ui/icon.icns` (macOS) e ajuste a linha
`icon=` no `digitracker.spec`.

## Dicas / solução de problemas

- **Antivírus no Windows** às vezes marca binários onefile do PyInstaller como
  suspeitos (falso positivo). Assinar o executável resolve em distribuição séria.
- Se o app abrir e fechar na hora, gere uma versão com console para ver o erro:
  no `.spec`, troque `console=False` por `console=True` e rebuilde.
- Build mais rápido para depurar: use onedir em vez de onefile (substitua o bloco
  `EXE(...)` por `EXE(...)` + `COLLECT(...)`). O onefile é melhor só para
  distribuir.
