"""Real cursor readers with controlled authorization cost; no authority bypass in production."""
import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import operations, pm_access, pm_read_cache, pm_runs, pm_source_reads
from bat_agent_connector import pm_report_read_work as work
from bat_agent_connector import pm_roadmap_report_materialization as reports
from bat_agent_connector import pm_roadmap_report_sources as sources
from bat_agent_connector.operations import OperationError, OperationService
from bat_agent_connector.task_journal import Journal
from tests import pm_report_read_reference as reference
from tests.test_pm_plan_operations import OWNER

READERS = ('operations', 'events', 'runs', 'receipts')


@pytest.fixture
def pages(tmp_path, monkeypatch):
    journal = Journal(tmp_path / 'page-work.db')
    ops = OperationService(journal, actions=[])
    reports.schema(journal)
    db, wid = journal.db, 'ws_' + '1' * 32
    # Minimal run index; projection and visibility are controlled seams here.
    db.execute('CREATE TABLE pm_runs(run_id TEXT, workspace_id TEXT, thread_id TEXT)')
    projections, records = {}, {}
    for n in range(1, 56):
        oid, rid, mid = f'op_{n:032x}', f'pmr_{n:032x}', f'pmrpt_{n:032x}'
        db.execute('''INSERT INTO operations(operation_id,actor,entry,idem_key,request_hash,
            action,target,params,preconditions,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (oid, OWNER.actor, 'fixture', oid, 'fixture', 'fixture.read', '{}', '{}', '{}', 'succeeded', 1000 - n, 1000 - n))
        db.execute('INSERT INTO pm_runs VALUES(?,?,?)', (rid, wid, 'thread-fixture'))
        records[rid] = {'operation_id': oid, 'run_id': rid, 'workspace_id': wid}
        db.execute('''INSERT INTO pm_roadmap_report_materializations
            (sequence,materialization_id,workspace_id,observation_revision_id,source_set_digest,
             view_digest,renderer_version,format,locale,output_digest,byte_length,git_receipt,operation_id,actor,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (n, mid, wid, 'pmro_' + '2' * 32, 'a' * 64, 'b' * 64, 1, 'markdown', 'en', 'c' * 64, 1, '{}', oid, OWNER.actor, n))
        projections[mid] = {'kind': 'roadmap_report_materialization', 'operation_id': oid,
                           'materialization_id': mid, 'workspace_id': wid}
        journal.api_event('operation', oid, 'operation.succeeded', {}, actor=OWNER.actor)
    config = {'records': 0, 'bytes': 0, 'observations': False, 'hidden': set(), 'seen': [],
              'cost': None, 'observation_cost': None}
    def visible(db, principal, row):
        oid = row.get('operation_id', row.get('resource_id'))
        config['seen'].append(oid)
        observations = config['observation_cost'](len(config['seen'])) if config['observation_cost'] else int(config['observations'])
        for i in range(observations):
            work.observation(db, principal, wid, (oid, i))
        records, byte_length = (config['cost'](len(config['seen'])) if config['cost']
                               else (config['records'], config['bytes']))
        work.charge(db, records, byte_length)
        pm_read_cache.remember('examined-fixture', (oid,), lambda: True)
        return oid not in config['hidden']
    for module, name in [(pm_access, 'visible'), (pm_access, 'event_visible'),
                         (pm_runs, 'source_visible'), (reports, 'source_visible')]:
        monkeypatch.setattr(module, name, visible)
    monkeypatch.setattr(pm_runs, 'get', lambda db, rid: records[rid])
    monkeypatch.setattr(pm_runs, '_summary', lambda row: dict(row))
    monkeypatch.setattr(pm_runs.pm_discussions, '_read', lambda *args: None)
    monkeypatch.setattr(reports, '_read', lambda *args: None)
    monkeypatch.setattr(reports, '_record', lambda db, wid, mid: projections[mid])
    def read(kind, cursor=None, *, old=False):
        module = {'operations': operations, 'events': pm_access, 'runs': pm_runs, 'receipts': reports}[kind]
        namespace = dict(vars(module))
        if old:
            source = getattr(reference, module.__name__.rsplit('.', 1)[1].upper())
            exec(source, namespace)
        if kind == 'operations':
            fn = namespace['ReferenceOperations'].list if old else OperationService.list
            return fn(ops, principal=OWNER, limit=100, **(cursor or {}))
        if kind == 'events':
            fn = namespace['filter_events']
            return fn(db, OWNER, journal.api_events(cursor or 0, 100))
        if kind == 'runs':
            return namespace['list_runs'](ops, OWNER, wid, limit=100, after=cursor)
        return namespace['list_materializations'](ops, OWNER, wid, limit=50, after=cursor or 0)
    yield SimpleNamespace(ops=ops, db=db, wid=wid, config=config, read=read)
    journal.close()


def rows(kind, page):
    return page['materializations' if kind == 'receipts' else kind]


def cursor(kind, page):
    if kind == 'operations':
        return ({'before': page['next_before'], 'before_id': page['next_before_id']}
                if page['next_before'] is not None else None)
    if kind == 'events':
        return page['next_cursor'] if page['has_more'] else None
    return page['next_after' if kind == 'receipts' else 'next_cursor']


@pytest.mark.parametrize('kind', READERS)
@pytest.mark.parametrize('threshold,expected', [('observations', 20), ('records', 2), ('bytes', 2)])
def test_work_stop_is_before_the_next_row_and_never_a_visibility_decision(pages, kind, threshold, expected):
    pages.config[threshold] = {'observations': True, 'records': 1000, 'bytes': 8_000_000}[threshold]
    with pm_source_reads.scope(pages.db, OWNER):
        page = pages.read(kind)
        assert len(rows(kind, page)) == expected
        assert len(pages.config['seen']) == expected
        deferred = f'op_{expected + 1:032x}'
        assert pm_read_cache.recall('examined-fixture', (deferred,)) == (False, None)
        assert not page.get('source_visibility_filtered')
        assert not page.get('coverage', {}).get('visibility_filtered')
    assert cursor(kind, page) is not None
    following = pages.read(kind, cursor(kind, page))
    first = rows(kind, following)[0]
    assert first.get('operation_id', first.get('resource_id')) == deferred


@pytest.mark.parametrize('kind', READERS)
@pytest.mark.parametrize('later', [False, True])
@pytest.mark.parametrize('threshold', ['observations', 'records', 'bytes'])
def test_atomic_record_finishes_when_it_crosses_the_response_threshold(pages, kind, later, threshold):
    if threshold == 'observations':
        pages.config['observation_cost'] = lambda n: 19 if later and n == 1 else 21
    elif threshold == 'records':
        pages.config['cost'] = lambda n: (1999 if later and n == 1 else 2001, 0)
    else:
        pages.config['cost'] = lambda n: (0, 15_999_999 if later and n == 1 else 16_000_001)
    page = pages.read(kind)
    assert len(rows(kind, page)) == len(pages.config['seen']) == (2 if later else 1)
    assert cursor(kind, page) is not None


@pytest.mark.parametrize('kind', READERS)
@pytest.mark.parametrize('visible_prefix', [False, True])
def test_hidden_pages_and_tail_progress_to_terminal_without_skipping_rows(pages, kind, visible_prefix):
    pages.config['observations'] = True
    pages.config['hidden'] = {f'op_{n:032x}' for n in range(6 if visible_prefix else 1, 56)}
    after, traversed = None, []
    while True:
        page = pages.read(kind, after)
        assert len(rows(kind, page)) == (5 if visible_prefix and not traversed else 0)
        after = cursor(kind, page)
        traversed.append(after)
        if after is None:
            break
    assert len(traversed) == 3
    assert pages.config['seen'] == [f'op_{n:032x}' for n in range(1, 56)]


@pytest.mark.parametrize('kind', READERS)
@pytest.mark.parametrize('hidden', [False, True])
def test_no_report_closure_keeps_main_reader_response_bytes(pages, kind, hidden):
    pages.config['hidden'] = {f'op_{n:032x}' for n in range(1, 56, 2)} if hidden else set()
    # A no-report receipt page has no receipts; use its valid terminal cursor.
    after = reports.MAX_CURSOR if kind == 'receipts' else None
    current, previous = pages.read(kind, after), pages.read(kind, after, old=True)
    assert json.dumps(current, ensure_ascii=False) == json.dumps(previous, ensure_ascii=False)


def test_charge_observer_counts_failed_closures_and_serializes_only_once(pages, monkeypatch):
    original, serializations = sources.canonical, []
    def canonical(value):
        serializations.append(value)
        return original(value)
    monkeypatch.setattr(sources, 'canonical', canonical)
    monkeypatch.setattr(sources, 'MAX_AUTH_BYTES', 3)
    with work.scope(pages.db) as budget:
        closure = sources._Closure(pages.ops, OWNER, pages.wid)
        with pytest.raises(OperationError):
            closure.charge('字')
        assert budget.records == 1 and budget.bytes == len(original('字').encode())
        assert serializations == ['字']
        with pytest.raises(TypeError):
            closure.charge(object())
        assert budget.records == 2


def test_nested_list_finishes_inside_the_enclosing_atomic_record(pages):
    pages.config['records'] = 1000
    with work.scope(pages.db) as budget:
        with budget.record():
            work.charge(pages.db, 2000, 0)
            assert len(pages.read('operations')['operations']) == 55
            assert not budget.stopped
        assert budget.stopped
    assert len(pages.read('operations')['operations']) == 2
