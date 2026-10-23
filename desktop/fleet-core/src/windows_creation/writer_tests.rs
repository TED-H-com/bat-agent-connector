use super::*;

#[path = "reservation_tests.rs"]
mod reservation;

fn fresh() -> Fixture {
    fresh_from(Fixture::new())
}
fn fresh_from(f: Fixture) -> Fixture {
    f.reader.directories.borrow_mut().clear();
    for path in [
        f.reader.expected.scope.kit_root.clone(),
        f.reader.root(),
        f.reader
            .expected
            .scope
            .state_root
            .join("fleet-publications"),
        f.reader
            .expected
            .scope
            .state_root
            .join("fleet-publication-plans"),
        f.reader
            .expected
            .scope
            .state_root
            .join("fleet-publication-decisions"),
    ] {
        std::fs::remove_dir_all(path).unwrap();
    }
    f.reader.security.held.set(false);
    f
}

fn seeded() -> Fixture {
    seeded_from(Fixture::new())
}
fn seeded_from(f: Fixture) -> Fixture {
    let f = fresh_from(f);
    let root = f.reader.root();
    crate::publication_state::create_new_directory(&root).unwrap();
    let directories = root.join(
        EvidenceKey::Directory("0".repeat(64))
            .relative_path()
            .unwrap()
            .parent()
            .unwrap(),
    );
    crate::publication_state::create_new_directory(&directories).unwrap();
    write_private(
        &f.reader.key_path(&EvidenceKey::Provisioning).unwrap(),
        &Record::Provisioning(Box::new(f.reader.expected.clone()))
            .encode()
            .unwrap(),
    );
    for path in [root, directories] {
        let record = Record::Directory(DirectoryCreated {
            binding: f
                .reader
                .expected
                .binding(
                    parse_owner_reference(&f.owner).unwrap(),
                    f.reader.expected.creator.session_id,
                )
                .unwrap(),
            parent_identity: f.reader.identity(path.parent().unwrap()).unwrap(),
            identity: f.reader.identity(&path).unwrap(),
            canonical_path: path,
        });
        write_private(
            &f.reader.key_path(&record.key()).unwrap(),
            &record.encode().unwrap(),
        );
    }
    f
}

#[cfg(windows)]
#[path = "writer_windows_tests.rs"]
mod windows;

#[test]
fn writer_reopens_recorded_reservation_without_creating_artifacts() {
    let f = seeded();
    let mut p = f.platform();
    let owner = p.with_writer(|w| w.reserve(&mut || Ok(()))).unwrap();
    assert_eq!(owner, f.owner);
    p.reader.security.held.set(true);
    evidence::capture(&p.reader, &owner)
        .unwrap()
        .resolve(&[], &[])
        .unwrap()
        .baseline()
        .unwrap();
    for slot in &p.reader.expected.slots {
        assert!(!slot.canonical_path.exists());
    }
}

#[test]
fn writer_effect_context_requires_both_guards_and_rechecks_monitor() {
    let f = fresh();
    let mut p = f.platform();
    p.with_writer(|w| {
        w.verify_effect_context()?;
        w.platform.reader.security.running.set(true);
        assert_eq!(w.verify_effect_context(), Err("MONITOR_ALREADY_RUNNING"));
        w.platform.reader.security.running.set(false);
        w.platform.reader.security.monitor.set(false);
        assert!(w.verify_effect_context().is_err());
        w.platform.reader.security.monitor.set(true);
        let running = w.platform.reader.security.running.clone();
        let trace = w.platform.reader.security.trace.clone();
        trace.borrow_mut().clear();
        *w.platform.reader.security.on_validate.borrow_mut() = Some(Box::new(move |_| {
            if trace.borrow().contains(&"unconfigured") {
                running.set(true);
            }
        }));
        assert!(w.verify_effect_context().is_err());
        Ok(())
    })
    .unwrap();
}

