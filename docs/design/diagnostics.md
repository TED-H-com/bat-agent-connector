# Local diagnostics

`desktop/src/diagnostics.js` selects named fields and runs a serialized JSON
scrubber. `batc.diagnostics.v1` contains client/build metadata, anonymous hosts,
client-read session counts, optional history reason counts, failed/uncertain
operations from one page, a 50-entry memory-only failure ring, source availability
and static log/state location templates. No server endpoint, journal/schema step,
telemetry, credentials fallback or central mutation is introduced.
All OS templates for managed desktop and standalone installations are included;
the client's OS does not establish the Connector's OS or actual state directory.

The report referenced an observation status endpoint and host `bat`/`policy`
fields that this base does not supply. This branch has no `/v1/observation/status`;
its source stays unavailable. `/hosts` supplies reachability, stale reasons and
server version. Discovery from the explicitly matching profile supplies only
error/coverage reason codes and boolean capability flags; its identity, credential
reference and workspace list are excluded. The builder accepts optional BAT fields without fabricating them.
Capabilities supply policy booleans. `/managed/setup` is read only for a managed
installation with `manage`; other existing reads require `observe`. Session facts
come from prior successful client reads and contain no conversation content.
History reasons are sampled from message responses' top-level `reading` object;
inventory refreshes retain that separate snapshot fact, and a later message read
without reasons, including an absent `reading` object, replaces it with unknown.
Message content is never cached here.
Revocation clears cached counts and the preview; a revision fence rejects late
reads. Connection changes clear the failure ring and retire previews.
Save keeps keyboard focus while its write runs, and Tab still reaches it; a
second press does nothing. A preview over 256 KiB, and withdrawal, leave Save
disabled outside that write. Withdrawal moves focus to Close when Save or Copy
had it, or when focus had already left the dialog controls.

Never copied: tokens/secret refs, fingerprints, hostnames/endpoints/SSH aliases,
paths/managed roots, titles/messages/prompts/drafts, repositories/remotes/branches,
actors/emails. Hosts sort by internal logical key and become `host-1`, `host-2`, …;
no key is serialized. Opaque operation IDs, enum error codes and fixed timestamps
are allowed. Server free-text errors are omitted. The final pass parses the
serialized JSON and percent-decodes each string value when its escapes are valid.
Fixed protocols, valid timestamps/contract dates and module-generated route/log
templates are preserved. Pattern rules cover common tokens and credential forms
(including Basic authorization, `api_key`, `passwd` and `fp`), fingerprints, URLs,
common hostname suffixes, IP addresses, email/user-at-host strings and POSIX,
Windows, UNC and tilde paths, including spaces. After those rules, a value survives
only if it matches a module-owned code/reason, strict numeric version with a
bounded alpha/beta/rc/dev suffix, full commit, operation ID/action or host alias
format, or a fixed schema/locale string. Everything else becomes `[redacted]` in
full; free text cannot survive as a partially redacted fragment. Keys come from
the named-field projection (history keys are validated reason codes).
Host protocols have no numeric fallback, and host versions containing names are
null. All commit validators accept only 40 or 64 lowercase hexadecimal characters.

Shared API and session errors expose one sentence and a ghost export action inside
Details. Renderers pass their destination through `within` so errors reuse an
existing disclosure. A detached caller that constructs its own Details passes
`insideDetails: true`; this avoids adding an error disclosure inside another one.

## Build identity

Capabilities adds `build: {version, commit, source}`. Git installs use installed
PEP 610 `direct_url.json` (`vcs_info.commit_id`) only when the distribution's package
directory resolves to the running package's directory; a different site-packages
copy cannot identify a checkout on `PYTHONPATH`. Plain packages and editable local
checkouts without matching VCS metadata have a null commit. A frozen runtime reads only
its packaged `build-info.json`. No runtime Git process is invoked. The packaging
job supplies `BATC_BUILD_COMMIT` to PyInstaller and Rust `option_env!`; native
version comes from `CARGO_PKG_VERSION`. Missing commit means unknown in both
locales. Browser assets use the serving Connector build and remain reproducible.

## Save boundary

Browsers use a Blob download and report only that the download started. Native
saving confirms completion after its write receipt and does not rely on Blob support across
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
