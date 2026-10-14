// Session workspace harness: the demo fixture plus per-test response overrides, a request
// log and a controllable event stream. Only mock responses; nothing leaves the page.
import type {Browser, Page} from '@playwright/test';
import {demoFixture, type DemoRequest} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {DEMO_NOW} from './demo/project.ts';

export type Override = (input: DemoRequest, url: URL) => Promise<any> | any;
export interface Logged {at: number, method: string, path: string, status?: number}

export const notFound = {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Session not found'}}};
export const forbidden = {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Not allowed'}}};

export async function openSession(browser: Browser, {locale = 'en', width = 1440, height = 900, overrides = [] as Override[]} = {}) {
  const context = await browser.newContext({locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US', viewport: {width, height}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  await page.clock.setFixedTime(new Date(DEMO_NOW + 60_000));
  const fixture = demoFixture({locale});
  const log: Logged[] = [], writes: any[] = [], errors: string[] = [], events: any[] = [];
  const extra = [...overrides];
  let seq = 0;
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
  const emit = (...items: Array<Record<string, unknown>>) => {
    for (const item of items) {seq += 1; events.push({seq, kind: `${item.resource_type}.updated`, ...item});}
  };
  const dispatch = async (input: DemoRequest) => {
    const url = new URL(input.path, 'http://fixture');
    const entry: Logged = {at: Date.now(), method: input.method || 'GET', path: url.pathname + url.search};
    log.push(entry);
    if (entry.method !== 'GET') writes.push(structuredClone(input));
    let result: any;
    if (url.pathname === '/events') {
      const after = Number(url.searchParams.get('after') || 0);
      const fresh = events.filter(event => Number(event.seq) > after).slice(0, 100);
      const next = fresh.length ? Number(fresh[fresh.length - 1].seq) : after;
      result = {status: 200, data: {events: fresh, next_cursor: next, head_cursor: Math.max(seq, after),
        has_more: next < seq, sync: {checkpoint: {cursor: next, token: `session-${next}`}}}};
    } else {
      for (const override of extra) {
        const value = await override(input, url);
        if (value !== undefined) {result = value; break;}
      }
      if (result === undefined) result = await fixture.dispatch(input);
    }
    entry.status = result.status;
    const body = input.body as any;
    if (entry.method === 'POST' && url.pathname === '/operations' && result.status === 200
      && typeof body?.action === 'string' && body.action.startsWith('session.') && body.target?.host)
      emit({resource_type: 'session', resource_id: `${body.target.host}/${body.target.session_id}`});
    return result;
  };
  await mountConversation(page, false, dispatch);
  const {ids, copy} = fixture;
  return {context, page, fixture, log, writes, errors, emit, overrides: extra, ids, copy,
    sessionEvent: (which: 'implement' | 'review' | 'manual') => emit({resource_type: 'session', resource_id: `${ids.host}/${ids.sessions[which]}`}),
    reads: (pathname: string, since = 0) => log.filter(row => row.method === 'GET' && row.at >= since && row.path.split('?')[0] === pathname).length};
}

// Hash assignment stays in the same document, so the session remembers the project view.
export async function go(page: Page, hash: string) {
  await page.evaluate(next => {location.hash = next;}, hash);
}

export async function fromRequirements(page: Page, ids: any, which: 'implement' | 'review' | 'manual') {
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/requirements`);
  await page.locator('[data-pm-view=requirements][aria-current=page]').waitFor({state: 'attached'});
  await go(page, `#/session/${ids.host}/${ids.sessions[which]}`);
  await page.locator('.workspace-session-heading h1').filter({hasText: /\S/}).waitFor();
}

export const consoleNoise = (line: string) => line.includes('browser-session') || line.startsWith('Failed to load resource:');

const SHA = 'c0ffee1234567890abcdef1234567890abcdef12';
export function checkpointHost(s: Awaited<ReturnType<typeof openSession>>, sessionId: string): Override {
  const {ids, fixture} = s;
  const cp = {checkpoint_id: 'cp_' + '7'.repeat(32), host: ids.host, session_id: ids.sessions.manual, commit_sha: SHA, branch: 'main',
    captured_at: '2026-10-13T08:30:00Z', actor: 'demo-reader', excerpt_messages: 12, dirty: 0, artifacts: []};
  const caps = async () => {
    const base = (await fixture.dispatch({method: 'GET', path: '/capabilities'})).data;
    return {...base, scopes: [...base.scopes, 'start'], features: {...base.features, checkpoints: [ids.host]},
      actions: [...base.actions, {action: 'checkpoint.create', allowed: true}, {action: 'checkpoint.continue', allowed: true}]};
  };
  let seq = 0;
  return async (input, url) => {
    const method = input.method || 'GET';
    if (method === 'GET' && url.pathname === '/capabilities') return {status: 200, data: await caps()};
    if (method === 'GET' && url.pathname === '/bootstrap') return {status: 200, data: {...(await fixture.dispatch({method: 'GET', path: '/bootstrap'})).data, capabilities: await caps()}};
    if (method === 'GET' && url.pathname === '/checkpoints') return {status: 200, data: {checkpoints: url.searchParams.get('session_id') === sessionId ? [cp] : []}};
    if (method === 'GET' && url.pathname.endsWith('/checkpoint-preview'))
      return {status: 200, data: {preview: {head: SHA, dirty: 0, commits: [{hash: SHA, message: 'Reset flow notes'}, {hash: 'b'.repeat(40), message: 'Earlier commit'}]}}};
    if (method === 'GET' && url.pathname === `/checkpoints/${cp.checkpoint_id}`) return {status: 200, data: {checkpoint: cp, source: {head: SHA}}};
    if (method === 'POST' && url.pathname === '/operations' && String((input.body as any)?.action).startsWith('checkpoint.')) {
      seq += 1;
      const body = input.body as any;
      return {status: 200, data: {operation: {operation_id: 'op_' + (0xabc000 + seq).toString(16).padStart(32, '0'), action: body.action, status: 'accepted',
        target: body.target, params: body.params, ...(body.action === 'checkpoint.continue' ? {result: {host: ids.host, session_id: 'continued-session'}} : {})}}};
    }
    if (method === 'GET' && url.pathname.startsWith('/operations/op_')) return {status: 200, data: {operation: {operation_id: url.pathname.split('/').pop(), status: 'accepted'}}};
    return undefined;
  };
}
