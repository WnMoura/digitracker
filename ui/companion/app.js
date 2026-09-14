"use strict";

import {guideBlocks, mediaUrl, namespaceFor, nodeById, normalized, patchConfirmedValue, rebaseNextDependent, requestId, systemById, targetKey, versionFor} from "./state.js";
import {createStorage} from "./storage.js";

const content = document.getElementById("content");
const tabs = document.getElementById("tabs");
const connection = document.getElementById("connection");
const messageBox = document.getElementById("message");
const M = {data: null, revisions: {}, storage: null, namespace: "", loadedNamespace: "", connected: false, refreshing: false, retry: 0, retryTimer: 0, flushing: null, message: "", error: false, drafts: {question: "", items: {}}, p: defaults()};

function defaults() {
  return {view: "home", history: [], gameSlug: "", guideMode: "read", guideBlockId: "", atlasMode: "systems", atlasQuery: "", atlasSystemId: "", atlasNodeId: "", atlasEdgeId: "", itemFilter: "all", itemQuery: "", itemId: "", search: false, query: "", library: false, moreMode: "menu", assistant: false, source: null, conflict: null, forget: null, recent: [], pending: []};
}
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num = (node, fallback = 0) => String(Number(node?.card_number || fallback || 0)).padStart(3, "0");
const sourceName = (refs) => (refs || []).map((ref) => ref?.page ? `p.${ref.page}` : ref?.row_id || ref?.table_id || ref?.section || "").filter(Boolean).join(" · ");
const pending = (key) => M.p.pending.includes(key);
const slug = () => M.p.gameSlug || M.data?.game?.slug || "";
function queryMatches(query, ...values) {
  const q = normalized(query).trim();
  if (!q) return true;
  const id = q.match(/^#\s*(\d+)$/)?.[1];
  if (id) return values.some((value) => /^\d+$/.test(String(value ?? "").trim()) && Number(value) === Number(id));
  return normalized(values.map((value) => String(value ?? "")).join(" ")).includes(q);
}

function notify(text, error = false) {
  M.message = String(text || ""); M.error = Boolean(error);
  messageBox.textContent = M.message; messageBox.hidden = !M.message;
  messageBox.classList.toggle("error", M.error);
}
function image(raw) {
  raw = String(raw || "").trim(); if (!raw) return "";
  try { const url = new URL(raw, location.origin); return url.origin === location.origin && url.pathname.startsWith("/assets/") ? esc(url.pathname + url.search) : ""; } catch (_) { return ""; }
}
function cover(game) {
  const url = image(typeof game?.art === "string" ? game.art : game?.art?.url || game?.art?.src);
  return url ? `<img class="game-cover" src="${url}" alt="">` : `<span class="game-cover fallback">◇</span>`;
}
function nodeImage(system, node) {
  return mediaUrl(M.data, M.data?.system_state?.node_media?.[`${system.id}:${node.id}`] || node?.media_id, node?.image?.url || node?.image?.src);
}
function games() { return M.data?.games?.length ? M.data.games.filter((row) => row?.slug) : [M.data?.game].filter(Boolean); }
function setConnected(value) {
  M.connected = value;
  connection.textContent = !M.data?.ok ? "Conexão necessária" : !value ? "Reconectando…" : M.p.pending.length ? `${M.p.pending.length} pendente(s)` : "PC sincronizado";
}
function rememberRecent(kind, id, title, detail = "") {
  M.p.recent = [{kind, id, title, detail}, ...M.p.recent.filter((row) => row.kind !== kind || row.id !== id)].slice(0, 5); persist();
}

let saveTimer = 0;
function persist() {
  if (!M.storage?.available || !M.namespace) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { const value = globalThis.structuredClone ? structuredClone(M.p) : JSON.parse(JSON.stringify(M.p)); delete value.pending; delete value.forget; M.storage.setPresentation(M.namespace, value).catch(() => notify("Não foi possível guardar a navegação neste aparelho.", true)); }, 120);
}
async function restorePresentation() {
  if (!M.namespace || M.namespace === M.loadedNamespace) return;
  const saved = await M.storage?.getPresentation(M.namespace);
  const operations = await M.storage?.pending(M.namespace) || [];
  M.p = {...defaults(), ...(saved || {}), gameSlug: slug(), pending: [...new Set(operations.map((row) => row.target))]}; M.loadedNamespace = M.namespace;
}
function selections() {
  const blocks = guideBlocks(M.data);
  if (!blocks.some((row) => row.id === M.p.guideBlockId)) M.p.guideBlockId = M.data?.objective?.block_id || M.data?.progress?.checkpoint || blocks[0]?.id || "";
  const systems = M.data?.systems || [];
  if (!systems.some((row) => row.id === M.p.atlasSystemId)) M.p.atlasSystemId = M.data?.system_state?.active_system || systems[0]?.id || "";
  const system = systemById(M.data, M.p.atlasSystemId);
  if (!nodeById(system, M.p.atlasNodeId)) M.p.atlasNodeId = M.data?.system_state?.goals?.[system?.id] || system?.nodes?.[0]?.id || "";
  if (!(system?.edges || []).some((row) => row.id === M.p.atlasEdgeId)) M.p.atlasEdgeId = (system?.edges || []).find((row) => row.to === M.p.atlasNodeId || row.from === M.p.atlasNodeId)?.id || "";
  M.p.gameSlug = slug();
}
function retryAfterSeconds(response) {
  const raw = response?.headers?.get?.("Retry-After");
  if (!raw) return 0;
  const numeric = Number(raw);
  if (Number.isFinite(numeric) && numeric >= 0) return Math.min(60, numeric);
  const date = Date.parse(raw);
  return Number.isFinite(date) ? Math.max(0, Math.min(60, (date - Date.now()) / 1000)) : 0;
}
async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: body === undefined ? {} : {"Content-Type":"application/json"}, body: body === undefined ? undefined : JSON.stringify(body), credentials:"same-origin"});
  let value = {}; try { value = await response.json(); } catch (_) {}
  if (!response.ok || value.ok === false) { const error = new Error(value.error || "Não foi possível falar com o DigiTracker."); error.status=response.status; error.value=value; error.retryAfter=retryAfterSeconds(response); throw error; }
  return value;
}
async function restoreSession() {
  try { await api("/session/restore", {}); return "restored"; }
  catch (error) { return error.status === 401 && error.value?.needs_pairing ? "needs-pairing" : "temporary"; }
}
function retry(minimum = 0) {
  clearTimeout(M.retryTimer); const backoff=[1,2,4,8,15,30][Math.min(M.retry++,5)];
  const seconds=Math.max(Number(minimum)||0,backoff);
  M.retryTimer=setTimeout(()=>M.data?poll(true):refresh(true),seconds*1000+Math.floor(Math.random()*250));
}
function query(path) { const mark=path.includes("?")?"&":"?"; return `${path}${mark}slug=${encodeURIComponent(slug())}`; }
async function paged(path, key) {
  const rows=[]; let offset=0, total=0;
  do { const response=await api(query(`${path}?offset=${offset}&limit=100`)); const page=response[key]||[]; rows.push(...page); total=Number(response.total??rows.length); offset+=page.length; if(!page.length)break; } while(rows.length<total);
  return rows;
}
function scrubPresentation() {
  const visibleGuide=new Set(guideBlocks(M.data).filter(row=>!row.hidden).map(row=>row.id));
  const visibleAtlas=new Set((M.data?.systems||[]).flatMap(system=>(system.nodes||[]).map(node=>node.id)));
  const visibleItems=new Set((M.data?.items||[]).map(item=>item.id));
  M.p.recent=(M.p.recent||[]).filter(row=>row.kind==="guide"?visibleGuide.has(row.id):row.kind==="atlas"?visibleAtlas.has(row.id):row.kind==="item"?visibleItems.has(row.id):false).slice(0,5);
}
async function refresh(draw = true) {
  if (M.refreshing || document.hidden) return; M.refreshing=true;
  try {
    const response=await fetch(`/api/state${slug()?`?slug=${encodeURIComponent(slug())}`:""}`,{credentials:"same-origin"});
    let value={}; try{value=await response.json();}catch(_){}
    if(response.status===401){const restored=await restoreSession();if(restored==="restored"){M.refreshing=false;return refresh(draw);}if(restored==="needs-pairing"){setConnected(false);if(draw)disconnected("Este aparelho precisa ser pareado novamente.");return;} }
    if(!response.ok||!value.ok){const error=new Error(value.error||"Conexão indisponível.");error.retryAfter=retryAfterSeconds(response);throw error;}
    const ns=namespaceFor(value,slug()||value.game?.slug), changed=ns!==M.namespace;
    M.data=value;M.revisions={...(value.revisions||{})};M.namespace=ns;M.retry=0;setConnected(true);
    if(changed)await restorePresentation();selections();scrubPresentation();
    if(draw)render();flush().catch(()=>{});
  } catch(error){setConnected(false);if(draw&&!M.data)disconnected(error.message);retry(error.retryAfter);}
  finally{M.refreshing=false;}
}
async function poll(draw = true) {
  if(M.refreshing||document.hidden||!M.data)return;M.refreshing=true;
  try {
    let revisionState;
    try { revisionState=await api(query("/api/revisions")); }
    catch(error){if(error.status===401){const restored=await restoreSession();if(restored==="restored"){M.refreshing=false;return poll(draw);}if(restored==="needs-pairing"){setConnected(false);if(draw)disconnected("Este aparelho precisa ser pareado novamente.");return;}}throw error;}
    const next=revisionState.revisions||{}, changed=Object.keys(next).filter(key=>next[key]!==M.revisions[key]);
    if(changed.includes("guide")){const r=await api(query("/api/guide"));M.data.chapters=r.chapters||[];M.data.progress=r.progress||{};M.data.objective=r.objective||{};}
    if(changed.includes("atlas")){const r=await api(query("/api/atlas/systems"));M.data.systems=r.systems||[];M.data.system_state=r.system_state||{};}
    if(changed.includes("items"))M.data.items=await paged("/api/items","items");
    if(changed.includes("media")){const r=await api(query("/api/media"));M.data.media=r.media||[];}
    if(changed.includes("achievements"))M.data.achievements=await paged("/api/achievements","achievements");
    if(changed.includes("assistant")){const r=await api(query("/api/assistant"));M.data.answer=r.answer||{};}
    if(changed.includes("games")){const r=await api(query("/api/games"));M.data.games=r.games||[];M.data.active_pc_slug=r.active_slug||"";const selected=M.data.games.find(game=>game.slug===slug());if(selected)M.data.game=selected;}
    M.revisions={...next};M.data.revisions={...next};M.retry=0;setConnected(true);selections();scrubPresentation();
    if(draw&&changed.length)render();
    flush().catch(()=>{});
  }catch(error){setConnected(false);retry(error.retryAfter);}
  finally{M.refreshing=false;}
}
function disconnected(detail) {
  tabs.hidden = true;
  content.innerHTML = `<section class="mobile-empty"><span class="eyebrow">CONEXÃO</span><h1>O DigiTracker do PC não está disponível</h1><p>${esc(detail)}</p><button class="primary" data-act="retry">Tentar novamente</button></section>`;
}

