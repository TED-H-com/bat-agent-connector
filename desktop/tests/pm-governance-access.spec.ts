import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {action, governanceFixture, mount, boot, openAction, reason, snapshotId} from './pm-governance-fixture';

const region = '[data-pm-governance]', detail = '[data-pm-governance-detail]';
const refresh = (page: Page) => page.evaluate(() => (window as any).refreshGovernance());
for (const native of [false, true]) test.describe(native ? 'native source withdrawal' : 'web source withdrawal', () => {
  test('withdrawn selection retains other findings and the original pending request', async ({page}) => {
    const f = governanceFixture(), a = f.state.records[0], b = f.state.records[1];
    await mount(page, f, 'en', native); await reason(page, 'revise', 'Retain this original request');
    f.state.lost = true; await action(page, 'revise').getByRole('button', {name: 'Save finding revision', exact: true}).click();
    await expect(action(page, 'revise')).toContainText('Reply unavailable');
    const original = structuredClone(f.state.writes[0]);
    const saved = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.includes('pm.governance.revise'))));
    f.state.hiddenRecords = [a.record.record_id]; await refresh(page);
    await expect(page.locator(`[data-pm-governance-row="${b.record.record_id}"]`)).toBeVisible();
    await expect(page.locator(detail)).toHaveCount(0); await expect(action(page, 'revise')).toHaveCount(0);
    expect(await page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.includes('pm.governance.revise'))))).toEqual(saved);
    await openAction(page, 'record'); await expect(action(page, 'record').getByLabel('Finding title', {exact: true})).toBeEnabled();
    await page.reload(); await boot(page);
    await page.locator(`[data-pm-governance-row="${b.record.record_id}"]`).getByRole('button').click();
    await expect(page.locator(detail)).toContainText(b.revision.document.title);
    expect(f.state.writes).toHaveLength(1);
    f.state.hiddenRecords = []; await refresh(page);
    await page.locator(`[data-pm-governance-row="${a.record.record_id}"]`).getByRole('button').click();
    await action(page, 'revise').getByRole('button', {name: 'Check original request', exact: true}).click();
    await expect.poll(() => f.state.writes.length).toBe(2);
    expect(f.state.writes.at(-1)).toEqual(original); expect(f.state.ops.size).toBe(1);
  });
  test('a readable selection beyond the first page is retained', async ({page}) => {
    const f = governanceFixture(), selected = f.state.records[1]; await mount(page, f, 'en', native);
    await page.locator(`[data-pm-governance-row="${selected.record.record_id}"]`).getByRole('button').click();
    await expect(page.locator(detail)).toContainText(selected.revision.document.title);
    f.state.pageSize = 1; await refresh(page);
    await expect(page.locator('[data-pm-governance-row]')).toHaveCount(1);
    await expect(page.locator(detail)).toHaveAttribute('data-pm-governance-detail', selected.record.record_id);
    expect(f.state.writes).toEqual([]);
  });
  test('an unavailable creation snapshot remains selectable without hiding readable findings', async ({page}) => {
    const f = governanceFixture(), validId = 'pmsnap_' + '9'.repeat(32);
    const valid = {...structuredClone(f.state.snapshots[0]), snapshot_id: validId, title: 'Still available source'};
    f.state.snapshots.unshift(valid);
    await mount(page, f, 'en', native); await openAction(page, 'record');
    await action(page, 'record').getByLabel('Finding title', {exact: true}).fill('Keep this unsent draft');
    f.state.hiddenSnapshots = [snapshotId]; f.state.hiddenRecords = [f.state.records[0].record.record_id];
    f.state.records[1].record.context.snapshot_ref.snapshot_id = validId;
    await refresh(page);
    await expect(page.locator('[data-pm-governance-row]')).toHaveCount(1);
    await openAction(page, 'record');
    const editor = action(page, 'record'); await expect(editor).toContainText('Original source access removed');
    await expect(editor.getByLabel('Finding title', {exact: true})).toHaveValue('Keep this unsent draft');
    await expect(editor.getByRole('checkbox')).toHaveCount(0);
    await expect(editor.getByRole('button', {name: 'Save finding', exact: true})).toBeDisabled();
    await editor.getByLabel('Source snapshot', {exact: true}).selectOption(validId);
    await expect(editor.getByRole('checkbox').first()).toBeEnabled();
    await page.locator('[data-pm-governance-row]').getByRole('button').click();
    await expect(page.locator(detail)).toContainText('Use the existing mail provider');
    expect(f.state.writes).toEqual([]);
  });
  test('workspace access loss still hides all findings and editors', async ({page}) => {
    const f = governanceFixture(); await mount(page, f, 'en', native); f.state.failRead = true;
    await refresh(page); await expect(page.locator(region)).toContainText('Workspace access removed');
    await expect(page.locator('[data-pm-governance-row]')).toHaveCount(0);
    await expect(page.locator(detail)).toHaveCount(0); await expect(action(page, 'record')).toBeHidden();
    expect(f.state.writes).toEqual([]);
  });
  test('workspace withdrawal during selected-source recovery cannot expose the earlier list', async ({page}) => {
    const f = governanceFixture(); await mount(page, f, 'en', native);
    f.state.hiddenRecords = [f.state.records[0].record.record_id]; f.state.workspaceDenied = true;
    await refresh(page); await expect(page.locator(region)).toContainText('Workspace access removed');
    await expect(page.locator('[data-pm-governance-row]')).toHaveCount(0);
    await expect(page.locator(detail)).toHaveCount(0); await expect(action(page, 'record')).toHaveCount(0);
    expect(f.state.writes).toEqual([]);
  });
});
for (const width of [390, 768, 1440]) test(`source recovery layout ${width}`, async ({page}) => {
  test.skip(!process.env.PM_GOVERNANCE_ACCESS_SCREENSHOTS, 'Optional review screenshots');
  await page.setViewportSize({width, height: 900}); const f = governanceFixture(); await mount(page, f);
  await mkdir(process.env.PM_GOVERNANCE_ACCESS_SCREENSHOTS!, {recursive: true});
  await page.screenshot({path: `${process.env.PM_GOVERNANCE_ACCESS_SCREENSHOTS}/before-${width}.png`, fullPage: true});
  f.state.hiddenRecords = [f.state.records[0].record.record_id]; await refresh(page);
  await expect(page.locator('[data-pm-governance-row]')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: `${process.env.PM_GOVERNANCE_ACCESS_SCREENSHOTS}/after-${width}.png`, fullPage: true});
});
