"""Near-limit legacy carrier probes from the independent report review.

The historical helper is an exact source snapshot, so recovery runs the actual
older transaction without requiring Git history in a shallow CI checkout.
"""
import base64
import copy
import json
import random
import shlex
import string
import subprocess
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from bat_agent_connector import pm_plans, pm_revision_git, pm_spec_format, pm_worktree
from bat_agent_connector.operations import NeedsAttention
from tests.test_pm_roadmap_report_materialization import fixed as fixed_fixture
from tests.test_pm_roadmap_report_materialization import pm as pm_fixture
from tests.test_pm_roadmap_report_materialization import ready as ready_fixture
from tests.test_pm_roadmap_report_materialization import spec_fixed as spec_fixed_fixture
from tests.test_pm_roadmap_report_materialization import world as world_fixture

fixed, pm, ready, spec_fixed, world = fixed_fixture, pm_fixture, ready_fixture, spec_fixed_fixture, world_fixture
OLD = Path(__file__).parent / 'fixtures' / 'pm-carrier-helper-49fe29bb.txt'
OLD_OVERHEAD = 41_419
OP = 'op_' + 'd' * 32


def legacy_script(oid, plan, phase, **payload):
    request = {**plan, 'phase': phase, 'active_operation_id': oid,
               **{k: plan['config'][k] for k in ('managed_root', 'remote_url')}, **payload}
    arg = base64.b64encode(zlib.compress(json.dumps(request).encode(), 9)).decode()
    return 'python3 -c ' + shlex.quote(OLD.read_text()) + ' ' + shlex.quote(arg)


def near_limit(w, kind):
    """Reviewer seed/search and service-validated plan, extended to spec/snapshot."""
    rng = random.Random(918)
    content = ''.join(rng.choices(string.ascii_letters + string.digits, k=128000))
    ops, plan = w['d'].ops, w['plan']
    current = pm_plans.head(ops.db, w['workspace_id'])
    doc = pm_plans._revision(ops.db, w['workspace_id'], current['accepted_revision_id'])['document']
    spec = pm_spec_format.document(ops.db, w['package'])
    ctx = SimpleNamespace(operation_id=OP, op={'created_at': 1234567890})
    phase = 'pm-snapshot' if kind == 'snapshot' else 'pm-revision'
    def make(n):
        if kind == 'snapshot':
            return {'snapshot': pm_worktree.snapshot_document(plan, 'pmr_' + 'd'*32, 'context',
                {'text': content[:n]}, operation_id=OP)}
        if kind == 'plan':
            rid = 'pmrev_' + 'd'*32
            rev = {'workspace_id': w['workspace_id'], 'revision_id': rid,
                   **pm_plans._files(rid, {**doc, 'revision_id': rid, 'markdown': content[:n]})}
        else:
            value = copy.deepcopy(spec)
            value['package']['design']['text'] = content[:n]
            rev = pm_spec_format.files(value, 'pmsp_' + 'd'*32, {'artifact_id': 'fixture'})
        return {'revision': pm_revision_git.request(ctx, plan, rev, pm_plans.latest_git_receipt(ops.db, w['workspace_id'])['commit_sha'])}
    lo, hi = 0, len(content)
    while lo < hi:
        mid = (lo + hi + 1)//2
        if len(legacy_script(OP, plan, phase, **make(mid)).encode()) <= 120000:
            lo = mid
        else:
            hi = mid - 1
    payload = make(lo)
    old_size = len(legacy_script(OP, plan, phase, **payload).encode())
    if kind == 'plan' and old_size != 119_999:
        # zlib sizes need not be monotonic: UUIDs/paths can move a local jump
        # across the target. Keep the exact historical probe, searching nearby
        # synthetic intents before executing either helper, never after effects.
        for offset in range(1, 129):
            for candidate in (lo - offset, lo + offset):
                if 0 <= candidate <= len(content):
                    value = make(candidate)
                    if len(legacy_script(OP, plan, phase, **value).encode()) == 119_999:
                        payload, old_size = value, 119_999
                        break
            if old_size == 119_999:
                break
    assert 119_990 <= old_size <= 120000
    if kind == 'plan':
        assert old_size == 119_999
    return phase, payload


@pytest.mark.parametrize('kind', ['plan', 'spec', 'snapshot'])
async def test_review_near_limit_legacy_requests_fit_every_phase(fixed, kind):
    phase, payload = near_limit(fixed, kind)
    for selected in [phase, 'read.' + phase]:
        script = pm_worktree.host_script(OP, fixed['plan'], selected, **payload)
        assert len(script.encode()) <= 120000
        # Request bytes and argument position remain identical, including readback.
        assert shlex.split(script)[3] == shlex.split(legacy_script(OP, fixed['plan'], selected, **payload))[3]


async def test_original_old_helper_readback_at_legacy_limit(fixed):
    phase, payload = near_limit(fixed, 'plan')
    def execute(script):
        argv = shlex.split(script)
        result = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, check=True, input='{"proceed":true}\n')
        return json.loads(result.stdout.splitlines()[-1])['result']
    original = execute(legacy_script(OP, fixed['plan'], phase, **payload))
    assert original['status'] == 'verified'
    recovered = execute(pm_worktree.host_script(OP, fixed['plan'], 'read.' + phase, **payload))
    assert recovered['receipt'] == original
    assert recovered['receipt']['operation_id'] == OP


def test_plain_helper_overhead_never_consumes_legacy_capacity():
    plan = {'config': {'managed_root': '/synthetic', 'remote_url': 'https://example.invalid/o/r'}}
    command = pm_worktree.host_script(OP, plan, 'read.pm-revision')
    argv = shlex.split(command)
    assert argv[:2] == ['python3', '-c'] and len(argv) == 4
    # Plain source plus shell quoting must fit the historical overhead budget.
    assert len(shlex.quote(argv[2]).encode()) <= OLD_OVERHEAD
    assert len(shlex.quote(OLD.read_text()).encode()) == OLD_OVERHEAD


def test_spec_receipt_error_preserves_original_message():
    fixed = dict.fromkeys(('workspace_id', 'revision_id', 'operation_id', 'document_digest',
                          'file_digests', 'binding_id', 'change_id', 'package_id'))
    with pytest.raises(NeedsAttention) as error:
        pm_revision_git._spec_receipt({**fixed, 'artifact_kind': 'pm-spec'}, {})
    assert error.value.message == 'The original exact spec Git receipt cannot yet be verified'
