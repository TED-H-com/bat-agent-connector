import test from 'node:test';
import assert from 'node:assert/strict';
import {MAX_DISCUSS_REFS, MAX_DISCUSS_TITLE, isContextRef, discussHref, parseDiscussRefs} from '../src/pm-discuss-link.js';

const hex = '0123456789abcdef'.repeat(2);
const plan = 'pmrev_' + hex;
const baseline = 'pmrb_' + hex;
const snapshot = 'pmsnap_' + hex;
const refs = [
  {kind: 'snapshot', id: snapshot},
  {kind: 'plan_revision', id: plan},
  {kind: 'requirement', id: 'login.v2_retry-1', revision_id: plan},
  {kind: 'milestone', id: 'release.v2_candidate-1', revision_id: baseline},
  {kind: 'work_item', id: 'wi_' + hex.slice(0, 20)},
  {kind: 'governance', id: 'pmgov_' + hex},
  {kind: 'finding', id: 'pmfn_' + hex, observation_id: 'pmgh_' + hex, document_digest: hex.repeat(2)},
  {kind: 'repair', id: 'pmrp_' + hex},
  {kind: 'follow_up', id: 'pmfu_' + hex},
  {kind: 'proposal', id: 'pmcp_' + hex},
  {kind: 'issue_mapping', id: 'pmim_' + hex},
  {kind: 'meeting', id: 'pmmeet_' + hex},
  {kind: 'lifecycle_transition', id: 'pmlt_' + hex},
];
const empty = {refs: [], title: '', from: '', report: ''};
const queryOf = (href: string) => new URL(href, 'http://x').hash.split('?')[1];
const queryFor = (values: unknown[]) => new URLSearchParams(values.map(value => ['ref', JSON.stringify(value)]));

for (const ref of refs) {
  test(`${ref.kind} round-trips in the backend context shape`, () => {
    assert.equal(isContextRef(ref), true);
    const query = queryOf(discussHref({workspaceId: 'ws_fixture', refs: [Object.freeze(ref)]}));
    assert.deepEqual(parseDiscussRefs(query), {...empty, refs: [ref]});
    assert.deepEqual(parseDiscussRefs(new URLSearchParams(query)), {...empty, refs: [ref]});
  });
}

test('requirement and milestone IDs preserve every allowed character and both length boundaries', () => {
  for (const kind of ['requirement', 'milestone']) {
    const revision_id = kind === 'requirement' ? plan : baseline;
    const values = ['a', 'abcdefghijklmnopqrstuvwxyz0123456789_.-', 'z'.repeat(80)]
      .map(id => ({kind, id, revision_id}));
    assert.deepEqual(parseDiscussRefs(queryOf(discussHref({workspaceId: 'ws_fixture', refs: values}))).refs, values);
    for (const id of ['', 'z'.repeat(81), '1login', '_login', 'Login', '需求']) {
      assert.equal(isContextRef({kind, id, revision_id}), false, id);
    }
  }
});

test('reserved characters round-trip in workspace and title; backend-forbidden IDs are rejected', () => {
  const workspaceId = 'ws:fixture@team/path~v1?part=2&x+#% 工作';
  const title = '討論: @owner / release~v1?part=2&x+#% "決策"';
  const href = discussHref({workspaceId, refs: [refs[2]], title});
  assert.equal(href.split('/')[2], encodeURIComponent(workspaceId));
  assert.equal(decodeURIComponent(href.split('/')[2]), workspaceId);
  assert.deepEqual(parseDiscussRefs(queryOf(href)), {...empty, refs: [refs[2]], title});
  // pm_thread_contexts.py allows only ASCII slugs or prefixed hex identities.
  const invalid = [':', '@', '/', '~', '%', '&', '+', '?', '#'].flatMap(char =>
    refs.map(ref => ({...ref, id: ref.id + char})));
  assert.ok(invalid.every(ref => !isContextRef(ref)));
  assert.deepEqual(parseDiscussRefs(queryFor(invalid)), empty);
  assert.deepEqual(parseDiscussRefs(queryOf(discussHref({workspaceId, refs: invalid}))), empty);
});

