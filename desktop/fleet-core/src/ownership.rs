//! Pure process evidence decisions. A true match is necessary, never sufficient to kill:
//! the Windows adapter must hold the original process handle through its final recheck.
use crate::Result;
use serde::{Deserialize, Serialize};

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ProcessEvidence {
    pub pid: u32,
    /// UTC DateTime ticks, compatible with existing PowerShell ownership records.
    pub created: String,
    pub executable: String,
    pub arguments: Vec<String>,
    pub monitor_instance: String,
    pub owner_sid: String,
    pub session_id: u32,
}

pub fn sid_valid(sid: &str) -> bool {
    sid.len() <= 184 && sid.starts_with("S-1-") && {
        let parts: Vec<_> = sid[4..].split('-').collect();
        parts.len() >= 2
            && parts.iter().all(|s| {
                !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()) && s.parse::<u64>().is_ok()
            })
    }
}
pub fn epoch_valid(epoch: &str) -> bool {
    epoch.len() == 32
        && epoch
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn ticks_valid(ticks: &str) -> bool {
    ticks.len() <= 19
        && !ticks.starts_with('0')
        && ticks.bytes().all(|b| b.is_ascii_digit())
        && ticks.parse::<u64>().is_ok()
        && !ticks.is_empty()
}
pub fn monitor_mutex(sid: &str) -> Result<String> {
    if !sid_valid(sid) {
        return Err("OWNER_UNPROVEN");
    }
    Ok(format!("Global\\BatFleetMonitor_{sid}"))
}
impl ProcessEvidence {
    pub fn valid(&self) -> bool {
        self.pid > 0
            && ticks_valid(&self.created)
            && sid_valid(&self.owner_sid)
            && epoch_valid(&self.monitor_instance)
            && !self.executable.is_empty()
            && self.executable.len() <= 32768
            && !self.executable.contains('\0')
            && !self.arguments.is_empty()
            && self.arguments.len() <= 128
            && self
                .arguments
                .iter()
                .all(|s| s.len() <= 32768 && !s.contains('\0'))
            && self
                .arguments
                .iter()
                .filter(|s| s.starts_with("SetEnv=BAT_FLEET_MONITOR="))
                .eq([&format!(
                    "SetEnv=BAT_FLEET_MONITOR={}",
                    self.monitor_instance
                )])
    }
    pub fn matches(&self, actual: &Self) -> bool {
        self.valid()
            && actual.valid()
            && self.pid == actual.pid
            && self.created == actual.created
            && self.executable.eq_ignore_ascii_case(&actual.executable)
            && self.arguments == actual.arguments
            && self.monitor_instance == actual.monitor_instance
            && self.owner_sid == actual.owner_sid
            && self.session_id == actual.session_id
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProcessState {
    Unknown,
    Dead,
    Live { created: String },
}
/// A different creation time proves the old incarnation ended, never ownership of the new PID.
pub fn origin_state(
    recorded_pid: u32,
    recorded_created: &str,
    observed: &ProcessState,
) -> ProcessState {
    if recorded_pid == 0 || !ticks_valid(recorded_created) {
        return ProcessState::Unknown;
    }
    match observed {
        ProcessState::Dead => ProcessState::Dead,
        ProcessState::Live { created } if ticks_valid(created) && created != recorded_created => {
            ProcessState::Dead
        }
        ProcessState::Live { created } if created == recorded_created => observed.clone(),
        _ => ProcessState::Unknown,
    }
}

#[derive(Clone, Debug)]
pub struct Recovery {
    attempts: u8,
    next_ms: u64,
}
impl Recovery {
    pub fn new(now_ms: u64) -> Self {
        Self {
            attempts: 0,
            next_ms: now_ms.saturating_add(5000),
        }
    }
    pub fn due(&self, now_ms: u64) -> bool {
        self.attempts < 3 && now_ms >= self.next_ms
    }
    pub fn continuous_failed(&mut self, now_ms: u64) {
        let delay = 5000u64
            .saturating_mul(1u64 << self.attempts.min(4))
            .min(60000);
        self.next_ms = now_ms.saturating_add(delay);
    }
    pub fn continuous_due(&self, now_ms: u64) -> bool {
        now_ms >= self.next_ms
    }
    pub fn continuous_attempt(&mut self, now_ms: u64) -> Result<()> {
        if !self.continuous_due(now_ms) {
            return Err("RECOVERY_NOT_DUE");
        }
        self.attempts = self.attempts.saturating_add(1);
        let delay = 5000u64
            .saturating_mul(1u64 << self.attempts.min(4))
            .min(60000);
        self.next_ms = now_ms.saturating_add(delay);
        Ok(())
    }
    pub fn exhausted(&self) -> bool {
        self.attempts >= 3
    }
    pub fn attempt(&mut self, now_ms: u64) -> Result<()> {
        if !self.due(now_ms) {
            return Err("RECOVERY_NOT_DUE");
        }
        self.attempts += 1;
        self.next_ms = now_ms.saturating_add(if self.attempts == 1 { 15_000 } else { 45_000 });
        Ok(())
    }
}

/// Readiness may only be published for the exact selection/configuration it observed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProbeGeneration {
    pub epoch: String,
    pub configuration_binding: String,
    pub selection_revision: String,
    pub generation: u64,
}
pub fn probe_current(
    observed: &ProbeGeneration,
    current: &ProbeGeneration,
    observed_ms: u64,
    now_ms: u64,
) -> bool {
    observed == current && now_ms >= observed_ms && now_ms - observed_ms <= 60_000
}
