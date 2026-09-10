# DigiTracker v0.10.6

- Corrige a validação do Atlas quando duas rotas documentadas ligam os mesmos nós com rótulos, tipos ou requisitos diferentes.
- Preserva relações alternativas durante a consolidação dos lotes, sem aceitar duplicatas realmente idênticas.
- Adiciona teste de regressão para rotas alternativas e mantém a validação de duplicatas.

Validação: 570 testes passando localmente. O workflow da release repete a suíte e gera o executável Windows com SHA-256.
