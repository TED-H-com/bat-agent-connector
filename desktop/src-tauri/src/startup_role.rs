//! Native installation role, separate from Connector identity and Fleet login preferences.
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

pub const UNREADABLE: &str = "MANAGED_CONFIGURATION_UNREADABLE";
const MAX_RECORD: u64 = 4096;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    Client,
    Provider,
    Both,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub role: Role,
    pub expected_generation: u64,
}
impl Role {
    pub fn provides(self) -> bool {
        matches!(self, Self::Provider | Self::Both)
    }
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct Snapshot {
    pub role: Option<Role>,
    pub generation: u64,
    pub installation_ref: Option<String>,
}
impl Snapshot {
    fn pending() -> Self {
        Self {
            role: None,
            generation: 0,
            installation_ref: None,
        }
    }
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Record {
    version: u8,
    installation_ref: String,
    generation: u64,
    role: Role,
}

pub fn record_path(data: &Path) -> PathBuf {
    data.join("startup-role/role.json")
}

fn linked(meta: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        meta.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        meta.file_type().is_symlink()
    }
}
fn metadata(path: &Path) -> Result<Option<fs::Metadata>, String> {
    match path.symlink_metadata() {
        Ok(meta) if !linked(&meta) => Ok(Some(meta)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        _ => Err(UNREADABLE.into()),
    }
}
fn check_private(meta: &fs::Metadata) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if meta.uid() != unsafe { libc::geteuid() } || meta.mode() & 0o077 != 0 {
            return Err(UNREADABLE.into());
        }
    }
    let _ = meta;
    Ok(())
}
fn check_parents(data: &Path) -> Result<(), String> {
    if let Some(meta) = metadata(data)? {
        if !meta.is_dir() {
            return Err(UNREADABLE.into());
        }
    }
    if let Some(meta) = metadata(&data.join("startup-role"))? {
        if !meta.is_dir() {
            return Err(UNREADABLE.into());
        }
        check_private(&meta)?;
        #[cfg(windows)]
        bat_fleet_core::publication_state::verify_ancestors(
            &data.join("startup-role"),
            &data.join("startup-role"),
        )
        .map_err(|_| UNREADABLE)?;
    }
    Ok(())
}
pub(crate) struct Evidence {
    pub snapshot: Snapshot,
    pub record_sha256: String,
    pub version: u8,
}
pub(crate) fn evidence(data: &Path) -> Result<Evidence, String> {
    read_evidence(data)?.ok_or_else(|| UNREADABLE.into())
}
fn read(data: &Path) -> Result<Option<Snapshot>, String> {
    Ok(read_evidence(data)?.map(|e| e.snapshot))
}
fn read_evidence(data: &Path) -> Result<Option<Evidence>, String> {
    check_parents(data)?;
    let path = record_path(data);
    let Some(meta) = metadata(&path)? else {
        return Ok(None);
    };
    if !meta.is_file() || meta.len() > MAX_RECORD {
        return Err(UNREADABLE.into());
    }
    check_private(&meta)?;
    #[cfg(windows)]
    let bytes = bat_fleet_core::publication_state::read_startup_role(&path, MAX_RECORD as usize)
        .map_err(|_| UNREADABLE)?;
    #[cfg(not(windows))]
    let bytes = {
        use std::io::Read;
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }

        let file = options.open(&path).map_err(|_| UNREADABLE)?;
        let meta = file.metadata().map_err(|_| UNREADABLE)?;
        if !meta.is_file() || linked(&meta) || meta.len() > MAX_RECORD {
            return Err(UNREADABLE.into());
        }
        check_private(&meta)?;
        let mut bytes = vec![];
        file.take(MAX_RECORD + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| UNREADABLE)?;
        bytes
    };
    let record: Record = serde_json::from_value(
        bat_fleet_core::strict_json::parse(&bytes, MAX_RECORD as usize).map_err(|_| UNREADABLE)?,
    )
    .map_err(|_| UNREADABLE)?;
    if record.version != 1
        || record.generation != 1
        || uuid::Uuid::parse_str(&record.installation_ref).is_err()
    {
        return Err(UNREADABLE.into());
    }
    Ok(Some(Evidence {
        snapshot: Snapshot {
            role: Some(record.role),
            generation: record.generation,
            installation_ref: Some(record.installation_ref),
        },
        record_sha256: bat_fleet_core::digest(&bytes),
        version: record.version,
    }))
}

