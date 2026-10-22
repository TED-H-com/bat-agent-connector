import {test, expect, type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import {unavailableBriefTransport} from './pm-source-transport-fixture';
import {bootstrap, setup, changeIdentity} from './managed-storage-fixture';

async function writeForms(page: Page, suffix: string) {
  await page.route('**/__draft_src/**', async route => {
    const file = new URL(route.request().url()).pathname.split('/__draft_src/')[1];
    let body = await readFile(new URL('../src/' + file, import.meta.url), 'utf8');
    if (file.endsWith('.ts')) body = stripTypeScriptTypes(body);
    if (file === 'pm-workspace.js') body = body.replace('function durableForm(', 'export function durableForm(');
    await route.fulfill({contentType: 'text/javascript', body});
  });
  await unavailableBriefTransport(page, '__draft_src');
  await page.locator('textarea').first().fill(`Session draft ${suffix}`);
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.sync.')))).toBe(true);
  await page.evaluate(async suffix => {
    const {h} = await import('/__draft_src/ui/dom.js');
    const {t} = await import('/__draft_src/i18n.js');
    const {durableForm} = await import('/__draft_src/pm-workspace.js');
    const {createRecordForm} = await import('/__draft_src/create-record.js');
    const f = (window as any).__storage;
    const namespace = [f.endpoint, f.server, f.principal, f.actor].map(encodeURIComponent).join(':');
    const api = async () => {throw Error('Draft writer must not send');};
    const base = {h, t, api, caps: () => ({scopes: [], actions: []}), guard: () => {}, namespace, errorBox: () => h('p'), opStatus: () => ''};
    const create = createRecordForm({...base, kind: 'work_item', projectId: 'prj_' + 'a'.repeat(20)});
    document.body.append(create.box);
    const title = create.box.querySelector('input'); title.value = `Work title ${suffix}`; title.dispatchEvent(new Event('input'));
    for (const [action, name, text] of [['pm.message.append', 'content', `PM draft ${suffix}`], ['session.send', 'text', `Follow-up ${suffix}`]]) {
      const input = h('textarea');
      const target = {workspace_id: 'ws_' + 'c'.repeat(32), ...(action === 'session.send'
        ? {work_item_id: 'wi_' + 'd'.repeat(20)} : {thread_id: 'pmt_' + 'e'.repeat(32)})};
      const form = durableForm({...base, action, target, fields: {[name]: input},
        params: (values: any) => action === 'session.send' ? {text: values.text.trim(), schedule: 'after_current'} : {content: values.content},
        preconditions: () => action === 'session.send' ? {expected_attempt_id: 'att_' + 'f'.repeat(32)} : {expected_version: 1},
        permitted: () => false, label: 'send', extra: input});
      document.body.append(form.box); input.value = text; input.dispatchEvent(new Event('input')); form.dispose(); form.box.remove();
    }
    create.box.remove();
  }, suffix);
}

const labels = (locale: string) => locale === 'zh-TW'
  ? {title: '先前本機位址的草稿', copy: '複製文字', hide: '隱藏', notice: '找到先前本機位址留下的草稿。'}
  : {title: 'Drafts from earlier local addresses', copy: 'Copy text', hide: 'Hide', notice: 'Drafts from an earlier local address are available.'};

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 1440]) {
  test(`earlier drafts from production writers can only be copied or hidden (${locale}, ${width})`, async ({browser}, testInfo) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}, permissions: ['clipboard-read', 'clipboard-write']});
    const page = await context.newPage(), words = labels(locale);
    await setup(page, {source: 'external'});
    await writeForms(page, 'one');
    await changeIdentity(page, {endpoint: 'http://127.0.0.1:41002/'}); await page.reload();
    await writeForms(page, 'two');
    const before = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.')).sort());
    await changeIdentity(page, {endpoint: 'http://127.0.0.1:41003/', source: 'managed'}); await page.reload();
    await expect(page.locator('textarea').first()).toHaveValue('');
    await page.locator('textarea').first().fill('Current draft');
    const current = await page.evaluate(() => {
      const namespace = Object.keys(localStorage).find(key => key.startsWith('batc.sync.managed:'))!.slice('batc.sync.'.length);
      // The existing operation-key writer's persisted {key, op} envelope.
      const key = `batc.key.${namespace}.fixture-original`, value = JSON.stringify({key: 'original-current-key', op: 'op_' + 'a'.repeat(32)});
      localStorage.setItem(key, value); return {key, value};
    });
    await expect(page.getByText(words.notice, {exact: true})).toBeVisible();
    await page.getByRole('link', {name: words.title, exact: true}).click();
    const panel = page.locator('[data-earlier-drafts]');
    await expect(panel).toBeVisible();
    await expect(panel.locator('summary')).toBeInViewport();
    await expect(panel.locator('[data-earlier-draft]')).toHaveCount(8);
    expect(await panel.locator('textarea').evaluateAll(nodes => nodes.map(node => (node as HTMLTextAreaElement).value).sort())).toEqual(
      ['Session draft', 'Work title', 'PM draft', 'Follow-up'].flatMap(text => [`${text} one`, `${text} two`]).sort());
    await expect(panel.locator('textarea:not([readonly])')).toHaveCount(0);
    await expect(panel.locator('[data-error-summary], [role=alert]')).toHaveCount(0);
    const row = panel.locator('[data-earlier-draft]').first();
    const copied = await row.locator('textarea').inputValue();
    await row.getByRole('button', {name: words.copy, exact: true}).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(copied);
    await row.getByRole('button', {name: words.hide, exact: true}).click();
    await expect(panel.locator('[data-earlier-draft]')).toHaveCount(7);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await panel.screenshot({path: testInfo.outputPath('earlier-drafts.png')});
    await page.reload();
    await expect(page.locator('[data-earlier-draft]')).toHaveCount(7);
    await expect(page.getByText(words.notice, {exact: true})).toHaveCount(0);
    const after = await page.evaluate(() => ({entries: Object.fromEntries(Object.entries(localStorage)), writes: (window as any).__storage.writes}));
    for (const [key, value] of before) expect(after.entries[key]).toBe(value);
    expect(Object.entries(after.entries).filter(([key]) => key.startsWith('batc.draft.managed:')).map(([, value]) => value)).toEqual(['Current draft']);
    expect(after.writes).toBe(0);
    expect(after.entries[current.key]).toBe(current.value);
    await context.close();
  });
}

