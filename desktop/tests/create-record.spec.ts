import {test, expect, type Page} from '@playwright/test';
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

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`work item form opens on request and retains its draft ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
  try {
    const page = await context.newPage(), f = fixture(); await mountConversation(page, false, f.dispatch);
    await page.goto('/dashboard/#/project/' + pid);
    const form = page.locator('[data-create-record="work_item"]');
    const open = page.locator('[data-project-work-create]'); await expect(open).toBeVisible();
    const folder = process.env.BATC_POLISH_SHOTS;
    if (folder) {mkdirSync(folder, {recursive: true}); await page.screenshot({animations: 'disabled', path: `${folder}/work-items-closed-${locale}-${width}.png`});}
    await expect(form).toBeHidden();
    await open.click();
    const title = form.getByLabel(locale === 'en' ? 'New work item' : '新工作項目', {exact: true});
    await expect(title).toBeFocused(); await title.fill('Retained draft');
    expect(await form.locator('.actions').evaluate(node => [...node.children].map(child => child.tagName))).toEqual(['BUTTON', 'BUTTON', 'BUTTON', 'SPAN']);
    if (folder) await page.screenshot({animations: 'disabled', path: `${folder}/work-items-open-${locale}-${width}.png`});
    await form.getByRole('button', {name: locale === 'en' ? 'Cancel' : '取消', exact: true}).click();
    await expect(form).toBeHidden(); await expect(open).toBeFocused();
    await open.click(); await expect(title).toHaveValue('Retained draft');
    await page.reload(); await expect(form).toBeHidden(); await open.click(); await expect(title).toHaveValue('Retained draft');
    expect(f.state.writes).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {await context.close();}
});

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
        if (name === 'project') await page.locator('[data-project-new]').click();
        if (name === 'item') await page.locator('[data-project-work-create]').click();
        if (name === 'locations') await page.locator('[data-setup-advanced] > summary').click();
        const details = page.locator(name === 'locations' ? '[data-connection-card] > details' : '[data-create-advanced]');
        await details.locator('summary').click();
        await expect(details).toHaveAttribute('open','');
        expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({animations: 'disabled', path:`${folder}/${name}-${locale}-${width}.png`,fullPage:true});
        if (name !== 'locations') {
          // Fast-forward the chevron transition before checking its settled geometry.
          const transform = await details.locator('summary').evaluate(el => getComputedStyle(el, '::before').transform);
          expect(transform).toBe('matrix(-0.707107, 0.707107, -0.707107, -0.707107, 0, 0)');
        }
      }
    }
    await context.close();
  }
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) test(`project form opens on request and retains its draft ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
  try {
    const page = await context.newPage(), f = fixture(), errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await mountConversation(page, false, f.dispatch); await page.goto('/dashboard/#/projects');
    const form = page.locator('[data-create-record="project"]'), open = page.locator('[data-project-new]');
    await expect(open).toHaveText(locale === 'en' ? 'New project' : '新增專案');
    await expect(open).toBeVisible(); await expect(form).toBeHidden();
    const folder = process.env.BATC_FIX1_SHOTS;
    if (folder) {mkdirSync(folder, {recursive: true}); await page.screenshot({animations: 'disabled', path: `${folder}/projects-closed-${locale}-${width}.png`});}
    await open.click();
    const name = form.getByLabel(locale === 'en' ? 'New project name' : '新專案名稱', {exact: true});
    await expect(name).toBeFocused(); await name.fill('Retained project draft');
    expect(await form.locator('.actions').evaluate(node => [...node.children].map(child => child.tagName))).toEqual(['BUTTON', 'BUTTON', 'BUTTON', 'SPAN']);
    if (folder) await page.screenshot({animations: 'disabled', path: `${folder}/projects-open-${locale}-${width}.png`});
    await form.getByRole('button', {name: locale === 'en' ? 'Cancel' : '取消', exact: true}).click();
    await expect(form).toBeHidden(); await expect(open).toBeFocused();
    await open.click(); await expect(name).toHaveValue('Retained project draft');
    await page.reload(); await expect(form).toBeHidden(); await open.click(); await expect(name).toHaveValue('Retained project draft');
    expect(f.state.writes).toEqual([]);
    await form.getByRole('button', {name: locale === 'en' ? 'Add' : '新增', exact: true}).click();
    await expect(page).toHaveURL(new RegExp('#/project/' + pid + '$'));
    await expect(page.locator('[data-project-summary]')).toBeVisible();
    if (folder) await page.screenshot({animations: 'disabled', path: `${folder}/project-created-${locale}-${width}.png`});
    expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body).toEqual({action: 'project.create', target: {}, params: {name: 'Retained project draft'}, preconditions: {}});
    expect(errors).toEqual([]); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {await context.close();}
});

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 768, 390]) test(`project repository chip stays compact ${locale} ${width}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
  try {
    const page = await context.newPage(), f = fixture(); await mountConversation(page, false, f.dispatch);
    await page.goto('/dashboard/#/project/' + pid);
    const chip = page.locator('[data-project-summary] .chip').filter({hasText: 'example/docs'});
    await expect(chip).toBeVisible(); const bounds = await chip.boundingBox();
    expect(bounds!.height).toBeLessThanOrEqual(24);
    const row = await page.locator('.project-summary-actions').boundingBox();
    expect(Math.abs(bounds!.y + bounds!.height / 2 - row!.y - row!.height / 2)).toBeLessThan(1);
    const folder = process.env.BATC_FIX1_SHOTS;
    if (folder) {mkdirSync(folder, {recursive: true}); await page.screenshot({animations: 'disabled', path: `${folder}/project-header-${locale}-${width}.png`});}
    expect(f.state.writes).toEqual([]); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {await context.close();}
});

for (const native of [false,true]) test.describe(native ? 'Native create' : 'Browser create',()=>{
  test('advanced project creation keeps exact central relationships and original lost-reply request',async({page})=>{
    const f=fixture(); f.state.lost=true; await mountConversation(page,native,f.dispatch);
    await page.goto('/dashboard/#/projects'); await page.locator('[data-project-new]').click();
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
    await expect(form.getByRole('button',{name:'Cancel',exact:true})).toHaveCount(0);
    await page.reload();
    await expect(form).toBeVisible(); await expect(page.locator('[data-project-new]')).toBeHidden();
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
    await page.locator('[data-project-work-create]').click();
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
    await page.reload(); await page.locator('[data-project-work-create]').click(); await form.locator('summary').click();
    await expect(form.getByLabel('Request (verbatim)',{exact:true})).toHaveValue('Preserve this exact request.');
    await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body).toEqual({action:'work_item.create',target:{project_id:pid},params:{title:'Review changed files',
      goal:'Keep the requested behavior',request:'Preserve this exact request.',acceptance:'Focused checks pass.',
      steps:[{text:'Read source',done:false},{text:'Run checks',done:false}],state:'waiting',parent_id:wid,derived_from:wid},preconditions:{}});
    await expect(form.getByLabel('New work item',{exact:true})).toHaveValue('');
    await expect(form.getByRole('button',{name:'Add',exact:true,includeHidden:true})).toBeEnabled();
    await expect(form).toBeHidden();
    await expect(page.locator('[data-project-work-create]')).toBeFocused();
  });

  test('unavailable draft storage prevents a creation request',async({page})=>{
    const f=fixture(); await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects'); await page.locator('[data-project-new]').click();
    const form=page.locator('[data-create-record="project"]');
    await form.getByLabel('New project name',{exact:true}).fill('Never sent');
    await page.evaluate(()=>{const set=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key.startsWith('batc.create.'))throw Error('Draft storage unavailable');return set.call(this,key,value);};});
    await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect(form).toContainText('Draft storage unavailable'); expect(f.state.writes).toHaveLength(0);
  });

  test('work item recovery stays open and uses the original request after reload', async ({page}) => {
    const f = fixture(); f.state.lost = true; await mountConversation(page, native, f.dispatch);
    await page.goto('/dashboard/#/project/' + pid); await page.locator('[data-project-work-create]').click();
    const form = page.locator('[data-create-record="work_item"]');
    await form.getByLabel('New work item', {exact: true}).fill('Recover this creation');
    await form.getByRole('button', {name: 'Add', exact: true}).click();
    await expect(form).toContainText('Original reply unavailable');
    await expect(form.getByRole('button', {name: 'Cancel', exact: true})).toHaveCount(0);
    await page.reload(); await expect(form).toBeVisible();
    await expect(page.locator('[data-project-work-create]')).toBeHidden();
    await expect(form.getByLabel('New work item', {exact: true})).toHaveValue('Recover this creation');
    await form.getByRole('button', {name: 'Check original creation request', exact: true}).click();
    await expect.poll(() => f.state.writes.length).toBe(2);
    expect(f.state.writes[1]).toEqual(f.state.writes[0]); await expect(form).toBeHidden();
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
    const f=fixture(); f.state.wrongReceipt=true; await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects'); await page.locator('[data-project-new]').click();
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
    await expect(page).toHaveURL(new RegExp('#/project/' + pid + '$'));
  });

  for (const uncertain of ['lost reply','foreign receipt']) for (const status of [401,403,422])
    test(`${uncertain} followed by ${status} retains the original creation across reload`,async({page})=>{
      const f=fixture(); f.state.lost=uncertain==='lost reply'; f.state.wrongReceipt=uncertain==='foreign receipt';
      await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects'); await page.locator('[data-project-new]').click();
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
      if (!native && status===401) {
        await expect(page.getByRole('alert')).toContainText('Token not accepted.');
        await page.getByRole('button',{name:'Retry remembered token',exact:true}).click();
        await page.evaluate(()=>{location.hash='#/projects';});
      }
      await expect(form.getByLabel('New project name',{exact:true})).toBeDisabled();
      await expect(form.getByRole('button',{name:'Edit a new creation request',exact:true})).toBeHidden();
      f.state.reject=null; f.state.wrongReceipt=false;
      await form.getByRole('button',{name:'Check original creation request',exact:true}).click();
      await expect(page).toHaveURL(new RegExp('#/project/' + pid + '$'));
      expect(f.state.writes).toHaveLength(3); expect(f.state.ops.size).toBe(1);
      for (const request of f.state.writes.slice(1)) {
        expect(request.idempotency_key).toBe(f.state.writes[0].idempotency_key);
        expect(request.body).toEqual(f.state.writes[0].body);
      }
    });

  test('proven first-attempt validation refusal allows reviewing a new creation request',async({page})=>{
    const f=fixture(); f.state.reject={status:422,code:'INVALID_PARAMS'};
    await mountConversation(page,native,f.dispatch); await page.goto('/dashboard/#/projects'); await page.locator('[data-project-new]').click();
    const form=page.locator('[data-create-record="project"]');
    await form.getByLabel('New project name',{exact:true}).fill('Invalid original');
    await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect(form.getByRole('button',{name:'Edit a new creation request',exact:true})).toBeVisible();
    expect(f.state.ops.size).toBe(0);
    await page.reload();
    await form.getByRole('button',{name:'Edit a new creation request',exact:true}).click();
    await form.getByLabel('New project name',{exact:true}).fill('Corrected work');
    f.state.reject=null; await form.getByRole('button',{name:'Add',exact:true}).click();
    await expect(page).toHaveURL(new RegExp('#/project/' + pid + '$'));
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

// Hold the creation POST until release. The count increments when the request
// arrives, before the response, so assertions during the hold do not wait on a timer.
function holdPosts() {
  let release = () => {};
  const promise = new Promise<void>(resolve => { release = resolve; });
  const posts = {n: 0};
  return {promise, release: () => release(), posts};
}
function countPosts(f: ReturnType<typeof fixture>, hold: ReturnType<typeof holdPosts>, status?: string) {
  const inner = f.dispatch;
  f.dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    const creating = input.method === 'POST' && path === '/operations';
    if (creating) { hold.posts.n += 1; await hold.promise; }
    const result = await inner(input);
    if (creating && status && result?.data?.operation) result.data.operation.status = status;
    return result;
  };
  return f;
}
async function createBusy(page: Page, kind: string) {
  return page.evaluate(recordKind => {
    const form = document.querySelector(`[data-create-record="${recordKind}"]`);
    const button = form?.querySelector('button[type="submit"]') as HTMLButtonElement | null;
    const status = form?.querySelector(':scope > [role="status"]');
    const active = document.activeElement;
    return {
      active: active === document.body ? 'body' : active === button ? 'submit'
        : `${active?.tagName || ''}:${(active?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 48)}`,
      disabled: button ? button.disabled : null,
      ariaDisabled: button?.getAttribute('aria-disabled') ?? null,
      ariaBusy: status?.getAttribute('aria-busy') ?? null,
    };
  }, kind);
}

test.describe('create record busy focus', () => {
  test('1. Enter on Add item, with the POST held', async ({page}) => {
    const hold = holdPosts(), f = countPosts(fixture(), hold);
    await mountConversation(page, false, f.dispatch);
    await page.goto('/dashboard/#/project/' + pid);
    await page.locator('[data-project-work-create]').click();
    const form = page.locator('[data-create-record="work_item"]');
    const title = form.getByLabel('New work item', {exact: true});
    await title.fill('Focus stays on Add');
    const add = form.locator('button[type="submit"]');
    await add.focus();
    await expect(add).toBeFocused();
    await page.keyboard.press('Enter');
    await expect.poll(() => hold.posts.n).toBe(1);
    try {
      await expect.poll(() => createBusy(page, 'work_item')).toEqual({
        active: 'submit', disabled: false, ariaDisabled: 'true', ariaBusy: 'true',
      });
      await expect(title).toBeDisabled();
      await page.keyboard.press('Enter');
      await add.click({force: true});
      expect(hold.posts.n).toBe(1);
      await expect(add).toBeFocused();
    } finally { hold.release(); }
    await expect(page.locator('[data-project-work-create]')).toBeFocused();
    await expect(add).not.toHaveAttribute('aria-disabled');
    await expect(form.locator(':scope > [role="status"]')).not.toHaveAttribute('aria-busy');
    expect(hold.posts.n).toBe(1);
    expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body.action).toBe('work_item.create');
  });

  test('2. Enter in a text field, with the POST held', async ({page}) => {
    const hold = holdPosts(), f = countPosts(fixture(), hold);
    await mountConversation(page, false, f.dispatch);
    await page.goto('/dashboard/#/project/' + pid);
    await page.locator('[data-project-work-create]').click();
    const form = page.locator('[data-create-record="work_item"]');
    const title = form.getByLabel('New work item', {exact: true});
    const add = form.locator('button[type="submit"]');
    await title.fill('Implicit submit');
    await title.press('Enter');
    await expect.poll(() => hold.posts.n).toBe(1);
    try {
      await expect(add).toBeFocused();
      await expect(add).toHaveAttribute('aria-disabled', 'true');
      expect(await add.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(false);
      await expect(title).toBeDisabled();
      await page.keyboard.press('Enter');
      expect(hold.posts.n).toBe(1);
    } finally { hold.release(); }
    expect(hold.posts.n).toBe(1);
    await expect(page.locator('[data-project-work-create]')).toBeFocused();
    expect(f.state.writes).toHaveLength(1);
  });

  test('3. A terminal failure moves focus to Edit request', async ({page}) => {
    const hold = holdPosts(), f = countPosts(fixture(), hold, 'failed');
    await mountConversation(page, false, f.dispatch);
    await page.goto('/dashboard/#/project/' + pid);
    await page.locator('[data-project-work-create]').click();
    const form = page.locator('[data-create-record="work_item"]');
    await form.getByLabel('New work item', {exact: true}).fill('Terminal failure');
    const add = form.locator('button[type="submit"]');
    await add.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => hold.posts.n).toBe(1);
    try { await expect(add).toBeFocused(); }
    finally { hold.release(); }
    const review = form.getByRole('button', {name: 'Edit a new creation request', exact: true});
    await expect(review).toBeFocused();
    expect(await add.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(true);
    await expect(add).not.toHaveAttribute('aria-disabled');
    await expect(form.locator(':scope > [role="status"]')).not.toHaveAttribute('aria-busy');
    expect(hold.posts.n).toBe(1);
    expect(f.state.writes).toHaveLength(1);
    expect([...f.state.ops.values()][0].status).toBe('failed');
  });

  test('4. An initial admission refusal moves focus to Edit request', async ({page}) => {
    const hold = holdPosts(), f = countPosts(fixture(), hold);
    f.state.reject = {status: 422, code: 'INVALID_PARAMS'};
    await mountConversation(page, false, f.dispatch);
    await page.goto('/dashboard/#/project/' + pid);
    await page.locator('[data-project-work-create]').click();
    const form = page.locator('[data-create-record="work_item"]');
    await form.getByLabel('New work item', {exact: true}).fill('Refused original');
    const add = form.locator('button[type="submit"]');
    await add.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => hold.posts.n).toBe(1);
    try { await expect(add).toBeFocused(); }
    finally { hold.release(); }
    const review = form.getByRole('button', {name: 'Edit a new creation request', exact: true});
    await expect(review).toBeFocused();
    expect(await add.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(true);
    await expect(add).not.toHaveAttribute('aria-disabled');
    await expect(form.locator(':scope > [role="status"]')).not.toHaveAttribute('aria-busy');
    expect(hold.posts.n).toBe(1);
    expect(f.state.ops.size).toBe(0);
    expect(f.state.writes).toHaveLength(1);
  });

  test('5. Tab while busy reaches the busy Add button', async ({page}) => {
    const hold = holdPosts(), f = countPosts(fixture(), hold);
    await mountConversation(page, false, f.dispatch);
    await page.goto('/dashboard/#/project/' + pid);
    await page.locator('[data-project-work-create]').click();
    const form = page.locator('[data-create-record="work_item"]');
    await form.getByLabel('New work item', {exact: true}).fill('Tab while busy');
    const add = form.locator('button[type="submit"]');
    await add.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => hold.posts.n).toBe(1);
    try {
      await expect(add).toHaveAttribute('aria-disabled', 'true');
      // Text fields are disabled while the request is in flight, so the tab stop
      // before Add is the details summary.
      const summary = form.locator('[data-create-advanced] > summary');
      await summary.focus();
      await expect(summary).toBeFocused();
      await page.keyboard.press('Tab');
      await expect(add).toBeFocused();
      expect(hold.posts.n).toBe(1);
    } finally { hold.release(); }
    await expect(page.locator('[data-project-work-create]')).toBeFocused();
    expect(hold.posts.n).toBe(1);
  });

  test('6. Enter on Add project, with the POST held', async ({page}) => {
    const hold = holdPosts(), f = countPosts(fixture(), hold);
    await mountConversation(page, false, f.dispatch);
    await page.goto('/dashboard/#/projects');
    await page.locator('[data-project-new]').click();
    const form = page.locator('[data-create-record="project"]');
    await form.getByLabel('New project name', {exact: true}).fill('Focus stays on Add');
    const add = form.locator('button[type="submit"]');
    await add.focus();
    await expect(add).toBeFocused();
    await page.keyboard.press('Enter');
    await expect.poll(() => hold.posts.n).toBe(1);
    try {
      await expect.poll(() => createBusy(page, 'project')).toEqual({
        active: 'submit', disabled: false, ariaDisabled: 'true', ariaBusy: 'true',
      });
      await page.keyboard.press('Enter');
      await add.click({force: true});
      expect(hold.posts.n).toBe(1);
      await expect(add).toBeFocused();
    } finally { hold.release(); }
    await expect(page.locator('[data-project-summary]')).toBeVisible();
    expect(hold.posts.n).toBe(1);
    expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body.action).toBe('project.create');
    await expect(page.locator('[data-create-record="project"]')).toHaveCount(0);
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    // viewProjects sets location.hash to the new project. route() removes the
    // projects view, including this disclosure's toggle, before toggle.focus()
    // can run. The project page does not move focus.
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(true);
  });
});
