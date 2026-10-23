//! Test-only owned processes; never use this runner for a product monitor.
use std::{
    cell::RefCell,
    io::Read,
    process::{Child, Command, Stdio},
    sync::{atomic::AtomicBool, atomic::Ordering, mpsc, Arc, Mutex, MutexGuard},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

const READY: &[u8] = b"BATC_POWERSHELL_FIXTURE_READY_V1\n";
const MAX_OUTPUT: u64 = 32_768;

#[cfg(windows)]
#[path = "powershell_cpu.rs"]
mod cpu;
#[path = "powershell_guard.rs"]
mod guard;
#[path = "powershell_reader.rs"]
mod reader;

#[derive(Clone, Copy, Debug, Default)]
struct Lifecycle {
    ready: Option<Duration>,
    stdout_eof: Option<Duration>,
    process_exit: Option<Duration>,
}

#[derive(Debug)]
struct Breach {
    lifecycle: Lifecycle,
    wall: Duration,
    child_cpu: String,
    exit_poll_error: Option<std::io::Error>,
}

#[cfg(windows)]
pub const READY_SCRIPT: &str =
    "[Console]::Out.Write(\"BATC_POWERSHELL_FIXTURE_READY_V1`n\");[Console]::Out.Flush();\n";

#[cfg(windows)]
pub fn command() -> Command {
    let system = std::env::var_os("SystemRoot").unwrap();
    let base = std::path::Path::new(&system).join("System32/WindowsPowerShell/v1.0");
    let executable = base.join("powershell.exe");
    assert!(executable.is_absolute());
    let mut command = Command::new(executable);
    command
        .args(["-NoProfile", "-NonInteractive"])
        .env("PSModulePath", base.join("Modules"));
    command
}

// Windows 5.1 cold CLR/assembly/module loading and first-use antivirus scanning
// compete across parallel tests even with -NoProfile. Like the desktop contract
// bootstrap, wait for THIS child's flushed marker (at most 60s), then start the
// original work budget. This is a separate startup phase, not a larger work
// deadline, a warm-up process or a retry. The process-wide lock covers each whole
// fixture; callers acquire it before creating any short-lived observed child.
pub struct Fixture {
    _guard: MutexGuard<'static, ()>,
    breach: RefCell<Option<Breach>>,
}
impl Fixture {
    pub fn new() -> Self {
        Self {
            _guard: guard::acquire(),
            breach: RefCell::new(None),
        }
    }

    pub fn run(&self, command: &mut Command, work: Duration) -> Vec<u8> {
        self.run_with_deadlines(command, Duration::from_secs(60), work)
            .unwrap_or_else(|error| panic!("{}", self.failure_message(error)))
    }

    fn failure_message(&self, reason: &str) -> String {
        match self.breach.borrow().as_ref() {
            None => reason.to_string(),
            Some(breach) => format!(
                "{reason}; before cleanup, relative to spawn call: READY={:?}; stdout_EOF={:?}; process_exit={:?}; wall={:?}; {}; exit_poll_error={:?}",
                breach.lifecycle.ready, breach.lifecycle.stdout_eof,
                breach.lifecycle.process_exit, breach.wall, breach.child_cpu,
                breach.exit_poll_error,
            ),
        }
    }

    fn record_breach(&self, owned: &mut Owned, began: Instant, lifecycle: &Mutex<Lifecycle>) {
        // Freeze the evidence before Owned kills/reaps the child or cancels its
        // pipe reader. These are observations, never the child's script phases.
        let exit_poll_error = owned.observe_exit(began, lifecycle).err();
        let lifecycle = *lifecycle.lock().unwrap();
        let wall = began.elapsed();
        #[cfg(windows)]
        let child_cpu = {
            use std::os::windows::io::AsHandle;
            cpu::describe(owned.child.as_handle(), wall)
        };
        #[cfg(not(windows))]
        let child_cpu = "child_cpu=unavailable (non-Windows)".to_string();
        self.breach.replace(Some(Breach {
            lifecycle,
            wall,
            child_cpu,
            exit_poll_error,
        }));
    }

    fn run_with_deadlines(
        &self,
        command: &mut Command,
        startup: Duration,
        work: Duration,
    ) -> Result<Vec<u8>, &'static str> {
        self.breach.replace(None);
        let began = Instant::now();
        let mut owned = Owned {
            child: command
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::null())
                .spawn()
                .map_err(|_| "PowerShell fixture spawn failed")?,
            reader: None,
        };
        let lifecycle = Arc::new(Mutex::new(Lifecycle::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let mut stdout = reader::Stdout::new(
            owned.child.stdout.take().unwrap(),
            stop.clone(),
            lifecycle.clone(),
            began,
        );
        let (ready_tx, ready_rx) = mpsc::channel();
        let (output_tx, output_rx) = mpsc::channel();
        let reader_lifecycle = lifecycle.clone();
        let join = thread::spawn(move || {
            let mut marker = [0; READY.len()];
            let ready = stdout.read_exact(&mut marker).is_ok() && marker == READY;
            let ready_at = ready.then(Instant::now);
            reader_lifecycle.lock().unwrap().ready = ready_at.map(|at| at.duration_since(began));
            let _ = ready_tx.send(ready_at);
            if ready {
                let mut output = Vec::new();
                let result = stdout
                    .take(MAX_OUTPUT + 1)
                    .read_to_end(&mut output)
                    .map_err(|_| "PowerShell fixture output read failed")
                    .and({
                        if output.len() > MAX_OUTPUT as usize {
                            Err("PowerShell fixture output exceeded its bound")
                        } else {
                            Ok(output)
                        }
                    });
                let _ = output_tx.send(result);
            }
        });
        owned.reader = Some(Reader { stop, join });
        let started = loop {
            owned
                .observe_exit(began, &lifecycle)
                .map_err(|_| "PowerShell fixture wait failed")?;
            // Observe exit during startup too, including a child whose pipe is
            // kept open elsewhere. The original startup budget stays unchanged.
            let poll = Duration::from_millis(10).min(startup.saturating_sub(began.elapsed()));
            match ready_rx.recv_timeout(poll) {
                Ok(Some(ready)) if ready.duration_since(began) <= startup => break ready,
                Err(mpsc::RecvTimeoutError::Timeout) if began.elapsed() < startup => continue,
                Err(mpsc::RecvTimeoutError::Timeout) | Ok(Some(_)) => {
                    self.record_breach(&mut owned, began, &lifecycle);
                    return Err("PowerShell fixture did not start within 60 s");
                }
                _ => return Err("PowerShell fixture readiness marker missing or invalid"),
            }
        };
        let mut output = None;
        loop {
            // Neither a late exit nor buffered output can borrow startup time.
            if started.elapsed() >= work {
                self.record_breach(&mut owned, began, &lifecycle);
                return Err("PowerShell fixture exceeded its work deadline");
            }
            if output.is_none() {
                match output_rx.try_recv() {
                    Ok(result) => output = Some(result?),
                    Err(mpsc::TryRecvError::Disconnected) => {
                        return Err("PowerShell fixture output reader failed");
                    }
                    Err(mpsc::TryRecvError::Empty) => {}
                }
            }
            if let Some(status) = owned
                .observe_exit(began, &lifecycle)
                .map_err(|_| "PowerShell fixture wait failed")?
            {
                if !status.success() {
                    return Err("PowerShell fixture failed");
                }
                if let Some(output) = output {
                    return Ok(output);
                }
            }
            thread::sleep(Duration::from_millis(10).min(work.saturating_sub(started.elapsed())));
        }
    }
}

struct Owned {
    child: Child,
    reader: Option<Reader>,
}
struct Reader {
    stop: Arc<AtomicBool>,
    join: JoinHandle<()>,
}
impl Owned {
    fn observe_exit(
        &mut self,
        began: Instant,
        lifecycle: &Mutex<Lifecycle>,
    ) -> std::io::Result<Option<std::process::ExitStatus>> {
        let status = self.child.try_wait()?;
        if status.is_some() {
            lifecycle
                .lock()
                .unwrap()
                .process_exit
                .get_or_insert_with(|| began.elapsed());
        }
        Ok(status)
    }
}
impl Drop for Owned {
    fn drop(&mut self) {
        // A pipe writer may outlive this child. Cancel only our reader; never
        // wait for EOF or kill unrelated processes to make cleanup complete.
        if let Some(reader) = &self.reader {
            reader.stop.store(true, Ordering::Relaxed);
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(reader) = self.reader.take() {
            let _ = reader.join.join();
        }
    }
}

#[cfg(all(test, unix))]
#[path = "powershell_tests.rs"]
mod tests;
