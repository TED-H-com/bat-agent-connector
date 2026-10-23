use super::super::Scope;
use super::*;
use crate::creation_evidence::{StartupBinding, StartupRole};
use serde_json::{json, Value};
use std::{cell::Cell, fs, io::Write};

const ROLE: &[u8] = br#"{"version":1,"installation_ref":"11111111-1111-4111-8111-111111111111","generation":1,"role":"client"}"#;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Fault {
    None,
    Directory,
    Stage,
    Linked,
    Published,
    WrongBytes,
}
struct Fixture {
    temp: PathBuf,
    context: Context,
    held: Cell<bool>,
    fault: Cell<Fault>,
    session: Cell<u32>,
}
struct Observations<'a>(&'a Fixture);
fn file_id(file: &File) -> ObjectIdentity {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let m = file.metadata().unwrap();
        ObjectIdentity {
            volume: m.dev(),
            file_id: format!("{:032x}", m.ino()),
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::*;
        let mut info = FILE_ID_INFO::default();
        let buffer = (&mut info as *mut FILE_ID_INFO).cast();
        let size = std::mem::size_of_val(&info) as u32;
        let status =
            unsafe { GetFileInformationByHandleEx(file.as_raw_handle(), FileIdInfo, buffer, size) };
        assert_ne!(status, 0);
        let file_id = info
            .FileId
            .Identifier
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        ObjectIdentity {
            volume: info.VolumeSerialNumber,
            file_id,
        }
    }
}
fn open(path: &Path) -> File {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // Deliberately permissive observations: the real walk must reject links itself.
        options.custom_flags(0x02000000).share_mode(7);
    }
    options
        .open(path)
        .unwrap_or_else(|error| panic!("fixture open {path:?}: {error:?}"))
}
impl Security for Observations<'_> {
    fn verify(&self) -> Result<LoginIdentity> {
        ensure(self.0.held.get())?;
        Ok(LoginIdentity {
            owner_sid: "S-1-5-21-123-456-789-1001".into(),
            session_id: self.0.session.get(),
        })
    }
    fn validate_path(&self, _: &Path) -> Result<()> {
        Ok(())
    }
    fn open(&self, path: &Path, _: bool, _: bool) -> Result<File> {
        Ok(open(path))
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        Ok(file_id(file))
    }
    fn create_directory(&self, path: &Path) -> Result<()> {
        crate::publication_state::create_new_directory(path).map_err(|_| Error::Unavailable)?;
        self.0.fail(Fault::Directory)?;
        Ok(())
    }
    fn publish(
        &self,
        path: &Path,
        stage: &Path,
        bytes: &[u8],
        verify: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()> {
        verify()?;
        let mut file = crate::publication_state::create_new_file(stage).map_err(|error| {
            eprintln!(
                "create-only stage {stage:?} for {path:?}: {error}; last OS error: {:?}",
                std::io::Error::last_os_error()
            );
            Error::Conflict
        })?;
        file.write_all(bytes).unwrap();
        file.sync_all().unwrap();
        self.0.fail(Fault::Stage)?;
        verify()?;
        drop(file);
        // Portable no-replace surrogate; not a Windows durability implementation.
        fs::hard_link(stage, path).map_err(|error| {
            eprintln!("create-only link {stage:?} -> {path:?}: {error:?}");
            Error::Conflict
        })?;
        self.0.fail(Fault::Linked)?;
        fs::remove_file(stage).unwrap();
        self.0.fail(Fault::Published)?;
        if self.0.fault.get() == Fault::WrongBytes {
            fs::write(path, b"{}").unwrap();
        }
        Ok(())
    }
}
impl Fixture {
    fn fail(&self, fault: Fault) -> Result<()> {
        ensure(self.fault.get() != fault).map_err(|_| Error::Unavailable)
    }
    fn root(&self) -> PathBuf {
        self.context.state.join("connections-v1")
    }
    fn new() -> Self {
        Self::under(&std::env::temp_dir())
    }
    fn under(base: &Path) -> Self {
        // Resolve only the external fixture base, before creating protected state.
        // This also gives native Windows primitives extended-length path spelling.
        let temp = base
            .canonicalize()
            .unwrap_or_else(|error| panic!("fixture base {base:?}: {error:?}"))
            .join(format!(
                "participant-authority-{:032x}",
                rand::random::<u128>()
            ));
        fs::create_dir(&temp).unwrap();
        let state = temp.join("state");
        fs::create_dir(&state).unwrap();
        let startup_record = temp.join("role.json");
        fs::write(&startup_record, ROLE).unwrap();
        Self {
            context: Context {
                state_identity: file_id(&open(&state)),
                state,
                startup_record,
                startup: StartupBinding {
                    installation_ref: "11111111-1111-4111-8111-111111111111".into(),
                    role_record_sha256: digest(ROLE),
                    generation: 1,
                    role: StartupRole::Client,
                },
            },
            temp,
            held: Cell::new(true),
            fault: Cell::new(Fault::None),
            session: Cell::new(1),
        }
    }
    fn namespace(&self, op: char) -> Namespace {
        Namespace::for_operation(
            &Scope {
                server_id: "fixture".into(),
                principal_id: "a".repeat(64),
                native_installation: self.context.startup.installation_ref.clone(),
                installation_ref: "desktop-fixture".into(),
            },
            &format!("op_{}", op.to_string().repeat(32)),
        )
        .unwrap_or_else(|error| panic!("fixture namespace {op}: {error:?}"))
    }
    fn authority(&self) -> Authority<Observations<'_>> {
        Authority::new(
            self.context.clone(),
            Observations(self),
            &self.namespace('b'),
        )
        .unwrap_or_else(|error| panic!("authority for {:?}: {error:?}", self.context.state))
    }
    fn levels(&self) -> [Level; 3] {
        let n = self.namespace('b').relative_path();
        let scope = n.split('/').nth(2).unwrap();
        [
            Level::participant(),
            Level::scope(scope).unwrap(),
            Level::operation(scope, &format!("op_{}", "b".repeat(32))).unwrap(),
        ]
    }
    fn prepare(&self) {
        let a = self.authority();
        a.bootstrap()
            .unwrap_or_else(|error| panic!("prepare bootstrap {:?}: {error:?}", self.temp));
        for level in self.levels() {
            a.create_level(&self.namespace('b'), &level)
                .unwrap_or_else(|error| panic!("prepare level {}: {error:?}", level.relative()));
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.temp).unwrap();
    }
}
fn invalid<T>(result: Result<T>) {
    assert_eq!(result.err(), Some(Error::Invalid));
}
fn snapshot(path: &Path) -> Vec<(PathBuf, Option<Vec<u8>>)> {
    let mut result = Vec::new();
    for item in fs::read_dir(path).unwrap() {
        let p = item.unwrap().path();
        if p.is_dir() {
            result.push((p.clone(), None));
            result.extend(snapshot(&p));
        } else {
            result.push((p.clone(), Some(fs::read(p).unwrap())));
        }
    }
    result.sort();
    result
}
#[test]
fn fixture_resolves_temporary_base_alias_before_authority_walk() {
    let parent = Fixture::new();
    let alias = parent.temp.join("alias");
    link_directory(&parent.temp, &alias);
    {
        let f = Fixture::under(&alias);
        assert_eq!(
            f.temp,
            f.temp.canonicalize().unwrap(),
            "resolved fixture base"
        );
        f.prepare();
    }
    #[cfg(unix)]
    fs::remove_file(alias).unwrap();
    #[cfg(windows)]
    fs::remove_dir(alias).unwrap();
}
#[test]
fn fixture_supports_native_stage_paths_longer_than_max_path() {
    let parent = Fixture::new();
    let base = parent.temp.join("long".repeat(40));
    fs::create_dir(&base).unwrap();
    let f = Fixture::under(&base);
    let stage = f.levels()[2].stage(&f.root());
    assert!(stage.as_os_str().len() > 260, "long-stage case: {stage:?}");
    f.prepare();
}
#[test]
fn absent_root_is_observation_only_and_complete_chain_reopens() {
    let f = Fixture::new();
    assert_eq!(
        f.authority().observe(&f.namespace('b'), &f.levels()[2]),
        Ok(None)
    );
    assert!(!f.root().exists());
    f.prepare();
    let before = snapshot(&f.temp);
    f.session.set(2);
    let a = f.authority();
    a.bootstrap().unwrap();
    for level in f.levels() {
        assert!(a.observe(&f.namespace('b'), &level).unwrap().is_some());
    }
    assert_eq!(snapshot(&f.temp), before);
    assert_eq!(
        a.create_level(&f.namespace('b'), &f.levels()[0]),
        Err(Error::Conflict)
    );
}
#[test]
fn foreign_namespace_and_unrecorded_parent_refuse_before_effects() {
    let f = Fixture::new();
    let a = f.authority();
    a.bootstrap().unwrap();
    let before = snapshot(&f.temp);
    invalid(a.create_level(&f.namespace('c'), &f.levels()[0]));
    invalid(a.create_level(&f.namespace('b'), &f.levels()[2]));
    invalid(a.create_level(&f.namespace('b'), &Level::scope(&"f".repeat(64)).unwrap()));
    assert_eq!(snapshot(&f.temp), before);
}
#[test]
fn root_and_every_level_crash_residue_is_never_adopted_or_deleted() {
    for depth in 0..4 {
        for fault in [
            Fault::Directory,
            Fault::Stage,
            Fault::Linked,
            Fault::Published,
        ] {
            let f = Fixture::new();
            let ns = f.namespace('b');
            let levels = f.levels();
            {
                let a = f.authority();
                if depth > 0 {
                    a.bootstrap().unwrap();
                    for level in &levels[..depth - 1] {
                        a.create_level(&ns, level).unwrap();
                    }
                }
                f.fault.set(fault);
                let result = if depth == 0 {
                    a.bootstrap()
                } else {
                    a.create_level(&ns, &levels[depth - 1])
                };
                assert_eq!(result, Err(Error::Unavailable), "depth {depth}, {fault:?}");
            }
            f.fault.set(Fault::None);
            let before = snapshot(&f.temp);
            let a = f.authority();
            let result = if depth == 0 {
                a.bootstrap()
            } else {
                a.observe(&ns, &levels[depth - 1])
                    .map(|id| assert!(id.is_some()))
            };
            assert_eq!(
                result,
                if fault == Fault::Published {
                    Ok(())
                } else {
                    Err(Error::Invalid)
                },
                "reopen depth {depth}, {fault:?}"
            );
            assert_eq!(snapshot(&f.temp), before);
        }
    }
}
#[test]
fn recorded_but_missing_directory_and_stage_only_are_not_absence() {
    for fault in 0..5 {
        let f = Fixture::new();
        f.prepare();
        let level = f.levels()[2].clone();
        let path = f.root().join(level.relative());
        if fault == 0 {
            fs::remove_dir(&path).unwrap();
        }
        if fault == 1 {
            fs::remove_file(level.record(&f.root())).unwrap();
        }
        if fault == 2 {
            fs::write(level.stage(&f.root()), b"matching staged proof").unwrap();
        }
        if fault == 3 {
            fs::remove_dir_all(f.root()).unwrap();
        }
        if fault == 4 {
            fs::write(f.root().join("unknown"), b"residue").unwrap();
        }
        let before = snapshot(&f.temp);
        invalid(f.authority().observe(&f.namespace('b'), &level));
        assert_eq!(snapshot(&f.temp), before);
    }
}
fn codec<T: authority::Document>(original: &[u8], limit: usize) {
    let decoded = decode::<T>(original, limit).unwrap();
    assert_eq!(encode(&decoded, limit).unwrap(), original);
    let value: Value = serde_json::from_slice(original).unwrap();
    for (field, replacement, error) in [
        ("future", json!(true), Error::Invalid),
        ("minimum_reader_version", json!(2), Error::Unsupported),
        ("minimum_reader_version", json!("wrong"), Error::Invalid),
    ] {
        let mut candidate = value.clone();
        candidate[field] = replacement;
        assert_eq!(
            decode::<T>(&serde_json::to_vec(&candidate).unwrap(), limit).err(),
            Some(error)
        );
    }
    let mut future = value;
    future["format_version"] = 2.into();
    future["extra"] = true.into();
    assert_eq!(
        decode::<T>(&serde_json::to_vec(&future).unwrap(), limit).err(),
        Some(Error::Unsupported)
    );
    let duplicate = [b"{\"format_version\":1,".as_slice(), &original[1..]].concat();
    assert_eq!(decode::<T>(&duplicate, limit).err(), Some(Error::Invalid));
    assert_eq!(
        decode::<T>(&vec![b' '; limit + 1], limit).err(),
        Some(Error::Invalid)
    );
}
#[test]
fn strict_receipt_and_sidecar_codecs_reject_bad_versions_fields_syntax_and_bounds() {
    let f = Fixture::new();
    f.prepare();
    codec::<RootReceipt>(
        &fs::read(f.context.state.join("connection-participant-root.json")).unwrap(),
        ROOT_LIMIT,
    );
    codec::<DirectoryRecord>(
        &fs::read(f.levels()[2].record(&f.root())).unwrap(),
        LEVEL_LIMIT,
    );
}
#[test]
fn native_identity_parent_and_sidecar_binding_changes_refuse() {
    let f = Fixture::new();
    f.prepare();
    let root = f.context.state.join("connection-participant-root.json");
    let side = f.levels()[2].record(&f.root());
    let id = "f".repeat(32);
    let other = "S-1-5-21-999-456-789-1001";
    for (path, pointer, replacement) in [
        (&root, "/context/startup/generation", json!(2)),
        (
            &root,
            "/context/startup/installation_ref",
            json!("bad-uuid"),
        ),
        (&root, "/context/state_identity/file_id", json!(id)),
        (&root, "/creator/owner_sid", json!(other)),
        (&root, "/identity/file_id", json!(id)),
        (&side, "/identity/file_id", json!(id)),
        (&side, "/parent_identity/file_id", json!(id)),
        (&side, "/root_digest", json!("f".repeat(64))),
        (&side, "/root_id", json!("bad-id")),
        (&side, "/owner_sid", json!("bad-sid")),
        (&side, "/relative_path", json!("../escape")),
    ] {
        let original = fs::read(path).unwrap();
        let mut v: Value = serde_json::from_slice(&original).unwrap();
        *v.pointer_mut(pointer).unwrap() = replacement;
        fs::write(path, serde_json::to_vec(&v).unwrap()).unwrap();
        assert_eq!(
            f.authority().observe(&f.namespace('b'), &f.levels()[2]),
            Err(Error::Invalid),
            "{pointer}"
        );
        fs::write(path, original).unwrap();
    }
}
#[test]
fn changed_readback_and_revoked_guard_refuse() {
    let f = Fixture::new();
    f.fault.set(Fault::WrongBytes);
    invalid(f.authority().bootstrap());
    let f = Fixture::new();
    let a = f.authority();
    f.held.set(false);
    invalid(a.bootstrap());
    assert!(!f.root().exists());
}
fn linked_level(link: impl FnOnce(&Path, &Path)) {
    let f = Fixture::new();
    {
        let a = f.authority();
        a.bootstrap().unwrap();
        a.create_level(&f.namespace('b'), &f.levels()[0]).unwrap();
    }
    let path = f.root().join("participant");
    let moved = f.temp.join("original");
    fs::rename(&path, &moved).unwrap();
    link(&moved, &path);
    invalid(f.authority().observe(&f.namespace('b'), &f.levels()[0]));
    assert!(fs::read_dir(&moved).unwrap().next().is_none());
    #[cfg(windows)]
    fs::remove_dir(&path).unwrap();
    #[cfg(unix)]
    fs::remove_file(&path).unwrap();
}
#[cfg(unix)]
#[test]
fn followed_symlink_with_original_file_id_is_rejected() {
    linked_level(link_directory);
}
#[cfg(windows)]
#[test]
fn followed_junction_with_original_file_id_is_rejected() {
    linked_level(link_directory);
}
fn link_directory(target: &Path, path: &Path) {
    assert!(target.is_dir(), "link target missing: {target:?}");
    assert!(
        !path.try_exists().unwrap(),
        "link path already exists: {path:?}"
    );
    #[cfg(unix)]
    std::os::unix::fs::symlink(target, path)
        .unwrap_or_else(|error| panic!("symlink {path:?} -> {target:?}: {error:?}"));
    #[cfg(windows)]
    {
        let output = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(path)
            .arg(target)
            .output()
            .unwrap_or_else(|error| panic!("junction {path:?} -> {target:?}: {error:?}"));
        assert!(
            output.status.success(),
            "junction {path:?} -> {target:?}: status {}; stderr: {}; stdout: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr),
            String::from_utf8_lossy(&output.stdout)
        );
    }
}
