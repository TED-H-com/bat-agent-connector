//! Read-only prerequisites for configured bootstrap; no process or central effects.
use crate::{
    bootstrap::{Phase, Recipe, SavedStatus},
    configuration::Configuration,
    discovery::{MonitorIdentity, Observation, Ownership},
    migration_io as files,
    ownership::ProbeGeneration,
    process_adapter::{legacy_created, MonitorPointer, TunnelRecord},
    selection_io::Snapshot as Selection,
    supervisor_status::Snapshot,
    tunnel::Plan,
    Result,
};

/// No new automatic intent after historical success or the original finite budget.
/// The caller still proves the exact selected route/readiness before any transport.
#[derive(Debug, PartialEq, Eq)]
pub enum Automatic {
    Hold,
    Prepare,
    Resume(String),
}
pub fn automatic(recipe: &Recipe, saved: Option<&SavedStatus>) -> Result<Automatic> {
    if !recipe.auto_ensure() {
        return Ok(Automatic::Hold);
    }
    match saved {
        Some(saved) if saved.status.phase == Phase::ServiceRunning || saved.status.queries >= 4 => {
            Ok(Automatic::Hold)
        }
        Some(saved) if saved.recipe_binding != recipe.binding() => Err("BOOTSTRAP_UNRESOLVED"),
        Some(saved) => Ok(Automatic::Resume(saved.status.request_id.clone())),
        None => Ok(Automatic::Prepare),
    }
}

/// Fresh service-unavailable observation only. Auth/version/scope/unknown failures
/// never authorize a service repair; a healthy central endpoint needs no ensure.
pub fn unavailable(
    snapshot: &Snapshot,
    selected: &Selection,
    generation: &ProbeGeneration,
    now: u64,
) -> Result<()> {
    if !snapshot.is_fresh(now)?
        || snapshot.lifecycle != "running"
        || snapshot.monitor_epoch != generation.epoch
        || snapshot.configuration_binding != generation.configuration_binding
        || selected.configuration_binding() != generation.configuration_binding
        || selected.revision != generation.selection_revision
        || snapshot.applied_selection_revision != selected.revision
    {
        return Err("BOOTSTRAP_READINESS_UNPROVEN");
    }
    let row = snapshot
        .entries
        .iter()
        .find(|r| r.name == "connector")
        .ok_or("BOOTSTRAP_READINESS_UNPROVEN")?;
    if row.level == "ready" {
        return Err("BOOTSTRAP_NOT_NEEDED");
    }
    let observed = row
        .observed_at
        .as_deref()
        .ok_or("BOOTSTRAP_READINESS_UNPROVEN")?;
    let observed =
        time::OffsetDateTime::parse(observed, &time::format_description::well_known::Rfc3339)
            .map_err(|_| "BOOTSTRAP_READINESS_UNPROVEN")?
            .unix_timestamp_nanos()
            / 1_000_000;
    if !u64::try_from(observed)
        .ok()
        .and_then(|at| now.checked_sub(at))
        .is_some_and(|age| age <= 60_000)
    {
        return Err("BOOTSTRAP_READINESS_UNPROVEN");
    }
    if !row.selected
        || row.stale
        || row.observed_at.is_none()
        || !["down", "degraded"].contains(&row.level.as_str())
        || !matches!(
            row.code.as_deref(),
            Some("CONNECTOR_UNAVAILABLE" | "PROBE_TIMEOUT")
        )
    {
        return Err("BOOTSTRAP_READINESS_UNPROVEN");
    }
    Ok(())
}
/// Exact current-login tunnel process and original monitor, not a TCP listener.
/// Both PS and native use the reviewed same argv; ambiguity/missing evidence refuses.
pub fn route(
    configuration: &Configuration,
    selected: &Selection,
    owner: &MonitorIdentity,
    observation: &impl Observation,
    ssh_executable: &str,
) -> Result<()> {
    configuration.verify_current()?;
    let epoch = owner
        .instance
        .as_deref()
        .ok_or("BOOTSTRAP_OWNER_UNPROVEN")?;
    if owner.legacy
        || owner.ownership != Ownership::CurrentLogin
        || owner.process.login != observation.current_login()?
    {
        return Err("BOOTSTRAP_OWNER_UNPROVEN");
    }
    let alias = configuration.inventory.connector()["ssh_alias"]
        .as_str()
        .ok_or("BOOTSTRAP_ROUTE_UNPROVEN")?;
    let plan = Plan::new(configuration, selected, "connector", alias, epoch)?;
    let pointer = MonitorPointer {
        pid: owner.process.pid,
        created: legacy_created(owner.process.created_filetime)?,
        instance: epoch.into(),
    };
    let mut proven = None;
    for directory in &owner.directories {
        let path = directory.join("fleet-tunnel-owners/connector.json");
        let Some(raw) = files::read(&path, 262144)? else {
            continue;
        };
        let record = TunnelRecord::parse(&raw, Some(&pointer))?;
        let actual = observation
            .observe(record.process.pid)?
            .ok_or("BOOTSTRAP_ROUTE_UNPROVEN")?;
        if !record.process.matches(&actual.tunnel_evidence()?)
            || record
                .created_filetime
                .is_some_and(|birth| birth != actual.created_filetime)
            || actual.login != owner.process.login
            || !crate::tunnel::windows_executable_eq(&actual.executable, ssh_executable)
            || actual.arguments != plan.arguments()
            || record
                .origin
                .as_ref()
                .is_none_or(|origin| origin.pid != pointer.pid || origin.created != pointer.created)
            || proven.as_ref().is_some_and(|prior| prior != &actual)
            || files::read(&path, 262144)?.as_deref() != Some(&raw)
        {
            return Err("BOOTSTRAP_ROUTE_UNPROVEN");
        }
        proven = Some(actual);
    }
    proven.ok_or("BOOTSTRAP_ROUTE_UNPROVEN").map(|_| ())
}
