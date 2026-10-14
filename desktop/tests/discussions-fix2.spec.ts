import {test,expect} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {openThreadTab} from './pm-navigation.ts';
import {pmFixture,pmRoute} from './pm-fixture.ts';
import {meetingFixture,mount as mountMeetings} from './pm-meetings-fixture.ts';
import {mkdir} from 'node:fs/promises';

for(const locale of ['en','zh-TW']){
  const zh=locale==='zh-TW';
  for(const width of [1440,1000,390])test(`excluded scope is named before review ${locale} ${width}`,async({browser,baseURL})=>{
    const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=demoFixture({locale,discussions:true});
    const text=zh?'推薦獎勵不在本輪範圍。':'Referral rewards are outside this release.';
    await mountConversation(page,false,async input=>{
      const result=await f.dispatch(input);
      if(result.data?.proposal?.document)result.data.proposal.document.candidates.push({key:'referral',kind:'excluded',text,source_refs:['note.owner']});
      return result;
    });
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);await page.locator('[data-discussion-ask]').click();await expect(page.locator('[data-proposal-review]')).toBeEnabled();
    if(width<1200)await page.keyboard.press('Escape');
    const card=page.locator('[data-pm-plan-proposals]'),excluded=card.locator('[data-proposal-exclusion=referral]');
    await expect(excluded).toBeVisible();await expect(excluded).toContainText(text);await expect(excluded).toContainText(zh?'本輪不做':'Out of scope');
    await expect(card.locator('[data-proposal-adopt-panel]')).toBeHidden();await expect(page.locator('.ui-button.primary:visible')).toHaveText(zh?'審閱並採用':'Review & adopt');
    await card.locator('[data-proposal-review]').click();const row=card.locator('[data-proposal-candidate=referral]').locator('..');
    await expect(row).toContainText(text);await expect(row.getByRole('checkbox')).toBeVisible();await expect(row.getByRole('checkbox')).not.toBeChecked();
    await expect(page.locator('.ui-button.primary:visible')).toHaveText(zh?'採用':'Adopt');
    expect(f.discussion.writes.map((row:any)=>row.body.action)).toEqual(['pm.conversation.propose']);await context.close();
  });

  for(const width of [390,799,800,1000,1440])test(`the list and draft follow their routes ${locale} ${width}`,async({browser,baseURL})=>{
    const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=demoFixture({locale,discussions:true});await mountConversation(page,false,f.dispatch);
    const root=`/dashboard/#/pm/${f.ids.workspaceId}/discussions`;await page.goto(root);await expect(page.locator('.pm-thread-link')).toHaveCount(3);
    await expect(page.getByRole('searchbox',{name:zh?'搜尋':'Search',exact:true})).toBeVisible();
    if(width<800){await expect(page.locator('.pd-new-layout')).toBeHidden();await expect(page.locator('[data-discussion-back]')).toBeHidden();for(const row of await page.locator('.pm-thread-link').all())await expect(row).toBeInViewport({ratio:1});}
    else{await expect(page.locator('.pd-new-layout')).toBeVisible();const list=(await page.locator('.pm-workspace-list').boundingBox())!,draft=(await page.locator('.pd-new-layout').boundingBox())!;expect(draft.x).toBeGreaterThanOrEqual(list.x+list.width-1);}
    await page.locator('[data-discussion-new]').click();await expect(page).toHaveURL(/\/discussions\/new$/);const composer=page.locator('[data-discussion-composer]');await expect(composer).toBeFocused();
    const draft=zh?'保留這段討論草稿。':'Keep this discussion draft.';await composer.fill(draft);
    if(width<800){await expect(page.locator('.pm-workspace-list')).toBeHidden();await page.locator('[data-discussion-back]').click();await expect(page).toHaveURL(/\/discussions$/);await expect(page.locator('.pd-new-layout')).toBeHidden();await page.locator('[data-discussion-new]').click();}
    else await page.reload();
    await expect(composer).toHaveValue(draft);await expect(composer).toBeFocused();expect(f.discussion.writes).toEqual([]);await context.close();
  });

  for(const role of ['owner','reader'])for(const width of [1440,1000,390])test(`an empty Meeting tab for ${role} has one sentence and first-row refresh ${locale} ${width}`,async({browser,baseURL})=>{
    const context=await browser.newContext({baseURL,locale,viewport:{width,height:width===390?844:900}}),page=await context.newPage(),f=demoFixture({locale,discussions:true}),reads:string[]=[];
    await mountConversation(page,false,async input=>{
      if(input.method==='GET')reads.push(input.path);
      if(input.path.includes('/meetings'))return {status:200,data:{workspace_id:f.ids.workspaceId,meetings:[],next_cursor:null}};
      if(input.path.includes('/sprints'))return {status:200,data:{workspace_id:f.ids.workspaceId,sprints:[],next_cursor:null}};
      const result=await f.dispatch(input),caps=input.path==='/bootstrap'?result.data.capabilities:input.path==='/capabilities'?result.data:null;
      if(caps){caps.features.pm_workspaces.meetings=true;if(role==='reader')caps.scopes=['observe'];}
      if(result.data?.workspace)result.data.workspace.membership.role=role;
      if(result.data?.workspaces)for(const workspace of result.data.workspaces)workspace.membership.role=role;
      if(result.data?.thread){result.data.thread.mode='sprint_review';if(role==='reader')Object.assign(result.data.capabilities,{write:false,decide:false});}return result;
    });
    await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);await openThreadTab(page,'context');
    expect(reads.filter(path=>path.includes('/meetings'))).toEqual([]);await page.getByRole('tab',{name:zh?'會議':'Meeting',exact:true}).click();
    const card=page.locator('[data-pm-meetings]');await expect(card.locator('.ui-empty, .ui-section-card')).toHaveCount(0);
    await expect(card.getByRole('heading',{name:zh?'衝刺討論':'Sprint discussions',exact:true})).toBeVisible();
    const empty=role==='owner'?(zh?'請先規劃衝刺，再建立會議。':'Plan a Sprint before creating a meeting.'):(zh?'尚未建立會議紀錄。':'No meeting has been recorded.');
    await expect(card.locator('.pm-note-text:visible')).toHaveText(empty);await expect(card.getByText(empty,{exact:true})).toHaveCount(1);
    if(role==='reader')await expect(card.locator('[data-pm-panel-readonly]')).toBeVisible();else await expect(card.locator('[data-pm-panel-readonly]')).toBeHidden();
    await expect(card.locator('p:visible')).toHaveCount(role==='reader'?2:1);
    const geometry=await card.locator('.pm-heading').evaluate(node=>{
      const title=node.querySelector('h3')!.getBoundingClientRect(),refresh=node.querySelector('.ui-icon-button')!.getBoundingClientRect();
      const reason=node.querySelector('[data-pm-panel-readonly]')!,reasonLines=reason.getBoundingClientRect().height/parseFloat(getComputedStyle(reason).lineHeight);
      return {delta:Math.abs(title.top+title.height/2-refresh.top-refresh.height/2),reasonLines};
    });
    expect(geometry.delta).toBeLessThanOrEqual(4);expect(geometry.reasonLines).toBeLessThanOrEqual(2);
    await expect(card.locator('.pm-heading .ui-icon-button')).toBeVisible();
    await expect(card.getByRole('combobox')).toHaveCount(0);await expect(card.locator('[data-pm-meeting-create]')).toBeHidden();
    expect(reads.filter(path=>path.includes('/meetings')).length).toBe(1);expect(f.discussion.writes).toEqual([]);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    if(process.env.BATC_C4_SHOTS){await mkdir(process.env.BATC_C4_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.BATC_C4_SHOTS}/c4-r2-meetings-thread-empty-${role}-${locale}-${width}.png`});}
    await context.close();
  });

  for(const width of [1440,1000,390])test(`the disconnected composer and info title use plain copy ${locale} ${width}`,async({browser,baseURL})=>{
    const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=pmFixture();await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);
    const note=page.locator('.pd-composer-status > p');await expect(note).toHaveText(zh?'訊息會儲存；PM 尚未連線，不會回覆。 設定 PM':'Messages are saved. The PM is not connected and will not reply. Set up PM');
    await expect(note.getByRole('link',{name:zh?'設定 PM':'Set up PM',exact:true})).toHaveAttribute('href','#/settings?section=agents&from='+encodeURIComponent(new URL(page.url()).hash));
    await openThreadTab(page,'context');await expect(page.locator('.pd-panel-overlay > header h2')).toHaveText(zh?'討論資料':'Discussion info');
    await expect(page.getByRole('tablist',{name:zh?'討論資料':'Discussion info',exact:true})).toBeVisible();expect(f.state.writes).toEqual([]);await context.close();
  });

  test(`meeting choices return when sources exist ${locale}`,async({page})=>{
    const f=meetingFixture(),sprints=structuredClone(f.state.sprints),meetings=structuredClone(f.state.meetings);f.state.sprints=[];f.state.meetings=[];await mountMeetings(page,f,locale);
    const card=page.locator('[data-pm-meetings]');await expect(card.getByRole('combobox')).toHaveCount(0);await expect(card.getByRole('heading')).toHaveText(zh?'衝刺討論':'Sprint discussions');
    f.state.sprints=sprints;f.state.meetings=meetings;await page.evaluate(()=>(window as any).refreshMeetings());
    await expect(card.getByRole('combobox',{name:zh?'會議':'Meeting',exact:true})).toBeEnabled();await expect(card.locator('.ui-empty')).toHaveCount(0);await expect(card.locator('[data-pm-meeting-create]')).toBeVisible();expect(f.state.writes).toEqual([]);
  });
}
