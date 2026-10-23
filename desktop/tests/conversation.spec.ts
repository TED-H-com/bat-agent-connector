import {test, expect, type Page} from '@playwright/test';
import {conversationFixture, mountConversation, checkpoint, richMessage} from './conversation-fixture.ts';

const saved = (page: Page) => page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([key]) => key.startsWith('batc.sync.'))?.[1] || 'null'));
const bottom = (page: Page) => page.locator('.conversation-scroll').evaluate(el => el.scrollHeight-el.scrollTop-el.clientHeight);
for (const native of [false,true]) {
  const platform = native ? 'IPC' : 'Web';
  for (const timestamp of ['', 'not-a-time']) test(`missing message time stays empty after a ticker refresh (${platform}, ${timestamp ? 'invalid' : 'empty'})`, async ({page}) => {
    const {data, dispatch} = conversationFixture(); await mountConversation(page, native, dispatch);
    await page.goto('/dashboard/#/session/demo/session-1');
    const time = page.locator('[data-message-id="message-0"] .message-time');
    await expect(time).toHaveAttribute('datetime', /2026-10-09/);
    await time.evaluate(node => (window as any).retainedMessageTime = node);
    const draft = page.locator('textarea').first(); await draft.fill('Retain this draft.');
    data.messages[0].ts = timestamp; data.cursor++;
    await expect.poll(() => saved(page)).toEqual(checkpoint(1));
    await expect(time).toHaveText('');
    await expect(time).not.toHaveAttribute('datetime');
    await expect(time).not.toHaveAttribute('title');
    await expect(time).not.toHaveAttribute('data-ui-time');
    await page.evaluate(async () => {const {refreshTimeStamps} = await import('/src/ui/stamps.js'); refreshTimeStamps();});
    await expect(time).toHaveText('');
    expect(await time.evaluate(node => node === (window as any).retainedMessageTime)).toBe(true);
    await expect(page.locator('.msg')).toHaveCount(12); await expect(draft).toHaveValue('Retain this draft.');
    expect(data.writes).toEqual([]);
  });
  test(`safe rich content, exact clipboard and failed clipboard fallback (${platform})`, async ({page}) => {
    const {data,dispatch}=conversationFixture();
    data.messages[data.messages.length-1].text += '\n<img src=x onerror="window.attacked=1">\n[unsafe](javascript:alert(1))';
    const copied:string[]=[]; await page.exposeFunction('fixtureCopy', (text:string)=>copied.push(text));
    await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:(text:string)=>(window as any).fixtureCopy(text)}}));
    await mountConversation(page,native,dispatch); await page.goto('/dashboard/#/session/demo/session-1');
    await expect(page.locator('.conversation table')).toHaveCount(1);
    await expect(page.locator('.conversation th')).toHaveText(['Option','Result']);
    await expect(page.locator('.conversation strong')).toHaveText('A');
    await expect(page.locator('.conversation img,.conversation script,.conversation a')).toHaveCount(0);
    const last = page.locator('.msg').last();
    await last.hover();
    await last.getByRole('button',{name:'Copy original',exact:true}).click();
    await expect.poll(()=>copied).toEqual([data.messages.at(-1)!.text]);
    await page.getByRole('button',{name:'Copy code',exact:true}).click();
    await expect.poll(()=>copied[1]).toBe('const result = "<script>fixture only</script>";\r\nconsole.log(result);\r\n');
    await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw new Error('denied');}}}));
    await last.hover();
    await last.getByRole('button',{name:'Copy original',exact:true}).click();
    const fallback=page.getByRole('textbox',{name:'Original text to copy'});
    await expect(fallback).toHaveValue(data.messages.at(-1)!.text.replace(/\r\n/g,'\n'));
    const manual=await fallback.evaluate(el=>{
      const clipboardData=new DataTransfer();
      el.dispatchEvent(new ClipboardEvent('copy',{clipboardData,cancelable:true}));
      return clipboardData.getData('text/plain');
    });
    expect(manual).toBe(data.messages.at(-1)!.text);
    await expect(page.getByText('Copied.',{exact:true})).toHaveCount(0);
    expect(data.writes).toEqual([]);
  });
  test(`reading anchor, selection, focus and drafts survive refresh; explicit latest resumes following (${platform})`, async ({page}) => {
    const {data,dispatch}=conversationFixture(); await mountConversation(page,native,dispatch);
    await page.goto('/dashboard/#/session/demo/session-1'); await expect(page.locator('.msg')).toHaveCount(12);
    await expect.poll(()=>bottom(page)).toBeLessThanOrEqual(1);
    const draft=page.locator('textarea').first(); await draft.fill('Keep this draft');
    const row=page.locator('.msg').nth(3);
    await row.evaluate(el=>{const box=el.closest('.conversation-scroll')!; box.scrollTop+=el.getBoundingClientRect().top-box.getBoundingClientRect().top;});
    await row.hover();
    await row.getByRole('button',{name:'Copy original'}).focus();
    await row.locator('.message-text').evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range);});
    const oldTop=await row.evaluate(el=>el.getBoundingClientRect().top), selected=await page.evaluate(()=>String(window.getSelection()));
    data.messages[0].text += '\nEarlier row now has more lines.'.repeat(15);
    data.messages.at(-1)!.text += '\nStreamed extra text.'; data.cursor++;
    await expect.poll(()=>saved(page)).toEqual(checkpoint(1));
    expect(Math.abs(await row.evaluate(el=>el.getBoundingClientRect().top)-oldTop)).toBeLessThan(2);
    expect(await page.evaluate(()=>String(window.getSelection()))).toBe(selected);
    await expect(row.getByRole('button',{name:'Copy original'})).toBeFocused(); await expect(draft).toHaveValue('Keep this draft');
    await page.evaluate(()=>window.getSelection()?.removeAllRanges());
    await page.getByRole('button',{name:'New messages',exact:true}).click();
    await expect.poll(()=>bottom(page)).toBeLessThanOrEqual(1);
    data.messages.at(-1)!.text += '\nMore streamed content.'.repeat(20); data.cursor++;
    await expect.poll(()=>saved(page)).toEqual(checkpoint(2)); await expect.poll(()=>bottom(page)).toBeLessThanOrEqual(1);
    expect(data.writes).toEqual([]);
  });
  test(`rolling window reports lost anchor and failed refresh retains messages and event checkpoint (${platform})`, async ({page}) => {
    const {data,dispatch}=conversationFixture(); await mountConversation(page,native,dispatch);
    await page.goto('/dashboard/#/session/demo/session-1'); await expect(page.locator('.msg')).toHaveCount(12);
    await page.locator('.conversation-scroll').evaluate(el=>el.scrollTop=0);
    data.messages=data.messages.slice(3); data.cursor++;
    await expect.poll(()=>saved(page)).toEqual(checkpoint(1));
    await expect(page.getByText('Your previous reading position is outside the messages currently loaded.')).toBeVisible();
    data.fail=true; data.cursor++;
    await expect(page.locator('[data-session-error=UNAVAILABLE]')).toContainText('Messages unavailable');
    await expect(page.locator('[data-session-error=UNAVAILABLE] details')).toContainText('UNAVAILABLE');
    expect(await saved(page)).toEqual(checkpoint(1)); await expect(page.locator('.msg')).toHaveCount(9);
    data.fail=false; await expect.poll(()=>saved(page),{timeout:10000}).toEqual(checkpoint(2));
    expect(data.writes).toEqual([]);
  });
  for (const locale of ['en-US', 'zh-TW']) test(`reader movement during a delayed response wins over the previous position (${platform}, ${locale})`, async ({page}, info) => {
    const {data,dispatch}=conversationFixture(); let release!:()=>void, waiting=false;
    const gate=new Promise<void>(resolve=>release=resolve);
    await page.addInitScript(language=>Object.defineProperty(navigator,'language',{value:language}),locale);
    await mountConversation(page,native,async input=>{if(data.cursor && input.path.includes('/messages')) {waiting=true;await gate;} return dispatch(input);});
    await page.goto('/dashboard/#/session/demo/session-1'); await expect(page.locator('.msg')).toHaveCount(12);
    await expect.poll(()=>bottom(page)).toBeLessThanOrEqual(1);
    data.cursor++; await expect.poll(()=>waiting).toBe(true);
    await page.locator('.conversation-scroll').evaluate(el=>{
      // Withhold scroll delivery to the reader until the response has rendered.
      // This controls the browser's asynchronous event order without a delay.
      const delivery = {held: 0, delivered: 0, release: () => {}};
      const hold = (event: Event) => {
        if (event.target === el) {delivery.held++; event.stopImmediatePropagation();}
      };
      window.addEventListener('scroll', hold, true);
      el.addEventListener('scroll', () => delivery.delivered++);
      delivery.release = () => {
        window.removeEventListener('scroll', hold, true);
        el.dispatchEvent(new Event('scroll'));
      };
      (window as any).readerScrollDelivery = delivery;
      el.scrollTop = 100;
    });
    data.messages.at(-1)!.text+='\nnew output'; release();
    await expect.poll(()=>saved(page)).toEqual(checkpoint(1));
    const rendered = await page.locator('.conversation-scroll').evaluate(el=>({
      scrollTop: el.scrollTop, bottom: el.scrollHeight-el.clientHeight,
      held: (window as any).readerScrollDelivery.held, delivered: (window as any).readerScrollDelivery.delivered,
      output: el.lastElementChild!.textContent!.includes('new output'),
    }));
    await info.attach('response-before-scroll-delivery', {body: JSON.stringify(rendered), contentType: 'application/json'});
    expect(rendered.output).toBe(true);
    expect(rendered.delivered).toBe(0);
    expect(rendered.scrollTop).toBe(100);
    await page.evaluate(()=>(window as any).readerScrollDelivery.release());
    expect(await page.locator('.conversation-scroll').evaluate(el=>el.scrollTop)).toBe(100);
    await expect(page.getByRole('button',{name:locale==='zh-TW'?'新訊息':'New messages',exact:true})).toBeVisible();
    expect(data.writes).toEqual([]);
  });
  test(`messages without IDs preserve duplicate rows and a retained anchor when older rows leave (${platform})`, async ({page}) => {
    const {data,dispatch}=conversationFixture();
    data.messages=data.messages.map(({id,...message})=>message) as typeof data.messages;
    data.messages[1]={...data.messages[0]};
    await mountConversation(page,native,dispatch); await page.goto('/dashboard/#/session/demo/session-1');
    await expect(page.locator('.msg')).toHaveCount(12);
    const row=page.locator('.msg').filter({hasText:'Review note 5'});
    await row.evaluate(el=>{const box=el.closest('.conversation-scroll')!; box.scrollTop+=el.getBoundingClientRect().top-box.getBoundingClientRect().top;});
    await row.hover();
    await row.getByRole('button',{name:'Copy original'}).focus();
    const top=await row.evaluate(el=>el.getBoundingClientRect().top);
    data.messages=data.messages.slice(2); data.cursor++;
    await expect.poll(()=>saved(page)).toEqual(checkpoint(1));
    await expect(page.locator('.msg')).toHaveCount(10);
    expect(Math.abs(await row.evaluate(el=>el.getBoundingClientRect().top)-top)).toBeLessThan(2);
    await expect(row.getByRole('button',{name:'Copy original'})).toBeFocused();
  });
  test(`late clipboard failure cannot show a previous session's text after navigation (${platform})`, async ({page}) => {
    const {dispatch}=conversationFixture(); await mountConversation(page,native,dispatch);
    await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:()=>new Promise((_,reject)=>Object.assign(window,{rejectCopy:reject}))}}));
    await page.goto('/dashboard/#/session/demo/session-1');
    const late = page.locator('.msg').last();
    await late.hover();
    await late.getByRole('button',{name:'Copy original',exact:true}).click();
    await page.getByRole('button', {name: 'Tools', exact: true}).click();
    await page.getByRole('menuitem',{name:'Sessions',exact:true}).click();
    await expect(page.locator('.conversation')).toHaveCount(0);
    await page.evaluate(()=>(window as any).rejectCopy(new Error('denied')));
    await expect(page.getByRole('textbox',{name:'Original text to copy'})).toHaveCount(0);
  });
  for(const locale of ['en-US','zh-TW']) for(const width of [390,768,1440]) {
    test(`conversation layout ${platform} ${locale} ${width}`,async({page},info)=>{
      await page.setViewportSize({width,height:900});
      await page.addInitScript(language=>Object.defineProperty(navigator,'language',{value:language}),locale);
      const {data,dispatch}=conversationFixture(); const errors:string[]=[]; page.on('pageerror',e=>errors.push(e.message));
      data.messages.at(-1)!.text = richMessage+'\n\n```text\n'+'long_unbroken_value_'.repeat(70)+'\n```';
      await mountConversation(page,native,dispatch); await page.goto('/dashboard/#/session/demo/session-1');
      await expect(page.locator('.conversation table')).toHaveCount(1);
      await page.locator('.conversation').evaluate(el=>scrollBy(0,el.getBoundingClientRect().top-document.querySelector('.top')!.getBoundingClientRect().bottom-12));
      await page.locator('.conversation-scroll').evaluate(el=>el.scrollTop=el.scrollHeight);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      const article = page.locator('.msg').last();
      const copyName = locale==='zh-TW'?'複製原文':'Copy original';
      if (width > 800) await article.hover();
      await expect(article.getByRole('button',{name:copyName,exact:true})).toBeVisible();
      await page.screenshot({path:info.outputPath(`conversation-${platform}-${locale}-${width}.png`)});
      expect(errors).toEqual([]); expect(data.writes).toEqual([]);
    });
  }
}

