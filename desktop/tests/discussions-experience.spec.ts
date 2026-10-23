import {openThreadTab, recoverOriginalAdoption} from './pm-navigation.ts';
import {test, expect} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {discussHref, parseDiscussRefs} from '../src/pm-discuss-link.js';

for (const locale of ['en', 'zh-TW']) test(`saved context keeps its caption, age and analysis status readable ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width: 1200, height: 900}});
  const page = await context.newPage(), f = demoFixture({locale, discussions: true});
  const runId = 'pmrun_' + '7'.repeat(32), zh = locale === 'zh-TW';
  await mountConversation(page, false, async input => {
    if (new URL(input.path, 'http://fixture').pathname === `/pm/workspaces/${f.ids.workspaceId}/runs`) return {status: 200,
      data: {workspace_id: f.ids.workspaceId, runs: [{run_id: runId, workspace_id: f.ids.workspaceId, thread_id: f.ids.threadFollow, operation_id: 'op_' + '7'.repeat(32), created_at: Date.now() / 1000 - 20,
        purpose: zh ? '確認重設連結效期為 15 分鐘；保留登入與密碼重設，推薦獎勵不在本輪範圍。'
          : 'Agree a 15-minute reset-link expiry; keep sign-in and recovery, and exclude referral rewards.',
        adapter: 'codex-native-linux', state: 'completed'}], next_cursor: null}};
    const result = await f.dispatch(input);
    const caps = input.path === '/bootstrap' ? result.data.capabilities : input.path === '/capabilities' ? result.data : null;
    if (caps) caps.features.pm_workspaces.context_packages = true;
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadFollow}`);
  for (const width of [1200, 1440, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    // An open dock moves into its sheet after responsive CSS has hidden the old home.
    if (await page.locator('[data-discussion-panel-toggle]').getAttribute('aria-expanded') === 'true') await expect(page.locator('[data-discussion-panel]')).toBeVisible();
    await openThreadTab(page, 'context');
    const summary = page.locator(`[data-pm-run="${runId}"] > summary`);
    await expect(summary).toContainText(zh ? '分析已完成' : 'Analysis completed');
    await expect(summary.locator('time')).toBeVisible();
    // Words may move to the next line; a narrow flex column must not split
    // their letters over several lines beside the saved purpose.
    expect(await summary.evaluate(node => {
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT), split: string[] = [];
      for (let text = walker.nextNode(); text; text = walker.nextNode()) {
        for (const match of text.textContent!.matchAll(/[A-Za-z]{3,}/g)) {
          const range = document.createRange(); range.setStart(text, match.index!); range.setEnd(text, match.index! + match[0].length);
          if (range.getClientRects().length > 1) split.push(match[0]);
        }
      }
      return split;
    }), `${locale} at ${width}`).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width < 1200) await page.keyboard.press('Escape');
  }
  expect(f.discussion.writes).toEqual([]);
  await context.close();
});

