import {test, expect} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
import {discussHref} from '../src/pm-discuss-link.js';

const kinds=['snapshot','plan_revision','requirement','milestone','work_item','governance','finding','repair','follow_up','proposal','issue_mapping','meeting','lifecycle_transition'];
for (const locale of ['en','zh-TW'] as const) for (const kind of kinds) test(`central discussion label ${kind} ${locale}`, async ({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=demoFixture({locale,discussions:true});
  const wid=f.ids.workspaceId, root=`/pm/workspaces/${wid}`, title=locale==='en'?'Recovery support budget':'帳號復原支援預算';
  const id=(prefix:string)=>prefix+'a'.repeat(prefix==='wi_'?20:32), work=id('wi_'), plan=id('pmrev_'), roadmap=id('pmrb_');
  const refs:any={snapshot:{kind,id:id('pmsnap_')},plan_revision:{kind,id:plan},requirement:{kind,id:'email',revision_id:plan},milestone:{kind,id:'review',revision_id:roadmap},
    work_item:{kind,id:work},governance:{kind,id:id('pmgov_')},finding:{kind,id:id('pmfn_'),observation_id:id('pmgh_'),document_digest:'a'.repeat(64)},
    repair:{kind,id:id('pmrp_')},follow_up:{kind,id:id('pmfu_')},proposal:{kind,id:id('pmcp_')},issue_mapping:{kind,id:id('pmim_')},meeting:{kind,id:id('pmmeet_')},lifecycle_transition:{kind,id:id('pmlt_')}};
  const ref=refs[kind], sources:any={
    [`/kanban/${work}`]:{workspace_id:wid,card:{work_item_id:work,work:{work_item_id:work,title},binding:{workspace_id:wid,work_item_id:work}}},
    [`/plans/${plan}`]:{plan:{workspace_id:wid,revision_id:plan,sequence:7,document:{title,requirements:[{id:'email',text:title}]}}},
    [`/roadmap/revisions/${roadmap}`]:{revision:{workspace_id:wid,revision_id:roadmap,document:{milestones:[{milestone_id:'review',title}]}}},
    [`/snapshots/${id('pmsnap_')}`]:{snapshot:{workspace_id:wid,snapshot_id:id('pmsnap_'),title}},
    [`/governance/${id('pmgov_')}`]:{workspace_id:wid,record:{record_id:id('pmgov_'),kind:'risk'},revision:{document:{title}}},
    '/maintenance-findings/current':{workspace_id:wid,observation_ref:{observation_id:id('pmgh_'),document_digest:'a'.repeat(64)},sources:{issues:{findings:[{finding_id:id('pmfn_'),facts:{statement:title}}]}}},
    [`/maintenance-repairs/${id('pmrp_')}`]:{workspace_id:wid,repair:{repair_id:id('pmrp_'),work_item_id:work}},
    [`/follow-ups/${id('pmfu_')}`]:{workspace_id:wid,intake:{intake_id:id('pmfu_'),document:{title}}},
    [`/conversation-proposals/${id('pmcp_')}`]:{workspace_id:wid,proposal:{proposal_id:id('pmcp_'),document:{title}}},
    [`/issue-mappings/${id('pmim_')}`]:{workspace_id:wid,current:{mapping_id:id('pmim_'),work_item_id:work,document:{public_title:title}}},
    [`/meetings/${id('pmmeet_')}`]:{workspace_id:wid,meeting:{meeting_id:id('pmmeet_'),title}},
    [`/lifecycle/transitions/${id('pmlt_')}`]:{workspace_id:wid,transition:{transition_id:id('pmlt_'),reason:title}},
  };
  let withdrawn=false;const writes:any[]=[];
  await mountConversation(page,false,async input=>{
    if(input.method!=='GET')writes.push(input);
    const suffix=input.path.startsWith(root)?input.path.slice(root.length):'';
    if(Object.hasOwn(sources,suffix))return withdrawn?{status:404,data:{error:{code:'NOT_FOUND',message:'Unavailable'}}}:{status:200,data:sources[suffix]};
    return f.dispatch(input);
  });
  const from=kind==='work_item'?'#/session/mock/session':kind==='requirement'?'schedule':'requirements';
  await page.goto('/dashboard/'+discussHref({workspaceId:wid,refs:[ref],title:'Untrusted URL label',from}));
  const chip=page.locator(`[data-discussion-context=${kind}]`);await expect(chip).toContainText(title);
  await expect(chip).not.toContainText('Untrusted URL label');await expect(chip).not.toContainText(/(?:pm[a-z]*|wi)_a{20}/);
  if(kind==='plan_revision')await expect(chip).toContainText(locale==='en'?'Revision 7':'版本 7');
  if(kind==='governance')await expect(chip).toContainText(locale==='en'?'Risk ·':'風險 ·');
  const composer=page.locator('[data-discussion-composer]');await composer.fill('Preserved local draft');
  await expect(page.getByRole('button',{name:locale==='en'?'Send':'傳送',exact:true})).toBeEnabled();
  withdrawn=true;await page.locator('[data-pm-refresh]').click();
  await expect(chip).toContainText(locale==='en'?'Unavailable':'無法取得');await expect(chip).not.toContainText(title);
  await expect(composer).toHaveValue('Preserved local draft');await expect(page.getByRole('button',{name:locale==='en'?'Send':'傳送',exact:true})).toBeDisabled();
  expect(writes).toEqual([]);await context.close();
});
