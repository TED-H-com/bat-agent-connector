import assert from 'node:assert/strict';
import {test} from 'node:test';
import {operationPage} from '../src/operation-page.js';

const id = (digit: string) => 'op_' + digit.repeat(32);
const page = (time: any = 100, key: any = id('8')) => ({
  operations: [{operation_id: id('9')}], next_before: time, next_before_id: key,
});

test('preserves the central cursor even when its row is not visible', () => {
  const result = operationPage(page());
  assert.deepEqual(result?.next, {time: 100, id: id('8')});
  assert.deepEqual(result?.operations, [{operation_id: id('9')}]);
  assert.deepEqual(operationPage({...page(), operations: []})?.next, result?.next);
  assert.deepEqual(operationPage({operations: [], next_before: null, next_before_id: null}), {operations: [], next: null});
});

test('equal timestamps advance only through decreasing operation IDs', () => {
  const previous = {time: 100, id: id('8')};
  assert.deepEqual(operationPage(page(100, id('7')), previous)?.next, {time: 100, id: id('7')});
  for (const key of [id('8'), id('9')]) assert.equal(operationPage(page(100, key), previous), null);
  assert.equal(operationPage(page(101, id('1')), previous), null);
  assert.deepEqual(operationPage(page(99, id('f')), previous)?.next, {time: 99, id: id('f')});
});

test('legacy timestamp-only pages retain strict timestamp advancement', () => {
  const first = operationPage({operations: [], next_before: 100});
  assert.deepEqual(first?.next, {time: 100, id: null});
  assert.equal(operationPage({operations: [], next_before: 100}, first?.next), null);
  assert.equal(operationPage(page(100, id('7')), first?.next), null);
  assert.deepEqual(operationPage({operations: [], next_before: 99}, first?.next)?.next, {time: 99, id: null});
  assert.deepEqual(operationPage({operations: []}), {operations: [], next: null});
});

test('paired-to-legacy continuation must still advance the timestamp', () => {
  const previous = {time: 100, id: id('8')};
  assert.equal(operationPage({operations: [], next_before: 100}, previous), null);
  assert.deepEqual(operationPage({operations: [], next_before: 99}, previous)?.next, {time: 99, id: null});
});

test('partial and malformed cursor pairs cannot silently end or advance history', () => {
  for (const time of [0, -1, '100', NaN, Infinity, true]) assert.equal(operationPage(page(time)), null);
  for (const key of [null, '', 'invalid', 'op_' + 'a'.repeat(33), id('A'), 123])
    assert.equal(operationPage(page(100, key)), null);
  assert.equal(operationPage({...page(), next_before_id: undefined}), null);
  assert.equal(operationPage(page(null, id('8'))), null);
  assert.equal(operationPage({operations: [], next_before_id: id('8')}), null);
});

test('invalid operation collections never replace the loaded window', () => {
  for (const operations of [undefined, {}, [null], [{operation_id: 'bad'}]])
    assert.equal(operationPage({...page(), operations}), null);
  assert.equal(operationPage(null), null);
});
