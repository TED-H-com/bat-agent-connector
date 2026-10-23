//! Native creation observation and rooted ownership. No IPC or process effects.
//! The default view is read-only. Explicit writer scopes retain effect guards;
//! first reservation bootstraps only its root, record container and receipt.
use crate::{
    connection_setup::{Artifact, FileOwnership, OwnerEvidence},
    creation_evidence::{self as evidence, *},
    digest,
    process_adapter::LoginIdentity,
    Result,
};
use std::{
    cell::RefCell,
    collections::BTreeMap,
    fs::File,
    path::{Path, PathBuf},
};
const UNPROVEN: &str = "CREATION_EVIDENCE_UNPROVEN";
const UNSUPPORTED: &str = "CREATION_OPERATION_UNSUPPORTED";
const NAMESPACES: [fn(String) -> EvidenceKey; 4] = [
    EvidenceKey::Lineage,
    EvidenceKey::Closure,
    EvidenceKey::Directory,
    EvidenceKey::MigrationCompletion,
];

// Tests substitute OS identity, ACL, path, process and guard observations.
// All namespace enumeration, bounded reads, parsing and replay remain real.
#[doc(hidden)]
pub trait Security {
    type LauncherGuard;
    type MonitorGuard;
    fn launcher_guard(&self, verify: &dyn Fn() -> Result<()>) -> Result<Self::LauncherGuard>;
    fn monitor_guard(&self, verify: &dyn Fn() -> Result<()>) -> Result<Self::MonitorGuard>;
    fn unconfigured(&self, pointer: &Path, roaming: &Path) -> Result<()>;
    fn discover(&self) -> Result<Option<crate::discovery::MonitorIdentity>>;
    fn unpublished(&self, journal: &Path) -> Result<()>;
    fn validate_path(&self, path: &Path) -> Result<()>;
    fn login(&self) -> Result<LoginIdentity>;
    fn held(&self) -> Result<()>;
    fn effect_held(&self) -> Result<()> {
        Err(UNSUPPORTED)
    }
    fn open(&self, path: &Path, private: bool, child_absent: bool) -> Result<File>;
    fn identity(&self, file: &File) -> Result<ObjectIdentity>;
}
struct Reader<S> {
    expected: ProvisioningReceipt,
    security: S,
    login: LoginIdentity,
    // Directory handles deny delete sharing on Windows. Retain them throughout
    // this synchronous operation, including evidence/history readbacks.
    directories: RefCell<BTreeMap<PathBuf, (std::rc::Rc<File>, ObjectIdentity)>>,
}
fn present(path: &Path) -> Result<bool> {
    match path.symlink_metadata() {
        Ok(_) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err(UNPROVEN),
    }
}
impl<S: Security> Reader<S> {
    fn new(expected: ProvisioningReceipt, security: S) -> Result<Self> {
        Record::Provisioning(Box::new(expected.clone())).encode()?;
        // Managed identity requires its native manifest/service attestation.
        // The observation-only boundary cannot establish that binding yet.
        if expected.managed_connector.is_some() {
            return Err(UNSUPPORTED);
        }
        let login = security.login()?;
        if !login.valid() || login.owner_sid != expected.creator.owner_sid {
            return Err("OTHER_LOGIN_OWNER");
        }
        Ok(Self {
            expected,
            security,
            login,
            directories: RefCell::default(),
        })
    }
    fn root(&self) -> PathBuf {
        self.expected.scope.state_root.join("fleet-creation")
    }
    fn key_path(&self, key: &EvidenceKey) -> Result<PathBuf> {
        Ok(self.root().join(key.relative_path()?))
    }
    fn directory_container(&self) -> Result<PathBuf> {
        Ok(self
            .key_path(&EvidenceKey::Directory("0".repeat(64)))?
            .parent()
            .ok_or(UNPROVEN)?
            .into())
    }
    fn private(&self, path: &Path) -> Result<bool> {
        let role_parent = self
            .expected
            .scope
            .startup_role_record
            .parent()
            .ok_or(UNPROVEN)?;
        Ok(path.starts_with(&self.expected.scope.kit_root)
            || path.starts_with(self.root())
            || self
                .expected
                .directories
                .iter()
                .any(|d| path.starts_with(d.path()))
            || path == self.expected.scope.pointer
            || path.starts_with(role_parent))
    }
    fn walk(&self, path: &Path) -> Result<Option<std::rc::Rc<File>>> {
        use crate::publication_state::{metadata, walk_checked, WalkObject};
        self.security.validate_path(path)?;
        let object = walk_checked(
            path,
            &mut self.directories.borrow_mut(),
            UNPROVEN,
            |p, child| {
                let Some(m) = metadata(p)? else {
                    return Ok(None);
                };
                let file = self.security.open(
                    p,
                    self.private(p)?,
                    child.is_some_and(|c| matches!(metadata(c), Ok(None))),
                )?;
                let metadata = file.metadata().map_err(|_| UNPROVEN)?;
                if metadata.is_dir() != m.is_dir() {
                    return Err(UNPROVEN);
                }
                let identity = self.security.identity(&file)?;
                Ok(Some(WalkObject::new(
                    metadata,
                    std::rc::Rc::new(file),
                    identity,
                )))
            },
        )?;
        Ok(object.map(|o| o.handle))
    }
    fn identity(&self, path: &Path) -> Result<ObjectIdentity> {
        self.security
            .identity(self.walk(path)?.ok_or(UNPROVEN)?.as_ref())
    }
    fn read(&self, path: &Path, limit: usize) -> Result<Option<Vec<u8>>> {
        crate::publication_state::read_limit(limit).map_err(|_| UNPROVEN)?;
        let Some(file) = self.walk(path)? else {
            return Ok(None);
        };
        let identity = self.security.identity(&file)?;
        let bytes = crate::publication_state::read_bounded(&file, limit).map_err(|_| UNPROVEN)?;
        if self.identity(path)? != identity {
            return Err(UNPROVEN);
        }
        Ok(Some(bytes))
    }
    fn base(&self) -> Result<()> {
        if self.security.login()? != self.login {
            return Err("OTHER_LOGIN_OWNER");
        }
        let r = &self.expected;
        if self.identity(&r.scope.state_root)? != r.scope.state_identity {
            return Err(UNPROVEN);
        }
        let role = self
            .read(&r.scope.startup_role_record, RECEIPT_LIMIT)?
            .ok_or(UNPROVEN)?;
        if digest(&role) != r.startup.role_record_sha256 {
            return Err(UNPROVEN);
        }
        for slot in &r.slots {
            if self.identity(&slot.nearest_existing_parent)? != slot.parent_identity {
                return Err(UNPROVEN);
            }
            self.walk(slot.canonical_path.parent().ok_or(UNPROVEN)?)?;
        }
        Ok(())
    }
    fn root_bytes(&self) -> Result<Vec<u8>> {
        let bytes = self
            .read(&self.key_path(&EvidenceKey::Provisioning)?, RECEIPT_LIMIT)?
            .ok_or(UNPROVEN)?;
        if Record::decode(&EvidenceKey::Provisioning, &bytes)?
            != Record::Provisioning(Box::new(self.expected.clone()))
        {
            return Err(UNPROVEN);
        }
        Ok(bytes)
    }
    fn directory(&self, path: &Path, receipt_hash: &str) -> Result<()> {
        let identity = self.identity(path)?;
        let parent_identity = self.identity(path.parent().ok_or(UNPROVEN)?)?;
        let key = Record::Directory(DirectoryCreated {
            binding: self.expected.binding(receipt_hash, self.login.session_id)?,
            canonical_path: path.into(),
            parent_identity: parent_identity.clone(),
            identity: identity.clone(),
        })
        .key();
        let bytes = self
            .read(&self.key_path(&key)?, RECORD_LIMIT)?
            .ok_or(UNPROVEN)?;
        let Record::Directory(record) = Record::decode(&key, &bytes)? else {
            return Err(UNPROVEN);
        };
        if record.canonical_path != path
            || record.identity != identity
            || record.parent_identity != parent_identity
            || record.binding
                != self
                    .expected
                    .binding(receipt_hash, record.binding.session_id)?
        {
            return Err(UNPROVEN);
        }
        if let Some(DirectoryReservation::ProvenExisting {
            identity: old,
            creation_reference,
            ..
        }) = self.expected.directories.iter().find(|d| d.path() == path)
        {
            if *old != identity || *creation_reference != digest(&bytes) {
                return Err(UNPROVEN);
            }
        }
        Ok(())
    }
    fn namespace_directories(&self) -> Result<Vec<PathBuf>> {
        NAMESPACES
            .into_iter()
            .map(|key| {
                Ok(self.root().join(
                    key("0".repeat(64))
                        .relative_path()?
                        .parent()
                        .ok_or(UNPROVEN)?,
                ))
            })
            .collect()
    }
    fn directory_reserved(&self, path: &Path) -> Result<bool> {
        if self.expected.directories.iter().any(|d| d.path() == path)
            || evidence::directory_reserved(&self.expected, path, &[])
        {
            return Ok(true);
        }
        // Probe only the shape here. Actual admissible reviews come from records;
        // the resolver validates their full chain before any directory effect.
        let name = path.file_name().and_then(|n| n.to_str()).ok_or(UNPROVEN)?;
        if !evidence::directory_reserved(&self.expected, path, &[name]) {
            return Ok(false);
        }
        let mut reviews = Vec::new();
        for key in self.entries()? {
            if matches!(key, EvidenceKey::Lineage(_)) {
                if let Record::Publication(link) = Record::decode(
                    &key,
                    &self.read_private(&key, RECORD_LIMIT)?.ok_or(UNPROVEN)?,
                )? {
                    reviews.push(link.frozen.review_digest);
                }
            }
        }
        Ok(evidence::directory_reserved(
            &self.expected,
            path,
            &reviews.iter().map(String::as_str).collect::<Vec<_>>(),
        ))
    }
}
// The resolver needs EvidenceIo's read boundary. This private view never exposes
// reserve/append: it cannot manufacture a reservation or a creation attestation.
impl<S: Security> EvidenceIo for Reader<S> {
    fn login(&self) -> Result<LoginIdentity> {
        self.security.held()?;
        self.base()?;
        Ok(self.login.clone())
    }
    fn verify_context(&self, receipt: &ProvisioningReceipt) -> Result<()> {
        self.security.held()?;
        if receipt != &self.expected {
            return Err(UNPROVEN);
        }
        self.base()?;
        let hash = digest(&self.root_bytes()?);
        if !receipt.directories.iter().any(|d| d.path() == self.root()) {
            return Err(UNPROVEN);
        }
        for d in &receipt.directories {
            if present(d.path())? {
                self.directory(d.path(), &hash)?;
            }
        }
        self.directory(&self.directory_container()?, &hash)?;
        self.verify_intermediates(receipt)
    }
    fn verify_effect_context(&self) -> Result<()> {
        Err(UNSUPPORTED)
    }
    fn verify_record(&self, record: &Record) -> Result<()> {
        self.security.held()?;
        match record {
            Record::Directory(d) => {
                if !self.directory_reserved(&d.canonical_path)? {
                    return Err(UNPROVEN);
                }
                self.directory(&d.canonical_path, &digest(&self.root_bytes()?))
            }
            Record::Managed(_)
            | Record::MigrationIntent(_)
            | Record::MigrationCompletion(_)
            | Record::MigrationTermination(_) => Err(UNSUPPORTED),
            _ => Ok(()),
        }
    }
    fn entries(&self) -> Result<Vec<EvidenceKey>> {
        self.entries_except(None)
    }
    fn read_private(&self, key: &EvidenceKey, limit: usize) -> Result<Option<Vec<u8>>> {
        self.security.held()?;
        self.read(&self.key_path(key)?, limit)
    }
    fn observe(&self, slot: &SlotReservation, limit: usize) -> Result<ObservedSlot> {
        self.security.held()?;
        if !self.expected.slots.contains(slot) {
            return Err(UNPROVEN);
        }
        Ok(match self.read(&slot.canonical_path, limit)? {
            Some(bytes) => ObservedSlot::Bytes(bytes),
            None => ObservedSlot::Absent,
        })
    }
    fn create_only(
        &mut self,
        _: &ProvisioningReceipt,
        _: &EvidenceKey,
        _: &[u8],
        _: &WriteFence,
        _: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        Err(UNSUPPORTED)
    }
}
impl<S: Security> Reader<S> {
    fn verify_intermediates(&self, receipt: &ProvisioningReceipt) -> Result<()> {
        // Every newly present intermediate must belong to the reservation.
        for slot in &receipt.slots {
            for p in slot.canonical_path.parent().ok_or(UNPROVEN)?.ancestors() {
                if !p.starts_with(&receipt.scope.kit_root)
                    && slot.nearest_existing_parent.starts_with(p)
                {
                    break;
                }
                if present(p)? && !receipt.directories.iter().any(|d| d.path() == p) {
                    return Err(UNPROVEN);
                }
            }
        }
        Ok(())
    }
    fn entries_except(&self, staging: Option<&Path>) -> Result<Vec<EvidenceKey>> {
        self.security.held()?;
        let mut allowed = BTreeMap::new();
        for key in [EvidenceKey::Provisioning, EvidenceKey::Managed] {
            allowed.insert(key.relative_path()?, Some(key));
        }
        let namespaces = NAMESPACES;
        for namespace in namespaces {
            allowed.insert(
                namespace("0".repeat(64))
                    .relative_path()?
                    .parent()
                    .unwrap()
                    .into(),
                None,
            );
        }
        let mut keys = Vec::new();
        self.walk(&self.root())?;
        if !present(&self.root())? {
            return Ok(keys);
        }
        let mut count = 0;
        for entry in std::fs::read_dir(self.root()).map_err(|_| UNPROVEN)? {
            count += 1;
            if count > ENTRY_LIMIT {
                return Err(UNPROVEN);
            }
            let entry = entry.map_err(|_| UNPROVEN)?;
            if staging == Some(entry.path().as_path()) {
                continue;
            }
            let relative = PathBuf::from(entry.file_name());
            let selected = allowed.get(&relative).ok_or(UNPROVEN)?;
            if let Some(key) = selected {
                keys.push(key.clone());
                continue;
            }
            let path = entry.path();
            if !self
                .walk(&path)?
                .ok_or(UNPROVEN)?
                .metadata()
                .map_err(|_| UNPROVEN)?
                .is_dir()
            {
                return Err(UNPROVEN);
            }
            for entry in std::fs::read_dir(path).map_err(|_| UNPROVEN)? {
                count += 1;
                if count > ENTRY_LIMIT {
                    return Err(UNPROVEN);
                }
                let entry = entry.map_err(|_| UNPROVEN)?;
                if staging == Some(entry.path().as_path()) {
                    continue;
                }
                let name = PathBuf::from(entry.file_name());
                let stem = name.file_stem().and_then(|s| s.to_str()).ok_or(UNPROVEN)?;
                let key = namespaces
                    .iter()
                    .map(|f| f(stem.into()))
                    .find(|k| k.relative_path().is_ok_and(|p| p == relative.join(&name)))
                    .ok_or(UNPROVEN)?;
                keys.push(key);
            }
        }
        Ok(keys)
    }
}

#[cfg(windows)]
mod native;
mod platform;
mod writer;
#[cfg(windows)]
pub use native::{WindowsCreationPlatform, WindowsCreationWriter};

#[cfg(test)]
mod tests;
