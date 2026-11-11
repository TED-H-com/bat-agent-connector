//! Windows-only fixed trusted Tailscale installation. No PATH lookup or login/network CLI.
use crate::{
    tailscale::{self, LoginState, Spawn},
    Result,
};
use std::{
    fs::{File, OpenOptions},
    os::windows::{
        fs::{MetadataExt, OpenOptionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::Stdio,
};
use windows::Win32::{
    System::Com::CoTaskMemFree,
    UI::Shell::{
        FOLDERID_ProgramFiles, FOLDERID_RoamingAppData, SHGetKnownFolderPath, KF_FLAG_DONT_VERIFY,
    },
};

pub fn roaming_directory() -> Result<PathBuf> {
    known_folder(&FOLDERID_RoamingAppData)
}
fn known_folder(id: &windows::core::GUID) -> Result<PathBuf> {
    let raw = unsafe { SHGetKnownFolderPath(id, KF_FLAG_DONT_VERIFY, None) }
        .map_err(|_| "TAILSCALE_STORAGE_UNAVAILABLE")?;
    let text = unsafe { raw.to_string() }.map_err(|_| "TAILSCALE_STORAGE_UNAVAILABLE");
    unsafe { CoTaskMemFree(Some(raw.0.cast())) };
    let path = PathBuf::from(text?);
    if !crate::installation::local_path(&path) {
        return Err("TAILSCALE_INSTALLATION_UNPROVEN");
    }
    crate::configuration::absolute(&path)
}
fn plain(path: &Path, directory: bool) -> Result<bool> {
    match path.symlink_metadata() {
        Ok(m)
            if m.file_type().is_symlink()
                || m.file_attributes() & 0x400 != 0
                || if directory { !m.is_dir() } else { !m.is_file() } =>
        {
            Err("TAILSCALE_INSTALLATION_UNPROVEN")
        }
        Ok(_) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err("TAILSCALE_INSTALLATION_UNPROVEN"),
    }
}
fn executable(path: &Path) -> Result<Option<File>> {
    for parent in path.ancestors().skip(1) {
        if !plain(parent, true)? {
            return Ok(None);
        }
    }
    if !plain(path, false)? {
        return Ok(None);
    }
    // Retain read handles denying write/delete while commands use these exact installed files.
    let file = OpenOptions::new()
        .read(true)
        .share_mode(1)
        .custom_flags(0x0020_0000)
        .open(path)
        .map_err(|_| "TAILSCALE_INSTALLATION_UNPROVEN")?;
    let meta = file
        .metadata()
        .map_err(|_| "TAILSCALE_INSTALLATION_UNPROVEN")?;
    if !meta.is_file() || meta.file_attributes() & 0x400 != 0 {
        return Err("TAILSCALE_INSTALLATION_UNPROVEN");
    }
    Ok(Some(file))
}
pub struct Installation {
    directory: PathBuf,
    cli: Option<File>,
    app: Option<File>,
}
impl Installation {
    pub fn load() -> Result<Self> {
        Self::at(&known_folder(&FOLDERID_ProgramFiles)?)
    }
    fn at(program_files: &Path) -> Result<Self> {
        let directory = crate::configuration::absolute(&program_files.join("Tailscale"))?;
        let cli = executable(&directory.join("tailscale.exe"))?;
        let app = executable(&directory.join("tailscale-ipn.exe"))?;
        Ok(Self {
            directory,
            cli,
            app,
        })
    }
    pub fn installed(&self) -> &'static str {
        match (self.cli.is_some(), self.app.is_some()) {
            (true, true) => "available",
            (false, false) => "missing",
            _ => "incomplete",
        }
    }
    pub fn can_open(&self) -> bool {
        self.app.is_some()
    }
    pub async fn status(&self) -> LoginState {
        if self.cli.is_none() {
            return LoginState::Unknown;
        }
        let mut command =
            crate::hidden_process::hidden_tokio_command(self.directory.join("tailscale.exe"));
        command
            .args(["status", "--json"])
            .env_clear()
            .envs(crate::monitor_launch::environment(std::env::vars_os()))
            .current_dir(&self.directory)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .creation_flags(crate::hidden_process::hidden_flags(0x0800_0000));
        tailscale::query_status(command).await
    }

    pub fn open(&self) -> Spawn {
        if self.app.is_none() {
            return Spawn::NotStarted;
        }
        // No arguments: only the vendor's existing tray application is opened.
        let child = crate::hidden_process::hidden_command(self.directory.join("tailscale-ipn.exe"))
            .env_clear()
            .envs(crate::monitor_launch::environment(std::env::vars_os()))
            .current_dir(&self.directory)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(crate::hidden_process::hidden_flags(0x0000_0400))
            .spawn(); // CREATE_UNICODE_ENVIRONMENT; hide only any console; retain the vendor tray UI.
        match child {
            Ok(_held_child) => Spawn::Started,
            Err(_) => Spawn::NotStarted,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_fixed_standard_installation_paths_are_discovered() {
        let root = std::env::temp_dir().join(format!(
            "bac-tailscale-install-{:032x}",
            rand::random::<u128>()
        ));
        std::fs::create_dir_all(root.join("Tailscale")).unwrap();
        assert_eq!(Installation::at(&root).unwrap().installed(), "missing");
        std::fs::write(root.join("tailscale-ipn.exe"), b"not a candidate").unwrap();
        assert!(!Installation::at(&root).unwrap().can_open());
        std::fs::write(
            root.join("Tailscale/tailscale.exe"),
            b"synthetic never executed",
        )
        .unwrap();
        assert_eq!(Installation::at(&root).unwrap().installed(), "incomplete");
        std::fs::write(
            root.join("Tailscale/tailscale-ipn.exe"),
            b"synthetic never executed",
        )
        .unwrap();
        let installation = Installation::at(&root).unwrap();
        assert_eq!(installation.installed(), "available");
        // Write/delete exclusion is held until the native effect/status lifetime ends.
        assert!(OpenOptions::new()
            .write(true)
            .open(root.join("Tailscale/tailscale-ipn.exe"))
            .is_err());
        drop(installation);
        std::fs::remove_dir_all(root).unwrap();
    }
}
