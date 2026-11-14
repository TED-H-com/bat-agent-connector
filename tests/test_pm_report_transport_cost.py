"""Loader bounds over complete HTTP, socket RPC and MCP requests, not handlers."""
import json
import time

import pytest

from bat_agent_connector import pm_report_read_work, pm_source_lineage
from bat_agent_connector import pm_roadmap_report_materialization as reports
from tests.pm_report_transport_helpers import EVENT_KIND, report_rows, transports
from tests.test_api_v1 import token
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_roadmap_report_materialization import fixed as fixed_fixture
from tests.test_pm_roadmap_report_materialization import pm, ready, spec_fixed, world  # noqa: F401
from tests.test_pm_roadmap_report_materialization_visibility import withdraw

fixed = fixed_fixture


@pytest.mark.parametrize('distinct', [1, 20, 50, 500])
async def test_complete_report_transport_loader_bounds(fixed, monkeypatch, distinct, record_property):
    w, ops = fixed, fixed['d'].ops
    count = 500 if distinct == 1 else distinct
    first, report_ids, start = await report_rows(w, distinct=distinct, count=count)
    original_loader = reports.pm_roadmap_report_sources.load_fixed
    original_charge = pm_report_read_work.charge
    original_request, original_response = pm_source_lineage.read_request, pm_source_lineage.read_response
    phase, charges, calls = 'handler', [], []
    def loader(*args, **kwargs):
        calls.append(phase)
        return original_loader(*args, **kwargs)
    def charge(db, records, byte_length):
        charges.append((phase, records, byte_length))
        return original_charge(db, records, byte_length)
    def request(*args, **kwargs):
        nonlocal phase
        phase = 'selector'
        try:
            return original_request(*args, **kwargs)
        finally:
            phase = 'handler'
    def response(*args, **kwargs):
        nonlocal phase
        phase = 'barrier'
        try:
            return original_response(*args, **kwargs)
        finally:
            phase = 'handler'
    monkeypatch.setattr(reports.pm_roadmap_report_sources, 'load_fixed', loader)
    monkeypatch.setattr(pm_report_read_work, 'charge', charge)
    monkeypatch.setattr(pm_source_lineage, 'read_request', request)
    monkeypatch.setattr(pm_source_lineage, 'read_response', response)
    auth = token(w['d'], OWNER.actor, *OWNER.scopes)
    authority = ops.db.execute('SELECT controller_authority FROM tasks WHERE task_id=?',
        (w['source_task']['task_id'],)).fetchone()[0]
    measurements = []
    async with transports(w, monkeypatch) as read:
        for hidden in (False, True):
            if hidden:
                withdraw(w)
            for role, credential in (('member', auth), ('admin', w['d']._admin_token)):
                for transport in ('http', 'rpc', 'mcp'):
                    for reader in ('operations', 'events', 'runs', 'receipts', 'selected_events'):
                        actual = 'events' if reader == 'selected_events' else reader
                        params = {'operations': {'action': reports.ACTION, 'limit': 200},
                            'events': {'after': start, 'kind': EVENT_KIND, 'limit': 500},
                            'runs': {'workspace_id': w['workspace_id'], 'limit': 100},
                            'receipts': {'workspace_id': w['workspace_id'], 'limit': 50},
                            'selected_events': {'after': start, 'kind': EVENT_KIND, 'limit': 500,
                                'resource_type': 'operation', 'resource_id': first['operation_id']}}[reader]
                        pages, seen, elapsed, totals = 0, set(), 0, [0, 0, 0]
                        page_calls, page_rows, statuses = [], [], []
                        while True:
                            calls.clear()
                            charges.clear()
                            begin = time.perf_counter()
                            status, value = await read(transport, actual, credential, params.copy())
                            elapsed += time.perf_counter() - begin
                            pages += 1
                            statuses.append(status)
                            totals[0] += len(calls)
                            totals[1] += sum(row[1] for row in charges)
                            totals[2] += sum(row[2] for row in charges)
                            page_calls.append({key: calls.count(key) for key in ('selector', 'handler', 'barrier')})
                            if reader == 'selected_events' and hidden:
                                assert status == {'http': 404, 'rpc': 400, 'mcp': 1}[transport], value
                                assert 'NOT_FOUND' in json.dumps(value)
                                assert calls == ['selector']
                                page_rows.append(0)
                                break
                            assert status == (0 if transport == 'mcp' else 200), (reader, value)
                            collection = 'materializations' if actual == 'receipts' else actual
                            rows = value[collection]
                            found = {row.get('operation_id', row.get('resource_id')) for row in rows} & report_ids
                            assert not (found & seen)
                            seen.update(found)
                            page_rows.append(len(found))
                            if distinct == 1:
                                assert len(calls) <= (3 if reader == 'selected_events' else 2), page_calls
                                assert all(calls.count(key) <= 1 for key in ('selector', 'handler', 'barrier'))
                                if found:
                                    assert calls == (['selector', 'handler', 'barrier'] if reader == 'selected_events'
                                        else ['handler', 'barrier'])
                            else:
                                assert calls.count('handler') <= 20
                                assert calls.count('barrier') <= 20
                            if actual == 'operations':
                                if value['next_before'] is None:
                                    assert value['next_before_id'] is None
                                    break
                                cursor = {'before': value['next_before'], 'before_id': value['next_before_id']}
                            elif actual == 'events':
                                if not value['has_more']:
                                    break
                                cursor = {'after': value['next_cursor']}
                            else:
                                next_value = value['next_after' if actual == 'receipts' else 'next_cursor']
                                if next_value is None:
                                    break
                                cursor = {'after': next_value}
                            assert any(params.get(key) != val for key, val in cursor.items())
                            params.update(cursor)
                            assert pages < 100
                        assert len(seen) == (0 if hidden else 1 if reader == 'selected_events' else count)
                        if distinct == 1 and not hidden:
                            assert max(page_rows) == {'operations': 200, 'events': 500, 'runs': 100,
                                'receipts': 50, 'selected_events': 1}[reader]
                        measurements.append({'distinct': distinct, 'records': count, 'withdrawn': hidden,
                            'role': role, 'transport': transport, 'reader': reader, 'closures': totals[0],
                            'charged_records': totals[1], 'charged_bytes': totals[2], 'returned': len(seen),
                            'pages': pages, 'seconds': round(elapsed, 6), 'page_calls': page_calls,
                            'page_rows': page_rows, 'statuses': statuses})
            ops.db.execute('UPDATE tasks SET controller_authority=? WHERE task_id=?',
                (authority, w['source_task']['task_id']))
    # The runner may export JUnit properties; tests never write dispatch evidence.
    record_property('report_transport_measurements', json.dumps(measurements))
