import {expect, type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {stripTypeScriptTypes} from 'node:module';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';

export const workspaceId = 'ws_' + '1'.repeat(32), snapshotId = 'pmsnap_' + '2'.repeat(32), planId = 'pmrev_' + '3'.repeat(32);
export const proposedId = 'pmrev_' + '4'.repeat(32), assumptionId = 'pmi_' + '5'.repeat(32), evidenceId = 'pme_' + '6'.repeat(32), adoptionId = 'op_' + 'f'.repeat(32);
export const action = (page: Page, name: string) => page.locator(`[data-pm-action="pm.governance.${name}"]`);
const names = ['record', 'revise', 'decide', 'adoption.link'].map(name => 'pm.governance.' + name);
export function governanceFixture() {
  const snapshot = {workspace_id: workspaceId, snapshot_id: snapshotId, title: 'Account recovery before release', snapshot_digest: 'a'.repeat(64),
    as_of: '2026-10-11T12:00:00Z', journal_cursor: 50, visibility: {filtered: false}, coverage: {evidence: 'partial'},
    baseline: {document_revision: planId, document_digest: 'b'.repeat(64), scope_revision: 1, document: {
      title: 'Reliable membership access', selected_inputs: [{input_id: assumptionId, value: 'Password reset depends on a working mail route', status: 'agent_assumption'}],
      requirements: [{id: 'account.recovery', text: 'A member can recover a forgotten password'}]}},
    evidence: [{evidence_id: evidenceId, source_kind: 'observed_verification', source_id: 'verification_1', fact_digest: 'c'.repeat(64), summary: 'Delivery check has no current result'}],
    work: [{binding: {binding_id: 'pmwb_' + '7'.repeat(32), work_item_id: 'wi_example', plan_revision_id: planId, requirement_ids: ['account.recovery']}}]};
  const context = {snapshot_ref: {snapshot_id: snapshotId, snapshot_digest: snapshot.snapshot_digest, as_of: snapshot.as_of, journal_cursor: 50},
    plan_revision_id: planId, scope_revision: 1, plan_document_digest: 'b'.repeat(64), coverage: snapshot.coverage};
  const state: any = {actor: 'owner', role: 'owner', manage: true, admin: false, workspaceVersion: 4, planVersion: 2,
    records: [], history: {}, snapshots: [snapshot], reads: [], writes: [], ops: new Map(), lost: false, mismatch: false, failRead: false,
    wrongScope: false, badCursor: false, pageSize: 20, hiddenFirst: false, historyPageSize: 20, snapshotsPaged: false,
    snapshotFailed: false, adoptionMismatch: false, defer: null, rejection: null, hiddenRecords: [], hiddenSnapshots: [], workspaceDenied: false};
  function sources(refs: any[], snap = snapshot) {
    const values = [...snap.baseline.document.selected_inputs.map(row => ({kind: 'plan_input', id: row.input_id, source: row})),
      ...snap.baseline.document.requirements.map(row => ({kind: 'requirement', id: row.id, source: row})),
      ...snap.evidence.map(row => ({kind: 'evidence', id: row.evidence_id, source: row})),
      ...snap.work.map(row => ({kind: 'work_binding', id: row.binding.binding_id, source: row.binding}))];
    return refs.map(ref => ({...values.find(row => row.kind === ref.kind && row.id === ref.id), identity: ref.kind + ':' + ref.id}));
  }
  const document = (fields: any = {}) => ({title: 'Mail delivery remains unverified', description: 'Password recovery needs a verified mail route before release.',
    owner_actor: 'owner', affected_requirement_ids: ['account.recovery'], trigger: 'Before release', response: null, review_at: null, status: 'open', dimension: null,
    alternatives: [], proposed_revision: null, sources: sources([{kind: 'plan_input', id: assumptionId}]), rationale: 'Keep this uncertainty visible',
    assessment_basis: 'attributed_management_finding', verification_status: 'unassessed', decision: null, adoption: null, disambiguation: null, scope_applied: false, execution_authorized: false, ...fields});
  const add = (kind = 'risk', fields: any = {}) => {
    const n = state.records.length + 1, rid = 'pmgov_' + n.toString(16).padStart(32, '0'), revision_id = 'pmgrev_' + n.toString(16).padStart(32, '0');
    const entry = {record: {workspace_id: workspaceId, record_id: rid, revision_id, sequence: n, version: 1, kind, root_cause_key: 'cause-' + n,
      context: structuredClone(context), context_digest: 'd'.repeat(64), created_by: 'owner', operation_id: 'op_' + n.toString(16).padStart(32, '0')},
      revision: {record_id: rid, revision_id, sequence: 1, version: 1, actor: 'owner', created_at: 1791720000,
        previous_revision_id: null, operation_id: 'op_' + n.toString(16).padStart(32, '0'), action: 'pm.governance.record', document_digest: 'e'.repeat(64), document: document(fields)}};
    state.records.push(entry); state.history[rid] = [structuredClone(entry.revision)]; return entry;
  };
  add();
  add('change', {title: 'Use the existing mail provider', description: 'A smaller change can meet the recovery goal.', sources: sources([{kind: 'requirement', id: 'account.recovery'}]),
    proposed_revision: {revision_id: proposedId, document_digest: 'f'.repeat(64), scope_digest: '0'.repeat(64)}});
  const config = () => ({caps: {actor: state.actor, scopes: ['observe', ...(state.manage ? ['manage'] : []), ...(state.admin ? ['admin'] : [])],
    actions: names.map(action => ({action, allowed: state.manage}))}, workspace: {workspace_id: workspaceId, version: state.workspaceVersion,
      membership: {actor: state.actor, role: state.role}, members: [{actor: 'owner', role: state.actor === 'owner' ? state.role : 'owner'},
        {actor: 'developer', role: state.actor === 'developer' ? state.role : 'contributor'}, {actor: 'reader', role: 'reader'}]}});
  const response = (data: any, status = 200) => ({data: structuredClone(data), status});
  const error = (code: string, message: string, status = 403) => response({error: {code, message}}, status);
  const paginate = (rows: any[], after: number, limit: number, key = 'sequence') => {
    const choices = rows.filter(row => row[key] > after), items = choices.slice(0, limit);
    return {items, next: state.badCursor ? after : choices.length > limit ? items.at(-1)[key] : null};
  };
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), base = '/pm/workspaces/' + workspaceId, p = url.pathname;
    if (input.method === 'GET') {
      state.reads.push(input.path);
      if (p.startsWith('/operations/')) {
        if (p.endsWith(adoptionId)) return response({operation: {operation_id: adoptionId, action: 'pm.plan.adopt', status: 'succeeded',
          target: {workspace_id: workspaceId, revision_id: state.adoptionMismatch ? planId : proposedId}, result: {previous_revision_id: planId}}});
        return response({operation: [...state.ops.values()].find((row: any) => p.endsWith(row.operation_id))});
      }
      if (state.defer && p.endsWith('/governance')) {const promise = state.defer; state.defer = null; await promise;}
      if (state.failRead) return error('FORBIDDEN', 'Workspace access removed');
      if (p === base) return state.workspaceDenied ? error('FORBIDDEN', 'Workspace access removed') : response({workspace: config().workspace});
      if (p === base + '/governance') {
        const after = Number(url.searchParams.get('after') || 0);
        if (state.hiddenFirst && after === 0) return response({workspace_id: workspaceId, records: [], next_cursor: state.badCursor ? 0 : 1});
        const rows = state.records.map((row: any) => ({...row, sequence: row.record.sequence}));
        const page = paginate(rows, after, state.pageSize);
        return response({workspace_id: state.wrongScope ? 'ws_wrong' : workspaceId, records: page.items.filter((row: any) => !state.hiddenRecords.includes(row.record.record_id)), next_cursor: page.next, coverage: {byte_limited: page.next != null}});
      }
      if (p === base + '/plans/accepted') return response({head: {workspace_id: workspaceId, version: state.planVersion, accepted_revision_id: planId}, plan: {workspace_id: workspaceId, revision_id: planId}});
      if (p.startsWith(base + '/plans/')) return response({plan: {workspace_id: workspaceId, revision_id: p.split('/').at(-1), document_digest: 'f'.repeat(64), document: {title: 'Use the existing mail provider'}}});
      if (p === base + '/snapshots') {
        const after = Number(url.searchParams.get('after') || 0);
        if (state.snapshotsPaged && after === 0) return response({workspace_id: workspaceId, snapshots: [], next_cursor: state.badCursor ? 0 : 10});
        return response({workspace_id: workspaceId, snapshots: state.snapshots.map((s: any) => ({workspace_id: workspaceId, snapshot_id: s.snapshot_id, title: s.title})), next_cursor: null});
      }
      if (p.startsWith(base + '/snapshots/')) {
        if (state.hiddenSnapshots.includes(p.split('/').at(-1))) return error('PM_SNAPSHOT_SOURCE_UNAVAILABLE', 'Original source access removed', 404);
        if (state.snapshotFailed) return state.snapshotFailed === 'large'
          ? error('PM_GOVERNANCE_READ_TOO_LARGE', 'One complete source exceeds the read limit', 413)
          : error('PM_SNAPSHOT_SOURCE_UNAVAILABLE', 'Original source access removed', 404);
        const snapshot = state.snapshots.find((row: any) => p.endsWith(row.snapshot_id));
        return snapshot ? response({snapshot}) : error('NOT_FOUND', 'Snapshot unavailable', 404);
      }
      const match = p.match(/\/governance\/(pmgov_[a-f0-9]+)(\/history)?$/);
      if (match) {
        const row = state.records.find((row: any) => row.record.record_id === match[1]);
        if (!row || state.hiddenRecords.includes(match[1])) return error('PM_GOVERNANCE_SOURCE_UNAVAILABLE', 'Finding unavailable', 404);
        if (match[2]) {
          const after = Number(url.searchParams.get('after') || 0), page = paginate(state.history[match[1]], after, state.historyPageSize);
          return response({workspace_id: workspaceId, record_id: match[1], context: row.record.context, revisions: page.items, next_cursor: page.next});
        }
        return response({workspace_id: workspaceId, ...row, execution_authorized: false});
      }
      throw Error('Unexpected read ' + input.path);
    }
    if (input.method !== 'POST' || p !== '/operations') throw Error('Unexpected write ' + input.path);
    state.writes.push(structuredClone(input)); let op = state.ops.get(input.idempotency_key);
    if (!op) {
      if (state.rejection) return error(state.rejection, 'The original sources changed', 409);
      const {action, target, params} = input.body;
      let entry = state.records.find((row: any) => row.record.record_id === target.record_id), deduplicated = false;
      if (action === 'pm.governance.record') {
        entry = state.records.find((row: any) => row.record.kind === params.kind && row.record.root_cause_key === params.root_cause_key);
        if (entry) deduplicated = true;
        else {
          entry = add(params.kind); entry.record.root_cause_key = params.root_cause_key;
          entry.revision.document = document({...params, sources: sources(params.source_refs), proposed_revision: params.proposed_revision_id ? {revision_id: params.proposed_revision_id, document_digest: 'f'.repeat(64)} : null});
          state.history[entry.record.record_id] = [structuredClone(entry.revision)];
        }
      } else {
        if (input.body.preconditions.expected_version !== entry.record.version) return error('VERSION_CONFLICT', 'Finding changed', 409);
        const prev = entry.revision, revisionId = 'pmgrev_' + (state.ops.size + 10).toString(16).padStart(32, '0');
        const d = structuredClone(prev.document);
        if (action === 'pm.governance.revise') Object.assign(d, params, {sources: sources(params.source_refs), decision: null, adoption: null, scope_applied: false});
        if (action === 'pm.governance.decide') Object.assign(d, {decision: {...params, recorded_by: state.actor, proposal_revision_id: prev.revision_id}, adoption: null, scope_applied: false});
        if (action === 'pm.governance.adoption.link') Object.assign(d, {adoption: {operation_id: params.adoption_operation_id, revision_id: proposedId, adopted_by: 'owner'}, scope_applied: true});
        entry.record.version++; entry.record.revision_id = revisionId;
        entry.revision = {...prev, document: d, action, version: entry.record.version, sequence: state.history[entry.record.record_id].length + 1,
          revision_id: revisionId, previous_revision_id: prev.revision_id, actor: state.actor};
        state.history[entry.record.record_id].push(structuredClone(entry.revision));
      }
      op = {...input.body, actor: state.actor, idempotency_key: input.idempotency_key, operation_id: 'op_' + (state.ops.size + 10).toString(16).padStart(32, '0'), status: 'succeeded',
        result: {workspace_id: workspaceId, record_id: entry.record.record_id, revision_id: entry.record.revision_id, version: entry.record.version, deduplicated, input_applied: !deduplicated, execution_authorized: false}};
      state.ops.set(input.idempotency_key, op);
    }
    if (state.lost) {state.lost = false; return error('LOST_REPLY', 'Reply unavailable', 503);}
    return response({operation: state.mismatch ? {...op, actor: 'another-actor'} : op});
  };
  return {state, config, dispatch, add};
}

