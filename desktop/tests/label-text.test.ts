import {test} from 'node:test';
import assert from 'node:assert/strict';
import {firstLabel, joinLabel, labelSegment} from '../src/label-text.js';

test('label segments drop missing values and keep real text', () => {
  for (const value of [undefined, null, Number.NaN, Infinity, {}, [], 'undefined', ' null ', 'NaN', '[object Object]', '  '])
    assert.equal(labelSegment(value), '');
  assert.equal(labelSegment('example/library'), 'example/library');
  assert.equal(labelSegment(0), '0');
  assert.equal(labelSegment(false), 'false');
  assert.equal(labelSegment('nullable'), 'nullable');
  assert.equal(labelSegment('pmi_undefined'), 'pmi_undefined');
});

test('composed labels skip gaps and keep the first real repository name', () => {
  assert.equal(firstLabel(undefined, null, 'example/library'), 'example/library');
  assert.equal(firstLabel('owner/name', 'example/library'), 'owner/name');
  assert.equal(firstLabel(undefined, undefined), '');
  assert.equal(joinLabel([firstLabel(undefined, 'example/library'), '#41', 'Tracking']), 'example/library · #41 · Tracking');
  assert.equal(joinLabel([firstLabel(undefined, undefined), '#41', 'Tracking']), '#41 · Tracking');
  assert.equal(joinLabel([undefined, null, 'NaN', '[object Object]']), '');
});
