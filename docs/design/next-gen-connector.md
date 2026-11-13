# Next generation Hermes-BAT connector

Date: 2026-09-27. Source snapshots inspected read-only: connector `e72e5e4`, BAT
`5a61d43`, Goose `04ed836`, Buzz `b0d6fb8`. The copied static review is in
[`docs/research/2026-09-27-review.md`](../research/2026-09-27-review.md).
This document records decisions from source inspection; the review is evidence to
check, not a specification.

## What Stage 1 changes

The current connector remains the control surface. Existing MCP tools and `batc`
commands retain their names and arguments. New responses add `turn_phase`,
`turn_attribution`, and explicit `after.message_id`/`after.after_ms`; `after_ms`
remains a host timestamp cursor and `turn_marker` remains accepted by `after=`.

| Review finding | Verdict and Stage 1 action | Remaining limit |
| --- | --- | --- |
| 1. Turn marker | **Verified for Claude; fixed.** BAT's `node-sidecar/src/handlers/claude-send.mjs` `emitUserEcho` sets `id` to `clientMessageId`. The connector had matched the prompt's first 160 normalized characters and then tried to parse `batc-<uuid>` as a time. Exact echo ID now identifies Claude sends. A queued send has an accepted record and a conservative progress fence based on the previous turn count; unconfirmed old output is hidden. | **Refinement:** BAT's Codex router (`src-tauri/src/commands/claude.rs`, `codex_app_server.rs`) drops `clientMessageId` and emits a `user-<time>` ID. Codex keeps a labeled, best-effort timestamp fallback and connector-side automatic send retry is disabled for Codex because the host cannot deduplicate it. For Claude, if the queued turn crosses its boundary while the connector is disconnected, BAT's messages have no command/turn ID, so progress remains unconfirmed. |
| 2. Write ownership | **Verified; partially improved.** The previous lock was per event loop and global. It is now per host within an event loop, so unrelated hosts do not block each other. | A local lock cannot arbitrate other MCP processes, VMs, or GUI actions. Per-session locking would let a send race a same-host merge or workspace save, so it is not a safe blanket replacement. `isStreaming` is a snapshot. |
| 3. Failover reservation | **Verified; fixed locally.** `registry.reserve` now checks for an active/starting `failover_of` and reserves under the same `flock`; a competitor receives the existing successor. It also refuses a second active failover successor for the same worktree. Rollback restores only the predecessor linked to that reservation. Cleanup waits for the successor's handoff acknowledgement. | The registry is local to a state directory; separate machines without shared state still need Stage 2 ownership. A crash with a `starting` record or uncertain Codex handoff requires inspection/reconciliation rather than blindly spawning or resending. The old Claude session stays loaded when handoff acceptance is uncertain. |
| 4. Workspace append | **Verified; deferred to BAT.** `append_workspace_terminal` compares before save and verifies after, but `workspace:save` replaces the whole document. A GUI save between compare and write can lose GUI changes, as the test demonstrates. Tab registration remains opt-in. | A connector retry cannot repair an update it never observed. BAT needs a revision check or atomic append channel. |
| 5. Verification gate | **Verified; fixed for automatic cleanup.** The old `test_evidence` recognized a test-looking tool command without an exit code or commit binding; Jev `tests_ok` could replace a missing run. New `session_record_verification` records candidate HEAD, command, exit code, environment, log reference, actor, and time. The host must show that HEAD and a clean tree at record time and again at cleanup. A new commit or dirty tree invalidates it. Automatic merge and archive cleanup require a passing record. | BAT has no safe remote shell verification channel. The caller attests that the recorded command/log is real; Stage 2 should run or ingest a verifier with a durable log. Direct `worktree_merge` remains a separate explicit command for compatibility and requires its existing safety checks. |

