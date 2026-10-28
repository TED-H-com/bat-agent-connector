mod support;
use bat_fleet_core::{
    configuration::Configuration,
    discovery::{Backend, MonitorRecord, Observation},
    ownership::ProcessState,
    probe::{Authentication, ProbeCredential, ProbeKind, ProbeObservation},
    process_adapter::{
        legacy_created, HeldProcess, LoginIdentity, ProcessSnapshot, StopMode, StopOutcome,
        TunnelRecord,
    },
    route::{ProbeFuture, RouteProbe},
    supervisor::{Effects, Options, Supervisor},
    supervisor_io::MonitorLease,
    supervisor_probe::{ProbeFactory, ProbeFuture as ReadFuture},
    supervisor_status::Snapshot as Status,
    tunnel::{self, Launched, Plan},
    Result,
};
use serde_json::json;
use std::{
    collections::{HashMap, HashSet},
    path::Path,
    sync::{Arc, Mutex},
    time::Duration,
};
use support::Fixture;
use tokio::time::Instant;
const SELF: u32 = 20;
#[derive(Default)]
struct State {
    processes: HashMap<u32, ProcessSnapshot>,
    launches: Vec<String>,
    stops: Vec<u32>,
    next_pid: u32,
    unknown_stop: bool,
    listener_down: HashSet<String>,
    iteration_error: bool,
    unknown_processes: HashSet<u32>,
    unknown_launch: HashSet<String>,
    installation_changed_on_launch: bool,
    hold: HashSet<String>,
    tokens: HashMap<String, String>,
    missing_credentials: HashSet<String>,
    starts: Vec<String>,
    active: usize,
    peak: usize,
    cancelled: usize,
    requirement_events: Option<tokio::sync::mpsc::UnboundedSender<RequirementEvent>>,
}
type Shared = Arc<Mutex<State>>;
fn login() -> LoginIdentity {
    LoginIdentity {
        owner_sid: "S-1-5-21-123-456-789-1001".into(),
        session_id: 1,
    }
}
fn me() -> ProcessSnapshot {
    ProcessSnapshot {
        pid: SELF,
        created_filetime: 100000,
        executable: "C:\\Fixture\\dashboard.exe".into(),
        arguments: vec![
            "--fleet-supervisor".into(),
            "--fleet-config".into(),
            "C:\\Fixture\\fleet.json".into(),
        ],
        login: login(),
    }
}
struct FakeEffects(Shared);
impl Observation for FakeEffects {
    fn current_login(&self) -> Result<LoginIdentity> {
        Ok(login())
    }
    fn state(&self, pid: u32) -> ProcessState {
        let state = self.0.lock().unwrap();
        if state.unknown_processes.contains(&pid) {
            return ProcessState::Unknown;
        }
        state
            .processes
            .get(&pid)
            .map(|p| ProcessState::Live {
                created: legacy_created(p.created_filetime).unwrap(),
            })
            .unwrap_or(ProcessState::Dead)
    }
    fn observe(&self, pid: u32) -> Result<Option<ProcessSnapshot>> {
        Ok(self.0.lock().unwrap().processes.get(&pid).cloned())
    }
    fn legacy_candidates(&self) -> Result<Vec<ProcessSnapshot>> {
        Ok(vec![])
    }
}
struct FakeChild {
    shared: Shared,
    pid: u32,
}
impl HeldProcess for FakeChild {
    fn snapshot(&self) -> Result<ProcessSnapshot> {
        self.shared
            .lock()
            .unwrap()
            .processes
            .get(&self.pid)
            .cloned()
            .ok_or("OWNER_UNPROVEN")
    }
    fn terminate_and_wait(&mut self) -> Result<()> {
        self.shared.lock().unwrap().processes.remove(&self.pid);
        Ok(())
    }
}
impl Effects for FakeEffects {
    type Child = FakeChild;
    fn prove_exclusive(&self) -> Result<()> {
        Ok(())
    }
    fn prove_absence(&self, _: &Configuration) -> Result<()> {
        Ok(())
    }
    fn record(&self, _: &Configuration, epoch: &str) -> Result<MonitorRecord> {
        let p = self.observe(SELF)?.ok_or("OWNER_UNPROVEN")?;
        Ok(MonitorRecord {
            pid: p.pid,
            created: legacy_created(p.created_filetime)?,
            executable: p.executable,
            owner_sid: p.login.owner_sid,
            session_id: p.login.session_id,
            instance: epoch.into(),
            backend: Backend::Rust,
            script: None,
            arguments: Some(p.arguments),
            inventory_path: Some("C:\\Fixture\\client\\fleet-inventory.json".into()),
            profile_index_path: Some("C:\\Fixture\\client\\bat-profiles\\index.json".into()),
            created_filetime: Some(p.created_filetime.to_string()),
        })
    }
    fn verify_owner(&self, _: &Configuration, lease: &MonitorLease) -> Result<()> {
        if self.0.lock().unwrap().iteration_error {
            return Err("OWNER_UNPROVEN");
        }
        lease.verify(self)
    }
    fn listening(&self, _: &Configuration, name: &str) -> Result<bool> {
        Ok(!self.0.lock().unwrap().listener_down.contains(name))
    }
    fn launch(
        &mut self,
        context: &tunnel::Context<'_>,
        plan: &Plan,
    ) -> Result<Launched<Self::Child>> {
        let mut state = self.0.lock().unwrap();
        if state.installation_changed_on_launch {
            return Err("INSTALLATION_CHANGED");
        }
        state.launches.push(plan.name().into());
        if state.unknown_launch.contains(plan.name()) {
            let dir = context.selection.directory().join("fleet-tunnel-intents");
            std::fs::create_dir_all(&dir).unwrap();
            let epoch = plan
                .arguments()
                .iter()
                .find_map(|a| a.strip_prefix("SetEnv=BAT_FLEET_MONITOR="))
                .unwrap();
            std::fs::write(
                dir.join(format!("{}.json", plan.name())),
                serde_json::to_vec(&json!({"monitor_instance":epoch})).unwrap(),
            )
            .unwrap();
            return Err("TUNNEL_START_UNSETTLED");
        }
        state.next_pid += 1;
        let pid = 100 + state.next_pid;
        let process = ProcessSnapshot {
            pid,
            created_filetime: u64::from(pid) * 10000,
            executable: "C:\\Windows\\System32\\OpenSSH\\ssh.exe".into(),
            arguments: plan.arguments().to_vec(),
            login: login(),
        };
        let record = TunnelRecord::from_snapshot(&process, &state.processes[&SELF])?;
        let record_bytes = serde_json::to_vec(&record.document()?).unwrap();
        let dir = context.selection.directory().join("fleet-tunnel-owners");
        std::fs::create_dir_all(&dir).unwrap();
        let record_path = dir.join(format!("{}.json", plan.name()));
        assert!(!record_path.exists());
        std::fs::write(&record_path, &record_bytes).unwrap();
        state.processes.insert(pid, process);
        Ok(Launched {
            child: FakeChild {
                shared: self.0.clone(),
                pid,
            },
            record,
            record_path,
            record_bytes,
        })
    }
    fn stop(&mut self, record: &TunnelRecord, _: StopMode<'_>) -> Result<StopOutcome> {
        let mut state = self.0.lock().unwrap();
        state.stops.push(record.process.pid);
        if state.unknown_stop {
            return Err("STOP_UNCONFIRMED");
        }
        state.processes.remove(&record.process.pid);
        Ok(StopOutcome::Stopped)
    }
}
struct FakeRoutes;
impl RouteProbe for FakeRoutes {
    fn tailscale_direct<'a>(&'a self, _: &'a str, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async { false })
    }
    fn tcp<'a>(
        &'a self,
        _: &'a bat_fleet_core::inventory::Endpoint,
        _: Instant,
    ) -> ProbeFuture<'a> {
        Box::pin(async { false })
    }
}
struct FakeProbes(Shared);
struct Worker(Shared);
impl Drop for Worker {
    fn drop(&mut self) {
        let mut s = self.0.lock().unwrap();
        s.active -= 1;
        s.cancelled += 1;
    }
}
impl ProbeFactory for FakeProbes {
    fn credential(&self, _: &Configuration, _: &Path, name: &str) -> Result<ProbeCredential> {
        if self.0.lock().unwrap().missing_credentials.contains(name) {
            return Err("CREDENTIAL_MISSING");
        }
        Ok(ProbeCredential::new(
            self.0
                .lock()
                .unwrap()
                .tokens
                .get(name)
                .cloned()
                .unwrap_or_else(|| "synthetic-token".into()),
        ))
    }
    fn start(
        &self,
        _: &Configuration,
        name: &str,
        _: &serde_json::Value,
        _: ProbeCredential,
        generation: bat_fleet_core::ownership::ProbeGeneration,
        now: u64,
    ) -> Result<ReadFuture> {
        let shared = self.0.clone();
        let name = name.to_owned();
        Ok(Box::pin(async move {
            {
                let mut s = shared.lock().unwrap();
                s.starts.push(name.clone());
                s.active += 1;
                s.peak = s.peak.max(s.active);
            }
            let _guard = Worker(shared.clone());
            while shared.lock().unwrap().hold.contains(&name) {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
            if let Some(events) = &shared.lock().unwrap().requirement_events {
                events
                    .send(RequirementEvent::ProbeReady(name.clone()))
                    .unwrap();
            }
            ProbeObservation {
                generation,
                observed_ms: now,
                kind: if name == "connector" {
                    ProbeKind::Connector
                } else {
                    ProbeKind::Bat
                },
                tunnel: true,
                tls: true,
                auth: Authentication::Ok,
                bat: true,
                workspace: true,
                connector: true,
                version: Some("3.2.5".into()),
                code: None,
                blocking: None,
                reason: None,
            }
        }))
    }
}
type Runtime = Supervisor<FakeEffects, FakeProbes, FakeRoutes>;
fn fixture() -> (Fixture, Shared, Runtime) {
    fixture_with_routes(Arc::new(FakeRoutes))
}
fn fixture_with_routes<R: RouteProbe + Send + 'static>(
    routes: Arc<R>,
) -> (Fixture, Shared, Supervisor<FakeEffects, FakeProbes, R>) {
    fixture_with_roaming(routes, Path::to_path_buf)
}
fn fixture_with_roaming<R: RouteProbe + Send + 'static>(
    routes: Arc<R>,
    roaming: impl FnOnce(&Path) -> std::path::PathBuf,
) -> (Fixture, Shared, Supervisor<FakeEffects, FakeProbes, R>) {
    let fixture = Fixture::new();
    let path = fixture.0.join("kit/fleet-inventory.json");
    let mut doc: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    doc["connector"]["ssh_alias"] = "fixture-connector".into();
    std::fs::write(path, serde_json::to_vec(&doc).unwrap()).unwrap();
    let ssh = fixture.0.join("kit/ssh-config");
    let mut text = std::fs::read_to_string(&ssh).unwrap();
    text.push_str("\nHost fixture-connector\n HostName example.invalid\n");
    std::fs::write(ssh, text).unwrap();
    let dir = fixture.0.join("BetterAgentTerminal");
    std::fs::create_dir(&dir).unwrap();
    std::fs::write(dir.join("fleet-client.json"),br#"{"version":1,"connections":["node-1","node-2","node-3","node-4","connector"],"profiles":[],"dashboard":false}"#).unwrap();
    let shared = Arc::new(Mutex::new(State::default()));
    shared.lock().unwrap().processes.insert(SELF, me());
    let runtime = Supervisor::start(
        Options {
            configuration: fixture.paths(),
            roaming: roaming(&fixture.0),
            quit_file: fixture.0.join("quit.json"),
        },
        FakeEffects(shared.clone()),
        FakeProbes(shared.clone()),
        routes,
    )
    .unwrap();
    (fixture, shared, runtime)
}

#[derive(Default)]
struct RouteCounts {
    active: usize,
    peak: usize,
}
struct PendingRoutes(Arc<Mutex<RouteCounts>>);
struct PendingRoute(Arc<Mutex<RouteCounts>>);
impl Drop for PendingRoute {
    fn drop(&mut self) {
        self.0.lock().unwrap().active -= 1;
    }
}
impl RouteProbe for PendingRoutes {
    fn tailscale_direct<'a>(&'a self, _: &'a str, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async {
            {
                let mut counts = self.0.lock().unwrap();
                counts.active += 1;
                counts.peak = counts.peak.max(counts.active);
            }
            let _held = PendingRoute(self.0.clone());
            std::future::pending::<bool>().await
        })
    }
    fn tcp<'a>(
        &'a self,
        _: &'a bat_fleet_core::inventory::Endpoint,
        _: Instant,
    ) -> ProbeFuture<'a> {
        Box::pin(async { false })
    }
}
#[tokio::test(start_paused = true)]
async fn route_slots_are_bounded_separately_and_connector_remains_independent() {
    let counts = Arc::new(Mutex::new(RouteCounts::default()));
    let (_f, shared, mut runtime) = fixture_with_routes(Arc::new(PendingRoutes(counts.clone())));
    let mut status = runtime.tick(1000).await.unwrap();
    for i in 1..8 {
        tokio::time::sleep(Duration::from_millis(2)).await;
        if i == 1 {
            // Scheduling capacity is independent of wall time spent in local IO.
            // This exceeds the real selector deadline and reproduced active=0
            // before the fixture clock was paused. Production bounds stay intact.
            let before_io = Instant::now();
            std::thread::sleep(Duration::from_millis(2_100));
            assert_eq!(Instant::now(), before_io);
        }
        status = runtime.tick(1000 + i).await.unwrap();
    }
    let (active, peak) = {
        let counts = counts.lock().unwrap();
        (counts.active, counts.peak)
    };
    assert_eq!(active, 3);
    assert_eq!(peak, 3);
    assert_eq!(shared.lock().unwrap().launches, ["connector"]);
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "connector")
            .unwrap()
            .level,
        "ready"
    );
    runtime.shutdown(2000).await.unwrap();
    assert_eq!(counts.lock().unwrap().active, 0);
}
async fn advance(runtime: &mut Runtime, now: u64) -> Status {
    let mut status = runtime.tick(now).await.unwrap();
    for i in 1..8 {
        tokio::time::sleep(Duration::from_millis(2)).await;
        status = runtime.tick(now + i).await.unwrap();
    }
    status
}
#[tokio::test]
async fn exact_publication_independent_readiness_and_clean_quit() {
    let (f, s, mut r) = fixture();
    let snapshot = advance(&mut r, 1000).await;
    assert_eq!(
        snapshot
            .entries
            .iter()
            .filter(|e| e.level == "ready")
            .count(),
        5
    );
    let bytes = std::fs::read(f.0.join("BetterAgentTerminal/fleet-desktop-status.json")).unwrap();
    Status::parse(&bytes, &f.load(), r.epoch(), 1100).unwrap();
    for secret in [
        "synthetic-token",
        "ssh.exe",
        "SetEnv",
        "owner_sid",
        "C:\\Fixture",
    ] {
        assert!(!String::from_utf8_lossy(&bytes).contains(secret));
    }
    assert!(s.lock().unwrap().peak <= 4);
    assert_eq!(s.lock().unwrap().launches.len(), 5);
    std::fs::write(
        f.0.join("quit.json"),
        serde_json::to_vec(&json!({"pid":SELF,"instance":r.epoch()})).unwrap(),
    )
    .unwrap();
    assert_eq!(r.tick(1200).await.unwrap().lifecycle, "stopped");
    assert!(r.stopped());
    assert!(!f.0.join("BetterAgentTerminal/fleet-monitor.json").exists());
    assert!(!f.0.join("quit.json").exists());
    assert_eq!(s.lock().unwrap().stops.len(), 5);
}
#[tokio::test]
async fn equivalent_roaming_paths_preserve_owned_children_and_restart_receipts() {
    // Production selection expands lexical paths (and Windows 8.3 aliases).
    // Ownership scans must identify those same paths instead of treating our
    // own children as orphans or missing the existing monitor on restart.
    let (f, shared, mut runtime) = fixture_with_roaming(Arc::new(FakeRoutes), |path| {
        std::fs::create_dir(path.join("roaming-entry")).unwrap();
        path.join("roaming-entry/..")
    });
    let first = advance(&mut runtime, 1000).await;
    assert_eq!(
        first.entries.iter().filter(|e| e.level == "ready").count(),
        5
    );
    assert!(shared.lock().unwrap().stops.is_empty());
    let mut runtime = restart(&f, &shared, runtime);
    let recovered = advance(&mut runtime, 2000).await;
    assert_eq!(
        recovered
            .entries
            .iter()
            .filter(|e| e.level == "ready")
            .count(),
        5
    );
    assert_eq!(shared.lock().unwrap().stops.len(), 0);
    assert_eq!(shared.lock().unwrap().launches.len(), 5);
    // Adopted receipts retain their original epoch and still supervise process death.
    let receipt_path =
        f.0.join("BetterAgentTerminal/fleet-tunnel-owners/node-1.json");
    let receipt = std::fs::read(&receipt_path).unwrap();
    let record = TunnelRecord::parse(&receipt, None).unwrap();
    assert_ne!(record.process.monitor_instance, runtime.epoch());
    shared.lock().unwrap().processes.remove(&record.process.pid);
    advance(&mut runtime, 2100).await;
    advance(&mut runtime, 7000).await;
    assert_eq!(shared.lock().unwrap().launches.len(), 5);
    advance(&mut runtime, 7200).await;
    assert_eq!(shared.lock().unwrap().launches.len(), 6);
    assert_eq!(runtime.shutdown(8000).await.unwrap().lifecycle, "stopped");
    assert_eq!(shared.lock().unwrap().stops.len(), 5);
    assert!(!f.0.join("BetterAgentTerminal/fleet-monitor.json").exists());
}
#[tokio::test]
async fn slow_bat_workers_do_not_block_connector_or_exceed_three_bat_slots() {
    let (_f, s, mut r) = fixture();
    s.lock()
        .unwrap()
        .hold
        .extend(["node-1", "node-2", "node-3", "node-4"].map(str::to_owned));
    let status = advance(&mut r, 1000).await;
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "connector")
            .unwrap()
            .level,
        "ready"
    );
    assert!(s.lock().unwrap().active <= 3);
    assert!(s.lock().unwrap().peak <= 4);
    let launched_bat = s
        .lock()
        .unwrap()
        .launches
        .iter()
        .filter(|name| name.starts_with("node-"))
        .count();
    assert_eq!(launched_bat, 4, "pending probes cannot occupy route slots");
    assert!(status
        .entries
        .iter()
        .filter(|e| e.name != "connector")
        .all(|e| e.level != "ready"));
    r.shutdown(2000).await.unwrap();
    assert_eq!(s.lock().unwrap().active, 0);
}
#[tokio::test]
async fn credential_change_cancels_old_observation_even_with_equal_config_and_selection() {
    let (_f, s, mut r) = fixture();
    s.lock().unwrap().hold.insert("node-1".into());
    advance(&mut r, 1000).await;
    let prior = s.lock().unwrap().cancelled;
    s.lock()
        .unwrap()
        .tokens
        .insert("node-1".into(), "replacement".into());
    let status = advance(&mut r, 1100).await;
    assert!(s.lock().unwrap().cancelled > prior);
    assert_ne!(
        status
            .entries
            .iter()
            .find(|e| e.name == "node-1")
            .unwrap()
            .level,
        "ready"
    );
    s.lock().unwrap().hold.remove("node-1");
    assert_eq!(
        advance(&mut r, 1200)
            .await
            .entries
            .iter()
            .find(|e| e.name == "node-1")
            .unwrap()
            .level,
        "ready"
    );
}
#[tokio::test]
async fn unknown_launch_fences_only_its_host_and_shutdown_retains_monitor_evidence() {
    let (f, s, mut r) = fixture();
    s.lock().unwrap().unknown_launch.insert("node-1".into());
    let status = advance(&mut r, 1000).await;
    assert_eq!(
        s.lock()
            .unwrap()
            .launches
            .iter()
            .filter(|n| *n == "node-1")
            .count(),
        1
    );
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "connector")
            .unwrap()
            .level,
        "ready"
    );
    assert_ne!(
        status
            .entries
            .iter()
            .find(|e| e.name == "node-1")
            .unwrap()
            .level,
        "ready"
    );
    assert_eq!(
        r.shutdown(1200).await.unwrap().lifecycle,
        "stop_unconfirmed"
    );
    assert!(!r.stopped());
    assert!(f.0.join("BetterAgentTerminal/fleet-monitor.json").exists());
    assert!(f
        .0
        .join("BetterAgentTerminal/fleet-tunnel-intents/node-1.json")
        .exists());
}
#[tokio::test]
async fn uncertain_stop_is_not_resent_and_is_cleaned_only_after_positive_death() {
    let (f, s, mut r) = fixture();
    advance(&mut r, 1000).await;
    s.lock().unwrap().unknown_stop = true;
    assert_eq!(
        r.shutdown(1200).await.unwrap().lifecycle,
        "stop_unconfirmed"
    );
    let attempts = s.lock().unwrap().stops.len();
    assert_eq!(
        r.shutdown(1300).await.unwrap().lifecycle,
        "stop_unconfirmed"
    );
    assert_eq!(s.lock().unwrap().stops.len(), attempts);
    assert!(f.0.join("BetterAgentTerminal/fleet-monitor.json").exists());
    s.lock().unwrap().processes.retain(|pid, _| *pid == SELF);
    assert_eq!(r.shutdown(1400).await.unwrap().lifecycle, "stopped");
    assert_eq!(s.lock().unwrap().stops.len(), attempts);
}

