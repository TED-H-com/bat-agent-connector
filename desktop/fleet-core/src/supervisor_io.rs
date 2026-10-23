//! Fixed supervisor files. Native paths only; no IPC or process effects.
use crate::{
    configuration::data_directory,
    discovery::{Backend, MonitorRecord, Observation},
    ownership::{origin_state, ProcessState},
    process_adapter::{MonitorPointer, ProcessSnapshot, TunnelRecord},
    strict_json, Result,
};
use serde::{Deserialize, Serialize};
use std::{
    fs::OpenOptions,
    io::Read,
    path::{Path, PathBuf},
};

mod observation;
pub(crate) use observation::{classify_intent, stop_intent_bytes};
pub use observation::{observe_tunnels, TunnelEntry, TunnelKind, TunnelObservation, TunnelState};

pub(crate) const BOUND: usize = 262144;
const ENTRY_BOUND: usize = 2000;
pub(crate) fn directories(roaming: &Path) -> Result<[PathBuf; 2]> {
    crate::configuration::data_directories(roaming)
}
fn regular(metadata: &std::fs::Metadata) -> bool {
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
pub(crate) fn read(path: &Path) -> Result<Option<Vec<u8>>> {
    match path.symlink_metadata() {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Ok(m) if m.is_file() && regular(&m) && m.len() <= BOUND as u64 => (),
        _ => return Err("SUPERVISOR_FILE_UNPROVEN"),
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
    let file = options.open(path).map_err(|_| "SUPERVISOR_FILE_UNPROVEN")?;
    let m = file.metadata().map_err(|_| "SUPERVISOR_FILE_UNPROVEN")?;
    if !m.is_file() || !regular(&m) || m.len() > BOUND as u64 {
        return Err("SUPERVISOR_FILE_UNPROVEN");
    }
    let mut bytes = Vec::new();
    file.take(BOUND as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "SUPERVISOR_FILE_UNPROVEN")?;
    if bytes.len() > BOUND {
        return Err("SUPERVISOR_FILE_UNPROVEN");
    }
    Ok(Some(bytes))
}
fn directory(path: &Path) -> Result<()> {
    match path.symlink_metadata() {
        Ok(m) if m.is_dir() && regular(&m) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            std::fs::create_dir_all(path).map_err(|_| "SUPERVISOR_FILE_UNPROVEN")
        }
        _ => Err("SUPERVISOR_FILE_UNPROVEN"),
    }
}
pub(crate) fn replace(
    path: &Path,
    expected: Option<&[u8]>,
    bytes: &[u8],
    mut verify: impl FnMut() -> Result<()>,
) -> Result<()> {
    if bytes.len() > BOUND {
        return Err("STATUS_TOO_LARGE");
    }
    directory(path.parent().ok_or("SUPERVISOR_FILE_UNPROVEN")?)?;
    crate::compare_replace::replace(
        path,
        expected,
        Some(bytes),
        crate::compare_replace::Policy {
            limit: BOUND,
            too_large: "STATUS_TOO_LARGE",
            invalid_path: "SUPERVISOR_FILE_UNPROVEN",
            changed: "SUPERVISOR_FILE_CHANGED",
            unavailable: "SUPERVISOR_FILE_UNPROVEN",
            prefix: "fleet-stage",
            create_only: true,
            skip_equal: false,
            sync_directory: false,
            private: false,
        },
        || read(path),
        Some(&mut verify),
    )
}
pub(crate) fn remove(path: &Path, expected: &[u8]) -> Result<()> {
    if read(path)?.as_deref() != Some(expected) {
        return Err("SUPERVISOR_FILE_CHANGED");
    }
    std::fs::remove_file(path).map_err(|_| "SUPERVISOR_FILE_UNPROVEN")
}

/// Private filesystem ownership lease; it does not replace the OS account mutex.
pub struct MonitorLease {
    pub(crate) directory: PathBuf,
    pub(crate) record: MonitorRecord,
    bytes: Vec<u8>,
    previous: Vec<(PathBuf, Option<Vec<u8>>)>,
    status: Option<Vec<u8>>,
}
fn matches_record(record: &MonitorRecord, process: &ProcessSnapshot) -> bool {
    process.pid == record.pid
        && process.executable == record.executable
        && Some(&process.arguments) == record.arguments.as_ref()
        && Some(process.created_filetime.to_string()) == record.created_filetime
        && process.login.owner_sid == record.owner_sid
        && process.login.session_id == record.session_id
}
impl MonitorLease {
    /// Caller holds the shared account mutex and proves monitor absence before and
    /// during publication. Previous records are retained for legacy tunnel parent evidence.
    pub fn publish(
        roaming: &Path,
        record: MonitorRecord,
        observation: &impl Observation,
        mut prove_absence: impl FnMut() -> Result<()>,
    ) -> Result<Self> {
        let bytes = serde_json::to_vec(&record).map_err(|_| "OWNER_UNPROVEN")?;
        let record = MonitorRecord::parse(&bytes)?;
        if record.backend != Backend::Rust {
            return Err("OWNER_UNPROVEN");
        }
        let proposed = observation.observe(record.pid)?.ok_or("OWNER_UNPROVEN")?;
        if !matches_record(&record, &proposed) || observation.current_login()? != proposed.login {
            return Err("OWNER_UNPROVEN");
        }
        prove_absence()?;
        let mut previous = Vec::new();
        for dir in directories(roaming)? {
            match dir.symlink_metadata() {
                Ok(metadata) if metadata.is_dir() && regular(&metadata) => (),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                _ => return Err("OWNER_UNPROVEN"),
            }
            let old = read(&dir.join("fleet-monitor.json"))?;
            if let Some(raw) = &old {
                let prior = MonitorRecord::parse(raw)?;
                if origin_state(prior.pid, &prior.created, &observation.state(prior.pid))
                    != ProcessState::Dead
                {
                    return Err("OWNER_UNPROVEN");
                }
            }
            previous.push((dir, old));
        }
        let directory = data_directory(roaming)?;
        let expected = previous
            .iter()
            .find(|(p, _)| p == &directory)
            .and_then(|(_, raw)| raw.as_deref());
        let validate = || {
            prove_absence()?;
            if observation.current_login()? != proposed.login
                || observation.observe(record.pid)?.as_ref() != Some(&proposed)
            {
                return Err("OWNER_UNPROVEN");
            }
            if data_directory(roaming)? != directory {
                return Err("DATA_DIRECTORY_CHANGED");
            }
            for (dir, raw) in &previous {
                if read(&dir.join("fleet-monitor.json"))? != *raw {
                    return Err("OWNER_UNPROVEN");
                }
            }
            Ok(())
        };
        replace(
            &directory.join("fleet-monitor.json"),
            expected,
            &bytes,
            validate,
        )?;
        let status = read(&directory.join("fleet-desktop-status.json"))?;
        Ok(Self {
            directory,
            record,
            bytes,
            previous,
            status,
        })
    }
    pub fn epoch(&self) -> &str {
        &self.record.instance
    }
    pub fn directory(&self) -> &Path {
        &self.directory
    }
    pub fn process_matches(&self, process: &ProcessSnapshot) -> bool {
        matches_record(&self.record, process)
    }
    pub fn verify(&self, observation: &impl Observation) -> Result<()> {
        if read(&self.directory.join("fleet-monitor.json"))?.as_deref() != Some(&self.bytes)
            || observation.current_login()?.owner_sid != self.record.owner_sid
            || observation.current_login()?.session_id != self.record.session_id
            || !observation
                .observe(self.record.pid)?
                .as_ref()
                .is_some_and(|p| self.process_matches(p))
        {
            return Err("OWNER_UNPROVEN");
        }
        Ok(())
    }
    pub(crate) fn previous_pointer(&self, directory: &Path) -> Result<Option<MonitorPointer>> {
        self.previous
            .iter()
            .find(|(p, _)| p == directory)
            .and_then(|(_, r)| r.as_deref())
            .map(|bytes| {
                MonitorRecord::parse(bytes).map(|record| MonitorPointer {
                    pid: record.pid,
                    created: record.created,
                    instance: record.instance,
                })
            })
            .transpose()
    }
    pub(crate) fn publish_status(
        &mut self,
        bytes: Vec<u8>,
        observation: &impl Observation,
        mut verify: impl FnMut() -> Result<()>,
    ) -> Result<()> {
        replace(
            &self.directory.join("fleet-desktop-status.json"),
            self.status.as_deref(),
            &bytes,
            || {
                self.verify(observation)?;
                verify()
            },
        )?;
        self.status = Some(bytes);
        Ok(())
    }
    pub(crate) fn finish(&self) -> Result<()> {
        remove(&self.directory.join("fleet-monitor.json"), &self.bytes)
    }
}

pub(crate) struct TunnelFile {
    pub name: String,
    pub path: PathBuf,
    pub bytes: Vec<u8>,
    pub record: Result<TunnelRecord>,
}
pub(crate) struct TunnelFiles {
    pub owners: Vec<TunnelFile>,
    pub blocked: Vec<String>,
}
fn name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}
/// No process is touched here. An intent without an exact completed owner receipt
/// stays blocked even when its parent is dead: its unknown child must never be replayed.
pub(crate) fn scan(roaming: &Path, lease: &MonitorLease) -> Result<TunnelFiles> {
    scan_with(roaming, |dir| lease.previous_pointer(dir), |_, _, _| {})
}
fn scan_with(
    roaming: &Path,
    mut pointer: impl FnMut(&Path) -> Result<Option<MonitorPointer>>,
    mut candidate: impl FnMut(&Path, &str, Option<&str>),
) -> Result<TunnelFiles> {
    let mut out = TunnelFiles {
        owners: Vec::new(),
        blocked: Vec::new(),
    };
    for dir in directories(roaming)? {
        let pointer = pointer(&dir)?;
        for (child, intent) in [
            ("fleet-tunnel-owners", false),
            ("fleet-tunnel-intents", true),
            ("fleet-tunnel-stop-intents", true),
        ] {
            let directory = dir.join(child);
            let entries = match std::fs::read_dir(&directory) {
                Ok(entries) => entries,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
                Err(_) => return Err("TUNNEL_RECORD_UNAVAILABLE"),
            };
            if !regular(
                &directory
                    .symlink_metadata()
                    .map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?,
            ) {
                return Err("TUNNEL_RECORD_UNAVAILABLE");
            }
            for (count, entry) in entries.enumerate() {
                if count >= ENTRY_BOUND {
                    return Err("TUNNEL_RECORD_UNAVAILABLE");
                }
                let path = entry.map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?.path();
                let id = path.file_stem().and_then(|s| s.to_str()).filter(|id| {
                    name(id) && path.extension().and_then(|s| s.to_str()) == Some("json")
                });
                candidate(&path, child, id);
                let Some(id) = id.map(str::to_owned) else {
                    continue;
                };
                if intent {
                    out.blocked.push(id);
                    continue;
                }
                match read(&path) {
                    Ok(Some(bytes)) => {
                        let record = TunnelRecord::parse(&bytes, pointer.as_ref());
                        out.owners.push(TunnelFile {
                            name: id,
                            path,
                            bytes,
                            record,
                        });
                    }
                    _ => out.blocked.push(id),
                }
            }
        }
    }
    out.owners.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(out)
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Quit {
    pid: u32,
    instance: String,
}
pub(crate) fn quit_bytes(path: &Path, lease: &MonitorLease) -> Result<Option<Vec<u8>>> {
    let Some(bytes) = read(path)? else {
        return Ok(None);
    };
    let parsed = strict_json::parse(&bytes, BOUND)
        .and_then(|doc| serde_json::from_value::<Quit>(doc).map_err(|_| "QUIT_UNPROVEN"));
    Ok(parsed
        .ok()
        .filter(|request| request.pid == lease.record.pid && request.instance == lease.epoch())
        .map(|_| bytes))
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Temp(PathBuf);
    impl Temp {
        fn new() -> Self {
            let path = std::env::temp_dir()
                .join(format!("bac-supervisor-io-{:032x}", rand::random::<u128>()));
            std::fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    fn staged_replacement_preserves_intervening_editor_bytes_and_leaves_no_temp() {
        let temp = Temp::new();
        let path = temp.0.join("fleet-monitor.json");
        std::fs::write(&path, b"original").unwrap();
        let mut checks = 0;
        let result = replace(&path, Some(b"original"), b"replacement", || {
            checks += 1;
            if checks == 2 {
                std::fs::write(&path, b"edited while staging").unwrap();
            }
            Ok(())
        });
        assert_eq!(result, Err("SUPERVISOR_FILE_CHANGED"));
        assert_eq!(std::fs::read(&path).unwrap(), b"edited while staging");
        assert_eq!(std::fs::read_dir(&temp.0).unwrap().count(), 1);
    }
    #[test]
    fn missing_file_publication_never_overwrites_a_racing_foreign_record() {
        let temp = Temp::new();
        let path = temp.0.join("fleet-monitor.json");
        let mut checks = 0;
        let result = replace(&path, None, b"replacement", || {
            checks += 1;
            if checks == 2 {
                std::fs::write(&path, b"foreign record").unwrap();
            }
            Ok(())
        });
        assert_eq!(result, Err("SUPERVISOR_FILE_CHANGED"));
        assert_eq!(std::fs::read(&path).unwrap(), b"foreign record");
    }
    #[test]
    fn oversized_and_nonregular_records_refuse_without_replacement() {
        let temp = Temp::new();
        let path = temp.0.join("record.json");
        std::fs::write(&path, vec![b'x'; BOUND + 1]).unwrap();
        assert!(read(&path).is_err());
        assert!(replace(&path, None, b"new", || Ok(())).is_err());
        assert_eq!(std::fs::metadata(&path).unwrap().len(), (BOUND + 1) as u64);
        let directory = temp.0.join("directory.json");
        std::fs::create_dir(&directory).unwrap();
        assert!(read(&directory).is_err());
    }
    #[cfg(unix)]
    #[test]
    fn final_symlink_record_is_not_followed_or_removed() {
        use std::os::unix::fs::symlink;
        let temp = Temp::new();
        let original = temp.0.join("human.txt");
        std::fs::write(&original, b"human").unwrap();
        let link = temp.0.join("fleet-monitor.json");
        symlink(&original, &link).unwrap();
        assert!(read(&link).is_err());
        assert!(remove(&link, b"human").is_err());
        assert!(replace(&link, Some(b"human"), b"changed", || Ok(())).is_err());
        assert_eq!(std::fs::read(original).unwrap(), b"human");
        assert!(link.symlink_metadata().unwrap().file_type().is_symlink());
    }
}

#[cfg(test)]
#[path = "supervisor_io/observation_tests.rs"]
mod observation_tests;
