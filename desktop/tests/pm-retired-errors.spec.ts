import {test, expect, type Page} from '@playwright/test';
import {workspaceFixture} from './workspace-fixture.ts';
import {mountAttention} from './attention-fixture.ts';
import {intake, workspaceId as followWorkspaceId} from './pm-follow-up-fixture.ts';
import {reviewFixture} from './pm-maintenance-review-fixture.ts';
import {openPmPanel, openReportTask, openThreadTab} from './pm-navigation.ts';

// A PM read can finish after its view was left. These tests hold that read, leave for
// another project, and release it while the new view's event refresh is still open.
// The polling title is painted only after that batch is acknowledged and actions are
// writable again. A missing error node is not that proof: evidence can discard the
// node or parent it to a status that is no longer on the page.
// A failed read is separate. The transport throws HTTP 503 or NETWORK_ERROR
// before the caller reaches alive(). Leaving the workspace changes the view
// generation, not the connection epoch, so that failure is not a retired-view
// code. These tests reject the old request while the next batch is open. The
// checkpoint must still advance and Refresh must stay usable. A failure in the
// view that is still open must still stop that batch.
// Quality projections (pm-quality-projections.js:41), plan diff (pm-plan-diff.js:56,
// :81, :169), plan revision paging (pm-plans.js:158), evidence detail toggles, and the
// other catches that already check isConnected are not covered. Replacing the page
// runs before the next batch, so a disconnected node cannot report into it. The
// workspace draft persist (pm-workspace.js:94) is synchronous with the turn that
// removes the field. The board refresh is owned by a separate change and is not here.
// Plan proposals pass without the retired-view predicate: leaving the workspace
// withdraws that module's ticket before the next batch. Those two cases lock that
// existing withdrawal.

const sourceId = followWorkspaceId;
const destId = 'ws_' + '2'.repeat(32);
const threadId = 'pmt_' + '3'.repeat(32);
const ideaThreadId = 'pmt_' + '6'.repeat(32);
const projectId = 'prj_' + 'a'.repeat(32);
const snapshotId = 'pmsnap_' + '2'.repeat(32);
const transitionId = 'pmlt_' + 'a'.repeat(32);
const revisionId = 'pmrr_' + 'c'.repeat(32);
const checkpoint = (cursor: number) => ({cursor, token: `retired-${cursor}`});

const pausedTitle = /Source refresh failed|資料更新失敗/;
const waitingTitle = /Waiting to refresh|等待更新/;
const pollingTitle = /updates every second|每秒更新/;

function workspaceRecord(id: string, locale: string) {
  const zh = locale === 'zh-TW';
  return {
    workspace_id: id, server_id: 'retired-errors', name: id === sourceId ? (zh ? '目錄整理' : 'Catalogue') : (zh ? '會員服務' : 'Membership'),
    repositories: ['example/library'], version: 1, created_by: 'owner', created_at: 1791705600, updated_at: 1791705600,
    membership: {actor: 'owner', role: 'owner', controller_id: 'ctr_' + '4'.repeat(32)},
  };
}

function lifecycleDocument(id: string) {
  return {
    workspace_id: id,
    lifecycle: {stage: 'planning', status: 'active', revision: 1, execution_epoch: 1, transition_id: null, coverage: 'recorded'},
    transition: null, transition_visibility_filtered: false, blocked_actions: [], record_actions: [],
    available_stages: ['idea', 'planning', 'development'],
    maintenance: {available: false, reason: 'release_baseline_and_policy_required'},
    inflight_summary: {visible_unresolved: 0, visible_retained: 0, visibility_filtered: false},
  };
}

function threadRecord(id: string) {
  return {
    workspace_id: id, thread_id: threadId, title: 'Membership recovery', version: 1, mode: 'planning', updated_at: 1791705600,
  };
}

function intakeFor(id: string) {
  return JSON.parse(JSON.stringify(intake(1)).split(followWorkspaceId).join(id));
}

function savedSync(page: Page) {
  return page.evaluate(() => {
    const entry = Object.entries(localStorage).find(([key]) => key.startsWith('batc.sync.'));
    return entry ? JSON.parse(entry[1]) : null;
  });
}

