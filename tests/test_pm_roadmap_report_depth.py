"""Native-budget depth proof across complete stored documents and read entry points."""
from __future__ import annotations

import copy
import json
import sys
from contextlib import contextmanager

import pytest

from bat_agent_connector import connection_sources, pm_plans, pm_reads
from bat_agent_connector import pm_roadmap_report_schema as schema
from bat_agent_connector import pm_roadmap_report_sources as sources
from bat_agent_connector import pm_roadmap_reports as reports
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import http, token
from tests.test_pm_discussions import act, prepared, record
from tests.test_pm_evidence import capture
from tests.test_pm_roadmap import READER, milestone, revision
from tests.test_pm_roadmap_report_api import READ, daemon, served, server_for  # noqa: F401
from tests.test_pm_roadmap_report_review import document, fault, raw_http
from tests.test_pm_roadmap_report_sources import ops as ops_fixture
from tests.test_pm_run_api import unpack
from tests.test_pm_snapshots import adopt, adoption, draft, snapshot, work_for

ops = ops_fixture
KINDS = ('array', 'object', 'mixed')
DOCUMENTS = ('observation', 'baseline', 'forecast', 'snapshot', 'facts', 'plan', 'adoption')


def nested(depth, kind):
    value = 'opaque'
    for level in range(depth):
        value = [value] if kind == 'array' or (kind == 'mixed' and level % 2) else {'x': value}
    return value


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('depth', (127, 128))
def test_native_container_count_agrees(kind, depth):
    value = nested(depth, kind)
    assert schema.MAX_CONTAINER_DEPTH == 127
    if depth == 127:
        schema.require_depth(value)
        connection_sources._native_json_values(value)
    else:
        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
            schema.require_depth(value)
        with pytest.raises(ValueError):
            connection_sources._native_json_values(value)


class Frames:
    """Measure actual f_back chains, including recursion in deepcopy/JSON encoders."""
    def __init__(self):
        self.peaks = {}
        self.stages = set()

    @contextmanager
    def route(self, name):
        active = {}
        def profile(frame, event, arg):
            if event == 'return':
                active.pop(id(frame), None)
                return
            if event != 'call':
                return
            if frame.f_code.co_filename.endswith(('pm_roadmap_report_sources.py',
                    'pm_roadmap_report_schema.py', 'pm_roadmap_reports.py')):
                self.stages.add(frame.f_code.co_name)
            parent_depth = active.get(id(frame.f_back))
            if parent_depth is None:
                cursor, depth = frame, 0
                while cursor:
                    depth += 1
                    cursor = cursor.f_back
            else:
                depth = parent_depth + 1
            active[id(frame)] = depth
            self.peaks[name] = max(self.peaks.get(name, 0), depth)
        previous = sys.getprofile()
        sys.setprofile(profile)
        try:
            yield
        finally:
            sys.setprofile(previous)

    def check(self):
        # Use half the *default* limit even if a test runner raises its own limit.
        assert sys.getrecursionlimit() == 1000
        print('DEPTH_STACK', sys.version.split()[0], json.dumps(self.peaks, sort_keys=True))
        assert max(self.peaks.values()) <= 500, self.peaks
        assert {'collect', 'validate_v1', '_verify', 'project', 'render_fixed'} <= self.stages


