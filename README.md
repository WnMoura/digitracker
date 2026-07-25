# DigiTracker

Dashboard desktop pessoal (janela always-on-top, sem moldura) que acompanha o
progresso das suas conquistas no **RetroAchievements**, mas **reordenadas pela
ordem de um walkthrough/guia** em vez da ordem padrão do site. Funciona como
biblioteca: vários jogos cadastrados, cada um com o progresso separado entre
**hardcore** e **softcore** e o quanto falta para o **Mastery**.

Construído para acompanhar platinas de jogos de Digimon enquanto se cria guias
em PDF — adicionar um jogo novo é feito **inteiramente pela interface**, sem
editar código.

## Stack

- **Python + pywebview** — janela nativa always-on-top com a UI em HTML/CSS/JS.
- **requests** — acesso direto à API REST da RetroAchievements (não há lib oficial).
- Comunicação frontend ↔ backend via `js_api` do pywebview (sem Flask).
- A UI e os assets são servidos por um pequeno servidor estático interno.
- Sem CDN: as fontes ficam em `ui/fonts/`, então o app tem a mesma aparência
  offline.

## Estrutura

```
digitracker/
├── engine.py            # janela, js_api, sync 30s, cálculo de progresso
├── ra_api.py            # cliente da API da RetroAchievements
├── gamefaqs.py          # baixa guias do GameFAQs (cloudscraper)
├── guide_ai.py          # refino opcional por IA (Claude, Gemini ou compatível)
├── guide_parser.py      # parsing do guia: PDF estruturado e FAQ de texto livre
├── emulator_tracker.py  # acha a janela do emulador (overlay gruda nela)
├── requirements.txt
├── tests/               # pytest — parser, servidor estático, cliente da API
├── config/
│   ├── secrets.json     # username + Web API Key (gitignored)
│   ├── settings.json    # auto-import + jogos dispensados (gitignored)
│   ├── cache/           # índice de jogos p/ a busca do wizard (gitignored)
│   └── games/{slug}.json
├── assets/badges/{slug}/  # cache local dos ícones das conquistas (gitignored)
└── ui/
    ├── index.html
    ├── style.css
    ├── app.js
    ├── fonts.css        # @font-face das fontes locais
    └── fonts/           # woff2 embutidos (o app não depende do Google Fonts)
```

## Como rodar

> No Linux, o pywebview precisa de um backend GUI. A forma mais simples é
> reaproveitar o GTK3 + WebKit2 do sistema (pacotes `python3-gi` e
> `gir1.2-webkit2-4.1`, já comuns no Ubuntu) criando o venv com
> `--system-site-packages`.

```bash
cd digitracker
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
python engine.py
```

Na primeira execução, informe seu **username** e a **Web API Key**
(retroachievements.org → Settings → Keys). As credenciais ficam só em
`config/secrets.json`, na sua máquina. Há também um botão **Ver demonstração**
para visualizar a interface com dados fictícios, sem credenciais.

## A biblioteca espelha a sua conta

Você **não precisa cadastrar jogo nenhum**. Assim que conecta a conta, o app
importa sozinho todos os jogos em que você já tem alguma conquista, e continua
verificando a cada **5 minutos** — comece um jogo novo no emulador e, no primeiro
achievement, ele aparece sozinho na lateral, como no site da RetroAchievements.

A varredura usa `API_GetUserCompletionProgress` (uma chamada, paginada) e importa
o que ainda não está na biblioteca. Cada jogo entra com as conquistas na ordem
nativa do RetroAchievements — o mesmo resultado de abrir o wizard e clicar em
"Salvar".

- **Apagou um jogo à mão?** Ele vai para a lista de *dispensados* em
  `config/settings.json` e **não volta sozinho**. Sem isso, remover um jogo seria
  inútil: a varredura seguinte o traria de volta.
- **Quer desligar?** Há um interruptor em "⤓ Importar meus jogos" →
  *Importar jogos novos automaticamente*. Desligado, a importação vira manual.
- O botão **"⤓ Importar meus jogos"** continua útil para ver a conta inteira,
  reimportar algo dispensado ou trazer tudo na hora sem esperar os 5 minutos.

A importação roda em background em duas fases: primeiro os jogos (uma consulta
cada) e depois os ícones das conquistas, que são centenas de arquivos pequenos —
os jogos já ficam utilizáveis antes dessa segunda fase terminar.

