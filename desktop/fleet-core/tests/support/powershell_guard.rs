//! One test-only PowerShell admission gate per test binary.
use std::{
    sync::{Mutex, MutexGuard, TryLockError},
    thread,
    time::{Duration, Instant},
};

static FIXTURES: Mutex<()> = Mutex::new(());
// Queue budget only: four core fixture slots at 60s startup + 15s work each.
// This does not change any child startup/work deadline. A stuck holder must fail
// its waiters instead of hanging the suite. Never nest acquisitions or join a
// fixture waiting for this gate while holding it; acquire before timed children.
const GUARD_WAIT: Duration = Duration::from_secs(300);

pub fn acquire() -> MutexGuard<'static, ()> {
    acquire_with_timeout(&FIXTURES, GUARD_WAIT)
        .unwrap_or_else(|error| panic!("{error} (limit: {GUARD_WAIT:?})"))
}

fn acquire_with_timeout(
    gate: &Mutex<()>,
    wait: Duration,
) -> Result<MutexGuard<'_, ()>, &'static str> {
    let began = Instant::now();
    loop {
        // Check before acquisition too: a late unlock cannot turn an expired
        // wait into admission. try_lock never adds an unbounded blocking wait.
        if began.elapsed() >= wait {
            return Err(
                "PowerShell fixture guard wait timed out; another fixture still holds the guard",
            );
        }
        match gate.try_lock() {
            Ok(held) => return Ok(held),
            // An assertion failure unwinds the owned child before releasing the
            // guard. Preserve the original failure and let the next test run.
            Err(TryLockError::Poisoned(poison)) => return Ok(poison.into_inner()),
            Err(TryLockError::WouldBlock) => {
                thread::sleep(Duration::from_millis(10).min(wait.saturating_sub(began.elapsed())));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::{mpsc, Mutex},
        thread,
        time::{Duration, Instant},
    };

    #[test]
    fn held_guard_times_out_without_entering_and_releases_for_the_next_fixture() {
        let gate = Mutex::new(());
        let held = gate.lock().unwrap();
        let began = Instant::now();
        let wait = Duration::from_millis(100);
        // Same-thread re-entry must fail rather than deadlock. No child is launched.
        let error = acquire_with_timeout(&gate, wait).unwrap_err();
        assert_eq!(
            error,
            "PowerShell fixture guard wait timed out; another fixture still holds the guard"
        );
        assert!(began.elapsed() >= wait);
        assert!(began.elapsed() < Duration::from_secs(5));
        drop(held);
        assert!(acquire_with_timeout(&gate, wait).is_ok());
    }

    #[test]
    fn two_synthetic_fixtures_never_overlap() {
        let held = acquire();
        let (attempt_tx, attempt_rx) = mpsc::channel();
        let (entered_tx, entered_rx) = mpsc::channel();
        let worker = thread::spawn(move || {
            attempt_tx.send(()).unwrap();
            let _held = acquire();
            entered_tx.send(Instant::now()).unwrap();
        });
        let attempted = attempt_rx.recv_timeout(Duration::from_secs(5));
        let while_held = entered_rx.recv_timeout(Duration::from_millis(100));
        let released = Instant::now();
        drop(held);
        // Always release before joining/asserting, including on a broken guard.
        let joined = worker.join();
        assert!(attempted.is_ok());
        assert_eq!(while_held, Err(mpsc::RecvTimeoutError::Timeout));
        assert!(entered_rx.recv_timeout(Duration::from_secs(5)).unwrap() >= released);
        joined.unwrap();
    }

    #[test]
    fn panicking_fixture_does_not_strand_the_next_fixture() {
        let gate = Mutex::new(());
        let failed = std::panic::catch_unwind(|| {
            let _held = acquire_with_timeout(&gate, Duration::from_secs(1)).unwrap();
            panic!("synthetic fixture failure");
        });
        assert!(failed.is_err());
        assert!(acquire_with_timeout(&gate, Duration::from_secs(1)).is_ok());
    }
}