async fn unresolved_stop_fixture(deselect: bool) -> serde_json::Value {
    let (f, s, mut r) = fixture();
    advance(&mut r, 1000).await;
    let dir = f.0.join("BetterAgentTerminal");
    let receipt_path = dir.join("fleet-tunnel-owners/node-1.json");
    let receipt = std::fs::read(&receipt_path).unwrap();
    let monitor = std::fs::read(dir.join("fleet-monitor.json")).unwrap();
    let launches = s.lock().unwrap().launches.clone();
    s.lock().unwrap().unknown_stop = true;
    if deselect {
        let path = dir.join("fleet-client.json");
        let mut selection: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        selection["connections"] = json!(["connector"]);
        std::fs::write(path, serde_json::to_vec(&selection).unwrap()).unwrap();
        assert_eq!(r.tick(1200).await.unwrap().lifecycle, "selection_pending");
    } else {
        assert_eq!(
            r.shutdown(1200).await.unwrap().lifecycle,
            "stop_unconfirmed"
        );
    }
    let stops = s.lock().unwrap().stops.clone();
    assert!(!stops.is_empty());
    let stop_receipt_path = dir.join("fleet-tunnel-stop-intents/node-1.json");
    let stop_receipt = std::fs::read(&stop_receipt_path).unwrap();
    let bytes = std::fs::read(dir.join("fleet-desktop-status.json")).unwrap();
    let now = if deselect { 1300 } else { 62001 };
    let parsed = Status::parse(&bytes, &f.load(), r.epoch(), now).unwrap();
    let disk_reread = Status::parse(
        &std::fs::read(dir.join("fleet-desktop-status.json")).unwrap(),
        &f.load(),
        r.epoch(),
        now,
    )
    .unwrap();
    assert_eq!(
        serde_json::to_value(&parsed).unwrap(),
        serde_json::to_value(&disk_reread).unwrap()
    );
    let reread = Status::parse(
        &serde_json::to_vec(&parsed).unwrap(),
        &f.load(),
        r.epoch(),
        now,
    )
    .unwrap();
    assert_eq!(
        serde_json::to_value(&parsed).unwrap(),
        serde_json::to_value(&reread).unwrap()
    );
    if deselect {
        r.tick(1300).await.unwrap();
    } else {
        r.shutdown(now + 1).await.unwrap();
    }
    assert_eq!(s.lock().unwrap().launches, launches);
    assert_eq!(s.lock().unwrap().stops, stops);
    assert_eq!(std::fs::read(receipt_path).unwrap(), receipt);
    assert_eq!(std::fs::read(stop_receipt_path).unwrap(), stop_receipt);
    assert_eq!(
        std::fs::read(dir.join("fleet-monitor.json")).unwrap(),
        monitor
    );
    let selection_bytes = std::fs::read(dir.join("fleet-client.json")).unwrap();
    assert_eq!(
        parsed.applied_selection_revision != bat_fleet_core::digest(&selection_bytes),
        deselect
    );
    let selection: serde_json::Value = serde_json::from_slice(&selection_bytes).unwrap();
    let mut snapshot = serde_json::to_value(parsed).unwrap();
    // Only nondeterministic/native binding metadata is de-identified. Rows are
    // the actual file producer -> parse -> re-read result, never invented states.
    snapshot["monitor_epoch"] = json!("a".repeat(32));
    snapshot["configuration_binding"] = json!("c".repeat(64));
    snapshot["applied_selection_revision"] = json!("d".repeat(64));
    json!({"now":now, "snapshot":snapshot, "connections":selection["connections"], "selection_revision":
        if deselect { "e".repeat(64) } else { "d".repeat(64) }})
}
#[tokio::test]
async fn unresolved_stop_survives_age() {
    assert_unresolved_stop(false).await;
}
#[tokio::test]
async fn unresolved_stop_survives_deselection() {
    assert_unresolved_stop(true).await;
}
async fn assert_unresolved_stop(deselect: bool) {
    {
        let case = unresolved_stop_fixture(deselect).await;
        let row = case["snapshot"]["entries"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["name"] == "node-1")
            .unwrap();
        assert_eq!(row["code"], "STOP_UNCONFIRMED", "deselect={deselect}");
        assert_eq!(row["level"], "down");
        assert_eq!(row["selected"], !deselect);
        assert_eq!(row["layers"]["tunnel"], false);
        if !deselect {
            assert_eq!(row["stale"], true);
        }
    }
}
#[tokio::test]
async fn unresolved_stop_rows_match_native_fixture() {
    let expected: serde_json::Value =
        serde_json::from_str(include_str!("../../tests/fixtures/fleet-stop.json")).unwrap();
    assert_eq!(
        json!({"aged":unresolved_stop_fixture(false).await, "deselected":unresolved_stop_fixture(true).await}),
        expected
    );
}
#[tokio::test]
async fn deselected_stop_evidence_clears_only_after_positive_death() {
    let (f, s, mut r) = fixture();
    advance(&mut r, 1000).await;
    s.lock().unwrap().unknown_stop = true;
    let dir = f.0.join("BetterAgentTerminal");
    let receipt = dir.join("fleet-tunnel-owners/node-1.json");
    let owned = TunnelRecord::parse(&std::fs::read(&receipt).unwrap(), None).unwrap();
    let selection = dir.join("fleet-client.json");
    let mut doc: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&selection).unwrap()).unwrap();
    doc["connections"] = json!(["connector"]);
    std::fs::write(selection, serde_json::to_vec(&doc).unwrap()).unwrap();
    let pending = r.tick(1200).await.unwrap();
    assert_eq!(
        pending
            .entries
            .iter()
            .find(|e| e.name == "node-1")
            .unwrap()
            .code
            .as_deref(),
        Some("STOP_UNCONFIRMED")
    );
    let stops = s.lock().unwrap().stops.clone();
    let launches = s.lock().unwrap().launches.clone();
    s.lock().unwrap().processes.remove(&owned.process.pid);
    let reconciled = r.tick(1300).await.unwrap();
    let row = reconciled
        .entries
        .iter()
        .find(|e| e.name == "node-1")
        .unwrap();
    assert_eq!(row.level, "off");
    assert_eq!(row.code, None);
    assert!(!receipt.exists());
    assert!(!dir.join("fleet-tunnel-stop-intents/node-1.json").exists());
    assert_eq!(s.lock().unwrap().stops, stops);
    assert_eq!(s.lock().unwrap().launches, launches);
}
#[tokio::test]
#[ignore = "explicit fixture regeneration only"]
async fn generate_unresolved_stop_fixture() {
    println!(
        "FLEET_STOP={}",
        json!({
            "aged": unresolved_stop_fixture(false).await,
            "deselected": unresolved_stop_fixture(true).await,
        })
    );
}
#[tokio::test]
async fn unrelated_quit_and_replaced_monitor_record_are_preserved() {
    let (f, s, mut r) = fixture();
    let unrelated = br#"{"pid":999,"instance":"ffffffffffffffffffffffffffffffff"}"#;
    std::fs::write(f.0.join("quit.json"), unrelated).unwrap();
    advance(&mut r, 1000).await;
    assert_eq!(std::fs::read(f.0.join("quit.json")).unwrap(), unrelated);
    assert!(s.lock().unwrap().stops.is_empty());
    std::fs::write(
        f.0.join("BetterAgentTerminal/fleet-monitor.json"),
        b"foreign edit",
    )
    .unwrap();
    assert!(r.tick(1200).await.is_err());
    assert_eq!(
        std::fs::read(f.0.join("BetterAgentTerminal/fleet-monitor.json")).unwrap(),
        b"foreign edit"
    );
}
#[tokio::test]
async fn selection_change_cancels_pending_and_retains_unapplied_revision_until_owned_stop() {
    let (f, s, mut r) = fixture();
    advance(&mut r, 1000).await;
    let old = std::fs::read(f.0.join("BetterAgentTerminal/fleet-client.json")).unwrap();
    s.lock().unwrap().unknown_stop = true;
    let mut doc: serde_json::Value = serde_json::from_slice(&old).unwrap();
    doc["connections"] = json!(["connector"]);
    let changed = serde_json::to_vec(&doc).unwrap();
    std::fs::write(f.0.join("BetterAgentTerminal/fleet-client.json"), &changed).unwrap();
    let status = r.tick(1200).await.unwrap();
    assert_eq!(status.lifecycle, "selection_pending");
    assert_eq!(
        status.applied_selection_revision,
        bat_fleet_core::digest(&old)
    );
    assert_ne!(
        status.applied_selection_revision,
        bat_fleet_core::digest(&changed)
    );
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "connector")
            .unwrap()
            .level,
        "degraded"
    );
}
#[tokio::test]
async fn config_edit_never_relabels_old_ready_observation_and_stop_does_not_retarget() {
    let (f, s, mut r) = fixture();
    let old = advance(&mut r, 1000).await;
    let launches = s.lock().unwrap().launches.len();
    let path = f.0.join("kit/ssh-config");
    let mut bytes = std::fs::read(&path).unwrap();
    bytes.extend_from_slice(b"\n# same endpoints, new generation\n");
    std::fs::write(path, bytes).unwrap();
    s.lock().unwrap().unknown_stop = true;
    assert!(matches!(r.tick(1200).await, Err("STOP_UNCONFIRMED")));
    let status = std::fs::read(f.0.join("BetterAgentTerminal/fleet-desktop-status.json")).unwrap();
    assert!(Status::parse(&status, &f.load(), r.epoch(), 1200).is_err());
    assert_eq!(s.lock().unwrap().launches.len(), launches);
    assert_ne!(old.configuration_binding, f.load().binding());
    s.lock().unwrap().processes.retain(|pid, _| *pid == SELF);
    s.lock().unwrap().unknown_stop = false;
    let new = advance(&mut r, 1300).await;
    assert_eq!(new.configuration_binding, f.load().binding());
    assert_eq!(new.entries.iter().filter(|e| e.level == "ready").count(), 5);
}
#[tokio::test]
async fn status_age_and_clock_rollback_cannot_refresh_cached_readiness() {
    let (f, _s, mut r) = fixture();
    let status = advance(&mut r, 1000).await;
    let bytes = serde_json::to_vec(&status).unwrap();
    let stale = Status::parse(&bytes, &f.load(), r.epoch(), 62000).unwrap();
    assert!(stale
        .entries
        .iter()
        .filter(|e| e.selected)
        .all(|e| e.level == "degraded" && e.stale));
    assert!(Status::parse(&bytes, &f.load(), r.epoch(), 999).is_err());
}

