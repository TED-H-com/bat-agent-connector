import {test, expect} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';

function fixture() {
  const base = conversationFixture();
  const counts = {doing: 0, awaiting_approval: 0, done: 0, total: 0};
  const project = (i: number, name: string, pinned = false) => ({project_id: 'prj_' + String(i).padStart(20, '0'),
    name, pinned, version: i + 1, description: '', children: [] as any[], counts});
  const projects = [project(1, 'Pinned', true), project(2, 'One'), project(3, 'Two')];
  projects[1].children.push(project(4, 'Child'));
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method !== 'GET') {
      base.data.writes.push(input);
      if (input.body.action === 'project.order') {
        const original = [...projects]; projects.splice(0, projects.length,
          ...input.body.params.order.map((id: string) => original.find(item => item.project_id === id)!));
      }
      return {status: 200, data: {operation: {operation_id: 'op_' + 'a'.repeat(32), status: 'succeeded'}}};
    }
    if (path === '/projects') return {status: 200, data: {projects, archived: []}};
    if (path.startsWith('/projects/')) return {status: 200, data: {project: projects.find(p => path.endsWith(p.project_id)), work: [], work_items: []}};
    const result = await base.dispatch(input);
    if (path === '/capabilities') result.data = {...result.data, scopes: ['observe', 'manage']};
    if (path === '/bootstrap') result.data.capabilities = {...result.data.capabilities, scopes: ['observe', 'manage']};
    return result;
  };
  return {data: base.data, projects, dispatch};
}

for (const native of [false, true]) {
  test(`drag only orders displayed siblings with versions, never changes parents (${native ? 'IPC' : 'Web'})`, async ({page}) => {
    const {data, projects, dispatch} = fixture(); await mountConversation(page, native, dispatch);
    await page.goto('/dashboard/#/projects');
    const rows = page.locator('#main .row.tree');
    const row = (name: string) => rows.filter({has: page.getByRole('link', {name, exact: true})});
    const first = [...projects];
    await row('One').getByRole('button', {name: 'Drag to reorder: One', exact: true}).dragTo(row('Child'));
    expect(data.writes).toHaveLength(0);
    await row('One').getByRole('button', {name: 'Drag to reorder: One', exact: true}).dragTo(row('Pinned'));
    expect(data.writes).toHaveLength(0);
    await row('One').getByRole('button', {name: 'Drag to reorder: One', exact: true}).dragTo(row('Two'));
    await expect.poll(() => data.writes.length).toBe(1);
    expect(data.writes[0].body).toEqual({action: 'project.order', target: {},
      params: {parent_id: '', order: [first[0].project_id, first[2].project_id, first[1].project_id]},
      preconditions: {before: first.map(p => p.project_id), expected_versions: Object.fromEntries(first.map(p => [p.project_id, p.version]))}});
    expect(projects.find(p => p.name === 'One')!.children[0].name).toBe('Child');
    // Accessible buttons use the same central preconditions as dragging.
    await row('One').getByRole('button', {name: 'More', exact: true}).click();
    await page.locator('.drawer:not([hidden])').getByRole('button', {name: 'Move up', exact: true}).click();
    await expect.poll(() => data.writes.length).toBe(2);
    expect(data.writes[1].body.preconditions.expected_versions).toEqual(data.writes[0].body.preconditions.expected_versions);
  });

  test(`right click and keyboard expose the same More actions with Escape focus (${native ? 'IPC' : 'Web'})`, async ({page}) => {
    const {dispatch} = fixture(); await mountConversation(page, native, dispatch); await page.goto('/dashboard/#/projects');
    const row = page.locator('#main .row.tree').filter({has: page.getByRole('link', {name: 'One', exact: true})});
    await row.getByRole('link', {name: 'One', exact: true}).click({button: 'right'});
    const drawer = page.locator('#main .drawer:not([hidden])');
    await expect(drawer.getByRole('button', {name: 'Rename', exact: true})).toBeVisible();
    await expect(drawer.getByRole('button', {name: 'Move down', exact: true})).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(row.getByRole('button', {name: 'More', exact: true})).toBeFocused();
    await expect(drawer).toHaveCount(0);
    await page.keyboard.press('Shift+F10');
    await expect(drawer.getByRole('button', {name: 'Rename', exact: true})).toBeVisible();
    await page.keyboard.press('Escape');
    await row.getByRole('button', {name: 'More', exact: true}).click();
    await expect(drawer.getByRole('button', {name: 'Rename', exact: true})).toBeVisible();
    await expect(drawer.getByRole('textbox', {name: 'Rename', exact: true})).toHaveValue('One');
    await page.evaluate(() => localStorage.setItem('batc.lang', 'zh-TW'));
    await page.reload();
    await row.getByRole('button', {name: '更多', exact: true}).click();
    await expect(drawer.getByRole('textbox', {name: '改名', exact: true})).toHaveValue('One');
  });
}
