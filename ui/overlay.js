const root = document.getElementById("overlay-root");
const surface = new URLSearchParams(location.search).get("surface") === "details" ? "details" : "summary";
let state = null;
let refreshTimer = null;
let rowObserver = null;
const shownCelebrations = new Set();

const api = () => window.pywebview?.api;
const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));
const color = (game) => game?.palette?.primary || game?.accent || "#4caeff";

function background(game, mode) {
  const art = game?.art || {};
  if (mode === "solid") return "";
  if (mode === "cover") return art.cover || art.box || art.background || art.ingame || art.title || "";
  if (mode === "title") return art.title || art.background || art.ingame || art.cover || art.box || "";
  return art.background || art.ingame || art.title || art.cover || art.box || "";
}

function objective(game) {
  const smart = game?.smart_guide || {};
  const system = smart.system_objective || {};
  if (system.title) {
    const requirement = system.next_requirement?.text || "";
    return {
      title: system.title,
      text: requirement || system.subtitle || `${system.requirements_completed || 0} de ${system.requirements_total || 0} requisitos`,
      system: true,
    };
  }
  const next = smart.next_objective || {};
  if (next.title || next.text) return { title: next.title || next.text, text: next.text || "" };
  const achievement = (game?.next_ids || []).map((id) => (game.achievements || []).find((row) => row.id === id)).find(Boolean);
  return achievement
    ? { title: achievement.name, text: achievement.desc || "" }
    : { title: "Guia concluído", text: "Nenhum próximo objetivo pendente." };
}

function missableWarning(game) {
  const row = (game?.pending_missables || [])[0];
  if (!row) return null;
  return { title: row.name || "Conquista perdível", softcore: !!row.earned && !row.hardcore };
}

function shell(game, body) {
  const cfg = state?.config || {};
  const art = background(game, cfg.background_mode || "background");
  return `<section class="hud ${state?.editing ? "editing" : ""}" style="--accent:${esc(color(game))}">
    ${art ? `<div class="hud-bg" style="background-image:url('${esc(art)}')"></div>` : ""}
    <div class="hud-shade"></div>
    <header class="hud-head"><span class="hud-brand">DIGITRACKER</span>${surface === "details" ? `<span class="hud-title">${esc(game?.title || "")}</span>` : ""}<i class="hud-dot"></i>${state?.editing ? `<button class="hud-return" id="hud-return" title="Voltar ao aplicativo">×</button>` : ""}</header>
    ${body}
    ${state?.editing ? `<span class="edit-hint">ARRASTE PARA POSICIONAR</span><button class="resize-handle" id="resize-handle" aria-label="Redimensionar"></button>` : ""}
  </section>`;
}

function summaryHTML(game) {
  const current = objective(game);
  const missable = missableWarning(game);
  const mastery = game.mastery || {};
  const progress = Math.max(0, Math.min(100, Number(mastery.percent || 0)));
  const cover = game.art?.cover || game.art?.box;
  const body = `<div class="summary-card">
    ${cover ? `<img class="summary-cover" src="${esc(cover)}" alt="">` : `<span class="summary-cover"></span>`}
    <div class="summary-copy"><span class="kicker">${missable ? "△ PERDÍVEL ANTES DE AVANÇAR" : "PRÓXIMO OBJETIVO"}</span><strong>${esc(missable?.title || current.title)}</strong><small>${esc(missable ? (missable.softcore ? "Obtida só em Softcore · refaça em Hardcore" : "Classificação oficial RetroAchievements") : (current.text || `${mastery.hardcore || 0} de ${mastery.total || 0} conquistas`))}</small></div>
    <div class="progress-ring" style="--p:${progress}"><span>${progress}%</span></div>
  </div>`;
  return shell(game, body);
}

function achievementRow(row, locked) {
  const badge = row.badge_url
    ? `<span class="badge ${locked ? "locked" : ""}" style="background-image:url('${esc(row.badge_url)}')"></span>`
    : `<span class="badge ${locked ? "locked" : ""}"></span>`;
  return `<div class="achievement">${badge}<div><b>${esc(row.name)}</b><small>${row.points ? `${esc(row.points)} pts · ` : ""}${esc(row.desc || "")}</small></div></div>`;
}

function achievementsHTML(game) {
  const cfg = state?.config || {};
  const earned = (game.achievements || []).filter((row) => row.earned && row.date_raw)
    .sort((a, b) => a.date_raw < b.date_raw ? 1 : -1);
  const slots = Math.max(1, Number(root.dataset.slots || 4));
  const last = earned.slice(0, Math.min(3, slots, Number(cfg.last ?? 2)));
  const next = (game.next_ids || []).map((id) => (game.achievements || []).find((row) => row.id === id))
    .filter(Boolean).slice(0, Math.max(0, slots - last.length));
  const group = (label, rows, locked) => rows.length
    ? `<p class="section-label">${label}</p>${rows.map((row) => achievementRow(row, locked)).join("")}` : "";
  return `<div class="detail-kicker">CONQUISTAS · ${game.score?.earned || 0} PTS</div>${group("Últimas obtidas", last, false)}${group("Próximas", next, true)}${!last.length && !next.length ? `<p class="empty">Sem conquistas registradas.</p>` : ""}`;
}