#[tokio::test]
async fn orphan_recovery_reads_both_directories_and_malformed_host_does_not_block_connector() {
    let (f, s, mut r) = fixture();
    let old_dir =
        f.0.join("org.tonyq.better-agent-terminal/fleet-tunnel-owners");
    std::fs::create_dir_all(&old_dir).unwrap();
    let mut parent = me();
    parent.pid = 900;
    let child = ProcessSnapshot {
        pid: 901,
        created_filetime: 9000000,
        executable: "C:\\Windows\\System32\\OpenSSH\\ssh.exe".into(),
        arguments: vec![
            "-N".into(),
            "-o".into(),
            "SetEnv=BAT_FLEET_MONITOR=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
        ],
        login: login(),
    };
    let record = TunnelRecord::from_snapshot(&child, &parent).unwrap();
    std::fs::write(
        old_dir.join("node-2.json"),
        serde_json::to_vec(&record.document().unwrap()).unwrap(),
    )
    .unwrap();
    std::fs::write(old_dir.join("node-1.json"), b"malformed original evidence").unwrap();
    s.lock().unwrap().processes.insert(901, child);
    let status = advance(&mut r, 1000).await;
    assert!(s.lock().unwrap().stops.contains(&901));
    assert!(!old_dir.join("node-2.json").exists());
    assert_eq!(
        std::fs::read(old_dir.join("node-1.json")).unwrap(),
        b"malformed original evidence"
    );
    assert_ne!(
        status
            .entries
            .iter()
            .find(|e| e.name == "node-1")
            .unwrap()
            .level,
        "ready"
    );
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "connector")
            .unwrap()
            .level,
        "ready"
    );
}
#[tokio::test]
async fn prior_uncertain_stop_survives_supervisor_restart_without_another_termination() {
    let (f, s, mut r) = fixture();
    advance(&mut r, 1000).await;
    s.lock().unwrap().unknown_stop = true;
    r.shutdown(1200).await.unwrap();
    let attempts = s.lock().unwrap().stops.len();
    drop(r);
    s.lock()
        .unwrap()
        .processes
        .get_mut(&SELF)
        .unwrap()
        .created_filetime += 100000;
    let mut resumed = Supervisor::start(
        Options {
            configuration: f.paths(),
            roaming: f.0.clone(),
            quit_file: f.0.join("quit.json"),
        },
        FakeEffects(s.clone()),
        FakeProbes(s.clone()),
        Arc::new(FakeRoutes),
    )
    .unwrap();
    let status = advance(&mut resumed, 2000).await;
    assert_eq!(s.lock().unwrap().stops.len(), attempts);
    assert!(status
        .entries
        .iter()
        .filter(|e| e.selected)
        .all(|e| e.level != "ready"));
    assert_eq!(s.lock().unwrap().launches.len(), 5);
    s.lock().unwrap().processes.retain(|pid, _| *pid == SELF);
    s.lock().unwrap().unknown_stop = false;
    let status = advance(&mut resumed, 3000).await;
    assert_eq!(
        status.entries.iter().filter(|e| e.level == "ready").count(),
        5
    );
    assert_eq!(s.lock().unwrap().stops.len(), attempts);
}
#[tokio::test]
async fn facade_status_and_quit_require_exact_current_owner_and_preserve_other_requests() {
    use bat_fleet_core::{
        discovery::{self, DiscoveryConfig, NativeIdentity, Ownership},
        supervisor_control,
    };
    let (f, s, mut r) = fixture();
    advance(&mut r, 1000).await;
    let config = f.load();
    let observation = FakeEffects(s);
    let discovery = DiscoveryConfig::new(
        "C:\\Fixture\\client",
        "C:\\Fixture\\client\\fleet-inventory.json",
        "C:\\Fixture\\client\\bat-profiles\\index.json",
        &f.0,
        Some(NativeIdentity::new("C:\\Fixture\\dashboard.exe", "C:\\Fixture\\fleet.json").unwrap()),
    )
    .unwrap();
    let owner = discovery::discover(&discovery, &observation)
        .unwrap()
        .unwrap();
    let status = supervisor_control::read_snapshot(&config, &discovery, &observation, 1100)
        .unwrap()
        .unwrap();
    assert_eq!(
        status.entries.iter().filter(|e| e.level == "ready").count(),
        5
    );
    let path = f.0.join("quit.json");
    let mut other = owner.clone();
    other.instance = Some("f".repeat(32));
    assert!(
        supervisor_control::request_quit(&config, &discovery, &observation, &path, &other).is_err()
    );
    assert!(!path.exists());
    other = owner.clone();
    other.ownership = Ownership::OtherLogin;
    assert!(
        supervisor_control::request_quit(&config, &discovery, &observation, &path, &other).is_err()
    );
    std::fs::write(&path, b"unrelated original request").unwrap();
    assert!(
        supervisor_control::request_quit(&config, &discovery, &observation, &path, &owner).is_err()
    );
    assert_eq!(std::fs::read(&path).unwrap(), b"unrelated original request");
    std::fs::remove_file(&path).unwrap();
    supervisor_control::request_quit(&config, &discovery, &observation, &path, &owner).unwrap();
    let original = std::fs::read(&path).unwrap();
    supervisor_control::request_quit(&config, &discovery, &observation, &path, &owner).unwrap();
    assert_eq!(std::fs::read(&path).unwrap(), original);
    assert_eq!(r.tick(1200).await.unwrap().lifecycle, "stopped");
}
#[test]
fn live_profile_binding_checks_selected_connection_and_retains_unrelated_application_metadata() {
    use bat_fleet_core::supervisor_probe::NativeProbes;
    let f = Fixture::new();
    let config = f.load();
    let directory = f.0.join("data");
    std::fs::create_dir_all(directory.join("profiles")).unwrap();
    let path = directory.join("profiles/index.json");
    let mut live = config.profile_index.document().clone();
    live["windowLayout"] = json!({"columns":2});
    live["profiles"]
        .as_array_mut()
        .unwrap()
        .push(json!({"id":"local-human","type":"local","workspaceLayout":{"custom":"retained"}}));
    std::fs::write(&path, serde_json::to_vec(&live).unwrap()).unwrap();
    let token = ProbeCredential::new("only-synthetic".into());
    let old = NativeProbes
        .binding(&config, &directory, "node-1", &token)
        .unwrap();
    live["windowLayout"]["columns"] = 3.into();
    std::fs::write(&path, serde_json::to_vec(&live).unwrap()).unwrap();
    assert_ne!(
        old,
        NativeProbes
            .binding(&config, &directory, "node-1", &token)
            .unwrap()
    );
    live["profiles"][0]["remotePort"] = 19999.into();
    std::fs::write(&path, serde_json::to_vec(&live).unwrap()).unwrap();
    assert!(matches!(
        NativeProbes.binding(&config, &directory, "node-1", &token),
        Err("PROFILE_DRIFT")
    ));
    // Connector readiness never depends on BAT's installed profile document.
    NativeProbes
        .binding(&config, &directory, "connector", &token)
        .unwrap();
    live["profiles"][0]["remotePort"] = 19001.into();
    let duplicate = live["profiles"][0].clone();
    live["profiles"].as_array_mut().unwrap().push(duplicate);
    std::fs::write(&path, serde_json::to_vec(&live).unwrap()).unwrap();
    assert!(NativeProbes
        .binding(&config, &directory, "node-1", &token)
        .is_err());
}

