"""Profiles describe professional PM work without advertising unimplemented modes."""

import pytest

from bat_agent_connector import pm_profiles
from bat_agent_connector.operations import OperationError


def test_profiles_are_versioned_context_first_and_only_local_idea_planning_are_available():
    catalog = pm_profiles.catalog()
    assert len(catalog["profiles"]) == 9
    assert catalog["executable_modes"] == ["idea", "planning"]
    for profile in catalog["profiles"]:
        assert profile["version"] == 2 and profile["purpose"] and profile["context_selection"]
        assert profile["question_policy"]["context_first"]
        assert profile["question_policy"]["max_questions"] == 3
        assert profile["question_policy"]["min_questions"] == 0
        assert profile["output_contract"] and profile["exit_intents"] and profile["validation_rules"]
        assert profile["runtime_available"] is False
        if profile["mode"] not in {"idea", "planning"}:
            assert not profile["executable"] and not profile["allowed_commands"]
    planning = pm_profiles.get("planning")
    assert {slot["domain"] for slot in planning["input_slots"]} == set(pm_profiles.DOMAINS)
    assert "pm.thread.plan" in pm_profiles.get("idea")["allowed_commands"]
    assert "pm.thread.plan" not in planning["allowed_commands"]
    assert "pm.thread.plan.revise" in planning["allowed_commands"]
    assert "pm.thread.plan.revise" not in pm_profiles.get("idea")["allowed_commands"]
    assert pm_profiles.get("idea")["question_policy"]["no_forced_questionnaire"]


def test_profile_reads_cannot_mutate_builtin_contract_and_unknown_mode_is_refused():
    document = pm_profiles.get("planning")
    document["input_slots"].clear()
    document["allowed_commands"].append("repository.continue")
    assert pm_profiles.get("planning")["input_slots"]
    assert "repository.continue" not in pm_profiles.get("planning")["allowed_commands"]
    with pytest.raises(OperationError, match="PM_MODE_UNKNOWN"):
        pm_profiles.get("autonomous_delivery")
