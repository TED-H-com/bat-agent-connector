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
    await route.fulfill({contentType: 'text/javascript', body});
  });
  await unavailableBriefTransport(page, '__draft_src');
  await page.locator('textarea').first().fill(`Session draft ${suffix}`);
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.sync.')))).toBe(true);
  await page.evaluate(async suffix => {
    const {h} = await import('/__draft_src/ui/dom.js');
    const {t} = await import('/__draft_src/i18n.js');
    const {pmDurableForm: durableForm} = await import('/__draft_src/pm-workspace.js');
    const {createRecordForm} = await import('/__draft_src/create-record.js');
    const f = (window as any).__storage;
    const namespace = [f.endpoint, f.server, f.principal, f.actor].map(encodeURIComponent).join(':');
    const api = async () => {throw Error('Draft writer must not send');};
    const base = {h, t, api, caps: () => ({scopes: [], actions: []}), guard: () => {}, namespace, errorBox: () => h('p'), opStatus: () => ''};
    const create = createRecordForm({...base, kind: 'work_item', projectId: 'prj_' + 'a'.repeat(20)});
    document.body.append(create.box);
    const title = create.box.querySelector('input'); title.value = `Work title ${suffix}`; title.dispatchEvent(new Event('input'));
    if (suffix === 'fields') {const goal = create.box.querySelector('textarea'); goal.value = 'Authored goal'; goal.dispatchEvent(new Event('input'));}
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
    await expect(page.locator('[data-earlier-drafts-slot]')).toHaveAttribute('data-scan-state', 'complete');
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
    expect(Object.entries(after.entries).filter(([key]) => key.includes('http%3A%2F%2F')).sort()).toEqual(before.filter(([key]) => key.includes('http%3A%2F%2F')));
    expect(Object.entries(after.entries).filter(([key]) => key.startsWith('batc.draft.managed:')).map(([, value]) => value)).toEqual(['Current draft']);
    expect(after.writes).toBe(0);
    expect(after.entries[current.key]).toBe(current.value);
    await context.close();
  });
}