function request(values, id = requestId()) {
  return {api_version: 2, request_id: id, slug: slug(), kind: values.kind, action: values.action, value: values.value, target: values.target, expected_definition_revision: M.data?.definition_revision || "", expected_value_version: versionFor(M.data, values)};
}
function addPending(key) { M.p.pending = [...new Set([...M.p.pending, key])]; setConnected(M.connected); }
function dropPending(key) { M.p.pending = M.p.pending.filter((value) => value !== key); setConnected(M.connected); }
async function queue(values, body, status = "pending") {
  if (!M.storage?.available) { notify("O navegador não pode armazenar alterações offline. Tente novamente quando reconectar.", true); return false; }
  const key = targetKey(values);
  await M.storage.enqueue({id: body.request_id, namespace: M.namespace, createdAt: Date.now(), status, target: key, values, body});
  addPending(key); notify("Pendente no telefone. Será enviada ao PC quando reconectar."); return true;
}
async function submit(values) {
  const key = targetKey(values), body = request(values);
  if (M.demo) {
    const result = {ok: true, kind: values.kind, target: key, value: values.value, item_id: values.target?.item_id,
      value_version: versionFor(M.data, values) + 1};
    M.data = patchConfirmedValue(M.data, result); notify("Guardado no modo demo."); render(); return;
  }
  if (!M.connected || !navigator.onLine) { await queue(values, body); render(); return; }
  addPending(key); render();
  try {
    const result = await api("/api/action", body);
    M.data = patchConfirmedValue(M.data, result); dropPending(key); notify("Salvo no PC."); persist(); render();
  } catch (error) {
    if (error.value?.conflict) { await queue(values, body, "conflict"); M.p.conflict = {id: body.request_id, target: key, values, body, server: error.value}; notify("Existe uma alteração diferente no PC.", true); }
    else await queue(values, body);
    render();
  }
}
async function flush() {
  if (!M.connected || !M.storage?.available || M.flushing) return M.flushing;
  M.flushing = (async () => {
    const blocked = new Set();
    const operations = await M.storage.pending(M.namespace);
    for (let index = 0; index < operations.length; index += 1) {
      const op = operations[index];
      if (op.status === "conflict") { blocked.add(op.target); continue; }
      if (blocked.has(op.target)) continue;
      try {
        // Replay the exact request first. A lost ACK is recovered through the
        // persistent receipt for this request_id; a newer PC value must not be
        // substituted before the server decides whether this intent conflicts.
        const result = await api("/api/action", op.body);
        M.data = patchConfirmedValue(M.data, result);
        await M.storage.remove(op.id);
        dropPending(op.target);

        // Successive changes on the same semantic target are dependent. Only
        // the next intent advances to this confirmed version. If it succeeds,
        // it advances the following one in turn.
        const dependent = rebaseNextDependent(operations, index, op.target, result.value_version);
        if (dependent) {
          operations[dependent.index] = dependent.operation;
          await M.storage.update(dependent.operation.id, {
            values: dependent.operation.values,
            body: dependent.operation.body,
          });
        }
        notify("Alteração pendente salva no PC.");
      } catch (error) {
        if (error.value?.conflict) { await M.storage.update(op.id, {status: "conflict", server: error.value}); M.p.conflict ||= {id: op.id, target: op.target, values: op.values, body: op.body, server: error.value}; blocked.add(op.target); }
        else break;
      }
    }
    persist(); render();
  })();
  try { await M.flushing; } finally { M.flushing = null; }
}
async function resolveConflict(mode) {
  const row = M.p.conflict; if (!row) return;
  if (mode === "pc") { await M.storage?.remove(row.id); dropPending(row.target); M.p.conflict = null; notify("A alteração do PC foi mantida."); return refresh(true); }
  try {
    const result = await api("/api/action", {...row.body, request_id: requestId(), expected_value_version: Number(row.server?.value_version || 0)});
    M.data = patchConfirmedValue(M.data, result); await M.storage?.remove(row.id); dropPending(row.target); M.p.conflict = null; notify("Sua alteração foi aplicada no PC.");
  } catch (error) { notify(error.message, true); }
  render();
}

