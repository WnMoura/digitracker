"use strict";

const content = document.getElementById("content");
const tabs = document.getElementById("tabs");
const M = {
  data: null, tab: "now", chapter: 0, pending: false, pendingTarget: "",
  pendingTargets: new Set(), paired: false, undo: null, requestSeq: 0,
  controller: null,
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
}[char]));

function localImage(url) {
  const raw = String(url ?? "").trim();
  if (!raw) return "";
  if (raw.startsWith("/assets/")) return esc(raw);
  if (raw.startsWith("assets/")) return esc(`/${raw}`);
  try {
    const parsed = new URL(raw, location.origin);
    return parsed.origin === location.origin && parsed.pathname.startsWith("/assets/")
      ? esc(`${parsed.pathname}${parsed.search}`) : "";
  } catch (_) { return ""; }
}

function gameArt(game) {
  const art = game?.art;
  if (typeof art === "string") return art;
  if (art && typeof art === "object") return art.box || art.cover || art.title || art.ingame || "";
  return game?.icon || "";
}

function progressInfo(game, data) {
  const mastery = game?.mastery || {};
  const completion = game?.completion || {};
  const completed = Array.isArray(data?.progress?.completed) ? data.progress.completed.length : 0;
  const total = Number(completion.total ?? mastery.total ?? 0) || 0;
  const earned = Number(completion.earned ?? mastery.earned ?? completed) || 0;
  const percent = Number(completion.percent ?? (total ? earned / total * 100 : mastery.percent ?? 0));
  return { completed, total, earned, percent: Math.max(0, Math.min(100, Math.round(percent || 0))) };
}

function coverHTML(game, className = "") {
  const image = localImage(gameArt(game));
  const title = esc(game?.title || "Jogo");
  return image
    ? `<div class="game-cover ${className}"><img src="${image}" alt="Capa de ${title}" loading="lazy"></div>`
    : `<div class="game-cover fallback ${className}" aria-label="${title}"><span>◇</span></div>`;
}

function mediaURL(data, mediaId, fallback = "") {
  const media = (data?.media || []).find((item) => item.id === mediaId);
  return localImage(media?.url || media?.src || fallback);
}

function pageHeader(kicker, title, subtitle = "") {
  return `<header class="mobile-page-head"><span class="eyebrow">${esc(kicker)}</span><h1>${esc(title)}</h1>${subtitle ? `<p>${esc(subtitle)}</p>` : ""}</header>`;
}