test('unknown kinds, wrong prefixes, composite IDs and invalid shapes are dropped', () => {
  const invalid = [
    null, false, 1, 'snapshot', [], {}, {id: snapshot}, {kind: 'snapshot'},
    {kind: 'unknown', id: snapshot}, {kind: 'constructor', id: snapshot}, {kind: '__proto__', id: snapshot},
    {kind: ['snapshot'], id: snapshot}, {kind: 'snapshot', id: 1},
    ...refs.filter(ref => !['requirement', 'milestone'].includes(ref.kind)).map(ref => ({...ref, id: 'wrong_' + hex})),
    {kind: 'meeting', id: 'pmmeet_' + hex + ':item-1'},
    {kind: 'meeting_item', id: 'pmmeet_' + hex, item_id: 'item-1'},
    {kind: 'review', id: 'pmrevw_' + hex},
    {kind: 'plan_revision', id: 'pmrevw_' + hex},
    {kind: 'candidate', id: 'pmrve_' + hex},
    {kind: 'proposal', id: 'pmrve_' + hex},
  ];
  for (const ref of invalid) assert.equal(isContextRef(ref), false, JSON.stringify(ref));
  assert.deepEqual(parseDiscussRefs(queryFor([...invalid, refs[0]])), {...empty, refs: [refs[0]]});
  assert.deepEqual(parseDiscussRefs(queryOf(discussHref({workspaceId: 'ws_fixture', refs: [...invalid, refs[0]]}))),
    {...empty, refs: [refs[0]]});
});

test('each required identity field is a complete, exact string', () => {
  for (const ref of refs) {
    for (const [key, value] of Object.entries(ref)) {
      if (key === 'kind') continue;
      const missing = {...ref};
      delete missing[key];
      const badGrammar = key === 'id' && ['requirement', 'milestone'].includes(ref.kind)
        ? ['a'.repeat(81)] : [value.slice(0, -1), value + '0', value.slice(0, -1) + 'g', value.slice(0, -1) + 'F'];
      const invalid = [missing, ...[null, 1, true, {}, [value], '', ...badGrammar,
        value.toUpperCase(), ' ' + value, value + '\n', value + '\r', value + '\u2028', value + '\u2029', value + '\0']
        .map(bad => ({...ref, [key]: bad}))];
      for (const bad of invalid) assert.equal(isContextRef(bad), false, JSON.stringify(bad));
      assert.deepEqual(parseDiscussRefs(queryFor(invalid)), empty);
      assert.deepEqual(parseDiscussRefs(queryOf(discussHref({workspaceId: 'ws_fixture', refs: invalid}))), empty);
    }
  }
  assert.equal(isContextRef({...refs[2], revision_id: baseline}), false);
  assert.equal(isContextRef({...refs[3], revision_id: plan}), false);
  assert.equal(isContextRef({...refs[6], observation_id: 'pmfn_' + hex}), false);
});

test('extra fields and display metadata are refused and never enter the URL', () => {
  const invalid = ['label', 'position', 'available', 'bound_label', 'path', 'revision_id'].map(key =>
    ({...refs[0], [key]: 'private-display-metadata'}));
  invalid.push({...refs[0], available: false});
  for (const ref of invalid) assert.equal(isContextRef(ref), false);
  const href = discussHref({workspaceId: 'ws_fixture', refs: [...invalid, refs[1]]});
  assert.equal(decodeURIComponent(href).includes('private-display-metadata'), false);
  assert.deepEqual(parseDiscussRefs(queryOf(href)).refs, [refs[1]]);
  assert.deepEqual(parseDiscussRefs(queryFor([...invalid, refs[1]])).refs, [refs[1]]);
  assert.equal(isContextRef(Object.create(refs[0])), false);
});

test('deduplication uses kind and every identity field, independent of property order', () => {
  const requirement = refs[2], finding = refs[6];
  const distinct = [
    requirement,
    {...requirement, revision_id: 'pmrev_' + 'a'.repeat(32)},
    {...requirement, kind: 'milestone', revision_id: baseline},
    finding,
    {...finding, observation_id: 'pmgh_' + 'a'.repeat(32)},
    {...finding, document_digest: 'b'.repeat(64)},
  ];
  const duplicates = distinct.flatMap(ref => [ref, Object.fromEntries(Object.entries(ref).reverse())]);
  assert.deepEqual(parseDiscussRefs(queryFor(duplicates)).refs, distinct);
  assert.deepEqual(parseDiscussRefs(queryOf(discussHref({workspaceId: 'ws_fixture', refs: duplicates}))).refs, distinct);
});

test('both directions keep the first eight valid distinct refs in caller order', () => {
  assert.equal(MAX_DISCUSS_REFS, 8);
  const values = [null, ...refs.flatMap(ref => [ref, {...ref}])];
  const expected = refs.slice(0, 8);
  assert.deepEqual(parseDiscussRefs(queryFor(values)).refs, expected);
  const href = discussHref({workspaceId: 'ws_fixture', refs: values});
  assert.equal(new URLSearchParams(queryOf(href)).getAll('ref').length, 8);
  assert.deepEqual(parseDiscussRefs(queryOf(href)).refs, expected);
});

