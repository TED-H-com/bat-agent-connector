import {test, expect, type Page} from '@playwright/test';
import {setup} from './managed-storage-fixture.ts';

const fact = 'Original managed service fact';
const draftText = 'Retain typed instructions\n保留原文';

async function editingSession(page: Page) {
  await setup(page);
  await page.evaluate(fact => {
    const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
    const checkpoint = {cursor: 5, token: 'fixture-refreshed-proof'};
    const reset = (window as any).__viewReset = {reason: '', waiting: false, returned: false, refreshed: false, issued: false};
    bridge.invoke = async (command: string, args: any) => {
      if (command !== 'connector_request') return invoke(command, args);
      const path = args.input.path;
      if (path.startsWith('/sessions/demo/session-1/messages')) return {status: 200, data: {
        messages: [{id: 'fixture-identity-fact', role: 'assistant', text: fact, ts: '2026-10-10T12:03:00Z'}], next_offset: null}};
      if (path.startsWith('/events') && reset.reason && (!reset.issued || reset.reason !== 'cursor_expired')) {
        reset.issued = true;
        return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: reset.reason, resnapshot: true, preserve_drafts: true}}};
      }
      if (path === '/bootstrap' && reset.issued) {
        reset.waiting = true;
        await new Promise(resolve => {reset.release = resolve;});
        const fixture = (window as any).__storage;
        if (reset.reason === 'principal_changed') fixture.principal = 'different-principal';
        if (reset.reason === 'server_changed') fixture.server = 'different-server';
        const response = await invoke(command, args);
        if (reset.reason === 'cursor_expired') {
          reset.refreshed = true;
          Object.assign(response.data.sync, {checkpoint, head_cursor: checkpoint.cursor});
        }
        reset.returned = true;
        return response;
      }
      const response = await invoke(command, args);
      if (path.startsWith('/events') && reset.refreshed)
        Object.assign(response.data, {next_cursor: checkpoint.cursor, head_cursor: checkpoint.cursor,
          sync: {...response.data.sync, checkpoint, head_cursor: checkpoint.cursor}});
      return response;
    };
  }, fact);
  await page.evaluate(() => {location.hash = '#/session/demo/session-1?tab=conversation';});
  await expect(page.locator('#main').getByText(fact, {exact: true})).toBeVisible();
  const draft = page.locator('#main textarea').first();
  await draft.fill(draftText);
  await expect.poll(() => page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('batc.sync.managed:'))
    .map(([, raw]) => JSON.parse(raw).cursor))).toEqual([1]);
  await page.evaluate(() => {
    const state = (window as any).__viewReset;
    state.scope = Object.keys(localStorage).find(key => key.startsWith('batc.sync.managed:'))!.slice('batc.sync.'.length);
    // Original operation-key writer envelope, retained without sending a request.
    localStorage.setItem(`batc.key.${state.scope}.fixture-original`, JSON.stringify({key: 'original-key', op: 'op_' + 'a'.repeat(32)}));
    state.before = Object.entries(localStorage).filter(([key]) => key.includes(state.scope)).sort();
    state.editor = document.querySelector('#main textarea');
    state.editor.setSelectionRange(2, 6);
  });
  return draft;
}

async function originalStorage(page: Page) {
  return page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.includes((window as any).__viewReset.scope)).sort());
}

for (const locale of ['en-US', 'zh-TW']) {
  for (const reason of ['principal_changed', 'server_changed']) test(`managed ${reason} hides old facts before failed verification without changing original storage (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    await editingSession(page);
    const send = page.locator('#main').getByRole('button', {name: locale === 'zh-TW' ? '送出' : 'Send', exact: true});
    await expect(send).toBeEnabled();
    await send.evaluate((node, reason) => {
      const state = (window as any).__viewReset; state.send = node; state.reason = reason;
    }, reason);
    await expect.poll(() => page.evaluate(() => (window as any).__viewReset.waiting)).toBe(true);
    const waiting = locale === 'zh-TW' ? '等待更新，草稿已保留' : 'Waiting to refresh · draft preserved';
    await expect.soft(page.locator('#main')).toHaveText(waiting);
    await expect.soft(page.locator('#main').getByText(fact, {exact: true})).toHaveCount(0);
    await page.evaluate(() => (window as any).__viewReset.send.click());
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    expect(await originalStorage(page)).toEqual(await page.evaluate(() => (window as any).__viewReset.before));
    await page.evaluate(() => (window as any).__viewReset.release());
    await expect.poll(() => page.evaluate(() => (window as any).__viewReset.returned)).toBe(true);
    await expect.soft(page.locator('#main')).toHaveText(waiting);
    await page.evaluate(() => (window as any).__viewReset.send.click());
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    expect(await originalStorage(page)).toEqual(await page.evaluate(() => (window as any).__viewReset.before));
    await context.close();
  });

  test(`managed cursor expiry retains the editing view through a completed resnapshot (${locale})`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const draft = await editingSession(page);
    await page.evaluate(() => {(window as any).__viewReset.reason = 'cursor_expired';});
    await expect.poll(() => page.evaluate(() => (window as any).__viewReset.waiting)).toBe(true);
    await expect(page.locator('#main').getByText(fact, {exact: true})).toBeVisible();
    await expect(draft).toHaveValue(draftText);
    expect(await originalStorage(page)).toEqual(await page.evaluate(() => (window as any).__viewReset.before));
    await page.evaluate(() => (window as any).__viewReset.release());
    // A saved refreshed checkpoint and a subsequent poll prove completion, not
    // merely entry into the bootstrap request while the old view is still there.
    await expect.poll(() => page.evaluate(() => (window as any).__storage.events.some((path: string) =>
      new URL(path, 'http://fixture.test').searchParams.get('checkpoint') === 'fixture-refreshed-proof'))).toBe(true);
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('batc.sync.' + (window as any).__viewReset.scope)!)))
      .toEqual({cursor: 5, token: 'fixture-refreshed-proof'});
    expect(await draft.evaluate(node => node === (window as any).__viewReset.editor && node === document.activeElement)).toBe(true);
    expect(await draft.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 6]);
    await expect(draft).toHaveValue(draftText);
    const before = await page.evaluate(() => (window as any).__viewReset.before);
    expect((await originalStorage(page)).filter(([key]) => !key.startsWith('batc.sync.'))).toEqual(before.filter(([key]: string[]) => !key.startsWith('batc.sync.')));
    expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(0);
    await context.close();
  });
}
