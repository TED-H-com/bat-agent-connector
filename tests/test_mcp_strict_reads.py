"""Strict read registration remains opt-in to each generated tool model."""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from bat_agent_connector import mcp_server
from bat_agent_connector.config import Config


@pytest.mark.parametrize('read_only', [False, True])
async def test_strict_reads_leave_other_schemas_and_argument_handling_unchanged(monkeypatch, read_only):
    config = Config(hosts={})
    with monkeypatch.context() as patch:
        patch.setattr(mcp_server, '_strict_tool_arguments', lambda *args, **kwargs: None)
        baseline, baseline_fleet = mcp_server.build_server(config, read_only=read_only, principal_only=True)
    server, fleet = mcp_server.build_server(config, read_only=read_only, principal_only=True)
    try:
        before = {t.name: t.input_schema for t in await baseline.list_tools()}
        after = {t.name: t.input_schema for t in await server.list_tools()}
        assert before.keys() == after.keys()
        strict = {'pm_quality_assessment_get', 'pm_history_cleanup_list', 'pm_workspace_wip_get'}
        for name in before.keys() - strict:
            assert after[name] == before[name], name
        for name in strict:
            assert after[name] == before[name] | {'additionalProperties': False}

        cases = [
            ('pm_quality_review_get', {'workspace_id': 'null', 'review_id': '[]', 'unknown': True},
                {'workspace_id': 'null', 'review_id': '[]'}),
            ('pm_history_get', {'workspace_id': 'workspace', 'source_id': 'source', 'after': 2, 'unknown': True},
                {'workspace_id': 'workspace', 'source_id': 'source', 'after': 2, 'limit': 50}),
            ('pm_history_excerpt_preview', {'workspace_id': 'workspace', 'source_id': 'source', 'entry_id': 'entry',
                'expected_content_digest': 'digest', 'selection': '{"kind":"whole_entry"}', 'unknown': True},
                {'workspace_id': 'workspace', 'source_id': 'source', 'entry_id': 'entry',
                 'expected_content_digest': 'digest', 'selection': {'kind': 'whole_entry'}}),
        ]
        for name, params, expected in cases:
            old = baseline._tool_manager.get_tool(name).fn_metadata
            new = server._tool_manager.get_tool(name).fn_metadata
            original = copy.deepcopy(params)
            assert old.validate_arguments(params) == new.validate_arguments(params) == expected
            assert params == original
            invalid = params | ({'after': '2'} if name == 'pm_history_get' else {'workspace_id': []})
            with pytest.raises(ValidationError) as old_error:
                old.validate_arguments(invalid)
            with pytest.raises(ValidationError) as new_error:
                new.validate_arguments(invalid)
            assert new_error.value.errors() == old_error.value.errors()
    finally:
        await fleet.close()
        await baseline_fleet.close()