for (const locale of ['en', 'zh-TW']) test(`new discussion stays focused when central exposes all thread tools ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width: locale === 'en' ? 1440 : 390, height: 900}});
  const page = await context.newPage(), f = demoFixture({locale, discussions: true});
  await mountConversation(page, false, async input => {
    if (input.path.startsWith(`/pm/workspaces/${f.ids.workspaceId}/projects?`)) return {status: 200,
      data: {workspace_id: f.ids.workspaceId, bindings: [], next_cursor: null}};
    const result = await f.dispatch(input);
    const caps = input.path === '/bootstrap' ? result.data.capabilities : input.path === '/capabilities' ? result.data : null;
    if (caps) Object.assign(caps.features.pm_workspaces, {meetings: true, work_evidence: true, spec_sources: true, follow_up_prepare: true});
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/new`);
  await expect(page.locator('[data-discussion-composer]')).toBeFocused();
  await expect(page.locator('[data-discussion-composer]')).toBeInViewport();
  await expect(page.locator('[data-discussion-draft-ready]')).toHaveText(locale === 'zh-TW' ? '先儲存第一則筆記建立討論，再傳送要求給 PM。' : 'Save the first note to create this discussion, then send a request to the PM.');
  await expect(page.locator('[data-discussion-pm-unavailable]')).toHaveCount(0);
  await expect(page.locator('[data-pm-action]:visible')).toHaveCount(1);
  await expect(page.locator('.pm-main > [data-pm-follow-up], .pm-main > [data-pm-meetings], .pm-main > .pm-evidence, .pm-main > .pm-spec-sources')).toHaveCount(0);
  expect(f.discussion.writes).toHaveLength(0);
  await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) {
  test(`discussion with context, review and return ${locale} ${width}`, async ({browser, baseURL}, testInfo) => {
    test.setTimeout(60000);
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      // The shared bearer-only fixture deliberately returns 404 for this optional
      // managed-session probe. Every discussion/API or script error still fails.
      const optionalProbe = message.location().url.endsWith('/api/v1/browser-session') && message.text().includes('404');
      if (message.type() === 'error' && !optionalProbe) errors.push(message.text() + ' ' + message.location().url);
    });
    const f = demoFixture({locale, discussions: true}), zh = locale === 'zh-TW';
    await mountConversation(page, false, f.dispatch);
    const ref = {kind: 'governance', id: 'pmgov_' + '1'.padStart(32, '0')};
    const href = discussHref({workspaceId: f.ids.workspaceId, refs: [ref], title: f.copy.blocker, from: 'overview', report: f.ids.snap1});
    expect(parseDiscussRefs(href.split('?')[1]).refs).toEqual([ref]);
    // Producer views share this exact link; the receiving contract is exercised here.
    await page.goto('/dashboard/' + href);
    const composer = page.locator('[data-discussion-composer]');
    await expect(composer).toBeFocused();
    await expect(page.locator('[data-discussion-title]')).toHaveText(f.copy.blocker);
    await expect(page.getByLabel(zh ? '討論標題' : 'Discussion title', {exact: true})).toBeHidden();
    await expect(page.locator('.pd-thread-header [data-discussion-context=governance]')).toBeVisible();
    await expect(page.locator('.pd-thread-header [data-discussion-context=governance]')).toContainText(f.copy.blocker);
    const draftTitle = zh ? '審閱復原阻礙' : 'Review the recovery blocker';
    await page.getByRole('button', {name: zh ? '重新命名' : 'Rename', exact: true}).click();
    await page.getByLabel(zh ? '討論標題' : 'Discussion title', {exact: true}).fill(draftTitle);
    await page.getByRole('button', {name: zh ? '儲存' : 'Save', exact: true}).click();
    await expect(page.locator('[data-discussion-title]')).toHaveText(draftTitle);
    expect(f.discussion.writes).toHaveLength(0);
    await composer.fill(zh ? '請整理這個阻礙，保留尚未確認的預算。' : 'Review this blocker and keep the budget uncertainty explicit.');
    await page.reload(); await expect(composer).not.toHaveValue(''); expect(f.discussion.writes).toHaveLength(0);
    await expect(page.locator('[data-discussion-title]')).toHaveText(draftTitle);
    const sendDraft = page.getByRole('button', {name: zh ? '只存筆記' : 'Save note only', exact: true});
    await expect(sendDraft).toBeEnabled();
    await composer.press('Tab');
    await sendDraft.focus(); await expect(sendDraft).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/discussions\/pmt_[a-f0-9]{32}$/);
    await expect(page.locator('.pm-notes')).toContainText(zh ? '請整理這個阻礙' : 'Review this blocker');
    expect(f.discussion.writes.map((w: any) => w.body.action)).toEqual(['pm.thread.create', 'pm.message.append']);
    expect(f.discussion.writes[0].body.params.context).toEqual([ref]);
    expect(f.discussion.writes[0].body.params.title).toBe(draftTitle);
    await page.reload(); expect(f.discussion.threads).toHaveLength(4);
    await expect(page.locator('[data-discussion-composer]')).toBeVisible();
    await page.screenshot({path: testInfo.outputPath(`discussions-default-${locale}-${width}.png`)});
    await page.locator('[data-discussion-ask]').click();
    await expect(page.locator('[data-pm-plan-proposals]')).toContainText(f.copy.proposalSummary);
    await expect(page.locator('[data-discussion-panel]')).toBeHidden();
    await page.getByRole('button', {name: zh ? '重新命名' : 'Rename', exact: true}).click();
    const rename = page.locator('[data-pm-action="pm.thread.rename"]');
    await rename.getByLabel(zh ? '討論標題' : 'Discussion title').fill(zh ? '解除復原阻礙' : 'Unblock recovery');
    await rename.getByRole('button', {name: zh ? '儲存' : 'Save', exact: true}).click();
    await expect(rename).toBeHidden();
    await expect(page.locator('[data-discussion-title]')).toHaveText(zh ? '解除復原阻礙' : 'Unblock recovery');
    await expect(page.locator('[data-proposal-review]')).toBeInViewport();
    const send = await page.getByRole('button', {name: zh ? '只存筆記' : 'Save note only', exact: true}).boundingBox();
    const ask = await page.locator('[data-discussion-ask]').boundingBox();
    expect(send!.y).toBe(ask!.y); expect(send!.x).toBeLessThan(ask!.x);
    await expect(page.locator('[data-discussion-working]')).toHaveText(zh ? '此討論上次的 PM 執行：PM 已完成' : 'This discussion’s last PM run: PM finished');
    await page.screenshot({path: testInfo.outputPath(`discussions-proposed-${locale}-${width}.png`)});
    await page.locator('[data-proposal-review]').press('Enter');
    const adopt = page.locator('[data-pm-action="pm.conversation.adopt"]');
    await expect(adopt.locator('.pd-candidate input[type="checkbox"]').first()).toBeFocused();
    await expect(page.locator('[data-pm-plan-proposals] [data-proposal-details]')).toHaveCount(1);
    await expect(adopt.locator('[data-proposal-adopt-panel] > details')).toHaveCount(0);
    await adopt.getByLabel(zh ? '採用這組更新的理由' : 'Reason for adopting this update').fill(zh ? '保留未決事項並採用審閱後的範圍。' : 'Adopt the reviewed scope and preserve open decisions.');
    await adopt.getByRole('button', {name: zh ? '採用' : 'Adopt', exact: true}).click();
    await expect(page.locator('[data-proposal-adopted]')).toBeVisible();
    await expect(page.locator('[data-proposal-adopted] [data-status=adopted]')).toHaveText(zh ? '已採用' : 'Adopted');
    await expect(page.locator('[data-proposal-adopted-revision]')).toHaveText(zh ? '檢視版本' : 'Open revision');
    expect(f.discussion.writes.at(-1).body.action).toBe('pm.conversation.adopt');
    await page.locator('[data-discussion-panel-toggle]').click();
    if (width < 1200) await expect(page.locator('.pd-panel-overlay')).toHaveAttribute('aria-modal', 'true');
    if (width === 390) await page.screenshot({path: testInfo.outputPath(`discussions-sheet-${locale}-390.png`)});
    await page.getByRole('tab', {name: zh ? '計畫' : 'Plan', exact: true}).click();
    await page.locator('[data-pm-section=pm_brief_title] > details > summary').click();
    expect(await page.locator('.pm-brief').innerText()).not.toMatch(/\b(undefined|null|NaN)\b/);
    if (width < 1200) {await page.keyboard.press('Escape'); await expect(page.locator('[data-discussion-panel-toggle]')).toBeFocused();}
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator('#main').innerText()).not.toMatch(/pm_mode_|\b(undefined|null|NaN)\b/);
    await page.screenshot({path: testInfo.outputPath(`discussions-${locale}-${width}.png`)});
    if (width === 1440 && !zh) {await page.evaluate(() => document.documentElement.dataset.theme = 'dark'); await page.screenshot({path: testInfo.outputPath(`discussions-en-1440-dark.png`), animations: 'disabled'});}
    // The origin link belongs to the single-pane header; use that real return path.
    if (width >= 800) {await page.setViewportSize({width: 390, height: 844}); await page.keyboard.press('Escape');}
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
  const chips = page.locator('.pd-thread-header [data-discussion-context=governance]');
  await expect(chips.first()).toHaveAttribute('title', 'Decision · Current sensitive label\nOriginally: Earlier sensitive label');
  f.discussion.withdrawn = true; await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(chips.first()).toContainText('Unavailable');
  expect(await page.locator('#main').textContent()).not.toMatch(/Current sensitive label|Earlier sensitive label|pmgov_cc/);
  expect(f.discussion.writes).toHaveLength(0);
});

