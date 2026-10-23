"""Connection-local current grants with real journal transactions and activation."""
import sqlite3
from dataclasses import replace

import pytest

from bat_agent_connector import managed_setup, session_history
from bat_agent_connector import pm_external_history as refs
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from bat_agent_connector.session_history_config import HistoryConfig
from bat_agent_connector.task_journal import Journal
from tests.test_api_v1 import daemon  # noqa: F401
from tests.test_external_history import append, message, read, ready  # noqa: F401
from tests.test_pm_api import OWNER, seed


@pytest.fixture
async def collected(ready):  # noqa: F811
    d, wid, source = ready
    append(source.path, message())
    await d.history_collector.collect_once()
    return d, wid, source


@pytest.fixture(params=['same_file', 'separate_journal'])
def isolated_journal(collected, tmp_path, request):
    d, _, _ = collected
    journal = Journal(d.journal.path if request.param == 'same_file' else tmp_path / 'isolated.sqlite')
    yield journal
    journal.close()


async def test_second_connection_or_journal_does_not_inherit_grants(collected, isolated_journal):
    d, wid, source = collected
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
        with refs.current_grants(isolated_journal.db, OWNER, wid):
            pytest.fail('No activation on this connection')
    assert read(d, wid, source)['entries']


@pytest.mark.parametrize('damage', ['missing', 'inactive', 'stale'])
async def test_projection_fail_closed_even_for_admin(collected, damage):
    d, wid, source = collected
    if damage == 'missing':
        d.journal.db.execute('CREATE TABLE main.session_history_current_grants AS SELECT * FROM temp.session_history_current_grants')
        d.journal.db.execute('DROP TABLE temp.session_history_current_grants')
    elif damage == 'inactive':
        d.history_collector._generation = None
    else:
        d.journal.db.execute("UPDATE temp.session_history_grant_state SET generation='stale'")
    admin = Principal(OWNER.actor, OWNER.scopes, admin=True)
    for principal in (OWNER, admin):
        with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
            session_history.read(d.ops, principal, wid, source.source_id)


async def test_real_activation_invalidates_positive_grant_before_response(collected):
    d, wid, source = collected
    assert read(d, wid, source)['entries']
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
        with refs.current_grants(d.journal.db, OWNER, wid) as granted:
            assert granted[0]['source_id'] == source.source_id
            managed_setup._activate(d, replace(d._config, session_history=HistoryConfig()), {}, 'withdraw-source')
    assert session_history.list_sources(d.ops, OWNER, wid)['rows'] == []
    with pytest.raises(OperationError):
        read(d, wid, source)
    # Regranting identical content does not restore the authority of a held read.
    config = HistoryConfig(10, (source,))
    d.history_collector.config = config
    with pytest.raises(OperationError):
        with refs.current_grants(d.journal.db, OWNER, wid):
            d.history_collector.config = config
    assert read(d, wid, source)['entries']


@pytest.mark.parametrize('surface', ['list', 'read', 'preview'])
async def test_activation_during_serialization_cannot_return_old_body(collected, monkeypatch, surface):
    d, wid, source = collected
    entry = read(d, wid, source)['entries'][0]
    original = session_history.json.dumps
    fired = False

    def withdraw(value, *args, **kwargs):
        nonlocal fired
        if not fired:
            fired = True
            managed_setup._activate(d, replace(d._config, session_history=HistoryConfig()), {}, 'during-response')
        return original(value, *args, **kwargs)

    # Inject at a response construction boundary, never replace the grant decision.
    if surface == 'list':
        original_row = session_history._row
        def row(*args):
            result = original_row(*args)
            withdraw(None)
            return result
        monkeypatch.setattr(session_history, '_row', row)
    else:
        monkeypatch.setattr(session_history.json, 'dumps', withdraw)
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
        if surface == 'preview':
            refs.preview(d.journal.db, OWNER, wid, source.source_id, entry['entry_id'], entry['content_digest'], {'kind': 'entry'})
        elif surface == 'list':
            session_history.list_sources(d.ops, OWNER, wid)
        else:
            read(d, wid, source)
    assert fired


async def test_projection_schema_activation_is_atomic_and_idempotent(collected):
    d, wid, source = collected
    db = d.journal.db
    version = db.execute('PRAGMA user_version').fetchone()[0]
    original = read(d, wid, source)
    config = d.history_collector.config
    generation = db.execute('SELECT generation FROM temp.session_history_grant_state').fetchone()[0]
    db.execute("""CREATE TEMP TRIGGER reject_grant BEFORE INSERT ON session_history_current_grants
        BEGIN SELECT RAISE(ABORT,'activation fixture'); END""")
    try:
        with pytest.raises(sqlite3.IntegrityError, match='activation fixture'):
            d.history_collector.config = config
        assert d.history_collector.config == config
        assert db.execute('SELECT generation FROM temp.session_history_grant_state').fetchone()[0] == generation
        assert read(d, wid, source) == original
    finally:
        db.execute('DROP TRIGGER temp.reject_grant')
    session_history.schema(d.journal)
    session_history.HistoryCollector(d.journal, config)
    assert read(d, wid, source) == original
    assert db.execute('PRAGMA user_version').fetchone()[0] == version
    names = {row[0] for row in db.execute("SELECT name FROM main.sqlite_schema WHERE type='table'")}
    assert not names & {'session_history_current_grants', 'session_history_grant_state', 'pm_external_excerpts',
        'pm_external_links', 'pm_external_link_events'}
    assert str(source.path) not in '\n'.join(db.iterdump())


async def test_actor_workspace_and_scope_are_independent_read_requirements(collected):
    d, wid, source = collected
    other, _ = await seed(d)
    for principal, workspace in [(Principal('outsider', OWNER.scopes), wid),
        (Principal('ungranted-admin', OWNER.scopes, admin=True), wid), (OWNER, other['workspace_id']),
        (Principal(OWNER.actor, frozenset({'manage'})), wid)]:
        with pytest.raises(OperationError):
            session_history.read(d.ops, principal, workspace, source.source_id)
    assert read(d, wid, source)['entries']


async def test_connection_activation_isolation_and_outer_rollback(collected):
    d, wid, source = collected
    other = Journal(d.journal.path)
    try:
        collector = session_history.HistoryCollector(other, HistoryConfig())
        with refs.current_grants(other.db, OWNER, wid) as rows:
            assert rows == []
        assert read(d, wid, source)['entries']
        collector.config = HistoryConfig(10, (source,))
        with refs.current_grants(other.db, OWNER, wid) as rows:
            assert rows[0]['source_id'] == source.source_id
        with pytest.raises(RuntimeError, match='rollback'):
            with other.tx():
                collector.config = HistoryConfig()
                raise RuntimeError('rollback')
        with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
            with refs.current_grants(other.db, OWNER, wid):
                pytest.fail('An outer rollback must not revive a generation')
        collector.config = HistoryConfig()
        assert read(d, wid, source)['entries']
    finally:
        other.close()


async def test_empty_schema_before_activation_does_not_grant(collected, isolated_journal):
    _, wid, _ = collected
    with isolated_journal.tx():
        refs.grant_schema(isolated_journal.db)
        refs.grant_schema(isolated_journal.db)
    with pytest.raises(OperationError, match='PM_HISTORY_SOURCE_UNAVAILABLE'):
        with refs.current_grants(isolated_journal.db, OWNER, wid):
            pytest.fail('Schema installation is not activation')
