"""One original maintenance report across shared reads, exports and stopped stages."""
import json
from types import SimpleNamespace

import pytest

from bat_agent_connector import mcp_server, pm_brief_exports, pm_snapshots
from bat_agent_connector import pm_maintenance_checks as checks
from bat_agent_connector import pm_maintenance_review as review
from bat_agent_connector import pm_maintenance_review_schedule as weekly
from tests import test_pm_github_observation_api as githubapi
from tests import test_pm_maintenance_review as domain
from tests.test_api_v1 import http, token, write_frames
from tests.test_pm_brief_exports import visible
from tests.test_pm_github_observation_api import (
    baseline_ready,  # noqa: F401 - fixture dependencies
    daemon,  # noqa: F401 - fixture dependencies
    release_ready,  # noqa: F401 - fixture dependencies
    served,  # noqa: F401 - fixture dependencies
)
from tests.test_pm_governance_api import send
from tests.test_pm_lifecycle import decide
from tests.test_pm_release_baselines import OWNER, READER
from tests.test_pm_run_api import unpack

ready = githubapi.ready


async def test_installed_weekly_schedule_uses_existing_worker_and_shared_status(ready, monkeypatch):
    d, port, wid, _ = ready
    await decide(d.ops, wid, 'set_stage', params={'stage': 'development'})
    token(d, OWNER.actor, 'observe', 'manage')
    record = checks.policies.current_policy(d.ops, OWNER, wid)['policy']
    now = weekly.timing(record, record['created_at'])['next_due'] + 1
    for module in (checks, review, pm_snapshots):
        monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda: now))
    tick = checks.tick(d.ops)
    assert tick['weekly_scheduled'] == 1
    original = d.ops.db.execute('SELECT operation_id FROM pm_maintenance_review_windows').fetchone()[0]
    reads = list(d.ops.context['github'].reads)
    await d.ops._execute(original)
    done = d.ops.get(original)
    assert done['status'] == 'succeeded', done
    assert d.ops.context['github'].reads == reads
    auth = token(d, READER.actor, 'observe')
    caps = (await http(port, 'GET', '/api/v1/capabilities', tok=auth))[1]
    assert caps['features']['pm_workspaces']['maintenance_checks']['weekly_review_execution']
    status, value = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}/maintenance-schedule', tok=auth)
    assert status == 200 and value['schedule']['weekly_review']['execution_available']
    assert value['schedule']['weekly_review']['pending_report'] == done['result']['review_id']
    current = review.current_review(d.ops, READER, wid)
    assert current['review']['status'] == 'prepared' and current['completion'] is None
    report = current['snapshot']['maintenance']['report']
    assert current['review']['operation_id'] == original and current['snapshot']['operation_id'] == original
    assert report['schedule']['window'] == 1
    assert not report['schedule']['missed_weeks_checked']


async def prepare(d, port, wid):
    end = review.time.time() - 1
    original = domain.request(d.ops, {'workspace_id': wid}, period_start=end - 7 * 86400, period_end=end)
    operation = await send(d, port, original)
    return original, operation, review.get_review(d.ops, OWNER, wid, operation['result']['review_id'])


async def test_http_rpc_mcp_parity_never_prepares_or_completes_a_meeting(ready, monkeypatch, mock):
    d, port, wid, _ = ready
    original, operation, saved = await prepare(d, port, wid)
    rid = saved['review']['review_id']
    await send(d, port, domain.mutation(d.ops, {'workspace_id': wid}, rid, 'note', {
        'content': 'Discuss original coverage gaps', 'rationale': 'Keep the discussion separate'}))
    auth = token(d, READER.actor, 'observe')
    monkeypatch.setenv('BATC_TASK_URL', f'http://127.0.0.1:{port}/rpc')
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    server, fleet = mcp_server.build_server(d.fleet.config, read_only=True, principal_only=True)
    changes, reads = d.ops.db.total_changes, list(d.ops.context['github'].reads)
    try:
        discovered = {tool.name: tool for tool in await server.list_tools()}
        for method, fields, suffix in [
            ('pm_maintenance_reviews_list', {'after': 0, 'limit': 1}, '?after=0&limit=1'),
            ('pm_maintenance_review_current_get', {'history_limit': 1}, '/current?history_limit=1'),
            ('pm_maintenance_review_get', {'review_id': rid, 'history_limit': 1}, '/' + rid + '?history_limit=1'),
            ('pm_maintenance_review_events_list', {'review_id': rid, 'after': 0, 'limit': 1}, '/' + rid + '/events?after=0&limit=1'),
        ]:
            assert discovered[method].annotations.read_only_hint
            status, result = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}/maintenance-reviews' + suffix, tok=auth)
            assert status == 200, result
            params = {'workspace_id': wid, **fields}
            assert await d.call_api(method, params, READER) == result
            assert unpack(await server.call_tool(method, params)) == result
        assert d.ops.db.total_changes == changes
        replay = await send(d, port, original)
        assert replay['operation_id'] == operation['operation_id'] and replay['result'] == operation['result']
        assert d.ops.context['github'].reads == reads
        assert not review.get_review(d.ops, OWNER, wid, rid)['completion']
        assert not write_frames(mock)
    finally:
        await fleet.close()


