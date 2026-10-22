# GitHub to project board sync

A Connector-owned, one-way read integration over the existing projects, work items,
links, durable operations and PM workspace associations. GitHub receives GETs only.
This extends the journal-only work item contract at its source boundary; it does
not replace maintenance observations, issue mappings, execution or human approval.

## Actions and reads

All actions require `manage` and the ordinary operation idempotency key.
`project.github.import` and `project.github.configure` target `{project_id}` and
accept `{repository: "owner/repo", enabled?: boolean, include_closed?: boolean}`. `project.github.sync`
targets the same project with empty params. Each carries
`preconditions: {expected_revision}` (initially zero).

`pm.github.import` targets `{workspace_id}`, requires current workspace ownership,
and accepts `{repository, enabled?, include_closed?, project_id?: null | "prj_…"}`. An existing
project must already be explicitly associated. With no selection it creates a
central project and association, or resolves the single associated project for
that repository. Imports also associate the cards with this workspace using the
existing immutable association history. They never infer requirements, start
work, approve content or create BAT resources. A project sync is linked to at most
one workspace; other workspace associations and existing card bindings stay intact.
The admitted workspace lineage participates in the normal lifecycle gates.

`GET /projects/{id}` (including navigation) adds `github_sync`.
`GET /pm/workspaces/{id}/projects` adds the associated projects' `github_sync`
settings and a repository suggestion. Capability `board_github_sync.version=1`
gates both frontend entry points. No additional native transport route is needed.
CLI `operation submit` and MCP `operation_submit` use these same registered actions.

Suggestions parse existing project repositories and recorded workspace Git remotes
(HTTPS, SSH and SCP forms), plus the project's configured Task Service repository
URL when available. Ambiguous sources leave the field empty. Reads do not execute
Git in a person's checkout or guess a local path; projects without a recorded
remote need an editable `owner/repo` binding.

## Provider and schedule

