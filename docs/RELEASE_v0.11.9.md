# DigiTracker v0.11.9

Esta versão consolida a nova experiência mobile do Companion e a continuação da integração Atlas/Guia.

## Destaques

- Companion mobile redesenhado com Início, Guia, Atlas, Itens e Mais.
- Consulta de jogo independente no celular, sem trocar o jogo ativo no PC.
- Guia mobile com leitura, retomada, conclusão, favoritos, spoilers e pergunta contextual à IA.
- Atlas mobile em fluxo vertical com rotas, alternativas, requisitos, objetivos, itens e referências editoriais.
- Busca global por nome e `#ID`, com retorno ao mesmo conjunto de resultados.
- Itens com estado `null` / `0` / quantidade positiva, detalhe, origem e usos estruturados.
- Fila offline IndexedDB com `request_id` idempotente, controle de versão por alvo e revisão explícita de conflitos.
- Pareamento e aparelhos lembrados com SQLite, restauração de sessão, revogação e suporte opcional a mDNS.
- Polling leve por revisões, atualização seletiva de recursos, backoff e respeito a `Retry-After`.
- Ajustes de responsividade para teclado virtual, safe areas e navegação mobile.
- Melhorias no Atlas desktop, entidades, imagens e reprocessamento de guias.

## Validação

A continuação mobile passou pela suíte completa de testes e por smoke tests em Chromium e WebKit antes do merge para `main`.

Ainda permanecem como validações manuais de ambiente real: WebView2 no executável Windows, mDNS/LAN real, mudança de IP e suspensão/retomada no Safari de iPhone.

## Atualização

O executável desta release é publicado junto com `DigiTracker.exe.sha256` para conferência de integridade.

**Full Changelog:** https://github.com/WnMoura/digitracker/compare/v0.11.8...v0.11.9
