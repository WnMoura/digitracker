# DigiTracker — Handoff para a sessão do Windows

## Estado atual — Atlas, rodada 12/09/2026

Base local: `main`, com a implementação do Atlas na linha `v0.11.8`. A tag
`v0.11.7` existente é apenas local e aponta para o commit anterior desta
rodada; não houve push nem release no GitHub. A tag `v0.11.8` será criada
somente no commit que contém as validações concluídas abaixo; o push permanece
separado.

- `atlas_entities.py` separa listas explícitas de origens/destinos e remove
  marcadores de nota do nome. Nomes editoriais exatos vencem a pontuação;
  combinações ambíguas/fusões não viram rotas independentes inventadas. Um
  único link explícito pode ignorar uma continuação editorial em minúsculas
  (por exemplo, `Chuumon and your bad decisions`), mas qualquer segundo nome
  não vinculado mantém a linha pendente para revisão.
- `guide_ai.py` usa contrato de extração v5: tabelas diretas antes das inversas,
  `Previous Forms` como origem e a primeira coluna como destino nos mapas
  declarados pela IA. Tabelas inversas corroboram relações detalhadas. Duplicatas
  consolidam referências; operadores, cotas e condições diferentes preservam
  rotas alternativas. Cada linha mantém seu resultado de cobertura.
- `smart_guide.reconcile_system_ids` reaproveita IDs de entidades/rotas iguais
  na substituição aprovada, preservando imagens, objetivos e marcações. Uma
  imagem de um card agrupado antigo não passa para uma entidade individual.
- Rascunhos estruturados que ainda tragam um card composto (por exemplo,
  `Agumon, Guilmon`) agora ficam bloqueados com `kind=combined_entity_card`,
  número do card e ação de correção; não são aprovados silenciosamente.
- `atlas_images.py` aceita contexto/jogo e domínio de wiki opcionais; padrão
  continua só o nome. Filtra evidência de contexto, domínio, entidade e vídeos
  versus/gameplay; prioriza ilustrações da própria fonte quando compatíveis.
  É filtro de metadados, não reconhecimento visual infalível.
- `atlas_image_fill.py` controla fila persistente por jogo/sistema/prévia:
  tentativas por card, outros candidatos/provedores, backoff de 15 a 300s,
  pausa/retomada e conclusão somente sem imagens faltantes. Arquivos ausentes
  voltam à fila. Downloads novos são validados com Pillow. Associação CAS e
  desfazer não sobrescrevem alterações manuais concorrentes. Pausa permanece
  no disco; fila ativa é retomada ao abrir o Atlas após reiniciar o app.
- `ui/atlas.css` e `ui/app.js`: cabeçalho compacto, cartões verticais 176×226,
  inspector separado, zoom no rodapé, foco padrão, busca por nome/#ID,
  conexões adicionais paginadas, filtros recolhidos. Mapa completo usa camadas
  e componentes fortemente conectados para ciclos. Autozoom mínimo 85%; em
  largura estreita usa lista. Prévia não deixa seu aviso ocupar o mapa inteiro.

Validação local: **624 testes pytest**, `compileall`, `node --check ui/app.js`,
`git diff --check` e `tests/atlas_ui_smoke.cjs` passaram. O smoke usa Edge com dados sintéticos e
valida 1600×900, 1280×720, largura 820, prévia, modal de contexto, busca, marcação,
150 nós e ciclo. Build PyInstaller concluído em
`dist-check/atlas-v0.11.8-corrective/DigiTracker.exe`; os novos módulos e CSS estão incluídos.
A abertura nativa pelo `computer-use` não foi validada: a autorização para
abrir a janela expirou. Não confundir o smoke do Edge com validação do WebView2.

Auditoria de reimportação (não versionada): a captura salva do FAQ 64658 foi
materializada com **mapeamentos de colunas explícitos de teste, sem chamar IA**:
144 cartões anteriores → 91 entidades, 198 caminhos, nenhum nome agrupado por
vírgula; 593/593 linhas selecionadas cobertas, com 328 exclusões justificadas
por papel documentado. O rascunho legado de 144 entidades foi bloqueado por
20 cards compostos; ele não substitui o publicado. A prévia limpa preserva
91/91 IDs, 59 das 68 associações de imagem aplicáveis, o objetivo existente,
cartões sequenciais e zero arestas duplicadas. Os hashes da fonte e de
`current.json` permaneceram inalterados durante a revisão.

