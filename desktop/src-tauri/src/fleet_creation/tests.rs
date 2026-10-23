use super::*;
use bat_fleet_core::{
    connection_publish::{self as publication, Platform as _, Publisher},
    creation_evidence::{ObjectIdentity, PublicationLink},
    migration::Platform as _,
    process_adapter::LoginIdentity,
};
use std::{
    cell::{Cell, RefCell},
    fs::File,
    path::PathBuf,
    rc::Rc,
    sync::Arc,
};
mod generator {
    include!("../../../fleet-core/tests/support/connection_setup.rs");
}
#[derive(Clone)]
struct SecurityFixture {
    login: Rc<RefCell<LoginIdentity>>,
    launcher: Rc<Cell<bool>>,
    monitor: Rc<Cell<bool>>,
}
struct Guard(Rc<Cell<bool>>);
impl Drop for Guard {
    fn drop(&mut self) {
        self.0.set(false);
    }
}
impl Security for SecurityFixture {
    type LauncherGuard = Guard;
    type MonitorGuard = Guard;
    fn launcher_guard(&self, check: &dyn Fn() -> Result<()>) -> Result<Guard> {
        if self.launcher.replace(true) {
            return Err("LAUNCHER_BUSY");
        }
        let guard = Guard(self.launcher.clone());
        check()?;
        Ok(guard)
    }
    fn monitor_guard(&self, check: &dyn Fn() -> Result<()>) -> Result<Guard> {
        self.held()?;
        if self.monitor.replace(true) {
            return Err("MONITOR_ALREADY_RUNNING");
        }
        let guard = Guard(self.monitor.clone());
        check()?;
        Ok(guard)
    }
    fn unconfigured(&self, _: &Path, _: &Path) -> Result<()> {
        Ok(())
    }
    fn discover(&self) -> Result<Option<bat_fleet_core::discovery::MonitorIdentity>> {
        Ok(None)
    }
    fn unpublished(&self, _: &Path) -> Result<()> {
        Ok(())
    }
    fn validate_path(&self, _: &Path) -> Result<()> {
        Ok(())
    }
    fn login(&self) -> Result<LoginIdentity> {
        Ok(self.login.borrow().clone())
    }
    fn held(&self) -> Result<()> {
        if self.launcher.get() {
            Ok(())
        } else {
            Err("LAUNCHER_REQUIRED")
        }
    }
    fn effect_held(&self) -> Result<()> {
        self.held()?;
        if self.monitor.get() {
            Ok(())
        } else {
            Err("MONITOR_GUARD_REQUIRED")
        }
    }
    fn open(&self, path: &Path, _: bool, _: bool) -> Result<File> {
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
            options.custom_flags(0x0220_0000);
        }
        options.open(path).map_err(|_| "CREATION_EVIDENCE_UNPROVEN")
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let m = file.metadata().map_err(|_| "CREATION_EVIDENCE_UNPROVEN")?;
            Ok(ObjectIdentity {
                volume: m.dev(),
                file_id: format!("{:032x}", m.ino()),
            })
        }
        #[cfg(windows)]
        {
            bat_fleet_core::publication_state::file_identity(file)
        }
    }
}
struct Fixture {
    _root: tempfile::TempDir,
    data: PathBuf,
    state: PathBuf,
    security: SecurityFixture,
}
impl Fixture {
    fn new() -> Self {
        Self::with_role(startup_role::Role::Client)
    }
    fn with_role(role: startup_role::Role) -> Self {
        let root = tempfile::tempdir().unwrap();
        let root_path = bat_fleet_core::installation::canonical_local(root.path()).unwrap();
        let data = root_path.join("data");
        let state = root_path.join("config");
        for path in [&data, &state] {
            bat_fleet_core::publication_state::create_new_directory(path).unwrap();
        }
        startup_role::select(&data, &state, role, 0).unwrap();
        Self {
            _root: root,
            data,
            state,
            security: SecurityFixture {
                login: Rc::new(RefCell::new(LoginIdentity {
                    owner_sid: "S-1-5-21-123-456-789-1001".into(),
                    session_id: 7,
                })),
                launcher: Default::default(),
                monitor: Default::default(),
            },
        }
    }
    fn input(&self) -> NativeInput {
        NativeInput {
            pointer: self.state.join("fleet.json"),
            kit_root: self.data.join("fleet-kit"),
            role_record: startup_role::record_path(&self.data),
            startup: startup(&self.data).unwrap(),
            roaming: self.data.clone(),
            startup_directory: self.data.join("Startup"),
            user: self.data.join("user"),
        }
    }
    fn platform(&self, mint: &mut dyn FnMut() -> [String; 2]) -> CreationPlatform<SecurityFixture> {
        CreationPlatform::from_native_input(self.input(), self.security.clone(), &|| Ok(()), mint)
            .unwrap()
    }
}
#[cfg(not(windows))]
#[test]
fn unsupported_creation_never_discovers_or_touches_the_sentinel() {
    let root = tempfile::tempdir().unwrap();
    let sentinel = root.path().join("sentinel");
    std::fs::write(&sentinel, b"unchanged").unwrap();
    let calls = Cell::new(0);
    assert_eq!(
        supported(|| {
            calls.set(calls.get() + 1);
            std::fs::write(&sentinel, b"changed").unwrap();
            Ok(())
        }),
        Err("LOCAL_PLATFORM_UNSUPPORTED")
    );
    assert_eq!(calls.get(), 0);
    assert_eq!(std::fs::read(&sentinel).unwrap(), b"unchanged");
    assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
}
#[test]
fn real_selected_startup_context_reserves_reopens_and_proves_original_publication() {
    let f = Fixture::new();
    let tickets = Arc::new(crate::fleet_lifecycle::Tickets::default());
    let ticket = tickets.capture();
    let role = startup_role::evidence(&f.data).unwrap();
    let original_role = std::fs::read(startup_role::record_path(&f.data)).unwrap();
    let minted = Cell::new(0);
    let mut p = f.platform(&mut || {
        minted.set(minted.get() + 1);
        ["a".repeat(32), "b".repeat(32)]
    });
    let prepared = prepare(&mut p, &ticket).unwrap();
    assert_eq!(
        p.receipt().startup.installation_ref,
        role.snapshot.installation_ref.unwrap()
    );
    assert_eq!(p.receipt().startup.installation_ref.len(), 36);
    assert_eq!(minted.get(), 1);
    assert_ne!(
        prepared.owner.installation_id,
        p.receipt().startup.installation_ref.replace('-', "")
    );
    assert!(p.receipt().managed_connector.is_none());
    for slot in &p.receipt().slots {
        assert!(!slot.canonical_path.exists());
    }
    drop(p);
    // A new login session does not change the historical reservation's identity.
    f.security.login.borrow_mut().session_id += 1;
    let mut p = f.platform(&mut || panic!("existing receipt must not mint"));
    let reopened = prepare(&mut p, &ticket).unwrap();
    assert_eq!(reopened.owner, prepared.owner);
    assert_eq!(
        std::fs::read(startup_role::record_path(&f.data)).unwrap(),
        original_role
    );
    let mut input = generator::setup();
    input.root = p.receipt().scope.kit_root.clone();
    input.owner = prepared.owner.clone();
    let plan = bat_fleet_core::connection_setup::build(&input).unwrap();
    let publisher = Publisher::new(
        &f.state.join("fleet.json"),
        &f.data.join("Startup"),
        &f.data.join("user"),
    )
    .unwrap();
    let frozen = publisher.export_plan(&plan, &mut p).unwrap();
    p.with_writer(|w| {
        let bytes = w
            .read_private(&EvidenceKey::Provisioning, RECEIPT_LIMIT)?
            .unwrap();
        let hash = bat_fleet_core::digest(&bytes);
        let link = Record::Publication(PublicationLink {
            binding: w
                .receipt()
                .binding(&hash, f.security.login.borrow().session_id)?,
            predecessor_sha256: hash,
            predecessor_terminal: None,
            frozen,
            generator_version: plan.generator_version,
            baseline: reopened.baseline,
            acceptance_reference: "native-review-fixture".into(),
        });
        w.append(&prepared.owner, &link, &mut || ticket.verify())?;
        Ok(())
    })
    .unwrap();
    assert_eq!(
        publisher.publish_authorized(
            &plan,
            &mut p,
            &mut |_, _| ticket.verify(),
            &mut |point, _| {
                if point == publication::Boundary::AfterIntended {
                    Err("INTERRUPTED")
                } else {
                    Ok(())
                }
            }
        ),
        Err(publication::Error::Gate("INTERRUPTED"))
    );
    let _launcher = p.launcher_guard().unwrap();
    let path = p
        .receipt()
        .path(bat_fleet_core::connection_setup::Artifact::Inventory);
    assert!(matches!(
        p.ownership(
            &prepared.owner,
            bat_fleet_core::connection_setup::Artifact::Inventory,
            path,
            None
        )
        .unwrap(),
        bat_fleet_core::connection_setup::FileOwnership::Owned { .. }
    ));
}
#[test]
fn changed_native_startup_uuid_generation_sid_and_stopped_ticket_refuse() {
    let f = Fixture::new();
    let tickets = Arc::new(crate::fleet_lifecycle::Tickets::default());
    let ticket = tickets.capture();
    let mut p = f.platform(&mut || ["a".repeat(32), "b".repeat(32)]);
    prepare(&mut p, &ticket).unwrap();
    drop(p);
    let path = startup_role::record_path(&f.data);
    let original = std::fs::read(&path).unwrap();
    for key in ["installation_ref", "generation"] {
        let mut changed: serde_json::Value = serde_json::from_slice(&original).unwrap();
        changed[key] = if key == "generation" {
            2.into()
        } else {
            uuid::Uuid::new_v4().to_string().into()
        };
        std::fs::write(&path, serde_json::to_vec(&changed).unwrap()).unwrap();
        let result = startup(&f.data).and_then(|startup| {
            let mut input = f.input();
            input.startup = startup;
            CreationPlatform::from_native_input(input, f.security.clone(), &|| Ok(()), &mut || {
                panic!("changed startup never mints")
            })
        });
        assert!(result.is_err());
        std::fs::write(&path, &original).unwrap();
    }
    f.security.login.borrow_mut().owner_sid = "S-1-5-21-999".into();
    assert!(CreationPlatform::from_native_input(
        f.input(),
        f.security.clone(),
        &|| Ok(()),
        &mut || panic!("changed SID never mints")
    )
    .is_err());
    f.security.login.borrow_mut().owner_sid = "S-1-5-21-123-456-789-1001".into();
    let mut p = f.platform(&mut || panic!("existing receipt never mints"));
    tickets.set_stopping(true);
    assert!(matches!(
        prepare(&mut p, &ticket),
        Err("FLEET_STOP_REQUESTED")
    ));
}

#[cfg(windows)]
#[path = "role_windows_tests.rs"]
mod windows;

#[test]
fn managed_or_providing_context_cannot_fall_back_to_an_unbound_receipt() {
    for role in [startup_role::Role::Provider, startup_role::Role::Both] {
        let f = Fixture::with_role(role);
        assert!(matches!(
            startup(&f.data),
            Err("CREATION_OPERATION_UNSUPPORTED")
        ));
    }
    let f = Fixture::new();
    std::fs::create_dir(f.data.join("managed-central")).unwrap();
    assert!(matches!(
        startup(&f.data),
        Err("CREATION_OPERATION_UNSUPPORTED")
    ));
}
