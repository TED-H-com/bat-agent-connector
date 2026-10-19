import assert from 'node:assert/strict';
import {test} from 'node:test';
import {fleetStatus, fleetStatusKey} from '../src/fleet-status.js';
import {STRINGS} from '../src/i18n.js';

test('Fleet wake, retry and pending messages are localized with raw codes only in details', () => {
  for (const locale of ['en', 'zh-TW']) {
    const strings = STRINGS[locale];
    const t = (key: string) => strings[key];
    const h = (tag: string, attrs: unknown, ...children: unknown[]) => ({tag, attrs, children});
    for (const code of ['TAILSCALE_WAKING', 'ROUTE_UNAVAILABLE', 'PROBE_PENDING']) {
      const key = fleetStatusKey(code);
      assert.ok(key);
      assert.ok(t(key));
      assert.ok(t('fleet_status_details'));
      assert.ok(!t(key).includes(code));
      assert.equal(/[\u3400-\u9fff]/u.test(t(key)), locale === 'zh-TW');
      const view = fleetStatus({code}, {h, t});
      assert.deepEqual(view.children, [
        h('span', {role: 'status'}, t(key)),
        h('details', {}, h('summary', {}, t('fleet_status_details')), h('code', {}, code)),
      ]);
    }
    assert.equal(fleetStatus({code: 'OTHER_CODE'}, {h, t}), null);
    assert.equal(fleetStatus(null, {h, t}), null);
  }
});
