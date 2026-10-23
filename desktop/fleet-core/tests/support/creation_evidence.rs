use bat_fleet_core::{
    connection_publish::{self, FrozenHistory, FrozenPlanRef, PublicationHistory, Publisher},
    connection_setup::{self, Artifact, FileOwnership, OwnerEvidence, PublicationPlan},
    creation_evidence::*,
    digest,
    discovery::{Backend, MonitorIdentity},
    migration::{self, Owner},
    process_adapter::LoginIdentity,
    Result,
};
use std::{
    cell::{Cell, RefCell},
    io::Write,
    path::{Path, PathBuf},
    rc::Rc,
};
#[path = "connection_setup.rs"]
pub mod generator;

type RecordDrift = Rc<RefCell<Option<(EvidenceKey, Vec<u8>)>>>;

pub struct Guard(Rc<Cell<bool>>);
impl Drop for Guard {
    fn drop(&mut self) {
        self.0.set(false);
    }
}
#[derive(Clone)]
pub struct Native {
    pub root: PathBuf,
    pub receipt: ProvisioningReceipt,
    pub owner: OwnerEvidence,
    pub launcher: Rc<Cell<bool>>,
    pub monitor: Rc<Cell<bool>>,
    pub session: Rc<Cell<u32>>,
    pub login_sid: Rc<RefCell<String>>,
    pub running: Rc<RefCell<Option<MonitorIdentity>>>,
    pub refused_record: Rc<RefCell<Option<EvidenceKey>>>,
    pub record_drift: RecordDrift,
    pub slot_drift_after: Rc<Cell<Option<usize>>>,
    pub observations_seen: Rc<Cell<usize>>,
    pub observed_limits: Rc<RefCell<Vec<(Artifact, usize)>>>,
    expected_scope: Scope,
    expected_startup: StartupBinding,
    pub context: Rc<Cell<bool>>,
    pub external: Rc<Cell<Option<Artifact>>>,
    pub unavailable: Rc<Cell<Option<Artifact>>>,
    pub drift: Rc<Cell<bool>>,
    pub create_race: Rc<Cell<bool>>,
    pub fail_after_create: Rc<Cell<bool>>,
    creating_namespace: Rc<Cell<bool>>,
}
impl Native {
    pub fn held(&self) {
        assert!(self.launcher.get() && self.monitor.get());
    }
    pub fn evidence_held(&self) {
        assert!(self.launcher.get());
    }
    pub fn evidence_guard(&mut self) -> Guard {
        migration::Platform::launcher_guard(self).unwrap()
    }
    pub fn guards(&mut self) -> (Guard, Guard) {
        (
            migration::Platform::launcher_guard(self).unwrap(),
            migration::Platform::monitor_guard(self).unwrap(),
        )
    }
    pub fn publisher(&self) -> Publisher {
        Publisher::new(
            &self.root.join("native/fleet.json"),
            &self.root.join("Startup"),
            &self.root.join("user"),
        )
        .unwrap()
    }
    pub fn evidence_path(&self, key: &EvidenceKey) -> PathBuf {
        self.root
            .join("native/fleet-creation")
            .join(key.relative_path().unwrap())
    }
    pub fn frozen_path(&self, reference: &FrozenPlanRef) -> PathBuf {
        self.root
            .join("native/fleet-publication-plans")
            .join(format!("{}.json", reference.review_digest))
    }
    pub fn try_history(&self, reference: &FrozenPlanRef) -> Result<PublicationHistory> {
        let bytes = std::fs::read(self.frozen_path(reference)).map_err(|_| "HISTORY_IO")?;
        let frozen = FrozenHistory::decode(reference, &bytes).map_err(|_| "HISTORY_INVALID")?;
        self.publisher()
            .read_evidence_history(frozen, &self.clone())
            .map_err(|_| "HISTORY_INVALID")
    }
    pub fn history(&self, reference: &FrozenPlanRef) -> PublicationHistory {
        self.try_history(reference).unwrap()
    }
    pub fn histories(&self) -> Vec<PublicationHistory> {
        let mut seen = std::collections::BTreeSet::new();
        self.entries()
            .unwrap()
            .iter()
            .filter_map(|key| {
                let bytes = self.read_private(key, RECEIPT_LIMIT).unwrap().unwrap();
                if let Record::Publication(link) = Record::decode(key, &bytes).unwrap() {
                    seen.insert((
                        link.frozen.review_digest.clone(),
                        link.frozen.sha256.clone(),
                    ))
                    .then(|| self.history(&link.frozen))
                } else {
                    None
                }
            })
            .collect()
    }
    pub fn migrations(
        &self,
        candidate: Option<&Record>,
    ) -> Result<Vec<migration::MigrationHistory>> {
        let mut ids = std::collections::BTreeSet::new();
        let mut collect = |record: &Record| match record {
            Record::MigrationIntent(i) => {
                ids.insert(i.migration_id.clone());
            }
            Record::MigrationTermination(t) => {
                if let MigrationOutcome::Restored { migration_id, .. } = &t.outcome {
                    ids.insert(migration_id.clone());
                }
            }
            _ => (),
        };
        for key in self.entries()? {
            collect(&Record::decode(
                &key,
                &self.read_private(&key, RECEIPT_LIMIT)?.ok_or("MISSING")?,
            )?);
        }
        if let Some(record) = candidate {
            collect(record);
        }
        let store = migration::Store::new(&self.receipt.scope.pointer, &self.root.join("Startup"))?;
        ids.iter().map(|id| store.observe_history(id)).collect()
    }
    pub fn resolution(&mut self) -> Result<Resolution> {
        let _launcher = self.evidence_guard();
        capture(self, &self.owner)?.resolve(&self.histories(), &self.migrations(None)?)
    }
    pub fn append(&mut self, record: &Record) -> Result<String> {
        let _launcher = self.evidence_guard();
        let _monitor = if matches!(
            record,
            Record::MigrationCompletion(_) | Record::MigrationTermination(_)
        ) {
            None
        } else {
            Some(migration::Platform::monitor_guard(self)?)
        };
        let mut histories = self.histories();
        if let Record::Publication(link) = record {
            if !histories
                .iter()
                .any(|h| h.frozen().reference() == &link.frozen)
            {
                histories.push(self.try_history(&link.frozen)?);
            }
        }
        let migrations = self.migrations(Some(record))?;
        append(
            self,
            &self.owner.clone(),
            record,
            &histories,
            &migrations,
            &mut || Ok(()),
        )
    }
}
impl migration::Platform for Native {
    type LauncherGuard = Guard;
    type MonitorGuard = Guard;
    fn launcher_guard(&mut self) -> Result<Guard> {
        if self.launcher.replace(true) {
            return Err("BUSY");
        }
        Ok(Guard(self.launcher.clone()))
    }
    fn monitor_guard(&mut self) -> Result<Guard> {
        assert!(self.launcher.get());
        if self.running.borrow().is_some() {
            return Err("MONITOR_ALREADY_RUNNING");
        }
        if self.monitor.replace(true) {
            return Err("BUSY");
        }
        Ok(Guard(self.monitor.clone()))
    }
    fn login(&self) -> Result<LoginIdentity> {
        EvidenceIo::login(self)
    }
    fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
        self.evidence_held();
        Ok(self.running.borrow().clone().map(|mut monitor| {
            monitor.ownership = if monitor.process.login.owner_sid == *self.login_sid.borrow()
                && monitor.process.login.session_id == self.session.get()
            {
                bat_fleet_core::discovery::Ownership::CurrentLogin
            } else {
                bat_fleet_core::discovery::Ownership::OtherLogin
            };
            monitor
        }))
    }
    fn request_quit(&mut self, _: &Owner) -> Result<()> {
        panic!("no process effects")
    }
    fn launch(&mut self, _: Backend) -> Result<()> {
        panic!("no process effects")
    }
    fn validate_config(&self, bytes: &[u8], backend: Backend) -> Result<()> {
        self.held();
        let v: serde_json::Value = serde_json::from_slice(bytes).unwrap();
        if v["backend"] == serde_json::to_value(backend).unwrap() {
            Ok(())
        } else {
            Err("INVALID_CONFIG")
        }
    }
    fn classify_shortcut(&self, _: &[u8]) -> Result<Backend> {
        panic!("no startup effects")
    }
    fn shortcut(&self, _: Backend) -> Result<Vec<u8>> {
        panic!("no startup effects")
    }
}
impl connection_publish::Platform for Native {
    fn ownership(
        &self,
        owner: &OwnerEvidence,
        artifact: Artifact,
        path: &Path,
        current: Option<&[u8]>,
    ) -> Result<FileOwnership> {
        self.evidence_held();
        Ok(
            match capture(self, owner)
                .and_then(|c| c.resolve(&self.histories(), &self.migrations(None)?))
            {
                Ok(resolution) => resolution.ownership(owner, artifact, path, current),
                Err(_) => self
                    .observe(
                        &self.receipt.slots[ARTIFACTS.iter().position(|a| *a == artifact).unwrap()],
                        if artifact == Artifact::Installation {
                            16_384
                        } else {
                            1_048_576
                        },
                    )
                    .unwrap_or(ObservedSlot::Unavailable)
                    .unproven_ownership(),
            },
        )
    }
    fn verify_private_state(&self, path: &Path, owner: &OwnerEvidence) -> Result<()> {
        self.evidence_held();
        if owner != &self.owner
            || !self.context.get()
            || !path.starts_with(self.root.join("native"))
        {
            return Err("UNPROVEN");
        }
        no_links(path)?;
        Ok(())
    }
}
fn no_links(path: &Path) -> Result<()> {
    for p in path.ancestors() {
        match p.symlink_metadata() {
            Ok(m) if m.file_type().is_symlink() => return Err("LINK"),
            Ok(_) => (),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(_) => return Err("UNREADABLE"),
        }
    }
    Ok(())
}
pub fn bounded_file(path: &Path, limit: usize) -> Result<Option<Vec<u8>>> {
    use std::io::Read;
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT);
    }
    let file = match options.open(path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("IO"),
    };
    let metadata = file.metadata().map_err(|_| "IO")?;
    if !metadata.is_file() {
        return Err("TYPE");
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err("LINK");
        }
    }
    let mut bytes = Vec::new();
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "IO")?;
    if bytes.len() > limit {
        return Err("BOUND");
    }
    Ok(Some(bytes))
}
impl EvidenceIo for Native {
    fn login(&self) -> Result<LoginIdentity> {
        self.evidence_held();
        Ok(LoginIdentity {
            owner_sid: self.login_sid.borrow().clone(),
            session_id: self.session.get(),
        })
    }
    fn verify_context(&self, receipt: &ProvisioningReceipt) -> Result<()> {
        self.evidence_held();
        if !self.context.get()
            || receipt.scope != self.expected_scope
            || receipt.startup != self.expected_startup
        {
            return Err("CONTEXT_CHANGED");
        }
        for s in &receipt.slots {
            no_links(&s.canonical_path)?;
        }
        no_links(&self.evidence_path(&EvidenceKey::Provisioning))
    }
    fn verify_effect_context(&self) -> Result<()> {
        self.held();
        if self.running.borrow().is_some() {
            return Err("MONITOR_ALREADY_RUNNING");
        }
        Ok(())
    }
    fn verify_record(&self, record: &Record) -> Result<()> {
        self.evidence_held();
        if self.refused_record.borrow().as_ref() == Some(&record.key()) {
            return Err("RECORD_UNPROVEN");
        }
        Ok(())
    }
    fn verify_no_pending_migration(&self) -> Result<()> {
        self.held();
        let store = migration::Store::new(&self.receipt.scope.pointer, &self.root.join("Startup"))?;
        if store.pending()?.is_some() {
            return Err("MIGRATION_PENDING");
        }
        Ok(())
    }
    fn entries(&self) -> Result<Vec<EvidenceKey>> {
        self.evidence_held();
        let root = self.root.join("native/fleet-creation");
        if !root.exists() {
            return Ok(Vec::new());
        }
        if !root.join("provision.json").exists()
            && !self.creating_namespace.get()
            && std::fs::read_dir(&root).unwrap().next().is_none()
        {
            return Err("ORPHAN_NAMESPACE");
        }
        let mut out = Vec::new();
        for e in std::fs::read_dir(&root).map_err(|_| "IO")? {
            let e = e.map_err(|_| "IO")?;
            let name = e.file_name().to_string_lossy().into_owned();
            match name.as_str() {
                "provision.json" => out.push(EvidenceKey::Provisioning),
                "managed.json" => out.push(EvidenceKey::Managed),
                "lineage" | "closures" | "directories" | "migrations" => {
                    for child in std::fs::read_dir(e.path()).map_err(|_| "IO")? {
                        let child = child.map_err(|_| "IO")?;
                        let n = child.file_name().to_string_lossy().into_owned();
                        let h = n.strip_suffix(".json").ok_or("NAME")?.to_owned();
                        out.push(match name.as_str() {
                            "lineage" => EvidenceKey::Lineage(h),
                            "closures" => EvidenceKey::Closure(h),
                            "directories" => EvidenceKey::Directory(h),
                            _ => EvidenceKey::MigrationCompletion(h),
                        });
                    }
                }
                _ => return Err("UNKNOWN_ENTRY"),
            }
            if out.len() > ENTRY_LIMIT {
                return Err("BOUND");
            }
        }
        if out.is_empty() && !root.join("provision.json").exists() && !self.creating_namespace.get()
        {
            return Err("ORPHAN_NAMESPACE");
        }
        Ok(out)
    }
    fn read_private(&self, key: &EvidenceKey, limit: usize) -> Result<Option<Vec<u8>>> {
        self.evidence_held();
        let p = self.evidence_path(key);
        no_links(&p)?;
        bounded_file(&p, limit)
    }

