import {test, expect, type Browser} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';
import {STRINGS} from '../src/i18n.js';

const id = 'op_' + '8'.repeat(32);
const original = (kind: string) => ({operation_id: id, action: 'setup.connection.apply',
  status: 'needs_attention', error_code: kind === 'projection_conflict' ? 'CONNECTION_PROJECTION_CHANGED' : 'EXTERNAL_RESULT_UNKNOWN',
  status_reason: null, actor: 'reader', entry: 'http', created_at: 1793664000, idempotency_key: 'connection-original-key',
  target: {installation_ref: 'desktop-fixture', connection_id: 'conn_' + '7'.repeat(32)},
  external_refs: {native_effects_sent: true, receipt_refs: ['receipt-original']},
  result: null,
  steps: [{seq: 1, name: 'managed_configuration', status: 'succeeded', error: null},
    {seq: 2, name: 'native_effects', status: kind === 'projection_conflict' ? 'succeeded' : 'uncertain', error: null}],
});

async function mount(browser: Browser, locale: string, native: boolean, kind = 'unknown') {
  const context = await browser.newContext({locale: locale === 'en' ? 'en-US' : locale,
    viewport: {width: locale === 'zh-TW' ? 390 : 1440, height: 900}});
  const page = await context.newPage(), fixture = conversationFixture();
  const state = {op: original(kind) as any, reads: 0, failRead: false, failWrite: false, writes: [] as any[],
    release: null as null | (() => void), holdWrite: false};
  await mountConversation(page, native, async input => {
    if (input.method === 'GET' && input.path === `/operations/${id}`) {
      state.reads++;
      return state.failRead ? {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Original read unavailable'}}}
        : {status: 200, data: {operation: structuredClone(state.op)}};
    }
    if (input.method === 'POST') {
      state.writes.push(structuredClone(input));
      if (state.holdWrite) await new Promise<void>(resolve => {state.release = resolve;});
      if (input.path === `/operations/${id}/cancel`) {
        state.op.status = 'cancelled';
        if (kind === 'projection_conflict') state.op.external_refs.projection_decision = {
          code: 'CONNECTION_PROJECTION_NOT_SAVED', native_outcome: 'applied'};
        else state.op.steps[1].error = {code: 'LOCAL_EFFECT_NEVER_PROVEN'};
      } else if (input.path === `/operations/${id}/resume`) state.op.status = 'waiting_external';
      else throw new Error(`Unexpected write: ${input.path}`);
      return state.failWrite ? {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Reply unavailable'}}}
        : {status: 200, data: {operation: structuredClone(state.op)}};
    }
    return fixture.dispatch(input);
  });
  await page.clock.install();
  await page.goto(`/dashboard/#/op/${id}`);
  await expect(page.locator('[data-connection-recovery]')).toHaveAttribute('data-connection-recovery', kind);
  await expect(page.locator('#live')).toHaveText(STRINGS[locale].ui_connected);
  return {context, page, state};
}

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) {
  const copy = STRINGS[locale], mode = native ? 'IPC' : 'HTTP';
  for (const kind of ['unknown', 'projection_conflict']) test(`original connection ${kind} close is explicit and single ${locale} ${mode}`, async ({browser}) => {
    const {context, page, state} = await mount(browser, locale, native, kind);
    try {
      const notice = page.locator('[data-connection-recovery]');
      await expect(notice).toContainText(copy['cop_' + kind]);
      await expect(notice).toContainText(copy.cop_preserved);
      const key = kind === 'unknown' ? 'cop_close_unknown' : 'cop_close_projection';
      const close = page.getByRole('button', {name: copy[key], exact: true});
      state.holdWrite = true;
      await close.click();
      await expect.poll(() => state.writes.length).toBe(1);
      await expect(close).toBeFocused();
      await expect(close).toHaveAttribute('aria-disabled', 'true');
      await close.dispatchEvent('click');
      await page.locator('[data-operation-control=resume]').dispatchEvent('click');
      expect(state.writes).toHaveLength(1);
      state.release!();
      await expect(notice).toHaveAttribute('data-connection-recovery', kind === 'unknown' ? 'unknown_closed' : 'projection_closed');
      await expect(page.locator('[data-operation-control=refresh]')).toBeFocused();
      expect(state.writes[0]).toMatchObject({method: 'POST', path: `/operations/${id}/cancel`, body: {}});
      expect(state.op.idempotency_key).toBe('connection-original-key');
      expect(state.op.external_refs.receipt_refs).toEqual(['receipt-original']);
      await expect(page.locator('[data-operation-control=cancel]')).toHaveCount(0);
      await page.reload();
      await expect(notice).toContainText(copy[kind === 'unknown' ? 'cop_unknown_closed' : 'cop_projection_closed']);
      expect(state.writes).toHaveLength(1);
    } finally {state.release?.(); await context.close();}
  });

  test(`lost connection close reply reads the original result without resend ${locale} ${mode}`, async ({browser}) => {
    const {context, page, state} = await mount(browser, locale, native);
    try {
      state.failWrite = true;
      const close = page.locator('[data-operation-control=cancel]');
      await close.click();
      await expect(page.locator('[data-connection-readback]')).toBeVisible();
      await expect(close).toHaveAttribute('aria-disabled', 'true');
      await close.dispatchEvent('click');
      expect(state.writes).toHaveLength(1);
      const before = state.reads;
      await page.getByRole('button', {name: copy.cop_refresh, exact: true}).click();
      await expect(page.locator('[data-connection-recovery]')).toHaveAttribute('data-connection-recovery', 'unknown_closed');
      await expect(page.locator('[data-operation-control=refresh]')).toBeFocused();
      expect(state.reads).toBe(before + 1);
      expect(state.writes).toHaveLength(1);
    } finally {await context.close();}
  });

  test(`failed connection read withdraws controls and can recover by GET ${locale} ${mode}`, async ({browser}) => {
    const {context, page, state} = await mount(browser, locale, native);
    try {
      state.failRead = true;
      await page.getByRole('button', {name: copy.cop_refresh, exact: true}).click();
      await expect(page.locator('[data-operation-control=cancel]')).toHaveCount(0);
      await expect(page.locator('[data-operation-control=resume]')).toHaveCount(0);
      await expect(page.locator('[data-connection-recovery]')).toHaveAttribute('data-connection-recovery', 'original');
      await expect(page.locator('[data-operation-control=refresh]')).toBeFocused();
      state.failRead = false;
      await page.getByRole('button', {name: copy.cop_refresh, exact: true}).click();
      await expect(page.getByRole('button', {name: copy.cop_close_unknown, exact: true})).toBeVisible();
      expect(state.writes).toEqual([]);
    } finally {await context.close();}
  });

  test(`continue uses only the original connection operation ${locale} ${mode}`, async ({browser}) => {
    const {context, page, state} = await mount(browser, locale, native);
    try {
      await page.getByRole('button', {name: copy.cop_resume, exact: true}).click();
      await expect(page.locator('[data-connection-recovery]')).toHaveAttribute('data-connection-recovery', 'dispatched');
      expect(state.writes).toHaveLength(1);
      expect(state.writes[0]).toMatchObject({method: 'POST', path: `/operations/${id}/resume`, body: {}});
      await expect(page.locator('[data-operation-control=cancel]')).toHaveCount(0);
      expect(state.op.idempotency_key).toBe('connection-original-key');
    } finally {await context.close();}
  });

  test(`detach recovery preserves its original action and does not stop a service ${locale} ${mode}`, async ({browser}) => {
    const {context, page, state} = await mount(browser, locale, native);
    try {
      state.op.action = 'connection.reference.detach';
      await page.getByRole('button', {name: copy.cop_refresh, exact: true}).click();
      await expect(page.locator('[data-connection-recovery]')).toContainText(copy.cop_preserved);
      await page.getByRole('button', {name: copy.cop_close_unknown, exact: true}).click();
      await expect(page.locator('[data-connection-recovery]')).toHaveAttribute('data-connection-recovery', 'unknown_closed');
      expect(state.writes.map(write => write.path)).toEqual([`/operations/${id}/cancel`]);
      expect(state.op.action).toBe('connection.reference.detach');
      expect(state.op.target.installation_ref).toBe('desktop-fixture');
    } finally {await context.close();}
  });

  test(`late connection recovery reply cannot restore a departed view ${locale} ${mode}`, async ({browser}) => {
    const {context, page, state} = await mount(browser, locale, native);
    try {
      state.holdWrite = true;
      state.failWrite = true;
      await page.getByRole('button', {name: copy.cop_close_unknown, exact: true}).click();
      await expect.poll(() => state.writes.length).toBe(1);
      await page.evaluate(() => {location.hash = '#/sessions';});
      await expect(page.locator('[data-connection-recovery]')).toHaveCount(0);
      state.release!();
      // Flush the delayed reply through the same transport before checking the new view.
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await expect(page.locator('[data-connection-recovery]')).toHaveCount(0);
      await expect(page.locator('main')).not.toContainText('Reply unavailable');
      expect(state.writes).toHaveLength(1);
    } finally {state.release?.(); await context.close();}
  });
}
