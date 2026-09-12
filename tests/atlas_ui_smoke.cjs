/* Optional browser regression. Serve the repo locally, then:
 * NODE_PATH=<runtime node_modules> node tests/atlas_ui_smoke.cjs http://127.0.0.1:8765
 * Uses synthetic data only; never connects to the pywebview API or downloads art.
 */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { mkdir } = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const output = path.resolve(__dirname, '../build-check/atlas-ui-smoke');
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.route('**/ui/**', route => route.continue());
    await page.goto(`${process.argv[2] || 'http://127.0.0.1:8765'}/ui/index.html`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(async () => {
      booted = true; S.mode = 'demo'; S.view = 'dashboard'; S.activeSlug = DEMO[0].slug;
      S.tab = 'atlas'; S.library = DEMO_LIB(); S.guideAtlas.nodeId = 'node_greymon';
      await renderDashboard({ force: true });
    });
    const settle = () => page.waitForFunction(() => !!S.guideAtlas.viewKey && document.querySelector('.atlas-v2')?.style.getPropertyValue('--atlas-shell-height'));
    const geometry = () => page.evaluate(() => {
      const main = document.querySelector('.atlas-main').getBoundingClientRect();
      const shell = document.querySelector('.atlas-v2').getBoundingClientRect();
      const viewport = document.querySelector('#atlas-viewport').getBoundingClientRect();
      return {
        bottomFits: shell.bottom <= main.bottom + 1,
        widthFits: shell.right <= main.right + 1,
        zoom: S.guideAtlas.zoom,
        cards: [...document.querySelectorAll('.atlas-node')].map(element => {
          const rect = element.getBoundingClientRect();
          return { width: rect.width, fits: rect.left >= viewport.left - 1 && rect.right <= viewport.right + 1
            && rect.top >= viewport.top - 1 && rect.bottom <= viewport.bottom + 1 };
        }),
      };
    });
    for (const [width, height] of [[1600, 900], [1280, 720]]) {
      await page.setViewportSize({ width, height });
      await page.evaluate(async () => { S.guideAtlas.viewKey = ''; await renderDashboard({ force: true }); });
      await settle();
      const result = await geometry();
      assert(result.bottomFits && result.widthFits, `Workspace overflow at ${width}: ${JSON.stringify(result)}`);
      assert(result.zoom >= .85, `Unreadable autozoom at ${width}`);
      assert(result.cards.every(card => card.fits && card.width >= 145), `Clipped focus card at ${width}: ${JSON.stringify(result)}`);
      await page.screenshot({ path: path.join(output, `focus-${width}.png`) });
      await page.evaluate(async () => {
        DEMO[0].smart_guide.current.systems[0].status = 'suggested';
        S.guideAtlas.viewKey = ''; await renderDashboard({ force: true });
      });
      await settle();
      const preview = await geometry();
      assert(preview.bottomFits && preview.widthFits && preview.cards.every(card => card.fits && card.width >= 145),
        `Review preview overflow at ${width}: ${JSON.stringify(preview)}`);
      assert(await page.locator('.atlas-review').evaluate(element => element.clientHeight <= 70), 'Review header consumed the map');
      await page.evaluate(async () => {
        DEMO[0].smart_guide.current.systems[0].status = 'approved';
        S.guideAtlas.viewKey = ''; await renderDashboard({ force: true });
      });
      await settle();
    }
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.locator('#atlas-search').fill('#003');
    await page.waitForFunction(() => S.guideAtlas.search === '#003');
    assert.equal(await page.locator('.atlas-node.selected b').innerText(), 'Greymon');
    assert(await page.locator('.atlas-node').count() > 1, 'Search hid neighboring routes');
    const checked = await page.locator('[data-atlas-requirement]').first().isChecked();
    await page.locator('[data-atlas-requirement]').first().click();
    assert.equal(await page.locator('[data-atlas-requirement]').first().isChecked(), !checked);
    await page.locator('#atlas-fill-images').click();
    await page.locator('#atlas-image-context').fill('Digimon World 3');
    await page.locator('#atlas-image-wiki').fill('https://wikimon.net/Agumon');
    assert.match(await page.locator('#atlas-image-example').innerText(), /^Digimon World 3 .+ site:wikimon.net$/);
    await page.screenshot({ path: path.join(output, 'image-context.png') });
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#atlas-image-modal').count(), 0);
    await page.evaluate(async () => {
      const ref = [{ section: 1, block: 1, page: 1 }];
      const nodes = Array.from({ length: 150 }, (_, i) => ({ id: `n${i}`, label: `Entidade de teste ${i}`, card_number: i + 1, source_refs: ref }));
      const edges = nodes.slice(1).map((node, i) => ({ id: `e${i}`, from: `n${i}`, to: node.id, requirements: [], source_refs: ref }));
      edges.push({ id: 'cycle', from: 'n80', to: 'n78', requirements: [], source_refs: ref });
      DEMO[0].smart_guide.current.systems = [{ id: 'large', title: 'Teste 150 entidades', status: 'approved', nodes, edges, source_refs: ref }];
      S.guideAtlas.systemId = ''; S.guideAtlas.nodeId = 'n79'; S.guideAtlas.search = ''; S.guideAtlas.viewKey = '';
      const layout = guideSystemLayout(DEMO[0].smart_guide.current.systems[0], nodes);
      if (new Set([...layout.positions.values()].map(p => `${p.x}:${p.y}`)).size !== nodes.length) throw Error('Overlapping graph positions');
      await renderDashboard({ force: true });
    });
    await settle();
    assert((await page.locator('.atlas-node').count()) <= 5, 'Full graph leaked into default focus mode');
    await page.locator('[data-atlas-mode="map"]').click();
    await settle();
    assert.equal(await page.locator('.atlas-node').count(), 150);
    const selectedFits = await page.evaluate(() => {
      const a = document.querySelector('.atlas-node.selected').getBoundingClientRect();
      const b = document.querySelector('#atlas-viewport').getBoundingClientRect();
      return a.top >= b.top && a.bottom <= b.bottom && a.left >= b.left && a.right <= b.right;
    });
    assert(selectedFits, 'Full map did not keep the selected entity in view');
    await page.setViewportSize({ width: 820, height: 900 });
    await page.waitForSelector('.atlas-v2.list-view');
    assert(await page.evaluate(() => document.querySelector('.atlas-main').scrollWidth <= document.querySelector('.atlas-main').clientWidth + 1), 'Narrow layout overflows horizontally');
    await page.screenshot({ path: path.join(output, 'narrow-820.png') });
    assert.deepEqual(errors, []);
    console.log('Atlas UI smoke passed: 1600x900, 1280x720, 820x900, context dialog, search, progress, 150 nodes and a cycle.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
