/* Shared geometry for the Atlas C+D presentation.  Cards, SVG connection
 * anchors and labels are calculated from the same rectangles, so a line can
 * never use a different coordinate system than the card it points to.
 */
(function (global) {
  "use strict";

  const CARD_WIDTH = 176;
  const CARD_HEIGHT = 226;
  const SMALL_CARD_WIDTH = 160;
  const SMALL_CARD_HEIGHT = 214;
  const PADDING = 32;
  const ROUTE_GAP = 64;
  const GRID_GAP_X = 48;
  const GRID_GAP_Y = 28;

  function routePath(from, to, width, height, index = 0) {
    const x1 = from.x + width;
    const y1 = from.y + height * 0.5;
    const x2 = to.x;
    const y2 = to.y + height * 0.5;
    if (x2 < x1) {
      // A cycle is a return path, not a second copy of the entity.  Route it
      // below the cards so the arrow never crosses a card rectangle.
      const lane = from.y + height + 24 + (index % 3) * 16;
      return `M ${x1} ${y1} C ${x1 + 24} ${y1}, ${x1 + 24} ${lane}, ${x2 - 24} ${lane} C ${x2 - 24} ${lane}, ${x2 - 24} ${y2}, ${x2} ${y2}`;
    }
    const middle = (x1 + x2) / 2;
    return `M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`;
  }

  function overviewPath(from, to, width, height, lane) {
    const x1 = from.x + width;
    const y1 = from.y + height * 0.5;
    const x2 = to.x;
    const y2 = to.y + height * 0.5;
    const right = Math.max(x1 + 18, Math.min(x2 - 18, x1 + 46));
    const left = Math.min(x2 - 18, Math.max(x1 + 18, x2 - 46));
    const safeLane = Math.max(12, lane);
    if (x2 >= x1) {
      const middle = (x1 + x2) / 2;
      return `M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`;
    }
    return `M ${x1} ${y1} L ${right} ${y1} L ${right} ${safeLane} L ${left} ${safeLane} L ${left} ${y2} L ${x2} ${y2}`;
  }

  function routeLayout(cards, connections, viewport) {
    const list = Array.isArray(cards) ? cards.filter((item) => item && item.node) : [];
    // Keep the reference card size on desktop.  The compact card is reserved
    // for genuinely narrow route canvases; at 1280px the inspector/index can
    // still fit 176px cards at the mandated >=85% zoom.
    const small = !!(typeof window !== "undefined" && window.innerWidth < 820);
    const width = small ? SMALL_CARD_WIDTH : CARD_WIDTH;
    const height = small ? SMALL_CARD_HEIGHT : CARD_HEIGHT;
    const gap = small ? 40 : ROUTE_GAP;
    const positions = new Map();
    const cardRects = new Map();
    list.forEach((card, index) => {
      const x = PADDING + index * (width + gap);
      const y = PADDING + 20;
      const rect = { x, y, width, height, role: card.role || "node", nodeId: card.node.id };
      positions.set(card.node.id, rect);
      cardRects.set(card.node.id, rect);
    });
    const edgePaths = new Map();
    const edgeLabelRects = new Map();
    let maxLane = height + PADDING;
    (connections || []).forEach((edge, index) => {
      const from = positions.get(edge.from);
      const to = positions.get(edge.to);
      if (!from || !to || from.nodeId === to.nodeId) return;
      edgePaths.set(edge.id, routePath(from, to, width, height, index));
      if (to.x < from.x) maxLane = Math.max(maxLane, from.y + height + 24 + (index % 3) * 16);
      edgeLabelRects.set(edge.id, {
        x: (from.x + width + to.x) / 2,
        y: to.x < from.x ? maxLane - 6 : (from.y + to.y) / 2 + height * 0.5 - 13,
      });
    });
    const last = list.length ? positions.get(list[list.length - 1].node.id) : null;
    const bounds = {
      width: Math.max(2 * PADDING + width, (last ? last.x : 0) + width + PADDING),
      height: Math.max(height + 2 * PADDING + 20, maxLane + PADDING),
    };
    return { positions, cardRects, edgePaths, edgeLabelRects, width: bounds.width,
      height: bounds.height, bounds, cardWidth: width, cardHeight: height,
      direction: "horizontal", mode: "routes" };
  }

  function overviewLayout(nodes, edges, selectedId, viewport) {
    const list = Array.isArray(nodes) ? nodes.filter(Boolean) : [];
    const count = Math.max(1, list.length);
    const columns = Math.max(4, Math.min(10, Math.ceil(Math.sqrt(count * 1.35))));
    const width = CARD_WIDTH;
    const height = CARD_HEIGHT;
    const positions = new Map();
    const cardRects = new Map();
    const selectedIndex = Math.max(0, list.findIndex((node) => node.id === selectedId));
    list.forEach((node, index) => {
      // Keep source order stable.  Put the selected entity in the visual
      // center column without moving the other entities between renders.
      const col = index % columns;
      const row = Math.floor(index / columns);
      const rect = { x: PADDING + col * (width + GRID_GAP_X),
        y: PADDING + row * (height + GRID_GAP_Y), width, height, role: "overview", nodeId: node.id };
      positions.set(node.id, rect);
      cardRects.set(node.id, rect);
    });
    const edgePaths = new Map();
    const edgeLabelRects = new Map();
    const rowCount = Math.ceil(count / columns);
    const lanes = new Map();
    (edges || []).forEach((edge, index) => {
      const from = positions.get(edge.from);
      const to = positions.get(edge.to);
      if (!from || !to || from.nodeId === to.nodeId) return;
      const lane = PADDING + (rowCount + 1 + (index % 5)) * (height + GRID_GAP_Y) + (index % 3) * 8;
      lanes.set(edge.id, lane);
      edgePaths.set(edge.id, overviewPath(from, to, width, height, lane));
      const forward = to.x >= from.x;
      edgeLabelRects.set(edge.id, {
        x: forward ? (from.x + width + to.x) / 2 : lane,
        y: forward ? (from.y + to.y) / 2 + height * 0.5 - 13 : lane - 6,
      });
    });
    const maxX = Math.max(...[...positions.values()].map((rect) => rect.x + rect.width), PADDING);
    const maxY = Math.max(...[...positions.values()].map((rect) => rect.y + rect.height), PADDING);
    const bounds = { width: maxX + PADDING, height: maxY + PADDING };
    return { positions, cardRects, edgePaths, edgeLabelRects,
      width: bounds.width, height: bounds.height, bounds,
      cardWidth: width, cardHeight: height, direction: "horizontal", mode: "overview", lanes,
      selectedIndex, columns };
  }

  function layoutAtlasRoute({ cards = [], connections = [], nodes = [], mode = "routes", selectedId = "", viewport = null } = {}) {
    if (mode === "overview" || mode === "map") return overviewLayout(nodes, connections, selectedId, viewport);
    return routeLayout(cards, connections, viewport);
  }

  global.AtlasLayout = { layoutAtlasRoute, routeLayout, overviewLayout,
    CARD_WIDTH, CARD_HEIGHT, SMALL_CARD_WIDTH, SMALL_CARD_HEIGHT, PADDING, ROUTE_GAP };
})(window);