// A read-only fixture makes the companion easy to review before pairing it
// with the PC: open /ui/companion/index.html?demo=1 from a local static server.
// Production never enters this branch and continues to use /api/state.
function demoSnapshot() {
  const cover = "/assets/art/digimon_world/box.png";
  const game = { slug: "demo-digimon-world", title: "Digimon World Re:Digitize", platform: "PSP", art: { box: cover }, mastery: { total: 44, earned: 12, percent: 12 }, completion: { total: 44, earned: 12 } };
  const node = (id, label, stage, card, imageId) => ({ id, label, stage, card_number: card, media_id: imageId, subtitle: "Acompanhe este objetivo e as rotas disponíveis." });
  const system = {
    id: "demo-evolutions", title: "Evoluções", description: "Rotas ilustrativas para revisão no celular.", status: "approved",
    nodes: [node("agumon", "Agumon", "Rookie", 1, "agumon"), node("greymon", "Greymon", "Champion", 2, "greymon"), node("metalgreymon", "MetalGreymon", "Ultimate", 3, "metalgreymon"), node("gabumon", "Gabumon", "Rookie", 4, "gabumon"), node("weregarurumon", "WereGarurumon", "Champion", 5, "weregarurumon")],
    edges: [
      { id: "demo-edge-agumon", from: "agumon", to: "greymon", requirements: [{ id: "req-training", mode: "requirement", text: "Treinamento documentado", condition: { op: "all" }, source_refs: [{ page: 4 }] }] },
      { id: "demo-edge-greymon", from: "greymon", to: "metalgreymon", requirements: [{ id: "req-item", mode: "item", text: "Use Metal Banana", condition: { op: "item", item_name: "Metal Banana", action: "use" }, source_refs: [{ page: 5 }] }] },
      { id: "demo-edge-gabumon", from: "gabumon", to: "weregarurumon", requirements: [{ id: "req-weight", mode: "requirement", text: "Peso: 15 ou mais", condition: { op: "minimum", field: "weight", value: 15 }, source_refs: [{ page: 7 }] }] },
    ],
  };
  return {
    ok: true, api_version: 2, version: "demo", definition_revision: "demo", content_revision: "demo", progress_revision: "demo",
    game, games: [game, { slug: "demo-rumble", title: "Digimon Rumble Arena", platform: "PSP", art: { box: "/assets/art/digimon_rumble_arena/box.png" }, completion: { total: 32, earned: 9 } }, { slug: "demo-world-2", title: "Digimon World 2", platform: "PlayStation", art: { box: "/assets/art/digimon_world_2/box.png" }, completion: { total: 50, earned: 21 } }],
    chapters: [{ id: "chapter-1", title: "Primeiros passos", blocks: [{ id: "demo-guide-1", type: "step", title: "Chegue à Cidade File", text: "Conclua a introdução e encontre o primeiro parceiro." }, { id: "demo-guide-2", type: "missable", title: "Não perca o treinamento", text: "Confira os requisitos antes de avançar." }] }, { id: "chapter-2", title: "Evoluções", blocks: [{ id: "demo-guide-3", type: "step", title: "Escolha sua rota", text: "Abra o Atlas para comparar destinos." }, { id: "demo-guide-4", type: "spoiler", title: "Detalhe oculto", text: "Revele quando quiser.", hidden: true }] }],
    systems: [system], media: ["agumon", "greymon", "metalgreymon", "gabumon", "weregarurumon"].map((id) => ({ id, url: cover, title: id })),
    progress: { completed: ["demo-guide-1"], checkpoint: "demo-guide-1", revealed_spoilers: [], value_versions: {} }, system_state: { completed_requirements: ["req-training"], goals: {}, node_media: {}, preferences: {}, value_versions: {} },
    objective: { block_id: "demo-guide-2", title: "Não perca o treinamento", text: "Confira os requisitos antes de avançar." },
    items: [{ id: "item-metal-banana", name: "Metal Banana", description: "Item de evolução", quantity: 1 }, { id: "item-sacred-wings", name: "Sacred Wings", description: "Item especial", quantity: null }, { id: "item-x-program", name: "X-Program", description: "Item de evolução", quantity: 0 }], items_revision: 1,
    missables: [{ name: "Treinamento inicial", earned: false }], achievements: [{ id: "a1", name: "Primeiros passos", desc: "Inicie sua jornada.", points: 10, earned: true, hardcore: false }, { id: "a2", name: "Novo parceiro", desc: "Obtenha seu primeiro Digimon.", points: 15, earned: true, hardcore: false }, { id: "a3", name: "Treinador dedicado", desc: "Aumente um atributo para 100.", points: 20, earned: false, hardcore: false }, { id: "a4", name: "Evolução perfeita", desc: "Alcance uma evolução Ultimate.", points: 30, earned: false, hardcore: true }], answer: {},
  };
}

function message(text, error = false) {
  const el = document.getElementById("message");
  el.textContent = text; el.dataset.error = error ? "true" : "false"; el.style.display = "block";
  clearTimeout(message.timer); message.timer = setTimeout(() => { el.style.display = "none"; }, 5000);
}

