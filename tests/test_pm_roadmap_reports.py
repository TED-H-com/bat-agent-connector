"""Pinned six-format bytes, complete Unicode pagination and read-only reports."""
from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bat_agent_connector import pm_brief_exports as brief
from bat_agent_connector import pm_roadmap_report_layout as layout
from bat_agent_connector import pm_roadmap_report_sources as sources
from bat_agent_connector import pm_roadmap_reports as reports
from bat_agent_connector.operations import OperationError
from tests.test_pm_roadmap_report_sources import READER, read, selected
from tests.test_pm_roadmap_report_sources import ops as ops_fixture

ops = ops_fixture
FIXTURES = Path(__file__).parent / 'fixtures' / 'roadmap-reports'


def fixture_projection():
    # Synthetic immutable records, intentionally independent of clocks/random IDs.
    rid, sid, pid = 'pmro_' + '1' * 32, 'pmsnap_' + '2' * 32, 'pmrev_' + '3' * 32
    title = 'Login 登入 e\u0301 👩‍💻 🇹🇼 🫨 \U0001e4ec'
    plan = {'title': title, 'requirements': [{'id': 'login.exact', 'acceptance_input_ids': ['criterion']}],
        'selected_inputs': [{'input_id': 'criterion', 'text': 'Reject <script> and ``` delimiters; https://example.invalid stays text', 'status': 'user_confirmed'}],
        'sections': {'scope': ['Login only; other scope 未知']}}
    milestone = {'milestone_id': 'ready', 'title': title, 'plan_revision_id': pid, 'plan_document_digest': '4' * 64,
        'scope_revision': 'pmscope_' + '5' * 32, 'requirement_ids': ['login.exact'], 'work_binding_ids': [],
        'baseline_date': '2026-11-12', 'original_baseline_date': None, 'depends_on': []}
    observation = {'revision_id': rid, 'document': {'milestones': [{'milestone_id': 'ready', 'evidence_ids': [],
        'actual': {'completion': 'unassessed', 'dod': 'unassessed', 'actual_finish_date': None, 'facts': []},
        'coverage': {'work_mapping': 'unmapped'}}]}}
    snapshot = {'snapshot_id': sid, 'workspace_id': 'ws_' + '6' * 32, 'workspace_name': 'Synthetic report',
        'as_of': 1794484800, 'journal_cursor': 42, 'baseline': {'document': plan, 'status': 'accepted',
        'document_revision': pid, 'scope_revision': milestone['scope_revision']}, 'evidence': [], 'work': [],
        'coverage': {'work_truncated': False, 'freshness': 'per_source_saved_times'}, 'candidate_document': None}
    value = {'version': 1, 'template_version': 1, 'workspace_id': snapshot['workspace_id'],
        'snapshot_id': sid, 'observation_revision_id': rid, 'snapshot_digest': sources.digest(snapshot),
        'baseline_revision_id': 'pmrb_' + 'a' * 32, 'forecast_revision_id': None,
        'snapshot_view_digest': '7' * 64, 'baseline_digest': '8' * 64, 'forecast_digest': None,
        'observation_digest': sources.digest(observation), 'source_set_digest': '9' * 64,
        'snapshot': snapshot, 'baseline': {'revision_id': 'pmrb_' + 'a' * 32,
        'document': {'title': 'Fixed roadmap', 'rationale': 'No inferred dates', 'milestones': [milestone], 'changes': {}}},
        'forecast': None, 'observation': observation, 'historical_plans': [], 'source_manifest': [], 'quality': None,
        'coverage': {'projection': 'roadmap_report', 'content_truncated': False, 'snapshot': snapshot['coverage'],
                     'visibility': {'filtered': False, 'source_policy': 'whole_original_closure'}}}
    value['view_digest'] = sources.digest(value)
    return value


@pytest.mark.parametrize('format', reports.FORMATS)
@pytest.mark.parametrize('locale', ['en', 'zh-TW'])
def test_golden_bytes(format, locale):
    result = reports.render_fixed(fixture_projection(), format=format, locale=locale)
    path = FIXTURES / (locale + '.' + {'markdown': 'md', 'html': 'html', 'svg': 'svg'}[format])
    if os.environ.get('BATC_UPDATE_ROADMAP_REPORT_GOLDEN') == '1':
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result['content'].encode())
    raw = path.read_bytes()
    assert result['content'].encode() == raw
    assert result['byte_length'] == len(raw)
    assert result['output_digest'] == hashlib.sha256(raw).hexdigest()
    assert '🫨' in result['content']  # Unicode 15: unassigned in Python 3.10's UCD.
    assert '\U0001e4ec' in result['content']  # Unicode 15 combining mark.
    assert 'Actual finish dates are unknown' in result['content'] if locale == 'en' else '目前尚無實際日期紀錄' in result['content']


