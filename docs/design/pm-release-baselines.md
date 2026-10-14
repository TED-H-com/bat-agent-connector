# Fixed release baselines (W44 / A50)

This slice selects an existing release or an already verified deployment as the
workspace's primary maintenance baseline. It implements the fixed-version portion
of v1.2 §13.8 and W44. Selection never publishes, dispatches work, writes a worktree,
adopts scope, starts a session, or enters Maintain. Maintenance policy, freshness
checks, work observation and the automatic transition remain separate work.

GitHub Release publication, package-registry publication, deployment and functional
acceptance are separate facts. A GitHub Release does not prove registry success.
There is no registry adapter in this slice: registry sources are rejected, and
`package_registry` remains `not_assessed`. Release notes, a tag alone, merged PR,
completed workflow, task checkbox, main's CI and local builds cannot substitute
for these receipts. Known limitations are explicitly owner claims. Every baseline
has `quality.status=unassessed` and `functional_acceptance_verified=false`.

## Original source and authority

The repository must be in the server's `github_config.repos` and in the current
repository list of a Connector project explicitly associated with this PM workspace
by `pm.project.bind`. A workspace's descriptive `repositories` strings alone grant
no access. The selected immutable `pmpb_…` association, its document digest, server
identity, fixed repository ID, provider/config digests and original source are
captured in private operation admission metadata. Public request target, params
and preconditions stay byte-equivalent as JSON values across receipt replay.

The caller needs `manage` and an actual current `owner` membership for selection;
an administrator without that membership does not acquire this direction decision.
Reads need `observe` and current workspace membership. Original operation replay
uses its original source association and current owner membership without adding
an `observe` scope requirement or rerunning admission/CAS/provider reads.

Every provider GET rechecks the original admission context and current caller
membership, project association, configuration and baseline CAS both before and
after the call. GitHub access uses only the configured client and constructed
relative repository paths, never URLs returned by the provider. It does not need
a managed BAT checkout and never touches the filesystem or user's worktree.
Provider URL, token/config references, transport names and paths are not included
in public source provenance; provider identity is a digest.

Historical visibility continues to check original project repository membership,
workspace identity and, for deployments, the original operation/resource visibility
through `pm_evidence._read_operation`. Removing the repository from its bound
project withdraws that source. A new baseline may quote the prior selection's
rationale, so its visibility preserves the previous baseline source chain, bounded
to 256 records. It never substitutes the currently visible latest source. Removing
network configuration prevents new observations; that is not deletion of a stored
historical publication fact. Configuration is not used as a substitute for source
ACLs.

## Supported evidence

### GitHub Release

Read `/repos/{owner}/{repo}/releases/{release_id}` by fixed positive native ID.
Require `draft=false`, an actual publication timestamp and the actual boolean
`prerelease` flag matching the explicitly selected `stable` or `prerelease` enum.
Resolve the release's exact tag through the Git reference and at most 16 immutable
annotated tag objects to a full commit SHA. Reject cycles, absent refs, unexpected
object types and a mismatch with `expected_commit_sha`. Verify the final Git commit
object. `target_commitish` is not used: it may name a branch and is not proof of
the existing tag's target. Re-read the exact tag and release metadata, then the
repository identity to detect changes during observation.

The stored version is `{kind:"tag", value:<exact tag>}`. The source proof retains
native release ID, publication time, flags, tag chain, resolved commit and observed
time. The proof is a point-in-time original publication observation, not a claim
that GitHub promises the tag or release can never change. Recovery of a successful
source-read receipt uses that original observation rather than silently rebinding
to a moved tag. No release body, asset URL or asset contents are imported.

