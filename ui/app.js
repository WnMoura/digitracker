/* ===========================================================================
   DigiTracker — frontend (vanilla JS dentro do pywebview)
   Renderiza dashboard, wizard de adicionar jogo e tela de configuração.
   Cai em "modo demonstração" quando rodando sem o backend pywebview.
   =========================================================================== */

/* Como a RetroAchievements classifica cada desbloqueio — não é dificuldade do
   jogo. Hardcore (sem savestate) é o único que vale Mastery, por isso o dourado.
   Qualquer modo desconhecido que venha do backend ganha uma cor da paleta extra
   e o nome em caixa alta. */
const MODE_COLOR = { hardcore: "#E0B341", softcore: "#7FA8C9" };
const MODE_LABEL = { hardcore: "HARDCORE", softcore: "SOFTCORE" };
const MODE_ICON = { hardcore: "⚡", softcore: "○" };
const EXTRA_MODE_COLORS = ["#F5C518", "#27AE60", "#A855F7", "#FF8A3D"];
const C_LINE = "#23233a", C_LOCKED = "#2C2C42";

const modeColor = (key) => MODE_COLOR[key]
  || EXTRA_MODE_COLORS[Math.abs([...String(key)].reduce((h, c) => h + c.charCodeAt(0), 0)) % EXTRA_MODE_COLORS.length];
const modeLabel = (key) => MODE_LABEL[key] || String(key).toUpperCase();
const modeIcon = (key) => MODE_ICON[key] || "●";

/* '#RRGGBB' + alfa -> 'rgba(r,g,b,a)'. Usado nos fundos translúcidos dos chips
   (antes o código comparava a string da cor para escolher o rgba fixo). */
function tint(hex, alpha) {
  const n = parseInt(String(hex).replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/* ─────────────────  A COR DO JOGO VEM DA PRÓPRIA CAPA  ─────────────────
   Assinatura do design: cada jogo tinge a interface com a cor dominante da
   sua arte. Fazemos no <canvas>, sem dependência nenhuma, e guardamos em
   cache porque a capa não muda. Antes o app sorteava entre 4 cores fixas,
   que repetiam a partir do 5º jogo. */
const CORES_DA_CAPA = new Map();
const COR_PADRAO = "#4C9BE8";           // azul Steam, quando não dá para extrair

const rgbParaHsl = (r, g, b) => {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
  else if (max === g) h = ((b - r) / d + 2) / 6;
  else h = ((r - g) / d + 4) / 6;
  return [h, s, l];
};

const hslParaHex = (h, s, l) => {
  const f = (n) => {
    const k = (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    const v = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(v * 255).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
};

/* Extrai a cor e a domestica: mantém o matiz (a identidade do jogo), mas
   limita saturação e luminosidade. Sem isso, uma capa amarela vibrante vira
   superfície que cansa a vista, e uma capa escura vira accent invisível. */
function corDaCapa(url) {
  if (!url) return Promise.resolve(COR_PADRAO);
  if (CORES_DA_CAPA.has(url)) return Promise.resolve(CORES_DA_CAPA.get(url));

  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      let cor = COR_PADRAO;
      try {
        const N = 48;
        const cv = document.createElement("canvas");
        cv.width = cv.height = N;
        const ctx = cv.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, N, N);
        const px = ctx.getImageData(0, 0, N, N).data;
        let melhor = null, top = 0;
        for (let i = 0; i < px.length; i += 4) {
          if (px[i + 3] < 200) continue;
          const [h, s, l] = rgbParaHsl(px[i], px[i + 1], px[i + 2]);
          if (l < 0.18 || l > 0.85 || s < 0.30) continue;   // preto, branco, lavado
          if (s > top) { top = s; melhor = [h, s, l]; }
        }
        if (melhor) {
          const [h, s, l] = melhor;
          cor = hslParaHex(h, Math.min(s, 0.62), Math.min(Math.max(l, 0.42), 0.62));
        }
      } catch (_) { /* canvas bloqueado: fica o padrão */ }
      CORES_DA_CAPA.set(url, cor);
      resolve(cor);
    };
    img.onerror = () => { CORES_DA_CAPA.set(url, COR_PADRAO); resolve(COR_PADRAO); };
    img.src = url;
  });
}

/* Pré-carrega as cores da biblioteca inteira e re-renderiza quando chegarem. */
async function carregarCores(jogos) {
  const novas = (jogos || []).filter((g) => g.art?.box && !CORES_DA_CAPA.has(g.art.box));
  if (!novas.length) return false;
  await Promise.all(novas.map((g) => corDaCapa(g.art.box)));
  return true;
}

const corDe = (g) => (g && g.art?.box && CORES_DA_CAPA.get(g.art.box)) || (g && g.accent) || COR_PADRAO;

/* Fita de estado na capa. Só aparece quando há algo a dizer: jogo em
   progresso não recebe fita. */
function fitaDe(g) {
  const m = (g && g.mastery) || {};
  if (m.complete) return { cls: "mastery", txt: "Mastery" };
  if (m.total && m.earned >= m.total && !m.hardcore) return { cls: "softcore", txt: "Softcore" };
  return null;
}

const S = {
  mode: "real",          // 'real' | 'demo'
  view: "loading",
  library: [],
  activeSlug: null,
  tab: "walk",           // aba do painel: 'walk' | 'tips'
  onTop: true,
  compact: false,        // modo mini-overlay (progresso de conquistas)
  poll: null,
  sig: null,             // assinatura do último render (evita redesenhar à toa)
  screenKey: null,       // identidade da tela atual (p/ decidir se mantém o scroll)
  W: null,               // estado do wizard
  I: null,               // estado da importação em lote
  autoImport: true,      // espelhar a conta (jogo novo entra sozinho)
  autoOverlay: true,     // grudar no emulador quando ele abrir
  aiReady: false,        // há chave salva para o provedor de IA escolhido
  overlayExitFullscreen: false,  // mandar Alt+Enter ao detectar tela cheia exclusiva
  overlaySecondScreen: false,    // levar o overlay para o monitor livre
  G: null,               // estado do painel do GameFAQs
  AI: null,              // estado do painel de configuração da IA
  SET: null,             // estado da tela de Configurações
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
  async orderByPdfData(b64, name) {
    if (S.mode === "demo") return { ok: false, demo: true };
    return window.pywebview.api.order_by_pdf_data(b64, name);
  },
  async extractGuidePdf(b64, name) {
    if (S.mode === "demo") return { ok: false, demo: true };
    return window.pywebview.api.extract_guide_pdf(b64, name);
  },
  async attachGuidePdf(slug, b64, name) {
    if (S.mode === "demo") return { ok: false, demo: true };
    return window.pywebview.api.attach_guide_pdf(slug, b64, name);
  },
  async playedGames() {
    if (S.mode === "demo") return { ok: true, games: DEMO_PLAYED() };
    return window.pywebview.api.list_played_games();
  },
  async startBulkImport(ids) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.start_bulk_import(ids);
  },
  async bulkStatus() {
    if (S.mode === "demo") return { running: false };
    return window.pywebview.api.get_bulk_status();
  },
  async setAutoImport(v) {
    if (S.mode === "demo") return { ok: true, auto_import: v };
    return window.pywebview.api.set_auto_import(v);
  },
  async setAutoOverlay(v) {
    if (S.mode === "demo") return { ok: true, auto_overlay: v };
    return window.pywebview.api.set_auto_overlay(v);
  },
  async setOverlayOption(chave, v) {
    if (S.mode === "demo") return { ok: true };
    return window.pywebview.api.set_overlay_option(chave, v);
  },
  async gamefaqsList(url) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.gamefaqs_list(url);
  },
  async gamefaqsImport(url) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.gamefaqs_import(url);
  },
  async gamefaqsAttach(slug, url) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.gamefaqs_attach(slug, url);
  },
  async refineGuideAi() {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.refine_guide_ai();
  },
  async getAiConfig() {
    if (S.mode === "demo") return { ok: false };
    return window.pywebview.api.get_ai_config();
  },
  async setAiConfig(cfg) {
    if (S.mode === "demo") return { ok: false };
    return window.pywebview.api.set_ai_config(cfg.provider, cfg.api_key, cfg.model, cfg.base_url);
  },
};

/* O backend entra/sai do modo compacto sozinho quando detecta um emulador —
   ele chama esta função para a interface acompanhar na hora. */
window.onOverlayChanged = async (compact) => {
  S.compact = !!compact;
  const btn = document.getElementById("btn-compact");
  if (btn) {
    btn.classList.toggle("active", S.compact);
    btn.title = S.compact ? "Sair do modo compacto" : "Modo compacto (overlay de progresso)";
  }
  if (S.view === "dashboard") await renderDashboard({ force: true });
  if (S.compact) toast("Emulador detectado — overlay grudado na janela.");
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
  const col = modeColor(key);
  const lit = done || earned > 0;
  return `<div class="mode-chip" style="border-color:${lit ? col : C_LINE}">
    <span class="pip" style="background:${lit ? col : C_LOCKED}"></span>
    <span class="txt" style="color:${lit ? col : "var(--text-mid)"}">${modeIcon(key)} ${esc(modeLabel(key))} ${earned}/${total} · ${pct}%</span>
  </div>`;
}

