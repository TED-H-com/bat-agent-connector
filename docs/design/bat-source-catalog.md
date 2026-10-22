# Native BAT admission and profile catalog

This is the native parsing part of D3 in [Fleet configuration](fleet-configuration.md).
It supplies E01/E11 prerequisites; it does not complete E00–E11, R0–R5 or A01–A40
acceptance. Python Connector remains the remote work authority. A remote Connector
cannot read a desktop's BAT files. This module adds no command, IPC, HTTP route,
source descriptor, setup UI, generator, Kit change, enrollment or schema migration.

`credential::AdmissionStore` implements `BAT_SOURCE_POLICY = 1` alongside Python's
`connection_sources.parse_token_store(..., ADMISSION)`. Both assert against the
single `admission` entry in
[`bat-source-policy-v1.json`](../../tests/fixtures/bat-source-policy-v1.json).
Its producer and regeneration command are recorded in the fixture's provenance;
expectations must be regenerated through that producer. `python_runtime` and
`rust_legacy_fleet` retain their reviewed values and behavior. The native legacy
resolver is never admission. Policy-bound references are described beside the
existing [credential reference contract](fleet-credentials.md).

Admission requires an object with literal `enc:false`, string `data` containing
JSON, and an exact token property. It refuses BOMs, malformed Unicode/numbers,
the 128th nested container, and duplicate decoded/case-competing keys at every
level. Store bytes are capped at 1 MiB; a nonempty token is capped at 64 KiB UTF-8
and may not contain NUL. Missing keys, case-only mismatch, invalid token, encrypted
data, non-string data, ambiguous JSON, invalid format and unreadable files retain
the same `state`/`reason` as Python for the shared policy inputs. A format error
takes precedence over ambiguity: duplicate keys are classified only after a full
valid parse, including numbers, Unicode and container depth. Secrets are not
normalized or trimmed. The Unicode table limit is described below.

File admission also requires valid UTF-8 and, on POSIX, ownership by the real
`getuid()` user and `mode & 0o077 == 0`, matching Python's private-file validation.
Unreadable, foreign-owned, group/other-accessible and invalid-UTF-8 stores report
`unavailable / TOKEN_STORE_UNREADABLE`. Windows keeps its existing file behavior.
These checks apply to the catalog and policy-bound references; absent-policy
legacy references keep their existing file access and token-resolution behavior.

## Listing choices

`source_catalog::parse_profile_index` and Python's `parse_profile_index` implement
one listing rule. A 1 MiB UTF-8 JSON object contains at most 200 `profiles`; missing
`profiles` is empty. Invalid JSON, duplicate decoded/case-competing object keys,
invalid nesting/Unicode/numbers, a non-object root, non-array profiles or too many
entries yields `PROFILE_INDEX_INVALID`. BOM is invalid. Root extension fields
and unknown per-profile fields are ignored.

Listing skips non-remote or incomplete entries individually. This preserves usable
choices when one BAT record is incomplete and permits additive fields from a newer
BAT. Unknown fields therefore cannot silently erase every profile. Each skipped
entry has only its zero-based position and a fixed reason: `PROFILE_NOT_REMOTE`,
`PROFILE_INCOMPLETE` or `PROFILE_ID_AMBIGUOUS`. IDs competing after Unicode lowercase
exclude every competing entry, even if one is local or incomplete; order never picks
an identity. Other complete records remain visible.

Each complete remote profile has `id`, `name`, `endpoint` (`host`, `port`, `url`),
normalized `fingerprint` and `remote_profile_id`. Text is nonempty, trimmed, at most
256 Unicode scalar values, and contains no C0/C1 controls. Fingerprint input is at
most 200 scalars; the existing Python normalization accepts optional `SHA256:`,
colons and internal spaces and emits 64 uppercase hex digits. Missing/empty names
default to the ID; missing/empty remote profile IDs default to `default`, retaining
the listing's existing false/empty fallback. Hosts must be ASCII domain/IP text or IPv6;
IPv6 URLs are bracketed, and the host field omits brackets. Whitespace, URL
credentials/path/query/fragment delimiters, backslashes and IPv6 scope identifiers
are refused. Ports are integers in 1–65535 or ASCII decimal strings of at most
256 characters; boolean and
floating-point coercion is refused. C1 controls, malformed URL hosts, duplicate IDs
and noninteger ports are now consistently excluded by both listing readers.

The listing/catalog reason is `PROFILE_CATALOG_AVAILABLE`, `PROFILE_INDEX_PARTIAL`
or `NO_COMPLETE_PROFILES`. The native catalog additionally attaches admission
`token: {state, reason}` to every complete remote profile, including unavailable
ones, so a person can distinguish profile completeness from usable credentials.
Python setup projects the same complete entries through its existing output fields
and retains its existing `profiles_error` strings and successful `None` error.
Its existing output has no diagnostic field; the shared parser retains diagnostics
for future consumers without changing that setup API.

