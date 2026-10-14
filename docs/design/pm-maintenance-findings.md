# Configured maintenance findings (W47)

An adopted policy may add `observation.maintenance_sources` alongside the explicit
`github_open_prs_and_actions_v1` profile. Omission preserves the old document and
means unconfigured; it never enables Issues or dependency scanning implicitly.
The object has exactly `issues` and `dependencies`, each null or configured:

- Issues: `{labels_all: [<exact label names>]}`. An empty list explicitly selects
  all open Issues. Pull requests returned by the Issues endpoint are excluded.
- Dependencies: `{manifests: [<exact repository-relative file paths>],
  base_branch: <adopted maintenance branch without refs/heads/>,
  dependabot_actor_id: <positive provider-native Bot ID>, alerts: <bool>,
  pull_requests: <bool>}`. At least one provider is selected. Paths do not grant
  repair authority; policy allowed paths and accepted-version proof remain separate.

A selected source must also occur in policy.sources. No arbitrary URL, provider,
repository, scopes, risk label or claimed scan completeness is accepted. Owner
adoption, current baseline/provider identity, lifecycle, scheduler delegation and
per-request cancellation/epoch/deadline checks remain the existing contracts.

The existing GitHub Traversal performs all GETs with its one page/row/byte/time
budget, durable page receipts and two full traversals. New projection kinds retain
only bounded native identities, configuration match facts and dependency advisory
metadata. Text descriptions, issue bodies, patches and provider URLs are never
persisted or used as instructions. Pagination proves the same provider endpoint
and immutable filters, including Dependabot cursor pagination. Duplicate identities,
truncation, changing heads/files, permission errors or unsupported shapes produce
incomplete coverage. No retries recreate a missing original read receipt.

Configured facts are part of whole-traversal equality. Source coverage is disabled,
complete with matched/empty results, or incomplete. A successful empty list proves
only that this configured endpoint returned no matches at observation time. It does
not prove all manifests were scanned or that the released version is unaffected.
Read-time status separately reports missing/stale/context_changed/stopped; records
remain immutable. Disappearing findings never become resolved implicitly.

Findings live inside the existing immutable observation, with stable IDs derived
from workspace, provider configuration digest, native repository ID, source kind
and provider-native Issue/PR/alert ID. Register, overview and discussion reads
project a locale-neutral `finding` identity from those stored facts at read time.
The object is not stored and does not add a schema or `user_version`. See the
[register contract](pm-register.md#finding-身分). Each occurrence binds the exact policy and
release baseline. W46 must consume an exact observation ID, document digest and
finding ID; a stable ID alone grants no execution authority. Existing native PR
identity is retained for deduplication, and exact existing Connector Issue/work
mappings may be referenced with their full original source barrier. No prose-based
association between an alert and a PR is invented.

Dependabot PR matching requires configured native Bot identity, the adopted base
branch and complete changed-file coverage touching a configured manifest. PR files
are verified against the detail's changed_files count and fixed head, within the
provider's 3000-file ceiling. Existing exact-head CI facts remain separate. Alerts
retain package/ecosystem, manifest, severity, advisory identity, vulnerable range
and first patched version, with fixed=true/false provider state distinct from a
release fix. Alert coverage belongs to the provider's default branch; its current
branch head is observation context, not a proven scanner revision. Accepted release
version applicability and adopted dependency version remain unknown. No second
resolver, repair dispatcher, model call or write endpoint is introduced.

Provider permission boundaries are explicit: Issues read; Pull requests read;
Dependabot alerts read (classic tokens require security_events, or public_repo for
public-only access). A 403/404 is unavailable/unknown, never clean or disabled.
Official references: [Issues](https://docs.github.com/en/rest/issues/issues),
[Pull requests and file ceiling](https://docs.github.com/en/rest/pulls/pulls),
[Dependabot alerts and cursor pagination](https://docs.github.com/en/rest/dependabot/alerts).
