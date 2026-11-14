"""Phase accounting for the real bootstrap handler and its fresh final barrier."""
import pytest

from bat_agent_connector import api_auth
from bat_agent_connector import pm_source_lineage as lineage
from tests.test_api_v1 import daemon as daemon  # noqa: F401
from tests.test_api_v1 import served as served  # noqa: F401
from tests.test_api_v1 import token
from tests.test_pm_page_source_limits import page_rows
from tests.test_pm_page_source_transport import OWNER, request_page
from tests.test_pm_page_source_transport import pages as pages  # noqa: F401


@pytest.mark.parametrize('pages', ['early_failure'], indirect=True)
@pytest.mark.parametrize('transport', ['http', 'call_api'])
@pytest.mark.parametrize('role', ['member', 'admin'])
async def test_bootstrap_barrier_reuses_only_its_own_completed_proofs(pages, monkeypatch, transport, role):
    d = pages[0]
    credential = token(d, OWNER.actor, *OWNER.scopes) if role == 'member' else d._admin_token
    principal = api_auth.authenticate(d.ops.db, credential, d._admin_token)
    original_root, original_barrier = lineage._visible_roots, lineage.read_response
    evaluations = {'handler': [], 'barrier': []}
    supplied, occurrences = [], []
    phase = 'handler'

    def root(db, principal, identities):
        evaluations[phase].extend(identities)
        return original_root(db, principal, identities)

    def barrier(db, principal, value):
        nonlocal phase
        supplied.append(value)
        for row, host in page_rows(value):
            occurrences.extend(lineage.document_roots(db, row, inherited_host=host))
        phase = 'barrier'
        try:
            return original_barrier(db, principal, value)
        finally:
            phase = 'handler'

    monkeypatch.setattr(lineage, '_visible_roots', root)
    monkeypatch.setattr(lineage, 'read_response', barrier)
    status, body = await request_page(pages, monkeypatch, 'bootstrap', transport, principal, credential)
    assert status == 200 and body == supplied[0]
    assert len(evaluations['handler']) == 23
    assert len(occurrences) == 155
    assert len(evaluations['barrier']) == len(set(evaluations['barrier'])) == 66
    assert set(evaluations['barrier']) == set(occurrences)
    # Handler-time authorization must never replace the fresh final proof.
    assert set(evaluations['handler']) & set(evaluations['barrier'])
