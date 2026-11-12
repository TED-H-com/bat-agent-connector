"""Report files retain renderer bytes and cannot expand the owned carrier scope."""
import copy
import hashlib
import json
import random
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_revision_git as git
from bat_agent_connector import pm_revision_host as host
from bat_agent_connector import pm_revision_paths as paths
from bat_agent_connector import pm_roadmap_report_files as files
from bat_agent_connector import pm_roadmap_reports as reports
from bat_agent_connector import pm_worktree
from bat_agent_connector.operations import NeedsAttention, OperationError
from tests.test_pm_roadmap_report_sources import ops as ops_fixture
from tests.test_pm_roadmap_report_sources import read, selected

ops = ops_fixture
OP = 'op_' + '1' * 32


def rendered(content='private report\n', format='markdown', locale='en'):
    result = dict.fromkeys(paths.REPORT_METADATA)
    result.update(workspace_id='ws_' + '2' * 32, observation_revision_id='pmro_' + '3' * 32,
        template_version=1, renderer_version=1, format=format, locale=locale, media_type=reports.FORMATS[format],
        content=content, byte_length=len(content.encode()), output_digest=hashlib.sha256(content.encode()).hexdigest(),
        source_set_digest='4' * 64, view_digest='5' * 64, coverage={})
    return result


def binding():
    return {'pm_workspace_id': 'ws_' + '2' * 32, 'server_id': 'fixture', 'operation_id': OP, 'binding_digest': '6' * 64,
            'config': {'managed_root': '/synthetic/managed', 'remote_url': 'https://example.invalid/o/r'}}


def fixed(value=None):
    ctx = SimpleNamespace(operation_id=OP, op={'created_at': 1_700_000_000})
    return git.request(ctx, binding(), value or files.files(rendered(), [], OP), 'a' * 40)


@pytest.mark.parametrize('format', ['markdown', 'html', 'svg'])
@pytest.mark.parametrize('locale', ['en', 'zh-TW'])
async def test_exact_renderer_bytes_and_manifest(ops, format, locale):
    projection = read(ops, await selected(ops))
    report = reports.render_fixed(projection, format=format, locale=locale)
    revision = files.files(report, projection['source_manifest'], OP)
    prefix = 'docs/pm/reports/pmrpt_' + OP[3:] + '/'
    assert set(revision['files']) == {prefix + 'report.' + paths.REPORT_EXTENSIONS[format], prefix + 'manifest.json'}
    assert revision['files'][prefix + 'report.' + paths.REPORT_EXTENSIONS[format]].encode() == report['content'].encode()
    manifest = json.loads(revision['files'][prefix + 'manifest.json'])
    assert manifest['source_manifest'] == projection['source_manifest']
    assert manifest['output_digest'] == report['output_digest']
    assert not {'commit_sha', 'tree_sha', 'document_digest'} & manifest.keys()
    assert paths.report_manifest({**revision, 'operation_id': OP}) == manifest


@pytest.mark.parametrize('bad', ['path', 'old_id', 'extra', 'kind', 'format', 'locale', 'version', 'bytes', 'manifest_kind', 'manifest_digest', 'non_text'])
def test_service_and_host_reject_changed_or_expanded_intent(bad):
    value = files.files(rendered(), [], OP)
    if bad == 'path':
        value['files']['roadmap.yaml'] = 'overwrite'
    elif bad == 'old_id':
        value = files.files(rendered(), [], 'op_' + '7' * 32)
    elif bad == 'extra':
        value['package_id'] = 'pmwp_' + '8' * 32
    elif bad == 'kind':
        value['artifact_kind'] = 'generic-file'
    elif bad in {'format', 'locale', 'version'}:
        value[{'version': 'renderer_version'}.get(bad, bad)] = True
    elif bad == 'non_text':
        value['files'][next(p for p in value['files'] if '/report.' in p)] = None
    elif bad == 'bytes':
        value['files'][next(iter(value['files']))] += 'changed'
    else:
        path = next(p for p in value['files'] if p.endswith('/manifest.json'))
        manifest = json.loads(value['files'][path])
        manifest['artifact_kind' if bad == 'manifest_kind' else 'output_digest'] = 'wrong'
        value['files'][path] = files.canonical(manifest)
    with pytest.raises((OperationError, TypeError, ValueError)):
        fixed(value)
    request = {**value, 'operation_id': OP, 'expected_parent': 'a' * 40, 'timestamp': 1_700_000_000, 'binding_id': git.binding_id(binding())}
    with pytest.raises((ValueError, TypeError)):
        host.rev_request({**binding(), 'revision': request})


@pytest.mark.parametrize('bad', ['kind', 'id', 'binding', 'files', 'extra', 'sha', 'bool'])
def test_strict_receipt_rejects_each_changed_identity(bad):
    request = fixed()
    receipt = {k: v for k, v in request.items() if k not in {'files', 'timestamp', 'expected_parent'}}
    receipt.update(status='verified', commit_sha='b' * 40, tree_sha='c' * 40)
    assert git._spec_receipt(request, receipt) == receipt
    changed = copy.deepcopy(receipt)
    if bad == 'files':
        changed['file_digests']['README.md'] = 'd' * 64
    else:
        key = {'kind': 'artifact_kind', 'id': 'revision_id', 'binding': 'binding_id', 'sha': 'commit_sha', 'bool': 'renderer_version'}.get(bad, bad)
        changed[key] = True if bad == 'bool' else 'changed'
    with pytest.raises(NeedsAttention):
        git._spec_receipt(request, changed)


def test_combined_file_limit_is_exact_and_whole():
    size = 510_000
    small = files.files(rendered('x' * size), [], OP)
    overhead = sum(len(v.encode()) for v in small['files'].values()) - size
    exact = files.files(rendered('x' * (512_000 - overhead)), [], OP)
    assert sum(len(v.encode()) for v in exact['files'].values()) == 512_000
    with pytest.raises(OperationError) as error:
        files.files(rendered('x' * (512_001 - overhead)), [], OP)
    assert (error.value.code, error.value.status) == ('PM_REPORT_MATERIALIZATION_TOO_LARGE', 413)


def test_exact_transport_limit_and_incompressible_payload(monkeypatch):
    plan = binding()
    base = pm_worktree.host_script(OP, plan, 'pm-revision', revision=fixed())
    source = pm_worktree._host_source()
    monkeypatch.setattr(pm_worktree, '_host_source', lambda: source + ' ' * (120_000 - len(base.encode())))
    assert len(pm_worktree.host_script(OP, plan, 'pm-revision', revision=fixed()).encode()) == 120_000
    with pytest.raises(OperationError, match='PM_SNAPSHOT_TOO_LARGE'):
        pm_worktree.host_script(OP, plan, 'read.pm-revision', revision=fixed())
    monkeypatch.setattr(pm_worktree, '_host_source', lambda: source)
    rng = random.Random(42)
    content = ''.join(rng.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(160_000))
    request = fixed(files.files(rendered(content), [], OP))
    assert sum(len(v.encode()) for v in request['files'].values()) < 512_000
    with pytest.raises(OperationError, match='PM_SNAPSHOT_TOO_LARGE'):
        pm_worktree.host_script(OP, plan, 'pm-revision', revision=request)
