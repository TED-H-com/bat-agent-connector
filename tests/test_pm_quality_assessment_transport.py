"""Exact original quality receipts share the existing read authority on every transport."""
from __future__ import annotations

import copy
import uuid
from urllib.parse import urlencode

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from bat_agent_connector import api_auth, mcp_server, pm_reads
from bat_agent_connector import pm_quality_reviews as quality
from bat_agent_connector.operations import OperationError
from bat_agent_connector.task_daemon import API_RPC
from tests import test_pm_quality_reviews as reviews
from tests.test_api_v1 import http, token
from tests.test_pm_plan_operations import OWNER
from tests.test_pm_run_api import unpack
from tests.test_pm_work_packages import member

world, pm, ready, fixed, reviewed = reviews.world, reviews.pm, reviews.ready, reviews.fixed, reviews.reviewed
METHOD = 'pm_quality_assessment_get'
REVIEW = 'pm_quality_review_get'


@pytest.fixture
async def assessed(reviewed):
    w = reviewed
    row = await reviews.new_review(w)
    await reviews.execute(w, reviews.assessment(w, row), actor=reviews.REVIEWER)
    w['review'] = reviews.read(w, row)
    w['exact'] = {'workspace_id': w['workspace_id'], 'review_id': row['review_id'],
        **{key: w['review']['assessment'][key] for key in ('operation_id', 'version', 'document_digest')}}
    return w


def path(params, *, exact=True):
    base = f"/api/v1/pm/workspaces/{params['workspace_id']}/quality-reviews/{params['review_id']}"
    return base + '/assessments/' + params['operation_id'] + '?' + urlencode(
        {key: params[key] for key in ('version', 'document_digest')}) if exact else base


