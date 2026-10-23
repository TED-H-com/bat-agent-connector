use super::*;
use crate::{
    configuration::Configuration,
    process_adapter::{HeldProcess, LoginIdentity},
    selection_io::Store,
    tunnel::{self, Context, Plan, Platform, SpawnFailure},
};
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    time::SystemTime,
};
mod support {
    use crate as bat_fleet_core;
    include!("../../tests/support/mod.rs");
}
struct Child(ProcessSnapshot);
impl HeldProcess for Child {
    fn snapshot(&self) -> Result<ProcessSnapshot> {
        Ok(self.0.clone())
    }
    fn terminate_and_wait(&mut self) -> Result<()> {
        panic!("reader must never stop")
    }
}
struct Os {
    parent: ProcessSnapshot,
    child: RefCell<Option<ProcessSnapshot>>,
    login: RefCell<LoginIdentity>,
    unknown: Cell<bool>,
    image: PathBuf,
    intent: PathBuf,
    captured: RefCell<Vec<u8>>,
}
impl Observation for Os {
    fn current_login(&self) -> Result<LoginIdentity> {
        Ok(self.login.borrow().clone())
    }
    fn state(&self, _: u32) -> ProcessState {
        panic!("child proof uses exact observation")
    }
    fn observe(&self, _: u32) -> Result<Option<ProcessSnapshot>> {
        if self.unknown.get() {
            Err("OWNER_UNPROVEN")
        } else {
            Ok(self.child.borrow().clone())
        }
    }
    fn legacy_candidates(&self) -> Result<Vec<ProcessSnapshot>> {
        panic!("not a Monitor reader")
    }
}
impl Platform for Os {
    type Child = Child;
    fn owner(&mut self, _: &str) -> Result<ProcessSnapshot> {
        Ok(self.parent.clone())
    }
    fn endpoint_free(&mut self, _: std::net::SocketAddrV4) -> Result<()> {
        Ok(())
    }
    fn executable(&self) -> &Path {
        &self.image
    }
    fn spawn(&mut self, plan: &Plan) -> std::result::Result<Child, SpawnFailure> {
        *self.captured.borrow_mut() = std::fs::read(&self.intent).unwrap();
        let child = ProcessSnapshot {
            pid: 222,
            created_filetime: 2000000,
            executable: self.image.to_str().unwrap().into(),
            arguments: plan.arguments().to_vec(),
            login: self.parent.login.clone(),
        };
        *self.child.borrow_mut() = Some(child.clone());
        Ok(Child(child))
    }
}
struct Fixture {
    disk: support::Fixture,
    os: Os,
    path: PathBuf,
    bytes: Vec<u8>,
    record: TunnelRecord,
}
impl Fixture {
    fn new() -> Self {
        let disk = support::Fixture::new();
        // Exercise Windows' verbatim temp-path form: embedded '/' is a literal
        // component there, unlike the native separators returned by read_dir.
        #[cfg(windows)]
        let disk = {
            let mut disk = disk;
            disk.0 = std::fs::canonicalize(&disk.0).unwrap();
            disk
        };
        let c: Configuration = disk.load();
        let store = Store::new(disk.0.clone());
        let selection = store.read(&c).unwrap();
        let host = &c.inventory.hosts()[0];
        let plan = Plan::new(
            &c,
            &selection,
            host["name"].as_str().unwrap(),
            host["routes"][0]["alias"].as_str().unwrap(),
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        .unwrap();
        let login = LoginIdentity {
            owner_sid: "S-1-5-21-123-456-789-1001".into(),
            session_id: 1,
        };
        let mut os = Os {
            parent: ProcessSnapshot {
                pid: 111,
                created_filetime: 1000000,
                executable: "C:\\Fixture\\dashboard.exe".into(),
                arguments: vec!["--fleet-supervisor".into()],
                login: login.clone(),
            },
            child: RefCell::default(),
            login: RefCell::new(login),
            unknown: Cell::new(false),
            image: disk.0.join("system").join("ssh.exe"),
            intent: crate::configuration::data_directory(&disk.0)
                .unwrap()
                .join("fleet-tunnel-intents")
                .join(format!("{}.json", plan.name())),
            captured: RefCell::default(),
        };
        let launched = tunnel::launch(
            &Context {
                configuration: &c,
                store: &store,
                selection: &selection,
                roaming: &disk.0,
            },
            &plan,
            &mut os,
        )
        .unwrap();
        assert_eq!(
            os.intent,
            launched
                .record_path
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .join("fleet-tunnel-intents")
                .join(launched.record_path.file_name().unwrap()),
            "fixture intent must use the producer's native path components"
        );
        Self {
            disk,
            os,
            path: launched.record_path,
            bytes: launched.record_bytes,
            record: launched.record,
        }
    }
    fn read(&self) -> TunnelObservation {
        self.read_at(&self.disk.0)
    }
    fn read_at(&self, roaming: &Path) -> TunnelObservation {
        let before = snapshot(&self.disk.0);
        let result = observe_tunnels(roaming, &self.os).unwrap();
        assert_eq!(snapshot(&self.disk.0), before, "reader changed disk state");
        result
    }
    fn marker(&self) -> PathBuf {
        self.path
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .join("fleet-tunnel-stop-intents")
            .join(self.path.file_name().unwrap())
    }
}
type Tree = BTreeMap<PathBuf, (u64, SystemTime, Vec<u8>)>;
fn snapshot(root: &Path) -> Tree {
    fn walk(path: &Path, out: &mut Tree) {
        let m = path.symlink_metadata().unwrap();
        let bytes = if m.is_file() {
            std::fs::read(path).unwrap()
        } else {
            vec![]
        };
        out.insert(path.into(), (m.len(), m.modified().unwrap(), bytes));
        if m.is_dir() && !m.file_type().is_symlink() {
            for e in std::fs::read_dir(path).unwrap() {
                walk(&e.unwrap().path(), out);
            }
        }
    }
    let mut out = Tree::new();
    walk(root, &mut out);
    out
}
#[test]
fn tunnel_observation_fresh_typed_states_and_no_writes() {
    let f = Fixture::new();
    let first = f.read();
    assert!(first.complete);
    assert_eq!(first.entries[0].state, TunnelState::Active);
    f.os.login.borrow_mut().session_id += 1;
    assert_eq!(f.read().entries[0].state, TunnelState::Foreign);
    f.os.login.borrow_mut().session_id -= 1;
    let sid = f.os.login.borrow().owner_sid.clone();
    f.os.login.borrow_mut().owner_sid = "S-1-5-21-123-456-789-1002".into();
    assert_eq!(f.read().entries[0].state, TunnelState::Foreign);
    f.os.login.borrow_mut().owner_sid = sid;
    f.os.unknown.set(true);
    let unknown = f.read();
    assert!(!unknown.complete);
    assert_eq!(unknown.entries[0].state, TunnelState::Unreadable);
    f.os.unknown.set(false);
    f.os.child.borrow_mut().take();
    assert_eq!(f.read().entries[0].state, TunnelState::Residue);
    std::fs::write(&f.path, b"{").unwrap();
    assert_eq!(f.read().entries[0].state, TunnelState::Malformed);
    std::fs::remove_file(&f.path).unwrap();
    let empty = f.read();
    assert!(empty.entries.is_empty());
    assert!(
        empty.complete,
        "coverage comes from the successful scan, not list length"
    );
}
#[test]
fn tunnel_observation_intents_share_the_effect_validator() {
    let f = Fixture::new();
    let name = f.path.file_stem().unwrap().to_str().unwrap();
    let launch = f.os.captured.borrow().clone();
    let stop = stop_intent_bytes(&f.bytes, &f.record).unwrap();
    for (kind, path, bytes) in [
        (TunnelKind::LaunchIntent, f.os.intent.clone(), launch),
        (TunnelKind::StopIntent, f.marker(), stop),
    ] {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, &bytes).unwrap();
        assert_eq!(
            classify_intent(kind, &bytes, name, Some((&f.bytes, &f.record))),
            Ok(())
        );
        let read = f.read();
        assert_eq!(
            read.entries.iter().find(|e| e.path == path).unwrap().state,
            TunnelState::Active
        );
        assert!(read.complete);
        let live = f.os.child.borrow_mut().take();
        assert_eq!(
            f.read()
                .entries
                .iter()
                .find(|e| e.path == path)
                .unwrap()
                .state,
            TunnelState::Residue
        );
        *f.os.child.borrow_mut() = live;
        f.os.unknown.set(true);
        assert_eq!(
            f.read()
                .entries
                .iter()
                .find(|e| e.path == path)
                .unwrap()
                .state,
            TunnelState::Unreadable
        );
        f.os.unknown.set(false);
        // Valid JSON alone is not the recovery validator's receipt binding.
        let mut changed: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        changed["schema_version"] = 2.into();
        let changed = serde_json::to_vec(&changed).unwrap();
        std::fs::write(&path, &changed).unwrap();
        assert_eq!(
            classify_intent(kind, &changed, name, Some((&f.bytes, &f.record))),
            Err(if kind == TunnelKind::LaunchIntent {
                "TUNNEL_START_UNSETTLED"
            } else {
                "TUNNEL_RECORD_CHANGED"
            })
        );
        let state = if kind == TunnelKind::LaunchIntent {
            TunnelState::Malformed
        } else {
            TunnelState::Unreadable
        };
        assert_eq!(
            f.read()
                .entries
                .into_iter()
                .find(|e| e.path == path)
                .unwrap()
                .state,
            state
        );
        // A second call must read the new bytes, through the same validator as recovery.
        std::fs::write(&path, b"{").unwrap();
        assert_eq!(
            classify_intent(kind, b"{", name, Some((&f.bytes, &f.record))),
            Err(if kind == TunnelKind::LaunchIntent {
                "JSON_INVALID"
            } else {
                "TUNNEL_RECORD_CHANGED"
            })
        );
        let entry = f
            .read()
            .entries
            .into_iter()
            .find(|e| e.path == path)
            .unwrap();
        assert_eq!(
            entry.state,
            if kind == TunnelKind::LaunchIntent {
                TunnelState::Malformed
            } else {
                TunnelState::Unreadable
            }
        );
        std::fs::remove_file(path).unwrap();
    }
    std::fs::write(&f.os.intent, f.os.captured.borrow().as_slice()).unwrap();
    std::fs::remove_file(&f.path).unwrap();
    let unmatched = f.read();
    assert!(!unmatched.complete);
    assert_eq!(unmatched.entries[0].state, TunnelState::Unreadable);
    std::fs::write(&f.os.intent, b"{").unwrap();
    assert_eq!(f.read().entries[0].state, TunnelState::Malformed);
    std::fs::write(&f.os.intent, f.os.captured.borrow().as_slice()).unwrap();
    // The original encoder supports flat legacy records without a parent proof.
    let mut legacy = TunnelRecord::parse(&f.bytes, None).unwrap();
    legacy.origin = None;
    std::fs::write(
        &f.path,
        serde_json::to_vec(&legacy.document().unwrap()).unwrap(),
    )
    .unwrap();
    let read = f.read();
    assert!(!read.complete);
    assert_eq!(
        read.entries
            .iter()
            .find(|e| e.path == f.os.intent)
            .unwrap()
            .state,
        TunnelState::Unreadable
    );
}
#[test]
fn tunnel_observation_unreadable_and_unparsed_names_are_incomplete() {
    let f = Fixture::new();
    let dir = f.path.parent().unwrap();
    for name in ["unparsed.txt", "bad name.json"] {
        let bad = dir.join(name);
        std::fs::write(&bad, b"evidence").unwrap();
        assert!(!f.read().complete);
        std::fs::remove_file(bad).unwrap();
    }
    std::fs::remove_file(&f.path).unwrap();
    std::fs::create_dir(&f.path).unwrap();
    let read = f.read();
    assert!(!read.complete);
    assert_eq!(read.entries[0].state, TunnelState::Unreadable);
}
#[test]
fn tunnel_observation_over_bound_directory_is_incomplete() {
    let f = Fixture::new();
    // The bound counts names. Separate empty files also work on filesystems
    // whose per-file hard-link limit is lower than the directory entry bound.
    for index in 0..ENTRY_BOUND {
        std::fs::write(
            f.path.parent().unwrap().join(format!("entry{index}.json")),
            b"",
        )
        .unwrap();
    }
    assert!(!f.read().complete);
}
#[cfg(unix)]
#[test]
fn tunnel_observation_reparse_directory_is_incomplete_without_following() {
    let f = Fixture::new();
    let dir = f.path.parent().unwrap();
    let target = dir.with_file_name("target");
    std::fs::rename(dir, &target).unwrap();
    std::os::unix::fs::symlink(&target, dir).unwrap();
    assert!(!f.read().complete);
}

#[test]
fn tunnel_observation_empty_unreadable_scope_is_not_complete() {
    let f = Fixture::new();
    let unknown = f.read_at(&f.disk.0.join("missing-scope"));
    assert!(unknown.entries.is_empty());
    assert!(!unknown.complete);
}
