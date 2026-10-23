"""Offline route admission uses the real operation service and profile producer."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_work_profiles as profiles
from bat_agent_connector import pm_work_start as work
from bat_agent_connector.operations import ActionDef, OperationError
from tests.operation_helpers import settle_operations
from tests.test_pm_work_profiles import (
    OWNER,
    WS,
    _body,
    _document,
    _finish,
    _open_start,
    _save,
    _save_start,
    _start_doc,
    _view,
    ops,  # noqa: F401
    pm,  # noqa: F401
    ready,  # noqa: F401
    start_ready,  # noqa: F401
    world,  # noqa: F401
)
from tests.test_pm_work_start import STARTER_OWNER, _advance_start, _park_until_executed


def install(service):
    from bat_agent_connector import pm_ahp_work_start
    pm_ahp_work_start.install(service)
    return pm_ahp_work_start


def counts(service):
    return tuple(service.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                 for table in ('operations', 'work_profiles'))


def ssh(service):
    runner = service.context.get('git_runner') or SimpleNamespace(available=lambda host: True)
    runner.execution_binding = lambda host: {'transport': 'ssh', 'host_identity': {'name': host}}
    service.context['git_runner'] = runner


def test_profile_write_requires_install(ops):  # noqa: F811
    before = counts(ops)
    with pytest.raises(OperationError, match='PM_WORK_ROUTE_UNAVAILABLE'):
        _save(ops, _document(adapter='ahp'))
    assert counts(ops) == before


@pytest.mark.parametrize('adapter', [None, {}, '', 'BAT', 'unknown'])
def test_unknown_adapter_is_not_bat(adapter):
    with pytest.raises(OperationError, match='INVALID_PARAMS'):
        profiles.normalize_document(_document(adapter=adapter))


async def test_explicit_install_and_unavailable_readiness(ops):  # noqa: F811
    module = install(ops)
    install(ops)
    saved = await _finish(ops, _save(ops, _document(adapter='ahp'))[0])
    profile_id = saved['result']['profile_id']
    before = counts(ops)
    view = profiles.readiness(ops, OWNER, WS, profile_id)
    assert view['coding_route_ready'] is False
    assert view['states']['can_start_own']['reason'] == 'GIT_RUNNER_UNAVAILABLE'
    ssh(ops)
    view = profiles.readiness(ops, OWNER, WS, profile_id)
    assert view['states']['can_start_own']['reason'] == 'AHP_CREATE_UNAVAILABLE'
    assert view['work_follow_up']['route'] == module.ROUTE
    assert view['work_follow_up']['state'] == 'unavailable' and not view['calls_bat']
    assert counts(ops) == before
    ops.context['work_start_routes'].clear()
    assert profiles.readiness(ops, OWNER, WS, profile_id)['smallest_gap']['reason'] == 'PM_WORK_ROUTE_UNAVAILABLE'
    assert counts(ops) == before


async def test_install_is_rechecked_at_profile_write(ops):  # noqa: F811
    install(ops)
    operation, _ = _save(ops, _document(adapter='ahp'))
    ops.context['work_start_routes'].clear()
    await settle_operations(ops)
    result = ops.get(operation['operation_id'])
    assert result['error_code'] == 'PM_WORK_ROUTE_UNAVAILABLE'
    assert ops.db.execute('SELECT COUNT(*) FROM work_profiles').fetchone()[0] == 0


@pytest.mark.parametrize('entry', ['http', 'mcp', 'work-start'])
def test_direct_child_submit_is_refused_before_effect(ops, entry):  # noqa: F811
    module = install(ops)
    before = counts(ops)
    from bat_agent_connector.api_auth import Principal
    principal = Principal(OWNER.actor, OWNER.scopes | {'start'})
    with pytest.raises(OperationError, match='AHP_HANDOFF_REQUIRED'):
        ops.create(principal, action=module.ACTION, target={'workspace_id': WS, 'work_item_id': 'wi_' + '1' * 20},
            params={'parent_id': 'op_' + '2' * 32}, preconditions={}, idempotency_key='synthetic-direct', entry=entry)
    assert counts(ops) == before


@pytest.mark.parametrize('installed', [False, True])
async def test_work_admission_uses_the_profile_adapter(start_ready, installed):  # noqa: F811
    w = start_ready
    service = w['d'].ops
    install(service)
    ssh(service)
    saved = await _save_start(w, _start_doc(adapter='ahp'))
    preview = await _view(w, _body(saved))
    if not installed:
        service.context['work_start_routes'].clear()
    before = counts(service)
    code = 'AHP_CREATE_UNAVAILABLE' if installed else 'PM_WORK_ROUTE_UNAVAILABLE'
    with pytest.raises(OperationError, match=code):
        _open_start(w, preview, saved)
    assert counts(service) == before


def test_route_builder_and_install_scope(ops):  # noqa: F811
    module = install(ops)
    pin = {'profile_id': 'wpf_' + '1' * 32, 'revision': 1, 'digest': 'a' * 64}
    intent = {'route': module.ROUTE, 'profile': pin, 'workspace_id': WS, 'work_item_id': 'wi_' + '2' * 20}
    package = {'workspace_id': WS, 'package_id': 'pmwp_' + '3' * 32, 'document_digest': 'b' * 64}
    request = work._handoff_request('op_' + '4' * 32, intent, package)
    assert request['action'] == module.ACTION
    assert request['idempotency_key'] == 'work-start:op_' + '4' * 32 + ':handoff'
    assert request['params'] == {'parent_id': 'op_' + '4' * 32, 'profile': pin, 'package_ref': package}
    assert 'instruction' not in request['params']
    isolated = SimpleNamespace(context={}, actions={})
    with pytest.raises(OperationError, match='PM_WORK_ROUTE_UNAVAILABLE'):
        work.profile_route(isolated, {'adapter': 'ahp'})
    unknown = copy.deepcopy(intent)
    unknown['route'] = 'unknown_work_start'
    with pytest.raises(OperationError, match='PM_WORK_ROUTE_UNAVAILABLE'):
        work._handoff_request('op_' + '4' * 32, unknown, package)
    source = Path(module.__file__).parent
    assert not any('pm_ahp_work_start.install(' in path.read_text() for path in source.glob('*.py'))


@pytest.fixture
async def routed(start_ready):  # noqa: F811
    w = start_ready
    service = w['d'].ops
    module = install(service)
    ssh(service)
    # Substitute only availability to exercise the real handoff before its creator exists.
    service.context['work_start_routes'][module.ROUTE] = lambda *args: {'available': True, 'reason': None}
    saved = await _save_start(w, _start_doc(adapter='ahp'))
    parent, _ = _open_start(w, await _view(w, _body(saved)), saved)
    assert work._intent(parent)['route'] == module.ROUTE
    assert work._intent(parent)['profile']['digest'] == saved['digest']
    await service._execute(parent['operation_id'])
    child = service.get(parent['operation_id'])['result']['children']['package']
    await service._execute(child)
    _park_until_executed(service, parent['operation_id'])
    return w, saved, parent['operation_id'], module


@pytest.mark.parametrize('change', ['none', 'profile', 'install'])
async def test_pinned_route_handoff_and_late_refusal(routed, change):
    w, saved, parent_id, module = routed
    service = w['d'].ops
    if change == 'profile':
        await _save_start(w, _start_doc(adapter='ahp', model='changed'),
                          profile_id=saved['profile_id'], expected_revision=1)
    elif change == 'install':
        service.context['work_start_routes'].clear()
    await service._execute(parent_id)
    parent = service.get(parent_id)
    child_id = parent['result']['children']['handoff']
    if change != 'none':
        assert child_id is None
        assert parent['error_code'] == ('PM_WORK_PROFILE_CHANGED' if change == 'profile' else 'PM_WORK_ROUTE_UNAVAILABLE')
        return
    child = service.get(child_id)
    assert child['action'] == module.ACTION
    assert child['params']['profile']['digest'] == saved['digest']
    assert child['external_refs'][work.LINK] == {'operation_id': parent_id, 'slot': 'handoff'}
    row = service.db.execute('SELECT * FROM pm_work_start_children WHERE operation_id=?', (child_id,)).fetchone()
    assert row['request_digest'] == work._digest(work._expected_child_request(service, parent, 'handoff'))
    request = {key: child[key] for key in ('action', 'target', 'params', 'preconditions', 'idempotency_key')}
    before = counts(service)
    with pytest.raises(OperationError, match='AHP_HANDOFF_REQUIRED'):
        service.create(STARTER_OWNER, **request, entry='mcp')
    assert counts(service) == before
    parent = await _advance_start(service, parent_id)
    assert parent['status'] == 'needs_attention' and parent['error_code'] == 'AHP_CREATE_UNAVAILABLE'
    assert parent['result']['stage'] == 'starting' and parent['result']['session'] is None


def test_handoff_admission_scope_is_closed(tmp_path, monkeypatch):
    from tests.test_work_start_bat_golden import produce
    produce(tmp_path, monkeypatch)
    assert work._HANDOFF_ADMISSION.get() is None


def test_install_refuses_an_existing_different_action(ops):  # noqa: F811
    from bat_agent_connector import pm_ahp_work_start as module
    async def unrelated(ctx):
        return {}
    definition = ActionDef(module.ACTION, 'start', 'Unrelated action', unrelated)
    ops.register(definition)
    with pytest.raises(OperationError, match='PM_WORK_ROUTE_CONFLICT'):
        module.install(ops)
    assert ops.actions[module.ACTION] is definition
    assert module.ROUTE not in ops.context.get('work_start_routes', {})


@pytest.mark.parametrize('transport,host,available', [('local', 'h1', True), ('ssh', 'other', True), ('ssh', 'h1', False)])
def test_readiness_never_converts_a_git_binding(ops, transport, host, available):  # noqa: F811
    module = install(ops)
    binding = {'transport': transport, 'host_identity': {'name': host}}
    ops.context['git_runner'] = SimpleNamespace(execution_binding=lambda _: binding, available=lambda _: available)
    assert module.readiness(ops, _document(adapter='ahp')) == {'available': False, 'reason': 'GIT_RUNNER_UNAVAILABLE'}
    assert binding == {'transport': transport, 'host_identity': {'name': host}}
