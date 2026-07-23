# DigiTracker

Dashboard desktop pessoal (janela always-on-top, sem moldura) que acompanha o
progresso das suas conquistas no **RetroAchievements**, mas **reordenadas pela
ordem de um walkthrough/guia** em vez da ordem padrão do site. Funciona como
biblioteca: vários jogos cadastrados, cada um com progresso separado por modo
de dificuldade (**Normal** e **Hard**).

Construído para acompanhar platinas de jogos de Digimon enquanto se cria guias
em PDF — adicionar um jogo novo é feito **inteiramente pela interface**, sem
editar código.

## Stack

- **Python + pywebview** — janela nativa always-on-top com a UI em HTML/CSS/JS.
- **requests** — acesso direto à API REST da RetroAchievements (não há lib oficial).
- Comunicação frontend ↔ backend via `js_api` do pywebview (sem Flask).
- A UI e os assets são servidos por um pequeno servidor estático interno.

## Estrutura

```
digitracker/
├── engine.py            # janela, js_api, sync 30s, cálculo de progresso
├── ra_api.py            # cliente da API da RetroAchievements
├── guide_parser.py      # parsing do PDF do guia (ordem, modos, dicas)
├── requirements.txt
├── config/
│   ├── secrets.json     # username + Web API Key (gitignored)
│   ├── cache/           # índice de jogos p/ a busca do wizard (gitignored)
│   └── games/{slug}.json
├── assets/badges/{slug}/  # cache local dos ícones das conquistas (gitignored)
└── ui/
    ├── index.html
    ├── style.css
    └── app.js
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

## Adicionar um jogo (wizard de 2 passos)

1. **Buscar** — digite o nome; o app consulta a RetroAchievements, baixa a
   lista completa de conquistas, cacheia os ícones das conquistas e o **ícone
   do jogo** (mostrado na lateral e no cabeçalho). (A API não tem busca por
   nome, então na primeira vez o app monta um índice local dos jogos com
   conquistas — pode levar ~1 min.)
2. **Organizar walkthrough** — o passo 2 **já abre pré-ordenado pela ordem
   nativa do RetroAchievements** (campo `DisplayOrder`, que já é uma ordem
   lógica/curatorial), com o modo **Normal/Hard** inferido da descrição de cada
   conquista. Ou seja: **basta clicar em "Salvar"** — sem arrastar nada. Se
   quiser, ainda dá para arrastar para reordenar e trocar os modos (N/H) à mão.
   "Salvar" grava `config/games/{slug}.json`.

   Como **alternativa**, há o botão **"📄 Ordenar pelo PDF do guia"**: escolha o PDF
   do walkthrough e o app faz o **parsing estruturado** do guia
   ([`guide_parser.py`](guide_parser.py)) e automaticamente:
   - **ordena as conquistas** na ordem da seção de conquistas do guia;
   - **sugere o modo (Normal/Hard)** de cada uma a partir das subseções
     (ex.: "Modo Normal" / "Modo Hard" / "Super Hard") e de sufixos no nome
     (ex.: `(Normal)`, `(Hard)`, `(VH)`);
   - **captura as dicas e tutoriais** do PDF (mecânicas, chefes, side quests,
     grinding…) para a aba **Dicas & Tutoriais** (ver abaixo).

   O casamento conquista↔guia é tolerante: nome exato → sem sufixo de
   dificuldade → similaridade (fuzzy) → com os **pontos** como desempate. Ao
   aplicar o PDF, as conquistas reconhecidas vão para o topo na ordem do guia e
   **as demais continuam logo abaixo na ordem do RA — nada é perdido**. A
   leitura usa `pypdf` (só PDFs com texto; digitalizados/imagem não têm).

   > Atenção: alguns guias usam nomes **aproximados/traduzidos** das conquistas,
   > que podem não bater com os títulos oficiais do RA. Por isso o **padrão é a
   > ordem nativa do RetroAchievements**; o PDF é um ajuste opcional (e a maior
   > utilidade dele costuma ser a aba **Dicas & Tutoriais**).

A ordem final continua **curatorial**: você pode revisar, reordenar e ajustar
os modos como quiser.

## Dicas & Tutoriais

Cada jogo tem duas abas no painel: **Walkthrough** (a ordem das conquistas) e
**Dicas & Tutoriais**. A segunda mostra o conteúdo de guia extraído do PDF —
mecânicas básicas, escolha de personagem, estratégias de chefe, side quests,
evoluções, itens e dicas avançadas — formatado em seções, passos, notas e
cartões de chefe. É só preencher uma vez (ao usar "Ordenar pelo PDF"); o guia
fica salvo junto do jogo em `config/games/{slug}.json`.

## Sincronização

Um thread em background consulta a API a cada **30s** e recalcula o progresso de
cada jogo (total combinado + Normal/Hard separados, última conquista obtida e os
próximos alvos na ordem do walkthrough). A interface atualiza sozinha.

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
  `BadgeName`; `DateEarned`/`DateEarnedHardcore` indicam o que está destravado.
- Os modos **Normal/Hard** são curadoria nossa (campo `mode` no JSON do jogo),
  não o softcore/hardcore da RA. A RA só informa quais IDs estão destravados.
- Ícones: `media.retroachievements.org/Badge/{BadgeName}.png`, cacheados em
  `assets/badges/{slug}/`. Destravadas = coloridas; bloqueadas = grayscale.
```
