# Native save for a fixed Project Brief

The packaged Dashboard may ask Rust to save one report already selected through
the [Brief JSON read contract](pm-brief-exports.md). This command does not accept
content, a URL, a filesystem path, an artifact identity or an overwrite flag.

```json
{
  "reference": {
    "workspace_id": "ws_11111111111111111111111111111111",
    "snapshot_id": "pmsnap_22222222222222222222222222222222",
    "format": "html",
    "locale": "zh-TW",
    "expected_view_digest": "3333333333333333333333333333333333333333333333333333333333333333",
    "expected_output_digest": "4444444444444444444444444444444444444444444444444444444444444444"
  }
}
```

`native_brief_save` is available only to the local main window. Its exact reference
requires lowercase IDs/digests, one of `markdown`, `html`, `svg`, and locale `en`
or `zh-TW`. Unknown fields are rejected. The native status field `brief_exports`
advertises this native command, separately from the central renderer capability
and current source permission. Older desktop builds without the field do not
advertise native report saving.

The command holds the existing native file-dialog lock and captures the verified
Bridge `FileScope` (backend, principal, credential and connection generation). It
fetches the fixed JSON through the ordinary allowlisted GET before opening a save
dialog. After the user chooses a local destination, it fetches the same fixed
report again through that original scope. A changed source projection or output
digest requires a fresh user selection; it never silently saves an earlier cached
view, substitutes `latest`, or substitutes newly filtered content.

Both reads validate report version, workspace/snapshot identity, format, locale,
media type, original/view/output digest shapes, renderer/template versions,
complete coverage metadata, SVG page bounds and passive-report semantics. Content
must be a UTF-8 string of the exact byte length and SHA-256 supplied by the central
renderer. Limits remain 1,500,000 content bytes and 2,000,000 serialized JSON bytes;
no partial report is written. Source text is saved literally and is never opened,
interpreted as app HTML, sent to an external URL or executed.

The English or zh-TW native save dialog supplies the only destination capability.
The generated filename uses the snapshot ID, locale and fixed extension; the user
may change the filename. The existing `Destination` writer saves via a temporary
file in the selected directory, checks exact bytes and digest, and publishes with
atomic create-new semantics. It rechecks the captured local Bridge scope while
writing and immediately before publication. Existing files and destinations that
appear during the write are preserved, including symlinks. Only the command's own
temporary fragment is removed on failure. A filesystem that cannot support safe
create-new publication returns `DESTINATION_UNAVAILABLE`.

Cancellation returns `null`, without a file or central write. Successful saving
returns `{stage: "saved", workspace_id, snapshot_id, format, locale, view_digest,
output_digest, byte_length, display_name}`. `display_name` is the selected basename;
the local path, content, credentials and backend configuration are not returned.
No ordinary artifact or central operation is created and no transfer spool is
persisted. A lost native reply is not an instruction to overwrite: the UI should
ask the user to check the selected destination before another explicit save.

Error prefixes are `BRIEF_EXPORT_REFERENCE_INVALID`, `BRIEF_EXPORT_INVALID`,
`BRIEF_EXPORT_CHANGED`, `BRIEF_EXPORT_UNAVAILABLE`, and
`BRIEF_EXPORT_SAVE_INTERRUPTED`, plus the existing destination and connection
errors. The shared UI localizes these states and treats cancellation separately
from failure. Already authorized offline files cannot be revoked by later source
permission changes; every new command performs fresh reads.

Unit fixtures cover exact references and native permissions, UTF-8/digest/type
validation, bounds, source changes or withdrawal while the dialog is open, changed
connection identity, literal saved bytes, existing-file preservation and fragment
cleanup. The transport fixture serves only synthetic loopback responses; it uses
no real central server, Fleet or model. Native OS dialog interaction remains
separate from these automated file and protocol checks.
