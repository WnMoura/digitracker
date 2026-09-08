# DigiTracker v0.10.4

- Torna as chamadas REST de IA resilientes a falhas transitórias de rede, 429 e 5xx com exponential backoff, jitter e suporte a `Retry-After`.
- Evita falha imediata do Atlas em respostas `503 UNAVAILABLE` do Gemini e apresenta mensagens de erro mais claras após esgotar as novas tentativas.
- Atualiza o Gemini padrão para `gemini-3.8-flash`; no Atlas, quando o usuário não escolhe manualmente um modelo, habilita raciocínio alto e fallback para `gemini-3.7-flash` e `gemini-3.6-flash` em indisponibilidade transitória.
- Corrige a validação de cobertura do Atlas para não contar cabeçalhos e separadores Markdown como linhas reais de dados.
- Normaliza referências `section`/`block` quando um modelo renumera posições relativamente ao lote, reduzindo falsos erros de cobertura como `0 de 4`.
- Reforça o prompt do Atlas para preservar os índices absolutos da fonte e registra o modelo efetivamente utilizado na análise.
- Adiciona testes de regressão para retry 503, fallback de modelo, cobertura de tabelas e normalização de referências.

Esta versão mantém a escolha manual de modelo: fallback automático só é usado quando o campo **Modelo** está vazio.
