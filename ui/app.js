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
  libraryQuery: "",
  activeSlug: null,
  tab: "tips",           // aba do painel: overview | walk | mastery | tips
  onTop: true,
  compact: false,        // modo mini-overlay (progresso de conquistas)
  cwTab: "walk",         // aba do overlay compacto: 'walk' (conquistas) | 'tips'
  compactCfg: null,      // tamanho/contagens do overlay (vêm do backend)
  poll: null,
  sig: null,             // assinatura do último render (evita redesenhar à toa)
  screenKey: null,       // identidade da tela atual (p/ decidir se mantém o scroll)
  W: null,               // estado do wizard
  I: null,               // estado da importação em lote
  autoImport: true,      // espelhar a conta (jogo novo entra sozinho)
  autoOverlay: true,     // grudar no emulador quando ele abrir
  autoCheckUpdates: true,
  version: "0.0.0",
  aiReady: false,        // há chave salva para o provedor de IA escolhido
  aiProviderLabel: "",
  aiModel: "",
  tipsAI: null,          // progresso persistente da tradução/melhoria das dicas
  tipsAIPolling: false,
  guideMode: "compact", // compact | full | source
  guideQuery: "",
  guideFilter: "all",
  smartGuideAuto: true,
  smartGuideConsent: false,
  guideDensity: "comfortable",
  uiScale: 100,
  reducedMotion: false,
  gamepadLoop: null,
  gamepadLast: {},
  smartStatuses: {},
  overlayExitFullscreen: false,  // mandar Alt+Enter ao detectar tela cheia exclusiva
  overlaySecondScreen: false,    // levar o overlay para o monitor livre
  overlayFitEmulator: true,      // dimensionar o overlay conforme a janela do emulador
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
    if (S.mode === "demo") return {
      configured: true, version: "0.8.2", auto_import: true,
      auto_overlay: true, auto_check_updates: true,
      smart_guide_auto: true, smart_guide_consent: false,
      guide_density: "comfortable", ui_scale: 100, reduced_motion: false,
    };
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
  async setAutoCheckUpdates(v) {
    if (S.mode === "demo") return { ok: true, auto_check_updates: v };
    return window.pywebview.api.set_auto_check_updates(v);
  },
  async checkForUpdates(force = false) {
    if (S.mode === "demo") return { ok: true, update_available: false, source_mode: true };
    return window.pywebview.api.check_for_updates(!!force);
  },
  async deferUpdate(hours = 24) {
    if (S.mode === "demo") return { ok: true };
    return window.pywebview.api.defer_update(hours);
  },
  async startUpdateDownload() {
    return window.pywebview.api.start_update_download();
  },
  async updateStatus() {
    if (S.mode === "demo") return { ok: true, phase: "current" };
    return window.pywebview.api.get_update_status();
  },
  async installUpdate() {
    return window.pywebview.api.install_downloaded_update();
  },
  async confirmUpdateBoot() {
    if (S.mode === "demo") return { ok: true };
    return window.pywebview.api.confirm_update_boot();
  },
  async overlayStatus() {
    if (S.mode === "demo") return { ok: true, detected: false, error: "Modo demonstração" };
    return window.pywebview.api.get_overlay_status();
  },
  async testOverlay() {
    if (S.mode === "demo") return { ok: true, detected: false, error: "Modo demonstração" };
    return window.pywebview.api.test_overlay_detection();
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
  async refineGameTips(slug) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.refine_game_tips(slug);
  },
  async translateGameTips(slug) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.translate_game_tips(slug);
  },
  async startGameTipsAI(slug, operation) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.start_game_tips_ai(slug, operation);
  },
  async gameTipsAIStatus() {
    if (S.mode === "demo") return { ok: true, phase: "idle" };
    return window.pywebview.api.get_game_tips_ai_status();
  },
  async smartGuide(slug) {
    if (S.mode === "demo") return { ok: true };
    return window.pywebview.api.get_smart_guide(slug);
  },
  async startSmartGuide(slug, force = false) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.start_smart_guide(slug, !!force);
  },
  async smartGuideStatus(slug) {
    if (S.mode === "demo") return { ok: true, phase: "idle" };
    return window.pywebview.api.get_smart_guide_status(slug);
  },
  async cancelSmartGuide(slug) { return window.pywebview.api.cancel_smart_guide(slug); },
  async updateGuideProgress(slug, action, blockId, value) {
    if (S.mode === "demo") return { ok: true };
    return window.pywebview.api.update_guide_progress(slug, action, blockId || "", value);
  },
  async restoreSmartGuide(slug, revisionId) {
    return window.pywebview.api.restore_smart_guide_revision(slug, revisionId);
  },
  async searchGuideMedia(query, source) {
    return window.pywebview.api.search_guide_media(query, source || "openverse");
  },
  async approveGuideMedia(slug, candidate, confirmed = false) {
    return window.pywebview.api.approve_guide_media(slug, candidate, !!confirmed);
  },
  async addGuideMedia(slug, data, filename, title = "") {
    return window.pywebview.api.add_guide_media(slug, data, filename, title);
  },
  async createGuideDiagram(slug, spec) {
    return window.pywebview.api.create_guide_diagram(slug, spec);
  },
  async broadMediaSearch(query) { return window.pywebview.api.open_broad_media_search(query); },
  async setExperience(cfg) {
    if (S.mode === "demo") return { ok: true, ...cfg };
    return window.pywebview.api.set_experience_preferences(
      cfg.smart_auto ?? null, cfg.consent ?? null, cfg.density || "",
      cfg.ui_scale ?? null, cfg.reduced_motion ?? null);
  },
  async setSettingsSession(section, payload) {
    if (S.mode === "demo") return { ok: true, section, ...payload };
    return window.pywebview.api.set_settings_session(section, payload || {});
  },
  async exportGuidePack(slug, includeProgress = true) {
    return window.pywebview.api.export_guide_pack(slug, !!includeProgress);
  },
  async importGuidePack(slug, data) { return window.pywebview.api.import_guide_pack(slug, data); },
  async getAiConfig() {
    if (S.mode === "demo") return { ok: false };
    return window.pywebview.api.get_ai_config();
  },
  async setAiConfig(cfg) {
    if (S.mode === "demo") return { ok: false };
    return window.pywebview.api.set_ai_config(cfg.provider, cfg.api_key, cfg.model, cfg.base_url);
  },
  async getSourcesConfig() {
    if (S.mode === "demo") return { ok: true, ready: {} };
    return window.pywebview.api.get_sources_config();
  },
  async setSourceKey(source, key1, key2) {
    if (S.mode === "demo") return { ok: true, ready: {} };
    return window.pywebview.api.set_source_key(source, key1 ?? null, key2 ?? null);
  },
  async coversSearch(slug, query, source) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.covers_search(slug, query || "", source || "steamgriddb");
  },
  async coversFor(gameId, source) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.covers_for(gameId, source || "steamgriddb");
  },
  async setGameCover(slug, url, role) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.set_game_cover(slug, url, role || "cover");
  },
  async refreshGameArt(slug, force = false) {
    if (S.mode === "demo") return { ok: true, status: "ready", art: DEMO_GAME(slug)?.art || {} };
    return window.pywebview.api.refresh_game_art(slug, force);
  },
  async gameArtStatus(slug) {
    if (S.mode === "demo") return { ok: true, status: "ready" };
    return window.pywebview.api.get_art_enrichment_status(slug);
  },
  async clearGameCover(slug, role) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.clear_game_cover(slug, role || "cover");
  },
  async getCompactConfig() {
    if (S.mode === "demo") return { ok: true, width: 300, height: 232, last: 2, next: 0 };
    return window.pywebview.api.get_compact_config();
  },
  async setCompactConfig(cfg) {
    if (S.mode === "demo") return { ok: true, ...cfg };
    return window.pywebview.api.set_compact_config(
      cfg.width, cfg.height, cfg.last, cfg.next);
  },
  moveWindow(x, y) {
    if (S.mode === "demo" || !hasBackend()) return;
    try { window.pywebview.api.move_window(x, y); } catch (_) { /* janela indo embora */ }
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
          <label for="in-user">Username</label>
          <input id="in-user" autocomplete="off" spellcheck="false" placeholder="seu_usuario_RA" />
        </div>
        <div class="field">
          <label for="in-key">Web API Key</label>
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
  S.compactCfg = await backend.getCompactConfig().catch(() => null);
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
  $("#btn-library").hidden = !!S.compact;
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
  const { t, e } = totals(game);
  const mst = game.mastery || {};
  const cor = corDe(game);
  const tab = S.cwTab || "walk";
  // A arte escolhida (capa) vira o fundo full-bleed do overlay, transparente.
  const capa = game.art?.cover || game.art?.box;

  const conteudo = tab === "tips" ? cwTipsHTML(game) : cwWalkHTML(game, t, e, cor);

  return `<div class="cw" style="--jogo:${cor}">
    ${capa ? `<div class="cw-art" style="background-image:url('${esc(capa)}')"></div>` : ""}
    <div class="cw-scrim"></div>
    <button class="cw-nav prev" id="cw-prev" title="Jogo anterior">‹</button>
    <button class="cw-nav next" id="cw-next" title="Próximo jogo">›</button>
    <div class="cw-head" title="${esc(game.title)} — arraste para mover">
      <div class="cw-tabs">
        <button class="cw-tab ${tab === "walk" ? "on" : ""}" data-cwtab="walk">Conquistas</button>
        <button class="cw-tab ${tab === "tips" ? "on" : ""}" data-cwtab="tips">Dicas</button>
      </div>
      <span class="cw-badges">
        <span class="cw-count" title="Em hardcore, o que vale Mastery">${mst.hardcore || 0}<small>/${t}</small></span>
        <span class="cw-pct">${mst.percent || 0}%</span>
      </span>
    </div>
    <div class="cw-content" data-scroll="compact">${conteudo}</div>
  </div>`;
}

/* Aba Conquistas do overlay: barra de progresso + N últimas obtidas (fixo, do
   ajuste) + as próximas que couberem na altura atual. */
function cwWalkHTML(game, t, e, cor) {
  const mst = game.mastery || {};
  const cfg = S.compactCfg || { height: 232, last: 2, next: 0 };
  const lastN = Math.max(0, cfg.last ?? 2);

  const earned = (game.achievements || []).filter((a) => a.earned && a.date_raw);
  earned.sort((a, b) => (a.date_raw < b.date_raw ? 1 : a.date_raw > b.date_raw ? -1 : 0));
  const ultimas = earned.slice(0, lastN);

  const proxIds = game.next_ids || [];
  let nextN = Math.max(0, cfg.next ?? 0);
  if (nextN === 0) {
    // auto: estima quantas linhas cabem na ALTURA REAL da janela (vale tanto
    // para o tamanho manual quanto para o auto-ajuste ao emulador).
    const H = window.innerHeight || cfg.height || 232;
    const ROW = 40, LABEL = 18, HEAD = 36, BAR = 16, PAD = 14;
    const usadoUlt = ultimas.length ? LABEL + ultimas.length * ROW : 0;
    const restante = H - HEAD - BAR - usadoUlt - LABEL - PAD;
    nextN = Math.max(1, Math.floor(restante / ROW));
  }
  const proximas = proxIds.slice(0, nextN)
    .map((id) => (game.achievements || []).find((a) => a.id === id))
    .filter(Boolean);

  const secao = (label, itens, locked) => itens.length ? `<div class="cw-sec">
    <p class="cw-sec-label">${label}</p>
    ${itens.map((a) => cwRow(a, locked)).join("")}
  </div>` : "";

  const vazio = !ultimas.length && !proximas.length
    ? (t > 0 && e >= t
        ? `<div class="cw-done">✓ 100% concluído</div>`
        : `<p class="cw-empty-sm">Sem conquistas registradas ainda.</p>`)
    : "";

  return `<div class="cw-bar"><div class="cw-fill" style="width:${mst.percent || 0}%;background:${cor}"></div></div>
    ${secao(lastN === 1 ? "Última obtida" : `Últimas ${ultimas.length} obtidas`, ultimas, false)}
    ${secao("Próximas", proximas, true)}
    ${vazio}`;
}

function cwRow(a, locked) {
  const badge = a.badge_url
    ? `<div class="cw-badge${locked && !a.earned ? " locked" : ""}" style="background-image:url('${esc(a.badge_url)}')"></div>`
    : `<div class="cw-badge${locked && !a.earned ? " locked" : ""}">${locked && !a.earned ? "🔒" : "🏆"}</div>`;
  return `<div class="cw-row">
    ${badge}
    <div class="cw-info">
      <p class="cw-name">${esc(a.name)}</p>
      <p class="cw-desc">${esc(a.desc || "")}</p>
    </div>
  </div>`;
}

/* Aba Dicas do overlay: as seções do guia importado, roláveis. */
function cwTipsHTML(game) {
  const smart = game.smart_guide || {};
  const next = smart.next_objective || {};
  if (next.block_id) {
    const current = smart.current || {};
    const chapter = (current.chapters || []).find((c) => c.id === next.chapter_id) || {};
    const block = (chapter.blocks || []).find((b) => b.id === next.block_id) || {};
    const warning = (chapter.blocks || []).find((b) => ["warning", "missable"].includes(b.type) && !(smart.effective_progress?.completed || []).includes(b.id));
    const previous = (smart.progress?.history || []).slice(-1)[0]?.block_id || "";
    return `<div class="cw-smart"><p class="cw-sec-label">PRÓXIMO OBJETIVO</p><h3>${esc(next.title)}</h3><p>${esc(next.text)}</p>
      ${(block.items || []).slice(0, 4).map((item) => `<div class="cw-check">□ ${esc(item.text)}</div>`).join("")}
      ${warning ? `<div class="cw-warning">! ${esc(warning.title || warning.text)}</div>` : ""}
      <div class="cw-objective-actions">${previous ? `<button data-cw-undo="${esc(previous)}">← Voltar</button>` : ""}<button class="cw-complete" data-cw-complete="${esc(next.block_id)}">Concluir e avançar</button></div></div>`;
  }
  const secs = game.guide || [];
  if (!secs.length) {
    return `<p class="cw-empty-sm">Sem dicas importadas para este jogo.<br>Importe um guia na tela completa.</p>`;
  }
  return `<div class="cw-tips">${secs.map((s) => `
    <p class="cw-tip-h">${esc(s.title || "")}</p>
    ${(s.blocks || []).map((b) =>
      `<p class="cw-tip-${b.type === "subhead" ? "sub" : "p"}">${esc(b.text || "")}</p>`).join("")}
  `).join("")}</div>`;
}

