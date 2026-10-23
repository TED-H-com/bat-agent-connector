use super::*;
use crate::connection_setup::PublicationPlan;
#[test]
fn platform_guard_and_final_observation_recheck_login() {
    let f = Fixture::new();
    let mut p = f.platform();
    p.reader.expected.scope.state_identity.file_id = "0".repeat(32);
    p.reader.security.trace.borrow_mut().clear();
    assert!(p.observation().is_err());
    assert!(!p.reader.security.trace.borrow().contains(&"unconfigured"));
    p.reader.expected.scope.state_identity = f.reader.expected.scope.state_identity.clone();
    p.reader.security.held.set(false);
    p.reader.security.login.borrow_mut().session_id += 1;
    assert!(matches!(p.launcher_guard(), Err("OTHER_LOGIN_OWNER")));
    p.reader.security.login.borrow_mut().session_id -= 1;
    p.reader.security.held.set(true);
    write_private(&p.reader.expected.scope.pointer, b"raw pointer");
    p.reader.security.login_after_discover.set(true);
    assert!(migration::Platform::discover(&mut p).is_err());
}
#[test]
fn platform_ownership_rechecks_login_after_history_reads() {
    let f = Fixture::new();
    let p = f.platform();
    let directory = p.reader.root().join("directories");
    let count = Cell::new(0);
    let login = p.reader.security.login.clone();
    *p.reader.security.on_validate.borrow_mut() = Some(Box::new(move |path| {
        // Each capture enumerates twice; each history read enumerates once.
        if path == directory {
            count.set(count.get() + 1);
            if count.get() == 6 {
                login.borrow_mut().session_id += 1;
            }
        }
    }));
    assert!(p
        .ownership(
            &f.owner,
            Artifact::Inventory,
            p.reader.expected.path(Artifact::Inventory),
            None
        )
        .is_err());
}
#[test]
fn platform_history_limit_is_applied_before_resolver_replay() {
    let f = Fixture::new();
    let (p, _, journal, _) = admitted(&f);
    std::fs::remove_file(journal).unwrap();
    let mut link = p
        .reader
        .entries()
        .unwrap()
        .into_iter()
        .find_map(|k| {
            match Record::decode(
                &k,
                &p.reader.read_private(&k, RECEIPT_LIMIT).unwrap().unwrap(),
            )
            .unwrap()
            {
                Record::Publication(link) => Some(link),
                _ => None,
            }
        })
        .unwrap();
    for n in 0..HISTORY_LIMIT {
        link.predecessor_sha256 = format!("{n:064x}");
        let record = Record::Publication(link.clone());
        write_private(
            &p.reader.key_path(&record.key()).unwrap(),
            &record.encode().unwrap(),
        );
    }
    assert!(p.histories().is_err());
}

