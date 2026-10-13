# Native Fleet backend and login-startup migration

This implements the local transition contract in the Tauri plan §§08/09/21 and
[fleet-rust.md](fleet-rust.md). It does not change central tasks, inventory, profile
selection or credentials, and is not installed Windows parity evidence.

Reviewed Kit installs only the current-user Startup `Open BAT.lnk`:
`client/install-autostart.ps1` targets system `wscript.exe`, exactly the quoted
`client/Open BAT.vbs`, with the client directory as working directory. No registry
Run value or scheduled task belongs to this Kit. Migration touches only this fixed
slot. Foreign or ambiguous links refuse; other entries remain untouched. Entry presence is configured login startup, not proof that
Windows policy or Task Manager permits execution; those OS settings are not overwritten. Native
startup targets the trusted installed Dashboard with `--fleet-login --fleet-config`
and the trusted absolute config path. The same slot prevents adding a second Fleet
login entry. The config's `kit_root` is the installation root; PS scripts are under
its `client` child. Config validation comes from the installation snapshot adapter.

An opaque native preview binds exact source bytes, paths, backend and owner; the adapter
checks the UI's reviewed fingerprint before admission. A reviewed intent saves exact
original/proposed config and shortcut bytes, caller
login, original monitor identity and fixed direction before effects. Each intent has
one local journal. Original bytes remain available after completion and are used by
an explicit reverse intent; there is no blind automatic rollback. Journal/config/link
writes use bounded regular files, expected-original comparison, staged write and
replacement. Uncooperative editors can still race the final check/rename; stop such
editors during migration. This is not a multi-file atomic transaction.

One caller holds the shared `Global\\BatFleetLauncher_<SID>` coordination across
transition effects. The core requests normal quit only for the exact current-login
recorded owner with a valid epoch. Other-login, unrecorded legacy and unknown evidence
refuse. It never force-kills a monitor, BAT, SSH, Tailscale or another app. A quit
receipt is not proof of exit: a later discovery must prove no owner, and the caller
must acquire the common Monitor mutex and recheck absence before writing settings.
The monitor mutex stays held through config/startup replacement. It is released only
for launch while launcher coordination remains held.

Recovery recognizes each slot only as its original or requested bytes. A third value
refuses without overwriting it. A write whose receipt was lost can be read back and
settled. A durable launch-intent precedes the launch hook; after a crash/unknown hook
result, recovery may accept a freshly proven matching current-login backend, but
never automatically launches again. No owner after that intent means attention is
required. Explicit reverse migration first stops any proven current owner and uses
the same exclusion and byte checks. Launchers must refuse an unfinished migration
journal, including when one slot already changed.

The Windows adapter uses COM ShellLink persistence in memory: it does not resolve or
execute an inspected link. It checks exact target, arguments, working directory and
unsupported link flags, creates only fixed known invocations, and retains all source
bytes. Existing local target/argument/working-directory paths expand Windows 8.3 names
with [GetLongPathNameW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getlongpathnamew)
before comparison with the captured installation. This corrects
the short spelling retained by WScript.Shell; it does not call ShellLink Resolve or
accept another command line. Reparse components, missing paths, foreign paths and
unsupported/elevated flags remain refused. Tests use synthetic files/links/process observations and temporary directories;
no real Startup, registry, monitor, account credential or live tunnel is changed.

## Interfaces and integration requirements

- `Store::new(fleet_json, startup_directory)` accepts only trusted native absolute paths;
  the config basename and startup filename are fixed. `preview(platform, from)` returns
  a native `Preview` with safe `fingerprint`, `backend`, `autostart_entry_present` accessors.
- `begin(platform, id, &preview, to, exact_next_config, autostart)` saves an immutable
  reviewed request. IDs are 32 lowercase hex characters and cannot change their request.
  `advance(platform, id)` performs bounded transitions; the adapter owns finite polling
  deadlines. `pending()` blocks ordinary launchers while a transition is unfinished.
- `restore(platform, source_id, new_id)` retains both journals and requests exact original
  bytes. A lost pointer-publication reply resumes the same prepared reverse intent.
- `Platform` supplies current discovery/login, installation validation for both exact
  payloads, normal exact-owner quit, shortcut codec, launcher/common-monitor guards and
  bounded low-level launch. Launch must not reacquire the already held launcher mutex.
  Configuration and process observations are revalidated before every local effect;
  unknown outcomes are errors, never a false `None` owner.