#[tokio::test]
async fn missing_probe_token_does_not_prevent_selected_connector_transport_or_borrow_a_token() {
    let (_f, s, mut r) = fixture();
    s.lock()
        .unwrap()
        .missing_credentials
        .insert("connector".into());
    let status = advance(&mut r, 1000).await;
    assert!(s
        .lock()
        .unwrap()
        .launches
        .iter()
        .any(|name| name == "connector"));
    assert!(!s
        .lock()
        .unwrap()
        .starts
        .iter()
        .any(|name| name == "connector"));
    let connector = status
        .entries
        .iter()
        .find(|e| e.name == "connector")
        .unwrap();
    assert_eq!(connector.code.as_deref(), Some("CREDENTIAL_MISSING"));
    assert_ne!(connector.level, "ready");
    s.lock().unwrap().missing_credentials.remove("connector");
    let status = advance(&mut r, 2000).await;
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "connector")
            .unwrap()
            .level,
        "ready"
    );
    assert_eq!(
        s.lock()
            .unwrap()
            .launches
            .iter()
            .filter(|n| *n == "connector")
            .count(),
        1
    );
}
#[tokio::test]
async fn private_legacy_selection_edit_invalidates_workers_with_identical_public_revision() {
    let (f, s, mut r) = fixture();
    let root = f.0.join("BetterAgentTerminal");
    std::fs::remove_file(root.join("fleet-client.json")).unwrap();
    std::fs::write(
        root.join("open-bat-selection.json"),
        br#"{"profile-1":true}"#,
    )
    .unwrap();
    s.lock().unwrap().hold.insert("node-1".into());
    let before = advance(&mut r, 1000).await;
    let completed = s.lock().unwrap().cancelled;
    std::fs::write(
        root.join("open-bat-selection.json"),
        br#"{"profile-1":false}"#,
    )
    .unwrap();
    let after = advance(&mut r, 2000).await;
    assert_eq!(
        before.applied_selection_revision,
        after.applied_selection_revision
    );
    assert_eq!(
        after.applied_selection_revision,
        bat_fleet_core::digest(b"")
    );
    assert!(s.lock().unwrap().cancelled > completed);
    assert_ne!(
        after
            .entries
            .iter()
            .find(|e| e.name == "node-1")
            .unwrap()
            .level,
        "ready"
    );
}
#[tokio::test]
async fn dead_and_listenerless_tunnels_retry_with_capped_backoff() {
    for dead in [true, false] {
        let (f, s, mut r) = fixture();
        advance(&mut r, 1000).await;
        let mut death = 20000;
        // More than three failures must keep recovering; a preference edit cannot reset timing.
        for (index, delay) in [5000, 10000, 20000, 40000, 60000, 60000]
            .into_iter()
            .enumerate()
        {
            let path =
                f.0.join("BetterAgentTerminal/fleet-tunnel-owners/node-1.json");
            let record = TunnelRecord::parse(&std::fs::read(path).unwrap(), None).unwrap();
            if dead {
                s.lock().unwrap().processes.remove(&record.process.pid);
            } else {
                s.lock().unwrap().listener_down.insert("node-1".into());
            }
            advance(&mut r, death).await;
            s.lock().unwrap().listener_down.clear();
            if index == 3 {
                let selection = f.0.join("BetterAgentTerminal/fleet-client.json");
                let mut doc: serde_json::Value =
                    serde_json::from_slice(&std::fs::read(&selection).unwrap()).unwrap();
                doc["connections"] = json!(["connector", "node-1", "node-2", "node-3", "node-4"]);
                std::fs::write(selection, serde_json::to_vec(&doc).unwrap()).unwrap();
                advance(&mut r, death + 100).await;
            }
            let launches = || {
                s.lock()
                    .unwrap()
                    .launches
                    .iter()
                    .filter(|n| *n == "node-1")
                    .count()
            };
            assert_eq!(launches(), index + 1);
            advance(&mut r, death + delay - 20).await;
            assert_eq!(launches(), index + 1);
            advance(&mut r, death + delay + 20).await;
            assert_eq!(launches(), index + 2);
            death += delay + 20000;
        }
        r.shutdown(death + 1000).await.unwrap();
    }
}
#[tokio::test]
async fn ownership_read_error_leaves_children_and_next_iteration_recovers() {
    let (_f, s, mut r) = fixture();
    advance(&mut r, 1000).await;
    s.lock().unwrap().iteration_error = true;
    let result = r.tick(2000).await.map(|_| ());
    assert_eq!(result, Err("OWNER_UNPROVEN"));
    assert_eq!(bat_fleet_core::monitor_log::iteration(result), Ok(()));
    assert!(!r.stopped());
    s.lock().unwrap().iteration_error = false;
    advance(&mut r, 3000).await;
    assert_eq!(s.lock().unwrap().launches.len(), 5);
    assert!(s.lock().unwrap().stops.is_empty());
    r.shutdown(4000).await.unwrap();
}

