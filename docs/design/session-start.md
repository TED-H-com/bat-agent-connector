# Durable standalone session start

`session.start` is the standalone R01 action. It uses the existing central
`OperationService`, `start` scope, registry start claim/cap, resource policy and
confinement evidence. It neither creates nor adopts a task. Task submit, failover,
relay, fanout and merge retain their existing contracts.

## Request and identity

`POST /api/v1/operations`: action `session.start`, target `{host, workspace}`;
params `agent` (Claude/Codex), optional original `prompt`, `model`, `title`, and
`use_worktree` (default true). This slice refuses false at admission with
`START_WORKTREE_REQUIRED`: a new standalone SID does not prove exclusive ownership
of an existing task/manual/shared folder. Internal coordinator/checkpoint starts
keep their established authority. The legacy flag remains available to return this
explicit refusal; it never falls back to raw BAT. Unknown fields, caller session/task IDs, cwd overrides
and preconditions are refused. A canonical request needs an explicit literal
idempotency key. Replay checks caller scope and the original request hash before
resolving the workspace again. The compatibility RPC/MCP `session_start` and CLI
`start` retain `confirm=true`, host tier and independent no-key calls; they submit
the same action, never start another daemon or fall back to direct BAT writes.

Read-only `workspaces_list` / `GET /api/v1/workspaces` require `observe`, return a
bounded configured-host listing, and expose no write authority. Workspace selection
accepts an exact ID/name or one unambiguous legacy substring. The first durable
resolution fixes the full workspace ID/folder, Git root/branch/full commit, generated
session ID and start options. Recovery never reselects by name. The original prompt
is stored unchanged; no planner-generated replacement is inserted.

## Effects and recovery

Each external effect has a separate intent/receipt: `worktree.create`,
`session.start`, optional `workspace.register`, and optional `send`. Local registry
reservation/confirmation is recorded separately and bound to the operation ID.
Before each new frame, recheck cancellation, configured write/orchestration tier,
fixed policy, absence of task ownership and exact registry/workspace/Git binding.
The client identity-read path is used while a write owns its semaphore, including
when max_in_flight is one. Existing manual-root/shared-clone policy still applies;
`use_worktree=false` remains refused until a reviewed shared-folder authority contract exists.

BAT worktree creation uses its existing branch selection contract. The fixed source
branch/commit is checked before creation and the created worktree HEAD must equal
that commit before starting. Drift fails closed; it never silently starts another
revision. External BAT/GUI/Git clients are not governed by Connector's lock; the
read/check-to-frame race is not an atomic cross-client transaction.

A persisted transport fence distinguishes positively unsent calls from unknown
outcomes. A lost worktree reply requires matching BAT worktree identity plus Git
read-back; missing evidence never authorizes another create. A sent start requires
exact session/cwd/confinement read-back; it is never resent. A known created carrier
is retained after positively unsent failure or cancellation. Creation ownership
alone does not prove that no other session, new commit or uncommitted result now
uses it, so this action never removes worktrees or branches. The original durable
source/reservation/creation receipts remain available for canonical reviewed cleanup;
retention itself grants no cleanup authority. Cleanup projects these positive receipts with
the original registry resource ID even if the failed local row is missing. A changed
registry incarnation remains blocked. Missing managed-clone markers, live consumers
and new committed or uncommitted results retain their existing cleanup protections. External consumers remain subject to
cleanup's existing fresh dependency, content and final-frame checks.

Completed receipts replay without new write authority. Local registry projections compare the original operation/incarnation and owner under the registry flock; they do not overwrite a later binding. Positively unsent cancellation records a failed reservation and retains its known carrier for reviewed cleanup; unresolved external effects retain capacity.

Tab registration remains append-only. On a host whose connection negotiated the host contract's workspace read and append it uses the host's revision-checked append; on every other host it keeps the whole-document save with the existing GUI save race limitation, where a detected loss of previous workspace identities stays an explicit sticky conflict that is never repaired by another save.
Lost tab ACK needs identity read-back through the route that carried the write: the exact terminal through `workspace:load`, or the host's registration comparison through `bac:workspace-read` (see [host contracts](host-contracts.md#tab-registration-through-the-host)); an uncertain tab blocks later
effects. A first prompt uses a fixed operation message ID and no automatic client
resume. Positive ACK, its saved operation-generated accepted-turn record, or the exact Claude user-message echo proves acceptance;
Codex metadata/text similarity does not. Unknown send never resends. A partial
projection retains `started=true` and `prompt_sent=null` when start is known and
the prompt is unproven. It never claims the whole start failed merely because a
later effect is unresolved.

B2 managed artifact lineage accepts this action only after successful completion,
matching operation-owned reservation/start identity, and a positive initial-send
receipt with its exact generated message ID. A prompt-free start is not execution
evidence. Acceptance still does not mark work done, merged or deployed.

## Validation layers

Focused tests use actual central HTTP/RPC admission, MockBat frames and temporary
Git repositories. Cover scope/key replay and no-key isolation; malformed/reserved
inputs; cap/manual/task/cleanup policy; per-frame owner/policy/workspace/commit drift;
max_in_flight=1; worktree/start/send lost ACK and restart; positive-unsent retention of new consumers and committed/uncommitted results;
tab identity; original prompt; partial projection; and B2 lineage. Native or live
host/provider acceptance is separate evidence, not implied by these fixtures.

Protocol shapes reuse the repository’s [BAT 3.2.12 source table](../ORCHESTRATE.md)
(`src-tauri/src/commands/worktree.rs`, `remote_server.rs`,
`node-sidecar/src/handlers/claude-session.mjs`) and
[pinned protocol/permission notes](../PROTOCOL.md). No new BAT channel is introduced.

## Shared start form

The Sessions page links to `#/start`. The shared browser/native form uses the existing
Dashboard panels, fields, colors and responsive spacing. Select a configured host,
then one exact workspace ID returned by `GET /api/v1/workspaces?host=…&limit=200`.
No first workspace is implicitly selected. A truncated listing is explicitly labeled;
failed, ambiguous or wrong-host discovery cannot enable a new start. Agent, optional
model/title and original instructions are explicit; the supported new-worktree option
is fixed, with no unsupported shared-folder checkbox.

The draft and operation envelope/key are scoped to the verified backend/principal.
The original text is not trimmed or rewritten. Storage must succeed before the first
POST. A lost reply freezes the original request for explicit same-key retry; replay
remains available when the host tier has changed, since central checks an accepted key
before new admission. Only this action's proven post-replay tier/worktree admission
refusals permit an explicit replacement draft. Generic authentication failures and
key conflicts preserve the original intent. Accepted reloads and event refreshes use
GET only. A response must match actor, key, full request and known operation ID; a
positive start result must also match its host and recorded session identity.

Start confirmation and initial-prompt acceptance are displayed separately. A known
start with an unproven prompt stays partial, links to step receipts and cannot silently
open a replacement session. Event refresh joins a held first submission, then rereads
the accepted operation before advancing its checkpoint. A read failure retains the
original draft and blocks another submission until read-back succeeds.

Native adds only a fixed GET `/workspaces` route with bounded, non-duplicate host/limit
query fields, no body or operation key. It uses the existing verified credential and
central transport. Shared transport fixtures cover browser/native replay, malformed
receipts, principal/backend isolation, stale workspace responses and event ordering.
`npm run test:start` exercises the actual central API, operation service and MockBat:
original prompts, lost reply replay before changed tier, accepted reload, and an
unknown Codex send without resend. Temporary fixture evidence is not installed or
live-host acceptance.
