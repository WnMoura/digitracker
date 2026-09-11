# Plano de implementação — Celular, Atlas, fontes e itens

Data: 11/09/2026. Base inspecionada: commit b8cdc31, versão 0.11.4.

Especificação de implementação para o GPT-5.6 Luna. A execução desta rodada
está registrada em
[`EXECUCAO_CELULAR_ATLAS_FONTES_ITENS.md`](EXECUCAO_CELULAR_ATLAS_FONTES_ITENS.md);
itens ainda não validados fisicamente no Windows permanecem explicitamente
marcados na seção de limites.

## 1. Decisões de produto

- Celular é uma segunda tela de consulta e acompanhamento: pesquisa, detalhes, fontes, objetivos e marcação de progresso. Importação, edição estrutural, publicação e resolução de conflitos ficam no PC.
- Compacto mantém janelas, dimensões, docking e atalhos. Compartilha objetivos e progresso com desktop/celular, mas tem apresentação própria.
- Atlas móvel terá cards, detalhes, foco e **mapa completo com zoom e arraste**, conforme escolha do usuário.
- Itens terá catálogo e posse/quantidade manual. Não inclui leitura de memória, inventário automático, slots de equipamento ou consumo automático.
- Fontes atendem Guia/Jornada, Atlas e Itens: GameFAQs, GameFAQs arquivado, HTML genérico, JavaScript, carregamento tardio, rolagem e “carregar mais”, além de arquivos/texto.
- Ao informar uma URL, o app descobre páginas relacionadas e permite selecionar o conjunto antes da captura completa. Não percorre indiscriminadamente um domínio.
- Divergências entre fontes são comparadas para revisão; nenhuma fonte vence silenciosamente.
- Atlas ganha **Preencher imagens**, buscando exclusivamente o nome da entidade: `Agumon`. Não acrescentar jogo, plataforma, `artwork` ou `icon png`.
- Atlas desktop segue a referência visual: cards verticais ilustrados, barra compacta, conexões legíveis e inspector lateral de rota.
- Corrigir explicitamente extração de guias e marcar/desmarcar pelo celular; são entregas obrigatórias, não efeitos colaterais do redesign.

Padrões: português brasileiro, PC como autoridade dos dados, imagens locais, sem localStorage para progresso, sem novo framework de UI ou serviço em nuvem. O PC precisa estar ligado e acessível na LAN. Sem conexão, celular mantém a tela em memória para leitura e suspende gravações.

## 2. Diagnóstico confirmado

### Atlas

A prévia histórica analisada tinha 138 cards, 235 caminhos e 28 pendências: 13 linhas de múltiplas origens, 2 placeholders, 3 evoluções por item, 4 combinações de Jogress e 6 instruções de reencarnação. O checkpoint mais recente era outra execução parcial. Não apresentar os 28 erros antigos como resultado novo da 0.11.4.

Reproduções em memória na base atual:

1. Rotas com `>=25` e `<=25` podem ser consolidadas porque a identidade textual remove operadores.
2. Um requisito do destino é copiado para todas as entradas, embora possa valer apenas para uma origem.
3. Três origens na mesma célula podem virar um card com os nomes concatenados.
4. Uma frase documentando origem/item/destino não cria rota sem outra tabela redundante.
5. `at least`, `15+`, alternativas e cotas não possuem interpretação consistente; “or” em “25 or more” pode virar alternativa.
6. Jogress perde os papéis dos participantes; ordenar nomes presume simetria indevida.
7. Coluna Item pode ser proibida como nó até em um Atlas de fabricação.
8. Frontend usa `every()` e backend conta checkboxes, sem implementar cotas, alternativas, sequência e desconhecido.

Entradas relevantes: `guide_ai._structured_table_semantic_role`, `_structured_requirement_row`, `_materialize_structured_atlas`, `_atlas_identity`; `smart_guide._validate_systems`, `system_objective`; `ui/app.js::guideSystemsHTML`.

### Guias

`engine.Api.add_walkthrough_gamefaqs` recebe a captura, mas envia apenas texto a `guide_parser.parse_freeform`. A Jornada perde a estrutura editorial já disponível ao Atlas. A consolidação tem chamadas finais grandes e analisa trechos iniciais limitados para conflitos; isso não comprova cobertura integral.

O Título do diálogo web não é enviado ao backend. O progresso se limita ao botão “Importando…”. Corrigir ambos.

A imagem do diálogo não contém exceção ou resultado da importação. Identifica o fluxo a corrigir, mas não demonstra um erro específico de rede/provedor.