/* Selo de como a conquista foi destravada. Só faz sentido em conquista obtida —
   bloqueada ainda não tem modo. */
function modeTag(mode) {
  const col = modeColor(mode);
  return `<span class="mode-tag" style="color:${col};background:${tint(col, .12)};border-color:${col}">${modeIcon(mode)} ${esc(modeLabel(mode))}</span>`;
}

/* Marcadores na lateral: quantas conquistas você já tem em cada modo. Aceso
   quando há pelo menos uma; cheio quando o modo está completo (Mastery). */
function modeDots(modes) {
  return `<div class="mode-dots">` + Object.entries(modes).map(([k, v]) => {
    const full = v.total > 0 && v.earned >= v.total;
    const lit = v.earned > 0;
    const col = modeColor(k);
    return `<span class="mode-dot" title="${esc(modeLabel(k))} ${v.earned}/${v.total}"
      style="border-color:${full ? col : lit ? tint(col, .45) : C_LINE};background:${lit ? tint(col, .1) : "transparent"}">
      <span class="pip" style="background:${lit ? col : C_LOCKED}"></span>
      <span class="lbl" style="color:${lit ? col : "var(--text-low)"}">${modeIcon(k)}${v.earned}</span>
    </span>`;
  }).join("") + `</div>`;
}

/* Total do jogo. Vem do bloco `mastery`: somar os modos daria o dobro, porque
   hardcore e softcore têm o mesmo denominador (toda conquista conta nos dois). */
const totals = (g) => {
  const m = (g && g.mastery) || {};
  const t = m.total || 0, e = m.earned || 0;
  return { t, e, pct: t ? Math.round((e / t) * 100) : 0 };
};
/* Concluído = Mastery, isto é, 100% em hardcore. */
const isMastered = (g) => !!(g && g.mastery && g.mastery.complete);
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
  await carregarCores(S.library);       // a cor de cada jogo vem da capa dele
  await renderDashboard({ force: true });
  startPolling();
}

function startPolling() {
  stopPolling();
  S.poll = setInterval(async () => {
    if (S.view !== "dashboard") return;
    try {
      S.library = await backend.library();
      await renderDashboard();
      await checkAutoImported();
    } catch (_) {}
  }, 5000);
}

/* Avisa quando a importação automática trouxe jogos novos — eles simplesmente
   aparecem na lateral, então sem o aviso a mudança passaria despercebida. */
async function checkAutoImported() {
  if (S.mode === "demo") return;
  const st = await backend.bulkStatus();
  /* aviso do overlay (ex.: emulador em tela cheia exclusiva, onde nenhum
     overlay aparece — sem isso o app simplesmente sumiria sem explicação) */
  if (st.overlay_notice) toast(st.overlay_notice, true);
  const novos = st.auto_imported || [];
  if (novos.length) {
    const nomes = novos.map((slug) => {
      const g = S.library.find((x) => x.slug === slug);
      return g ? g.title : slug;
    });
    toast(nomes.length === 1
      ? `Jogo novo importado: ${nomes[0]}`
      : `${nomes.length} jogos novos importados: ${nomes.slice(0, 2).join(", ")}${nomes.length > 2 ? "…" : ""}`);
    S.library = await backend.library();
    await renderDashboard({ force: true });
  }
}
function stopPolling() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }

/* Guarda/restaura o scroll dos painéis roláveis (marcados com data-scroll).
   Sem isso, todo redesenho joga a lista de conquistas de volta ao topo. */
function captureScroll() {
  const map = {};
  root.querySelectorAll("[data-scroll]").forEach((el) => { map[el.dataset.scroll] = el.scrollTop; });
  return map;
}
function restoreScroll(map) {
  root.querySelectorAll("[data-scroll]").forEach((el) => {
    const y = map[el.dataset.scroll];
    if (y) el.scrollTop = y;
  });
}

/* O polling roda a cada 5s, mas o conteúdo muda raramente. Redesenhamos só
   quando a assinatura (estado de UI + dados) muda de fato — assim a leitura da
   lista não é interrompida. `force` para transições de tela. */
async function renderDashboard({ force = false } = {}) {
  const game = S.activeSlug ? await backend.game(S.activeSlug) : null;
  const sig = JSON.stringify([S.mode, S.compact, S.tab, S.activeSlug, S.library, game]);
  if (!force && sig === S.sig) return;
  S.sig = sig;

  // Só faz sentido preservar o scroll se continuamos na MESMA tela; ao trocar
  // de jogo ou de aba o conteúdo é outro e deve começar do topo.
  const screenKey = `${S.compact}|${S.tab}|${S.activeSlug}`;
  const sameScreen = screenKey === S.screenKey;
  S.screenKey = screenKey;

  const scroll = captureScroll();
  document.getElementById("app").classList.toggle("compact", S.compact);
  if (S.compact) {
    root.innerHTML = compactHTML(game);
    bindCompact();
  } else {
    root.innerHTML = `${sidebarHTML()}${mainHTML(game)}`;
    bindSidebar();
  }
  // Trocou de jogo ou de aba? Começa do topo. Só faz sentido preservar a
  // rolagem quando o conteúdo é o mesmo (redesenho do polling de 5s).
  if (sameScreen) restoreScroll(scroll);
  else root.querySelectorAll("[data-scroll]").forEach((el) => { el.scrollTop = 0; });
  $("#sync-tag").textContent = S.mode === "demo" ? "DEMO" : "● SYNC 30s";
}

/* ========================= MODO COMPACTO (OVERLAY) ======================= */
/* Mini-quadro tipo o popup de conquistas do RetroArch: progresso do jogo
   ativo, última conquista obtida e a próxima a ser destravada. */
function compactHTML(game) {
  if (!game) {
    return `<div class="cw-empty">Nenhum jogo selecionado.<br>Volte ao modo completo para adicionar um.</div>`;
  }
  const { t, e, pct } = totals(game);
  const le = game.last_earned;
  const nextId = (game.next_ids || [])[0];
  const nextAch = nextId != null ? game.achievements.find((a) => a.id === nextId) : null;

  const badge = (url, fallback) => url
    ? `<div class="cw-badge${fallback === "🔒" ? " locked" : ""}" style="background-image:url('${esc(url)}')"></div>`
    : `<div class="cw-badge${fallback === "🔒" ? " locked" : ""}">${fallback}</div>`;

  const card = (label, a, locked) => a ? `<div class="cw-sec">
    <p class="cw-sec-label">${label}</p>
    <div class="cw-row">
      ${badge(a.badge_url, locked ? "🔒" : "🏆")}
      <div class="cw-info">
        <p class="cw-name">${esc(a.name)}</p>
        <p class="cw-desc">${esc(a.desc || "")}</p>
      </div>
    </div>
  </div>` : "";

  const mst = game.mastery || {};
  const cor = corDe(game);
  const capa = game.art?.box;
  return `<div class="cw" style="--jogo:${cor}">
    ${capa ? `<div class="cw-art" style="background-image:url('${esc(capa)}')"></div>` : ""}
    <div class="cw-in">
      <div class="cw-head">
        <button class="cw-nav" id="cw-prev" title="Jogo anterior">‹</button>
        <div class="cw-title" title="${esc(game.title)}">${esc(game.title)}</div>
        <button class="cw-nav" id="cw-next" title="Próximo jogo">›</button>
      </div>
      <div class="cw-top">
        <span class="cw-pills">
          <span class="cw-pill" title="Em hardcore, o que vale Mastery">${mst.hardcore || 0}<small>/${t}</small></span>
          <span class="cw-pill">${mst.percent || 0}%</span>
        </span>
      </div>
      <div class="cw-bar"><div class="cw-fill" style="width:${mst.percent || 0}%;background:${cor}"></div></div>
      ${card("Última conquista", le, false)}
      ${card("Próxima", nextAch, true)}
      ${!le && !nextAch ? (t > 0 && e >= t ? `<div class="cw-done">✓ 100% concluído</div>` : `<p class="cw-empty-sm">Sem conquistas registradas ainda.</p>`) : ""}
    </div>
  </div>`;
}

function bindCompact() {
  $("#cw-prev")?.addEventListener("click", () => switchCompactGame(-1));
  $("#cw-next")?.addEventListener("click", () => switchCompactGame(1));
}

function switchCompactGame(dir) {
  if (!S.library.length) return;
  const idx = S.library.findIndex((g) => g.slug === S.activeSlug);
  const next = (idx + dir + S.library.length) % S.library.length;
  S.activeSlug = S.library[next].slug;
  renderDashboard();
}

