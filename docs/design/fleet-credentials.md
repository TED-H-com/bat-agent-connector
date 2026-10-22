# Native Fleet credential resolution

This is a native-only reader for the reviewed Kit `Resolve-FleetCredential` /
`Set-FleetCredential` formats. It adds no IPC, credential editor, store migration, environment
lookup or supervisor wiring. Private deployment files and installed credentials are never fixtures.

`CredentialRef::for_bat(validated_inventory, logical_host)` binds the BAT profile token reference.
`CredentialRef::for_connector(validated_inventory)` binds the configured Fleet observe credential.
`resolve(selected_bat_data_directory, reference)` returns an owned `ProbeCredential`; neither
reference nor credential implements `Debug` or serialization. It does not search another BAT data
directory, profile, environment variable, desktop vault or admin token when resolution fails.
The supervisor must resolve against its current validated inventory/data-directory selection and
revalidate configuration before transport and publication. A credential read is not an authority grant.

| Source | Exact supported format | Refused forms |
| --- | --- | --- |
| `bat-profile-token` | `profiles/remote-tokens.enc.json`, JSON `enc: false` and string `data` containing JSON `tokens`; select only the configured profile property | Encrypted store, missing/string/numeric `enc`, non-string token, duplicate decoded keys including case variants, missing profile |
| `windows-dpapi` | `fleet-credentials/<validated name>.dpapi`; hexadecimal bytes produced by Windows PowerShell `ConvertFrom-SecureString` without a key | Non-Windows, malformed hex, keyed AES export, failed decryption, invalid/empty UTF-16 or excessive plaintext |

Profile-property lookup ignores case like PowerShell, after rejecting all duplicate decoded keys.
Secret values are never trimmed or normalized. Kit writes UTF-8 without BOM; UTF-8 BOM is also accepted.
Other file encodings are conservatively unsupported. Names use the existing 1–64 ASCII
alphanumeric/underscore/hyphen rule, starting with an alphanumeric character. Unknown reference kinds,
including `env`, are not in the reviewed inventory schema and remain refused.

BAT entries in the inventory's `credentials` table may additionally carry
`source_policy: 1`, for example `{kind:"bat-profile-token", profile_id:"remote-1",
source_policy:1}`. The host's `bat.credential_ref` remains its existing logical
table key. Absence preserves the legacy resolver above, including case-insensitive
lookup, BOM handling and `CREDENTIAL_MISSING` errors; reading never adds the field
or rewrites inventory bytes. Any present value other than integer `1` is invalid,
including null, booleans, strings and floating-point values. DPAPI entries do not
accept the field. No generator writes it yet.

Policy-bound resolution uses [BAT admission](bat-source-catalog.md), with an exact
decoded token key and its fixed `TOKEN_*` reason codes. A case-only change after
admission fails with `TOKEN_KEY_MISMATCH`; it cannot fall through to legacy lookup.
Unreadable files report `TOKEN_STORE_UNREADABLE`. Neither path searches another
store or retries with another credential. Existing inventories and the legacy
Fleet vector test remain unchanged.

Kit's writer protects UTF-16LE bytes using Windows DPAPI CurrentUser, no additional entropy, then
hex-encodes the blob. The reader uses `CryptUnprotectData` with no entropy or prompt and with
`CRYPTPROTECT_UI_FORBIDDEN`, then strictly decodes UTF-16LE. These semantics match the
[PowerShell implementation](https://github.com/PowerShell/PowerShell/blob/master/src/System.Management.Automation/security/SecureStringHelper.cs).
There is no custom parser for opaque DPAPI internals: compatibility with the CurrentUser writer does
not independently attest which scope an arbitrary preexisting blob was originally protected under.
Windows validates the blob. The
[DPAPI API contract](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
distinguishes CurrentUser protection from LocalMachine protection. The trusted credential directory and
configured reference remain prerequisites; Connector readiness separately requires exactly `observe`
scope. Successful decryption alone never establishes that scope or permits mutation.

Reads are limited to regular files under the two fixed subdirectories of the selected absolute data
root. Descendant symlinks/reparse points are refused; Unix opens also refuse final symlink/FIFO swaps.
The retained file is size-checked and read with a hard byte cap: 1 MiB token store, 256 KiB protected
hex file, 64 KiB decoded UTF-8 token (and at most 128 KiB intermediate UTF-16 bytes). Empty and embedded
NUL secrets are rejected. These are local file byte bounds, not a filesystem wall-clock deadline or
an atomic defense against an uncooperative same-user editor replacing ancestor directories.
The runtime must keep blocking filesystem/DPAPI work off its UI thread.

Owned file buffers, retained parsed secret strings, UTF-16 buffers, tokens and Windows plaintext
allocations are zeroed on drop; allocated Windows output is then freed. Parser failure internals,
OS/crypto internals and later transport libraries may make temporary copies outside this guarantee.
Errors expose only fixed missing/invalid-reference or admission reason codes, never paths, profile names, credential
contents or OS exception text. The resolver never changes a credential file.

Linux tests use synthetic JSON and temporary files, including missing, malformed, oversized, linked,
FIFO, replaced and removed stores and lack of cross-profile/desktop-token fallback. Windows-only tests
use fresh temporary files and synthetic values: native DPAPI tamper/entropy/replace/remove cases plus
real system PowerShell's no-key format in both directions. No actual BAT directory or credential
manager record is touched. Windows compilation is not execution evidence; installed-account DPAPI,
roaming profile, cross-account and packaged runtime integration remain separate acceptance gates.
