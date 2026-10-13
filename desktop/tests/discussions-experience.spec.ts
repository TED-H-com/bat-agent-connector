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
    await page.locator('[data-proposal-review]').click();
    const adopt = page.locator('[data-pm-action="pm.conversation.adopt"]');
    await adopt.getByLabel(zh ? '採用這組更新的理由' : 'Reason for adopting this update').fill(zh ? '保留未決事項並採用審閱後的範圍。' : 'Adopt the reviewed scope and preserve open decisions.');
    await adopt.getByRole('button', {name: zh ? '採用這組更新' : 'Adopt this update', exact: true}).click();
    await expect(page.locator('[data-proposal-adopted]')).toBeVisible();
    expect(f.discussion.writes.at(-1).body.action).toBe('pm.conversation.adopt');
    if (width < 1200) {await page.locator('[data-discussion-panel-toggle]').click(); await expect(page.locator('.pd-panel-overlay')).toHaveAttribute('aria-modal', 'true');}
    await page.getByRole('tab', {name: zh ? '計畫' : 'Plan', exact: true}).click();
    await page.locator('[data-pm-section=pm_brief_title] > details > summary').click();
    await expect(page.locator('.pm-brief')).not.toContainText(/\b(undefined|null|NaN)\b/);
    if (width < 1200) {await page.keyboard.press('Escape'); await expect(page.locator('[data-discussion-panel-toggle]')).toBeFocused();}
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('#main')).not.toContainText(/pm_mode_|\b(undefined|null|NaN)\b/);
    const dir = `${process.env.TMPDIR || '/tmp'}/shots`; await mkdir(dir, {recursive: true});
    await page.screenshot({path: `${dir}/discussions-${locale}-${width}.png`});
    if (width === 1440 && !zh) {await page.evaluate(() => document.documentElement.dataset.theme = 'dark'); await page.screenshot({path: `${dir}/discussions-en-1440-dark.png`});}
    await page.locator('[data-discussion-back]').first().click(); await expect(page).toHaveURL(/\/overview$/);
    expect(errors).toEqual([]); await context.close();
  });
}
