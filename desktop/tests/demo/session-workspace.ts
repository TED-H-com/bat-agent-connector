// Stateful session reads for the member sign-in demo. Writes stay inside this fixture.
import {iso, type DemoProject} from './project.ts';
import {sessionGet as staticSession} from './sessions.ts';

export function sessionWorkspace(project: DemoProject) {
  const buckets = new Map<string, Array<Record<string, unknown>>>();
  const pending = new Map<string, Record<string, unknown>>();
  const operations = new Map<string, Record<string, unknown>>();
  const log: Array<Record<string, unknown>> = [];
  let seq = 0;

  const paragraph = 'The sign-in form keeps the member on this step until the code is confirmed. ';
  const implement = [];
  for (let i = 1; i <= 16; i++) {
    implement.push({
      id: `impl-${i}`,
      role: i % 2 ? 'assistant' : 'user',
      ts: iso(project.now, -2),
      text: i === 8
        ? 'The handler checks the token.\n\n```ts\nfunction allow(token: string) {\n  return token.length > 8;\n}\n```\n'
        : `Note ${i}. ${paragraph.repeat(2)}`,
      ...(i % 2 ? {agent: 'codex', model: 'example-codex'} : {}),
    });
  }
  implement.push({id: 'impl-tool', role: 'tool', tool: 'Bash', status: 'completed', ts: iso(project.now, -1), text: 'npm test\n12 passed'});
  implement.push({id: 'impl-last', role: 'assistant', agent: 'codex', model: 'example-codex', ts: iso(project.now, -1), text: project.copy.sessionImplement});
  buckets.set(project.ids.sessions.implement, implement);
  buckets.set(project.ids.sessions.review, [
    {id: 'review-1', role: 'assistant', agent: 'claude', model: 'example-claude', ts: iso(project.now, -1), text: project.copy.sessionReview},
  ]);
  buckets.set(project.ids.sessions.manual, [
    {id: 'manual-note-1', role: 'user', ts: iso(project.now, -1), text: project.copy.manualNote},
  ]);
  pending.set(project.ids.sessions.review, {
    kind: 'permission', toolUseId: 'toolu_demo_review', toolName: 'Bash', input_preview: project.copy.approvalAsk,
  });

  function publish(host: string, id: string) {
    seq += 1;
    log.push({seq, kind: 'session.updated', resource_type: 'session', resource_id: `${host}/${id}`});
  }

  function document(host: string, id: string) {
    const base = staticSession(project, host, id);
    if (!base) return null;
    const session = {...base.session, ...(pending.has(id) ? {pending: pending.get(id)} : {})};
    const page: Record<string, unknown> = {...base, session};
    if (id === project.ids.sessions.implement) {
      page.work_items = [{work_item_id: project.ids.work.email, title: project.copy.work.email, project_id: project.ids.projectId}];
    }
    return page;
  }

  function messages(host: string, id: string) {
    if (!document(host, id)) return null;
    return {messages: buckets.get(id) || [], next_offset: null};
  }

  function events(url: URL) {
    const after = Number(url.searchParams.get('after') || 0);
    const incoming = url.searchParams.get('checkpoint') || 'demo-0';
    const fresh = log.filter(event => Number(event.seq) > after);
    const next = fresh.length ? Number(fresh[fresh.length - 1].seq) : after;
    const token = fresh.length ? `demo-${next}` : incoming;
    return {events: fresh, next_cursor: next, head_cursor: Math.max(next, after), has_more: false,
      sync: {checkpoint: {cursor: next, token}}};
  }

  function operate(input: {body?: unknown, idempotency_key?: string}) {
    const key = input.idempotency_key || '';
    if (key && operations.has(key)) return {status: 200, data: {operation: operations.get(key)}};
    const body = (input.body && typeof input.body === 'object' ? input.body : {}) as {action?: string, target?: {host?: string, session_id?: string}, params?: Record<string, unknown>};
    const host = body.target?.host || '';
    const id = body.target?.session_id || '';
    const session = document(host, id)?.session as {api_access?: string} | undefined;
    const forbid = {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Not allowed'}}};
    if (!session || session.api_access !== 'managed') return forbid;
    if (body.action === 'session.send' && typeof body.params?.text === 'string') {
      const rows = buckets.get(id) || [];
      rows.push({id: `sent-${rows.length + 1}`, role: 'user', ts: iso(project.now, 0), text: body.params.text});
      buckets.set(id, rows);
      publish(host, id);
    } else if (body.action === 'session.answer') {
      const current = pending.get(id);
      if (!current || body.params?.tool_use_id !== current.toolUseId)
        return {status: 409, data: {error: {code: 'PENDING_CHANGED', message: 'Pending changed'}}};
      pending.delete(id);
      publish(host, id);
    } else if (body.action === 'session.interrupt') publish(host, id);
    else return forbid;
    const operation = {operation_id: `op_${seq.toString(16).padStart(32, '0')}`, action: body.action, status: 'succeeded',
      target: body.target, params: body.params || {}};
    if (key) operations.set(key, operation);
    return {status: 200, data: {operation}};
  }

  function append(id: string, text: string) {
    const rows = buckets.get(id);
    if (!rows) return;
    rows.push({id: `live-${rows.length + 1}`, role: 'assistant', agent: 'codex', model: 'example-codex', ts: iso(project.now, 0), text});
    publish(project.ids.host, id);
  }

  return {document, messages, events, operate, append};
}
