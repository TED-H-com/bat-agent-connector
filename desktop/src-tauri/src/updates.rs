//! Native-only, explicit signed updates. Nothing accepts a caller URL, key or installer path.
use base64::Engine;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::OpenOptions,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri_plugin_updater::{Update, UpdaterExt};

// Consume the real release verifier's output, not a second hand-written feed shape.
#[cfg(test)]
// The test-only real producer loads the same validator source a second time.
#[allow(clippy::duplicate_mod)]
#[path = "../examples/update_manifest.rs"]
mod feed_producer;

const FEED: &str = "https://github.com/TED-H-com/bat-agent-connector/releases/latest/download/dashboard-update.json";
const TARGET: &str = "windows-x86_64-nsis";
const MAX_DOWNLOAD: usize = 128 * 1024 * 1024;
const CONTRACT: &str = "2026-10-08";

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Status {},
    Check {},
    Download { candidate_id: String },
    Install { candidate_id: String },
}

#[derive(Clone, Serialize)]
pub struct Candidate {
    pub candidate_id: String,
    pub version: String,
    pub source_sha: String,
    pub workflow_version: String,
    pub checked_at: f64,
}

#[derive(Clone, Serialize)]
pub struct Status {
    pub current_version: String,
    pub available: bool,
    pub phase: &'static str,
    pub code: Option<String>,
    pub candidate: Option<Candidate>,
    pub installation: Option<Intent>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Intent {
    schema: u32,
    from_version: String,
    to_version: String,
    candidate_id: String,
    source_sha: String,
    artifact_sha256: String,
}

struct Pending {
    view: Candidate,
    update: Update,
    bytes: Option<Vec<u8>>,
}

pub struct Updates {
    version: String,
    pubkey: Result<String, String>,
    supported: bool,
    directory: PathBuf,
    pub serial: tokio::sync::Mutex<()>,
    status: Mutex<Status>,
    pending: Mutex<Option<Pending>>,
}

fn key(config: &serde_json::Value) -> Result<String, String> {
    let config: tauri_plugin_updater::Config =
        serde_json::from_value(config.clone()).map_err(|_| "UPDATE_CONFIGURATION_INVALID")?;
    if !config.require_signed_version
        || config.dangerous_insecure_transport_protocol
        || config.dangerous_accept_invalid_certs
        || config.dangerous_accept_invalid_hostnames
        || config.allow_downgrades
    {
        return Err("UPDATE_CONFIGURATION_INVALID".into());
    }
    let value = &config.pubkey;
    if value.is_empty() {
        return Err("UPDATE_SIGNING_NOT_CONFIGURED".into());
    }
    if value.len() > 4096 {
        return Err("UPDATE_CONFIGURATION_INVALID".into());
    }
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(value)
        .map_err(|_| "UPDATE_CONFIGURATION_INVALID")?;
    let decoded = std::str::from_utf8(&decoded).map_err(|_| "UPDATE_CONFIGURATION_INVALID")?;
    minisign_verify::PublicKey::decode(decoded).map_err(|_| "UPDATE_CONFIGURATION_INVALID")?;
    Ok(value.to_owned())
}

fn safe_url(url: &url::Url) -> bool {
    url.scheme() == "https"
        && url.username().is_empty()
        && url.password().is_none()
        && url.port().is_none_or(|p| p == 443)
        && matches!(
            url.host_str(),
            Some(
                "github.com"
                    | "release-assets.githubusercontent.com"
                    | "objects.githubusercontent.com"
            )
        )
}

fn metadata(update: &Update) -> Result<Candidate, String> {
    let meta = update
        .raw_json
        .get("bat_dashboard")
        .ok_or("UPDATE_METADATA_INCOMPATIBLE")?;
    let source = meta
        .get("source_sha")
        .and_then(|v| v.as_str())
        .unwrap_or_default();
    let workflow = meta
        .get("workflow_version")
        .and_then(|v| v.as_str())
        .unwrap_or_default();
    if meta.get("api_version").and_then(|v| v.as_u64()) != Some(1)
        || meta.get("contract_version").and_then(|v| v.as_str()) != Some(CONTRACT)
        || source.len() != 40
        || !source.bytes().all(|b| b.is_ascii_hexdigit())
        || !crate::workflow_version::valid(workflow)
        || update.version.len() > 128
        || update.signature.len() > 8192
        || !safe_url(&update.download_url)
        || update.download_url.host_str() != Some("github.com")
        || !update
            .download_url
            .path()
            .starts_with("/TED-H-com/bat-agent-connector/releases/download/")
        || !update.download_url.path().ends_with(".exe")
        || update.download_url.query().is_some()
        || update.download_url.fragment().is_some()
    {
        return Err("UPDATE_METADATA_INCOMPATIBLE".into());
    }
    Ok(Candidate {
        candidate_id: uuid::Uuid::new_v4().to_string(),
        version: update.version.clone(),
        source_sha: source.into(),
        workflow_version: workflow.into(),
        checked_at: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "UPDATE_CLOCK_UNAVAILABLE")?
            .as_secs_f64(),
    })
}

