"""Negative controls adapted from the independent fourth review's guard probes."""
import copy
import sys
from pathlib import Path

import pytest

from bat_agent_connector import pm_roadmap_report_schema as schema
from bat_agent_connector import pm_roadmap_report_sources as sources
from bat_agent_connector import pm_roadmap_reports as reports
from tests import test_pm_roadmap_report_consumers as consumer
from tests import test_pm_roadmap_report_review_r3 as guard
from tests.test_pm_roadmap_report_api import daemon, served  # noqa: F401
from tests.test_pm_roadmap_report_sources import READER, selected
from tests.test_pm_roadmap_report_sources import ops as ops_fixture

ops = ops_fixture


@pytest.mark.parametrize('operation', [
    lambda value: value['new'], lambda value: value.get('new', 0),
    lambda value: 'new' in value, lambda value: list(value),
    lambda value: list(value.keys()), lambda value: list(value.values()),
    lambda value: list(value.items()), lambda value: {**value},
    lambda value: dict(value), lambda value: value.copy(),
    lambda value: copy.copy(value), lambda value: copy.deepcopy(value).get('new'),
], ids=['index', 'get', 'contains', 'iter', 'keys', 'values', 'items',
        'unpack', 'dict_copy', 'method_copy', 'shallow_copy', 'deepcopy'])
def test_wrapper_blindspots(operation):
    reads = set()
    value = consumer.wrap({'new': 1}, schema.obj(), ('source',), reads)
    try:
        operation(value)
    except AssertionError as error:
        assert 'untracked mapping copy/keys' in str(error)
    else:
        assert (('source', 'new'), False) in reads


async def test_shallow_copy_hides_new_renderer_read(ops, monkeypatch):
    values = await selected(ops)
    closure = sources._Closure(ops, READER, values[0]['workspace_id']).collect(values[3]['revision_id'])
    closure[1]['document']['undeclared_new_field'] = 'test-only new renderer dependency'
    closure[1]['document_digest'] = sources.digest(closure[1]['document'])
    original = reports.blocks
    def changed(fixed, locale):
        result = original(fixed, locale)
        result.append(('body', dict(fixed['baseline']['document']).get('undeclared_new_field', '')))
        return result
    monkeypatch.setattr(reports, 'blocks', changed)
    with pytest.raises(AssertionError, match='untracked mapping copy/keys'):
        consumer.check(closure, 'new consumer hidden behind dict copy')


@pytest.mark.parametrize('use', [
    'emit = self.add; emit("future_" + str(item["version"]), item["work_item_id"], None, {})',
    'consume(self.add)', 'emit = getattr(self, "add")',
    'emit = functools.partial(self.add, "future")',
], ids=['assignment', 'passing', 'getattr', 'partial'])
def test_dynamic_kind_through_alias_is_not_detected(monkeypatch, tmp_path, use):
    source = Path('src/bat_agent_connector/pm_evidence.py')
    text = source.read_text()
    needle = '    def collect(self, item):\n'
    assert needle in text
    modified = tmp_path / 'pm_evidence_alias.py'
    modified.write_text(text.replace(needle, needle + '        ' + use + '\n', 1))
    monkeypatch.setattr(guard, 'Path', lambda name: modified if name == str(source) else Path(name))
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        guard.test_producer_enumerations_do_not_drift()


async def test_companion_review_probe_clears_existing_profile(ops, served, monkeypatch):  # noqa: F811
    def sentinel(frame, event, arg):
        pass
    previous = sys.getprofile()
    try:
        sys.setprofile(sentinel)
        await guard.test_deep_json_real_http_and_all_downstream_stages(ops, served, monkeypatch, 'array')
        assert sys.getprofile() is sentinel
    finally:
        sys.setprofile(previous)


@pytest.mark.parametrize('text,maintenance', [
    ('emit = ref', True), ('consume(ref)', True), ('functools.partial(ref, "future")', True),
    ('getattr(module, "ref")', True), ('ref(kind, "id", "digest")', True),
    ('self.__getattribute__("add")', False), ('module.__dict__["ref"]', True),
    ('builtins.getattr(module, "ref")', True),
    ('alias = collector', False), ('alias = self', False), ('alias = _Collector', False),
    ('_Collector.add(self, "kind")', False), ('self.add(*args)', False),
    ('collector.add("known", **kwargs)', False),
])
def test_unclassifiable_emitter_forms_are_rejected(text, maintenance):
    import ast
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        for call in guard.emitter_calls(ast.parse(text), maintenance=maintenance):
            guard.literals(call.args[0])


def test_shallow_copy_keeps_tracking_absent_keys():
    reads = set()
    value = consumer.wrap({}, schema.obj(), ('source',), reads)
    for clone in (value.copy(), copy.copy(value)):
        assert isinstance(clone, consumer.Access)
        assert clone.get('new_missing') is None
    assert (('source', 'new_missing'), False) in reads


async def test_snapshot_envelope_copies_remain_tracked_and_pinned(ops, monkeypatch):
    from bat_agent_connector import pm_snapshots
    values = await selected(ops)
    closure = sources._Closure(ops, READER, values[0]['workspace_id']).collect(values[3]['revision_id'])
    row = closure[3]
    original = pm_snapshots._response
    expected = original(row['document'], row['sequence'], row['snapshot_digest'])
    reads = set()
    document = consumer.wrap(row['document'], schema.V1['snapshot'], ('snapshot',), reads)
    with consumer.tracked_snapshot_envelope():
        result = pm_snapshots._response(document, row['sequence'], row['snapshot_digest'])
    assert sources.canonical(result) == sources.canonical(expected)
    for value in (result['snapshot'], result['snapshot']['coverage']):
        assert isinstance(value, consumer.Access)
        value.get('future_missing')
        assert (value.path + ('future_missing',), False) in reads
    def changed(*args, **kwargs):
        return original(*args, **kwargs)
    monkeypatch.setattr(pm_snapshots, '_response', changed)
    with pytest.raises(AssertionError, match='Re-audit'):
        with consumer.tracked_snapshot_envelope():
            pytest.fail('A changed copy builder must be re-audited before execution')
