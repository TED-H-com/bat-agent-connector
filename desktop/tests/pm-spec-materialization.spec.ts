import {openPmSection} from './pm-navigation.ts';
import {mountConversation} from './conversation-fixture.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {bootSpec,mountSpec,specFixture,panel,handoff,materialize,startForm,workspaceId,packageId} from './pm-spec-materialization-fixture.ts';
const review=async(page:any)=>{await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(handoff(page).getByRole('heading',{name:'Reviewed implementation base',exact:true})).toBeVisible();};
const fill=async(page:any)=>{await startForm(page).getByLabel('Why use this implementation base?',{exact:true}).fill('Use the selected fixed base');await startForm(page).getByLabel('Instructions for this implementation',{exact:true}).fill('Implement fixed requirements and verify results.');};
const choose=async(page:any,id:string)=>{await review(page);await startForm(page).getByLabel('Specification delivery',{exact:true}).selectOption(id);await expect(startForm(page).locator('button[type=submit]')).toBeDisabled();await review(page);await handoff(page).getByRole('button',{name:'Use this reviewed project version',exact:true}).click();};

test('materialization fixes package digest and string scope without starting implementation',async({page})=>{
  const f=specFixture();await mountSpec(page,f);expect(f.state.writes).toHaveLength(0);await materialize(page).locator('button[type=submit]').click();
  await expect(panel(page).getByRole('heading',{name:'Fixed specification saved',exact:true})).toBeVisible();
  expect(f.state.writes[0].body).toEqual({action:'pm.spec.materialize',target:{workspace_id:workspaceId,package_id:packageId},params:{},preconditions:{expected_package_digest:f.base.row.document_digest,expected_scope_revision:'pmscope_'+'a'.repeat(32)}});
  expect(f.base.state.writes).toHaveLength(0);await expect(materialize(page).locator('button[type=submit]')).toBeDisabled();
  await expect(panel(page).getByRole('link',{name:'Open fixed specification attachment',exact:true})).toHaveAttribute('href',`#/artifact-review/artifact/${f.state.rows[0].artifact_ref.artifact_id}/1`);
});

test('stable task history retains removed identities and separate artifact and file-set digests',async({page})=>{
  const f=specFixture(),row=f.save();row.document.tasks[0].state='removed';row.document.tasks[0].history.push({package_id:packageId,document_digest:f.base.row.document_digest,state:'removed'});
  await mountSpec(page,f);await panel(page).getByText('Stable task identities and revisions',{exact:true}).click();await panel(page).getByText('token',{exact:true}).click();
  await expect(panel(page)).toContainText('Removed from this revision');await expect(panel(page)).toContainText(row.document.tasks[0].task_id);
  await panel(page).getByText('Original artifact and Git proof',{exact:true}).click();await expect(panel(page)).toContainText('Specification attachment digest: '+'a'.repeat(64));await expect(panel(page)).toContainText('Generated file-set digest: '+'7'.repeat(64));expect(f.base.state.writes).toHaveLength(0);
});

