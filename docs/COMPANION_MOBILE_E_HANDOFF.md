# DigiTracker Celular — entrega da proposta E

Este documento registra o estado implementado para a proposta E (Jornada +
consulta). Ele é o roteiro de validação do executável e não substitui a
revisão manual em WebView2, Safari ou em um aparelho físico.

## Entregue

- Navegação celular com Início, Guia, Atlas, Itens e Mais.
- Guia em índice/leitura/favoritos, retomada, concluir/desmarcar, favorito,
  spoiler e pergunta contextual à IA.
- Atlas celular em Sistemas → Entidades → Rota, com origem, entidade e
  destino verticais, alternativas recolhidas, requisitos por rota, item do
  jogo, referências e objetivo.
- Busca global por nome ou `#ID`, sem diferenciar caixa ou acentuação; limpar a
  busca restaura a tela inicial de resultados.
- Estado de apresentação separado do transporte: uma atualização não fecha a
  leitura, a rota, o inspetor ou um formulário em edição.
- Fila IndexedDB por instalação, conta e jogo, com operações absolutas,
  `request_id`, reenvio idempotente e revisão explícita de conflitos.
- Protocolo v2 para etapas, favoritos, retomada, requisitos, objetivos e
  quantidades de itens. `null`, zero e quantidade positiva continuam distintos.
- Requisitos agora usam a chave `requirement:<sistema>:<caminho>:<requisito>`.
  A lista legada `completed_requirements` continua sendo escrita para clientes
  antigos, mas a interface usa a lista escopada assim que a primeira operação
  v2 ocorre. Assim, marcar Airdramon não marca Growlmon quando ambos reutilizam
  o mesmo identificador de condição.
- Companion com cadastro SQLite de aparelhos lembrados, restauração de sessão,
  revogação, porta preferida `47831`, opção de inicialização automática e
  anúncio mDNS opcional quando `zeroconf` estiver disponível.
- Demo local sem gravações reais, adequada para testar os controles quando o
  PC não estiver pareado.

## Continuação da etapa 8 — integração

Após o checkpoint `wip/mobile-companion-e-local`, a continuação foi aberta em
`feat/mobile-step8-integration` sem alterar a `main`.

A auditoria encontrou um caso de concorrência importante na fila offline: ao
reconectar, o cliente recalculava `expected_value_version` com o valor mais
novo recebido do PC. Isso poderia transformar uma intenção antiga em uma
escrita atual e impedir o `409` que deveria pedir revisão ao usuário. O estado
móvel agora captura a versão observada quando a operação nasce e reutiliza essa
mesma versão em reenvios. O mesmo `request_id` continua sendo seguro no caso de
ACK perdido porque o backend verifica o recibo idempotente antes do conflito.

Também foram endurecidos dois detalhes do contrato móvel:

- o fallback de `request_id` usa `crypto.getRandomValues` e UUID v4 quando
  `crypto.randomUUID` não está disponível em uma página HTTP da LAN; não usa
  `Math.random`;
- a projeção legada `completed_requirements` permanece marcada enquanto outra
  rota escopada com o mesmo `requirement_id` ainda estiver concluída, espelhando
  o comportamento persistido pelo `SmartGuideStore`.

`tests/companion_ui_smoke.cjs` recebeu regressões para esses três casos. Essas
alterações ainda precisam passar novamente pelo smoke com Edge, pela suíte
completa e pelo build Windows antes de serem consideradas validadas para
publicação.

## Verificações executadas no checkpoint da proposta E

Na pasta do projeto:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Resultado do checkpoint anterior: **642 testes aprovados**.

```powershell
$env:NODE_PATH='C:\Users\wanso\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$node='C:\Users\wanso\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $node --check ui/companion/app.js
& $node --check ui/companion/state.js
& $node --check ui/companion/storage.js
& $node --check ui/atlas-view-model.js
& $node --check ui/atlas-layout.js
& $node --check ui/experience.js
& $node tests/companion_ui_smoke.cjs
& $node tests/atlas_cd_fixture_smoke.cjs
```

Os dois smoke tests do checkpoint passaram em 360, 390, 430 e 820 px,
cobrindo Guia, Atlas, alternativas, transformação por item, busca, marcações,
imagens e conflitos do caso composto. Os testes usam dados sintéticos; não
substituem a validação de rede, Gemini ou WebView2.

## Build

Recriar o executável com:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller digitracker.spec --noconfirm
```

O resultado é `dist\DigiTracker.exe`. O build não publica release nem altera o
executável de Downloads. Antes de uma publicação, abrir o executável em uma
máquina Windows com WebView2 e conferir pareamento, restauração de aparelho,
marcação de etapa/requisito, fila offline e preenchimento de imagens.

## Regressão obrigatória do Atlas

Para um guia já publicado, usar **Fontes e manutenção → Reprocessar fonte
salva**, revisar a prévia e aprovar somente depois de conferir os cards
compostos. O caso `#104` deve reutilizar as quatro rotas individuais para
MegaSeadramon, remover o caminho composto redundante, preservar imagens e
objetivos compatíveis e apresentar qualquer marcação ambígua para decisão
explícita. O conteúdo publicado anterior continua recuperável pela revisão.

## Limitações honestas

Downloads reais de fontes externas, processamento pago com Gemini, mDNS em
uma LAN real e suspensão/retomada no Safari não foram declarados como
validados por estes testes automatizados. A captura e o conteúdo privados do
PC não são enviados ao demo nem ao repositório.
