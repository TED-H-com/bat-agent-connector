use super::{platform::CreationPlatform, *};
use crate::migration::Platform as _;

/// A synchronous effect scope; it cannot outlive its Launcher/Monitor guards.
pub struct CreationWriter<'a, S> {
    pub(super) platform: &'a mut CreationPlatform<S>,
    pending: RefCell<BTreeMap<PathBuf, DirectoryCreated>>,
    staging: RefCell<Option<PathBuf>>,
    // Granted only to the original reserve call, never reconstructed from disk.
    bootstrap: bool,
}
impl<S: Security> CreationPlatform<S> {
    pub fn with_writer<T>(
        &mut self,
        write: impl FnOnce(&mut CreationWriter<'_, S>) -> Result<T>,
    ) -> Result<T> {
        let _launcher = self.launcher_guard()?;
        let _monitor = self.monitor_guard()?;
        write(&mut CreationWriter::held(self)?)
    }
}
impl<S: Security> CreationWriter<'_, S> {
    pub fn baseline(&self, owner: &OwnerEvidence) -> Result<[Baseline; 4]> {
        let resolved = evidence::capture(self, owner)?.resolve(&self.platform.histories()?, &[])?;
        // Preparing a new review must preserve any unfinished original review.
        if !resolved.settled() {
            return Err(UNPROVEN);
        }
        resolved.baseline()
    }
    pub fn receipt(&self) -> &ProvisioningReceipt {
        self.platform.receipt()
    }
    /// Prepare only a fixed artifact parent, one recorded level at a time.
    pub fn prepare_parents(
        &mut self,
        path: &Path,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        self.verify_effect_context()?;
        if !self
            .platform
            .reader
            .expected
            .slots
            .iter()
            .any(|s| s.canonical_path.parent() == Some(path))
        {
            return Err(UNPROVEN);
        }
        let mut missing = Vec::new();
        let mut parent = path;
        while self.platform.reader.walk(parent)?.is_none() {
            if !self.platform.reader.directory_reserved(parent)? {
                return Err(UNPROVEN);
            }
            missing.push(parent.to_path_buf());
            parent = parent.parent().ok_or(UNPROVEN)?;
        }
        self.parent_proof(parent)?;
        for level in missing.into_iter().rev() {
            self.directory_level(&level, authorize)?;
        }
        self.proven_directory(path)
    }
    fn proven_directory(&self, path: &Path) -> Result<()> {
        if !self
            .platform
            .reader
            .walk(path)?
            .ok_or(UNPROVEN)?
            .metadata()
            .map_err(|_| UNPROVEN)?
            .is_dir()
        {
            return Err(UNPROVEN);
        }
        self.parent_proof(path).map(|_| ())
    }
    fn ensure_directory(
        &mut self,
        path: &Path,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        self.verify_effect_context()?;
        if present(path)? {
            self.proven_directory(path)
        } else {
            self.directory_level(path, authorize)
        }
    }
    /// Explicitly prepare evidence containers and the fixed publisher roots.
    /// Call before export/admission; read-only publisher calls never do this.
    pub fn prepare_publication_directories(
        &mut self,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        let reader = &self.platform.reader;
        let mut paths = reader.namespace_directories()?;
        paths.extend(
            reader
                .expected
                .directories
                .iter()
                .map(|d| d.path())
                .filter(|p| {
                    p.parent() == Some(reader.expected.scope.state_root.as_path())
                        && **p != reader.root()
                        && !p.starts_with(&reader.expected.scope.kit_root)
                })
                .map(Path::to_path_buf),
        );
        for path in paths {
            self.ensure_directory(&path, authorize)?;
        }
        self.platform.directories_prepared = true;
        Ok(())
    }
    /// Prepare the bounded child for an already admitted original frozen plan.
    pub fn prepare_decision_directory(
        &mut self,
        reference: &crate::connection_publish::FrozenPlanRef,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        self.verify_effect_context()?;
        if !self
            .platform
            .histories()?
            .iter()
            .any(|h| h.frozen().reference() == reference)
        {
            return Err(UNPROVEN);
        }
        let path = self
            .platform
            .publisher
            .decision_directory(&reference.review_digest);
        self.ensure_directory(&path, authorize)
    }
    pub(super) fn held(platform: &mut CreationPlatform<S>) -> Result<CreationWriter<'_, S>> {
        platform.reader.security.effect_held()?;
        Ok(CreationWriter {
            platform,
            pending: RefCell::default(),
            staging: RefCell::default(),
            bootstrap: false,
        })
    }
    /// Reserve absent slots, or reopen the complete original reservation.
    pub fn reserve(&mut self, authorize: &mut dyn FnMut() -> Result<()>) -> Result<OwnerEvidence> {
        let receipt = self.platform.reader.expected.clone();
        self.verify_effect_context()?;
        if !self.pending.borrow().is_empty() {
            return Err(UNPROVEN);
        }
        self.bootstrap = !present(&self.platform.reader.root())?
            && receipt.directories.iter().any(|d| {
                matches!(d,
                DirectoryReservation::ReservedAbsent { canonical_path }
                if *canonical_path == self.platform.reader.root())
            });
        let result = evidence::reserve(self, &receipt, authorize);
        self.bootstrap = false;
        self.pending.borrow_mut().clear();
        result
    }
    pub fn append(
        &mut self,
        owner: &OwnerEvidence,
        record: &Record,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<String> {
        record.encode()?;
        if !self.pending.borrow().is_empty()
            && !matches!(record, Record::Directory(d) if self.pending.borrow().get(&d.canonical_path) == Some(d))
        {
            return Err(UNPROVEN);
        }
        let mut histories = self.platform.histories()?;
        if let Record::Publication(link) = record {
            if !histories
                .iter()
                .any(|h| h.frozen().reference() == &link.frozen)
            {
                if histories.len() >= HISTORY_LIMIT {
                    return Err(UNPROVEN);
                }
                histories.push(self.platform.history(&link.frozen)?);
            }
        }
        evidence::append(self, owner, record, &histories, &[], authorize)
    }
    fn receipt_hash(&self) -> Result<String> {
        let reader = &self.platform.reader;
        Ok(digest(
            &if present(&reader.key_path(&EvidenceKey::Provisioning)?)? {
                reader.root_bytes()?
            } else {
                Record::Provisioning(Box::new(reader.expected.clone())).encode()?
            },
        ))
    }
    fn pending_migration(&self) -> Result<()> {
        if self.platform.migrations.pending()?.is_some() {
            return Err("MIGRATION_PENDING");
        }
        Ok(())
    }
    fn parent_proof(&self, parent: &Path) -> Result<ObjectIdentity> {
        let reader = &self.platform.reader;
        let identity = reader.identity(parent)?;
        let expected = &reader.expected;
        if parent == expected.scope.state_root {
            if identity != expected.scope.state_identity {
                return Err(UNPROVEN);
            }
        } else if let Some(slot) = expected
            .slots
            .iter()
            .find(|s| s.nearest_existing_parent == parent)
        {
            if identity != slot.parent_identity {
                return Err(UNPROVEN);
            }
        } else {
            if self.bootstrap
                && self
                    .pending
                    .borrow()
                    .get(parent)
                    .is_some_and(|d| d.identity == identity)
            {
                return Ok(identity);
            }
            reader.directory(parent, &self.receipt_hash()?)?;
        }
        Ok(identity)
    }
    // The sole strict mkdir/readback step, shared by bootstrap and ordinary levels.
    fn create_level(
        &mut self,
        path: &Path,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<DirectoryCreated> {
        self.verify_effect_context()?;
        let reader = &self.platform.reader;
        if !reader.directory_reserved(path)?
            || reader.walk(path)?.is_some()
            || (self.bootstrap && path != reader.root() && path != reader.directory_container()?)
        {
            return Err(UNPROVEN);
        }
        let parent = path.parent().ok_or(UNPROVEN)?;
        let parent_identity = self.parent_proof(parent)?;
        authorize()?;
        self.verify_effect_context()?;
        if self.parent_proof(parent)? != parent_identity {
            return Err(UNPROVEN);
        }
        crate::publication_state::create_new_directory(path)?;
        let record = DirectoryCreated {
            binding: self
                .platform
                .reader
                .expected
                .binding(&self.receipt_hash()?, self.login()?.session_id)?,
            canonical_path: path.into(),
            parent_identity,
            identity: self.platform.reader.identity(path)?,
        };
        self.pending
            .borrow_mut()
            .insert(path.into(), record.clone());
        Ok(record)
    }
    fn record_level(
        &mut self,
        record: DirectoryCreated,
        histories: &[crate::connection_publish::PublicationHistory],
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        let path = record.canonical_path.clone();
        let owner = self
            .platform
            .reader
            .expected
            .owner(&self.platform.reader.root_bytes()?)?;
        // The directory and its record are one level's authorized effect.
        evidence::append(
            self,
            &owner,
            &Record::Directory(record),
            histories,
            &[],
            authorize,
        )?;
        self.platform
            .reader
            .directory(&path, &self.receipt_hash()?)?;
        self.pending.borrow_mut().remove(&path);
        Ok(())
    }
    /// Create and record one reserved level before permitting a descendant.
    pub fn directory_level(
        &mut self,
        path: &Path,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        if self.bootstrap || !self.pending.borrow().is_empty() {
            return Err(UNPROVEN);
        }
        self.verify_effect_context()?;
        self.platform.reader.root_bytes()?;
        // Read original histories before entering the unrecorded-directory interval.
        let histories = self.platform.histories()?;
        let owner = self
            .platform
            .reader
            .expected
            .owner(&self.platform.reader.root_bytes()?)?;
        evidence::capture(self, &owner)?.resolve(&histories, &[])?;
        let record = self.create_level(path, authorize)?;
        // The directory and its record are one level's authorized effect.
        self.record_level(record, &histories, &mut || Ok(()))
    }
}
impl<S: Security> EvidenceIo for CreationWriter<'_, S> {
    fn login(&self) -> Result<LoginIdentity> {
        self.platform.reader.login()
    }
    fn verify_context(&self, receipt: &ProvisioningReceipt) -> Result<()> {
        let reader = &self.platform.reader;
        reader.security.effect_held()?;
        if receipt != &reader.expected
            || !receipt
                .directories
                .iter()
                .any(|d| d.path() == reader.root())
        {
            return Err(UNPROVEN);
        }
        reader.base()?;
        let hash = self.receipt_hash()?;
        let has_receipt = present(&reader.key_path(&EvidenceKey::Provisioning)?)?;
        for path in receipt
            .directories
            .iter()
            .map(|d| d.path().to_path_buf())
            .chain(reader.namespace_directories()?)
        {
            if !present(&path)? {
                continue;
            }
            if let Some(pending) = self.pending.borrow().get(&path) {
                if reader.identity(&path)? != pending.identity
                    || reader.identity(path.parent().ok_or(UNPROVEN)?)? != pending.parent_identity
                {
                    return Err(UNPROVEN);
                }
            } else if has_receipt {
                reader.directory(&path, &hash)?;
            } else {
                return Err(UNPROVEN);
            }
        }
        reader.verify_intermediates(receipt)
    }
    fn verify_effect_context(&self) -> Result<()> {
        self.platform.reader.security.effect_held()?;
        self.verify_context(&self.platform.reader.expected)?;
        self.pending_migration()?;
        if self.platform.observation()?.is_some() {
            return Err("MONITOR_ALREADY_RUNNING");
        }
        self.platform.reader.base()?;
        if self
            .platform
            .process_observation(present(&self.platform.reader.expected.scope.pointer)?)?
            .is_some()
        {
            return Err("MONITOR_ALREADY_RUNNING");
        }
        self.platform
            .reader
            .security
            .unpublished(&self.platform.roaming.join("bat-fleet-monitor-launch.json"))?;
        if self.platform.reader.security.login()? != self.platform.reader.login {
            return Err("OTHER_LOGIN_OWNER");
        }
        self.platform.reader.security.effect_held()
    }
    fn verify_no_pending_migration(&self) -> Result<()> {
        self.verify_effect_context()?;
        self.pending_migration()
    }
    fn verify_record(&self, record: &Record) -> Result<()> {
        if let Record::Directory(directory) = record {
            if self.pending.borrow().get(&directory.canonical_path) == Some(directory) {
                return self.verify_context(&self.platform.reader.expected);
            }
        }
        self.platform.reader.verify_record(record)
    }
    fn entries(&self) -> Result<Vec<EvidenceKey>> {
        self.platform
            .reader
            .entries_except(self.staging.borrow().as_deref())
    }
    fn read_private(&self, key: &EvidenceKey, limit: usize) -> Result<Option<Vec<u8>>> {
        self.platform.reader.read_private(key, limit)
    }
    fn observe(&self, slot: &SlotReservation, limit: usize) -> Result<ObservedSlot> {
        self.platform.reader.observe(slot, limit)
    }
    fn create_only(
        &mut self,
        receipt: &ProvisioningReceipt,
        key: &EvidenceKey,
        bytes: &[u8],
        fence: &WriteFence,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        self.verify_context(receipt)?;
        let record = Record::decode(key, bytes)?;
        self.verify_record(&record)?;
        let bootstrap =
            if key == &EvidenceKey::Provisioning && !present(&self.platform.reader.root())? {
                if !self.bootstrap {
                    return Err(UNSUPPORTED);
                }
                let root = self.platform.reader.root();
                let container = self.platform.reader.directory_container()?;
                Some([
                    self.create_level(&root, authorize)?,
                    self.create_level(&container, authorize)?,
                ])
            } else {
                None
            };
        let path = self.platform.reader.key_path(key)?;
        let parent = path.parent().ok_or(UNPROVEN)?;
        if !present(parent)? {
            return Err(UNPROVEN);
        }
        authorize()?;
        let mut verify = || {
            self.verify_effect_context()?;
            fence.verify(self, receipt)
        };
        let result = crate::compare_replace::replace_observed(
            &path,
            None,
            Some(bytes),
            crate::compare_replace::Policy {
                limit: if key == &EvidenceKey::Provisioning {
                    RECEIPT_LIMIT
                } else {
                    RECORD_LIMIT
                },
                too_large: UNPROVEN,
                invalid_path: UNPROVEN,
                changed: "CREATION_EVIDENCE_CONFLICT",
                unavailable: UNPROVEN,
                prefix: ".fleet-creation",
                create_only: true,
                skip_equal: false,
                sync_directory: true,
                private: true,
            },
            || {
                self.platform.reader.read(
                    &path,
                    if key == &EvidenceKey::Provisioning {
                        RECEIPT_LIMIT
                    } else {
                        RECORD_LIMIT
                    },
                )
            },
            Some(&mut verify),
            &mut |path| {
                *self.staging.borrow_mut() = Some(path.into());
            },
        );
        self.staging.borrow_mut().take();
        result?;
        if self
            .read_private(
                key,
                if key == &EvidenceKey::Provisioning {
                    RECEIPT_LIMIT
                } else {
                    RECORD_LIMIT
                },
            )?
            .as_deref()
            != Some(bytes)
        {
            return Err(UNPROVEN);
        }
        if let Some(records) = bootstrap {
            // Only these two live, just-created objects may precede their records.
            // No history can exist in the namespace whose absence reserve proved.
            for record in records {
                self.record_level(record, &[], authorize)?;
            }
        }
        Ok(())
    }
}
