"""Contradictory proof-bearing event fields never establish an impact edge."""
import json

import pytest

from bat_agent_connector.operations import OperationError
from tests import test_pm_work_start as starting
from tests.test_pm_plan_impact import mapped as mapped
from tests.test_pm_plan_impact import pm as pm
from tests.test_pm_plan_impact import read, selector
from tests.test_pm_plan_impact import ready as ready
from tests.test_pm_plan_impact import selected as selected
from tests.test_pm_plan_impact import start_ready as start_ready
from tests.test_pm_plan_impact import world as world


def contradict(ops, kind, field, value, oid=None):
    row = ops.db.execute('SELECT * FROM api_events WHERE kind=? AND (? IS NULL OR resource_id=?)',
                         (kind, oid, oid)).fetchone()
    body = json.loads(row['body'])
    if field == 'actor':
        ops.db.execute('UPDATE api_events SET actor=? WHERE seq=?', (value, row['seq']))
    else:
        body[field] = value
        ops.db.execute('UPDATE api_events SET body=? WHERE seq=?', (json.dumps(body), row['seq']))


@pytest.mark.parametrize(('kind', 'field', 'value'), [
    ('pm.issue.recorded', 'issue', {'id': 999, 'number': 999}),
    ('pm.issue.recorded', 'publication', None),
    ('pm.issue.recorded', 'state', 'cancelled'),
    ('pm.issue.recorded', 'external_issue_state', 'changed'),
    ('pm.issue.recorded', 'execution_authorized', True),
    ('pm.work_package.created', 'previous_package_id', 'pmwp_' + '0' * 32),
    ('pm.work_package.created', 'execution_authorized', True),
    ('pm.work_package.created', 'execution_spec', 'assigned'),
    ('operation.accepted', 'entry', 'contradictory-entry'),
    ('operation.accepted', 'actor', 'contradictory-actor'),
])
async def test_impact_contradictory_proving_event_fails_closed(selected, kind, field, value):
    w = selected
    contradict(w['d'].ops, kind, field, value,
               w['package']['operation_id'] if kind == 'operation.accepted' else None)
    with pytest.raises(OperationError, match='PM_IMPACT_EVIDENCE_INVALID'):
        read(w, w['impact_selector'])


async def test_impact_contradictory_attempt_finish_fails_closed(start_ready):
    w, ops = start_ready, start_ready['d'].ops
    op, _ = ops.create(starting.STARTER_OWNER, **await starting._start_envelope(w))
    done = await starting._advance_start(ops, op['operation_id'])
    assert done['status'] == 'succeeded', done
    params = await selector(w)
    row = ops.db.execute("SELECT body FROM api_events WHERE kind='work.start.succeeded'").fetchone()
    contradict(ops, 'work.start.succeeded', 'package_reused', not json.loads(row[0])['package_reused'])
    with pytest.raises(OperationError, match='PM_IMPACT_EVIDENCE_INVALID'):
        read(w, params)


@pytest.mark.parametrize('change', ['missing', 'boolean_type'])
async def test_impact_proof_fields_require_presence_and_exact_json_types(selected, change):
    w, ops = selected, selected['d'].ops
    row = ops.db.execute("SELECT seq,body FROM api_events WHERE kind='pm.work_package.created'").fetchone()
    body = json.loads(row['body'])
    if change == 'missing':
        assert body.pop('previous_package_id') is None
    else:
        body['execution_authorized'] = 0
    ops.db.execute('UPDATE api_events SET body=? WHERE seq=?', (json.dumps(body), row['seq']))
    with pytest.raises(OperationError, match='PM_IMPACT_EVIDENCE_INVALID'):
        read(w, w['impact_selector'])
