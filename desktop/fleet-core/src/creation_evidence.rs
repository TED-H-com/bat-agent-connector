//! Rooted native creation evidence. Parsers and digests never confer ownership.
//! The synchronous IO boundary holds Launcher on one thread; new reservations
//! and admissions additionally require Monitor absence under its guard;
//! there is deliberately no production OS adapter or configuration writer here.
use crate::{
    connection_publish::{FrozenPlanRef, PublicationHistory},
    connection_setup::{Artifact, FileOwnership, OwnerEvidence},
    digest,
    migration::MigrationHistory,
    process_adapter::LoginIdentity,
    Result,
};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};
mod records;
mod replay;
pub use records::*;
pub use replay::Resolution;
const INVALID: &str = "CREATION_EVIDENCE_UNPROVEN";
fn ensure(condition: bool) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(INVALID)
    }
}
fn index(a: Artifact) -> usize {
    ARTIFACTS.iter().position(|v| *v == a).unwrap()
}

/// A no-follow bounded observation. Unavailable includes inaccessible, linked,
/// wrong-type, overlarge or unstable objects. It is never absence.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ObservedSlot {
    Absent,
    Bytes(Vec<u8>),
    External(Vec<u8>),
    Unavailable,
}
impl ObservedSlot {
    /// Read-only fallback when root/history proof is unavailable. Only an
    /// independently known external origin is ReferenceOnly; never returns Owned.
    pub fn unproven_ownership(&self) -> FileOwnership {
        if matches!(self, Self::External(_)) {
            FileOwnership::ReferenceOnly
        } else {
            FileOwnership::Unknown
        }
    }

