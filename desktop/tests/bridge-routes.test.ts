import assert from 'node:assert/strict';
import {test} from 'node:test';
import {allowedByBridge, discoverCentralRoutes} from './bridge-route-inventory.mjs';

test('shared frontend central routes are on the desktop bridge allow-list', () => {
  const found = discoverCentralRoutes();
  assert.deepEqual(found.unresolved, [], 'an api() or central forwarder did not resolve to a path');
  const discovered = found.calls.map(call => `${call.method} ${call.path} ${call.file}`);
  for (const needle of [
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/attempts?limit=100 pm-work-start.js',
    'POST /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/start-preview pm-work-start.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work-attempts/att_cccccccccccccccccccccccccccccccc/result pm-work-result.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/inputs?limit=50&attempt_id=att_cccccccccccccccccccccccccccccccc pm-work-follow-up.js',
    'POST /operations/op_dddddddddddddddddddddddddddddddd/cancel pm-work-follow-up.js',
    'POST /operations/op_dddddddddddddddddddddddddddddddd/resume pm-work-follow-up.js',
  ]) {
    assert.ok(discovered.includes(needle), `discovery missed ${needle}`);
  }
  const refused = found.calls
    .filter(call => !allowedByBridge(call))
    .map(call => `${call.method} ${call.path} (${call.file}:${call.line})`);
  assert.deepEqual(refused, []);
});
