//! Fixed native monitor spawn. Caller holds the Kit-compatible launcher mutex on this thread.
use crate::{
    configuration::Configuration,
    discovery::{self, DiscoveryConfig, MonitorIdentity, NativeIdentity, Observation},
    installation::Snapshot,
    monitor_launch::Platform,
    ownership::ProcessState,
    process_adapter::{HeldProcess, LoginIdentity, ProcessSnapshot},
    tunnel::SpawnFailure,
    windows::{MonitorMutex, WindowsMonitorObservation, WindowsProcess},
    windows_launcher::LauncherMutex,
    Result,
};
use std::{
    os::windows::process::CommandExt,
    process::{Child, Command, Stdio},
};
use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

pub struct WindowsLaunch<'a> {
    launcher: &'a LauncherMutex,
    installation: &'a Snapshot,
    configuration: &'a Configuration,
    discovery: &'a DiscoveryConfig,
    native: &'a NativeIdentity,
    child: Option<Child>,
}
impl<'a> WindowsLaunch<'a> {
    pub fn new(
        launcher: &'a LauncherMutex,
        installation: &'a Snapshot,
        configuration: &'a Configuration,
        discovery: &'a DiscoveryConfig,
        native: &'a NativeIdentity,
    ) -> Self {
        Self {
            launcher,
            installation,
            configuration,
            discovery,
            native,
            child: None,
        }
    }
}
impl Observation for WindowsLaunch<'_> {
    fn current_login(&self) -> Result<LoginIdentity> {
        WindowsMonitorObservation.current_login()
    }
    fn state(&self, pid: u32) -> ProcessState {
        WindowsMonitorObservation.state(pid)
    }
    fn observe(&self, pid: u32) -> Result<Option<ProcessSnapshot>> {
        WindowsMonitorObservation.observe(pid)
    }
    fn legacy_candidates(&self) -> Result<Vec<ProcessSnapshot>> {
        WindowsMonitorObservation.legacy_candidates()
    }
}
impl Platform for WindowsLaunch<'_> {
    fn verify(&self) -> Result<()> {
        if &self.current_login()? != self.launcher.login() {
            return Err("OTHER_LOGIN_OWNER");
        }
        self.installation.verify_current()?;
        self.configuration.verify_current()?;
        if !self.configuration.issues.is_empty() {
            return Err("CONFIGURATION_INVALID");
        }
        Ok(())
    }
    fn owner(&self) -> Result<Option<MonitorIdentity>> {
        discovery::discover(self.discovery, self)
    }
    fn spawn(&mut self) -> std::result::Result<ProcessSnapshot, SpawnFailure> {
        self.verify().map_err(|_| SpawnFailure::NotStarted)?;
        // A concurrently starting legacy monitor may not have published its record yet.
        // Do not spawn while its shared ownership mutex is occupied. Our child acquires it anew.
        let mutex = MonitorMutex::try_acquire()
            .map_err(|_| SpawnFailure::NotStarted)?
            .ok_or(SpawnFailure::NotStarted)?;
        if self
            .owner()
            .map_err(|_| SpawnFailure::NotStarted)?
            .is_some()
        {
            return Err(SpawnFailure::NotStarted);
        }
        let mut command = Command::new(self.native.executable());
        command
            .args(self.native.arguments())
            .current_dir(self.installation.client_root())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .env_clear();
        // No inherited central/BAT/proxy token; user stores are resolved afresh by the supervisor.
        command.envs(crate::monitor_launch::environment(std::env::vars_os()));
        self.verify().map_err(|_| SpawnFailure::NotStarted)?;
        drop(mutex);
        let child = crate::windows_detach::spawn(&mut command, CREATE_NO_WINDOW)
            .map_err(|_| SpawnFailure::NotStarted)?;
        self.child = Some(child);
        // Never force-kill a monitor that might already own tunnels. Incomplete evidence keeps intent.
        let held = WindowsProcess::from_child(self.child.as_ref().unwrap())
            .map_err(|_| SpawnFailure::Unconfirmed)?;
        held.snapshot().map_err(|_| SpawnFailure::Unconfirmed)
    }
}
