"""Governance proof, responsible actors and original proposal sources."""
import copy
import uuid

import pytest

from bat_agent_connector import pm_governance as g
from bat_agent_connector import pm_plans, pm_sprints
from bat_agent_connector.operations import OperationError
from tests import test_pm_meetings as m
from tests.test_pm_discussions import CONTRIBUTOR, OWNER, settle
from tests.test_pm_sprint_review import prepared

sprint_ops = m.sprint_ops
ops = m.ops


async def proposal(ops):
    g.install(ops)
    data, sprint, snap, target, basis = await prepared(ops)
    decision = m.draft_params('review')['decisions'][0]
    decision.update(owner_actor=CONTRIBUTOR.actor, follow_up_ids=['check_quality'])
    params = m.draft_params('review',decisions=[decision],follow_ups=[m.follow_up()],review_basis=basis)
    params['output']['feedback'] = []
    await m.apply(ops,target,'draft',who=CONTRIBUTOR,**params)
    fixed = m.read(ops,target)['draft']
    ref = dict(meeting_id=target['meeting_id'],draft_revision_id=fixed['revision_id'],draft_digest=fixed['document_digest'],decision_id=decision['id'])
    params = dict(kind='change',root_cause_key='retain-direction',snapshot_id=snap['snapshot_id'],
        title='Retain direction',description=decision['text'],owner_actor=CONTRIBUTOR.actor,
        source_refs=[dict(kind='requirement',id='membership.login')],rationale='Keep evidence gaps visible', review_proposal_ref=ref)
    request=dict(action='pm.governance.record',target={'workspace_id':data['workspace_id']},params=params,
        preconditions={'expected_plan_version':pm_plans.head(ops.db,data['workspace_id'])['version']},idempotency_key=uuid.uuid4().hex)
    record=(await settle(ops,ops.create(CONTRIBUTOR,**request)[0]))['result']
    return target, fixed, record, request


async def decided(ops):
    target, fixed, record, request=await proposal(ops)
    request=dict(action='pm.governance.decide',target={k:record[k] for k in ('workspace_id','record_id')},
        params={'disposition':'adopted_direction','rationale':'Retain direction and investigate the gaps'},
        preconditions={'expected_version':record['version']},idempotency_key=uuid.uuid4().hex)
    for actor in (CONTRIBUTOR,m.ADMIN):
        with pytest.raises(OperationError,match='FORBIDDEN'):
            ops.create(actor,**request)
    record=(await settle(ops,ops.create(OWNER,**request)[0]))['result']
    revision=g._revision(ops.db,record['revision_id'])
    proof={'decision_id':'retain_direction','revision_id':revision['revision_id'],'document_digest':revision['document_digest']}
    return target,fixed,proof


async def completed(ops):
    target,fixed,proof=await decided(ops)
    request=m.request(ops,target,'complete',draft_revision_id=fixed['revision_id'],discussion_summary='Original discussion',
        decision_ids=['retain_direction'],governance_decisions=[proof])
    before=m.execution_state(ops)
    for actor in (CONTRIBUTOR,m.ADMIN):
        with pytest.raises(OperationError,match='FORBIDDEN'):
            ops.create(actor,**request)
    result=await settle(ops,ops.create(OWNER,**request)[0])
    assert m.execution_state(ops)==before
    return target,result,request


async def test_responsible_governance_actor_owner_and_follow_up(ops):
    target,result,_=await completed(ops)
    doc=m.read(ops,target)['completion']['document']
    assert doc['decisions'][0]['recorded_by']==OWNER.actor
    assert doc['decisions'][0]['owner_actor']==CONTRIBUTOR.actor
    assert doc['decisions'][0]['follow_up_ids']==['check_quality']
    assert doc['governance_decisions'][0]['disposition']=='adopted_direction'
    assert not doc['scope_applied'] and not result['result']['execution_authorized']
    await m.apply(ops,target,'follow_up.record',follow_up_id='check_quality',status='blocked',result='Acceptance still missing')


async def test_missing_forged_and_unselected_proof_never_completes(ops):
    target,fixed,proof=await decided(ops)
    base=m.request(ops,target,'complete',draft_revision_id=fixed['revision_id'],discussion_summary='Keep gaps visible',decision_ids=['retain_direction'])
    with pytest.raises(OperationError,match='PM_SPRINT_REVIEW_DECISION_REQUIRED'):
        ops.create(OWNER,**base)
    wrong=copy.deepcopy(base)
    wrong['params']['governance_decisions']=[dict(proof,document_digest='0'*64)]
    with pytest.raises(OperationError,match='PM_SPRINT_REVIEW_REFERENCE_MISMATCH'):
        ops.create(OWNER,**wrong)
    wrong['params']['governance_decisions']=[proof]
    wrong['params']['decision_ids']=[]
    with pytest.raises(OperationError,match='PM_SPRINT_REVIEW_DECISION_REQUIRED'):
        ops.create(OWNER,**wrong)


async def test_administrator_generic_membership_override_survives(ops):
    _,_,_,target,basis=await prepared(ops)
    assert pm_sprints._role(ops.db,target['workspace_id'],m.ADMIN.actor) is None
    await m.apply(ops,target,'note',who=m.ADMIN,content='Administrator records discussion only')
    params=m.draft_params('review',decisions=[],review_basis=basis)
    params['output']['feedback']=[]
    await m.apply(ops,target,'draft',who=m.ADMIN,**params)
    fixed=m.read(ops,target)['draft']
    with pytest.raises(OperationError,match='FORBIDDEN'):
        ops.create(m.ADMIN,**m.request(ops,target,'complete',draft_revision_id=fixed['revision_id'],discussion_summary='Cannot decide',decision_ids=[]))


async def test_completion_and_governance_reads_and_restart_replay(ops,tmp_path):
    from bat_agent_connector import pm_meetings
    from bat_agent_connector.operations import OperationService
    from bat_agent_connector.task_journal import Journal
    target,result,request=await completed(ops)
    tables=[r[0] for r in ops.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    def counts(db):
        return [db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables]
    before=ops.db.total_changes,counts(ops.db)
    completion=m.read(ops,target)['completion']['document']
    rid=completion['governance_decisions'][0]['record_id']
    g.get_record(ops,OWNER,target['workspace_id'],rid)
    g.list_records(ops,OWNER,target['workspace_id'])
    g.list_history(ops,OWNER,target['workspace_id'],rid)
    assert (ops.db.total_changes,counts(ops.db))==before
    reopened=Journal(tmp_path/'sprints.db')
    try:
        recovered=OperationService(reopened)
        pm_meetings.install(recovered)
        g.install(recovered)
        before=counts(reopened.db)
        original,fresh=recovered.create(OWNER,**request)
        assert not fresh and original['operation_id']==result['operation_id']
        assert original['result']==result['result']
        assert counts(reopened.db)==before
    finally:
        reopened.close()
