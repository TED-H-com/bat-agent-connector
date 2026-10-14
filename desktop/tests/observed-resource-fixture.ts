export const wt = 'wt_' + 'a'.repeat(32);
export const manualWt = 'wt_' + 'b'.repeat(32);
export const unknownWt = 'wt_' + 'c'.repeat(32);
export const taskId = 'task_' + 'd'.repeat(32);
export const opId = 'op_' + 'e'.repeat(32);
export const projectId = 'prj_' + 'f'.repeat(20);
export const workId = 'op_' + '1'.repeat(32);
export const bareId = 'op_' + '2'.repeat(32);
export const sessionTitle = 'Review the login copy';
export const workTitle = 'Ship the login copy';

function caps() {
  return {actor: 'observed', scopes: ['observe', 'integrate'], api_version: 1, contract_version: '2026-10-08',
    hosts: [{host: 'demo', writes: true}], repositories: [{repository: 'example/repository'}],
    features: {execution_delivery: {version: 1}, cleanup_task: true},
    artifacts: {capture: {managed_single_file: true}}, actions: []};
}
export function work() {
  return {kind: 'execution', id: workId, host: 'demo', session_id: 'login-session', operation_id: opId,
    task_id: taskId, status: 'succeeded', action: 'repository.continue', title: workTitle, actor: 'codex',
    branch: 'login-copy', repository: 'example/repository', eligible: true, worktree_id: wt, created_at: 1791588000,
    result: {status: 'unverified', commit_sha: null},
    session: {title: sessionTitle, streaming: true},
    delivered_to: [{repository: 'example/repository', pull_number: 12, operation_id: opId,
      pinned_sha: 'a'.repeat(40), delivered_sha: 'b'.repeat(40), delivered_at: 1791590000}]};
}
export function tree(extra = {}) {
  return {resource_id: wt, host: 'demo', branch: 'login-copy', worktree_path: '/srv/login', intent_type: 'task',
    intent_id: taskId, scope_status: 'current', known_sessions: [
      {host: 'demo', session_id: 'login-session', observation: {title: sessionTitle, streaming: true, observed_at: '2026-10-09T23:20:00Z'}},
      {host: 'demo', session_id: 'quiet-session', observation: {streaming: null, observed_at: null, title: ''}},
    ], work: [work()], ...extra};
}

export async function mount(page: any, locale: string, state: {gone: boolean, seq: number, events: any[], empty?: boolean, error?: boolean, intent?: string, resource?: any, task?: any, posts?: any[], hold?: () => Promise<void>}) {
  await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), locale);
  await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname.slice('/api/v1'.length);
    const send = (status: number, data: unknown) => route.fulfill({status, json: data});
    if (route.request().method() !== 'GET') {state.posts?.push(path); return send(405, {error: {code: 'READ_ONLY', message: 'Read only'}});}
    if (path === '/capabilities') return send(200, caps());
    if (path === '/bootstrap') return send(200, {capabilities: caps(), sync: {version: 1, server_id: 'observed', principal_id: 'reader', checkpoint: {cursor: 0, token: 'zero'}}});
    if (path === '/events') {
      const after = Number(url.searchParams.get('after') || 0);
      const events = state.events.filter(event => event.seq > after);
      const cursor = events.at(-1)?.seq ?? after;
      return send(200, {events, next_cursor: cursor, head_cursor: cursor, has_more: false, sync: {checkpoint: {cursor, token: 'e' + cursor}}});
    }
    if (path === '/worktrees/' + wt) {
      const record = structuredClone(state.resource || tree(state.empty ? {work: [], known_sessions: []} : {}));
      if (state.intent) record.intent_type = state.intent;
      const denied = state.gone || state.error;
      if (state.hold) await state.hold();
      return denied ? send(state.error ? 403 : 404, {error: {code: state.error ? 'FORBIDDEN' : 'NOT_FOUND', message: 'Record unavailable'}}) : send(200, {worktree: record});
    }
    if (path === '/worktrees/' + manualWt) return send(200, {worktree: {resource_id: manualWt, host: 'demo', scope_status: 'current', intent_type: 'manual'}});
    if (path === '/worktrees/' + unknownWt) return send(200, {worktree: {resource_id: unknownWt, host: 'demo', scope_status: 'current', intent_type: 'unknown'}});
    if (path === '/tasks/' + taskId) return state.gone || state.error ? send(state.error ? 403 : 404, {error: {code: state.error ? 'FORBIDDEN' : 'NOT_FOUND', message: 'Record unavailable'}}) : send(200, {task: state.task || {task_id: taskId, host: 'demo', session_id: 'login-session', project: 'Login copy',
      original_words: workTitle, state: state.empty ? null : 'running', paused: state.empty ? null : false, external_branch: 'login-copy', external_worktree_path: '/srv/login'}});
    if (path === '/projects/' + projectId) {
      if (state.error) return send(403, {error: {code: 'FORBIDDEN', message: 'Record unavailable'}});
      const bare = {...work(), id: bareId, operation_id: bareId, title: 'Notes without a session', session_id: '', delivered_to: []};
      return send(200, {project: {project_id: projectId, name: 'Login', repositories: ['example/repository'], version: 1, counts: {total: 0, approved: 0}},
        path: [], sub_projects: [], work_items: [], work: state.empty ? [] : [work(), bare], archived: []});
    }
    if (path.endsWith('/history')) return send(200, {events: [], next_cursor: null, as_of: 1});
    if (path.endsWith('/relations')) return send(200, {relations: [], next_cursor: null, as_of: 1});
    return send(200, {sessions: [], operations: [], hosts: [], work_items: [], events: [], relations: [], next_cursor: null});
  });
}

