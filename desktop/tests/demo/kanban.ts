import {unix, type DemoProject} from './project.ts';

const COVERAGE = {network_checked: false, unavailable_sources: false, truncated: false, inaccessible_sources: false, unsupported_sources: false};

function completion(column: string) {
  if (column === 'accepted') return {approved: true, pending: false, display_state: 'done'};
  if (column === 'review') return {approved: false, pending: true, display_state: 'awaiting_approval'};
  if (column === 'in_progress') return {approved: false, pending: false, display_state: 'doing'};
  if (column === 'waiting') return {approved: false, pending: false, display_state: 'waiting'};
  return {approved: false, pending: false, display_state: 'todo'};
}

function workState(column: string) {
  if (column === 'accepted' || column === 'review') return 'done';
  if (column === 'in_progress') return 'doing';
  if (column === 'waiting') return 'waiting';
  return 'todo';
}

function flow(project: DemoProject, item: DemoProject['work'][number]) {
  const wip = item.column === 'in_progress' || item.column === 'waiting' || item.column === 'review';
  return {
    wip, history: 'available_central_events', age_seconds: item.ageSeconds, cycle_seconds: item.cycleSeconds,
    started_at: item.column === 'backlog' ? null : unix(project.now, -6), state_since: unix(project.now, -1),
    closed_at: item.column === 'accepted' ? unix(project.now, -1) : null,
    waiting_since: item.column === 'waiting' ? unix(project.now, -5) : null,
    waiting_seconds: item.column === 'waiting' ? 5 * 86400 : null, total_waiting_seconds: item.column === 'waiting' ? 5 * 86400 : null,
    reopened: false, sources: [], blocked: 'unknown', blocked_reason: null, cancelled: 'unknown', basis: 'recorded_work_content_approval',
  };
}

function execution(project: DemoProject, key: string) {
  const empty = {attempts: [], accepted_attempts: 0, attempts_basis: 'visible_send_identities_not_operation_retries_or_sessions',
    tasks: [], operations: [], sessions: [], coverage: COVERAGE};
  if (key === 'rate') return {...empty, sessions: [{resource_id: `${project.ids.host}/${project.ids.sessions.review}`, runtime_status: 'needs_review', observed_at: unix(project.now, -1)}]};
  if (key !== 'email') return empty;
  return {
    attempts: [
      {attempt_id: 'command-1', accepted: false, status: 'failed', observed_at: unix(project.now, -6)},
      {attempt_id: 'command-2', accepted: true, status: 'succeeded', observed_at: unix(project.now, -4)},
      {attempt_id: 'command-3', accepted: true, status: 'succeeded', observed_at: unix(project.now, -2)},
    ],
    accepted_attempts: 2, attempts_basis: 'visible_send_identities_not_operation_retries_or_sessions', tasks: [],
    operations: [
      {operation_id: project.ids.mergeOp, status: 'succeeded', observed_at: unix(project.now, -3)},
      {operation_id: project.ids.deployOp, status: 'succeeded', observed_at: unix(project.now, -1)},
    ],
    sessions: [{resource_id: `${project.ids.host}/${project.ids.sessions.implement}`, runtime_status: 'running', observed_at: unix(project.now)}],
    coverage: COVERAGE,
  };
}

function capture(project: DemoProject, item: DemoProject['work'][number]) {
  if (item.key !== 'email') return null;
  return {
    workspace_id: project.ids.workspaceId, work_item_id: item.id, binding_id: item.bindingId, capture_id: 'capture-email-fix',
    source_work_version: item.version, captured_at: unix(project.now, -1), coverage: COVERAGE,
    evidence: [{workspace_id: project.ids.workspaceId, evidence_id: 'evidence-email-fix', source_kind: 'observed_verification',
      assessment: {verification: 'passed'}, source_observed_at: unix(project.now, -1)}],
  };
}

export function cards(project: DemoProject) {
  const observed = unix(project.now);
  return project.work.map(item => {
    const shot = capture(project, item);
    return {
      work_item_id: item.id,
      binding: {
        binding_id: item.bindingId, sequence: item.n, workspace_id: project.ids.workspaceId, work_item_id: item.id,
        project_id: project.ids.projectId, project_binding_id: project.ids.projectBindingId, work_version: item.version,
        previous_binding_id: null, plan_revision_id: project.ids.planV3, requirement_ids: [item.requirementId],
        created_at: unix(project.now, -12), operation_id: `op_${item.n.toString(16).padStart(32, '0')}`,
      },
      work: {
        work_item_id: item.id, project_id: project.ids.projectId, title: item.title, state: workState(item.column),
        version: item.version, pinned: false, archived: false, created_at: unix(project.now, -18), updated_at: observed,
      },
      column: item.column, completion: completion(item.column), project_archived: false, flow: flow(project, item),
      execution: execution(project, item.key), quality: {dod: 'unassessed'}, latest_capture: shot,
      capture_matches_binding: shot ? true : null, capture_matches_work_version: shot ? true : null,
      dependencies: {status: 'unknown', items: [], reason: 'no_work_dependency_contract'},
      assignment: {status: 'unknown', actor: null, reason: 'creator_is_not_assignee'},
    };
  });
}

function capabilities() {
  return {read_only: true, dispatch: false, session_control: false, quality_evaluation: false, ready_gate: false,
    work_dependencies: false, assignment: false, wip_limit: {status: 'not_configured', value: null},
    resource_capacity: {status: 'unknown', value: null}, cost: 'unknown'};
}

export function kanbanPage(project: DemoProject, url: URL) {
  const after = Number(url.searchParams.get('after') || 0);
  const limit = Number(url.searchParams.get('limit') || 20);
  const projectId = url.searchParams.get('project_id');
  const rows = cards(project).filter(card => card.binding.sequence > after && (!projectId || card.binding.project_id === projectId));
  const slice = rows.slice(0, limit);
  const active = slice.filter(card => card.column !== 'archived');
  return {
    version: 1, workspace_id: project.ids.workspaceId, project_id: projectId, observed_at: unix(project.now), journal_cursor: 80,
    cards: slice, next_cursor: rows.length > slice.length && slice.length ? slice[slice.length - 1].binding.sequence : null,
    capabilities: capabilities(),
    coverage: {basis: 'current_explicit_work_bindings', network_checked: false, scope: 'returned_page', pagination_snapshot: false, byte_limited: false},
    wip: {scope: 'returned_page', basis: 'started_not_content_approved', count: active.filter(card => card.flow.wip === true).length,
      unknown: active.filter(card => card.flow.wip === null).length, cards: active.length, archived_excluded: slice.length - active.length, limit: null},
  };
}

export function kanbanCard(project: DemoProject, workItemId: string) {
  const card = cards(project).find(row => row.work_item_id === workItemId);
  if (!card) return null;
  return {version: 1, workspace_id: project.ids.workspaceId, observed_at: unix(project.now), journal_cursor: 80, card};
}