fn marker_path(directory: &Path, version: &str) -> PathBuf {
    directory.join(format!("{:x}.json", Sha256::digest(version.as_bytes())))
}

fn read_intent(directory: &Path, version: &str) -> Result<Option<Intent>, String> {
    let path = marker_path(directory, version);
    let meta = match std::fs::symlink_metadata(&path) {
        Ok(meta) => meta,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err("UPDATE_RECEIPT_UNREADABLE".into()),
    };
    if !meta.is_file() || meta.len() > 8192 {
        return Err("UPDATE_RECEIPT_UNREADABLE".into());
    }
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .and_then(|f| f.take(8193).read_to_end(&mut bytes))
        .map_err(|_| "UPDATE_RECEIPT_UNREADABLE")?;
    let intent: Intent = serde_json::from_slice(&bytes).map_err(|_| "UPDATE_RECEIPT_UNREADABLE")?;
    if intent.schema != 1 || intent.from_version != version {
        return Err("UPDATE_RECEIPT_UNREADABLE".into());
    }
    Ok(Some(intent))
}

fn write_intent(directory: &Path, intent: &Intent) -> Result<(), String> {
    std::fs::create_dir_all(directory).map_err(|_| "UPDATE_RECEIPT_UNWRITABLE")?;
    let bytes = serde_json::to_vec(intent).map_err(|_| "UPDATE_RECEIPT_UNWRITABLE")?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(marker_path(directory, &intent.from_version))
        .map_err(|_| "UPDATE_INSTALLATION_UNSETTLED")?;
    // Even a partial intent blocks a repeat. Never erase a possibly sent installer invocation.
    file.write_all(&bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| "UPDATE_RECEIPT_UNWRITABLE")?;
    #[cfg(unix)]
    std::fs::File::open(directory)
        .and_then(|d| d.sync_all())
        .map_err(|_| "UPDATE_RECEIPT_UNWRITABLE")?;
    Ok(())
}

impl Updates {
    pub fn new(directory: PathBuf, version: String, config: serde_json::Value) -> Self {
        let pubkey = key(&config);
        let supported = cfg!(all(windows, target_arch = "x86_64"));
        let code = if !supported {
            Some("UPDATE_PLATFORM_UNSUPPORTED".into())
        } else {
            pubkey.as_ref().err().cloned()
        };
        Self {
            status: Mutex::new(Status {
                current_version: version.clone(),
                available: supported && pubkey.is_ok(),
                phase: "idle",
                code,
                candidate: None,
                installation: None,
            }),
            version,
            pubkey,
            supported,
            directory,
            serial: tokio::sync::Mutex::new(()),
            pending: Mutex::new(None),
        }
    }

    pub fn status(&self) -> Result<Status, String> {
        let mut view = self
            .status
            .lock()
            .map_err(|_| "UPDATE_STATE_UNAVAILABLE")?
            .clone();
        if let Some(intent) = read_intent(&self.directory, &self.version)? {
            view.phase = "installation_unknown";
            view.code = Some("UPDATE_INSTALLATION_UNSETTLED".into());
            view.installation = Some(intent);
        }
        Ok(view)
    }

