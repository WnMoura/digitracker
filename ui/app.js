/* ===========================================================================
   DigiTracker — frontend (vanilla JS dentro do pywebview)
   Renderiza dashboard, wizard de adicionar jogo e tela de configuração.
   Cai em "modo demonstração" quando rodando sem o backend pywebview.
   =========================================================================== */

const MODE_COLOR = { normal: "var(--cyan)", hard: "var(--red)" };
const MODE_LABEL = { normal: "NORMAL", hard: "HARD" };
const C_LINE = "#23233a", C_LOCKED = "#2C2C42";

const S = {
  mode: "real",          // 'real' | 'demo'
  view: "loading",
  library: [],
  activeSlug: null,
  onTop: true,
  poll: null,
  W: null,               // estado do wizard
};

const root = document.getElementById("root");
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------------------- camada de dados ---------------------------- */
const hasBackend = () => typeof window.pywebview !== "undefined" && window.pywebview.api;

const backend = {
  async appState() {
    if (S.mode === "demo") return { configured: true };
    return window.pywebview.api.get_app_state();
  },
  async library() {
    if (S.mode === "demo") return DEMO_LIB();
    return window.pywebview.api.get_library();
  },
  async game(slug) {
    if (S.mode === "demo") return DEMO_GAME(slug);
    return window.pywebview.api.get_game(slug);
  },
  async saveSecrets(u, k) { return window.pywebview.api.save_secrets(u, k); },
  async search(q) {
    if (S.mode === "demo") {
      const r = DEMO_SEARCH().filter((g) => g.title.toLowerCase().includes(q.toLowerCase()));
      return { ready: true, building: false, results: r };
    }
    return window.pywebview.api.search_games(q);
  },
  async importGame(id) {
    if (S.mode === "demo") return DEMO_IMPORT(id);
    return window.pywebview.api.import_game(id);
  },
  async saveGame(p) {
    if (S.mode === "demo") return { ok: true, slug: null, demo: true };
    return window.pywebview.api.save_game(p);
  },
};

/* ------------------------------- toast ---------------------------------- */
let toastT;
function toast(msg, isErr = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "show" + (isErr ? " err" : "");
  clearTimeout(toastT);
  toastT = setTimeout(() => (t.className = ""), 2600);
}

/* ------------------------------ primitivas ------------------------------ */
function ring(percent, size = 52, stroke = 4, color = "var(--cyan)") {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c - (Math.max(0, Math.min(100, percent)) / 100) * c;
  return `
    <svg width="${size}" height="${size}" style="transform:rotate(-90deg);flex-shrink:0">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke="${C_LINE}" stroke-width="${stroke}" fill="none"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke="${color}" stroke-width="${stroke}" fill="none"
        stroke-dasharray="${c}" stroke-dashoffset="${offset}" stroke-linecap="round"
        style="transition:stroke-dashoffset .7s ease"/>
    </svg>`;
}

function modeChip(key, earned, total) {
  const pct = total ? Math.round((earned / total) * 100) : 0;
  const done = pct >= 100;
  const col = MODE_COLOR[key];
  return `<div class="mode-chip" style="border-color:${done ? col : C_LINE}">
    <span class="pip" style="background:${done ? col : C_LOCKED}"></span>
    <span class="txt" style="color:${done ? col : "var(--text-mid)"}">${MODE_LABEL[key]} ${earned}/${total} · ${pct}%</span>
  </div>`;
}

function modeTag(mode) {
  const col = MODE_COLOR[mode];
  return `<span class="mode-tag" style="color:${col};background:${col === "var(--cyan)" ? "rgba(45,226,230,.12)" : "rgba(214,40,57,.12)"};border-color:${col}">${MODE_LABEL[mode]}</span>`;
}

