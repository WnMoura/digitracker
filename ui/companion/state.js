let requestSequence = 0;

export function normalized(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
}

export function requestId(source = globalThis.crypto) {
  if (source?.randomUUID) return source.randomUUID();
  if (source?.getRandomValues) {
    const bytes = new Uint8Array(16);
    source.getRandomValues(bytes);
    // UUID v4 layout. getRandomValues remains available on ordinary HTTP LAN
    // pages where randomUUID may be unavailable because the context is not
    // considered secure.
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  // request_id is an idempotency key, not a credential. This last-resort
  // monotonic value keeps very old browsers usable without falling back to
  // Math.random; supported mobile browsers should always take a crypto branch.
  requestSequence += 1;
  return `mobile-${Date.now().toString(36)}-${requestSequence.toString(36)}`;
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
  if (values.kind === "progress") {
    // There is only one resume point per guide/game. Completion, favorite and
    // reveal remain block-scoped, but checkpoint writes share one version.
    if (values.action === "checkpoint") return "progress:checkpoint";
    return "progress:" + values.action + ":" + values.target.block_id;
  }
  if (values.kind === "requirement") return "requirement:" + values.target.system_id + ":" + values.target.edge_id + ":" + values.target.requirement_id;
  if (values.kind === "goal") return "goal:" + values.target.system_id;
  if (values.kind === "item") return "item:" + values.target.item_id;
  return values.kind || "unknown";
}

export function rebaseNextDependent(operations, completedIndex, target, valueVersion) {
  const version = Number(valueVersion);
  if (!Array.isArray(operations) || !Number.isInteger(version) || version < 0) return null;
  for (let index = completedIndex + 1; index < operations.length; index += 1) {
    const operation = operations[index];
    if (!operation || operation.target !== target || operation.status === "conflict") continue;
    return {
      index,
      operation: {
        ...operation,
        values: {...operation.values, _expectedValueVersion: version},
        body: {...operation.body, expected_value_version: version},
      },
    };
  }
  return null;
}

export function versionFor(data, values) {
  // The first version observed for an operation is part of that operation's
  // intent. Keep it attached to the queued value so reconnect/flush cannot
  // silently replace a stale write with the newest PC version and overwrite a
  // concurrent change. Retrying the same request_id after a lost ACK remains
  // safe because the backend checks the idempotency receipt first.
  if (values && Object.prototype.hasOwnProperty.call(values, "_expectedValueVersion")) {
    const captured = Number(values._expectedValueVersion);
    if (Number.isInteger(captured) && captured >= 0) return captured;
  }

  let current = 0;
  if (values.kind === "progress") current = Number(data?.progress?.value_versions?.[targetKey(values)] || 0);
  else if (values.kind === "requirement" || values.kind === "goal") current = Number(data?.system_state?.value_versions?.[targetKey(values)] || 0);
  else if (values.kind === "item") current = Number((data?.items || []).find((item) => item.id === values.target.item_id)?.value_version || 0);

  current = Number.isInteger(current) && current >= 0 ? current : 0;
  if (values && typeof values === "object") values._expectedValueVersion = current;
  return current;
}

export function patchConfirmedValue(data, result) {
  if (!data || !result?.ok) return data;
  const next = globalThis.structuredClone ? structuredClone(data) : JSON.parse(JSON.stringify(data));
  const target = String(result.target || "");
  if (result.kind === "progress") {
    const parts = target.split(":");
    const action = parts[1], blockId = parts[2];
    const map = {complete: "completed", favorite: "favorites", reveal: "revealed_spoilers"};
    if (action === "checkpoint") next.progress.checkpoint = String(result.value || "");
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
    if (result.value) {
      values.add(requirementId);
      scoped.add(target);
    } else {
      scoped.delete(target);
      // Keep the legacy projection while another scoped route still uses the
      // same requirement id. This mirrors SmartGuideStore and avoids a brief
      // client-side mismatch before the next server refresh.
      if (![...scoped].some((item) => item.split(":").at(-1) === requirementId)) values.delete(requirementId);
    }
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
