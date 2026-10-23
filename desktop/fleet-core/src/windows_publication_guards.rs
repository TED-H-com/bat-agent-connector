//! Shared account-wide exclusion. Policy checks remain with each caller.
use crate::{
    discovery::{MonitorIdentity, Observation},
    monitor_launch,
    process_adapter::LoginIdentity,
    windows::{current_login, MonitorMutex},
    windows_launcher::LauncherMutex,
    Result,
};
use std::{
    cell::RefCell,
    path::Path,
    rc::{Rc, Weak},
};

/// Thread-bound guards are returned to the operation; weak references cannot
/// prolong exclusion after it returns. Never retain them over an async boundary.
pub(crate) struct WindowsPublicationGuards {
    login: LoginIdentity,
    launcher: RefCell<Weak<LauncherMutex>>,
    monitor: RefCell<Weak<MonitorMutex>>,
}
impl WindowsPublicationGuards {
    pub(crate) fn new(login: LoginIdentity) -> Self {
        Self {
            login,
            launcher: RefCell::default(),
            monitor: RefCell::default(),
        }
    }
    pub(crate) fn launcher(&self) -> Result<Rc<LauncherMutex>> {
        let guard = self
            .launcher
            .borrow()
            .upgrade()
            .ok_or("LAUNCHER_REQUIRED")?;
        if guard.login() != &self.login || current_login()? != self.login {
            return Err("OTHER_LOGIN_OWNER");
        }
        Ok(guard)
    }
    pub(crate) fn monitor_held(&self) -> bool {
        self.monitor.borrow().upgrade().is_some()
    }
    pub(crate) fn launcher_guard(
        &self,
        verify: impl Fn() -> Result<()>,
    ) -> Result<Rc<LauncherMutex>> {
        if self.launcher.borrow().upgrade().is_some() {
            return Err("LAUNCHER_BUSY");
        }
        verify()?;
        let guard = Rc::new(LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY")?);
        if guard.login() != &self.login {
            return Err("OTHER_LOGIN_OWNER");
        }
        *self.launcher.borrow_mut() = Rc::downgrade(&guard);
        verify()?;
        Ok(guard)
    }
    pub(crate) fn monitor_guard(
        &self,
        verify_absence: impl FnOnce() -> Result<()>,
    ) -> Result<Rc<MonitorMutex>> {
        self.launcher()?;
        if self.monitor_held() {
            return Err("MONITOR_ALREADY_RUNNING");
        }
        let guard = Rc::new(MonitorMutex::try_acquire()?.ok_or("MONITOR_ALREADY_RUNNING")?);
        if guard.login() != &self.login {
            return Err("OTHER_LOGIN_OWNER");
        }
        verify_absence()?;
        *self.monitor.borrow_mut() = Rc::downgrade(&guard);
        Ok(guard)
    }
    pub(crate) fn observed(
        owner: Option<MonitorIdentity>,
        journal: &Path,
        observation: &impl Observation,
    ) -> Result<Option<MonitorIdentity>> {
        if owner.is_none() {
            monitor_launch::verify_absence(journal, observation)?;
        }
        Ok(owner)
    }
}
