use bat_fleet_core::{
    digest,
    selection_io::{FileObservation, ReadFailure, Store},
};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};
mod support;
use support::Fixture;

type Tree = BTreeMap<PathBuf, (Option<Vec<u8>>, String)>;
fn tree(path: &Path) -> Tree {
    let mut result = Tree::new();
    let bytes = if path.is_dir() {
        for entry in fs::read_dir(path).unwrap() {
            result.extend(tree(&entry.unwrap().path()));
        }
        None
    } else {
        Some(fs::read(path).unwrap())
    };
    // Capture metadata after warming reads, including access times and Unix identity/mode.
    result.insert(
        path.into(),
        (bytes, format!("{:?}", fs::metadata(path).unwrap())),
    );
    result
}

#[test]
fn absence_never_becomes_an_empty_file_or_creates_a_directory_or_lock() {
    let f = Fixture::new();
    let config = f.load();
    let store = Store::new(f.0.clone());
    let before = tree(&f.0);
    let observed = store.observe(&config).unwrap();
    assert_eq!(observed.primary(), &FileObservation::Absent);
    assert_eq!(observed.legacy(), &FileObservation::Absent);
    assert_eq!(observed.configuration_binding(), config.binding());
    assert_eq!(observed.directory(), f.0.join("BetterAgentTerminal"));
    assert!(!observed.directory().exists());
    assert_eq!(observed.snapshot(&config).unwrap().revision, digest(b""));
    assert_eq!(tree(&f.0), before);
}

#[test]
fn present_bytes_bad_json_and_legacy_remain_distinct_without_writes() {
    let f = Fixture::new();
    let config = f.load();
    let store = Store::new(f.0.clone());
    let directory = f.0.join("BetterAgentTerminal");
    fs::create_dir(&directory).unwrap();
    let primary = directory.join("fleet-client.json");
    let legacy = directory.join("open-bat-selection.json");
    fs::write(&legacy, br#"{"profile-2":true}"#).unwrap();
    let before = tree(&f.0);
    let observed = store.observe(&config).unwrap();
    assert_eq!(observed.primary(), &FileObservation::Absent);
    assert!(matches!(observed.legacy(), FileObservation::Present { .. }));
    assert_eq!(
        observed.snapshot(&config).unwrap().preferences().profiles,
        ["profile-2"]
    );
    assert_eq!(tree(&f.0), before);
    let persisted = observed
        .snapshot(&config)
        .unwrap()
        .preferences()
        .persisted(&config.inventory)
        .unwrap();
    for bytes in [vec![], b"{broken".to_vec(), persisted] {
        fs::write(&primary, &bytes).unwrap();
        let before = tree(&f.0);
        let observed = store.observe(&config).unwrap();
        assert_eq!(
            observed.primary(),
            &FileObservation::Present {
                bytes: bytes.clone(),
                digest: digest(&bytes)
            }
        );
        assert!(matches!(observed.legacy(), FileObservation::Present { .. }));
        let snapshot = observed.snapshot(&config);
        assert_eq!(snapshot.is_ok(), !bytes.is_empty() && bytes != b"{broken");
        assert_eq!(tree(&f.0), before);
        if let Ok(snapshot) = snapshot {
            assert!(snapshot.same_snapshot(&store.read(&config).unwrap()));
        }
    }
}

#[test]
fn unreadable_and_oversized_are_typed_and_never_absent() {
    let f = Fixture::new();
    let config = f.load();
    let store = Store::new(f.0.clone());
    let directory = f.0.join("BetterAgentTerminal");
    fs::create_dir(&directory).unwrap();
    let path = directory.join("fleet-client.json");
    fs::create_dir(&path).unwrap();
    let before = tree(&f.0);
    let observed = store.observe(&config).unwrap();
    assert!(matches!(observed.primary(), FileObservation::Unreadable(_)));
    assert!(observed.snapshot(&config).is_err());
    assert_eq!(tree(&f.0), before);
    fs::remove_dir(&path).unwrap();
    fs::write(&path, vec![b' '; 262145]).unwrap();
    let before = tree(&f.0);
    assert_eq!(
        store.observe(&config).unwrap().primary(),
        &FileObservation::Unreadable(ReadFailure::Invalid)
    );
    assert_eq!(tree(&f.0), before);
}

#[test]
fn observations_retain_directory_and_original_configuration_binding() {
    let f = Fixture::new();
    let old = f.0.join("org.tonyq.better-agent-terminal");
    fs::create_dir(&old).unwrap();
    let config = f.load();
    let store = Store::new(f.0.clone());
    let observed = store.observe(&config).unwrap();
    assert_eq!(observed.directory(), old);
    fs::create_dir(f.0.join("BetterAgentTerminal")).unwrap();
    assert_ne!(
        observed.directory(),
        store.observe(&config).unwrap().directory()
    );
    fs::write(f.0.join("kit/ssh-config"), b"changed").unwrap();
    let before = tree(&f.0);
    assert!(matches!(
        store.observe(&config),
        Err("CONFIGURATION_CHANGED")
    ));
    assert!(matches!(
        observed.snapshot(&f.load()),
        Err("CONFIGURATION_CHANGED")
    ));
    assert_eq!(tree(&f.0), before);
}

#[cfg(unix)]
#[test]
fn denied_reads_and_dangling_links_are_unknown_not_absent() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let f = Fixture::new();
    let config = f.load();
    let store = Store::new(f.0.clone());
    let directory = f.0.join("BetterAgentTerminal");
    fs::create_dir(&directory).unwrap();
    let path = directory.join("fleet-client.json");
    fs::write(&path, b"{}").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o0)).unwrap();
    let metadata = format!("{:?}", fs::metadata(&path).unwrap());
    assert_eq!(
        store.observe(&config).unwrap().primary(),
        &FileObservation::Unreadable(ReadFailure::Unreadable)
    );
    assert_eq!(format!("{:?}", fs::metadata(&path).unwrap()), metadata);
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    fs::remove_file(&path).unwrap();
    symlink(directory.join("missing"), &path).unwrap();
    assert_eq!(
        store.observe(&config).unwrap().primary(),
        &FileObservation::Unreadable(ReadFailure::Unreadable)
    );
    assert_eq!(fs::read_link(&path).unwrap(), directory.join("missing"));
}