// Only this fixture writer permits artifact directories. History, receipt and
// frozen-plan bytes are emitted by the real producers, then read by the core.
struct Writer<'a>(&'a mut CreationPlatform<SecurityFixture>);
impl migration::Platform for Writer<'_> {
    type LauncherGuard = Guard;
    type MonitorGuard = Guard;
    fn launcher_guard(&mut self) -> Result<Guard> {
        self.0.launcher_guard()
    }
    fn monitor_guard(&mut self) -> Result<Guard> {
        self.0.monitor_guard()
    }
    fn login(&self) -> Result<LoginIdentity> {
        migration::Platform::login(self.0)
    }
    fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
        migration::Platform::discover(self.0)
    }
    fn request_quit(&mut self, _: &migration::Owner) -> Result<()> {
        Err(UNSUPPORTED)
    }
    fn launch(&mut self, _: Backend) -> Result<()> {
        Err(UNSUPPORTED)
    }
    fn validate_config(&self, _: &[u8], _: Backend) -> Result<()> {
        Err(UNSUPPORTED)
    }
    fn classify_shortcut(&self, _: &[u8]) -> Result<Backend> {
        Err(UNSUPPORTED)
    }
    fn shortcut(&self, _: Backend) -> Result<Vec<u8>> {
        Err(UNSUPPORTED)
    }
}
impl publication::Platform for Writer<'_> {
    fn ownership(
        &self,
        o: &OwnerEvidence,
        a: Artifact,
        p: &Path,
        b: Option<&[u8]>,
    ) -> Result<FileOwnership> {
        self.0.ownership(o, a, p, b)
    }
    fn verify_private_state(&self, p: &Path, o: &OwnerEvidence) -> Result<()> {
        self.0.verify_private_state(p, o)
    }
}
fn admitted(
    f: &Fixture,
) -> (
    CreationPlatform<SecurityFixture>,
    PublicationPlan,
    PathBuf,
    Vec<u8>,
) {
    let mut p = f.platform();
    let publisher = publication::Publisher::new(
        &p.reader.expected.scope.pointer,
        &f.root.join("Startup"),
        &f.root.join("user"),
    )
    .unwrap();
    let mut input = generator::setup();
    input.root = p.reader.expected.scope.kit_root.clone();
    input.owner = f.owner.clone();
    let plan = crate::connection_setup::build(&input).unwrap();
    p.reader.security.held.set(false);
    let frozen = publisher.export_plan(&plan, &mut Writer(&mut p)).unwrap();
    p.reader.security.held.set(true);
    let captured = evidence::capture(&p.reader, &f.owner).unwrap();
    let record = Record::Publication(PublicationLink {
        binding: p
            .reader
            .expected
            .binding(captured.receipt_sha256(), 7)
            .unwrap(),
        predecessor_sha256: captured.receipt_sha256().into(),
        predecessor_terminal: None,
        frozen,
        generator_version: plan.generator_version,
        baseline: captured.resolve(&[], &[]).unwrap().baseline().unwrap(),
        acceptance_reference: "native-review-fixture".into(),
    });
    crate::publication_state::create_new_directory(&p.reader.root().join("lineage")).unwrap();
    write_private(
        &p.reader.key_path(&record.key()).unwrap(),
        &record.encode().unwrap(),
    );
    p.reader.security.held.set(false);
    assert_eq!(
        publisher.publish_authorized(
            &plan,
            &mut Writer(&mut p),
            &mut |_, _| Ok(()),
            &mut |b, a| {
                if b == publication::Boundary::AfterIntended && a == Artifact::Inventory {
                    Err("INTERRUPTED")
                } else {
                    Ok(())
                }
            }
        ),
        Err(publication::Error::Gate("INTERRUPTED"))
    );
    p.reader.security.held.set(true);
    let journal = p
        .reader
        .expected
        .scope
        .state_root
        .join("fleet-publications")
        .join(format!("{}.json", plan.review_digest));
    let bytes = std::fs::read(&journal).unwrap();
    (p, plan, journal, bytes)
}
#[test]
fn platform_ownership_uses_real_history_and_resolver_and_requires_receipt() {
    let f = Fixture::new();
    let (p, plan, _, _) = admitted(&f);
    let path = p.reader.expected.path(Artifact::Inventory);
    let after = &plan.output(Artifact::Inventory).bytes;
    write_private(path, after);
    assert!(matches!(
        p.ownership(&f.owner, Artifact::Inventory, path, Some(after))
            .unwrap(),
        FileOwnership::Owned { .. }
    ));
    write_private(path, b"third");
    assert_eq!(
        p.ownership(&f.owner, Artifact::Inventory, path, Some(b"third"))
            .unwrap(),
        FileOwnership::Unknown
    );
    std::fs::remove_file(p.reader.key_path(&EvidenceKey::Provisioning).unwrap()).unwrap();
    assert_eq!(
        p.ownership(&f.owner, Artifact::Inventory, path, None)
            .unwrap(),
        FileOwnership::Unknown
    );
    p.reader.security.held.set(false);
    assert!(p
        .ownership(&f.owner, Artifact::Inventory, path, None)
        .is_err());
}
#[test]
fn platform_refuses_receipt_drift_between_captures() {
    let f = Fixture::new();
    let (p, plan, _, _) = admitted(&f);
    let receipt = p.reader.key_path(&EvidenceKey::Provisioning).unwrap();
    let mut changed = std::fs::read(&receipt).unwrap();
    changed.push(b' ');
    let frozen = p
        .reader
        .expected
        .scope
        .state_root
        .join("fleet-publication-plans")
        .join(format!("{}.json", plan.review_digest));
    *p.reader.security.on_open.borrow_mut() = Some(Box::new(move |path| {
        if path == frozen {
            std::fs::write(&receipt, &changed).unwrap();
        }
    }));
    assert!(p
        .ownership(
            &f.owner,
            Artifact::Inventory,
            p.reader.expected.path(Artifact::Inventory),
            None
        )
        .is_err());
}
#[test]
fn platform_refuses_history_signature_drift_between_reads() {
    let f = Fixture::new();
    let (p, plan, journal, bytes) = admitted(&f);
    std::fs::remove_file(&journal).unwrap();
    let frozen = p
        .reader
        .expected
        .scope
        .state_root
        .join("fleet-publication-plans")
        .join(format!("{}.json", plan.review_digest));
    let count = Cell::new(0);
    *p.reader.security.on_open.borrow_mut() = Some(Box::new(move |path| {
        if path == frozen {
            count.set(count.get() + 1);
            if count.get() == 4 {
                write_private(&journal, &bytes);
            }
        }
    }));
    assert!(p
        .ownership(
            &f.owner,
            Artifact::Inventory,
            p.reader.expected.path(Artifact::Inventory),
            None
        )
        .is_err());
}
#[test]
fn platform_private_state_checks_membership_scope_owner_and_directory_creation() {
    for case in 0..7 {
        let f = Fixture::new();
        let p = f.platform();
        let root = p.reader.root();
        let mut owner = f.owner.clone();
        p.verify_private_state(&root, &owner).unwrap();
        let path = match case {
            0 => root.join("unreserved/nested.json"),
            1 => p.reader.expected.scope.kit_root.clone(),
            6 => {
                let path = p
                    .reader
                    .expected
                    .scope
                    .state_root
                    .join("fleet-publications")
                    .join(format!("{}.json", "c".repeat(64)));
                write_private(&path, b"private leaf");
                *p.reader.security.denied.borrow_mut() = Some(path.clone());
                path
            }
            2 => {
                owner.installation_id = "c".repeat(32);
                root.clone()
            }
            _ => {
                let key = p.reader.entries().unwrap().into_iter().find(|key| {
                    matches!(Record::decode(key, &p.reader.read_private(key, RECORD_LIMIT).unwrap().unwrap()).unwrap(), Record::Directory(d) if d.canonical_path == root)
                }).unwrap();
                let file = p.reader.key_path(&key).unwrap();
                if case == 3 {
                    std::fs::remove_file(file).unwrap();
                } else {
                    let Record::Directory(mut d) =
                        Record::decode(&key, &std::fs::read(&file).unwrap()).unwrap()
                    else {
                        unreachable!()
                    };
                    d.identity.file_id = "0".repeat(32);
                    let bytes = Record::Directory(d).encode().unwrap();
                    if case == 4 {
                        write_private(&file, &bytes);
                    } else {
                        let receipt = p.reader.key_path(&EvidenceKey::Provisioning).unwrap();
                        let count = Cell::new(0);
                        *p.reader.security.on_open.borrow_mut() = Some(Box::new(move |path| {
                            // Context readback opens the receipt twice; corrupt
                            // the directory record only after that proof.
                            if path == receipt {
                                count.set(count.get() + 1);
                                if count.get() == 3 {
                                    write_private(&file, &bytes);
                                }
                            }
                        }));
                    }
                }
                root.clone()
            }
        };
        assert!(
            p.verify_private_state(&path, &owner).is_err(),
            "case {case}"
        );
    }
}
#[test]
fn platform_directory_hook_authorizes_once_and_never_creates() {
    let f = Fixture::new();
    let mut p = f.platform();
    let path = f.root.join("never-created/child");
    for allowed in [false, true] {
        let mut calls = 0;
        let result = p.prepare_directory(&path, &f.owner, &mut || {
            calls += 1;
            if allowed {
                Ok(())
            } else {
                Err("EXPIRED")
            }
        });
        assert_eq!(calls, 1);
        assert_eq!(
            result,
            Err(if allowed {
                publication::Error::Gate("CREATION_DIRECTORY_UNAVAILABLE")
            } else {
                publication::Error::Authorization { code: "EXPIRED" }
            })
        );
        assert!(!path.parent().unwrap().exists());
    }
    p.reader.security.login.borrow_mut().session_id += 1;
    let mut calls = 0;
    assert_eq!(
        p.prepare_directory(&path, &f.owner, &mut || {
            calls += 1;
            Ok(())
        }),
        Err(publication::Error::Gate("OTHER_LOGIN_OWNER"))
    );
    assert_eq!(calls, 1);
    assert!(!path.parent().unwrap().exists());
}
#[test]
fn platform_observation_guards_and_unsupported_effects_use_only_their_seams() {
    let f = Fixture::new();
    let mut p = f.platform();
    assert!(migration::Platform::discover(&mut p).unwrap().is_none());
    assert!(p.reader.security.trace.borrow().contains(&"unconfigured"));
    write_private(&p.reader.expected.scope.pointer, b"raw invalid pointer");
    p.reader.security.trace.borrow_mut().clear();
    assert!(migration::Platform::discover(&mut p).unwrap().is_none());
    assert!(p.reader.security.trace.borrow().contains(&"unpublished"));
    p.reader.security.held.set(false);
    assert!(p.monitor_guard().is_err());
    let launcher = p.launcher_guard().unwrap();
    p.reader.security.running.set(true);
    assert!(matches!(p.monitor_guard(), Err("MONITOR_ALREADY_RUNNING")));
    let monitor = p.reader.security.discover().unwrap().unwrap();
    let owner = migration::Owner::from_monitor(&monitor, &p.reader.login).unwrap();
    p.reader.security.trace.borrow_mut().clear();
    assert_eq!(p.request_quit(&owner), Err(UNSUPPORTED));
    assert_eq!(p.launch(Backend::Rust), Err(UNSUPPORTED));
    assert_eq!(p.validate_config(b"", Backend::Rust), Err(UNSUPPORTED));
    assert_eq!(p.classify_shortcut(b""), Err(UNSUPPORTED));
    assert_eq!(p.shortcut(Backend::Rust), Err(UNSUPPORTED));
    assert!(p.reader.security.trace.borrow().is_empty());
    p.reader.security.running.set(false);
    drop(launcher);
    assert_eq!(migration::Platform::login(&p), Err("LAUNCHER_REQUIRED"));
    assert!(p.observation().is_err());
    std::fs::remove_file(&p.reader.expected.scope.pointer).unwrap();
    assert!(p.observation().is_err());
    assert!(p.histories().is_err());
    assert!(p.verify_private_state(&p.reader.root(), &f.owner).is_err());
    assert!(p
        .ownership(
            &f.owner,
            Artifact::Inventory,
            p.reader.expected.path(Artifact::Inventory),
            None
        )
        .is_err());
}
#[test]
fn platform_verifies_context_before_discovering_present_pointer() {
    let f = Fixture::new();
    let mut p = f.platform();
    write_private(&p.reader.expected.scope.pointer, b"raw pointer");
    std::fs::remove_file(p.reader.key_path(&EvidenceKey::Provisioning).unwrap()).unwrap();
    p.reader.security.trace.borrow_mut().clear();
    assert!(migration::Platform::discover(&mut p).is_err());
    assert!(!p.reader.security.trace.borrow().contains(&"discover"));
}
