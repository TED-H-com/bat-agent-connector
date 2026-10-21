import {test, expect} from '@playwright/test';
import {readFile, cp, mkdir, symlink, appendFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {STRINGS} from '../src/i18n.js';
import {diagnosticsFixture} from './diagnostics-fixture.ts';

const desktop = fileURLToPath(new URL('..', import.meta.url));
const placeholder = '__BATC_FRONTEND_DIGEST_PLACEHOLDER______________________________';
const bootstrap = JSON.parse(await readFile(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 1440]) {
  test(`loaded browser UI and producer Connector have separate identities ${locale} ${width}`, async ({browser}, info) => {
    const context = await browser.newContext({locale, viewport: {width, height: 1000}}), page = await context.newPage();
    const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
    const t = (key: string, vars: Record<string,string> = {}) => copy[key].replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? '');
    const state = await diagnosticsFixture(page);
    state.producer.bootstrap = structuredClone(bootstrap);
    let served = 'a'.repeat(64);
    state.producer.bootstrap.capabilities.frontend = {digest: served};
    await page.route('**/dashboard/app.js', async route => {
      const source = await readFile(new URL('../../src/bat_agent_connector/dashboard/app.js', import.meta.url), 'utf8');
      await route.fulfill({body: source.replace(placeholder, served), contentType: 'text/javascript'});
    });
    try {
      await page.goto('/dashboard/#/settings?section=about');
      const line = page.locator('[data-build-line]');
      await expect(line).toBeVisible();
      const digest = served;
      const frontend = t('diag_frontend_build', {digest: digest.slice(0,12)});
      const connector = t('diag_connector_build', {version: bootstrap.capabilities.build.version,
        commit: bootstrap.capabilities.build.commit || copy.diag_unknown, source: bootstrap.capabilities.build.source});
      await page.screenshot({path:info.outputPath(`about-${locale}-${width}.png`),fullPage:true});
      await expect(line).toHaveText(`${frontend} · ${connector}`);
      await expect(page.locator('#settings-panel-about')).not.toContainText(/Fleet|native|原生|桌面版|Desktop/);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.locator('[data-export-diagnostics]').click();
      const json = page.locator('[data-diagnostics-json]');
      await expect(json).toContainText('batc.diagnostics.v1');
      const bundle = JSON.parse((await json.textContent())!);
      expect(bundle.client).toMatchObject({frontend_digest: digest, served_frontend_digest: served, app_version: null, app_commit: null,
        connector_version: bootstrap.capabilities.build.version, connector_commit: bootstrap.capabilities.build.commit,
        connector_source: bootstrap.capabilities.build.source, platform: 'browser'});
      for(const surface of ['#settings-panel-about','[data-diagnostics-summary]']) {
        await expect(page.locator(`${surface} [data-version-row=loaded_ui]`)).toContainText(digest);
        await expect(page.locator(`${surface} [data-version-row=served_ui]`)).toContainText(served);
      }
      expect(bundle.versions.rows.find(row=>row.id==='loaded_ui')).toMatchObject({digest,source:'loaded_ui',period:'loaded'});
      expect(bundle.versions.rows.find(row=>row.id==='served_ui')).toMatchObject({digest:served,source:'capabilities',period:'read'});
      const loadedAt=bundle.versions.rows.find(row=>row.id==='loaded_ui').observed_at;
      expect(Number.isFinite(Date.parse(loadedAt))).toBe(true);
      await expect(page.locator('[data-diagnostics-summary]')).toContainText(`${frontend} · ${connector}`);
      expect(Object.keys(bundle.client).some(key => /fleet|native/i.test(key))).toBe(false);
      const download = page.waitForEvent('download');
      await page.getByRole('dialog').getByRole('button', {name: copy.diag_save, exact: true}).click();
      expect(JSON.parse(await readFile((await (await download).path())!, 'utf8'))).toEqual(bundle);
      await page.screenshot({path: info.outputPath(`frontend-${locale}-${width}.png`)});
      await page.getByRole('dialog').getByRole('button', {name: copy.diag_close, exact: true}).click();
      served = 'b'.repeat(64);state.producer.bootstrap.capabilities.frontend.digest = served;
      await page.evaluate(() => {location.hash = '#/settings?section=connection';});
      await expect(page.locator('#settings-panel-connection')).toBeVisible();
      await page.evaluate(() => {location.hash = '#/settings?section=about';});
      await expect(page.locator('[data-build-line]')).toContainText(copy.diag_frontend_stale);
      await expect(page.locator('[data-build-line]')).toContainText(digest.slice(0,12));
      await page.locator('[data-export-diagnostics]').click();
      await expect(page.locator('[data-diagnostics-json]')).toContainText(served);
      const stale = JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
      expect(stale.client.frontend_digest).toBe(digest);expect(stale.client.served_frontend_digest).toBe(served);
      expect(stale.versions.rows.find(row=>row.id==='loaded_ui')).toMatchObject({digest,observed_at:loadedAt});
      expect(stale.versions.rows.find(row=>row.id==='served_ui').digest).toBe(served);
      for(const surface of ['#settings-panel-about','[data-diagnostics-summary]']) {
        await expect(page.locator(`${surface} [data-version-row=loaded_ui]`)).toContainText(digest);
        await expect(page.locator(`${surface} [data-version-row=served_ui]`)).toContainText(served);
      }
      await expect(page.locator('[data-diagnostics-summary]')).toContainText(copy.diag_frontend_stale);
      await page.screenshot({path: info.outputPath(`stale-${locale}-${width}.png`)});
      await page.reload();
      await expect(page.locator('[data-build-line]')).toContainText(served.slice(0,12));
      await expect(page.locator('[data-build-line]')).not.toContainText(copy.diag_frontend_stale);

    } finally {await context.close();}
  });
}

