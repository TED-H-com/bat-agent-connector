//! Fixed native profile files; no path in this module comes from IPC.
use crate::Result;
use std::{fs::OpenOptions, io::Read, path::Path};
pub(crate) const LIMIT: usize = 1024 * 1024;
fn plain(metadata: &std::fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return false;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return false;
        }
    }
    true
}
pub(crate) fn directory(path: &Path) -> Result<()> {
    let meta = path
        .symlink_metadata()
        .map_err(|_| "PROFILE_INDEX_UNAVAILABLE")?;
    if !meta.is_dir() || !plain(&meta) {
        return Err("PROFILE_INDEX_UNPROVEN");
    }
    Ok(())
}
pub(crate) fn read(path: &Path) -> Result<Vec<u8>> {
    directory(path.parent().ok_or("PROFILE_INDEX_UNPROVEN")?)?;
    let meta = path
        .symlink_metadata()
        .map_err(|_| "PROFILE_INDEX_UNAVAILABLE")?;
    if !meta.is_file() || !plain(&meta) || meta.len() > LIMIT as u64 {
        return Err("PROFILE_INDEX_UNPROVEN");
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x0020_0000);
    }
    let file = options
        .open(path)
        .map_err(|_| "PROFILE_INDEX_UNAVAILABLE")?;
    let meta = file.metadata().map_err(|_| "PROFILE_INDEX_UNPROVEN")?;
    if !meta.is_file() || !plain(&meta) || meta.len() > LIMIT as u64 {
        return Err("PROFILE_INDEX_UNPROVEN");
    }
    let mut bytes = Vec::new();
    file.take(LIMIT as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "PROFILE_INDEX_UNAVAILABLE")?;
    if bytes.len() > LIMIT {
        return Err("PROFILE_INDEX_UNPROVEN");
    }
    Ok(bytes)
}
pub(crate) fn replace(
    path: &Path,
    expected: &[u8],
    bytes: &[u8],
    mut verify: impl FnMut() -> Result<()>,
) -> Result<()> {
    if bytes.len() > LIMIT {
        return Err("PROFILE_INDEX_UNPROVEN");
    }
    path.parent().ok_or("PROFILE_INDEX_UNPROVEN")?;
    crate::compare_replace::replace(
        path,
        Some(expected),
        Some(bytes),
        crate::compare_replace::Policy {
            limit: LIMIT,
            too_large: "PROFILE_INDEX_UNPROVEN",
            invalid_path: "PROFILE_INDEX_UNPROVEN",
            changed: "PROFILE_INDEX_CHANGED",
            unavailable: "PROFILE_INDEX_UNAVAILABLE",
            prefix: "fleet-profile",
            create_only: false,
            skip_equal: false,
            sync_directory: false,
            private: false,
        },
        || read(path).map(Some),
        Some(&mut verify),
    )
}
