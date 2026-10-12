# Original batch-turn completion and manual quiescence

This is the contract for the M4 joined-flow integration under development. It
does not claim installed BAT or live-model acceptance. The first supported
managed path is an explicitly selected W38 batch child. A standalone handoff
without an original explicit result-evidence selection remains unproven; a
matching newest review or branch is not a substitute.

## Original managed turn

Only the existing `repository.continue` first-send callback can capture an
origin. The actual saved batch child relation, frozen handoff, controller,
registry incarnation, authenticated host binding, confinement, native SDK,
input epoch and fresh replay cursor must agree. The private origin preserves
the original prompt digest and the adapter's registered normalization. Old
executions never acquire a retrospective first-send origin.

A private registered `pm.execution.runtime.observe` operation reads the same
original native turn. It uses the existing OperationService, one fixed original
key and a service-created child relation, without another execution queue.
Caller-supplied ownership or parent fields cannot create this observer. It sends
no input, stop, kill, resume or cleanup command.

Bounded contiguous native replay must prove the sole original input completed.
ACK, idle metadata, assistant prose, a successful verification or accepted
quality alone cannot complete it. A missing/truncated page, changed SDK/input
epoch, later or pending input, abort, changed confinement, changed registry
incarnation or unproven candidate retains the original reservation. The reader
may resume at its saved cursor; it never repairs an unknown input by resending.

The batch entry's explicitly recorded immutable result evidence supplies the
fixed candidate and its original proof. The registered condition remains
distinct: `fixed_revision_verification` is not semantic acceptance, while
`accepted_requirements_v1` requires the exact qualified review selected for that
original entry. Where delivery is selected, the original settled receipt must
cover that same candidate; active or uncertain derived effects remain blockers.
The final read rechecks the clean owned candidate and current native input state
after the last replay page. The saved original completion receipt replays before
new admission checks, preserving historical evidence after pause or withdrawal;
public reads remain gated by the full original source chain.

The completion fact means `runtime_status: original_turn_completed` and
`process_death_proven: false`. It cannot grant process ownership, assert that a
shared app-server exited, publish a release or upgrade verification into quality.
Workload exclusion additionally requires a current complete authenticated
inventory agreeing with the original SDK, input counter, registry incarnation,
BAT workspace/profile, host binding and source/freshness limits. The exact saved
scan ID and host/session timestamps must identify the same complete scan begun
after the completion receipt; whole-second display timestamps do not replace
that identity or introduce a freshness tolerance. Later input or a changed work commitment
invalidates current applicability without changing the historical receipt.

## Existing manual session

A separate explicit read-only observation selects an existing work-item/session
link in the same workspace. The link must predate this observation and remain
accessible; a path or session title cannot create that relation. The observation
records its original link and source identities, never a managed creation claim.
Before each new read and final record, the original controller must still have
observe scope and workspace writer membership. The authenticated current tab
catalogue must retain the original BAT workspace/profile relation. A downgrade
to reader prevents a new observation but does not alter an already saved,
source-authorized historical receipt.

The original bounded profile is one independently completed native input in a
currently authenticated session: `bat_input_state_v1`, exactly one received input,
zero pending inputs, the same SDK/input epoch, and complete contiguous replay
from sequence zero through the current cursor. The final metadata and replay
must still agree. Retained messages without original terminal replay, an initial
gap, multiple historical inputs under that original profile, ambiguous scope or missing native input identity
remain unknown. A later input or stale/incomplete inventory invalidates current
quiescence.

This fact is `runtime_status: current_single_input_quiescent`. It confers no
ownership, does not clear registry capacity, and cannot declare work or quality
accepted. The manual session/tab and its worktree remain untouched. Workload may
exclude only this positively observed current session activity, while evaluating
all other work, delivery, policy and source commitments independently.

New admissions may select the separate `pm_manual_serial_inputs_v1` private
origin when authenticated current inventory reports 2–64 received inputs. The
request still selects only the original explicit link and its opaque identity
digest; callers cannot supply a proof or provider identity. An old pending
operation without this frozen profile is never upgraded. Single-input origin,
completion bytes, digest and original-key recovery remain unchanged.

The serial reducer examines every event from replay sequence zero, within the
existing 2 MiB / 4096-event / 256-page bounds. Replay epoch and native input epoch
are distinct identities; neither may change. It requires strictly alternating
user inputs and unique native completed turns on the same SDK. Each turn must
have actual status evidence containing the original input epoch and the exact
received ordinal; pending counters greater than one are unsupported. Completed
pair count must equal authenticated `received`, with final `pending=0`. It
retains normal native terminal/message validation; a last assistant message,
last terminal or count equality alone is insufficient. Final empty delta, exact
metadata and original scope/current authority are rechecked before recording.

