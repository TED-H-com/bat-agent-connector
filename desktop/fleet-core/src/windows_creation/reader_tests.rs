use super::*;
fn reseal(f: &mut Fixture) {
    let records: Vec<_> = f
        .reader
        .entries()
        .unwrap()
        .into_iter()
        .map(|k| {
            Record::decode(
                &k,
                &f.reader.read_private(&k, RECEIPT_LIMIT).unwrap().unwrap(),
            )
            .unwrap()
        })
        .collect();
    let bytes = Record::Provisioning(Box::new(f.reader.expected.clone()))
        .encode()
        .unwrap();
    write_private(
        &f.reader.key_path(&EvidenceKey::Provisioning).unwrap(),
        &bytes,
    );
    f.owner = f.reader.expected.owner(&bytes).unwrap();
    for record in records {
        if let Record::Directory(mut d) = record {
            let key = Record::Directory(d.clone()).key();
            if !f
                .reader
                .expected
                .directories
                .iter()
                .any(|r| r.path() == d.canonical_path)
            {
                std::fs::remove_file(f.reader.key_path(&key).unwrap()).unwrap();
            } else {
                d.binding = f.reader.expected.binding(&digest(&bytes), 7).unwrap();
                write_private(
                    &f.reader.key_path(&key).unwrap(),
                    &Record::Directory(d).encode().unwrap(),
                );
            }
        }
    }
}
#[test]
fn reader_scope_and_attestation_checks_are_independent() {
    for case in 0..6 {
        let mut f = Fixture::new();
        let path = f.reader.expected.scope.kit_root.join("client");
        let hash = digest(&f.reader.root_bytes().unwrap());
        let mut d = DirectoryCreated {
            binding: f.reader.expected.binding(&hash, 7).unwrap(),
            canonical_path: path.clone(),
            parent_identity: f.reader.identity(path.parent().unwrap()).unwrap(),
            identity: f.reader.identity(&path).unwrap(),
        };
        let key = Record::Directory(d.clone()).key();
        match case {
            0 => {
                f.reader.expected.scope.state_identity.file_id = "0".repeat(32);
                assert!(f.reader.base().is_err());
            }
            1 => {
                f.reader.expected.slots[0].parent_identity.file_id = "0".repeat(32);
                assert!(f.reader.base().is_err());
            }
            2 => {
                let root = f.reader.root();
                f.reader.expected.directories.retain(|d| d.path() != root);
                reseal(&mut f);
                assert!(f.reader.verify_context(&f.reader.expected).is_err());
            }
            3 => {
                let mut receipt = f.reader.expected.clone();
                receipt.creator.reservation_id = "d".repeat(32);
                assert!(f.reader.verify_context(&receipt).is_err());
                write_private(
                    &f.reader.key_path(&EvidenceKey::Provisioning).unwrap(),
                    &Record::Provisioning(Box::new(receipt)).encode().unwrap(),
                );
                assert!(f.reader.root_bytes().is_err());
            }
            _ => {
                if case == 4 {
                    d.parent_identity.file_id = "0".repeat(32);
                } else {
                    d.binding.owner_sid = "S-1-5-21-999".into();
                }
                write_private(
                    &f.reader.key_path(&key).unwrap(),
                    &Record::Directory(d).encode().unwrap(),
                );
                assert!(f.reader.directory(&path, &hash).is_err());
                assert!(f.reader.verify_context(&f.reader.expected).is_err());
            }
        }
    }
}
#[test]
fn reader_each_read_requires_launcher_and_exact_reserved_arguments() {
    let f = Fixture::new();
    let mut slot = f.reader.expected.slots[0].clone();
    slot.canonical_path = f.root.join("foreign-output");
    write_private(&slot.canonical_path, b"foreign");
    assert!(f.reader.observe(&slot, 32).is_err());
    let record = Record::Directory(DirectoryCreated {
        binding: f
            .reader
            .expected
            .binding(&digest(&f.reader.root_bytes().unwrap()), 7)
            .unwrap(),
        canonical_path: f.root.clone(),
        parent_identity: f.reader.identity(f.root.parent().unwrap()).unwrap(),
        identity: f.reader.identity(&f.root).unwrap(),
    });
    write_private(
        &f.reader.key_path(&record.key()).unwrap(),
        &record.encode().unwrap(),
    );
    assert!(f.reader.verify_record(&record).is_err());
    f.reader.security.held.set(false);
    assert_eq!(f.reader.login(), Err("LAUNCHER_REQUIRED"));
    assert_eq!(f.reader.entries(), Err("LAUNCHER_REQUIRED"));
    assert_eq!(
        f.reader.verify_context(&f.reader.expected),
        Err("LAUNCHER_REQUIRED")
    );
    assert_eq!(
        f.reader
            .verify_record(&Record::Provisioning(Box::new(f.reader.expected.clone()))),
        Err("LAUNCHER_REQUIRED")
    );
    assert_eq!(
        f.reader
            .read_private(&EvidenceKey::Provisioning, RECEIPT_LIMIT),
        Err("LAUNCHER_REQUIRED")
    );
    assert_eq!(
        f.reader.observe(&f.reader.expected.slots[0], 32),
        Err("LAUNCHER_REQUIRED")
    );
}
#[test]
fn reader_constructor_scope_and_read_only_effect_gates_are_explicit() {
    let mut f = Fixture::new();
    let expected = f.reader.expected.clone();
    f.reader.security.invalid_path.set(true);
    assert!(f.reader.walk(&f.root).is_err());
    f.reader.security.invalid_path.set(false);
    assert_eq!(
        evidence::reserve(&mut f.reader, &expected, &mut || panic!(
            "no write authorization"
        )),
        Err(UNSUPPORTED)
    );
    f.reader.security.login.borrow_mut().owner_sid = "S-1-5-21-999".into();
    assert!(Reader::new(expected, f.reader.security.clone()).is_err());
}
#[test]
fn reader_refuses_type_change_between_metadata_and_checked_open() {
    let f = Fixture::new();
    let path = f.root.join("racing-object");
    crate::publication_state::create_new_directory(&path).unwrap();
    let target = path.clone();
    let changed = Cell::new(false);
    *f.reader.security.on_open.borrow_mut() = Some(Box::new(move |p| {
        if p == target && !changed.replace(true) {
            std::fs::remove_dir(&target).unwrap();
            write_private(&target, b"changed type");
        }
    }));
    assert!(f.reader.walk(&path).is_err());
}
#[test]
fn reader_rejects_unknown_child_names_without_waiting_for_parse() {
    let f = Fixture::new();
    let path = f.reader.root().join("lineage");
    crate::publication_state::create_new_directory(&path).unwrap();
    std::fs::write(path.join("invalid.json"), b"").unwrap();
    assert!(f.reader.entries().is_err());
    f.reader.directories.borrow_mut().clear();
    std::fs::remove_dir_all(f.reader.root()).unwrap();
    assert!(f.reader.entries().unwrap().is_empty());
}

