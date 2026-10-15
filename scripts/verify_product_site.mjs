// Browser verification of the static site; does not contact central or any BAT host.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { chromium } from '../desktop/node_modules/@playwright/test/index.mjs';
const root = new URL('../site/', import.meta.url).pathname;
const output = process.env.SITE_SCREENSHOT_DIR;
if (!output) throw new Error('Set SITE_SCREENSHOT_DIR to a disk-backed evidence directory.');
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
    await page.screenshot({ path: resolve(output, `hero-${lang}-${width}.png`) });
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
      await page.screenshot({ path: resolve(output, `${view}-${lang}-${width}.png`) });
    }
    await page.locator('[data-view="overview"]').click();
    await page.evaluate(() => window.scrollTo({top: 0, behavior: 'instant'}));
    await page.screenshot({ path: resolve(output, `page-${lang}-${width}.png`), fullPage: true });
    for (const section of ['workflow', 'features', 'architecture', 'windows', 'start', 'platforms', 'decisions', 'status', 'docs', 'faq']) {
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({
        path: resolve(output, `${section}-${lang}-${width}.png`),
        fullPage: true,
        clip: await page.locator('#' + section).boundingBox(),
      });
    }
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
    await page.locator('#workflow').scrollIntoViewIfNeeded();
    await page.screenshot({path: resolve(output, `language-switch-${lang}-${width}.png`)});
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
  await fallback.screenshot({ path: resolve(output, 'no-js-390.png') });
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
  await writeFile(resolve(output, 'result.json'), JSON.stringify({ results, noJavaScript: true, blockedStorage: true, notFound: true }, null, 2) + '\n');
  console.log('Passed 6 responsive bilingual scenarios, all 8 gallery scenes (48 selections), keyboard/mobile navigation, preference persistence, blocked storage, no-JS fallback and 404 recovery.');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
