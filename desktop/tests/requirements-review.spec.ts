import {test, expect} from '@playwright/test';
import {reviewFixture} from './requirements-review-fixture.ts';
for (const mode of ['absent', 'partial', 'filtered', 'byte-limited', 'failed']) test(`B2 acceptance ${mode} remains unknown`, async ({page}) => {
  const f = reviewFixture();
  f.overrides.push((url, r) => {
    if (['/bootstrap', '/capabilities'].includes(url.pathname)) {
      const caps = url.pathname === '/bootstrap' ? r.data.capabilities : r.data; caps.features.pm_workspaces.quality_reviews = mode !== 'absent';
    }
    if (url.pathname.endsWith('/quality-reviews')) return mode === 'failed' ? {status: 413, data: {error: {code: 'PM_QUALITY_LIMIT', message: 'Too large'}}}
      : {status: 200, data: {workspace_id: f.ids.workspaceId, reviews: [], next_cursor: mode === 'partial' ? 5 : null,
        visibility_filtered: mode === 'filtered', coverage: {byte_limited: mode === 'byte-limited'}}};
    return r;
  });
  await f.mount(page, 'requirements');
  const row = page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"]`);
  await expect(row.locator('[data-column=acceptance] [data-status=unknown]')).toHaveCount(1);
  await expect(page.locator('[data-pm-work-meta]')).not.toContainText('0 accepted');
  await row.locator('[data-column=title]').click();
  await expect(page.locator('[data-work-inspector]')).toContainText('Acceptance unknown');
  await expect(page.locator('[data-work-section=result]')).not.toContainText('No result recorded');
  expect(f.writes).toEqual([]);
});
for (const mode of ['partial', 'filtered', 'absent']) test(`B3 delivery ${mode} marks every surface`, async ({page}) => {
  const f = reviewFixture();
  f.overrides.push((url, r) => {
    if (mode === 'absent' && ['/bootstrap', '/capabilities'].includes(url.pathname)) {
      const caps = url.pathname === '/bootstrap' ? r.data.capabilities : r.data; caps.features.pm_workspaces.engineering_delivery = false;
    }
    if (url.pathname.endsWith('/deliveries') && url.pathname.includes(f.ids.work.email)) {
      r.data.deliveries = r.data.deliveries.filter((row: any) => row.target.pull_number === 91);
      r.data.next_after = mode === 'partial' ? 2 : null; r.data.source_visibility_filtered = mode === 'filtered';
    } return r;
  });
  await f.mount(page, 'requirements');
  const row = page.locator(`[data-rtm-requirement="${f.ids.requirements.email}"]`), state = mode === 'absent' ? 'unknown' : 'partial';
  await expect(row.locator(`[data-column=pr] [data-status=${state}]`)).toHaveCount(1);
  await expect(row.locator(`[data-column=deploy] [data-status=${state}]`)).toHaveCount(1);
  await page.locator(`[data-rtm-toggle="${f.ids.requirements.email}"]`).click();
  await page.locator(`[data-rtm-select="${f.ids.work.email}"]`).click();
  await expect(page.locator('[data-work-section=delivery]')).toContainText(mode === 'absent' ? 'Delivery unknown' : 'Partial');
  await expect(page.locator('[data-work-section=delivery]')).not.toContainText('No delivery recorded');
  await page.locator('[data-pm-view=board]').click();
  await expect(page.locator(`[data-pm-kanban-work="${f.ids.work.email}"] [data-status=${state}]`)).toHaveCount(1);
  expect(f.writes).toEqual([]);
});
for (const view of ['requirements', 'board']) test(`B5 ${view} live ages preserve actions and menus`, async ({page}) => {
  const f = reviewFixture(); let tick = 0;
  f.overrides.push((url, r) => {
    if (url.pathname.includes('/kanban')) for (const card of r.data.cards || [r.data.card].filter(Boolean)) {
      card.flow.age_seconds = 432017.81 + tick++; card.flow.waiting_seconds = tick; card.work.updated_at = tick;
    } return r;
  });
  await f.mount(page, view, f.ids.work.audit);
  if (view === 'board') await page.locator(`[data-pm-kanban-work="${f.ids.work.audit}"]`).click();
  const inspector = page.locator('[data-work-inspector]'), discuss = inspector.locator('[data-work-discuss]');
  await expect(discuss).toBeVisible(); await discuss.focus();
  const oldReads = f.reads.length; f.emit(); await expect.poll(() => f.reads.length).toBeGreaterThan(oldReads + 5);
  await page.waitForTimeout(500); await expect(discuss).toBeFocused();
  await inspector.getByRole('button', {name: 'More actions', exact: true}).click(); await expect(inspector.getByRole('menu')).toBeVisible();
  const old = f.reads.length; f.emit(); await expect.poll(() => f.reads.length).toBeGreaterThan(old + 5);
  await page.waitForTimeout(500); await expect(inspector.getByRole('menu')).toBeVisible(); await expect(inspector.getByRole('menuitem').first()).toBeFocused();
  expect(f.writes).toEqual([]);
});