function modeDots(modes) {
  return `<div class="mode-dots">` + Object.entries(modes).map(([k, v]) => {
    const done = v.earned >= v.total && v.total > 0;
    const col = MODE_COLOR[k];
    return `<span class="mode-dot" style="border-color:${done ? col : C_LINE};background:${done ? (col === "var(--cyan)" ? "rgba(45,226,230,.1)" : "rgba(214,40,57,.1)") : "transparent"}">
      <span class="pip" style="background:${done ? col : C_LOCKED}"></span>
      <span class="lbl" style="color:${done ? col : "var(--text-low)"}">${k[0].toUpperCase()}</span>
    </span>`;
  }).join("") + `</div>`;
}

const totals = (modes) => {
  const t = Object.values(modes).reduce((s, m) => s + m.total, 0);
  const e = Object.values(modes).reduce((s, m) => s + m.earned, 0);
  return { t, e, pct: t ? Math.round((e / t) * 100) : 0 };
};
const initials = (title) => title.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

/* ============================== SETUP VIEW =============================== */
function renderSetup() {
  S.view = "setup";
  root.innerHTML = `
    <div class="setup">
      <div class="setup-card">
        <h1>DIGI<span>TRACKER</span></h1>
        <p class="sub">Conecte sua conta da RetroAchievements. Suas credenciais ficam salvas apenas localmente em <code>config/secrets.json</code> — nunca saem da sua máquina.</p>
        <div class="field">
          <label>Username</label>
          <input id="in-user" autocomplete="off" spellcheck="false" placeholder="seu_usuario_RA" />
        </div>
        <div class="field">
          <label>Web API Key</label>
          <input id="in-key" autocomplete="off" spellcheck="false" placeholder="••••••••••••••••" />
          <p class="hint">Gere em retroachievements.org → Settings → Keys.</p>
        </div>
        <div class="setup-actions">
          <button class="btn-primary" id="btn-save-secrets">Conectar</button>
          <button class="btn-ghost" id="btn-demo">Ver demonstração</button>
        </div>
        <p class="form-error" id="setup-err"></p>
      </div>
    </div>`;

  $("#btn-demo").onclick = () => { S.mode = "demo"; document.getElementById("demo-banner").classList.remove("hidden"); enterDashboard(); };
  $("#btn-save-secrets").onclick = async () => {
    const u = $("#in-user").value, k = $("#in-key").value;
    const btn = $("#btn-save-secrets"), err = $("#setup-err");
    err.textContent = "";
    btn.disabled = true; btn.textContent = "Validando…";
    try {
      const res = await backend.saveSecrets(u, k);
      if (res.ok) { toast("Conectado!"); enterDashboard(); }
      else { err.textContent = res.error || "Falha ao conectar."; }
    } catch (e) { err.textContent = "Erro: " + e; }
    btn.disabled = false; btn.textContent = "Conectar";
  };
}

/* ============================ DASHBOARD VIEW ============================ */
async function enterDashboard() {
  S.view = "dashboard";
  S.library = await backend.library();
  if (!S.activeSlug && S.library.length) S.activeSlug = S.library[0].slug;
  await renderDashboard();
  startPolling();
}

function startPolling() {
  stopPolling();
  S.poll = setInterval(async () => {
    if (S.view !== "dashboard") return;
    try {
      S.library = await backend.library();
      await renderDashboard();
    } catch (_) {}
  }, 5000);
}
function stopPolling() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }

async function renderDashboard() {
  const game = S.activeSlug ? await backend.game(S.activeSlug) : null;
  root.innerHTML = `${sidebarHTML()}${mainHTML(game)}`;
  bindSidebar();
  $("#sync-tag").textContent = S.mode === "demo" ? "DEMO" : "● SYNC 30s";
}

