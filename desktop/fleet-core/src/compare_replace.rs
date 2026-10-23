//! One bounded compare/stage/recheck/publish primitive. Callers retain their read
//! policy, guards and error vocabulary. A rename is not a filesystem CAS against
//! non-cooperating editors; the installation/owner guard excludes other writers.
use std::{fs::OpenOptions, io::Write, path::Path};

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

/// Report the exact staging name to a guarded namespace reader. Only this live
/// staging file may be excluded from its immutable-record snapshot.
pub(crate) fn replace_observed<E: Clone>(
    path: &Path,
    expected: Option<&[u8]>,
    next: Option<&[u8]>,
    policy: Policy<E>,
    mut read: impl FnMut() -> Result<Option<Vec<u8>>, E>,
    mut verify: Option<&mut dyn FnMut() -> Result<(), E>>,
    staging: &mut dyn FnMut(&Path),
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
    let temporary = parent.join(format!(
        "{}-{:032x}.tmp",
        policy.prefix,
        rand::random::<u128>()
    ));
    staging(&temporary);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    #[cfg(windows)]
    let staged = if policy.private {
        crate::publication_state::create_file(&temporary).map_err(|_| policy.unavailable.clone())
    } else {
        options
            .open(&temporary)
            .map_err(|_| policy.unavailable.clone())
    };
    #[cfg(not(windows))]
    let staged = {
        let _ = policy.private;
        options
            .open(&temporary)
            .map_err(|_| policy.unavailable.clone())
    };
    let mut file = staged?;
    let result = (|| {
        file.write_all(next)
            .and_then(|_| file.sync_all())
            .map_err(|_| policy.unavailable.clone())?;
        if let Some(verify) = &mut verify {
            verify()?;
        }
        if read()?.as_deref() != expected {
            return Err(policy.changed.clone());
        }
        drop(file);
        if expected.is_none() && policy.create_only {
            std::fs::hard_link(&temporary, path).map_err(|_| policy.changed.clone())?;
        } else {
            std::fs::rename(&temporary, path).map_err(|_| policy.unavailable.clone())?;
        }
        sync_directory(parent, &policy)
    })();
    let _ = std::fs::remove_file(temporary);
    result
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
