"""Synthetic BAT contract captured from the original Work handoff producer."""
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from bat_agent_connector import controller_authority, operations, pm_work_profiles, pm_work_start
from bat_agent_connector.operations import ActionDef, OpContext
from tests.test_pm_work_start import OWNER, _service

GOLDEN = Path(__file__).parent / 'fixtures/ahp-work-start-bat.json'


def produce(tmp_path, monkeypatch):
    ops = _service(tmp_path)
    pm_work_start.schema(ops.journal)
    identities = iter([uuid.UUID('1' * 32), uuid.UUID('2' * 32)])
    monkeypatch.setattr(operations, 'uuid', SimpleNamespace(uuid4=lambda: next(identities)))
    monkeypatch.setattr(pm_work_start, 'time', SimpleNamespace(time=lambda: 1800000000.0))
    monkeypatch.setattr(pm_work_start, '_guard', lambda *args, **kwargs: OWNER)
    async def run(ctx):
        return {}
    ops.register(ActionDef('repository.continue', 'start', 'Synthetic BAT carrier', run))
    try:
        parent, _ = ops.create(OWNER, action='work.start',
            target={'workspace_id': 'ws_' + '1' * 32, 'work_item_id': 'wi_' + '3' * 20},
            params={}, preconditions={}, idempotency_key='synthetic-parent')
        intent = {'code_source': {'repository': 'o/r', 'host': 'synthetic-host', 'workspace_id': 'synthetic-workspace',
            'source_ref': 'refs/heads/main', 'source_sha': 'a' * 40, 'repository_id': 42, 'binding_digest': 'b' * 64},
            'agent': 'claude', 'model': 'synthetic-model', 'instruction': 'Synthetic 工作', 'title': 'Synthetic title',
            'project_id': 'proj_' + '4' * 16, 'work_item_id': 'wi_' + '3' * 20,
            'expected_project_version': 2, 'expected_work_item_fingerprint': 'c' * 64}
        package = {'workspace_id': 'ws_' + '1' * 32, 'package_id': 'pmwp_' + '5' * 32, 'document_digest': 'd' * 64}
        request = pm_work_start._handoff_request(ops, parent['operation_id'], intent, package)
        bound = pm_work_start._intent(parent)['controller']
        with controller_authority.use(bound, ops.journal):
            pm_work_start._admit_child(OpContext(ops, parent), 'handoff', request)
        rows = [dict(row) for row in ops.db.execute('SELECT * FROM pm_work_start_children')]
        document = pm_work_profiles.normalize_document({'version': 1, 'binding': {
            'repository': 'o/r', 'host': 'synthetic-host', 'workspace_id': 'synthetic-workspace'}, 'agent': 'claude'})
        profile_bytes = pm_work_profiles._json(document)
        value = {'synthetic': True, 'producer_commit': '9e888a05a96b56483ae68ed712e46db3bdcc5a7b',
            'producer': 'pm_work_start._handoff_request/_admit_child; pm_work_profiles.normalize_document',
            'request': request, 'children': rows, 'request_digest': pm_work_start._digest(request),
            'profile_document': profile_bytes, 'profile_digest': hashlib.sha256(profile_bytes.encode()).hexdigest()}
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    finally:
        ops.journal.close()


def test_bat_handoff_and_profile_bytes(tmp_path, monkeypatch):
    assert produce(tmp_path, monkeypatch) == GOLDEN.read_bytes()
