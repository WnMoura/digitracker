# Product

## Register

product

## Platform

web

## Users
Jogadores que acompanham conquistas e walkthroughs em diferentes franquias,
emuladores e plataformas. O contexto principal é durante o jogo: a janela fica
always-on-top, grudada no emulador e consultada de relance. O produto é local-first
e de usuário único, mas seus modelos de dados não podem depender de um jogo.

## Product Purpose
Acompanhar progresso e transformar qualquer walkthrough em um guia compacto,
visual e acionável. RetroAchievements é o primeiro provedor, não uma restrição
arquitetural. Sucesso é saber, sem sair do jogo, qual é a próxima ação, quais
riscos são perdíveis e o que cabe na sessão atual, sempre podendo conferir a fonte.

## Positioning
Um HUD de conquistas que segue a ordem do seu guia, não a do site — e que vive por cima do emulador em vez de numa aba do navegador.

## Brand Personality
Uma mistura de painel de console — Steam Deck / Big Picture: escuro, gamer, hierarquia por peso e tamanho, cada jogo com a cor tirada da própria capa — com um HUD discreto que sai da frente, informa de relance e não compete com o jogo. A sensação alvo é a de uma peça do próprio setup do console, calma e confiante, nunca a de um app corporativo à parte.

## Anti-references
Não pode ter cara de UI gerada por IA: grades de cards idênticos, texto cinza sobre quase-branco, eyebrow em maiúscula acima de cada seção, bordas coloridas de um lado só. Não pode virar dashboard corporativo — frio, denso, cheio de tabelas e KPIs. E não deve imitar a UI nem a identidade do próprio site da RetroAchievements; o DigiTracker tem cara própria.

## Design Principles
O jogo é o herói; o app fica na periferia. Cada tela informa de relance e se recolhe — o overlay serve a jogatina, não disputa atenção com ela.

A ordem é do guia, não do site. O valor central é reordenar as conquistas pela walkthrough; toda tela reforça onde você está no guia e o que vem a seguir.

Cada jogo carrega a própria identidade. A cor de destaque sai da capa do jogo, então a arte é quem identifica e o app não impõe uma paleta única por cima.

Legível sobre qualquer cena. Como vive por cima do emulador, contraste e redundância importam: estados como hardcore e softcore se distinguem por rótulo e ícone, nunca só pela cor.

Offline-first de verdade. Fontes e assets são locais, sem CDN em runtime — o app tem a mesma aparência sem internet, e isso limita as escolhas visuais ao que não depende de rede.

## Accessibility & Inclusion
Sem exigência formal de nível WCAG, por ser ferramenta pessoal. Duas prioridades concretas: contraste que aguente o overlay ficar por cima de cenas coloridas do emulador sem o texto sumir; e não depender só de cor para transmitir informação — hardcore/softcore e os estados de progresso vêm sempre acompanhados de rótulo ou ícone, para funcionar com daltonismo.
