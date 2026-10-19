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
        h('details', {}, h('summary', {}, t('fleet_status_details')), h('code', {}, code), null),
      ]);
    }
    for (const row of [{code: 'OWNER_UNPROVEN', blocking: 'tunnel'}, {blocking: 'OWNER_UNPROVEN'}, {blocking: 'tls'}]) {
      const reason = row.code || row.blocking;
      const view = fleetStatus(row, {h, t});
      const details = view.children[1] as ReturnType<typeof h>;
      assert.deepEqual(details.children[0], h('summary', {}, `${t('fleet_status_details')}: ${reason}`));
      assert.deepEqual(details.children[1], h('code', {}, reason));
      if (row.code) assert.deepEqual(details.children[2], h('code', {}, row.blocking));
    }
    assert.ok(fleetStatus({blocking: 'PROBE_PENDING'}, {h, t}));
    assert.equal(fleetStatus(null, {h, t}), null);
  }
});
