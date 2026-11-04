import {test, expect} from '@playwright/test';
import {publishedFixture} from './repository-start-fixture';

for (const native of [false, true]) for (const language of ['en', 'zh-TW']) {
  test(`deployment status is an explicit read with escaped metadata (${native ? 'IPC' : 'HTTP'}, ${language})`, async ({page}) => {
    await page.addInitScript(language => localStorage.setItem('batc.lang', language), language);
    let reads = 0;
    const state = await publishedFixture(page, native, {extraDispatch: async (input: any) => {
      const url = new URL(input.path, 'http://fixture');
      if (url.pathname === '/hosts/demo/discovery') return {status: 200, data: {scopes: []}};
      if (url.pathname === '/hosts/deploy-status') {
        expect(input.method).toBe('GET'); expect(url.searchParams.get('host')).toBe('demo'); reads++;
        return {status: 200, data: {version: 1, hosts: [{host: 'demo', hostname: 'fixture-host', collected_at: '2026-11-04T00:00:00Z',
          containers: [{name: '<img src=x onerror=alert(1)>', state: 'running'}], compose_projects: [], systemd: [], deploys: [],
          errors: [{source: 'podman', code: 'permission_denied'}]}]}};
      }
    }});
    await page.goto('/dashboard/#/host/demo');
    const button = page.getByRole('button', {name: language === 'en' ? 'Host deployment status (read-only)' : '主機部署狀態（唯讀）', exact: true});
    await expect(button).toBeVisible(); expect(reads).toBe(0);
    await button.click();
    await expect(page.locator('#main')).toContainText('fixture-host');
    const panel = button.locator('..');
    await panel.locator('summary').first().click();
    await expect(panel).toContainText('<img src=x onerror=alert(1)>');
    expect(await panel.locator('img').count()).toBe(0);
    await panel.locator('summary').last().click();
    await expect(panel).toContainText(language === 'en' ? 'permission denied' : '權限不足');
    expect(reads).toBe(1); expect(state.posts).toEqual([]); expect(state.errors).toEqual([]);
  });
}
