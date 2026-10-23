use super::platform::CreationPlatform;
use super::*;
use crate::{
    connection_publish::{self as publication, Platform as _},
    discovery::{Backend, MonitorIdentity},
    migration::{self, Platform as _},
};
use std::{cell::Cell, io::Write, rc::Rc};
type OpenHook = Rc<RefCell<Option<Box<dyn FnMut(&Path)>>>>;
mod generator {
    use crate as bat_fleet_core;
    include!("../../tests/support/connection_setup.rs");
}
#[derive(Clone)]
struct SecurityFixture {
    login: Rc<RefCell<LoginIdentity>>,
    held: Rc<Cell<bool>>,
    denied: Rc<RefCell<Option<PathBuf>>>,
    monitor: Rc<Cell<bool>>,
    running: Rc<Cell<bool>>,
    trace: Rc<RefCell<Vec<&'static str>>>,
    on_open: OpenHook,
    on_validate: OpenHook,
    invalid_path: Rc<Cell<bool>>,
    login_after_discover: Rc<Cell<bool>>,
    changed_identity: Rc<RefCell<Option<(ObjectIdentity, usize)>>>,
    policies: Rc<RefCell<Vec<(PathBuf, bool, bool)>>>,
}
impl SecurityFixture {
    fn new(login: LoginIdentity) -> Self {
        Self {
            login: Rc::new(RefCell::new(login)),
            held: Rc::new(Cell::new(true)),
            denied: Default::default(),
            monitor: Default::default(),
            running: Default::default(),
            trace: Default::default(),
            on_open: Default::default(),
            on_validate: Default::default(),
            invalid_path: Default::default(),
            login_after_discover: Default::default(),
            changed_identity: Default::default(),
            policies: Default::default(),
        }
    }
    fn touch(&self, call: &'static str) {
        self.trace.borrow_mut().push(call);
    }
}
struct Guard(Rc<Cell<bool>>);
impl Drop for Guard {
    fn drop(&mut self) {
        self.0.set(false);
    }
}
fn identity(file: &File) -> Result<ObjectIdentity> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let m = file.metadata().map_err(|_| UNPROVEN)?;
        Ok(ObjectIdentity {
            volume: m.dev(),
            file_id: format!("{:032x}", m.ino()),
        })
    }
    #[cfg(windows)]
    {
        super::native::file_identity(file)
    }
}
impl Security for SecurityFixture {
    type LauncherGuard = Guard;
    type MonitorGuard = Guard;
    fn launcher_guard(&self, verify: &dyn Fn() -> Result<()>) -> Result<Guard> {
        self.touch("launcher");
        if self.held.get() {
            return Err("LAUNCHER_BUSY");
        }
        verify()?;
        self.held.set(true);
        let guard = Guard(self.held.clone());
        verify()?;
        Ok(guard)
    }
    fn monitor_guard(&self, verify: &dyn Fn() -> Result<()>) -> Result<Guard> {
        self.held()?;
        self.touch("monitor");
        if self.monitor.get() {
            return Err("MONITOR_ALREADY_RUNNING");
        }
        self.monitor.set(true);
        let guard = Guard(self.monitor.clone());
        verify()?;
        Ok(guard)
    }
    fn unconfigured(&self, _: &Path, _: &Path) -> Result<()> {
        self.touch("unconfigured");
        if self.running.get() {
            Err("MONITOR_ALREADY_RUNNING")
        } else {
            Ok(())
        }
    }
    fn discover(&self) -> Result<Option<MonitorIdentity>> {
        self.touch("discover");
        if self.login_after_discover.get() {
            self.login.borrow_mut().session_id += 1;
        }
        Ok(self.running.get().then(|| MonitorIdentity {
            process: crate::process_adapter::ProcessSnapshot {
                pid: 42,
                created_filetime: 123,
                executable: "C:\\fixture.exe".into(),
                arguments: vec![],
                login: self.login.borrow().clone(),
            },
            backend: Backend::Rust,
            instance: Some("a".repeat(32)),
            ownership: crate::discovery::Ownership::CurrentLogin,
            legacy: false,
            directories: vec![],
            record: None,
        }))
    }
    fn unpublished(&self, _: &Path) -> Result<()> {
        self.touch("unpublished");
        Ok(())
    }

