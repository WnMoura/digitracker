# Fontes locais

Orbitron, Inter e Share Tech Mono — todas sob a
[SIL Open Font License 1.1](https://scripts.sil.org/OFL), que permite
redistribuir os arquivos junto do app.

Ficam aqui (em vez de virem do CDN do Google Fonts) porque o DigiTracker é um
app desktop offline-first: sem rede, o CDN falha e a interface cai nas fontes do
sistema, perdendo o visual.

Só os subsets **latin** e **latin-ext** são incluídos — o suficiente para
português e inglês, ~164 KB no total. O Inter é uma *variable font*: o mesmo
arquivo atende os pesos 400–700, então há menos arquivos do que regras
`@font-face` em [`../fonts.css`](../fonts.css).

## Regenerar

```bash
curl -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36" \
  "https://fonts.googleapis.com/css2?family=Orbitron:wght@600;800&family=Inter:wght@400;500;600;700&family=Share+Tech+Mono&display=swap" \
  -o /tmp/gf.css
```

Depois, para cada bloco `@font-face` dos subsets `latin` e `latin-ext`: baixe a
URL do `src`, salve aqui e reescreva o `src` para `url(fonts/<arquivo>)` em
`../fonts.css`. O User-Agent moderno é necessário — sem ele o Google devolve
`.ttf` em vez de `.woff2`.
