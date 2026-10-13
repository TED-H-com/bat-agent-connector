"""The shared read memo must never extend source authority or a snapshot."""
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace

import pytest

from bat_agent_connector import pm_read_cache, pm_source_lineage
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.pm_release_baselines import _snapshot

OWNER = Principal('reader', frozenset({'observe'}), credential_id='credential-one')


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / 'memo.sqlite', isolation_level=None, check_same_thread=False)
    connection.execute('CREATE TABLE records (value TEXT)')
    connection.execute("INSERT INTO records VALUES ('original')")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(params=['decorator', 'explicit'])
def reader(request):
    calls = []

    def load(db, key='row'):
        calls.append(key)
        return {'rows': [row[0] for row in db.execute('SELECT value FROM records')]}

    decorated = pm_read_cache.memoized(load)

    def read(db, key='row'):
        if request.param == 'decorator':
            return decorated(db, key)
        return pm_read_cache.remember('test.records', key, lambda: load(db, key))

    return read, calls


def test_nested_scope_joins_and_outer_exit_discards_values(db, reader):
    read, calls = reader
    with _snapshot(db):
        with pm_read_cache.scope(db, OWNER):
            expected = read(db)
            with pm_read_cache.scope(db):
                assert read(db) == expected
            assert read(db) == expected
            assert len(calls) == 1
            inherited = copy_context()
            current = pm_read_cache._CURRENT.get().scope
        assert current.values == {} and not current.active
        assert inherited.run(read, db) == expected
        with pm_read_cache.scope(db, OWNER):
            assert read(db) == expected
            assert read(db) == expected
        assert len(calls) == 3
    assert not db.in_transaction


def test_outside_snapshot_bypasses_even_if_transaction_starts_later(db, reader):
    read, calls = reader
    with pm_read_cache.scope(db):
        read(db)
        read(db)
        with _snapshot(db), pm_read_cache.scope(db):
            read(db)
            read(db)
    assert len(calls) == 4


def test_snapshot_ending_disables_scope_permanently(db, reader):
    read, calls = reader
    db.execute('BEGIN')
    with pm_read_cache.scope(db):
        read(db)
        db.execute('COMMIT')
        read(db)
        with _snapshot(db), pm_read_cache.scope(db):
            read(db)
            read(db)
    assert len(calls) == 4


def test_distinct_databases_have_distinct_scopes_and_restore_outer(db, reader, tmp_path):
    read, calls = reader
    other = sqlite3.connect(tmp_path / 'other.sqlite', isolation_level=None)
    try:
        other.execute('CREATE TABLE records (value TEXT)')
        other.execute("INSERT INTO records VALUES ('other')")
        with _snapshot(db), pm_read_cache.scope(db):
            assert read(db)['rows'] == ['original']
            with _snapshot(other), pm_read_cache.scope(other):
                assert read(other)['rows'] == ['other']
                read(other)
                with pm_read_cache.scope(db):
                    assert read(db)['rows'] == ['original']
                    with pm_read_cache.scope(other):
                        assert read(other)['rows'] == ['other']
            assert read(db)['rows'] == ['original']
    finally:
        other.close()
    assert len(calls) == 2


def test_decorator_bypasses_a_different_database(db, tmp_path):
    other = sqlite3.connect(tmp_path / 'other.sqlite', isolation_level=None)
    calls = []

    @pm_read_cache.memoized
    def read(connection):
        calls.append(connection)
        return len(calls)

    try:
        with _snapshot(db), pm_read_cache.scope(db):
            assert read(db) == read(db) == 1
            assert read(other) == 2
            assert read(other) == 3
    finally:
        other.close()


def test_inherited_thread_context_bypasses_live_owner(db, reader):
    read, calls = reader

    def child():
        with pm_read_cache.scope(db, OWNER):
            read(db)
            read(db)

    with _snapshot(db), pm_read_cache.scope(db, OWNER):
        expected = read(db)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(copy_context().run, child).result()
        assert read(db) == expected
        assert len(calls) == 3