@pytest.mark.parametrize('seed,zone', [('1', 'UTC'), ('71', 'Pacific/Honolulu')])
def test_golden_bytes_ignore_process_hash_seed_timezone_and_clock(seed, zone):
    # subprocess uses the current suite interpreter, also exercised under 3.10.
    script = """
import datetime, hashlib, json, os
from types import SimpleNamespace
from tests.test_pm_roadmap_reports import fixture_projection
from bat_agent_connector import pm_roadmap_reports as r
epoch = -999999999 if os.environ['PYTHONHASHSEED'] == '1' else 1999999999
class Clock(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(epoch, tz)
    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(epoch, datetime.timezone.utc).replace(tzinfo=None)
# Replace only the renderer/helper's module-owned references, never stdlib classes.
r.time = SimpleNamespace(time=lambda: epoch)
r.brief.dt = SimpleNamespace(datetime=Clock, timezone=datetime.timezone)
print(json.dumps([hashlib.sha256(r.render_fixed(fixture_projection(), format=f, locale=l)['content'].encode()).hexdigest()
                 for l in ('en','zh-TW') for f in ('markdown','html','svg')]))
"""
    env = dict(os.environ, PYTHONHASHSEED=seed, TZ=zone)
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, check=True)
    expected = [hashlib.sha256((FIXTURES / f'{locale}.{ext}').read_bytes()).hexdigest()
                for locale in ('en', 'zh-TW') for ext in ('md', 'html', 'svg')]
    assert json.loads(result.stdout) == expected


def test_pinned_unicode_generator_and_runtime_properties():
    if sys.version_info[:2] == (3, 13):
        from scripts.generate_roadmap_unicode import DESTINATION, generate
        assert DESTINATION.read_text() == generate()
    assert layout.ucd.UNICODE_VERSION == '15.1.0'
    assert layout.contains(ord('🫨'), layout.ucd.WIDE)
    assert layout.contains(0x1E4EC, layout.ucd.MARK)
    assert list(layout.clusters('e\U0001e4ec')) == ['e\U0001e4ec']


def test_ascii_fast_path_table_constant():
    assert layout._ASCII_SINGLE_CLUSTER is True
    assert set(layout._ASCII_ESCAPE) == {code for code in range(128) if layout.contains(code, layout._ESCAPE)}


def test_ascii_fast_path_matches_reference():
    contains, _ESCAPE, MAX_CLUSTER, clusters = layout.contains, layout._ESCAPE, layout.MAX_CLUSTER, layout.clusters

    def literal(value):
        text = str(value).replace('\r\n', '\n').replace('\r', '\n').expandtabs(4)
        return ''.join(f'\\u{ord(c):04x}' if contains(ord(c), _ESCAPE) else c for c in text)

    def normalize(value):
        # Escaping pathological clusters is part of all three formats, not SVG-only.
        return ''.join(cluster if len(cluster) <= MAX_CLUSTER else ''.join(f'\\u{ord(c):04x}' for c in cluster)
                       for cluster in clusters(literal(value)))

    alphabet = (
        *(chr(c) for c in range(32)),
        '\r', '\n', '\t', '\x7f', '\r\n',
        *(chr(c) for c in range(0x20, 0x7F)),
        *(chr(c) for c in range(0x80, 0xA0)),
        '漢', '字',
        '\u0300', '\u0301', '\u20d0', '\U0001e4ec',
        '\ufe0e', '\ufe0f', '\U000e0100',
        '\U0001f3fb', '\U0001f3ff',
        '\u200d',
        '\U0001f1f9', '\U0001f1fc',
        '\u061c', '\u200e', '\u200f', '\u202a', '\u202e', '\u2066', '\u2069',
        '\ufdd0', '\ufdef', '\ufffe', '\uffff', '\U0001fffe', '\U0010ffff',
        '\ud800',
    )
    ascii_alphabet = tuple(token for token in alphabet if token.isascii())
    rng = random.Random(20261114)
    samples = []
    for index in range(2400):
        pool = alphabet if index < 2000 else ascii_alphabet
        target = rng.randint(0, 200)
        pieces, size = [], 0
        while size < target:
            token = rng.choice(pool)
            if size + len(token) > target:
                token = token[:target - size]
            pieces.append(token)
            size += len(token)
        samples.append(''.join(pieces))
    for item in [*samples, '', '\n', 'a' + '\u0301' * 33, 12345]:
        assert (layout.literal(item), layout.normalize(item)) == (literal(item), normalize(item))


