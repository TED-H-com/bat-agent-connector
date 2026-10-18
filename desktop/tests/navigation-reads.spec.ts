import {test, expect} from '@playwright/test';
import {workspaceFixture, mountWorkspace, workspaceProject} from './workspace-fixture';

for (const native of [false, true]) for (const summary of [true, false]) {
  test(`${native ? 'IPC' : 'HTTP'} navigation ${summary ? 'summary' : 'legacy'} keeps details on demand`, async ({page}) => {
    const f = workspaceFixture(), original = f.dispatch, originalCaps = f.caps;
    f.caps = () => ({...originalCaps(), features: {...originalCaps().features,
      ...(summary ? {project_navigation: {version: 1}} : {})}});
    const item = f.fixture.data.items[0]; item.title = 'Review original work';
    item.goal = 'Original goal is available only after selecting the work.';
    item.request = 'Original requirements remain available. '.repeat(200);
    f.data.detail.work_items = [item]; f.data.detail.work = []; f.data.linkedItem = true;
    const reads: string[] = [];
    f.dispatch = async input => {
      const url = new URL(input.path, 'http://fixture');
      const reply = await original(input);
      if (url.pathname === '/capabilities') reply.data = f.caps();
      if (url.pathname === '/bootstrap') reply.data.capabilities = f.caps();
      if (url.pathname === '/projects/' + workspaceProject) {
        reads.push(input.path);
        if (url.searchParams.get('projection') === 'navigation') {
          reply.data = {...reply.data, projection:'navigation', work_items:[{
            work_item_id:item.work_item_id, title:item.title, project_id:workspaceProject,
            completion:{display_state:item.completion.display_state,pending:item.completion.pending,approved:false},
            children:[], step_summary:{total:0,done:0,next:null}, version:item.version
          }]};
        }
      }
      return reply;
    };
    await mountWorkspace(page, native, f);
    await page.goto('/dashboard/?workbench=1#/work-list');
    const search = page.getByRole('searchbox', {name:'Search loaded projects and work'});
    const row = page.locator(`[data-work-item="${item.work_item_id}"]`);
    await expect(row).toBeVisible();
    expect(reads.length).toBeGreaterThan(0);
    expect(reads.every(path => new URL(path,'http://fixture').searchParams.has('projection') === summary)).toBe(true);
    await search.fill('Review original'); await search.focus();
    const before = reads.length;
    f.data.events.push(...Array.from({length:12}, (_,index) => ({seq:index+1,
      resource_type:'work_item', resource_id:item.work_item_id, kind:'work_item.updated'})));
    await expect.poll(() => f.data.after).toBe(12);
    expect(reads.length - before).toBe(1);
    await expect(search).toHaveValue('Review original'); await expect(search).toBeFocused();
    await row.click();
    await expect(page.locator('#main .wc-goal')).toBeVisible();
    await expect(page.locator('#main .wc-goal')).toHaveText(item.goal);
    expect(f.data.posts).toEqual([]);
  });
}
