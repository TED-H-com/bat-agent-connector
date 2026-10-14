import {openThreadTab} from './pm-navigation.ts';
import {test, expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {governanceFixture, mount, action, openAction, workspaceId} from './pm-governance-fixture';
import {pmFixture, threadId} from './pm-fixture.ts';

test('repeated governance refreshes release discarded forms and their state callbacks', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const f = governanceFixture();
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/pm/workspaces') return {status: 200, data: {workspaces: [f.config().workspace]}};
    if (path === `/pm/workspaces/${workspaceId}/threads`) return {status: 200, data: {workspace_id: workspaceId, threads: [], next_cursor: null}};
    return f.dispatch(input);
  };
  await mount(page, {...f, dispatch});
  // Instrument only the source served by this test. The production bundle has
  // neither a registry probe nor a public durable-form export.
  await page.route('**/__governance_src/pm-workspace.js', async route => {
    let source = await readFile(new URL('../src/pm-workspace.js', import.meta.url), 'utf8');
    source = source.replace('const forms = new Set(), alive =',
      'const forms = new Set(); window.formRegistryProbe = {size: () => forms.size, connected: () => [...forms].every(value => value.box.isConnected), entries: []}; const alive =');
    source = source.replace('const form = options => {', `const form = options => {
      const record = {updates: 0, states: 0}; const stateChange = options.onStateChange;
      options = {...options, onStateChange: () => {record.states++; stateChange?.();}};`);
    source = source.replace('const dispose = value.dispose;', `record.box = value.box;
      const update = value.update; value.update = () => {record.updates++; update();};
      window.formRegistryProbe.entries.push(record); const dispose = value.dispose;`);
    source += '\nexport const receiptSubscriberCount = () => [...receiptSubscribers.values()].reduce((count, subscribers) => count + subscribers.size, 0);';
    await route.fulfill({contentType: 'text/javascript', body: source});
  });
  await page.reload();
  await page.evaluate(async workspaceId => {
    const win = window as any;
    const {mountPmWorkspace, receiptSubscriberCount} = await import('/__governance_src/pm-workspace.js');
    const {h} = await import('/__governance_src/ui/dom.js'), {t} = await import('/__governance_src/i18n.js');
    const config = await win.governanceFixtureConfig();
    config.caps.features = {pm_workspaces: {version: 1, governance: true}};
    const api = async (method: string, path: string, body: any, key: string) => {
      const response = await win.governanceFixtureRequest({method, path, body, idempotency_key: key});
      if (response.status >= 400) throw Object.assign(Error(response.data.error.message), {status: response.status, code: response.data.error.code});
      return response.data;
    };
    win.disposeWorkspace = await mountPmWorkspace({main: document.getElementById('mount'), h, t, api, caps: () => config.caps,
      guard: () => {}, namespace: 'form-connections', workspaceId, view: 'issues', onEvents: () => () => {}, errorBox: (error: any) => h('p', {}, error.message)});
    win.formRegistryProbe.receipts = receiptSubscriberCount;
  }, workspaceId);
  await expect(action(page, 'revise')).toHaveCount(1);
  const registry = () => page.evaluate(() => {
    const probe = (window as any).formRegistryProbe;
    return {size: probe.size(), receipts: probe.receipts(), connected: probe.connected(),
      dom: document.querySelectorAll('[data-pm-action]').length,
      created: probe.entries.filter((entry: any) => entry.box.dataset.pmAction.startsWith('pm.governance.')).length};
  });
  const initial = await registry();
  expect(initial.size).toBe(initial.dom); expect(initial.receipts).toBe(initial.dom); expect(initial.connected).toBe(true);
  for (let i = 0; i < 12; i++) {
    await page.evaluate(() => {
      const probe = (window as any).formRegistryProbe;
      probe.discarded = probe.entries.filter((entry: any) => !entry.box.isConnected).map((entry: any) => ({entry, updates: entry.updates, states: entry.states}));
    });
    f.state.workspaceVersion++;
    await page.getByRole('button', {name: 'Refresh', exact: true}).click();
    await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
    expect(await page.evaluate(() => (window as any).formRegistryProbe.discarded.every(({entry, updates, states}: any) => entry.updates === updates && entry.states === states))).toBe(true);
    const current = await registry();
    expect(current.size).toBe(initial.size); expect(current.size).toBe(current.dom);
    expect(current.receipts).toBe(current.dom); expect(current.connected).toBe(true);
    expect(current.created).toBeGreaterThan(initial.created);
  }
  expect((await registry()).created).toBe(initial.created * 13);
  await openAction(page, 'revise'); await expect(action(page, 'revise')).toBeVisible();
  expect(await action(page, 'revise').evaluate(node => node instanceof HTMLFormElement)).toBe(true);
  await action(page, 'revise').evaluate(node => {(node as HTMLElement).hidden = true;});
  await expect(action(page, 'revise')).toBeHidden();
  await action(page, 'revise').evaluate(node => {(node as HTMLElement).hidden = false;});
  await expect(action(page, 'revise')).toBeVisible();
  await page.evaluate(() => (window as any).disposeWorkspace());
  expect((await registry()).size).toBe(0); expect((await registry()).receipts).toBe(0);
  expect(f.state.writes).toEqual([]);
  expect(errors).toEqual([]);
});

