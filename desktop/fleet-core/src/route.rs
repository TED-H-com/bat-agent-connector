//! Local route choices, never central authority or a BAT readiness assertion.
pub use crate::route_probe::NativeRouteProbe;
use crate::{
    inventory::{endpoint, Endpoint, Inventory},
    ownership::{probe_current, ProbeGeneration, Recovery},
    strict_json, Result,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::HashMap,
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    sync::Notify,
    time::{timeout_at, Instant},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RouteKind {
    Direct,
    Lan,
    Cf,
    Configured,
}
/// Explanation of existing eligibility only; no routing input leaves the producer.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE", try_from = "String")]
pub enum RouteRequirement {
    DirectPeerUnknown,
    DirectNotConfirmed,
    DirectTcpUnreachable,
    ConfiguredRoutesExhausted,
}
impl TryFrom<String> for RouteRequirement {
    type Error = &'static str;
    fn try_from(value: String) -> Result<Self> {
        // Serde's default enum decoder also accepts externally tagged objects.
        // Status and IPC carry only a code string, never an object representation.
        match value.as_str() {
            "DIRECT_PEER_UNKNOWN" => Ok(Self::DirectPeerUnknown),
            "DIRECT_NOT_CONFIRMED" => Ok(Self::DirectNotConfirmed),
            "DIRECT_TCP_UNREACHABLE" => Ok(Self::DirectTcpUnreachable),
            "CONFIGURED_ROUTES_EXHAUSTED" => Ok(Self::ConfiguredRoutesExhausted),
            _ => Err("ROUTE_REQUIREMENT_INVALID"),
        }
    }
}
#[derive(Debug, Serialize)]
pub struct RouteFailure {
    pub code: &'static str,
    pub route_requirement: Option<RouteRequirement>,
}
impl From<&'static str> for RouteFailure {
    fn from(code: &'static str) -> Self {
        Self {
            code,
            route_requirement: None,
        }
    }
}
#[derive(Clone, PartialEq, Eq)]
struct Route {
    kind: RouteKind,
    alias: String,
    probe: Option<Endpoint>,
    probe_budget: Duration,
    allow_tailscale_relay: bool,
}
/// Native-only private endpoints/aliases; never Debug/Serialize this input.
#[derive(Clone, PartialEq, Eq)]
pub struct Routes {
    name: String,
    routes: Vec<Route>,
    prefer_lan: bool,
}
impl Routes {
    pub fn from_inventory(inventory: &Inventory, name: &str) -> Result<Self> {
        let doc = inventory.document();
        let budget = doc["direct_probe_ms"]
            .as_u64()
            .filter(|v| (1..=5000).contains(v))
            .ok_or("INVENTORY_INVALID")?;
        let mut routes = Vec::new();
        let mut prefer_lan = false;
        if doc["connector"]["name"].as_str() == Some(name) {
            if let Some(alias) = doc["connector"]["ssh_alias"].as_str() {
                routes.push(Route {
                    kind: RouteKind::Configured,
                    alias: alias.into(),
                    probe: None,
                    probe_budget: Duration::from_millis(budget),
                    allow_tailscale_relay: false,
                });
            }
        } else {
            let host = doc["hosts"]
                .as_array()
                .ok_or("INVENTORY_INVALID")?
                .iter()
                .find(|v| v["name"].as_str() == Some(name))
                .ok_or("ROUTE_UNKNOWN_HOST")?;
            prefer_lan = host["prefer_lan"].as_bool().unwrap_or(false);
            for route in host["routes"].as_array().ok_or("INVENTORY_INVALID")? {
                let kind = match route["kind"].as_str() {
                    Some("direct") => RouteKind::Direct,
                    Some("lan") => RouteKind::Lan,
                    Some("cf") => RouteKind::Cf,
                    _ => return Err("INVENTORY_INVALID"),
                };
                let alias = route["alias"]
                    .as_str()
                    .ok_or("INVENTORY_INVALID")?
                    .to_owned();
                let probe = if matches!(kind, RouteKind::Direct | RouteKind::Lan) {
                    Some(endpoint(
                        route["probe"].as_str().ok_or("INVENTORY_INVALID")?,
                        false,
                    )?)
                } else {
                    None
                };
                let probe_budget = Duration::from_millis(
                    route
                        .get("direct_probe_ms")
                        .or_else(|| host.get("direct_probe_ms"))
                        .and_then(Value::as_u64)
                        .unwrap_or(budget),
                );
                let allow_tailscale_relay = route
                    .get("allow_tailscale_relay")
                    .or_else(|| host.get("allow_tailscale_relay"))
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                routes.push(Route {
                    kind,
                    alias,
                    probe,
                    probe_budget,
                    allow_tailscale_relay,
                });
            }
        }
        Ok(Self {
            name: name.into(),
            routes,
            prefer_lan,
        })
    }
}
pub type ProbeFuture<'a> = Pin<Box<dyn Future<Output = bool> + Send + 'a>>;
/// Only trusted native code supplies a probe. Every implementation must respect
/// the passed absolute deadline; the selector additionally bounds the whole call.
pub trait RouteProbe: Sync {
    fn tailscale_direct<'a>(&'a self, ip: &'a str, deadline: Instant) -> ProbeFuture<'a>;
    /// Implementations without peer identity evidence never authorize wake traffic.
    fn tailscale_peer<'a>(&'a self, ip: &'a str, deadline: Instant) -> PeerFuture<'a> {
        Box::pin(async move {
            if self.tailscale_direct(ip, deadline).await {
                TailscalePeer::Direct
            } else {
                TailscalePeer::Unknown
            }
        })
    }
    fn tailscale_wake<'a>(&'a self, _ip: &'a str, _deadline: Instant) -> ProbeFuture<'a> {
        Box::pin(async { false })
    }
    fn tcp<'a>(&'a self, endpoint: &'a Endpoint, deadline: Instant) -> ProbeFuture<'a>;
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TailscalePeer {
    Unknown,
    Idle,
    Relay,
    Direct,
}
pub type PeerFuture<'a> = Pin<Box<dyn Future<Output = TailscalePeer> + Send + 'a>>;
/// Strict local Tailscale evidence. Duplicate/case/escape variants fail closed;
/// multiple peers claiming the target IP are ambiguous rather than first-wins.
pub fn tailscale_peer(bytes: &[u8], ip: &str) -> TailscalePeer {
    // Only literal tailnet addresses may be woken, never a mutable MagicDNS name.
    if ip.parse::<std::net::IpAddr>().is_err() {
        return TailscalePeer::Unknown;
    }
    let Ok(value) = strict_json::parse(bytes, 1024 * 1024) else {
        return TailscalePeer::Unknown;
    };
    let Some(peers) = value.get("Peer").and_then(Value::as_object) else {
        return TailscalePeer::Unknown;
    };
    let mut matched = peers.values().filter(|peer| {
        peer.get("TailscaleIPs")
            .and_then(Value::as_array)
            .is_some_and(|ips| ips.iter().any(|v| v.as_str() == Some(ip)))
    });
    let Some(peer) = matched.next() else {
        return TailscalePeer::Unknown;
    };
    if matched.next().is_some() {
        return TailscalePeer::Unknown;
    }
    if peer.get("Online") == Some(&Value::Bool(true))
        && peer
            .get("CurAddr")
            .and_then(Value::as_str)
            .is_some_and(|v| !v.trim().is_empty())
    {
        TailscalePeer::Direct
    } else if peer.get("Online") == Some(&Value::Bool(true))
        && peer.get("Active") == Some(&Value::Bool(true))
        && peer.get("CurAddr").is_none_or(|v| v.as_str() == Some(""))
        && peer
            .get("Relay")
            .and_then(Value::as_str)
            .is_some_and(|v| !v.trim().is_empty())
    {
        TailscalePeer::Relay
    } else {
        TailscalePeer::Idle
    }
}
pub fn tailscale_direct(bytes: &[u8], ip: &str) -> bool {
    tailscale_peer(bytes, ip) == TailscalePeer::Direct
}

#[derive(Default)]
struct WakeBackoff {
    failures: u8,
    next: Option<Instant>,
    active: bool,
    pending: Option<String>,
    handle: Option<tokio::task::JoinHandle<()>>,
    completed: bool,
    epoch: u128,
}
/// Owned by the background task, not the short selection future. Cancellation
/// keeps the charged cooldown and releases the reservation when the task drops.
struct WakeAttempt {
    state: Arc<Mutex<WakeBackoff>>,
    progress: Arc<Notify>,
    epoch: u128,
    cooldown: Duration,
}
impl Drop for WakeAttempt {
    fn drop(&mut self) {
        let mut wake = self.state.lock().unwrap();
        wake.active = false;
        wake.completed = wake.epoch == self.epoch;
        if wake.completed {
            wake.next = if wake.failures == 0 {
                None
            } else {
                Some(Instant::now() + self.cooldown)
            };
        }
        self.progress.notify_one();
    }
}
#[derive(Default)]
struct Failures {
    count: u8,
    blocked_until: Option<u64>,
}
#[derive(Clone, PartialEq, Eq)]
pub struct Run {
    policy_instance: u128,
    name: String,
    generation: ProbeGeneration,
    sequence: u64,
}
struct Active {
    run: Run,
    alias: String,
    started_ms: u64,
}
/// One per logical host and configuration generation, not a second supervisor.
pub struct HostPolicy {
    instance: u128,
    name: String,
    generation: ProbeGeneration,
    revision: u64,
    sequence: u64,
    routes: Routes,
    aliases: HashMap<String, Failures>,
    active: Option<Active>,
    recovery: Option<Recovery>,
    wake: Arc<Mutex<WakeBackoff>>,
    wake_progress: Arc<Notify>,
}
#[derive(Debug, PartialEq, Eq)]
pub enum Exit {
    Ignored,
    Deliberate,
    Normal,
    FastFailure,
    Demoted,
}
impl HostPolicy {
    pub fn new(routes: &Routes, generation: ProbeGeneration) -> Self {
        Self {
            instance: rand::random(),
            name: routes.name.clone(),
            generation,
            revision: 0,
            sequence: 0,
            routes: routes.clone(),
            aliases: routes
                .routes
                .iter()
                .map(|r| (r.alias.clone(), Failures::default()))
                .collect(),
            active: None,
            recovery: None,
            wake: Arc::default(),
            wake_progress: Arc::default(),
        }
    }
    pub(crate) fn notify_wakes(&mut self, progress: Arc<Notify>) {
        self.wake_progress = progress;
    }
    pub fn waking(&self) -> bool {
        self.wake.lock().unwrap().active
    }
    /// Native callers retain the trusted probe for an independently bounded wake.
    /// No selection borrow, route deadline or host-global lock spans this task.
    pub fn start_wake<P: RouteProbe + Send + 'static>(&self, probe: Arc<P>) {
        let mut wake = self.wake.lock().unwrap();
        let Some(ip) = wake.pending.take() else {
            return;
        };
        let reservation = WakeAttempt {
            state: self.wake.clone(),
            progress: self.wake_progress.clone(),
            epoch: wake.epoch,
            cooldown: Duration::from_secs((5 << wake.failures.saturating_sub(1).min(4)).min(60)),
        };
        let until = Instant::now() + Duration::from_secs(5);
        wake.handle = Some(tokio::spawn(async move {
            let _reservation = reservation;
            // The native ping retries three times. Even an uncooperative probe
            // cannot outlive this window; dropping it kills its retained child.
            let _ = timeout_at(until, probe.tailscale_wake(&ip, until)).await;
        }));
    }
    pub async fn wake_progress(&self) {
        self.wake_progress.notified().await;
    }
    pub(crate) fn take_wake_completion(&self) -> bool {
        std::mem::take(&mut self.wake.lock().unwrap().completed)
    }
    fn invalidate_wake(&self) {
        let mut wake = self.wake.lock().unwrap();
        wake.epoch = rand::random();
        wake.pending = None;
        wake.completed = false;
        if let Some(handle) = &wake.handle {
            handle.abort();
            if handle.is_finished() {
                wake.active = false;
            }
        } else {
            wake.active = false;
        }
    }
    /// Abort and join before replacing policy inputs or shutting down the owner.
    pub async fn stop_wake(&self) {
        self.invalidate_wake();
        let handle = self.wake.lock().unwrap().handle.take();
        if let Some(handle) = handle {
            let _ = handle.await;
        }
    }
    fn blocked(&self, alias: &str, now: u64) -> bool {
        self.aliases
            .get(alias)
            .and_then(|f| f.blocked_until)
            .is_some_and(|until| now < until)
    }
    /// Preference/probe generation CAS for an unchanged endpoint/credential source.
    /// It invalidates pending choices without resetting counters or an owned run.
    /// A configuration or monitor epoch change requires a new policy after owned stop.
    pub fn update_selection_generation(
        &mut self,
        expected: &ProbeGeneration,
        next: ProbeGeneration,
    ) -> Result<()> {
        if &self.generation != expected
            || next.epoch != expected.epoch
            || next.configuration_binding != expected.configuration_binding
            || (next != *expected && next.generation <= expected.generation)
        {
            return Err("ROUTE_CONFIGURATION_CHANGED");
        }
        if next != *expected {
            self.revision = self
                .revision
                .checked_add(1)
                .ok_or("ROUTE_COUNTER_EXHAUSTED")?;
            self.invalidate_wake();
            self.generation = next;
        }
        Ok(())
    }
    pub fn recovery_due(&self, now: u64) -> bool {
        self.active.is_none() && self.recovery.as_ref().is_none_or(|r| r.due(now))
    }
    pub fn recovery_exhausted(&self) -> bool {
        self.recovery.as_ref().is_some_and(Recovery::exhausted)
    }
    /// Consume only immediately before an explicitly permitted launch attempt.
    pub fn take_recovery_attempt(&mut self, now: u64) -> Result<()> {
        if self.active.is_some() {
            return Err("ROUTE_RUN_ACTIVE");
        }
        if let Some(recovery) = &mut self.recovery {
            recovery.attempt(now)?;
        }
        Ok(())
    }
    /// Call after the launch is bound to a retained process handle. For a failed
    /// launch, use launch_failed only after proving that no child remains alive.
    pub fn started(
        &mut self,
        choice: &RouteChoice,
        current: &ProbeGeneration,
        now: u64,
    ) -> Result<Run> {
        let alias = choice.alias(self, current, now)?.to_owned();
        if self.active.is_some() {
            return Err("ROUTE_RUN_ACTIVE");
        }
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or("ROUTE_COUNTER_EXHAUSTED")?;
        self.revision = self
            .revision
            .checked_add(1)
            .ok_or("ROUTE_COUNTER_EXHAUSTED")?;
        let run = Run {
            policy_instance: self.instance,
            name: self.name.clone(),
            generation: self.generation.clone(),
            sequence: self.sequence,
        };
        self.active = Some(Active {
            run: run.clone(),
            alias,
            started_ms: now,
        });
        Ok(run)
    }
    pub fn launch_failed(
        &mut self,
        choice: &RouteChoice,
        current: &ProbeGeneration,
        now: u64,
    ) -> Result<Exit> {
        let run = self.started(choice, current, now)?;
        self.exited(&run, now, false)
    }
    /// A positive owned-process liveness observation is required from the runtime.
    pub fn observed_alive(&mut self, run: &Run, now: u64) -> Result<bool> {
        let active = self
            .active
            .as_ref()
            .filter(|a| &a.run == run)
            .ok_or("ROUTE_RUN_CHANGED")?;
        let lived = now
            .checked_sub(active.started_ms)
            .ok_or("ROUTE_CLOCK_CHANGED")?;
        if lived >= 60000 && self.recovery.take().is_some() {
            return Ok(true);
        }
        Ok(false)
    }
    /// Exactly one observation per run. A deliberate stop never increments fast
    /// failures or starts recovery; selected/disconnected state belongs to runtime.
    pub fn exited(&mut self, run: &Run, now: u64, deliberate: bool) -> Result<Exit> {
        let Some(active) = self.active.as_ref().filter(|a| &a.run == run) else {
            return Ok(Exit::Ignored);
        };
        let lived = now
            .checked_sub(active.started_ms)
            .ok_or("ROUTE_CLOCK_CHANGED")?;
        self.revision = self
            .revision
            .checked_add(1)
            .ok_or("ROUTE_COUNTER_EXHAUSTED")?;
        let active = self.active.take().unwrap();
        if deliberate {
            return Ok(Exit::Deliberate);
        }
        self.recovery.get_or_insert_with(|| Recovery::new(now));
        let failures = self
            .aliases
            .get_mut(&active.alias)
            .ok_or("ROUTE_CONFIGURATION_CHANGED")?;
        if lived >= 15000 {
            failures.count = 0;
            return Ok(Exit::Normal);
        }
        failures.count += 1;
        if failures.count >= 3 {
            failures.count = 0;
            failures.blocked_until = Some(now.saturating_add(600000));
            return Ok(Exit::Demoted);
        }
        Ok(Exit::FastFailure)
    }
}
impl Drop for HostPolicy {
    fn drop(&mut self) {
        self.invalidate_wake();
    }
}
/// The alias can cross only a native call boundary, after freshness revalidation.
pub struct RouteChoice {
    policy_instance: u128,
    name: String,
    alias: String,
    kind: RouteKind,
    generation: ProbeGeneration,
    policy_revision: u64,
    observed_ms: u64,
}
/// Latest chosen route only: no alias, endpoint or raw peer evidence.
#[derive(Clone)]
pub(crate) struct RouteObservation {
    pub kind: RouteKind,
    pub generation: ProbeGeneration,
    pub confirmed_ms: u64,
}
impl RouteObservation {
    /// Called only after the supervisor proves the retained run is still in use.
    pub(crate) fn confirm_use(&mut self, generation: &ProbeGeneration, now: u64) {
        if self.generation == *generation {
            self.confirmed_ms = now;
        }
    }
}
impl RouteChoice {
    pub(crate) fn observation(&self, confirmed_ms: u64) -> RouteObservation {
        RouteObservation {
            kind: self.kind,
            generation: self.generation.clone(),
            confirmed_ms,
        }
    }

