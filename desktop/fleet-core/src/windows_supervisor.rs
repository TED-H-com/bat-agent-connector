//! Fixed supervisor entrypoint; the account mutex never moves off this calling thread.
use crate::{
    configuration::Configuration,
    discovery::{
        self, Backend, DiscoveryConfig, MonitorRecord, NativeIdentity, Observation, Ownership,
    },
    ownership::ProcessState,
    process_adapter::{
        legacy_created, LoginIdentity, ProcessSnapshot, StopMode, StopOutcome, TunnelRecord,
    },
    route::NativeRouteProbe,
    supervisor::{Effects, Options, Supervisor},
    supervisor_io::MonitorLease,
    supervisor_probe::NativeProbes,
    tunnel::{self, Launched, Plan},
    windows::{MonitorMutex, WindowsMonitorObservation},
    windows_tunnel::{LaunchedProcess, WindowsPlatform},
    Result,
};
use std::{
    path::{Path, PathBuf},
    rc::Rc,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

pub struct WindowsEffects<'a> {
    mutex: Rc<MonitorMutex>,
    kit: PathBuf,
    roaming: PathBuf,
    native: NativeIdentity,
    verify_installation: &'a dyn Fn() -> Result<()>,
}
impl<'a> WindowsEffects<'a> {
    /// kit is the reviewed script/configuration directory (normally kit_root/client),
    /// not its repository parent. Paths have already passed native installation validation.
    pub fn new(
        mutex: Rc<MonitorMutex>,
        kit: PathBuf,
        roaming: PathBuf,
        native: NativeIdentity,
        verify_installation: &'a dyn Fn() -> Result<()>,
    ) -> Result<Self> {
        if !kit.is_absolute() || !roaming.is_absolute() {
            return Err("CONFIGURATION_INVALID");
        }
        Ok(Self {
            mutex,
            kit,
            roaming,
            native,
            verify_installation,
        })
    }
    fn discovery(&self, configuration: &Configuration) -> Result<DiscoveryConfig> {
        DiscoveryConfig::from_configuration(
            self.kit.to_str().ok_or("CONFIGURATION_INVALID")?,
            configuration,
            &self.roaming,
            Some(self.native.clone()),
        )
    }
}
impl Observation for WindowsEffects<'_> {
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
impl Effects for WindowsEffects<'_> {
    type Child = LaunchedProcess;
    fn prove_exclusive(&self) -> Result<()> {
        if &self.current_login()? != self.mutex.login() {
            return Err("OTHER_LOGIN_OWNER");
        }
        Ok(())
    }
    fn prove_absence(&self, configuration: &Configuration) -> Result<()> {
        (self.verify_installation)().map_err(|_| "INSTALLATION_CHANGED")?;
        self.prove_exclusive()?;
        if let Some(owner) = discovery::discover(&self.discovery(configuration)?, self)? {
            return Err(if owner.process.pid == std::process::id() {
                "OWNER_UNPROVEN"
            } else {
                "MONITOR_ALREADY_RUNNING"
            });
        }
        Ok(())
    }
    fn record(&self, configuration: &Configuration, epoch: &str) -> Result<MonitorRecord> {
        (self.verify_installation)().map_err(|_| "INSTALLATION_CHANGED")?;
        self.prove_exclusive()?;
        let process = self.observe(std::process::id())?.ok_or("OWNER_UNPROVEN")?;
        if !discovery::windows_path(&process.executable)?
            .eq_ignore_ascii_case(self.native.executable())
            || process.arguments != self.native.arguments()
            || &process.login != self.mutex.login()
        {
            return Err("OWNER_UNPROVEN");
        }
        Ok(MonitorRecord {
            pid: process.pid,
            created: legacy_created(process.created_filetime)?,
            executable: process.executable,
            owner_sid: process.login.owner_sid,
            session_id: process.login.session_id,
            instance: epoch.into(),
            backend: Backend::Rust,
            script: None,
            arguments: Some(process.arguments),
            inventory_path: Some(
                configuration
                    .inventory_path()
                    .to_str()
                    .ok_or("CONFIGURATION_INVALID")?
                    .into(),
            ),
            profile_index_path: Some(
                configuration
                    .profile_index_path()
                    .to_str()
                    .ok_or("CONFIGURATION_INVALID")?
                    .into(),
            ),
            created_filetime: Some(process.created_filetime.to_string()),
        })
    }
    fn verify_owner(&self, configuration: &Configuration, lease: &MonitorLease) -> Result<()> {
        self.prove_exclusive()?;
        lease.verify(self)?;
        let owner =
            discovery::discover(&self.discovery(configuration)?, self)?.ok_or("OWNER_UNPROVEN")?;
        if owner.backend != Backend::Rust
            || owner.legacy
            || owner.ownership != Ownership::CurrentLogin
            || owner.instance.as_deref() != Some(lease.epoch())
            || !lease.process_matches(&owner.process)
        {
            return Err("OWNER_UNPROVEN");
        }
        Ok(())
    }
    fn listening(&self, configuration: &Configuration, name: &str) -> Result<bool> {
        let row = if name == "connector" {
            configuration.inventory.connector()
        } else {
            configuration
                .inventory
                .hosts()
                .iter()
                .find(|row| row["name"] == name)
                .ok_or("CONNECTION_NOT_CONFIGURED")?
        };
        let endpoint =
            crate::inventory::endpoint(row["local"].as_str().ok_or("INVENTORY_INVALID")?, true)?;
        let address = std::net::SocketAddr::new(
            endpoint.address.parse().map_err(|_| "INVENTORY_INVALID")?,
            endpoint.port,
        );
        match std::net::TcpStream::connect_timeout(&address, Duration::from_secs(3)) {
            Ok(_) => Ok(true),
            Err(error) if error.kind() == std::io::ErrorKind::ConnectionRefused => Ok(false),
            Err(_) => {
                crate::monitor_log::event("LISTENER_UNPROVEN");
                Err("LISTENER_UNPROVEN")
            }
        }
    }
    fn launch(
        &mut self,
        context: &tunnel::Context<'_>,
        plan: &Plan,
    ) -> Result<Launched<Self::Child>> {
        (self.verify_installation)().map_err(|_| "INSTALLATION_CHANGED")?;
        let discovery = self.discovery(context.configuration)?;
        tunnel::launch(
            context,
            plan,
            &mut WindowsPlatform::new(&self.mutex, &discovery)?,
        )
    }
    fn stop(&mut self, record: &TunnelRecord, mode: StopMode<'_>) -> Result<StopOutcome> {
        self.mutex.stop_tunnel(record, mode)
    }
}
fn now_ms() -> Result<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "CLOCK_UNPROVEN")?
        .as_millis()
        .try_into()
        .map_err(|_| "CLOCK_UNPROVEN")
}
/// This is called only from the fixed pre-Tauri --fleet-supervisor CLI dispatch.
/// Closing/crashing a WebView cannot end this separately owned process.
pub fn run(
    options: Options,
    kit_client_directory: PathBuf,
    native: NativeIdentity,
    tailscale: Option<&Path>,
    verify_installation: impl Fn() -> Result<()>,
) -> Result<()> {
    crate::monitor_log::initialize(&options.roaming);
    verify_installation().map_err(|_| "INSTALLATION_CHANGED")?;
    let mutex = Rc::new(MonitorMutex::try_acquire()?.ok_or("MONITOR_ALREADY_RUNNING")?);
    let routes = Arc::new(NativeRouteProbe::new(tailscale)?);
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "SUPERVISOR_UNAVAILABLE")?;
    runtime.block_on(async {
        verify_installation().map_err(|_| "INSTALLATION_CHANGED")?;
        let mut supervisor = loop {
            verify_installation().map_err(|_| "INSTALLATION_CHANGED")?;
            let effects = WindowsEffects::new(
                mutex.clone(),
                kit_client_directory.clone(),
                options.roaming.clone(),
                native.clone(),
                &verify_installation,
            )?;
            match Supervisor::start(options.clone(), effects, NativeProbes, routes.clone()) {
                Ok(supervisor) => break supervisor,
                Err(error) => {
                    crate::monitor_log::iteration(Err(error))?;
                    tokio::time::sleep(Duration::from_secs(1)).await;
                }
            }
        };
        loop {
            let result = match now_ms() {
                Ok(now) => supervisor
                    .tick_guarded(now, &verify_installation)
                    .await
                    .map(|_| ()),
                Err(error) => Err(error),
            };
            crate::monitor_log::iteration(result)?;
            if supervisor.stopped() {
                return Ok(());
            }
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_secs(1)) => (),
                _ = supervisor.route_progress() => (),
            }
        }
    })
}
