export function normalized(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
}

export function requestId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return "mobile-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

export function namespaceFor(data, slug) {
  const companion = data?.companion || {};
  return [location.origin, companion.installation_id || "unknown", companion.account_scope || "unknown", slug || data?.game?.slug || ""].join("|");
}

export function guideBlocks(data) {
  return (data?.chapters || []).flatMap((chapter) =>
    (chapter.blocks || []).map((block) => ({...block, chapterId: chapter.id, chapterTitle: chapter.title})));
}

export function systemById(data, systemId) {
  return (data?.systems || []).find((item) => item.id === systemId) || null;
}

export function nodeById(system, nodeId) {
  return (system?.nodes || []).find((item) => item.id === nodeId) || null;
}

export function mediaUrl(data, mediaId, fallback = "") {
  const media = (data?.media || []).find((item) => item.id === mediaId);
  const raw = String(media?.url || media?.src || fallback || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw, location.origin);
    return parsed.origin === location.origin && parsed.pathname.startsWith("/assets/") ? parsed.pathname + parsed.search : "";
  } catch (_) { return ""; }
}

export function targetKey(values) {
  if (values.kind === "progress") return "progress:" + values.action + ":" + values.target.block_id;
  if (values.kind === "requirement") return "requirement:" + values.target.system_id + ":" + values.target.edge_id + ":" + values.target.requirement_id;
  if (values.kind === "goal") return "goal:" + values.target.system_id;
  if (values.kind === "item") return "item:" + values.target.item_id;
  return values.kind || "unknown";
}

export function versionFor(data, values) {
  if (values.kind === "progress") return Number(data?.progress?.value_versions?.[targetKey(values)] || 0);
  if (values.kind === "requirement" || values.kind === "goal") return Number(data?.system_state?.value_versions?.[targetKey(values)] || 0);
  if (values.kind === "item") return Number((data?.items || []).find((item) => item.id === values.target.item_id)?.value_version || 0);
  return 0;
}

export function patchConfirmedValue(data, result) {
  if (!data || !result?.ok) return data;
  const next = globalThis.structuredClone ? structuredClone(data) : JSON.parse(JSON.stringify(data));
  const target = String(result.target || "");
  if (result.kind === "progress") {
    const parts = target.split(":");
    const action = parts[1], blockId = parts[2];
    const map = {complete: "completed", favorite: "favorites", reveal: "revealed_spoilers"};
    if (action === "checkpoint") next.progress.checkpoint = result.value ? blockId : "";
    else if (map[action]) {
      const values = new Set(next.progress[map[action]] || []);
      result.value ? values.add(blockId) : values.delete(blockId);
      next.progress[map[action]] = [...values];
    }
    next.progress.value_versions = {...next.progress.value_versions, [target]: result.value_version};
  }
  if (result.kind === "requirement") {
    const requirementId = target.split(":").at(-1);
    const values = new Set(next.system_state.completed_requirements || []);
    const scoped = new Set(next.system_state.completed_requirement_targets || []);
    result.value ? values.add(requirementId) : values.delete(requirementId);
    result.value ? scoped.add(target) : scoped.delete(target);
    next.system_state.completed_requirements = [...values];
    next.system_state.completed_requirement_targets = [...scoped];
    next.system_state.requirements_scoped = true;
    next.system_state.value_versions = {...next.system_state.value_versions, [target]: result.value_version};
  }
  if (result.kind === "goal") {
    const systemId = target.split(":")[1];
    next.system_state.goals = {...next.system_state.goals};
    if (result.value) {
      next.system_state.goals[systemId] = result.value;
      next.system_state.active_system = systemId;
    } else {
      delete next.system_state.goals[systemId];
      if (next.system_state.active_system === systemId) next.system_state.active_system = "";
    }
    next.system_state.value_versions = {...next.system_state.value_versions, [target]: result.value_version};
  }
  if (result.kind === "item") {
    const item = (next.items || []).find((row) => row.id === result.item_id);
    if (item) { item.quantity = result.value; item.value_version = result.value_version; }
  }
  return next;
}