function requestId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `mobile-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function apiRaw(path, body, options = {}) {
  const controller = options.controller || new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 12000);
  try {
    const response = await fetch(path, body === undefined ? { signal: controller.signal } : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    return { ...data, _httpStatus: response.status };
  } finally { clearTimeout(timeout); }
}

async function api(path, body, options = {}) {
  const data = await apiRaw(path, body, options);
  if (data._httpStatus >= 400 || data.ok === false) throw Object.assign(new Error(data.error || "Não foi possível concluir."), { status: data._httpStatus, payload: data });
  return data;
}

function captureFocus() {
  const active = document.activeElement;
  if (!active || !content.contains(active)) return null;
  const result = { id: active.id || "", value: "", start: 0, end: 0, details: [] };
  if ("value" in active) { result.value = active.value; result.start = active.selectionStart ?? 0; result.end = active.selectionEnd ?? result.start; }
  content.querySelectorAll("details[open]").forEach((item) => { const id = item.dataset.nodeId; if (id) result.details.push(id); });
  return result;
}

function restoreFocus(focus) {
  if (!focus) return;
  focus.details.forEach((id) => Array.from(content.querySelectorAll("details[data-node-id]"))
    .find((item) => item.dataset.nodeId === id)?.setAttribute("open", ""));
  if (!focus.id) return;
  const active = document.getElementById(focus.id); if (!active) return;
  active.focus({ preventScroll: true });
  if ("value" in active) { active.value = focus.value; try { active.setSelectionRange(focus.start, focus.end); } catch (_) {} }
}

async function refresh(force = false) {
  if (M.pendingTargets.size && !force) return;
  const sequence = ++M.requestSeq; M.controller?.abort(); M.controller = new AbortController();
  try {
    const data = await api("/api/state", undefined, { controller: M.controller });
    if (sequence !== M.requestSeq) return;
    const changed = !M.data || M.data.version !== data.version || M.data.game?.slug !== data.game?.slug || JSON.stringify(M.data.answer) !== JSON.stringify(data.answer);
    if (M.data?.game?.slug !== data.game?.slug) { M.chapter = 0; M.undo = null; }
    M.data = data; M.paired = true; tabs.hidden = false;
    document.getElementById("connection").textContent = "PC sincronizado";
    document.body.dataset.tab = M.tab;
    if (force || changed) render();
  } catch (error) {
    if (error.name === "AbortError") return;
    document.getElementById("connection").textContent = "Sem conexão";
    if (error.status === 401) {
      M.paired = false; tabs.hidden = true;
      content.innerHTML = '<section class="card connection-card"><span class="eyebrow">DIGITRACKER · CELULAR</span><h1>Conexão encerrada</h1><p>Leia um novo QR Code no PC para continuar.</p></section>';
    } else if (force) message(error.message, true);
  }
}

function targetFor(values) {
  if (values.kind === "progress") return { block_id: values.block_id };
  if (values.kind === "requirement") return { system_id: values.system_id, edge_id: values.edge_id, requirement_id: values.requirement_id };
  if (values.kind === "item") return { item_id: values.item_id };
  return {};
}

function valueVersionFor(target) {
  if (target.block_id) return M.data?.progress?.value_versions?.[`progress:${target.action || "complete"}:${target.block_id}`];
  if (target.requirement_id) return M.data?.system_state?.value_versions?.[`requirement:${target.system_id}:${target.edge_id}:${target.requirement_id}`];
  return undefined;
}

async function action(values, source = null) {
  if (!M.data) return;
  const target = targetFor(values);
  const targetKey = values.kind === "progress" ? `progress:${values.action}:${values.block_id}` : values.kind === "requirement" ? `requirement:${values.system_id}:${values.edge_id}:${values.requirement_id}` : `${values.kind}:${values.system_id || ""}:${values.node_id || values.item_id || ""}`;
  if (M.pendingTargets.has(targetKey)) return;
  const requestedValue = values.kind === "requirement" ? !!values.value : values.value;
  M.pendingTargets.add(targetKey); M.pending = true; M.pendingTarget = targetKey; if (source) source.disabled = true;
  try {
    let result;
    if (values.kind === "goal" || values.kind === "ask") result = await apiRaw("/api/action", { slug: M.data.game.slug, version: M.data.version, ...values });
    else {
      const expected = valueVersionFor({ ...target, action: values.action });
      result = await apiRaw("/api/action", { api_version: 2, request_id: requestId(), slug: M.data.game.slug, expected_definition_revision: M.data.definition_revision || "", expected_value_version: expected ?? 0, expected_item_revision: M.data.items_revision ?? 0, target, ...values });
    }
    if (result.ok === false || result._httpStatus >= 400) {
      if (source?.type === "checkbox" && values.kind === "requirement") source.checked = !requestedValue;
      if (result.conflict) { message(`${result.error || "Esta marcação mudou em outro lugar."} Atualizando o card.`, true); await refresh(true); }
      else message(result.error || "Não foi possível salvar.", true);
      return;
    }
    message(result.idempotent ? "Marcação já sincronizada." : "Salvo no PC");
    if (values.kind === "progress" && values.action === "complete") M.undo = { ...values, value: !values.value };
  } catch (error) {
    if (source?.type === "checkbox" && values.kind === "requirement") source.checked = !requestedValue;
    message(error.message, true);
  } finally {
    M.pendingTargets.delete(targetKey); M.pending = M.pendingTargets.size > 0;
    if (M.pendingTarget === targetKey) M.pendingTarget = "";
    if (source) source.disabled = false;
    await refresh(true);
  }
}

function blockHTML(block, number) {
  if (block.hidden) return `<article class="card" data-guide-block="${esc(block.id)}"><div class="card-topline"><span class="eyebrow">SPOILER</span><span class="card-id">GUIA #${String(number).padStart(3, "0")}</span></div><h3>Spoiler oculto</h3><button data-testid="guide-reveal" data-reveal="${esc(block.id)}" data-block-id="${esc(block.id)}">Revelar este trecho</button></article>`;
  const done = (M.data.progress.completed || []).includes(block.id);
  return `<article class="card ${["missable", "warning"].includes(block.type) ? "alert" : ""}" id="guide-${esc(block.id)}" data-guide-block="${esc(block.id)}" data-card-number="${String(number)}"><div class="card-topline"><span class="eyebrow">${esc(block.type === "missable" ? "Aviso do guia" : "Etapa")}</span><span class="card-id">#${String(number).padStart(3, "0")}</span></div><h2>${esc(block.title || "Etapa sem título")}</h2><p class="text">${esc(block.text)}</p>${(block.items || []).map((item) => `<p>• ${esc(item.text || item)}</p>`).join("")}<button class="check" data-testid="guide-complete" data-complete="${esc(block.id)}" data-block-id="${esc(block.id)}" data-value="${!done}"><b>${done ? "✓" : "○"}</b>${done ? "Concluído · desmarcar" : "Marcar como concluído"}</button><button data-checkpoint="${esc(block.id)}" data-block-id="${esc(block.id)}">Continuar daqui</button></article>`;
}

