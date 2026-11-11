import {test} from 'node:test';
import assert from 'node:assert/strict';
import {historyTextSelection, historySelectionKey, historyReferenceKey, matchesHistoryContent} from '../src/pm-history-selection.js';

for (const [name, text, start, end, points] of [
  ['emoji', 'A🦇Z', 1, 3, [1,2]],
  ['after emoji', 'A🦇Z', 3, 4, [2,3]],
  ['CRLF', 'A\r\n🦇B', 2, 4, [3,4]],
  ['CRLF included', 'A\r\n🦇B', 1, 4, [1,4]],
  ['CR and LF', 'A\rB\nC', 1, 5, [1,5]],
  ['combining characters', 'e\u0301中', 0, 2, [0,2]],
  ['ZWJ sequence', '👩‍💻!', 0, 5, [0,3]],
  ['whitespace', ' \t\n', 0, 3, [0,3]],
] as const) test(`textarea offsets retain original codepoints: ${name}`, () => {
  assert.deepEqual(historyTextSelection(text, 2, start, end), {kind:'text',part_index:2,start_codepoint:points[0],end_codepoint:points[1]});
});
for (const [name, text, part, start, end] of [
  ['split start', 'A🦇Z', 0, 2, 4], ['split end', 'A🦇Z', 0, 1, 2],
  ['empty', 'abc', 0, 1, 1], ['backward', 'abc', 0, 2, 1],
  ['negative', 'abc', 0, -1, 1], ['outside', 'abc', 0, 0, 4],
  ['bad part', 'abc', 128, 0, 1], ['surrogate', '\uD800', 0, 0, 1],
  ['oversize', 'a'.repeat(16001), 0, 0, 1], ['fractional', 'abc', 0, 0.5, 1],
] as const) test(`refuse invalid range: ${name}`, () => assert.equal(historyTextSelection(text, part, start, end), null));

test('selection schemas are exact and reject boolean offsets', () => {
  assert.equal(historySelectionKey({kind:'entry',part_index:0}), null);
  assert.equal(historySelectionKey({kind:'text',part_index:0,start_codepoint:false,end_codepoint:1}), null);
  assert.equal(historySelectionKey({kind:'text',part_index:0,start_codepoint:0,end_codepoint:16001}), null);
});
test('preview verification preserves text, tools, coverage and actual part boundaries', () => {
  const entry = {role:'assistant',clipped:true,omitted_parts:2,parts:[{kind:'tool_request',name:'read'}, {kind:'text',text:'A🦇\r\nZ'}]};
  const selection = {kind:'text',part_index:1,start_codepoint:1,end_codepoint:4};
  const content = {...entry,parts:[{kind:'text',text:'🦇\r\n'}]};
  assert.equal(matchesHistoryContent(content, entry, selection), true);
  for (const bad of [{...content,clipped:false}, {...content,omitted_parts:0}, {...content,parts:[{kind:'text',text:'🦇\n'}]}, {...content,parts:[{kind:'text',text:'🦇\r\n',extra:true}]}])
    assert.equal(matchesHistoryContent(bad, entry, selection), false);
  assert.equal(matchesHistoryContent(content,entry,{...selection,part_index:0}), false);
  assert.equal(matchesHistoryContent(content,entry,{...selection,end_codepoint:99}), false);
  assert.equal(matchesHistoryContent(entry,entry,{kind:'entry'}), true);
});
test('reference equality ignores key order and refuses extra fields without minting hashes', () => {
  const ref = {schema:'external_history_ref/v1',server_id:'example',workspace_id:'workspace',source_id:'source',entry_id:'entry',
    source_digest:'a'.repeat(64),content_digest:'b'.repeat(64),excerpt_digest:'c'.repeat(64),selection:{kind:'entry'}};
  assert.equal(historyReferenceKey(ref), historyReferenceKey(Object.fromEntries(Object.entries(ref).reverse())));
  assert.equal(historyReferenceKey({...ref,extra:true}), null);
  assert.equal(historyReferenceKey({...ref,excerpt_digest:'not-a-digest'}), null);
});