O FAQ mostrado pelo usuário é um walkthrough de duas páginas, com seções e mapas; deve validar hierarquia, paginação e mídia. A fonte também declara limitações próprias: não completar fatos ausentes por invenção. [Walkthrough de Saphonax, FAQ 74471](https://gamefaqs.gamespot.com/psp/637157-digimon-world-redigitize/faqs/74471).

### Celular e imagens

`ui/companion/app.js` desabilita todos os controles durante gravação e reconstrói `content.innerHTML` depois. Polling pode fechar detalhes e perder foco. INPUT/SELECT/TEXTAREA focados suspendem atualização, inclusive checkbox depois do toque. Faltam timeout e ordenação de leituras.

`_companion_snapshot` usa versão global que não cobre todas as informações exibidas; `_companion_command` devolve somente ok, sem valor persistido. É preciso diferenciar conflito de gravações e revisão de conteúdo.

Conclusão efetiva do guia une progresso manual e RetroAchievements. Desmarcar manualmente uma conquista já obtida não remove a conquista externa; a interface precisa comunicar essa origem.

Na análise original, os 23 testes de experiência/API passaram. Eles não
reproduziam os gestos e corridas de rede relatados; portanto, não se deve
considerar demonstrada uma causa única no aparelho apenas por essa amostra.

A busca de imagens acrescenta contexto em três locais: `bindGuideAtlas`, `search_guide_system_media` e `_web_art_query`. Corrigir o fluxo de entidades nas três camadas, preservando a busca de capas/fundos.

O mapa pode reduzir os cards a 10% e usa handlers de mouse globais. O overlay nativo `ui/overlay.js::objective` já considera Atlas; o fallback `compactObjective` não usa a mesma prioridade. Ambos devem consumir a nova projeção compartilhada.

## 3. Organização de código

Manter Python 3.12, JSON/TypedDict com validadores explícitos e JavaScript sem framework. Não adicionar ORM, React ou Pydantic.

| Módulo | Responsabilidade |
|---|---|
| `source_document.py` — novo | Tipos, parser editorial, validação de referências e Markdown derivado. |
| `source_store.py` — novo | Fontes por jogo, capturas imutáveis, seleções, jobs e checkpoints. |
| `source_import.py` — novo | Descoberta/captura, transporte HTTP, adapters e progresso. |
| `source_capture_browser.py` — novo | Navegador isolado, esperas, rolagem e acumulação dinâmica. |
| `atlas_model.py` — novo | Entidades, regras, condições, avaliação e projeção do grafo. |
| `atlas_extract.py` — novo | Mapeamento declarativo e montagem local da fonte comum. |
| `item_catalog.py` — novo | Catálogo, revisões, obtenções e quantidade manual. |
| `companion_presenter.py` — novo | Projeções públicas e revisões de leitura. |
| `atlas_images.py` — novo | Preenchimento em lote, candidatos, cancelamento e desfazer. |
| `gamefaqs.py` | Adapter GameFAQs/Wayback; delegar parsing comum. |
| `guide_ai.py` | Provedores/retries e guias; delegar novo extrator Atlas. |
| `smart_guide.py` | Publicação, revisão, migração e progresso, usando novos contratos. |
| `engine.py` / `experience_api.py` | Bridge e serviços compartilhados; sem parsing no bridge. |
| `ui/shared/atlas-graph.js` e CSS — novos | Geometria e interação desktop/celular. |
| `ui/shared/requirements.js` — novo | Renderizar avaliação do backend, sem duplicar lógica. |
| `ui/source-review.js` — novo | Descoberta, seleção, cobertura e correções no PC. |
| `ui/companion/state.js` / `views.js` — novos | Separar transporte/estado e telas; app.js inicializa. |

Usar namespaces explícitos nos scripts compartilhados e carregá-los antes dos consumidores, compatíveis com os scripts atuais. Companion serve somente caminhos permitidos desses recursos/fontes. Não expor a pasta inteira do app.

Wrappers antigos do bridge continuam compatíveis; a UI nova usa as APIs assíncronas. Não mudar silenciosamente retorno de callers legados.

## 4. Fonte comum e persistência

### Contrato editorial

Formato `digitracker-source-v1`, schema_version 1. Aceitar `gamefaqs-json-v1` por adapter de leitura.

Campos: source_id, capture_id, format, schema_version, parser_version, title, kind, adapter, requested_url, canonical_url, captured_at, edition, pages, stats e completeness.

- source_id: identidade lógica local, não derivada de título.
- capture_id: hash do conteúdo editorial normalizado, URLs e parser; excluir horário. Capturas existentes são imutáveis.
- edition: texto declarado, plataforma/versão e confirmação. edition_key separa edições; não fundir automaticamente.
- Página: ID, URL solicitada/final, ordem, hash, âncoras e elements ordenados.
- Elementos: heading, paragraph, list, table, definition_list, figure, note, preformatted, com hierarquia.
- Tabela: cabeçalhos por nível, células físicas com spans, matriz que referencia essas células. Ausente, vazio, hífen e zero são distintos.
- Referência: source_id, capture_id, page_id, element_id e table_id/row_id/cell_id quando aplicáveis; aceitar section/block/page legados.

IDs editoriais são estáveis dentro da captura, não entre versões do site. Validar contra a captura, sem ajustar referências por mera soma de índices.

Extrair a base de `_table_grid`/`parse_faq_document`. Corrigir TRs de tabelas aninhadas, cabeçalhos múltiplos/repetidos, listas nas células, spans, legendas e notas. Preservar espaçamento de preformatted.

JSON é autoridade. Markdown é derivado, escapa pipes, preserva quebras e identifica spans; não reconstruir dados a partir dele.

Revisão: árvore, tabelas renderizadas, elementos/linhas selecionáveis, contagens por página, edição e completude. Contexto ancestral e notas necessárias acompanham a seleção, com indicação visível.

### Armazenamento e compartilhamento

Raiz: `config/guides/<slug>/sources/`. Conter index.json, <source_id>/metadata.json, captures/<capture_id>/document.json, document.md e raw/. Jobs têm state.json e checkpoints próprios.

Guia, Atlas e Itens fixam source/capture e seleção própria. A captura não tem um único system_id/status de consumidor. Uma captura alimenta três destinos sem três downloads.

Usar gravação atômica e tolerância Windows existentes. IDs/caminhos são criados pelo backend. Worker só conclui se job_id, geração e fingerprint ainda corresponderem.

Arquivar fonte utilizada conserva referências e lista dependências. Exportação compartilhável não inclui HTML bruto por rglob indiscriminado. Backup local inclui capturas e manifestos; ajustar export_pack/import_pack/write_backup e testar restauração. Perfis de navegador, cookies e credenciais ficam fora.

## 5. Captura ampla da web

### Descoberta

Adapter conhecido ou HTML genérico procurando main/article/role=main e regiões editoriais. Se houver candidatos ambíguos, mostrar trechos para seleção.

Descobrir índice, paginação, categoria/seção e links relacionados. Inicialmente um nível e até 500 candidatos pesquisáveis; “Descobrir mais” amplia explicitamente. Outros domínios não entram automaticamente. O usuário confirma páginas e ordem antes da captura completa.

Wayback preserva URL original e timestamp/URL arquivada efetivamente obtidos por página. Não trocar pelo site atual silenciosamente.

### HTTP e navegador

Modo auto tenta HTTP e avalia conteúdo; shell vazio/evidência dinâmica usa navegador. “Capturar no navegador” também está disponível manualmente, pois algum texto não comprova completude.

Adicionar `playwright==1.62.0`. Usar canal Microsoft Edge `msedge` instalado, em contexto isolado sem perfil pessoal. Empacotar driver Python/Node no PyInstaller; não depender do Python/Node da máquina de desenvolvimento. Não carregar sites na janela com bridge do DigiTracker. [Canais](https://playwright.dev/python/docs/browsers#google-chrome--microsoft-edge), [PyInstaller](https://playwright.dev/python/docs/library#pyinstaller), [pacote](https://pypi.org/project/playwright/).

Não embutir segundo navegador nesta entrega. Se Edge estiver ausente, informar “Captura dinâmica requer Microsoft Edge”, manter outras importações utilizáveis e permitir repetir após instalação. Não declarar sucesso com uma captura estática insuficiente.

Worker dedicado possui o ciclo de vida Playwright. Não compartilhar Page/Context entre threads; cancelar fecha somente recursos do job.

### Espera, rolagem e carregamento progressivo

1. Navegar até domcontentloaded e localizar conteúdo.
2. Observar alterações editoriais, quantidade/hash de linhas e altura. Não depender exclusivamente de networkidle. [Navegação](https://playwright.dev/python/docs/api/class-page#page-goto).
3. Esperar conteúdo inicial até 30 s. Rolar 80% da área visível por ciclo, inclusive contêiner interno; esperar estabilização editorial.
4. Acionar paginação/carregar mais identificado por adapter ou confirmado pelo usuário. Não clicar botões arbitrários nem enviar formulários.
5. Acumular conteúdo a cada ciclo: listas virtualizadas removem nós anteriores, tornando insuficiente salvar só o DOM final.
6. Deduplicar por chave editorial/URL/posição do site. Sem chave, usar contexto/sobreposição entre janelas consecutivas; não apagar repetições legítimas globalmente por igualdade de texto.
7. Encerrar por fim explícito ou fim com três ciclos sem conteúdo novo. Registrar termination_reason e base da completude: estabilização é indício, não total comprovado.

Padrões ajustáveis: 30 páginas/job, máximo 200; 180 s/página dinâmica, máximo 600; 60 ciclos, máximo 300; 15 MiB/documento e 150 MiB de conteúdo salvo/job. Uma página ativa por host, intervalo mínimo de 1 s entre páginas; respeitar Retry-After. Limite atingido salva parcial e oferece ampliar/retomar.

“Abrir captura assistida” permite ao usuário resolver consentimento/login, selecionar região e clicar Capturar agora no contexto isolado. Não registrar credenciais/cookies. CAPTCHA/autorização não resolvida gera needs_user_action/blocked; não prometer captura universal nem automatizar contorno desses controles.

### Proteções e estados

Aceitar HTTP/HTTPS com destinos públicos; validar conexão, redirects e requests do navegador, impedindo loopback/rede privada/metadados/arquivos. Testes locais usam transport injetado, não liberação de produção.

HTML é dado não confiável. A UI renderiza conteúdo normalizado/escapado; nunca executar HTML bruto ou código devolvido pela IA.

Fases: discovering, awaiting_page_selection, capturing, needs_user_action, awaiting_source_review, extracting, awaiting_result_review, completed, partial, failed, cancelled.

Erro tem código, fase, página/elemento, ação sugerida e retomada. Diferenciar rede, limite, navegador ausente, conteúdo insuficiente, armazenamento, IA e validação. Fechar modal não cancela job; Cancelar é explícito.

## 6. Atlas genérico: entidade, regra e condição

### Contrato canônico

Incrementar smart_guide.SCHEMA_VERSION e ATLAS_EXTRACTION_VERSION de 3 para 4. Contratos novos de fonte/catálogo começam em 1.

Entidade: entity_id, edition_key, type, name, aliases, attributes, source_refs. Tipos base: creature, item, skill, quest, location, state, other. Atributos são fatos nomeados com valor e referência; não colunas fixas de Digimon.

Cards mantêm id/card_number e recebem entity_id. Números seguem ordem da revisão/exibição, não são chaves de progresso. Renomear, reordenar ou trocar imagem não altera identidade.

Formato da regra:

~~~json
{
  "id": "rule_local_id",
  "kind": "transformation",
  "label": "Evolução por item",
  "inputs": [{"entity_id": "entity_a", "role": "subject", "quantity": 1}],
  "outputs": [{"entity_id": "entity_b", "role": "result", "quantity": 1}],
  "condition": {
    "id": "condition_root",
    "op": "all",
    "children": [
      {"id": "have_item", "op": "item", "item_id": "item_stone", "action": "possess", "quantity": 1, "source_refs": []},
      {"id": "use_item", "op": "item", "item_id": "item_stone", "action": "use", "subject_id": "entity_a", "quantity": 1, "source_refs": []}
    ]
  },
  "source_refs": []
}
~~~

O exemplo define a forma, não fatos a acrescentar ao jogo. Campo não documentado fica ausente/desconhecido. Não presumir quantidade 1 quando a fonte não indicar quantidade ou unidade singular.

inputs são participantes simultâneos com papéis. Origens alternativas geram regras separadas ligadas à mesma evidência. Participante pode ser entidade ou seletor documentado por tipo/estágio; nunca card “A, B ou C”.

Kinds: transformation, crafting, unlock, acquisition, progression, other. Evolução/Jogress são rótulos de mecânicas, não regras universais por franquia.

rules é a autoridade; edges é projeção compatível com rule_id. Combinações usam conector de operação reunindo entradas. Ele não é entidade nem recebe número de card. A+B→C não vira A→C e B→C independentes.

### Árvore de condições

| op | Significado/campos |
|---|---|
| all | Todos os children. |
| any | Pelo menos um child. |
| at_least | count entre children; condições sempre obrigatórias ficam fora, em all. |
| sequence | Ações na ordem da fonte. |
| compare | field, operator em = != > >= < <= in, value tipado e unidade quando documentada. |
| item | item_id, action possess/use/consume/equip, quantity e sujeito/escopo conhecidos. |
| event | Ação manual ou vínculo explícito com etapa/estado. |
| unknown | Texto original, motivo e referência. |

Toda folha preserva original/source_refs. Não inferir alternativas pela mera presença de “or” ou barra. Reconhecer >=, <=, at least/most, or more/less e sufixos +/- somente no contexto de coluna/legenda. Expressão não entendida é pending/unknown, nunca operador = por conveniência.

Quota documentada vira at_least com conjunto exato de condições elegíveis e exceções obrigatórias. Quota ambígua fica unknown com a explicação da fonte.

### Avaliador único

`atlas_model.evaluate_rule(rule, progress_context)` é função pura: retorna satisfied, unsatisfied ou unknown, motivos, próxima ação e avaliação por condição.

- all: falso se algum falso; verdadeiro se todos verdadeiros; desconhecido no restante.
- any: verdadeiro se algum verdadeiro; falso se todos falsos; desconhecido no restante.
- at_least: verdadeiro com N verdadeiros; falso quando verdadeiros+desconhecidos não podem chegar a N; desconhecido no restante.
- sequence: só concluir etapa se anteriores concluídas. Desmarcar etapa invalida posteriores da mesma sequência, com lista explícita de afetados e Desfazer.
- Folha manual entendida e não marcada: unsatisfied. Valor do jogador ausente ou interpretação desconhecida: unknown.
- Posse informada satisfaz apenas possuir/ter disponível. Usar/equipar/consumir exige ação separada.

O aplicativo não atua no jogo. Marcar evolução não decrementa estoque. A UI informa origem: Manual, Quantidade informada ou Sincronização externa.

Disponibilidade: Disponível, Não atendido, Não determinado. Unknown não vira disponível/checkbox de conclusão. Contagem bruta de folhas não representa progresso obrigatório em any/at_least.

### Mapeamento e montagem local

Objetivo e tipos visíveis entram no mapeamento. Item é condição em evolução, mas pode ser card em fabricação; loja pode ser contexto ou entidade do sistema.

IA classifica elementos em entity, rule, condition, acquisition, reference ou unknown. Seletores permitidos: coluna/célula, ancestral, linha, texto e links de elemento. Mapear estágio, atributos, participantes e alcance de condições.

Contrato fechado: nenhuma expressão Python/JavaScript, regex livre ou código executável. Validar IDs/intervalos/referências antes de aplicar.

Agrupar estruturas compatíveis; enviar hierarquia, cabeçalhos, amostras e notas adjacentes. Aplicar às linhas localmente. Prosa especial é analisada em lotes por entidade/contexto e pode gerar regra autossuficiente.

“Use Sacred wings on Angemon or Devimon” sob um destino gera duas regras de item documentadas, sem alterar a evolução natural para o mesmo destino. Evolves from corrobora participantes; não inventa condições.

Canonicalização inclui edição, participantes, papéis, quantidades e árvore tipada com operadores/valores. Usar JSON canônico+hash, não identidade textual com símbolos removidos. Ordenar somente grupos comutativos; preservar sequence e papéis de Jogress. Equivalência reversa exige evidência.

## 7. Correção da extração dos guias

Mudar HTML → parse_freeform para SourceDocument → seleção editorial → guia. parse_freeform permanece para texto/legado.

Montar primeiro uma projeção local legível com capítulos, texto, tabelas e candidatos de imagens, revisável sem IA. Organização assistida gera outra prévia preservando dados detalhados/referências.

- Respeitar ordem/hierarquia; objetivo curto separado da explicação. Tabelas de valores não viram só um parágrafo resumido.
- Lotes por seção/bloco com IDs. Dividir tabela grande por linhas com cabeçalhos/contexto e reassemblar sem deslocamento.
- Cobrir todas as unidades selecionadas, incluindo notas/especiais e fim do guia.
- Preservar avisos, incerteza, spoilers, edição e NG+. Não completar lacunas do autor.
- Mapas mantêm seção, legenda e referência. Baixar mídia aprovada pelo downloader validado, oferecer ampliação; não substituir mapa por busca genérica.
- Consolidação compara fatos/objetivos correspondentes em lotes. Não enviar tudo em uma chamada final nem analisar só os primeiros trechos para declarar ausência de conflito.
- Preservar fontes complementares, rotas alternativas e detalhes exclusivos; apresentar contradições por fato.
- Rastrear cada bloco de saída; abrir original e corrigir/excluir resultado na revisão.
- Cancelar/retomar reaproveita lotes compatíveis. Publicação atual só muda na aprovação.

Diálogo novo: Adicionar fonte, URL/arquivo/texto, título respeitado, jogo/edição, descoberta, progresso, revisão e destinos de uso. Preservar formulário após falha. Bridge inicia job curto; fechar diálogo não interrompe importação.

## 8. Catálogo de itens

Persistir catálogo/revisões em `config/guides/<slug>/items/` e posse separadamente. Não reutilizar o campo items de checklist.

Item: ID permanente, número de exibição, edição, nome, aliases confirmados, categoria, descrição, efeitos/atributos, obtenções, imagem, fontes, status de revisão.

Obtenções: alternativas drop, compra, recompensa, coleta, fabricação, troca, desconhecida; local/NPC/inimigo, quantidade, custo/moeda e condições somente quando documentados. Nenhuma invenção de taxa/preço/local.

Requisito pode criar item mínimo com nome/referência e “Obtenção não informada”. Isso não bloqueia uma evolução bem documentada.

Posse: quantity inteiro >=0 ou null (“Não informado”), favoritos e meta de quantidade opcionais. Zero significa não possuo. Controles -, quantidade, +, Confirmar e Desfazer. Usar valor absoluto versionado, não delta que possa duplicar após timeout.

Nova aba Itens no jogo, PC e celular: busca nome/alias/ID, filtros categoria/posse, lista de coleta. Ficha: Como obter, Onde é usado, fontes e links para Atlas/Guia.

Nomes iguais geram candidatos de vínculo dentro da mesma edição; homônimos/aliases pedem revisão. União confirmada preserva redirecionamento de IDs, quantidade e imagem.

Fontes diferentes podem fornecer evolução e obtenção. Contradição guarda alternativas/edição/refs e resolução por captura. Evidência nova incompatível reabre só o conflito afetado.

## 9. Pendências acionáveis e cobertura

Ledger por linha/parágrafo/item de lista/figura: mapped, consolidated, reference, excluded_by_user, pending; incluir IDs dos resultados e motivo. Linha inteiramente placeholder é reference/empty, não rota.

Diagnóstico: código, severidade, fonte/página/tabela/linha, cards/regras afetados, original, interpretação, motivo e ações possíveis.

Editor permite corrigir papel/colunas, separar participantes, ajustar alcance, operador/valor/grupo, vincular entidade/item, confirmar alias, escolher fato contraditório e excluir linha/bloco com justificativa.

“Fonte não documenta a regra” é distinto de “não conseguimos extrair”. O primeiro pode ser reconhecido explicitamente e publicado como unknown; o segundo continua bloqueando até correção/exclusão. Recorte excluído deve ser declarado, sem anunciar extração integral.

Backend valida cobertura, referência, conflito e versão do job na aprovação. Correção local reprocessa apenas dependentes; não exige recapturar ou chamar IA para tudo.

## 10. Preenchimento automático de imagens

### Busca unificada de entidades

Criar `search_entity_images(label, query_override="")`. Consulta padrão: label com espaços normalizados. Chamar busca existente diretamente, sem _web_art_query. Override só quando editado intencionalmente; consultas antigas geradas com nome de jogo não são override.

Trocar imagem e preencher lote usam o mesmo caminho. Testar a query recebida pelo transport: Agumon deve continuar Agumon.

Prioridade: mídia local vinculada à entidade; figura da fonte com associação inequívoca; busca web. Legenda/alt igual ao nome ou figura isolada sob título da entidade são evidências fortes. Galeria ambígua exige escolha.

Busca web exige correspondência nome/alias nos metadados disponíveis. Ranking determinístico: correspondência, imagem válida >=128×128, resolução útil, ordem do provedor. Não exigir transparência. Sem candidato suficiente: Revisar imagem; não aplicar imagem aleatória.

### Botão e job

Preencher imagens inicia cards sem imagem do sistema atual, inclusive prévia. Mostrar total, concluídos, preenchidos, sem resultado, falhas e Cancelar. O clique autoriza aplicação automática dos candidatos válidos; não confirmar card por card.

Preservar existentes por padrão; oferecer Revisar resultados, Trocar individualmente e Desfazer lote. Não sobrescrever imagem editada durante o job.

Uma busca ativa, até dois downloads, intervalo mínimo 1 s entre buscas, respeitando provedor. Cache por consulta/provedor; checkpoint por card; associação por entidade. Retomar reaproveita arquivos salvos.

Download validado com MIME/bytes/dimensões, redirects e hash. Guardar URL/página, atribuição disponível e lote. Prévia→publicação conserva mídia.

Desfazer restaura associação anterior somente onde o lote ainda é o último autor. Não remover arquivo usado por outro card. Informar cards preservados por edição posterior.

Imagens são reais da fonte/busca. Geração de arte/remoção de fundo não fazem parte.

## 11. Atlas desktop próximo da referência visual

Manter o shell e redesenhar a área interna:

- Barra: Atlas, sistema, busca, Fontes e revisões, Preencher imagens, Novo sistema.
- Filtros secundários recolhíveis; Mais reúne editar/exportar/excluir. Excluir permanece encontrável e confirma nome do sistema.
- Jobs antigos/cancelados em Importações e revisões; job ativo/falha relevante tem resumo de uma linha.
- Mapa flexível e inspector de 304 px acima de 1100 px; abaixo, drawer. Elementos grid/flex usam min-width/min-height 0.
- Cards 192×248 no mundo do grafo; ilustração 164 px; nome 16 px em até duas linhas; estágio/ref 12–13 px; ID 12 px.
- Imagem contain, nome completo no detalhe/acessibilidade, campos longos quebram no inspector.

Extrair guideSystemLayout para módulo compartilhado. Condensar ciclos em componentes fortemente conectados, ordenar DAG em camadas esquerda→direita e distribuir ciclo em área própria com retorno explícito. Ordem estável por card/fonte.

Geometria única para cards, portas e bounding box; SVG/cards sob mesma transformação. Linhas atrás dos cards; distinguir rota selecionada/alternativa. Combinações têm conector de operação, sem card falso.

Foco: último selecionado → objetivo → primeiro card. Mostrar entradas/saídas imediatas; muitos vizinhos viram grupos expansíveis com contagem, sem encolher tudo. Zoom automático de foco começa 100%, mínimo 75%; excesso usa pan.

Mapa completo pode ajustar mais distante para visão geral; Focar card restaura leitura. Não tratar uma miniatura de todo o grafo como tela principal de leitura.

Calcular layout global por revisão/filtro; renderizar cards na janela + uma tela de margem. Lista paginada 50. Não truncar conteúdo para desempenho.

Pointer Events/setPointerCapture com cleanup; não handlers globais de mouse. Pan com dedo, pinch no centro dos dedos, wheel no cursor; touch-action none somente no grafo.

Inspector: ID/nome/estágio, arte grande, atributos, rota, condições agrupadas, item clicável, cota/avisos, original e Ver fonte com linha destacada. Avaliação vem do backend; estados por texto/ícone/cor.

Spoilers escondem imagens/nomes sensíveis e oferecem revelação explícita. A referência visual não fornece fatos ou conexões.

## 12. Marcações pelo celular — prioridade

### Corrigir antes do redesign

Adicionar regressões com navegador e servidor real de teste para marcar/desmarcar etapa e requisito; não apenas chamar _companion_command diretamente.

Primeiro preservar detalhe aberto, remover suspensão de polling por foco, ordenar leituras e aplicar valor confirmado. Esta etapa funciona com schema atual e depois migra para condições novas.

### Contrato de gravação v2

Novo protocolo `api_version: 2`. Exemplo de comando:

~~~json
{
  "request_id": "uuid",
  "slug": "game",
  "kind": "requirement",
  "target": {"system_id": "system", "rule_id": "rule", "condition_id": "condition"},
  "expected": {"definition_revision": "revision", "value_version": 7},
  "value": true
}
~~~

No guia, target usa block_id; em quantidade, item_id; objetivo usa kind+ID. Servidor deriva sessão do cookie, não de campos livres.

Sucesso contém request_id, alvo, valor persistido, nova value_version, progress_revision e avaliações/resumos afetados. Só confirmar depois de persistir. Exigir boolean JSON; nunca converter a string “false” em true.

Versão de escrita é por alvo. Outro requisito pode mudar sem conflito; mudança da própria definição/remoção do alvo gera conflito específico. Imagem/arte não invalida marcação.

Sob lock: validar alvo/publicação/definição, verificar versão, persistir valor, incremento e recibo idempotente no mesmo arquivo atômico. Guardar os últimos 256 recibos por arquivo. Mesmo request_id+payload retorna o resultado anterior; mesmo ID com outro payload é erro.

HTTP 409 traz código, valor/versão atuais e mensagem inline. Não reaplicar intenção desatualizada silenciosamente. POST sem resposta: consultar estado e, se necessário, repetir mesmo request_id; não gerar comando duplicado.

Adapter mantém API antiga durante migração; clientes novos usam v2. Não mascarar 401/403/429/armazenamento como conflito.

### Interação correta

- Checkbox com label clicável >=44 px. Um handler change envia valor explícito; não duplicar com onclick do card.
- Capturar slug/alvo/revisão/valor antes de await; troca de jogo não muda o destino da gravação iniciada.
- Salvando/Salvo/Não salvo no controle afetado. Bloquear só ele, não a tela inteira.
- Falha restaura valor confirmado e preserva card, rota, scroll e rascunho.
- Desfazer envia valor anterior com versão atual; não inverte duas vezes uma ação antiga.
- Comandos independentes podem esperar em fila serial online; não existe fila offline escondida.
- request_id: randomUUID quando disponível, fallback crypto.getRandomValues, compatível com HTTP LAN.
- Conquista externa mostra “Obtida no RetroAchievements”, sem falso checkbox de desmarcação manual.
- Unknown abre explicação, posse calculada abre quantidade, condição manual tem checkbox. São controles diferentes.

### Leitura/reconexão

Polling serial 3 s, timeout 10 s, backoff 3/6/12/30 s. Uma leitura ativa. Geração/slug/ordem descartam resposta velha após ACK ou troca de tela/jogo.

Pausar document.hidden; retomar visibilitychange/online. Foco em input não impede receber dados; preservar rascunho do campo.

content_revision cobre conteúdo público, inclusive conquistas/alertas/imagens/catálogo; progress_revision cobre progresso. Cache/revisões por componente evitam reconstrução global a cada toque.

Offline conserva tela/horário em memória, desabilita escrita e oferece Reconectar. 401 pede pareamento; 409 mantém tela e decisão no controle conflitante.

## 13. UX/UI do celular

Cinco abas com ícone/rótulo: Agora, Guia, Atlas, Itens, Conquistas. Cabeçalho com jogo/conexão/seletor. Padrão acompanha jogo do PC. Escolher outro jogo no telefone só muda consulta; não troca o compacto do PC. “Voltar ao jogo do PC” restaura acompanhamento.

### Agora e objetivo comum

Exibir objetivo fixado, próxima etapa, alerta relevante e continuar. Objetivo pode ser bloco de guia, card/regra Atlas ou meta de quantidade de item.

Fixar explicitamente substitui objetivo ativo daquele jogo; preservar favoritos/histórico. Serviço compartilhado resolve: objetivo fixado válido → próxima etapa → próxima conquista. Desktop, overlay nativo, compacto legado e celular consomem a mesma resposta.

### Guia

Índice/busca/progresso, leitor com texto de 16 px e espaçamentos 8/12/16/24 px. Tabela larga rola dentro do bloco, com contexto de colunas. Mapas abrem ampliados com legenda.

Checklist, continuar daqui, favoritos e fonte próximos do trecho. Pergunta IA mantém rascunho/resposta por trecho, provedor/consentimento do PC; não expõe edição estrutural.

### Atlas

Seletor/busca por nome/alias/ID/estágio; card abre detalhe com Voltar, imagem, atributos, rota, condições e itens.

Foco/Mapa/Lista. Mapa completo ocupa área útil com pan/pinch e 100%/Focar; inspector vira bottom sheet recolhido/meia tela/tela inteira. Não reduzir o grafo para caber ao lado do inspector.

Filtros em drawer, seleção preservada. Não carregar imagens fora da área visível. Lista/busca acessam todos os cards mesmo em grafo grande.

### Itens, Conquistas e estado de navegação

Itens com catálogo/filtros/quantidade/ficha. Conquistas com busca e pendentes/obtidas/perdíveis, atualizando sem depender de toque no guia.

Detalhes sob demanda; estado em memória por slug/aba conserva seleção, scroll, filtro, rota, texto e rascunho. Não armazenar progresso em localStorage.

Validar 360×800, 390×844, 412×915, tablet 768×1024 e paisagem; safe-area, teclado virtual e texto 200%. Alvos >=44 px, texto >=16 px, labels auxiliares >=12 px. Sem scroll horizontal da página; mapa/tabela têm rolagem interna explícita. Foco visível, aria-live e reduced-motion.

## 14. APIs públicas e integração

Objetos request/options são validados por schema/listas permitidas; não repassar argumentos arbitrários ao browser/provedor.

### Bridge desktop

| Nome | Contrato |
|---|---|
| start_source_import(slug, request) | URL/arquivo/texto, título, edição, modo auto/http/browser → job_id/phase. |
| get_source_import_job(slug, job_id) | Progresso/páginas/erros/ações possíveis. |
| select_source_pages(slug, job_id, selection) | Candidatos/URLs e opções confirmados → capturing. |
| cancel_source_import(slug, job_id) | Cancela job e preserva checkpoints. |
| resume_source_import(slug, job_id, options) | Retoma pendentes com limites escolhidos. |
| list_game_sources(slug) | Fontes/capturas/consumidores. |
| get_game_source_review(slug, source_id, capture_id, page_id="") | Revisão paginada/contagens/avisos/seleção. |
| export_game_source(slug, source_id, capture_id, format) | JSON/Markdown ou backup local explícito. |
| start_source_extraction(slug, consumer, request) | consumer walkthrough/atlas/items, fontes/capturas/seleções, objetivo/tipos e destino de substituição. |
| get_source_extraction_job(slug, job_id) | Lotes/cobertura/prévia/pendências. |
| cancel_source_extraction / resume_source_extraction | slug/job_id; reutilização condicionada a fingerprint. |
| resolve_extraction_issue(slug, job_id, resolution) | issue_id, tipo de correção, valores/refs → cobertura. |
| approve_source_extraction(slug, job_id, expected_revision) | Valida e publica nova revisão. |
| get_source_reference(slug, ref) | Trecho normalizado correspondente e URL/âncora. |
| list_game_items / get_game_item | slug/edição/busca/filtros/cursor ou item_id. |
| update_game_item_quantity | slug/item_id/valor absoluto/versão/request_id → confirmado. |
| start_atlas_image_fill / get_atlas_image_fill_job | slug/sistema/revisão/cards ou job_id → progresso. |
| cancel_atlas_image_fill / undo_atlas_image_fill | slug/job_id/revisão → resultado por card. |

Métodos existentes add_walkthrough_gamefaqs/create_guide_system_from_gamefaqs/start_atlas_source/get_atlas_source_review continuam como adapters. Não depender deles para importar sites genéricos. O objeto backend atual fica em ui/app.js; não inventar ui/backend.js existente.

### HTTP companion

Manter pareamento/guards e GETs autenticados:

- /api/state: api_version, jogo ativo/consultado, revisões, objetivos, alertas, capacidades, contagens.
- /api/games: biblioteca resumida permitida.
- /api/guide e /api/guide/chapter: índice/capítulo por ID.
- /api/atlas/systems, /api/atlas/nodes, /api/atlas/node, /api/atlas/graph: índices/detalhe/grafo leve.
- /api/items, /api/items/detail, /api/achievements: listas/fichas paginadas, 50 padrão/100 máximo.
- /api/reference: trecho publicado permitido, validado pela referência.

Slug explícito na consulta manual. /api/action aceita progress, requirement, quantity, goal, path, ask. Importar, aprovar ou editar estrutura não é permitido pelo companion.

Fontes brutas, segredos, notas privadas e rascunhos não saem do PC. Filtros de spoilers valem em todas as rotas, inclusive busca/imagem/referência. Não vazar dados por cache entre sessões.

## 15. Migração, identidade e retomada

- Ler schema 3 e migrar em memória para regra por aresta, preservando IDs de sistema/nó/aresta/requisito.
- Converter semântica inequívoca; ambiguidade antiga vira unknown, não all obrigatório. Conservar marcações antigas armazenadas.
- Persistir schema 4 ao salvar/aprovar revisão, mantendo anterior restaurável. Não regravar a biblioteca inteira no boot.
- Reconciliação explícita de entidade/regra/condição preserva imagem/progresso. Reordenar fonte não reseta IDs; alterar condição real não herda conclusão sem revisão.
- Preferência edge_id migra para rule_id com alias. Aresta de projeção não é identidade do progresso.
- Sistemas novos: até 1.000 cards/3.000 regras e 20.000 participantes/segmentos de projeção; catálogo até 10.000 itens/edição. Excesso é diagnóstico para dividir/selecionar, nunca truncamento.
- Remover corte silencioso de 30 requisitos e referências; validar tamanho total e avisar, sem perder evidência.
- Fingerprint inclui fontes/capturas, seleção, objetivo, edição, aliases relevantes, schemas, parser/materializador e provedor/modelo. Quantidade do inventário não invalida extração.
- Mapeamento antigo incompatível é reprocessado com captura existente. Distinguir Reprocessar interpretação de Baixar novamente.
- Worker cancelado/antigo não publica. Aprovação compara revisão sob lock; job de imagens verifica associação por card.

## 16. Ordem de execução para Luna

Cada etapa encerra com teste e resultado verificável. Não começar pelo redesign enquanto marcações continuam falhando.

| Etapa | Entrega | Gate |
|---|---|---|
| 0 | Baseline e regressões. | Casos >=/<=, alcance de item e cliques mobile documentados; dados pessoais intactos. |
| 1 | Marcações/reconexão no celular atual. | Marcar/desmarcar persiste/aparece no PC, detalhe não fecha, conflitos/rede têm resposta correta. |
| 2 | Fonte comum/SourceStore/migração de leitura. | Captura compartilhada conserva tabelas/mídia/refs. |
| 3 | Descoberta/captura HTTP+dinâmica/revisão. | HTML/JS/tempo/rolagem/virtualização testados; smoke do exe com Edge. |
| 4 | Atlas 4/avaliador/mapeamento/pendências. | Casos Digimon e outros jogos preservam regras/combinações/cobertura. |
| 5 | Guias fiéis e catálogo/quantidades. | Hierarquia/tabelas/mapas, fontes compartilhadas e posse ligada corretamente. |
| 6 | Imagens em lote e Atlas desktop. | Query só nome, cancelamento/desfazer, leitura/conexões corretas. |
| 7 | Celular redesenhado/mapa completo/objetivo comum. | Mesma avaliação nos clientes, estado de navegação preservado. |
| 8 | Integração/migração/backup/documentação. | IDs/mídia/progresso preservados, suite/build Windows e verificação física registrada. |

Não substituir feature por toast/mock permanente nem marcar etapa concluída só porque o botão aparece. Se verificação externa não for possível, registrar qual critério ficou pendente sem alegar sucesso.

## 17. Matriz de testes

### Semântica Atlas

- >=25 e <=25 distintas; 15+, zeros, vazio, hífens.
- Natural e item para mesmo destino são alternativas; item não contamina rota.
- Três origens separadas; texto autossuficiente gera regra referenciada.
- Jogress conserva papéis/simetria documentada; múltiplas combinações são alternativas.
- Reencarnação mantém sequência e Desfazer.
- Receita sintética com dois ingredientes/quantidades→item; missão+nível→habilidade.
- any/all/at_least/sequence/unknown iguais em backend e clientes.
- ANY Mega conserva seletor quando atributos insuficientes.
- Cobertura total não aceita deduplicação de operadores opostos nem referência inexistente.

### Fontes/Guias

- Paginação, URLs repetidas, página ausente, redirects e edição divergente.
- Spans, tabela aninhada, cabeçalhos múltiplos, repetições legítimas, preformatted, listas, notas, figuras.
- Conteúdo JS, atraso, scroll body/contêiner, carregar mais e virtualização que remove nós anteriores.
- Timeout/limites salvam parcial; retomada/cancelamento preservam publicado.
- Uma captura alimenta consumidores sem download duplicado, com seleções independentes.
- Guia longo cobre também o fim, por lotes, sem eliminar detalhes.
- Título digitado chega aos metadados.
- Fontes complementares enriquecem; conflito real exige resolução localizada.
- Caso local FAQ 74471: duas páginas, árvore e mapas. Fixtures versionadas são sintéticas/pequenos trechos.
- Números antigos do FAQ 71975 (6 páginas/248 tabelas/323 linhas de evolução) identificam aquela captura, não constantes do importador.

### Itens/Imagens

- null/0/positivo distintos, request repetido idempotente.
- Fonte B complementa obtenção de item citado por A; edição/homônimo não são unidos indevidamente.
- Catálogo revisto não zera estoque/mídia.
- Consulta chega à busca como Agumon, sem prefixos/sufixos.
- Figura inequivocamente associada reutilizada; ambiguidades pedem revisão.
- Lote 150 cards cancela/retoma sem duplicar ou sobrescrever imagem posterior.
- Desfazer só afeta associações do lote; arquivo compartilhado permanece.
- Imagem inválida/404/rate limit deixa diagnóstico por card, sem corromper concluídos.

### Cliques mobile e concorrência

- Navegador + Flask/Waitress de teste; pareamento e POSTs reais em dados temporários.
- Toque checkbox/label gera uma gravação. Marcar/desmarcar persiste ao recarregar/reparear.
- Alteração PC→celular em até 3 s com LAN disponível; celular→PC em até um ciclo normal do desktop.
- Polling antigo não reverte ACK recente.
- Alvos diferentes não conflitam; mesmo alvo concorrente devolve 409/valor atual.
- Timeout depois do commit, clique duplo e request_id repetido não duplicam efeitos.
- Troca de aba/jogo durante POST não muda alvo.
- Wi-Fi interrompido, 401/403/429, JSON inválido e WinError recebem diagnóstico correto, sem Salvo falso.
- Card/rota/scroll/rascunho permanecem; checkbox/select focado não paralisa polling.
- Posse calculada, ação de usar item, unknown e conquista externa têm controles corretos.
- Guards de autorização/publicação/spoilers/referências mantidos em cada rota nova.

### UI e escala

- Desktop 1280×720, 1600×900 e estreita; celular nos tamanhos definidos, paisagem e texto 200%.
- 150/1.000 cards, 500/10.000 itens, 250 conquistas, sem truncamento e com detalhes sob demanda.
- Sem sobreposição de cards/toolbar/conexões ou scroll horizontal externo.
- Pan/zoom não recriam todo o grafo; polling não recria formulário.
- WebView2, Android Chrome e iPhone Safari. Viewport de desktop não equivale a aparelho físico.

## 18. Verificação e continuidade

Comandos de base na implementação:

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests -q
node --check ui/app.js
node --check ui/companion/app.js
git diff --check
.\.venv\Scripts\python.exe -m PyInstaller digitracker.spec --noconfirm
~~~

Adicionar checks dos scripts novos e testes Playwright em tests/e2e com pytest/API Python, sem novo ecossistema npm. Separar marcadores e2e/browser/live; suite unitária não usa rede, IA paga ou configurações/saves reais.

Build Windows verifica driver Playwright empacotado, captura sintética no Edge, companion e recursos compartilhados dentro do exe. Cancelamento fecha recursos próprios e não deixa workers órfãos.

Uma chamada real controlada ao provedor escolhido serve de smoke, usando consentimento/configuração autorizados; não trocar modelo por conveniência nem expor chave. Registrar simulação e chamada real separadamente.

Durante execução manter `docs/EXECUCAO_CELULAR_ATLAS_FONTES_ITENS.md` com etapa, commits, testes executados, falhas, limites e próximo passo exato. Atualizar HANDOFF ao concluir uma etapa integrada.

Nesta rodada a implementação integrada foi executada com **599 testes
passando**, além dos checks de sintaxe Python/JavaScript e `git diff --check`.
Build físico Windows/WebView2, smoke com provedor real e testes em aparelhos
continuam como validação de entrega, conforme o registro de execução.

Entrega: código, fixtures sintéticas, validação, documentação de uso/pendências e
migração restaurável. Não reutilizar tags; publicação depende da autorização
vigente de entrega e validação versão/tag/build. Push e tag não fazem parte desta
rodada.

## 19. Decisões de implementação que não devem ficar implícitas

### Identidade e publicação conjunta

O registro de entidades pertence ao jogo/edição e é persistido em revisões sob `config/guides/<slug>/entities/`. Um item usa o mesmo valor de entity_id como item_id; não criar identidades independentes para o mesmo item no catálogo e no Atlas.

IDs novos são gerados uma vez pelo backend e persistidos no job antes de revisão. O materializador recebe um resolvedor de entidades que consulta registro aprovado e entidades provisórias do job. Renomear uma entidade preserva ID; título/nome normalizado apenas procura candidatos.

Regras recebem IDs permanentes. Hash semântico serve para comparação/consolidação e não substitui o ID. Identificar condições pela posição lógica/identidade semântica reconciliada, sem recalcular todas ao reordenar linhas.

Job não publica entidades ou itens provisórios enquanto usuário revisa. Aprovação grava primeiro revisões imutáveis de entidades/catálogo e documento, valida todas e somente então substitui `current.json` atomicamente. No schema 4, current.json inclui entity_revision_id e catalog_revision_id; leitores resolvem esses ponteiros, sem “current” paralelo divergente no catálogo.

Falha antes da troca de current.json mantém a revisão anterior; revisão incompleta não é lida. Registrar transação/job para limpar arquivos órfãos em manutenção posterior, sem excluir arquivos durante a recuperação normal. A mesma operação sob lock preserva o restante do guia/sistemas quando só o catálogo é atualizado.

Arquivos legados `sources/<hash>.json` coexistem com a biblioteca nova e não são removidos ou reinterpretados como index.json. Importação legado→novo exige mapa explícito.

### Progresso e revisões

Estender progress.json/systems_state.json, preservando campos antigos, com value_versions e request_receipts. Quantidades ficam em items/progress.json. Objetivo ativo fica em objectives.json. Cada comando altera um desses documentos atomicamente; nenhuma marcação depende de gravar dois arquivos para ser confirmada.

Uma sequência que afeta várias condições registra todas no mesmo systems_state.json e no mesmo recibo. Projeções de UI/objetivo são calculadas depois; uma falha de notificação não transforma uma gravação concluída em falha de persistência.

definition_revision é hash da definição semântica do alvo, excluindo imagem, layout, descrição editorial e progresso. Preservar IDs não autoriza transferir conclusão quando condição mudou de >=25 para >=40.

Os comandos de desktop usam a mesma rotina de mutação/versionamento do companion. Manter a chamada de atualização do bundle do jogo e dos overlays após sucesso; não resolver o problema só com um estado local do telefone.

Receita com item exige separar disponibilidade de conclusão: estoque suficiente pode tornar os ingredientes disponíveis, mas não significa que o jogador fabricou/evoluiu. Não criar bloco concluído ou evento de evolução a partir da simples leitura do inventário.

### Limites da IA e cobertura

Schemas de mapeamento e condição rejeitam campos/tipos inválidos. Valores numéricos permanecem numéricos na validação; não passar árvores/grupos/value por _clean_text.

Adicionar intervalos de evidência em parágrafos longos: elemento+início/fim do trecho. Para tabelas, usar linha/célula. Texto dividido em lotes precisa mapa de cobertura de todos os intervalos, não só “elemento citado uma vez”.

Lotes de interpretação terão teto de 20.000 caracteres de entrada e até 18 tabelas; tabela maior é segmentada com cabeçalhos/contexto, texto maior em parágrafos/intervalos. Saída só atribui referências existentes no lote/contexto informado.

Unidades silenciosamente omitidas pelo modelo continuam pending. Reapresentar unidade omitida em um lote de reparo no máximo uma vez; depois oferecer revisão localizada. Retries HTTP existentes continuam separados de reparo semântico.

Campos normais de tabelas são convertidos localmente; prosa/nota difícil pode ser interpretada pela IA com schema restrito. Mapeamentos manuais corrigidos persistem no job e precedem o mapeamento sugerido apenas naquele contexto/seleção.

### Integração visual e teste

Montagem do componente compartilhado: `AtlasGraph.mount(element, {graph, selection, mode, onSelect, onViewportChange})` retorna update() e destroy(). Cálculo de layout é função pura exportada no namespace. Não acessar S global dentro dele.

O componente de requisitos recebe árvore+evaluation+callback. Não conhece pywebview, fetch ou arquivos; o caller desktop/companion injeta comandos.

IDs de teste estáveis: data-testid para guide-complete, atlas-requirement, item-quantity, save-status, source-import-progress e atlas-image-fill. Identidade do alvo em data-block-id/data-condition-id/data-item-id, não texto traduzido.

Adicionar fixtures sintéticas que exercitam o fluxo completo até current.json. Para imagens, mockar busca/download com imagens pequenas criadas pelo teste; para browser, servidor local de fixtures controlado. Não usar os arquivos em Downloads/config como fixture mutável.