    /// Durable installer uncertainty also fences launches after a refused Quit.
    pub fn keep_fleet_stopped(&self) -> bool {
        !matches!(read_intent(&self.directory, &self.version), Ok(None))
    }

    fn ready(&self) -> Result<(), String> {
        if !self.supported {
            return Err("UPDATE_PLATFORM_UNSUPPORTED".into());
        }
        self.pubkey.as_ref().map_err(Clone::clone)?;
        if read_intent(&self.directory, &self.version)?.is_some() {
            return Err("UPDATE_INSTALLATION_UNSETTLED".into());
        }
        Ok(())
    }

    fn phase(&self, phase: &'static str, code: Option<String>) -> Result<(), String> {
        let mut view = self.status.lock().map_err(|_| "UPDATE_STATE_UNAVAILABLE")?;
        view.phase = phase;
        view.code = code;
        Ok(())
    }

    pub async fn check(&self, app: &tauri::AppHandle) -> Result<Status, String> {
        self.ready()?;
        // Endpoint, proxy policy, keys, target and arguments are native/build-owned.
        let updater = app
            .updater_builder()
            .endpoints(vec![FEED
                .parse()
                .map_err(|_| "UPDATE_CONFIGURATION_INVALID")?])
            .map_err(|_| "UPDATE_CONFIGURATION_INVALID")?
            .pubkey(self.pubkey.as_ref().map_err(Clone::clone)?)
            .target(TARGET)
            .clear_headers()
            .clear_installer_args()
            .no_proxy()
            .version_comparator(|current, release| release.version > current)
            .timeout(Duration::from_secs(30))
            .configure_client(|client| {
                client
                    .https_only(true)
                    .redirect(updater_http::redirect::Policy::custom(|attempt| {
                        if attempt.previous().len() < 5 && safe_url(attempt.url()) {
                            attempt.follow()
                        } else {
                            attempt.stop()
                        }
                    }))
            })
            .build()
            .map_err(|_| "UPDATE_CONFIGURATION_INVALID")?;
        self.phase("checking", None)?;
        let result = tokio::time::timeout(Duration::from_secs(35), updater.check()).await;
        self.finish_check(match result {
            Ok(Ok(update)) => Ok(update),
            _ => Err(()),
        })
    }

    fn finish_check(&self, result: Result<Option<Update>, ()>) -> Result<Status, String> {
        let update = match result {
            Ok(update) => update,
            Err(()) => {
                self.phase("check_failed", Some("UPDATE_CHECK_FAILED".into()))?;
                return self.status();
            }
        };
        let pending = match update {
            Some(mut update) => {
                let view = match metadata(&update) {
                    Ok(view) => view,
                    Err(code) => {
                        self.phase("check_failed", Some(code))?;
                        return self.status();
                    }
                };
                update.timeout = Some(Duration::from_secs(180));
                Some(Pending {
                    view,
                    update,
                    bytes: None,
                })
            }
            None => None,
        };
        let view = pending.as_ref().map(|p| p.view.clone());
        *self
            .pending
            .lock()
            .map_err(|_| "UPDATE_STATE_UNAVAILABLE")? = pending;
        self.status
            .lock()
            .map_err(|_| "UPDATE_STATE_UNAVAILABLE")?
            .candidate = view;
        self.phase(
            if self
                .pending
                .lock()
                .map_err(|_| "UPDATE_STATE_UNAVAILABLE")?
                .is_some()
            {
                "available"
            } else {
                "up_to_date"
            },
            None,
        )?;
        self.status()
    }