for (const withDraft of [false, true]) {
test(`a stale hidden view rebuilds connected forms with the current workspace gates${withDraft ? ' and preserves an unsent draft' : ''}`, async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const f = governanceFixture();
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/pm/workspaces') return {status: 200, data: {workspaces: [f.config().workspace]}};
    if (path === `/pm/workspaces/${workspaceId}/threads`) return {status: 200, data: {workspace_id: workspaceId, threads: [], next_cursor: null}};
    return f.dispatch(input);
  };
  await mount(page, {...f, dispatch});
  await page.reload();
  await page.evaluate(async workspaceId => {
    const win = window as any;
    const {mountPmWorkspace} = await import('/__governance_src/pm-workspace.js');
    const {h} = await import('/__governance_src/ui/dom.js'), {t} = await import('/__governance_src/i18n.js');
    const config = await win.governanceFixtureConfig();
    config.caps.features = {pm_workspaces: {version: 1, governance: true}};
    const api = async (method: string, path: string, body: any, key: string) => {
      const response = await win.governanceFixtureRequest({method, path, body, idempotency_key: key});
      if (response.status >= 400) throw Object.assign(Error(response.data.error.message), {status: response.status, code: response.data.error.code});
      return response.data;
    };
    win.disposeWorkspace = await mountPmWorkspace({main: document.getElementById('mount'), h, t, api, caps: () => config.caps,
      guard: () => {}, namespace: 'stale-form-gates', workspaceId, view: 'issues',
      onEvents: (callback: any) => {win.workspaceEvent = callback; return () => {};}, errorBox: (error: any) => h('p', {}, error.message)});
  }, workspaceId);
  const title = action(page, 'revise').getByLabel('Finding title', {exact: true});
  await openAction(page, 'revise'); await expect(title).toBeEnabled();
  const draftTitle = 'Keep the unsent finding draft across workspace gates';
  if (withDraft) {
    await title.fill(draftTitle);
    await action(page, 'revise').getByLabel('Reason for this record', {exact: true}).fill('Retain this draft without submitting it');
  }
  const previous = await action(page, 'revise').elementHandle();
  const storedDraft = () => page.evaluate(() => Object.fromEntries(Object.entries(localStorage)
    .filter(([key]) => key.startsWith('batc.pm.stale-form-gates.pm.governance.revise.'))));
  const saved = await storedDraft();
  if (withDraft) {
    expect(Object.keys(saved)).toHaveLength(1);
    expect(JSON.parse(Object.values(saved)[0]).values.title).toBe(draftTitle);
  }
  for (const role of ['reader', 'owner']) {
    await page.evaluate(workspaceId => (window as any).disposeWorkspace.navigate(workspaceId, null, 'discussions'), workspaceId);
    f.state.role = role;
    // A version change intentionally makes a dirty draft stale. Test draft
    // preservation with permission changes alone; keep the original rebuild
    // coverage for changing workspace versions when no draft is dirty.
    if (!withDraft) f.state.workspaceVersion++;
    const before = f.state.reads.filter((path: string) => path.includes('/governance')).length;
    await page.evaluate(workspaceId => (window as any).workspaceEvent({resource_type: 'pm_governance', resource_id: workspaceId}), workspaceId);
    expect(f.state.reads.filter((path: string) => path.includes('/governance'))).toHaveLength(before);
    await expect(action(page, 'revise')).toBeHidden();
    await page.evaluate(workspaceId => (window as any).disposeWorkspace.navigate(workspaceId, null, 'issues'), workspaceId);
    await expect.poll(() => f.state.reads.filter((path: string) => path.includes('/governance')).length).toBeGreaterThan(before);
    if (role === 'reader' || !withDraft) await expect.poll(() => previous!.evaluate(node => node.isConnected)).toBe(false);
    await expect(page.locator('[data-pm-source-loading]')).toHaveCount(0);
    if (role === 'reader') {
      await expect(action(page, 'revise')).toHaveCount(0);
      await expect(page.getByText('Revise finding', {exact: true})).toHaveCount(0);
    } else {
      await openAction(page, 'revise'); await expect(title).toBeVisible(); await expect(title).toBeEnabled();
      if (withDraft) {
        await expect(title).toHaveValue(draftTitle);
        await expect(action(page, 'revise').getByLabel('Reason for this record', {exact: true})).toHaveValue('Retain this draft without submitting it');
      }
      await expect(action(page, 'revise').locator('button[type=submit]')).toBeEnabled();
      expect(await action(page, 'revise').evaluate(node => node instanceof HTMLFormElement && node.querySelector('batc-pm-form-connection')?.isConnected)).toBe(true);
    }
    expect(await storedDraft()).toEqual(saved);
    expect(f.state.writes).toEqual([]); expect(errors).toEqual([]);
  }
  await previous!.dispose();
  expect(f.state.writes).toEqual([]); expect(errors).toEqual([]);
  await page.evaluate(() => (window as any).disposeWorkspace());
});
}

