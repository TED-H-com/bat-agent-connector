import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {discussHref, parseDiscussRefs} from '../src/pm-discuss-link.js';

for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`discussion with context, review and return ${locale} ${width}`, async ({browser, baseURL}) => {
    test.setTimeout(60000);
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), errors: string[] = [];
    const dir = `${process.env.TMPDIR || '/tmp'}/shots2`; await mkdir(dir, {recursive: true});
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      // The shared bearer-only fixture deliberately returns 404 for this optional
      // managed-session probe. Every discussion/API or script error still fails.
      const optionalProbe = message.location().url.endsWith('/api/v1/browser-session') && message.text().includes('404');
      if (message.type() === 'error' && !optionalProbe) errors.push(message.text() + ' ' + message.location().url);
    });
    const f = demoFixture({locale, discussions: true}), zh = locale === 'zh-TW';
    await mountConversation(page, false, f.dispatch);
    const ref = {kind: 'governance', id: 'pmgov_' + 'a'.repeat(32)};
    const href = discussHref({workspaceId: f.ids.workspaceId, refs: [ref], title: f.copy.blocker, from: 'overview', report: f.ids.snap1});
    expect(parseDiscussRefs(href.split('?')[1]).refs).toEqual([ref]);
    // Producer views share this exact link; the receiving contract is exercised here.
    await page.goto('/dashboard/' + href);
    const composer = page.locator('[data-discussion-composer]');
    await expect(composer).toBeFocused();
    await expect(page.locator('[data-discussion-title]')).toHaveText(f.copy.blocker);
    await expect(page.getByLabel(zh ? '討論標題' : 'Discussion title', {exact: true})).toBeHidden();
    await expect(page.locator('[data-discussion-context=governance]')).toBeVisible();
    await composer.fill(zh ? '請整理這個阻礙，保留尚未確認的預算。' : 'Review this blocker and keep the budget uncertainty explicit.');
    await page.reload(); await expect(composer).not.toHaveValue(''); expect(f.discussion.writes).toHaveLength(0);
    await composer.press('Tab'); await page.getByRole('button', {name: zh ? '傳送' : 'Send', exact: true}).press('Enter');
    await expect(page).toHaveURL(/\/discussions\/pmt_[a-f0-9]{32}$/);
    await expect(page.locator('.pm-notes')).toContainText(zh ? '請整理這個阻礙' : 'Review this blocker');
    expect(f.discussion.writes.map((w: any) => w.body.action)).toEqual(['pm.thread.create', 'pm.message.append']);
    expect(f.discussion.writes[0].body.params.context).toEqual([ref]);
    await page.reload(); expect(f.discussion.threads).toHaveLength(4);
    await expect(page.locator('[data-discussion-composer]')).toBeVisible();
    await page.screenshot({path: `${dir}/discussions-default-${locale}-${width}.png`});
    await page.locator('[data-discussion-ask]').click();
    await expect(page.locator('[data-pm-plan-proposals]')).toContainText(f.copy.proposalSummary);
    await page.getByRole('button', {name: zh ? '重新命名' : 'Rename', exact: true}).click();
    const rename = page.locator('[data-pm-action="pm.thread.rename"]');
    await rename.getByLabel(zh ? '討論標題' : 'Discussion title').fill(zh ? '解除復原阻礙' : 'Unblock recovery');
    await rename.getByRole('button', {name: zh ? '儲存' : 'Save', exact: true}).click();
    await expect(rename).toBeHidden();
    await expect(page.locator('[data-discussion-title]')).toHaveText(zh ? '解除復原阻礙' : 'Unblock recovery');
    await expect(page.locator('[data-proposal-review]')).toBeInViewport();
    const send = await page.getByRole('button', {name: zh ? '傳送' : 'Send', exact: true}).boundingBox();
    const ask = await page.locator('[data-discussion-ask]').boundingBox();
    expect(send!.y).toBe(ask!.y); expect(send!.x).toBeLessThan(ask!.x);
    await expect(page.locator('[data-discussion-working]')).toHaveText(zh ? 'PM 已完成' : 'PM finished');
    await page.screenshot({path: `${dir}/discussions-proposed-${locale}-${width}.png`});
    await page.locator('[data-proposal-review]').press('Enter');
    const adopt = page.locator('[data-pm-action="pm.conversation.adopt"]');
    await adopt.getByLabel(zh ? '採用這組更新的理由' : 'Reason for adopting this update').fill(zh ? '保留未決事項並採用審閱後的範圍。' : 'Adopt the reviewed scope and preserve open decisions.');
    await adopt.getByRole('button', {name: zh ? '採用這組更新' : 'Adopt this update', exact: true}).click();
    await expect(page.locator('[data-proposal-adopted]')).toBeVisible();
    expect(f.discussion.writes.at(-1).body.action).toBe('pm.conversation.adopt');
    if (width < 1200) {await page.locator('[data-discussion-panel-toggle]').click(); await expect(page.locator('.pd-panel-overlay')).toHaveAttribute('aria-modal', 'true');}
    if (width === 390) await page.screenshot({path: `${dir}/discussions-sheet-${locale}-390.png`});
    await page.getByRole('tab', {name: zh ? '計畫' : 'Plan', exact: true}).click();
    await page.locator('[data-pm-section=pm_brief_title] > details > summary').click();
    expect(await page.locator('.pm-brief').innerText()).not.toMatch(/\b(undefined|null|NaN)\b/);
    if (width < 1200) {await page.keyboard.press('Escape'); await expect(page.locator('[data-discussion-panel-toggle]')).toBeFocused();}
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator('#main').innerText()).not.toMatch(/pm_mode_|\b(undefined|null|NaN)\b/);
    await page.screenshot({path: `${dir}/discussions-${locale}-${width}.png`});
    if (width === 1440 && !zh) {await page.evaluate(() => document.documentElement.dataset.theme = 'dark'); await page.screenshot({path: `${dir}/discussions-en-1440-dark.png`, animations: 'disabled'});}
    await page.locator('[data-discussion-back]').first().click(); await expect(page).toHaveURL(/\/overview$/);
    await expect(page.locator('[data-pm-overview]')).toHaveAttribute('data-overview-mode', 'saved');
    await expect(page.locator('.pm-overview-saved')).toContainText(f.copy.snapshotV2);
    expect(errors).toEqual([]); await context.close();
  });
}