for (const status of [403, 404]) test(`draft governance context withdrawn with ${status} drops its label and prevents sending`, async ({page}) => {
  const f = demoFixture({discussions: true}), id = 'pmgov_' + '1'.padStart(32, '0');
  let withdrawn = false;
  await mountConversation(page, false, async input => {
    if (withdrawn && input.path.endsWith('/governance/' + id)) return {status, data: {error: {code: 'NOT_FOUND', message: 'Unavailable'}}};
    return f.dispatch(input);
  });
  await page.goto('/dashboard/' + discussHref({workspaceId: f.ids.workspaceId, refs: [{kind: 'governance', id}], from: 'issues'}));
  const chip = page.locator('.pd-thread-header [data-discussion-context=governance]');
  await expect(chip).toContainText(f.copy.blocker);
  const composer = page.locator('[data-discussion-composer]');
  await composer.fill('Keep this local draft');
  withdrawn = true; await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(chip).toContainText('Unavailable');
  await expect(page.locator('#main')).not.toContainText(f.copy.blocker);
  await expect(chip).not.toContainText(id);
  await expect(composer).toHaveValue('Keep this local draft');
  await expect(page.getByRole('button', {name: 'Save note only', exact: true})).toBeDisabled();
  expect(f.discussion.writes).toHaveLength(0);
});

test('a late draft context response cannot revive a withdrawn record', async ({page}) => {
  const f = demoFixture({discussions: true}), id = 'pmgov_' + '1'.padStart(32, '0');
  let count = 0, release: () => void;
  const delayed = new Promise<void>(resolve => {release = resolve;});
  await mountConversation(page, false, async input => {
    if (input.path.endsWith('/governance/' + id)) {
      count++;
      if (count === 2) {await delayed; return f.dispatch(input);}
      if (count > 2) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Unavailable'}}};
    }
    return f.dispatch(input);
  });
  await page.goto('/dashboard/' + discussHref({workspaceId: f.ids.workspaceId, refs: [{kind: 'governance', id}]}));
  const chip = page.locator('.pd-thread-header [data-discussion-context=governance]');
  await expect(chip).toContainText(f.copy.blocker);
  const refresh = page.getByRole('button', {name: 'Refresh', exact: true});
  await refresh.click(); await expect.poll(() => count).toBe(2);
  // A second projection refresh wins over the in-flight older read.
  await refresh.dispatchEvent('click'); await expect(chip).toContainText('Unavailable');
  const response = page.waitForResponse(response => response.url().endsWith('/governance/' + id) && response.status() === 200);
  release!(); await response;
  await expect(chip).toContainText('Unavailable');
  await expect(page.locator('#main')).not.toContainText(f.copy.blocker);
  await expect(page.getByRole('button', {name: 'Save note only', exact: true})).toBeDisabled();
  expect(f.discussion.writes).toHaveLength(0);
});

