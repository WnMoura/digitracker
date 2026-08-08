# DigiTracker — Handoff para a sessão do Windows

> Contexto para uma sessão do Claude Code rodando na máquina **Windows** do autor.
> O desenvolvimento e os testes de Linux foram feitos numa sessão separada (GNOME/Wayland).
> Aqui no Windows está o **alvo real**: onde o `.exe` roda e onde o overlay gruda no emulador (Dolphin etc.).

## O que é o projeto

App desktop **pywebview** (Python + HTML/CSS/JS vanilla) que acompanha conquistas do **RetroAchievements** reordenadas pela ordem de um guia, com um **overlay que gruda por cima da janela do emulador**. Uso pessoal, single-user, offline-first.

- Repositório: **WnMoura/digitracker** (privado), branch `main`, última release **v0.5.0**.
- Backend: `engine.py` (janela + `js_api` + sync 30s + estado + overlay). Módulos: `ra_api.py`, `gamefaqs.py`, `guide_ai.py`, `guide_parser.py`, `emulator_tracker.py`, `steamgriddb.py`, `rawg.py`, `igdb.py`, `image_fetch.py`.
- Frontend: `ui/index.html`, `ui/app.js`, `ui/style.css`, fontes locais (`ui/fonts/`).
- Testes: `tests/` (pytest) — **374 passando** na última medição.

## Como rodar / buildar no Windows

- **Rodar do fonte:** precisa de Python 3.12 + `pip install -r requirements.txt` + runtime do **WebView2** (Edge). `python engine.py`.
- **Buildar o `.exe`:** PyInstaller com `digitracker.spec` (ver `BUILD.md`) — onefile, sem console. O CI já faz isso no Windows a cada push de tag `v*` e publica o Release.
- **Onde ficam os dados no `.exe`:** `config/` (incl. `secrets.json`, `settings.json`), `assets/` e cache ficam **na mesma pasta do `DigiTracker.exe`** (não na pasta temporária). As chaves que o autor configurou estão em `<pasta do exe>/config/secrets.json`.

## Fatos específicos do Windows (importante)

- A janela é **WebView2 (Edge Chromium)** dentro de um **WinForms**.
- **Detecção do emulador no Windows** usa `WindowsTracker` (ctypes/user32 `EnumWindows`) — este é o backend "alvo" e já funcionava. **A correção de detecção via `xwininfo` que foi feita é só do Linux/Wayland e NÃO afeta o Windows.**
- **Always-on-top:** re-aplicado a cada ciclo via `SetWindowPos(HWND_TOPMOST)` (`make_topmost`) — é o que impede o overlay de sumir atrás do Dolphin quando o jogo rouba o foco.
- **Arraste do overlay no Windows:** o drag-region nativo do pywebview **não funciona no WinForms** (ele chama `window.move` na thread do bridge js_api, que não surte efeito). Por isso existe um arraste próprio: `makeDraggable` (ui/app.js) → `move_window` → `_window_op` (roda numa thread própria, igual fechar/minimizar/dockar). Se o arraste falhar no Windows, é aqui que se investiga.
- **Fullscreen exclusivo (D3D):** nenhum overlay aparece por cima. O app detecta (`SHQueryUserNotificationState`) e, com o interruptor ligado nas Configurações, pode mandar **Alt+Enter** ou levar o overlay para o **segundo monitor**. Sem interruptor, só avisa.

## O que validar no Windows (novidades da v0.5.0 e recentes)

1. **Overlay grudando no emulador (o principal):** abrir Dolphin/PCSX2/ePSXe em **janela ou borderless** → o app deve entrar em compacto, **dimensionar proporcional** à janela do emulador (~26%×44%) e **grudar no canto superior-direito de dentro**; seguir se a janela mover/redimensionar; **restaurar** ao fechar. Toggle "Ajustar ao tamanho do emulador" nas Configurações (ligado por padrão).
2. **Arraste do overlay** pela faixa de cima (o `makeDraggable`).
3. **Download de imagem corrigido:** o bug era o token da API indo pro CDN (403). Abrir um jogo → **Trocar arte** → escolher uma capa → deve **baixar e aplicar** (era o caso que falhava).
4. **Fontes de imagem:** Configurações → **Fontes de imagem** — SteamGridDB, **RAWG** (chave), **IGDB** (Client ID + Secret da Twitch), e **Colar URL**. No seletor, alternar as abas.
5. **IA nas dicas:** importar um guia do GameFAQs num jogo salvo → aba Dicas → **✨ Refinar dicas** e **🌐 Traduzir (PT-BR)** (precisa de chave de IA em Configurações → Inteligência artificial). Refina/traduz **só as dicas**, sem mexer na ordem das conquistas.

## Build/Release

- CI: `.github/workflows/build-windows.yml` dispara em **push de tag `v*`** e publica o `.exe` como Release. Actions já estão em v7 (Node 24).
- **Gotcha conhecido:** às vezes o push da tag **não dispara** o build (hiccup do GitHub Actions). Fallback confiável: `gh workflow run build-windows.yml --ref <tag>` — como a `ref` é a própria tag, o passo de Release roda e publica o `.exe` igual.

## Itens em aberto

- **"Deixar online / não rebuildar toda hora":** decisão pendente do autor. A restrição-chave é que o **overlay é inerentemente desktop** (navegador não enxerga/posiciona janela de outro app), então site puro perde a feature central. Caminhos sem perder o overlay: **auto-update do `.exe`** (recomendado), **carregar a UI da web** (frontend ao vivo, perde offline-first), a combinação dos dois, ou um **painel web separado** só de leitura. Se o autor decidir por aqui, esse é um bom ponto de partida.
- Ajustes finos possíveis: proporção do auto-ajuste (`OVERLAY_FIT_W/H` em `engine.py`), qualidade das fontes RAWG (mais fundos) vs IGDB/SteamGridDB (capas), prompt da tradução (`guide_ai.py`).

## Convenções do repo

- Só commitar/pushar quando o autor pedir. Histórico usa trailers `Co-Authored-By:` e `Claude-Session:` (cada sessão tem o seu próprio link).
- Tudo verde: `python -m pytest tests/ -q` antes de commitar.