function itemRequirementHTML(requirement, system, edge, done) {
  const mode = requirement.mode === "item" ? "ITEM DO JOGO" : requirement.mode === "jogress" ? "JOGRESS" : "REQUISITO";
  const source = (requirement.source_refs || []).map((ref) => ref.page ? `p.${ref.page}` : `§${ref.section}.${ref.block}`).join(" · ");
  return `<label class="requirement ${requirement.condition?.op === "unknown" ? "unknown" : ""}"><input type="checkbox" data-testid="atlas-requirement" data-requirement="${esc(requirement.id)}" data-condition-id="${esc(requirement.id)}" data-rule-id="${esc(edge.id)}" data-edge="${esc(edge.id)}" data-system="${esc(system.id)}" ${done ? "checked" : ""} ${requirement.condition?.op === "unknown" ? "disabled" : ""}><span><b>${esc(mode)}</b>${esc(requirement.text || "Sem descrição")}${source ? `<small>Fonte: ${esc(source)}</small>` : ""}</span></label>`;
}

function atlasHTML(data) {
  const systems = data.systems || [];
  if (!systems.length) return '<section class="card"><h2>Nenhum sistema publicado</h2><p>Crie e aprove um sistema no Atlas do PC. Prévias não são enviadas ao celular.</p></section>';
  const completed = new Set(data.system_state?.completed_requirements || []);
  return systems.map((system) => `<section class="card atlas-system"><div class="card-topline"><span class="eyebrow">Sistema visual</span><span class="card-id">${esc(system.id)}</span></div><h2>${esc(system.title)}</h2><p>${esc(system.description)}</p><div class="atlas-mobile-nodes">${(system.nodes || []).map((node, index) => {
    const card = Number(node.card_number || index + 1);
    const mediaId = data.system_state?.node_media?.[`${system.id}:${node.id}`] || node.media_id;
    const image = mediaURL(data, mediaId, node.image?.url || node.image?.src);
    const incoming = (system.edges || []).filter((edge) => edge.to === node.id);
    const thumbnail = image ? `<span class="node-thumb"><img src="${image}" alt="" loading="eager"></span>` : `<span class="node-thumb fallback" aria-hidden="true">◇</span>`;
    return `<details class="node" data-node-id="${esc(node.id)}" data-card-number="${String(card)}"><summary>${thumbnail}<span class="card-id">#${String(card).padStart(3, "0")}</span><span class="node-label">${esc(node.label)}</span><small>${esc(node.stage || node.group || "Detalhes")}</small></summary>${image ? `<img src="${image}" alt="${esc(node.label)}" loading="lazy">` : ""}<p>${esc(node.subtitle || "Acompanhe este objetivo e os caminhos disponíveis.")}</p><button data-goal="${esc(node.id)}" data-system="${esc(system.id)}" data-card-number="${String(card)}">◎ Fixar como objetivo</button>${incoming.map((edge, edgeIndex) => `<div class="atlas-path"><h3>Caminho ${edgeIndex + 1} · ${esc(system.nodes.find((value) => value.id === edge.from)?.label || "Origem")}</h3>${(edge.requirements || []).map((requirement) => itemRequirementHTML(requirement, system, edge, completed.has(requirement.id))).join("") || "<p class=muted>Nenhum requisito documentado.</p>"}</div>`).join("")}</details>`;
  }).join("")}</div></section>`).join("");
}

function libraryGames(data) {
  const rows = Array.isArray(data?.games) && data.games.length ? data.games : [data?.game].filter(Boolean);
  return rows.filter((game) => game && game.slug);
}

function librarySheetHTML(data) {
  const games = libraryGames(data);
  return `<div class="mobile-sheet-backdrop" data-sheet-backdrop><section class="mobile-sheet" id="mobile-sheet" role="dialog" aria-modal="true" aria-labelledby="mobile-sheet-title"><div class="mobile-sheet-head"><div><span class="eyebrow">BIBLIOTECA</span><h2 id="mobile-sheet-title">Jogos sincronizados</h2></div><button class="mobile-icon-button" type="button" data-close-sheet aria-label="Fechar biblioteca">×</button></div><p class="mobile-sheet-note">O celular acompanha o jogo aberto no PC. Abra outro jogo no desktop para alternar a sessão.</p><div class="mobile-sheet-list">${games.map((game) => `<button class="mobile-game-row" type="button" data-library-game="${esc(game.slug)}">${coverHTML(game)}<span class="mobile-game-copy"><b>${esc(game.title || game.slug)}</b><small>${esc(game.platform || "Jogo")}</small></span><span aria-hidden="true">›</span></button>`).join("") || "<p>Nenhum jogo sincronizado.</p>"}</div></section></div>`;
}

function openLibrarySheet() {
  if (!M.data || document.querySelector("[data-sheet-backdrop]")) return;
  document.body.insertAdjacentHTML("beforeend", librarySheetHTML(M.data));
  const backdrop = document.querySelector("[data-sheet-backdrop]");
  const close = () => backdrop?.remove();
  backdrop?.addEventListener("click", (event) => { if (event.target === backdrop) close(); });
  backdrop?.querySelector("[data-close-sheet]")?.addEventListener("click", close);
  backdrop?.querySelectorAll("[data-library-game]").forEach((button) => button.addEventListener("click", () => {
    message(button.dataset.libraryGame === M.data.game?.slug ? "Este jogo já está aberto." : "Abra o jogo desejado no PC para trocar a sessão.");
    close();
  }));
  backdrop?.querySelector("[data-close-sheet]")?.focus();
}

function render() {
  const data = M.data; if (!data) return;
  const focus = captureFocus();
  tabs.querySelectorAll("button").forEach((button) => button.setAttribute("aria-current", button.dataset.tab === M.tab));
  document.body.dataset.tab = M.tab;
  const game = data.game || {};
  const info = progressInfo(game, data);
  const chapters = data.chapters || [];
  M.chapter = chapters.length ? Math.min(M.chapter, chapters.length - 1) : 0;
  const chapter = chapters[M.chapter];
  let html = `<div class="mobile-screen mobile-${esc(M.tab)}">`;

  if (M.tab === "now") {
    html += `<section class="mobile-welcome"><span class="eyebrow">SEU UNIVERSO DE JOGOS</span><h1>Seu jogo, sempre com você.</h1><p>Guias, conquistas e mapas sincronizados com o PC.</p></section>`;
    html += `<section class="mobile-hero">${coverHTML(game)}<div class="mobile-hero-copy"><span class="eyebrow">JOGANDO AGORA</span><h2>${esc(game.title || "Jogo ativo")}</h2><p>${esc(game.platform || "Plataforma não informada")} · progresso acompanhado</p><div class="progress-meta"><span>${info.earned} de ${info.total || "—"}</span><b>${info.percent}%</b></div><progress max="100" value="${info.percent}" aria-label="${info.percent}% concluído"></progress><button class="primary" data-open-guide>Continuar →</button></div></section>`;
    html += `<div class="mobile-stat-grid"><div class="mobile-stat"><b>${(data.achievements || []).filter((item) => item.earned).length}</b><span>Conquistas</span></div><div class="mobile-stat"><b>${chapters.length}</b><span>Guias</span></div><div class="mobile-stat"><b>${(data.systems || []).length}</b><span>Sistemas</span></div></div>`;
    const recent = libraryGames(data).filter((item) => item.slug !== game.slug).slice(0, 3);
    if (recent.length) html += `<section class="mobile-section"><div class="mobile-section-head"><h2>Jogos recentes</h2><button class="mobile-link-button" type="button" data-open-library>Ver todos ›</button></div><div class="mobile-recent">${recent.map((item) => `<button class="mobile-game-row" type="button" data-library-game="${esc(item.slug)}">${coverHTML(item)}<span class="mobile-game-copy"><b>${esc(item.title || item.slug)}</b><small>${esc(item.platform || "Jogo")} · sincronizado</small></span><span aria-hidden="true">›</span></button>`).join("")}</div></section>`;
    if ((data.missables || []).length) html += `<section class="card alert"><span class="eyebrow">Atenção · perdíveis</span>${data.missables.slice(0, 3).map((item) => `<p>${esc(item.name)}${item.earned ? " · Refazer em Hardcore" : ""}</p>`).join("")}</section>`;
    html += `<section class="card"><div class="card-topline"><span class="eyebrow">PROGRESSO</span>${M.undo ? `<button data-undo>Desfazer</button>` : ""}</div><h2>Seu controle de progresso</h2><p>${(data.progress.completed || []).length} etapas concluídas. Requisitos e etapas são marcados manualmente.</p></section>`;
  }

  if (M.tab === "guide") {
    html += pageHeader("GUIA", "Guia inteligente", "Navegue por capítulos, marque o que concluiu e retome do celular.");
    html += `<div class="mobile-segmented" aria-label="Seções do guia"><button type="button">Índice</button><button type="button">Navegação</button><button type="button">Favoritos</button></div>`;
    html += chapters.length ? `<label class="mobile-chapter-label" for="chapter">Capítulo<select id="chapter">${chapters.map((item, index) => `<option value="${index}" ${index === M.chapter ? "selected" : ""}>${index + 1}. ${esc(item.title)}</option>`).join("")}</select></label>${(chapter?.blocks || []).map((block, index) => blockHTML(block, index + 1)).join("")}<div class="actions"><button data-prev ${M.chapter === 0 ? "disabled" : ""}>← Anterior</button><button data-next ${M.chapter >= chapters.length - 1 ? "disabled" : ""}>Próximo →</button></div><form id="question-form" class="card"><span class="eyebrow">ASSISTENTE</span><h2>Perguntar sobre este trecho</h2><p>A pergunta e o trecho serão enviados ao provedor configurado no PC, sujeito a cobrança.</p><select name="block">${(chapter.blocks || []).filter((block) => !block.hidden).map((block) => `<option value="${esc(block.id)}">${esc(block.title || "Etapa")}</option>`).join("")}</select><textarea id="question" name="question" maxlength="2000" placeholder="Qual é o próximo passo?" required></textarea><button class="primary" ${data.answer?.running ? "disabled" : ""}>${data.answer?.running ? "Consultando…" : "Perguntar à IA"}</button>${data.answer?.answer ? `<p class="text">${esc(data.answer.answer)}</p>` : ""}${data.answer?.error ? `<p>${esc(data.answer.error)}</p>` : ""}</form>` : '<section class="card"><h2>O guia ainda não está pronto</h2><p>Importe e publique um guia no PC para continuar aqui.</p></section>';
  }

  if (M.tab === "atlas") {
    html += pageHeader("ATLAS DE SISTEMAS", "Mapas e evoluções", "Entenda as rotas, requisitos e itens que levam a cada objetivo.");
    html += `<div class="mobile-segmented" aria-label="Visualizações do Atlas"><button type="button">Sistemas</button><button type="button">Rotas</button><button type="button">Tabelas</button></div>${atlasHTML(data)}`;
  }

  if (M.tab === "items") {
    const items = data.items || [];
    html += pageHeader("INVENTÁRIO", "Itens do jogo", "Registre a posse dos itens usados por evoluções e outras regras.");
    html += items.length ? `<section class="card"><div class="card-topline"><span class="eyebrow">CATÁLOGO</span><span class="card-id">${items.length} itens</span></div>${items.map((item) => `<article class="item-row"><div><div class="card-topline"><h2>${esc(item.name)}</h2><span class="card-id">${esc(item.id || "")}</span></div><p>${esc(item.description || "")}</p></div><div class="item-controls"><input id="item-${esc(item.id)}" data-testid="item-quantity" data-item-id="${esc(item.id)}" type="number" min="0" step="1" value="${item.quantity == null ? "" : Number(item.quantity)}" placeholder="—" aria-label="Quantidade de ${esc(item.name)}"><button data-testid="item-save" data-item-save="${esc(item.id)}">Salvar</button></div></article>`).join("")}</section>` : '<section class="card"><h2>Itens ainda não catalogados</h2><p>Quando uma fonte documentar itens, eles aparecerão aqui separados das criaturas e objetivos.</p></section>';
  }

  if (M.tab === "achievements") {
    const rows = [...(data.achievements || [])].sort((a, b) => Number(a.hardcore) - Number(b.hardcore));
    const earned = rows.filter((item) => item.earned).length;
    html += pageHeader("CONQUISTAS", "Sua coleção", "Acompanhe os marcos do jogo e saiba o que ainda falta.");
    html += `<section class="card"><div class="card-topline"><span class="eyebrow">PROGRESSO</span><b>${earned}/${rows.length || 0}</b></div><progress max="${rows.length || 1}" value="${earned}" aria-label="${earned} de ${rows.length} conquistas"></progress></section>`;
    html += rows.map((item, index) => `<article class="card achievement-mobile"><span class="achievement-mark" aria-hidden="true">${item.hardcore ? "◆" : item.earned ? "◇" : "○"}</span><div><div class="card-topline"><h3>${esc(item.name)}</h3><span class="card-id">#${String(index + 1).padStart(3, "0")}</span></div><p>${esc(item.desc)}</p><small>${item.points || 0} pontos · ${item.hardcore ? "Hardcore" : item.earned ? "Softcore" : "Pendente"}${item.achievement_type === "missable" ? " · Perdível" : ""}</small></div></article>`).join("") || "<p>Nenhuma conquista sincronizada.</p>";
  }

  html += "</div>";
  content.innerHTML = html;
  restoreFocus(focus);
  content.querySelector("[data-open-guide]")?.addEventListener("click", () => {
    M.tab = "guide"; const id = data.objective?.block_id;
    const index = chapters.findIndex((item) => (item.blocks || []).some((block) => block.id === id));
    M.chapter = Math.max(0, index); render(); window.scrollTo(0, 0);
  });
  content.querySelectorAll("[data-open-library]").forEach((button) => button.addEventListener("click", openLibrarySheet));
  content.querySelectorAll("[data-library-game]").forEach((button) => button.addEventListener("click", openLibrarySheet));
  content.querySelector("#chapter")?.addEventListener("change", (event) => { M.chapter = Number(event.target.value); render(); });
  content.querySelector("[data-prev]")?.addEventListener("click", () => { M.chapter -= 1; render(); window.scrollTo(0, 0); });
  content.querySelector("[data-next]")?.addEventListener("click", () => { M.chapter += 1; render(); window.scrollTo(0, 0); });
  content.querySelectorAll("[data-complete]").forEach((button) => button.addEventListener("click", () => action({ kind: "progress", action: "complete", block_id: button.dataset.complete, value: button.dataset.value === "true" }, button)));
  content.querySelectorAll("[data-reveal]").forEach((button) => button.addEventListener("click", () => action({ kind: "progress", action: "reveal", block_id: button.dataset.reveal, value: true }, button)));
  content.querySelectorAll("[data-checkpoint]").forEach((button) => button.addEventListener("click", () => action({ kind: "progress", action: "checkpoint", block_id: button.dataset.checkpoint, value: true }, button)));
  content.querySelectorAll("[data-goal]").forEach((button) => button.addEventListener("click", () => action({ kind: "goal", system_id: button.dataset.system, node_id: button.dataset.goal }, button)));
  content.querySelectorAll("[data-requirement]").forEach((input) => input.addEventListener("change", () => action({ kind: "requirement", system_id: input.dataset.system, edge_id: input.dataset.edge, requirement_id: input.dataset.requirement, value: input.checked }, input)));
  content.querySelectorAll("[data-item-save]").forEach((button) => button.addEventListener("click", () => {
    const input = document.getElementById(`item-${button.dataset.itemSave}`); const raw = String(input?.value ?? "").trim(); const quantity = raw === "" ? null : Number(raw);
    if (quantity !== null && (!Number.isInteger(quantity) || quantity < 0)) return message("Informe uma quantidade inteira válida ou deixe em branco para não informado.", true);
    action({ kind: "item", item_id: button.dataset.itemSave, value: quantity, expected_item_revision: data.items_revision }, button);
  }));
  content.querySelector("[data-undo]")?.addEventListener("click", async () => { const undo = M.undo; M.undo = null; await action(undo); M.undo = null; });
  content.querySelector("#question-form")?.addEventListener("submit", (event) => { event.preventDefault(); const form = new FormData(event.target); action({ kind: "ask", block_id: form.get("block"), question: form.get("question") }); });
}