function sidebarHTML() {
  const isDone = (g) => Object.values(g.modes).every((m) => m.total > 0 && m.earned >= m.total);
  const done = S.library.filter(isDone);
  const prog = S.library.filter((g) => !isDone(g));
  const tile = (g) => {
    const { pct } = totals(g.modes);
    const active = g.slug === S.activeSlug;
    return `<button class="tile ${active ? "active" : ""}" data-slug="${esc(g.slug)}" style="border-color:${active ? g.accent : "transparent"}">
      <div class="ring-wrap">${ring(pct, 48, 4, g.accent)}
        <div class="ring-core" style="background:linear-gradient(145deg, ${g.accent}33, var(--panel))">${esc(initials(g.title))}</div>
      </div>
      <div class="meta">
        <div class="name">${esc(g.title)}</div>
        <div class="plat">${esc(g.platform)}</div>
        ${modeDots(g.modes)}
      </div>
    </button>`;
  };
  return `<aside class="sidebar">
    <div class="sidebar-head">
      <h2>BIBLIOTECA</h2>
      <p>${S.library.length} ${S.library.length === 1 ? "jogo" : "jogos"} no ecossistema</p>
    </div>
    <div class="sidebar-list">
      ${done.length ? `<p class="section-label done">✓ CONCLUÍDOS</p>${done.map(tile).join("")}<div style="height:8px"></div>` : ""}
      <p class="section-label progress">◎ EM PROGRESSO</p>
      ${prog.length ? prog.map(tile).join("") : `<p style="color:var(--text-low);font-size:11px;padding:4px">Nenhum jogo ainda.</p>`}
    </div>
    <div class="sidebar-foot">
      <button class="add-btn" id="btn-add">＋ Adicionar Jogo</button>
    </div>
  </aside>`;
}

function mainHTML(game) {
  if (!game) {
    return `<main class="main"><div class="empty-main">
      <div class="big">Nenhum jogo selecionado</div>
      <p>Use "Adicionar Jogo" para importar seu primeiro título da RetroAchievements.</p>
    </div></main>`;
  }
  const { t, e, pct } = totals(game.modes);
  const nextIds = game.next_ids || [];
  const le = game.last_earned;

  const badge = (a) => a.badge_url
    ? `<div class="ach-badge ${a.earned ? "" : "locked"}" style="background-image:url('${esc(a.badge_url)}')"></div>`
    : `<div class="ach-badge ${a.earned ? "" : "locked"}">${a.earned ? "🏆" : "🔒"}</div>`;

  // agrupa visualmente por etapa/área
  let rows = "", lastStep = null;
  for (const a of game.achievements) {
    if (a.step !== lastStep) {
      lastStep = a.step;
      if (a.area) rows += `<p class="step-area">▸ ETAPA ${a.step} — ${esc(a.area)}</p>`;
    }
    const isNext = nextIds.includes(a.id);
    rows += `<div class="ach-row ${isNext ? "next" : ""} ${a.earned || isNext ? "" : "locked"}">
      ${badge(a)}
      <div class="ach-body">
        <div class="ach-titleline">
          <span class="ach-name">${esc(a.name)}</span>
          ${modeTag(a.mode)}
          ${isNext ? `<span class="next-tag">PRÓXIMO</span>` : ""}
        </div>
        <div class="ach-desc">${esc(a.desc)}</div>
      </div>
      ${a.earned ? `<span class="ach-check">✓</span>` : ""}
    </div>`;
  }

  return `<main class="main">
    <div class="panel-head" style="background:linear-gradient(180deg, ${game.accent}14, transparent)">
      <div class="head-top">
        <div class="head-ring">${ring(pct, 72, 5, game.accent)}<span class="ico">🎮</span></div>
        <div class="head-info">
          <h1>${esc(game.title)}</h1>
          <p class="plat">${esc(game.platform)}</p>
          <p class="total" style="color:${game.accent}">${e} / ${t} CONQUISTAS NO TOTAL · ${pct}%</p>
        </div>
      </div>
      <div class="chips-row">
        ${Object.entries(game.modes).map(([k, v]) => modeChip(k, v.earned, v.total)).join("")}
      </div>
      ${le ? `<div class="last-earned">
        <span class="spark">✦</span>
        <div class="le-body">
          <p class="le-label">ÚLTIMA CONQUISTA OBTIDA</p>
          <p class="le-name">${esc(le.name)}</p>
          <p class="le-desc">${esc(le.desc)}</p>
        </div>
        <span class="le-date">${esc(le.date)}</span>
      </div>` : ""}
    </div>
    <div class="list-wrap">
      <p class="list-title">ORDEM DO WALKTHROUGH</p>
      ${rows || `<p style="color:var(--text-low);font-size:11px">Sem conquistas no walkthrough.</p>`}
    </div>
  </main>`;
}