    pub fn kind(&self) -> RouteKind {
        self.kind
    }
    pub fn alias<'a>(
        &'a self,
        policy: &HostPolicy,
        current: &ProbeGeneration,
        now: u64,
    ) -> Result<&'a str> {
        if self.policy_instance != policy.instance
            || self.name != policy.name
            || self.policy_revision != policy.revision
            || policy.generation != *current
            || !probe_current(&self.generation, current, self.observed_ms, now)
        {
            return Err("ROUTE_STALE");
        }
        if !policy.aliases.contains_key(&self.alias) {
            return Err("ROUTE_CONFIGURATION_CHANGED");
        }
        Ok(&self.alias)
    }
}
fn deadline(total: Instant, budget: Duration) -> Instant {
    total.min(Instant::now() + budget)
}
/// Immutable native-only request for a background worker. It grants no launch
/// authority: the result must be checked against the CURRENT policy and generation.
pub struct SelectionRequest {
    routes: Routes,
    policy_instance: u128,
    policy_revision: u64,
    generation: ProbeGeneration,
    observed_ms: u64,
    blocked: Vec<String>,
    wake_epoch: u128,
    wake: Arc<Mutex<WakeBackoff>>,
    wake_progress: Arc<Notify>,
}
impl SelectionRequest {
    pub fn new(
        routes: &Routes,
        policy: &HostPolicy,
        generation: ProbeGeneration,
        observed_ms: u64,
    ) -> Result<Self> {
        if *routes != policy.routes || generation != policy.generation {
            return Err("ROUTE_CONFIGURATION_CHANGED");
        }
        if policy.active.is_some() {
            return Err("ROUTE_RUN_ACTIVE");
        }
        Ok(Self {
            routes: routes.clone(),
            policy_instance: policy.instance,
            policy_revision: policy.revision,
            generation,
            observed_ms,
            wake_epoch: policy.wake.lock().unwrap().epoch,
            wake: policy.wake.clone(),
            wake_progress: policy.wake_progress.clone(),
            blocked: routes
                .routes
                .iter()
                .filter(|r| policy.blocked(&r.alias, observed_ms))
                .map(|r| r.alias.clone())
                .collect(),
        })
    }
    async fn direct<P: RouteProbe>(&self, probe: &P, ip: &str, until: Instant) -> TailscalePeer {
        let peer = timeout_at(until, probe.tailscale_peer(ip, until))
            .await
            .unwrap_or(TailscalePeer::Unknown);
        let mut wake = self.wake.lock().unwrap();
        if wake.epoch != self.wake_epoch {
            return TailscalePeer::Unknown;
        }
        match peer {
            TailscalePeer::Direct => {
                wake.failures = 0;
                wake.next = None;
            }
            TailscalePeer::Relay
                if self
                    .routes
                    .routes
                    .iter()
                    .any(|r| r.kind == RouteKind::Direct && r.allow_tailscale_relay) =>
            {
                wake.failures = 0;
                wake.next = None;
            }
            TailscalePeer::Idle | TailscalePeer::Relay => {
                if !wake.active && !wake.next.is_some_and(|next| Instant::now() < next) {
                    wake.active = true;
                    wake.pending = Some(ip.into());
                    wake.next = Some(
                        Instant::now() + Duration::from_secs((5 << wake.failures.min(4)).min(60)),
                    );
                    wake.failures = wake.failures.saturating_add(1);
                    self.wake_progress.notify_one();
                }
            }
            TailscalePeer::Unknown => (),
        }
        peer
    }
    /// No policy borrow or host-global lock across await. Cancellation drops this
    /// request and its status/TCP resources. Wake intent belongs to the policy;
    /// only its native owner may start the separately bounded background task.
    pub async fn run<P: RouteProbe>(
        self,
        probe: &P,
        budget: Duration,
    ) -> std::result::Result<RouteChoice, RouteFailure> {
        if budget.is_zero() || budget > Duration::from_secs(30) {
            return Err("ROUTE_BUDGET_INVALID".into());
        }
        let total = Instant::now() + budget;
        let direct = self
            .routes
            .routes
            .iter()
            .find(|r| r.kind == RouteKind::Direct);
        let lan = self.routes.routes.iter().find(|r| r.kind == RouteKind::Lan);
        let mut direct_requirement = RouteRequirement::DirectPeerUnknown;
        let mut relayed = false;
        let (direct_ok, lan_ok) = timeout_at(total, async {
            tokio::join!(
                async {
                    let Some(route) = direct else { return false };
                    let endpoint = route.probe.as_ref().unwrap();
                    let peer = self
                        .direct(
                            probe,
                            &endpoint.address,
                            deadline(total, route.probe_budget),
                        )
                        .await;
                    if peer != TailscalePeer::Direct
                        && !(peer == TailscalePeer::Relay && route.allow_tailscale_relay)
                    {
                        direct_requirement =
                            if matches!(peer, TailscalePeer::Idle | TailscalePeer::Relay) {
                                RouteRequirement::DirectNotConfirmed
                            } else {
                                RouteRequirement::DirectPeerUnknown
                            };
                        return false;
                    }
                    relayed = peer == TailscalePeer::Relay;
                    direct_requirement = RouteRequirement::DirectTcpUnreachable;
                    probe
                        .tcp(endpoint, deadline(total, route.probe_budget))
                        .await
                },
                async {
                    let Some(route) = lan else { return false };
                    probe
                        .tcp(
                            route.probe.as_ref().unwrap(),
                            deadline(total, route.probe_budget),
                        )
                        .await
                }
            )
        })
        .await
        .map_err(|_| "ROUTE_PROBE_TIMEOUT")?;
        if Instant::now() >= total {
            return Err("ROUTE_PROBE_TIMEOUT".into());
        }
        let mut eligible: Vec<_> = self
            .routes
            .routes
            .iter()
            .filter(|r| match r.kind {
                RouteKind::Direct => direct_ok && !(self.routes.prefer_lan && relayed && lan_ok),
                RouteKind::Lan => lan_ok,
                RouteKind::Cf | RouteKind::Configured => true,
            })
            .collect();
        eligible.sort_by_key(|r| match r.kind {
            RouteKind::Lan => {
                if self.routes.prefer_lan {
                    0
                } else {
                    1
                }
            }
            RouteKind::Direct => {
                if self.routes.prefer_lan {
                    1
                } else {
                    0
                }
            }
            RouteKind::Cf | RouteKind::Configured => 2,
        });
        let chosen = eligible
            .iter()
            .find(|r| !self.blocked.contains(&r.alias))
            .or_else(|| eligible.first())
            .ok_or_else(|| RouteFailure {
                code: if self.wake.lock().unwrap().active {
                    "TAILSCALE_WAKING"
                } else {
                    "ROUTE_UNAVAILABLE"
                },
                route_requirement: Some(if self.routes.routes.len() == 1 && direct.is_some() {
                    direct_requirement
                } else {
                    RouteRequirement::ConfiguredRoutesExhausted
                }),
            })?;
        Ok(RouteChoice {
            policy_instance: self.policy_instance,
            name: self.routes.name.clone(),
            alias: chosen.alias.clone(),
            kind: chosen.kind,
            generation: self.generation,
            policy_revision: self.policy_revision,
            observed_ms: self.observed_ms,
        })
    }
}
/// Convenience wrapper for callers that do not need an owned selection request.
/// TAILSCALE_WAKING reserves a separate wake: the native owner must call
/// HostPolicy::start_wake with its retained probe, then reselect on wake_progress.
pub async fn select<P: RouteProbe>(
    routes: &Routes,
    policy: &HostPolicy,
    probe: &P,
    generation: ProbeGeneration,
    observed_ms: u64,
    budget: Duration,
) -> Result<RouteChoice> {
    SelectionRequest::new(routes, policy, generation, observed_ms)?
        .run(probe, budget)
        .await
        .map_err(|failure| failure.code)
}
