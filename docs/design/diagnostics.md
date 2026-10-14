# Local diagnostics

`desktop/src/diagnostics.js` selects named fields and runs a serialized JSON
scrubber. `batc.diagnostics.v1` contains client/build metadata, anonymous hosts,
client-read session counts, optional history reason counts, failed/uncertain
operations from one page, a 50-entry memory-only failure ring, source availability
and static log/state location templates. No server endpoint, journal/schema step,
telemetry, credentials fallback or central mutation is introduced.

The report referenced an observation status endpoint and host `bat`/`policy`
fields that this base does not supply. This branch has no `/v1/observation/status`;
its source stays unavailable. `/hosts` supplies reachability, stale reasons and
server version. Discovery from the explicitly matching profile supplies only
error/coverage reason codes and boolean capability flags; its identity, credential
reference and workspace list are excluded. The builder accepts optional BAT fields without fabricating them.
Capabilities supply policy booleans. `/managed/setup` is read only for a managed
installation with `manage`; other existing reads require `observe`. Session facts
come from prior successful client reads and contain no conversation content.
Revocation clears cached counts and the preview; a revision fence rejects late
reads. Connection changes clear the failure ring and retire previews.

Never copied: tokens/secret refs, fingerprints, hostnames/endpoints/SSH aliases,
paths/managed roots, titles/messages/prompts/drafts, repositories/remotes/branches,
actors/emails. Hosts sort by internal logical key and become `host-1`, `host-2`, …;
no key is serialized. Opaque operation IDs, enum error codes and fixed timestamps
are allowed. Server free-text errors are omitted. JSON string scrubbing removes
common token formats, fingerprints, URLs, IP addresses, absolute POSIX/Windows
paths and email addresses; only generated route and default-location templates
are exempt from path scrubbing.

## Build identity

Capabilities adds `build: {version, commit, source}`. Git installs use installed
PEP 610 `direct_url.json` (`vcs_info.commit_id`); plain packages and editable local
checkouts without VCS metadata have a null commit. A frozen runtime reads only
its packaged `build-info.json`. No runtime Git process is invoked. The packaging
job supplies `BATC_BUILD_COMMIT` to PyInstaller and Rust `option_env!`; native
version comes from `CARGO_PKG_VERSION`. Missing commit means unknown in both
locales. Browser assets use the serving Connector build and remain reproducible.

## Save boundary

Browsers use a Blob download. Native saving does not rely on Blob support across
WebViews: upstream documents platform-dependent downloads and reports macOS Blob
failures ([Tauri issue 8452](https://github.com/tauri-apps/tauri/issues/8452),
[download events](https://docs.rs/tauri/2.12.1/tauri/webview/enum.DownloadEvent.html)).
These sources justify a native fallback; no claim of testing all OS WebViews is made.

`native_diagnostics_save` is local-main-window only, with its own permission. It
accepts only text, a generated `.json` basename, and locale; rejects unknown fields,
invalid schema and content above 256 KB in UTF-8. The existing file-dialog lock and
Destination writer provide a native dialog and atomic create-new publication.
Only a display name returns, cancellation returns null, existing files are
preserved. No generic filesystem/dialog permission, network read, artifact upload
or automatic open follows saving. Tests cover payload bounds, exact bytes,
permission shape and preservation of files appearing during the dialog.

Managed desktop stdout/stderr is not persisted. State/configuration locations are
hints only, use `io.betteragent.dashboard`, and never assert that a log file exists.
Standalone defaults come from `config.state_dir()` (the `.local/state` convention
on all platforms, with environment overrides), not the report's claimed platform
defaults. `installation.json` is at the central root, not under state; credentials are neither
read nor included. See the bilingual getting-started Diagnostics sections.
