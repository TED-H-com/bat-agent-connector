use bat_fleet_core::{
    inventory::Endpoint,
    route::{NativeRouteProbe, RouteProbe, TailscalePeer},
};
use std::{
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};
use tokio::{net::TcpListener, time::Instant as Deadline};
struct Temporary(PathBuf);
impl Temporary {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "bat-fleet-route-{}-{:032x}",
            std::process::id(),
            rand::random::<u128>()
        ));
        std::fs::create_dir(&p).unwrap();
        Self(p)
    }
}
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
struct ChildGuard(Child);
impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn fixture() -> (Temporary, PathBuf) {
    let temp = Temporary::new();
    std::fs::create_dir(temp.0.join("src")).unwrap();
    std::fs::write(
        temp.0.join("src/main.rs"),
        include_bytes!("fixtures/route-probe/main.rs"),
    )
    .unwrap();
    std::fs::write(
        temp.0.join("Cargo.toml"),
        b"[package]\nname='tailscale'\nversion='0.0.0'\nedition='2021'\n[workspace]\n",
    )
    .unwrap();
    let child = Command::new(env!("CARGO"))
        .args(["build", "--offline", "-j1", "--manifest-path"])
        .arg(temp.0.join("Cargo.toml"))
        .env("CARGO_TARGET_DIR", temp.0.join("target"))
        .env_remove("CARGO_MAKEFLAGS")
        .env_remove("MAKEFLAGS")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let mut child = ChildGuard(child);
    let deadline = Instant::now() + Duration::from_secs(60);
    loop {
        if let Some(status) = child.0.try_wait().unwrap() {
            assert!(status.success());
            break;
        }
        assert!(Instant::now() < deadline, "owned fixture compile deadline");
        std::thread::sleep(Duration::from_millis(20));
    }
    let path = temp.0.join("target/debug").join(if cfg!(windows) {
        "tailscale.exe"
    } else {
        "tailscale"
    });
    (temp, path)
}
fn mode(path: &Path, value: &str) {
    let dir = path.parent().unwrap();
    for file in [
        "started",
        "status-count",
        "finished",
        "argv-ok",
        "ping-ok",
        "ping-count",
        "awakened",
    ] {
        let _ = std::fs::remove_file(dir.join(file));
    }
    std::fs::write(dir.join("mode"), value).unwrap();
}
#[tokio::test]
async fn native_status_double_checks_fixed_command_environment_bounds_and_cancellation() {
    let (_temp, path) = fixture();
    let probe = NativeRouteProbe::new(Some(&path)).unwrap();
    mode(&path, "ok");
    assert!(
        probe
            .tailscale_direct("100.64.0.1", Deadline::now() + Duration::from_secs(2))
            .await
    );
    assert_eq!(
        std::fs::read_to_string(path.parent().unwrap().join("argv-ok")).unwrap(),
        "yes"
    );
    for value in ["invalid_utf8", "invalid_json", "oversized", "exit_nonzero"] {
        mode(&path, value);
        assert!(
            !probe
                .tailscale_direct("100.64.0.1", Deadline::now() + Duration::from_secs(2))
                .await
        );
    }
    mode(&path, "hang");
    let start = Instant::now();
    assert!(
        !probe
            .tailscale_direct("100.64.0.1", Deadline::now() + Duration::from_millis(200))
            .await
    );
    assert!(start.elapsed() < Duration::from_secs(1));
    tokio::time::sleep(Duration::from_millis(900)).await;
    assert!(!path.parent().unwrap().join("finished").exists());
    mode(&path, "hang");
    let mut call = probe.tailscale_direct("100.64.0.1", Deadline::now() + Duration::from_secs(5));
    let started = path.parent().unwrap().join("started");
    tokio::select! {_=&mut call=>panic!("hang double returned before cancellation"),_=async{let until=Deadline::now()+Duration::from_secs(2);while !started.exists(){assert!(Deadline::now()<until);tokio::time::sleep(Duration::from_millis(5)).await;}}=>()}
    drop(call);
    tokio::time::sleep(Duration::from_millis(900)).await;
    assert!(!path.parent().unwrap().join("finished").exists());
}
#[tokio::test]
async fn native_tcp_is_bounded_and_missing_executable_never_searches_path() {
    let probe = NativeRouteProbe::new(None).unwrap();
    assert!(
        !probe
            .tailscale_direct("100.64.0.1", Deadline::now() + Duration::from_secs(1))
            .await
    );
    assert!(NativeRouteProbe::new(Some(Path::new("tailscale"))).is_err());
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let ep = Endpoint {
        address: "127.0.0.1".into(),
        port: listener.local_addr().unwrap().port(),
    };
    assert!(
        probe
            .tcp(&ep, Deadline::now() + Duration::from_secs(1))
            .await
    );
    assert!(
        !probe
            .tcp(&ep, Deadline::now() - Duration::from_millis(1))
            .await
    );
    drop(listener);
    // A just-released listener is not a reliable absence oracle: close propagation
    // and ephemeral-port reuse can race the next connect. Reserve a socket without
    // listening, keeping its port unavailable to another fixture throughout the probe.
    let reserved = tokio::net::TcpSocket::new_v4().unwrap();
    reserved.bind("127.0.0.1:0".parse().unwrap()).unwrap();
    let closed = Endpoint {
        address: "127.0.0.1".into(),
        port: reserved.local_addr().unwrap().port(),
    };
    assert!(
        !probe
            .tcp(&closed, Deadline::now() + Duration::from_secs(1))
            .await
    );
    drop(reserved);
}