    pub async fn download(&self, candidate_id: &str) -> Result<Status, String> {
        self.ready()?;
        let update = {
            let pending = self
                .pending
                .lock()
                .map_err(|_| "UPDATE_STATE_UNAVAILABLE")?;
            let pending = pending
                .as_ref()
                .filter(|p| p.view.candidate_id == candidate_id)
                .ok_or("UPDATE_CANDIDATE_CHANGED")?;
            if pending.bytes.is_some() {
                self.phase("verified", None)?;
                return self.status();
            }
            pending.update.clone()
        };
        self.phase("downloading", None)?;
        let exceeded = tokio::sync::Notify::new();
        let mut count = 0usize;
        let download = update.download(
            |chunk, total| {
                count = count.saturating_add(chunk);
                if count > MAX_DOWNLOAD || total.is_some_and(|n| n > MAX_DOWNLOAD as u64) {
                    exceeded.notify_one();
                }
            },
            || {},
        );
        let bytes = tokio::select! {
            biased;
            _ = exceeded.notified() => Err("UPDATE_DOWNLOAD_TOO_LARGE"),
            result = tokio::time::timeout(Duration::from_secs(185), download) => match result {
                Ok(Ok(bytes)) if bytes.len() <= MAX_DOWNLOAD => Ok(bytes),
                Ok(Ok(_)) => Err("UPDATE_DOWNLOAD_TOO_LARGE"),
                _ => Err("UPDATE_SIGNATURE_OR_DOWNLOAD_FAILED"),
            }
        };
        match bytes {
            Ok(bytes) => {
                let mut pending = self
                    .pending
                    .lock()
                    .map_err(|_| "UPDATE_STATE_UNAVAILABLE")?;
                pending
                    .as_mut()
                    .filter(|p| p.view.candidate_id == candidate_id)
                    .ok_or("UPDATE_CANDIDATE_CHANGED")?
                    .bytes = Some(bytes);
                self.phase("verified", None)?;
            }
            Err(code) => self.phase("download_failed", Some(code.into()))?,
        }
        self.status()
    }

    pub fn begin_install(&self, candidate_id: &str) -> Result<(), String> {
        self.ready()?;
        let pending = self
            .pending
            .lock()
            .map_err(|_| "UPDATE_STATE_UNAVAILABLE")?;
        let pending = pending
            .as_ref()
            .filter(|p| p.view.candidate_id == candidate_id)
            .ok_or("UPDATE_CANDIDATE_CHANGED")?;
        if pending.bytes.is_none() {
            return Err("UPDATE_DOWNLOAD_REQUIRED".into());
        }
        self.phase("stopping_fleet", None)
    }

    pub fn install_returned(&self) -> Result<bool, String> {
        let unsettled = read_intent(&self.directory, &self.version)?.is_some();
        self.phase(
            if unsettled {
                "installation_unknown"
            } else {
                "verified"
            },
            Some(
                if unsettled {
                    "UPDATE_INSTALLATION_UNSETTLED"
                } else {
                    "UPDATE_FLEET_STOP_REFUSED"
                }
                .into(),
            ),
        )?;
        Ok(unsettled)
    }

    /// Caller holds the native lifecycle gate and proven local Fleet absence through this closure.
    /// No bytes leave Rust; the only installer input is the official plugin's verified download.
    pub fn install(&self, candidate_id: &str) -> Result<(), String> {
        self.ready()?;
        let mut pending = self
            .pending
            .lock()
            .map_err(|_| "UPDATE_STATE_UNAVAILABLE")?;
        let pending = pending
            .as_mut()
            .filter(|p| p.view.candidate_id == candidate_id)
            .ok_or("UPDATE_CANDIDATE_CHANGED")?;
        let bytes = pending.bytes.as_ref().ok_or("UPDATE_DOWNLOAD_REQUIRED")?;
        let intent = Intent {
            schema: 1,
            from_version: self.version.clone(),
            to_version: pending.view.version.clone(),
            candidate_id: pending.view.candidate_id.clone(),
            source_sha: pending.view.source_sha.clone(),
            artifact_sha256: format!("{:x}", Sha256::digest(bytes)),
        };
        write_intent(&self.directory, &intent)?;
        self.phase("installation_unknown", None)?;
        // Windows exits only after starting the installer. Neither that nor a successful return proves installation.
        pending
            .update
            .install(bytes)
            .map_err(|_| "UPDATE_INSTALLATION_UNSETTLED".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn current_canonical_workflow_accepted() {
        let workflow = crate::workflow_version::canonical();
        let feed = feed_producer::tests::signed_feed(workflow);
        let (updates, _directory, _peer) = download_fixture(false, Some("2.0.0")).await;
        let mut pending = updates.pending.lock().unwrap();
        let update = &mut pending.as_mut().unwrap().update;
        update.download_url = feed["platforms"][TARGET]["url"]
            .as_str()
            .unwrap()
            .parse()
            .unwrap();
        update.raw_json = feed;
        let candidate =
            metadata(update).expect("release producer's canonical workflow must be accepted");
        assert_eq!(candidate.workflow_version, workflow);
        assert_eq!(candidate.version, update.version);
        let mut candidate = serde_json::to_value(candidate).unwrap();
        candidate["candidate_id"] = serde_json::json!("opaque-native-candidate");
        candidate["checked_at"] = serde_json::json!(1792670400.125);
        let golden: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/desktop-update-candidate.json"
        ))
        .unwrap();
        assert_eq!(candidate, golden);
    }

