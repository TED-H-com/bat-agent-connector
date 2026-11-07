import {expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workbenchFixture, route, workspaceId, workId} from './pm-workbench-fixture.ts';
import {revealPmView} from './pm-navigation.ts';

export const entryId = 'shi_' + 'a'.repeat(32), linkId = 'hxl_' + 'b'.repeat(32);
export const text = 'Review 🦇 the keyboard fix.\n<img src=x onerror=alert(1)> stays literal.';
export function fixture() {
  const base = workbenchFixture();
  const entry = {entry_id:entryId, sequence:1, role:'assistant', observed_at:'2026-11-06T10:00:00Z',
    content_digest:'c'.repeat(64), parts:[{kind:'text', text}], clipped:true, omitted_parts:2};
  const entries = [entry];
  const source = {source_id:'example', adapter:'codex-history-jsonl-v1', status:'current', readable:true, stale:false,
    entries:1, collected_at:'2026-11-06T10:00:00Z', coverage:{history_complete:false}};
  const ref = {schema:'external_history_ref/v1', server_id:'pm-fixture', workspace_id:workspaceId, source_id:'example',
    entry_id:entryId, source_digest:'a'.repeat(64), content_digest:entry.content_digest, selection:{kind:'entry'}, excerpt_digest:'d'.repeat(64)};
  const link = () => ({link_id:linkId, workspace_id:workspaceId, work_item_id:workId(1), attempt_id:null,
    excerpt_id:'hxe_'+'e'.repeat(64), purpose:'reference', state:'active', version:1, created_by:'reader',
    created_at:'2026-11-06T10:00:00Z', creation_operation_id:'op_'+'1'.repeat(32), last_operation_id:'op_'+'1'.repeat(32), fixed_ref:structuredClone(ref)});
  const state = {deny:false, supported:true, admin:false, previewMismatch:false, workMismatch:false, failWork:false, lost:false, stale:false,
    hiddenPage:false, workPageSize:20, links:[] as any[], reads:[] as string[], previews:[] as any[], writes:[] as any[], ops:new Map<string,any>(),
    previewTransform:null as ((data:any)=>void)|null, holdPreview:null as Promise<void>|null, holdWork:null as Promise<void>|null};
  async function dispatch(input:any):Promise<any> {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname, endpoint = `/pm/workspaces/${workspaceId}`;
    if (input.method === 'GET') state.reads.push(input.path);
    const unavailable = {status:404, data:{error:{code:'PM_HISTORY_SOURCE_UNAVAILABLE', message:'History withdrawn'}}};
    if (path === endpoint+'/history') return state.deny ? unavailable : {status:200,data:{workspace_id:workspaceId,rows:[{...source,entries:entries.length}]}};
    if (path === endpoint+'/history/example') return state.deny ? unavailable : {status:200,data:{workspace_id:workspaceId,source:{...source,entries:entries.length},entries,next_after:null}};
    if (path === endpoint+'/history/example/associations') {
      if (state.deny) return unavailable;
      if (state.hiddenPage && Number(url.searchParams.get('after')) === 0) return {status:200,data:{links:[],next_after:5}};
      return {status:200,data:{links:structuredClone(state.links.filter(link=>link.fixed_ref.entry_id === url.searchParams.get('entry_id'))),next_after:null}};
    }
    if (path === endpoint+'/history-excerpt-preview') {
      state.previews.push(structuredClone(input));
      const selected = entries.find(entry=>entry.entry_id === input.body.entry_id)!;
      const range = input.body.selection;
      const parts = range.kind === 'text' ? [{kind:'text', text:[...selected.parts[range.part_index].text].slice(range.start_codepoint, range.end_codepoint).join('')}] : selected.parts;
      const result = state.deny ? unavailable : {status:200,data:{ref:{...structuredClone(ref), entry_id:selected.entry_id, content_digest:selected.content_digest, selection:structuredClone(range), excerpt_digest:range.kind === 'entry' ? ref.excerpt_digest : 'f'.repeat(64),
        ...(state.previewMismatch ? {workspace_id:'ws_'+'9'.repeat(32)} : {})},
        content:{role:selected.role, parts, clipped:selected.clipped, omitted_parts:selected.omitted_parts}, history_complete:false,execution_status:'unknown'}};
      state.previewTransform?.(result.data);
      if (state.holdPreview) await state.holdPreview;
      return result;
    }
    if (path.startsWith('/work-items/')) {
      const work = base.board.state.cards.find((card:any)=>path.endsWith(card.work_item_id))?.work;
      return {status:200,data:{work_item:structuredClone(work)}};
    }
    if (path === endpoint+'/kanban') {
      url.searchParams.set('limit',String(state.workPageSize));
      return base.dispatch({...input,path:url.pathname+url.search});
    }
    if (path.startsWith(endpoint+'/kanban/')) {
      if (state.failWork) return {status:503,data:{error:{code:'WORK_UNAVAILABLE',message:'Work read unavailable'}}};
      const result = await base.dispatch(input);
      if (state.workMismatch) result.data.card.binding.workspace_id = 'ws_'+'9'.repeat(32);
      if (state.holdWork && path.endsWith(workId(1))) await state.holdWork;
      return result;
    }
    if (path === '/operations' && input.method === 'POST' && input.body.action.startsWith('pm.history.')) {
      state.writes.push(structuredClone(input));
      let op = state.ops.get(input.idempotency_key);
      if (!op) {
        const action = input.body.action, unlink = action === 'pm.history.unlink';
        if (!state.stale) state.links = unlink ? [] : [{...link(),work_item_id:input.body.target.work_item_id,fixed_ref:structuredClone(input.body.params.fixed_ref)}];
        op = {...structuredClone(input.body), actor:base.base.state.actor, idempotency_key:input.idempotency_key,
          operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'), status:state.stale ? 'failed' : 'succeeded',
          result:state.stale ? null : {link_id:linkId,state:unlink ? 'withdrawn' : 'active',version:unlink ? 2 : 1},
          ...(state.stale ? {error:{code:'PM_HISTORY_LINK_CHANGED',message:'Work changed'}} : {})};
        state.ops.set(input.idempotency_key,op);
      }
      if (state.lost) {state.lost = false; return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }
    if (path.startsWith('/operations/')) {
      const op = [...state.ops.values()].find(row=>path.endsWith(row.operation_id));
      if (op) return {status:200,data:{operation:op}};
    }
    const result = await base.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.external_history = {version:1, ...(state.supported ? {relationships:{version:1,refs:['external_history_ref/v1'],actions:['pm.history.link','pm.history.unlink'],derive_ready:false}} : {})};
      caps.actions.push(...['pm.history.link','pm.history.unlink'].map(action=>({action,allowed:base.base.state.manage})));
      if (state.admin) caps.scopes.push('admin');
    }
    return result;
  }
  return {base, state, entry, entries, ref, link, dispatch};
}
export const detail = (page:Page) => page.locator('[data-history-detail]');
export const relations = (page:Page) => page.locator('[data-history-links]');
export async function reopen(page:Page) {
  await page.locator('[data-history-source]').click();
  await expect(relations(page)).toHaveCount(1);
  await relations(page).locator('summary').click();
}
export async function open(page:Page, native:boolean, f:ReturnType<typeof fixture>) {
  await mountConversation(page, native, f.dispatch); await page.goto(route);
  if ((page.viewportSize()?.width || 1440) < 1000) await page.locator('#workspace-menu').click();
  await (await revealPmView(page,'agents')).click(); await reopen(page);
}
export async function select(page:Page, id = workId(1)) {
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
  await relations(page).getByRole('combobox').selectOption(id);
  await expect(relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true})).toBeEnabled();
}