async function installHeldBodyProbe(page: Page) {
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    (window as any).__heldBodyTokens = [];
    window.fetch = async function(...args) {
      const requestUrl = typeof args[0] === 'string' ? args[0] : args[0] instanceof Request ? args[0].url : '';
      try {
        const response = await originalFetch.apply(this, args as [RequestInfo, RequestInit?]);
        const held = response.headers.get('x-batc-held-read');
        const originalJson = response.json.bind(response);
        response.json = async function() {
          const data = await originalJson();
          if (held) (window as any).__heldBodyTokens.push(held);
          return data;
        };
        return response;
      } catch (error) {
        ((window as any).__heldFetchRejections ||= []).push(requestUrl);
        throw error;
      }
    };
  });
}

async function syncHeldBody(page: Page, token: string) {
  await page.waitForFunction(expected => ((window as any).__heldBodyTokens || []).includes(expected), token);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve(undefined))));
}

async function syncHeldRejection(page: Page, path: string) {
  await page.waitForFunction(expected => ((window as any).__heldFetchRejections || []).some((url: string) => url.includes(expected)), path);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve(undefined))));
}

type HeldFailure = 'unavailable' | 'network';

interface Hold {
  path: string;
  started: () => boolean;
  token: () => string | null;
  release: () => void;
  fail: (kind: HeldFailure) => void;
}

interface FixtureExtras {catalog?: boolean, briefHistory?: boolean, phaseHistory?: boolean, roadmapHistory?: boolean}

