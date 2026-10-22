# Native Fleet creation evidence

`fleet-core::creation_evidence` provides bounded records, guarded storage contracts,
and a pure ownership resolver. It has no production Windows adapter, native route,
IPC, configuration writer, or public Apply entrypoint. Native integration must
supply `EvidenceIo` on one blocking thread with Launcher then Monitor held. An
unsupported native platform must refuse before a claim; these portable contracts
do not synthesize Windows identities from Unix accounts.

A readable file, familiar path, ACL, startup role, loaded installation snapshot, or
syntactically valid owner reference does not establish creation authority. A
native first-create operation reserves four absent slots before generator use.
Existing external configuration is ReferenceOnly when its origin is known;
unproven state is Unknown. No API here adopts it, repairs its ACL, rolls it back,
or changes the backend to obtain ownership.

## Reference and identities

The sole formatter emits exactly 115 bytes:

```
fleet-creation.v1.<32-lowercase-hex-Fleet-ID>.<64-lowercase-hex-receipt-hash>
```

The exact parser first uses the existing `OwnerEvidence::valid` syntax authority.
It recognizes only this version, case, separators, ID and hash. The identifier is
never a path or URL and cannot authenticate itself. `OwnerEvidence.installation_id`
is the independent Fleet creation ID; `owner_sid` is the creator's Windows SID.
The startup installation reference retains its exact UUID spelling. A managed
Connector installation ID remains a separate 32-hex ID, bound to its verified
manifest, actor, server and principal. No ID is stripped, replaced or derived from
a pathname. Production account SIDs come from the actual token.

## Guarded operations and replay

1. `reserve` validates the typed receipt, native context and all four absences,
   then writes only through `EvidenceIo::create_only`. That boundary must use
   protected create-new storage, authorize each directory/staging/final effect,
   call `WriteFence::verify` immediately before final publication, synchronize
   and read back. A namespace left without a valid receipt is unproven residue.
   A retry cannot mint a replacement identity. `capture` reopens durable evidence
   across same-SID sessions; new reservations use their creator's current session.
2. `capture` reads the entire fixed authority namespace and all slots twice,
   checking exact bytes, native context and login. The native boundary independently
   validates canonical paths, parent/object identities, protected ancestors,
   directory-creation references, no-follow reads and external origin. It must
   reject unknown entries and unsafe/orphan directories, not silently skip them.
3. Read frozen plans, journals, decisions and migration Saved records through
   their original validators under the same guards. Supply fresh views to
   `CapturedEvidence::resolve`; do not reuse them across effects, guard release,
   review or await. History loading does not call `ownership` recursively.
4. `append` admits a fresh native-accepted frozen review only at a settled head
   with all four current baseline states Owned. The predecessor filename is
   create-only, preventing competing successors. Each link seals the predecessor's
   exact terminal journal/decision, closure or migration-completion bytes. A
   no-journal closure requires an unchanged baseline; only a zero-step review may
   close automatically using its original acceptance. Nonempty admissions require
   explicit abandonment. A review with an existing journal cannot be readmitted.
5. Replay begins with four reserved absences. Published transitions consume their
   prior versions. The latest reached Intended step permits its exact before or
   after version; unreached planned output grants nothing. Deleted Published
   files, older bytes and third bytes do not rewind to reservation authority.
   Abandonment preserves only reached, independently proven versions and keeps
   third/unreadable slots unproven. A fresh review cannot bless them.
6. A migration intent must start from an already Owned exact pointer version.
   Its completion binds an original validated Complete Saved record, exact
   before/after pointer bytes, native paths, SID/session, review and restore source.
   It changes only Installation. A restore requires an earlier bound reverse
   pair. The resolver never begins or advances a migration.
7. `Resolution::ownership` also compares the caller's actual bytes to the captured
   observation. Absence remains distinct from an empty file, although an owned
   absent slot uses `sha256(empty)` in the existing ownership interface. Require
   `baseline()` for all-four admission/final preflight, including unchanged roles.
   Require the captured/current execution session for pending effects and the
   separate native review/lifecycle/expiry authorizer. Ownership is not execution
   authorization. A changed session permits inspection/abandonment, not continuation
   of the old effect.

Create-only failures preserve possible effects. Missing or future-version evidence
refuses mutation. Older installations without a receipt retain reference/manual
use; they are never enrolled retroactively. An older binary is not assumed to
understand these records. Returning after a downgrade requires fresh exact replay.
There is no same-account hostile-tamper or backup anti-rollback guarantee.

## Record bounds and public data types

