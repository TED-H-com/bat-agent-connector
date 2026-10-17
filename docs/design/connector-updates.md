# Connector automatic updates

Owner decision: October 17, 2026; EC-1.1, T8 / E11 / K16. This adds automatic
updates to existing per-user Linux Connector installations. It does not change
Work authorization, restart BAT sessions, or make desktop installation unsigned.
The [desktop update contract](desktop-updates.md) and
[managed central lifecycle](managed-installation.md) still apply.

## Policy and release authority

An operator enrolls an installation once and chooses `stable` or `preview`.
The installed user timer then checks hourly, with jitter, and installs eligible
newer releases. Disabling the policy stops automatic installation; manual check
remains available. Preview enrollment is explicit and is required for the current
development candidate. The updater has no Connector agent/operator credentials.

The publisher is the fixed `TED-H-com/bat-agent-connector` GitHub repository.
Only published releases with a `release-manifest.json` explicitly declaring
Connector update protocol 1 and compatibility epoch 1 are eligible. The manifest
must match the release tag, repository, three-part package version and full source
commit. Drafts, same-version repacks, downgrades and unsupported epochs are refused.
GitHub HTTPS and repository release authority are the trust boundary; a checksum
is integrity evidence, not an independent publisher signature. Desktop Minisign
requirements are unchanged. Clients do not accept caller-supplied feeds or URLs.

The updater fetches the exact published tag from the fixed repository, verifies
its commit, installs locked dependencies in a new versioned environment, and
compares installed Python files and the canonical skill with that source. It
also verifies installed package metadata, VCS commit and command availability.
No branch tip, mutable checkout or unpublished Git object is an update source.

## Installation ownership and activation

Enrollment requires the existing deployment receipt, matching installed source,
entrypoints and canonical skill. A familiar directory name is not ownership
proof. Unknown, edited or foreign entrypoints are refused. Enrollment and later
updates use private state, a kernel lease and durable intents. An interrupted
switch is reconciled against the existing intent and pointer before new work.

After enrollment, Connector entrypoints and the canonical skill share one atomic
`current` pointer. A verified candidate is activated by switching that pointer;
an incomplete download/build never changes the active installation. Versioned
environments remain at their original paths because their shebangs contain those
paths. Previous verified environments and receipts are retained for rollback.

Activation changes **future launches**. Running MCP clients, central services,
desktop bundles and BAT work continue on their loaded code. The updater never
kills them or sends task operations. Status distinguishes available, staged,
installed for next launch and failed; installation is not live acceptance.
Normal client reconnection loads the installed version. A central service update
must still use its existing safe-stop/lifecycle protocol.

Rollback selects only a previously verified updater-managed installation in the
same compatibility epoch. It disables automatic application until explicitly
re-enabled, and changes only the package pointer. It never restores or downgrades
business databases, credentials, host configuration or operation journals.
Pre-enrollment deployment backups remain available to the operator separately.

## Bounds and recovery

Network responses, release enumeration and subprocess time are bounded. Only
the updater's own spawned process group may be terminated on timeout. Failed
attempts retain a concise receipt and leave the active version intact. Retries
reconcile existing installation evidence; they do not replay Work operations.
The next scheduled run can retry a failed preparation, while an uncertain or
externally changed active pointer requires reconciliation before switching.

Private state is owner-only and rejects symlink traversal. Logs/status contain
release identity and failure codes, not credentials, command output or private
repository content. Scheduling uses a per-user systemd timer; unsupported hosts
are reported explicitly. No root service or unrelated scheduler is modified.

## Acceptance

- Release/channel/version/epoch rejection and fixed-origin download limits.
- Missing or mismatched creation receipt, edited files, foreign links and private
  state symlinks cannot enroll or replace an installation.
- Concurrent runs, failure before activation, crash after pointer switch and
  rollback reconcile without a second effect or changes to application data.
- A real isolated package install proves exact source, locked dependencies and
  canonical skill. Unit tests use fake release/network/install boundaries.
- Fleet receipts record enrollment policy, scheduler state, installed identity
  and original process continuity separately. Physical mobile, native desktop
  and real BAT/model acceptance retain their own EC-1.1 evidence.