/// Files are evidence for one-time adoption only. Their contents/identity stay with their owner.
pub fn external(config: &Path) -> Result<bool, String> {
    // Even invalid existing configurations stay external; never provision as a fallback.
    Ok(match config.join("central.json").symlink_metadata() {
        Ok(_) => true,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
        Err(_) => return Err(UNREADABLE.into()),
    })
}
fn inferred(data: &Path, config: &Path) -> Result<Option<Role>, String> {
    let local = match metadata(&data.join("managed-central"))? {
        Some(meta) if !meta.is_dir() => return Err(UNREADABLE.into()),
        _ => match metadata(&data.join("managed-central/installation.json"))? {
            Some(meta) if meta.is_file() => true,
            Some(_) => return Err(UNREADABLE.into()),
            None => false,
        },
    };
    Ok(match (local, external(config)?) {
        (true, true) => Some(Role::Both),
        (true, false) => Some(Role::Provider),
        (false, true) => Some(Role::Client),
        (false, false) => None,
    })
}
pub fn load_or_infer(data: &Path, config: &Path) -> Result<Snapshot, String> {
    if let Some(record) = read(data)? {
        return Ok(record);
    }
    match inferred(data, config)? {
        Some(role) => publish(data, role),
        None => Ok(Snapshot::pending()),
    }
}
pub fn select(
    data: &Path,
    config: &Path,
    role: Role,
    expected_generation: u64,
) -> Result<Snapshot, String> {
    let current = load_or_infer(data, config)?;
    if current.generation != expected_generation || current.role.is_some() {
        return Err("STARTUP_ROLE_CHANGED".into());
    }
    publish(data, role)
}
fn publish(data: &Path, role: Role) -> Result<Snapshot, String> {
    check_parents(data)?;
    fs::create_dir_all(data).map_err(|_| UNREADABLE)?;
    let directory = data.join("startup-role");
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        match fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(_) => return Err(UNREADABLE.into()),
        }
    }
    #[cfg(windows)]
    create_windows_directory(&directory)?;
    check_parents(data)?;
    let temporary = directory.join(format!(".role-{}", uuid::Uuid::new_v4()));
    let result = (|| {
        let record = Record {
            version: 1,
            installation_ref: uuid::Uuid::new_v4().to_string(),
            generation: 1,
            role,
        };
        let bytes = serde_json::to_vec(&record).map_err(|_| UNREADABLE)?;
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary).map_err(|_| UNREADABLE)?;
        file.write_all(&bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| UNREADABLE)?;
        drop(file);
        // Atomic create-only publication: a second instance cannot overwrite the first choice.
        fs::hard_link(&temporary, record_path(data)).map_err(|_| "STARTUP_ROLE_CHANGED")?;
        #[cfg(unix)]
        fs::File::open(&directory)
            .and_then(|dir| dir.sync_all())
            .map_err(|_| UNREADABLE)?;
        read(data)?.ok_or_else(|| UNREADABLE.to_string())
    })();
    let _ = fs::remove_file(temporary);
    result
}

