# Fontes locais

**Archivo**, sob a [SIL Open Font License 1.1](https://scripts.sil.org/OFL), que
permite redistribuir os arquivos junto do app.

Fica aqui (em vez de vir do CDN do Google Fonts) porque o DigiTracker é um app
desktop offline-first: sem rede, o CDN falha e a interface cai nas fontes do
sistema, perdendo o visual.

## Por que uma família só

A direção visual é a do Steam Deck / Big Picture, que usa **uma única família**
(Motiva Sans) em toda a interface. A hierarquia vem de **peso e tamanho**, não de
contraste entre tipos. Os cinco pesos (400–800) cobrem desde legenda até título.

Os **algarismos tabulares** do Archivo (`font-feature-settings: "tnum"`, aplicado
no `body`) alinham pontos, datas e contadores — por isso **nenhuma fonte
monoespaçada é baixada**. Orbitron, Inter e Share Tech Mono saíram nessa troca.

Só o subset **latin** é incluído (~188 KB no total).

## Regenerar

```bash
curl -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36" \
  "https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&display=swap" \
  -o /tmp/archivo.css
```

Para cada bloco `@font-face` do subset `latin`: baixe a URL do `src`, salve aqui
como `Archivo-<peso>.woff2` e confira o `src` em [`../fonts.css`](../fonts.css). O
User-Agent moderno é necessário — sem ele o Google devolve `.ttf` em vez de
`.woff2`.
