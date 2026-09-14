/*
 * Smoke visual principal do Atlas C+D.
 *
 * Rode com o servidor estático do repositório:
 *   NODE_PATH=<runtime node_modules> node tests/atlas_cd_fixture_smoke.cjs http://127.0.0.1:8765
 *
 * A página tests/atlas-cd-test.html é a fixture canônica: ela usa os mesmos
 * módulos de apresentação do aplicativo e pode ser aberta manualmente para
 * testar a interação sem credenciais ou acesso a fontes externas.
 */
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { mkdir } = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const base = process.argv[2] || "http://127.0.0.1:8765";
  const output = path.resolve(__dirname, "../build-check/atlas-cd-fixture");
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: "msedge" });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    await page.goto(`${base}/tests/atlas-cd-test.html`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#atlas-shell .atlas-node-cd");
    const state = () => page.evaluate(() => ({ ...AtlasFixture.state }));
    const geometry = () => page.evaluate(() => {
      const shell = document.querySelector("#atlas-shell").getBoundingClientRect();
      const main = document.querySelector("#atlas-test-main").getBoundingClientRect();
      const nodes = [...document.querySelectorAll("#atlas-shell .atlas-node-cd")].map(node => {
        const rect = node.getBoundingClientRect();
        return { rect, visible: rect.left >= 0 && rect.right <= innerWidth + 1 && rect.top >= 0 && rect.bottom <= innerHeight + 1 };
      });
      return { shell, main, nodes, bodyWidth: document.body.scrollWidth, viewportWidth: innerWidth };
    });
    const reset = async () => { await page.evaluate(() => AtlasFixture.reset()); await page.waitForSelector("#atlas-shell .atlas-node-cd"); };

    assert.equal(await page.locator("#atlas-shell.atlas-cd").count(), 1, "Fixture não renderizou o shell C+D");
    assert(await page.locator(".atlas-entity-index").count() === 1, "Índice de entidades ausente");
    assert(await page.locator(".atlas-node-cd").count() <= 3, "Rotas renderizaram mais de três cartões");
    assert(await page.locator(".atlas-index-row").count() >= 10, "Fixture não expôs o índice completo");
    assert(await page.locator(".atlas-inspector-cd").count() === 1, "Inspetor C+D ausente");

    for (const [width, height] of [[1600, 900], [1280, 720]]) {
      await page.setViewportSize({ width, height });
      await reset();
      const result = await geometry();
      assert(result.shell.right <= result.main.right + 1, `Shell saiu do contêiner em ${width}px`);
      assert(result.shell.bottom <= result.main.bottom + 1, `Shell saiu verticalmente em ${width}px`);
      assert(result.nodes.length <= 3 && result.nodes.every(node => node.visible), `Cartão fora da rota em ${width}px`);
      await page.screenshot({ path: path.join(output, `routes-${width}x${height}.png`) });
    }

    await page.setViewportSize({ width: 1600, height: 900 });
    await reset();
    const selectedBefore = await state();
    await page.locator('[data-atlas-route="e002"]').dispatchEvent("click");
    await page.waitForFunction(() => AtlasFixture.state.incomingEdgeId === "e002");
    const selectedAfter = await state();
    assert.equal(selectedAfter.nodeId, selectedBefore.nodeId, "Alternativa moveu o cartão central");
    assert.equal(selectedAfter.incomingEdgeId, "e002", "Origem alternativa não foi selecionada");
    assert.match(await page.locator(".atlas-requirement-mode").innerText(), /ITEM DO JOGO/i, "Requisito de item não foi identificado");
    const itemRequirement = page.locator('[data-atlas-requirement="r002"]');
    assert.equal(await itemRequirement.isChecked(), false, "Requisito começou marcado indevidamente");
    await itemRequirement.check();
    assert(await page.evaluate(() => AtlasFixture.completed.includes("r002")), "Marcação da rota alternativa não foi preservada");

    await reset();
    await page.locator("#atlas-test-review").click();
    assert.equal(await page.locator("#atlas-test-dialog").count(), 1, "Revisão da fonte não abriu");
    assert.match(await page.locator("#atlas-test-dialog").innerText(), /Regressão #104/);
    assert.match(await page.locator("#atlas-test-dialog").innerText(), /p001-e0229-r0015/);
    await page.locator("#atlas-test-dialog [data-close]").first().click();

    await page.locator("#atlas-test-search").fill("#003");
    await page.waitForFunction(() => document.querySelector(".atlas-node.selected b")?.textContent === "Greymon");
    assert.equal(await page.locator(".atlas-node.selected b").innerText(), "Greymon", "Busca por #ID não selecionou Greymon");

    await reset();
    await page.locator("#atlas-test-fill").click();
    await page.waitForFunction(() => AtlasFixture.progress.images === AtlasFixture.progress.total, null, { timeout: 10000 });
    assert.equal((await page.evaluate(() => AtlasFixture.progress)).images, 13, "Preenchimento não completou todas as imagens");
    await page.screenshot({ path: path.join(output, "all-images.png") });

    await reset();
    await page.locator('[data-atlas-mode="map"]').click();
    await page.waitForFunction(() => document.querySelectorAll(".atlas-node-cd").length === 12);
    await page.locator(".atlas-filter-menu > summary").click();
    await page.locator("#atlas-test-spoilers").check();
    await page.waitForFunction(() => document.querySelectorAll(".atlas-node-cd").length === 13);
    const overviewEdges = await page.locator("[data-atlas-edge]").count();
    assert(overviewEdges > 0 && overviewEdges <= 10, "Visão geral desenhou conexões fora da seleção");
    assert(await page.evaluate(() => [...document.querySelectorAll("[data-atlas-edge]")].every(path => {
      const edge = AtlasFixture.system.edges.find(item => item.id === path.dataset.atlasEdge);
      return edge?.from === AtlasFixture.state.nodeId || edge?.to === AtlasFixture.state.nodeId;
    })), "Visão geral mostrou arestas não incidentes");

    await page.setViewportSize({ width: 820, height: 900 });
    await page.waitForSelector("#atlas-shell.list-view");
    const narrow = await geometry();
    assert(narrow.bodyWidth <= narrow.viewportWidth + 1, "Modo estreito criou rolagem horizontal da página");
    await page.screenshot({ path: path.join(output, "list-820x900.png") });
    assert.deepEqual(errors, []);
    console.log("Atlas C+D fixture smoke passed: routes, alternatives, item, review #104, search, images, overview and 820px.");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
