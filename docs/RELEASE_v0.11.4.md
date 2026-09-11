# DigiTracker v0.11.4

## Atlas: requisitos de evolução por item

- Diferencia relações de evolução, requisitos, referências e atributos durante a revisão da fonte.
- Itens, lojas e locais de obtenção deixam de virar cards; quando aplicável, ficam anexados à rota como requisito ou referência.
- Adiciona os modos estruturados `item`, `jogress`/`DigiMemory` e `reincarnation`, preservando texto original, operadores, valores e referências.
- Deduplica pares reversos de Jogress sem perder as referências da fonte e mantém pendências explícitas quando uma regra não pode ser determinada.
- Exibe no Atlas a função de cada tabela e badges para requisitos de item, Jogress/DigiMemory e reencarnação.

## Resiliência e compatibilidade

- Mantém a gravação atômica tolerante a bloqueios transitórios do Windows e o diagnóstico específico para arquivos em uso.
- Incrementa a versão do processamento estruturado para invalidar checkpoints incompatíveis com segurança.
- Preserva sistemas publicados, progresso, imagens e fontes existentes; dados antigos continuam legíveis.

## Verificação

- `\.venv\Scripts\python.exe -m pytest tests -q` — 588 testes passando.
- `\.venv\Scripts\python.exe -m compileall -q .` — concluído.
- `node --check ui/app.js` — concluído.
- `git diff --check` — concluído.
