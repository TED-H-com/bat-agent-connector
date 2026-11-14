"""Collection entry under the actual joined repair source guard."""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from bat_agent_connector import pm_access, pm_read_cache, pm_runs
from bat_agent_connector import pm_maintenance_repairs as repairs
from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector import pm_source_reads as reads
from tests import test_pm_joined_lifecycle as story
from tests.pm_joined_lifecycle_helpers import OWNER
from tests.test_pm_joined_lifecycle import bat_world, daemon, joined, world  # noqa: F401


async def test_real_repair_guard_keeps_nested_page_budgets(joined, mock, monkeypatch, record_property):  # noqa: F811
    observed, receipts = [], []
    class Checked(Exception):
        pass

    async def inspect(w, mock, monkeypatch):
        ops, wid = w['d'].ops, w['workspace_id']
        reports.schema(ops.journal)
        parent = repairs._operation(ops, w['ledger'].references['repair_operation_id'])
        access, record = repairs._access, reads.record
        inside = []
        @contextmanager
        def boundary(db, principal):
            before = reads._READ.get()
            with record(db, principal) as state:
                if inside:
                    observed.append((pm_read_cache.is_guarded(), state['budget'] is before['budget']))
                yield state
        def nested(reader, principal, op):
            if not inside:
                assert pm_read_cache.is_guarded()
                inside.append(True)
                try:
                    for who in (principal, replace(principal, credential_id='repair-page-credential'),
                                replace(principal, actor='repair-page-admin', admin=True)):
                        ops.list(principal=who, action='pm.workspace.create', limit=2)
                        pm_access.filter_events(ops.db, who, ops.journal.api_events(limit=2))
                        pm_runs.list_runs(ops, who, wid, limit=2)
                        receipts.append(reports.list_materializations(ops, who, wid))
                finally:
                    inside.pop()
            return access(reader, principal, op)
        with monkeypatch.context() as patch:
            patch.setattr(reads, 'record', boundary)
            patch.setattr(repairs, '_access', nested)
            cold = replace(OWNER, credential_id='repair-page-outer')
            with reads.scope(ops.db, cold):
                repairs._full_access(ops, cold, parent)
        assert observed and all(guard and shared for guard, shared in observed)
        # Joined repair fixture has no materialized reports. Nonempty guarded
        # receipt rows are covered by test_pm_nested_page_scopes.
        assert receipts and all(page['materializations'] == [] for page in receipts)
        raise Checked

    monkeypatch.setattr(story, 'reconnect_and_replan', inspect)
    with pytest.raises(Checked):
        await story.test_same_ledger_development_and_semantic_dependency(joined, mock, monkeypatch, record_property)
