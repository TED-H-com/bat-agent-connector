import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {mountSetupObservation} from './setup-observation-fixture.ts';

const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-evidence.json', import.meta.url), 'utf8'));
const previews = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-preview.json', import.meta.url), 'utf8'));
const start = 1800000600000;
const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>(done => {resolve = done;});
  return {promise, resolve};
};
const barrier = (page: Page) => page.evaluate(() => (window as any).__setupReadBarrier());
const tick = async (page: Page, milliseconds = 180000) => {
  await page.clock.fastForward(milliseconds);
  await page.evaluate(() => document.dispatchEvent(new Event('health-display-tick')));
};
const draft = () => {
  const {name:host,url,fingerprint,profile_id} = golden.observations.connected.setup.hosts[0];
  return {host,url,fingerprint,profile_id};
};
async function preview(route: any) {
  expect(new URL(route.request().url()).pathname).toBe('/api/v1/managed/setup/preview');
  const request = route.request().postDataJSON();
  await route.fulfill({status:200,json:{preview:{...structuredClone(previews.existing.preview),
    target:request.target,config_revision:request.preconditions.config_revision}}});
}

for (const locale of ['en','zh-TW']) for (const width of [390,1440]) test.describe(`${locale} Settings read ordering at ${width}px`, () => {
  test.beforeEach(async ({page}) => {
    await page.setViewportSize({width,height:900});
    await page.clock.install({time:new Date(start)}); await page.clock.pauseAt(start);
  });

  test('a full refresh survives a deadline tick and spends the skipped renewal', async ({page}) => {
    const entered = deferred(), release = deferred();
    const body = structuredClone(golden.observations.connected.setup);
    const full = structuredClone(body); full.repositories = [];
    full.verification.commands = {'full-read-applied':['uv','run','pytest','-q']};
    const f = await mountSetupObservation(page,body,golden.operation,locale,async (route,n) => {
      if(n===3){entered.resolve(); await release.promise;}
      await route.fulfill({status:200,json:n===3?full:body});
    },{manualTicks:true});
    const count = () => f.reads.filter(path=>path.endsWith('/managed/setup')).length;
    try {
      await page.evaluate(() => {(window as any).__fullRead=(window as any).__setupObservation.reload();});
      await entered.promise;
      expect(count()).toBe(3);
      await tick(page);
      release.resolve();
      await page.evaluate(() => (window as any).__fullRead); await barrier(page);
      await expect(f.panel).toContainText('full-read-applied');
      await expect(f.panel.locator('[data-setup-details]')).toContainText(locale==='en'?'0 repositories':'0 個儲存庫');
      expect(count()).toBe(3);
      await tick(page,3600000); await barrier(page); expect(count()).toBe(3);
      expect(f.posts).toEqual([]);
      expect(f.reads.every(path=>path.endsWith('/managed/setup')||path.includes('/operations/'))).toBe(true);
    } finally {release.resolve();}
  });

  test('a full refresh supersedes an already pending observation reply', async ({page}) => {
    const entered = deferred(), release = deferred();
    const body = structuredClone(golden.observations.connected.setup);
    const full = structuredClone(golden.observations.renewed.setup);
    full.verification.commands = {'newer-full-read':['uv','run','pytest','-q']};
    const f = await mountSetupObservation(page,body,golden.operation,locale,async (route,n) => {
      if(n===3){entered.resolve(); await release.promise;}
      await route.fulfill({status:200,json:n===4?full:body});
    },{manualTicks:true});
    try {
      await tick(page); await entered.promise;
      await page.evaluate(()=>(window as any).__setupObservation.reload());
      await expect(f.panel).toContainText('newer-full-read');
      release.resolve(); await barrier(page);
      await expect(f.fact('connection')).toHaveText(locale==='en'?'Ready':'已就緒');
      expect(f.reads.filter(path=>path.endsWith('/managed/setup'))).toHaveLength(4);
      expect(f.posts).toEqual([]);
    } finally {release.resolve();}
  });

  for (const change of ['revision','host names','binding']) test(`a changed ${change} observation uses normal refresh and invalidates review`, async ({page}) => {
    const body = structuredClone(golden.observations.connected.setup);
    const renewed = structuredClone(golden.observations.renewed.setup);
    const changed = structuredClone(change==='host names'?golden.observations.connected.multiple_hosts:
      change==='binding'?golden.observations.changed_binding.setup:renewed);
    if(change==='revision')changed.revision='c'.repeat(64);
    changed.verification.commands = {'configuration-read-applied':['uv','run','pytest','-q']};
    const f = await mountSetupObservation(page,body,golden.operation,locale,async (route,n) => {
      await route.fulfill({status:200,json:n===1?body:n===2?renewed:changed});
    },{draft:draft(),onWrite:preview,manualTicks:true});
    await f.panel.locator('[data-setup-host] button[type=submit]').click(); await barrier(page);
    await expect(f.panel.locator('[data-setup-review]')).toBeVisible();
    const field = f.panel.locator('[data-setup-field=verification_project]');
    await field.fill('retained-verification-draft'); await field.focus();
    await tick(page); await barrier(page);
    await expect(f.panel.locator('[data-setup-review]')).toBeVisible();
    await expect(field).toBeFocused(); await expect(field).toHaveValue('retained-verification-draft');
    expect(f.reads.filter(path=>path.endsWith('/managed/setup'))).toHaveLength(2);
    const writes = [...f.posts];
    await tick(page); await barrier(page);
    await expect(f.panel).toContainText('configuration-read-applied');
    await expect(f.panel.locator('[data-setup-review]')).toBeHidden();
    await expect(f.panel.locator('[data-setup-progress]')).toHaveText(locale==='en'?'Settings changed; review again':'設定已變更，請重新預覽');
    await expect(field).toHaveValue('retained-verification-draft');
    expect(f.reads.filter(path=>path.endsWith('/managed/setup'))).toHaveLength(3);
    expect(f.posts).toEqual(writes);
    expect(f.reads.every(path=>path.endsWith('/managed/setup'))).toBe(true);
  });

  for (const status of ['unsupported','reviewing','review changed','applying']) test(`a deadline tick preserves ${status} status`, async ({page}) => {
    const entered = deferred(), release = deferred();
    const body = structuredClone(golden.observations.connected.setup);
    if(status==='unsupported')body.preview_contract_version=0;
    const saved:any = draft();
    if(status==='applying') {
      const {action,target,params,preconditions,idempotency_key:key}=golden.operation;
      saved.intent={key,request:{action,target,params,preconditions}};
    }
    const f = await mountSetupObservation(page,body,golden.operation,locale,undefined,{draft:saved,manualTicks:true,onWrite:async route => {
      if(status==='reviewing'||status==='applying'){entered.resolve(); await release.promise;}
      if(status==='applying')await route.fulfill({status:200,json:{operation:golden.operation}});
      else await preview(route);
    }});
    const expected = locale==='en'?{unsupported:'This Connector does not support connection review. Update it before continuing.',
      reviewing:'Reviewing connection','review changed':'Settings changed; review again',applying:'Applying connection'}:
      {unsupported:'此 Connector 尚不支援連線預覽，請先更新再繼續。',reviewing:'正在預覽連線','review changed':'設定已變更，請重新預覽',applying:'正在套用連線'};
    try {
      if(status==='applying')await f.panel.getByRole('button',{name:locale==='en'?'Check request':'查回請求',exact:true}).click();
      else if(status!=='unsupported')await f.panel.locator('[data-setup-host] button[type=submit]').click();
      if(status==='reviewing'||status==='applying')await entered.promise;
      if(status==='review changed') {
        await barrier(page); await expect(f.panel.locator('[data-setup-review]')).toBeVisible();
        await f.panel.locator('[data-setup-field=host]').fill('retained-draft');
      }
      const progress=f.panel.locator('[data-setup-progress]');
      await expect(progress).toHaveText(expected[status]);
      const writes=[...f.posts];
      await tick(page);
      // Observe the GET barrier independently of the deliberately held POST.
      await expect(f.fact('connection')).toContainText('OBSERVATION_NOT_RENEWED');
      await expect(progress).toHaveText(expected[status]);
      expect(f.reads.filter(path=>path.endsWith('/managed/setup'))).toHaveLength(2);
      expect(f.posts).toEqual(writes);
      expect(f.reads.every(path=>path.endsWith('/managed/setup'))).toBe(true);
    } finally {release.resolve(); await barrier(page);}
  });
});
