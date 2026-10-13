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
    const dir = `${process.env.TMPDIR || '/tmp'}/shots`; await mkdir(dir, {recursive: true});
    page.on('pageerror', error => errors.push(error.message));
    const f = demoFixture({locale, discussions: true}), zh = locale === 'zh-TW';
    await mountConversation(page, false, f.dispatch);
    const ref = {kind: 'governance', id: 'pmgov_' + 'a'.repeat(32)};
    const href = discussHref({workspaceId: f.ids.workspaceId, refs: [ref], title: f.copy.blocker, from: 'overview'});
    expect(parseDiscussRefs(href.split('?')[1]).refs).toEqual([ref]);
    // Producer views share this exact link; the receiving contract is exercised here.
    await page.goto('/dashboard/' + href);
    const composer = page.locator('[data-discussion-composer]');
    await expect(composer).toBeFocused();
    await expect(page.getByLabel(zh ? '討論標題' : 'Discussion title', {exact: true})).toHaveValue(f.copy.blocker);
    await expect(page.locator('[data-discussion-context=governance]')).toBeVisible();
    await composer.fill(zh ? '請整理這個阻礙，保留尚未確認的預算。' : 'Review this blocker and keep the budget uncertainty explicit.');
    await page.reload(); await expect(composer).not.toHaveValue(''); expect(f.discussion.writes).toHaveLength(0);
    await composer.press('Tab'); await page.getByRole('button', {name: zh ? '傳送' : 'Send', exact: true}).press('Enter');
    await expect(page).toHaveURL(/\/discussions\/pmt_[a-f0-9]{32}$/);
    await expect(page.locator('.pm-notes')).toContainText(zh ? '請整理這個阻礙' : 'Review this blocker');
    expect(f.discussion.writes.map((w: any) => w.body.action)).toEqual(['pm.thread.create', 'pm.message.append']);
    expect(f.discussion.writes[0].body.params.context).toEqual([ref]);
    await page.reload(); expect(f.discussion.threads).toHaveLength(4);
    await page.locator('[data-discussion-ask]').click();
    await expect(page.locator('[data-pm-plan-proposals]')).toContainText(f.copy.proposalSummary);
    const rename = page.locator('[data-pm-action="pm.thread.rename"]');
    await rename.getByLabel(zh ? '討論標題' : 'Discussion title').fill(zh ? '解除復原阻礙' : 'Unblock recovery');
    await rename.getByRole('button', {name: zh ? '重新命名' : 'Rename', exact: true}).click();
    await expect(page.locator('[data-proposal-review]')).toBeInViewport();
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
    await expect(page.locator('.pm-brief')).not.toContainText(/\b(undefined|null|NaN)\b/);
    if (width < 1200) {await page.keyboard.press('Escape'); await expect(page.locator('[data-discussion-panel-toggle]')).toBeFocused();}
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('#main')).not.toContainText(/pm_mode_|\b(undefined|null|NaN)\b/);
    await page.screenshot({path: `${dir}/discussions-${locale}-${width}.png`});
    if (width === 1440 && !zh) {await page.evaluate(() => document.documentElement.dataset.theme = 'dark'); await page.screenshot({path: `${dir}/discussions-en-1440-dark.png`, animations: 'disabled'});}
    await page.locator('[data-discussion-back]').first().click(); await expect(page).toHaveURL(/\/overview$/);
    expect(errors).toEqual([]); await context.close();
  });
}

test('withdrawn context drops both labels and IDs from list, header and panel', async ({page}) => {
  const f = demoFixture({discussions: true}); await mountConversation(page, false, f.dispatch);
  const data = f.discussion.details.get(f.ids.threadFollow);
  data.thread.context = [{kind: 'governance', id: 'pmgov_' + 'c'.repeat(32), position: 0, available: true, label: 'Current sensitive label', bound_label: 'Earlier sensitive label'}];
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadFollow}`);
  const chips = page.locator('[data-discussion-context=governance]');
  await expect(chips.first()).toHaveAttribute('title', 'Originally: Earlier sensitive label');
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
  await expect(page.locator('[data-proposal-reason]')).toHaveText('Out of date');
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
  await expect(page.locator('[data-discussion-working]')).toHaveText('Uncertain');
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
  await tabTo(page.getByLabel('Discussion title', {exact: true})); await page.keyboard.press('ControlOrMeta+A'); await page.keyboard.type('Resolve the blocker');
  await tabTo(page.getByRole('button', {name: 'Rename', exact: true})); await page.keyboard.press('Enter');
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
  await expect(page.locator('[data-proposal-reason]')).toHaveText('Out of date');
  await expect(page.getByRole('button', {name: 'Adopt this update', exact: true})).toBeDisabled();
});
