"""M4 phases share one daemon, workspace, source graph and evidence ledger.

This is an offline fixture. It makes no installed BAT, live-model or human
hardware acceptance claim. Later phases consume these exact original receipts.
"""
import pytest

from tests.pm_joined_delivery_helpers import observe_release_workload, publish_release
from tests.pm_joined_lifecycle_helpers import (
    assert_manual_preserved,
    complete_native_turn,
    develop_original_requirements,
    prepare_world,
    provider_mux,
)
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


async def test_same_ledger_development_and_semantic_dependency(joined, mock):
    w = await develop_original_requirements(joined, mock)
    assert w['ledger'].phases == ['idea_to_adopted_planning', 'development_and_quality']
    assert len(w['ledger'].references['execution_ids']) == 2
    assert_manual_preserved(w, mock)
    await publish_release(w, mock)
    complete_native_turn(w, mock, MANUAL)
    document = await observe_release_workload(w)
    assert document['classification'] == 'idle_candidate', document['blocking_reasons']
