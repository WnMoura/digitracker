/* Atlas C+D view model.
 * Pure presentation projection: it never writes progress or source data.
 * The canvas shows one route; the complete entity set remains available in
 * the index/list projections.  This file is intentionally framework-free so
 * the same rules work in WebView2 and the browser smoke harness.
 */
(function (global) {
  "use strict";

  const SEMANTICS = new Set(["single", "alternatives", "fusion", "item", "unknown"]);

  function cardNumber(node, allNodes) {
    const explicit = Number(node && node.card_number);
    if (Number.isFinite(explicit) && explicit > 0) return explicit;
    return Math.max(1, (allNodes || []).indexOf(node) + 1);
  }

  function requirementTarget(systemId, edgeId, requirementId) {
    return `requirement:${systemId || ""}:${edgeId || ""}:${requirementId || ""}`;
  }

  function edgeStatus(edge, completed, context) {
    const requirements = Array.isArray(edge && edge.requirements) ? edge.requirements : [];
    const doneSet = completed instanceof Set ? completed : new Set(completed || []);
    const isCompleted = context && typeof context.isRequirementCompleted === "function"
      ? (req) => context.isRequirementCompleted(edge, req)
      : (req) => doneSet.has(req && req.id);
    const unknown = requirements.some((req) => {
      const condition = req && req.condition;
      return req && (req.operator === "unknown" || req.group === "unknown"
        || (condition && condition.op === "unknown"));
    });
    const done = requirements.filter((req) => req && isCompleted(req)).length;
    const anyGroups = requirements.some((req) => req && req.group === "any");
    const allGroups = requirements.filter((req) => !req || req.group !== "any");
    const anyDone = requirements.filter((req) => req && req.group === "any" && isCompleted(req)).length;
    const allDone = allGroups.filter((req) => req && isCompleted(req)).length;
    const state = unknown ? "unknown" : (anyGroups
      ? (anyDone > 0 && allDone === allGroups.length ? "satisfied" : "unsatisfied")
      : (done === requirements.length ? "satisfied" : "unsatisfied"));
    return {
      done, total: requirements.length, unknown, state,
      available: state === "satisfied",
      label: unknown ? "Regra desconhecida" : requirements.length ? `${done}/${requirements.length}` : "Sem requisitos",
    };
  }

  function textMatch(node, query, number) {
    if (!query) return true;
    const value = [node.label, node.subtitle, node.stage, node.group,
      `#${String(number).padStart(3, "0")}`, String(number)]
      .filter(Boolean).join(" ").toLocaleLowerCase("pt-BR");
    return value.includes(String(query).toLocaleLowerCase("pt-BR"));
  }

  function buildAtlasViewModel({ system, systemState, selection, filters } = {}) {
    const source = system || {};
    const state = systemState || {};
    const options = selection || {};
    const filter = filters || {};
    const allNodes = Array.isArray(source.nodes) ? source.nodes : [];
    const allEdges = Array.isArray(source.edges) ? source.edges : [];
    const byId = new Map(allNodes.map((node) => [node.id, node]));
    const completed = new Set(state.completed_requirements || []);
    const completedTargets = new Set(state.completed_requirement_targets || []);
    const scopedRequirements = Boolean(state.requirements_scoped || completedTargets.size);
    const isRequirementCompleted = (edge, requirement) => {
      if (!requirement) return false;
      return scopedRequirements
        ? completedTargets.has(requirementTarget(source.id, edge && edge.id, requirement.id))
        : completed.has(requirement.id);
    };
    const edgeStatusFor = (edge) => edgeStatus(edge, completed, {isRequirementCompleted});
    const number = (node) => cardNumber(node, allNodes);
    const statusFor = (node) => {
      const incoming = allEdges.filter((edge) => edge.to === node.id);
      if (!incoming.length) return "available";
      if (incoming.some((edge) => edgeStatusFor(edge).available)) return "available";
      return incoming.some((edge) => edgeStatusFor(edge).unknown) ? "unknown" : "blocked";
    };
    const eligibleNodes = allNodes.filter((node) => {
      if (node.spoiler && !filter.spoilers) return false;
      if (filter.group && filter.group !== "all" && (node.stage || node.group) !== filter.group) return false;
      if (filter.tag && filter.tag !== "all" && !(node.tags || []).includes(filter.tag)) return false;
      if (filter.availability && filter.availability !== "all" && statusFor(node) !== filter.availability) return false;
      return true;
    });
    const matches = eligibleNodes.filter((node) => textMatch(node, filter.search || "", number(node)));
    const selected = matches.find((node) => node.id === options.nodeId)
      || matches.find((node) => node.id === state.goals?.[source.id])
      || matches[0]
      || eligibleNodes[0]
      || allNodes[0]
      || null;
    const selectedId = selected && selected.id;
    const visible = options.mode === "map" || options.mode === "overview" ? matches : eligibleNodes;
    const visibleIds = new Set(visible.map((node) => node.id));
    const incoming = allEdges.filter((edge) => edge.to === selectedId && visibleIds.has(edge.from));
    const outgoing = allEdges.filter((edge) => edge.from === selectedId && visibleIds.has(edge.to));
    const preferredIncoming = options.incomingEdgeId || options.edgeId || state.preferences?.[source.id]?.edge_id;
    const selectedIncomingRoute = incoming.find((edge) => edge.id === preferredIncoming) || incoming[0] || null;
    const preferredOutgoing = options.outgoingEdgeId || state.preferences?.[source.id]?.outgoing_edge_id;
    const selectedOutgoingRoute = outgoing.find((edge) => edge.id === preferredOutgoing) || outgoing[0] || null;
    // An explicit alternative chip sets one side of the route.  Keep that
    // side active in the inspector while the other side remains available as
    // the neighbouring card; without this, choosing a destination would
    // unexpectedly keep showing the previous incoming requirements.
    const selectedRoute = options.outgoingEdgeId
      ? selectedOutgoingRoute
      : (options.incomingEdgeId || options.edgeId) ? selectedIncomingRoute
        : selectedIncomingRoute || selectedOutgoingRoute;
    const routeEdges = [selectedIncomingRoute, selectedOutgoingRoute]
      .filter(Boolean).filter((edge, index, list) => list.findIndex((item) => item.id === edge.id) === index);
    const routeCards = [];
    const addRouteCard = (role, node, edge) => {
      if (!node || routeCards.some((card) => card.node.id === node.id)) return;
      routeCards.push({ role, node, edge });
    };
    if (selectedIncomingRoute && byId.has(selectedIncomingRoute.from)
      && selectedIncomingRoute.from !== selectedId) {
      addRouteCard("origin", byId.get(selectedIncomingRoute.from), selectedIncomingRoute);
    }
    addRouteCard("selected", selected, selectedIncomingRoute || selectedOutgoingRoute);
    if (selectedOutgoingRoute && byId.has(selectedOutgoingRoute.to)
      && selectedOutgoingRoute.to !== selectedId) {
      addRouteCard("destination", byId.get(selectedOutgoingRoute.to), selectedOutgoingRoute);
    }
    const uniqueNodes = (items) => items.filter((node, index, list) => node && list.findIndex((item) => item.id === node.id) === index);
    const alternativeOrigins = incoming
      .filter((edge) => !selectedIncomingRoute || edge.id !== selectedIncomingRoute.id)
      .map((edge) => ({ node: byId.get(edge.from), edge }))
      .filter((item) => item.node);
    const alternativeDestinations = outgoing
      .filter((edge) => !selectedOutgoingRoute || edge.id !== selectedOutgoingRoute.id)
      .map((edge) => ({ node: byId.get(edge.to), edge }))
      .filter((item) => item.node);
    const overviewEdges = allEdges.filter((edge) => {
      if (!visibleIds.has(edge.from) || !visibleIds.has(edge.to)) return false;
      return edge.from === selectedId || edge.to === selectedId;
    });
    const semanticsFor = (edge) => SEMANTICS.has(edge?.relation_semantics) ? edge.relation_semantics : "single";
    const issues = Array.isArray(source._draftDiagnostics?.pending_items)
      ? source._draftDiagnostics.pending_items.slice() : [];
    const routeName = selectedIncomingRoute
      ? `${byId.get(selectedIncomingRoute.from)?.label || "Origem"} → ${selected?.label || "Destino"}`
      : selected ? selected.label : "Nenhuma entidade selecionada";
    return {
      system: source, state, allNodes, allEdges, byId, completed, number,
      eligibleNodes, matches, visibleNodes: visible, selected, selectedId,
      selectedNode: selected,
      statusFor, status: selected ? statusFor(selected) : "unknown",
      incoming, outgoing, selectedIncomingRoute, selectedOutgoingRoute,
      incomingRoutes: incoming, outgoingRoutes: outgoing,
      selectedRoute,
      routeEdges, routeCards, alternativeOrigins, alternativeDestinations,
      overviewEdges, routeName, edgeStatus: edgeStatusFor, isRequirementCompleted,
      requirementTarget, semanticsFor, issues,
      groups: [...new Set(allNodes.map((node) => node.stage || node.group).filter(Boolean))],
      tags: [...new Set(allNodes.flatMap((node) => node.tags || []))],
      uniqueNodes,
    };
  }

  global.AtlasViewModel = { buildAtlasViewModel, edgeStatus, cardNumber };
})(window);
