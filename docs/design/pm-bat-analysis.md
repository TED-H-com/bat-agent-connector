# Dedicated BAT PM analysis

`pm.run.bat.analyze` is a registered central operation for one frozen Idea or
Planning discussion. It accepts the same discussion target, purpose, optional
model, context bound, timeout and expected thread version as native analysis.
Prepare the workspace's configured PM worktree first. Its original repository,
host and BAT workspace selection are reused; a path or workspace label cannot
select another host or claim an existing human session.

The operation reserves the existing PM writer generation and workspace PM
controller. A trusted internal context reuses the ordinary durable standalone
start executor, including resource policy, new carrier, original creation ID,
incarnation, confinement, final-frame guards and sent-effect reconciliation.
Public `session.start` cannot choose this internal profile. The runtime receives
only frozen context and runs in its private empty directory. The Connector
continues to own PM documents, discussion imports and scope decisions.

The BAT prerequisite is the additive `codex-pm-linux-v1` contract on top of
audited upstream `b7419892fbc9946799b64cca24c2ec8c7fa15c42`, with pinned Codex
`0.160.0` / `a956835d020762cb2b570053af06f643a11c0ecc`. This contract requires a
dedicated isolated process, no inherited environment access, no MCP servers or
skills, read-only filesystem options, a fresh thread and a single native input.
The capability descriptor is configuration eligibility; actual attestation,
authentication and model execution remain separate per-run evidence. The
ordinary BAT profile's external-tool limitation remains unchanged.

Start and send have distinct durable receipts. The send requires an authenticated
fresh `claude:sync-session` cursor, original runtime/context IDs and a fixed
`clientMessageId`. That cursor, prompt digest and transport fence are persisted
before writing. A missing ACK never creates another process or input; recovery
reads the original native receipt and replay. Cancellation, source withdrawal,
membership changes and lifecycle stops block a new frame. Original read-back
does not select a newer context or adopt another native thread.

Assistant import requires contiguous original replay, exactly one original
input and completed turn, matching native terminal JSON digest/output digest,
and confirmed death of the original dedicated process. A native message ID in
BAT's replay and the underlying app-server item ID are distinct; both remain in
their original evidence. ACK, idle state and synthetic abort do not satisfy this
contract. The existing assistant provenance import is still the sole discussion
write path; analysis never adopts a plan or launches engineering work.

Failed preflight has a separate abort-only proof: the original persisted failed
record can positively prove no launch, or the original process identity can
prove death even before thread/attestation completion. Preparing records,
missing receipts and unknown process identity retain the writer. An abort-only
proof never authorizes sending or importing an assistant answer.

`claude:get-pm-runtime` is read-only. It preserves original binary/source and
process identities, native completion and actual exit evidence separately from
Connector's local native-CLI journal. Observation records are immutable and
bounded; no new schema migration number is allocated. Derived session reads
retain current original workspace access.

Offline integration tests use MockBat and the actual central operation service,
session-start engine and transport fences. They cover completed import, original
lost-ACK recovery, missing exit, replay gaps, unproven tools, final-frame
membership withdrawal and abort-only preflight receipts. These checks do not
claim a BAT installation, live model turn or hardware acceptance.
