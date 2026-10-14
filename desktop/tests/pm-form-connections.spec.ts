import {test, expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {governanceFixture, mount, action, openAction, workspaceId} from './pm-governance-fixture';

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
