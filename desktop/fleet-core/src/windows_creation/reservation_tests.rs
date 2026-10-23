use super::*;

pub(super) fn snapshot(path: &Path) -> BTreeMap<PathBuf, Option<Vec<u8>>> {
    let mut result = BTreeMap::new();
    if path.exists() {
        result.insert(path.into(), None);
        for entry in std::fs::read_dir(path).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                result.extend(snapshot(&path));
            } else {
                result.insert(path.clone(), Some(std::fs::read(path).unwrap()));
            }
        }
    }
    result
}

#[test]
fn reservation_crashes_preserve_residue_and_restart_cannot_finish_it() {
    // reserve admission, root, container, receipt, root record (two checks),
    // container record (two checks). Fail the next effect, after readback.
    for (fail_at, has_container, has_receipt, records) in [
        (3, false, false, 0),
        (4, true, false, 0),
        (5, true, true, 0),
        (7, true, true, 1),
    ] {
        let f = fresh();
        let mut p = f.platform();
        let mut calls = 0;
        assert_eq!(
            p.with_writer(|w| w.reserve(&mut || {
                calls += 1;
                if calls == fail_at {
                    Err("CRASH")
                } else {
                    Ok(())
                }
            })),
            Err("CRASH")
        );
        assert_eq!(calls, fail_at);
        let root = p.reader.root();
        let container = p
            .reader
            .key_path(&EvidenceKey::Directory("0".repeat(64)))
            .unwrap()
            .parent()
            .unwrap()
            .to_path_buf();
        assert!(root.is_dir());
        assert_eq!(container.is_dir(), has_container);
        assert_eq!(
            p.reader
                .key_path(&EvidenceKey::Provisioning)
                .unwrap()
                .is_file(),
            has_receipt
        );
        let actual = snapshot(&root);
        assert_eq!(
            actual.len(),
            1 + usize::from(has_container) + usize::from(has_receipt) + records
        );
        if records == 1 {
            let record = actual
                .values()
                .filter_map(|b| b.as_ref())
                .find_map(|bytes| {
                    let key = Record::Directory(DirectoryCreated {
                        binding: p
                            .reader
                            .expected
                            .binding(parse_owner_reference(&f.owner).unwrap(), 7)
                            .unwrap(),
                        canonical_path: root.clone(),
                        identity: p.reader.identity(&root).unwrap(),
                        parent_identity: p.reader.expected.scope.state_identity.clone(),
                    })
                    .key();
                    Record::decode(&key, bytes).ok()
                })
                .unwrap();
            assert!(matches!(record, Record::Directory(d) if d.canonical_path == root));
        }
        let root_identity = p.reader.identity(&root).unwrap();
        drop(p);
        // No in-process capability or remembered handles survive the restart.
        let mut restarted = f.platform();
        restarted.reader.security.held.set(true);
        let answer = restarted.ownership(
            &f.owner,
            Artifact::Inventory,
            restarted.reader.expected.path(Artifact::Inventory),
            None,
        );
        if has_receipt {
            assert_eq!(answer, Err(UNPROVEN));
        } else {
            assert_eq!(answer, Ok(FileOwnership::Unknown));
        }
        restarted.reader.security.held.set(false);
        assert_eq!(
            restarted.with_writer(|w| w.reserve(&mut || panic!("retry must not authorize"))),
            Err(UNPROVEN)
        );
        assert_eq!(snapshot(&root), actual);
        assert_eq!(restarted.reader.identity(&root).unwrap(), root_identity);
        assert!(!restarted.reader.expected.scope.kit_root.exists());
    }
}

#[test]
fn reservation_reads_container_before_receipt_and_records_before_other_descendants() {
    let f = fresh();
    let mut p = f.platform();
    let root = p.reader.root();
    let receipt = p.reader.key_path(&EvidenceKey::Provisioning).unwrap();
    let container = p
        .reader
        .key_path(&EvidenceKey::Directory("0".repeat(64)))
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    let observed = Rc::new(Cell::new(false));
    let seen = observed.clone();
    let directory = container.clone();
    let provision = receipt.clone();
    *p.reader.security.on_open.borrow_mut() = Some(Box::new(move |path| {
        if path == directory && !seen.replace(true) {
            assert!(
                !provision.exists(),
                "container readback must precede receipt publication"
            );
        }
    }));
    p.with_writer(|w| {
        w.reserve(&mut || Ok(()))?;
        assert!(observed.get(), "mandatory container security readback");
        let hash = parse_owner_reference(&f.owner)?;
        for path in [&root, &container] {
            w.platform.reader.directory(path, hash)?;
        }
        assert_eq!(snapshot(&root).len(), 5);
        let lineage = w
            .platform
            .reader
            .key_path(&EvidenceKey::Lineage("0".repeat(64)))?
            .parent()
            .ok_or(UNPROVEN)?
            .to_path_buf();
        w.directory_level(&lineage, &mut || Ok(()))?;
        w.platform.reader.directory(&lineage, hash)?;
        let kit = w.platform.reader.expected.scope.kit_root.clone();
        w.directory_level(&kit, &mut || Ok(()))?;
        w.platform.reader.directory(&kit, hash)?;
        Ok(())
    })
    .unwrap();
}

#[test]
fn reservation_failed_container_readback_leaves_no_receipt() {
    let f = fresh();
    let mut p = f.platform();
    let container = p
        .reader
        .key_path(&EvidenceKey::Directory("0".repeat(64)))
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    *p.reader.security.denied.borrow_mut() = Some(container.clone());
    assert_eq!(
        p.with_writer(|w| w.reserve(&mut || Ok(()))),
        Err("SECURITY_DENIED")
    );
    assert!(container.is_dir());
    assert!(!p
        .reader
        .key_path(&EvidenceKey::Provisioning)
        .unwrap()
        .exists());
}
