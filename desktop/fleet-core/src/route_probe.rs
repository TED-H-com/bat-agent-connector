//! Native route probe adapter. Fixed trusted executable; never PATH/IPC commands.
use crate::{
    inventory::Endpoint,
    route::{tailscale_peer, PeerFuture, ProbeFuture, RouteProbe, TailscalePeer},
    Result,
};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
};
use tokio::{
    io::AsyncReadExt,
    net::TcpStream,
    process::Command,
    time::{timeout_at, Instant},
};
use zeroize::Zeroizing;

pub struct NativeRouteProbe {
    tailscale: Option<PathBuf>,
}
impl NativeRouteProbe {
    /// Path comes from trusted native installation discovery/configuration.
    /// None means not installed; direct is ineligible, with no command lookup.
    pub fn new(tailscale: Option<&Path>) -> Result<Self> {
        let tailscale = tailscale
            .map(|path| {
                if !path.is_absolute() {
                    return Err("ROUTE_EXECUTABLE_INVALID");
                }
                let canonical = path
                    .canonicalize()
                    .map_err(|_| "ROUTE_EXECUTABLE_INVALID")?;
                let expected = if cfg!(windows) {
                    "tailscale.exe"
                } else {
                    "tailscale"
                };
                if !canonical.is_file()
                    || !canonical
                        .file_name()
                        .and_then(|s| s.to_str())
                        .is_some_and(|s| s.eq_ignore_ascii_case(expected))
                {
                    return Err("ROUTE_EXECUTABLE_INVALID");
                }
                #[cfg(windows)]
                {
                    use std::path::{Component, Prefix};
                    // Native installation discovery also excludes mapped drives.
                    if !matches!(canonical.components().next(), Some(Component::Prefix(p))
                        if matches!(p.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)))
                    {
                        return Err("ROUTE_EXECUTABLE_INVALID");
                    }
                }
                Ok(canonical)
            })
            .transpose()?;
        Ok(Self { tailscale })
    }
}
impl NativeRouteProbe {
    /// All commands share executable trust, process isolation and cancellation.
    fn command(&self) -> Option<Command> {
        let path = self.tailscale.as_ref().filter(|path| path.is_file())?;
        let mut command = Command::new(path);
        command
            .env_clear()
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        #[cfg(windows)]
        {
            command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
            for key in ["SystemRoot", "WINDIR"] {
                if let Some(value) = std::env::var_os(key) {
                    command.env(key, value);
                }
            }
        }
        Some(command)
    }
}
impl RouteProbe for NativeRouteProbe {
    fn tailscale_direct<'a>(&'a self, ip: &'a str, deadline: Instant) -> ProbeFuture<'a> {
        Box::pin(async move { self.tailscale_peer(ip, deadline).await == TailscalePeer::Direct })
    }
    fn tailscale_peer<'a>(&'a self, ip: &'a str, deadline: Instant) -> PeerFuture<'a> {
        Box::pin(async move {
            if Instant::now() >= deadline {
                return TailscalePeer::Unknown;
            }
            let Some(mut command) = self.command() else {
                return TailscalePeer::Unknown;
            };
            command.args(["status", "--json"]).stdout(Stdio::piped());
            let Ok(mut child) = command.spawn() else {
                return TailscalePeer::Unknown;
            };
            let Some(stdout) = child.stdout.take() else {
                return TailscalePeer::Unknown;
            };
            let attempt = async {
                let mut bytes = Zeroizing::new(Vec::new());
                if stdout
                    .take(1024 * 1024 + 1)
                    .read_to_end(&mut bytes)
                    .await
                    .is_err()
                    || bytes.len() > 1024 * 1024
                {
                    return TailscalePeer::Unknown;
                }
                match child.wait().await {
                    Ok(status) if status.success() => tailscale_peer(&bytes, ip),
                    _ => TailscalePeer::Unknown,
                }
            };
            // Dropping this future terminates only its retained child process.
            let peer = timeout_at(deadline, attempt)
                .await
                .unwrap_or(TailscalePeer::Unknown);
            if Instant::now() < deadline {
                peer
            } else {
                TailscalePeer::Unknown
            }
        })
    }
    fn tailscale_wake<'a>(&'a self, ip: &'a str, deadline: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            // Defense in depth: the selector additionally requires unique status evidence.
            if ip.parse::<std::net::IpAddr>().is_err() || Instant::now() >= deadline {
                return false;
            }
            let Some(mut command) = self.command() else {
                return false;
            };
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return false;
            }
            command
                .args(["ping", "--c", "3", "--timeout", "2s", ip])
                .stdout(Stdio::null());
            let Ok(mut child) = command.spawn() else {
                return false;
            };
            matches!(timeout_at(deadline, child.wait()).await, Ok(Ok(status)) if status.success())
                && Instant::now() < deadline
        })
    }
    fn tcp<'a>(&'a self, endpoint: &'a Endpoint, deadline: Instant) -> ProbeFuture<'a> {
        Box::pin(async move {
            if Instant::now() >= deadline {
                return false;
            }
            matches!(
                timeout_at(
                    deadline,
                    TcpStream::connect((endpoint.address.as_str(), endpoint.port))
                )
                .await,
                Ok(Ok(_))
            ) && Instant::now() < deadline
        })
    }
}
