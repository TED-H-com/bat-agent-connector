"""Preserve the follow-up row predicate while sharing its one-pass projection."""
import json

import pytest

from bat_agent_connector import pm_attempt_results, pm_work_follow_up


@pytest.mark.parametrize(('document', 'expected'), [
    ({'turn_marker': 'doc-marker', 'boundary': 'turn_done'}, {'pos-marker', 'doc-marker'}),
    ({'turn_marker': 'doc-marker', 'turn_progress': {'boundary': 'agent_error'}}, {'pos-marker', 'doc-marker'}),
    ({'turn_marker': 'doc-marker', 'boundary': 'process_ended'}, set()),
    ({'turn_marker': 'doc-marker'}, set()),
    ([], set()),
    ('malformed-json', set()),
])
def test_shared_terminal_row_predicate_preserves_both_marker_sources(document, expected):
    row = {'source_position': pm_attempt_results.format_source_position('pos-marker', 'answer'),
           'document': document if isinstance(document, str) else json.dumps(document)}
    assert pm_work_follow_up.terminal_receipt_markers(row) == expected