function bindSidebar() {
  root.querySelectorAll(".tile").forEach((b) => {
    b.onclick = async () => { S.activeSlug = b.dataset.slug; await renderDashboard(); };
  });
  const add = $("#btn-add");
  if (add) add.onclick = () => enterWizard1();
}

/* ============================== WIZARD ================================= */
function wizHeadHTML(step) {
  return `<div class="wiz-head">
    <button class="back" id="wiz-back">←</button>
    <div><div class="t">ADICIONAR JOGO</div><div class="s">PASSO ${step} DE 2</div></div>
  </div>`;
}

/* ---- Passo 1: buscar ---- */
function enterWizard1() {
  S.view = "wizard1";
  stopPolling();
  S.W = { results: [], query: "" };
  root.innerHTML = `<div class="view">
    ${wizHeadHTML(1)}
    <div class="wiz-body">
      <p style="color:var(--text-mid);font-size:12px;margin-bottom:12px">Busque o jogo na RetroAchievements — a lista completa de conquistas é carregada automaticamente.</p>
      <div class="search-box">
        <span style="color:var(--text-low)">🔍</span>
        <input id="wiz-q" placeholder="digite o nome do jogo…" autocomplete="off" spellcheck="false" />
      </div>
      <div id="wiz-results"></div>
    </div>
  </div>`;
  $("#wiz-back").onclick = enterDashboard;
  const input = $("#wiz-q");
  input.focus();
  let timer;
  input.oninput = () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { $("#wiz-results").innerHTML = ""; return; }
    timer = setTimeout(() => doSearch(q), 350);
  };
}

async function doSearch(q) {
  const box = $("#wiz-results");
  box.innerHTML = `<div class="status-msg">⏳ Buscando…</div>`;
  try {
    const res = await backend.search(q);
    if (res.building) {
      box.innerHTML = `<div class="status-msg">⏳ Montando catálogo de jogos pela primeira vez (pode levar ~1 min). Tente a busca novamente em instantes.</div>`;
      return;
    }
    if (!res.ready) { box.innerHTML = `<div class="status-msg">${esc(res.error || "Indisponível.")}</div>`; return; }
    const results = res.results || [];
    if (!results.length) { box.innerHTML = `<div class="status-msg">Nenhum resultado para "${esc(q)}".</div>`; return; }
    box.innerHTML = results.map((r) => `
      <button class="result-row" data-id="${r.id}">
        <div><div class="rt">${esc(r.title)}</div><div class="rs">${esc(r.console)} · ${r.count} conquistas</div></div>
        <span class="arrow">→</span>
      </button>`).join("");
    box.querySelectorAll(".result-row").forEach((b) => b.onclick = () => importGame(b.dataset.id, b));
  } catch (e) {
    box.innerHTML = `<div class="status-msg">Erro na busca: ${esc(e)}</div>`;
  }
}

async function importGame(id, btn) {
  if (btn) { btn.style.opacity = ".5"; btn.querySelector(".arrow").textContent = "⏳"; }
  try {
    const res = await backend.importGame(id);
    if (!res.ok) return toast(res.error || "Falha ao importar.", true);
    enterWizard2(res);
  } catch (e) { toast("Erro: " + e, true); }
}

