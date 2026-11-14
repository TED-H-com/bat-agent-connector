# Orchestrate tier: parallel worktree sessions

Off by default. Enable per host with `writes = true` **and** `orchestrate = true`.

Every tool below only acts on sessions the connector started, in folders it owns; sessions created in BAT stay
read-only. Merges only target a main checkout inside a configured managed root. See
[design/resource-policy.md](design/resource-policy.md).

## What BAT 3.2.12 does (upstream source)

| Channel | Behaviour | Source |
|---|---|---|
| `worktree:create {sessionId, cwd, installPnpm}` | Host finds the git root of `cwd`, creates `<root>/.bat-worktrees/<8 hex>` on a new branch **`bat/worktree-<8 hex>`** (host-chosen, collision-proof; a client cannot pick the name), adds `/.bat-worktrees/` to `.git/info/exclude`, links untracked `.claude/` files, copies local env files, records the fork point in `branch.<name>.bat-fork-head`. State is kept in host memory keyed by `sessionId`. | `src-tauri/src/commands/worktree.rs` `create_worktree_native`, `allocate_worktree_slot` |
| `claude:start-session {sessionId, options}` | Codex when `options.agentPreset` is a codex preset (`codex-agent`, `codex-agent-worktree`), else Claude via the Node sidecar. For worktrees the GUI passes `cwd` = worktree folder plus `useWorktree:true, worktreePath, worktreeBranch`. The host registers the session for notifications. | `remote_server.rs` (`should_handle_codex`), `node-sidecar/src/handlers/claude-session.mjs` (`applyWorktreeOptions`) |
| `worktree:status {sessionId}` | `{diff, branchName, worktreePath, sourceBranch, merged, mergedKind}`; `mergedKind` is `ancestor`/`patch-equivalent` (merged), `ahead` (source is an ancestor: conflict-free), `diverged`, or `unknown` (no new commits). `null` if the host lost its in-memory state (e.g. restart) until `worktree:rehydrate`. | `worktree_status_native`, `compute_merged_kind` |
| `worktree:merge {sessionId, strategy}` | In the **main checkout**: refuses if dirty, `git checkout <source>` if needed, then `merge --no-ff --no-edit` (or cherry-pick). A conflicting merge is not aborted. | `merge_worktree_native` |
| `worktree:remove {sessionId, deleteBranch=true}` | `git worktree remove --force` (falls back to `rm -rf` + prune) and `git branch -D` when `deleteBranch`. Returns `success: true` **without removing anything** when the host has no record for the session; the connector rehydrates first and verifies. | `remove_worktree_native`, `force_remove_worktree` |
| `worktree:rehydrate {sessionId, cwd, worktreePath, branchName}` | Re-registers a worktree in host memory (also re-copies local env files into it). | `rehydrate_worktree_native` |
| `claude:cleanup-worktree` | Deletes the branch by default. **Not exposed.** | `remote_server.rs` |
| `claude:start-session` with `options.worktreePath` of an existing worktree (codex preset) | The host **reuses** that worktree for the new session (no new folder), keeps its branch, copies env files without overwriting. Used by failover. | `ensure_worktree_for_session_native` |
| `claude:stop-session {sessionId}` | Unloads the runtime session; the transcript stays and it can be resumed. | `remote_server.rs` |
| `claude:set-permission-mode`, `claude:set-codex-sandbox-mode`, `claude:set-codex-approval-policy` | Change a live session's permissions. A Claude query not launched with bypass cannot be raised mid-turn: the sidecar then closes the live query, ending the turn. Codex applies it via `thread/resume` for the next turn. | `claude-session.mjs`, `codex_app_server.rs` |

### Does a started session appear as a GUI tab?

No. BAT tabs are entries in the host's workspace document (`workspace:load`). The GUI creates the worktree, adds a
terminal entry and calls `workspace:save` with the **whole** document; the Claude/Codex session is started later when
the panel mounts. `claude:start-session` alone creates a running session with no tab, so neither the GUI nor
`workspace:load` show it.

