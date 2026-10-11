# Explicit Roadmap baselines, forecasts and observed results

This is the W13 scope and milestone foundation from [PM v1.2](../product/pm-v1.2-plan.md)
sections 8–11. It uses [adopted plans](pm-plans.md), [explicit work bindings](pm-evidence.md)
and [fixed project snapshots](pm-snapshots.md). No plan input is automatically
converted into a milestone, and no new work, session, delivery or remote request
is started here.

The central journal holds three distinct current references: an owner-selected
scheduling baseline, an explicit forecast revision, and an observation of existing
fixed facts. Changing one does not silently rewrite the others' history.

## Scheduling baseline

`pm.roadmap.baseline.set` uses the registered `OperationService`:

```json
{
  "action": "pm.roadmap.baseline.set",
  "target": {"workspace_id": "ws_<32 hex>"},
  "params": {
    "title": "Membership release",
    "rationale": "Separate login readiness from launch",
    "milestones": [{
      "milestone_id": "login-ready",
      "title": "Login readiness",
      "plan_revision_id": "pmrev_<32 hex>",
      "requirement_ids": ["member.login"],
      "work_binding_ids": ["pmwb_<32 hex>"],
      "baseline_date": null,
      "depends_on": []
    }]
  },
  "preconditions": {"expected_roadmap_version": 0, "expected_plan_version": 4},
  "idempotency_key": "original-request-key"
}
```

The actor needs `manage`, `observe`, and current workspace owner authority (or
the existing administrator override). The exact workspace is the entire target.
Admission and execution both recheck membership and the two integer versions.
Titles are nonempty and bounded at 200 characters; the optional Roadmap title
defaults to `Roadmap`. Rationale is required and bounded at 4,000 characters.

Milestone IDs are explicit workspace-local keys: a lowercase initial letter,
followed by lowercase letters, digits, `_`, `.` or `-`, at most 80 characters.
Changing the title keeps that identity. The first baseline revision and original
baseline date are retained in an append-only identity record, including when the
original date was unknown. Removing and later reintroducing the same ID restores
that identity; its history is not a new milestone under an invented ID.

Each milestone selects an exact historically adopted plan in the same workspace.
Its requirement IDs must occur in that revision. Selecting an older adopted plan
is valid and does not imply scope drift. The plan digest and adoption scope
revision are frozen alongside the selection. A candidate draft cannot serve as
an adopted commitment. Later changes to the accepted-plan pointer never rebind a
milestone's requirements.

Work binding IDs are optional explicit references to immutable `pm_work_bindings`
rows. They must belong to this workspace and the same fixed plan. When a binding
selects requirements, it must overlap the milestone's selected requirements.
An empty requirement selection stays unknown. One milestone cannot select two
revisions of the same work, but two milestones can explicitly share work. Shared
work does not become two completed deliverables.

Dependencies must name distinct milestones in the submitted baseline; unknown,
self and cyclic dependencies are rejected. Dates must be real calendar dates in
strict `YYYY-MM-DD` form or `null` for unknown. A dependency does not infer or
promise a date. The whole list is an explicit replacement baseline, including an
empty list if the owner deliberately removes every current milestone.

The saved document contains `milestones`, `title`, `rationale`, and
`plan_head_at_creation`. Each milestone also records `plan_document_digest`,
`scope_revision`, `original_baseline_date` and `first_baseline_revision_id`.
`changes` lists added/removed/reintroduced milestone IDs and added/removed exact
`{plan_revision_id, requirement_id}` pairs. Those pairs describe selected scope
references; they are not a semantic change assessment or a completion denominator.

The result is `status: accepted_scheduling_baseline`, `execution_authorized: false`,
and `git_materialization: not_recorded`. This is an explicit journal scheduling
decision. It does not revise the accepted plan/spec or claim that `roadmap.yaml`
has been written to the PM Git worktree. Git materialization remains a separate
integration responsibility; the plan's existing verified Git adoption receipt is
not reused as proof that this new schedule was committed.

## Forecast revisions

`pm.roadmap.forecast.set` has target `{workspace_id}`, params
`{baseline_revision_id, rationale, updates: [{milestone_id, forecast_date}]}` and
precondition `{expected_roadmap_version}`. It has the same owner/scopes requirement
as baseline selection and must name the current baseline.

Updates require an explicit date or `null`. Each revision saves the complete
forecast map: unspecified entries preserve the prior forecast for this baseline,
or remain unknown when no forecast exists. There is no fallback from a baseline
date to a forecast date. A forecast neither changes baseline dates, requirements
or dependencies nor adopts a different plan. `updated_milestone_ids` and rationale
make the explicit change traceable.

A new baseline resets the current forecast and observation pointers; their
immutable revisions remain readable. A forecast update preserves the current
observation. The latter still names the forecast that applied when that observation
was recorded, so clients can identify that it predates the current forecast.

## Fixed-source observation

`pm.roadmap.observe` has target `{workspace_id}`, params
`{baseline_revision_id, snapshot_id}` and precondition `{expected_roadmap_version}`.
Contributor/owner authority plus both `manage` and `observe` is sufficient. It
does not set a date or adopt a scheduling commitment.

The operation records the current baseline and forecast references, the selected
DocumentSnapshot ID and digest, its `as_of` and journal cursor. `as_of` describes
snapshot assembly; the journal cursor is a central event cursor, not a remote
watermark. Every evidence summary retains its original observation/capture times.
No provider is contacted, no evidence capture is refreshed, and no latest work or
plan data substitutes for the selected snapshot.

For every selected milestone work binding, both the snapshot's work binding and
its fixed capture binding must equal that exact ID. The returned `work` entries
contain `work_binding_id`, `work_item_id`, `capture_id` and `match_status`:

