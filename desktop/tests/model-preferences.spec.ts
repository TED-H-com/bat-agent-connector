import {test, expect} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';

function fixture() {
  const base = conversationFixture();
  const state = {lost: false, revision: 2, reads: [] as string[], errors: [] as string[], prefs: {
    initial_agent: 'claude', initial_model: 'unavailable-original', last_agent: 'codex', last_model: 'actual:last',
    hidden: ['claude:model-b'], order: ['claude:model-b', 'claude:model-a']}};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method !== 'GET') {
      base.data.writes.push(structuredClone(input));
      if (state.lost) {state.lost = false; return {status: 503, data: {error: {code: 'LOST', message: 'Lost preference reply'}}};}
      state.prefs = {...state.prefs, ...input.body.params}; state.revision++;
      return {status: 200, data: {operation: {operation_id: 'op_'+'a'.repeat(32), status: 'succeeded'}}};
    }
    if (path.endsWith('/preferences')) {
      state.reads.push(input.path); const agent = url.searchParams.get('agent') || 'claude';
      return {status: 200, data: {version: 1, host: path.split('/')[2], model_preferences: {...state.prefs, revision: state.revision},
        model_catalog: {agent, status: 'available', source: 'bat_host', observed_at: 1791580000, stale: false,
          models: ['model-a', 'model-b'].map(id => ({id, agent, label: id, available: true}))},
        unknown_models: [{agent: 'claude', id: 'unavailable-original'}], usage: {source: 'bat_host', status: 'available', observed_at: 1791580000,
          providers: [{provider: 'claude', account_email: 'host-account@example.test', plan_type: 'subscription', stale: true, reason: 'old_snapshot',
            fetched_at: 1791500000, five_hour: {utilization: .42, resets_at: 1791590000}, seven_day: null}]}}};
    }
    if (path === '/workspaces') return {status: 200, data: {workspaces: [{host: 'demo', workspace_id: 'workspace', folder: '/srv/project'}], has_more: false, errors: {}}};
    const result = await base.dispatch(input);
    const extend = (caps: any) => ({...caps, hosts: [{host: 'demo', writes: true, orchestrate: true}], scopes: ['observe', 'start'],
      actions: [{action: 'preferences.models.update', allowed: true}, {action: 'session.start', allowed: true}], features: {...caps.features, host_preferences: {version: 1}}});
    if (path === '/capabilities') result.data = extend(result.data);
    if (path === '/bootstrap') result.data.capabilities = extend(result.data.capabilities);
    return result;
  };
  return {state, data: base.data, dispatch};
}
for (const native of [false, true]) {
  test(`personal preferences preserve unknown IDs, drafts and frozen retry (${native ? 'IPC' : 'Web'})`, async ({page}) => {
    const f = fixture(); page.on('pageerror', e => f.state.errors.push(e.message)); await mountConversation(page, native, f.dispatch);
    await page.goto('/dashboard/#/settings'); await page.getByRole(native ? 'link' : 'tab', {name: /^(Models|模型)$/}).click(); const panel = page.locator('[data-model-preferences]');
    await panel.locator('summary').click(); const initial = panel.getByRole('textbox', {name: 'Initial model ID', exact: true});
    await expect(initial).toHaveValue('unavailable-original'); await expect(panel).toContainText('42%');
    await expect(panel).toContainText('7-day usage: unknown'); await expect(panel).toContainText('Host-reported provider account: host-account@example.test');
    await expect(panel).toContainText('This usage snapshot is stale');
    await initial.fill('preserved-draft'); await panel.getByRole('button', {name: 'Refresh catalog and usage', exact: true}).click();
    await expect(initial).toHaveValue('preserved-draft');
    await panel.getByRole('button', {name: 'Move model model-a up', exact: true}).click();
    f.state.lost = true; await panel.getByRole('button', {name: 'Save', exact: true}).click();
    await expect(panel).toContainText('This request could not be completed. Check Details.'); await expect(initial).toBeDisabled();
    const original = f.data.writes[0]; expect(original.body.preconditions).toEqual({expected_revision: 2});
    expect(original.body.params).toMatchObject({initial_model: 'preserved-draft', last_model: 'actual:last', order: ['claude:model-a', 'claude:model-b']});
    await page.reload(); await page.getByRole(native ? 'link' : 'tab', {name: /^(Models|模型)$/}).click(); await panel.locator('summary').click(); await expect(initial).toHaveValue('preserved-draft'); await expect(initial).toBeDisabled();
    await panel.getByRole('button', {name: 'Retry original request', exact: true}).click(); await expect(initial).toBeEnabled();
    expect(f.data.writes[1]).toEqual(original);
    await initial.fill('private-draft'); f.data.principal = 'another'; await page.reload(); await page.getByRole(native ? 'link' : 'tab', {name: /^(Models|模型)$/}).click(); await panel.locator('summary').click();
    await expect(initial).toHaveValue('preserved-draft'); expect(f.state.errors).toEqual([]);
  });
  test(`start model choices use actual catalog and explicit initial versus last (${native ? 'IPC' : 'Web'})`, async ({page}) => {
    const f = fixture(); await mountConversation(page, native, f.dispatch); await page.goto('/dashboard/#/start');
    const panel = page.locator('[data-session-start]'); await panel.getByRole('combobox', {name: 'Host', exact: true}).selectOption('demo');
    await panel.getByRole('combobox', {name: 'Workspace', exact: true}).selectOption('workspace');
    await expect(panel.locator('datalist option')).toHaveCount(1);
    await panel.getByRole('button', {name: 'Use initial preference', exact: true}).click();
    const model = panel.getByRole('combobox', {name: 'Model (optional)', exact: true});
    await expect(model).toHaveValue('unavailable-original'); await expect(panel).toContainText('outside the currently verified catalog');
    await panel.getByRole('button', {name: 'Use last choice', exact: true}).click();
    await expect(panel.getByRole('combobox', {name: 'Agent', exact: true})).toHaveValue('codex'); await expect(model).toHaveValue('actual:last');
    expect(f.data.writes).toEqual([]);
  });
}

