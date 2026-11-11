//! Test-only native observations. Formatting/delta logic also runs on Linux.
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug)]
struct Cpu {
    created: u64,
    user: u64,
    kernel: u64,
}
#[derive(Clone, Debug)]
struct Process {
    pid: u32,
    parent: u32,
    image: String,
    cpu: Option<Cpu>,
}
#[derive(Clone, Copy, Debug)]
struct System {
    idle: u64,
    kernel: u64,
    user: u64,
}
#[derive(Clone, Copy, Debug)]
struct Memory {
    load: u32,
    available: u64,
}
#[derive(Debug)]
pub(super) struct Snapshot {
    at: Duration,
    finished: Duration,
    utc: u64,
    system: Option<System>,
    memory: Option<Memory>,
    processes: Vec<Process>,
    complete: bool,
}
pub(super) type Sample = Result<Snapshot, &'static str>;

#[cfg(windows)]
#[path = "powershell_snapshot_windows.rs"]
mod windows;

#[cfg(windows)]
pub(super) fn capture(began: Instant) -> Sample {
    static BUSY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    bounded(&BUSY, Duration::from_millis(750), move |until| {
        // Leave time to return a partial enumeration before the caller bound.
        windows::collect(began, until - Duration::from_millis(250))
    })
}
#[cfg(not(windows))]
pub(super) fn capture(_: Instant) -> Sample {
    Err("unavailable (non-Windows)")
}

// OS calls cannot be interrupted safely. Bound the caller's wait and permit at
// most one collector per binary, even if an OS call outlives that wait. The
// collector owns all handles, checks its deadline between calls, and drops them.
#[cfg(any(windows, test))]
fn bounded(
    busy: &'static std::sync::atomic::AtomicBool,
    budget: Duration,
    collect: impl FnOnce(Instant) -> Sample + Send + 'static,
) -> Sample {
    use std::sync::{atomic::Ordering, mpsc};
    if busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Err("previous native snapshot still in flight");
    }
    struct Release(&'static std::sync::atomic::AtomicBool);
    impl Drop for Release {
        fn drop(&mut self) {
            self.0.store(false, Ordering::Release);
        }
    }
    let permit = Release(busy);
    let until = Instant::now() + budget;
    let (tx, rx) = mpsc::sync_channel(1);
    std::thread::Builder::new()
        .name("fixture-system-snapshot".into())
        .spawn(move || {
            let _permit = permit;
            let _ = tx.send(collect(until));
        })
        .map_err(|_| "snapshot worker unavailable")?;
    rx.recv_timeout(until.saturating_duration_since(Instant::now()))
        .map_err(|_| "snapshot timed out (750ms caller bound)")?
}