## Importar o guia direto do GameFAQs

Antes: rodar um scraper à parte → mandar o texto para uma IA → montar um PDF →
importar o PDF. Agora é **colar a URL** e pronto.

No passo 2 do wizard (ou na aba **Dicas** de um jogo salvo), o botão
**"🌐 Importar do GameFAQs"** abre um painel: cole o endereço da aba
*FAQs/Guides* do jogo, escolha um dos guias listados, e o app baixa o texto,
**ordena as conquistas pela ordem em que o guia manda obtê-las** e preenche as
seções de dicas.

O acesso usa `cloudscraper` (resolve o desafio do Cloudflare) com pausas entre
as páginas — listar leva alguns segundos, baixar um guia grande pode levar um
minuto. A ordenação reaproveita o mesmo casamento do fluxo de PDF: nome exato →
sem sufixo → aproximado → posição no texto. O que não casar continua no pool
manual: **nada é perdido**.

> Uso pessoal e em volume baixo. O texto dos guias é de autoria de quem os
> escreveu — fica no seu `config/games/{slug}.json`, não é para redistribuir.

### Refinar com IA (opcional, provedor à sua escolha)

Depois de importar, aparece **"✨ Refinar com IA"**. Ele manda o guia + a lista
de conquistas para o modelo escolhido e recebe de volta uma ordem mais fiel ao
walkthrough e as dicas reorganizadas em seções limpas — o que antes você fazia à
mão, agora dentro do app.

**Você escolhe o provedor** no botão **⚙** ao lado:

| Provedor | Padrão | Observação |
|---|---|---|
| **Anthropic (Claude)** | `claude-opus-4-8` | via SDK oficial |
| **Google (Gemini)** | `gemini-2.5-pro` | via API REST do AI Studio |
| **OpenAI ou compatível** | `gpt-4o` | endpoint configurável — serve para OpenRouter, DeepSeek, **Ollama**, LM Studio… |

O campo *Modelo* aceita qualquer id (`gemini-2.5-flash`, `llama3`, …) e o
*Endpoint* permite apontar para um servidor local. Cada provedor guarda a
**própria chave**, então trocar de um para outro não apaga a anterior.

É **opcional e cobrado pelo provedor** (com Claude, um guia grande sai por menos
de US$ 1; com um modelo local via Ollama, nada). Sem chave, tudo acima continua
funcionando pela heurística. As chaves ficam só em `config/secrets.json`, nesta
máquina — o app nunca as devolve para a interface, só informa se existem.

Independente do provedor, o que a IA responde é tratado como **sugestão, nunca
como verdade**: ela devolve *nomes* de conquista, que passam pelo mesmo
casamento com o set real do RetroAchievements. Se inventar um nome, ele
simplesmente não casa — não há como a IA fabricar um id.

## Adicionar um jogo (wizard de 2 passos)

1. **Buscar** — digite o nome; o app consulta a RetroAchievements, baixa a
   lista completa de conquistas, cacheia os ícones das conquistas e o **ícone
   do jogo** (mostrado na lateral e no cabeçalho). (A API não tem busca por
   nome, então na primeira vez o app monta um índice local dos jogos com
   conquistas — pode levar ~1 min.)
