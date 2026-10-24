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


SETUP_ACTIONS = frozenset({
    "setup.connection.apply",
    "setup.host",
    "setup.repository",
    "setup.verification",
})

HOST_EFFECT_ACTIONS = frozenset({
    "artifact.accept",
    "artifact.capture",
    "artifact.capture.managed",
    "checkpoint.continue",
    "checkpoint.continue.revalidate",
    "checkpoint.create",
    "cleanup.apply",
    "fanout.plan",
    "fanout.start",
    "github.pr.publish",
    "integration.apply",
    "integration.handoff",
    "integration.preview",
    "pm.conversation.adopt",
    "pm.conversation.propose",
    "pm.continuation.prepare",
    "pm.overview.assess",
    "pm.dependencies.capture",
    "pm.execution.runtime.observe",
    "pm.execution.verify",
    "pm.follow_up.analyze",
    "pm.plan.adopt",
    "pm.run.bat.analyze",
    "pm.run.prepare",
    "pm.session.quiescence.observe",
    "pm.spec.materialize",
    "repository.continue",
    "session.answer",
    "session.approve_pending",
    "session.failover",
    "session.interrupt",
    "session.permissions",
    "session.record_verification",
    "session.relay",
    "session.send",
    "session.start",
    "task.command.reconcile",
    "task.mark_stage",
    "task.pause",
    "task.request_ted",
    "task.resume",
    "task.submit",
    "task.verify",
    "worktree.merge",
})

JOURNAL_ONLY_ACTIONS = frozenset({
    "pm.history.link", "pm.history.unlink",
    "connection.reference.apply",
    "connection.reference.detach",
    "artifact.upload",
    "delivery.merge_and_deploy",
    "deployment.rollback",
    "deployment.start",
    "github.pr.merge",
    "github.pr.update",
    "pm.ahp.observe",
    "pm.backlog.revise",
    "pm.dependencies.declare",
    "pm.dependencies.retract",
    "pm.dod.revise",
    "pm.evidence.capture",
    "pm.follow_up.prepare",
    "pm.github.observe",
    "pm.governance.adoption.link",
    "pm.governance.decide",
    "pm.governance.record",
    "pm.governance.revise",
    "pm.input.confirm",
    "pm.input.record",
    "pm.issue.cancel",
    "pm.issue.publish",
    "pm.lifecycle.close",
    "pm.lifecycle.pause",
    "pm.lifecycle.reopen",
    "pm.lifecycle.retain_inflight",
    "pm.lifecycle.set_stage",
    "pm.maintenance.check",
    "pm.maintenance.repair",
    "pm.maintenance.repair.evidence.record",
    "pm.maintenance.repair.runtime.observe",
    "pm.maintenance_policy.adopt",
    "pm.maintenance_review.candidate.record",
    "pm.maintenance_review.complete",
    "pm.maintenance_review.follow_up.record",
    "pm.maintenance_review.note",
    "pm.maintenance_review.prepare",
    "pm.meeting.complete",
    "pm.meeting.context.revise",
    "pm.meeting.create",
    "pm.meeting.draft",
    "pm.meeting.follow_up.record",
    "pm.meeting.note",
    "pm.message.append",
    "pm.plan.draft",
    "pm.product_goal.revise",
    "pm.project.bind",
    "pm.quality.review.create",
    "pm.quality.review.record",
    "pm.quality.review.revoke",
    "pm.release_baseline.register",
    "pm.roadmap.baseline.set",
    "pm.roadmap.forecast.set",
    "pm.roadmap.observe",
    "pm.run.analyze",
    "pm.snapshot.create",
    "pm.sprint.cancel",
    "pm.sprint.close",
    "pm.sprint.create",
    "pm.sprint.goal.revise",
    "pm.sprint.select",
    "pm.sprint.start",
    "pm.thread.create",
    "pm.thread.plan",
    "pm.thread.plan.revise",
    "pm.thread.rename",
    "pm.work.bind",
    "pm.work_batch.evidence.record",
    "pm.work_batch.start",
    "pm.work_input.defer",
    "pm.work_package.create",
    "pm.work_profile.save",
    "pm.workload.capture",
    "pm.workspace.adapter.select",
    "pm.workspace.create",
    "pm.workspace.member.set",
    "preferences.models.update",
    "pm.github.import",
    "project.github.configure",
    "project.github.import",
    "project.github.sync",
    "project.create",
    "project.order",
    "project.pin",
    "project.skills.update",
    "project.update",
    "repair.create",
    "session.labels.set",
    "session.position",
    "session.read",
    "work.start",
    "work.adopt_and_start",
    "work_item.approve",
    "work_item.continue",
    "work_item.create",
    "work_item.link",
    "work_item.order",
    "work_item.pin",
    "work_item.read",
    "work_item.update",
})


