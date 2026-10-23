use bat_fleet_core::{
    inventory::{Endpoint, Inventory},
    ownership::ProbeGeneration,
    route::*,
};
use serde_json::{json, Value};
use std::{sync::Mutex, time::Duration};
use tokio::time::Instant;
fn generation() -> ProbeGeneration {
    ProbeGeneration {
        epoch: "fixture-epoch".into(),
        configuration_binding: "fixture-binding".into(),
        selection_revision: "fixture-revision".into(),
        generation: 1,
    }
}
#[test]
fn route_requirement_wire_codes_reject_non_strings() {
    for value in [
        json!({"DIRECT_PEER_UNKNOWN": null}),
        json!(["DIRECT_PEER_UNKNOWN"]),
        Value::Null,
        json!(false),
        json!("UNKNOWN_REQUIREMENT"),
    ] {
        assert!(serde_json::from_value::<RouteRequirement>(value).is_err());
    }
}
fn routes(kinds: &[&str]) -> Routes {
    let mut doc: Value = serde_json::from_slice(include_bytes!("fixtures/inventory.json")).unwrap();
    doc["hosts"][0]["routes"] =
        json!(kinds.iter().map(|kind|match *kind{
        "direct"=>json!({"kind":"direct","alias":"fixture-direct","probe":"100.64.0.1:22"}),
        "lan"=>json!({"kind":"lan","alias":"fixture-lan","probe":"192.0.2.1:22"}),
        _=>json!({"kind":"cf","alias":"fixture-cf","access_host":"node-1.example.invalid"})
    }).collect::<Vec<_>>());
    doc["hosts"][0]["provision_route"] = json!(kinds[0]);
    Routes::from_inventory(
        &Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap(),
        "node-1",
    )
    .unwrap()
}
struct Mock {
    direct: bool,
    peer: Option<TailscalePeer>,
    direct_tcp: bool,
    lan: bool,
    calls: Mutex<Vec<&'static str>>,
    hang: bool,
}
impl Mock {
    fn new(direct: bool, direct_tcp: bool, lan: bool) -> Self {
        Self {
            direct,
            peer: None,
            direct_tcp,
            lan,
            calls: Mutex::new(Vec::new()),
            hang: false,
        }
    }
}
impl RouteProbe for Mock {
    fn tailscale_peer<'a>(&'a self, ip: &'a str, deadline: Instant) -> PeerFuture<'a> {
        Box::pin(async move {
            if let Some(peer) = self.peer {
                self.calls.lock().unwrap().push("tailscale");
                peer
            } else if self.tailscale_direct(ip, deadline).await {
                TailscalePeer::Direct
            } else {
                TailscalePeer::Unknown
            }
        })
    }
    fn tailscale_direct<'a>(&'a self, _: &'a str, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            self.calls.lock().unwrap().push("tailscale");
            if self.hang {
                std::future::pending().await
            } else {
                self.direct
            }
        })
    }
    fn tcp<'a>(&'a self, ep: &'a Endpoint, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            if ep.address.starts_with("100.") {
                self.calls.lock().unwrap().push("direct_tcp");
                self.direct_tcp
            } else {
                self.calls.lock().unwrap().push("lan_tcp");
                self.lan
            }
        })
    }
}
async fn choice(routes: &Routes, policy: &HostPolicy, probe: &Mock, now: u64) -> RouteChoice {
    select(
        routes,
        policy,
        probe,
        generation(),
        now,
        Duration::from_secs(1),
    )
    .await
    .ok()
    .unwrap()
}
#[test]
fn tailscale_requires_online_curaddr_exact_unique_peer_not_derp_or_tcp_speed() {
    let valid = json!({"Peer":{"id":{"TailscaleIPs":["100.64.0.1"],"Online":true,"CurAddr":"203.0.113.1:1234","Relay":"fixture-derp"}}});
    assert!(tailscale_direct(
        &serde_json::to_vec(&valid).unwrap(),
        "100.64.0.1"
    ));
    for field in [
        "offline",
        "unknown_online",
        "derp",
        "numeric_addr",
        "spaces",
        "wrong_ip",
        "duplicates",
        "missing",
    ] {
        let mut v = valid.clone();
        match field {
            "offline" => v["Peer"]["id"]["Online"] = json!(false),
            "unknown_online" => v["Peer"]["id"]["Online"] = json!("true"),
            "derp" => v["Peer"]["id"]["CurAddr"] = json!(""),
            "numeric_addr" => v["Peer"]["id"]["CurAddr"] = json!(123),
            "spaces" => v["Peer"]["id"]["CurAddr"] = json!(" "),
            "wrong_ip" => v["Peer"]["id"]["TailscaleIPs"] = json!(["100.64.0.2"]),
            "duplicates" => v["Peer"]["other"] = v["Peer"]["id"].clone(),
            _ => v["Peer"] = Value::Null,
        };
        assert!(
            !tailscale_direct(&serde_json::to_vec(&v).unwrap(), "100.64.0.1"),
            "{field}"
        );
    }
    for raw in [
        b"{\"Peer\":{},\"peer\":{}}".as_slice(),
        b"secret malformed JSON",
        b"null",
        &[0xff],
    ] {
        assert!(!tailscale_direct(raw, "100.64.0.1"));
    }
    assert!(!tailscale_direct(
        &vec![b' '; 1024 * 1024 + 1],
        "100.64.0.1"
    ));
}
#[tokio::test]
async fn ordered_eligibility_and_short_circuit_never_infer_direct_from_tcp() {
    for (ts, tcp, lan, expected) in [
        (true, true, true, RouteKind::Direct),
        (true, false, true, RouteKind::Lan),
        (false, true, true, RouteKind::Lan),
        (false, true, false, RouteKind::Cf),
    ] {
        let routes = routes(&["direct", "lan", "cf"]);
        let policy = HostPolicy::new(&routes, generation());
        let probe = Mock::new(ts, tcp, lan);
        let selected = choice(&routes, &policy, &probe, 1000).await;
        assert_eq!(selected.kind(), expected);
        if !ts {
            assert!(!probe.calls.lock().unwrap().contains(&"direct_tcp"));
        }
        assert!(selected
            .alias(&policy, &generation(), 1000)
            .unwrap()
            .starts_with("fixture-"));
    }
}
#[tokio::test]
async fn direct_only_and_no_cf_have_no_legacy_unproven_default() {
    for kinds in [&["direct"][..], &["direct", "lan"][..]] {
        let routes = routes(kinds);
        let policy = HostPolicy::new(&routes, generation());
        let probe = Mock::new(false, true, false);
        assert!(matches!(
            select(
                &routes,
                &policy,
                &probe,
                generation(),
                0,
                Duration::from_secs(1)
            )
            .await,
            Err("ROUTE_UNAVAILABLE")
        ));
        assert!(!probe.calls.lock().unwrap().contains(&"direct_tcp"));
    }
    let routes = routes(&["lan"]);
    let policy = HostPolicy::new(&routes, generation());
    assert_eq!(
        choice(&routes, &policy, &Mock::new(false, false, true), 0)
            .await
            .kind(),
        RouteKind::Lan
    );
}
#[tokio::test]
async fn direct_requirement_explains_unknown_evidence_without_extra_attempts() {
    let routes = routes(&["direct"]);
    let policy = HostPolicy::new(&routes, generation());
    let probe = Mock::new(false, true, true);
    let failure = SelectionRequest::new(&routes, &policy, generation(), 1000)
        .unwrap()
        .run(&probe, Duration::from_secs(1))
        .await
        .err()
        .unwrap();
    assert_eq!(*probe.calls.lock().unwrap(), vec!["tailscale"]);
    assert_eq!(
        serde_json::to_value(failure).unwrap(),
        json!({
            "code": "ROUTE_UNAVAILABLE", "route_requirement": "DIRECT_PEER_UNKNOWN"
        })
    );
}
#[tokio::test]
async fn route_requirements_keep_missing_idle_unknown_tcp_and_exhaustion_distinct() {
    for (peer, kinds, expected, code, calls) in [
        (
            TailscalePeer::Unknown,
            vec!["direct"],
            "DIRECT_PEER_UNKNOWN",
            "ROUTE_UNAVAILABLE",
            vec!["tailscale"],
        ),
        (
            TailscalePeer::Idle,
            vec!["direct"],
            "DIRECT_NOT_CONFIRMED",
            "TAILSCALE_WAKING",
            vec!["tailscale"],
        ),
        (
            TailscalePeer::Direct,
            vec!["direct"],
            "DIRECT_TCP_UNREACHABLE",
            "ROUTE_UNAVAILABLE",
            vec!["tailscale", "direct_tcp"],
        ),
        (
            TailscalePeer::Unknown,
            vec!["direct", "lan"],
            "CONFIGURED_ROUTES_EXHAUSTED",
            "ROUTE_UNAVAILABLE",
            vec!["tailscale", "lan_tcp"],
        ),
    ] {
        let routes = routes(&kinds);
        let policy = HostPolicy::new(&routes, generation());
        let mut probe = Mock::new(false, false, false);
        probe.peer = Some(peer);
        let failure = SelectionRequest::new(&routes, &policy, generation(), 1000)
            .unwrap()
            .run(&probe, Duration::from_secs(1))
            .await
            .err()
            .unwrap();
        assert_eq!(
            serde_json::to_value(failure).unwrap(),
            json!({"code":code,"route_requirement":expected})
        );
        assert_eq!(*probe.calls.lock().unwrap(), calls);
    }
}
async fn fail(
    routes: &Routes,
    policy: &mut HostPolicy,
    probe: &Mock,
    start: u64,
    lived: u64,
    deliberate: bool,
) -> (Run, Exit) {
    let selected = choice(routes, policy, probe, start).await;
    let run = policy.started(&selected, &generation(), start).unwrap();
    let result = policy.exited(&run, start + lived, deliberate).unwrap();
    (run, result)
}
#[tokio::test]
async fn three_fast_failures_demote_for_ten_minutes_and_duplicate_exits_do_not_count() {
    let routes = routes(&["direct", "lan", "cf"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let probe = Mock::new(true, true, true);
    for index in 0..3 {
        let (run, result) = fail(&routes, &mut policy, &probe, index * 100, 99, false).await;
        assert_eq!(
            result,
            if index == 2 {
                Exit::Demoted
            } else {
                Exit::FastFailure
            }
        );
        assert_eq!(
            policy.exited(&run, index * 100 + 99, false).unwrap(),
            Exit::Ignored
        );
    }
    assert_eq!(
        choice(&routes, &policy, &probe, 300).await.kind(),
        RouteKind::Lan
    );
    assert_eq!(
        choice(&routes, &policy, &probe, 600298).await.kind(),
        RouteKind::Lan
    );
    assert_eq!(
        choice(&routes, &policy, &probe, 600299).await.kind(),
        RouteKind::Direct
    );
}
#[tokio::test]
async fn demotion_fallback_checks_eligibility_and_all_blocked_keep_best_eligible() {
    let routes = routes(&["direct", "lan", "cf"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let cf = Mock::new(false, true, false);
    for n in 0..3 {
        fail(&routes, &mut policy, &cf, n * 10, 1, false).await;
    }
    // CF is demoted; direct TCP could be fast, but Tailscale says DERP. It stays CF.
    assert_eq!(
        choice(&routes, &policy, &cf, 100).await.kind(),
        RouteKind::Cf
    );
    let both = Mock::new(true, true, true);
    for n in 0..3 {
        fail(&routes, &mut policy, &both, 100 + n * 10, 1, false).await;
    }
    assert_eq!(
        choice(&routes, &policy, &both, 200).await.kind(),
        RouteKind::Lan
    );
    for n in 0..3 {
        fail(&routes, &mut policy, &both, 200 + n * 10, 1, false).await;
    }
    assert_eq!(
        choice(&routes, &policy, &both, 300).await.kind(),
        RouteKind::Direct
    );
}
#[tokio::test]
async fn fifteen_second_run_resets_fast_failure_and_deliberate_stop_does_not_count() {
    let routes = routes(&["direct", "cf"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let probe = Mock::new(true, true, false);
    fail(&routes, &mut policy, &probe, 0, 1, false).await;
    fail(&routes, &mut policy, &probe, 10, 1, false).await;
    assert_eq!(
        fail(&routes, &mut policy, &probe, 20, 0, true).await.1,
        Exit::Deliberate
    );
    assert_eq!(
        fail(&routes, &mut policy, &probe, 30, 15000, false).await.1,
        Exit::Normal
    );
    assert_eq!(
        fail(&routes, &mut policy, &probe, 15040, 14999, false)
            .await
            .1,
        Exit::FastFailure
    );
    assert_eq!(
        choice(&routes, &policy, &probe, 40000).await.kind(),
        RouteKind::Direct
    );
    let mut fresh = HostPolicy::new(&routes, generation());
    fail(&routes, &mut fresh, &probe, 0, 1, true).await;
    assert!(fresh.recovery_due(1));
}
#[tokio::test]
async fn existing_bounded_recovery_is_reset_only_by_positive_sixty_second_liveness() {
    let routes = routes(&["cf"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let probe = Mock::new(false, false, false);
    fail(&routes, &mut policy, &probe, 0, 1, false).await;
    assert!(!policy.recovery_due(5000));
    assert!(policy.recovery_due(5001));
    policy.take_recovery_attempt(5001).unwrap();
    assert!(!policy.recovery_due(20000));
    policy.take_recovery_attempt(20001).unwrap();
    assert!(!policy.recovery_due(65000));
    policy.take_recovery_attempt(65001).unwrap();
    assert!(policy.recovery_exhausted());
    assert!(policy.take_recovery_attempt(110001).is_err());
    let selected = choice(&routes, &policy, &probe, 65001).await;
    let run = policy.started(&selected, &generation(), 65001).unwrap();
    assert!(!policy.observed_alive(&run, 125000).unwrap());
    assert!(policy.recovery_exhausted());
    assert!(policy.observed_alive(&run, 125001).unwrap());
    assert!(!policy.recovery_exhausted());
    assert_eq!(policy.exited(&run, 125002, false).unwrap(), Exit::Normal);
    assert!(!policy.recovery_due(130001));
    assert!(policy.recovery_due(130002));
}
#[tokio::test]
async fn selection_binds_generation_policy_and_age_and_wrong_run_cannot_change_host() {
    let routes = routes(&["direct", "cf"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let probe = Mock::new(true, true, false);
    let selected = choice(&routes, &policy, &probe, 100).await;
    for now in [99, 60101] {
        assert!(matches!(
            selected.alias(&policy, &generation(), now),
            Err("ROUTE_STALE")
        ));
    }
    assert!(selected.alias(&policy, &generation(), 60100).is_ok());
    for field in 0..4 {
        let mut changed = generation();
        match field {
            0 => changed.epoch.push('x'),
            1 => changed.configuration_binding.push('x'),
            2 => changed.selection_revision.push('x'),
            _ => changed.generation += 1,
        };
        assert!(selected.alias(&policy, &changed, 100).is_err());
    }
    let run = policy.started(&selected, &generation(), 100).unwrap();
    assert!(selected.alias(&policy, &generation(), 100).is_err());
    assert!(policy.exited(&run, 99, false).is_err());
    assert!(policy.observed_alive(&run, 99).is_err());
    let other = routes_for_other();
    let mut other_policy = HostPolicy::new(&other, generation());
    assert_eq!(
        other_policy.exited(&run, 100, false).unwrap(),
        Exit::Ignored
    );
}
fn routes_for_other() -> Routes {
    Routes::from_inventory(
        &Inventory::parse(include_bytes!("fixtures/inventory.json")).unwrap(),
        "node-2",
    )
    .unwrap()
}
#[tokio::test]
async fn total_deadline_and_independent_host_futures_do_not_wait_on_blocked_peer() {
    let routes = routes(&["direct", "lan", "cf"]);
    let policy = HostPolicy::new(&routes, generation());
    let mut slow = Mock::new(false, false, false);
    slow.hang = true;
    let fast = Mock::new(true, true, true);
    let pending = select(
        &routes,
        &policy,
        &slow,
        generation(),
        0,
        Duration::from_millis(150),
    );
    tokio::pin!(pending);
    let quick = select(
        &routes,
        &policy,
        &fast,
        generation(),
        0,
        Duration::from_secs(1),
    );
    tokio::select! {result=quick=>assert_eq!(result.ok().unwrap().kind(),RouteKind::Direct),_= &mut pending=>panic!("blocked peer must not serialize the fast host")}
    assert!(matches!(pending.await, Err("ROUTE_PROBE_TIMEOUT")));
    assert!(slow.calls.lock().unwrap().contains(&"lan_tcp"));
}
#[tokio::test]
async fn connector_alias_is_fixed_and_local_connector_does_not_invent_tunnel() {
    let mut doc: Value = serde_json::from_slice(include_bytes!("fixtures/inventory.json")).unwrap();
    let inv = Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap();
    let routes = Routes::from_inventory(&inv, "connector").unwrap();
    let policy = HostPolicy::new(&routes, generation());
    assert!(matches!(
        select(
            &routes,
            &policy,
            &Mock::new(true, true, true),
            generation(),
            0,
            Duration::from_secs(1)
        )
        .await,
        Err("ROUTE_UNAVAILABLE")
    ));
    doc["connector"]["ssh_alias"] = json!("fixture-connector");
    let inv = Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap();
    let routes = Routes::from_inventory(&inv, "connector").unwrap();
    let policy = HostPolicy::new(&routes, generation());
    let selected = choice(&routes, &policy, &Mock::new(false, false, false), 0).await;
    assert_eq!(selected.kind(), RouteKind::Configured);
    assert_eq!(
        selected.alias(&policy, &generation(), 0).unwrap(),
        "fixture-connector"
    );
}

#[tokio::test]
async fn recreated_policy_does_not_reuse_a_choice_or_run_from_same_generation() {
    let routes = routes(&["cf"]);
    let mut old = HostPolicy::new(&routes, generation());
    let probe = Mock::new(false, false, false);
    let old_choice = choice(&routes, &old, &probe, 0).await;
    let old_run = old.started(&old_choice, &generation(), 0).unwrap();
    let mut new = HostPolicy::new(&routes, generation());
    assert!(old_choice.alias(&new, &generation(), 0).is_err());
    let new_choice = choice(&routes, &new, &probe, 0).await;
    let new_run = new.started(&new_choice, &generation(), 0).unwrap();
    assert_eq!(new.exited(&old_run, 1, false).unwrap(), Exit::Ignored);
    assert_eq!(new.exited(&new_run, 1, false).unwrap(), Exit::FastFailure);
}

#[tokio::test]
async fn owned_selection_worker_cannot_publish_after_preference_generation_changes() {
    let routes = routes(&["direct", "cf"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let request = SelectionRequest::new(&routes, &policy, generation(), 100).unwrap();
    // The worker owns immutable input and does not hold a borrow of the supervisor.
    let worker = tokio::spawn(async move {
        request
            .run(&Mock::new(true, true, false), Duration::from_secs(1))
            .await
    });
    let mut next = generation();
    next.generation += 1;
    next.selection_revision.push('2');
    policy
        .update_selection_generation(&generation(), next.clone())
        .unwrap();
    let old = worker.await.unwrap().ok().unwrap();
    assert!(matches!(old.alias(&policy, &next, 100), Err("ROUTE_STALE")));
    let current = SelectionRequest::new(&routes, &policy, next.clone(), 100)
        .unwrap()
        .run(&Mock::new(true, true, false), Duration::from_secs(1))
        .await
        .ok()
        .unwrap();
    assert_eq!(
        current.alias(&policy, &next, 100).unwrap(),
        "fixture-direct"
    );
    // A stale writer, epoch/source change, or non-monotonic counter cannot advance it.
    assert!(policy
        .update_selection_generation(&generation(), next.clone())
        .is_err());
    for field in 0..4 {
        let mut changed = next.clone();
        changed.generation += 1;
        match field {
            0 => changed.epoch.push('x'),
            1 => changed.configuration_binding.push('x'),
            2 => changed.generation = next.generation - 1,
            _ => {
                changed.generation = next.generation;
                changed.selection_revision.push('x');
            }
        }
        assert!(policy.update_selection_generation(&next, changed).is_err());
    }
    policy
        .update_selection_generation(&next, next.clone())
        .unwrap();
    assert!(current.alias(&policy, &next, 100).is_ok());
}

#[tokio::test]
async fn preference_update_preserves_owned_run_and_exhausted_recovery_until_stable() {
    let routes = routes(&["cf"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let probe = Mock::new(false, false, false);
    fail(&routes, &mut policy, &probe, 0, 1, false).await;
    for now in [5001, 20001, 65001] {
        policy.take_recovery_attempt(now).unwrap();
    }
    let selected = choice(&routes, &policy, &probe, 65001).await;
    let run = policy.started(&selected, &generation(), 65001).unwrap();
    assert!(!policy.recovery_due(200000));
    assert!(matches!(
        SelectionRequest::new(&routes, &policy, generation(), 65001),
        Err("ROUTE_RUN_ACTIVE")
    ));
    let mut next = generation();
    next.generation += 1;
    next.selection_revision.push('x');
    policy
        .update_selection_generation(&generation(), next.clone())
        .unwrap();
    assert!(policy.recovery_exhausted());
    assert!(!policy.observed_alive(&run, 125000).unwrap());
    assert!(policy.observed_alive(&run, 125001).unwrap());
    assert!(!policy.recovery_exhausted());
    assert_eq!(policy.exited(&run, 125002, false).unwrap(), Exit::Normal);
    assert!(!policy.recovery_due(130001));
    assert!(policy.recovery_due(130002));
    assert!(SelectionRequest::new(&routes, &policy, next, 130002).is_ok());
}

#[tokio::test]
async fn preference_update_preserves_fast_failure_counts_and_alias_demotion() {
    let routes = routes(&["direct", "cf"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let probe = Mock::new(true, true, false);
    for now in [0, 10] {
        fail(&routes, &mut policy, &probe, now, 1, false).await;
    }
    let mut next = generation();
    next.generation += 1;
    next.selection_revision.push('x');
    policy
        .update_selection_generation(&generation(), next.clone())
        .unwrap();
    let selected = SelectionRequest::new(&routes, &policy, next.clone(), 20)
        .unwrap()
        .run(&probe, Duration::from_secs(1))
        .await
        .ok()
        .unwrap();
    let run = policy.started(&selected, &next, 20).unwrap();
    assert_eq!(policy.exited(&run, 21, false).unwrap(), Exit::Demoted);
    let mut later = next.clone();
    later.generation += 1;
    policy
        .update_selection_generation(&next, later.clone())
        .unwrap();
    let selected = SelectionRequest::new(&routes, &policy, later.clone(), 22)
        .unwrap()
        .run(&probe, Duration::from_secs(1))
        .await
        .ok()
        .unwrap();
    assert_eq!(selected.kind(), RouteKind::Cf);
    assert!(selected.alias(&policy, &later, 22).is_ok());
    // Keeping aliases/name while changing an endpoint does not preserve source identity.
    let mut doc: Value = serde_json::from_slice(include_bytes!("fixtures/inventory.json")).unwrap();
    doc["hosts"][0]["routes"] = json!([
        {"kind":"direct","alias":"fixture-direct","probe":"100.64.0.1:23"},
        {"kind":"cf","alias":"fixture-cf","access_host":"node-1.example.invalid"}
    ]);
    doc["hosts"][0]["probe"] = json!("100.64.0.1:23");
    let changed = Routes::from_inventory(
        &Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap(),
        "node-1",
    )
    .unwrap();
    assert!(matches!(
        SelectionRequest::new(&changed, &policy, later, 22),
        Err("ROUTE_CONFIGURATION_CHANGED")
    ));
}

struct WakeProbe {
    before: Value,
    after: Value,
    pings: std::sync::Arc<Mutex<Vec<(String, Instant)>>>,
    ready: std::sync::atomic::AtomicBool,
    active: std::sync::Arc<std::sync::atomic::AtomicUsize>,
    delay: Duration,
    hang: bool,
}
struct RunningWake(std::sync::Arc<std::sync::atomic::AtomicUsize>);
impl Drop for RunningWake {
    fn drop(&mut self) {
        self.0.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
    }
}
impl WakeProbe {
    fn idle(recovers: bool) -> Self {
        let before = json!({"Peer":{"id":{"TailscaleIPs":["100.64.0.1"],"HostName":"old-name","DNSName":"renamed.example.invalid","Online":true,"CurAddr":"","Relay":"fixture"}}});
        let mut after = before.clone();
        if recovers {
            after["Peer"]["id"]["CurAddr"] = json!("203.0.113.1:22");
        }
        Self {
            before,
            after,
            pings: Default::default(),
            ready: false.into(),
            active: Default::default(),
            delay: Duration::ZERO,
            hang: false,
        }
    }
    fn count(&self) -> usize {
        self.pings.lock().unwrap().len()
    }
}
impl RouteProbe for WakeProbe {
    fn tailscale_direct<'a>(&'a self, _: &'a str, _: Instant) -> ProbeFuture<'a> {
        panic!("selection must use peer identity evidence")
    }
    fn tailscale_peer<'a>(&'a self, ip: &'a str, _: Instant) -> PeerFuture<'a> {
        Box::pin(async move {
            let doc = if self.ready.load(std::sync::atomic::Ordering::SeqCst) {
                &self.after
            } else {
                &self.before
            };
            tailscale_peer(&serde_json::to_vec(doc).unwrap(), ip)
        })
    }
    fn tailscale_wake<'a>(&'a self, ip: &'a str, deadline: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            self.pings.lock().unwrap().push((ip.into(), deadline));
            self.active
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let _running = RunningWake(self.active.clone());
            if self.hang {
                std::future::pending::<()>().await;
            }
            tokio::time::sleep(self.delay).await;
            self.ready.store(true, std::sync::atomic::Ordering::SeqCst);
            true
        })
    }
    fn tcp<'a>(&'a self, _: &'a Endpoint, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async { true })
    }
}
async fn wake_select(
    routes: &Routes,
    policy: &HostPolicy,
    probe: &std::sync::Arc<WakeProbe>,
    budget: Duration,
) -> Result<RouteChoice, &'static str> {
    let result = select(routes, policy, probe.as_ref(), generation(), 0, budget).await;
    policy.start_wake(probe.clone());
    result
}
async fn finish_wake(policy: &HostPolicy) {
    while policy.waking() {
        policy.wake_progress().await;
    }
}
#[tokio::test(start_paused = true)]
async fn six_hundred_ms_probe_does_not_cancel_fifteen_hundred_ms_ip_wake() {
    // The inventory fixture sets direct_probe_ms=600, just like a small deployment budget.
    let routes = routes(&["direct"]);
    let policy = HostPolicy::new(&routes, generation());
    let mut probe = WakeProbe::idle(true);
    probe.delay = Duration::from_millis(1500);
    let probe = std::sync::Arc::new(probe);
    let start = Instant::now();
    assert!(matches!(
        wake_select(&routes, &policy, &probe, Duration::from_secs(2)).await,
        Err("TAILSCALE_WAKING")
    ));
    assert!(Instant::now() - start < Duration::from_millis(600));
    assert!(policy.waking());
    tokio::time::sleep(Duration::from_millis(650)).await;
    assert_eq!(probe.active.load(std::sync::atomic::Ordering::SeqCst), 1);
    assert!(matches!(
        wake_select(&routes, &policy, &probe, Duration::from_secs(2)).await,
        Err("TAILSCALE_WAKING")
    ));
    finish_wake(&policy).await;
    assert!(Instant::now() - start >= Duration::from_millis(1500));
    let choice = wake_select(&routes, &policy, &probe, Duration::from_secs(2))
        .await
        .ok()
        .unwrap();
    assert_eq!(choice.kind(), RouteKind::Direct);
    assert_eq!(probe.count(), 1);
    assert_eq!(probe.pings.lock().unwrap()[0].0, "100.64.0.1");
    assert!(!policy.waking());
}
#[tokio::test(start_paused = true)]
async fn failed_wakes_are_rate_limited_across_cycles_with_capped_backoff() {
    let routes = routes(&["direct"]);
    let policy = HostPolicy::new(&routes, generation());
    let probe = std::sync::Arc::new(WakeProbe::idle(false));
    for (cycle, delay) in [5, 10, 20, 40, 60, 60].into_iter().enumerate() {
        assert!(matches!(
            wake_select(&routes, &policy, &probe, Duration::from_secs(2)).await,
            Err("TAILSCALE_WAKING")
        ));
        finish_wake(&policy).await;
        let finished = Instant::now();
        assert_eq!(probe.count(), cycle + 1);
        // Ping success without fresh direct evidence remains unavailable.
        assert!(matches!(
            wake_select(&routes, &policy, &probe, Duration::from_secs(2)).await,
            Err("ROUTE_UNAVAILABLE")
        ));
        tokio::time::sleep_until(finished + Duration::from_secs(delay) - Duration::from_millis(1))
            .await;
        assert!(
            wake_select(&routes, &policy, &probe, Duration::from_secs(2))
                .await
                .is_err()
        );
        assert_eq!(probe.count(), cycle + 1);
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
}
#[tokio::test(start_paused = true)]
async fn wake_has_its_own_five_second_deadline_independent_of_selection_budget() {
    for budget in [Duration::from_millis(40), Duration::from_secs(2)] {
        let routes = routes(&["direct"]);
        let policy = HostPolicy::new(&routes, generation());
        let mut probe = WakeProbe::idle(false);
        probe.hang = true;
        let probe = std::sync::Arc::new(probe);
        let start = Instant::now();
        assert!(matches!(
            wake_select(&routes, &policy, &probe, budget).await,
            Err("TAILSCALE_WAKING")
        ));
        assert!(Instant::now() - start < budget);
        tokio::time::sleep(Duration::from_millis(650)).await;
        assert!(policy.waking());
        finish_wake(&policy).await;
        assert_eq!(Instant::now() - start, Duration::from_secs(5));
        assert_eq!(probe.count(), 1);
        assert_eq!(probe.active.load(std::sync::atomic::Ordering::SeqCst), 0);
        assert_eq!(
            probe.pings.lock().unwrap()[0].1,
            start + Duration::from_secs(5)
        );
        assert!(matches!(
            wake_select(&routes, &policy, &probe, budget).await,
            Err("ROUTE_UNAVAILABLE")
        ));
    }
}
#[tokio::test(start_paused = true)]
async fn unknown_ambiguous_malformed_and_non_ip_peers_never_get_pinged() {
    let routes = routes(&["direct"]);
    for kind in ["unknown", "ambiguous", "malformed"] {
        let policy = HostPolicy::new(&routes, generation());
        let mut probe = WakeProbe::idle(true);
        match kind {
            "unknown" => probe.before["Peer"]["id"]["TailscaleIPs"] = json!(["100.64.0.2"]),
            "ambiguous" => probe.before["Peer"]["other"] = probe.before["Peer"]["id"].clone(),
            _ => probe.before = Value::Null,
        }
        let probe = std::sync::Arc::new(probe);
        assert!(matches!(
            wake_select(&routes, &policy, &probe, Duration::from_secs(2)).await,
            Err("ROUTE_UNAVAILABLE")
        ));
        assert_eq!(probe.count(), 0);
    }
    for ip in ["renamed.example.invalid", "old-name"] {
        let doc = json!({"Peer":{"id":{"TailscaleIPs":[ip],"Online":true,"CurAddr":""}}});
        assert_eq!(
            tailscale_peer(&serde_json::to_vec(&doc).unwrap(), ip),
            TailscalePeer::Unknown
        );
    }
    let ip = "fd7a:115c:a1e0::1";
    let doc = json!({"Peer":{"id":{"TailscaleIPs":[ip],"Online":true,"CurAddr":""}}});
    assert_eq!(
        tailscale_peer(&serde_json::to_vec(&doc).unwrap(), ip),
        TailscalePeer::Idle
    );
}
#[tokio::test(start_paused = true)]
async fn generation_change_cancels_owned_wake_and_rejects_stale_selection_intents() {
    let routes = routes(&["direct"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let mut probe = WakeProbe::idle(false);
    probe.hang = true;
    let probe = std::sync::Arc::new(probe);
    let stale = SelectionRequest::new(&routes, &policy, generation(), 0).unwrap();
    assert!(
        wake_select(&routes, &policy, &probe, Duration::from_secs(2))
            .await
            .is_err()
    );
    tokio::task::yield_now().await;
    assert_eq!(probe.active.load(std::sync::atomic::Ordering::SeqCst), 1);
    let mut next = generation();
    next.generation += 1;
    policy
        .update_selection_generation(&generation(), next.clone())
        .unwrap();
    tokio::task::yield_now().await;
    assert!(!policy.waking());
    assert_eq!(probe.active.load(std::sync::atomic::Ordering::SeqCst), 0);
    assert!(matches!(
        stale
            .run(probe.as_ref(), Duration::from_secs(2))
            .await
            .map_err(|failure| failure.code),
        Err("ROUTE_UNAVAILABLE")
    ));
    assert!(select(
        &routes,
        &policy,
        probe.as_ref(),
        next,
        0,
        Duration::from_secs(2)
    )
    .await
    .is_err());
    policy.start_wake(probe.clone());
    assert_eq!(probe.count(), 1, "cancellation retains cooldown");
}
#[tokio::test(start_paused = true)]
async fn owned_wake_does_not_block_another_host_and_stop_or_policy_drop_cancels_it() {
    let routes = routes(&["direct"]);
    let policy = HostPolicy::new(&routes, generation());
    let mut probe = WakeProbe::idle(false);
    probe.hang = true;
    let probe = std::sync::Arc::new(probe);
    assert!(
        wake_select(&routes, &policy, &probe, Duration::from_secs(2))
            .await
            .is_err()
    );
    tokio::task::yield_now().await;
    let other = HostPolicy::new(&routes, generation());
    let fast = std::sync::Arc::new(WakeProbe::idle(true));
    assert!(wake_select(&routes, &other, &fast, Duration::from_secs(2))
        .await
        .is_err());
    finish_wake(&other).await;
    assert!(wake_select(&routes, &other, &fast, Duration::from_secs(2))
        .await
        .is_ok());
    assert!(policy.waking());
    assert!(
        wake_select(&routes, &policy, &probe, Duration::from_secs(2))
            .await
            .is_err()
    );
    assert_eq!(probe.count(), 1);
    policy.stop_wake().await;
    assert_eq!(probe.active.load(std::sync::atomic::Ordering::SeqCst), 0);
    assert!(!policy.waking());
    let dropped = HostPolicy::new(&routes, generation());
    assert!(
        wake_select(&routes, &dropped, &probe, Duration::from_secs(2))
            .await
            .is_err()
    );
    tokio::task::yield_now().await;
    assert_eq!(probe.active.load(std::sync::atomic::Ordering::SeqCst), 1);
    drop(dropped);
    tokio::task::yield_now().await;
    assert_eq!(probe.active.load(std::sync::atomic::Ordering::SeqCst), 0);
}

#[tokio::test(start_paused = true)]
async fn direct_observation_resets_cooldown_and_unstarted_generation_intent_is_cancelled() {
    let routes = routes(&["direct"]);
    let mut policy = HostPolicy::new(&routes, generation());
    let probe = std::sync::Arc::new(WakeProbe::idle(true));
    assert!(
        wake_select(&routes, &policy, &probe, Duration::from_secs(2))
            .await
            .is_err()
    );
    finish_wake(&policy).await;
    assert!(
        wake_select(&routes, &policy, &probe, Duration::from_secs(2))
            .await
            .is_ok()
    );
    probe
        .ready
        .store(false, std::sync::atomic::Ordering::SeqCst);
    // Direct evidence resets the previous cooldown, so a newly idle path can wake.
    assert!(matches!(
        select(
            &routes,
            &policy,
            probe.as_ref(),
            generation(),
            0,
            Duration::from_secs(2)
        )
        .await,
        Err("TAILSCALE_WAKING")
    ));
    let mut next = generation();
    next.generation += 1;
    policy
        .update_selection_generation(&generation(), next)
        .unwrap();
    policy.start_wake(probe.clone());
    tokio::task::yield_now().await;
    assert_eq!(
        probe.count(),
        1,
        "an unstarted stale intent must never ping"
    );
    assert!(!policy.waking());
}

#[tokio::test(start_paused = true)]
async fn route_relay_requires_explicit_opt_in_and_tcp() {
    let mut doc: Value = serde_json::from_slice(include_bytes!("fixtures/inventory.json")).unwrap();
    let mut probe = Mock::new(false, true, false);
    let status = json!({"Peer":{"id":{"TailscaleIPs":["100.64.0.1"],
        "Online":true,"Active":true,"Relay":"example-derp","CurAddr":""}}});
    let bytes = serde_json::to_vec(&status).unwrap();
    assert_eq!(tailscale_peer(&bytes, "100.64.0.1"), TailscalePeer::Relay);
    assert!(!tailscale_direct(&bytes, "100.64.0.1"));
    probe.peer = Some(tailscale_peer(&bytes, "100.64.0.1"));
    for (host, route, tcp, expected) in [
        (false, None, true, RouteKind::Cf),
        (true, None, true, RouteKind::Direct),
        (true, Some(false), true, RouteKind::Cf),
        (false, Some(true), true, RouteKind::Direct),
        (true, None, false, RouteKind::Cf),
    ] {
        doc["hosts"][0]["allow_tailscale_relay"] = json!(host);
        doc["hosts"][0]["routes"][0]
            .as_object_mut()
            .unwrap()
            .remove("allow_tailscale_relay");
        if let Some(value) = route {
            doc["hosts"][0]["routes"][0]["allow_tailscale_relay"] = json!(value);
        }
        probe.direct_tcp = tcp;
        let routes = Routes::from_inventory(
            &Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap(),
            "node-1",
        )
        .unwrap();
        let policy = HostPolicy::new(&routes, generation());
        assert_eq!(choice(&routes, &policy, &probe, 0).await.kind(), expected);
    }
    for (key, value) in [
        ("Online", json!(false)),
        ("Active", json!(false)),
        ("Relay", json!("")),
        ("CurAddr", Value::Null),
    ] {
        let mut invalid = status.clone();
        invalid["Peer"]["id"][key] = value;
        assert_ne!(
            tailscale_peer(&serde_json::to_vec(&invalid).unwrap(), "100.64.0.1"),
            TailscalePeer::Relay
        );
    }
}

#[test]
fn route_policy_strict_validation() {
    let original: Value =
        serde_json::from_slice(include_bytes!("fixtures/inventory.json")).unwrap();
    for route in [false, true] {
        for (key, values) in [
            (
                "direct_probe_ms",
                vec![
                    json!(0),
                    json!(5001),
                    json!(true),
                    json!(150.5),
                    json!("600"),
                    Value::Null,
                ],
            ),
            (
                "allow_tailscale_relay",
                vec![json!(1), json!("true"), Value::Null],
            ),
            ("allow_tailscale_relai", vec![json!(true)]),
        ] {
            for value in values {
                let mut doc = original.clone();
                let host = &mut doc["hosts"][0];
                let target = if route { &mut host["routes"][0] } else { host };
                target[key] = value;
                assert!(Inventory::parse(&serde_json::to_vec(&doc).unwrap()).is_err());
            }
        }
    }
}

struct BudgetProbe(u64);
impl RouteProbe for BudgetProbe {
    fn tailscale_direct<'a>(&'a self, _: &'a str, until: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            assert_eq!(until - Instant::now(), Duration::from_millis(self.0));
            true
        })
    }
    fn tcp<'a>(&'a self, _: &'a Endpoint, until: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            assert_eq!(until - Instant::now(), Duration::from_millis(self.0));
            true
        })
    }
}
#[tokio::test(start_paused = true)]
async fn route_probe_budget_route_over_host_over_global() {
    for (host, route, expected) in [
        (None, None, 150),
        (Some(600), None, 600),
        (Some(600), Some(1500), 1500),
        (None, Some(1500), 1500),
    ] {
        let mut doc: Value =
            serde_json::from_slice(include_bytes!("fixtures/inventory.json")).unwrap();
        doc["direct_probe_ms"] = json!(150);
        doc["hosts"][0]["routes"]
            .as_array_mut()
            .unwrap()
            .truncate(1);
        doc["hosts"][0]["provision_route"] = json!("direct");
        if let Some(value) = host {
            doc["hosts"][0]["direct_probe_ms"] = json!(value);
        }
        if let Some(value) = route {
            doc["hosts"][0]["routes"][0]["direct_probe_ms"] = json!(value);
        }
        let routes = Routes::from_inventory(
            &Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap(),
            "node-1",
        )
        .unwrap();
        let policy = HostPolicy::new(&routes, generation());
        assert_eq!(
            select(
                &routes,
                &policy,
                &BudgetProbe(expected),
                generation(),
                0,
                Duration::from_secs(2)
            )
            .await
            .ok()
            .unwrap()
            .kind(),
            RouteKind::Direct
        );
    }
}

struct HomeProbe {
    peer: TailscalePeer,
    lan_ok: bool,
    tcp_ok: bool,
}
impl RouteProbe for HomeProbe {
    fn tailscale_direct<'a>(&'a self, _: &'a str, _: Instant) -> ProbeFuture<'a> {
        Box::pin(async move { self.peer == TailscalePeer::Direct })
    }
    fn tailscale_peer<'a>(&'a self, _: &'a str, _: Instant) -> PeerFuture<'a> {
        Box::pin(async move { self.peer })
    }
    fn tcp<'a>(&'a self, ep: &'a Endpoint, until: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            if ep.address.starts_with("100.") {
                assert_eq!(until - Instant::now(), Duration::from_millis(600));
                self.tcp_ok
            } else {
                assert_eq!(until - Instant::now(), Duration::from_millis(1500));
                tokio::time::sleep(Duration::from_millis(1200)).await;
                self.lan_ok
            }
        })
    }
}
#[tokio::test(start_paused = true)]
async fn home_lan_precedes_direct_and_relay_with_cf_fallback() {
    for prefer_lan in [None, Some(false), Some(true)] {
        for (peer, allow, lan_ok, tcp_ok, expected) in [
            (TailscalePeer::Relay, true, true, true, RouteKind::Lan),
            (TailscalePeer::Direct, true, true, true, RouteKind::Lan),
            (TailscalePeer::Direct, false, false, true, RouteKind::Direct),
            (TailscalePeer::Relay, true, false, true, RouteKind::Direct),
            (TailscalePeer::Relay, false, false, true, RouteKind::Cf),
            (TailscalePeer::Relay, true, false, false, RouteKind::Cf),
        ] {
            let mut doc: Value =
                serde_json::from_slice(include_bytes!("fixtures/inventory.json")).unwrap();
            if let Some(value) = prefer_lan {
                doc["hosts"][0]["prefer_lan"] = json!(value);
            }
            doc["hosts"][0]["routes"] = json!([
                {"kind":"lan","alias":"fixture-lan","probe":"192.0.2.1:22","direct_probe_ms":1500},
                {"kind":"direct","alias":"fixture-direct","probe":"100.64.0.1:22",
                 "allow_tailscale_relay":allow,"direct_probe_ms":600},
                {"kind":"cf","alias":"fixture-cf","access_host":"node-1.example.invalid"}
            ]);
            if prefer_lan != Some(true) {
                doc["hosts"][0]["routes"].as_array_mut().unwrap().swap(0, 1);
            }
            let routes = Routes::from_inventory(
                &Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap(),
                "node-1",
            )
            .unwrap();
            let policy = HostPolicy::new(&routes, generation());
            let result = select(
                &routes,
                &policy,
                &HomeProbe {
                    peer,
                    lan_ok,
                    tcp_ok,
                },
                generation(),
                0,
                Duration::from_secs(2),
            )
            .await
            .unwrap();
            let expected =
                if prefer_lan != Some(true) && tcp_ok && (peer == TailscalePeer::Direct || allow) {
                    RouteKind::Direct
                } else {
                    expected
                };
            assert_eq!(result.kind(), expected);
        }
    }
}

#[test]
fn home_example_inventory_is_valid() {
    Inventory::parse(include_bytes!(
        "../../fleet.example/client/fleet-inventory.json"
    ))
    .unwrap();
}

#[test]
fn home_inventory_still_rejects_duplicate_and_unknown_route_kinds() {
    let doc: Value = serde_json::from_slice(include_bytes!(
        "../../fleet.example/client/fleet-inventory.json"
    ))
    .unwrap();
    for kind in ["lan", "direct", "cf", "unknown"] {
        let mut invalid = doc.clone();
        let mut route = invalid["hosts"][0]["routes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|route| route["kind"] == kind)
            .unwrap_or(&invalid["hosts"][0]["routes"][0])
            .clone();
        route["kind"] = kind.into();
        invalid["hosts"][0]["routes"]
            .as_array_mut()
            .unwrap()
            .push(route);
        assert!(matches!(
            Inventory::parse(&serde_json::to_vec(&invalid).unwrap()),
            Err("INVENTORY_INVALID")
        ));
    }
}

#[test]
fn prefer_lan_is_strict_host_only_and_controls_route_order() {
    let doc: Value = serde_json::from_slice(include_bytes!(
        "../../fleet.example/client/fleet-inventory.json"
    ))
    .unwrap();
    for value in [
        Value::Null,
        json!("true"),
        json!(1),
        json!([]),
        json!({}),
        json!(false),
    ] {
        let mut invalid = doc.clone();
        invalid["hosts"][0]["prefer_lan"] = value;
        assert!(Inventory::parse(&serde_json::to_vec(&invalid).unwrap()).is_err());
    }
    let mut default = doc.clone();
    default["hosts"][0]
        .as_object_mut()
        .unwrap()
        .remove("prefer_lan");
    assert!(Inventory::parse(&serde_json::to_vec(&default).unwrap()).is_err());
    default["hosts"][0]["routes"]
        .as_array_mut()
        .unwrap()
        .swap(0, 1);
    Inventory::parse(&serde_json::to_vec(&default).unwrap()).unwrap();
    default["hosts"][0]["prefer_lan"] = json!(false);
    Inventory::parse(&serde_json::to_vec(&default).unwrap()).unwrap();
    default["hosts"][0]["routes"][0]["prefer_lan"] = json!(true);
    assert!(Inventory::parse(&serde_json::to_vec(&default).unwrap()).is_err());
    let mut invalid = doc.clone();
    invalid["hosts"][0]["routes"]
        .as_array_mut()
        .unwrap()
        .swap(1, 2);
    assert!(Inventory::parse(&serde_json::to_vec(&invalid).unwrap()).is_err());
    let bytes = String::from_utf8(serde_json::to_vec(&doc).unwrap())
        .unwrap()
        .replace(
            "\"prefer_lan\":true",
            "\"prefer_lan\":true,\"prefer_lan\":true",
        );
    assert!(Inventory::parse(bytes.as_bytes()).is_err());
}
