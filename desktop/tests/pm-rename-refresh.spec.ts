import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

// A new discussion has no central thread yet. Rename Save commits the local
// draft title and must not send pm.thread.rename. A pm_workspace event after
// the composer is focused withdraws lifecycle and reads it again; that read
// is held so the assertion sees the open editor during the refresh.
const cases = [
  {locale: 'en' as const, width: 1440, height: 900, rename: 'Rename', title: 'Discussion title', save: 'Save', typed: 'Choose a first improvement'},
  {locale: 'zh-TW' as const, width: 390, height: 844, rename: '重新命名', title: '討論標題', save: '儲存', typed: '先選一項改善'},
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

function createRefreshFixture(locale: 'en' | 'zh-TW') {
  const f = demoFixture({locale, discussions: true});
  const workspaceId = f.ids.workspaceId;
  const lifecyclePath = `/pm/workspaces/${workspaceId}/lifecycle`;
  const bindingsPath = `/pm/workspaces/${workspaceId}/projects`;
  let eventCursor = 0;
  let holdLifecycle = false;
  let releaseLifecycle = () => {};
  let gate: Promise<void> = Promise.resolve();
  const held = {lifecycle: false};
  const renames: unknown[] = [];

  const arm = () => {
    held.lifecycle = false;
    holdLifecycle = true;
    gate = new Promise<void>(resolve => { releaseLifecycle = resolve; });
  };
  const release = () => {
    holdLifecycle = false;
    releaseLifecycle();
  };
  const dispatch = async (input: {method?: string, path: string, body?: unknown, idempotency_key?: string}) => {
    const url = new URL(input.path, 'http://fixture');
    const path = url.pathname;
    const method = input.method || 'GET';
    if (method === 'POST' && path === '/operations' && (input.body as {action?: string} | null)?.action === 'pm.thread.rename')
      renames.push(structuredClone(input));
    if (method === 'GET' && path === '/events' && eventCursor > Number(url.searchParams.get('after') || 0)) {
      return {status: 200, data: {
        events: [{seq: eventCursor, resource_type: 'pm_workspace', resource_id: workspaceId, kind: 'pm.workspace.updated'}],
        next_cursor: eventCursor, head_cursor: eventCursor, has_more: false,
        sync: {checkpoint: {cursor: eventCursor, token: `demo-${eventCursor}`}},
      }};
    }
    if (method === 'GET' && path === lifecyclePath) {
      if (holdLifecycle) {
        held.lifecycle = true;
        await gate;
      }
      return {status: 200, data: lifecycleBody(workspaceId)};
    }
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
  return {dispatch, workspaceId, arm, release, held, renames, emit() { eventCursor += 1; }};
}

async function openRename(page: Page, spec: typeof cases[number]) {
  const field = page.getByLabel(spec.title, {exact: true});
  await page.getByRole('button', {name: spec.rename, exact: true}).click();
  await expect(field).toBeFocused();
  await field.fill(spec.typed);
  const caret = Math.floor(spec.typed.length / 2);
  await field.evaluate((node, start) => {
    const input = node as HTMLInputElement;
    input.dataset.renameField = 'open';
    input.setSelectionRange(start, start);
  }, caret);
  await page.getByRole('button', {name: spec.save, exact: true}).evaluate(node => {
    (node as HTMLButtonElement).dataset.renameSave = 'open';
  });
  return caret;
}

async function expectEditor(page: Page, typed: string, caret: number) {
  const field = page.locator('[data-rename-field="open"]');
  const save = page.locator('[data-rename-save="open"]');
  await expect(field).toBeVisible();
  await expect(field).toBeEnabled();
  await expect(field).toBeFocused();
  await expect(field).toHaveValue(typed);
  await expect(field).toHaveJSProperty('selectionStart', caret);
  await expect(field).toHaveJSProperty('selectionEnd', caret);
  await expect(save).toBeVisible();
  await expect(save).toBeEnabled();
  await expect(save).toHaveJSProperty('isConnected', true);
}

for (const spec of cases) {
  test(`new discussion rename survives a background refresh (${spec.locale} ${spec.width})`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale: spec.locale, viewport: {width: spec.width, height: spec.height}});
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const fixture = createRefreshFixture(spec.locale);
    await mountConversation(page, false, fixture.dispatch);
    await page.goto(`/dashboard/#/pm/${fixture.workspaceId}/discussions/new`);
    await expect(page.locator('[data-discussion-composer]')).toBeFocused();
    await expect(page.locator('[data-pm-refresh]')).toBeEnabled();

    const caret = await openRename(page, spec);
    fixture.arm();
    fixture.emit();
    await expect.poll(() => fixture.held.lifecycle).toBe(true);
    await expectEditor(page, spec.typed, caret);

    fixture.release();
    await expect(page.locator('[data-pm-refresh]')).toBeEnabled();
    await expectEditor(page, spec.typed, caret);

    await page.locator('[data-rename-save="open"]').click();
    await expect(page.locator('[data-discussion-title]')).toHaveText(spec.typed);
    await expect(page.locator('[data-rename-field="open"]')).toBeHidden();
    expect(fixture.renames).toHaveLength(0);
    expect(errors).toEqual([]);
    await context.close();
  });
}
