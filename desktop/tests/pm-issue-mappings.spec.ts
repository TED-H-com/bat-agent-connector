import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {issueFixture,packageId,workId,route} from './pm-issue-mappings-fixture.ts';
const section=(page:any)=>page.locator('[data-pm-issue-mappings]');
const action=(page:any,kind:string)=>section(page).locator(`[data-pm-action="pm.issue.${kind}"]`);
async function selectPackage(page:any,number=1){await openPmSection(page,'requirements','ui_packages');await page.locator('[data-pm-work-packages]').getByLabel('Package',{exact:true}).selectOption({label:`Restore member access · Preparation ${number}`});await expect(section(page)).toContainText('Selected preparation');await openPmSection(page,'requirements','ui_publication');}
async function openPublication(page:any){await section(page).getByText('Prepare selected public text',{exact:true}).click();return action(page,'publish');}
async function writePublic(page:any,title='Selected public title',body='Selected public body'){const editor=action(page,'publish');await editor.getByLabel('Public issue title',{exact:true}).fill(title);await editor.getByLabel('Public issue description',{exact:true}).fill(body);}
async function stopEditor(page:any){await section(page).locator('details').filter({has:page.locator('[data-pm-action="pm.issue.cancel"]')}).locator(':scope > summary').click();return action(page,'cancel');}
for(const native of [false,true])test.describe(native?'Native issue mapping':'Web issue mapping',()=>{
  test('selected whole-work publication starts blank and later appends explicit text without copying private facts',async({page})=>{
    const f=issueFixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);const editor=await openPublication(page);
    await expect(editor.getByLabel('Public issue title',{exact:true})).toHaveValue('');await expect(editor.getByLabel('Public issue description',{exact:true})).toHaveValue('');
    await writePublic(page);await editor.getByRole('button',{name:'Publish selected text',exact:true}).click();
    await expect(section(page)).toContainText('example/library · #41');expect(f.state.writes).toHaveLength(1);
    expect(f.state.writes[0].body).toEqual({action:'pm.issue.publish',target:{workspace_id:f.base.state.workspaces[0].workspace_id,work_item_id:workId},params:{package_id:packageId,public_title:'Selected public title',public_body:'Selected public body'},preconditions:{expected_package_digest:'d'.repeat(64),expected_mapping_revision:null}});
    expect(JSON.stringify(f.state.writes)).not.toContain('Private');expect(f.state.revisions).toHaveLength(1);
    const original=structuredClone(f.current());await openPublication(page);await writePublic(page,'Revised public heading','A literal <script>comment</script>');
    await editor.getByRole('button',{name:'Publish selected text',exact:true}).click();await expect.poll(()=>f.state.revisions.length).toBe(2);
    expect(f.state.revisions[0]).toEqual(original);expect(f.current().document.kind).toBe('revision');
    expect(f.state.writes[1].body.preconditions.expected_mapping_revision).toBe(original.revision_id);
    await section(page).getByText('Publication history',{exact:true}).click();await section(page).getByText('Revision 2 · reader',{exact:true}).click();
    await expect(section(page)).toContainText('A literal <script>comment</script>');expect(await section(page).locator('script').count()).toBe(0);
    await expect(section(page)).toContainText('Implementation, acceptance, merge and release remain separate results.');
    expect(f.base.state.writes).toHaveLength(0);
  });
  test('lost acknowledgement preserves the original package, mapping CAS and key across a refresh',async({page})=>{
    const f=issueFixture();f.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);const editor=await openPublication(page);
    await writePublic(page);await editor.getByRole('button',{name:'Publish selected text',exact:true}).click();await expect(editor).toContainText('Reply unavailable');
    await section(page).getByRole('button',{name:'Refresh issue mappings'}).click();await openPublication(page);
    await editor.getByRole('button',{name:'Check original request'}).click();await expect(editor.getByLabel('Public issue title')).toHaveValue('');
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1]).toEqual(f.state.writes[0]);expect(f.state.revisions).toHaveLength(1);
  });
  test('changing the selected package preserves typed public text and requires explicit version review',async({page})=>{
    const f=issueFixture();f.addPackage();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);const editor=await openPublication(page);await writePublic(page);
    await selectPackage(page,2);await openPublication(page);await expect(editor).toContainText('Your draft retains its original version.');
    await expect(editor.getByRole('button',{name:'Publish selected text'})).toBeDisabled();await expect(editor.getByLabel('Public issue title')).toHaveValue('Selected public title');
    await editor.getByRole('button',{name:'Use the currently reviewed version'}).click();await editor.getByRole('button',{name:'Publish selected text'}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.package_id).toBe(f.state.packages[1].package_id);
    expect(f.state.writes[0].body.preconditions.expected_package_digest).toBe(f.state.packages[1].document_digest);
  });
  test('another issue revision cannot silently replace the draft original mapping CAS',async({page})=>{
    const f=issueFixture();f.revision({public_title:'Earlier public',public_body:'Earlier body'});await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);const editor=await openPublication(page);await writePublic(page);
    const newer=f.revision({public_title:'Another author revision',public_body:'A separate comment'});
    await section(page).getByRole('button',{name:'Refresh issue mappings'}).click();await openPublication(page);
    await expect(editor.getByRole('button',{name:'Publish selected text'})).toBeDisabled();await expect(editor.getByLabel('Public issue title')).toHaveValue('Selected public title');
    await editor.getByRole('button',{name:'Use the currently reviewed version'}).click();await editor.getByRole('button',{name:'Publish selected text'}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.preconditions.expected_mapping_revision).toBe(newer.revision_id);
  });
  test('paused tracking can record a private stop after removing an optional public explanation',async({page})=>{
    const f=issueFixture();f.revision({public_title:'Existing issue',public_body:'Existing content'});f.state.stage='paused';await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);
    const editor=await stopEditor(page);await editor.getByLabel('Private management reason',{exact:true}).fill('Keep the published version.');
    await expect(editor.getByRole('button',{name:'Stop this workspace’s tracking',exact:true})).toBeEnabled();
    await editor.getByLabel('Optional public explanation',{exact:true}).fill('Do not publish while paused.');
    await expect(editor.getByRole('button',{name:'Stop this workspace’s tracking',exact:true})).toBeDisabled();
    await expect(editor.getByLabel('Optional public explanation')).toHaveAttribute('maxlength','8000');
    await editor.getByRole('button',{name:'Remove the public explanation'}).click();
    await expect(editor.getByLabel('Optional public explanation')).toHaveValue('');await editor.getByRole('button',{name:'Stop this workspace’s tracking',exact:true}).click();
    await expect.poll(()=>f.state.revisions.length).toBe(2);expect(f.state.writes[0].body.params).toEqual({rationale:'Keep the published version.'});
    expect(f.current().document.state).toBe('cancelled');expect(f.current().document.external_issue_state).toBe('not_changed');
  });
  test('readers can inspect original history but cannot publish or cancel',async({page})=>{
    const f=issueFixture();f.base.state.role='reader';f.revision({public_title:'Existing readable title',public_body:'Existing readable body'});await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);
    const editor=await openPublication(page);await expect(editor.getByRole('button',{name:'Publish selected text'})).toBeDisabled();
    const cancel=await stopEditor(page);await expect(cancel.getByRole('button',{name:'Stop this workspace’s tracking',exact:true})).toBeDisabled();
    await section(page).getByText('Publication history',{exact:true}).click();await section(page).getByText('Revision 1 · reader',{exact:true}).click();
    await expect(section(page)).toContainText('Existing readable body');expect(f.state.writes).toHaveLength(0);
  });
  test('partial and hidden mappings are never treated as absence; the cursor remains usable',async({page})=>{
    const f=issueFixture();f.revision({public_title:'Existing issue',public_body:'Existing body'});f.state.partial=true;await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);
    await expect(section(page)).toContainText('Read the remaining mappings');await expect(action(page,'publish')).toHaveCount(0);
    await section(page).getByRole('button',{name:'Load more issue mappings'}).click();await expect(section(page)).toContainText('#41');
    expect(f.state.reads.some(row=>row.path.includes('issue-mappings?after=1&'))).toBe(true);
    f.state.hidden=true;await section(page).getByRole('button',{name:'Refresh issue mappings'}).click();
    await expect(section(page)).toContainText('Hidden mappings cannot be treated as absent');await expect(action(page,'publish')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
  });
  test('source revocation clears the old history and preserves a frozen public draft without dispatch',async({page})=>{
    const f=issueFixture();f.revision({public_title:'Original source title',public_body:'Original source body'});await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);const editor=await openPublication(page);await writePublic(page);
    f.state.denied=true;await section(page).getByRole('button',{name:'Refresh issue mappings'}).click();
    await expect(section(page)).toContainText('Previously displayed mapping details have been removed.');await expect(section(page)).not.toContainText('#41');await expect(editor).toHaveCount(0);
    f.state.denied=false;await selectPackage(page);await openPublication(page);await expect(editor.getByLabel('Public issue title')).toHaveValue('Selected public title');expect(f.state.writes).toHaveLength(0);
  });
  test('a late publication response cannot restore withdrawn facts and later recovers only the original operation',async({page})=>{
    const f=issueFixture();let release=()=>{};f.state.writeGate=new Promise<void>(resolve=>release=resolve);
    await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);const editor=await openPublication(page);await writePublic(page);
    await editor.getByRole('button',{name:'Publish selected text'}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    f.state.denied=true;await section(page).getByRole('button',{name:'Refresh issue mappings'}).click();await expect(section(page)).toContainText('Previously displayed mapping details have been removed.');
    release();await page.waitForTimeout(350);await expect(section(page)).not.toContainText('#41');await expect(editor).toHaveCount(0);
    f.state.denied=false;await selectPackage(page);await openPublication(page);
    await expect(editor.getByLabel('Public issue title')).toHaveValue('');await expect(editor.locator('button[type=submit]')).toBeEnabled();
    expect(f.state.writes).toHaveLength(1);expect(f.state.ops.size).toBe(1);
  });
  test('a delayed mapping read cannot restore private history after its selected package is withdrawn',async({page})=>{
    const f=issueFixture();f.revision({public_title:'Sensitive original title',public_body:'Sensitive original detail'});
    await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);
    let release=()=>{};f.state.gate=new Promise<void>(resolve=>release=resolve);
    const prior=f.state.reads.length;await section(page).getByRole('button',{name:'Refresh issue mappings'}).click();await expect.poll(()=>f.state.reads.length).toBeGreaterThan(prior);
    f.state.packageDenied=true;await openPmSection(page,'requirements','ui_packages');await page.locator('[data-pm-work-packages]').getByRole('button',{name:'Refresh work packages'}).click();
    await expect(page.locator('[data-pm-work-packages]')).toContainText('Package source unavailable');release();await page.waitForTimeout(350);
    await expect(section(page)).not.toContainText('#41');await expect(section(page)).not.toContainText('Sensitive original');await expect(action(page,'publish')).toHaveCount(0);
    expect(f.state.writes).toHaveLength(0);
  });
  test('a stopped mapping still exposes recovery of an earlier ambiguous publication',async({page})=>{
    const f=issueFixture();f.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_publication');await selectPackage(page);const editor=await openPublication(page);await writePublic(page);
    await editor.getByRole('button',{name:'Publish selected text'}).click();await expect(editor).toContainText('Reply unavailable');
    f.revision({rationale:'Another member stopped local tracking.'},f.state.packages[0],true);
    await section(page).getByRole('button',{name:'Refresh issue mappings'}).click();await openPublication(page);
    await editor.getByRole('button',{name:'Check original request'}).click();await expect(editor.getByLabel('Public issue title')).toHaveValue('');
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1]).toEqual(f.state.writes[0]);expect(f.state.revisions).toHaveLength(2);
  });
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`issue mapping layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=issueFixture();
  f.revision({public_title:'A public heading · 公開標題',public_body:'A long literal source '.repeat(50)});
  await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page, 'requirements' , 'ui_packages');const picker=page.locator('[data-pm-work-packages]').getByLabel(/^(Package|工作包)$/);await picker.selectOption(packageId);await expect(picker).toHaveValue(packageId);
  await openPmSection(page,'requirements','ui_publication');await expect(section(page)).toContainText('#41');await section(page).locator('details').filter({has:page.locator('[data-pm-action="pm.issue.publish"]')}).locator(':scope > summary').click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.PM_ISSUE_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await section(page).screenshot({path:`${out}/${locale}-${width}.png`});}
  await context.close();
});
