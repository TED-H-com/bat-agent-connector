//! Read-only configured tunnel evidence. No observation grants stop authority.
use super::*;
use crate::process_adapter::LoginIdentity;
use std::collections::BTreeMap;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TunnelKind {
    Owner,
    LaunchIntent,
    StopIntent,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TunnelState {
    Active,
    Foreign,
    Malformed,
    Unreadable,
    Residue,
}
#[derive(Clone, Debug)]
pub struct TunnelEntry {
    pub path: PathBuf,
    pub kind: TunnelKind,
    pub state: TunnelState,
}
#[derive(Clone, Debug)]
pub struct TunnelObservation {
    /// True only after all configured scopes and candidates were read without gaps.
    /// An empty list alone is not coverage; complete coverage does not mean safe.
    pub complete: bool,
    pub entries: Vec<TunnelEntry>,
}
pub(crate) fn stop_intent_bytes(bytes: &[u8], record: &TunnelRecord) -> Result<Vec<u8>> {
    serde_json::to_vec(
        &serde_json::json!({"schema_version":1,"record_digest":crate::digest(bytes),
        "pid":record.process.pid,"created":record.process.created}),
    )
    .map_err(|_| "OWNER_UNPROVEN")
}
/// Shared with recovery before its original effects. Stop markers retain exact-byte
/// comparison; a mismatch alone cannot distinguish corruption from a different receipt.
pub(crate) fn classify_intent(
    kind: TunnelKind,
    bytes: &[u8],
    name: &str,
    owner: Option<(&[u8], &TunnelRecord)>,
) -> Result<()> {
    if let (TunnelKind::StopIntent, Some((owner_bytes, record))) = (kind, owner) {
        return if bytes == stop_intent_bytes(owner_bytes, record)? {
            Ok(())
        } else {
            Err("TUNNEL_RECORD_CHANGED")
        };
    }
    let doc = strict_json::parse(bytes, BOUND)?;
    let Some((_, record)) = owner else {
        return Err("OWNER_UNPROVEN");
    };
    if doc["schema_version"] != 1
        || doc["name"] != name
        || doc["monitor_instance"] != record.process.monitor_instance
        || doc["arguments"]
            != serde_json::to_value(&record.process.arguments).map_err(|_| "OWNER_UNPROVEN")?
        || !record
            .origin
            .as_ref()
            .is_some_and(|o| doc["monitor_pid"] == o.pid && doc["monitor_created"] == o.created)
    {
        return Err("TUNNEL_START_UNSETTLED");
    }
    Ok(())
}
fn owner_state(record: &TunnelRecord, login: &LoginIdentity, os: &impl Observation) -> TunnelState {
    let same_login = record.process.owner_sid == login.owner_sid
        && record.process.session_id == login.session_id;
    match os.observe(record.process.pid) {
        Ok(None) if same_login => TunnelState::Residue,
        Ok(Some(p)) if record.matches(&p) => {
            if same_login {
                TunnelState::Active
            } else {
                TunnelState::Foreign
            }
        }
        _ => TunnelState::Unreadable,
    }
}
/// Caller retains Launcher then Monitor exclusion and proves the configured scope.
/// Only the predecessor pointer is needed from scan's lease: here it is read from
/// each configured directory, with exact-byte rechecks. No lease is constructed.
/// Unmatched intents remain unreadable: their owner/child cannot be inferred.
pub fn observe_tunnels(roaming: &Path, os: &impl Observation) -> Result<TunnelObservation> {
    let login = os.current_login()?;
    if !login.valid() {
        return Err("OWNER_UNPROVEN");
    }
    let mut result = TunnelObservation {
        complete: false,
        entries: Vec::new(),
    };
    if !roaming
        .symlink_metadata()
        .is_ok_and(|m| m.is_dir() && regular(&m))
    {
        return Ok(result);
    }
    let mut candidates = Vec::new();
    let mut pointers = Vec::new();
    let scan = scan_with(
        roaming,
        |dir| {
            match dir.symlink_metadata() {
                Ok(m) if m.is_dir() && regular(&m) => (),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
                _ => return Err("OWNER_UNPROVEN"),
            }
            let path = dir.join("fleet-monitor.json");
            let bytes = read(&path)?;
            let pointer = bytes
                .as_deref()
                .map(MonitorRecord::parse)
                .transpose()?
                .map(|r| MonitorPointer {
                    pid: r.pid,
                    created: r.created,
                    instance: r.instance,
                });
            pointers.push((path, bytes));
            Ok(pointer)
        },
        |path, child, id| {
            let kind = match child {
                "fleet-tunnel-owners" => TunnelKind::Owner,
                "fleet-tunnel-intents" => TunnelKind::LaunchIntent,
                _ => TunnelKind::StopIntent,
            };
            candidates.push((path.to_path_buf(), kind, id.map(str::to_owned)));
        },
    );
    let Ok(scan) = scan else {
        return Ok(result);
    };
    result.complete = true;
    let owners: BTreeMap<_, _> = scan
        .owners
        .iter()
        .map(|file| (file.path.clone(), file))
        .collect();
    for (path, kind, id) in candidates {
        let mut state = TunnelState::Unreadable;
        if let Some(id) = id {
            let owner = if kind == TunnelKind::Owner {
                path.clone()
            } else {
                path.parent()
                    .and_then(Path::parent)
                    .ok_or("OWNER_UNPROVEN")?
                    .join("fleet-tunnel-owners")
                    .join(format!("{id}.json"))
            };
            let file = owners.get(&owner).copied();
            state = if kind == TunnelKind::Owner {
                match file.map(|file| &file.record) {
                    Some(Err(_)) => TunnelState::Malformed,
                    Some(Ok(record)) => owner_state(record, &login, os),
                    None => TunnelState::Unreadable,
                }
            } else {
                match read(&path) {
                    Ok(Some(bytes)) => {
                        let binding = file.and_then(|file| {
                            file.record
                                .as_ref()
                                .ok()
                                .map(|r| (file.bytes.as_slice(), r))
                        });
                        let checked = classify_intent(kind, &bytes, &id, binding);
                        let state = match checked {
                            Ok(()) => binding.map_or(TunnelState::Unreadable, |(_, r)| {
                                owner_state(r, &login, os)
                            }),
                            Err("JSON_INVALID" | "JSON_BOUND") => TunnelState::Malformed,
                            Err("TUNNEL_START_UNSETTLED")
                                if binding.is_some_and(|(_, r)| r.origin.is_some()) =>
                            {
                                TunnelState::Malformed
                            }
                            _ => TunnelState::Unreadable,
                        };
                        if read(&path).ok().flatten().as_deref() == Some(&bytes) {
                            state
                        } else {
                            TunnelState::Unreadable
                        }
                    }
                    _ => TunnelState::Unreadable,
                }
            };
            if let Some(file) = file {
                if read(&file.path).ok().flatten().as_deref() != Some(&file.bytes) {
                    state = TunnelState::Unreadable;
                }
            }
        }
        result.complete &= state != TunnelState::Unreadable;
        result.entries.push(TunnelEntry { path, kind, state });
    }
    if os.current_login()? != login
        || pointers
            .iter()
            .any(|(path, bytes)| read(path).ok().as_ref() != Some(bytes))
    {
        return Err("OWNER_UNPROVEN");
    }
    result.entries.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(result)
}
