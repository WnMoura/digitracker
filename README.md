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
   lista completa de conquistas e cacheia os ícones. (A API não tem busca por
   nome, então na primeira vez o app monta um índice local dos jogos com
   conquistas — pode levar ~1 min.)
2. **Organizar walkthrough** — crie etapas e mova cada conquista para a etapa
   certa na ordem do seu guia, marcando o modo (**N**ormal / **H**ard) de cada
   uma. "Salvar" grava `config/games/{slug}.json`.

A curadoria da ordem é **manual/curatorial** por design — não há parsing
automático de PDF.

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
