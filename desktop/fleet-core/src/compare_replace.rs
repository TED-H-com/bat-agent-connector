//! One bounded compare/stage/recheck/publish primitive. Callers retain their read
//! policy, guards and error vocabulary. A rename is not a filesystem CAS against
//! non-cooperating editors; the installation/owner guard excludes other writers.
use std::{
    fs::{File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

pub(crate) struct Policy<E> {
    pub limit: usize,
    pub too_large: E,
    pub invalid_path: E,
    pub changed: E,
    pub unavailable: E,
    pub prefix: &'static str,
    pub create_only: bool,
    pub skip_equal: bool,
    pub sync_directory: bool,
    pub private: bool,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Phase {
    Planned,
    Created,
    Flushed,
    Published,
}
pub(crate) struct Stage<'a, I> {
    pub path: &'a Path,
    pub identity: Option<&'a I>,
    pub phase: Phase,
}
pub(crate) type Observer<'a, I, E> = dyn FnMut(&Stage<'_, I>) -> Result<(), E> + 'a;
/// Publication mechanics only; this grants no path or creation authority.
pub(crate) trait Strategy<E> {
    type Identity: Clone + PartialEq;
    const READBACK: bool = false;
    fn name(&mut self, path: &Path, policy: &Policy<E>) -> Result<PathBuf, E>;
    fn create(&mut self, path: &Path, policy: &Policy<E>) -> Result<File, E>;
    fn identity(&mut self, file: &File) -> Result<Self::Identity, E>;
    fn flush(&mut self, file: &File, policy: &Policy<E>) -> Result<(), E>;
    fn publish(
        &mut self,
        file: File,
        stage: &Stage<'_, Self::Identity>,
        path: &Path,
        expected: Option<&[u8]>,
        policy: &Policy<E>,
    ) -> Result<(), E>;
    fn readback(
        &mut self,
        path: &Path,
        bytes: &[u8],
        identity: &Self::Identity,
        limit: usize,
    ) -> Result<(), E>;
    fn cleanup(&mut self, stage: &Stage<'_, Self::Identity>) -> Result<(), E>;
}
pub(crate) fn replace<E: Clone>(
    path: &Path,
    expected: Option<&[u8]>,
    next: Option<&[u8]>,
    policy: Policy<E>,
    read: impl FnMut() -> Result<Option<Vec<u8>>, E>,
    verify: Option<&mut dyn FnMut() -> Result<(), E>>,
) -> Result<(), E> {
    replace_observed(path, expected, next, policy, read, verify, &mut |_| {})
}
/// Original name-only observer remains at the pre-create boundary.
pub(crate) fn replace_observed<E: Clone>(
    path: &Path,
    expected: Option<&[u8]>,
    next: Option<&[u8]>,
    policy: Policy<E>,
    read: impl FnMut() -> Result<Option<Vec<u8>>, E>,
    verify: Option<&mut dyn FnMut() -> Result<(), E>>,
    staging: &mut dyn FnMut(&Path),
) -> Result<(), E> {
    replace_with(
        path,
        expected,
        next,
        policy,
        read,
        verify,
        (&mut Legacy, &mut |s| {
            if s.phase == Phase::Planned {
                debug_assert!(s.identity.is_none());
                staging(s.path);
            }
            Ok(())
        }),
    )
}
/// The same write/flush/fence flow serves legacy and checked no-replace modes.
/// Observers can identify only the live stage, never adopt an older stage.
pub(crate) fn replace_with<E: Clone, S: Strategy<E>>(
    path: &Path,
    expected: Option<&[u8]>,
    next: Option<&[u8]>,
    policy: Policy<E>,
    mut read: impl FnMut() -> Result<Option<Vec<u8>>, E>,
    mut verify: Option<&mut dyn FnMut() -> Result<(), E>>,
    (strategy, observer): (&mut S, &mut Observer<'_, S::Identity, E>),
) -> Result<(), E> {
    if next.is_some_and(|bytes| bytes.len() > policy.limit) {
        return Err(policy.too_large);
    }
    if let Some(verify) = &mut verify {
        verify()?;
    }
    if read()?.as_deref() != expected {
        return Err(policy.changed);
    }
    if policy.skip_equal && next == expected {
        return Ok(());
    }
    let parent = path.parent().ok_or_else(|| policy.invalid_path.clone())?;
    let Some(next) = next else {
        std::fs::remove_file(path).map_err(|_| policy.unavailable.clone())?;
        return Ok(());
    };
    let temporary = strategy.name(path, &policy)?;
    observer(&Stage {
        path: &temporary,
        identity: None,
        phase: Phase::Planned,
    })?;
    let mut file = strategy.create(&temporary, &policy)?;
    let mut identity = None;
    let result = (|| {
        identity = Some(strategy.identity(&file)?);
        let stage = |phase| Stage {
            path: temporary.as_path(),
            identity: identity.as_ref(),
            phase,
        };
        observer(&stage(Phase::Created))?;
        file.write_all(next)
            .map_err(|_| policy.unavailable.clone())?;
        strategy.flush(&file, &policy)?;
        observer(&stage(Phase::Flushed))?;
        if let Some(verify) = &mut verify {
            verify()?;
        }
        if read()?.as_deref() != expected {
            return Err(policy.changed.clone());
        }
        strategy.publish(file, &stage(Phase::Flushed), path, expected, &policy)?;
        observer(&stage(Phase::Published))?;
        if S::READBACK {
            if let Some(verify) = &mut verify {
                verify()?;
            }
            strategy.readback(path, next, identity.as_ref().unwrap(), policy.limit)?;
            if let Some(verify) = &mut verify {
                verify()?;
            }
        }
        sync_directory(parent, &policy)
    })();
    if S::READBACK {
        if let Some(verify) = &mut verify {
            verify()?;
        }
    }
    strategy.cleanup(&Stage {
        path: &temporary,
        identity: identity.as_ref(),
        phase: Phase::Published,
    })?;
    result
}
struct Legacy;
impl<E: Clone> Strategy<E> for Legacy {
    type Identity = ();
    fn name(&mut self, path: &Path, policy: &Policy<E>) -> Result<PathBuf, E> {
        Ok(path
            .parent()
            .ok_or_else(|| policy.invalid_path.clone())?
            .join(format!(
                "{}-{:032x}.tmp",
                policy.prefix,
                rand::random::<u128>()
            )))
    }
    fn create(&mut self, path: &Path, policy: &Policy<E>) -> Result<File, E> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        #[cfg(windows)]
        if policy.private {
            return crate::publication_state::create_file(path)
                .map_err(|_| policy.unavailable.clone());
        }
        let _ = policy.private;
        options.open(path).map_err(|_| policy.unavailable.clone())
    }
    fn identity(&mut self, _: &File) -> Result<(), E> {
        Ok(())
    }
    fn flush(&mut self, file: &File, policy: &Policy<E>) -> Result<(), E> {
        file.sync_all().map_err(|_| policy.unavailable.clone())
    }
    fn publish(
        &mut self,
        file: File,
        stage: &Stage<'_, ()>,
        path: &Path,
        expected: Option<&[u8]>,
        policy: &Policy<E>,
    ) -> Result<(), E> {
        drop(file);
        if expected.is_none() && policy.create_only {
            std::fs::hard_link(stage.path, path).map_err(|_| policy.changed.clone())?;
        } else {
            std::fs::rename(stage.path, path).map_err(|_| policy.unavailable.clone())?;
        }
        Ok(())
    }
    fn readback(&mut self, _: &Path, _: &[u8], _: &(), _: usize) -> Result<(), E> {
        Ok(())
    }
    fn cleanup(&mut self, stage: &Stage<'_, ()>) -> Result<(), E> {
        let _ = std::fs::remove_file(stage.path);
        Ok(())
    }
}
fn sync_directory<E: Clone>(parent: &Path, policy: &Policy<E>) -> Result<(), E> {
    #[cfg(unix)]
    if policy.sync_directory {
        std::fs::File::open(parent)
            .and_then(|file| file.sync_all())
            .map_err(|_| policy.unavailable.clone())?;
    }
    #[cfg(not(unix))]
    let _ = (parent, policy.sync_directory);
    Ok(())
}
