//! Read-only liveness and bounded lifecycle recovery; never replay application requests.
use crate::{bridge::Bridge, managed::Managed};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};

#[derive(Default)]
struct Backoff {
    failed_since: Option<Instant>,
    retry_at: Option<Instant>,
    attempts: u32,
    blocked: bool,
}
impl Backoff {
    fn due(&mut self, now: Instant, reachable: bool) -> bool {
        if self.blocked {
            return false;
        }
        if reachable {
            *self = Self::default();
            return false;
        }
        let since = *self.failed_since.get_or_insert(now);
        now.duration_since(since) >= Duration::from_secs(10)
            && self.retry_at.is_none_or(|next| now >= next)
    }
    fn settled(&mut self) {
        self.blocked = true;
        self.retry_at = None;
        self.attempts = 0;
    }
    fn failed(&mut self, now: Instant, identity: bool) {
        self.blocked = identity;
        self.retry_at = Some(now + Duration::from_secs(15 * (1u64 << self.attempts.min(2))));
        self.attempts = self.attempts.saturating_add(1);
    }
}

// Keep the loop's visible announcement independently testable.
pub(super) fn announce(bridge: &Bridge, current: u64, managed: &Managed) {
    if !managed.is_held() {
        bridge.recovery_state(current, Some("MANAGED_RECOVERING".into()));
    }
}

pub fn start(bridge: Arc<Bridge>, managed: Arc<Managed>) {
    tauri::async_runtime::spawn(async move {
        let mut backoff = Backoff::default();
        let mut generation = None;
        loop {
            tokio::time::sleep(Duration::from_secs(2)).await;
            let Some((current, reachable)) = bridge.managed_probe().await else {
                backoff = Backoff::default();
                generation = None;
                continue;
            };
            if generation != Some(current) {
                backoff = Backoff::default();
                generation = Some(current);
            }
            if reachable && !backoff.blocked {
                bridge.recovery_state(current, None);
            }
            if !backoff.due(Instant::now(), reachable) {
                continue;
            }
            announce(&bridge, current, &managed);
            let result = bridge.recover_managed(current, managed.clone()).await;
            match result {
                Ok(()) => {
                    backoff = Backoff::default();
                }
                Err(error) if crate::managed::held(&error) => {
                    backoff.settled();
                    bridge.managed_hold(Some(current));
                }
                Err(error) => {
                    let identity = error == "MANAGED_RECOVERY_IDENTITY_CHANGED";
                    bridge.recovery_state(current, Some(error));
                    backoff.failed(Instant::now(), identity);
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hold_settles_without_retries_or_backoff_growth() {
        let mut policy = Backoff::default();
        policy.settled();
        for seconds in [0, 10, 60, 3600] {
            assert!(!policy.due(Instant::now() + Duration::from_secs(seconds), false));
        }
        assert_eq!(policy.attempts, 0);
        assert!(policy.retry_at.is_none());
    }
    #[test]
    fn recovery_has_a_threshold_and_capped_backoff() {
        let now = Instant::now();
        let mut policy = Backoff::default();
        assert!(!policy.due(now, false));
        assert!(!policy.due(now + Duration::from_secs(9), false));
        let mut time = now + Duration::from_secs(10);
        for delay in [15, 30, 60, 60, 60] {
            assert!(policy.due(time, false));
            policy.failed(time, false);
            assert!(!policy.due(time + Duration::from_secs(delay - 1), false));
            time += Duration::from_secs(delay);
        }
        assert!(!policy.due(time, true));
        assert!(!policy.due(time, false));
    }
    #[test]
    fn identity_change_blocks_repeated_ensure() {
        let now = Instant::now();
        let mut policy = Backoff::default();
        policy.failed(now, true);
        assert!(!policy.due(now + Duration::from_secs(3600), false));
        assert!(!policy.due(now + Duration::from_secs(3600), true));
    }
}
