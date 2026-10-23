import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {fleetDiagnosis, fleetStatus} from '../src/fleet-status.js';
import {STRINGS} from '../src/i18n.js';

const requirements = JSON.parse(readFileSync(new URL('./fixtures/fleet-requirement.golden.json', import.meta.url), 'utf8'));
const pending = JSON.parse(readFileSync(new URL('./fixtures/fleet-pending.golden.json', import.meta.url), 'utf8'));
const stops = JSON.parse(readFileSync(new URL('./fixtures/fleet-stop.golden.json', import.meta.url), 'utf8'));
const requirementWords = {
  en: {
    missing: 'Only Tailscale direct is configured. The peer evidence is unknown.',
    unknown: 'Only Tailscale direct is configured. The peer evidence is unknown.',
    idle: 'Only Tailscale direct is configured. A direct peer connection was not confirmed.',
    tcp: 'Only Tailscale direct is configured. The direct TCP check did not succeed.',
    exhausted: 'None of the configured routes met the connection requirements.',
  },
  'zh-TW': {
    missing: '僅設定 Tailscale 直連。目前的 peer 證據未知。',
    unknown: '僅設定 Tailscale 直連。目前的 peer 證據未知。',
    idle: '僅設定 Tailscale 直連。尚未確認 peer 的直連連線。',
    tcp: '僅設定 Tailscale 直連。直連 TCP 檢查未成功。',
    exhausted: '所有已設定的路徑都未滿足連線條件。',
  },
};
const h = (tag: string, attrs: any, ...children: any[]) => ({tag, attrs, children});
function render(snapshot: any, row: any, locale: string, now: number) {
  const t = (key: string, args: any = {}) => {
    assert.ok(STRINGS[locale][key], key);
    return STRINGS[locale][key].replace(/\{(\w+)\}/g, (_: string, name: string) => args[name]);
  };
  return fleetStatus(row, {h, t, snapshot, readable: true, now,
    entry: snapshot.configuration.connections.find((entry: any) => entry.name === row.name), detailsState: new Map()});
}
for (const locale of ['en', 'zh-TW']) {
  test(`between-attempts rows show Checking and the last failed selection: ${locale}`, () => {
    for (const [name, variants] of Object.entries(pending) as [string, any][]) {
      const snapshot = variants.pending;
      const row = snapshot.readiness.hosts[0];
      assert.equal(row.code, 'PROBE_PENDING');
      const view = render(snapshot, row, locale, Date.parse(snapshot.readiness.observed_at));
      assert.equal(view.attrs['data-fleet-state'], 'checking');
      assert.equal(view.children[0].children[0], locale === 'en' ? 'unknown · Checking' : '未知 · 檢查中');
      assert.ok(JSON.stringify(view).includes(requirementWords[locale][name]), name);
      assert.equal(fleetDiagnosis(snapshot.readiness.hosts[1], {snapshot, readable: true}).state, 'ready');
    }
  });
  test(`expiry withdraws between-attempts explanations: ${locale}`, () => {
    for (const [name, variants] of Object.entries(pending) as [string, any][]) {
      const snapshot = variants.aged;
      const row = snapshot.readiness.hosts[0];
      assert.equal(row.code, 'PROBE_STALE');
      const view = render(snapshot, row, locale, Date.parse(snapshot.readiness.observed_at) + 60_001);
      assert.equal(view.attrs['data-fleet-state'], 'stale');
      assert.ok(!JSON.stringify(view).includes('data-fleet-route-requirement'));
      assert.ok(!JSON.stringify(view).includes(requirementWords[locale][name]));
    }
  });
  test(`real route requirement goldens explain only the affected host: ${locale}`, () => {
    for (const name of Object.keys(requirementWords[locale])) {
      const snapshot = requirements[name];
      const now = Date.parse(snapshot.readiness.observed_at);
      const row = snapshot.readiness.hosts[0];
      const view = render(snapshot, row, locale, now);
      assert.ok(JSON.stringify(view).includes(requirementWords[locale][name]), name);
      assert.ok(!JSON.stringify(view).includes('data-fleet-route"'));
      assert.ok(!JSON.stringify(view).includes('latency'));
      assert.equal(fleetDiagnosis(snapshot.readiness.hosts[1], {snapshot, readable: true}).state, 'ready');
      assert.ok(!JSON.stringify(render(snapshot, snapshot.readiness.hosts[1], locale, now)).includes('data-fleet-route-requirement'));
    }
  });
  test(`aged route failure withdraws its explanation with the code: ${locale}`, () => {
    const snapshot = requirements.aged;
    const row = snapshot.readiness.hosts[0];
    assert.equal(row.code, 'PROBE_STALE');
    const view = render(snapshot, row, locale, Date.parse(snapshot.readiness.observed_at) + 60_001);
    assert.equal(view.attrs['data-fleet-state'], 'stale');
    assert.ok(!JSON.stringify(view).includes('data-fleet-route-requirement'));
    for (const text of Object.values(requirementWords[locale])) assert.ok(!JSON.stringify(view).includes(text));
  });
  test(`real unresolved stop goldens retain priority across age, deselection and re-read: ${locale}`, () => {
    for (const snapshot of Object.values(stops) as any[]) {
      for (const reread of [snapshot, JSON.parse(JSON.stringify(snapshot))]) {
        const row = reread.readiness.hosts.find((row: any) => row.name === 'node-1');
        assert.equal(row.code, 'STOP_UNCONFIRMED');
        const view = render(reread, row, locale, Date.parse(reread.readiness.observed_at) + 70_000);
        assert.equal(view.attrs['data-fleet-state'], 'ownership');
        assert.notEqual(view.attrs['data-freshness'], 'current');
        assert.ok(JSON.stringify(view).includes(locale === 'en'
          ? 'The background connection outcome is unconfirmed' : '背景連線的操作結果尚未確認'));
      }
    }
  });
}
