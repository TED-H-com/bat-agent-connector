import {test, expect} from '@playwright/test';
import {browserAuthService as service} from './browser-auth-service';
import {STRINGS} from '../src/i18n.js';

test.describe.configure({mode: 'parallel'});
const tokenKey = 'batc.dashboard.token';

for (const locale of ['en-US', 'zh-TW']) for (const status of [401, 403]) {
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  for (const storage of ['sessionStorage', 'localStorage'] as const) {
    test(`remembered credential refusal pauses automatic starts ${status} ${storage} ${locale}`, async ({browser}) => {
      const central = await service(), context = await browser.newContext({locale});
      try {
        await central.status(status);
        const page = await context.newPage();
        await page.goto(central.origin + '/dashboard/');
        await expect(page.getByRole('button', {name: copy.connect, exact: true})).toBeVisible();
        const saved = {'batc.draft.example': '{"text":"draft","attachments":[]}',
          'batc.key.example.pending': '{"key":"original-key"}',
          'batc.key.example.accepted': JSON.stringify({key: 'accepted-key', op: 'op_' + 'a'.repeat(32)})};
        await page.evaluate(({storage, token, saved}) => {
          window[storage].setItem('batc.dashboard.token', token);
          for (const [key, value] of Object.entries(saved)) localStorage.setItem(key, value);
        }, {storage, token: central.token, saved});
        let presentations = 0;
        context.on('request', request => {if (request.headers().authorization === `Bearer ${central.token}`) presentations++;});
        await page.reload();
        await expect(page.getByRole('alert')).toContainText(copy.global_token_rejected);
        expect(presentations).toBe(1);
        expect(await page.evaluate(() => [sessionStorage, localStorage].map(store => Object.keys(store)
          .filter(key => key.startsWith('batc.credential-refused.')).length)))
          .toEqual(storage === 'sessionStorage' ? [1, 0] : [0, 1]);
        await page.reload();
        await expect(page.getByRole('alert')).toContainText(copy.global_token_rejected);
        if (storage === 'localStorage') {
          const next = await context.newPage(); await next.goto(central.origin + '/dashboard/');
          await expect(next.getByRole('alert')).toContainText(copy.global_token_rejected);
          await next.close();
        }
        expect(presentations).toBe(1);
        expect(await page.evaluate(({storage, tokenKey}) => window[storage].getItem(tokenKey), {storage, tokenKey})).toBe(central.token);
        await central.status(200);
        await page.getByRole('button', {name: copy.connection_retry_remembered, exact: true}).click();
        await expect(page.locator('#live')).toHaveText(copy.ui_connected);
        for (const [key, value] of Object.entries(saved)) expect(await page.evaluate(k => localStorage.getItem(k), key)).toBe(value);
      } finally {await context.close(); await central.close();}
    });
  }

  test(`poll refusal stops credential requests until explicit connection ${status} ${locale}`, async ({browser}) => {
    const central = await service(), context = await browser.newContext({locale});
    try {
      const page = await context.newPage();
      await page.goto(central.origin + '/dashboard/');
      await page.locator('input[type=password]').fill(central.token);
      await page.locator('input[type=checkbox]').check();
      await page.getByRole('button', {name: copy.connect, exact: true}).click();
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      let presentations = 0;
      page.on('request', request => {if (request.headers().authorization === `Bearer ${central.token}`) presentations++;});
      await central.status(status);
      await expect(page.locator('#live')).toHaveText(copy.ui_connection_refused);
      const refusedCount = presentations;
      // Advance beyond multiple normal poll and retry intervals without resending.
      await page.clock.install(); await page.clock.runFor(10_000);
      expect(presentations).toBe(refusedCount);
      await page.reload();
      await expect(page.getByRole('alert')).toContainText(copy.global_token_rejected);
      expect(presentations).toBe(refusedCount);
      await page.locator('input[type=password]').fill(central.replacement);
      await page.getByRole('button', {name: copy.connect, exact: true}).click();
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      expect(await page.evaluate(key => sessionStorage.getItem(key), tokenKey)).toBe(central.replacement);
      expect(await page.evaluate(() => [localStorage, sessionStorage].flatMap(store => Object.keys(store))
        .filter(key => key.startsWith('batc.credential-refused.')))).toEqual([]);
    } finally {await context.close(); await central.close();}
  });

  test(`restore refusal after capabilities pauses the same remembered credential ${status} ${locale}`, async ({browser}) => {
    const central = await service(), context = await browser.newContext({locale});
    try {
      const page = await context.newPage();
      await page.goto(central.origin + '/dashboard/');
      await expect(page.getByRole('button', {name: copy.connect, exact: true})).toBeVisible();
      await page.evaluate(token => localStorage.setItem('batc.dashboard.token', token), central.token);
      await page.route('**/api/v1/bootstrap', async route => {
        await central.status(status); await route.continue();
      });
      let presentations = 0;
      page.on('request', request => {if (request.headers().authorization === `Bearer ${central.token}`) presentations++;});
      await page.reload();
      await expect(page.getByRole('alert')).toContainText(copy.global_token_rejected);
      expect(presentations).toBe(2);
      await page.reload();
      await expect(page.getByRole('alert')).toContainText(copy.global_token_rejected);
      expect(presentations).toBe(2);
      await page.getByRole('button', {name: copy.connection_retry_remembered, exact: true}).click();
      await expect.poll(() => presentations).toBe(3);
      await expect(page.getByRole('alert')).toContainText(copy.global_token_rejected);
      await page.reload();
      await expect(page.getByRole('alert')).toContainText(copy.global_token_rejected);
      expect(presentations).toBe(3);
    } finally {await context.close(); await central.close();}
  });
}

for (const locale of ['en-US', 'zh-TW']) {
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  test(`disconnect forgets the refused credential record ${locale}`, async ({browser}) => {
    const central = await service(), context = await browser.newContext({locale});
    try {
      const page = await context.newPage();
      await page.goto(central.origin + '/dashboard/');
      await page.locator('input[type=password]').fill(central.token);
      await page.locator('input[type=checkbox]').check();
      await page.getByRole('button', {name: copy.connect, exact: true}).click();
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await central.status(401);
      await expect(page.locator('#live')).toHaveText(copy.ui_connection_refused);
      await page.locator('#live').click();
      await page.getByRole('button', {name: copy.disconnect, exact: true}).click();
      await expect(page.locator('input[type=password]')).toBeVisible();
      expect(await page.evaluate(() => [localStorage, sessionStorage].flatMap(store => Object.keys(store))
        .filter(key => key.startsWith('batc.credential-refused.') || key === 'batc.dashboard.token'))).toEqual([]);
    } finally {await context.close(); await central.close();}
  });
}
