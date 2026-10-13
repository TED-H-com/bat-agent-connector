import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutGraph} from '../src/pm-dependencies-layout.js';

const ids = positions => positions.map(row => row.id).sort();

test('a diamond uses the longest path and keeps every node once', () => {
  const nodes = ['a', 'b', 'c', 'd'].map(id => ({id}));
  const links = [['a', 'b'], ['a', 'c'], ['b', 'd'], ['c', 'd']].map(([from, to]) => ({from, to}));
  const layout = layoutGraph(nodes, links);
  const layer = Object.fromEntries(layout.positions.map(row => [row.id, row.layer]));
  assert.equal(layer.a, 0);
  assert.equal(layer.b, 1);
  assert.equal(layer.c, 1);
  assert.equal(layer.d, 2);
  assert.deepEqual(ids(layout.positions), ['a', 'b', 'c', 'd']);
  assert.equal(layout.layers, 3);
});

test('a chain increases depth from the source', () => {
  const layout = layoutGraph([{id: 'a'}, {id: 'b'}, {id: 'c'}], [{from: 'a', to: 'b'}, {from: 'b', to: 'c'}]);
  const layer = Object.fromEntries(layout.positions.map(row => [row.id, row.layer]));
  assert.deepEqual(layer, {a: 0, b: 1, c: 2});
  assert.equal(layout.positions.length, 3);
});

test('a cycle finishes and still places every node once', () => {
  const layout = layoutGraph([{id: 'a'}, {id: 'b'}], [{from: 'a', to: 'b'}, {from: 'b', to: 'a'}]);
  assert.deepEqual(ids(layout.positions), ['a', 'b']);
  assert.ok(layout.layers >= 1);
});

test('an empty graph has no layers', () => {
  assert.deepEqual(layoutGraph([], []), {positions: [], layers: 0});
});