async def fixture_closure(ops, source):
    # Small legitimate producer inputs leave room for the independent SVG bound.
    target = await prepared(ops)
    selected = await record(ops, target, slot='governance', value='One planning input')
    await act(ops, target, 'pm.thread.plan',
              {'selected_input_ids': [selected['input_id']], 'rationale': 'Plan'})
    plan = await draft(ops, target, title='Plan', requirements=[])
    adopt(adoption(ops, target, plan['revision_id']))
    wid = target['workspace_id']
    if source == 'facts':
        await capture(ops, await work_for(ops, target))
    await revision(ops, wid, milestones=[milestone(plan, requirement_ids=[])])
    await revision(ops, wid, 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': None}])
    candidate = await draft(ops, target, title='Candidate') if source == 'plan' else None
    frozen = await snapshot(ops, wid)
    observed = await revision(ops, wid, 'observation', snapshot_id=frozen['snapshot_id'])
    closure = sources._Closure(ops, READER, wid).collect(observed['revision_id'])
    if source == 'facts':
        assert closure[3]['document']['evidence']
    sources._verify(closure)
    return wid, observed['revision_id'], candidate['revision_id'] if candidate else None, closure


def inject(ops, original, candidate, source, depth, kind):
    """Corrupt only temporary stored JSON; reseal the exact original relationships."""
    closure = copy.deepcopy(original)
    observation, baseline, forecast, snapshot_row, evidence, plans, adoptions = closure
    saved = snapshot_row['document']
    if source in DOCUMENTS[:4]:
        closure[DOCUMENTS.index(source)]['document']['extension'] = nested(depth - 1, kind)
    elif source == 'facts':
        entry = saved['evidence'][0]
        # snapshot/evidence/entry/document/facts are the first five containers.
        entry['document']['facts']['extension'] = nested(depth - 5, kind)
        entry['fact_digest'] = sources.digest(entry['document'])
        row = next(row for row in evidence if row['evidence_id'] == entry['evidence_id'])
        # The deliberately over-budget 500-level input must reach the product gate.
        row.update(json.loads(json.dumps(entry)))
    elif source == 'plan':
        plan = plans[candidate]
        plan['document']['extension'] = nested(depth - 1, kind)
        plan['document_digest'] = pm_plans._files(candidate, plan['document'])['document_digest']
        saved['candidate_document']['document_digest'] = plan['document_digest']
    else:
        next(iter(adoptions.values()))['result']['extension'] = nested(depth - 1, kind)
    snapshot_row['snapshot_digest'] = sources.digest(saved)
    observation['document']['snapshot_digest'] = snapshot_row['snapshot_digest']
    for row in (observation, baseline, forecast):
        document(ops, 'pm_roadmap_revisions', 'revision_id', row['revision_id'], row['document'])
        ops.db.execute('UPDATE pm_roadmap_revisions SET document_digest=? WHERE revision_id=?',
                       (sources.digest(row['document']), row['revision_id']))
    document(ops, 'pm_document_snapshots', 'snapshot_id', snapshot_row['snapshot_id'], saved)
    ops.db.execute('UPDATE pm_document_snapshots SET snapshot_digest=? WHERE snapshot_id=?',
                   (snapshot_row['snapshot_digest'], snapshot_row['snapshot_id']))
    for row in evidence:
        document(ops, 'pm_evidence', 'evidence_id', row['evidence_id'], row['document'])
        ops.db.execute('UPDATE pm_evidence SET fact_digest=? WHERE evidence_id=?',
                       (row['fact_digest'], row['evidence_id']))
    for row in plans.values():
        document(ops, 'pm_plan_revisions', 'revision_id', row['revision_id'], row['document'])
        ops.db.execute('UPDATE pm_plan_revisions SET document_digest=? WHERE revision_id=?',
                       (row['document_digest'], row['revision_id']))
    ops.db.execute('DROP TRIGGER IF EXISTS pm_plan_adoptions_update_immutable')
    for row in adoptions.values():
        ops.db.execute('UPDATE pm_plan_adoptions SET result=? WHERE decision_id=?',
                       (json.dumps(row['result']), row['decision_id']))


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('source', DOCUMENTS)
async def test_complete_document_depth_through_routes(ops, served, monkeypatch, source, kind):  # noqa: F811
    d, port = served
    wid, rid, candidate, original = await fixture_closure(ops, source)
    monkeypatch.setattr(d, 'ops', ops)
    path = f'/api/v1/pm/workspaces/{wid}/roadmap/revisions/{rid}/report'
    fields = {'workspace_id': wid, 'observation_revision_id': rid}
    auth = token(d, READER.actor, 'observe')
    server, fleet = server_for(d, port, monkeypatch, auth)
    frames = Frames()
    try:
        for depth in (127, 128, 500):
            with fault(ops):
                inject(ops, original, candidate, source, depth, kind)
                closure = sources._Closure(ops, READER, wid).collect(rid)
                if depth > 127:
                    for gate in (schema.validate_v1, sources._verify):
                        with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
                            gate(closure)
                    for format in reports.FORMATS:
                        for locale in ('en', 'zh-TW'):
                            with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
                                reports.get_report(ops, READER, wid, rid, format=format, locale=locale)
                            status, body = await raw_http(port, path + f'?format={format}&locale={locale}', auth)
                            assert status == 409, body
                            assert b'PM_REPORT_SOURCE_CHANGED' in body
                    continue
                with frames.route('pipeline'):
                    schema.validate_v1(closure)
                    sources._verify(closure)
                    fixed = sources.project(closure)
                    sources.canonical(fixed)
                with frames.route('load_fixed'):
                    assert sources.load_fixed(ops, READER, wid, rid) == fixed
                for format in reports.FORMATS:
                    for locale in ('en', 'zh-TW'):
                        params = fields | {'format': format, 'locale': locale}
                        with frames.route('render_fixed'):
                            expected = reports.render_fixed(fixed, format=format, locale=locale)
                        with frames.route('get_report'):
                            assert reports.get_report(ops, READER, **params) == expected
                        with frames.route('pm_reads.read'):
                            assert pm_reads.read(ops, READER, READ, params.copy()) == expected
                        with frames.route('TaskDaemon.call_api'):
                            assert await d.call_api(READ, params.copy(), READER) == expected
                        with frames.route('HTTP GET'):
                            status, body = await raw_http(port, path + f'?format={format}&locale={locale}', auth)
                            assert status == 200, body
                            assert json.loads(body) == expected
                        with frames.route('RPC POST'):
                            status, body = await http(port, 'POST', '/rpc', body={'method': READ, 'params': params}, tok=auth)
                            assert status == 200, body
                            assert body['result'] == expected
                        with frames.route('MCP tool via RPC'):
                            assert unpack(await server.call_tool(READ, params)) == expected
        print('DEPTH_CASE', source, kind, '3 depths, 18 HTTP outputs')
        frames.check()
    finally:
        await fleet.close()
