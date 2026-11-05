import {test, expect, type Page} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';

const FIRST = 'a'.repeat(64), SECOND = 'b'.repeat(64);
const SAVED = 1793872800;
function archiveFixture() {
  const base = conversationFixture();
  const data = {latest:FIRST, gone:false, refused:0, liveUnavailable:false, hold:false,
    held:null as null | (()=>void), reads:[] as URL[]};
  const document = (url:URL) => {
    const snapshot = url.searchParams.get('snapshot_id') || data.latest;
    const offset = Number(url.searchParams.get('offset') || 0);
    const end = 65 - offset, start = Math.max(0, end - 30);
    return {host:'demo', session_id:'session-1', source:'retained', snapshot_id:snapshot,
      saved_at:SAVED, last_checked_at:SAVED + 60, source_status:'source_unavailable',
      execution_status:'unknown', coverage:{omitted_messages:2, clipped_messages:1, reason:'backfill_pending'},
      brief:{kind:'extractive', items:[{kind:'latest_request', message_id:'saved-64', revision:FIRST,
        text:'An excerpt from saved source text', clipped:false}]},
      messages:Array.from({length:Math.max(0, end - start)}, (_, i)=>({id:`saved-${start + i}`, role:i % 2 ? 'assistant' : 'user',
        text:`${snapshot === FIRST ? 'Original' : 'New'} saved note ${start + i} <img src=x onerror=alert(1)>`,
        revision:FIRST, clipped:start + i === 64})),
      offset, next_offset:start > 0 ? offset + end - start : null,
      snapshots:[{snapshot_id:data.latest, saved_at:SAVED}, ...(data.latest === FIRST ? [] : [{snapshot_id:FIRST, saved_at:SAVED - 60}])]};
  };
  const retained = async (url:URL) => {
    data.reads.push(url);
    const result = document(url);
    if (data.hold) {data.hold = false; await new Promise<void>(resolve=>{data.held = resolve;});}
    if (data.refused) return {status:data.refused, data:{error:{code:'NOT_FOUND', message:'Private source diagnostic'}}};
    return {status:200, data:result};
  };
  const dispatch = async (input:any) => {
    const url = new URL(input.path, 'http://fixture');
    if (url.pathname.endsWith('/messages') && url.searchParams.get('source') === 'retained') return retained(url);
    if (url.pathname === '/sessions/demo/session-1' && data.gone)
      return {status:404, data:{error:{code:'NOT_FOUND', message:'Session not found'}}};
    if (url.pathname.endsWith('/messages') && data.liveUnavailable)
      return {status:503, data:{error:{code:'SOURCE_UNAVAILABLE', message:'Live source unavailable'}}};
    const result = await base.dispatch(input);
    if (url.pathname === '/capabilities') result.data = {...result.data, features:{session_archive:{version:1}}};
    if (url.pathname === '/bootstrap') result.data.capabilities = {...result.data.capabilities, features:{session_archive:{version:1}}};
    return result;
  };
  return {data, base, retained, dispatch};
}

for (const native of [false, true]) for (const locale of ['en', 'zh-TW']) {
  test(`retained history pages remain pinned and readable with live source unavailable (${native ? 'IPC' : 'Web'}, ${locale})`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport:{width:390, height:844}}), page = await context.newPage();
    const {data, base, dispatch} = archiveFixture(); data.liveUnavailable = true;
    try {
      await mountConversation(page, native, dispatch); await page.goto('/dashboard/#/session/demo/session-1');
      const pane = page.locator('[data-session-archive]');
      await pane.locator('summary').click();
      expect(data.reads).toHaveLength(0);
      const read = pane.getByRole('button', {name:locale === 'en' ? 'Read saved history' : '讀取保存歷史', exact:true});
      await read.click();
      await expect(pane.locator('[data-archive-message="saved-64"]')).toContainText('Original saved note 64');
      await expect(pane).toContainText(locale === 'en' ? 'Earlier source pages are still being saved' : '較早的來源頁面仍在背景保存中');
      await expect(pane).toContainText(locale === 'en' ? 'last source read failed' : '上次來源讀取失敗');
      await expect(pane.locator('time')).toHaveCount(1);
      expect(await pane.innerText()).not.toMatch(/\[object |undefined|null|Private source diagnostic/);
      expect(await pane.evaluate(node=>node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      expect(await pane.locator('img').count()).toBe(0);
      data.latest = SECOND;
      await pane.getByRole('button', {name:locale === 'en' ? 'Older messages' : '較早的訊息', exact:true}).click();
      await expect(pane.locator('[data-archive-message="saved-5"]')).toContainText('Original saved note 5');
      await pane.getByRole('button', {name:locale === 'en' ? 'Older messages' : '較早的訊息', exact:true}).click();
      await expect(pane.locator('[data-archive-message="saved-0"]')).toContainText('Original saved note 0');
      await expect(pane.getByRole('button', {name:locale === 'en' ? 'Older messages' : '較早的訊息', exact:true})).toBeHidden();
      await pane.getByRole('button', {name:locale === 'en' ? 'Newer messages' : '較新的訊息', exact:true}).click();
      await expect(pane.locator('[data-archive-message="saved-5"]')).toBeVisible();
      await pane.getByRole('button', {name:locale === 'en' ? 'Latest in this version' : '此版本的最新訊息', exact:true}).click();
      await expect(pane.locator('[data-archive-message="saved-64"]')).toContainText('Original saved note 64');
      expect(data.reads.slice(1).every(url=>url.searchParams.get('snapshot_id') === FIRST)).toBe(true);
      await read.click();
      await expect(pane.locator('[data-archive-message="saved-64"]')).toContainText('New saved note 64');
      expect(data.reads.at(-1)!.searchParams.has('snapshot_id')).toBe(false);
      await pane.getByRole('combobox').selectOption(FIRST);
      await expect(pane.locator('[data-archive-message="saved-64"]')).toContainText('Original saved note 64');
      expect(base.data.writes).toEqual([]);
    } finally {await context.close();}
  });
}

