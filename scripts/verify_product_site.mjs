// Browser verification of the static site; does not contact central or any BAT host.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { chromium } from '../desktop/node_modules/@playwright/test/index.mjs';
const root = new URL('../site/', import.meta.url).pathname;
const output = process.env.SITE_SCREENSHOT_DIR;
if (!output) throw new Error('Set SITE_SCREENSHOT_DIR to a disk-backed evidence directory.');
// Limit review captures without skipping any browser checks. CI keeps the full set.
const sections = new Set((process.env.SITE_SCREENSHOT_SECTIONS || '').split(',').filter(Boolean));
async function screenshot(page, name, options = {}) {
  if (sections.size && !sections.has(name.replace(/-(?:en|zh-TW)-\d+$/, ''))) return;
  await page.screenshot({ path: resolve(output, name + '.png'), ...options });
}
await mkdir(output, { recursive: true });
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
  const prefix = '/bat-agent-connector/';
  const relative = path.startsWith(prefix) ? path.slice(prefix.length) : '404.html';
  const file = resolve(root, !relative || relative.endsWith('/') ? relative + 'index.html' : relative);
  if (!file.startsWith(resolve(root) + sep)) { res.writeHead(400).end(); return; }
  try {
    const body = await readFile(file);
    res.writeHead(200, { 'Content-Type': types[extname(file)] || 'text/plain' }).end(body);
  } catch {
    const body = await readFile(resolve(root, '404.html'));
    res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' }).end(body);
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const base = origin + '/bat-agent-connector/';
const views = ['overview', 'discussions', 'requirements', 'issues', 'schedule', 'dependencies', 'board', 'session'];
const manifest = JSON.parse(await readFile(resolve(root, 'images/manifest.json'), 'utf8'));
const browser = await chromium.launch({args: ['--disable-dev-shm-usage']});
const results = [];
const layoutResults = [];
async function checkLayout(page, lang, width) {
  const checks = await page.evaluate(({ lang, width }) => {
    const locale = lang === 'en' ? 'en' : 'zh';
    const visibleText = selector => document.querySelector(selector).innerText;
    const table = document.querySelector('#platforms .table-wrap');
    const bounds = element => element.getBoundingClientRect();
    const withinViewport = element => {
      const rect = bounds(element);
      return rect.left >= 0 && rect.right <= innerWidth;
    };
    const delivery = document.querySelector('.delivery-node');
    const caption = delivery.querySelector('p');
    const center = element => (bounds(element).left + bounds(element).right) / 2;
    const steps = [...document.querySelectorAll('.install-steps li')].map(element => bounds(element).width);
    const commandBlocks = [...document.querySelectorAll('#start pre code')];
    const expectedCommands = ['--tag v0.2.12 --channel-check stable', '--tag v0.2.13-preview.20261022 --channel-check preview'];
    const tokensUnbroken = commandBlocks.every(code => {
      const text = code.firstChild;
      return text?.nodeType === Node.TEXT_NODE && [...text.textContent.matchAll(/\S+/g)].every(match => {
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        return range.getClientRects().length === 1;
      });
    });
    const walker = document.createTreeWalker(document.querySelector('#start'), NodeFilter.SHOW_TEXT);
    let inlineFlagsUnbroken = true;
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (!text.parentElement.getClientRects().length) continue;
      for (const match of text.textContent.matchAll(/--[a-z][a-z-]*/g)) {
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        inlineFlagsUnbroken &&= range.getClientRects().length === 1;
      }
    }
    const faqAligned = [...document.querySelectorAll(`#faq summary .lang-${locale}`)].every(text => {
      const range = document.createRange();
      range.selectNodeContents(text);
      const lines = [...range.getClientRects()];
      return lines.every(line => Math.abs(line.left - lines[0].left) < 1);
    });
    const platformNote = visibleText('#platforms .section-note');
    const zh = visibleText('main') + visibleText('.site-footer');
    return {
      platformColumns: table.scrollWidth <= table.clientWidth && [...table.querySelectorAll('tbody th, tbody td')].every(withinViewport),
      architectureCaption: Math.abs(center(delivery) - center(caption)) < 2,
      installColumns: width <= 640 || Math.max(...steps) - Math.min(...steps) < 2,
      fullSourceCopy: visibleText('.install-steps').includes('437ee6a7143a82e32eae1d6a008203b2df072aaf'),
      commandCopy: JSON.stringify(commandBlocks.map(code => code.textContent)) === JSON.stringify(expectedCommands),
      commandWrapping: commandBlocks.length === 2 && tokensUnbroken && inlineFlagsUnbroken,
      faqHangingIndent: faqAligned,
      updateScope: platformNote.includes(lang === 'en' ? 'Connector automatic updates' : 'Connector 自動更新')
        && platformNote.includes(lang === 'en' ? 'signed desktop updates' : '桌面版簽章更新')
        && !!document.querySelector('#platforms a[href$="docs/design/connector-updates.md"]'),
      queuedFollowUps: lang === 'en' || (!zh.includes('持久第二句') && zh.includes('排隊中的追加指令')),
      translatedTerms: lang === 'en' || (!/主機與 workspace|連線 owner|舊 owner|非官方 companion/.test(zh)
        && zh.includes('選定的 BAT 主機與工作區') && zh.includes('連線擁有者') && zh.includes('非官方輔助工具')),
      overflow: document.documentElement.scrollWidth <= innerWidth,
    };
  }, { lang, width });
  layoutResults.push({ lang, width, checks });
}
try {
  for (const lang of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, locale: lang === 'en' ? 'en-US' : lang, colorScheme: 'light' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const remote = [];
    page.on('request', request => { if (!request.url().startsWith(origin)) remote.push(request.url()); });
    await page.goto(base + '?lang=' + lang);
    assert.equal(await page.locator('html').getAttribute('lang'), lang === 'en' ? 'en' : 'zh-Hant');
    assert.equal(await page.locator('h1:visible').count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const firstUse = await page.locator('#start .lang-' + (lang === 'en' ? 'en' : 'zh')).allTextContents();
    assert.ok(firstUse.join(' ').includes(lang === 'en' ? '1. Connect BAT' : '1. 連線至 BAT'));
    assert.ok(firstUse.join(' ').includes(lang === 'en' ? 'View sessions' : '查看工作階段'));
    assert.ok(firstUse.join(' ').includes(lang === 'en' ? 'collapsed until needed' : '需要時再展開'));
    assert.ok((await page.locator('#product .release-note').innerText()).includes(manifest.frontend_source_commit));
    await checkLayout(page, lang, width);
    await screenshot(page, `hero-${lang}-${width}`);
    const demoLink = page.locator('.hero a[href="demo/?lang=' + lang + '"]:visible');
    assert.equal(await demoLink.count(), 1);
    await demoLink.click();
    await page.locator('[data-pm-overview]').waitFor();
    assert.ok((await page.locator('#demo-bar').innerText()).includes(lang === 'en' ? 'Sample data' : '合成資料'));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('#demo-bar a[href*="prototype"]').click();
    await page.getByTestId('composer-textarea').waitFor();
    await page.getByTestId('composer-textarea').fill('Keep this preview draft');
    if (!await page.getByTestId('nav-item-requirements').isVisible()) await page.getByTestId('nav-drawer-toggle').click();
    const opened = page.waitForEvent('popup');
    await page.getByTestId('nav-item-requirements').click();
    const other = await opened;
    await other.locator('[data-pm-region="requirements"]').waitFor();
    assert.equal(await page.getByTestId('composer-textarea').inputValue(), 'Keep this preview draft');
    await other.close();
    await page.goto(base + '?lang=' + lang);
    await page.locator('#product').scrollIntoViewIfNeeded();
    assert.equal(await page.locator('[data-view]').count(), views.length);
    assert.equal(await page.locator('#view-overview').isVisible(), true);
    for (const view of views) {
      await page.locator(`[data-view="${view}"]`).focus();
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(`#view-${view}`).isVisible(), true);
      assert.equal(await page.locator('.product-shot:visible').count(), 1);
      assert.equal(await page.locator(`[data-view="${view}"]`).getAttribute('aria-pressed'), 'true');
      await page.locator(`#view-${view} img:visible`).evaluate(img => img.decode());
      const entry = manifest.images.find(entry => entry.file === `${view}-${lang === 'en' ? 'en' : 'zh'}.png`);
      const size = await page.locator(`#view-${view} img:visible`).evaluate(img => ({
        width: img.naturalWidth, height: img.naturalHeight, declaredWidth: Number(img.getAttribute('width')), declaredHeight: Number(img.getAttribute('height')),
      }));
      assert.deepEqual(size, {...entry.image_size, declaredWidth: entry.image_size.width, declaredHeight: entry.image_size.height});
      assert.equal(await page.locator(`#view-${view} img:visible`).getAttribute('src'), `images/${view}-${lang === 'en' ? 'en' : 'zh'}.png`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await screenshot(page, `${view}-${lang}-${width}`);
    }
    await page.locator('[data-view="overview"]').click();
    await page.evaluate(() => window.scrollTo({top: 0, behavior: 'instant'}));
    await screenshot(page, `page-${lang}-${width}`, { fullPage: true });
    for (const section of ['workflow', 'features', 'architecture', 'windows', 'start', 'platforms', 'decisions', 'status', 'docs', 'faq']) {
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      await screenshot(page, `${section}-${lang}-${width}`, {
        fullPage: true,
        clip: await page.locator('#' + section).boundingBox(),
      });
    }
    await screenshot(page, `footer-${lang}-${width}`, { fullPage: true, clip: await page.locator('.site-footer').boundingBox() });
    await page.locator('#faq summary').first().click();
    assert.equal(await page.locator('#faq details').first().getAttribute('open'), '');
    await page.evaluate(() => { location.hash = 'workflow'; });
    if (width <= 1000) {
      await page.locator('.menu-button').click();
      assert.equal(await page.locator('.menu-button').getAttribute('aria-expanded'), 'true');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.menu-button').getAttribute('aria-expanded'), 'false');
      assert.equal(await page.locator('.menu-button').evaluate(button => button === document.activeElement), true);
      await page.locator('.menu-button').click();
    }
    const changed = lang === 'en' ? 'zh' : 'en';
    await page.locator(`[data-language="${changed}"]`).click();
    assert.equal(new URL(page.url()).hash, '#workflow');
    const switchChecks = await page.evaluate(() => ({
      menuClosed: document.querySelector('.menu-button').getAttribute('aria-expanded') === 'false',
      headingVisible: document.querySelector('#workflow h2').getBoundingClientRect().top >= document.querySelector('.site-header').getBoundingClientRect().bottom
        && document.querySelector('#workflow h2').getBoundingClientRect().bottom <= innerHeight,
    }));
    layoutResults.push({ lang, width, checks: switchChecks });
    await screenshot(page, `language-switch-${lang}-${width}`);
    // Switching at a later section must also retain its heading, in both directions.
    await page.evaluate(() => { location.hash = 'start'; document.querySelector('#start').scrollIntoView({behavior: 'instant'}); });
    if (width <= 1000 && await page.locator('.menu-button').getAttribute('aria-expanded') === 'false') await page.locator('.menu-button').click();
    await page.locator(`[data-language="${lang === 'en' ? 'en' : 'zh'}"]`).click();
    const startHeadingVisible = await page.evaluate(() => {
      const heading = document.querySelector('#start h2').getBoundingClientRect();
      return heading.top >= document.querySelector('.site-header').getBoundingClientRect().bottom && heading.bottom <= innerHeight;
    });
    layoutResults.push({ lang, width, checks: { startHeadingVisible } });
    await screenshot(page, `language-switch-start-${lang}-${width}`);
    if (width <= 1000 && await page.locator('.menu-button').getAttribute('aria-expanded') === 'false') await page.locator('.menu-button').click();
    await page.locator(`[data-language="${changed}"]`).click();
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('data-lang'), changed);
    await page.goto(base); // Saved preference remains available without URL override.
    assert.equal(await page.locator('html').getAttribute('data-lang'), changed);
    if (width <= 1000) {
      await page.locator('.menu-button').click();
      await page.locator('.site-nav a[href="#docs"]').click();
      assert.equal(await page.locator('.menu-button').getAttribute('aria-expanded'), 'false');
      assert.equal(new URL(page.url()).hash, '#docs');
    }
    assert.deepEqual(remote, []);
    assert.deepEqual(errors, []);
    results.push({ lang, width, overflow: false, gallery: true, language: true, navigation: true, errors });
    await context.close();
  }
  const noJS = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  const fallback = await noJS.newPage();
  await fallback.goto(base);
  for (const view of views) {
    assert.equal(await fallback.locator(`#view-${view}`).isVisible(), true);
    await fallback.locator(`#view-${view} img:visible`).evaluate(img => img.decode());
  }
  assert.equal(await fallback.locator('h1:visible').count(), 1);
  assert.equal(await fallback.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await screenshot(fallback, 'no-js-390');
  await noJS.close();
  const blocked = await browser.newContext({ locale: 'en-US' });
  const page = await blocked.newPage();
  await page.addInitScript(() => Object.defineProperty(window, 'localStorage', { get() { throw new Error('Storage unavailable'); } }));
  await page.goto(base + '?lang=zh-TW');
  assert.equal(await page.locator('html').getAttribute('data-lang'), 'zh');
  await page.locator('[data-language="en"]').click();
  assert.equal(await page.locator('html').getAttribute('data-lang'), 'en');
  const response = await page.goto(base + 'missing-page');
  assert.equal(response.status(), 404);
  await page.locator('a[href="/bat-agent-connector/"]').click();
  assert.equal(new URL(page.url()).pathname, '/bat-agent-connector/');
  await blocked.close();
  await writeFile(resolve(output, 'result.json'), JSON.stringify({ results, layoutResults, noJavaScript: true, blockedStorage: true, notFound: true }, null, 2) + '\n');
  const failedLayout = layoutResults.flatMap(({ lang, width, checks }) => Object.entries(checks).filter(([, passed]) => !passed).map(([check]) => `${lang}/${width}: ${check}`));
  assert.deepEqual(failedLayout, [], 'Public site layout and copy regressions');
  console.log('Passed 6 responsive bilingual scenarios, all 8 gallery scenes (48 selections), keyboard/mobile navigation, preference persistence, blocked storage, no-JS fallback and 404 recovery.');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