Protocol references: [GitHub Releases](https://docs.github.com/en/rest/releases/releases#get-a-release),
[Git references](https://docs.github.com/en/rest/git/refs#get-a-reference), and
[annotated tag objects](https://docs.github.com/en/rest/git/tags#get-a-tag).

### Deployment

The existing deployment engine's `deployment.record` commits `verified=true`,
`provider_terminal=true` and its original `recorded_result` in the central journal.
Selection requires that receipt and checks its repository ID/name, original
provider, environment, generation, deployment identity and fixed product SHA.
The runtime evidence must say `version_checked=true` and identify the same
repository/environment/commit. A health-only successful deployment is insufficient.
If the original receipt claims a health check, the observed health must be true.

The channel is exactly the recorded environment; it cannot be relabeled stable or
production by the caller. The version is `{kind:"commit",value:<full SHA>}`, not an
invented package version. Receipt provenance includes original operation/deployment
IDs, receipt digest, runtime check flags, run IDs and artifact identity where the
engine recorded them. Workflow SHA may differ from product SHA. A historically
verified deployment can be selected after later deployments, but the result says
`current_environment_status=not_rechecked`; it does not assert that version is
currently live. A provider job alone or a legacy unverified deployment is rejected.

### Maintenance branch

`maintenance_branch=null` is valid and records `status=unconfigured`. Supplying
a branch requires its actual Git `refs/heads/…` object, full head SHA, and a fixed
comparison proving the baseline commit is the branch's ancestor or identical.
Read the branch again after comparison and reject movement. Store the observed
ref/head/relation separately from the fixed baseline commit. This association is
`verified_at_observation`, not authority to write to the branch and not proof of
future branch freshness. Do not infer `main` or force configuration merely to fill
a field.

## Action contract

`install(ops)` uses the existing `OperationService` and idempotent schema DDL without
allocating `user_version`. It registers exactly `pm.release_baseline.register`.

Target is exactly:

```json
{"workspace_id":"ws_<existing ID>","repository":"owner/repository"}
```

Parameters are exactly:

```json
{
  "project_binding_id":"pmpb_<32 hex>",
  "source":{"kind":"github_release","release_id":41},
  "channel":"stable",
  "maintenance_branch":null,
  "known_limitations":["Original hardware acceptance is still unassessed"],
  "rationale":"Maintain the version already released to users"
}
```

The alternative source is `{kind:"deployment",deployment_id:"dep_<32 hex>"}`.
Known limitations allow 0–40 nonempty strings of at most 2,000 characters; rationale
allows 1–4,000 characters. Branches and environment names are bounded to 256.
Unknown request fields, unsupported source kinds and malformed identifiers fail
before provider calls.

Preconditions are exactly:

```json
{
  "expected_baseline_version":0,
  "expected_previous_baseline_id":null,
  "expected_repository_id":4242,
  "expected_commit_sha":"<40 lowercase hex>"
}
```

The initial baseline version is zero/no selection. Every explicit replacement
adds one immutable document and one version, preserving `previous_baseline_id`.
The final journal transaction compares both head version and previous ID; two
simultaneous decisions cannot both replace the same head. The deterministic new
ID is `pmrb_` plus the original operation's 32 hex suffix. The action result contains
workspace/baseline IDs, version, previous ID, document digest and
`maintenance_authorized=false`.

`pm.baseline.sources` is a bounded, read-only external step. Unknown reads may be
repeated by reconciliation only as the original fixed request, after the ordinary
epoch/source/cancellation gates. `pm.baseline.record` is a transactional local
effect; it commits the immutable document, API event and result receipt together.
Pause/close/new epoch can prevent a new selection after observations were read.
An already successful local receipt is returned before authority/cancel/epoch
checks internally, including after process restart. Public visibility is evaluated
independently and can deny that same result. No uncertain publication is repaired
by publishing again: this action has no publication effect.
If an interrupted source read loses authority or is cancelled, the worker can
record that original read as stopped without repeating it. This only applies to
the named GET-only step: it does not produce a proof or baseline, and avoids
retaining an indefinite unknown external mutation where none was possible.

## Reads and integration hooks

- `capabilities()` returns supported source kinds/channels, one primary baseline,
  `package_registry=unsupported`, and `automatic_maintenance=false`.
- `current_baseline(ops, principal, workspace_id)` returns `head` (version and ID),
  `baseline`, `visibility_filtered`, and `maintenance_available=false`. A withdrawn
  head retains its safe version but hides its ID/document.
- `get_baseline(ops, principal, workspace_id, baseline_id)` returns one complete
  immutable baseline including `document`, digest, original operation, actor,
  sequence and creation time. Invalid/missing IDs are 404.
- `list_baselines(ops, principal, workspace_id, after=0, limit=20)` returns whole
  records, `next_cursor` and `visibility_filtered`. Limit is 1–100; cursor is a
  nonnegative signed-64-bit insertion sequence. Pages never use wall-clock ordering.
- `operation_visible(db, principal, operation)` validates the original persisted
  operation admission even before a baseline record exists. The integrator must
  use it for the action's shared operation read/replay feed and the
  `pm_release_baseline` / `pm.release_baseline.registered` event. No shared entrypoint
  is installed by this domain module.

All reads are bounded by 2,000,000 UTF-8 JSON bytes using the HTTP serialization's
ordinary spacing. List pages return a complete-record prefix. Filtered records
consume their original sequence; an entirely hidden page can still have a forward
cursor. A single mandatory record that cannot fit returns 413 rather than truncating
its proof or looping a cursor. Public reads do not mutate any state.
Head, membership and every record/source check in a public read or operation
visibility evaluation share one SQLite WAL read snapshot. An existing transaction
is reused. A withdrawal committed concurrently is visible on the next read;
the current response cannot mix the old head with a different authorization or
source snapshot. No provider calls occur while holding this read transaction.

The shared entries use:

| HTTP GET under `/pm/workspaces/{workspace_id}` | RPC / MCP |
| --- | --- |
| `/release-baselines?after=0&limit=20` | `pm_release_baselines_list` |
| `/release-baselines/current` | `pm_release_baseline_current_get` |
| `/release-baselines/{baseline_id}` | `pm_release_baseline_get` |

Detail/current routes accept no selectors. Empty, repeated or extra queries fail;
HTTP/native cursors are bounded to nine digits, matching other PM read routes.
Direct RPC rejects wrong types and integers outside signed 64-bit range. Shared
`POST /operations` is the only decision entry; its existing original-key and
waited-result source checks apply. MCP writes use the same central operation.
The Tauri bridge permits only these exact GET paths/queries, without an operation
key. Global `features.pm_workspaces.release_baselines` exposes `capabilities()`;
visibility does not grant selection or network access.

## Explicit source choices and preview

Users choose published sources; they do not type GitHub numeric IDs or full commit
hashes. The following read entries require `observe` and current source access:

| HTTP GET under the workspace | RPC / MCP | Query |
| --- | --- | --- |
| `/release-baseline-sources` | `pm_release_baseline_sources_list` | `after`, `limit` |
| `/release-baseline-sources/github-releases` | `pm_release_baseline_releases_list` | `project_binding_id`, `repository`, `page`, `limit` |
| `/release-baseline-sources/deployments` | `pm_release_baseline_deployments_list` | `project_binding_id`, `repository`, `after`, `limit` |
| `/release-baseline-preview` | `pm_release_baseline_preview` | `project_binding_id`, `repository`, `kind`, exactly one matching source ID, optional `maintenance_branch` |

The source catalog is local. It returns `bindings` with sequence, immutable project
binding ID, project ID/name and explicitly configured repository names. Deployment
choices are also local, with original receipt IDs, repository ID, actual environment
channel, fixed commit and commit-typed version. Unverified or inaccessible rows are
filtered while preserving the original insertion cursor. Neither read contacts BAT,
GitHub, an adapter or a model.

GitHub discovery is a separate explicit request, not something opening a workspace
starts. It reads the repository identity, one `releases` page, and the identity
again. Pages allow 1–20 records, use page numbers 1–10,000, never follow returned
provider URLs, and expose only published metadata (ID, tag, actual channel and
publication time). Drafts/malformed rows are omitted. A full page has `next_page`
even if every row is omitted; at the explicit page bound, coverage says
`page_bound_reached=true`. An empty final page is normal. Body text, URLs and assets
are not copied into the choices. Each network read rechecks original authorization
and binding both before and after the GET. All source reads retain the 2 MB limit.

Preview resolves one fixed native source, derives repository ID/full commit/actual
channel, verifies an optional short branch name, and includes the current baseline
CAS. It returns the original proof (including `selection`), a `registration`
operation envelope, `requires_input:["rationale"]` and
`maintenance_authorized=false`. Its parameters have empty rationale, empty known
limitations and the fixed original source fields. The UI asks for the actual owner
direction, keeps those fixed fields, and then uses normal operation submission.
Changing source or branch invalidates the old preview. Leaving the branch absent
means unconfigured; do not include an empty query or `refs/heads/` prefix.

For the same repository, a returning preview updates the mounted registration
form without detaching it. Owners may type their rationale while preview is pending;
the response preserves keyboard focus, text and the existing durable form. It does
not enable registration before the fixed evidence and current CAS match.

Preview does not save a receipt, create an operation/PM run, take a writer, start
maintenance, or authorize a later action. A reader may inspect it; selection still
requires the actual owner. Registration does not accept a caller-supplied proof or
preview authority token: it performs its original full fixed-source verification
and CAS. A moved tag after preview is rejected against that original SHA. The
current head and all prior source policies are rechecked during preview so it
cannot replace a hidden baseline with a convenient newly visible source.

## Verification mapping

The focused tests use the existing operation journal and a mock GET provider for
release/tag movement, draft/prerelease mismatch, repository identity changes,
unrelated branches, ownership loss, original source withdrawal, immutable history,
CAS, receipt recovery, original-key replay, UTF-8 read budgets and lifecycle epochs.
Deployment tests invoke the existing deployment engine against its local fake
GitHub and verifier, then select its real central receipt. They distinguish fixed
product identity from newer main, original environment from a chosen label,
health-only checks from fixed-version proof and historical delivery from current
runtime state. No live publication, real host or model turn is used. These provide
the source-selection portion of A50; package-registry failures and automatic
maintenance policy require their future adapters/contracts.