tabs.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => { M.tab = button.dataset.tab; render(); window.scrollTo(0, 0); }));

document.getElementById("mobile-menu")?.addEventListener("click", openLibrarySheet);
document.getElementById("mobile-search-toggle")?.addEventListener("click", () => {
  const search = document.getElementById("mobile-search");
  search.hidden = !search.hidden;
  if (!search.hidden) document.getElementById("mobile-search-input")?.focus();
});
document.getElementById("mobile-search-close")?.addEventListener("click", () => { document.getElementById("mobile-search").hidden = true; });
document.getElementById("mobile-search-input")?.addEventListener("input", (event) => {
  const query = String(event.target.value || "").trim().toLocaleLowerCase("pt-BR");
  if (!query) return;
  const matches = [...content.querySelectorAll("[data-guide-block], .node, .item-row, .achievement-mobile")];
  matches.forEach((item) => { item.hidden = !item.textContent.toLocaleLowerCase("pt-BR").includes(query); });
});

async function boot() {
  const params = new URLSearchParams(location.search);
  const code = new URLSearchParams(location.hash.slice(1)).get("pair"); history.replaceState(null, "", location.pathname + (params.get("demo") === "1" ? "?demo=1" : ""));
  if (params.get("demo") === "1") {
    M.data = demoSnapshot(); M.paired = false; tabs.hidden = false;
    document.getElementById("connection").textContent = "Modo demo";
    render();
    return;
  }
  if (code) {
    try {
      await api("/pair", { code, name: /iPad|Tablet/i.test(navigator.userAgent) ? "Tablet" : "Celular" });
      content.innerHTML = '<section class="card connection-card"><span class="eyebrow">PAREAMENTO</span><h1>Confirme no PC</h1><p>Abra Celular no DigiTracker e aprove este dispositivo.</p></section>';
      let waiting = true;
      while (waiting) { await new Promise((resolve) => setTimeout(resolve, 1500)); waiting = (await api("/pair/status", {})).pending; }
      await refresh(true);
    } catch (error) { message(error.message, true); document.getElementById("connection").textContent = "Pareamento necessário"; }
  } else await refresh(true);
}

boot();
setInterval(() => { if (M.paired && !document.hidden && !M.pendingTargets.size) refresh(); }, 3000);
