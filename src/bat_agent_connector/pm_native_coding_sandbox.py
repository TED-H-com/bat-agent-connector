"""Unregistered Linux coding-boundary experiment; no model or execution authority.

This module constructs an offline probe, not a product runtime. See
docs/design/pm-native-coding-sandbox.md before reusing any boundary component.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from .pm_native_runtime import (
    DISABLED_FEATURES,
    SOURCE,
    VERSION,
    _check_host_config,
    _process_identity,
    inspect_process,
)

PROFILE = "connector-coding-offline-spike-v1"
MAX_BYTES = 64 * 1024
MAX_SECONDS = 30
SCRATCH_TARGET = "/tmp"  # noqa: S108 - namespace target bound to private disk storage


@dataclass(frozen=True)
class Mount:
    """An explicit, reviewed toolchain input, never an ownership assertion."""

    source: Path
    target: str


def eligibility() -> dict:
    return {"supported": False, "reason": "offline_boundary_spike_only", "profile": PROFILE,
            "required_version": VERSION, "reviewed_source": SOURCE,
            "model_execution_verified": False, "product_execution_authorized": False}


def _quoted(value):
    return json.dumps(str(value), ensure_ascii=True)


def permission_config() -> str:
    """No builtin inheritance or :minimal broad read roots. No auth is supplied."""
    settings = {
        "default_permissions": PROFILE, "approval_policy": "never", "web_search": "disabled",
        "allow_login_shell": False, "project_root_markers": [], "project_doc_max_bytes": 0,
        "cli_auth_credentials_store": "file", "history.persistence": "none",
        "log_dir": "/controller/log", "sqlite_home": "/controller/state",
        "agents.enabled": False, "orchestrator.mcp.enabled": False,
        "cloud.skills.enabled": False, "skills.bundled.enabled": False,
        "skills.include_instructions": False, "features.skip_host_skill_discovery": True,
        "features.use_legacy_landlock": False,
        **{f"features.{name}": False for name in DISABLED_FEATURES
           if name not in {"shell_tool", "unified_exec"}},
        "features.shell_tool": True, "features.unified_exec": True,
        "shell_environment_policy.inherit": "none",
        "shell_environment_policy.set.PATH": "/runtime/bin:/usr/bin",
        "shell_environment_policy.set.SHELL": "/runtime/bin/sh",
        "shell_environment_policy.set.TMPDIR": SCRATCH_TARGET,
        "shell_environment_policy.set.PYTHONHOME": "/runtime/python",
        "shell_environment_policy.set.PYTHONDONTWRITEBYTECODE": "1",
    }
    lines = [f"{key} = {json.dumps(value)}" for key, value in settings.items()]
    lines += ["[mcp_servers]", f"[permissions.{PROFILE}.filesystem]",
              '"/runtime" = "read"', '"/lib" = "read"', '"/lib64" = "read"', '"/usr/lib" = "read"',
              '"/usr/bin/bwrap" = "read"',
              '"/workspace" = "write"', '"/workspace/.git" = "read"',
              '"/workspace/.codex" = "none"', '"/workspace/.agents" = "none"',
              '"/tmp" = "write"', '"/controller" = "none"', '"/foreign" = "none"',
              '"/home" = "none"', '"/root" = "none"',
              f"[permissions.{PROFILE}.network]", "enabled = false"]
    return "\n".join(lines) + "\n"


def boundary_argv(*, bwrap: Path, codex: Path, bundled_bwrap: Path, workspace: Path,
                  controller: Path, scratch: Path, empty: Path, foreign: Path,
                  toolchain: tuple[Mount, ...], home: str, native_home: str,
                  command: tuple[str, ...]) -> list[str]:
    """Build a closed filesystem from explicit inputs; only for caller-owned probes.

    Directories must already exist. These checks reject accidental broad mounts;
    they do not adopt resources or replace central creation/source authority.
    """
    if sys.platform != "linux" or not 1 <= len(toolchain) <= 32:
        raise ValueError("Linux and an explicit bounded toolchain are required")
    _check_host_config()  # Never hide administrator policy to make a probe pass.
    directories = [workspace, controller, scratch, empty, foreign]
    if any(not path.is_absolute() or path.is_symlink() or not path.is_dir() for path in directories):
        raise ValueError("Probe directories must be original absolute directories")
    resolved = [path.resolve() for path in directories]
    if any(a == b or a in b.parents or b in a.parents
           for index, a in enumerate(resolved) for b in resolved[index + 1:]):
        raise ValueError("Probe directories must be disjoint")
    if list(empty.iterdir()):
        raise ValueError("Customization mask must be empty")
    if not home.startswith(("/home/", "/root")) or not native_home.startswith(home + "/"):
        raise ValueError("Nonstandard inherited home layout is unsupported by this spike")
    if any(part in {".", ".."} for value in (home, native_home) for part in Path(value).parts):
        raise ValueError("Home paths must be absolute normalized paths")
    if not command or len(command) > 16 or sum(len(arg.encode()) for arg in command) > MAX_BYTES:
        raise ValueError("Probe command exceeds its fixed argument budget")
    if not command[0].startswith("/runtime/"):
        raise ValueError("Only explicit toolchain commands may be probed")
    for path in (bwrap, codex, bundled_bwrap):
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("Explicit executable unavailable")
    args = [str(bwrap), "--unshare-all", "--die-with-parent", "--new-session", "--tmpfs", "/",
            "--proc", "/proc", "--dev", "/dev", "--dir", "/runtime/bin",
            "--ro-bind", str(codex), "/runtime/bin/codex",
            "--ro-bind", str(bundled_bwrap), "/runtime/codex-resources/bwrap"]
    seen = set()
    for mount in toolchain:
        target = Path(mount.target)
        if (not mount.source.is_absolute() or not mount.source.exists()
                or mount.source.resolve() in {Path("/"), Path("/usr"), Path("/etc"), Path("/home"), Path(home)}
                or not target.is_absolute() or ".." in target.parts
                or (not mount.target.startswith(("/runtime/", "/lib/", "/lib64/", "/usr/lib/"))
                    and mount.target != "/usr/bin/bwrap")
                or (not mount.target.startswith("/runtime/") and not mount.source.is_file())
                or mount.target in seen or mount.target in {"/runtime/bin/codex", "/runtime/codex-resources/bwrap"}):
            raise ValueError("Toolchain mounts must be narrow, unique explicit inputs")
        seen.add(mount.target)
        args += ["--ro-bind", str(mount.source.resolve()), mount.target]
    args += ["--bind", str(workspace), "/workspace", "--bind", str(scratch), SCRATCH_TARGET,
             "--bind", str(controller), "/controller", "--dir", home,
             "--bind", str(controller), native_home, "--ro-bind", str(foreign), "/foreign",
             "--ro-bind", str(empty), "/workspace/.codex", "--ro-bind", str(empty), "/workspace/.agents",
             "--chdir", "/workspace", "--", "/runtime/bin/codex", "sandbox", "linux",
             "--permission-profile", PROFILE, "--cd", "/workspace", "--", *command]
    return args


def environment(home: str, native_home: str) -> dict:
    # Per-child values only. Neither caller os.environ nor credential variables
    # are inherited. Home values refer solely to synthetic namespace mounts.
    return {"HOME": home, "CODEX_HOME": native_home, "PATH": "/runtime/bin:/usr/bin",
            "SHELL": "/runtime/bin/sh", "TMPDIR": SCRATCH_TARGET, "LANG": "C.UTF-8"}


async def offline_probe(argv: list[str], env: dict, *, timeout: float = MAX_SECONDS) -> dict:
    """Capture one offline diagnostic command with original process/death evidence.

    This is deliberately not a model-turn adapter. Callers must retain their
    synthetic data if process death is unknown; no path is removed here.
    """
    if not 0 < timeout <= MAX_SECONDS:
        raise ValueError("Probe timeout must be 0–30 seconds")
    proc = await asyncio.create_subprocess_exec(*argv, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, close_fds=True)
    identity = _process_identity(proc.pid)
    chunks = [bytearray(), bytearray()]

    async def drain(stream, index):
        while block := await stream.read(4096):
            chunks[index].extend(block)
            if len(chunks[index]) > MAX_BYTES:
                raise ValueError("Probe output exceeds 64 KiB")

    readers = [asyncio.create_task(drain(proc.stdout, 0)), asyncio.create_task(drain(proc.stderr, 1))]
    reason = None
    try:
        await asyncio.wait_for(asyncio.gather(*readers, proc.wait()), timeout)
    except (asyncio.TimeoutError, ValueError) as error:
        reason = "timeout" if isinstance(error, asyncio.TimeoutError) else "output_limit"
    finally:
        if proc.returncode is None and inspect_process(identity)["state"] == "alive":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), 3)
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # A byte-limit cancellation can leave asyncio's pipe transport paused
        # with buffered output even after the original process has died. Drain
        # without retaining bytes, bounded independently of the command budget.
        async def discard(stream):
            while await stream.read(4096):
                pass
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.gather(discard(proc.stdout), discard(proc.stderr)), 1)
    death = inspect_process(identity)
    stderr = bytes(chunks[1][:MAX_BYTES]).decode("utf-8", errors="replace")
    if reason is None and proc.returncode != 0 and "bwrap: No permissions to create a new namespace" in stderr:
        reason = "namespace_unavailable"
    return {**eligibility(), "returncode": proc.returncode, "reason": reason,
            "stdout": bytes(chunks[0][:MAX_BYTES]).decode("utf-8", errors="replace"),
            "stderr": stderr,
            "process": identity, "process_death_confirmed": death["state"] == "dead",
            "death_proof": death["proof"], "network_mode": "offline",
            "config_digest": hashlib.sha256(permission_config().encode()).hexdigest()}