test('a thread event restores hidden lifecycle gates after superseding a manual refresh', async ({page}) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const f = pmFixture(), governance = governanceFixture();
  let holdNext = false, held = false, release = () => {}, lifecycleReads = 0;
  let blockedActions: string[] = [];
  const waiting = new Promise<void>(resolve => {release = resolve;});
  const dispatch = async (request: any) => {
    const path = new URL(request.path, 'http://fixture').pathname;
    if (request.method === 'GET' && path === `/pm/workspaces/${workspaceId}` && holdNext) {
      holdNext = false; held = true; await waiting;
    }
    if (path === `/pm/workspaces/${workspaceId}/lifecycle`) {
      lifecycleReads++;
      return {status: 200, data: {workspace_id: workspaceId,
        lifecycle: {revision: 1, stage: 'idea', status: 'active', transition_id: null},
        blocked_actions: blockedActions, available_stages: ['idea', 'planning', 'development'], inflight_summary: {}}};
    }
    return f.dispatch(request);
  };
  await mount(page, {...governance, dispatch, config: async () => {
    const {data: caps} = await f.dispatch({method: 'GET', path: '/capabilities'});
    caps.features.pm_workspaces.lifecycle = {version: 1, gated_actions: ['pm.run.prepare']};
    return {caps, workspace: governance.config().workspace};
  }});
  await page.reload();
  await page.evaluate(async ({workspaceId, threadId}) => {
    const win = window as any;
    const {mountPmWorkspace} = await import('/__governance_src/pm-workspace.js');
    const {h} = await import('/__governance_src/ui/dom.js'), {t} = await import('/__governance_src/i18n.js');
    const config = await win.governanceFixtureConfig();
    const api = async (method: string, path: string, body: any, key: string) => {
      const response = await win.governanceFixtureRequest({method, path, body, idempotency_key: key});
      if (response.status >= 400) throw Object.assign(Error(response.data.error.message), {status: response.status, code: response.data.error.code});
      return response.data;
    };
    win.disposeWorkspace = await mountPmWorkspace({main: document.getElementById('mount'), h, t, api, caps: () => config.caps,
      guard: () => {}, namespace: 'hidden-lifecycle-gates', workspaceId, threadId, view: 'discussions',
      onEvents: (callback: any) => {win.workspaceEvent = callback; return () => {};}, errorBox: (error: any) => h('p', {}, error.message)});
  }, {workspaceId, threadId});
  const purpose = page.locator('[data-pm-action="pm.run.prepare"] textarea');
  expect(lifecycleReads).toBe(1);
  await expect(purpose).toBeEnabled();
  await expect(page.locator('[data-pm-lifecycle]')).toBeHidden();
  holdNext = true;
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect.poll(() => held).toBe(true);
  await expect(purpose).toBeDisabled();
  try {
    await page.evaluate(workspaceId => (window as any).workspaceEvent({resource_type: 'pm_thread', resource_id: workspaceId}), workspaceId);
  } finally {release();}
  await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
  await expect(purpose).toBeEnabled();
  expect(lifecycleReads).toBe(2);
  for (const blocked of [true, false]) {
    blockedActions = blocked ? ['pm.run.prepare'] : [];
    await page.evaluate(workspaceId => (window as any).workspaceEvent({resource_type: 'pm_lifecycle', resource_id: workspaceId}), workspaceId);
    if (blocked) await expect(purpose).toBeDisabled();
    else await expect(purpose).toBeEnabled();
  }
  expect(lifecycleReads).toBe(4);
  expect(f.state.writes).toEqual([]); expect(errors).toEqual([]);
  await page.evaluate(() => (window as any).disposeWorkspace());
});