2. **Organizar walkthrough** — o passo 2 **já abre pré-ordenado pela ordem
   nativa do RetroAchievements** (campo `DisplayOrder`, que já é uma ordem
   lógica/curatorial). Ou seja: **basta clicar em "Salvar"** — sem arrastar
   nada. Se quiser, ainda dá para arrastar para reordenar à mão. "Salvar" grava
   `config/games/{slug}.json`. Não há nada de dificuldade para marcar: se uma
   conquista vale Mastery ou não é a RetroAchievements que diz.

   Como **alternativa**, há o botão **"📄 Ordenar pelo PDF do guia"**: escolha o PDF
   do walkthrough e o app faz o **parsing estruturado** do guia
   ([`guide_parser.py`](guide_parser.py)) e automaticamente:
   - **ordena as conquistas** na ordem da seção de conquistas do guia;
   - **captura as dicas e tutoriais** do PDF (mecânicas, chefes, side quests,
     grinding…) para a aba **Dicas & Tutoriais** (ver abaixo).

   O casamento conquista↔guia é tolerante: nome exato → sem sufixo de
   dificuldade → similaridade (fuzzy) → com os **pontos** como desempate. Ao
   aplicar o PDF, as conquistas reconhecidas vão para o topo na ordem do guia e
   **as demais continuam logo abaixo na ordem do RA — nada é perdido**. A
   leitura usa `pypdf` (só PDFs com texto; digitalizados/imagem não têm).

   O parser não é amarrado a um guia específico: o cabeçalho/rodapé repetido em
   cada página é descoberto **por repetição**, e a seção que lista as conquistas
   é identificada **pelo conteúdo** (não se assume que seja a de número 8).

   > Atenção: alguns guias usam nomes **aproximados/traduzidos** das conquistas,
   > que podem não bater com os títulos oficiais do RA. Por isso o **padrão é a
   > ordem nativa do RetroAchievements**; o PDF é um ajuste opcional (e a maior
   > utilidade dele costuma ser a aba **Dicas & Tutoriais**).

A ordem final continua **curatorial**: você pode revisar e reordenar como quiser.

## Hardcore, softcore e Mastery

O RetroAchievements distingue **como** você destravou cada conquista:

- **hardcore** — sem carregar savestate, sem rewind, sem cheat, sem câmera lenta
  (fast-forward é permitido). É o único desbloqueio que conta para o **Mastery**,
  o troféu de 100% com a borda dourada.
- **softcore** — destravada no modo casual.

Isso vem pronto da API (`DateEarnedHardcore` / `DateEarned`) — **não há nada para
marcar à mão**. A aba **⚡ Mastery** mostra o progresso hardcore, quanto falta e,
o mais útil, **quais conquistas você tem só em softcore** e precisaria refazer.
No walkthrough, cada conquista obtida ganha um selo indicando em qual modo caiu.

> Versões antigas do app tinham um eixo próprio de dificuldade (Normal/Hard,
> curadoria manual). Ele foi removido: arquivos de jogo antigos que ainda tenham
> o campo `mode` continuam carregando normalmente — o campo é simplesmente
> ignorado.

## Dicas & Tutoriais

Cada jogo tem três abas no painel: **Walkthrough** (a ordem das conquistas),
**⚡ Mastery** (o recorte hardcore/softcore) e **Dicas & Tutoriais**. A última
mostra o conteúdo de guia extraído do PDF —
mecânicas básicas, escolha de personagem, estratégias de chefe, side quests,
evoluções, itens e dicas avançadas — formatado em seções, passos, notas e
cartões de chefe. É só preencher uma vez (ao usar "Ordenar pelo PDF"); o guia
fica salvo junto do jogo em `config/games/{slug}.json`.

## Sincronização

Um thread em background consulta a API a cada **30s** e recalcula o progresso de
cada jogo (total obtido, hardcore e softcore separados, o que falta para o
Mastery, a última conquista obtida e os próximos alvos na ordem do walkthrough).
A interface atualiza sozinha.

O cliente HTTP repete requisições que falham por motivo transitório (429 e 5xx,
além de quedas de rede) com **backoff exponencial**, respeitando o header
`Retry-After`. Se mesmo assim a RetroAchievements continuar limitando, o ciclo
de sincronização **dobra o próprio intervalo** (até 10 min) e volta ao normal
assim que a API liberar. Quando uma consulta falha, o último estado bom é
mantido — o progresso na tela não "zera" por causa de uma oscilação de rede.

## Configurações

O **⚙** na barra de título abre a tela de Configurações, com quatro seções:
**Conta** (qual conta da RetroAchievements está conectada), **Inteligência
artificial** (provedor, chave, modelo e endpoint), **Biblioteca** (importação
automática) e **Overlay** (comportamento sobre o emulador).

> Antes da v0.3.0 o provedor de IA só era configurável dentro do wizard de
> "Adicionar Jogo" — que a importação automática fez ninguém abrir. Na prática só
> dava para configurar editando código.

## Atalhos de teclado

A faixa no rodapé mostra os atalhos, e todos funcionam:

