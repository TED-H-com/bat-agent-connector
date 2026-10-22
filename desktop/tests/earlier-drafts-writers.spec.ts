import {STRINGS} from '../src/i18n.js';
import {setup} from './managed-storage-fixture';
import {unavailableBriefTransport} from './pm-source-transport-fixture';
import {test, expect, type Page} from '@playwright/test';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {startFixture, openStart} from './session-start-fixture';
import {publishedFixture, openPublished, dispatchProject, publishedSha, selected} from './repository-start-fixture';
import {orchestrationFixture, openOrchestration} from './orchestration-fixture';
import {labelsFixture, openLabels} from './session-labels-fixture';

// Snapshots are emitted by the real UI writers, before and after a lost reply.
// Only random keys are normalized; request shapes, values and field names stay exact.
async function snapshot(page: Page, family: string) {
  return page.evaluate(family => Object.entries(localStorage).filter(([key]) => key.startsWith(`batc.${family}.`) && !key.endsWith('.shortcut')).map(([key, raw]) => {
    const scope = key.match(/^batc\.[a-z-]+\.([^:]+:[^:]+:[^:]+:[^.]+)/)?.[1] || 'writer-scope';
    const suffix = key.slice(`batc.${family}.${scope}`.length).replace(/^\./, '');
    return {family, suffix, raw: raw.replaceAll(/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/g, '00000000-0000-4000-8000-000000000001')};
  }), family);
}
async function golden(name: string, rows: any[]) {
  const path = new URL(`./fixtures/earlier-drafts-${name}.json`, import.meta.url);
  if (process.env.UPDATE_EARLIER_DRAFT_FIXTURES === '1') {
    await mkdir(new URL('./fixtures/', import.meta.url), {recursive: true});
    await writeFile(path, JSON.stringify(rows, null, 2) + '\n');
  }
  expect(rows).toEqual(JSON.parse(await readFile(path, 'utf8')));
}