test('an unknown admission waits for sending to resume and retries only the original request', async ({page}) => {
  const f = pmFixture(), governance = governanceFixture();
  f.state.lost = true;
  let pending = false;
  const receiptReads: string[] = [];
  const dispatch = async (request: any) => {
    const response = await f.dispatch(request);
    if (request.method === 'GET' && request.path.startsWith('/operations/')) receiptReads.push(request.path);
    if (pending && request.method === 'POST' && response.status < 400) {
      return {status: 202, data: {operation: {...response.data.operation, status: 'running', result: null}}};
    }
    return response;
  };
  await mount(page, {...governance, dispatch});
  await page.reload();
  await page.evaluate(async ({workspaceId, threadId}) => {
    const win = window as any;
    const {mountPmWorkspace} = await import('/__governance_src/pm-workspace.js');
    const {h} = await import('/__governance_src/ui/dom.js'), {t} = await import('/__governance_src/i18n.js');
    const {data: caps} = await win.governanceFixtureRequest({method: 'GET', path: '/capabilities'});
    const api = async (method: string, path: string, body: any, key: string) => {
      const response = await win.governanceFixtureRequest({method, path, body, idempotency_key: key});
      if (response.status >= 400) {
        win.sendAvailable = false;
        win.notifyOnline?.();
        throw Object.assign(Error(response.data.error.message), {status: response.status, code: response.data.error.code});
      }
      if (response.status === 202) {win.sendAvailable = false; win.notifyOnline?.();}
      return response.data;
    };
    win.sendAvailable = true;
    win.disposeWorkspace = await mountPmWorkspace({main: document.getElementById('mount'), h, t, api, caps: () => caps,
      guard: () => {}, namespace: 'paused-original-admission', workspaceId, threadId, view: 'discussions',
      canSend: () => win.sendAvailable,
      onOnline: (callback: any) => {win.notifyOnline = callback; return () => {win.notifyOnline = null;};},
      onEvents: () => () => {}, errorBox: (error: any) => h('p', {}, error.message)});
    win.disposeWorkspace.navigate(workspaceId, threadId, 'discussions');
  }, {workspaceId, threadId});
  await openThreadTab(page, 'inputs');
  await page.locator('[data-pm-add-input] > summary').click();
  const form = page.locator('[data-pm-action="pm.input.record"]');
  await form.getByLabel('Topic', {exact: true}).selectOption('problem');
  await form.getByLabel('Statement or question', {exact: true}).fill('Retain the lost transport admission');
  await form.getByRole('button', {name: 'Save input', exact: true}).click();
  await expect(form).toContainText('Reply unavailable');
  const original = await page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([key]) => key.includes('.pm.input.record.'))![1]).intent);
  const retry = form.getByRole('button', {name: 'Check original request', exact: true});
  await expect(retry).toBeDisabled();
  expect(original.operation_id).toBeUndefined(); expect(f.state.writes).toHaveLength(1);
  await form.evaluate(node => node.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})));
  await expect(form.locator(':scope > [role=status]')).toContainText('This action is currently unavailable.');
  expect(f.state.writes).toHaveLength(1);
  expect(await page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([key]) => key.includes('.pm.input.record.'))![1]).intent)).toEqual(original);
  await page.evaluate(() => {const win = window as any; win.sendAvailable = true; win.notifyOnline?.();});
  await expect(retry).toBeEnabled();
  expect(f.state.writes).toHaveLength(1);
  await retry.click();
  await expect(form.locator('[data-pm-saved]')).toHaveCount(1);
  expect(f.state.writes).toHaveLength(2);
  expect(f.state.writes[1].idempotency_key).toBe(original.key);
  expect(f.state.writes[1].body).toEqual(original.request);
  expect(f.state.ops.size).toBe(1);
  expect(f.state.inputs.filter(row => row.value === 'Retain the lost transport admission')).toHaveLength(1);
  // An acknowledged ID still settles by GET while new POSTs remain paused.
  pending = true;
  await form.getByLabel('Topic', {exact: true}).selectOption('problem');
  await form.getByLabel('Statement or question', {exact: true}).fill('Read the acknowledged original receipt');
  await form.getByRole('button', {name: 'Save input', exact: true}).click();
  await expect(form.locator('[data-pm-saved]')).toHaveCount(1);
  expect(await page.evaluate(() => (window as any).sendAvailable)).toBe(false);
  expect(receiptReads).toEqual(['/operations/' + f.state.ops.get(f.state.writes[2].idempotency_key).operation_id]);
  expect(f.state.writes).toHaveLength(3); expect(f.state.ops.size).toBe(2);
  expect(f.state.inputs.filter(row => row.value === 'Read the acknowledged original receipt')).toHaveLength(1);
  await expect(form.getByRole('button', {name: 'Save input', exact: true})).toBeDisabled();
  await page.evaluate(() => (window as any).disposeWorkspace());
  expect(await page.evaluate(() => (window as any).notifyOnline)).toBeNull();
});
