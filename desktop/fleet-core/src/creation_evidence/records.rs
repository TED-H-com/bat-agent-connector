use super::*;
use serde::{Deserialize, Serialize};
fn required_option<'de, D, T>(deserializer: D) -> std::result::Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}
use std::path::Component;

pub const RECEIPT_LIMIT: usize = 65_536;
pub const RECORD_LIMIT: usize = 32_768;
pub const HISTORY_LIMIT: usize = 1024;
pub const ENTRY_LIMIT: usize = 4096;
pub const ARTIFACTS: [Artifact; 4] = [
    Artifact::Inventory,
    Artifact::ProfileIndex,
    Artifact::SshReference,
    Artifact::Installation,
];

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObjectIdentity {
    pub volume: u64,
    pub file_id: String,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StartupRole {
    Client,
    Provider,
    Both,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StartupBinding {
    pub installation_ref: String,
    pub role_record_sha256: String,
    pub generation: u64,
    pub role: StartupRole,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Creator {
    pub owner_sid: String,
    pub session_id: u32,
    pub reservation_id: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    pub platform: String,
    pub pointer: PathBuf,
    pub kit_root: PathBuf,
    pub state_root: PathBuf,
    pub startup_role_record: PathBuf,
    pub state_identity: ObjectIdentity,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case", deny_unknown_fields)]
pub enum DirectoryReservation {
    ProvenExisting {
        canonical_path: PathBuf,
        identity: ObjectIdentity,
        creation_reference: String,
    },
    ReservedAbsent {
        canonical_path: PathBuf,
    },
}
impl DirectoryReservation {
    pub fn path(&self) -> &Path {
        match self {
            Self::ProvenExisting { canonical_path, .. }
            | Self::ReservedAbsent { canonical_path } => canonical_path,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SlotReservation {
    pub artifact: Artifact,
    pub canonical_path: PathBuf,
    pub observed: String,
    #[serde(deserialize_with = "required_option")]
    pub before_sha256: Option<String>,
    pub nearest_existing_parent: PathBuf,
    pub parent_identity: ObjectIdentity,
    pub missing_components: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedIdentity {
    pub installation_id: String,
    pub manifest_sha256: String,
    pub actor: String,
    pub server_id: String,
    pub principal_id: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProvisioningReceipt {
    pub startup: StartupBinding,
    pub creator: Creator,
    pub fleet_creation_id: String,
    pub scope: Scope,
    pub directories: Vec<DirectoryReservation>,
    pub slots: [SlotReservation; 4],
    #[serde(deserialize_with = "required_option")]
    pub managed_connector: Option<ManagedIdentity>,
}
/// None is a reserved absence, never a digest of an empty file.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Baseline {
    pub artifact: Artifact,
    #[serde(deserialize_with = "required_option")]
    pub sha256: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub provisioning_sha256: String,
    pub startup_installation_ref: String,
    pub fleet_creation_id: String,
    pub owner_sid: String,
    pub session_id: u32,
    pub pointer: PathBuf,
    pub kit_root: PathBuf,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Terminal {
    #[serde(deserialize_with = "required_option")]
    pub journal_sha256: Option<String>,
    #[serde(deserialize_with = "required_option")]
    pub decision_sha256: Option<String>,
    #[serde(deserialize_with = "required_option")]
    pub closure_sha256: Option<String>,
    #[serde(deserialize_with = "required_option")]
    pub migration_sha256: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublicationLink {
    pub binding: Binding,
    pub predecessor_sha256: String,
    #[serde(deserialize_with = "required_option")]
    pub predecessor_terminal: Option<Terminal>,
    pub frozen: FrozenPlanRef,
    pub generator_version: u32,
    pub baseline: [Baseline; 4],
    pub acceptance_reference: String,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CloseReason {
    Unchanged,
    AbandonedBeforeEffect,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdmissionClosure {
    pub binding: Binding,
    pub link_sha256: String,
    pub reason: CloseReason,
    pub decision_reference: String,
    pub baseline: [Baseline; 4],
    pub journal_absent: bool,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DirectoryCreated {
    pub binding: Binding,
    pub canonical_path: PathBuf,
    pub parent_identity: ObjectIdentity,
    pub identity: ObjectIdentity,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedBinding {
    pub binding: Binding,
    pub managed: ManagedIdentity,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MigrationIntent {
    pub binding: Binding,
    pub predecessor_sha256: String,
    pub predecessor_terminal: Terminal,
    pub migration_id: String,
    pub startup_path: PathBuf,
    pub review_fingerprint: String,
    pub before_sha256: String,
    #[serde(deserialize_with = "required_option")]
    pub restores: Option<String>,
    pub acceptance_reference: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MigrationCompletion {
    pub binding: Binding,
    pub intent_sha256: String,
    pub saved_sha256: String,
    pub after_sha256: String,
}
/// Ending a never-completed migration grants no pointer version, even when a
/// validated restore put old bytes back. Refused requires proven Saved absence.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "reason", rename_all = "snake_case", deny_unknown_fields)]
pub enum MigrationOutcome {
    Refused,
    Abandoned,
    Restored {
        migration_id: String,
        saved_sha256: String,
    },
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MigrationTermination {
    pub binding: Binding,
    pub intent_sha256: String,
    #[serde(deserialize_with = "required_option")]
    pub saved_sha256: Option<String>,
    pub outcome: MigrationOutcome,
    pub decision_reference: String,
}
/// Structural records are data, not capabilities. Only guarded create/readback
/// through EvidenceIo can produce a CapturedEvidence authority.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", content = "body", deny_unknown_fields)]
pub enum Record {
    #[serde(rename = "fleet-slot-provisioning")]
    Provisioning(Box<ProvisioningReceipt>),
    #[serde(rename = "fleet-publication-link")]
    Publication(PublicationLink),
    #[serde(rename = "fleet-admission-closure")]
    Closure(AdmissionClosure),
    #[serde(rename = "fleet-directory-created")]
    Directory(DirectoryCreated),
    #[serde(rename = "fleet-managed-binding")]
    Managed(ManagedBinding),
    #[serde(rename = "fleet-migration-intent")]
    MigrationIntent(MigrationIntent),
    #[serde(rename = "fleet-migration-completion")]
    MigrationCompletion(MigrationCompletion),
    #[serde(rename = "fleet-migration-termination")]
    MigrationTermination(MigrationTermination),
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    version: u32,
    minimum_reader_version: u32,
    record: Record,
}

/// Fixed native namespaces. Hash components are validated before path derivation.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum EvidenceKey {
    Provisioning,
    Lineage(String),
    Closure(String),
    Directory(String),
    Managed,
    MigrationCompletion(String),
}
impl EvidenceKey {
    pub fn relative_path(&self) -> Result<PathBuf> {
        let (directory, hash) = match self {
            Self::Provisioning => return Ok("provision.json".into()),
            Self::Managed => return Ok("managed.json".into()),
            Self::Lineage(h) => ("lineage", h),
            Self::Closure(h) => ("closures", h),
            Self::Directory(h) => ("directories", h),
            Self::MigrationCompletion(h) => ("migrations", h),
        };
        ensure(hex(hash, 64))?;
        Ok(Path::new(directory).join(format!("{hash}.json")))
    }
}
impl Record {
    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let bytes = serde_json::to_vec(&Document {
            version: 1,
            minimum_reader_version: 1,
            record: self.clone(),
        })
        .map_err(|_| INVALID)?;
        Self::decode(&self.key(), &bytes)?;
        Ok(bytes)
    }
    /// The native storage key selects the byte bound before JSON parsing. The
    /// decoded kind and derived key must then agree with that exact location.
    pub fn decode(key: &EvidenceKey, bytes: &[u8]) -> Result<Self> {
        key.relative_path()?;
        let bound = if *key == EvidenceKey::Provisioning {
            RECEIPT_LIMIT
        } else {
            RECORD_LIMIT
        };
        let value = crate::strict_json::parse(bytes, bound).map_err(|_| INVALID)?;
        fn depth(v: &serde_json::Value) -> usize {
            match v {
                serde_json::Value::Object(m) => 1 + m.values().map(depth).max().unwrap_or(0),
                serde_json::Value::Array(a) => 1 + a.iter().map(depth).max().unwrap_or(0),
                _ => 0,
            }
        }
        ensure(depth(&value) <= 8)?;
        let doc: Document = serde_json::from_value(value).map_err(|_| INVALID)?;
        ensure(doc.version == 1 && doc.minimum_reader_version == 1)?;
        ensure(doc.record.key() == *key)?;
        doc.record.validate()?;
        Ok(doc.record)
    }
    pub fn key(&self) -> EvidenceKey {
        match self {
            Self::Provisioning(_) => EvidenceKey::Provisioning,
            Self::Publication(r) => EvidenceKey::Lineage(r.predecessor_sha256.clone()),
            Self::Closure(r) => EvidenceKey::Closure(r.link_sha256.clone()),
            Self::Directory(r) => {
                let normalized: PathBuf = r.canonical_path.components().collect();
                #[cfg(windows)]
                let normalized = normalized.to_string_lossy().to_ascii_lowercase();
                #[cfg(not(windows))]
                let normalized = normalized.to_string_lossy();
                EvidenceKey::Directory(digest(normalized.as_bytes()))
            }
            Self::Managed(_) => EvidenceKey::Managed,
            Self::MigrationIntent(r) => EvidenceKey::Lineage(r.predecessor_sha256.clone()),
            Self::MigrationCompletion(r) => {
                EvidenceKey::MigrationCompletion(r.intent_sha256.clone())
            }
            Self::MigrationTermination(r) => {
                EvidenceKey::MigrationCompletion(r.intent_sha256.clone())
            }
        }
    }
    pub(super) fn binding(&self) -> Option<&Binding> {
        match self {
            Self::Provisioning(_) => None,
            Self::Publication(r) => Some(&r.binding),
            Self::Closure(r) => Some(&r.binding),
            Self::Directory(r) => Some(&r.binding),
            Self::Managed(r) => Some(&r.binding),
            Self::MigrationIntent(r) => Some(&r.binding),
            Self::MigrationCompletion(r) => Some(&r.binding),
            Self::MigrationTermination(r) => Some(&r.binding),
        }
    }
    fn validate(&self) -> Result<()> {
        self.key().relative_path()?;
        if let Some(b) = self.binding() {
            b.validate()?;
        }
        match self {
            Self::Provisioning(r) => r.validate(),
            Self::Publication(r) => {
                ensure(
                    hex(&r.predecessor_sha256, 64)
                        && hex(&r.frozen.review_digest, 64)
                        && hex(&r.frozen.sha256, 64)
                        && r.generator_version == crate::connection_setup::GENERATOR_VERSION
                        && opaque(&r.acceptance_reference),
                )?;
                baseline(&r.baseline)?;
                if let Some(t) = &r.predecessor_terminal {
                    t.validate()?;
                }
                Ok(())
            }
            Self::Closure(r) => {
                ensure(
                    hex(&r.link_sha256, 64) && opaque(&r.decision_reference) && r.journal_absent,
                )?;
                baseline(&r.baseline)
            }
            Self::Directory(r) => ensure(
                path_valid(&r.canonical_path) && r.parent_identity.valid() && r.identity.valid(),
            ),
            Self::Managed(r) => r.managed.validate(),
            Self::MigrationIntent(r) => {
                r.predecessor_terminal.validate()?;
                ensure(
                    hex(&r.predecessor_sha256, 64)
                        && hex(&r.migration_id, 32)
                        && path_valid(&r.startup_path)
                        && r.startup_path
                            .file_name()
                            .is_some_and(|n| n == "Open BAT.lnk")
                        && hex(&r.review_fingerprint, 64)
                        && hex(&r.before_sha256, 64)
                        && r.restores.as_ref().is_none_or(|s| hex(s, 32))
                        && opaque(&r.acceptance_reference),
                )
            }
            Self::MigrationCompletion(r) => ensure(
                hex(&r.intent_sha256, 64) && hex(&r.saved_sha256, 64) && hex(&r.after_sha256, 64),
            ),
            Self::MigrationTermination(r) => {
                ensure(hex(&r.intent_sha256, 64) && opaque(&r.decision_reference))?;
                match &r.outcome {
                    MigrationOutcome::Refused => ensure(r.saved_sha256.is_none()),
                    MigrationOutcome::Abandoned | MigrationOutcome::Restored { .. } => {
                        ensure(r.saved_sha256.as_ref().is_some_and(|h| hex(h, 64)))?;
                        if let MigrationOutcome::Restored {
                            migration_id,
                            saved_sha256,
                        } = &r.outcome
                        {
                            ensure(hex(migration_id, 32) && hex(saved_sha256, 64))?;
                        }
                        Ok(())
                    }
                }
            }
        }
    }
}
impl ObjectIdentity {
    fn valid(&self) -> bool {
        hex(&self.file_id, 32)
    }
}
impl ManagedIdentity {
    fn validate(&self) -> Result<()> {
        ensure(
            hex(&self.installation_id, 32)
                && hex(&self.manifest_sha256, 64)
                && self.actor == format!("desktop-{}", self.installation_id)
                && opaque(&self.server_id)
                && opaque(&self.principal_id),
        )
    }
}
impl Binding {
    fn validate(&self) -> Result<()> {
        ensure(
            hex(&self.provisioning_sha256, 64)
                && uuid(&self.startup_installation_ref)
                && hex(&self.fleet_creation_id, 32)
                && sid(&self.owner_sid)
                && path_valid(&self.pointer)
                && path_valid(&self.kit_root),
        )
    }
    pub(super) fn matches(&self, receipt: &ProvisioningReceipt, hash: &str) -> bool {
        self.provisioning_sha256 == hash
            && self.startup_installation_ref == receipt.startup.installation_ref
            && self.fleet_creation_id == receipt.fleet_creation_id
            && self.owner_sid == receipt.creator.owner_sid
            && self.pointer == receipt.scope.pointer
            && self.kit_root == receipt.scope.kit_root
    }
}
impl Terminal {
    fn validate(&self) -> Result<()> {
        let hashes = [
            &self.journal_sha256,
            &self.decision_sha256,
            &self.closure_sha256,
            &self.migration_sha256,
        ];
        ensure(
            hashes.iter().all(|h| h.as_ref().is_none_or(|s| hex(s, 64)))
                && matches!(
                    (
                        &self.journal_sha256,
                        &self.decision_sha256,
                        &self.closure_sha256,
                        &self.migration_sha256,
                    ),
                    (Some(_), _, None, None)
                        | (None, None, Some(_), None)
                        | (None, None, None, Some(_))
                ),
        )
    }
}
impl ProvisioningReceipt {
    pub fn owner(&self, exact_bytes: &[u8]) -> Result<OwnerEvidence> {
        ensure(
            Record::decode(&EvidenceKey::Provisioning, exact_bytes)?
                == Record::Provisioning(Box::new(self.clone())),
        )?;
        Ok(OwnerEvidence {
            installation_id: self.fleet_creation_id.clone(),
            owner_sid: self.creator.owner_sid.clone(),
            creation_receipt: format_reference(&self.fleet_creation_id, &digest(exact_bytes))?,
        })
    }
    pub fn binding(&self, receipt_sha256: &str, session_id: u32) -> Result<Binding> {
        self.validate()?;
        let binding = Binding {
            provisioning_sha256: receipt_sha256.into(),
            startup_installation_ref: self.startup.installation_ref.clone(),
            fleet_creation_id: self.fleet_creation_id.clone(),
            owner_sid: self.creator.owner_sid.clone(),
            session_id,
            pointer: self.scope.pointer.clone(),
            kit_root: self.scope.kit_root.clone(),
        };
        binding.validate()?;
        Ok(binding)
    }
    pub fn path(&self, artifact: Artifact) -> &Path {
        &self.slots[index(artifact)].canonical_path
    }
    pub(super) fn allowed_directories(&self) -> Vec<PathBuf> {
        let root = &self.scope.kit_root;
        let state = &self.scope.state_root;
        vec![
            root.clone(),
            root.join("client"),
            root.join("client/bat-profiles"),
            state.join("fleet-creation"),
            state.join("fleet-publications"),
            state.join("fleet-publication-plans"),
            state.join("fleet-publication-decisions"),
        ]
    }
    fn validate(&self) -> Result<()> {
        ensure(
            uuid(&self.startup.installation_ref)
                && hex(&self.startup.role_record_sha256, 64)
                && self.startup.generation > 0
                && sid(&self.creator.owner_sid)
                && hex(&self.creator.reservation_id, 32)
                && hex(&self.fleet_creation_id, 32),
        )?;
        let s = &self.scope;
        ensure(
            s.platform == "windows"
                && [
                    &s.pointer,
                    &s.kit_root,
                    &s.state_root,
                    &s.startup_role_record,
                ]
                .into_iter()
                .all(|p| path_valid(p))
                && s.pointer == s.state_root.join("fleet.json")
                && s.state_identity.valid(),
        )?;
        ensure(!self.directories.is_empty() && self.directories.len() <= 8)?;
        let allowed = self.allowed_directories();
        let mut seen = std::collections::BTreeSet::new();
        for d in &self.directories {
            ensure(allowed.contains(&d.path().to_path_buf()) && seen.insert(d.path()))?;
            if let DirectoryReservation::ProvenExisting {
                identity,
                creation_reference,
                ..
            } = d
            {
                ensure(identity.valid() && opaque(creation_reference))?;
            }
        }
        let expected = [
            s.kit_root.join("client/fleet-inventory.json"),
            s.kit_root.join("client/bat-profiles/index.json"),
            s.kit_root.join("client/ssh-config"),
            s.pointer.clone(),
        ];
        for ((slot, a), path) in self.slots.iter().zip(ARTIFACTS).zip(expected) {
            ensure(
                slot.artifact == a
                    && slot.canonical_path == path
                    && slot.observed == "absent"
                    && slot.before_sha256.is_none()
                    && path_valid(&slot.canonical_path)
                    && path_valid(&slot.nearest_existing_parent)
                    && slot.parent_identity.valid()
                    && !slot.missing_components.is_empty()
                    && slot.missing_components.len() <= 8
                    && slot.canonical_path != s.startup_role_record,
            )?;
            let mut rebuilt = slot.nearest_existing_parent.clone();
            for c in &slot.missing_components {
                ensure(component(c))?;
                rebuilt.push(c);
            }
            ensure(rebuilt == slot.canonical_path)?;
        }
        ensure(
            self.slots
                .iter()
                .map(|s| &s.canonical_path)
                .collect::<std::collections::BTreeSet<_>>()
                .len()
                == 4,
        )?;
        if let Some(m) = &self.managed_connector {
            m.validate()?;
        }
        Ok(())
    }
}
pub(super) fn baseline(values: &[Baseline; 4]) -> Result<()> {
    ensure(
        values
            .iter()
            .zip(ARTIFACTS)
            .all(|(v, a)| v.artifact == a && v.sha256.as_ref().is_none_or(|h| hex(h, 64))),
    )
}
pub(super) fn hex(s: &str, length: usize) -> bool {
    s.len() == length
        && s.bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn uuid(s: &str) -> bool {
    s.len() == 36
        && s.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_hexdigit()
            }
        })
}
fn sid(s: &str) -> bool {
    s.len() <= 184 && crate::ownership::sid_valid(s)
}
fn opaque(s: &str) -> bool {
    !s.is_empty() && s.len() <= 256 && !s.chars().any(char::is_control)
}
fn component(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 255
        && s != "."
        && s != ".."
        && !s.contains(['/', '\\', ':', '\0'])
        && !s.ends_with(['.', ' '])
}
fn path_valid(p: &Path) -> bool {
    p.to_str().is_some_and(|s| {
        !s.is_empty()
            && s.len() <= 4096
            && s.encode_utf16().count() <= 4096
            && !s.contains('\0')
            && !s.split(['/', '\\']).any(|c| c == "." || c == "..")
    }) && p.is_absolute()
        && !p
            .components()
            .any(|c| matches!(c, Component::CurDir | Component::ParentDir))
}
