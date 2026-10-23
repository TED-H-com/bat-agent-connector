//! One native owner; readiness futures cannot launch or stop processes.
use crate::{
    configuration::{Configuration, Paths},
    discovery::{MonitorRecord, Observation},
    ownership::{origin_state, ProbeGeneration, ProcessState},
    probe::{Layer, ProbeKind, ProbeObservation},
    process_adapter::{HeldProcess, StopMode, StopOutcome, TunnelRecord},
    route::{
        HostPolicy, RouteChoice, RouteFailure, RouteProbe, RouteRequirement, Routes, Run,
        SelectionRequest,
    },
    selection::launch_plan,
    selection_io::{Snapshot, Store},
    supervisor_io::{self as files, MonitorLease, TunnelFile},
    supervisor_probe::ProbeFactory,
    supervisor_status::{self, Entry},
    tunnel::{self, Launched, Plan},
    Result,
};
use serde_json::json;
use std::{
    collections::{BTreeMap, HashMap},
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{sync::Notify, task::JoinHandle};

/// Native configuration only. These paths are never accepted from a WebView.
pub struct Options {
    pub configuration: Paths,
    pub roaming: PathBuf,
    pub quit_file: PathBuf,
}
/// Implementations must retain the shared OS mutex for their entire lifetime.
pub trait Effects: Observation {
    type Child: HeldProcess;
    fn prove_exclusive(&self) -> Result<()>;
    fn prove_absence(&self, configuration: &Configuration) -> Result<()>;
    fn record(&self, configuration: &Configuration, epoch: &str) -> Result<MonitorRecord>;
    fn verify_owner(&self, configuration: &Configuration, lease: &MonitorLease) -> Result<()>;
    fn launch(
        &mut self,
        context: &tunnel::Context<'_>,
        plan: &Plan,
    ) -> Result<Launched<Self::Child>>;
    fn stop(&mut self, record: &TunnelRecord, mode: StopMode<'_>) -> Result<StopOutcome>;
}
enum Work {
    Route(JoinHandle<std::result::Result<RouteChoice, RouteFailure>>),
    Probe(JoinHandle<ProbeObservation>),
}
impl Work {
    fn finished(&self) -> bool {
        match self {
            Self::Route(h) => h.is_finished(),
            Self::Probe(h) => h.is_finished(),
        }
    }
    async fn cancel(self) {
        match self {
            Self::Route(h) => {
                h.abort();
                let _ = h.await;
            }
            Self::Probe(h) => {
                h.abort();
                let _ = h.await;
            }
        }
    }
}
struct Owned<C> {
    launch: Launched<C>,
    run: Option<Run>,
}
struct Host<C> {
    label: String,
    kind: ProbeKind,
    selected: bool,
    generation: ProbeGeneration,
    routes: Routes,
    policy: HostPolicy,
    credential: Option<String>,
    worker: Option<Work>,
    observation: Option<ProbeObservation>,
    route: Option<crate::route::RouteObservation>,
    route_requirement: Option<RouteRequirement>,
    owned: Option<Owned<C>>,
    failure: Option<(&'static str, Layer)>,
    next_probe: u64,
}
impl<C> Host<C> {
    async fn invalidate(&mut self) {
        if let Some(worker) = self.worker.take() {
            worker.cancel().await;
        }
        self.policy.stop_wake().await;
        self.observation = None;
        self.route = None;
        self.route_requirement = None;
        self.next_probe = 0;
    }
}
pub struct Supervisor<E: Effects, F: ProbeFactory, R: RouteProbe> {
    options: Options,
    effects: E,
    probes: F,
    route_probe: Arc<R>,
    route_progress: Arc<Notify>,
    configuration: Configuration,
    store: Store,
    selection: Snapshot,
    lease: MonitorLease,
    hosts: BTreeMap<String, Host<E::Child>>,
    client_info: serde_json::Value,
    generation: u64,
    applied_revision: String,
    quitting: bool,
    stopped: bool,
}
fn next(value: &mut u64) -> Result<u64> {
    *value = value.checked_add(1).ok_or("GENERATION_EXHAUSTED")?;
    Ok(*value)
}
fn snapshot_generation(
    epoch: &str,
    configuration: &Configuration,
    selection: &Snapshot,
    generation: u64,
) -> ProbeGeneration {
    ProbeGeneration {
        epoch: epoch.into(),
        configuration_binding: configuration.binding().into(),
        selection_revision: selection.revision.clone(),
        generation,
    }
}
impl<E: Effects, F: ProbeFactory, R: RouteProbe + Send + 'static> Supervisor<E, F, R> {
    pub fn start(options: Options, effects: E, probes: F, route_probe: Arc<R>) -> Result<Self> {
        effects.prove_exclusive()?;
        if !options.roaming.is_absolute() || !options.quit_file.is_absolute() {
            return Err("CONFIGURATION_INVALID");
        }
        let configuration = Configuration::load(options.configuration.clone())?;
        effects.prove_absence(&configuration)?;
        let epoch = format!("{:032x}", rand::random::<u128>());
        let record = effects.record(&configuration, &epoch)?;
        let lease = MonitorLease::publish(&options.roaming, record, &effects, || {
            effects.prove_exclusive()?;
            configuration.verify_current()?;
            effects.prove_absence(&configuration)
        })?;
        effects.verify_owner(&configuration, &lease)?;
        let store = Store::new(options.roaming.clone());
        let selection = store.read(&configuration)?;
        let device_path = lease.directory().join("fleet-probe-device-id");
        let old = files::read(&device_path)?;
        let device = match &old {
            Some(bytes) => std::str::from_utf8(bytes)
                .ok()
                .map(str::trim)
                .filter(|s| crate::ownership::epoch_valid(s))
                .ok_or("DEVICE_ID_UNPROVEN")?
                .to_owned(),
            None => {
                let device = format!("{:032x}", rand::random::<u128>());
                files::replace(&device_path, None, device.as_bytes(), || {
                    effects.verify_owner(&configuration, &lease)
                })?;
                device
            }
        };
        let client_info = json!({"appName":"BAT Fleet Monitor", "appVersion":env!("CARGO_PKG_VERSION"),
            "label":"fleet-monitor", "deviceId":device, "platform":"windows"});
        let applied_revision = selection.revision.clone();
        let mut out = Self {
            options,
            effects,
            probes,
            route_probe,
            route_progress: Arc::default(),
            configuration,
            store,
            selection,
            lease,
            hosts: BTreeMap::new(),
            client_info,
            generation: 0,
            applied_revision,
            quitting: false,
            stopped: false,
        };
        out.add_hosts()?;
        Ok(out)
    }
    /// Wake transitions interrupt the native idle wait, so short wake attempts
    /// are published without polling every host more frequently.
    pub async fn route_progress(&self) {
        self.route_progress.notified().await;
    }
    pub fn epoch(&self) -> &str {
        self.lease.epoch()
    }
    pub fn stopped(&self) -> bool {
        self.stopped
    }
    fn add_hosts(&mut self) -> Result<()> {
        let selected =
            launch_plan(&self.configuration.inventory, self.selection.preferences()).connect;
        let mut rows = self.configuration.inventory.hosts().to_vec();
        rows.push(self.configuration.inventory.connector().clone());
        for row in rows {
            let name = row["name"].as_str().unwrap();
            if self.hosts.contains_key(name) {
                continue;
            }
            let generation = snapshot_generation(
                self.epoch(),
                &self.configuration,
                &self.selection,
                self.generation,
            );
            let routes = Routes::from_inventory(&self.configuration.inventory, name)?;
            let mut policy = HostPolicy::new(&routes, generation.clone());
            policy.notify_wakes(self.route_progress.clone());
            self.hosts.insert(
                name.into(),
                Host {
                    label: row["label"].as_str().unwrap().into(),
                    kind: if name == "connector" {
                        ProbeKind::Connector
                    } else {
                        ProbeKind::Bat
                    },
                    selected: selected.iter().any(|s| s == name),
                    generation,
                    routes,
                    policy,
                    credential: None,
                    worker: None,
                    observation: None,
                    route: None,
                    route_requirement: None,
                    owned: None,
                    failure: None,
                    next_probe: 0,
                },
            );
        }
        Ok(())
    }
    async fn invalidate_all(&mut self) {
        for host in self.hosts.values_mut() {
            host.invalidate().await;
        }
    }
    fn verify(&self) -> Result<()> {
        self.effects.prove_exclusive()?;
        self.effects
            .verify_owner(&self.configuration, &self.lease)?;
        self.configuration.verify_current()?;
        if crate::configuration::data_directory(&self.options.roaming)? != self.lease.directory()
            || !self
                .selection
                .same_snapshot(&self.store.read(&self.configuration)?)
        {
            return Err("SELECTION_CHANGED");
        }
        Ok(())
    }
    /// A stop intent fences an uncertain termination across supervisor restarts.
    fn stop_file(&mut self, file: &TunnelFile, owned: bool) -> Result<()> {
        let record = file.record.as_ref().map_err(|_| "OWNER_UNPROVEN")?;
        if record.process.owner_sid != self.lease.record.owner_sid
            || record.process.session_id != self.lease.record.session_id
        {
            return Err("OTHER_LOGIN_OWNER");
        }
        if files::read(&file.path)?.as_deref() != Some(&file.bytes) {
            return Err("TUNNEL_RECORD_CHANGED");
        }
        let dir = file
            .path
            .parent()
            .and_then(Path::parent)
            .ok_or("OWNER_UNPROVEN")?;
        let marker = dir
            .join("fleet-tunnel-stop-intents")
            .join(format!("{}.json", file.name));
        let marker_bytes = files::stop_intent_bytes(&file.bytes, record)?;
        let ended = origin_state(
            record.process.pid,
            &record.process.created,
            &self.effects.state(record.process.pid),
        ) == ProcessState::Dead;
        let previous = files::read(&marker)?;
        if let Some(bytes) = &previous {
            files::classify_intent(
                files::TunnelKind::StopIntent,
                bytes,
                &file.name,
                Some((&file.bytes, record)),
            )?;
        }
        if !ended {
            if previous.is_some() {
                return Err("STOP_UNCONFIRMED");
            }
            // Do not create a stop intent for unknown/foreign evidence. The effect
            // adapter repeats this proof with the retained termination handle.
            let actual = self
                .effects
                .observe(record.process.pid)?
                .ok_or("OWNER_UNPROVEN")?;
            if !actual
                .tunnel_evidence()
                .is_ok_and(|evidence| record.process.matches(&evidence))
                || record
                    .created_filetime
                    .is_some_and(|time| time != actual.created_filetime)
                || actual.login.owner_sid != self.lease.record.owner_sid
                || actual.login.session_id != self.lease.record.session_id
                || !owned
                    && !record.origin.as_ref().is_some_and(|p| {
                        origin_state(p.pid, &p.created, &self.effects.state(p.pid))
                            == ProcessState::Dead
                    })
            {
                return Err("OWNER_UNPROVEN");
            }
            self.effects
                .verify_owner(&self.configuration, &self.lease)?;
            files::replace(&marker, None, &marker_bytes, || {
                self.effects.verify_owner(&self.configuration, &self.lease)
            })?;
            let mode = if owned {
                StopMode::Owned {
                    current_epoch: self.lease.epoch(),
                }
            } else {
                StopMode::Orphan
            };
            self.effects.stop(record, mode)?;
        }
        // Keep the full child/origin receipt until every associated intent is
        // retired. A crash or failed deletion must leave enough evidence for
        // restart to prove the child ended and finish without another stop.
        self.clear_launch_intent(file)?;
        if let Some(bytes) = files::read(&marker)? {
            files::classify_intent(
                files::TunnelKind::StopIntent,
                &bytes,
                &file.name,
                Some((&file.bytes, record)),
            )?;
            files::remove(&marker, &bytes)?;
        }
        files::remove(&file.path, &file.bytes)?;
        Ok(())
    }
    fn clear_launch_intent(&self, file: &TunnelFile) -> Result<()> {
        let record = file.record.as_ref().map_err(|_| "OWNER_UNPROVEN")?;
        let dir = file
            .path
            .parent()
            .and_then(Path::parent)
            .ok_or("OWNER_UNPROVEN")?;
        let path = dir
            .join("fleet-tunnel-intents")
            .join(format!("{}.json", file.name));
        let Some(bytes) = files::read(&path)? else {
            return Ok(());
        };
        files::classify_intent(
            files::TunnelKind::LaunchIntent,
            &bytes,
            &file.name,
            Some((&file.bytes, record)),
        )?;
        files::remove(&path, &bytes)
    }
    fn owned_file(name: &str, owned: &Owned<E::Child>) -> TunnelFile {
        TunnelFile {
            name: name.into(),
            path: owned.launch.record_path.clone(),
            bytes: owned.launch.record_bytes.clone(),
            record: Ok(owned.launch.record.clone()),
        }
    }
    async fn sync_inputs(&mut self, now: u64) -> Result<()> {
        let current = match Configuration::load(self.options.configuration.clone()) {
            Ok(c) => c,
            Err(error) => {
                self.invalidate_all().await;
                return Err(error);
            }
        };
        if current.binding() != self.configuration.binding() {
            self.invalidate_all().await;
            // Existing fixed process receipts retain stop authority; changed inventory
            // never retargets an old child. Failure retains it for explicit recovery.
            let names = self.hosts.keys().cloned().collect::<Vec<_>>();
            let mut unsettled = false;
            for name in names {
                let file = self.hosts[&name]
                    .owned
                    .as_ref()
                    .map(|o| Self::owned_file(&name, o));
                if let Some(file) = file {
                    if self.stop_file(&file, true).is_err() {
                        unsettled = true;
                    } else {
                        self.hosts.get_mut(&name).unwrap().owned = None;
                    }
                }
            }
            if unsettled {
                return Err("STOP_UNCONFIRMED");
            }
            self.configuration = current;
            self.hosts.clear();
            self.selection = self.store.read(&self.configuration)?;
            next(&mut self.generation)?;
            self.add_hosts()?;
        }
        let selection = self.store.read(&self.configuration)?;
        if !self.selection.same_snapshot(&selection) {
            next(&mut self.generation)?;
            let selected =
                launch_plan(&self.configuration.inventory, selection.preferences()).connect;
            let epoch = self.epoch().to_owned();
            for host in self.hosts.values_mut() {
                host.invalidate().await;
                let generation =
                    snapshot_generation(&epoch, &self.configuration, &selection, self.generation);
                host.policy
                    .update_selection_generation(&host.generation, generation.clone())?;
                host.generation = generation;
            }
            for (name, host) in &mut self.hosts {
                host.selected = selected.contains(name);
            }
            self.selection = selection;
        }
        let names = self.hosts.keys().cloned().collect::<Vec<_>>();
        for name in names {
            if !self.hosts[&name].selected {
                let file = self.hosts[&name]
                    .owned
                    .as_ref()
                    .map(|o| Self::owned_file(&name, o));
                if let Some(file) = file {
                    match self.stop_file(&file, true) {
                        Ok(()) => {
                            let h = self.hosts.get_mut(&name).unwrap();
                            if let Some(run) = h.owned.as_ref().and_then(|o| o.run.as_ref()) {
                                h.policy.exited(run, now, true)?;
                            }
                            h.owned = None;
                            if h.failure
                                .is_some_and(|(code, _)| code == "STOP_UNCONFIRMED")
                            {
                                h.failure = None;
                            }
                        }
                        Err(error) => {
                            self.hosts.get_mut(&name).unwrap().failure =
                                Some((error, Layer::Tunnel));
                        }
                    }
                }
            }
        }
        if self.hosts.values().all(|h| h.selected || h.owned.is_none()) {
            self.applied_revision = self.selection.revision.clone();
        }
        Ok(())
    }
    async fn reconcile_records(&mut self, now: u64) -> Result<HashMap<String, &'static str>> {
        let scan = files::scan(&self.options.roaming, &self.lease)?;
        let mut blocked = HashMap::new();
        let mut confirmed = Vec::new();
        // Check every record, including removed/unselected hosts in the old directory.
        for file in scan.owners {
            let matching = self
                .hosts
                .get(&file.name)
                .and_then(|h| h.owned.as_ref())
                .is_some_and(|o| {
                    o.launch.record_path == file.path && o.launch.record_bytes == file.bytes
                });
            if matching {
                let h = self.hosts.get_mut(&file.name).unwrap();
                let owned = h.owned.as_ref().unwrap();
                let record = &owned.launch.record;
                let process = origin_state(
                    record.process.pid,
                    &record.process.created,
                    &self.effects.state(record.process.pid),
                );
                if process == ProcessState::Dead {
                    h.invalidate().await;
                    if let Some(run) = h.owned.as_ref().and_then(|o| o.run.as_ref()) {
                        h.policy.exited(run, now, false)?;
                    }
                    match self.stop_file(&file, true) {
                        Ok(()) => {
                            let host = self.hosts.get_mut(&file.name).unwrap();
                            host.owned = None;
                            if host
                                .failure
                                .is_some_and(|(code, _)| code == "STOP_UNCONFIRMED")
                            {
                                host.failure = None;
                            }
                        }
                        Err(error) => {
                            blocked.insert(file.name.clone(), error);
                        }
                    }
                } else {
                    let actual = owned.launch.child.snapshot();
                    if !actual.as_ref().is_ok_and(|p| {
                        p.tunnel_evidence()
                            .is_ok_and(|evidence| record.process.matches(&evidence))
                            && p.created_filetime
                                == record.created_filetime.unwrap_or(p.created_filetime)
                    }) {
                        h.invalidate().await;
                        blocked.insert(file.name.clone(), "OWNER_UNPROVEN");
                    } else {
                        if let Some(run) = &owned.run {
                            h.policy.observed_alive(run, now)?;
                        }
                        let in_use =
                            matches!(process, ProcessState::Live { .. }) && owned.run.is_some();
                        if let Err(error) = self.clear_launch_intent(&file) {
                            blocked.insert(file.name.clone(), error);
                        } else if in_use {
                            confirmed.push(file.name.clone());
                        }
                    }
                }
            } else {
                match self.stop_file(&file, false) {
                    Ok(()) => (),
                    Err(error) => {
                        blocked.insert(file.name.clone(), error);
                    }
                }
            }
        }
        for name in files::scan(&self.options.roaming, &self.lease)?.blocked {
            blocked.insert(name, "TUNNEL_START_UNSETTLED");
        }
        // A missing/replaced ownership receipt never becomes permission to relaunch.
        for (name, host) in &self.hosts {
            if let Some(owned) = &host.owned {
                if files::read(&owned.launch.record_path)?.as_deref()
                    != Some(&owned.launch.record_bytes)
                {
                    blocked.insert(name.clone(), "OWNER_UNPROVEN");
                }
            }
        }
        // Renew only after every ownership/intent/receipt check for this host
        // has passed. Unknown liveness keeps the previous confirmation time.
        for name in confirmed {
            if !blocked.contains_key(&name) {
                let host = self.hosts.get_mut(&name).unwrap();
                if let Some(route) = &mut host.route {
                    route.confirm_use(&host.generation, now);
                }
            }
        }
        Ok(blocked)
    }
    pub async fn tick(&mut self, now: u64) -> Result<supervisor_status::Snapshot> {
        self.tick_guarded(now, &|| Ok(())).await
    }
    /// The installation snapshot is a native caller authority, separate from
    /// Kit inputs and original process ownership. Drift latches normal shutdown;
    /// restoring the file cannot restart work under this incarnation.
    pub async fn tick_guarded(
        &mut self,
        now: u64,
        verify_installation: &dyn Fn() -> Result<()>,
    ) -> Result<supervisor_status::Snapshot> {
        if self.stopped {
            return Err("SUPERVISOR_STOPPED");
        }
        if verify_installation().is_err() {
            return self.shutdown(now).await;
        }
        if let Err(error) = self.effects.verify_owner(&self.configuration, &self.lease) {
            self.invalidate_all().await;
            return Err(error);
        }
        if let Some(bytes) = files::quit_bytes(&self.options.quit_file, &self.lease)? {
            files::remove(&self.options.quit_file, &bytes)?;
            self.quitting = true;
        }
        if self.quitting {
            return self.shutdown(now).await;
        }
        self.sync_inputs(now).await?;
        self.verify()?;
        let blocked = self.reconcile_records(now).await?;
        let mut names = self.hosts.keys().cloned().collect::<Vec<_>>();
        names.sort_by_key(|name| (name != "connector", self.hosts[name].next_probe));
        for name in names {
            if verify_installation().is_err() {
                return self.shutdown(now).await;
            }
            let mut host = self.hosts.remove(&name).unwrap();
            let result = self
                .tick_host(&name, &mut host, blocked.get(&name).copied(), now)
                .await;
            self.hosts.insert(name, host);
            if result == Err("INSTALLATION_CHANGED") {
                return self.shutdown(now).await;
            }
            result?;
        }
        // A later host's slow local work cannot allow an earlier credential to
        // change unnoticed before publication. No fingerprint leaves this map.
        for (name, host) in &mut self.hosts {
            if host.selected && host.credential.is_some() {
                let current = self
                    .probes
                    .credential(&self.configuration, self.lease.directory(), name)
                    .and_then(|credential| {
                        self.probes.binding(
                            &self.configuration,
                            self.lease.directory(),
                            name,
                            &credential,
                        )
                    });
                if current.as_ref().ok() != host.credential.as_ref() {
                    host.invalidate().await;
                    host.credential = None;
                    host.failure = Some(("CREDENTIAL_CHANGED", Layer::Auth));
                }
            }
        }
        if verify_installation().is_err() {
            return self.shutdown(now).await;
        }
        self.publish(
            now,
            if self.applied_revision == self.selection.revision {
                "running"
            } else {
                "selection_pending"
            },
        )
    }
    async fn tick_host(
        &mut self,
        name: &str,
        host: &mut Host<E::Child>,
        blocked: Option<&'static str>,
        now: u64,
    ) -> Result<()> {
        if !host.selected {
            host.invalidate().await;
            return Ok(());
        }
        let issue = self
            .configuration
            .issues
            .iter()
            .find(|i| i.host == name)
            .map(|i| (i.code, Layer::Inventory));
        if let Some(failure) = issue.or(blocked.map(|code| (code, Layer::Tunnel))) {
            host.invalidate().await;
            host.failure = Some(failure);
            return Ok(());
        }
        if let Err(error) = self
            .probes
            .source(&self.configuration, self.lease.directory(), name)
        {
            host.invalidate().await;
            host.credential = None;
            host.failure = Some((error, Layer::Inventory));
            return Ok(());
        }
        // The selected transport is authorized by preferences/configuration. A
        // missing readonly probe credential does not block creating that tunnel.
        let resolved = self
            .probes
            .credential(&self.configuration, self.lease.directory(), name)
            .and_then(|credential| {
                self.probes
                    .binding(
                        &self.configuration,
                        self.lease.directory(),
                        name,
                        &credential,
                    )
                    .map(|binding| (credential, binding))
            });
        let (credential, fingerprint, credential_error) = match resolved {
            Ok((credential, binding)) => (Some(credential), Some(binding), None),
            Err(error) => (None, None, Some(error)),
        };
        if host.credential != fingerprint {
            host.invalidate().await;
            next(&mut self.generation)?;
            let mut generation = host.generation.clone();
            generation.generation = self.generation;
            host.policy
                .update_selection_generation(&host.generation, generation.clone())?;
            host.generation = generation;
            host.credential = fingerprint;
        }
        if credential.is_none() {
            if matches!(host.worker, Some(Work::Probe(_))) {
                host.worker.take().unwrap().cancel().await;
            }
            host.observation = None;
        }
        host.failure = credential_error.map(|error| (error, Layer::Auth));
        if host.worker.as_ref().is_some_and(Work::finished) {
            match host.worker.take().unwrap() {
                Work::Probe(handle) => {
                    if let Ok(observation) = handle.await {
                        if crate::ownership::probe_current(
                            &observation.generation,
                            &host.generation,
                            observation.observed_ms,
                            now,
                        ) {
                            host.next_probe = now.saturating_add(if observation.code.is_some() {
                                15000
                            } else {
                                30000
                            });
                            host.observation = Some(observation);
                        }
                    }
                }
                Work::Route(handle) => match handle.await {
                    Ok(Ok(choice)) => {
                        host.route = None;
                        host.route_requirement = None;
                        let alias = choice.alias(&host.policy, &host.generation, now)?;
                        self.verify()?;
                        let plan = Plan::new(
                            &self.configuration,
                            &self.selection,
                            name,
                            alias,
                            self.epoch(),
                        )?;
                        host.policy.take_recovery_attempt(now)?;
                        let context = tunnel::Context {
                            configuration: &self.configuration,
                            store: &self.store,
                            selection: &self.selection,
                            roaming: &self.options.roaming,
                        };
                        match self.effects.launch(&context, &plan) {
                            Ok(launch) => {
                                let run = host.policy.started(&choice, &host.generation, now);
                                host.route = run.as_ref().ok().map(|_| choice.observation(now));
                                host.owned = Some(Owned {
                                    launch,
                                    run: run.ok(),
                                });
                                host.observation = None;
                                host.next_probe = 0;
                            }
                            Err("TUNNEL_START_FAILED") => {
                                host.policy.launch_failed(&choice, &host.generation, now)?;
                                host.failure = Some(("TUNNEL_DOWN", Layer::Tunnel));
                                return Ok(());
                            }
                            Err("INSTALLATION_CHANGED") => return Err("INSTALLATION_CHANGED"),
                            Err(error) => {
                                host.failure = Some((error, Layer::Tunnel));
                                return Ok(());
                            }
                        }
                    }
                    result => {
                        host.route = None;
                        host.route_requirement = result
                            .ok()
                            .and_then(|r| r.err())
                            .and_then(|e| e.route_requirement);
                        host.failure = Some(("ROUTE_UNAVAILABLE", Layer::Tunnel));
                        host.next_probe = now.saturating_add(15000);
                    }
                },
            }
        }
        host.policy.start_wake(self.route_probe.clone());
        if host.policy.take_wake_completion() {
            // A fresh status/TCP observation, never the ping result, decides
            // whether the direct route is eligible after wake completion.
            host.next_probe = 0;
        }
        let bat_probes = self
            .hosts
            .values()
            .filter(|h| h.kind == ProbeKind::Bat && matches!(h.worker, Some(Work::Probe(_))))
            .count();
        let bat_routes = self
            .hosts
            .values()
            .filter(|h| h.kind == ProbeKind::Bat && matches!(h.worker, Some(Work::Route(_))))
            .count();
        if host.worker.is_none() && now >= host.next_probe && !host.policy.waking() {
            if host.owned.is_some() {
                if host.kind == ProbeKind::Bat && bat_probes >= 3 {
                    return Ok(());
                }
                let Some(credential) = credential else {
                    return Ok(());
                };
                let future = self.probes.start(
                    &self.configuration,
                    name,
                    &self.client_info,
                    credential,
                    host.generation.clone(),
                    now,
                )?;
                host.worker = Some(Work::Probe(tokio::spawn(future)));
            } else if host.policy.recovery_due(now) {
                if host.kind == ProbeKind::Bat && bat_routes >= 3 {
                    return Ok(());
                }
                let request = SelectionRequest::new(
                    &host.routes,
                    &host.policy,
                    host.generation.clone(),
                    now,
                )?;
                let probe = self.route_probe.clone();
                host.worker = Some(Work::Route(tokio::spawn(async move {
                    request.run(probe.as_ref(), Duration::from_secs(2)).await
                })));
            } else {
                host.failure = Some((
                    if host.policy.recovery_exhausted() {
                        "RECOVERY_EXHAUSTED"
                    } else {
                        "TUNNEL_DOWN"
                    },
                    Layer::Tunnel,
                ));
            }
        }
        Ok(())
    }
    fn publish(&mut self, now: u64, lifecycle: &str) -> Result<supervisor_status::Snapshot> {
        self.verify()?;
        let entries = self
            .hosts
            .iter()
            .map(|(name, h)| {
                Entry::observed(
                    (name, &h.label),
                    h.selected,
                    h.kind,
                    (
                        h.observation.as_ref(),
                        h.route.as_ref(),
                        h.route_requirement,
                    ),
                    &h.generation,
                    now,
                    if h.policy.waking() {
                        Some(("TAILSCALE_WAKING", Layer::Tunnel))
                    } else {
                        h.failure
                    },
                )
            })
            .collect::<Result<Vec<_>>>()?;
        let mut snapshot = supervisor_status::Snapshot::new(
            self.epoch(),
            self.configuration.binding(),
            &self.applied_revision,
            entries,
            now,
        )?;
        snapshot.lifecycle = lifecycle.into();
        let bytes = serde_json::to_vec(&snapshot).map_err(|_| "STATUS_TOO_LARGE")?;
        let config = &self.configuration;
        let effects = &self.effects;
        let selection = &self.selection;
        let store = &self.store;
        self.lease.publish_status(bytes, effects, || {
            config.verify_current()?;
            effects.prove_exclusive()?;
            if !selection.same_snapshot(&store.read(config)?) {
                return Err("SELECTION_CHANGED");
            }
            Ok(())
        })?;
        Ok(snapshot)
    }
    pub async fn shutdown(&mut self, now: u64) -> Result<supervisor_status::Snapshot> {
        self.quitting = true;
        self.invalidate_all().await;
        self.effects
            .verify_owner(&self.configuration, &self.lease)?;
        let names = self.hosts.keys().cloned().collect::<Vec<_>>();
        let mut unsettled = false;
        for name in names {
            let file = self.hosts[&name]
                .owned
                .as_ref()
                .map(|o| Self::owned_file(&name, o));
            if let Some(file) = file {
                match self.stop_file(&file, true) {
                    Ok(()) => {
                        let host = self.hosts.get_mut(&name).unwrap();
                        host.owned = None;
                        if host
                            .failure
                            .is_some_and(|(code, _)| code == "STOP_UNCONFIRMED")
                        {
                            host.failure = None;
                        }
                    }
                    Err(_) => {
                        unsettled = true;
                        self.hosts.get_mut(&name).unwrap().failure =
                            Some(("STOP_UNCONFIRMED", Layer::Tunnel));
                    }
                }
            }
        }
        for dir in files::directories(&self.options.roaming)? {
            let intent_dir = dir.join("fleet-tunnel-intents");
            match std::fs::read_dir(intent_dir) {
                Ok(entries) => {
                    for (index, entry) in entries.enumerate() {
                        if index >= 2000 {
                            return Err("TUNNEL_RECORD_UNAVAILABLE");
                        }
                        let path = entry.map_err(|_| "TUNNEL_RECORD_UNAVAILABLE")?.path();
                        if path.extension().and_then(|s| s.to_str()) != Some("json") {
                            continue;
                        }
                        let bytes = files::read(&path)?.ok_or("TUNNEL_START_UNSETTLED")?;
                        let doc = crate::strict_json::parse(&bytes, files::BOUND)?;
                        if doc["monitor_instance"] == self.epoch() {
                            unsettled = true;
                        }
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                Err(_) => return Err("TUNNEL_RECORD_UNAVAILABLE"),
            }
        }
        let entries = self
            .hosts
            .iter()
            .map(|(name, h)| {
                Entry::observed(
                    (name, &h.label),
                    h.selected,
                    h.kind,
                    (None, None, None),
                    &h.generation,
                    now,
                    if h.policy.waking() {
                        Some(("TAILSCALE_WAKING", Layer::Tunnel))
                    } else {
                        h.failure
                    },
                )
            })
            .collect::<Result<Vec<_>>>()?;
        let mut snapshot = supervisor_status::Snapshot::new(
            self.epoch(),
            self.configuration.binding(),
            &self.applied_revision,
            entries,
            now,
        )?;
        snapshot.lifecycle = if unsettled {
            "stop_unconfirmed"
        } else {
            "stopped"
        }
        .into();
        // Even a configuration/selection edit must not prevent a positively owned
        // shutdown. A failed status write cannot erase unsettled ownership evidence.
        if let Ok(bytes) = serde_json::to_vec(&snapshot) {
            let _ = self
                .lease
                .publish_status(bytes, &self.effects, || self.effects.prove_exclusive());
        }
        if !unsettled {
            self.lease.verify(&self.effects)?;
            self.lease.finish()?;
            self.stopped = true;
        }
        Ok(snapshot)
    }
}
impl<E: Effects, F: ProbeFactory, R: RouteProbe> Drop for Supervisor<E, F, R> {
    fn drop(&mut self) {
        // No process effects or optimistic receipt deletion from Drop.
        for host in self.hosts.values() {
            match &host.worker {
                Some(Work::Route(h)) => h.abort(),
                Some(Work::Probe(h)) => h.abort(),
                None => (),
            }
        }
    }
}
