import {expect, type Page} from '@playwright/test';
export const startOperationId = 'op_'+'a'.repeat(32);
export async function startFixture(page: Page, native: boolean, options: any = {}) {
  const state = {actor: 'start-person', server: 'start-server', principal: 'start-principal', scopes: ['observe', 'start'],
    allowed: true, writes: true, orchestrate: true, status: 'succeeded', posts: [] as any[], reads: [] as string[],
    errors: [] as string[], events: [] as any[], after: 0, operation: null as any, ...options};
  const caps = () => ({actor: state.actor, scopes: state.scopes, api_version: 1, contract_version: '2026-10-08',
    hosts: ['demo', 'other'].map(host => ({host, writes: state.writes, orchestrate: state.orchestrate})),
    actions: state.absent ? [] : [{action: 'session.start', allowed: state.allowed}], features: {checkpoints: []}});
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method === 'POST') {
      state.posts.push(structuredClone(input));
      if (state.refuse) return {status: state.refuse === 'TIER_DISABLED' ? 403 : 409, data: {error: {code: state.refuse, message: 'Fixture refusal'}}};
      if (!state.operation || state.operation.idempotency_key !== input.idempotency_key) state.operation = {
        operation_id: startOperationId, actor: state.actor, idempotency_key: input.idempotency_key, ...structuredClone(input.body), status: state.status,
        steps: [{name: 'session.start', status: 'succeeded'}, ...(input.body.params.prompt ? [{name: 'send', status: state.status === 'uncertain' ? 'uncertain' : 'succeeded'}] : [])],
        external_refs: {session_id: 'managed-created', start_result: {host: input.body.target.host, session_id: 'managed-created', started: true}},
        result: state.status === 'succeeded' ? {host: input.body.target.host, session_id: 'managed-created', started: true, prompt_sent: Boolean(input.body.params.prompt)} : null};
      const op = structuredClone(state.operation);
      if (state.bad === 'key') op.idempotency_key = 'wrong';
      if (state.bad === 'actor') op.actor = 'wrong';
      if (state.bad === 'host') op.target.host = 'wrong';
      if (state.bad === 'prompt') op.params.prompt = 'changed';
      if (state.bad === 'preconditions') op.preconditions = {unexpected: true};
      if (state.bad === 'result') op.external_refs.start_result.session_id = 'wrong';
      if (state.delayPost) await new Promise(resolve => {state.holdPost = resolve;});
      if (state.lost) {state.lost = false; return {status: 503, data: {error: {code: 'LOST', message: 'Lost start reply'}}};}
      return {status: 200, data: {operation: op}};
    }
    state.reads.push(input.path);
    if (path === '/workspaces') {
      const host = url.searchParams.get('host');
      if (state.delayHost === host) await new Promise(resolve => {state.holdDiscovery = resolve;});
      return {status: 200, data: {workspaces: [{host: state.wrongHost ? 'wrong' : host, workspace_id: host+'-ws', name: 'Dashboard', folder: '/srv/dashboard'}],
        count: 1, has_more: Boolean(state.truncated), errors: state.discoveryError ? {[host!]: {code: 'OFFLINE'}} : {}}};
    }
    if (path === '/operations/'+startOperationId) {
      if (state.delayRead) await new Promise(resolve => {state.holdRead = resolve;});
      if (state.failRead) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Start read failed'}}};
      return {status: 200, data: {operation: {...state.operation, status: state.status}}};
    }
    if (path === '/events') state.after = Number(url.searchParams.get('after'));
    const events = state.events.filter((e: any) => e.seq > state.after), cursor = events.at(-1)?.seq || state.after;
    if (path === '/events' && events.length) state.delivered = true;
    return {status: 200, data: path === '/capabilities' ? caps() : path === '/bootstrap' ? {capabilities: caps(),
      sync: {version: 1, server_id: state.server, principal_id: state.principal, checkpoint: {cursor: 0, token: 'proof-0'}}}
      : path === '/events' ? {events, head_cursor: cursor, next_cursor: cursor, sync: {checkpoint: {cursor, token: 'proof-'+cursor}}}
      : {hosts: caps().hosts, sessions: [], operations: [], work_items: [], checkpoints: []}};
  };
  page.on('pageerror', e => state.errors.push(e.message));
  if (native) {
    await page.exposeFunction('startFixture', dispatch);
    await page.addInitScript(() => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: 'https://fixture.example', credential_available: true};
      if (command === 'connector_connect') return (await (window as any).startFixture({method: 'GET', path: '/capabilities'})).data;
      if (command === 'connector_disconnect') return;
      if (command === 'connector_request') return (window as any).startFixture(args.input);
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      throw new Error(command);
    }}}));
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Native start must use IPC');
    const request = route.request(), url = new URL(request.url());
    const response = await dispatch({method: request.method(), path: url.pathname.slice('/api/v1'.length)+url.search,
      body: request.postDataJSON(), idempotency_key: request.headers()['idempotency-key']});
    await route.fulfill({status: response.status, json: response.data});
  });
  return state;
}
export async function openStart(page: Page) {
  await page.goto('/dashboard/#/start'); const form = page.locator('[data-session-start]'); await expect(form).toBeVisible(); return form;
}
export async function chooseWorkspace(page: Page, host = 'demo') {
  const form = page.locator('[data-session-start]'); await form.getByRole('combobox', {name: 'Host', exact: true}).selectOption(host);
  await expect(form.getByRole('combobox', {name: 'Workspace', exact: true})).toBeEnabled();
  await form.getByRole('combobox', {name: 'Workspace', exact: true}).selectOption(host+'-ws'); return form;
}
