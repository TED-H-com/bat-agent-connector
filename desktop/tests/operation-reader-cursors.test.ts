import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {parse} from 'acorn';
import {runInNewContext} from 'node:vm';
import {operationPage} from '../src/operation-page.js';

// Execute each real reader closure with only its view dependencies stubbed.
// Extract by AST so tests do not duplicate the pagination implementation.
function reader(file: string, name: string, context: any) {
  const source = readFileSync(new URL(`../src/${file}.js`, import.meta.url), 'utf8');
  let found: any;
  function walk(node: any) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'FunctionDeclaration' && node.id.name === name) found = node;
    if (node.type === 'VariableDeclarator' && node.id.name === name) found = node.init;
    for (const value of Object.values(node)) if (Array.isArray(value)) value.forEach(walk); else walk(value);
  }
  walk(parse(source, {ecmaVersion: 'latest', sourceType: 'module'}));
  assert.ok(found, `${file}:${name}`);
  return runInNewContext(`(${source.slice(found.start, found.end)})`, {operationPage, ...context});
}
const noop = () => {};
const id = (n: number) => 'op_' + n.toString(16).padStart(32, '0');
const node = () => ({replaceChildren: noop, contains: () => false, dataset: {}});

function harness(kind: string, api: any) {
  const context: any = {api, request: api, valid: () => true, guard: noop, t: (key: string) => key,
    error: () => Error('invalid'), invalid: () => Error('invalid'), filtered: false};
  if (kind === 'attention') {
    Object.assign(context, {document: {activeElement: null}, syncBusy: noop, skeleton: noop,
      reconcile: noop, publishPending: noop, open: noop, errorBox: noop, inlineError: noop,
      tab: 'active', lastPending: null});
    const section: any = {size: 0, field: 'operations', cursor: 'next_before', param: 'before',
      path: '/operations?limit=50', list: node(), status: node(), count: node(), badge: node(), more: node(),
      section: node(), row: (row: any) => row};
    const rows: any[] = [];
    section.list.replaceChildren = (...items: any[]) => rows.splice(0, rows.length, ...items);
    const load = reader(kind, 'load', context);
    return {run: async (bound = 3) => {await load(section, bound); return rows;}, section};
  }
  if (kind === 'artifact-review') {
    const candidates = new Map();
    Object.assign(context, {source: {host: 'fixture', session_id: 'session'}, frozen: () => false,
      currentView: () => true, update: noop, executionReads: 0, executions: ['session.send'], pages: new Map(),
      candidates, sourceFromOperation: () => context.source, managedCaptureExecution: () => true,
      stable: JSON.stringify, actionLabel: (s: string) => s, renderChoice: noop});
    const load = reader(kind, 'loadExecutions', context);
    return {run: async () => {await load(); await load(true); await load(true);
      return [...candidates.values()].map((v: any) => ({operation_id: v.selector.execution_operation_id}));}};
  }
  if (kind === 'pm-quality-reviews') {
    const load = reader(kind, 'pages', context);
    return {run: () => load('/operations?action=repository.continue', 'operations', 1,
      {cursor: 'next_before', wid: false, descending: true})};
  }
  return {run: () => reader(kind, 'verifierOperations', context)(1)};
}

for (const kind of ['attention', 'artifact-review', 'pm-quality-reviews', 'pm-maintenance-repairs']) {
  for (const empty of [false, true]) test(`${kind}: ${empty ? 'empty continuing page' : 'equal timestamp boundary'}`, async () => {
    const calls: URL[] = [];
    const api = async (_method: string, path: string) => {
      const url = new URL(path, 'http://fixture'); calls.push(url);
      const before = url.searchParams.get('before'), beforeId = url.searchParams.get('before_id');
      if (calls.length > 1) {
        assert.equal(before, '42.5');
        assert.equal(beforeId, id(5 - calls.length));
      }
      const index = calls.length;
      return {operations: empty && index === 1 ? [] : [{operation_id: id(4 - index)}],
        next_before: index < 3 ? 42.5 : null, next_before_id: index < 3 ? id(4 - index) : null};
    };
    const f = harness(kind, api), rows = await f.run();
    assert.deepEqual(Array.from(rows, (r: any) => r.operation_id), (empty ? [2, 1] : [3, 2, 1]).map(id));
    assert.equal(calls.length, 3);
  });
}

test('Attention keeps an empty continuing section and its More control visible', async () => {
  const f = harness('attention', async () => ({operations: [], next_before: 42.5, next_before_id: id(3)}));
  await f.run(1);
  assert.equal(f.section.partial, true);
  assert.equal(f.section.section.hidden, false);
  assert.equal(f.section.more.hidden, false);
});

for (const kind of ['pm-quality-reviews', 'pm-maintenance-repairs']) test(`${kind}: retains the 20-page history bound`, async () => {
  let calls = 0;
  const f = harness(kind, async () => ({operations: [], next_before: 100 - ++calls,
    next_before_id: id(1)}));
  await assert.rejects(f.run(), {message: kind === 'pm-quality-reviews' ? 'pmq_history_bound' : 'pmr_limit'});
  assert.equal(calls, 20);
});
