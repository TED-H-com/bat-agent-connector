import {test, expect} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';
const pid = 'prj_'+'1'.repeat(20), skill = 'skill_'+'1'.repeat(32), missing = 'skill_'+'2'.repeat(32);
function fixture() {
  const base = conversationFixture(), state = {lost: false, stale: false, changed: false, errors: [] as string[], revision: 3,
    selected: [{skill_id: missing, digest: 'd'.repeat(64)}]};
  const project = {project_id: pid, name: 'Skill project', version: 1, description: '', repositories: [], task_project: '', counts: {doing: 0, done: 0, awaiting_approval: 0, total: 0}};
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method !== 'GET') {
      base.data.writes.push(structuredClone(input));
      if (state.lost) {state.lost = false; return {status: 503, data: {error: {code: 'LOST', message: 'Lost selection reply'}}};}
      state.selected = input.body.params.selected; state.revision++;
      return {status: 200, data: {operation: {operation_id: 'op_'+'a'.repeat(32), status: 'succeeded'}}};
    }
    if (path.endsWith('/skills')) return {status: 200, data: {version: 1, project_id: pid, host: 'demo', workspace_id: 'actual-ws',
      catalog: {source: 'host_workspace', status: 'available', complete: true, stale: state.stale, catalog_digest: (state.changed ? 'b' : 'a').repeat(64), observed_at: 1791580000,
        skills: [{skill_id: skill, digest: 'c'.repeat(64), name: 'Review changes', relative_path: '.claude/skills/review', scope: 'project', agent: 'claude', available: true, files: 2, size_bytes: 512}]},
      selection: {revision: state.revision, host: 'demo', workspace_id: 'actual-ws', selected: state.selected, application: 'selected_not_applied', unresolved: state.selected.filter(ref => ref.skill_id === missing)}}};
    if (path === '/workspaces') return {status: 200, data: {workspaces: [{host: 'demo', workspace_id: 'actual-ws', name: 'Actual workspace'}], errors: {}, has_more: false}};
    if (path === '/projects') return {status: 200, data: {projects: [{...project, children: []}], archived: []}};
    if (path === '/projects/'+pid) return {status: 200, data: {project, path: [], sub_projects: [], work_items: [], work: []}};
    const result = await base.dispatch(input);
    const extend = (caps: any) => ({...caps, hosts: [{host: 'demo'}], scopes: ['observe', 'manage'], actions: [{action: 'project.skills.update', allowed: true}], features: {project_skills: {version: 1}}});
    if (path === '/capabilities') result.data = extend(result.data);
    if (path === '/bootstrap') result.data.capabilities = extend(result.data.capabilities);
    return result;
  };
  return {state, data: base.data, dispatch};
}
for (const native of [false, true]) test(`skills pin exact source, preserve unresolved selections and replay fixed intent (${native ? 'IPC' : 'Web'})`, async ({page}) => {
  await page.setViewportSize({width: 390, height: 900});
  const f = fixture(); page.on('pageerror', error => f.state.errors.push(error.message)); await mountConversation(page, native, f.dispatch);
  await page.goto('/dashboard/#/project/'+pid); const panel = page.locator('[data-project-skills]'); await panel.locator(':scope > summary').click();
  await panel.getByRole('combobox', {name: 'Host', exact: true}).selectOption('demo');
  await panel.getByRole('combobox', {name: 'Workspace', exact: true}).selectOption('actual-ws');
  await expect(panel).toContainText('They are not applied to an agent'); await expect(panel).toContainText('fixed version retained');
  await panel.getByRole('checkbox', {name: 'Review changes', exact: true}).check();
  f.state.changed = true; await panel.getByRole('button', {name: 'Rescan skill sources', exact: true}).click();
  await expect(panel).toContainText('source or saved selection in Connector changed'); await expect(panel.getByRole('checkbox')).toBeChecked();
  // A stale response cannot authorize a new selection, and a fresh reload remains explicit.
  f.state.stale = true; await panel.getByRole('button', {name: 'Rescan skill sources', exact: true}).click();
  await expect(panel.getByRole('button', {name: 'Save skill selection', exact: true})).toBeDisabled();
  f.state.stale = false; await panel.getByRole('button', {name: 'Reload saved selection', exact: true}).click();
  await expect(panel.getByRole('checkbox')).not.toBeChecked(); await panel.getByRole('checkbox').check();
  f.state.lost = true; await panel.getByRole('button', {name: 'Save skill selection', exact: true}).click();
  await expect(panel).toContainText('This request could not be completed. Check Details.'); const original = f.data.writes[0];
  expect(original.body).toEqual({action: 'project.skills.update', target: {project_id: pid}, params: {host: 'demo', workspace_id: 'actual-ws',
    selected: [{skill_id: missing, digest: 'd'.repeat(64)}, {skill_id: skill, digest: 'c'.repeat(64)}]}, preconditions: {expected_revision: 3, expected_catalog_digest: 'b'.repeat(64)}});
  await page.reload(); await panel.locator(':scope > summary').click(); await expect(panel.getByRole('checkbox')).toBeDisabled();
  await panel.getByRole('button', {name: 'Retry original request', exact: true}).click(); await expect(panel.getByRole('checkbox')).toBeEnabled();
  expect(f.data.writes[1]).toEqual(original); expect(f.state.errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
