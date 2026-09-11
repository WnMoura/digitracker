# Execução — Celular, Atlas, fontes e itens

Data da rodada: 11/09/2026  
Base de código: `v0.11.4` (`b8cdc31`)  
Estado: implementação local concluída e validada; publicação Git ainda não foi solicitada nesta rodada.

## Entregas verificadas

- Fonte editorial comum `digitracker-source-v1`: HTML seguro, GameFAQs/Wayback, páginas, hierarquia, tabelas, cabeçalhos repetidos/multinível, células vazias, `rowspan`/`colspan`, links, figuras, Markdown derivado e HTML bruto local.
- Captura HTTP valida URL efetiva após redirects, classifica conteúdo insuficiente e não grava shell estático como fonte completa. Captura dinâmica opcional usa Edge/Playwright e mantém descoberta no retorno.
- Atlas usa mapeamento declarativo por tabela e materialização local. Marcadores como `Evolution Item` não viram criaturas nem itens; tabelas de requisito separam item e local, preservam quantidade explícita e anexação por destino.
- Condições preservam texto original, referências estáveis, campo, operador, valor tipado, localização/escopo e avaliação `unknown` quando a fonte não documenta quantidade.
- Cards/nós exibem `card_number` numérico estável para revisão, busca, inspector e companion. Pendências registram tabela, linha, prévia, referência, cards afetados, ação sugerida e severidade.
- Catálogo de itens separado do Atlas mantém `null` (não informado), `0` (não possuo) e valores positivos; gravações por celular usam valor absoluto, revisão e `request_id` idempotente.
- Companion v2 mantém pareamento/guards, endpoints de leitura detalhada, conflitos por alvo, timeout/ACK sem falso sucesso e polling que preserva detalhe/foco/rascunho. O celular possui abas Guia, Atlas, Itens e Conquistas.
- Atlas desktop possui foco/mapa/lista, layout delimitado, conexões atrás dos cards, IDs, inspector, revisão de fonte, exclusão de sistema e preenchimento automático de imagens por nome da entidade com cancelamento/desfazer.

## Verificações executadas

```powershell
.\.venv\Scripts\python.exe -m py_compile source_import.py guide_ai.py source_document.py gamefaqs.py atlas_model.py smart_guide.py companion.py engine.py experience_api.py
node --check ui/app.js
node --check ui/companion/app.js
git diff --check
.\.venv\Scripts\python.exe -m pytest -q
```

Resultado: **599 testes passaram**; os checks de sintaxe JavaScript/Python e whitespace também passaram.

Build Windows local concluído com PyInstaller: `dist/DigiTracker.exe` foi
reconstruído a partir deste estado do código. O processo emitiu apenas avisos
de dependências opcionais ausentes (`pycparser`/`tzdata`) e não falhou.

## Limites conhecidos

- A captura genérica de outro site ainda captura uma página por chamada; a lista de descoberta é retornada para seleção/retomada. O adapter GameFAQs continua responsável pela paginação conhecida.
- Playwright/Edge é opcional no ambiente de desenvolvimento e precisa ser instalado no ambiente de build para incluir a integração; o navegador Edge do usuário continua sendo requisito para páginas dinâmicas.
- Teste visual físico em WebView2, Android/iOS e smoke com uma chave real de provedor não foi executado nesta rodada. Capturas sintéticas versionadas permanecem em `docs/screenshots/`.
- Nenhum commit, push ou tag foi criado nesta rodada.

## Próximo passo exato

Abrir `dist/DigiTracker.exe` no Windows e validar no WebView2: importação de um
FAQ GameFAQs e de um URL Wayback, revisão/pendências, item `null/0/positivo`,
toque de marcação no celular e preenchimento/desfazer de imagens. Só depois
solicitar explicitamente commit, push e tag.
