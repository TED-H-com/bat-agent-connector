//! Concrete Windows SSH effect boundary. The supervisor owns the mutex beyond this adapter.
use crate::{
    discovery::{discover, Backend, DiscoveryConfig, Ownership},
    process_adapter::{HeldProcess, ProcessSnapshot},
    tunnel::{Plan, Platform, SpawnFailure},
    windows::{MonitorMutex, WindowsMonitorObservation, WindowsProcess},
    Result,
};
use std::{
    io::ErrorKind,
    net::{SocketAddr, SocketAddrV4, TcpListener, TcpStream},
    os::windows::process::CommandExt,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};
use windows_sys::Win32::System::{
    SystemInformation::GetSystemDirectoryW, Threading::CREATE_NO_WINDOW,
};

/// Resolve through the OS, never PATH, a WebView field or an inherited SystemRoot variable.
pub fn system_ssh() -> Result<PathBuf> {
    let mut buffer = vec![0u16; 32768];
    let count = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
    if count == 0 || count >= buffer.len() {
        return Err("SSH_UNAVAILABLE");
    }
    let path = PathBuf::from(String::from_utf16(&buffer[..count]).map_err(|_| "SSH_UNAVAILABLE")?)
        .join("OpenSSH")
        .join("ssh.exe");
    if !path.is_absolute() {
        return Err("SSH_UNAVAILABLE");
    }
    Ok(path)
}
pub struct WindowsPlatform<'a> {
    mutex: &'a MonitorMutex,
    discovery: &'a DiscoveryConfig,
    executable: PathBuf,
}
impl<'a> WindowsPlatform<'a> {
    pub fn new(mutex: &'a MonitorMutex, discovery: &'a DiscoveryConfig) -> Result<Self> {
        Ok(Self {
            mutex,
            discovery,
            executable: system_ssh()?,
        })
    }
}
pub struct LaunchedProcess {
    child: Child,
    held: WindowsProcess,
}
impl HeldProcess for LaunchedProcess {
    fn snapshot(&self) -> Result<ProcessSnapshot> {
        self.held.snapshot()
    }
    fn terminate_and_wait(&mut self) -> Result<()> {
        self.held.terminate_and_wait()?;
        match self.child.try_wait() {
            Ok(Some(_)) => Ok(()),
            _ => Err("STOP_UNCONFIRMED"),
        }
    }
}
fn spawn_owned(command: &mut Command) -> std::result::Result<LaunchedProcess, SpawnFailure> {
    let mut child = command.spawn().map_err(|_| SpawnFailure::NotStarted)?;
    match WindowsProcess::from_child(&child) {
        Ok(held) => Ok(LaunchedProcess { child, held }),
        Err(_) => {
            // Child::kill uses the retained Windows launch handle. Never reopen its PID.
            let _ = child.kill();
            let deadline = Instant::now() + Duration::from_secs(2);
            loop {
                match child.try_wait() {
                    Ok(Some(_)) => return Err(SpawnFailure::RolledBack),
                    Ok(None) if Instant::now() < deadline => {
                        std::thread::sleep(Duration::from_millis(20))
                    }
                    _ => return Err(SpawnFailure::Unconfirmed),
                }
            }
        }
    }
}
fn endpoint_free(local: SocketAddrV4) -> Result<()> {
    if !local.ip().is_loopback() || local.port() == 0 {
        return Err("INVENTORY_INVALID");
    }
    endpoint_proof(
        |timeout| TcpStream::connect_timeout(&SocketAddr::V4(local), timeout).map(drop),
        || TcpListener::bind(local).map(drop),
    )
}
fn endpoint_proof(
    connect: impl FnOnce(Duration) -> std::io::Result<()>,
    bind: impl FnOnce() -> std::io::Result<()>,
) -> Result<()> {
    // Windows can take about two seconds to return WSAECONNREFUSED even on a
    // closed loopback endpoint. A 100 ms deadline rejected every free endpoint
    // in the actual Windows oracle. Timeout is still unknown, never absence.
    match connect(Duration::from_secs(3)) {
        Err(error) if error.kind() == ErrorKind::ConnectionRefused => (),
        _ => return Err("ENDPOINT_IN_USE"),
    }
    // A connect refusal is not enough: independently prove a bind can be obtained.
    // SSH's ExitOnForwardFailure still handles an external listener winning after this check.
    bind().map_err(|_| "ENDPOINT_IN_USE")?;
    Ok(())
}
impl Platform for WindowsPlatform<'_> {
    type Child = LaunchedProcess;
    fn owner(&mut self, epoch: &str) -> Result<ProcessSnapshot> {
        let owner =
            discover(self.discovery, &WindowsMonitorObservation)?.ok_or("OWNER_UNPROVEN")?;
        if owner.backend != Backend::Rust
            || owner.legacy
            || owner.ownership != Ownership::CurrentLogin
            || owner.instance.as_deref() != Some(epoch)
            || owner.process.pid != std::process::id()
            || &owner.process.login != self.mutex.login()
        {
            return Err("OWNER_UNPROVEN");
        }
        Ok(owner.process)
    }
    fn endpoint_free(&mut self, local: SocketAddrV4) -> Result<()> {
        endpoint_free(local)
    }
    fn executable(&self) -> &Path {
        &self.executable
    }
    fn spawn(&mut self, plan: &Plan) -> std::result::Result<LaunchedProcess, SpawnFailure> {
        self.owner(plan.epoch())
            .map_err(|_| SpawnFailure::NotStarted)?;
        if !self.executable.is_file() {
            return Err(SpawnFailure::NotStarted);
        }
        spawn_owned(
            Command::new(&self.executable)
                .args(plan.arguments())
                .creation_flags(CREATE_NO_WINDOW)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null()),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn endpoint_proof_requires_refusal_and_bind_with_a_bounded_windows_budget() {
        let refused = |timeout| {
            assert_eq!(timeout, Duration::from_secs(3));
            Err(std::io::Error::from(ErrorKind::ConnectionRefused))
        };
        assert_eq!(endpoint_proof(refused, || Ok(())), Ok(()));
        assert_eq!(
            endpoint_proof(refused, || Err(std::io::Error::from(ErrorKind::AddrInUse))),
            Err("ENDPOINT_IN_USE")
        );
        assert_eq!(
            endpoint_proof(|_| Ok(()), || panic!("connected endpoint cannot be absent")),
            Err("ENDPOINT_IN_USE")
        );
        for kind in [
            ErrorKind::TimedOut,
            ErrorKind::WouldBlock,
            ErrorKind::PermissionDenied,
            ErrorKind::AddrNotAvailable,
            ErrorKind::ConnectionReset,
        ] {
            assert_eq!(
                endpoint_proof(
                    |_| Err(std::io::Error::from(kind)),
                    || panic!("unknown connection outcome cannot become bind-only proof"),
                ),
                Err("ENDPOINT_IN_USE")
            );
        }
    }
    fn endpoint_diagnostics(label: &str, local: SocketAddrV4) {
        // Only addresses allocated by this fixture. Report no private endpoint data.
        // Keep the positive production oracle below: a timeout must not become absence.
        for timeout in [Duration::from_millis(100), Duration::from_secs(3)] {
            let started = Instant::now();
            let outcome = TcpStream::connect_timeout(&SocketAddr::V4(local), timeout)
                .map(|_| ())
                .map_err(|error| error.kind());
            let elapsed = started.elapsed();
            let bind = TcpListener::bind(local)
                .map(drop)
                .map_err(|error| error.kind());
            eprintln!(
                "endpoint fixture={label} budget_ms={} elapsed_ms={} connect={outcome:?} bind={bind:?}",
                timeout.as_millis(),
                elapsed.as_millis(),
            );
        }
    }
    #[test]
    fn system_path_and_occupied_listener_are_fixed_and_read_only() {
        let path = system_ssh().unwrap();
        assert!(path.is_absolute());
        let rendered = path.to_str().unwrap();
        assert!(!rendered.contains('/'));
        assert!(rendered.ends_with(r"\OpenSSH\ssh.exe"));
        let unused_listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let SocketAddr::V4(unused) = unused_listener.local_addr().unwrap() else {
            panic!()
        };
        drop(unused_listener);
        endpoint_diagnostics("never_connected", unused);
        let never_connected = endpoint_free(unused);

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let SocketAddr::V4(local) = listener.local_addr().unwrap() else {
            panic!()
        };
        assert_eq!(endpoint_free(local), Err("ENDPOINT_IN_USE"));
        drop(listener);
        endpoint_diagnostics("occupied_then_closed", local);
        let occupied_then_closed = endpoint_free(local);
        assert_eq!(
            (never_connected, occupied_then_closed),
            (Ok(()), Ok(())),
            "both temporary endpoints must have positive refusal and bind evidence"
        );
    }
    #[test]
    #[ignore = "bounded child of retained launch adapter fixture"]
    fn owned_fixture() {
        if std::env::var("BAT_FLEET_TUNNEL_FIXTURE").as_deref() == Ok("1") {
            std::thread::sleep(Duration::from_secs(20));
        }
    }
    struct Cleanup(LaunchedProcess);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            let _ = self.0.terminate_and_wait();
        }
    }
    #[test]
    fn spawn_boundary_retains_handle_and_exact_arguments_for_rollback() {
        let args = [
            "--ignored",
            "--exact",
            "windows_tunnel::tests::owned_fixture",
            "--nocapture",
        ];
        let launch = spawn_owned(
            Command::new(std::env::current_exe().unwrap())
                .args(args)
                .env("BAT_FLEET_TUNNEL_FIXTURE", "1")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null()),
        );
        let mut launch = Cleanup(launch.unwrap_or_else(|_| panic!("owned fixture did not launch")));
        let evidence = launch.0.snapshot().unwrap();
        assert_eq!(evidence.arguments, args);
        assert_eq!(evidence.pid, launch.0.child.id());
        launch.0.terminate_and_wait().unwrap();
        assert!(launch.0.child.try_wait().unwrap().is_some());
    }
}