| Tecla | O que faz |
|---|---|
| `↑` `↓` | Troca de jogo na biblioteca |
| `Tab` | Alterna entre Walkthrough, Mastery e Dicas (`Shift+Tab` volta) |
| `C` | Liga e desliga o modo compacto |
| `Esc` | Fecha um painel, ou volta ao dashboard |

## Overlay que gruda no emulador

Abriu o emulador, o app **vira overlay sozinho e gruda no canto superior-direito
de dentro da janela dele** — e acompanha se você mover ou redimensionar. Fechou o
emulador, ele volta ao tamanho normal, na posição em que estava.

A janela é procurada a cada 2,5s pelo título e pela classe
([`emulator_tracker.py`](emulator_tracker.py)), com dois backends: **ctypes/user32**
no Windows e **`xprop`/`xwininfo`** no Linux. No Windows o always-on-top também é
re-aplicado a cada verificação — sem isso ele se perde quando o jogo rouba o foco,
que é o motivo de o overlay sumir atrás do Dolphin.

Reconhece Dolphin, RetroArch, PCSX2, DuckStation, ePSXe, PPSSPP, mGBA, melonDS,
Cemu, RPCS3, Project64, DeSmuME e outros; a lista pode ser trocada pelo campo
`emulators` em `config/settings.json`. O gerenciador de arquivos **Dolphin do KDE**
é explicitamente excluído, senão o app grudaria nele.

- **Saiu do compacto na mão com o jogo aberto?** O app não insiste — fica quieto
  até esse emulador fechar.
- **Desligar:** interruptor *Grudar no emulador automaticamente*, em
  "⤓ Importar meus jogos".
### Tela cheia exclusiva

Nesse modo o emulador toma a saída de vídeo e o compositor sai do caminho:
**nenhuma janela comum é desenhada por cima**, por mais "sempre visível" que
seja. Steam e Discord só conseguem porque injetam uma DLL dentro do processo do
jogo — fora do escopo aqui.

O app **detecta** a situação (`SHQueryUserNotificationState`, no Windows) e, em
vez de sumir sem explicação, avisa. Nas configurações há dois interruptores,
ambos **desligados por padrão** — o app não mexe no seu jogo sem permissão:

- **Sair do fullscreen exclusivo** — manda `Alt+Enter` para o emulador, uma vez
  por sessão de tela cheia. Nunca em laço.
- **Usar o segundo monitor** — com dois monitores, leva o overlay para a tela que
  o jogo não ocupa. É o único caso em que overlay e tela cheia exclusiva
  convivem de verdade.

Sem nenhum dos dois, use o emulador em **janela ou borderless** (no Dolphin:
*Options → Graphics → desmarcar Fullscreen*).

## Testes

O parser do guia, o servidor estático e o cliente da API são cobertos por testes
sem rede nem interface:

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

## Empacotar (PyInstaller)

Há um `digitracker.spec` pronto que gera um executável standalone (a `ui/` é
embutida; `config/` e `assets/` ficam ao lado do executável). Veja **[BUILD.md](BUILD.md)**
para os passos por SO. Resumo:

```bash
pip install -r requirements.txt -r requirements-build.txt
pyinstaller digitracker.spec --noconfirm   # saída em dist/
```

Lembre: o PyInstaller não faz cross-compile — para gerar `DigiTracker.exe`,
rode o build em uma máquina **Windows**.

## Notas sobre a API da RetroAchievements

- Base: `https://retroachievements.org/API/` — auth pela Web API Key no
  parâmetro `y`; usuário alvo em `u`.
- `API_GetGameInfoAndUserProgress.php?g=&u=&a=1` traz as conquistas com
  `BadgeName`, `Points`, `DisplayOrder` e as datas de desbloqueio.
- `DateEarned` e `DateEarnedHardcore` **só existem se a conquista foi obtida**
  naquele modo — não vêm nulos, simplesmente não aparecem no JSON. Por isso o
  parsing usa `.get()` e considera obtida se qualquer uma das duas existir.
- A resposta também traz `NumAwardedToUser` / `NumAwardedToUserHardcore` e
  `UserCompletion` / `UserCompletionHardcore`, que o app não usa: o progresso é
  recalculado localmente sobre a ordem do walkthrough.
- Ícones: `media.retroachievements.org/Badge/{BadgeName}.png`, cacheados em
  `assets/badges/{slug}/`. Destravadas = coloridas; bloqueadas = grayscale.
