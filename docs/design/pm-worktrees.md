# Fixed PM worktrees and draft snapshots

This is the W07 bootstrap and service-written note path for [PM v1.2](../product/pm-v1.2-plan.md).
It does not establish a writable runtime adapter or adopt a project plan.

`pm_worktree.select` requires an existing configured repository/BAT host/workspace
binding, the original GitHub repository ID, a full commit SHA and the exact preview
digest. Repository strings in a PM workspace grant neither filesystem nor network
authority. The selected branch must still publish that SHA at first fetch. Later
runs retain the original pinned commit even after that branch moves.

The original operation persists the complete JSON binding before host effects.
Server and PM workspace identity derive one managed clone/worktree namespace;
existing Git helpers enforce its paths, marker identity and inode locks. The
markers distinguish the PM role, stable internal controller and original creation
operation. Existing unrelated directories, symlinks and manual checkouts are never
adopted. This path uses the existing SSH Git runner and adds no BAT write channel.

Each effect also checks the original requester, current writer generation, current
configured binding and BAT workspace folder under the existing locked-frame gate.
This is the same PM-owned controller across turns; the authenticated requester and
operation remain distinct. The helper never grants membership or starts a session.

Only `docs/pm/drafts/pmr_<32 hex>/{context,reply}.json` can be written. Files are
immutable drafts with workspace/run/operation identity, the repository ID/SHA,
source payload digest and original payload. A separate service manifest in the
owned clone's Git metadata records exact file digests. The fixed source revision's
tracked files must remain unchanged, and unknown untracked files are refused.
Existing plan/spec/OpenSpec locations remain intact. JSON content cannot change
paths, adopt requirements or authorize execution.

Directory-relative, no-follow filesystem access refuses symlinks, special files
and hard-linked targets. A tracked snapshot path is refused even if its content
matches. File publication is exclusive and cannot replace an existing destination.
Interrupted partial files retain the original intent and require reconciliation;
they are not deleted or overwritten to make retry succeed.

`pm.repository.fetch`, `pm.worktree.prepare` and `pm.snapshot.context/reply` use
existing durable operation steps. Lost replies read the original pinned resource
or exact snapshot digest. Missing or conflicting proof remains uncertain or needs
attention. Read-back does not repeat an external effect. None of these receipts is
proof of runtime sandbox enforcement, professional review or accepted-plan adoption.

## Immutable PM revision commits (W08)

`commit_revision` accepts the exact UTF-8 files and digests produced by the PM plan
service: `docs/pm/revisions/pmrev_<32 hex>/{plan.json,plan.md}`. The current adopted
plan pointer is separate. The expected parent comes from the latest verified PM
Git receipt, including a Git commit that has not yet been adopted; the first
parent is the original source SHA. A source branch moving does not rebase the PM
worktree or rewrite that selection.
The receipt order follows durable journal insertion order, so a wall-clock
correction cannot select an older parent.

The helper constructs Git blob/tree/commit bytes deterministically without using
the worktree's staging index. Only those two immutable revision files enter the
commit; unrelated draft snapshots remain untracked. The fixed operation time and
operation marker identify one commit. The existing clone's metadata stores its
original intent before effects, and a private operation ref pins that exact
commit. Branch advancement uses the expected-parent comparison. The proven clean
PM-owned index is then synchronized with the new tree; no human checkout is named.

The Git receipt includes workspace, revision, original operation, full commit and
tree SHA, document/file digests and a stable worktree binding ID. Read-back checks
those bytes, the operation ref and the complete chain of PM-only commits. Arbitrary
descendants of the source revision are not accepted merely because they share its
ancestry. The original code source SHA remains unchanged while the PM branch head
advances.

`pm.revision.commit` can recover after intent recording, object creation, file
publication, branch advancement or index synchronization. It resumes only the
original deterministic transaction after proving its original parent or exact own
commit, original or exact own index, scoped files and unchanged surrounding work.
A lost final reply returns the same commit receipt without another commit. An
unexpected branch, staged change, hidden index flag, code edit or document conflict
requires attention and is never reset to make recovery pass. Another operation
cannot take over a pending revision transaction.
Only a read-back proving that no original host intent exists permits a
zero-effect retry. A partial transaction retains its original unresolved step;
continuation requires the current writer, requester authority and cancellation
gate. Cancelling or revoking authority during a partial transaction preserves
that writer and requires reconciliation. A fully completed transaction can still
be read back and recorded after cancellation or authority loss, without making
another commit or adopting its plan.

A verified Git receipt is a delivery fact. The PM plan service separately checks
current decision authority and the accepted-plan comparison before changing the
journal's adopted pointer. A Git-only result remains recoverable evidence; this
helper never infers that the plan was adopted, dispatches work or publishes files.