#[cfg(unix)]
#[test]
fn inaccessible_metadata_is_never_absence() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fixture::new();
    let directory = f.root.join("inaccessible");
    std::fs::create_dir(&directory).unwrap();
    let child = directory.join("child");
    std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o000)).unwrap();
    let native = child.symlink_metadata();
    let adapter = present(&child);
    let storage = crate::publication_state::metadata(&child);
    std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o700)).unwrap();
    assert!(
        matches!(native,Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied),
        "required inaccessible metadata fixture"
    );
    assert!(adapter.is_err());
    assert!(storage.is_err());
}
#[test]
fn reader_private_policy_and_absent_child_acl_checks_are_explicit() {
    let mut f = Fixture::new();
    f.reader.observe(&f.reader.expected.slots[0], 32).unwrap();
    assert!(f
        .reader
        .security
        .policies
        .borrow()
        .iter()
        .any(|(p, private, absent)| *p
            == f.reader
                .expected
                .path(Artifact::Inventory)
                .parent()
                .unwrap()
            && *private
            && *absent));
    let original = f.reader.expected.directories.clone();
    for path in [
        f.reader.expected.scope.kit_root.join("child"),
        f.reader.root().join("child"),
        f.reader.expected.scope.pointer.clone(),
        f.reader.expected.scope.startup_role_record.clone(),
        f.reader
            .expected
            .scope
            .state_root
            .join("fleet-publications/child"),
    ] {
        f.reader.expected.directories = if path.starts_with(f.reader.root()) {
            vec![original[0].clone()]
        } else {
            vec![original[3].clone(), original[4].clone()]
        };
        assert!(f.reader.private(&path).unwrap());
    }
    assert!(!f.reader.private(&f.root).unwrap());
}
#[test]
fn walk_checks_links_parent_type_path_syntax_and_final_presence() {
    use crate::publication_state::{walk_checked, WalkObject};
    let f = Fixture::new();
    let file = f.root.join("regular");
    write_private(&file, b"x");
    let leaf = f.reader.expected.scope.kit_root.join("client");
    for case in 0..3 {
        assert!(
            walk_checked(&leaf, &mut Default::default(), UNPROVEN, |p, _| {
                if case == 2 && p == leaf {
                    return Ok(None);
                }
                let mut object = WalkObject::new(
                    if case == 1 && p == f.reader.expected.scope.kit_root {
                        file.symlink_metadata().unwrap()
                    } else {
                        p.symlink_metadata().unwrap()
                    },
                    (),
                    (),
                );
                if case == 0 && p == leaf {
                    object.linked = true;
                }
                Ok(Some(object))
            })
            .is_err()
        );
    }
    for path in [
        PathBuf::from("relative"),
        f.root.join("kit/./client"),
        f.root.join("kit/../kit"),
    ] {
        assert!(
            walk_checked(&path, &mut Default::default(), UNPROVEN, |p, _| Ok(
                crate::publication_state::metadata(p)?.map(|m| WalkObject::new(m, (), ()))
            ))
            .is_err()
        );
    }
}
#[test]
fn reader_refuses_a_missing_startup_role_parent() {
    let mut f = Fixture::new();
    f.reader.expected.scope.startup_role_record = f.root.ancestors().last().unwrap().into();
    assert!(f.reader.walk(&f.root).is_err());
}
#[test]
fn reader_refuses_unreserved_present_intermediate_directory() {
    let mut f = Fixture::new();
    let path = f
        .reader
        .expected
        .path(Artifact::Inventory)
        .parent()
        .unwrap()
        .to_path_buf();
    let records: Vec<_> = f
        .reader
        .entries()
        .unwrap()
        .into_iter()
        .map(|key| {
            Record::decode(
                &key,
                &f.reader.read_private(&key, RECEIPT_LIMIT).unwrap().unwrap(),
            )
            .unwrap()
        })
        .collect();
    f.reader.expected.directories.retain(|d| d.path() != path);
    let bytes = Record::Provisioning(Box::new(f.reader.expected.clone()))
        .encode()
        .unwrap();
    write_private(
        &f.reader.key_path(&EvidenceKey::Provisioning).unwrap(),
        &bytes,
    );
    for record in records {
        if let Record::Directory(mut d) = record {
            let old_key = Record::Directory(d.clone()).key();
            if d.canonical_path == path {
                std::fs::remove_file(f.reader.key_path(&old_key).unwrap()).unwrap();
            } else {
                d.binding = f.reader.expected.binding(&digest(&bytes), 7).unwrap();
                write_private(
                    &f.reader.key_path(&old_key).unwrap(),
                    &Record::Directory(d).encode().unwrap(),
                );
            }
        }
    }
    assert!(f.reader.verify_context(&f.reader.expected).is_err());
}
#[test]
fn reader_refuses_a_held_directory_replaced_between_reads() {
    let f = Fixture::new();
    let path = f.reader.expected.scope.kit_root.join("client");
    f.reader.identity(&path).unwrap();
    std::fs::rename(&path, f.root.join("previous-client")).unwrap();
    crate::publication_state::create_new_directory(&path).unwrap();
    assert!(f.reader.identity(&path).is_err());
}
#[test]
fn reader_refuses_over_bound_bytes_and_changed_file_identity() {
    let f = Fixture::new();
    let path = f.root.join("bounded-file");
    write_private(&path, b"1234");
    assert!(f.reader.read(&path, 3).is_err());
    let id = identity(&File::open(&path).unwrap()).unwrap();
    *f.reader.security.changed_identity.borrow_mut() = Some((id, 0));
    assert!(f.reader.read(&path, 4).is_err());
}
#[test]
fn reader_namespace_entry_limit_counts_every_child() {
    let f = Fixture::new();
    let root = f.reader.root();
    for entry in std::fs::read_dir(&root).unwrap() {
        let entry = entry.unwrap();
        if entry.file_type().unwrap().is_dir() {
            std::fs::remove_dir_all(entry.path()).unwrap();
        } else {
            std::fs::remove_file(entry.path()).unwrap();
        }
    }
    let namespace = root.join("lineage");
    crate::publication_state::create_new_directory(&namespace).unwrap();
    for i in 0..ENTRY_LIMIT {
        std::fs::write(namespace.join(format!("{i:064x}.json")), b"").unwrap();
    }
    assert!(f.reader.entries().is_err());
}
#[test]
fn reader_refuses_all_unattested_managed_and_migration_records() {
    let f = Fixture::new();
    let binding = f
        .reader
        .expected
        .binding(&digest(&f.reader.root_bytes().unwrap()), 7)
        .unwrap();
    let records = [
        Record::Managed(ManagedBinding {
            binding: binding.clone(),
            managed: ManagedIdentity {
                installation_id: "c".repeat(32),
                manifest_sha256: "d".repeat(64),
                actor: format!("desktop-{}", "c".repeat(32)),
                server_id: "server-a".into(),
                principal_id: "principal-a".into(),
            },
        }),
        Record::MigrationCompletion(MigrationCompletion {
            binding: binding.clone(),
            intent_sha256: "d".repeat(64),
            saved_sha256: "e".repeat(64),
            after_sha256: "f".repeat(64),
        }),
        Record::MigrationTermination(MigrationTermination {
            binding: binding.clone(),
            intent_sha256: "d".repeat(64),
            saved_sha256: None,
            outcome: MigrationOutcome::Refused,
            decision_reference: "decision-a".into(),
        }),
        Record::MigrationIntent(MigrationIntent {
            binding,
            predecessor_sha256: "d".repeat(64),
            predecessor_terminal: Terminal {
                journal_sha256: Some("e".repeat(64)),
                decision_sha256: None,
                closure_sha256: None,
                migration_sha256: None,
            },
            migration_id: "a".repeat(32),
            startup_path: f.root.join("Startup/Open BAT.lnk"),
            review_fingerprint: "e".repeat(64),
            before_sha256: "f".repeat(64),
            restores: None,
            acceptance_reference: "native-review-a".into(),
        }),
    ];
    for record in records {
        record.encode().unwrap();
        assert_eq!(f.reader.verify_record(&record), Err(UNSUPPORTED));
    }
}
#[test]
fn reader_proven_existing_checks_identity_and_creation_reference_separately() {
    let mut f = Fixture::new();
    let path = f.reader.root();
    let hash = digest(&f.reader.root_bytes().unwrap());
    let record = Record::Directory(DirectoryCreated {
        binding: f.reader.expected.binding(&hash, 7).unwrap(),
        canonical_path: path.clone(),
        parent_identity: f.reader.identity(path.parent().unwrap()).unwrap(),
        identity: f.reader.identity(&path).unwrap(),
    });
    let bytes = std::fs::read(f.reader.key_path(&record.key()).unwrap()).unwrap();
    let position = f
        .reader
        .expected
        .directories
        .iter()
        .position(|d| d.path() == path)
        .unwrap();
    for bad_identity in [true, false] {
        let mut id = f.reader.identity(&path).unwrap();
        if bad_identity {
            id.file_id = "0".repeat(32);
        }
        f.reader.expected.directories[position] = DirectoryReservation::ProvenExisting {
            canonical_path: path.clone(),
            identity: id,
            creation_reference: if bad_identity {
                digest(&bytes)
            } else {
                "wrong-reference".into()
            },
        };
        assert!(f.reader.directory(&path, &hash).is_err());
    }
}
#[test]
fn bounded_handle_read_validates_limits_type_and_overflow() {
    let f = Fixture::new();
    let path = f.root.join("bounded");
    write_private(&path, b"abcd");
    assert!(f.reader.read(&f.root.join("absent"), 0).is_err());
    assert!(crate::publication_state::read_private(&f.root.join("absent"), 0).is_err());
    for limit in [0, 32 * 1024 * 1024 + 1, 3] {
        assert!(
            crate::publication_state::read_bounded(&File::open(&path).unwrap(), limit).is_err()
        );
    }
    assert_eq!(
        crate::publication_state::read_bounded(&File::open(&path).unwrap(), 4).unwrap(),
        b"abcd"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        let fifo = f.root.join("fifo");
        let name = std::ffi::CString::new(fifo.to_str().unwrap()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
        let file = std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NONBLOCK)
            .open(fifo)
            .unwrap();
        assert!(crate::publication_state::read_bounded(&file, 4).is_err());
    }
}

#[path = "outer_limit_tests.rs"]
mod outer_limit_tests;
