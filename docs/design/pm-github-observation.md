# Bounded GitHub workload observations

This W47 source implements the explicitly selected
`github_open_prs_and_actions_v1` profile. It reads GitHub and records evidence;
it does not schedule, repair, publish, merge, deploy or change the lifecycle.

## Explicit scope adoption

A new maintenance policy may add
`observation.github_profile: github_open_prs_and_actions_v1`. Its immutable
`scope.github_scope` records that exact selection. Omitting the field preserves
the earlier `github_scope: repository` contract, which remains incomplete:
observing this smaller profile cannot silently satisfy repository-wide coverage.

The selected profile enumerates open PRs, their fixed head SHAs, all check suites
and each suite's check runs (`filter=all`), commit statuses for those SHAs, and
GitHub Actions runs in `in_progress`, `queued`, `requested`, `waiting` and `pending`.
Checks use individual suites to avoid the commit-check endpoint's suite selection
limit. It includes fork PR head identities through the bound base repository.
Missing provider support remains incomplete; there is no fallback to another repo.

It excludes issues, third-party CI outside those PR heads, completed Actions
outside those heads, unpublished/local work and provider changes between reads.
Two equal traversals are bounded observation evidence, not an atomic provider
snapshot or proof that no transient change happened between requests. See the
[GitHub check run contract](https://docs.github.com/en/rest/checks/runs) and
[workflow run contract](https://docs.github.com/en/rest/actions/workflow-runs).

## Operation and original receipts

`pm.github.observe` requires `manage` and writable workspace membership. It uses
`target: {workspace_id}`, `params: {policy_id}` and the same preconditions as
`pm.workload.capture`: `expected_policy_version`, `expected_baseline_id` and
`expected_lifecycle_revision`. No caller supplies completeness, remote rows,
a repository URL or alternative scope. Admission freezes the current policy,
compatible release baseline, provider configuration digest and lifecycle epoch.

The observer verifies repository native ID and full name before and after each
traversal. Each GET and final journal transaction recheck original authority,
source visibility, current provider binding, policy, baseline and lifecycle.
Explicit reads while paused/closed use the current revision; a lifecycle change
during traversal prevents another request or publication of a changed-context
observation. Reads acquire no foreign session or checkout authority.

Successful original page steps retain exact request digests, response byte hashes,
received time, count, continuation and narrow projected identity/status data.
Their copies in `pm_github_observation_pages` are immutable. Provider prose,
URLs, token values, logs and authors are not retained. All step and derived-result
reads are gated by the original policy/baseline source visibility.

Same-key recovery reuses the original pages and final local receipt. A process
interruption after a GET but before saving its response leaves an explicit
`original_page_receipt_unavailable` gap; recovery never invents the original
response by making a later GET. A new explicit operation key requests a new
observation. Cancellation never deletes a recorded observation.

## Bounds and conservative failure

One operation allows at most 128 pages, 4096 projected rows, 8 MB of response
bytes and 60 seconds from its original start. Each GET is limited to 512 KB and
30 seconds within the remaining deadline. The opt-in GitHub client uses bounded
reads, resolves current credentials, refuses redirects, preserves Link metadata
for lists and objects, and rejects incomplete declared response bodies. Existing
delivery client's response contract is unchanged.

Link URLs must match the original provider, endpoint and filters and advance by
one page. The observer generates each subsequent request itself. Duplicates,
missing counts/pages, unknown statuses, changed head/suite/repository identity,
permission failures, rate limits, body/time/row/page limits and differences
between traversals yield incomplete evidence. Filtered Actions counts at 1000
or more are incomplete because the provider search has a result ceiling.

Known activity still blocks idle even when another portion is incomplete. An
incomplete observation is a successful recording operation, with its reason;
authority/context loss fails the operation and publishes no observation. Neither
case is repaired by a new request under the old key.

## Immutable reads and W45 consumption

Internal entrypoints are `install`, `capabilities`, `get_observation`,
`current_observation`, `list_observations` and `operation_visible`. IDs use `pmgh_`.
Detail returns `{workspace_id, observation}`; current adds `visibility_filtered`.
List returns `{workspace_id, observations, next_cursor, visibility_filtered}`.
Whole spaced UTF-8 JSON responses are capped at 2 MB. Membership, original source
checks and pagination run in one journal read snapshot. DDL is idempotent and
allocates no migration data step or `user_version`.

The shared API exposes these reads through
`/pm/workspaces/{workspace_id}/github-observations`, `/current` and `/{id}` and
RPC/MCP `pm_github_observations_list`, `pm_github_observation_current_get`,
`pm_github_observation_get`. Operation/event privacy hooks apply the same original
sources. Tauri permits only these exact GET paths and list cursor/limit queries;
detail/current reject all query selectors. List cursors preserve signed 64-bit
sequence values. `features.pm_github_observation` advertises the exact profile and
bounds, never full GitHub repository coverage.

W45 reads the newest saved observation in its local journal transaction. It
requires the same original intent, scope and unexpired observation; a later
incomplete, hidden or changed-context record never falls back to an older idle
one. Source withdrawal hides the derived workload too. Open PRs and nonterminal
Actions are development blockers. Complete empty evidence satisfies only this
explicitly selected GitHub profile; BAT, tasks, Sprint, central operations and
stability gates still apply. This is workload evidence, not automatic maintenance
authorization or Definition of Done acceptance.

The asynchronous caller deadline also covers DNS and response headers. One
bounded read worker at most may remain outstanding per configured client after
that deadline; its slot remains occupied until it exits. Later reads fail
incomplete while it finishes, and its late response is discarded. This bounds
worker accumulation when a provider or resolver stalls before returning headers.
