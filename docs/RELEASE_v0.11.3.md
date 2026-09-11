# DigiTracker v0.11.3

- Torna a gravação atômica do Atlas tolerante a bloqueios transitórios do
  Windows (WinError 5/32), comuns durante a inspeção do Defender ou indexador.
- Mantém os lotes e checkpoints já salvos para retomada após uma falha de
  armazenamento.
- Exibe um diagnóstico específico quando o arquivo local continua bloqueado,
  em vez de apresentar a falha como erro genérico da IA.

Validação: 583 testes passando localmente; o workflow da release repete os
testes e gera o executável Windows com SHA-256.
