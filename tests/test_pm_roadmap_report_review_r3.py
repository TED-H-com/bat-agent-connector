"""Independent review regressions: whole-document depth and producer identities."""
from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import pytest

from bat_agent_connector import pm_roadmap_report_schema as schema
from bat_agent_connector import pm_roadmap_report_sources as sources
from bat_agent_connector import pm_roadmap_reports as reports
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import token
from tests.test_pm_api import OWNER
from tests.test_pm_evidence_api import capture, prepared
from tests.test_pm_roadmap_report_api import anchor, daemon, served  # noqa: F401
from tests.test_pm_roadmap_report_review import document, fault, raw_http
from tests.test_pm_roadmap_report_schema import inventory, world
from tests.test_pm_roadmap_report_sources import READER, ops, selected  # noqa: F401
from tests.test_work_items import act


def nested(depth, kind='array'):
    value = 'opaque'
    for _ in range(depth):
        value = [value] if kind == 'array' else {'extension': value}
    return value


def frame_depth():
    frame, depth = sys._getframe(), 0
    while frame:
        depth += 1
        frame = frame.f_back
    return depth


@pytest.mark.parametrize('kind', ['array', 'object'])
async def test_deep_json_real_http_and_all_downstream_stages(ops, served, monkeypatch, kind):  # noqa: F811
    d, port = served
    chosen = await selected(ops)
    wid, rid = chosen[0]['workspace_id'], chosen[3]['revision_id']
    monkeypatch.setattr(d, 'ops', ops)
    path = f'/api/v1/pm/workspaces/{wid}/roadmap/revisions/{rid}/report'
    auth = token(d, READER.actor, 'observe')
    original = sources._Closure(ops, OWNER, wid).collect(rid)[0]['document']
    # Measure the real HTTP stack, including the recursive deepcopy frames.
    maximum = 0
    stages = set()
    def profile(frame, event, arg):
        nonlocal maximum
        if event == 'call':
            maximum = max(maximum, frame_depth())
            if frame.f_code.co_filename.endswith(('pm_roadmap_report_sources.py', 'pm_roadmap_report_schema.py', 'pm_roadmap_reports.py')):
                stages.add(frame.f_code.co_name)
    for depth in (getattr(schema, 'MAX_CONTAINER_DEPTH', 12), getattr(schema, 'MAX_CONTAINER_DEPTH', 12) + 1, 500):
        with fault(ops):
            changed = copy.deepcopy(original)
            if depth == 500:
                # Preserve the original review probe in the printed coverage field.
                changed['coverage']['extension'] = nested(depth - 2, kind)
            else:
                # Root is container 1; isolate depth from the separate SVG size bound.
                changed['extension'] = nested(depth - 1, kind)
            document(ops, 'pm_roadmap_revisions', 'revision_id', rid, changed)
            ops.db.execute('UPDATE pm_roadmap_revisions SET document_digest=? WHERE revision_id=?',
                           (sources.digest(changed), rid))
            closure = sources._Closure(ops, OWNER, wid).collect(rid)
            if depth > getattr(schema, 'MAX_CONTAINER_DEPTH', 12):
                with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
                    schema.validate_v1(closure)
                with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
                    sources._verify(closure)
            else:
                schema.validate_v1(closure)
                sources._verify(closure)
                sources.canonical(sources.project(closure))
            for format in reports.FORMATS:
                for locale in ('en', 'zh-TW'):
                    if depth == getattr(schema, 'MAX_CONTAINER_DEPTH', 12):
                        expected = reports.render_fixed(sources.project(closure), format=format, locale=locale)
                    sys.setprofile(profile)
                    try:
                        status, body = await raw_http(port, path + f'?format={format}&locale={locale}', auth)
                    finally:
                        sys.setprofile(None)
                    if depth > getattr(schema, 'MAX_CONTAINER_DEPTH', 12):
                        assert status == 409, body
                        assert b'PM_REPORT_SOURCE_CHANGED' in body
                    else:
                        assert status == 200, body
                        assert json.loads(body) == expected
    assert {'collect', 'validate_v1', '_verify', 'project', 'render_fixed'} <= stages
    assert sys.getrecursionlimit() - maximum > 500
    print('real HTTP stack maximum', maximum, 'remaining Python frames', sys.getrecursionlimit() - maximum)


