# DigiTracker v0.10 — validação local

Build de teste: `dist-check/v010-experience/DigiTracker.exe`.

Esta build está em pasta separada. Não substitui o executável instalado e não copia credenciais ou guias pessoais automaticamente. Faça backup antes de testar com dados existentes. Nenhuma release foi publicada nesta etapa.

## Verificações realizadas

- Suíte automatizada: **533 testes aprovados**.
- Build PyInstaller concluída no Windows.
- Verificação de sintaxe JavaScript e de espaços inválidos no diff.
- Navegação e capturas do HTML real do aplicativo em navegador, com dados demonstrativos. Essas capturas não são mockups, mas também não comprovam funcionamento nativo, login real ou sincronização da conta.
- Testes isolados de pareamento, autorização, revogação, referências do Atlas, publicação de revisões, isolamento de conta, backup e validação de imagens.

## 1. Abertura e regressões críticas

- [ ] Abrir o executável da pasta de teste; conferir a versão em Configurações.
- [ ] Clicar em campos, botões, menus e modais; confirmar que a janela principal nunca fica passa-clique.
- [ ] Conectar a conta e verificar biblioteca, guias, fontes, imagens e progresso.
- [ ] Sair e reabrir; confirmar persistência sem apagar a fonte original.
- [ ] Testar teclado, mouse e controles Xbox/PlayStation. Verificar Esc, Enter, setas e LB/RB onde indicados.
- [ ] Testar 1600×900, 1280×720 e janela estreita, com DPI 100%, 125% e 150%.
- [ ] Conferir redução de movimento, escala, foco visível e ausência de botões cortados.

## 2. Atlas

- [ ] Abrir Atlas vazio e usar “Novo sistema”; conferir que o diálogo fica visível.
- [ ] Importar PDF textual e GameFAQs como fonte exclusiva. Conferir nome e origem.
- [ ] Testar sem IA, sem consentimento, com provedor válido e com erro de rede.
- [ ] Acompanhar processamento, cancelar e repetir. Uma tarefa antiga não deve substituir a nova.
- [ ] Conferir a prévia, editar nomes/requisitos, aprovar ou rejeitar. A versão ativa só muda após aprovação.
- [ ] Verificar referências de nós, caminhos e requisitos na fonte; não aceitar relações inventadas.
- [ ] Testar filtros, spoilers, zoom, arraste do mapa, ajustar à tela e visualização em lista.
- [ ] Fixar objetivo e escolher caminhos alternativos. Requisitos de alternativas não devem ser somados como obrigatórios.
- [ ] Adicionar/substituir imagem aprovada; simular falha e confirmar preservação da anterior.
- [ ] Restaurar revisão e conferir objetivo, imagens e requisitos dos nós ainda existentes.
- [ ] Consolidar a Jornada e confirmar que o Atlas dedicado permanece intacto.

## 3. Jornada, biblioteca e busca

- [ ] Usar Minha Jornada, Biblioteca, Hall, Atividade e voltar ao jogo.
- [ ] Conferir favoritos no topo e histórico de jogos abertos.
- [ ] Alterar estados Quero jogar/Jogando/Pausado e testar filtros.
- [ ] Conferir distinção Hardcore/Softcore, contagens e pontos; Mastery fica no Hall.
- [ ] Usar Ctrl+K para jogo, conquista, texto do guia e anotação; conferir destino do resultado.
- [ ] Testar acentos, consulta vazia, sem resultados e termos com pontuação.
- [ ] Iniciar, pausar, retomar e encerrar sessão. Fechar o app não deve contar tempo offline.
- [ ] Salvar metas e desafio semanal; conferir calendário e raridade quando houver dados reais.
- [ ] Conferir perdíveis oficiais pendentes, incluindo “Refazer em Hardcore” para Softcore.

## 4. Notificações e overlays