function sidebarHTML() {
  const done = S.library.filter(isMastered);
  const prog = S.library.filter((g) => !isMastered(g));
  /* Cápsula de capa: a arte identifica o jogo, a fita diz o que exige ação,
     e a selecionada brilha com a própria cor. */
  const tile = (g) => {
    const m = g.mastery || {};
    const active = g.slug === S.activeSlug;
    const cor = corDe(g);
    const fita = fitaDe(g);
    const capa = g.art?.box
      ? `<img src="${esc(g.art.box)}" alt="">`
      : `<div class="fallback">${esc(initials(g.title))}</div>`;
    return `<button class="tile ${active ? "active" : ""}" data-slug="${esc(g.slug)}" style="--jogo:${cor}">
      <div class="cover">
        ${capa}
        ${fita ? `<div class="ribbon ${fita.cls}">${fita.txt}</div>` : ""}
      </div>
      <div class="meta">
        <div class="name">${esc(g.title)}</div>
        <div class="tile-bar"><i style="width:${m.percent || 0}%"></i></div>
        <div class="tile-num">${m.hardcore || 0} / ${m.total || 0} · ${m.percent || 0}%</div>
      </div>
    </button>`;
  };
  return `<aside class="sidebar">
    <div class="sidebar-head">
      <h2>BIBLIOTECA</h2>
      <p>${S.library.length} ${S.library.length === 1 ? "jogo" : "jogos"} no ecossistema</p>
    </div>
    <div class="sidebar-list" data-scroll="sidebar">
      ${done.length ? `<p class="section-label done">★ MASTERY</p>${done.map(tile).join("")}<div style="height:8px"></div>` : ""}
      <p class="section-label progress">◎ EM PROGRESSO</p>
      ${prog.length ? prog.map(tile).join("") : `<p style="color:var(--text-low);font-size:11px;padding:4px">Nenhum jogo ainda.</p>`}
    </div>
    <div class="sidebar-foot">
      <button class="add-btn" id="btn-add">＋ Adicionar Jogo</button>
      <button class="import-btn" id="btn-import-all" title="Trazer de uma vez todos os jogos em que você já tem conquistas">⤓ Importar meus jogos</button>
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
  const { t, e, pct } = totals(game);
  const le = game.last_earned;
  const tips = (game.guide || []).length;
  const mst = game.mastery || {};

  const cor = corDe(game);
  const arte = game.art || {};
  return `<main class="main" data-scroll="main" style="--jogo:${cor}">
    ${arte.ingame ? `<div class="game-bg" style="background-image:url('${esc(arte.ingame)}')"></div>` : ""}
    <div class="game-glow"></div>
    <div class="panel-head">
      <div class="hero">
        ${arte.title ? `<div class="hero-art" style="background-image:url('${esc(arte.title)}')"></div>` : ""}
        <div class="hero-scrim"></div>
        <div class="hero-txt">
          <div class="head-info">
            <h1>${esc(game.title)}</h1>
            <p class="plat">${esc(game.platform)}</p>
            <p class="total">${e}<small>/${t}</small>
              <small class="sep">obtidas</small>
              <span class="mst">${mst.percent || 0}% rumo ao Mastery</span></p>
          </div>
          <div class="hero-bar" title="Progresso de Mastery: ${mst.hardcore || 0} de ${t} em hardcore">
            <i style="width:${mst.percent || 0}%"></i>
          </div>
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
      <div class="panel-tabs">
        <button class="ptab ${S.tab === "walk" ? "active" : ""}" data-tab="walk">Walkthrough</button>
        <button class="ptab ${S.tab === "mastery" ? "active" : ""}" data-tab="mastery">Mastery${mst.softcore_only ? `<span class="count">${mst.softcore_only}</span>` : ""}</button>
        <button class="ptab ${S.tab === "tips" ? "active" : ""}" data-tab="tips">Dicas${tips ? `<span class="count">${tips}</span>` : ""}</button>
      </div>
    </div>
    ${S.tab === "tips" ? guideHTML(game) : S.tab === "mastery" ? masteryHTML(game) : walkHTML(game)}
  </main>`;
}

/* ============================ ABA MASTERY ============================== */
/* O Mastery da RetroAchievements exige 100% em hardcore — savestate, rewind e
   cheat desqualificam o desbloqueio. Esta aba mostra o quanto falta e, mais
   útil, QUAIS conquistas você tem só em softcore e precisaria refazer. */
function masteryHTML(game) {
  const m = game.mastery || {};
  const total = m.total || 0;
  const bar = (label, value, color, extra = "") => {
    const pct = total ? Math.round((value / total) * 100) : 0;
    return `<div class="ms-row">
      <div class="ms-row-head">
        <span class="ms-label">${label}</span>
        <span class="ms-num" style="color:${color}">${value}/${total} · ${pct}%</span>
      </div>
      <div class="ms-bar"><div class="ms-fill" style="width:${pct}%;background:${color}"></div></div>
      ${extra ? `<p class="ms-note">${extra}</p>` : ""}
    </div>`;
  };

  const softIds = new Set(m.softcore_ids || []);
  const softRows = (game.achievements || []).filter((a) => softIds.has(a.id));
  const badge = (a) => a.badge_url
    ? `<div class="ach-badge soft" style="background-image:url('${esc(a.badge_url)}')"></div>`
    : `<div class="ach-badge soft">🏆</div>`;

  const header = m.complete
    ? `<div class="ms-done">★ MASTERY COMPLETO — ${total}/${total} em hardcore</div>`
    : `<div class="ms-remaining">FALTAM <b>${m.remaining || 0}</b> ${(m.remaining === 1) ? "CONQUISTA" : "CONQUISTAS"} PARA O MASTERY</div>`;

  const list = softRows.length ? `
    <p class="list-title" style="margin-top:22px">○ SÓ EM SOFTCORE (${softRows.length})</p>
    <p class="ms-hint">Destravadas com savestate/rewind. Para o Mastery precisam ser refeitas em hardcore.</p>
    ${softRows.map((a) => `<div class="ach-row soft">
      ${badge(a)}
      <div class="ach-body">
        <div class="ach-titleline">
          <span class="ach-name">${esc(a.name)}</span>
          ${modeTag("softcore")}
        </div>
        <div class="ach-desc">${esc(a.desc)}</div>
      </div>
      <span class="ach-date">${esc(a.date || "")}</span>
    </div>`).join("")}` : "";

  return `<div class="list-wrap">
    <p class="list-title">PROGRESSO DE MASTERY</p>
    ${bar("⚡ HARDCORE", m.hardcore || 0, MODE_COLOR.hardcore)}
    ${bar("○ SÓ EM SOFTCORE", m.softcore_only || 0, MODE_COLOR.softcore)}
    ${header}
    ${list}
    ${!softRows.length && !m.complete ? `<p class="ms-hint" style="margin-top:16px">Nenhuma conquista presa em softcore — tudo o que você destravou já vale Mastery.</p>` : ""}
  </div>`;
}

function walkHTML(game) {
  const nextIds = game.next_ids || [];
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
          ${a.earned ? modeTag(a.mode) : ""}
          ${isNext ? `<span class="next-tag">PRÓXIMO</span>` : ""}
        </div>
        <div class="ach-desc">${esc(a.desc)}</div>
      </div>
      ${a.earned ? `<span class="ach-check" style="color:${modeColor(a.mode)}">✓</span>` : ""}
    </div>`;
  }
  return `<div class="list-wrap">
    <p class="list-title">ORDEM DO WALKTHROUGH</p>
    ${rows || `<p style="color:var(--text-low);font-size:11px">Sem conquistas no walkthrough.</p>`}
  </div>`;
}

/* Renderiza as seções de dicas/tutoriais extraídas do PDF do guia. */
function guideHTML(game) {
  const secs = game.guide || [];
  const importBtn = S.mode === "demo" ? "" : `
    <button class="pdf-order-btn gf" id="guide-gamefaqs">🌐 ${secs.length ? "Substituir pelo GameFAQs" : "Importar do GameFAQs"}</button>
    <button class="pdf-order-btn" id="guide-import">📄 ${secs.length ? "Substituir dicas (PDF)" : "Importar dicas do PDF"}</button>`;
  if (!secs.length) {
    return `<div class="list-wrap"><p class="list-title">DICAS & TUTORIAIS</p>
      <p class="guide-empty">Nenhuma dica importada para este jogo.<br>
      Importe um PDF de guia abaixo — só as dicas/tutoriais, <b>sem alterar os troféus</b>.</p>
      ${importBtn}
    </div>`;
  }
  const block = (b) => {
    switch (b.type) {
      case "boss":    return `<div class="g-boss">⚔️ CHEFE: ${esc(b.text)}</div>`;
      case "step":    return `<div class="g-step"><span class="g-step-n">#${b.n}</span> ${esc(b.text)}</div>`;
      case "subhead": return `<div class="g-sub">${esc(b.text)}</div>`;
      case "note":    return `<div class="g-note">${esc(b.text)}</div>`;
      case "label":   return `<div class="g-row"><span class="g-lbl">${esc(b.label)}</span> ${esc(b.text)}</div>`;
      case "li":      return `<div class="g-li">${esc(b.text)}</div>`;
      default:        return `<p class="g-p">${esc(b.text)}</p>`;
    }
  };
  const section = (s) => `<section class="g-section">
    <p class="g-title">▸ ${esc(s.num)}. ${esc(s.title)}</p>
    ${(s.blocks || []).map(block).join("")}
  </section>`;
  return `<div class="list-wrap guide-wrap">
    <div class="guide-head">
      <p class="list-title" style="margin:0">DICAS & TUTORIAIS DO GUIA</p>
      ${importBtn}
    </div>
    ${secs.map(section).join("")}
  </div>`;
}