async def test_long_multibyte_reference_through_producers(served):  # noqa: F811
    """The reviewer's inventory → link → capture → snapshot → observation probe."""
    d, port = served
    target, _, binding = await prepared(d)
    wid = target['workspace_id']
    host = next(iter(d.fleet.config.hosts))
    sid = '漢' * 180
    ref = host + '/' + sid
    assert len(ref) <= 300 and len(ref.encode()) > 512
    d.inventory._record_success(host, 1700000000.0,
        [{'session_id': sid, 'api_access': 'read_only'}], 'fixture')
    await act(d, OWNER, 'work_item.link', {'work_item_id': target['work_item_id']}, {'kind': 'session', 'ref': ref})
    await capture(d, target, binding)
    fields, path = await anchor(d, wid)
    fixed = sources.load_fixed(d.ops, OWNER, wid, fields['observation_revision_id'])
    entries = [entry for entry in fixed['snapshot']['evidence'] if entry['source_kind'] in ('work_link', 'session_observation')]
    assert len(entries) == 2
    auth = token(d, OWNER.actor, 'observe')
    for format in reports.FORMATS:
        for locale in ('en', 'zh-TW'):
            expected = reports.render_fixed(fixed, format=format, locale=locale)
            status, body = await raw_http(port, path + f'?format={format}&locale={locale}', auth)
            assert status == 200, body
            assert json.loads(body) == expected


@pytest.mark.parametrize('kind', ['array', 'object'])
async def test_generated_whole_documents_include_opaque_leaves_and_extra_members(ops, kind):  # noqa: F811
    _, _, _, original = await world(ops)
    count = 0
    for index, original_document in enumerate(schema.closure_documents(original)):
        for depth in (getattr(schema, 'MAX_CONTAINER_DEPTH', 12), getattr(schema, 'MAX_CONTAINER_DEPTH', 12) + 1):
            closure = copy.deepcopy(original)
            doc = list(schema.closure_documents(closure))[index]
            doc['extension'] = nested(depth - 1, kind)
            if depth == getattr(schema, 'MAX_CONTAINER_DEPTH', 12):
                schema.validate_v1(closure)
            else:
                with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
                    schema.validate_v1(closure)
            count += 1
    # Generated opaque schema leaves receive deep values, not only empty containers.
    from tests.test_pm_roadmap_report_schema import changed, example
    for name, spec in schema.V1.items():
        for path, child in inventory(spec):
            if child['kind'] != 'json':
                continue
            for depth in (getattr(schema, 'MAX_CONTAINER_DEPTH', 12), getattr(schema, 'MAX_CONTAINER_DEPTH', 12) + 1):
                value = changed(example(spec), spec, path, nested(depth - len(path), kind))
                if depth == getattr(schema, 'MAX_CONTAINER_DEPTH', 12):
                    schema.require_depth(value)
                else:
                    with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
                        schema.require_depth(value)
                count += 1
    print('whole-document and opaque-leaf depth cases', kind, count)


def test_reference_inventory_classifies_every_original_path():
    rows = json.loads(Path('tests/fixtures/roadmap-report-reference-domains.json').read_text())
    actual = []
    for phase, schemas in [('authority', schema.AUTHORITY), ('post', schema.V1)]:
        for name, spec in schemas.items():
            for path, child in inventory(spec):
                if child['kind'] in ('ref', 'external'):
                    actual.append({'phase': phase, 'source': name, 'path': list(path), 'domain': child['kind']})
    assert len(rows) == 168
    assert actual == rows
    for value in ('漢' * 300, 'a' * 513):
        assert schema.valid(schema.EXTERNAL, value)
        assert not schema.valid(schema.REF, value)
    assert schema.valid(schema.FACT_AUTH['ref'], '漢' * 300)
    assert not schema.valid(schema.FACT_AUTH['ref'], 'a' * 301)
    for value in ('', '\ud800', [], {}, 10**100, True, 0.1):
        assert not schema.valid(schema.EXTERNAL, value)


def literals(expression):
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return {expression.value}
    if isinstance(expression, ast.IfExp):
        return literals(expression.body) | literals(expression.orelse)
    pytest.fail('A producer added a kind/basis that is not statically recognizable')


def test_producer_enumerations_do_not_drift():
    evidence = ast.parse(Path('src/bat_agent_connector/pm_evidence.py').read_text())
    kinds = set()
    for node in ast.walk(evidence):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add'
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'collector'):
            kinds |= literals(node.args[0])
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add'
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self'):
            kinds |= literals(node.args[0])
    assert kinds == set(schema.EVIDENCE_KINDS)
    maintenance = ast.parse(Path('src/bat_agent_connector/pm_maintenance_review_sources.py').read_text())
    kinds, basis = set(), set()
    for node in ast.walk(maintenance):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'ref':
            kinds |= literals(node.args[0])
            for kw in node.keywords:
                if kw.arg == 'basis':
                    basis |= literals(kw.value)
    assert kinds == set(schema.MAINTENANCE_KINDS)
    assert basis == set(schema.ACCESS_REF['fields']['basis']['values'])
