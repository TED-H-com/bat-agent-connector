import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {nativeFixture} from './pm-analysis-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {pmRoute, workspaceId} from './pm-fixture.ts';
import {mountProposal, proposalFixture} from './pm-plan-proposals-fixture.ts';
import {workPathsStrings} from '../src/work-paths-strings.js';

const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/pm-availability.json', import.meta.url), 'utf8'));
// Current producer always offers PM workspaces. This is an explicit compatibility
// omission of the optional feature from a real producer row, not producer output.
const unsupported = structuredClone(golden.owner); delete unsupported.capabilities.features.pm_workspaces;
const cases = {...golden, unsupported, refused: golden.owner, failed: golden.owner, network: golden.owner};
const errors: Record<string, {status: number, code: string}> = {refused: {status:403, code:'FORBIDDEN'}, failed:{status:503, code:'UNAVAILABLE'}, network:{status:0, code:'NETWORK_ERROR'}};
const words = {
  en: {unsupported:'PM workspaces are not supported by this Connector. Use a compatible Connector version.', failed:'PM availability could not be read. Try again, or ask the Connector operator.'},
  'zh-TW': {unsupported:'此 Connector 不支援 PM 工作區。請使用相容版本的 Connector。', failed:'無法讀取 PM 可用性。請重試，或詢問 Connector 管理員。'},
};
const expected = (name:string, lang:'en'|'zh-TW') => errors[name] ? words[lang].failed : name === 'unsupported' ? words[lang].unsupported
  : workPathsStrings[lang][({owner:'agents_preflight',observer:'agents_scope',executable_unavailable:'agents_executable',action_unregistered:'agents_action'} as any)[name] as keyof typeof workPathsStrings.en];
const capPath = (path:string) => path === '/pm/installation-capabilities' || path.endsWith('/capabilities') && path.startsWith('/pm/');

async function noGuess(page:Page) {
  await expect(page.locator('body')).not.toContainText('The PM is not connected');
  await expect(page.locator('body')).not.toContainText('PM 尚未連線');
}

test.describe.configure({mode:'parallel'});
for (const lang of ['en','zh-TW'] as const) for (const width of [390,1440]) {
  test.describe(`${lang} ${width}`, () => {
    test.use({locale:lang === 'en' ? 'en-US' : lang, viewport:{width,height:900}});
    for (const [name, source] of Object.entries(cases) as [string,any][]) {
      test(`${name}: Settings, discussion and proposal retain the availability cause`, async ({page}) => {
        const f = nativeFixture(); f.state.start = name !== 'observer'; f.state.manage = name !== 'observer'; f.state.plans = true;
        const requests:any[] = [];
        const dispatch = async (input:any) => {
          requests.push(structuredClone(input));
          const path = new URL(input.path,'http://fixture').pathname;
          if (capPath(path)) {
            if (errors[name] && name !== 'network') return {status:errors[name].status,data:{error:{code:errors[name].code,message:'Availability read rejected'}}};
            return {status:200,data:structuredClone(path === '/pm/installation-capabilities' ? source.installation : source.workspace)};
          }
          if (path.endsWith('/conversation-proposals')) return {status:200,data:{version:1,workspace_id:workspaceId,proposals:[],next_cursor:null,coverage:{visibility_filtered:false}}};
          const response = await f.dispatch(input);
          if (path === '/capabilities' || path === '/bootstrap') {
            const caps = path === '/bootstrap' ? response.data.capabilities : response.data;
            caps.scopes = source.capabilities.scopes;
            caps.actions = [...caps.actions.filter((row:any) => !source.capabilities.actions.some((actual:any) => row.action === actual.action)), ...source.capabilities.actions];
            if (name === 'unsupported') delete caps.features.pm_workspaces;
            else caps.features.pm_workspaces = structuredClone(source.capabilities.features.pm_workspaces);
          }
          return response;
        };
        await mountConversation(page,false,dispatch);
        if (name === 'network') await page.route('**/api/v1/pm/**', async (route) => {
          const path = new URL(route.request().url()).pathname.slice(7);
          if (capPath(path)) {requests.push({method:'GET',path}); await route.abort('failed');} else await route.fallback();
        });
        for (const from of ['', `&from=${encodeURIComponent(`#/pm/${workspaceId}/discussions`)}`]) {
          await page.goto('/dashboard/#/settings?section=agents'+from);
          const settings = page.locator('[data-settings-pm-agent]');
          await expect(settings).toContainText(expected(name,lang));
          if (errors[name]) {
            await expect(settings.locator('code')).toContainText(errors[name].code);
            await expect(page.locator('[data-pm-eligibility]')).not.toHaveText(workPathsStrings[lang].agents_unchecked);
          }
          await noGuess(page);
        }
        await page.goto(pmRoute);
        if (name === 'owner') await expect(page.locator('[data-discussion-reply]')).toBeVisible();
        else {
          await expect(page.locator('#main')).toContainText(expected(name,lang));
          await expect(page.locator('[data-discussion-reply]:visible, [data-discussion-ask]:visible')).toHaveCount(0);
          if (errors[name]) await expect(page.locator('#main')).toContainText(errors[name].code);
        }
        await noGuess(page);
        expect(requests.filter(row => row.method === 'POST')).toEqual([]);

        // Reuse the existing proposal component harness for its composer. The
        // actual parent error path is covered above; no parallel product logic.
        const p = proposalFixture(), original = p.dispatch;
        p.dispatch = async input => {
          const path = new URL(input.path,'http://fixture').pathname;
          if (path === '/fixture-state') return {status:200,data:{caps:{actor:'owner',...source.capabilities},capability:source.workspace,thread:p.state.thread,head:p.state.head}};
          if (capPath(path)) {
            if (name === 'network') throw Error('Network request failed');
            if (errors[name]) return {status:errors[name].status,data:{error:{code:errors[name].code,message:'Availability read rejected'}}};
            return {status:200,data:source.workspace};
          }
          return original(input);
        };
        await mountProposal(page,p,lang,{availability:true});
        if (name === 'owner') await expect(page.locator('[data-discussion-ask]')).toBeVisible();
        else {
          await expect(page.locator('#mount')).toContainText(expected(name,lang));
          await expect(page.locator('[data-discussion-ask]:visible')).toHaveCount(0);
          if (errors[name]) await expect(page.locator('#mount')).toContainText(errors[name].code);
        }
        await noGuess(page);
        expect(p.state.writes).toEqual([]);
      });
    }
  });
}
