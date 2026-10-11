# Exact native required CI within GitHub observations

An optional observation.required_ci_profile=github_required_status_checks_v1 must
be adopted alongside the existing GitHub profile. Omission preserves old policy,
intent and observation canonical projections. The same bounded Traversal, journal,
GET transport, source/owner/epoch guards and two-pass comparison are used.

For each exact open PR, the collector reads native PR ID/head/base, its exact base
branch, every active applied branch rule and legacy branch protection. It then
reads complete latest check runs with native app IDs and context identities, and
commit statuses at the same head. Required contexts/app IDs are matched exactly;
passing unrelated checks does not satisfy them. An empty required set is explicit
and does not pass required CI. Context text is hashed in stored projections.
All matching returned check runs must pass; ambiguous duplicate statuses at the
same latest timestamp must all succeed. This conservative profile can block a
provider that cannot distinguish obsolete duplicate check runs.

Applied rules include active organization/repository rules. Legacy protection
requires administration:read. A 403 or 404 never proves no legacy requirement.
Unrecognized rules, required workflows, merge queues, truncated pages, changing
head/base/rules, missing native app identity or ambiguous legacy contexts produce
incomplete evidence. Bypass authority is not used to waive required checks.
Other native review/deployment requirements remain enforced by existing provider
and delivery gates; this profile does not claim to implement them.

Official endpoint contracts checked for this implementation:
- https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch
- https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection
- https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference

Current proof only applies to an exact original owned PR/native repository/head
and base. An explicit immutable observation reference must be frozen in the repair
and delivery binding; later observations cannot replace it silently. Before an
unsent merge the exact selected proof must still be newest, fresh and complete.
Historical source visibility still requires its original observation after expiry.
The profile is read-only; it claims no model cost and no provider billing guarantee.