function bindSidebar() {
  root.querySelectorAll(".tile").forEach((b) => {
    b.onclick = async () => { S.activeSlug = b.dataset.slug; await renderDashboard(); };
  });
  root.querySelectorAll(".ptab").forEach((b) => {
    b.onclick = () => { S.tab = b.dataset.tab; renderDashboard(); };
  });
  const gimport = $("#guide-import");
  if (gimport) gimport.onclick = attachGuide;
  const gfaqs = $("#guide-gamefaqs");
  if (gfaqs) gfaqs.onclick = () => {
    if (S.mode === "demo") return toast("Disponível só no app real.", true);
    const jogo = S.library.find((g) => g.slug === S.activeSlug);
    openGameFaqs({
      title: jogo ? jogo.title : "",
      attachTo: S.activeSlug,
      onDone: async (res) => {
        toast(`${res.sections} seções de dicas importadas (conquistas intactas).`);
        await renderDashboard({ force: true });
      },
    });
  };
  const add = $("#btn-add");
  if (add) add.onclick = () => enterWizard1();
  const imp = $("#btn-import-all");
  if (imp) imp.onclick = () => enterImportAll();
}

/* ═══════════════════════════  CONFIGURAÇÕES  ═══════════════════════════
   Tela própria, aberta pelo ⚙ da barra de título. Antes o provedor de IA só
   era alcançável dentro do wizard de adicionar jogo — que a importação
   automática fez você nunca abrir. */
async function enterSettings() {
  S.view = "settings";
  stopPolling();
  const [estado, ia] = await Promise.all([
    backend.appState().catch(() => ({})),
    backend.getAiConfig().catch(() => ({ ok: false })),
  ]);
  S.SET = { estado, ia: ia && ia.ok ? ia : null };
  renderSettings();
}

function renderSettings() {
  const { estado, ia } = S.SET;
  const prov = ia && (ia.providers.find((p) => p.id === ia.provider) || ia.providers[0]);

  const chave = (id, txt, sub, ligado) => `
    <div class="set-row">
      <div><div class="set-txt">${esc(txt)}</div><div class="set-sub">${esc(sub)}</div></div>
      <button class="switch ${ligado ? "on" : ""}" data-toggle="${id}" role="switch"
              aria-checked="${!!ligado}" aria-label="${esc(txt)}"></button>
    </div>`;

  root.innerHTML = `<div class="view">
    <div class="wiz-head">
      <button class="back" id="set-back" title="Voltar">←</button>
      <div><div class="t">Configurações</div>
      <div class="s">Conta, inteligência artificial, biblioteca e overlay</div></div>
    </div>
    <div class="settings">
      <div class="settings-inner">

        <section class="set-section">
          <h3>Conta</h3>
          <div class="set-row">
            <div><div class="set-txt">RetroAchievements</div>
            <div class="set-sub">${estado.username ? esc(estado.username) : "não conectada"}</div></div>
            <button class="btn-ghost" id="set-reconnect">Trocar conta</button>
          </div>
        </section>

        <section class="set-section">
          <h3>Inteligência artificial</h3>
          <p class="set-hint">Opcional. Refina o guia importado do GameFAQs: reordena as
            conquistas pelo walkthrough e organiza as dicas. Cobrado pelo provedor que você
            escolher; sem chave, a importação continua funcionando pela heurística.</p>
          ${ia ? `
            <div class="ai-providers">
              ${ia.providers.map((p) => `
                <button class="ai-prov ${p.id === ia.provider ? "on" : ""}" data-prov="${esc(p.id)}">
                  <span class="ai-prov-name">${esc(p.label)}</span>
                  ${p.has_key ? `<span class="ai-prov-ok">✓ chave salva</span>` : ""}
                </button>`).join("")}
            </div>
            <label class="set-label">Chave da API${prov.has_key ? " (salva — deixe em branco para manter)" : ""}</label>
            <input class="set-field" id="set-key" type="password" autocomplete="off" spellcheck="false"
                   placeholder="${prov.has_key ? "••••••••••••••••" : "cole a chave aqui"}" />
            <label class="set-label">Modelo</label>
            <input class="set-field" id="set-model" autocomplete="off" spellcheck="false"
                   value="${esc(ia.model || "")}" placeholder="${esc(prov.default_model)}" />
            ${prov.needs_base_url ? `
              <label class="set-label">Endpoint (OpenRouter, Ollama, LM Studio…)</label>
              <input class="set-field" id="set-base" autocomplete="off" spellcheck="false"
                     value="${esc(ia.base_url || "")}" placeholder="${esc(prov.default_base_url || "")}" />` : ""}
            <p class="set-hint">A chave fica só em <code>config/secrets.json</code>, nesta máquina.
              Obter em <span class="ai-link">${esc(prov.key_url || "")}</span></p>
            <div style="display:flex;gap:8px">
              <button class="btn-primary" id="set-ai-save">Salvar</button>
              <button class="btn-ghost" id="set-ai-clear">Remover chave</button>
            </div>` : `<p class="set-hint">Indisponível no modo demonstração.</p>`}
        </section>

        <section class="set-section">
          <h3>Biblioteca</h3>
          ${chave("auto_import", "Importar jogos novos automaticamente",
                  "Verifica a cada 5 minutos e traz os jogos em que você começou a jogar",
                  S.autoImport)}
        </section>

        <section class="set-section">
          <h3>Overlay</h3>
          ${chave("auto_overlay", "Grudar no emulador",
                  "Vira overlay e acompanha a janela quando um emulador abre", S.autoOverlay)}
          ${chave("overlay_exit_fullscreen", "Sair do fullscreen exclusivo",
                  "Manda Alt+Enter para o emulador. Nenhum overlay aparece sobre tela cheia exclusiva, então esta é a única forma de continuar vendo o progresso num monitor só",
                  S.overlayExitFullscreen)}
          ${chave("overlay_second_screen", "Usar o segundo monitor",
                  "Com dois monitores, leva o overlay para a tela que o jogo não ocupa",
                  S.overlaySecondScreen)}
        </section>

      </div>
    </div>
  </div>`;

  $("#set-back").onclick = enterDashboard;
  $("#set-reconnect").onclick = () => { S.mode = "real"; renderSetup(); };
  root.querySelectorAll("[data-toggle]").forEach((b) => {
    b.onclick = () => alternarPreferencia(b.dataset.toggle, b);
  });
  root.querySelectorAll("[data-prov]").forEach((b) => {
    b.onclick = () => { S.SET.ia.provider = b.dataset.prov; S.SET.ia.model = ""; renderSettings(); };
  });
  const salvar = $("#set-ai-save");
  if (salvar) salvar.onclick = () => salvarIa(null);
  const limpar = $("#set-ai-clear");
  if (limpar) limpar.onclick = () => salvarIa("");
}

async function alternarPreferencia(chave, botao) {
  const novo = !botao.classList.contains("on");
  botao.classList.toggle("on", novo);
  botao.setAttribute("aria-checked", String(novo));
  if (chave === "auto_import") { S.autoImport = novo; await backend.setAutoImport(novo); }
  else if (chave === "auto_overlay") { S.autoOverlay = novo; await backend.setAutoOverlay(novo); }
  else {
    if (chave === "overlay_exit_fullscreen") S.overlayExitFullscreen = novo;
    else S.overlaySecondScreen = novo;
    await backend.setOverlayOption(chave, novo);
  }
}

async function salvarIa(forcarChave) {
  const digitada = ($("#set-key")?.value || "").trim();
  const res = await backend.setAiConfig({
    provider: S.SET.ia.provider,
    api_key: forcarChave !== null ? forcarChave : (digitada || null),
    model: ($("#set-model")?.value || "").trim(),
    base_url: ($("#set-base")?.value || "").trim(),
  });
  if (!res || !res.ok) return toast("Não foi possível salvar.", true);
  S.aiReady = !!res.ai_ready;
  S.SET.ia = res;
  renderSettings();
  const nome = res.providers.find((p) => p.id === res.provider).label;
  toast(S.aiReady ? `IA pronta via ${nome}.` : "Chave removida.");
}

/* ========================= IMPORTAR DO GAMEFAQS ========================= */
/* Cole a URL do jogo (ou de um guia) no GameFAQs: o app lista os guias, baixa o
   escolhido, ordena as conquistas pela ordem do texto e preenche as dicas.
   `onDone` decide o que fazer com o resultado (wizard x dashboard). */
function openGameFaqs({ title, onDone, attachTo = null }) {
  S.G = { faqs: [], url: "", busy: false, error: "", step: "url", attachTo, onDone, title };
  renderGameFaqs();
}

function closeGameFaqs() {
  S.G = null;
  const el = $("#gf-modal");
  if (el) el.remove();
}