fn sample_header(out: &mut String, label: &str, sample: Option<&Sample>) {
    use std::fmt::Write;
    match sample {
        Some(Ok(s)) => {
            writeln!(
                out,
                "snapshot_{label}=@{:?}..{:?} complete:{} processes:{}",
                s.at,
                s.finished,
                s.complete,
                s.processes.len()
            )
            .unwrap();
            match s.memory {
                Some(m) => writeln!(
                    out,
                    "memory_{label}=load:{}% available:{}B",
                    m.load, m.available
                )
                .unwrap(),
                None => writeln!(out, "memory_{label}=unavailable").unwrap(),
            }
        }
        Some(Err(reason)) => {
            writeln!(out, "snapshot_{label}={reason}").unwrap();
        }
        None => {
            writeln!(out, "snapshot_{label}=not reached").unwrap();
        }
    }
}
fn delta(p: &Process, previous: Option<&Process>, window_start: u64) -> Option<(u64, u64)> {
    let cpu = p.cpu?;
    if let Some(old) = previous
        .and_then(|p| p.cpu)
        .filter(|old| old.created == cpu.created)
    {
        Some((
            cpu.user.checked_sub(old.user)?,
            cpu.kernel.checked_sub(old.kernel)?,
        ))
    } else if cpu.created >= window_start {
        // A process born inside the window has zero CPU at the window start.
        Some((cpu.user, cpu.kernel))
    } else {
        None
    } // Missing/inaccessible/reused baseline is not zero CPU.
}
fn image_name(image: &str) -> String {
    // Image basenames only, no command line or path; ASCII escaping and a byte
    // cap also keep untrusted names from injecting lines or expanding the report.
    image
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or("")
        .chars()
        .flat_map(char::escape_default)
        .take(48)
        .collect()
}
pub(super) fn describe(ready: Option<&Sample>, breach: &Sample, child: u32) -> String {
    use std::{
        collections::{HashMap, HashSet},
        fmt::Write,
    };
    let mut out = String::new();
    sample_header(&mut out, "READY", ready);
    sample_header(&mut out, "breach", Some(breach));
    let before = ready.and_then(|s| s.as_ref().ok());
    let after = breach.as_ref().ok();
    let previous: HashMap<_, _> = before
        .into_iter()
        .flat_map(|s| &s.processes)
        .map(|p| (p.pid, p))
        .collect();
    let change = |p: &Process| before.and_then(|s| delta(p, previous.get(&p.pid).copied(), s.utc));
    let busy = before
        .and_then(|s| s.system)
        .zip(after.and_then(|s| s.system))
        .and_then(|(a, b)| {
            let total = b
                .kernel
                .checked_sub(a.kernel)?
                .checked_add(b.user.checked_sub(a.user)?)?;
            let idle = b.idle.checked_sub(a.idle)?;
            (total > 0 && idle <= total).then(|| (total - idle) as f64 / total as f64 * 100.0)
        });
    match busy {
        Some(value) => writeln!(out, "system_busy={value:.2}% (kernel includes idle)").unwrap(),
        None => writeln!(out, "system_busy=unavailable").unwrap(),
    }
    if let Some(after) = after {
        if before.is_some() {
            let mut ranked: Vec<_> = after
                .processes
                .iter()
                .filter_map(|p| change(p).map(|(u, k)| (p, u, k)))
                .collect();
            ranked.sort_by_key(|(p, u, k)| (std::cmp::Reverse(u.saturating_add(*k)), p.pid));
            for (p, u, k) in ranked.into_iter().take(8) {
                writeln!(
                    out,
                    "top_cpu pid:{} image:{} delta_user_ms:{:.3} delta_kernel_ms:{:.3}",
                    p.pid,
                    image_name(&p.image),
                    u as f64 / 10000.0,
                    k as f64 / 10000.0
                )
                .unwrap();
            }
        } else {
            out.push_str("top_cpu=unavailable (no READY baseline)\n");
        }
        let by_pid: HashMap<_, _> = after.processes.iter().map(|p| (p.pid, p)).collect();
        let mut children: HashMap<u32, Vec<u32>> = HashMap::new();
        for p in &after.processes {
            let parent = by_pid.get(&p.parent).and_then(|v| v.cpu);
            if !parent
                .zip(p.cpu)
                .is_some_and(|(a, b)| a.created > b.created)
            {
                children.entry(p.parent).or_default().push(p.pid);
            }
        }
        let mut family = HashSet::from([child]);
        let mut pending = vec![child];
        while let Some(pid) = pending.pop() {
            for &kid in children.get(&pid).into_iter().flatten() {
                if family.insert(kid) {
                    pending.push(kid);
                }
            }
        }
        let mut omitted = 0;
        for p in after
            .processes
            .iter()
            .filter(|p| p.pid != child && family.contains(&p.pid))
        {
            let Some(c) = p.cpu else { continue }; // inaccessible/exited entries skipped
            let change = change(p)
                .map(|(u, k)| format!("{:.3}", (u as f64 + k as f64) / 10000.0))
                .unwrap_or_else(|| "unavailable".into());
            let line = format!(
                "descendant pid:{} image:{} user_ms:{:.3} kernel_ms:{:.3} delta_ms:{}\n",
                p.pid,
                image_name(&p.image),
                c.user as f64 / 10000.0,
                c.kernel as f64 / 10000.0,
                change
            );
            if out.len() + line.len() <= 3850 {
                out.push_str(&line);
            } else {
                omitted += 1;
            }
        }
        writeln!(out,"descendants_omitted={omitted}; sampled ancestry only; exited/inaccessible processes skipped").unwrap();
    }
    debug_assert!(out.len() < 4096);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    fn sample(at: u64, system: System, processes: Vec<Process>) -> Sample {
        Ok(Snapshot {
            at: Duration::from_secs(at),
            finished: Duration::from_secs(at),
            utc: at * 10_000_000,
            system: Some(system),
            memory: Some(Memory {
                load: 42,
                available: 123456789,
            }),
            processes,
            complete: true,
        })
    }
    fn process(pid: u32, parent: u32, created: u64, ticks: u64) -> Process {
        Process {
            pid,
            parent,
            image: format!("p{pid}.exe"),
            cpu: Some(Cpu {
                created,
                user: ticks,
                kernel: 0,
            }),
        }
    }
    #[test]
    fn snapshot_deltas_rank_eight_and_follow_descendants_without_pid_reuse() {
        let before = sample(
            1,
            System {
                idle: 100,
                kernel: 200,
                user: 100,
            },
            vec![process(1, 0, 1, 100), process(9, 0, 2, 900)],
        );
        let mut ps = vec![
            process(1, 0, 1, 200),
            process(2, 1, 2, 400),
            process(3, 2, 3, 300),
            process(9, 0, 3, 99999),
        ];
        ps.extend((10..30).map(|pid| process(pid, 0, 15_000_000, u64::from(pid) * 100)));
        let after = sample(
            2,
            System {
                idle: 150,
                kernel: 300,
                user: 200,
            },
            ps,
        );
        let report = describe(Some(&before), &after, 1);
        assert!(report.contains("system_busy=75.00%"), "{report}");
        assert!(
            report.contains("memory_READY=load:42% available:123456789B"),
            "{report}"
        );
        assert!(
            report.contains("memory_breach=load:42% available:123456789B"),
            "{report}"
        );
        assert_eq!(report.matches("top_cpu ").count(), 8, "{report}");
        assert!(report.find("p29.exe").unwrap() < report.find("p28.exe").unwrap());
        assert!(
            !report.contains("top_cpu pid:9 "),
            "reused PID must not inherit earlier CPU: {report}"
        );
        assert!(
            report.contains("descendant pid:2 ") && report.contains("descendant pid:3 "),
            "{report}"
        );
    }
    #[test]
    fn snapshot_report_bounds_names_and_descendants_and_handles_missing_samples() {
        let ps = (2..1000)
            .map(|pid| Process {
                image: "長\n".repeat(1000),
                ..process(pid, 1, 1, 10)
            })
            .collect();
        let after = sample(
            2,
            System {
                idle: 0,
                kernel: 0,
                user: 0,
            },
            ps,
        );
        let report = describe(None, &after, 1);
        assert!(report.len() < 4096, "{}", report.len());
        assert!(report.contains("descendants_omitted="), "{report}");
        assert!(report.contains("system_busy=unavailable"), "{report}");
        assert!(describe(
            Some(&Err("sample timed out")),
            &Err("access unavailable"),
            1
        )
        .contains("sample timed out"));
    }
    #[test]
    fn snapshot_wait_is_bounded_and_a_stuck_collector_cannot_multiply() {
        use std::sync::{
            atomic::{AtomicBool, Ordering},
            mpsc,
        };
        static BUSY: AtomicBool = AtomicBool::new(false);
        let (release, wait) = mpsc::channel();
        let start = Instant::now();
        let result = bounded(&BUSY, Duration::from_millis(20), move |_| {
            wait.recv_timeout(Duration::from_secs(3)).unwrap();
            Err("synthetic unavailable")
        });
        let elapsed = start.elapsed();
        let second = bounded(&BUSY, Duration::from_millis(20), |_| {
            panic!("must not start a second worker")
        });
        release.send(()).unwrap();
        let cleanup = Instant::now();
        while BUSY.load(Ordering::Acquire) {
            assert!(cleanup.elapsed() < Duration::from_secs(1));
            std::thread::yield_now();
        }
        assert!(result.unwrap_err().contains("timed out"));
        assert!(elapsed < Duration::from_secs(1));
        assert_eq!(
            second.unwrap_err(),
            "previous native snapshot still in flight"
        );
        assert_eq!(
            bounded(&BUSY, Duration::from_secs(1), |_| Err("next sample")).unwrap_err(),
            "next sample"
        );
    }
}
