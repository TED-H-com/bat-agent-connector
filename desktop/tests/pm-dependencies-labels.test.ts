import {test} from 'node:test';
import assert from 'node:assert/strict';
import {dependencyNodeNames, wrapDependencyName} from '../src/pm-dependencies-labels.js';

test('duplicate numbering follows only same-name IDs, independent of input order and unrelated insertions', () => {
  const rows = [{id: 'c:8', name: 'api'}, {id: 'c:1', name: 'web'}, {id: 'c:3', name: 'api'}];
  for (const nodes of [rows, [...rows].reverse(), [{id: 'c:0', name: 'other'}, ...rows]]) {
    const name = dependencyNodeNames(nodes);
    assert.equal(name('c:3'), 'api [1]');
    assert.equal(name('c:8'), 'api [2]');
    assert.equal(name('c:1'), 'web');
  }
  assert.equal(dependencyNodeNames([...rows, {id: 'c:3', name: 'peer alias'}])('c:3'), 'api [1]');
});

test('wrapping preserves delimiters and fitting words, including long unbroken names', () => {
  const measure = (text: string) => Array.from(text).length;
  const name = 'alpha-beta_gamma/delta.epsilon zeta';
  const lines = wrapDependencyName(name, 10, measure);
  assert.equal(lines.join(''), name);
  assert.ok(lines.every(line => measure(line) <= 10));
  for (const word of ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta']) assert.ok(lines.some(line => line.includes(word)));
  assert.deepEqual(wrapDependencyName('abcdefghijklmnop', 5, measure), ['abcde', 'fghij', 'klmno', 'p']);
});

test('CJK wraps between characters without losing mixed English names', () => {
  const name = '會員服務membership/關係圖';
  const lines = wrapDependencyName(name, 12, (text: string) => Array.from(text).reduce((n, char) => n + (/\p{Script=Han}/u.test(char) ? 2 : 1), 0));
  assert.equal(lines.join(''), name);
  assert.ok(lines.some(line => line.includes('membership')));
});
