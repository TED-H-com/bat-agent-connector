//! Installation lifecycle only. Python owns identity, locking, journal and operations.
//! Secrets travel in a bounded anonymous stdout pipe and never cross WebView IPC.
use crate::{
    bridge, credentials,
    startup_role::{self, Role, Snapshot},
};
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

#[allow(dead_code)]
mod creation;

fn runtime_executable_busy(error: &std::io::Error) -> bool {
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        error.raw_os_error() == Some(libc::ETXTBSY)
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        let _ = error;
        false
    }
}

fn spawn_runtime<T>(mut spawn: impl FnMut() -> std::io::Result<T>) -> std::io::Result<T> {
    let mut delays = [10, 20, 40, 80, 160].into_iter();
    loop {
        match spawn() {
            Err(error) if runtime_executable_busy(&error) => {
                let Some(delay) = delays.next() else {
                    return Err(error);
                };
                // ETXTBSY means execve never ran the runtime or sent ensure; this
                // retries only that failed spawn, never an uncertain start.
                std::thread::sleep(Duration::from_millis(delay));
            }
            result => return result,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    protocol: u8,
    runtime_version: String,
    #[serde(default, rename = "build_commit")]
    _build_commit: Option<String>,
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

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Lifecycle {
    pub state: String,
    pub requested_at: Option<f64>,
    pub stopped_at: Option<f64>,
}
impl Lifecycle {
    fn running() -> Self {
        Self {
            state: "running".into(),
            requested_at: None,
            stopped_at: None,
        }
    }
    fn valid(&self) -> bool {
        let time = |value: Option<f64>| value.is_some_and(|v| v.is_finite() && v > 0.0);
        match self.state.as_str() {
            "running" => self.requested_at.is_none() && self.stopped_at.is_none(),
            "stop_pending" => time(self.requested_at) && self.stopped_at.is_none(),
            "stopped" => time(self.requested_at) && time(self.stopped_at),
            _ => false,
        }
    }
}
pub fn held(error: &str) -> bool {
    matches!(error, "SERVICE_STOPPED" | "SERVICE_STILL_STOPPING")
}
// Shared with startup visibility: a held login launch is quiet.
pub fn startup_visible(
    status: &Status,
    login: bool,
    update_pending: bool,
    show_dashboard: bool,
) -> bool {
    if login
        && status
            .lifecycle
            .as_ref()
            .is_some_and(|v| v.state != "running")
    {
        return false;
    }
    status.mode == "role_pending"
        || status.error.is_some()
        || update_pending
        || !login && show_dashboard
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
    pub lifecycle: Option<Lifecycle>,
    pub bundled_runtime: Option<RuntimeIdentity>,
    pub running_runtime: Option<RuntimeIdentity>,
    pub runtime_difference: Option<RuntimeDifference>,
}

#[derive(Clone, Default)]
struct RuntimeFacts {
    creation: Option<creation::ServiceIdentity>,
    bundled: Option<RuntimeIdentity>,
    running: Option<RuntimeIdentity>,
    difference: Option<RuntimeDifference>,
}

impl RuntimeFacts {
    fn from_launch(launch: &Launch) -> Self {
        Self {
            creation: Some(creation::ServiceIdentity {
                actor: launch.actor.clone(),
                identity: launch.identity(),
            }),
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
    executable: Mutex<Option<PathBuf>>,
    data_dir: PathBuf,
    resources: PathBuf,
    app_data: PathBuf,
    config: PathBuf,
    role: Mutex<Option<Snapshot>>,
    status: Mutex<Status>,
    pub serial: Mutex<()>,
    runtime_facts: Mutex<RuntimeFacts>,
}

impl Managed {
    pub fn prepare(resources: &Path, app_data: &Path, config: &Path) -> (Self, Option<Launch>) {
        let state = Self {
            executable: Mutex::new(None),
            data_dir: app_data.join("managed-central"),
            resources: resources.to_owned(),
            app_data: app_data.to_owned(),
            config: config.to_owned(),
            role: Mutex::new(None),
            status: Mutex::new(Status {
                mode: "role_pending",
                ready: false,
                background: false,
                runtime_version: None,
                server_id: None,
                principal_id: None,
                error: None,
                login_enabled: false,
                lifecycle: None,
                bundled_runtime: None,
                running_runtime: None,
                runtime_difference: None,
            }),
            serial: Mutex::new(()),
            runtime_facts: Mutex::new(RuntimeFacts::default()),
        };
        let result = (|| {
            let _serial = state.serial.lock().map_err(|_| "MANAGED_SERVICE_BUSY")?;
            let snapshot = startup_role::load_or_infer(app_data, config)?;
            *state.role.lock().unwrap() = Some(snapshot.clone());
            state.prepare_role(snapshot)
        })();
        let launch = match result {
            Ok(launch) => launch,
            Err(error) => {
                if !held(&error) {
                    state.status.lock().unwrap().error = Some(error);
                }
                None
            }
        };
        (state, launch)
    }

    pub fn startup_role(&self) -> Option<Snapshot> {
        self.role.lock().unwrap().clone()
    }

    // The fixed native command is the only selector. Persist before any materialize/ensure.
    pub fn select_role(
        &self,
        role: Role,
        expected_generation: u64,
    ) -> Result<Option<Launch>, String> {
        let _serial = self.serial.try_lock().map_err(|_| "MANAGED_SERVICE_BUSY")?;
        let snapshot =
            startup_role::select(&self.app_data, &self.config, role, expected_generation)?;
        *self.role.lock().unwrap() = Some(snapshot.clone());
        let result = self.prepare_role(snapshot);
        if let Err(error) = &result {
            self.status.lock().unwrap().error = Some(error.clone());
        }
        result
    }

    // Caller holds serial. Existing external selection wins, including malformed config;
    // a selected client cannot become a provider due to a missing/unreachable endpoint.
    fn prepare_role(&self, snapshot: Snapshot) -> Result<Option<Launch>, String> {
        let Some(role) = snapshot.role else {
            return Ok(None);
        };
        let external = startup_role::external(&self.config)?;
        {
            let mut status = self.status.lock().unwrap();
            status.mode = if external || role == Role::Client {
                "external"
            } else {
                "managed"
            };
            status.background = role.provides();
            status.lifecycle = role.provides().then(Lifecycle::running);
            status.error = None;
        }
        if !role.provides() {
            return Ok(None);
        }
        let (executable, version) =
            materialize(&self.resources.join("managed-runtime"), &self.app_data)?;
        *self.executable.lock().unwrap() = Some(executable);
        self.status.lock().unwrap().runtime_version = Some(version.clone());
        let bytes = match self.request("ensure") {
            Err(error) if held(&error) => return Ok(None),
            result => result?,
        };
        let launch: Launch =
            serde_json::from_slice(&bytes).map_err(|_| "MANAGED_RUNTIME_REPLY_INVALID")?;
        launch.validate(&version)?;
        {
            let mut status = self.status.lock().unwrap();
            status.ready = true;
            status.server_id = Some(launch.server_id.clone());
            status.principal_id = Some(launch.principal_id.clone());
        }
        self.remember_runtime(&launch);
        Ok(Some(launch))
    }

    pub fn recover(&self) -> Result<Launch, String> {
        let status = self.status();
        if status
            .lifecycle
            .as_ref()
            .is_some_and(|v| v.state != "running")
        {
            return Err("SERVICE_STOPPED".into());
        }
        if status.mode != "managed" || !status.ready {
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
            status
                .runtime_version
                .as_deref()
                .ok_or("MANAGED_RUNTIME_UNAVAILABLE")?,
        )?;
        if Some(&launch.server_id) != status.server_id.as_ref()
            || Some(&launch.principal_id) != status.principal_id.as_ref()
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
        self.status.lock().unwrap().lifecycle = Some(Lifecycle::running());
    }

    pub fn status(&self) -> Status {
        let mut status = self.status.lock().unwrap().clone();
        if let Ok(facts) = self.runtime_facts.lock() {
            facts.apply(&mut status);
        }
        match crate::managed_login::enabled() {
            Ok(enabled) => status.login_enabled = enabled,
            Err(error) => status.error = Some(error),
        }
        status
    }

    pub fn read_status(&self) -> Result<Status, String> {
        Ok(self.status())
    }

    pub fn is_held(&self) -> bool {
        self.status
            .lock()
            .unwrap()
            .lifecycle
            .as_ref()
            .is_some_and(|v| v.state != "running")
    }

    fn require_owned(&self) -> Result<(), String> {
        if !self
            .role
            .lock()
            .unwrap()
            .as_ref()
            .and_then(|s| s.role)
            .is_some_and(Role::provides)
        {
            return Err("MANAGED_SERVICE_NOT_OWNED".into());
        }
        Ok(())
    }

    // Caller holds serial, as for browser/login. Python owns both lifecycle effects.
    pub fn stop(&self) -> Result<Status, String> {
        self.require_owned()?;
        let bytes = self.request("stop")?;
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Reply {
            protocol: u8,
            stopped: bool,
            lifecycle: Lifecycle,
        }
        let reply: Reply =
            serde_json::from_slice(&bytes).map_err(|_| "MANAGED_RUNTIME_REPLY_INVALID")?;
        if reply.protocol != 1
            || !reply.lifecycle.valid()
            || reply.stopped != (reply.lifecycle.state == "stopped")
        {
            return Err("MANAGED_RUNTIME_REPLY_INVALID".into());
        }
        self.status.lock().unwrap().lifecycle = Some(reply.lifecycle);
        Ok(self.status())
    }

    pub fn start(&self) -> Result<Launch, String> {
        self.require_owned()?;
        let bytes = self.request("resume")?;
        let launch: Launch =
            serde_json::from_slice(&bytes).map_err(|_| "MANAGED_RUNTIME_REPLY_INVALID")?;
        let mut status = self.status.lock().unwrap();
        launch.validate(
            status
                .runtime_version
                .as_deref()
                .ok_or("MANAGED_RUNTIME_UNAVAILABLE")?,
        )?;
        if status
            .server_id
            .as_ref()
            .is_some_and(|v| v != &launch.server_id)
            || status
                .principal_id
                .as_ref()
                .is_some_and(|v| v != &launch.principal_id)
        {
            return Err("MANAGED_RECOVERY_IDENTITY_CHANGED".into());
        }
        status.ready = true;
        status.error = None;
        status.server_id = Some(launch.server_id.clone());
        status.principal_id = Some(launch.principal_id.clone());
        status.lifecycle = Some(Lifecycle::running());
        drop(status);
        self.remember_runtime(&launch);
        Ok(launch)
    }

    pub fn set_login(&self, enabled: bool) -> Result<Status, String> {
        self.require_owned()?;
        crate::managed_login::set_enabled(enabled)?;
        Ok(self.status())
    }

    pub fn browser_file(&self) -> Result<PathBuf, String> {
        if self
            .status()
            .lifecycle
            .as_ref()
            .is_some_and(|v| v.state != "running")
        {
            return Err("SERVICE_STOPPED".into());
        }
        if !self.status().ready {
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
            .lock()
            .unwrap()
            .clone()
            .ok_or("MANAGED_RUNTIME_UNAVAILABLE")?;
        let mut command = Command::new(&executable);
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
        let mut child =
            spawn_runtime(|| command.spawn()).map_err(|_| "MANAGED_RUNTIME_START_FAILED")?;
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
        if let Some(lifecycle) = reply_lifecycle(&bytes) {
            self.status.lock().unwrap().lifecycle = Some(lifecycle);
        }
        if !result.success() {
            if let Some((code, _)) = lifecycle_failure(&bytes) {
                return Err(code);
            }
            return Err(journal_startup_failure(&bytes)
                .unwrap_or_else(|| "MANAGED_RUNTIME_START_FAILED".into()));
        }
        Ok(bytes)
    }
}

fn reply_lifecycle(bytes: &[u8]) -> Option<Lifecycle> {
    let reply: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    if reply.get("protocol")?.as_u64()? != 1 {
        return None;
    }
    let lifecycle: Lifecycle = serde_json::from_value(reply.get("lifecycle")?.clone()).ok()?;
    lifecycle.valid().then_some(lifecycle)
}

fn lifecycle_failure(bytes: &[u8]) -> Option<(String, Option<Lifecycle>)> {
    let failure: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    if failure.get("protocol")?.as_u64()? != 1 {
        return None;
    }
    let code = failure.get("error")?.as_str()?;
    if held(code) {
        let lifecycle: Lifecycle =
            serde_json::from_value(failure.get("lifecycle")?.clone()).ok()?;
        if !lifecycle.valid() || lifecycle.state == "running" {
            return None;
        }
        return Some((code.into(), Some(lifecycle)));
    }
    if matches!(code, "SERVICE_LIFECYCLE_UNPROVEN" | "SERVICE_STOP_REFUSED") {
        return Some((code.into(), None));
    }
    if code == "SERVICE_BUSY" {
        let message = failure
            .get("message")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        if [
            "finish or reconcile current operations before stopping",
            "finish or pause service shutdown until current tasks settle",
            "current work is still running",
        ]
        .contains(&message)
        {
            return Some((format!("SERVICE_BUSY: {message}"), None));
        }
        return Some(("SERVICE_BUSY".into(), None));
    }
    None
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
                // Close our writer before publication. A concurrent fork can
                // retain its CLOEXEC copy; spawn_runtime bounds that exec-only race.
                drop(writer);
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

    #[cfg(unix)]
    #[test]
    fn startup_hold_is_settled_and_does_not_spawn_another_ensure() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let config = root.path().join("config");
        startup_role::select(&data, &config, Role::Provider, 0).unwrap();
        fs::create_dir_all(data.join("managed-central")).unwrap();
        fs::write(data.join("managed-central/held"), b"hold").unwrap();
        let (managed, launch) = Managed::prepare(&resources, &data, &config);
        assert!(launch.is_none());
        assert!(!startup_visible(&managed.status(), true, true, true));
        assert_eq!(managed.browser_file().unwrap_err(), "SERVICE_STOPPED");
        let value = serde_json::to_value(managed.status()).unwrap();
        assert_eq!(value["lifecycle"]["state"], "stopped");
        assert_eq!(value["error"], serde_json::Value::Null);
        assert_eq!(
            fs::read(data.join("managed-central/ensure-count")).unwrap(),
            b"ensure\n"
        );
        assert!(!data.join("managed-central/service-count").exists());
    }

    #[cfg(unix)]
    #[test]
    fn recovery_hold_is_cached_without_further_launcher_calls() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let config = root.path().join("config");
        let (managed, _) = Managed::prepare(&resources, &data, &config);
        managed.select_role(Role::Provider, 0).unwrap();
        fs::write(data.join("managed-central/held"), b"hold").unwrap();
        for _ in 0..5 {
            assert_eq!(managed.recover().err().unwrap(), "SERVICE_STOPPED");
            assert!(!startup_visible(&managed.status(), true, true, true));
        }
        assert_eq!(
            fs::read(data.join("managed-central/ensure-count")).unwrap(),
            b"ensure\nensure\n"
        );
        assert_eq!(
            fs::read(data.join("managed-central/service-count")).unwrap(),
            b"service\n"
        );
    }

    #[cfg(unix)]
    #[test]
    fn status_observes_an_external_stop_only_after_recovery_ensure() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let (managed, _) = Managed::prepare(&resources, &data, &root.path().join("config"));
        managed.select_role(Role::Provider, 0).unwrap();
        fs::write(data.join("managed-central/held"), b"hold").unwrap();
        assert_eq!(
            crate::managed_status(&managed)
                .unwrap()
                .lifecycle
                .unwrap()
                .state,
            "running"
        );
        assert_eq!(managed.recover().err().unwrap(), "SERVICE_STOPPED");
        for _ in 0..3 {
            assert_eq!(
                crate::managed_status(&managed)
                    .unwrap()
                    .lifecycle
                    .unwrap()
                    .state,
                "stopped"
            );
        }
        assert_eq!(
            fs::read(data.join("managed-central/ensure-count")).unwrap(),
            b"ensure\nensure\n"
        );
        assert_eq!(
            fs::read(data.join("managed-central/launcher-count")).unwrap(),
            b"ensure\nensure\n"
        );
        assert_eq!(
            fs::read(data.join("managed-central/service-count")).unwrap(),
            b"service\n"
        );
    }

    #[cfg(unix)]
    #[test]
    fn status_is_concurrent_and_never_spawns_a_launcher() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let (managed, _) = Managed::prepare(&resources, &data, &root.path().join("config"));
        managed.select_role(Role::Provider, 0).unwrap();
        let barrier = std::sync::Barrier::new(2);
        let results = std::thread::scope(|threads| {
            let first = threads.spawn(|| {
                barrier.wait();
                crate::managed_status(&managed)
            });
            let second = threads.spawn(|| {
                barrier.wait();
                crate::managed_status(&managed)
            });
            (first.join().unwrap(), second.join().unwrap())
        });
        assert!(
            results.0.is_ok() && results.1.is_ok(),
            "both Status calls must succeed"
        );
        assert_eq!(
            fs::read(data.join("managed-central/launcher-count")).unwrap(),
            b"ensure\n"
        );
    }

    #[cfg(unix)]
    #[test]
    fn status_succeeds_while_serial_is_held_and_makes_no_launcher_call() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let (managed, _) = Managed::prepare(&resources, &data, &root.path().join("config"));
        managed.select_role(Role::Provider, 0).unwrap();
        let _serial = managed.serial.lock().unwrap();
        assert!(crate::managed_status(&managed).is_ok());
        assert_eq!(
            fs::read(data.join("managed-central/launcher-count")).unwrap(),
            b"ensure\n"
        );
    }

    #[cfg(unix)]
    #[test]
    fn real_recovery_announces_and_cached_hold_stays_quiet_without_a_launcher() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let config = root.path().join("config");
        let (managed, _) = Managed::prepare(&resources, &data, &config);
        let launch = managed.select_role(Role::Provider, 0).unwrap().unwrap();
        let bridge = crate::bridge::Bridge::managed(
            &config,
            launch.config(),
            launch.token.clone(),
            launch.identity(),
        );
        crate::managed_recovery::announce(&bridge, 0, &managed);
        assert_eq!(
            bridge.status().managed_recovery.as_deref(),
            Some("MANAGED_RECOVERING")
        );
        fs::write(data.join("managed-central/held"), b"hold").unwrap();
        assert_eq!(managed.recover().err().unwrap(), "SERVICE_STOPPED");
        bridge.managed_hold(Some(0));
        assert!(bridge.status().managed_recovery.is_none());
        // Keep the bridge eligible: silence must come from the cached lifecycle,
        // not from Bridge ignoring announcements after recovery was disabled.
        let eligible = crate::bridge::Bridge::managed(
            &config,
            launch.config(),
            launch.token.clone(),
            launch.identity(),
        );
        crate::managed_recovery::announce(&eligible, 0, &managed);
        assert!(eligible.status().managed_recovery.is_none());
        assert_eq!(managed.recover().err().unwrap(), "SERVICE_STOPPED");
        assert_eq!(
            fs::read(data.join("managed-central/launcher-count")).unwrap(),
            b"ensure\nensure\n"
        );
    }

    #[test]
    fn unknown_busy_text_is_typed_without_forwarding_unlisted_text() {
        let bytes = br#"{"protocol":1,"error":"SERVICE_BUSY","message":"unlisted private text"}"#;
        assert_eq!(
            lifecycle_failure(bytes).map(|v| v.0).as_deref(),
            Some("SERVICE_BUSY")
        );
    }

    #[cfg(unix)]
    #[test]
    fn launcher_results_and_refusals_refresh_cached_lifecycle() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let (managed, _) = Managed::prepare(&resources, &data, &root.path().join("config"));
        managed.select_role(Role::Provider, 0).unwrap();
        let runtime = data.join("managed-central");
        fs::write(runtime.join("reply-stop"), br#"{"protocol":1,"stopped":false,"lifecycle":{"state":"stop_pending","requested_at":1800000000,"stopped_at":null}}"#).unwrap();
        managed.request("stop").unwrap();
        assert_eq!(managed.status().lifecycle.unwrap().state, "stop_pending");
        for code in ["SERVICE_BUSY", "SERVICE_STOP_REFUSED"] {
            fs::write(runtime.join("reply-stop"), serde_json::to_vec(&serde_json::json!({"protocol":1,"error":code,
                "message":"unlisted private text", "lifecycle":{"state":"running","requested_at":null,"stopped_at":null}})).unwrap()).unwrap();
            fs::write(runtime.join("fail-stop"), b"fail").unwrap();
            assert_eq!(managed.request("stop").err().unwrap(), code);
            assert_eq!(managed.status().lifecycle.unwrap().state, "running");
        }
    }

    #[test]
    fn status_serialization_keeps_all_existing_fields_and_adds_lifecycle() {
        let value = serde_json::to_value(identity_status()).unwrap();
        for key in [
            "mode",
            "ready",
            "background",
            "runtime_version",
            "server_id",
            "principal_id",
            "error",
            "login_enabled",
            "bundled_runtime",
            "running_runtime",
            "runtime_difference",
            "lifecycle",
        ] {
            assert!(value.get(key).is_some(), "{key}");
        }
        assert_eq!(value["ready"], true);
        assert_eq!(value["background"], true);
    }

    #[test]
    fn clean_install_waits_for_role_before_touching_runtime_or_python_data() {
        let root = tempfile::tempdir().unwrap();
        let data = root.path().join("application");
        let config = root.path().join("configuration");
        // An absent package must not even be inspected until a provider is chosen.
        let (managed, launch) = Managed::prepare(&root.path().join("resources"), &data, &config);
        assert_eq!(managed.status().mode, "role_pending");
        assert!(managed.status().error.is_none());
        assert!(launch.is_none());
        assert!(!data.join("managed-runtimes").exists());
        assert!(!data.join("managed-central").exists());
        assert!(!config.exists());
    }

    #[cfg(unix)]
    fn startup_fixture(root: &Path) -> PathBuf {
        // Real materialize + subprocess request boundary. Only the Python owner is doubled.
        // Production launch fields use the same parser/validation as packaged runtime replies.
        let resources = root.join("resources");
        fs::create_dir(&resources).unwrap();
        let script = br##"#!/bin/sh
set -eu
root="$3"
mkdir -p "$root"
printf '%s\n' "$1" >> "$root/launcher-count"
if [ -f "$root/reply-$1" ]; then
 cat "$root/reply-$1"
 if [ -f "$root/fail-$1" ]; then exit 1; fi
 exit 0
fi
if [ "$1" = status ]; then
 sleep 0.1
 if [ -f "$root/held" ]; then
  printf '%s' '{"protocol":1,"lifecycle":{"state":"stopped","requested_at":1800000000,"stopped_at":1800000001}}'
 else
  printf '%s' '{"protocol":1,"lifecycle":{"state":"running","requested_at":null,"stopped_at":null}}'
 fi
 exit 0
fi
[ "$1" = ensure ]
mkdir -p "$root/state"
printf 'ensure\n' >> "$root/ensure-count"
if [ -f "$root/held" ]; then
 printf '%s' '{"protocol":1,"error":"SERVICE_STOPPED","lifecycle":{"state":"stopped","requested_at":1800000000,"stopped_at":1800000001}}'
 exit 1
fi
if [ ! -f "$root/installation.json" ]; then
  printf 'original-manifest' > "$root/installation.json"
  printf 'original-identity' > "$root/identity.token"
  printf 'original-journal' > "$root/state/tasks.sqlite3"
  printf 'original-project' > "$root/state/project-fixture"
  printf 'service\n' >> "$root/service-count"
fi
if [ -f "$root/fail-once" ]; then rm "$root/fail-once"; exit 1; fi
printf '%s' '{"protocol":1,"endpoint":"http://127.0.0.1:41001/","actor":"fixture-user","server_id":"fixture-server","principal_id":"fixture-principal","token":"fixture-token","runtime_version":"fixture-1"}'
"##;
        let package = fixture(root, script);
        fs::rename(package.join("runtime.gz"), package.join("payload.gz")).unwrap();
        fs::create_dir(package.join("managed-runtime")).unwrap();
        fs::rename(
            package.join("payload.gz"),
            package.join("managed-runtime/runtime.gz"),
        )
        .unwrap();
        fs::rename(
            package.join("manifest.json"),
            package.join("managed-runtime/manifest.json"),
        )
        .unwrap();
        resources
    }

    #[cfg(unix)]
    fn spawn_fixture_runtime(
        executable: &Path,
        data: &Path,
        mut observed: impl FnMut(&std::io::Result<std::process::Child>),
    ) -> std::io::Result<std::process::Child> {
        let mut command = Command::new(executable);
        command
            .args(["ensure", "--data-dir"])
            .arg(data)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        spawn_runtime(|| {
            let result = command.spawn();
            observed(&result);
            result
        })
    }

    #[cfg(target_os = "linux")]
    fn busy_runtime_fixture(root: &Path) -> (Managed, File) {
        use std::os::unix::fs::PermissionsExt;
        let resources = startup_fixture(root);
        let data = root.join("app");
        let (managed, _) = Managed::prepare(&resources, &data, &root.join("config"));
        let (executable, _) = materialize(&resources.join("managed-runtime"), &data).unwrap();
        // A forked child's CLOEXEC copy has the same effect as this held writer.
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
        let writer = OpenOptions::new().write(true).open(&executable).unwrap();
        *managed.executable.lock().unwrap() = Some(executable);
        (managed, writer)
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn runtime_spawn_busy_held_returns_bounded_error_without_running_runtime() {
        let root = tempfile::tempdir().unwrap();
        let (managed, writer) = busy_runtime_fixture(root.path());
        assert_eq!(
            managed.request("ensure").unwrap_err(),
            "MANAGED_RUNTIME_START_FAILED"
        );
        assert!(!managed.data_dir.join("ensure-count").exists());
        let executable = managed.executable.lock().unwrap().clone().unwrap();
        let mut attempts = 0;
        let error = spawn_fixture_runtime(&executable, &managed.data_dir, |result| {
            attempts += 1;
            assert_eq!(
                result.as_ref().unwrap_err().raw_os_error(),
                Some(libc::ETXTBSY)
            );
        })
        .unwrap_err();
        assert_eq!(error.raw_os_error(), Some(libc::ETXTBSY));
        assert_eq!(attempts, 6);
        assert!(!managed.data_dir.join("ensure-count").exists());
        assert!(!managed.data_dir.join("installation.json").exists());
        drop(writer);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn runtime_spawn_busy_released_after_first_error_runs_runtime_exactly_once() {
        let root = tempfile::tempdir().unwrap();
        let (managed, writer) = busy_runtime_fixture(root.path());
        let executable = managed.executable.lock().unwrap().clone().unwrap();
        let (release, held) = std::sync::mpsc::channel();
        let mut first_error = None;
        let mut attempts = 0;
        let result = std::thread::scope(|threads| {
            threads.spawn(move || {
                held.recv().unwrap();
                std::thread::sleep(Duration::from_millis(20));
                drop(writer);
            });
            spawn_fixture_runtime(&executable, &managed.data_dir, |result| {
                attempts += 1;
                if attempts == 1 {
                    first_error = result.as_ref().err().and_then(std::io::Error::raw_os_error);
                    // Release cannot start until a real exec has failed with the writer held.
                    release.send(()).unwrap();
                }
            })
        });
        assert_eq!(first_error, Some(libc::ETXTBSY));
        assert!(result.unwrap().wait().unwrap().success());
        assert!((2..=6).contains(&attempts));
        assert_eq!(
            fs::read(managed.data_dir.join("ensure-count")).unwrap(),
            b"ensure\n"
        );
        assert_eq!(
            fs::read(managed.data_dir.join("service-count")).unwrap(),
            b"service\n"
        );
    }

    #[test]
    fn runtime_spawn_never_retries_other_errors() {
        for code in [1, 2, 13, 16, 22, 24, 27] {
            let mut attempts = 0;
            let result = spawn_runtime(|| {
                attempts += 1;
                Err::<(), _>(std::io::Error::from_raw_os_error(code))
            });
            assert_eq!(result.unwrap_err().raw_os_error(), Some(code));
            assert_eq!(attempts, 1);
        }
        for kind in [
            std::io::ErrorKind::Interrupted,
            std::io::ErrorKind::WouldBlock,
            std::io::ErrorKind::ExecutableFileBusy,
        ] {
            let mut attempts = 0;
            let result = spawn_runtime(|| {
                attempts += 1;
                Err::<(), _>(std::io::Error::from(kind))
            });
            assert_eq!(result.unwrap_err().kind(), kind);
            assert_eq!(attempts, 1);
        }
    }

    #[test]
    fn runtime_spawn_busy_retry_is_platform_specific() {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        let code = libc::ETXTBSY;
        #[cfg(not(any(target_os = "linux", target_os = "macos")))]
        let code = 26;
        let mut attempts = 0;
        let result = spawn_runtime(|| {
            attempts += 1;
            if attempts == 1 {
                Err(std::io::Error::from_raw_os_error(code))
            } else {
                Ok(())
            }
        });
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        {
            assert!(result.is_ok());
            assert_eq!(attempts, 2);
        }
        #[cfg(not(any(target_os = "linux", target_os = "macos")))]
        {
            assert_eq!(result.unwrap_err().raw_os_error(), Some(code));
            assert_eq!(attempts, 1);
        }
    }

    #[test]
    fn runtime_spawn_success_does_not_make_an_extra_attempt() {
        let mut attempts = 0;
        let result = spawn_runtime(|| {
            attempts += 1;
            Ok::<_, std::io::Error>("started")
        });
        assert_eq!(result.unwrap(), "started");
        assert_eq!(attempts, 1);
    }

    #[cfg(unix)]
    #[test]
    fn selected_client_never_materializes_or_calls_ensure_even_after_reopen_or_external_failure() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let config = root.path().join("config");
        let (managed, _) = Managed::prepare(&resources, &data, &config);
        assert!(managed.select_role(Role::Client, 0).unwrap().is_none());
        assert_eq!(managed.stop().err().unwrap(), "MANAGED_SERVICE_NOT_OWNED");
        assert_eq!(managed.start().err().unwrap(), "MANAGED_SERVICE_NOT_OWNED");
        assert!(managed.read_status().unwrap().lifecycle.is_none());
        fs::create_dir(&config).unwrap();
        fs::write(
            config.join("central.json"),
            b"unreadable external connection",
        )
        .unwrap();
        let (reopened, launch) = Managed::prepare(&resources, &data, &config);
        assert!(launch.is_none());
        assert_eq!(reopened.status().mode, "external");
        for path in [
            "managed-runtimes",
            "managed-central/ensure-count",
            "managed-central/identity.token",
            "managed-central/state/tasks.sqlite3",
            "managed-central/state/project-fixture",
        ] {
            assert!(!data.join(path).exists(), "{path}");
        }
    }

    #[cfg(unix)]
    #[test]
    fn provider_and_both_prepare_one_service_and_preserve_existing_blank_installations() {
        for role in (0..10).flat_map(|_| [Role::Provider, Role::Both]) {
            let root = tempfile::tempdir().unwrap();
            let resources = startup_fixture(root.path());
            let data = root.path().join("app");
            let config = root.path().join("config");
            let (managed, _) = Managed::prepare(&resources, &data, &config);
            assert!(!data.join("managed-runtimes").exists());
            assert!(managed.select_role(role, 0).unwrap().is_some());
            assert!(managed.select_role(role, 0).is_err());
            let original = managed.startup_role().unwrap();
            let files = [
                "installation.json",
                "identity.token",
                "state/tasks.sqlite3",
                "state/project-fixture",
                "service-count",
            ];
            let before: Vec<_> = files
                .iter()
                .map(|path| fs::read(data.join("managed-central").join(path)).unwrap())
                .collect();
            assert_eq!(
                fs::read_to_string(data.join("managed-central/ensure-count")).unwrap(),
                "ensure\n"
            );
            // Both uses the existing external configuration path after local readiness.
            if role == Role::Both {
                fs::create_dir(&config).unwrap();
                fs::write(config.join("central.json"), b"original-external-config").unwrap();
            }
            let (reopened, launch) = Managed::prepare(&resources, &data, &config);
            assert!(launch.is_some());
            assert_eq!(reopened.startup_role().unwrap(), original);
            assert_eq!(
                reopened.status().mode,
                if role == Role::Both {
                    "external"
                } else {
                    "managed"
                }
            );
            assert_eq!(
                fs::read_to_string(data.join("managed-central/ensure-count")).unwrap(),
                "ensure\nensure\n"
            );
            for (path, original) in files.iter().zip(before) {
                assert_eq!(
                    fs::read(data.join("managed-central").join(path)).unwrap(),
                    original
                );
            }
            if role == Role::Both {
                assert_eq!(
                    fs::read(config.join("central.json")).unwrap(),
                    b"original-external-config"
                );
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn interruption_after_persistence_resumes_provider_once_and_client_never_ensures() {
        for role in [Role::Client, Role::Provider, Role::Both] {
            let root = tempfile::tempdir().unwrap();
            let resources = startup_fixture(root.path());
            let data = root.path().join("app");
            let config = root.path().join("config");
            let original = startup_role::select(&data, &config, role, 0).unwrap();
            // Simulate process exit after publication, before the first ensure.
            assert!(!data.join("managed-runtimes").exists());
            let (reopened, launch) = Managed::prepare(&resources, &data, &config);
            assert_eq!(reopened.startup_role().unwrap(), original);
            assert_eq!(launch.is_some(), role.provides());
            if role.provides() {
                assert_eq!(
                    fs::read_to_string(data.join("managed-central/ensure-count")).unwrap(),
                    "ensure\n"
                );
                assert_eq!(
                    fs::read_to_string(data.join("managed-central/service-count")).unwrap(),
                    "service\n"
                );
            } else {
                assert!(!data.join("managed-central").exists());
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn old_installation_is_adopted_without_changing_its_manifest_identity_journal_or_service() {
        for external in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let resources = startup_fixture(root.path());
            let data = root.path().join("app");
            let config = root.path().join("config");
            // An original installation predates the role record.
            let (executable, _) = materialize(&resources.join("managed-runtime"), &data).unwrap();
            assert!(
                spawn_fixture_runtime(&executable, &data.join("managed-central"), |_| {})
                    .unwrap()
                    .wait()
                    .unwrap()
                    .success()
            );
            if external {
                fs::create_dir(&config).unwrap();
                fs::write(config.join("central.json"), b"original-external").unwrap();
            }
            let names = [
                "installation.json",
                "identity.token",
                "state/tasks.sqlite3",
                "state/project-fixture",
                "service-count",
            ];
            let original: Vec<_> = names
                .iter()
                .map(|name| fs::read(data.join("managed-central").join(name)).unwrap())
                .collect();
            assert!(!startup_role::record_path(&data).exists());
            let (managed, launch) = Managed::prepare(&resources, &data, &config);
            assert!(launch.is_some());
            assert_eq!(
                managed.startup_role().unwrap().role,
                Some(if external { Role::Both } else { Role::Provider })
            );
            for (name, bytes) in names.iter().zip(original) {
                assert_eq!(
                    fs::read(data.join("managed-central").join(name)).unwrap(),
                    bytes
                );
            }
            if external {
                assert_eq!(
                    fs::read(config.join("central.json")).unwrap(),
                    b"original-external"
                );
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn failed_ensure_after_service_creation_reopens_the_same_service_and_original_data() {
        let root = tempfile::tempdir().unwrap();
        let resources = startup_fixture(root.path());
        let data = root.path().join("app");
        let config = root.path().join("config");
        let (managed, _) = Managed::prepare(&resources, &data, &config);
        fs::create_dir_all(data.join("managed-central")).unwrap();
        fs::write(data.join("managed-central/fail-once"), b"interrupt reply").unwrap();
        assert!(managed.select_role(Role::Provider, 0).is_err());
        assert!(managed.select_role(Role::Provider, 0).is_err());
        let reference = managed.startup_role().unwrap();
        let (reopened, launch) = Managed::prepare(&resources, &data, &config);
        assert!(launch.is_some());
        assert_eq!(reopened.startup_role().unwrap(), reference);
        assert_eq!(
            fs::read(data.join("managed-central/state/tasks.sqlite3")).unwrap(),
            b"original-journal"
        );
        assert_eq!(
            fs::read_to_string(data.join("managed-central/ensure-count")).unwrap(),
            "ensure\nensure\n"
        );
        assert_eq!(
            fs::read_to_string(data.join("managed-central/service-count")).unwrap(),
            "service\n"
        );
    }

    #[test]
    fn unreadable_role_record_refuses_before_any_package_or_python_request() {
        let root = tempfile::tempdir().unwrap();
        let config = root.path().join("config");
        startup_role::select(root.path(), &config, Role::Provider, 0).unwrap();
        fs::write(startup_role::record_path(root.path()), b"broken").unwrap();
        let (managed, launch) =
            Managed::prepare(&root.path().join("missing-resources"), root.path(), &config);
        assert!(launch.is_none());
        assert_eq!(
            managed.status().error.as_deref(),
            Some("MANAGED_CONFIGURATION_UNREADABLE")
        );
        assert!(!root.path().join("managed-runtimes").exists());
        assert!(!root.path().join("managed-central").exists());
    }

    #[test]
    fn produced_manifest_has_optional_build_identity_and_rejects_unknown_fields() {
        let mut document: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/runtime-manifest.json"
        ))
        .unwrap();
        let current: Manifest = serde_json::from_value(document.clone()).unwrap();
        assert_eq!(current._build_commit, Some("0".repeat(40)));
        document.as_object_mut().unwrap().remove("build_commit");
        let legacy: Manifest = serde_json::from_value(document.clone()).unwrap();
        assert_eq!(legacy._build_commit, None);
        document["unrecognized"] = serde_json::json!(true);
        assert!(serde_json::from_value::<Manifest>(document).is_err());
    }

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
            lifecycle: Some(Lifecycle::running()),
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
            executable: Mutex::new(None),
            data_dir: PathBuf::new(),
            resources: PathBuf::new(),
            app_data: PathBuf::new(),
            config: PathBuf::new(),
            role: Mutex::new(None),
            status: Mutex::new(identity_status()),
            serial: Mutex::new(()),
            runtime_facts: Mutex::new(RuntimeFacts::default()),
        };
        for name in ["same", "differs", "unknown_running"] {
            let launch: Launch = serde_json::from_value(golden[name].clone()).unwrap();
            managed.remember_runtime(&launch);
            let mut status = managed.status.lock().unwrap().clone();
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
        assert_eq!(managed.status().mode, "external");
        assert!(managed.status().background);
        assert!(!managed.status().ready && managed.status().error.is_some() && launch.is_none());
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
        assert_eq!(state.status().mode, "external");
        assert!(launch.is_none());
        assert!(!data.join("managed-central").exists());
        assert!(!data.join("managed-runtimes").exists());
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
