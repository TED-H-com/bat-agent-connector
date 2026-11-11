import assert from 'node:assert/strict';
import {test} from 'node:test';
import {allowedByBridge, discoverCentralRoutes} from './bridge-route-inventory.mjs';

test('cleanup discovery has a native read route before its UI is introduced', () => {
  const path = '/pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/external-links/cleanup';
  assert.ok(allowedByBridge({method: 'GET', path: `${path}?after=hxl_cccccccccccccccccccccccccccccccc&limit=50`}));
  assert.equal(allowedByBridge({method: 'POST', path}), false);
  assert.equal(allowedByBridge({method: 'GET', path: `${path}/raw`}), false);
});

test('shared frontend central routes are on the desktop bridge allow-list', () => {
  const found = discoverCentralRoutes();
  assert.deepEqual(found.unresolved, [], 'an api() or central forwarder did not resolve to a path');
  const discovered = found.calls.map(call => `${call.method} ${call.path} ${call.file}`);
  for (const needle of [
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/attempts?limit=100 pm-work-start.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/attempts?after=1&limit=20 pm-history-attempt-picker.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work-attempts/att_cccccccccccccccccccccccccccccccc pm-history-attempt-picker.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/external-links/hxl_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pm-history-replacement.js',
    'POST /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/start-preview pm-work-start.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work-attempts/att_cccccccccccccccccccccccccccccccc/result pm-work-result.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/inputs?limit=50&attempt_id=att_cccccccccccccccccccccccccccccccc pm-work-follow-up.js',
    'POST /operations/op_dddddddddddddddddddddddddddddddd/cancel pm-work-follow-up.js',
    'POST /operations/op_dddddddddddddddddddddddddddddddd/resume pm-work-follow-up.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work-profiles?limit=50 pm-work-profile-picker.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work-profiles/wpf_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pm-work-profile-picker.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work-profiles/wpf_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/readiness pm-work-profile-picker.js',
    'GET /pm/workspaces pm-work-item-history.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pm-work-item-history.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/kanban/wi_bbbbbbbbbbbbbbbbbbbb pm-work-item-history.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/external-links?after=1&limit=20 pm-work-history.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/external-links/hxl_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pm-work-history.js',
    'GET /pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/history-excerpts/hxe_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pm-work-history.js',
  ]) {
    assert.ok(discovered.includes(needle), `discovery missed ${needle}`);
  }
  const refused = found.calls
    .filter(call => !allowedByBridge(call))
    .map(call => `${call.method} ${call.path} (${call.file}:${call.line})`);
  assert.deepEqual(refused, []);
});

test('fixed impact preview exposes only the exact native read route before UI', () => {
  const path = '/pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/plan-impact';
  assert.ok(allowedByBridge({method: 'GET', path}));
  assert.equal(allowedByBridge({method: 'POST', path}), false);
  assert.equal(allowedByBridge({method: 'GET', path: `${path}/raw`}), false);
});