test('browser bundle keeps one fixed placeholder across independent input bytes and build commits', async ({}, info) => {
  const project = info.outputPath('project');
  await mkdir(project, {recursive: true});
  for (const input of ['src', 'scripts', 'index.html', 'vite.config.ts', 'tsconfig.json', 'package.json', 'package-lock.json'])
    await cp(join(desktop, input), join(project, input), {recursive: true});
  await symlink(join(desktop, 'node_modules'), join(project, 'node_modules'), 'dir');
  const run = promisify(execFile);
  const build = async (name: string, commit: string) => {
    const output = info.outputPath(name);
    await run(process.execPath, [join(desktop, 'node_modules/vite/bin/vite.js'), 'build', '--mode', 'browser', '--outDir', output],
      {cwd: project, env: {...process.env, BATC_BUILD_COMMIT: commit}});
    const js = await readFile(join(output, 'app.js'), 'utf8');
    expect(js).not.toContain(commit);
    return {js, count: js.split(placeholder).length - 1};
  };
  const first = await build('first', '1234567890abcdef'.repeat(2) + '12345678');
  const second = await build('second', 'fedcba0987654321'.repeat(2) + 'fedcba09');
  expect(first.count).toBe(1);
  expect(second.count).toBe(1);
  expect(second.js).toBe(first.js);
  await appendFile(join(project, 'src/main.ts'), ' ');
  const changed = await build('changed', '1234567890abcdef'.repeat(2) + '12345678');
  expect(changed.count).toBe(1);
  expect(changed.js).toBe(first.js);
});

for (const locale of ['en-US','zh-TW']) for (const native of [false,true]) {
 test(`unsubstituted ${native?'native':'browser'} identity is unknown ${locale}`,async({browser})=>{
  const context=await browser.newContext({locale}),page=await context.newPage();
  const copy=STRINGS[locale==='zh-TW'?'zh-TW':'en'];
  try{
   await diagnosticsFixture(page,native);await page.goto('/dashboard/#/settings?section=about');
   if(native)await expect(page.locator('[data-build-line]')).not.toContainText(copy.diag_frontend_build.split('{')[0]);
   else await expect(page.locator('[data-build-line]')).toContainText(copy.diag_frontend_build.replace('{digest}',copy.diag_unknown));
   await page.locator('[data-export-diagnostics]').click();await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
   const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
   expect(bundle.client.frontend_digest).toBeNull();expect(bundle.client.served_frontend_digest).toBeNull();
  }finally{await context.close();}
 });
}
