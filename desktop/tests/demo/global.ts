// Pending, operations and hosts for the member sign-in demo. Shapes follow the central reads.
// Pending work items are only the review column, and only when the list is not scoped to a project.
import {hexId, unix, type DemoProject} from './project.ts';

export const UNCERTAIN_OPERATION = hexId('op_', 41, 32);
export const RUNNING_OPERATION = hexId('op_', 42, 32);

function operation(project: DemoProject, id: string, action: string, status: string, extra: Record<string, unknown> = {}) {
  const created = Object.hasOwn(extra, 'created_at') ? extra.created_at : unix(project.now, -1);
  return {
    operation_id: id, action, status, status_reason: extra.status_reason ?? null, error_code: extra.error_code ?? null,
    actor: extra.actor ?? null, entry: 'http', target: extra.target ?? null, params: {}, preconditions: {},
    result: null, external_refs: null, created_at: created, updated_at: created, finished_at: extra.finished_at ?? null,
    attempts: status === 'uncertain' ? 1 : 0, uncertain_tries: status === 'uncertain' ? 1 : 0, cancel_requested: false,
    idem_key: null, idempotency_key: null, idempotency_enabled: false, version: 1, steps: extra.steps ?? [],
  };
}

function catalog(project: DemoProject) {
  return [
    operation(project, UNCERTAIN_OPERATION, 'checkpoint.continue', 'uncertain', {
      created_at: null, actor: null, target: null, status_reason: 'Checkpoint result is not confirmed',
      steps: [{seq: 1, name: 'continue', status: 'uncertain', external_ref: null, error: null, started_at: null, finished_at: null}],
    }),
    operation(project, RUNNING_OPERATION, 'artifact.upload', 'running', {
      actor: project.ids.actor, target: {repository: project.ids.repository, title: project.copy.work.rate},
      steps: [{seq: 1, name: 'upload', status: 'running', external_ref: null, error: null, started_at: unix(project.now, -1), finished_at: null}],
    }),
  ];
}

function hostRecord(project: DemoProject) {
  return {
    host: project.ids.host, reachable: false, error: null, server_version: null, sessions: 3,
    last_attempt_at: null, last_success_at: null, consecutive_failures: 1, stale: true,
    stale_reason: 'host_unreachable', confinement: {level: 'none', options: {}, evidence: {}}, discovery: [],
  };
}

function sessionRecord(project: DemoProject, id: string, title: string, extra: Record<string, unknown>) {
  return {
    host: project.ids.host, session_id: id, title, workspace: project.copy.name, agent_kind: extra.agent_kind ?? null,
    api_access: extra.api_access, provenance: extra.provenance, streaming: extra.streaming === true,
    pending: extra.pending ?? null, stale: false, stale_reason: null, worktree_branch: null,
    observed_at: new Date(project.now).toISOString(), resource_id: `${project.ids.host}/${id}`,
    state: {lifecycle: 'active', activity: extra.streaming === true ? 'streaming' : 'not_streaming', freshness: 'fresh', enumeration: 'present'},
  };
}

function sessions(project: DemoProject) {
  return [
    sessionRecord(project, project.ids.sessions.implement, project.copy.sessionImplement,
      {agent_kind: 'codex', api_access: 'managed', provenance: 'connector_managed', streaming: true}),
    sessionRecord(project, project.ids.sessions.review, project.copy.sessionReview,
      {agent_kind: 'claude', api_access: 'managed', provenance: 'connector_managed', pending: {kind: 'ask_user'}}),
    sessionRecord(project, project.ids.sessions.manual, project.copy.manualTitle,
      {api_access: 'read_only', provenance: 'manual'}),
  ];
}

function pendingItems(project: DemoProject) {
  return project.work.filter(item => item.column === 'review').map(item => ({
    work_item_id: item.id, project_id: project.ids.projectId, parent_id: null, derived_from: null,
    title: item.title, goal: '', request: '', acceptance: '', steps: [], attachments: [],
    state: 'done', done_by: project.ids.actor, done_at: unix(project.now, -1), approved_by: null, approved_at: null,
    pinned: false, archived: false, archived_at: null, archived_by: null, version: item.version,
    created_by: project.ids.actor, created_at: unix(project.now, -2), updated_at: unix(project.now, -1),
    project_name: project.copy.name,
    completion: {fingerprint: 'demo-review', approved: false, pending: true, display_state: 'awaiting_approval', claimed_by: null, approved_by: null},
    reading: {read_version: item.version, current_version: item.version, unread: false},
  }));
}

export function globalRoutes(project: DemoProject, url: URL) {
  const path = url.pathname;
  if (path === '/hosts') return {status: 200, data: {hosts: [hostRecord(project)]}};
  if (path === '/sessions') {
    const attention = url.searchParams.get('attention') === 'true';
    const rows = sessions(project).filter(row => !attention || row.pending);
    return {status: 200, data: {sessions: rows, count: rows.length, next_cursor: null, as_of: 0,
      hosts: [hostRecord(project)], coverage: {source: 'journal', paging: 'keyset', relation_scope: 'history'}}};
  }
  if (path === '/operations') {
    const statuses = (url.searchParams.get('status') || '').split(',').filter(Boolean);
    const rows = catalog(project).filter(row => !statuses.length || statuses.includes(String(row.status)));
    return {status: 200, data: {operations: rows, next_before: null}};
  }
  const one = path.match(/^\/operations\/(op_[0-9a-f]{32})$/);
  if (one) {
    const found = catalog(project).find(row => row.operation_id === one[1]);
    if (!found) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'operation not found'}}};
    return {status: 200, data: {operation: found, work_items: [], cleanup_receipts: []}};
  }
  if (path === '/work-items' && !url.searchParams.get('project_id') && (url.searchParams.has('pending') || url.searchParams.has('unread'))) {
    const unread = url.searchParams.has('unread');
    const rows = unread ? [] : pendingItems(project);
    return {status: 200, data: {work_items: rows, next_cursor: null}};
  }
  return null;
}
