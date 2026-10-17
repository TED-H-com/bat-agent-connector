import {expect, type Page} from '@playwright/test';
import {workspaceFixture, mountWorkspace} from './workspace-fixture.ts';

export const WS = 'ws_' + 'a'.repeat(32);
export const WI = 'wi_' + String(1).padStart(20, '0');
export const ATTEMPT = 'att_' + 'b'.repeat(32);

const QUEUED_LABELS = {
  state: {en: 'Queued', 'zh-TW': '排隊中'},
  detail: {
    en: 'Waiting for the current turn to end.',
    'zh-TW': '正在等待目前回合結束。',
  },
};

export function followUpFixture(locale = 'en', options: Record<string, any> = {}) {
  const base = workspaceFixture(locale);
  base.data.linkedItem = true;
  base.data.session.agent_kind = options.agent || 'claude';
  if (options.provenance) base.data.session.provenance = options.provenance;
  if (options.api_access) base.data.session.api_access = options.api_access;
  if (options.pending) base.data.session.pending = options.pending;
  if (options.title) base.fixture.data.items[0].title = options.title;
  base.fixture.data.items[0].work_item_id = WI;
  const zh = locale.startsWith('zh');
  const name = zh ? '搜尋工作區' : 'Search workspace';
  let eventSeq = 0;
  const follow = {
    feature: options.feature === undefined ? {version: 1, available: true} : options.feature,
    pm: options.pm !== false,
    sendAllowed: options.sendAllowed !== false,
    operate: options.operate !== false,
    actor: 'owner',
    items: [] as any[],
    hasMore: false,
    cursor: null as string | null,
    pages: new Map<string, any>(),
    filtered: false,
    latest: options.latest === undefined ? ATTEMPT : options.latest,
    result: options.result || 'offer',
    resultStatus: options.resultStatus || 200,
    inputsStatus: 200,
    malformed: false,
    failPost: null as null | {status: number, code: string, admission_refused?: boolean},
    holdPost: null as null | Promise<void>,
    postStarted: false,
    holdFiltered: false,
    holdConsumed: false,
    releaseHold: () => {},
    lateReturned: false,
    hold: {inputs: false, result: false},
    held: [] as Array<() => void>,
    releaseHeld() {const waiting = follow.held.splice(0); for (const resolve of waiting) resolve();},
    receiptMode: 'normal' as 'normal' | 'missing' | 'mismatch',
    opStatus: '' as string,
    receiptState: '' as string,
    ops: new Map<string, any>(),
    seq: 0,
    reads: [] as string[],
    posts: [] as any[],
    errors: [] as string[],
    native: options.native === true,
    cancelMode: 'ok' as 'ok' | 'denied',
  };
  const caps = () => {
    const features: Record<string, any> = {...base.caps().features};
    if (follow.pm) features.pm_workspaces = {version: 1, ...(follow.feature ? {work_follow_up: follow.feature} : {})};
    return {
      ...base.caps(),
      actor: follow.actor,
      scopes: ['observe', ...(follow.operate ? ['operate'] : []), 'manage'],
      features,
      hosts: [{host: 'demo', writes: true}],
      actions: [
        {action: 'session.labels.set', allowed: true},
        {action: 'session.send', allowed: follow.sendAllowed},
        {action: 'session.interrupt', allowed: true},
      ],
    };
  };
  const listBody = (rows: any[]) => ({
    workspace_id: WS, work_item_id: WI,
    attempt_id: follow.latest || null,
    latest_attempt_id: follow.latest || null,
    items: rows, next_cursor: follow.cursor, has_more: follow.hasMore === true && Boolean(follow.cursor),
    visibility_filtered: follow.filtered === true,
  });
  const resultBody = () => {
    const head = {contract: 'pm-work-attempt-result/v1', workspace_id: WS, attempt_id: follow.latest || ATTEMPT, identity: {work_item_id: WI}};
    if (follow.result === 'bad') return {contract: 'other'};
    if (follow.result === 'codex') return {...head, next_allowed_actions: [], blocked_actions: [{action: 'session.send', reason: 'codex_uncorrelated_turn_boundary'}]};
    if (follow.result === 'not_controller') return {...head, next_allowed_actions: [], blocked_actions: [{action: 'session.send', reason: 'not_controller'}]};
    if (follow.result === 'missing_scope') return {...head, next_allowed_actions: [], blocked_actions: [{action: 'session.send', reason: 'missing_scope'}]};
    if (follow.result === 'none') return {...head, next_allowed_actions: [], blocked_actions: []};
    return {...head, blocked_actions: [], next_allowed_actions: [{
      kind: 'action', action: 'session.send', allowed: true,
      target: {workspace_id: WS, work_item_id: WI},
      allowed_schedules: ['after_current', 'now'],
    }]};
  };
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    follow.reads.push(`${input.method} ${input.path}`);
    if (path === '/capabilities') return {status: 200, data: caps()};
    if (path === '/bootstrap') return {status: 200, data: {capabilities: caps(), sync: {version: 1, server_id: 'workspace-fixture', principal_id: base.fixture.data.principal, checkpoint: {cursor: 0, token: 'w-0'}}}};
    if (input.method === 'GET' && path === '/pm/workspaces') return {status: 200, data: {workspaces: [{workspace_id: WS, name}]}};
    if (input.method === 'GET' && path === `/pm/workspaces/${WS}`) return {status: 200, data: {workspace: {workspace_id: WS, name, version: 1, membership: {role: 'owner', actor: follow.actor}}}};
    if (input.method === 'GET' && path === `/pm/workspaces/${WS}/threads`) return {status: 200, data: {workspace_id: WS, threads: [], next_cursor: null}};
    const pause = async (kind: 'inputs' | 'result') => {
      if (!follow.hold[kind]) return;
      await new Promise<void>(resolve => { follow.held.push(resolve); });
    };
    if (input.method === 'GET' && path === `/pm/workspaces/${WS}/work/${WI}/inputs`) {
      const status = follow.inputsStatus;
      const after = url.searchParams.get('after');
      const body = status !== 200 ? {error: {code: 'NOT_FOUND', message: 'gone'}}
        : after && follow.pages.has(after) ? follow.pages.get(after)
        : follow.malformed ? {workspace_id: 'not-this-workspace'}
        : listBody(follow.items.map(row => ({...row})));
      if (url.searchParams.has('attempt_id') && follow.holdFiltered && !follow.holdConsumed) {
        follow.holdConsumed = true;
        await new Promise<void>(resolve => { follow.releaseHold = () => { follow.lateReturned = true; resolve(); }; });
      }
      await pause('inputs');
      return {status, data: body};
    }
    if (input.method === 'GET' && path.startsWith(`/pm/workspaces/${WS}/work-attempts/`) && path.endsWith('/result')) {
      const status = follow.resultStatus;
      const data = status === 200 ? resultBody() : {error: {code: status === 403 ? 'FORBIDDEN' : 'NOT_FOUND', message: 'no'}};
      await pause('result');
      return {status, data};
    }
    const target = input.body?.target;
    const workTarget = target && !Array.isArray(target) && Object.keys(target).length === 2 && target.workspace_id === WS && target.work_item_id === WI;
    if (input.method === 'POST' && path === '/operations' && input.body?.action === 'session.send' && workTarget) {
      follow.posts.push(input);
      if (follow.holdPost) { follow.postStarted = true; await follow.holdPost; }
      if (follow.failPost) return {status: follow.failPost.status, data: {error: {code: follow.failPost.code, message: 'refused', admission_refused: follow.failPost.admission_refused === true}}};
      const key = input.idempotency_key;
      let op = follow.ops.get(key);
      if (!op) {
        follow.seq += 1;
        const operation_id = 'op_' + follow.seq.toString(16).padStart(32, '0');
        const text = input.body.params?.text || '';
        const state = follow.receiptState || 'queued';
        const receipt = follow.receiptMode === 'missing' ? null : {
          operation_id: follow.receiptMode === 'mismatch' ? 'op_' + 'f'.repeat(32) : operation_id,
          workspace_id: WS, work_item_id: WI, attempt_id: ATTEMPT,
          sequence: follow.items.length + 1, state, actor: follow.actor, cancellable: state === 'queued',
          text_excerpt: text, text_truncated: false,
          host: 'do-not-render-host', message_id: 'msg-secret', session_id: 'search-results',
          labels: state === 'queued' ? QUEUED_LABELS : {
            state: {en: state, 'zh-TW': state === 'failed' ? '失敗' : state === 'cancelled' ? '已取消' : '已接受'},
            detail: {en: state, 'zh-TW': '狀態說明'},
          },
          next_allowed_actions: state === 'queued' ? [{kind: 'operation_endpoint', endpoint: 'cancel', operation_id, label: 'Cancel queued instruction', label_zh_TW: '取消排隊中的指令'}] : [],
        };
        op = {
          operation_id, actor: follow.actor, idempotency_key: key, status: follow.opStatus || 'queued',
          action: input.body.action, target: input.body.target, params: input.body.params, preconditions: input.body.preconditions,
          ...(receipt ? {input_receipt: receipt} : {}),
          result: {input_receipt: {labels: {state: {en: 'DECOY', 'zh-TW': '誘餌'}, detail: {en: 'DECOY', 'zh-TW': '誘餌'}}}},
        };
        follow.ops.set(key, op);
        if (receipt && follow.receiptMode !== 'mismatch') follow.items = [...follow.items, receipt];
      }
      return {status: 200, data: {operation: op}};
    }
    const endpoint = path.match(/^\/operations\/(op_[0-9a-f]{32})\/(cancel|resume)$/);
    if (input.method === 'POST' && endpoint) {
      follow.posts.push(input);
      if (endpoint[2] === 'cancel' && follow.cancelMode === 'denied')
        return {status: 409, data: {error: {code: 'PM_WORK_CANCEL_NOT_ALLOWED_AFTER_SEND', message: 'started'}}};
      const id = endpoint[1];
      follow.items = follow.items.map(row => {
        if (row.operation_id !== id) return row;
        if (endpoint[2] === 'resume') return {...row, state: 'queued', next_allowed_actions: []};
        return {...row, state: 'cancelled', cancellable: false, next_allowed_actions: [{
          kind: 'action', action: 'session.send', target: {workspace_id: WS, work_item_id: WI},
          params: row.editText === undefined ? {text: row.full_text || row.text_excerpt, schedule: 'after_current'} : row.editText,
          label: 'Edit and send as new instruction', label_zh_TW: '編輯並以新指令送出',
        }]};
      });
      return {status: 200, data: {operation: {operation_id: id, status: endpoint[2] === 'cancel' ? 'cancelled' : 'queued'}}};
    }
    return base.dispatch(input);
  };
  return {...base, caps, dispatch, follow, pushEvent(resource_type = 'operation') {
    eventSeq += 1;
    base.data.events.push({seq: eventSeq, resource_type, resource_id: resource_type === 'work_item' ? WI : 'op_' + 'e'.repeat(32), kind: `${resource_type}.updated`});
  }};
}

export async function openWork(page: Page, f: ReturnType<typeof followUpFixture>, lang = 'en', {section = true} = {}) {
  page.on('pageerror', error => f.follow.errors.push(String(error)));
  await mountWorkspace(page, f.follow.native, f);
  await page.addInitScript(value => Object.defineProperty(navigator, 'language', {value}), lang);
  await page.goto(`/dashboard/?workbench=1#/pm/${WS}/discussions`);
  await expect(page.locator('[data-workspace-id]')).toHaveAttribute('data-workspace-id', WS, {timeout: 20000});
  await page.evaluate(id => { location.hash = '#/item/' + id; }, WI);
  if (section) await expect(page.locator('[data-work-section="next-turn"]')).toBeVisible({timeout: 20000});
  else await expect(page.locator('.conversation .msg, [data-session-readonly], [data-session-withdrawn]').first()).toBeVisible({timeout: 20000});
}
