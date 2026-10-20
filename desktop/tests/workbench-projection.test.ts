import {test} from 'node:test';
import assert from 'node:assert/strict';
import {workbenchProjection} from '../src/workbench-projection.js';

const session = {host: 'fixture', session_id: 's1', title: 'Native title', streaming: true, api_access: 'managed', provenance: 'connector_managed'};
test('stale observations and ended sessions cannot claim current execution', () => {
  assert.equal(workbenchProjection({session: {...session, stale: true, pending: {kind: 'ask_user'}}}).state, 'stale');
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