for (const status of ['waiting_external', 'waiting_checks']) test(`adoption checks the original ${status} receipt until it succeeds`, async ({page}) => {
  const f = demoFixture({discussions: true}), reads: string[] = []; let adoption: any;
  await mountConversation(page, false, async input => {
    const result = await f.dispatch(input);
    if (input.method === 'POST' && input.body?.action === 'pm.conversation.adopt') {
      adoption = structuredClone(result.data.operation);
      result.data.operation.status = status;
    } else if (adoption && input.method === 'GET' && input.path.startsWith('/operations/')) {
      reads.push(input.path);
      result.data.operation.status = reads.length < 2 ? status : 'succeeded';
    }
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click();
  await page.locator('[data-proposal-review]').click();
  const form = page.locator('[data-pm-action="pm.conversation.adopt"]');
  await form.getByLabel('Reason for adopting this update').fill('Adopt the reviewed update');
  await form.getByRole('button', {name: 'Adopt', exact: true}).click();
  await expect(page.locator('[data-proposal-adopted]')).toBeVisible();
  expect(reads).toEqual([`/operations/${adoption.operation_id}`, `/operations/${adoption.operation_id}`]);
  const writes = f.discussion.writes.filter((row: any) => row.body.action === 'pm.conversation.adopt');
  expect(writes).toHaveLength(1);
  expect(writes[0].idempotency_key).toBe(adoption.idempotency_key);
});

test('a new message makes the proposal out of date while preserving review choices', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click();
  await expect(page.locator('[data-proposal-review]')).toBeVisible();
  await page.locator('[data-proposal-review]').click();
  const candidate = page.locator('[data-proposal-candidate]').first();
  await candidate.locator(':scope > summary').click(); await candidate.getByLabel('Reviewed wording').fill('Retained owner review');
  await page.locator('[data-discussion-composer]').fill('New evidence requires another proposal.');
  await page.getByRole('button', {name: 'Save note only', exact: true}).click();
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
  await page.getByRole('button', {name: 'Adopt', exact: true}).click();
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
    await expect(locator).toBeEnabled();
    for (let step = 0; step < 100; step++) {
      if (await locator.evaluate((node: Element) => node === document.activeElement)) return;
      await page.keyboard.press('Tab');
    }
    await expect(locator).toBeFocused();
  };
  await page.goto('/dashboard/' + discussHref({workspaceId: f.ids.workspaceId, title: f.copy.blocker, from: 'overview'}));
  await expect(page.locator('[data-discussion-composer]')).toBeFocused();
  await page.keyboard.type('Review the top blocker.');
  await tabTo(page.getByRole('button', {name: 'Save note only', exact: true})); await page.keyboard.press('Enter');
  await expect(page.locator('.pm-notes')).toContainText('Review the top blocker.');
  await tabTo(page.locator('[data-discussion-ask]')); await page.keyboard.press('Enter');
  await expect(page.locator('[data-proposal-review]')).toBeVisible();
  await tabTo(page.getByRole('button', {name: 'Rename', exact: true})); await page.keyboard.press('Enter');
  await tabTo(page.getByLabel('Discussion title', {exact: true})); await page.keyboard.press('ControlOrMeta+A'); await page.keyboard.type('Resolve the blocker');
  await page.keyboard.press('Enter');
  await expect.poll(() => f.discussion.writes.at(-1).body.action).toBe('pm.thread.rename');
  await tabTo(page.locator('[data-proposal-review]')); await page.keyboard.press('Enter');
  await tabTo(page.getByLabel('Reason for adopting this update')); await page.keyboard.type('Keep the reviewed decisions.');
  await tabTo(page.getByRole('button', {name: 'Adopt', exact: true})); await page.keyboard.press('Enter');
  await expect(page.locator('[data-proposal-adopted]')).toBeVisible();
  await tabTo(page.locator('[data-discussion-ask]')); await page.keyboard.press('Enter');
  await expect(page.locator('[data-proposal-adopted]')).toHaveCount(0);
  await expect(page.locator('[data-proposal-review]')).toBeVisible();
  expect(f.discussion.writes.filter((row: any) => row.body.action === 'pm.conversation.propose')).toHaveLength(2);
  await tabTo(page.locator('[data-pm-view=overview]')); await page.keyboard.press('Enter');
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
  await expect(page.getByRole('button', {name: 'Adopt', exact: true})).toBeEnabled();
  await page.locator('[data-discussion-composer]').fill('A newer message changes the content.');
  await page.getByRole('button', {name: 'Save note only', exact: true}).click();
  await expect(page.locator('[data-proposal-reason]')).toHaveText('Out of date · Discussion changed');
  await expect(page.getByRole('button', {name: 'Adopt', exact: true})).toBeDisabled();
});

test('inline rename cancels without writing and recovers an unknown save with the original key', async ({page}) => {
  const f = demoFixture({discussions: true}); let loseReply = true, restoring = false;
  let releaseRestore!: () => void; const restoreGate = new Promise<void>(resolve => {releaseRestore = resolve;});
  await mountConversation(page, false, async input => {
    const value = await f.dispatch(input);
    if (restoring && input.path.includes('/conversation-proposals?')) await restoreGate;
    if (input.body?.action === 'pm.thread.rename' && loseReply) {
      loseReply = false; return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Original reply unavailable'}}};
    }
    return value;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const title = page.locator('[data-discussion-title]'), original = await title.textContent();
  const rename = page.getByRole('button', {name: 'Rename', exact: true}), input = page.getByLabel('Discussion title', {exact: true});
  await expect(rename).toBeEnabled();
  await expect(input).toBeHidden(); await rename.press('Enter'); await expect(input).toBeFocused();
  await input.fill('Discard this title'); await input.press('Escape');
  await expect(rename).toBeFocused(); await expect(title).toHaveText(original!); expect(f.discussion.writes).toHaveLength(0);
  await rename.press('Enter'); await input.fill('Retained rename draft'); restoring = true; await page.reload();
  await expect(input).toHaveValue('Retained rename draft'); expect(f.discussion.writes).toHaveLength(0);
  await expect(input).not.toBeEditable(); await expect(input).toHaveAttribute('readonly', '');
  await expect(page.locator('[data-pm-action="pm.thread.rename"]').getByRole('button', {name: 'Save', exact: true})).toBeDisabled();
  await input.press('Enter'); expect(f.discussion.writes).toHaveLength(0);
  restoring = false; releaseRestore();
  // Restoring a draft precedes the complete snapshot's send admission.
  await expect(input).toBeEditable(); await input.press('Enter');
  const form = page.locator('[data-pm-action="pm.thread.rename"]');
  await expect(form).toContainText('Original reply unavailable');
  await expect(input).toBeDisabled();
  await input.evaluate(node => node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})));
  await expect(input).toBeVisible(); await expect(input).toHaveValue('Retained rename draft');
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
  await expect(chips).toHaveCount(1);
  await expect(page.locator('.pm-thread-link[aria-current=page] [data-discussion-context]')).toHaveCount(1);
  for (const chip of await chips.all()) {
    const box = (await chip.boundingBox())!; expect(box.height).toBeLessThanOrEqual(24); expect(box.width).toBeLessThanOrEqual(width === 390 ? 240 : 280);
    const text = await chip.textContent(); await expect(chip).toHaveAttribute('aria-label', text!); await expect(chip).toHaveAttribute('title', new RegExp('Original decision'));
  }
  await header.getByRole('button', {name: '+4 more context items', exact: true}).press('Enter');
  await expect(page.getByRole('tab', {name: 'Context', exact: true})).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.pd-context-details [data-discussion-context]')).toHaveCount(5);
  if (width === 390) {await expect(page.locator('.pd-panel-overlay')).toBeVisible(); await page.keyboard.press('Escape'); await expect(header.getByRole('button', {name: '+4 more context items', exact: true})).toBeFocused();}
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
  const proposal = f.discussion.proposals.find((row: any) => row.thread_id === f.ids.threadIdea); proposal.adoptable = false; proposal.adoptable_reason = 'future_central_reason';
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
  await openThreadTab(page, 'plan');
  const revisions = page.locator('[data-pm-revision]'); await expect(revisions).toHaveCount(2);
  await expect(revisions.nth(0).locator('.ui-pill')).toHaveText('Historical'); await expect(revisions.nth(1).locator('.ui-pill')).toHaveText('Adopted');
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
  await page.locator('[data-discussion-ask]').click(); await expect(page.locator('[data-discussion-working]')).toHaveText('This discussion’s last PM run: PM finished');
  await page.locator('[data-discussion-ask]').click();
  const status = page.locator('[data-discussion-working]'); await expect(status).toContainText('This request could not be completed. Check Details.');
  await expect(page.locator('[data-pm-action="pm.conversation.propose"] [data-error-details]')).toContainText('Original reply unavailable');
  await expect(status).not.toContainText('PM finished');
  await status.getByRole('button', {name: 'Retry', exact: true}).click(); await expect(status).toHaveText('This discussion’s last PM run: PM finished');
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
  await expect(status).toContainText(locale === 'en' ? 'PM availability is unknown. Refresh, or ask the Connector operator.' : 'PM 可用性未知。請重新整理，或詢問 Connector 管理員。');
  await expect(status.getByRole('link')).toHaveAttribute('href', '#/settings?section=agents&from=' + encodeURIComponent(`#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`));
  await expect(page.locator('[data-discussion-ask]')).toBeHidden();
  expect(f.discussion.writes).toHaveLength(0); await context.close();
});

