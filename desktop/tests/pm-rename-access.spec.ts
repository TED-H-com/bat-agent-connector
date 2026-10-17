import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

// Creating a workspace inserts its owner. The next events poll then returns
// access_changed and the shell rebuilds the page. An open local rename has to
// come back with the same draft, caret and focus, and Save has to be pressable.
const cases = [
  {locale: 'en' as const, width: 1440, height: 900, discuss: 'Discuss', rename: 'Rename', title: 'Discussion title', save: 'Save', typed: 'Choose a first improvement'},
  {locale: 'zh-TW' as const, width: 390, height: 844, discuss: '討論', rename: '重新命名', title: '討論標題', save: '儲存', typed: '先選一項改善'},
];

function lifecycleBody(workspaceId: string) {
  return {
    workspace_id: workspaceId,
    lifecycle: {stage: 'idea', status: 'active', revision: 4, execution_epoch: 1, transition_id: null, coverage: 'recorded'},
    transition: null,
    transition_visibility_filtered: false,
    blocked_actions: [],
    record_actions: ['pm.message.append', 'pm.thread.rename'],
    available_stages: ['idea', 'planning', 'development'],
    maintenance: {available: false, reason: 'release_baseline_and_policy_required'},
    inflight_summary: {visible_unresolved: 0, visible_retained: 0, visibility_filtered: false},
  };
}

function createAccessFixture(locale: 'en' | 'zh-TW') {
  const f = demoFixture({locale, discussions: true});
  const workspaceId = f.ids.workspaceId;
  const lifecyclePath = `/pm/workspaces/${workspaceId}/lifecycle`;
  const bindingsPath = `/pm/workspaces/${workspaceId}/projects`;
  let accessReset = false;
  let bootstraps = 0;
  const renames: unknown[] = [];
  const dispatch = async (input: {method?: string, path: string, body?: unknown}) => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;
    const method = input.method || 'GET';
    if (path === '/bootstrap') bootstraps += 1;
    if (method === 'POST' && path === '/operations' && (input.body as {action?: string} | null)?.action === 'pm.thread.rename')
      renames.push(structuredClone(input));
    if (method === 'GET' && path === '/events' && accessReset) {
      accessReset = false;
      return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: 'access_changed', message: 'Access changed', resnapshot: true, preserve_drafts: true}}};
    }
    if (method === 'GET' && path === lifecyclePath)
      return {status: 200, data: lifecycleBody(workspaceId)};
    if (method === 'GET' && path === bindingsPath)
      return {status: 200, data: {workspace_id: workspaceId, bindings: [], next_cursor: null}};
    const result = await f.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.lifecycle = {
        version: 1,
        gated_actions: ['pm.thread.create'],
        record_actions: ['pm.message.append', 'pm.thread.rename'],
      };
      caps.features.pm_workspaces.work_evidence = true;
    }
    return result;
  };
  return {dispatch, workspaceId, renames, bootstraps: () => bootstraps, arm() { accessReset = true; }};
}

async function openDiscuss(page: Page, spec: typeof cases[number], workspaceId: string) {
  await page.goto(`/dashboard/#/pm/${workspaceId}`);
  await page.locator('[data-overview-discuss]').click();
  await expect(page).toHaveURL(new RegExp(`/pm/${workspaceId}/discussions/new`));
  await expect(page.locator('.pd-new-layout')).toBeVisible();
  await expect(page.locator('[data-discussion-composer]')).toBeFocused();
  await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
  const field = page.getByLabel(spec.title, {exact: true});
  await page.getByRole('button', {name: spec.rename, exact: true}).click();
  await expect(field).toBeFocused();
  await field.fill(spec.typed);
  const caret = Math.floor(spec.typed.length / 2);
  for (let i = spec.typed.length; i > caret; i -= 1) await field.press('ArrowLeft');
  await expect(field).toHaveJSProperty('selectionStart', caret);
  await expect(field).toHaveJSProperty('selectionEnd', caret);
  return caret;
}

for (const spec of cases) {
  test(`access reset keeps the open new-discussion Save (${spec.locale} ${spec.width})`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale: spec.locale, viewport: {width: spec.width, height: spec.height}});
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const fixture = createAccessFixture(spec.locale);
    await mountConversation(page, false, fixture.dispatch);
    const caret = await openDiscuss(page, spec, fixture.workspaceId);
    const before = fixture.bootstraps();
    fixture.arm();
    await expect.poll(() => fixture.bootstraps()).toBeGreaterThan(before);
    const field = page.getByLabel(spec.title, {exact: true});
    const save = page.getByRole('button', {name: spec.save, exact: true});
    await expect(save).toBeVisible();
    await expect(save).toBeEnabled();
    await expect(field).toBeVisible();
    await expect(field).toBeEnabled();
    await expect(field).toBeFocused();
    await expect(field).toHaveValue(spec.typed);
    await expect(field).toHaveJSProperty('selectionStart', caret);
    await expect(field).toHaveJSProperty('selectionEnd', caret);
    await save.click();
    await expect(page.locator('[data-discussion-title]')).toHaveText(spec.typed);
    await expect(field).toBeHidden();
    expect(fixture.renames).toHaveLength(0);
    expect(errors).toEqual([]);
    await context.close();
  });
}