#[test]
fn writer_directory_level_records_before_descendant_and_checks_each_authorization() {
    let f = seeded();
    let mut p = f.platform();
    p.with_writer(|w| {
        let owner = w.reserve(&mut || Ok(()))?;
        let kit = w.platform.reader.expected.scope.kit_root.clone();
        let mut count = 0;
        w.directory_level(&kit, &mut || {
            count += 1;
            Ok(())
        })?;
        assert_eq!(count, 1);
        w.platform
            .reader
            .directory(&kit, parse_owner_reference(&owner)?)?;
        assert_eq!(
            w.directory_level(&kit.join("client"), &mut || {
                count += 1;
                Err("EXPIRED")
            }),
            Err("EXPIRED")
        );
        assert_eq!(count, 2);
        assert!(kit.is_dir());
        assert!(!kit.join("client").exists());
        Ok(())
    })
    .unwrap();
}

#[test]
fn writer_create_only_rechecks_fence_and_preserves_a_raced_final_name() {
    for race_name in [false, true] {
        let f = seeded();
        let mut p = f.platform();
        p.with_writer(|w| {
            let kit = w.platform.reader.expected.scope.kit_root.clone();
            w.directory_level(&kit, &mut || Ok(()))?;
            w.directory_level(&kit.join("client"), &mut || Ok(()))?;
            let root = w.platform.reader.root();
            let before = w.entries()?;
            let slot = w
                .platform
                .reader
                .expected
                .path(Artifact::Inventory)
                .to_path_buf();
            let target = kit.join("client/bat-profiles");
            let binding = w
                .platform
                .reader
                .expected
                .binding(&digest(&w.platform.reader.root_bytes()?), 7)?;
            let security = w.platform.reader.security.clone();
            let fired = Rc::new(Cell::new(false));
            let marker = fired.clone();
            // The shared primitive's final callback runs after a real staging
            // file exists. Race only then, rather than during admission.
            *w.platform.reader.security.on_validate.borrow_mut() = Some(Box::new(move |_| {
                if marker.get() {
                    return;
                }
                let directory = root.join("directories");
                let stage = std::fs::read_dir(&directory)
                    .unwrap()
                    .filter_map(|e| e.ok())
                    .find(|e| {
                        e.file_name()
                            .to_string_lossy()
                            .starts_with(".fleet-creation-")
                    });
                if stage.is_some() {
                    marker.set(true);
                    if race_name {
                        let identity = security
                            .identity(&security.open(&target, true, false).unwrap())
                            .unwrap();
                        let key = Record::Directory(DirectoryCreated {
                            binding: binding.clone(),
                            canonical_path: target.clone(),
                            parent_identity: security
                                .identity(
                                    &security
                                        .open(target.parent().unwrap(), true, false)
                                        .unwrap(),
                                )
                                .unwrap(),
                            identity,
                        })
                        .key();
                        std::fs::write(root.join(key.relative_path().unwrap()), b"racer").unwrap();
                    } else {
                        write_private(&slot, b"appeared after admission");
                    }
                }
            }));
            assert!(w
                .directory_level(&kit.join("client/bat-profiles"), &mut || Ok(()))
                .is_err());
            assert!(fired.get());
            w.platform.reader.security.on_validate.borrow_mut().take();
            if race_name {
                assert!(w.entries()?.len() > before.len());
                let bytes: Vec<_> = std::fs::read_dir(w.platform.reader.root().join("directories"))
                    .unwrap()
                    .map(|e| std::fs::read(e.unwrap().path()).unwrap())
                    .collect();
                assert!(bytes.iter().any(|b| b == b"racer"));
            }
            Ok(())
        })
        .unwrap();
    }
}