/* ---- Passo 2: organizar walkthrough ---- */
function enterWizard2(imported) {
  S.view = "wizard2";
  const items = {};
  const order = [];
  for (const a of imported.achievements) {
    items[a.id] = { ...a };
    order.push(a.id);
  }
  S.W = {
    title: imported.title,
    platform: imported.platform,
    items, order,
    steps: [{ area: "", ids: [] }],
  };
  renderWizard2();
}

function assignedSet() {
  const s = new Set();
  S.W.steps.forEach((st) => st.ids.forEach((id) => s.add(id)));
  return s;
}

let DRAG = { id: null };  // id da conquista sendo arrastada

function renderWizard2() {
  const W = S.W;
  const assigned = assignedSet();
  const unsorted = W.order.filter((id) => !assigned.has(id));

  const modeToggle = (id) => {
    const m = W.items[id].mode;
    return `<div class="mode-toggle" data-id="${id}">
      <button class="${m === "normal" ? "on-normal" : ""}" data-mode="normal" draggable="false">N</button>
      <button class="${m === "hard" ? "on-hard" : ""}" data-mode="hard" draggable="false">H</button>
    </div>`;
  };

  const item = (id, inStep) => {
    const a = W.items[id];
    return `<div class="dnd-item" draggable="true" data-id="${id}">
      <span class="grip" title="Arraste">⠿</span>
      <span class="nm" title="${esc(a.title)}">${esc(a.title)}</span>
      ${modeToggle(id)}
      ${inStep ? `<button class="mini-btn rm" data-remove="${id}" title="Remover da etapa" draggable="false">×</button>` : ""}
    </div>`;
  };

  const unsortedHTML = unsorted.length
    ? unsorted.map((id) => item(id, false)).join("")
    : `<p class="zone-empty">Tudo organizado. Arraste de volta para cá para desfazer.</p>`;

  const stepsHTML = W.steps.map((st, i) => {
    const inner = st.ids.length
      ? st.ids.map((id) => item(id, true)).join("")
      : `<p class="empty">Arraste conquistas para esta etapa.</p>`;
    return `<div class="step-block dropzone" data-zone="step" data-step="${i}">
      <div class="sb-head">
        <span class="sb-title">Etapa ${i + 1} — <input data-area="${i}" value="${esc(st.area)}" placeholder="nome da área" /></span>
        <button class="sb-del" data-delstep="${i}" title="Remover etapa">🗑</button>
      </div>
      ${inner}
    </div>`;
  }).join("");

  root.innerHTML = `<div class="view">
    ${wizHeadHTML(2)}
    <div class="wiz-body">
      <p style="color:var(--text-mid);font-size:12px;margin-bottom:4px"><b style="color:var(--text-hi)">${esc(W.title)}</b> — arraste as conquistas para as etapas, na ordem do seu walkthrough.</p>
      <p style="color:var(--text-low);font-size:10.5px;margin-bottom:16px">A ordem é curatorial — segue o seu guia, não é gerada automaticamente. Marque o modo (N/ormal · H/ard) de cada conquista.</p>
      <div class="wiz2-cols">
        <div class="wiz2-col dropzone" data-zone="unsorted">
          <p class="col-label">Conquistas (${unsorted.length})</p>
          ${unsortedHTML}
        </div>
        <div class="wiz2-col">
          <p class="col-label">Estrutura do walkthrough</p>
          ${stepsHTML}
          <button class="new-step-btn" id="new-step">＋ Nova Etapa</button>
        </div>
      </div>
    </div>
    <div class="wiz-foot">
      <button class="btn-ghost" id="wiz-back">← Voltar</button>
      <button class="btn-primary gold" id="wiz-save">💾 Salvar e Voltar à Biblioteca</button>
    </div>
  </div>`;

  bindWizard2();
}