#[tokio::test]
async fn native_wake_uses_fixed_ip_command_environment_and_kills_on_timeout_or_cancel() {
    let (_temp, path) = fixture();
    let probe = NativeRouteProbe::new(Some(&path)).unwrap();
    let dir = path.parent().unwrap();
    mode(&path, "idle");
    assert_eq!(
        probe
            .tailscale_peer("100.64.0.1", Deadline::now() + Duration::from_secs(2))
            .await,
        TailscalePeer::Idle
    );
    assert!(
        probe
            .tailscale_wake("100.64.0.1", Deadline::now() + Duration::from_secs(2))
            .await
    );
    assert_eq!(std::fs::read_to_string(dir.join("ping-ok")).unwrap(), "yes");
    assert!(
        probe
            .tailscale_direct("100.64.0.1", Deadline::now() + Duration::from_secs(2))
            .await
    );
    mode(&path, "idle");
    assert!(
        !probe
            .tailscale_wake("old-name", Deadline::now() + Duration::from_secs(2))
            .await
    );
    assert!(!dir.join("started").exists());
    for cancel in [false, true] {
        mode(&path, "ping_hang");
        if cancel {
            let mut call =
                probe.tailscale_wake("100.64.0.1", Deadline::now() + Duration::from_secs(5));
            tokio::select! {
                _ = &mut call => panic!("ping must wait for cancellation"),
                _ = async {
                    let until = Deadline::now() + Duration::from_secs(2);
                    while !dir.join("started").exists() {
                        assert!(Deadline::now() < until);
                        tokio::time::sleep(Duration::from_millis(5)).await;
                    }
                } => ()
            }
            drop(call);
        } else {
            assert!(
                !probe
                    .tailscale_wake("100.64.0.1", Deadline::now() + Duration::from_millis(200))
                    .await
            );
        }
        tokio::time::sleep(Duration::from_millis(900)).await;
        assert_eq!(std::fs::read_to_string(dir.join("ping-ok")).unwrap(), "yes");
        assert!(!dir.join("finished").exists());
        assert!(!dir.join("awakened").exists());
    }
}

#[tokio::test]
async fn aliases_for_the_same_peer_share_one_owned_native_ping_and_release_after_completion() {
    let (_temp, path) = fixture();
    let probe = NativeRouteProbe::new(Some(&path)).unwrap();
    mode(&path, "ping_hang");
    let end = Deadline::now() + Duration::from_secs(3);
    let (first, joined) = tokio::join!(
        probe.tailscale_wake("100.64.0.1", end),
        probe.tailscale_wake("100.64.0.1", end),
    );
    assert!(first);
    assert!(!joined, "a joined ping never returns direct-path evidence");
    let count = path.parent().unwrap().join("ping-count");
    assert_eq!(std::fs::read_to_string(&count).unwrap().lines().count(), 1);
    assert!(
        probe
            .tailscale_wake("100.64.0.1", Deadline::now() + Duration::from_secs(3))
            .await
    );
    assert_eq!(std::fs::read_to_string(&count).unwrap().lines().count(), 2);
}

#[tokio::test]
async fn native_missing_idle_and_unreadable_peer_explain_direct_requirement_without_fallback() {
    use bat_fleet_core::{
        inventory::Inventory,
        ownership::ProbeGeneration,
        route::{HostPolicy, Routes, SelectionRequest},
    };
    let (_temp, path) = fixture();
    let probe = NativeRouteProbe::new(Some(&path)).unwrap();
    let mut inventory: serde_json::Value =
        serde_json::from_slice(include_bytes!("fixtures/inventory.json")).unwrap();
    inventory["hosts"][0]["routes"] =
        serde_json::json!([{"kind":"direct","alias":"fixture-direct","probe":"100.64.0.1:22"}]);
    inventory["hosts"][0]["provision_route"] = "direct".into();
    let inventory = Inventory::parse(&serde_json::to_vec(&inventory).unwrap()).unwrap();
    let routes = Routes::from_inventory(&inventory, "node-1").unwrap();
    let generation = ProbeGeneration {
        epoch: "fixture".into(),
        configuration_binding: "fixture".into(),
        selection_revision: "fixture".into(),
        generation: 1,
    };
    for (value, expected) in [
        ("missing", "DIRECT_PEER_UNKNOWN"),
        ("idle", "DIRECT_NOT_CONFIRMED"),
        ("invalid_json", "DIRECT_PEER_UNKNOWN"),
    ] {
        mode(&path, value);
        let policy = HostPolicy::new(&routes, generation.clone());
        let failure = SelectionRequest::new(&routes, &policy, generation.clone(), 1000)
            .unwrap()
            .run(&probe, Duration::from_secs(2))
            .await
            .err()
            .unwrap();
        assert_eq!(
            serde_json::to_value(failure).unwrap()["route_requirement"],
            expected
        );
        assert_eq!(
            std::fs::read_to_string(path.parent().unwrap().join("status-count"))
                .unwrap()
                .lines()
                .count(),
            1
        );
        assert!(!path.parent().unwrap().join("ping-count").exists());
    }
}