- [ ] Usar o teste de notificação em Atividade e verificar posição escolhida.
- [ ] Obter uma conquista real; conferir imagem, nome, pontos, raridade e modo.
- [ ] Sincronizar novamente: o mesmo evento não deve produzir popup duplicado.
- [ ] Primeira importação da conta não deve disparar todas as conquistas antigas.
- [ ] Testar som, modo silencioso, histórico e replay da Mastery.
- [ ] Testar os HUDs Resumo e Detalhes isolados e simultâneos.
- [ ] Confirmar hotkeys, edição/arraste, redimensionamento e retorno ao dashboard.
- [ ] Mover, minimizar, restaurar e fechar cada emulador usado; testar segundo monitor e escalas distintas.
- [ ] Conferir teclado, mouse e controle chegando ao jogo, inclusive durante o popup.
- [ ] Testar janela normal e borderless. Fullscreen exclusivo exige validação específica e pode impedir sobreposição.

## 5. Arte e personalização

- [ ] Pesquisar arte e abrir comparação: a imagem ainda não deve ter sido aplicada.
- [ ] Recortar com mouse e campos numéricos; testar proporções de capa e fundo.
- [ ] Cancelar e conferir a arte anterior; aplicar e depois usar Desfazer.
- [ ] Alterar paleta e desfazer; reiniciar e conferir persistência.
- [ ] Testar URL inválida, HTML, imagem corrompida e download bloqueado sem perder arte anterior.
- [ ] Conferir tema opcional por plataforma e modo streamer antes de transmitir a tela.
- [ ] Revisar telas de conta, notificações, resultados e diagnósticos: não transmitir dados pessoais que ainda estejam visíveis. O modo streamer não substitui essa conferência.

## 6. Companion no celular

- [ ] Colocar PC e celular na mesma rede privada confiável; iniciar manualmente em Celular.
- [ ] Escolher a interface local correta, ler QR e aprovar o dispositivo no PC.
- [ ] Confirmar que dispositivo não aprovado não acessa conteúdo.
- [ ] Testar QR expirado, código reutilizado, revogação e encerramento do servidor.
- [ ] Ver objetivo, navegar capítulos, marcar etapas e avançar/voltar.
- [ ] Consultar Atlas, imagens, requisitos e próximas conquistas.
- [ ] Testar spoilers e confirmar que notas privadas/fontes completas não são enviadas.
- [ ] Alterar a mesma etapa no PC e no celular: conflito deve pedir atualização, não sobrescrever silenciosamente.
- [ ] Fazer pergunta contextual à IA com consentimento; testar falha sem perda do guia.
- [ ] Desconectar o Wi-Fi e reconectar; conferir aviso e ausência de falsa confirmação de salvamento.
- [ ] Trocar conta, parar servidor e fechar aplicativo; conferir invalidação do acesso.

O companion usa HTTP local, não uma conexão cifrada. Não exponha a porta na internet, não configure redirecionamento de portas e não o use em Wi-Fi público. Firewall, roteamento e aparelhos físicos ainda precisam ser validados manualmente.

## 7. Dados e diagnóstico

- [ ] Exportar diagnóstico e confirmar ausência de chaves, usuário e texto privado.
- [ ] Criar backup comum; conferir que credenciais não estão incluídas.
- [ ] Criar backup com credenciais, senha de pelo menos 12 caracteres e abrir com ferramenta compatível com ZIP AES.
- [ ] Conferir rejeição com senha incorreta; guardar a senha fora do aplicativo.
- [ ] Conferir conteúdo do backup em pasta isolada antes de depender dele para recuperação.
- [ ] Exportar biblioteca, importar a prévia e verificar duplicados antes de confirmar.
- [ ] Testar arquivo inválido e confirmar que guias e configurações existentes não foram substituídos.

## Limites desta validação

Ainda não foram comprovados em hardware real: controle/gamepad, emuladores, fullscreen, múltiplos monitores, DPI misto, notificações nativas durante gameplay, pareamento em celular físico e chamadas pagas a provedores de IA. As estatísticas de sessão dependem do registro local e dos dados disponíveis; não representam leitura de saves nem uma medição histórica exata do tempo de platina.

As capturas em `docs/screenshots/v010-experience/` usam exemplos demonstrativos, sem credenciais. A aprovação desta checklist deve anteceder a publicação de uma release.
