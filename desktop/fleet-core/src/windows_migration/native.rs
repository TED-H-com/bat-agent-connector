use super::{current_installation, ps_arguments, ps_environment, wait_for_owner};
use crate::{
    configuration::{Configuration, Paths},
    discovery::{self, Backend, DiscoveryConfig, MonitorIdentity, NativeIdentity},
    installation::{canonical_local, local_path, Snapshot},
    migration::{Owner, Platform},
    monitor_launch::{self, Outcome},
    process_adapter::{HeldProcess, LoginIdentity},
    supervisor_control,
    windows::{current_login, MonitorMutex, WindowsMonitorObservation, WindowsProcess},
    windows_launcher::LauncherMutex,
    windows_monitor_launch::WindowsLaunch,
    windows_publication_guards::WindowsPublicationGuards,
    windows_startup::Codec,
    Result,
};
use std::{
    os::windows::process::CommandExt,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    rc::Rc,
    time::Instant,
};
use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

fn text(path: &Path) -> Result<&str> {
    path.to_str().ok_or("INSTALLATION_INVALID")
}

struct Legacy {
    powershell: PathBuf,
    script: PathBuf,
}
impl Legacy {
    fn load(client: &Path, system: &Path) -> Result<Self> {
        let powershell = canonical_local(&system.join("WindowsPowerShell/v1.0/powershell.exe"))?;
        let script = canonical_local(&client.join("bat-connect.ps1"))?;
        if !powershell.is_file()
            || !powershell.starts_with(system)
            || !script.is_file()
            || !script.starts_with(client)
        {
            return Err("INSTALLATION_INVALID");
        }
        Ok(Self { powershell, script })
    }
}