/* Acha o item após o qual inserir, comparando o cursor com o meio de cada item. */
function getDragAfterElement(zone, y) {
  const items = [...zone.querySelectorAll(".dnd-item:not(.dragging)")];
  for (const el of items) {
    const box = el.getBoundingClientRect();
    if (y < box.top + box.height / 2) return el;
  }
  return null;
}

function clearDropMarks() {
  root.querySelectorAll(".drop-before").forEach((e) => e.classList.remove("drop-before"));
  root.querySelectorAll(".drag-over, .drop-end").forEach((e) => e.classList.remove("drag-over", "drop-end"));
}

function moveTo(zoneEl, afterEl) {
  const W = S.W;
  const id = DRAG.id;
  if (id == null) return;
  // remove de qualquer etapa onde esteja
  W.steps.forEach((st) => { st.ids = st.ids.filter((x) => x !== id); });
  if (zoneEl.dataset.zone === "step") {
    const ids = W.steps[+zoneEl.dataset.step].ids;
    const afterId = afterEl ? +afterEl.dataset.id : null;
    let idx = afterId == null ? ids.length : ids.indexOf(afterId);
    if (idx < 0) idx = ids.length;
    ids.splice(idx, 0, id);
  }
  // zona "unsorted": já removido das etapas -> volta para o pool (ordem de import)
  renderWizard2();
}

function bindWizard2() {
  const W = S.W;
  $("#wiz-back").onclick = enterWizard1;
  $("#new-step").onclick = () => { W.steps.push({ area: "", ids: [] }); renderWizard2(); };

  // toggles de modo (não devem disparar drag)
  root.querySelectorAll(".mode-toggle").forEach((tg) => {
    tg.querySelectorAll("button").forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      W.items[tg.dataset.id].mode = b.dataset.mode; renderWizard2();
    });
  });
  // remover da etapa (botão ×)
  root.querySelectorAll("[data-remove]").forEach((b) => b.onclick = (e) => {
    e.stopPropagation();
    const id = +b.dataset.remove;
    W.steps.forEach((st) => { st.ids = st.ids.filter((x) => x !== id); });
    renderWizard2();
  });

  // --- HTML5 Drag and Drop ---
  root.querySelectorAll(".dnd-item").forEach((el) => {
    el.addEventListener("dragstart", (e) => {
      DRAG.id = +el.dataset.id;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", el.dataset.id);
      requestAnimationFrame(() => el.classList.add("dragging"));
    });
    el.addEventListener("dragend", () => { DRAG.id = null; clearDropMarks(); el.classList.remove("dragging"); });
  });

  root.querySelectorAll(".dropzone").forEach((zone) => {
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      clearDropMarks();
      zone.classList.add("drag-over");
      const after = getDragAfterElement(zone, e.clientY);
      if (after) after.classList.add("drop-before");
      else if (zone.dataset.zone === "step") zone.classList.add("drop-end");
    });
    zone.addEventListener("dragleave", (e) => {
      if (!zone.contains(e.relatedTarget)) { zone.classList.remove("drag-over", "drop-end"); }
    });
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      const after = getDragAfterElement(zone, e.clientY);
      clearDropMarks();
      moveTo(zone, after);
    });
  });

  // editar área (preserva foco/valor sem re-render a cada tecla)
  root.querySelectorAll("[data-area]").forEach((inp) => inp.oninput = () => {
    W.steps[+inp.dataset.area].area = inp.value;
  });
  // remover etapa
  root.querySelectorAll("[data-delstep]").forEach((b) => b.onclick = () => {
    if (W.steps.length <= 1) return toast("Mantenha ao menos uma etapa.", true);
    W.steps.splice(+b.dataset.delstep, 1); renderWizard2();
  });
  // salvar
  $("#wiz-save").onclick = saveWizard;
}