The cleanup decision now exposes four separate booleans: `turn_finished`,
`agent_claims_complete`, `verified_candidate`, and `approved_for_merge`.
`BAT-STATUS: MILESTONE` and Jev can inform the claim stage, but neither creates
execution evidence. Approval for merge is set only when acting with `confirm=true`
and a fresh HEAD/clean-tree check succeeds. A textual tool run remains useful
diagnostic context, not proof. Already merged/no-change branches and removed
worktrees may be cleaned without inventing a test run; a superseded Claude
session is stopped only after its Codex handoff was acknowledged. `CLEAN_ONLY`
for a main-checkout coding session also needs verification; a read-only planner
is exempt because it has no candidate code.

Example: run tests in the candidate environment, retain a durable log, then
record its exact result before `batc cleanup HOST SESSION --apply --confirm`:

```sh
batc record-verification HOST SESSION --commit FULL_HEAD --command 'uv run pytest -q' \
  --exit-code 0 --environment 'host/worktree path; Python and dependency versions' \
  --log-ref 'ci/job/123/log' --confirm
```

## Why Hermes can appear to lose a session

Code-backed likely paths, not a diagnosis of the host that runs Hermes: (1) the
old `session_wait(after=batc-<uuid>)` raised a parse error even though BAT accepted
the Claude send; a Discord agent could then stop tracking the turn. (2) A
queued send's immediate user echo preceded the old turn's final output, which
the old `_progress_after` could mistake for the new answer. (3) headless
orchestrated sessions live in this connector's **local** `orchestrated.json`;
Hermes on another machine with a different state directory will not discover
those via `sessions_list`. (4) WebSocket events are ephemeral; the client
reconnects and rereads state, but a transient disconnect can lose an event.
The source does not prove which path occurred in Discord. No accessible local
connector audit log was found in the default state directory, and that host's
Hermes/BAT logs were unavailable here.

To confirm, correlate a single Discord thread/request ID with Hermes MCP tool
call and result IDs, `host`/`session_id`, `clientMessageId`, connector
`audit.jsonl` attempt/result timestamps, BAT sidecar `receive-send-message`
and `emit-user-echo` records, `agent:turn-end` and disconnect/reconnect times,
and registry state path/content. Capture whether the turn was queued, the
BAT `numTurns` sequence, the echoed ID, and whether Hermes received a parse
error or timeout. Avoid logging prompt bodies or auth tokens.

## Stage 2: one shared coordinator

Run one authoritative service for Hermes, `batc`, and the dashboard. Use a
transactional local database (SQLite WAL is adequate for one service host;
move to a proper shared database if multiple service replicas are required).
Persist **task**, **command**, **session mapping**, **ownership**, **event**, and
**verification** records. A task ID is the durable business identity; BAT
session IDs are replaceable execution mappings. Keep the person's original
request verbatim and relay interpretation separately attributed, as
`relay.py` already does.

The command journal writes intent and an idempotency key before dispatch.
Suggested states: `proposed → reserved → dispatching → accepted → running →
terminal`, plus `uncertain` and `reconciled`. The key is unique per task and
logical action, not merely a BAT message ID. After a timeout/disconnect, read
BAT's echo and runtime state, retry with the same `clientMessageId` only where
the host deduplicates it, and do not start a second failover. A command accepted
without a terminal event stays visible as uncertain. Persist the BAT event
cursor and reconciliation observations; event delivery alone is never the
ledger. Migrate the local registry with an explicit one-time import and keep
old tools as adapters to this service.

Ownership is `(task/worktree, owner, control_version, lease_expiry)` under a
transaction. Every mutating task command carries the expected version; takeover
increments it, records reason/actor, and rejects stale commands. The service
should serialize writes to a worktree and refuse a second active successor.
This protects cooperating connector clients, while BAT GUI writes still need
host enforcement. A read-only dashboard shows task state, command uncertainty,
session/failover links, ownership, blockers, worktree/branch, verification and
milestones. Leave full conversations in BAT. HTTP binds to loopback only until
there is explicit authentication, authorization by task/worktree, CSRF policy,
and audit attribution; the current generic MCP actor `mcp` is insufficient for
a network-facing write API.

## Stage 3: task-level MCP and optional stock Goose

