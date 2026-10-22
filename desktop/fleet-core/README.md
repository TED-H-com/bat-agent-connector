# Native Fleet core

Internal Rust library for the existing Fleet inventory, profile pairing, independent local selections,
bounded configuration snapshots, locked selection updates, process identity decisions and bounded recovery.
The Windows OS adapter stops only explicitly verified local processes through retained handles.
The probe module performs bounded, read-only loopback TLS/BAT and Connector capabilities requests.
The route module probes configured TCP endpoints and a fixed trusted local Tailscale
`status --json` command under deadlines, and keeps route/recovery decisions native-only.
The native desktop controller wires these bounded effects through the reviewed
supervisor and lifecycle guards. Source and synthetic fixtures do not establish
installed Rust Fleet parity.

The port retains Fleet Kit compatibility for existing installations. Public source and
`tests/fixtures` define its inventory/index/SSH contracts using synthetic values only;
private configuration, real host identities, credentials and pins are excluded. New
installations can use the [public Rust setup](../../docs/design/fleet-configuration.md).

For a packaged Windows client, use the [desktop workflow on `main`](https://github.com/TED-H-com/bat-agent-connector/actions/workflows/desktop.yml?query=branch%3Amain).
Choose a successful run from the current public history and match its source commit,
Windows package job and installed-fixture records before downloading
`desktop-Windows-unsigned`. The [public templates](../fleet.example/README.md) supply a
standalone Rust configuration; provide your own inventory, SSH aliases and BAT profiles.
Legacy PowerShell scripts are optional compatibility inputs for existing installations.

The native-only credential reader resolves the selected inventory's BAT profile token or Fleet
observe DPAPI reference from fixed, bounded files. It never falls back to a desktop mutation token,
another profile, another data directory or an environment variable. See the
[format and evidence contract](../../docs/design/fleet-credentials.md); Windows DPAPI runtime
compatibility remains a Windows-runner gate. The [native BAT admission and bounded catalog](../../docs/design/bat-source-catalog.md)
shares versioned admission expectations with Python and preserves legacy references.

Malformed existing preferences fail closed instead of silently enabling every connection. JSON field
spelling is exact and decoded keys must be unique ignoring case. Profile IDs are also unique ignoring
case. These stricter ambiguity rules are deliberate; ordinary reviewed fixtures remain compatible.
Native adapters must use the shared preference file lock/CAS and revalidate configuration before effects.
`ProcessEvidence::matches` is a necessary identity check, not permission to kill by PID. Hold the process
handle before final identity verification, require current login ownership and prove the originating
monitor ended before orphan recovery. Probe results retain their captured configuration/selection/epoch
generation and expire after 60 seconds; callers must revalidate configuration before publication.
The observe credential is separate from the desktop mutation credential. Synthetic loopback peers cover
pinning, TLS signatures, BAT auth/version/profile/workspace, strict observe capabilities, fragmented
UTF-8/ping, total deadlines and size limits. Installed Windows process/probe/migration evidence is still
required; loopback fixtures do not establish live Fleet acceptance.

Run `cargo test --locked --manifest-path desktop/fleet-core/Cargo.toml` from the repository root.
The parser uses Serde's [Visitor](https://docs.rs/serde/1.0.229/serde/de/trait.Visitor.html) interface.

`Configuration` reads bounded UTF-8 (optional BOM) Kit inventory/index/SSH files and retains the exact
bytes used for validation. The default four-input binding matches the PowerShell facade, including
UTF-16 path lengths; an alternate profile index also binds the trusted canonical schema/pin source.
Read failures or byte changes invalidate old evidence. This is a local snapshot check, not an atomic
transaction with external editors. SSH Include expansion is not added by this loader.
`data_directory` rechecks both BAT directory names on every call and creates neither.

`selection_io::Store` holds the shared preference lock while comparing the retained primary/legacy
bytes, current configuration and freshly proven owner epoch. It writes a flushed create-new temporary
file and atomically replaces only `fleet-client.json`. Unknown ownership and directory migration refuse.
The public preference revision remains the PowerShell primary-byte digest; callers must retain the
private Snapshot too, so changes to legacy choices cannot hide behind an absent primary file.
Windows uses `windows::PreferenceLock` from the companion OS adapter; Unix fixtures use File locking.

Windows boundaries are in `windows` and tested independently from the platform-neutral
`process_adapter` decisions. `MonitorMutex::try_acquire()` uses the PowerShell account mutex and
must stay on one supervisor thread. `PreferenceLock::{try_acquire,acquire}` opens the same `.lock`
sidecar with zero sharing; keep the guard alive through the entire preference CAS/publication.
Process arguments and paths remain native-only evidence, never frontend status. See
[the Windows adapter contract](../../docs/design/fleet-windows.md) for the exact interfaces,
legacy creation-time format, NT command-line limitations and pending Windows execution evidence.

`route::SelectionRequest` lets independent background workers probe an immutable snapshot.
A result's alias is available only after current generation/policy validation; the supervisor
still revalidates configuration before launch. Selection updates preserve an owned Run and
recovery history, while source changes require a fresh policy after owned stop. See
[route policy and intentional Kit corrections](../../docs/design/fleet-routes.md).

`migration` records explicit backend/autostart transitions with exact original bytes and
normal-owner exit proof; it never retries an uncertain launch. The fixed Windows startup
codec reads/writes only the reviewed Open BAT shortcut via the caller's local journal
flow. These modules require the native facade's guarded hooks and are not wired into
automatic startup by this library. See [migration contract](../../docs/design/fleet-migration.md).

The opt-in `bootstrap` module records a local request before a fixed SSH query/ensure
exchange. `windows_bootstrap` uses the trusted system SSH executable and shared launcher
guard; the separately deployed Linux helper proves the existing unit/state/owner before
a single start request. No recipe/helper is installed or enabled by this source slice.
See [the bootstrap contract and deployment limits](../../docs/design/fleet-bootstrap.md).

`bootstrap_policy` checks positive current-login tunnel ownership and fresh selected
Connector-unavailable readiness before the desktop bootstrap controller proceeds.
The optional trusted recipe `auto_ensure` flag enables only the original finite
request; the shared settings UI reads its local receipt independently of central
authentication. A healthy central endpoint needs no service repair.