All authority documents have exactly `version = 1`, `minimum_reader_version = 1`,
and a tagged `record` with `kind` and `body`. Parsers reject unknown fields,
duplicate decoded keys, missing fields (including explicit nullable fields), and
nesting deeper than eight containers. Hashes are 64 lowercase hex; IDs are 32
lowercase hex unless explicitly UUIDs. Paths are absolute UTF-8, at most 4096 bytes
and 4096 UTF-16 units; lexical validation does not replace native canonicalization.
Opaque acceptance/creation references are nonempty, at most 256 bytes, without
control characters. SID validation reuses the existing 184-byte bound.

| Public type | Fields and enforced bounds |
| --- | --- |
| `ObjectIdentity` | Volume `u64`, file ID 32 lowercase hex; actual identity is attested by the native boundary. |
| `StartupRole`, `StartupBinding` | Closed Client/Provider/Both; exact 36-character UUID spelling, role-record hash, positive `u64` generation. |
| `Creator` | SID, `u32` session, independent 32-hex reservation nonce. |
| `Scope` | Literal Windows platform; pointer, kit root, state root, startup-role record; state-root identity. Pointer is exactly state-root/fleet.json. |
| `DirectoryReservation` | ProvenExisting with identity and creation reference, or ReservedAbsent. At most eight exact permitted root/ancestor entries; no duplicate paths. |
| `SlotReservation` | Exactly four in Artifact order, exact derived path, literal absent and explicit null before hash, nearest existing parent and identity, 1–8 validated missing components (at most 255 bytes each). |
| `ManagedIdentity` | Independent ID, manifest hash, exact desktop-ID actor, bounded server/principal IDs; no credentials. |
| `ProvisioningReceipt` | Immutable tuple of the above and optional managed identity; at most 65,536 encoded bytes. |
| `Baseline` | Artifact and nullable hash; four ordered entries. Null is reserved absence, never an empty file. |
| `Binding` | Receipt hash, exact startup UUID, Fleet ID, SID/session, pointer and kit root. |
| `Terminal` | Closed combinations of journal/optional decision, closure, or migration-completion hash. |
| `PublicationLink` | Binding, predecessor and terminal, original frozen reference, current generator version, four baseline entries, native acceptance reference. |
| `CloseReason`, `AdmissionClosure` | Closed Unchanged/AbandonedBeforeEffect; link/binding, decision reference, unchanged four-entry baseline, journal-absent assertion. |
| `DirectoryCreated` | Binding, exact authorized directory, parent and new object identities. Fixed evidence subdirectories and admitted review-hash decision directories are allowed; arbitrary descendants are not. |
| `ManagedBinding` | Binding and verified managed tuple; conflicting rebinding refuses. |
| `MigrationIntent` | Binding, predecessor/terminal, migration ID, startup shortcut path, review, prior pointer hash, optional restore source, acceptance reference. |
| `MigrationCompletion` | Binding, intent hash, exact Saved hash and next pointer hash. |
| `Record` | Closed union of the seven document kinds (provisioning boxed); auxiliary documents at most 32,768 bytes. Deserialization alone is not authority. |
| `EvidenceKey` | Fixed provision/lineage/closure/directory/managed/migration namespaces; variable filename components are 64-hex hashes. |
| `ObservedSlot` | Absent, bounded Bytes/External, or Unavailable. Capture bounds Installation to 16 KiB and each other slot to 1 MiB. |
| `EvidenceIo` | Trusted synchronous platform boundary; seven methods listed below. No production implementation is supplied. |
| `WriteFence` | Opaque pre-write login, at most 4096 record hashes and four bounded observations. |
| `CapturedEvidence` | Opaque guarded receipt/record/observation capture; no deserializer or public constructor. |
| `Resolution` | Opaque replay result, at most two admissible versions per slot for active Intended recovery. |
| `FrozenHistory` | Immutable original-validator result; 32 MiB frozen-plan bound, exact reference/hash and original generator seal. |
| `PublicationHistory` | Immutable original journal/decision validator result; 32 KiB each, at most four reached steps/observations, original 1024 decision-entry limit. |
| `SavedHistory` | Immutable original Saved validator result; 6,000,000 encoded bytes, each decoded config/startup slot at most 1 MiB. Original ID/path/login/phase checks remain unchanged. Creation replay further bounds pointer payloads to 16 KiB and matches exact scope. |

`ARTIFACTS` is the fixed four-role array. `RECEIPT_LIMIT`, `RECORD_LIMIT`,
`HISTORY_LIMIT`, and `ENTRY_LIMIT` are respectively 65,536, 32,768, 1024 lineage
nodes, and 4096 authority entries. Bounds cause refusal, never truncation or pruning.
Provide one validated view per frozen reference or migration ID; views are not
serialized authority records.

## Public functions and methods

