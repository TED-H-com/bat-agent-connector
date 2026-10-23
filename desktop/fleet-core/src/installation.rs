//! Trusted local installation configuration; never receives paths from the WebView.
use crate::{discovery::Backend, strict_json, Result};
use serde::Deserialize;
use std::{
    fs::File,
    io::Read,
    path::{Path, PathBuf},
};

fn legacy_backend() -> Backend {
    Backend::Powershell
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    kit_root: PathBuf,
    #[serde(default = "legacy_backend")]
    backend: Backend,
}

/// Validate bounded pointer bytes against their declared root/backend, without
/// reading the filesystem or establishing creation authority. Uses the same
/// Document schema and legacy backend default as Snapshot.
pub fn validate_pointer(bytes: &[u8], root: &Path, backend: Backend) -> Result<()> {
    let document: Document = serde_json::from_value(strict_json::parse(bytes, 16_384)?)
        .map_err(|_| "INSTALLATION_INVALID")?;
    if document.kit_root != root || document.backend != backend {
        return Err("INSTALLATION_CHANGED");
    }
    Ok(())
}

fn read(path: &Path) -> Result<Vec<u8>> {
    let metadata = std::fs::symlink_metadata(path).map_err(|_| "INSTALLATION_UNAVAILABLE")?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err("INSTALLATION_INVALID");
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err("INSTALLATION_INVALID");
        }
    }
    let file = File::open(path).map_err(|_| "INSTALLATION_UNAVAILABLE")?;
    if !file
        .metadata()
        .map_err(|_| "INSTALLATION_UNAVAILABLE")?
        .is_file()
    {
        return Err("INSTALLATION_INVALID");
    }
    let mut bytes = Vec::new();
    file.take(16385)
        .read_to_end(&mut bytes)
        .map_err(|_| "INSTALLATION_UNAVAILABLE")?;
    if bytes.len() > 16384 {
        return Err("INSTALLATION_TOO_LARGE");
    }
    Ok(bytes)
}

/// Excludes UNC/device namespaces and mapped network drives before and after canonicalization.
pub fn local_path(path: &Path) -> bool {
    #[cfg(windows)]
    {
        use std::path::{Component, Prefix};
        use windows_sys::Win32::Storage::FileSystem::GetDriveTypeW;
        let drive = match path.components().next() {
            Some(Component::Prefix(p)) => match p.kind() {
                Prefix::Disk(d) | Prefix::VerbatimDisk(d) => d,
                _ => return false,
            },
            _ => return false,
        };
        let root = [drive as u16, b':' as u16, b'\\' as u16, 0];
        matches!(unsafe { GetDriveTypeW(root.as_ptr()) }, 2 | 3 | 6)
    }
    #[cfg(not(windows))]
    {
        path.is_absolute()
    }
}

/// Normal drive spelling is shared with PowerShell binding and process argv evidence.
pub fn canonical_local(path: &Path) -> Result<PathBuf> {
    if !path.is_absolute() || !local_path(path) {
        return Err("INSTALLATION_INVALID");
    }
    let canonical = path
        .canonicalize()
        .map_err(|_| "INSTALLATION_UNAVAILABLE")?;
    if !local_path(&canonical) {
        return Err("INSTALLATION_INVALID");
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::{OsStrExt, OsStringExt};
        let units: Vec<_> = canonical.as_os_str().encode_wide().collect();
        let start = if units.starts_with(&[92, 92, 63, 92]) {
            4
        } else {
            0
        };
        let value = PathBuf::from(std::ffi::OsString::from_wide(&units[start..]));
        if !value.is_absolute() || !local_path(&value) {
            return Err("INSTALLATION_INVALID");
        }
        Ok(value)
    }
    #[cfg(not(windows))]
    {
        Ok(canonical)
    }
}

