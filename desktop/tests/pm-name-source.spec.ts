import {mkdir} from 'node:fs/promises';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {openPmSection} from './pm-navigation.ts';

function fixture(locale = 'en', governance = false) {
  const f = demoFixture({locale}), root = `/pm/workspaces/${f.ids.workspaceId}`;
  const reads: string[] = [], writes: unknown[] = [], afters: number[] = [];
  const state = {cursor: 0, kind: 'pm_plan', title: '', denied: 0, nameDenied: 0, hold: false, held: false, released: false};
  let release = () => {};
  const gate = new Promise<void>(resolve => {release = resolve;});
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method !== 'GET') writes.push(input);
    if (path === '/events') {
      const after = Number(new URL(input.path, 'http://fixture').searchParams.get('after') || 0); afters.push(after);
      return {status: 200, data: {events: after < state.cursor ? [{seq: state.cursor, kind: 'pm.plan.adopted', resource_type: state.kind, resource_id: f.ids.workspaceId}] : [],
        head_cursor: state.cursor, next_cursor: state.cursor, has_more: false, sync: {checkpoint: {cursor: state.cursor, token: `name-${state.cursor}`}}}};
    }
    reads.push(path);
    if (path === root && state.denied) return {status: state.denied, data: {error: {code: 'FORBIDDEN', message: 'Workspace withdrawn'}}};
    const result = await f.dispatch(input);
    if (governance && ['/bootstrap', '/capabilities'].includes(path)) delete (path === '/bootstrap' ? result.data.capabilities : result.data).features.pm_register;
    if (path === root + '/plans/accepted') {
      if (state.nameDenied) return {status: state.nameDenied, data: {error: {code: 'FORBIDDEN', message: 'Name source withdrawn'}}};
      if (state.title) result.data.plan.document.requirements.find((row: any) => row.id === f.ids.requirements.email).text = state.title;
      if (state.hold) {state.held = true; await gate; state.released = true;}
    }
    return result;
  };
  return {...f, originalDispatch: f.dispatch, root, state, reads, writes, afters, dispatch, release, publish: (kind = 'pm_plan') => {state.kind = kind; state.cursor++;},
    count: (path: string) => reads.filter(read => read === root + path).length};
}
async function boot(page: Page, f: ReturnType<typeof fixture>, view = 'issues') {
  await mountConversation(page, false, f.dispatch); await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/${view}`);
  await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
}
async function settled(f: ReturnType<typeof fixture>) {
  const polls = f.afters.length; await expect.poll(() => f.afters.length).toBeGreaterThan(polls);
}
async function shot(page: Page, name: string) {
  const directory = process.env.BATC_DEMO_PEOPLE_SCREENSHOTS; if (!directory) return;
  await mkdir(directory, {recursive: true}); await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path: `${directory}/${name}.png`, fullPage: true, animations: 'disabled'});
}
for (const locale of ['en', 'zh-TW']) {
  test(`saved Governance requirement absent from the fixed snapshot keeps its known name ${locale}`, async ({page}) => {
    const f = fixture(locale, true), id = f.ids.requirements.email;
    await mountConversation(page, false, async input => {
      const result = await f.dispatch(input), path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/bootstrap' || path === '/capabilities') (path === '/bootstrap' ? result.data.capabilities : result.data).scopes.push('manage', 'start');
      if (path === f.root) result.data.workspace.membership.role = 'contributor';
      if (path === f.root + '/governance') for (const row of result.data.records) row.revision.document.affected_requirement_ids = [id];
      if (path.startsWith(f.root + '/governance/') && result.data.revision) result.data.revision.document.affected_requirement_ids = [id];
      if (path.startsWith(f.root + '/snapshots/') && result.data.snapshot?.baseline?.document) {
        result.data.snapshot.baseline.document.requirements = result.data.snapshot.baseline.document.requirements.filter((row: any) => row.id !== id);
      }
      return result;
    });
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/issues`); await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
    const plan = await f.originalDispatch({path: f.root + '/plans/accepted'}), name = plan.data.plan.document.requirements.find((row: any) => row.id === id).text;
    const editor = page.locator('[data-pm-action="pm.governance.revise"]');
    await expect(editor.getByRole('checkbox', {name, exact: true, includeHidden: true})).toBeChecked();
    await expect(editor).not.toContainText(locale === 'en' ? 'The original source is unavailable' : '原始來源目前無法讀取');
    expect(f.count('/plans/accepted')).toBe(1); expect(f.writes).toEqual([]);
  });
  test(`direct Issues names requirement chips without opening Requirements or Board ${locale}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width: 1440, height: 900}}), page = await context.newPage(), f = fixture(locale);
    try {
      const plan = await f.originalDispatch({path: f.root + '/plans/accepted'}), name = plan.data.plan.document.requirements.find((row: any) => row.id === f.ids.requirements.email).text;
      await boot(page, f);
      // Related facts move to the inspector when the table prioritizes titles.
      await page.locator('[data-reg-table] [data-row-key]').first().click();
      const chip = page.locator(`[data-reg-inspector] [data-reg-requirement="${f.ids.requirements.email}"]`);
      await expect(chip).toHaveAccessibleName(name); await expect(chip).toHaveText(name);
      // The eager baseline also used a generic Work chip before either work view loaded.
      await expect(page.locator('[data-reg-inspector] [data-reg-work]').first()).toHaveText(locale === 'en' ? 'Work' : '工作');
      expect(f.count('/plans/accepted')).toBe(1); expect(f.count('/kanban')).toBe(0); expect(f.count('/overview')).toBe(0);
      expect(f.reads.some(path => path.endsWith('/deliveries'))).toBe(false);
      await shot(page, `register-${locale}-1440`);
      await expect(page.locator(`[data-reg-inspector] [data-reg-requirement="${f.ids.requirements.email}"]`)).toHaveText(name);
      expect(f.writes).toEqual([]);
    } finally {await context.close();}
  });
  test(`direct Governance details name affected requirements from their existing plan read ${locale}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width: 1440, height: 900}}), page = await context.newPage(), f = fixture(locale, true);
    try {
      const plan = await f.originalDispatch({path: f.root + '/plans/accepted'}), name = plan.data.plan.document.requirements.find((row: any) => row.id === f.ids.requirements.audit).text;
      await boot(page, f);
      const detail = page.locator('[data-pm-governance-detail]');
      await detail.locator(':scope > details > summary').click();
      const affected = detail.locator('.pm-brief-row').filter({has: page.getByRole('heading', {name: locale === 'en' ? 'Affected requirements' : '受影響需求', exact: true})});
      await expect(affected).toContainText(name); await expect(affected).not.toContainText(f.ids.requirements.audit);
      expect(f.count('/plans/accepted')).toBe(1); expect(f.count('/kanban')).toBe(0); expect(f.count('/overview')).toBe(0);
      await shot(page, `governance-detail-${locale}-1440`); expect(f.writes).toEqual([]);
    } finally {await context.close();}
  });
}

