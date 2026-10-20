# Durable native PM analysis

`pm.run.analyze` performs one bounded discussion turn for the current Idea or
Planning thread. It shares the workspace PM generation writer and discussion
history with package preparation and plan adoption. It does not adopt a plan,
confirm an input, create coding work, change phase, or take over any existing
session. It currently selects the fixed `codex-native-linux` runtime contract;
[the native runtime contract](pm-native-runtime.md) defines its actual isolation
and pinned protocol boundary.

## Availability reasons

`pmAvailability` projects existing global PM features/scopes and workspace or
installation analysis descriptors. Settings Agents, discussion replies and plan
proposal composers share this reason mapping. A missing reply element is never
connection evidence; Connector connection state remains owned by
`centralServiceStatus`.

Unsupported PM and missing View scope have separate explanations and next steps.
Failed capability reads retain the existing localized error presentation and
code. Unchecked means a check has not run; a completed read with no usable
descriptor is unknown, while an in-flight check is checking. Known installation
reasons reuse `eligibilityReasons`; an unexplained `authorized: false` stays a
generic refusal rather than an invented membership or credential diagnosis.

Settings keeps its existing descriptor set and any-installation ready predicate.
Ready is an installation hint, not model execution evidence or permission for a
specific request. This mapping does not choose a workspace route or change the
existing runtime, durable-request or final server admission gates. The choice
between an any-installation summary and a workspace-selected-route summary
remains the separate I01/C02 product decision.

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
thread, turn, original user input/client ID/digest, complete turn items,
and final assistant message. A native summary terminal may instead be matched
to the original complete user and assistant `item/completed` events: the fixed
input/client ID/digest must match, and its terminal assistant must exactly equal
the completed final assistant from that same turn. The evidence references retain
the user item, assistant item and terminal event sequences. Tool calls, event gaps, partial replies, another
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
Updating the reducer cannot reopen an already aborted run, replace its immutable
assessment, or change a failed operation to success. A new explicit operation
uses the updated contract while the original outcome remains historical evidence.

## Verification

Durable integration tests use real disk-backed SQLite and a fake runtime, with
no model calls. They cover exact source import, Idea/Planning preservation,
lost acknowledgements, crash/restart, live cancellation, membership loss,
parallel writers, process identity readback and immutable native evidence.
Runtime protocol/isolation tests are separate. Neither set constitutes real
model execution or supported-platform acceptance.


## Shared execution choice

The existing discussion analysis form offers an explicit choice between
`pm.run.analyze` on the central native runtime and registered
`pm.run.bat.analyze` on the selected BAT workspace. Both receive only the same
purpose, optional model and original thread-version precondition. BAT eligibility
requires the original prepared PM worktree binding; capability reads do not
prove sign-in, model execution, native completion or process death.

Each action retains its own original durable form storage, envelope and operation
key. Switching the displayed execution method never copies or rewrites another
method's draft or pending request. An unresolved request blocks fresh analysis
on the other method and remains available through the labelled selection. Known
operation IDs are read back, and unknown admission is checked with the same exact
original key even after eligibility or the current thread version changes. The
selection preference uses the existing principal/connection namespace. Source
read failure hides the discussion and clears the displayed BAT binding and run
facts while preserving original request recovery in its private draft storage.
