//! Native-only creation composition. No commands or serialized machine context.
use crate::{fleet_lifecycle::Ticket, startup_role};
#[cfg(any(windows, test))]
use bat_fleet_core::windows_creation::NativeInput;
use bat_fleet_core::{
    creation_evidence::{Baseline, StartupBinding, StartupRole},
    Result,
};
#[cfg(any(windows, test))]
use bat_fleet_core::{
    creation_evidence::{EvidenceIo, EvidenceKey, Record, RECEIPT_LIMIT},
    windows_creation::{CreationPlatform, Security},
};
use std::path::Path;

// Deliberately neither Debug nor Serialize: authority stays in native code.
pub(crate) struct Prepared {
    pub owner: bat_fleet_core::connection_setup::OwnerEvidence,
    pub baseline: [Baseline; 4],
}
fn supported<T>(native: impl FnOnce() -> Result<T>) -> Result<T> {
    if !cfg!(windows) {
        return Err("LOCAL_PLATFORM_UNSUPPORTED");
    }
    native()
}
fn startup(data: &Path) -> Result<StartupBinding> {
    let evidence = startup_role::evidence(data).map_err(|_| "MANAGED_CONFIGURATION_UNREADABLE")?;
    if evidence.version != 1 {
        return Err("MANAGED_CONFIGURATION_UNREADABLE");
    }
    let snapshot = evidence.snapshot;
    // Managed creation needs a positive native binding; never fall back to null.
    if snapshot.role != Some(startup_role::Role::Client) {
        return Err("CREATION_OPERATION_UNSUPPORTED");
    }
    match data.join("managed-central").symlink_metadata() {
        Ok(_) => return Err("CREATION_OPERATION_UNSUPPORTED"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
        Err(_) => return Err("MANAGED_CONFIGURATION_UNREADABLE"),
    }
    Ok(StartupBinding {
        installation_ref: snapshot
            .installation_ref
            .ok_or("MANAGED_CONFIGURATION_UNREADABLE")?,
        role_record_sha256: evidence.record_sha256,
        generation: snapshot.generation,
        role: match snapshot.role.ok_or("MANAGED_CONFIGURATION_UNREADABLE")? {
            startup_role::Role::Client => StartupRole::Client,
            startup_role::Role::Provider => StartupRole::Provider,
            startup_role::Role::Both => StartupRole::Both,
        },
    })
}
#[cfg(any(windows, test))]
fn prepare<S: Security>(platform: &mut CreationPlatform<S>, ticket: &Ticket) -> Result<Prepared> {
    ticket.acquire(|| {
        platform.with_writer(|writer| {
            ticket.verify()?;
            let owner = match writer.read_private(&EvidenceKey::Provisioning, RECEIPT_LIMIT)? {
                Some(bytes) => platform_owner(writer, &bytes)?,
                None => writer.reserve(&mut || ticket.verify())?,
            };
            let parents = writer
                .receipt()
                .slots
                .iter()
                .map(|s| {
                    s.canonical_path
                        .parent()
                        .map(Path::to_path_buf)
                        .ok_or("CREATION_EVIDENCE_UNPROVEN")
                })
                .collect::<Result<Vec<_>>>()?;
            for parent in parents {
                writer.prepare_parents(&parent, &mut || ticket.verify())?;
            }
            writer.prepare_publication_directories(&mut || ticket.verify())?;
            let baseline = writer.baseline(&owner)?;
            Ok(Prepared { owner, baseline })
        })
    })
}
#[cfg(any(windows, test))]
fn platform_owner<S: Security>(
    writer: &bat_fleet_core::windows_creation::CreationWriter<'_, S>,
    bytes: &[u8],
) -> Result<bat_fleet_core::connection_setup::OwnerEvidence> {
    let expected = Record::Provisioning(Box::new(writer.receipt().clone())).encode()?;
    if bytes != expected {
        return Err("CREATION_EVIDENCE_UNPROVEN");
    }
    writer.receipt().owner(bytes)
}
/// One blocking closure owns all native paths, handles, guards and platform state.
/// Only the later participant may call this; there is no request-supplied path.
pub(crate) async fn reserve<R: tauri::Runtime>(
    app: tauri::AppHandle<R>,
    ticket: Ticket,
) -> Result<Prepared> {
    let job = supported(|| {
        Ok(tauri::async_runtime::spawn_blocking(move || {
            #[cfg(windows)]
            {
                use bat_fleet_core::{
                    installation::canonical_local, windows_creation::WindowsCreationPlatform,
                };
                use tauri::Manager;
                ticket.verify()?;
                let config = canonical_local(
                    &app.path()
                        .app_config_dir()
                        .map_err(|_| "LOGIN_PATH_UNAVAILABLE")?,
                )?;
                let data = canonical_local(
                    &app.path()
                        .app_local_data_dir()
                        .map_err(|_| "LOGIN_PATH_UNAVAILABLE")?,
                )?;
                let directory = |name| {
                    canonical_local(&std::path::PathBuf::from(
                        std::env::var_os(name).ok_or("LOGIN_PATH_UNAVAILABLE")?,
                    ))
                };
                let roaming = directory("APPDATA")?;
                let user = directory("USERPROFILE")?;
                let startup_directory = bat_fleet_core::windows_startup::startup_directory()?;
                let input = NativeInput {
                    pointer: config.join("fleet.json"),
                    kit_root: data.join("fleet-kit"),
                    role_record: startup_role::record_path(&data),
                    startup: startup(&data)?,
                    roaming,
                    startup_directory,
                    user,
                };
                let executable = std::env::current_exe().map_err(|_| "INSTALLATION_UNAVAILABLE")?;
                let mut platform = WindowsCreationPlatform::from_native(
                    input,
                    &executable,
                    &|| ticket.verify(),
                    &mut || {
                        [
                            uuid::Uuid::new_v4().simple().to_string(),
                            uuid::Uuid::new_v4().simple().to_string(),
                        ]
                    },
                )?;
                prepare(&mut platform, &ticket)
            }
            #[cfg(not(windows))]
            {
                let _ = (app, ticket);
                Err("LOCAL_PLATFORM_UNSUPPORTED")
            }
        }))
    })?;
    job.await.map_err(|_| "INSTALLATION_UNAVAILABLE")?
}
#[cfg(test)]
#[path = "fleet_creation/tests.rs"]
mod tests;