test('withdrawn context drops both labels and IDs from list, header and panel', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  const data = f.discussion.details.get(f.ids.threadFollow);
  data.thread.context = [{kind: 'governance', id: 'pmgov_' + 'c'.repeat(32), position: 0, available: true, label: 'Current sensitive label', bound_label: 'Earlier sensitive label'}];
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadFollow}`);
  const chips = page.locator('[data-discussion-context=governance]');
  await expect(chips.first()).toHaveAttribute('title', 'Decision · Current sensitive label\nOriginally: Earlier sensitive label');
  f.discussion.withdrawn = true; await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(chips.first()).toContainText('Unavailable');
  expect(await page.locator('#main').textContent()).not.toMatch(/Current sensitive label|Earlier sensitive label|pmgov_cc/);
  expect(f.discussion.writes).toHaveLength(0);
});

test('a new message makes the proposal out of date while preserving review choices', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click();
  await expect(page.locator('[data-proposal-review]')).toBeVisible();
  const candidate = page.locator('[data-proposal-candidate]').first();
  await candidate.locator(':scope > summary').click(); await candidate.getByLabel('Reviewed wording').fill('Retained owner review');
  await page.locator('[data-discussion-composer]').fill('New evidence requires another proposal.');
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await expect(page.locator('[data-proposal-reason]')).toHaveText('Out of date · Discussion changed');
  await expect(candidate.getByLabel('Reviewed wording')).toHaveValue('Retained owner review');
  await page.getByRole('button', {name: 'Ask PM again', exact: true}).click();
  await expect.poll(() => f.discussion.writes.filter((w: any) => w.body.action === 'pm.conversation.propose').length).toBe(2);
});

test('central conflict offers another analysis without dropping the review draft', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click(); await page.locator('[data-proposal-review]').click();
  await page.getByLabel('Reason for adopting this update').fill('Review against the current sources'); f.discussion.conflict = true;
  await page.getByRole('button', {name: 'Adopt this update', exact: true}).click();
  await expect(page.locator('[data-proposal-reason]')).toHaveText('Out of date');
  await expect(page.getByLabel('Reason for adopting this update')).toHaveValue('Review against the current sources');
  await page.getByRole('button', {name: 'Ask PM again', exact: true}).click();
  await expect.poll(() => f.discussion.writes.filter((w: any) => w.body.action === 'pm.conversation.propose').length).toBe(2);
});

test('running and uncertain analysis retain the acknowledged operation and never resubmit', async ({page}) => {
  const f = demoFixture({discussions: true}); f.discussion.running = true;
  await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click();
  await expect(page.locator('[data-discussion-working]')).toContainText(/PM is working · 0:4[2-9]/);
  const op: any = [...f.discussion.operations.values()][0]; op.status = 'uncertain';
  await expect(page.locator('[data-discussion-working]')).toContainText('Uncertain');
  await expect(page.locator('[data-discussion-working]').getByRole('button', {name: 'Retry', exact: true})).toBeVisible();
  await page.reload(); await expect(page.locator('[data-discussion-ask]')).toHaveText('Check original request');
  await page.locator('[data-discussion-ask]').click(); expect(f.discussion.writes).toHaveLength(1);
});

test('the complete review path works with the keyboard, including a second proposal', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.addInitScript(() => { (window as any).pointerActions = 0; document.addEventListener('pointerdown', () => (window as any).pointerActions++); });
  const tabTo = async (locator: any) => {
    for (let step = 0; step < 100; step++) {
      if (await locator.evaluate((node: Element) => node === document.activeElement)) return;
      await page.keyboard.press('Tab');
    }
    await expect(locator).toBeFocused();
  };
  await page.goto('/dashboard/' + discussHref({workspaceId: f.ids.workspaceId, title: f.copy.blocker, from: 'overview'}));
  await expect(page.locator('[data-discussion-composer]')).toBeFocused();
  await page.keyboard.type('Review the top blocker.');
  await tabTo(page.getByRole('button', {name: 'Send', exact: true})); await page.keyboard.press('Enter');
  await expect(page.locator('.pm-notes')).toContainText('Review the top blocker.');
  await tabTo(page.locator('[data-discussion-ask]')); await page.keyboard.press('Enter');
  await expect(page.locator('[data-proposal-review]')).toBeVisible();
  await tabTo(page.getByRole('button', {name: 'Rename', exact: true})); await page.keyboard.press('Enter');
  await tabTo(page.getByLabel('Discussion title', {exact: true})); await page.keyboard.press('ControlOrMeta+A'); await page.keyboard.type('Resolve the blocker');
  await page.keyboard.press('Enter');
  await expect.poll(() => f.discussion.writes.at(-1).body.action).toBe('pm.thread.rename');
  await tabTo(page.locator('[data-proposal-review]')); await page.keyboard.press('Enter');
  await expect(page.getByLabel('Reason for adopting this update')).toBeFocused(); await page.keyboard.type('Keep the reviewed decisions.');
  await tabTo(page.getByRole('button', {name: 'Adopt this update', exact: true})); await page.keyboard.press('Enter');
  await expect(page.locator('[data-proposal-adopted]')).toBeVisible();
  await tabTo(page.locator('[data-discussion-ask]')); await page.keyboard.press('Enter');
  await expect(page.locator('[data-proposal-adopted]')).toHaveCount(0);
  await expect(page.locator('[data-proposal-review]')).toBeVisible();
  expect(f.discussion.writes.filter((row: any) => row.body.action === 'pm.conversation.propose')).toHaveLength(2);
  await tabTo(page.locator('[data-discussion-back]').first()); await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/overview$/); expect(await page.evaluate(() => (window as any).pointerActions)).toBe(0);
});

test('rename remains reviewable when the fixed discussion exceeds the latest message page', async ({page}) => {
  const f = demoFixture({discussions: true}), data = f.discussion.details.get(f.ids.threadIdea);
  data.messages = Array.from({length: 60}, (_, index) => ({message_id: 'pmm_' + index.toString(16).padStart(32, '0'), sequence: index + 1, content: 'Recorded message ' + index, actor: f.ids.actor, created_at: 1791882000}));
  await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click(); await expect(page.locator('[data-proposal-review]')).toBeVisible();
  data.messages = data.messages.slice(-50); data.coverage.messages_truncated = true;
  data.thread.title = 'Cosmetic rename from another client'; data.thread.version++;
  await page.reload(); await page.locator('[data-proposal-review]').click();
  await page.getByLabel('Reason for adopting this update').fill('Review the original complete fixed context.');
  await expect(page.getByRole('button', {name: 'Adopt this update', exact: true})).toBeEnabled();
  await page.locator('[data-discussion-composer]').fill('A newer message changes the content.');
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await expect(page.locator('[data-proposal-reason]')).toHaveText('Out of date · Discussion changed');
  await expect(page.getByRole('button', {name: 'Adopt this update', exact: true})).toBeDisabled();
});

test('inline rename cancels without writing and recovers an unknown save with the original key', async ({page}) => {
  const f = demoFixture({discussions: true}); let loseReply = true;
  await mountConversation(page, false, async input => {
    const value = await f.dispatch(input);
    if (input.body?.action === 'pm.thread.rename' && loseReply) {
      loseReply = false; return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Original reply unavailable'}}};
    }
    return value;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const title = page.locator('[data-discussion-title]'), original = await title.textContent();
  const rename = page.getByRole('button', {name: 'Rename', exact: true}), input = page.getByLabel('Discussion title', {exact: true});
  await expect(input).toBeHidden(); await rename.press('Enter'); await expect(input).toBeFocused();
  await input.fill('Discard this title'); await input.press('Escape');
  await expect(rename).toBeFocused(); await expect(title).toHaveText(original!); expect(f.discussion.writes).toHaveLength(0);
  await rename.press('Enter'); await input.fill('Retained rename draft'); await page.reload();
  await expect(input).toHaveValue('Retained rename draft'); expect(f.discussion.writes).toHaveLength(0);
  await input.press('Enter');
  const form = page.locator('[data-pm-action="pm.thread.rename"]');
  await expect(form).toContainText('Original reply unavailable');
  await page.reload(); await form.getByRole('button', {name: 'Check original request', exact: true}).click();
  await expect(title).toHaveText('Retained rename draft'); await expect(input).toBeHidden();
  expect(f.discussion.writes).toHaveLength(2); expect(f.discussion.writes[1]).toEqual(f.discussion.writes[0]);
  expect(f.discussion.operations.size).toBe(1);
});

for (const width of [390, 1440]) test(`context chips stay compact and open all references at ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const f = demoFixture({discussions: true}), data = f.discussion.details.get(f.ids.threadIdea);
  data.thread.context = Array.from({length: 5}, (_, i) => ({kind: 'governance', id: 'pmgov_' + String(i).repeat(32), position: i,
    label: 'Current decision ' + i + ' with a deliberately long description of the recovery budget and the login lockout decision',
    bound_label: 'Original decision ' + i, available: true}));
  await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const header = page.locator('.pd-thread-header'), chips = header.locator('[data-discussion-context]');
  await expect(chips).toHaveCount(3);
  await expect(page.locator('.pm-thread-link[aria-current=page] [data-discussion-context]')).toHaveCount(1);
  for (const chip of await chips.all()) {
    const box = (await chip.boundingBox())!; expect(box.height).toBeLessThanOrEqual(24); expect(box.width).toBeLessThanOrEqual(width === 390 ? 240 : 280);
    const text = await chip.textContent(); await expect(chip).toHaveAttribute('aria-label', text!); await expect(chip).toHaveAttribute('title', new RegExp('Original decision'));
  }
  await header.getByRole('button', {name: '2 more context items'}).press('Enter');
  await expect(page.getByRole('tab', {name: 'Context', exact: true})).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.pd-context-details [data-discussion-context]')).toHaveCount(5);
  if (width === 390) {await expect(page.locator('.pd-panel-overlay')).toBeVisible(); await page.keyboard.press('Escape'); await expect(header.getByRole('button', {name: '2 more context items'})).toBeFocused();}
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

