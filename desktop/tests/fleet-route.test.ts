import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {fleetDiagnosis, fleetStatus} from '../src/fleet-status.js';
import {STRINGS} from '../src/i18n.js';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
const at = Date.parse(fixture.readiness.observed_at);
const words = {
  en: ['Route: Tailscale direct', 'Route: LAN', 'Route: Cloudflare', 'Route: Configured SSH',
    'Route: Tailscale direct · 1 min ago · Checking'],
  'zh-TW': ['路徑：Tailscale 直連', '路徑：區域網路', '路徑：Cloudflare', '路徑：已設定的 SSH',
    '路徑：Tailscale 直連 · 1 分鐘前 · 檢查中'],
};
const h = (tag: string, attrs: any, ...children: any[]) => ({tag, attrs, children});
function render(row: any, locale: string, now = at) {
  const t = (key: string, args: any = {}) => {
    assert.ok(STRINGS[locale][key], key);
    return STRINGS[locale][key].replace(/\{(\w+)\}/g, (_: string, name: string) => args[name]);
  };
  return fleetStatus(row, {h, t, now, snapshot: fixture, readable: true,
    entry: fixture.configuration.connections[0], detailsState: new Map()});
}
for (const locale of ['en', 'zh-TW']) {
  test(`route kind uses literal bilingual copy, independent of readiness: ${locale}`, () => {
    const base = fixture.readiness.hosts[0];
    for (const [i, kind] of ['direct', 'lan', 'cf', 'configured'].entries()) {
      const row = {...base, route: {kind, observed_at: base.observed_at, generation: 1}};
      const view = render(row, locale);
      assert.ok(JSON.stringify(view).includes(words[locale][i]));
      assert.deepEqual(fleetDiagnosis(row, {snapshot: fixture, readable: true}),
        fleetDiagnosis({...row, route: undefined}, {snapshot: fixture, readable: true}));
    }
  });
  test(`expired route retains value, age and Checking: ${locale}`, () => {
    const row = {...fixture.readiness.hosts[0], route: {kind: 'direct',
      observed_at: fixture.readiness.observed_at, generation: 1}};
    assert.ok(JSON.stringify(render(row, locale, at + 60_000)).includes(words[locale][4]));
  });
  test(`absent and down rows have no route reassurance: ${locale}`, () => {
    const base = fixture.readiness.hosts[0];
    for (const row of [{...base, route: undefined}, {...base, level: 'down', code: 'TUNNEL_DOWN',
      blocking: 'tunnel', layers: {tunnel: false, tls: false, bat: false, workspace: false},
      route: {kind: 'direct', observed_at: base.observed_at, generation: 1}}]) {
      assert.ok(!JSON.stringify(render(row, locale)).includes('data-fleet-route'));
    }
  });
}
