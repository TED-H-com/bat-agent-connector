"""Every admission source must participate in the explicit action inventory."""
import pytest

from bat_agent_connector import pm_delivery_lineage, pm_source_lineage
from bat_agent_connector.operations import ActionDef
from tests.test_api_v1 import daemon as daemon_fixture
from tests.test_pm_workspace_wip_classification import test_lineage_action_inventory as check_inventory

daemon = daemon_fixture


@pytest.mark.parametrize('source', ['derived', 'delivery', 'registered_pm', 'repository_exception'])
def test_each_admission_inventory_source_detects_a_new_action(daemon, monkeypatch, source):
    check_inventory(daemon)
    if source == 'derived':
        monkeypatch.setattr(pm_source_lineage, 'DERIVED_ACTIONS',
            pm_source_lineage.DERIVED_ACTIONS | {'fixture.extra_derived'})
    elif source == 'delivery':
        monkeypatch.setattr(pm_delivery_lineage, 'ACTIONS',
            pm_delivery_lineage.ACTIONS | {'fixture.extra_delivery'})
    elif source == 'registered_pm':
        async def run(ctx):
            return {}
        daemon.ops.register(ActionDef('pm.fixture_extra', 'observe', 'Synthetic inventory mutation', run))
    else:
        assert hasattr(pm_source_lineage, 'HANDOFF_ACTIONS'), 'Derive the repository exception from admission'
        monkeypatch.setattr(pm_source_lineage, 'HANDOFF_ACTIONS',
            pm_source_lineage.HANDOFF_ACTIONS | {'repository.fixture_extra'})
    with pytest.raises(AssertionError):
        check_inventory(daemon)
