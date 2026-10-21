//! Installation lifecycle only. Python owns identity, locking, journal and operations.
//! Secrets travel in a bounded anonymous stdout pipe and never cross WebView IPC.
use crate::{bridge, credentials};
use flate2::read::GzDecoder;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Mutex,
    time::Duration,
};
use zeroize::Zeroizing;

const MAX_RUNTIME: u64 = 512 * 1024 * 1024;
const MAX_REPLY: u64 = 16384;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    protocol: u8,
    runtime_version: String,
    platform: String,
    architecture: String,
    executable: String,
    payload_sha256: String,
    sha256: String,
    size: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RuntimeIdentity {
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub commit: Option<String>,
    #[serde(default)]
    pub build_source: Option<String>,
    pub source: String,
    pub observed_at: f64,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeDifference {
    Same,
    Differs,
    Unknown,
}

// No Debug / Serialize: this value contains a bearer credential.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Launch {
    protocol: u8,
    pub endpoint: String,
    pub actor: String,
    pub server_id: String,
    pub principal_id: String,
    pub token: Zeroizing<String>,
    pub runtime_version: String,
    #[serde(default)]
    pub bundled_runtime: Option<RuntimeIdentity>,
    #[serde(default)]
    pub running_runtime: Option<RuntimeIdentity>,
    #[serde(default)]
    pub runtime_difference: Option<RuntimeDifference>,
}
impl Launch {
    fn validate(&self, version: &str) -> Result<(), String> {
        if self.protocol != 1 || self.runtime_version != version {
            return Err("MANAGED_RUNTIME_PROTOCOL_MISMATCH".into());
        }
        let url = self
            .config()
            .validate()
            .map_err(|_| "MANAGED_RUNTIME_ENDPOINT_INVALID")?;
        if url.scheme() != "http" || url.host_str() != Some("127.0.0.1") {
            return Err("MANAGED_RUNTIME_ENDPOINT_INVALID".into());
        }
        self.identity()
            .validate()
            .map_err(|_| "MANAGED_RUNTIME_IDENTITY_INVALID")?;
        credentials::validate_token(&self.token)
            .map_err(|_| "MANAGED_RUNTIME_CREDENTIAL_INVALID".into())
    }
    pub fn config(&self) -> bridge::Config {
        bridge::Config {
            endpoint: self.endpoint.clone(),
            expected_actor: self.actor.clone(),
            contract_version: "2026-10-08".into(),
        }
    }
    pub fn identity(&self) -> credentials::Identity {
        credentials::Identity {
            server_id: self.server_id.clone(),
            principal_id: self.principal_id.clone(),
        }
    }
}

#[derive(Clone, Serialize)]
pub struct Status {
    pub mode: &'static str,
    pub ready: bool,
    pub background: bool,
    pub runtime_version: Option<String>,
    pub server_id: Option<String>,
    pub principal_id: Option<String>,
    pub error: Option<String>,
    pub login_enabled: bool,
    pub bundled_runtime: Option<RuntimeIdentity>,
    pub running_runtime: Option<RuntimeIdentity>,
    pub runtime_difference: Option<RuntimeDifference>,
}

#[derive(Clone, Default)]
struct RuntimeFacts {
    bundled: Option<RuntimeIdentity>,
    running: Option<RuntimeIdentity>,
    difference: Option<RuntimeDifference>,
}

impl RuntimeFacts {
    fn from_launch(launch: &Launch) -> Self {
        Self {
            bundled: launch.bundled_runtime.clone(),
            running: launch.running_runtime.clone(),
            difference: Some(
                launch
                    .runtime_difference
                    .unwrap_or(RuntimeDifference::Unknown),
            ),
        }
    }

    fn apply(&self, status: &mut Status) {
        status.bundled_runtime = self.bundled.clone();
        status.running_runtime = self.running.clone();
        status.runtime_difference = self.difference;
    }
}

