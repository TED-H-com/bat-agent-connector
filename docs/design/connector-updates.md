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

The shared installer/updater source verifier accepts the wheel's generated
`build-info.json` only with the producer's commit-only schema and the exact
candidate SHA. That validated file can lack a tracked source counterpart; all
other source files still match individually. The installed proof digest includes
the generated file, so later changes are detected on verification and reuse.

Git source installs disable automatic maintenance and automatic garbage collection in
their subprocess environment, preventing background repacking while uv copies
its Git database. These settings append after caller-supplied Git entries and
apply only to installer subprocesses; malformed entry counts are refused.

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

The answering Connector exposes sanitized `capabilities.connector_update` beside
its loaded `build`: enrollment/readability state, policy/channel, installed version
and commit for the next launch, pending transition and last result with its time.
At process startup, it establishes ownership only when the loaded commit equals
the updater's `current` commit and the resolved imported package is inside that
installation's root. It retains that process evidence across pointer switches,
while every capabilities call reads the current state afresh through the same
read-only snapshot as `Updater.status()`. Without that launch evidence, or if its
record no longer matches, it reports `not_managed`; a bundled runtime or checkout
cannot borrow another installation's facts by version or port. A missing enrollment,
unsupported platform or unreadable state is explicit. The read takes no lease,
creates no directories, reconciles nothing, writes no state/receipt and performs
no network requests; it reads at most one bounded private state file per call.
Paths, receipt IDs and package proofs remain private. Settings and Diagnostics
share a separate installed-for-next-launch row and commit comparison with its own
source/read time; a different installation can be an upgrade or a rollback and
does not imply that the running service restarted.

Rollback selects only a previously verified updater-managed installation in the
same compatibility epoch. It disables automatic application until explicitly
re-enabled, and changes only the package pointer. It never restores or downgrades
business databases, credentials, host configuration or operation journals.
Before any journal DDL or migration, the Connector refuses a recorded data step
greater than its `LATEST_DATA_STEP` with `JOURNAL_NEWER_DATA_STEP`; reopen that
journal with the newer Connector. A read-only preflight also prevents a refused
open from checkpointing a newer writer's WAL into the main database.
Refusal happens before the service binds, so
there is no `/api/v1` error response: CLI startup exits 1 with the diagnostic
on stderr, and a supervised service records the startup failure in its log.
The managed launcher carries this same bounded diagnostic to the desktop's
existing startup-error surface, with English and Traditional Chinese guidance;
other startup failures retain their existing private diagnostic handling.
Every new data step must bump the update compatibility epoch together with the
step, so the updater never offers a rollback across it. The journal fence is
the last line of defense for older or manually selected packages.
Pre-enrollment deployment backups remain available to the operator separately.

### Continuity evidence

The source continuity fixtures run upgrade and rollback as separate cases through
the real updater, private home layout and package pointers. They use one data
step and compatibility epoch, with synthetic package directories and unchanged
Connector handlers. The journal contains an actual Work, Attempt, collected
Result, queued and uncertain instruction receipts, original keys, steps and
events. Pointer changes leave the closed database bytes unchanged. Reopening a
real TaskDaemon preserves those rows and the durable Work/Result API view; new
process collection/liveness diagnostics are allowed to change. The original
credential still authenticates and the manual checkout remains unchanged.
Workers are held between admission and execution to inspect pending receipts;
publication recovery separately runs the real worker after restart.

Rollback intentionally leaves updates disabled. Reapplying requires an explicit
policy enable; a disabled `apply` is not evidence of a second upgrade. Publication
cases lose outer completion after the original 201 receipt is committed, switch
the pointer, then replay the original key. They assert the same operation and one
creation call and push, rather than relying on a journal sentinel.

