use super::*;
use bat_fleet_core::{
    creation_evidence::ManagedIdentity, discovery::MonitorIdentity, process_adapter::LoginIdentity,
};
use std::{cell::RefCell, fs::File};

pub(super) struct Binding<'a> {
    pub managed: &'a crate::managed::Managed,
    pub bridge: &'a crate::bridge::Bridge,
    pub original: RefCell<Option<crate::bridge::CreationScope>>,
}
impl Binding<'_> {
    fn observe<S: Security>(&self, security: &S) -> Result<Option<ManagedIdentity>> {
        let scope = self.bridge.creation_scope()?;
        if self.original.borrow().as_ref().is_some_and(|s| s != &scope) {
            return Err("CREATION_EVIDENCE_UNPROVEN");
        }
        let value = self.managed.creation_binding(&scope, |path, limit| {
            CreationPlatform::read_private_attestation(security, path, limit)
        })?;
        if self.bridge.creation_scope()? != scope {
            return Err("CREATION_EVIDENCE_UNPROVEN");
        }
        *self.original.borrow_mut() = Some(scope);
        Ok(value)
    }
}
pub(super) struct Attested<'a, S> {
    pub inner: S,
    pub binding: Binding<'a>,
}
impl<S: Security> Security for Attested<'_, S> {
    type LauncherGuard = S::LauncherGuard;
    type MonitorGuard = S::MonitorGuard;
    fn managed_identity(&self) -> Result<Option<ManagedIdentity>> {
        self.inner.held()?;
        self.binding.observe(&self.inner)
    }
    fn launcher_guard(&self, check: &dyn Fn() -> Result<()>) -> Result<Self::LauncherGuard> {
        self.inner.launcher_guard(check)
    }
    fn monitor_guard(&self, check: &dyn Fn() -> Result<()>) -> Result<Self::MonitorGuard> {
        self.inner.monitor_guard(check)
    }
    fn unconfigured(&self, pointer: &Path, roaming: &Path) -> Result<()> {
        self.inner.unconfigured(pointer, roaming)
    }
    fn discover(&self) -> Result<Option<MonitorIdentity>> {
        self.inner.discover()
    }
    fn unpublished(&self, path: &Path) -> Result<()> {
        self.inner.unpublished(path)
    }
    fn validate_path(&self, path: &Path) -> Result<()> {
        self.inner.validate_path(path)
    }
    fn login(&self) -> Result<LoginIdentity> {
        self.inner.login()
    }
    fn held(&self) -> Result<()> {
        self.inner.held()
    }
    fn effect_held(&self) -> Result<()> {
        self.inner.effect_held()
    }
    fn open(&self, path: &Path, private: bool, child_absent: bool) -> Result<File> {
        self.inner.open(path, private, child_absent)
    }
    fn identity(&self, file: &File) -> Result<bat_fleet_core::creation_evidence::ObjectIdentity> {
        self.inner.identity(file)
    }
}
