use super::*;
use crate::{
    discovery::{self, DiscoveryConfig, MonitorIdentity, NativeIdentity},
    installation::{canonical_local, local_path},
    publication_state,
    windows::{current_login, MonitorMutex, WindowsMonitorObservation},
    windows_launcher::LauncherMutex,
    windows_publication_guards::WindowsPublicationGuards,
};
use std::{os::windows::io::AsRawHandle, rc::Rc};
use windows_sys::Win32::Storage::FileSystem::{
    FileIdInfo, GetFileInformationByHandleEx, FILE_ID_INFO,
};

pub struct WindowsSecurity {
    guards: WindowsPublicationGuards,
    discovery: DiscoveryConfig,
    #[cfg(test)]
    pub(super) held_for_fixture: bool,
}
impl Security for WindowsSecurity {
    type LauncherGuard = Rc<LauncherMutex>;
    type MonitorGuard = Rc<MonitorMutex>;
    fn launcher_guard(&self, verify: &dyn Fn() -> Result<()>) -> Result<Self::LauncherGuard> {
        self.guards.launcher_guard(verify)
    }
    fn monitor_guard(&self, verify: &dyn Fn() -> Result<()>) -> Result<Self::MonitorGuard> {
        self.guards.monitor_guard(verify)
    }
    fn unconfigured(&self, pointer: &Path, roaming: &Path) -> Result<()> {
        crate::unconfigured::verify_absence(pointer, roaming, &WindowsMonitorObservation)
    }
    fn discover(&self) -> Result<Option<MonitorIdentity>> {
        discovery::discover(&self.discovery, &WindowsMonitorObservation)
    }
    fn unpublished(&self, journal: &Path) -> Result<()> {
        WindowsPublicationGuards::observed(None, journal, &WindowsMonitorObservation).map(|_| ())
    }

    fn validate_path(&self, path: &Path) -> Result<()> {
        use std::path::{Component, Prefix};
        if !path.is_absolute()
            || !local_path(path)
            || !matches!(path.components().next(),
            Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_)))
        {
            return Err(UNPROVEN);
        }
        for c in path.components() {
            if let Component::Normal(name) = c {
                let name = name.to_str().ok_or(UNPROVEN)?;
                let base = name.split('.').next().unwrap_or("").to_ascii_uppercase();
                if name.contains([':', '\0'])
                    || name.ends_with(['.', ' '])
                    || ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"].contains(&base.as_str())
                    || ["COM", "LPT"].iter().any(|p| {
                        base.strip_prefix(p).is_some_and(|n| {
                            ["1", "2", "3", "4", "5", "6", "7", "8", "9", "¹", "²", "³"]
                                .contains(&n)
                        })
                    })
                {
                    return Err(UNPROVEN);
                }
            }
        }
        Ok(())
    }
    fn login(&self) -> Result<LoginIdentity> {
        current_login()
    }
    fn held(&self) -> Result<()> {
        #[cfg(test)]
        if self.held_for_fixture {
            return Ok(());
        }
        self.guards.launcher().map(|_| ())
    }
    fn open(&self, path: &Path, private: bool, child_absent: bool) -> Result<File> {
        let file = publication_state::checked_handle(path, private, child_absent)?;
        if canonical_local(path)? != path {
            return Err(UNPROVEN);
        }
        Ok(file)
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        file_identity(file)
    }
}
pub(super) fn file_identity(file: &File) -> Result<ObjectIdentity> {
    let mut info = FILE_ID_INFO::default();
    if unsafe {
        GetFileInformationByHandleEx(
            file.as_raw_handle(),
            FileIdInfo,
            (&mut info as *mut FILE_ID_INFO).cast(),
            std::mem::size_of::<FILE_ID_INFO>() as u32,
        )
    } == 0
    {
        return Err(UNPROVEN);
    }
    Ok(ObjectIdentity {
        volume: info.VolumeSerialNumber,
        file_id: info
            .FileId
            .Identifier
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
    })
}

/// Keep this platform and its guards on one synchronous native blocking thread.
/// Context is native-discovered; no paths or ownership claims come from IPC.
pub type WindowsCreationPlatform = super::platform::CreationPlatform<WindowsSecurity>;
impl WindowsCreationPlatform {
    pub fn new(
        expected: ProvisioningReceipt,
        roaming: PathBuf,
        executable: PathBuf,
        startup: &Path,
        user: &Path,
    ) -> Result<Self> {
        let login = current_login()?;
        let roaming = canonical_local(&roaming)?;
        let executable = canonical_local(&executable)?;
        if !roaming.is_dir() || !executable.is_file() {
            return Err(UNPROVEN);
        }
        let text = |p: &Path| p.to_str().map(str::to_owned).ok_or(UNPROVEN);
        let native = NativeIdentity::new(&text(&executable)?, &text(&expected.scope.pointer)?)?;
        let discovery = DiscoveryConfig::new(
            &text(&expected.scope.kit_root.join("client"))?,
            &text(expected.path(Artifact::Inventory))?,
            &text(expected.path(Artifact::ProfileIndex))?,
            &roaming,
            Some(native),
        )?;
        Self::with_security(
            expected,
            roaming,
            startup,
            user,
            WindowsSecurity {
                guards: WindowsPublicationGuards::new(login),
                discovery,
                #[cfg(test)]
                held_for_fixture: false,
            },
        )
    }
}
