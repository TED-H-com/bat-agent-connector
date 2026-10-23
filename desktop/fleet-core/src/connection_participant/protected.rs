//! Installation-bound root and per-level evidence policy. No Backend or native caller yet.
use super::{
    model::{hex, uuid},
    Error, Namespace, Result,
};
use crate::{
    creation_evidence::{Creator, ObjectIdentity},
    digest,
    process_adapter::LoginIdentity,
    publication_state::{metadata, read_bounded, walk_checked, WalkObject},
};
use std::{
    cell::RefCell,
    collections::BTreeMap,
    fs::File,
    path::{Path, PathBuf},
    rc::Rc,
};
mod authority;
use authority::{decode, encode, DirectoryRecord, RootReceipt};
pub use authority::{Context, Level, LEVEL_LIMIT, ROOT_LIMIT};
const UNPROVEN: &str = "PARTICIPANT_UNPROVEN";
fn ensure(value: bool) -> Result<()> {
    value.then_some(()).ok_or(Error::Invalid)
}
/// OS observations only. Implementations must hold Launcher and the original native
/// ticket on this synchronous thread; there is no production implementation here.
pub trait Security {
    fn verify(&self) -> Result<LoginIdentity>;
    fn validate_path(&self, path: &Path) -> Result<()>;
    /// Directory sharing is READ|WRITE, never DELETE; file reads share READ only.
    fn open(&self, path: &Path, private: bool, child_absent: bool) -> Result<File>;
    fn identity(&self, file: &File) -> Result<ObjectIdentity>;
    fn create_directory(&self, path: &Path) -> Result<()>;
    /// Strict create-new stage, flush, same-directory no-replace write-through
    /// publication, checked cleanup/readback; verify before stage and final effects.
    /// Power-loss persistence rests on that API behavior and is not proven by CI.
    fn publish(
        &self,
        path: &Path,
        stage: &Path,
        bytes: &[u8],
        verify: &mut dyn FnMut() -> Result<()>,
    ) -> Result<()>;
}
pub struct Authority<S> {
    context: Context,
    security: S,
    login: LoginIdentity,
    namespace: String,
    directories: RefCell<BTreeMap<PathBuf, (Rc<File>, ObjectIdentity)>>,
}
/// Only this live invocation can bridge a just-created, not-yet-recorded level.
struct Birth {
    path: PathBuf,
    parent: ObjectIdentity,
    identity: ObjectIdentity,
}
impl<S: Security> Authority<S> {
    pub fn new(context: Context, security: S, namespace: &Namespace) -> Result<Self> {
        context.validate()?;
        let login = security.verify()?;
        ensure(login.valid())?;
        let result = Self {
            context,
            security,
            login,
            namespace: namespace.relative_path(),
            directories: RefCell::default(),
        };
        result.base()?;
        Ok(result)
    }
    fn root_path(&self) -> PathBuf {
        self.context.state.join("connections-v1")
    }
    fn receipt_path(&self) -> PathBuf {
        self.context.state.join("connection-participant-root.json")
    }
    fn root_stage(&self) -> PathBuf {
        self.context
            .state
            .join(".connection-participant-root.stage")
    }
    fn walk(&self, path: &Path) -> Result<Option<Rc<File>>> {
        self.security.validate_path(path)?;
        walk_checked(
            path,
            &mut self.directories.borrow_mut(),
            UNPROVEN,
            |p, child| {
                let Some(m) = metadata(p)? else {
                    return Ok(None);
                };
                let private = p.starts_with(self.root_path())
                    || p == self.receipt_path()
                    || p == self.context.startup_record;
                let absent = child
                    .map(|c| metadata(c).map(|m| m.is_none()))
                    .transpose()?
                    .unwrap_or(false);
                let file = self
                    .security
                    .open(p, private, absent)
                    .map_err(|_| UNPROVEN)?;
                if file.metadata().map_err(|_| UNPROVEN)?.is_dir() != m.is_dir() {
                    return Err(UNPROVEN);
                }
                let id = self.security.identity(&file).map_err(|_| UNPROVEN)?;
                Ok(Some(WalkObject::new(m, Rc::new(file), id)))
            },
        )
        .map(|o| o.map(|o| o.handle))
        .map_err(|_| Error::Invalid)
    }
    fn directory(&self, path: &Path) -> Result<Option<ObjectIdentity>> {
        self.walk(path)?
            .map(|f| {
                ensure(f.metadata().map_err(|_| Error::Unavailable)?.is_dir())?;
                self.security.identity(&f)
            })
            .transpose()
    }
    fn read(&self, path: &Path, bound: usize) -> Result<Option<Vec<u8>>> {
        let Some(file) = self.walk(path)? else {
            return Ok(None);
        };
        ensure(file.metadata().map_err(|_| Error::Unavailable)?.len() <= bound as u64)?;
        let id = self.security.identity(&file)?;
        let bytes = read_bounded(&file, bound).map_err(|_| Error::Invalid)?;
        let current = self.walk(path)?.ok_or(Error::Conflict)?;
        ensure(self.security.identity(&current)? == id)?;
        Ok(Some(bytes))
    }
    fn base(&self) -> Result<()> {
        ensure(self.security.verify()? == self.login)?;
        ensure(self.directory(&self.context.state)? == Some(self.context.state_identity.clone()))?;
        ensure(
            self.read(&self.context.startup_record, ROOT_LIMIT)?
                .is_some_and(|b| digest(&b) == self.context.startup.role_record_sha256),
        )
    }
    fn root(&self) -> Result<Option<(RootReceipt, Vec<u8>)>> {
        self.base()?;
        ensure(self.walk(&self.root_stage())?.is_none())?;
        let directory = self.directory(&self.root_path())?;
        let bytes = self.read(&self.receipt_path(), ROOT_LIMIT)?;
        match (directory, bytes) {
            (None, None) => Ok(None),
            (Some(id), Some(bytes)) => {
                let r: RootReceipt = decode(&bytes, ROOT_LIMIT)?;
                ensure(
                    r.context == self.context
                        && r.creator.owner_sid == self.login.owner_sid
                        && r.identity == id,
                )?;
                let proof = Level::participant().record(&self.root_path());
                for entry in std::fs::read_dir(self.root_path()).map_err(|_| Error::Unavailable)? {
                    let name = entry.map_err(|_| Error::Unavailable)?.file_name();
                    ensure(name == "participant" || Some(name.as_os_str()) == proof.file_name())?;
                }
                Ok(Some((r, bytes)))
            }
            _ => Err(Error::Invalid),
        }
    }
    fn birth(&self, path: &Path, parent: ObjectIdentity) -> Result<Birth> {
        self.base()?;
        ensure(self.walk(path)?.is_none())?;
        ensure(self.directory(path.parent().ok_or(Error::Invalid)?)? == Some(parent.clone()))?;
        ensure(self.security.verify()? == self.login)?;
        self.security.create_directory(path)?;
        let identity = self.directory(path)?.ok_or(Error::Invalid)?;
        Ok(Birth {
            path: path.into(),
            parent,
            identity,
        })
    }
    fn verify_birth(&self, birth: &Birth) -> Result<()> {
        self.base()?;
        ensure(
            self.directory(&birth.path)? == Some(birth.identity.clone())
                && self.directory(birth.path.parent().ok_or(Error::Invalid)?)?
                    == Some(birth.parent.clone()),
        )
    }
    pub fn bootstrap(&self) -> Result<()> {
        if self.root()?.is_some() {
            return Ok(());
        }
        let birth = self.birth(&self.root_path(), self.context.state_identity.clone())?;
        let record = RootReceipt {
            format_version: 1,
            minimum_reader_version: 1,
            context: self.context.clone(),
            creator: Creator {
                owner_sid: self.login.owner_sid.clone(),
                session_id: self.login.session_id,
                reservation_id: format!("{:032x}", rand::random::<u128>()),
            },
            root: birth.path.clone(),
            identity: birth.identity.clone(),
        };
        let bytes = encode(&record, ROOT_LIMIT)?;
        self.security.publish(
            &self.receipt_path(),
            &self.root_stage(),
            &bytes,
            &mut || self.verify_birth(&birth),
        )?;
        ensure(self.root()?.is_some_and(|(_, b)| b == bytes))
    }
    fn bound(&self, namespace: &Namespace, level: &Level) -> Result<()> {
        let selected = namespace.relative_path();
        let prefix = format!("connections-v1/{}", level.relative());
        ensure(
            selected == self.namespace
                && (selected == prefix || selected.starts_with(&(prefix + "/"))),
        )
    }
    pub fn observe(&self, namespace: &Namespace, level: &Level) -> Result<Option<ObjectIdentity>> {
        self.bound(namespace, level)?;
        let Some((root, bytes)) = self.root()? else {
            return Ok(None);
        };
        let mut levels = vec![level.clone()];
        while let Some(parent) = levels.last().unwrap().parent() {
            levels.push(parent)
        }
        let mut parent = Some(root.identity);
        for level in levels.into_iter().rev() {
            ensure(self.walk(&level.stage(&self.root_path()))?.is_none())?;
            let id = self.directory(&self.root_path().join(level.relative()))?;
            let record = self.read(&level.record(&self.root_path()), LEVEL_LIMIT)?;
            parent = match (id, record) {
                (None, None) => None,
                (Some(id), Some(record)) => {
                    let r: DirectoryRecord = decode(&record, LEVEL_LIMIT)?;
                    ensure(
                        r.root_digest == digest(&bytes)
                            && r.root_id == root.creator.reservation_id
                            && r.owner_sid == self.login.owner_sid
                            && r.relative_path == level.relative()
                            && Some(r.parent_identity) == parent
                            && r.identity == id,
                    )?;
                    Some(id)
                }
                _ => return Err(Error::Invalid),
            };
        }
        Ok(parent)
    }
    pub fn create_level(&self, namespace: &Namespace, level: &Level) -> Result<()> {
        self.bound(namespace, level)?;
        let (root, root_bytes) = self.root()?.ok_or(Error::Unavailable)?;
        if self.observe(namespace, level)?.is_some() {
            return Err(Error::Conflict);
        }
        let parent = match level.parent() {
            Some(p) => self.observe(namespace, &p)?.ok_or(Error::Invalid)?,
            None => root.identity,
        };
        let birth = self.birth(&self.root_path().join(level.relative()), parent.clone())?;
        let record = DirectoryRecord {
            format_version: 1,
            minimum_reader_version: 1,
            root_digest: digest(&root_bytes),
            root_id: root.creator.reservation_id,
            relative_path: level.relative().into(),
            parent_identity: parent,
            identity: birth.identity.clone(),
            owner_sid: self.login.owner_sid.clone(),
            session_id: self.login.session_id,
        };
        let bytes = encode(&record, LEVEL_LIMIT)?;
        self.security.publish(
            &level.record(&self.root_path()),
            &level.stage(&self.root_path()),
            &bytes,
            &mut || {
                self.verify_birth(&birth)?;
                ensure(
                    self.read(&self.receipt_path(), ROOT_LIMIT)?.as_deref()
                        == Some(root_bytes.as_slice()),
                )
            },
        )?;
        ensure(
            self.read(&level.record(&self.root_path()), LEVEL_LIMIT)?
                .as_deref()
                == Some(bytes.as_slice()),
        )?;
        ensure(self.observe(namespace, level)? == Some(birth.identity))
    }
}
#[cfg(test)]
#[path = "protected/authority_tests.rs"]
mod authority_tests;
