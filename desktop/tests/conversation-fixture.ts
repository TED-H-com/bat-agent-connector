import {managedStatusFixture} from './managed-status-fixture.ts';
import type {Page} from '@playwright/test';

export const caps = {actor: 'reader', scopes: ['observe', 'operate'], api_version: 1,
  contract_version: '2026-10-08', hosts: [], actions: [], features: {}};
export const checkpoint = (cursor: number) => ({cursor, token: `conversation-${cursor}`});
export const richMessage = 'Review the two options.\n\n| Option | Result |\n| --- | --- |\n| **A** | `ready` |\n| B | Keep the original |\n\n```js\r\nconst result = "<script>fixture only</script>";\r\nconsole.log(result);\r\n```\nAll source text remains available.';
export function conversationFixture() {
  const data = {cursor: 0, fail: false, principal: 'one', reads: 0, writes: [] as any[],
    messages: Array.from({length: 12}, (_, i) => ({id: `message-${i}`, role: i % 3 ? 'assistant' : 'user',
      ts: `2026-10-09T12:${String(i).padStart(2, '0')}:00Z`, text: i === 11 ? richMessage : `Review note ${i + 1}\n${'Keep the source and verify the result. '.repeat(10)}`}))};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method !== 'GET') {data.writes.push(input); throw new Error('Reading must not write');}
    if (path.endsWith('/messages')) {
      data.reads++;
      if (data.fail) return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Messages unavailable'}}};
      return {status: 200, data: {messages: data.messages}};
    }
    return {status: 200, data: path === '/capabilities' ? caps : path === '/bootstrap' ? {capabilities: caps,
      sync: {version: 1, server_id: 'conversation-server', principal_id: data.principal, checkpoint: checkpoint(0)}}
      : path === '/events' ? {events: Number(url.searchParams.get('after')) < data.cursor
        ? [{seq: data.cursor, resource_type: 'session', resource_id: 'demo/session-1', kind: 'session.updated'}] : [],
        next_cursor: data.cursor, head_cursor: data.cursor, has_more: false, sync: {checkpoint: checkpoint(data.cursor)}}
      : path === '/sessions/demo/session-1' ? {session: {host: 'demo', session_id: 'session-1', title: 'Review delivery options',
        workspace: 'Dashboard', agent_kind: 'codex', api_access: 'managed', provenance: 'connector_managed'}}
      : {messages: [], checkpoints: [], sessions: [], work_items: [], operations: [], relations: [], history: [], events: []}};
  };
  return {data, dispatch};
}
export async function mountConversation(page: Page, native: boolean, dispatch: (input: any) => Promise<any>) {
  if (native) {
    await page.exposeFunction('fixtureConnector', dispatch);
    await page.addInitScript(managed => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {
      invoke: async (command: string, args: any) => {
        if (command === 'managed_control' && args.input.action === 'status') return managed;
        if (command === 'native_status') return {endpoint: 'https://central.example/', credential_available: true};
        // The native connect receipt comes from the same Connector principal as
        // subsequent reads, including demos with a named human participant.
        if (command === 'connector_connect') {
          const result = await (window as any).fixtureConnector({method: 'GET', path: '/capabilities'});
          if (result.status !== 200) throw new Error('Fixture connection capability read failed');
          return result.data;
        }
        if (command === 'connector_disconnect') return null;
        if (command === 'connector_request') return (window as any).fixtureConnector(args.input);
        throw new Error(`Unexpected native command ${command}`);
      }
    }}), managedStatusFixture(false));
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token', 'fixture-token'));
  await page.route('**/api/v1/**', async route => {
    if (native) throw new Error('Native must use IPC');
    const request = route.request(), url = new URL(request.url());
    const result = await dispatch({method: request.method(), path: url.pathname.slice(7) + url.search,
      body: request.postData() ? request.postDataJSON() : null, idempotency_key: request.headers()['idempotency-key']});
    await route.fulfill({status: result.status, json: result.data});
  });
}
