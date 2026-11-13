"""Consumer-driven inventory of exercised accesses; wrappers exist only in tests.

Normal subscription/get/contains/iteration/items/values/copy/deepcopy are tracked.
keys(), dict(value), and mapping unpacking fail closed: plain dict copies cannot
retain tracking of later absent-key reads. Explicit dict base-class calls (such as
dict.get(value, key)) and C-extension accesses bypass this dynamic guard; they are
not claimed as covered. The existing snapshot envelope's two unpack expressions
are an explicit exception: its complete source is pinned, its source reads are recorded,
and its returned root/coverage copies regain their wrappers before other consumers
can read them. This is not a static proof for arbitrary consumer code.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import sys
from contextlib import contextmanager

import pytest

from bat_agent_connector import pm_roadmap, pm_snapshots
from bat_agent_connector import pm_roadmap_report_schema as schema
from bat_agent_connector import pm_roadmap_report_sources as sources
from bat_agent_connector import pm_roadmap_reports as reports
from tests import test_pm_roadmap_report_maintenance as maintenance
from tests import test_pm_roadmap_report_quality as quality
from tests.test_pm_roadmap import OWNER, revision
from tests.test_pm_roadmap_report_sources import ops as simple_ops
from tests.test_pm_roadmap_report_sources import selected

world, pm, ready, fixed, reviewed = quality.world, quality.pm, quality.ready, quality.fixed, quality.reviewed
base_ops, release_ops, workload_ops = maintenance.base_ops, maintenance.release_ops, maintenance.workload_ops
github_ops, maintenance_ops = maintenance.github_ops, maintenance.ops
ops = simple_ops


# These accepted-baseline members are copied to JSON by blocks(), never interpreted.
ENVELOPE_CODE = None
ENVELOPE_PATHS = {('snapshot',), ('snapshot', 'coverage')}

SERIALIZED_ONLY = {('snapshot', 'baseline', 'status'), ('snapshot', 'baseline', 'plan_head_version')}
SERIALIZED_ONLY.update(('snapshot', 'evidence', 0, key) for key in (
    'captured_at', 'cost', 'dod', 'journal_cursor', 'sequence', 'server_id', 'source_id',
    'source_observed_at', 'workspace_id'))


class Access(dict):
    """Preserve dict behavior and original paths through projection deepcopy."""
    def __init__(self, value, spec, path, reads):
        super().__init__()
        self.spec, self.path, self.reads = spec, path, reads
        self.original_keys = set(value)
        for key, child in value.items():
            child_spec = spec.get('fields', {}).get(key, {})
            if key == 'facts' and 'facts' in spec.get('fields', {}):
                child_spec = spec['fields']['facts']
            dict.__setitem__(self, key, wrap(child, child_spec, (*path, key), reads))

    def record(self, key, *, serialized=False):
        path = self.path + (key,)
        generated = dict.__contains__(self, key) and key not in self.original_keys
        self.reads.add((path, generated or key in self.spec.get('fields', {}) or serialized and path in SERIALIZED_ONLY))

    def __getitem__(self, key):
        if sys._getframe(1).f_code is ENVELOPE_CODE and self.path in ENVELOPE_PATHS:
            return dict.__getitem__(self, key)
        self.record(key)
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        self.record(key)
        return dict.get(self, key, default)

    def __contains__(self, key):
        self.record(key)
        return dict.__contains__(self, key)

    def __iter__(self):
        # Also disables CPython's dict-subclass fast copy that bypasses keys().
        for key in dict.__iter__(self):
            self.record(key)
            yield key

    def keys(self):
        if sys._getframe(1).f_code is ENVELOPE_CODE and self.path in ENVELOPE_PATHS:
            self.reads.add((self.path + ('<serialized-envelope-copy>',), True))
            return dict.keys(self)
        raise AssertionError(f'untracked mapping copy/keys at {self.path}')

    def values(self):
        for key, value in dict.items(self):
            self.record(key)
            yield value

    def copy(self):
        result = dict.__new__(Access)
        dict.__init__(result)
        result.spec, result.path, result.reads = self.spec, self.path, self.reads
        result.original_keys = self.original_keys
        for key, value in dict.items(self):
            self.record(key)
            dict.__setitem__(result, key, value)
        return result

    def __copy__(self):
        return self.copy()

    def items(self):
        # JSON encoding consumes arbitrary opaque members without interpreting them.
        frame = sys._getframe(1)
        serializing = frame.f_globals.get('__name__') in ('json.encoder', 'copy')
        for key, value in dict.items(self):
            if not serializing:
                self.record(key, serialized=True)
            yield key, value

    def __deepcopy__(self, memo):
        result = dict.__new__(Access)
        dict.__init__(result)
        result.spec, result.path, result.reads = self.spec, self.path, self.reads
        result.original_keys = self.original_keys
        memo[id(self)] = result
        for key, value in dict.items(self):
            dict.__setitem__(result, key, copy.deepcopy(value, memo))
        return result


def wrap(value, spec, path, reads):
    if isinstance(value, dict):
        if 'source_kind' in value and 'document' in value:
            spec = {**schema.EVIDENCE, 'fields': {**schema.EVIDENCE['fields'], 'document': schema.obj(
                facts=schema.FACT_VARIANTS.get(value['source_kind'], schema.FACTS))}}
        return Access(value, spec, path, reads)
    if isinstance(value, list):
        return [wrap(child, spec.get('item', {}), (*path, 0), reads) for child in value]
    return value



@contextmanager
def tracked_snapshot_envelope():
    """Execute the real, audited envelope builder and restore copy provenance.

    Any change to this small copy-only builder requires explicit re-audit. No
    arbitrary consumer copy is permitted and no production function is skipped.
    """
    global ENVELOPE_CODE
    original = pm_snapshots._response
    assert hashlib.sha256(inspect.getsource(original).encode()).hexdigest() == '9e2fb28110e08086c574ebb42a8e8091d2d6d7a27416629d7a23fd61d9e516e1', 'Re-audit the snapshot envelope copies'
    previous = ENVELOPE_CODE
    ENVELOPE_CODE = original.__code__
    def derived(parent, values):
        value = dict.__new__(Access)
        dict.__init__(value)
        value.spec, value.path, value.reads = parent.spec, parent.path, parent.reads
        value.original_keys = parent.original_keys
        dict.update(value, values)
        return value
    def response(document, *args, **kwargs):
        # These are the only original fields read by the pinned builder outside
        # _brief(document), whose reads remain dynamically instrumented.
        document.record('workspace_id')
        document.record('coverage')
        result = original(document, *args, **kwargs)
        out = result['snapshot']
        out['coverage'] = derived(dict.__getitem__(document, 'coverage'), out['coverage'])
        result['snapshot'] = derived(document, out)
        return result
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(pm_snapshots, '_response', response)
            yield
    finally:
        ENVELOPE_CODE = previous

def check(closure, label):
    sources._verify(closure)
    closure = copy.deepcopy(closure)
    reads = set()
    for name, row in zip(('observation', 'baseline', 'forecast', 'snapshot'), closure[:4], strict=True):
        if row:
            row['document'] = wrap(row['document'], schema.V1[name], (name,), reads)
    for row in closure[-2].values():
        row['document'] = wrap(row['document'], schema.PLAN, ('plan',), reads)
    for row in closure[-1].values():
        row['result'] = wrap(row['result'], schema.ADOPTION_RESULT, ('adoption',), reads)
    with tracked_snapshot_envelope():
        projected = sources.project(closure)
    for format in reports.FORMATS:
        for locale in ('en', 'zh-TW'):
            try:
                reports.render_fixed(projected, format=format, locale=locale)
            except Exception as error:
                # Rich quality fixtures may exceed SVG's existing panel bound.
                from bat_agent_connector.operations import OperationError
                assert isinstance(error, OperationError) and error.status == 413, error
    missing = sorted({'.'.join(map(str, path)) for path, declared in reads if not declared})
    if missing:
        print(label, "MISSING", missing)
    assert not missing, missing
    print(label, len(reads), 'declared consumer accesses')
    return reads


async def test_ordinary_legacy_forecast_and_empty_baseline_consumers(ops):
    chosen = await selected(ops)
    wid, rid = chosen[0]['workspace_id'], chosen[3]['revision_id']
    closure = sources._Closure(ops, OWNER, wid).collect(rid)
    check(closure, 'ordinary')
    legacy = copy.deepcopy(closure)
    for milestone in legacy[0]['document']['milestones']:
        milestone.pop('quality_context', None)
    legacy[0]['document_digest'] = sources.digest(legacy[0]['document'])
    check(legacy, 'legacy')
    await revision(ops, wid, 'forecast', updates=[{'milestone_id': 'release', 'forecast_date': '2027-01-01'}])
    observed = await revision(ops, wid, 'observation', snapshot_id=chosen[2]['snapshot_id'])
    check(sources._Closure(ops, OWNER, wid).collect(observed['revision_id']), 'forecast')
    await revision(ops, wid, milestones=[])
    obs = await revision(ops, wid, 'observation', snapshot_id=chosen[2]['snapshot_id'])
    check(sources._Closure(ops, OWNER, wid).collect(obs['revision_id']), 'empty baseline adoption')


@pytest.mark.parametrize('verdict', ['accepted', 'rejected'])
async def test_quality_matrix_consumers(reviewed, verdict):
    if verdict == 'accepted':
        _, _, observed = await quality.shared.assessed(reviewed)
    else:
        shared = quality.shared.shared
        row = await shared.base.new_review(reviewed)
        op = await shared.base.execute(reviewed, shared.base.assessment(reviewed, row, verdict='rejected'), actor=shared.base.REVIEWER)
        reference = {key: op['result'][key] for key in ('review_id', 'version', 'document_digest')} | {
            'operation_id': op['operation_id'], 'candidate_commit': reviewed['candidate']}
        await shared.capture(reviewed, reference)
        observed = await quality.shared.observe(reviewed, await shared.snapshot(reviewed))
    closure = sources._Closure(reviewed['d'].ops, quality.OWNER, reviewed['workspace_id']).collect(observed['revision_id'])
    stack = [(closure[3]['document'], 1)]
    maximum = 0
    while stack:
        value, depth = stack.pop()
        if isinstance(value, (dict, list)):
            maximum = max(maximum, depth)
            stack.extend((child, depth + 1) for child in (value.values() if isinstance(value, dict) else value))
    assert maximum == 11 and maximum <= schema.MAX_CONTAINER_DEPTH
    check(closure, 'quality matrix ' + verdict)


async def test_maintenance_consumers(maintenance_ops, monkeypatch):
    ops = maintenance_ops
    data = await maintenance.shared.gh.prepared(ops, monkeypatch)
    _, view = await maintenance.shared.prepare(ops, data)
    pm_roadmap.install(ops)
    wid = data['workspace_id']
    await revision(ops, wid, milestones=[])
    observed = await revision(ops, wid, 'observation', snapshot_id=view['snapshot']['snapshot_id'])
    check(sources._Closure(ops, OWNER, wid).collect(observed['revision_id']), 'maintenance')


def test_guard_detects_an_undeclared_consumer_access():
    reads = set()
    value = wrap({'new_field': 1}, schema.obj(), ('source',), reads)
    assert value['new_field'] == 1
    assert (('source', 'new_field'), False) in reads
