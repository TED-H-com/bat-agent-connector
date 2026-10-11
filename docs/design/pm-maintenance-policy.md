# Maintenance policy adoption

This is the policy-record portion of v1.2 W46/W51. It shares the workspace,
release baseline, lifecycle journal and operation service. Policy adoption does
not start a scheduler, create a repair, change lifecycle stage, or grant access
to another controller's session. Scheduling, complete workload coverage, budget
reservations, repair execution and Maintenance Review remain separate work.

## One explicit decision

`pm.maintenance_policy.adopt` uses target `{workspace_id}`, parameters
`{policy,rationale}`, and preconditions:

```json
{
  "expected_policy_revision": 0,
  "expected_previous_policy_id": null,
  "expected_baseline_id": "pmrb_11111111111111111111111111111111",
  "expected_lifecycle_revision": 0
}
```

The actual current workspace owner with `manage` may adopt. An administrator
scope does not replace workspace ownership. Original source visibility, actual
owner status, baseline, policy revision and lifecycle are checked at admission
and inside the journal transaction that records the policy. Two decisions from
the same previous revision cannot both commit. A pause or close blocks a queued
policy decision; reopening does not authorize an old execution epoch.

Lost responses reuse the exact original envelope/key. A committed receipt can
be recovered without observing the provider again or repeating adoption. Public
reads and result recovery still apply current membership and original source
visibility. Recovery needs `manage`; it does not add an `observe` requirement.

## Policy document

Every policy provides all of these fields. Nothing is inferred from a model's
"low risk" label, discussion enthusiasm, current checkout name or worktree age.

| Field | Meaning and limits |
| --- | --- |
| `enabled` | Explicit boolean, distinct from project lifecycle |
| `after_release_policy` | `maintain_when_idle`, `continue_development`, or `manual` |
| `sources` | Nonempty subset of issues, pull requests, dependencies, documentation, research |
| `allowed_work_kinds` | Subset of dependency fix, bug fix, documentation; empty permits observation without repairs |
| `allowed_paths` | 1–100 exact repository-relative files or trailing-slash directory prefixes; `.` explicitly selects the repository |
| `delivery` | Maximum report, pull request, merge or deploy; only deploy selects an environment |
| `observation` | Maximum observation age and minimum stable observation duration |
| `schedule` | IANA time zone, check interval, weekly review weekday and local `HH:MM` |
| `limits` | Concurrency, per-run duration, period duration/run count, optional cost budget in millionths of USD, retry limit |
| `verification` | Explicit CI/review requirements and nonempty acceptance evidence descriptions |

Exact schema validation rejects extra fields, booleans masquerading as integers,
invalid zones, absolute/traversing paths, wildcard paths, unsupported work kinds
and unbounded limits. Repairs require a verified maintenance branch and at least
a pull-request delivery ceiling. A report-only policy can be adopted without a
maintenance branch. A delivery ceiling never overrides repository branch rules,
required CI, current scopes, source access or the external operation's checks.

The schedule records intent; this slice does not calculate or execute due jobs.
`tzdata` is a runtime dependency and is included in the desktop managed runtime,
so IANA names do not depend on host system zone files. Python documents this
cross-platform requirement in its [zoneinfo documentation](https://docs.python.org/3/library/zoneinfo.html).

## Fixed sources and controller

The immutable record includes the selected baseline ID/digest, previous policy,
actual server-bound controller identity, operation ID, rationale and time. The
policy scope retains native repository ID, channel, explicit project binding,
verified maintenance branch and configured BAT workspace bindings. BAT binding
digests use the same canonical serialization as inventory observations. Provider
URLs, fingerprints, credentials and local checkout paths are not copied into
the policy. It has no public field for caller-selected controller authority.

`github_scope=repository` means a few locally linked PRs do not establish complete
repository coverage. Missing provider enumeration is unknown. A workspace absent
from inventory is unknown. Lack of recent output is not idle evidence.

`fixed_record` reads an original policy for source checking and audit. It does
not require that policy to remain current. `active_record` additionally requires
the latest policy and a compatible current primary baseline. A verified patch
on the same repository, channel, maintenance branch and workspace scope may
advance the baseline without re-adopting the same policy. A branch, channel,
repository, binding or provider-configuration change requires a new decision.
Neither accessor grants execution authority or proves complete workload coverage.

## Shared readers and privacy

HTTP readers are `/pm/workspaces/{workspace_id}/maintenance-policies`, `/current`
and `/{policy_id}`. RPC/MCP equivalents are `pm_maintenance_policies_list`,
`pm_maintenance_policy_current_get` and `pm_maintenance_policy_get`. They require
`observe` and current membership. Tauri permits only these exact GET routes and
list cursor/limit parameters. All mutations use the existing operation endpoint.

The same original baseline/source chain gates policy records, pending and
completed operations, replays and events. A later readable policy cannot expose
an earlier withdrawn source through a combined history. A hidden current head
retains its revision but removes its ID/details and reports `visibility_filtered`.
This is not equivalent to having no policy.

Readers hold one SQLite snapshot and cap serialized pages at 2 MB, returning
whole documents and stable sequence cursors. Empty filtered pages can still have
a continuation cursor. Tables and immutability triggers are idempotent; no new
`user_version` or migration data step is allocated.

## Verification boundaries

Domain tests exercise actual disk journals, fixed mock-provider publication
evidence, concurrent adoption, owner/source revocation, lifecycle epochs, exact
receipt recovery, baseline advancement, malformed policies, pagination and zone
data fallback without system files. Entry tests compare HTTP/RPC/MCP and cover
global operation/event filtering. These are automated contract checks, not real
Fleet, production publication or native installation acceptance.
