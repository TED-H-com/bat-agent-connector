//! Native Windows backend migration; no WebView paths or generic process control.
#[cfg(windows)]
mod native;
#[cfg(windows)]
pub use native::WindowsMigration;

use crate::{
    discovery::{Backend, MonitorIdentity},
    installation::Snapshot,
    migration::Owner,
    process_adapter::{LoginIdentity, ProcessSnapshot},
    Result,
};
use std::{ffi::OsString, path::Path, time::Duration};

fn current_installation(original: &Snapshot) -> Result<Snapshot> {
    original.verify_layout()?;
    let current = Snapshot::load(original.path())?;
    if current.path() != original.path() || current.client_root() != original.client_root() {
        return Err("INSTALLATION_CHANGED");
    }
    Ok(current)
}

fn ps_arguments(script: &Path, inventory: &Path, index: &Path) -> Result<Vec<OsString>> {
    if [script, inventory, index].iter().any(|p| !p.is_absolute()) {
        return Err("INSTALLATION_INVALID");
    }
    Ok(vec![
        "-ExecutionPolicy".into(),
        "Bypass".into(),
        "-NoProfile".into(),
        "-NonInteractive".into(),
        "-WindowStyle".into(),
        "Hidden".into(),
        "-File".into(),
        script.into(),
        "-InventoryPath".into(),
        inventory.into(),
        "-ProfileIndexPath".into(),
        index.into(),
    ])
}
fn ps_environment(
    system: &Path,
    values: impl IntoIterator<Item = (OsString, OsString)>,
) -> Vec<(OsString, OsString)> {
    let mut selected: Vec<_> = values
        .into_iter()
        .filter(|(key, _)| {
            key.to_str().is_some_and(|key| {
                [
                    "APPDATA",
                    "LOCALAPPDATA",
                    "USERPROFILE",
                    "USERNAME",
                    "USERDOMAIN",
                    "TEMP",
                    "TMP",
                    "HOMEDRIVE",
                    "HOMEPATH",
                    "PROGRAMDATA",
                    "PROGRAMFILES",
                    "COMMONPROGRAMFILES",
                    "SYSTEMDRIVE",
                    "PATH",
                ]
                .contains(&key.to_ascii_uppercase().as_str())
            })
        })
        .collect();
    selected.push((
        "PSModulePath".into(),
        system.join("WindowsPowerShell/v1.0/Modules").into(),
    ));
    if let Some(windows) = system.parent() {
        selected.push(("SystemRoot".into(), windows.into()));
        selected.push(("WINDIR".into(), windows.into()));
    }
    selected
}
/// No resend or effect inside the readback loop. A different owner is a conflict,
/// and observation errors remain unknown. Clocks/sleep are injectable for tests.
fn wait_for_owner(
    expected: &ProcessSnapshot,
    backend: Backend,
    login: &LoginIdentity,
    mut observe: impl FnMut() -> Result<Option<MonitorIdentity>>,
    mut elapsed: impl FnMut() -> Duration,
    mut sleep: impl FnMut(Duration),
) -> Result<()> {
    let budget = Duration::from_secs(5);
    loop {
        if let Some(owner) = observe()? {
            Owner::from_monitor(&owner, login)?;
            if owner.backend != backend || owner.process != *expected {
                return Err("MIGRATION_OWNER_CHANGED");
            }
            return Ok(());
        }
        let elapsed = elapsed();
        if elapsed >= budget {
            return Err("MIGRATION_LAUNCH_UNKNOWN");
        }
        sleep((budget - elapsed).min(Duration::from_millis(50)));
    }
}

#[cfg(test)]
mod tests;
