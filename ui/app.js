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

const corDe = (g) => g?.palette?.primary || (g && g.art?.box && CORES_DA_CAPA.get(g.art.box)) || (g && g.accent) || COR_PADRAO;

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
  tab: "journey",        // jornada | achievements | atlas
  achievementFilter: "all",
  onTop: true,
  compact: false,        // modo mini-overlay (progresso de conquistas)
  compactState: "hidden", // hidden | minimal | expanded | both
  compactTab: "achievements", // achievements | guide (por jogo/HUD)
  compactEditing: false,
  compactSlots: 3,
  compactRowObserver: null,
  compactCfg: null,      // tamanho/conteúdo/fundo do overlay
  compactPassive: false, // só fica true após a hotkey nativa ser confirmada
  compactNativeError: "",
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
  guideMode: "compact", // compatibilidade com estados antigos
  guideReader: false,
  guideChapter: 0,
  guideQuery: "",
  guideFilter: "all",
  guideAtlas: { systemId: "", nodeId: "", group: "all", tag: "all", availability: "all", spoilers: false, search: "", mode: "focus", zoom: 1, panX: 24, panY: 24 },
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
  sourceManager: null,
  hall: null,
  hallPlatform: "all",
  shownOverlayCelebrations: new Set(),
  dashboardGame: null,
};

const root = document.getElementById("root");
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------------------- camada de dados ---------------------------- */
const hasBackend = () => typeof window.pywebview !== "undefined" && window.pywebview.api;

async function appCall(method, ...args) {
  if (!hasBackend() || S.mode === "demo") return { ok: false, error: "Disponível no aplicativo conectado." };
  try { return await window.pywebview.api[method](...args); }
  catch (error) { return { ok: false, error: String(error) }; }
}

// Every modal lives in the same fixed layer, including dialogs added to body.
new MutationObserver((mutations) => {
  for (const change of mutations) for (const modal of change.addedNodes) {
    if (!(modal instanceof HTMLElement) || !modal.matches(".modal-bg")) continue;
    const previous = document.activeElement;
    modal.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault(); event.stopImmediatePropagation();
        const close = modal.querySelector('[id$="-close"], [id$="-cancel"], [data-modal-close]');
        if (close) close.click(); else modal.remove();
      } else if (event.key === "Tab") {
        const focusable = [...modal.querySelectorAll('button,input,select,textarea,a[href],[tabindex="0"]')].filter((el) => !el.disabled && el.getClientRects().length);
        if (!focusable.length) return event.preventDefault();
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    requestAnimationFrame(() => modal.querySelector('input,button,select,textarea')?.focus());
    const removal = new MutationObserver(() => {
      if (!modal.isConnected) { removal.disconnect(); if (previous?.isConnected) previous.focus(); }
    });
    removal.observe(document.body, { childList: true, subtree: true });
  }
}).observe(document.body, { childList: true, subtree: true });

