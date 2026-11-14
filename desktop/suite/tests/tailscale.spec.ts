// @ts-nocheck
import {test, expect} from '@playwright/test';

for (const language of ['en', 'zh-TW']) {
  test(`isolated Tailscale status is unsupported in ${language}`, async ({page}) => {
    await page.addInitScript(language => {
      localStorage.setItem('batc.lang', language);
      Object.assign(window, {isTauri: true, __suiteCalls: [], __TAURI_INTERNALS__: {invoke: async command => {
        window.__suiteCalls.push(command); throw new Error('Unexpected native call');
      }}});
    }, language);
    await page.goto('/__harness/suite/tests/harness/tailscale.html');
    await expect(page.getByText(language === 'en' ? 'Opening Tailscale is not supported on this platform yet.' : '此平台尚未提供 Tailscale 原生開啟功能。')).toBeVisible();
    await expect(page.locator('[data-tailscale-action="open"]')).toBeDisabled();
    expect(await page.evaluate(() => window.__suiteCalls)).toEqual([]);
  });
}