test('Issues reuses the loaded Overview requirement names', async ({page}) => {
  const f = fixture(); await boot(page, f, 'overview'); await openPmSection(page, 'issues');
  await expect(page.locator('[data-reg-table] [data-reg-requirement]').first()).not.toHaveText('Requirement');
  expect(f.count('/plans/accepted')).toBe(0); expect(f.count(`/plans/${f.ids.planV3}`)).toBe(1); expect(f.writes).toEqual([]);
});
test('a Register row without its own title uses the shared requirement name', async ({page}) => {
  const f = fixture(), id = f.ids.requirements.email;
  await mountConversation(page, false, async input => {
    const result = await f.dispatch(input);
    if (new URL(input.path, 'http://fixture').pathname === f.root + '/register') {
      Object.assign(result.data.rows[1], {title: null, statement: null, source: {kind: 'requirement', id}});
    }
    return result;
  });
  const plan = await f.originalDispatch({path: f.root + '/plans/accepted'}), name = plan.data.plan.document.requirements.find((row: any) => row.id === id).text;
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/issues`);
  await expect(page.locator('[data-reg-table] [data-row-key]').nth(1).locator('[data-column="title"]')).toContainText(name);
  expect(f.count('/plans/accepted')).toBe(1); expect(f.writes).toEqual([]);
});
test('a plan event invalidates a hidden name source and reads it once when Issues reopens', async ({page}) => {
  const f = fixture(); await boot(page, f); expect(f.count('/plans/accepted')).toBe(1);
  await openPmSection(page, 'schedule'); await settled(f); f.state.title = 'A newly adopted sign-in requirement'; f.publish();
  await expect.poll(() => f.reads.filter(path => path === f.root).length).toBe(2); await settled(f);
  expect(f.count('/plans/accepted')).toBe(1);
  await openPmSection(page, 'issues'); await expect(page.locator('[data-reg-table] [data-reg-requirement]').first()).toHaveText(f.state.title);
  await settled(f); expect(f.count('/plans/accepted')).toBe(2);
  await openPmSection(page, 'schedule'); await openPmSection(page, 'issues'); await settled(f);
  expect(f.count('/plans/accepted')).toBe(2); expect(f.writes).toEqual([]);
});
for (const code of [403, 404]) test(`workspace ${code} clears names and ignores a held plan reply`, async ({page}) => {
  const f = fixture(); await boot(page, f);
  f.state.title = 'Withdrawn requirement must not return'; f.state.hold = true; f.publish();
  try {
    await expect.poll(() => f.state.held).toBe(true); f.state.denied = code;
    await page.locator('[data-pm-refresh]').click();
    await expect(page.locator('[data-reg-table] [data-row-key]')).toHaveCount(0);
  } finally {f.release();}
  await expect.poll(() => f.state.released).toBe(true); await settled(f);
  await expect(page.locator('#main')).not.toContainText(f.state.title);
  await expect(page.locator('[data-reg-inspector]')).toHaveCount(0); expect(f.writes).toEqual([]);
});
test('a failed name read does not acknowledge the plan event checkpoint', async ({page}) => {
  const f = fixture(); await boot(page, f); await settled(f); f.state.nameDenied = 503; f.publish();
  await expect.poll(() => f.count('/plans/accepted')).toBeGreaterThan(1); await settled(f);
  expect(Math.max(...f.afters)).toBe(0);
  f.state.nameDenied = 0; await expect.poll(() => Math.max(...f.afters)).toBe(1);
  await expect(page.locator('[data-reg-table] [data-reg-requirement]').first()).not.toHaveText('Requirement'); expect(f.writes).toEqual([]);
});