The Kit's own reviewed-copy `inventory::ProfileIndex::parse` and
`configuration_issues` stay strict. A kit drift check and a list of user choices
answer different questions; the permissive listing never grants drift acceptance.

## Read and output boundaries

`source_catalog::read(roaming)` calls existing `data_directory` once per catalog
read: new directory, then legacy only when absent. Once selected, absent or bad
index/store files never trigger fallback. A later invocation rechecks the selection.
The adapter reads only `profiles/index.json` and `profiles/remote-tokens.enc.json`
through the existing bounded regular-file/no-descendant-link adapter. It performs
no writes, BAT file locks or process contact. Directory failures return fixed
`DATA_DIRECTORY_*` codes; missing/unreadable indexes return `PROFILE_INDEX_UNAVAILABLE`,
while readable UTF-8 containing invalid index JSON returns `PROFILE_INDEX_INVALID`;
unreadable stores leave profile metadata visible with `TOKEN_STORE_UNREADABLE`.
Blocking filesystem work belongs off the UI thread. This is not an atomic snapshot
against a same-account editor; later resolution rereads the selected store.

Only allowlisted profile metadata and fixed status codes enter the output. Unknown
index fields and all raw JSON/OS/parser errors are discarded. Neither catalog
output nor its `Debug` contains the selected absolute filesystem path. Index
metadata itself remains user supplied and is not an authority grant.

Secret-bearing surfaces are deliberately restricted:

- File buffers use `Zeroizing<Vec<u8>>` allocated once with capacity `metadata.len + 1`
  after the size check. Fixed-slice reads refuse growth beyond that observed length,
  so reading cannot reallocate and leave old secret copies. Successful outer and inner token JSON
  trees use `SecretJson`, whose drop recursively scrubs strings and keys, including
  unrelated profiles. Neither `AdmissionStore` nor `SecretJson` has Debug/Serialize.
- Catalog lookup returns only `TokenStatus` constants. No token accessor is public;
  only the credential resolver can copy one admitted token to a zeroizing string
  and then `ProbeCredential`, which has neither Debug nor serialization.
- Admission failures, reference validation and IO errors return fixed codes,
  with no input excerpt, token bytes, profile ID, selected path or OS exception.
  Legacy runtime keeps its fixed error behavior. Python token reprs still exclude
  their secret fields; setup projects only availability/state.

Serde JSON's unescape scratch buffer briefly holds the decoded `data` string even
on the success path and is freed without zeroing. The retained `SecretJson` tree
is scrubbed; this does not erase that separate parser copy. No new dependency or
claim of complete parser-memory zeroization is introduced. Parser failure
allocations, OS/crypto internals and later transport copies also remain outside
the guarantee. Tests use synthetic JSON and temporary
disk files, never personal BAT stores, credentials or live BAT processes. Linux
checks are separate from Windows/macOS CI and installed-account/native acceptance.

## Known differences and limits

- Case competition uses each runtime's Unicode lowercase tables. Measurements use
  Python 3.10.20 (Unicode 13.0.0), Python 3.13.13 (Unicode 15.1.0), and the pinned
  native runner's rustc 1.98.1. The native Unicode measurement test records its
  actual compiler and `std::char::UNICODE_VERSION` in the check-service log.
  For the Cyrillic Tje pair U+1C89/U+1C8A added in Unicode 16, the older Python
  tables treat the keys as distinct; newer native tables detect competition.
  Policy 1 does not bundle a replacement Unicode table. ASCII hosts avoid Unicode
  host normalization differences; profile/token IDs retain their existing rules.
- Relative to the old `_profiles` parser, `%` in DNS hosts and DEL are refused;
  port strings `" 9876"`, `"+9876"`, `"9_876"` and full-width digits are refused;
  duplicate/case-competing JSON keys and `NaN` invalidate the whole index. Boolean
  and floating-point ports, C1 controls, backslashes and IPv6 scopes are also
  refused. These are deliberate restrictions, not legacy runtime changes.
- Missing `index.json`, a missing BAT root or `profiles/`, and a linked `profiles`
  directory yield `PROFILE_INDEX_UNAVAILABLE` in both admission IO readers.
  Python setup retains its existing message, "BAT profiles are unavailable; enter
  the host and its trusted fingerprint", for unavailable or invalid indexes.
  Formerly its listing followed a linked profiles directory; listing now refuses
  it to match native without relaxing admission. Existing Python runtime token
  reads retain their previous behavior. These disk layouts have shared vectors;
  POSIX symlink/mode fixtures are skipped on Windows, not used as ACL evidence.
- A later generator must account for policy-bound `TOKEN_*` reasons reaching
  Fleet status. Older desktop readers reject inventories carrying `source_policy`;
  generation needs a compatible desktop version before publishing such entries.
