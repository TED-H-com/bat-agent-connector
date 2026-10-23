import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {mountProfilePicker} from './work-profile-picker-fixture.ts';
import {mountSetupObservation} from './setup-observation-fixture.ts';

const fleet = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
const profiles = JSON.parse(readFileSync(new URL('../../tests/fixtures/work-profile-readiness.json', import.meta.url), 'utf8'));
const setup = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-evidence.json', import.meta.url), 'utf8'));

for(const locale of ['en','zh-TW'])for(const width of [390,1440])test(`${locale} Settings producer reasons and shared facts at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});
  await page.clock.install({time:new Date(1800000790000)});await page.clock.pauseAt(1800000790000);
  const reasons=locale==='en'?{
    authentication:'Sign-in or protocol negotiation failed.',protocol:'Sign-in or protocol negotiation failed.',
    tls_trust:'The certificate did not match the trusted fingerprint.',missing_credential:'The configured credential is unavailable.',
    service_refusal:'BAT refused the read.',service_timeout:'BAT did not answer the read in time.',
    unknown:'No classified observation is available.',transport:'The client could not establish or keep the transport.',
    changed_binding:'The configured source changed; earlier observations do not apply.',
  }:{
    authentication:'登入或協定協商失敗。',protocol:'登入或協定協商失敗。',tls_trust:'憑證與信任的指紋不符。',
    missing_credential:'設定的憑證目前無法使用。',service_refusal:'BAT 拒絕讀取。',service_timeout:'BAT 未在期限內回應讀取。',
    unknown:'目前沒有已分類的觀察結果。',transport:'用戶端無法建立或維持傳輸連線。',
    changed_binding:'設定的來源已變更，先前觀察不適用。',
  };
  for(const [name,body] of Object.entries(setup.observations) as [string,any][]){
    const f=await mountSetupObservation(page,body.setup,setup.operation,locale);
    if(reasons[name])await expect(f.panel).toContainText(reasons[name]);
    await expect(f.panel.locator('[data-setup-facet]')).toHaveCount(4);
    const expected=await page.evaluate(async({state,now})=>{
      const {profileStateFacts}=await import('/src/pm-work-profile-state.js');
      const {healthLabel}=await import('/src/health-display.js');const {t}=await import('/src/i18n.js');
      const fact=profileStateFacts(state,{now}).fact;return fact?healthLabel(fact,t):null;
    },{state:body.work,now:1800000790000});
    if(expected)await expect(f.fact('connection')).toHaveText(expected);
    if(name==='active_writer'){
      await expect(f.fact('transport')).toHaveText(locale==='en'?'Ready · 3 min ago · Checking':'已就緒 · 3 分鐘前 · 檢查中');
      await expect(f.panel.locator('[data-setup-progress]')).not.toContainText(locale==='en'?'Setup is complete.':'設定完成，');
    }
    expect(f.posts).toEqual([]);
    expect(f.reads.every(path=>path.endsWith('/managed/setup')||path.includes('/operations/'))).toBe(true);
    await page.evaluate(()=>(window as any).__setupObservation.dispose());
    await page.unroute('**/api/v1/**');
  }
  for(const version of [null,2]){
    const body=structuredClone(setup.observations.connected.setup);
    if(version===null)delete body.hosts[0].connection_observation;
    else body.hosts[0].connection_observation.version=version;
    const f=await mountSetupObservation(page,body,setup.operation,locale);
    await expect(f.fact('connection')).toHaveText(locale==='en'?'Not verified yet · Checking':'尚未驗證 · 檢查中');
    await expect(f.panel.locator('[data-setup-progress]')).not.toContainText(locale==='en'?'Setup is complete.':'設定完成，');
    const count=f.reads.length;
    await page.clock.fastForward(3600000);
    expect(f.reads.length).toBe(count);expect(f.posts).toEqual([]);
    await page.evaluate(()=>(window as any).__setupObservation.dispose());
    await page.unroute('**/api/v1/**');
  }
});

for (const locale of ['en-US', 'zh-TW']) test.describe(`shared readiness ${locale}`, () => {
  test.use({locale});
  test('Fleet uses the header formatter at the same clock boundary', async ({page}) => {
    await page.goto('/ui-components.html');
    await page.evaluate(async ({fleet, locale}) => {
      const {fleetStatus} = await import('/src/fleet-status.js');
      const {h} = await import('/src/ui/dom.js');
      const {t, setLocale} = await import('/src/i18n.js');
      setLocale(locale === 'en-US' ? 'en' : locale);
      const now = Date.parse(fleet.readiness.observed_at) + 60_000;
      const node = fleetStatus(fleet.readiness.hosts[0], {h, t, now, snapshot: fleet,
        readable: true, entry: fleet.configuration.connections[0], detailsState: new Map()});
      document.getElementById('fixture')!.append(node);
    }, {fleet, locale});
    await expect(page.locator('.fleet-diagnosis-title')).toHaveText(locale === 'en-US'
      ? 'Connected · 1 min ago · Checking' : '已連線 · 1 分鐘前 · 檢查中');
    await expect(page.locator('[data-fleet-diagnosis]')).toHaveAttribute('data-freshness', 'stale');
    await page.locator('summary').click();
    await expect(page.locator('[data-check-state="passed"]')).toHaveCount(0);
  });

  test('Work unreachable accepts the newer attempt after an unchanged renewal', async ({page}) => {
    const body = profiles.cases.host_unreachable;
    // Keep the real producer golden body; only the declared states' observed_at
    // changes, by 60 seconds for a newer failed attempt. The deadline stays put.
    const renewed = structuredClone(body);
    for (const state of Object.values(renewed.states) as any[]) if (state.valid_until) state.observed_at += 60;
    await page.clock.install({time: new Date(1790000900000)});
    await page.clock.pauseAt(1790000900000);
    let release;
    const held = new Promise<void>(resolve => release = resolve);
    const {facts, reads, posts} = await mountProfilePicker(page, body, profiles.profile, locale, async (route, n) => {
      if (n > 1) await held;
      await route.fulfill({status: 200, json: n === 1 ? body : renewed});
    });
    try {
      for (const name of ['can_observe', 'can_start_own']) {
        await expect(facts.locator(`[data-work-profile-state="${name}"] [data-work-profile-value]`)).toHaveText(locale === 'en-US'
          ? 'Unavailable · Last attempt: 1 min ago; host unreachable, last successful observation is stale'
          : '不可用 · 上次嘗試：1 分鐘前；無法連至主機，上次成功觀察已過期');
        expect(body.states[name].observed_at).toBe(1790000840);
      }
      expect(body.states.can_observe.verified.observed_at).toBe(1790000540);
      await expect.poll(() => reads.filter(path => path.endsWith('/readiness')).length).toBe(2);
      await page.clock.fastForward(60_000);
      await expect(facts.locator('[data-work-profile-state="can_observe"] [data-work-profile-value]')).toHaveText(locale === 'en-US'
        ? 'Unavailable · Last attempt: 2 min ago; host unreachable, last successful observation is stale'
        : '不可用 · 上次嘗試：2 分鐘前；無法連至主機，上次成功觀察已過期');
      release();
      for (const name of ['can_observe', 'can_start_own']) {
        await expect(facts.locator(`[data-work-profile-state="${name}"] [data-work-profile-value]`)).toHaveText(locale === 'en-US'
          ? 'Unavailable · Last attempt: 1 min ago; host unreachable, last successful observation is stale'
          : '不可用 · 上次嘗試：1 分鐘前；無法連至主機，上次成功觀察已過期');
      }
      await expect(facts).not.toContainText(locale === 'en-US' ? 'Could not confirm' : '無法確認');
      await page.clock.fastForward(600_000);
      await expect(facts.locator('[data-work-profile-state="can_observe"] [data-work-profile-value]')).toHaveText(locale === 'en-US'
        ? 'Unavailable · Last attempt: 11 min ago; host unreachable, last successful observation is stale'
        : '不可用 · 上次嘗試：11 分鐘前；無法連至主機，上次成功觀察已過期');
      await page.evaluate(() => {
        for (const hidden of [true, false]) {
          Object.defineProperty(document, 'hidden', {configurable: true, value: hidden});
          document.dispatchEvent(new Event('visibilitychange'));
        }
      });
      expect(reads.filter(path => path.endsWith('/readiness'))).toHaveLength(2);
      expect(posts).toEqual([]);
    } finally {release();}
  });
  for (const failure of ['HTTP 503', 'invalid contract']) test(`Work unreachable renewal reports ${failure}`, async ({page}) => {
    const body = profiles.cases.host_unreachable;
    await page.clock.install({time: new Date(1790000900000)});
    await page.clock.pauseAt(1790000900000);
    const {facts, reads, posts} = await mountProfilePicker(page, body, profiles.profile, locale, async (route, n) => {
      await route.fulfill(n === 1 ? {status: 200, json: body} : failure === 'HTTP 503'
        ? {status: 503, json: {}} : {status: 200, json: {...body, contract: 'invalid'}});
    });
    const expected = locale === 'en-US' ? {
      'HTTP 503': 'Unavailable · 1 min ago · Could not confirm (503)',
      'invalid contract': 'Unavailable · 1 min ago · Could not confirm (READ_FAILED)',
    } : {
      'HTTP 503': '不可用 · 1 分鐘前 · 無法確認（503）',
      'invalid contract': '不可用 · 1 分鐘前 · 無法確認（READ_FAILED）',
    };
    for (const name of ['can_observe', 'can_start_own']) {
      await expect(facts.locator(`[data-work-profile-state="${name}"] [data-work-profile-value]`)).toHaveText(expected[failure]);
    }
    expect(reads.filter(path => path.endsWith('/readiness'))).toHaveLength(2);
    await page.clock.fastForward(600_000);
    expect(reads.filter(path => path.endsWith('/readiness'))).toHaveLength(2);
    expect(posts).toEqual([]);
  });

  const body = profiles.cases.owned_execution;
  const deadline = 1790000720000;
  const copy = locale === 'en-US' ? {
    ready: 'Ready', checking: 'Ready · 3 min ago · Checking',
    failed: 'Ready · 3 min ago · Could not confirm (FIXTURE_READ_FAILED)',
    unchanged: 'Ready · 3 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)',
    values: {ready: 'Ready', missing: 'Setup still missing', unknown: 'Not verified yet', unavailable: 'Unavailable'},
  } : {
    ready: '已就緒', checking: '已就緒 · 3 分鐘前 · 檢查中',
    failed: '已就緒 · 3 分鐘前 · 無法確認（FIXTURE_READ_FAILED）',
    unchanged: '已就緒 · 3 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）',
    values: {ready: '已就緒', missing: '還缺設定', unknown: '尚未驗證', unavailable: '不可用'},
  };
  const value = facts => facts.locator('[data-work-profile-state="can_observe"] [data-work-profile-value]');
  const count = reads => reads.filter(path => path.endsWith('/readiness')).length;
  async function heldPicker(page) {
    await page.clock.install({time: new Date(1790000540000)});
    await page.clock.pauseAt(1790000540000);
    let release;
    const held = new Promise<void>(resolve => release = resolve);
    const response = {body: profiles.cases.renewed_observation || body, status: 200};
    const fixture = await mountProfilePicker(page, body, profiles.profile, locale, async (route, n) => {
      if (n > 1) await held;
      await route.fulfill({status: n === 1 ? 200 : response.status, json: n === 1 ? body : response.body});
    });
    await expect(value(fixture.facts)).toHaveText(copy.ready);
    return {...fixture, release, response};
  }
  test('Work renews once at the deadline and retains focus, scroll and disclosure', async ({page}) => {
    const f = await heldPicker(page);
    await page.evaluate(() => {
      const box = document.querySelector('[data-work-profile-picker]')!;
      const details = document.createElement('details'); details.open = true;
      const summary = document.createElement('summary'); summary.textContent = 'Profile';
      box.before(details); details.append(summary, box);
    });
    const link = f.facts.locator('[data-work-profile-state="last_verified_execution"] a');
    await link.focus();
    const scroll = await page.evaluate(() => scrollY);
    await page.clock.runFor(deadline - 1790000540000 - 1);
    expect(count(f.reads)).toBe(1);
    await page.clock.runFor(1);
    await expect(value(f.facts)).toHaveText(copy.checking);
    await expect.poll(() => count(f.reads)).toBe(2);
    await expect(link).toBeFocused();
    expect(await page.evaluate(() => scrollY)).toBe(scroll);
    f.release();
    await expect(value(f.facts)).toHaveText(copy.ready);
    await expect(link).toBeFocused();
    await expect(page.locator('details')).toHaveAttribute('open', '');
    expect(await page.evaluate(() => scrollY)).toBe(scroll);
    expect(count(f.reads)).toBe(2); expect(f.posts).toEqual([]);
    await expect(f.facts.locator('[data-work-profile-state]')).toHaveCount(6);
    await expect(f.facts).toContainText(locale === 'en-US' ? 'PM analysis' : 'PM 分析');
    await expect(f.facts).toContainText(locale === 'en-US' ? 'A saved setting is not a ready check.' : '已設定不等於就緒。');
    await expect(f.facts.locator('[data-work-profile-state="can_observe"] [data-work-profile-past]')).toContainText(locale === 'en-US' ? 'Last verified' : '上次驗證');
  });
  for (const failure of ['failed', 'unchanged']) test(`Work ${failure} renewal never retries on later clocks`, async ({page}) => {
    const f = await heldPicker(page);
    f.response.body = failure === 'failed' ? {error: {code: 'FIXTURE_READ_FAILED'}} : body;
    f.response.status = failure === 'failed' ? 503 : 200;
    await page.clock.runFor(deadline - 1790000540000);
    await expect(value(f.facts)).toHaveText(copy.checking);
    await expect.poll(() => count(f.reads)).toBe(2);
    f.release();
    await expect(value(f.facts)).toHaveText(copy[failure]);
    for (const elapsed of [60_000, 600_000, 3_600_000]) {
      await page.clock.fastForward(elapsed);
      expect(count(f.reads)).toBe(2);
      await expect(value(f.facts)).not.toContainText(locale === 'en-US' ? 'Checking' : '檢查中');
      if(elapsed===60_000)await expect(value(f.facts)).toHaveText(copy[failure].replace('3 min ago','4 min ago').replace('3 分鐘前','4 分鐘前'));
    }
    expect(f.posts).toEqual([]);
  });
  test('Work permits a new renewal only after a normal read supplies a new deadline', async ({page}) => {
    const f = await heldPicker(page);
    f.response.body = body;
    await page.clock.runFor(deadline - 1790000540000);
    await expect.poll(() => count(f.reads)).toBe(2);
    f.release();
    await expect(value(f.facts)).toHaveText(copy.unchanged);
    await page.evaluate(() => (window as any).__profilePicker.load());
    expect(count(f.reads)).toBe(3);
    await expect(value(f.facts)).toHaveText(copy.unchanged);
    f.response.body = profiles.cases.renewed_observation;
    await page.evaluate(() => (window as any).__profilePicker.load());
    expect(count(f.reads)).toBe(4);
    await expect(value(f.facts)).toHaveText(copy.ready);
    await page.clock.runFor(180_000);
    await expect.poll(() => count(f.reads)).toBe(5);
    await expect(value(f.facts)).toHaveText(copy.unchanged);
    await page.clock.fastForward(3_600_000);
    expect(count(f.reads)).toBe(5);
  });
  test('Work rejects a renewal for another workspace and removes the timer on disposal', async ({page}) => {
    const f = await heldPicker(page);
    f.response.body = {...profiles.cases.renewed_observation, workspace_id: 'ws_' + '2'.repeat(32)};
    await page.clock.runFor(deadline - 1790000540000);
    await expect.poll(() => count(f.reads)).toBe(2);
    f.release();
    await expect(value(f.facts)).toHaveText(locale === 'en-US'
      ? 'Ready · 3 min ago · Could not confirm (READ_FAILED)' : '已就緒 · 3 分鐘前 · 無法確認（READ_FAILED）');
    f.response.body = profiles.cases.renewed_observation;
    await page.evaluate(() => (window as any).__profilePicker.load());
    expect(count(f.reads)).toBe(3);
    await page.evaluate(() => (window as any).__profilePicker.dispose());
    await page.clock.fastForward(3_600_000);
    expect(count(f.reads)).toBe(3); await expect(f.facts).toBeEmpty();
  });
  test('Work waits while hidden and renews once on return', async ({page}) => {
    const f = await heldPicker(page);
    await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'));});
    await page.clock.runFor(600_000);
    expect(count(f.reads)).toBe(1);
    await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'));});
    await expect.poll(() => count(f.reads)).toBe(2);
    f.release();
  });
  test('Work ignores a held renewal after selection changes', async ({page}) => {
    const f = await heldPicker(page);
    await page.clock.runFor(deadline - 1790000540000);
    await expect.poll(() => count(f.reads)).toBe(2);
    const select = page.locator('[data-work-profile-picker] select');
    await select.selectOption('');
    const response = page.waitForResponse(response => response.url().endsWith('/readiness'));
    f.release();
    // Wait for the response to be processed before checking the stale ticket.
    await response;
    await page.clock.runFor(1);
    await expect(select).toHaveValue(''); await expect(f.facts).toBeEmpty();
    expect(count(f.reads)).toBe(2);
  });
  test('Work returning to a selection cannot leave a discarded renewal permanently Checking', async ({page}) => {
    const f = await heldPicker(page);
    f.response.body = body;
    await page.clock.runFor(deadline - 1790000540000);
    await expect.poll(() => count(f.reads)).toBe(2);
    const select = page.locator('[data-work-profile-picker] select');
    await select.selectOption('');
    f.release();
    await select.selectOption(body.profile_id);
    await expect(value(f.facts)).toHaveText(copy.unchanged);
    expect(count(f.reads)).toBe(3);
    await page.clock.fastForward(600_000);
    expect(count(f.reads)).toBe(3);
  });
  for (const [name, data] of Object.entries(profiles.cases) as [string, any][]) {
    test(`Work ${name}: undeclared states never show Checking`, async ({page}) => {
      await page.clock.install({time: new Date(1790000540000)});
      await page.clock.pauseAt(1790000540000);
      const {facts, posts} = await mountProfilePicker(page, data, profiles.profile, locale);
      for (const elapsed of [0, 60_000, 600_000, 3_600_000]) {
        await page.clock.fastForward(elapsed);
        for (const [key, state] of Object.entries(data.states) as [string, any][]) if (!state.valid_until) {
          await expect(facts.locator(`[data-work-profile-state="${key}"] [data-work-profile-value]`)).toHaveText(copy.values[state.value]);
        }
      }
      expect(posts).toEqual([]);
    });
  }
});