for (const locale of ['en-US', 'zh-TW']) {
  for (const field of ['principal', 'server', 'actor', 'ambiguous', 'migrated', 'browser', 'external', 'unverified']) {
    test(`earlier draft recovery excludes ${field} with a positive control (${locale})`, async ({browser}) => {
      const context = await browser.newContext({locale}); const page = await context.newPage();
      await setup(page, {source: 'external'}); await writeForms(page, 'private');
      if (field === 'ambiguous') {
        await changeIdentity(page, {actor: bootstrap.capabilities.actor + '.send'}); await page.reload(); await writeForms(page, 'other actor');
      }
      const identity: Record<string, string> = {actor: bootstrap.capabilities.actor};
      if (['principal', 'server', 'actor'].includes(field)) identity[field] = 'different-identity';
      if (field === 'migrated') {
        await changeIdentity(page, {source: 'managed', endpoint: 'http://127.0.0.1:41003/'}); await page.reload();
        await expect(page.locator('textarea').first()).toHaveValue('Session draft private');
      }
      for (const [port, name] of [[41101, 'control one'], [41102, 'control two']] as const) {
        await changeIdentity(page, {...identity, source: 'external', endpoint: `http://127.0.0.1:${port}/`});
        await page.reload(); await writeForms(page, name);
      }
      await changeIdentity(page, {...identity, source: 'managed', endpoint: 'http://127.0.0.1:41003/'}); await page.reload();
      await page.evaluate(() => {location.hash = '#/settings?section=about&drafts=earlier';});
      const slot = page.locator('[data-earlier-drafts-slot]');
      await expect(slot).toHaveAttribute('data-scan-state', 'complete');
      await expect(slot.locator('textarea').filter({visible: true})).not.toHaveCount(0);
      const text = await slot.locator('textarea').evaluateAll(nodes => nodes.map(node => (node as HTMLTextAreaElement).value));
      expect(text).toContain('Session draft control one'); expect(text).toContain('Session draft control two');
      if (!['browser', 'external', 'unverified'].includes(field)) {
        expect(text).not.toContain('Session draft private'); expect(text).not.toContain('Session draft other actor');
        await expect(slot.locator('textarea')).toHaveCount(8);
      } else {
        const scopes = await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('batc.sync.http')).map(key => key.slice('batc.sync.'.length)));
        await page.addInitScript(scopes => {
          const getItem = Storage.prototype.getItem; (window as any).__legacyReads = 0;
          Storage.prototype.getItem = function(key: string) {if (scopes.some(scope => key.includes(scope))) (window as any).__legacyReads++; return getItem.call(this, key);};
        }, scopes);
        let browserPosts = 0;
        if (field === 'browser') {
          await page.addInitScript(() => {
            delete (window as any).isTauri; delete (window as any).__TAURI_INTERNALS__;
            localStorage.setItem('batc.dashboard.token', 'fixture-browser-token');
          });
          await page.route('**/api/v1/**', async route => {
            if (route.request().method() === 'POST') browserPosts++;
            const path = new URL(route.request().url()).pathname.slice('/api/v1'.length);
            const data = path === '/bootstrap' ? bootstrap : path === '/capabilities' ? bootstrap.capabilities : path === '/events'
              ? {events: [], next_cursor: 0, head_cursor: 0, has_more: false, sync: bootstrap.sync} : bootstrap.snapshot[path.slice(1).replaceAll('-', '_')];
            await route.fulfill({status: data ? 200 : 404, json: data || {error: {code: 'NOT_FOUND', message: 'Fixture read model unavailable'}}});
          });
        }
        if (field === 'external') await changeIdentity(page, {source: 'external'});
        if (field === 'unverified') await page.addInitScript(() => {
          const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
          bridge.invoke = async (command: string, args: any) => {
            if (command === 'connector_request' && args.input.path === '/bootstrap') {
              (window as any).__bootstrapWaiting = true;
              await new Promise(resolve => {(window as any).__releaseBootstrap = resolve;});
            }
            return invoke(command, args);
          };
        });
        await page.reload();
        await expect(slot).toBeAttached();
        await expect(slot).toHaveAttribute('data-scan-state', field === 'unverified' ? 'waiting' : 'skipped');
        if (field === 'unverified') await expect.poll(() => page.evaluate(() => (window as any).__bootstrapWaiting)).toBe(true);
        await expect(page.locator('[data-earlier-drafts], [data-earlier-drafts-notice]')).toHaveCount(0);
        expect(await page.evaluate(() => (window as any).__legacyReads)).toBe(0); expect(browserPosts).toBe(0);
        if (field === 'unverified') {
          await page.evaluate(() => (window as any).__releaseBootstrap());
          await expect(slot).toHaveAttribute('data-scan-state', 'complete');
          await expect(slot.locator('[data-earlier-drafts]')).toBeAttached();
        }
      }
      expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
      await context.close();
    });
  }
}


