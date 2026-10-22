import {test, expect, type Page} from '@playwright/test';
import {STRINGS} from '../src/i18n.js';
import {publishedFixture, dispatchProject, selected} from './repository-start-fixture';
import {conversationFixture, mountConversation} from './conversation-fixture';
import {serveGateBaseline} from './action-gate-baseline';

async function imageEvent(page: Page, kind: 'paste' | 'drop') {
  await page.locator('[data-published-start] textarea').first().evaluate((el, kind) => {
    const data = new DataTransfer();
    const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='), c => c.charCodeAt(0));
    data.items.add(new File([bytes], 'gate.png', {type: 'image/png'}));
    el.dispatchEvent(kind === 'paste'
      ? new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true})
      : new DragEvent('drop', {dataTransfer: data, bubbles: true, cancelable: true}));
  }, kind);
}

for (const locale of ['en-US', 'zh-TW']) {
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  for (const reason of ['manage_required', 'attachments_unavailable', 'uploads_not_allowed']) {
    for (const kind of reason === 'uploads_not_allowed' ? ['paste'] as const : ['paste', 'drop'] as const) {
      test(`real ${kind} explains ${reason === 'uploads_not_allowed' ? 'native paste refusal even without upload permission' : reason} while Connector is Connected ${locale}`, async ({browser}) => {
        const context = await browser.newContext({locale}), page = await context.newPage();
        await serveGateBaseline(page);
        const native = reason === 'uploads_not_allowed';
        const state = await publishedFixture(page, native, {dispatch: true, bindings: [selected],
          scopes: reason === 'manage_required' ? ['observe', 'start'] : ['observe', 'start', 'manage'],
          extendCaps: (caps: any) => {
            if (reason === 'attachments_unavailable') {delete caps.artifacts; caps.features.project_dispatch.artifacts = false;}
            if (reason === 'uploads_not_allowed') caps.actions = caps.actions.map((a: any) => a.action === 'artifact.upload' ? {...a, allowed: false} : a);
            return caps;
          }});
        try {
          await page.goto('/dashboard/#/dispatch/' + dispatchProject);
          const form = page.locator('[data-published-start]');
          await expect(form).toBeVisible();
          await form.locator('details').evaluateAll(nodes => nodes.forEach(el => { (el as HTMLDetailsElement).open = true; }));
          await expect(page.locator('#live')).toHaveAccessibleName(copy.ui_connector_status.replace('{status}', copy.ui_connected));
          await imageEvent(page, kind);
          const message = native ? locale === 'zh-TW'
            ? '桌面版不支援貼上圖片。請使用「選擇檔案」，或先「啟用拖放」再拖入檔案。'
            : 'Image paste is not supported in the desktop app. Use Choose files, or Enable file drop and drop the file.'
            : copy[`error_code_CLIENT_ACTION_${reason.toUpperCase()}`];
          await expect(form.getByText(message, {exact: true})).toBeVisible();
          await expect(form).not.toContainText(copy.offline_actions_paused);
          expect(state.posts).toHaveLength(0);
          expect(state.uploaded).toBe(false);
          expect(state.errors).toEqual([]);
        } finally { await context.close(); }
      });
    }
  }

  for (const reason of ['view_not_ready', 'service_error', 'network_error']) test(`operation POST distinguishes ${reason} ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    await serveGateBaseline(page);
    const fixture = conversationFixture(), id = 'wi_' + 'd'.repeat(20);
    let block = false, posts = 0, release!: () => void;
    const pending = new Promise<void>(resolve => {release = resolve;});
    const caps = {actor: 'reader', scopes: ['observe', 'manage'], api_version: 1, contract_version: '2026-10-08',
      hosts: [], actions: [], features: {}, artifacts: {limits: {max_file_bytes: 1048576}}};
    await mountConversation(page, false, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (input.method === 'POST') {posts++; throw new Error('Blocked POST reached transport');}
      if (path === '/events' && block) {
        if (reason === 'view_not_ready') await pending;
        else return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Events unavailable'}}};
      }
      const result = await fixture.dispatch(input);
      if (path === '/capabilities') return {status: 200, data: caps};
      if (path === '/bootstrap') result.data.capabilities = caps;
      if (path === '/work-items/' + id) return {status: 200, data: {
        work_item: {work_item_id: id, title: 'Gate fixture', version: 1, state: 'todo', goal: '', request: '', acceptance: '', steps: [], attachments: [],
          completion: {display_state: 'todo', fingerprint: 'fixture-fingerprint'}},
        project: {project_id: 'prj_fixture', name: 'Fixture'}, links: [], path: [], children: [], derived: [], events: []}};
      if (path === '/artifacts') return {status: 200, data: {artifacts: [], next_cursor: null}};
      return result;
    });
    try {
      await page.goto('/dashboard/#/item/' + id);
      await page.getByRole('button', {name: locale === 'zh-TW' ? '更多' : 'More', exact: true}).click();
      await page.locator('input[maxlength="120"]').fill('Changed draft');
      await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
      if (reason === 'network_error') await page.route('**/api/v1/**', route => {
        if (route.request().method() === 'POST') {posts++; return route.abort('connectionfailed');}
        return route.fallback();
      });
      else {
        block = true;
        if (reason === 'view_not_ready') await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})));
        else await expect(page.locator('#live')).toHaveAttribute('data-connection', 'reconnecting');
      }
      await page.getByRole('button', {name: copy.save, exact: true}).click();
      const message = reason === 'network_error' ? copy.error_code_NETWORK_ERROR : copy[`error_code_CLIENT_ACTION_${reason.toUpperCase()}`];
      await expect(page.getByText(message, {exact: true})).toBeVisible();
      expect(posts).toBe(reason === 'network_error' ? 1 : 0);
      if (reason === 'view_not_ready') {
        await expect(page.locator('#live')).toHaveAccessibleName(copy.ui_connector_status.replace('{status}', copy.ui_connected));
        await expect(page.locator('#main')).not.toContainText(copy.offline_actions_paused);
      }
    } finally {release(); await context.close();}
  });

  test(`waiting_external upload rechecks view readiness before bytes ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    await serveGateBaseline(page);
    const state = await publishedFixture(page, false, {dispatch: true, bindings: [selected]});
    let started = false, holdEvents = false, releaseUpload!: () => void, releaseEvents!: () => void;
    const upload = new Promise<void>(resolve => {releaseUpload = resolve;});
    const events = new Promise<void>(resolve => {releaseEvents = resolve;});
    await page.route('**/api/v1/**', async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      if (path.endsWith('/events') && holdEvents) await events;
      if (req.method() === 'POST' && req.postDataJSON()?.action === 'artifact.upload') {started = true; await upload;}
      await route.fallback();
    });
    try {
      await page.goto('/dashboard/#/dispatch/' + dispatchProject);
      const form = page.locator('[data-published-start]');
      await expect(form).toBeVisible();
      await form.locator('details').evaluateAll(nodes => nodes.forEach(el => {(el as HTMLDetailsElement).open = true;}));
      await expect(page.locator('#live')).toHaveAttribute('data-connection', 'connected');
      await imageEvent(page, 'paste');
      await expect.poll(() => started).toBe(true);
      holdEvents = true;
      await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})));
      releaseUpload();
      await expect(form.getByText(copy.error_code_CLIENT_ACTION_VIEW_NOT_READY, {exact: true})).toBeVisible();
      expect(state.posts).toHaveLength(1);
      expect(state.posts[0].body.action).toBe('artifact.upload');
      expect(state.uploaded).toBe(false);
      expect(state.bytes).toHaveLength(0);
    } finally {releaseUpload(); releaseEvents(); await context.close();}
  });

  for (const reason of ['not_connected', 'connecting', 'reconnecting', 'connection_changing', 'view_not_ready',
    'manage_required', 'attachments_unavailable', 'uploads_not_allowed']) {
    test(`reason presentation component ${reason} ${locale}`, async ({browser}) => {
      const context = await browser.newContext({locale}), page = await context.newPage();
      await serveGateBaseline(page);
      try {
        await page.goto('/ui-components.html');
        await page.evaluate(async reason => {
          const {clientActionGate} = await import('/src/central-service-status.js');
          const {errorPresentation, clientActionError, sessionErrorLine} = await import('/src/error-presentation.js');
          const {t} = await import('/src/i18n.js');
          const {h} = await import('/src/ui/dom.js');
          const facts = {token: 'credential', online: true, viewReady: true, nativeBusy: false, pollHealth: 'connected',
            manageAllowed: true, attachmentsSupported: true, uploadsAllowed: true};
          const requirements = {post: true, attachment: true, manage: true, supported: true, uploads: true};
          if (reason === 'not_connected') {facts.token = ''; facts.online = false; facts.viewReady = false;}
          if (reason === 'connecting') {facts.online = false; facts.viewReady = false; facts.pollHealth = 'initial';}
          if (reason === 'reconnecting') {facts.online = false; facts.pollHealth = 'reconnecting';}
          if (reason === 'connection_changing') {facts.nativeBusy = true; facts.online = false;}
          if (reason === 'view_not_ready') facts.viewReady = false;
          if (reason === 'manage_required') facts.manageAllowed = false;
          if (reason === 'attachments_unavailable') facts.attachmentsSupported = false;
          if (reason === 'uploads_not_allowed') facts.uploadsAllowed = false;
          const gate = clientActionGate(facts, requirements);
          const presented = errorPresentation(clientActionError(gate.reason, t), t);
          document.body.replaceChildren(h('p', {role: 'alert'}, presented.summary),
            h('p', {'data-session-error-line': ''}, sessionErrorLine(clientActionError(gate.reason, t), t)));
        }, reason);
        await expect(page.getByRole('alert')).toHaveText(copy[`error_code_CLIENT_ACTION_${reason.toUpperCase()}`]);
        await expect(page.locator('[data-session-error-line]')).toHaveText(copy[`error_code_CLIENT_ACTION_${reason.toUpperCase()}`]);
        if (reason !== 'reconnecting') await expect(page.getByRole('alert')).not.toContainText(/offline|cannot reach|離線|無法連線/i);
      } finally {await context.close();}
    });
  }
}