function createFixture(locale: 'en' | 'zh-TW', features: Record<string, unknown>, review = false, extras: FixtureExtras = {}) {
  const ws = workspaceFixture(locale);
  const reviewState = review ? reviewFixture() : null;
  if (reviewState) {
    const detail = reviewState.state.reviews[0];
    const event: any = reviewState.addEvent(detail, 'candidate.record', {
      title: 'Inspect recovery coverage', problem: 'The fixed report leaves a coverage gap.',
      goal_relation: 'Preserve the selected membership release', expected_effect: 'Explain the missing evidence',
      options: ['Investigate', 'Defer'], hypotheses: ['One narrow check may help'], source_refs: [],
      rationale: 'Keep this attributed proposal separate from accepted scope', disposition: 'proposal', scope_adopted: false,
    });
    event.document_digest = 'd'.repeat(64);
    event.planning_source = {
      kind: 'maintenance_review_candidate', review_id: event.review_id, event_id: event.event_id,
      document_digest: event.document_digest, operation_id: event.operation_id,
    };
  }
  const caps = () => ({
    actor: 'owner', scopes: ['observe', 'operate', 'manage', 'start'], api_version: 1, contract_version: '2026-10-08',
    hosts: [], repositories: [],
    features: {pm_workspaces: {version: 1, ...features}},
    actions: ['pm.thread.create', 'pm.follow_up.prepare', 'pm.follow_up.analyze', 'pm.run.prepare', 'pm.plan.draft'].map(action => ({action, allowed: true})),
  });
  const reads: string[] = [];
  let serial = 0;
  let failBrief = false;
  let failEvidence = false;
  let eventCursor = 0;
  let eventWorkspace = destId;
  const slots: {path: string, method: string, promise: Promise<HeldFailure | null>, started: boolean, token: string | null, armed: boolean}[] = [];

  const hold = (path: string, method = 'GET'): Hold => {
    let settle = (_failure: HeldFailure | null) => {};
    const promise = new Promise<HeldFailure | null>(resolve => {settle = resolve;});
    const slot = {path, method, promise, started: false, token: null as string | null, armed: true};
    slots.push(slot);
    return {path, started: () => slot.started, token: () => slot.token, release: () => settle(null), fail: kind => settle(kind)};
  };

  const pmBody = (id: string, rest: string, url: URL, input: any) => {
    const head = {workspace_id: id, version: 1, accepted_revision_id: null};
    const page = {workspace_id: id, next_cursor: null};
    if (rest === '') return {workspace: workspaceRecord(id, locale)};
    if (rest === '/threads') return {version: 1, workspace_id: id, threads: [], next_cursor: null, runtime: {available: false, reason: 'manual_discussion_only'}};
    if (rest === `/threads/${threadId}`) return {version: 1, thread: threadRecord(id), capabilities: {write: true, decide: false}, messages: [], next_message_cursor: null};
    if (rest === '/runs') return {workspace_id: id, thread_id: url.searchParams.get('thread_id'), runs: [], next_cursor: null};
    if (rest === '/plans' || rest === '/plans/accepted') return {version: 1, workspace_id: id, head, revisions: [], plan: null, next_cursor: null};
    if (rest === '/plans/head') return {version: 1, head: {workspace_id: id, version: 1}};
    if (rest === '/capabilities') return {workspace_id: id, worktrees: [{repository: 'example/library', host: 'fixture-host', workspace_id: 'fixture-workspace', available: true}]};
    if (rest === '/lifecycle') return lifecycleDocument(id);
    if (rest === '/snapshots/latest') return {workspace_id: id, snapshot: null};
    if (rest === '/snapshots') return {workspace_id: id, snapshots: extras.briefHistory && id === sourceId
      ? [{snapshot_id: snapshotId, workspace_id: id, title: 'Earlier report', as_of: 1791705600, sequence: 1}] : [], next_cursor: null};
    if (rest === '/lifecycle/transitions' && extras.phaseHistory && id === sourceId) return {
      workspace_id: id, transitions: [{transition_id: transitionId, reason: 'Moved to planning', actor: 'owner'}], next_cursor: null};
    if (rest === '/roadmap/revisions' && extras.roadmapHistory && id === sourceId) return {
      workspace_id: id, revisions: [{revision_id: revisionId, kind: 'baseline', created_at: 1791705600, sequence: 1}], next_cursor: null};
    if (rest === '/follow-ups') {
      const row = intakeFor(id);
      const {document: _document, ...metadata} = row;
      return {version: 1, workspace_id: id, head_version: 1, intakes: [metadata], next_cursor: null};
    }
    if (rest === '/follow-ups/latest') return {version: 1, workspace_id: id, head_version: 1, intake: intakeFor(id)};
    if (rest === '/follow-up-assessments') return {workspace_id: id, assessments: [], next_cursor: null};
    if (rest === '/projects' || rest === '/work') return {...page, bindings: []};
    if (rest === '/meetings') return {...page, meetings: []};
    if (rest === '/sprints') return {...page, sprints: []};
    if (rest === '/governance') return {...page, records: []};
    if (rest === '/conversation-proposals') return {...page, proposals: []};
    return {...page, ...head, head};
  };

  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;
    const method = input.method || 'GET';
    reads.push(`${method} ${input.path}`);
    const slot = slots.find(item => item.armed && item.method === method && item.path === path);
    let heldToken: string | null = null;
    let failure: HeldFailure | null = null;
    if (slot) {
      slot.armed = false;
      slot.started = true;
      slot.token = String(++serial);
      heldToken = slot.token;
      failure = await slot.promise;
    }
    if (failure === 'network') return {abort: true};
    const finish = (result: any) => heldToken && result && typeof result === 'object'
      ? {...result, headers: {...(result.headers || {}), 'x-batc-held-read': heldToken}}
      : result;

    if (failure === 'unavailable') return finish({status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Read failed'}}});

    if (method === 'POST' && path === '/repository-previews') {
      const target = {...(input.body || {})};
      const sourceRef = target.source_ref;
      delete target.source_ref;
      return finish({status: 200, data: {preview: {target, source_ref: sourceRef, source_sha: 'a'.repeat(40), repository_id: 7, binding_digest: 'b'.repeat(64)}}});
    }
    if (method !== 'GET') return finish({status: 200, data: {operation: {operation_id: 'op_' + 'd'.repeat(32), status: 'succeeded'}}});
    if (path === '/capabilities') return finish({status: 200, data: caps()});
    if (path === '/bootstrap') return finish({status: 200, data: {capabilities: caps(), sync: {version: 1, server_id: 'retired-errors', principal_id: 'owner', checkpoint: checkpoint(0)}}});
    if (path === '/events') {
      const after = Number(url.searchParams.get('after') || 0);
      const ready = eventCursor > after;
      return finish({status: 200, data: {
        events: ready ? [{seq: eventCursor, resource_type: 'pm_workspace', resource_id: eventWorkspace, kind: 'pm.workspace.updated'}] : [],
        next_cursor: ready ? eventCursor : after, head_cursor: ready ? eventCursor : after, has_more: false,
        sync: {checkpoint: checkpoint(ready ? eventCursor : after)},
      }});
    }
    if (path === '/projects') return finish({status: 200, data: {projects: extras.catalog ? [{project_id: projectId, name: 'Library', version: 1}] : []}});
    if (path === '/sessions') return finish({status: 200, data: {sessions: [], next_cursor: null}});
    if (path === '/hosts') return finish({status: 200, data: {hosts: []}});
    if (path === '/operations') return finish({status: 200, data: {operations: [], next_before: null}});
    if (failBrief && path === `/pm/workspaces/${sourceId}/snapshots/latest`) {
      return finish({status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Brief refresh failed'}}});
    }
    if (failEvidence && path === `/pm/workspaces/${sourceId}/work`) {
      return finish({status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Evidence refresh failed'}}});
    }
    const owned = path.startsWith(`/pm/workspaces/${sourceId}`) ? sourceId : path.startsWith(`/pm/workspaces/${destId}`) ? destId : null;
    if (owned && reviewState && owned === sourceId) {
      try {return finish(await reviewState.dispatch(input));}
      catch (error: any) {if (!String(error?.message || '').startsWith('Unexpected')) throw error;}
    }
    if (path === '/pm/workspaces') return finish({status: 200, data: {workspaces: [workspaceRecord(sourceId, locale), workspaceRecord(destId, locale)]}});
    if (owned) return finish({status: 200, data: pmBody(owned, path.slice(`/pm/workspaces/${owned}`.length), url, input)});
    return finish({status: 200, data: {}});
  };

  return {ws, caps, dispatch, hold, reads, reviewState, failBrief: () => {failBrief = true;}, failEvidence: () => {failEvidence = true;}, emit: (id = destId) => {eventWorkspace = id; eventCursor += 1;}};
}

async function connectionPhase(page: Page, fixture: {reads: string[]}) {
  const title = await page.locator('#live').getAttribute('title') || '';
  const sync = await savedSync(page);
  const tail = fixture.reads.slice(-6).join(' | ');
  if (pausedTitle.test(title)) return `paused ${JSON.stringify(sync)} ${tail}`;
  if (pollingTitle.test(title) && sync?.token === 'retired-0') return 'settled';
  return `pending ${title} ${JSON.stringify(sync)} ${tail}`;
}

async function leaveForDest(page: Page, fixture: ReturnType<typeof createFixture>) {
  await page.evaluate(hash => {location.hash = hash;}, `#/pm/${destId}/discussions`);
  await expect.poll(() => connectionPhase(page, fixture)).toBe('settled');
  await expect(page.locator('#main h1').first()).toHaveText(fixture.caps().actor ? (await page.evaluate(() => navigator.language)).startsWith('zh') ? '討論' : 'Discussions' : 'Discussions');
  await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
}

async function releaseDuringDestRefresh(page: Page, fixture: ReturnType<typeof createFixture>, source: Hold) {
  const token = source.token();
  expect(token).toBeTruthy();
  const dest = fixture.hold(`/pm/workspaces/${destId}`);
  fixture.emit(destId);
  await expect.poll(() => dest.started()).toBe(true);
  await expect(page.locator('#live')).toHaveAttribute('title', waitingTitle);
  expect(await savedSync(page)).toEqual(checkpoint(0));
  source.release();
  await syncHeldBody(page, token!);
  dest.release();
  const seen = {value: 'pending'};
  await expect.poll(async () => {
    const title = await page.locator('#live').getAttribute('title') || '';
    if (pausedTitle.test(title)) seen.value = 'paused';
    else if (pollingTitle.test(title)) seen.value = 'polling';
    return seen.value;
  }).not.toBe('pending');
  expect(seen.value).toBe('polling');
  expect(await savedSync(page)).toEqual(checkpoint(1));
  await expect(page.locator('[data-api-error]')).toHaveCount(0);
}

async function rejectDuringDestRefresh(page: Page, fixture: ReturnType<typeof createFixture>, source: Hold, kind: HeldFailure) {
  const token = source.token();
  expect(token).toBeTruthy();
  const dest = fixture.hold(`/pm/workspaces/${destId}`);
  fixture.emit(destId);
  await expect.poll(() => dest.started()).toBe(true);
  await expect(page.locator('#live')).toHaveAttribute('title', waitingTitle);
  expect(await savedSync(page)).toEqual(checkpoint(0));
  source.fail(kind);
  if (kind === 'network') await syncHeldRejection(page, source.path);
  else await syncHeldBody(page, token!);
  dest.release();
  const seen = {value: 'pending'};
  await expect.poll(async () => {
    const title = await page.locator('#live').getAttribute('title') || '';
    if (pausedTitle.test(title)) seen.value = 'paused';
    else if (pollingTitle.test(title)) seen.value = 'polling';
    return seen.value;
  }).not.toBe('pending');
  expect(seen.value).toBe('polling');
  expect(await savedSync(page)).toEqual(checkpoint(1));
  await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
  await expect(page.locator('[data-api-error]')).toHaveCount(0);
}

async function openSource(page: Page, locale: 'en' | 'zh-TW', fixture: ReturnType<typeof createFixture>, hash: string) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value: language}), locale);
  await installHeldBodyProbe(page);
  await page.setViewportSize({width: 1440, height: 900});
  await mountAttention(page, false, {...fixture.ws.fixture, caps: fixture.caps}, fixture.dispatch);
  await page.goto(`/dashboard/?workbench=1${hash}`);
  return errors;
}

const openDetails = (section: string) => async (page: Page) => {
  await page.locator(`[data-pm-section="${section}"] > details > summary`).click();
};
// The first route starts event polling only after it returns, so a read held
// during that mount cannot overlap the next view's refresh. Settle, then hold.
const clickRefresh = async (page: Page) => {await page.locator('[data-pm-refresh]').click();};
// Requirements is already the open route. Its project-view button is hidden in the
// workbench sidebar, so the header overflow is the control that opens Evidence.
const openEvidence = async (page: Page) => {
  await page.locator('[data-pm-view-tools="requirements"]').click();
  await page.locator('[data-menu-key="ui_evidence"]').click();
};

const families: {name: string, features: Record<string, unknown>, hash: string, path: string, method?: string, prepare?: (page: Page) => Promise<void>, start?: (page: Page) => Promise<void>, review?: boolean, extras?: FixtureExtras, holdAlso?: string[]}[] = [
  {name: 'brief', features: {brief_snapshots: true}, hash: `#/pm/${sourceId}/overview`, path: `/pm/workspaces/${sourceId}/snapshots/latest`, start: clickRefresh},
  {name: 'lifecycle', features: {lifecycle: {version: 1, gated_actions: [], record_actions: []}}, hash: `#/pm/${sourceId}/discussions`, path: `/pm/workspaces/${sourceId}/lifecycle`, start: clickRefresh},
  {name: 'meetings', features: {meetings: true}, hash: `#/pm/${sourceId}/discussions`, path: `/pm/workspaces/${sourceId}/meetings`, start: openDetails('ui_meetings')},
  {name: 'follow-up', features: {follow_up_prepare: true}, hash: `#/pm/${sourceId}/issues`, path: `/pm/workspaces/${sourceId}/follow-ups`, start: openDetails('ui_follow_up')},
  {name: 'follow-up analysis', features: {follow_up_prepare: true, follow_up_analysis: true}, hash: `#/pm/${sourceId}/issues`, path: `/pm/workspaces/${sourceId}/follow-up-assessments`, start: openDetails('ui_follow_up')},
  {name: 'follow-up request', features: {follow_up_prepare: true, follow_up_analysis: true}, hash: `#/pm/${sourceId}/issues`, path: `/pm/workspaces/${sourceId}/capabilities`,
    prepare: async page => {await openDetails('ui_follow_up')(page); await expect(page.locator('[data-pm-follow-up-analyze]')).toBeAttached();},
    start: async page => {await page.locator('[data-pm-follow-up-analyze] > summary').click();}},
  {name: 'sprints', features: {sprints: true}, hash: `#/pm/${sourceId}/schedule`, path: `/pm/workspaces/${sourceId}/sprint-artifacts`, start: openDetails('ui_sprints')},
  {name: 'governance', features: {governance: true}, hash: `#/pm/${sourceId}/issues`, path: `/pm/workspaces/${sourceId}/governance`, start: clickRefresh},
  {name: 'roadmap', features: {roadmap: true}, hash: `#/pm/${sourceId}/schedule`, path: `/pm/workspaces/${sourceId}/roadmap`, start: clickRefresh},
  // Dispose increments this module's ticket before the next batch, so the late read
  // never calls the error box. This does not exercise the retired-view predicate.
  {name: 'plan proposals', features: {conversation_proposals: {version: 1}}, hash: `#/pm/${sourceId}/discussions/${threadId}`, path: `/pm/workspaces/${sourceId}/conversation-proposals`, start: clickRefresh},
  {name: 'review candidate', features: {maintenance_review: {version: 1, planning_candidate_source: {version: 1}}, maintenance_checks: {version: 1, weekly_review_execution: true}}, hash: `#/pm/${sourceId}/issues`, path: `/pm/workspaces/${sourceId}/threads`, review: true,
    prepare: async page => {await openDetails('ui_maintenance_review')(page); await expect(page.locator('[data-review-candidate-import]')).toBeAttached();},
    start: async page => {await page.locator('[data-review-candidate-import] > summary').click();}},
  {name: 'worktree choice', features: {plan_drafting: true, context_packages: true}, hash: `#/pm/${sourceId}/discussions/${threadId}`, path: '/repository-previews', method: 'POST',
    prepare: async page => {
      await openThreadTab(page, 'context');
      await page.locator('[data-pm-prepare-context] > summary').click();
      await page.locator('[data-pm-worktree-choice] > summary').click();
      const target = {repository: 'example/library', host: 'fixture-host', workspace_id: 'fixture-workspace'};
      await page.getByRole('combobox', {name: /Save in a PM worktree|保存到 PM 工作目錄/}).selectOption(JSON.stringify(target));
    },
    start: async page => {await page.getByRole('button', {name: /Preview published revision|預覽已發布版本/}).click();}},
  {name: 'evidence project', features: {work_evidence: true}, hash: `#/pm/${sourceId}/requirements`, path: `/projects/${projectId}`, extras: {catalog: true},
    prepare: async page => {
      await openEvidence(page);
      await expect(page.getByRole('combobox', {name: /Connector project|Connector 專案/})).toContainText('Library');
    },
    start: async page => {await page.getByRole('combobox', {name: /Connector project|Connector 專案/}).selectOption(projectId);}},
  {name: 'evidence refresh', features: {work_evidence: true}, hash: `#/pm/${sourceId}/requirements`, path: `/pm/workspaces/${sourceId}/work`,
    prepare: async page => {
      await openEvidence(page);
      await expect(page.locator('[data-pm-evidence]')).toContainText(/No projects yet|尚無專案/);
    },
    start: clickRefresh},
  {name: 'brief history', features: {brief_snapshots: true}, hash: `#/pm/${sourceId}/overview`, path: `/pm/workspaces/${sourceId}/snapshots/${snapshotId}`, extras: {briefHistory: true},
    prepare: async page => {
      await openReportTask(page, 'versions');
      await expect(page.getByRole('button', {name: /Earlier report/})).toBeVisible();
    },
    start: async page => {await page.getByRole('button', {name: /Earlier report/}).click();}},
  {name: 'lifecycle history', features: {lifecycle: {version: 1, gated_actions: [], record_actions: []}}, hash: `#/pm/${sourceId}/overview`, path: `/pm/workspaces/${sourceId}/lifecycle/transitions/${transitionId}`, extras: {phaseHistory: true},
    prepare: async page => {
      await openPmPanel(page, 'ui_change_phase');
      await page.locator('[data-pm-lifecycle-history] > summary').click();
      await expect(page.locator('[data-pm-lifecycle-history] details > summary')).toContainText('Moved to planning');
    },
    start: async page => {await page.locator('[data-pm-lifecycle-history] details > summary').click();}},
  // A sibling source page can retire this serial when it finishes first, which
  // hides the snapshots read. Hold the other three so this read still owns the
  // serial when it finishes in the next batch.
  {name: 'follow-up prepare', features: {follow_up_prepare: true}, hash: `#/pm/${sourceId}/issues`, path: `/pm/workspaces/${sourceId}/snapshots`,
    holdAlso: [`/pm/workspaces/${sourceId}/threads`, `/pm/workspaces/${sourceId}/governance`, `/pm/workspaces/${sourceId}/meetings`],
    prepare: async page => {
      await openDetails('ui_follow_up')(page);
      await expect(page.locator('[data-pm-follow-up]')).toContainText(/Preparation 1|第 1 份討論準備/);
    },
    start: async page => {await page.locator('[data-pm-follow-up-prepare]').click();}},
  {name: 'follow-up thread', features: {follow_up_prepare: true, follow_up_analysis: true}, hash: `#/pm/${sourceId}/issues`, path: `/pm/workspaces/${sourceId}/threads/${threadId}`,
    prepare: async page => {
      await openDetails('ui_follow_up')(page);
      await expect(page.locator('[data-pm-follow-up-analyze]')).toBeVisible();
    },
    start: async page => {await page.locator('[data-pm-follow-up-analyze] > summary').click();}},
  {name: 'review destination', features: {maintenance_review: {version: 1, planning_candidate_source: {version: 1}}, maintenance_checks: {version: 1, weekly_review_execution: true}}, hash: `#/pm/${sourceId}/issues`, path: `/pm/workspaces/${sourceId}/threads/${ideaThreadId}`, review: true,
    prepare: async page => {
      await openDetails('ui_maintenance_review')(page);
      await page.locator('[data-review-candidate-import] > summary').click();
      await expect(page.getByRole('combobox', {name: /Choose an existing Idea discussion|選擇既有構想討論/})).toContainText('Possible recovery improvements');
    },
    start: async page => {await page.getByRole('combobox', {name: /Choose an existing Idea discussion|選擇既有構想討論/}).selectOption(ideaThreadId);}},
  {name: 'roadmap revision', features: {roadmap: true}, hash: `#/pm/${sourceId}/schedule`, path: `/pm/workspaces/${sourceId}/roadmap/revisions/${revisionId}`, extras: {roadmapHistory: true},
    prepare: async page => {
      await page.locator('[data-pm-roadmap-history] > summary').click();
      await expect(page.locator('[data-pm-roadmap-history] details > summary')).toContainText(/Scheduling baseline|里程碑基準/);
    },
    start: async page => {await page.locator('[data-pm-roadmap-history] details > summary').click();}},
];

// One primary refresh and one sibling detail stand in for the same catch shape.
const failedReads: typeof families = [
  families.find(family => family.name === 'evidence project')!,
  families.find(family => family.name === 'evidence refresh')!,
  families.find(family => family.name === 'brief')!,
  families.find(family => family.name === 'brief history')!,
];

test.describe('a left PM view does not fail the next view refresh', () => {
  for (const locale of ['en', 'zh-TW'] as const) {
    for (const family of families) {
      test(`${family.name} late read does not fail the next view (${locale})`, async ({page}) => {
        const fixture = createFixture(locale, family.features, family.review, family.extras);
        await openSource(page, locale, fixture, family.hash);
        await expect.poll(() => connectionPhase(page, fixture)).toBe('settled');
        if (family.prepare) await family.prepare(page);
        const held = fixture.hold(family.path, family.method);
        for (const path of family.holdAlso || []) fixture.hold(path);
        if (family.start) await family.start(page);
        await expect.poll(() => held.started()).toBe(true);
        await leaveForDest(page, fixture);
        await releaseDuringDestRefresh(page, fixture, held);
      });
    }

    for (const kind of ['unavailable', 'network'] as const) {
      for (const read of failedReads) {
        test(`${read.name} late ${kind} does not fail the next view (${locale})`, async ({page}) => {
          const fixture = createFixture(locale, read.features, read.review, read.extras);
          await openSource(page, locale, fixture, read.hash);
          await expect.poll(() => connectionPhase(page, fixture)).toBe('settled');
          if (read.prepare) await read.prepare(page);
          const held = fixture.hold(read.path, read.method);
          if (read.start) await read.start(page);
          await expect.poll(() => held.started()).toBe(true);
          await leaveForDest(page, fixture);
          await rejectDuringDestRefresh(page, fixture, held, kind);
        });
      }
    }

    test(`a real error in the current brief still stops the refresh (${locale})`, async ({page}) => {
      const fixture = createFixture(locale, {brief_snapshots: true});
      const errors = await openSource(page, locale, fixture, `#/pm/${sourceId}/overview`);
      await expect.poll(() => connectionPhase(page, fixture)).toBe('settled');
      fixture.failBrief();
      fixture.emit(sourceId);
      const seen = {value: 'pending'};
      await expect.poll(async () => {
        const title = await page.locator('#live').getAttribute('title') || '';
        if (pausedTitle.test(title)) seen.value = 'paused';
        else if (waitingTitle.test(title)) seen.value = 'waiting';
        else if (pollingTitle.test(title)) seen.value = 'polling';
        return seen.value;
      }).toBe('paused');
      await expect(page.locator('[data-api-error]')).not.toHaveCount(0);
      expect(await savedSync(page)).toEqual(checkpoint(0));
      expect(errors).toEqual([]);
    });

    test(`a real evidence refresh error still stops the refresh (${locale})`, async ({page}) => {
      const fixture = createFixture(locale, {work_evidence: true});
      const errors = await openSource(page, locale, fixture, `#/pm/${sourceId}/requirements`);
      await expect.poll(() => connectionPhase(page, fixture)).toBe('settled');
      await openEvidence(page);
      await expect(page.locator('[data-pm-evidence]')).toContainText(/No projects yet|尚無專案/);
      fixture.failEvidence();
      fixture.emit(sourceId);
      const seen = {value: 'pending'};
      await expect.poll(async () => {
        const title = await page.locator('#live').getAttribute('title') || '';
        if (pausedTitle.test(title)) seen.value = 'paused';
        else if (waitingTitle.test(title)) seen.value = 'waiting';
        else if (pollingTitle.test(title)) seen.value = 'polling';
        return seen.value;
      }).toBe('paused');
      await expect(page.locator('[data-pm-evidence]')).toContainText(locale === 'zh-TW' ? '無法取得專案' : 'Projects unavailable');
      expect(await savedSync(page)).toEqual(checkpoint(0));
      expect(errors).toEqual([]);
    });

    test(`a lost network in the current brief still stops the refresh (${locale})`, async ({page}) => {
      const fixture = createFixture(locale, {brief_snapshots: true});
      const errors = await openSource(page, locale, fixture, `#/pm/${sourceId}/overview`);
      await expect.poll(() => connectionPhase(page, fixture)).toBe('settled');
      const held = fixture.hold(`/pm/workspaces/${sourceId}/snapshots/latest`);
      fixture.emit(sourceId);
      await expect.poll(() => held.started()).toBe(true);
      held.fail('network');
      await syncHeldRejection(page, held.path);
      const seen = {value: 'pending'};
      await expect.poll(async () => {
        const title = await page.locator('#live').getAttribute('title') || '';
        if (pausedTitle.test(title)) seen.value = 'paused';
        else if (pollingTitle.test(title)) seen.value = 'polling';
        return seen.value;
      }).toBe('paused');
      await expect(page.locator('[data-api-error]')).not.toHaveCount(0);
      expect(await savedSync(page)).toEqual(checkpoint(0));
      expect(errors).toEqual([]);
    });

    test(`a lost network in the current evidence refresh still stops the refresh (${locale})`, async ({page}) => {
      const fixture = createFixture(locale, {work_evidence: true});
      const errors = await openSource(page, locale, fixture, `#/pm/${sourceId}/requirements`);
      await expect.poll(() => connectionPhase(page, fixture)).toBe('settled');
      await openEvidence(page);
      await expect(page.locator('[data-pm-evidence]')).toContainText(/No projects yet|尚無專案/);
      const held = fixture.hold(`/pm/workspaces/${sourceId}/work`);
      fixture.emit(sourceId);
      await expect.poll(() => held.started()).toBe(true);
      held.fail('network');
      await syncHeldRejection(page, held.path);
      const seen = {value: 'pending'};
      await expect.poll(async () => {
        const title = await page.locator('#live').getAttribute('title') || '';
        if (pausedTitle.test(title)) seen.value = 'paused';
        else if (pollingTitle.test(title)) seen.value = 'polling';
        return seen.value;
      }).toBe('paused');
      await expect(page.locator('[data-pm-evidence]')).toContainText(locale === 'zh-TW' ? '無法取得專案' : 'Projects unavailable');
      expect(await savedSync(page)).toEqual(checkpoint(0));
      expect(errors).toEqual([]);
    });
  }
});
