import {test, expect} from '@playwright/test';
import {readFile, cp, mkdir, symlink, appendFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {STRINGS} from '../src/i18n.js';
import {diagnosticsFixture} from './diagnostics-fixture.ts';

const desktop = fileURLToPath(new URL('..', import.meta.url));
const bootstrap = JSON.parse(await readFile(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 1440]) {
  test(`loaded browser UI and producer Connector have separate identities ${locale} ${width}`, async ({browser}, info) => {
    const context = await browser.newContext({locale, viewport: {width, height: 1000}}), page = await context.newPage();
    const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
    const t = (key: string, vars: Record<string,string> = {}) => copy[key].replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? '');
    const state = await diagnosticsFixture(page);
    state.producer.bootstrap = structuredClone(bootstrap);
    try {
      await page.goto('/dashboard/#/settings?section=about');
      const line = page.locator('[data-build-line]');
      await expect(line).toBeVisible();
      await expect(line).toContainText('Frontend SHA-256');
      const {frontendDigest} = await import('../scripts/frontend-build.mjs');
      const digest = frontendDigest(desktop);
      const frontend = t('diag_frontend_build', {digest});
      const connector = t('diag_connector_build', {version: bootstrap.capabilities.build.version,
        commit: bootstrap.capabilities.build.commit || copy.diag_unknown, source: bootstrap.capabilities.build.source});
      await expect(line).toHaveText(`${frontend} · ${connector}`);
      await expect(page.locator('#settings-panel-about')).not.toContainText(/Fleet|native|原生|桌面版|Desktop/);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.locator('[data-export-diagnostics]').click();
      const json = page.locator('[data-diagnostics-json]');
      await expect(json).toContainText('batc.diagnostics.v1');
      const bundle = JSON.parse((await json.textContent())!);
      expect(bundle.client).toMatchObject({frontend_digest: digest, app_version: null, app_commit: null,
        connector_version: bootstrap.capabilities.build.version, connector_commit: bootstrap.capabilities.build.commit,
        connector_source: bootstrap.capabilities.build.source, platform: 'browser'});
      await expect(page.locator('[data-diagnostics-summary]')).toContainText(`${frontend} · ${connector}`);
      expect(Object.keys(bundle.client).some(key => /fleet|native/i.test(key))).toBe(false);
      const download = page.waitForEvent('download');
      await page.getByRole('dialog').getByRole('button', {name: copy.diag_save, exact: true}).click();
      expect(JSON.parse(await readFile((await (await download).path())!, 'utf8'))).toEqual(bundle);
      await page.screenshot({path: info.outputPath(`frontend-${locale}-${width}.png`)});
    } finally {await context.close();}
  });
}

test('browser build digest is reproducible, ignores native commit, and changes for one input byte', async ({}, info) => {
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
    return {js, digest: js.match(/frontend_digest:\s*["']([a-f0-9]{64})["']/)?.[1]};
  };
  const first = await build('first', '1234567890abcdef'.repeat(2) + '12345678');
  const second = await build('second', 'fedcba0987654321'.repeat(2) + 'fedcba09');
  expect(first.digest).toMatch(/^[a-f0-9]{64}$/);
  expect(second.digest).toBe(first.digest);
  expect(second.js).toBe(first.js);
  await appendFile(join(project, 'src/main.ts'), ' ');
  const changed = await build('changed', '1234567890abcdef'.repeat(2) + '12345678');
  expect(changed.digest).toMatch(/^[a-f0-9]{64}$/);
  expect(changed.digest).not.toBe(first.digest);
});