#[tokio::test]
async fn another_login_record_remains_read_only_even_when_the_old_pid_is_gone() {
    let (f, s, mut r) = fixture();
    let old_dir =
        f.0.join("org.tonyq.better-agent-terminal/fleet-tunnel-owners");
    std::fs::create_dir_all(&old_dir).unwrap();
    let mut parent = me();
    parent.pid = 900;
    parent.login.session_id = 2;
    let child = ProcessSnapshot {
        pid: 901,
        created_filetime: 9000000,
        executable: "C:\\Windows\\System32\\OpenSSH\\ssh.exe".into(),
        arguments: vec![
            "-N".into(),
            "-o".into(),
            "SetEnv=BAT_FLEET_MONITOR=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
        ],
        login: parent.login.clone(),
    };
    let bytes = serde_json::to_vec(
        &TunnelRecord::from_snapshot(&child, &parent)
            .unwrap()
            .document()
            .unwrap(),
    )
    .unwrap();
    let path = old_dir.join("node-1.json");
    std::fs::write(&path, &bytes).unwrap();
    let status = advance(&mut r, 1000).await;
    assert_eq!(std::fs::read(path).unwrap(), bytes);
    assert!(!s.lock().unwrap().stops.contains(&901));
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "node-1")
            .unwrap()
            .code
            .as_deref(),
        Some("OTHER_LOGIN_OWNER")
    );
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "connector")
            .unwrap()
            .level,
        "ready"
    );
}

#[tokio::test]
async fn installation_drift_cancels_pending_routes_before_any_launch() {
    let (f, s, mut r) = fixture();
    r.tick_guarded(1000, &|| Ok(())).await.unwrap();
    tokio::time::sleep(Duration::from_millis(2)).await;
    assert!(s.lock().unwrap().launches.is_empty());
    let status = r
        .tick_guarded(1001, &|| Err("INSTALLATION_CHANGED"))
        .await
        .unwrap();
    assert_eq!(status.lifecycle, "stopped");
    assert!(r.stopped());
    assert!(s.lock().unwrap().launches.is_empty());
    assert!(!f.0.join("BetterAgentTerminal/fleet-monitor.json").exists());
}

#[tokio::test]
async fn installation_drift_latches_shutdown_and_retains_uncertain_stop_evidence() {
    let (f, s, mut r) = fixture();
    s.lock().unwrap().hold.insert("node-1".into());
    advance(&mut r, 1000).await;
    assert!(s.lock().unwrap().active > 0);
    s.lock().unwrap().unknown_stop = true;
    let status = r
        .tick_guarded(1100, &|| Err("INSTALLATION_CHANGED"))
        .await
        .unwrap();
    assert_eq!(status.lifecycle, "stop_unconfirmed");
    assert!(!r.stopped());
    assert_eq!(s.lock().unwrap().active, 0);
    let (launches, stops, starts) = {
        let state = s.lock().unwrap();
        (state.launches.len(), state.stops.len(), state.starts.len())
    };
    assert_eq!(launches, 5);
    assert_eq!(stops, 5);
    assert!(f.0.join("BetterAgentTerminal/fleet-monitor.json").exists());
    // A restored installation does not resume probes, reissue stop or relaunch.
    assert_eq!(
        r.tick_guarded(1200, &|| Ok(())).await.unwrap().lifecycle,
        "stop_unconfirmed"
    );
    {
        let state = s.lock().unwrap();
        assert_eq!(state.launches.len(), launches);
        assert_eq!(state.stops.len(), stops);
        assert_eq!(state.starts.len(), starts);
    }
    // Original handles and records still authorize positive-death cleanup after drift.
    s.lock().unwrap().processes.retain(|pid, _| *pid == SELF);
    assert_eq!(
        r.tick_guarded(1300, &|| Err("INSTALLATION_CHANGED"))
            .await
            .unwrap()
            .lifecycle,
        "stopped"
    );
    assert!(r.stopped());
    assert!(!f.0.join("BetterAgentTerminal/fleet-monitor.json").exists());
    assert_eq!(s.lock().unwrap().stops.len(), stops);
}

#[tokio::test]
async fn installation_drift_at_launch_boundary_prevents_remaining_hosts_from_launching() {
    let (_f, s, mut r) = fixture();
    r.tick_guarded(1000, &|| Ok(())).await.unwrap();
    tokio::time::sleep(Duration::from_millis(2)).await;
    s.lock().unwrap().installation_changed_on_launch = true;
    assert_eq!(
        r.tick_guarded(1001, &|| Ok(())).await.unwrap().lifecycle,
        "stopped"
    );
    assert!(r.stopped());
    assert!(s.lock().unwrap().launches.is_empty());
}

#[tokio::test]
async fn outer_status_age_is_validated_even_when_all_connections_are_off() {
    let (f, _s, mut r) = fixture();
    std::fs::write(
        f.0.join("BetterAgentTerminal/fleet-client.json"),
        br#"{"version":1,"connections":[],"profiles":[],"dashboard":false}"#,
    )
    .unwrap();
    let mut status = r.tick(1000).await.unwrap();
    assert!(status.entries.iter().all(|entry| entry.level == "off"));
    assert!(status.is_fresh(61_000).unwrap());
    assert!(!status.is_fresh(61_001).unwrap());
    assert_eq!(status.is_fresh(999), Err("STATUS_UNPROVEN"));
    let raw = serde_json::to_vec(&status).unwrap();
    let parsed = Status::parse(&raw, &f.load(), r.epoch(), 61_001).unwrap();
    assert!(!parsed.is_fresh(61_001).unwrap());
    assert!(parsed.entries.iter().all(|entry| entry.level == "off"));
    status.observed_at = "malformed".into();
    assert_eq!(status.is_fresh(61_001), Err("STATUS_UNPROVEN"));
}

fn launch_intent(record: &TunnelRecord, name: &str) -> Vec<u8> {
    let parent = record.origin.as_ref().unwrap();
    serde_json::to_vec(&json!({
        "schema_version":1, "name":name,
        "monitor_instance":record.process.monitor_instance,
        "monitor_pid":parent.pid, "monitor_created":parent.created,
        "arguments":record.process.arguments,
    }))
    .unwrap()
}
fn restart(f: &Fixture, s: &Shared, runtime: Runtime) -> Runtime {
    drop(runtime);
    s.lock()
        .unwrap()
        .processes
        .get_mut(&SELF)
        .unwrap()
        .created_filetime += 100000;
    Supervisor::start(
        Options {
            configuration: f.paths(),
            roaming: f.0.clone(),
            quit_file: f.0.join("quit.json"),
        },
        FakeEffects(s.clone()),
        FakeProbes(s.clone()),
        Arc::new(FakeRoutes),
    )
    .unwrap()
}
#[tokio::test]
async fn failed_intent_retirement_keeps_owner_proof_for_restart() {
    let (f, s, mut r) = fixture();
    advance(&mut r, 1000).await;
    let directory = f.0.join("BetterAgentTerminal");
    let owner = directory.join("fleet-tunnel-owners/node-1.json");
    let original = std::fs::read(&owner).unwrap();
    let record = TunnelRecord::parse(&original, None).unwrap();
    let intent = directory.join("fleet-tunnel-intents/node-1.json");
    std::fs::create_dir_all(intent.parent().unwrap()).unwrap();
    // A failed/mismatched intent cleanup cannot delete the full positive child receipt.
    let unrelated = br#"{"monitor_instance":"ffffffffffffffffffffffffffffffff"}"#;
    std::fs::write(&intent, unrelated).unwrap();
    assert_eq!(
        r.shutdown(1200).await.unwrap().lifecycle,
        "stop_unconfirmed"
    );
    assert_eq!(std::fs::read(&owner).unwrap(), original);
    assert_eq!(std::fs::read(&intent).unwrap(), unrelated);
    assert!(!s
        .lock()
        .unwrap()
        .processes
        .contains_key(&record.process.pid));
    let stops = s.lock().unwrap().stops.len();
    // Repair only this synthetic fixture's exact original receipt; production never
    // clears an unproven intent by filename or guesses which child it belongs to.
    std::fs::write(&intent, launch_intent(&record, "node-1")).unwrap();
    let mut resumed = restart(&f, &s, r);
    let status = advance(&mut resumed, 2000).await;
    assert_eq!(s.lock().unwrap().stops.len(), stops);
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "node-1")
            .unwrap()
            .level,
        "ready"
    );
}
#[tokio::test]
async fn each_stop_retirement_cut_point_resumes_without_another_termination() {
    for retired in 0..=2 {
        let (f, s, mut r) = fixture();
        advance(&mut r, 1000).await;
        let directory = f.0.join("BetterAgentTerminal");
        let owner = directory.join("fleet-tunnel-owners/node-1.json");
        let original = std::fs::read(&owner).unwrap();
        let record = TunnelRecord::parse(&original, None).unwrap();
        let intent = directory.join("fleet-tunnel-intents/node-1.json");
        std::fs::create_dir_all(intent.parent().unwrap()).unwrap();
        std::fs::write(&intent, launch_intent(&record, "node-1")).unwrap();
        s.lock().unwrap().unknown_stop = true;
        assert_eq!(
            r.shutdown(1200).await.unwrap().lifecycle,
            "stop_unconfirmed"
        );
        let attempts = s.lock().unwrap().stops.len();
        s.lock().unwrap().processes.retain(|pid, _| *pid == SELF);
        s.lock().unwrap().unknown_stop = false;
        // Crash before retirement, after launch-intent retirement, or after
        // stop-intent retirement. The owner receipt is always the last deletion.
        if retired >= 1 {
            std::fs::remove_file(&intent).unwrap();
        }
        if retired >= 2 {
            std::fs::remove_file(directory.join("fleet-tunnel-stop-intents/node-1.json")).unwrap();
        }
        assert_eq!(std::fs::read(&owner).unwrap(), original);
        let mut resumed = restart(&f, &s, r);
        let status = advance(&mut resumed, 2000).await;
        assert_eq!(s.lock().unwrap().stops.len(), attempts);
        assert_eq!(
            status.entries.iter().filter(|e| e.level == "ready").count(),
            5
        );
        assert!(!directory
            .join("fleet-tunnel-stop-intents/node-1.json")
            .exists());
        assert!(!intent.exists());
    }
}
#[tokio::test]
async fn powershell_sparse_layers_remain_false_and_strict_validation_is_preserved() {
    let (f, _s, mut r) = fixture();
    let snapshot = advance(&mut r, 1000).await;
    let mut doc = serde_json::to_value(snapshot).unwrap();
    doc.as_object_mut().unwrap().remove("lifecycle");
    let row = &mut doc["entries"][0];
    row["selected"] = json!(false);
    row["level"] = json!("off");
    row["blocking"] = json!(null);
    row["code"] = json!(null);
    row["observed_at"] = json!(null);
    // Kit ConvertTo-FleetDesktopReadiness includes only present boolean layers;
    // an off row from Get-EntryReadiness has an empty layers object.
    row["layers"] = json!({});
    let config = f.load();
    let parsed =
        Status::parse(&serde_json::to_vec(&doc).unwrap(), &config, r.epoch(), 1100).unwrap();
    let row = &parsed.entries[0];
    assert_eq!(row.level, "off");
    assert!(!row.layers.tunnel && !row.layers.tls && !row.layers.bat && !row.layers.workspace);
    assert!(row.layers.auth.is_none() && row.layers.version.is_none());
    doc["entries"][0]["layers"] = json!({"tunnel":true});
    let parsed =
        Status::parse(&serde_json::to_vec(&doc).unwrap(), &config, r.epoch(), 1100).unwrap();
    assert!(parsed.entries[0].layers.tunnel);
    assert!(!parsed.entries[0].layers.tls);
    for invalid in [
        json!({"tls":"false"}),
        json!({"unexpected":true}),
        json!({"bat":null}),
    ] {
        let mut wrong = doc.clone();
        wrong["entries"][0]["layers"] = invalid;
        assert!(Status::parse(
            &serde_json::to_vec(&wrong).unwrap(),
            &config,
            r.epoch(),
            1100
        )
        .is_err());
    }
    let encoded = String::from_utf8(serde_json::to_vec(&doc).unwrap()).unwrap();
    let duplicate = encoded.replacen("\"tunnel\":true", "\"tunnel\":true,\"tunnel\":false", 1);
    assert!(Status::parse(duplicate.as_bytes(), &config, r.epoch(), 1100).is_err());
    assert!(Status::parse(encoded.as_bytes(), &config, &"f".repeat(32), 1100).is_err());
    doc["schema_version"] = json!(2);
    assert!(Status::parse(&serde_json::to_vec(&doc).unwrap(), &config, r.epoch(), 1100).is_err());
}