function holdGate() {
  let holding = false, finish = () => {};
  let gate = Promise.resolve();
  return {async wait() {if (holding) await gate;}, hold() {holding = true; gate = new Promise(resolve => {finish = resolve;});}, release() {holding = false; const done = finish; finish = () => {}; done();}};
}
async function openModels(page: import('@playwright/test').Page, options: {hosts?: {host: string, writes?: boolean, orchestrate?: boolean}[]} = {}) {
  const hosts = options.hosts ?? [{host: 'demo', writes: true, orchestrate: true}];
  const base = conversationFixture();
  const readHold = holdGate(), postHold = holdGate();
  let refreshReads = 0, posts = 0;
  const prefs = {initial_agent: 'claude', initial_model: 'unavailable-original', last_agent: 'codex', last_model: 'actual:last', hidden: ['claude:model-b'], order: ['claude:model-b', 'claude:model-a']};
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method !== 'GET') {
      if (path === '/operations') {posts++; await postHold.wait();}
      return {status: 200, data: {operation: {operation_id: 'op_'+'a'.repeat(32), status: 'succeeded'}}};
    }
    if (path.endsWith('/preferences')) {
      if (url.searchParams.get('refresh') === '1') {refreshReads++; await readHold.wait();}
      const agent = url.searchParams.get('agent') || 'claude';
      return {status: 200, data: {version: 1, host: path.split('/')[2], model_preferences: {...prefs, revision: 2},
        model_catalog: {agent, status: 'available', source: 'bat_host', observed_at: 1791580000, stale: false,
          models: ['model-a', 'model-b'].map(id => ({id, agent, label: id, available: true}))},
        unknown_models: [{agent: 'claude', id: 'unavailable-original'}], usage: {source: 'bat_host', status: 'available', observed_at: 1791580000, providers: []}}};
    }
    const result = await base.dispatch(input);
    const extend = (caps: any) => ({...caps, hosts, scopes: ['observe', 'start'],
      actions: [{action: 'preferences.models.update', allowed: true}, {action: 'session.start', allowed: true}], features: {...caps.features, host_preferences: {version: 1}}});
    if (path === '/capabilities') result.data = extend(result.data);
    if (path === '/bootstrap') result.data.capabilities = extend(result.data.capabilities);
    return result;
  };
  await mountConversation(page, false, dispatch);
  await page.goto('/dashboard/#/settings');
  await page.getByRole('tab', {name: /^(Models|模型)$/}).click();
  const panel = page.locator('[data-model-preferences]');
  await panel.locator('summary').click();
  if (hosts.length) await expect(panel.getByRole('textbox', {name: 'Initial model ID', exact: true})).toHaveValue('unavailable-original');
  const refresh = panel.getByRole('button', {name: 'Refresh catalog and usage', exact: true});
  const save = panel.locator('button.primary');
  const status = panel.locator('[role="status"]');
  return {panel, refresh, save, status, refreshReads: () => refreshReads, posts: () => posts, readHold, postHold};
}
async function pressedState(button: import('@playwright/test').Locator, rootSelector: string) {
  return button.evaluate((node, selector) => {
    const region = node.closest(selector)?.querySelector('[role="status"]');
    return {focused: document.activeElement === node, ariaDisabled: node.getAttribute('aria-disabled'),
      disabled: (node as HTMLButtonElement).disabled, ariaBusy: region?.getAttribute('aria-busy') ?? null};
  }, rootSelector);
}