#[cfg(windows)]
fn create_windows_directory(path: &Path) -> Result<(), String> {
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::{
        Foundation::{GetLastError, LocalFree, ERROR_ALREADY_EXISTS},
        Security::{
            Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW,
            SECURITY_ATTRIBUTES,
        },
        Storage::FileSystem::CreateDirectoryW,
    };
    // Protected DACL, full control for the owner and SYSTEM; children inherit it.
    let sddl: Vec<u16> = "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let mut descriptor = ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            1,
            &mut descriptor,
            ptr::null_mut(),
        )
    } == 0
    {
        return Err(UNREADABLE.into());
    }
    let attrs = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    let name: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let created = unsafe { CreateDirectoryW(name.as_ptr(), &attrs) };
    let error = unsafe { GetLastError() };
    unsafe {
        LocalFree(descriptor);
    }
    if created == 0 && error != ERROR_ALREADY_EXISTS {
        return Err(UNREADABLE.into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn selection_is_typed_and_cannot_supply_process_inputs() {
        for input in [
            serde_json::json!({"role": "unknown", "expected_generation": 0}),
            serde_json::json!({"role": "provider", "expected_generation": 0, "executable": "untrusted"}),
            serde_json::json!({"role": "provider", "expected_generation": 0, "environment": {}}),
            serde_json::json!({"role": "client", "expected_generation": -1}),
        ] {
            assert!(serde_json::from_value::<Selection>(input).is_err());
        }
    }

    #[test]
    fn concurrent_publication_keeps_one_choice_and_installation_reference() {
        use std::sync::Barrier;
        let root = tempfile::tempdir().unwrap();
        let data = root.path().join("data");
        let config = root.path().join("config");
        let barrier = Barrier::new(2);
        let results = std::thread::scope(|threads| {
            let workers: Vec<_> = [Role::Client, Role::Provider]
                .into_iter()
                .map(|role| {
                    let data = &data;
                    let config = &config;
                    let barrier = &barrier;
                    threads.spawn(move || {
                        barrier.wait();
                        select(data, config, role, 0)
                    })
                })
                .collect();
            workers
                .into_iter()
                .map(|worker| worker.join().unwrap())
                .collect::<Vec<_>>()
        });
        assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
        let winner = results.into_iter().find_map(Result::ok).unwrap();
        assert_eq!(load_or_infer(&data, &config).unwrap(), winner);
        assert_eq!(fs::read_dir(data.join("startup-role")).unwrap().count(), 1);
        assert!(!data.join("managed-central").exists());
    }

    #[test]
    fn clean_role_is_pending_and_selection_is_stable_and_generation_guarded() {
        for role in [Role::Client, Role::Provider, Role::Both] {
            let root = tempfile::tempdir().unwrap();
            let data = root.path().join("app-data");
            let config = root.path().join("config");
            let pending = load_or_infer(&data, &config).unwrap();
            assert_eq!(pending.role, None);
            assert_eq!(pending.generation, 0);
            assert!(!data.exists());
            let selected = select(&data, &config, role, 0).unwrap();
            assert_eq!(selected.role, Some(role));
            assert_eq!(selected.generation, 1);
            assert!(uuid::Uuid::parse_str(selected.installation_ref.as_ref().unwrap()).is_ok());
            assert_eq!(load_or_infer(&data, &config).unwrap(), selected);
            assert_eq!(
                select(&data, &config, Role::Both, 0).unwrap_err(),
                "STARTUP_ROLE_CHANGED"
            );
            assert_eq!(load_or_infer(&data, &config).unwrap(), selected);
            assert!(!data.join("managed-central").exists());
        }
    }

    #[test]
    fn existing_files_infer_once_and_are_never_rewritten() {
        for (local, external, role) in [
            (true, false, Role::Provider),
            (false, true, Role::Client),
            (true, true, Role::Both),
        ] {
            let root = tempfile::tempdir().unwrap();
            let data = root.path().join("data");
            let config = root.path().join("config");
            fs::create_dir_all(data.join("managed-central")).unwrap();
            fs::create_dir(&config).unwrap();
            let manifest = data.join("managed-central/installation.json");
            let central = config.join("central.json");
            if local {
                fs::write(&manifest, b"original manifest").unwrap();
            }
            if external {
                fs::write(&central, b"original external configuration").unwrap();
            }
            let original = load_or_infer(&data, &config).unwrap();
            assert_eq!(original.role, Some(role));
            if local {
                assert_eq!(fs::read(&manifest).unwrap(), b"original manifest");
            }
            if external {
                assert_eq!(
                    fs::read(&central).unwrap(),
                    b"original external configuration"
                );
            }
            // A later connection must not silently rewrite the installation's chosen role.
            fs::write(&central, b"later connection").unwrap();
            assert_eq!(load_or_infer(&data, &config).unwrap(), original);
        }
    }

    #[test]
    fn malformed_or_future_record_never_falls_back_to_provisioning() {
        let root = tempfile::tempdir().unwrap();
        let config = root.path().join("config");
        select(root.path(), &config, Role::Client, 0).unwrap();
        let path = record_path(root.path());
        let bytes = fs::read(&path).unwrap();
        for invalid in [
            b"null".to_vec(),
            b"{}".to_vec(),
            bytes
                .iter()
                .copied()
                .chain(b"trailing".iter().copied())
                .collect(),
        ] {
            fs::write(&path, invalid).unwrap();
            assert_eq!(load_or_infer(root.path(), &config).unwrap_err(), UNREADABLE);
        }
        let mut doc: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        doc["version"] = 2.into();
        fs::write(&path, serde_json::to_vec(&doc).unwrap()).unwrap();
        assert_eq!(load_or_infer(root.path(), &config).unwrap_err(), UNREADABLE);
    }

    #[cfg(unix)]
    #[test]
    fn symlinks_and_nonprivate_records_are_refused() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let root = tempfile::tempdir().unwrap();
        let config = root.path().join("config");
        select(root.path(), &config, Role::Client, 0).unwrap();
        let path = record_path(root.path());
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(load_or_infer(root.path(), &config).unwrap_err(), UNREADABLE);
        fs::remove_file(&path).unwrap();
        let target = root.path().join("unrelated");
        fs::write(&target, b"untouched").unwrap();
        symlink(&target, &path).unwrap();
        assert_eq!(load_or_infer(root.path(), &config).unwrap_err(), UNREADABLE);
        assert_eq!(fs::read(&target).unwrap(), b"untouched");
        fs::remove_file(&path).unwrap();
        symlink(root.path().join("missing"), &path).unwrap();
        assert_eq!(load_or_infer(root.path(), &config).unwrap_err(), UNREADABLE);
    }
}