The existing Connector token store wins; otherwise the Connector uses the local
`gh auth token --hostname github.com` credential for the public GitHub API, or
unauthenticated public reads. Custom API endpoints never borrow that CLI login;
they require their explicitly configured token store.
Credentials never reach UI, receipts, cache or logs. No redirects are followed.
GitHub REST API version follows existing configuration. The [repository issues
endpoint](https://docs.github.com/en/rest/issues/issues#list-repository-issues)
includes draft and ordinary PRs. Identity uses its stable **issue** node ID for both
issues and PRs, together with numeric repository identity. Repository metadata is
read before the issues traversal. Subsequent reads resolve `/repositories/{id}`;
a named repository's 301 may supply only a same-origin numeric repository ID.
The Connector constructs the fixed ID endpoint itself; it never follows a redirect
URL. Renames/transfers update the canonical display name/URL while matching existing
cards by issue node ID. Stable card keys, IDs and history survive those changes.
Changing to another repository does not adopt unrelated node identities.

First import creates only open items by default. `include_closed: true` explicitly
opts into closed history and persists for later syncs; turning it off never deletes
existing cards. Explicit primary links can still adopt already closed legacy cards,
with their human lanes preserved. Traversals read all states so tracked closures
and missing identities are distinguishable from an open-only response. New cards and associations put open
work first so the first page is actionable; existing order/history is preserved. Subsequent traversals use
`If-None-Match` and persist each page's ETag and normalized identity/state/title/URL.
Every cached page is revalidated, including pages after an unchanged first page;
pagination never follows a provider-supplied URL. Normal polling is five minutes
plus up to thirty seconds of jitter, in the single daemon owner, independently of
open clients. Manual import can run immediately except during error backoff.
Rate-limit reset/Retry-After and exponential error backoff are persisted; manual
sync respects them. This follows [GitHub's conditional request and rate-limit
recommendations](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).
A public-access note explains the smaller unauthenticated request allowance.

A traversal is bounded to 100 pages, 100 items per page, 2 MB per response and
120 seconds total caller wait. A single surviving read slot prevents unbounded
workers after timeout. Any failed/incomplete traversal applies **no partial cards
or cache**; the previous successful cards remain. Provider state can change during
pagination; this is eventual synchronization, not a transactional GitHub snapshot.
Items absent from a complete traversal retain their last source observation and
local state with `availability: missing`; the project reports `missing`, not
“up to date”. Reappearance clears that marker. Incomplete traversals change neither
cards nor cache. A transfer outside the bound repository is missing until that
source is explicitly imported; it is never inferred closed.

Enabled schedules retain the original principal and credential identity, not its
bearer. Revoked/expired API credentials, lost membership or lifecycle admission
stop automatic sync rather than borrowing a different principal. Configure/import
can explicitly authorize it again. Pending operations prevent another scheduled
import. Settings edits and imports recheck revision, source and membership after
provider I/O; the local transaction and operation receipt commit together. Each created project,
item and association uses its existing registered action and a real
child operation linked to the import receipt. Only these fixed, non-suspending
journal handlers run inside the batch transaction; provider I/O finishes before it.
A failed first import creates no project, project binding or enabled schedule.
Its repository/history/polling choices remain an unsent form draft, including
after reload; the original terminal read receipt remains in operation history.
Invalid source titles are rejected before mutation; other deterministic local
refusals roll back the batch. Existing schedules record a visible failure code,
backoff and a new sync generation so a terminal operation cannot trap the poller.
Authorization and lifecycle refusals disable scheduling with distinct statuses; other admission errors use
`sync_failed` with `last_error`. Config revision tracks configuration changes,
while sync generation tracks polling intents. Ordinary polls do not invalidate
settings drafts. Source metadata updates emit source events without changing the
local work version, content fingerprint or update time, preserving start admission.
Cancellation is checked after waiting for the import slot and again before local
mutation. A canceled read cannot create cards or enable a new schedule; completed
receipts remain recoverable. Canceled scheduled intents retain their original key;
manual sync or saving settings explicitly admits a new intent.

## Cards and migration

Mapping: open issue → `todo`; open PR → `doing`, with a retained draft marker.
Merged PRs use `github_done`; completed issues use `github_completed`;
other closures (unmerged PRs, not-planned, duplicate and unknown reasons) use
`github_closed` (Closed, not completed). These source lanes never mutate local
work state, completion claims, unfinished steps or human approval. Completed
issues emit source events with `source: github`; delivery evidence still comes
only from delivery lineage. Initial titles are copied; later edits are preserved.

Human moves persist, including moving away and back between polls. A subsequent
source closure can set the applicable source lane, while reopening never revokes
human approval. First adoption always preserves the human lane, even for closed
sources. Archived cards are neither restored nor moved. An explicit human move
clears the source lane override.

An additive nullable `work_items.github_external` stores repository, number, issue
node ID, stable key, URL, source state and move bookkeeping. A unique expression
index enforces project/repository/number identity. A new idempotent settings table
stores schedule, configuration revision, sync generation, history opt-in, failure code, authority and bounded page cache. A nonunique project/node index supports rename matching without rewriting pre-existing identities. No `user_version`,
data step, historical copy, ID replacement, item deletion or Hub import is involved.
Existing journals remain valid; legacy items have no external key until import.

Legacy adoption accepts exactly one active `github_source` primary link whose
whole ref is a canonical `https://github.com/owner/repo/issues/N` or `/pull/N` URL.
Fragments, queries and trailing prose are rejected. The registered `work_item.link`
action creates/removes this explicit link kind; the existing UI offers it in both
locales. Titles, descriptions, acceptance, notes and `pull_request` delivery links
never establish source identity. Multiple candidates or multiple primary links
are conflicts; no duplicate is created as a workaround. The 96-card fixture now
records explicit primary links: the original prose-only snapshot is intentionally
ineligible for automatic adoption under this stricter contract.

Automatic workspace bindings reuse `pm.work.bind` child receipts and are labelled
`association_basis: github_import`, derived from the parent operation reference (or `unknown` if its original receipt is unavailable).
They never claim `explicit_owner_association`. Unscoped imported source-only cards
are outside the Overview's planned-work scan; local edits, Work start intents and explicit plan associations remain
subject to its normal bounded coverage. Closed history begins in local `todo`,
so it supplies no started-work or completion-claim WIP evidence. Source lanes
supply no cycle time, delivery acceptance or requirement completion.

Every PM reader uses the same display-only source lane. A subsequent closure of
locally `doing`/`waiting` work leaves that local record intact but removes it from
WIP, active age and waiting metrics. Overview excludes it from active requirement
progress; without other work or acceptance evidence the requirement remains
`not_started`. The schedule returns `unknown` with the GitHub observation in refs
and a source label, never an invented delivery date or completion. Independently
recorded delivery lineage and session attempts remain visible. Fixed evidence
captures save the source observation; brief exports use that saved lane in both
locales, without joining current GitHub state. MCP lists, project counts,
navigation and the Work view expose `display_state`/`source_lane` separately from
the unchanged local `state` and `completion`; list state filters use the display
lane. Genuine local completion claims, human approval and archives take priority
over a source lane. Runtime activity remains separate in the Work view.

The shared UI provides editable binding, import/sync, persistent opt-in (on by
default for the first import), save settings and plain English/zh-TW status for no
repository, not imported, no open issues, unreachable, rate limited, authorization
conflicts, missing sources, invalid source data and local/scheduler failures.
Closed history is a separate unchecked option. Existing durable form drafts and original operation keys are reused.

## Decided product choices (owner-confirmed 2026-10-22)

- Create open items only by default; closed history requires explicit opt-in.
- Use “Closed (not completed) / 已關閉（未完成）” for unmerged PRs and every
  issue closure except explicit `completed`, including unknown reasons.
- Use “Completed · GitHub / 完成議題 · GitHub” for completed issues, distinct
  from merged PR “Done · GitHub / 完成 · GitHub” and human content approval.
- Preserve all local work states and approvals during synchronization; use source
  lanes only. Adoption never changes a human lane, including closed imports.
- Require one explicit primary source link; do not guess identity from prose or
  delivery links. Do not automatically repair pre-existing human completion claims.
- Exclude unscoped automatic source-only bindings from planned-work progress.
  Actual local work and plan associations retain the existing PM contracts.
- Treat source closure as inactive for WIP and active requirement progress;
  without independent acceptance, show no requirement completion. Keep schedule
  delivery status unknown unless delivery lineage supports it; display the source
  lane alongside independent delivery status when available.
- Keep genuine local claims, approval and archived status ahead of the source
  display lane. Save source observations in new evidence captures without
  rewriting older captures or briefs.

This increment covers the GitHub source semantics within E04/E09 and the existing
A01–A40 backlog. It changes no R0–R5 acceptance claim and does not complete E00–E11
or the remaining v1.2 lifecycle work. Real BAT/model, native and physical-mobile
acceptance remain separate from these fixtures.

Draft is an independent source marker: a closed Draft PR shows both its closure
and Draft label in Board and Work. A missing observation continues to show only
the missing marker instead of implying its previous source state is current.
