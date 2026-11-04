//! Fixed native SSH bootstrap adapter. Requires caller-held shared launcher mutex.
//! No process PID lookup/kill, service/path IPC, deployment or credential fallback.
use crate::{
    bootstrap::{Exchange, Platform, Recipe, Request},
    configuration::Configuration,
    monitor_launch,
    ownership::ProbeGeneration,
    windows::current_login,
    windows_launcher::LauncherMutex,
    windows_tunnel::system_ssh,
    Result,
};
use std::{path::PathBuf, process::Stdio};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    time::{timeout_at, Instant},
};
use zeroize::Zeroizing;

pub struct WindowsBootstrap<'a, F> {
    configuration: &'a Configuration,
    launcher: &'a LauncherMutex,
    selected: F,
    executable: PathBuf,
}
impl<'a, F: Fn(&ProbeGeneration) -> Result<()>> WindowsBootstrap<'a, F> {
    /// `selected` must reread current selection/installation and require the
    /// Connector selected with the exact captured generation. Native-only hook.
    /// Retain the launcher on this same blocking thread through Store::advance.
    pub fn new(
        configuration: &'a Configuration,
        launcher: &'a LauncherMutex,
        selected: F,
    ) -> Result<Self> {
        let executable = crate::installation::canonical_local(&system_ssh()?)?;
        Ok(Self {
            configuration,
            launcher,
            selected,
            executable,
        })
    }
}
impl<F: Fn(&ProbeGeneration) -> Result<()>> Platform for WindowsBootstrap<'_, F> {
    fn verify(&self, recipe: &Recipe, generation: &ProbeGeneration) -> Result<()> {
        if &current_login()? != self.launcher.login() {
            return Err("OTHER_LOGIN_OWNER");
        }
        recipe.verify_current(self.configuration)?;
        (self.selected)(generation)?;
        if crate::installation::canonical_local(&system_ssh()?)? != self.executable {
            return Err("BOOTSTRAP_EXECUTABLE_CHANGED");
        }
        Ok(())
    }
    fn exchange<'a>(
        &'a mut self,
        recipe: &'a Recipe,
        request: &'a Request,
        deadline: Instant,
    ) -> Exchange<'a> {
        Box::pin(async move {
            transport(
                &self.executable,
                &recipe.arguments(),
                request.bytes()?,
                deadline,
            )
            .await
        })
    }
}

async fn transport(
    executable: &std::path::Path,
    arguments: &[String],
    body: Vec<u8>,
    deadline: Instant,
) -> Result<Vec<u8>> {
    if Instant::now() >= deadline {
        return Err("BOOTSTRAP_TIMEOUT");
    }
    // Store saves its ensure fence before this method. Even positive
    // local spawn failure remains fenced; it is never silently retried.
    let mut child = crate::hidden_process::hidden_tokio_command(executable)
        .args(arguments)
        .env_clear()
        .envs(monitor_launch::environment(std::env::vars_os()))
        .creation_flags(crate::hidden_process::hidden_flags(0x0800_0000))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|_| "BOOTSTRAP_TRANSPORT_UNAVAILABLE")?;
    let mut input = child
        .stdin
        .take()
        .ok_or("BOOTSTRAP_TRANSPORT_UNAVAILABLE")?;
    let output = child
        .stdout
        .take()
        .ok_or("BOOTSTRAP_TRANSPORT_UNAVAILABLE")?;
    let body = Zeroizing::new(body);
    let attempt = async {
        input
            .write_all(&body)
            .await
            .map_err(|_| "BOOTSTRAP_TRANSPORT_UNKNOWN")?;
        input
            .shutdown()
            .await
            .map_err(|_| "BOOTSTRAP_TRANSPORT_UNKNOWN")?;
        drop(input);
        let mut bytes = Zeroizing::new(Vec::new());
        output
            .take(16385)
            .read_to_end(&mut bytes)
            .await
            .map_err(|_| "BOOTSTRAP_TRANSPORT_UNKNOWN")?;
        if bytes.len() > 16384 {
            return Err("BOOTSTRAP_RESPONSE_UNPROVEN");
        }
        if !child
            .wait()
            .await
            .map_err(|_| "BOOTSTRAP_TRANSPORT_UNKNOWN")?
            .success()
        {
            return Err("BOOTSTRAP_REFUSED");
        }
        Ok(bytes.to_vec())
    };
    // Cancellation only closes this retained local SSH child; it cannot
    // prove the remote effect was not received. The local fence remains.
    timeout_at(deadline, attempt)
        .await
        .map_err(|_| "BOOTSTRAP_TIMEOUT")?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{Read, Write},
        time::Duration,
    };
    #[test]
    #[ignore = "owned pipe double; only invoked by the bounded transport test"]
    fn pipe_peer() {
        let mut request = Vec::new();
        std::io::stdin().read_to_end(&mut request).unwrap();
        match request.as_slice() {
            b"large" => {
                std::io::stdout().write_all(&vec![b'x'; 20000]).unwrap();
            }
            b"slow" => std::thread::sleep(Duration::from_secs(30)),
            b"echo" => println!("synthetic bootstrap response"),
            _ => panic!("unknown owned fixture input"),
        }
    }
    #[tokio::test]
    async fn fixed_owned_pipe_double_exercises_eof_output_limit_and_timeout() {
        let executable = std::env::current_exe().unwrap();
        let arguments = [
            "--ignored",
            "--exact",
            "windows_bootstrap::tests::pipe_peer",
            "--nocapture",
        ]
        .map(str::to_owned);
        let output = transport(
            &executable,
            &arguments,
            b"echo".to_vec(),
            Instant::now() + Duration::from_secs(5),
        )
        .await
        .unwrap();
        assert!(String::from_utf8(output)
            .unwrap()
            .contains("synthetic bootstrap response"));
        assert_eq!(
            transport(
                &executable,
                &arguments,
                b"large".to_vec(),
                Instant::now() + Duration::from_secs(5)
            )
            .await,
            Err("BOOTSTRAP_RESPONSE_UNPROVEN")
        );
        assert_eq!(
            transport(
                &executable,
                &arguments,
                b"slow".to_vec(),
                Instant::now() + Duration::from_millis(200)
            )
            .await,
            Err("BOOTSTRAP_TIMEOUT")
        );
    }
}
