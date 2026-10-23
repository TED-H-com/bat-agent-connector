//! Recheck the original IPC ticket at owned guard acquisition and final native effects.
use crate::fleet_lifecycle::Ticket;
use bat_fleet_core::{
    discovery::{self, Backend, MonitorIdentity},
    migration, monitor_launch,
    ownership::ProcessState,
    process_adapter::{LoginIdentity, ProcessSnapshot},
    profile_launch,
    tunnel::SpawnFailure,
    Result,
};
pub struct Guarded<'a, T> {
    pub inner: T,
    pub ticket: &'a Ticket,
}
impl<T: discovery::Observation> discovery::Observation for Guarded<'_, T> {
    fn current_login(&self) -> Result<LoginIdentity> {
        self.inner.current_login()
    }
    fn state(&self, pid: u32) -> ProcessState {
        self.inner.state(pid)
    }
    fn observe(&self, pid: u32) -> Result<Option<ProcessSnapshot>> {
        self.inner.observe(pid)
    }
    fn legacy_candidates(&self) -> Result<Vec<ProcessSnapshot>> {
        self.inner.legacy_candidates()
    }
}
impl<T: monitor_launch::Platform> monitor_launch::Platform for Guarded<'_, T> {
    fn verify(&self) -> Result<()> {
        self.ticket.verify()?;
        self.inner.verify()
    }
    fn owner(&self) -> Result<Option<MonitorIdentity>> {
        self.inner.owner()
    }
    fn spawn(&mut self) -> std::result::Result<ProcessSnapshot, SpawnFailure> {
        self.ticket.verify().map_err(|_| SpawnFailure::NotStarted)?;
        self.inner.spawn()
    }
}
impl<T: profile_launch::Platform> profile_launch::Platform for Guarded<'_, T> {
    fn verify(&self) -> Result<()> {
        self.ticket.verify()?;
        self.inner.verify()
    }
    fn login(&self) -> Result<LoginIdentity> {
        self.inner.login()
    }
    fn executable(&self) -> Result<profile_launch::Executable> {
        self.inner.executable()
    }
    fn running(&self) -> Result<Vec<ProcessSnapshot>> {
        self.inner.running()
    }
    fn observe(&self, pid: u32) -> Result<Option<ProcessSnapshot>> {
        self.inner.observe(pid)
    }
    fn spawn(
        &mut self,
        exe: &profile_launch::Executable,
    ) -> std::result::Result<ProcessSnapshot, SpawnFailure> {
        self.ticket.verify().map_err(|_| SpawnFailure::NotStarted)?;
        self.inner.spawn(exe)
    }
}
impl<T: migration::Platform> migration::Platform for Guarded<'_, T> {
    type LauncherGuard = T::LauncherGuard;
    type MonitorGuard = T::MonitorGuard;
    fn launcher_guard(&mut self) -> Result<Self::LauncherGuard> {
        self.ticket.acquire(|| self.inner.launcher_guard())
    }
    fn monitor_guard(&mut self) -> Result<Self::MonitorGuard> {
        self.ticket.acquire(|| self.inner.monitor_guard())
    }
    fn login(&self) -> Result<LoginIdentity> {
        self.inner.login()
    }
    fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
        self.inner.discover()
    }
    fn request_quit(&mut self, owner: &migration::Owner) -> Result<()> {
        self.ticket.verify()?;
        self.inner.request_quit(owner)
    }
    fn launch(&mut self, backend: Backend) -> Result<()> {
        self.ticket.verify()?;
        self.inner.launch(backend)
    }
    fn validate_config(&self, bytes: &[u8], backend: Backend) -> Result<()> {
        self.ticket.verify()?;
        self.inner.validate_config(bytes, backend)
    }
    fn classify_shortcut(&self, bytes: &[u8]) -> Result<Backend> {
        self.inner.classify_shortcut(bytes)
    }
    fn shortcut(&self, backend: Backend) -> Result<Vec<u8>> {
        self.ticket.verify()?;
        self.inner.shortcut(backend)
    }
}

// The writer's context/fence reads must observe the original lifecycle ticket,
// including the callback between staging and final create-only publication.
impl<T: bat_fleet_core::windows_creation::Security> bat_fleet_core::windows_creation::Security
    for Guarded<'_, T>
{
    fn managed_identity(
        &self,
    ) -> Result<Option<bat_fleet_core::creation_evidence::ManagedIdentity>> {
        self.ticket.verify()?;
        let identity = self.inner.managed_identity()?;
        self.ticket.verify()?;
        Ok(identity)
    }
    type LauncherGuard = T::LauncherGuard;
    type MonitorGuard = T::MonitorGuard;
    fn launcher_guard(&self, check: &dyn Fn() -> Result<()>) -> Result<Self::LauncherGuard> {
        self.ticket.acquire(|| {
            self.inner.launcher_guard(&|| {
                self.ticket.verify()?;
                check()
            })
        })
    }
    fn monitor_guard(&self, check: &dyn Fn() -> Result<()>) -> Result<Self::MonitorGuard> {
        self.ticket.acquire(|| {
            self.inner.monitor_guard(&|| {
                self.ticket.verify()?;
                check()
            })
        })
    }
    fn unconfigured(&self, pointer: &std::path::Path, roaming: &std::path::Path) -> Result<()> {
        self.ticket.verify()?;
        self.inner.unconfigured(pointer, roaming)
    }
    fn discover(&self) -> Result<Option<MonitorIdentity>> {
        self.ticket.verify()?;
        self.inner.discover()
    }
    fn unpublished(&self, path: &std::path::Path) -> Result<()> {
        self.ticket.verify()?;
        self.inner.unpublished(path)
    }
    fn validate_path(&self, path: &std::path::Path) -> Result<()> {
        self.ticket.verify()?;
        self.inner.validate_path(path)
    }
    fn login(&self) -> Result<LoginIdentity> {
        self.ticket.verify()?;
        self.inner.login()
    }
    fn held(&self) -> Result<()> {
        self.ticket.verify()?;
        self.inner.held()
    }
    fn effect_held(&self) -> Result<()> {
        self.ticket.verify()?;
        self.inner.effect_held()
    }
    fn open(
        &self,
        path: &std::path::Path,
        private: bool,
        child_absent: bool,
    ) -> Result<std::fs::File> {
        self.ticket.verify()?;
        self.inner.open(path, private, child_absent)
    }
    fn identity(
        &self,
        file: &std::fs::File,
    ) -> Result<bat_fleet_core::creation_evidence::ObjectIdentity> {
        self.ticket.verify()?;
        self.inner.identity(file)
    }
}