async function saveWizard() {
  const W = S.W;
  const walkthrough = W.steps
    .map((st, i) => ({
      step: i + 1,
      area: st.area || `Etapa ${i + 1}`,
      achievements: st.ids.map((id) => ({ id, mode: W.items[id].mode })),
    }))
    .filter((st) => st.achievements.length > 0);

  if (!walkthrough.length) return toast("Adicione conquistas a pelo menos uma etapa.", true);

  try {
    const res = await backend.saveGame({ walkthrough });
    if (!res.ok) return toast(res.error || "Falha ao salvar.", true);
    if (res.slug) S.activeSlug = res.slug;
    toast(res.demo ? "Demonstração — jogo não persistido." : "Jogo salvo!");
    await enterDashboard();
  } catch (e) { toast("Erro: " + e, true); }
}

/* ============================ JANELA / BOOT ============================ */
function bindWindowControls() {
  $("#btn-min")?.addEventListener("click", () => hasBackend() && window.pywebview.api.minimize());
  $("#btn-close")?.addEventListener("click", () => hasBackend() && window.pywebview.api.close());
  $("#btn-pin")?.addEventListener("click", (e) => {
    S.onTop = !S.onTop;
    e.currentTarget.classList.toggle("active", S.onTop);
    if (hasBackend()) window.pywebview.api.toggle_on_top(S.onTop);
  });
  $("#btn-pin")?.classList.add("active");
}

async function boot() {
  bindWindowControls();
  S.mode = hasBackend() ? "real" : "demo";
  if (S.mode === "demo") document.getElementById("demo-banner").classList.remove("hidden");
  // atalho de dev: ?screen=wizard2 abre direto o Passo 2 com dados de exemplo
  if (new URLSearchParams(location.search).get("screen") === "wizard2") {
    S.mode = "demo";
    document.getElementById("demo-banner").classList.remove("hidden");
    return enterWizard2(DEMO_IMPORT());
  }
  try {
    const st = await backend.appState();
    if (S.mode === "real" && !st.configured) renderSetup();
    else enterDashboard();
  } catch (e) {
    S.mode = "demo";
    document.getElementById("demo-banner").classList.remove("hidden");
    enterDashboard();
  }
}

// pywebview injeta a API de forma assíncrona. Boot quando a API existir
// (evento pywebviewready ou polling); só cai em modo demo se ela nunca aparecer.
let booted = false;
const startBoot = () => { if (!booted) { booted = true; boot(); } };
window.addEventListener("pywebviewready", startBoot);
window.addEventListener("DOMContentLoaded", () => {
  let waited = 0;
  const iv = setInterval(() => {
    waited += 200;
    if (booted) { clearInterval(iv); return; }
    if (hasBackend() || waited >= 3500) { clearInterval(iv); startBoot(); }
  }, 200);
});

/* ============================ DADOS DEMO ============================== */
function DEMO_LIB() {
  return DEMO.map((g) => ({ slug: g.slug, title: g.title, platform: g.platform, accent: g.accent, modes: g.modes }));
}
function DEMO_GAME(slug) { return DEMO.find((g) => g.slug === slug) || null; }
function DEMO_SEARCH() {
  return [
    { id: "survive", title: "Digimon Survive", console: "PlayStation 4", count: 64 },
    { id: "cyber", title: "Digimon Story: Cyber Sleuth", console: "PlayStation 4", count: 51 },
    { id: "rumble", title: "Digimon Rumble Arena 2", console: "PlayStation 2", count: 40 },
  ];
}
function DEMO_IMPORT() {
  return {
    ok: true, slug: "demo_novo", title: "Digimon Survive", platform: "PlayStation 4",
    achievements: [
      { id: 101, title: "First Partner", desc: "Recrute seu primeiro parceiro.", badge_url: "", mode: "normal" },
      { id: 102, title: "Jogress Evolution", desc: "Realize uma DNA digivolution.", badge_url: "", mode: "normal" },
      { id: 103, title: "Kaiser's Fall", desc: "Derrote o chefe no modo Hard.", badge_url: "", mode: "hard" },
      { id: 104, title: "Perfect Survivors", desc: "Termine sem perder nenhum aliado.", badge_url: "", mode: "hard" },
      { id: 105, title: "Card Master", desc: "Colete todas as cartas.", badge_url: "", mode: "normal" },
    ],
  };
}