test('lost materialization receipt reloads its original key despite paused scope',async({page})=>{
  const f=specFixture();await mountSpec(page,f);f.state.lost=true;await materialize(page).locator('button[type=submit]').click();await expect(materialize(page)).toContainText('Reply unavailable');
  const original=structuredClone(f.state.writes[0]);f.base.fixture.state.stage='paused';f.base.state.blocked='scope';await page.reload();await bootSpec(page);
  await materialize(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect(panel(page)).toContainText('Fixed specification saved');expect(f.state.writes.at(-1)).toEqual(original);expect(f.state.ops.size).toBe(1);
});

test('scope and lifecycle stop new writes while readers retain source facts',async({page})=>{
  const f=specFixture();f.base.state.blocked='scope';await mountSpec(page,f);await expect(materialize(page).locator('button[type=submit]')).toBeDisabled();await expect(panel(page)).toContainText('Current scope, work or dependency checks');
  f.base.state.blocked=null;f.base.fixture.state.stage='paused';await page.evaluate(()=>(window as any).specRefresh());await expect(materialize(page).locator('button[type=submit]')).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});

test('reader cannot save and unavailable capability causes no materialization reads',async({page})=>{
  const f=specFixture();f.base.fixture.base.state.role='reader';await mountSpec(page,f);await expect(materialize(page).locator('button[type=submit]')).toBeDisabled();await expect(panel(page)).toContainText('workspace write membership');
  f.state.enabled=false;const count=f.state.reads.length;await page.evaluate(()=>(window as any).specRefresh());expect(f.state.reads).toHaveLength(count);await expect(panel(page)).toContainText('does not advertise');
});

test('empty filtered pages advance and malformed cursor clears original facts',async({page})=>{
  const f=specFixture();f.save();f.state.partial=true;await mountSpec(page,f);expect(f.state.reads.some((value:string)=>value.includes('after=10'))).toBe(true);await expect(panel(page)).toContainText('hidden by source access');
  f.state.badCursor=true;await page.evaluate(()=>(window as any).specRefresh());await expect(panel(page).getByRole('heading',{name:'Fixed specification saved',exact:true})).toHaveCount(0);await expect(materialize(page).locator('button[type=submit]')).toBeDisabled();
});

test('withdrawal while a source list is pending cannot restore private facts',async({page})=>{
  const f=specFixture();f.save();await mountSpec(page,f);let release!:()=>void;f.state.gate=new Promise<void>(resolve=>release=resolve);
  const pending=page.evaluate(()=>(window as any).specRefresh());await expect.poll(()=>f.state.reads.length).toBeGreaterThan(2);await page.evaluate(()=>(window as any).specWithdraw());release();await pending;
  await expect(panel(page)).not.toContainText('Restore member access');await expect(panel(page).getByRole('heading',{name:'Fixed specification saved',exact:true})).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});

test('mismatched Git receipt blocks facts and new materialization',async({page})=>{
  const f=specFixture();f.save();f.state.wrong=true;await mountSpec(page,f);await expect(panel(page)).toContainText('does not match the original specification');await expect(panel(page).getByRole('heading',{name:'Fixed specification saved',exact:true})).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});

test('selected attachment handoff freezes exact spec and artifact references',async({page})=>{
  const f=specFixture(),row=f.save();await mountSpec(page,f);await choose(page,row.materialization_id);await fill(page);await startForm(page).locator('button[type=submit]').click();
  await expect(handoff(page)).toContainText('Implementation was started');expect(f.base.state.writes).toHaveLength(1);
  expect(f.base.state.writes[0].body.params.pm_spec_ref).toEqual({workspace_id:workspaceId,materialization_id:row.materialization_id,document_digest:row.document_digest});
  expect(f.base.state.writes[0].body.params.artifacts).toEqual([{artifact_id:row.artifact_ref.artifact_id,revision:1,digest:row.document_digest}]);expect(f.base.state.writes[0].body.params).not.toHaveProperty('spec_selection');
});

test('inline handoff never silently selects the available saved artifact',async({page})=>{
  const f=specFixture();f.save();await mountSpec(page,f);await review(page);await fill(page);await startForm(page).locator('button[type=submit]').click();
  expect(f.base.state.writes[0].body.params).not.toHaveProperty('pm_spec_ref');expect(f.base.state.writes[0].body.params).not.toHaveProperty('artifacts');
});

test('lost attachment handoff survives reload and pause without changing its request',async({page})=>{
  const f=specFixture(),row=f.save();await mountSpec(page,f);await choose(page,row.materialization_id);await fill(page);f.base.state.lost=true;await startForm(page).locator('button[type=submit]').click();await expect(startForm(page)).toContainText('Reply unavailable');
  const original=structuredClone(f.base.state.writes[0]);f.base.fixture.state.stage='paused';await page.reload();await bootSpec(page);await startForm(page).getByRole('button',{name:'Check original request',exact:true}).click();expect(f.base.state.writes.at(-1)).toEqual(original);expect(f.base.state.ops.size).toBe(1);
});

test('withdrawn selected attachment never falls back to inline specification',async({page})=>{
  const f=specFixture(),row=f.save();await mountSpec(page,f);await review(page);await startForm(page).getByLabel('Specification delivery',{exact:true}).selectOption(row.materialization_id);
  f.state.rows=[];f.state.filtered=true;await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(handoff(page)).toContainText('originally selected specification attachment is unavailable');
  await expect(startForm(page).locator('button[type=submit]')).toBeDisabled();expect(f.base.state.writes).toHaveLength(0);
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`spec materialization ${locale} ${width}`,async({page},info)=>{
  const f=specFixture();f.save();await page.setViewportSize({width,height:900});await mountSpec(page,f,locale);
  expect(await page.evaluate(()=>[...document.querySelectorAll('main *')].filter(node=>node.getBoundingClientRect().right>innerWidth+1).map(node=>({tag:node.tagName,class:node.className,text:node.textContent?.slice(0,70),right:node.getBoundingClientRect().right})))).toEqual([]);expect(await page.locator('main').innerText()).not.toMatch(/(?:psm|ph|wp)_[a-z_]+/);
  const output=process.env.BATC_PM_SPEC_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});await page.screenshot({path:`${output}/spec-${locale}-${width}.png`,fullPage:true});
});

for(const native of [false,true])test(native?'native workspace fixed specification handoff':'browser workspace fixed specification handoff',async({page})=>{
  const f=specFixture();await mountConversation(page,native,f.dispatch);await page.goto('/dashboard/#/pm/'+workspaceId);await openPmSection(page,'requirements','ui_packages');
  await page.locator('[data-pm-work-packages]').getByRole('button',{name:'Restore member access · Preparation 1',exact:true}).click();
  await openPmSection(page,'requirements','ui_materialization');await panel(page).locator('summary').first().click();await materialize(page).locator('button[type=submit]').click();
  await expect(panel(page)).toContainText('Fixed specification saved');expect(f.state.writes).toHaveLength(1);expect(f.base.state.writes).toHaveLength(0);
  f.base.state.deny=true;f.base.fixture.base.state.cursor++;
  await expect(panel(page).getByRole('heading',{name:'Fixed specification saved',exact:true})).toHaveCount(0);
  await expect(panel(page)).not.toContainText('Restore member access');
});