function renderGameFaqs() {
  const G = S.G;
  if (!G) return;
  let el = $("#gf-modal");
  if (!el) {
    el = document.createElement("div");
    el.id = "gf-modal";
    el.className = "gf-backdrop";
    document.body.appendChild(el);
  }

  const lista = G.faqs.map((f) => `
    <button class="gf-faq" data-url="${esc(f.url)}">
      <span class="gf-faq-title">${esc(f.title)}</span>
      <span class="gf-faq-id">#${esc(f.id)}</span>
    </button>`).join("");

  el.innerHTML = `<div class="gf-panel">
    <div class="gf-head">
      <div>
        <div class="gf-title">🌐 IMPORTAR DO GAMEFAQS</div>
        <div class="gf-sub">${esc(G.title || "")}</div>
      </div>
      <button class="gf-close" id="gf-x">✕</button>
    </div>

    <div class="gf-body">
      ${G.busy ? `<div class="status-msg">⏳ ${esc(G.busyMsg || "Falando com o GameFAQs…")}</div>` : ""}
      ${G.error ? `<div class="gf-error">${esc(G.error)}</div>` : ""}

      ${G.step === "url" && !G.busy ? `
        <p class="gf-lead">Cole o endereço da aba <b>FAQs/Guides</b> do jogo — ou de um guia específico.</p>
        <div class="search-box">
          <span style="color:var(--text-low)">🔗</span>
          <input id="gf-url" placeholder="https://gamefaqs.gamespot.com/ps2/580782-digimon-world-4/faqs"
                 autocomplete="off" spellcheck="false" value="${esc(G.url)}" />
        </div>
        <p class="gf-note">O GameFAQs limita a velocidade de acesso: listar leva alguns segundos e baixar um guia grande pode levar um minuto.</p>
      ` : ""}

      ${G.step === "list" && !G.busy ? `
        <p class="gf-lead">${G.faqs.length} guia${G.faqs.length === 1 ? "" : "s"} disponíve${G.faqs.length === 1 ? "l" : "is"} — escolha um:</p>
        ${lista}
      ` : ""}
    </div>

    <div class="gf-foot">
      ${G.step === "list" && !G.busy
        ? `<button class="btn-ghost" id="gf-back">← Outra URL</button>`
        : `<span></span>`}
      ${G.step === "url" && !G.busy
        ? `<button class="btn-primary gold" id="gf-go">Listar guias →</button>`
        : `<span></span>`}
    </div>
  </div>`;

  $("#gf-x").onclick = closeGameFaqs;
  const go = $("#gf-go");
  if (go) go.onclick = gfListar;
  const back = $("#gf-back");
  if (back) back.onclick = () => { G.step = "url"; G.faqs = []; renderGameFaqs(); };
  const input = $("#gf-url");
  if (input) {
    input.focus();
    input.onkeydown = (e) => { if (e.key === "Enter") gfListar(); };
  }
  el.querySelectorAll(".gf-faq").forEach((b) => {
    b.onclick = () => gfBaixar(b.dataset.url);
  });
}

async function gfListar() {
  const G = S.G;
  G.url = ($("#gf-url")?.value || "").trim();
  if (!G.url) return;
  G.busy = true; G.busyMsg = "Procurando os guias…"; G.error = "";
  renderGameFaqs();
  try {
    const res = await backend.gamefaqsList(G.url);
    G.busy = false;
    if (!res.ok) { G.error = res.error || "Não consegui listar os guias."; }
    else { G.faqs = res.faqs || []; G.step = "list"; }
  } catch (e) {
    G.busy = false;
    G.error = "Erro: " + e;
  }
  renderGameFaqs();
}

async function gfBaixar(url) {
  const G = S.G;
  G.busy = true;
  G.busyMsg = "Baixando o guia… isso pode levar um minuto.";
  G.error = "";
  renderGameFaqs();
  try {
    const res = G.attachTo
      ? await backend.gamefaqsAttach(G.attachTo, url)
      : await backend.gamefaqsImport(url);
    G.busy = false;
    if (!res.ok) { G.error = res.error || "Falha ao baixar o guia."; renderGameFaqs(); return; }
    closeGameFaqs();
    G.onDone(res);
  } catch (e) {
    G.busy = false;
    G.error = "Erro: " + e;
    renderGameFaqs();
  }
}

/* Refina o guia recém-importado com a IA (opcional, custa por uso). */
async function refinarComIa() {
  if (!S.aiReady) return pedirChaveIa();
  const btn = $("#wiz-ai");
  if (btn) { btn.disabled = true; btn.textContent = "✨ Refinando com IA…"; }
  try {
    const res = await backend.refineGuideAi();
    if (!res.ok) return toast(res.error || "Falha ao refinar.", true);
    S.W.guide = res.guide || [];
    applyPdfOrder(res.ordered_ids || []);
    toast(`IA reordenou ${res.matched_by_name}/${res.total} conquistas · ${(res.guide || []).length} seções de dicas.`);
  } catch (e) {
    toast("Erro ao refinar: " + e, true);
  } finally {
    const b = $("#wiz-ai");
    if (b) { b.disabled = false; b.textContent = "✨ Refinar com IA"; }
  }
}

/* ---- Configuração do provedor de IA (Claude, Gemini, OpenAI-compatível) ---- */
async function pedirChaveIa() {
  const cfg = await backend.getAiConfig();
  if (!cfg || !cfg.ok) return toast("Configuração de IA indisponível.", true);
  S.AI = cfg;
  renderAiConfig();
}

function renderAiConfig() {
  const cfg = S.AI;
  let el = $("#ai-modal");
  if (!el) {
    el = document.createElement("div");
    el.id = "ai-modal";
    el.className = "gf-backdrop";
    document.body.appendChild(el);
  }
  const atual = cfg.providers.find((p) => p.id === cfg.provider) || cfg.providers[0];

  el.innerHTML = `<div class="gf-panel">
    <div class="gf-head">
      <div>
        <div class="gf-title">✨ REFINAR GUIA COM IA</div>
        <div class="gf-sub">opcional · cobrado pelo provedor que você escolher</div>
      </div>
      <button class="gf-close" id="ai-x">✕</button>
    </div>
    <div class="gf-body">
      <div class="field">
        <label>Provedor</label>
        <div class="ai-providers">
          ${cfg.providers.map((p) => `
            <button class="ai-prov ${p.id === cfg.provider ? "on" : ""}" data-prov="${esc(p.id)}">
              <span class="ai-prov-name">${esc(p.label)}</span>
              ${p.has_key ? `<span class="ai-prov-ok">✓ chave salva</span>` : ""}
            </button>`).join("")}
        </div>
      </div>
      <div class="field">
        <label>API key ${atual.has_key ? "(já salva — deixe em branco para manter)" : ""}</label>
        <input id="ai-key" type="password" autocomplete="off" spellcheck="false"
               placeholder="${atual.has_key ? "••••••••••••" : "cole a chave aqui"}" />
        <p class="hint">Fica só em <code>config/secrets.json</code>, nesta máquina. Obter em
          <span class="ai-link">${esc(atual.key_url || "")}</span></p>
      </div>
      <div class="field">
        <label>Modelo (opcional)</label>
        <input id="ai-model" autocomplete="off" spellcheck="false"
               value="${esc(cfg.model || "")}" placeholder="${esc(atual.default_model)}" />
      </div>
      ${atual.needs_base_url ? `
      <div class="field">
        <label>Endpoint (para OpenRouter, Ollama, LM Studio…)</label>
        <input id="ai-base" autocomplete="off" spellcheck="false"
               value="${esc(cfg.base_url || "")}" placeholder="${esc(atual.default_base_url || "")}" />
      </div>` : ""}
    </div>
    <div class="gf-foot">
      <button class="btn-ghost" id="ai-clear">Remover chave</button>
      <button class="btn-primary gold" id="ai-save">Salvar</button>
    </div>
  </div>`;

  $("#ai-x").onclick = () => el.remove();
  el.querySelectorAll(".ai-prov").forEach((b) => {
    b.onclick = () => { S.AI.provider = b.dataset.prov; S.AI.model = ""; renderAiConfig(); };
  });
  $("#ai-save").onclick = () => salvarAiConfig(null);
  $("#ai-clear").onclick = () => salvarAiConfig("");
}

async function salvarAiConfig(forcarChave) {
  const el = $("#ai-modal");
  const digitada = ($("#ai-key")?.value || "").trim();
  // null = mantém a chave salva; "" = apaga
  const api_key = forcarChave !== null ? forcarChave : (digitada || null);
  const res = await backend.setAiConfig({
    provider: S.AI.provider,
    api_key,
    model: ($("#ai-model")?.value || "").trim(),
    base_url: ($("#ai-base")?.value || "").trim(),
  });
  if (!res || !res.ok) return toast("Não foi possível salvar.", true);
  S.aiReady = !!res.ai_ready;
  if (el) el.remove();
  toast(S.aiReady
    ? `IA pronta via ${res.providers.find((p) => p.id === res.provider).label}.`
    : "Chave removida — o refino por IA fica desligado.");
  if (S.view === "wizard2") renderWizard2();
}

/* ====================== IMPORTAR BIBLIOTECA (LOTE) ====================== */
/* Traz de uma vez todos os jogos em que você já tem conquistas na RA, em vez de
   cadastrar um a um pelo wizard. Cada jogo entra na ordem nativa do RA. */
async function enterImportAll() {
  S.view = "importAll";
  stopPolling();
  S.I = { games: [], sel: new Set(), loading: true, error: "" };
  renderImportAll();

  try {
    const res = await backend.playedGames();
    S.I.loading = false;
    if (!res.ok) S.I.error = res.error || "Não foi possível listar seus jogos.";
    else {
      S.I.games = res.games || [];
      // pré-seleciona o que ainda não está na biblioteca
      S.I.games.forEach((g) => { if (!g.imported) S.I.sel.add(g.id); });
    }
  } catch (e) {
    S.I.loading = false;
    S.I.error = "Erro ao consultar a RetroAchievements: " + e;
  }
  renderImportAll();
}