def action_class(action):
    if action in SETUP_ACTIONS:
        return "setup"
    if action in HOST_EFFECT_ACTIONS:
        return "host_effect"
    if action in JOURNAL_ONLY_ACTIONS:
        return "journal_only"
    raise ValueError(f"unclassified action {action!r}")


def _operation_hosts(ops, target, params, binding):
    hosts = _host_names(target, params, binding)
    if hosts or ops is None or getattr(ops, "db", None) is None:
        return hosts
    if isinstance(target, dict) and isinstance(target.get("execution_operation_id"), str):
        row = ops.db.execute(
            "SELECT target, params, result, external_refs FROM operations WHERE operation_id=?",
            (target["execution_operation_id"],),
        ).fetchone()
        if row is not None:
            hosts.update(_host_names(*(json.loads(value) for value in row if value)))
        if not hosts:
            for step in ops.db.execute(
                "SELECT response FROM operation_steps WHERE operation_id=? AND response IS NOT NULL",
                (target["execution_operation_id"],),
            ):
                hosts.update(_host_names(json.loads(step[0])))
    if (not hosts and isinstance(target, dict) and isinstance(params, dict)
            and isinstance(target.get("work_item_id"), str) and type(params.get("link_id")) is int
            and ops.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_item_links'").fetchone()):
        row = ops.db.execute(
            "SELECT ref FROM work_item_links WHERE link_id=? AND work_item_id=? AND kind='session' AND removed_at IS NULL",
            (params["link_id"], target["work_item_id"]),
        ).fetchone()
        if row is not None and isinstance(row[0], str):
            host, sep, _ = row[0].partition("/")
            if sep and host:
                hosts.add(host)
    return hosts


def admission_bindings(ops, action, target, params, binding):
    runner = ops.context.get("git_runner")
    if action in SETUP_ACTIONS or action in JOURNAL_ONLY_ACTIONS or not isinstance(runner, HostGitRunner):
        return binding
    hosts = _operation_hosts(ops, target, params, binding)
    # Indirect workspace/repository actions resolve carriers later. Freeze the
    # configured inventory at admission so that resolution cannot choose a new
    # execution location. Unknown hosts are never authorized by this binding.
    if not hosts:
        hosts.update(runner._hosts)
    locations = {host: runner.execution_binding(host) for host in sorted(hosts)}
    return {**(binding or {}), "git_execution": locations}


def install(journal):
    # Additive DDL only: no user_version or historical data step is allocated.
    with journal.tx():
        journal.db.execute("""CREATE TABLE IF NOT EXISTS git_execution_steps (
            operation_id TEXT NOT NULL REFERENCES operations(operation_id) ON DELETE CASCADE,
            name TEXT NOT NULL, binding TEXT NOT NULL, PRIMARY KEY(operation_id,name))""")


def bind_step(ops, operation_id, name):
    # Effect requests are domain evidence: permission ACKs and repair recovery
    # compare their exact bytes. External refs also have atomic receipt/recovery
    # semantics. Freeze location separately in the same step transaction.
    refs = ops._row(operation_id).get("external_refs") or {}
    locations = (refs.get("admission_binding") or {}).get("git_execution")
    if locations is None:
        return None
    saved = ops.db.execute("SELECT binding FROM git_execution_steps WHERE operation_id=? AND name=?",
                           (operation_id, name)).fetchone()
    if saved is not None:
        if json.loads(saved[0]) != locations:
            raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Original Git step location changed")
    else:
        ops.db.execute("INSERT INTO git_execution_steps(operation_id,name,binding) VALUES(?,?,?)",
                       (operation_id, name, _canonical(locations)))
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
    if op["action"] in SETUP_ACTIONS:
        return  # Setup separately binds before/after configuration revisions.
    if host is None and op["action"] in JOURNAL_ONLY_ACTIONS:
        return
    refs = op.get("external_refs") or {}
    locations = (refs.get("admission_binding") or {}).get("git_execution")
    steps = [json.loads(row[0]) for row in ops.db.execute(
        "SELECT binding FROM git_execution_steps WHERE operation_id=?", (operation_id,))]
    if steps:
        locations = locations if locations is not None else steps[0]
        if any(saved != locations for saved in steps):
            raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Original Git step location changed")
    if locations is None:
        # Legacy unresolved operations predate opt-in. They may read/reconcile
        # on SSH; an explicit local declaration never redirects their effects.
        names = [host] if host else (_operation_hosts(ops, op["target"], op["params"],
            (op.get("external_refs") or {}).get("admission_binding")) or list(runner._hosts))
        if any(runner._require_current(name) is None or runner.execution_binding(name)["transport"] != "ssh"
               for name in names):
            raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Legacy operation requires its original SSH location")
        return
    names = [host] if host else list(locations)
    for name in names:
        if name not in locations or locations[name] is None or locations[name] != runner._require_current(name):
            raise NeedsAttention("GIT_EXECUTION_LOCATION_CHANGED", "Original Git execution location changed")