These source cases do not prove a downloaded package install, an OS credential
vault or a rollback across data steps. The native MockVault test retains the
encoded credential through a real updater/API peer; real Windows/macOS vault
retention and operator installations still need their own acceptance. Distinct
managed-core upgrade evidence and its platform limits are documented in the
[managed lifecycle contract](managed-installation.md#background-recovery-and-upgrades).

## Read-only rollback compatibility preview

`batc-update rollback --preview` uses the same default journal path as
`batc serve` without `--db`: `BATC_STATE_DIR/tasks.sqlite3`, or otherwise
`XDG_STATE_HOME/bat-agent-connector/tasks.sqlite3` (with `~/.local/state` as the
fallback state root). It does not discover a deployment's `--db` override.
A deployment started with a different `--db`, including a Fleet recipe's
`journal_path`, needs `--journal /path/to/journal.db` for its preview.
Every preview reports the absolute source journal path in `journal.path`, even
when it cannot be read; `journal.source` is `explicit` for `--journal` or
`default` for the shared default. This is the selected original file, not the
temporary snapshot, and does not certify that it belongs to a running deployment.
Preview leaves ordinary rollback and managed Desktop downgrade refusal intact. It
is separate from `status` so existing status readers keep their output contract.
It does not acquire/create an updater lease, reconcile activation, invoke an
installer, send operations, or restart a running process. It creates only a
short-lived private temporary disk copy of the journal and WAL: original files
are opened with `O_RDONLY`, and SQLite opens the copy with `mode=ro`. Opening the
original with `mode=ro` alone could still update WAL shared-memory read marks.
The copy excludes original shared memory; changed file generations, unreadable
data, a recovery journal, or snapshots exceeding 128 MiB produce unknown facts.
Temporary copies are removed when the read completes.

Every fact has a value, compatibility classification, reason, evidence build,
observer build and observation time. Journal data step, newer-journal fence,
in-flight action/status and step counts, credential records and enrollment
formats remain separate facts. The previous build's support comes only from
its own `compatibility.json`, read without executing code and checked against
its saved package digest. A missing, malformed, unreadable or changed declaration
cannot establish support. Existing releases through 0.2.12 have no declaration;
version numbers and source-code text searches are not substitutes. The new file
ships with subsequent builds; a freshness test compares its format and support
facts with the producer, current `LATEST_DATA_STEP`, fence, updater epoch and
supported enrollment formats. Package version remains owned by package metadata
and `__init__.py`; the declaration's digest already binds it to its build.

`compatible` means all observed facts are known and compatible with the verified
declarations. `incompatible` means at least one known fact conflicts, even when
other facts are unknown. Otherwise the verdict is `unknown`, never safe. An empty
credential store is a known absence of records, not a known credential format.
Present API-principal records, task capabilities and the local admin token have
no independent format version and remain unknown. Updater state has format 1
and an epoch; retained deployment receipts have no format version and remain
unknown. External BAT/profile/keychain credentials are not inspected. The
shipped declaration certifies no in-flight actions yet; their support remains
unknown, including legacy task/command work. Parameterized test declarations
exercise lower data-step maxima, missing fences and future support separately.
An empty work set has reason `NO_IN_FLIGHT_WORK`; `IN_FLIGHT_SUPPORT_DECLARED`
means existing rows and every recorded step are covered by the declaration.

Today, every real enrolled installation previews `unknown` even when its journal
and fence facts are compatible: it has unversioned credentials or a retained
rollout receipt. A known incompatibility still takes precedence. Versioned
credential and receipt formats, with declared reader support, are needed to make
`compatible` reachable for these deployments. Versioning those formats is
separate work. A rollback policy that refused `unknown` would refuse rollback
on every current installation with these otherwise-compatible facts.

Even `compatible` does not prove every rollback effect will succeed: observation
can become stale, external systems can change, and declarations do not execute
the old reconciler. Whether rollback refuses `incompatible` or `unknown`
evidence, or offers force, is an owner decision that has not been made. Preview
does not enforce such a policy or change automatic update/downgrade rules.

## Bounds and recovery

Network responses, release enumeration and subprocess time are bounded. Only
the updater's own spawned process group may be terminated on timeout or interruption,
before releasing the installation lease. Failed
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


Completed `check` and automatic `run` release discovery retain `release_check`
in the existing private updater state, before any installation. The public
projection contains only completion time and the validated candidate's version,
commit and publication time. Publication time is display-only: the existing
check records a timezone-aware ISO string of at most 40 characters from the same
selected release item. Other values record no clock and leave the released slot
unknown. This step never rejects a release or changes its installation candidate;
the shared manifest fields and bootstrap publication acceptance remain identical.
Existing private records retain their shape and meaning. A completed check with no candidate reports a null
candidate. A failed check preserves the earlier completed observation without
renewing its time; changing channels withdraws that observation until a check of
the selected channel completes. Older states have no released observation.
`batc://identity` reuses this installation status for the calling MCP process,
independently from any central service; only its captured startup installation
proof permits a next-launch comparison and process-specific restart notice.

`installed_at` retains the successful enrollment/switch/rollback receipt time
independently of later check failures or policy changes. Older state without that
clock reports it unknown. The shared installation row keeps its separate client
read time as well; neither clock fills the released or answering slot.
