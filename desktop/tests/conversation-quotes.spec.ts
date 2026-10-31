import {test, expect, type Page} from '@playwright/test';
import {conversationFixture, mountConversation, checkpoint} from './conversation-fixture.ts';

const source = 'Review the original proposal.\n> **Keep** `markup`  \r\n>\r\n>> Nested decision\r\n>  Preserve spacing\r\n> <img src=x onerror="window.attacked=1">\r\n> [link](javascript:alert(1))\r\n\n```text\r\n> Keep this code marker\r\n```\n> Second quote';
const quote = '**Keep** `markup`  \r\n\r\n> Nested decision\r\n Preserve spacing\r\n<img src=x onerror="window.attacked=1">\r\n[link](javascript:alert(1))\r\n';
const saved = (page: Page) => page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([key]) => key.startsWith('batc.sync.'))?.[1] || 'null'));
const names = (zh: boolean) => ({quote: zh ? '複製引用' : 'Copy quote', original: zh ? '複製原文' : 'Copy original',
  code: zh ? '複製程式碼' : 'Copy code', fallback: zh ? '要複製的原文' : 'Original text to copy'});

for (const native of [false, true]) {
  const transport = native ? 'IPC' : 'Web';
  for (const locale of ['en-US', 'zh-TW']) {
    const label = names(locale === 'zh-TW');
    for (const width of [390, 1440]) test(`exact quote copy and safe layout (${transport}, ${locale}, ${width})`, async ({page}, info) => {
      const {data, dispatch} = conversationFixture(), copied: string[] = [], errors: string[] = [];
      data.messages = [{id:'quote', role:'assistant', ts:'2026-10-09T12:00:00Z', text:source, status:'completed'}] as any;
      await page.setViewportSize({width, height:900});
      await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value:language}), locale);
      await page.exposeFunction('fixtureCopy', (text: string) => copied.push(text));
      await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {value:{writeText:(text: string) => (window as any).fixtureCopy(text)}}));
      page.on('pageerror', error => errors.push(error.message));
      await mountConversation(page, native, dispatch); await page.goto('/dashboard/#/session/demo/session-1');
      const row = page.locator('[data-message-id="quote"]');
      await expect(row.locator('blockquote')).toHaveCount(2);
      await expect(row.locator('blockquote strong')).toHaveText('Keep');
      await expect(row.locator('img, script, a')).toHaveCount(0);
      await row.getByRole('button', {name:label.quote, exact:true}).first().click();
      await expect.poll(() => copied).toEqual([quote]);
      await row.getByRole('button', {name:label.quote, exact:true}).last().click();
      await expect.poll(() => copied).toEqual([quote, 'Second quote']);
      await row.getByRole('button', {name:label.code, exact:true}).click();
      await expect.poll(() => copied.at(-1)).toBe('> Keep this code marker\r\n');
      await row.hover(); await row.getByRole('button', {name:label.original, exact:true}).click();
      await expect.poll(() => copied.at(-1)).toBe(source);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await row.locator('.message-quote-copy').first().evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
      await row.getByRole('button', {name:label.quote, exact:true}).last().focus();
      await page.keyboard.press('Enter'); await expect.poll(() => copied.length).toBe(5);
      await page.screenshot({path:info.outputPath(`quote-${transport}-${locale}-${width}.png`)});
      expect(errors).toEqual([]); expect(data.writes).toEqual([]);
    });

    test(`message completion controls quote copy without replacing selected text (${transport}, ${locale})`, async ({page}) => {
      const {data, dispatch} = conversationFixture(), copied: string[] = [];
      const message: any = {id:'quote', role:'assistant', ts:'2026-10-09T12:00:00Z', text:'> First part'};
      data.messages = [message];
      await page.addInitScript(language => Object.defineProperty(navigator, 'language', {value:language}), locale);
      await page.exposeFunction('fixtureCopy', (text: string) => copied.push(text));
      await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {value:{writeText:(text: string) => (window as any).fixtureCopy(text)}}));
      await mountConversation(page, native, async input => {
        const result = await dispatch(input);
        // Idle and full reading coverage still cannot prove message completion.
        if (input.path.includes('/messages')) Object.assign(result.data, {streaming:false, reading:{complete:true}});
        return result;
      });
      await page.goto('/dashboard/#/session/demo/session-1');
      const row = page.locator('[data-message-id="quote"]'), button = row.locator('.message-quote-copy');
      await expect(row.locator('blockquote')).toBeVisible(); await expect(button).toBeHidden();
      const draft = page.locator('textarea').first(); await draft.fill('Keep my draft');
      message.status = 'running'; message.text += '\n> More output'; data.cursor++;
      await expect.poll(() => saved(page)).toEqual(checkpoint(data.cursor));
      await expect(button).toBeHidden(); await expect(row.locator('blockquote')).toContainText('More output');
      await row.locator('blockquote .message-text').evaluate(el => {
        (window as any).quoteNode = el;
        const range = document.createRange(); range.selectNodeContents(el);
        window.getSelection()?.removeAllRanges(); window.getSelection()?.addRange(range);
      });
      const selected = await page.evaluate(() => String(window.getSelection()));
      message.status = 'completed'; data.cursor++;
      await expect.poll(() => saved(page)).toEqual(checkpoint(data.cursor));
      await expect(button).toBeVisible();
      expect(await row.locator('blockquote .message-text').evaluate(el => el === (window as any).quoteNode)).toBe(true);
      expect(await page.evaluate(() => String(window.getSelection()))).toBe(selected);
      await page.evaluate(() => window.getSelection()?.removeAllRanges());
      await button.click(); await expect.poll(() => copied).toEqual(['First part\nMore output']);
      await button.focus(); data.cursor++; await expect.poll(() => saved(page)).toEqual(checkpoint(data.cursor));
      await expect(button).toBeFocused(); await expect(draft).toHaveValue('Keep my draft');
      for (const update of [{status:'running'}, {status:'failed'}, {status:'unknown'}, {status:undefined},
        {status:'completed', denied:true}, {status:'completed', denied:false, deferred:true}]) {
        Object.assign(message, update); data.cursor++;
        await expect.poll(() => saved(page)).toEqual(checkpoint(data.cursor)); await expect(button).toBeHidden();
        // A retained reference cannot bypass a later status change.
        await button.evaluate(el => (el as HTMLButtonElement).click()); expect(copied).toHaveLength(1);
      }
      Object.assign(message, {status:'completed', deferred:false}); data.cursor++;
      await expect.poll(() => saved(page)).toEqual(checkpoint(data.cursor)); await expect(button).toBeVisible();
      await button.evaluate(el => (window as any).oldQuoteCopy = el);
      message.text = '> Revised content'; data.cursor++;
      await expect.poll(() => saved(page)).toEqual(checkpoint(data.cursor));
      await page.evaluate(() => (window as any).oldQuoteCopy.click()); expect(copied).toHaveLength(1);
      await button.click(); await expect.poll(() => copied.at(-1)).toBe('Revised content');
      expect(data.writes).toEqual([]);
    });
  }

  test(`quote clipboard fallback keeps exact line endings and expires on navigation (${transport})`, async ({page}) => {
    const {data, dispatch} = conversationFixture();
    data.messages = [{id:'quote', role:'assistant', ts:'2026-10-09T12:00:00Z', text:'> line\r\n>\r\n>> nested', status:'completed'}] as any;
    await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {configurable:true,
      value:{writeText:async () => {throw new Error('denied');}}}));
    await mountConversation(page, native, dispatch); await page.goto('/dashboard/#/session/demo/session-1');
    await page.getByRole('button', {name:'Copy quote', exact:true}).click();
    const fallback = page.getByRole('textbox', {name:'Original text to copy'});
    await expect(fallback).toHaveValue('line\n\n> nested');
    const exact = await fallback.evaluate(el => {
      const clipboardData = new DataTransfer();
      el.dispatchEvent(new ClipboardEvent('copy', {clipboardData, cancelable:true}));
      return clipboardData.getData('text/plain');
    });
    expect(exact).toBe('line\r\n\r\n> nested');
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
      value:{writeText:() => new Promise((_, reject) => (window as any).rejectQuoteCopy = reject)}}));
    await page.getByRole('button', {name:'Copy quote', exact:true}).click();
    await expect(fallback).toHaveCount(0);
    await page.getByRole('button', {name:'Tools', exact:true}).click();
    await page.getByRole('menuitem', {name:'Sessions', exact:true}).click();
    await expect(page.locator('.conversation')).toHaveCount(0);
    await page.evaluate(() => (window as any).rejectQuoteCopy(new Error('denied')));
    await expect(fallback).toHaveCount(0); expect(data.writes).toEqual([]);
  });
}
