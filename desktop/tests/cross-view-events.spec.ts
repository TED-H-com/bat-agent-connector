import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';

// Budgets cover all central GETs caused by one event page, including the shared
// rail's reads. Event polling itself is excluded. PM projections coalesce once
// per page; no mixed resource event reloads the workspace or conversation.
const budgets: Record<string, number> = {
  overview: 28, discussions: 28, requirements: 48, board: 28, schedule: 28,
  dependencies: 28, issues: 28, agents: 28, session: 24,
  pending: 18, operations: 8, delivery: 12, settings: 8, projects: 8,
};
for (const [view, budget] of Object.entries(budgets)) test(`20 mixed events coalesce on ${view} (budget ${budget})`, async ({page}) => {
  const f=demoFixture({discussions:true}), reads: {path:string, at:number}[] = [], writes: string[] = [];
  let cursor=0, delivered=0;
  const types=['session','task','command','operation','work_item','deployment','integration'];
  const dispatch = async (input:any) => {
    const url=new URL(input.path,'http://fixture');
    if (url.pathname === '/events') {
      const after=Number(url.searchParams.get('after'));
      if (cursor > after) delivered=Date.now();
      return {status:200,data:{events: Array.from({length:cursor},(_,i) => ({seq:i+1,resource_type:types[i%types.length],
        resource_id: types[i%types.length]==='session' ? `${f.ids.host}/${f.ids.sessions.implement}` : f.ids.work.email,
        kind:types[i%types.length]+'.updated'})).filter(event => event.seq>after),
        next_cursor:cursor,head_cursor:cursor,has_more:false,sync:{checkpoint:{cursor,token:`demo-${cursor}`}}}};
    }
    if (input.method === 'GET') reads.push({path:input.path,at:Date.now()}); else writes.push(input.path);
    return f.dispatch(input);
  };
  await mountConversation(page,false,dispatch);
  const route=view==='session' ? `#/session/${f.ids.host}/${f.ids.sessions.implement}`
    : view==='pending' ? '#/home' : ['operations','delivery','settings','projects'].includes(view) ? '#/'+view
    : view==='discussions' ? `#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}` : `#/pm/${f.ids.workspaceId}/${view}`;
  await page.goto('/dashboard/'+route); await expect(page.locator('#main h1')).toBeVisible();
  await page.waitForTimeout(1200); reads.length=0; cursor=20;
  // ACK proves the read promises for the complete page have settled.
  await expect.poll(() => page.evaluate(() => Object.values(localStorage).some(value => value.includes('demo-20')))).toBe(true);
  await page.waitForTimeout(600);
  expect(writes).toEqual([]);
  expect(reads.length, JSON.stringify(reads.map(read => read.path))).toBeLessThanOrEqual(budget);
  expect(reads.some(read => read.at >= delivered), 'A central read must start after the final event was delivered').toBe(true);
  expect(reads.filter(read => read.path === '/projects').length).toBeLessThanOrEqual(view === 'projects' ? 2 : 1);
  expect(reads.filter(read => read.path === `/pm/workspaces/${f.ids.workspaceId}`)).toEqual([]);
  expect(reads.filter(read => /\/threads\/[^/]+$/.test(read.path))).toEqual([]);
});
