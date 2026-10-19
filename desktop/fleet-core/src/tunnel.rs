//! Fixed SSH launch plans and durable local effects. No WebView-supplied commands or paths.
//! A platform adapter must retain the account monitor mutex and each launched process handle.
use crate::{
    configuration::{data_directory, Configuration},
    inventory::endpoint,
    ownership::epoch_valid,
    process_adapter::{HeldProcess, ProcessSnapshot, TunnelRecord},
    selection::launch_plan,
    selection_io::{Snapshot, Store},
    Result,
};
use serde_json::json;
use std::{
    fs::OpenOptions,
    io::{Read, Write},
    path::{Path, PathBuf},
};

/// Opaque plan derived from a validated configuration and the exact applied selection.
/// Arguments contain private inventory data and must never be projected to the UI.
pub struct Plan {
    name: String,
    epoch: String,
    configuration_binding: String,
    selection: Snapshot,
    arguments: Vec<String>,
    local: std::net::SocketAddrV4,
}
impl Plan {
    pub fn new(
        configuration: &Configuration,
        selected: &Snapshot,
        name: &str,
        alias: &str,
        epoch: &str,
    ) -> Result<Self> {
        configuration.verify_current()?;
        if selected.configuration_binding() != configuration.binding() || !epoch_valid(epoch) {
            return Err("CONFIGURATION_CHANGED");
        }
        if !launch_plan(&configuration.inventory, selected.preferences())
            .connect
            .iter()
            .any(|id| id == name)
        {
            return Err("CONNECTION_NOT_SELECTED");
        }
        if configuration.issues.iter().any(|issue| issue.host == name) {
            return Err("CONFIGURATION_INVALID");
        }
        let entry = if name == "connector" {
            let entry = configuration.inventory.connector();
            if entry["ssh_alias"] != alias {
                return Err("ROUTE_NOT_CONFIGURED");
            }
            entry
        } else {
            let entry = configuration
                .inventory
                .hosts()
                .iter()
                .find(|entry| entry["name"] == name)
                .ok_or("CONNECTION_NOT_CONFIGURED")?;
            if !entry["routes"]
                .as_array()
                .unwrap()
                .iter()
                .any(|route| route["alias"] == alias)
            {
                return Err("ROUTE_NOT_CONFIGURED");
            }
            entry
        };
        let local = endpoint(entry["local"].as_str().unwrap(), true)?;
        let local = std::net::SocketAddrV4::new(
            local.address.parse().map_err(|_| "INVENTORY_INVALID")?,
            local.port,
        );
        Ok(Self {
            name: name.into(),
            epoch: epoch.into(),
            configuration_binding: configuration.binding().into(),
            selection: selected.clone(),
            local,
            arguments: vec![
                "-N".into(),
                "-o".into(),
                "ExitOnForwardFailure=yes".into(),
                "-o".into(),
                "BatchMode=yes".into(),
                "-o".into(),
                "ServerAliveInterval=15".into(),
                "-o".into(),
                "ServerAliveCountMax=10".into(),
                "-o".into(),
                "TCPKeepAlive=yes".into(),
                "-o".into(),
                format!("SetEnv=BAT_FLEET_MONITOR={epoch}"),
                "-L".into(),
                format!("{local}:{}", entry["target"].as_str().unwrap()),
                alias.into(),
            ],
        })
    }
    pub fn name(&self) -> &str {
        &self.name
    }
    #[cfg(windows)]
    pub(crate) fn epoch(&self) -> &str {
        &self.epoch
    }
    /// Native-only argv, never a status document or log entry.
    pub fn arguments(&self) -> &[String] {
        &self.arguments
    }
    pub fn local(&self) -> std::net::SocketAddrV4 {
        self.local
    }
}

