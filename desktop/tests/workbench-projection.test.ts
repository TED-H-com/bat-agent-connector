import {test} from 'node:test';
import assert from 'node:assert/strict';
import {workbenchProjection} from '../src/workbench-projection.js';

const session = {host: 'fixture', session_id: 's1', title: 'Native title', streaming: true, api_access: 'managed', provenance: 'connector_managed'};
test('stale observations and ended sessions cannot claim current execution', () => {
  assert.equal(workbenchProjection({session: {...session, stale: true, pending: {kind: 'ask_user'}}}).state, 'wait');
  assert.equal(workbenchProjection({session: {...session, state: {lifecycle: 'ended'}}}).state, 'unknown');
  assert.equal(workbenchProjection({session: {...session, streaming: false}}).state, 'idle');
  assert.equal(workbenchProjection({session: {...session, streaming: undefined}}).state, 'unknown');
});
test('identity and purpose use the explicit work record across attempts', () => {
  const record = {work_item: {work_item_id: 'wi_fixed', title: 'Fix search', goal: 'Keep the query', state: 'doing'}};
  const first = workbenchProjection({session}, record);
  const second = workbenchProjection({session: {...session, session_id: 'attempt2'}}, record);
  assert.equal(first.id, second.id); assert.equal(second.title, 'Fix search'); assert.equal(second.goal, 'Keep the query');
  assert.equal(first.recordState, 'doing');
});
test('recorded relationships retain source and type without inferring completion or dependencies', () => {
  const projection = workbenchProjection({session, work_items: [{work_item_id: 'wi_one', title: 'Linked'}, {work_item_id: 'wi_one', title: 'Linked'}],
    started_from: {source_host: 'original', source_session_id: 'manual', commit_sha: 'a'.repeat(40)}});
  assert.deepEqual(projection.edges.map(edge => edge.type), ['linked', 'derived']);
  assert.ok(projection.edges.every(edge => edge.source === '/sessions/fixture/s1'));
  assert.equal(workbenchProjection({session: {...session, provenance: 'manual'}}).managed, false);
  assert.equal(workbenchProjection({}), null);
});
test('GitHub source closure supplies the Work record state while runtime stays independent', () => {
  const record = {work_item: {work_item_id: 'wi_fixed', title: 'Closed source', state: 'doing', display_state: 'github_closed',
    source_lane: 'github_closed', completion: {display_state: 'doing', pending: false, approved: false},
    external: {source: 'github', state: 'closed', board_lane: 'github_closed'}}};
  const result = workbenchProjection({session}, record);
  assert.equal(result.recordState, 'github_closed');
  assert.equal(result.external, record.work_item.external);
  assert.equal(result.state, 'running');
});

test('managed attention without a pending prompt uses the existing shared status', () => {
  for(const status of ['needs_you','blocked','uncertain','needs_attention'])
    assert.equal(workbenchProjection({session:{...session,status,streaming:false}}).state,'wait',status);
});

test('producer facets agree across the workbench, result-source adapter and diagnostics', async () => {
  const {readFileSync} = await import('node:fs');
  const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/session-runtime.json', import.meta.url),'utf8'));
  const {sessionFacets,sessionStatus,sessionActivity,runtimeStale} = await import('../src/state/sessions.js');
  const {resultSourceFacets} = await import('../src/result-sources.js');
  const {sessionFacts} = await import('../src/diagnostics.js');
  for(const [name,entry] of Object.entries(golden) as [string,any][]){
    const row=entry.listed,facets=sessionFacets(row),projection=workbenchProjection({session:row})!;
    assert.deepEqual(projection.facets,facets,name);
    const status=sessionStatus(row);
    assert.equal(projection.state,['asking_you','needs_you'].includes(status!)?'wait':status|| (facets.activity==='not_streaming'?'idle':'unknown'),name);
    const result=resultSourceFacets(entry.observation);
    for(const key of ['activity','attention','staleness'])assert.deepEqual(result[key],facets[key],`${name}: ${key}`);
    assert.equal(sessionFacts(row).stale,runtimeStale(row),name);
    if(facets.staleness.runtime)assert.notEqual(sessionActivity(row).key,'obs_value_streaming');
  }
});

test('diagnostics includes nested activity staleness without turning missing counts into zero', async () => {
  const {sessionFacts} = await import('../src/diagnostics.js');
  assert.equal(sessionFacts({state:{evidence:{activity:{stale:true}}}}).stale,true);
  assert.equal(sessionFacts({state:{evidence:{activity:{stale:false}}}}).stale,false);
  assert.equal(sessionFacts({}).stale,null);
});

test('result adapter keeps count pending, unknown activity and stale availability independent', async () => {
  const {resultSourceFacets} = await import('../src/result-sources.js');
  for(const pending of [{kind:'permission'},2]){
    const facets=resultSourceFacets({status:'available',stale:true,streaming:true,pending});
    assert.equal(facets.activity,'unknown');
    assert.equal(facets.attention,typeof pending==='object'?'asking_you':'needs_you');
    assert.equal(facets.staleness.runtime,true);
  }
  assert.equal(resultSourceFacets({status:'available',streaming:null,pending:0}).activity,'unknown');
  assert.equal(resultSourceFacets({status:'unavailable',streaming:true,pending:{kind:'ask_user'}}).attention,'asking_you');
  assert.equal(resultSourceFacets({status:'unavailable',streaming:true}).activity,'unknown');
});
