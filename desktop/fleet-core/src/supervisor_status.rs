//! Safe PS-compatible readiness publication and strict consumer validation.
use crate::{
    configuration::Configuration,
    ownership::{epoch_valid, ProbeGeneration},
    probe::{self, Authentication, Layer, Level, ProbeKind, ProbeObservation},
    route::{RouteKind, RouteObservation, RouteRequirement},
    strict_json, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub(crate) fn timestamp(ms: u64) -> Result<String> {
    OffsetDateTime::from_unix_timestamp_nanos(i128::from(ms) * 1_000_000)
        .map_err(|_| "CLOCK_UNPROVEN")?
        .format(&Rfc3339)
        .map_err(|_| "CLOCK_UNPROVEN")
}
fn millis(value: &str) -> Result<u64> {
    OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| "STATUS_UNPROVEN")?
        .unix_timestamp_nanos()
        .checked_div(1_000_000)
        .and_then(|n| n.try_into().ok())
        .ok_or("STATUS_UNPROVEN")
}
fn digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Layers {
    pub tunnel: bool,
    pub tls: bool,
    pub bat: bool,
    pub workspace: bool,
    pub auth: Option<Authentication>,
    pub version: Option<String>,
}
/// Public route evidence cannot contain native routing inputs.
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Route {
    pub kind: RouteKind,
    pub observed_at: String,
    pub generation: u64,
}
/// Convert age in the producer's observation clock against its Unix clock anchor.
/// Never format a monotonic observation directly as a Unix timestamp. The native
/// supervisor currently supplies Unix milliseconds for both clock coordinates.
fn observation_time(observed_ms: u64, clock_ms: u64, unix_ms: u64) -> Result<String> {
    let age = clock_ms.checked_sub(observed_ms).ok_or("CLOCK_UNPROVEN")?;
    timestamp(unix_ms.checked_sub(age).ok_or("CLOCK_UNPROVEN")?)
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub name: String,
    pub label: String,
    pub selected: bool,
    pub level: String,
    pub blocking: Option<Layer>,
    pub code: Option<String>,
    pub observed_at: Option<String>,
    pub stale: bool,
    pub layers: Layers,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub route: Option<Route>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub route_requirement: Option<RouteRequirement>,
}
impl Entry {
    pub(crate) fn observed(
        identity: (&str, &str),
        selected: bool,
        kind: ProbeKind,
        (observation, route, route_requirement): (
            Option<&ProbeObservation>,
            Option<&RouteObservation>,
            Option<RouteRequirement>,
        ),
        generation: &ProbeGeneration,
        now: u64,
        failure: Option<(&'static str, Layer)>,
    ) -> Result<Self> {
        let state = probe::readiness(selected, kind, observation, generation, now);
        let mut row = Self {
            name: identity.0.into(),
            label: identity.1.into(),
            selected,
            level: match state.level {
                Level::Off => "off",
                Level::Down => "down",
                Level::Ready => "ready",
                Level::Degraded => "degraded",
            }
            .into(),
            blocking: state.blocking,
            code: state.code.map(Into::into),
            observed_at: None,
            stale: state.blocking == Some(Layer::Stale),
            layers: Layers::default(),
            route: None,
            route_requirement: None,
        };
        if let Some(probe) = observation.filter(|p| {
            crate::ownership::probe_current(&p.generation, generation, p.observed_ms, now)
        }) {
            row.observed_at = Some(timestamp(probe.observed_ms)?);
            row.layers = Layers {
                tunnel: probe.tunnel,
                tls: probe.tls,
                bat: probe.bat,
                workspace: probe.workspace,
                auth: Some(probe.auth),
                version: probe.version.clone(),
            };
        }
        // A future/unrepresentable route confirmation cannot suppress the rest
        // of the status row (for example after the producer clock steps back).
        row.route = route
            .filter(|r| selected && r.generation == *generation)
            .and_then(|route| {
                observation_time(route.confirmed_ms, now, now)
                    .ok()
                    .map(|observed_at| Route {
                        kind: route.kind,
                        observed_at,
                        generation: route.generation.generation,
                    })
            });
        // An unresolved owned stop is evidence even after the desired selection
        // changes. It is not an ordinary off row and cannot claim readiness.
        if selected || failure.is_some_and(|(code, _)| code == "STOP_UNCONFIRMED") {
            if let Some((code, layer)) = failure {
                row.level = if code == "TAILSCALE_WAKING" {
                    "degraded"
                } else {
                    "down"
                }
                .into();
                row.code = Some(code.into());
                row.blocking = Some(layer);
                row.layers = Layers::default();
            }
        }
        if row.unobserved_route_state() {
            row.route_requirement = route_requirement;
        }
        Ok(row)
    }
    /// A retained failed selection can explain waiting or recovery, but never
    /// current probe evidence, a chosen route, or an unrelated failure.
    fn unobserved_route_state(&self) -> bool {
        self.selected
            && self.observed_at.is_none()
            && self.route.is_none()
            && !self.layers.tunnel
            && !self.layers.tls
            && !self.layers.bat
            && !self.layers.workspace
            && self.layers.auth.is_none()
            && self.layers.version.is_none()
            && matches!(
                (self.code.as_deref(), self.level.as_str(), self.blocking),
                (
                    Some("PROBE_PENDING"),
                    "degraded",
                    Some(Layer::Bat | Layer::Connector)
                ) | (Some("TAILSCALE_WAKING"), "degraded", Some(Layer::Tunnel))
                    | (
                        Some("ROUTE_UNAVAILABLE" | "TUNNEL_DOWN" | "RECOVERY_EXHAUSTED"),
                        "down",
                        Some(Layer::Tunnel)
                    )
            )
    }
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub schema_version: u32,
    pub monitor_epoch: String,
    pub configuration_binding: String,
    pub observed_at: String,
    pub applied_selection_revision: String,
    pub entries: Vec<Entry>,
    #[serde(default = "running")]
    pub lifecycle: String,
}
fn running() -> String {
    "running".into()
}
impl Snapshot {
    /// Outer publication age is independent of selected entries. A file with
    /// every connection off can still be stale; future/malformed dates refuse.
    pub fn is_fresh(&self, now_ms: u64) -> Result<bool> {
        Ok(now_ms
            .checked_sub(millis(&self.observed_at)?)
            .ok_or("STATUS_UNPROVEN")?
            <= 60_000)
    }
    pub(crate) fn new(
        epoch: &str,
        binding: &str,
        applied: &str,
        entries: Vec<Entry>,
        now: u64,
    ) -> Result<Self> {
        Ok(Self {
            schema_version: 1,
            monitor_epoch: epoch.into(),
            configuration_binding: binding.into(),
            observed_at: timestamp(now)?,
            applied_selection_revision: applied.into(),
            entries,
            lifecycle: running(),
        })
    }
    /// The facade must freshly prove the matching monitor before reading. Outer file
    /// freshness never upgrades a stale per-entry observation.
    pub fn parse(
        bytes: &[u8],
        configuration: &Configuration,
        epoch: &str,
        now: u64,
    ) -> Result<Self> {
        let mut value: Self = serde_json::from_value(strict_json::parse(bytes, 262144)?)
            .map_err(|_| "STATUS_UNPROVEN")?;
        if value.schema_version != 1
            || !epoch_valid(epoch)
            || value.monitor_epoch != epoch
            || value.configuration_binding != configuration.binding()
            || !digest(&value.applied_selection_revision)
            || ![
                "running",
                "selection_pending",
                "stopped",
                "stop_unconfirmed",
            ]
            .contains(&value.lifecycle.as_str())
        {
            return Err("STATUS_UNPROVEN");
        }
        let file_stale = !value.is_fresh(now)?;
        let mut expected = configuration
            .inventory
            .hosts()
            .iter()
            .map(|v| (v["name"].as_str().unwrap(), v["label"].as_str().unwrap()))
            .collect::<Vec<_>>();
        expected.push((
            "connector",
            configuration.inventory.connector()["label"]
                .as_str()
                .unwrap(),
        ));
        if value.entries.len() != expected.len() {
            return Err("STATUS_UNPROVEN");
        }
        let mut seen = HashSet::new();
        for row in &mut value.entries {
            let stop_unconfirmed = row.code.as_deref() == Some("STOP_UNCONFIRMED");
            let label = expected
                .iter()
                .find(|(id, _)| *id == row.name)
                .map(|(_, label)| *label)
                .ok_or("STATUS_UNPROVEN")?;
            if !seen.insert(row.name.clone())
                || !["off", "ready", "degraded", "down"].contains(&row.level.as_str())
                || row.selected == (row.level == "off")
                    && !(stop_unconfirmed && !row.selected && row.level == "down")
                || row.route_requirement.is_some() && !row.unobserved_route_state()
                || row.code.as_ref().is_some_and(|s| {
                    s.is_empty()
                        || s.len() > 80
                        || !s
                            .bytes()
                            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
                })
            {
                return Err("STATUS_UNPROVEN");
            }
            // A retained status converted to an off row must discard its route,
            // independently of the legacy sparse readiness-layer contract.
            if !row.selected {
                row.route = None;
            }
            if let Some(route) = &row.route {
                if route.generation == 0
                    || route.generation > 9_007_199_254_740_991
                    || millis(&route.observed_at)? > now
                {
                    return Err("STATUS_UNPROVEN");
                }
            }
            row.label = label.into(); // Never trust file-supplied presentation text.
            let old = match &row.observed_at {
                Some(at) => now.checked_sub(millis(at)?).ok_or("STATUS_UNPROVEN")? > 60_000,
                None => true,
            };
            if (row.selected || stop_unconfirmed)
                && (file_stale || row.stale || old && row.level == "ready")
            {
                // Age withdraws fresh layers, never an unresolved stop outcome.
                if !stop_unconfirmed {
                    row.level = "degraded".into();
                    row.blocking = Some(Layer::Stale);
                    row.code = Some("PROBE_STALE".into());
                    row.route_requirement = None;
                }
                row.stale = true;
                row.layers = Layers::default();
            }
            if let Some(version) = &row.layers.version {
                if version.len() > 80
                    || !version
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b".-+".contains(&b))
                {
                    return Err("STATUS_UNPROVEN");
                }
            }
        }
        Ok(value)
    }
}

