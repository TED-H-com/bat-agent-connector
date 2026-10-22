import {eventDelivery} from './event-delivery.mjs';
import {mkdirSync} from 'node:fs';
import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {nativeFixture, answer} from './pm-analysis-fixture.ts';
import {proposalFixture} from './pm-plan-proposals-fixture.ts';
import {workspaceId, threadId} from './pm-fixture.ts';

function guardedFixture(page: any) {
  const f = nativeFixture(), events = eventDelivery(page), dispatch = f.dispatch;
  f.dispatch = input => events.forward(input, dispatch);
  return {...f, events};
}

const route = `/dashboard/#/pm/${workspaceId}/discussions/${threadId}`;
for (const native of [false, true]) for (const adapter of ['native', 'bat'] as const) {
  test(`main composer sends ordinary PM requests and keeps notes separate ${adapter} ${native}`, async ({page}) => {
    const f = guardedFixture(page); f.native.available = adapter === 'native'; f.bat.available = adapter === 'bat';
    await mountConversation(page, native, f.dispatch); await page.goto(route);
    const composer = page.locator('[data-discussion-composer]'), reply = page.locator('[data-discussion-reply]');
    await expect(reply).toBeDisabled();
    await composer.fill('Explain the missing acceptance evidence.');
    await expect(reply).toHaveText('Send to PM');
    await f.events.post(reply);
    await expect(page.locator('[data-pm-role=assistant]')).toContainText(answer);
    await expect(page.locator('[data-pm-request]')).toContainText('Explain the missing acceptance evidence.');
    await expect(composer).toHaveValue('');
    expect(f.state.writes.map(input => input.body.action)).toEqual([adapter === 'bat' ? 'pm.run.bat.analyze' : 'pm.run.analyze']);
    await composer.fill('What should we verify next?'); await f.events.post(reply);
    await expect(page.locator('[data-pm-request]')).toHaveCount(2);
    await expect(page.locator('[data-pm-role=assistant]')).toHaveCount(2);
    expect(f.state.writes[1].body.preconditions.expected_version).toBe(2);
    expect(new Set(f.state.writes.map(input => input.idempotency_key)).size).toBe(2);
    await composer.fill('Keep this as a note, without running the PM.');
    await f.events.post(page.getByRole('button', {name: 'Save note only', exact: true}));
    await expect(page.locator('.pm-notes')).toContainText('Keep this as a note');
    expect(f.state.writes.map(input => input.body.action)).toEqual([
      adapter === 'bat' ? 'pm.run.bat.analyze' : 'pm.run.analyze',
      adapter === 'bat' ? 'pm.run.bat.analyze' : 'pm.run.analyze', 'pm.message.append']);
    await page.reload();
    await expect(page.locator('[data-pm-request]')).toHaveCount(2);
    await expect(page.locator('[data-pm-role=assistant]')).toHaveCount(2);
    expect(f.state.writes).toHaveLength(3);
  });
}

for (const native of [false, true]) test(`lost reply recovers the original request and preserves a newer composer draft ${native ? 'IPC' : 'HTTP'}`, async ({page}) => {
  const f = guardedFixture(page); f.state.lost = true;
  await mountConversation(page, native, f.dispatch); await page.goto(route);
  const composer = page.locator('[data-discussion-composer]'), reply = page.locator('[data-discussion-reply]');
  await composer.fill('Original question'); await f.events.post(reply);
  await expect(page.locator('[data-discussion-reply-working]')).toContainText('Check Details');
  await page.locator('[data-discussion-reply-details]').click();
  await expect(page.locator('[data-pm-native-analysis]')).toBeVisible();
  await expect(page.locator('[data-pm-action="pm.run.analyze"] [data-error-details]')).toContainText('Reply unavailable');
  await expect(page.locator('[data-pm-action="pm.run.analyze"] button[type=submit]')).toBeFocused();
  await page.keyboard.press('Escape');
  await composer.fill('A new question that must survive recovery');
  await page.reload(); await expect(reply).toHaveText('Check original request');
  await f.events.post(reply);
  await expect(page.locator('[data-pm-role=assistant]')).toHaveCount(1);
  await expect(composer).toHaveValue('A new question that must survive recovery');
  expect(f.state.writes).toHaveLength(2);
  expect(f.state.writes[1]).toEqual(f.state.writes[0]);
  expect(f.state.ops.size).toBe(1);
});