for (const native of [false, true]) {
  test(`live withdrawal fences delayed saved reads but permits a new authorized archive read (${native ? 'IPC' : 'Web'})`, async ({page}) => {
    const {data, base, dispatch} = archiveFixture();
    await mountConversation(page, native, dispatch); await page.goto('/dashboard/#/session/demo/session-1');
    const pane = page.locator('[data-session-archive]');
    await pane.locator('summary').click(); await pane.getByRole('button', {name:'Read saved history', exact:true}).click();
    await expect(pane.locator('[data-archive-message]')).toHaveCount(30);
    data.hold = true; await pane.getByRole('button', {name:'Older messages', exact:true}).click();
    await expect.poll(()=>Boolean(data.held)).toBe(true);
    data.gone = true; base.data.cursor++;
    await expect(page.locator('[data-session-withdrawn="gone"]')).toBeVisible({timeout:10000});
    await expect(page.locator('[data-session-view]')).toHaveCount(0);
    await expect(pane.locator('[data-archive-message]')).toHaveCount(0);
    data.held!(); data.held = null;
    // A new explicit read starts after withdrawal; the pending response cannot authorize it.
    await pane.getByRole('button', {name:'Read saved history', exact:true}).click();
    await expect(pane.locator('[data-archive-message="saved-64"]')).toContainText('Original saved note 64');
    await expect(pane.locator('[data-archive-message="saved-5"]')).toHaveCount(0);
    await expect(page.locator('[data-session-view]')).toHaveCount(0);
    data.refused = 403;
    await pane.getByRole('button', {name:'Read saved history', exact:true}).click();
    await expect(pane.locator('[data-archive-message]')).toHaveCount(0);
    await expect(pane).toContainText('Saved history could not be read');
    await expect(pane).not.toContainText('An excerpt from saved source text');
    expect(base.data.writes).toEqual([]);
  });
}

async function mountComponent(page:Page, fixture:ReturnType<typeof archiveFixture>) {
  await page.exposeFunction('readArchive', async (_method:string, path:string)=>{
    const result = await fixture.retained(new URL(path, 'http://fixture'));
    if (result.status !== 200) throw Error('Source unavailable');
    return result.data;
  });
  await page.goto('/ui-components.html');
  await page.evaluate(async ()=>{
    const {sessionArchivePanel} = await import('/src/session-archive.js');
    const {sessionArchiveStrings} = await import('/src/session-archive-strings.js');
    const {h} = await import('/src/ui/dom.js');
    const win = window as any; win.archiveActive = true;
    win.archivePanel = sessionArchivePanel({h, host:'demo', sessionId:'session-1', api:win.readArchive,
      guard:()=>{if (!win.archiveActive) throw Error('View changed');},
      when:(value:number)=>new Date(value).toISOString(),
      t:(key:string, vars:Record<string, unknown> = {})=>Object.entries(vars).reduce((text, [name, value])=>text.replaceAll('{'+name+'}', String(value)), sessionArchiveStrings.en[key])});
    document.getElementById('fixture')!.append(win.archivePanel.box);
  });
  const pane = page.locator('[data-session-archive]');
  await pane.locator('summary').click(); await pane.getByRole('button', {name:'Read saved history', exact:true}).click();
  await expect(pane.locator('[data-archive-message]')).toHaveCount(30);
  return pane;
}

test('immutable-page reauthorization preserves selection and clears revoked content', async ({page})=>{
  await page.clock.install();
  const fixture = archiveFixture(), pane = await mountComponent(page, fixture);
  await page.evaluate(()=>{
    const text = document.querySelector('[data-archive-message="saved-64"] p')!.firstChild!;
    getSelection()!.setBaseAndExtent(text, 0, text, 22);
    (window as any).savedTextNode = text;
  });
  const selected = await page.evaluate(()=>getSelection()!.toString());
  await page.clock.fastForward(30001);
  await expect.poll(()=>fixture.data.reads.length).toBe(2);
  await expect(pane).toHaveAttribute('aria-busy', 'false');
  expect(await page.evaluate(()=>getSelection()!.toString())).toBe(selected);
  expect(await page.evaluate(()=>document.querySelector('[data-archive-message="saved-64"] p')!.firstChild === (window as any).savedTextNode)).toBe(true);
  fixture.data.refused = 404;
  await page.clock.fastForward(30001);
  await expect(pane.locator('[data-archive-message]')).toHaveCount(0);
  await expect(pane).toContainText('Saved history could not be read');
  await page.clock.fastForward(60001);
  expect(fixture.data.reads).toHaveLength(3);
});

test('a retired view cannot repaint a late retained page or leave old private content visible', async ({page})=>{
  const fixture = archiveFixture(), pane = await mountComponent(page, fixture);
  fixture.data.hold = true;
  await pane.getByRole('button', {name:'Older messages', exact:true}).click();
  await expect.poll(()=>Boolean(fixture.data.held)).toBe(true);
  await page.evaluate(()=>{(window as any).archiveActive = false;});
  fixture.data.held!();
  await expect(pane.locator('[data-archive-message]')).toHaveCount(0);
  await expect(pane).not.toContainText('An excerpt from saved source text');
  await expect(pane).toHaveAttribute('aria-busy', 'false');
});