def reconstruct(content, original):
    root = ET.fromstring(content)
    received = {i: '' for i in range(len(original))}
    for node in root.iter():
        if 'data-block' not in node.attrib:
            continue
        index = int(node.attrib['data-block'])
        assert int(node.attrib['data-start']) == len(received[index])
        received[index] += node.text or ''
        assert int(node.attrib['data-end']) == len(received[index])
    assert [received[i] for i in received] == [value for _, value in original]
    return root


def test_long_unicode_is_complete_across_panels_and_controls_remain_literal():
    source = ('漢字 é 👩‍💻 🇹🇼 🫨\U0001e4ec\t\r\n\n' * 100) + 'id' * 500 + '\x00\u202e<script>&end'
    blocks = [('title', 'Long source'), ('body', layout.normalize(source))]
    content, pages = layout.svg(blocks, 'en', 'fixed', 'Page')
    assert pages > 2
    root = reconstruct(content, blocks)
    assert {'script', 'image', 'foreignObject'}.isdisjoint(node.tag.rsplit('}', 1)[-1] for node in root.iter())
    assert '\\u0000' in content and '\\u202e' in content and '&lt;script&gt;' in content
    assert '\u202e' not in content
    assert '…' not in content and 'href=' not in content
    extreme = layout.normalize('x' + '\u0301' * 200)
    assert extreme.startswith('\\u0078') and extreme.count('\\u0301') == 200
    assert ''.join(t for _, _, t in layout.wrap(extreme, 20)) == extreme


def test_exact_svg_32_and_33_panel_boundary():
    # Body adds four initial units; 49 lines fit on the first panel; 50 on each continuation with 23-unit steps.
    blocks = [('body', 'x' * (58 * (49 + 50 * 31)))]
    content, pages = layout.svg(blocks, 'en', 'fixed', 'Page')
    assert pages == 32
    reconstruct(content, blocks)
    with pytest.raises(OperationError) as error:
        layout.svg([('body', blocks[0][1] + 'x')], 'en', 'fixed', 'Page')
    assert (error.value.code, error.value.status) == ('PM_EXPORT_TOO_LARGE', 413)


@pytest.mark.parametrize('format', reports.FORMATS)
def test_precise_utf8_content_and_response_bounds(format, monkeypatch):
    fixed = fixture_projection()
    result = reports.render_fixed(fixed, format=format, locale='zh-TW')
    content_size = len(result['content'].encode())
    response_size = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode())
    monkeypatch.setattr(reports, 'MAX_CONTENT_BYTES', content_size)
    monkeypatch.setattr(reports, 'MAX_RESPONSE_BYTES', response_size)
    assert reports.render_fixed(fixed, format=format, locale='zh-TW') == result
    for field, size in [('MAX_CONTENT_BYTES', content_size), ('MAX_RESPONSE_BYTES', response_size)]:
        with monkeypatch.context() as bounds:
            bounds.setattr(reports, field, size - 1)
            with pytest.raises(OperationError, match='PM_EXPORT_TOO_LARGE'):
                reports.render_fixed(fixed, format=format, locale='zh-TW')


async def test_real_fixed_report_reads_all_formats_without_changing_brief(ops):
    chosen = await selected(ops)
    fixed = read(ops, chosen)
    wid, rid, sid = fixed['workspace_id'], fixed['observation_revision_id'], fixed['snapshot_id']
    for locale in ('en', 'zh-TW'):
        for format in reports.FORMATS:
            before = brief.render_brief(ops, READER, wid, sid, format=format, locale=locale)
            changes = ops.db.total_changes
            result = reports.get_report(ops, READER, wid, rid, format=format, locale=locale)
            assert result == reports.get_report(ops, READER, wid, rid, format=format, locale=locale)
            assert result == reports.render_fixed(fixed, format=format, locale=locale)
            assert brief.render_brief(ops, READER, wid, sid, format=format, locale=locale) == before
            assert ops.db.total_changes == changes


async def test_authorized_render_is_413_but_hidden_source_always_404(ops, monkeypatch):
    from bat_agent_connector import pm_access
    chosen = await selected(ops)
    wid, rid = chosen[0]['workspace_id'], chosen[3]['revision_id']
    monkeypatch.setattr(reports, 'MAX_CONTENT_BYTES', 1)
    with pytest.raises(OperationError) as authorized:
        reports.get_report(ops, READER, wid, rid)
    assert authorized.value.status == 413
    def hidden(*args):
        raise OperationError('HIDDEN', 'Not readable', 404)
    monkeypatch.setattr(pm_access, 'require_operation_sources', hidden)
    for version in (1, 999):
        with pytest.raises(OperationError) as denied:
            reports.get_report(ops, READER, wid, rid, renderer_version=version)
        assert (denied.value.code, denied.value.status) == ('PM_REPORT_SOURCE_UNAVAILABLE', 404)
