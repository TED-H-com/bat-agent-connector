import {overview, overviewGovernance} from './overview.ts';
import {discussionDemo} from './discussions.ts';
// One dispatcher for the member sign-in demo. Shapes come from the current central reads.
// Unknown routes throw. A known id that is not in this project is a 404. Nothing here writes.
import {demoProject, type DemoProject} from './project.ts';
import {pagePlans, plans} from './plans.ts';
import {pageSnapshots, snapshots} from './snapshots.ts';
import {kanbanCard, kanbanPage} from './kanban.ts';
import {deliveryPage, issueMappings, pageIssues} from './rtm.ts';
import {roadmap, roadmapRevisionList, roadmapView, workBindings} from './roadmap.ts';
import {schedulePage} from './schedule.ts';
import {resources} from './resources.ts';
import {proposalDetail, proposalList, threadDetail, threadList, workspaceCapabilities} from './threads.ts';
import {registerDetail, registerList} from './register.ts';
import {bootstrap, capabilities, events, projectDetail, projectList, sessionGet, sessionMessages, workItems, workspaceRecord} from './sessions.ts';
import {dependencyDocument, dependencyImpact, dependencyRevisions} from './dependencies.ts';
import {globalRoutes} from './global.ts';

export interface DemoRequest {
  method?: string;
  path: string;
  body?: unknown;
  idempotency_key?: string;
}

const WORKBENCH_VIEWS = new Set(['charter', 'rtm', 'board', 'agents', 'planning', 'governance', 'workspace', 'maintenance', 'pm']);

function notFound() {
  return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Not found'}}};
}

function ok(data: unknown) {
  return {status: 200, data: structuredClone(data)};
}

function segment(value: string) {
  return decodeURIComponent(value);
}

function workspaceRoutes(project: DemoProject, url: URL, rest: string) {
  if (rest === '' || rest === '/') return ok({workspace: workspaceRecord(project)});
  if (rest === '/overview') return ok(overview(project));
  if (rest === '/governance') return ok({version: 1, workspace_id: project.ids.workspaceId, records: overviewGovernance(project), next_cursor: null});
  const governance = rest.match(/^\/governance\/([^/]+)$/);
  if (governance) {const row = overviewGovernance(project).find(row => row.record.record_id === governance[1]); return row ? ok({version: 1, workspace_id: project.ids.workspaceId, ...row}) : notFound();}
  if (rest === '/capabilities') return ok(workspaceCapabilities(project));
  if (rest === '/plans/head') return ok({version: 1, head: plans(project).head});
  if (rest === '/plans/accepted') {
    const saved = plans(project);
    return ok({version: 1, plan: saved.v3, head: saved.head});
  }
  if (rest === '/plans') return ok(pagePlans(project, url));
  const plan = rest.match(/^\/plans\/([^/]+)$/);
  if (plan) {
    const saved = plans(project);
    const found = saved.all.find(row => row.revision_id === segment(plan[1]));
    return found ? ok({version: 1, plan: found, head: saved.head}) : notFound();
  }
  if (rest === '/snapshots/latest') {
    const rows = snapshots(project);
    return ok({version: 1, workspace_id: project.ids.workspaceId, snapshot: rows[rows.length - 1]});
  }
  if (rest === '/snapshots') return ok(pageSnapshots(project, url));
  const snapshot = rest.match(/^\/snapshots\/([^/]+)$/);
  if (snapshot) {
    const found = snapshots(project).find(row => row.snapshot_id === segment(snapshot[1]));
    return found ? ok({version: 1, workspace_id: project.ids.workspaceId, snapshot: found}) : notFound();
  }
  if (rest === '/kanban') return ok(kanbanPage(project, url));
  const card = rest.match(/^\/kanban\/([^/]+)$/);
  if (card) {
    const found = kanbanCard(project, segment(card[1]));
    return found ? ok(found) : notFound();
  }
  if (rest === '/issue-mappings') return ok(pageIssues(project, url));
  const mapping = rest.match(/^\/issue-mappings\/([^/]+)$/);
  if (mapping) {
    const found = issueMappings(project).find(row => row.mapping_id === segment(mapping[1]));
    return found ? ok({version: 1, workspace_id: project.ids.workspaceId, mapping_id: found.mapping_id,
      current: found, revisions: [found], execution_authorized: false}) : notFound();
  }
  const delivery = rest.match(/^\/work\/([^/]+)\/deliveries$/);
  if (delivery) {
    const workId = segment(delivery[1]);
    if (!project.work.some(item => item.id === workId)) return notFound();
    return ok(deliveryPage(project, workId, url));
  }
  if (rest === '/roadmap/revisions') return ok(roadmapRevisionList(project, url));
  const revision = rest.match(/^\/roadmap\/revisions\/([^/]+)$/);
  if (revision) {
    const found = roadmap(project).revisions.find(row => row.revision_id === segment(revision[1]));
    return found ? ok({version: 1, workspace_id: project.ids.workspaceId, revision: found}) : notFound();
  }
  if (rest === '/schedule') return ok(schedulePage(project, url));
  if (rest === '/roadmap') return ok(roadmapView(project));
  if (rest === '/work') return ok(workBindings(project, url));
  if (rest === '/resources') return ok(resources(project, url));
  if (rest === '/conversation-proposals') return ok(proposalList(project, url));
  const proposal = rest.match(/^\/conversation-proposals\/([^/]+)$/);
  if (proposal) {
    const detail = proposalDetail(project);
    return segment(proposal[1]) === detail.proposal.proposal_id ? ok(detail) : notFound();
  }
  if (rest === '/dependencies/revisions') return ok(dependencyRevisions(project, url));
  if (rest === '/dependencies/impact') {
    const key = url.searchParams.get('dependency') || '';
    if (!/^pmdepkey_[0-9a-f]{32}$/.test(key)) return {status: 400, data: {error: {code: 'BAD_REQUEST', message: 'Invalid dependency'}}};
    return ok(dependencyImpact(project, key));
  }
  if (rest === '/dependencies') return ok(dependencyDocument(project));
  if (rest === '/register') return registerList(project, url);
  const registerRow = rest.match(/^\/register\/([^/]+)$/);
  if (registerRow) return registerDetail(project, segment(registerRow[1]));
  if (rest === '/threads') return ok(threadList(project, url));
  const thread = rest.match(/^\/threads\/([^/]+)$/);
  if (thread) {
    const detail = threadDetail(project, segment(thread[1]));
    return detail ? ok(detail) : notFound();
  }
  throw new Error(`Unknown demo fixture route GET ${url.pathname}${url.search}`);
}