test('a new multi-context draft opens Context after leaving the Plan tab', async ({page}) => {
  const f = demoFixture({discussions: true});
  const refs = Array.from({length: 5}, (_, i) => ({kind: 'governance', id: 'pmgov_' + String(i + 1).padStart(32, '0')}));
  await mountConversation(page, false, async input => {
    const ref = refs.find(ref => input.path === `/pm/workspaces/${f.ids.workspaceId}/governance/${ref.id}`);
    if (input.method === 'GET' && ref) {
      const result = structuredClone(await f.dispatch({...input, path: `/pm/workspaces/${f.ids.workspaceId}/governance/${refs[0].id}`}));
      result.data.record.record_id = ref.id;
      return result;
    }
    return f.dispatch(input);
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await openThreadTab(page, 'plan');
  const href = discussHref({workspaceId: f.ids.workspaceId, refs, title: 'Review five decisions', from: 'overview'});
  await page.evaluate(href => {location.hash = href;}, href);
  await expect(page.locator('[data-discussion-composer]')).toBeFocused();
  await expect(page.locator('[data-discussion-panel]')).toBeHidden();
  await page.locator('[data-discussion-more-context]').click();
  await expect(page.getByRole('tab', {name: 'Context', exact: true})).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('[data-discussion-tab=context]')).toBeVisible();
  await expect(page.locator('[data-discussion-tab=context] [data-discussion-context]')).toHaveCount(5);
  expect(f.discussion.writes).toHaveLength(0);
});

for (const outcome of ['failed', 'uncertain']) test(`first-message ${outcome} never redirects another launch or resends an edited draft`, async ({page}, testInfo) => {
  await page.setViewportSize({width: 1440, height: 900});
  const f = demoFixture({discussions: true}); let original: any;
  await mountConversation(page, false, async (input: any) => {
    if (input.method === 'POST' && input.body?.action === 'pm.message.append' && !original) {
      f.discussion.writes.push(structuredClone(input));
      original = {...input.body, actor: f.ids.actor, idempotency_key: input.idempotency_key,
        operation_id: 'op_' + 'e'.repeat(32), status: outcome, result: null, error: {code: 'APPEND_FAILED', message: 'First note not confirmed'}};
      return {status: 200, data: {operation: original}};
    }
    if (original && input.path === '/operations/' + original.operation_id) return {status: 200, data: {operation: original}};
    return f.dispatch(input);
  });
  await page.goto('/dashboard/' + discussHref({workspaceId: f.ids.workspaceId, title: 'Topic A', from: 'overview'}));
  const composer = page.locator('[data-discussion-composer]'); await composer.fill('Original first note');
  await page.getByRole('button', {name: 'Save note only', exact: true}).click();
  await expect.poll(() => f.discussion.writes.length).toBe(2);
  await expect(page.locator('.pd-composer-status > [role=status]')).toBeVisible();
  const thread = page.url().split('/').at(-1)!;
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.includes('discussion-send')))).toEqual([]);
  const before = f.discussion.writes.length;
  if (outcome === 'failed') {await page.screenshot({path: testInfo.outputPath(`discussions-failed-first-message.png`)});}
  await page.reload(); await expect(composer).toHaveValue('Original first note');
  if (outcome === 'failed') {
    await page.getByRole('button', {name: 'New request', exact: true}).click();
    await composer.fill('Edited replacement, not ready to send');
    await page.reload(); await expect(composer).toHaveValue('Edited replacement, not ready to send');
    await page.locator(`.pm-thread-link[href$="${f.ids.threadIdea}"]`).click();
    await page.locator(`.pm-thread-link[href$="${thread}"]`).click();
    await expect(composer).toHaveValue('Edited replacement, not ready to send');
  } else {
    await expect(composer).toBeDisabled();
    await page.getByRole('button', {name: 'Check original request', exact: true}).click();
  }
  expect(f.discussion.writes).toHaveLength(before);
  const ref = {kind: 'requirement', id: 'req.login', revision_id: f.ids.planV3};
  await page.evaluate(href => {location.hash = href;}, discussHref({workspaceId: f.ids.workspaceId, refs: [ref], title: 'Topic B', from: 'requirements'}));
  await expect(page).toHaveURL(/\/discussions\/new\?/); await expect(page.locator('[data-discussion-title]')).toHaveText('Topic B');
  expect(parseDiscussRefs(page.url().split('?')[1]).refs).toEqual([ref]);
  await expect(page.locator('[data-discussion-back]')).toHaveText('Back to Requirements');
  await expect(composer).toHaveValue(''); await page.locator('[data-discussion-new]').click();
  await expect(page).toHaveURL(/\/discussions\/new$/); expect(f.discussion.writes).toHaveLength(before);
});

