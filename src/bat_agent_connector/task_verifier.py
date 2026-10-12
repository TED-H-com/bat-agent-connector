"""Trusted, observed verification on an isolated worktree.

Only administrator-configured argv (plus a code-owned lockfile install after a
missing-dependency failure) is executed. A task caller cannot supply a command,
exit status or candidate hash. Output is kept in a private 0600 log and never
placed in the service journal or the event feed; only a short redacted tail of
a code/test failure is sent back to the task's own lead for bounded rework.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import signal
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import platform_files
from ._bounded import capture
from .config import state_dir


def _remote_command(alias: str, cwd: str, argv: tuple[str, ...]) -> tuple[str, ...]:
    """Build one SSH remote-command argument so the login shell preserves quoting."""
    script = "cd -- " + shlex.quote(cwd) + " && " + shlex.join(argv)
    # ssh concatenates all command arguments before handing them to the remote
    # shell.  Passing ``sh``, ``-lc`` and a separately quoted script therefore
    # loses the quote boundaries (and can run from the wrong directory).
    return ("ssh", "-o", "BatchMode=yes", alias, "sh -lc " + shlex.quote(script))


def _remote_pidfile(marker: str) -> str:
    """Shell word (expanded remotely) for the private pgid file; marker is hex only."""
    if not re.fullmatch(r"[0-9a-f]{32}", marker):
        raise ValueError("invalid verification marker")
    return '"$HOME/.batc-verify-' + marker + '.pid"'


def _remote_group_command(alias: str, cwd: str, argv: tuple[str, ...], marker: str) -> tuple[str, ...]:
    """Run argv remotely in its own session/process group and record its pgid.

    SSH disconnects do not reliably reach a remote process tree, so a timeout
    kills the recorded group explicitly (see ``_remote_kill_command``).
    """
    pidfile = _remote_pidfile(marker)
    inner = 'echo "$$" >"$0" && cd -- "$1" && shift && exec "$@"'
    script = (shlex.join(("setsid", "-w", "sh", "-c", inner)) + " " + pidfile + " "
              + shlex.join((cwd, *argv)) + "; rc=$?; rm -f " + pidfile + "; exit $rc")
    return ("ssh", "-o", "BatchMode=yes", alias, "sh -lc " + shlex.quote(script))


def _remote_kill_command(alias: str, marker: str) -> tuple[str, ...]:
    """Kill the recorded remote process group and print ``gone`` only once it is empty."""
    pidfile = _remote_pidfile(marker)
    script = (f"i=0; while ! test -f {pidfile} && test $i -lt 30; do i=$((i+1)); sleep 0.1; done; "
              f"if ! test -f {pidfile}; then echo gone; exit 0; fi; "
              f"pg=$(cat {pidfile}); case \"$pg\" in ''|*[!0-9]*) exit 3;; esac; "
              "pkill -KILL -g \"$pg\" 2>/dev/null; i=0; "
              "while ps -eo pgid=,stat= | awk -v g=\"$pg\" '$1==g && $2 !~ /^Z/ {f=1} END {exit !f}'; "
              "do i=$((i+1)); test $i -gt 50 && exit 4; "
              "pkill -KILL -g \"$pg\" 2>/dev/null; sleep 0.1; done; "
              f"rm -f {pidfile}; echo gone")
    return ("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, "sh -c " + shlex.quote(script))


def _group_alive(pgid: int) -> bool:
    """Whether any non-zombie process remains in a local process group."""
    proc = Path("/proc")
    if not proc.is_dir():
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        return True
    for stat_file in proc.glob("[0-9]*/stat"):
        try:
            fields = stat_file.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(fields) > 2 and fields[0] != "Z" and fields[2] == str(pgid):
            return True
    return False


class VerificationProcessStuck(RuntimeError):
    """A timed-out verification process tree could not be proven gone."""


try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib


@dataclass(frozen=True)
class VerificationSettings:
    commands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    ssh_hosts: dict[str, str] = field(default_factory=dict)
    timeout_s: int = 600
    register_tabs: bool = False
    artifact_dir: str | None = None
    base_branches: dict[str, str] = field(default_factory=dict)
    repo_urls: dict[str, str] = field(default_factory=dict)
    event_webhook_url: str | None = None
    event_webhook_secret_file: str | None = None
    artifacts: dict = field(default_factory=dict)


def load_settings(path: str | None = None) -> VerificationSettings:
    path = path or os.environ.get("BATC_TASK_SETTINGS")
    if not path:
        return VerificationSettings()
    p = Path(path).expanduser()
    if platform_files.WINDOWS:
        platform_files.check_private(p)
    elif stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise ValueError("task settings must be mode 0600")
    raw = tomllib.loads(platform_files.read_private(p).decode() if platform_files.WINDOWS else p.read_text())
    return parse_settings(raw)


def parse_settings(raw: dict) -> VerificationSettings:
    """Parse an already read settings document for managed atomic configuration."""
    section = raw.get("verification", {})
    commands = {k: tuple(v) for k, v in section.get("commands", {}).items()}
    if any(not cmd or not all(isinstance(a, str) and a for a in cmd) for cmd in commands.values()):
        raise ValueError("verification commands must be nonempty argv arrays")
    aliases = section.get("ssh_hosts", {})
    if any(not isinstance(v, str) or not v or v.startswith("-") for v in aliases.values()):
        raise ValueError("invalid SSH alias")
    register_tabs = raw.get("task_service", {}).get("register_tabs", False)
    if not isinstance(register_tabs, bool):
        raise ValueError("task_service.register_tabs must be boolean")
    base_branches = raw.get("task_service", {}).get("base_branches", {})
    if not isinstance(base_branches, dict) or any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v or v.startswith("-")
        for k, v in base_branches.items()
    ):
        raise ValueError("task_service.base_branches must map project names to branch names")
    repo_urls = raw.get("task_service", {}).get("repo_urls", {})
    if not isinstance(repo_urls, dict) or any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v.startswith("https://")
        or any(c.isspace() for c in v)
        for k, v in repo_urls.items()
    ):
        raise ValueError("task_service.repo_urls must map project names to https URLs")
    hook = raw.get("task_service", {}).get("event_webhook", {})
    if not isinstance(hook, dict) or any(
            k not in {"url", "secret_file"} or not isinstance(v, str) or not v for k, v in hook.items()):
        raise ValueError("task_service.event_webhook takes url and secret_file strings")
    if hook.get("url"):
        from .task_push import validate_callback_url

        validate_callback_url(hook["url"])
    return VerificationSettings(commands, aliases, int(section.get("timeout_s", 600)), register_tabs,
                                section.get("artifact_dir"), dict(base_branches), dict(repo_urls),
                                hook.get("url"), hook.get("secret_file"), raw.get("artifacts", {}))


class ObservedVerifier:
    def __init__(self, settings: VerificationSettings):
        self.settings = settings

    async def _run(self, host: str, cwd: str, argv: tuple[str, ...], timeout: int = 30) -> tuple[int, str]:
        alias = self.settings.ssh_hosts.get(host)
        if alias:
            cmd = _remote_command(alias, cwd, argv)
            working_dir = None
        else:
            cmd = argv
            working_dir = cwd
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=working_dir,
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        try:
            stdout, _ = await asyncio.wait_for(capture(proc, 4096), timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        if len(stdout) > 4096:
            raise ValueError("verification metadata output too large")
        return proc.returncode, stdout.decode(errors="replace").strip()

    async def identity(self, task: dict, cwd: str) -> dict | None:
        host = task["host"]
        try:
            head_rc, head = await self._run(host, cwd, ("git", "rev-parse", "HEAD"))
            tree_rc, tree = await self._run(host, cwd, ("git", "rev-parse", "HEAD^{tree}"))
            dirty_rc, dirty = await self._run(host, cwd, ("git", "status", "--porcelain"))
        except (OSError, ValueError, asyncio.TimeoutError):
            return None
        if any(rc != 0 for rc in (head_rc, tree_rc, dirty_rc)) or len(head) != 40 or len(tree) != 40:
            return None
        return {"candidate_commit": head, "tree_hash": tree, "clean": not bool(dirty)}

    async def _group_run(self, host: str, cwd: str, argv: tuple[str, ...], timeout: float,
                         fd: int, digest) -> int:
        """Run argv under one deadline covering start, output drain and exit.

        On timeout or cancellation the whole process group is killed; over SSH
        the remote group is killed too and must be confirmed gone.
        """
        alias = self.settings.ssh_hosts.get(host)
        marker = uuid.uuid4().hex
        if alias:
            cmd, working_dir = _remote_group_command(alias, cwd, argv, marker), None
        else:
            cmd, working_dir = argv, cwd
        proc = None
        tree = None
        if platform_files.WINDOWS:
            from .windows_processes import CREATE_SUSPENDED, ProcessTree

            tree = ProcessTree()
        captured = 0
        tail = bytearray()
        limit = 2_000_000

        async def run() -> int:
            nonlocal proc, captured, tail
            platform_args = {"creationflags": CREATE_SUSPENDED} if tree else {"start_new_session": True}
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=working_dir, **platform_args,
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT)
            if tree:
                tree.attach_and_resume(proc)
            assert proc.stdout
            while chunk := await proc.stdout.read(65_536):
                digest.update(chunk)
                if captured < limit:
                    kept = chunk[:limit - captured]
                    os.write(fd, kept)
                    captured += len(kept)
                tail = (tail + chunk)[-65_536:]
            return await proc.wait()

        try:
            exit_code = await asyncio.wait_for(run(), timeout)
        except BaseException as exc:
            await self._kill_tree(proc, alias, marker, tree=tree)
            if not isinstance(exc, asyncio.TimeoutError):
                raise
            exit_code = 124
        else:
            if tree:
                # A successful parent can leave descendants with detached pipes.
                # Closing a kill-on-close job alone does not wait for their exit.
                await self._kill_tree(proc, None, marker, tree=tree)
        finally:
            if tree:
                tree.close()
        if captured >= limit:
            # Keep the real end of the output for failure summaries.
            os.write(fd, b"\n[... output truncated ...]\n" + bytes(tail))
        return exit_code

    async def _kill_tree(self, proc, alias: str | None, marker: str, *, tree=None) -> None:
        if tree:
            tree.terminate()
        if proc is not None and proc.returncode is None:
            try:
                if tree:
                    proc.kill()  # also covers a still-suspended child whose job assignment failed
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if alias:
            # The remote group may still hold the SSH output pipe; kill it first.
            killer = await asyncio.create_subprocess_exec(*_remote_kill_command(alias, marker),
                                                          stdout=asyncio.subprocess.PIPE,
                                                          stderr=asyncio.subprocess.DEVNULL)
            try:
                out, _ = await asyncio.wait_for(capture(killer, 4096), 30)
            except (asyncio.TimeoutError, ValueError):
                if killer.returncode is None:
                    killer.kill()
                await killer.wait()
                out = b""
            if killer.returncode != 0 or out.strip() != b"gone":
                raise VerificationProcessStuck("remote verification process tree is not confirmed gone")
        if tree:
            for _ in range(50):
                if not tree.alive():
                    break
                await asyncio.sleep(0.1)
            else:
                raise VerificationProcessStuck("local verification job is still alive")
        elif proc is not None and not alias:
            for _ in range(50):
                if not _group_alive(proc.pid):
                    break
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.1)
            else:
                raise VerificationProcessStuck("local verification process group is still alive")
        if proc is not None:
            try:
                # asyncio also waits for the output pipe; a holder outside the
                # group is a process we cannot prove gone.
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                raise VerificationProcessStuck("verification output pipe is still held open") from None

    def _log(self, task: dict, kind: str = "") -> tuple[Path, int]:
        artifacts = Path(self.settings.artifact_dir or state_dir() / "task-artifacts")
        if platform_files.WINDOWS:
            platform_files.ensure_private_directory(artifacts)
        else:
            artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
            artifacts.chmod(0o700)
        log_path = artifacts / (str(task["task_id"]) + kind + "-" + uuid.uuid4().hex + ".log")
        return log_path, platform_files.open_private_file(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)

    async def observe(self, task: dict, cwd: str, *, before_run=None, expected_identity=None) -> dict | None:
        argv = self.settings.commands.get(task["project"])
        if not argv:
            return None
        before = await self.identity(task, cwd)
        if (not before or not before["clean"] or expected_identity is not None
                and any(before.get(key) != value for key, value in expected_identity.items())):
            return None
        log_path, fd = self._log(task)
        digest = hashlib.sha256()
        try:
            if before_run:
                before_run()
            exit_code = await self._group_run(task["host"], cwd, argv, self.settings.timeout_s, fd, digest)
        finally:
            os.close(fd)
        after = await self.identity(task, cwd)
        if not after or not after["clean"] or after != before:
            return None
        return {"source": "observed_runner", "candidate_commit": before["candidate_commit"],
                "tree_hash": before["tree_hash"],
                # Administrator argv may itself contain credentials. The private
                # config remains the authority; the journal records its stable
                # fingerprint without copying arguments into status/feed output.
                "command": "argv_sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest(),
                "exit_code": exit_code, "log_ref": str(log_path),
                "output_sha256": digest.hexdigest()}

    async def install_dependencies(self, task: dict, cwd: str, *, before_run=None) -> dict:
        """Run the repo's tracked lockfile install once; the worktree must stay clean."""
        rc, tracked = await self._run(task["host"], cwd, ("git", "ls-files", "--", *LOCKFILE_INSTALLS))
        present = set(tracked.splitlines()) if rc == 0 else set()
        lockfile = next((name for name in LOCKFILE_INSTALLS if name in present), None)
        if not lockfile:
            return {"ok": False, "reason": "no_supported_lockfile"}
        before = await self.identity(task, cwd)
        if not before or not before["clean"]:
            return {"ok": False, "reason": "candidate_not_clean", "lockfile": lockfile}
        log_path, fd = self._log(task, "-install")
        try:
            if before_run:
                before_run()
            exit_code = await self._group_run(task["host"], cwd, LOCKFILE_INSTALLS[lockfile],
                                              self.settings.timeout_s, fd, hashlib.sha256())
        finally:
            os.close(fd)
        after = await self.identity(task, cwd)
        ok = exit_code == 0 and after == before
        return {"ok": ok, "lockfile": lockfile, "exit_code": exit_code, "log_ref": str(log_path),
                "reason": "installed" if ok else ("install_failed" if exit_code else "install_dirtied_worktree")}