for (const locale of ['en-US', 'zh-TW']) test.describe(locale, () => {
  test.use({locale});
  const words = STRINGS[locale === 'en-US' ? 'en' : locale];

test('session start writer retains the prompt with its original intent', async ({page}) => {
  await startFixture(page, false, {lost: true}); const form = await openStart(page);
  await form.locator('select').nth(0).selectOption('demo'); await expect(form.locator('select').nth(1)).toBeEnabled();
  await form.locator('select').nth(1).selectOption('demo-ws');
  await form.getByRole('textbox', {name: words.start_prompt, exact: true}).fill('  Original start\n');
  const before = await snapshot(page, 'start');
  await form.getByRole('button', {name: words.start_apply, exact: true}).click();
  await expect(form).toContainText('Lost start reply');
  await golden('start', [...before, ...await snapshot(page, 'start')]);
});

for (const family of ['published', 'dispatch']) test(`${family} writer retains the prompt with its original intent`, async ({page}) => {
  await publishedFixture(page, false, {lost: true, dispatch: true, bindings: [selected]});
  const form = family === 'published' ? await openPublished(page) : page.locator('[data-published-start]');
  if (family === 'dispatch') {
    await page.goto('/dashboard/#/dispatch/' + dispatchProject);
    await form.getByRole('textbox', {name: words.pub_ref}).fill('refs/heads/main');
    await form.getByRole('button', {name: words.pub_preview}).click();
    await expect(form.locator('[data-published-preview]')).toContainText(publishedSha);
    await form.getByRole('textbox', {name: words.pub_prompt, exact: true}).fill('Original dispatch');
  } else {
    await form.locator('select').first().selectOption(JSON.stringify(selected));
    await form.getByRole('textbox', {name: words.pub_ref}).fill('refs/heads/main');
    await form.getByRole('button', {name: words.pub_preview}).click();
    await expect(form.locator('[data-published-preview]')).toContainText(publishedSha);
    await form.getByRole('textbox', {name: words.pub_prompt, exact: true}).fill('  Work from this version.\nPreserve these instructions.  ');
  }
  const before = await snapshot(page, family), attachments = await snapshot(page, 'draft');
  await form.getByRole('button', {name: words.pub_apply, exact: true}).click();
  await expect(form).toContainText('Lost published start reply');
  await golden(family, [...before, ...await snapshot(page, family), ...attachments]);
});

test('orchestration writer retains item text with its original intent', async ({page}) => {
  await orchestrationFixture(page, false, {lost: true}); const form = await openOrchestration(page, 'items');
  await form.locator('select').nth(0).selectOption('demo'); await expect(form.locator('select').nth(1)).toBeEnabled();
  await form.locator('select').nth(1).selectOption('demo-ws');
  await form.locator('.orch-item input').first().fill('Review login'); await form.locator('.orch-item textarea').first().fill('  Literal first item.  ');
  await form.getByRole('button', {name: words.orch_add_item}).click();
  await form.locator('.orch-item input').nth(1).fill('Add tests'); await form.locator('.orch-item textarea').nth(1).fill('Second literal item.');
  await form.locator('.orch-check input').last().check(); const before = await snapshot(page, 'orchestrate');
  await form.locator('button.primary').click(); await expect(form).toContainText('Lost orchestration reply');
  await golden('orchestrate', [...before, ...await snapshot(page, 'orchestrate')]);
});

test('labels writer retains authored labels with its original intent', async ({page}) => {
  await labelsFixture(page, false, {lost: true}); const form = await openLabels(page);
  await form.getByRole('textbox').fill('Authored label'); const before = await snapshot(page, 'labels');
  await form.getByRole('button', {name: words.labels_save, exact: true}).click(); await expect(form).toContainText('Lost label reply');
  await golden('labels', [...before, ...await snapshot(page, 'labels')]);
});


test('PM, create and artifact review writers retain their exact envelopes', async ({page}) => {
  await page.route('**/__writer_src/**', async route => {
    const file = new URL(route.request().url()).pathname.split('/__writer_src/')[1];
    await route.fulfill({contentType: 'text/javascript', body: await readFile(new URL('../src/' + file, import.meta.url), 'utf8')});
  });
  await unavailableBriefTransport(page, '__writer_src');
  await page.route('**/__writer_src/transport/index.ts', route => route.fulfill({contentType: 'text/javascript', body: `
    export const nativeDesktop=false,nativeBriefExportSupport=false,nativeFileSupport=false;
    export function nativeBriefSave(){throw Error('No native file writes in this fixture');}
    export const nativeFilesPreview=nativeBriefSave,nativeFilesSave=nativeBriefSave,nativeFilesStatus=nativeBriefSave,nativeFilesControl=nativeBriefSave;`}));
  await page.goto('/ui-components.html');
  const rows = await page.evaluate(async () => {
    const {h} = await import('/__writer_src/ui/dom.js');
    const {pmDurableForm} = await import('/__writer_src/pm-workspace.js');
    const {createRecordForm} = await import('/__writer_src/create-record.js');
    const {mountArtifactReview} = await import('/__writer_src/artifact-review.js');
    const rows: any[] = [], namespace = 'writer-scope';
    const target = {workspace_id: 'ws_' + 'c'.repeat(32), work_item_id: 'wi_' + 'd'.repeat(20)};
    const caps = {actor: 'fixture-operator', scopes: ['manage', 'observe', 'approve'], actions:
      ['session.send', 'work_item.create', 'artifact.accept'].map(action => ({action, allowed: true}))};
    const base = {h, t: (key: string) => key, caps: () => caps, namespace, guard: () => {}, errorBox: () => h('p'), opStatus: () => '',
      api: async () => {throw Error('Lost fixture reply');}};
    const take = (family: string, suffix: string) => rows.push({family, suffix, raw: localStorage.getItem(`batc.${family}.${namespace}.${suffix}`)!});
    const field = h('textarea'), action = 'session.send';
    const form = pmDurableForm({...base, action, target, fields: {text: field}, params: (values: any) => ({text: values.text.trim(), schedule: 'after_current'}),
      preconditions: () => ({expected_attempt_id: 'att_' + 'e'.repeat(32)}), permitted: () => true, label: 'send', extra: field});
    document.body.append(form.box); field.value = '  Authored follow-up\n'; field.dispatchEvent(new Event('input'));
    take('pm', action + '.' + JSON.stringify(target)); form.stage(); take('pm', action + '.' + JSON.stringify(target)); form.dispose(); form.box.remove();
    const create = createRecordForm({...base, kind: 'work_item', projectId: 'prj_' + 'a'.repeat(20)});
    document.body.append(create.box); create.setContext({rows: []});
    const title = create.box.querySelector('input'); title.value = 'Authored work title'; title.dispatchEvent(new Event('input'));
    const createSuffix = 'work_item.prj_' + 'a'.repeat(20); take('create', createSuffix);
    create.box.dispatchEvent(new Event('submit', {cancelable: true}));
    await new Promise(resolve => setTimeout(resolve, 0)); take('create', createSuffix); create.box.remove();
    const aid = 'art_' + 'd'.repeat(32), capture = 'op_' + 'a'.repeat(32), execution = 'op_' + 'c'.repeat(32), digest = 'e'.repeat(64);
    const artifact = {artifact_id: aid, revision: 1, digest, size_bytes: 20, state: 'ready', display_name: 'result.txt', operation_id: capture,
      source: {kind: 'managed_capture', operation_id: capture, fingerprint: 'f'.repeat(64), relative_path: 'result.txt',
        evidence: {digest, size_bytes: 20, head_sha: '1'.repeat(40)}, source: {host: 'demo', session_id: 'managed-session', provenance: 'connector_managed',
          selector: {execution_operation_id: execution}, lineage: {kind: 'execution_operation', execution_operation_id: execution}}}};
    const context = {kind: 'artifact', artifact_id: aid, revision: 1}, main = h('div'); document.body.append(main);
    const off = await mountArtifactReview({...base, main, context, onEvents: () => () => {}, storageKey: `batc.artifact-review.${namespace}.${JSON.stringify(context)}`,
      api: async (method: string, path: string) => {if (method === 'POST') throw Error('Lost fixture reply'); return path.includes('/revisions/') ? {artifact} : {artifacts: []};}});
    const receipt = main.querySelector('textarea[aria-label="ar_receipt"]'); receipt.value = 'Authored artifact review'; receipt.dispatchEvent(new Event('input'));
    take('artifact-review', JSON.stringify(context));
    const accept = [...main.querySelectorAll('button')].find(button => button.textContent === 'ar_accept');
    if (accept.disabled) throw Error('Fixture did not enable artifact review');
    accept.click(); await new Promise(resolve => setTimeout(resolve, 0)); take('artifact-review', JSON.stringify(context)); off();
    return JSON.parse(JSON.stringify(rows).replaceAll(/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/g, '00000000-0000-4000-8000-000000000001'));
  });
  await golden('forms', rows);
});


test('session writer retains the text beside its hashed operation key', async ({page}) => {
  await setup(page, {source: 'external'});
  await page.evaluate(() => {
    const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
    bridge.invoke = async (command: string, args: any) => {
      if (command === 'connector_request' && args.input.path.startsWith('/sessions/demo/session-1/messages'))
        return {status: 200, data: {messages: [], next_offset: null}};
      if (command === 'connector_request' && args.input.method === 'POST') {
        const fixture = (window as any).__storage; fixture.writes++;
        return {status: 200, data: {operation: {operation_id: 'op_' + 'a'.repeat(32), actor: fixture.actor,
          idempotency_key: args.input.idempotency_key, ...args.input.body, status: 'queued', steps: []}}};
      }
      return invoke(command, args);
    };
  });
  await page.locator('textarea').first().fill('Original session message');
  await page.getByRole('button', {name: words.send, exact: true}).click();
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith('batc.key.')))).toBe(true);
  await expect.poll(() => page.evaluate(() => Object.entries(localStorage).some(([key, value]) => key.startsWith('batc.key.') && value.includes('op_')))).toBe(true);
  await golden('session', [...await snapshot(page, 'draft'), ...await snapshot(page, 'key')]);
  expect(await page.evaluate(() => (window as any).__storage.writes)).toBe(1);
});
});