test('cancelled renames discard stale text and successful renames restore keyboard focus', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const rename = page.getByRole('button', {name: 'Rename', exact: true}), title = page.getByLabel('Discussion title', {exact: true});
  await rename.click(); await title.fill('Cancelled local title'); await title.press('Escape'); await expect(rename).toBeFocused();
  const data = f.discussion.details.get(f.ids.threadIdea); data.thread.title = 'Renamed elsewhere'; data.thread.version++;
  await page.reload(); await expect(page.locator('[data-discussion-title]')).toHaveText('Renamed elsewhere'); await expect(title).toBeHidden();
  await rename.press('Enter'); await expect(title).toHaveValue('Renamed elsewhere'); await title.fill('Reviewed title'); await title.press('Enter');
  await expect(page.locator('[data-discussion-title]')).toHaveText('Reviewed title'); await expect(rename).toBeFocused();
  expect(f.discussion.writes).toHaveLength(1);
});

test('refresh reads one proposal list and only its selected document, with revision details deferred to Plan', async ({page}) => {
  const f = demoFixture({discussions: true}), reads: string[] = [], revisionReads: string[] = []; let older = false;
  await mountConversation(page, false, async (input: any) => {
    if (input.method === 'GET') {reads.push(input.path); if (/\/plans\/pmrev_/.test(input.path)) revisionReads.push(input.path);}
    if (input.path.endsWith('/conversation-proposals/pmcp_' + '9'.repeat(32))) return {status: 409, data: {error: {code: 'PM_CONVERSATION_CHANGED', message: 'Old proposal unreadable'}}};
    const result = await f.dispatch(input);
    if (older && input.path.includes('/conversation-proposals?')) result.data.proposals.unshift({...result.data.proposals[0], proposal_id: 'pmcp_' + '9'.repeat(32), title: 'Older unreadable proposal'});
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await expect(page.locator('[data-discussion-ask]')).toBeEnabled();
  const revisions = () => revisionReads; expect(revisions()).toHaveLength(0);
  reads.length = 0; await page.locator('[data-discussion-ask]').click(); await expect(page.locator('[data-proposal-review]')).toBeEnabled();
  await expect(page.locator('[data-discussion-ask]')).toBeEnabled();
  expect(reads.filter(path => path.includes('/conversation-proposals?'))).toHaveLength(1);
  expect(reads.filter(path => /\/conversation-proposals\/pmcp_/.test(path))).toHaveLength(1);
  older = true; reads.length = 0; await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
  expect(reads.filter(path => path.includes('/conversation-proposals?'))).toHaveLength(1);
  expect(reads.filter(path => /\/conversation-proposals\/pmcp_/.test(path))).toHaveLength(1);
  await expect(page.locator('[data-proposal-review]')).toBeVisible(); expect(revisions()).toHaveLength(0);
  await openThreadTab(page, 'plan'); await expect.poll(() => revisions().length).toBe(2);
  await page.getByRole('tab', {name: 'Context', exact: true}).click(); await openThreadTab(page, 'plan');
  expect(revisions()).toHaveLength(2);
});

for (const locale of ['en', 'zh-TW']) for (const width of [390, 1440]) test(`centrally adopted proposal survives reload and links to the accepted revision ${locale} ${width}`, async ({browser, baseURL}, testInfo) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}}), page = await context.newPage();
  const f = demoFixture({locale, discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadFollow}`); await page.reload();
  const card = page.locator('[data-pm-plan-proposals]');
  await expect(card.locator('[data-status=adopted]')).toHaveText(locale === 'en' ? 'Adopted' : '已採用');
  await expect(card.locator('[data-proposal-again], [data-proposal-review]')).toHaveCount(0);
  await page.screenshot({path: testInfo.outputPath(`discussions-adopted-${locale}-${width}.png`)});
  await card.locator('[data-proposal-adopted-revision]').click();
  await expect(page.locator('[data-discussion-tab=plan]')).toBeVisible();
  await expect(page.locator(`[data-pm-revision="${f.ids.planV3}"] [data-pm-revision-open]`)).toBeFocused();
  await expect(page.locator(`[data-pm-revision="${f.ids.planV3}"] [data-pm-revision-open]`)).toBeInViewport();
  expect(f.discussion.writes).toHaveLength(0); await context.close();
});

for (const trigger of ['refresh', 'pm_run', 'pm_conversation_proposal']) test(`working clock stops with polling and ${trigger} checks the original operation`, async ({page}) => {
  const f = demoFixture({discussions: true}); f.discussion.running = true;
  const reads: string[] = [], events: any[] = [];
  await mountConversation(page, false, async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname === '/events') {const after = Number(url.searchParams.get('after') || 0), pending = events.filter(e => e.seq > after), cursor = Math.max(after, ...pending.map(e => e.seq));
      return {status: 200, data: {events: pending, next_cursor: cursor, head_cursor: cursor, has_more: false, sync: {checkpoint: {cursor, token: 'demo-' + cursor}}}};}
    if (input.method === 'GET' && input.path.startsWith('/operations/')) reads.push(input.path);
    return f.dispatch(input);
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click();
  const working = page.locator('[data-discussion-working]'); await expect(working).toContainText('PM is working');
  await expect(working.locator('[data-discussion-elapsed]')).toHaveAttribute('aria-hidden', 'true');
  await page.evaluate(() => {const node = document.querySelector('[data-discussion-working] [role=status]')!;
    (window as any).statusAnnouncements = 0; new MutationObserver(() => (window as any).statusAnnouncements++).observe(node, {childList: true, subtree: true, characterData: true});});
  await expect(working).toHaveText('Status not checked', {timeout: 15000});
  expect(reads).toHaveLength(6); await expect(working.locator('[data-discussion-elapsed]')).toHaveText('');
  await page.waitForTimeout(1100); expect(await page.evaluate(() => (window as any).statusAnnouncements)).toBe(1);
  const op: any = [...f.discussion.operations.values()][0]; op.status = 'succeeded';
  if (trigger === 'refresh') await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  else events.push({seq: 1, resource_type: trigger, resource_id: 'analysis', kind: trigger + '.updated'});
  await expect(working).toHaveText('This discussion’s last PM run: PM finished', {timeout: 10000}); await expect(page.locator('[data-discussion-ask]')).toHaveText('Make a plan');
  expect(reads).toHaveLength(7); expect(new Set(reads)).toEqual(new Set(['/operations/' + op.operation_id]));
  expect(f.discussion.writes).toHaveLength(1);
});

test('a list-opened mobile thread returns to its filtered discussion list', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844}); const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions`);
  await page.getByRole('searchbox', {name: 'Search', exact: true}).fill('Delivery');
  await page.locator(`.pm-thread-link[href$="${f.ids.threadFollow}"]`).click();
  await expect(page.locator('[data-discussion-back]')).toHaveText('Back to Discussions');
  await page.locator('[data-discussion-back]').click(); await expect(page).toHaveURL(/\/discussions$/);
  await expect(page.getByRole('searchbox', {name: 'Search', exact: true})).toHaveValue('Delivery');
  await expect(page.locator('.pm-thread-link:visible')).toHaveCount(1);
  await page.locator(`.pm-thread-link[href$="${f.ids.threadFollow}"]`).click(); await page.reload(); await expect(page.locator('[data-discussion-back]')).toHaveText('Back to Discussions');
  expect(f.discussion.writes).toHaveLength(0);
});

