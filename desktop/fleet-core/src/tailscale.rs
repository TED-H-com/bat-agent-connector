//! Fixed local Tailscale diagnostics and durable explicit GUI launch. No network configuration.
use crate::{migration_io as files, strict_json, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

pub const STATUS_LIMIT: usize = 1_048_576;
const JOURNAL_LIMIT: usize = 524288;
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LoginState {
    Unknown,
    NeedsLogin,
    NeedsApproval,
    Stopped,
    Starting,
    Running,
}
pub fn login_state(bytes: &[u8], success: bool) -> LoginState {
    if !success {
        return LoginState::Unknown;
    }
    let Ok(value) = strict_json::parse(bytes, STATUS_LIMIT) else {
        return LoginState::Unknown;
    };
    match value
        .get("BackendState")
        .and_then(serde_json::Value::as_str)
    {
        Some("NeedsLogin") => LoginState::NeedsLogin,
        Some("NeedsMachineAuth") => LoginState::NeedsApproval,
        Some("Stopped") => LoginState::Stopped,
        Some("Starting") => LoginState::Starting,
        Some("Running") => LoginState::Running,
        _ => LoginState::Unknown,
    }
}
/// Internal command runner. Production constructs only the fixed installed CLI/argv.
#[cfg(any(windows, test))]
pub(crate) async fn query_status(mut command: tokio::process::Command) -> LoginState {
    use std::process::Stdio;
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let Ok(mut child) = command.spawn() else {
        return LoginState::Unknown;
    };
    query_child_status(&mut child).await
}

#[cfg(any(windows, test))]
async fn query_child_status(child: &mut tokio::process::Child) -> LoginState {
    use std::time::Duration;
    use tokio::{io::AsyncReadExt, time::timeout};
    let result = if let Some(stdout) = child.stdout.take() {
        timeout(Duration::from_secs(3), async {
            let mut bytes = zeroize::Zeroizing::new(Vec::new());
            stdout
                .take(STATUS_LIMIT as u64 + 1)
                .read_to_end(&mut bytes)
                .await
                .ok()?;
            if bytes.len() > STATUS_LIMIT {
                return None;
            }
            let status = child.wait().await.ok()?;
            Some(login_state(&bytes, status.success()))
        })
        .await
        .ok()
        .flatten()
    } else {
        None
    };
    if let Some(state) = result {
        return state;
    }
    // Dropping a Child only requests termination; Windows can still hold the
    // executable open. Reap this exact child after incomplete reads/timeouts.
    // Cleanup has its own bounded grace and cannot extend status collection.
    let _ = child.start_kill();
    let _ = timeout(Duration::from_secs(1), child.wait()).await;
    LoginState::Unknown
}

pub fn id_valid(id: &str) -> bool {
    id.len() == 32
        && id
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Uncertain,
    Started,
    NotStarted,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub request_id: String,
    pub phase: Phase,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    version: u8,
    scope: String,
    latest: String,
    receipts: BTreeMap<String, Receipt>,
}
pub struct Store {
    path: PathBuf,
    scope: String,
}
pub enum Spawn {
    Started,
    NotStarted,
    Uncertain,
}
impl Store {
    /// Native-derived current-account/login scope only. Caller holds Launcher during open.
    pub fn new(roaming: &Path, scope: &str) -> Result<Self> {
        if !roaming.is_absolute()
            || scope.len() != 64
            || !scope
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return Err("TAILSCALE_JOURNAL_INVALID");
        }
        files::directory(roaming)?;
        Ok(Self {
            path: roaming.join(format!("bat-tailscale-open-{scope}.json")),
            scope: scope.into(),
        })
    }
    fn read(&self) -> Result<(Option<Vec<u8>>, Option<Journal>)> {
        files::directory(self.path.parent().ok_or("TAILSCALE_JOURNAL_INVALID")?)?;
        let bytes = files::read(&self.path, JOURNAL_LIMIT)?;
        let value = if let Some(bytes) = &bytes {
            let doc: Journal = serde_json::from_value(strict_json::parse(bytes, JOURNAL_LIMIT)?)
                .map_err(|_| "TAILSCALE_JOURNAL_INVALID")?;
            if doc.version != 1
                || doc.scope != self.scope
                || doc.receipts.len() > 4096
                || !doc.receipts.contains_key(&doc.latest)
                || doc
                    .receipts
                    .iter()
                    .any(|(k, r)| !id_valid(k) || r.request_id != *k)
            {
                return Err("TAILSCALE_JOURNAL_INVALID");
            }
            Some(doc)
        } else {
            None
        };
        Ok((bytes, value))
    }
    pub fn latest(&self) -> Result<Option<Receipt>> {
        Ok(self
            .read()?
            .1
            .and_then(|d| d.receipts.get(&d.latest).cloned()))
    }
    pub fn receipt(&self, id: &str) -> Result<Option<Receipt>> {
        if !id_valid(id) {
            return Err("TAILSCALE_REQUEST_INVALID");
        }
        Ok(self.read()?.1.and_then(|d| d.receipts.get(id).cloned()))
    }
    pub fn open(
        &self,
        id: &str,
        verify: impl Fn() -> Result<()>,
        spawn: impl FnOnce() -> Spawn,
    ) -> Result<Receipt> {
        if !id_valid(id) {
            return Err("TAILSCALE_REQUEST_INVALID");
        }
        let (bytes, old) = self.read()?;
        if let Some(receipt) = old.as_ref().and_then(|d| d.receipts.get(id)) {
            return Ok(receipt.clone());
        }
        if old
            .as_ref()
            .is_some_and(|d| d.receipts[&d.latest].phase == Phase::Uncertain)
        {
            return Err("TAILSCALE_OPEN_UNCERTAIN");
        }
        let mut doc = old.unwrap_or_else(|| Journal {
            version: 1,
            scope: self.scope.clone(),
            latest: id.into(),
            receipts: BTreeMap::new(),
        });
        if doc.receipts.len() >= 4096 {
            return Err("TAILSCALE_HISTORY_FULL");
        }
        verify()?;
        let mut receipt = Receipt {
            request_id: id.into(),
            phase: Phase::Uncertain,
        };
        doc.latest = id.into();
        doc.receipts.insert(id.into(), receipt.clone());
        let intent = serde_json::to_vec(&doc).map_err(|_| "TAILSCALE_JOURNAL_INVALID")?;
        files::replace(&self.path, bytes.as_deref(), Some(&intent), JOURNAL_LIMIT)?;
        // A crash after intent publication must never authorize another spawn.
        receipt.phase = if verify().is_err() {
            Phase::NotStarted
        } else {
            match spawn() {
                Spawn::Started => Phase::Started,
                Spawn::NotStarted => Phase::NotStarted,
                Spawn::Uncertain => Phase::Uncertain,
            }
        };
        doc.receipts.insert(id.into(), receipt.clone());
        let completed = serde_json::to_vec(&doc).map_err(|_| "TAILSCALE_JOURNAL_INVALID")?;
        files::replace(&self.path, Some(&intent), Some(&completed), JOURNAL_LIMIT)?;
        Ok(receipt)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn bounded_synthetic_process_output_exit_timeout_and_no_secret_projection() {
        let root = std::env::temp_dir().join(format!(
            "bac-tailscale-child-{:032x}",
            rand::random::<u128>()
        ));
        std::fs::create_dir(&root).unwrap();
        let source = root.join("fixture.rs");
        let exe = root.join(if cfg!(windows) {
            "fixture.exe"
        } else {
            "fixture"
        });
        std::fs::write(
            &source,
            r##"fn main(){
          use std::io::Write;
          match std::env::args().nth(1).as_deref(){
            Some("valid")=>print!("{{\"BackendState\":\"NeedsLogin\",\"AuthURL\":\"secret\"}}"),
            Some("invalid")=>print!("not JSON"),
            Some("failure")=>{print!("{{\"BackendState\":\"Running\"}}");std::process::exit(1);},
            Some("large")=>{std::io::stdout().write_all(&vec![b'x';2*1024*1024]).ok();std::thread::sleep(std::time::Duration::from_secs(60));},
            _=>std::thread::sleep(std::time::Duration::from_secs(60)),
          }
        }"##,
        )
        .unwrap();
        assert!(std::process::Command::new("rustc")
            .arg(&source)
            .arg("-o")
            .arg(&exe)
            .status()
            .unwrap()
            .success());
        for (mode, expected) in [
            ("valid", LoginState::NeedsLogin),
            ("invalid", LoginState::Unknown),
            ("failure", LoginState::Unknown),
            ("large", LoginState::Unknown),
            ("timeout", LoginState::Unknown),
        ] {
            let mut command = tokio::process::Command::new(&exe);
            command
                .arg(mode)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::null())
                .kill_on_drop(true);
            let mut child = command.spawn().unwrap();
            let started = std::time::Instant::now();
            assert_eq!(query_child_status(&mut child).await, expected);
            // Observe the original process handle, not a sleep or inferred PID.
            assert!(
                child.try_wait().unwrap().is_some(),
                "{mode} child not reaped"
            );
            assert!(started.elapsed() < std::time::Duration::from_secs(5));
        }
        let mut command = tokio::process::Command::new(&exe);
        command.arg("valid");
        assert_eq!(query_status(command).await, LoginState::NeedsLogin);
        assert_eq!(
            query_status(tokio::process::Command::new(root.join("missing"))).await,
            LoginState::Unknown
        );
        std::fs::remove_dir_all(root).unwrap();
    }
}