function guideHTML(game) {
  const smart = game.smart_guide || {};
  const missable = missableWarning(game);
  const chapters = (smart.current?.chapters || []).slice(0, 3);
  if (!chapters.length) {
    const current = objective(game);
    return `<div class="detail-kicker">GUIA INTELIGENTE</div>${missable ? `<div class="guide-block missable"><b>△ ${esc(missable.title)}</b><p>${missable.softcore ? "Refazer em Hardcore" : "Perdível oficial RetroAchievements"}</p></div>` : ""}<div class="guide-block"><b>${esc(current.title)}</b><p>${esc(current.text)}</p></div>`;
  }
  return `<div class="detail-kicker">GUIA INTELIGENTE</div>${missable ? `<div class="guide-block missable"><b>△ ${esc(missable.title)}</b><p>${missable.softcore ? "Refazer em Hardcore" : "Antes do próximo objetivo comum"}</p></div>` : ""}${chapters.map((chapter) => `<div class="guide-block"><b>${esc(chapter.title || "Capítulo")}</b>${(chapter.blocks || []).filter((block) => block.type !== "text").slice(0, 2).map((block) => `<p>${esc(block.title || block.text || "")}</p>`).join("")}</div>`).join("")}`;
}

function detailsHTML(game) {
  const tab = game.compact_tab || state?.config?.tab || "achievements";
  const body = `<nav class="detail-tabs"><button data-tab="achievements" class="${tab === "achievements" ? "on" : ""}">♜ Conquistas</button><button data-tab="guide" class="${tab === "guide" ? "on" : ""}">♢ Guia Inteligente</button></nav><div class="detail-content" id="detail-content">${tab === "guide" ? guideHTML(game) : achievementsHTML(game)}</div>`;
  return shell(game, body);
}

function render() {
  rowObserver?.disconnect?.();
  rowObserver = null;
  const game = state?.game;
  if (!game) {
    root.innerHTML = shell(null, `<p class="empty">Nenhum jogo selecionado.</p>`);
  } else {
    root.innerHTML = surface === "summary" ? summaryHTML(game) : detailsHTML(game);
  }
  bind(game);
  showCompletion(game);
}

function showCompletion(game) {
  if (!game) return;
  const events = ((game.completion_events || {}).events || []).filter((event) => event.pending);
  const event = [...events].reverse().find((item) => item.kind === "mastery");
  if (!event || shownCelebrations.has(event.id)) return;
  shownCelebrations.add(event.id);
  const toast = document.createElement("div");
  toast.className = "completion-toast";
  toast.innerHTML = `<span>★</span><div><small>MASTERY CONQUISTADA</small><b>${esc(game.title)}</b></div>`;
  root.querySelector(".hud")?.appendChild(toast);
  setTimeout(() => toast.remove(), 6000);
}

function bind(game) {
  document.getElementById("hud-return")?.addEventListener("click", () => api()?.set_compact(false));
  root.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", async () => {
    if (!state?.editing || !game) return;
    await api()?.set_compact_tab(game.slug, button.dataset.tab);
    await refresh();
    api()?.touch_overlay_edit_mode();
  }));
  if (state?.editing) {
    makeDraggable(root.querySelector(".hud-head"));
    makeResizable(document.getElementById("resize-handle"));
  }
  if (surface === "details" && typeof ResizeObserver !== "undefined") {
    const content = document.getElementById("detail-content");
    if (content) {
      const update = () => {
        const slots = Math.max(1, Math.min(10, Math.floor((content.clientHeight - 32) / 35)));
        if (String(slots) !== root.dataset.slots) {
          root.dataset.slots = String(slots);
          render();
        }
      };
      rowObserver = new ResizeObserver(update);
      rowObserver.observe(content);
      requestAnimationFrame(update);
    }
  }
}

function makeDraggable(handle) {
  if (!handle) return;
  let grabX = 0, grabY = 0, pending = null, frame = 0;
  const flush = () => { frame = 0; if (pending) { api()?.move_overlay_surface(surface, pending[0], pending[1]); pending = null; } };
  const move = (event) => { pending = [Math.round(event.screenX - grabX), Math.round(event.screenY - grabY)]; if (!frame) frame = requestAnimationFrame(flush); };
  const up = () => {
    document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
    api()?.save_overlay_geometry(surface, window.screenX, window.screenY, window.innerWidth, window.innerHeight);
  };
  handle.addEventListener("mousedown", (event) => {
    if (event.button !== 0 || event.target.closest("button")) return;
    grabX = event.clientX; grabY = event.clientY;
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
    api()?.touch_overlay_edit_mode(); event.preventDefault();
  });
}

function makeResizable(handle) {
  if (!handle) return;
  let x = 0, y = 0, width = 0, height = 0;
  const move = (event) => { api()?.resize_overlay_surface(surface, width + event.screenX - x, height + event.screenY - y); api()?.touch_overlay_edit_mode(); };
  const up = () => {
    document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
    api()?.save_overlay_geometry(surface, window.screenX, window.screenY, window.innerWidth, window.innerHeight);
  };
  handle.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    x = event.screenX; y = event.screenY; width = window.innerWidth; height = window.innerHeight;
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up); event.preventDefault();
  });
}

async function refresh() {
  try {
    state = await api()?.get_compact_surface_state(surface);
    if (state?.ok) render();
  } catch (_) { /* a janela pode estar sendo encerrada */ }
}

window.onCompactSurfaceChanged = refresh;
window.addEventListener("pywebviewready", () => {
  refresh();
  refreshTimer = setInterval(refresh, 2000);
});
window.addEventListener("beforeunload", () => clearInterval(refreshTimer));
