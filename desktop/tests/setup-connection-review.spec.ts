import {managedStatusFixture} from './managed-status-fixture.ts';
import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {discoverSetupProfiles, reviewSetupConnection} from './managed-setup-fixture.ts';
import {attentionFixture} from './attention-fixture.ts';

const discoveryGolden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-discovery.json', import.meta.url), 'utf8'));
const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-preview.json', import.meta.url), 'utf8'));
// Local until the event-delivery helper handoff is merged. Hold delivery, not clocks.
async function reviewFixture(page: Page, native: boolean, locale: string) {
  const base = attentionFixture();
  let releaseEvents!: () => void, releasePreview!: () => void;
  const events = new Promise<void>(resolve => {releaseEvents = resolve;});
  const state = {previews: [] as any[], posts: [] as any[], staged: [] as any[], lost: false, mismatch: false,
    holdPreview: false, primeEvents: false, eventReads: 0, version: 1, saved: false, revision: 'a'.repeat(64), operation: null as any,
    releaseEvents, releasePreview: () => releasePreview?.()};
  const caps = () => ({...base.caps(), scopes: ['observe', 'manage'],
    managed_installation: {runtime_version: '0.2.4', background: true}, hosts: state.saved ? [{host: 'bat'}] : [],
    actions: ['host', 'repository', 'verification'].map(kind => ({action: `setup.${kind}`, allowed: true}))});
  const dispatch = async (input: any): Promise<any> => {
    const path = new URL(input.path, 'http://example.invalid').pathname;
    if (path === '/capabilities') return {status: 200, data: caps()};
    if (path === '/bootstrap') return {status: 200, data: {capabilities: caps(), sync: {version: 1,
      server_id: 'server-a', principal_id: 'person-a', checkpoint: {cursor: 0, token: 'checkpoint-a'}}}};
    if (path === '/events') {state.eventReads++; if (!state.primeEvents || state.eventReads > 1) await events; return {status: 200, data: {events: [], next_cursor: 0,
      has_more: false, head_cursor: 0, sync: {checkpoint: {cursor: 0, token: 'checkpoint-a'}}}};}
    if (path === '/managed/setup') return {status: 200, data: {preview_contract_version: state.version, discovery_contract_version:1,
      revision: state.revision, hosts: state.saved ? [{name: 'bat', url: 'wss://host-a.example.invalid:9876/', connection_status: 'checking'}] : [],
      repositories: [], profiles: [{id: 'profile-demo', name: 'host-a', url: 'wss://host-a.example.invalid:9876/',
        fingerprint: 'A'.repeat(64), profile_id: 'default', token_available: true}], profiles_error: null,
      verification: {commands: {}, timeout_s: 600}, busy: false}};
    if (path === '/managed/setup/discover') return {status:200, data:structuredClone(discoveryGolden[input.body.import_profile_id ? 'imported' : 'default'].response)};
    if (path === '/managed/setup/preview') {
      state.previews.push(input);
      if (state.holdPreview) await new Promise<void>(resolve => {releasePreview = resolve;});
      return {status: 200, data: {preview: structuredClone(golden[input.body.params.secret_ref ? 'staged' : 'profile'].preview)}};
    }
    if (path === '/managed/setup/secrets') {state.staged.push(input); return {status: 200, data: {kind: 'bat', secret_ref: 'setup_' + 'b'.repeat(32)}};}
    if (path.startsWith('/operations/')) return {status: 200, data: {operation: state.operation}};
    if (path === '/operations' && input.method === 'POST') {
      state.posts.push(input);
      state.operation ||= {...input.body, actor: caps().actor, operation_id: 'op_' + 'c'.repeat(32),
        idempotency_key: input.idempotency_key, status: 'succeeded', updated_at: 1800000000,
        result: {configured: true, revision: 'b'.repeat(64)}};
      state.saved = true; state.revision = 'b'.repeat(64);
      if (state.lost && state.posts.length === 1) return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Synthetic reply loss'}}};
      return {status: 200, data: {operation: state.mismatch ? {...state.operation, actor: 'other-person'} : state.operation}};
    }
    return base.dispatch(input);
  };
  await page.addInitScript(locale => localStorage.setItem('batc.lang', locale), locale);
  if (native) {
    await page.exposeFunction('reviewConnector', dispatch);
    await page.exposeFunction('reviewManagedStatus', () => managedStatusFixture());
    await page.addInitScript(c => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {
      invoke: async (command: string, args: any) => {
        if (command === 'native_status') return {endpoint: 'http://127.0.0.1:9870', credential_available: true};
        if (command === 'connector_connect') return c;
        if (command === 'connector_request') return (window as any).reviewConnector(args.input);
        if (command === 'managed_control') return (window as any).reviewManagedStatus();
        if (command === 'connector_disconnect') return null;
        throw Error('Unavailable native fixture command');
      }}}), caps());
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw Error('Native uses IPC');
    const req = route.request(), url = new URL(req.url());
    const result = await dispatch({method: req.method(), path: url.pathname.slice(7) + url.search,
      body: req.postData() ? req.postDataJSON() : null, idempotency_key: req.headers()['idempotency-key']});
    await route.fulfill({status: result.status, json: result.data});
  });
  return state;
}

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test.describe(`${locale} ${native ? 'native' : 'web'} connection review`, () => {
  test.use({locale});
  const review = locale === 'en' ? 'Review connection' : '預覽連線';
  const apply = locale === 'en' ? 'Apply connection' : '套用連線';
  test('review and cancel create no intent; Apply recovers the original lost reply', async ({page}) => {
    const state = await reviewFixture(page, native, locale); state.lost = true;
    try {
      await page.goto('/dashboard/');
      await reviewSetupConnection(page,locale);
      await expect(page.locator('[data-setup-review]')).toBeVisible();
      expect(state.previews).toHaveLength(1); expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
      await page.getByRole('button', {name: locale === 'en' ? 'Cancel' : '取消', exact: true}).click();
      expect(state.posts).toEqual([]);
      await reviewSetupConnection(page,locale);
      await page.getByRole('button', {name: apply, exact: true}).click();
      await expect(page.locator('[data-error-summary]')).toBeVisible();
      expect(state.posts).toHaveLength(1);
      expect(state.posts[0].body.preconditions.review_digest).toBe(golden.profile.preview.review_digest);
      await page.goto('/dashboard/#/settings');
      await page.getByRole('button', {name: locale === 'en' ? 'Check request' : '查回請求', exact: true}).click();
      await expect(page.locator('[data-setup-sessions]')).toBeVisible();
      expect(state.posts).toHaveLength(2); expect(state.posts[1]).toEqual(state.posts[0]);
      expect(await page.locator('[data-setup-progress]').innerText()).not.toContain(locale === 'en' ? 'You’re ready' : '可以開始使用');
      expect(await page.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}))).not.toContain('synthetic-bat-secret');
    } finally {state.releasePreview(); state.releaseEvents();}
  });
  test('form changes discard a late preview and never submit it', async ({page}) => {
    const state = await reviewFixture(page, native, locale); state.holdPreview = true;
    try {
      await page.goto('/dashboard/');
      await reviewSetupConnection(page,locale);
      await expect.poll(() => state.previews.length).toBe(1);
      await page.locator('[data-setup-advanced] > summary').click();
      await page.locator('[data-setup-field=host]').fill('host-b');
      state.releasePreview();
      await expect(page.getByRole('button', {name: apply, exact: true})).toBeHidden();
      await expect(page.locator('[data-setup-field=host]')).toHaveValue('host-b');
      expect(state.posts).toEqual([]);
    } finally {state.releasePreview(); state.releaseEvents();}
  });
  test('unsupported preview never silently falls back to a legacy mutation', async ({page}) => {
    const state = await reviewFixture(page, native, locale); state.version = 0;
    try {
      await page.goto('/dashboard/');
      await expect(page.locator('[data-managed-setup]')).toContainText(locale === 'en'
        ? 'This Connector does not support connection review. Update it before continuing.' : '此 Connector 尚不支援連線預覽，請先更新再繼續。');
      expect(state.posts).toEqual([]); expect(state.previews).toEqual([]);
    } finally {state.releaseEvents();}
  });
  test('a mismatched receipt retains the original key and refuses another Apply', async ({page}) => {
    const state = await reviewFixture(page, native, locale); state.mismatch = true;
    try {
      await page.goto('/dashboard/');
      await reviewSetupConnection(page,locale);
      await page.getByRole('button', {name: apply, exact: true}).click();
      await expect(page.locator('[data-error-summary]')).toContainText(locale === 'en'
        ? 'The receipt does not match the original configuration request.' : '回執與原設定請求不一致。');
      await expect(page.getByRole('button', {name: apply, exact: true})).toBeHidden();
      expect(state.posts).toHaveLength(1);
      state.mismatch = false;
      await page.getByRole('button', {name: locale === 'en' ? 'Check request' : '查回請求', exact: true}).click();
      await expect(page.locator('[data-setup-sessions]')).toBeVisible();
      expect(state.posts).toHaveLength(2); expect(state.posts[1]).toEqual(state.posts[0]);
    } finally {state.releaseEvents();}
  });
  test('held foreground refresh refuses the first click and explicit retry succeeds', async ({page}) => {
    const state = await reviewFixture(page, native, locale);
    state.primeEvents = true;
    try {
      await page.goto('/dashboard/');
      await expect.poll(() => state.eventReads).toBe(2);
      await discoverSetupProfiles(page,locale);
      await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true})));
      await expect(page.getByRole('button', {name: review, exact: true}).first()).toBeEnabled();
      await reviewSetupConnection(page,locale);
      await page.locator('details:has(> [data-error-details]) > summary').click();
      await expect(page.locator('[data-error-details] code')).toHaveText('CLIENT_ACTION_VIEW_NOT_READY');
      expect(state.previews).toEqual([]); expect(state.posts).toEqual([]);
      expect(await page.evaluate(() => JSON.parse(localStorage.getItem(Object.keys(localStorage).find(key => key.startsWith('batc.managed.setup.'))!)!).host)).toBe('bat');
      state.releaseEvents();
      await expect.poll(() => state.eventReads).toBeGreaterThan(2);
      await reviewSetupConnection(page,locale);
      await expect(page.getByRole('button', {name: apply, exact: true})).toBeVisible();
      expect(state.previews).toHaveLength(1); expect(state.posts).toEqual([]);
    } finally {state.releaseEvents();}
  });
  test('pasted credential staging is explicit; Cancel and reload never apply it', async ({page}) => {
    const state = await reviewFixture(page, native, locale);
    try {
      await page.goto('/dashboard/');
      await page.locator('[data-setup-advanced] > summary').click();
      await page.locator('[data-setup-field=connection_url]').fill(`wss://host-a.example.invalid:9876?token=synthetic-bat-secret&fp=${'A'.repeat(64)}`);
      await reviewSetupConnection(page,locale);
      await expect(page.locator('[data-setup-review]')).toContainText(locale === 'en' ? 'already staged' : '已先存入');
      expect(state.staged).toHaveLength(1); expect(state.posts).toEqual([]);
      expect(await page.locator('[data-managed-setup]').innerText()).not.toMatch(/synthetic-bat-secret|token=/);
      expect(await page.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}))).not.toMatch(/synthetic-bat-secret|token=/);
      await page.getByRole('button', {name: locale === 'en' ? 'Cancel' : '取消', exact: true}).click();
      await page.reload();
      await expect(page.getByRole('button', {name: apply, exact: true})).toBeHidden();
      expect(state.posts).toEqual([]); expect(state.staged).toHaveLength(1);
    } finally {state.releaseEvents();}
  });
  test('fresh configuration and source reads invalidate the reviewed candidate', async ({page}) => {
    const state = await reviewFixture(page, native, locale);
    try {
      await page.goto('/dashboard/');
      await reviewSetupConnection(page,locale);
      await expect(page.getByRole('button', {name: apply, exact: true})).toBeVisible();
      state.revision = 'd'.repeat(64);
      await page.locator('#main > .ui-page-header').getByRole('button', {name: locale === 'en' ? 'Refresh' : '重新整理', exact: true}).click();
      await expect(page.getByRole('button', {name: apply, exact: true})).toBeHidden();
      await expect(page.locator('[data-setup-progress]')).toHaveText(locale === 'en' ? 'Settings changed; review again' : '設定已變更，請重新預覽');
      expect(state.posts).toEqual([]);
    } finally {state.releaseEvents();}
  });
});