for (const locale of ['en', 'zh-TW']) test(`backend adoptability is authoritative and unknown reasons stay private ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale}); const page = await context.newPage();
  const f = demoFixture({discussions: true, locale}), zh = locale === 'zh-TW'; await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click(); await expect(page.locator('[data-proposal-review]')).toBeVisible();
  const data = f.discussion.details.get(f.ids.threadIdea);
  // Different visible source IDs are no longer a client-side veto. Only the
  // backend's explicit field decides whether the existing reviewed intent is fresh.
  data.thread.version += 10; data.thread.draft_id = 'pmd_' + 'e'.repeat(32); data.messages = [];
  await page.getByRole('button', {name: zh ? '重新整理' : 'Refresh', exact: true}).click();
  await page.locator('[data-proposal-review]').click();
  const adopt = page.locator('[data-pm-action="pm.conversation.adopt"]');
  const rationale = adopt.getByLabel(zh ? '採用這組更新的理由' : 'Reason for adopting this update'); await rationale.fill('Retain this reviewed decision');
  await expect(adopt.locator('button[type=submit]')).toBeEnabled();
  const proposal = f.discussion.proposals[0]; proposal.adoptable = false; proposal.adoptable_reason = 'future_central_reason';
  await page.getByRole('button', {name: zh ? '重新整理' : 'Refresh', exact: true}).click();
  await expect(page.locator('[data-proposal-reason]')).toHaveText(zh ? '已過期' : 'Out of date');
  await expect(page.getByRole('button', {name: zh ? '再次詢問 PM' : 'Ask PM again', exact: true})).toBeVisible();
  await expect(adopt.locator('button[type=submit]')).toBeDisabled(); await expect(rationale).toHaveValue('Retain this reviewed decision');
  expect(await page.locator('[data-pm-plan-proposals]').innerText()).not.toContain('future_central_reason');
  await page.locator('[data-proposal-details] > summary').click();
  await expect(page.locator('[data-proposal-adoptable-reason]')).toHaveText('future_central_reason');
  expect(f.discussion.writes).toHaveLength(1); await context.close();
});

test('Plan tab lists central revision states and compares from the selected row', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadPlanning}`);
  await page.getByRole('tab', {name: 'Plan', exact: true}).click();
  const revisions = page.locator('[data-pm-revision]'); await expect(revisions).toHaveCount(2);
  await expect(revisions.nth(0).locator('.ui-pill')).toHaveText('Historical'); await expect(revisions.nth(1).locator('.ui-pill')).toHaveText('Accepted');
  expect(await revisions.evaluateAll(nodes => nodes.every(node => node.tagName !== 'DETAILS'))).toBe(true);
  const refresh = page.getByRole('button', {name: 'Refresh proposals', exact: true}); await expect(refresh).toHaveText(''); await expect(refresh.locator('svg')).toHaveCount(1);
  await revisions.nth(0).getByRole('button', {name: 'Compare', exact: true}).click();
  await expect(page.getByLabel('From version', {exact: true})).toHaveValue(f.ids.planV2); await expect(page.getByLabel('To version', {exact: true})).toHaveValue(f.ids.planV3);
  expect(await page.locator('[data-discussions] details').evaluateAll(nodes => nodes.every(node => node.classList.contains('ui-disclosure') && node.querySelector(':scope > summary > svg')))).toBe(true);
  expect(f.discussion.writes).toHaveLength(0);
});

