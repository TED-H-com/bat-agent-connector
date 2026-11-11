"""Byte comparison captured on ce3df78a before adding optional Review fields."""
import itertools
import json
import os
import secrets
import time
import uuid
from pathlib import Path

from bat_agent_connector import pm_discussions, pm_governance, pm_plans, pm_workspace
from tests import test_pm_meetings as meetings
from tests.test_pm_discussions import OWNER, settle

sprint_ops = meetings.sprint_ops
ops = meetings.ops
FIXTURE = Path(__file__).parent / 'fixtures' / 'sprint-review-legacy.json'


async def test_legacy_envelopes_hashes_events_and_response_bytes(ops, monkeypatch):
    counter = itertools.count(1000)
    monkeypatch.setattr(uuid, 'uuid4', lambda: uuid.UUID(int=next(counter)))
    monkeypatch.setattr(time, 'time', lambda: 1800000000.0)
    monkeypatch.setattr(pm_workspace, 'server_id', lambda db: '00000000-0000-0000-0000-000000000001')
    tokens = itertools.count(1)
    monkeypatch.setattr(secrets, 'token_hex', lambda n: f'{next(tokens):0{n*2}x}')
    pm_governance.install(ops)
    data, _, snap, target = await meetings.meeting(ops, kind='review')
    captured = []

    async def save(body, reader):
        cursor = ops.journal.api_head()
        operation = (await settle(ops, ops.create(OWNER, **body)[0]))
        events = [dict(row) for row in ops.db.execute('SELECT * FROM api_events WHERE seq>? ORDER BY seq', (cursor,))]
        captured.append({'request': body, 'request_hash': ops.db.execute('SELECT request_hash FROM operations WHERE operation_id=?', (operation['operation_id'],)).fetchone()[0],
                         'result': operation['result'], 'events': events, 'read': reader(operation['result'])})
        return operation['result']

    fields = dict(title='Keep direction', description='Evidence remains incomplete', owner_actor=OWNER.actor,
                  source_refs=[{'kind':'requirement','id':'membership.login'}], rationale='Preserve the original direction')
    gov = await save(dict(action='pm.governance.record', target={'workspace_id':data['workspace_id']},
        params=dict(kind='change',root_cause_key='legacy-direction',snapshot_id=snap['snapshot_id'],**fields),
        preconditions={'expected_plan_version':pm_plans.head(ops.db,data['workspace_id'])['version']},idempotency_key='legacy.record'),
        lambda r: pm_governance.get_record(ops,OWNER,r['workspace_id'],r['record_id']))
    for action, params in [('revise',fields),('decide',{'disposition':'adopted_direction','rationale':'Keep direction explicitly'})]:
        gov = await save(dict(action='pm.governance.'+action,target={k:gov[k] for k in ('workspace_id','record_id')},
            params=params,preconditions={'expected_version':gov['version']},idempotency_key='legacy.'+action),
            lambda r: pm_governance.get_record(ops,OWNER,r['workspace_id'],r['record_id']))
    await meetings.note(ops,target)
    draft = await save(meetings.request(ops,target,'draft',**meetings.draft_params('review')),
                       lambda r: meetings.read(ops,target))
    await save(meetings.request(ops,target,'complete',draft_revision_id=draft['draft_revision_id'],
                               discussion_summary='Review recorded',decision_ids=['retain_direction']),lambda r:meetings.read(ops,target))
    ws = data['workspace_id']
    created = await settle(ops,ops.create(OWNER,action='pm.thread.create',target={'workspace_id':ws},params={'title':'Next idea'},
        preconditions={'expected_version':pm_workspace.get_workspace(ops.db,OWNER,ws)['version']},idempotency_key='legacy.idea')[0])
    tid=created['result']['thread_id']
    def thread(_):
        return pm_discussions.get_thread(ops,OWNER,ws,tid)
    inp=await save(dict(action='pm.input.record',target={'workspace_id':ws,'thread_id':tid},
        params={'slot':'scope','value':'Explore later','status':'unresolved','source_ref':{'kind':'statement','ref':'Review discussion'}},
        preconditions={'expected_version':1},idempotency_key='legacy.input'),thread)
    await save(dict(action='pm.thread.plan',target={'workspace_id':ws,'thread_id':tid},
        params={'selected_input_ids':[inp['input_id']],'rationale':'Explicit Planning only'},
        preconditions={'expected_version':2},idempotency_key='legacy.plan'),thread)
    # Server identity is created by fixture setup before the deterministic clock/UUIDs.
    server = pm_workspace.server_id(ops.db)
    encoded=json.dumps(captured,ensure_ascii=False,sort_keys=True,separators=(',',':')).replace(server,'fixed-server')+'\n'
    if os.environ.get('SRV_ACTUAL'):
        Path(os.environ['SRV_ACTUAL']).write_text(encoded)
    if os.environ.get('SRV_CAPTURE_LEGACY') == '1':
        FIXTURE.write_text(encoded)
    assert encoded == FIXTURE.read_text()
    assert len(captured)==7
