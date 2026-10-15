import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';
const pid = 'prj_' + 'a'.repeat(20), other = 'prj_' + 'b'.repeat(20), wid = 'wi_' + 'a'.repeat(20);
function fixture() {
  const base = conversationFixture();
  const counts = {doing: 0, awaiting_approval: 0, done: 0, total: 0};
  const root = {project_id: pid, name: 'Documentation', description: '', repositories: ['example/docs'],
    children: [], counts, version: 1, archived: false};
  const second = {...root, project_id: other, name: 'Related project'};
  const item = {work_item_id: wid, project_id: pid, title: 'Existing source', children: [], steps: [], version: 1,
    completion: {display_state: 'todo', pending: false}, archived: false};
  const state = {writes: [] as any[], lost: false, manage: true, managed: false, wrongReceipt: false, archived: false, reject: null as null | {status:number,code:string}, ops: new Map<string, any>()};
  const caps = () => ({actor: 'reader', scopes: state.manage ? ['observe', 'manage'] : ['observe'], api_version: 1,
    contract_version: '2026-10-08', hosts: [], actions: [{action:'project.create',allowed:state.manage},{action:'work_item.create',allowed:state.manage}],
    ...(state.managed ? {managed_installation:{runtime_version:'0.2.4',background:true}} : {}),
    features:{repository_sync:[{repository:'example/docs'}]}});
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (path === '/capabilities') return {status:200,data:caps()};
    if (path === '/bootstrap') return {status:200,data:{capabilities:caps(),sync:{version:1,server_id:'create-fixture',principal_id:'person',checkpoint:{cursor:0,token:'create-0'}}}};
    if (path === '/projects') return {status:200,data:{projects:[root,second],archived:[]}};
    if (path.startsWith('/projects/')) return {status:200,data:{project:{...(path.endsWith(other)?second:root),archived:state.archived},path:[],sub_projects:[],work_items:[item],work:[],archived:[]}};
    if (path === '/managed/setup') return {status:200,data:{revision:'a'.repeat(64),hosts:[],repositories:[],profiles:[],busy:false}};
    if (path.startsWith('/operations/') && input.method === 'GET') return {status:200,data:{operation:[...state.ops.values()].find(op=>path.endsWith(op.operation_id))}};
    if (path === '/operations' && input.method === 'POST') {
      state.writes.push(input);
      if (state.reject) return {status:state.reject.status,data:{error:{code:state.reject.code,message:'Creation request rejected'}}};
      const operation = state.ops.get(input.idempotency_key) || {...input.body,actor:'reader',idempotency_key:input.idempotency_key,
        operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'),status:'succeeded',result:{project_id:pid,work_item_id:'wi_'+'b'.repeat(20)}};
      state.ops.set(input.idempotency_key,operation);
      if (state.lost) {state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Original reply unavailable'}}};}
      return {status:200,data:{operation:state.wrongReceipt?{...operation,actor:'another-person'}:operation}};
    }
    return base.dispatch(input);
  };
  return {state,dispatch};
}

test('creation layout', async ({browser, baseURL}, testInfo) => {
  const folder = process.env.CREATE_SCREENSHOTS || testInfo.outputPath('screenshots');
  mkdirSync(folder,{recursive:true});
  for (const locale of ['en','zh-TW']) {
    const context = await browser.newContext({locale, baseURL});
    const page = await context.newPage(), f = fixture(); f.state.managed = true;
    await mountConversation(page,false,f.dispatch);
    for (const width of [390,768,1440]) {
      await page.setViewportSize({width,height:900});
      for (const [name, route] of [['project','projects'],['item','project/'+pid],['locations','settings']]) {
        await page.goto('/dashboard/#/'+route);
        const details = page.locator(name === 'locations' ? '[data-connection-card] > details' : '[data-create-advanced]');
        await details.locator('summary').click();
        await expect(details).toHaveAttribute('open','');
        expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({path:`${folder}/${name}-${locale}-${width}.png`,fullPage:true});
      }
    }
    await context.close();
  }
});

for (const native of [false,true]) test.describe(native ? 'Native create' : 'Browser create',()=>{
  test('advanced project creation keeps exact central relationships and original lost-reply request',async({page})=>{
    const f=fixture(); f.state.lost=true; await mountConversation(page,native,f.dispatch);
    await page.goto('/dashboard/#/projects');
    const form=page.locator('[data-create-record="project"]');
    await form.getByLabel('New project name',{exact:true}).fill('Source review');
    await form.getByLabel('Repository for dispatch (optional)',{exact:true}).selectOption('example/docs');
    await expect(form.getByLabel('Description',{exact:true})).toBeHidden();
    await form.locator('summary').click();
    await form.getByLabel('Description',{exact:true}).fill('Literal <script>source</script>');
    await form.getByLabel('Task Service project name',{exact:true}).fill('review');
    await form.getByLabel('Parent',{exact:true}).selectOption(pid);
    await form.getByLabel('Branched from',{exact:true}).selectOption(other);
    await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect(form).toContainText('Original reply unavailable');
    await expect(form.getByLabel('New project name',{exact:true})).toBeDisabled();
    await page.reload();
    await expect(form.getByLabel('New project name',{exact:true})).toHaveValue('Source review');
    await form.getByRole('button',{name:'Check original creation request',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
    expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[0].body).toEqual({action:'project.create',target:{},params:{name:'Source review',repositories:['example/docs'],
      description:'Literal <script>source</script>',task_project:'review',parent_id:pid,derived_from:other},preconditions:{}});
  });

  test('work item is created in the displayed project with detailed request and unchecked steps',async({page})=>{
    const f=fixture(); await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/project/'+pid);
    const form=page.locator('[data-create-record="work_item"]');
    await expect(page.getByRole('heading',{name:'Documentation',exact:true})).toBeVisible();
    await expect(form).not.toContainText(pid);
    await form.getByLabel('New work item',{exact:true}).fill('Review changed files');
    await form.locator('summary').click();
    await form.getByLabel('Goal',{exact:true}).fill('Keep the requested behavior');
    await form.getByLabel('Request (verbatim)',{exact:true}).fill('Preserve this exact request.');
    await form.getByLabel('Acceptance',{exact:true}).fill('Focused checks pass.');
    await form.getByLabel('Steps (one per line)',{exact:true}).fill('Read source\nRun checks');
    await form.getByLabel('State',{exact:true}).selectOption('waiting');
    await form.getByLabel('Parent',{exact:true}).selectOption(wid);
    await form.getByLabel('Branched from',{exact:true}).selectOption(wid);
    await page.reload(); await form.locator('summary').click();
    await expect(form.getByLabel('Request (verbatim)',{exact:true})).toHaveValue('Preserve this exact request.');
    await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body).toEqual({action:'work_item.create',target:{project_id:pid},params:{title:'Review changed files',
      goal:'Keep the requested behavior',request:'Preserve this exact request.',acceptance:'Focused checks pass.',
      steps:[{text:'Read source',done:false},{text:'Run checks',done:false}],state:'waiting',parent_id:wid,derived_from:wid},preconditions:{}});
    await expect(form.getByLabel('New work item',{exact:true})).toHaveValue('');
    await expect(form.getByRole('button',{name:'Add',exact:true})).toBeEnabled();
  });

  test('unavailable draft storage prevents a creation request',async({page})=>{
    const f=fixture(); await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects');
    const form=page.locator('[data-create-record="project"]');
    await form.getByLabel('New project name',{exact:true}).fill('Never sent');
    await page.evaluate(()=>{const set=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key.startsWith('batc.create.'))throw Error('Draft storage unavailable');return set.call(this,key,value);};});
    await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect(form).toContainText('Draft storage unavailable'); expect(f.state.writes).toHaveLength(0);
  });

  test('observe scope and archived projects keep creation disabled',async({page})=>{
    const f=fixture(); f.state.manage=false; await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects');
    const project=page.locator('[data-create-record="project"]');
    await expect(project.getByLabel('New project name',{exact:true})).toBeDisabled();
    await expect(project.getByRole('button',{name:'Add',exact:true,includeHidden:true})).toBeDisabled();
    await expect(project).toBeHidden();
    f.state.manage=true; f.state.archived=true; await page.reload(); await page.goto('/dashboard/#/project/'+pid);
    const item=page.locator('[data-create-record="work_item"]');
    await expect(item.getByLabel('New work item',{exact:true})).toBeDisabled();
    await expect(item.getByRole('button',{name:'Add',exact:true,includeHidden:true})).toBeDisabled();
    await expect(item).toBeHidden();
    expect(f.state.writes).toHaveLength(0);
  });

  test('a foreign receipt retains the draft and frozen request until its original receipt is verified',async({page})=>{
    const f=fixture(); f.state.wrongReceipt=true; await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects');
    const form=page.locator('[data-create-record="project"]');
    await form.getByLabel('New project name',{exact:true}).fill('Original work');
    await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect(form).toContainText('The receipt does not match the original creation request');
    await expect(form.getByLabel('New project name',{exact:true})).toHaveValue('Original work');
    await expect(form.getByLabel('New project name',{exact:true})).toBeDisabled();
    f.state.wrongReceipt=false;
    await form.getByRole('button',{name:'Check original creation request',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
    await expect(page).toHaveURL(new RegExp('#/project/'+pid+'$'));
  });

  for (const uncertain of ['lost reply','foreign receipt']) for (const status of [401,403,422])
    test(`${uncertain} followed by ${status} retains the original creation across reload`,async({page})=>{
      const f=fixture(); f.state.lost=uncertain==='lost reply'; f.state.wrongReceipt=uncertain==='foreign receipt';
      await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects');
      const form=page.locator('[data-create-record="project"]');
      await form.getByLabel('New project name',{exact:true}).fill('Only one creation');
      await form.getByRole('button',{name:'Add',exact:true}).click();
      await expect(form).toContainText(uncertain==='lost reply'?'Original reply unavailable':'The receipt does not match');
      expect(f.state.ops.size).toBe(1);
      if (status===401 && uncertain==='lost reply') await page.evaluate(()=>{
        // The previous client could persist this flag after an ambiguous replay.
        const key=Object.keys(localStorage).find(key=>key.startsWith('batc.create.'))!;
        const value=JSON.parse(localStorage.getItem(key)!); value.intent.refused=true;
        localStorage.setItem(key,JSON.stringify(value));
      });
      await page.reload();
      f.state.reject={status,code:status===401?'UNAUTHORIZED':status===403?'FORBIDDEN':'INVALID_PARAMS'};
      await form.getByRole('button',{name:'Check original creation request',exact:true}).click();
      await expect.poll(()=>f.state.writes.length).toBe(2);
      await expect(form.getByRole('button',{name:'Edit a new creation request',exact:true})).toBeHidden();
      await expect(form.getByRole('button',{name:'Check original creation request',exact:true})).toBeEnabled();
      await page.reload();
      await expect(form.getByLabel('New project name',{exact:true})).toBeDisabled();
      await expect(form.getByRole('button',{name:'Edit a new creation request',exact:true})).toBeHidden();
      f.state.reject=null; f.state.wrongReceipt=false;
      await form.getByRole('button',{name:'Check original creation request',exact:true}).click();
      await expect(page).toHaveURL(new RegExp('#/project/'+pid+'$'));
      expect(f.state.writes).toHaveLength(3); expect(f.state.ops.size).toBe(1);
      for (const request of f.state.writes.slice(1)) {
        expect(request.idempotency_key).toBe(f.state.writes[0].idempotency_key);
        expect(request.body).toEqual(f.state.writes[0].body);
      }
    });

  test('proven first-attempt validation refusal allows reviewing a new creation request',async({page})=>{
    const f=fixture(); f.state.reject={status:422,code:'INVALID_PARAMS'};
    await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects');
    const form=page.locator('[data-create-record="project"]');
    await form.getByLabel('New project name',{exact:true}).fill('Invalid original');
    await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect(form.getByRole('button',{name:'Edit a new creation request',exact:true})).toBeVisible();
    expect(f.state.ops.size).toBe(0);
    await page.reload();
    await form.getByRole('button',{name:'Edit a new creation request',exact:true}).click();
    await form.getByLabel('New project name',{exact:true}).fill('Corrected work');
    f.state.reject=null; await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect(page).toHaveURL(new RegExp('#/project/'+pid+'$'));
    expect(f.state.ops.size).toBe(1);
    expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
  });

  test('managed location explanation separates central data and selected BAT workspaces',async({page})=>{
    const f=fixture(); f.state.managed=true; await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/settings');
    await expect(page.locator('[data-managed-locations]')).toContainText('Connector stores your identity, operation history and artifacts');
    await expect(page.locator('[data-managed-locations]')).toContainText('selected BAT host and workspace');
    expect(f.state.writes).toHaveLength(0);
  });
});