async def test_inherited_async_task_bypasses_live_owner(db, reader):
    read, calls = reader

    async def child():
        with pm_read_cache.scope(db, OWNER):
            read(db)
            read(db)

    with _snapshot(db), pm_read_cache.scope(db, OWNER):
        expected = read(db)
        await asyncio.create_task(child())
        assert read(db) == expected
        assert len(calls) == 3


async def test_inherited_child_cannot_revive_finished_scope(db, reader):
    read, calls = reader
    ready = asyncio.Event()

    async def child():
        await ready.wait()
        with _snapshot(db), pm_read_cache.scope(db):
            read(db)
            read(db)

    with _snapshot(db), pm_read_cache.scope(db):
        read(db)
        task = asyncio.create_task(child())
    ready.set()
    await task
    assert len(calls) == 3


@pytest.mark.parametrize('rollback', [False, True])
def test_write_clears_all_entries_and_bypasses_rest_of_scope(db, reader, rollback):
    read, calls = reader
    with _snapshot(db), pm_read_cache.scope(db):
        read(db, 'first')
        read(db, 'second')
        current = pm_read_cache._CURRENT.get().scope
        db.execute('SAVEPOINT write_test')
        db.execute("UPDATE records SET value='changed'")
        if rollback:
            db.execute('ROLLBACK TO write_test')
        db.execute('RELEASE write_test')
        expected = ['original' if rollback else 'changed']
        assert read(db, 'first')['rows'] == expected
        assert current.values == {}
        with pm_read_cache.scope(db):
            assert read(db, 'second')['rows'] == expected
            assert read(db, 'second')['rows'] == expected
        assert len(calls) == 5


@pytest.mark.parametrize('fails', [False, True])
def test_write_during_load_cannot_repopulate_cache(db, fails):
    calls = []

    def load():
        calls.append(True)
        db.execute("UPDATE records SET value='changed'")
        if fails:
            raise OperationError('REFUSED', 'Changed while loading', 409)
        return 'changed'

    with _snapshot(db), pm_read_cache.scope(db):
        for _ in range(2):
            if fails:
                with pytest.raises(OperationError):
                    pm_read_cache.remember('write', 'row', load)
            else:
                assert pm_read_cache.remember('write', 'row', load) == 'changed'
        assert pm_read_cache._CURRENT.get().scope.values == {}
    assert len(calls) == 2


@pytest.mark.parametrize('difference', [
    {'actor': 'other-reader'}, {'scopes': frozenset({'manage'})},
    {'admin': True}, {'credential_id': 'credential-two'},
])
def test_lineage_visibility_keys_full_explicit_principal(db, monkeypatch, difference):
    calls = []
    other = replace(OWNER, **difference)

    def visible_roots(db, principal, roots):
        calls.append(principal)
        return principal is OWNER

    monkeypatch.setattr(pm_source_lineage, 'roots', lambda *args: {'operation'})
    monkeypatch.setattr(pm_source_lineage, 'visible_roots', visible_roots)
    with _snapshot(db), pm_read_cache.scope(db, OWNER):
        assert pm_source_lineage.visible(db, OWNER, 'work_item', 'item')
        assert not pm_source_lineage.visible(db, other, 'work_item', 'item')
        assert pm_source_lineage.visible(db, OWNER, 'work_item', 'item')
        assert not pm_source_lineage.visible(db, other, 'work_item', 'item')
    assert len(calls) == 2


@pytest.mark.parametrize('difference', [
    {'actor': 'other-reader'}, {'scopes': frozenset({'manage'})},
    {'admin': True}, {'credential_id': 'credential-two'},
])
def test_explicit_keys_include_nested_scope_principal(db, difference):
    calls = []

    def load():
        calls.append(True)
        return len(calls)

    with _snapshot(db), pm_read_cache.scope(db, OWNER):
        assert pm_read_cache.remember('visibility', 'item', load) == 1
        with pm_read_cache.scope(db, replace(OWNER, **difference)):
            assert pm_read_cache.remember('visibility', 'item', load) == 2
            assert pm_read_cache.remember('visibility', 'item', load) == 2
        assert pm_read_cache.remember('visibility', 'item', load) == 1
    assert len(calls) == 2


