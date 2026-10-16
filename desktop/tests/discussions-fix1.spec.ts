import {test, expect, type Locator} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openThreadTab, openThreadBrief} from './pm-navigation.ts';
import {pmFixture, pmRoute} from './pm-fixture.ts';
import {proposalFixture, mountProposal, panel} from './pm-plan-proposals-fixture.ts';

async function emptyCopy(state: Locator, title: string, text: string) {
  await expect(state.locator(':scope > h2')).toHaveText(title);
  await expect(state.locator(':scope > p')).toHaveText(text);
  const weights = await state.evaluate(node => [getComputedStyle(node.querySelector('h2')!).fontWeight, getComputedStyle(node.querySelector('p')!).fontWeight]);
  expect(Number(weights[0])).toBeGreaterThan(Number(weights[1]));
  const colours = await state.evaluate(node => {
    const marker=document.createElement('span'); marker.style.color='var(--text-3)'; node.append(marker);
    const values=[getComputedStyle(node.querySelector('p')!).color,getComputedStyle(marker).color]; marker.remove(); return values;
  });
  expect(colours[0]).toBe(colours[1]);
}

for (const locale of ['en', 'zh-TW']) {
  const zh = locale === 'zh-TW';
  for (const width of [1440, 1000, 390]) test(`proposal answers stay compact beside Adopt ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport:{width,height:width === 390 ? 844 : 900}}), page = await context.newPage();
    const f = demoFixture({locale, discussions:true}); await mountConversation(page, false, f.dispatch);
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
    await page.locator('[data-discussion-ask]').click(); await expect(page.locator('[data-proposal-review]')).toBeEnabled();
    await expect(page.locator('[data-discussion-panel]')).toBeHidden();
    await page.locator('[data-proposal-review]').click();
    const answers = page.locator('.pd-questions'), send = answers.getByRole('button',{name:zh ? '傳送回答' : 'Send answers',exact:true});
    await expect(send).toBeEnabled(); await expect(send).toHaveClass(/secondary/);
    const size = (await send.boundingBox())!, available = (await answers.boundingBox())!;
    expect(size.width).toBeLessThan(available.width * 0.65);
    expect(Math.abs(size.x - (await page.getByRole('button',{name:zh ? '採用' : 'Adopt',exact:true}).boundingBox())!.x)).toBeLessThan(2);
    await expect(page.locator('.ui-button.primary:visible')).toHaveText(zh ? '採用' : 'Adopt');
    const reply = zh ? '請採用五次失敗的門檻。' : 'Use a threshold of five failed attempts.';
    await answers.getByRole('textbox').fill(reply); await send.click();
    await expect(page.locator('.pm-note').last()).toContainText(reply);
    await expect(page.locator('.pm-note').last()).toContainText(zh ? '已儲存' : 'Saved');
    expect(f.discussion.writes.map((row:any) => row.body.action)).toEqual(['pm.conversation.propose','pm.message.append']);
    expect(f.discussion.writes[1].body.params.content).toContain(reply);
    await context.close();
  });

  for (const width of [1440, 390]) test(`report saving needs its own authorization ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL,locale,viewport:{width,height:900}}), page = await context.newPage();
    const f = demoFixture({locale,discussions:true}); await mountConversation(page,false,f.dispatch);
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`); await openThreadBrief(page);
    await expect(page.locator('[data-pm-brief-section]')).toHaveCount(5);
    await expect(page.getByRole('button',{name:zh ? '儲存報告' : 'Save report',exact:true})).toBeHidden();
    await expect(page.locator('[data-pm-action="pm.snapshot.create"]')).toBeHidden();
    if (width < 1200) await page.keyboard.press('Escape');
    await expect(page.getByRole('button',{name:zh ? '只存筆記' : 'Save note only',exact:true})).toBeEnabled();
    expect(f.discussion.writes).toEqual([]); await context.close();
  });

  for (const width of [1440, 390]) test(`empty discussion and tools have short titles and muted help ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL,locale,viewport:{width,height:width === 390 ? 844 : 900}}), page = await context.newPage();
    const f = demoFixture({locale,discussions:true}), detail = f.discussion.details.get(f.ids.threadIdea);
    detail.messages=[]; detail.inputs=[];
    await mountConversation(page,false,async input => {
      if (input.path.endsWith('/snapshots/latest')) return {status:200,data:{workspace_id:f.ids.workspaceId,snapshot:null}};
      if (input.path.endsWith('/snapshots?limit=20')) return {status:200,data:{workspace_id:f.ids.workspaceId,snapshots:[],next_cursor:null}};
      return f.dispatch(input);
    });
    const title=zh ? '尚無訊息' : 'No messages yet';
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
    await expect(page.locator('[data-discussion-composer]')).toBeEnabled();
    await emptyCopy(page.locator('.pm-notes > .ui-empty'),title,zh ? '先記下問題、一個選項，或待釐清的疑問。' : 'Record the problem, an option, or a question to revisit.');
    await openThreadTab(page,'context'); await emptyCopy(page.locator('.pd-context-details > .ui-empty'),zh ? '尚未連結脈絡' : 'No bound context',zh ? '從專案紀錄開啟討論，即可帶入脈絡。' : 'Open a discussion from a project record to add context.');
    await openThreadTab(page,'inputs'); await emptyCopy(page.locator('.pm-facts > .ui-empty'),zh ? '尚無資訊' : 'No inputs yet',zh ? '加入有來源的陳述，或這段討論中的疑問。' : 'Add a sourced statement or a question from this discussion.');
    await openThreadBrief(page); await emptyCopy(page.locator('.pm-brief-view > .ui-empty'),zh ? '尚無報告' : 'No reports yet',zh ? '儲存的報告會顯示在這裡。' : 'Saved reports will appear here.');
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions`);
    await page.getByRole('searchbox',{name:zh ? '搜尋' : 'Search',exact:true}).fill('2099');
    await emptyCopy(page.locator('[data-discussion-search-empty]'),zh ? '沒有符合的討論' : 'No matching discussions',zh ? '試試其他標題或脈絡名稱。' : 'Try another title or context name.');
    for(const node of [page.locator('[data-discussion-search-empty] h2'),page.locator('[data-discussion-search-empty] p'),page.getByRole('button',{name:zh ? '清除搜尋' : 'Clear search',exact:true})]) await expect(node).toBeInViewport({ratio:1});
    await page.getByRole('button',{name:zh ? '清除搜尋' : 'Clear search',exact:true}).click(); await expect(page.locator('.pm-thread-link:visible')).toHaveCount(3);
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/new`);
    await emptyCopy(page.locator('.pd-transcript > .ui-empty'),title,zh ? '從需要決定的事開始。' : 'Start with what needs a decision.');
    expect(f.discussion.writes).toEqual([]); await context.close();
  });

  for (const width of [1440,390]) test(`an invalid proposal has one review title and muted explanation ${locale} ${width}`,async({browser,baseURL})=>{
    const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage();
    const f=demoFixture({locale,discussions:true});
    await mountConversation(page,false,async input=>{
      const result=await f.dispatch(input);
      if(result.data?.proposal){Object.assign(result.data.proposal,{document:null,document_digest:null,adoptable:false,adoptable_reason:'invalid_proposal',validation:{status:'needs_review',errors:['fixture_validation_detail']}});}
      return result;
    });
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);await page.locator('[data-discussion-ask]').click();
    await expect(page.locator('[data-discussion-panel]')).toBeHidden();
    const card=page.locator('[data-pm-plan-proposals]');
    await emptyCopy(card.locator('.ui-empty'),zh?'需要審閱':'Review needed',zh?'回覆內容需要檢視，目前還不能作為計畫提案。':'The reply needs review before it can become a plan proposal.');
    await expect(card.getByRole('heading')).toHaveCount(1);
    expect(await card.innerText()).not.toContain('fixture_validation_detail');
    await card.getByText(zh?'詳細資訊':'Details',{exact:true}).click();await expect(card).toContainText('fixture_validation_detail');
    expect(f.discussion.writes.map((row:any)=>row.body.action)).toEqual(['pm.conversation.propose']);await context.close();
  });

  for (const width of [1440,390]) test(`empty context packages use a short title and muted help ${locale} ${width}`,async({browser,baseURL})=>{
    const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=pmFixture();
    await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'context');
    await emptyCopy(page.locator('[data-pm-packages] > .ui-empty'),zh?'尚無脈絡紀錄':'No saved context',zh?'為下次執行準備脈絡套件。':'Prepare a context package for the next run.');
    expect(f.state.writes).toEqual([]);await context.close();
  });

  test(`standalone proposal empty state stays localized ${locale}`,async({page})=>{
    const f=proposalFixture();await mountProposal(page,f,locale);
    await emptyCopy(panel(page).locator('.ui-empty'),zh?'尚無提案':'No proposals yet',zh?'這段討論還沒有計畫提案。':'No proposal has been prepared for this discussion.');
    expect(f.state.writes).toEqual([]);
  });
}
