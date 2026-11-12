"""Systematic schema mutations; raw refusal equality and post-access shape safety."""
from __future__ import annotations

import copy
import json
import sqlite3
from collections import Counter

import pytest

from bat_agent_connector import controller_authority, pm_access, pm_plans
from bat_agent_connector import pm_roadmap_report_schema as schema
from bat_agent_connector import pm_roadmap_report_sources as sources
from bat_agent_connector import pm_roadmap_reports as reports
from bat_agent_connector.operations import OperationError
from tests.test_api_v1 import token
from tests.test_pm_evidence import capture, link, task
from tests.test_pm_roadmap import OWNER, READER, mapped, revision
from tests.test_pm_roadmap_report_api import daemon, served  # noqa: F401
from tests.test_pm_roadmap_report_review import document, fault, raw_http
from tests.test_pm_roadmap_report_sources import ops as ops_fixture
from tests.test_pm_snapshots import adopt, adoption, draft, snapshot

ops = ops_fixture
MUTATIONS = (None, [], {}, 'wrong', 7, 10**100, 0.5, False, '', '漢' * 171)


def example(spec):
    kind = spec['kind']
    if kind == 'object':
        return {key: example(child) for key, child in spec['fields'].items()}
    if kind == 'array':
        return [example(spec['item'])]
    return {'json': None, 'text': 'text', 'ref': 'reference', 'bool': True,
            'number': 123, 'int': 1, 'enum': spec.get('values', [''])[0]}[kind]


def inventory(spec, path=()):
    yield path, spec
    if spec['kind'] == 'object':
        for key, child in spec['fields'].items():
            yield from inventory(child, (*path, key))
    elif spec['kind'] == 'array':
        yield from inventory(spec['item'], (*path, 0))


def changed(value, spec, path, replacement):
    if not path:
        return copy.deepcopy(replacement)
    value = copy.deepcopy(value)
    if not schema.valid(spec, value):
        value = example(spec)
    part, *tail = path
    child = spec['item'] if spec['kind'] == 'array' else spec['fields'][part]
    if isinstance(part, int):
        if not value:
            value.append(example(child))
    elif part not in value:
        value[part] = example(child)
    value[part] = changed(value[part], child, tail, replacement)
    return value


def cases(spec):
    for path, child in inventory(spec):
        for index, value in enumerate(MUTATIONS):
            yield path, child, index, value


def counts():
    result = {}
    for phase, schemas in [('authority', schema.AUTHORITY), ('post', schema.V1)]:
        for name, spec in schemas.items():
            tally = Counter('compatible' if schema.valid(child, value) else 'invalid'
                            for _, child, _, value in cases(spec))
            result[phase + ':' + name] = dict(tally)
    return result


@pytest.mark.parametrize('phase,schemas', [('authority', schema.AUTHORITY), ('post', schema.V1)])
def test_complete_field_and_element_inventory(phase, schemas):
    for name, spec in schemas.items():
        valid = example(spec)
        assert schema.valid(spec, valid), name
        for path, child, index, value in cases(spec):
            mutated = changed(valid, spec, path, value)
            if schema.valid(child, value):
                # A value of the correct type (including legitimate null) is not corruption.
                schema.require(spec, mutated, authority=phase == 'authority')
            else:
                with pytest.raises(OperationError) as error:
                    schema.require(spec, mutated, authority=phase == 'authority')
                assert error.value.status == (404 if phase == 'authority' else 409), (name, path, index)
    print(json.dumps({key: value for key, value in counts().items() if key.startswith(phase + ':')}, sort_keys=True))


