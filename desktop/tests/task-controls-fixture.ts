import {type Page} from '@playwright/test';
export const taskId = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
export const opId = 'op_' + 'd'.repeat(32);
export async function taskFixture(page: Page, native = false, options: any = {}) {
  const state = {actor: 'task-person', server: 'task-server', principal: 'task-principal', scopes: ['observe', 'operate'],
    allowed: true, paused: false, version: 7, taskState: 'running', status: 'succeeded', failRead: false, failTask: false,
    operation: null as any, posts: [] as any[], reads: [] as string[], errors: [] as string[], events: [] as any[],
    after: 0, lost: false, holdPost: null as any, delayPost: false, taskEvents: [] as any[], list: [] as any[], listLimit: 2,
    holdList: null as any, delayList: false, failList: false, ...options};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    const caps = {actor: state.actor, scopes: state.scopes, api_version: 1, contract_version: '2026-10-08',
      hosts: [{host: 'demo', writes: true}], actions: state.allowed === null ? []
        : ['task.pause', 'task.resume'].map(action => ({action, allowed: state.allowed})), features: {}};
    if (input.method === 'POST') {
      state.posts.push(structuredClone(input));
      if (state.refuse) return {status: 409, data: {error: {code: state.refuse, message: 'Task admission refused'}}};
      if (!state.operation || state.operation.idempotency_key !== input.idempotency_key) {
        state.operation = {...input.body, actor: state.actor, operation_id: opId, idempotency_key: input.idempotency_key,
          status: state.status, steps: [{name: 'task_pause', status: 'succeeded'}], external_refs: {},
          result: {task_id: taskId, paused: input.body.action === 'task.pause'}};
        if (state.status === 'succeeded') {state.paused = input.body.action === 'task.pause'; state.version++;}
      }
      const response = structuredClone(state.operation);
      if (state.delayPost) await new Promise(resolve => {state.holdPost = resolve;});
      if (state.lost) {state.lost = false; return {status: 503, data: {error: {code: 'LOST', message: 'Task reply lost'}}};}
      if (state.badKey) response.idempotency_key = 'wrong-key';
      if (state.badVersion) response.preconditions.control_version++;
      return {status: 200, data: {operation: response}};
    }
    state.reads.push(input.path);
    if (path === '/tasks/'+taskId) {
      if (state.delayTask) await new Promise(resolve => {state.holdTask = resolve;});
      if (state.failTask) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Task read failed'}}};
      return {status: 200, data: {task: {task_id: taskId, host: 'demo', session_id: 'managed-fixture-session',
        project: 'Review the shared dashboard', state: state.taskState, paused: state.paused, control_version: state.version,
        events: state.taskEvents}}};
    }
    if (path === '/operations/'+opId) {
      if (state.delayRead) await new Promise(resolve => {state.holdRead = resolve;});
      if (state.failRead) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'Operation read failed'}}};
      return {status: 200, data: {operation: {...state.operation, status: state.status}}};
    }
    if (path === '/operations') {
      if (state.delayList) {state.delayList = false; await new Promise(resolve => {state.holdList = resolve;});}
      if (state.failList) return {status: 503, data: {error: {code: 'READ_FAILED', message: 'List read failed'}}};
      const before = Number(url.searchParams.get('before') || Infinity);
      const statuses = url.searchParams.get('status')?.split(',');
      const rows = state.list.filter((op: any) => op.created_at < before && (!statuses || statuses.includes(op.status)));
      const operations = rows.slice(0, state.listLimit);
      return {status: 200, data: {operations, next_before: rows.length > operations.length ? operations.at(-1).created_at : null}};
    }
    if (path === '/events') state.after = Number(url.searchParams.get('after'));
    const events = state.events.filter((e: any) => e.seq > state.after), cursor = events.at(-1)?.seq || state.after;
    return {status: 200, data: path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
      sync: {version: 1, server_id: state.server, principal_id: state.principal, checkpoint: {cursor: 0, token: 'proof-0'}}}
      : path === '/events' ? {events, head_cursor: cursor, next_cursor: cursor, sync: {checkpoint: {cursor, token: 'proof-'+cursor}}}
      : {operations: [], hosts: [], sessions: [], work_items: [], events: [], relations: [], next_cursor: null}};
  };
  page.on('pageerror', e => state.errors.push(e.message));
  if (native) {
    await page.exposeFunction('taskFixture', dispatch);
    await page.addInitScript(() => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === 'native_status') return {endpoint: 'https://fixture.example', credential_available: true};
      if (command === 'connector_connect') return (await (window as any).taskFixture({method: 'GET', path: '/capabilities'})).data;
      if (command === 'connector_disconnect') return;
      if (command === 'connector_request') return (window as any).taskFixture(args.input);
      if (command === 'fleet_availability') return {configured: false, platform_supported: false};
      throw new Error(command);
    }}}));
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Unexpected browser HTTP');
    const req = route.request(), url = new URL(req.url());
    const response = await dispatch({method: req.method(), path: url.pathname.slice(7)+url.search,
      body: req.postDataJSON(), idempotency_key: req.headers()['idempotency-key']});
    await route.fulfill({status: response.status, json: response.data});
  });
  return state;
}