    fn version(&self) -> Option<Option<String>> {
        match self {
            Self::Absent => Some(None),
            Self::Bytes(b) => Some(Some(digest(b))),
            _ => None,
        }
    }
    fn bytes(&self) -> Option<&[u8]> {
        match self {
            Self::Bytes(b) | Self::External(b) => Some(b),
            _ => None,
        }
    }
}
/// Trusted native boundary, never implemented by WebView input. Each method
/// requires Launcher on the calling thread. New reservations/admissions also
/// require verify_effect_context (Launcher then Monitor, positive absence).
/// Historical captures and migration outcomes need no Monitor absence; they
/// publish no artifact and do not authorize a later effect. Reads reject links, unsafe ACLs,
/// aliases and changed object identities; only genuine NotFound is absence.
/// `verify_context` checks the exact startup record, current token SID, native
/// roots, all protected ancestors, parent identities and independent directory
/// creation references. Session equality is checked separately for new effects.
///
/// `entries` must enumerate the entire fixed namespace, rejecting unknown names,
/// orphan directories and more than ENTRY_LIMIT entries (including unfinished
/// residue). `read_private` enforces its byte bound before allocating/readback.
/// `create_only` must use private create-new staging/final publication, invoke the
/// callback before every directory/staging/final effect, repeat verify_context
/// and WriteFence::verify immediately before final publication, sync and read
/// back without replacement. Newly created namespace directories must stay bound
/// to the adapter's live creation handles while the initial receipt is absent.
/// Existing objects are never repaired, adopted, deleted or silently replaced.
pub trait EvidenceIo {
    fn login(&self) -> Result<LoginIdentity>;
    fn verify_context(&self, receipt: &ProvisioningReceipt) -> Result<()>;
    /// New reservation/admission gate, repeated by the final write fence. Hold
    /// Launcher then Monitor and positively prove absence; never stop a Monitor.
    fn verify_effect_context(&self) -> Result<()>;
    /// Migration-intent admission only: under Launcher then Monitor, re-read the
    /// scoped Store and require pending() == None. Errors are not absence. The
    /// default refuses, so read-only adapters cannot admit migration effects.
    fn verify_no_pending_migration(&self) -> Result<()> {
        Err("MIGRATION_PENDING")
    }
    /// Native-only auxiliary attestations: directory creation handle/identity,
    /// managed manifest identity, or the original authorized migration intent.
    fn verify_record(&self, record: &Record) -> Result<()>;
    fn entries(&self) -> Result<Vec<EvidenceKey>>;
    fn read_private(&self, key: &EvidenceKey, limit: usize) -> Result<Option<Vec<u8>>>;
    /// Enforce the supplied artifact bound before allocating or reading bytes.
    /// No-follow open and handle type/identity checks precede a limit+1 read.
    fn observe(&self, slot: &SlotReservation, limit: usize) -> Result<ObservedSlot>;
    fn create_only(
        &mut self,
        receipt: &ProvisioningReceipt,
        key: &EvidenceKey,
        bytes: &[u8],
        fence: &WriteFence,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()>;
}

/// Immutable pre-write state, minted only by reserve/append. The native adapter
/// calls verify under its guards immediately before the create-only final effect.
/// At most ENTRY_LIMIT record hashes and four bounded slot observations are held.
pub struct WriteFence {
    login: LoginIdentity,
    record_hashes: BTreeMap<EvidenceKey, String>,
    observed: [ObservedSlot; 4],
    effect_context: bool,
    migration_admission: bool,
}
impl WriteFence {
    pub fn verify(&self, io: &impl EvidenceIo, receipt: &ProvisioningReceipt) -> Result<()> {
        if self.effect_context {
            io.verify_effect_context()?;
        }
        if self.migration_admission {
            io.verify_no_pending_migration()?;
        }
        io.verify_context(receipt)?;
        ensure(io.login()? == self.login)?;
        let records = load_records(io)?;
        ensure(
            records.len() == self.record_hashes.len()
                && records
                    .iter()
                    .all(|(k, s)| self.record_hashes.get(k) == Some(&digest(&s.bytes))),
        )?;
        ensure(observations(io, receipt)? == self.observed)?;
        io.verify_context(receipt)?;
        if self.effect_context {
            io.verify_effect_context()?;
        }
        if self.migration_admission {
            io.verify_no_pending_migration()?;
        }
        ensure(io.login()? == self.login)
    }
}

struct Stored {
    record: Record,
    bytes: Vec<u8>,
}
/// Non-deserializable guarded capture. Re-capture for every ownership/effect
/// check; never cache this value across review, await, guard release or effects.
pub struct CapturedEvidence {
    receipt: ProvisioningReceipt,
    receipt_sha256: String,
    owner: OwnerEvidence,
    login: LoginIdentity,
    records: BTreeMap<EvidenceKey, Stored>,
    observed: [ObservedSlot; 4],
}
impl CapturedEvidence {
    pub fn receipt(&self) -> &ProvisioningReceipt {
        &self.receipt
    }
    pub fn receipt_sha256(&self) -> &str {
        &self.receipt_sha256
    }
    pub fn resolve(
        &self,
        publications: &[PublicationHistory],
        migrations: &[MigrationHistory],
    ) -> Result<Resolution> {
        replay::resolve(self, publications, migrations)
    }
}
fn observations(io: &impl EvidenceIo, receipt: &ProvisioningReceipt) -> Result<[ObservedSlot; 4]> {
    let mut out = Vec::new();
    for slot in &receipt.slots {
        let bound = crate::connection_publish::limit(slot.artifact);
        let value = io.observe(slot, bound).unwrap_or(ObservedSlot::Unavailable);
        out.push(if value.bytes().is_some_and(|b| b.len() > bound) {
            ObservedSlot::Unavailable
        } else {
            value
        });
    }
    out.try_into().map_err(|_| INVALID)
}
fn load_records(io: &impl EvidenceIo) -> Result<BTreeMap<EvidenceKey, Stored>> {
    let keys = io.entries()?;
    ensure(keys.len() <= ENTRY_LIMIT)?;
    let mut records = BTreeMap::new();
    for key in keys {
        key.relative_path()?;
        let bound = if key == EvidenceKey::Provisioning {
            RECEIPT_LIMIT
        } else {
            RECORD_LIMIT
        };
        let bytes = io.read_private(&key, bound)?.ok_or(INVALID)?;
        ensure(bytes.len() <= bound)?;
        let record = Record::decode(&key, &bytes)?;
        ensure(record.key() == key)?;
        ensure(records.insert(key, Stored { record, bytes }).is_none())?;
    }
    Ok(records)
}
/// Re-read every authoritative record and every slot twice under caller-held
/// guards. A syntactically valid OwnerEvidence without the root is rejected.
pub fn capture(io: &impl EvidenceIo, owner: &OwnerEvidence) -> Result<CapturedEvidence> {
    let hash = parse_owner_reference(owner)?;
    let records = load_records(io)?;
    let stored = records.get(&EvidenceKey::Provisioning).ok_or(INVALID)?;
    let Record::Provisioning(receipt) = &stored.record else {
        return Err(INVALID);
    };
    ensure(digest(&stored.bytes) == hash && receipt.owner(&stored.bytes)? == *owner)?;
    io.verify_context(receipt)?;
    let login = io.login()?;
    ensure(login.valid() && login.owner_sid == receipt.creator.owner_sid)?;
    for stored in records.values() {
        if let Some(binding) = stored.record.binding() {
            ensure(binding.matches(receipt, hash))?;
        }
        io.verify_record(&stored.record)?;
    }
    let observed = observations(io, receipt)?;
    let reread = load_records(io)?;
    ensure(
        records.len() == reread.len()
            && records
                .iter()
                .all(|(k, v)| reread.get(k).is_some_and(|r| r.bytes == v.bytes)),
    )?;
    io.verify_context(receipt)?;
    ensure(io.login()? == login && observations(io, receipt)? == observed)?;
    Ok(CapturedEvidence {
        receipt: receipt.as_ref().clone(),
        receipt_sha256: hash.into(),
        owner: owner.clone(),
        login,
        records,
        observed,
    })
}
/// Reserve all four absent slots before generator use. Retry may reopen exactly
/// the original bytes/IDs; it never replaces a receipt or adopts namespace residue.
/// A retry re-captures the root, but does not require slots still absent: the
/// generator may already have run. It grants no new reservation or effect.
pub fn reserve(
    io: &mut impl EvidenceIo,
    receipt: &ProvisioningReceipt,
    authorize: &mut dyn FnMut() -> Result<()>,
) -> Result<OwnerEvidence> {
    let record = Record::Provisioning(Box::new(receipt.clone()));
    let bytes = record.encode()?;
    let owner = receipt.owner(&bytes)?;
    io.verify_effect_context()?;
    io.verify_context(receipt)?;
    ensure(
        io.login()?
            == (LoginIdentity {
                owner_sid: receipt.creator.owner_sid.clone(),
                session_id: receipt.creator.session_id,
            }),
    )?;
    if io
        .read_private(&EvidenceKey::Provisioning, RECEIPT_LIMIT)?
        .is_some()
    {
        capture(io, &owner)?;
        return Ok(owner);
    }
    ensure(io.entries()?.is_empty())?;
    ensure(
        observations(io, receipt)?
            .iter()
            .all(|o| *o == ObservedSlot::Absent),
    )?;
    authorize()?;
    let fence = WriteFence {
        login: io.login()?,
        record_hashes: BTreeMap::new(),
        observed: std::array::from_fn(|_| ObservedSlot::Absent),
        effect_context: true,
        migration_admission: false,
    };
    fence.verify(io, receipt)?;
    io.create_only(
        receipt,
        &EvidenceKey::Provisioning,
        &bytes,
        &fence,
        authorize,
    )?;
    ensure(
        io.read_private(&EvidenceKey::Provisioning, RECEIPT_LIMIT)?
            .as_deref()
            == Some(&bytes),
    )?;
    let captured = capture(io, &owner)?;
    ensure(captured.observed.iter().all(|o| *o == ObservedSlot::Absent))?;
    Ok(owner)
}
/// Append one validated immutable record through the native create-only boundary.
/// Runtime authorization is mandatory, including the boundary's final callback.
/// Invalid/replayed admissions cannot replace a predecessor or confer authority.
pub fn append(
    io: &mut impl EvidenceIo,
    owner: &OwnerEvidence,
    record: &Record,
    publications: &[PublicationHistory],
    migrations: &[MigrationHistory],
    authorize: &mut dyn FnMut() -> Result<()>,
) -> Result<String> {
    ensure(!matches!(record, Record::Provisioning(_)))?;
    let bytes = record.encode()?;
    let historical = matches!(
        record,
        Record::MigrationCompletion(_) | Record::MigrationTermination(_)
    );
    if !historical {
        io.verify_effect_context()?;
    }
    let migration_admission = matches!(record, Record::MigrationIntent(_));
    if migration_admission {
        io.verify_no_pending_migration()?;
    }
    let captured = capture(io, owner)?;
    let binding = record.binding().ok_or(INVALID)?;
    ensure(
        binding.matches(&captured.receipt, &captured.receipt_sha256)
            && (historical || binding.session_id == captured.login.session_id),
    )?;
    io.verify_record(record)?;
    replay::validate_append(&captured, record, publications, migrations)?;
    let key = record.key();
    if let Some(existing) = io.read_private(&key, RECORD_LIMIT)? {
        ensure(existing == bytes)?;
        return Ok(digest(&bytes));
    }
    authorize()?;
    let fence = WriteFence {
        login: captured.login.clone(),
        record_hashes: captured
            .records
            .iter()
            .map(|(k, s)| (k.clone(), digest(&s.bytes)))
            .collect(),
        observed: captured.observed.clone(),
        effect_context: !historical,
        migration_admission,
    };
    fence.verify(io, &captured.receipt)?;
    io.create_only(&captured.receipt, &key, &bytes, &fence, authorize)?;
    ensure(io.read_private(&key, RECORD_LIMIT)?.as_deref() == Some(&bytes))?;
    capture(io, owner)?;
    Ok(digest(&bytes))
}

/// Shared directory namespace policy for replay and guarded preparation.
/// Review names come from parsed linkage; this is not an ownership decision.
pub(crate) fn directory_reserved(
    receipt: &ProvisioningReceipt,
    path: &Path,
    reviews: &[&str],
) -> bool {
    let creation = receipt.scope.state_root.join("fleet-creation");
    let internal = ["lineage", "closures", "directories", "migrations"]
        .iter()
        .any(|name| path == creation.join(name));
    let decisions = receipt.scope.state_root.join("fleet-publication-decisions");
    let decision_child = path.parent() == Some(decisions.as_path())
        && path
            .file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|review| records::hex(review, 64) && reviews.contains(&review));
    let reserved = receipt.directories.iter().any(|d| {
        matches!(d,
        DirectoryReservation::ReservedAbsent { canonical_path } if canonical_path == path)
    });
    internal || decision_child || reserved
}

/// Produce the exact 115-byte native receipt identifier; not a path or authority.
pub fn format_reference(fleet_creation_id: &str, receipt_sha256: &str) -> Result<String> {
    ensure(records::hex(fleet_creation_id, 32) && records::hex(receipt_sha256, 64))?;
    Ok(format!(
        "fleet-creation.v1.{fleet_creation_id}.{receipt_sha256}"
    ))
}
/// The existing OwnerEvidence validator remains the syntax authority. This
/// parser recognizes only our exact version/ID/hash binding within that syntax.
pub fn parse_owner_reference(owner: &OwnerEvidence) -> Result<&str> {
    ensure(owner.valid())?;
    let prefix = format!("fleet-creation.v1.{}.", owner.installation_id);
    let hash = owner
        .creation_receipt
        .strip_prefix(&prefix)
        .ok_or(INVALID)?;
    ensure(records::hex(hash, 64))?;
    Ok(hash)
}