function bindCompact() {
  $("#cw-prev")?.addEventListener("click", () => switchCompactGame(-1));
  $("#cw-next")?.addEventListener("click", () => switchCompactGame(1));
  makeDraggable(root.querySelector(".cw-head"));
  root.querySelectorAll("[data-cwtab]").forEach((b) =>
    b.addEventListener("click", () => { S.cwTab = b.dataset.cwtab; renderDashboard({ force: true }); }));
  $("[data-cw-complete]")?.addEventListener("click", async (e) => {
    await atualizarGuia("complete", e.currentTarget.dataset.cwComplete, true);
  });
  $("[data-cw-undo]")?.addEventListener("click", async (e) => {
    await atualizarGuia("complete", e.currentTarget.dataset.cwUndo, false);
  });
}

function switchCompactGame(dir) {
  if (!S.library.length) return;
  const idx = S.library.findIndex((g) => g.slug === S.activeSlug);
  const next = (idx + dir + S.library.length) % S.library.length;
  S.activeSlug = S.library[next].slug;
  renderDashboard();
}

/* Arraste manual da janela por um elemento (a faixa do overlay). Substitui o
   drag-region nativo do pywebview, que só funciona no Linux — no Windows ele
   chama window.move na thread do bridge, que não surte efeito. Aqui a posição
   nova (canto = cursor na tela menos o ponto agarrado) vai pelo move_window, que
   passa pelo _window_op e funciona nos dois SOs. rAF coalesce os mousemove. */