This is supported by the ordinary Rust Codex producer in BAT
[`c397fd7`](https://github.com/teddashh/better-agent-terminal/blob/c397fd7/src-tauri/src/codex_app_server.rs#L637):
metadata includes `inputState`; `send_message` emits user echo then metadata
(lines 5912–5920), and native completion emits metadata then the original
SDK/turn identity (lines 8314–8350). Tests reproduce this source order, including
the initial system announcement and optional worktree-information event; these
announcements grant no ownership. The local `local-<session>-<n>` turn fallback
is rejected by this profile. Claude SDK completed events lack the required
native turn ID and remain unsupported. Bare audited upstream `b7419892` does not
imply the later input-counter capability. No provider code or human session is
changed by this observer.

Received counters include refused, queued and injected attempts, not only
successful model turns. Missing or reused IDs, overlapping inputs, missing
counter status, changed ordinal/epoch, abort/error, noncontiguous or truncated
history and unaccounted input remain unknown. The producer's ten-minute bounded
replay retention means many long-lived manual sessions cannot satisfy this
profile. The observer neither resets those counters nor resumes/stops sessions.

The new public schema is `pm_manual_serial_inputs_quiescence_v1`, with status
`current_serial_inputs_quiescent`. It retains the explicit source IDs, counter,
cursor, event/origin/document digests and recorded timestamp. Only
`completed_turn_count` (2–64, equal to received) and `turns_digest` are added;
there is no singular `native_turn_id` or public `turns` array. Complete per-turn
pairings and replay bytes remain in private steps. Nested public counters and
cursors are projected by an explicit whitelist. Ownership remains
`manual_read_only`, work acceptance `not_evaluated`, process death unproven, and
no reservation is released. Later input invalidates current workload
applicability while preserving the immutable historical receipt.

## Public evidence and source withdrawal

Raw prompt, replay pages, metadata and diagnostics remain in original private
steps. Public projections explicitly allow only original IDs, digests, native
completion facts, bounded counters and observation/freshness timestamps. Unknown
nested fields cannot flow through a proof object.

Workload reasons distinguish `original_batch_attempt_completed` from
`manual_session_currently_quiescent`; neither changes an independent quality
assessment. A frozen snapshot keeps its original referenced observation and
values. Withdrawal of any original source hides the complete dependent
operation/event/snapshot contribution; a latest accessible source cannot replace
it. Public state must not retain counts or prose derived from withdrawn evidence.

## Required offline evidence

The joined fixture uses one TaskDaemon, workspace, accepted plan, original
working candidates and ledger from development through release and Maintain.
Disk Git and the configured verifier run for real; BAT/GitHub are bounded test
providers. It keeps the manual session and ended managed worktrees in inventory,
without deleting rows to manufacture idleness or creating synthetic Tasks.

Focused negatives cover original ACK loss, missing terminal, gap, later/queued
input, identity/confinement/candidate change, source/credential withdrawal,
unknown derived effects and pause/reopen. Public sentinel tests cover operation,
event, workload and Kanban projections. Existing repair-runtime completion keeps
its original receipt semantics and does not acquire new ownership assumptions.

## Registered selectors and existing views

The private managed action is not a user action. The service creates its one
original key during the first-send capture. Its observer may read original
completion after investment is paused; a current observe credential and the
original controller/source checks still apply. A successful dispatch by itself
never produces a completion receipt.

`pm.session.quiescence.observe` is an explicit read operation, with target
`{workspace_id, work_item_id}`, params `{link_id}`, and preconditions
`{expected_link_operation_id, expected_project_binding_id,
expected_session_identity_digest}`. The last value is an opaque server digest of
the observed host binding, BAT workspace/profile, SDK and native input identity.
It prevents a stale selector from silently following a reused session ID or a
later input. Admission requires observe scope and a current workspace writer;
there is no new session control, ownership or manage-scope delegation.

Existing Kanban detail advertises bounded
`execution.manual_observation_sources` containing those exact original selector
values plus host/session IDs and `available`/`reason`. A withdrawn source is
omitted. Supported availability reasons are
`manual_native_identity_unavailable`, `manual_multiple_inputs_unsupported`, and
`manual_native_turn_active`. The UI copies the returned opaque selector; it does
not derive IDs or accept a pasted proof. The advertised action catalog remains
the capability gate on older servers. The existing operation endpoint and
original durable key provide admission and result recovery.
For new serial observations, `available` means current non-stale native identity,
2–64 received inputs, zero pending and no active interaction allow an observation
attempt; it does not claim replay coverage or completed turns. Zero or more than
64 inputs keep `manual_multiple_inputs_unsupported`; missing identity or active
input keep their existing reasons. One input continues to select the old profile.

Each ordinary execution attempt may have `runtime_completion` containing only
`observation_operation_id`, `execution_operation_id`, `document_digest`,
`recorded_at`, `runtime_status`, and `process_death_proven`. It is a historical
original-turn fact; current workload applicability and independent quality are
not inferred from this display. Receipts expose an explicit schema projection,
including nested input counters and replay cursor; unknown private diagnostics
are not public fields.

The serial profile covers only the positively proven subset above. Queued,
interleaved, injected or incomplete retained histories remain a daily-workflow
limitation; this is not evidence that every ended manual session is covered.

Source lineage also contains authorization dependencies. During the exact
synchronous `pm.repair.reserve` callback, the service may disregard that same
parent's pending local reservation when rechecking completion of its original
baseline execution. It must still have no reservation, child or other step;
the sole started step must be registered as local with no result or external
reference. The private scope ends before the reservation is inserted. Ordinary
workload reads, other parents and unknown or external effects keep their usual
blocking behavior. Current authority, lifecycle, complete source and workload
checks still run in the original reservation transaction.
