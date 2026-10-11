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