The connector therefore:

1. tracks orchestrated sessions in a local registry (`~/.local/state/bat-agent-connector/orchestrated.json`); all
   read/write tools resolve them, `sessions_list` shows them with `orchestrated: true, has_tab: false`;
2. optionally (`orchestrate_register_tabs = true`) appends a tab using an append-only helper: load, add exactly one
   terminal (nothing else changes), re-load and abort if the document changed meanwhile, save, re-load and verify all
   previous workspaces/terminals are still present. BAT has no compare-and-swap, so a narrow race with a concurrent
   GUI save remains; see SECURITY.md. Connected GUI clients receive `workspace:reload` and show the new tab.

## Connector tools

| Tool | Tier | Guard rails |
|---|---|---|
| `worktree_status`, `session_worktree_status` | read | Only reads (`worktree:status`, `git:status/branch/getRoot`). |
| `session_start(host, workspace, agent, confirm, prompt?, model?, use_worktree=true, title?)` | orchestrate | A standalone durable start requires a new worktree; `use_worktree=false` is refused with `START_WORKTREE_REQUIRED`. A worktree in a human clone is allowed only while `shared_clone_worktrees = true`; the host-chosen worktree path must be under `.bat-worktrees`. `confirm`, per-host cap `orchestrate_max_sessions` (default 4) counted from the registry, hourly write cap, audit. Rolls back the fresh worktree if the session fails to start. Custom branch names are not supported by BAT 3.2.12. |
| `worktree_merge(host, session_id, confirm)` | orchestrate | Refuses (`DESTINATION_MANUAL`) unless the main checkout is inside a managed root. Merges only if `mergedKind == ahead`, session idle, worktree clean, main checkout clean **and already on the source branch** (so BAT never switches branches there). Otherwise returns the reason and changes nothing. Never forced. |
| `worktree_remove(host, session_id, confirm, delete_branch=false, allow_unmerged=false, discard_uncommitted=false)` | orchestrate | Disabled legacy action: after its existing tier and resource checks, it refuses with `LEGACY_WORKTREE_REMOVE_DISABLED`. Use `cleanup_preview` / `cleanup_apply` or `batc resource-cleanup preview` / `apply`. |
| `batc fanout PLAN.md [--start ...]` | CLI helper | Splits a markdown plan (`- [ ]` items, numbered items, `##` headings) into task prompts; `--start` needs `--confirm` and is capped by `max_start_per_call`. |

| `session_failover(...)` | orchestrate | Only for connector-managed sessions in a folder the connector owns, and only for sessions classified `quota_exhausted` (unless `force`), never while streaming; one successor per session and worktree (atomic registry reservation); `max_start_per_call` for `all_exhausted`; the old registry entry becomes `superseded` and is stopped only after the handoff is acknowledged. |
| `session_record_verification(...)` | central operate + host orchestrate | Records external command/exit-code/environment/log-reference testimony through [`session.record_verification`](design/session-verification.md). Exact HEAD and BAT-reported empty status are checked; BAT can also return empty status after a Git failure, so this is not trusted verifier proof. No command/log access or BAT mutation. Task-owned sources refuse. CLI: `batc record-verification --key …`; reuse the original key after reply loss. |
| `session_cleanup(...)` | read-only legacy evaluation | Reports the decisions below. Apply always returns `LEGACY_CLEANUP_DISABLED` (409); use `batc resource-cleanup`. The deprecated `auto_cleanup` host key still parses and never enables writes. |

## Quota failover

