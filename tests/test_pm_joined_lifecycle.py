"""M4 phases share one daemon, workspace, source graph and evidence ledger.

This is an offline fixture. It makes no installed BAT, live-model or human
hardware acceptance claim. Later phases consume these exact original receipts.
"""
import json

import pytest

from bat_agent_connector import pm_session_quiescence
from tests.pm_joined_delivery_helpers import observe_release_workload, publish_release
from tests.pm_joined_lifecycle_helpers import (
    OWNER,
    assert_manual_preserved,
    complete_native_turn,
    develop_original_requirements,
    execute,
    finish_original_readers,
    prepare_world,
    provider_mux,
)
from tests.pm_joined_maintenance_helpers import enter_maintenance, repair_original_release, review_and_reopen
from tests.pm_joined_quality_helpers import shared_outcomes
from tests.test_checkpoints import MANUAL
from tests.test_pm_bat_operations import bat_world  # noqa: F401
from tests.test_repository_sync import world  # noqa: F401


@pytest.fixture
def daemon(world):  # noqa: F811
    return world['d']


@pytest.fixture
async def joined(world, bat_world, mock, monkeypatch):  # noqa: F811
    provider_mux(world, mock, monkeypatch, bat_world)
    return await prepare_world(world)


async def test_same_ledger_development_and_semantic_dependency(joined, mock, monkeypatch):
    w = await develop_original_requirements(joined, mock)
    assert w['ledger'].phases == ['idea_to_adopted_planning', 'development_and_quality']
    assert len(w['ledger'].references['execution_ids']) == 2
    assert_manual_preserved(w, mock)
    await finish_original_readers(w)
    await shared_outcomes(w, monkeypatch)
    await publish_release(w, mock)
    complete_native_turn(w, mock, MANUAL)
    ops = w['d'].ops
    link = ops.db.execute("SELECT * FROM work_item_links WHERE kind='session' AND ref=?", ('h1/' + MANUAL,)).fetchone()
    await execute(w, pm_session_quiescence.ACTION,
        {'workspace_id': w['workspace_id'], 'work_item_id': link['work_item_id']}, {'link_id': link['link_id']},
        {'expected_link_operation_id': link['link_operation'], 'expected_project_binding_id': w['project_binding_id'],
            'expected_session_identity_digest': pm_session_quiescence.sources(ops, OWNER, w['workspace_id'], link['work_item_id'])[0]['expected_session_identity_digest']})
    document = await observe_release_workload(w)
    manual = ops.db.execute('SELECT * FROM sessions_observed WHERE host=? AND session_id=?', ('h1', MANUAL)).fetchone()
    diagnostic = {key: json.loads(manual['body']).get(key) for key in ('loaded', 'streaming', 'pending', 'fields_stale',
        'specific_stale_reason', 'field_evidence', 'native_input_state', 'sdk_session_id', 'registry_origin', 'provenance')}
    assert document['classification'] == 'idle_candidate', {'reasons': document['blocking_reasons'],
        'manual': diagnostic, 'last_seen_at': manual['last_seen_at'], 'exclusions': document['exclusions']}
    await enter_maintenance(w, mock)
    await repair_original_release(w, mock)
    await review_and_reopen(w, mock)
