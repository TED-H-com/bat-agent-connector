import {expect, test} from '@playwright/test';

for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) test(`shared stamps tick without rebuilding the view ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: 960}, timezoneId:'UTC'}), page = await context.newPage();
  const now = Date.parse('2026-10-13T15:14:00Z');
  await page.clock.install({time:now});
  await page.goto('/ui-components.html');
  await page.evaluate(async now => {
    const {timeStamp, startTimeTicker} = await import('/src/ui/stamps.js');
    const fixture = document.getElementById('fixture')!;
    fixture.append(timeStamp((now - 60000) / 1000), timeStamp('2026-10-14', {dateOnly:true}));
    const input = document.createElement('input'); input.value = 'retained draft'; fixture.append(input);
    input.focus(); input.setSelectionRange(2, 7);
    (window as any).stamp = fixture.querySelector('time');
    (window as any).detachedStamp = timeStamp((now - 60000) / 1000);
    startTimeTicker();
  }, now);
  const stamps = page.locator('time');
  await expect(stamps.first()).toHaveText(locale === 'en' ? '1 min ago' : '1 分鐘前');
  await expect(stamps.first()).toHaveAttribute('datetime', '2026-10-13T15:13:00.000Z');
  await expect(stamps.first()).toHaveAttribute('title', /2026/);
  await expect(stamps.first()).toHaveAttribute('title', /Coordinated Universal Time|世界標準時間/);
  await expect(stamps.nth(1)).toHaveText(locale === 'en' ? 'Oct 14' : '10月14日');
  await page.clock.fastForward(60000);
  await expect(stamps.first()).toHaveText(locale === 'en' ? '2 min ago' : '2 分鐘前');
  expect(await page.evaluate(() => ({same:(window as any).stamp === document.querySelector('time'), detached:(window as any).detachedStamp.textContent,
    focus:document.activeElement === document.querySelector('input'), selection:[document.querySelector('input')!.selectionStart, document.querySelector('input')!.selectionEnd]})))
    .toEqual({same:true, detached:locale === 'en' ? '1 min ago' : '1 分鐘前', focus:true, selection:[2,7]});
  await page.evaluate(() => {Object.defineProperty(document, 'hidden', {value:true, configurable:true}); document.dispatchEvent(new Event('visibilitychange'));});
  await page.clock.fastForward(120000);
  await expect(stamps.first()).toHaveText(locale === 'en' ? '2 min ago' : '2 分鐘前');
  await page.evaluate(() => {Object.defineProperty(document, 'hidden', {value:false, configurable:true}); document.dispatchEvent(new Event('visibilitychange'));});
  await expect(stamps.first()).toHaveText(locale === 'en' ? '4 min ago' : '4 分鐘前');
  await expect(page.locator('input')).toHaveValue('retained draft');
  await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) test(`ticker retains a backward text selection inside a stamp ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: 960}, timezoneId:'UTC'}), page = await context.newPage();
  const now = Date.parse('2026-10-13T15:14:00Z');
  await page.clock.install({time:now});
  await page.goto('/ui-components.html');
  await page.evaluate(async now => {
    const {timeStamp, startTimeTicker} = await import('/src/ui/stamps.js');
    const stamp = timeStamp((now - 59 * 60000) / 1000) as HTMLElement;
    document.getElementById('fixture')!.append(stamp);
    const content = stamp.firstChild!;
    (window as any).stampText = content;
    document.getSelection()!.setBaseAndExtent(content, content.textContent!.length, content, 0);
    startTimeTicker();
  }, now);
  await page.clock.fastForward(60000);
  await expect(page.locator('time')).toHaveText(locale === 'en' ? '1 hr ago' : '1 小時前');
  expect(await page.evaluate(() => {
    const content = document.querySelector('time')!.firstChild!, selection = document.getSelection()!;
    return {same:content === (window as any).stampText, text:selection.toString(),
      anchor:selection.anchorNode === content && selection.anchorOffset === content.textContent!.length,
      focus:selection.focusNode === content && selection.focusOffset === 0};
  })).toEqual({same:true, text:locale === 'en' ? '1 hr ago' : '1 小時前', anchor:true, focus:true});
  await context.close();
});