struct WakingRoutes(std::sync::atomic::AtomicBool);
impl RouteProbe for WakingRoutes {
    fn tailscale_direct<'a>(&'a self, _: &'a str, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async { false })
    }
    fn tailscale_peer<'a>(
        &'a self,
        ip: &'a str,
        _: Instant,
    ) -> bat_fleet_core::route::PeerFuture<'a> {
        Box::pin(async move {
            use bat_fleet_core::route::TailscalePeer;
            if ip != "100.64.0.1" {
                TailscalePeer::Unknown
            } else if self.0.load(std::sync::atomic::Ordering::SeqCst) {
                TailscalePeer::Direct
            } else {
                TailscalePeer::Idle
            }
        })
    }
    fn tailscale_wake<'a>(&'a self, _: &'a str, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            tokio::time::sleep(Duration::from_millis(1500)).await;
            self.0.store(true, std::sync::atomic::Ordering::SeqCst);
            true
        })
    }
    fn tcp<'a>(
        &'a self,
        ep: &'a bat_fleet_core::inventory::Endpoint,
        _: Instant,
    ) -> ProbeFuture<'a> {
        Box::pin(async move { ep.address == "100.64.0.1" })
    }
}
#[tokio::test(start_paused = true)]
async fn wake_progress_publishes_pending_and_completion_reselects_without_fifteen_second_wait() {
    let (fixture, shared, mut runtime) = fixture_with_routes(Arc::new(WakingRoutes(false.into())));
    let path = fixture.0.join("kit/fleet-inventory.json");
    let mut doc: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    assert_eq!(doc["direct_probe_ms"], 600);
    doc["hosts"][0]["routes"]
        .as_array_mut()
        .unwrap()
        .retain(|route| route["kind"] == "direct");
    doc["hosts"][0]["provision_route"] = json!("direct");
    std::fs::write(&path, serde_json::to_vec(&doc).unwrap()).unwrap();
    runtime.tick(1000).await.unwrap();
    let start = Instant::now();
    runtime.route_progress().await;
    assert_eq!(Instant::now(), start);
    let status = runtime.tick(1001).await.unwrap();
    let row = status
        .entries
        .iter()
        .find(|row| row.name == "node-1")
        .unwrap();
    assert_eq!(row.code.as_deref(), Some("TAILSCALE_WAKING"));
    assert_eq!(row.level, "degraded");
    assert!(!shared
        .lock()
        .unwrap()
        .launches
        .contains(&"node-1".to_owned()));
    assert!(!serde_json::to_string(&status).unwrap().contains("100.64."));
    runtime.route_progress().await;
    assert_eq!(Instant::now() - start, Duration::from_millis(1500));
    runtime.tick(2501).await.unwrap();
    tokio::task::yield_now().await;
    runtime.tick(2502).await.unwrap();
    assert!(shared
        .lock()
        .unwrap()
        .launches
        .contains(&"node-1".to_owned()));
    runtime.shutdown(2503).await.unwrap();
}

#[tokio::test]
async fn chosen_route_is_published_without_private_transport_inputs() {
    let (_f, _shared, mut runtime) = fixture();
    let status = advance(&mut runtime, 1000).await;
    let row =
        serde_json::to_value(status.entries.iter().find(|r| r.name == "node-1").unwrap()).unwrap();
    assert_eq!(row["route"]["kind"], "cf");
    assert!(row["route"]["generation"].as_u64().unwrap() > 0);
    let at = row["route"]["observed_at"].as_str().unwrap();
    assert!(at.starts_with("1970-01-01T00:00:01"));
    assert_eq!(row["route"].as_object().unwrap().len(), 3);
    runtime.shutdown(2000).await.unwrap();
}

#[tokio::test]
async fn route_confirmation_renews_only_use_and_clears_on_generation_and_deselection() {
    let (f, shared, mut runtime) = fixture();
    let first = advance(&mut runtime, 1000).await;
    let route = serde_json::to_value(&first.entries[0]).unwrap()["route"].clone();
    assert!(!route.is_null());
    // The existing ownership check confirms use independently of readiness probes.
    let renewed = advance(&mut runtime, 70_000).await;
    assert_eq!(renewed.entries[0].level, "ready");
    let confirmed = renewed.entries[0].route.as_ref().unwrap();
    assert_eq!(confirmed.observed_at, "1970-01-01T00:01:10.007Z");
    assert_eq!(
        serde_json::to_value(confirmed).unwrap()["kind"],
        route["kind"]
    );
    assert_eq!(
        serde_json::to_value(confirmed).unwrap()["generation"],
        route["generation"]
    );
    shared
        .lock()
        .unwrap()
        .tokens
        .insert("node-1".into(), "replacement".into());
    let changed = advance(&mut runtime, 71_000).await;
    assert!(changed
        .entries
        .iter()
        .find(|r| r.name == "node-1")
        .unwrap()
        .route
        .is_none());
    let selection = f.0.join("BetterAgentTerminal/fleet-client.json");
    std::fs::write(
        selection,
        br#"{"version":1,"connections":["connector"],"profiles":[],"dashboard":false}"#,
    )
    .unwrap();
    let off = runtime.tick(72_000).await.unwrap();
    assert!(off.entries.iter().all(|r| r.route.is_none()));
    let shutdown = runtime.shutdown(73_000).await.unwrap();
    assert!(shutdown.entries.iter().all(|r| r.route.is_none()));
}

#[tokio::test]
async fn replacement_route_never_retains_the_previous_choices_time_or_generation() {
    let (_f, shared, mut runtime) = fixture();
    let first = advance(&mut runtime, 1000).await;
    let before = first
        .entries
        .iter()
        .find(|r| r.name == "node-1")
        .unwrap()
        .route
        .as_ref()
        .unwrap();
    shared
        .lock()
        .unwrap()
        .processes
        .retain(|_, p| !p.arguments.iter().any(|a| a == "node-1-cf"));
    let ended = runtime.tick(2000).await.unwrap();
    assert!(ended
        .entries
        .iter()
        .find(|r| r.name == "node-1")
        .unwrap()
        .route
        .is_none());
    let restarted = advance(&mut runtime, 8000).await;
    let after = restarted
        .entries
        .iter()
        .find(|r| r.name == "node-1")
        .unwrap()
        .route
        .as_ref()
        .unwrap();
    assert_ne!(after.observed_at, before.observed_at);
    assert_eq!(after.kind, before.kind);
    runtime.shutdown(9000).await.unwrap();
}

#[tokio::test]
async fn live_route_is_dated_by_the_latest_ownership_check() {
    let (_f, _shared, mut runtime) = fixture();
    advance(&mut runtime, 1000).await;
    let checked = runtime.tick(301_000).await.unwrap();
    let route = checked
        .entries
        .iter()
        .find(|row| row.name == "node-1")
        .unwrap()
        .route
        .as_ref()
        .unwrap();
    assert_eq!(route.observed_at, "1970-01-01T00:05:01Z");
    runtime.shutdown(302_000).await.unwrap();
}

#[tokio::test]
async fn unknown_ownership_keeps_route_age_despite_fresh_probe_and_snapshot_read() {
    let (fixture, shared, mut runtime) = fixture();
    let first = advance(&mut runtime, 1000).await;
    let original = first
        .entries
        .iter()
        .find(|row| row.name == "node-1")
        .unwrap()
        .route
        .as_ref()
        .unwrap()
        .observed_at
        .clone();
    {
        let mut state = shared.lock().unwrap();
        let pid = *state
            .processes
            .iter()
            .find(|(_, p)| p.arguments.iter().any(|a| a == "node-1-cf"))
            .unwrap()
            .0;
        state.unknown_processes.insert(pid);
    }
    let checked = advance(&mut runtime, 301_000).await;
    let row = checked
        .entries
        .iter()
        .find(|row| row.name == "node-1")
        .unwrap();
    assert_eq!(row.level, "ready");
    assert_eq!(row.route.as_ref().unwrap().observed_at, original);
    let parsed = Status::parse(
        &serde_json::to_vec(&checked).unwrap(),
        &fixture.load(),
        runtime.epoch(),
        301_100,
    )
    .unwrap();
    assert_eq!(
        parsed
            .entries
            .iter()
            .find(|row| row.name == "node-1")
            .unwrap()
            .route
            .as_ref()
            .unwrap()
            .observed_at,
        original
    );
    shared.lock().unwrap().unknown_processes.clear();
    let verified = runtime.tick(302_000).await.unwrap();
    assert_eq!(
        verified
            .entries
            .iter()
            .find(|row| row.name == "node-1")
            .unwrap()
            .route
            .as_ref()
            .unwrap()
            .observed_at,
        "1970-01-01T00:05:02Z"
    );
    runtime.shutdown(303_000).await.unwrap();
}

