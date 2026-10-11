# Fixed maintenance reports and explicit Review records (W49)

`pm.maintenance_review.prepare` creates one genuine `DocumentSnapshot` through
`pm_snapshots._build` and its central transactional insert helper. The snapshot's
immutable `maintenance` extension contains a deterministic maintenance report and
flat references to every original source. No Sprint, model, provider request,
repair, plan adoption or lifecycle transition is created by preparing a report.

## Report period and evidence

The caller explicitly selects a past half-open `[period_start, period_end)`
period, no longer than 31 days. The action requires the current plan version,
review revision, immutable active policy ID/version, baseline ID and lifecycle
revision. These and the optional previous review version/snapshot identity are
checked again in the final local effect. The report and its snapshot/receipt
commit atomically; replay returns the original receipt and never rebuilds it.

Saved GitHub observations, workload observations and maintenance checks are
selected by the period, retaining their original IDs and document digests.
Completed checks use `finished_at`; a still-pending check uses its immutable
admission context and is marked pending. Only the selected policy's exact scope
may support its conclusions. Each source type is bounded to 100 rows and 300 KB
of projected evidence; truncation, unavailable scopes and filtered sources are
visible coverage gaps. Source references have a separate 512-entry limit.

The report gives fixed publication/baseline identity, maintenance branch,
policy scope and bounds, sample counts and freshness, saved blockers, observed
changes, pending/reported follow-up context, cost/effort coverage and next focus.
Two complete samples can support `no_observed_changes` between their sampled
facts. A single empty sample cannot support that statement. Every report says
`whole_period_no_events_proven: false`; point samples cannot prove continuous
weekly absence. Removed identities are `no_longer_observed`, never resolved.
Different scopes cannot be compared. Newest incomplete evidence cannot be hidden
behind an older complete sample. `unchecked`, `stale`, `incomplete`, `disabled`
and `not_configured` remain separate statuses. Issues, dependencies, research,
quality and model analysis remain explicitly unobserved/unassessed/not requested
unless a later implementation supplies original evidence for them.

## Discussion, completion and follow-up

Actions under `pm.maintenance_review.` are:

- `prepare`: target `{workspace_id}`; params `{title, period_start, period_end,
  previous_review_id?}`; preconditions `{expected_plan_version,
  expected_review_revision, expected_policy_id, expected_policy_version,
  expected_baseline_id, expected_lifecycle_revision}`.
- `note`: target `{workspace_id, review_id}`; params `{content, rationale,
  source_refs?}`; precondition `{expected_version}`.
- `candidate.record`: the same target/precondition; params `{title, problem,
  goal_relation, expected_effect, options, hypotheses, source_refs, rationale,
  public_sources?}`. Original report source IDs are mandatory. Optional public
  sources contain HTTPS URL, title, checked-at timestamp and a claim attributed
  to the actor; the operation does not fetch or independently verify that claim.
- `complete`: params `{acknowledged_note_ids, discussion_acknowledged:true,
  discussion_summary, direction, direction_rationale, follow_ups,
  next_observation_focus, rationale, candidate_ids?}`. At least one actual saved
  discussion note is required. A current actual workspace owner acknowledges
  the notes and records the outcome; admin scope cannot impersonate ownership.
  Membership and original sources are rechecked immediately before recording.
- `follow_up.record`: params `{follow_up_id, status, result, rationale,
  snapshot_id?}`. Only its original assigned current writer or a current actual
  owner may report a result. Status is `open`, `blocked` or `reported_done`;
  verification stays unassessed. An optional full result Snapshot adds its exact
  original source references to this event's visibility binding.

Completion directions are `continue_maintenance`, `bounded_repair_proposal`,
`explore_idea`, `request_planning`, `revise_policy`, `pause_proposal`,
`close_proposal` and `defer`. These are discussion records only. Candidates remain
proposals, follow-ups are assignments rather than acceptance proof, and no scope
or lifecycle change is applied. A report without a completed discussion remains
prepared; the optional next report explicitly carries that pending context.

A review has immutable workspace revision and one fixed Snapshot. Its separate
mutable state has a CAS version and completion pointer. Every note, candidate,
completion and follow-up is an immutable attributed event with original
operation identity and a digest binding its full source references. Completed
reviews accept follow-up results but cannot rewrite discussion or decisions.
Schema DDL is idempotent and does not allocate `user_version`.

## Reads, bounds and authority

`head`, `get_review`, `current_review`, `list_reviews` and `list_events` serve
stored records. Detail returns `review`, the same `snapshot`, a separate
`completion`, and whole event records with a continuation cursor. Immutable
Snapshot reports never pretend that later meeting decisions were known when
prepared. Detail and list wrappers are bounded to 2 MB of actual UTF-8 JSON;
whole event pagination advances the underlying sequence even when rows are
hidden. An event too large for a standalone page returns 413, never an empty
nonadvancing page. Embedded history can expose cursor zero for separate paging.

All reads require current membership and observe scope. Every original policy,
baseline, observation, workload/check, project evidence and prior comparison
source must still be readable with its original identity/digest. Report Snapshot
metadata/detail/latest/Brief/export paths use the same maintenance access gate.
A source loss withholds the complete report instead of leaving quoted report
text next to a filtered source. Operations and history need the same original
source checks through `operation_visible`. Current reads do not fall back to an
older visible report. Lists omit inaccessible rows with `visibility_filtered`.

These five actions are local records and may be admitted while Paused/Closed
through the shared lifecycle record-action allowlist. Weekly schedule execution
is a separate integration over the existing bounded scheduler and is not implied
by the existence of these manual actions or report generation.

## Shared entrypoints and exports

All mutations use the registered central operation endpoint. Read-only routes
relative to `/api/v1/pm/workspaces/{workspace_id}` are:

| HTTP GET | RPC / MCP |
| --- | --- |
| `/maintenance-reviews?after&limit` | `pm_maintenance_reviews_list` |
| `/maintenance-reviews/current?history_limit` | `pm_maintenance_review_current_get` |
| `/maintenance-reviews/{review_id}?history_limit` | `pm_maintenance_review_get` |
| `/maintenance-reviews/{review_id}/events?after&limit` | `pm_maintenance_review_events_list` |

API/RPC/MCP share the same readers and native route/query allowlist. Sequence
cursors use nonnegative signed-64-bit bounds; page/history limits are 1–100.
Unknown, duplicate and empty selectors fail. Reads never admit work.

The original report Snapshot can use the existing Brief export and native save
route. Renderer 2 pairs exactly with `project_brief_with_maintenance_review`;
ordinary Brief snapshots retain renderer 1 and their original output bytes.
Both use original snapshot/view/output digests, escaped literal source text and
the same whole-content byte/page limits. The maintenance section preserves its
fixed period, original publication/policy, coverage gaps and comparison facts.
Subsequent discussion/completion events do not change the historical export.
The ordinary `pm.snapshot.created` event emitted by preparing a report also
inherits the original preparing operation's source gate.
