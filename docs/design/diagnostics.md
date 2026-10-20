# Local diagnostics

`desktop/src/diagnostics.js` selects named fields and runs a serialized JSON
scrubber. `batc.diagnostics.v1` contains client/build metadata, anonymous hosts,
client-read session counts, optional history reason counts, failed/uncertain
operations from one page, a 50-entry memory-only failure ring, source availability
and static log/state location templates. No server endpoint, journal/schema step,
telemetry, credentials fallback or central mutation is introduced.
All OS templates for managed desktop and standalone installations are included;
the client's OS does not establish the Connector's OS or actual state directory.

## Source reads and compatibility

`batc.diagnostics.v1` evolves additively. Existing `sources` entries retain the
`available` / `not_available` strings for old consumers. The additional
`source_reads` map covers `capabilities`, `hosts`, `operations`, `managed_setup`
and `observation`. Each entry contains `status`, `http_status`, `code` and
`read_at`; the binary map is derived from this same classification. Readers may
ignore additional fields. Changing existing field types or enum meanings requires
a new schema string.

| Status | Meaning |
| --- | --- |
| `available` | The read completed and its data passed the current revision fence. |
| `not_permitted` | Current capabilities lack the required scope; no request is made. |
| `not_offered` | This installation does not provide the source; no request is made. |
| `failed` | The attempted read failed, or its data was discarded after access changed. |
| `not_attempted` | No read was recorded, or failed capabilities prevented deciding whether to read it. |

`read_at` is the client time when a read completed or a decision to skip it was
made; it is null if no read or decision was recorded. It is not a host observation
time. Failed reads retain their HTTP status (null for transport failures) and
validated error code, with `HTTP_ERROR` / `NETWORK_ERROR` fallbacks. A successful
read discarded by the withdrawal fence has code `SOURCE_ACCESS_CHANGED` and null
HTTP status; an original refusal keeps its own status and code. Other statuses
have null HTTP status and code. Server free-text messages are never selected.

Without a successful capabilities read, dependent sources are `not_attempted`,
not inferred to lack scope. A non-managed installation's setup source is
`not_offered`; a managed installation without `manage` is `not_permitted`.
The absent observation status endpoint is `not_offered` and is never probed.
Each anonymous host additionally retains `last_attempt_at` and `last_success_at`
through the existing timestamp validator; absent or invalid times stay null.
Neither `read_at` nor `exported_at` replaces those observations. Unknown counts
remain null, while counts from a known empty page can remain zero. All additional
fields pass the unchanged serialized scrubber as the final gate.

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
copy cannot identify a checkout on `PYTHONPATH`. Wheels read their packaged
`bat_agent_connector/build-info.json` with `source: package`; conflicting embedded
and VCS commits produce an unknown commit with `source: vcs`. Packages without
provenance and editable local checkouts without matching VCS metadata have a null
commit. A frozen runtime reads only
its packaged `build-info.json`. No runtime Git process is invoked. The packaging
job supplies `BATC_BUILD_COMMIT` to PyInstaller and Rust `option_env!`; native
version comes from `CARGO_PKG_VERSION`. Missing commit means unknown in both
locales. Browser assets use the serving Connector build and remain reproducible.
The Python build is captured once at module initialization, alongside the loaded
version; later in-place metadata changes cannot identify that running process.

The Hatch build hook accepts a full 40-hex `BATC_BUILD_COMMIT` (normalized to
lowercase). In a clean Git checkout it must equal HEAD; a mismatch is unknown.
Without a valid override, the hook uses that clean checkout's HEAD.
Dirty tracked inputs always produce an unknown commit, including with an override.
Outside any checkout, without `PKG-INFO`, an explicit override can identify an
exported source candidate: there is no independent checkout identity to contradict
it. This remains a build-time declaration, never a guessed Git identity.
An sdist carries that attribution into a wheel without consulting an enclosing
checkout; a conflicting override makes it unknown. No provenance is guessed from
an archive without it. This commit identifies the build's declared source
candidate; it does not prove that the wheel's bytes match a published release.
Release manifests and their verification remain separate.

Editable builds skip provenance and workflow file copies: their imports resolve
to `src/`, without a partial `bat_agent_connector/` in site-packages. The existing
distribution-path guard keeps their build identity unknown. Normal Python
bytecode caches may be created; source and provenance files are not modified.

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
