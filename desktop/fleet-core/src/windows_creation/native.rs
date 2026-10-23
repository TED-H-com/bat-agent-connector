use super::*;
use crate::{
    discovery::{self, DiscoveryConfig, MonitorIdentity, NativeIdentity},
    installation::{canonical_local, local_path},
    publication_state,
    windows::{current_login, MonitorMutex, WindowsMonitorObservation},
    windows_launcher::LauncherMutex,
    windows_publication_guards::WindowsPublicationGuards,
};
use std::rc::Rc;
pub struct WindowsSecurity {
    guards: WindowsPublicationGuards,
    discovery: DiscoveryConfig,
    role_record: PathBuf,
    #[cfg(test)]
    pub(super) held_for_fixture: bool,
    #[cfg(test)]
    pub(super) monitor_for_fixture: bool,
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
        let file = if path == self.role_record {
            publication_state::with_startup_role_file(path, |file| {
                file.try_clone().map_err(|_| UNPROVEN)
            })?
        } else {
            publication_state::checked_handle(path, private, child_absent)?
        };
        if canonical_local(path)? != path {
            return Err(UNPROVEN);
        }
        Ok(file)
    }
    fn effect_held(&self) -> Result<()> {
        self.held()?;
        #[cfg(test)]
        if self.monitor_for_fixture {
            return Ok(());
        }
        if !self.guards.monitor_held() {
            return Err("MONITOR_GUARD_REQUIRED");
        }
        Ok(())
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        file_identity(file)
    }
}
pub(super) use crate::publication_state::file_identity;

/// Keep this platform and its guards on one synchronous native blocking thread.
/// Context is native-discovered; no paths or ownership claims come from IPC.
pub type WindowsCreationPlatform = super::platform::CreationPlatform<WindowsSecurity>;
pub type WindowsCreationWriter<'a> = super::writer::CreationWriter<'a, WindowsSecurity>;
impl WindowsSecurity {
    #[doc(hidden)]
    pub fn for_context(input: &NativeInput, executable: &Path) -> Result<Self> {
        Self::for_input(
            &input.pointer,
            &input.kit_root,
            input.role_record.clone(),
            &input.roaming,
            executable,
        )
    }

    fn for_input(
        pointer: &Path,
        kit: &Path,
        role_record: PathBuf,
        roaming: &Path,
        executable: &Path,
    ) -> Result<Self> {
        let login = current_login()?;
        let roaming = canonical_local(roaming)?;
        let executable = canonical_local(executable)?;
        if !roaming.is_dir() || !executable.is_file() {
            return Err(UNPROVEN);
        }
        let text = |p: &Path| p.to_str().map(str::to_owned).ok_or(UNPROVEN);
        let native = NativeIdentity::new(&text(&executable)?, &text(pointer)?)?;
        let discovery = DiscoveryConfig::new(
            &text(&kit.join("client"))?,
            &text(&super::context::slot_path(
                pointer,
                kit,
                Artifact::Inventory,
            ))?,
            &text(&super::context::slot_path(
                pointer,
                kit,
                Artifact::ProfileIndex,
            ))?,
            &roaming,
            Some(native),
        )?;
        Ok(Self {
            guards: WindowsPublicationGuards::new(login),
            discovery,
            role_record,
            #[cfg(test)]
            held_for_fixture: false,
            #[cfg(test)]
            monitor_for_fixture: false,
        })
    }
}
impl WindowsCreationPlatform {
    pub fn new(
        expected: ProvisioningReceipt,
        roaming: PathBuf,
        executable: PathBuf,
        startup: &Path,
        user: &Path,
    ) -> Result<Self> {
        let security = WindowsSecurity::for_input(
            &expected.scope.pointer,
            &expected.scope.kit_root,
            expected.scope.startup_role_record.clone(),
            &roaming,
            &executable,
        )?;
        Self::with_security(expected, roaming, startup, user, security)
    }
    pub fn from_native(
        input: NativeInput,
        executable: &Path,
        verify: &dyn Fn() -> Result<()>,
        mint: &mut dyn FnMut() -> [String; 2],
    ) -> Result<Self> {
        let security = WindowsSecurity::for_input(
            &input.pointer,
            &input.kit_root,
            input.role_record.clone(),
            &input.roaming,
            executable,
        )?;
        Self::from_native_input(input, security, verify, mint)
    }
}
