// Mobile Proposal E regression. Serve ui/companion, then run this file with NODE_PATH set to the bundled node_modules.
const assert = require("node:assert/strict");
const path = require("node:path");
const {mkdir} = require("node:fs/promises");
const {chromium} = require("playwright");

(async () => {
  const output = path.resolve(__dirname, "../build-check/companion-ui-smoke");
  await mkdir(output, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: "msedge"});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.goto(`${process.argv[2] || "http://127.0.0.1:8781"}/index.html?demo=1`, {waitUntil: "domcontentloaded"});
    await page.waitForSelector(".mobile-game-hero");
    await page.waitForTimeout(300);
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
    assert.equal(await page.locator(".item-card").count(), 1, "Items local search did not resolve name");
    await page.locator("#item-local-search").fill("");
    await page.locator("[data-item-input]").fill("2");
    await page.locator("[data-act='item-save']").click();
    await page.locator("#tabs [data-tab='more']").click();
    await page.locator("[data-act='more-mode'][data-mode='connection']").click();
    assert.match(await page.locator(".connection-panel").innerText(), /Armazenamento offline/);

    await page.locator("#mobile-search-toggle").click();
    await page.locator("#global-search").fill("agumon");
    assert(await page.locator(".search-results .simple-row").count() >= 1, "Search did not return Atlas result");
    await page.locator("#global-search").fill("");
    assert.match(await page.locator(".search-results").innerText(), /Buscar no jogo/);
    await page.locator("[data-act='search-close']").click();

    for (const width of [360, 390, 430]) {
      await page.setViewportSize({width, height: 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1),
        `Horizontal page overflow at ${width}px`);
      await page.screenshot({path: path.join(output, `proposal-e-${width}.png`)});
    }
    assert.deepEqual(errors, []);
    console.log("Companion UI smoke passed: 360, 390 and 430px; Guide, Atlas, items, More and search.");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
