# Durable native PM analysis

`pm.run.analyze` performs one bounded discussion turn for the current Idea or
Planning thread. It shares the workspace PM generation writer and discussion
history with package preparation and plan adoption. It does not adopt a plan,
confirm an input, create coding work, change phase, or take over any existing
session. It currently selects the fixed `codex-native-linux` runtime contract;
[the native runtime contract](pm-native-runtime.md) defines its actual isolation
and pinned protocol boundary.

## Admission and context

The original caller needs `observe`, `manage`, and `start`, plus current write
membership in the selected workspace. Target contains `workspace_id` and
`thread_id`; preconditions contain the exact `expected_version`. Parameters are
`purpose` (1–4000 characters), optional `model` (1–128 characters), `max_chars`
(2000–64000, default 32000), and `timeout_s` (1–3600, default 300).

Executable paths, credentials, tools, working directories, repository paths and
external MCP configuration cannot be supplied by this action. The engine chooses
its private runtime root beside its journal. The native process uses its own
empty working directory and frozen PM evidence only. This is a context analysis
adapter, not a repository-reading or coding adapter.

An atomic local effect creates the immutable run, context and prompt digests,
original requester binding, stable PM execution controller and generation writer.
A new human message can be added while the run is in progress; it is retained for
the next turn and cannot rewrite this run's context. Source identities, original
wording, unresolved input statuses, phase profile and omitted-context coverage
remain attached to the snapshot.

## At most one native turn

The single external operation step owns one fresh runtime and thread. Runtime
preflight must prove the pinned protocol/version, read-only isolated
configuration, no external tools/MCP/skills, and a usable model identity before
any turn is sent. Authentication validity and model execution are separate from
installation detection; cheap capability reads cannot claim successful execution.

Before sending, the runtime callback persists the exact original process
identity (`pid`, Linux process start ticks, boot identity digest), native thread,
request ID, client message ID, prompt/context digests and preflight capability.
Caller membership and generation are checked immediately before this intent.
Raw native events are then saved unchanged with contiguous sequence numbers and
content digests in `pm_native_events`. Lifecycle facts and events are append-only;
replaying an identical fact is allowed, replacing an original fact is not.

The operation never reruns its external step. A lost acknowledgement is resolved
by reading its saved process facts and native evidence. Missing launch/process
identity cannot justify another process. Raw native events are not translated
into BAT events and never enter BAT's turn-end parser.

## Import and provenance

Import requires both confirmed original-process death and complete native turn
evidence. The native reducer must match the original request acknowledgement,
thread, turn, terminal original user input/client ID/digest, full terminal items,
and final assistant message. Tool calls, event gaps, partial replies, another
request, a changed prompt, or an unknown runtime assessment are insufficient.
The saved runtime assessment must equal a new reduction over the unchanged
journal events; process death alone never establishes a completed reply.

The assistant message and its provenance commit atomically with release of the
writer. Provenance includes the native turn/message/thread IDs, actual model,
context/prompt/config digests, original source message/input IDs, coverage, event
sequence references/digests and process exit evidence. The shared provenance
schema's `sdk_session_id` holds the native thread ID for compatibility; its
`source_refs.adapter` and `native_thread_id` explicitly identify native Codex.
No BAT session or event is synthesized. Retrieving an already imported internal
receipt is safe after authority changes; public reads still check membership.

## Cancellation, authority loss and restart

While the runtime is active, the operation checks cancellation and current
requester membership. Either condition cancels only its own live task; the
runtime closes its own process and confirms exit before the writer can be
released. An unknown exit leaves the original operation unresolved and the
writer occupied. A known failure before this instance attempted process launch
is recorded separately and can release the writer without inventing a process.

On service restart, recovery only inspects the exact saved process identity. An
absent original PID, changed boot, reused PID start ticks, or exited/zombie
original process can prove death. An alive or unreadable identity remains
unresolved. Recovery never signals a PID and never launches or sends again.
When death is proven but completion is not, the run becomes aborted with no
assistant reply. Later cancellation or lost membership also forbids import,
even when a complete native reply was obtained before the acknowledgement was
lost. Cancellation flags are never cleared to recover or retry a turn.

A saved complete original reply may be imported on recovery only while the
original caller still has write membership and has not cancelled. A fresh
explicit operation after an aborted run gets the next generation; it cannot
resume or impersonate the previous native process.

## Verification

Durable integration tests use real disk-backed SQLite and a fake runtime, with
no model calls. They cover exact source import, Idea/Planning preservation,
lost acknowledgements, crash/restart, live cancellation, membership loss,
parallel writers, process identity readback and immutable native evidence.
Runtime protocol/isolation tests are separate. Neither set constitutes real
model execution or supported-platform acceptance.