    fn observe(&self, slot: &SlotReservation, limit: usize) -> Result<ObservedSlot> {
        self.evidence_held();
        self.observed_limits
            .borrow_mut()
            .push((slot.artifact, limit));
        no_links(&slot.canonical_path)?;
        if self.unavailable.get() == Some(slot.artifact) {
            return Err("READ_DENIED");
        }
        if self.drift.replace(false) {
            std::fs::write(&slot.canonical_path, b"appeared during capture").unwrap();
        }
        let result = match bounded_file(&slot.canonical_path, limit) {
            Ok(Some(b)) if self.external.get() == Some(slot.artifact) => {
                Ok(ObservedSlot::External(b))
            }
            Ok(Some(b)) => Ok(ObservedSlot::Bytes(b)),
            Ok(None) => Ok(ObservedSlot::Absent),
            Err(_) => Err("IO"),
        };
        let seen = self.observations_seen.get() + 1;
        self.observations_seen.set(seen);
        if self.slot_drift_after.get() == Some(seen) {
            self.slot_drift_after.set(None);
            std::fs::write(
                self.receipt.path(Artifact::Inventory),
                b"changed between observations",
            )
            .unwrap();
        }
        if slot.artifact == Artifact::Installation {
            if let Some((key, bytes)) = self.record_drift.borrow_mut().take() {
                std::fs::write(self.evidence_path(&key), bytes).unwrap();
            }
        }
        result
    }
    fn create_only(
        &mut self,
        receipt: &ProvisioningReceipt,
        key: &EvidenceKey,
        bytes: &[u8],
        fence: &WriteFence,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        self.evidence_held();
        authorize()?;
        self.verify_context(receipt)?;
        if self.create_race.replace(false) {
            std::fs::write(receipt.path(Artifact::Inventory), b"race").unwrap();
        }
        if key == &EvidenceKey::Provisioning {
            for s in &receipt.slots {
                if self.observe(
                    s,
                    if s.artifact == Artifact::Installation {
                        16_384
                    } else {
                        1_048_576
                    },
                )? != ObservedSlot::Absent
                {
                    return Err("APPEARED");
                }
            }
        }
        fence.verify(self, receipt)?;
        self.creating_namespace.set(true);
        let _namespace = Guard(self.creating_namespace.clone());
        let path = self.evidence_path(key);
        let parent = path.parent().unwrap();
        if !parent.exists() {
            authorize()?;
            std::fs::create_dir_all(parent).map_err(|_| "IO")?;
        }
        authorize()?;
        self.verify_context(receipt)?;
        fence.verify(self, receipt)?;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(path).map_err(|_| "CONFLICT")?;
        file.write_all(bytes).map_err(|_| "IO")?;
        file.sync_all().map_err(|_| "IO")?;
        if self.fail_after_create.replace(false) {
            return Err("INTERRUPTED");
        }
        Ok(())
    }
}
pub struct Fixture {
    pub root: PathBuf,
    pub native: Native,
    pub input: connection_setup::ReviewedSetup,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
impl Fixture {
    pub fn unreserved() -> Self {
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("fleet-creation-{:032x}", rand::random::<u128>()));
        for p in ["kit/client/bat-profiles", "native", "Startup", "user/.ssh"] {
            std::fs::create_dir_all(root.join(p)).unwrap();
        }
        let mut input = generator::setup();
        input.root = root.join("kit");
        std::fs::write(root.join("user/.ssh/config"), &input.ssh_source).unwrap();
        let identity = ObjectIdentity {
            volume: 9,
            file_id: "5".repeat(32),
        };
        let scope = Scope {
            platform: "windows".into(),
            pointer: root.join("native/fleet.json"),
            kit_root: input.root.clone(),
            state_root: root.join("native"),
            startup_role_record: root.join("native/startup-role.json"),
            state_identity: identity.clone(),
        };
        let paths = [
            scope.kit_root.join("client/fleet-inventory.json"),
            scope.kit_root.join("client/bat-profiles/index.json"),
            scope.kit_root.join("client/ssh-config"),
            scope.pointer.clone(),
        ];
        let receipt = ProvisioningReceipt {
            startup: StartupBinding {
                installation_ref: "D0F9B7AA-1651-4b7b-8F7C-4C76BFD504AC".into(),
                role_record_sha256: digest(b"synthetic startup role"),
                generation: 1,
                role: StartupRole::Both,
            },
            creator: Creator {
                owner_sid: "S-1-5-21-123-456-789-1001".into(),
                session_id: 7,
                reservation_id: "a".repeat(32),
            },
            fleet_creation_id: "b".repeat(32),
            scope,
            directories: vec![
                DirectoryReservation::ProvenExisting {
                    canonical_path: input.root.clone(),
                    identity: identity.clone(),
                    creation_reference: "synthetic-native-directory-creation".into(),
                },
                DirectoryReservation::ReservedAbsent {
                    canonical_path: root.join("native/fleet-creation"),
                },
            ],
            slots: std::array::from_fn(|i| SlotReservation {
                artifact: ARTIFACTS[i],
                canonical_path: paths[i].clone(),
                observed: "absent".into(),
                before_sha256: None,
                nearest_existing_parent: paths[i].parent().unwrap().into(),
                parent_identity: identity.clone(),
                missing_components: vec![paths[i].file_name().unwrap().to_str().unwrap().into()],
            }),
            managed_connector: None,
        };
        let owner = receipt
            .owner(
                &Record::Provisioning(Box::new(receipt.clone()))
                    .encode()
                    .unwrap(),
            )
            .unwrap();
        input.owner = owner.clone();
        let native = Native {
            root: root.clone(),
            expected_scope: receipt.scope.clone(),
            expected_startup: receipt.startup.clone(),
            login_sid: Rc::new(RefCell::new(owner.owner_sid.clone())),
            running: Rc::new(RefCell::new(None)),
            refused_record: Rc::new(RefCell::new(None)),
            record_drift: Rc::new(RefCell::new(None)),
            slot_drift_after: Rc::new(Cell::new(None)),
            observations_seen: Rc::new(Cell::new(0)),
            observed_limits: Rc::new(RefCell::new(Vec::new())),
            receipt,
            owner,
            launcher: Rc::new(Cell::new(false)),
            monitor: Rc::new(Cell::new(false)),
            session: Rc::new(Cell::new(7)),
            context: Rc::new(Cell::new(true)),
            external: Rc::new(Cell::new(None)),
            unavailable: Rc::new(Cell::new(None)),
            drift: Rc::new(Cell::new(false)),
            create_race: Rc::new(Cell::new(false)),
            fail_after_create: Rc::new(Cell::new(false)),
            creating_namespace: Rc::new(Cell::new(false)),
        };
        Self {
            root,
            native,
            input,
        }
    }
    pub fn new() -> Self {
        let mut f = Self::unreserved();
        f.reserve().unwrap();
        f
    }
    pub fn reserve(&mut self) -> Result<OwnerEvidence> {
        let _guards = self.native.guards();
        {
            let receipt = self.native.receipt.clone();
            reserve(&mut self.native, &receipt, &mut || Ok(()))
        }
    }
    pub fn path(&self, a: Artifact) -> PathBuf {
        self.native.receipt.path(a).into()
    }
    pub fn plan(&self) -> PublicationPlan {
        connection_setup::build(&self.input).unwrap()
    }
    pub fn reference(&self, plan: &PublicationPlan) -> FrozenPlanRef {
        FrozenPlanRef {
            review_digest: plan.review_digest.clone(),
            sha256: digest(
                &std::fs::read(self.root.join(format!(
                    "native/fleet-publication-plans/{}.json",
                    plan.review_digest
                )))
                .unwrap(),
            ),
        }
    }
    pub fn prepare(&mut self, plan: &PublicationPlan) -> PublicationLink {
        let reference = self
            .native
            .publisher()
            .export_plan(plan, &mut self.native)
            .unwrap();
        let resolution = self.native.resolution().unwrap();
        PublicationLink {
            binding: self
                .native
                .receipt
                .binding(
                    parse_owner_reference(&self.native.owner).unwrap(),
                    self.native.session.get(),
                )
                .unwrap(),
            predecessor_sha256: resolution.head().into(),
            predecessor_terminal: resolution.terminal().cloned(),
            frozen: reference,
            generator_version: plan.generator_version,
            baseline: resolution.baseline().unwrap(),
            acceptance_reference: format!("native-review:{}", &plan.review_digest[..32]),
        }
    }
    pub fn admit(&mut self, plan: &PublicationPlan) -> (PublicationLink, String) {
        let link = self.prepare(plan);
        let hash = self
            .native
            .append(&Record::Publication(link.clone()))
            .unwrap();
        (link, hash)
    }
    pub fn publish(&mut self, plan: &PublicationPlan) {
        self.native
            .publisher()
            .publish_authorized(plan, &mut self.native, &mut |_, _| Ok(()), &mut |_, _| {
                Ok(())
            })
            .unwrap();
    }
    pub fn ownership(&mut self, a: Artifact) -> FileOwnership {
        let current = std::fs::read(self.path(a)).ok();
        let _launcher = self.native.evidence_guard();
        connection_publish::Platform::ownership(
            &self.native,
            &self.native.owner,
            a,
            &self.path(a),
            current.as_deref(),
        )
        .unwrap()
    }
    pub fn refresh(&mut self) {
        self.input.existing.clear();
        for a in ARTIFACTS {
            if let Ok(bytes) = std::fs::read(self.path(a)) {
                let ownership = self.ownership(a);
                self.input.existing.insert(
                    a,
                    connection_setup::ExistingFile {
                        sha256: digest(&bytes),
                        bytes,
                        ownership,
                    },
                );
            }
        }
    }
}