export function demoFixture({locale, now, discussions = false, buckets = true}: {locale?: string, now?: number, discussions?: boolean, buckets?: boolean} = {}) {
  const project = demoProject(locale, now);
  const root = `/pm/workspaces/${project.ids.workspaceId}`;
  const dispatch = async (input: DemoRequest) => {
    const method = input.method || 'GET';
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;
    if (method !== 'GET') throw new Error(`Demo fixture does not perform ${method} ${input.path}`);
    // Web boot probes a managed browser session. This demo uses the bearer token, so the
    // central answer is the ordinary missing-route 404 (no csrf, no managed session).
    if (path === '/browser-session') return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'no such route'}}};
    if (path === '/capabilities') return ok(capabilities(project, buckets));
    if (path === '/bootstrap') return ok(bootstrap(project, buckets));
    if (path === '/events') return ok(events(url));
    if (path === '/projects') return ok(projectList(project));
    if (path === `/projects/${project.ids.projectId}`) return ok(projectDetail(project));
    if (path.startsWith('/projects/')) return notFound();
    if (path === '/pm/workspaces') return ok({workspaces: [workspaceRecord(project)]});
    if (path.startsWith('/pm/workspaces/')) {
      if (path !== root && !path.startsWith(`${root}/`)) return notFound();
      return workspaceRoutes(project, url, path.slice(root.length));
    }
    const global = globalRoutes(project, url);
    if (global) return global.status === 200 ? ok(global.data) : global;
    if (path === '/work-items') return ok(workItems(project, url));
    const messages = path.match(/^\/sessions\/([^/]+)\/([^/]+)\/messages$/);
    if (messages) {
      const page = sessionMessages(project, segment(messages[1]), segment(messages[2]));
      return page ? ok(page) : notFound();
    }
    const session = path.match(/^\/sessions\/([^/]+)\/([^/]+)$/);
    if (session) {
      const page = sessionGet(project, segment(session[1]), segment(session[2]));
      return page ? ok(page) : notFound();
    }
    if (path === '/checkpoints') return ok({checkpoints: []});
    throw new Error(`Unknown demo fixture route ${method} ${input.path}`);
  };
  const discussion = discussions ? discussionDemo(project, dispatch) : null;
  return {dispatch: discussion?.dispatch || dispatch, discussion: discussion?.state, locale: project.locale, now: project.now, copy: project.copy, ids: project.ids, dates: project.dates};
}

// Workbench view names are in-memory. They are not URL selections, so charter/rtm/board/agents
// all open the workspace. Thread and session names select a real hash.
export function demoRoute(view?: string) {
  const ids = demoProject('en').ids;
  if (!view || WORKBENCH_VIEWS.has(view)) return `/dashboard/#/pm/${ids.workspaceId}`;
  if (view === 'idea') return `/dashboard/#/pm/${ids.workspaceId}/${ids.threadIdea}`;
  if (view === 'planning-thread') return `/dashboard/#/pm/${ids.workspaceId}/${ids.threadPlanning}`;
  if (view === 'delivery') return `/dashboard/#/pm/${ids.workspaceId}/${ids.threadFollow}`;
  if (view.startsWith('pmt_')) return `/dashboard/#/pm/${ids.workspaceId}/${view}`;
  if (view === 'manual' || view === 'session') return `/dashboard/#/session/${ids.host}/${ids.sessions.manual}`;
  if (view === 'project') return `/dashboard/#/project/${ids.projectId}`;
  throw new Error(`Unknown demo route ${view}`);
}
