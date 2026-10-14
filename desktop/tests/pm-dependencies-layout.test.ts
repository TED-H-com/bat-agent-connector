import test from 'node:test';
import assert from 'node:assert/strict';
import {boundGraph, layoutGraph, limitColumns} from '../src/pm-dependencies-layout.js';

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


test('demo declarations are separate from captured edges and impact paths', async () => {
  const {dependencyDocument, dependencyFixtureIds, dependencyImpact} = await import('./demo/dependencies.ts');
  const {demoProject} = await import('./demo/project.ts');
  const project = demoProject('en'), doc = dependencyDocument(project);
  assert.equal(doc.edges.some(row => row.dependency_key === dependencyFixtureIds.keys.legacy), false);
  assert.equal(doc.counts.edges, doc.edges.length);
  assert.equal(doc.counts.dependencies, Object.values(doc.dependencies).flat().length);
  const declared = dependencyImpact(project, dependencyFixtureIds.keys.legacy);
  assert.equal(declared.reason, 'bounded_captured_graph');
  assert.equal(declared.partial, true);
  assert.deepEqual(declared.components, []);
  assert.deepEqual(declared.declared, doc.declared);
  const cross = dependencyImpact(project, dependencyFixtureIds.keys.contract);
  assert.equal(cross.cross_repository.complete, false);
  assert.equal(cross.cross_repository.reason, 'selection_budget');
  assert.ok(cross.cross_repository.selection_reasons.includes('unreadable_manifest'));
});


test('column budget covers each shown component before taking extra dependencies', () => {
  const rows = [{component_id: 'a'}, {component_id: 'b'}, {component_id: 'c'}];
  const columns = ['a1', 'a2', 'a3', 'b1', 'c1'];
  const edges = columns.map(dependency_key => ({component_id: dependency_key[0], dependency_key}));
  assert.deepEqual(limitColumns(rows, columns, edges, 3), ['a1', 'b1', 'c1']);
});

test('graph budget retains focused identity without dangling links', () => {
  const nodes = Array.from({length: 520}, (_, i) => ({id: String(i)}));
  const links = nodes.slice(1).map(node => ({from: '0', to: node.id}));
  const result = boundGraph(nodes, links, {limit: 60, required: ['519']});
  assert.equal(result.nodes.length, 60);
  assert.ok(result.nodes.some(node => node.id === '519'));
  const ids = new Set(result.nodes.map(node => node.id));
  assert.ok(result.links.every(link => ids.has(link.from) && ids.has(link.to)));
  assert.deepEqual(boundGraph([], []), {nodes: [], links: []});
});
