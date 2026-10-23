"""Managed Git preparation seam. No native route or launcher is registered here."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import uuid
from dataclasses import asdict, dataclass
from importlib import resources

from . import checkpoints
from .operations import RERUN, AmbiguousOutcome, NeedsAttention


async def prepare_published(ctx, plan, call):
    async def reconcile_fetch(_):
        observed = await call("read.fetch")
        if observed.get("pinned") is True:
            return {"clone_path": plan["clone_path"], "source_sha": plan["source_sha"], "pinned": True}
        return RERUN  # fixed object fetch/CAS only; carrier/start is a separate effect
    await ctx.step("repository.fetch", lambda: call("fetch"),
                   request={"source_sha": plan["source_sha"], "repository_id": plan["repository_id"]}, reconcile=reconcile_fetch)
    async def reconcile_carrier(_):
        observed = await call("read.carrier")
        return observed.get("carrier") or RERUN  # prepare refuses a leftover branch or different carrier
    await ctx.step("worktree.prepare", lambda: call("prepare"),
                   request={"clone_path": plan["clone_path"], "worktree_path": plan["worktree_path"], "branch": plan["branch"]},
                   reconcile=reconcile_carrier)


async def prepare_pm(ctx, plan, call):
    async def read_pin(_=None):
        observed = await call("read.fetch")
        return {"clone_path": plan["clone_path"], "source_sha": plan["source_sha"], "pinned": True} if observed.get("pinned") else None
    async def fetch():
        # A later run reuses the proven original commit even when its branch moved.
        return await read_pin() or await call("fetch")
    await ctx.step("pm.repository.fetch", fetch, request={"binding": plan["binding_digest"], "source_sha": plan["source_sha"]},
                   reconcile=read_pin)
    async def read_carrier(_=None):
        observed = await call("read.pm-carrier")
        return observed.get("carrier")
    async def prepare():
        return await read_carrier() or await call("prepare")
    result = await ctx.step("pm.worktree.prepare", prepare,
                            request={"worktree_path": plan["worktree_path"], "creation_operation_id": plan["creation_operation_id"]},
                            reconcile=read_carrier)
    current = await read_carrier()
    if not current or any(current[key] != result[key] for key in ("clone_path", "worktree_path", "branch")):
        raise NeedsAttention("PM_WORKTREE_CHANGED", "The original PM carrier cannot be verified")
    # A verified PM-only commit may advance the branch after this bootstrap
    # receipt; the host validates the complete own-commit chain separately.
    return current


EMPTY_MANIFEST = hashlib.sha256(b'[]').hexdigest()
CREATION_UNPROVEN = 'NATIVE_CARRIER_CREATION_UNPROVEN'
CHANGED = 'NATIVE_CARRIER_CHANGED'
SOURCE_UNPROVEN = 'NATIVE_SOURCE_UNPROVEN'


def _require(condition, code):
    if not condition:
        raise NeedsAttention(code, 'Managed carrier evidence is unproven')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class FixedSource:
    host: str
    repository: str
    sha: str
    location: str
    ref: str | None = None  # None means a read-only checkpoint on this exact host.


@dataclass(frozen=True, init=False)
class CarrierReceipt:
    _document: str

    def __init__(self, *args, **kwargs):
        raise NeedsAttention(CREATION_UNPROVEN, 'Only managed preparation produces carrier receipts')

    def reference(self):
        data = json.loads(self._document)
        return {**data, 'creation_digest': hashlib.sha256(self._document.encode()).hexdigest()}


def _receipt(document):
    receipt = object.__new__(CarrierReceipt)
    object.__setattr__(receipt, '_document', _json(document))
    return receipt


async def _call(ctx, plan, phase, check, creation=None):
    runner = ctx.service.context['git_runner']
    binding = runner.execution_binding(plan['host']) if hasattr(runner, 'execution_binding') else None
    _require(runner.available(plan['host']) and (binding is None or binding['transport'] == 'ssh'), 'GIT_RUNNER_UNAVAILABLE')
    shared = (resources.files(__package__) / 'repository_sync_host.py').read_text().split('\nif __name__ == "__main__":', 1)[0]
    extension = (resources.files(__package__) / 'native_carrier_host.py').read_text()
    req = {**plan, 'phase': phase, 'creation': creation}
    arg = base64.b64encode(_json(req).encode()).decode()
    script = 'python3 -c ' + shlex.quote(shared + '\n' + extension) + ' ' + shlex.quote(arg)
    await check()
    token = checkpoints._LOCKED_CHECK.set(None if phase.startswith('read.') else check)
    try:
        result = json.loads(await runner.run(plan['host'], script, timeout_s=1800))
    finally:
        checkpoints._LOCKED_CHECK.reset(token)
    if 'error' in result:
        if result['error'] == 'REPOSITORY_HOST_UNCERTAIN':
            raise AmbiguousOutcome('Native carrier host effect is unproven')
        raise NeedsAttention(result['error'], 'Managed carrier requires review')
    return result['result']


async def prepare(ctx, source, *, attempt_id, managed_root, artifact_manifest_digest, check):
    """Internal bound-source caller only; check revalidates authority at the SSH fence."""
    _require(type(source) is FixedSource and isinstance(source.sha, str) and re.fullmatch('[0-9a-f]{40}', source.sha), SOURCE_UNPROVEN)
    _require(isinstance(attempt_id, str) and bool(attempt_id) and re.fullmatch('op_[0-9a-f]{32}', ctx.operation_id), CREATION_UNPROVEN)
    _require(artifact_manifest_digest == EMPTY_MANIFEST, SOURCE_UNPROVEN)
    _require(source.ref is not None or source.location.startswith('/'), SOURCE_UNPROVEN)
    oid = 'op_' + uuid.uuid5(uuid.NAMESPACE_URL, ctx.operation_id + ':' + attempt_id).hex
    clone = managed_root + '/batc-published-' + oid[3:]
    plan = {'host': source.host, 'repository': source.repository, 'source_sha': source.sha,
        'source_ref': source.ref, 'remote_url': source.location, 'source': asdict(source),
        'operation_id': oid, 'creation_operation_id': ctx.operation_id, 'attempt_id': attempt_id,
        'managed_root': managed_root, 'clone_path': clone, 'worktree_path': clone + '/.bat-worktrees/batc-published-' + oid[3:15],
        'branch': 'batc/published-' + oid[3:15], 'artifact_manifest_digest': artifact_manifest_digest,
        'markers': {'managed-clone': 'true', 'operation': oid, 'host': source.host, 'repository': source.repository}}
    async def unknown(_):
        return None  # No creation proof means no authorization to repeat an effect.
    fetched = await ctx.step('native.repository.fetch', lambda: _call(ctx, plan, 'fetch', check), request=plan, reconcile=unknown)
    observed = await ctx.step('native.worktree.prepare', lambda: _call(ctx, plan, 'prepare', check, fetched['physical']),
                              request=plan, reconcile=unknown)
    receipt = _receipt({**{k: plan[k] for k in ('creation_operation_id', 'host', 'repository', 'source_sha',
        'attempt_id', 'branch', 'artifact_manifest_digest')}, **observed['physical'], 'cwd': plan['worktree_path'], 'plan': plan})
    return await readback(ctx, receipt, check=check)


async def readback(ctx, receipt, *, check):
    _require(type(receipt) is CarrierReceipt, CREATION_UNPROVEN)
    saved = receipt.reference()
    current = await _call(ctx, saved['plan'], 'read.carrier', check, {k: saved[k] for k in ('clone', 'worktree', 'git_dir')})
    _require(current.get('pinned') is True and current.get('carrier') and current.get('physical') == {k: saved[k] for k in ('clone', 'worktree', 'git_dir')}, CHANGED)
    return receipt