test('unavailable PM and oversized requests retain the note without silently running anything', async ({page}) => {
  const f = guardedFixture(page); f.native.eligible = false;
  await mountConversation(page, false, f.dispatch); await page.goto(route);
  await page.locator('[data-discussion-composer]').fill('Keep this draft');
  await expect(page.locator('[data-discussion-reply]')).toBeHidden();
  await expect(page.getByRole('button', {name: 'Save note only', exact: true})).toBeEnabled();
  expect(f.state.writes).toHaveLength(0);
  f.native.eligible = true; await page.reload();
  const composer = page.locator('[data-discussion-composer]');
  await expect(composer).toHaveValue('Keep this draft');
  await composer.fill('x'.repeat(4001));
  await expect(page.locator('[data-discussion-reply]')).toBeDisabled();
  await expect(page.locator('.pd-composer')).toContainText('4,000');
  expect(f.state.writes).toHaveLength(0);
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) {
  test(`ordinary reply composer remains readable ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: 900}});
    const page = await context.newPage(); const f = guardedFixture(page);
    await mountConversation(page, false, f.dispatch); await page.goto(route);
    await page.locator('[data-discussion-composer]').fill(locale === 'en' ? 'What evidence is still missing?' : '目前還缺哪些驗收證據？');
    await expect(page.locator('[data-discussion-reply]')).toHaveText(locale === 'en' ? 'Send to PM' : '傳送給 PM');
    await page.locator('[data-discussion-reply]').click();
    await expect(page.locator('[data-pm-request]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    if (process.env.BATC_REPLY_SHOTS) {
      mkdirSync(process.env.BATC_REPLY_SHOTS, {recursive: true});
      await page.screenshot({path: `${process.env.BATC_REPLY_SHOTS}/reply-${locale}-${width}.png`, fullPage: true});
    }
    await context.close();
  });
}


test('ordinary replies and explicit plan requests coexist in the same composer', async ({page}) => {
  const f = guardedFixture(page), p = proposalFixture();
  // Both halves of this composed fixture answer for the same principal.
  p.state.actor = f.state.actor;
  f.state.packages = false;
  p.state.thread.thread = f.state.thread; p.state.head = f.state.planHead;
  await mountConversation(page, false, async input => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path.includes('/conversation-proposals') || path.endsWith('/plans')
      || input.method === 'POST' && input.body?.action === 'pm.conversation.propose'
      || path.startsWith('/operations/') && [...p.state.ops.values()].some((op: any) => path.endsWith(op.operation_id))) return p.dispatch(input);
    const result = await f.dispatch(input);
    const caps = path === '/bootstrap' ? result.data.capabilities : path === '/capabilities' ? result.data : null;
    if (caps) {caps.features.pm_workspaces.conversation_proposals = {version: 1}; caps.actions.push(...p.caps().actions);}
    return result;
  });
  await page.goto(route);
  const composer = page.locator('[data-discussion-composer]');
  await composer.fill('Explain the delivery risks.');
  await f.events.post(page.getByRole('button', {name: 'Send to PM', exact: true}));
  await expect(page.locator('[data-pm-role=assistant]')).toContainText(answer);
  await expect(page.locator('[data-pm-request]')).toContainText('Explain the delivery risks.');
  expect(p.state.writes).toHaveLength(0);
  await composer.fill('Organize a plan from this discussion.');
  await f.events.post(page.getByRole('button', {name: 'Make a plan', exact: true}));
  await expect(page.locator('[data-pm-plan-proposals]')).toContainText('Paid member access');
  await expect(page.locator('.ui-button.primary:visible')).toHaveCount(1);
  await expect(page.locator('.ui-button.primary:visible')).toHaveText('Review & adopt');
  expect(p.state.writes.map((input: any) => input.body.action)).toEqual(['pm.conversation.propose']);
  expect(f.state.writes.map(input => input.body.action)).toEqual(['pm.run.analyze']);
  expect([...p.state.writes, ...f.state.writes].some(input => /adopt|dispatch|session/.test(input.body.action))).toBe(false);
});


test('a failed PM request offers retry without a dead primary action', async ({page}) => {
  const f = guardedFixture(page); f.native.pending = true;
  await mountConversation(page, false, async input => {
    const result = await f.dispatch(input);
    if (input.method === 'POST' && input.body?.action === 'pm.run.analyze' && f.native.pending) {
      result.data.operation.status = 'failed';
      f.state.runs.at(-1).state = 'failed';
    }
    return result;
  });
  await page.goto(route);
  await page.locator('[data-discussion-composer]').fill('Explain the missing evidence.');
  await page.locator('[data-discussion-reply]').click();
  await expect(page.locator('[data-discussion-reply]')).toBeHidden();
  const status = page.locator('[data-discussion-reply-working]');
  await expect(status.getByRole('button', {name: 'Retry', exact: true})).toBeEnabled();
  f.native.pending = false;
  await f.events.post(status.getByRole('button', {name: 'Retry', exact: true}));
  await expect(page.locator('[data-pm-role=assistant]')).toHaveCount(1);
  expect(f.state.writes).toHaveLength(2);
  expect(f.state.writes[0].idempotency_key).not.toBe(f.state.writes[1].idempotency_key);
  expect(f.state.writes[0].body.params).toEqual(f.state.writes[1].body.params);
});
