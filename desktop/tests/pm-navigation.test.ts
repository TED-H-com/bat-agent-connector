import test from 'node:test';
import assert from 'node:assert/strict';
import {availableProjectViews} from '../src/pm-workbench.js';
const views = (features: Record<string, unknown>) => availableProjectViews({features: {pm_workspaces: {version: 1, ...features}}});
test('thread navigation keeps every independently available secondary source reachable', () => {
  for (const capability of [
    {quality_reviews: {version: 1}}, {spec_materialization: {version: 1}}, {package_handoff: {available: true}},
    {engineering_delivery: {contract: 'pm-engineering-delivery/v1'}}, {issue_mapping: true},
  ]) assert.deepEqual(views(capability), ['discussions', 'requirements']);
  assert.deepEqual(views({maintenance_policy: {version: 1}}), ['discussions', 'dependencies']);
  assert.deepEqual(views({maintenance_review: {version: 1}}), ['discussions', 'issues']);
  assert.deepEqual(views({ahp_observation: {contract: 'ahp_observer_v1'}}), ['discussions', 'agents']);
  assert.deepEqual(views({work_batches: {version: 1}}), ['discussions', 'board']);
});
test('unsupported secondary contracts do not advertise empty views', () => {
  assert.deepEqual(views({quality_reviews: {version: 2}, lifecycle: {version: 2}, maintenance_policy: {version: 2},
    maintenance_review: {version: 2}, maintenance_repairs: {version: 2}, work_batches: {version: 2},
    ahp_observation: {contract: 'future'}}), ['discussions']);
});
