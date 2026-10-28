//! Read-only monitor discovery. Returned identity never authorizes a later process effect.
use crate::{
    configuration::Configuration,
    ownership::{epoch_valid, origin_state, sid_valid, ticks_valid, ProcessState},
    process_adapter::{legacy_created, LoginIdentity, ProcessSnapshot},
    strict_json, Result,
};
use serde::{Deserialize, Serialize};
use std::{
    fs::File,
    io::Read,
    path::{Path, PathBuf},
};

/// Lexical Win32 absolute path, independent of the platform running pure tests. No CWD,
/// device namespaces, ADS, trailing-dot aliases or drive-relative guesses.
pub fn windows_path(value: &str) -> Result<String> {
    if value.is_empty()
        || value.encode_utf16().count() > 32767
        || value
            .chars()
            .any(|c| c.is_control() || ['*', '?', '"', '<', '>', '|'].contains(&c))
    {
        return Err("OWNER_UNPROVEN");
    }
    let value = value.replace('/', "\\");
    let (root, tail) = if value.as_bytes().get(1) == Some(&b':')
        && value.as_bytes().get(2) == Some(&b'\\')
        && value.as_bytes()[0].is_ascii_alphabetic()
    {
        (value[..3].to_owned(), &value[3..])
    } else if let Some(tail) = value.strip_prefix("\\\\") {
        let mut parts = tail.splitn(3, '\\');
        let server = parts.next().unwrap_or("");
        let share = parts.next().unwrap_or("");
        if server.is_empty()
            || share.is_empty()
            || [server, share]
                .iter()
                .any(|s| s.ends_with(['.', ' ']) || s.contains(':') || *s == ".")
        {
            return Err("OWNER_UNPROVEN");
        }
        (
            format!("\\\\{server}\\{share}\\"),
            parts.next().unwrap_or(""),
        )
    } else {
        return Err("OWNER_UNPROVEN");
    };
    let mut parts = Vec::new();
    for part in tail.split('\\') {
        match part {
            "" | "." => (),
            ".." => {
                if parts.pop().is_none() {
                    return Err("OWNER_UNPROVEN");
                }
            }
            p => {
                if p.contains(':') || p.ends_with(['.', ' ']) {
                    return Err("OWNER_UNPROVEN");
                }
                parts.push(p);
            }
        }
    }
    Ok(format!("{root}{}", parts.join("\\")))
}
fn same_path(a: &str, b: &str) -> bool {
    windows_path(a)
        .ok()
        .zip(windows_path(b).ok())
        .is_some_and(|(a, b)| a.eq_ignore_ascii_case(&b))
}
fn join(base: &str, part: &str) -> Result<String> {
    windows_path(&format!("{}\\{part}", base.trim_end_matches(['\\', '/'])))
}
fn absolute_input(value: &str) -> bool {
    let bytes = value.as_bytes();
    value.starts_with(['\\', '/']) || bytes.get(1) == Some(&b':')
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Backend {
    Powershell,
    Rust,
}
fn powershell() -> Backend {
    Backend::Powershell
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MonitorRecord {
    pub pid: u32,
    pub created: String,
    pub executable: String,
    pub owner_sid: String,
    pub session_id: u32,
    pub instance: String,
    #[serde(default = "powershell")]
    pub backend: Backend,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub script: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub arguments: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inventory_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile_index_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_filetime: Option<String>,
}
impl MonitorRecord {
    pub fn parse(bytes: &[u8]) -> Result<Self> {
        let document = strict_json::parse(bytes, 262144)?;
        for name in [
            "backend",
            "script",
            "arguments",
            "inventory_path",
            "profile_index_path",
            "created_filetime",
        ] {
            if document.get(name).is_some_and(serde_json::Value::is_null) {
                return Err("OWNER_UNPROVEN");
            }
        }
        let value: Self = serde_json::from_value(document).map_err(|_| "OWNER_UNPROVEN")?;
        if value.pid == 0
            || !ticks_valid(&value.created)
            || !value.created.parse::<u64>().is_ok_and(|n| {
                n > crate::process_adapter::FILETIME_EPOCH_TICKS
                    && n <= crate::process_adapter::MAX_DATETIME_TICKS
                    && n.is_multiple_of(10)
            })
            || !sid_valid(&value.owner_sid)
            || !epoch_valid(&value.instance)
            || windows_path(&value.executable).is_err()
            || value.arguments.as_ref().is_some_and(|a| {
                a.len() > 128 || a.iter().any(|s| s.contains('\0') || s.len() > 32768)
            })
            || value.created_filetime.as_ref().is_some_and(|n| {
                !ticks_valid(n)
                    || n.parse().ok().and_then(|n| legacy_created(n).ok()).as_ref()
                        != Some(&value.created)
            })
        {
            return Err("OWNER_UNPROVEN");
        }
        for path in [
            &value.script,
            &value.inventory_path,
            &value.profile_index_path,
        ]
        .into_iter()
        .flatten()
        {
            windows_path(path)?;
        }
        if value.backend == Backend::Powershell && value.script.is_none()
            || value.backend == Backend::Rust
                && (value.arguments.is_none()
                    || value.inventory_path.is_none()
                    || value.profile_index_path.is_none()
                    || value.created_filetime.is_none()
                    || value.script.is_some())
        {
            return Err("OWNER_UNPROVEN");
        }
        Ok(value)
    }
    fn birth_matches(&self, p: &ProcessSnapshot) -> bool {
        legacy_created(p.created_filetime).is_ok_and(|n| n == self.created)
            && self
                .created_filetime
                .as_ref()
                .is_none_or(|n| n == &p.created_filetime.to_string())
    }
}
#[derive(Clone)]
pub struct NativeIdentity {
    executable: String,
    arguments: Vec<String>,
}
impl NativeIdentity {
    pub fn new(executable: &str, fleet_config: &str) -> Result<Self> {
        Ok(Self {
            executable: windows_path(executable)?,
            arguments: vec![
                "--fleet-supervisor".into(),
                "--fleet-config".into(),
                windows_path(fleet_config)?,
            ],
        })
    }
    pub fn executable(&self) -> &str {
        &self.executable
    }
    pub fn arguments(&self) -> &[String] {
        &self.arguments
    }
    pub fn fleet_config(&self) -> &str {
        &self.arguments[2]
    }
}
pub struct DiscoveryConfig {
    kit: String,
    inventory: String,
    index: String,
    directories: Vec<PathBuf>,
    native: Option<NativeIdentity>,
}
impl DiscoveryConfig {
    /// Native trusted values; the record paths always use both standard BAT directories.
    pub fn new(
        kit: &str,
        inventory: &str,
        index: &str,
        roaming: &Path,
        native: Option<NativeIdentity>,
    ) -> Result<Self> {
        if !roaming.is_absolute() {
            return Err("OWNER_UNPROVEN");
        }
        Ok(Self {
            kit: windows_path(kit)?,
            inventory: windows_path(inventory)?,
            index: windows_path(index)?,
            directories: vec![
                roaming.join("BetterAgentTerminal"),
                roaming.join("org.tonyq.better-agent-terminal"),
            ],
            native,
        })
    }
    pub fn from_configuration(
        kit: &str,
        configuration: &Configuration,
        roaming: &Path,
        native: Option<NativeIdentity>,
    ) -> Result<Self> {
        Self::new(
            kit,
            configuration
                .inventory_path()
                .to_str()
                .ok_or("OWNER_UNPROVEN")?,
            configuration
                .profile_index_path()
                .to_str()
                .ok_or("OWNER_UNPROVEN")?,
            roaming,
            native,
        )
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ownership {
    CurrentLogin,
    OtherLogin,
}
#[derive(Clone)]
pub struct MonitorIdentity {
    pub process: ProcessSnapshot,
    pub backend: Backend,
    pub instance: Option<String>,
    pub ownership: Ownership,
    pub directories: Vec<PathBuf>,
    pub legacy: bool,
    /// Original validated record, retained for normal-exit migration and byte-bound revalidation.
    pub record: Option<MonitorRecord>,
}
/// Native observations only. Complete candidate enumeration may positively exclude other accounts;
/// inaccessible potential candidates must produce an error, never an empty vector.
pub trait Observation {
    fn current_login(&self) -> Result<LoginIdentity>;
    fn state(&self, pid: u32) -> ProcessState;
    fn observe(&self, pid: u32) -> Result<Option<ProcessSnapshot>>;
    fn legacy_candidates(&self) -> Result<Vec<ProcessSnapshot>>;
}

fn resolve_path(argument: &str, stored: Option<&str>, exact_saved_argv: bool) -> Result<String> {
    if absolute_input(argument) {
        return windows_path(argument);
    }
    if !exact_saved_argv {
        return Err("OWNER_UNPROVEN");
    }
    windows_path(stored.ok_or("OWNER_UNPROVEN")?)
}
fn ps_script(
    arguments: &[String],
    record: Option<&MonitorRecord>,
) -> Result<Option<(String, usize)>> {
    let Some(at) = arguments
        .iter()
        .position(|a| a.eq_ignore_ascii_case("-File"))
    else {
        return Ok(None);
    };
    let mut i = 0;
    while i < at {
        let flag = arguments[i].to_ascii_lowercase();
        match flag.as_str() {
            "-noprofile" | "-noninteractive" | "-nologo" | "-sta" | "-mta" => i += 1,
            "-executionpolicy" | "-windowstyle" | "-inputformat" | "-outputformat" => {
                if i + 1 >= at || arguments[i + 1].starts_with('-') {
                    return Err("OWNER_UNPROVEN");
                }
                i += 2;
            }
            _ => return Err("OWNER_UNPROVEN"),
        }
    }
    let arg = arguments.get(at + 1).ok_or("OWNER_UNPROVEN")?;
    let exact = record
        .and_then(|r| r.arguments.as_ref())
        .is_some_and(|a| a == arguments);
    let path = resolve_path(arg, record.and_then(|r| r.script.as_deref()), exact)?;
    Ok(Some((path, at + 2)))
}
fn ps_paths(
    config: &DiscoveryConfig,
    p: &ProcessSnapshot,
    record: Option<&MonitorRecord>,
) -> Result<bool> {
    let Some((script, tail)) = ps_script(&p.arguments, record)? else {
        return Ok(false);
    };
    if !same_path(&script, &join(&config.kit, "bat-connect.ps1")?) {
        return Ok(false);
    }
    if record.is_some_and(|r| r.script.as_deref().is_none_or(|s| !same_path(s, &script))) {
        return Err("OWNER_UNPROVEN");
    }
    let exact = record
        .and_then(|r| r.arguments.as_ref())
        .is_some_and(|a| a == &p.arguments);
    let default_inventory = join(&config.kit, "fleet-inventory.json")?;
    let default_index = join(&config.kit, "bat-profiles\\index.json")?;
    let mut inventory = None;
    let mut index = None;
    let mut at = tail;
    while at < p.arguments.len() {
        let arg = &p.arguments[at];
        let (field, value) = if arg.starts_with('-') {
            let (name, inline) = arg
                .split_once(':')
                .map_or((arg.as_str(), None), |(a, b)| (a, Some(b)));
            let field = if name.eq_ignore_ascii_case("-InventoryPath") {
                0
            } else if name.eq_ignore_ascii_case("-ProfileIndexPath") {
                1
            } else {
                return Err("OWNER_UNPROVEN");
            };
            let value = match inline {
                Some(v) => v,
                None => {
                    at += 1;
                    p.arguments.get(at).ok_or("OWNER_UNPROVEN")?
                }
            };
            (field, value)
        } else {
            (if inventory.is_none() { 0 } else { 1 }, arg.as_str())
        };
        if value.is_empty() || value.starts_with('-') {
            return Err("OWNER_UNPROVEN");
        }
        let slot = if field == 0 {
            &mut inventory
        } else {
            &mut index
        };
        if slot.is_some() {
            return Err("OWNER_UNPROVEN");
        }
        *slot = Some(value.to_owned());
        at += 1;
    }
    let actual_inventory = resolve_path(
        inventory.as_deref().unwrap_or(&default_inventory),
        record.and_then(|r| r.inventory_path.as_deref()),
        exact,
    )?;
    let actual_index = match index {
        Some(ref value) => resolve_path(
            value,
            record.and_then(|r| r.profile_index_path.as_deref()),
            exact,
        )?,
        None => default_index.clone(),
    };
    if (record.is_some_and(|r| r.profile_index_path.is_none()) || index.is_none())
        && (!same_path(&actual_inventory, &default_inventory)
            || !same_path(&actual_index, &default_index))
    {
        return Err("OWNER_UNPROVEN");
    }
    if let Some(record) = record {
        if record
            .inventory_path
            .as_deref()
            .is_some_and(|v| !same_path(v, &actual_inventory))
            || record
                .profile_index_path
                .as_deref()
                .is_some_and(|v| !same_path(v, &actual_index))
        {
            return Err("OWNER_UNPROVEN");
        }
    }
    if !same_path(&actual_inventory, &config.inventory) || !same_path(&actual_index, &config.index)
    {
        return Err("MONITOR_INVENTORY_MISMATCH");
    }
    Ok(true)
}
fn validate_record(
    config: &DiscoveryConfig,
    r: &MonitorRecord,
    p: &ProcessSnapshot,
    login: &LoginIdentity,
) -> Result<()> {
    if !p.valid()
        || p.pid != r.pid
        || !r.birth_matches(p)
        || p.login.owner_sid != r.owner_sid
        || p.login.session_id != r.session_id
        || r.owner_sid != login.owner_sid
        || !same_path(&p.executable, &r.executable)
        || r.arguments.as_ref().is_some_and(|a| a != &p.arguments)
    {
        return Err("OWNER_UNPROVEN");
    }
    match r.backend {
        Backend::Powershell => {
            let filename = p.executable.rsplit(['\\', '/']).next().unwrap_or("");
            if !["powershell.exe", "pwsh.exe"]
                .iter()
                .any(|n| filename.eq_ignore_ascii_case(n))
                || !ps_paths(config, p, Some(r))?
            {
                return Err("OWNER_UNPROVEN");
            }
        }
        Backend::Rust => {
            let expected = config.native.as_ref().ok_or("OWNER_UNPROVEN")?;
            if !same_path(&p.executable, &expected.executable)
                || p.arguments != expected.arguments
                || !r
                    .inventory_path
                    .as_deref()
                    .is_some_and(|v| same_path(v, &config.inventory))
                || !r
                    .profile_index_path
                    .as_deref()
                    .is_some_and(|v| same_path(v, &config.index))
            {
                return Err("OWNER_UNPROVEN");
            }
        }
    }
    Ok(())
}
fn read(path: &Path) -> Result<Option<Vec<u8>>> {
    match std::fs::symlink_metadata(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Ok(m) if m.is_file() && !m.file_type().is_symlink() => (),
        _ => return Err("OWNER_UNPROVEN"),
    };
    let file = File::open(path).map_err(|_| "OWNER_UNPROVEN")?;
    if !file.metadata().map_err(|_| "OWNER_UNPROVEN")?.is_file() {
        return Err("OWNER_UNPROVEN");
    }
    let mut bytes = Vec::new();
    file.take(262145)
        .read_to_end(&mut bytes)
        .map_err(|_| "OWNER_UNPROVEN")?;
    if bytes.len() > 262144 {
        return Err("OWNER_UNPROVEN");
    }
    Ok(Some(bytes))
}
fn combine(found: &mut Option<MonitorIdentity>, next: MonitorIdentity) -> Result<()> {
    if let Some(found) = found {
        if found.process != next.process
            || found.backend != next.backend
            || found.instance != next.instance
        {
            return Err("MONITOR_CONFLICT");
        }
        found.directories.extend(next.directories);
    } else {
        *found = Some(next);
    }
    Ok(())
}
/// Enumeration cannot establish ownership of an inaccessible process.
/// Only complete positive observations participate in discovery.
pub fn candidate(result: Result<Option<ProcessSnapshot>>) -> Option<ProcessSnapshot> {
    match result {
        Ok(Some(process)) if process.valid() => Some(process),
        Ok(None) => None,
        _ => {
            crate::monitor_log::event("CANDIDATE_SKIPPED_OWNER_UNPROVEN");
            None
        }
    }
}
pub fn discover(
    config: &DiscoveryConfig,
    observations: &impl Observation,
) -> Result<Option<MonitorIdentity>> {
    let login = observations.current_login()?;
    if !login.valid() {
        return Err("OWNER_UNPROVEN");
    }
    let mut inputs = Vec::new();
    let mut found = None;
    for directory in &config.directories {
        let path = directory.join("fleet-monitor.json");
        let bytes = read(&path)?;
        if let Some(bytes) = &bytes {
            let record = MonitorRecord::parse(bytes)?;
            match origin_state(record.pid, &record.created, &observations.state(record.pid)) {
                ProcessState::Dead => (),
                ProcessState::Unknown => {
                    crate::monitor_log::event("CANDIDATE_SKIPPED_STATE_UNKNOWN");
                }
                ProcessState::Live { .. } => {
                    if let Some(process) = candidate(observations.observe(record.pid)) {
                        if record.birth_matches(&process) {
                            validate_record(config, &record, &process, &login)?;
                            let ownership = if process.login.session_id == login.session_id {
                                Ownership::CurrentLogin
                            } else {
                                Ownership::OtherLogin
                            };
                            combine(
                                &mut found,
                                MonitorIdentity {
                                    process,
                                    backend: record.backend,
                                    instance: Some(record.instance.clone()),
                                    ownership,
                                    directories: vec![directory.clone()],
                                    legacy: false,
                                    record: Some(record),
                                },
                            )?;
                        }
                    }
                }
            }
        }
        inputs.push((path, bytes));
    }
    let candidates = observations.legacy_candidates()?;
    if candidates.len() > 1024 {
        return Err("OWNER_UNPROVEN");
    }
    for process in candidates {
        let Some(process) = candidate(Ok(Some(process))) else {
            continue;
        };
        if process.login.owner_sid != login.owner_sid {
            continue;
        }
        let filename = process.executable.rsplit(['\\', '/']).next().unwrap_or("");
        if !filename.eq_ignore_ascii_case("powershell.exe")
            && !filename.eq_ignore_ascii_case("pwsh.exe")
        {
            continue;
        }
        if found.as_ref().is_some_and(|m| m.process == process) {
            continue;
        }
        if ps_paths(config, &process, None)? {
            let ownership = if process.login.session_id == login.session_id {
                Ownership::CurrentLogin
            } else {
                Ownership::OtherLogin
            };
            combine(
                &mut found,
                MonitorIdentity {
                    process,
                    backend: Backend::Powershell,
                    instance: None,
                    ownership,
                    directories: vec![],
                    legacy: true,
                    record: None,
                },
            )?;
        }
    }
    if let Some(owner) = &found {
        if observations.observe(owner.process.pid)?.as_ref() != Some(&owner.process) {
            return Err("OWNER_UNPROVEN");
        }
    }
    for (path, bytes) in inputs {
        if read(&path)? != bytes {
            return Err("OWNER_UNPROVEN");
        }
    }
    Ok(found)
}
