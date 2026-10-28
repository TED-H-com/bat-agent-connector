//! Best-effort bounded diagnostics: codes only, never process arguments or inventory.
use std::{
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};
static LOG: OnceLock<Mutex<PathBuf>> = OnceLock::new();
const LIMIT: u64 = 1024 * 1024;
pub fn initialize(directory: &Path) {
    LOG.get_or_init(|| Mutex::new(directory.join("fleet-monitor.log")));
}
fn append(path: &Path, code: &str) -> std::io::Result<()> {
    if !code
        .bytes()
        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
        || code.len() > 128
    {
        return Ok(());
    }
    if let Ok(metadata) = path.symlink_metadata() {
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return Ok(());
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Ok(());
            }
        }
        if metadata.len() >= LIMIT {
            let backup = path.with_extension("log.1");
            match std::fs::remove_file(&backup) {
                Ok(()) => (),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                Err(error) => return Err(error),
            }
            std::fs::rename(path, backup)?;
        }
    }
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut file = options.open(path)?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    writeln!(file, "{now} {code}")
}
pub fn event(code: &str) {
    if let Some(log) = LOG.get() {
        if let Ok(path) = log.lock() {
            let _ = append(&path, code);
        }
    }
}
/// Transient evidence/read failures must not release the monitor mutex or orphan its children.
pub fn iteration(result: crate::Result<()>) -> crate::Result<()> {
    match result {
        Err(error) => {
            event(error);
            match error {
                "INSTALLATION_CHANGED" | "MONITOR_ALREADY_RUNNING" => Err(error),
                _ => Ok(()),
            }
        }
        Ok(()) => Ok(()),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn iteration_errors_continue_until_a_real_fence() {
        for error in [
            "OWNER_UNPROVEN",
            "OTHER_LOGIN_OWNER",
            "MONITOR_EPOCH_CHANGED",
            "CLOCK_UNPROVEN",
            "SUPERVISOR_FILE_UNPROVEN",
        ] {
            assert_eq!(iteration(Err(error)), Ok(()));
            assert_eq!(iteration(Ok(())), Ok(()));
        }
        assert_eq!(
            iteration(Err("INSTALLATION_CHANGED")),
            Err("INSTALLATION_CHANGED")
        );
    }
    #[test]
    fn log_rotates_and_accepts_only_codes() {
        let dir = std::env::temp_dir().join(format!("fleet-log-{:032x}", rand::random::<u128>()));
        std::fs::create_dir(&dir).unwrap();
        let path = dir.join("fleet-monitor.log");
        std::fs::write(&path, vec![b'x'; LIMIT as usize]).unwrap();
        append(&path, "OWNER_UNPROVEN").unwrap();
        append(&path, "secret command arguments").unwrap();
        assert_eq!(
            std::fs::metadata(path.with_extension("log.1"))
                .unwrap()
                .len(),
            LIMIT
        );
        let text = std::fs::read_to_string(path).unwrap();
        assert!(text.ends_with(" OWNER_UNPROVEN\n"));
        assert!(!text.contains("secret"));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
