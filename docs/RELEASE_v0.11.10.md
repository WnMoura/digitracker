# DigiTracker v0.11.10

Esta versão reúne a integração remota mais recente da experiência mobile com as melhorias do Atlas, do Guia e do modo compacto.

## Destaques

- Guia com classificação determinística de missões, filtro dedicado e destaque visual sem inventar missões em texto narrativo.
- Contexto editorial preservado para etapas dependentes, incluindo pré-condições, local, resultado e referências da fonte.
- Atlas e Guia com apresentação mais estável, preservação de seleção/progresso e correções de marcação e imagens.
- Companion mobile com comandos versionados, fila offline, reconciliação e mensagens transitórias que não ficam presas na tela.
- Overlay e modo compacto com rastreamento de emuladores e jogos nativos, mantendo o comportamento de janela normal fora do compacto.
- Melhorias no processamento de fontes, entidades, requisitos e compatibilidade com a integração mobile consolidada na `main`.

## Validação

- Suíte Python: `661 passed`.
- `node --check` aprovado para `ui/app.js`, `ui/companion/app.js` e `ui/overlay.js`.
- `git diff --check` aprovado.

As validações dependentes de ambiente real (WebView2, mDNS/LAN, suspensão/retomada no Safari e comportamento com cada emulador) devem continuar sendo conferidas no equipamento de uso.

## Atualização

O workflow de tag publica `DigiTracker.exe` e `DigiTracker.exe.sha256` para conferência de integridade.

**Full Changelog:** https://github.com/WnMoura/digitracker/compare/v0.11.9...v0.11.10