/// Err from spawn distinguishes a positive failure before launch from an uncertain child.
pub enum SpawnFailure {
    NotStarted,
    RolledBack,
    Unconfirmed,
}
pub trait Platform {
    type Child: HeldProcess;
    /// Prove the current monitor record, held mutex, login, incarnation and exact epoch.
    /// Unknown evidence is an error. This function is called again before every publication.
    fn owner(&mut self, epoch: &str) -> Result<ProcessSnapshot>;
    /// Prove there is no existing listener; a timeout/access failure is not absence.
    fn endpoint_free(&mut self, local: std::net::SocketAddrV4) -> Result<()>;
    /// Spawn only the configured system SSH executable with this plan's exact argv.
    /// Keep the launch handle; never reopen a PID to roll back a failed publication.
    fn spawn(&mut self, plan: &Plan) -> std::result::Result<Self::Child, SpawnFailure>;
    /// Exact system SSH image path used by spawn, obtained from trusted OS configuration.
    fn executable(&self) -> &Path;
}

pub struct Context<'a> {
    pub configuration: &'a Configuration,
    pub store: &'a Store,
    pub selection: &'a Snapshot,
    pub roaming: &'a Path,
}
impl Context<'_> {
    fn verify(&self, plan: &Plan, directory: &Path) -> Result<()> {
        self.configuration.verify_current()?;
        if self.configuration.binding() != plan.configuration_binding
            || data_directory(self.roaming)? != directory
            || self.selection.directory() != directory
        {
            return Err("CONFIGURATION_CHANGED");
        }
        if !self
            .selection
            .same_snapshot(&self.store.read(self.configuration)?)
            || !self.selection.same_snapshot(&plan.selection)
        {
            return Err("SELECTION_CHANGED");
        }
        Ok(())
    }
}

fn create(path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary = path.with_file_name(format!("fleet-stage-{:032x}.tmp", rand::random::<u128>()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&temporary)
        .map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?;
    let result = (|| {
        file.write_all(bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?;
        // Publish a complete, flushed file without replacing any existing path. NTFS
        // supports this; filesystems without hard links fail before publication.
        std::fs::hard_link(&temporary, path).map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")
    })();
    drop(file);
    let _ = std::fs::remove_file(temporary);
    result
}
fn absent(path: &Path) -> Result<()> {
    match std::fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        _ => Err("TUNNEL_START_UNSETTLED"),
    }
}
fn remove_exact(path: &Path, expected: &[u8]) -> Result<()> {
    // The account monitor mutex excludes cooperative owners; retain an externally changed file.
    let metadata = std::fs::symlink_metadata(path).map_err(|_| "TUNNEL_RECORD_CHANGED")?;
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|_| "TUNNEL_RECORD_CHANGED")?
        .take(expected.len() as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "TUNNEL_RECORD_CHANGED")?;
    if !metadata.is_file() || metadata.len() != expected.len() as u64 || bytes != expected {
        return Err("TUNNEL_RECORD_CHANGED");
    }
    std::fs::remove_file(path).map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")
}

/// The runtime keeps the child handle and private record until a proven exit/owned stop.
pub struct Launched<C> {
    pub child: C,
    pub record: TunnelRecord,
    pub record_path: PathBuf,
    pub record_bytes: Vec<u8>,
}

