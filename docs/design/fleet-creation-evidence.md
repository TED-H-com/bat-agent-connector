# Native Fleet creation evidence

`fleet-core::creation_evidence` provides bounded records, guarded storage contracts,
and a pure ownership resolver. A [read-only Windows adapter core](../../desktop/fleet-core/src/windows_creation/native.rs#L116)
now implements ownership reads through this resolver; it has no production caller,
evidence writer, native route, IPC or public Apply entrypoint. Native integration must
keep `EvidenceIo` on one blocking thread. Launcher serializes all evidence
operations. New reservations and admissions additionally hold Monitor and prove
absence through `verify_effect_context`; the write fence repeats that check.
Migration-intent admission additionally requires `verify_no_pending_migration`
before authorization and in the append fence. This hook re-reads the scoped
Store under those guards and requires `pending()` to return None; errors refuse.
Its default returns `MIGRATION_PENDING`, keeping read-only adapters read-only.
Historical capture and migration completion/termination need only Launcher, so a
running Monitor does not prevent recording past effects. They publish no artifact.
The adapter must not try to reacquire the running Monitor's mutex for those reads. An
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
   A retry cannot mint a replacement identity. It re-captures exact receipt bytes,
   context and login but does not repeat the four-absence postcondition: generation
   may already have occurred. It grants no new reservation. `capture` reopens durable evidence
   across same-SID sessions; new reservations use their creator's current session.
2. `capture` reads the entire fixed authority namespace and all slots twice,
   checking exact bytes, native context and login. The native boundary independently
   validates canonical paths, parent/object identities, protected ancestors,
   directory-creation references, no-follow reads and external origin. It must
   reject unknown entries and unsafe/orphan directories, not silently skip them.
3. Read frozen plans, journals, decisions and migration Saved records through
   their original validators under Launcher. `Publisher::read_evidence_history`
   checks the current SID, private storage and original journal/decision validators
   without the publication effect gates. `Publisher::read_history` retains its
   inherited `MIGRATION_PENDING` and `MONITOR_ALREADY_RUNNING` gates, as do all
   existing publication operations. Neither history view authorizes an effect.
   `Store::observe_history` returns scoped, genuine absence or a Saved record from
   the original reader; unreadable/invalid data is never absence. Supply fresh views to
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
   third/unreadable slots unproven. This taint is permanent even if earlier bytes
   reappear: a fresh review cannot bless them. Because every later admission and
   migration intent requires an all-four Owned baseline, one tainted slot blocks
   every later admission and migration for this creation, not just that slot.
6. A migration intent must start from an already Owned exact pointer version.
   Migration IDs are unique both at append and in independent replay. Until an
   outcome exists, the intent is unsettled and only Installation is Unknown;
   the other three artifacts retain their independently rooted versions.
   A completion retains the intent's historical binding, including session, and
   binds an original validated Complete Saved record, exact before/after pointer
   bytes, paths, SID/session, review and restore source. Its session need not equal
   today's session; `capture` still requires today's SID to match the receipt, and
   the fence binds today's unchanged login. A restore completion requires an
   earlier bound exact reverse pair. Completion and termination share one
   create-only key per intent, so they cannot coexist or replace each other.

   A never-completed intent has a bounded `MigrationTermination` with the same
   historical binding and a native decision reference. Refused requires a scoped
   original-reader observation that no Saved file exists. Abandoned binds the
   exact non-Complete Saved hash (including an unconfirmed launch). Restored binds
   that non-Complete source plus an exact Complete restore Saved hash/ID whose
   `restores` names this source; its before bytes must be either source version
   and its after bytes must exactly restore the source's original version.
   Native attestation and authorization remain mandatory for these records.
   Every termination clears only Installation's allowed versions, including a
   successful restore: old bytes are never re-adopted. The other three artifacts
   retain their proof, but all-four admission remains blocked. An adapter must
   never start effects for the terminated ID or reuse it itself. Terminate only
   from the intent's own session, or once that session has ended; session-liveness
   validation is an adapter precondition, not a hook provided by this library.
   Existing user-facing Store controls remain unchanged. If those controls later
   begin, reactivate or advance that ID, the termination still settles its intent
   and confines the conflict to Installation. The other three slots retain exactly
   their previous answers, and no completion, intent or all-four admission revives.

   New termination admission strictly validates every seal above. During replay
   of a durable termination, only these mismatches are Store progress: Saved
   appearing after Refused, a source Saved hash/non-Complete-phase mismatch, or a
   restore Saved hash/Complete-phase mismatch. Replay still requires the scoped
   original-reader views, required Saved presence, exact intent binding,
   Saved-vs-intent checks, restore lineage/SID/before/after bytes and backend checks.
   Missing or invalid views, other content mismatches, duplicate/unused records
   and conflicting outcomes still fail the entire resolution. No changed seal can
   grant any Owned version. Identical re-append is idempotent after permitted Store
   changes; a different outcome cannot replace the create-only record. Replay
   relies on the protected append-only record's admission provenance; it cannot
   reconstruct historical Saved bytes that the Store has overwritten.
   No completion/termination repairs, replaces, removes or rolls back artifacts.
   The resolver never begins, advances, restores or launches a migration.
7. `Resolution::ownership` also compares the caller's actual bytes to the captured
   observation. Absence remains distinct from an empty file, although an owned
   absent slot uses `sha256(empty)` in the existing ownership interface. Require
   `baseline()` for all-four admission/final preflight, including unchanged roles.
   Require the exact active frozen reference and captured/current execution
   session for pending effects, plus the separate native review/lifecycle/expiry
   authorizer. Ownership is not execution
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
nesting deeper than eight containers. The native `EvidenceKey` selects the
receipt/auxiliary byte limit before parsing; the decoded kind and key must match
that location. No valid typed schema currently exceeds eight containers, so the
depth-nine malformed-array test also fails typed deserialization; it is not
independent regression coverage for the depth check. Hashes are 64 lowercase hex; IDs are 32
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
| `MigrationCompletion` | Historical intent binding, intent hash, exact Complete Saved hash and next pointer hash. |
| `MigrationOutcome`, `MigrationTermination` | Closed Refused/Abandoned/Restored outcome; historical binding, intent hash, explicitly nullable Saved hash, bounded decision reference; Restored also names 32-hex restore ID and 64-hex exact Saved hash. Auxiliary 32 KiB bound; no pointer version granted. |
| `Record` | Closed union of the eight document kinds (provisioning boxed); auxiliary documents at most 32,768 bytes. Deserialization alone is not authority. |
| `EvidenceKey` | Fixed provision/lineage/closure/directory/managed/migration namespaces; variable filename components are 64-hex hashes. |
| `ObservedSlot` | Absent, bounded Bytes/External, or Unavailable. The publisher's shared artifact limit is passed to observe: Installation 16 KiB, each other slot 1 MiB. The adapter must bound allocation/read, not just truncate afterward. |
| `EvidenceIo` | Trusted synchronous platform boundary; methods listed below. The Windows adapter's [private reader](../../desktop/fleet-core/src/windows_creation.rs#L214) implements reads; effect checks and create-only writes still refuse. |
| `WriteFence` | Opaque pre-write login, at most 4096 record hashes and four bounded observations. |
| `CapturedEvidence` | Opaque guarded receipt/record/observation capture; no deserializer or public constructor. |
| `Resolution` | Opaque replay result, at most two admissible versions per slot for active Intended recovery. |
| `FrozenHistory` | Immutable original-validator result; 32 MiB frozen-plan bound, exact reference/hash and original generator seal. |
| `PublicationHistory` | Immutable original journal/decision validator result; 32 KiB each, at most four reached steps/observations, original 1024 decision-entry limit. |
| `MigrationHistory` | Non-deserializable original-reader view for a fixed 32-hex ID, pointer and startup path; genuine absence or a SavedHistory. No caller-created absence proof. |
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
| `Record::{encode, decode, key}` | Decode takes the expected native EvidenceKey and enforces its bound before parsing; the decoded key must match. Directory keys normalize component separators and ASCII case on Windows; native canonical spelling/object identity checks still reject aliases, including unsupported Unicode case aliases. Key equality alone grants nothing. |
| `EvidenceKey::relative_path` | Only fixed relative namespaces and validated hash components; no arbitrary supplied paths. |
| `DirectoryReservation::path` | Borrow its schema-bound exact path; does not authorize creation. |
| `ProvisioningReceipt::{owner, binding, path}` | Validate exact encoded receipt before formatting owner; construct a hash/session binding; borrow the fixed artifact path. These data helpers do not mint capabilities. |
| `reserve`, `capture`, `append` | Guarded create/readback, twice-read capture, and validated immutable append; all document/count/slot bounds above apply. Caller supplies the original runtime authorizer. |
| `EvidenceIo::{login, verify_context, verify_record}` | Launcher-held real token/login, native scope/ancestor/directory proof, and native directory/managed/migration attestations. No browser claims. |
| `EvidenceIo::verify_effect_context` | New reservations and all non-outcome appends require Launcher then Monitor and positive absence; repeated before final effects. Historical migration outcomes have no such absence requirement. |
| `EvidenceIo::verify_no_pending_migration` | Migration-intent admission/fence only; scoped Store pending must be absent under the guards, before authorization and effects. Default refuses with MIGRATION_PENDING. No Store mutation or session-liveness inference. |
| `EvidenceIo::{entries, read_private, observe}` | Enumerate all fixed authority entries within 4096; bounded private no-follow reads; independently read each slot with its supplied limit, no-follow open and handle type checks before a limit+1 read. Errors are not absence. |
| `EvidenceIo::create_only` | Receives the exact bounded bytes and opaque fence; must authorize/revalidate each effect and call fence verification immediately before final create-new publication. |
| `WriteFence::verify` | Re-read exact record hashes, all slots, native context and login; rejects drift. |
| `CapturedEvidence::{receipt, receipt_sha256, resolve}` | Borrow validated root data; replay at most 1024 links and bounded validated history views without IO or effects. |
| `ObservedSlot::unproven_ownership` | Read-only fallback: known External is ReferenceOnly, everything else Unknown; cannot return Owned. |
| `Resolution::{ownership, baseline}` | Compare exact owner/artifact/path/current bytes; require all four Owned states for baseline. No cached-current or absent/empty equivalence. |
| `Resolution::{head, terminal, settled}` | Read-only rooted head and settlement information, not effect authorization. |
| `Resolution::require_effect_session` | Takes the exact frozen reference and actual captured/current login; requires that still-active link and execution session. Does not replace runtime authorization. |
| `FrozenHistory::{decode, plan, reference}` | Original bounded decoder, then immutable plan/reference access. Decoder establishes consistency, not storage provenance. |
| `Publisher::read_history` | Caller-held Launcher then Monitor; original validators and publication absence gates; no nested acquisition, writes or ownership recursion. |
| `Publisher::read_evidence_history` | Launcher-held current SID/private storage checks and original plan/journal/decision validators, without migration/Monitor absence; bounded evidence only, no effect authorization. |
| `PublicationHistory::{frozen, journal_sha256, reached, complete, decision}` | Immutable validated views; reached is absent/Intended/Published, decision exposes exact hash and bounded observations. Content states are not ownership. |
| `Store::{read_history, observe_history}` | Original bounded Saved reader; observe_history also represents genuine NotFound with the exact id/native paths. No begin/advance/write. Native caller supplies Launcher, storage provenance and freshness. |
| `MigrationHistory::{id, config_path, startup_path, saved}` | Immutable scoped observation; saved returns a validated history or genuine absence. |
| `installation::validate_pointer` | Read-only 16 KiB payload check using Snapshot's existing Document schema and legacy backend default; exact declared root/backend comparison, no filesystem access or ownership inference. |
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

The existing publisher's methods and gates retain their behavior; both history
paths share the original journal decoder. Unix private verify/read helpers use
O_NOFOLLOW|O_NONBLOCK and inspect the opened handle before any read, so a FIFO or
swapped leaf symlink cannot block or be followed. Existing create helpers retain
their create-only behavior.
Windows directory power-loss durability is not inferred from Unix directory sync.
Real DACLs, owners, reparse points and denied reads are exercised only by cfg(windows)
fixtures in the existing Windows CI jobs. Fixture construction failures fail the test.
Local Linux tests use real temporary files and original generators/journal writers,
with synthetic identities/guards/platform observations. They are not Windows or live
acceptance evidence.
