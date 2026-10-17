# Explicit original requirement acceptance in engineering flows

## Reviewing a fixed Work Result

The shared Result panel offers **Review this result** in both Board and the live
Work view when central supplies a complete Work/Attempt/package/execution identity
and a fixed commit. Opening the entry reads the existing quality catalog; it does
not create a review, run a verifier, start analysis or adopt evidence.

The existing review editor is scoped to that original package, execution and
commit. Those source fields are fixed under Details. Saved reviews for another
execution or commit are excluded. A new review still requires the owner's
explicit reviewer, criterion policy and rationale; recording conclusions remains
the appointed reviewer's action. The Result's currently offered action targets
add a guard to the existing role, scope, version and source checks. Central remains
the authority at submission. The ordinary Requirements editor is unchanged.

Each Attempt/commit has its own durable form namespace. Refresh preserves its
draft and original operation key; changing Result withdraws the old editor without
retargeting it. A failed, withdrawn or mismatched Result removes the review entry,
and late source replies cannot recreate it. Missing original execution/package
evidence is an unavailable state, never a reason to substitute another operation.
JSON object member order does not identify a source: central serializes original
package references with a different key order from browser-created objects.
Compare complete values with normalized object keys while retaining array order;
changed package digests, criterion identities or policy values still differ.
After an explicit save succeeds, refresh the original Result's current actions
before refreshing the editor. This lets the newly appointed reviewer record
conclusions without reloading the page or using stale authority. A withdrawn
Result still retires the editor; the original saved operation is not replayed.

Recorded conclusions appear with the Result through the existing central
projection. This entry does not expose the Requirements editor's local evidence
selection button: selecting a reference without an evidence consumer would have
no effect. Requirement adoption, dependencies and milestones continue to follow
their original evidence policy; a review or passing CI alone does not complete them.

## Original requirement acceptance selections

The shared workspace handoff and selected-batch panels consume the registered
`accepted_requirements_v1` condition. They preserve the distinction between
fixed revision verification and requirement acceptance. A checked prerequisite,
a started session, or GitHub checks alone cannot supply semantic acceptance.

The batch result selector freezes the selected package, adopted plan/scope, and
all original acceptance input identities. Each dependency preserves that exact
condition. Changing an upstream condition requires explicitly selecting its
prerequisite again. Existing GitHub result requests retain their original shape.

A source-gated quality catalog supplies ordinary assessment choices, labeled by
package, candidate, version and responsible reviewer. Only currently qualified,
applicable assessments with complete original criterion coverage are offered.
Batch evidence additionally matches the original child and package. The submitted
reference retains the exact review, assessment operation/version/digest, and
candidate; refresh cannot substitute another assessment.

For a handoff's declared requirement prerequisites, the user selects each
original assessment and reviews the handoff again. Newly populated prerequisite
selectors stay disabled until the original package, readiness and published-base
reads finish. Rebuilding the choices during review must apply the editor's current
read/lock/authority state before the next awaited read, so a displayed selection
cannot be silently ignored. This same editor supplies both single handoff starts
and selected batch entries. The identity-only
`pm_requirement_quality_refs` map is included in readiness and the fixed handoff
only when nonempty. It is bounded to 16 KiB using public JSON spacing and UTF-8.
The selector requires
`features.pm_workspaces.package_handoff.requirement_acceptance_profiles` to
include `accepted_requirements_v1`; a quality-enabled older central receives the
legacy readiness request. Batch capability discovery uses the existing
`work_batches.profiles` list.

The service remains the authority for full original history, reviewer authority,
current applicability, source access and final-frame checks. Catalog pagination
must advance and is bounded. Refresh preserves a selected original identity;
unavailable originals disable new submission. Source withdrawal removes displayed
private choices and facts while durable local drafts and original request keys
remain available for permitted reconciliation. Recovery sends the exact saved
request even if a newer assessment exists or the lifecycle is paused.

The controls reuse the existing shared tokens, fields, details and action layout.
There is no new workspace tab, JSON editor, hash entry, or acceptance checkbox.
English and zh-TW use the same browser/native source. Parent integration owns
mounting and generated assets.

A fixed base may also be reviewed for batch selection when the only remaining
checks are `dependency:<requirement>` for that package's exact declared
prerequisites. This requires the acceptance and batch capabilities. The single
handoff submit remains disabled. The user explicitly chooses an upstream batch
entry with its frozen result condition; the service keeps the dependent blocked
until actual selected result evidence exists. Scope, work, source, coverage and
other readiness failures still prevent this preparation path.