| Match status | Meaning |
| --- | --- |
| `exact` | Snapshot and capture both name the selected immutable work binding |
| `missing_work` | The fixed snapshot does not include this work |
| `binding_mismatch` | The snapshot contains a different work-binding revision |
| `missing_capture` | The matching work has no saved capture in the snapshot |
| `capture_binding_mismatch` | The snapshot's capture was taken against a different binding |

`exact` establishes identity, not quality or completeness. A milestone with no
explicit work bindings has `coverage.work_mapping: unmapped`. Missing work may
also reflect the source snapshot's truncation; its original coverage is retained.

Each milestone has fixed selected `evidence_ids` and an `actual` projection:

```json
{
  "completion": "unassessed",
  "dod": "unassessed",
  "actual_finish_date": null,
  "facts": []
}
```

`facts` contains visible immutable evidence summaries: original IDs, source kind
and ID, digest, times, cursor, normalized `assessment`, and fixed SHA or deployment
identity `version_refs`. Full evidence remains available through its existing ID.
An observed test result retains `assessment.verification`; a work claim retains
`assessment.state`; operation success retains `assessment.status`. None becomes
DoD, milestone acceptance, deployment verification or an actual finish date.
`coverage.scope_assessment` also stays `unassessed`.

Every observation read rechecks the original snapshot's evidence visibility for
the current reader. Inaccessible facts and all their selected IDs and summaries
are removed; both milestone and document coverage disclose this filtering. No
creator authority is inherited. Restoring access can reveal only facts selected
in this original observation, never newly collected evidence.

The immutable `document_digest` identifies the stored selection. An observation's
`view_digest` identifies its currently visible projection and changes when access
changes. Lists return metadata without facts. Fixed accepted-plan and milestone
metadata follow current membership of their own workspace.

## History, bounds and recovery

The module adds idempotent schema without allocating `user_version`:
`pm_roadmap_heads`, immutable `pm_roadmap_revisions`, immutable first-identity
`pm_roadmap_milestones`, and immutable `pm_roadmap_applied` receipts. An append and
its pointer update, receipt and event commit in one journal transaction.

Every action increments the workspace's Roadmap version. Revision IDs derive
from the original operation ID, with prefixes `pmrb_`, `pmrf_` and `pmro_` for
baseline, forecast and observation. Original-key replay or lost-response recovery
returns that same revision without new effects. Concurrent stale operations fail
before appending. Current membership is rechecked before new execution and replay.

History order uses insertion `sequence`, not wall-clock time. Each revision names
the prior current revision of its own kind. A newly selected baseline begins a
new forecast/observation chain; earlier chains remain accessible in history.

A baseline contains at most 100 milestones and 500 work-binding references.
The canonical stored document is limited to 1,000,000 actual UTF-8 bytes. An
observation contains at most 1,000 milestone-to-fact references, and its projected
document is bounded at 2,000,000 UTF-8 bytes. Bounds retain complete facts in a
deterministic prefix and set `evidence_truncated` / `bytes_truncated`. All selected
milestone/work identities remain. If metadata alone cannot fit, creation fails
before appending. Source-snapshot truncation and unavailable-source coverage are
preserved, never recast as complete project coverage.

## Read functions and response shapes

All public domain reads require `observe` and current workspace membership.
Another workspace's revision is not found through this workspace. They return
`version: 1`, `workspace_id`, and the following fields:

- `get_roadmap(ops, principal, workspace_id)` returns `head`, `baseline`, `forecast`
  and `observation`, with each current revision or `null`. It does not inline all
  historical revisions.
- `get_revision(ops, principal, workspace_id, revision_id)` returns `revision`.
- `list_revisions(ops, principal, workspace_id, kind=None, after=0, limit=50)`
  returns `head`, metadata-only `revisions`, and `next_cursor`. Limit is an integer
  from 1 to 100; cursor is a nonnegative insertion sequence; optional kind is
  `baseline`, `forecast` or `observation`.

`head` has `workspace_id`, integer `version` (initially zero), and nullable
`baseline_revision_id`, `forecast_revision_id`, `observation_revision_id`.
A full revision has `sequence`, `revision_id`, `kind`, `workspace_id`,
`previous_revision_id`, `baseline_revision_id`, `created_by`, `created_at`,
`operation_id`, `document_digest` and `document`. Observations add `view_digest`.
Action results contain IDs, sequence, updated head, digest and
`execution_authorized: false`; they do not copy private evidence into the durable
operation response.

This increment does not evaluate semantic scope drift, date variance causes,
critical paths, DoD, delivery acceptance or forecast confidence. It does not
render SVG or generate narrative reports. Those later consumers must use these
same fixed baseline, forecast and evidence references rather than inventing a
second source of PM truth.

## Shared client entry points

HTTP reads are `/api/v1/pm/workspaces/{workspace_id}/roadmap`,
`/roadmap/revisions?kind=baseline|forecast|observation&after=0&limit=50`, and
`/roadmap/revisions/{revision_id}`. MCP/RPC expose `pm_roadmap_get`,
`pm_roadmap_revisions_list`, and `pm_roadmap_revision_get`. Every entry uses the
same current-membership domain read; native route/query validation is bounded.

The shared workspace home presents milestones, baseline dates, forecasts and
recorded-result coverage together. Baseline editing, forecasts, saved observations
and history expand in place. Unsaved drafts retain their original versions and
exact association IDs across reloads. Old associations remain explicitly removable.
A changed current baseline never replaces an uncertain request's saved body/key.
Choosing the current baseline is explicit; saved historical results are reread
under current source permissions. The UI does not mark milestones Done.