test('Web and desktop can observe the same central conversation together while keeping local drafts separate',async({page,browser})=>{
  const {data,dispatch}=conversationFixture(), desktop=await browser.newPage();
  try {
    await mountConversation(page,false,dispatch); await mountConversation(desktop,true,dispatch);
    for(const client of [page,desktop]) {
      await client.goto('/dashboard/#/session/demo/session-1'); await expect(client.locator('.msg')).toHaveCount(12);
    }
    await page.locator('textarea').first().fill('Browser draft');
    await desktop.locator('textarea').first().fill('Desktop draft');
    data.messages.push({id:'shared',role:'assistant',ts:'2026-10-09T13:00:00Z',text:'Update visible in both clients.'}); data.cursor++;
    for(const client of [page,desktop]) {
      await expect.poll(()=>saved(client)).toEqual(checkpoint(1));
      await expect(client.getByText('Update visible in both clients.',{exact:true})).toBeVisible();
    }
    await expect(page.locator('textarea').first()).toHaveValue('Browser draft');
    await expect(desktop.locator('textarea').first()).toHaveValue('Desktop draft');
    await page.close(); data.messages.at(-1)!.text='Still available after the other client closes.'; data.cursor++;
    await expect.poll(()=>saved(desktop)).toEqual(checkpoint(2));
    await expect(desktop.getByText('Still available after the other client closes.',{exact:true})).toBeVisible();
    expect(data.writes).toEqual([]);
  } finally {await desktop.close();}
});
