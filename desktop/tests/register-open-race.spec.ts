import {expect, test} from '@playwright/test';
import {registerStatusFixture} from './register-status-fixture.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [390, 1440]) {
  test(`Explicit register opening survives a concurrent list refresh ${locale} ${width}`, async ({page}, info) => {
    await page.setViewportSize({width, height: 900});
    const f = registerStatusFixture(locale);
    let detailReads = 0, release!: () => void;
    const waiting = new Promise<void>(resolve => {release = resolve;});
    const id = 'plan_revision.exact_adopted';
    f.overrides.push(async (url, result) => {
      if (url.pathname.endsWith('/register/' + id) && ++detailReads === 1) await waiting;
      return result;
    });
    await f.mount(page, 'issues');
    const row = page.locator(`[data-row-key="${id}"]`);
    await expect(row).toBeVisible();
    const title = locale === 'en' ? 'Plan revision 1' : '計畫修訂 1';
    await row.click();
    await expect.poll(() => detailReads).toBe(1);
    const before = f.reads.filter(path => path.includes('/register?')).length;
    f.emit();
    try {
      await expect.poll(() => f.reads.filter(path => path.includes('/register?')).length).toBeGreaterThan(before);
      release();
      await expect(page.locator('[data-reg-inspector]:not([data-reg-loading])')).toContainText(title);
      await page.screenshot({path: info.outputPath('opened.png')});
      expect(f.writes).toEqual([]);
    } finally {release();}
  });
}

for (const close of ['selection', 'withdraw']) test(`Pending register opening respects ${close}`, async ({page}) => {
  await page.setViewportSize({width: 390, height: 900});
  const f = registerStatusFixture('en');
  let reads = 0, release!: () => void, withdrawn = false;
  const waiting = new Promise<void>(resolve => {release = resolve;});
  const id = 'plan_revision.exact_adopted';
  f.overrides.push(async (url, result) => {
    if (url.pathname.endsWith('/register/' + id)) {
      reads++;
      if (reads === 1) await waiting;
      else if (withdrawn) return {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Unavailable'}}};
    }
    if (withdrawn && url.pathname.endsWith('/register')) result.data.rows = [];
    return result;
  });
  await f.mount(page, 'issues');
  await page.locator(`[data-row-key="${id}"]`).click();
  await expect.poll(() => reads).toBe(1);
  await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
  if (close === 'selection') await page.locator(`[data-row-key]:not([data-row-key="${id}"])`).first().click();
  else withdrawn = true;
  const before = f.reads.filter(path => path.includes('/register?')).length;
  f.emit();
  try {
    await expect.poll(() => f.reads.filter(path => path.includes('/register?')).length).toBeGreaterThan(before);
    if (close === 'withdraw') await expect.poll(() => reads).toBeGreaterThan(1);
    if (close === 'withdraw') await expect(page.locator('[data-reg-inspector]')).toHaveCount(0);
    else await expect(page.locator('[data-reg-inspector]')).not.toHaveAttribute('data-reg-row', id);
    release();
    await expect(page.locator(`[data-reg-inspector][data-reg-row="${id}"]`)).toHaveCount(0);
    expect(f.writes).toEqual([]);
  } finally {release();}
});