    // The real updater verifies ephemeral signatures against a bounded loopback peer.
    // No private key, production feed request or installer invocation is involved.
    struct Peer(tokio::task::JoinHandle<()>);

    impl Drop for Peer {
        fn drop(&mut self) {
            self.0.abort();
        }
    }

    async fn download_fixture(
        tampered: bool,
        signed_version: Option<&str>,
    ) -> (Updates, tempfile::TempDir, Peer) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let keys = minisign::KeyPair::generate_unencrypted_keypair().unwrap();
        let public =
            base64::engine::general_purpose::STANDARD.encode(keys.pk.to_box().unwrap().to_string());
        let payload = b"fixture bytes that must never be executed";
        let comment = signed_version.map_or("timestamp:1".into(), |version| {
            format!("timestamp:1\tfile:fixture.exe\tversion:{version}")
        });
        let signature = minisign::sign(
            Some(&keys.pk),
            &keys.sk,
            payload.as_slice(),
            Some(&comment),
            None,
        )
        .unwrap();
        let signature = base64::engine::general_purpose::STANDARD.encode(signature.to_string());
        let feed = serde_json::json!({
            "version": "2.0.0",
            "platforms": {TARGET: {
                "url": "https://github.com/TED-H-com/bat-agent-connector/releases/download/v2.0.0/fixture.exe",
                "signature": signature
            }},
            "bat_dashboard": {"api_version":1,"contract_version":CONTRACT,
                "source_sha":"a".repeat(40),"workflow_version":"2026-10-08.10"}
        }).to_string();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = Peer(tokio::spawn(async move {
            while let Ok((mut socket, _)) = listener.accept().await {
                let mut request = Vec::new();
                while !request.ends_with(b"\r\n\r\n") && request.len() < 8192 {
                    match tokio::time::timeout(Duration::from_secs(2), socket.read_u8()).await {
                        Ok(Ok(byte)) => request.push(byte),
                        _ => break,
                    }
                }
                let body = if request.starts_with(b"GET /feed ") {
                    feed.as_bytes()
                } else if tampered {
                    b"different bytes"
                } else {
                    payload.as_slice()
                };
                let header = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                if socket.write_all(header.as_bytes()).await.is_ok() {
                    let _ = socket.write_all(body).await;
                }
            }
        }));
        let config = serde_json::json!({"pubkey":public,"requireSignedVersion":true});
        let mut context = tauri::test::mock_context(tauri::test::noop_assets());
        context
            .config_mut()
            .plugins
            .0
            .insert("updater".into(), config.clone());
        let app = tauri::test::mock_builder()
            .plugin(tauri_plugin_updater::Builder::new().build())
            .build(context)
            .unwrap();
        let update = app
            .updater_builder()
            .endpoints(vec![format!("http://{address}/feed").parse().unwrap()])
            .unwrap()
            .target(TARGET)
            .no_proxy()
            .timeout(Duration::from_secs(3))
            .version_comparator(|_, _| true)
            .build()
            .unwrap()
            .check()
            .await
            .unwrap()
            .unwrap();
        let directory = tempfile::tempdir().unwrap();
        let mut updates = Updates::new(directory.path().into(), "0.1.0".into(), config);
        updates.supported = true;
        updates.finish_check(Ok(Some(update))).unwrap();
        // Only the test-owned download transport changes; signature validation stays official.
        updates
            .pending
            .lock()
            .unwrap()
            .as_mut()
            .unwrap()
            .update
            .download_url = format!("http://{address}/payload").parse().unwrap();
        (updates, directory, peer)
    }

    #[tokio::test]
    async fn released_candidate_keeps_its_completed_check_clock() {
        let (updates, _directory, _peer) = download_fixture(false, Some("2.0.0")).await;
        let first = serde_json::to_value(updates.status().unwrap()).unwrap();
        assert!(first["candidate"]["checked_at"].as_f64().is_some());
        assert_eq!(
            serde_json::to_value(updates.status().unwrap()).unwrap(),
            first
        );
        let failed = serde_json::to_value(updates.finish_check(Err(())).unwrap()).unwrap();
        assert_eq!(failed["candidate"], first["candidate"]);
        assert!(updates.finish_check(Ok(None)).unwrap().candidate.is_none());
    }

    #[tokio::test]
    async fn verified_download_survives_failed_recheck_and_restores_install_review() {
        let (updates, _directory, peer) = download_fixture(false, Some("2.0.0")).await;
        let id = updates.status().unwrap().candidate.unwrap().candidate_id;
        assert_eq!(updates.download(&id).await.unwrap().phase, "verified");
        assert_eq!(updates.finish_check(Err(())).unwrap().phase, "check_failed");
        drop(peer); // Recovery uses the same verified bytes without another request.
        assert_eq!(updates.download(&id).await.unwrap().phase, "verified");
        assert!(updates.begin_install("different-candidate").is_err());
        updates.begin_install(&id).unwrap();
        assert_eq!(updates.status().unwrap().phase, "stopping_fleet");
        assert!(!updates.install_returned().unwrap());
        assert_eq!(updates.status().unwrap().phase, "verified");
    }

    #[tokio::test]
    async fn tampered_or_relabelled_artifacts_never_become_installable() {
        for (tampered, version) in [(true, Some("2.0.0")), (false, Some("1.0.0")), (false, None)] {
            let (updates, _directory, _peer) = download_fixture(tampered, version).await;
            let id = updates.status().unwrap().candidate.unwrap().candidate_id;
            let view = updates.download(&id).await.unwrap();
            assert_eq!(view.phase, "download_failed");
            assert_eq!(
                view.code.as_deref(),
                Some("UPDATE_SIGNATURE_OR_DOWNLOAD_FAILED")
            );
            assert!(updates
                .pending
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .bytes
                .is_none());
            assert_eq!(
                updates.begin_install(&id).unwrap_err(),
                "UPDATE_DOWNLOAD_REQUIRED"
            );
        }
    }

    fn intent() -> Intent {
        Intent {
            schema: 1,
            from_version: "0.1.0".into(),
            to_version: "0.2.0".into(),
            candidate_id: "fixed-candidate".into(),
            source_sha: "a".repeat(40),
            artifact_sha256: "b".repeat(64),
        }
    }

    #[test]
    fn typed_requests_cannot_supply_native_inputs() {
        for value in [
            r#"{"action":"check","url":"https://other.invalid"}"#,
            r#"{"action":"install","candidate_id":"one","path":"installer.exe"}"#,
            r#"{"action":"download","candidate_id":"one","pubkey":"other"}"#,
        ] {
            assert!(serde_json::from_str::<Request>(value).is_err());
        }
        assert!(
            serde_json::from_str::<Request>(r#"{"action":"install","candidate_id":"one"}"#).is_ok()
        );
    }

    #[test]
    fn unsigned_or_unsafe_configuration_is_disabled() {
        assert_eq!(
            key(&serde_json::json!({"pubkey":"", "requireSignedVersion":true})).unwrap_err(),
            "UPDATE_SIGNING_NOT_CONFIGURED"
        );
        assert!(key(&serde_json::json!({"pubkey":"bad", "requireSignedVersion":true})).is_err());
        assert!(key(&serde_json::json!({"pubkey":"", "requireSignedVersion":false})).is_err());
        assert!(key(&serde_json::json!({"pubkey":"", "requireSignedVersion":true, "dangerousAcceptInvalidCerts":true})).is_err());
        for flag in [
            "dangerous-insecure-transport-protocol",
            "dangerous-accept-invalid-certs",
            "dangerous-accept-invalid-hostnames",
            "allow-downgrades",
        ] {
            assert_eq!(
                key(&serde_json::json!({"pubkey":"", "requireSignedVersion":true, flag:true}))
                    .unwrap_err(),
                "UPDATE_CONFIGURATION_INVALID"
            );
        }
    }

    #[test]
    fn network_targets_are_public_fixed_https_origins() {
        for address in [
            "http://github.com/",
            "https://user@github.com/",
            "https://github.com:444/",
            "https://github.com.attacker.invalid/",
            "https://127.0.0.1/",
            "file:///tmp/release",
        ] {
            assert!(!safe_url(&address.parse().unwrap()), "{address}");
        }
        assert!(safe_url(&FEED.parse().unwrap()));
        assert!(safe_url(
            &"https://release-assets.githubusercontent.com/asset?token=temporary"
                .parse()
                .unwrap()
        ));
    }

    #[test]
    fn durable_intent_prevents_retry_after_reopen_without_claiming_installed() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("updates");
        assert!(read_intent(&path, "0.1.0").unwrap().is_none());
        write_intent(&path, &intent()).unwrap();
        assert!(write_intent(&path, &intent()).is_err());
        let updates = Updates::new(path.clone(), "0.1.0".into(), serde_json::Value::Null);
        let view = updates.status().unwrap();
        assert!(updates.keep_fleet_stopped());
        assert_eq!(view.phase, "installation_unknown");
        assert_eq!(view.installation.unwrap().to_version, "0.2.0");
        // A new binary version is its own future update epoch. The original receipt remains immutable.
        assert!(read_intent(&path, "0.2.0").unwrap().is_none());
        let next = Updates::new(path.clone(), "0.2.0".into(), serde_json::Value::Null);
        assert!(!next.keep_fleet_stopped());
        assert!(read_intent(&path, "0.1.0").unwrap().is_some());
    }

    #[test]
    fn partial_or_unknown_receipt_is_never_replaced() {
        let dir = tempfile::tempdir().unwrap();
        let path = marker_path(dir.path(), "0.1.0");
        std::fs::write(&path, b"{").unwrap();
        assert!(read_intent(dir.path(), "0.1.0").is_err());
        let updates = Updates::new(dir.path().into(), "0.1.0".into(), serde_json::Value::Null);
        assert!(updates.keep_fleet_stopped());
        assert!(write_intent(dir.path(), &intent()).is_err());
        assert_eq!(std::fs::read(path).unwrap(), b"{");
    }

    #[test]
    fn refused_quit_preserves_pending_and_corrupt_installer_launch_fences() {
        for receipt in [
            None,
            Some(serde_json::to_vec(&intent()).unwrap()),
            Some(b"{".to_vec()),
        ] {
            let dir = tempfile::tempdir().unwrap();
            if let Some(bytes) = &receipt {
                std::fs::write(marker_path(dir.path(), "0.1.0"), bytes).unwrap();
            }
            // Reopen the desktop, then simulate the shared refusal path after Quit fails.
            let updates = Updates::new(dir.path().into(), "0.1.0".into(), serde_json::Value::Null);
            let control = crate::fleet_control::Control::new(dir.path().join("fleet.json"));
            let old_request = control.ticket();
            control.set_stopping(true);
            crate::restore_fleet_after_refusal(&updates, &control);
            assert_eq!(control.ticket().verify().is_err(), receipt.is_some());
            assert!(old_request.verify().is_err()); // No queued request is revived after refusal.
        }
    }
}