# Code-owned install commands, keyed by tracked lockfile in priority order.
LOCKFILE_INSTALLS: dict[str, tuple[str, ...]] = {
    "pnpm-lock.yaml": ("pnpm", "install", "--frozen-lockfile"),
    "yarn.lock": ("yarn", "install", "--frozen-lockfile"),
    "package-lock.json": ("npm", "ci"),
    "uv.lock": ("uv", "sync", "--frozen"),
    "Cargo.lock": ("cargo", "fetch", "--locked"),
}

_MISSING_DEPS = re.compile(
    r"Cannot find module|ERR_MODULE_NOT_FOUND|Cannot find package|ModuleNotFoundError|No module named"
    r"|node_modules/\.bin|: not found\s*$|command not found|error\[E0463\]|npm ERR! missing", re.I | re.M)
_ENVIRONMENT = re.compile(
    r"permission denied|EACCES|EPERM|authenticat|not logged in|login required|could not read Username"
    r"|401 Unauthorized|403 Forbidden|Host key verification failed|No space left on device|ENOSPC"
    r"|Could not resolve host|Temporary failure in name resolution|Connection refused|Read-only file system",
    re.I)


def failure_tail(log_ref: str, *, max_chars: int = 2500, max_lines: int = 40) -> str:
    try:
        with open(log_ref, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65_536))
            raw = fh.read().decode(errors="replace")
    except OSError:
        return ""
    return "\n".join(raw.splitlines()[-max_lines:])[-max_chars:]


def classify_failure(evidence: dict) -> str:
    """Deterministic failure class: code, missing_dependencies or environment."""
    if evidence.get("exit_code") in {124, 126, 255}:
        return "environment"  # timeout, not executable, or SSH transport failure
    tail = failure_tail(str(evidence.get("log_ref") or ""), max_chars=20_000, max_lines=400)
    if not tail:
        return "environment"
    if _MISSING_DEPS.search(tail):
        return "missing_dependencies"
    if _ENVIRONMENT.search(tail):
        return "environment"
    return "code"
