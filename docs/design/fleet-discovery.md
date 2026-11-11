# Read-only Fleet monitor discovery

Extends [the Windows adapter](fleet-windows.md). Discovery reads both BAT data directories and
complete potential legacy PowerShell monitor observations before returning an owner. It never
creates a directory, publishes a record, launches a process, requests quit or terminates anything.
Absence is an observation, not permission to start: the runtime must acquire the shared monitor
mutex and revalidate configuration, ownership and listeners before effects.

The native monitor is a separate invocation of the trusted installed Dashboard executable with
exact parsed argv `--fleet-supervisor --fleet-config <absolute trusted fleet.json path>`.
`NativeIdentity` constructs this fixed invocation. The supervisor creates its random epoch after
ownership exclusion, not from a WebView request or CLI argument. Native records add `backend: rust`,
exact `arguments`, `created_filetime`, inventory/index paths and `instance` to existing identity
fields. PowerShell records retain their script path and may omit the additive backend field.
Native records must match stored, observed and trusted expected executable/argv and effective
configuration paths. Discovery consumes resolved Configuration paths, not a second pairing rule.

PowerShell `-File` must identify the exact reviewed `bat-connect.ps1`. Supported launch options
precede it; invocation via command text/encoded text is not proof. Inventory/index arguments may
be named, colon-form or legacy positional arguments. Duplicates/unknown flags refuse. Relative
script/config arguments require an exact saved argv match and absolute saved corresponding paths;
discovery never invents the original working directory. Missing old profile-index metadata is only
compatible with the original default inventory/index pair. Absolute paths are compared using
explicit Windows path rules, even in synthetic Linux tests; ambiguous drive-relative/device paths
are refused.

Records are bounded strict JSON. Missing files differ from unreadable, malformed or nonregular
files. All records are checked, including the older directory even when the newer one has a live
owner. Proven dead/reused incarnations are stale observations; unknown incarnation is skipped and logged, never evidence permitting process cleanup.
Multiple live identities or epochs conflict. The same proven identity recorded twice is one owner.
A matching account in another login remains visible/read-only. Legacy enumeration uses native process/token/argv reads, with no shell invocation.
Inaccessible potential PowerShell candidates are skipped and logged; only complete
positive observations can establish a same-account monitor or another login owner.

Discovery rechecks record bytes and returned process identity before publication. This bounds
ordinary races but is not an atomic OS/filesystem snapshot and cannot authorize future effects.
Legacy unrecorded monitors have no invented epoch and require their separate normal-exit migration
path. Tests use temporary records and fake observations only; Windows compilation does not establish
installed cross-login discovery or supervisor parity.
