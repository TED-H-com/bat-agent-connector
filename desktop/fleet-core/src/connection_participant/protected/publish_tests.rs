use super::native::*;
use super::*;
use crate::compare_replace::Phase;
use std::cell::RefCell;

struct Disk {
    root: PathBuf,
    events: RefCell<Vec<String>>,
    fault: CellFault,
}
type CellFault = std::cell::Cell<u8>;
fn id(file: &File) -> ObjectIdentity {
    #[cfg(windows)]
    {
        crate::publication_state::file_identity(file).unwrap()
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let m = file.metadata().unwrap();
        ObjectIdentity {
            volume: m.dev(),
            file_id: format!("{:032x}", m.ino()),
        }
    }
}
impl Disk {
    fn new() -> Self {
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("participant-stage-{:032x}", rand::random::<u128>()));
        crate::publication_state::create_new_directory(&root).unwrap();
        Self {
            root,
            events: RefCell::default(),
            fault: CellFault::new(0),
        }
    }
    fn publish(&self, bytes: &[u8]) -> Result<()> {
        publish_with(
            self,
            &self.root.join("part.json"),
            &self.root.join(".part.stage"),
            bytes,
            64,
            &mut || Ok(()),
            &mut |s| {
                self.events.borrow_mut().push(format!(
                    "{:?}:{}",
                    s.phase,
                    s.path.file_name().unwrap().to_string_lossy()
                ));
                if s.phase != Phase::Planned {
                    assert!(s.identity.is_some());
                }
                Ok(())
            },
        )
    }
}
impl Drop for Disk {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}
impl Io for Disk {
    fn create(&self, path: &Path) -> Result<File> {
        crate::publication_state::create_new_file(path).map_err(|_| Error::Invalid)
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        Ok(id(file))
    }
    fn observe(&self, path: &Path, bound: usize) -> Result<Option<(Vec<u8>, ObjectIdentity)>> {
        self.events.borrow_mut().push(format!(
            "read:{}",
            path.file_name().unwrap().to_string_lossy()
        ));
        if self.fault.get() == 6 && path.ends_with(".part.stage") {
            self.fault.set(0);
            fs::rename(path, self.root.join("original")).unwrap();
            fs::write(path, b"foreign").unwrap();
        }
        let file = match File::open(path) {
            Ok(f) => f,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(Error::Unavailable),
        };
        let mut identity = id(&file);
        if self.fault.get() == 4 && path.ends_with("part.json") {
            identity.file_id = "f".repeat(32);
        }
        let mut bytes =
            crate::publication_state::read_bounded(&file, bound).map_err(|_| Error::Invalid)?;
        if self.fault.get() == 5 && path.ends_with("part.json") {
            bytes.push(b'!');
        }
        Ok(Some((bytes, identity)))
    }
    fn flush(&self, file: &File) -> Result<()> {
        self.events.borrow_mut().push("flush".into());
        if self.fault.get() == 1 {
            return Err(Error::Unavailable);
        }
        file.sync_all().map_err(|_| Error::Unavailable)
    }
    fn move_file(&self, stage: &Path, path: &Path, flags: u32) -> Result<()> {
        self.events.borrow_mut().push(format!("move:{flags}"));
        assert_eq!(
            flags, 8,
            "only WRITE_THROUGH; no REPLACE_EXISTING or COPY_ALLOWED"
        );
        assert!(
            self.events.borrow().iter().any(|e| e == "flush"),
            "flush before move"
        );
        if self.fault.get() == 2 {
            return Err(Error::Unavailable);
        }
        if self.fault.get() == 3 {
            fs::write(path, b"raced").unwrap();
        }
        // Disk surrogate only; Windows uses MoveFileExW, never this link.
        fs::hard_link(stage, path).map_err(|_| Error::Conflict)?;
        fs::remove_file(stage).map_err(|_| Error::Unavailable)
    }
    fn remove(&self, path: &Path) -> Result<()> {
        fs::remove_file(path).map_err(|_| Error::Unavailable)
    }
}
use std::fs;
#[test]
fn stage_policy_orders_flush_move_and_exact_identity_readback() {
    let d = Disk::new();
    d.publish(b"candidate").unwrap();
    assert_eq!(fs::read(d.root.join("part.json")).unwrap(), b"candidate");
    assert!(!d.root.join(".part.stage").exists());
    let events = d.events.borrow();
    for required in [
        "Planned:.part.stage",
        "Created:.part.stage",
        "Flushed:.part.stage",
        "Published:.part.stage",
        "move:8",
    ] {
        assert!(
            events.iter().any(|e| e == required),
            "missing {required}: {events:?}"
        );
    }
    assert_eq!(
        events.iter().filter(|e| *e == "read:part.json").count(),
        3,
        "before, fence, readback"
    );
}
#[test]
fn fixed_stage_and_existing_targets_are_never_adopted_or_replaced() {
    let d = Disk::new();
    for _ in 0..2 {
        d.fault.set(1);
        assert_eq!(d.publish(b"new"), Err(Error::Unavailable));
    }
    assert_eq!(
        d.events
            .borrow()
            .iter()
            .filter(|e| *e == "Planned:.part.stage")
            .count(),
        2
    );
    fs::write(d.root.join(".part.stage"), b"crash residue").unwrap();
    d.fault.set(0);
    assert_eq!(d.publish(b"new"), Err(Error::Invalid));
    assert_eq!(
        fs::read(d.root.join(".part.stage")).unwrap(),
        b"crash residue"
    );
    fs::remove_file(d.root.join(".part.stage")).unwrap();
    d.publish(b"old").unwrap();
    let before = id(&File::open(d.root.join("part.json")).unwrap());
    for bytes in [b"old".as_slice(), b"new"] {
        assert_eq!(d.publish(bytes), Err(Error::Conflict));
        assert_eq!(id(&File::open(d.root.join("part.json")).unwrap()), before);
        assert_eq!(fs::read(d.root.join("part.json")).unwrap(), b"old");
    }
}
#[test]
fn publication_faults_refuse_without_partial_target_or_foreign_cleanup() {
    for fault in 1..=5 {
        let d = Disk::new();
        d.fault.set(fault);
        assert_eq!(
            d.publish(b"candidate"),
            Err(if fault < 3 {
                Error::Unavailable
            } else {
                Error::Conflict
            }),
            "fault {fault}"
        );
        let target = fs::read(d.root.join("part.json")).ok();
        assert_eq!(
            target.as_deref(),
            match fault {
                1 | 2 => None,
                3 => Some(b"raced".as_slice()),
                _ => Some(b"candidate".as_slice()),
            }
        );
    }
    let d = Disk::new();
    d.fault.set(6);
    assert_eq!(d.publish(b"new"), Err(Error::Unavailable));
    assert_eq!(fs::read(d.root.join(".part.stage")).unwrap(), b"foreign");
}
#[test]
fn invalid_paths_bounds_and_revoked_fence_refuse_before_publication() {
    let d = Disk::new();
    for text in [
        r"relative",
        r"C:\a\..\b",
        r"\\server\share\x",
        r"C:\a/./b",
        "C:\\a\0b",
    ] {
        assert_eq!(extended_name(text), Err(Error::Invalid), "{text:?}");
    }
    assert_eq!(
        extended_name(r"C:/native/a/b").unwrap(),
        r"\\?\C:\native\a\b"
    );
    assert_eq!(d.publish(&[0; 65]), Err(Error::Invalid));
    let mut calls = 0;
    let result = publish_with(
        &d,
        &d.root.join("part.json"),
        &d.root.join(".part.stage"),
        b"new",
        64,
        &mut || {
            calls += 1;
            if calls > 1 {
                Err(Error::Unavailable)
            } else {
                Ok(())
            }
        },
        &mut |_| Ok(()),
    );
    assert_eq!(result, Err(Error::Unavailable));
    assert!(!d.root.join("part.json").exists());
}
#[test]
fn process_kill_stage_child() {
    let Some(root) = std::env::var_os("FLEET_STAGE_TEST_ROOT") else {
        return;
    };
    let d = Disk {
        root: root.into(),
        events: RefCell::default(),
        fault: CellFault::new(0),
    };
    let root = d.root.clone();
    #[cfg(windows)]
    let io = WindowsIo::new(&root).unwrap();
    #[cfg(not(windows))]
    let io = d;
    let stop = std::env::var("FLEET_STAGE_TEST_STOP").unwrap();
    publish_with(
        &io,
        &root.join("part.json"),
        &root.join(".part.stage"),
        b"candidate",
        64,
        &mut || Ok(()),
        &mut |stage| {
            if format!("{:?}", stage.phase) == stop {
                fs::write(root.join("checkpoint"), b"ready").unwrap();
                loop {
                    std::thread::park();
                }
            }
            Ok(())
        },
    )
    .unwrap();
    panic!("child failed to stop at {stop}");
}
#[test]
fn process_kill_before_and_after_move_never_adopts_residue() {
    use std::{
        process::{Command, Stdio},
        time::{Duration, Instant},
    };
    for phase in ["Flushed", "Published"] {
        let d = Disk::new();
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "connection_participant::protected::publish_tests::process_kill_stage_child",
                "--nocapture",
            ])
            .env("FLEET_STAGE_TEST_ROOT", &d.root)
            .env("FLEET_STAGE_TEST_STOP", phase)
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        while !d.root.join("checkpoint").exists() && Instant::now() < deadline {
            if child.try_wait().unwrap().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let reached = d.root.join("checkpoint").exists();
        let _ = child.kill();
        child.wait().unwrap();
        assert!(reached, "child did not reach {phase}");
        assert_eq!(
            fs::read(d.root.join("part.json")).ok(),
            (phase == "Published").then(|| b"candidate".to_vec())
        );
        assert_eq!(
            d.publish(b"candidate"),
            Err(if phase == "Published" {
                Error::Conflict
            } else {
                Error::Invalid
            })
        );
        assert_eq!(d.root.join(".part.stage").exists(), phase == "Flushed");
    }
}
#[cfg(windows)]
#[test]
fn windows_native_long_and_forward_slash_paths_never_leave_partial_targets() {
    let d = Disk::new();
    let target = d.root.join("part.json");
    let stage = d.root.join(".part.stage");
    let io = WindowsIo::new(&d.root).unwrap();
    let mut born = None;
    publish_with(
        &io,
        &target,
        &stage,
        b"candidate",
        64,
        &mut || Ok(()),
        &mut |s| {
            if s.phase == Phase::Created {
                born = s.identity.cloned();
            }
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(Some(id(&File::open(&target).unwrap())), born);
    let file = io.create(&stage).unwrap();
    io.flush(&file).unwrap();
    drop(file);
    assert_eq!(io.move_file(&stage, &target, 8), Err(Error::Conflict));
    assert_eq!(fs::read(&target).unwrap(), b"candidate");
    assert_eq!(Some(id(&File::open(&target).unwrap())), born);
    assert!(stage.exists());
    drop(io);
    let mut parent = d.root.clone();
    for _ in 0..3 {
        parent.push("long".repeat(25));
        crate::publication_state::create_new_directory(&parent).unwrap();
    }
    assert!(parent.as_os_str().len() > 260);
    for forward in [false, true] {
        let target = parent.join(if forward { "slash.json" } else { "long.json" });
        let stage = target.with_extension("stage");
        let spell = |p: &Path| {
            if forward {
                PathBuf::from(
                    p.to_str()
                        .unwrap()
                        .strip_prefix(r"\\?\")
                        .unwrap()
                        .replace('\\', "/"),
                )
            } else {
                p.to_owned()
            }
        };
        let result = publish(
            &spell(&target),
            &spell(&stage),
            b"candidate",
            64,
            &mut || Ok(()),
        );
        match result {
            Ok(()) => {
                assert_eq!(fs::read(&target).unwrap(), b"candidate");
            }
            Err(error) => {
                let actual = fs::read(&target);
                assert!(
                    actual.as_deref().is_ok_and(|b| b == b"candidate")
                        || actual
                            .as_ref()
                            .is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
                    "{forward}: {error:?}, partial target: {actual:?}"
                );
            }
        }
    }
}
