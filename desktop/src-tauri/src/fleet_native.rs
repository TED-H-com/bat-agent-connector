//! Windows-only native facade. WebView fields are logical IDs and observed versions.
#[path = "fleet_native/bootstrap.rs"]
pub(crate) mod bootstrap;
#[path = "fleet_native/controls.rs"]
mod controls;
use crate::fleet::{sanitize_result, FleetRequest};
use crate::fleet_guarded::Guarded;
use crate::fleet_lifecycle::Ticket;
use bat_fleet_core::{
    configuration::{Configuration, Paths},
    discovery::{self, Backend, DiscoveryConfig, NativeIdentity, Ownership},
    installation::{canonical_local, Snapshot},
    monitor_launch,
    selection_io::Store,
    supervisor::Options,
    supervisor_control,
    windows::WindowsMonitorObservation,
    windows_launcher::LauncherMutex,
    windows_monitor_launch::WindowsLaunch,
    Result,
};
pub use controls::Controller;
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

fn now_ms() -> Result<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "CLOCK_UNPROVEN")?
        .as_millis()
        .try_into()
        .map_err(|_| "CLOCK_UNPROVEN")
}
fn directory(name: &str) -> Result<PathBuf> {
    canonical_local(&PathBuf::from(
        std::env::var_os(name).ok_or("LOGIN_PATH_UNAVAILABLE")?,
    ))
}
struct Context {
    installation: Snapshot,
    configuration: Configuration,
    paths: Paths,
    roaming: PathBuf,
    quit_file: PathBuf,
    native: NativeIdentity,
    discovery: DiscoveryConfig,
}
impl Context {
    fn load(installation: Snapshot) -> Result<Self> {
        let roaming = directory("APPDATA")?;
        let user = directory("USERPROFILE")?;
        let username = std::env::var("USERNAME").map_err(|_| "LOGIN_PATH_UNAVAILABLE")?;
        if username.is_empty()
            || username.len() > 256
            || username
                .chars()
                .any(|c| c.is_control() || "\\/:*?\"<>|".contains(c))
        {
            return Err("LOGIN_PATH_UNAVAILABLE");
        }
        let quit_file = directory("TEMP")?.join(format!("bat-fleet-monitor-{username}.quit"));
        let executable =
            canonical_local(&std::env::current_exe().map_err(|_| "INSTALLATION_UNAVAILABLE")?)?;
        let native = NativeIdentity::new(
            executable.to_str().ok_or("INSTALLATION_INVALID")?,
            installation.path().to_str().ok_or("INSTALLATION_INVALID")?,
        )?;
        let paths = Paths::new(installation.client_root(), &user, None, None)?;
        let configuration = Configuration::load(paths.clone())?;
        let discovery = DiscoveryConfig::from_configuration(
            installation
                .client_root()
                .to_str()
                .ok_or("INSTALLATION_INVALID")?,
            &configuration,
            &roaming,
            Some(native.clone()),
        )?;
        installation.verify_current()?;
        Ok(Self {
            installation,
            configuration,
            paths,
            roaming,
            quit_file,
            native,
            discovery,
        })
    }
    fn verify(&self, expected: &str) -> Result<()> {
        self.installation.verify_current()?;
        self.configuration.verify_current()?;
        if expected != self.configuration.binding() {
            return Err("CONFIGURATION_CHANGED");
        }
        Ok(())
    }
    fn configuration(&self) -> Value {
        let mut connections: Vec<_> = self
            .configuration
            .inventory
            .hosts()
            .iter()
            .map(|host| json!({"name":host["name"],"label":host["label"],"kind":"host"}))
            .collect();
        connections.push(json!({"name":"connector","label":self.configuration.inventory.connector()["label"],"kind":"connector"}));
        json!({"valid":self.configuration.issues.is_empty(),"binding":self.configuration.binding(),
            "issues":vec![Value::Null; self.configuration.issues.len()],"connections":connections})
    }
    fn owner_epoch(&self) -> Result<Option<String>> {
        self.verify(self.configuration.binding())?;
        let owner = discovery::discover(&self.discovery, &WindowsMonitorObservation)?;
        if owner.is_none() {
            monitor_launch::verify_absence(
                &self.roaming.join("bat-fleet-monitor-launch.json"),
                &WindowsMonitorObservation,
            )?;
        }
        owner
            .map(|owner| {
                if owner.ownership != Ownership::CurrentLogin || owner.legacy {
                    return Err("OWNER_UNPROVEN");
                }
                owner.instance.ok_or("OWNER_UNPROVEN")
            })
            .transpose()
    }
    fn status(&self) -> Result<Value> {
        self.verify(self.configuration.binding())?;
        let owner = discovery::discover(&self.discovery, &WindowsMonitorObservation)?;
        if owner.is_none() {
            monitor_launch::verify_absence(
                &self.roaming.join("bat-fleet-monitor-launch.json"),
                &WindowsMonitorObservation,
            )?;
        }
        let selected = Store::new(self.roaming.clone()).read(&self.configuration)?;
        // Unreadable/invalid readiness never grants control or readiness; identity failures still refuse.
        let now = now_ms()?;
        let snapshot = supervisor_control::read_snapshot(
            &self.configuration,
            &self.discovery,
            &WindowsMonitorObservation,
            now,
        )
        .ok()
        .flatten();
        let readiness = crate::fleet::native_readiness(snapshot.as_ref(), now)?;
        self.verify(self.configuration.binding())?;
        // A status read cannot combine an old owner with a replacement owner's readiness.
        let current = discovery::discover(&self.discovery, &WindowsMonitorObservation)?;
        if owner.as_ref().map(|o| (&o.process, &o.instance))
            != current.as_ref().map(|o| (&o.process, &o.instance))
        {
            return Err("MONITOR_EPOCH_CHANGED");
        }
        Ok(json!({"configuration":self.configuration(),
            "monitor":{"state":if owner.is_some(){"running"}else{"stopped"},
                "epoch":owner.as_ref().and_then(|o| o.instance.as_deref()),
                "controllable":owner.as_ref().is_some_and(|o| o.ownership == Ownership::CurrentLogin && !o.legacy
                    && o.instance.is_some())},
            "selection":{"revision":selected.revision,"connections":selected.preferences().connections,
                "applied_revision":snapshot.as_ref().map(|s| &s.applied_selection_revision)},
            "readiness":readiness}))
    }
    fn ensure(&self, launcher: &LauncherMutex, ticket: &Ticket) -> Result<()> {
        verify_no_migration(&self.installation)?;
        self.verify(self.configuration.binding())?;
        let outcome = monitor_launch::ensure(
            &self.roaming.join("bat-fleet-monitor-launch.json"),
            &self.native,
            self.configuration.binding(),
            &mut Guarded {
                inner: WindowsLaunch::new(
                    launcher,
                    &self.installation,
                    &self.configuration,
                    &self.discovery,
                    &self.native,
                ),
                ticket,
            },
        )?;
        if matches!(outcome, monitor_launch::Outcome::Running(_)) {
            return Ok(());
        }
        let deadline = Instant::now() + Duration::from_secs(8);
        loop {
            self.verify(self.configuration.binding())?;
            if self.owner_epoch()?.is_some() {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err("MONITOR_STARTING");
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    fn quit(&self, epoch: &str) -> Result<()> {
        self.verify(self.configuration.binding())?;
        let owner = discovery::discover(&self.discovery, &WindowsMonitorObservation)?
            .ok_or("MONITOR_NOT_RUNNING")?;
        if owner.backend != Backend::Rust || owner.instance.as_deref() != Some(epoch) {
            return Err("MONITOR_EPOCH_CHANGED");
        }
        supervisor_control::request_quit(
            &self.configuration,
            &self.discovery,
            &WindowsMonitorObservation,
            &self.quit_file,
            &owner,
        )
    }
}

pub fn request(
    installation: Snapshot,
    input: FleetRequest,
    ticket: &Ticket,
) -> std::result::Result<Value, String> {
    let result = (|| -> Result<(&str, Value)> {
        let context = Context::load(installation)?;
        match input {
            FleetRequest::Contract {} => Ok(("native_contract", json!({
                "implementation":"bat-fleet-rust","implementation_version":"desktop-facade-v1",
                "schema_version":1,"platform":"windows","platform_supported":true,
                "supported_actions":["contract","status","validate_configuration","set_connections","ensure_monitor","quit_owned"],
                "max_request_bytes":65536,"max_response_bytes":262144,"readiness_max_age_s":60}))),
            FleetRequest::ValidateConfiguration {} => Ok(("validate_configuration", context.configuration())),
            FleetRequest::Status {} => Ok(("status", context.status()?)),
            FleetRequest::SetConnections { connections, expected_configuration_binding, expected_selection_revision, expected_monitor_epoch } => {
                let _launcher = ticket.acquire(|| LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY"))?;
                verify_no_migration(&context.installation)?;
                context.verify(&expected_configuration_binding)?;
                let store = Store::new(context.roaming.clone());
                let snapshot = store.read(&context.configuration)?;
                if snapshot.revision != expected_selection_revision { return Err("SELECTION_CHANGED"); }
                store.set_connections(&context.configuration, &snapshot, &connections, expected_monitor_epoch.as_deref(),
                    || { ticket.verify()?; context.owner_epoch() })?;
                Ok(("set_connections", context.status()?))
            }
            FleetRequest::EnsureMonitor { expected_configuration_binding } => {
                context.verify(&expected_configuration_binding)?;
                let launcher = ticket.acquire(|| LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY"))?;
                context.ensure(&launcher, ticket)?;
                Ok(("ensure_monitor", context.status()?))
            }
            FleetRequest::QuitOwned { expected_configuration_binding, expected_monitor_epoch } => {
                let _launcher = ticket.acquire(|| LauncherMutex::try_acquire()?.ok_or("LAUNCHER_BUSY"))?;
                verify_no_migration(&context.installation)?;
                context.verify(&expected_configuration_binding)?;
                ticket.verify()?;
                context.quit(&expected_monitor_epoch)?;
                Ok(("quit_owned", json!({"requested":true,"monitor_epoch":expected_monitor_epoch,"state":"quit_requested"})))
            }
        }
    })().map_err(|code| format!("Fleet refused the request ({code}); read status before retrying"))?;
    if result.0 == "native_contract" {
        Ok(result.1)
    } else {
        sanitize_result(&result.1, result.0)
    }
}

/// Pre-Tauri fixed CLI dispatch. No WebView, central authority or long-lived desktop token.
pub fn run_supervisor(path: &Path) -> Result<()> {
    let context = Context::load(Snapshot::load(path)?)?;
    if context.installation.backend() != Backend::Rust {
        return Err("BACKEND_CHANGED");
    }
    let options = Options {
        configuration: context.paths,
        roaming: context.roaming,
        quit_file: context.quit_file,
    };
    let tailscale =
        bat_fleet_core::windows_startup::program_files_directory()?.join("Tailscale/tailscale.exe");
    let tailscale = if tailscale
        .try_exists()
        .map_err(|_| "ROUTE_EXECUTABLE_INVALID")?
    {
        Some(canonical_local(&tailscale)?)
    } else {
        None
    };
    bat_fleet_core::windows_supervisor::run(
        options,
        context.installation.client_root().into(),
        context.native,
        tailscale.as_deref(),
        || context.installation.verify_current(),
    )
}

/// Caller retains the shared launcher guard through its following effect.
pub fn verify_no_migration(installation: &Snapshot) -> Result<()> {
    let store = bat_fleet_core::migration::Store::new(
        installation.path(),
        &bat_fleet_core::windows_startup::startup_directory()?,
    )?;
    if store.pending()?.is_some() {
        return Err("MIGRATION_PENDING");
    }
    Ok(())
}

/// Blocking lifecycle fence shared by explicit Quit and a caller-owned installer.
/// The closure runs on this thread while Launcher and Monitor guards remain held.
pub fn with_stopped_fleet<T>(
    path: &Path,
    effect: impl FnOnce() -> std::result::Result<T, String>,
) -> std::result::Result<T, String> {
    struct Stop {
        context: Context,
        began: Instant,
    }
    impl crate::fleet_lifecycle::Platform for Stop {
        type Owner = discovery::MonitorIdentity;
        type Launcher = LauncherMutex;
        type Monitor = bat_fleet_core::windows::MonitorMutex;
        fn launcher(&mut self) -> std::result::Result<Self::Launcher, String> {
            LauncherMutex::try_acquire()
                .map_err(String::from)?
                .ok_or("LAUNCHER_BUSY".into())
        }
        fn monitor(&mut self) -> std::result::Result<Self::Monitor, String> {
            bat_fleet_core::windows::MonitorMutex::try_acquire()
                .map_err(String::from)?
                .ok_or("FLEET_STOP_UNCONFIRMED".into())
        }
        fn verify(&self) -> std::result::Result<(), String> {
            self.context
                .verify(self.context.configuration.binding())
                .and_then(|_| verify_no_migration(&self.context.installation))
                .map_err(String::from)
        }
        fn owner(&self) -> std::result::Result<Option<Self::Owner>, String> {
            discovery::discover(&self.context.discovery, &WindowsMonitorObservation)
                .map_err(String::from)
        }
        fn allowed(&self, owner: &Self::Owner) -> bool {
            owner.ownership == Ownership::CurrentLogin && !owner.legacy && owner.instance.is_some()
        }
        fn same(&self, a: &Self::Owner, b: &Self::Owner) -> bool {
            a.process == b.process
                && a.instance == b.instance
                && a.backend == b.backend
                && a.directories == b.directories
        }
        fn quit(&self, owner: &Self::Owner) -> std::result::Result<(), String> {
            supervisor_control::request_quit(
                &self.context.configuration,
                &self.context.discovery,
                &WindowsMonitorObservation,
                &self.context.quit_file,
                owner,
            )
            .map_err(String::from)
        }
        fn launch_absent(&self) -> std::result::Result<(), String> {
            monitor_launch::verify_absence(
                &self.context.roaming.join("bat-fleet-monitor-launch.json"),
                &WindowsMonitorObservation,
            )
            .map_err(String::from)
        }
        fn elapsed(&self) -> Duration {
            self.began.elapsed()
        }
        fn wait(&mut self) {
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    let context =
        Context::load(Snapshot::load(path).map_err(String::from)?).map_err(String::from)?;
    crate::fleet_lifecycle::with_stopped(
        &mut Stop {
            context,
            began: Instant::now(),
        },
        effect,
    )
}

/// Missing installation configuration grants no ownership. Retain both account-wide mutexes
/// and prove independent absence immediately before Quit/installation; never stop a process here.
pub fn with_unconfigured_fleet_absent<T>(
    path: &Path,
    effect: impl FnOnce() -> std::result::Result<T, String>,
) -> std::result::Result<T, String> {
    struct Absent {
        path: PathBuf,
        roaming: PathBuf,
        login: bat_fleet_core::process_adapter::LoginIdentity,
    }
    impl crate::fleet_lifecycle::AbsencePlatform for Absent {
        type Launcher = LauncherMutex;
        type Monitor = bat_fleet_core::windows::MonitorMutex;
        fn launcher(&mut self) -> std::result::Result<Self::Launcher, String> {
            let guard = LauncherMutex::try_acquire()
                .map_err(String::from)?
                .ok_or("LAUNCHER_BUSY")?;
            if guard.login() != &self.login {
                return Err("OTHER_LOGIN_OWNER".into());
            }
            Ok(guard)
        }
        fn monitor(&mut self) -> std::result::Result<Self::Monitor, String> {
            let guard = bat_fleet_core::windows::MonitorMutex::try_acquire()
                .map_err(String::from)?
                .ok_or("FLEET_STOP_UNCONFIRMED")?;
            if guard.login() != &self.login {
                return Err("OTHER_LOGIN_OWNER".into());
            }
            Ok(guard)
        }
        fn verify_absence(&self) -> std::result::Result<(), String> {
            if bat_fleet_core::windows::current_login().map_err(String::from)? != self.login
                || directory("APPDATA").map_err(String::from)? != self.roaming
            {
                return Err("OTHER_LOGIN_OWNER".into());
            }
            bat_fleet_core::unconfigured::verify_absence(
                &self.path,
                &self.roaming,
                &WindowsMonitorObservation,
            )
            .map_err(String::from)
        }
    }
    if !path.is_absolute() || !bat_fleet_core::installation::local_path(path) {
        return Err("INSTALLATION_INVALID".into());
    }
    let mut platform = Absent {
        // canonical_local requires an existing file; absence is the exact proof below.
        path: path.to_path_buf(),
        roaming: directory("APPDATA").map_err(String::from)?,
        login: bat_fleet_core::windows::current_login().map_err(String::from)?,
    };
    crate::fleet_lifecycle::with_absent(&mut platform, effect)
}

/// Read-only fixed login policy before constructing Tauri or enforcing a single instance.
pub fn login_options(path: &Path) -> Result<(bool, bool)> {
    let c = Context::load(Snapshot::load(path)?)?;
    verify_no_migration(&c.installation)?;
    let picker =
        crate::desktop_preferences::load(&c.roaming).map_err(|_| "LOGIN_PREFERENCES_INVALID")?;
    let selection = Store::new(c.roaming.clone()).read(&c.configuration)?;
    Ok((picker.show_picker, selection.preferences().dashboard))
}

/// The legacy fixed PS facade shares native unpublished-launch ambiguity as well as migration exclusion.
pub fn verify_control_owner(installation: &Snapshot, starting: bool) -> Result<()> {
    let c = Context::load(installation.clone())?;
    c.owner_epoch()?;
    if starting
        && discovery::discover(&c.discovery, &WindowsMonitorObservation)?
            .is_some_and(|owner| owner.backend != c.installation.backend())
    {
        return Err("BACKEND_OWNER_CHANGED");
    }
    Ok(())
}