use std::sync::atomic::{AtomicBool, Ordering};
enum RequirementEvent {
    RouteProbe,
    WakeStarted(String),
    ProbeReady(String),
}
struct RequirementRoutes {
    peer: bat_fleet_core::route::TailscalePeer,
    calls: Mutex<Vec<String>>,
    hold: AtomicBool,
    healthy: AtomicBool,
    wake_ended: Arc<AtomicBool>,
    events: tokio::sync::mpsc::UnboundedSender<RequirementEvent>,
}
impl RouteProbe for RequirementRoutes {
    fn tailscale_direct<'a>(&'a self, _: &'a str, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async { false })
    }
    fn tailscale_peer<'a>(
        &'a self,
        ip: &'a str,
        _: Instant,
    ) -> bat_fleet_core::route::PeerFuture<'a> {
        Box::pin(async move {
            self.calls.lock().unwrap().push(ip.into());
            self.events.send(RequirementEvent::RouteProbe).unwrap();
            if ip == "100.64.0.1" && self.hold.load(Ordering::Relaxed) {
                return std::future::pending().await;
            }
            if ip == "100.64.0.1" && self.healthy.load(Ordering::Relaxed) {
                bat_fleet_core::route::TailscalePeer::Direct
            } else {
                self.peer
            }
        })
    }
    fn tcp<'a>(
        &'a self,
        endpoint: &'a bat_fleet_core::inventory::Endpoint,
        _: Instant,
    ) -> ProbeFuture<'a> {
        Box::pin(async move {
            self.calls
                .lock()
                .unwrap()
                .push(format!("tcp:{}", endpoint.address));
            self.events.send(RequirementEvent::RouteProbe).unwrap();
            endpoint.address == "100.64.0.1" && self.healthy.load(Ordering::Relaxed)
        })
    }
    fn tailscale_wake<'a>(&'a self, ip: &'a str, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            struct WakeEnd(Arc<AtomicBool>);
            impl Drop for WakeEnd {
                fn drop(&mut self) {
                    self.0.store(true, Ordering::SeqCst);
                }
            }
            let _end = (ip == "100.64.0.1").then(|| WakeEnd(self.wake_ended.clone()));
            self.calls.lock().unwrap().push(format!("wake:{ip}"));
            self.events
                .send(RequirementEvent::WakeStarted(ip.into()))
                .unwrap();
            std::future::pending().await
        })
    }
}
struct RequirementFixture {
    files: Fixture,
    state: Shared,
    runtime: Supervisor<FakeEffects, FakeProbes, RequirementRoutes>,
    probe: Arc<RequirementRoutes>,
    expected: bat_fleet_core::route::RouteRequirement,
    events: tokio::sync::mpsc::UnboundedReceiver<RequirementEvent>,
}
fn requirement_fixture(
    peer: bat_fleet_core::route::TailscalePeer,
    exhausted: bool,
) -> RequirementFixture {
    use bat_fleet_core::route::{RouteRequirement, TailscalePeer};
    let (events, receiver) = tokio::sync::mpsc::unbounded_channel();
    let probe = Arc::new(RequirementRoutes {
        peer,
        calls: Mutex::new(Vec::new()),
        hold: AtomicBool::new(false),
        healthy: AtomicBool::new(false),
        wake_ended: Arc::default(),
        events: events.clone(),
    });
    let (files, state, runtime) = fixture_with_routes(probe.clone());
    state.lock().unwrap().requirement_events = Some(events);
    let path = files.0.join("kit/fleet-inventory.json");
    let mut inventory: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    inventory["hosts"][0]["routes"]
        .as_array_mut()
        .unwrap()
        .retain(|route| route["kind"] == "direct");
    inventory["hosts"][0]["provision_route"] = "direct".into();
    if exhausted {
        inventory["hosts"][0]["routes"]
            .as_array_mut()
            .unwrap()
            .push(json!({"kind":"lan","alias":"fixture-lan","probe":"192.0.2.10:22"}));
        let ssh = files.0.join("kit/ssh-config");
        let mut text = std::fs::read_to_string(&ssh).unwrap();
        text.push_str("\nHost fixture-lan\n HostName example.invalid\n");
        std::fs::write(ssh, text).unwrap();
    }
    std::fs::write(path, serde_json::to_vec(&inventory).unwrap()).unwrap();
    let expected = if exhausted {
        RouteRequirement::ConfiguredRoutesExhausted
    } else {
        match peer {
            TailscalePeer::Unknown => RouteRequirement::DirectPeerUnknown,
            TailscalePeer::Idle => RouteRequirement::DirectNotConfirmed,
            TailscalePeer::Direct => RouteRequirement::DirectTcpUnreachable,
        }
    };
    RequirementFixture {
        files,
        state,
        runtime,
        probe,
        expected,
        events: receiver,
    }
}
fn requirement_row(status: &Status) -> &bat_fleet_core::supervisor_status::Entry {
    status
        .entries
        .iter()
        .find(|row| row.name == "node-1")
        .unwrap()
}
impl RequirementFixture {
    async fn settle(&mut self, now: u64) {
        // These current-thread tests pause Tokio time. Disk IO must not consume
        // the product's five-second wake deadline or the held route's budget.
        let start = Instant::now();
        self.runtime.tick(now).await.unwrap();
        let mut ready = HashSet::new();
        let mut saw_failure = false;
        let waking = self.probe.peer == bat_fleet_core::route::TailscalePeer::Idle;
        let mut wake_started = false;
        loop {
            // Drive one supervisor transition per actual fixture effect, never
            // retry a wrong publication or guess completion from a tick count.
            // Ready probes return in this same poll without another await.
            match self.events.recv().await.unwrap() {
                RequirementEvent::ProbeReady(name) => {
                    ready.insert(name);
                }
                RequirementEvent::WakeStarted(ip) => {
                    if ip == "100.64.0.1" {
                        wake_started = true;
                        // Consume the reservation notification. A later progress
                        // wait must prove actual completion, not this old permit.
                        self.runtime.route_progress().await;
                    }
                }
                RequirementEvent::RouteProbe => (),
            }
            let published = self.runtime.tick(now).await.unwrap();
            let row = requirement_row(&published);
            if wake_started {
                assert!(self
                    .probe
                    .calls
                    .lock()
                    .unwrap()
                    .contains(&"wake:100.64.0.1".into()));
                assert!(!self.probe.wake_ended.load(Ordering::SeqCst));
                assert_eq!(row.code.as_deref(), Some("TAILSCALE_WAKING"));
            }
            if matches!(
                row.code.as_deref(),
                Some("ROUTE_UNAVAILABLE" | "TAILSCALE_WAKING")
            ) && row.route_requirement.is_some()
            {
                assert_eq!(row.route_requirement, Some(self.expected));
                saw_failure = true;
            }
            let expected_ready: &[&str] = if waking {
                // Wake also gates these peers' probes; only Connector is
                // expected to finish while their wake effects remain pending.
                &["connector"]
            } else {
                &["connector", "node-2", "node-3", "node-4"]
            };
            if expected_ready.iter().all(|name| ready.contains(*name))
                && saw_failure
                && (!waking || wake_started)
            {
                for name in expected_ready {
                    assert!(ready.contains(*name));
                    assert_eq!(
                        published
                            .entries
                            .iter()
                            .find(|row| row.name == *name)
                            .unwrap()
                            .level,
                        "ready"
                    );
                }
                break;
            }
        }
        assert!(saw_failure);
        assert_eq!(Instant::now(), start);
    }
    async fn wait_for_calls(
        &mut self,
        peer_attempts: usize,
        tcp_attempts: usize,
        lan_attempts: usize,
    ) {
        loop {
            let complete = {
                let calls = self.probe.calls.lock().unwrap();
                [
                    ("100.64.0.1", peer_attempts),
                    ("tcp:100.64.0.1", tcp_attempts),
                    ("tcp:192.0.2.10", lan_attempts),
                ]
                .into_iter()
                .all(|(call, expected)| {
                    calls.iter().filter(|value| *value == call).count() >= expected
                })
            };
            if complete {
                return;
            }
            assert!(matches!(
                self.events.recv().await.unwrap(),
                RequirementEvent::RouteProbe
            ));
        }
    }
    async fn wait_for_probe(&mut self, expected: &str) {
        // Earlier route-effect notifications may still be queued; only the
        // named observation's completion authorizes the next harvest tick.
        loop {
            match self.events.recv().await.unwrap() {
                RequirementEvent::ProbeReady(name) => {
                    assert_eq!(name, expected);
                    return;
                }
                RequirementEvent::RouteProbe => (),
                RequirementEvent::WakeStarted(_) => panic!("unexpected wake"),
            }
        }
    }
    fn bytes(&self) -> Vec<u8> {
        std::fs::read(
            self.files
                .0
                .join("BetterAgentTerminal/fleet-desktop-status.json"),
        )
        .unwrap()
    }
    fn parse(&self, now: u64) -> Status {
        Status::parse(&self.bytes(), &self.files.load(), self.runtime.epoch(), now).unwrap()
    }
    fn assert_effects(
        &self,
        status: &Status,
        peer_attempts: usize,
        tcp_attempts: usize,
        lan_attempts: usize,
        wakes: usize,
    ) {
        assert_eq!(
            status
                .entries
                .iter()
                .find(|row| row.name == "connector")
                .unwrap()
                .level,
            "ready"
        );
        let state = self.state.lock().unwrap();
        assert!(!state.launches.iter().any(|name| name == "node-1"));
        assert!(state.stops.is_empty());
        let calls = self.probe.calls.lock().unwrap();
        for (call, expected) in [
            ("100.64.0.1", peer_attempts),
            ("tcp:100.64.0.1", tcp_attempts),
            ("tcp:192.0.2.10", lan_attempts),
            ("wake:100.64.0.1", wakes),
        ] {
            assert_eq!(
                calls.iter().filter(|actual| *actual == call).count(),
                expected,
                "{call}"
            );
        }
    }
}
#[tokio::test(start_paused = true)]
async fn requirement_settle_returns_a_live_waking_publication() {
    let mut f = requirement_fixture(bat_fleet_core::route::TailscalePeer::Idle, false);
    f.settle(1000).await;
    assert_eq!(
        requirement_row(&f.parse(1000)).code.as_deref(),
        Some("TAILSCALE_WAKING")
    );
    assert!(!f.probe.wake_ended.load(Ordering::SeqCst));
    f.runtime.shutdown(1001).await.unwrap();
}
#[tokio::test(start_paused = true)]
async fn requirement_wake_deadline_ends_the_effect_before_pending() {
    let mut f = requirement_fixture(bat_fleet_core::route::TailscalePeer::Idle, false);
    f.settle(1000).await;
    tokio::time::advance(Duration::from_secs(4)).await;
    let waking = f.runtime.tick(1001).await.unwrap();
    assert_eq!(
        requirement_row(&waking).code.as_deref(),
        Some("TAILSCALE_WAKING")
    );
    assert!(!f.probe.wake_ended.load(Ordering::SeqCst));
    f.assert_effects(&waking, 1, 0, 0, 1);
    tokio::time::advance(Duration::from_secs(1)).await;
    f.runtime.route_progress().await;
    assert!(f.probe.wake_ended.load(Ordering::SeqCst));
    let pending = f.runtime.tick(1002).await.unwrap();
    assert_eq!(
        requirement_row(&pending).code.as_deref(),
        Some("PROBE_PENDING")
    );
    f.assert_effects(&pending, 1, 0, 0, 1);
    f.runtime.shutdown(1003).await.unwrap();
}
#[tokio::test(start_paused = true)]
async fn direct_requirement_survives_between_and_during_route_attempts() {
    use bat_fleet_core::route::TailscalePeer;
    for (peer, exhausted) in [
        (TailscalePeer::Unknown, false),
        (TailscalePeer::Direct, false),
        (TailscalePeer::Unknown, true),
        (TailscalePeer::Idle, false),
    ] {
        let mut f = requirement_fixture(peer, exhausted);
        f.settle(1000).await;
        let tcp = usize::from(peer == TailscalePeer::Direct);
        let lan = usize::from(exhausted);
        let wakes = usize::from(peer == TailscalePeer::Idle);
        for now in [1001, 1200, 5000, 15999] {
            let status = f.runtime.tick(now).await.unwrap();
            assert_eq!(
                serde_json::to_value(&status).unwrap(),
                serde_json::to_value(f.parse(now)).unwrap()
            );
            let row = requirement_row(&status);
            assert_eq!(
                row.code.as_deref(),
                Some(if wakes == 1 {
                    "TAILSCALE_WAKING"
                } else {
                    "PROBE_PENDING"
                })
            );
            assert_eq!(
                row.route_requirement,
                Some(f.expected),
                "peer={peer:?}, exhausted={exhausted}, now={now}"
            );
            assert!(row.route.is_none());
            assert!(row.observed_at.is_none());
            f.assert_effects(&status, 1, tcp, lan, wakes);
        }
        if wakes == 0 {
            f.probe.hold.store(true, Ordering::Relaxed);
            f.runtime.tick(16000).await.unwrap();
            f.wait_for_calls(2, tcp, lan * 2).await;
            let status = f.runtime.tick(16001).await.unwrap();
            let row = requirement_row(&status);
            assert_eq!(row.code.as_deref(), Some("PROBE_PENDING"));
            assert_eq!(row.route_requirement, Some(f.expected));
            // LAN probes run alongside the held second direct probe.
            f.assert_effects(&status, 2, tcp, lan * 2, wakes);
        }
        f.runtime.shutdown(16002).await.unwrap();
    }
}
#[tokio::test(start_paused = true)]
async fn route_requirement_clears_on_success_before_a_probe_arrives() {
    let mut f = requirement_fixture(bat_fleet_core::route::TailscalePeer::Unknown, false);
    f.settle(1000).await;
    f.probe.healthy.store(true, Ordering::Relaxed);
    f.runtime.tick(16000).await.unwrap();
    f.wait_for_calls(2, 1, 0).await;
    let status = f.runtime.tick(16000).await.unwrap();
    let row = requirement_row(&status);
    assert!(row.route.is_some());
    assert!(row.observed_at.is_none());
    assert_eq!(row.code.as_deref(), Some("PROBE_PENDING"));
    assert!(row.route_requirement.is_none());
    f.wait_for_probe("node-1").await;
    let status = f.runtime.tick(16001).await.unwrap();
    assert_eq!(requirement_row(&status).level, "ready");
    assert!(requirement_row(&status).route_requirement.is_none());
    {
        let state = f.state.lock().unwrap();
        assert_eq!(
            state
                .launches
                .iter()
                .filter(|name| *name == "node-1")
                .count(),
            1
        );
        assert!(state.stops.is_empty());
    }
    f.runtime.shutdown(16002).await.unwrap();
}
#[tokio::test(start_paused = true)]
async fn route_requirement_clears_on_selection_invalidation() {
    let mut f = requirement_fixture(bat_fleet_core::route::TailscalePeer::Unknown, false);
    f.settle(1000).await;
    f.probe.hold.store(true, Ordering::Relaxed);
    let path = f.files.0.join("BetterAgentTerminal/fleet-client.json");
    let original = std::fs::read(&path).unwrap();
    let mut selection: serde_json::Value = serde_json::from_slice(&original).unwrap();
    selection["connections"]
        .as_array_mut()
        .unwrap()
        .retain(|v| v != "node-1");
    std::fs::write(&path, serde_json::to_vec(&selection).unwrap()).unwrap();
    let off = f.runtime.tick(1200).await.unwrap();
    assert!(!requirement_row(&off).selected);
    assert!(requirement_row(&off).route_requirement.is_none());
    std::fs::write(path, original).unwrap();
    let pending = f.runtime.tick(1300).await.unwrap();
    assert_eq!(
        requirement_row(&pending).code.as_deref(),
        Some("PROBE_PENDING")
    );
    assert!(requirement_row(&pending).route_requirement.is_none());
    assert!(f.state.lock().unwrap().stops.is_empty());
    assert!(!f
        .state
        .lock()
        .unwrap()
        .launches
        .iter()
        .any(|name| name == "node-1"));
    f.runtime.shutdown(1400).await.unwrap();
}
#[tokio::test(start_paused = true)]
async fn route_requirement_clears_on_credential_generation_change() {
    let mut f = requirement_fixture(bat_fleet_core::route::TailscalePeer::Unknown, false);
    f.settle(1000).await;
    f.probe.hold.store(true, Ordering::Relaxed);
    f.state
        .lock()
        .unwrap()
        .tokens
        .insert("node-1".into(), "rotated-synthetic-token".into());
    let status = f.runtime.tick(1200).await.unwrap();
    assert_eq!(
        requirement_row(&status).code.as_deref(),
        Some("PROBE_PENDING")
    );
    assert!(requirement_row(&status).route_requirement.is_none());
    assert!(f.state.lock().unwrap().stops.is_empty());
    assert!(!f
        .state
        .lock()
        .unwrap()
        .launches
        .iter()
        .any(|name| name == "node-1"));
    f.runtime.shutdown(1300).await.unwrap();
}
#[tokio::test(start_paused = true)]
async fn route_requirement_withholds_credential_errors() {
    let mut f = requirement_fixture(bat_fleet_core::route::TailscalePeer::Unknown, false);
    f.settle(1000).await;
    f.probe.hold.store(true, Ordering::Relaxed);
    f.state
        .lock()
        .unwrap()
        .missing_credentials
        .insert("node-1".into());
    let status = f.runtime.tick(1200).await.unwrap();
    assert_eq!(
        requirement_row(&status).code.as_deref(),
        Some("CREDENTIAL_MISSING")
    );
    assert!(requirement_row(&status).route_requirement.is_none());
    assert!(f.state.lock().unwrap().stops.is_empty());
    assert!(!f
        .state
        .lock()
        .unwrap()
        .launches
        .iter()
        .any(|name| name == "node-1"));
    f.runtime.shutdown(1300).await.unwrap();
}
fn pending_native_case(
    mut snapshot: Status,
    now: u64,
    selection: &serde_json::Value,
) -> serde_json::Value {
    snapshot.monitor_epoch = "a".repeat(32);
    snapshot.configuration_binding = "c".repeat(64);
    snapshot.applied_selection_revision = "d".repeat(64);
    json!({"snapshot":snapshot,"now":now,"connections":selection["connections"],"selection_revision":"d".repeat(64)})
}
async fn pending_requirement_fixture() -> serde_json::Value {
    use bat_fleet_core::route::TailscalePeer;
    let mut cases = json!({});
    let anchor = 1_791_460_800_000;
    for (name, peer, exhausted) in [
        ("unknown", TailscalePeer::Unknown, false),
        ("tcp", TailscalePeer::Direct, false),
        ("exhausted", TailscalePeer::Unknown, true),
    ] {
        let mut f = requirement_fixture(peer, exhausted);
        f.settle(anchor).await;
        let now = anchor + 200;
        f.runtime.tick(now).await.unwrap();
        let pending = f.parse(now);
        let aged = f.parse(now + 60_001);
        f.assert_effects(
            &pending,
            1,
            usize::from(peer == TailscalePeer::Direct),
            usize::from(exhausted),
            0,
        );
        let selection: serde_json::Value = serde_json::from_slice(
            &std::fs::read(f.files.0.join("BetterAgentTerminal/fleet-client.json")).unwrap(),
        )
        .unwrap();
        cases[name] = json!({"pending":pending_native_case(pending,now,&selection),"aged":pending_native_case(aged,now+60_001,&selection)});
        f.runtime.shutdown(now + 1).await.unwrap();
    }
    cases
}
#[tokio::test(start_paused = true)]
#[ignore = "explicit fixture regeneration only"]
async fn generate_pending_requirement_fixture() {
    println!("FLEET_PENDING={}", pending_requirement_fixture().await);
}
#[tokio::test(start_paused = true)]
async fn pending_requirement_rows_match_native_fixture() {
    let expected: serde_json::Value =
        serde_json::from_str(include_str!("../../tests/fixtures/fleet-pending.json")).unwrap();
    assert_eq!(pending_requirement_fixture().await, expected);
}