async def world(ops):
    target, old, work, *_ = await mapped(ops)
    wid = target['workspace_id']
    with controller_authority.use(controller_authority.binding(ops.db, OWNER), ops.journal):
        source = task(ops)
    await link(ops, work, 'task', source['task_id'])
    await capture(ops, work)
    new = await draft(ops, target, title='Accepted later')
    adopt(adoption(ops, target, new['revision_id'], previous=old['revision_id']))
    candidate = await draft(ops, target, title='Candidate')
    await revision(ops, wid, 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': None}])
    snap = await snapshot(ops, wid)
    observation = await revision(ops, wid, 'observation', snapshot_id=snap['snapshot_id'])
    closure = sources._Closure(ops, READER, wid).collect(observation['revision_id'])
    sources._verify(closure)
    sources.project(closure)
    return wid, observation['revision_id'], candidate, closure


def reseal(closure, *, preserve_snapshot_digest=False):
    observation, baseline, forecast, snapshot, *_ = closure
    snapshot['snapshot_digest'] = sources.digest(snapshot['document'])
    if not preserve_snapshot_digest:
        observation['document']['snapshot_digest'] = snapshot['snapshot_digest']
    for row in (observation, baseline, forecast):
        row['document_digest'] = sources.digest(row['document'])


@pytest.mark.parametrize('name', list(schema.V1))
async def test_generated_post_authorization_shapes(ops, name):
    _, _, _, original = await world(ops)
    spec = schema.V1[name]
    tested = 0
    for path, child, _, value in cases(spec):
        closure = copy.deepcopy(original)
        if name in ('observation', 'baseline', 'forecast', 'snapshot'):
            row = closure[('observation', 'baseline', 'forecast', 'snapshot').index(name)]
            row['document'] = changed(row['document'], spec, path, value)
        elif name == 'plan':
            row = next(iter(closure[-2].values()))
            row['document'] = changed(row['document'], spec, path, value)
        elif name == 'adoption':
            row = next(iter(closure[-1].values()))
            row['result'] = changed(row['result'], spec, path, value)
        else:
            entry = closure[3]['document']['evidence'][0]
            entry['source_kind'] = name.split('.', 1)[1]
            entry['document']['facts'] = changed(example(spec), spec, path, value)
        # Root or identity changes can fail the earlier identity proof. Exercise
        # the structural gate independently too, so they cannot mask a schema gap.
        if not schema.valid(child, value):
            with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
                schema.validate_v1(closure)
            tested += 1
        # Root replacements are already tested at the gate; identity proof needs
        # mappings to compute the general snapshot digest/observation anchor.
        if not all(isinstance(row['document'], dict) for row in closure[:4]):
            continue
        reseal(closure, preserve_snapshot_digest=name == 'observation' and path == ('snapshot_digest',))
        try:
            sources._verify(closure)
            fixed = sources.project(closure)
            for format in reports.FORMATS:
                reports.render_fixed(fixed, format=format)
        except OperationError as error:
            assert error.status == 409 or (error.status == 413 and schema.valid(child, value)), (name, path, value, error)
        else:
            assert schema.valid(child, value), (name, path, value)
    print('post', name, tested, 'invalid shapes; every compatible mutation also traversed proofs/projection/render or refused 409')


@pytest.mark.parametrize('name', list(schema.AUTHORITY))
async def test_generated_authority_raw_refusals(ops, served, monkeypatch, name):  # noqa: F811
    d, port = served
    wid, rid, candidate, closure = await world(ops)
    monkeypatch.setattr(d, 'ops', ops)
    path = f'/api/v1/pm/workspaces/{wid}/roadmap/revisions/{rid}/report'
    auth = token(d, OWNER.actor, 'observe')
    assert (await raw_http(port, path, auth))[0] == 200
    gate = pm_access.require_operation_sources
    later = pm_plans._revision(ops.db, wid, candidate['revision_id'])['operation_id']
    def hidden(db, principal, ids):
        if later in ids:
            raise OperationError('PRIVATE', 'hidden later source', 404)
        return gate(db, principal, ids)
    with monkeypatch.context() as patch:
        patch.setattr(pm_access, 'require_operation_sources', hidden)
        expected = await raw_http(port, path, auth)
    assert expected[0] == 404
    spec = schema.AUTHORITY[name]
    total = 0
    for field, child, _, value in cases(spec):
        wrong_identity = child['kind'] == 'ref' and value == 'wrong'
        if name == 'evidence_facts':
            original_fact = next(e['document']['facts'] for e in closure[3]['document']['evidence']
                                 if e['document']['facts'].get('task_id'))
            altered = changed(original_fact, spec, field, value)
            wrong_identity |= isinstance(altered, dict) and any(
                original_fact.get(key) != altered.get(key) for key in schema.FACT_AUTH)
        if schema.valid(child, value) and not wrong_identity:
            continue  # Legitimately nullable/compatible values are covered above.
        for condition in ('ordinary', 'large', 'hidden_later'):
            with fault(ops), monkeypatch.context() as patch:
                if condition == 'hidden_later':
                    patch.setattr(pm_access, 'require_operation_sources', hidden)
                snapshot = copy.deepcopy(closure[3]['document'])
                if condition == 'large':
                    snapshot['padding'] = 'x' * (sources.MAX_SOURCE_BYTES + 1)
                if name in ('observation', 'baseline', 'forecast'):
                    row = closure[('observation', 'baseline', 'forecast').index(name)]
                    document(ops, 'pm_roadmap_revisions', 'revision_id', row['revision_id'], changed(row['document'], spec, field, value))
                elif name == 'snapshot':
                    snapshot = changed(snapshot, spec, field, value)
                    if condition == 'large' and isinstance(snapshot, dict):
                        snapshot['padding'] = 'x' * (sources.MAX_SOURCE_BYTES + 1)
                elif name == 'plan':
                    row = next(iter(closure[-2].values()))
                    document(ops, 'pm_plan_revisions', 'revision_id', row['revision_id'], changed(row['document'], spec, field, value))
                elif name == 'adoption':
                    row = next(iter(closure[-1].values()))
                    ops.db.execute('DROP TRIGGER pm_plan_adoptions_update_immutable')
                    ops.db.execute('UPDATE pm_plan_adoptions SET result=? WHERE decision_id=?',
                                   (json.dumps(changed(row['result'], spec, field, value)), row['decision_id']))
                elif name == 'maintenance_reference':
                    snapshot['maintenance'] = {'access_refs': [changed(example(spec), spec, field, value)]}
                elif name == 'evidence_facts':
                    entry = next(e for e in snapshot['evidence'] if e['document']['facts'].get('task_id'))
                    fact = changed(entry['document']['facts'], spec, field, value)
                    document(ops, 'pm_evidence', 'evidence_id', entry['evidence_id'], {**entry['document'], 'facts': fact})
                else:  # SQL row operation IDs: inject before this exact existing reader.
                    collect = sources._Closure.collect
                    def bad_operation(self, identity):
                        self.operations([value])
                        return collect(self, identity)
                    patch.setattr(sources._Closure, 'collect', bad_operation)
                document(ops, 'pm_document_snapshots', 'snapshot_id', closure[3]['snapshot_id'], snapshot)
                assert await raw_http(port, path, auth) == expected, (name, field, value, condition)
                total += 1
    print('authority', name, total, 'raw HTTP identical-404 cases across three contexts')


async def test_exact_collection_backstop_and_later_error_boundary(ops, monkeypatch):
    wid, rid, _, _ = await world(ops)
    concealed = (OperationError, KeyError, TypeError, ValueError, RecursionError, AttributeError, IndexError,
                 sqlite3.ProgrammingError, sqlite3.InterfaceError, OverflowError)
    for error in (*concealed, sqlite3.OperationalError, sqlite3.DatabaseError):
        with monkeypatch.context() as patch:
            def fail(*args, **kw):
                if error is OperationError:
                    raise error('PRIVATE', 'private', 413)
                raise error('sentinel')
            patch.setattr(sources._Closure, 'collect', fail)
            if error in concealed:
                with pytest.raises(OperationError, match='PM_REPORT_SOURCE_UNAVAILABLE'):
                    sources.load_fixed(ops, READER, wid, rid)
            else:
                with pytest.raises(error, match='sentinel'):
                    sources.load_fixed(ops, READER, wid, rid)
    for module, stage in ((sources, '_verify'), (sources, 'project'), (reports, 'render_fixed')):
        for error in (TypeError, sqlite3.ProgrammingError, sqlite3.InterfaceError, OverflowError):
            with monkeypatch.context() as patch:
                def fail(*args, **kw):
                    raise error('after authorization')
                patch.setattr(module, stage, fail)
                with pytest.raises(error, match='after authorization'):
                    reports.get_report(ops, READER, wid, rid)


@pytest.mark.parametrize('schemas', [schema.AUTHORITY, schema.V1], ids=['authority', 'post'])
def test_generated_missing_fields(schemas):
    for spec in schemas.values():
        for _, parent in inventory(spec):
            if parent['kind'] != 'object':
                continue
            for key, child in parent['fields'].items():
                value = example(parent)
                del value[key]
                assert schema.valid(parent, value) == child.get('optional', False)


async def test_quality_criterion_map_uses_canonical_identity(ops):
    _, _, _, closure = await world(ops)
    entry = closure[3]['document']['evidence'][0]
    entry['source_kind'] = 'quality_assessment'
    facts = example(schema.QUALITY)
    matrix = facts['criterion_assessments']
    matrix['criteria'][0]['criterion'] = {'kind': 'dod', 'extension': 1}
    matrix['assessment']['criteria'][0].update(
        status='rejected', criterion={'kind': 'dod', 'extension': True})
    entry['document']['facts'] = facts
    # Python equates 1 and True; the shared projection indexes canonical JSON.
    with pytest.raises(OperationError, match='PM_REPORT_SOURCE_CHANGED'):
        schema.validate_v1(closure)
