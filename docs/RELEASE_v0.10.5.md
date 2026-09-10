# DigiTracker v0.10.5

- Faz o Atlas auditar cada trecho da fonte com identificadores locais confiáveis e extrair todos os caminhos documentados, sem escolher somente uma rota representativa.
- Reduz o tamanho de cada lote e deixa de enviar a lista completa de conquistas em toda chamada do Atlas.
- Salva checkpoints atômicos por lote. Após falha de rede, limite ou indisponibilidade da API, **Tentar novamente** retoma apenas o que falta.
- Mantém o Gemini 3.8 Flash como padrão do Atlas, com raciocínio alto e fallback automático para modelos Flash compatíveis quando o usuário não escolheu um modelo manualmente.
- Serializa os trabalhos do Gemini para evitar rajadas concorrentes e respeita `Retry-After` e `RetryInfo` nas retentativas de 408, 429 e 5xx.
- Melhora a leitura do GameFAQs atual, separando o conteúdo real de navegação e anúncios e preservando tabelas e múltiplas páginas.
- Mostra uma tela de processamento ao ler PDF, baixar GameFAQs, estruturar a fonte e analisar com IA.
- Distingue falha da fonte, conexão, chave/modelo, rate limit, serviço indisponível, resposta incompleta e erro interno, mantendo detalhes técnicos recolhidos.
- Adiciona em **Configurações > Inteligência artificial** os contadores locais de requisições, tentativas, tokens, limites detectados e espera recomendada, sem registrar chaves, prompts ou respostas.

Validação: 569 testes passando localmente, além dos testes de resiliência já presentes na v0.10.4. O workflow da release repete a suíte e gera o executável Windows com SHA-256.
