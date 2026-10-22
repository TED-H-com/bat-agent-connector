"""One fail-closed workspace projection, separate from admission and choice editing."""
import copy
import json
from pathlib import Path

import pytest

from bat_agent_connector.pm_workspace_adapter import effective_route

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "pm-effective-route.json").read_text())


@pytest.mark.parametrize("name", GOLDEN)
def test_effective_route_matches_handler_projection(name):
    case = GOLDEN[name]
    body = case["workspace"]
    result = effective_route(body["workspace_adapter"], body["native_analysis"], body["bat_analysis"], case["capabilities"]["scopes"])
    assert result == body["effective_pm_route"]


@pytest.mark.parametrize("field", ["authorized", "eligible_for_preflight", "action_registered"])
@pytest.mark.parametrize("value", [None, False, 1, "true"])
def test_only_explicit_true_can_make_the_saved_route_ready(field, value):
    case = copy.deepcopy(GOLDEN["saved_native_ready"])
    body = case["workspace"]
    body["native_analysis"][field] = value
    result = effective_route(body["workspace_adapter"], body["native_analysis"], body["bat_analysis"], case["capabilities"]["scopes"])
    assert result["ready"] is False and result["eligible_adapters"] == []
    assert result["adapter"] == "codex-native-linux"


def test_no_choice_can_offer_bat_without_selecting_it():
    body = GOLDEN["saved_native_blocked"]["workspace"]
    choice = GOLDEN["unset_native_ready"]["workspace"]["workspace_adapter"]
    result = effective_route(choice, body["native_analysis"], body["bat_analysis"], ["observe", "manage", "start"])
    assert result["ready"] is True and result["eligible_adapters"] == ["bat-pm-linux-v1"]
    assert result["choice_source"] == "unset" and result["adapter"] is None and result["choice_ref"] is None


def test_permission_to_change_the_choice_is_not_preflight_eligibility():
    case = copy.deepcopy(GOLDEN["saved_native_ready"])
    body = case["workspace"]
    body["workspace_adapter"].update(available=False, reason="owner_and_scopes_required")
    result = effective_route(body["workspace_adapter"], body["native_analysis"], body["bat_analysis"], case["capabilities"]["scopes"])
    assert result["ready"] is True