export async function mount(page: Page, fixture: ReturnType<typeof governanceFixture>, locale = 'en', native = false) {
  await page.exposeFunction('governanceFixtureRequest', fixture.dispatch); await page.exposeFunction('governanceFixtureConfig', fixture.config);
  if (native) await page.addInitScript(() => Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {
    invoke: async (command: string, args: any) => {
      if (command !== 'connector_request') throw Error('Unexpected native command');
      return (window as any).governanceFixtureRequest(args.input);
    }
  }}));
  await page.route('**/api/v1/**', async route => {
    if (native) throw Error('Native governance must use IPC');
    const request = route.request(), url = new URL(request.url());
    const result = await fixture.dispatch({method: request.method(), path: url.pathname.slice(7) + url.search,
      body: request.postData() ? request.postDataJSON() : null, idempotency_key: request.headers()['idempotency-key']});
    await route.fulfill({status: result.status, json: result.data});
  });
  await page.route('**/__governance_src/**', async route => {
    const file = new URL(route.request().url()).pathname.slice('/__governance_src/'.length);
    let source: string;
    if (file === 'transport.js') source = stripTypeScriptTypes(await readFile(new URL('../src/transport/index.ts', import.meta.url), 'utf8'))
      .replace('@tauri-apps/api/core', './tauri-core.js');
    else if (file === 'tauri-core.js') source = await readFile(new URL('../node_modules/@tauri-apps/api/core.js', import.meta.url), 'utf8');
    else if (file === 'external/tslib/tslib.es6.js') source = await readFile(new URL('../node_modules/@tauri-apps/api/external/tslib/tslib.es6.js', import.meta.url), 'utf8');
    else source = await readFile(new URL('../src/' + path.basename(file), import.meta.url), 'utf8');
    if (file === 'pm-workspace.js') source = source.replace('function durableForm(', 'export function durableForm(');
    await route.fulfill({contentType: 'text/javascript', body: source});
  });
  await unavailableBriefTransport(page,'__governance_src');
  await page.route('**/__governance_harness', route => route.fulfill({contentType: 'text/html', body:
    '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><div class="pm-shell"><aside><h2>PM workspace</h2><p>Membership service</p></aside><main class="pm-main" id="mount"></main></div>'}));
  await page.goto('/__governance_harness'); await boot(page, locale);
}
export async function boot(page: Page, locale = 'en') {
  await page.evaluate(async locale => {
    const {workspaceGovernance} = await import('/__governance_src/pm-governance.js'), {durableForm} = await import('/__governance_src/pm-workspace.js');
    const {connectorRequest} = await import('/__governance_src/transport.js');
    const {pmGovernanceStrings} = await import('/__governance_src/pm-governance-strings.js'), {pmStrings} = await import('/__governance_src/pm-strings.js');
    const strings = {...pmStrings[locale], ...pmGovernanceStrings[locale]}, t = (key: string) => strings[key] || key;
    const h = (tag: string, attrs: any = {}, ...children: any[]) => {
      const node = document.createElement(tag); for (const [key, value] of Object.entries(attrs || {})) {
        if (value === null || value === undefined || value === false) continue;
        if (key.startsWith('on')) node.addEventListener(key.slice(2), value as any); else node.setAttribute(key, value === true ? '' : String(value));
      }
      for (const child of children.flat()) if (child !== false && child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      return node;
    };
    const win = window as any; let config = await win.governanceFixtureConfig();
    const api = async (method: string, path: string, body: any, key: string) => {
      const response = await connectorRequest(method, path, body, key, 'fixture-token');
      if (response.status >= 400) throw Object.assign(Error(response.data.error.message), {status: response.status, code: response.data.error.code}); return response.data;
    };
    const input = (key: string, {multiline = false, max = 4000, required = true, value = ''}: any = {}) => {
      const node = h(multiline ? 'textarea' : 'input', {'aria-label': t(key), maxlength: max, required}) as HTMLInputElement; node.value = value; return node;
    }, labelled = (key: string, field: any) => h('label', {}, t(key), field), errorBox = (error: any) => h('p', {class: 'error'}, error.message);
    const caps = () => config.caps, alive = () => {}, form = (options: any) => durableForm({h, t, api, caps, guard: alive,
      namespace: 'governance-fixture.' + config.caps.actor, errorBox, ...options});
    const ui = workspaceGovernance({h, t, api, alive, workspaceId: config.workspace.workspace_id, workspace: () => config.workspace,
      permitted: (decide: boolean) => config.caps.scopes.includes('manage') && (config.caps.scopes.includes('admin') || (decide ? config.workspace.membership.role === 'owner' : ['owner', 'contributor'].includes(config.workspace.membership.role))),
      caps, form, input, labelled, errorBox});
    document.getElementById('mount')!.append(ui.element); win.refreshGovernance = async () => {config = await win.governanceFixtureConfig(); await ui.refresh();}; await ui.refresh();
  }, locale);
  await expect(page.locator('[data-pm-governance]')).toBeVisible();
}
export async function openAction(page: Page, name: string) {
  await action(page, name).evaluate(node => {for (let parent = node.parentElement; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') (parent as HTMLDetailsElement).open = true;});
}
export async function reason(page: Page, name: string, value = 'Preserve the fixed evidence and make this uncertainty explicit') {
  await openAction(page, name); await action(page, name).getByLabel('Reason for this record', {exact: true}).fill(value);
}
