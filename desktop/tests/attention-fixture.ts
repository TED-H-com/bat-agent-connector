import type {Page} from '@playwright/test';

export function attentionFixture() {
  const data = {principal: 'one', cursor: 0, pageSize: 50, failure: '', supported: true, writes: [] as any[], reads: [] as string[],
    markers: new Map<string, number>(),
    items: Array.from({length: 3}, (_, i) => ({work_item_id: 'wi_' + String(i + 1).padStart(20, '0'), title: ['Review delivery', 'Draft follow-up', 'Accepted work'][i],
      version: 1, project_id: 'prj_' + '1'.repeat(20), project_name: 'Dashboard', state: i === 1 ? 'doing' : 'done',
      completion: {pending: i === 0, approved: i === 2, fingerprint: 'content-' + i,
        display_state: i === 0 ? 'awaiting_approval' : i === 1 ? 'doing' : 'done', claimed_by: i === 0 ? 'agent' : null, approved_by: i === 2 ? 'reviewer' : null},
      goal: 'Keep Web and desktop aligned.', request: 'Review the current result.', acceptance: 'Shared central state.',
      steps: [], archived: false, attachments: []})),
    sessions: [{host: 'demo', session_id: 'session-1', title: 'Question from agent', api_access: 'managed', pending: {kind: 'ask_user'}},
      {host: 'demo', session_id: 'session-2', title: 'Permission requested', api_access: 'read_only', stale: true, stale_reason: 'host_unreachable', pending: {kind: 'permission'}}],
    operations: [{operation_id: 'op_' + '1'.repeat(32), action: 'checkpoint.continue', status: 'uncertain'},
      {operation_id: 'op_' + '2'.repeat(32), action: 'artifact.upload', status: 'running'}]};
  const caps = () => ({actor: 'reader', scopes: ['observe'], api_version: 1, contract_version: '2026-10-08',
    features: data.supported ? {work_item_reads: {version: 1}} : {}, hosts: [],
    actions: data.supported ? [{action: 'work_item.read', allowed: true, scope: 'observe'}] : []});
  const reading = (item: any) => ({...item, reading: {read_version: data.markers.get(data.principal + item.work_item_id) || 0,
    current_version: item.version, unread: (data.markers.get(data.principal + item.work_item_id) || 0) < item.version}});
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method !== 'GET') {
      data.writes.push(input);
      if (input.body.action !== 'work_item.read') throw new Error('Unexpected write');
      const key = data.principal + input.body.target.work_item_id;
      data.markers.set(key, Math.max(data.markers.get(key) || 0, input.body.preconditions.expected_version)); data.cursor++;
      return {status: 200, data: {operation: {operation_id: 'op_' + '3'.repeat(32), status: 'succeeded'}}};
    }
    data.reads.push(input.path);
    if (data.failure && path === data.failure) return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Fixture unavailable'}}};
    let doc: any = {};
    if (path === '/capabilities') doc = caps();
    else if (path === '/bootstrap') doc = {capabilities: caps(), sync: {version: 1, server_id: 'attention-central',
      principal_id: data.principal, checkpoint: {cursor: 0, token: 'attention-0'}}};
    else if (path === '/events') doc = {events: Number(url.searchParams.get('after')) < data.cursor
      ? [{seq: data.cursor, resource_type: 'work_item', resource_id: data.items[0].work_item_id, kind: 'work_item.read'}] : [],
      next_cursor: data.cursor, has_more: false, head_cursor: data.cursor, sync: {checkpoint: {cursor: data.cursor, token: 'attention-' + data.cursor}}};
    else if (path === '/hosts') doc = {hosts: [{host: 'demo', stale: true, stale_reason: 'host_unreachable'}]};
    else if (path === '/sessions') doc = {sessions: data.sessions, next_cursor: null};
    else if (path === '/operations') doc = {operations: data.operations.filter(op => url.searchParams.get('status')?.split(',').includes(op.status)), next_before: null};
    else if (path === '/work-items') {
      const items = data.items.map(reading).filter(item => (!url.searchParams.has('pending') || item.completion.pending)
        && (!url.searchParams.has('unread') || item.reading.unread));
      const offset = Number(url.searchParams.get('cursor') || 0), end = offset + data.pageSize;
      doc = {work_items: items.slice(offset, end), next_cursor: end < items.length ? String(end) : null};
    } else if (path.startsWith('/work-items/')) doc = {work_item: reading(data.items.find(i => path.endsWith(i.work_item_id))),
      project: {project_id: data.items[0].project_id, name: 'Dashboard', archived: false}, path: [], links: [], children: [], derived: [], events: []};
    return {status: 200, data: doc};
  };
  return {data, caps, dispatch};
}

export async function mountAttention(page: Page, native: boolean, fixture: ReturnType<typeof attentionFixture>,
  dispatch = fixture.dispatch) {
  if (native) {
    await page.exposeFunction('attentionConnector', dispatch);
    await page.addInitScript(c => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {
      invoke: async (command: string, args: any) => {
        if (command === 'native_status') return {endpoint: 'https://central.example/', credential_available: true};
        if (command === 'connector_connect') return c;
        if (command === 'connector_disconnect') return null;
        if (command === 'connector_request') return (window as any).attentionConnector(args.input);
        throw new Error(`Unexpected native command ${command}`);
      }
    }}), fixture.caps());
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Native must use IPC');
    const req = route.request(), url = new URL(req.url());
    const result = await dispatch({method: req.method(), path: url.pathname.slice(7) + url.search,
      body: req.postData() ? req.postDataJSON() : null, idempotency_key: req.headers()['idempotency-key']});
    const fulfill: {status: number, json: unknown, headers?: Record<string, string>} = {status: result.status, json: result.data};
    if (result.headers) fulfill.headers = {'content-type': 'application/json', ...result.headers};
    await route.fulfill(fulfill);
  });
}