const DEMO = [
  {
    slug: "digimon_world_4", title: "Digimon World 4", platform: "GameCube", accent: "#D62839",
    modes: { normal: { total: 4, earned: 1 }, hard: { total: 2, earned: 0 } },
    next_ids: [2, 3, 6],
    last_earned: { name: "Extravagant Petals", desc: "Complete Humid Cave on Normal difficulty.", date: "04/03/2026 · 22:08" },
    achievements: [
      { id: 1, name: "Extravagant Petals", desc: "Complete Humid Cave on Normal difficulty.", mode: "normal", earned: true, badge_url: "", step: 1, area: "Death Valley - Humid Cave" },
      { id: 2, name: "Tusks of Ash", desc: "Defeat Mammothmon in Cliff Dungeon.", mode: "normal", earned: false, badge_url: "", step: 1, area: "Death Valley - Humid Cave" },
      { id: 3, name: "Two Keys, One Fortress", desc: "Unlock the Goburimon Fortress with both IDs.", mode: "normal", earned: false, badge_url: "", step: 2, area: "Goburimon Fortress" },
      { id: 4, name: "Ferryman's Mercy", desc: "Rescue all 10 Digi-Elves at Numenume River.", mode: "normal", earned: false, badge_url: "", step: 2, area: "Goburimon Fortress" },
      { id: 6, name: "Death Valley, Twice Over", desc: "Complete Death Valley on Hard difficulty.", mode: "hard", earned: false, badge_url: "", step: 3, area: "Death Valley (Hard)" },
      { id: 8, name: "302 and Counting", desc: "Clear Undead Yard on Hard mode.", mode: "hard", earned: false, badge_url: "", step: 3, area: "Death Valley (Hard)" },
    ],
  },
  {
    slug: "digimon_world_2", title: "Digimon World 2", platform: "PlayStation", accent: "#F5C518",
    modes: { normal: { total: 1, earned: 1 }, hard: { total: 1, earned: 1 } },
    next_ids: [],
    last_earned: { name: "Master Tamer", desc: "Complete every battle on the final set.", date: "12/02/2026 · 19:41" },
    achievements: [
      { id: 1, name: "Digivolution Archivist", desc: "Record all DNA digivolution chains.", mode: "normal", earned: true, badge_url: "", step: 1, area: "Campanha" },
      { id: 2, name: "Master Tamer", desc: "Complete every battle on the final set.", mode: "hard", earned: true, badge_url: "", step: 2, area: "Pós-jogo" },
    ],
  },
  {
    slug: "digimon_digital_card_battle", title: "Digimon Digital Card Battle", platform: "PlayStation", accent: "#2DE2E6",
    modes: { normal: { total: 1, earned: 1 }, hard: { total: 2, earned: 0 } },
    next_ids: [2, 3],
    last_earned: { name: "Full Deck", desc: "Collect every card in the Omega set.", date: "28/01/2026 · 21:15" },
    achievements: [
      { id: 1, name: "Full Deck", desc: "Collect every card in the Omega set.", mode: "normal", earned: true, badge_url: "", step: 1, area: "Coleção" },
      { id: 2, name: "Arena Veteran", desc: "Win 20 ranked duels in a row.", mode: "hard", earned: false, badge_url: "", step: 2, area: "Arena" },
      { id: 3, name: "Black Card Hunter", desc: "Obtain all Black-rarity cards.", mode: "hard", earned: false, badge_url: "", step: 2, area: "Arena" },
    ],
  },
];
