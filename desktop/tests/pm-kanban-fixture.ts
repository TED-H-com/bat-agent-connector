import {expect, type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';

export const workspaceId = 'ws_' + '1'.repeat(32), projectId = 'prj_' + '2'.repeat(20);
export const workId = (n:number) => 'wi_' + String(n).repeat(20);
const at = 1791705600;
export function fixture() {
  const cards = [1, 2, 3].map(n => {
    const wid = workId(n), bindingId = 'pmwb_' + String(n).repeat(32), op = 'op_' + String(n).repeat(32);
    return {work_item_id: wid, binding: {workspace_id: workspaceId, work_item_id: wid, sequence: n, binding_id: bindingId,
      project_id: projectId, plan_revision_id: 'pmrev_' + '4'.repeat(32), requirement_ids: ['membership.login'], work_version: 1},
      work: {work_item_id: wid, title: ['Member login', 'Password recovery', 'Account settings'][n-1], version: 2,
        project_id: projectId, state: n === 1 ? 'doing' : 'done'}, column: n === 1 ? 'in_progress' : n === 2 ? 'review' : 'accepted',
      completion: {approved: n === 3, pending: n === 2}, flow: {wip: n < 3, age_seconds: n === 1 ? 7800 : null,
        waiting_seconds: null, history: n === 2 ? 'missing_history' : 'available_central_events', reopened: 0},
      quality: {dod: 'unassessed'}, execution: {accepted_attempts: n === 1 ? 3 : 0,
        tasks: n === 1 ? [{task_id: 'task-login', state: 'running', observed_at: at + 100}] : [],
        operations: [{operation_id: op, status: 'succeeded', observed_at: at + 120}], sessions: [],
        attempts: n === 1 ? [1,2,3].map(i => ({attempt_id: `command-${i}`, accepted: true, status: 'accepted', observed_at: at + 80 + i})) : [],
        coverage: {network_checked: false, unavailable_sources: n === 2, truncated: n === 2}},
      latest_capture: {workspace_id: workspaceId, work_item_id: wid, capture_id: 'pmcap_' + String(n).repeat(32),
        binding_id: bindingId, captured_at: at, source_work_version: 1, coverage: {},
        evidence: [{evidence_id: 'pme_' + String(n).repeat(32), source_kind: 'observed_verification',
          assessment: {verification: 'passed'}, source_observed_at: at - 60}]},
      capture_matches_binding: true, capture_matches_work_version: false};
  });
  const state:any = {cards, reads: [], writes: [], failRead: null, failMore: null, wrongScope: false, wrongCard: false,
    nonAdvancing: false, byteLimited: false, holdNext: false, held: [], time: at + 300};
  const dispatch = async (input:any) => {
    if (input.method !== 'GET') {state.writes.push(input); throw Error('Unexpected write');}
    state.reads.push(input.path);
    const url = new URL(input.path, 'http://fixture'), endpoint = `/pm/workspaces/${workspaceId}/kanban`;
    const after = Number(url.searchParams.get('after') || 0), limit = Number(url.searchParams.get('limit') || 20);
    const fail = state.failRead || (after > 0 && state.failMore);
    if (fail) return {status: fail, data: {error: {code: fail === 403 ? 'FORBIDDEN' : 'UNAVAILABLE',
      message: fail === 403 ? 'Workspace access removed' : 'Work-flow read unavailable'}}};
    let data:any;
    if (url.pathname === endpoint) {
      const selected = state.cards.filter((card:any) => card.binding.sequence > after).slice(0, limit);
      const more = state.cards.some((card:any) => card.binding.sequence > (selected.at(-1)?.binding.sequence ?? after));
      data = {version: 1, workspace_id: workspaceId, project_id: url.searchParams.get('project_id'), observed_at: state.time,
        journal_cursor: 50, cards: selected, next_cursor: more ? state.nonAdvancing ? after : selected.at(-1).binding.sequence : null,
        wip: {scope: 'returned_page', count: selected.filter((c:any) => c.flow.wip === true).length,
          unknown: selected.filter((c:any) => c.flow.wip === null).length, cards: selected.length, archived_excluded: 0},
        coverage: {byte_limited: state.byteLimited}, capabilities: {read_only: true}};
    } else if (url.pathname.startsWith(endpoint + '/')) {
      const card = state.cards.find((c:any) => url.pathname.endsWith(c.work_item_id));
      if (!card) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Work unavailable'}}};
      data = {version: 1, workspace_id: workspaceId, observed_at: state.time, journal_cursor: 50,
        card: state.wrongCard ? {...card, work_item_id: workId(9)} : card};
    } else throw Error('Unexpected read ' + input.path);
    if (state.wrongScope) data.workspace_id = 'ws_' + '9'.repeat(32);
    const result = structuredClone({status: 200, data});
    if (state.holdNext) {state.holdNext = false; return await new Promise(resolve => state.held.push(() => resolve(result)));}
    return result;
  };
  return {state, dispatch};
}

export async function mount(page:Page, f:ReturnType<typeof fixture>, {locale='en', pageSize=2, project=null as string|null}={}) {
  await page.exposeFunction('kanbanFixtureRequest', f.dispatch);
  await page.route('**/__kanban_src/**', async route => {
    const file = new URL(route.request().url()).pathname.split('/__kanban_src/')[1];
    const body = await readFile(new URL('../src/' + file, import.meta.url), 'utf8');
    await route.fulfill({contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body});
  });
  await page.route('**/__kanban_harness', route => route.fulfill({contentType: 'text/html', body:
    '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><link rel="stylesheet" href="/__kanban_src/pm-kanban.css"><div class="pm-shell"><aside><h2>PM workspace</h2><p>Membership service</p></aside><main class="pm-main" id="mount"><section class="panel"><h1>Membership service</h1><p>Goal · A returning member can access their account.</p></section></main></div>'}));
  await page.addInitScript(locale => Object.defineProperty(navigator, 'language', {value: locale}), locale);
  await page.goto('/__kanban_harness');
  await page.evaluate(async ({locale, workspaceId, pageSize, project}) => {
    const {workspaceKanban} = await import('/__kanban_src/pm-kanban.js');
    const {t} = await import('/__kanban_src/i18n.js');
    const h = (tag:string, attrs:any={}, ...children:any[]) => {
      const node = document.createElement(tag);
      for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key.startsWith('on')) node.addEventListener(key.slice(2), value as any);
        else node.setAttribute(key, value === true ? '' : String(value));
      }
      for (const child of children.flat()) if (child !== false && child !== null && child !== undefined) node.append(child instanceof Node ? child : String(child));
      return node;
    };
    const win = window as any, api = async (method:string, path:string) => {
      const result = await win.kanbanFixtureRequest({method, path});
      if (result.status >= 400) throw Object.assign(Error(result.data.error.message), {code: result.data.error.code, status: result.status});
      return result.data;
    };
    const ui = workspaceKanban({h,t,api,alive:()=>{},workspaceId,pageSize,projectId:project,
      errorBox:(error:any)=>h('p',{class:'error'},error.message)});
    document.getElementById('mount')!.append(ui.element);
    win.refreshKanban = ui.refresh;
    await ui.refresh();
  }, {locale, workspaceId, pageSize, project});
  await expect(page.locator('[data-pm-kanban]')).toBeVisible();
}
