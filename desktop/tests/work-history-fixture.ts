import {workbenchFixture, workspaceId, workId} from './pm-workbench-fixture.ts';

export const linkId = (n=1) => 'hxl_'+String(n).repeat(32);
export const fullText = 'Review 🦇 the keyboard fix.\n<img src=x onerror=alert(1)> remains plain text.';
export function fixture(base = workbenchFixture()) {
  const makeLink = (n=1, work=1):any => ({link_id:linkId(n), workspace_id:workspaceId, work_item_id:workId(work), attempt_id:null,
    excerpt_id:'hxe_'+String(n).repeat(64), purpose:'reference', state:'active', version:1, created_by:'reader', created_at:'2026-11-07T10:00:00Z',
    fixed_ref:{schema:'external_history_ref/v1', server_id:'pm-fixture', workspace_id:workspaceId, source_id:'example', entry_id:'shi_'+String(n).repeat(32),
      source_digest:'a'.repeat(64), content_digest:'b'.repeat(64), excerpt_digest:'c'.repeat(64), selection:{kind:'entry'}}});
  const state = {supported:true, deny:0, hiddenFirst:false, nonAdvancing:false, pageSize:20, links:[makeLink(),makeLink(2,2)],
    reads:[] as string[], writes:[] as any[], linkReads:0, excerptReads:0, finalWithdraw:false,
    holdList:null as Promise<void>|null, holdExcerpt:null as Promise<void>|null,
    mutateExcerpt:null as ((data:any)=>void)|null, mutateDetail:null as ((data:any)=>void)|null};
  async function dispatch(input:any):Promise<any> {
    const url = new URL(input.path,'http://fixture'), path = url.pathname, endpoint = `/pm/workspaces/${workspaceId}`;
    if (input.method === 'GET') state.reads.push(input.path); else state.writes.push(input);
    const unavailable = {status:state.deny || 404,data:{error:{code:'PM_HISTORY_SOURCE_UNAVAILABLE',message:'History unavailable'}}};
    if (path.endsWith('/external-links')) {
      const work = path.split('/').at(-2), after = Number(url.searchParams.get('after') || 0);
      const matches = state.links.filter(row=>row.work_item_id === work);
      const links = state.hiddenFirst && after === 0 ? [] : matches.slice(state.hiddenFirst ? 0 : after, (state.hiddenFirst ? 0 : after)+state.pageSize);
      const next = state.nonAdvancing ? after : state.hiddenFirst && after === 0 ? 5 : after+links.length < matches.length ? after+links.length : null;
      const result = state.deny ? unavailable : {status:200,data:{links:structuredClone(links),next_after:next}};
      if (state.holdList) await state.holdList;
      return result;
    }
    if (path.startsWith(endpoint+'/external-links/')) {
      state.linkReads++;
      const link = structuredClone(state.links.find(row=>path.endsWith(row.link_id)));
      if (state.finalWithdraw && state.linkReads % 2 === 0) link.state = 'withdrawn';
      const data = {link}; state.mutateDetail?.(data);
      return state.deny || !link ? unavailable : {status:200,data};
    }
    if (path.startsWith(endpoint+'/history-excerpts/')) {
      state.excerptReads++;
      const link = state.links.find(row=>path.endsWith(row.excerpt_id));
      const data:any = {excerpt_id:link?.excerpt_id,ref:structuredClone(link?.fixed_ref),history_complete:false,execution_status:'unknown',
        content:{role:'assistant',parts:link?.fixed_ref.selection.kind === 'text' ? [{kind:'text',text:'🦇'}]
          : [{kind:'text',text:fullText},{kind:'tool_request',name:'shell'},{kind:'tool_result',name:null}],clipped:true,omitted_parts:2}};
      state.mutateExcerpt?.(data);
      const result = state.deny || !link ? unavailable : {status:200,data};
      if (state.holdExcerpt) await state.holdExcerpt;
      return result;
    }
    const result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.external_history = {version:1,...(state.supported ? {relationships:{version:1,refs:['external_history_ref/v1'],derive_ready:false}} : {})};
    }
    return result;
  }
  return {base,state,makeLink,dispatch};
}
