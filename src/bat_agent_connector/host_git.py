"""Configuration-bound Git transport. Local execution deliberately remains disabled.

Only central code constructs these runners. No URL inference, transport retry,
caller executable/environment, or local auditor channel is supported.
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from .checkpoints import _LOCKED_CHECK, SshGitRunner
from .config import git_transport
from .operations import NeedsAttention, StepFailed, _canonical

LOCAL_GIT_REASONS = frozenset({"local_git_not_enabled", "local_git_platform_unsupported", "local_git_tools_missing"})
_EXECUTION = contextvars.ContextVar("host_git_execution", default=None)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class GitRunnerUnavailable(StepFailed):
    def __init__(self, reason, *, missing_helpers=()):
        self.reason = reason
        self.details = {"missing_helpers": list(missing_helpers)} if missing_helpers else {}
        super().__init__("GIT_RUNNER_UNAVAILABLE", reason)


def _readiness(hosts, aliases, platform):
    # PR 2 has no prerequisite probes. Run this configuration preflight off the
    # event loop; PR 3 may add bounded helper checks here, never in available().
    return {name: {"available": bool(aliases.get(name)) if host.git == "ssh" else False,
                   "reason": (None if aliases.get(name) else "ssh_alias_missing") if host.git == "ssh" else
                   ("local_git_platform_unsupported" if platform == "win32" else "local_git_not_enabled"),
                   "details": {}} for name, host in hosts.items()}


class HostGitRunner:
    def __init__(self, config, aliases, *, installation_id=None, server_id=None, current_config=None,
                 current_identity=None):
        self._hosts = copy.deepcopy(config.hosts)
        self._ssh = SshGitRunner(aliases)
        self._installation_id = installation_id
        self._server_id = server_id
        self._current_config = current_config or (lambda: config)
        self._current_identity = current_identity
        with ThreadPoolExecutor(max_workers=1) as pool:
            self._status = pool.submit(_readiness, self._hosts, self._ssh.aliases, sys.platform).result()

    @property
    def aliases(self):
        # Adjacent services retain their own mappings; this is compatibility
        # metadata, never a local dispatch fallback.
        return dict(self._ssh.aliases)

    def _binding(self, host, config):
        configured = config.hosts.get(host)
        if configured is None:
            return None
        # Standalone configuration historically accepts more than bare origins.
        # Bind exact endpoint bytes without copying a possible secret-bearing
        # query/userinfo into durable operation JSON.
        identity = {"name": configured.name, "endpoint_digest": _digest(configured.url),
                    "fingerprint": configured.fingerprint, "profile_id": configured.profile_id}
        transport = git_transport(configured.git)
        relevant = {"transport": transport, "host_identity": identity,
                    "alias": self._ssh.aliases.get(host) if transport == "ssh" else None,
                    "managed_roots": list(configured.managed_roots)}
        return {"transport": transport, "host_identity": identity,
                "installation_id": self._installation_id, "server_id": self._server_id,
                "config_digest": _digest(relevant)}

    def execution_binding(self, host):
        # Use the frozen construction snapshot, rather than mutable Fleet objects.
        class Snapshot:
            hosts = self._hosts
        return self._binding(host, Snapshot)

    def _require_current(self, host):
        binding = self.execution_binding(host)
        if binding is not None:
            identity = {"installation_id": self._installation_id, "server_id": self._server_id}
            if (binding != self._binding(host, self._current_config())
                    or self._current_identity is not None and identity != self._current_identity()):
                raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Original Git execution location changed")
        return binding

    def available(self, host):
        try:
            self._require_current(host)
        except NeedsAttention:
            return False
        return bool(self._status.get(host, {}).get("available"))

    def status(self, host):
        try:
            self._require_current(host)
        except NeedsAttention:
            return {"available": False, "reason": "git_execution_location_changed", "details": {}}
        return copy.deepcopy(self._status.get(host, {"available": False, "reason": "host_unknown", "details": {}}))

    def reader_binding(self, host):
        binding = self.execution_binding(host)
        if binding and binding["transport"] == "ssh":
            # Exact pre-composite identity: persisted merge plans must still read.
            return _digest({"implementation": "bat_agent_connector.checkpoints.SshGitRunner",
                            "alias": self._ssh.aliases.get(host)})
        return _digest(binding)

    async def run(self, host, script, timeout_s=None):
        binding = self._require_current(host)
        require_execution(host)
        if binding is None:
            raise GitRunnerUnavailable("host_unknown")
        if binding["transport"] == "local":
            status = self.status(host)
            raise GitRunnerUnavailable(status["reason"], missing_helpers=status["details"].get("missing_helpers", ()))
        check = _LOCKED_CHECK.get()
        async def locked_check():
            await check()
            self._require_current(host)
            require_execution(host)
        token = _LOCKED_CHECK.set(locked_check) if check is not None else None
        try:
            return await self._ssh.run(host, script, timeout_s)
        finally:
            if token is not None:
                _LOCKED_CHECK.reset(token)

    async def run_account_check(self, host, command, timeout_s=None, *, ssh_alias):
        configured = self._hosts.get(host)
        trusted = configured.confinement.get("check_ssh_alias") if configured else None
        if not trusted or ssh_alias != trusted:
            raise GitRunnerUnavailable("account_check_channel_untrusted")
        return await self._ssh.run_account_check(host, command, timeout_s, ssh_alias=trusted)


def _host_names(*values):
    hosts = set()
    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("host"), str):
                hosts.add(value["host"])
            for item in value.values():
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
    for value in values:
        collect(value)
    return hosts


def admission_bindings(ops, action, target, params, binding):
    runner = ops.context.get("git_runner")
    if action.startswith("setup.") or not isinstance(runner, HostGitRunner):
        return binding
    hosts = _host_names(target, params, binding)
    # Indirect workspace/repository actions resolve carriers later. Freeze the
    # configured inventory at admission so that resolution cannot choose a new
    # execution location. Unknown hosts are never authorized by this binding.
    if not hosts:
        hosts.update(runner._hosts)
    locations = {host: runner.execution_binding(host) for host in sorted(hosts)}
    return {**(binding or {}), "git_execution": locations}


def bind_step(ops, operation_id, name):
    # Effect requests are domain evidence: permission ACKs and repair recovery
    # compare their exact bytes. Freeze location separately, in the same step
    # transaction, without altering those requests or allocating a schema step.
    refs = ops._row(operation_id).get("external_refs") or {}
    locations = (refs.get("admission_binding") or {}).get("git_execution")
    if locations is None:
        return None
    steps = refs.get("git_execution_steps") or {}
    if name in steps:
        if steps[name] != locations:
            raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Original Git step location changed")
    else:
        ops.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?",
            (_canonical({**refs, "git_execution_steps": {**steps, name: locations}}), operation_id))
    return locations


@contextlib.contextmanager
def execution(ops, operation_id):
    token = _EXECUTION.set((ops, operation_id))
    try:
        # Domain readers may check available() before run(). Do not let that
        # definitive refusal terminalize an old effect whose outcome is still
        # unknown on its original location. Fully recorded receipts remain
        # readable; new effects have their own step/frame/proceed fences.
        if (isinstance(ops.context.get("git_runner"), HostGitRunner)
                and ops.db.execute("SELECT 1 FROM operation_steps WHERE operation_id=? "
                                   "AND status IN ('started','uncertain') LIMIT 1", (operation_id,)).fetchone()):
            require_execution()
        yield
    finally:
        _EXECUTION.reset(token)


def require_execution(host=None):
    current = _EXECUTION.get()
    if current is None:
        return
    ops, operation_id = current
    runner = ops.context.get("git_runner")
    if not isinstance(runner, HostGitRunner):
        return
    op = ops._row(operation_id)
    if op["action"].startswith("setup."):
        return  # Setup separately binds before/after configuration revisions.
    refs = op.get("external_refs") or {}
    locations = (refs.get("admission_binding") or {}).get("git_execution")
    steps = list((refs.get("git_execution_steps") or {}).values())
    if steps:
        locations = locations if locations is not None else steps[0]
        if any(saved != locations for saved in steps):
            raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Original Git step location changed")
    if locations is None:
        # Legacy unresolved operations predate opt-in. They may read/reconcile
        # on SSH; an explicit local declaration never redirects their effects.
        names = [host] if host else (_host_names(op["target"], op["params"],
            (op.get("external_refs") or {}).get("admission_binding")) or list(runner._hosts))
        if any(runner._require_current(name) is None or runner.execution_binding(name)["transport"] != "ssh"
               for name in names):
            raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Legacy operation requires its original SSH location")
        return
    names = [host] if host else list(locations)
    for name in names:
        if name not in locations or locations[name] is None or locations[name] != runner._require_current(name):
            raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Original Git execution location changed")
