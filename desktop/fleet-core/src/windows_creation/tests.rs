use super::*;
use std::cell::Cell;
mod generator {
    use crate as bat_fleet_core;
    include!("../../tests/support/connection_setup.rs");
}
struct SecurityFixture {
    login: RefCell<LoginIdentity>,
    held: Cell<bool>,
    denied: RefCell<Option<PathBuf>>,
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
    fn validate_path(&self, _: &Path) -> Result<()> {
        Ok(())
    }
    fn login(&self) -> Result<LoginIdentity> {
        Ok(self.login.borrow().clone())
    }
    fn held(&self) -> Result<()> {
        if self.held.get() {
            Ok(())
        } else {
            Err("LAUNCHER_REQUIRED")
        }
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        identity(file)
    }
    fn open(&self, path: &Path, _: bool, _: bool) -> Result<File> {
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
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("wca-{:032x}", rand::random::<u128>()));
        for p in [
            "kit/client/bat-profiles",
            "state/fleet-creation/directories",
            "roles",
        ] {
            std::fs::create_dir_all(root.join(p)).unwrap();
        }
        std::fs::write(root.join("roles/role.json"), b"synthetic startup role").unwrap();
        let security = SecurityFixture {
            login: RefCell::new(LoginIdentity {
                owner_sid: "S-1-5-21-123-456-789-1001".into(),
                session_id: 7,
            }),
            held: Cell::new(true),
            denied: RefCell::default(),
        };
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
        std::fs::write(reader.key_path(&EvidenceKey::Provisioning).unwrap(), &bytes).unwrap();
        for d in &reader.expected.directories {
            let record = Record::Directory(DirectoryCreated {
                binding: reader.expected.binding(&digest(&bytes), 7).unwrap(),
                canonical_path: d.path().into(),
                parent_identity: reader.identity(d.path().parent().unwrap()).unwrap(),
                identity: reader.identity(d.path()).unwrap(),
            });
            std::fs::write(
                reader.key_path(&record.key()).unwrap(),
                record.encode().unwrap(),
            )
            .unwrap();
        }
        Self {
            root,
            reader,
            owner,
        }
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