def test_mutating_returned_value_cannot_change_cached_value(db, reader):
    read, calls = reader
    with _snapshot(db), pm_read_cache.scope(db):
        first = read(db)
        first['rows'].append('caller annotation')
        assert read(db) == {'rows': ['original']}
        assert len(calls) == 1


def test_mutating_loader_value_cannot_change_cached_value(db):
    source = {'rows': [{'labels': {'original'}}]}
    with _snapshot(db), pm_read_cache.scope(db):
        value = pm_read_cache.remember('mutable', 'row', lambda: source)
        source['rows'][0]['labels'].add('loader annotation')
        value['rows'].clear()
        assert pm_read_cache.remember('mutable', 'row', lambda: None) == {'rows': [{'labels': {'original'}}]}


def test_operation_errors_are_cached_as_fields_and_raised_fresh(db):
    original = OperationError('UNAVAILABLE', 'Source withdrawn', 404)
    calls, errors = [], []

    def load():
        calls.append(True)
        raise original

    with _snapshot(db), pm_read_cache.scope(db):
        for _ in range(3):
            with pytest.raises(OperationError) as exc:
                pm_read_cache.remember('failure', 'row', load)
            assert exc.value is not original
            assert (exc.value.code, exc.value.message, exc.value.status) == ('UNAVAILABLE', 'Source withdrawn', 404)
            errors.append(exc.value)
            exc.value.message = 'caller mutation'
        assert len({id(error) for error in errors}) == 3
        assert len(calls) == 1
        db.execute("UPDATE records SET value='changed'")
        with pytest.raises(OperationError):
            pm_read_cache.remember('failure', 'row', load)
        assert len(calls) == 2


def test_other_exceptions_are_not_cached(db):
    calls = []

    def load():
        calls.append(True)
        raise ValueError('Try again')

    with _snapshot(db), pm_read_cache.scope(db):
        for _ in range(2):
            with pytest.raises(ValueError, match='Try again'):
                pm_read_cache.remember('failure', 'row', load)
    assert len(calls) == 2


def test_explicit_namespaces_do_not_collide(db):
    with _snapshot(db), pm_read_cache.scope(db):
        assert pm_read_cache.remember('one', 'id', lambda: 1) == 1
        assert pm_read_cache.remember('two', 'id', lambda: 2) == 2
        assert pm_read_cache.remember('one', 'id', lambda: 3) == 1


def test_decorator_preserves_keyword_calls_and_argument_errors(db):
    calls = []

    @pm_read_cache.memoized
    def read(connection, *, principal):
        calls.append(principal)
        return principal.actor

    with _snapshot(db), pm_read_cache.scope(db):
        assert read(connection=db, principal=OWNER) == OWNER.actor
        assert read(db, principal=OWNER) == OWNER.actor
        assert len(calls) == 1
        with pytest.raises(TypeError):
            read(db, connection=db, principal=OWNER)
        with pytest.raises(TypeError):
            read(principal=OWNER)
        assert len(calls) == 1


def test_scope_exception_clears_values_and_rolls_back_snapshot(db):
    with pytest.raises(ValueError), _snapshot(db), pm_read_cache.scope(db):
        pm_read_cache.remember('one', 'id', lambda: 1)
        current = pm_read_cache._CURRENT.get().scope
        raise ValueError('Read failed')
    assert current.values == {} and not current.active
    assert pm_read_cache._CURRENT.get() is None
    assert not db.in_transaction


