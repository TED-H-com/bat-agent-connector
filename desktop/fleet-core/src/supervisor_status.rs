//! Safe PS-compatible readiness publication and strict consumer validation.
use crate::{
    configuration::Configuration,
    ownership::{epoch_valid, ProbeGeneration},
    probe::{self, Authentication, Layer, Level, ProbeKind, ProbeObservation},
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
}
impl Entry {
    pub(crate) fn observed(
        identity: (&str, &str),
        selected: bool,
        kind: ProbeKind,
        observation: Option<&ProbeObservation>,
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
        if selected {
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
        Ok(row)
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
            let label = expected
                .iter()
                .find(|(id, _)| *id == row.name)
                .map(|(_, label)| *label)
                .ok_or("STATUS_UNPROVEN")?;
            if !seen.insert(row.name.clone())
                || !["off", "ready", "degraded", "down"].contains(&row.level.as_str())
                || row.selected == (row.level == "off")
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
            row.label = label.into(); // Never trust file-supplied presentation text.
            let old = match &row.observed_at {
                Some(at) => now.checked_sub(millis(at)?).ok_or("STATUS_UNPROVEN")? > 60_000,
                None => true,
            };
            if row.selected && (file_stale || row.stale || old && row.level == "ready") {
                row.level = "degraded".into();
                row.blocking = Some(Layer::Stale);
                row.code = Some("PROBE_STALE".into());
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

    #[test]
    fn observed_rows_match_native_status_fixture() {
        // De-identified Context::status input for the Tauri sanitizer test.
        // Build with the real producer, including Connector's false BAT layers.
        let raw: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/fleet-status.json")).unwrap();
        let status = &raw["result"];
        let now = millis(status["readiness"]["observed_at"].as_str().unwrap()).unwrap();
        let generation = ProbeGeneration {
            epoch: "a".repeat(32),
            configuration_binding: "c".repeat(64),
            selection_revision: "d".repeat(64),
            generation: 1,
        };
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
            let row = Entry::observed(
                (
                    expected["name"].as_str().unwrap(),
                    expected["label"].as_str().unwrap(),
                ),
                true,
                probe.kind,
                Some(&probe),
                &generation,
                now,
                None,
            )
            .unwrap();
            assert_eq!(serde_json::to_value(row).unwrap(), *expected);
        }
    }
}