pub struct MigrationNative {
    pub native: Native,
}
impl migration::Platform for MigrationNative {
    type LauncherGuard = Guard;
    type MonitorGuard = Guard;
    fn launcher_guard(&mut self) -> Result<Guard> {
        migration::Platform::launcher_guard(&mut self.native)
    }
    fn monitor_guard(&mut self) -> Result<Guard> {
        migration::Platform::monitor_guard(&mut self.native)
    }
    fn login(&self) -> Result<LoginIdentity> {
        assert!(self.native.launcher.get());
        Ok(LoginIdentity {
            owner_sid: self.native.login_sid.borrow().clone(),
            session_id: self.native.session.get(),
        })
    }
    fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
        assert!(self.native.launcher.get());
        let login = self.login()?;
        Ok(self.native.running.borrow().clone().map(|mut monitor| {
            monitor.ownership = if monitor.process.login == login {
                bat_fleet_core::discovery::Ownership::CurrentLogin
            } else {
                bat_fleet_core::discovery::Ownership::OtherLogin
            };
            monitor
        }))
    }
    fn request_quit(&mut self, _: &Owner) -> Result<()> {
        panic!("no existing process")
    }
    fn launch(&mut self, backend: Backend) -> Result<()> {
        *self.native.running.borrow_mut() = Some(MonitorIdentity {
            backend,
            ownership: bat_fleet_core::discovery::Ownership::CurrentLogin,
            legacy: false,
            instance: Some("7".repeat(32)),
            directories: vec![],
            record: None,
            process: bat_fleet_core::process_adapter::ProcessSnapshot {
                pid: 77,
                created_filetime: 123456077,
                executable: "C:\\Synthetic\\monitor.exe".into(),
                arguments: vec!["--fixed".into()],
                login: self.login()?,
            },
        });
        Ok(())
    }
    fn validate_config(&self, bytes: &[u8], backend: Backend) -> Result<()> {
        let v: serde_json::Value = serde_json::from_slice(bytes).unwrap();
        if v == serde_json::json!({"kit_root":self.native.receipt.scope.kit_root,"backend":backend})
        {
            Ok(())
        } else {
            Err("CONFIG")
        }
    }
    fn classify_shortcut(&self, b: &[u8]) -> Result<Backend> {
        match b {
            b"rust" => Ok(Backend::Rust),
            b"powershell" => Ok(Backend::Powershell),
            _ => Err("SHORTCUT"),
        }
    }
    fn shortcut(&self, b: Backend) -> Result<Vec<u8>> {
        Ok(if b == Backend::Rust {
            b"rust".to_vec()
        } else {
            b"powershell".to_vec()
        })
    }
}
pub fn migration_case() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    let before = std::fs::read(f.path(Artifact::Installation)).unwrap();
    let resolution = f.native.resolution().unwrap();
    let store =
        migration::Store::new(&f.path(Artifact::Installation), &f.root.join("Startup")).unwrap();
    let mut producer = MigrationNative {
        native: f.native.clone(),
    };
    let preview = store.preview(&mut producer, Backend::Rust).unwrap();
    let intent = MigrationIntent {
        binding: f
            .native
            .receipt
            .binding(parse_owner_reference(&f.native.owner).unwrap(), 7)
            .unwrap(),
        predecessor_sha256: resolution.head().into(),
        predecessor_terminal: resolution.terminal().unwrap().clone(),
        migration_id: "9".repeat(32),
        startup_path: f.root.join("Startup/Open BAT.lnk"),
        review_fingerprint: preview.fingerprint().into(),
        before_sha256: digest(&before),
        restores: None,
        acceptance_reference: "original-native-migration".into(),
    };
    let intent_hash = f
        .native
        .append(&Record::MigrationIntent(intent.clone()))
        .unwrap();
    assert_eq!(
        f.native
            .append(&Record::MigrationIntent(intent.clone()))
            .unwrap(),
        intent_hash
    );
    let after = serde_json::to_vec(&serde_json::json!({
        "kit_root": f.native.receipt.scope.kit_root,
        "backend": Backend::Powershell,
    }))
    .unwrap();
    store
        .begin(
            &mut producer,
            &intent.migration_id,
            &preview,
            Backend::Powershell,
            &after,
            false,
        )
        .unwrap();
    assert_eq!(
        store.advance(&mut producer, &intent.migration_id),
        Ok(migration::Progress::WaitingLaunch)
    );
    let saved = store.read_history(&intent.migration_id).unwrap();
    assert_eq!(saved.phase(), migration::Phase::LaunchRequested);
    {
        let _launcher = f.native.evidence_guard();
        let c = capture(&f.native, &f.native.owner).unwrap();
        let fresh = f.native.histories();
        let pending = c
            .resolve(
                &fresh,
                &[store.observe_history(&intent.migration_id).unwrap()],
            )
            .unwrap();
        assert!(pending.baseline().is_err());
        assert!(!pending.settled());
    }
    assert_eq!(
        store.advance(&mut producer, &intent.migration_id),
        Ok(migration::Progress::Complete)
    );
    let saved = store.read_history(&intent.migration_id).unwrap();
    assert_eq!(saved.config(false).unwrap(), before);
    assert_eq!(saved.config(true).unwrap(), after);
    let completion = MigrationCompletion {
        binding: intent.binding.clone(),
        intent_sha256: intent_hash,
        saved_sha256: saved.sha256().into(),
        after_sha256: digest(&after),
    };
    {
        let _launcher = f.native.evidence_guard();
        let histories = f.native.histories();
        let intent_path = f
            .native
            .evidence_path(&Record::MigrationIntent(intent.clone()).key());
        let original_intent = std::fs::read(&intent_path).unwrap();
        std::fs::remove_file(&intent_path).unwrap();
        let unbound = capture(&f.native, &f.input.owner)
            .unwrap()
            .resolve(
                &histories,
                &[store.observe_history(&intent.migration_id).unwrap()],
            )
            .unwrap();
        for artifact in ARTIFACTS {
            let bytes = std::fs::read(f.path(artifact)).unwrap();
            let ownership =
                unbound.ownership(&f.input.owner, artifact, &f.path(artifact), Some(&bytes));
            if artifact == Artifact::Installation {
                assert_eq!(ownership, FileOwnership::Unknown);
            } else {
                assert!(matches!(ownership, FileOwnership::Owned { .. }));
            }
        }
        std::fs::write(&intent_path, &original_intent).unwrap();
        for field in ["predecessor", "path", "session", "review", "restore"] {
            let mut wrong = intent.clone();
            match field {
                "predecessor" => wrong.predecessor_sha256 = digest(b"foreign predecessor"),
                "path" => wrong.startup_path = f.root.join("foreign/Open BAT.lnk"),
                "session" => wrong.binding.session_id += 1,
                "review" => wrong.review_fingerprint = digest(b"another review"),
                "restore" => wrong.restores = Some("8".repeat(32)),
                _ => unreachable!(),
            }
            let record = Record::MigrationIntent(wrong);
            let bytes = record.encode().unwrap();
            let wrong_path = f.native.evidence_path(&record.key());
            std::fs::remove_file(&intent_path).unwrap();
            std::fs::write(&wrong_path, &bytes).unwrap();
            let wrong_completion = Record::MigrationCompletion(MigrationCompletion {
                binding: match &record {
                    Record::MigrationIntent(i) => i.binding.clone(),
                    _ => unreachable!(),
                },
                intent_sha256: digest(&bytes),
                ..completion.clone()
            });
            let completion_path = f.native.evidence_path(&wrong_completion.key());
            std::fs::create_dir_all(completion_path.parent().unwrap()).unwrap();
            std::fs::write(&completion_path, wrong_completion.encode().unwrap()).unwrap();
            assert!(
                capture(&f.native, &f.input.owner)
                    .unwrap()
                    .resolve(
                        &histories,
                        &[store.observe_history(&intent.migration_id).unwrap()]
                    )
                    .is_err(),
                "{field}"
            );
            std::fs::remove_file(&completion_path).unwrap();
            std::fs::remove_file(&wrong_path).unwrap();
            std::fs::write(&intent_path, &original_intent).unwrap();
        }
        let mut wrong = completion.clone();
        wrong.after_sha256 = digest(b"wrong");
        assert!(append(
            &mut f.native,
            &f.input.owner,
            &Record::MigrationCompletion(wrong),
            &histories,
            &[store.observe_history(&intent.migration_id).unwrap()],
            &mut || Ok(())
        )
        .is_err());
        append(
            &mut f.native,
            &f.input.owner,
            &Record::MigrationCompletion(completion),
            &histories,
            &[store.observe_history(&intent.migration_id).unwrap()],
            &mut || Ok(()),
        )
        .unwrap();
        let c = capture(&f.native, &f.input.owner).unwrap();
        let resolution = c
            .resolve(
                &histories,
                &[store.observe_history(&intent.migration_id).unwrap()],
            )
            .unwrap();
        for a in ARTIFACTS {
            let bytes = std::fs::read(f.path(a)).unwrap();
            assert!(matches!(
                resolution.ownership(&f.input.owner, a, &f.path(a), Some(&bytes)),
                FileOwnership::Owned { .. }
            ));
        }
        assert_eq!(
            resolution.ownership(
                &f.input.owner,
                Artifact::Installation,
                &f.path(Artifact::Installation),
                Some(&before)
            ),
            FileOwnership::Unknown
        );
    }
}