const backend = {
  async appState() {
    if (S.mode === "demo") return {
      configured: true, version: "0.8.3", auto_import: true,
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
  async setActiveGame(slug) {
    if (S.mode === "demo") return { ok: true, slug };
    return window.pywebview.api.set_active_game(slug || "");
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
  async masteryHall() {
    if (S.mode === "demo") return { ok: true, mastery: S.library.filter((g) => g.mastery?.complete), softcore: [] };
    return window.pywebview.api.get_mastery_hall();
  },
  async completionEvents(slug = "", pendingOnly = true) {
    if (S.mode === "demo") return { ok: true, events: [] };
    return window.pywebview.api.get_completion_events(slug, pendingOnly);
  },
  async confirmCompletionEvent(slug, id) {
    if (S.mode === "demo") return { ok: true };
    return window.pywebview.api.confirm_completion_event(slug, id);
  },
  async reopenCompletionEvent(slug, id = "") {
    if (S.mode === "demo") return { ok: false };
    return window.pywebview.api.reopen_completion_event(slug, id);
  },
  async setCompact(v) {
    if (S.mode === "demo") return { ok: true, compact: !!v };
    return window.pywebview.api.set_compact(!!v);
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
  async walkthroughSources(slug) {
    if (S.mode === "demo") return { ok: true, sources: [] };
    return window.pywebview.api.list_walkthrough_sources(slug);
  },
  async walkthroughSource(slug, sourceId) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.get_walkthrough_source(slug, sourceId);
  },
  async addWalkthroughPdf(slug, data, filename) {
    return window.pywebview.api.add_walkthrough_pdf(slug, data, filename);
  },
  async addWalkthroughGameFaqs(slug, url) {
    return window.pywebview.api.add_walkthrough_gamefaqs(slug, url);
  },
  async addWalkthroughText(slug, title, value) {
    return window.pywebview.api.add_walkthrough_text(slug, title, value);
  },
  async updateWalkthroughSource(slug, sourceId, title = null, enabled = null) {
    return window.pywebview.api.update_walkthrough_source(slug, sourceId, title, enabled);
  },
  async removeWalkthroughSource(slug, sourceId) {
    return window.pywebview.api.remove_walkthrough_source(slug, sourceId);
  },
  async startWalkthroughMerge(slug, sourceIds) {
    return window.pywebview.api.start_walkthrough_merge(slug, sourceIds);
  },
  async walkthroughMergeStatus(slug) {
    return window.pywebview.api.get_walkthrough_merge_status(slug);
  },
  async resolveWalkthroughConflict(slug, conflictId, choice) {
    return window.pywebview.api.resolve_walkthrough_conflict(slug, conflictId, choice);
  },
  async publishWalkthroughMerge(slug) {
    return window.pywebview.api.publish_walkthrough_merge(slug);
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
  async guideSystems(slug) {
    if (S.mode === "demo") return { ok: true, systems: [], approved: [], suggested: [], state: {}, media: [] };
    return window.pywebview.api.get_guide_systems(slug);
  },
  async saveGuideSystem(slug, system) {
    if (S.mode === "demo") {
      const systems = S.dashboardGame?.smart_guide?.current?.systems || [];
      const index = systems.findIndex((item) => item.id === system.id);
      const saved = { ...system, id: system.id || `sys-demo-${Date.now()}` };
      if (index >= 0) systems[index] = saved; else systems.push(saved);
      return { ok: true, system: saved, revision: { revision_id: `demo-${Date.now()}` } };
    }
    return window.pywebview.api.save_guide_system(slug, system);
  },
  async createGuideSystemPdf(slug, title, data, filename) {
    return window.pywebview.api.create_guide_system_from_pdf(slug, title, data, filename);
  },
  async createGuideSystemGameFaqs(slug, title, url) {
    return window.pywebview.api.create_guide_system_from_gamefaqs(slug, title, url);
  },
  async guideSystemSource(slug, systemId) {
    return window.pywebview.api.get_guide_system_source(slug, systemId);
  },
  async atlasSourceReview(slug, sourceId) {
    return window.pywebview.api.get_atlas_source_review(slug, sourceId);
  },
  async startAtlasSource(slug, sourceId, selection) {
    return window.pywebview.api.start_atlas_source(slug, sourceId, selection || {});
  },
  async replaceGuideSystemSource(slug, systemId, source) {
    return window.pywebview.api.replace_guide_system_source(slug, systemId, source);
  },
  async deleteGuideSystem(slug, systemId) {
    if (S.mode === "demo") {
      const current = S.dashboardGame?.smart_guide?.current;
      const systems = current?.systems || [];
      if (current) current.systems = systems.filter((item) => item.id !== systemId);
      return { ok: true, systems: current?.systems || [] };
    }
    return window.pywebview.api.delete_guide_system(slug, systemId);
  },
  async setGuideSystemGoal(slug, systemId, nodeId) {
    if (S.mode === "demo") {
      const state = S.dashboardGame.smart_guide.system_state;
      state.active_system = nodeId ? systemId : "";
      if (nodeId) state.goals[systemId] = nodeId; else delete state.goals[systemId];
      return { ok: true, state };
    }
    return window.pywebview.api.set_guide_system_goal(slug, systemId, nodeId);
  },
  async updateGuideRequirement(slug, systemId, edgeId, requirementId, completed) {
    if (S.mode === "demo") {
      const list = new Set(S.dashboardGame.smart_guide.system_state.completed_requirements || []);
      if (completed) list.add(requirementId); else list.delete(requirementId);
      S.dashboardGame.smart_guide.system_state.completed_requirements = [...list];
      return { ok: true, state: S.dashboardGame.smart_guide.system_state };
    }
    return window.pywebview.api.update_guide_requirement(slug, systemId, edgeId, requirementId, !!completed);
  },
  async searchGuideSystemMedia(slug, systemId, nodeId, query, page = 0, sourceId = '') {
    if (S.mode === "demo") return { ok: true, results: [], query, provider: "demo" };
    return window.pywebview.api.search_guide_system_media(slug, systemId, nodeId, query || "", page, sourceId);
  },
  async setGuideSystemMedia(slug, systemId, nodeId, mediaId, sourceId = '') {
    if (S.mode === "demo") {
      S.dashboardGame.smart_guide.system_state.node_media[`${systemId}:${nodeId}`] = mediaId;
      return { ok: true, state: S.dashboardGame.smart_guide.system_state };
    }
    return window.pywebview.api.set_guide_system_media(slug, systemId, nodeId, mediaId || "", sourceId);
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
  async getAiUsageStatus() {
    if (S.mode === "demo") return { ok: true, provider: "demo", providers: {} };
    return window.pywebview.api.get_ai_usage_status();
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
  async applyWebArt(slug, candidate, role) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.apply_web_art(slug, candidate, role || "cover");
  },
  async setGamePalette(slug, primary, secondary) {
    if (S.mode === "demo") return { ok: true, palette: { primary, secondary, manual: true } };
    return window.pywebview.api.set_game_palette(slug, primary, secondary || "");
  },
  async recalculateGamePalette(slug) {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real." };
    return window.pywebview.api.recalculate_game_palette(slug);
  },
  async webImageSearch(slug, query, page = 0, safe = "moderate", role = "cover") {
    if (S.mode === "demo") return { ok: false, error: "Disponível só no app real.", results: [] };
    return window.pywebview.api.search_web_images(slug, query || "", page, safe, role, "google");
  },
  async openImageSource(url) {
    if (S.mode === "demo") return { ok: false };
    return window.pywebview.api.open_image_source(url || "");
  },
  async openWebImageSearch(slug, query, safe = "moderate", role = "cover") {
    if (S.mode === "demo") return { ok: false };
    return window.pywebview.api.open_web_image_search(slug, query || "", safe, role);
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
    if (S.mode === "demo") return S.compactCfg || { ok: true, width: 320, height: 110, expanded_width: 420, expanded_height: 300, last: 3, next: 0, size_mode: "auto", tab: "achievements", view: "minimal", corner: "auto", background_mode: "background", opacity: 100, hotkey: "ctrl+alt+g", edit_hotkey: "ctrl+alt+e", editing: false, variant: "minimal", auto_expand: false, auto_collapse_seconds: 0 };
    return window.pywebview.api.get_compact_config();
  },
  async setCompactConfig(cfg) {
    if (S.mode === "demo") return { ok: true, ...cfg };
    return window.pywebview.api.set_compact_config(
      cfg.width, cfg.height, cfg.last, cfg.next, cfg.size_mode, null, cfg.tab,
      cfg.corner, 100, cfg.hotkey, cfg.edit_hotkey, cfg.auto_expand, cfg.auto_collapse_seconds,
      cfg.expanded_width, cfg.expanded_height, cfg.background_mode, cfg.view);
  },
  async setCompactState(state) {
    if (S.mode === "demo") return { ok: true, compact: true, state };
    return window.pywebview.api.set_compact_state(state, true);
  },
  async setCompactTab(slug, tab) {
    if (S.mode === "demo") return { ok: true, tab };
    return window.pywebview.api.set_compact_tab(slug, tab);
  },
  async setOverlayEditing(value) {
    if (S.mode === "demo") return { ok: true, editing: !!value };
    return window.pywebview.api.set_overlay_edit_mode(value, false);
  },
  touchOverlayEditing() {
    if (S.mode === "demo" || !hasBackend()) return;
    const now = Date.now();
    if (now - (backend._lastEditTouch || 0) < 700) return;
    backend._lastEditTouch = now;
    try { window.pywebview.api.touch_overlay_edit_mode(); } catch (_) { /* fechando */ }
  },
  resizeCompact(width, height) {
    if (S.mode === "demo" || !hasBackend()) return;
    try { window.pywebview.api.resize_compact_window(width, height); } catch (_) { /* fechando */ }
  },
  saveOverlayGeometry(variant, x, y, width, height) {
    if (S.mode === "demo" || !hasBackend()) return;
    try { window.pywebview.api.save_overlay_geometry(variant, x, y, width, height); } catch (_) { /* fechando */ }
  },
  moveWindow(x, y) {
    if (S.mode === "demo" || !hasBackend()) return;
    try { window.pywebview.api.move_window(x, y); } catch (_) { /* janela indo embora */ }
  },
};

/* O backend entra/sai do modo compacto sozinho quando detecta um emulador —
   ele chama esta função para a interface acompanhar na hora. */
window.onOverlayChanged = async (compact, state, editing) => {
  S.compact = !!compact;
  S.compactState = compact ? (state || "minimal") : "hidden";
  S.compactEditing = !!editing;
  await refreshCompactNativeStatus();
  syncInputMode();
  const btn = document.getElementById("btn-compact");
  if (btn) {
    btn.classList.toggle("active", S.compact);
    btn.title = S.compact ? "Sair do modo compacto" : "Modo compacto (overlay de progresso)";
  }
  if (S.view === "dashboard") await renderDashboard({ force: true });
};

async function refreshCompactNativeStatus() {
  if (!S.compact) {
    S.compactPassive = false;
    S.compactNativeError = "";
    return;
  }
  try {
    const status = await backend.overlayStatus();
    S.compactPassive = ["passive", "interactive"].includes(status?.native_input_mode) && !!status?.hotkey_registered;
    S.compactEditing = !!status?.editing;
    S.compactNativeError = status?.hotkey_error || status?.compatibility_error || "";
  } catch (e) {
    S.compactPassive = false;
    S.compactNativeError = String(e || "Modo passivo indisponível.");
  }
}

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
  // Um jogo removido ou uma instalacao migrada pode deixar um slug antigo no
  // localStorage. Nao abra o dashboard vazio quando ainda ha jogos validos.
  if ((!S.activeSlug || !S.library.some((game) => game.slug === S.activeSlug)) && S.library.length) {
    S.activeSlug = (S.library.find((game) => !isMastered(game)) || S.library[0]).slug;
  }
  if (S.activeSlug) await backend.setActiveGame(S.activeSlug).catch(() => null);
  S.compactCfg = await backend.getCompactConfig().catch(() => null);
  if (S.compactCfg?.tab) S.compactTab = S.compactCfg.tab;
  if (S.compact && S.compactCfg?.variant) S.compactState = S.compactCfg.variant;
  S.compactEditing = !!S.compactCfg?.editing;
  await carregarCores(S.library);       // a cor de cada jogo vem da capa dele
  await renderDashboard({ force: true });
  startPolling();
  if (!S.experienceOpened && !S.compact && typeof renderExperience === "function") {
    S.experienceOpened = true;
    await renderExperience("home");
  }
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
  if (S.view !== "dashboard") return;
  const requestedSlug = S.activeSlug;
  const game = S.activeSlug ? await backend.game(S.activeSlug) : null;
  if (S.view !== "dashboard" || requestedSlug !== S.activeSlug) return;
  S.dashboardGame = game;
  const sig = JSON.stringify([S.mode, S.compact, S.compactState, S.compactTab, S.compactEditing, S.compactSlots, S.tab, S.activeSlug, S.library, game]);
  if (!force && sig === S.sig) return;
  S.sig = sig;

  // Só faz sentido preservar o scroll se continuamos na MESMA tela; ao trocar
  // de jogo ou de aba o conteúdo é outro e deve começar do topo.
  const screenKey = `${S.compact}|${S.compactState}|${S.compactTab}|${S.tab}|${S.activeSlug}`;
  const sameScreen = screenKey === S.screenKey;
  S.screenKey = screenKey;

  const scroll = captureScroll();
  document.getElementById("app").classList.toggle("compact", S.compact);
  document.documentElement.classList.toggle("compact-window", S.compact);
  document.body.classList.toggle("compact-window", S.compact);
  $("#btn-library").hidden = !!S.compact;
  S.compactRowObserver?.disconnect?.();
  S.compactRowObserver = null;
  if (S.compact) {
    root.innerHTML = compactHTML(game);
    bindCompact();
  } else {
    root.innerHTML = xpShell(mainHTML(game), true);
    $("#btn-library").hidden = true;
    bindSidebar();
  }
  // Trocou de jogo ou de aba? Começa do topo. Só faz sentido preservar a
  // rolagem quando o conteúdo é o mesmo (redesenho do polling de 5s).
  if (sameScreen) restoreScroll(scroll);
  else root.querySelectorAll("[data-scroll]").forEach((el) => { el.scrollTop = 0; });
  $("#sync-tag").textContent = S.mode === "demo" ? "DEMO" : "● SYNC 30s";
  handlePendingCelebrations(game);
}

function handlePendingCelebrations(game) {
  if (!game) return;
  const events = ((game.completion_events || {}).events || []).filter((e) => e.pending);
  if (!events.length) return;
  const event = [...events].reverse().find((e) => e.kind === "mastery") || events[events.length - 1];
  if (S.compact) {
    if (S.shownOverlayCelebrations.has(event.id)) return;
    S.shownOverlayCelebrations.add(event.id);
    const notice = document.createElement("div");
    notice.className = `completion-overlay-toast ${event.kind}`;
    notice.innerHTML = `<span>${event.kind === "mastery" ? "★" : "100%"}</span><div><small>${event.kind === "mastery" ? "MASTERY CONQUISTADA" : "100% CONCLUÍDO"}</small><b>${esc(game.title)}</b><em>${event.points || 0} pontos${event.playtime?.label ? ` · ${esc(event.playtime.label)}` : ""}</em></div>`;
    root.appendChild(notice);
    setTimeout(() => notice.remove(), 6000);
    return;
  }
  if (!document.querySelector(".completion-modal")) showCompletionCelebration({ ...event, game }, false);
}

function showCompletionCelebration(payload, replay = false) {
  const previousFocus = document.activeElement;
  const game = payload.game || S.library.find((g) => g.slug === payload.slug) || {};
  const kind = payload.kind === "mastery" ? "mastery" : "softcore";
  const playtime = payload.playtime?.label || game.playtime?.label || "";
  const points = payload.points ?? (kind === "mastery" ? game.score?.hardcore : game.score?.earned) ?? 0;
  const count = payload.achievements ?? game.mastery?.total ?? 0;
  const date = payload.date || (kind === "mastery" ? game.completion?.mastery_date : game.completion?.softcore_date) || "";
  const modal = document.createElement("div");
  modal.className = `modal-bg completion-modal ${kind}`;
  modal.style.setProperty("--jogo", corDe(game));
  modal.innerHTML = `<div class="completion-panel" role="dialog" aria-modal="true"><button class="completion-close" aria-label="Fechar">×</button><div class="completion-rays"></div><div class="completion-emblem">${kind === "mastery" ? "★" : "100%"}</div><small>${kind === "mastery" ? "HARDCORE · CONCLUSÃO PERFEITA" : "CONCLUSÃO SOFTCORE"}</small><h2>${kind === "mastery" ? "MASTERY CONQUISTADA" : "100% CONCLUÍDO"}</h2><h3>${esc(game.title || "Jogo concluído")}</h3><div class="completion-stats"><span><b>${count}</b> conquistas</span><span><b>${points}</b> pontos</span>${playtime ? `<span><b>${esc(playtime)}</b> tempo RA</span>` : ""}${date ? `<span><b>${esc(date)}</b> conclusão</span>` : ""}</div><button class="completion-primary">${kind === "mastery" ? "Ver no Hall da Mastery" : "Continuar para a Mastery"}</button><p>As cores desta celebração foram adaptadas à identidade do jogo.</p></div>`;
  document.body.appendChild(modal);
  const onKey = (event) => {
    if (event.key === "Escape") { event.preventDefault(); close(); }
  };
  const close = async () => {
    document.removeEventListener("keydown", onKey);
    modal.remove();
    if (!replay && payload.id && (game.slug || payload.slug)) await backend.confirmCompletionEvent(game.slug || payload.slug, payload.id).catch(() => null);
    previousFocus?.focus?.();
  };
  document.addEventListener("keydown", onKey);
  modal.querySelector(".completion-close").onclick = close;
  modal.querySelector(".completion-primary").onclick = async () => { await close(); if (kind === "mastery") enterHall(); };
  modal.querySelector(".completion-close")?.focus();
}

/* ========================= MODO COMPACTO (OVERLAY) ======================= */
/* HUD passivo: não depende de foco, não exibe arte em tela cheia e só mostra
   detalhes quando o estado expandido é solicitado pela hotkey global. */
function compactBackground(game, mode) {
  const art = game?.art || {};
  if (mode === "solid") return "";
  if (mode === "cover") return art.cover || art.box || art.background || art.ingame || art.title || "";
  if (mode === "title") return art.title || art.background || art.ingame || art.cover || art.box || "";
  return art.background || art.ingame || art.title || art.cover || art.box || "";
}

function compactHTML(game) {
  if (!game) return `<div class="cw cw-empty">Nenhum jogo selecionado.</div>`;
  const { t } = totals(game);
  const mst = game.mastery || {};
  const cor = corDe(game);
  const cfg = S.compactCfg || {};
  const state = ["expanded", "both"].includes(S.compactState) ? S.compactState : "minimal";
  const content = game.compact_tab || S.compactTab || cfg.tab || "achievements";
  S.compactTab = content;
  const capa = game.art?.cover || game.art?.box;
  const fundo = compactBackground(game, cfg.background_mode || "background");
  const objective = compactObjective(game);
  const progress = Math.max(0, Math.min(100, Number(mst.percent || 0)));
  const hotkey = String(cfg.hotkey || "Ctrl+Alt+G").replace(/\+/g, " + ").toUpperCase();
  const summary = `<div class="cw-min-card">
    ${capa ? `<img class="cw-min-cover" src="${esc(capa)}" alt="">` : `<span class="cw-min-cover cw-cover-fallback">D</span>`}
    <div class="cw-min-objective"><span class="cw-kicker">PRÓXIMO OBJETIVO</span>
      <strong>${esc(objective.title || "Continue seu guia")}</strong>
      <small>${objective.text ? esc(objective.text) : `${mst.hardcore || 0} de ${t} conquistas`}</small>
    </div>
    <div class="cw-ring" style="--p:${progress}" aria-label="${progress}%"><span>${progress}%</span></div>
  </div>`;
  const tab = (id, icon, label) => `<button type="button" data-cw-content="${id}" class="${content === id ? "on" : ""}">${icon}<b>${label}</b></button>`;
  const viewControls = () => S.compactEditing ? `<div class="cw-view-switch" aria-label="Visual do overlay">
    <button type="button" data-cw-view="minimal" class="${state === "minimal" ? "on" : ""}" title="Somente resumo" aria-label="Mostrar somente resumo">R</button>
    <button type="button" data-cw-view="expanded" class="${state === "expanded" ? "on" : ""}" title="Somente troféus" aria-label="Mostrar somente troféus">T</button>
    <button type="button" data-cw-view="both" class="${state === "both" ? "on" : ""}" title="Resumo e troféus" aria-label="Mostrar resumo e troféus">2</button>
  </div>` : "";
  const panel = (variant, includeSummary, primary) => {
    const expanded = variant === "expanded";
    const body = expanded ? compactContentHTML(game, content, t) : summary;
    return `<section class="cw cw-${variant}" style="--jogo:${cor}">
      ${fundo ? `<div class="cw-bg" style="background-image:url('${esc(fundo)}')"></div>` : ""}
      <div class="cw-shade"></div>
      <div class="cw-head" title="${esc(game.title)}">
        <div class="cw-title"><span>DIGITRACKER</span>${expanded ? `<strong>${esc(game.title)}</strong>` : ""}</div>
        <i class="cw-live-dot" title="${S.compactEditing ? "Modo de edição" : "Overlay passivo ativo"}"></i>
        ${primary ? viewControls() : ""}
        ${primary && !S.compactPassive ? `<button class="cw-recover" id="cw-recover" title="${esc(S.compactNativeError || "Modo de recuperação: fechar overlay")}" aria-label="Fechar overlay">×</button>` : ""}
      </div>
      ${expanded && includeSummary ? `<div class="cw-summary">${summary}</div>` : ""}
      ${expanded ? `<div class="cw-tabs">${tab("achievements", "♜", "Troféus")}${tab("guide", "♢", "Guia Inteligente")}</div>` : ""}
      <div class="cw-content" data-scroll="compact">${body}</div>
      ${expanded && objective.warning ? `<button type="button" class="cw-alert" id="cw-alert-guide"><span>⚠</span><b>ALERTA</b><em>${esc(objective.warning)}</em><i>›</i></button>` : ""}
      ${state !== "both" && (S.compactEditing || !S.compactPassive) ? `<div class="cw-hotkey">${esc(hotkey)} · mostrar/ocultar&nbsp;&nbsp; CTRL + ALT + E · editar</div>` : ""}
      ${state !== "both" && S.compactEditing ? `<button class="cw-resize" id="cw-resize" aria-label="Redimensionar overlay" title="Arraste para redimensionar"></button>` : ""}
    </section>`;
  };
  if (state === "both") {
    return `<div class="cw-layout-both" style="--jogo:${cor};--cw-min-h:${Math.max(90, Math.min(130, Number(cfg.height || 110)))}px">
      ${panel("minimal", false, true)}
      ${panel("expanded", false, false)}
      ${S.compactEditing || !S.compactPassive ? `<div class="cw-hotkey cw-both-hotkey">${esc(hotkey)} · mostrar/ocultar&nbsp;&nbsp; CTRL + ALT + E · editar</div>` : ""}
      ${S.compactEditing ? `<button class="cw-resize" id="cw-resize" aria-label="Redimensionar overlay" title="Arraste para redimensionar"></button>` : ""}
    </div>`;
  }
  return panel(state, state === "expanded", true);
}

function compactObjective(game) {
  const smart = game.smart_guide || {};
  const next = smart.next_objective || {};
  if (next.title || next.text) {
    const current = smart.current || {};
    const chapter = (current.chapters || []).find((c) => c.id === next.chapter_id) || {};
    const warning = (chapter.blocks || []).find((b) => ["warning", "missable"].includes(b.type)
      && !(smart.effective_progress?.completed || []).includes(b.id));
    return { title: next.title || next.text, text: next.text || "", items: [], warning: warning?.title || warning?.text || "" };
  }
  const nextAchievement = (game.next_ids || []).map((id) => (game.achievements || []).find((a) => a.id === id)).find(Boolean);
  return nextAchievement
    ? { title: nextAchievement.name, text: nextAchievement.desc || "", items: [], warning: "" }
    : { title: "Guia concluído", text: "Nenhum próximo objetivo pendente.", items: [], warning: "" };
}

function compactContentHTML(game, content, total) {
  if (content === "achievements") return cwAchievementsHTML(game, total);
  return cwGuideHTML(game);
}

function cwObjectiveHTML(game) {
  const objective = compactObjective(game);
  const smart = game.smart_guide || {};
  const current = smart.current || {};
  const chapter = (current.chapters || []).find((c) => c.id === smart.next_objective?.chapter_id) || {};
  const block = (chapter.blocks || []).find((b) => b.id === smart.next_objective?.block_id) || {};
  const items = (block.items || []).slice(0, 3);
  const capa = game.art?.cover || game.art?.box;
  return `<div class="cw-detail cw-objective">
    <div class="cw-objective-main">
      ${capa ? `<img class="cw-objective-cover" src="${esc(capa)}" alt="">` : `<span class="cw-objective-cover cw-cover-fallback">D</span>`}
      <div><div class="cw-detail-kicker">PRÓXIMO OBJETIVO</div><h3>${esc(objective.title)}</h3><p>${esc(objective.text)}</p></div>
    </div>
    <i class="cw-objective-progress"><em></em></i>
    <div class="cw-checklist">${items.length ? items.map((item, index) => `<div class="cw-check ${index ? "done" : ""}"><i>${index ? "✓" : ""}</i>${esc(item.text || item)}</div>`).join("") : `<div class="cw-check"><i></i>Continue pelo próximo passo do guia</div>`}</div>
  </div>`;
}

function cwAchievementsHTML(game, total) {
  const cfg = S.compactCfg || {};
  const slots = Math.max(1, Number(S.compactSlots || 3));
  const lastN = Math.min(3, slots, Math.max(0, Number(cfg.last ?? 3)));
  const earned = (game.achievements || []).filter((a) => a.earned && a.date_raw)
    .sort((a, b) => (a.date_raw < b.date_raw ? 1 : a.date_raw > b.date_raw ? -1 : 0));
  const last = earned.slice(0, lastN);
  const nextN = Math.max(0, slots - last.length);
  const next = (game.next_ids || []).map((id) => (game.achievements || []).find((a) => a.id === id))
    .filter(Boolean).slice(0, nextN);
  const rows = (label, list, locked) => list.length ? `<div class="cw-sec"><p class="cw-sec-label">${label}</p>${list.map((a) => cwRow(a, locked)).join("")}</div>` : "";
  return `<div class="cw-detail cw-achievements"><div class="cw-detail-kicker">CONQUISTAS · ${game.score?.earned || 0} PTS</div>
    ${rows("Últimas obtidas", last, false)}${rows("Próximas", next, true)}
    ${!last.length && !next.length ? `<p class="cw-empty-sm">${total ? "Sem conquistas pendentes." : "Sem conquistas registradas."}</p>` : ""}</div>`;
}

function cwGuideHTML(game) {
  const smart = game.smart_guide || {};
  const doc = smart.current || {};
  const chapters = (doc.chapters || []).slice(0, 2);
  if (!chapters.length) return cwObjectiveHTML(game);
  return `<div class="cw-detail"><div class="cw-detail-kicker">GUIA INTELIGENTE</div>
    ${chapters.map((chapter) => `<section class="cw-guide-chapter"><b>${esc(chapter.title || "Capítulo")}</b>${(chapter.blocks || []).filter((b) => b.type !== "text").slice(0, 3).map((b) => `<p>${esc(b.title || b.text || "")}</p>`).join("")}</section>`).join("")}</div>`;
}

function cwRow(a, locked) {
  const badge = a.badge_url
    ? `<div class="cw-badge${locked && !a.earned ? " locked" : ""}" style="background-image:url('${esc(a.badge_url)}')"></div>`
    : `<div class="cw-badge${locked && !a.earned ? " locked" : ""}">${locked && !a.earned ? "🔒" : "🏆"}</div>`;
  return `<div class="cw-row" data-mode="${a.earned ? (a.hardcore ? "hardcore" : "softcore") : "locked"}">
    ${badge}
    <div class="cw-info">
      <p class="cw-name">${esc(a.name)}</p>
      <p class="cw-desc">${a.points ? `${esc(a.points)} pts · ` : ""}${esc(a.desc || "")}</p>
    </div>
  </div>`;
}

function bindCompact() {
  $("#cw-recover")?.addEventListener("click", () => toggleCompact(false));
  root.querySelectorAll("[data-cw-view]").forEach((button) => button.addEventListener("click", async () => {
    if (!S.compactEditing) return;
    const state = button.dataset.cwView;
    const result = await backend.setCompactState(state).catch(() => null);
    if (!result?.ok) return toast(result?.error || "Não foi possível alterar o HUD.", true);
    S.compactState = state;
    if (state !== "minimal") {
      S.compactTab = "achievements";
      await backend.setCompactTab(S.activeSlug, "achievements").catch(() => null);
    }
    if (S.compactCfg) { S.compactCfg.view = state; S.compactCfg.variant = state; }
    backend.touchOverlayEditing();
    await renderDashboard({ force: true });
  }));
  $("#cw-alert-guide")?.addEventListener("click", async () => {
    if (!S.compactEditing) return;
    S.compactTab = "guide";
    await backend.setCompactTab(S.activeSlug, "guide").catch(() => null);
    backend.touchOverlayEditing();
    await renderDashboard({ force: true });
  });
  root.querySelectorAll("[data-cw-content]").forEach((button) => button.addEventListener("click", async () => {
    if (!["expanded", "both"].includes(S.compactState) || !S.compactEditing) return;
    S.compactTab = button.dataset.cwContent;
    await backend.setCompactTab(S.activeSlug, S.compactTab).catch(() => null);
    backend.touchOverlayEditing();
    await renderDashboard({ force: true });
  }));
  if (S.compactEditing) {
    root.querySelectorAll(".cw-head").forEach(makeDraggable);
    makeResizable($("#cw-resize"));
    root.querySelector(".cw")?.addEventListener("pointermove", backend.touchOverlayEditing, { passive: true });
  }
  observeCompactRows();
}

function observeCompactRows() {
  if (!["expanded", "both"].includes(S.compactState) || S.compactTab !== "achievements") return;
  const area = root.querySelector(".cw-expanded .cw-content");
  if (!area || typeof ResizeObserver === "undefined") return;
  const update = () => {
    const labels = 36;
    const slots = Math.max(1, Math.min(10, Math.floor((area.clientHeight - labels - 16) / 36)));
    if (slots !== S.compactSlots) {
      S.compactSlots = slots;
      renderDashboard({ force: true });
    }
  };
  const observer = new ResizeObserver(update);
  S.compactRowObserver = observer;
  observer.observe(area);
  requestAnimationFrame(update);
}

function makeResizable(el) {
  if (!el || !S.compactEditing) return;
  let startX = 0, startY = 0, startW = 0, startH = 0;
  const onMove = (e) => {
    backend.resizeCompact(startW + e.screenX - startX, startH + e.screenY - startY);
    backend.touchOverlayEditing();
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    backend.saveOverlayGeometry(S.compactState, window.screenX, window.screenY, window.innerWidth, window.innerHeight);
  };
  el.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    startX = e.screenX; startY = e.screenY;
    startW = window.innerWidth; startH = window.innerHeight;
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    e.preventDefault();
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
    backend.saveOverlayGeometry(S.compactState, window.screenX, window.screenY, window.innerWidth, window.innerHeight);
  };
  el.addEventListener("mousedown", (e) => {
    // botão esquerdo, e nunca sobre um controle (abas): esses clicam, não arrastam
    if (!S.compactEditing || e.button !== 0 || e.target.closest("button")) return;
    grabX = e.clientX; grabY = e.clientY;
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    backend.touchOverlayEditing();
    e.preventDefault();
  });
}

/* Liga/desliga o modo compacto (usado pelo botão da barra E pelo controle que
   aparece no hover do próprio overlay, já que a barra some no compacto). */
async function toggleCompact(value) {
  S.compact = value !== undefined ? !!value : !S.compact;
  S.compactState = S.compact ? (S.compactCfg?.view || S.compactCfg?.variant || "minimal") : "hidden";
  syncInputMode();
  const btn = document.getElementById("btn-compact");
  if (btn) {
    btn.classList.toggle("active", S.compact);
    btn.title = S.compact ? "Sair do modo compacto" : "Modo compacto (overlay de progresso)";
  }
  if (hasBackend()) await backend.setCompact(S.compact);
  await refreshCompactNativeStatus();
  if (S.compact && S.view !== "dashboard") {
    await enterDashboard();
    return;
  }
  if (S.view === "dashboard") await renderDashboard({ force: true });
}

function sidebarHTML() {
  const query = S.libraryQuery.trim().toLocaleLowerCase("pt-BR");
  const activeLibrary = S.library.filter((game) => !isMastered(game));
  const visible = query
    ? activeLibrary.filter((g) => g.title.toLocaleLowerCase("pt-BR").includes(query))
    : activeLibrary;
  const done = S.library.filter(isMastered);
  const prog = visible;
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
        ${g.playtime?.label ? `<div class="tile-time" title="Tempo registrado pela RetroAchievements">◷ ${esc(g.playtime.label)}</div>` : ""}
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
    ${typeof experienceNav === "function" ? experienceNav() : ""}
    <div class="sidebar-head">
      <div class="sidebar-title-row"><h2>BIBLIOTECA</h2><span class="library-chevron">⌄</span></div>
      <label class="library-search" title="Buscar na biblioteca">
        <span aria-hidden="true">⌕</span>
        <input id="library-search" type="search" placeholder="Buscar jogo" value="${esc(S.libraryQuery)}" aria-label="Buscar jogo na biblioteca">
      </label>
    </div>
    <button class="hall-entry ${S.view === "hall" ? "active" : ""}" id="hall-entry"><span>✦</span><div><b>Hall da Mastery</b><small>${done.length} jogos no espaço VIP</small></div><i>›</i></button>
    <div class="sidebar-list" data-scroll="sidebar">
      ${prog.length ? `<p class="section-label progress">◎ EM PROGRESSO</p>${prog.map(tile).join("")}` : ""}
      <div class="library-empty ${visible.length ? "hidden" : ""}" id="library-filter-empty"><p>${query ? "Nenhum jogo em progresso encontrado." : (done.length ? "Todos os jogos estão no Hall da Mastery." : "Nenhum jogo ainda.")}</p>${!query && done.length ? `<button class="btn-ghost" id="library-open-hall">Abrir Hall da Mastery</button>` : ""}</div>
    </div>
    <div class="sidebar-foot">
      <button class="add-btn" id="btn-add"><span class="foot-icon">＋</span><span class="foot-label">Adicionar jogo</span></button>
      <button class="import-btn" id="btn-import-all" title="Trazer de uma vez todos os jogos em que você já tem conquistas"><span class="foot-icon">⤓</span><span class="foot-label">Importar meus jogos</span></button>
    </div>
  </aside>`;
}

async function enterHall() {
  S.view = "hall";
  stopPolling();
  S.library = await backend.library();
  S.hall = await backend.masteryHall().catch(() => ({ ok: false, mastery: [], softcore: [] }));
  if (S.view !== "hall") return;
  renderHall();
}

function renderHall() {
  const hall = S.hall || { mastery: [], softcore: [] };
  const all = [...(hall.mastery || []), ...(hall.softcore || [])];
  const platforms = [...new Set(all.map((g) => g.platform).filter(Boolean))].sort();
  const filter = (list) => S.hallPlatform === "all" ? list : list.filter((g) => g.platform === S.hallPlatform);
  const card = (game, kind) => {
    const completion = game.completion || {};
    const score = game.score || {};
    const cover = game.art?.cover || game.art?.box;
    const date = kind === "mastery" ? completion.mastery_date : completion.softcore_date;
    const event = [...((game.completion_events || {}).events || [])].reverse().find((e) => e.kind === kind);
    return `<article class="hall-card ${kind}" style="--jogo:${corDe(game)}">
      <div class="hall-cover">${cover ? `<img src="${esc(cover)}" alt="">` : `<span>${esc(initials(game.title))}</span>`}<i>${kind === "mastery" ? "★" : "100%"}</i></div>
      <div class="hall-card-body"><small>${kind === "mastery" ? "MASTERY HARDCORE" : "CONCLUSÃO SOFTCORE"}</small><h3>${esc(game.title)}</h3><p>${esc(game.platform || "Plataforma não informada")}</p>
      <div class="hall-stats"><span><b>${kind === "mastery" ? score.hardcore || 0 : score.earned || 0}</b> pontos</span>${game.playtime?.label ? `<span><b>${esc(game.playtime.label)}</b> tempo RA</span>` : ""}${date ? `<span><b>${esc(date)}</b> conclusão</span>` : ""}</div>
      <div class="hall-actions"><button data-hall-open="${esc(game.slug)}">Abrir jogo</button><button data-hall-replay="${esc(game.slug)}" data-event="${esc(event?.id || "")}">Rever celebração</button></div></div>
    </article>`;
  };
  root.innerHTML = xpShell(`<main class="main hall-main"><div class="hall-hero"><span>✦ ARQUIVO DE CONQUISTAS</span><h1>Hall da Mastery</h1><p>Seu espaço VIP de conclusões. Pontos e tempo são registrados oficialmente pela RetroAchievements.</p><select id="hall-platform"><option value="all">Todas as plataformas</option>${platforms.map((p) => `<option value="${esc(p)}" ${S.hallPlatform === p ? "selected" : ""}>${esc(p)}</option>`).join("")}</select></div>
    <div class="hall-scroll" data-scroll="hall"><section class="hall-section mastery"><div class="hall-section-title"><div><span>★</span><h2>Hall da Mastery</h2></div><b>${filter(hall.mastery || []).length}</b></div><div class="hall-grid">${filter(hall.mastery || []).map((g) => card(g, "mastery")).join("") || `<p class="hall-empty">Nenhuma Mastery nesta seleção ainda.</p>`}</div></section>
    <section class="hall-section softcore"><div class="hall-section-title"><div><span>◇</span><h2>Conclusões 100%</h2></div><b>${filter(hall.softcore || []).length}</b></div><div class="hall-grid">${filter(hall.softcore || []).map((g) => card(g, "softcore")).join("") || `<p class="hall-empty">Nenhuma conclusão Softcore nesta seleção.</p>`}</div></section></div></main>`, true);
  $("#btn-library").hidden = true;
  bindSidebar();
  $("#hall-platform")?.addEventListener("change", (e) => { S.hallPlatform = e.target.value; renderHall(); });
  root.querySelectorAll("[data-hall-open]").forEach((b) => b.onclick = async () => { S.activeSlug = b.dataset.hallOpen; await backend.setActiveGame(S.activeSlug).catch(() => null); await enterDashboard(); });
  root.querySelectorAll("[data-hall-replay]").forEach((b) => b.onclick = async () => {
    const label = b.textContent;
    b.disabled = true; b.textContent = "Abrindo…";
    try {
      const res = await backend.reopenCompletionEvent(b.dataset.hallReplay, b.dataset.event);
      if (!res?.ok) return toast(res?.error || "Não foi possível rever a comemoração.", true);
      showCompletionCelebration({ ...res.event, game: res.game, slug: b.dataset.hallReplay }, true);
    } catch (error) {
      toast(`Não foi possível rever a comemoração: ${error}`, true);
    } finally {
      b.disabled = false; b.textContent = label;
    }
  });
}

function mainHTML(game) {
  if (!game) {
    return `<main class="main"><div class="empty-main">
      <div class="big">Nenhum jogo selecionado</div>
      <p>Use "Adicionar Jogo" para importar seu primeiro título da RetroAchievements.</p>
    </div></main>`;
  }
  // Migra estados de navegação persistidos pelas versões anteriores.
  if (["mastery", "walk"].includes(S.tab)) S.tab = "achievements";
  if (["overview", "tips"].includes(S.tab)) S.tab = "journey";
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
  const pendingGuideMissables = pendingBlocks.filter((b) => b.type === "missable");
  const pendingRAMissables = game.pending_missables || [];
  const journeySteps = [
    ...pendingRAMissables.slice(0, 1).map((row) => ({ title: `Perdível: ${row.name}${row.earned && !row.hardcore ? " · refazer em Hardcore" : ""}`, urgent: true })),
    ...nextSteps,
  ].slice(0, 3);
  const missables = pendingRAMissables.length + pendingGuideMissables.length;
  const firstMissable = pendingRAMissables[0] || pendingGuideMissables[0] || {};
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
              <div><strong>${t ? Math.round((mst.hardcore || 0) / t * 100) : 0}%</strong><small>${mst.hardcore || 0}/${t} Hardcore</small></div>
              <div class="hero-bar" title="Progresso de Mastery: ${mst.hardcore || 0} de ${t} em hardcore">
                <i style="width:${t ? Math.round((mst.hardcore || 0) / t * 100) : 0}%"></i>
              </div>
              <div class="hero-ra-meta"><span>${game.score?.hardcore || 0}/${game.score?.available || 0} pts</span>${game.playtime?.label ? `<span title="Tempo registrado pela RetroAchievements">◷ ${esc(game.playtime.label)}</span>` : ""}</div>
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
      <button class="ptab ${S.tab === "journey" ? "active" : ""}" data-tab="journey"><b>✦</b> Guia${(smartDoc.chapters || []).length ? `<span class="count">${smartDoc.chapters.length}</span>` : tips ? `<span class="count">${tips}</span>` : ""}</button>
      <button class="ptab ${S.tab === "achievements" ? "active" : ""}" data-tab="achievements"><b>♜</b> Conquistas${mst.softcore_only ? `<span class="count">${mst.softcore_only}</span>` : ""}</button>
      <button class="ptab ${S.tab === "atlas" ? "active" : ""}" data-tab="atlas"><b>◇</b> Atlas${(smartDoc.systems || []).filter((item) => item.status !== "rejected").length ? `<span class="count">${(smartDoc.systems || []).filter((item) => item.status !== "rejected").length}</span>` : ""}</button>
      <span class="tab-bumper">RB</span>
    </div>
    ${S.tab === "journey" && S.guideReader ? `<div class="guide-commandbar dashboard-commandbar journey-reader-bar"><button class="btn-ghost" id="guide-reader-close">← Jornada</button><label class="guide-search"><span>⌕</span><input id="guide-search" value="${esc(S.guideQuery)}" placeholder="Buscar neste capítulo"></label><select id="guide-filter" aria-label="Filtrar guia"><option value="all">Tudo</option><option value="pending">Pendentes</option><option value="missable">Perdíveis</option><option value="warning">Avisos</option><option value="favorites">Favoritos</option><option value="achievement">Conquistas</option></select></div>` : ""}
    ${S.tab === "journey" && !S.guideReader ? `<section class="activity-deck hybrid-dashboard" aria-label="Continuar jogando">
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
          <ul>${journeySteps.length ? journeySteps.map((b, i) => `<li class="${b.urgent ? "urgent" : (i ? "" : "done")}">${esc(b.title || b.text || `Passo ${i + 1}`)}</li>`).join("") : `<li>Organize o guia para receber os próximos passos.</li>`}</ul>
          <i>${smartDone} concluídos <b>→</b></i>
        </div>
        <button class="guide-progress-strip" data-open-achievements title="Abrir conquistas e ordem recomendada">
          <span class="progress-trophy">♜</span><div><small>PROGRESSO DE CONQUISTAS</small><strong>${e}/${t} (${pct}%)</strong></div>
          <div class="guide-progress-bar"><i style="width:${pct}%"></i></div><span>${Math.max(0, t - e)} restantes</span><b>→</b>
        </button>
      </div>
      <aside class="activity-side-grid">
        <button type="button" class="activity-card missable ${missables ? "warn" : "clear"}" data-open-missables aria-label="Ver detalhes dos perdíveis">
          <span class="activity-kicker">△ PERDÍVEL</span>
          <div><strong>${missables}</strong><span>${missables === 1 ? " perdível nesta seção" : " perdíveis pendentes"}</span></div>
          ${missables ? `<small class="missable-origin">${pendingRAMissables.length ? "Classificação oficial RetroAchievements" : "Aviso do guia"}</small>` : ""}
          <p>${esc(firstMissable.name || firstMissable.title || firstMissable.text || "Nenhum alerta crítico para o próximo objetivo.")}</p>
          ${pendingRAMissables[0]?.earned && !pendingRAMissables[0]?.hardcore ? `<span class="missable-softcore">Refazer em Hardcore</span>` : ""}
          <i>Ver detalhes <b>→</b></i>
        </button>
        <button class="activity-card personal-notes" data-jump-guide="${esc(next.block_id || "")}">
          <span class="activity-kicker">✎ MINHAS ANOTAÇÕES</span>
          <p>${personalNotes ? `${personalNotes} ${personalNotes === 1 ? "anotação pessoal salva" : "anotações pessoais salvas"}.` : "Adicione suas anotações pessoais sobre estratégias, itens ou lembretes aqui."}</p>
          <i><b>→</b></i>
        </button>
      </aside>
    </section><div class="journey-actions"><button class="journey-open-guide" id="guide-reader-open"><span>▤</span><div><b>Abrir walkthrough completo</b><small>Leia um capítulo por vez, sem poluir sua Jornada.</small></div><i>→</i></button><button class="journey-manage-sources" id="journey-sources"><span>＋</span><div><b>Fontes dos guias</b><small>${(smart.walkthrough_sources || []).length} de 10 adicionadas</small></div></button></div>` : ""}
    ${S.tab === "atlas" ? guideSystemsHTML(game) : S.tab === "achievements" ? achievementsHTML(game) : (S.guideReader ? guideHTML(game) : "")}
  </main>`;
}

/* ======================= CONQUISTAS + ROTEIRO ========================== */
/* Progresso, porcentagem e ordem recomendada moram na mesma sessão. O filtro
   substitui a antiga troca constante entre Walkthrough e Mastery. */
function achievementsHTML(game) {
  const m = game.mastery || {};
  const total = m.total || 0;
  const hardPct = total ? Math.round(((m.hardcore || 0) / total) * 100) : 0;
  const earnedPct = total ? Math.round(((m.earned || 0) / total) * 100) : 0;
  const softIds = new Set(m.softcore_ids || []);
  const nextIds = game.next_ids || [];
  const badge = (a) => a.badge_url
    ? `<div class="ach-badge ${a.earned ? "" : "locked"}" style="background-image:url('${esc(a.badge_url)}')"></div>`
    : `<div class="ach-badge ${a.earned ? "" : "locked"}">${a.earned ? "🏆" : "🔒"}</div>`;

  const guideOrder = new Map();
  const guideSteps = new Map();
  const guideCompleted = new Set(game.smart_guide?.effective_progress?.completed || game.smart_guide?.progress?.completed || []);
  (game.smart_guide?.current?.chapters || []).forEach((chapter, chapterIndex) => {
    (chapter.blocks || []).forEach((block, blockIndex) => {
      const achievementId = Number(block.achievement_id || 0);
      if (block.type === "achievement" && achievementId > 0 && !guideOrder.has(achievementId)) {
        guideOrder.set(achievementId, { position: chapterIndex * 10000 + blockIndex, step: chapterIndex + 1, area: chapter.title });
      } else if (["objective", "checklist", "checkpoint", "warning", "missable", "challenge"].includes(block.type)) {
        if (!guideSteps.has(chapterIndex + 1)) guideSteps.set(chapterIndex + 1, []);
        guideSteps.get(chapterIndex + 1).push(block);
      }
    });
  });
  const orderedAchievements = (game.achievements || []).map((achievement, officialIndex) => {
    const guide = guideOrder.get(Number(achievement.id));
    return { ...achievement, step: guide?.step ?? achievement.step, area: guide?.area || achievement.area, _position: guide ? guide.position : 100000000 + officialIndex };
  }).sort((a, b) => a._position - b._position);
  const filter = S.achievementFilter || "all";
  const visible = orderedAchievements.filter((a) => {
    if (filter === "pending") return !a.hardcore;
    if (filter === "softcore") return softIds.has(a.id);
    if (filter === "missable") return a.achievement_type === "missable";
    return true;
  });
  const renderGuideRows = (step) => (guideSteps.get(Number(step)) || []).filter((block) => {
    if (filter === "pending") return !guideCompleted.has(block.id);
    if (filter === "missable") return block.type === "missable";
    if (filter === "softcore") return false;
    return true;
  }).map((block) => `<div class="guide-step-row type-${esc(block.type)} ${guideCompleted.has(block.id) ? "done" : ""}" data-guide-block="${esc(block.id)}"><button class="smart-check" data-guide-action="complete" data-value="${!guideCompleted.has(block.id)}">${guideCompleted.has(block.id) ? "✓" : block.type === "missable" ? "◆" : block.type === "warning" ? "!" : "→"}</button><div><span>${block.type === "missable" ? "AVISO DO GUIA · PERDÍVEL" : esc(block.type)}</span><b>${esc(block.title || block.text)}</b>${block.title && block.text ? `<small>${esc(block.text)}</small>` : ""}</div></div>`).join("");
  let rows = "", lastStep = null;
  const renderedGuideSteps = new Set();
  for (const a of visible) {
    if (a.step !== lastStep) {
      lastStep = a.step;
      if (a.area) rows += `<p class="step-area">▸ ETAPA ${a.step || "—"} — ${esc(a.area)}</p>`;
      renderedGuideSteps.add(Number(a.step));
      rows += renderGuideRows(a.step);
    }
    const isNext = nextIds.includes(a.id);
    if (a.achievement_type === "missable" && !a.hardcore) rows += `<div class="achievement-missable-alert"><span>△ PERDÍVEL</span><b>${a.earned ? "Obtida somente em Softcore · refaça em Hardcore" : "Conclua antes de avançar além desta etapa"}</b></div>`;
    rows += `<div class="ach-row ${isNext ? "next" : ""} ${a.earned || isNext ? "" : "locked"} ${a.achievement_type === "missable" ? "missable" : ""}" data-ach-id="${esc(a.id)}" data-ach-state="${softIds.has(a.id) ? "softcore" : a.earned ? "earned" : "pending"}">
      ${badge(a)}
      <div class="ach-body">
        <div class="ach-titleline">
          <span class="ach-name">${esc(a.name)}</span>
          ${a.earned ? modeTag(a.mode) : ""}
          ${a.achievement_type === "missable" ? `<span class="missable-tag">PERDÍVEL</span>` : ""}
          ${isNext ? `<span class="next-tag">PRÓXIMO</span>` : ""}
        </div>
        <div class="ach-desc">${esc(a.desc)}</div>
      </div>
      ${a.earned ? `<span class="ach-check" style="color:${modeColor(a.mode)}">✓</span>` : ""}
    </div>`;
  }
  for (const [step] of [...guideSteps.entries()].sort((a, b) => a[0] - b[0])) {
    if (renderedGuideSteps.has(step)) continue;
    const chapter = game.smart_guide?.current?.chapters?.[step - 1];
    const extra = renderGuideRows(step);
    if (extra) rows += `<p class="step-area">▸ ETAPA ${step} — ${esc(chapter?.title || "Jornada")}</p>${extra}`;
  }
  const status = m.complete
    ? `<div class="ms-done">★ MASTERY COMPLETO — ${total}/${total} em hardcore</div>`
    : `<div class="ms-remaining">FALTAM <b>${m.remaining || 0}</b> ${(m.remaining === 1) ? "CONQUISTA" : "CONQUISTAS"} PARA O MASTERY</div>`;
  const filterButton = (id, label, count = "") => `<button class="achievement-filter ${filter === id ? "on" : ""}" data-ach-filter="${id}">${label}${count !== "" ? `<span>${count}</span>` : ""}</button>`;
  return `<div class="list-wrap achievements-hub">
    <section class="achievement-overview">
      <div class="achievement-overview-head"><div><small>PROGRESSO DE CONQUISTAS</small><h2>${hardPct}% Mastery</h2></div><div class="achievement-score"><b>${game.score?.hardcore || 0}</b><span>pontos hardcore</span></div></div>
      <div class="achievement-progress-grid">
        <div class="ms-row"><div class="ms-row-head"><span class="ms-label">⚡ HARDCORE</span><span class="ms-num" style="color:${MODE_COLOR.hardcore}">${m.hardcore || 0}/${total} · ${hardPct}%</span></div><div class="ms-bar"><div class="ms-fill" style="width:${hardPct}%;background:${MODE_COLOR.hardcore}"></div></div></div>
        <div class="ms-row"><div class="ms-row-head"><span class="ms-label">○ TOTAL OBTIDO</span><span class="ms-num" style="color:${MODE_COLOR.softcore}">${m.earned || 0}/${total} · ${earnedPct}%</span></div><div class="ms-bar"><div class="ms-fill" style="width:${earnedPct}%;background:${MODE_COLOR.softcore}"></div></div></div>
      </div>
      ${status}
    </section>
      <div class="achievement-list-head"><div><p class="list-title">ORDEM RECOMENDADA</p><span>Conquistas organizadas pelo walkthrough importado.</span></div><div class="achievement-filters">${filterButton("all", "Todas", total)}${filterButton("pending", "Pendentes", m.remaining || 0)}${filterButton("missable", "Perdíveis", (game.pending_missables || []).length)}${filterButton("softcore", "Só softcore", m.softcore_only || 0)}</div></div>
    ${rows || `<p class="achievement-empty">Nenhuma conquista corresponde a este filtro.</p>`}
  </div>`;
}

// Contratos antigos ainda podem chamar estes nomes durante uma atualização.
const masteryHTML = achievementsHTML;
const walkHTML = achievementsHTML;

const ATLAS_CARD_WIDTH = 214;
const ATLAS_CARD_HEIGHT = 214;
const ATLAS_WORLD_PADDING = 56;
const ATLAS_COLUMN_GAP = 86;
const ATLAS_ROW_GAP = 52;

function guideSystemLayout(system, visibleNodes) {
  const visible = new Set(visibleNodes.map((node) => node.id));
  const edges = (system.edges || []).filter((edge) => visible.has(edge.from) && visible.has(edge.to));
  const incoming = new Map(visibleNodes.map((node) => [node.id, 0]));
  edges.forEach((edge) => incoming.set(edge.to, (incoming.get(edge.to) || 0) + 1));
  const levels = new Map(), queue = visibleNodes.filter((node) => !incoming.get(node.id)).map((node) => node.id);
  queue.forEach((id) => levels.set(id, 0));
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const id = queue[cursor], level = levels.get(id) || 0;
    edges.filter((edge) => edge.from === id).forEach((edge) => {
      levels.set(edge.to, Math.max(levels.get(edge.to) || 0, level + 1));
      incoming.set(edge.to, Math.max(0, (incoming.get(edge.to) || 0) - 1));
      if (!incoming.get(edge.to)) queue.push(edge.to);
    });
  }
  visibleNodes.forEach((node, index) => { if (!levels.has(node.id)) levels.set(node.id, index % 4); });
  const columns = new Map();
  visibleNodes.forEach((node) => {
    const level = levels.get(node.id) || 0;
    if (!columns.has(level)) columns.set(level, []);
    columns.get(level).push(node);
  });
  const positions = new Map();
  [...columns.entries()].sort((a, b) => a[0] - b[0]).forEach(([level, nodes]) => {
    nodes.forEach((node, row) => positions.set(node.id, {
      x: ATLAS_WORLD_PADDING + level * (ATLAS_CARD_WIDTH + ATLAS_COLUMN_GAP),
      y: ATLAS_WORLD_PADDING + row * (ATLAS_CARD_HEIGHT + ATLAS_ROW_GAP),
    }));
  });
  const maxLevel = Math.max(0, ...levels.values());
  const maxRows = Math.max(1, ...[...columns.values()].map((items) => items.length));
  let width = Math.max(760, ATLAS_WORLD_PADDING * 2 + (maxLevel + 1) * ATLAS_CARD_WIDTH + maxLevel * ATLAS_COLUMN_GAP);
  let height = Math.max(520, ATLAS_WORLD_PADDING * 2 + maxRows * ATLAS_CARD_HEIGHT + (maxRows - 1) * ATLAS_ROW_GAP + 70);
  let direction = "horizontal";
  if (system.layout === "vertical") {
    [...positions.entries()].forEach(([id, pos]) => positions.set(id, { x: pos.y, y: pos.x }));
    [width, height] = [Math.max(760, height), Math.max(520, width)];
    direction = "vertical";
  } else if (system.layout === "radial") {
    const radius = Math.max(230, visibleNodes.length * 28), center = radius + ATLAS_WORLD_PADDING + ATLAS_CARD_WIDTH / 2;
    visibleNodes.forEach((node, index) => {
      const angle = (Math.PI * 2 * index / Math.max(1, visibleNodes.length)) - Math.PI / 2;
      positions.set(node.id, {
        x: center + Math.cos(angle) * radius - ATLAS_CARD_WIDTH / 2,
        y: center + Math.sin(angle) * radius - ATLAS_CARD_HEIGHT / 2,
      });
    });
    width = height = Math.max(760, center * 2 + ATLAS_WORLD_PADDING);
    direction = "radial";
  }
  return { edges, positions, width, height, direction,
    cardWidth: ATLAS_CARD_WIDTH, cardHeight: ATLAS_CARD_HEIGHT };
}

function atlasSystems(game) {
  const draft = (game.smart_guide?.atlas_drafts || []).find((item) => item.source_id === S.guideAtlas.draftSource);
  return draft?.system ? [{ ...draft.system, _draftSource: draft.source_id, _draftMedia: draft.node_media || {}, _draftDiagnostics: draft.diagnostics || {} }] : (game.smart_guide?.current?.systems || []);
}

function atlasJobsHTML(game) {
  const labels = { running: "Processando a fonte", suggested: "Prévia pronta para revisão", error: "Falha na análise", interrupted: "Análise interrompida", cancelled: "Análise cancelada", awaiting_source_review: "Aguardando revisão da fonte", awaiting_consent: "Aguardando consentimento", awaiting_configuration: "Aguardando configuração da IA" };
  const errorMeta = (job) => {
    const raw = String(job.error || ""); let kind = job.error_kind || "";
    if (!kind && /(503|UNAVAILABLE|high demand)/i.test(raw)) kind = "api_service";
    if (!kind && /(429|rate.?limit|limite de uso)/i.test(raw)) kind = "api_limit";
    if (!kind && /(tabela inteira|linhas referenciadas|Atlas inválido)/i.test(raw)) kind = "ai_response";
    const titles = { api_service: "O serviço de IA está instável", api_limit: "A chave atingiu um limite temporário", api_configuration: "A configuração da IA precisa de atenção", network: "Falha de conexão com a IA", ai_response: "A fonte carregou, mas a resposta da IA falhou", source_import: "Não foi possível ler a fonte", source_validation: "A fonte não pôde ser validada", internal: "Falha interna do Atlas" };
    let message = raw;
    if (kind === "api_service" && /\{.*(?:503|UNAVAILABLE)/s.test(raw)) message = "O provedor está temporariamente indisponível ou com alta demanda. O DigiTracker tentou novamente.";
    const hint = job.error_details?.hint || (Number(job.checkpoint_count || 0) ? `${job.checkpoint_count} lote(s) já foram salvos e serão reaproveitados.` : "Você pode tentar novamente sem alterar o Atlas publicado.");
    return { kind: kind || "internal", title: titles[kind] || "A análise não terminou", message, hint };
  };
  const cards = (game.smart_guide?.atlas_jobs || []).filter((job) => labels[job.status]).map((job) => {
    const done = Number(job.analysis_done || 0), total = Number(job.analysis_total || 0);
    const pct = total ? Math.max(2, Math.min(100, Math.round(done / total * 100))) : 8;
    const meta = job.error ? errorMeta(job) : null;
    const technical = job.error_details && Object.keys(job.error_details).length
      ? JSON.stringify(job.error_details, null, 2) : "";
    return `<article class="atlas-job ${job.status} ${meta ? esc(meta.kind) : ""}">
      <div class="atlas-job-main"><div class="atlas-job-heading"><div><span>${esc(job.provider || "ATLAS")}${job.model ? ` · ${esc(job.model)}` : ""}</span><b>${esc(job.title)}</b></div><em>${esc(labels[job.status])}</em></div>
      ${job.status === "running" ? `<p>${esc(job.message || "Analisando relações e requisitos…")}</p><div class="atlas-job-progress"><i style="width:${pct}%"></i></div><small>${total ? `Lote ${done}/${total}` : "Preparando lotes"}${job.checkpoint_count ? ` · ${esc(job.checkpoint_count)} salvo(s)` : ""}</small>` : ""}
      ${meta ? `<div class="atlas-job-error"><b>${esc(meta.title)}</b><p>${esc(meta.message)}</p><small>${esc(meta.hint)}</small>${technical ? `<details><summary>Detalhes técnicos</summary><pre>${esc(technical)}</pre></details>` : ""}</div>` : ""}</div>
      <div class="atlas-job-actions">${job.status === "suggested" ? `<button data-atlas-review-job="${esc(job.id)}">Revisar prévia</button>` : job.status === "awaiting_source_review" ? `<button data-atlas-review-source="${esc(job.id)}">Revisar fonte</button>` : job.status === "running" ? `<button data-atlas-cancel-job="${esc(job.id)}">Cancelar</button>` : `<button data-atlas-retry-job="${esc(job.id)}">${job.checkpoint_count ? "Retomar" : "Tentar novamente"}</button>${["api_limit","api_service","api_configuration"].includes(meta?.kind) ? `<button data-atlas-settings>Trocar modelo</button>` : ""}<button data-atlas-manual-job="${esc(job.id)}">Criar manualmente</button>`}</div>
    </article>`;
  }).join("");
  return `<div class="atlas-jobs">${cards}</div>`;
}

function guideSystemsHTML(game) {
  const bundle = game.smart_guide || {}, doc = bundle.current || {};
  const systems = atlasSystems(game).filter((item) => item.status !== "rejected");
  const jobs = atlasJobsHTML(game);
  if (!systems.length) return `${jobs}<section class="atlas-empty"><span>◇</span><h3>Crie o primeiro sistema visual</h3><p>Cada sistema usa um PDF ou GameFAQs exclusivo para gerar caminhos e requisitos fiéis à fonte.</p><button id="atlas-create">＋ Novo sistema com fonte própria</button></section>`;
  const A = S.guideAtlas;
  let system = systems.find((item) => item.id === A.systemId) || systems.find((item) => item.status === "approved") || systems[0];
  A.systemId = system.id;
  const state = bundle.system_state || {}, completed = new Set(state.completed_requirements || []);
  const groups = [...new Set((system.nodes || []).map((node) => node.group).filter(Boolean))];
  const tags = [...new Set((system.nodes || []).flatMap((node) => node.tags || []).filter(Boolean))];
  const cardNumberFor = (node) => Number(node?.card_number || (system.nodes || []).indexOf(node) + 1) || 0;
  const available = (node) => {
    const incoming = (system.edges || []).filter((edge) => edge.to === node.id);
    return !incoming.length || incoming.some((edge) => (edge.requirements || []).every((req) => completed.has(req.id)));
  };
  const visibleNodes = (system.nodes || []).filter((node) => {
    if (node.spoiler && !A.spoilers) return false;
    if (A.group !== "all" && node.group !== A.group) return false;
    if (A.tag !== "all" && !(node.tags || []).includes(A.tag)) return false;
    if (A.search && !`${node.label} ${node.subtitle} ${node.stage} ${node.group} ${(node.tags || []).join(" ")} card ${cardNumberFor(node)} #${cardNumberFor(node)}`.toLocaleLowerCase("pt-BR").includes(A.search.toLocaleLowerCase("pt-BR"))) return false;
    if (A.availability === "available" && !available(node)) return false;
    if (A.availability === "blocked" && available(node)) return false;
    return true;
  });
  const selectedId = visibleNodes.some((node) => node.id === A.nodeId)
    ? A.nodeId : (visibleNodes.find(node => node.id === (state.goals || {})[system.id])?.id || visibleNodes[0]?.id || "");
  A.nodeId = selectedId;
  const selected = (system.nodes || []).find((node) => node.id === selectedId) || null;
  const mode = A.mode || "focus";
  const focusedIds = new Set(selectedId ? [selectedId] : []);
  if (mode === "focus" && selectedId) {
    (system.edges || []).forEach((edge) => { if (edge.from === selectedId || edge.to === selectedId) { focusedIds.add(edge.from); focusedIds.add(edge.to); } });
  }
  const mapNodes = mode === "focus" ? visibleNodes.filter((node) => focusedIds.has(node.id)) : visibleNodes;
  const layout = guideSystemLayout(system, mapNodes);
  const routeEdges = new Set();
  const trace = (nodeId, seen = new Set()) => {
    if (seen.has(nodeId)) return; seen.add(nodeId);
    (system.edges || []).filter((edge) => edge.to === nodeId).forEach((edge) => { routeEdges.add(edge.id); trace(edge.from, seen); });
  };
  if (selectedId) trace(selectedId);
  const path = (edge) => {
    const from = layout.positions.get(edge.from), to = layout.positions.get(edge.to);
    if (!from || !to) return "";
    const width = layout.cardWidth || ATLAS_CARD_WIDTH;
    const height = layout.cardHeight || ATLAS_CARD_HEIGHT;
    if (layout.direction === "radial") {
      const center = (item) => ({ x: item.x + width / 2, y: item.y + height / 2 });
      const a = center(from), b = center(to), dx = b.x - a.x, dy = b.y - a.y;
      const edgePoint = (origin, vector) => {
        const scale = 1 / Math.max(Math.abs(vector.x) / (width / 2 - 4), Math.abs(vector.y) / (height / 2 - 4), .001);
        return { x: origin.x + vector.x * scale, y: origin.y + vector.y * scale };
      };
      const start = edgePoint(a, { x: dx, y: dy });
      const end = edgePoint(b, { x: -dx, y: -dy });
      return `M${start.x},${start.y} Q${(start.x + end.x) / 2},${(start.y + end.y) / 2} ${end.x},${end.y}`;
    }
    if (layout.direction === "vertical") {
      const x1 = from.x + width / 2, y1 = from.y + height;
      const x2 = to.x + width / 2, y2 = to.y;
      if (y2 > y1 + 10) {
        const bend = Math.max(36, (y2 - y1) * .45);
        return `M${x1},${y1} C${x1},${y1 + bend} ${x2},${y2 - bend} ${x2},${y2}`;
      }
      const channelX = Math.max(from.x + width, to.x + width) + 30;
      return `M${x1},${y1} C${x1},${y1 + 28} ${channelX},${y1 + 28} ${channelX},${y1 + 56} L${channelX},${y2 - 56} C${channelX},${y2 - 28} ${x2},${y2 - 28} ${x2},${y2}`;
    }
    const x1 = from.x + width, y1 = from.y + height / 2;
    const x2 = to.x, y2 = to.y + height / 2;
    if (x2 > x1 + 10) {
      const bend = Math.max(42, (x2 - x1) * .45);
      return `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`;
    }
    // Backward/cyclic links use a reserved lower channel so they never cut
    // through a card or leave the bounded SVG world.
    const channelY = Math.max(from.y + height, to.y + height) + 30;
    const laneStart = Math.min(layout.width - 16, x1 + 30);
    const laneEnd = Math.max(16, x2 + width / 2);
    return `M${x1},${y1} C${laneStart},${y1} ${laneStart},${channelY} ${laneStart},${channelY} L${laneEnd},${channelY} C${laneEnd},${channelY} ${x2},${y2 - 28} ${x2},${y2}`;
  };
  const refsHTML = (refs) => (refs || []).map((ref) => ref.page ? `p.${ref.page}` : `§${ref.section}.${ref.block}`).join(" · ");
  const nodeHTML = mapNodes.map((node) => {
    const pos = layout.positions.get(node.id), mediaId = (system._draftMedia || {})[node.id] ?? (state.node_media || {})[`${system.id}:${node.id}`];
    const media = (bundle.media || []).find((item) => item.id === mediaId);
    const goal = (state.goals || {})[system.id] === node.id;
    const cardNumber = cardNumberFor(node);
    const incomingCount = (system.edges || []).filter((edge) => edge.to === node.id).length;
    const outgoingCount = (system.edges || []).filter((edge) => edge.from === node.id).length;
    return `<button class="atlas-node ${node.id === selectedId ? "selected" : ""} ${available(node) ? "available" : "blocked"} ${goal ? "goal" : ""}" data-atlas-node="${esc(node.id)}" data-atlas-card-number="${cardNumber}" aria-label="Card ${cardNumber}: ${esc(node.label)}" title="Card #${String(cardNumber).padStart(3, "0")} — ${esc(node.label)}" style="left:${pos.x}px;top:${pos.y}px">
      <span class="atlas-node-id">#${String(cardNumber).padStart(3, "0")}</span>
      <span class="atlas-node-art">${media?.url ? `<img src="${esc(media.url)}" alt="">` : `<i>◇</i>`}</span>
      <span class="atlas-node-copy"><small>${esc(node.stage || node.group || "NÓ")}</small><b>${esc(node.label)}</b><em>${esc(node.subtitle || (node.tags || []).slice(0, 2).join(" · "))}</em><span class="atlas-node-relations">${incomingCount} entrada${incomingCount === 1 ? "" : "s"} · ${outgoingCount} saída${outgoingCount === 1 ? "" : "s"}</span></span>
      ${goal ? `<span class="atlas-goal-badge">OBJETIVO</span>` : ""}
    </button>`;
  }).join("");
  const edgeHTML = layout.edges.map((edge) => {
    const marker = edge.missable ? "missable" : routeEdges.has(edge.id) ? "route" : "muted";
    const fromNumber = cardNumberFor((system.nodes || []).find((node) => node.id === edge.from));
    const toNumber = cardNumberFor((system.nodes || []).find((node) => node.id === edge.to));
    const title = `Card #${String(fromNumber).padStart(3, "0")} → Card #${String(toNumber).padStart(3, "0")}${edge.label ? ` — ${edge.label}` : ""}`;
    return `<path class="atlas-edge ${routeEdges.has(edge.id) ? "route" : "muted"} ${edge.path_kind === "alternative" ? "alternative" : ""} ${edge.missable ? "missable" : ""}" data-atlas-edge="${esc(edge.id)}" marker-end="url(#atlas-arrow-${marker})" d="${path(edge)}"><title>${esc(title)}</title></path>`;
  }).join("");
  const incoming = selected ? (system.edges || []).filter((edge) => edge.to === selected.id) : [];
  const chosenPath = incoming.find(edge => edge.id === state.preferences?.[system.id]?.edge_id) || [...incoming].sort((a,b) => a.requirements.filter(r=>!completed.has(r.id)).length-b.requirements.filter(r=>!completed.has(r.id)).length)[0];
  const requirements = chosenPath ? (chosenPath.requirements || []).map(req => ({...req,edge_id:chosenPath.id,missable:chosenPath.missable})) : [];
  const mediaId = selected ? ((system._draftMedia || {})[selected.id] ?? (state.node_media || {})[`${system.id}:${selected.id}`]) : "";
  const media = (bundle.media || []).find((item) => item.id === mediaId);
  const selectedCardNumber = selected ? cardNumberFor(selected) : 0;
  const inspector = selected ? `<aside class="atlas-inspector">
    ${incoming.length > 1 ? `<label class="atlas-path-label">Caminho para este objetivo<select id="atlas-path">${incoming.map(edge=>{ const fromNode = system.nodes.find(node=>node.id===edge.from); return `<option value="${esc(edge.id)}" ${edge.id===chosenPath?.id?'selected':''}>Card #${String(cardNumberFor(fromNode)).padStart(3, "0")} · ${esc(fromNode?.label || edge.label)}</option>`; }).join('')}</select></label>` : ''}
    <div class="atlas-inspector-art">${media?.url ? `<img src="${esc(media.url)}" alt="${esc(selected.label)}">` : `<span>◇</span>`}</div>
    <div class="atlas-inspector-card-id">CARD #${String(selectedCardNumber).padStart(3, "0")}</div><small>${esc(selected.stage || selected.group || "SISTEMA")}</small><h3>${esc(selected.label)}</h3><p>${esc(selected.subtitle || "Selecione uma imagem e acompanhe os requisitos deste objetivo.")}</p>
    ${(selected.tags || []).length ? `<div class="atlas-tags">${selected.tags.map((tag) => `<span>${esc(tag)}</span>`).join("")}</div>` : ""}
    ${Object.keys(selected.attributes || {}).length ? `<dl>${Object.entries(selected.attributes).map(([key, value]) => `<div><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>` : ""}
    <div class="atlas-requirements"><b>REQUISITOS</b>${requirements.length ? requirements.map((req) => `<label class="${req.missable ? "missable" : ""} ${req.operator === "unknown" ? "unknown" : ""}"><input type="checkbox" data-atlas-requirement="${esc(req.id)}" data-edge="${esc(req.edge_id)}" ${req.operator === "unknown" ? "disabled" : ""} ${completed.has(req.id) ? "checked" : ""}><span>${req.operator === "unknown" ? "? " : ""}${esc(req.text)}</span></label>`).join("") : `<p>Nenhum requisito documentado.</p>`}</div>
    ${system.source_id ? `<button class="atlas-source" data-atlas-source="${esc(selected.id)}">Fonte exclusiva: ${esc(system.source_id === "legacy-main" ? "guia migrado" : refsHTML(selected.source_refs || system.source_refs || []))}</button>` : ""}
    <div class="atlas-inspector-actions"><button class="primary" id="atlas-goal">${(state.goals || {})[system.id] === selected.id ? "✓ Objetivo fixado" : "◎ Fixar como objetivo"}</button><button id="atlas-media">▧ Trocar imagem</button><button id="atlas-edit">✎ Editar sistema</button><button id="atlas-replace-source">↺ Trocar fonte</button>${!system._draftSource ? `<button class="danger wide" id="atlas-delete">⌫ Excluir sistema</button>` : ""}</div>
  </aside>` : `<aside class="atlas-inspector empty">Selecione um nó para ver seus detalhes.</aside>`;
  const diagnostics = system._draftDiagnostics || {};
  const pendingItems = diagnostics.pending_items || [];
  const warningItems = diagnostics.warning_items || [];
  const pendingCount = pendingItems.length || (diagnostics.pending_table_ids || []).length;
  const warningCount = warningItems.length;
  const coverage = diagnostics.batches ? `${(system.nodes || []).length} cartões · ${(system.edges || []).length} caminhos · ${diagnostics.batches} lotes${diagnostics.source_pages ? ` · ${diagnostics.referenced_pages}/${diagnostics.source_pages} páginas citadas` : ""}${diagnostics.table_blocks ? ` · ${diagnostics.covered_table_blocks}/${diagnostics.table_blocks} linhas de tabela cobertas` : ""}${pendingCount ? ` · ${pendingCount} pendência${pendingCount === 1 ? "" : "s"}` : ""}${warningCount ? ` · ${warningCount} aviso${warningCount === 1 ? "" : "s"}` : ""}` : "";
  const review = system.status === "suggested" ? `<div class="atlas-review"><span>REVISÃO NECESSÁRIA</span><p>Confira nomes, caminhos, requisitos, spoilers e perdíveis antes de publicar.${coverage ? `<strong>${esc(coverage)}</strong>` : ""}${pendingCount ? `<small class="atlas-pending-warning">A aprovação está bloqueada até cada pendência ser resolvida ou excluída na seleção da fonte.</small>` : warningCount ? `<small class="atlas-review-warning">Há avisos de interpretação para confirmar; eles não bloqueiam a publicação.</small>` : ""}</p><div class="atlas-review-actions">${pendingCount ? `<button class="atlas-review-secondary" id="atlas-open-diagnostics">Ver detalhes (${pendingCount})</button>${system._draftSource ? `<button class="atlas-review-secondary" id="atlas-review-pending-source">Revisar tabelas</button>` : ""}` : warningCount ? `<button class="atlas-review-secondary" id="atlas-open-diagnostics">Ver avisos (${warningCount})</button>` : ""}<button id="atlas-approve" ${pendingCount ? "disabled" : ""}>Aprovar sistema</button><button id="atlas-edit">Revisar e editar</button><button id="atlas-reject">Rejeitar</button></div></div>` : "";
  return `${jobs}<section class="atlas-shell ${mode === "list" ? "list-view" : ""}" style="--atlas-zoom:${A.zoom};--atlas-x:${A.panX}px;--atlas-y:${A.panY}px">
    <header class="atlas-toolbar"><div><span>ATLAS DE SISTEMAS</span><h2>${esc(system.title)}</h2><p>${esc(system.description)}</p></div><div class="atlas-toolbar-actions"><button id="atlas-create">＋ Novo sistema</button>${!system._draftSource ? `<button class="danger" id="atlas-delete-header">⌫ Excluir sistema</button>` : ""}</div></header>
    ${review}<div class="atlas-filters">
      <select id="atlas-system">${systems.map((item) => `<option value="${esc(item.id)}" ${item.id === system.id ? "selected" : ""}>${esc(item.title)}${item.status === "suggested" ? " · revisar" : ""}</option>`).join("")}</select>
      <select id="atlas-group"><option value="all">Todos os ${esc(system.group_label || "grupos")}</option>${groups.map((group) => `<option value="${esc(group)}" ${A.group === group ? "selected" : ""}>${esc(group)}</option>`).join("")}</select>
      <select id="atlas-tag"><option value="all">Todas as tags</option>${tags.map((tag) => `<option value="${esc(tag)}" ${A.tag === tag ? "selected" : ""}>${esc(tag)}</option>`).join("")}</select>
      <select id="atlas-availability"><option value="all">Toda disponibilidade</option><option value="available" ${A.availability === "available" ? "selected" : ""}>Disponíveis</option><option value="blocked" ${A.availability === "blocked" ? "selected" : ""}>Bloqueados</option></select>
      <input id="atlas-search" value="${esc(A.search || "")}" placeholder="Buscar no mapa" aria-label="Buscar no mapa">
      <label><input id="atlas-spoilers" type="checkbox" ${A.spoilers ? "checked" : ""}> Spoilers</label>
      <div class="atlas-zoom"><button class="${mode === "focus" ? "active" : ""}" data-atlas-mode="focus">Foco</button><button class="${mode === "map" ? "active" : ""}" data-atlas-mode="map">Mapa</button><button class="${mode === "list" ? "active" : ""}" data-atlas-mode="list">Lista</button><button data-atlas-zoom="out">−</button><button data-atlas-zoom="fit">Ajustar</button><button data-atlas-zoom="in">＋</button>${A.draftSource ? '<button id="atlas-back-published">Voltar ao publicado</button>' : ""}</div>
    </div>
    <div class="atlas-layout"><div class="atlas-viewport" id="atlas-viewport"><div class="atlas-world" style="width:${layout.width}px;height:${layout.height}px"><svg width="${layout.width}" height="${layout.height}" aria-hidden="true"><defs><marker id="atlas-arrow-route" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto" markerUnits="userSpaceOnUse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#4bc4ff"></path></marker><marker id="atlas-arrow-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto" markerUnits="userSpaceOnUse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#37516b"></path></marker><marker id="atlas-arrow-missable" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto" markerUnits="userSpaceOnUse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#e5ae3e"></path></marker></defs>${edgeHTML}</svg>${nodeHTML}</div>${!visibleNodes.length ? `<p class="atlas-no-results">Nenhum nó corresponde aos filtros.</p>` : ""}</div>${inspector}</div>
  </section>`;
}

/* Leitor focado da Jornada. As fontes permanecem preservadas, mas sua gestão
   vive apenas em Configurações > Biblioteca para manter a navegação limpa. */
function guideHTML(game) {
  const bundle = game.smart_guide || {};
  const doc = bundle.current || {};
  const chapters = doc.chapters || [];
  if (!chapters.length) return `<div class="list-wrap"><div class="guide-empty-state">
    <span class="guide-empty-kicker">JORNADA</span><h2>Crie seu walkthrough completo</h2>
    <p>Adicione PDFs, GameFAQs ou textos em Fontes dos guias e consolide o melhor de cada um.</p>
    <button class="guide-import-card primary" id="journey-sources-empty"><span class="guide-import-icon">＋</span><span><b>Gerenciar fontes</b><small>Até dez fontes independentes por jogo</small></span><span class="guide-arrow">→</span></button>
  </div></div>`;
  S.guideChapter = Math.max(0, Math.min(Number(S.guideChapter || 0), chapters.length - 1));
  const chapter = chapters[S.guideChapter];
  const progress = bundle.effective_progress || bundle.progress || {};
  const completed = new Set(progress.completed || []);
  const favorites = new Set(progress.favorites || []);
  const revealed = new Set(progress.revealed_spoilers || []);
  const notes = progress.notes || {};
  const mediaById = new Map((bundle.media || []).map((item) => [item.id, item]));
  const query = S.guideQuery.trim().toLocaleLowerCase("pt-BR");
  const filter = S.guideFilter || "all";
  const icons = { objective: "→", checklist: "✓", warning: "!", missable: "◆", achievement: "♜", challenge: "⚔", table: "▦", comparison: "⇄", image: "▧", route: "↝", graph: "◇", note: "i", spoiler: "◉", resource: "＋", checkpoint: "◷", text: "·" };
  const visible = (block) => {
    const hay = `${block.title || ""} ${block.text || ""} ${(block.items || []).map((item) => item.text).join(" ")}`.toLocaleLowerCase("pt-BR");
    if (query && !hay.includes(query)) return false;
    if (filter === "pending" && completed.has(block.id)) return false;
    if (filter === "favorites" && !favorites.has(block.id)) return false;
    if (!["all", "pending", "favorites"].includes(filter) && block.type !== filter) return false;
    return true;
  };
  const blocks = (chapter.blocks || []).filter(visible);
  const renderBlock = (block) => {
    const done = completed.has(block.id), favorite = favorites.has(block.id);
    const hiddenSpoiler = block.type === "spoiler" && !revealed.has(block.id);
    const visual = mediaById.get(block.visual_id);
    const items = (block.items || []).length ? `<ul>${block.items.map((item) => `<li>${esc(item.text)}</li>`).join("")}</ul>` : "";
    const table = (block.rows || []).length ? `<div class="smart-table">${block.rows.map((row) => `<div>${row.map((cell) => `<span>${esc(cell)}</span>`).join("")}</div>`).join("")}</div>` : "";
    return `<article class="smart-block type-${esc(block.type)} ${done ? "done" : ""}" id="guide-${esc(block.id)}" data-guide-block="${esc(block.id)}">
      <button class="smart-check" data-guide-action="complete" data-value="${!done}" aria-label="${done ? "Marcar pendente" : "Concluir"}">${done ? "✓" : icons[block.type] || "·"}</button>
      <div class="smart-content"><div class="smart-block-head"><span class="smart-type">${esc(block.type)}</span>${block.estimated_minutes ? `<span>◷ ${block.estimated_minutes} min</span>` : ""}</div>
        ${block.title ? `<h4>${esc(block.title)}</h4>` : ""}
        ${hiddenSpoiler ? `<button class="spoiler-cover" data-guide-action="reveal" data-value="true">Revelar spoiler</button>` : `<p>${esc(block.text)}</p>${items}${table}${visual ? `<figure><img src="${esc(visual.url)}" alt="${esc(visual.title || "Imagem do guia")}"><figcaption>${esc(visual.attribution || visual.source_name || "")}</figcaption></figure>` : ""}`}
        ${notes[block.id] ? `<div class="smart-note">Sua nota: ${esc(notes[block.id])}</div>` : ""}</div>
      <div class="smart-actions"><button data-guide-action="favorite" data-value="${!favorite}" title="Favoritar">${favorite ? "★" : "☆"}</button><button data-guide-note="${esc(block.id)}" title="Nota">＋</button></div>
    </article>`;
  };
  const done = (chapter.blocks || []).filter((block) => completed.has(block.id)).length;
  const pct = (chapter.blocks || []).length ? Math.round(done / chapter.blocks.length * 100) : 0;
  return `<div class="list-wrap guide-wrap journey-reader ${S.guideDensity === "compact" ? "density-compact" : ""}">
    <header class="journey-reader-head"><div><span>CAPÍTULO ${S.guideChapter + 1} DE ${chapters.length}</span><h2>${esc(chapter.title)}</h2><p>${esc(chapter.objective || doc.summary || "")}</p></div><div class="journey-reader-progress"><b>${pct}%</b><span><i style="width:${pct}%"></i></span><small>${done}/${(chapter.blocks || []).length} passos</small></div></header>
    <section class="smart-chapter focused">${blocks.map(renderBlock).join("") || `<div class="guide-no-results">Nenhum passo corresponde aos filtros.</div>`}</section>
    <footer class="journey-reader-nav"><button id="guide-chapter-prev" ${S.guideChapter <= 0 ? "disabled" : ""}>← Capítulo anterior</button><button id="journey-reader-sources">Gerenciar fontes</button><button id="guide-chapter-next" ${S.guideChapter >= chapters.length - 1 ? "disabled" : ""}>Próximo capítulo →</button></footer>
  </div>`;
}

/* Passa as dicas JÁ importadas do jogo pela IA: refina (limpa/cura) ou traduz
   para PT-BR. Não toca nas conquistas nem na ordem. */
async function dicasIa(kind) {
  const slug = S.activeSlug;
  if (!slug) return;
  if (!S.aiReady) {
    toast("Configure uma chave de IA para continuar.");
    return enterSettings("ai", { slug, tab: "journey" });
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
    return enterSettings("experience", { slug, tab: "journey" });
  }
  if (!S.aiReady) {
    toast("Configure uma chave de IA para continuar.");
    return enterSettings("ai", { slug, tab: "journey" });
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

function openGuideMedia(query = "", context = null) {
  const localMedia = S.dashboardGame?.smart_guide?.media || [];
  S.GM = { query, context, source: context ? "web" : "openverse", results: context ? localMedia.map((item) => ({ ...item, thumbnail: item.url, _local: true })) : [], busy: false, error: "" };
  renderGuideMedia();
  if (query) searchGuideMedia();
}

function closeGuideMedia() { document.getElementById("guide-media-modal")?.remove(); S.GM = null; }

async function finishGuideMedia(media) {
  const context = S.GM?.context;
  if (context && media?.id) {
    const linked = await backend.setGuideSystemMedia(S.activeSlug, context.systemId, context.nodeId, media.id, context.sourceId || '')
      .catch((error) => ({ ok: false, error: String(error) }));
    if (!linked?.ok) return toast(linked?.error || "A imagem foi salva, mas não pôde ser associada.", true);
  }
  closeGuideMedia(); toast(context ? "Imagem associada ao nó." : "Imagem salva com atribuição."); await renderDashboard({ force: true });
}

function renderGuideMedia() {
  let modal = document.getElementById("guide-media-modal");
  if (!modal) { modal = document.createElement("div"); modal.id = "guide-media-modal"; modal.className = "modal-bg"; document.body.appendChild(modal); }
  const G = S.GM;
  modal.innerHTML = `<div class="gf-panel media-panel" role="dialog" aria-modal="true" aria-label="Recursos visuais do guia">
    <div class="gf-head"><div><div class="gf-title">REVISAR RECURSO VISUAL</div><div class="gf-sub">Nada é anexado sem sua aprovação.</div></div><button class="gf-close" id="gm-x">✕</button></div>
    <div class="gf-body"><div class="cv-sources">${G.context ? `<button class="cv-src ${G.source === "web" ? "on" : ""}" data-gm-source="web">Web / Google</button><button class="cv-src ${G.source === "library" ? "on" : ""}" data-gm-source="library">Biblioteca local</button>` : ""}<button class="cv-src ${G.source === "openverse" ? "on" : ""}" data-gm-source="openverse">Openverse</button><button class="cv-src ${G.source === "wikimedia" ? "on" : ""}" data-gm-source="wikimedia">Wikimedia</button><button class="cv-src" id="gm-broad">Busca ampla ↗</button></div>
      <div class="search-box"><span>⌕</span><input id="gm-q" value="${esc(G.query)}" placeholder="O que ajudaria a explicar esta etapa?"><button class="cv-go" id="gm-go">Buscar</button></div>
      <div class="media-manual"><input id="gm-url" placeholder="Cole aqui a URL direta encontrada na busca ampla"><label><input id="gm-rights" type="checkbox"> Confirmo que posso usar esta imagem</label><button id="gm-url-save">Revisar e salvar URL</button></div>
      <label class="media-local"><span>＋ Adicionar imagem local</span><input id="gm-file" type="file" accept="image/*"></label>
      ${G.busy ? `<div class="status-msg">Buscando mídia com licença identificável…</div>` : ""}${G.error ? `<div class="gf-error">${esc(G.error)}</div>` : ""}
      <div class="media-results">${G.results.map((m, i) => `<article><img src="${esc(m.thumbnail || m.thumb || m.url)}" alt=""><div><b>${esc(m.title || "Imagem encontrada")}</b><span>${esc(m.creator || m.host || m.source_name || "Origem externa")}</span><small>${esc(m.license || (m.width ? `${m.width}×${m.height || "?"}` : "Licença não informada"))}</small><button data-gm-approve="${i}">${m._local ? "Usar esta imagem" : "Aprovar e salvar"}</button></div></article>`).join("")}</div>
    </div></div>`;
  $("#gm-x").onclick = closeGuideMedia;
  modal.querySelectorAll("[data-gm-source]").forEach((b) => b.onclick = () => { G.source = b.dataset.gmSource; if (G.source === "library") G.results = (S.dashboardGame?.smart_guide?.media || []).map((item) => ({ ...item, thumbnail: item.url, _local: true })); renderGuideMedia(); if (G.source !== "library") searchGuideMedia(); });
  $("#gm-go").onclick = () => { G.query = ($("#gm-q")?.value || "").trim(); searchGuideMedia(); };
  $("#gm-q").onkeydown = (e) => { if (e.key === "Enter") { G.query = e.currentTarget.value.trim(); searchGuideMedia(); } };
  $("#gm-broad").onclick = () => backend.broadMediaSearch(G.query || ($("#gm-q")?.value || ""));
  $("#gm-url-save").onclick = async () => {
    const url = ($("#gm-url")?.value || "").trim(), confirmed = !!$("#gm-rights")?.checked;
    if (!url || !confirmed) return toast("Cole a URL e confirme o direito de uso.", true);
    const res = await backend.approveGuideMedia(S.activeSlug, { source:"manual", url, title:"Imagem da busca ampla", creator:"", license:"Uso confirmado pelo usuário", provider:"Busca ampla", landing_url:url }, true);
    if (!res?.ok) return toast(res?.error || "Falha ao salvar URL.", true);
    await finishGuideMedia(res.media);
  };
  $("#gm-file").onchange = async (e) => {
    const file = e.target.files?.[0]; if (!file) return;
    const data = await fileToBase64(file);
    const res = await backend.addGuideMedia(S.activeSlug, data, file.name, file.name);
    if (!res?.ok) return toast(res?.error || "Falha ao anexar.", true);
    await finishGuideMedia(res.media);
  };
  modal.querySelectorAll("[data-gm-approve]").forEach((b) => b.onclick = async () => {
    const candidate = G.results[Number(b.dataset.gmApprove)];
    if (candidate?._local) return finishGuideMedia(candidate);
    b.disabled = true; b.textContent = "Salvando…";
    const web = G.source === "web";
    const confirmed = web ? window.confirm("Confirme que você possui direito de usar esta imagem. Ela só será salva localmente após sua aprovação.") : false;
    if (web && !confirmed) { b.disabled = false; b.textContent = "Aprovar e salvar"; return; }
    const reviewed = web ? { ...candidate, source: "manual", thumbnail: candidate.thumb, landing_url: candidate.source_page || candidate.source || candidate.url, license: "Uso confirmado pelo usuário", provider: candidate.provider || "Busca web" } : candidate;
    const res = await backend.approveGuideMedia(S.activeSlug, reviewed, confirmed);
    if (!res?.ok) { b.disabled = false; b.textContent = "Aprovar e salvar"; return toast(res?.error || "Falha ao salvar.", true); }
    await finishGuideMedia(res.media);
  });
}

async function searchGuideMedia() {
  const G = S.GM; if (!G || !G.query) return;
  if (G.source === "library") return;
  G.busy = true; G.error = ""; renderGuideMedia();
  const res = await (G.source === "web" && G.context
    ? backend.searchGuideSystemMedia(S.activeSlug, G.context.systemId, G.context.nodeId, G.query, 0, G.context.sourceId || '')
    : backend.searchGuideMedia(G.query, G.source)).catch((e) => ({ ok: false, error: String(e) }));
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

function openGuideSystemEditor(system = null, sourceId = "") {
  const clone = system ? JSON.parse(JSON.stringify(system)) : {
    id: "", title: "Novo sistema visual", description: "", group_label: "Grupo",
    layout: "layered", origin: "manual", status: "approved", source_id: sourceId, source_refs: [],
    nodes: [
      { id: "node-a", card_number: 1, label: "Origem", subtitle: "", stage: "", group: "", tags: [], attributes: {}, media_query: "", spoiler: false, source_refs: [] },
      { id: "node-b", card_number: 2, label: "Destino", subtitle: "", stage: "", group: "", tags: [], attributes: {}, media_query: "", spoiler: false, source_refs: [] },
    ],
    edges: [{ id: "", from: "node-a", to: "node-b", label: "", path_kind: "normal", requirements: [], missable: false, spoiler: false, source_refs: [] }],
  };
  clone.origin = "manual"; clone.status = "approved";
  S.GSE = clone;
  renderGuideSystemEditor();
}

function closeAtlasSourceWizard() { document.getElementById("atlas-source-wizard")?.remove(); }

function atlasImportProgress(title, kind) {
  $("#atlas-processing")?.remove();
  const modal = document.createElement("div");
  modal.id = "atlas-processing"; modal.className = "modal-bg";
  modal.innerHTML = `<div class="atlas-processing" role="dialog" aria-modal="true" aria-live="polite">
    <div class="atlas-processing-orbit"><i></i><span>◇</span></div>
    <span class="atlas-processing-kicker">ATLAS / ${kind === "pdf" ? "PDF" : "GAMEFAQS"}</span>
    <h2>${esc(title || "Novo sistema visual")}</h2>
    <p id="atlas-processing-message">${kind === "pdf" ? "Lendo e estruturando o arquivo…" : "Baixando todas as páginas do guia…"}</p>
    <div class="atlas-processing-steps"><span class="active" data-process-step="source">1 <b>Fonte</b></span><i></i><span data-process-step="structure">2 <b>Estrutura</b></span><i></i><span data-process-step="ai">3 <b>IA</b></span></div>
    <div class="atlas-processing-error" id="atlas-processing-error" hidden></div>
    <button id="atlas-processing-close" hidden>Fechar</button>
  </div>`;
  document.body.appendChild(modal);
  return modal;
}

function updateAtlasImportProgress(step, message, result = null) {
  const modal = $("#atlas-processing"); if (!modal) return;
  const order = ["source", "structure", "ai"], active = Math.max(0, order.indexOf(step));
  modal.querySelectorAll("[data-process-step]").forEach((item) => {
    const index = order.indexOf(item.dataset.processStep);
    item.classList.toggle("done", index < active); item.classList.toggle("active", index === active);
  });
  const copy = $("#atlas-processing-message"); if (copy) copy.textContent = message || "Processando…";
  if (result && result.ok === false) {
    modal.querySelector(".atlas-processing")?.classList.add("failed");
    const error = $("#atlas-processing-error"), kind = result.error_kind || "source_import";
    const titles = { source_import: "Não consegui ler a fonte", source_validation: "A fonte não pôde ser validada", network: "Falha de conexão", api_limit: "Limite da API", api_service: "Serviço de IA indisponível", api_configuration: "Configuração da IA", ai_response: "Resposta da IA inválida", internal: "Falha interna" };
    const hint = result.error_details?.hint || "Confira os dados e tente novamente.";
    error.hidden = false; error.innerHTML = `<b>${esc(titles[kind] || "Falha no processamento")}</b><p>${esc(result.error || "Não foi possível continuar.")}</p><small>${esc(hint)}</small>${result.error_code ? `<code>${esc(result.error_code)}</code>` : ""}`;
    const close = $("#atlas-processing-close"); close.hidden = false; close.onclick = () => modal.remove();
  }
}

function closeAtlasImportProgress() { $("#atlas-processing")?.remove(); }

async function openAtlasSourceReview(sourceId, initialReview = null, title = "Fonte do Atlas") {
  if (!sourceId) return toast("Fonte não encontrada.", true);
  const loaded = await backend.atlasSourceReview(S.activeSlug, sourceId).catch((error) => ({ ok: false, error: String(error) }));
  if (!loaded?.ok) return toast(loaded?.error || "Não foi possível abrir a revisão da fonte.", true);
  const review = { ...(loaded.review || initialReview || {}), ...(initialReview || {}) };
  const pendingTables = new Set(review.pending_table_ids || []);
  const selected = new Set(review.selection?.table_ids || (review.tables || []).filter((table) => table.selected).map((table) => table.id));
  $("#atlas-source-review")?.remove();
  const modal = document.createElement("div"); modal.id = "atlas-source-review"; modal.className = "modal-bg";
  const hierarchy = (review.headings || []).slice(0, 160).map((heading) => `<span style="--heading-depth:${Math.max(0, Number(heading.level || 2) - 2)}">${esc(heading.title || "")}</span>`).join("");
  const rows = (review.tables || []).map((table) => {
    const pending = pendingTables.has(table.id);
    return `<label class="atlas-source-table-row ${pending ? "is-pending" : ""}"><input type="checkbox" data-source-table="${esc(table.id)}" ${selected.has(table.id) ? "checked" : ""}><span><b>${esc(table.title || table.path?.join(" › ") || "Tabela sem título")}</b><small>p. ${esc(table.page)} · ${esc(table.rows)} linhas · ${esc((table.headers || []).join(" · ") || "sem cabeçalho")}</small><em>ID da tabela: ${esc(table.id || "não disponível")}</em><em>${(table.sample || []).slice(0, 2).map((row) => esc((row.cells || []).join(" | "))).join("  •  ")}</em>${pending ? `<strong>⚠ Esta tabela gerou a pendência atual</strong>` : ""}</span></label>`;
  }).join("");
  modal.innerHTML = `<div class="atlas-source-review-panel" role="dialog" aria-modal="true" aria-label="Revisão da fonte do Atlas">
    <header><div><span>ATLAS / REVISÃO DA FONTE</span><h2>${esc(review.title || title)}</h2><p>Confirme as tabelas que serão interpretadas. O HTML original permanece local e não é executado.</p></div><button id="atlas-source-review-close">×</button></header>
    ${review.edition_warning ? `<div class="atlas-source-warning"><b>⚠ Edição da fonte</b><p>${esc(review.edition_warning)}</p></div>` : ""}
    ${pendingTables.size ? `<div class="atlas-source-warning blocking"><b>⚠ ${pendingTables.size} tabela(s) pendente(s) destacada(s)</b><p>Desmarque uma tabela se ela não fizer parte do objetivo ou mantenha-a selecionada para tentar um novo mapeamento.</p></div>` : ""}
    <div class="atlas-source-review-stats"><b>${esc(review.pages || 0)} páginas</b><b>${esc(review.stats?.tables || review.tables?.length || 0)} tabelas</b><b>${esc(review.stats?.table_rows || 0)} linhas de tabela</b>${review.edition_signals?.length ? `<span>${review.edition_signals.map((signal) => esc(signal)).join(" · ")}</span>` : ""}</div>
    <div class="atlas-source-review-actions"><button id="atlas-source-select-all">Selecionar tudo</button><button id="atlas-source-select-none">Limpar seleção</button><button id="atlas-source-export-json">Exportar JSON</button><button id="atlas-source-export-md">Exportar Markdown</button></div>
    ${hierarchy ? `<details class="atlas-source-hierarchy"><summary>Árvore editorial (${esc(review.headings?.length || 0)} títulos)</summary><div>${hierarchy}</div></details>` : ""}
    <div class="atlas-source-table-list">${rows || `<p>Não foram encontradas tabelas nesta captura.</p>`}</div>
    <footer><p>Objetivo: <b>${esc(title || review.title || "Sistema visual")}</b>. Cabeçalhos e notas do contexto acompanham as tabelas selecionadas.</p><button id="atlas-source-review-cancel">Cancelar</button><button class="primary" id="atlas-source-review-start">Confirmar e analisar</button></footer>
  </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  $("#atlas-source-review-close", modal).onclick = close;
  $("#atlas-source-review-cancel", modal).onclick = close;
  const inputs = () => [...modal.querySelectorAll("[data-source-table]")];
  $("#atlas-source-select-all", modal).onclick = () => inputs().forEach((input) => { input.checked = true; });
  $("#atlas-source-select-none", modal).onclick = () => inputs().forEach((input) => { input.checked = false; });
  const download = (name, content, type) => { const url = URL.createObjectURL(new Blob([content], { type })); const link = document.createElement("a"); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 0); };
  $("#atlas-source-export-json", modal).onclick = () => download(`${(review.title || "atlas-fonte").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.json`, JSON.stringify(loaded.structured || review, null, 2), "application/json");
  $("#atlas-source-export-md", modal).onclick = () => download(`${(review.title || "atlas-fonte").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.md`, loaded.markdown || "", "text/markdown;charset=utf-8");
  $("#atlas-source-review-start", modal).onclick = async () => {
    const button = $("#atlas-source-review-start", modal); button.disabled = true; button.textContent = "Iniciando…";
    const selection = { table_ids: inputs().filter((input) => input.checked).map((input) => input.dataset.sourceTable) };
    const result = await backend.startAtlasSource(S.activeSlug, sourceId, selection).catch((error) => ({ ok: false, error: String(error), error_kind: "internal" }));
    if (!result?.ok) { button.disabled = false; button.textContent = "Confirmar e analisar"; return toast(result?.error || "Não foi possível iniciar a análise.", true); }
    close(); atlasImportProgress(title || review.title, "gamefaqs"); await finishAtlasSourceImport(result, title || review.title);
  };
}

function openAtlasSourceWizard(replaceSystem = null) {
  closeAtlasSourceWizard();
  const modal = document.createElement("div");
  modal.id = "atlas-source-wizard"; modal.className = "modal-bg";
  modal.innerHTML = `<div class="atlas-source-wizard" role="dialog" aria-modal="true" aria-label="Nova fonte exclusiva do Atlas">
    <header><div><span>ATLAS / ${replaceSystem ? "TROCAR FONTE" : "NOVO SISTEMA"}</span><h2>${replaceSystem ? "Escolha a nova fonte exclusiva" : "Qual estrutura deseja mapear?"}</h2><p>A fonte anterior continuará no histórico de revisões.</p></div><button id="atlas-source-close">×</button></header>
    <label>Nome do sistema<input id="atlas-source-title" value="${esc(replaceSystem?.title || "")}" placeholder="Ex.: Árvore de habilidades, crafting, relacionamentos"></label>
    <div class="atlas-source-options"><button id="atlas-source-pdf"><span>▤</span><b>Importar PDF</b><small>Analisa texto, relações e requisitos deste arquivo.</small></button><button id="atlas-source-gamefaqs"><span>◎</span><b>Usar GameFAQs</b><small>Cole o endereço direto de um guia.</small></button></div>
    <div class="atlas-source-url" id="atlas-source-url-row" hidden><input id="atlas-source-url" placeholder="https://gamefaqs.gamespot.com/..."><button id="atlas-source-url-submit">Analisar guia</button></div>
    <p class="atlas-source-foot">A IA não poderá inventar relações ausentes. Fontes longas serão processadas em vários lotes e podem gerar custo no provedor. Sem IA configurada, a fonte será anexada ao editor manual.</p>
  </div>`;
  document.body.appendChild(modal);
  const title = () => ($("#atlas-source-title", modal)?.value || "").trim();
  $("#atlas-source-close", modal).onclick = closeAtlasSourceWizard;
  $("#atlas-source-pdf", modal).onclick = () => {
    if (!title()) return toast("Informe o nome do sistema.", true);
    const input = document.createElement("input"); input.type = "file"; input.accept = ".pdf,application/pdf";
    input.onchange = async () => {
      const file = input.files?.[0]; if (!file) return;
      const systemTitle = title(); closeAtlasSourceWizard(); atlasImportProgress(systemTitle, "pdf");
      try {
        const data = await fileToBase64(file);
        updateAtlasImportProgress("structure", "Extraindo texto, tabelas e referências do PDF…");
        const payload = { kind: "pdf", title: systemTitle, data, filename: file.name };
        const result = replaceSystem ? await backend.replaceGuideSystemSource(S.activeSlug, replaceSystem.id, payload).catch((error) => ({ ok: false, error: String(error), error_kind: "internal" })) : await backend.createGuideSystemPdf(S.activeSlug, systemTitle, data, file.name).catch((error) => ({ ok: false, error: String(error), error_kind: "internal" }));
        await finishAtlasSourceImport(result, systemTitle);
      } catch (error) {
        updateAtlasImportProgress("source", "Não foi possível abrir o arquivo selecionado.", {
          ok: false, error: String(error), error_kind: "source_import",
          error_code: "local_file_read", error_details: { hint: "Escolha o PDF novamente ou confira se outro programa o bloqueou." },
        });
      }
    };
    input.click();
  };
  $("#atlas-source-gamefaqs", modal).onclick = () => { $("#atlas-source-url-row", modal).hidden = false; $("#atlas-source-url", modal).focus(); };
  $("#atlas-source-url-submit", modal).onclick = async () => {
    const url = ($("#atlas-source-url", modal)?.value || "").trim();
    if (!title() || !url) return toast("Informe o nome e o endereço do guia.", true);
    const systemTitle = title(); closeAtlasSourceWizard(); atlasImportProgress(systemTitle, "gamefaqs");
    updateAtlasImportProgress("source", "Baixando e conferindo todas as páginas do GameFAQs…");
    const payload = { kind: "gamefaqs", title: systemTitle, url };
    const result = replaceSystem ? await backend.replaceGuideSystemSource(S.activeSlug, replaceSystem.id, payload).catch((error) => ({ ok: false, error: String(error), error_kind: "internal" })) : await backend.createGuideSystemGameFaqs(S.activeSlug, systemTitle, url).catch((error) => ({ ok: false, error: String(error), error_kind: "internal" }));
    await finishAtlasSourceImport(result, systemTitle);
  };
}

async function finishAtlasSourceImport(result, title) {
  if (!result?.ok) {
    updateAtlasImportProgress("source", "A importação não pôde continuar.", result || { ok: false });
    return toast(result?.error || "Não foi possível importar a fonte do Atlas.", true);
  }
  const sourceId = result.source?.id || result.source_id;
  if (result.phase === "awaiting_source_review") {
    closeAtlasImportProgress();
    return openAtlasSourceReview(sourceId, result.review, title);
  }
  if (["awaiting_configuration", "awaiting_consent"].includes(result.phase)) {
    closeAtlasImportProgress();
    toast(result.message || "Fonte anexada. Complete o sistema manualmente.");
    return openGuideSystemEditor(null, sourceId);
  }
  updateAtlasImportProgress("ai", "Fonte pronta. A IA iniciou a extração em lotes recuperáveis…");
  await esperar(350); closeAtlasImportProgress();
  toast("Fonte anexada. A IA está montando a prévia do sistema.");
  // Dashboard polling displays persisted job state; no loop tied to mutable activeSlug.
  await renderDashboard({ force: true });
  acompanharAtlasJob(S.activeSlug, sourceId);
}

async function acompanharAtlasJob(slug, sourceId) {
  if (!slug || !sourceId) return;
  S.atlasPolling ||= new Set();
  const key = `${slug}:${sourceId}`; if (S.atlasPolling.has(key)) return;
  S.atlasPolling.add(key);
  try {
    while (S.view === "dashboard" && S.activeSlug === slug) {
      const status = await appCall("get_atlas_job", slug, sourceId);
      if (!status?.ok || status.phase !== "running") {
        await renderDashboard({ force: true }); break;
      }
      await esperar(850);
      await renderDashboard({ force: true });
    }
  } finally { S.atlasPolling.delete(key); }
}

async function viewAtlasSource(systemId, nodeId = "") {
  const result = await backend.guideSystemSource(S.activeSlug, systemId).catch((error) => ({ ok: false, error: String(error) }));
  if (!result?.ok) return toast(result?.error || "Fonte não encontrada.", true);
  const source = result.source || {};
  const review = source.id ? await backend.atlasSourceReview(S.activeSlug, source.id).catch(() => ({ ok: false })) : { ok: false };
  const system = atlasSystems(S.dashboardGame || {}).find((item) => item.id === systemId);
  const node = system?.nodes?.find((item) => item.id === nodeId);
  const refs = new Set((node?.source_refs || []).map((ref) => `${ref.section}:${ref.block}`));
  const matches = (review.review?.tables || []).flatMap((table) => (table.row_refs || []).filter((row) => refs.has(`${row.ref?.section}:${row.ref?.block}`)).map((row) => ({ ...row, table })));
  const excerpt = matches.slice(0, 12).map((item) => {
    const rowNumber = (item.table.row_refs || []).findIndex((row) => row.id === item.id) + 1;
    const ref = item.ref || {};
    const locator = [`p. ${item.table.page}`, ref.section || ref.block ? `§${ref.section || 0}.${ref.block || 0}` : "", rowNumber > 0 ? `linha ${rowNumber}` : "", item.table.id ? `tabela ${item.table.id}` : ""].filter(Boolean).join(" · ");
    return `<div class="atlas-source-excerpt-row"><small>${esc(locator)} · ${esc(item.table.title || "Tabela")}</small><span>${esc((item.cells || []).join(" | "))}</span></div>`;
  }).join("");
  root.insertAdjacentHTML("beforeend", `<div class="gf-backdrop" id="atlas-source-view"><div class="gf-panel atlas-source-view"><h3>Fonte exclusiva do Atlas</h3><p><b>${esc(source.title || "Fonte do sistema")}</b></p><dl><div><dt>Tipo</dt><dd>${esc(source.kind || "legacy")}</dd></div><div><dt>Arquivo</dt><dd>${esc(source.filename || "—")}</dd></div><div><dt>URL</dt><dd>${source.url ? `<a href="${esc(source.url)}" target="_blank" rel="noreferrer">Abrir GameFAQs</a>` : "—"}</dd></div>${source.metadata?.pages ? `<div><dt>Páginas importadas</dt><dd>${esc(source.metadata.pages)}</dd></div>` : ""}</dl>${excerpt ? `<h4>Trechos referenciados</h4><div class="atlas-source-excerpts">${excerpt}</div>` : `<p>Esta fonte não contém um trecho estruturado para o nó selecionado.</p>`}<button class="btn-primary" id="atlas-source-view-close">Fechar</button></div></div>`);
  $("#atlas-source-view-close").onclick = () => $("#atlas-source-view")?.remove();
}

function openAtlasDiagnostics(system) {
  const diagnostics = system?._draftDiagnostics || {};
  const pending = diagnostics.pending_items || (diagnostics.pending_table_ids || []).map((tableId) => ({
    id: `legacy-${tableId}`, severity: "blocking", kind: "table_pending",
    table_id: tableId, table_title: "Tabela sem detalhe nesta versão",
    message: "A tabela selecionada não foi completamente interpretada.",
    action: "Revise a seleção da fonte ou processe novamente com outro modelo.",
  }));
  const warnings = diagnostics.warning_items || [];
  const refs = (item) => {
    const ref = item.source_ref || {};
    const parts = [];
    if (ref.page) parts.push(`p. ${ref.page}`);
    if (ref.section || ref.block) parts.push(`§${ref.section || 0}.${ref.block || 0}`);
    if (item.row_number) parts.push(`linha ${item.row_number}`);
    if (item.table_id) parts.push(item.table_id);
    return parts.join(" · ") || "referência não disponível";
  };
  const cardLabel = (item) => (item.card_numbers || []).filter(Boolean).map((number) => `<button type="button" class="atlas-diagnostic-card-link" data-atlas-diagnostic-card="${Number(number)}">Card #${String(number).padStart(3, "0")}</button>`).join(' <span aria-hidden="true">→</span> ') || "Nenhum card materializado";
  const itemHTML = (item, warning = false) => `<article class="atlas-diagnostic-item ${warning ? "warning" : "blocking"}">
    <div class="atlas-diagnostic-kicker"><span>${warning ? "AVISO" : "PENDÊNCIA"}</span><b>${cardLabel(item)}</b></div>
    <h4>${esc(item.message || "Interpretação incompleta")}</h4>
    <p>${esc(item.action || "Revise o trecho e processe novamente.")}</p>
    <small>${esc(item.table_title || "Tabela")} · ${esc(refs(item))}${item.kind ? ` · ${esc(item.kind)}` : ""}</small>
    ${item.row_preview ? `<pre>${esc(item.row_preview)}</pre>` : ""}
  </article>`;
  $("#atlas-diagnostics")?.remove();
  root.insertAdjacentHTML("beforeend", `<div class="gf-backdrop" id="atlas-diagnostics"><div class="gf-panel atlas-diagnostics-panel" role="dialog" aria-modal="true" aria-label="Diagnóstico do Atlas">
    <header><div><span>ATLAS / DIAGNÓSTICO DA REVISÃO</span><h3>${esc(system?.title || "Sistema visual")}</h3><p>Use o card, a tabela e a linha indicados para corrigir somente o trecho necessário.</p></div><button id="atlas-diagnostics-close">×</button></header>
    ${pending.length ? `<section><h4>Pendências que bloqueiam a aprovação (${pending.length})</h4><div class="atlas-diagnostic-list">${pending.map((item) => itemHTML(item)).join("")}</div></section>` : ""}
    ${warnings.length ? `<section><h4>Avisos para confirmar (${warnings.length})</h4><div class="atlas-diagnostic-list">${warnings.map((item) => itemHTML(item, true)).join("")}</div></section>` : ""}
    ${!pending.length && !warnings.length ? `<p class="atlas-diagnostics-empty">Nenhuma pendência detalhada foi registrada nesta revisão.</p>` : ""}
    <footer><button class="btn-primary" id="atlas-diagnostics-close-footer">Fechar</button></footer>
  </div></div>`);
  const close = () => $("#atlas-diagnostics")?.remove();
  $("#atlas-diagnostics-close")?.addEventListener("click", close);
  $("#atlas-diagnostics-close-footer")?.addEventListener("click", close);
  document.querySelectorAll("[data-atlas-diagnostic-card]").forEach((button) => button.addEventListener("click", async () => {
    const cardNumber = Number(button.dataset.atlasDiagnosticCard);
    const node = (system?.nodes || []).find((item) => Number(item.card_number || (system.nodes || []).indexOf(item) + 1) === cardNumber);
    if (!node) return toast("Esse card não está mais materializado nesta revisão.", true);
    S.guideAtlas.systemId = system.id; S.guideAtlas.nodeId = node.id; S.guideAtlas.mode = "focus";
    close(); await renderDashboard({ force: true });
  }));
}

function closeGuideSystemEditor() { document.getElementById("guide-system-editor")?.remove(); S.GSE = null; }

function normalizeGuideEditorCardNumbers() {
  const nodes = S.GSE?.nodes || [];
  nodes.forEach((node, index) => { node.card_number = index + 1; });
}

function syncGuideSystemEditor() {
  const E = S.GSE; if (!E) return;
  E.title = $("#gse-title")?.value || E.title;
  E.description = $("#gse-description")?.value || "";
  E.group_label = $("#gse-group-label")?.value || "Grupo";
  E.layout = $("#gse-layout")?.value || "layered";
  document.querySelectorAll("[data-gse-node]").forEach((row) => {
    const node = E.nodes[Number(row.dataset.gseNode)]; if (!node) return;
    node.label = row.querySelector("[data-node-label]")?.value || "";
    node.stage = row.querySelector("[data-node-stage]")?.value || "";
    node.group = row.querySelector("[data-node-group]")?.value || "";
    node.subtitle = row.querySelector("[data-node-subtitle]")?.value || "";
    node.media_query = row.querySelector("[data-node-media-query]")?.value || "";
    node.tags = (row.querySelector("[data-node-tags]")?.value || "").split(",").map((item) => item.trim()).filter(Boolean);
    node.spoiler = !!row.querySelector("[data-node-spoiler]")?.checked;
  });
  normalizeGuideEditorCardNumbers();
  document.querySelectorAll("[data-gse-edge]").forEach((row) => {
    const edge = E.edges[Number(row.dataset.gseEdge)]; if (!edge) return;
    edge.from = row.querySelector("[data-edge-from]")?.value || "";
    edge.to = row.querySelector("[data-edge-to]")?.value || "";
    edge.label = row.querySelector("[data-edge-label]")?.value || "";
    edge.path_kind = row.querySelector("[data-edge-kind]")?.value || "normal";
    edge.missable = !!row.querySelector("[data-edge-missable]")?.checked;
    const texts = (row.querySelector("[data-edge-requirement]")?.value || "").split("\n").map((item) => item.trim()).filter(Boolean);
    const previous = edge.requirements || [];
    edge.requirements = texts.map((text, index) => ({ ...(previous[index] || {}), id: previous[index]?.id || "", text, source_refs: previous[index]?.source_refs || edge.source_refs || [] }));
  });
}

function renderGuideSystemEditor() {
  let modal = document.getElementById("guide-system-editor");
  if (!modal) { modal = document.createElement("div"); modal.id = "guide-system-editor"; modal.className = "modal-bg"; document.body.appendChild(modal); }
  normalizeGuideEditorCardNumbers();
  const E = S.GSE, options = (selected) => E.nodes.map((node) => `<option value="${esc(node.id)}" ${node.id === selected ? "selected" : ""}>${esc(node.label || "Sem nome")}</option>`).join("");
  modal.innerHTML = `<div class="system-editor-panel" role="dialog" aria-modal="true" aria-label="Editor de sistema visual">
    <header><div><span>EDITOR GENÉRICO</span><h2>${E.id ? "Editar sistema visual" : "Criar sistema visual"}</h2></div><button id="gse-close">×</button></header>
    <div class="system-editor-scroll"><section class="system-editor-basics"><label>Título<input id="gse-title" value="${esc(E.title)}"></label><label>Rótulo dos grupos<input id="gse-group-label" value="${esc(E.group_label)}"></label><label>Layout<select id="gse-layout"><option value="layered" ${E.layout === "layered" ? "selected" : ""}>Em camadas</option><option value="vertical" ${E.layout === "vertical" ? "selected" : ""}>Vertical</option><option value="radial" ${E.layout === "radial" ? "selected" : ""}>Radial</option></select></label><label class="wide">Descrição<textarea id="gse-description">${esc(E.description)}</textarea></label></section>
    <section><div class="system-editor-title"><div><span>01</span><h3>Nós</h3><p>Entidades, estados, classes, receitas ou etapas. O número do card é automático e acompanha a ordem.</p></div><button id="gse-add-node">＋ Adicionar nó</button></div><div class="system-editor-list">${E.nodes.map((node, index) => { const number = Number(node.card_number || index + 1) || index + 1; return `<article data-gse-node="${index}"><div class="row-index"><b>#${String(number).padStart(3, "0")}</b><small>card</small></div><div class="node-fields"><input data-node-label value="${esc(node.label)}" placeholder="Nome"><input data-node-stage value="${esc(node.stage)}" placeholder="Estágio"><input data-node-group value="${esc(node.group)}" placeholder="Grupo"><input data-node-tags value="${esc((node.tags || []).join(", "))}" placeholder="Tags separadas por vírgula"><input class="wide" data-node-subtitle value="${esc(node.subtitle)}" placeholder="Descrição curta"><input class="wide" data-node-media-query value="${esc(node.media_query || "")}" placeholder="Consulta sugerida para imagem"><label><input data-node-spoiler type="checkbox" ${node.spoiler ? "checked" : ""}> spoiler</label></div><button data-remove-node="${index}" title="Remover">×</button></article>`; }).join("")}</div></section>
    <section><div class="system-editor-title"><div><span>02</span><h3>Caminhos e condições</h3><p>Relações documentadas entre os nós.</p></div><button id="gse-add-edge">＋ Adicionar caminho</button></div><div class="system-editor-list">${E.edges.map((edge, index) => `<article data-gse-edge="${index}"><div class="row-index">${index + 1}</div><div class="edge-fields"><select data-edge-from>${options(edge.from)}</select><span>→</span><select data-edge-to>${options(edge.to)}</select><input data-edge-label value="${esc(edge.label)}" placeholder="Rótulo do caminho"><select data-edge-kind><option value="normal" ${edge.path_kind === "normal" ? "selected" : ""}>Normal</option><option value="alternative" ${edge.path_kind === "alternative" ? "selected" : ""}>Alternativo</option><option value="optional" ${edge.path_kind === "optional" ? "selected" : ""}>Opcional</option></select><textarea class="wide" data-edge-requirement placeholder="Um requisito documentado por linha">${esc((edge.requirements || []).map((item) => item.text).join("\n"))}</textarea><label><input data-edge-missable type="checkbox" ${edge.missable ? "checked" : ""}> perdível</label></div><button data-remove-edge="${index}" title="Remover">×</button></article>`).join("")}</div></section></div>
    <footer><p>A fonte original não será modificada. Esta edição cria uma nova revisão atômica.</p><button id="gse-cancel">Cancelar</button><button class="primary" id="gse-save">Publicar nova revisão</button></footer>
  </div>`;
  $("#gse-close").onclick = closeGuideSystemEditor; $("#gse-cancel").onclick = closeGuideSystemEditor;
  $("#gse-add-node").onclick = () => { syncGuideSystemEditor(); E.nodes.push({ id: `manual-${Date.now()}`, card_number: E.nodes.length + 1, label: "Novo nó", subtitle: "", stage: "", group: "", tags: [], attributes: {}, media_query: "", spoiler: false, source_refs: [] }); renderGuideSystemEditor(); };
  $("#gse-add-edge").onclick = () => { syncGuideSystemEditor(); if (E.nodes.length < 2) return toast("Adicione ao menos dois nós.", true); E.edges.push({ id: "", from: E.nodes[0].id, to: E.nodes[1].id, label: "", path_kind: "normal", requirements: [], missable: false, spoiler: false, source_refs: [] }); renderGuideSystemEditor(); };
  modal.querySelectorAll("[data-remove-node]").forEach((button) => button.onclick = () => { syncGuideSystemEditor(); const removed = E.nodes.splice(Number(button.dataset.removeNode), 1)[0]; E.edges = E.edges.filter((edge) => edge.from !== removed.id && edge.to !== removed.id); normalizeGuideEditorCardNumbers(); renderGuideSystemEditor(); });
  modal.querySelectorAll("[data-remove-edge]").forEach((button) => button.onclick = () => { syncGuideSystemEditor(); E.edges.splice(Number(button.dataset.removeEdge), 1); renderGuideSystemEditor(); });
  $("#gse-save").onclick = async () => {
    syncGuideSystemEditor(); const button = $("#gse-save"); button.disabled = true; button.textContent = "Validando…";
    const result = E._draftSource ? await appCall("approve_atlas_job", S.activeSlug, E._draftSource, E)
      : await backend.saveGuideSystem(S.activeSlug, E).catch((error) => ({ ok: false, error: String(error) }));
    if (!result?.ok) { button.disabled = false; button.textContent = "Publicar nova revisão"; return toast(result?.error || "Sistema inválido.", true); }
    S.guideAtlas.draftSource = ""; S.guideAtlas.systemId = result.system?.id || E.id; closeGuideSystemEditor(); toast("Sistema visual publicado em uma nova revisão."); await renderDashboard({ force: true });
  };
}

function bindGuideAtlas(game) {
  if (S.tab !== "atlas" || !game) return;
  const systems = atlasSystems(game);
  const system = systems.find((item) => item.id === S.guideAtlas.systemId) || systems.find((item) => item.status !== "rejected");
  const selected = system?.nodes?.find((node) => node.id === S.guideAtlas.nodeId);
  const rerender = () => renderDashboard({ force: true });
  $("#atlas-path")?.addEventListener("change", async event => { const r = await appCall("set_guide_system_path", game.slug, system.id, event.target.value); if (!r.ok) toast(r.error,true); await rerender(); });
  root.querySelectorAll("[data-atlas-review-job]").forEach((b) => b.onclick = () => { S.guideAtlas.draftSource = b.dataset.atlasReviewJob; S.guideAtlas.systemId = ""; rerender(); });
  root.querySelectorAll("[data-atlas-review-source]").forEach((b) => b.onclick = async () => {
    const review = await backend.atlasSourceReview(game.slug, b.dataset.atlasReviewSource).catch((error) => ({ ok: false, error: String(error) }));
    if (!review?.ok) return toast(review?.error || "Não foi possível abrir a revisão da fonte.", true);
    openAtlasSourceReview(b.dataset.atlasReviewSource, review.review, review.source?.title || "Fonte do Atlas");
  });
  $("#atlas-open-diagnostics")?.addEventListener("click", () => openAtlasDiagnostics(system));
  $("#atlas-review-pending-source")?.addEventListener("click", async () => {
    if (!system._draftSource) return;
    const loaded = await backend.atlasSourceReview(game.slug, system._draftSource).catch((error) => ({ ok: false, error: String(error) }));
    if (!loaded?.ok) return toast(loaded?.error || "Não foi possível abrir a revisão da fonte.", true);
    openAtlasSourceReview(system._draftSource, {
      ...(loaded.review || {}),
      pending_table_ids: (system._draftDiagnostics || {}).pending_table_ids || [],
    }, loaded.source?.title || system.title || "Fonte do Atlas");
  });
  root.querySelectorAll("[data-atlas-manual-job]").forEach((b) => b.onclick = () => openGuideSystemEditor(null, b.dataset.atlasManualJob));
  root.querySelectorAll("[data-atlas-settings]").forEach((b) => b.onclick = () => enterSettings("ai", { slug: game.slug, tab: "atlas" }));
  for (const [attr, method] of [["atlasCancelJob", "cancel_atlas_job"], ["atlasRetryJob", "retry_atlas_job"]]) {
    root.querySelectorAll(attr === "atlasCancelJob" ? "[data-atlas-cancel-job]" : "[data-atlas-retry-job]").forEach((b) => b.onclick = async () => {
      b.disabled = true; const r = await appCall(method, game.slug, b.dataset[attr]);
      if (!r.ok) { b.disabled = false; toast(r.error, true); }
      else {
        await rerender();
        if (method === "retry_atlas_job" && r.phase === "awaiting_source_review") openAtlasSourceReview(r.source_id || b.dataset[attr], r.review, r.source?.title || "Fonte do Atlas");
        else if (method === "retry_atlas_job") acompanharAtlasJob(game.slug, r.source_id || b.dataset[attr]);
      }
    });
  }
  $("#atlas-back-published")?.addEventListener("click", () => { S.guideAtlas.draftSource = ""; rerender(); });
  root.querySelectorAll("[data-atlas-mode]").forEach((button) => button.addEventListener("click", () => { S.guideAtlas.mode = button.dataset.atlasMode; rerender(); }));
  $("#atlas-list-toggle")?.addEventListener("click", () => { S.guideAtlas.mode = S.guideAtlas.mode === "list" ? "map" : "list"; rerender(); });
  $("#atlas-system")?.addEventListener("change", (event) => { S.guideAtlas.systemId = event.target.value; S.guideAtlas.nodeId = ""; S.guideAtlas.group = "all"; S.guideAtlas.tag = "all"; rerender(); });
  $("#atlas-group")?.addEventListener("change", (event) => { S.guideAtlas.group = event.target.value; rerender(); });
  $("#atlas-tag")?.addEventListener("change", (event) => { S.guideAtlas.tag = event.target.value; rerender(); });
  $("#atlas-availability")?.addEventListener("change", (event) => { S.guideAtlas.availability = event.target.value; rerender(); });
  $("#atlas-search")?.addEventListener("change", (event) => { S.guideAtlas.search = event.target.value; rerender(); });
  $("#atlas-spoilers")?.addEventListener("change", (event) => { S.guideAtlas.spoilers = event.target.checked; rerender(); });
  root.querySelectorAll("[data-atlas-node]").forEach((button) => {
    button.onclick = () => { S.guideAtlas.nodeId = button.dataset.atlasNode; rerender(); };
    button.onkeydown = (event) => {
      const current = button.dataset.atlasNode;
      const edge = event.key === "ArrowRight" ? system?.edges?.find((item) => item.from === current)
        : event.key === "ArrowLeft" ? system?.edges?.find((item) => item.to === current) : null;
      const target = edge ? (event.key === "ArrowRight" ? edge.to : edge.from) : "";
      if (target) { event.preventDefault(); S.guideAtlas.nodeId = target; rerender().then(() => root.querySelector(`[data-atlas-node="${target}"]`)?.focus()); }
    };
  });
  root.querySelectorAll("[data-atlas-zoom]").forEach((button) => button.onclick = () => {
    const action = button.dataset.atlasZoom;
    if (action === "fit") {
      const view = $("#atlas-viewport"), world = view?.querySelector(".atlas-world");
      S.guideAtlas.zoom = Math.max(.1, Math.min(1.6, (view.clientWidth - 32) / world.offsetWidth, (view.clientHeight - 32) / world.offsetHeight));
      S.guideAtlas.panX = (view.clientWidth - world.offsetWidth * S.guideAtlas.zoom) / 2;
      S.guideAtlas.panY = (view.clientHeight - world.offsetHeight * S.guideAtlas.zoom) / 2;
    }
    else S.guideAtlas.zoom = Math.max(.1, Math.min(1.6, S.guideAtlas.zoom + (action === "in" ? .12 : -.12)));
    rerender();
  });
  const viewport = $("#atlas-viewport");
  if (viewport) {
    let start = null, origin = null;
    viewport.onmousedown = (event) => { if (event.target.closest("button,input,select,label")) return; start = [event.clientX, event.clientY]; origin = [S.guideAtlas.panX, S.guideAtlas.panY]; viewport.classList.add("panning"); };
    window.onmousemove = (event) => { if (!start) return; S.guideAtlas.panX = origin[0] + event.clientX - start[0]; S.guideAtlas.panY = origin[1] + event.clientY - start[1]; viewport.closest(".atlas-shell")?.style.setProperty("--atlas-x", `${S.guideAtlas.panX}px`); viewport.closest(".atlas-shell")?.style.setProperty("--atlas-y", `${S.guideAtlas.panY}px`); };
    window.onmouseup = () => { start = null; viewport.classList.remove("panning"); };
    viewport.onwheel = (event) => { event.preventDefault(); S.guideAtlas.zoom = Math.max(.1, Math.min(1.6, S.guideAtlas.zoom + (event.deltaY < 0 ? .08 : -.08))); viewport.closest(".atlas-shell")?.style.setProperty("--atlas-zoom", S.guideAtlas.zoom); };
  }
  $("#atlas-create")?.addEventListener("click", () => openAtlasSourceWizard());
  root.querySelectorAll("#atlas-edit").forEach((button) => button.onclick = () => openGuideSystemEditor(system));
  $("#atlas-approve")?.addEventListener("click", async () => { const result = system._draftSource ? await appCall("approve_atlas_job", game.slug, system._draftSource) : await backend.saveGuideSystem(game.slug, { ...system, status: "approved" }); if (!result?.ok) return toast(result?.error || "Falha ao aprovar.", true); S.guideAtlas.draftSource = ""; toast("Sistema aprovado."); await rerender(); });
  $("#atlas-reject")?.addEventListener("click", async () => { const result = system._draftSource ? await appCall("cancel_atlas_job", game.slug, system._draftSource) : await backend.saveGuideSystem(game.slug, { ...system, status: "rejected" }); if (!result?.ok) return toast(result?.error || "Falha ao rejeitar.", true); S.guideAtlas.draftSource = ""; S.guideAtlas.systemId = ""; toast("Sugestão rejeitada; a fonte permanece intacta."); await rerender(); });
  $("#atlas-goal")?.addEventListener("click", async () => { if (!selected || system._draftSource) return; const current = game.smart_guide?.system_state?.goals?.[system.id]; const result = await backend.setGuideSystemGoal(S.activeSlug, system.id, current === selected.id ? "" : selected.id); if (!result?.ok) return toast(result?.error || "Falha ao fixar objetivo.", true); toast(current === selected.id ? "Objetivo removido." : "Objetivo enviado ao overlay."); await rerender(); });
  root.querySelectorAll("[data-atlas-requirement]").forEach((input) => input.onchange = async () => { const result = await backend.updateGuideRequirement(S.activeSlug, system.id, input.dataset.edge, input.dataset.atlasRequirement, input.checked); if (!result?.ok) { input.checked = !input.checked; return toast(result?.error || "Falha ao salvar requisito.", true); } await rerender(); });
  $("#atlas-media")?.addEventListener("click", () => selected && openGuideMedia(selected.media_query || `${game.title} ${selected.label}`, { systemId: system.id, nodeId: selected.id, sourceId: system._draftSource || "" }));
  $("#atlas-replace-source")?.addEventListener("click", () => openAtlasSourceWizard(system));
  const deleteSystem = async () => {
    if (system._draftSource) return toast("Rejeite a prévia antes de excluir um sistema.", true);
    if (!window.confirm(`Excluir o sistema visual “${system.title}”? A fonte original ficará preservada para reimportação.`)) return;
    const result = await backend.deleteGuideSystem(game.slug, system.id);
    if (!result?.ok) return toast(result?.error || "Falha ao excluir o sistema.", true);
    S.guideAtlas.systemId = ""; S.guideAtlas.nodeId = ""; S.guideAtlas.draftSource = "";
    toast("Sistema visual excluído; a fonte foi preservada.");
    await rerender();
  };
  $("#atlas-delete")?.addEventListener("click", deleteSystem);
  $("#atlas-delete-header")?.addEventListener("click", deleteSystem);
  $("[data-atlas-source]")?.addEventListener("click", () => viewAtlasSource(system.id, selected?.id || ""));
  if (system?._draftSource) root.querySelectorAll('#atlas-goal,#atlas-image,#atlas-path,[data-atlas-requirement]').forEach(el => {el.disabled=true;el.title='Aprove o sistema antes de alterar progresso.';});
}

function showMissableDetails(game) {
  if (!game) return toast("Não foi possível carregar os perdíveis. Abra o jogo novamente.", true);
  const smart = game.smart_guide || {};
  const progress = smart.effective_progress || smart.progress || {};
  const completed = new Set(progress.completed || []);
  const official = (game.pending_missables || []).filter(a => !a.hardcore);
  const guide = (smart.current?.chapters || []).flatMap(chapter =>
    (chapter.blocks || []).filter(b => b.type === 'missable' && !completed.has(b.id))
      .map(block => ({ ...block, chapter: chapter.title })));
  const modal = xpModal('Perdíveis pendentes', `<p>Confira estes avisos antes de avançar. Conquistas oficiais só são resolvidas em Hardcore.</p>
    <div class="missable-details">${official.map(a => `<article><small>RETROACHIEVEMENTS · OFICIAL</small><h3>${esc(a.name || a.title || 'Conquista perdível')}</h3><p>${esc(a.desc || a.description || 'Descrição indisponível. Sincronize o jogo novamente.')}</p><b>${a.earned ? 'Refazer em Hardcore' : 'Pendente em Hardcore'}</b><p>${esc(a.area || 'Etapa ainda não identificada')}</p></article>`).join('')}
    ${guide.map(b => `<article><small>AVISO DO GUIA</small><h3>${esc(b.title || 'Aviso perdível')}</h3><p>${esc(b.text || 'Consulte a etapa no guia.')}</p><ul>${(b.items || []).map(item => `<li>${esc(typeof item === 'string' ? item : item.text || item.title || '')}</li>`).join('')}</ul><p>${esc(b.chapter || 'Etapa ainda não identificada')}</p></article>`).join('')}
    ${official.length || guide.length ? '' : '<p>Nenhum perdível pendente nesta sincronização.</p>'}</div>
    ${official.length ? '<button type="button" class="primary" data-missables-list>Ver em Conquistas</button>' : ''}`);
  modal.querySelector('[data-missables-list]')?.addEventListener('click', () => {
    modal.remove(); S.tab = 'achievements'; S.achievementFilter = 'missable'; renderDashboard({ force: true });
  });
}

function bindSidebar() {
  root.querySelector('[data-open-missables]')?.addEventListener('click', () => showMissableDetails(S.dashboardGame));
  bindGuideAtlas(S.dashboardGame);
  $("#hall-entry")?.addEventListener("click", enterHall);
  $("#library-open-hall")?.addEventListener("click", enterHall);
  root.querySelectorAll(".tile").forEach((b) => {
    b.onclick = async () => {
      S.activeSlug = b.dataset.slug;
      await backend.setActiveGame(S.activeSlug).catch(() => null);
      closeLibraryDrawer();
      if (S.view === "hall") await enterDashboard();
      else await renderDashboard();
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
    b.onclick = () => { S.tab = b.dataset.tab; if (S.tab === "journey") S.guideReader = false; renderDashboard(); };
  });
  root.querySelectorAll("[data-ach-filter]").forEach((b) => {
    b.onclick = () => {
      S.achievementFilter = b.dataset.achFilter || "all";
      renderDashboard({ force: true });
    };
  });
  $("[data-open-achievements]")?.addEventListener("click", () => {
    S.tab = "achievements";
    renderDashboard({ force: true });
  });
  $("#smart-generate")?.addEventListener("click", gerarSmartGuide);
  $("#guide-consent")?.addEventListener("click", () => enterSettings("experience", { slug: S.activeSlug, tab: "journey" }));
  $("#guide-config-ai")?.addEventListener("click", () => enterSettings("ai", { slug: S.activeSlug, tab: "journey" }));
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
    S.tab = "journey"; S.guideReader = true;
    const id = b.dataset.jumpGuide;
    const chapters = S.dashboardGame?.smart_guide?.current?.chapters || [];
    const index = chapters.findIndex((chapter) => (chapter.blocks || []).some((block) => block.id === id));
    if (index >= 0) S.guideChapter = index;
    renderDashboard({ force: true }).then(() => {
      const id = b.dataset.jumpGuide; (id ? document.getElementById(`guide-${id}`) : $(".guide-console-head"))?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  $("#guide-reader-open")?.addEventListener("click", () => { S.guideReader = true; S.guideChapter = Math.max(0, S.guideChapter || 0); renderDashboard({ force: true }); });
  $("#guide-reader-close")?.addEventListener("click", () => { S.guideReader = false; renderDashboard({ force: true }); });
  $("#guide-chapter-prev")?.addEventListener("click", () => { S.guideChapter = Math.max(0, S.guideChapter - 1); S.guideQuery = ""; renderDashboard({ force: true }); });
  $("#guide-chapter-next")?.addEventListener("click", () => { S.guideChapter += 1; S.guideQuery = ""; renderDashboard({ force: true }); });
  const openSources = () => enterSettings("library", { slug: S.activeSlug, tab: "journey" });
  $("#journey-sources")?.addEventListener("click", openSources);
  $("#journey-sources-empty")?.addEventListener("click", openSources);
  $("#journey-reader-sources")?.addEventListener("click", openSources);
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
  const [estado, ia, aiUsage, sources, compact, overlay, update, guideSources] = await Promise.all([
    backend.appState().catch(() => ({})),
    backend.getAiConfig().catch(() => ({ ok: false })),
    backend.getAiUsageStatus().catch(() => ({ ok: false, providers: {} })),
    backend.getSourcesConfig().catch(() => ({ ok: false })),
    backend.getCompactConfig().catch(() => null),
    backend.overlayStatus().catch((e) => ({ ok: false, error: String(e) })),
    backend.updateStatus().catch((e) => ({ ok: false, error: String(e) })),
    S.activeSlug ? backend.walkthroughSources(S.activeSlug).catch((e) => ({ ok: false, error: String(e), sources: [] })) : Promise.resolve({ ok: true, sources: [], merge: { phase: "idle" } }),
  ]);
  S.SET = {
    estado,
    ia: ia && ia.ok ? ia : null,
    aiUsage: aiUsage && aiUsage.ok ? aiUsage : null,
    sources: sources && sources.ok ? sources.ready : {},
    compact: compact && compact.ok ? compact : { width: 300, height: 232, last: 2, next: 0 },
    overlay,
    update,
    guideSources: { slug: S.activeSlug || "", ...(guideSources || { sources: [], merge: { phase: "idle" } }) },
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
  if (S.SET.section === "library" && S.SET.guideSources?.merge?.phase === "running") pollWalkthroughMerge();
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
    S.tab = back.tab || "journey";
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
    compact_summary_enabled: cc.surfaces?.summary?.enabled ?? ["minimal", "both"].includes(cc.view || cc.variant || "minimal"),
    compact_details_enabled: cc.surfaces?.details?.enabled ?? ["expanded", "both"].includes(cc.view || cc.variant || "minimal"),
    compact_summary_corner: cc.surfaces?.summary?.corner || "top-right",
    compact_details_corner: cc.surfaces?.details?.corner || "bottom-right",
    compact_size_mode: cc.size_mode || "auto",
    compact_view: cc.view || cc.variant || "minimal",
    compact_width: Number(cc.width || 320), compact_height: Number(cc.height || 110),
    compact_expanded_width: Number(cc.expanded_width || 420),
    compact_expanded_height: Number(cc.expanded_height || 300),
    compact_last: Math.min(3, Number(cc.last ?? 3)), compact_next: Number(cc.next || 0),
    compact_corner: cc.corner || "auto",
    compact_background_mode: cc.background_mode || "background",
    compact_hotkey: cc.hotkey || "ctrl+alt+g",
    compact_edit_hotkey: cc.edit_hotkey || "ctrl+alt+e",
    compact_auto_expand: !!cc.auto_expand,
    compact_auto_collapse_seconds: Number(cc.auto_collapse_seconds || 0),
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

function guideSourcesSettingsHTML() {
  const state = S.SET.guideSources || { slug: S.activeSlug || "", sources: [], merge: { phase: "idle" } };
  const sources = state.sources || [];
  const merge = state.merge || { phase: "idle" };
  const selected = sources.filter((source) => source.enabled !== false);
  const characters = selected.reduce((sum, source) => sum + Number(source.character_count || 0), 0);
  const conflicts = Array.isArray(merge.conflicts) ? merge.conflicts : [];
  const unresolved = conflicts.filter((conflict) => conflict.blocking && !conflict.resolution).length;
  const sourceCards = sources.map((source) => `<article class="guide-source-card ${source.enabled === false ? "off" : ""}">
    <label class="guide-source-enabled"><input type="checkbox" data-guide-source-toggle="${esc(source.id)}" ${source.enabled === false ? "" : "checked"}><span></span></label>
    <div class="guide-source-icon">${source.kind === "pdf" ? "▤" : source.kind === "gamefaqs" ? "◎" : source.kind === "legacy" ? "↺" : "≡"}</div>
    <div class="guide-source-copy"><b>${esc(source.title)}</b><span>${esc(source.kind || "guia")} · ${source.section_count || 0} seções · ${Number(source.character_count || 0).toLocaleString("pt-BR")} caracteres</span><small>${source.enabled === false ? "Fora da próxima consolidação" : "Incluída na próxima consolidação"}</small></div>
    <div class="guide-source-actions"><button data-guide-source-view="${esc(source.id)}">Visualizar</button><button data-guide-source-rename="${esc(source.id)}">Renomear</button><button data-guide-source-export="${esc(source.id)}">Exportar</button><button class="danger" data-guide-source-remove="${esc(source.id)}">Remover</button></div>
  </article>`).join("");
  const conflictHTML = conflicts.length ? `<div class="guide-merge-conflicts"><h4>Divergências para revisar</h4>${conflicts.map((conflict) => `<article class="guide-conflict ${conflict.blocking ? "blocking" : ""}"><div><span>${conflict.blocking ? "DECISÃO OBRIGATÓRIA" : "DIFERENÇA DESCRITIVA"}</span><h5>${esc(conflict.subject || "Divergência")}</h5><p>${esc(conflict.recommendation || "Compare as alternativas das fontes.")}</p></div><div class="guide-conflict-options">${(conflict.alternatives || []).map((choice) => `<button class="${conflict.resolution === choice.id ? "selected" : ""}" data-guide-conflict="${esc(conflict.id)}" data-guide-choice="${esc(choice.id)}"><b>${esc(choice.label || choice.text || choice.id)}</b><small>${esc(choice.source_title || choice.source_id || "Fonte")}</small></button>`).join("")}</div></article>`).join("")}</div>` : "";
  const phaseText = { idle: "Nenhuma consolidação em andamento.", running: merge.message || "Consolidando as fontes…", awaiting_configuration: merge.message || "Aguardando configuração da IA.", awaiting_consent: merge.message || "Aguardando consentimento para uso da IA.", awaiting_review: merge.message || "Prévia pronta para revisão.", published: "Walkthrough consolidado publicado.", error: merge.error || "Falha na consolidação." }[merge.phase] || merge.message || "";
  return `<div class="settings-card guide-sources-card"><div class="guide-sources-head"><div><h3>Fontes dos guias</h3><p class="set-hint">Combine até dez fontes. O conteúdo original permanece separado e só muda a Jornada quando você publicar uma nova consolidação.</p></div><span>${sources.length}/10</span></div>
    ${sourceCards || `<div class="guide-sources-empty"><b>Nenhuma fonte adicionada</b><span>Importe um PDF, GameFAQs ou texto para começar.</span></div>`}
    <div class="guide-source-add"><button id="guide-source-add-pdf">＋ PDF</button><button id="guide-source-add-gamefaqs">◎ GameFAQs</button><button id="guide-source-add-text">≡ Colar texto</button></div>
    <div class="guide-merge-box ${merge.phase === "error" ? "error" : ""}"><div><span>WALKTHROUGH CONSOLIDADO</span><b>${selected.length} fonte(s) · ~${characters.toLocaleString("pt-BR")} caracteres</b><small>${esc(phaseText)}</small></div>${merge.phase === "running" ? `<i class="guide-merge-spinner">✦</i>` : `<button id="guide-merge-start" ${selected.length ? "" : "disabled"}>Criar walkthrough completo</button>`}</div>
    ${conflictHTML}
    ${merge.phase === "awaiting_review" ? `<div class="guide-merge-publish"><div><b>${unresolved ? `${unresolved} conflito(s) importante(s) pendente(s)` : "Prévia validada e pronta"}</b><span>A versão atual continua ativa até a publicação.</span></div><button id="guide-merge-publish" ${unresolved ? "disabled" : ""}>Publicar na Jornada</button></div>` : ""}
  </div>`;
}

async function refreshGuideSources(slug = S.activeSlug) {
  if (!slug || !S.SET) return;
  const result = await backend.walkthroughSources(slug).catch((error) => ({ ok: false, error: String(error), sources: [] }));
  S.SET.guideSources = { slug, ...(result || {}) };
  if (S.SET.section === "library") renderSettings();
}

function openGuideSourceDialog(kind) {
  $("#guide-source-dialog")?.remove();
  const game = S.library.find((item) => item.slug === S.activeSlug) || S.dashboardGame || {};
  root.insertAdjacentHTML("beforeend", `<div class="gf-backdrop" id="guide-source-dialog"><div class="gf-panel guide-source-dialog"><h3>${kind === "gamefaqs" ? "Adicionar GameFAQs" : "Adicionar texto"}</h3><p>Fonte para a Jornada de <b>${esc(game.title || "jogo atual")}</b>.</p><label>Título<input id="guide-source-dialog-title" placeholder="Nome que identifica esta fonte"></label>${kind === "gamefaqs" ? `<label>URL direta do guia<input id="guide-source-dialog-value" placeholder="https://gamefaqs.gamespot.com/..."></label>` : `<label>Conteúdo<textarea id="guide-source-dialog-value" placeholder="Cole o guia completo aqui"></textarea></label>`}<div class="settings-pending-actions"><button class="btn-primary" id="guide-source-dialog-save">Adicionar fonte</button><button class="btn-ghost" id="guide-source-dialog-close">Cancelar</button></div></div></div>`);
  $("#guide-source-dialog-close").onclick = () => $("#guide-source-dialog")?.remove();
  $("#guide-source-dialog-save").onclick = async () => {
    const title = ($("#guide-source-dialog-title")?.value || "").trim();
    const value = ($("#guide-source-dialog-value")?.value || "").trim();
    if (!value) return toast("Informe o conteúdo da fonte.", true);
    const button = $("#guide-source-dialog-save"); button.disabled = true; button.textContent = "Importando…";
    const result = kind === "gamefaqs" ? await backend.addWalkthroughGameFaqs(S.activeSlug, value).catch((error) => ({ ok: false, error: String(error) })) : await backend.addWalkthroughText(S.activeSlug, title || "Texto colado", value).catch((error) => ({ ok: false, error: String(error) }));
    if (!result?.ok) { button.disabled = false; button.textContent = "Adicionar fonte"; return toast(result?.error || "Falha ao importar.", true); }
    $("#guide-source-dialog")?.remove(); toast(result.duplicate ? "Esta fonte já estava na coleção." : "Fonte adicionada."); await refreshGuideSources();
  };
}

function confirmWalkthroughMerge() {
  const state = S.SET.guideSources || {};
  const selected = (state.sources || []).filter((source) => source.enabled !== false);
  const characters = selected.reduce((sum, source) => sum + Number(source.character_count || 0), 0);
  $("#guide-merge-confirm")?.remove();
  root.insertAdjacentHTML("beforeend", `<div class="gf-backdrop" id="guide-merge-confirm"><div class="gf-panel guide-merge-confirm"><h3>Criar walkthrough completo?</h3><p>A IA processará <b>${selected.length} fonte(s)</b>, aproximadamente <b>${characters.toLocaleString("pt-BR")} caracteres</b>. Seu provedor poderá cobrar pelo processamento.</p><p>A versão atual continuará ativa até você revisar conflitos e publicar.</p><div class="settings-pending-actions"><button class="btn-primary" id="guide-merge-confirm-start">Iniciar consolidação</button><button class="btn-ghost" id="guide-merge-confirm-close">Cancelar</button></div></div></div>`);
  $("#guide-merge-confirm-close").onclick = () => $("#guide-merge-confirm")?.remove();
  $("#guide-merge-confirm-start").onclick = async () => {
    const button = $("#guide-merge-confirm-start"); button.disabled = true; button.textContent = "Iniciando…";
    const result = await backend.startWalkthroughMerge(S.activeSlug, selected.map((source) => source.id)).catch((error) => ({ ok: false, error: String(error) }));
    $("#guide-merge-confirm")?.remove();
    if (!result?.ok && !["awaiting_configuration", "awaiting_consent"].includes(result?.phase)) return toast(result?.error || "Falha ao iniciar.", true);
    toast(result?.message || "Consolidação iniciada."); await refreshGuideSources(); pollWalkthroughMerge();
  };
}

async function pollWalkthroughMerge() {
  while (S.view === "settings" && S.SET?.section === "library" && S.activeSlug) {
    const result = await backend.walkthroughMergeStatus(S.activeSlug).catch((error) => ({ ok: false, phase: "error", error: String(error) }));
    if (S.SET?.guideSources) S.SET.guideSources.merge = result;
    renderSettings();
    if (result.phase !== "running") return;
    await esperar(900);
  }
}

async function viewWalkthroughSource(sourceId) {
  const result = await backend.walkthroughSource(S.activeSlug, sourceId).catch((error) => ({ ok: false, error: String(error) }));
  if (!result?.ok) return toast(result?.error || "Fonte não encontrada.", true);
  const source = result.source || {};
  const text = source.text || (source.sections || []).map((section) => `${section.title || ""}\n${(section.blocks || []).map((block) => block.text || "").join("\n")}`).join("\n\n");
  root.insertAdjacentHTML("beforeend", `<div class="gf-backdrop" id="guide-source-view"><div class="gf-panel guide-source-view"><h3>${esc(source.title)}</h3><p>${esc(source.kind)} · conteúdo original preservado</p><pre>${esc(text.slice(0, 100000))}</pre><button class="btn-primary" id="guide-source-view-close">Fechar</button></div></div>`);
  $("#guide-source-view-close").onclick = () => $("#guide-source-view")?.remove();
}

function aiUsageHTML(status, provider) {
  const bucket = status?.providers?.[provider] || {};
  const providerMeta = status?.provider_meta?.[provider] || {};
  const limitsUrl = providerMeta.limits_url || (status?.provider === provider ? status?.limits_url : "");
  const health = bucket.health || "unknown";
  const label = {
    healthy: "API respondendo", degraded: "Serviço instável",
    rate_limited: "Limite temporário atingido", error: "Configuração com erro",
    unknown: "Ainda não testada",
  }[health] || "Status desconhecido";
  const retry = Math.ceil(Number(bucket.retry_after_seconds || 0));
  return `<div class="settings-card ai-usage-card ${esc(health)}">
    <div class="ai-usage-head"><div><h3>Uso e limite da API</h3><p class="set-hint">${esc(status?.quota_note || "Medição local desta execução.")}</p></div><span>${esc(label)}</span></div>
    <div class="ai-usage-grid"><div><b>${Number(bucket.requests || 0).toLocaleString("pt-BR")}</b><span>requisições</span></div><div><b>${Number(bucket.retries || 0).toLocaleString("pt-BR")}</b><span>novas tentativas</span></div><div><b>${Number(bucket.total_tokens || 0).toLocaleString("pt-BR")}</b><span>tokens observados</span></div><div><b>${Number(bucket.rate_limit_events || 0).toLocaleString("pt-BR")}</b><span>limites detectados</span></div></div>
    ${retry ? `<p class="ai-limit-wait">Nova tentativa recomendada em aproximadamente <b>${retry}s</b>.</p>` : ""}
    <div class="ai-usage-actions"><button class="btn-ghost" id="ai-usage-refresh">Atualizar status</button>${limitsUrl ? `<button class="btn-ghost" id="ai-usage-provider" data-url="${esc(limitsUrl)}">Abrir painel do provedor ↗</button>` : ""}</div>
  </div>`;
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
  let panel = id === "account" ? settingsPanelFrame(id, `<div class="settings-card"><h3>Conta conectada</h3><div class="set-row"><div><div class="set-txt">RetroAchievements</div><div class="set-sub" data-private>${estado.username ? esc(estado.username) : "não conectada"}</div></div><button class="btn-ghost" id="set-reconnect">Trocar conta</button></div></div>
    <div class="settings-card"><h3>Atualizações</h3><div class="set-row"><div><div class="set-txt">DigiTracker ${esc(estado.version || S.version)}</div><div class="set-sub">${upText}</div></div><button class="btn-ghost" id="set-update-check">Procurar agora</button></div>${settingsToggle("auto_check_updates", draft.auto_check_updates, "Procurar atualizações ao iniciar", "Apenas releases estáveis; a instalação sempre pede confirmação")}</div>`)
  : id === "experience" ? settingsPanelFrame(id, `<div class="settings-card"><h3>Experiência DigiTracker Console</h3><p class="set-hint">Combina a apresentação cinematográfica da PSN, a navegação do Steam Deck e a identidade do DigiTracker. O Guia Inteligente nunca apaga sua fonte importada.</p>${settingsToggle("smart_guide_auto", draft.smart_guide_auto, "Organizar guias automaticamente", "Depois de cada importação, cria uma revisão compacta e validada")}${settingsToggle("smart_guide_consent", draft.smart_guide_consent, "Permitir envio do guia à IA", "O provedor configurado pode cobrar pelo processamento. Imagens pesquisadas continuam exigindo aprovação")}${settingsToggle("reduced_motion", draft.reduced_motion, "Reduzir animações", "Remove transições de profundidade e movimentos não essenciais")}</div><div class="settings-card"><div class="experience-grid"><div><label class="set-label">Densidade</label><select class="set-field" data-draft-field="guide_density"><option value="comfortable" ${draft.guide_density === "comfortable" ? "selected" : ""}>Confortável adaptável</option><option value="compact" ${draft.guide_density === "compact" ? "selected" : ""}>Compacta</option></select></div><div><label class="set-label">Escala da interface: <b id="settings-scale-label">${draft.ui_scale}%</b></label><input class="set-range" data-draft-field="ui_scale" type="range" min="80" max="140" step="5" value="${draft.ui_scale}"></div></div></div>`)
  : id === "ai" ? settingsPanelFrame(id, ia ? `<div class="settings-card"><h3>Provedor ativo</h3><div class="ai-providers">${ia.providers.map((p) => `<button class="ai-prov ${p.id === draft.provider ? "on" : ""}" data-settings-provider="${esc(p.id)}"><span class="ai-prov-name">${esc(p.label)}</span>${p.has_key ? `<span class="ai-prov-ok">✓ chave salva</span>` : ""}</button>`).join("")}</div></div><div class="settings-card"><label class="set-label">Chave da API${(ia.providers.find((p) => p.id === draft.provider) || {}).has_key ? " (salva — deixe em branco para manter)" : ""}</label>${field("api_key", "", "password", `placeholder="${((ia.providers.find((p) => p.id === draft.provider) || {}).has_key) ? "••••••••••••••••" : "cole a chave aqui"}" autocomplete="off"`)}<button class="btn-ghost settings-inline-action ${draft.clear_key ? "selected" : ""}" data-ai-clear>${draft.clear_key ? "Chave será removida" : "Remover chave salva"}</button><label class="set-label">Modelo</label>${field("model", draft.model, "text", "autocomplete=off")}${(ia.providers.find((p) => p.id === draft.provider) || {}).needs_base_url ? `<label class="set-label">Endpoint (OpenRouter, Ollama, LM Studio…)</label>${field("base_url", draft.base_url, "text", "autocomplete=off")}` : ""}<p class="set-hint">A chave fica só em <code>config/secrets.json</code>, nesta máquina.</p></div>${aiUsageHTML(S.SET.aiUsage, draft.provider)}` : `<div class="settings-card"><h3>Inteligência artificial</h3><p class="set-hint">Indisponível no modo demonstração.</p></div>`)
  : id === "images" ? settingsPanelFrame(id, `<p class="set-hint settings-intro">Configure as fontes opcionais de capas e fundos. As credenciais só serão enviadas quando você salvar esta sessão.</p>${[["steamgriddb","SteamGridDB","Capas da comunidade.",ready.steamgriddb],["rawg","RAWG","Fundos e screenshots para jogos retrô.",ready.rawg],["igdb","IGDB","Capas de qualidade via Twitch.",ready.igdb]].map(([key,label,sub,has]) => `<div class="src-cfg settings-card"><h3>${label} ${has ? "✓" : ""}</h3><p class="set-hint">${sub}</p>${field(`${key}.key1`, "", key === "igdb" ? "text" : "password", `placeholder="${has ? "•••••••• (salva — em branco mantém)" : (key === "igdb" ? "Twitch Client ID" : "chave da API")}" autocomplete="off"`)}${key === "igdb" ? field(`${key}.key2`, "", "password", `placeholder="${has ? "•••• (segredo salvo — em branco mantém)" : "Twitch Client Secret"}" autocomplete="off"`) : ""}<button class="btn-ghost settings-inline-action ${draft[key].clear ? "selected" : ""}" data-source-clear="${key}">${draft[key].clear ? "Fonte será removida" : "Remover credencial salva"}</button></div>`).join("")}`)
  : id === "library" ? settingsPanelFrame(id, `<div class="settings-card"><h3>Entrada de jogos</h3>${settingsToggle("auto_import", draft.auto_import, "Importar jogos novos automaticamente", "Verifica a cada 5 minutos e traz os jogos em que você começou a jogar")}</div>${guideSourcesSettingsHTML()}`)
  : id === "overlay" ? settingsPanelFrame(id, `<div class="settings-card"><h3>Comportamento</h3>${settingsToggle("auto_overlay", draft.auto_overlay, "Grudar no emulador", "Vira overlay e acompanha a janela quando um emulador abre")}${settingsToggle("overlay_exit_fullscreen", draft.overlay_exit_fullscreen, "Sair do fullscreen exclusivo", "Manda Alt+Enter para o emulador quando autorizado")}${settingsToggle("overlay_second_screen", draft.overlay_second_screen, "Usar o segundo monitor", "Leva o overlay para a tela que o jogo não ocupa")}${settingsToggle("overlay_fit_emulator", draft.overlay_fit_emulator, "Ajustar ao tamanho do emulador", "Mantém o overlay proporcional à janela do emulador")}</div><div class="overlay-diag settings-card ${ov.detected ? "ok" : ""}"><h3>Diagnóstico de detecção</h3><div class="set-sub">${ovState}</div><div class="overlay-diag-grid"><span>Área interna</span><code>${esc(ovRect)}</code><span>Overlay</span><code>${esc((ov.overlay_size || []).join(" × ") || "—")}</code><span>Posição</span><code>${esc((ov.dock || []).join(", ") || "—")}</code><span>Modo nativo</span><code>${esc(ov.native_input_mode || "—")}</code><span>Hotkey</span><code>${esc(ov.hotkey || "—")}${ov.hotkey_error ? ` · ${esc(ov.hotkey_error)}` : ""}</code></div><button class="btn-ghost" id="overlay-test">Testar detecção agora</button></div>`)
  : settingsPanelFrame(id, `<div class="settings-card"><h3>HUD passivo</h3><p class="set-hint">Durante a gameplay, os HUDs passam cliques e não capturam teclado, mouse ou controle. Use a hotkey de edição para arrastar, redimensionar ou trocar o visual.</p><div class="set-grid2"><div><label class="set-label">Visual ao abrir</label><select class="set-field" data-draft-field="compact_view"><option value="minimal" ${draft.compact_view === "minimal" ? "selected" : ""}>Resumo mínimo</option><option value="expanded" ${draft.compact_view === "expanded" ? "selected" : ""}>Troféus</option><option value="both" ${draft.compact_view === "both" ? "selected" : ""}>Resumo + troféus</option></select></div><div><label class="set-label">Tamanho</label><select class="set-field" data-draft-field="compact_size_mode"><option value="auto" ${draft.compact_size_mode === "auto" ? "selected" : ""}>Adaptar ao emulador</option><option value="manual" ${draft.compact_size_mode === "manual" ? "selected" : ""}>Manual</option></select></div><div><label class="set-label">HUD mínimo · largura</label>${field("compact_width", draft.compact_width, "number", "min=260 max=380")}</div><div><label class="set-label">HUD mínimo · altura</label>${field("compact_height", draft.compact_height, "number", "min=90 max=130")}</div><div><label class="set-label">HUD de troféus · largura</label>${field("compact_expanded_width", draft.compact_expanded_width, "number", "min=340 max=560")}</div><div><label class="set-label">HUD de troféus · altura</label>${field("compact_expanded_height", draft.compact_expanded_height, "number", "min=220 max=480")}</div><div><label class="set-label">Canto</label><select class="set-field" data-draft-field="compact_corner"><option value="auto" ${draft.compact_corner === "auto" ? "selected" : ""}>Automático (preferir superior direito)</option><option value="top-right" ${draft.compact_corner === "top-right" ? "selected" : ""}>Superior direito</option><option value="bottom-right" ${draft.compact_corner === "bottom-right" ? "selected" : ""}>Inferior direito</option><option value="top-left" ${draft.compact_corner === "top-left" ? "selected" : ""}>Superior esquerdo</option><option value="bottom-left" ${draft.compact_corner === "bottom-left" ? "selected" : ""}>Inferior esquerdo</option></select></div></div></div><div class="settings-card"><h3>Fundo do modo compacto</h3><p class="set-hint">O painel é sempre opaco. A arte escolhida aparece dentro do HUD com uma máscara escura para manter o texto legível.</p><div class="set-grid2"><div><label class="set-label">Arte usada no HUD</label><select class="set-field" data-draft-field="compact_background_mode"><option value="background" ${draft.compact_background_mode === "background" ? "selected" : ""}>Fundo do jogo (recomendado)</option><option value="cover" ${draft.compact_background_mode === "cover" ? "selected" : ""}>Capa do jogo</option><option value="title" ${draft.compact_background_mode === "title" ? "selected" : ""}>Arte de título</option><option value="solid" ${draft.compact_background_mode === "solid" ? "selected" : ""}>Cor sólida</option></select></div><div class="settings-art-action"><label class="set-label">Fundo do jogo atual</label><button class="btn-ghost" id="compact-art-picker" ${S.activeSlug ? "" : "disabled"}>Escolher imagem…</button></div></div></div><div class="settings-card"><h3>Hotkeys e recuperação</h3><div class="set-grid2"><div><label class="set-label">Mostrar / ocultar</label>${field("compact_hotkey", draft.compact_hotkey, "text", "placeholder=ctrl+alt+g autocomplete=off")}</div><div><label class="set-label">Editar por 15 segundos</label>${field("compact_edit_hotkey", draft.compact_edit_hotkey, "text", "placeholder=ctrl+alt+e autocomplete=off")}</div><div>${settingsToggle("compact_auto_expand", draft.compact_auto_expand, "Mostrar troféus ao obter conquista", "Desligado por padrão para preservar a imersão")}</div><div><label class="set-label">Recolher automaticamente (segundos)</label>${field("compact_auto_collapse_seconds", draft.compact_auto_collapse_seconds, "number", "min=0 max=60")}</div></div></div><div class="settings-card"><h3>Conquistas responsivas</h3><p class="set-hint">O HUD calcula automaticamente quantas linhas completas cabem. São exibidas no máximo três conquistas recentes e o espaço restante recebe as próximas.</p><div class="set-grid2"><div><label class="set-label">Máximo de obtidas recentes</label>${field("compact_last", draft.compact_last, "number", "min=0 max=3")}</div></div></div>`);

  if (id === "compact") {
    const corners = (value) => `<option value="top-right" ${value === "top-right" ? "selected" : ""}>Superior direito</option><option value="bottom-right" ${value === "bottom-right" ? "selected" : ""}>Inferior direito</option><option value="top-left" ${value === "top-left" ? "selected" : ""}>Superior esquerdo</option><option value="bottom-left" ${value === "bottom-left" ? "selected" : ""}>Inferior esquerdo</option>`;
    const surfaceCard = `<div class="settings-card compact-surfaces-card"><h3>Janelas independentes</h3><p class="set-hint">Resumo e Conquistas/Guia são janelas separadas. Você pode usar uma ou ambas e posicioná-las individualmente.</p>${settingsToggle("compact_summary_enabled", draft.compact_summary_enabled, "Mostrar Resumo", "Capa, próximo objetivo e progresso")}${settingsToggle("compact_details_enabled", draft.compact_details_enabled, "Mostrar Conquistas/Guia", "Painel separado com as duas abas")}<div class="set-grid2"><div><label class="set-label">Canto inicial do Resumo</label><select class="set-field" data-draft-field="compact_summary_corner">${corners(draft.compact_summary_corner)}</select></div><div><label class="set-label">Canto inicial de Conquistas/Guia</label><select class="set-field" data-draft-field="compact_details_corner">${corners(draft.compact_details_corner)}</select></div></div></div>`;
    panel = panel.replace(`<div class="settings-panel-scroll" id="settings-panel-scroll">`, `<div class="settings-panel-scroll" id="settings-panel-scroll">${surfaceCard}`);
  }
  if (id === "images") {
    const webCard = `<div class="settings-card"><h3>Google Imagens / Web ✓</h3><p class="set-hint">Fonte padrão, sem chave. A consulta direta ao Google é experimental; se houver CAPTCHA ou mudança no site, o DigiTracker usa resultados relevantes do Yandex e, por último, do Bing. O mecanismo usado é identificado e nenhuma arte é aplicada sem sua escolha.</p></div>`;
    panel = panel.replace(`<div class="settings-panel-scroll" id="settings-panel-scroll">`, `<div class="settings-panel-scroll" id="settings-panel-scroll">${webCard}`);
  }
  root.innerHTML = `<div class="view"><div class="wiz-head"><button class="back" id="set-back" title="Voltar">←</button><div><div class="t">Configurações</div><div class="s">Central de controle · sessão independente</div></div></div><div class="settings"><div class="settings-shell"><nav class="settings-nav" aria-label="Categorias das configurações"><p>Preferências</p>${Object.entries(SETTINGS_META).map(([key,meta]) => `<button class="set-nav-btn ${id === key ? "active" : ""}" data-set-target="${key}"><span>${meta[0]}</span><span>${esc(meta[1])}</span>${settingsHasChanges(key) ? "<i>•</i>" : ""}</button>`).join("")}</nav><main class="settings-inner">${panel}</main></div></div></div>`;
  if (id === "compact") {
    // Os controles legados continuam no DOM apenas para migração das builds
    // antigas, mas não disputam a decisão com as duas superfícies novas.
    root.querySelector('[data-draft-field="compact_view"]')?.closest("div")?.setAttribute("hidden", "");
    root.querySelector('[data-draft-field="compact_corner"]')?.closest("div")?.setAttribute("hidden", "");
  }
  const scroll = $("#settings-panel-scroll");
  if (scroll) { scroll.scrollTop = S.SET.scroll?.[id] || 0; scroll.onscroll = () => { S.SET.scroll[id] = scroll.scrollTop; }; }
  $("#set-back").onclick = leaveSettings;
  $("#set-reconnect")?.addEventListener("click", () => { S.mode = "real"; renderSetup(); });
  $("#set-update-check")?.addEventListener("click", () => procurarAtualizacao(true));
  $("#overlay-test")?.addEventListener("click", testarOverlay);
  $("#compact-art-picker")?.addEventListener("click", async () => {
    const game = S.activeSlug ? await backend.game(S.activeSlug).catch(() => null) : null;
    if (game) openCoverPicker(game, "background");
  });
  $("#settings-save")?.addEventListener("click", () => saveSettingsSession());
  $("#settings-discard")?.addEventListener("click", () => discardSettingsSession());
  root.querySelectorAll("[data-set-target]").forEach((b) => b.addEventListener("click", () => requestSettingsSection(b.dataset.setTarget)));
  root.querySelectorAll("[data-draft-toggle]").forEach((b) => b.addEventListener("click", () => { const key = b.dataset.draftToggle; S.SET.drafts[id][key] = !S.SET.drafts[id][key]; b.classList.toggle("on", S.SET.drafts[id][key]); b.setAttribute("aria-checked", String(S.SET.drafts[id][key])); markSettingsDirty(); }));
  root.querySelectorAll("[data-draft-field]").forEach((input) => {
    const update = () => { setDraftValue(input.dataset.draftField, input.value); if (input.dataset.draftField === "ui_scale") $("#settings-scale-label").textContent = `${input.value}%`; markSettingsDirty(); };
    input.addEventListener("input", update); input.addEventListener("change", update);
  });
  root.querySelectorAll("[data-settings-provider]").forEach((b) => b.addEventListener("click", () => { S.SET.drafts.ai.provider = b.dataset.settingsProvider; S.SET.drafts.ai.model = ""; S.SET.drafts.ai.base_url = ""; markSettingsDirty(); renderSettings(); }));
  $("[data-ai-clear]")?.addEventListener("click", () => { S.SET.drafts.ai.clear_key = !S.SET.drafts.ai.clear_key; renderSettings(); });
  $("#ai-usage-refresh")?.addEventListener("click", async () => {
    S.SET.aiUsage = await backend.getAiUsageStatus().catch(() => null); renderSettings();
  });
  $("#ai-usage-provider")?.addEventListener("click", () => {
    const url = $("#ai-usage-provider")?.dataset.url; if (url) backend.openImageSource(url);
  });
  root.querySelectorAll("[data-source-clear]").forEach((b) => b.addEventListener("click", () => { const key = b.dataset.sourceClear; S.SET.drafts.images[key].clear = !S.SET.drafts.images[key].clear; renderSettings(); }));
  if (id === "library") bindGuideSourceSettings();
}

function bindGuideSourceSettings() {
  $("#guide-source-add-pdf")?.addEventListener("click", () => {
    const input = document.createElement("input"); input.type = "file"; input.accept = ".pdf,application/pdf";
    input.onchange = async () => {
      const file = input.files?.[0]; if (!file) return;
      const data = await fileToBase64(file);
      const result = await backend.addWalkthroughPdf(S.activeSlug, data, file.name).catch((error) => ({ ok: false, error: String(error) }));
      if (!result?.ok) return toast(result?.error || "Falha ao importar o PDF.", true);
      toast(result.duplicate ? "Este PDF já estava na coleção." : "PDF adicionado às fontes."); await refreshGuideSources();
    };
    input.click();
  });
  $("#guide-source-add-gamefaqs")?.addEventListener("click", () => openGuideSourceDialog("gamefaqs"));
  $("#guide-source-add-text")?.addEventListener("click", () => openGuideSourceDialog("text"));
  root.querySelectorAll("[data-guide-source-toggle]").forEach((input) => input.onchange = async () => {
    const result = await backend.updateWalkthroughSource(S.activeSlug, input.dataset.guideSourceToggle, null, input.checked).catch((error) => ({ ok: false, error: String(error) }));
    if (!result?.ok) { input.checked = !input.checked; return toast(result?.error || "Falha ao atualizar a fonte.", true); }
    S.SET.guideSources.sources = result.sources || []; renderSettings();
  });
  root.querySelectorAll("[data-guide-source-view]").forEach((button) => button.onclick = () => viewWalkthroughSource(button.dataset.guideSourceView));
  root.querySelectorAll("[data-guide-source-rename]").forEach((button) => button.onclick = async () => {
    const source = (S.SET.guideSources.sources || []).find((item) => item.id === button.dataset.guideSourceRename);
    const title = window.prompt("Novo nome da fonte:", source?.title || "");
    if (title === null || !title.trim()) return;
    const result = await backend.updateWalkthroughSource(S.activeSlug, button.dataset.guideSourceRename, title.trim(), null).catch((error) => ({ ok: false, error: String(error) }));
    if (!result?.ok) return toast(result?.error || "Falha ao renomear.", true);
    S.SET.guideSources.sources = result.sources || []; renderSettings();
  });
  root.querySelectorAll("[data-guide-source-export]").forEach((button) => button.onclick = async () => {
    const result = await backend.walkthroughSource(S.activeSlug, button.dataset.guideSourceExport).catch((error) => ({ ok: false, error: String(error) }));
    if (!result?.ok) return toast(result?.error || "Falha ao exportar.", true);
    const source = result.source || {}; const blob = new Blob([JSON.stringify(source, null, 2)], { type: "application/json" });
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${source.title || "fonte-guia"}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
  root.querySelectorAll("[data-guide-source-remove]").forEach((button) => button.onclick = async () => {
    if (!window.confirm("Remover esta fonte? O walkthrough já publicado não será alterado.")) return;
    const result = await backend.removeWalkthroughSource(S.activeSlug, button.dataset.guideSourceRemove).catch((error) => ({ ok: false, error: String(error) }));
    if (!result?.ok) return toast(result?.error || "Falha ao remover.", true);
    S.SET.guideSources.sources = result.sources || []; renderSettings();
  });
  $("#guide-merge-start")?.addEventListener("click", confirmWalkthroughMerge);
  root.querySelectorAll("[data-guide-conflict]").forEach((button) => button.onclick = async () => {
    const result = await backend.resolveWalkthroughConflict(S.activeSlug, button.dataset.guideConflict, button.dataset.guideChoice).catch((error) => ({ ok: false, error: String(error) }));
    if (!result?.ok) return toast(result?.error || "Falha ao resolver o conflito.", true);
    S.SET.guideSources.merge.conflicts = result.conflicts || []; renderSettings();
  });
  $("#guide-merge-publish")?.addEventListener("click", async () => {
    const button = $("#guide-merge-publish"); button.disabled = true; button.textContent = "Publicando…";
    const result = await backend.publishWalkthroughMerge(S.activeSlug).catch((error) => ({ ok: false, error: String(error) }));
    if (!result?.ok) { button.disabled = false; button.textContent = "Publicar na Jornada"; return toast(result?.error || "Falha ao publicar.", true); }
    toast("Walkthrough consolidado publicado na Jornada."); await refreshGuideSources();
  });
}

function setDraftValue(path, value) {
  const parts = String(path).split("."); let obj = S.SET.drafts[S.SET.section];
  for (let i = 0; i < parts.length - 1; i++) obj = obj[parts[i]];
  const key = parts[parts.length - 1]; obj[key] = ["ui_scale", "compact_width", "compact_height", "compact_expanded_width", "compact_expanded_height", "compact_last", "compact_next", "compact_auto_collapse_seconds"].includes(key) ? Number(value) : value;
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
      const payload = id === "compact" ? {
        ...draft,
        compact_surfaces: {
          version: 1,
          summary: { enabled: !!draft.compact_summary_enabled, corner: draft.compact_summary_corner },
          details: { enabled: !!draft.compact_details_enabled, corner: draft.compact_details_corner },
        },
      } : draft;
      if (id === "compact") {
        delete payload.compact_summary_enabled;
        delete payload.compact_details_enabled;
        delete payload.compact_summary_corner;
        delete payload.compact_details_corner;
      }
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
  if (id === "compact") {
    const legacyView = draft.compact_summary_enabled && draft.compact_details_enabled ? "both" : (draft.compact_details_enabled ? "expanded" : "minimal");
    if (draft.compact_details_enabled) {
      S.compactTab = "achievements";
      if (S.activeSlug) await backend.setCompactTab(S.activeSlug, "achievements").catch(() => null);
    }
    S.compactCfg = { ok: true, width: draft.compact_width, height: draft.compact_height, expanded_width: draft.compact_expanded_width, expanded_height: draft.compact_expanded_height, last: Math.min(3, draft.compact_last), next: 0, size_mode: draft.compact_size_mode, tab: S.compactTab, view: legacyView, variant: legacyView, surfaces: { version: 1, summary: { enabled: !!draft.compact_summary_enabled, corner: draft.compact_summary_corner }, details: { enabled: !!draft.compact_details_enabled, corner: draft.compact_details_corner } }, corner: draft.compact_corner, background_mode: draft.compact_background_mode, opacity: 100, hotkey: draft.compact_hotkey, edit_hotkey: draft.compact_edit_hotkey, auto_expand: draft.compact_auto_expand, auto_collapse_seconds: draft.compact_auto_collapse_seconds };
  }
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
  { id: "icon", label: "Ícone" },
  { id: "both", label: "Ambos" },
];
const CV_SOURCES = [
  { id: "web", label: "Buscar na web" },
  { id: "steamgriddb", label: "SteamGridDB" },
  { id: "rawg", label: "RAWG" },
  { id: "igdb", label: "IGDB" },
  { id: "url", label: "Colar URL" },
];
const CV_SRC_LABEL = { steamgriddb: "SteamGridDB", rawg: "RAWG", igdb: "IGDB" };

async function openCoverPicker(game, initialRole = "cover") {
  const cfg = await backend.getSourcesConfig().catch(() => ({ ready: {} }));
  const ready = (cfg && cfg.ready) || {};
  const first = "web";
  S.CV = {
    slug: game.slug, title: game.title, query: `${game.title} ${game.platform || ""}`.trim(),
    source: first, ready, role: initialRole, returnView: S.view, urlValue: "",
    busy: false, error: "", blocked: false, openUrl: "", page: 0, safe: "moderate",
    provider: "google", fallbackUsed: false, webResults: [],
    palette: game.palette || { primary: corDe(game), secondary: corDe(game) },
    matches: [], chosen: null, covers: [], heroes: [],
  };
  renderCoverPicker();
  cvBuscar("");
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
    <div class="cv-cell-wrap">
    <button class="cv-cell ${cls}" data-url="${esc(c.url)}" data-web-index="${V.source === "web" ? (V.webResults || []).indexOf(c) : ""}" title="Aplicar (${esc(cvRoleLabel())})">
      <img src="${esc(c.thumb)}" alt="" loading="lazy" />
      ${c.width && c.height ? `<span>${c.width}×${c.height}</span>` : ""}
    </button>${c.source_page || c.source ? `<button class="cv-origin" data-origin="${esc(c.source_page || c.source)}" title="Abrir página original">${esc(c.host || c.provider || "origem")}</button>` : ""}
    </div>`;
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
  } else if (V.source === "web") {
    corpo = `<div class="search-box">
        <span style="color:var(--text-low)">🔎</span>
        <input id="cv-q" placeholder="Nome do jogo + plataforma" aria-label="Buscar arte na web"
               autocomplete="off" spellcheck="false" value="${esc(V.query || "")}" />
        <button class="cv-go" id="cv-go">Buscar</button>
      </div>
      <div class="cv-web-options"><label>SafeSearch <select id="cv-safe"><option value="strict" ${V.safe === "strict" ? "selected" : ""}>Rigoroso</option><option value="moderate" ${V.safe === "moderate" ? "selected" : ""}>Padrão</option><option value="off" ${V.safe === "off" ? "selected" : ""}>Desativado</option></select></label><span>${V.fallbackUsed ? `Google indisponível · resultados relevantes do ${V.provider === "yandex" ? "Yandex" : "Bing"}` : "Google Imagens · aprovação manual"}</span></div>
      ${V.busy ? `<div class="status-msg">⏳ Buscando artes na web…</div>` : ""}
      ${V.error ? `<div class="gf-error">${esc(V.error)}</div>` : ""}
      ${V.blocked || V.fallbackUsed ? `<button class="btn-primary" id="cv-open-web">Abrir esta busca no Google Imagens</button>${V.blocked && !capas && !fundos ? `<p class="cv-url-hint">Depois, use “Colar URL” para aprovar a imagem escolhida.</p>` : ""}` : ""}
      ${capas ? `<p class="cv-grid-label">CAPAS</p><div class="cv-grid">${capas}</div>` : ""}
      ${fundos ? `<p class="cv-grid-label">FUNDOS</p><div class="cv-grid wide">${fundos}</div>` : ""}
      ${!V.busy && !V.blocked && !capas && !fundos ? `<p class="cv-empty">Nenhuma imagem encontrada. Ajuste a consulta ou abra a busca no navegador.</p>` : ""}
      ${!V.busy && (capas || fundos) ? `<button class="btn-ghost cv-load-more" id="cv-more">Carregar mais</button>` : ""}`;
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
          !["url","web"].includes(s.id) && !V.ready[s.id] ? " off" : ""}" data-src="${s.id}"
          title="${!["url","web"].includes(s.id) && !V.ready[s.id] ? "sem chave configurada" : ""}">${s.label}</button>`).join("")}
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
      <button class="btn-ghost" id="cv-undo">↶ Desfazer</button>
      <div class="cv-palette"><label title="Cor primária do jogo"><input type="color" id="cv-primary" value="${esc(V.palette?.primary || "#2F9DFF")}"></label><label title="Cor secundária"><input type="color" id="cv-secondary" value="${esc(V.palette?.secondary || V.palette?.primary || "#66D7FF")}"></label><button class="btn-ghost" id="cv-palette-save">Salvar cores</button><button class="btn-ghost" id="cv-palette-auto">Extrair da arte</button></div>
    </div>
  </div>`;

  $("#cv-x").onclick = closeCoverPicker;
  $("#cv-settings")?.addEventListener("click", () => { closeCoverPicker(); enterSettings(); });
  $("#cv-clear")?.addEventListener("click", cvLimpar);
  $("#cv-undo")?.addEventListener("click", async () => {
    const result = await appCall("undo_game_art", V.slug);
    if (!result.ok) return toast(result.error, true);
    V.palette = result.palette; renderCoverPicker(); toast("Arte e paleta anteriores restauradas.");
  });
  $("#cv-palette-save")?.addEventListener("click", async () => {
    const res = await backend.setGamePalette(V.slug, $("#cv-primary").value, $("#cv-secondary").value).catch((e) => ({ ok: false, error: String(e) }));
    if (!res?.ok) return toast(res?.error || "Não foi possível salvar as cores.", true);
    V.palette = res.palette; toast("Cores do jogo atualizadas.");
  });
  $("#cv-palette-auto")?.addEventListener("click", async () => {
    const res = await backend.recalculateGamePalette(V.slug).catch((e) => ({ ok: false, error: String(e) }));
    if (!res?.ok) return toast(res?.error || "Não foi possível extrair as cores.", true);
    V.palette = res.palette; renderCoverPicker(); toast("Paleta recalculada pela arte.");
  });
  el.querySelectorAll(".cv-src").forEach((b) =>
    b.onclick = () => cvTrocarFonte(b.dataset.src));
  el.querySelectorAll(".cv-role").forEach((b) =>
    b.onclick = () => { V.role = b.dataset.role; V.page = 0; renderCoverPicker(); if (V.source === "web") cvBuscar(V.query); });
  const go = $("#cv-go");
  if (go) go.onclick = () => cvBuscar(($("#cv-q")?.value || "").trim());
  const q = $("#cv-q");
  if (q) q.onkeydown = (e) => { if (e.key === "Enter") cvBuscar((q.value || "").trim()); };
  $("#cv-safe")?.addEventListener("change", (e) => { V.safe = e.target.value; V.page = 0; cvBuscar(V.query); });
  $("#cv-more")?.addEventListener("click", () => { V.page += 1; cvBuscar(V.query, true); });
  $("#cv-open-web")?.addEventListener("click", () => backend.openWebImageSearch(V.slug, V.query, V.safe, V.role));
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
    b.onclick = () => cvAplicar(b.dataset.url,
      V.source === "web" ? V.webResults?.[Number(b.dataset.webIndex)] : null));
  el.querySelectorAll("[data-origin]").forEach((b) =>
    b.onclick = () => backend.openImageSource(b.dataset.origin));
}

function cvRoleLabel() {
  return (CV_ROLES.find((r) => r.id === S.CV?.role) || CV_ROLES[0]).label;
}

function cvTrocarFonte(source) {
  const V = S.CV;
  if (!V || V.source === source) return;
  V.source = source; V.error = "";
  V.matches = []; V.chosen = null; V.covers = []; V.heroes = []; V.webResults = [];
  renderCoverPicker();
  if (source === "web" || (source !== "url" && V.ready[source])) cvBuscar("");
}

async function cvBuscar(query, append = false) {
  const V = S.CV;
  if (!V) return;
  V.busy = true; V.error = ""; V.blocked = false; V.fallbackUsed = false;
  if (query) V.query = query;
  renderCoverPicker();
  try {
    const res = V.source === "web"
      ? await backend.webImageSearch(V.slug, query || "", V.page || 0, V.safe || "moderate", V.role || "cover")
      : await backend.coversSearch(V.slug, query || "", V.source);
    V.busy = false;
    if (!res.ok) {
      V.error = res.error || "Não consegui buscar artes.";
      V.blocked = !!res.blocked; V.openUrl = res.open_url || "";
    } else {
      if (V.source === "web") {
        const results = res.results || [];
        V.provider = res.provider || "google"; V.fallbackUsed = !!res.fallback_used;
        V.webResults = append ? [...V.webResults, ...results] : results;
        const covers = results.filter((item) => V.role === "icon" || !item.width || !item.height || item.orientation !== "landscape");
        const heroes = results.filter((item) => item.orientation === "landscape");
        V.covers = append ? [...V.covers, ...covers] : covers;
        V.heroes = append ? [...V.heroes, ...heroes] : heroes;
        V.blocked = !!res.blocked; V.openUrl = res.open_google_url || res.open_url || ""; V.chosen = { name: V.query };
      } else {
      V.matches = res.matches || [];
      V.chosen = res.chosen || null;
      V.covers = res.covers || [];
      V.heroes = res.heroes || [];
      }
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

async function cvAplicar(url, candidate = null, approved = false) {
  const V = S.CV;
  if (!V) return;
  url = (url || "").trim();
  if (!url) return toast("Cole uma URL de imagem.", true);
  if (!approved && typeof xpCompareArt === "function" && S.mode !== "demo") return xpCompareArt(V, url, candidate);
  const role = V.role;
  V.busy = true; V.error = ""; renderCoverPicker();
  if (candidate) candidate = { ...candidate, query: candidate.query || V.query };
  const res = await (candidate
    ? backend.applyWebArt(V.slug, candidate, role)
    : backend.setGameCover(V.slug, url, role)).catch((e) => ({ ok: false, error: "" + e }));
  if (!res || !res.ok) {
    V.busy = false; V.error = (res && res.error) || "Não consegui aplicar a arte.";
    renderCoverPicker();
    return;
  }
  const returnView = V.returnView;
  closeCoverPicker();
  toast(role === "both" ? "Capa e fundo atualizados." : role === "background" ? "Fundo atualizado." : "Capa atualizada.");
  if (returnView === "settings") await renderSettings();
  else await renderDashboard({ force: true });
}

async function cvLimpar() {
  const V = S.CV;
  if (!V) return;
  const role = V.role;
  const res = await backend.clearGameCover(V.slug, role).catch((e) => ({ ok: false, error: "" + e }));
  if (!res || !res.ok) return toast(res && res.error ? res.error : "Não consegui remover.", true);
  const returnView = V.returnView;
  closeCoverPicker();
  toast("Voltou à arte padrão da RA.");
  if (returnView === "settings") await renderSettings();
  else await renderDashboard({ force: true });
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
const ABAS = ["journey", "achievements", "atlas"];

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
  if (["mastery", "walk"].includes(S.tab)) S.tab = "achievements";
  if (["overview", "tips"].includes(S.tab)) S.tab = "journey";
  const i = Math.max(0, ABAS.indexOf(S.tab));
  S.tab = ABAS[(i + dir + ABAS.length) % ABAS.length];
  renderDashboard({ force: true }).then(() => $(".ptab.active")?.focus());
}

function bindAtalhos() {
  document.addEventListener("keydown", async (e) => {
    // O overlay fica sobre o jogo. Não deixe os atalhos globais (setas, C,
    // Esc etc.) consumirem a entrada enquanto o usuário joga com teclado.
    // Cliques continuam funcionando porque esta proteção cobre somente
    // eventos de teclado.
    if (S.compact && S.compactPassive) return;
    // não sequestra digitação em campos de texto
    const alvo = e.target;
    if (alvo && (alvo.tagName === "INPUT" || alvo.tagName === "TEXTAREA" || alvo.tagName === "SELECT" || alvo.isContentEditable)) {
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

    if (!["dashboard", "experience", "hall", "settings"].includes(S.view)) return;

    if (["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(e.key)) {
      e.preventDefault(); return spatialMove(e.key.replace("Arrow", "").toLowerCase());
    }
    if (e.key === "[" || e.key === "]") { e.preventDefault(); return cyclePanelTab(e.key === "]" ? 1 : -1); }
  });
}

function stopGamepadNavigation() {
  if (S.gamepadLoop !== null) cancelAnimationFrame(S.gamepadLoop);
  S.gamepadLoop = null;
  S.gamepadLast = {};
}

function startGamepadNavigation() {
  if (S.compact || S.gamepadLoop !== null) return;
  const pulse = (key, active, fn) => {
    const now = performance.now(), state = S.gamepadLast[key] || { active: false, at: 0 };
    if (active && (!state.active || now - state.at > 230)) { fn(); state.at = now; }
    state.active = active; S.gamepadLast[key] = state;
  };
  const frame = () => {
    // O controle deve permanecer exclusivamente com o jogo quando o
    // DigiTracker estiver encaixado como overlay compacto.
    if (S.compact) {
      stopGamepadNavigation();
      return;
    }
    const pad = document.hasFocus() && !document.hidden ? [...(navigator.getGamepads?.() || [])].find(Boolean) : null;
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
    if (S.compact) {
      stopGamepadNavigation();
      return;
    }
    S.gamepadLoop = requestAnimationFrame(frame);
  };
  S.gamepadLoop = requestAnimationFrame(frame);
}

function syncInputMode() {
  if (S.compact) {
    stopGamepadNavigation();
    // Evita que um botão previamente focado receba um "A" atrasado ao
    // alternar para o overlay. O mouse continua podendo focar os controles.
    document.activeElement?.blur?.();
  } else {
    startGamepadNavigation();
  }
}

async function trocarJogo(dir) {
  const games = S.library.filter((game) => !isMastered(game));
  if (!games.length) return;
  const i = games.findIndex((g) => g.slug === S.activeSlug);
  S.activeSlug = games[(i + dir + games.length) % games.length].slug;
  await backend.setActiveGame(S.activeSlug).catch(() => null);
  await renderDashboard({ force: true });
}

async function toggleCompacto() {
  const btn = $("#btn-compact");
  S.compact = !S.compact;
  S.compactState = S.compact ? (S.compactCfg?.view || S.compactCfg?.variant || "minimal") : "hidden";
  syncInputMode();
  btn?.classList.toggle("active", S.compact);
  if (hasBackend()) await backend.setCompact(S.compact);
  await refreshCompactNativeStatus();
  if (S.compact && S.view !== "dashboard") {
    await enterDashboard();
    return;
  }
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
    if (st.compact_tab) S.compactTab = st.compact_tab;
    if (st.compact_view && S.compactCfg) S.compactCfg.view = st.compact_view;
    if (st.compact_editing !== undefined) S.compactEditing = !!st.compact_editing;
    if (st.compact !== undefined) { S.compact = !!st.compact; S.compactState = st.compact ? (st.compact_state || "minimal") : "hidden"; }
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
      ], visual_suggestions: [{id:"demo-v1",type:"route",chapter_id:"demo-c2",title:"Mapa esquemático de Goblin Pass",reason:"A sequência de pontes e a conversão ao sul ficam mais claras visualmente.",query:"Goblin Pass Digimon World 4 map"}], systems: [{
        id:"sys_demo", title:"Linhas de progressão documentadas", description:"Exemplo demonstrativo: caminhos, condições e alternativas extraídos de uma fonte revisada.", group_label:"Linha", layout:"layered", origin:"manual", status:"approved", source_refs:[{section:2,block:3,page:0}],
        nodes:[
          {id:"node_veemon",label:"Veemon",subtitle:"Ponto inicial",stage:"INÍCIO",group:"Coragem",tags:["base"],attributes:{Afinidade:"Neutro"},media_query:"Veemon artwork",spoiler:false,source_refs:[{section:1,block:1,page:0}]},
          {id:"node_agumon",label:"Agumon",subtitle:"Rota alternativa",stage:"INÍCIO",group:"Coragem",tags:["base"],attributes:{Afinidade:"Fogo"},media_query:"Agumon artwork",spoiler:false,source_refs:[{section:1,block:1,page:0}]},
          {id:"node_greymon",label:"Greymon",subtitle:"Forma intermediária",stage:"ESTÁGIO II",group:"Coragem",tags:["ataque"],attributes:{Elemento:"Fogo"},media_query:"Greymon artwork",spoiler:false,source_refs:[{section:2,block:3,page:0}]},
          {id:"node_garurumon",label:"Garurumon",subtitle:"Caminho veloz",stage:"ESTÁGIO II",group:"Amizade",tags:["velocidade"],attributes:{Elemento:"Gelo"},media_query:"Garurumon artwork",spoiler:false,source_refs:[{section:2,block:3,page:0}]},
          {id:"node_metal",label:"MetalGreymon",subtitle:"Objetivo selecionado",stage:"ESTÁGIO III",group:"Coragem",tags:["máquina","ataque"],attributes:{Nível:"35",Técnica:"15"},media_query:"MetalGreymon artwork",spoiler:false,source_refs:[{section:2,block:5,page:0}]},
          {id:"node_were",label:"WereGarurumon",subtitle:"Rota alternativa",stage:"ESTÁGIO III",group:"Amizade",tags:["velocidade"],attributes:{Nível:"35"},media_query:"WereGarurumon artwork",spoiler:false,source_refs:[{section:2,block:5,page:0}]},
          {id:"node_final",label:"Forma final",subtitle:"Conteúdo com spoiler",stage:"FINAL",group:"Coragem",tags:["final"],attributes:{Nível:"50"},media_query:"final form artwork",spoiler:true,source_refs:[{section:3,block:2,page:0}]},
        ],
        edges:[
          {id:"edge_1",from:"node_veemon",to:"node_greymon",label:"Caminho principal",path_kind:"normal",requirements:[{id:"req_level",text:"Alcançar o nível 15",source_refs:[{section:2,block:3,page:0}]}],missable:false,spoiler:false,source_refs:[{section:2,block:3,page:0}]},
          {id:"edge_2",from:"node_agumon",to:"node_greymon",label:"Alternativa",path_kind:"alternative",requirements:[{id:"req_item",text:"Obter o item documentado",source_refs:[{section:2,block:3,page:0}]}],missable:false,spoiler:false,source_refs:[{section:2,block:3,page:0}]},
          {id:"edge_3",from:"node_veemon",to:"node_garurumon",label:"Rota de velocidade",path_kind:"alternative",requirements:[],missable:false,spoiler:false,source_refs:[{section:2,block:3,page:0}]},
          {id:"edge_4",from:"node_greymon",to:"node_metal",label:"Progressão",path_kind:"normal",requirements:[{id:"req_tech",text:"Técnica 15 ou superior",source_refs:[{section:2,block:5,page:0}]},{id:"req_boss",text:"Concluir o desafio da área",source_refs:[{section:2,block:5,page:0}]}],missable:false,spoiler:false,source_refs:[{section:2,block:5,page:0}]},
          {id:"edge_5",from:"node_garurumon",to:"node_were",label:"Progressão",path_kind:"normal",requirements:[{id:"req_speed",text:"Atributo de velocidade documentado",source_refs:[{section:2,block:5,page:0}]}],missable:true,spoiler:false,source_refs:[{section:2,block:5,page:0}]},
          {id:"edge_6",from:"node_metal",to:"node_final",label:"Etapa final",path_kind:"optional",requirements:[{id:"req_final",text:"Cumprir a condição final da fonte",source_refs:[{section:3,block:2,page:0}]}],missable:true,spoiler:true,source_refs:[{section:3,block:2,page:0}]},
        ],
      }] },
      progress: { completed: ["demo-b1"], favorites: ["demo-b5"], revealed_spoilers: [], notes: {"demo-b3":"Fazer o farm antes do chefe."}, checkpoint: "demo-b1", history: [], session_minutes: 30 },
      effective_progress: { completed: ["demo-b1"], favorites: ["demo-b5"], revealed_spoilers: [], notes: {"demo-b3":"Fazer o farm antes do chefe."}, checkpoint: "demo-b1", history: [], session_minutes: 30 },
      next_objective: { chapter_id:"demo-c1", chapter:"Preparação antes da rota", block_id:"demo-b2", type:"checklist", title:"Preparação rápida", text:"Escolha uma arma de ataque à distância." },
      system_state: {active_system:"sys_demo",goals:{sys_demo:"node_metal"},completed_requirements:["req_level"],node_media:{"sys_demo:node_veemon":"media_cover","sys_demo:node_greymon":"media_hero","sys_demo:node_metal":"media_card"},preferences:{}},
      system_objective: {system_id:"sys_demo",system_title:"Linhas de progressão documentadas",node_id:"node_metal",title:"MetalGreymon",next_requirement:{id:"req_tech",text:"Técnica 15 ou superior"},requirements_total:2,requirements_completed:0},
      revisions: [{revision_id:"demo-r1",created_at:1770000000,provider:"local",model:"structured-fallback"}], media: [
        {id:"media_cover",url:"/ui/assets/demo-digital-dungeon-cover.png",title:"Arte demonstrativa",license:"Demonstração local"},
        {id:"media_hero",url:"/ui/assets/demo-digital-world-hero.png",title:"Arte demonstrativa",license:"Demonstração local"},
        {id:"media_card",url:"/ui/assets/demo-digital-card-cover.png",title:"Arte demonstrativa",license:"Demonstração local"},
      ],
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
