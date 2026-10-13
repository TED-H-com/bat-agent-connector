//! Test-only owned processes; never use this runner for a product monitor.
use std::{
    io::Read,
    process::{Child, Command, Stdio},
    sync::{mpsc, Mutex, MutexGuard},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

const READY: &[u8] = b"BATC_POWERSHELL_FIXTURE_READY_V1\n";
const MAX_OUTPUT: u64 = 32_768;
static FIXTURES: Mutex<()> = Mutex::new(());

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
pub struct Fixture(MutexGuard<'static, ()>);
impl Fixture {
    pub fn new() -> Self {
        Self(FIXTURES.lock().unwrap_or_else(|poison| poison.into_inner()))
    }

    #[cfg(windows)]
    pub fn run(&self, command: &mut Command, work: Duration) -> Vec<u8> {
        self.run_with_deadlines(command, Duration::from_secs(60), work)
            .unwrap_or_else(|error| panic!("{error}"))
    }

    fn run_with_deadlines(
        &self,
        command: &mut Command,
        startup: Duration,
        work: Duration,
    ) -> Result<Vec<u8>, &'static str> {
        let _lock = &self.0;
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
        let mut stdout = owned.child.stdout.take().unwrap();
        let (ready_tx, ready_rx) = mpsc::channel();
        let (output_tx, output_rx) = mpsc::channel();
        owned.reader = Some(thread::spawn(move || {
            let mut marker = [0; READY.len()];
            let ready = stdout.read_exact(&mut marker).is_ok() && marker == READY;
            let _ = ready_tx.send(ready.then(Instant::now));
            if ready {
                let mut output = Vec::new();
                let result = stdout
                    .take(MAX_OUTPUT + 1)
                    .read_to_end(&mut output)
                    .map_err(|_| "PowerShell fixture output read failed")
                    .and_then(|_| {
                        if output.len() > MAX_OUTPUT as usize {
                            Err("PowerShell fixture output exceeded its bound")
                        } else {
                            Ok(output)
                        }
                    });
                let _ = output_tx.send(result);
            }
        }));
        let started = match ready_rx.recv_timeout(startup.saturating_sub(began.elapsed())) {
            Ok(Some(ready)) if ready.duration_since(began) <= startup => ready,
            Err(mpsc::RecvTimeoutError::Timeout) | Ok(Some(_)) => {
                return Err("PowerShell fixture did not start within 60 s");
            }
            _ => return Err("PowerShell fixture readiness marker missing or invalid"),
        };
        let mut output = None;
        loop {
            // Neither a late exit nor buffered output can borrow startup time.
            if started.elapsed() >= work {
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
                .child
                .try_wait()
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
    reader: Option<JoinHandle<()>>,
}
impl Drop for Owned {
    fn drop(&mut self) {
        // Every result and panic cleans up the same child before joining its pipe
        // reader. Fixture scripts must not pass stdout to long-lived descendants.
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}

#[cfg(all(test, unix))]
#[path = "powershell_tests.rs"]
mod tests;
