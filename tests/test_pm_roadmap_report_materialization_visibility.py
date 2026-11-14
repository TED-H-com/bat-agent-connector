"""Complete shared responses never disclose private freeze bytes or withdrawn reports."""
import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import (
    dashboard_sync,
    observation,
    pm_access,
    pm_discussions,
    pm_runs,
    pm_source_lineage,
)
from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_roadmap_report_materialization import (  # noqa: F401
    fixed as fixed_fixture,
)
from tests.test_pm_roadmap_report_materialization import (
    materialize,
    record,
)
from tests.test_pm_roadmap_report_materialization import pm as pm_fixture
from tests.test_pm_roadmap_report_materialization import ready as ready_fixture
from tests.test_pm_roadmap_report_materialization import spec_fixed as spec_fixed_fixture
from tests.test_pm_roadmap_report_materialization import world as world_fixture

fixed = fixed_fixture
pm = pm_fixture
ready = ready_fixture
spec_fixed = spec_fixed_fixture
world = world_fixture

SENTINELS = ('PRIVATE-REPORT-BODY-52', 'PRIVATE-MANIFEST-83', 'PRIVATE-SOURCE-MANIFEST-91')


def withdraw(w):
    w['d'].ops.db.execute("UPDATE tasks SET controller_authority='{}' WHERE task_id=?", (w['source_task']['task_id'],))


def responses(ops, wid, op, *, hidden=False):
    identity = op['operation_id']
    mid, run_id = op['result']['materialization_id'], op['external_refs']['run_id']
    run = pm_runs.get(ops.db, run_id)
    values = [ops.list(principal=OWNER, action=reports.ACTION),
        pm_access.filter_events(ops.db, OWNER, ops.journal.api_events(limit=500)),
        dashboard_sync.event_page(ops.journal, OWNER, limit=500),
        pm_runs.list_runs(ops, OWNER, wid), reports.list_materializations(ops, OWNER, wid),
        pm_discussions.list_threads(ops, OWNER, wid),
        pm_discussions.get_thread(ops, OWNER, wid, run['thread_id']),
        pm_discussions.context(ops, OWNER, wid, run['thread_id'])]
    for read in (lambda: ops.get(identity, principal=OWNER),
                 lambda: pm_runs.read(ops, OWNER, wid, run_id),
                 lambda: reports.get_materialization(ops, OWNER, wid, mid)):
        if hidden:
            with pytest.raises(OperationError) as error:
                read()
            assert error.value.status == 404
            values.append({'error': error.value.code, 'message': str(error.value)})
        else:
            values.append(read())
    for row in ops.db.execute("SELECT resource_type,resource_id FROM observation_resources WHERE resource_type='worktree'"):
        history = observation.Observation(ops.journal).history(row['resource_type'], row['resource_id'])
        values.append(pm_source_lineage.read_response(ops.db, OWNER, history))
    serialized = json.dumps(values, ensure_ascii=False)
    assert all(sentinel not in serialized for sentinel in SENTINELS)
    if hidden:
        # Traversal metadata may preserve IDs/counts; report-derived fields may not.
        assert mid not in serialized
        assert op['result']['output_digest'] not in serialized
        assert 'docs/pm/reports/' not in serialized
        assert all(row['operation_id'] != identity for row in values[0]['operations'])
        assert not values[4]['materializations']
    return values


async def test_private_freeze_all_local_reads_before_after_restart_and_withdrawal(fixed):
    w, ops = fixed, fixed['d'].ops
    op = await materialize(w)
    # Inject distinct sentinels into all private subtrees, including summary-key
    # names. The committed files stay untouched; this tests serialization only.
    row = ops.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (op['operation_id'],)).fetchone()
    frozen = json.loads(row[0])
    file_map = frozen['revision']['files']
    report_path = next(path for path in file_map if '/report.' in path)
    manifest_path = next(path for path in file_map if path.endswith('/manifest.json'))
    file_map[report_path] += SENTINELS[0]
    manifest = json.loads(file_map[manifest_path])
    manifest['path'] = SENTINELS[1]
    file_map[manifest_path] = json.dumps(manifest)
    frozen['report']['path'] = SENTINELS[1]
    frozen['source_manifest'].append({'path': SENTINELS[2], 'count': 777})
    raw = json.dumps(frozen)
    ops.db.execute("UPDATE operation_steps SET response=? WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (raw, op['operation_id']))
    assert all(value not in json.dumps(observation.operation_summary(ops.db, op['operation_id'], frozen)) for value in SENTINELS)
    responses(ops, w['workspace_id'], op)
    path = ops.db.execute('PRAGMA database_list').fetchone()['file']
    restarted = Journal(path)
    try:
        service = OperationService(restarted, actions=[])
        reports.install(service)
        responses(service, w['workspace_id'], op)
        withdraw(w)
        responses(ops, w['workspace_id'], op, hidden=True)
        responses(service, w['workspace_id'], op, hidden=True)
        assert restarted.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name='pm.roadmap.report.freeze'", (op['operation_id'],)).fetchone()[0] == raw
        assert restarted.db.execute('SELECT COUNT(*) FROM pm_roadmap_report_materializations').fetchone()[0] == 1
    finally:
        restarted.close()


async def test_full_closure_once_per_request_and_no_permission_cache_after_withdrawal(fixed, monkeypatch):
    w, ops = fixed, fixed['d'].ops
    first = await materialize(w)
    await materialize(w)
    original = reports.pm_roadmap_report_sources.load_fixed
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(reports.pm_roadmap_report_sources, 'load_fixed', counted)
    for read in (lambda: ops.list(principal=OWNER, action=reports.ACTION),
                 lambda: reports.list_materializations(ops, OWNER, w['workspace_id']),
                 lambda: pm_runs.list_runs(ops, OWNER, w['workspace_id']),
                 lambda: pm_access.filter_events(ops.db, OWNER, ops.journal.api_events(limit=500))):
        calls.clear()
        read()
        assert len(calls) == 1
    withdraw(w)
    assert not reports.source_visible(ops.db, OWNER, first)
    with pytest.raises(OperationError):
        record(w, first)
    # A caller-supplied projection must never replace canonical admission proof.
    assert not reports.source_visible(ops.db, OWNER, SimpleNamespace(operation_id='missing').__dict__)
