import {test} from 'node:test';
import assert from 'node:assert/strict';
import {messageBlocks, tableCells} from '../src/message-format.js';

test('fenced code preserves CRLF, empty lines and unclosed streaming content', () => {
  assert.deepEqual(messageBlocks('before\n```js\r\nconst x = 1;\r\n\r\n```\r\nafter'), [
    {kind:'text',text:'before\n'}, {kind:'code',language:'js',text:'const x = 1;\r\n\r\n'}, {kind:'text',text:'after'}]);
  assert.deepEqual(messageBlocks('~~~python\nprint(1)\n\nmore'), [{kind:'code',language:'python',text:'print(1)\n\nmore'}]);
  assert.deepEqual(messageBlocks('````md\n```\n| a | b |\n| --- | --- |\n````'),
    [{kind:'code',language:'md',text:'```\n| a | b |\n| --- | --- |\n'}]);
});
test('tables require a valid divider and preserve code and escaped pipes', () => {
  assert.deepEqual(tableCells('| A\\|B | `x|y` |'), ['A|B','`x|y`']);
  assert.deepEqual(messageBlocks('| A | B |\n| :--- | ---: |\n| a\\|b | `x|y` |\ntrailing'), [
    {kind:'table',header:['A','B'],rows:[['a|b','`x|y`']]}, {kind:'text',text:'trailing'}]);
  for (const text of ['A | B\nno divider', '| A | B |\n| --- |\n', 'unclosed **markup', '<img src=x onerror=alert(1)>'])
    assert.deepEqual(messageBlocks(text), [{kind:'text',text}]);
});
test('table-like content in streamed code stays code until an actual closing fence', () => {
  const code = '```txt\n| A | B |\n| --- | --- |\n| 1 | 2 |';
  assert.equal(messageBlocks(code)[0].kind, 'code');
  assert.equal(messageBlocks(code)[0].text, code.slice(7));
  assert.equal(messageBlocks(code+'\n```\n| A | B |\n| --- | --- |')[1].kind, 'table');
});
test('large messages use a lossless plain-text fallback', () => {
  for (const text of ['```\n'+'x'.repeat(200001), '\n'.repeat(4001), '> '+'x'.repeat(200001), '> quote\n'.repeat(4001)])
    assert.deepEqual(messageBlocks(text), [{kind:'text',text}]);
});

test('quotes remove only one explicit outer marker, preserving nested markup and exact newlines', () => {
  const source = '> **Keep** `markup`  \r\n>\r\n>> nested\n> > nested with space\n   >\t\tindented\n>  two spaces\n>no space';
  assert.deepEqual(messageBlocks(source), [{kind:'quote', text:
    '**Keep** `markup`  \r\n\r\n> nested\n> nested with space\n\tindented\n two spaces\nno space'}]);
  assert.deepEqual(messageBlocks('>'), [{kind:'quote',text:''}]);
});

test('quote boundaries exclude unmarked lines, escaped markers and indented code', () => {
  assert.deepEqual(messageBlocks('before\n> one\n\n> two\nafter'), [
    {kind:'text',text:'before\n'}, {kind:'quote',text:'one\n'}, {kind:'text',text:'\n'},
    {kind:'quote',text:'two\n'}, {kind:'text',text:'after'},
  ]);
  for (const text of ['    > indented', '\t> indented', '\\> escaped', 'inline > marker'])
    assert.deepEqual(messageBlocks(text), [{kind:'text',text}]);
});

test('quotes do not reinterpret fenced code or swallow adjacent tables', () => {
  assert.deepEqual(messageBlocks('```md\n> code\n```\n> ```js\n> const x = 1;\n> ```\n'), [
    {kind:'code',language:'md',text:'> code\n'}, {kind:'quote',text:'```js\nconst x = 1;\n```\n'},
  ]);
  assert.deepEqual(messageBlocks('| A | B |\n| --- | --- |\n> | C | D |\n> | --- | --- |\n'), [
    {kind:'table',header:['A','B'],rows:[]}, {kind:'quote',text:'| C | D |\n| --- | --- |\n'},
  ]);
});