for (const locale of ['en-US', 'zh-TW']) {
  for (const width of [390, 1440]) test(`retained multi-port requests are separate from ordinary drafts (${locale}, ${width})`, async ({browser}, testInfo) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}}); const page = await context.newPage();
    await setup(page, {legacy: true, legacyCursors: [1, 2]});
    const before = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.includes('http%3A%2F%2F')).sort());
    await page.evaluate(() => {location.hash = '#/settings?section=about&drafts=earlier';});
    const slot = page.locator('[data-earlier-drafts-slot]'); await expect(slot.locator('[data-earlier-draft]')).toHaveCount(4);
    const pending = slot.locator('[data-earlier-group="possibly-sent"]');
    await expect(pending.locator('textarea')).toHaveCount(4);
    await expect(pending).toContainText(locale === 'zh-TW' ? '可能已經送出 — 再次送出前請先確認' : 'May already have been sent — check it before sending it again');
    await expect(pending.locator('a[href="#/op/op_' + 'a'.repeat(32) + '"]')).toHaveCount(2);
    await expect(pending.locator('a[href="#/op/op_' + 'b'.repeat(32) + '"]')).toHaveCount(2);
    await expect(slot.locator('[data-earlier-group="draft"] textarea')).toHaveCount(0);
    await expect(pending.getByRole('button', {name: labels(locale).copy, exact: true})).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await pending.screenshot({path: testInfo.outputPath('possibly-sent-requests.png')});
    expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.includes('http%3A%2F%2F')).sort())).toEqual(before);
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await context.close();
  });

  test(`notice survives until opened, follows language and stays in main layout (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale}); const page = await context.newPage();
    await setup(page, {legacy: true, legacyCursors: [1, 2]});
    await expect(page.locator('#main [data-earlier-drafts-notice]')).toBeVisible();
    expect(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.earlier-drafts.') && key.endsWith('.noticed')))).toBe(false);
    await page.reload(); await expect(page.locator('[data-earlier-drafts-notice]')).toBeVisible();
    await page.evaluate(() => {location.hash = '#/settings?section=about';});
    await expect(page.locator('[data-earlier-drafts-slot]')).toHaveAttribute('data-scan-state', 'complete');
    const next = locale === 'zh-TW' ? 'en-US' : 'zh-TW';
    await page.locator('[data-language-switch] button').nth(next === 'zh-TW' ? 1 : 0).click();
    await expect(page.locator('[data-earlier-drafts-notice]')).toContainText(labels(next).notice);
    await page.locator('[data-earlier-drafts] summary').click();
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.earlier-drafts.') && key.endsWith('.noticed')))).toBe(true);
    await expect(page.locator('[data-earlier-drafts-notice]')).toHaveCount(0);
    await context.close();
  });

  test(`cancelled configuration change retains recovery and last hide keeps focus (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale}); const page = await context.newPage();
    await setup(page, {legacy: true, legacyCursors: [1, 2]});
    await page.evaluate(() => {
      const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
      bridge.invoke = async (command: string, args: any) => command === 'connector_setup_configuration' ? null
        : command === 'native_status' ? {...await invoke(command, args), configuration_setup: true} : invoke(command, args);
    });
    await page.locator('#live').click();
    const join = page.locator('[data-managed-join]'); await join.locator('summary').click();
    await join.locator('input').nth(0).fill('http://127.0.0.1:42001/'); await join.locator('input').nth(1).fill('other-actor');
    await join.getByRole('button', {name: locale === 'zh-TW' ? '檢視連線設定' : 'Review connection settings', exact: true}).click();
    await page.evaluate(() => {location.hash = '#/settings?section=about&drafts=earlier';});
    await expect(page.locator('[data-earlier-draft]')).toHaveCount(4);
    for (let remaining = 4; remaining > 0; remaining--) {
      await page.locator('[data-earlier-draft]').first().getByRole('button', {name: labels(locale).hide, exact: true}).click();
      await expect(page.locator('[data-earlier-draft]')).toHaveCount(remaining - 1);
    }
    expect(await page.evaluate(() => document.activeElement !== document.body && document.activeElement?.isConnected)).toBe(true);
    await expect(page.locator('[data-earlier-drafts]')).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await context.close();
  });

  test(`resnapshot preserves the open panel, reading position and focused control (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale}); const page = await context.newPage();
    await setup(page, {legacy: true, legacyCursors: [1, 2]});
    await page.evaluate(() => {location.hash = '#/settings?section=about&drafts=earlier';});
    await expect(page.locator('[data-earlier-draft]')).toHaveCount(4);
    await page.locator('[data-earlier-draft] button').first().focus();
    await page.evaluate(() => {
      (window as any).__panel = document.querySelector('[data-earlier-drafts]');
      (window as any).__focused = document.activeElement;
      (window as any).__storage.reset = true;
    });
    await expect.poll(() => page.evaluate(() => (window as any).__storage.bootstraps)).toBeGreaterThan(1);
    expect(await page.evaluate(() => (window as any).__panel === document.querySelector('[data-earlier-drafts]') && (window as any).__focused === document.activeElement)).toBe(true);
    await expect(page.locator('[data-earlier-drafts]')).toHaveAttribute('open', '');
    await context.close();
  });
}

for (const locale of ['en-US', 'zh-TW']) {
  test(`changing connection while recovery scans discards the old result (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale}); const page = await context.newPage();
    await setup(page, {legacy: true, legacyCursors: [1, 2]});
    await expect(page.locator('[data-earlier-drafts-notice]')).toBeVisible();
    await page.addInitScript(() => {
      const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
      bridge.invoke = async (command: string, args: any) => command === 'native_status'
        ? {...await invoke(command, args), configuration_setup: (window as any).__storage.source === 'managed'} : invoke(command, args);
      const digest = crypto.subtle.digest.bind(crypto.subtle); let held = false;
      crypto.subtle.digest = async (algorithm: any, bytes: any) => {
        if (!held && new TextDecoder().decode(bytes).startsWith('["batc.')) {
          held = true; (window as any).__scanHeld = true;
          await new Promise(resolve => {(window as any).__releaseScan = resolve;});
        }
        const value = await digest(algorithm, bytes);
        if (held) (window as any).__digestsReleased = ((window as any).__digestsReleased || 0) + 1;
        return value;
      };
    });
    await page.reload(); await expect.poll(() => page.evaluate(() => (window as any).__scanHeld)).toBe(true);
    // View readiness does not wait for the paused optional scan.
    await expect(page.locator('textarea').first()).toBeVisible();
    await page.locator('#live').click();
    const join = page.locator('[data-managed-join]'); await join.locator('summary').click();
    await join.locator('input').nth(0).fill('http://127.0.0.1:42001/'); await join.locator('input').nth(1).fill('other-actor');
    await changeIdentity(page, {principal: 'different-principal', endpoint: 'http://127.0.0.1:42001/', source: 'external'});
    await join.getByRole('button', {name: locale === 'zh-TW' ? '檢視連線設定' : 'Review connection settings', exact: true}).click();
    await page.getByRole('button', {name: locale === 'zh-TW' ? '連線' : 'Connect', exact: true}).click();
    await page.evaluate(() => {location.hash = '#/settings?section=about';});
    await expect(page.locator('[data-earlier-drafts-slot]')).toHaveAttribute('data-scan-state', 'skipped');
    await page.evaluate(() => (window as any).__releaseScan());
    await expect.poll(() => page.evaluate(() => (window as any).__digestsReleased)).toBe(4);
    await expect(page.locator('[data-earlier-drafts], [data-earlier-drafts-notice]')).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await context.close();
  });
}

