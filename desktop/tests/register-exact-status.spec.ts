import {expect, test} from '@playwright/test';
import {recordedStatuses, registerStatusFixture} from './register-status-fixture.ts';
import {reviewFixture} from './requirements-review-fixture.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 1000, 390]) {
  test(`Register retains exact statuses while filtering by group ${locale} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    const f = registerStatusFixture(locale); await f.mount(page, 'issues');
    const panel = page.locator('[data-pm-register]');
    await expect(panel.locator('[data-row-key]')).toHaveCount(recordedStatuses.length);
    for (const item of recordedStatuses) {
      const row = panel.locator(`[data-row-key="plan_revision.exact_${item.status}"]`);
      const pill = row.locator(`[data-record-status="${item.status}"]`);
      await expect(pill).toBeVisible(); await expect(pill).toHaveText(locale === 'en' ? item.en : item.zh);
      await expect(pill).toHaveAttribute('data-status', item.group);
      expect(await pill.evaluate(node => {
        const bounds = node.getBoundingClientRect(), cell = node.closest('td')?.getBoundingClientRect();
        return node.scrollWidth <= node.clientWidth && (!cell || bounds.right <= cell.right);
      })).toBe(true);
    }
    await panel.locator('[data-reg-status]').selectOption('done');
    await expect(panel.locator('[data-row-key]')).toHaveCount(3);
    await expect(panel.locator('[data-row-key="plan_revision.exact_adopted"]')).toBeVisible();
    await expect(panel.locator('[data-row-key="plan_revision.exact_reported_done"]')).toBeVisible();
    await panel.locator('[data-reg-status]').selectOption('');
    for (const item of recordedStatuses) {
      await panel.locator(`[data-row-key="plan_revision.exact_${item.status}"]`).click();
      const inspector = page.locator('[data-reg-inspector]');
      const label = locale === 'en' ? item.en : item.zh;
      await expect(inspector.locator('.reg-facts [data-record-status]')).toHaveText(label);
      await expect(inspector.locator('[data-reg-timeline] [data-record-status]')).toHaveText(label);
      await page.keyboard.press('Escape'); await expect(inspector).toBeHidden();
    }
    expect(f.writes).toEqual([]);
  });

  test(`Board preserves content approval separately from completion evidence ${locale} ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    const f = reviewFixture(locale); await f.mount(page, 'board');
    const board = page.locator('[data-pm-kanban]');
    const approved = board.locator('[data-pm-kanban-column=accepted] [data-pm-kanban-work]').first();
    await expect(approved).toBeVisible();
    const id = await approved.getAttribute('data-pm-kanban-work');
    const label = locale === 'en' ? 'Content approved' : '內容已核准';
    await expect(approved.locator('.sr-only')).toHaveText(label);
    await board.getByLabel(locale === 'en' ? 'Group by' : '分組方式', {exact: true}).selectOption('milestone');
    const regrouped = board.locator(`[data-pm-kanban-work="${id}"]`);
    await expect(regrouped.locator('.pm-board-state .ui-pill')).toBeVisible();
    await expect(regrouped.locator('.pm-board-state .ui-pill')).toHaveText(label);
    await regrouped.locator('[data-board-open]').click();
    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector.locator('[data-work-section=status] .pm-work-inline > .ui-pill')).toHaveText(label);
    await expect(inspector.locator('[data-work-section=result]')).toContainText(locale === 'en' ? 'Definition of done not assessed' : '完成定義尚未評估');
    expect(f.writes).toEqual([]);
  });
}