test('model save keeps focus and ignores a second press while saving', async ({page}) => {
  const ui = await openModels(page);
  await expect(ui.save).toHaveText('Save');
  await expect(ui.save).toBeEnabled();
  const before = ui.posts();
  ui.postHold.hold();
  try {
    await ui.save.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => ui.posts()).toBe(before + 1);
    expect(await pressedState(ui.save, '[data-model-preferences]')).toEqual({focused: true, ariaDisabled: 'true', disabled: false, ariaBusy: 'true'});
    await page.keyboard.press('Enter');
    ui.postHold.release();
    await expect(ui.status).not.toHaveAttribute('aria-busy', 'true');
    expect(ui.posts()).toBe(before + 1);
    if (await ui.save.evaluate(node => (node as HTMLButtonElement).disabled || (node as HTMLButtonElement).hidden)) await expect(ui.status).toBeFocused();
    else await expect(ui.save).toBeFocused();
  } finally {ui.postHold.release();}
});

test('model refresh stays disabled without aria-disabled when no host is chosen', async ({page}) => {
  const ui = await openModels(page, {hosts: []});
  expect(await ui.refresh.evaluate(node => ({disabled: (node as HTMLButtonElement).disabled, ariaDisabled: node.getAttribute('aria-disabled')}))).toEqual({disabled: true, ariaDisabled: null});
});

test('model refresh keeps focus and ignores a second read while reloading', async ({page}) => {
  const ui = await openModels(page);
  const before = ui.refreshReads();
  ui.readHold.hold();
  try {
    await ui.refresh.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => ui.refreshReads()).toBe(before + 1);
    expect(await pressedState(ui.refresh, '[data-model-preferences]')).toEqual({focused: true, ariaDisabled: 'true', disabled: false, ariaBusy: 'true'});
    await page.keyboard.press('Enter');
    ui.readHold.release();
    await expect(ui.status).not.toHaveAttribute('aria-busy', 'true');
    expect(ui.refreshReads()).toBe(before + 1);
    await expect(ui.refresh).toBeFocused();
  } finally {ui.readHold.release();}
});