for (const locale of ['en-US', 'zh-TW']) test(`authored markup stays literal and fields have distinct labels (${locale})`, async ({browser}) => {
  const context = await browser.newContext({locale}); const page = await context.newPage();
  await setup(page, {source: 'external'}); await writeForms(page, 'fields');
  await page.locator('textarea').first().fill('<img src=x onerror="window.draftExecuted=true">');
  await changeIdentity(page, {endpoint: 'http://127.0.0.1:41002/'}); await page.reload(); await writeForms(page, 'two');
  await changeIdentity(page, {source: 'managed', endpoint: 'http://127.0.0.1:41003/'}); await page.reload();
  await page.evaluate(() => {location.hash = '#/settings?section=about&drafts=earlier';});
  await expect(page.locator('[data-earlier-drafts-slot]')).toHaveAttribute('data-scan-state', 'complete');
  await expect(page.locator('[data-earlier-draft]')).toHaveCount(9);
  const panel = page.locator('[data-earlier-drafts]');
  const names = await panel.locator('textarea').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')));
  expect(names.every(name => name && name.includes(' · '))).toBe(true);
  expect(new Set(names).size).toBe(5);
  await expect(panel.locator('img, script')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).draftExecuted)).toBeUndefined();
  expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
  await context.close();
});