Validação externa controlada concluída com a chave/modelo configurados pelo
usuário: Gemini `gemini-3.5-flash-lite` processou as seis páginas do FAQ 71975
em 85 lotes (248 tabelas capturadas, incluindo 323 linhas de evolução), gerando
147 nós e 332 arestas. Após a revisão da fonte, 246 tabelas
e 459/459 linhas foram cobertas; duas tabelas vazias de Sukamon foram excluídas
explicitamente e a linha `Chuumon and your bad decisions` foi corroborada pelo
link único `Chuumon`, sem criar card composto nem aresta duplicada. Todas as
seis páginas foram referenciadas e não restaram pendências bloqueantes. Os
avisos de cota continuam não bloqueantes porque a semântica da cota não é
determinável na fonte.

Também foram validados três downloads reais de imagem em biblioteca isolada
(Agumon, Greymon e MetalGreymon): 3/3 aprovados, arquivos locais presentes e
hashes registrados; nenhum dado publicado ou associação do usuário foi
alterado. A execução real usou o provedor/modelo Gemini configurado e a
revisão não grava o resultado diretamente em Downloads/config.

O guia publicado em Downloads não foi modificado. Capturas da cópia local estão
em `docs/screenshots/atlas-context/atlas-real-1600.png`,
`atlas-real-1280.png` e `imagens-contexto.png`. As imagens nessas capturas são
as antigas já salvas; os três downloads reais acima são evidência isolada de
associação, não uma substituição automática do acervo publicado.

Para aplicar a correção a um guia antigo: **Fontes e manutenção → Reprocessar
fonte salva**, revisar seleção/resultado e aprovar. Para trocar imagens antigas
inadequadas: **Preencher imagens**, informar jogo/wiki e marcar **Refazer também
as imagens existentes**. Não alterar dados publicados sem essa revisão.

## Histórico — rodada 11/09/2026 (antes da v0.11.7)

O código local está na linha `v0.11.5` e recebeu a implementação do Atlas
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
o WebView2, além do smoke com provedor real. O commit de implementação já foi
publicado em `26fc247`; a tag `v0.11.5` será publicada após este ajuste de
versão.

O registro detalhado está em
[`docs/EXECUCAO_CELULAR_ATLAS_FONTES_ITENS.md`](EXECUCAO_CELULAR_ATLAS_FONTES_ITENS.md).

> Contexto para uma sessão do Claude Code rodando na máquina **Windows** do autor.
> O desenvolvimento e os testes de Linux foram feitos numa sessão separada (GNOME/Wayland).
> Aqui no Windows está o **alvo real**: onde o `.exe` roda e onde o overlay gruda no emulador (Dolphin etc.).

## O que é o projeto

App desktop **pywebview** (Python + HTML/CSS/JS vanilla) que acompanha conquistas do **RetroAchievements** reordenadas pela ordem de um guia, com um **overlay que gruda por cima da janela do emulador**. Uso pessoal, single-user, offline-first.

- Repositório: **WnMoura/digitracker** (privado), branch `main`, base **v0.11.6** com alterações locais descritas acima.
- Backend: `engine.py` (janela + `js_api` + sincronização + estado + overlay). Módulos principais: `ra_api.py`, `gamefaqs.py`, `guide_ai.py`, `guide_parser.py`, `smart_guide.py`, `guide_media.py`, `experience.py`, `experience_api.py`, `companion.py`, `emulator_tracker.py`, `updater.py` e provedores de imagem.
- Frontend: `ui/index.html`, `ui/app.js`, `ui/style.css`, fontes locais (`ui/fonts/`).
- Testes: `tests/` (pytest) — **621 passando** na validação integrada atual
  (os números menores abaixo são registros históricos das releases anteriores).

### Contexto histórico da release v0.10.6

- O Atlas agora processa a fonte em lotes auditáveis, salva checkpoints atômicos e retoma após falhas ou rate limits.
- Gemini é serializado, respeita `Retry-After`/`RetryInfo` e usa fallback automático quando o modelo padrão não está disponível.
- Relações alternativas do Atlas entre os mesmos nós preservam rótulo, tipo de rota e requisitos, sem serem rejeitadas como duplicatas.
- A importação do GameFAQs separa navegação/anúncios do conteúdo e preserva tabelas e paginação.
- PDF, GameFAQs e Atlas exibem estado de processamento e erros classificados; a área de IA mostra telemetria local sem chaves, prompts ou respostas.
- A release histórica usava `0.10.6`; o estado local atual aponta para `0.11.7` em
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

## O que validar no Windows (aplicável à v0.11.8)

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
