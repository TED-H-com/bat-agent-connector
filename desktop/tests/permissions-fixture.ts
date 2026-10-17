import {expect, type Page} from '@playwright/test';
export const sessionPath = '/sessions/demo/managed-session-1';
export const operationId = 'op_'+'a'.repeat(32);
export async function permissionFixture(page: Page, native: boolean, options: any = {}) {
  const state = {actor: 'permission-person', server: 'permission-server', principal: 'permission-principal',
    scopes: ['observe', 'operate'], action: true, writes: true, apiAccess: 'managed', provenance: 'connector_managed',
    status: 'succeeded', controlVersion: 7, streaming: false, failRead: false, failSession: false,
    posts: [] as any[], reads: [] as string[], events: [] as any[], errors: [] as string[], after: 0,
    lost: false, holdPost: null as any, delayPost: false, operation: null as any, ...options};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    const caps = {actor: state.actor, scopes: state.scopes, api_version: 1, contract_version: '2026-10-08',
      hosts: [{host: 'demo', writes: state.writes}], actions: state.action === null ? []
        : [{action: 'session.permissions', allowed: state.action}], features: {checkpoints: []}};
    if (state.delayLateSession) caps.actions.push({action: 'session.labels.set', allowed: false});
    const row = {host: 'demo', session_id: 'managed-session-1', title: 'Managed session', agent_kind: 'claude',
      api_access: state.apiAccess, provenance: state.provenance, streaming: state.streaming, control_version: state.controlVersion};
    if (input.method === 'POST') {
      state.posts.push(input);
      if (state.refuse) return {status: ['PERMISSIONS_HOST_POLICY', 'CONFINEMENT_RAISE_REFUSED'].includes(state.refuse) ? 403 : 409,
        data: {error: {code: state.refuse, message: 'Fixture refusal: '+state.refuse}}};
      const previous = state.operation;
      if (!previous || previous.key !== input.idempotency_key) state.operation = {key: input.idempotency_key,
        operation_id: operationId, idempotency_key: input.idempotency_key, action: 'session.permissions', target: input.body.target, params: input.body.params,
        status: state.status, error_code: state.status === 'failed' ? 'PERMISSIONS_STREAMING' : null,
        status_reason: state.status === 'failed' ? 'Claude is streaming. Wait for idle and submit a new operation.' : '',
        steps: [{name: 'permission_frame', status: state.status === 'uncertain' ? 'uncertain' : 'succeeded'}],
        external_refs: {permission_receipts: [{channel: 'session:set-permission-mode', status: state.status}]}};
      if (state.delayPost) await new Promise(resolve => {state.holdPost = resolve;});
      if (state.lost) {state.lost = false; return {status: 503, data: {error: {code: 'LOST', message: 'Lost permission reply'}}};}
      const operation = {...state.operation};
      if (state.badTarget) operation.target = {host: 'other', session_id: 'other-session'};
      if (state.badMode) operation.params = {mode: 'default'};
      if (state.badKey) operation.idempotency_key = 'another-identical-request';
      return {status: 200, data: {operation}};
    }
    state.reads.push(input.path);
    if (path === sessionPath && state.delayLateSession && state.reads.filter(path => path === sessionPath).length === 2)
      await new Promise(resolve => {state.releaseLateSession = resolve;});
    if (path === '/operations/'+operationId) {
      if (state.delayRead) {state.delayRead = false; await new Promise(resolve => {state.holdRead = resolve;});}
      if (state.failRead) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Permission read failed'}}};
      return {status: 200, data: {operation: {...state.operation, status: state.status}}};
    }
    if (path === sessionPath && state.failSession) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Session read failed'}}};
    if (path === '/events') state.after = Number(url.searchParams.get('after'));
    const events = state.events.filter((event: any) => event.seq > state.after), cursor = events.at(-1)?.seq || state.after;
    if (path === '/events' && events.length) state.delivered = true;
    return {status: 200, data: path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
      sync: {version: 1, server_id: state.server, principal_id: state.principal, checkpoint: {cursor: 0, token: 'proof-0'}}}
      : path === '/events' ? {events, head_cursor: cursor, next_cursor: cursor, sync: {checkpoint: {cursor, token: 'proof-'+cursor}}}
      : path === sessionPath ? {session: row, connector_metadata: {version: 0, labels: []}}
      : {messages: [], sessions: [], operations: [], hosts: [], work_items: [], checkpoints: []}};
  };
  page.on('pageerror', error => state.errors.push(error.message));
  if (native) {
    await page.exposeFunction('permissionFixture', dispatch);
    await page.addInitScript(() => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: 'https://fixture.example', credential_available: true};
      if (command === 'connector_connect') return (await (window as any).permissionFixture({method: 'GET', path: '/capabilities'})).data;
      if (command === 'connector_disconnect') return;
      if (command === 'connector_request') return (window as any).permissionFixture(args.input);
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      throw new Error(command);
    }}}));
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Native permissions cannot use browser HTTP');
    const request = route.request(), url = new URL(request.url());
    const response = await dispatch({method: request.method(), path: url.pathname.slice('/api/v1'.length)+url.search,
      body: request.postDataJSON(), idempotency_key: request.headers()['idempotency-key']});
    await route.fulfill({status: response.status, json: response.data});
  });
  return state;
}
export async function openPermissions(page: Page) {
  await page.goto('/dashboard/#/session/demo/managed-session-1');
  await expect(page.locator('[data-session-view]')).toBeVisible();
  const opener = page.getByRole('button', {name: /^(Results|成果)$/, exact: true});
  if (await opener.isVisible()) await opener.click();
  const section = page.locator('[data-permissions]');
  await expect(section).toBeVisible();
  await section.locator('summary').click();
  return section;
}