pub struct MigrationCase {
    pub fixture: Fixture,
    pub store: migration::Store,
    pub producer: MigrationNative,
    pub intent: MigrationIntent,
    pub intent_hash: String,
    pub preview: migration::Preview,
    pub after: Vec<u8>,
}
impl MigrationCase {
    pub fn new() -> Self {
        let mut case = Self::unadmitted();
        assert_eq!(
            case.fixture
                .native
                .append(&Record::MigrationIntent(case.intent.clone()))
                .unwrap(),
            case.intent_hash
        );
        case
    }
    pub fn unadmitted() -> Self {
        let mut fixture = Fixture::new();
        let plan = fixture.plan();
        fixture.admit(&plan);
        fixture.publish(&plan);
        let resolution = fixture.native.resolution().unwrap();
        let store = migration::Store::new(
            &fixture.path(Artifact::Installation),
            &fixture.root.join("Startup"),
        )
        .unwrap();
        let mut producer = MigrationNative {
            native: fixture.native.clone(),
        };
        let preview = store.preview(&mut producer, Backend::Rust).unwrap();
        let intent = MigrationIntent {
            binding: fixture
                .native
                .receipt
                .binding(parse_owner_reference(&fixture.native.owner).unwrap(), 7)
                .unwrap(),
            predecessor_sha256: resolution.head().into(),
            predecessor_terminal: resolution.terminal().unwrap().clone(),
            migration_id: "9".repeat(32),
            startup_path: fixture.root.join("Startup/Open BAT.lnk"),
            review_fingerprint: preview.fingerprint().into(),
            before_sha256: digest(&std::fs::read(fixture.path(Artifact::Installation)).unwrap()),
            restores: None,
            acceptance_reference: "native-migration-review".into(),
        };
        let intent_hash = digest(&Record::MigrationIntent(intent.clone()).encode().unwrap());
        let after = serde_json::to_vec(
            &serde_json::json!({"kit_root": fixture.input.root, "backend": Backend::Powershell}),
        )
        .unwrap();
        Self {
            fixture,
            store,
            producer,
            intent,
            intent_hash,
            preview,
            after,
        }
    }
    pub fn begin(&mut self) {
        self.store
            .begin(
                &mut self.producer,
                &self.intent.migration_id,
                &self.preview,
                Backend::Powershell,
                &self.after,
                false,
            )
            .unwrap();
    }
    pub fn complete(&mut self) -> MigrationCompletion {
        self.begin();
        assert_eq!(
            self.store
                .advance(&mut self.producer, &self.intent.migration_id),
            Ok(migration::Progress::WaitingLaunch)
        );
        assert_eq!(
            self.store
                .advance(&mut self.producer, &self.intent.migration_id),
            Ok(migration::Progress::Complete)
        );
        MigrationCompletion {
            binding: self.intent.binding.clone(),
            intent_sha256: self.intent_hash.clone(),
            saved_sha256: self
                .store
                .read_history(&self.intent.migration_id)
                .unwrap()
                .sha256()
                .into(),
            after_sha256: digest(&self.after),
        }
    }
    pub fn assert_only_installation_unknown(&mut self) {
        for a in ARTIFACTS {
            let ownership = self.fixture.ownership(a);
            if a == Artifact::Installation {
                assert_eq!(ownership, FileOwnership::Unknown);
            } else {
                assert!(matches!(ownership, FileOwnership::Owned { .. }), "{a:?}");
            }
        }
    }
}