#[cfg(test)]
mod contract_tests {
    use super::*;
    use serde_json::Value;

    struct FixtureRoutes;
    impl crate::route::RouteProbe for FixtureRoutes {
        fn tailscale_direct<'a>(
            &'a self,
            _: &'a str,
            _: tokio::time::Instant,
        ) -> crate::route::ProbeFuture<'a> {
            Box::pin(async { true })
        }
        fn tcp<'a>(
            &'a self,
            _: &'a crate::inventory::Endpoint,
            _: tokio::time::Instant,
        ) -> crate::route::ProbeFuture<'a> {
            Box::pin(async { true })
        }
    }
    async fn observed_fixture() -> Value {
        // De-identified Context::status input for the Tauri sanitizer test.
        // Build with the real producer, including Connector's false BAT layers.
        let mut raw: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/fleet-status.json")).unwrap();
        let status = &raw["result"];
        let now = millis(status["readiness"]["observed_at"].as_str().unwrap()).unwrap();
        let generation = ProbeGeneration {
            epoch: "a".repeat(32),
            configuration_binding: "c".repeat(64),
            selection_revision: "d".repeat(64),
            generation: 1,
        };
        let mut inventory: Value =
            serde_json::from_str(include_str!("../tests/fixtures/inventory.json")).unwrap();
        inventory["connector"]["ssh_alias"] = "fixture-connector".into();
        let inventory =
            crate::inventory::Inventory::parse(&serde_json::to_vec(&inventory).unwrap()).unwrap();
        let mut rows = Vec::new();
        for expected in status["readiness"]["hosts"]
            .as_array()
            .unwrap()
            .iter()
            .chain(std::iter::once(&status["readiness"]["connector"]))
        {
            let bat = expected["name"] != "connector";
            let probe = ProbeObservation {
                generation: generation.clone(),
                observed_ms: now,
                kind: if bat {
                    ProbeKind::Bat
                } else {
                    ProbeKind::Connector
                },
                tunnel: true,
                tls: bat,
                auth: Authentication::Ok,
                bat,
                workspace: bat,
                connector: !bat,
                version: bat.then(|| "3.2.5".into()),
                code: None,
                blocking: None,
                reason: None,
            };
            let routes = crate::route::Routes::from_inventory(
                &inventory,
                expected["name"].as_str().unwrap(),
            )
            .unwrap();
            let policy = crate::route::HostPolicy::new(&routes, generation.clone());
            let choice = crate::route::SelectionRequest::new(
                &routes,
                &policy,
                generation.clone(),
                now - 300_000,
            )
            .unwrap()
            .run(&FixtureRoutes, std::time::Duration::from_secs(2))
            .await
            .unwrap();
            let mut route = choice.observation(now - 300_000);
            // The retained run is still in use at the snapshot's ownership check.
            route.confirm_use(&generation, now);
            let row = Entry::observed(
                (
                    expected["name"].as_str().unwrap(),
                    expected["label"].as_str().unwrap(),
                ),
                true,
                probe.kind,
                (Some(&probe), Some(&route), None),
                &generation,
                now,
                None,
            )
            .unwrap();
            rows.push(serde_json::to_value(row).unwrap());
        }
        raw["result"]["readiness"]["connector"] = rows.pop().unwrap();
        raw["result"]["readiness"]["hosts"] = rows.into();
        raw
    }
    struct RequirementProbe {
        peer: crate::route::TailscalePeer,
    }
    impl crate::route::RouteProbe for RequirementProbe {
        fn tailscale_direct<'a>(
            &'a self,
            _: &'a str,
            _: tokio::time::Instant,
        ) -> crate::route::ProbeFuture<'a> {
            Box::pin(async { false })
        }
        fn tailscale_peer<'a>(
            &'a self,
            _: &'a str,
            _: tokio::time::Instant,
        ) -> crate::route::PeerFuture<'a> {
            Box::pin(async move { self.peer })
        }
        fn tcp<'a>(
            &'a self,
            _: &'a crate::inventory::Endpoint,
            _: tokio::time::Instant,
        ) -> crate::route::ProbeFuture<'a> {
            Box::pin(async { false })
        }
    }
    async fn requirement_fixture() -> Value {
        let mut cases = serde_json::json!({});
        for (name, peer, exhausted) in [
            ("missing", b"{\"Peer\":{}}".as_slice(), false),
            ("unknown", b"unreadable fixture status".as_slice(), false),
            ("idle", br#"{"Peer":{"fixture":{"TailscaleIPs":["100.64.0.1"],"Online":true,"CurAddr":""}}}"#.as_slice(), false),
            ("tcp", br#"{"Peer":{"fixture":{"TailscaleIPs":["100.64.0.1"],"Online":true,"CurAddr":"203.0.113.1:1234"}}}"#.as_slice(), false),
            ("aged", br#"{"Peer":{"fixture":{"TailscaleIPs":["100.64.0.1"],"Online":true,"CurAddr":"203.0.113.1:1234"}}}"#.as_slice(), false),
            ("exhausted", b"{\"Peer\":{}}".as_slice(), true),
        ] {
            let mut raw = observed_fixture().await;
            let now = millis(raw["result"]["readiness"]["observed_at"].as_str().unwrap()).unwrap();
            let generation = ProbeGeneration { epoch: "a".repeat(32), configuration_binding: "c".repeat(64), selection_revision: "d".repeat(64), generation: 1 };
            let mut inventory: Value = serde_json::from_str(include_str!("../tests/fixtures/inventory.json")).unwrap();
            inventory["hosts"][0]["routes"] = serde_json::json!([{"kind":"direct","alias":"fixture-direct","probe":"100.64.0.1:22"}]);
            inventory["hosts"][0]["provision_route"] = "direct".into();
            if exhausted { inventory["hosts"][0]["routes"].as_array_mut().unwrap().push(serde_json::json!({"kind":"lan","alias":"fixture-lan","probe":"192.0.2.1:22"})); }
            let inventory = crate::inventory::Inventory::parse(&serde_json::to_vec(&inventory).unwrap()).unwrap();
            let routes = crate::route::Routes::from_inventory(&inventory, "node-1").unwrap();
            let policy = crate::route::HostPolicy::new(&routes, generation.clone());
            let probe = RequirementProbe { peer: crate::route::tailscale_peer(peer, "100.64.0.1") };
            let failure = crate::route::SelectionRequest::new(&routes, &policy, generation.clone(), now).unwrap()
                .run(&probe, std::time::Duration::from_secs(2)).await.err().unwrap();
            let row = Entry::observed(("node-1", "Fixture 1"), true, ProbeKind::Bat,
                (None, None, failure.route_requirement), &generation, now, Some((failure.code, Layer::Tunnel))).unwrap();
            raw["result"]["readiness"]["hosts"][0] = serde_json::to_value(row).unwrap();
            if name == "aged" {
                let later = now + 60_001;
                let parsed = parse_fixture_file(&raw, &inventory, later).unwrap();
                assert!(!parsed.is_fresh(later).unwrap());
                for row in parsed.entries {
                    if row.name == "connector" {
                        raw["result"]["readiness"]["connector"] = serde_json::to_value(row).unwrap();
                    } else {
                        let target = raw["result"]["readiness"]["hosts"].as_array_mut().unwrap()
                            .iter_mut().find(|target| target["name"] == row.name).unwrap();
                        *target = serde_json::to_value(row).unwrap();
                    }
                }
                raw["now"] = later.into();
            }
            cases[name] = raw;
        }
        cases
    }
    /// Publish typed producer rows to disk, then use the real configuration-bound
    /// parser at the reader's clock. No expected row values are synthesized here.
    fn parse_fixture_file(
        raw: &Value,
        inventory: &crate::inventory::Inventory,
        now: u64,
    ) -> Result<Snapshot> {
        struct Temporary(std::path::PathBuf);
        impl Drop for Temporary {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let temp = Temporary(
            std::env::temp_dir().join(format!("fleet-requirement-{:032x}", rand::random::<u128>())),
        );
        std::fs::create_dir_all(temp.0.join("kit/bat-profiles")).unwrap();
        std::fs::create_dir(temp.0.join("user")).unwrap();
        std::fs::write(
            temp.0.join("kit/fleet-inventory.json"),
            serde_json::to_vec(inventory.document()).unwrap(),
        )
        .unwrap();
        std::fs::write(
            temp.0.join("kit/bat-profiles/index.json"),
            include_bytes!("../tests/fixtures/profile-index.json"),
        )
        .unwrap();
        std::fs::write(
            temp.0.join("kit/ssh-config"),
            include_bytes!("../tests/fixtures/ssh-config"),
        )
        .unwrap();
        let config = Configuration::load(
            crate::configuration::Paths::new(&temp.0.join("kit"), &temp.0.join("user"), None, None)
                .unwrap(),
        )
        .unwrap();
        let readiness = &raw["result"]["readiness"];
        let mut entries: Vec<Entry> = serde_json::from_value(readiness["hosts"].clone()).unwrap();
        entries.push(serde_json::from_value(readiness["connector"].clone()).unwrap());
        let snapshot = Snapshot::new(
            &"a".repeat(32),
            config.binding(),
            &"d".repeat(64),
            entries,
            millis(readiness["observed_at"].as_str().unwrap()).unwrap(),
        )
        .unwrap();
        let path = temp.0.join("fleet-desktop-status.json");
        std::fs::write(&path, serde_json::to_vec(&snapshot).unwrap()).unwrap();
        Snapshot::parse(
            &std::fs::read(path).unwrap(),
            &config,
            &snapshot.monitor_epoch,
            now,
        )
    }
    #[tokio::test]
    async fn aged_route_requirement_is_withdrawn_with_its_failure() {
        let cases = requirement_fixture().await;
        let row = &cases["aged"]["result"]["readiness"]["hosts"][0];
        assert_eq!(row["code"], "PROBE_STALE");
        assert_eq!(row["level"], "degraded");
        assert_eq!(row["blocking"], "stale");
        assert_eq!(row["layers"]["tunnel"], false);
        assert!(row.get("route_requirement").is_none());
    }
    #[test]
    fn route_requirement_follows_unobserved_tunnel_states() {
        let generation = ProbeGeneration {
            epoch: "a".repeat(32),
            configuration_binding: "c".repeat(64),
            selection_revision: "d".repeat(64),
            generation: 1,
        };
        for selected in [true, false] {
            for code in [
                None,
                Some("ROUTE_UNAVAILABLE"),
                Some("TAILSCALE_WAKING"),
                Some("TUNNEL_DOWN"),
                Some("RECOVERY_EXHAUSTED"),
                Some("STOP_UNCONFIRMED"),
                Some("CREDENTIAL_MISSING"),
                Some("PROFILE_DRIFT"),
                Some("BAT_AUTH_REFUSED"),
            ] {
                let row = Entry::observed(
                    ("node-1", "Fixture 1"),
                    selected,
                    ProbeKind::Bat,
                    (None, None, Some(RouteRequirement::DirectTcpUnreachable)),
                    &generation,
                    1000,
                    code.map(|code| (code, Layer::Tunnel)),
                )
                .unwrap();
                let expected = selected
                    && matches!(
                        code,
                        None | Some(
                            "ROUTE_UNAVAILABLE"
                                | "TAILSCALE_WAKING"
                                | "TUNNEL_DOWN"
                                | "RECOVERY_EXHAUSTED"
                        )
                    );
                assert_eq!(
                    row.route_requirement.is_some(),
                    expected,
                    "selected={selected}, code={code:?}"
                );
            }
        }
    }
    #[test]
    fn route_requirement_withholds_current_probe_observations() {
        let generation = ProbeGeneration {
            epoch: "a".repeat(32),
            configuration_binding: "c".repeat(64),
            selection_revision: "d".repeat(64),
            generation: 1,
        };
        let probe = ProbeObservation {
            generation: generation.clone(),
            observed_ms: 1000,
            kind: ProbeKind::Bat,
            tunnel: false,
            tls: false,
            auth: Authentication::Unknown,
            bat: false,
            workspace: false,
            connector: false,
            version: None,
            code: None,
            blocking: None,
            reason: None,
        };
        for code in [
            None,
            Some("ROUTE_UNAVAILABLE"),
            Some("TAILSCALE_WAKING"),
            Some("TUNNEL_DOWN"),
        ] {
            let row = Entry::observed(
                ("node-1", "Fixture 1"),
                true,
                ProbeKind::Bat,
                (
                    Some(&probe),
                    None,
                    Some(RouteRequirement::DirectPeerUnknown),
                ),
                &generation,
                1000,
                code.map(|code| (code, Layer::Tunnel)),
            )
            .unwrap();
            assert!(row.route_requirement.is_none(), "code={code:?}");
        }
    }
    #[tokio::test]
    async fn route_requirement_rejects_observed_or_ready_rows() {
        let raw = observed_fixture().await;
        let now = millis(raw["result"]["readiness"]["observed_at"].as_str().unwrap()).unwrap();
        let inventory =
            crate::inventory::Inventory::parse(include_bytes!("../tests/fixtures/inventory.json"))
                .unwrap();
        let generation = ProbeGeneration {
            epoch: "a".repeat(32),
            configuration_binding: "c".repeat(64),
            selection_revision: "d".repeat(64),
            generation: 1,
        };
        let row = Entry::observed(
            ("node-1", "Fixture 1"),
            true,
            ProbeKind::Bat,
            (None, None, Some(RouteRequirement::DirectPeerUnknown)),
            &generation,
            now,
            Some(("ROUTE_UNAVAILABLE", Layer::Tunnel)),
        )
        .unwrap();
        for (field, value) in [
            ("selected", serde_json::json!(false)),
            ("level", serde_json::json!("ready")),
            ("observed_at", serde_json::json!(timestamp(now).unwrap())),
            ("layers", serde_json::json!({"tunnel":true})),
            ("layers", serde_json::json!({"tls":true})),
            ("layers", serde_json::json!({"bat":true})),
            ("layers", serde_json::json!({"workspace":true})),
            ("layers", serde_json::json!({"auth":"unknown"})),
            ("layers", serde_json::json!({"version":"3.2.5"})),
            (
                "route",
                serde_json::json!({"kind":"direct","generation":1,"observed_at":timestamp(now).unwrap()}),
            ),
        ] {
            let mut invalid = raw.clone();
            invalid["result"]["readiness"]["hosts"][0] = serde_json::to_value(&row).unwrap();
            invalid["result"]["readiness"]["hosts"][0][field] = value.clone();
            assert!(
                matches!(
                    parse_fixture_file(&invalid, &inventory, now),
                    Err("STATUS_UNPROVEN")
                ),
                "{field}={value}"
            );
        }
    }
    #[tokio::test]
    async fn route_requirement_impossible_pairs_are_unproven() {
        let raw = observed_fixture().await;
        let now = millis(raw["result"]["readiness"]["observed_at"].as_str().unwrap()).unwrap();
        let inventory =
            crate::inventory::Inventory::parse(include_bytes!("../tests/fixtures/inventory.json"))
                .unwrap();
        for code in [
            None,
            Some("STOP_UNCONFIRMED"),
            Some("TUNNEL_DOWN"),
            Some("PROBE_STALE"),
        ] {
            let mut invalid = raw.clone();
            let row = &mut invalid["result"]["readiness"]["hosts"][0];
            row["route_requirement"] = serde_json::json!(RouteRequirement::DirectTcpUnreachable);
            row["code"] = code.into();
            if code.is_some() {
                row["level"] = "down".into();
                row["blocking"] = "tunnel".into();
            }
            assert!(
                matches!(
                    parse_fixture_file(&invalid, &inventory, now),
                    Err("STATUS_UNPROVEN")
                ),
                "code={code:?}"
            );
        }
    }
    #[tokio::test]
    async fn requirement_rows_match_native_status_fixture() {
        let expected: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/fleet-requirement.json"))
                .unwrap();
        assert_eq!(requirement_fixture().await, expected);
    }
    #[tokio::test]
    #[ignore = "explicit fixture regeneration only"]
    async fn generate_requirement_fixture() {
        println!("FLEET_REQUIREMENT={}", requirement_fixture().await);
    }
    #[tokio::test]
    async fn observed_rows_match_native_status_fixture() {
        let raw: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/fleet-status.json")).unwrap();
        assert_eq!(observed_fixture().await, raw);
    }
    #[tokio::test]
    #[ignore = "explicit fixture regeneration only"]
    async fn generate_route_status_fixture() {
        println!("FLEET_RAW={}", observed_fixture().await);
    }
    #[test]
    fn future_route_time_omits_only_the_route() {
        let generation = ProbeGeneration {
            epoch: "a".repeat(32),
            configuration_binding: "c".repeat(64),
            selection_revision: "d".repeat(64),
            generation: 1,
        };
        let route = RouteObservation {
            kind: RouteKind::Direct,
            generation: generation.clone(),
            confirmed_ms: 2001,
        };
        let probe = ProbeObservation {
            generation: generation.clone(),
            observed_ms: 2000,
            kind: ProbeKind::Bat,
            tunnel: true,
            tls: true,
            auth: Authentication::Ok,
            bat: true,
            workspace: true,
            connector: false,
            version: Some("3.2.5".into()),
            code: None,
            blocking: None,
            reason: None,
        };
        let plain = Entry::observed(
            ("node-1", "Fixture 1"),
            true,
            ProbeKind::Bat,
            (Some(&probe), None, None),
            &generation,
            2000,
            None,
        )
        .unwrap();
        assert_eq!(plain.level, "ready");
        let with_route = Entry::observed(
            ("node-1", "Fixture 1"),
            true,
            ProbeKind::Bat,
            (Some(&probe), Some(&route), None),
            &generation,
            2000,
            None,
        );
        assert!(
            with_route.is_ok(),
            "a future route must not fail the status row"
        );
        assert_eq!(
            serde_json::to_value(with_route.unwrap()).unwrap(),
            serde_json::to_value(plain).unwrap()
        );
    }
    #[test]
    fn observation_clock_is_translated_against_the_producers_unix_anchor() {
        assert_eq!(
            observation_time(1000, 2000, 1_791_460_801_000).unwrap(),
            "2026-10-08T12:00:00Z"
        );
        assert!(observation_time(2001, 2000, 1_791_460_801_000).is_err());
        assert!(observation_time(0, 2000, 1000).is_err());
    }
}
