//! Handle-based process decisions. No paths/PIDs in this API are WebView inputs.
//! The runtime must hold its account monitor mutex and revalidate configuration before effects.
use crate::ownership::{
    epoch_valid, origin_state, sid_valid, ticks_valid, ProcessEvidence, ProcessState,
};
use crate::{strict_json, Result};
use serde_json::Value;

pub const FILETIME_EPOCH_TICKS: u64 = 504_911_232_000_000_000;
pub const MAX_DATETIME_TICKS: u64 = 3_155_378_975_999_999_999;

/// FILETIME and DateTime both count 100ns units, from 1601 and 0001 respectively.
pub fn datetime_ticks(filetime: u64) -> Result<u64> {
    filetime
        .checked_add(FILETIME_EPOCH_TICKS)
        .filter(|n| *n <= MAX_DATETIME_TICKS && filetime > 0)
        .ok_or("OWNER_UNPROVEN")
}
/// Win32_Process.CreationDate uses CIM's six fractional digits (microseconds).
/// Preserve the older PS wire representation; retain native FILETIME separately.
pub fn legacy_created(filetime: u64) -> Result<String> {
    Ok((datetime_ticks(filetime)? / 10 * 10).to_string())
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LoginIdentity {
    pub owner_sid: String,
    pub session_id: u32,
}
impl LoginIdentity {
    pub fn valid(&self) -> bool {
        sid_valid(&self.owner_sid)
    }
}

/// Native-only evidence; never serialize executable/argv into UI status or logs.
#[derive(Clone, PartialEq, Eq)]
pub struct ProcessSnapshot {
    pub pid: u32,
    pub created_filetime: u64,
    pub executable: String,
    pub arguments: Vec<String>,
    pub login: LoginIdentity,
}
impl ProcessSnapshot {
    pub fn valid(&self) -> bool {
        self.pid > 0
            && datetime_ticks(self.created_filetime).is_ok()
            && self.login.valid()
            && !self.executable.is_empty()
            && self.executable.len() <= 32768
            && !self.executable.contains('\0')
            && self.arguments.len() <= 128
            && self
                .arguments
                .iter()
                .all(|s| s.len() <= 32768 && !s.contains('\0'))
            && self
                .arguments
                .iter()
                .map(|s| s.encode_utf16().count() + 1)
                .sum::<usize>()
                <= 32768
    }
    pub fn tunnel_evidence(&self) -> Result<ProcessEvidence> {
        if !self.valid() {
            return Err("OWNER_UNPROVEN");
        }
        let epochs: Vec<_> = self
            .arguments
            .iter()
            .filter_map(|a| a.strip_prefix("SetEnv=BAT_FLEET_MONITOR="))
            .collect();
        if epochs.len() != 1 || !epoch_valid(epochs[0]) {
            return Err("OWNER_UNPROVEN");
        }
        Ok(ProcessEvidence {
            pid: self.pid,
            created: legacy_created(self.created_filetime)?,
            executable: self.executable.clone(),
            arguments: self.arguments.clone(),
            monitor_instance: epochs[0].into(),
            owner_sid: self.login.owner_sid.clone(),
            session_id: self.login.session_id,
        })
    }
}

#[derive(Clone)]
pub struct Origin {
    pub pid: u32,
    pub created: String,
}
impl Origin {
    fn valid(&self) -> bool {
        self.pid > 0 && ticks_valid(&self.created)
    }
}
/// Fields needed from fleet-monitor.json for pre-parent-copy PowerShell records.
#[derive(Clone)]
pub struct MonitorPointer {
    pub pid: u32,
    pub created: String,
    pub instance: String,
}
#[derive(Clone)]
pub struct TunnelRecord {
    pub process: ProcessEvidence,
    /// Native records add this exact kernel timestamp. PowerShell ignores the additive field.
    pub created_filetime: Option<u64>,
    pub origin: Option<Origin>,
}
impl TunnelRecord {
    /// Existing flat PS records remain readable. Partial parent evidence never falls back.
    pub fn parse(bytes: &[u8], legacy_monitor: Option<&MonitorPointer>) -> Result<Self> {
        let Value::Object(mut fields) = strict_json::parse(bytes, 256 * 1024)? else {
            return Err("OWNER_UNPROVEN");
        };
        let pid = fields.remove("monitor_pid");
        let created = fields.remove("monitor_created");
        let raw = fields.remove("created_filetime");
        let native = match raw {
            None => None,
            Some(Value::String(v)) if ticks_valid(&v) => {
                Some(v.parse::<u64>().map_err(|_| "OWNER_UNPROVEN")?)
            }
            _ => return Err("OWNER_UNPROVEN"),
        };
        let process: ProcessEvidence =
            serde_json::from_value(Value::Object(fields)).map_err(|_| "OWNER_UNPROVEN")?;
        if !process.valid() {
            return Err("OWNER_UNPROVEN");
        }
        if native.is_some_and(|n| legacy_created(n).ok().as_ref() != Some(&process.created)) {
            return Err("OWNER_UNPROVEN");
        }
        let origin = match (pid, created) {
            (None, None) => legacy_monitor
                .filter(|p| p.instance == process.monitor_instance && epoch_valid(&p.instance))
                .map(|p| Origin {
                    pid: p.pid,
                    created: p.created.clone(),
                }),
            (Some(Value::Number(pid)), Some(Value::String(created))) => Some(Origin {
                pid: pid
                    .as_u64()
                    .and_then(|p| u32::try_from(p).ok())
                    .ok_or("OWNER_UNPROVEN")?,
                created,
            }),
            _ => return Err("OWNER_UNPROVEN"),
        };
        if origin.as_ref().is_some_and(|p| !p.valid()) {
            return Err("OWNER_UNPROVEN");
        }
        Ok(Self {
            process,
            created_filetime: native,
            origin,
        })
    }
    pub fn from_snapshot(child: &ProcessSnapshot, parent: &ProcessSnapshot) -> Result<Self> {
        if !parent.valid() || child.login != parent.login {
            return Err("OWNER_UNPROVEN");
        }
        Ok(Self {
            process: child.tunnel_evidence()?,
            created_filetime: Some(child.created_filetime),
            origin: Some(Origin {
                pid: parent.pid,
                created: legacy_created(parent.created_filetime)?,
            }),
        })
    }
    pub fn document(&self) -> Result<Value> {
        let mut doc = serde_json::to_value(&self.process).map_err(|_| "OWNER_UNPROVEN")?;
        if let Some(n) = self.created_filetime {
            doc["created_filetime"] = n.to_string().into();
        }
        if let Some(p) = &self.origin {
            doc["monitor_pid"] = p.pid.into();
            doc["monitor_created"] = p.created.clone().into();
        }
        // A document produced here must pass the same bounded consumer validation.
        Self::parse(
            &serde_json::to_vec(&doc).map_err(|_| "OWNER_UNPROVEN")?,
            None,
        )?;
        Ok(doc)
    }
    pub(crate) fn matches(&self, observed: &ProcessSnapshot) -> bool {
        observed
            .tunnel_evidence()
            .is_ok_and(|p| self.process.matches(&p))
            && self
                .created_filetime
                .is_none_or(|birth| birth == observed.created_filetime)
    }
}

/// The implementation owns one OS process handle throughout snapshot and termination.
pub trait HeldProcess {
    fn snapshot(&self) -> Result<ProcessSnapshot>;
    /// Must terminate this held handle, wait at most two seconds and report uncertain exit.
    fn terminate_and_wait(&mut self) -> Result<()>;
}
pub trait ProcessAccess {
    type Held: HeldProcess;
    fn current_login(&self) -> Result<LoginIdentity>;
    /// None means proven absent/exited; failures/access denied are Err, never None.
    fn open_for_stop(&self, pid: u32) -> Result<Option<Self::Held>>;
    fn state(&self, pid: u32) -> ProcessState;
}
pub enum StopMode<'a> {
    Owned { current_epoch: &'a str },
    Orphan,
}
#[derive(Debug, PartialEq, Eq)]
pub enum StopOutcome {
    AlreadyGone,
    Stopped,
}

/// Call only while holding the account monitor mutex. No kill-by-PID or retry fallback.
pub fn stop_tunnel<A: ProcessAccess>(
    access: &A,
    record: &TunnelRecord,
    mode: StopMode<'_>,
) -> Result<StopOutcome> {
    if !record.process.valid() {
        return Err("OWNER_UNPROVEN");
    }
    let login = access.current_login()?;
    if !login.valid()
        || login.owner_sid != record.process.owner_sid
        || login.session_id != record.process.session_id
    {
        return Err("OTHER_LOGIN_OWNER");
    }
    if let StopMode::Owned { current_epoch } = mode {
        if !epoch_valid(current_epoch) || current_epoch != record.process.monitor_instance {
            return Err("OWNER_UNPROVEN");
        }
    }
    let Some(mut held) = access.open_for_stop(record.process.pid)? else {
        return Ok(StopOutcome::AlreadyGone);
    };
    let first = held.snapshot()?;
    if !record.matches(&first) {
        return Err("OWNER_UNPROVEN");
    }
    if matches!(mode, StopMode::Orphan) {
        let parent = record
            .origin
            .as_ref()
            .filter(|p| p.valid())
            .ok_or("OWNER_UNPROVEN")?;
        if origin_state(parent.pid, &parent.created, &access.state(parent.pid))
            != ProcessState::Dead
        {
            return Err("ORIGIN_NOT_DEAD");
        }
    }
    // Recheck after any parent query. Both observations and the effect use the same handle.
    let final_snapshot = held.snapshot()?;
    if first != final_snapshot || !record.matches(&final_snapshot) {
        return Err("OWNER_UNPROVEN");
    }
    held.terminate_and_wait()?;
    Ok(StopOutcome::Stopped)
}