/// Keeps exact source bytes and resolved installation paths through a local effect.
/// This is configuration drift detection, not code signature or ancestor ACL attestation.
#[derive(Clone)]
pub struct Snapshot {
    declared_path: PathBuf,
    path: PathBuf,
    bytes: Vec<u8>,
    declared_root: PathBuf,
    root: PathBuf,
    client: PathBuf,
    script: Option<PathBuf>,
    backend: Backend,
}
impl Snapshot {
    pub fn load(path: &Path) -> Result<Self> {
        if !path.is_absolute() || !local_path(path) {
            return Err("INSTALLATION_INVALID");
        }
        // Reject a linked config leaf before resolving it; the trusted native caller owns its ancestors.
        let bytes = read(path)?;
        let declared_path = path.to_path_buf();
        let path = canonical_local(path)?;
        let document: Document = serde_json::from_value(strict_json::parse(&bytes, 16384)?)
            .map_err(|_| "INSTALLATION_INVALID")?;
        let root = canonical_local(&document.kit_root)?;
        let client = canonical_local(&root.join("client"))?;
        if !root.is_dir() || !client.is_dir() || !client.starts_with(&root) {
            return Err("INSTALLATION_INVALID");
        }
        let script = match document.backend {
            Backend::Powershell => Some(Self::facade(&client)?),
            Backend::Rust => None,
        };
        let snapshot = Self {
            declared_path,
            path,
            bytes,
            declared_root: document.kit_root,
            root,
            client,
            script,
            backend: document.backend,
        };
        snapshot.verify_current()?;
        Ok(snapshot)
    }
    pub fn verify_current(&self) -> Result<()> {
        if read(&self.declared_path).map_err(|_| "INSTALLATION_CHANGED")? != self.bytes {
            return Err("INSTALLATION_CHANGED");
        }
        self.verify_layout()
    }
    /// Migration validates original and proposed bytes while deliberately changing the backend.
    /// This checks the immutable installation, without comparing the intentionally replaced bytes.
    pub(crate) fn verify_layout(&self) -> Result<()> {
        if canonical_local(&self.declared_path).map_err(|_| "INSTALLATION_CHANGED")? != self.path
            || canonical_local(&self.declared_root).map_err(|_| "INSTALLATION_CHANGED")?
                != self.root
            || canonical_local(&self.root.join("client")).map_err(|_| "INSTALLATION_CHANGED")?
                != self.client
        {
            return Err("INSTALLATION_CHANGED");
        }
        if let Some(script) = &self.script {
            if Self::facade(&self.client).map_err(|_| "INSTALLATION_CHANGED")? != *script {
                return Err("INSTALLATION_CHANGED");
            }
        }
        Ok(())
    }
    fn facade(client: &Path) -> Result<PathBuf> {
        let script = canonical_local(&client.join("fleet-desktop.ps1"))?;
        if !script.is_file() || !script.starts_with(client) {
            return Err("INSTALLATION_INVALID");
        }
        Ok(script)
    }
    pub fn validate_payload(&self, bytes: &[u8], backend: Backend) -> Result<()> {
        self.verify_layout()?;
        let document: Document = serde_json::from_value(strict_json::parse(bytes, 16384)?)
            .map_err(|_| "INSTALLATION_INVALID")?;
        if document.backend != backend || canonical_local(&document.kit_root)? != self.root {
            return Err("INSTALLATION_CHANGED");
        }
        // A standalone Rust installation cannot opt into an absent legacy adapter.
        if backend == Backend::Powershell {
            Self::facade(&self.client)?;
        }
        Ok(())
    }
    pub fn backend_payload(&self, backend: Backend) -> Result<Vec<u8>> {
        self.verify_current()?;
        let mut value = strict_json::parse(&self.bytes, 16384)?;
        value["backend"] = serde_json::to_value(backend).map_err(|_| "INSTALLATION_INVALID")?;
        let bytes = serde_json::to_vec_pretty(&value).map_err(|_| "INSTALLATION_INVALID")?;
        self.validate_payload(&bytes, backend)?;
        Ok(bytes)
    }
    pub fn path(&self) -> &Path {
        &self.path
    }
    pub fn client_root(&self) -> &Path {
        &self.client
    }
    pub fn script(&self) -> Result<&Path> {
        self.script
            .as_deref()
            .ok_or("POWERSHELL_ADAPTER_UNAVAILABLE")
    }
    pub fn backend(&self) -> Backend {
        self.backend
    }
}

/// Executable-only arguments, parsed before Tauri single-instance or WebView initialization.
/// Native paths originate in a fixed launcher/autostart entry, never an IPC request.
#[derive(Debug, PartialEq, Eq)]
pub enum Entry {
    Dashboard,
    Login(PathBuf),
    Supervisor(PathBuf),
}
impl Entry {
    pub fn parse(arguments: &[std::ffi::OsString]) -> Result<Self> {
        if arguments.is_empty() {
            return Ok(Self::Dashboard);
        }
        if arguments.len() != 3 || arguments[1] != "--fleet-config" {
            return Err("INVALID_STARTUP_ARGUMENTS");
        }
        let path = PathBuf::from(&arguments[2]);
        if !path.is_absolute() || !local_path(&path) || path.as_os_str().is_empty() {
            return Err("INVALID_STARTUP_ARGUMENTS");
        }
        if arguments[0] == "--fleet-supervisor" {
            Ok(Self::Supervisor(path))
        } else if arguments[0] == "--fleet-login" {
            Ok(Self::Login(path))
        } else {
            Err("INVALID_STARTUP_ARGUMENTS")
        }
    }
}
