# DigiTracker v0.11.5

## Atlas estruturado e experiência mobile

- Importa fontes GameFAQs, Wayback e HTML para JSON editorial e Markdown
  derivado, preservando tabelas, células vazias, cabeçalhos, páginas e
  referências estáveis.
- Interpreta relações declarativas localmente, mantendo alternativas,
  pendências e condições por item sem criar criaturas, lojas ou locais como
  cards do Atlas.
- Adiciona catálogo de itens com posse `null`, `0` e valores positivos,
  preenchimento automático de imagens por entidade, IDs numéricos e exclusão
  segura de sistemas.
- Melhora foco/mapa/lista do Atlas e o companion mobile com gravações por alvo,
  idempotência, conflitos explícitos, polling preservado e marcação por toque.

## Verificação

- 599 testes passando localmente.
- Sintaxe Python/JavaScript e `git diff --check` aprovados.
- Build local do `DigiTracker.exe` concluído com PyInstaller.
