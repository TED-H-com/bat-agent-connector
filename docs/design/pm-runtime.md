# Bounded BAT PM runtime evidence

Issue [#88](https://github.com/teddashh/bat-agent-connector/issues/88) introduces
the read-only `claude:sync-session` channel and `pm_runtime.py`. The PM operation
service, workspace membership and durable writer record remain responsible for
admission. This module neither creates sessions nor dispatches coding tasks.

The audited BAT source is fixed at
[`b7419892fbc9946799b64cca24c2ec8c7fa15c42`](https://github.com/tony1223/better-agent-terminal/tree/b7419892fbc9946799b64cca24c2ec8c7fa15c42).
Runtime version observations are separate from that source audit.

## Filesystem options and external tools

The Codex start, resume and turn builders forward read-only sandbox and never
approval settings. `readonly_start_options` supplies these exact BAT options;
`check_readonly` uses existing confinement readback. `options_confirmed` is
distinct from actual sandbox enforcement. Automated tests do not claim live OS
acceptance, and human OS acceptance is not an implementation prerequisite.

The audited builders do not expose a per-session external MCP tool allowlist or
disable switch, and session metadata does not attest that external tool effects
are restricted. Consequently read-only filesystem options alone cannot authorize
an automatic Idea/Planning turn. The capability report names the limitation and
provides `observer_context_only` fallback. It does not claim a writable PM adapter,
silently install/change runtime configuration, or use a prompt as enforcement.
Future adapters must establish this boundary in their actual runtime contract.

Sources: [request builders](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/codex_app_server.rs#L2028),
[session metadata](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/codex_app_server.rs#L600),
and [runtime version probe](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/commands/runtime.rs#L47).

## Original source evidence

`sync_session(client, session_id, cursor)` validates a single replay page. BAT
returns original event dictionaries containing `seq`, `at`, `channel`, and
`params`, plus a session-specific `{epoch, seq}` cursor. Events remain unchanged;
the journal may attach their epoch when persisting. A page and its cursor must
commit together. Invalid, foreign-session or noncontiguous events cannot advance
the durable cursor. Public reads still require the PM workspace's visibility.

A fresh PM-owned native session serves exactly one bounded turn. Before sending,
`fresh_send_cursor` requires a stable read-only snapshot with zero previous turns,
no messages, no pending interaction and the expected native session ID. The input
key, prompt digest and this cursor are persisted before transport. A missing ACK
never authorizes resending; replay is the recovery path.

The replay buffer lasts ten minutes and has event/byte limits. A BAT restart,
session eviction or retention gap requires a snapshot. Such a snapshot after send
is an unknown result, even when its transcript looks complete. A new snapshot may
not erase a recorded gap. Pagination must be fully collected before import.

Sources: [replay implementation](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/crates/bat-remote-protocol/src/session_replay.rs#L88),
[sync snapshot boundary](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/remote_server.rs#L2361).

## Completion and assistant provenance

`assess_turn` reads all durable events after the original send cursor. A normal
completion needs contiguous evidence and exactly one `claude:turn-end` carrying
`reason=completed`, a nonempty `turnId` and the original `sdkSessionId`. The input
digest can additionally verify the single native user message. Permission/model
drift, multiple inputs or terminal events, malformed evidence and late assistant
messages keep the result unknown. Abort, error, resting, missing or oversized
output remain incomplete. Idle state, timestamps and send ACKs are insufficient.

Assistant messages preserve their native IDs and event sequence references. The
last completed assistant message supplies the answer because BAT can receive a
complete message without earlier text deltas; native terminal text is a fallback.
The original events remain available to examine preceding commentary. Import uses
an internal assistant provenance path, never authenticated human message append.
Only actual observed model metadata is reported; requested model is not proof.

Sources: [native assistant messages](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/codex_app_server.rs#L7877),
[native completion](https://github.com/tony1223/better-agent-terminal/blob/b7419892fbc9946799b64cca24c2ec8c7fa15c42/src-tauri/src/codex_app_server.rs#L8163).
