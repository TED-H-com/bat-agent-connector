use super::*;

#[cfg(unix)]
#[test]
fn reader_namespace_directory_gate_never_follows_linked_namespace() {
    let f = Fixture::new();
    let target = f.root.join("outside-namespace");
    crate::publication_state::create_new_directory(&target).unwrap();
    let name = format!("{}.json", "a".repeat(64));
    write_private(&target.join(&name), b"preserved");
    let namespace = f.reader.root().join(
        EvidenceKey::Lineage("0".repeat(64))
            .relative_path()
            .unwrap()
            .parent()
            .unwrap(),
    );
    std::os::unix::fs::symlink(&target, namespace).unwrap();
    assert!(f.reader.entries().is_err());
    assert_eq!(std::fs::read(target.join(name)).unwrap(), b"preserved");
}

#[test]
fn reader_namespace_entry_limit_includes_root_entries_after_children() {
    let f = Fixture::new();
    let root = f.reader.root();
    f.reader.directories.borrow_mut().clear();
    for entry in std::fs::read_dir(&root).unwrap() {
        let entry = entry.unwrap();
        if entry.file_type().unwrap().is_dir() {
            std::fs::remove_dir_all(entry.path()).unwrap();
        } else {
            std::fs::remove_file(entry.path()).unwrap();
        }
    }
    for key in [
        EvidenceKey::Provisioning,
        EvidenceKey::Managed,
        EvidenceKey::Lineage("0".repeat(64)),
        EvidenceKey::Closure("0".repeat(64)),
        EvidenceKey::Directory("0".repeat(64)),
        EvidenceKey::MigrationCompletion("0".repeat(64)),
    ] {
        let relative = key.relative_path().unwrap();
        if matches!(key, EvidenceKey::Provisioning | EvidenceKey::Managed) {
            std::fs::write(root.join(relative), b"").unwrap();
        } else {
            crate::publication_state::create_new_directory(&root.join(relative.parent().unwrap()))
                .unwrap();
        }
    }
    let order: Vec<_> = std::fs::read_dir(&root)
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    let index = order.iter().position(|p| p.is_dir()).unwrap();
    assert!(index + 1 < order.len());
    for n in 0..ENTRY_LIMIT - index - 1 {
        std::fs::write(order[index].join(format!("{n:064x}.json")), b"").unwrap();
    }
    let after: Vec<_> = std::fs::read_dir(&root)
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    assert_eq!(
        order, after,
        "required stable task-owned directory enumeration"
    );
    assert!(f.reader.entries().is_err());
}
