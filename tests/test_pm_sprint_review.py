"""Guided Review freezes evidence, never creates acceptance or execution."""
import copy

import pytest

from bat_agent_connector import pm_meetings, pm_source_lineage
from bat_agent_connector.operations import OperationError
from tests import test_pm_meetings as m
from tests.test_pm_discussions import OWNER, READER

sprint_ops = m.sprint_ops
ops = m.ops


def basis(**changes):
    return dict(version=1, results=[], feedback_items=[], governance_revision_ids=[], follow_up_ref=None, **changes)


async def prepared(ops):
    pm_source_lineage.schema(ops.journal)
    data, sprint, snap, target = await m.meeting(ops, kind='review')
    note = await m.note(ops, target)
    selected = basis()
    selected['feedback_items'] = [{'id':'feedback_one','source_ref':{'kind':'meeting_note','note_id':note['note_id']}}]
    return data, sprint, snap, target, selected


async def save(ops, target, selected, **extra):
    params=m.draft_params('review',decisions=[],review_basis=selected,**extra)
    params['output']['feedback']=[]
    return await m.apply(ops,target,'draft',**params)


async def test_freezes_exact_feedback_and_missing_acceptance_as_gaps(ops):
    _, _, _, target, selected = await prepared(ops)
    await save(ops,target,selected)
    view=m.read(ops,target)
    guided=view['draft']['document']['guided_review']
    assert guided['feedback'][0]['content']=='We discussed the evidence and retained the open questions.'
    assert guided['feedback'][0]['reference']==selected['feedback_items'][0]['source_ref']
    assert guided['gaps'] and not guided['results']
    assert all(not row['qualified'] for row in guided['requirements'])
    before=copy.deepcopy(view['draft'])
    await m.note(ops,target)
    assert m.read(ops,target)['draft']==before


async def test_read_paths_do_not_write(ops):
    _, _, _, target, selected=await prepared(ops)
    await save(ops,target,selected)
    tables=[r[0] for r in ops.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    def counts():
        return {t:ops.db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables}
    original=counts(),ops.db.total_changes
    for who in (OWNER,READER):
        m.read(ops,target,who)
        pm_meetings.list_meetings(ops,who,target['workspace_id'])
        for kind in pm_meetings.HISTORY:
            pm_meetings.list_history(ops,who,**target,kind=kind)
    assert (counts(),ops.db.total_changes)==original


@pytest.mark.parametrize('field',['results','feedback_items','governance_revision_ids'])
async def test_selection_one_past_limit_refuses_before_sources(ops,field):
    _, _, _, target, selected=await prepared(ops)
    selected[field]=[{}]*21
    with pytest.raises(OperationError,match='INVALID_PARAMS'):
        await save(ops,target,selected)


async def test_twenty_feedback_items_preserve_cjk_and_whitespace(ops):
    _, _, _, target, selected=await prepared(ops)
    selected['feedback_items']=[]
    for i in range(20):
        note=await m.apply(ops,target,'note',content=f'  原文 {i}\n保留空白  ')
        selected['feedback_items'].append({'id':f'feedback_{i}','source_ref':{'kind':'meeting_note','note_id':note['note_id']}})
    await save(ops,target,selected)
    doc=m.read(ops,target)['draft']['document']
    assert len(doc['guided_review']['feedback'])==20
    assert doc['output']['feedback'][0]=='  原文 0\n保留空白  '


@pytest.mark.parametrize('kind',['results','feedback_items','governance_revision_ids','issues'])
def test_exact_selection_limits(kind):
    from bat_agent_connector.pm_sprint_review import validate
    selected=basis()
    for n in (20,21):
        if kind=='results':
            selected[kind]=[dict(attempt_id='att_'+f'{i:032x}',result_id='res_'+f'{i:032x}',digest='sha256:'+'a'*64,assessment_ref=None) for i in range(n)]
        elif kind=='feedback_items':
            selected[kind]=[dict(id=f'feedback_{i}',source_ref={'kind':'meeting_note','note_id':'pmmnote_'+f'{i:032x}'}) for i in range(n)]
        elif kind=='governance_revision_ids':
            selected[kind]=['pmgrev_'+f'{i:032x}' for i in range(n)]
        else:
            selected['follow_up_ref']={'intake_id':'pmfu_'+'a'*32,'document_digest':'a'*64,'issue_ids':['pmfui_'+f'{i:032x}' for i in range(n)]}
        if n==20:
            validate(selected)
        else:
            with pytest.raises(OperationError):
                validate(selected)


def test_exact_source_byte_budget_with_cjk():
    from bat_agent_connector.pm_sprint_review import MAX_SOURCE_BYTES, source_budget
    # JSON quotes are two bytes; no codepoint/UTF-16 assumption.
    exact='界'*((MAX_SOURCE_BYTES-2)//3)+'a'*((MAX_SOURCE_BYTES-2)%3)
    source_budget(exact)
    with pytest.raises(OperationError,match='PM_SPRINT_REVIEW_SOURCE_LIMIT') as error:
        source_budget(exact+'a')
    assert error.value.status==413


async def test_draft_byte_budget_and_questions(ops):
    from bat_agent_connector import pm_snapshots
    _, _, _, target, selected=await prepared(ops)
    params=m.draft_params('review',decisions=[],review_basis=selected,unresolved_questions=['Unknown']*20)
    params['output']['feedback']=[]
    meeting=pm_meetings._meeting(ops.db,target['workspace_id'],target['meeting_id'])
    context,snapshot=pm_meetings._access(ops,OWNER,meeting)
    params['rationale']='x'
    original=pm_meetings._draft(ops,OWNER,meeting,context['document'],snapshot,params)
    # Use all bounded outcome/environment/next-step fields to approach the whole-document limit.
    params['output']['environment_changes']=['界'*4000]*14
    oversized=copy.deepcopy(params)
    oversized['output']['next_steps']=['界'*4000]*20
    with pytest.raises(OperationError,match='PM_MEETING_TOO_LARGE'):
        pm_meetings._draft(ops,OWNER,meeting,context['document'],snapshot,oversized)
    assert len(pm_snapshots._json(original).encode())<200000
    params['unresolved_questions'].append('One too many')
    with pytest.raises(OperationError,match='INVALID_PARAMS'):
        pm_meetings._draft(ops,OWNER,meeting,context['document'],snapshot,params)


def test_meeting_history_index_plan_and_schema_idempotency(ops):
    import json
    import os
    from pathlib import Path
    query='EXPLAIN QUERY PLAN SELECT sequence FROM pm_meeting_drafts WHERE meeting_id=? AND sequence>? ORDER BY sequence LIMIT ?'
    version=ops.db.execute('PRAGMA user_version').fetchone()[0]
    ops.db.execute('DROP INDEX pm_meeting_drafts_by_meeting')
    before=[tuple(r) for r in ops.db.execute(query,('meeting',0,21))]
    pm_meetings.schema(ops.journal)
    after=[tuple(r) for r in ops.db.execute(query,('meeting',0,21))]
    pm_meetings.schema(ops.journal)
    assert ops.db.execute('PRAGMA user_version').fetchone()[0]==version
    assert not any('pm_meeting_drafts_by_meeting' in str(r) for r in before)
    assert any('pm_meeting_drafts_by_meeting' in str(r) for r in after)
    if os.environ.get('BAC_EVIDENCE'):
        (Path(os.environ['BAC_EVIDENCE'])/'r2'/'index-plan.json').write_text(json.dumps({'query':query,'before':before,'after':after},indent=2)+'\n')
