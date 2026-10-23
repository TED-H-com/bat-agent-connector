use super::{platform::CreationPlatform, *};
use crate::migration::Platform as _;

/// A synchronous effect scope; it cannot outlive its Launcher/Monitor guards.
pub struct CreationWriter<'a, S> {
    pub(super) platform: &'a mut CreationPlatform<S>,
    pending: RefCell<BTreeMap<PathBuf, DirectoryCreated>>,
    staging: RefCell<Option<PathBuf>>,
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
    pub(super) fn held(platform: &mut CreationPlatform<S>) -> Result<CreationWriter<'_, S>> {
        platform.reader.security.effect_held()?;
        Ok(CreationWriter {
            platform,
            pending: RefCell::default(),
            staging: RefCell::default(),
        })
    }
    /// Reopen recorded reservation evidence. New namespace bootstrap is unavailable.
    pub fn reserve(&mut self, authorize: &mut dyn FnMut() -> Result<()>) -> Result<OwnerEvidence> {
        let receipt = self.platform.reader.expected.clone();
        evidence::reserve(self, &receipt, authorize)
    }
    pub fn append(
        &mut self,
        owner: &OwnerEvidence,
        record: &Record,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<String> {
        if !self.pending.borrow().is_empty()
            && !matches!(record, Record::Directory(d) if self.pending.borrow().get(&d.canonical_path) == Some(d))
        {
            return Err(UNPROVEN);
        }
        let histories = self.platform.histories()?;
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
            reader.directory(parent, &self.receipt_hash()?)?;
        }
        Ok(identity)
    }
    /// Create one receipt-reserved level under the current effect guards.
    pub fn directory_level(
        &mut self,
        path: &Path,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        self.verify_effect_context()?;
        let reader = &self.platform.reader;
        reader.root_bytes()?;
        if !reader.directory_reserved(path)? || reader.walk(path)?.is_some() {
            return Err(UNPROVEN);
        }
        let parent = path.parent().ok_or(UNPROVEN)?;
        let parent_identity = self.parent_proof(parent)?;
        // Read original histories while every existing directory is recorded.
        // These immutable views remain inside the same held guard scope.
        let histories = self.platform.histories()?;
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
            &histories,
            &[],
            &mut || Ok(()),
        )?;
        self.platform
            .reader
            .directory(path, &self.receipt_hash()?)?;
        self.pending.borrow_mut().remove(path);
        Ok(())
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
        if key == &EvidenceKey::Provisioning && !present(&self.platform.reader.root())? {
            return Err(UNSUPPORTED);
        }
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
        Ok(())
    }
}