function sourceButton(refs) {
  const ref = (refs || [])[0]; if (!ref) return "";
  return `<button class="text-action" data-act="source" data-source="${esc(JSON.stringify(ref))}">Consultar fonte · ${esc(sourceName([ref]) || "trecho")}</button>`;
}
function home() {
  const blocks = guideBlocks(M.data), guide = blocks.find((row) => row.id === M.data?.progress?.checkpoint) || blocks[0];
  const system = systemById(M.data, M.data?.system_state?.active_system), goal = nodeById(system, M.data?.system_state?.goals?.[system?.id]);
  const achievements = M.data.achievements || [], earned = achievements.filter((row) => row.earned).length;
  return `<section class="mobile-game-hero">${cover(M.data.game)}<div><span class="eyebrow">JOGANDO AGORA</span><h1>${esc(M.data.game?.title || "Jogo atual")}</h1><p>${esc(M.data.game?.platform || "Jogo")}</p></div></section>
  <section class="primary-panel"><span class="eyebrow">JORNADA</span><h2>${esc(guide?.title || "Comece sua jornada")}</h2><p>${esc(guide?.text || "Escolha um guia para continuar.")}</p><button class="primary" data-act="continue-guide">Continuar</button></section>
  <section class="summary-grid"><button data-act="tab" data-tab="guide"><b>${(M.data.progress?.completed || []).length}</b><span>Etapas concluídas</span></button><button data-act="tab" data-tab="more" data-more="achievements"><b>${earned}/${achievements.length || 0}</b><span>Conquistas</span></button></section>
  <section class="list-section"><div class="section-title"><h2>Objetivo do Atlas</h2><button class="text-action" data-act="tab" data-tab="atlas">Explorar</button></div>${goal ? `<button class="objective-row" data-act="atlas-node" data-system="${esc(system.id)}" data-node="${esc(goal.id)}"><span class="node-mini">${nodeImage(system, goal) ? `<img src="${nodeImage(system, goal)}" alt="">` : "◇"}</span><span><small>Card #${num(goal)}</small><b>${esc(goal.label)}</b><em>${esc(system.title)}</em></span><span>›</span></button>` : `<button class="objective-row" data-act="tab" data-tab="atlas"><span class="node-mini">◇</span><span><b>Explorar Atlas</b><em>Nenhum objetivo fixado.</em></span><span>›</span></button>`}</section>
  <section class="list-section"><div class="section-title"><h2>Consultados recentemente</h2></div>${M.p.recent.map((row) => `<button class="simple-row" data-act="recent" data-kind="${esc(row.kind)}" data-id="${esc(row.id)}"><span>${row.kind === "atlas" ? "◇" : row.kind === "item" ? "▣" : "▤"}</span><span><b>${esc(row.title)}</b><small>${esc(row.detail)}</small></span><span>›</span></button>`).join("") || "<p class='muted'>As consultas feitas no celular aparecerão aqui.</p>"}</section>`;
}
function guideCard(block) {
  if (!block) return `<section class="mobile-empty"><h1>Guia indisponível</h1><p>Publique um guia no PC para lê-lo no telefone.</p></section>`;
  const all = guideBlocks(M.data), done = (M.data.progress?.completed || []).includes(block.id), favorite = (M.data.progress?.favorites || []).includes(block.id);
  if (block.hidden) return `<article class="reading-card"><span class="eyebrow">SPOILER</span><h1>Spoiler oculto</h1><p>Revele este trecho quando quiser vê-lo.</p><button class="primary" data-act="reveal" data-block="${esc(block.id)}">Revelar trecho</button></article>`;
  return `<article class="reading-card"><div class="card-meta"><span>ETAPA</span><b>#${String(all.findIndex((row) => row.id === block.id) + 1).padStart(3, "0")}</b></div><h1>${esc(block.title || "Etapa")}</h1>${block.text ? `<p>${esc(block.text)}</p>` : ""}${(block.items || []).map((item) => `<p>• ${esc(item.text || item)}</p>`).join("")}<div class="reading-actions"><button class="${done ? "selected-action" : "outline"}" data-act="complete" data-block="${esc(block.id)}" data-value="${done ? "false" : "true"}">${done ? "✓ Concluído" : "○ Concluir"}</button><button class="outline" data-act="checkpoint" data-block="${esc(block.id)}">Retomar daqui</button><button class="icon-text" data-act="favorite" data-block="${esc(block.id)}" data-value="${favorite ? "false" : "true"}" aria-label="Favoritar">${favorite ? "★" : "☆"}</button></div>${sourceButton(block.source_refs)}</article>`;
}
function assistant(block) {
  if (!M.p.assistant) return "";
  const answer = M.data.answer || {};
  return `<section class="assistant-panel"><span class="eyebrow">ASSISTENTE</span><h2>Perguntar sobre este trecho</h2><p>A pergunta e o trecho serão enviados ao provedor configurado no PC.</p><form data-form="question"><textarea name="question" maxlength="2000" placeholder="Qual é o próximo passo?">${esc(M.drafts.question)}</textarea><input name="block" type="hidden" value="${esc(block?.id || "")}"><button class="primary" ${!M.connected || answer.running ? "disabled" : ""}>${answer.running ? "Consultando…" : "Perguntar à IA"}</button></form>${answer.answer ? `<p class="answer">${esc(answer.answer)}</p>` : ""}${answer.error ? `<p class="warning">${esc(answer.error)}</p>` : ""}</section>`;
}
function guide() {
  const rows = guideBlocks(M.data), selected = rows.find((row) => row.id === M.p.guideBlockId) || rows[0], index = Math.max(0, rows.indexOf(selected)), mode = M.p.guideMode;
  const list = mode === "favorites" ? rows.filter((row) => (M.data.progress?.favorites || []).includes(row.id)) : rows;
  return `<section class="screen-heading"><span class="eyebrow">GUIA</span><h1>${mode === "read" ? "Leitura" : mode === "favorites" ? "Favoritos" : "Índice"}</h1></section><div class="mobile-segmented"><button class="${mode === "index" ? "active" : ""}" data-act="guide-mode" data-mode="index">Índice</button><button class="${mode === "read" ? "active" : ""}" data-act="guide-mode" data-mode="read">Leitura</button><button class="${mode === "favorites" ? "active" : ""}" data-act="guide-mode" data-mode="favorites">Favoritos</button></div>${mode === "read" ? `${guideCard(selected)}<div class="pager"><button class="outline" data-act="guide-step" data-dir="-1" ${index <= 0 ? "disabled" : ""}>← Anterior</button><button class="outline" data-act="guide-step" data-dir="1" ${index >= rows.length - 1 ? "disabled" : ""}>Próximo →</button></div><button class="assistant-trigger" data-act="assistant">✦ Perguntar sobre este trecho</button>${assistant(selected)}` : `<section class="list-section">${list.map((row) => `<button class="guide-index-row" data-act="guide-open" data-block="${esc(row.id)}"><span>#${String(rows.indexOf(row) + 1).padStart(3,"0")}</span><span><b>${esc(row.title || "Etapa")}</b><small>${esc(row.chapterTitle || "")}</small></span><span>${(M.data.progress?.completed || []).includes(row.id) ? "✓" : "›"}</span></button>`).join("") || "<p class='muted'>Nenhum favorito neste guia.</p>"}</section>`}`;
}
function requirements(system, edge) {
  if (!edge) return "<p class='muted'>Escolha uma rota para consultar as condições.</p>";
  if (!(edge.requirements || []).length) return "<p class='muted'>Sem requisitos nesta fonte.</p>";
  const state = M.data.system_state || {};
  const legacy = new Set(state.completed_requirements || []);
  const scoped = new Set(state.completed_requirement_targets || []);
  const scopedMode = Boolean(state.requirements_scoped || scoped.size);
  return edge.requirements.map((rule) => {
    if (rule.none || rule.text === "Prerequisites: None") return "<p class='requirement-note'>Sem requisitos nesta fonte.</p>";
    const unknown = rule.condition?.op === "unknown" || rule.availability === "unknown", key = `requirement:${system.id}:${edge.id}:${rule.id}`;
    const checked = scopedMode ? scoped.has(key) : legacy.has(rule.id);
    return `<label class="mobile-requirement ${unknown ? "unknown" : ""}"><input type="checkbox" data-act="requirement" data-system="${esc(system.id)}" data-edge="${esc(edge.id)}" data-requirement="${esc(rule.id)}" ${checked ? "checked" : ""} ${unknown || pending(key) ? "disabled" : ""}><span><b>${esc(rule.mode === "item" ? "Item do jogo" : rule.mode === "jogress" ? "Combinação" : "Requisito")}</b>${esc(rule.text || "Condição sem descrição")}${unknown ? "<small>Disponibilidade desconhecida: a fonte não define esta regra.</small>" : ""}${sourceName(rule.source_refs) ? `<small>Fonte: ${esc(sourceName(rule.source_refs))}</small>` : ""}</span></label>`;
  }).join("");
}
function routeNode(system, node, role, active) {
  if (!node) return ""; const art = nodeImage(system, node);
  return `<button class="route-node ${active ? "active" : ""}" data-act="atlas-node" data-system="${esc(system.id)}" data-node="${esc(node.id)}">${art ? `<img src="${art}" alt="">` : "<span class='route-fallback'>◇</span>"}<span><small>${esc(role)} · #${num(node)}</small><b>${esc(node.label)}</b><em>${esc(node.stage || node.group || "Entidade")}</em></span></button>`;
}
function atlasRoute(system, node) {
  const edges = system.edges || [], incoming = edges.filter((row) => row.to === node.id), outgoing = edges.filter((row) => row.from === node.id);
  const edge = edges.find((row) => row.id === M.p.atlasEdgeId) || incoming[0] || outgoing[0], origin = edge ? nodeById(system, edge.from) : null, destination = edge ? nodeById(system, edge.to) : null;
  const from = edge?.to === node.id ? origin : null, to = edge?.from === node.id ? destination : null;
  const alternatives = (rows, endpoint) => rows.filter((row) => row.id !== edge?.id).slice(0,4).map((row) => { const item = nodeById(system, row[endpoint]); return item ? `<button class="alt-chip" data-act="atlas-edge" data-edge="${esc(row.id)}">${esc(item.label)} · #${num(item)}</button>` : ""; }).join("");
  return `<section class="atlas-route"><div class="atlas-route-head"><button class="text-action" data-act="atlas-mode" data-mode="entities">← Entidades</button><button class="text-action" data-act="atlas-mode" data-mode="systems">Sistemas</button></div><div class="route-stack">${routeNode(system, from, "Origem", false)}${from ? "<span class='route-arrow'>↓</span>" : ""}${routeNode(system, node, "Selecionado", true)}${to ? "<span class='route-arrow'>↓</span>" : ""}${routeNode(system, to, "Destino", false)}</div>${incoming.length > 1 ? `<div class="alternatives"><b>Outras origens · ${incoming.length - 1}</b><div>${alternatives(incoming, "from")}</div></div>` : ""}${outgoing.length > 1 ? `<div class="alternatives"><b>Outros destinos · ${outgoing.length - 1}</b><div>${alternatives(outgoing, "to")}</div></div>` : ""}<section class="route-detail">${nodeImage(system,node) ? `<img class="detail-image" src="${nodeImage(system,node)}" alt="${esc(node.label)}">` : ""}<div class="card-meta"><span>ENTIDADE</span><b>#${num(node)}</b></div><h2>${esc(node.label)}</h2><p>${esc(node.subtitle || node.stage || "Rota documentada no Atlas.")}</p><button class="primary" data-act="goal" data-system="${esc(system.id)}" data-node="${esc(node.id)}">${M.data.system_state?.goals?.[system.id] === node.id ? "✓ Objetivo fixado" : "Fixar objetivo"}</button><h3>Requisitos desta rota</h3>${requirements(system,edge)}${sourceButton(edge?.source_refs || node.source_refs)}</section></section>`;
}
function atlas() {
  const systems = M.data.systems || [], atlasQuery = normalized(M.p.atlasQuery || "");
  if (!systems.length) return "<section class='mobile-empty'><span class='eyebrow'>ATLAS</span><h1>Nenhum Atlas publicado</h1><p>Crie e aprove um sistema no PC para consultá-lo aqui.</p></section>";
  const system = systemById(M.data, M.p.atlasSystemId) || systems[0], node = nodeById(system, M.p.atlasNodeId) || system.nodes?.[0];
  const matchingSystems = systems.filter((row) => queryMatches(atlasQuery, row.title, row.id));
  const matchingNodes = (system.nodes || []).filter((row) => queryMatches(atlasQuery, row.label, row.id, row.card_number, row.stage || row.group));
  const atlasSearch = `<label class="local-search"><span aria-hidden="true">⌕</span><input id="atlas-local-search" type="search" value="${esc(M.p.atlasQuery || "")}" placeholder="Buscar sistema, entidade ou #ID" aria-label="Buscar no Atlas"></label>`;
  let body = "";
  if (M.p.atlasMode === "systems") body = `${atlasSearch}<section class="list-section">${matchingSystems.map((row) => `<button class="system-row" data-act="atlas-system" data-system="${esc(row.id)}"><span>◇</span><span><b>${esc(row.title)}</b><small>${row.nodes?.length || 0} entidades</small></span><span>›</span></button>`).join("") || "<p class='muted'>Nenhum sistema corresponde à busca.</p>"}</section>`;
  else if (M.p.atlasMode === "entities") body = `${atlasSearch}<section class="list-section"><div class="section-title"><h2>${esc(system.title)}</h2><button class="text-action" data-act="atlas-mode" data-mode="systems">Sistemas</button></div>${matchingNodes.map((row,index) => `<button class="entity-row" data-act="atlas-node" data-system="${esc(system.id)}" data-node="${esc(row.id)}">${nodeImage(system,row) ? `<img src="${nodeImage(system,row)}" alt="">` : "<span class='node-mini'>◇</span>"}<span><small>#${num(row,index+1)}</small><b>${esc(row.label)}</b><em>${esc(row.stage || row.group || "Entidade")}</em></span><span>›</span></button>`).join("") || "<p class='muted'>Nenhuma entidade corresponde à busca.</p>"}</section>`;
  else body = atlasRoute(system,node);
  return `<section class="screen-heading"><span class="eyebrow">ATLAS DE SISTEMAS</span><h1>${M.p.atlasMode === "route" ? "Rota" : "Atlas"}</h1></section>${body}`;
}
function items() {
  const all = M.data.items || [], filter = M.p.itemFilter, itemQuery = normalized(M.p.itemQuery || "");
  const rows = all.filter((item) => (filter === "have" ? Number(item.quantity) > 0 : filter === "missing" ? item.quantity === 0 || item.quantity == null : true) && queryMatches(itemQuery, item.name, item.id, ...(item.aliases || [])));
  return `<section class="screen-heading"><span class="eyebrow">ITENS</span><h1>Inventário</h1></section><label class="local-search"><span aria-hidden="true">⌕</span><input id="item-local-search" type="search" value="${esc(M.p.itemQuery || "")}" placeholder="Buscar item ou #ID" aria-label="Buscar item"></label><div class="mobile-segmented"><button class="${filter === "all" ? "active" : ""}" data-act="item-filter" data-filter="all">Todos</button><button class="${filter === "have" ? "active" : ""}" data-act="item-filter" data-filter="have">Tenho</button><button class="${filter === "missing" ? "active" : ""}" data-act="item-filter" data-filter="missing">Faltam</button></div><section class="list-section">${rows.map((item) => { const value = item.quantity == null ? "" : Number(item.quantity), status = item.quantity == null ? "Quantidade desconhecida" : Number(item.quantity) === 0 ? "Não tenho" : `Tenho · ${item.quantity}`; return `<article class="item-card"><div><b>${esc(item.name)}</b><small>${esc(status)}</small><p>${esc(item.description || item.source || "Origem e uso documentados no guia.")}</p></div><label>Qtd.<input inputmode="numeric" type="number" min="0" step="1" data-item-input="${esc(item.id)}" value="${M.drafts.items[item.id] ?? value}" placeholder="—"></label><button class="outline" data-act="item-save" data-item="${esc(item.id)}">Salvar</button></article>`; }).join("") || "<p class='muted'>Nenhum item corresponde a este filtro.</p>"}</section>`;
}
function achievements() {
  return `<section class="screen-heading"><span class="eyebrow">CONQUISTAS</span><h1>Conquistas</h1></section><section class="list-section">${(M.data.achievements || []).map((row,index) => `<article class="achievement-row"><span>${row.earned ? "✓" : row.hardcore ? "◆" : "○"}</span><div><b>${esc(row.name)}</b><p>${esc(row.desc || "")}</p><small>#${String(index+1).padStart(3,"0")} · ${row.earned ? "Conquistada" : "Pendente"}</small></div></article>`).join("") || "<p class='muted'>Nenhuma conquista sincronizada.</p>"}</section>`;
}
function more() {
  if (M.p.moreMode === "achievements") return `<button class="text-action back-row" data-act="more-mode" data-mode="menu">← Mais</button>${achievements()}`;
  if (M.p.moreMode === "favorites") { const values = guideBlocks(M.data).filter((row) => (M.data.progress?.favorites || []).includes(row.id)); return `<button class="text-action back-row" data-act="more-mode" data-mode="menu">← Mais</button><section class="screen-heading"><span class="eyebrow">FAVORITOS</span><h1>Favoritos</h1></section><section class="list-section">${values.map((row) => `<button class="guide-index-row" data-act="guide-open" data-block="${esc(row.id)}"><span>★</span><span><b>${esc(row.title)}</b><small>${esc(row.chapterTitle || "Guia")}</small></span><span>›</span></button>`).join("") || "<p class='muted'>Favorite um trecho do guia para encontrá-lo aqui.</p>"}</section>`; }
  if (M.p.moreMode === "connection") return `<button class="text-action back-row" data-act="more-mode" data-mode="menu">← Mais</button><section class="screen-heading"><span class="eyebrow">CONEXÃO</span><h1>Este aparelho</h1></section><section class="connection-panel"><b>${M.connected ? "PC sincronizado" : "Aguardando o PC"}</b><p>${M.data?.companion?.server_url ? "Conectado à rede local." : "Mantenha o DigiTracker aberto no PC e conectado à mesma rede."}</p><dl><div><dt>Pendências</dt><dd>${M.p.pending.length}</dd></div><div><dt>Armazenamento offline</dt><dd>${M.storage?.available ? "Ativo" : "Indisponível"}</dd></div></dl><button class="outline" data-act="forget">Esquecer este aparelho</button></section>`;
  return `<section class="screen-heading"><span class="eyebrow">MAIS</span><h1>Mais opções</h1></section><section class="list-section"><button class="simple-row" data-act="more-mode" data-mode="achievements"><span>♜</span><span><b>Conquistas</b><small>Acompanhe os marcos recebidos do PC</small></span><span>›</span></button><button class="simple-row" data-act="more-mode" data-mode="favorites"><span>★</span><span><b>Favoritos</b><small>Trechos guardados no guia</small></span><span>›</span></button><button class="simple-row" data-act="more-mode" data-mode="connection"><span>⌁</span><span><b>Conexão e aparelho</b><small>Sincronização com o DigiTracker</small></span><span>›</span></button></section>`;
}
function search() {
  const q = normalized(M.p.query);
  const guideRows = guideBlocks(M.data).filter((row) => !row.hidden && queryMatches(q, row.title, row.text, row.card_number));
  const atlasRows = (M.data.systems || []).flatMap((system) => (system.nodes || []).map((node) => ({system,node}))).filter(({system,node}) => queryMatches(q, node.label, node.id, node.card_number, system.title));
  const itemRows = (M.data.items || []).filter((row) => queryMatches(q, row.name, row.id));
  const group = (title, body) => `<section><h2>${title}</h2>${body || "<p class='muted'>Nenhum resultado.</p>"}</section>`;
  return `<div class="overlay-screen"><div class="overlay-head"><button class="mobile-icon-button" data-act="search-close" aria-label="Voltar">←</button><label><span class="sr-only">Buscar</span><input id="global-search" type="search" value="${esc(M.p.query)}" placeholder="Buscar guia, Atlas ou item" autofocus></label></div><div class="search-results">${q ? group("Guia", guideRows.map((row) => `<button class="simple-row" data-act="search-guide" data-block="${esc(row.id)}"><span>▤</span><span><b>${esc(row.title)}</b><small>${esc(row.chapterTitle || "")}</small></span><span>›</span></button>`).join("")) + group("Atlas", atlasRows.map(({system,node}) => `<button class="simple-row" data-act="search-atlas" data-system="${esc(system.id)}" data-node="${esc(node.id)}"><span>◇</span><span><b>${esc(node.label)}</b><small>#${num(node)} · ${esc(system.title)}</small></span><span>›</span></button>`).join("")) + group("Itens", itemRows.map((row) => `<button class="simple-row" data-act="search-item" data-item="${esc(row.id)}"><span>▣</span><span><b>${esc(row.name)}</b><small>${esc(row.id)}</small></span><span>›</span></button>`).join("")) : "<section class='mobile-empty'><h2>Buscar no jogo</h2><p>Use nome ou #ID. A busca ignora acentos e maiúsculas.</p></section>"}</div></div>`;
}
function library() {
  return `<div class="overlay-screen"><div class="overlay-head"><button class="mobile-icon-button" data-act="library-close" aria-label="Fechar">×</button><h1>Biblioteca</h1></div><div class="library-list">${games().map((game) => `<button class="library-game ${game.slug === slug() ? "selected" : ""}" data-act="select-game" data-game="${esc(game.slug)}">${cover(game)}<span><b>${esc(game.title || game.slug)}</b><small>${esc(game.platform || "Jogo")}</small></span><span>${game.slug === slug() ? "✓" : "›"}</span></button>`).join("") || "<p class='muted'>Nenhum jogo recebido do PC.</p>"}</div></div>`;
}
function sourceModal() {
  const ref = M.p.source; if (!ref) return "";
  return `<div class="modal-backdrop"><section class="mobile-modal" role="dialog" aria-modal="true"><button class="mobile-icon-button modal-close" data-act="source-close" aria-label="Fechar">×</button><span class="eyebrow">FONTE</span><h2>Referência local</h2><p>${esc(sourceName([ref]) || "Trecho documentado")}</p><p>A fonte original é preservada no PC. O Atlas mantém esta referência ao caminho consultado.</p>${ref.url ? `<a class="outline link-button" href="${esc(ref.url)}" target="_blank" rel="noreferrer">Abrir fonte externa</a>` : ""}</section></div>`;
}
function conflictModal() {
  const row = M.p.conflict; if (!row) return "";
  return `<div class="modal-backdrop"><section class="mobile-modal" role="dialog" aria-modal="true"><span class="eyebrow">CONFLITO</span><h2>Esta alteração mudou no PC</h2><p>PC: <b>${esc(String(row.server?.value ?? "não informado"))}</b><br>Telefone: <b>${esc(String(row.values?.value ?? "não informado"))}</b></p><p>As demais alterações continuam independentes.</p><div class="modal-actions"><button class="outline" data-act="conflict-pc">Usar valor do PC</button><button class="primary" data-act="conflict-phone">Aplicar minha alteração</button></div></section></div>`;
}
function body() { if (M.p.view === "guide") return guide(); if (M.p.view === "atlas") return atlas(); if (M.p.view === "items") return items(); if (M.p.view === "more") return more(); return home(); }
function render() {
  if (!M.data?.ok) return;
  tabs.hidden = false;
  tabs.querySelectorAll("button").forEach((button) => { const active = button.dataset.tab === M.p.view; button.classList.toggle("active", active); button.setAttribute("aria-current", active ? "page" : "false"); });
  content.innerHTML = `<div class="mobile-screen">${body()}</div>${M.p.search ? search() : ""}${M.p.library ? library() : ""}${sourceModal()}${conflictModal()}${forgetModal()}`; setConnected(M.connected);
}
function nav(view) { if (M.p.view !== view) M.p.history = [...M.p.history, M.p.view].slice(-20); M.p.view = view; window.scrollTo({top:0,behavior:"instant"}); persist(); render(); }
async function ask(form) {
  if (!M.connected) return notify("A pergunta à IA exige conexão com o PC.", true);
  const values = new FormData(form), question = String(values.get("question") || "").trim();
  if (!question) return notify("Escreva uma pergunta antes de enviar.", true);
  M.drafts.question = question;
  try { await api("/api/action", {kind:"ask",slug:slug(),block_id:values.get("block"),question}); notify("Pergunta enviada ao PC."); render(); } catch (error) { notify(error.message,true); }
}
async function forget(mode = "ask") {
  const rows=await M.storage?.pending(M.namespace)||[];
  if(rows.length&&mode==="ask"){M.p.forget={count:rows.length};return render();}
  if(mode==="cancel"){M.p.forget=null;return render();}
  if(rows.length&&mode==="sync"){
    if(!M.connected)return notify("Reconecte ao PC para sincronizar antes de esquecer este aparelho.",true);
    await flush();const remaining=await M.storage?.pending(M.namespace)||[];
    if(remaining.length){M.p.forget={count:remaining.length};notify("Ainda há pendências ou conflitos. Revise-os ou descarte antes de esquecer.",true);return render();}
  }
  if(mode==="discard")for(const row of rows)await M.storage.remove(row.id);
  const remaining=await M.storage?.pending(M.namespace)||[];
  if(remaining.length)return;
  try{await api("/api/device/forget",{});M.connected=false;M.p.pending=[];M.p.forget=null;notify("Aparelho esquecido. Leia um novo QR Code para reconectar.");disconnected("Este aparelho foi removido do DigiTracker.");}catch(error){notify(error.message,true);}
}
function forgetModal(){
  const row=M.p.forget;if(!row)return "";
  return `<div class="modal-backdrop"><section class="mobile-modal" role="dialog" aria-modal="true"><span class="eyebrow">APARELHO</span><h2>Há ${Number(row.count)||0} alteração(ões) pendente(s)</h2><p>Escolha se quer sincronizar com o PC antes de remover a autorização deste aparelho ou descartar apenas estas pendências locais.</p><div class="modal-actions"><button class="outline" data-act="forget-cancel">Cancelar</button><button class="outline" data-act="forget-discard">Descartar e esquecer</button><button class="primary" data-act="forget-sync">Sincronizar antes</button></div></section></div>`;
}
async function click(event) {
  const button = event.target.closest("[data-act],[data-tab]"); if (!button) return;
  const a = button.dataset.act;
  if (!a && button.dataset.tab) return nav(button.dataset.tab);
  if (a === "retry") return refresh(true);
  if (a === "tab") { if (button.dataset.more) M.p.moreMode = button.dataset.more; return nav(button.dataset.tab); }
  if (a === "continue-guide") { M.p.guideMode = "read"; return nav("guide"); }
  if (a === "guide-mode") { M.p.guideMode = button.dataset.mode; persist(); return render(); }
  if (a === "guide-open") { M.p.guideBlockId = button.dataset.block; M.p.guideMode = "read"; const row = guideBlocks(M.data).find((item) => item.id === button.dataset.block); rememberRecent("guide",button.dataset.block,row?.title || "Guia",row?.chapterTitle || ""); return nav("guide"); }
  if (a === "guide-step") { const rows = guideBlocks(M.data), index = rows.findIndex((row) => row.id === M.p.guideBlockId); M.p.guideBlockId = rows[index+Number(button.dataset.dir)]?.id || M.p.guideBlockId; persist(); render(); return window.scrollTo({top:0,behavior:"instant"}); }
  if (["complete","favorite","reveal","checkpoint"].includes(a)) {
    const action = a === "complete" ? "complete" : a === "favorite" ? "favorite" : a === "reveal" ? "reveal" : "checkpoint";
    const value = action === "checkpoint" ? button.dataset.block : action === "reveal" ? true : button.dataset.value === "true";
    return submit({kind:"progress",action,value,target:{block_id:button.dataset.block}});
  }
  if (a === "assistant") { M.p.assistant = !M.p.assistant; persist(); return render(); }
  if (a === "atlas-mode") { M.p.atlasMode = button.dataset.mode; persist(); return render(); }
  if (a === "atlas-system") { M.p.atlasSystemId=button.dataset.system;M.p.atlasNodeId="";M.p.atlasMode="entities";selections();persist();return render(); }
  if (a === "atlas-node") { M.p.atlasSystemId=button.dataset.system;M.p.atlasNodeId=button.dataset.node;M.p.atlasEdgeId="";M.p.atlasMode="route";selections(); const sys=systemById(M.data,M.p.atlasSystemId), node=nodeById(sys,M.p.atlasNodeId);rememberRecent("atlas",node?.id,node?.label || "Atlas",sys?.title || ""); return nav("atlas"); }
  if (a === "atlas-edge") { M.p.atlasEdgeId=button.dataset.edge;persist();return render(); }
  if (a === "goal") { const current=M.data.system_state?.goals?.[button.dataset.system], value=current === button.dataset.node ? "" : button.dataset.node; return submit({kind:"goal",value,target:{system_id:button.dataset.system,node_id:value}}); }
  if (a === "item-filter") { M.p.itemFilter=button.dataset.filter;persist();return render(); }
  if (a === "item-save") { const field=Array.from(content.querySelectorAll("[data-item-input]")).find((row)=>row.dataset.itemInput===button.dataset.item); const raw=String(M.drafts.items[button.dataset.item] ?? field?.value ?? "").trim(), value=raw === "" ? null : Number(raw); if (value !== null && (!Number.isInteger(value)||value<0)) return notify("Informe uma quantidade inteira, zero ou deixe em branco.",true); const item=(M.data.items||[]).find((row)=>row.id===button.dataset.item);rememberRecent("item",button.dataset.item,item?.name || "Item","Inventário"); return submit({kind:"item",value,target:{item_id:button.dataset.item}}); }
  if (a === "more-mode") { M.p.moreMode=button.dataset.mode;persist();return render(); }
  if (a === "forget") return forget("ask");
  if (a === "forget-sync") return forget("sync");
  if (a === "forget-discard") return forget("discard");
  if (a === "forget-cancel") return forget("cancel");
  if (a === "search-close") { M.p.search=false;persist();return render(); }
  if (a === "library-close") { M.p.library=false;return render(); }
  if (a === "select-game") { M.p={...defaults(),gameSlug:button.dataset.game}; M.loadedNamespace="";notify("Contexto de consulta alterado no telefone.");return refresh(true); }
  if (a === "search-guide") { M.p.search=false;M.p.guideBlockId=button.dataset.block;M.p.guideMode="read";return nav("guide"); }
  if (a === "search-atlas") { M.p.search=false;M.p.atlasSystemId=button.dataset.system;M.p.atlasNodeId=button.dataset.node;M.p.atlasMode="route";selections();return nav("atlas"); }
  if (a === "search-item") { M.p.search=false;M.p.itemId=button.dataset.item;return nav("items"); }
  if (a === "recent") { if(button.dataset.kind==="guide"){M.p.guideBlockId=button.dataset.id;M.p.guideMode="read";return nav("guide");}if(button.dataset.kind==="atlas"){const sys=(M.data.systems||[]).find((row)=>(row.nodes||[]).some((node)=>node.id===button.dataset.id));M.p.atlasSystemId=sys?.id||"";M.p.atlasNodeId=button.dataset.id;M.p.atlasMode="route";selections();return nav("atlas");}M.p.itemId=button.dataset.id;return nav("items"); }
  if (a === "source") { try { M.p.source=JSON.parse(button.dataset.source||"{}"); } catch (_) {} return render(); }
  if (a === "source-close") { M.p.source=null;return render(); }
  if (a === "conflict-pc") return resolveConflict("pc");
  if (a === "conflict-phone") return resolveConflict("phone");
}
function change(event) { const input=event.target;if(input.matches("[data-act='requirement']")) return submit({kind:"requirement",value:input.checked,target:{system_id:input.dataset.system,edge_id:input.dataset.edge,requirement_id:input.dataset.requirement}}); }
function input(event) {
  const field=event.target;
  if(field.matches("#global-search")){M.p.query=field.value;render();requestAnimationFrame(()=>document.getElementById("global-search")?.focus());}
  if(field.matches("#atlas-local-search")){M.p.atlasQuery=field.value;persist();render();requestAnimationFrame(()=>document.getElementById("atlas-local-search")?.focus());}
  if(field.matches("#item-local-search")){M.p.itemQuery=field.value;persist();render();requestAnimationFrame(()=>document.getElementById("item-local-search")?.focus());}
  if(field.matches("[data-item-input]"))M.drafts.items[field.dataset.itemInput]=field.value;
  if(field.matches("textarea[name='question']"))M.drafts.question=field.value;
}
content.addEventListener("click",click);content.addEventListener("change",change);content.addEventListener("input",input);content.addEventListener("submit",(event)=>{if(event.target.matches("[data-form='question']")){event.preventDefault();ask(event.target);}});
tabs.addEventListener("click",click);
document.getElementById("mobile-menu")?.addEventListener("click",()=>{M.p.library=true;render();});
document.getElementById("mobile-search-toggle")?.addEventListener("click",()=>{M.p.search=true;render();requestAnimationFrame(()=>document.getElementById("global-search")?.focus());});
document.getElementById("mobile-search-close")?.addEventListener("click",()=>{M.p.search=false;render();});
document.getElementById("mobile-search-input")?.addEventListener("input",(event)=>{M.p.query=event.target.value;M.p.search=true;render();});
function demoSnapshot() {
  return {
    ok: true, api_version: 2, definition_revision: "demo-v1",
    companion: {installation_id: "demo", account_scope: "demo", server_url: ""},
    game: {slug: "demo", title: "Digimon World Re:Digitize", platform: "PSP"},
    games: [{slug: "demo", title: "Digimon World Re:Digitize", platform: "PSP"}],
    chapters: [{id: "intro", title: "Início", blocks: [
      {id: "b1", title: "Início da Aventura", text: "Depois de acordar, siga o tutorial e prepare seu parceiro.", source_refs: [{page: 1}]},
      {id: "b2", title: "Instalações de File City", text: "Consulte a clínica, o armazém e o ginásio antes de continuar.", source_refs: [{page: 2}]},
    ]}],
    progress: {completed: [], favorites: [], checkpoint: "b1", revealed_spoilers: [], value_versions: {}},
    systems: [{id: "evolution", title: "Evoluções", nodes: [
      {id: "koromon", label: "Koromon", card_number: 1, stage: "Baby I", source_refs: [{page: 1}]},
      {id: "agumon", label: "Agumon", card_number: 24, stage: "Rookie", source_refs: [{page: 1}]},
      {id: "greymon", label: "Greymon", card_number: 63, stage: "Champion", source_refs: [{page: 1}]},
    ], edges: [
      {id: "koromon-agumon", from: "koromon", to: "agumon", requirements: [{id: "age", text: "Idade: 3 dias ou mais", condition: {op: ">="}, source_refs: [{page: 1}]}], source_refs: [{page: 1}]},
      {id: "agumon-greymon", from: "agumon", to: "greymon", requirements: [{id: "item", text: "Use o item Digivice", mode: "item", source_refs: [{page: 1}]}], source_refs: [{page: 1}]},
    ]}],
    system_state: {active_system: "evolution", goals: {evolution: "agumon"}, completed_requirements: [], completed_requirement_targets: [], requirements_scoped: false, node_media: {}, value_versions: {}},
    items: [{id: "digivice", name: "Digivice", quantity: null, value_version: 0, description: "Item usado em uma evolução documentada."}],
    achievements: [{name: "Primeiros passos", desc: "Conclua a introdução.", earned: false}],
    media: [], answer: {},
  };
}
async function boot() {
  M.storage=await createStorage(); const params=new URLSearchParams(location.search), pair=new URLSearchParams(location.hash.slice(1)).get("pair");
  history.replaceState(null,"",location.pathname+(params.get("demo")==="1"?"?demo=1":""));
  if(params.get("demo")==="1"){M.demo=true;M.data=demoSnapshot();M.namespace=namespaceFor(M.data,"demo");M.p.gameSlug="demo";selections();setConnected(true);notify("Modo demo: as marcações não são enviadas ao PC.");return render();}
  if(pair){try{await api("/pair",{code:pair,name:/iPad|Tablet/i.test(navigator.userAgent)?"Tablet":"Celular",remember:true});notify("Confirme este aparelho no PC.");const wait=async()=>{try{const value=await api("/pair/status",{});if(value.pending)return setTimeout(wait,1500);refresh(true);}catch(error){notify(error.message,true);disconnected(error.message);}};wait();}catch(error){notify(error.message,true);disconnected(error.message);}}else await refresh(true);
}
window.addEventListener("online",()=>M.data?poll(true):refresh(true));window.addEventListener("focus",()=>M.data?poll(true):refresh(true));document.addEventListener("visibilitychange",()=>{if(!document.hidden)(M.data?poll(true):refresh(true));});setInterval(()=>{if(M.connected&&!document.hidden)poll(true);},3000);boot();
