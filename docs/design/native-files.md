# Native attachment transfers (R05 / T08)

This client slice reuses central `artifact.upload`, immutable ArtifactRefs, operation readback/cancel,
and the exact content routes. It does not add a remote filesystem proxy, another task journal, or
change manual capture policy. Windows is the first native acceptance target; browser fallback stays.

## User flow and boundary

Existing attachment rows offer Choose files, explicitly enabled drop into the active window, progress,
Stop, Retry, Cancel upload, Preview and Save As. The native picker/drop event grants a regular file;
Rust copies bounded bytes into a private app-local spool, hashes them and returns an opaque handle
plus name/size/type/digest. Dropped selections require an explicit Upload click; a two-second native lease is renewed only while the armed attachment form is visible. Paths never cross IPC or enter prompts. No JavaScript filesystem, dialog,
raw drag-event, HTTP or shell plugin permission is added. The former raw-byte upload IPC permission is removed. Native source bytes are never written.

In an attachment-enabled desktop composer, an image paste arms `native_files_paste_arm` and then
calls `native_files_paste`, each with only the draft ID. The WebView inspects paste-event MIME metadata
only; no WebView clipboard API and no raw-byte IPC are used. Rust reads one OS clipboard image,
spools it through the same file manager as the picker and returns the identical `Receipt` fields.
The existing attachment rows upload and verify through `native_files_upload`; Stop, Retry and Cancel
retain their existing operation semantics. Manage, upload-allowed and attachment-support gates run
before either native call. Picker and paste share one in-flight selection guard; another paste while
selection/upload/readback is pending makes no native call. A selection/upload failure displays the
error and re-reads native status so the existing row reflects its retained transfer stage.
Paste errors are presented by the composer, including its typed unsupported refusal. Picker errors
stay in the native attachment notice. Both paths still re-read native status after failure.
Text paste and browser attachment flows are unchanged.

The native lease binds the current file scope (including generation) and a valid draft ID, lasts less
than two seconds, and is consumed by the first read attempt, including a refused attempt. Arming and
reading require the focused main window. Missing, expired, reused or foreign-draft leases refuse
before any clipboard access; scope is checked before and after reading and during spooling. Rust
cannot prove a keypress or independently observe the attachment form: this bounds access to a focused
window with that attachment form open, using the form's short lease. It is not a cryptographic proof
of a physical paste gesture. There is no background polling of clipboard contents.

The window thread consumes the lease and checks focus/scope, copies bounded OS bytes into owned PNG
or DIB buffers, then releases the clipboard. macOS rechecks `changeCount` after the copy. The reader
cannot accept an encoding/spooling callback. PNG validation, DIB encoding, hashing, file writes and
`sync_all` run in `spawn_blocking`, using the same spool path as picker selections. Scope is checked
before and after processing and again on return to the async command; the short lease state lock is
released before the OS reader runs.

Windows reads the registered `PNG` format first, otherwise `CF_DIB` (including the OS-synthesized
DIB for `CF_BITMAP`) and encodes PNG in Rust using the existing pinned `png` crate. Supported DIBs
are 24/32-bit uncompressed RGB or 32-bit standard RGB bitfields, top-down or bottom-up; unsupported
headers, masks, compression and embedded profiles return a typed unsupported reason. macOS reads
only `public.png` from the general pasteboard; other image formats are unsupported. Linux remains
unsupported. Copied files (`CF_HDROP` / pasteboard file URLs) are never read.

Results use `pasted-image-<UTC Unix milliseconds>Z.png` and `image/png`. PNG input and final files
are at most 16 MiB (or the smaller central limit); dimensions are at most 8192 per side and
16,777,216 pixels, with at most 64 MiB decoded pixels. OS byte lengths are checked before the owned
copy; the worker checks dimensions before allocating pixels. DIB input is bounded to 64 MiB plus its
124-byte header. After PNG validation, only bytes through a complete, zero-length `IEND` chunk with
its valid CRC are retained; OS allocation padding is discarded before size/digest/spool publication.
A missing, malformed or truncated `IEND` is `invalid_image`.
PNG parsing has a separate 64 MiB decoder allocation limit; animated PNG is unsupported. The shared
64 MiB spool and 1,000-record limits apply, with no separate clipboard cache. OS clipboard providers
may allocate their own buffers before returning; these limits cover the client's copies and decode.
Unsupported platform/format reports `CLIENT_ACTION_NATIVE_PASTE_UNSUPPORTED`; busy, malformed,
expired-lease and oversized failures do not masquerade as unsupported platforms.

Commands accept only handles, draft IDs, exact ArtifactRefs and fixed controls. Local transfer state
is scoped by endpoint/actor/contract, backend/principal and the original credential (Rust-only hash),
in addition to the active native generation. Two credentials with identical actor/scopes are distinct
for native transfer recovery. The current central upload contract is actor/manage based; this stronger
client restriction does not claim that central ordinary uploads enforce original-token identity.

Upload intent/key is persisted before its first POST. Every operation response must match the saved
key/action/target/params/preconditions/actor and accepted operation ID. Completion requires an exact
ready revision with the original digest, size and creating operation. Lost replies retain the original
intent and query/replay its key; no automatic fresh key after uncertainty or terminal failure.
Native reconnect/replacement invalidates in-flight work; it never adopts another identity's receipt.

Upload is streamed with fixed Content-Length, bounded progress and a transfer timeout. The smaller
of central limits and native bounds applies: 16 MiB/file, 20 selections, 64 MiB local spool. Progress
means bytes supplied/read locally, not acknowledged durable remote bytes. Verification is a separate
stage. Central only supports retry from byte zero, which is also the download retry policy; no Range
or byte-offset resume is claimed. Retry preserves the same upload operation. Explicit cancellation
uses the central operation contract and is confirmed through readback; Stop only halts local I/O.

