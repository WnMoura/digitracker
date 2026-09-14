// Mobile Proposal E regression. Serve ui/companion, then run this file with NODE_PATH set to the bundled node_modules.
const assert = require("node:assert/strict");
const path = require("node:path");
const {mkdir} = require("node:fs/promises");
const {chromium, webkit} = require("playwright");

(async () => {
  const output = path.resolve(__dirname, "../build-check/companion-ui-smoke");
  await mkdir(output, {recursive: true});
  const engine = process.env.PW_ENGINE === "webkit" ? "webkit" : "chromium";
  const browserType = engine === "webkit" ? webkit : chromium;
  const launchOptions = {headless: true};
  if (engine === "chromium" && process.env.PW_CHANNEL) launchOptions.channel = process.env.PW_CHANNEL;
  const browser = await browserType.launch(launchOptions);
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.goto(`${process.argv[2] || "http://127.0.0.1:8781"}/index.html?demo=1`, {waitUntil: "domcontentloaded"});
    await page.waitForSelector(".mobile-game-hero");
    await page.waitForTimeout(300);

    const stateChecks = await page.evaluate(async () => {
      const state = await import("./state.js");
      let next = 0;
      const fallbackId = state.requestId({
        getRandomValues(bytes) {
          for (let index = 0; index < bytes.length; index += 1) bytes[index] = next++;
          return bytes;
        },
      });
      const data = {progress: {value_versions: {"progress:complete:block-a": 2}}};
      const values = {kind: "progress", action: "complete", target: {block_id: "block-a"}};
      const firstVersion = state.versionFor(data, values);
      data.progress.value_versions["progress:complete:block-a"] = 7;
      const replayVersion = state.versionFor(data, values);

      const requirementData = {
        system_state: {
          completed_requirements: ["shared-condition"],
          completed_requirement_targets: [
            "requirement:system-a:edge-a:shared-condition",
            "requirement:system-a:edge-b:shared-condition",
          ],
          requirements_scoped: true,
          value_versions: {},
        },
      };
      const patchedRequirement = state.patchConfirmedValue(requirementData, {
        ok: true,
        kind: "requirement",
        target: "requirement:system-a:edge-a:shared-condition",
        value: false,
        value_version: 3,
      });
      const operations = [
        {id: "one", target: "progress:checkpoint", status: "pending", values: {_expectedValueVersion: 0}, body: {expected_value_version: 0}},
        {id: "two", target: "progress:checkpoint", status: "pending", values: {_expectedValueVersion: 0}, body: {expected_value_version: 0}},
        {id: "three", target: "item:x", status: "pending", values: {_expectedValueVersion: 4}, body: {expected_value_version: 4}},
      ];
      const rebased = state.rebaseNextDependent(operations, 0, "progress:checkpoint", 1);
      const checkpointTarget = state.targetKey({kind: "progress", action: "checkpoint", target: {block_id: "block-a"}});
      return {
        fallbackId,
        firstVersion,
        replayVersion,
        checkpointTarget,
        rebased,
        legacyRequirementKept: patchedRequirement.system_state.completed_requirements.includes("shared-condition"),
        remainingScopedTargets: patchedRequirement.system_state.completed_requirement_targets,
      };
    });
    assert.match(stateChecks.fallbackId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      "requestId fallback must use a UUID v4 generated with crypto.getRandomValues");
    assert.equal(stateChecks.firstVersion, 2, "Initial target version was not captured");
    assert.equal(stateChecks.replayVersion, 2,
      "Queued operations must keep their original expected version so reconnect exposes a conflict instead of overwriting PC state");
    assert.equal(stateChecks.checkpointTarget, "progress:checkpoint", "Checkpoint concurrency must be guide-scoped");
    assert.equal(stateChecks.rebased.index, 1, "Only the next dependent operation should be rebased");
    assert.equal(stateChecks.rebased.operation.body.expected_value_version, 1,
      "The next same-target offline intent must advance from the version confirmed by its predecessor");
    assert.equal(stateChecks.legacyRequirementKept, true,
      "Legacy requirement projection must remain marked while another scoped route with the same requirement id is still complete");
    assert.deepEqual(stateChecks.remainingScopedTargets, ["requirement:system-a:edge-b:shared-condition"]);

    assert.match(await page.evaluate(() => document.querySelector("#tabs button.active")?.textContent || ""), /Início/);
    await page.locator("#tabs [data-tab='guide']").click();
    await page.waitForSelector(".reading-card");
    await page.locator("[data-act='guide-mode'][data-mode='index']").click();
    assert(await page.locator(".guide-index-row").count() >= 2, "Guide index is not interactive");
    await page.locator(".guide-index-row").first().click();
    await page.locator("[data-act='complete']").click();
    assert.match(await page.locator("[data-act='complete']").innerText(), /Concluído/);

    await page.locator("#tabs [data-tab='atlas']").click();
    await page.locator(".system-row").first().click();
    await page.locator(".entity-row").nth(1).click();
    await page.waitForSelector(".atlas-route");
    assert(await page.locator(".route-node").count() >= 1, "Atlas must use a vertical route, not a full graph");
    await page.locator("[data-act='goal']").click();
    await page.locator("[data-act='atlas-mode'][data-mode='entities']").click();
    await page.locator("#atlas-local-search").fill("#24");
    assert.equal(await page.locator(".entity-row").count(), 1, "Atlas local search did not resolve #ID");
    await page.locator("#atlas-local-search").fill("");

    await page.locator("#tabs [data-tab='items']").click();
    await page.locator("#item-local-search").fill("digivice");
    assert.equal(await page.locator("[data-act='item-open']").count(), 1, "Items local search did not resolve name");
    await page.locator("#item-local-search").fill("");
    await page.locator("[data-act='item-open']").first().click();
    await page.waitForSelector(".item-detail");
    await page.locator("[data-item-input]").fill("2");
    await page.locator("[data-act='item-save']").click();
    await page.locator("[data-act='item-back']").click();
    await page.waitForSelector("[data-act='item-open']");
    await page.locator("#tabs [data-tab='more']").click();
    await page.locator("[data-act='more-mode'][data-mode='connection']").click();
    assert.match(await page.locator(".connection-panel").innerText(), /Armazenamento offline/);

    await page.locator("#mobile-search-toggle").click();
    await page.locator("#global-search").fill("agumon");
    assert(await page.locator(".search-results .simple-row").count() >= 1, "Search did not return Atlas result");
    await page.locator("[data-act='search-atlas']").first().click();
    await page.waitForSelector("[data-act='search-back']");
    await page.locator("[data-act='search-back']").click();
    assert.equal(await page.locator("#global-search").inputValue(), "agumon", "Search query was not restored after detail");
    await page.locator("#global-search").fill("");
    assert.match(await page.locator(".search-results").innerText(), /Buscar no jogo/);
    await page.locator("[data-act='search-close']").click();

    await page.locator("#tabs [data-tab='items']").click();
    await page.locator("[data-act='item-open']").first().click();
    await page.waitForSelector(".item-detail");
    assert.match(await page.locator(".item-detail").innerText(), /Como obter/);
    await page.locator("[data-act='item-back']").click();
    assert(await page.locator("[data-act='item-open']").count() >= 1, "Item list did not return from detail");

    await page.locator("#tabs [data-tab='guide']").click();
    await page.locator("[data-act='guide-mode'][data-mode='read']").click();
    await page.locator("[data-act='source']").first().click();
    await page.waitForSelector(".source-reference");
    assert.match(await page.locator(".source-modal").innerText(), /Início da Aventura|Trecho correspondente/);
    await page.locator("[data-act='source-close']").click();

    await page.evaluate(() => document.body.classList.add("keyboard-open"));
    assert.equal(await page.locator("#tabs").evaluate((node) => getComputedStyle(node).display), "none", "Bottom nav must hide while the software keyboard is open");
    await page.evaluate(() => document.body.classList.remove("keyboard-open"));

    for (const width of [360, 390, 430]) {
      await page.setViewportSize({width, height: 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1),
        `Horizontal page overflow at ${width}px`);
      await page.screenshot({path: path.join(output, `${engine}-proposal-e-${width}.png`)});
    }
    assert.deepEqual(errors, []);
    console.log(`Companion UI smoke passed on ${engine}: 360, 390 and 430px; Guide, Atlas, items, More, search and offline version guards.`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