test('titles trim and truncate at 160 Python characters without splitting emoji', () => {
  assert.equal(MAX_DISCUSS_TITLE, 160);
  for (const [input, expected] of [
    ['  討論需求與時程　', '討論需求與時程'],
    ['a'.repeat(161), 'a'.repeat(160)],
    ['a'.repeat(159) + '😀尾', 'a'.repeat(159) + '😀'],
    ['a'.repeat(160) + '😀', 'a'.repeat(160)],
    ['😀'.repeat(161), '😀'.repeat(160)],
    ['討'.repeat(159) + '😀', '討'.repeat(159) + '😀'],
    ['e\u0301'.repeat(81), 'e\u0301'.repeat(80)],
    [' \t\n　', ''],
  ]) {
    assert.equal(parseDiscussRefs(new URLSearchParams({title: input})).title, expected);
    const href = discussHref({workspaceId: 'ws_fixture', title: input});
    assert.equal(parseDiscussRefs(queryOf(href)).title, expected);
    assert.equal(new URLSearchParams(queryOf(href)).has('title'), Boolean(expected));
  }
});

test('only project view IDs and valid snapshot reports survive in either direction', () => {
  for (const from of ['overview', 'discussions', 'requirements', 'schedule', 'dependencies', 'issues', 'agents', 'board']) {
    const href = discussHref({workspaceId: 'ws_fixture', from, report: snapshot});
    assert.deepEqual(parseDiscussRefs(queryOf(href)), {...empty, from, report: snapshot});
  }
  for (const from of ['', 'Overview', ' overview ', 'settings', 'toString', '__proto__', '#/pm/ws_fixture', 'https://example.com']) {
    assert.equal(parseDiscussRefs(new URLSearchParams({from})).from, '');
    assert.equal(new URLSearchParams(queryOf(discussHref({workspaceId: 'ws_fixture', from}))).has('from'), false);
  }
  for (const report of ['', plan, 'pmsnap_fixture', snapshot + '\n', snapshot + ':1', snapshot.toUpperCase()]) {
    assert.equal(parseDiscussRefs(new URLSearchParams({report})).report, '');
    assert.equal(new URLSearchParams(queryOf(discussHref({workspaceId: 'ws_fixture', report}))).has('report'), false);
  }
  assert.equal(discussHref({workspaceId: 'ws_fixture'}), '#/pm/ws_fixture/discussions/new');
  assert.deepEqual(parseDiscussRefs(''), empty);
});

test('malformed and oversized query values cannot prevent later valid refs from being parsed', () => {
  const valid = queryFor([refs[0]]).toString();
  for (const hostile of [
    '%', '%E0%A4%A', '&&&&', 'ref=', 'ref=%', 'ref=%E0%A4%A', 'ref={', 'ref=null',
    'ref=%5B%5D', 'ref=%7B%22__proto__%22%3A%7B%7D%7D', 'ref=' + 'x'.repeat(10_000),
    queryFor([{kind: 'requirement', id: 'a'.repeat(10_000), revision_id: plan}]).toString(),
    'title=' + '字'.repeat(10_000) + '&from=' + 'x'.repeat(10_000) + '&report=' + 'x'.repeat(10_000),
  ]) {
    assert.doesNotThrow(() => parseDiscussRefs(hostile));
    assert.deepEqual(parseDiscussRefs(hostile).refs, []);
    for (const query of [hostile + '&&' + valid, new URLSearchParams(hostile + '&&' + valid)]) {
      const parsed = parseDiscussRefs(query);
      assert.deepEqual(parsed.refs, [refs[0]]);
      assert.ok(Array.from(parsed.title).length <= 160);
      assert.equal(parsed.from, '');
      assert.equal(parsed.report, '');
    }
  }
  assert.deepEqual(parseDiscussRefs(undefined), empty);
  assert.deepEqual(parseDiscussRefs(null), empty);
  assert.deepEqual(parseDiscussRefs({toString() { throw new Error('do not coerce arbitrary input'); }}), empty);
});

test('non-string details and non-array refs are omitted without coercion', () => {
  for (const value of [null, false, 123, {}, [], ['overview']]) {
    const href = discussHref({workspaceId: 'ws_fixture', refs: value, title: value, from: value, report: value});
    assert.equal(href, '#/pm/ws_fixture/discussions/new');
  }
});

test('discussion return origins accept exact session routes and reject external or malformed paths', () => {
  const from = '#/session/demo%20host/complete%2Fsession%3Fid?from=requirements';
  assert.equal(parseDiscussRefs(queryOf(discussHref({workspaceId: 'ws_fixture', from}))).from, from);
  for (const from of ['https://example.test', '//example.test', '#/session//id', '#/session/host/',
    '#/session/host/id/extra', '#/session/host/%', '#/session/host/id?from=settings',
    '#/session/host/id?from=overview&from=board', '#/session/host/id?to=https://example.test', '#/session/host/%00id']) {
    assert.equal(parseDiscussRefs(new URLSearchParams({from})).from, '');
    assert.equal(new URLSearchParams(queryOf(discussHref({workspaceId: 'ws_fixture', from}))).has('from'), false);
  }
});
