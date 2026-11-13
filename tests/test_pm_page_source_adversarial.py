"""Unmodified authorization denies missing originals, including inherited hosts."""
import json
import sqlite3

import pytest

from bat_agent_connector import pm_source_lineage as lineage
from bat_agent_connector.api_auth import Principal
from bat_agent_connector.operations import OperationError
from tests.pm_page_source_reference import namespace

OWNER = Principal('fixture-reader', frozenset({'observe'}))


@pytest.fixture
def hidden_db(tmp_path):
    db = sqlite3.connect(tmp_path / 'hidden-originals.db', isolation_level=None)
    db.execute('CREATE TABLE operations(operation_id TEXT,action TEXT,target TEXT,params TEXT)')
    db.execute('CREATE TABLE pm_source_lineage(resource_type TEXT,resource_id TEXT,original_operation_id TEXT,creation_operation_id TEXT)')
    for i in range(65):
        oid = f'op_{i:032x}'
        db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)', ('operation', oid, oid, oid))
    db.execute('INSERT INTO pm_source_lineage VALUES(?,?,?,?)',
        ('session', 'fixture-host/fixture-session', 'op_' + '0' * 32, 'op_' + '0' * 32))
    yield db
    db.close()


@pytest.mark.parametrize('wrapped', [False, True])
def test_reviewer_inherited_host_case_uses_real_authorization(hidden_db, wrapped):
    page = {'sessions': [{'session_id': 'fixture-session', 'text': 'PRIVATE-SENTINEL'}], 'count': 1}
    value = {'host': 'fixture-host', **page}
    if wrapped:
        value = {'host': 'fixture-host', 'snapshot': {'sessions': page}, 'sync': {}, 'capabilities': {}, 'pagination': {}}
    with pytest.raises(OperationError) as old:
        namespace()['read_response'](hidden_db, OWNER, value)
    assert old.value.status == 404
    result = lineage.read_response(hidden_db, OWNER, value)
    actual = result['snapshot']['sessions'] if wrapped else result
    assert actual['sessions'] == [] and actual['count'] == 0 and actual['source_visibility_filtered']
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    with pytest.raises(OperationError) as unknown:
        lineage.read_response(hidden_db, OWNER, {'host': 'fixture-host', 'unknown': page})
    assert unknown.value.status == 404


def test_oversized_hidden_row_still_refuses_whole_page(hidden_db):
    value = {'operations': [{'operation_ids': [f'op_{i:032x}' for i in range(65)]}]}
    for read in (namespace()['read_response'], lineage.read_response):
        with pytest.raises(OperationError) as caught:
            read(hidden_db, OWNER, value)
        assert (caught.value.code, caught.value.message, caught.value.status) == (
            'PM_SOURCE_LINEAGE_LIMIT', 'Too many original sources for one document', 409)


def test_deployment_and_established_groups_preserve_legacy_count(hidden_db):
    hidden = {'operation_id': 'op_' + '0' * 32, 'deployment_id': 'fixture-deployment'}
    value = {'operations': [hidden, {'legacy': 'readable'}],
             'items': [hidden, {'deployment_id': 'legacy-deployment'}], 'next_cursor': 'fixed', 'count': 4}
    result = lineage.read_response(hidden_db, OWNER, value)
    assert result == namespace()['read_response'](hidden_db, OWNER, value)
    # Existing filter_page calls update count for the group that changed last.
    assert result['count'] == 1 and result['source_visibility_filtered']
    assert result['next_cursor'] == 'fixed'