    fn validate_path(&self, path: &Path) -> Result<()> {
        self.touch("path");
        if self.invalid_path.get() {
            return Err("PATH_UNPROVEN");
        }
        if let Some(hook) = self.on_validate.borrow_mut().as_mut() {
            hook(path);
        }
        Ok(())
    }
    fn login(&self) -> Result<LoginIdentity> {
        self.touch("login");
        Ok(self.login.borrow().clone())
    }
    fn held(&self) -> Result<()> {
        self.touch("held");
        if self.held.get() {
            Ok(())
        } else {
            Err("LAUNCHER_REQUIRED")
        }
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        self.touch("identity");
        let mut id = identity(file)?;
        if let Some((target, count)) = self.changed_identity.borrow_mut().as_mut() {
            if *target == id {
                *count += 1;
                if *count >= 3 {
                    id.file_id = "0".repeat(32);
                }
            }
        }
        Ok(id)
    }
    fn open(&self, path: &Path, private: bool, child_absent: bool) -> Result<File> {
        self.touch("open");
        self.policies
            .borrow_mut()
            .push((path.into(), private, child_absent));
        if let Some(hook) = self.on_open.borrow_mut().as_mut() {
            hook(path);
        }
        if self.denied.borrow().as_deref() == Some(path) {
            return Err("SECURITY_DENIED");
        }
        if path
            .symlink_metadata()
            .map_err(|_| UNPROVEN)?
            .file_type()
            .is_symlink()
        {
            return Err(UNPROVEN);
        }
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
            options.custom_flags(
                windows_sys::Win32::Storage::FileSystem::FILE_FLAG_BACKUP_SEMANTICS
                    | windows_sys::Win32::Storage::FileSystem::FILE_FLAG_OPEN_REPARSE_POINT,
            );
        }
        options.open(path).map_err(|_| UNPROVEN)
    }
}
struct Fixture {
    root: PathBuf,
    reader: Reader<SecurityFixture>,
    owner: OwnerEvidence,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.reader.directories.borrow_mut().clear();
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
impl Fixture {
    fn new() -> Self {
        Self::with_sid("S-1-5-21-123-456-789-1001".into())
    }
    fn with_sid(sid: String) -> Self {
        let root = crate::installation::canonical_local(&std::env::temp_dir())
            .unwrap()
            .join(format!("fleet-creation-{:032x}", rand::random::<u128>()));
        crate::publication_state::create_new_directory(&root).unwrap();
        for p in [
            "kit",
            "kit/client",
            "kit/client/bat-profiles",
            "state",
            "state/fleet-creation",
            "state/fleet-creation/directories",
            "state/fleet-publications",
            "state/fleet-publication-plans",
            "state/fleet-publication-decisions",
            "roles",
        ] {
            crate::publication_state::create_new_directory(&root.join(p)).unwrap();
        }
        write_private(&root.join("roles/role.json"), b"synthetic startup role");
        let security = SecurityFixture::new(LoginIdentity {
            owner_sid: sid,
            session_id: 7,
        });
        let object = |p: &Path| {
            security
                .identity(&security.open(p, false, false).unwrap())
                .unwrap()
        };
        let parent = object(&root);
        let scope = Scope {
            platform: "windows".into(),
            pointer: root.join("state/fleet.json"),
            kit_root: root.join("kit"),
            state_root: root.join("state"),
            startup_role_record: root.join("roles/role.json"),
            state_identity: object(&root.join("state")),
        };
        let paths = [
            scope.kit_root.join("client/fleet-inventory.json"),
            scope.kit_root.join("client/bat-profiles/index.json"),
            scope.kit_root.join("client/ssh-config"),
            scope.pointer.clone(),
        ];
        let receipt = ProvisioningReceipt {
            startup: StartupBinding {
                installation_ref: "d0f9b7aa-1651-4b7b-8f7c-4c76bfd504ac".into(),
                role_record_sha256: digest(b"synthetic startup role"),
                generation: 1,
                role: StartupRole::Client,
            },
            creator: Creator {
                owner_sid: security.login().unwrap().owner_sid,
                session_id: 7,
                reservation_id: "a".repeat(32),
            },
            fleet_creation_id: "b".repeat(32),
            directories: [
                scope.kit_root.clone(),
                scope.kit_root.join("client"),
                scope.kit_root.join("client/bat-profiles"),
                scope.state_root.join("fleet-creation"),
                scope.state_root.join("fleet-publications"),
                scope.state_root.join("fleet-publication-plans"),
                scope.state_root.join("fleet-publication-decisions"),
            ]
            .into_iter()
            .map(|canonical_path| DirectoryReservation::ReservedAbsent { canonical_path })
            .collect(),
            slots: std::array::from_fn(|i| SlotReservation {
                artifact: ARTIFACTS[i],
                canonical_path: paths[i].clone(),
                observed: "absent".into(),
                before_sha256: None,
                nearest_existing_parent: if i == 3 {
                    scope.state_root.clone()
                } else {
                    root.clone()
                },
                parent_identity: if i == 3 {
                    scope.state_identity.clone()
                } else {
                    parent.clone()
                },
                missing_components: paths[i]
                    .strip_prefix(if i == 3 { &scope.state_root } else { &root })
                    .unwrap()
                    .iter()
                    .map(|v| v.to_str().unwrap().into())
                    .collect(),
            }),
            scope,
            managed_connector: None,
        };
        let bytes = Record::Provisioning(Box::new(receipt.clone()))
            .encode()
            .unwrap();
        let owner = receipt.owner(&bytes).unwrap();
        let reader = Reader::new(receipt, security).unwrap();
        write_private(
            &reader.key_path(&EvidenceKey::Provisioning).unwrap(),
            &bytes,
        );
        for d in &reader.expected.directories {
            let record = Record::Directory(DirectoryCreated {
                binding: reader.expected.binding(&digest(&bytes), 7).unwrap(),
                canonical_path: d.path().into(),
                parent_identity: reader.identity(d.path().parent().unwrap()).unwrap(),
                identity: reader.identity(d.path()).unwrap(),
            });
            write_private(
                &reader.key_path(&record.key()).unwrap(),
                &record.encode().unwrap(),
            );
        }
        Self {
            root,
            reader,
            owner,
        }
    }
    fn platform(&self) -> CreationPlatform<SecurityFixture> {
        CreationPlatform::with_security(
            self.reader.expected.clone(),
            self.root.clone(),
            &self.root.join("Startup"),
            &self.root.join("user"),
            self.reader.security.clone(),
        )
        .unwrap()
    }
    fn resolve(&self) -> Result<Resolution> {
        evidence::capture(&self.reader, &self.owner)?.resolve(&[], &[])
    }
}
#[test]
fn reader_uses_real_receipt_resolver_and_generator_without_adopting_output() {
    let f = Fixture::new();
    let resolution = f.resolve().unwrap();
    resolution.baseline().unwrap();
    let mut input = generator::setup();
    input.root = f.reader.expected.scope.kit_root.clone();
    input.owner = f.owner.clone();
    let plan = crate::connection_setup::build(&input).unwrap();
    let path = f.reader.expected.path(Artifact::Inventory);
    let bytes = &plan.output(Artifact::Inventory).bytes;
    std::fs::write(path, bytes).unwrap();
    assert_eq!(
        f.resolve()
            .unwrap()
            .ownership(&f.owner, Artifact::Inventory, path, Some(bytes)),
        FileOwnership::Unknown
    );
    assert_eq!(std::fs::read(path).unwrap(), *bytes);
}
#[test]
fn reader_keeps_unreadable_slots_unknown_for_raw_recovery() {
    let f = Fixture::new();
    let path = f.reader.expected.path(Artifact::Inventory);
    std::fs::write(path, b"unreadable third bytes").unwrap();
    *f.reader.security.denied.borrow_mut() = Some(path.into());
    assert_eq!(
        f.resolve()
            .unwrap()
            .ownership(&f.owner, Artifact::Inventory, path, None),
        FileOwnership::Unknown
    );
    assert_eq!(std::fs::read(path).unwrap(), b"unreadable third bytes");
}
#[test]
fn reader_requires_directory_creation_and_refuses_namespace_residue() {
    let f = Fixture::new();
    f.resolve().unwrap();
    std::fs::create_dir(f.reader.root().join("unexpected")).unwrap();
    assert!(f.resolve().is_err());
    std::fs::remove_dir(f.reader.root().join("unexpected")).unwrap();
    let key = f
        .reader
        .entries()
        .unwrap()
        .into_iter()
        .find(|k| matches!(k, EvidenceKey::Directory(_)))
        .unwrap();
    std::fs::remove_file(f.reader.key_path(&key).unwrap()).unwrap();
    assert!(f.resolve().is_err());
}
#[test]
fn reader_rechecks_login_guards_role_and_security_without_repair() {
    let f = Fixture::new();
    f.reader.security.held.set(false);
    assert!(f.resolve().is_err());
    f.reader.security.held.set(true);
    f.resolve().unwrap();
    f.reader.security.login.borrow_mut().session_id += 1;
    assert!(f.resolve().is_err());
    f.reader.security.login.borrow_mut().session_id -= 1;
    *f.reader.security.denied.borrow_mut() = Some(f.root.clone());
    assert!(f.resolve().is_err());
    *f.reader.security.denied.borrow_mut() = None;
    std::fs::write(&f.reader.expected.scope.startup_role_record, b"changed").unwrap();
    assert!(f.resolve().is_err());
    assert_eq!(
        std::fs::read(&f.reader.expected.scope.startup_role_record).unwrap(),
        b"changed"
    );
}
#[test]
fn reader_refuses_managed_receipts_without_native_manifest_attestation() {
    let f = Fixture::new();
    let mut receipt = f.reader.expected.clone();
    receipt.managed_connector = Some(ManagedIdentity {
        installation_id: "c".repeat(32),
        manifest_sha256: digest(b"unverified manifest"),
        actor: format!("desktop-{}", "c".repeat(32)),
        server_id: "server-a".into(),
        principal_id: "principal-a".into(),
    });
    let security = SecurityFixture::new(f.reader.login.clone());
    assert!(Reader::new(receipt, security).is_err());
}

#[test]
fn walk_refuses_present_component_below_missing_ancestor() {
    use crate::publication_state::{walk_checked, WalkObject};
    let f = Fixture::new();
    let leaf = f
        .reader
        .expected
        .path(Artifact::Inventory)
        .parent()
        .unwrap();
    let missing = f.reader.expected.scope.kit_root.clone();
    assert!(
        walk_checked(leaf, &mut Default::default(), UNPROVEN, |p, _| {
            if p == missing {
                return Ok(None);
            }
            Ok(Some(WalkObject::new(p.symlink_metadata().unwrap(), (), ())))
        })
        .is_err()
    );
}

fn write_private(path: &Path, bytes: &[u8]) {
    if present(path).unwrap() {
        std::fs::write(path, bytes).unwrap();
    } else {
        let mut file = crate::publication_state::create_new_file(path).unwrap();
        file.write_all(bytes).unwrap();
        file.sync_all().unwrap();
    }
}

#[path = "platform_tests.rs"]
mod platform_tests;

#[path = "reader_tests.rs"]
mod reader_tests;

#[cfg(windows)]
#[path = "windows_tests.rs"]
mod windows_tests;