const AWARD_LABEL = {
  "mastered": "★ MASTERY",
  "completed": "✓ 100% SOFTCORE",
  "beaten-hardcore": "⚡ ZERADO HARDCORE",
  "beaten-softcore": "○ ZERADO SOFTCORE",
};

function renderImportAll() {
  const I = S.I;
  const body = I.loading
    ? `<div class="status-msg">⏳ Consultando seus jogos na RetroAchievements…</div>`
    : I.error
      ? `<div class="status-msg">${esc(I.error)}</div>`
      : I.games.length
        ? importListHTML(I)
        : `<div class="status-msg">Nenhum jogo com conquistas encontrado nesta conta.</div>`;

  const selCount = I.sel.size;
  root.innerHTML = `<div class="view">
    <div class="wiz-head">
      <button class="back" id="imp-back">←</button>
      <div><div class="t">IMPORTAR MEUS JOGOS</div><div class="s">TUDO QUE VOCÊ JÁ COMEÇOU NA RETROACHIEVEMENTS</div></div>
    </div>
    <div class="wiz-body">${body}</div>
    <div class="wiz-foot">
      <button class="btn-ghost" id="imp-back2">← Voltar</button>
      <label class="auto-toggle" title="Verifica a cada 5 minutos e traz sozinho os jogos que você começar">
        <input type="checkbox" id="imp-auto" ${S.autoImport ? "checked" : ""} />
        <span>Importar jogos novos automaticamente</span>
      </label>
      <label class="auto-toggle" title="Ao abrir um emulador, vira overlay e gruda no canto da janela dele">
        <input type="checkbox" id="imp-overlay" ${S.autoOverlay ? "checked" : ""} />
        <span>Grudar no emulador automaticamente</span>
      </label>
      <span class="imp-count">${selCount} selecionado${selCount === 1 ? "" : "s"}</span>
      <button class="btn-primary gold" id="imp-go" ${selCount ? "" : "disabled"}>⤓ Importar ${selCount || ""}</button>
    </div>
  </div>`;

  $("#imp-back").onclick = enterDashboard;
  $("#imp-back2").onclick = enterDashboard;
  $("#imp-go").onclick = runBulkImport;
  $("#imp-auto").onchange = async (e) => {
    S.autoImport = e.target.checked;
    await backend.setAutoImport(S.autoImport);
    toast(S.autoImport
      ? "Jogos novos passarão a entrar sozinhos."
      : "Importação automática desligada.");
  };
  $("#imp-overlay").onchange = async (e) => {
    S.autoOverlay = e.target.checked;
    await backend.setAutoOverlay(S.autoOverlay);
    toast(S.autoOverlay
      ? "O app vai grudar no emulador quando ele abrir."
      : "Overlay automático desligado.");
  };
  $("#imp-all")?.addEventListener("click", () => {
    const livres = I.games.filter((g) => !g.imported);
    const todos = livres.every((g) => I.sel.has(g.id));
    livres.forEach((g) => todos ? I.sel.delete(g.id) : I.sel.add(g.id));
    renderImportAll();
  });
  root.querySelectorAll("[data-gid]").forEach((el) => {
    el.onclick = () => {
      const id = +el.dataset.gid;
      if (I.sel.has(id)) I.sel.delete(id); else I.sel.add(id);
      renderImportAll();
    };
  });
}

function importListHTML(I) {
  const novos = I.games.filter((g) => !g.imported).length;
  const row = (g) => {
    const on = I.sel.has(g.id);
    const pct = g.total ? Math.round((g.earned / g.total) * 100) : 0;
    const award = AWARD_LABEL[g.award] || "";
    return `<button class="imp-row ${on ? "on" : ""} ${g.imported ? "already" : ""}"
        ${g.imported ? "" : `data-gid="${g.id}"`}>
      <span class="imp-check">${g.imported ? "✓" : on ? "☑" : "☐"}</span>
      <span class="imp-info">
        <span class="imp-title">${esc(g.title)}</span>
        <span class="imp-sub">${esc(g.console)} · ${g.earned}/${g.total} · ${pct}%${award ? ` · ${award}` : ""}</span>
      </span>
      <span class="imp-modes">
        ${g.hardcore ? `<span class="imp-pill" style="color:${MODE_COLOR.hardcore};border-color:${tint(MODE_COLOR.hardcore, .5)}">⚡${g.hardcore}</span>` : ""}
        ${g.softcore_only ? `<span class="imp-pill" style="color:${MODE_COLOR.softcore};border-color:${tint(MODE_COLOR.softcore, .5)}">○${g.softcore_only}</span>` : ""}
      </span>
      ${g.imported ? `<span class="imp-already">JÁ NA BIBLIOTECA</span>` : ""}
    </button>`;
  };
  return `
    <div class="imp-head">
      <p class="imp-lead">${I.games.length} jogo${I.games.length === 1 ? "" : "s"} com conquistas nesta conta${novos ? ` · ${novos} fora da biblioteca` : " · todos já importados"}.</p>
      ${novos ? `<button class="pdf-order-btn ghost" id="imp-all">Marcar/desmarcar todos</button>` : ""}
    </div>
    <p class="imp-note">Cada jogo entra com as conquistas na ordem nativa do RetroAchievements — a mesma coisa que o wizard faria. Dá para reordenar depois pelo PDF do guia.</p>
    ${I.games.map(row).join("")}`;
}

async function runBulkImport() {
  const ids = [...S.I.sel];
  if (!ids.length) return;
  const res = await backend.startBulkImport(ids);
  if (!res.ok) return toast(res.error || "Não foi possível iniciar.", true);

  S.view = "importRun";
  renderImportProgress({ running: true, done: 0, total: ids.length, current: "", phase: "games", errors: [] });

  const poll = setInterval(async () => {
    let st;
    try { st = await backend.bulkStatus(); } catch (_) { return; }
    renderImportProgress(st);
    if (!st.running) {
      clearInterval(poll);
      const n = (st.imported || []).length;
      toast(st.errors && st.errors.length
        ? `${n} jogo(s) importado(s), ${st.errors.length} com erro.`
        : `${n} jogo(s) importado(s)!`, !!(st.errors && st.errors.length));
      setTimeout(() => { S.activeSlug = null; enterDashboard(); }, 1200);
    }
  }, 600);
}

