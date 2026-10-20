import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {tokenizer} from 'acorn';
import {decideLayout} from '../src/workbench-mode.js';

const forms = [
  ['', 'classic', 'default', null],
  ['?', 'classic', 'default', null],
  ['?other=1', 'classic', 'default', null],
  ['?workbench=0', 'classic', 'url', null],
  ['?workbench=1', 'workbench', 'url', null],
  ['?workbench=1&workbench=0', 'workbench', 'url', null],
  ['?workbench=0&workbench=1', 'classic', 'url', null],
  ['?workbench=%31', 'workbench', 'url', null],
  ['?workbench=junk', 'classic', 'url', 'unrecognized_workbench_value'],
  ['?workbench=01', 'classic', 'url', 'unrecognized_workbench_value'],
  ['?workbench=', 'classic', 'url', 'unrecognized_workbench_value'],
] as const;
for (const [search, layout, source, reason] of forms) test(`layout decision preserves URL semantics: ${search || '(none)'}`, () => {
  const result = decideLayout({search});
  assert.deepEqual(result, {layout, source, status: 'preview', reason});
  assert.ok(Object.isFrozen(result));
});

test('injected policy can restrict preview without choosing a default or granting acceptance', () => {
  assert.deepEqual(decideLayout({search: '?workbench=1', policy: {allowWorkbench: false}}),
    {layout: 'classic', source: 'policy', status: 'preview', reason: 'policy_disabled'});
  for (const policy of [undefined, {}, {allowWorkbench: true}, {allowWorkbench: false}]) {
    assert.equal(decideLayout({policy}).layout, 'classic');
    for (const [search] of forms) assert.equal(decideLayout({search, policy}).status, 'preview');
  }
  assert.equal(decideLayout({search: '?workbench=1', policy: {allowWorkbench: true}}).layout, 'workbench');
  assert.equal(decideLayout({search: '?workbench=0', policy: {allowWorkbench: false}}).reason, null);
});

test('page decision reads the URL once and remains fixed for every importer', () => {
  // A child isolates the page-global input from other Node test modules.
  const moduleUrl = new URL('../src/workbench-mode.js', import.meta.url).href;
  const result = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', `
    let reads = 0;
    const page = {search: '?workbench=1'};
    Object.defineProperty(globalThis, 'location', {get() {reads++; return page;}});
    const first = await import(${JSON.stringify(moduleUrl)});
    page.search = '?workbench=0';
    const second = await import(${JSON.stringify(moduleUrl)});
    console.log(JSON.stringify({reads, same: first.layoutDecision === second.layoutDecision,
      decision: second.layoutDecision}));
  `], {encoding: 'utf8'}));
  assert.deepEqual(result, {reads: 1, same: true,
    decision: {layout: 'workbench', source: 'url', status: 'preview', reason: null}});
});

function layoutQueryReads(source: string): number {
  // Tokenization also accepts the type syntax in our .ts modules and ignores
  // comments. It needs no TypeScript compiler API or generated source copy.
  const tokens = [...tokenizer(source, {ecmaVersion: 'latest', sourceType: 'module'})];
  let reads = 0;
  for (const [index, token] of tokens.entries()) {
    if (token.value === 'location') {
      const member = index + (tokens[index + 1]?.type.label === ']' ? 2 : 1);
      if (['.', '?.', '['].includes(tokens[member]?.type.label) && tokens[member + 1]?.value === 'search') reads++;
    }
    if (['get', 'getAll', 'has'].includes(token.value) && tokens[index + 1]?.type.label === '('
      && tokens[index + 2]?.value === 'workbench') reads++;
  }
  return reads;
}

test('query guard catches outer-query and URL workbench reads while allowing toggle writes', () => {
  for (const source of [
    'new URLSearchParams(location.search).get("workbench")',
    'window.location["search"]',
    'new URL(location.href).searchParams.get("workbench")',
    'params.getAll("workbench")',
  ]) assert.ok(layoutQueryReads(source), source);
  assert.equal(layoutQueryReads('url.searchParams.set("workbench", "1"); location.hash = "#/home";'), 0);
});

test('only the layout decision module reads the outer query or workbench parameter', () => {
  const root = new URL('../src/', import.meta.url);
  const scan = (directory: URL) => {
    for (const entry of readdirSync(directory, {withFileTypes: true})) {
      const url = new URL(entry.name + (entry.isDirectory() ? '/' : ''), directory);
      if (entry.isDirectory()) scan(url);
      else if (/\.[cm]?[jt]s$/.test(entry.name) && url.href !== new URL('workbench-mode.js', root).href)
        assert.equal(layoutQueryReads(readFileSync(url, 'utf8')), 0, fileURLToPath(url));
    }
  };
  scan(root);
});