pub struct Managed {
    executable: Option<PathBuf>,
    data_dir: PathBuf,
    status: Status,
    pub serial: Mutex<()>,
    runtime_facts: Mutex<RuntimeFacts>,
}

impl Managed {
    pub fn prepare(resources: &Path, app_data: &Path, config: &Path) -> (Self, Option<Launch>) {
        let mut state = Self {
            executable: None,
            data_dir: app_data.join("managed-central"),
            status: Status {
                mode: "managed",
                ready: false,
                background: true,
                runtime_version: None,
                server_id: None,
                principal_id: None,
                error: None,
                login_enabled: false,
                bundled_runtime: None,
                running_runtime: None,
                runtime_difference: None,
            },
            serial: Mutex::new(()),
            runtime_facts: Mutex::new(RuntimeFacts::default()),
        };
        // An advanced central always remains the selected client binding. Only an
        // already saved installation can additionally recover its background service;
        // a remote connection failure never provisions a second journal.
        match fs::symlink_metadata(config.join("central.json")) {
            Ok(_) => {
                state.status.mode = "external";
                state.status.background = false;
                match fs::symlink_metadata(state.data_dir.join("installation.json")) {
                    Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => {
                        // Python verifies the private manifest, identity and ownership
                        // challenge before reconnecting. Its failure is never a fallback.
                        state.status.background = true;
                    }
                    _ => return (state, None),
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => {
                state.status.error = Some("MANAGED_CONFIGURATION_UNREADABLE".into());
                return (state, None);
            }
        }
        let result = (|| {
            let (executable, version) = materialize(&resources.join("managed-runtime"), app_data)?;
            state.executable = Some(executable);
            state.status.runtime_version = Some(version.clone());
            let bytes = state.request("ensure")?;
            let launch: Launch = serde_json::from_slice(&bytes)
                .map_err(|_| "MANAGED_RUNTIME_REPLY_INVALID".to_string())?;
            launch.validate(&version)?;
            state.status.ready = true;
            state.status.server_id = Some(launch.server_id.clone());
            state.status.principal_id = Some(launch.principal_id.clone());
            state.remember_runtime(&launch);
            Ok::<_, String>(launch)
        })();
        match result {
            Ok(launch) => (state, Some(launch)),
            Err(error) => {
                state.status.error = Some(error);
                (state, None)
            }
        }
    }

    pub fn recover(&self) -> Result<Launch, String> {
        if self.status.mode != "managed" || !self.status.ready {
            return Err("MANAGED_RECOVERY_UNAVAILABLE".into());
        }
        let _guard = self
            .serial
            .try_lock()
            .map_err(|_| "MANAGED_RECOVERY_BUSY")?;
        let bytes = self.request("ensure")?;
        let launch: Launch =
            serde_json::from_slice(&bytes).map_err(|_| "MANAGED_RUNTIME_REPLY_INVALID")?;
        launch.validate(
            self.status
                .runtime_version
                .as_deref()
                .ok_or("MANAGED_RUNTIME_UNAVAILABLE")?,
        )?;
        if Some(&launch.server_id) != self.status.server_id.as_ref()
            || Some(&launch.principal_id) != self.status.principal_id.as_ref()
        {
            return Err("MANAGED_RECOVERY_IDENTITY_CHANGED".into());
        }
        self.remember_runtime(&launch);
        Ok(launch)
    }

    // Identification is observational: it never changes reuse/readiness gates.
    fn remember_runtime(&self, launch: &Launch) {
        if let Ok(mut facts) = self.runtime_facts.lock() {
            *facts = RuntimeFacts::from_launch(launch);
        }
    }

    pub fn status(&self) -> Status {
        let mut status = self.status.clone();
        if let Ok(facts) = self.runtime_facts.lock() {
            facts.apply(&mut status);
        }
        match crate::managed_login::enabled() {
            Ok(enabled) => status.login_enabled = enabled,
            Err(error) => status.error = Some(error),
        }
        status
    }

    pub fn set_login(&self, enabled: bool) -> Result<Status, String> {
        if !self.status.ready {
            return Err("MANAGED_SERVICE_UNAVAILABLE".into());
        }
        crate::managed_login::set_enabled(enabled)?;
        Ok(self.status())
    }

    pub fn browser_file(&self) -> Result<PathBuf, String> {
        if !self.status.ready {
            return Err("MANAGED_SERVICE_UNAVAILABLE".into());
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Reply {
            protocol: u8,
            handoff_file: PathBuf,
        }
        let bytes = self.request("browser")?;
        let reply: Reply = serde_json::from_slice(&bytes)
            .map_err(|_| "MANAGED_BROWSER_REPLY_INVALID".to_string())?;
        let directory = self.data_dir.join("browser-handoffs");
        regular(&reply.handoff_file)?;
        if reply.protocol != 1
            || reply.handoff_file.extension().and_then(|v| v.to_str()) != Some("html")
            || reply.handoff_file.parent() != Some(directory.as_path())
            || reply
                .handoff_file
                .canonicalize()
                .map_err(|_| "MANAGED_BROWSER_PATH_INVALID")?
                .parent()
                != Some(
                    directory
                        .canonicalize()
                        .map_err(|_| "MANAGED_BROWSER_PATH_INVALID")?
                        .as_path(),
                )
        {
            return Err("MANAGED_BROWSER_PATH_INVALID".into());
        }
        Ok(reply.handoff_file)
    }

    fn request(&self, action: &str) -> Result<Zeroizing<Vec<u8>>, String> {
        let executable = self
            .executable
            .as_ref()
            .ok_or("MANAGED_RUNTIME_UNAVAILABLE")?;
        let mut command = Command::new(executable);
        command
            .args([action, "--data-dir"])
            .arg(&self.data_dir)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW; serve detaches itself.
        }
        let mut child = command
            .spawn()
            .map_err(|_| "MANAGED_RUNTIME_START_FAILED")?;
        let output = child.stdout.take().ok_or("MANAGED_RUNTIME_PIPE_FAILED")?;
        let (sender, receiver) = std::sync::mpsc::sync_channel(1);
        std::thread::spawn(move || {
            let mut bytes = Zeroizing::new(Vec::new());
            let result = output
                .take(MAX_REPLY + 1)
                .read_to_end(&mut bytes)
                .map(|_| bytes)
                .map_err(|_| "MANAGED_RUNTIME_PIPE_FAILED");
            let _ = sender.send(result);
        });
        let deadline = std::time::Instant::now() + Duration::from_secs(60);
        let reply = receiver.recv_timeout(Duration::from_secs(60));
        if reply.is_err() {
            let _ = child.kill(); // only the launcher handle we created, never a PID from disk.
            let _ = child.wait();
            return Err("MANAGED_RUNTIME_START_TIMEOUT".into());
        }
        let bytes = reply.unwrap().map_err(str::to_string)?;
        let result = loop {
            if let Some(status) = child
                .try_wait()
                .map_err(|_| "MANAGED_RUNTIME_START_FAILED")?
            {
                break status;
            }
            if std::time::Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                return Err("MANAGED_RUNTIME_START_TIMEOUT".into());
            }
            std::thread::sleep(Duration::from_millis(20));
        };
        if bytes.len() as u64 > MAX_REPLY {
            return Err("MANAGED_RUNTIME_START_FAILED".into());
        }
        if !result.success() {
            return Err(journal_startup_failure(&bytes)
                .unwrap_or_else(|| "MANAGED_RUNTIME_START_FAILED".into()));
        }
        Ok(bytes)
    }
}

fn journal_startup_failure(bytes: &[u8]) -> Option<String> {
    // Reconstruct the one public diagnostic; never forward child messages or paths.
    let failure: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    if failure.get("protocol")?.as_u64()? != 1
        || failure.get("error")?.as_str()? != "JOURNAL_NEWER_DATA_STEP"
    {
        return None;
    }
    let recorded = failure.get("recorded_data_step")?.as_u64()?;
    let supported = failure.get("supported_data_step")?.as_u64()?;
    if recorded > i32::MAX as u64 || supported > i32::MAX as u64 {
        return None;
    }
    Some(format!("JOURNAL_NEWER_DATA_STEP: journal records data step {recorded}; this Connector supports up to {supported}; reopen with the newer Connector"))
}

fn regular(path: &Path) -> Result<fs::Metadata, String> {
    let metadata = fs::symlink_metadata(path).map_err(|_| "MANAGED_FILE_UNAVAILABLE")?;
    if !metadata.is_file() || linked(&metadata) {
        return Err("MANAGED_FILE_INVALID".into());
    }
    Ok(metadata)
}
fn linked(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_type().is_symlink() || metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    metadata.file_type().is_symlink()
}
fn digest(path: &Path) -> Result<String, String> {
    let metadata = regular(path)?;
    if metadata.len() > MAX_RUNTIME {
        return Err("MANAGED_RUNTIME_TOO_LARGE".into());
    }
    let mut file = File::open(path).map_err(|_| "MANAGED_FILE_UNAVAILABLE")?;
    let mut hash = Sha256::new();
    std::io::copy(&mut file, &mut hash).map_err(|_| "MANAGED_FILE_UNAVAILABLE")?;
    Ok(format!("{:x}", hash.finalize()))
}
fn private_directory(path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        if !parent.exists() {
            private_directory(parent)?;
        }
        if linked(&fs::symlink_metadata(parent).map_err(|_| "MANAGED_DIRECTORY_UNAVAILABLE")?) {
            return Err("MANAGED_DIRECTORY_INVALID".into());
        }
    }
    match fs::create_dir(path) {
        Ok(()) => {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(path, fs::Permissions::from_mode(0o700))
                    .map_err(|_| "MANAGED_DIRECTORY_PROTECTION_FAILED")?;
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(_) => return Err("MANAGED_DIRECTORY_UNAVAILABLE".into()),
    }
    let metadata = fs::symlink_metadata(path).map_err(|_| "MANAGED_DIRECTORY_UNAVAILABLE")?;
    if !metadata.is_dir() || linked(&metadata) {
        return Err("MANAGED_DIRECTORY_INVALID".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.uid() != unsafe { libc::geteuid() } || metadata.mode() & 0o077 != 0 {
            return Err("MANAGED_DIRECTORY_NOT_PRIVATE".into());
        }
    }
    Ok(())
}

fn materialize(resources: &Path, app_data: &Path) -> Result<(PathBuf, String), String> {
    let path = resources.join("manifest.json");
    if regular(&path)?.len() > MAX_REPLY {
        return Err("MANAGED_RUNTIME_MANIFEST_INVALID".into());
    }
    let document: Manifest =
        serde_json::from_slice(&fs::read(path).map_err(|_| "MANAGED_RUNTIME_UNAVAILABLE")?)
            .map_err(|_| "MANAGED_RUNTIME_MANIFEST_INVALID")?;
    let expected = if cfg!(windows) {
        "batc-managed-runtime.exe"
    } else {
        "batc-managed-runtime"
    };
    let valid_hash = |value: &str| {
        value.len() == 64
            && value
                .bytes()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    };
    if document.protocol != 1
        || document.platform != std::env::consts::OS
        || document.architecture != std::env::consts::ARCH
        || document.executable != expected
        || document.runtime_version.is_empty()
        || document.runtime_version.len() > 100
        || !valid_hash(&document.sha256)
        || !valid_hash(&document.payload_sha256)
        || document.size == 0
        || document.size > MAX_RUNTIME
    {
        return Err("MANAGED_RUNTIME_MANIFEST_INVALID".into());
    }
    let payload = resources.join("runtime.gz");
    if digest(&payload)? != document.payload_sha256 {
        return Err("MANAGED_RUNTIME_INTEGRITY_FAILED".into());
    }
    let cache = app_data.join("managed-runtimes");
    private_directory(&cache)?;
    let directory = cache.join(&document.sha256);
    private_directory(&directory)?;
    let target = directory.join(expected);
    match fs::symlink_metadata(&target) {
        Ok(_) => {
            if regular(&target)?.len() != document.size || digest(&target)? != document.sha256 {
                return Err("MANAGED_RUNTIME_INTEGRITY_FAILED".into());
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let temporary = directory.join(format!(".runtime-{}", uuid::Uuid::new_v4()));
            let result: Result<(), String> = (|| {
                let mut options = OpenOptions::new();
                options.write(true).create_new(true);
                #[cfg(unix)]
                {
                    use std::os::unix::fs::OpenOptionsExt;
                    options.mode(0o500);
                }
                let mut writer = options
                    .open(&temporary)
                    .map_err(|_| "MANAGED_RUNTIME_WRITE_FAILED")?;
                let reader = GzDecoder::new(
                    File::open(&payload).map_err(|_| "MANAGED_RUNTIME_UNAVAILABLE")?,
                );
                let count = std::io::copy(&mut reader.take(document.size + 1), &mut writer)
                    .map_err(|_| "MANAGED_RUNTIME_UNPACK_FAILED")?;
                writer.flush().map_err(|_| "MANAGED_RUNTIME_WRITE_FAILED")?;
                writer
                    .sync_all()
                    .map_err(|_| "MANAGED_RUNTIME_WRITE_FAILED")?;
                if count != document.size || digest(&temporary)? != document.sha256 {
                    return Err("MANAGED_RUNTIME_INTEGRITY_FAILED".into());
                }
                // A concurrent client can publish only the exact same content. Never replace.
                match fs::hard_link(&temporary, &target) {
                    Ok(()) => Ok(()),
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                        if digest(&target)? == document.sha256 {
                            Ok(())
                        } else {
                            Err("MANAGED_RUNTIME_INTEGRITY_FAILED".into())
                        }
                    }
                    Err(_) => Err("MANAGED_RUNTIME_PUBLISH_FAILED".into()),
                }
            })();
            let _ = fs::remove_file(&temporary);
            result?;
        }
        Err(_) => return Err("MANAGED_RUNTIME_UNAVAILABLE".into()),
    }
    Ok((target, document.runtime_version))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity_golden() -> serde_json::Value {
        // Generated by real same-version VCS installs and Python _verify().
        serde_json::from_str(include_str!(
            "../../../tests/fixtures/managed-runtime-identity.json"
        ))
        .unwrap()
    }

    fn identity_status() -> Status {
        Status {
            mode: "managed",
            ready: true,
            background: true,
            runtime_version: None,
            server_id: None,
            principal_id: None,
            error: None,
            login_enabled: false,
            bundled_runtime: None,
            running_runtime: None,
            runtime_difference: None,
        }
    }

    #[test]
    fn launch_and_status_preserve_python_runtime_identity_facts() {
        for reply in identity_golden().as_object().unwrap().values() {
            let launch: Launch = serde_json::from_value(reply.clone()).unwrap();
            let mut status = identity_status();
            RuntimeFacts::from_launch(&launch).apply(&mut status);
            let serialized = serde_json::to_value(&status).unwrap();
            for field in ["bundled_runtime", "running_runtime", "runtime_difference"] {
                assert_eq!(serialized[field], reply[field], "{field}");
            }
            // Availability is independent of a same-version build difference.
            assert!(status.ready);
        }
    }

    #[test]
    fn old_launch_without_identity_fields_still_parses() {
        let mut reply = identity_golden()["same"].clone();
        for field in ["bundled_runtime", "running_runtime", "runtime_difference"] {
            reply.as_object_mut().unwrap().remove(field);
        }
        let launch: Launch = serde_json::from_value(reply.clone()).unwrap();
        assert!(launch.bundled_runtime.is_none());
        assert!(launch.running_runtime.is_none());
        assert!(launch.runtime_difference.is_none());
        assert_eq!(
            RuntimeFacts::from_launch(&launch).difference,
            Some(RuntimeDifference::Unknown)
        );
        reply["unexpected"] = serde_json::json!(true);
        assert!(serde_json::from_value::<Launch>(reply).is_err());
    }

    #[test]
    fn refreshed_runtime_facts_replace_the_previous_observation() {
        let golden = identity_golden();
        let managed = Managed {
            executable: None,
            data_dir: PathBuf::new(),
            status: identity_status(),
            serial: Mutex::new(()),
            runtime_facts: Mutex::new(RuntimeFacts::default()),
        };
        for name in ["same", "differs", "unknown_running"] {
            let launch: Launch = serde_json::from_value(golden[name].clone()).unwrap();
            managed.remember_runtime(&launch);
            let mut status = managed.status.clone();
            managed.runtime_facts.lock().unwrap().apply(&mut status);
            assert_eq!(status.runtime_difference, launch.runtime_difference);
            assert_eq!(status.running_runtime, launch.running_runtime);
        }
    }

    fn fixture(root: &Path, bytes: &[u8]) -> PathBuf {
        let resources = root.join("resources");
        fs::create_dir_all(&resources).unwrap();
        let mut gzip = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        gzip.write_all(bytes).unwrap();
        let compressed = gzip.finish().unwrap();
        fs::write(resources.join("runtime.gz"), &compressed).unwrap();
        let manifest = serde_json::json!({
            "protocol": 1, "runtime_version": "fixture-1", "platform": std::env::consts::OS,
            "architecture": std::env::consts::ARCH,
            "executable": if cfg!(windows) { "batc-managed-runtime.exe" } else { "batc-managed-runtime" },
            "payload_sha256": format!("{:x}", Sha256::digest(&compressed)),
            "sha256": format!("{:x}", Sha256::digest(bytes)), "size": bytes.len(),
        });
        fs::write(
            resources.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        resources
    }

    #[test]
    fn bundled_runtime_integrity_is_checked_before_reuse_and_never_overwritten() {
        let root = tempfile::tempdir().unwrap();
        let resources = fixture(root.path(), b"fixed-runtime-fixture");
        let data = root.path().join("application");
        let (path, version) = materialize(&resources, &data).unwrap();
        assert_eq!(version, "fixture-1");
        assert_eq!(fs::read(&path).unwrap(), b"fixed-runtime-fixture");
        assert_eq!(materialize(&resources, &data).unwrap().0, path);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        fs::write(&path, b"modified").unwrap();
        assert_eq!(
            materialize(&resources, &data).unwrap_err(),
            "MANAGED_RUNTIME_INTEGRITY_FAILED"
        );
        assert_eq!(fs::read(&path).unwrap(), b"modified");
    }

    #[test]
    fn corrupted_package_and_architecture_refuse_before_extracting() {
        let root = tempfile::tempdir().unwrap();
        let resources = fixture(root.path(), b"fixed-runtime-fixture");
        let data = root.path().join("application");
        fs::write(resources.join("runtime.gz"), b"corrupt").unwrap();
        assert_eq!(
            materialize(&resources, &data).unwrap_err(),
            "MANAGED_RUNTIME_INTEGRITY_FAILED"
        );
        assert!(!data.exists());
        let manifest_path = resources.join("manifest.json");
        let mut manifest: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
        manifest["architecture"] = serde_json::json!("other-platform");
        fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
        assert_eq!(
            materialize(&resources, &data).unwrap_err(),
            "MANAGED_RUNTIME_MANIFEST_INVALID"
        );
        assert!(!data.exists());
    }

    #[test]
    fn existing_owned_manifest_keeps_external_client_selected_during_background_recovery() {
        let root = tempfile::tempdir().unwrap();
        let config = root.path().join("configuration");
        let data = root.path().join("application");
        fs::create_dir(&config).unwrap();
        fs::write(
            config.join("central.json"),
            b"operator-owned external configuration",
        )
        .unwrap();
        fs::create_dir_all(data.join("managed-central")).unwrap();
        let installation = data.join("managed-central/installation.json");
        fs::write(
            &installation,
            b"saved installation evidence for Python to verify",
        )
        .unwrap();
        let (managed, launch) =
            Managed::prepare(&root.path().join("missing-package"), &data, &config);
        assert_eq!(managed.status.mode, "external");
        assert!(managed.status.background);
        assert!(!managed.status.ready && managed.status.error.is_some() && launch.is_none());
        assert_eq!(
            fs::read(installation).unwrap(),
            b"saved installation evidence for Python to verify"
        );
        assert!(!data.join("managed-central/state/tasks.sqlite3").exists());
    }

    #[test]
    fn explicit_remote_configuration_never_provisions_a_local_service() {
        let root = tempfile::tempdir().unwrap();
        let config = root.path().join("configuration");
        fs::create_dir(&config).unwrap();
        fs::write(
            config.join("central.json"),
            b"even invalid existing configuration",
        )
        .unwrap();
        let data = root.path().join("application");
        let (state, launch) = Managed::prepare(&root.path().join("resources"), &data, &config);
        assert_eq!(state.status.mode, "external");
        assert!(launch.is_none());
        assert!(!data.exists());
    }

    #[test]
    fn journal_startup_diagnostic_has_only_the_registered_code_and_numbers() {
        let mut failure = serde_json::json!({"protocol": 1, "error": "JOURNAL_NEWER_DATA_STEP",
            "recorded_data_step": 4, "supported_data_step": 3,
            "message": "private-provider-diagnostic"});
        assert_eq!(journal_startup_failure(&serde_json::to_vec(&failure).unwrap()).unwrap(),
            "JOURNAL_NEWER_DATA_STEP: journal records data step 4; this Connector supports up to 3; reopen with the newer Connector");
        for invalid in [
            serde_json::json!(true),
            serde_json::json!(-1),
            serde_json::json!(2147483648_u64),
        ] {
            failure["recorded_data_step"] = invalid;
            assert!(journal_startup_failure(&serde_json::to_vec(&failure).unwrap()).is_none());
        }
        failure["recorded_data_step"] = serde_json::json!(4);
        failure["error"] = serde_json::json!("UNKNOWN_FAILURE");
        assert!(journal_startup_failure(&serde_json::to_vec(&failure).unwrap()).is_none());
    }

    #[test]
    fn launch_reply_is_loopback_and_identity_bound() {
        let mut reply = Launch {
            protocol: 1,
            endpoint: "http://127.0.0.1:9999/".into(),
            actor: "fixture-user".into(),
            server_id: "fixture-server".into(),
            principal_id: "fixture-principal".into(),
            token: Zeroizing::new("fixture-token".into()),
            runtime_version: "fixture-1".into(),
            bundled_runtime: None,
            running_runtime: None,
            runtime_difference: None,
        };
        reply.validate("fixture-1").unwrap();
        assert!(reply.validate("other-version").is_err());
        reply.endpoint = "https://unrelated.example/".into();
        assert!(reply.validate("fixture-1").is_err());
        reply.endpoint = "http://127.0.0.1:9999/".into();
        reply.server_id = "invalid/server".into();
        assert!(reply.validate("fixture-1").is_err());
    }

    #[cfg(unix)]
    #[test]
    fn symbolic_runtime_directory_is_refused_without_touching_target() {
        let root = tempfile::tempdir().unwrap();
        let resources = fixture(root.path(), b"fixed-runtime-fixture");
        let data = root.path().join("application");
        fs::create_dir(&data).unwrap();
        let unrelated = root.path().join("unrelated");
        fs::create_dir(&unrelated).unwrap();
        std::os::unix::fs::symlink(&unrelated, data.join("managed-runtimes")).unwrap();
        assert!(materialize(&resources, &data).is_err());
        assert_eq!(fs::read_dir(&unrelated).unwrap().count(), 0);
    }
}
