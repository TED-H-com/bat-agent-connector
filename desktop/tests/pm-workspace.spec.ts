import {openThreadTab, openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture,pmRoute,pmPath,workspaceId,threadId,inputA,inputB} from './pm-fixture.ts';
const action=(page:any,name:string)=>page.locator(`[data-pm-action="${name}"]`);
for(const native of [false,true])test.describe(native?'Native PM':'Web PM',()=>{
  test('context preparation keeps original sources and recovers the original request',async({page})=>{
    const f=pmFixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
    await openThreadTab(page,'context');await page.getByText('Prepare discussion context',{exact:true}).click();
    const prepare=action(page,'pm.run.prepare');
    await prepare.getByLabel('What should this discussion help decide?',{exact:true}).fill('Compare the first visit with a guided tour');
    f.state.lost=true;await prepare.getByRole('button',{name:'Save context package',exact:true}).click();
    await expect(prepare).toContainText('Reply unavailable');
    f.state.messages[0].content='A later note does not change the saved context.';
    await page.reload();await openThreadTab(page,'context');await page.getByText('Prepare discussion context',{exact:true}).click();
    await prepare.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(prepare.getByLabel('What should this discussion help decide?',{exact:true})).toHaveValue('');
    await page.locator('[data-pm-run] summary').click();
    await expect(page.locator('[data-pm-run]')).toContainText('Visitors should find their first useful action');
    await expect(page.locator('[data-pm-run]')).toContainText('Unverified assumption');
    await expect(page.locator('[data-pm-run]')).not.toContainText('A later note');
    expect(f.state.runs).toHaveLength(1);expect(f.state.messages).toHaveLength(1);
    expect(f.state.writes.map(input=>input.body.action)).toEqual(['pm.run.prepare','pm.run.prepare']);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
    expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
  });
  test('older centrals do not expose context preparation',async({page})=>{
    const f=pmFixture();f.state.packages=false;await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
    await expect(page.getByText('Prepare discussion context',{exact:true})).toHaveCount(0);
    expect(f.state.writes).toHaveLength(0);
  });
  test('workspace and Idea creation use explicit central records',async({page})=>{
    const f=pmFixture();await mountConversation(page,native,f.dispatch);await page.goto('/dashboard/#/pm');
    await page.getByRole('button',{name:'New workspace',exact:true}).click();
    await page.getByLabel('Workspace name',{exact:true}).fill('Review space');
    await page.getByText('Repositories (optional)',{exact:true}).click();
    await page.getByLabel('Repositories, separated by commas',{exact:true}).fill('example/library, example/docs');
    await page.getByRole('button',{name:'Create workspace',exact:true}).click();
    await expect(page).toHaveURL(new RegExp('#/pm/ws_'+'5'.repeat(32)+'/discussions$'));
    expect(f.state.writes[0].body).toEqual({action:'pm.workspace.create',target:{},params:{name:'Review space',repositories:['example/library','example/docs']},preconditions:{}});
    await page.goto(`/dashboard/#/pm/${workspaceId}/discussions`);
    await page.getByLabel('Discussion title',{exact:true}).fill('Try a focused first screen');
    await page.getByRole('button',{name:'Start discussion',exact:true}).click();
    await expect(page).toHaveURL(new RegExp('#/pm/'+workspaceId+'/discussions/'+threadId+'$'));
    expect(f.state.writes[1].body).toEqual({action:'pm.thread.create',target:{workspace_id:workspaceId},params:{title:'Try a focused first screen'},preconditions:{expected_version:1}});
  });
  for(const original of [false,true])test(`initial section reads preserve the ${original?'original uncertain intent':'saved draft'} until submission is available`,async({page})=>{
    const f=pmFixture();let hold=false,held=false,release:()=>void=()=>{};
    const gate=new Promise<void>(resolve=>release=resolve);
    const dispatch=async(input:any)=>{
      const path=new URL(input.path,'http://fixture').pathname;
      const result=await f.dispatch(input);
      if(path==='/capabilities')result.data.features.pm_workspaces.brief_snapshots=true;
      if(path==='/bootstrap')result.data.capabilities.features.pm_workspaces.brief_snapshots=true;
      if(path===`/pm/workspaces/${workspaceId}/snapshots/latest`){
        if(hold){held=true;await gate;}
        return {status:200,data:{version:1,workspace_id:workspaceId,snapshot:null}};
      }
      if(path===`/pm/workspaces/${workspaceId}/snapshots`)return {status:200,data:{version:1,workspace_id:workspaceId,snapshots:[],next_cursor:null}};
      if(path===`/pm/workspaces/${workspaceId}/plans/head`)return {status:200,data:{head:structuredClone(f.state.planHead)}};
      return result;
    };
    await mountConversation(page,native,dispatch);await page.goto(`/dashboard/#/pm/${workspaceId}/discussions`);
    const form=action(page,'pm.thread.create'),field=form.getByLabel('Discussion title',{exact:true});
    await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
    await field.fill('Retain the original discussion draft');
    if(original){f.state.lost=true;await form.getByRole('button',{name:'Start discussion',exact:true}).click();await expect(form).toContainText('Reply unavailable');}
    const expectedWrites=original?1:0,label=original?'Check original request':'Start discussion';
    const saved=()=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.pm.thread.create.')).map(([,value])=>JSON.parse(value)));
    // The snapshot belongs to Overview. Keep the discussion draft, then hold
    // the visible Overview read on reload instead of relying on a hidden read.
    await openPmSection(page,'overview');
    const before=await saved();hold=true;await page.reload();
    try {
      await expect.poll(()=>held).toBe(true);
      await expect(field).toHaveValue('Retain the original discussion draft');
      await expect(form.getByRole('button',{name:label,exact:true,includeHidden:true})).toBeDisabled();
      await expect(field).toBeDisabled();
      // Even an explicit form event while the initial read is pending must not
      // freeze an unsent operation or consume a new original key.
      await form.evaluate((node:HTMLFormElement)=>node.requestSubmit());
      expect(f.state.writes).toHaveLength(expectedWrites);
      expect(await saved()).toEqual(before);
    } finally {release();}
    await expect(form.getByRole('button',{name:label,exact:true,includeHidden:true})).toBeEnabled();
    await expect(field).toHaveValue('Retain the original discussion draft');
    await openPmSection(page,'discussions');
    await form.getByRole('button',{name:label,exact:true}).click();
    await expect(page).toHaveURL(new RegExp('#/pm/'+workspaceId+'/discussions/'+threadId+'$'));
    expect(f.state.writes).toHaveLength(expectedWrites+1);
    expect(new Set(f.state.writes.map(input=>input.idempotency_key)).size).toBe(1);
    if(original)expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[0].body.params).toEqual({title:'Retain the original discussion draft'});
  });
  test('saved notes reload without inventing an AI response or dispatch',async({page})=>{
    const f=pmFixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
    const note=action(page,'pm.message.append');
    await note.getByLabel('Message the PM…',{exact:true}).fill('Keep literal <script>notes</script> and compare two options.');
    await page.reload();await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('Keep literal <script>notes</script> and compare two options.');
    await note.getByRole('button',{name:'Save note only',exact:true}).click();
    await expect(page.locator('.pm-notes')).toContainText('Keep literal <script>notes</script>');
    await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('');
    await page.reload();await expect(page.locator('.pm-notes')).toContainText('Keep literal <script>notes</script>');
    await expect(page.getByText('PM analysis is unavailable in this Connector; install a compatible version',{exact:true})).toBeVisible();
    await expect(page.locator('.pm-note time').first()).toHaveAttribute('datetime','2026-10-11T12:00:00.000Z');
    expect(f.state.writes.map(input=>input.body.action)).toEqual(['pm.message.append']);
    expect(await page.locator('.pm-notes script').count()).toBe(0);
  });
  test('source claims stay unverified; owner selection preserves candidates and does not adopt',async({page})=>{
    const f=pmFixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
    await openThreadTab(page,'inputs');await page.locator('[data-pm-add-input] summary').click();const form=action(page,'pm.input.record');
    await form.getByLabel('Topic',{exact:true}).selectOption('unknowns');
    await form.getByLabel('Statement or question',{exact:true}).fill('The existing layout has three primary actions.');
    await form.getByLabel('Evidence status',{exact:true}).selectOption('repo_evidence');
    await form.getByLabel('Source type or saved note',{exact:true}).selectOption('repository');
    await form.getByLabel('Source reference',{exact:true}).fill('example/library:src/home.js');
    await page.reload();await openThreadTab(page,'inputs');await page.locator('[data-pm-add-input] summary').click();
    await expect(form.getByLabel('Topic',{exact:true})).toHaveValue('unknowns');
    await expect(form.getByLabel('Source type or saved note',{exact:true})).toHaveValue('repository');
    await form.getByRole('button',{name:'Save input',exact:true}).click();
    await expect(page.locator('.pm-facts')).toContainText('example/library:src/home.js');
    const caveat = page.locator('[data-pm-information] > details.ui-disclosure').filter({hasText:'Not independently verified'});
    await caveat.locator(':scope > summary').click(); await expect(caveat).toContainText('Not independently verified');
    await openThreadTab(page,'inputs');await page.locator(`[data-input-id="${inputA}"] input[type=checkbox]`).check();
    await openThreadTab(page,'plan');await page.locator('[data-pm-plan] > summary').click();const plan=action(page,'pm.thread.plan');await plan.getByLabel('Why this direction',{exact:true}).fill('Keep the first action visible; compare the tour later.');
    await page.reload();await expect(page.locator(`[data-input-id="${inputA}"] input[type=checkbox]`)).toBeChecked();
    await openThreadTab(page,'plan');if(!await page.locator('[data-pm-plan]').evaluate(node=>(node as HTMLDetailsElement).open))await page.locator('[data-pm-plan] > summary').click();await plan.getByRole('button',{name:'Save Planning draft',exact:true}).click();
    await expect(page.locator('.pm-draft')).toContainText('Keep the first useful action visible');
    await expect(page.locator('.pm-draft')).toContainText('Offer a guided tour as another option');
    await expect(page.locator('.pm-draft')).toContainText('Requirements have not been adopted');
    await expect(page.locator('.pm-draft')).toContainText('Finance');
    expect(f.state.writes.map(input=>input.body.action)).toEqual(['pm.input.record','pm.thread.plan']);
    expect(f.state.writes[1].body.params.selected_input_ids).toEqual([inputA]);
    expect(f.state.inputs.some(item=>item.input_id===inputB)).toBe(true);
  });
  test('lost reply then auth rejection keeps exact key across reload and receipt recovery',async({page})=>{
    const f=pmFixture();f.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
    const note=action(page,'pm.message.append');await note.getByLabel('Message the PM…',{exact:true}).fill('Save once only');
    await note.getByRole('button',{name:'Save note only',exact:true}).click();await expect(note).toContainText('Reply unavailable');
    f.state.reject={status:403,code:'FORBIDDEN'};await note.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(2);await expect(note.getByRole('button',{name:'New request',exact:true})).toBeHidden();
    await page.reload();await expect(note.getByLabel('Message the PM…',{exact:true})).toBeDisabled();f.state.reject=null;
    await note.getByRole('button',{name:'Check original request',exact:true}).click();await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('');
    expect(f.state.messages.filter(message=>message.content==='Save once only')).toHaveLength(1);
    expect(f.state.writes).toHaveLength(3);for(const request of f.state.writes.slice(1)){expect(request.idempotency_key).toBe(f.state.writes[0].idempotency_key);expect(request.body).toEqual(f.state.writes[0].body);}
  });
  test('stale admission retains text and read failure freezes mutation until refresh',async({page})=>{
    const f=pmFixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);const note=action(page,'pm.message.append');
    await note.getByLabel('Message the PM…',{exact:true}).fill('Keep this draft');f.state.reject={status:409,code:'VERSION_CONFLICT'};
    await note.getByRole('button',{name:'Save note only',exact:true}).click();await expect(note).toContainText('Could not confirm the current version. Your draft is preserved. Refresh and review before another change.');
    await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('Keep this draft');
    await note.getByRole('button',{name:'New request',exact:true}).click();f.state.reject=null;f.state.thread.version=9;
    f.state.failRead=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(page.locator('.pm-status')).toContainText('PM read unavailable');
    await expect(note).toBeHidden();await expect(note.locator('button[type=submit]')).toBeDisabled();await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('Keep this draft');
    f.state.failRead=false;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(note.getByRole('button',{name:'Save note only',exact:true})).toBeEnabled();
    await note.getByRole('button',{name:'Save note only',exact:true}).click();await expect(page.locator('.pm-notes')).toContainText('Keep this draft');expect(f.state.writes[1].body.preconditions).toEqual({expected_version:9});
  });
  test('readers cannot edit; contributors cannot confirm or select a direction',async({page})=>{
    const f=pmFixture();f.state.role='reader';await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
    await expect(action(page,'pm.message.append').getByRole('button',{name:'Save note only',exact:true})).toBeDisabled();
    await expect(page.locator('[data-pm-plan]')).toBeHidden();await expect(page.getByText('Review and confirm',{exact:true})).toHaveCount(0);
    f.state.role='contributor';await page.reload();await expect(action(page,'pm.message.append').getByRole('button',{name:'Save note only',exact:true})).toBeEnabled();
    await expect(page.locator('[data-pm-plan]')).toBeHidden();await expect(page.getByText('Review and confirm',{exact:true})).toHaveCount(0);
    expect(f.state.writes).toHaveLength(0);
  });
  test('connection identity isolates drafts and late receipts cannot navigate or clear the original',async({page})=>{
    const f=pmFixture();let resolve:()=>void=()=>{};f.state.defer=new Promise<void>(r=>resolve=r);
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);const note=action(page,'pm.message.append');
    await note.getByLabel('Message the PM…',{exact:true}).fill('Original account note');await note.getByRole('button',{name:'Save note only',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);await page.goto('/dashboard/#/settings');
    await page.getByRole('button',{name:'Disconnect',exact:true}).click();resolve();
    await expect(page).toHaveURL(/#\/settings$/);expect(f.state.writes).toHaveLength(1);
    f.state.principal='person-2';f.state.defer=null;await page.reload();await page.goto(pmRoute);
    await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('');
    f.state.principal='person-1';f.state.actor='reader';await page.reload();await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('Original account note');
    await expect(note.getByRole('button',{name:'Check original request',exact:true})).toBeVisible();
  });
  test('a lost Planning receipt remains recoverable after the mode already changed',async({page})=>{
    const f=pmFixture();f.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
    await openThreadTab(page,'inputs');await page.locator(`[data-input-id="${inputA}"] input[type=checkbox]`).check();await openThreadTab(page,'plan');await page.locator('[data-pm-plan] > summary').click();const plan=action(page,'pm.thread.plan');
    await plan.getByLabel('Why this direction',{exact:true}).fill('Keep one reviewed direction');
    await openThreadTab(page,'plan');if(!await page.locator('[data-pm-plan]').evaluate(node=>(node as HTMLDetailsElement).open))await page.locator('[data-pm-plan] > summary').click();await plan.getByRole('button',{name:'Save Planning draft',exact:true}).click();await expect(plan).toContainText('Reply unavailable');
    await page.reload();await openThreadTab(page,'plan');await expect(page.locator('.pm-draft')).toContainText('Keep one reviewed direction');
    await openThreadTab(page,'plan');if(!await page.locator('[data-pm-plan]').evaluate(node=>(node as HTMLDetailsElement).open))await page.locator('[data-pm-plan] > summary').click();await plan.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(plan).toBeHidden();expect(f.state.ops.size).toBe(1);expect(f.state.writes).toHaveLength(2);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
  });
  test('foreign receipts keep the original draft and unavailable storage never sends',async({page})=>{
    const f=pmFixture();f.state.mismatch=true;await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);const note=action(page,'pm.message.append');
    await note.getByLabel('Message the PM…',{exact:true}).fill('Identity-bound note');
    await page.evaluate(()=>{const original=Storage.prototype.setItem;(window as any).restorePmStorage=()=>Storage.prototype.setItem=original;Storage.prototype.setItem=function(key,value){if(key.startsWith('batc.pm.'))throw Error('Draft storage unavailable');return original.call(this,key,value);};});
    await note.getByRole('button',{name:'Save note only',exact:true}).click();await expect(note).toContainText('Draft storage unavailable');
    await note.getByRole('button',{name:'Check original request',exact:true}).click();expect(f.state.writes).toHaveLength(0);
    await page.evaluate(()=>(window as any).restorePmStorage());await note.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(note).toContainText('The receipt does not match');await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('Identity-bound note');
    f.state.mismatch=false;await page.reload();await note.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(note.getByLabel('Message the PM…',{exact:true})).toHaveValue('');expect(f.state.ops.size).toBe(1);
  });
  test('owner confirmation and event refresh preserve an in-progress note',async({page})=>{
    const f=pmFixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);const note=action(page,'pm.message.append');
    const field=note.getByLabel('Message the PM…',{exact:true});await field.fill('An unfinished comparison');
    await openThreadTab(page,'inputs');await page.locator(`[data-input-id="${inputB}"] input[type=checkbox]`).check();
    await openThreadTab(page,'inputs');await page.locator(`[data-input-id="${inputB}"] details:has([data-pm-action="pm.input.confirm"]) > summary`).click();const confirm=action(page,'pm.input.confirm');
    await confirm.getByLabel('Why this statement is confirmed',{exact:true}).fill('Reviewed with the responsible person');
    await confirm.getByRole('button',{name:'Confirm statement',exact:true}).click();
    await expect(page.locator('[data-input-id="pmi_'+ 'c'.repeat(32)+'"]')).toContainText('User confirmed');
    await expect(page.locator('[data-pm-missing-selection]')).toBeVisible();
    await page.getByRole('checkbox',{name:'Selected input outside this view',exact:true}).click();
    await expect(page.locator('[data-pm-missing-selection]')).toHaveCount(0);
    await field.focus();f.state.messages.push({message_id:'pmm_'+'7'.repeat(32),sequence:2,actor:'teammate',content:'Another saved contribution',created_at:Date.parse('2026-10-11T12:01:00Z')/1000});f.state.cursor++;
    await expect(page.locator('.pm-notes')).toContainText('Another saved contribution');await expect(field).toHaveValue('An unfinished comparison');await expect(field).toBeFocused();
    expect(f.state.writes.map(input=>input.body.action)).toEqual(['pm.input.confirm']);
  });
  test('bounded notes and discussions can load earlier pages without losing local text',async({page})=>{
    const f=pmFixture();const seen:string[]=[];const other='pmt_'+'9'.repeat(32);f.state.messages[0].sequence=50;
    const dispatch=async(input:any)=>{
      const url=new URL(input.path,'http://fixture');
      if(url.pathname===pmPath){seen.push(input.path);const data=f.detail();
        if(url.searchParams.has('before_message'))return {status:200,data:{...data,messages:[{message_id:'pmm_'+'8'.repeat(32),sequence:1,actor:'teammate',content:'Earlier source note',created_at:'2026-10-10T12:00:00Z'}]}};
        return {status:200,data:{...data,next_message_cursor:50,coverage:{...data.coverage,messages_truncated:true}}};
      }
      if(url.pathname===`/pm/workspaces/${workspaceId}/threads`){seen.push(input.path);return {status:200,data:{version:1,workspace_id:workspaceId,threads:url.searchParams.has('after')?[{...f.state.thread,thread_id:other,title:'Another retained idea'}]:[f.state.thread],next_cursor:url.searchParams.has('after')?null:threadId}};}
      return f.dispatch(input);
    };
    await mountConversation(page,native,dispatch);await page.goto(pmRoute);const field=action(page,'pm.message.append').getByLabel('Message the PM…',{exact:true});
    await field.fill('Draft beside older evidence');await page.getByRole('button',{name:'Load earlier notes',exact:true}).click();
    await expect(page.locator('.pm-notes')).toContainText('Earlier source note');await expect(page.locator('.pm-notes')).toContainText('Visitors should find');
    await page.getByRole('button',{name:'Load more discussions',exact:true}).click();await expect(page.getByRole('link',{name:/Another retained idea Idea/})).toBeVisible();
    await expect(field).toHaveValue('Draft beside older evidence');await expect(page.locator('.pm-notes')).toContainText('Earlier source note');
    expect(seen.some(path=>path.endsWith('?before_message=50'))).toBe(true);expect(seen.some(path=>path.endsWith('?after='+threadId))).toBe(true);expect(f.state.writes).toHaveLength(0);
  });
  test('an event arriving during an older read still refreshes before its checkpoint',async({page})=>{
    const f=pmFixture();let hold=false, held=false, release:()=>void=()=>{};const gate=new Promise<void>(resolve=>release=resolve);
    const dispatch=async(input:any)=>{const result=await f.dispatch(input);if(input.path===pmPath&&hold){hold=false;held=true;await gate;}return result;};
    await mountConversation(page,native,dispatch);await page.goto(pmRoute);await expect(page.locator('.pm-notes')).toContainText('Visitors should find');
    hold=true;await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect.poll(()=>held).toBe(true);
    f.state.messages.push({message_id:'pmm_'+'9'.repeat(32),sequence:2,actor:'teammate',content:'Newer than the pending read',created_at:Date.parse('2026-10-11T12:02:00Z')/1000});f.state.cursor++;
    try{await expect(page.locator('.pm-notes')).toContainText('Newer than the pending read');}finally{release();}
    await expect(page.locator('.pm-notes')).toContainText('Newer than the pending read');expect(f.state.writes).toHaveLength(0);
  });
  test('missing PM capability offers no entry or mutation',async({page})=>{
    const f=pmFixture();f.state.available=false;await mountConversation(page,native,f.dispatch);await page.goto('/dashboard/#/pm');
    await expect(page.locator('#nav a[href="#/pm"]')).toHaveCount(0);await expect(page.locator('#main')).toContainText('does not provide PM workspaces');
    expect(await page.locator('[data-pm-action]').count()).toBe(0);expect(f.state.writes).toHaveLength(0);
  });
});

test('PM shared layout in both languages and desktop, tablet, mobile sizes',async({browser,baseURL},testInfo)=>{
  const folder=process.env.PM_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  for(const locale of ['en','zh-TW']){
    const context=await browser.newContext({locale,baseURL});const page=await context.newPage();const f=pmFixture();await mountConversation(page,false,f.dispatch);
    for(const [width,height]of [[1440,900],[768,900],[390,844]]){
      await page.setViewportSize({width,height});await page.goto(pmRoute);await expect(page.locator('.pm-notes')).toContainText('Visitors should find');
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:`${folder}/discussion-${locale}-${width}.png`,fullPage:true});
    }await context.close();
  }
});