function makeDraggable(el) {
  if (!el) return;
  let grabX = 0, grabY = 0, raf = 0, pend = null;
  const flush = () => { raf = 0; if (pend) { backend.moveWindow(pend[0], pend[1]); pend = null; } };
  const onMove = (e) => {
    pend = [Math.round(e.screenX - grabX), Math.round(e.screenY - grabY)];
    if (!raf) raf = requestAnimationFrame(flush);
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  el.addEventListener("mousedown", (e) => {
    // botão esquerdo, e nunca sobre um controle (abas): esses clicam, não arrastam
    if (e.button !== 0 || e.target.closest("button")) return;
    grabX = e.clientX; grabY = e.clientY;
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    e.preventDefault();
  });
}

/* Liga/desliga o modo compacto (usado pelo botão da barra E pelo controle que
   aparece no hover do próprio overlay, já que a barra some no compacto). */
async function toggleCompact(value) {
  S.compact = value !== undefined ? !!value : !S.compact;
  const btn = document.getElementById("btn-compact");
  if (btn) {
    btn.classList.toggle("active", S.compact);
    btn.title = S.compact ? "Sair do modo compacto" : "Modo compacto (overlay de progresso)";
  }
  if (hasBackend()) window.pywebview.api.set_compact(S.compact);
  if (S.view === "dashboard") await renderDashboard({ force: true });
}

function sidebarHTML() {
  const query = S.libraryQuery.trim().toLocaleLowerCase("pt-BR");
  const visible = query
    ? S.library.filter((g) => g.title.toLocaleLowerCase("pt-BR").includes(query))
    : S.library;
  const done = visible.filter(isMastered);
  const prog = visible.filter((g) => !isMastered(g));
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
    return `<button class="tile ${active ? "active" : ""}" data-slug="${esc(g.slug)}"
      data-title="${esc(g.title.toLowerCase())}" style="--jogo:${cor}"
      title="${esc(g.title)}" aria-label="Abrir ${esc(g.title)}">
      <div class="cover">
        ${capa}
        ${fita ? `<div class="ribbon ${fita.cls}">${fita.txt}</div>` : ""}
      </div>
      <div class="meta">
        <div class="name">${esc(g.title)}</div>
        <div class="tile-bar"><i style="width:${m.percent || 0}%"></i></div>
        <div class="tile-num">${m.hardcore || 0} / ${m.total || 0} · ${m.percent || 0}%</div>
      </div>
      <span class="tile-star" aria-hidden="true">${isMastered(g) ? "★" : "☆"}</span>
    </button>`;
  };
  return `<button class="library-scrim" id="library-scrim" aria-label="Fechar biblioteca"></button>
  <aside class="sidebar" aria-label="Biblioteca de jogos">
    <div class="console-brand pywebview-drag-region">
      <span>DigiTracker</span>
      <button class="console-search-button" type="button" title="Buscar na biblioteca" aria-label="Buscar na biblioteca">⌕</button>
    </div>
    <div class="sidebar-head">
      <div class="sidebar-title-row"><h2>BIBLIOTECA</h2><span class="library-chevron">⌄</span></div>
      <label class="library-search" title="Buscar na biblioteca">
        <span aria-hidden="true">⌕</span>
        <input id="library-search" type="search" placeholder="Buscar jogo" value="${esc(S.libraryQuery)}" aria-label="Buscar jogo na biblioteca">
      </label>
    </div>
    <div class="sidebar-list" data-scroll="sidebar">
      ${done.length ? `<p class="section-label done">★ MASTERY</p>${done.map(tile).join("")}<div style="height:8px"></div>` : ""}
      ${prog.length ? `<p class="section-label progress">◎ EM PROGRESSO</p>${prog.map(tile).join("")}` : ""}
      <p class="library-empty ${visible.length ? "hidden" : ""}" id="library-filter-empty">${query ? "Nenhum jogo encontrado." : "Nenhum jogo ainda."}</p>
    </div>
    <div class="sidebar-foot">
      <button class="add-btn" id="btn-add"><span class="foot-icon">＋</span><span class="foot-label">Adicionar jogo</span></button>
      <button class="import-btn" id="btn-import-all" title="Trazer de uma vez todos os jogos em que você já tem conquistas"><span class="foot-icon">⤓</span><span class="foot-label">Importar meus jogos</span></button>
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
  const smart = game.smart_guide || {};
  const next = smart.next_objective || {};
  const smartDoc = smart.current || {};
  const smartProgress = smart.effective_progress || smart.progress || {};
  const smartBlocks = (smartDoc.chapters || []).flatMap((c) => c.blocks || []);
  const completedSmart = new Set(smartProgress.completed || []);
  const smartDone = completedSmart.size;
  const pendingBlocks = smartBlocks.filter((b) => !completedSmart.has(b.id));
  const nextSteps = pendingBlocks.filter((b) => ["objective", "checklist", "checkpoint", "challenge"].includes(b.type)).slice(0, 3);
  const pendingMissables = pendingBlocks.filter((b) => b.type === "missable");
  const missables = pendingMissables.length;
  const firstMissable = pendingMissables[0] || {};
  const personalNotes = Object.keys(smartProgress.notes || {}).length;
  const sessionMinutes = smartProgress.session_minutes || 30;

  const cor = corDe(game);
  const arte = game.art || {};
  // Fundo da lista: o wallpaper escolhido (art.background) tem prioridade;
  // depois a capa; e por fim o screenshot in-game, como antes. Uma imagem
  // escolhida ganha a classe .escolhida (mais visível), o screenshot fica sutil.
  const fundo = arte.background || arte.title || arte.ingame || arte.cover || arte.box;
  const escolhido = arte.background || arte.cover;
  const heroArt = arte.background || arte.title || arte.ingame || arte.cover || arte.box;
  return `<main class="main" data-scroll="main" style="--jogo:${cor}">
    ${fundo ? `<div class="game-bg ${escolhido ? "escolhida" : ""}" style="background-image:url('${esc(fundo)}')"></div>` : ""}
    <div class="game-glow"></div>
    <div class="panel-head">
      <div class="hero">
        ${heroArt ? `<div class="hero-art" style="background-image:url('${esc(heroArt)}')"></div>` : ""}
        <div class="hero-scrim"></div>
        <button class="cover-btn" id="btn-cover" title="Escolher capa ou fundo">▧ Trocar arte</button>
        <div class="hero-txt">
          <div class="hero-identity">
            <div class="head-info">
              <h1>${esc(game.title)}</h1>
              <p class="plat"><span>▷</span>${esc(game.genre || "Aventura")} <i>•</i> ${esc(game.platform)} ${game.year ? `<i>•</i> ${esc(game.year)}` : ""} <i>•</i> ${game.players ? esc(game.players) : "1 jogador"}</p>
            </div>
          </div>
          <div class="hero-insights">
            <div class="hero-progress-card">
              <span>PROGRESSO</span>
              <div><strong>${mst.percent || pct || 0}%</strong><small>${mst.hardcore || 0}/${t}</small></div>
              <div class="hero-bar" title="Progresso de Mastery: ${mst.hardcore || 0} de ${t} em hardcore">
                <i style="width:${mst.percent || 0}%"></i>
              </div>
            </div>
            ${le ? `<div class="hero-achievement-card">
              <span class="spark">✦</span>
              <div><small>CONQUISTA RECENTE</small><strong>${esc(le.name)}</strong><p>${esc(le.desc)}</p></div>
            </div>` : `<div class="hero-achievement-card empty"><span class="spark">◇</span><div><small>PRÓXIMA CONQUISTA</small><strong>Continue sua jornada</strong><p>O progresso aparecerá aqui.</p></div></div>`}
          </div>
          <button class="hero-continue" data-jump-guide="${esc(next.block_id || "")}"><span>Continuar</span><b>→</b></button>
        </div>
      </div>
    </div>
    <div class="panel-tabs">
      <span class="tab-bumper">LB</span>
      <button class="ptab ${S.tab === "overview" ? "active" : ""}" data-tab="overview"><b>▦</b> Visão geral</button>
      <button class="ptab ${S.tab === "walk" ? "active" : ""}" data-tab="walk"><b>⚑</b> Walkthrough</button>
      <button class="ptab ${S.tab === "mastery" ? "active" : ""}" data-tab="mastery"><b>♜</b> Conquistas${mst.softcore_only ? `<span class="count">${mst.softcore_only}</span>` : ""}</button>
      <button class="ptab ${S.tab === "tips" ? "active" : ""}" data-tab="tips"><b>✦</b> Guia Inteligente${(smartDoc.chapters || []).length ? `<span class="count">${smartDoc.chapters.length}</span>` : tips ? `<span class="count">${tips}</span>` : ""}</button>
      <span class="tab-bumper">RB</span>
    </div>
    ${S.tab === "tips" ? `<div class="guide-commandbar dashboard-commandbar">
      <label class="guide-search"><span>⌕</span><input id="guide-search" value="${esc(S.guideQuery)}" placeholder="Buscar no guia"></label>
      <div class="segmented" role="tablist">${[["compact","Compacto"],["full","Completo"],["source","Fonte"]].map(([id,label]) => `<button class="${S.guideMode === id ? "on" : ""}" data-guide-mode="${id}">${label}</button>`).join("")}</div>
      <select id="guide-filter" aria-label="Filtrar guia"><option value="all">Tudo</option><option value="pending">Pendentes</option><option value="missable">Perdíveis</option><option value="warning">Avisos</option><option value="favorites">Favoritos</option><option value="achievement">Conquistas</option></select>
      <select id="guide-session" aria-label="Tempo da sessão">${[15,30,45,60,90,120].map((m) => `<option value="${m}" ${Number(sessionMinutes) === m ? "selected" : ""}>${m} min</option>`).join("")}</select>
    </div>` : ""}
    ${["tips", "overview"].includes(S.tab) ? `<section class="activity-deck hybrid-dashboard" aria-label="Continuar jogando">
      <div class="activity-main-grid">
        <button class="activity-card primary" data-jump-guide="${esc(next.block_id || "")}">
          <span class="activity-kicker">CONTINUAR DE ONDE PAREI</span>
          <div class="activity-title-row"><span class="activity-icon">◎</span><div><small>OBJETIVO ATUAL</small><strong>${esc(next.title || (smartDoc.chapters?.length ? "Escolha o próximo objetivo" : "Importe um guia para começar"))}</strong></div></div>
          <span>${esc(next.text || next.chapter || "O DigiTracker reúne sua próxima ação aqui.")}</span>
          <i>Ver detalhes do objetivo <b>→</b></i>
        </button>
        <div class="activity-card session">
          <span class="activity-kicker">◷ SESSÃO DE ${sessionMinutes} MIN</span>
          <strong>${sessionMinutes} min</strong><span>Sessão planejada</span>
          <i><small>FOCO DA SESSÃO</small>${esc(next.chapter || next.title || "avançar no guia")}</i>
        </div>
        <div class="activity-card steps">
          <span class="activity-kicker">PRÓXIMOS PASSOS</span>
          <ul>${nextSteps.length ? nextSteps.map((b, i) => `<li class="${i ? "" : "done"}">${esc(b.title || b.text || `Passo ${i + 1}`)}</li>`).join("") : `<li>Organize o guia para receber os próximos passos.</li>`}</ul>
          <i>${smartDone} concluídos <b>→</b></i>
        </div>
        <div class="guide-progress-strip">
          <span class="progress-trophy">♜</span><div><small>PROGRESSO DE CONQUISTAS</small><strong>${e}/${t} (${pct}%)</strong></div>
          <div class="guide-progress-bar"><i style="width:${pct}%"></i></div><span>${Math.max(0, t - e)} restantes</span><b>→</b>
        </div>
      </div>
      <aside class="activity-side-grid">
        <div class="activity-card missable ${missables ? "warn" : "clear"}">
          <span class="activity-kicker">△ PERDÍVEL</span>
          <div><strong>${missables}</strong><span>${missables === 1 ? " perdível nesta seção" : " perdíveis pendentes"}</span></div>
          <p>${esc(firstMissable.title || firstMissable.text || "Nenhum alerta crítico para o próximo objetivo.")}</p>
          <i>Ver detalhes <b>→</b></i>
        </div>
        <button class="activity-card personal-notes" data-jump-guide="${esc(next.block_id || "")}">
          <span class="activity-kicker">✎ MINHAS ANOTAÇÕES</span>
          <p>${personalNotes ? `${personalNotes} ${personalNotes === 1 ? "anotação pessoal salva" : "anotações pessoais salvas"}.` : "Adicione suas anotações pessoais sobre estratégias, itens ou lembretes aqui."}</p>
          <i><b>→</b></i>
        </button>
      </aside>
    </section>` : ""}
    ${S.tab === "tips" ? guideHTML(game) : S.tab === "mastery" ? masteryHTML(game) : S.tab === "overview" ? guideHTML(game) : walkHTML(game)}
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
  const bundle = game.smart_guide || {};
  const doc = bundle.current || {};
  const progress = bundle.effective_progress || bundle.progress || {};
  const completed = new Set(progress.completed || []);
  const favorites = new Set(progress.favorites || []);
  const revealed = new Set(progress.revealed_spoilers || []);
  const notes = progress.notes || {};
  const media = bundle.media || [];
  const mediaById = new Map(media.map((m) => [m.id, m]));
  const status = S.smartStatuses[S.activeSlug] || bundle.status || {};
  const mode = S.guideMode || "compact";
  const query = S.guideQuery.trim().toLocaleLowerCase("pt-BR");
  const filter = S.guideFilter || "all";
  const importBtn = S.mode === "demo" ? "" : secs.length ? `
    <details class="guide-more"><summary>Importar/substituir <span>⌄</span></summary>
      <div class="guide-more-menu">
        <button class="guide-menu-btn" id="guide-gamefaqs"><span>◎</span><span><b>GameFAQs</b><small>Buscar guia online</small></span></button>
        <button class="guide-menu-btn" id="guide-import"><span>▤</span><span><b>Arquivo PDF</b><small>Texto e imagens locais</small></span></button>
      </div></details>` : `
    <div class="guide-empty-actions">
      <button class="guide-import-card primary" id="guide-gamefaqs"><span class="guide-import-icon">◎</span><span><b>Importar do GameFAQs</b><small>Busque e escolha um guia online</small></span><span class="guide-arrow">→</span></button>
      <button class="guide-import-card" id="guide-import"><span class="guide-import-icon">▤</span><span><b>Importar arquivo PDF</b><small>Inclui imagens incorporadas</small></span><span class="guide-arrow">→</span></button>
    </div>`;
  if (!secs.length) return `<div class="list-wrap"><div class="guide-empty-state">
    <span class="guide-empty-kicker">GUIA INTELIGENTE</span><h2>Transforme informação em próxima ação</h2>
    <p>Importe qualquer detonado. A fonte permanece intacta e o DigiTracker cria uma versão compacta, pesquisável e reversível.</p>${importBtn}
  </div></div>`;

  const phaseClass = ["error", "cancelled"].includes(status.phase) ? "err"
    : ["success", "ready"].includes(status.phase) ? "ok" : "";
  const statusAction = status.phase === "awaiting_consent"
    ? `<button class="guide-status-action" id="guide-consent">Revisar e ativar</button>`
    : status.phase === "awaiting_configuration"
      ? `<button class="guide-status-action" id="guide-config-ai">Configurar IA</button>`
      : status.phase === "error"
        ? `<button class="guide-status-action" id="guide-retry">Tentar novamente</button>` : "";
  const statusHTML = status.phase && status.phase !== "idle" ? `<div class="guide-ai-status ${phaseClass}" role="status">
    <span class="task-icon">${status.phase === "running" ? "✦" : status.phase === "error" ? "!" : "✓"}</span>
      <div class="task-copy"><b>${status.phase === "running" ? "Organizando o guia" : status.phase === "queued" ? "Guia na fila de organização" : status.phase === "awaiting_consent" ? "Sua confirmação é necessária" : status.phase === "awaiting_configuration" ? "IA ainda não configurada" : status.phase === "error" ? "Não foi possível publicar" : "Guia protegido e versionado"}</b>
      <span>${esc(status.error || status.message || "Fonte original preservada.")}</span></div>
    ${status.total ? `<div class="guide-ai-progress"><span style="width:${Math.round(100 * (status.completed || 0) / status.total)}%"></span></div>` : ""}
    ${statusAction}
  </div>` : "";

  const legacyBlock = (b) => {
    const cls = { boss: "g-boss", step: "g-step", subhead: "g-sub", note: "g-note", label: "g-row", li: "g-li" }[b.type] || "g-p";
    return `<div class="${cls}">${esc(b.text)}</div>`;
  };
  const sourceHTML = `<div class="source-notice"><strong>Fonte original</strong><span>Conteúdo importado sem reescrita. Sempre disponível para conferência.</span></div>
    ${secs.map((s) => `<section class="g-section"><p class="g-title">${esc(s.num)}. ${esc(s.title)}</p>${(s.blocks || []).map(legacyBlock).join("")}</section>`).join("")}`;

  const icon = { objective: "→", checklist: "✓", warning: "!", missable: "◆", achievement: "🏆", challenge: "⚔", table: "▦", comparison: "⇄", image: "▧", route: "↝", graph: "◇", note: "i", spoiler: "◉", resource: "＋", checkpoint: "◷", text: "·" };
  const visibleTypes = new Set(["objective", "checklist", "warning", "missable", "achievement", "challenge", "checkpoint"]);
  const blockVisible = (block) => {
    if (mode === "compact" && !visibleTypes.has(block.type)) return false;
    const hay = `${block.title || ""} ${block.text || ""} ${(block.items || []).map((i) => i.text).join(" ")}`.toLocaleLowerCase("pt-BR");
    if (query && !hay.includes(query)) return false;
    if (filter === "pending" && completed.has(block.id)) return false;
    if (filter === "favorites" && !favorites.has(block.id)) return false;
    if (filter !== "all" && !["pending", "favorites"].includes(filter) && block.type !== filter) return false;
    return true;
  };
  const renderBlock = (block) => {
    const done = completed.has(block.id), favorite = favorites.has(block.id);
    const hiddenSpoiler = block.type === "spoiler" && !revealed.has(block.id);
    const visual = mediaById.get(block.visual_id);
    const items = (block.items || []).length ? `<ul>${block.items.map((item) => `<li>${esc(item.text)}</li>`).join("")}</ul>` : "";
    const table = (block.rows || []).length ? `<div class="smart-table">${block.rows.map((row) => `<div>${row.map((cell) => `<span>${esc(cell)}</span>`).join("")}</div>`).join("")}</div>` : "";
    const refs = (block.source_refs || []).length ? `<span class="smart-source">Fonte ${block.source_refs.map((r) => r.page ? `p.${r.page}` : `§${r.section}`).join(", ")}</span>` : "";
    return `<article class="smart-block type-${block.type} ${done ? "done" : ""}" id="guide-${esc(block.id)}" data-guide-block="${esc(block.id)}">
      <button class="smart-check" data-guide-action="complete" data-value="${!done}" aria-label="${done ? "Marcar pendente" : "Concluir"}">${done ? "✓" : icon[block.type] || "·"}</button>
      <div class="smart-content">
        <div class="smart-block-head"><span class="smart-type">${esc(block.type)}</span>${block.estimated_minutes ? `<span>◷ ${block.estimated_minutes} min</span>` : ""}${refs}</div>
        ${block.title ? `<h4>${esc(block.title)}</h4>` : ""}
        ${hiddenSpoiler ? `<button class="spoiler-cover" data-guide-action="reveal" data-value="true">Revelar spoiler</button>` : `<p>${esc(block.text)}</p>${items}${table}${visual ? `<figure><img src="${esc(visual.url)}" alt="${esc(visual.title || "Visual do guia")}"><figcaption>${esc(visual.attribution || visual.source_name || "")}</figcaption></figure>` : ""}`}
        ${notes[block.id] ? `<div class="smart-note">Sua nota: ${esc(notes[block.id])}</div>` : ""}
      </div>
      <div class="smart-actions"><button data-guide-action="favorite" data-value="${!favorite}" title="Favoritar">${favorite ? "★" : "☆"}</button><button data-guide-note="${esc(block.id)}" title="Nota">＋</button></div>
    </article>`;
  };
  const chaptersHTML = (doc.chapters || []).map((chapter, index) => {
    const blocks = (chapter.blocks || []).filter(blockVisible);
    if (!blocks.length) return "";
    const done = (chapter.blocks || []).filter((b) => completed.has(b.id)).length;
    return `<section class="smart-chapter"><header><span>${String(index + 1).padStart(2, "0")}</span><div><h3>${esc(chapter.title)}</h3><p>${esc(chapter.objective || "")}</p></div><b>${done}/${(chapter.blocks || []).length}</b></header>${blocks.map(renderBlock).join("")}</section>`;
  }).join("");

  const suggestions = (doc.visual_suggestions || []).filter((v) => v.status !== "rejected");
  const visualHTML = mode === "full" && (suggestions.length || media.length) ? `<section class="visual-workbench">
    <div><p class="list-title">RECURSOS VISUAIS</p><h3>Imagens e mapas com origem</h3></div>
    ${suggestions.map((v) => `<button class="visual-suggestion" ${(["route","graph"].includes(v.type) && (v.nodes || []).length) ? `data-diagram-id="${esc(v.id)}"` : `data-media-query="${esc(v.query)}"`}><span>${icon[v.type] || "▧"}</span><b>${esc(v.title)}</b><small>${esc(v.reason)}</small><i>${(["route","graph"].includes(v.type) && (v.nodes || []).length) ? "Gerar diagrama →" : "Revisar busca →"}</i></button>`).join("")}
    ${media.length ? `<div class="guide-gallery">${media.map((m) => `<figure><img src="${esc(m.url)}" alt=""><figcaption>${esc(m.title)} · ${esc(m.license || m.source_name)}</figcaption></figure>`).join("")}</div>` : ""}
  </section>` : "";
  const revisions = bundle.revisions || [];
  const blockLookup = new Map((doc.chapters || []).flatMap((chapter) => (chapter.blocks || []).map((block) => [block.id, block.title || block.text || chapter.title])));
  const revisionHTML = revisions.length ? `<details class="revision-panel"><summary>Histórico de versões (${revisions.length})</summary>${revisions.map((r, i) => `<div><span>${new Date((r.created_at || 0) * 1000).toLocaleString("pt-BR")} · ${esc(r.provider || "local")}</span>${i ? `<button data-restore-revision="${esc(r.revision_id)}">Restaurar</button>` : `<b>ATUAL</b>`}</div>`).join("")}</details>` : "";
  const completionHistory = (progress.history || []).slice(-12).reverse();
  const completionHTML = completionHistory.length ? `<details class="revision-panel"><summary>Histórico da jornada (${progress.history.length})</summary>${completionHistory.map((h) => `<div><span>${new Date((h.at || 0) * 1000).toLocaleString("pt-BR")} · ${esc(blockLookup.get(h.block_id) || "Etapa")}</span><b>${esc(h.action === "completed" ? "CONCLUÍDO" : h.action)}</b></div>`).join("")}</details>` : "";
  const portabilityHTML = S.mode === "demo" ? "" : `<div class="guide-portability"><div><b>Pacote portátil</b><span>Fonte, revisões, atribuições, mídia e progresso opcional.</span></div><button id="guide-pack-export">Exportar</button><button id="guide-pack-import">Importar</button></div>`;
  let sessionUsed = 0;
  const sessionBlocks = (doc.chapters || []).flatMap((chapter) => (chapter.blocks || []).map((block) => ({ ...block, chapter: chapter.title })))
    .filter((block) => !completed.has(block.id) && visibleTypes.has(block.type))
    .filter((block) => { const minutes = block.estimated_minutes || 5; if (sessionUsed + minutes > (progress.session_minutes || 30)) return false; sessionUsed += minutes; return true; })
    .slice(0, 6);
  const sessionHTML = mode !== "source" && sessionBlocks.length ? `<aside class="session-plan"><div><span>SESSÃO DE ${progress.session_minutes || 30} MIN</span><b>${sessionBlocks.length} ações · ~${sessionUsed} min</b></div>${sessionBlocks.map((b) => `<button data-jump-block="${esc(b.id)}"><small>${esc(b.chapter)}</small><strong>${esc(b.title || b.text)}</strong></button>`).join("")}</aside>` : "";

  return `<div class="list-wrap guide-wrap ${S.guideDensity === "compact" ? "density-compact" : ""}">
    <div class="guide-console-head">
      <div><p class="list-title">GUIA INTELIGENTE</p><h2>${esc(doc.title || "Guia estruturado")}</h2><p>${esc(doc.summary || "A fonte original está preservada e disponível a qualquer momento.")}</p></div>
      <div class="guide-actions"><button class="guide-action ai" id="smart-generate" ${["running","queued"].includes(status.phase) ? "disabled" : ""}>✦ ${doc.provider && doc.provider !== "local" ? "Atualizar com IA" : "Organizar com IA"}</button>${importBtn}</div>
    </div>
    ${statusHTML}
    ${sessionHTML}${mode === "source" ? sourceHTML : (chaptersHTML || `<div class="guide-no-results">Nenhum bloco corresponde aos filtros.</div>`)}
    ${visualHTML}${revisionHTML}${completionHTML}${portabilityHTML}
  </div>`;
}

/* Passa as dicas JÁ importadas do jogo pela IA: refina (limpa/cura) ou traduz
   para PT-BR. Não toca nas conquistas nem na ordem. */
async function dicasIa(kind) {
  const slug = S.activeSlug;
  if (!slug) return;
  if (!S.aiReady) {
    toast("Configure uma chave de IA para continuar.");
    return enterSettings("ai", { slug, tab: "tips" });
  }
  try {
    const res = await backend.startGameTipsAI(slug, kind);
    S.tipsAI = res;
    if (!res || !res.ok) {
      toast(res && res.error ? res.error : "Falha na IA.", true);
      return;
    }
    await renderDashboard({ force: true });
    acompanharDicasIa();
  } catch (e) {
    toast("Erro: " + e, true);
  }
}

