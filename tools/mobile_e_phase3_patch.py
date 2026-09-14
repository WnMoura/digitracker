from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found < count:
        raise RuntimeError(f"{path}: expected {count}, found {found}: {old[:120]!r}")
    p.write_text(text.replace(old, new, count), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Reference endpoint: match stable source coordinates and return only the
# editorial fragment needed by the phone, never raw source HTML/documents.
# ---------------------------------------------------------------------------
replace(
    "companion.py",
    '''        @app.get("/api/reference")
        def reference():
            value, error = public_state()
            if error:
                return error
            ref_id = str(request.args.get("id", request.args.get("block_id", ""))).strip()
            page = str(request.args.get("page", "")).strip()
            matches = []
            for chapter in value.get("chapters") or []:
                for block in chapter.get("blocks") or []:
                    if ref_id and ref_id not in {str(block.get("id", "")), str(block.get("element_id", ""))}:
                        continue
                    refs = block.get("source_refs") or []
                    if page and not any(str(ref.get("page", "")) == page for ref in refs if isinstance(ref, dict)):
                        continue
                    matches.append({"chapter_id": chapter.get("id", ""),
                                   "chapter_title": chapter.get("title", ""),
                                   "block": block})
            if ref_id and not matches:
                for system in value.get("systems") or []:
                    for node in system.get("nodes") or []:
                        if ref_id in {str(node.get("id", "")), str(node.get("entity_id", ""))}:
                            matches.append({"system_id": system.get("id", ""), "system_title": system.get("title", ""), "node": node})
            return jsonify(ok=True, references=matches[:100], total=len(matches),
                           content_revision=value.get("content_revision", ""))
''',
    '''        @app.get("/api/reference")
        def reference():
            value, error = public_state()
            if error:
                return error
            ref_id = str(request.args.get("id", request.args.get("block_id", ""))).strip()
            selectors = {
                key: str(request.args.get(key, "")).strip()
                for key in ("page", "section", "block", "row_id", "table_id", "element_id")
                if str(request.args.get(key, "")).strip()
            }

            def ref_matches(candidate):
                if not isinstance(candidate, dict):
                    return False
                return all(str(candidate.get(key, "")) == wanted for key, wanted in selectors.items())

            def editorial_block(block):
                rows = list(block.get("rows") or [])
                row_id = selectors.get("row_id")
                if row_id:
                    selected = [row for row in rows if isinstance(row, dict) and str(row.get("id", row.get("row_id", ""))) == row_id]
                    rows = selected or rows[:20]
                else:
                    rows = rows[:20]
                return {
                    key: copy.deepcopy(block.get(key))
                    for key in ("id", "type", "title", "text", "hidden", "source_refs")
                    if block.get(key) is not None
                } | {"items": copy.deepcopy((block.get("items") or [])[:20]), "rows": copy.deepcopy(rows)}

            matches = []
            for chapter in value.get("chapters") or []:
                for block in chapter.get("blocks") or []:
                    direct = ref_id and ref_id in {str(block.get("id", "")), str(block.get("element_id", ""))}
                    refs = block.get("source_refs") or []
                    if ref_id and not direct and not selectors:
                        continue
                    if selectors and not any(ref_matches(ref) for ref in refs):
                        continue
                    if not ref_id and not selectors:
                        continue
                    matches.append({"kind": "guide", "chapter_id": chapter.get("id", ""),
                                   "chapter_title": chapter.get("title", ""),
                                   "block": editorial_block(block)})
            # If the source coordinate is Atlas-only, return a safe structured
            # summary instead of reaching into source files or exposing raw HTML.
            if not matches:
                for system in value.get("systems") or []:
                    for node in system.get("nodes") or []:
                        refs = node.get("source_refs") or []
                        if (ref_id and ref_id in {str(node.get("id", "")), str(node.get("entity_id", ""))}) or (selectors and any(ref_matches(ref) for ref in refs)):
                            matches.append({"kind": "atlas", "system_id": system.get("id", ""),
                                            "system_title": system.get("title", ""),
                                            "node": {key: copy.deepcopy(node.get(key)) for key in ("id", "label", "subtitle", "card_number", "source_refs")}})
                    for edge in system.get("edges") or []:
                        if selectors and any(ref_matches(ref) for ref in edge.get("source_refs") or []):
                            matches.append({"kind": "atlas", "system_id": system.get("id", ""),
                                            "system_title": system.get("title", ""),
                                            "edge": {key: copy.deepcopy(edge.get(key)) for key in ("id", "label", "from", "to", "requirements", "source_refs")}})
            return jsonify(ok=True, references=matches[:20], total=len(matches),
                           content_revision=value.get("content_revision", ""))
''',
)

# ---------------------------------------------------------------------------
# Presentation state: saved scroll per logical screen, search return state and
# item detail are explicit instead of inferred from browser focus/DOM.
# ---------------------------------------------------------------------------
replace(
    "ui/companion/app.js",
    '''  return {view: "home", history: [], gameSlug: "", guideMode: "read", guideBlockId: "", atlasMode: "systems", atlasQuery: "", atlasSystemId: "", atlasNodeId: "", atlasEdgeId: "", itemFilter: "all", itemQuery: "", itemId: "", search: false, query: "", library: false, moreMode: "menu", assistant: false, source: null, conflict: null, forget: null, recent: [], pending: []};''',
    '''  return {view: "home", history: [], gameSlug: "", guideMode: "read", guideBlockId: "", atlasMode: "systems", atlasQuery: "", atlasSystemId: "", atlasNodeId: "", atlasEdgeId: "", itemFilter: "all", itemQuery: "", itemId: "", search: false, searchReturn: null, query: "", library: false, moreMode: "menu", assistant: false, source: null, conflict: null, forget: null, scroll: {}, recent: [], pending: []};''',
)

# Add presentation helpers before retryAfterSeconds.
replace(
    "ui/companion/app.js",
    '''function retryAfterSeconds(response) {''',
    '''function viewKey() {
  if(M.p.view==="guide")return `guide:${M.p.guideMode}:${M.p.guideMode==="read"?M.p.guideBlockId:"list"}`;
  if(M.p.view==="atlas")return `atlas:${M.p.atlasMode}:${M.p.atlasSystemId}:${M.p.atlasMode==="route"?M.p.atlasNodeId:"list"}`;
  if(M.p.view==="items")return `items:${M.p.itemId||"list"}:${M.p.itemFilter}`;
  if(M.p.view==="more")return `more:${M.p.moreMode}`;
  return "home";
}
function rememberScroll() { M.p.scroll={...(M.p.scroll||{}),[viewKey()]:Math.max(0,Math.round(window.scrollY||0))}; }
function storedScroll() { return Number(M.p.scroll?.[viewKey()]||0); }
function focusSnapshot(rootNode=content) {
  const active=document.activeElement;if(!active||!rootNode.contains(active))return null;
  let selector="";
  if(active.id)selector=`#${CSS.escape(active.id)}`;
  else if(active.dataset?.itemInput)selector=`[data-item-input="${CSS.escape(active.dataset.itemInput)}"]`;
  else if(active.name)selector=`[name="${CSS.escape(active.name)}"]`;
  return selector?{selector,start:active.selectionStart,end:active.selectionEnd}:null;
}
function restoreFocus(snapshot) { if(!snapshot)return;requestAnimationFrame(()=>{const field=content.querySelector(snapshot.selector);if(!field)return;field.focus({preventScroll:true});try{if(snapshot.start!=null)field.setSelectionRange(snapshot.start,snapshot.end??snapshot.start)}catch(_){}}); }
function retryAfterSeconds(response) {''',
)

# Selective polling updates only the visible screen region unless an affected
# overlay itself is open.
replace(
    "ui/companion/app.js",
    '''    M.revisions={...next};M.data.revisions={...next};M.retry=0;setConnected(true);selections();scrubPresentation();
    if(draw&&changed.length)render();
    flush().catch(()=>{});''',
    '''    M.revisions={...next};M.data.revisions={...next};M.retry=0;setConnected(true);selections();scrubPresentation();
    if(draw&&changed.length)renderChanged(changed);
    flush().catch(()=>{});''',
)

# Atlas alternatives: no truncation, collapsed list as specified.
replace(
    "ui/companion/app.js",
    '''  const alternatives = (rows, endpoint) => rows.filter((row) => row.id !== edge?.id).slice(0,4).map((row) => { const item = nodeById(system, row[endpoint]); return item ? `<button class="alt-chip" data-act="atlas-edge" data-edge="${esc(row.id)}">${esc(item.label)} · #${num(item)}</button>` : ""; }).join("");
  return `<section class="atlas-route"><div class="atlas-route-head"><button class="text-action" data-act="atlas-mode" data-mode="entities">← Entidades</button><button class="text-action" data-act="atlas-mode" data-mode="systems">Sistemas</button></div><div class="route-stack">${routeNode(system, from, "Origem", false)}${from ? "<span class='route-arrow'>↓</span>" : ""}${routeNode(system, node, "Selecionado", true)}${to ? "<span class='route-arrow'>↓</span>" : ""}${routeNode(system, to, "Destino", false)}</div>${incoming.length > 1 ? `<div class="alternatives"><b>Outras origens · ${incoming.length - 1}</b><div>${alternatives(incoming, "from")}</div></div>` : ""}${outgoing.length > 1 ? `<div class="alternatives"><b>Outros destinos · ${outgoing.length - 1}</b><div>${alternatives(outgoing, "to")}</div></div>` : ""}<section class="route-detail">''',
    '''  const alternatives = (rows, endpoint) => rows.filter((row) => row.id !== edge?.id).map((row) => { const item = nodeById(system, row[endpoint]); return item ? `<button class="alt-chip" data-act="atlas-edge" data-edge="${esc(row.id)}">${esc(item.label)} · #${num(item)}</button>` : ""; }).join("");
  const alternativeList=(label,rows,endpoint)=>rows.length>1?`<details class="alternatives"><summary>${esc(label)} · ${rows.length-1}</summary><div>${alternatives(rows,endpoint)}</div></details>`:"";
  return `<section class="atlas-route"><div class="atlas-route-head"><button class="text-action" data-act="atlas-mode" data-mode="entities">← Entidades</button><button class="text-action" data-act="atlas-mode" data-mode="systems">Sistemas</button></div><div class="route-stack">${routeNode(system, from, "Origem", false)}${from ? "<span class='route-arrow'>↓</span>" : ""}${routeNode(system, node, "Selecionado", true)}${to ? "<span class='route-arrow'>↓</span>" : ""}${routeNode(system, to, "Destino", false)}</div>${alternativeList("Outras origens",incoming,"from")}${alternativeList("Outros destinos",outgoing,"to")}<section class="route-detail">''',
)

# Item list -> explicit detail with structured acquisitions and structured Atlas
# uses only. No text/name inference is used to manufacture relationships.
replace(
    "ui/companion/app.js",
    '''function items() {
  const all = M.data.items || [], filter = M.p.itemFilter, itemQuery = normalized(M.p.itemQuery || "");
  const rows = all.filter((item) => (filter === "have" ? Number(item.quantity) > 0 : filter === "missing" ? item.quantity === 0 || item.quantity == null : true) && queryMatches(itemQuery, item.name, item.id, ...(item.aliases || [])));
  return `<section class="screen-heading"><span class="eyebrow">ITENS</span><h1>Inventário</h1></section><label class="local-search"><span aria-hidden="true">⌕</span><input id="item-local-search" type="search" value="${esc(M.p.itemQuery || "")}" placeholder="Buscar item ou #ID" aria-label="Buscar item"></label><div class="mobile-segmented"><button class="${filter === "all" ? "active" : ""}" data-act="item-filter" data-filter="all">Todos</button><button class="${filter === "have" ? "active" : ""}" data-act="item-filter" data-filter="have">Tenho</button><button class="${filter === "missing" ? "active" : ""}" data-act="item-filter" data-filter="missing">Faltam</button></div><section class="list-section">${rows.map((item) => { const value = item.quantity == null ? "" : Number(item.quantity), status = item.quantity == null ? "Quantidade desconhecida" : Number(item.quantity) === 0 ? "Não tenho" : `Tenho · ${item.quantity}`; return `<article class="item-card"><div><b>${esc(item.name)}</b><small>${esc(status)}</small><p>${esc(item.description || item.source || "Origem e uso documentados no guia.")}</p></div><label>Qtd.<input inputmode="numeric" type="number" min="0" step="1" data-item-input="${esc(item.id)}" value="${M.drafts.items[item.id] ?? value}" placeholder="—"></label><button class="outline" data-act="item-save" data-item="${esc(item.id)}">Salvar</button></article>`; }).join("") || "<p class='muted'>Nenhum item corresponde a este filtro.</p>"}</section>`;
}
''',
    '''function itemStatus(item){return item.quantity==null?"Quantidade desconhecida":Number(item.quantity)===0?"Não tenho":`Tenho · ${item.quantity}`;}
function acquisitionText(row){if(typeof row==="string")return row;if(!row||typeof row!=="object")return "";return row.text||row.label||row.method||row.location||row.name||"";}
function itemUses(item){
  const uses=[];
  for(const system of M.data.systems||[])for(const edge of system.edges||[])for(const rule of edge.requirements||[]){
    const ids=[rule.item_id,rule.condition?.item_id,...(rule.items||[]).map(value=>value?.item_id||value?.id)].filter(Boolean).map(String);
    if(!ids.includes(String(item.id)))continue;
    const target=nodeById(system,edge.to);uses.push({system,edge,target,rule});
  }
  return uses;
}
function itemDetail(item){
  if(!item)return `<section class="mobile-empty"><h1>Item indisponível</h1><button class="outline" data-act="item-back">Voltar à lista</button></section>`;
  const value=item.quantity==null?"":Number(item.quantity), acquisitions=(item.acquisitions||[]).map(acquisitionText).filter(Boolean), uses=itemUses(item);
  return `<button class="text-action back-row" data-act="item-back">← Itens</button><article class="item-detail"><div class="card-meta"><span>ITEM</span><b>${esc(item.category||item.item_kind||"")}</b></div><h1>${esc(item.name)}</h1><p>${esc(item.description||"")}</p><section class="item-quantity"><div><b>${esc(itemStatus(item))}</b><small>Deixe em branco para quantidade desconhecida; zero significa que você não possui o item.</small></div><label>Qtd.<input inputmode="numeric" type="number" min="0" step="1" data-item-input="${esc(item.id)}" value="${M.drafts.items[item.id]??value}" placeholder="—"></label><button class="primary" data-act="item-save" data-item="${esc(item.id)}">Salvar quantidade</button></section><section class="detail-section"><h2>Como obter</h2>${acquisitions.map(text=>`<p>• ${esc(text)}</p>`).join("")||"<p class='muted'>Nenhuma origem estruturada foi documentada para este item.</p>"}</section><section class="detail-section"><h2>Usado em</h2>${uses.map(({system,edge,target,rule})=>`<button class="simple-row" data-act="item-use" data-system="${esc(system.id)}" data-edge="${esc(edge.id)}" data-node="${esc(target?.id||edge.to||"")}"><span>◇</span><span><b>${esc(target?.label||edge.label||"Rota do Atlas")}</b><small>${esc(system.title)} · ${esc(rule.text||"Uso documentado")}</small></span><span>›</span></button>`).join("")||"<p class='muted'>Nenhum uso estruturado foi documentado no Atlas.</p>"}</section>${sourceButton(item.source_refs)}</article>`;
}
function items() {
  const all=M.data.items||[], selected=all.find(item=>item.id===M.p.itemId);if(M.p.itemId)return itemDetail(selected);
  const filter=M.p.itemFilter,itemQuery=normalized(M.p.itemQuery||"");
  const rows=all.filter(item=>(filter==="have"?Number(item.quantity)>0:filter==="missing"?item.quantity===0||item.quantity==null:true)&&queryMatches(itemQuery,item.name,item.id,...(item.aliases||[])));
  return `<section class="screen-heading"><span class="eyebrow">ITENS</span><h1>Inventário</h1></section><label class="local-search"><span aria-hidden="true">⌕</span><input id="item-local-search" type="search" value="${esc(M.p.itemQuery||"")}" placeholder="Buscar item ou #ID" aria-label="Buscar item"></label><div class="mobile-segmented"><button class="${filter==="all"?"active":""}" data-act="item-filter" data-filter="all">Todos</button><button class="${filter==="have"?"active":""}" data-act="item-filter" data-filter="have">Tenho</button><button class="${filter==="missing"?"active":""}" data-act="item-filter" data-filter="missing">Faltam</button></div><section class="list-section">${rows.map(item=>`<button class="item-list-row" data-act="item-open" data-item="${esc(item.id)}"><span>▣</span><span><b>${esc(item.name)}</b><small>${esc(itemStatus(item))}</small></span><span>›</span></button>`).join("")||"<p class='muted'>Nenhum item corresponde a este filtro.</p>"}</section>`;
}
''',
)

# Search overlay gets an identity and details can return to the same results.
replace(
    "ui/companion/app.js",
    '''  return `<div class="overlay-screen"><div class="overlay-head"><button class="mobile-icon-button" data-act="search-close" aria-label="Voltar">←</button><label><span class="sr-only">Buscar</span><input id="global-search" type="search" value="${esc(M.p.query)}" placeholder="Buscar guia, Atlas ou item" autofocus></label></div><div class="search-results">''',
    '''  return `<div class="overlay-screen" data-overlay="search"><div class="overlay-head"><button class="mobile-icon-button" data-act="search-close" aria-label="Voltar">←</button><label><span class="sr-only">Buscar</span><input id="global-search" type="search" value="${esc(M.p.query)}" placeholder="Buscar guia, Atlas ou item" autofocus></label></div><div class="search-results">''',
)
replace(
    "ui/companion/app.js",
    '''  return `<div class="overlay-screen"><div class="overlay-head"><button class="mobile-icon-button" data-act="library-close" aria-label="Fechar">×</button><h1>Biblioteca</h1></div><div class="library-list">''',
    '''  return `<div class="overlay-screen" data-overlay="library"><div class="overlay-head"><button class="mobile-icon-button" data-act="library-close" aria-label="Fechar">×</button><h1>Biblioteca</h1></div><div class="library-list">''',
)

# Source modal now loads/render editorial evidence.
replace(
    "ui/companion/app.js",
    '''function sourceModal() {
  const ref = M.p.source; if (!ref) return "";
  return `<div class="modal-backdrop"><section class="mobile-modal" role="dialog" aria-modal="true"><button class="mobile-icon-button modal-close" data-act="source-close" aria-label="Fechar">×</button><span class="eyebrow">FONTE</span><h2>Referência local</h2><p>${esc(sourceName([ref]) || "Trecho documentado")}</p><p>A fonte original é preservada no PC. O Atlas mantém esta referência ao caminho consultado.</p>${ref.url ? `<a class="outline link-button" href="${esc(ref.url)}" target="_blank" rel="noreferrer">Abrir fonte externa</a>` : ""}</section></div>`;
}
''',
    '''function referenceRows(rows){return (rows||[]).map(row=>Array.isArray(row)?row.map(value=>String(value??"")).join(" · "):row&&typeof row==="object"?Object.values(row).filter(value=>typeof value!=="object").map(value=>String(value??"")).join(" · "):String(row??"")).filter(Boolean);}
function sourceModal() {
  const state=M.p.source;if(!state)return "";const ref=state.ref||state, references=state.references||[];
  const body=state.loading?"<p>Carregando o trecho publicado…</p>":state.error?`<p class="warning">${esc(state.error)}</p>`:references.map(row=>{const block=row.block;if(block)return `<article class="source-reference"><small>${esc(row.chapter_title||"Guia")}</small><h3>${esc(block.title||"Trecho")}</h3>${block.hidden?"<p>Spoiler oculto.</p>":`${block.text?`<p>${esc(block.text)}</p>`:""}${(block.items||[]).map(item=>`<p>• ${esc(item.text||item)}</p>`).join("")}${referenceRows(block.rows).map(text=>`<p class="source-row">${esc(text)}</p>`).join("")}`}</article>`;const node=row.node;return node?`<article class="source-reference"><small>${esc(row.system_title||"Atlas")}</small><h3>${esc(node.label||"Entidade")}</h3><p>${esc(node.subtitle||"Referência estruturada no Atlas.")}</p></article>`:"";}).join("")||"<p>Nenhum trecho editorial publicado corresponde exatamente a esta referência.</p>";
  return `<div class="modal-backdrop"><section class="mobile-modal source-modal" role="dialog" aria-modal="true"><button class="mobile-icon-button modal-close" data-act="source-close" aria-label="Fechar">×</button><span class="eyebrow">FONTE</span><h2>Trecho correspondente</h2><p>${esc(sourceName([ref])||"Trecho documentado")}</p>${body}</section></div>`;
}
async function openSource(ref){
  M.p.source={ref,loading:true,references:[]};render();
  try{
    if(M.demo){const matches=[];for(const chapter of M.data.chapters||[])for(const block of chapter.blocks||[])if((block.source_refs||[]).some(candidate=>Object.entries(ref).every(([key,value])=>value==null||value===""||String(candidate?.[key]??"")===String(value))))matches.push({kind:"guide",chapter_title:chapter.title,block});M.p.source={ref,references:matches};}
    else{const params=new URLSearchParams({slug:slug()});for(const key of ["page","section","block","row_id","table_id","element_id"])if(ref?.[key]!=null&&ref[key]!=="")params.set(key,String(ref[key]));if(ref?.id)params.set("id",String(ref.id));const response=await api(`/api/reference?${params}`);M.p.source={ref,references:response.references||[]};}
  }catch(error){M.p.source={ref,references:[],error:error.message};}
  render();
}
''',
)

# Partial rendering/preserved scroll and search-return affordance.
replace(
    "ui/companion/app.js",
    '''function body() { if (M.p.view === "guide") return guide(); if (M.p.view === "atlas") return atlas(); if (M.p.view === "items") return items(); if (M.p.view === "more") return more(); return home(); }
function render() {
  if (!M.data?.ok) return;
  tabs.hidden = false;
  tabs.querySelectorAll("button").forEach((button) => { const active = button.dataset.tab === M.p.view; button.classList.toggle("active", active); button.setAttribute("aria-current", active ? "page" : "false"); });
  content.innerHTML = `<div class="mobile-screen">${body()}</div>${M.p.search ? search() : ""}${M.p.library ? library() : ""}${sourceModal()}${conflictModal()}${forgetModal()}`; setConnected(M.connected);
}
function nav(view) { if (M.p.view !== view) M.p.history = [...M.p.history, M.p.view].slice(-20); M.p.view = view; window.scrollTo({top:0,behavior:"instant"}); persist(); render(); }
''',
    '''function searchBackButton(){return M.p.searchReturn?`<button class="text-action search-return" data-act="search-back">← Resultados da busca</button>`:"";}
function body() { let page;if(M.p.view==="guide")page=guide();else if(M.p.view==="atlas")page=atlas();else if(M.p.view==="items")page=items();else if(M.p.view==="more")page=more();else page=home();return searchBackButton()+page; }
function tabState(){tabs.hidden=false;tabs.querySelectorAll("button").forEach(button=>{const active=button.dataset.tab===M.p.view;button.classList.toggle("active",active);button.setAttribute("aria-current",active?"page":"false");});}
function render(options={}) {
  if(!M.data?.ok)return;const top=options.top??window.scrollY, focus=focusSnapshot();tabState();
  content.innerHTML=`<div class="mobile-screen">${body()}</div>${M.p.search?search():""}${M.p.library?library():""}${sourceModal()}${conflictModal()}${forgetModal()}`;setConnected(M.connected);
  requestAnimationFrame(()=>window.scrollTo({top,behavior:"instant"}));restoreFocus(focus);
}
function renderScreen() {
  if(!M.data?.ok)return render();const screen=content.querySelector(".mobile-screen");if(!screen)return render();const top=window.scrollY,focus=focusSnapshot(screen);screen.innerHTML=body();setConnected(M.connected);requestAnimationFrame(()=>window.scrollTo({top,behavior:"instant"}));restoreFocus(focus);
}
function replaceOverlay(name,html){const current=content.querySelector(`[data-overlay="${name}"]`);if(!current)return;const top=current.scrollTop,template=document.createElement("template");template.innerHTML=html.trim();current.replaceWith(template.content.firstElementChild);const next=content.querySelector(`[data-overlay="${name}"]`);if(next)next.scrollTop=top;}
function renderChanged(changed){
  const keys=new Set(changed),view=M.p.view;
  const affectsScreen=keys.has("games")||view==="home"||(view==="guide"&&(keys.has("guide")||keys.has("assistant")))||(view==="atlas"&&(keys.has("atlas")||keys.has("media")))||(view==="items"&&keys.has("items"))||(view==="more"&&(keys.has("achievements")||keys.has("guide")));
  if(affectsScreen)renderScreen();
  if(M.p.search&&["guide","atlas","items"].some(key=>keys.has(key)))replaceOverlay("search",search());
  if(M.p.library&&keys.has("games"))replaceOverlay("library",library());
}
function nav(view,options={}){rememberScroll();if(M.p.view!==view)M.p.history=[...M.p.history,M.p.view].slice(-20);M.p.view=view;if(!options.keepSearchReturn)M.p.searchReturn=null;persist();render({top:options.top??storedScroll()});}
''',
)

# Event transitions preserve list scroll and explicit search-return state.
replace(
    "ui/companion/app.js",
    '''  if (a === "guide-mode") { M.p.guideMode = button.dataset.mode; persist(); return render(); }
  if (a === "guide-open") { M.p.guideBlockId = button.dataset.block; M.p.guideMode = "read"; const row = guideBlocks(M.data).find((item) => item.id === button.dataset.block); rememberRecent("guide",button.dataset.block,row?.title || "Guia",row?.chapterTitle || ""); return nav("guide"); }
''',
    '''  if (a === "guide-mode") { rememberScroll(); M.p.guideMode=button.dataset.mode; persist(); return render({top:storedScroll()}); }
  if (a === "guide-open") { rememberScroll(); M.p.guideBlockId=button.dataset.block;M.p.guideMode="read";const row=guideBlocks(M.data).find(item=>item.id===button.dataset.block);rememberRecent("guide",button.dataset.block,row?.title||"Guia",row?.chapterTitle||"");persist();return render({top:storedScroll()}); }
''',
)
replace(
    "ui/companion/app.js",
    '''  if (a === "item-filter") { M.p.itemFilter=button.dataset.filter;persist();return render(); }
  if (a === "item-save") {''',
    '''  if (a === "item-filter") { M.p.itemFilter=button.dataset.filter;persist();return renderScreen(); }
  if (a === "item-open") { rememberScroll();M.p.itemId=button.dataset.item;const item=(M.data.items||[]).find(row=>row.id===M.p.itemId);rememberRecent("item",M.p.itemId,item?.name||"Item","Inventário");persist();return render({top:storedScroll()}); }
  if (a === "item-back") { rememberScroll();M.p.itemId="";persist();return render({top:storedScroll()}); }
  if (a === "item-use") { M.p.atlasSystemId=button.dataset.system;M.p.atlasNodeId=button.dataset.node;M.p.atlasEdgeId=button.dataset.edge;M.p.atlasMode="route";return nav("atlas"); }
  if (a === "item-save") {''',
)

replace(
    "ui/companion/app.js",
    '''  if (a === "search-close") { M.p.search=false;persist();return render(); }
''',
    '''  if (a === "search-close") { M.p.search=false;M.p.searchReturn=null;persist();return render(); }
  if (a === "search-back") { M.p.search=true;M.p.query=M.p.searchReturn?.query||M.p.query;const top=Number(M.p.searchReturn?.scrollTop||0);render();requestAnimationFrame(()=>{const overlay=content.querySelector('[data-overlay="search"]');if(overlay)overlay.scrollTop=top;document.getElementById("global-search")?.focus();});return; }
''',
)

replace(
    "ui/companion/app.js",
    '''  if (a === "search-guide") { M.p.search=false;M.p.guideBlockId=button.dataset.block;M.p.guideMode="read";return nav("guide"); }
  if (a === "search-atlas") { M.p.search=false;M.p.atlasSystemId=button.dataset.system;M.p.atlasNodeId=button.dataset.node;M.p.atlasMode="route";selections();return nav("atlas"); }
  if (a === "search-item") { M.p.search=false;M.p.itemId=button.dataset.item;return nav("items"); }
''',
    '''  if (["search-guide","search-atlas","search-item"].includes(a)) {
    const overlay=content.querySelector('[data-overlay="search"]');M.p.searchReturn={query:M.p.query,scrollTop:overlay?.scrollTop||0};M.p.search=false;
    if(a==="search-guide"){M.p.guideBlockId=button.dataset.block;M.p.guideMode="read";return nav("guide",{keepSearchReturn:true,top:0});}
    if(a==="search-atlas"){M.p.atlasSystemId=button.dataset.system;M.p.atlasNodeId=button.dataset.node;M.p.atlasMode="route";selections();return nav("atlas",{keepSearchReturn:true,top:0});}
    M.p.itemId=button.dataset.item;return nav("items",{keepSearchReturn:true,top:0});
  }
''',
)
replace(
    "ui/companion/app.js",
    '''  if (a === "source") { try { M.p.source=JSON.parse(button.dataset.source||"{}"); } catch (_) {} return render(); }
''',
    '''  if (a === "source") { try { return openSource(JSON.parse(button.dataset.source||"{}")); } catch (_) { return; } }
''',
)

# Fresh top-level search starts a new search history; keyboard handling hides
# bottom navigation while a software keyboard reduces the visual viewport.
replace(
    "ui/companion/app.js",
    '''document.getElementById("mobile-search-toggle")?.addEventListener("click",()=>{M.p.search=true;render();requestAnimationFrame(()=>document.getElementById("global-search")?.focus());});''',
    '''document.getElementById("mobile-search-toggle")?.addEventListener("click",()=>{M.p.searchReturn=null;M.p.search=true;render();requestAnimationFrame(()=>document.getElementById("global-search")?.focus());});''',
)
replace(
    "ui/companion/app.js",
    '''window.addEventListener("online",()=>M.data?poll(true):refresh(true));''',
    '''function keyboardState(){const viewport=window.visualViewport;if(!viewport)return document.body.classList.remove("keyboard-open");document.body.classList.toggle("keyboard-open",window.innerHeight-viewport.height>120);}
window.visualViewport?.addEventListener("resize",keyboardState);window.visualViewport?.addEventListener("scroll",keyboardState);document.addEventListener("focusin",event=>{if(event.target.matches("input,textarea,select"))setTimeout(()=>event.target.scrollIntoView({block:"center",behavior:"smooth"}),120);});keyboardState();
window.addEventListener("online",()=>M.data?poll(true):refresh(true));''',
)

# Submit/flush confirmations can update the stable visible region without
# rebuilding all overlays and losing an open path/form.
replace(
    "ui/companion/app.js",
    '''    M.data = patchConfirmedValue(M.data, result); dropPending(key); notify("Salvo no PC."); persist(); render();''',
    '''    M.data=patchConfirmedValue(M.data,result);dropPending(key);notify("Salvo no PC.");persist();renderScreen();''',
)
replace(
    "ui/companion/app.js",
    '''    persist(); render();
  })();
''',
    '''    persist(); M.p.conflict?render():renderScreen();
  })();
''',
)

# ---------------------------------------------------------------------------
# CSS for item detail, source evidence, alternatives and software keyboard.
# ---------------------------------------------------------------------------
style = Path("ui/companion/style.css")
css = style.read_text(encoding="utf-8")
marker = "/* Mobile E phase 3 */"
if marker not in css:
    css += '''\n\n/* Mobile E phase 3 */
.item-list-row { width:100%; min-height:60px; padding:10px 16px; display:grid; grid-template-columns:28px minmax(0,1fr) auto; align-items:center; gap:10px; border:0; border-bottom:1px solid var(--line); border-radius:0; text-align:left; background:transparent; }
.item-list-row:last-child { border-bottom:0; }
.item-list-row > span:first-child { color:var(--cyan); text-align:center; }
.item-list-row b,.item-list-row small { display:block; overflow-wrap:anywhere; }
.item-detail { display:grid; gap:16px; }
.item-detail > h1 { overflow-wrap:anywhere; }
.item-quantity { padding:16px; display:grid; grid-template-columns:minmax(0,1fr) 76px; gap:10px; border:1px solid var(--line); border-radius:14px; background:var(--surface); }
.item-quantity small { display:block; margin-top:4px; }
.item-quantity .primary { grid-column:1 / -1; }
.detail-section { display:grid; gap:8px; }
.detail-section > .muted { padding:12px 0; }
.alternatives { border:1px solid var(--line); border-radius:12px; background:var(--surface); }
.alternatives summary { min-height:44px; padding:11px 12px; cursor:pointer; font-weight:700; font-size:13px; }
.alternatives > div { padding:0 10px 10px; display:flex; flex-wrap:wrap; gap:7px; }
.source-modal { max-height:min(86dvh,720px); overflow:auto; }
.source-reference { margin-top:12px; padding:12px; border:1px solid var(--line); border-radius:12px; background:#07182b; }
.source-reference h3 { margin:4px 0 8px; }
.source-reference p { white-space:pre-wrap; overflow-wrap:anywhere; }
.source-row { margin-top:6px; padding-top:6px; border-top:1px solid var(--line); }
.search-return { margin-bottom:-8px; }
body.keyboard-open #tabs { display:none !important; }
body.keyboard-open #content { padding-bottom:max(16px,env(safe-area-inset-bottom)); }
@media (max-width:380px) { .item-quantity { grid-template-columns:1fr; } .item-quantity .primary { grid-column:1; } }
'''
    style.write_text(css, encoding="utf-8", newline="\n")

# ---------------------------------------------------------------------------
# Browser regression: search -> detail -> results, item detail, source evidence
# and keyboard nav hiding.
# ---------------------------------------------------------------------------
path = Path("tests/companion_ui_smoke.cjs")
text = path.read_text(encoding="utf-8")
anchor = '''    await page.locator("#mobile-search-toggle").click();
    await page.locator("#global-search").fill("agumon");
    assert(await page.locator(".search-results .simple-row").count() >= 1, "Search did not return Atlas result");
    await page.locator("#global-search").fill("");
    assert.match(await page.locator(".search-results").innerText(), /Buscar no jogo/);
    await page.locator("[data-act='search-close']").click();
'''
replacement = '''    await page.locator("#mobile-search-toggle").click();
    await page.locator("#global-search").fill("agumon");
    assert(await page.locator(".search-results .simple-row").count() >= 1, "Search did not return Atlas result");
    await page.locator("[data-act='search-atlas']").first().click();
    await page.waitForSelector("[data-act='search-back']");
    await page.locator("[data-act='search-back']").click();
    assert.equal(await page.locator("#global-search").inputValue(), "agumon", "Search query was not restored after detail");
    await page.locator("#global-search").fill("");
    assert.match(await page.locator(".search-results").innerText(), /Buscar no jogo/);
    await page.locator("[data-act='search-close']").click();

    await page.locator("#tabs [data-tab='items']").click();
    await page.locator("[data-act='item-open']").first().click();
    await page.waitForSelector(".item-detail");
    assert.match(await page.locator(".item-detail").innerText(), /Como obter/);
    await page.locator("[data-act='item-back']").click();
    assert(await page.locator("[data-act='item-open']").count() >= 1, "Item list did not return from detail");

    await page.locator("#tabs [data-tab='guide']").click();
    await page.locator("[data-act='guide-mode'][data-mode='read']").click();
    await page.locator("[data-act='source']").first().click();
    await page.waitForSelector(".source-reference");
    assert.match(await page.locator(".source-modal").innerText(), /Início da Aventura|Trecho correspondente/);
    await page.locator("[data-act='source-close']").click();

    await page.evaluate(() => document.body.classList.add("keyboard-open"));
    assert.equal(await page.locator("#tabs").evaluate((node) => getComputedStyle(node).display), "none", "Bottom nav must hide while the software keyboard is open");
    await page.evaluate(() => document.body.classList.remove("keyboard-open"));
'''
if anchor not in text:
    raise RuntimeError("companion smoke search anchor not found")
path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8", newline="\n")

# Reference endpoint contract test.
path = Path("tests/test_experience.py")
text = path.read_text(encoding="utf-8")
if "test_companion_reference_returns_editorial_fragment_only" not in text:
    text += '''\n\ndef test_companion_reference_returns_editorial_fragment_only(tmp_path):
    snapshot = {
        "ok": True, "game": {"slug": "a"}, "games": [{"slug": "a"}],
        "chapters": [{"id": "c1", "title": "Chapter", "blocks": [{
            "id": "b1", "type": "text", "title": "Exact line", "text": "Published editorial text",
            "source_refs": [{"page": 7, "section": 2, "block": 4}],
        }]}],
        "systems": [], "media": [], "progress": {}, "system_state": {}, "items": [], "achievements": [], "answer": {},
    }
    service = companion.CompanionServer(tmp_path / "ui", tmp_path / "assets", lambda slug: dict(snapshot), lambda body: {"ok": True})
    service.host, service.port = "127.0.0.1", 8766
    client = client_for(service); pair(service, client)
    result = request(client, "/api/reference?slug=a&page=7&section=2&block=4").json
    assert result["total"] == 1
    block = result["references"][0]["block"]
    assert block["title"] == "Exact line" and block["text"] == "Published editorial text"
    assert "html" not in block and "raw" not in block
'''
    path.write_text(text, encoding="utf-8", newline="\n")

print("Mobile E phase 3 staged.")
