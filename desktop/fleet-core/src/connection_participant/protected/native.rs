//! Checked no-replace publication mechanics, with no Backend or native factory.
//! Caller retains installation/level authority, Launcher and its original ticket.
//! Power-loss persistence rests on MOVEFILE_WRITE_THROUGH, not CI evidence.
use super::*;
use crate::compare_replace::{self, Observer, Policy, Stage, Strategy};
const WRITE_THROUGH: u32 = 0x8;

pub(super) trait Io {
    fn create(&self, path: &Path) -> Result<File>;
    fn identity(&self, file: &File) -> Result<ObjectIdentity>;
    fn observe(&self, path: &Path, bound: usize) -> Result<Option<(Vec<u8>, ObjectIdentity)>>;
    fn flush(&self, file: &File) -> Result<()>;
    fn move_file(&self, stage: &Path, path: &Path, flags: u32) -> Result<()>;
    fn remove(&self, path: &Path) -> Result<()>;
}
struct NoReplace<'a, I> {
    stage: &'a Path,
    io: &'a I,
}
impl<I: Io> Strategy<Error> for NoReplace<'_, I> {
    type Identity = ObjectIdentity;
    const READBACK: bool = true;
    fn name(&mut self, path: &Path, _: &Policy<Error>) -> Result<PathBuf> {
        ensure(path != self.stage && path.parent() == self.stage.parent() && path.is_absolute())?;
        let name = self
            .stage
            .file_name()
            .and_then(|s| s.to_str())
            .ok_or(Error::Invalid)?;
        ensure(name.starts_with('.') && name.ends_with(".stage"))?;
        Ok(self.stage.to_owned())
    }
    fn create(&mut self, path: &Path, _: &Policy<Error>) -> Result<File> {
        self.io.create(path)
    }
    fn identity(&mut self, file: &File) -> Result<ObjectIdentity> {
        self.io.identity(file)
    }
    fn flush(&mut self, file: &File, _: &Policy<Error>) -> Result<()> {
        self.io.flush(file)
    }
    fn publish(
        &mut self,
        file: File,
        stage: &Stage<'_, ObjectIdentity>,
        path: &Path,
        _: Option<&[u8]>,
        policy: &Policy<Error>,
    ) -> Result<()> {
        drop(file);
        let (_, identity) = self
            .io
            .observe(stage.path, policy.limit)?
            .ok_or(Error::Conflict)?;
        ensure(Some(&identity) == stage.identity).map_err(|_| Error::Conflict)?;
        self.io.move_file(stage.path, path, WRITE_THROUGH)
    }
    fn readback(
        &mut self,
        path: &Path,
        bytes: &[u8],
        identity: &ObjectIdentity,
        limit: usize,
    ) -> Result<()> {
        let (actual, id) = self.io.observe(path, limit)?.ok_or(Error::Conflict)?;
        if actual != bytes || &id != identity {
            return Err(Error::Conflict);
        }
        Ok(())
    }
    fn cleanup(&mut self, stage: &Stage<'_, ObjectIdentity>) -> Result<()> {
        let Some(id) = stage.identity else {
            return Ok(());
        };
        if let Some((_, current)) = self
            .io
            .observe(stage.path, 32 * 1024 * 1024)
            .map_err(|_| Error::Unavailable)?
        {
            if &current != id {
                return Err(Error::Unavailable);
            }
            self.io.remove(stage.path)?;
        }
        Ok(())
    }
}
pub(super) fn publish_with<I: Io>(
    io: &I,
    path: &Path,
    stage: &Path,
    bytes: &[u8],
    limit: usize,
    verify: &mut dyn FnMut() -> Result<()>,
    observer: &mut Observer<'_, ObjectIdentity, Error>,
) -> Result<()> {
    ensure((1..=32 * 1024 * 1024).contains(&limit))?;
    compare_replace::replace_with(
        path,
        None,
        Some(bytes),
        Policy {
            limit,
            too_large: Error::Invalid,
            invalid_path: Error::Invalid,
            changed: Error::Conflict,
            unavailable: Error::Unavailable,
            prefix: "",
            create_only: true,
            skip_equal: false,
            sync_directory: false,
            private: true,
        },
        || io.observe(path, limit).map(|o| o.map(|(bytes, _)| bytes)),
        Some(verify),
        (&mut NoReplace { stage, io }, observer),
    )
}
/// Lexical local-drive conversion, never canonicalization of protected objects.
/// Reject ambiguous names; all subsequent opens, moves and readbacks use this spelling.
pub(super) fn extended_name(text: &str) -> Result<String> {
    let normalized = text.replace('/', "\\");
    let local = normalized.strip_prefix(r"\\?\").unwrap_or(&normalized);
    let b = local.as_bytes();
    ensure(b.len() >= 3 && b[0].is_ascii_alphabetic() && &b[1..3] == b":\\")?;
    ensure(local.encode_utf16().count() + 5 <= 32767)?;
    for name in local[3..].split('\\') {
        ensure(
            !name.is_empty()
                && name != "."
                && name != ".."
                && !name.contains([':', '\0', '"', '<', '>', '|', '?', '*'])
                && !name.ends_with(['.', ' ']),
        )?;
    }
    Ok(format!(r"\\?\{local}"))
}
#[cfg(windows)]
/// `stage` is the caller's fixed authorized slot, never a request-supplied name.
pub fn publish(
    path: &Path,
    stage: &Path,
    bytes: &[u8],
    limit: usize,
    verify: &mut dyn FnMut() -> Result<()>,
) -> Result<()> {
    let path = PathBuf::from(extended_name(path.to_str().ok_or(Error::Invalid)?)?);
    let stage = PathBuf::from(extended_name(stage.to_str().ok_or(Error::Invalid)?)?);
    let io = WindowsIo::new(path.parent().ok_or(Error::Invalid)?)?;
    publish_with(&io, &path, &stage, bytes, limit, verify, &mut |_| Ok(()))
}
#[cfg(windows)]
pub(super) struct WindowsIo {
    parent: PathBuf,
    directories: RefCell<BTreeMap<PathBuf, (Rc<File>, ObjectIdentity)>>,
}
#[cfg(windows)]
impl WindowsIo {
    pub(super) fn new(parent: &Path) -> Result<Self> {
        let io = Self {
            parent: parent.into(),
            directories: RefCell::default(),
        };
        ensure(
            io.open(parent)?
                .is_some_and(|f| f.metadata().is_ok_and(|m| m.is_dir())),
        )?;
        Ok(io)
    }
    fn open(&self, path: &Path) -> Result<Option<Rc<File>>> {
        use crate::publication_state::{checked_handle, file_identity};
        walk_checked(
            path,
            &mut self.directories.borrow_mut(),
            UNPROVEN,
            |p, child| {
                let Some(m) = metadata(p)? else {
                    return Ok(None);
                };
                let absent = child
                    .map(|c| metadata(c).map(|m| m.is_none()))
                    .transpose()?
                    .unwrap_or(false);
                let f = checked_handle(p, p.starts_with(&self.parent), absent)?;
                let identity = file_identity(&f)?;
                Ok(Some(WalkObject::new(m, Rc::new(f), identity)))
            },
        )
        .map(|o| o.map(|o| o.handle))
        .map_err(|_| Error::Invalid)
    }
}
#[cfg(windows)]
impl Io for WindowsIo {
    fn create(&self, path: &Path) -> Result<File> {
        ensure(self.open(path)?.is_none())?;
        crate::publication_state::create_new_file(path).map_err(|_| Error::Unavailable)
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        crate::publication_state::file_identity(file).map_err(|_| Error::Unavailable)
    }
    fn observe(&self, path: &Path, bound: usize) -> Result<Option<(Vec<u8>, ObjectIdentity)>> {
        let Some(file) = self.open(path)? else {
            return Ok(None);
        };
        ensure(file.metadata().map_err(|_| Error::Unavailable)?.len() <= bound as u64)?;
        let identity = self.identity(&file)?;
        let bytes = read_bounded(&file, bound).map_err(|_| Error::Invalid)?;
        let current = self.open(path)?.ok_or(Error::Conflict)?;
        if self.identity(&current)? != identity {
            return Err(Error::Conflict);
        }
        Ok(Some((bytes, identity)))
    }
    fn flush(&self, file: &File) -> Result<()> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::FlushFileBuffers;
        if unsafe { FlushFileBuffers(file.as_raw_handle()) } == 0 {
            return Err(Error::Unavailable);
        }
        Ok(())
    }
    fn move_file(&self, stage: &Path, path: &Path, flags: u32) -> Result<()> {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::{
            Foundation::{ERROR_ALREADY_EXISTS, ERROR_FILE_EXISTS},
            Storage::FileSystem::{MoveFileExW, MOVEFILE_WRITE_THROUGH},
        };
        debug_assert_eq!(WRITE_THROUGH, MOVEFILE_WRITE_THROUGH);
        let wide = |p: &Path| {
            p.as_os_str()
                .encode_wide()
                .chain(Some(0))
                .collect::<Vec<_>>()
        };
        if unsafe { MoveFileExW(wide(stage).as_ptr(), wide(path).as_ptr(), flags) } == 0 {
            let error = std::io::Error::last_os_error().raw_os_error();
            if [
                Some(ERROR_FILE_EXISTS as i32),
                Some(ERROR_ALREADY_EXISTS as i32),
            ]
            .contains(&error)
                || self.open(path)?.is_some()
            {
                return Err(Error::Conflict);
            }
            return Err(Error::Unavailable);
        }
        Ok(())
    }
    fn remove(&self, path: &Path) -> Result<()> {
        std::fs::remove_file(path).map_err(|_| Error::Unavailable)
    }
}