async function gerarSmartGuide() {
  const slug = S.activeSlug;
  if (!slug || S.mode === "demo") return;
  if (!S.smartGuideConsent) {
    toast("Revise e confirme o uso da IA primeiro.");
    return enterSettings("experience", { slug, tab: "tips" });
  }
  if (!S.aiReady) {
    toast("Configure uma chave de IA para continuar.");
    return enterSettings("ai", { slug, tab: "tips" });
  }
  const res = await backend.startSmartGuide(slug, true).catch((e) => ({ ok: false, error: String(e) }));
  if (!res?.ok) return toast(res?.error || res?.message || "Não foi possível iniciar.", true);
  S.smartStatuses[slug] = res;
  await renderDashboard({ force: true });
  acompanharSmartGuide(slug);
}

async function acompanharSmartGuide(slug) {
  while (S.view === "dashboard") {
    const status = await backend.smartGuideStatus(slug).catch((e) => ({ phase: "error", error: String(e) }));
    S.smartStatuses[slug] = status;
    await renderDashboard({ force: true });
    if (status.phase !== "running") {
      if (status.phase === "success") toast("Guia Inteligente publicado.");
      else if (status.phase === "error") toast(status.error || "Falha ao organizar o guia.", true);
      if (status.phase === "success") {
        delete S.smartStatuses[slug];
        S.library = await backend.library();
        await renderDashboard({ force: true });
      }
      break;
    }
    await esperar(800);
  }
}

async function atualizarGuia(action, blockId, value) {
  const res = await backend.updateGuideProgress(S.activeSlug, action, blockId, value)
    .catch((e) => ({ ok: false, error: String(e) }));
  if (!res?.ok) return toast(res?.error || "Não foi possível salvar.", true);
  await renderDashboard({ force: true });
}

function openGuideMedia(query = "") {
  S.GM = { query, source: "openverse", results: [], busy: false, error: "" };
  renderGuideMedia();
  if (query) searchGuideMedia();
}

function closeGuideMedia() { document.getElementById("guide-media-modal")?.remove(); S.GM = null; }

function renderGuideMedia() {
  let modal = document.getElementById("guide-media-modal");
  if (!modal) { modal = document.createElement("div"); modal.id = "guide-media-modal"; modal.className = "modal-bg"; document.body.appendChild(modal); }
  const G = S.GM;
  modal.innerHTML = `<div class="gf-panel media-panel" role="dialog" aria-modal="true" aria-label="Recursos visuais do guia">
    <div class="gf-head"><div><div class="gf-title">REVISAR RECURSO VISUAL</div><div class="gf-sub">Nada é anexado sem sua aprovação.</div></div><button class="gf-close" id="gm-x">✕</button></div>
    <div class="gf-body"><div class="cv-sources"><button class="cv-src ${G.source === "openverse" ? "on" : ""}" data-gm-source="openverse">Openverse</button><button class="cv-src ${G.source === "wikimedia" ? "on" : ""}" data-gm-source="wikimedia">Wikimedia</button><button class="cv-src" id="gm-broad">Busca ampla ↗</button></div>
      <div class="search-box"><span>⌕</span><input id="gm-q" value="${esc(G.query)}" placeholder="O que ajudaria a explicar esta etapa?"><button class="cv-go" id="gm-go">Buscar</button></div>
      <div class="media-manual"><input id="gm-url" placeholder="Cole aqui a URL direta encontrada na busca ampla"><label><input id="gm-rights" type="checkbox"> Confirmo que posso usar esta imagem</label><button id="gm-url-save">Revisar e salvar URL</button></div>
      <label class="media-local"><span>＋ Adicionar imagem local</span><input id="gm-file" type="file" accept="image/*"></label>
      ${G.busy ? `<div class="status-msg">Buscando mídia com licença identificável…</div>` : ""}${G.error ? `<div class="gf-error">${esc(G.error)}</div>` : ""}
      <div class="media-results">${G.results.map((m, i) => `<article><img src="${esc(m.thumbnail)}" alt=""><div><b>${esc(m.title)}</b><span>${esc(m.creator || "Autor desconhecido")}</span><small>${esc(m.license || "Licença não informada")}</small><button data-gm-approve="${i}">Aprovar e salvar</button></div></article>`).join("")}</div>
    </div></div>`;
  $("#gm-x").onclick = closeGuideMedia;
  modal.querySelectorAll("[data-gm-source]").forEach((b) => b.onclick = () => { G.source = b.dataset.gmSource; renderGuideMedia(); searchGuideMedia(); });
  $("#gm-go").onclick = () => { G.query = ($("#gm-q")?.value || "").trim(); searchGuideMedia(); };
  $("#gm-q").onkeydown = (e) => { if (e.key === "Enter") { G.query = e.currentTarget.value.trim(); searchGuideMedia(); } };
  $("#gm-broad").onclick = () => backend.broadMediaSearch(G.query || ($("#gm-q")?.value || ""));
  $("#gm-url-save").onclick = async () => {
    const url = ($("#gm-url")?.value || "").trim(), confirmed = !!$("#gm-rights")?.checked;
    if (!url || !confirmed) return toast("Cole a URL e confirme o direito de uso.", true);
    const res = await backend.approveGuideMedia(S.activeSlug, { source:"manual", url, title:"Imagem da busca ampla", creator:"", license:"Uso confirmado pelo usuário", provider:"Busca ampla", landing_url:url }, true);
    if (!res?.ok) return toast(res?.error || "Falha ao salvar URL.", true);
    closeGuideMedia(); toast("Imagem salva com confirmação de uso."); await renderDashboard({ force: true });
  };
  $("#gm-file").onchange = async (e) => {
    const file = e.target.files?.[0]; if (!file) return;
    const data = await fileToBase64(file);
    const res = await backend.addGuideMedia(S.activeSlug, data, file.name, file.name);
    if (!res?.ok) return toast(res?.error || "Falha ao anexar.", true);
    closeGuideMedia(); toast("Imagem local adicionada."); await renderDashboard({ force: true });
  };
  modal.querySelectorAll("[data-gm-approve]").forEach((b) => b.onclick = async () => {
    const candidate = G.results[Number(b.dataset.gmApprove)];
    b.disabled = true; b.textContent = "Salvando…";
    const res = await backend.approveGuideMedia(S.activeSlug, candidate, false);
    if (!res?.ok) { b.disabled = false; b.textContent = "Aprovar e salvar"; return toast(res?.error || "Falha ao salvar.", true); }
    closeGuideMedia(); toast("Imagem salva com atribuição."); await renderDashboard({ force: true });
  });
}

async function searchGuideMedia() {
  const G = S.GM; if (!G || !G.query) return;
  G.busy = true; G.error = ""; renderGuideMedia();
  const res = await backend.searchGuideMedia(G.query, G.source).catch((e) => ({ ok: false, error: String(e) }));
  if (!S.GM) return;
  G.busy = false; G.results = res?.results || []; G.error = res?.ok ? "" : (res?.error || "Falha na busca."); renderGuideMedia();
}

const esperar = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function acompanharDicasIa() {
  if (S.tipsAIPolling) return;
  S.tipsAIPolling = true;
  try {
    while (true) {
      const status = await backend.gameTipsAIStatus();
      S.tipsAI = status;
      if (S.view === "dashboard") await renderDashboard({ force: true });
      if (!["running", "queued"].includes(status.phase)) {
        if (status.phase === "success") toast(status.message || "Dicas processadas com sucesso.");
        else if (status.phase === "error") toast(status.error || "Falha ao processar as dicas.", true);
        break;
      }
      await esperar(700);
    }
  } catch (e) {
    toast("Não foi possível acompanhar a IA: " + e, true);
  } finally {
    S.tipsAIPolling = false;
  }
}

