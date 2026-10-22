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

In the desktop app, pasting image files is refused before an attachment or upload operation is created;
use Choose files or explicitly enable file drop instead. Text paste and browser attachment flows are unchanged.

Native clipboard-image paste is a follow-up requiring a Rust-side clipboard read into the existing spool,
with no WebView clipboard API and no raw-byte IPC.

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