test('completed launch storage is pruned and origins use a bounded index', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  const launch = discussHref({workspaceId: f.ids.workspaceId, title: 'First launch', from: 'overview'});
  await page.goto('/dashboard/' + launch); await page.locator('[data-discussion-composer]').fill('First note'); await page.getByRole('button', {name: 'Save note only', exact: true}).click();
  await expect(page.locator('.pm-notes')).toContainText('First note');
  await page.evaluate(() => {
    const indexKey = Object.keys(localStorage).find(key => key.includes('discussion-origin') && key.endsWith('.index'))!;
    const prefix = indexKey.slice(0, -5), ids = Array.from({length: 45}, (_, i) => 'pmt_' + (1000 + i).toString(16).padStart(32, '0'));
    for (const id of ids) localStorage.setItem(prefix + id, JSON.stringify({from: 'overview'})); localStorage.setItem(indexKey, JSON.stringify(ids));
  });
  await page.evaluate(href => {location.hash = href;}, discussHref({workspaceId: f.ids.workspaceId, title: 'Second launch', from: 'requirements'}));
  await page.locator('[data-discussion-composer]').fill('Second note'); await page.getByRole('button', {name: 'Save note only', exact: true}).click();
  await expect(page.locator('.pm-notes')).toContainText('Second note');
  const stored = await page.evaluate(() => ({origins: Object.keys(localStorage).filter(key => key.includes('discussion-origin')),
    launches: Object.keys(localStorage).filter(key => key.includes('.discussion.') && key.includes('pm.thread.create'))}));
  expect(stored.origins).toHaveLength(41); expect(stored.launches).toHaveLength(0);
});