/// Keep on one dedicated native blocking thread. Returned guards, COM and launch
/// readback never move between executor threads. No paths or commands come from IPC.
pub struct WindowsMigration {
    installation: Snapshot,
    configuration: Configuration,
    discovery: DiscoveryConfig,
    native: NativeIdentity,
    codec: Codec,
    system: PathBuf,
    legacy: Option<Legacy>,
    launch_journal: PathBuf,
    quit_file: PathBuf,
    login: LoginIdentity,
    guards: WindowsPublicationGuards,
}
impl WindowsMigration {
    /// `paths` must be built for installation.client_root() by trusted native
    /// discovery. `system_directory` comes from GetSystemDirectoryW, and
    /// `current_executable` from the installed process, never PATH search.
    pub fn new(
        installation: Snapshot,
        paths: Paths,
        roaming: PathBuf,
        quit_file: PathBuf,
        current_executable: PathBuf,
        system_directory: PathBuf,
    ) -> Result<Self> {
        installation.verify_current()?;
        let configuration = Configuration::load(paths)?;
        let roaming = canonical_local(&roaming)?;
        if !roaming.is_dir() || !quit_file.is_absolute() || !local_path(&quit_file) {
            return Err("INSTALLATION_INVALID");
        }
        let executable = canonical_local(&current_executable)?;
        let system = canonical_local(&system_directory)?;
        if !executable.is_file() || !system.is_dir() {
            return Err("INSTALLATION_INVALID");
        }
        let legacy = Legacy::load(installation.client_root(), &system);
        let legacy = if installation.backend() == Backend::Powershell {
            Some(legacy?)
        } else {
            // Only an explicit legacy transition needs these external scripts.
            legacy.ok()
        };
        let native = NativeIdentity::new(text(&executable)?, text(installation.path())?)?;
        let discovery = DiscoveryConfig::from_configuration(
            text(installation.client_root())?,
            &configuration,
            &roaming,
            Some(native.clone()),
        )?;
        let codec = Codec::new(
            installation.client_root(),
            &executable,
            installation.path(),
            &system,
        )?;
        let login = current_login()?;
        let out = Self {
            installation,
            configuration,
            discovery,
            native,
            codec,
            system,
            legacy,
            launch_journal: roaming.join("bat-fleet-monitor-launch.json"),
            quit_file,
            guards: WindowsPublicationGuards::new(login.clone()),
            login,
        };
        out.current_installation()?;
        Ok(out)
    }
    fn launcher(&self) -> Result<Rc<LauncherMutex>> {
        self.guards.launcher()
    }
    /// Reload intended config bytes; the old snapshot still binds the installation
    /// layout, while the migration Store checks original/proposed exact payloads.
    fn current_installation(&self) -> Result<Snapshot> {
        if current_login()? != self.login {
            return Err("OTHER_LOGIN_OWNER");
        }
        self.configuration.verify_current()?;
        if !self.configuration.issues.is_empty() {
            return Err("CONFIGURATION_INVALID");
        }
        let current = current_installation(&self.installation)?;
        if canonical_local(Path::new(self.native.executable()))?
            != Path::new(self.native.executable())
            || canonical_local(&self.system)? != self.system
        {
            return Err("INSTALLATION_CHANGED");
        }
        if self.legacy.is_some() || current.backend() == Backend::Powershell {
            self.legacy()?;
        }
        Ok(current)
    }
    fn legacy(&self) -> Result<&Legacy> {
        let expected = self
            .legacy
            .as_ref()
            .ok_or("POWERSHELL_ADAPTER_UNAVAILABLE")?;
        let current = Legacy::load(self.installation.client_root(), &self.system)?;
        if current.script != expected.script || current.powershell != expected.powershell {
            return Err("INSTALLATION_CHANGED");
        }
        Ok(expected)
    }
    fn verify_launch(&self, current: &Snapshot, backend: Backend) -> Result<()> {
        self.launcher()?;
        self.current_installation()?;
        current.verify_current()?;
        if current.backend() != backend {
            return Err("MIGRATION_BACKEND_CHANGED");
        }
        Ok(())
    }
    fn owner(&self) -> Result<Option<MonitorIdentity>> {
        discovery::discover(&self.discovery, &WindowsMonitorObservation)
    }
    fn absence(&self) -> Result<()> {
        if self.owner()?.is_some() {
            return Err("MIGRATION_OWNER_CHANGED");
        }
        WindowsPublicationGuards::observed(None, &self.launch_journal, &WindowsMonitorObservation)
            .map(|_| ())
    }
    fn launch_rust(&self, current: &Snapshot, launcher: &LauncherMutex) -> Result<()> {
        let mut launch = WindowsLaunch::new(
            launcher,
            current,
            &self.configuration,
            &self.discovery,
            &self.native,
        );
        match monitor_launch::ensure(
            &self.launch_journal,
            &self.native,
            self.configuration.binding(),
            &mut launch,
        )? {
            Outcome::Running(_) => self.verify_launch(current, Backend::Rust),
            Outcome::Started(child) => {
                let began = Instant::now();
                wait_for_owner(
                    &child,
                    Backend::Rust,
                    &self.login,
                    || {
                        self.verify_launch(current, Backend::Rust)?;
                        let owner = self.owner()?;
                        self.verify_launch(current, Backend::Rust)?;
                        Ok(owner)
                    },
                    || began.elapsed(),
                    std::thread::sleep,
                )
            }
        }
    }
    fn launch_powershell(&self, current: &Snapshot) -> Result<()> {
        // The migration journal already saved LaunchRequested. This low-level hook
        // may run only once; all errors after spawn remain unknown and never kill.
        let mutex = MonitorMutex::try_acquire()?.ok_or("MONITOR_ALREADY_RUNNING")?;
        if mutex.login() != &self.login {
            return Err("OTHER_LOGIN_OWNER");
        }
        self.verify_launch(current, Backend::Powershell)?;
        self.absence()?;
        let legacy = self.legacy()?;
        let mut command = Command::new(&legacy.powershell);
        command
            .args(ps_arguments(
                &legacy.script,
                self.configuration.inventory_path(),
                self.configuration.profile_index_path(),
            )?)
            .current_dir(current.client_root())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .env_clear()
            .envs(ps_environment(&self.system, std::env::vars_os()));
        self.verify_launch(current, Backend::Powershell)?;
        drop(mutex); // Child acquires the SAME account monitor mutex; launcher stays held.
        let child = command.spawn().map_err(|_| "MIGRATION_LAUNCH_UNKNOWN")?;
        let held = WindowsProcess::from_child(&child).map_err(|_| "MIGRATION_LAUNCH_UNKNOWN")?;
        let expected = held.snapshot().map_err(|_| "MIGRATION_LAUNCH_UNKNOWN")?;
        let expected_args = ps_arguments(
            &legacy.script,
            self.configuration.inventory_path(),
            self.configuration.profile_index_path(),
        )?
        .into_iter()
        .map(|s| s.into_string().map_err(|_| "INSTALLATION_INVALID"))
        .collect::<Result<Vec<_>>>()?;
        if expected.login != self.login
            || !expected
                .executable
                .eq_ignore_ascii_case(legacy.powershell.to_str().ok_or("INSTALLATION_INVALID")?)
            || expected.arguments != expected_args
        {
            return Err("MIGRATION_LAUNCH_UNKNOWN");
        }
        let began = Instant::now();
        let result = wait_for_owner(
            &expected,
            Backend::Powershell,
            &self.login,
            || {
                self.verify_launch(current, Backend::Powershell)?;
                let owner = self.owner()?;
                self.verify_launch(current, Backend::Powershell)?;
                Ok(owner)
            },
            || began.elapsed(),
            std::thread::sleep,
        );
        // Keep both original handles throughout readback; dropping them never kills the owner.
        drop(held);
        drop(child);
        result
    }
}
impl Platform for WindowsMigration {
    type LauncherGuard = Rc<LauncherMutex>;
    type MonitorGuard = Rc<MonitorMutex>;
    fn launcher_guard(&mut self) -> Result<Self::LauncherGuard> {
        self.guards
            .launcher_guard(|| self.current_installation().map(|_| ()))
    }
    fn monitor_guard(&mut self) -> Result<Self::MonitorGuard> {
        self.guards.monitor_guard(|| {
            self.current_installation()?;
            self.absence()
        })
    }
    fn login(&self) -> Result<LoginIdentity> {
        self.launcher()?;
        Ok(self.login.clone())
    }
    fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
        self.launcher()?;
        self.current_installation()?;
        let owner = WindowsPublicationGuards::observed(
            self.owner()?,
            &self.launch_journal,
            &WindowsMonitorObservation,
        )?;
        self.current_installation()?;
        Ok(owner)
    }
    fn request_quit(&mut self, expected: &Owner) -> Result<()> {
        self.launcher()?;
        self.current_installation()?;
        let owner = self.owner()?.ok_or("MIGRATION_OWNER_CHANGED")?;
        if !expected.matches(&owner, &self.login) {
            return Err("MIGRATION_OWNER_CHANGED");
        }
        supervisor_control::request_quit(
            &self.configuration,
            &self.discovery,
            &WindowsMonitorObservation,
            &self.quit_file,
            &owner,
        )
    }
    fn launch(&mut self, backend: Backend) -> Result<()> {
        let launcher = self.launcher()?;
        if self.guards.monitor_held() {
            return Err("MONITOR_STILL_HELD");
        }
        let current = self.current_installation()?;
        self.verify_launch(&current, backend)?;
        match backend {
            Backend::Rust => self.launch_rust(&current, &launcher),
            Backend::Powershell => self.launch_powershell(&current),
        }
    }
    fn validate_config(&self, bytes: &[u8], backend: Backend) -> Result<()> {
        self.launcher()?;
        self.current_installation()?;
        if backend == Backend::Powershell {
            self.legacy()?;
        }
        self.installation.validate_payload(bytes, backend)
    }
    fn classify_shortcut(&self, bytes: &[u8]) -> Result<Backend> {
        self.launcher()?;
        self.current_installation()?;
        self.codec.classify(bytes)
    }
    fn shortcut(&self, backend: Backend) -> Result<Vec<u8>> {
        self.launcher()?;
        self.current_installation()?;
        self.codec.render(backend)
    }
}