test('a second analysis with a lost reply shows Retry and never reuses a completed status', async ({page}) => {
  const f = demoFixture({discussions: true}); let attempts = 0;
  await mountConversation(page, false, async input => {
    const result = await f.dispatch(input);
    if (input.body?.action === 'pm.conversation.propose' && ++attempts === 2)
      return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Original reply unavailable'}}};
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click(); await expect(page.locator('[data-discussion-working]')).toHaveText('PM finished');
  await page.locator('[data-discussion-ask]').click();
  const status = page.locator('[data-discussion-working]'); await expect(status).toContainText('Original reply unavailable');
  await expect(status).not.toContainText('PM finished');
  await status.getByRole('button', {name: 'Retry', exact: true}).click(); await expect(status).toHaveText('PM finished');
  expect(f.discussion.writes).toHaveLength(3); expect(f.discussion.writes[2]).toEqual(f.discussion.writes[1]); expect(f.discussion.operations.size).toBe(2);
});

for (const locale of ['en', 'zh-TW']) test(`missing PM adapter occupies only the composer status slot ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale}); const page = await context.newPage();
  const f = demoFixture({discussions: true, locale}); await mountConversation(page, false, async input => {
    const value = await f.dispatch(input);
    if (input.path === `/pm/workspaces/${f.ids.workspaceId}/capabilities`) {
      value.data.native_analysis.eligible_for_preflight = false;
      if (value.data.structured_analysis) for (const action of Object.values(value.data.structured_analysis.actions) as any[])
        for (const adapter of action.adapters) adapter.eligible_for_preflight = false;
    }
    return value;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const status = page.locator('.pd-composer-status');
  await expect(status).toContainText(locale === 'en' ? 'PM agent not connected · Set up' : '尚未連接 PM Agent · 設定');
  await expect(status.getByRole('link')).toHaveAttribute('href', '#/settings');
  await expect(page.locator('[data-discussion-ask]')).toBeHidden();
  expect(f.discussion.writes).toHaveLength(0); await context.close();
});

test('a new multi-context draft opens Context after leaving the Plan tab', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.getByRole('tab', {name: 'Plan', exact: true}).click();
  const refs = Array.from({length: 5}, (_, i) => ({kind: 'governance', id: 'pmgov_' + String(i).repeat(32)}));
  const href = discussHref({workspaceId: f.ids.workspaceId, refs, title: 'Review five decisions', from: 'overview'});
  await page.evaluate(href => {location.hash = href;}, href);
  await expect(page.locator('[data-discussion-composer]')).toBeFocused();
  await expect(page.getByRole('tab', {name: 'Context', exact: true})).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('[data-discussion-tab=context]')).toBeVisible();
  await expect(page.locator('[data-discussion-tab=context] [data-discussion-context]')).toHaveCount(5);
  expect(f.discussion.writes).toHaveLength(0);
});