/// Save an intent before spawn. A crash or uncertain rollback leaves it in place and
/// blocks another launch. A completed owner record is never overwritten on retry.
pub fn launch<P: Platform>(
    context: &Context<'_>,
    plan: &Plan,
    platform: &mut P,
) -> Result<Launched<P::Child>> {
    let directory = data_directory(context.roaming)?;
    context.verify(plan, &directory)?;
    let parent = platform.owner(&plan.epoch)?;
    if !parent.valid() || !platform.executable().is_absolute() {
        return Err("OWNER_UNPROVEN");
    }
    let owners = directory.join("fleet-tunnel-owners");
    let intents = directory.join("fleet-tunnel-intents");
    std::fs::create_dir_all(&owners).map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?;
    std::fs::create_dir_all(&intents).map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?;
    let record_path = owners.join(format!("{}.json", plan.name));
    let intent_path = intents.join(format!("{}.json", plan.name));
    absent(&record_path)?;
    absent(&intent_path)?;
    platform.endpoint_free(plan.local)?;
    let intent = serde_json::to_vec(&json!({"schema_version":1,"monitor_instance":plan.epoch,
        "monitor_pid":parent.pid,"monitor_created":crate::process_adapter::legacy_created(parent.created_filetime)?,
        "configuration_binding":plan.configuration_binding,"selection_revision":context.selection.revision,
        "name":plan.name,"arguments":plan.arguments})).map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?;
    create(&intent_path, &intent)?;
    // Failure here is positively before spawn, so only this exact intent can be removed.
    let before = (|| {
        context.verify(plan, &directory)?;
        if platform.owner(&plan.epoch)? != parent {
            return Err("OWNER_UNPROVEN");
        }
        absent(&record_path)?;
        platform.endpoint_free(plan.local)?;
        context.verify(plan, &directory)
    })();
    if let Err(error) = before {
        remove_exact(&intent_path, &intent)?;
        return Err(error);
    }
    let mut child = match platform.spawn(plan) {
        Ok(child) => child,
        Err(SpawnFailure::NotStarted | SpawnFailure::RolledBack) => {
            remove_exact(&intent_path, &intent)?;
            return Err("TUNNEL_START_FAILED");
        }
        Err(SpawnFailure::Unconfirmed) => return Err("TUNNEL_START_UNSETTLED"),
    };
    let publish = (|| {
        let first = child.snapshot()?;
        if first.arguments != plan.arguments
            || first.login != parent.login
            || !windows_executable_eq(
                &first.executable,
                platform.executable().to_str().ok_or("OWNER_UNPROVEN")?,
            )
        {
            return Err("OWNER_UNPROVEN");
        }
        let record = TunnelRecord::from_snapshot(&first, &parent)?;
        context.verify(plan, &directory)?;
        if platform.owner(&plan.epoch)? != parent || child.snapshot()? != first {
            return Err("OWNER_UNPROVEN");
        }
        let bytes =
            serde_json::to_vec(&record.document()?).map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?;
        context.verify(plan, &directory)?;
        create(&record_path, &bytes)?;
        Ok((record, bytes))
    })();
    match publish {
        Ok((record, record_bytes)) => {
            // A leftover intent after publication is conservative. Never kill a published
            // child just because intent cleanup failed; recovery reads the owner record first.
            let _ = remove_exact(&intent_path, &intent);
            Ok(Launched {
                child,
                record,
                record_path,
                record_bytes,
            })
        }
        Err(error) => {
            // The retained launch handle is the only permissible rollback target. Keep
            // intent if exit is unconfirmed or a partial owner publication exists.
            if child.terminate_and_wait().is_ok() && absent(&record_path).is_ok() {
                remove_exact(&intent_path, &intent)?;
            }
            Err(error)
        }
    }
}

/// Compare Windows image paths, allowing only separator spelling and ASCII case differences.
/// Do not resolve aliases, dot components or links when proving executable identity.
pub(crate) fn windows_executable_eq(actual: &str, expected: &str) -> bool {
    actual
        .replace('/', "\\")
        .eq_ignore_ascii_case(&expected.replace('/', "\\"))
}

#[cfg(test)]
mod executable_path_tests {
    use super::windows_executable_eq;

    #[test]
    fn windows_image_paths_allow_separators_and_ascii_case_only() {
        let expected = r"C:\Windows\System32\OpenSSH\ssh.exe";
        let mixed = r"C:\Windows\system32\OpenSSH/ssh.exe";
        assert!(windows_executable_eq(mixed, expected));
        assert!(windows_executable_eq(expected, mixed));
        for different in [
            r"C:\Windows\System32\OpenSSH\scp.exe",
            r"C:\Windows\System32\Other\ssh.exe",
            r"C:\Windows\System32\OpenSSH\..\OpenSSH\ssh.exe",
        ] {
            assert!(!windows_executable_eq(different, expected));
        }
    }
}