- `windows_launcher::LauncherMutex` is shared with native ensure/start. Keep it on one
  native blocking thread, including readback; it is neither recursive nor tunnel authority.
  `windows_startup::Codec` reads/renders in-memory ShellLink bytes. Only the separate native
  `startup_directory()` queries the OS folder; tests never call it or write installed entries.

## Native Windows platform

`windows_migration::WindowsMigration::new(installation, paths, roaming, quit_file,
current_executable, system_directory)` implements the core `Platform`. All inputs
come from trusted native installation/OS discovery; `paths` belongs to the captured
installation's `client_root`. The system directory comes from `GetSystemDirectoryW`,
not PATH or an environment-selected executable. This is not an IPC interface.

Keep the adapter on one dedicated blocking thread. `Store` owns returned `Rc` launcher
and monitor guards; the adapter keeps only weak references and requires a live launcher
guard for effects. Launch uses that existing guard without re-entry. The Monitor guard
is released only at the core's persisted `LaunchRequested` boundary.

Discovery uses both BAT directories and exact process/login/epoch/configuration evidence.
Before accepting no owner, it also calls `monitor_launch::verify_absence` on the fixed
roaming `bat-fleet-monitor-launch.json`: an unknown or still-live unpublished child
blocks migration **before config/startup writes**. A historical other-login intent is
also refused, even if its child ended. This guard never edits or replays a launch receipt.
Normal quit delegates to `supervisor_control::request_quit` after matching the core's
accepted owner; no forced termination exists in this adapter.

Original and proposed config payloads are validated against the same installation
layout through `Snapshot::validate_payload`. After the intentional backend replacement,
launch loads a fresh snapshot of those bytes and rechecks the same client/script/executable
paths and unchanged Kit configuration. It does not require the old config bytes to remain.
Native launch reuses `monitor_launch::ensure` and `WindowsLaunch`, including its retained
child birth fence. PS reverse starts only fixed system Windows PowerShell with reviewed
`-ExecutionPolicy Bypass -NoProfile -File <client/bat-connect.ps1> -InventoryPath ...
-ProfileIndexPath ...` arguments and the client working directory. It does not run the
login VBS/updater or a shell command string. The inherited environment is restricted to
Windows identity/storage/routing variables; PSModulePath and Windows system roots are
fixed from the trusted system directory. BAT/Connector tokens and proxy credentials
are not inherited.

Each launch polls exact retained child PID/birth/executable/argv/login and a published
owner epoch for at most five seconds between local queries. Local Windows inspection
calls themselves are synchronous; this is not a hard interruption of an OS API. A different
owner, failed query or absent receipt remains an error/unknown result. No error kills a
monitor or retries a spawn. Once `LaunchRequested` is persisted, a later `Store::advance`
uses discovery only, including after the adapter process restarts.

Pure launch-plan/environment/readback tests use synthetic arguments, owners and clocks.
The migration/launch-journal integration fixture proves unpublished-child refusal leaves
both original slots unchanged. Windows cross-compilation validates the native adapter API;
it does not execute a real migration, PS monitor, installed Startup entry or live tunnel.

### Legacy PowerShell cold-start limitation

`launch_powershell` currently starts the five-second `wait_for_owner` budget after
spawn and retained-child identity validation, with no evidence that the interpreter
has entered `bat-connect.ps1`. A first migration call after boot/login can therefore
exhaust readback during cold PowerShell startup. Its durable `LaunchRequested`
remains unknown; the child is not killed, and subsequent recovery observes the
original effect rather than launching again.

The reviewed legacy `bat-connect.ps1` is an external installation input, absent
from this repository. It has no readiness handshake in the current contract.
Adding the same bounded startup phase safely requires cooperation from that
script (a fixed flushed marker before monitor work), followed by the existing
five-second owner readback. An encoded/command wrapper would change the exact
`-File` identity that discovery requires; accepting it without a coordinated
ownership contract would weaken proof. Waiting longer for the final owner or
starting a separate warm-up process would not establish script readiness in the
original child. This path remains unchanged pending the reviewed legacy source
and coordinated handshake; the fixture startup fix does not claim to fix it.

Windows fixture bodies include COM round trips and an actual system PowerShell/WScript.Shell
creation oracle, all targeting temporary synthetic files. Local Linux tests and Windows
cross-compilation are separate from execution of those Windows fixtures and installed
login/migration acceptance. The oracle compares both long and OS-provided short path
spellings for both fixed backends, and rejects extra arguments, foreign target/cwd and
a temporary junction alias. Failure diagnostics contain only those synthetic fixture
fields and link flags. Unsupported link flags or invocation forms refuse safely.