def table_counts(db):
    # All schema table names are trusted; include operation/event/collector-marker tables.
    return {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


async def rpc(w, auth, method, params):
    _, body = await http(w['port'], 'POST', '/rpc', tok=auth, body={'method': method, 'params': params})
    return body


def mcp(w, monkeypatch, auth):
    monkeypatch.setenv('BATC_TASK_URL', f"http://127.0.0.1:{w['port']}/rpc")
    monkeypatch.setenv('BATC_API_TOKEN', auth)
    return mcp_server.build_server(w['d'].fleet.config, read_only=True, principal_only=True)


async def test_exact_assessment_http_daemon_mcp_parity_and_no_effects(assessed, monkeypatch, mock):
    w, d = assessed, assessed['d']
    params = w['exact']
    await member(w, 'quality-reader', 'reader')
    credentials = [token(d, OWNER.actor, 'observe'), token(d, 'quality-reader', 'observe'), d._admin_token]
    for auth in credentials:
        principal = api_auth.authenticate(d.ops.db, auth, d._admin_token)
        if principal.admin:
            # Existing membership override is preserved, not silently tightened by this read.
            assert not d.ops.db.execute('SELECT 1 FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
                (w['workspace_id'], principal.actor)).fetchone()
        expected = quality.get_assessment(d.ops, principal, **params)
        assert expected['current_applicability'] == 'not_evaluated'
        assert expected['semantics'] == 'immutable_original_assessment'
        assert pm_reads.read(d.ops, principal, REVIEW, {key: params[key] for key in ('workspace_id', 'review_id')})['review']['assessment'] == expected['assessment']
        server, fleet = mcp(w, monkeypatch, auth)
        try:
            tools = {tool.name: tool for tool in await server.list_tools()}
            assert API_RPC[METHOD] == 'observe' and METHOD in mcp_server.READ_TOOLS
            assert tools[METHOD].annotations.read_only_hint
            assert set(tools[METHOD].input_schema['required']) == set(params)
            assert tools[METHOD].input_schema['additionalProperties'] is False
            before = (table_counts(d.ops.db), d.ops.db.total_changes, d.journal.api_head(),
                len(mock.invokes), len(w['gh'].requests), list(w['runner'].phases))
            assert pm_reads.read(d.ops, principal, METHOD, params) == expected
            assert await http(w['port'], 'GET', path(params), tok=auth) == (200, expected)
            assert await rpc(w, auth, METHOD, params) == {'result': expected}
            assert unpack(await server.call_tool(METHOD, params)) == expected
            assert before == (table_counts(d.ops.db), d.ops.db.total_changes, d.journal.api_head(),
                len(mock.invokes), len(w['gh'].requests), list(w['runner'].phases))
        finally:
            await fleet.close()


async def test_exact_assessment_never_calls_model_provider_or_collector(assessed, monkeypatch):
    w, d = assessed, assessed['d']
    def forbidden(*args, **kwargs):
        pytest.fail('An exact quality read must not call a model, provider, BAT or collector')
    monkeypatch.setattr(d.fleet, 'client', forbidden)
    monkeypatch.setattr(d.result_collector, 'collect_host', forbidden)
    monkeypatch.setitem(d.ops.context, 'pm_native_runtime_factory', forbidden)
    monkeypatch.setattr(w['gh'], 'before_request', forbidden)
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    before = table_counts(d.ops.db)
    try:
        expected = pm_reads.read(d.ops, OWNER, METHOD, w['exact'])
        assert await http(w['port'], 'GET', path(w['exact']), tok=auth) == (200, expected)
        assert await rpc(w, auth, METHOD, w['exact']) == {'result': expected}
        assert unpack(await server.call_tool(METHOD, w['exact'])) == expected
        assert table_counts(d.ops.db) == before
    finally:
        await fleet.close()


@pytest.mark.parametrize('denial', ['nonmember', 'removed_member', 'source_revoked', 'admin_source_revoked'])
async def test_exact_assessment_refusals_match_existing_read_in_each_adapter(assessed, monkeypatch, denial):
    w, d = assessed, assessed['d']
    if denial == 'nonmember':
        auth = token(d, 'quality-outsider', 'observe')
    elif denial == 'removed_member':
        await member(w, 'removed-quality-reader', 'reader')
        auth = token(d, 'removed-quality-reader', 'observe')
        d.ops.db.execute('DELETE FROM pm_workspace_members WHERE workspace_id=? AND actor=?',
            (w['workspace_id'], 'removed-quality-reader'))
    else:
        auth = d._admin_token if denial == 'admin_source_revoked' else token(d, OWNER.actor, 'observe')
        # Withdraw the original verifier's source, not a browser/cache-only flag.
        reviews.withdraw_verifier(w)
    params = w['exact']
    previous = {key: params[key] for key in ('workspace_id', 'review_id')}
    server, fleet = mcp(w, monkeypatch, auth)
    before = table_counts(d.ops.db)
    try:
        review_http = await http(w['port'], 'GET', path(params, exact=False), tok=auth)
        assert review_http[0] == 404
        # An operation selector reaches the shared lineage barrier before the
        # helper. Work quality carries a source selector too; compare its real
        # refusal without changing either adapter's established error ordering.
        baseline, baseline_params, baseline_path = REVIEW, previous, path(params, exact=False)
        if denial in {'nonmember', 'removed_member'}:
            baseline = 'pm_work_quality_get'
            baseline_params = {'workspace_id': w['workspace_id'], 'work_item_id': w['review']['work_item_id']}
            baseline_path = f"/api/v1/pm/workspaces/{w['workspace_id']}/work/{w['review']['work_item_id']}/quality"
        existing_http = await http(w['port'], 'GET', baseline_path, tok=auth)
        assert existing_http[0] == 404
        assert await http(w['port'], 'GET', path(params), tok=auth) == existing_http
        existing_rpc = await rpc(w, auth, baseline, baseline_params)
        assert set(existing_rpc) == {'error', 'message'} and existing_rpc['error'] == 'NOT_FOUND'
        assert await rpc(w, auth, METHOD, params) == existing_rpc
        with pytest.raises(ToolError) as old:
            await server.call_tool(baseline, baseline_params)
        with pytest.raises(ToolError) as new:
            await server.call_tool(METHOD, params)
        assert str(new.value) == str(old.value).replace(baseline, METHOD)
        assert 'NOT_FOUND' in str(new.value)
        assert table_counts(d.ops.db) == before
    finally:
        await fleet.close()


async def test_exact_assessment_observe_scope_refusals_keep_adapter_error_formats(assessed, monkeypatch):
    w, d = assessed, assessed['d']
    auth = token(d, OWNER.actor, 'manage')
    params = w['exact']
    previous = {key: params[key] for key in ('workspace_id', 'review_id')}
    old_http = await http(w['port'], 'GET', path(params, exact=False), tok=auth)
    assert old_http[0] == 403
    assert await http(w['port'], 'GET', path(params), tok=auth) == old_http
    old_rpc = await rpc(w, auth, REVIEW, previous)
    new_rpc = await rpc(w, auth, METHOD, params)
    assert old_rpc == {'error': 'FORBIDDEN', 'message': f"{REVIEW} needs the 'observe' scope"}
    # RPC checks referenced sources before its method scope check. Preserve
    # the same refusal as the existing source-bound Work quality read.
    baseline = 'pm_work_quality_get'
    work_params = {'workspace_id': w['workspace_id'], 'work_item_id': w['review']['work_item_id']}
    source_refusal = await rpc(w, auth, baseline, work_params)
    assert source_refusal['error'] == 'NOT_FOUND' and set(source_refusal) == {'error', 'message'}
    assert new_rpc == source_refusal
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        with pytest.raises(ToolError) as old:
            await server.call_tool(baseline, work_params)
        with pytest.raises(ToolError) as new:
            await server.call_tool(METHOD, params)
        assert str(new.value) == str(old.value).replace(baseline, METHOD)
        assert '403' not in str(new.value)
    finally:
        await fleet.close()


async def test_exact_assessment_preserves_original_after_new_assessment_revocation_and_supersession(assessed, monkeypatch):
    w, d = assessed, assessed['d']
    original = pm_reads.read(d.ops, OWNER, METHOD, w['exact'])
    await reviews.execute(w, reviews.assessment(w, w['review'], verdict='rejected'), actor=reviews.REVIEWER)
    await reviews.execute(w, {'action': quality.REVOKE,
        'target': {'workspace_id': w['workspace_id'], 'review_id': w['review']['review_id']},
        'params': {'reason': 'Retain the original assessment as historical evidence'},
        'preconditions': {'expected_version': 3}, 'idempotency_key': uuid.uuid4().hex})
    await reviews.new_review(w, previous=w['review']['review_id'])
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        assert await http(w['port'], 'GET', path(w['exact']), tok=auth) == (200, original)
        assert await rpc(w, auth, METHOD, w['exact']) == {'result': original}
        assert unpack(await server.call_tool(METHOD, w['exact'])) == original
        for key, value in [('version', 3), ('document_digest', 'f' * 64), ('operation_id', 'op_' + 'f' * 32),
                           ('workspace_id', 'ws_' + 'f' * 32), ('review_id', 'pmqr_' + 'f' * 32)]:
            params = {**w['exact'], key: value}
            with pytest.raises(OperationError) as refusal:
                pm_reads.read(d.ops, OWNER, METHOD, params)
            assert refusal.value.status == 404
            expected_error = {'code': refusal.value.code, 'message': refusal.value.message}
            assert await http(w['port'], 'GET', path(params), tok=auth) == (404, {'error': expected_error})
            assert await rpc(w, auth, METHOD, params) == {'error': refusal.value.code, 'message': refusal.value.message}
            with pytest.raises(ToolError, match=refusal.value.code):
                await server.call_tool(METHOD, params)
    finally:
        await fleet.close()


async def test_exact_assessment_http_selectors_are_exact_typed_bounded_and_never_body_defaults(assessed):
    w, d = assessed, assessed['d']
    auth = token(d, OWNER.actor, 'observe')
    base = path(w['exact']).split('?')[0]
    digest = w['exact']['document_digest']
    for query in ['', 'version=2', f'document_digest={digest}', f'version=2&document_digest={digest}&latest=true',
                  f'version=2&version=2&document_digest={digest}', f'version=2&document_digest={digest}&document_digest={digest}',
                  f'version=&document_digest={digest}', 'version=2&document_digest=',
                  f'version=2&%76ersion=2&document_digest={digest}',
                  *[f'version={version}&document_digest={digest}' for version in ('true', '-1', '0', '1', '34', '2.0', '9999999999999999999999', '%202', '%2B2')],
                  *[f'version=2&document_digest={value}' for value in ('a' * 63, 'a' * 65, 'A' * 64, 'z' * 64)]]:
        status, body = await http(w['port'], 'GET', base + '?' + query, tok=auth)
        assert status == 422, (query, body)
    # Existing HTTP GET bodies are ignored; they cannot provide missing selectors.
    assert (await http(w['port'], 'GET', base, tok=auth, body={'version': 2, 'document_digest': digest}))[0] == 422
    assert await http(w['port'], 'GET', path(w['exact']), tok=auth, body={'version': 3}) == await http(
        w['port'], 'GET', path(w['exact']), tok=auth)


async def test_exact_assessment_central_and_mcp_fields_are_strict_and_byte_limit_is_shared(assessed, monkeypatch):
    w, d = assessed, assessed['d']
    auth = token(d, OWNER.actor, 'observe')
    server, fleet = mcp(w, monkeypatch, auth)
    try:
        for change in [{'version': value} for value in (None, True, '2', 2.0, 1, 34)] + [
                {'document_digest': 'A' * 64}, {'operation_id': 'latest'}, {'latest': True}]:
            params = {**w['exact'], **change}
            with pytest.raises(OperationError) as invalid:
                pm_reads.read(d.ops, OWNER, METHOD, params)
            assert invalid.value.status == 422
            assert (await rpc(w, auth, METHOD, params))['error'] == invalid.value.code
            with pytest.raises(ToolError):
                await server.call_tool(METHOD, params)
        for key in w['exact']:
            params = {k: v for k, v in w['exact'].items() if k != key}
            with pytest.raises(OperationError):
                pm_reads.read(d.ops, OWNER, METHOD, params)
            with pytest.raises(ToolError):
                await server.call_tool(METHOD, params)
        before = table_counts(d.ops.db)
        monkeypatch.setattr(quality, 'MAX_BYTES', 1)
        with pytest.raises(OperationError) as bounded:
            quality.get_assessment(d.ops, OWNER, **w['exact'])
        assert bounded.value.status == 413
        assert await http(w['port'], 'GET', path(w['exact']), tok=auth) == (
            413, {'error': {'code': bounded.value.code, 'message': bounded.value.message}})
        assert await rpc(w, auth, METHOD, w['exact']) == {'error': bounded.value.code, 'message': bounded.value.message}
        with pytest.raises(ToolError, match=bounded.value.code):
            await server.call_tool(METHOD, w['exact'])
        assert table_counts(d.ops.db) == before
    finally:
        await fleet.close()


async def test_generic_verification_facet_keeps_last_execution_match_without_candidate_filter(assessed, monkeypatch):
    from bat_agent_connector.pm_attempt_result_reads import _verification_section
    w, ops = assessed, assessed['d'].ops
    first = w['review']
    last = copy.deepcopy(first)
    last.update(review_id='pmqr_' + 'a' * 32, qualified=False, acceptance='rejected')
    last['context']['native_result']['candidate_commit'] = 'f' * 40
    unrelated = copy.deepcopy(first)
    unrelated['context']['execution_operation_id'] = 'op_' + 'b' * 32
    rows = [first, last, unrelated]
    monkeypatch.setattr(quality, 'for_work', lambda *args: {
        'reviews': rows, 'next_cursor': None, 'visibility_filtered': False})
    def read_facet():
        return _verification_section(ops, OWNER, w['workspace_id'], first['work_item_id'],
            w['child']['operation_id'], w['child']['operation_id'], None, None, w['candidate'])
    block, _, decision, facet, _, _ = read_facet()
    assert block['quality_reviews']['current']['review_id'] == last['review_id']
    assert decision['acceptance'] == 'rejected'
    assert facet == {'status': 'blocked', 'semantic_acceptance': 'rejected'}
    rows[:] = [last, unrelated, first]
    block, _, _, facet, _, _ = read_facet()
    assert block['quality_reviews']['current']['review_id'] == first['review_id']
    assert facet == {'status': 'passed', 'semantic_acceptance': 'accepted'}
