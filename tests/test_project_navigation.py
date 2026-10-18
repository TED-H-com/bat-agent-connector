"""Navigation reads stay small, preserve state and revalidate on the next request."""
from __future__ import annotations

import json

import pytest

from bat_agent_connector import work_items
from tests.test_api_v1 import http, token
from tests.test_work_items import PERSON, act, daemon, get, item, project, served

_ = daemon, served


async def test_navigation_omits_unread_detail_and_preserves_completion(served, record_property):
    d, port = served
    pid = await project(d)
    wid = await item(d, pid, 'Keep original acceptance', goal='goal ' * 1200,
        request='request ' * 1200, acceptance='acceptance ' * 1200,
        steps=[{'text': 'step ' * 60, 'done': True} for _ in range(20)])
    auth = token(d, 'navigation-reader', 'observe')
    status, full = await http(port, 'GET', '/api/v1/projects/' + pid, tok=auth)
    assert status == 200
    status, small = await http(port, 'GET', '/api/v1/projects/' + pid + '?projection=navigation', tok=auth)
    assert status == 200
    assert small['projection'] == 'navigation'
    row, original = small['work_items'][0], full['work_items'][0]
    assert row['work_item_id'] == wid and row['title'] == original['title']
    assert row['completion'] == {key: original['completion'][key] for key in ('display_state', 'pending', 'approved')}
    assert row['completion']['pending'] is True
    assert row['step_summary'] == {'total': 20, 'done': 20, 'next': None}
    assert not {'request', 'goal', 'acceptance', 'steps', 'attachments'} & row.keys()
    assert 'fingerprint' not in row['completion']
    assert small['project']['counts'] == full['project']['counts']
    full_bytes, navigation_bytes = len(json.dumps(full).encode()), len(json.dumps(small).encode())
    assert navigation_bytes < full_bytes / 8
    record_property('full_project_bytes', full_bytes)
    record_property('navigation_project_bytes', navigation_bytes)
    assert get(d, wid)['steps'] == original['steps']
    version = get(d, wid)['version']
    await act(d, PERSON, 'work_item.update', {'work_item_id': wid},
        {'steps': [{'text': 'Verify original evidence', 'done': False}]}, {'expected_version': version})
    status, updated = await http(port, 'GET', '/api/v1/projects/' + pid + '?projection=navigation', tok=auth)
    assert status == 200 and updated['work_items'][0]['step_summary']['next'] == 'Verify original evidence'
    assert updated['work_items'][0]['completion']['pending'] is False
    status, detail = await http(port, 'GET', '/api/v1/work-items/' + wid, tok=auth)
    assert status == 200 and detail['work_item']['request'] == original['request']
    assert detail['work_item']['steps'] == [{'text': 'Verify original evidence', 'done': False}]


@pytest.mark.parametrize('value', ['unknown', '', 'navigation&projection=full'])
async def test_http_refuses_ambiguous_navigation_projection(served, value):
    d, port = served
    pid = await project(d)
    auth = token(d, 'navigation-reader', 'observe')
    status, _ = await http(port, 'GET', '/api/v1/projects/' + pid + '?projection=' + value, tok=auth)
    assert status == 422


def test_core_projection_rejects_non_string_or_unknown_values(daemon):
    from bat_agent_connector.operations import OperationError
    for value in [True, [], {}, 'unknown']:
        with pytest.raises(OperationError) as caught:
            work_items.project_get(daemon.journal.db, 'prj_' + '0' * 20, projection=value)
        assert caught.value.code == 'INVALID_PARAMS'