function bindSidebar() {
  root.querySelectorAll(".tile").forEach((b) => {
    b.onclick = async () => {
      S.activeSlug = b.dataset.slug;
      closeLibraryDrawer();
      await renderDashboard();
    };
  });
  $("#library-scrim")?.addEventListener("click", closeLibraryDrawer);
  $(".console-search-button")?.addEventListener("click", () => $("#library-search")?.focus());
  $("#library-search")?.addEventListener("input", (e) => {
    S.libraryQuery = e.currentTarget.value;
    const query = S.libraryQuery.trim().toLocaleLowerCase("pt-BR");
    let found = 0;
    root.querySelectorAll(".tile").forEach((tile) => {
      const show = !query || tile.dataset.title.includes(query);
      tile.hidden = !show;
      if (show) found += 1;
    });
    const empty = $("#library-filter-empty");
    empty?.classList.toggle("hidden", found > 0);
    if (empty) empty.textContent = query ? "Nenhum jogo encontrado." : "Nenhum jogo ainda.";
  });
  root.querySelectorAll(".ptab").forEach((b) => {
    b.onclick = () => { S.tab = b.dataset.tab; renderDashboard(); };
  });
  $("#smart-generate")?.addEventListener("click", gerarSmartGuide);
  $("#guide-consent")?.addEventListener("click", () => enterSettings("experience", { slug: S.activeSlug, tab: "tips" }));
  $("#guide-config-ai")?.addEventListener("click", () => enterSettings("ai", { slug: S.activeSlug, tab: "tips" }));
  $("#guide-retry")?.addEventListener("click", gerarSmartGuide);
  root.querySelectorAll("[data-guide-mode]").forEach((b) => b.onclick = () => { S.guideMode = b.dataset.guideMode; renderDashboard({ force: true }); });
  $("#guide-search")?.addEventListener("input", (e) => {
    S.guideQuery = e.currentTarget.value;
    const q = S.guideQuery.trim().toLocaleLowerCase("pt-BR");
    root.querySelectorAll(".smart-block").forEach((block) => { block.hidden = !!q && !block.textContent.toLocaleLowerCase("pt-BR").includes(q); });
    root.querySelectorAll(".smart-chapter").forEach((chapter) => { chapter.hidden = !chapter.querySelector(".smart-block:not([hidden])"); });
  });
  const guideFilter = $("#guide-filter");
  if (guideFilter) { guideFilter.value = S.guideFilter; guideFilter.onchange = () => { S.guideFilter = guideFilter.value; renderDashboard({ force: true }); }; }
  $("#guide-session")?.addEventListener("change", (e) => atualizarGuia("session_minutes", "", Number(e.currentTarget.value)));
  root.querySelectorAll("[data-jump-block]").forEach((b) => b.onclick = () => document.getElementById(`guide-${b.dataset.jumpBlock}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
  root.querySelectorAll("[data-guide-action]").forEach((b) => b.onclick = () => atualizarGuia(
    b.dataset.guideAction, b.closest("[data-guide-block]")?.dataset.guideBlock || "", b.dataset.value === "true"));
  root.querySelectorAll("[data-guide-note]").forEach((b) => b.onclick = async () => {
    const blockId = b.dataset.guideNote;
    const text = window.prompt("Nota pessoal para este passo:", "");
    if (text !== null) atualizarGuia("note", blockId, text);
  });
  root.querySelectorAll("[data-restore-revision]").forEach((b) => b.onclick = async () => {
    const res = await backend.restoreSmartGuide(S.activeSlug, b.dataset.restoreRevision);
    if (!res?.ok) return toast(res?.error || "Falha ao restaurar.", true);
    toast("Versão restaurada sem apagar o histórico."); await renderDashboard({ force: true });
  });
  root.querySelectorAll("[data-media-query]").forEach((b) => b.onclick = () => openGuideMedia(b.dataset.mediaQuery));
  root.querySelectorAll("[data-diagram-id]").forEach((b) => b.onclick = () => gerarDiagramaSugerido(b.dataset.diagramId));
  $("#guide-pack-export")?.addEventListener("click", exportGuidePack);
  $("#guide-pack-import")?.addEventListener("click", importGuidePack);
  root.querySelectorAll("[data-jump-guide]").forEach((b) => b.onclick = () => {
    S.tab = "tips"; S.guideMode = "compact"; renderDashboard({ force: true }).then(() => {
      const id = b.dataset.jumpGuide; (id ? document.getElementById(`guide-${id}`) : $(".guide-console-head"))?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
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
  const cover = $("#btn-cover");
  if (cover) cover.onclick = () => {
    if (S.mode === "demo") return toast("Disponível só no app real.", true);
    const jogo = S.library.find((g) => g.slug === S.activeSlug);
    if (jogo) openCoverPicker(jogo);
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
async function enterSettings(section = "", returnTo = null) {
  S.view = "settings";
  stopPolling();
  const [estado, ia, sources, compact, overlay, update] = await Promise.all([
    backend.appState().catch(() => ({})),
    backend.getAiConfig().catch(() => ({ ok: false })),
    backend.getSourcesConfig().catch(() => ({ ok: false })),
    backend.getCompactConfig().catch(() => null),
    backend.overlayStatus().catch((e) => ({ ok: false, error: String(e) })),
    backend.updateStatus().catch((e) => ({ ok: false, error: String(e) })),
  ]);
  S.SET = {
    estado,
    ia: ia && ia.ok ? ia : null,
    sources: sources && sources.ok ? sources.ready : {},
    compact: compact && compact.ok ? compact : { width: 300, height: 232, last: 2, next: 0 },
    overlay,
    update,
    returnTo,
    section: section || readSettingsSection(),
    drafts: {},
    originals: {},
    scroll: {},
  };
  Object.keys(SETTINGS_META).forEach((id) => {
    S.SET.drafts[id] = settingsSnapshot(id);
    S.SET.originals[id] = cloneSettings(S.SET.drafts[id]);
  });
  renderSettings();
}

async function exportGuidePack() {
  const res = await backend.exportGuidePack(S.activeSlug, true).catch((e) => ({ ok: false, error: String(e) }));
  if (!res?.ok) return toast(res?.error || "Falha ao exportar.", true);
  const bytes = Uint8Array.from(atob(res.data), (c) => c.charCodeAt(0));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
  link.download = res.filename || `${S.activeSlug}.dtguide`;
  link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  toast("Pacote do guia exportado.");
}

async function gerarDiagramaSugerido(id) {
  const game = await backend.game(S.activeSlug);
  const suggestion = (game?.smart_guide?.current?.visual_suggestions || []).find((v) => v.id === id);
  if (!suggestion) return toast("Sugestão visual não encontrada.", true);
  const res = await backend.createGuideDiagram(S.activeSlug, suggestion).catch((e) => ({ ok: false, error: String(e) }));
  if (!res?.ok) return toast(res?.error || "Falha ao criar diagrama.", true);
  toast("Diagrama seguro gerado localmente."); await renderDashboard({ force: true });
}

function importGuidePack() {
  const input = document.createElement("input"); input.type = "file"; input.accept = ".dtguide,application/zip";
  input.onchange = async () => {
    const file = input.files?.[0]; if (!file) return;
    const data = await fileToBase64(file);
    const res = await backend.importGuidePack(S.activeSlug, data).catch((e) => ({ ok: false, error: String(e) }));
    if (!res?.ok) return toast(res?.error || "Pacote inválido.", true);
    toast("Pacote importado com sucesso."); await renderDashboard({ force: true });
  };
  input.click();
}

async function leaveSettings(force = false) {
  if (!force && settingsDirty()) return showPendingSettingsModal(null);
  const back = S.SET && S.SET.returnTo;
  if (back) {
    S.activeSlug = back.slug || S.activeSlug;
    S.tab = back.tab || "tips";
  }
  await enterDashboard();
}

const SETTINGS_META = {
  account: ["◉", "Conta e atualizações", "Conta conectada, versão instalada e atualizações estáveis."],
  experience: ["◈", "Interface e guias", "Aparência, densidade e comportamento do Guia Inteligente."],
  ai: ["✦", "Inteligência artificial", "Provedor, modelo e credenciais usados para organizar seus guias."],
  images: ["▧", "Fontes de imagem", "Serviços opcionais para capas, fundos e arte dos jogos."],
  library: ["▦", "Biblioteca", "Como novos jogos entram automaticamente na sua biblioteca."],
  overlay: ["▣", "Overlay", "Detecção do emulador, encaixe e diagnóstico em tempo real."],
  compact: ["⊡", "Modo compacto", "Tamanho e quantidade de informações exibidas no overlay."],
};

const cloneSettings = (value) => JSON.parse(JSON.stringify(value || {}));
const settingsLocalKey = "digitracker.settings.section";
function readSettingsSection() { try { return localStorage.getItem(settingsLocalKey) || "account"; } catch (_) { return "account"; } }
function writeSettingsSection(id) { try { localStorage.setItem(settingsLocalKey, id); } catch (_) {} }

function settingsSnapshot(section) {
  const e = S.SET.estado || {};
  const cc = S.SET.compact || {};
  if (section === "account") return { auto_check_updates: !!e.auto_check_updates };
  if (section === "experience") return {
    smart_guide_auto: !!e.smart_guide_auto, smart_guide_consent: !!e.smart_guide_consent,
    reduced_motion: !!e.reduced_motion, guide_density: e.guide_density || "comfortable",
    ui_scale: Number(e.ui_scale || 100),
  };
  if (section === "library") return { auto_import: !!e.auto_import };
  if (section === "overlay") return {
    auto_overlay: !!e.auto_overlay, overlay_exit_fullscreen: !!e.overlay_exit_fullscreen,
    overlay_second_screen: !!e.overlay_second_screen, overlay_fit_emulator: !!e.overlay_fit_emulator,
  };
  if (section === "compact") return {
    compact_width: Number(cc.width || 300), compact_height: Number(cc.height || 232),
    compact_last: Number(cc.last || 2), compact_next: Number(cc.next || 0),
  };
  if (section === "ai") return {
    provider: S.SET.ia?.provider || "", model: S.SET.ia?.model || "", base_url: S.SET.ia?.base_url || "",
    api_key: "", clear_key: false,
  };
  if (section === "images") return {
    steamgriddb: { key1: "", key2: "", clear: false }, rawg: { key1: "", key2: "", clear: false },
    igdb: { key1: "", key2: "", clear: false },
  };
  return {};
}

function settingsHasChanges(section) {
  const draft = S.SET?.drafts?.[section];
  const original = S.SET?.originals?.[section];
  return JSON.stringify(draft || {}) !== JSON.stringify(original || {});
}
function settingsDirty() { return !!S.SET && settingsHasChanges(S.SET.section); }
function settingsToggle(key, value, label, sub) {
  return `<div class="set-row"><div><div class="set-txt">${esc(label)}</div><div class="set-sub">${esc(sub)}</div></div>
    <button class="switch ${value ? "on" : ""}" data-draft-toggle="${key}" role="switch" aria-checked="${!!value}" aria-label="${esc(label)}"></button></div>`;
}
function settingsPanelFrame(id, body) {
  const meta = SETTINGS_META[id];
  const dirty = settingsHasChanges(id);
  return `<section class="settings-panel" data-settings-panel="${id}" aria-labelledby="settings-panel-title">
    <div class="settings-panel-head"><div><div class="settings-kicker">${meta[0]} CONFIGURAÇÕES / ${esc(meta[1].toUpperCase())}</div>
      <h2 id="settings-panel-title">${esc(meta[1])}</h2><p>${esc(meta[2])}</p></div>
      <span class="settings-panel-state ${dirty ? "dirty" : ""}" id="settings-state">${dirty ? "Alterações pendentes" : "Tudo salvo"}</span></div>
    <div class="settings-panel-scroll" id="settings-panel-scroll">${body}</div>
    <div class="settings-panel-actions"><button class="btn-ghost" id="settings-discard" ${dirty ? "" : "disabled"}>Descartar</button>
      <button class="btn-primary" id="settings-save" ${dirty ? "" : "disabled"}>Salvar alterações</button></div>
  </section>`;
}

function renderSettings() {
  $("#btn-library").hidden = true; closeLibraryDrawer();
  const id = S.SET.section || "account";
  const { estado, ia, sources, overlay, update } = S.SET;
  const draft = S.SET.drafts[id] || (S.SET.drafts[id] = settingsSnapshot(id));
  if (!S.SET.originals[id]) S.SET.originals[id] = cloneSettings(draft);
  const ov = overlay || {}, ready = sources || {}, up = update || {};
  const ovRect = Array.isArray(ov.rect) ? ov.rect.join(", ") : "—";
  const ovState = ov.detected ? `${esc(ov.process || "processo desconhecido")} · ${esc(ov.title || "sem título")}` : (ov.error ? esc(ov.error) : "Nenhum emulador detectado agora");
  const upText = up.update_available ? `Versão ${esc(up.latest_version)} disponível` : (up.phase === "error" ? esc(up.error || "Falha ao consultar") : "Você está na versão atual");
  const field = (key, value, type = "text", extra = "") => `<input class="set-field" data-draft-field="${key}" type="${type}" value="${esc(value ?? "")}" ${extra} />`;
  const panel = id === "account" ? settingsPanelFrame(id, `<div class="settings-card"><h3>Conta conectada</h3><div class="set-row"><div><div class="set-txt">RetroAchievements</div><div class="set-sub">${estado.username ? esc(estado.username) : "não conectada"}</div></div><button class="btn-ghost" id="set-reconnect">Trocar conta</button></div></div>
    <div class="settings-card"><h3>Atualizações</h3><div class="set-row"><div><div class="set-txt">DigiTracker ${esc(estado.version || S.version)}</div><div class="set-sub">${upText}</div></div><button class="btn-ghost" id="set-update-check">Procurar agora</button></div>${settingsToggle("auto_check_updates", draft.auto_check_updates, "Procurar atualizações ao iniciar", "Apenas releases estáveis; a instalação sempre pede confirmação")}</div>`)
  : id === "experience" ? settingsPanelFrame(id, `<div class="settings-card"><h3>Experiência DigiTracker Console</h3><p class="set-hint">Combina a apresentação cinematográfica da PSN, a navegação do Steam Deck e a identidade do DigiTracker. O Guia Inteligente nunca apaga sua fonte importada.</p>${settingsToggle("smart_guide_auto", draft.smart_guide_auto, "Organizar guias automaticamente", "Depois de cada importação, cria uma revisão compacta e validada")}${settingsToggle("smart_guide_consent", draft.smart_guide_consent, "Permitir envio do guia à IA", "O provedor configurado pode cobrar pelo processamento. Imagens pesquisadas continuam exigindo aprovação")}${settingsToggle("reduced_motion", draft.reduced_motion, "Reduzir animações", "Remove transições de profundidade e movimentos não essenciais")}</div><div class="settings-card"><div class="experience-grid"><div><label class="set-label">Densidade</label><select class="set-field" data-draft-field="guide_density"><option value="comfortable" ${draft.guide_density === "comfortable" ? "selected" : ""}>Confortável adaptável</option><option value="compact" ${draft.guide_density === "compact" ? "selected" : ""}>Compacta</option></select></div><div><label class="set-label">Escala da interface: <b id="settings-scale-label">${draft.ui_scale}%</b></label><input class="set-range" data-draft-field="ui_scale" type="range" min="80" max="140" step="5" value="${draft.ui_scale}"></div></div></div>`)
  : id === "ai" ? settingsPanelFrame(id, ia ? `<div class="settings-card"><h3>Provedor ativo</h3><div class="ai-providers">${ia.providers.map((p) => `<button class="ai-prov ${p.id === draft.provider ? "on" : ""}" data-settings-provider="${esc(p.id)}"><span class="ai-prov-name">${esc(p.label)}</span>${p.has_key ? `<span class="ai-prov-ok">✓ chave salva</span>` : ""}</button>`).join("")}</div></div><div class="settings-card"><label class="set-label">Chave da API${(ia.providers.find((p) => p.id === draft.provider) || {}).has_key ? " (salva — deixe em branco para manter)" : ""}</label>${field("api_key", "", "password", `placeholder="${((ia.providers.find((p) => p.id === draft.provider) || {}).has_key) ? "••••••••••••••••" : "cole a chave aqui"}" autocomplete="off"`)}<button class="btn-ghost settings-inline-action ${draft.clear_key ? "selected" : ""}" data-ai-clear>${draft.clear_key ? "Chave será removida" : "Remover chave salva"}</button><label class="set-label">Modelo</label>${field("model", draft.model, "text", "autocomplete=off")}${(ia.providers.find((p) => p.id === draft.provider) || {}).needs_base_url ? `<label class="set-label">Endpoint (OpenRouter, Ollama, LM Studio…)</label>${field("base_url", draft.base_url, "text", "autocomplete=off")}` : ""}<p class="set-hint">A chave fica só em <code>config/secrets.json</code>, nesta máquina.</p></div>` : `<div class="settings-card"><h3>Inteligência artificial</h3><p class="set-hint">Indisponível no modo demonstração.</p></div>`)
  : id === "images" ? settingsPanelFrame(id, `<p class="set-hint settings-intro">Configure as fontes opcionais de capas e fundos. As credenciais só serão enviadas quando você salvar esta sessão.</p>${[["steamgriddb","SteamGridDB","Capas da comunidade.",ready.steamgriddb],["rawg","RAWG","Fundos e screenshots para jogos retrô.",ready.rawg],["igdb","IGDB","Capas de qualidade via Twitch.",ready.igdb]].map(([key,label,sub,has]) => `<div class="src-cfg settings-card"><h3>${label} ${has ? "✓" : ""}</h3><p class="set-hint">${sub}</p>${field(`${key}.key1`, "", key === "igdb" ? "text" : "password", `placeholder="${has ? "•••••••• (salva — em branco mantém)" : (key === "igdb" ? "Twitch Client ID" : "chave da API")}" autocomplete="off"`)}${key === "igdb" ? field(`${key}.key2`, "", "password", `placeholder="${has ? "•••• (segredo salvo — em branco mantém)" : "Twitch Client Secret"}" autocomplete="off"`) : ""}<button class="btn-ghost settings-inline-action ${draft[key].clear ? "selected" : ""}" data-source-clear="${key}">${draft[key].clear ? "Fonte será removida" : "Remover credencial salva"}</button></div>`).join("")}`)
  : id === "library" ? settingsPanelFrame(id, `<div class="settings-card"><h3>Entrada de jogos</h3>${settingsToggle("auto_import", draft.auto_import, "Importar jogos novos automaticamente", "Verifica a cada 5 minutos e traz os jogos em que você começou a jogar")}</div>`)
  : id === "overlay" ? settingsPanelFrame(id, `<div class="settings-card"><h3>Comportamento</h3>${settingsToggle("auto_overlay", draft.auto_overlay, "Grudar no emulador", "Vira overlay e acompanha a janela quando um emulador abre")}${settingsToggle("overlay_exit_fullscreen", draft.overlay_exit_fullscreen, "Sair do fullscreen exclusivo", "Manda Alt+Enter para o emulador quando autorizado")}${settingsToggle("overlay_second_screen", draft.overlay_second_screen, "Usar o segundo monitor", "Leva o overlay para a tela que o jogo não ocupa")}${settingsToggle("overlay_fit_emulator", draft.overlay_fit_emulator, "Ajustar ao tamanho do emulador", "Mantém o overlay proporcional à janela do emulador")}</div><div class="overlay-diag settings-card ${ov.detected ? "ok" : ""}"><h3>Diagnóstico de detecção</h3><div class="set-sub">${ovState}</div><div class="overlay-diag-grid"><span>Área interna</span><code>${esc(ovRect)}</code><span>Overlay</span><code>${esc((ov.overlay_size || []).join(" × ") || "—")}</code><span>Posição</span><code>${esc((ov.dock || []).join(", ") || "—")}</code></div><button class="btn-ghost" id="overlay-test">Testar detecção agora</button></div>`)
  : settingsPanelFrame(id, `<div class="settings-card"><h3>Dimensões e conteúdo</h3><p class="set-hint">Defina o tamanho do overlay e quantas conquistas ele mostra.</p><div class="set-grid2"><div><label class="set-label">Largura (px)</label>${field("compact_width", draft.compact_width, "number", "min=240 max=640")}</div><div><label class="set-label">Altura (px)</label>${field("compact_height", draft.compact_height, "number", "min=150 max=900")}</div><div><label class="set-label">Últimas obtidas</label>${field("compact_last", draft.compact_last, "number", "min=0 max=10")}</div><div><label class="set-label">Próximas (0 = auto)</label>${field("compact_next", draft.compact_next, "number", "min=0 max=10")}</div></div></div>`);

  root.innerHTML = `<div class="view"><div class="wiz-head"><button class="back" id="set-back" title="Voltar">←</button><div><div class="t">Configurações</div><div class="s">Central de controle · sessão independente</div></div></div><div class="settings"><div class="settings-shell"><nav class="settings-nav" aria-label="Categorias das configurações"><p>Preferências</p>${Object.entries(SETTINGS_META).map(([key,meta]) => `<button class="set-nav-btn ${id === key ? "active" : ""}" data-set-target="${key}"><span>${meta[0]}</span><span>${esc(meta[1])}</span>${settingsHasChanges(key) ? "<i>•</i>" : ""}</button>`).join("")}</nav><main class="settings-inner">${panel}</main></div></div></div>`;
  const scroll = $("#settings-panel-scroll");
  if (scroll) { scroll.scrollTop = S.SET.scroll?.[id] || 0; scroll.onscroll = () => { S.SET.scroll[id] = scroll.scrollTop; }; }
  $("#set-back").onclick = leaveSettings;
  $("#set-reconnect")?.addEventListener("click", () => { S.mode = "real"; renderSetup(); });
  $("#set-update-check")?.addEventListener("click", () => procurarAtualizacao(true));
  $("#overlay-test")?.addEventListener("click", testarOverlay);
  $("#settings-save")?.addEventListener("click", () => saveSettingsSession());
  $("#settings-discard")?.addEventListener("click", () => discardSettingsSession());
  root.querySelectorAll("[data-set-target]").forEach((b) => b.addEventListener("click", () => requestSettingsSection(b.dataset.setTarget)));
  root.querySelectorAll("[data-draft-toggle]").forEach((b) => b.addEventListener("click", () => { const key = b.dataset.draftToggle; S.SET.drafts[id][key] = !S.SET.drafts[id][key]; b.classList.toggle("on", S.SET.drafts[id][key]); b.setAttribute("aria-checked", String(S.SET.drafts[id][key])); markSettingsDirty(); }));
  root.querySelectorAll("[data-draft-field]").forEach((input) => input.addEventListener("input", () => { setDraftValue(input.dataset.draftField, input.value); if (input.dataset.draftField === "ui_scale") $("#settings-scale-label").textContent = `${input.value}%`; markSettingsDirty(); }));
  root.querySelectorAll("[data-settings-provider]").forEach((b) => b.addEventListener("click", () => { S.SET.drafts.ai.provider = b.dataset.settingsProvider; S.SET.drafts.ai.model = ""; S.SET.drafts.ai.base_url = ""; markSettingsDirty(); renderSettings(); }));
  $("[data-ai-clear]")?.addEventListener("click", () => { S.SET.drafts.ai.clear_key = !S.SET.drafts.ai.clear_key; renderSettings(); });
  root.querySelectorAll("[data-source-clear]").forEach((b) => b.addEventListener("click", () => { const key = b.dataset.sourceClear; S.SET.drafts.images[key].clear = !S.SET.drafts.images[key].clear; renderSettings(); }));
}

function setDraftValue(path, value) {
  const parts = String(path).split("."); let obj = S.SET.drafts[S.SET.section];
  for (let i = 0; i < parts.length - 1; i++) obj = obj[parts[i]];
  const key = parts[parts.length - 1]; obj[key] = ["ui_scale", "compact_width", "compact_height", "compact_last", "compact_next"].includes(key) ? Number(value) : value;
}
function markSettingsDirty() {
  const dirty = settingsDirty(); const state = $("#settings-state"); const save = $("#settings-save"); const discard = $("#settings-discard");
  if (state) { state.textContent = dirty ? "Alterações pendentes" : "Tudo salvo"; state.classList.toggle("dirty", dirty); }
  if (save) save.disabled = !dirty; if (discard) discard.disabled = !dirty;
  root.querySelectorAll(".set-nav-btn").forEach((b) => { const key = b.dataset.setTarget; const dot = b.querySelector("i"); const has = settingsHasChanges(key); if (has && !dot) b.insertAdjacentHTML("beforeend", "<i>•</i>"); if (!has && dot) dot.remove(); });
}
function discardSettingsSession() { const id = S.SET.section; S.SET.drafts[id] = cloneSettings(S.SET.originals[id]); renderSettings(); toast("Alterações descartadas."); }
function requestSettingsSection(id) {
  if (id === S.SET.section) return;
  if (settingsDirty()) return showPendingSettingsModal(id);
  switchSettingsSection(id);
}
function switchSettingsSection(id) { S.SET.section = id; writeSettingsSection(id); if (!S.SET.drafts[id]) { S.SET.drafts[id] = settingsSnapshot(id); S.SET.originals[id] = cloneSettings(S.SET.drafts[id]); } renderSettings(); }
function showPendingSettingsModal(target = null) {
  $("#settings-pending-modal")?.remove();
  root.insertAdjacentHTML("beforeend", `<div class="gf-backdrop" id="settings-pending-modal"><div class="gf-panel settings-pending"><h3>Alterações não salvas</h3><p>Você tem alterações pendentes nesta sessão. O que deseja fazer?</p><div class="settings-pending-actions"><button class="btn-primary" id="pending-save">Salvar e continuar</button><button class="btn-ghost" id="pending-discard">Descartar alterações</button><button class="btn-ghost" id="pending-cancel">Continuar editando</button></div></div></div>`);
  $("#pending-save").onclick = async () => { if (await saveSettingsSession()) { $("#settings-pending-modal")?.remove(); if (target) switchSettingsSection(target); else leaveSettings(true); } };
  $("#pending-discard").onclick = () => { discardSettingsSession(); $("#settings-pending-modal")?.remove(); if (target) switchSettingsSection(target); else leaveSettings(true); };
  $("#pending-cancel").onclick = () => $("#settings-pending-modal")?.remove();
}

async function saveSettingsSession() {
  const id = S.SET.section, draft = S.SET.drafts[id]; if (!settingsDirty()) return true;
  const save = $("#settings-save"); if (save) { save.disabled = true; save.textContent = "Salvando…"; }
  let res;
  try {
    if (id === "ai") {
      res = await backend.setAiConfig({ provider: draft.provider, api_key: draft.clear_key ? "" : (draft.api_key || null), model: draft.model, base_url: draft.base_url });
      if (res?.ok) S.SET.ia = res;
    } else if (id === "images") {
      res = { ok: true, ready: S.SET.sources || {} };
      for (const key of ["steamgriddb", "rawg", "igdb"]) {
        const item = draft[key]; const hasInput = item.key1 || item.key2 || item.clear;
        if (!hasInput) continue;
        const saved = await backend.setSourceKey(key, item.clear ? "" : (item.key1 || null), item.clear ? "" : (item.key2 || null));
        if (!saved?.ok) { res = saved; break; } res.ready = saved.ready || res.ready;
      }
      if (res.ok) S.SET.sources = res.ready;
    } else {
      const payload = id === "compact" ? draft : draft;
      res = await backend.setSettingsSession(id, payload);
    }
  } catch (e) { res = { ok: false, error: String(e) }; }
  if (!res?.ok) { if (save) { save.disabled = false; save.textContent = "Salvar alterações"; } toast(res?.error || "Não foi possível salvar.", true); return false; }
  S.SET.originals[id] = cloneSettings(draft); S.SET.drafts[id] = cloneSettings(draft);
  const e = S.SET.estado || {}; Object.assign(e, res); S.SET.estado = e;
  if (id === "experience") { S.smartGuideAuto = !!draft.smart_guide_auto; S.smartGuideConsent = !!draft.smart_guide_consent; S.reducedMotion = !!draft.reduced_motion; S.guideDensity = draft.guide_density; S.uiScale = Number(draft.ui_scale); applyExperience(); }
  if (id === "account") S.autoCheckUpdates = !!draft.auto_check_updates;
  if (id === "library") S.autoImport = !!draft.auto_import;
  if (id === "overlay") { S.autoOverlay = !!draft.auto_overlay; S.overlayExitFullscreen = !!draft.overlay_exit_fullscreen; S.overlaySecondScreen = !!draft.overlay_second_screen; S.overlayFitEmulator = !!draft.overlay_fit_emulator; }
  if (id === "compact") S.compactCfg = { ok: true, width: draft.compact_width, height: draft.compact_height, last: draft.compact_last, next: draft.compact_next };
  renderSettings(); toast("Sessão salva com sucesso."); return true;
}

/* Salva (ou remove) a credencial de uma fonte de imagem. IGDB tem dois campos
   (client id + secret); as demais, um. */
async function salvarFonte(source, remover) {
  let key1, key2 = null;
  if (source === "igdb") {
    key1 = remover ? "" : (($("#src-igdb-id")?.value || "").trim() || null);
    key2 = remover ? "" : (($("#src-igdb-secret")?.value || "").trim() || null);
  } else {
    key1 = remover ? "" : (($("#src-" + source)?.value || "").trim() || null);
  }
  if (!remover && key1 === null && key2 === null) return;  // nada digitado
  const res = await backend.setSourceKey(source, key1, key2);
  if (!res || !res.ok) return toast("Não foi possível salvar.", true);
  S.SET.sources = res.ready || {};
  renderSettings();
  const nome = { steamgriddb: "SteamGridDB", rawg: "RAWG", igdb: "IGDB" }[source] || source;
  toast(remover ? `Chave do ${nome} removida.` : `${nome} pronto.`);
}

async function salvarCompacto() {
  const num = (id, def) => {
    const v = parseInt($("#" + id)?.value, 10);
    return Number.isFinite(v) ? v : def;
  };
  const cfg = {
    width: num("cc-w", 300), height: num("cc-h", 232),
    last: num("cc-last", 2), next: num("cc-next", 0),
  };
  const res = await backend.setCompactConfig(cfg);
  if (!res || !res.ok) return toast("Não foi possível salvar.", true);
  S.SET.compact = res;
  S.compactCfg = res;   // o overlay passa a usar já no próximo render
  toast("Modo compacto atualizado.");
}

async function alternarPreferencia(chave, botao) {
  const novo = !botao.classList.contains("on");
  botao.classList.toggle("on", novo);
  botao.setAttribute("aria-checked", String(novo));
  if (chave === "auto_import") { S.autoImport = novo; await backend.setAutoImport(novo); }
  else if (chave === "auto_overlay") { S.autoOverlay = novo; await backend.setAutoOverlay(novo); }
  else if (chave === "auto_check_updates") {
    S.autoCheckUpdates = novo;
    await backend.setAutoCheckUpdates(novo);
  }
  else if (["smart_guide_auto", "smart_guide_consent", "reduced_motion"].includes(chave)) {
    if (chave === "smart_guide_auto") S.smartGuideAuto = novo;
    else if (chave === "smart_guide_consent") S.smartGuideConsent = novo;
    else S.reducedMotion = novo;
    await backend.setExperience({
      smart_auto: chave === "smart_guide_auto" ? novo : null,
      consent: chave === "smart_guide_consent" ? novo : null,
      reduced_motion: chave === "reduced_motion" ? novo : null,
    });
    applyExperience();
  }
  else {
    if (chave === "overlay_exit_fullscreen") S.overlayExitFullscreen = novo;
    else if (chave === "overlay_fit_emulator") S.overlayFitEmulator = novo;
    else S.overlaySecondScreen = novo;
    await backend.setOverlayOption(chave, novo);
  }
}

function applyExperience() {
  document.documentElement.style.setProperty("--ui-scale", String(S.uiScale / 100));
  document.documentElement.dataset.density = S.guideDensity;
  document.documentElement.classList.toggle("reduced-motion", !!S.reducedMotion);
}

async function salvarExperiencia() {
  S.guideDensity = $("#set-density")?.value || "comfortable";
  S.uiScale = Number($("#set-scale")?.value || 100);
  const res = await backend.setExperience({ density: S.guideDensity, ui_scale: S.uiScale });
  if (!res?.ok) return toast("Não foi possível salvar a aparência.", true);
  applyExperience();
  toast("Aparência aplicada.");
  renderSettings();
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
  const ativo = res.providers.find((p) => p.id === res.provider) || res.providers[0];
  S.aiProviderLabel = ativo?.label || "";
  S.aiModel = res.model || ativo?.default_model || "";
  S.SET.ia = res;
  renderSettings();
  const nome = ativo.label;
  toast(S.aiReady ? `IA pronta via ${nome}.` : "Chave removida.");
  if (S.aiReady && S.SET?.returnTo) await leaveSettings();
}

/* ============================ ATUALIZAÇÕES ============================== */
let updatePoll = null;
let updateModalPhase = "idle";

const formatBytes = (value) => {
  const n = Number(value) || 0;
  if (!n) return "0 MB";
  return `${(n / 1024 / 1024).toFixed(n >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
};

async function procurarAtualizacao(force = false) {
  let res;
  try {
    res = await backend.checkForUpdates(force);
  } catch (e) {
    if (force) toast("Não foi possível consultar atualizações: " + e, true);
    return;
  }
  if (S.view === "settings" && S.SET) {
    S.SET.update = res;
    renderSettings();
  }
  if (!res || !res.ok) {
    if (force) toast(res?.error || "Não foi possível consultar atualizações.", true);
    return;
  }
  if (res.update_available) return renderUpdateModal(res);
  if (force) toast(res.source_mode
    ? `Código-fonte na versão ${res.current_version}; instalação automática disponível apenas no .exe.`
    : `DigiTracker ${res.current_version} já está atualizado.`);
}

function closeUpdateModal(force = false) {
  if (!force && ["downloading", "installing"].includes(updateModalPhase)) return;
  clearInterval(updatePoll);
  updatePoll = null;
  $("#update-modal")?.remove();
}

function updateNotes(text) {
  const value = String(text || "Sem notas publicadas para esta versão.").slice(0, 5000);
  return esc(value).replace(/\r?\n/g, "<br>");
}

function renderUpdateModal(info) {
  let el = $("#update-modal");
  if (!el) {
    el = document.createElement("div");
    el.id = "update-modal";
    el.className = "gf-backdrop";
    document.body.appendChild(el);
  }
  const phase = info.phase || "available";
  updateModalPhase = phase;
  const total = Number(info.bytes_total || info.download_size || 0);
  const done = Number(info.bytes_downloaded || 0);
  const pct = total ? Math.max(0, Math.min(100, Math.round(done * 100 / total))) : 0;
  const busy = phase === "downloading" || phase === "installing";
  const action = phase === "ready"
    ? `<button class="btn-primary" id="update-install">Instalar e reiniciar</button>`
    : info.installable
      ? `<button class="btn-primary" id="update-download" ${busy ? "disabled" : ""}>Baixar e instalar</button>`
      : `<button class="btn-primary" id="update-open">Abrir release no GitHub</button>`;

  el.innerHTML = `<div class="gf-panel update-panel" role="dialog" aria-modal="true" aria-label="Atualização do DigiTracker">
    <div class="gf-head">
      <div><div class="gf-title">⬆ ATUALIZAÇÃO DISPONÍVEL</div>
      <div class="gf-sub">${esc(info.current_version)} → ${esc(info.latest_version)}</div></div>
      <button class="gf-close" id="update-x" ${busy ? "disabled" : ""}>✕</button>
    </div>
    <div class="gf-body">
      <div class="update-notes">${updateNotes(info.notes)}</div>
      ${phase === "downloading" ? `<div class="update-progress">
        <div class="update-progress-bar"><span style="width:${pct}%"></span></div>
        <div>${pct}% · ${formatBytes(done)} de ${formatBytes(total)}</div>
      </div>` : ""}
      ${phase === "ready" ? `<div class="status-msg">✓ Download verificado com SHA-256. Pronto para instalar.</div>` : ""}
      ${phase === "installing" ? `<div class="status-msg">Fechando o aplicativo para instalar…</div>` : ""}
      ${info.error ? `<div class="gf-error">${esc(info.error)}</div>` : ""}
    </div>
    <div class="gf-foot">
      <button class="btn-ghost" id="update-later" ${busy ? "disabled" : ""}>Lembrar depois</button>
      ${action}
    </div>
  </div>`;

  $("#update-x")?.addEventListener("click", () => closeUpdateModal());
  $("#update-later")?.addEventListener("click", async () => {
    await backend.deferUpdate(24);
    closeUpdateModal(true);
  });
  $("#update-download")?.addEventListener("click", iniciarDownloadAtualizacao);
  $("#update-install")?.addEventListener("click", instalarAtualizacao);
  $("#update-open")?.addEventListener("click", () => window.pywebview.api.open_update_release());
}

async function iniciarDownloadAtualizacao() {
  const res = await backend.startUpdateDownload();
  if (!res || !res.ok) {
    const status = await backend.updateStatus().catch(() => ({}));
    return renderUpdateModal({ ...status, error: res?.error || "Falha ao iniciar o download." });
  }
  renderUpdateModal(res);
  clearInterval(updatePoll);
  updatePoll = setInterval(async () => {
    const status = await backend.updateStatus().catch((e) => ({ ok: false, phase: "error", error: String(e) }));
    renderUpdateModal(status);
    if (["ready", "error"].includes(status.phase)) {
      clearInterval(updatePoll);
      updatePoll = null;
    }
  }, 500);
}

async function instalarAtualizacao() {
  const res = await backend.installUpdate();
  if (!res || !res.ok) {
    const status = await backend.updateStatus().catch(() => ({}));
    return renderUpdateModal({ ...status, error: res?.error || "Falha ao iniciar a instalação." });
  }
  renderUpdateModal({ ...(await backend.updateStatus()), phase: "installing" });
}

async function testarOverlay() {
  const btn = $("#overlay-test");
  if (btn) { btn.disabled = true; btn.textContent = "Testando…"; }
  const result = await backend.testOverlay().catch((e) => ({ ok: false, error: String(e) }));
  if (S.SET) {
    S.SET.overlay = result;
    renderSettings();
  }
  toast(result.detected ? `Detectado: ${result.process || result.title}`
    : (result.error || "PCSX2 não detectado."), !result.detected);
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

  el.innerHTML = `<div class="gf-panel" role="dialog" aria-modal="true" aria-label="Importar do GameFAQs">
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
                 aria-label="URL do jogo ou guia no GameFAQs"
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

/* ═══════════════════════════  TROCAR ARTE  ═══════════════════════════
   Busca imagens pelo nome do jogo em várias fontes (SteamGridDB, RAWG, IGDB) ou
   por URL colada, como no Playnite. Cada imagem pode virar CAPA (art.cover:
   overlay + lateral) ou FUNDO (art.background: atrás da lista) — ou os dois. */
const CV_ROLES = [
  { id: "cover", label: "Capa" },
  { id: "background", label: "Fundo" },
  { id: "both", label: "Ambos" },
];
const CV_SOURCES = [
  { id: "steamgriddb", label: "SteamGridDB" },
  { id: "rawg", label: "RAWG" },
  { id: "igdb", label: "IGDB" },
  { id: "url", label: "Colar URL" },
];
const CV_SRC_LABEL = { steamgriddb: "SteamGridDB", rawg: "RAWG", igdb: "IGDB" };

async function openCoverPicker(game) {
  const cfg = await backend.getSourcesConfig().catch(() => ({ ready: {} }));
  const ready = (cfg && cfg.ready) || {};
  const first = ["steamgriddb", "rawg", "igdb"].find((s) => ready[s]) || "steamgriddb";
  S.CV = {
    slug: game.slug, title: game.title, query: game.title,
    source: first, ready, role: "cover", urlValue: "",
    busy: false, error: "", matches: [], chosen: null, covers: [], heroes: [],
  };
  renderCoverPicker();
  if (ready[first]) cvBuscar("");   // só busca se a fonte tem chave
}

function closeCoverPicker() {
  S.CV = null;
  $("#cv-modal")?.remove();
}

function renderCoverPicker() {
  const V = S.CV;
  if (!V) return;
  let el = $("#cv-modal");
  if (!el) {
    el = document.createElement("div");
    el.id = "cv-modal";
    el.className = "gf-backdrop";
    document.body.appendChild(el);
  }

  const outros = (V.matches || []).filter((m) => !V.chosen || m.id !== V.chosen.id);
  const cell = (c, cls) => `
    <button class="cv-cell ${cls}" data-url="${esc(c.url)}" title="Aplicar (${esc(cvRoleLabel())})">
      <img src="${esc(c.thumb)}" alt="" loading="lazy" />
    </button>`;
  const capas = (V.covers || []).map((c) => cell(c, "portrait")).join("");
  const fundos = (V.heroes || []).map((c) => cell(c, "landscape")).join("");

  // corpo por fonte: URL tem um campo próprio; as demais, busca + grades.
  let corpo;
  if (V.source === "url") {
    corpo = `<p class="cv-url-hint">Cole o link direto de uma imagem (Google Imagens, etc.) e aplique no papel escolhido acima.</p>
      <div class="search-box">
        <span style="color:var(--text-low)">🔗</span>
        <input id="cv-url" placeholder="https://…/imagem.jpg" aria-label="URL da imagem"
               autocomplete="off" spellcheck="false" value="${esc(V.urlValue || "")}" />
        <button class="cv-go" id="cv-url-go">Aplicar</button>
      </div>
      ${V.busy ? `<div class="status-msg">⏳ Baixando…</div>` : ""}
      ${V.error ? `<div class="gf-error">${esc(V.error)}</div>` : ""}`;
  } else if (!V.ready[V.source]) {
    corpo = `<div class="cv-nokey">
      <p>Configure a chave do <b>${esc(CV_SRC_LABEL[V.source] || V.source)}</b> nas
         Configurações para buscar por aqui — ou use outra aba acima.</p>
      <button class="btn-primary" id="cv-settings">Abrir Configurações</button>
    </div>`;
  } else {
    corpo = `<div class="search-box">
        <span style="color:var(--text-low)">🔎</span>
        <input id="cv-q" placeholder="Nome do jogo" aria-label="Buscar arte pelo nome do jogo"
               autocomplete="off" spellcheck="false" value="${esc(V.query || "")}" />
        <button class="cv-go" id="cv-go">Buscar</button>
      </div>
      ${V.chosen ? `<p class="cv-match">Casado com <b>${esc(V.chosen.name)}</b>${
        outros.length ? " — ou troque de jogo:" : ""}</p>` : ""}
      ${outros.length ? `<div class="cv-alts">${outros.slice(0, 8).map((m) =>
        `<button class="cv-alt" data-gid="${esc(m.id)}">${esc(m.name)}</button>`).join("")}</div>` : ""}
      ${V.busy ? `<div class="status-msg">⏳ Buscando artes…</div>` : ""}
      ${V.error ? `<div class="gf-error">${esc(V.error)}</div>` : ""}
      ${!V.busy && !V.error && !V.chosen
        ? `<p class="cv-empty">Nenhum jogo encontrado. Ajuste o nome e busque de novo.</p>` : ""}
      ${!V.busy && V.chosen && !V.covers.length && !V.heroes.length
        ? `<p class="cv-empty">Nenhuma arte encontrada nesta fonte para este jogo.</p>` : ""}
      ${capas ? `<p class="cv-grid-label">CAPAS</p><div class="cv-grid">${capas}</div>` : ""}
      ${fundos ? `<p class="cv-grid-label">FUNDOS (wallpaper)</p><div class="cv-grid wide">${fundos}</div>` : ""}`;
  }

  el.innerHTML = `<div class="gf-panel cv-panel" role="dialog" aria-modal="true" aria-label="Trocar arte do jogo">
    <div class="gf-head">
      <div>
        <div class="gf-title">🖼 TROCAR ARTE</div>
        <div class="gf-sub">${esc(V.title || "")}</div>
      </div>
      <button class="gf-close" id="cv-x">✕</button>
    </div>

    <div class="gf-body">
      <div class="cv-sources">
        ${CV_SOURCES.map((s) => `<button class="cv-src ${V.source === s.id ? "on" : ""}${
          s.id !== "url" && !V.ready[s.id] ? " off" : ""}" data-src="${s.id}"
          title="${s.id !== "url" && !V.ready[s.id] ? "sem chave configurada" : ""}">${s.label}</button>`).join("")}
      </div>
      <div class="cv-roles">
        <span class="cv-roles-label">Aplicar como:</span>
        ${CV_ROLES.map((r) => `<button class="cv-role ${V.role === r.id ? "on" : ""}"
          data-role="${r.id}">${r.label}</button>`).join("")}
      </div>
      ${corpo}
    </div>

    <div class="gf-foot">
      <button class="btn-ghost" id="cv-clear">↺ Remover ${esc(cvRoleLabel().toLowerCase())}</button>
      <span></span>
    </div>
  </div>`;

  $("#cv-x").onclick = closeCoverPicker;
  $("#cv-settings")?.addEventListener("click", () => { closeCoverPicker(); enterSettings(); });
  $("#cv-clear")?.addEventListener("click", cvLimpar);
  el.querySelectorAll(".cv-src").forEach((b) =>
    b.onclick = () => cvTrocarFonte(b.dataset.src));
  el.querySelectorAll(".cv-role").forEach((b) =>
    b.onclick = () => { V.role = b.dataset.role; renderCoverPicker(); });
  const go = $("#cv-go");
  if (go) go.onclick = () => cvBuscar(($("#cv-q")?.value || "").trim());
  const q = $("#cv-q");
  if (q) q.onkeydown = (e) => { if (e.key === "Enter") cvBuscar((q.value || "").trim()); };
  const urlGo = $("#cv-url-go");
  if (urlGo) urlGo.onclick = () => cvAplicar(($("#cv-url")?.value || "").trim());
  const urlIn = $("#cv-url");
  if (urlIn) {
    urlIn.oninput = () => { V.urlValue = urlIn.value; };
    urlIn.onkeydown = (e) => { if (e.key === "Enter") cvAplicar((urlIn.value || "").trim()); };
  }
  el.querySelectorAll(".cv-alt").forEach((b) =>
    b.onclick = () => cvTrocarJogo(b.dataset.gid));
  el.querySelectorAll(".cv-cell").forEach((b) =>
    b.onclick = () => cvAplicar(b.dataset.url));
}

function cvRoleLabel() {
  return (CV_ROLES.find((r) => r.id === S.CV?.role) || CV_ROLES[0]).label;
}

function cvTrocarFonte(source) {
  const V = S.CV;
  if (!V || V.source === source) return;
  V.source = source; V.error = "";
  V.matches = []; V.chosen = null; V.covers = []; V.heroes = [];
  renderCoverPicker();
  if (source !== "url" && V.ready[source]) cvBuscar("");
}

async function cvBuscar(query) {
  const V = S.CV;
  if (!V) return;
  V.busy = true; V.error = ""; if (query) V.query = query;
  renderCoverPicker();
  try {
    const res = await backend.coversSearch(V.slug, query || "", V.source);
    V.busy = false;
    if (!res.ok) {
      V.error = res.error || "Não consegui buscar artes.";
    } else {
      V.matches = res.matches || [];
      V.chosen = res.chosen || null;
      V.covers = res.covers || [];
      V.heroes = res.heroes || [];
    }
  } catch (e) {
    V.busy = false; V.error = "Erro: " + e;
  }
  renderCoverPicker();
}

async function cvTrocarJogo(gameId) {
  const V = S.CV;
  if (!V) return;
  V.busy = true; V.error = "";
  V.chosen = (V.matches || []).find((m) => String(m.id) === String(gameId)) || V.chosen;
  renderCoverPicker();
  try {
    const res = await backend.coversFor(gameId, V.source);
    V.busy = false;
    if (!res.ok) V.error = res.error || "Não consegui buscar as artes.";
    else { V.covers = res.covers || []; V.heroes = res.heroes || []; }
  } catch (e) {
    V.busy = false; V.error = "Erro: " + e;
  }
  renderCoverPicker();
}

async function cvAplicar(url) {
  const V = S.CV;
  if (!V) return;
  url = (url || "").trim();
  if (!url) return toast("Cole uma URL de imagem.", true);
  const role = V.role;
  V.busy = true; V.error = ""; renderCoverPicker();
  const res = await backend.setGameCover(V.slug, url, role).catch((e) => ({ ok: false, error: "" + e }));
  if (!res || !res.ok) {
    V.busy = false; V.error = (res && res.error) || "Não consegui aplicar a arte.";
    renderCoverPicker();
    return;
  }
  closeCoverPicker();
  toast(role === "both" ? "Capa e fundo atualizados." : role === "background" ? "Fundo atualizado." : "Capa atualizada.");
  await renderDashboard({ force: true });
}

async function cvLimpar() {
  const V = S.CV;
  if (!V) return;
  const role = V.role;
  const res = await backend.clearGameCover(V.slug, role).catch((e) => ({ ok: false, error: "" + e }));
  if (!res || !res.ok) return toast(res && res.error ? res.error : "Não consegui remover.", true);
  closeCoverPicker();
  toast("Voltou à arte padrão da RA.");
  await renderDashboard({ force: true });
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

  el.innerHTML = `<div class="gf-panel" role="dialog" aria-modal="true" aria-label="Refinar guia com IA">
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
        <label for="ai-key">API key ${atual.has_key ? "(já salva — deixe em branco para manter)" : ""}</label>
        <input id="ai-key" type="password" autocomplete="off" spellcheck="false"
               placeholder="${atual.has_key ? "••••••••••••" : "cole a chave aqui"}" />
        <p class="hint">Fica só em <code>config/secrets.json</code>, nesta máquina. Obter em
          <span class="ai-link">${esc(atual.key_url || "")}</span></p>
      </div>
      <div class="field">
        <label for="ai-model">Modelo (opcional)</label>
        <input id="ai-model" autocomplete="off" spellcheck="false"
               value="${esc(cfg.model || "")}" placeholder="${esc(atual.default_model)}" />
      </div>
      ${atual.needs_base_url ? `
      <div class="field">
        <label for="ai-base">Endpoint (para OpenRouter, Ollama, LM Studio…)</label>
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
  root.innerHTML = `<div class="view wizard-view wizard-search-view">
    ${wizHeadHTML(1)}
    <div class="wiz-body">
      <section class="wizard-search-stage">
        <div class="wizard-intro">
          <span>RETROACHIEVEMENTS</span>
          <h1>Encontre seu próximo jogo</h1>
          <p>Pesquise pelo título. Conquistas, progresso e arte serão preparados automaticamente para a sua biblioteca.</p>
        </div>
        <div class="wizard-search-panel">
          <label for="wiz-q">BUSCAR NO CATÁLOGO</label>
          <div class="search-box">
            <span aria-hidden="true">⌕</span>
            <input id="wiz-q" placeholder="Digite o nome do jogo…" autocomplete="off" spellcheck="false" />
          </div>
          <div class="wizard-source-notes"><span>✓ Progresso sincronizado</span><span>✓ Guia original preservado</span><span>✓ Arte enriquecida em segundo plano</span></div>
        </div>
      </section>
      <div id="wiz-results"></div>
    </div>
  </div>`;
  $("#wiz-back").onclick = enterDashboard;
  const input = $("#wiz-q");
  input.focus({ preventScroll: true });
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
  $("#btn-compact")?.addEventListener("click", () => toggleCompact());
  $("#btn-library")?.addEventListener("click", () => {
    const app = document.getElementById("app");
    const open = app.classList.toggle("library-open");
    $("#btn-library")?.setAttribute("aria-expanded", String(open));
  });
  $("#btn-exit-demo")?.addEventListener("click", exitDemo);
  $("#btn-settings")?.addEventListener("click", () => {
    if (S.view === "settings") return enterDashboard();
    enterSettings();
  });
  bindAtalhos();
}

function closeLibraryDrawer() {
  document.getElementById("app").classList.remove("library-open");
  $("#btn-library")?.setAttribute("aria-expanded", "false");
}

/* ─────────────────────────  ATALHOS DE TECLADO  ─────────────────────────
   O rodapé anuncia essas teclas, então elas precisam existir. Também é a
   navegação por teclado que o app não tinha. */
const ABAS = ["overview", "walk", "mastery", "tips"];

function visibleFocusables() {
  return [...document.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex='-1'])")]
    .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; });
}

function spatialMove(direction) {
  const items = visibleFocusables();
  if (!items.length) return;
  const current = items.includes(document.activeElement) ? document.activeElement : null;
  if (!current) { items[0].focus(); return; }
  const from = current.getBoundingClientRect();
  const fx = from.left + from.width / 2, fy = from.top + from.height / 2;
  let best = null, score = Infinity;
  for (const item of items) {
    if (item === current) continue;
    const r = item.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    const dx = x - fx, dy = y - fy;
    if ((direction === "left" && dx >= -2) || (direction === "right" && dx <= 2)
      || (direction === "up" && dy >= -2) || (direction === "down" && dy <= 2)) continue;
    const primary = ["left", "right"].includes(direction) ? Math.abs(dx) : Math.abs(dy);
    const secondary = ["left", "right"].includes(direction) ? Math.abs(dy) : Math.abs(dx);
    const candidate = primary + secondary * 2.25;
    if (candidate < score) { score = candidate; best = item; }
  }
  if (best) { best.focus({ preventScroll: true }); best.scrollIntoView({ block: "nearest", inline: "nearest" }); }
}

function cyclePanelTab(dir) {
  if (S.view !== "dashboard" || S.compact) return;
  const i = ABAS.indexOf(S.tab);
  S.tab = ABAS[(i + dir + ABAS.length) % ABAS.length];
  renderDashboard({ force: true }).then(() => $(".ptab.active")?.focus());
}

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
      if ($("#cv-modal")) return closeCoverPicker();
      if ($("#ai-modal")) return $("#ai-modal").remove();
      if ($("#update-modal")) return closeUpdateModal();
      if (document.getElementById("app").classList.contains("library-open")) return closeLibraryDrawer();
      if (S.view === "settings") return leaveSettings();
      if (S.view !== "dashboard") return enterDashboard();
      if (S.compact) return toggleCompacto();
      return;
    }

    if (e.key === "c" || e.key === "C") { e.preventDefault(); return toggleCompacto(); }

    if (S.view !== "dashboard") return;

    if (["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(e.key)) {
      e.preventDefault(); return spatialMove(e.key.replace("Arrow", "").toLowerCase());
    }
    if (e.key === "[" || e.key === "]") { e.preventDefault(); return cyclePanelTab(e.key === "]" ? 1 : -1); }
  });
}

function startGamepadNavigation() {
  if (S.gamepadLoop) return;
  const pulse = (key, active, fn) => {
    const now = performance.now(), state = S.gamepadLast[key] || { active: false, at: 0 };
    if (active && (!state.active || now - state.at > 230)) { fn(); state.at = now; }
    state.active = active; S.gamepadLast[key] = state;
  };
  const frame = () => {
    const pad = [...(navigator.getGamepads?.() || [])].find(Boolean);
    if (pad) {
      const pressed = (i) => !!pad.buttons[i]?.pressed;
      pulse("up", pressed(12) || pad.axes[1] < -.65, () => spatialMove("up"));
      pulse("down", pressed(13) || pad.axes[1] > .65, () => spatialMove("down"));
      pulse("left", pressed(14) || pad.axes[0] < -.65, () => spatialMove("left"));
      pulse("right", pressed(15) || pad.axes[0] > .65, () => spatialMove("right"));
      pulse("accept", pressed(0), () => document.activeElement?.click?.());
      pulse("back", pressed(1), () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
      pulse("lb", pressed(4), () => cyclePanelTab(-1));
      pulse("rb", pressed(5), () => cyclePanelTab(1));
      pulse("compact", pressed(9), toggleCompacto);
    }
    S.gamepadLoop = requestAnimationFrame(frame);
  };
  S.gamepadLoop = requestAnimationFrame(frame);
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
  startGamepadNavigation();
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
    await backend.confirmUpdateBoot().catch(() => null);
    if (st.auto_import !== undefined) S.autoImport = st.auto_import;
    if (st.auto_overlay !== undefined) S.autoOverlay = st.auto_overlay;
    if (st.auto_check_updates !== undefined) S.autoCheckUpdates = st.auto_check_updates;
    if (st.version) S.version = st.version;
    if (st.ai_ready !== undefined) S.aiReady = st.ai_ready;
    if (st.ai_provider_label !== undefined) S.aiProviderLabel = st.ai_provider_label;
    if (st.ai_model !== undefined) S.aiModel = st.ai_model;
    if (st.smart_guide_auto !== undefined) S.smartGuideAuto = st.smart_guide_auto;
    if (st.smart_guide_consent !== undefined) S.smartGuideConsent = st.smart_guide_consent;
    if (st.guide_density) S.guideDensity = st.guide_density;
    if (st.ui_scale) S.uiScale = st.ui_scale;
    if (st.reduced_motion !== undefined) S.reducedMotion = st.reduced_motion;
    applyExperience();
    if (S.mode === "real") S.tipsAI = await backend.gameTipsAIStatus().catch(() => null);
    if (st.overlay_exit_fullscreen !== undefined) S.overlayExitFullscreen = st.overlay_exit_fullscreen;
    if (st.overlay_second_screen !== undefined) S.overlaySecondScreen = st.overlay_second_screen;
    if (st.overlay_fit_emulator !== undefined) S.overlayFitEmulator = st.overlay_fit_emulator;
    if (S.mode === "real" && !st.configured) renderSetup();
    else enterDashboard();
    if (S.mode === "real") setTimeout(() => procurarAtualizacao(false), 1200);
    if (S.tipsAI?.phase === "running") setTimeout(acompanharDicasIa, 100);
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
    modes: g.modes, mastery: g.mastery, icon: g.icon || "", art: g.art || {},
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
    genre: "RPG", year: "2005", players: "1–4 jogadores",
    art: { background: "/ui/assets/demo-digital-world-hero.png", title: "/ui/assets/demo-digital-world-hero.png", box: "/ui/assets/demo-digital-world-hero.png" },
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
    smart_guide: {
      status: { phase: "ready", message: "Fonte original preservada · revisão local pronta" },
      current: { title: "Rota essencial · Digimon World 4", summary: "Objetivos e alertas organizados sem aplicar regras fixas de franquia.", provider: "local", chapters: [
        { id: "demo-c1", title: "Preparação antes da rota", objective: "Evitar perda de progresso e preparar o equipamento.", blocks: [
          { id: "demo-b1", type: "warning", title: "Salve antes de entrar", text: "O Save Keeper fica no HomeServer. Confirme o salvamento antes de iniciar uma dungeon.", items: [], rows: [], source_refs: [{section:1,block:4,page:0}], estimated_minutes: 1 },
          { id: "demo-b2", type: "checklist", title: "Preparação rápida", text: "", items: [{id:"i1",text:"Escolha uma arma de ataque à distância."},{id:"i2",text:"Revise itens de cura e MP."}], rows: [], source_refs: [{section:1,block:3,page:0}], estimated_minutes: 3 },
        ]},
        { id: "demo-c2", title: "Goblin Pass e encontro principal", objective: "Cruzar as pontes e concluir o desafio da área.", blocks: [
          { id: "demo-b3", type: "objective", title: "Atravesse Goblin Pass", text: "Na segunda ponte, vire à esquerda e siga ao sul.", items: [], rows: [], source_refs: [{section:2,block:3,page:0}], estimated_minutes: 12 },
          { id: "demo-b4", type: "challenge", title: "Blossomon", text: "Mantenha distância máxima e use ataques Shot para evitar contato.", items: [], rows: [], source_refs: [{section:2,block:5,page:0}], estimated_minutes: 8 },
          { id: "demo-b5", type: "missable", title: "Condição de sessão", text: "Algumas conquistas citadas pela fonte exigem uma sessão solo.", items: [], rows: [], source_refs: [{section:3,block:2,page:0}], estimated_minutes: 0 },
        ]},
      ], visual_suggestions: [{id:"demo-v1",type:"route",chapter_id:"demo-c2",title:"Mapa esquemático de Goblin Pass",reason:"A sequência de pontes e a conversão ao sul ficam mais claras visualmente.",query:"Goblin Pass Digimon World 4 map"}] },
      progress: { completed: ["demo-b1"], favorites: ["demo-b5"], revealed_spoilers: [], notes: {"demo-b3":"Fazer o farm antes do chefe."}, checkpoint: "demo-b1", history: [], session_minutes: 30 },
      effective_progress: { completed: ["demo-b1"], favorites: ["demo-b5"], revealed_spoilers: [], notes: {"demo-b3":"Fazer o farm antes do chefe."}, checkpoint: "demo-b1", history: [], session_minutes: 30 },
      next_objective: { chapter_id:"demo-c1", chapter:"Preparação antes da rota", block_id:"demo-b2", type:"checklist", title:"Preparação rápida", text:"Escolha uma arma de ataque à distância." },
      revisions: [{revision_id:"demo-r1",created_at:1770000000,provider:"local",model:"structured-fallback"}], media: [],
    },
  },
  {
    slug: "digimon_world_2", title: "Digimon World 2", platform: "PlayStation", accent: "#F5C518",
    art: { box: "/ui/assets/demo-digital-dungeon-cover.png" },
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
    art: { box: "/ui/assets/demo-digital-card-cover.png" },
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