for (const locale of ['en', 'zh-TW']) test(`invalid proposal picker uses a concise review status ${locale}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale}), page = await context.newPage(), f = demoFixture({locale, discussions: true});
  await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click(); await expect(page.locator('[data-proposal-review]')).toBeVisible();
  Object.assign(f.discussion.proposals.at(-1), {validation: {status: 'needs_review', errors: []}, document: null, document_digest: null, adoptable: false, adoptable_reason: 'invalid_proposal'});
  await page.getByRole('button', {name: locale === 'en' ? 'Refresh' : '重新整理', exact: true}).click();
  await expect(page.locator('[data-proposal-history] option').last()).toHaveText(locale === 'en' ? 'Proposed update · Needs review' : '建議更新 · 待審閱');
  await expect(page.locator('[data-proposal-review]')).toHaveCount(0); expect(f.discussion.writes).toHaveLength(1); await context.close();
});

test('central adoption keeps an uncertain original receipt recoverable without another review', async ({page}) => {
  const f = demoFixture({discussions: true}), checks: string[] = []; let op: any;
  await mountConversation(page, false, async (input: any) => {
    if (input.method === 'GET' && input.path.startsWith('/operations/')) checks.push(input.path);
    const result = await f.dispatch(input);
    if (input.body?.action === 'pm.conversation.adopt') {op = [...f.discussion.operations.values()].find((row: any) => row.action === 'pm.conversation.adopt'); op.status = 'uncertain'; result.data.operation.status = 'uncertain';}
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click(); await page.locator('[data-proposal-review]').click();
  await page.getByLabel('Reason for adopting this update').fill('Reviewed central update'); await page.getByRole('button', {name: 'Adopt', exact: true}).click();
  await expect.poll(() => f.discussion.writes.length).toBe(2);
  await expect(page.locator('[data-pm-action="pm.conversation.adopt"] .pm-receipt')).toContainText('Uncertain');
  await page.reload();
  const card = page.locator('[data-pm-plan-proposals]'); await expect(card.locator('[data-status=adopted]')).toBeVisible();
  await expect(card.locator('[data-proposal-review]')).toHaveCount(0); await expect(card.locator('[data-proposal-again]')).toHaveCount(0);
  await card.locator('[data-proposal-recovery] > summary').click(); await card.getByRole('button', {name: 'Check original request', exact: true}).click();
  expect(checks.every(path => path === '/operations/' + op.operation_id)).toBe(true); expect(f.discussion.writes).toHaveLength(2);
});

test('the desktop list header leaves room for Search and an accessible New discussion icon', async ({page}, testInfo) => {
  await page.setViewportSize({width: 1440, height: 900}); const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const header = page.locator('.pd-list-header'), search = header.getByRole('searchbox', {name: 'Search', exact: true});
  await expect(search).toHaveAttribute('placeholder', 'Search'); expect((await search.boundingBox())!.width).toBeGreaterThan(180);
  const create = header.getByRole('button', {name: 'New discussion', exact: true}); await expect(create).toHaveText(''); await expect(create).toHaveAttribute('title', 'New discussion');
  await header.screenshot({path: testInfo.outputPath(`discussions-list-header-en-1440.png`)});
});

test('reload during the first thread mount preserves the staged intent without auto-sending', async ({page}) => {
  const f = demoFixture({discussions: true}); let hold = true, release: () => void;
  const pending = new Promise<void>(resolve => {release = resolve;});
  await mountConversation(page, false, async (input: any) => {
    if (hold && input.path.includes('/conversation-proposals?')) await pending;
    return f.dispatch(input);
  });
  await page.goto('/dashboard/' + discussHref({workspaceId: f.ids.workspaceId, title: 'Interrupted first mount'}));
  await page.locator('[data-discussion-composer]').fill('Keep my first note'); await page.getByRole('button', {name: 'Save note only', exact: true}).click();
  await expect(page).toHaveURL(/\/discussions\/pmt_[a-f0-9]{32}$/);
  await expect(page.locator('[data-discussion-composer]')).toHaveValue('Keep my first note');
  const saved = await page.evaluate(() => {const key = Object.keys(localStorage).find(key => key.includes('.pm.message.append.'))!; return JSON.parse(localStorage.getItem(key)!);});
  expect(saved.intent.request.params.content).toBe('Keep my first note');
  expect(await page.evaluate(() => Object.keys(localStorage).some(key => key.includes('discussion-send')))).toBe(false);
  hold = false; await page.reload(); release!();
  await expect(page.getByRole('button', {name: 'Check original request', exact: true})).toBeEnabled();
  expect(f.discussion.writes.map((row: any) => row.body.action)).toEqual(['pm.thread.create']);
  await page.getByRole('button', {name: 'Check original request', exact: true}).click();
  await expect(page.locator('.pm-notes')).toContainText('Keep my first note'); expect(f.discussion.writes).toHaveLength(2);
  expect(f.discussion.writes[1].idempotency_key).toBe(saved.intent.key);
});


test('adoption recovery opens Details and tolerates a completed receipt', async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale: 'zh-TW', viewport: {width: 390, height: 844}});
  const page = await context.newPage(), f = demoFixture({locale: 'zh-TW', discussions: true}), checks: string[] = [];
  // Count explicit recovery reads here. A restored form also schedules its own
  // acknowledged-receipt read after 250 ms; wall time must not race these steps.
  const at = Date.now(); await page.clock.pauseAt(at);
  let op: any;
  await mountConversation(page, false, async input => {
    if (input.method === 'GET' && input.path.startsWith('/operations/')) checks.push(input.path);
    const result = await f.dispatch(input);
    if (input.body?.action === 'pm.conversation.adopt') {
      op = [...f.discussion.operations.values()].find((row: any) => row.action === 'pm.conversation.adopt');
      op.status = 'uncertain'; result.data.operation.status = 'uncertain';
    }
    return result;
  });
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  await page.locator('[data-discussion-ask]').click();
  await expect(page.locator('[data-proposal-review]')).toBeEnabled(); await expect(page.locator('[data-discussion-panel]')).toBeHidden();
  await page.locator('[data-proposal-review]').click();
  await page.getByLabel('採用這組更新的理由', {exact: true}).fill('採用已審閱的範圍並保留原請求。');
  await page.getByRole('button', {name: '採用', exact: true}).click();
  await expect(page.locator('[data-pm-action="pm.conversation.adopt"] .pm-receipt')).toContainText('結果不明');
  await page.reload();
  const card = page.locator('[data-pm-plan-proposals]');
  await expect(card.locator('[data-proposal-adopted]')).toBeVisible();
  const retry = card.getByRole('button', {name: '查回原請求', exact: true, includeHidden: true});
  const saved = card.locator('[data-pm-action="pm.conversation.adopt"] [data-pm-saved]');
  await expect(retry).toHaveCount(1); await expect(retry).toBeHidden();
  await expect(page.locator('[data-discussion-panel]')).toBeHidden();
  await card.locator('[data-proposal-recovery] > summary').click(); await recoverOriginalAdoption(card, '查回原請求');
  await expect.poll(() => checks.length).toBe(1); await expect(retry).toBeEnabled();
  // The poll can observe this count before an asynchronous receipt changes the
  // name and removes the old name from the Details locator's `has` filter.
  expect(await retry.count()).toBe(1);
  op.status = 'succeeded';
  await retry.click(); await expect(saved).toHaveCount(1);
  await expect(retry).toHaveCount(0);
  // Resume the recovery step with that now-stale observation. It must finish,
  // leaving the original Saved evidence intact and never sending a new adoption.
  await recoverOriginalAdoption(card, '查回原請求');
  await expect(card.locator('[data-proposal-adopted]')).toBeVisible();
  await expect(saved).toHaveCount(1);
  await expect(saved).toBeVisible();
  await expect(card.locator('[data-pm-action="pm.conversation.adopt"] button[type=submit]')).toBeHidden();
  expect(checks).toEqual(['/operations/' + op.operation_id, '/operations/' + op.operation_id]);
  expect(f.discussion.writes).toHaveLength(2);
  expect(f.discussion.writes.filter((row: any) => row.body.action === 'pm.conversation.adopt')).toHaveLength(1);
  await context.close();
});
