# GitHub to project board sync

A Connector-owned, one-way read integration over the existing projects, work items,
links, durable operations and PM workspace associations. GitHub receives GETs only.
This extends the journal-only work item contract at its source boundary; it does
not replace maintenance observations, issue mappings, execution or human approval.

## Actions and reads

All actions require `manage` and the ordinary operation idempotency key.
`project.github.import` and `project.github.configure` target `{project_id}` and
accept `{repository: "owner/repo", enabled?: boolean}`. `project.github.sync`
targets the same project with empty params. Each carries
`preconditions: {expected_revision}` (initially zero).

`pm.github.import` targets `{workspace_id}`, requires current workspace ownership,
and accepts `{repository, enabled?, project_id?: null | "prj_…"}`. An existing
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
issues and PRs, together with lowercased repository and issue number.

First import includes open and closed history. New cards and associations put open
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
Disappearing/transferred/inaccessible issues are retained, never inferred closed.
Renamed repositories require editing the binding; old cards retain original keys.

Enabled schedules retain the original principal and credential identity, not its
bearer. Revoked/expired API credentials, lost membership or lifecycle admission
stop automatic sync rather than borrowing a different principal. Configure/import
can explicitly authorize it again. Pending operations prevent another scheduled
import. Settings edits and imports recheck revision, source and membership after
provider I/O; the local transaction and operation receipt commit together. Each created project,
item, state change and association uses its existing registered action and a real
child operation linked to the import receipt. Only these fixed, non-suspending
journal handlers run inside the batch transaction; provider I/O finishes before it.
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
stores schedule, revision, authority and bounded page cache. No `user_version`,
data step, historical copy, ID replacement, item deletion or Hub import is involved.
Existing journals remain valid; legacy items have no external key until import.

Legacy adoption scans goal/request/acceptance/title and active link refs/notes for
canonical GitHub issue/PR URLs, plus existing `pull_request` refs. One unambiguous
card is adopted without replacing its ID, text, links or chosen lane. Ambiguous
legacy matches or changed node IDs are reported as conflicts; no new duplicate is
created to work around them. The 96-card snapshot fixture explicitly proves first
adoption and a second import with zero creates, adoptions or updates.

The shared UI provides editable binding, import/sync, persistent opt-in (on by
default for the first import), save settings and plain English/zh-TW status for no
repository, not imported, no open issues, unreachable, rate limited, authorization
and conflicts. Existing durable form drafts and original operation keys are reused.