def test_lineage_roots_return_copies_and_keep_recursive_seen_state(db):
    db.execute('CREATE TABLE pm_source_lineage (resource_type TEXT, resource_id TEXT, original_operation_id TEXT)')
    db.execute("INSERT INTO pm_source_lineage VALUES ('work_item', 'item', 'operation')")
    with _snapshot(db), pm_read_cache.scope(db):
        found = pm_source_lineage.roots(db, 'work_item', 'item')
        found.clear()
        assert pm_source_lineage.roots(db, 'work_item', 'item') == {'operation'}
        seen = set()
        assert pm_source_lineage.roots(db, 'work_item', 'item', _seen=seen) == {'operation'}
        assert seen == {('work_item', 'item')}
        assert pm_source_lineage.roots(db, 'work_item', 'item', _seen=seen) == set()
        assert pm_source_lineage.roots(db, 'work_item', 'item') == {'operation'}


def test_guarded_loads_are_served_from_stored_entries_but_never_stored(db):
    calls = []

    def load(value):
        calls.append(value)
        return {'value': value}

    with _snapshot(db), pm_read_cache.scope(db, OWNER):
        assert pm_read_cache.remember('stored', 'id', lambda: load('outside')) == {'value': 'outside'}
        with pm_read_cache.guarded(), pm_read_cache.guarded():
            # An entry stored outside every guard is still the request-wide answer.
            assert pm_read_cache.remember('stored', 'id', lambda: load('inside')) == {'value': 'outside'}
            assert pm_read_cache.remember('guarded', 'id', lambda: load('assumed')) == {'value': 'assumed'}
            with pytest.raises(OperationError):
                pm_read_cache.remember('refused', 'id', lambda: (_ for _ in ()).throw(
                    OperationError('NOT_FOUND', 'Traversal budget', 404)))
        assert pm_read_cache._GUARDS.get() == 0
        assert pm_read_cache.remember('guarded', 'id', lambda: load('fresh')) == {'value': 'fresh'}
        assert pm_read_cache.remember('refused', 'id', lambda: 'visible') == 'visible'
    assert calls == ['outside', 'assumed', 'fresh']


def _cycle_world(guard):
    """B is memoized; its loader re-enters A's traversal, which refuses the cycle.

    A fresh decision for B (outside A's traversal) is True. A refusal computed
    while A was in progress is local to that traversal and must not be stored.
    """
    @pm_read_cache.memoized
    def b_visible(connection, principal):
        return guard(connection, principal, 'a', lambda: (b_visible(connection, principal), True)[1])

    return b_visible


def test_proposal_access_cycle_refusals_are_not_cached_request_wide(db):
    from bat_agent_connector import pm_conversation_proposals as proposals

    def guard(connection, principal, identity, check):
        return proposals._bounded_access(connection, principal, 'proposal', identity, check)

    b_visible = _cycle_world(guard)
    unscoped = b_visible(db, OWNER)
    assert unscoped is True
    with _snapshot(db), pm_read_cache.scope(db, OWNER):
        # A's traversal first: its nested B answer meets the cycle refusal.
        assert guard(db, OWNER, 'a', lambda: (b_visible(db, OWNER), True)[1]) is True
        assert b_visible(db, OWNER) is unscoped


def test_spec_source_cycle_refusals_are_not_cached_request_wide(db, monkeypatch):
    from bat_agent_connector import pm_spec_materialization as spec

    checks = {}
    monkeypatch.setattr(spec, '_source_visible', lambda connection, principal, operation: checks[
        operation['operation_id']]())

    def guard(connection, principal, identity, check):
        checks[identity] = check
        return spec.source_visible(connection, principal, {'operation_id': identity})

    b_visible = _cycle_world(guard)
    unscoped = b_visible(db, OWNER)
    assert unscoped is True
    with _snapshot(db), pm_read_cache.scope(db, OWNER):
        assert guard(db, OWNER, 'a', lambda: (b_visible(db, OWNER), True)[1]) is True
        assert b_visible(db, OWNER) is unscoped