| API | Bounds and meaning |
| --- | --- |
| `format_reference`, `parse_owner_reference` | 32/64 lowercase hex components, exact 115-byte reference. Parser delegates general owner syntax to the existing validator. |
| `Record::{encode, decode, key}` | Strict document bounds above; fixed namespace key derivation. `key` alone grants nothing. |
| `EvidenceKey::relative_path` | Only fixed relative namespaces and validated hash components; no arbitrary supplied paths. |
| `DirectoryReservation::path` | Borrow its schema-bound exact path; does not authorize creation. |
| `ProvisioningReceipt::{owner, binding, path}` | Validate exact encoded receipt before formatting owner; construct a hash/session binding; borrow the fixed artifact path. These data helpers do not mint capabilities. |
| `reserve`, `capture`, `append` | Guarded create/readback, twice-read capture, and validated immutable append; all document/count/slot bounds above apply. Caller supplies the original runtime authorizer. |
| `EvidenceIo::{login, verify_context, verify_record}` | Real token/login, native scope/ancestor/directory proof, and native directory/managed/migration attestations. No browser claims. |
| `EvidenceIo::{entries, read_private, observe}` | Enumerate all fixed authority entries within 4096; bounded private no-follow reads; independently read each bounded slot. Errors are not absence. |
| `EvidenceIo::create_only` | Receives the exact bounded bytes and opaque fence; must authorize/revalidate each effect and call fence verification immediately before final create-new publication. |
| `WriteFence::verify` | Re-read exact record hashes, all slots, native context and login; rejects drift. |
| `CapturedEvidence::{receipt, receipt_sha256, resolve}` | Borrow validated root data; replay at most 1024 links and bounded validated history views without IO or effects. |
| `ObservedSlot::unproven_ownership` | Read-only fallback: known External is ReferenceOnly, everything else Unknown; cannot return Owned. |
| `Resolution::{ownership, baseline}` | Compare exact owner/artifact/path/current bytes; require all four Owned states for baseline. No cached-current or absent/empty equivalence. |
| `Resolution::{head, terminal, settled}` | Read-only rooted head and settlement information, not effect authorization. |
| `Resolution::require_effect_session` | Requires the actual captured/current login and the still-active link's execution session. Does not replace runtime authorization. |
| `FrozenHistory::{decode, plan, reference}` | Original bounded decoder, then immutable plan/reference access. Decoder establishes consistency, not storage provenance. |
| `Publisher::read_history` | Caller-held Launcher then Monitor; original plan/journal/decision validators; no nested acquisition, writes or ownership recursion. |
| `PublicationHistory::{frozen, journal_sha256, reached, complete, decision}` | Immutable validated views; reached is absent/Intended/Published, decision exposes exact hash and bounded observations. Content states are not ownership. |
| `Store::read_history` | Original bounded Saved reader; no begin/advance/write. Native caller supplies storage provenance and freshness. |
| `SavedHistory::{sha256, id, config_path, startup_path, login, phase, review_fingerprint, restores, from, to, config}` | Immutable originally validated metadata and bounded decoded config bytes; no creation authority without the independently rooted binding. |
| `publication_state::verify_protected` | Existing object; current owner, present non-null protected DACL, 1–64 ordinary allow ACEs, owner/OWNER RIGHTS/SYSTEM only, effective owner and SYSTEM full access. |
| `publication_state::verify_ancestor` | Existing environmental directory; current/OS-trusted owner, bounded recognized ACLs and no effective untrusted replacement/security rights; absent children also prohibit untrusted creation grants. |
| `publication_state::verify_ancestors` | Walk all ancestors through filesystem root, private policy inside an existing protected root and environmental policy outside. Missing descendants grant no creation authority. |
| `publication_state::{create_new_directory, create_new_file}` | Strict create-new private objects; AlreadyExists always conflicts; no repair/adoption. Caller separately proves parent authorization. |
| `publication_state::read_private` | 1..=32 MiB requested bound, no-follow handle checks; only genuine NotFound is None. Caller separately verifies ancestors and native object identities. |

## Storage and validation limits

The new Windows primitives inspect security on opened handles, require
`SE_DACL_PROTECTED` for private state, reject unknown ACE forms/masks, and distinguish
inherit-only entries from effective grants. Environmental ancestors may have public
read grants, but not effective untrusted delete-child, security-write, owner-write,
delete, metadata-write, or absent-child creation grants. TrustedInstaller is accepted
only after a local OS account lookup. Unsupported ACL layouts refuse without repair.
These are storage properties, not ownership decisions. Unix private-file checks are
portable infrastructure, not Windows identity evidence.

The existing publisher's methods and original storage helpers retain their behavior.
Windows directory power-loss durability is not inferred from Unix directory sync.
Real DACLs, owners, reparse points and denied reads are exercised only by cfg(windows)
fixtures in the existing Windows CI jobs. Fixture construction failures fail the test.
Local Linux tests use real temporary files and original generators/journal writers,
with synthetic identities/guards/platform observations. They are not Windows or live
acceptance evidence.
