use super::*;
use crate::{desktop_preferences, fleet_control::Request};
use bat_fleet_core::{
    migration, profile_launch,
    selection::launch_plan,
    selection_io::{ChoicePreview, Snapshot as Selection},
    windows_migration::WindowsMigration,
    windows_profiles::WindowsProfiles,
};
use std::collections::HashMap;
struct Choices {
    context: Context,
    preview: ChoicePreview,
}
struct Launch {
    context: Context,
    selection: Selection,
    preview: profile_launch::Preview,
}
struct Migration {
    context: Context,
    preview: migration::Preview,
    backend: Backend,
    autostart: bool,
    bytes: Vec<u8>,
}
#[derive(Default)]
pub struct Controller {
    choices: HashMap<String, Choices>,
    launches: HashMap<String, Launch>,
    migrations: HashMap<String, Migration>,
}
fn id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}
fn safe<T: serde::Serialize>(value: T) -> Result<Value> {
    serde_json::to_value(value).map_err(|_| "FLEET_RESPONSE_INVALID")
}
fn migration_store(c: &Context) -> Result<migration::Store> {
    migration::Store::new(
        c.installation.path(),
        &bat_fleet_core::windows_startup::startup_directory()?,
    )
}
fn migration_platform<'a>(
    c: &Context,
    ticket: &'a Ticket,
) -> Result<Guarded<'a, WindowsMigration>> {
    Ok(Guarded {
        inner: WindowsMigration::new(
            c.installation.clone(),
            c.paths.clone(),
            c.roaming.clone(),
            c.quit_file.clone(),
            PathBuf::from(c.native.executable()),
            system_directory()?,
        )?,
        ticket,
    })
}
fn system_directory() -> Result<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    let mut buffer = [0u16; 32768];
    let n = unsafe {
        windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW(
            buffer.as_mut_ptr(),
            buffer.len() as u32,
        )
    } as usize;
    if n == 0 || n >= buffer.len() {
        return Err("SYSTEM_DIRECTORY_UNAVAILABLE");
    }
    canonical_local(&PathBuf::from(std::ffi::OsString::from_wide(&buffer[..n])))
}
fn overview(c: &Context) -> Result<Value> {
    let mut value =
        crate::fleet::sanitize_result(&c.status()?, "status").map_err(|_| "STATUS_UNPROVEN")?;
    let store = Store::new(c.roaming.clone());
    let selection = store.read(&c.configuration)?;
    let profiles = c
        .configuration
        .inventory
        .hosts()
        .iter()
        .map(|host| json!({"id":host["profile"],"label":host["label"],"connection":host["name"]}))
        .chain(std::iter::once(
            json!({"id":"default","label":"Local BAT","connection":null}),
        ))
        .collect::<Vec<_>>();
    let pending = migration_store(c)?.pending()?.map(|(id, _)| id);
    value["backend"] = safe(c.installation.backend())?;
    value["profiles"] = json!(profiles);
    value["selection"]["profiles"] = json!(selection.preferences().profiles);
    value["selection"]["dashboard"] = json!(selection.preferences().dashboard);
    value["login"] =
        safe(desktop_preferences::load(&c.roaming).map_err(|_| "LOGIN_PREFERENCES_INVALID")?)?;
    value["pending_migration"] = json!(pending);
    value["control_version"] = json!(1);
    value["launch_warning"] = if bat_fleet_core::windows_detach::lifetime_warning() {
        json!("FLEET_SESSION_LIFETIME")
    } else {
        Value::Null
    };
    Ok(value)
}
fn ensure(c: &Context, launcher: &LauncherMutex, ticket: &Ticket) -> Result<()> {
    verify_no_migration(&c.installation)?;
    verify_control_owner(&c.installation, true)?;
    if c.installation.backend() == Backend::Rust {
        c.ensure(launcher, ticket)
    } else {
        crate::fleet::ensure_powershell(c.installation.clone(), c.configuration.binding(), ticket)
            .map_err(|_| "MONITOR_START_UNPROVEN")
    }
}
/// Rechecked immediately before profile publication/spawn. Never promotes TCP-only or stale evidence.
fn ready(c: &Context, selection: &Selection, profiles: &[String]) -> Result<()> {
    c.verify(c.configuration.binding())?;
    if !selection.same_snapshot(&Store::new(c.roaming.clone()).read(&c.configuration)?) {
        return Err("SELECTION_CHANGED");
    }
    let required: Vec<_> = profiles
        .iter()
        .filter(|p| p.as_str() != "default")
        .map(|p| {
            c.configuration
                .inventory
                .hosts()
                .iter()
                .find(|h| h["profile"].as_str() == Some(p))
                .and_then(|h| h["name"].as_str())
                .ok_or("PROFILE_DRIFT")
        })
        .collect::<Result<_>>()?;
    if required.is_empty() {
        return Ok(());
    }
    let now = now_ms()?;
    let snapshot = supervisor_control::read_snapshot(
        &c.configuration,
        &c.discovery,
        &WindowsMonitorObservation,
        now,
    )?
    .ok_or("BAT_READINESS_PENDING")?;
    crate::fleet_readiness::profiles_ready(&snapshot, &required, &selection.revision, now)?;
    Ok(())
}
struct ReadyProfiles<'a> {
    native: Guarded<'a, WindowsProfiles<'a>>,
    context: &'a Context,
    selection: &'a Selection,
    profiles: &'a [String],
}
impl profile_launch::Platform for ReadyProfiles<'_> {
    fn verify(&self) -> Result<()> {
        self.native.verify()?;
        verify_no_migration(&self.context.installation)?;
        ready(self.context, self.selection, self.profiles)
    }
    fn login(&self) -> Result<bat_fleet_core::process_adapter::LoginIdentity> {
        self.native.login()
    }
    fn executable(&self) -> Result<profile_launch::Executable> {
        self.native.executable()
    }
    fn running(&self) -> Result<Vec<bat_fleet_core::process_adapter::ProcessSnapshot>> {
        self.native.running()
    }
    fn observe(
        &self,
        pid: u32,
    ) -> Result<Option<bat_fleet_core::process_adapter::ProcessSnapshot>> {
        self.native.observe(pid)
    }
    fn spawn(
        &mut self,
        executable: &profile_launch::Executable,
    ) -> std::result::Result<
        bat_fleet_core::process_adapter::ProcessSnapshot,
        bat_fleet_core::tunnel::SpawnFailure,
    > {
        self.verify()
            .map_err(|_| bat_fleet_core::tunnel::SpawnFailure::NotStarted)?;
        self.native.spawn(executable)
    }
}
impl Controller {
    fn bound(&self) -> Result<()> {
        if self.choices.len() + self.launches.len() + self.migrations.len() >= 32 {
            Err("FLEET_PREVIEW_LIMIT")
        } else {
            Ok(())
        }
    }
    pub fn request(&mut self, path: &Path, input: Request, ticket: &Ticket) -> Result<Value> {
        match input {
            Request::Discard { preview_id } => {
                self.choices.remove(&preview_id);
                self.launches.remove(&preview_id);
                self.migrations.remove(&preview_id);
                Ok(json!({"discarded":true}))
            }
            Request::ApplyChoices { preview_id } => {
                let saved = self.choices.get(&preview_id).ok_or("PREVIEW_EXPIRED")?;
                let c = &saved.context;
                let _launcher =
                    ticket.acquire(|| LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY"))?;
                verify_no_migration(&c.installation)?;
                c.verify(c.configuration.binding())?;
                Store::new(c.roaming.clone()).apply_choices(
                    &c.configuration,
                    &saved.preview,
                    || {
                        ticket.verify()?;
                        c.owner_epoch()
                    },
                )?;
                overview(c)
            }
            Request::Launch { preview_id } => {
                let saved = self.launches.get(&preview_id).ok_or("PREVIEW_EXPIRED")?;
                let c = &saved.context;
                let launcher =
                    ticket.acquire(|| LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY"))?;
                verify_no_migration(&c.installation)?;
                let mut native = Guarded {
                    inner: WindowsProfiles::new(&launcher, &c.installation),
                    ticket,
                };
                // Durable readback precedes fresh readiness; never resend after a lost reply.
                if saved.preview.summary().opens_bat {
                    if let Some(receipt) =
                        profile_launch::read_receipt(&c.roaming, &preview_id, &native)?
                    {
                        return safe(receipt);
                    }
                }
                if !launch_plan(&c.configuration.inventory, saved.selection.preferences())
                    .connect
                    .is_empty()
                {
                    crate::fleet_readiness::with_current_selection(
                        &c.configuration,
                        &Store::new(c.roaming.clone()),
                        &saved.selection,
                        || ensure(c, &launcher, ticket),
                    )?;
                }
                let summary = saved.preview.summary();
                let outcome = if !summary.opens_bat || summary.already_running {
                    profile_launch::apply(
                        &c.configuration,
                        &Store::new(c.roaming.clone()),
                        &saved.preview,
                        &mut native,
                    )?
                } else {
                    let mut platform = ReadyProfiles {
                        native,
                        context: c,
                        selection: &saved.selection,
                        profiles: &summary.profiles,
                    };
                    profile_launch::apply(
                        &c.configuration,
                        &Store::new(c.roaming.clone()),
                        &saved.preview,
                        &mut platform,
                    )?
                };
                match outcome {
                    profile_launch::Outcome::NoBat(summary) => {
                        Ok(json!({"state":"no_bat","summary":summary}))
                    }
                    profile_launch::Outcome::AlreadyRunning(summary) => {
                        Ok(json!({"state":"already_running","summary":summary}))
                    }
                    profile_launch::Outcome::Receipt(r) => safe(r),
                }
            }
            Request::ApplyMigration {
                preview_id,
                fingerprint,
            } => {
                let saved = self.migrations.get(&preview_id).ok_or("PREVIEW_EXPIRED")?;
                if saved.preview.fingerprint() != fingerprint {
                    return Err("MIGRATION_CHANGED");
                }
                let store = migration_store(&saved.context)?;
                let mut platform = migration_platform(&saved.context, ticket)?;
                store.begin(
                    &mut platform,
                    &preview_id,
                    &saved.preview,
                    saved.backend,
                    &saved.bytes,
                    saved.autostart,
                )?;
                safe(store.status(&preview_id)?)
            }
            input => {
                let c = Context::load(Snapshot::load(path)?)?;
                match input {
                    Request::Overview {} => overview(&c),
                    Request::PreviewChoices {
                        choices,
                        configuration_binding,
                        selection_revision,
                        monitor_epoch,
                    } => {
                        self.bound()?;
                        c.verify(&configuration_binding)?;
                        verify_no_migration(&c.installation)?;
                        let store = Store::new(c.roaming.clone());
                        let snapshot = store.read(&c.configuration)?;
                        if snapshot.revision != selection_revision
                            || c.owner_epoch() != Ok(monitor_epoch.clone())
                        {
                            return Err("SELECTION_CHANGED");
                        }
                        let preview = store.preview_choices(
                            &c.configuration,
                            &snapshot,
                            &choices,
                            monitor_epoch.as_deref(),
                        )?;
                        let id = id();
                        let result =
                            json!({"preview_id":id,"summary":preview.summary(&c.configuration)?});
                        self.choices.insert(
                            id,
                            Choices {
                                context: c,
                                preview,
                            },
                        );
                        Ok(result)
                    }
                    Request::PreviewLaunch {} | Request::PreviewProfile { .. } => {
                        self.bound()?;
                        let launcher = ticket
                            .acquire(|| LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY"))?;
                        verify_no_migration(&c.installation)?;
                        let store = Store::new(c.roaming.clone());
                        let selection = store.read(&c.configuration)?;
                        let native = Guarded {
                            inner: WindowsProfiles::new(&launcher, &c.installation),
                            ticket,
                        };
                        let preview = if let Request::PreviewProfile { profile_id } = input {
                            profile_launch::preview_profile(
                                &c.configuration,
                                &store,
                                &selection,
                                &c.roaming,
                                &native,
                                &profile_id,
                            )?
                        } else {
                            profile_launch::preview(
                                &c.configuration,
                                &store,
                                &selection,
                                &c.roaming,
                                &native,
                            )?
                        };
                        let summary = preview.summary();
                        let id = summary.launch_id.clone();
                        let value = json!({"preview_id":id,"summary":summary,"configuration_binding":c.configuration.binding()});
                        self.launches.insert(
                            id,
                            Launch {
                                context: c,
                                selection,
                                preview,
                            },
                        );
                        Ok(value)
                    }
                    Request::LaunchStatus { launch_id } => {
                        let launcher = ticket
                            .acquire(|| LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY"))?;
                        let platform = WindowsProfiles::new(&launcher, &c.installation);
                        safe(profile_launch::read_receipt(
                            &c.roaming, &launch_id, &platform,
                        )?)
                    }
                    Request::PreviewMigration { backend, autostart } => {
                        self.bound()?;
                        let store = migration_store(&c)?;
                        let mut platform = migration_platform(&c, ticket)?;
                        let preview = store.preview(&mut platform, c.installation.backend())?;
                        let id = id();
                        let value = json!({"preview_id":id,"fingerprint":preview.fingerprint(),"from":preview.backend(),"to":backend,"autostart_before":preview.autostart_entry_present(),"autostart":autostart});
                        let bytes = c.installation.backend_payload(backend)?;
                        self.migrations.insert(
                            id,
                            Migration {
                                context: c,
                                preview,
                                backend,
                                autostart,
                                bytes,
                            },
                        );
                        Ok(value)
                    }
                    Request::AdvanceMigration { migration_id } => {
                        let store = migration_store(&c)?;
                        store.advance(&mut migration_platform(&c, ticket)?, &migration_id)?;
                        safe(store.status(&migration_id)?)
                    }
                    Request::MigrationStatus { migration_id } => {
                        safe(migration_store(&c)?.status(&migration_id)?)
                    }
                    Request::RestoreMigration {
                        source_id,
                        restore_id,
                    } => {
                        let store = migration_store(&c)?;
                        store.restore(
                            &mut migration_platform(&c, ticket)?,
                            &source_id,
                            &restore_id,
                        )?;
                        safe(store.status(&restore_id)?)
                    }
                    Request::SaveLogin {
                        expected_revision,
                        show_picker,
                    } => {
                        let _launcher = ticket
                            .acquire(|| LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY"))?;
                        verify_no_migration(&c.installation)?;
                        safe(
                            desktop_preferences::save(
                                &c.roaming,
                                &expected_revision,
                                show_picker,
                                || {
                                    ticket.verify().map_err(String::from)?;
                                    c.installation.verify_current().map_err(String::from)
                                },
                            )
                            .map_err(|_| "LOGIN_PREFERENCES_CHANGED")?,
                        )
                    }
                    _ => Err("INVALID_FLEET_CONTROL"),
                }
            }
        }
    }
}