BAT has no "quota exhausted" field. The connector detects it from the limit text Claude writes into the transcript
("You've hit your … limit", "usage limit reached|<epoch>", "… resets <time>") and treats 429/overloaded messages
as transient. Only ambiguous cases are sent to Jev when it is configured. The Codex successor is started with the
`codex-agent-worktree` preset and the old `worktreePath`/`worktreeBranch` (same folder, same branch) or, for a
session in a managed root's main checkout, `codex-agent` in the same folder. A session created in BAT is never failed
over: its successor would write into a human folder. Its first message is a handoff prompt: original task, latest
instruction, recent output, git state (branch, dirty files, commits, diff stats) and the quota evidence.

## Legacy cleanup evaluation

`session_cleanup` evaluates orchestrated sessions (and failed-over Claude sessions) without merging, stopping, or removing them. Its decision labels describe the legacy evaluation only. Sessions created
in BAT and connector sessions in a human checkout are always `KEEP`: cleanup never stops, merges or removes them.

| Decision | When |
|---|---|
| `KEEP` | Streaming, waiting for a permission/answer, quota or transient limit, idle for less than `min_idle_s`, worktree shared by another active session. **Never stops a session that is mid-work.** |
| `CLEAN_ONLY` | Superseded by a failover successor whose handoff was acknowledged; archive-only successor (see below) that is idle, clean and verified; worktree already merged or removed; no new commits and no diff; main-checkout coding session with a passing verification record whose final output Jev confirms as finished. Reports eligibility; keeps the session, worktree and branch. |
| `MERGE_AND_CLEAN` | All hard gates pass: idle, worktree clean, `mergedKind == ahead` (conflict-free, via `worktree_merge` never-force semantics), main checkout clean and on the source branch, a passing `session_record_verification` record for the current commit, last test run not failed, deterministic risk checks clean (no credential-looking additions, no secrets/infra paths, no large deletions or huge diffs); **then** Jev must confirm the final output claims completion (≥ 0.8) and the diff is `safe_complete` (≥ 0.8). Reports the legacy eligibility result without acting. Reviewed cleanup uses delivery receipts, not this completion judgement. |
| `ESCALATE` | Merge destination is a human checkout (not a managed root), uncommitted changes, diverged branch, dirty main checkout, missing/stale/failed verification, failing tests, risk-check hit, Jev unavailable/unsure. Collected into one `escalation_summary` line per call. |

`ESCALATE_TO_TED` (the 0.2.0 name of `ESCALATE`) is still accepted as an alias by `normalize_decision`.

**Preserving superseded work.** When a session's work is superseded but its uncommitted changes should not be lost,
fail it over with `force`, `archive_only=true` and `instructions` such as "only commit everything on the current
branch with message …; do not build, test, push or merge". The Codex helper commits in the same worktree.
Use a reviewed resource-cleanup preview with `release_undelivered` to pin the commits, keep the branch and remove
the idle worktree. The result remains undelivered.

Evaluation decisions are appended to the audit log. Reviewed resource-cleanup writes permanent per-item receipts
and tombstones, preserves a retained ref before removal, and may CAS-delete a delivered local connector branch.
See [the cleanup design](design/cleanup.md). BAT's remote protocol has no push, tag or PR channel: merges stay in
the host's main checkout, and pushing or opening a PR is left to the host's own workflow.

## Suggested workflow

1. Read the plan; split it into independent tasks (`batc fanout PLAN.md` gives a first cut; edit it).
2. Start one worktree session per task (`session_start` / `batc fanout --start`), within the caps.
3. Monitor with `sessions_list` and `session_wait`; answer questions only when you are sure.
4. Review each branch: `session_worktree_status(include_diff=true)`, `session_read`.
5. Merge the clean ones with `worktree_merge` when the workspace is a managed clone; otherwise leave the branch for a
   pull request. For `diverged` branches ask the session to rebase first.
6. Preview with `batc resource-cleanup preview`, review every item and its retention reasons, then apply that
   exact preview with a stable key. The legacy `worktree_remove` action is disabled. Fanout stops the planner
   session and keeps its worktree for reviewed cleanup.
   Report receipts and retained content to the user.
