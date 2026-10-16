# Tailscale sign-in recovery (T10)

Connection settings offers a compact local Tailscale section using the existing
panel, actions, secondary-button and muted-text styles. It distinguishes missing
installation, unknown status, sign-in needed, device approval needed, stopped,
starting and running. Running is Tailscale's observation, never BAT/Connector readiness.
The explicit **Open Tailscale** action starts its existing Windows tray application;
the user chooses Log in there. Focus/visibility return and Refresh only reread status.
Keyboard focus stays on the pressed Tailscale action while that read or opening
runs, and moves to the next opening action only when the pressed one ends
disabled or hidden.
There is no automatic application launch, login CLI, network repair or AuthURL opener.

Native-only discovery uses the OS Program Files directory and exactly
`Tailscale/tailscale.exe` and `Tailscale/tailscale-ipn.exe`, with regular-file and
no-reparse checks. No PATH search, caller path, executable, URL or argv is accepted.
Only `status --json` is invoked for diagnosis, bounded to 3 seconds and 1 MiB.
Only its fixed BackendState enum crosses IPC; peer, user, tailnet, AuthURL and raw
errors/output do not. Unsupported/missing/failed observations never become logged in.

The packaged main-window command `tailscale_control` accepts `status`,
`open {request_id}` and `receipt {request_id}`; IDs are exactly 32 lowercase hex.
An OS-account/login-scoped opaque namespace separates local receipts from central
identity/cursors. The fixed local journal retains original requests across reload.
An opening intent is durable before process creation. Original IDs only read their
receipt thereafter, including uncertain outcomes; an uncertain latest intent blocks
a replacement request. A started receipt proves process creation, not tray rendering,
foreground focus, sign-in or a network connection. A proven pre-spawn failure is
distinct; a later explicit opening uses a new ID, never an automatic retry.
The journal is capped at 4096 receipts / 512 KiB; exhaustion refuses new opens and
does not prune IDs in a way that could permit replay. Its bytes contain only local
scope, request IDs and outcome enums. At most one diagnostic subprocess runs per app.

The IPC captures the existing monotonic Control ticket before scheduling. Open holds
the shared Launcher guard, verifies the original installation/configuration or its
continued absence, refuses pending migration, and rechecks the ticket at the final
effect. Quit/update stop then reset cannot revive queued work. Status and original
receipt reads remain usable while local mutation is fenced. No Fleet owner, tunnel,
selection, central operation, BAT window or Tailscale service is created or stopped.

Sources checked: official Tailscale
[Windows installation instructions](https://tailscale.com/docs/install/windows),
[Windows executable list](https://tailscale.com/docs/integrations/firewalls), and
[GUI maintainer clarification](https://github.com/tailscale/tailscale/issues/15094).
The GUI wrapper is closed source, so this adapter does not claim a session deep link
or a programmatic sign-in/focus guarantee. Status schema/CLI were inspected at
`tailscale/tailscale` commit `76648a00fd8156e56e75b84926d484abf55ee7f7`,
`ipn/ipnstate/ipnstate.go` and `cmd/tailscale/cli/status.go`. The upstream CLI warns
that JSON may change; unknown future states fail closed.

Tests use temporary journals and synthetic executables/IPC only. Installed Windows
path, tray, login and return-to-Dashboard acceptance remains required with the user's
Tailscale version. No real Tailscale status, launch, login or network command is part
of development verification. Browser and non-Windows clients show the native limit.
Ordinary Fleet route/readiness workers remain independent. Returning from sign-in
does not reset an exhausted recovery budget or implicitly launch a monitor/tunnel.