function renderImportProgress(st) {
  const games = st.total ? Math.round((st.done / st.total) * 100) : 0;
  const badges = st.badges_total ? Math.round((st.badges_done / st.badges_total) * 100) : 0;
  const naBadges = st.phase === "badges" || st.phase === "done";

  root.innerHTML = `<div class="view">
    <div class="wiz-head">
      <div><div class="t">IMPORTANDO…</div><div class="s">NÃO FECHE O APP</div></div>
    </div>
    <div class="wiz-body">
      <div class="ms-row">
        <div class="ms-row-head">
          <span class="ms-label">JOGOS</span>
          <span class="ms-num" style="color:var(--gold)">${st.done || 0}/${st.total || 0}</span>
        </div>
        <div class="ms-bar"><div class="ms-fill" style="width:${games}%;background:var(--gold)"></div></div>
        ${st.current ? `<p class="ms-note">${esc(st.current)}</p>` : ""}
      </div>
      ${naBadges ? `<div class="ms-row">
        <div class="ms-row-head">
          <span class="ms-label">ÍCONES DAS CONQUISTAS</span>
          <span class="ms-num" style="color:var(--cyan)">${st.badges_done || 0}/${st.badges_total || 0}</span>
        </div>
        <div class="ms-bar"><div class="ms-fill" style="width:${badges}%;background:var(--cyan)"></div></div>
        <p class="ms-note">São centenas de arquivos pequenos — os jogos já estão utilizáveis.</p>
      </div>` : ""}
      ${(st.errors || []).length ? `<div class="imp-errors">
        <p class="ms-label">ERROS</p>
        ${st.errors.map((e) => `<p class="ms-note">${esc(e)}</p>`).join("")}
      </div>` : ""}
      ${!st.running ? `<div class="ms-done">✓ IMPORTAÇÃO CONCLUÍDA</div>` : ""}
    </div>
  </div>`;
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
    icon: imported.icon || "",
    items, order,
    // Já vem pré-ordenado pela ordem nativa do RetroAchievements (sem arrastar).
    steps: [{ area: "Ordem RetroAchievements", ids: order.slice() }],
    guide: null,            // seções de dicas/tutoriais (preenchido ao ler o PDF)
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

  const item = (id, inStep) => {
    const a = W.items[id];
    return `<div class="dnd-item" draggable="true" data-id="${id}">
      <span class="grip" title="Arraste">⠿</span>
      <span class="nm" title="${esc(a.title)}">${esc(a.title)}</span>
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
      <p style="color:var(--text-mid);font-size:12px;margin-bottom:4px"><b style="color:var(--text-hi)">${esc(W.title)}</b> — as conquistas já vêm na <b style="color:var(--cyan)">ordem do RetroAchievements</b>. É só <b style="color:var(--gold)">Salvar</b>.</p>
      <p style="color:var(--text-low);font-size:10.5px;margin-bottom:12px">Quer ajustar? Arraste para reordenar, ou use "Ordenar pelo PDF do guia" abaixo (também traz as dicas/tutoriais). Softcore/hardcore vem da RetroAchievements — nada para marcar aqui.</p>
      <div class="pdf-order-row">
        <button class="pdf-order-btn gf" id="wiz-gamefaqs">🌐 Importar do GameFAQs</button>
        <button class="pdf-order-btn" id="pdf-order">📄 Ordenar pelo PDF do guia</button>
        <button class="pdf-order-btn ghost" id="pdf-tips">📖 Só as dicas (não reordena)</button>
        ${S.W.guide && S.W.guide.length ? `<button class="pdf-order-btn ai" id="wiz-ai">✨ Refinar com IA</button>` : ""}
        <button class="pdf-order-btn ghost" id="wiz-ai-cfg" title="Escolher provedor de IA (Claude, Gemini, OpenAI…)">⚙</button>
        <span class="pdf-order-hint">Cole a URL do guia no GameFAQs e o app baixa, ordena as conquistas e traz as dicas — sem precisar montar PDF.</span>
      </div>
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
  // ordenar pelo PDF do guia / importar só as dicas
  $("#pdf-order").onclick = orderByPdf;
  $("#pdf-tips").onclick = tipsOnlyPdf;
  // importar direto do GameFAQs (sem PDF) e refinar com IA
  $("#wiz-gamefaqs").onclick = () => {
    if (S.mode === "demo") return toast("Disponível só no app real.", true);
    openGameFaqs({
      title: S.W.title,
      onDone: (res) => {
        S.W.guide = res.guide || [];
        applyPdfOrder(res.ordered_ids || []);
        const achadas = res.matched_by_name != null ? res.matched_by_name : res.found;
        toast(`${achadas}/${res.total} conquistas posicionadas pelo guia · ${(res.guide || []).length} seções de dicas.`);
      },
    });
  };
  const ai = $("#wiz-ai");
  if (ai) ai.onclick = refinarComIa;
  const aiCfg = $("#wiz-ai-cfg");
  if (aiCfg) aiCfg.onclick = () => {
    if (S.mode === "demo") return toast("Disponível só no app real.", true);
    pedirChaveIa();
  };
  // salvar
  $("#wiz-save").onclick = saveWizard;
}

/* Abre o seletor nativo de arquivo (via WebView2), lê o PDF como base64 e
   chama `onPick(b64, nome)`. Reaproveitado por todas as ações de PDF. */
function pickPdf(onPick) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "application/pdf,.pdf";
  input.style.display = "none";
  document.body.appendChild(input);
  input.onchange = async () => {
    const file = input.files && input.files[0];
    document.body.removeChild(input);
    if (!file) return;                                      // cancelado
    try {
      const b64 = await fileToBase64(file);
      await onPick(b64, file.name);
    } catch (e) {
      toast("Erro ao ler o arquivo: " + e, true);
    }
  };
  input.click();                                            // abre o seletor (gesto do usuário)
}

/* Lê o PDF do guia e pré-ordena as conquistas + captura as dicas. O que não
   casar fica logo abaixo na ordem do RA — nada é perdido. */
function orderByPdf() {
  if (S.mode === "demo") {
    toast("Ordenação por PDF só funciona no app real (não na demonstração).", true);
    return;
  }
  pickPdf(async (b64, name) => {
    const btn = $("#pdf-order");
    if (btn) { btn.disabled = true; btn.textContent = "📄 Lendo PDF…"; }
    try {
      const res = await backend.orderByPdfData(b64, name);
      if (!res || !res.ok) return toast((res && res.error) || "Falha ao analisar o PDF.", true);

      S.W.guide = res.guide || [];                          // guarda dicas/tutoriais p/ salvar
      applyPdfOrder(res.ordered_ids || []);                 // ordem do guia; re-renderiza

      const matched = res.matched_by_name != null ? res.matched_by_name : res.found;
      const unplaced = (res.total || 0) - (res.found || 0);
      const tips = (res.guide || []).length;
      if (res.found === 0)
        toast("Nenhuma conquista do jogo foi reconhecida no guia. Confira se o PDF é deste jogo.", true);
      else
        toast(`${matched}/${res.total} conquistas reconhecidas · ${tips} seções de dicas${unplaced ? ` · ${unplaced} no pool` : ""}.`);
    } catch (e) {
      toast("Erro ao analisar o PDF: " + e, true);
    } finally {
      const b = $("#pdf-order");
      if (b) { b.disabled = false; b.textContent = "📄 Ordenar pelo PDF do guia"; }
    }
  });
}

/* Importa SÓ as dicas/tutoriais do PDF (não reordena/altera conquistas) — no
   wizard, guarda em S.W.guide para salvar junto. */
function tipsOnlyPdf() {
  if (S.mode === "demo") { toast("Disponível só no app real.", true); return; }
  pickPdf(async (b64, name) => {
    const btn = $("#pdf-tips");
    if (btn) { btn.disabled = true; btn.textContent = "📖 Lendo…"; }
    try {
      const res = await backend.extractGuidePdf(b64, name);
      if (!res || !res.ok) return toast((res && res.error) || "Falha ao ler as dicas.", true);
      S.W.guide = res.guide || [];
      toast(`${(res.guide || []).length} seções de dicas capturadas (conquistas não alteradas).`);
    } catch (e) {
      toast("Erro ao ler o PDF: " + e, true);
    } finally {
      const b = $("#pdf-tips");
      if (b) { b.disabled = false; b.textContent = "📖 Só as dicas (não reordena)"; }
    }
  });
}

/* Anexa as dicas de um PDF a um jogo JÁ SALVO (dashboard), sem tocar nos
   troféus. */
function attachGuide() {
  if (S.mode === "demo") { toast("Disponível só no app real.", true); return; }
  const slug = S.activeSlug;
  if (!slug) return;
  pickPdf(async (b64, name) => {
    toast("Lendo PDF…");
    const res = await backend.attachGuidePdf(slug, b64, name);
    if (!res || !res.ok) return toast((res && res.error) || "Falha ao importar as dicas.", true);
    toast(`${res.sections} seções de dicas importadas (troféus intactos).`);
    await renderDashboard({ force: true });
  });
}

/* Lê um File como base64 (sem o prefixo data:…;base64,). */
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(r.error || new Error("falha ao ler arquivo"));
    r.readAsDataURL(file);
  });
}

/* Aplica a ordem vinda do PDF SEM perder conquistas: as casadas vêm primeiro,
   na ordem do guia, e as demais seguem logo abaixo na ordem atual
   (RetroAchievements). */
function applyPdfOrder(orderedIds) {
  const W = S.W;
  const matched = orderedIds.filter((id) => W.items[id]);
  const seen = new Set(matched);
  const rest = W.order.filter((id) => !seen.has(id));   // mantém o restante (ordem RA)
  W.steps = [{ area: "Ordem do guia (PDF)", ids: matched.concat(rest) }];
  renderWizard2();
}

async function saveWizard() {
  const W = S.W;
  const walkthrough = W.steps
    .map((st, i) => ({
      step: i + 1,
      area: st.area || `Etapa ${i + 1}`,
      achievements: st.ids.map((id) => ({ id })),
    }))
    .filter((st) => st.achievements.length > 0);

  if (!walkthrough.length) return toast("Adicione conquistas a pelo menos uma etapa.", true);

  try {
    const res = await backend.saveGame({ walkthrough, guide: W.guide || [] });
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
  $("#btn-compact")?.addEventListener("click", async (e) => {
    S.compact = !S.compact;
    e.currentTarget.classList.toggle("active", S.compact);
    e.currentTarget.title = S.compact ? "Sair do modo compacto" : "Modo compacto (overlay de progresso)";
    if (hasBackend()) window.pywebview.api.set_compact(S.compact);
    if (S.view === "dashboard") await renderDashboard();
  });
  $("#btn-exit-demo")?.addEventListener("click", exitDemo);
  $("#btn-settings")?.addEventListener("click", () => {
    if (S.view === "settings") return enterDashboard();
    enterSettings();
  });
  bindAtalhos();
}

/* ─────────────────────────  ATALHOS DE TECLADO  ─────────────────────────
   O rodapé anuncia essas teclas, então elas precisam existir. Também é a
   navegação por teclado que o app não tinha. */
const ABAS = ["walk", "mastery", "tips"];

function bindAtalhos() {
  document.addEventListener("keydown", async (e) => {
    // não sequestra digitação em campos de texto
    const alvo = e.target;
    if (alvo && (alvo.tagName === "INPUT" || alvo.tagName === "TEXTAREA" || alvo.isContentEditable)) {
      if (e.key === "Escape") alvo.blur();
      return;
    }
    if (e.ctrlKey || e.altKey || e.metaKey) return;

    if (e.key === "Escape") {
      e.preventDefault();
      if ($("#gf-modal")) return closeGameFaqs();
      if (S.view !== "dashboard") return enterDashboard();
      if (S.compact) return toggleCompacto();
      return;
    }

    if (e.key === "c" || e.key === "C") { e.preventDefault(); return toggleCompacto(); }

    if (S.view !== "dashboard") return;

    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      return trocarJogo(e.key === "ArrowDown" ? 1 : -1);
    }
    if (e.key === "Tab") {
      e.preventDefault();
      const i = ABAS.indexOf(S.tab);
      S.tab = ABAS[(i + (e.shiftKey ? -1 : 1) + ABAS.length) % ABAS.length];
      return renderDashboard({ force: true });
    }
  });
}

async function trocarJogo(dir) {
  if (!S.library.length) return;
  const i = S.library.findIndex((g) => g.slug === S.activeSlug);
  S.activeSlug = S.library[(i + dir + S.library.length) % S.library.length].slug;
  await renderDashboard({ force: true });
}

async function toggleCompacto() {
  const btn = $("#btn-compact");
  S.compact = !S.compact;
  btn?.classList.toggle("active", S.compact);
  if (hasBackend()) window.pywebview.api.set_compact(S.compact);
  if (S.view === "dashboard") await renderDashboard({ force: true });
}

/* Sai do modo demonstração: volta para a tela de conexão (ou, se já houver
   credenciais salvas, direto para o dashboard com dados reais). */
async function exitDemo() {
  if (!hasBackend()) { toast("Backend indisponível — reinicie o app.", true); return; }
  stopPolling();
  S.mode = "real";
  S.activeSlug = null;
  document.getElementById("demo-banner").classList.add("hidden");
  try {
    const st = await backend.appState();
    if (!st.configured) renderSetup();
    else enterDashboard();
  } catch (e) {
    renderSetup();
  }
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
    if (st.auto_import !== undefined) S.autoImport = st.auto_import;
    if (st.auto_overlay !== undefined) S.autoOverlay = st.auto_overlay;
    if (st.ai_ready !== undefined) S.aiReady = st.ai_ready;
    if (st.overlay_exit_fullscreen !== undefined) S.overlayExitFullscreen = st.overlay_exit_fullscreen;
    if (st.overlay_second_screen !== undefined) S.overlaySecondScreen = st.overlay_second_screen;
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
  return DEMO.map((g) => ({
    slug: g.slug, title: g.title, platform: g.platform, accent: g.accent,
    modes: g.modes, mastery: g.mastery, icon: g.icon || "",
  }));
}
function DEMO_GAME(slug) { return DEMO.find((g) => g.slug === slug) || null; }
function DEMO_PLAYED() {
  return [
    { id: 1, title: "Digimon World", console: "PlayStation", total: 219, earned: 219, hardcore: 0, softcore_only: 219, award: "completed", imported: false },
    { id: 2, title: "Digimon World 2003", console: "PlayStation", total: 137, earned: 137, hardcore: 0, softcore_only: 137, award: "completed", imported: false },
    { id: 3, title: "Digimon World 4", console: "GameCube", total: 108, earned: 55, hardcore: 55, softcore_only: 0, award: "beaten-hardcore", imported: true },
    { id: 4, title: "Digimon: Digital Card Battle", console: "PlayStation", total: 115, earned: 68, hardcore: 0, softcore_only: 68, award: "beaten-softcore", imported: false },
  ];
}
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
      { id: 101, title: "First Partner", desc: "Recrute seu primeiro parceiro.", badge_url: "" },
      { id: 102, title: "Jogress Evolution", desc: "Realize uma DNA digivolution.", badge_url: "" },
      { id: 103, title: "Kaiser's Fall", desc: "Derrote o chefe final.", badge_url: "" },
      { id: 104, title: "Perfect Survivors", desc: "Termine sem perder nenhum aliado.", badge_url: "" },
      { id: 105, title: "Card Master", desc: "Colete todas as cartas.", badge_url: "" },
    ],
  };
}

const DEMO = [
  {
    slug: "digimon_world_4", title: "Digimon World 4", platform: "GameCube", accent: "#D62839",
    modes: { hardcore: { total: 6, earned: 1 }, softcore: { total: 6, earned: 2 } },
    mastery: { total: 6, hardcore: 1, earned: 3, softcore_only: 2, remaining: 5, percent: 17, complete: false, softcore_ids: [2, 4] },
    next_ids: [3, 6, 8],
    last_earned: { name: "Ferryman's Mercy", desc: "Rescue all 10 Digi-Elves at Numenume River.", date: "04/03/2026 · 22:08" },
    achievements: [
      { id: 1, name: "Extravagant Petals", desc: "Complete Humid Cave on Normal difficulty.", mode: "hardcore", earned: true, hardcore: true, date: "28/02/2026 · 20:11", badge_url: "", step: 1, area: "Death Valley - Humid Cave" },
      { id: 2, name: "Tusks of Ash", desc: "Defeat Mammothmon in Cliff Dungeon.", mode: "softcore", earned: true, hardcore: false, date: "01/03/2026 · 18:40", badge_url: "", step: 1, area: "Death Valley - Humid Cave" },
      { id: 3, name: "Two Keys, One Fortress", desc: "Unlock the Goburimon Fortress with both IDs.", mode: "softcore", earned: false, hardcore: false, date: "", badge_url: "", step: 2, area: "Goburimon Fortress" },
      { id: 4, name: "Ferryman's Mercy", desc: "Rescue all 10 Digi-Elves at Numenume River.", mode: "softcore", earned: true, hardcore: false, date: "04/03/2026 · 22:08", badge_url: "", step: 2, area: "Goburimon Fortress" },
      { id: 6, name: "Death Valley, Twice Over", desc: "Complete Death Valley on Hard difficulty.", mode: "softcore", earned: false, hardcore: false, date: "", badge_url: "", step: 3, area: "Death Valley (Hard)" },
      { id: 8, name: "302 and Counting", desc: "Clear Undead Yard on Hard mode.", mode: "softcore", earned: false, hardcore: false, date: "", badge_url: "", step: 3, area: "Death Valley (Hard)" },
    ],
    guide: [
      { num: "1", title: "INTRODUÇÃO & MECÂNICAS BÁSICAS", blocks: [
        { type: "p", text: "Action-RPG da Bandai (GameCube/PS2, 2005). Suporta até 4 jogadores e tem sistema de evolução, armas e técnicas de MP." },
        { type: "li", text: "Toque em inimigos causa dano — evite contato desnecessário." },
        { type: "li", text: "Usar sempre o mesmo tipo de arma aumenta a skill naquele tipo." },
        { type: "note", text: "Nunca saia de um dungeon sem salvar! O Save Keeper fica apenas no HomeServer." },
      ]},
      { num: "4", title: "WALKTHROUGH — SEQUÊNCIA TEMPORAL", blocks: [
        { type: "subhead", text: "4.1 Death Valley" },
        { type: "step", n: 3, text: "Goblin Pass" },
        { type: "label", label: "Objetivo", text: "Atravesse as pontes; na 2ª, vire à esquerda e siga ao sul." },
        { type: "label", label: "Dica", text: "Entre e saia repetidamente para respawnar itens e ganhar EXP." },
        { type: "boss", text: "BLOSSOMOM" },
        { type: "p", text: "Use arma de ataque a distância (Shot). Mantenha distância máxima e atire de longe." },
      ]},
      { num: "9", title: "DICAS AVANÇADAS & GRINDING", blocks: [
        { type: "li", text: "Goblin Pass: entre e saia para farmar inimigos e itens." },
        { type: "note", text: "Algumas conquistas do RetroAchievements são incompatíveis com multiplayer — jogue solo." },
      ]},
    ],
  },
  {
    slug: "digimon_world_2", title: "Digimon World 2", platform: "PlayStation", accent: "#F5C518",
    modes: { hardcore: { total: 2, earned: 2 }, softcore: { total: 2, earned: 0 } },
    mastery: { total: 2, hardcore: 2, earned: 2, softcore_only: 0, remaining: 0, percent: 100, complete: true, softcore_ids: [] },
    next_ids: [],
    last_earned: { name: "Master Tamer", desc: "Complete every battle on the final set.", date: "12/02/2026 · 19:41" },
    achievements: [
      { id: 1, name: "Digivolution Archivist", desc: "Record all DNA digivolution chains.", mode: "hardcore", earned: true, hardcore: true, date: "10/02/2026 · 21:02", badge_url: "", step: 1, area: "Campanha" },
      { id: 2, name: "Master Tamer", desc: "Complete every battle on the final set.", mode: "hardcore", earned: true, hardcore: true, date: "12/02/2026 · 19:41", badge_url: "", step: 2, area: "Pós-jogo" },
    ],
  },
  {
    slug: "digimon_digital_card_battle", title: "Digimon Digital Card Battle", platform: "PlayStation", accent: "#2DE2E6",
    modes: { hardcore: { total: 3, earned: 0 }, softcore: { total: 3, earned: 1 } },
    mastery: { total: 3, hardcore: 0, earned: 1, softcore_only: 1, remaining: 3, percent: 0, complete: false, softcore_ids: [1] },
    next_ids: [2, 3],
    last_earned: { name: "Full Deck", desc: "Collect every card in the Omega set.", date: "28/01/2026 · 21:15" },
    achievements: [
      { id: 1, name: "Full Deck", desc: "Collect every card in the Omega set.", mode: "softcore", earned: true, hardcore: false, date: "28/01/2026 · 21:15", badge_url: "", step: 1, area: "Coleção" },
      { id: 2, name: "Arena Veteran", desc: "Win 20 ranked duels in a row.", mode: "softcore", earned: false, hardcore: false, date: "", badge_url: "", step: 2, area: "Arena" },
      { id: 3, name: "Black Card Hunter", desc: "Obtain all Black-rarity cards.", mode: "softcore", earned: false, hardcore: false, date: "", badge_url: "", step: 2, area: "Arena" },
    ],
  },
];
