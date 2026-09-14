from pathlib import Path

path = Path("tests/companion_ui_smoke.cjs")
text = path.read_text(encoding="utf-8")
text = text.replace('const {chromium} = require("playwright");', 'const {chromium, webkit} = require("playwright");', 1)
text = text.replace(
    '''  const browser = await chromium.launch({headless: true, channel: "msedge"});''',
    '''  const engine = process.env.PW_ENGINE === "webkit" ? "webkit" : "chromium";
  const browserType = engine === "webkit" ? webkit : chromium;
  const launchOptions = {headless: true};
  if (engine === "chromium" && process.env.PW_CHANNEL) launchOptions.channel = process.env.PW_CHANNEL;
  const browser = await browserType.launch(launchOptions);''',
    1,
)
old_items = '''    await page.locator("#tabs [data-tab='items']").click();
    await page.locator("#item-local-search").fill("digivice");
    assert.equal(await page.locator(".item-card").count(), 1, "Items local search did not resolve name");
    await page.locator("#item-local-search").fill("");
    await page.locator("[data-item-input]").fill("2");
    await page.locator("[data-act='item-save']").click();
    await page.locator("#tabs [data-tab='more']").click();
'''
new_items = '''    await page.locator("#tabs [data-tab='items']").click();
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
'''
if old_items in text:
    text = text.replace(old_items, new_items, 1)
text = text.replace('`proposal-e-${width}.png`', '`${engine}-proposal-e-${width}.png`')
text = text.replace(
    'console.log("Companion UI smoke passed: 360, 390 and 430px; Guide, Atlas, items, More, search and offline version guards.");',
    'console.log(`Companion UI smoke passed on ${engine}: 360, 390 and 430px; Guide, Atlas, items, More, search and offline version guards.`);',
)
path.write_text(text, encoding="utf-8", newline="\n")
print("Cross-browser smoke finalized.")
