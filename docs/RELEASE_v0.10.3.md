# DigiTracker v0.10.3

- Reconstrói a extração do Atlas para processar fontes extensas em lotes menores, evitando gerar apenas um caminho representativo.
- Reúne automaticamente nós repetidos e preserva caminhos alternativos com requisitos diferentes.
- Mantém cada linha das tabelas do GameFAQs como uma referência individual e recusa prévias claramente incompletas.
- Mostra, antes da aprovação, a quantidade de nós, caminhos, lotes, páginas citadas e linhas de tabela cobertas.
- Amplia a capacidade de sistemas visuais extensos para até 240 nós e 720 relações, sem cortes silenciosos.
- Exibe o progresso da análise por lote e preserva o Atlas publicado quando a geração falha ou é cancelada.

Para reconstruir um Atlas criado anteriormente, use **Trocar fonte** e importe novamente o guia. Fontes longas usam várias chamadas ao provedor de IA e podem gerar custo.

Validação: 548 testes automatizados aprovados. O guia real de seis páginas informado para teste foi reconhecido com 1.006 blocos úteis e 581 linhas de tabela.