for (const locale of ['en-US', 'zh-TW']) {
  for (const field of ['principal', 'server', 'actor', 'ambiguous', 'migrated', 'browser', 'unverified']) {
    test(`earlier draft recovery excludes ${field} (${locale})`, async ({browser}) => {
      const context = await browser.newContext({locale}); const page = await context.newPage();
      await setup(page, {source: 'external'}); await writeForms(page, 'private');
      if (field === 'ambiguous') {
        await changeIdentity(page, {actor: bootstrap.capabilities.actor + '.send'}); await page.reload(); await writeForms(page, 'other actor');
      }
      const replacement: Record<string, string> = {source: 'managed', endpoint: 'http://127.0.0.1:41003/', actor: bootstrap.capabilities.actor};
      if (['principal', 'server', 'actor'].includes(field)) replacement[field] = 'different-identity';
      let browserPosts = 0;
      if (field === 'browser') {
        await page.addInitScript(() => {
          delete (window as any).isTauri; delete (window as any).__TAURI_INTERNALS__;
          localStorage.setItem('batc.dashboard.token', 'fixture-browser-token');
        });
        await page.route('**/api/v1/**', async route => {
          if (route.request().method() === 'POST') browserPosts++;
          const path = new URL(route.request().url()).pathname.slice('/api/v1'.length);
          let data: any;
          if (path === '/bootstrap') data = bootstrap;
          else if (path === '/capabilities') data = bootstrap.capabilities;
          else if (path === '/events') data = {events: [], next_cursor: 0, head_cursor: 0, has_more: false, sync: bootstrap.sync};
          else if (path === '/sessions/demo/session-1') data = {session: {host: 'demo', session_id: 'session-1', title: 'Fixture session', api_access: 'managed', provenance: 'connector'}};
          else data = bootstrap.snapshot[path.slice(1).replaceAll('-', '_')];
          await route.fulfill({status: data ? 200 : 404, json: data || {error: {code: 'NOT_FOUND', message: 'Fixture read model unavailable'}}});
        });
      }
      if (field === 'unverified') await page.addInitScript(() => {
        const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
        const getItem = Storage.prototype.getItem;
        (window as any).__legacyReads = 0;
        Storage.prototype.getItem = function(key: string) {
          if (key.includes('http%3A%2F%2F')) (window as any).__legacyReads++;
          return getItem.call(this, key);
        };
        bridge.invoke = async (command: string, args: any) => {
          if (command === 'connector_request' && args.input.path === '/bootstrap') {
            (window as any).__bootstrapWaiting = true; return new Promise(() => {});
          }
          return invoke(command, args);
        };
      });
      await changeIdentity(page, replacement); await page.reload();
      if (field === 'unverified') await expect.poll(() => page.evaluate(() => (window as any).__bootstrapWaiting)).toBe(true);
      if (field === 'migrated') await expect(page.locator('textarea').first()).toHaveValue('Session draft private');
      if (field !== 'unverified') await expect(page.locator('textarea').first()).toBeVisible();
      await page.evaluate(() => {location.hash = '#/settings?section=about';});
      await expect(page.locator('[data-earlier-drafts], [data-earlier-drafts-notice]')).toHaveCount(0);
      expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
      expect(browserPosts).toBe(0);
      if (field === 'unverified') expect(await page.evaluate(() => (window as any).__legacyReads)).toBe(0);
      await context.close();
    });
  }
}
