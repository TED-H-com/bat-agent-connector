import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {openPmSection} from './pm-navigation.ts';

function fixture() {
  const f = demoFixture(), root = `/pm/workspaces/${f.ids.workspaceId}`;
  const reads: string[] = [], writes: unknown[] = [];
  let cursor = 0, polls = 0;
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    if (input.method !== 'GET') writes.push(input);
    else if (url.pathname !== '/events') reads.push(url.pathname);
    if (url.pathname === '/events') {
      polls++;
      return {status: 200, data: {events: Number(url.searchParams.get('after') || 0) < cursor
        ? [{seq: cursor, kind: 'pm_governance.updated', resource_type: 'pm_governance', resource_id: 'demo-record'}] : [],
        next_cursor: cursor, head_cursor: cursor, has_more: false, sync: {checkpoint: {cursor, token: `demo-${cursor}`}}}};
    }
    return f.dispatch(input);
  };
  return {...f, root, reads, writes, dispatch, publish: () => ++cursor, polls: () => polls};
}
type Fixture = ReturnType<typeof fixture>;
const common = (f: Fixture) => ['/browser-session', '/capabilities', '/bootstrap', '/pm/workspaces', f.root, f.root + '/threads',
  '/projects', '/projects', `/projects/${f.ids.projectId}`];
const deliveries = (f: Fixture) => Object.values(f.ids.work).map(id => f.root + `/work/${id}/deliveries`);
const sessions = (f: Fixture) => Object.values(f.ids.sessions).map(id => `/sessions/${f.ids.host}/${id}`);
const viewReads = (f: Fixture): Record<string, string[]> => ({
  overview: [f.root + '/overview', f.root + `/plans/${f.ids.planV3}`], discussions: [],
  requirements: ['/plans/accepted', '/kanban', '/issue-mappings', '/quality-reviews', '/roadmap', '/plans/head'].map(path => f.root + path).concat(deliveries(f), sessions(f)),
  schedule: [f.root + '/schedule'], dependencies: [f.root + '/dependencies', f.root + '/dependencies/revisions'],
  issues: [f.root + '/register'], agents: [f.root + '/resources'],
  board: ['/kanban', '/plans/accepted', '/roadmap'].map(path => f.root + path).concat(deliveries(f), sessions(f)),
});
async function mounted(page: Page, f: Fixture, view: string) {
  await mountConversation(page, false, f.dispatch);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/${view}`);
  await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
}
async function settled(f: Fixture) {
  const polls = f.polls(); await expect.poll(f.polls).toBeGreaterThan(polls);
}
for (const view of ['overview', 'discussions', 'requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board']) {
  test(`direct ${view} loads only its projection and shared shell reads`, async ({page}, info) => {
    const f = fixture(); await mounted(page, f, view);
    const expected = common(f).concat(viewReads(f)[view]).sort();
    await expect.poll(() => [...f.reads].sort()).toEqual(expected);
    await settled(f); expect([...f.reads].sort()).toEqual(expected); expect(f.writes).toEqual([]);
    await info.attach('initial-requests', {body: JSON.stringify(f.reads, null, 2), contentType: 'application/json'});
  });
}
for (const section of ['ui_governance', 'ui_repairs', 'ui_follow_up', 'ui_meetings']) {
  test(`first opening ${section} loads once and retains its data on reopen`, async ({page}) => {
    const f = fixture(), view = section === 'ui_meetings' ? 'discussions' : 'issues';
    await mounted(page, f, view); await settled(f); const before = f.reads.length;
    await openPmSection(page, view, section);
    const expected: Record<string, string[]> = {
      ui_governance: ['/governance', '/plans/accepted', '/snapshots', `/snapshots/${f.ids.snap2}`],
      ui_repairs: ['/maintenance-policies/current', '/release-baselines/current', '/lifecycle', '/maintenance-findings/current', '/maintenance-repairs'],
      ui_follow_up: ['/plans/head', '/follow-ups', '/follow-ups/latest', '/follow-up-assessments'],
      ui_meetings: ['/meetings', '/sprints', '/snapshots', '/meetings/pmmeet_00000000000000000000000000000001'],
    };
    const paths = expected[section].map(path => f.root + path).sort();
    await expect.poll(() => f.reads.slice(before).sort()).toEqual(paths);
    await settled(f); expect(f.reads.slice(before).sort()).toEqual(paths);
    const summary = page.locator(`[data-pm-section="${section}"] > details > summary`);
    await summary.click(); await summary.click(); await settled(f);
    expect(f.reads.slice(before).sort()).toEqual(paths); expect(f.writes).toEqual([]);
  });
}
test('events defer hidden views and closed panels until one opening read', async ({page}) => {
  const f = fixture(); await mounted(page, f, 'issues'); await settled(f); f.publish();
  await expect.poll(() => f.reads.filter(path => path === f.root + '/register').length).toBe(2);
  await settled(f);
  expect(f.reads.filter(path => path === f.root + '/schedule')).toHaveLength(0);
  expect(f.reads.filter(path => path === f.root + '/governance')).toHaveLength(0);
  await openPmSection(page, 'schedule');
  await expect.poll(() => f.reads.filter(path => path === f.root + '/schedule').length).toBe(1);
  await settled(f); expect(f.reads.filter(path => path === f.root + '/schedule')).toHaveLength(1);
  await openPmSection(page, 'issues', 'ui_governance');
  await expect.poll(() => f.reads.filter(path => path === f.root + '/governance').length).toBe(1);
  await settled(f); expect(f.reads.filter(path => path === f.root + '/governance')).toHaveLength(1); expect(f.writes).toEqual([]);
});
