# DigiTracker — Handoff para a sessão do Windows

## Estado atual — rodada 11/09/2026

O código local está na linha `v0.11.4` e recebeu a implementação do Atlas
estruturado, fontes GameFAQs/Wayback/HTML, itens e companion detalhado. A
extração preserva JSON editorial e Markdown derivado; tabelas com células
mescladas/cabeçalhos repetidos, marcadores `Evolution Item` e requisitos por
item/local têm regressões cobrindo esses casos. Cards exibem IDs numéricos,
pendências apontam tabela/linha/cards afetados, e o Atlas oferece foco/mapa/lista,
preenchimento de imagens por nome da entidade, cancelamento/desfazer e exclusão
segura de sistemas.

O companion usa protocolo v2 com escrita por alvo, revisão, `request_id`
idempotente, conflitos explícitos, quantidade de item `null/0/positivo` e
polling que preserva detalhe/foco/rascunho. Foram adicionados endpoints de
leitura autenticados para capítulos, Atlas, itens, conquistas e referências.

Validação local desta rodada: **599 testes passaram**, `compileall`, `node
--check` dos dois companions e `git diff --check` passaram. O build físico
Windows foi concluído em `dist/DigiTracker.exe`; ainda falta abrir e validar
o WebView2, além do smoke com provedor real. Não houve commit, push ou tag
nesta rodada; fazer isso somente após autorização explícita do autor.

O registro detalhado está em
[`docs/EXECUCAO_CELULAR_ATLAS_FONTES_ITENS.md`](EXECUCAO_CELULAR_ATLAS_FONTES_ITENS.md).

> Contexto para uma sessão do Claude Code rodando na máquina **Windows** do autor.
> O desenvolvimento e os testes de Linux foram feitos numa sessão separada (GNOME/Wayland).
> Aqui no Windows está o **alvo real**: onde o `.exe` roda e onde o overlay gruda no emulador (Dolphin etc.).

## O que é o projeto

App desktop **pywebview** (Python + HTML/CSS/JS vanilla) que acompanha conquistas do **RetroAchievements** reordenadas pela ordem de um guia, com um **overlay que gruda por cima da janela do emulador**. Uso pessoal, single-user, offline-first.

- Repositório: **WnMoura/digitracker** (privado), branch `main`, código em preparação para a release **v0.11.4**.
- Backend: `engine.py` (janela + `js_api` + sincronização + estado + overlay). Módulos principais: `ra_api.py`, `gamefaqs.py`, `guide_ai.py`, `guide_parser.py`, `smart_guide.py`, `guide_media.py`, `experience.py`, `experience_api.py`, `companion.py`, `emulator_tracker.py`, `updater.py` e provedores de imagem.
- Frontend: `ui/index.html`, `ui/app.js`, `ui/style.css`, fontes locais (`ui/fonts/`).
- Testes: `tests/` (pytest) — **599 passando** na validação integrada atual
  (os números menores abaixo são registros históricos das releases anteriores).

### Contexto histórico da release v0.10.6

- O Atlas agora processa a fonte em lotes auditáveis, salva checkpoints atômicos e retoma após falhas ou rate limits.
- Gemini é serializado, respeita `Retry-After`/`RetryInfo` e usa fallback automático quando o modelo padrão não está disponível.
- Relações alternativas do Atlas entre os mesmos nós preservam rótulo, tipo de rota e requisitos, sem serem rejeitadas como duplicatas.
- A importação do GameFAQs separa navegação/anúncios do conteúdo e preserva tabelas e paginação.
- PDF, GameFAQs e Atlas exibem estado de processamento e erros classificados; a área de IA mostra telemetria local sem chaves, prompts ou respostas.
- A release histórica usava `0.10.6`; o estado local atual aponta para `0.11.4` em
  `version.py`. Antes de publicar, a tag planejada deve corresponder exatamente
  ao valor atual para o workflow aceitar o build.

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

## O que validar no Windows (aplicável ao v0.11.4)

1. **Overlay grudando no emulador (o principal):** abrir Dolphin/PCSX2/ePSXe em **janela ou borderless** → o app deve entrar em compacto, **dimensionar proporcional** à janela do emulador (~26%×44%) e **grudar no canto superior-direito de dentro**; seguir se a janela mover/redimensionar; **restaurar** ao fechar. Toggle "Ajustar ao tamanho do emulador" nas Configurações (ligado por padrão).
2. **Arraste do overlay** pela faixa de cima (o `makeDraggable`).
3. **Download de imagem corrigido:** o bug era o token da API indo pro CDN (403). Abrir um jogo → **Trocar arte** → escolher uma capa → deve **baixar e aplicar** (era o caso que falhava).
4. **Fontes de imagem:** Configurações → **Fontes de imagem** — SteamGridDB, **RAWG** (chave), **IGDB** (Client ID + Secret da Twitch), e **Colar URL**. No seletor, alternar as abas.
5. **IA/Atlas nas dicas:** importar um guia do GameFAQs ou PDF num jogo salvo → iniciar o processamento na aba Guia Inteligente/Atlas; confirmar tela de progresso, retomada após falha e aprovação explícita da revisão. Refinamento/tradução não deve alterar a ordem nem os IDs das conquistas.
6. **Experiência e companion:** conferir jornada, sessões, notificações e pareamento/revogação do companion local.

## Build/Release

- CI: `.github/workflows/build-windows.yml` dispara em **push de tag `v*`**, roda a suíte de testes, gera o `.exe` e o checksum SHA-256, e publica o Release. Actions já estão em v7 (Node 24).
- **Gotcha conhecido:** às vezes o push da tag **não dispara** o build (hiccup do GitHub Actions). Fallback confiável: `gh workflow run build-windows.yml --ref <tag>` — como a `ref` é a própria tag, o passo de Release roda e publica o `.exe` igual.
- Para validar uma tag antes do push: confira `version.py` no commit que será
  publicado e confirme que `APP_VERSION` corresponde exatamente ao sufixo da
  tag.

## Itens em aberto

- **"Deixar online / não rebuildar toda hora":** decisão pendente do autor. A restrição-chave é que o **overlay é inerentemente desktop** (navegador não enxerga/posiciona janela de outro app), então site puro perde a feature central. Caminhos sem perder o overlay: **auto-update do `.exe`** (recomendado), **carregar a UI da web** (frontend ao vivo, perde offline-first), a combinação dos dois, ou um **painel web separado** só de leitura. Se o autor decidir por aqui, esse é um bom ponto de partida.
- Ajustes finos possíveis: proporção do auto-ajuste (`OVERLAY_FIT_W/H` em `engine.py`), qualidade das fontes RAWG (mais fundos) vs IGDB/SteamGridDB (capas), prompt da tradução (`guide_ai.py`).

## Convenções do repo

- O repositório Git correto é a pasta `digitracker` (há um Git vazio no diretório pai `New project`).
- Só commitar/pushar quando o autor pedir. Histórico usa trailers `Co-Authored-By:` e `Claude-Session:` (cada sessão tem o seu próprio link).
- Tudo verde: `python -m pytest tests/ -q` antes de commitar.