// The Store itself emits the migration journal. These observations only seed
// a pending state on task-owned paths; no migration executor is substituted.
struct MigrationProducer(LoginIdentity);
impl migration::Platform for MigrationProducer {
    type LauncherGuard = ();
    type MonitorGuard = ();
    fn launcher_guard(&mut self) -> Result<()> {
        Ok(())
    }
    fn monitor_guard(&mut self) -> Result<()> {
        Ok(())
    }
    fn login(&self) -> Result<LoginIdentity> {
        Ok(self.0.clone())
    }
    fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
        Ok(None)
    }
    fn request_quit(&mut self, _: &migration::Owner) -> Result<()> {
        panic!("no process effects")
    }
    fn launch(&mut self, _: Backend) -> Result<()> {
        panic!("no process effects")
    }
    fn validate_config(&self, _: &[u8], _: Backend) -> Result<()> {
        Ok(())
    }
    fn classify_shortcut(&self, _: &[u8]) -> Result<Backend> {
        Err(UNSUPPORTED)
    }
    fn shortcut(&self, _: Backend) -> Result<Vec<u8>> {
        Err(UNSUPPORTED)
    }
}

#[test]
fn writer_pending_migration_and_store_read_errors_are_not_absence() {
    for pending in [false, true] {
        let f = seeded();
        let mut p = f.platform();
        p.with_writer(|w| {
            w.verify_no_pending_migration()?;
            let reader = &w.platform.reader;
            if pending {
                let mut producer = MigrationProducer(reader.security.login()?);
                write_private(&reader.expected.scope.pointer, b"original fixture config");
                let preview = w
                    .platform
                    .migrations
                    .preview(&mut producer, Backend::Rust)?;
                w.platform.migrations.begin(
                    &mut producer,
                    &"9".repeat(32),
                    &preview,
                    Backend::Powershell,
                    b"next fixture config",
                    false,
                )?;
                assert_eq!(w.verify_no_pending_migration(), Err("MIGRATION_PENDING"));
            } else {
                let path = reader.expected.scope.state_root.join("fleet-migrations");
                crate::publication_state::create_new_directory(&path)?;
                write_private(&path.join("active.json"), b"invalid Store state");
                assert_eq!(w.verify_no_pending_migration(), Err("JSON_INVALID"));
            }
            Ok(())
        })
        .unwrap();
    }
}

#[test]
fn writer_strict_directory_creation_refuses_a_name_raced_after_ancestry_checks() {
    let f = seeded();
    let mut p = f.platform();
    p.with_writer(|w| {
        let target = w.platform.reader.expected.scope.kit_root.clone();
        let parent = target.parent().unwrap().to_path_buf();
        let state = w.platform.reader.expected.scope.state_root.clone();
        let previous = RefCell::new(PathBuf::new());
        let armed = Rc::new(Cell::new(false));
        let race = armed.clone();
        let fired = Rc::new(Cell::new(false));
        let marker = fired.clone();
        let existing = target.clone();
        *w.platform.reader.security.on_validate.borrow_mut() = Some(Box::new(move |path| {
            if race.get() && !marker.get() && path == parent && *previous.borrow() == state {
                marker.set(true);
                crate::publication_state::create_new_directory(&existing).unwrap();
            }
            *previous.borrow_mut() = path.into();
        }));
        let before = w.entries()?;
        assert!(w
            .directory_level(&target, &mut || {
                armed.set(true);
                Ok(())
            })
            .is_err());
        assert!(fired.get(), "mandatory race after context and before mkdir");
        assert!(target.is_dir());
        assert_eq!(w.entries()?, before);
        Ok(())
    })
    .unwrap();
}

#[test]
fn writer_refuses_unrecorded_existing_directories_and_crash_residue() {
    for after_reserve in [false, true] {
        let f = if after_reserve { seeded() } else { fresh() };
        let mut p = f.platform();
        let path = if after_reserve {
            p.reader.expected.scope.kit_root.clone()
        } else {
            p.reader.root()
        };
        crate::publication_state::create_new_directory(&path).unwrap();
        assert!(p.with_writer(|w| w.reserve(&mut || Ok(()))).is_err());
        assert!(path.is_dir());
    }
}

