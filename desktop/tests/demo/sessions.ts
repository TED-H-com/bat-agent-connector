// Projects, managed sessions, and one manual BAT session. The manual session stays read-only.
import {iso, unix, type DemoProject} from './project.ts';

export function capabilities(project: DemoProject, buckets = true) {
  return {
    actor: project.ids.actor, scopes: ['observe', 'operate'], api_version: 1, contract_version: '2026-10-08',
    hosts: [{host: project.ids.host, writes: true, observe: true}],
    actions: [
      {action: 'session.send', allowed: true},
      {action: 'session.answer', allowed: true},
      {action: 'session.interrupt', allowed: true},
    ],
    features: {pm_overview: {version: 1, read_only: true}, pm_workspaces: {version: 1, plan_drafting: true, plan_adoption: true, brief_snapshots: true, kanban: true, governance: true, meetings: true, follow_up_prepare: true, follow_up_analysis: true,
      quality_reviews: true, issue_mapping: true, engineering_delivery: {contract: 'pm-engineering-delivery/v1'}, roadmap: true,
      conversation_proposals: {version: 1}, maintenance_repairs: {version: 1}}, pm_resources: buckets ? {version: 1, buckets: {granularity: 'day' as const}} : {version: 1},
      pm_schedule: {version: 1, read_only: true}, pm_dependencies: {version: 1}, pm_register: {version: 1}},
  };
}

export function bootstrap(project: DemoProject, buckets = true) {
  return {capabilities: capabilities(project, buckets), sync: {version: 1, server_id: project.ids.serverId, principal_id: 'demo-reader',
    checkpoint: {cursor: 0, token: 'demo-0'}}};
}

export function events(url: URL) {
  const after = Number(url.searchParams.get('after') || 0);
  const token = url.searchParams.get('checkpoint') || `demo-${after}`;
  return {events: [], next_cursor: after, head_cursor: after, has_more: false, sync: {checkpoint: {cursor: after, token}}};
}

function display(column: string) {
  if (column === 'accepted') return {state: 'done', completion: {approved: true, pending: false, display_state: 'done'}};
  if (column === 'review') return {state: 'done', completion: {approved: false, pending: true, display_state: 'awaiting_approval'}};
  if (column === 'in_progress') return {state: 'doing', completion: {approved: false, pending: false, display_state: 'doing'}};
  if (column === 'waiting') return {state: 'waiting', completion: {approved: false, pending: false, display_state: 'waiting'}};
  return {state: 'todo', completion: {approved: false, pending: false, display_state: 'todo'}};
}

function projectSummary(project: DemoProject) {
  return {
    project_id: project.ids.projectId, name: project.copy.name, description: project.copy.goal, archived: false, version: 1,
    repositories: [project.ids.repository], counts: {todo: 2, doing: 3, waiting: 1, awaiting_approval: 2, done: 4, total: 12, pending: 2},
    children: [],
  };
}

export function projectList(project: DemoProject) {
  return {projects: [projectSummary(project)]};
}

export function projectDetail(project: DemoProject) {
  const workItems = project.work.map(item => ({work_item_id: item.id, title: item.title, ...display(item.column), children: [], steps: []}));
  const session = (id: string, title: string, streaming: boolean, status: string, operationId?: string) => ({
    kind: 'session', id, title, host: project.ids.host, session_id: id, actor: project.ids.actor, eligible: false,
    session: {streaming}, status, ...(operationId ? {operation_id: operationId} : {}),
  });
  return {
    project: projectSummary(project), path: [], sub_projects: [], work_items: workItems,
    work: [
      session(project.ids.sessions.implement, project.copy.sessionImplement, true, 'running', `op_${'7'.padStart(32, '0')}`),
      session(project.ids.sessions.review, project.copy.sessionReview, false, 'needs_ted'),
      {...session(project.ids.sessions.manual, project.copy.manualTitle, false, 'human_owned'),
        reading: {unread_count: 3, complete: true}},
      {kind: 'execution', id: 'failed-delivery', title: project.copy.failedTitle, host: project.ids.host,
        status: 'failed', session: {}},
    ],
  };
}

function sessionRow(project: DemoProject, id: string) {
  const at = iso(project.now, -1);
  const common = {host: project.ids.host, session_id: id, workspace: project.copy.name, observed_at: at,
    confinement: {level: 'none', options: {}, evidence: {}}, streaming: false};
  if (id === project.ids.sessions.manual) return {...common, title: project.copy.manualTitle, api_access: 'read_only', provenance: 'manual'};
  if (id === project.ids.sessions.implement) return {...common, title: project.copy.sessionImplement, api_access: 'managed', provenance: 'connector_managed',
    agent_kind: 'codex', model: 'example-codex', streaming: true};
  if (id === project.ids.sessions.review) return {...common, title: project.copy.sessionReview, api_access: 'managed', provenance: 'connector_managed',
    agent_kind: 'claude', model: 'example-claude'};
  return null;
}

export function sessionGet(project: DemoProject, host: string, id: string) {
  if (host !== project.ids.host) return null;
  const session = sessionRow(project, id);
  if (!session) return null;
  return {session, connector_metadata: {version: 1, labels: [], updated_by: project.ids.actor, updated_at: iso(project.now, -1)}};
}

export function sessionMessages(project: DemoProject, host: string, id: string) {
  const session = sessionGet(project, host, id);
  if (!session) return null;
  if (id === project.ids.sessions.manual) return {messages: [{id: 'manual-note-1', role: 'user', ts: iso(project.now, -1), text: project.copy.manualNote}], next_offset: null};
  const text = id === project.ids.sessions.implement ? project.copy.sessionImplement : project.copy.sessionReview;
  return {messages: [{id: id + '-note', role: 'assistant', ts: iso(project.now, -1), text}], next_offset: null};
}

export function workspaceRecord(project: DemoProject) {
  const at = unix(project.now, -30);
  return {workspace_id: project.ids.workspaceId, server_id: project.ids.serverId, name: project.copy.name,
    repositories: [project.ids.repository], version: 4, created_by: project.ids.actor, created_at: at, updated_at: unix(project.now, -1),
    membership: {actor: project.ids.actor, role: 'reader', controller_id: project.ids.controllerId}};
}

export function workItems(project: DemoProject, url: URL) {
  const projectId = url.searchParams.get('project_id');
  const items = projectId === project.ids.projectId ? project.work.map(item => ({work_item_id: item.id, title: item.title})) : [];
  return {work_items: items, next_cursor: null};
}
