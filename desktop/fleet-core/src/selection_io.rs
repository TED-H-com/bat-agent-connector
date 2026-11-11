//! Fixed local preference storage. Call from a blocking native worker, not the WebView thread.
use crate::{
    configuration::{data_directory, Configuration},
    digest,
    selection::{launch_plan, Choices, Preferences},
    Result,
};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

#[cfg(windows)]
use crate::windows::PreferenceLock;

#[cfg(not(windows))]
struct PreferenceLock {
    _file: File,
}
#[cfg(not(windows))]
impl PreferenceLock {
    fn acquire(path: &Path) -> Result<Self> {
        let mut name = path.as_os_str().to_owned();
        name.push(".lock");
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(Path::new(&name))
            .map_err(|_| "SELECTION_BUSY")?;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(Self { _file: file }),
                Err(std::fs::TryLockError::WouldBlock) if std::time::Instant::now() < deadline => {
                    std::thread::sleep(std::time::Duration::from_millis(20))
                }
                Err(_) => return Err("SELECTION_BUSY"),
            }
        }
    }
}

fn read(path: &Path) -> Result<Option<Vec<u8>>> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("SELECTION_UNREADABLE"),
    };
    if !file
        .metadata()
        .map_err(|_| "SELECTION_UNREADABLE")?
        .is_file()
    {
        return Err("SELECTION_INVALID");
    }
    let mut bytes = Vec::new();
    file.take(262145)
        .read_to_end(&mut bytes)
        .map_err(|_| "SELECTION_UNREADABLE")?;
    if bytes.len() > 262144 {
        return Err("SELECTION_INVALID");
    }
    Ok(Some(bytes))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReadFailure {
    Unreadable,
    Invalid,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FileObservation {
    Absent,
    Present { bytes: Vec<u8>, digest: String },
    Unreadable(ReadFailure),
}
impl FileObservation {
    fn capture(path: &Path) -> Self {
        match read(path) {
            Ok(None) if matches!(path.symlink_metadata(), Err(e) if e.kind() == std::io::ErrorKind::NotFound) => {
                Self::Absent
            }
            Ok(None) => Self::Unreadable(ReadFailure::Unreadable),
            Ok(Some(bytes)) => Self::Present {
                digest: digest(&bytes),
                bytes,
            },
            Err("SELECTION_INVALID") => Self::Unreadable(ReadFailure::Invalid),
            Err(_) => Self::Unreadable(ReadFailure::Unreadable),
        }
    }
    fn bytes(&self) -> Result<Option<&[u8]>> {
        match self {
            Self::Absent => Ok(None),
            Self::Present { bytes, .. } => Ok(Some(bytes)),
            Self::Unreadable(ReadFailure::Invalid) => Err("SELECTION_INVALID"),
            Self::Unreadable(ReadFailure::Unreadable) => Err("SELECTION_UNREADABLE"),
        }
    }
}
/// Native-only sampled evidence, not write authority or a locked transaction.
/// Presence is independent of JSON validity and the compatibility Snapshot revision.
pub struct Observation {
    primary: FileObservation,
    legacy: FileObservation,
    configuration_binding: String,
    directory: PathBuf,
}
impl Observation {
    pub fn primary(&self) -> &FileObservation {
        &self.primary
    }
    pub fn legacy(&self) -> &FileObservation {
        &self.legacy
    }
    pub fn configuration_binding(&self) -> &str {
        &self.configuration_binding
    }
    pub fn directory(&self) -> &Path {
        &self.directory
    }
    /// Parse the captured bytes with the ordinary selection rules, without rereading slots.
    pub fn snapshot(&self, configuration: &Configuration) -> Result<Snapshot> {
        if self.configuration_binding != configuration.binding() {
            return Err("CONFIGURATION_CHANGED");
        }
        configuration.verify_current()?;
        let raw = self.primary.bytes()?.map(Vec::from);
        let legacy = if raw.is_none() {
            self.legacy.bytes()?.map(Vec::from)
        } else {
            None
        };
        Store::snapshot_from_bytes(configuration, &self.directory, raw, legacy)
    }
}

#[derive(Clone)]
pub struct Snapshot {
    preferences: Preferences,
    configuration_binding: String,
    pub revision: String,
    directory: PathBuf,
    raw: Option<Vec<u8>>,
    legacy: Option<Vec<u8>>,
}
impl Snapshot {
    pub fn configuration_binding(&self) -> &str {
        &self.configuration_binding
    }
    pub fn directory(&self) -> &Path {
        &self.directory
    }
    /// Private raw/legacy bytes and resolved directory are part of the selection generation.
    pub fn same_snapshot(&self, other: &Self) -> bool {
        self.configuration_binding == other.configuration_binding
            && self.revision == other.revision
            && self.directory == other.directory
            && self.raw == other.raw
            && self.legacy == other.legacy
    }
    pub fn preferences(&self) -> &Preferences {
        &self.preferences
    }
}
pub struct Store {
    roaming: PathBuf,
}
/// Opaque native capability. Never reconstruct this from a WebView document.
pub struct ChoicePreview {
    expected: Snapshot,
    preferences: Preferences,
    epoch: Option<String>,
}
#[derive(serde::Serialize)]
pub struct ChoiceSummary {
    pub choices: Choices,
    pub effective_connections: Vec<String>,
    pub added_connections: Vec<String>,
    pub selection_revision: String,
    pub configuration_binding: String,
    pub monitor_epoch: Option<String>,
}
impl ChoicePreview {
    pub fn summary(&self, configuration: &Configuration) -> Result<ChoiceSummary> {
        if self.expected.configuration_binding != configuration.binding() {
            return Err("CONFIGURATION_CHANGED");
        }
        let plan = launch_plan(&configuration.inventory, &self.preferences);
        Ok(ChoiceSummary {
            added_connections: plan
                .connect
                .iter()
                .filter(|name| !self.preferences.connections.contains(name))
                .cloned()
                .collect(),
            effective_connections: plan.connect,
            choices: Choices {
                connections: self.preferences.connections.clone(),
                profiles: self.preferences.profiles.clone(),
                dashboard: self.preferences.dashboard,
            },
            selection_revision: self.expected.revision.clone(),
            configuration_binding: self.expected.configuration_binding.clone(),
            monitor_epoch: self.epoch.clone(),
        })
    }
}
impl Store {
    pub fn new(roaming: PathBuf) -> Self {
        Self { roaming }
    }
    fn paths(&self) -> Result<(PathBuf, PathBuf, PathBuf)> {
        let directory = data_directory(&self.roaming)?;
        Ok((
            directory.join("fleet-client.json"),
            directory.join("open-bat-selection.json"),
            directory,
        ))
    }
    fn read_at(
        configuration: &Configuration,
        directory: &Path,
        path: &Path,
        legacy_path: &Path,
    ) -> Result<Snapshot> {
        let raw = read(path)?;
        let legacy = if raw.is_none() {
            read(legacy_path)?
        } else {
            None
        };
        Self::snapshot_from_bytes(configuration, directory, raw, legacy)
    }
    fn snapshot_from_bytes(
        configuration: &Configuration,
        directory: &Path,
        raw: Option<Vec<u8>>,
        legacy: Option<Vec<u8>>,
    ) -> Result<Snapshot> {
        let preferences =
            Preferences::load(&configuration.inventory, raw.as_deref(), legacy.as_deref())?;
        Ok(Snapshot {
            preferences,
            configuration_binding: configuration.binding().into(),
            revision: digest(raw.as_deref().unwrap_or_default()),
            directory: directory.into(),
            raw,
            legacy,
        })
    }
    /// Observe both fixed slots without creating a directory or acquiring a write lock.
    /// A later writer must still recheck bytes, directory, configuration and owner.
    pub fn observe(&self, configuration: &Configuration) -> Result<Observation> {
        configuration.verify_current()?;
        let (path, legacy, directory) = self.paths()?;
        let observed = Observation {
            primary: FileObservation::capture(&path),
            legacy: FileObservation::capture(&legacy),
            configuration_binding: configuration.binding().into(),
            directory,
        };
        if data_directory(&self.roaming)? != observed.directory {
            return Err("DATA_DIRECTORY_CHANGED");
        }
        configuration.verify_current()?;
        Ok(observed)
    }
    pub fn read(&self, configuration: &Configuration) -> Result<Snapshot> {
        let (path, legacy, directory) = self.paths()?;
        std::fs::create_dir_all(&directory).map_err(|_| "SELECTION_UNREADABLE")?;
        let _guard = PreferenceLock::acquire(&path)?;
        configuration.verify_current()?;
        if data_directory(&self.roaming)? != directory {
            return Err("DATA_DIRECTORY_CHANGED");
        }
        let snapshot = Self::read_at(configuration, &directory, &path, &legacy)?;
        configuration.verify_current()?;
        Ok(snapshot)
    }
    /// `owner` must freshly prove the controllable monitor epoch (or positive absence),
    /// including current login ownership. Unknown evidence returns an error, never None.
    /// The original snapshot binds configuration, bytes and one-time legacy migration,
    /// beyond the PowerShell-compatible public preference revision.
    pub fn set_connections(
        &self,
        configuration: &Configuration,
        expected: &Snapshot,
        connections: &[String],
        epoch: Option<&str>,
        owner: impl FnMut() -> Result<Option<String>>,
    ) -> Result<Snapshot> {
        let preferences = expected
            .preferences
            .set_connections(&configuration.inventory, connections)?;
        self.write_preferences(configuration, expected, preferences, epoch, owner)
    }
    pub fn preview_choices(
        &self,
        configuration: &Configuration,
        expected: &Snapshot,
        choices: &Choices,
        epoch: Option<&str>,
    ) -> Result<ChoicePreview> {
        if epoch.is_some_and(|e| !crate::ownership::epoch_valid(e)) {
            return Err("INVALID_REQUEST");
        }
        configuration.verify_current()?;
        if expected.configuration_binding != configuration.binding() {
            return Err("CONFIGURATION_CHANGED");
        }
        if !expected.same_snapshot(&self.read(configuration)?) {
            return Err("SELECTION_CHANGED");
        }
        Ok(ChoicePreview {
            expected: expected.clone(),
            preferences: Preferences::choose(&configuration.inventory, choices)?,
            epoch: epoch.map(Into::into),
        })
    }
    pub fn apply_choices(
        &self,
        configuration: &Configuration,
        preview: &ChoicePreview,
        owner: impl FnMut() -> Result<Option<String>>,
    ) -> Result<Snapshot> {
        self.write_preferences(
            configuration,
            &preview.expected,
            preview.preferences.clone(),
            preview.epoch.as_deref(),
            owner,
        )
    }
    fn write_preferences(
        &self,
        configuration: &Configuration,
        expected: &Snapshot,
        preferences: Preferences,
        epoch: Option<&str>,
        mut owner: impl FnMut() -> Result<Option<String>>,
    ) -> Result<Snapshot> {
        if epoch.is_some_and(|value| !crate::ownership::epoch_valid(value)) {
            return Err("INVALID_REQUEST");
        }
        if configuration.binding() != expected.configuration_binding {
            return Err("CONFIGURATION_CHANGED");
        }
        let (path, legacy, directory) = self.paths()?;
        if directory != expected.directory {
            return Err("DATA_DIRECTORY_CHANGED");
        }
        let _guard = PreferenceLock::acquire(&path)?;
        let mut check = || -> Result<()> {
            configuration.verify_current()?;
            if !configuration.issues.is_empty() {
                return Err("CONFIGURATION_INVALID");
            }
            if data_directory(&self.roaming)? != directory {
                return Err("DATA_DIRECTORY_CHANGED");
            }
            let observed = owner()?;
            if observed
                .as_deref()
                .is_some_and(|value| !crate::ownership::epoch_valid(value))
            {
                return Err("OWNER_UNPROVEN");
            }
            if observed.as_deref() != epoch {
                return Err("MONITOR_EPOCH_CHANGED");
            }
            let current = Self::read_at(configuration, &directory, &path, &legacy)?;
            if current.raw != expected.raw || current.legacy != expected.legacy {
                return Err("SELECTION_CHANGED");
            }
            configuration.verify_current()?;
            if data_directory(&self.roaming)? != directory {
                return Err("DATA_DIRECTORY_CHANGED");
            }
            Ok(())
        };
        check()?;
        let bytes = preferences.persisted(&configuration.inventory)?;
        let (temporary, mut file) = temporary(&directory)?;
        let result = (|| {
            file.write_all(&bytes)
                .map_err(|_| "SELECTION_UNAVAILABLE")?;
            file.sync_all().map_err(|_| "SELECTION_UNAVAILABLE")?;
            check()?;
            drop(file);
            // Never delete the old preferences first. Rename replaces in one OS operation.
            std::fs::rename(&temporary, &path).map_err(|_| "SELECTION_UNAVAILABLE")?;
            Ok(Snapshot {
                preferences,
                configuration_binding: configuration.binding().into(),
                revision: digest(&bytes),
                directory: directory.clone(),
                raw: Some(bytes),
                legacy: None,
            })
        })();
        if result.is_err() {
            let _ = std::fs::remove_file(&temporary);
        }
        result
    }
}

fn temporary(directory: &Path) -> Result<(PathBuf, File)> {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    for _ in 0..64 {
        let path = directory.join(format!(
            "fleet-client.{}.{}.tmp",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err("SELECTION_UNAVAILABLE"),
        }
    }
    Err("SELECTION_UNAVAILABLE")
}