#[cfg(unix)]
#[test]
fn writer_refuses_linked_parent_without_following_target() {
    let f = seeded();
    let mut p = f.platform();
    p.with_writer(|w| {
        let kit = w.platform.reader.expected.scope.kit_root.clone();
        w.directory_level(&kit, &mut || Ok(()))?;
        let target = f.root.join("external-parent");
        crate::publication_state::create_new_directory(&target)?;
        write_private(&target.join("preserved"), b"preserved");
        std::os::unix::fs::symlink(&target, kit.join("client")).unwrap();
        assert!(w
            .directory_level(&kit.join("client/bat-profiles"), &mut || Ok(()))
            .is_err());
        assert_eq!(
            std::fs::read(target.join("preserved")).unwrap(),
            b"preserved"
        );
        assert!(!target.join("bat-profiles").exists());
        Ok(())
    })
    .unwrap();
}

#[test]
fn writer_reserves_real_evidence_without_creating_artifacts() {
    let f = fresh();
    let mut p = f.platform();
    let owner = p.with_writer(|w| w.reserve(&mut || Ok(()))).unwrap();
    assert_eq!(owner, f.owner);
    p.reader.security.held.set(true);
    evidence::capture(&p.reader, &owner)
        .unwrap()
        .resolve(&[], &[])
        .unwrap()
        .baseline()
        .unwrap();
    for slot in &p.reader.expected.slots {
        assert!(!slot.canonical_path.exists());
    }
}

#[test]
fn writer_records_a_directory_after_real_intended_publication_history() {
    let f = Fixture::new();
    let profiles = f
        .reader
        .expected
        .path(Artifact::ProfileIndex)
        .parent()
        .unwrap()
        .to_path_buf();
    let key = f.reader.entries().unwrap().into_iter().find(|key| {
        matches!(Record::decode(key, &f.reader.read_private(key, RECEIPT_LIMIT).unwrap().unwrap()).unwrap(),
            Record::Directory(d) if d.canonical_path == profiles)
    }).unwrap();
    f.reader.directories.borrow_mut().clear();
    std::fs::remove_dir(&profiles).unwrap();
    std::fs::remove_file(f.reader.key_path(&key).unwrap()).unwrap();
    let (mut p, _, _, _) = super::platform_tests::admitted(&f);
    // Seal the fixture producer's fixed namespace directories with real IDs.
    for path in p.reader.namespace_directories().unwrap() {
        if path.exists() {
            let record = Record::Directory(DirectoryCreated {
                binding: p
                    .reader
                    .expected
                    .binding(parse_owner_reference(&f.owner).unwrap(), 7)
                    .unwrap(),
                parent_identity: p.reader.identity(path.parent().unwrap()).unwrap(),
                identity: p.reader.identity(&path).unwrap(),
                canonical_path: path,
            });
            write_private(
                &p.reader.key_path(&record.key()).unwrap(),
                &record.encode().unwrap(),
            );
        }
    }
    p.reader.security.held.set(false);
    p.with_writer(|w| {
        w.directory_level(&profiles, &mut || Ok(()))?;
        let record = w
            .entries()?
            .into_iter()
            .find_map(|key| {
                let record =
                    Record::decode(&key, &w.read_private(&key, RECEIPT_LIMIT).unwrap().unwrap())
                        .unwrap();
                matches!(&record, Record::Directory(d) if d.canonical_path == profiles)
                    .then_some(record)
            })
            .unwrap();
        assert_eq!(
            w.append(&f.owner, &record, &mut || Err("READBACK_ONLY"))?,
            digest(&record.encode()?)
        );
        Ok(())
    })
    .unwrap();
    p.reader.security.held.set(true);
    assert!(matches!(
        p.ownership(
            &f.owner,
            Artifact::Inventory,
            p.reader.expected.path(Artifact::Inventory),
            None
        )
        .unwrap(),
        FileOwnership::Owned { .. }
    ));
}