Expose `task_submit`, `task_status`, `task_wait`, `task_events`,
`task_answer`, `task_cancel`, and `task_verify` for Hermes; return durable task
and command IDs so a Discord thread can resume after a process restart.
Status/permission decisions and known-turn waits are deterministic service
operations. A task-scoped Goose ACP process may interpret ambiguous output or
plan subwork, using narrowly scoped MCP tools (`task_read`, `task_report`,
`task_request_action`) and no `task_submit` recursion. Goose needs its own
provider/model configuration. BAT remains the owner of coding sessions;
Goose owns only its ACP session. Goose's ACP server accepts session MCP servers,
its `ActiveRunRegistry` rejects simultaneous prompt runs **per Goose session**,
and `AcpProvider::resume` exists in the inspected source. Verify that resume
works with the deployed Goose version/provider; none of these features fences
an external BAT worktree.

## Stage 4: safe takeover and proposed BAT upstream work

> 2026-11-13: the owner decided to implement these host-side contracts in the product's own
> BAT-compatible fork instead of proposing them upstream; see the
> [single-product direction](../product/suite-direction-2026-11-13.md). "This repository does
> not modify BAT" below records the earlier premise. Connector must still work against
> unmodified upstream BAT hosts with the limits described here.

Use owner-only admission before generating prompts, and handle stop/takeover
as deterministic commands. Keep automation in separate BAT sessions where
possible until BAT enforces control versions. Propose upstream BAT changes;
this repository does not modify BAT:

1. `workspace:add-terminal {profileId, terminal, expectedRevision?, idempotencyKey}`
   performed atomically in BAT; return the new revision or a conflict. A
   revision-checked `workspace:save` would also work.
2. Carry `clientMessageId` through Codex sends and echo it, expose a stable
   `turnId` on Claude/Codex messages and turn-end events, and query command
   status by ID. Include queued/accepted/running/terminal transitions.
3. Optionally enforce a per-worktree owner/control-version token on all remote
   mutating channels and GUI sends, with an explicit human takeover action.
   Without this, no external coordinator can eliminate the GUI race.
4. Return a durable acceptance record before a send can outlive its RPC, and
   make retries idempotent after host restart. Expose an atomic Git HEAD/tree
   read together with dirty state for verifier binding.

## Stage 5: compare execution paths

Run the same bounded task set through direct BAT control and task-scoped Goose
ACP, holding model, budget, worktree policy and verifier constant. Compare
completion quality, human interventions, duplicate commands, uncertain
acceptances, time to resume after disconnect, and cost. Retain direct control
unless Goose measurably improves interpretive tasks.

### Buzz source lesson and disagreement

`buzz-acp run --task` creates a fresh agent process/session and emits one
terminal record. `run_task.rs` validates bounded input and separates trusted
launch config (executable, credentials, permissions, cwd) from task prompt;
`isolated_execution.rs` enforces a deadline, handles cancellation, shuts down
the adapter, and reports completed/cancelled/deadline/failed. Borrow this
checklist for optional execution: trusted capabilities, bounded duration,
explicit cancellation, structured terminal reason, and a separate verifier.
Even `end_turn` or exit 0 is not acceptance; `max_tokens` and `refusal` can be
reported as completed terminal reasons. `taskId` is correlation, not a
deduplication ledger. Buzz's relay/workspace ownership and Postgres/Redis/MinIO
stack solve a larger problem than controlling existing BAT sessions. Do **not**
adopt full Buzz unless replacing the Discord/dashboard system is itself a goal.

### Test plan

Stage 1 tests cover the real Claude `batc-<uuid>` echo, identical prompts,
queued old output after a new echo, disconnect after acceptance, two simultaneous
processes reserving failover, a GUI save between compare and whole-document
save, and a commit change after a successful test. Stage 2 should add
crash/restart and cross-controller journal tests: disconnect before and after
host acceptance, duplicate idempotency keys, stale control versions, and a
lost event followed by state reconciliation. BAT upstream integration tests
should cover Codex exact echo/turn IDs and atomic tab append under a GUI save.
