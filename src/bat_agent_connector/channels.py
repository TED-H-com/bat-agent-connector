"""Channel allowlist, enforced in the client core.

Only channels listed here can ever leave this process. Read channels are always
allowed. Write channels are allowed only when the host config enables writes.
Everything else (stop/reset/kill sessions, pty writes, file ops, settings,
workspace save, runtime installs, app updates, account/auth flows, ...) is
rejected before a frame is sent.
"""

from __future__ import annotations

# Channels that stay in the `agent:` namespace on the server (not folded to `claude:`).
AGENT_NATIVE = frozenset(
    {
        "agent:list-presets",
        "agent:get-supported-session-types",
        "agent:usage",
        "agent:usage-snapshot",
        "agent:latency-samples",
    }
)

READ_CHANNELS = frozenset(
    {
        "app:get-version",
        "profile:list",
        "profile:get-active-ids",
        "workspace:load",
        "claude:get-session-meta",
        "claude:get-session-state",
        # Bounded read-only replay, BAT b7419892; see docs/design/pm-runtime.md.
        "claude:sync-session",
        "claude:get-pm-runtime",
        "claude:load-archived",
        "claude:list-sessions",
        "claude:is-resting",
        "claude:get-context-usage",
        "claude:get-supported-models",
        "claude:get-supported-efforts",
        "agent:list-presets",
        "agent:get-supported-session-types",
        "agent:usage-snapshot",
        "notification:list",
        "runtime:get-status",
        # worktree/git reads (used by worktree_status and orchestrate pre-checks)
        "worktree:status",
        "claude:get-worktree-status",
        "git:status",
        "git:branch",
        "git:getRoot",
        "git:log",
        "git:diff",
        "git:diff-files",
    }
)

WRITE_CHANNELS = frozenset(
    {
        "claude:send-message",
        "claude:client-resume",
        "claude:interrupt-turn",
        "claude:abort-session",
        "claude:resolve-ask-user",
        "claude:resolve-permission",
        # permission mode of an existing session (used by session_set_permissions; the connector
        # only allows raising to allow-all when the host's default_permission_mode is allow_all)
        "claude:set-permission-mode",
        "claude:set-codex-sandbox-mode",
        "claude:set-codex-approval-policy",
    }
)

# Third tier, per host `orchestrate = true` (requires `writes = true` too).
ORCHESTRATE_CHANNELS = frozenset(
    {
        "claude:start-session",
        "worktree:create",
        "worktree:merge",
        "worktree:remove",
        "worktree:rehydrate",
        # unloads a finished agent from the host runtime (transcript and tab stay; resumable).
        # Only session_cleanup uses it, after its gates.
        "claude:stop-session",
    }
)

# Never sent through invoke(). Only BatClient.append_workspace_terminal() may use it,
# and only in an append-only, verified way (orchestrate tier + register_tabs).
GUARDED_CHANNELS = frozenset({"workspace:save"})

# Documented for tests and humans. Anything not in READ/WRITE is denied anyway.
NEVER_EXPOSED_EXAMPLES = frozenset(
    {
        "claude:reset-session",
        "claude:rest-session",
        "claude:stop-task",
        "claude:resume-session",
        "claude:fork-session",
        "claude:clear-archive",
        "claude:archive-messages",
        "claude:set-model",
        "claude:account-switch",
        "claude:account-remove",
        "claude:auth-login-start",
        "codex:account-switch",
        "pty:create",
        "pty:write",
        "pty:kill",
        "pty:restart",
        "fs:readFile",
        "fs:delete-path",
        "fs:mkdir",
        "fs:upload-tmp-begin",
        "settings:save",
        "workspace:save",
        "runtime:install",
        "runtime:clear-managed",
        "app:install-update",
        "app:relaunch",
        "claude:cleanup-worktree",
        "github:pr-comment",
        "snippet:create",
        "notification:clear",
        "profile:open",
    }
)

# Per-channel client-side timeouts (seconds). The server allows 300 s for
# send/resume; send-message resolves once the turn is *accepted*.
TIMEOUTS = {
    "claude:get-session-state": 60.0,
    "claude:sync-session": 60.0,
    "claude:load-archived": 30.0,
    "runtime:get-status": 30.0,
    "claude:client-resume": 300.0,
    "claude:send-message": 120.0,
    "claude:start-session": 300.0,
    "worktree:create": 120.0,
    "worktree:merge": 120.0,
    "worktree:remove": 60.0,
    "worktree:status": 90.0,  # computes the branch diff; large branches take a while
    "claude:stop-session": 30.0,
    "git:log": 15.0,
    "git:diff": 20.0,
    "git:diff-files": 15.0,
    "workspace:save": 30.0,
}
DEFAULT_TIMEOUT = 15.0


def canonical(channel: str) -> str:
    """Fold `agent:<verb>` to `claude:<verb>` like the server does."""
    if channel.startswith("agent:") and channel not in AGENT_NATIVE:
        return "claude:" + channel[len("agent:") :]
    return channel


def is_write(channel: str) -> bool:
    c = canonical(channel)
    return c in WRITE_CHANNELS or c in ORCHESTRATE_CHANNELS or c in GUARDED_CHANNELS


def check_allowed(channel: str, *, allow_writes: bool, allow_orchestrate: bool = False) -> str:
    """Return the canonical channel or raise ChannelNotAllowed."""
    from .errors import ChannelNotAllowed

    c = canonical(channel)
    if c in READ_CHANNELS:
        return c
    if c in WRITE_CHANNELS:
        if allow_writes:
            return c
        raise ChannelNotAllowed(f"write channel {c!r} blocked: writes are disabled for this host")
    if c in ORCHESTRATE_CHANNELS:
        if allow_writes and allow_orchestrate:
            return c
        raise ChannelNotAllowed(
            f"orchestrate channel {c!r} blocked: orchestrate tier is disabled for this host"
        )
    if c in GUARDED_CHANNELS:
        raise ChannelNotAllowed(
            f"channel {c!r} is only reachable through the append-only tab registration helper"
        )
    raise ChannelNotAllowed(f"channel {c!r} is not on the connector allowlist")


def timeout_for(channel: str) -> float:
    return TIMEOUTS.get(canonical(channel), DEFAULT_TIMEOUT)