Save As obtains its destination only from the OS dialog. Rust reads the fixed ref's metadata/content,
verifies size/digest, flushes a temporary file in the selected directory, and publishes without replacing
an existing destination. On a name collision, choose another name or Cancel. No overwrite mode is
provided. Publication uses an atomic create-new hard link within the chosen directory. A destination
filesystem without hard-link support refuses safely and requires another folder; no overwrite fallback is used. Only this transfer's temporary files may be removed. Local cache data lives below the fixed app-local directory, with an ownership marker, Unix owner permissions, a 1,000-receipt bound and no automatic replay on startup. A damaged cache refuses file operations while retaining its files for recovery. After restart, upload spools/receipts
can recover under the original credential and the retained frontend draft; unfinished downloads are shown when an attachment form is opened and request a destination again, rather than
reopening a saved arbitrary path. User-visible local receipts remain distinct from central operations.

Preview is bounded, verified data only: UTF-8 text (including HTML/SVG/Markdown shown literally) and
static PNG up to 2 MiB and one megapixel, decoded with bounded allocation and re-encoded without metadata. Text is limited to 256 KiB; other raster formats and animated images use Save As. No active document, remote URL, untrusted file navigation or
additional native authority. Unsupported/oversized content remains downloadable.

## Implementation and validation

The Rust file manager owns opaque capabilities, bounded spool/receipts, OS dialogs/drop, streaming,
and destination publication. Bridge helpers preserve native credentials, fixed paths, redirect refusal
and active identity checks. The shared frontend retains identity-scoped draft/operation keys and uses
compact existing rows, colors and en/zh-TW labels. Remote manual capture remains its separate reviewed
relative-path form; local selection does not confer managed ownership or alter the remote source.

Tests use mock dialogs/drop and temporary files, loopback central responses and actual central fixture
artifacts. Cover changed bytes, malformed/stale/foreign handles, wrong receipts, credential replacement
with identical claims, interruption/restart/cancel races, existing destination preservation, preview
limits and forbidden plugin access. Windows compile/packaging is distinct from actual installed picker,
drag/drop, tray and Save As evidence. No live hosts or installations are used during implementation.

Dialog dependency: official `tauri-plugin-dialog` 2.8.1, Rust API only. Directory-capability operations
use `cap-std`; upload streams use `reqwest`/Tokio. Pin lockfile changes and review new dependency scope.
References: [Tauri dialog](https://v2.tauri.app/plugin/dialog/), [directory capabilities](https://docs.rs/cap-std/4.0.3/cap_std/fs/struct.Dir.html), and [bounded PNG decoding](https://docs.rs/png/0.18.1/png/struct.Decoder.html).

Run the native real-central fixture with `BATC_NATIVE_TEST_PYTHON=/absolute/dev/venv/bin/python npm run test:native-files` from `desktop`. It uses this checkout's Python source, compiled native Rust adapter and temporary directories only. The interpreter needs the project's dev dependencies. Set `CARGO_TARGET_DIR` explicitly if sharing an existing build cache; no Python environment or host configuration is modified.


## Native command contract

| Command | Input from the main WebView | Result |
| --- | --- | --- |
| `native_files_pick` | `draftId` (bounded opaque draft identifier) | OS-selected, hashed upload receipt handles; no paths or bytes |
| `native_files_paste_arm` | `draftId` | Focused main-window, two-second, single-read lease scoped to this draft and generation |
| `native_files_paste` | `draftId` | One bounded OS image as the same picker receipt; consumes the lease |
| `native_files_drop_target` | `draftId`, `enabled` | A short-lived target for native drop events; an old draft cannot disarm a newer one |
| `native_files_status` | none | Credential-filtered bounded local receipts and progress |
| `native_files_upload` | `handleId` | Starts/rechecks the fixed persisted upload intent |
| `native_files_control` | `transferId`, `stop / retry / check / cancel_upload / discard_local` | Fixed control; download retry opens Save As again |
| `native_files_save` | exact `{artifact_id, revision, digest}` | OS destination and a local receipt; no destination path returned |
| `native_files_preview` | exact `{artifact_id, revision, digest}` | Verified literal text or sanitized PNG bytes, bounded before IPC |

Rust validates restored manifests against the fixed `artifact.upload` envelope before any request,
and rereads an operation's exact binding before cancellation. A corrupt cache cannot introduce another
action. Cancel-requested uploads are followed through readback without sending another byte body.
Save/upload progress receipts describe local work; they are not a replacement for the central operation
history or an event-stream acknowledgment. Removing a reference from a draft does not cancel a transfer.
If the original credential is unavailable, the retained draft entry stays visible and no replacement-key
or replacement-credential upload is issued. OS dialog callbacks are serialized independently of active
transfers. Restart never automatically replays a transfer.

Clipboard dependencies reuse locked `windows-sys` 0.61.2 APIs (DataExchange/Memory) and add direct,
exact-pinned access to the already locked `objc2-app-kit` / `objc2-foundation` 0.3.2 APIs. No new
package versions or image decoding stack are introduced. Windows CI runs a real OS bitmap round trip
in the native unit suite; an unavailable clipboard fails with an explicit device-acceptance diagnostic,
never a silent skip. CI compilation and IPC fixtures do not prove installed-device paste acceptance.
Windows bitmap synthesis follows [Microsoft's clipboard formats contract](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-formats);
macOS uses [NSPasteboard](https://docs.rs/objc2-app-kit/0.3.2/objc2_app_kit/struct.NSPasteboard.html).