async def test_original_source_withdrawal_hides_review_snapshot_operations_and_exports(ready):
    d, port, wid, project = ready
    _, operation, saved = await prepare(d, port, wid)
    rid, sid = saved['review']['review_id'], saved['snapshot']['snapshot_id']
    d.ops.db.execute("UPDATE projects SET repositories='[]' WHERE project_id=?", (project,))
    auth = token(d, READER.actor, 'observe')
    for suffix in ['/maintenance-reviews/' + rid, '/maintenance-reviews/' + rid + '/events',
                   '/snapshots/' + sid, '/snapshots/' + sid + '/brief-export?format=html&locale=en']:
        assert (await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}' + suffix, tok=auth))[0] == 404
    assert (await http(port, 'GET', '/api/v1/operations/' + operation['operation_id'], tok=auth))[0] == 404
    for suffix in ['/maintenance-reviews', '/maintenance-reviews/current']:
        status, result = await http(port, 'GET', f'/api/v1/pm/workspaces/{wid}' + suffix, tok=auth)
        assert status == 200 and result['visibility_filtered']
        assert rid not in json.dumps(result) and sid not in json.dumps(result)
    status, events = await http(port, 'GET', '/api/v1/events?after=0&limit=500', tok=auth)
    assert status == 200 and rid not in json.dumps(events) and sid not in json.dumps(events)


@pytest.mark.parametrize('stage', ['paused', 'closed'])
async def test_local_preparation_notes_and_completion_remain_available_while_stopped(ready, stage):
    d, port, wid, _ = ready
    await decide(d.ops, wid, 'set_stage', params={'stage': 'development'})
    await decide(d.ops, wid, 'pause' if stage == 'paused' else 'close')
    _, _, saved = await prepare(d, port, wid)
    rid = saved['review']['review_id']
    note = await send(d, port, domain.mutation(d.ops, {'workspace_id': wid}, rid, 'note', {
        'content': 'Record the stopped project discussion', 'rationale': 'No work starts'}))
    await send(d, port, domain.mutation(d.ops, {'workspace_id': wid}, rid, 'complete',
        domain.completion([note['result']['event_id']])))
    detail = review.get_review(d.ops, OWNER, wid, rid)
    assert detail['completion']['document']['meeting_completed']
    assert not detail['snapshot']['maintenance']['report']['meeting_completed']
    assert not detail['completion']['document']['lifecycle_changed']


@pytest.mark.parametrize('format', ['markdown', 'html', 'svg'])
@pytest.mark.parametrize('locale', ['en', 'zh-TW'])
async def test_exports_keep_fixed_report_separate_from_later_decisions(ready, format, locale):
    d, port, wid, _ = ready
    _, _, saved = await prepare(d, port, wid)
    rid, sid = saved['review']['review_id'], saved['snapshot']['snapshot_id']
    before = pm_brief_exports.render_brief(d.ops, OWNER, wid, sid, format=format, locale=locale)
    text = visible(before)
    assert ('Maintenance Review' if locale == 'en' else '維護 Review') in text
    assert 'whole_period_no_events_proven' in ''.join(text.split())
    assert before['renderer_version'] == 2
    assert before['coverage']['projection'] == 'project_brief_with_maintenance_review'
    assert before['snapshot_digest'] == saved['snapshot']['snapshot_digest']
    note = await send(d, port, domain.mutation(d.ops, {'workspace_id': wid}, rid, 'note', {
        'content': 'A later discussion <script>alert(1)</script>', 'rationale': 'Preserve original report'}))
    await send(d, port, domain.mutation(d.ops, {'workspace_id': wid}, rid, 'complete',
        domain.completion([note['result']['event_id']])))
    assert pm_brief_exports.render_brief(d.ops, OWNER, wid, sid, format=format, locale=locale) == before
    assert 'A later discussion' not in text


async def test_exact_routes_query_bounds_and_current_read_scope(ready):
    d, port, wid, _ = ready
    _, _, saved = await prepare(d, port, wid)
    rid = saved['review']['review_id']
    auth = token(d, READER.actor, 'observe')
    root = f'/api/v1/pm/workspaces/{wid}/maintenance-reviews'
    for suffix in ['?after=', '?after=9223372036854775808', '?limit=101', '?run=true', '/current?after=0',
                   '/current?history_limit=0', '/' + rid + '?history_limit=1&history_limit=2', '/' + rid + '/events?history_limit=1']:
        assert (await http(port, 'GET', root + suffix, tok=auth))[0] == 422, suffix
    for suffix in ['/latest', '/pmrevw_bad', '/' + rid + '/complete']:
        assert (await http(port, 'GET', root + suffix, tok=auth))[0] == 404
    assert (await http(port, 'GET', root + '?after=9223372036854775807', tok=auth))[0] == 200
    assert (await http(port, 'GET', root, tok=token(d, OWNER.actor, 'manage')))[0] == 403