#[tokio::test]
async fn failed_start_restores_previous_monitor_evidence_before_retry() {
    let (f, shared, mut runtime) = fixture();
    advance(&mut runtime, 1000).await;
    let path = f.0.join("BetterAgentTerminal/fleet-monitor.json");
    let previous = std::fs::read(&path).unwrap();
    drop(runtime);
    {
        let mut state = shared.lock().unwrap();
        state.processes.get_mut(&SELF).unwrap().created_filetime += 100000;
        state.iteration_error = true;
    }
    let start = || {
        Supervisor::start(
            Options {
                configuration: f.paths(),
                roaming: f.0.clone(),
                quit_file: f.0.join("quit.json"),
            },
            FakeEffects(shared.clone()),
            FakeProbes(shared.clone()),
            Arc::new(FakeRoutes),
        )
    };
    assert_eq!(start().err(), Some("OWNER_UNPROVEN"));
    assert_eq!(std::fs::read(path).unwrap(), previous);
    shared.lock().unwrap().iteration_error = false;
    let mut runtime = start().unwrap();
    advance(&mut runtime, 2000).await;
    assert_eq!(shared.lock().unwrap().launches.len(), 5);
    assert!(shared.lock().unwrap().stops.is_empty());
    runtime.shutdown(3000).await.unwrap();
}

#[tokio::test]
async fn listener_loss_with_uncertain_stop_never_resends_or_blocks_other_hosts() {
    let (_f, shared, mut runtime) = fixture();
    advance(&mut runtime, 1000).await;
    {
        let mut state = shared.lock().unwrap();
        state.listener_down.insert("node-1".into());
        state.unknown_stop = true;
    }
    advance(&mut runtime, 20000).await;
    let stops = shared.lock().unwrap().stops.len();
    assert_eq!(stops, 1);
    advance(&mut runtime, 90000).await;
    assert_eq!(shared.lock().unwrap().stops.len(), stops);
    assert_eq!(shared.lock().unwrap().launches.len(), 5);
    // Even if the listener comes back, the original stop intent fences recovery.
    shared.lock().unwrap().listener_down.clear();
    let status = advance(&mut runtime, 91000).await;
    assert_eq!(
        status
            .entries
            .iter()
            .find(|e| e.name == "connector")
            .unwrap()
            .level,
        "ready"
    );
    assert_eq!(shared.lock().unwrap().stops.len(), stops);
    // Resolve only the fixture-owned uncertain child, then allow ordinary cleanup.
    {
        let mut state = shared.lock().unwrap();
        state.unknown_stop = false;
        let pid = *state.stops.last().unwrap();
        state.processes.remove(&pid);
    }
    runtime.shutdown(92000).await.unwrap();
}
