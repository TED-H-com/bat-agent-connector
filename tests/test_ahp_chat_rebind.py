"""Crash readback preserves the first committed chat-binding evidence."""
import copy

import pytest

from bat_agent_connector import ahp_owned_resources as ledger
from bat_agent_connector import controller_authority as authority
from bat_agent_connector.errors import ResourceReadOnly
from bat_agent_connector.task_journal import Journal
from tests.test_ahp_owned import RID, producer, world  # noqa: F401


def stored(journal):
    return tuple(journal.db.execute(
        "SELECT digest,body FROM ahp_owned_sessions WHERE resource_id=? AND record_id='chat_bound'", (RID,)).fetchone())


@pytest.mark.parametrize('change', ['fresh', 'other_chat', 'invalid', 'other_creation'])
def test_rebind_after_committed_binding(world, monkeypatch, change):  # noqa: F811
    journal, _, _, _ = world
    evidence = producer(world)
    first = stored(journal)
    snapshot = copy.deepcopy(evidence.chat_snapshot)
    snapshot['fromSeq'] += 1
    snapshot['state']['modifiedAt'] = '2026-01-02T00:00:00Z'
    if change == 'other_chat':
        snapshot['resource'] = snapshot['state']['resource'] = 'ahp-chat:/other'
    elif change == 'invalid':
        snapshot['state']['resource'] = 'ahp-chat:/other'
    elif change == 'other_creation':
        original = ledger.get
        def get(db, resource, record):
            value = original(db, resource, record)
            return {**value, 'response_digest': 'changed'} if record == 'created' else value
        monkeypatch.setattr(ledger, 'get', get)
    # The caller lost its next step; a new journal connection sees the committed binding.
    recovered = Journal(journal.path)
    try:
        with authority.use(authority.require(), recovered):
            if change == 'fresh':
                try:
                    result = ledger.bind_chat(recovered, RID, snapshot)
                except ResourceReadOnly as error:
                    result = error.code
                assert result == ledger.get(recovered, RID, 'chat_bound')
            else:
                code = 'AHP_CHAT_UNPROVEN' if change == 'invalid' else 'AHP_EVIDENCE_CONFLICT'
                with pytest.raises(ResourceReadOnly, match=code):
                    ledger.bind_chat(recovered, RID, snapshot)
        assert stored(recovered) == first
        assert recovered.db.total_changes == 0
    finally:
        recovered.close()
