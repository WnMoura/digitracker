# DigiTracker v0.11.2

- Aceita guias do GameFAQs capturados pelo Web Archive, validando o alvo
  original e preservando a data, a URL arquivada e a paginação.
- Reutiliza o mesmo JSON estruturado, Markdown de revisão e referências de
  tabelas do importador direto do GameFAQs.
- Identifica a fonte arquivada na revisão e no painel de trechos do Atlas.
- Adiciona regressões para captura de duas páginas, títulos arquivados e
  rejeição de capturas que apontem para outro site.

Validação: 581 testes passando localmente; o workflow da release repete os
testes e gera o executável Windows com SHA-256.
