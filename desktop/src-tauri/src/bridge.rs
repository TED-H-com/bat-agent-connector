use crate::credentials::{self, Identity, Locale, OsVault, Record, Vault};
use regex::Regex;
use reqwest::{redirect::Policy, Client, Method};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
    time::Duration,
};
use url::Url;
use zeroize::Zeroizing;

pub const MAX_ARTIFACT_BYTES: usize = 16 * 1024 * 1024;

const MAX_REQUEST: usize = 1024 * 1024;
const MAX_RESPONSE: usize = 8 * 1024 * 1024;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub endpoint: String,
    pub expected_actor: String,
    pub contract_version: String,
}

impl Config {
    pub fn validate(&self) -> Result<Url, String> {
        let url = Url::parse(&self.endpoint).map_err(|_| "Invalid central endpoint")?;
        let loopback = matches!(url.host_str(), Some("127.0.0.1" | "[::1]"));
        if self.endpoint.len() > 512
            || self.endpoint.chars().any(char::is_control)
            || !(url.scheme() == "https" || url.scheme() == "http" && loopback)
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || url.path() != "/"
        {
            return Err("Endpoint must be an HTTPS origin or a literal loopback HTTP origin, without credentials, path, query or fragment".into());
        }
        if self.expected_actor.trim().is_empty()
            || self.expected_actor.len() > 200
            || self.expected_actor.chars().any(char::is_control)
            || self.contract_version != "2026-10-08"
        {
            return Err(
                "Configure expected_actor and the supported contract_version 2026-10-08".into(),
            );
        }
        Ok(url)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConnectorRequest {
    pub method: String,
    pub path: String,
    pub body: Option<Value>,
    pub idempotency_key: Option<String>,
}

// Only logical selection crosses IPC; central owns Git URLs, paths and credentials.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RepositoryPreviewRequest {
    repository: String,
    host: String,
    workspace_id: String,
    source_ref: String,
}

fn validate_repository_preview(body: Option<&Value>) -> Result<(), String> {
    let doc: RepositoryPreviewRequest =
        serde_json::from_value(body.cloned().ok_or("Repository preview body is required")?)
            .map_err(|_| "Invalid typed repository preview request")?;
    let logical = |s: &str| !s.is_empty() && s.len() <= 256 && !s.chars().any(char::is_control);
    let branch = doc.source_ref.strip_prefix("refs/heads/").unwrap_or("");
    if !logical(&doc.repository)
        || !Regex::new(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
            .unwrap()
            .is_match(&doc.repository)
        || !logical(&doc.host)
        || !doc
            .host
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
        || !logical(&doc.workspace_id)
        || branch.is_empty()
        || branch.len() > 200
        || branch.starts_with('-')
        || branch.ends_with(['.', '/'])
        || branch.contains("..")
        || branch.contains("//")
        || branch
            .split('/')
            .any(|s| s.starts_with('.') || s.ends_with(".lock"))
        || !branch
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._/-".contains(&c))
    {
        return Err("Invalid repository, workspace or published branch identity".into());
    }
    Ok(())
}

// Cleanup previews only describe existing central IDs. They cannot carry headers,
// host paths, commands, credential material, or an altered cleanup apply envelope.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CleanupPreviewRequest {
    target: CleanupTarget,
    #[serde(default)]
    choices: CleanupChoices,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum CleanupTarget {
    Host {
        host: String,
    },
    WorkItem {
        work_item_id: String,
        #[serde(default)]
        include_children: bool,
    },
    Checkpoint {
        checkpoint_id: String,
    },
    Integration {
        operation_id: String,
    },
    Task {
        task_id: String,
    },
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct CleanupChoices {
    #[serde(default)]
    discard_uncommitted: Vec<String>,
    #[serde(default)]
    release_undelivered: Vec<String>,
}

fn validate_cleanup_preview(body: Option<&Value>) -> Result<(), String> {
    let doc: CleanupPreviewRequest =
        serde_json::from_value(body.cloned().ok_or("Cleanup preview body is required")?)
            .map_err(|_| "Invalid typed cleanup preview request")?;
    let valid_id = match doc.target {
        CleanupTarget::Host { host } => {
            !host.is_empty()
                && host.len() <= 200
                && host
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        }
        CleanupTarget::WorkItem {
            work_item_id,
            include_children,
        } => {
            let _ = include_children; // accepted only as the typed boolean for this variant
            Regex::new(r"^wi_[0-9a-f]{20}$")
                .unwrap()
                .is_match(&work_item_id)
        }
        CleanupTarget::Checkpoint { checkpoint_id } => Regex::new(r"^cp_[0-9a-f]{32}$")
            .unwrap()
            .is_match(&checkpoint_id),
        CleanupTarget::Integration { operation_id } => Regex::new(r"^op_[0-9a-f]{32}$")
            .unwrap()
            .is_match(&operation_id),
        CleanupTarget::Task { task_id } => {
            Regex::new(r"^[0-9a-f-]{8,64}$").unwrap().is_match(&task_id)
        }
    };
    let resource = Regex::new(r"^(?:cr|wt)_[0-9a-f]{32}$").unwrap();
    if !valid_id
        || [
            &doc.choices.discard_uncommitted,
            &doc.choices.release_undelivered,
        ]
        .iter()
        .any(|ids| ids.len() > 500 || ids.iter().any(|id| !resource.is_match(id)))
    {
        return Err("Cleanup preview IDs or choice count are invalid".into());
    }
    Ok(())
}

#[derive(Serialize)]
pub struct ConnectorResponse {
    pub status: u16,
    pub data: Value,
}

#[derive(Serialize)]
pub struct NativeStatus {
    pub updates: bool,
    pub file_transfers: bool,
    pub endpoint: Option<String>,
    pub expected_actor: Option<String>,
    pub error: Option<String>,
    pub credential_available: bool,
    pub credential_source: Option<&'static str>,
    pub credential_saved: bool,
    pub enrollment_supported: bool,
    pub configuration_reload: bool,
    pub configuration_setup: bool,
    pub configuration_source: &'static str,
    pub configuration_file: Option<String>,
    pub connected: bool,
}

// Never serialized: credential identity remains native-only even for identical actor/scopes.
#[derive(Clone, PartialEq, Eq)]
pub(crate) struct FileScope {
    pub generation: u64,
    pub binding: String,
    pub actor: String,
}

#[derive(Clone)]
struct Active {
    config: Config,
    record: Record,
}
struct ConnectionState {
    generation: u64,
    managed: bool,
    config: Result<Config, String>,
    environment: Option<Zeroizing<String>>,
    environment_identity: Option<Identity>,
    active: Option<Active>,
}

pub struct Bridge {
    state: Mutex<ConnectionState>,
    configuration_file: Option<PathBuf>,
    vault: Arc<dyn Vault>,
    // One prompt/connection attempt at a time. Disconnect/reload can invalidate a pending attempt.
    connecting: tokio::sync::Mutex<()>,
    client: Client,
}

fn read_configuration(path: &Path) -> Result<Config, String> {
    let file = std::fs::File::open(path)
        .map_err(|_| "Create central.json in the app configuration directory")?;
    let mut bytes = Vec::new();
    file.take(16_385)
        .read_to_end(&mut bytes)
        .map_err(|_| "Unable to read central configuration")?;
    if bytes.len() > 16_384 {
        return Err("Central configuration exceeds its bound".into());
    }
    let config: Config =
        serde_json::from_slice(&bytes).map_err(|_| "Invalid central configuration")?;
    normalize(config)
}
fn normalize(mut config: Config) -> Result<Config, String> {
    config.endpoint = config.validate()?.to_string();
    Ok(config)
}
fn binding(config: &Config) -> String {
    credentials::binding(
        &config.endpoint,
        &config.expected_actor,
        &config.contract_version,
    )
}
fn get(path: &str) -> ConnectorRequest {
    ConnectorRequest {
        method: "GET".into(),
        path: path.into(),
        body: None,
        idempotency_key: None,
    }
}

impl Bridge {
    #[cfg(test)]
    pub(crate) fn file_fixture(endpoint: &str, token: &str) -> Self {
        let config = Config {
            endpoint: endpoint.into(),
            expected_actor: "fixture-operator".into(),
            contract_version: "2026-10-08".into(),
        };
        let bridge = Self::new(Ok(config.clone()), Zeroizing::new(token.into()));
        bridge.state.lock().unwrap().active = Some(Active {
            config: config.clone(),
            record: Record {
                version: 1,
                binding: binding(&config),
                token: Zeroizing::new(token.into()),
                identity: Identity {
                    server_id: "fixture-server".into(),
                    principal_id: "fixture-principal".into(),
                },
            },
        });
        bridge
    }
    pub fn load(config_dir: &Path, token: Zeroizing<String>) -> Self {
        let path = config_dir.join("central.json");
        let mut bridge = Self::new(read_configuration(&path), token);
        bridge.configuration_file = Some(path);
        bridge
    }
    pub fn managed(
        config_dir: &Path,
        config: Config,
        token: Zeroizing<String>,
        identity: Identity,
    ) -> Self {
        let mut bridge = Self::new(Ok(config), token);
        bridge.configuration_file = Some(config_dir.join("central.json"));
        {
            let mut state = bridge.state.lock().unwrap();
            state.environment_identity = Some(identity);
            state.managed = true;
        }
        bridge
    }
    pub fn managed_failure(config_dir: &Path, error: String) -> Self {
        let mut bridge = Self::new(Err(error), Zeroizing::new(String::new()));
        bridge.configuration_file = Some(config_dir.join("central.json"));
        bridge.state.lock().unwrap().managed = true;
        bridge
    }
    fn new(config: Result<Config, String>, token: Zeroizing<String>) -> Self {
        let config = config.and_then(normalize);
        // An environment credential without a valid original endpoint must never be forwarded
        // to whatever endpoint is configured later. The launcher can retry with valid configuration.
        let environment = if config.is_ok() && !token.is_empty() {
            Some(token)
        } else {
            None
        };
        Self {
            state: Mutex::new(ConnectionState {
                generation: 0,
                managed: false,
                config,
                environment,
                environment_identity: None,
                active: None,
            }),
            configuration_file: None,
            vault: Arc::new(OsVault),
            connecting: tokio::sync::Mutex::new(()),
            client: Client::builder()
                .redirect(Policy::none())
                .no_proxy()
                .connect_timeout(Duration::from_secs(10))
                .timeout(Duration::from_secs(30))
                .build()
                .expect("native HTTP client"),
        }
    }
    pub fn status(&self) -> NativeStatus {
        let state = self.state.lock().unwrap();
        let config = state.config.as_ref().ok();
        let stored = config.map(|config| self.vault.read(&binding(config)));
        let saved = matches!(&stored, Some(Ok(Some(_))));
        let store_error = match stored {
            Some(Err(error)) => Some(error),
            Some(Ok(Some(bytes))) => Record::decode(&bytes, &binding(config.unwrap())).err(),
            _ => None,
        };
        let env = state.environment.is_some();
        NativeStatus {
            updates: true,
            file_transfers: true,
            endpoint: config.map(|c| c.endpoint.clone()),
            expected_actor: config.map(|c| c.expected_actor.clone()),
            error: state
                .config
                .as_ref()
                .err()
                .cloned()
                .or(if env { None } else { store_error }),
            credential_available: env || saved,
            credential_source: if env {
                Some("launch_environment")
            } else if saved {
                Some(self.vault.source())
            } else {
                None
            },
            credential_saved: saved,
            enrollment_supported: self.vault.supported(),
            configuration_reload: !state.managed && self.configuration_file.is_some(),
            configuration_setup: (state.managed || state.config.is_err()) && self.configuration_file.as_ref().is_some_and(|path| {
                matches!(path.symlink_metadata(), Err(error) if error.kind() == std::io::ErrorKind::NotFound)
            }),
            configuration_source: if state.managed { "managed" } else { "external" },
            configuration_file: self
                .configuration_file
                .as_ref()
                .map(|p| p.to_string_lossy().into_owned()),
            connected: state.active.is_some(),
        }
    }
    pub fn disconnect(&self) {
        let mut state = self.state.lock().unwrap();
        state.generation += 1;
        state.active = None;
    }
    fn unchanged(&self, generation: u64) -> Result<(), String> {
        if self.state.lock().unwrap().generation != generation {
            return Err(
                "Native connection changed; read the original operation before retrying".into(),
            );
        }
        Ok(())
    }
    pub fn reload_configuration(&self) -> Result<NativeStatus, String> {
        if self.state.lock().unwrap().managed {
            return Err(
                "Review the existing central connection before switching this Dashboard".into(),
            );
        }
        let path = self
            .configuration_file
            .as_ref()
            .ok_or("Configuration reload is unavailable")?;
        {
            let mut state = self.state.lock().unwrap();
            state.generation += 1;
            state.active = None;
            let config = read_configuration(path);
            if state.config.as_ref().ok().map(binding) != config.as_ref().ok().map(binding) {
                state.environment = None;
                state.environment_identity = None; // never move a launch credential to a newly configured origin/account
            }
            state.config = config;
        }
        Ok(self.status())
    }
    // Initial creation only. A native confirmation binds the endpoint/actor before any token
    // can be enrolled. Existing files (including broken symlinks) are never replaced by IPC.
    pub async fn setup_configuration<F>(
        &self,
        config: Config,
        confirm: F,
    ) -> Result<Option<NativeStatus>, String>
    where
        F: FnOnce(&Config) -> bool + Send + 'static,
    {
        let _guard = self
            .connecting
            .try_lock()
            .map_err(|_| "Connection or enrollment already in progress")?;
        let config = normalize(config)?;
        let path = self
            .configuration_file
            .as_ref()
            .ok_or("Configuration setup is unavailable")?;
        let generation = {
            let state = self.state.lock().unwrap();
            if !state.managed && (state.config.is_ok() || state.active.is_some()) {
                return Err(
                    "A central configuration already exists; use the fixed file and reload".into(),
                );
            }
            state.generation
        };
        if !matches!(path.symlink_metadata(), Err(error) if error.kind() == std::io::ErrorKind::NotFound)
        {
            return Err(
                "A central configuration already exists; use the fixed file and reload".into(),
            );
        }
        let candidate = config.clone();
        if !tokio::task::spawn_blocking(move || confirm(&candidate))
            .await
            .map_err(|_| "Native setup confirmation failed")?
        {
            return Ok(None);
        }
        {
            let mut state = self.state.lock().unwrap();
            if state.generation != generation {
                return Err("Connection changed during setup; review configuration again".into());
            }
            let parent = path
                .parent()
                .ok_or("Configuration directory is unavailable")?;
            std::fs::create_dir_all(parent)
                .map_err(|_| "Unable to create configuration directory")?;
            let temporary = parent.join(format!(".central-setup-{}.json", uuid::Uuid::new_v4()));
            let mut options = std::fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options
                .open(&temporary)
                .map_err(|_| "Unable to prepare central configuration")?;
            let saved = (|| {
                let bytes = serde_json::to_vec_pretty(&config)
                    .map_err(|_| "Unable to encode configuration")?;
                file.write_all(&bytes)
                    .and_then(|_| file.sync_all())
                    .map_err(|_| "Unable to save central configuration")?;
                // Atomic, same-directory publication with no overwrite on all supported platforms.
                std::fs::hard_link(&temporary, path).map_err(|_| {
                    "Configuration appeared or could not be created; reload before retrying"
                })
            })();
            drop(file);
            let _ = std::fs::remove_file(&temporary);
            saved?;
            state.generation += 1;
            state.managed = false;
            state.config = Ok(config);
            state.environment = None;
            state.environment_identity = None;
            state.active = None;
        }
        Ok(Some(self.status()))
    }
    pub fn forget_credential(&self) -> Result<(), String> {
        let mut state = self.state.lock().unwrap();
        let config = state.config.as_ref().map_err(Clone::clone)?;
        self.vault.remove(&binding(config))?;
        state.generation += 1;
        state.active = None;
        state.environment = None;
        state.environment_identity = None; // explicit forget must not silently fall back to the launch credential
        Ok(())
    }
    pub async fn connect(&self) -> Result<Value, String> {
        let _guard = self
            .connecting
            .try_lock()
            .map_err(|_| "Connection or enrollment already in progress")?;
        let (generation, config, token, expected) = {
            let state = self.state.lock().unwrap();
            let config = state.config.as_ref().map_err(Clone::clone)?.clone();
            if let Some(token) = &state.environment {
                (
                    state.generation,
                    config,
                    token.clone(),
                    state.environment_identity.clone(),
                )
            } else {
                let bytes = self
                    .vault
                    .read(&binding(&config))?
                    .ok_or("Native credential unavailable; add a credential in Settings")?;
                let record = Record::decode(&bytes, &binding(&config))?;
                (
                    state.generation,
                    config,
                    record.token,
                    Some(record.identity),
                )
            }
        };
        let (caps, identity) = self
            .verify(&config, &token, expected.as_ref(), generation)
            .await?;
        let mut state = self.state.lock().unwrap();
        if state.generation != generation {
            return Err("Connection changed during verification".into());
        }
        state.generation += 1;
        if state.environment.is_some() {
            state.environment_identity = Some(identity.clone());
        }
        state.active = Some(Active {
            record: Record {
                version: 1,
                binding: binding(&config),
                identity,
                token,
            },
            config,
        });
        Ok(caps)
    }
    pub async fn enroll(&self, locale: Locale, parent: isize) -> Result<Option<Value>, String> {
        let _guard = self
            .connecting
            .try_lock()
            .map_err(|_| "Connection or enrollment already in progress")?;
        if !self.vault.supported() {
            return Err("Protected enrollment is unavailable on this platform".into());
        }
        let (generation, config) = {
            let state = self.state.lock().unwrap();
            (
                state.generation,
                state.config.as_ref().map_err(Clone::clone)?.clone(),
            )
        };
        let vault = self.vault.clone();
        let prompt_config = config.clone();
        let token = tauri::async_runtime::spawn_blocking(move || {
            vault.prompt(
                &prompt_config.expected_actor,
                &prompt_config.endpoint,
                locale,
                parent,
            )
        })
        .await
        .map_err(|_| "Native credential dialog did not complete")??;
        self.unchanged(generation)?;
        let Some(token) = token else {
            return Ok(None);
        };
        let (caps, identity) = self.verify(&config, &token, None, generation).await?;
        let record = Record {
            version: 1,
            binding: binding(&config),
            identity,
            token,
        };
        let bytes = record.encode()?;
        let mut state = self.state.lock().unwrap();
        if state.generation != generation {
            return Err("Connection changed; credential was not saved".into());
        }
        // Save only after verified identity and while generation cannot change. Save failure keeps
        // the old active connection and protected record. Explicit replacement may establish a new principal.
        self.vault.write(&record.binding, &bytes)?;
        state.generation += 1;
        state.environment = None;
        state.environment_identity = None;
        state.active = Some(Active { config, record });
        Ok(Some(caps))
    }
    fn verify_caps(config: &Config, caps: &Value) -> Result<(), String> {
        if caps.get("actor").and_then(Value::as_str) != Some(config.expected_actor.as_str())
            || caps.get("api_version").and_then(Value::as_u64) != Some(1)
            || caps.get("contract_version").and_then(Value::as_str)
                != Some(config.contract_version.as_str())
            || !caps
                .get("scopes")
                .and_then(Value::as_array)
                .is_some_and(|scopes| scopes.iter().any(|scope| scope == "observe"))
        {
            return Err(
                "Central identity, contract or observe scope does not match trusted configuration"
                    .into(),
            );
        }
        Ok(())
    }
    fn bootstrap_identity(config: &Config, data: &Value) -> Result<Identity, String> {
        Self::verify_caps(config, &data["capabilities"])?;
        if data["sync"]["version"] != 1 {
            return Err("Central bootstrap identity is required".into());
        }
        let identity = Identity {
            server_id: data["sync"]["server_id"]
                .as_str()
                .ok_or("Missing central server identity")?
                .into(),
            principal_id: data["sync"]["principal_id"]
                .as_str()
                .ok_or("Missing central principal identity")?
                .into(),
        };
        identity.validate()?;
        Ok(identity)
    }
    async fn verify(
        &self,
        config: &Config,
        token: &str,
        expected: Option<&Identity>,
        generation: u64,
    ) -> Result<(Value, Identity), String> {
        self.unchanged(generation)?;
        credentials::validate_token(token)?;
        let response = self.send(config, token, &get("/capabilities")).await?;
        if response.status != 200 {
            return Err(
                "Central authentication failed; check the credential and connection".into(),
            );
        }
        self.unchanged(generation)?;
        Self::verify_caps(config, &response.data)?;
        let bootstrap = self.send(config, token, &get("/bootstrap")).await?;
        if bootstrap.status != 200 {
            return Err("Central bootstrap identity could not be verified".into());
        }
        self.unchanged(generation)?;
        let identity = Self::bootstrap_identity(config, &bootstrap.data)?;
        if expected.is_some_and(|known| known != &identity) {
            return Err("Saved backend or principal identity changed; explicitly replace the credential to accept this identity".into());
        }
        let mut caps = bootstrap.data["capabilities"].clone();
        caps["desktop_identity"] = serde_json::to_value(&identity).unwrap();
        Ok((caps, identity))
    }
    fn active(&self) -> Result<(u64, Active), String> {
        let state = self.state.lock().unwrap();
        Ok((
            state.generation,
            state
                .active
                .clone()
                .ok_or("Connect and verify the configured central identity first")?,
        ))
    }
    fn invalidate(&self, generation: u64) {
        let mut state = self.state.lock().unwrap();
        if state.generation == generation {
            state.generation += 1;
            state.active = None;
        }
    }
    pub async fn request(&self, input: ConnectorRequest) -> Result<ConnectorResponse, String> {
        validate_request(&input)?;
        let (generation, active) = self.active()?;
        if input.method == "POST" {
            // Recheck identity before mutations, including reconnects of an existing loopback tunnel.
            if let Err(error) = self
                .verify(
                    &active.config,
                    &active.record.token,
                    Some(&active.record.identity),
                    generation,
                )
                .await
            {
                self.invalidate(generation);
                return Err(error);
            }
        }
        self.unchanged(generation)?;
        let response = self
            .send(&active.config, &active.record.token, &input)
            .await?;
        self.unchanged(generation)?;
        if response.status == 401 {
            self.invalidate(generation);
        }
        if input.path == "/bootstrap" && response.status == 200 {
            let identity = Self::bootstrap_identity(&active.config, &response.data);
            if identity.as_ref().ok() != Some(&active.record.identity) {
                self.invalidate(generation);
                return Err(
                    "Central backend or principal identity changed; reconnect explicitly".into(),
                );
            }
        }
        Ok(response)
    }
    pub(crate) fn file_scope(&self) -> Result<FileScope, String> {
        use sha2::{Digest, Sha256};
        let (generation, active) = self.active()?;
        let mut digest = Sha256::new();
        for part in [
            binding(&active.config),
            active.record.identity.server_id.clone(),
            active.record.identity.principal_id.clone(),
            active.record.token.to_string(),
        ] {
            let part = Zeroizing::new(part);
            digest.update((part.len() as u64).to_be_bytes());
            digest.update(part.as_bytes());
        }
        Ok(FileScope {
            generation,
            binding: format!("{:x}", digest.finalize()),
            actor: active.config.expected_actor,
        })
    }

    pub(crate) fn check_file_scope(&self, scope: &FileScope) -> Result<(), String> {
        if self.file_scope()? != *scope {
            return Err(
                "File transfer connection changed; reconnect with the original credential".into(),
            );
        }
        Ok(())
    }

    pub(crate) async fn file_request(
        &self,
        scope: &FileScope,
        input: ConnectorRequest,
    ) -> Result<ConnectorResponse, String> {
        validate_request(&input)?;
        self.check_file_scope(scope)?;
        let (generation, active) = self.active()?;
        if generation != scope.generation {
            return Err("File transfer connection changed".into());
        }
        self.verify(
            &active.config,
            &active.record.token,
            Some(&active.record.identity),
            generation,
        )
        .await?;
        self.check_file_scope(scope)?;
        let response = self
            .send(&active.config, &active.record.token, &input)
            .await?;
        self.check_file_scope(scope)?;
        Ok(response)
    }

    pub(crate) async fn file_content(
        &self,
        scope: &FileScope,
        route: &str,
        upload: Option<(reqwest::Body, u64)>,
    ) -> Result<reqwest::Response, String> {
        let download =
            Regex::new(r"^/artifacts/art_[0-9a-f]{32}/revisions/[1-9][0-9]{0,8}/content$").unwrap();
        let receiving = Regex::new(r"^/artifacts/uploads/op_[0-9a-f]{32}/content$").unwrap();
        if !(if upload.is_some() {
            receiving.is_match(route)
        } else {
            download.is_match(route)
        }) {
            return Err("Invalid file content route".into());
        }
        self.check_file_scope(scope)?;
        let (generation, active) = self.active()?;
        if generation != scope.generation {
            return Err("File transfer connection changed".into());
        }
        self.verify(
            &active.config,
            &active.record.token,
            Some(&active.record.identity),
            generation,
        )
        .await?;
        self.check_file_scope(scope)?;
        let url = active
            .config
            .validate()?
            .join(&format!("api/v1{route}"))
            .map_err(|_| "Invalid content route")?;
        let mut request = self
            .client
            .request(
                if upload.is_some() {
                    Method::POST
                } else {
                    Method::GET
                },
                url,
            )
            .bearer_auth(active.record.token.as_str())
            .timeout(Duration::from_secs(300));
        if let Some((body, size)) = upload {
            if size > MAX_ARTIFACT_BYTES as u64 {
                return Err("File exceeds native limit".into());
            }
            request = request
                .header("Content-Type", "application/octet-stream")
                .header("Content-Length", size)
                .body(body);
        }
        let response = request
            .send()
            .await
            .map_err(|_| "File transfer interrupted; retain the original operation")?;
        self.check_file_scope(scope)?;
        if response.status().is_redirection() {
            return Err("Central redirects are refused".into());
        }
        Ok(response)
    }

    #[cfg(test)]
    pub async fn upload_artifact(
        &self,
        operation_id: &str,
        bytes: &[u8],
    ) -> Result<ConnectorResponse, String> {
        validate_artifact_upload(operation_id, bytes.len())?;
        let (generation, active) = self.active()?;
        if let Err(error) = self
            .verify(
                &active.config,
                &active.record.token,
                Some(&active.record.identity),
                generation,
            )
            .await
        {
            self.invalidate(generation);
            return Err(error);
        }
        self.unchanged(generation)?;
        let url = active
            .config
            .validate()?
            .join(&format!("api/v1/artifacts/uploads/{operation_id}/content"))
            .map_err(|_| "Invalid upload operation")?;
        let response = self
            .client
            .post(url)
            .bearer_auth(active.record.token.as_str())
            .header("Content-Type", "application/octet-stream")
            .body(bytes.to_vec())
            .send()
            .await
            .map_err(|_| "Artifact upload interrupted; retry the original operation")?;
        let response = Self::read_response(response).await?;
        self.unchanged(generation)?;
        if response.status == 401 {
            self.invalidate(generation);
        }
        Ok(response)
    }
    async fn send(
        &self,
        config: &Config,
        token: &str,
        input: &ConnectorRequest,
    ) -> Result<ConnectorResponse, String> {
        let url = config
            .validate()?
            .join(&format!("api/v1{}", input.path))
            .map_err(|_| "Invalid central route")?;
        let method = Method::from_bytes(input.method.as_bytes()).map_err(|_| "Invalid method")?;
        let mut request = self.client.request(method, url).bearer_auth(token);
        if let Some(body) = &input.body {
            request = request.json(body);
        }
        if let Some(key) = &input.idempotency_key {
            request = request.header("Idempotency-Key", key);
        }
        let response = request
            .send()
            .await
            .map_err(|_| "Central request did not complete; retry uses the same operation key")?;
        Self::read_response(response).await
    }

    pub(crate) async fn read_response(
        mut response: reqwest::Response,
    ) -> Result<ConnectorResponse, String> {
        let status = response.status().as_u16();
        if (300..400).contains(&status) {
            return Err("Central redirects are refused".into());
        }
        if response
            .content_length()
            .is_some_and(|len| len > MAX_RESPONSE as u64)
        {
            return Err("Central response is too large".into());
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| "Central response interrupted; outcome may be unknown")?
        {
            if bytes.len() + chunk.len() > MAX_RESPONSE {
                return Err("Central response is too large".into());
            }
            bytes.extend_from_slice(&chunk);
        }
        let data = serde_json::from_slice(&bytes).map_err(|_| "Central response is not JSON")?;
        Ok(ConnectorResponse { status, data })
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CapturePreviewRequest {
    host: String,
    session_id: String,
    relative_path: String,
}

fn validate_capture_preview(body: Option<&Value>) -> Result<(), String> {
    let doc: CapturePreviewRequest =
        serde_json::from_value(body.cloned().ok_or("Capture preview body is required")?)
            .map_err(|_| "Invalid typed capture preview request")?;
    if doc.host.is_empty()
        || doc.host.len() > 200
        || !doc
            .host
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        || !(6..=256).contains(&doc.session_id.len())
        || doc.session_id.chars().any(char::is_control)
        || doc.session_id.contains(['/', '\\'])
        || doc.relative_path.is_empty()
        || doc.relative_path.len() > 4096
        || doc.relative_path.contains('\\')
        || doc.relative_path.chars().any(char::is_control)
        || doc
            .relative_path
            .split('/')
            .any(|p| p.is_empty() || p == "." || p == ".." || p.eq_ignore_ascii_case(".git"))
    {
        return Err(
            "Capture requires a host, full session ID and one safe relative file path".into(),
        );
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ManagedCapturePreviewRequest {
    host: String,
    session_id: String,
    relative_path: String,
    execution_operation_id: Option<String>,
    task_id: Option<String>,
    command_id: Option<String>,
}

fn validate_managed_capture_preview(body: Option<&Value>) -> Result<(), String> {
    let value = body.ok_or("Managed capture preview body is required")?;
    let doc: ManagedCapturePreviewRequest = serde_json::from_value(value.clone())
        .map_err(|_| "Invalid typed managed capture preview request")?;
    validate_capture_preview(Some(&serde_json::json!({
        "host": doc.host, "session_id": doc.session_id, "relative_path": doc.relative_path
    })))?;
    // Exact selector field sets also reject explicit nulls and mixed selector kinds.
    let fields = value.as_object().ok_or("Invalid managed capture fields")?;
    let valid = match (&doc.execution_operation_id, &doc.task_id, &doc.command_id) {
        (Some(operation), None, None) => {
            fields.len() == 4
                && Regex::new(r"^op_[0-9a-f]{32}$")
                    .unwrap()
                    .is_match(operation)
        }
        (None, Some(task), Some(command)) => {
            fields.len() == 5
                && Regex::new(r"^[0-9a-f-]{8,64}$").unwrap().is_match(task)
                && !command.is_empty()
                && command.len() <= 256
                && !command.chars().any(char::is_control)
        }
        _ => false,
    };
    if !valid {
        return Err("Select one exact execution operation or task command".into());
    }
    Ok(())
}

#[cfg(test)]
pub fn validate_artifact_upload(operation_id: &str, length: usize) -> Result<(), String> {
    static OPERATION: OnceLock<Regex> = OnceLock::new();
    if length > MAX_ARTIFACT_BYTES
        || !OPERATION
            .get_or_init(|| Regex::new(r"^op_[0-9a-f]{32}$").unwrap())
            .is_match(operation_id)
    {
        return Err("Upload requires an operation ID and at most 16 MiB of bytes".into());
    }
    Ok(())
}

pub fn external_url(input: &str) -> Result<Url, String> {
    let url = Url::parse(input).map_err(|_| "Invalid external URL")?;
    if input.len() > 2048
        || url.scheme() != "https"
        || url.host_str() != Some("github.com")
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
    {
        return Err(
            "Only credential-free GitHub HTTPS links can open in the system browser".into(),
        );
    }
    Ok(url)
}

pub fn validate_request(input: &ConnectorRequest) -> Result<(), String> {
    // Match raw paths before URL parsing so traversal cannot be normalized into an allowed route.
    if input.path.len() > 4096
        || input.path.contains(['\\', '#', '\r', '\n'])
        || input.path.chars().any(char::is_control)
    {
        return Err("Invalid central route".into());
    }
    let (path, query) = input.path.split_once('?').unwrap_or((&input.path, ""));
    let mut encoded = query.bytes();
    while let Some(byte) = encoded.next() {
        if byte == b'%'
            && !(encoded.next().is_some_and(|b| b.is_ascii_hexdigit())
                && encoded.next().is_some_and(|b| b.is_ascii_hexdigit()))
        {
            return Err("Malformed central query encoding".into());
        }
    }
    if path.contains('%') || path.split('/').any(|part| part == "." || part == "..") {
        return Err("Encoded or relative central route segments are refused".into());
    }
    static GET: OnceLock<Regex> = OnceLock::new();
    static POST: OnceLock<Regex> = OnceLock::new();
    let pattern = if input.method == "GET" {
        GET.get_or_init(|| Regex::new(concat!(r"^/(?:version|capabilities|bootstrap|hosts|workspaces|sessions|policy|operations|events|checkpoints|projects|work-items|integrations|integrations/candidates|managed/setup|",
            r"pm/profiles|pm/workspaces(?:/ws_[0-9a-f]{32}(?:/threads(?:/pmt_[0-9a-f]{32}(?:/context)?)?|/runs(?:/pmr_[0-9a-f]{32})?|/plans(?:/(?:head|accepted|pmrev_[0-9a-f]{32}))?|/projects|/plan-comparison|/roadmap(?:/revisions(?:/pmr[bfo]_[0-9a-f]{32})?)?|/snapshots(?:/(?:latest|pmsnap_[0-9a-f]{32}))?|/work(?:/wi_[0-9a-f]{20}/capture)?|/evidence(?:/pme_[0-9a-f]{32})?|/captures/pmcap_[0-9a-f]{32}|/capabilities)?)?|",
            r"cleanup-retained|cleanup-tombstones(?:/(?:cr|wt)_[0-9a-f]{32})?|artifacts(?:/art_[0-9a-f]{32}/revisions/[1-9][0-9]{0,8})?|",
            r"sessions/[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+(?:/(?:messages|checkpoint-preview|history|relations|instructions))?|",
            r"operations/op_[0-9a-f]{32}|tasks/[0-9a-f-]{8,64}(?:/(?:history|sessions))?|checkpoints/cp_[0-9a-f]{32}|",
            r"hosts/[A-Za-z0-9_.-]+/(?:discovery|preferences)|worktrees/wt_[0-9a-f]{32}(?:/(?:history|relations))?|",
            r"projects/prj_[0-9a-f]{20}(?:/(?:skills|repair-evidence))?|work-items/wi_[0-9a-f]{20}(?:/(?:result-sources|repair))?|repositories/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pulls(?:/[0-9]{1,9})?|",
            r"deployments(?:/(?:preview|dep_[0-9a-f]{32}))?|deployment-environments(?:/history)?|",
            r"delivery/previews/mpv_[0-9a-f]{32}|integrations/previews/ipv_[0-9a-f]{32}|integrations/op_[0-9a-f]{32})$")).unwrap())
    } else if input.method == "POST" {
        POST.get_or_init(|| {
            Regex::new(r"^/(?:managed/setup/secrets|repository-previews|approval-previews|artifact-capture-previews|artifact-managed-capture-previews|cleanup-previews|operations(?:/op_[0-9a-f]{32}/(?:cancel|resume))?)$")
                .unwrap()
        })
    } else {
        return Err("Only defined central GET and operation POST requests are allowed".into());
    };
    if !pattern.is_match(path) {
        return Err("Central route is not allowed".into());
    }
    if matches!(path, "/managed/setup" | "/managed/setup/secrets") {
        if input.path != path || input.idempotency_key.is_some() {
            return Err("Managed setup accepts no query or operation key".into());
        }
        if path == "/managed/setup/secrets" {
            let body = input
                .body
                .as_ref()
                .and_then(Value::as_object)
                .ok_or("Secret staging needs a typed body")?;
            if body.len() != 2
                || !matches!(
                    body.get("kind").and_then(Value::as_str),
                    Some("bat" | "github")
                )
                || !body
                    .get("value")
                    .and_then(Value::as_str)
                    .is_some_and(|value| {
                        (8..=4096).contains(&value.len())
                            && value.bytes().all(|b| b.is_ascii_graphic())
                    })
            {
                return Err("Invalid staged credential fields".into());
            }
        }
    }
    if path == "/artifact-capture-previews" {
        if input.path != path || input.idempotency_key.is_some() {
            return Err("Capture preview accepts no query or operation key".into());
        }
        validate_capture_preview(input.body.as_ref())?;
    }
    if path == "/repository-previews" {
        if input.path != path || input.idempotency_key.is_some() {
            return Err("Repository preview accepts no query or operation key".into());
        }
        validate_repository_preview(input.body.as_ref())?;
    }
    if path == "/approval-previews" {
        if input.path != path || input.idempotency_key.is_some() {
            return Err("Approval preview accepts no query or operation key".into());
        }
        let body = input
            .body
            .as_ref()
            .and_then(Value::as_object)
            .ok_or("Approval preview needs a typed body")?;
        if body
            .keys()
            .any(|key| !matches!(key.as_str(), "host" | "workspace"))
            || !body
                .get("host")
                .and_then(Value::as_str)
                .is_some_and(|host| {
                    !host.is_empty()
                        && host.len() <= 200
                        && host
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
                })
            || body.get("workspace").is_some_and(|value| {
                !value.as_str().is_some_and(|workspace| {
                    !workspace.is_empty()
                        && workspace.len() <= 512
                        && !workspace.chars().any(char::is_control)
                })
            })
        {
            return Err("Invalid approval preview scope".into());
        }
    }
    if path == "/artifact-managed-capture-previews" {
        if input.path != path || input.idempotency_key.is_some() {
            return Err("Managed capture preview accepts no query or operation key".into());
        }
        validate_managed_capture_preview(input.body.as_ref())?;
    }
    let pm = path.starts_with("/pm/");
    if pm && input.idempotency_key.is_some() {
        return Err("PM reads accept no operation key".into());
    }
    let cleanup = path.starts_with("/cleanup-");
    let repair_evidence = path.ends_with("/repair-evidence");
    let instructions = path.ends_with("/instructions");
    if path.ends_with("/repair") && !query.is_empty() {
        return Err("Fixed repair work accepts no query selectors".into());
    }
    if instructions && path.split('/').skip(2).take(2).any(|part| part.len() > 256) {
        return Err("Instruction receipt host and session IDs are bounded".into());
    }
    if path == "/workspaces" && input.idempotency_key.is_some() {
        return Err("Workspace discovery accepts no operation key".into());
    }
    let mut query_keys = std::collections::HashSet::new();
    for (key, value) in url::form_urlencoded::parse(query.as_bytes()) {
        if key.chars().chain(value.chars()).any(char::is_control) {
            return Err("Control characters in central query are refused".into());
        }
        if pm {
            let thread_list = path.ends_with("/threads");
            let run_list = path.ends_with("/runs");
            let plan_list = path.ends_with("/plans");
            let project_list = path.ends_with("/projects");
            let work_list = path.ends_with("/work");
            let evidence_list = path.ends_with("/evidence");
            let snapshot_list = path.ends_with("/snapshots");
            let comparison = path.ends_with("/plan-comparison");
            let roadmap_list = path.ends_with("/roadmap/revisions");
            let sequence_list = plan_list
                || project_list
                || work_list
                || evidence_list
                || snapshot_list
                || roadmap_list;
            let thread_detail = path
                .split('/')
                .nth(5)
                .is_some_and(|id| id.starts_with("pmt_"))
                && !path.ends_with("/context");
            let valid = match key.as_ref() {
                "from_revision_id" | "to_revision_id" if comparison => {
                    value.len() == 38
                        && value.starts_with("pmrev_")
                        && value[6..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "binding_ids" if comparison => {
                    value.is_empty()
                        || (value.split(',').count() <= 100
                            && value.split(',').all(|id| {
                                id.len() == 37
                                    && id.starts_with("pmwb_")
                                    && id[5..]
                                        .bytes()
                                        .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                            }))
                }
                "journal_cursor" if comparison => {
                    !value.is_empty()
                        && value.len() <= 9
                        && value.bytes().all(|b| b.is_ascii_digit())
                }
                "kind" if roadmap_list => {
                    matches!(value.as_ref(), "baseline" | "forecast" | "observation")
                }
                "limit" if thread_list || thread_detail || run_list || sequence_list => {
                    !value.is_empty()
                        && value.len() <= 3
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=100).contains(&n))
                }
                "after" if thread_list => {
                    value.len() == 36
                        && value.starts_with("pmt_")
                        && value[4..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "after" if run_list => {
                    value.len() == 36
                        && value.starts_with("pmr_")
                        && value[4..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "after" if sequence_list => {
                    !value.is_empty()
                        && value.len() <= 9
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u32>().is_ok()
                }
                "project_id" if work_list => {
                    value.len() == 24
                        && value.starts_with("prj_")
                        && value[4..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "work_item_id" if evidence_list => {
                    value.len() == 23
                        && value.starts_with("wi_")
                        && value[3..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "thread_id" if run_list => {
                    value.len() == 36
                        && value.starts_with("pmt_")
                        && value[4..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "before_message" | "before_input" if thread_detail => {
                    !value.is_empty()
                        && value.len() <= 9
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| n > 0)
                }
                "max_chars" if path.ends_with("/context") => {
                    !value.is_empty()
                        && value.len() <= 5
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value
                            .parse::<u32>()
                            .is_ok_and(|n| (2000..=64000).contains(&n))
                }
                _ => false,
            };
            if !valid || !query_keys.insert(key.to_string()) {
                return Err("Invalid PM read query".into());
            }
            continue;
        }
        if repair_evidence {
            let valid = match key.as_ref() {
                "kind" => matches!(value.as_ref(), "discovery" | "operation"),
                "host" => {
                    !value.is_empty()
                        && value.len() <= 256
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
                }
                "profile_id" => !value.is_empty() && value.len() <= 2048,
                "operation_id" => {
                    value.len() == 35
                        && value.starts_with("op_")
                        && value[3..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                _ => false,
            };
            if !valid || !query_keys.insert(key.to_string()) {
                return Err("Invalid fixed repair evidence selector".into());
            }
            continue;
        }
        if instructions {
            let valid = match key.as_ref() {
                "limit" => {
                    !value.is_empty()
                        && value.len() <= 3
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=100).contains(&n))
                }
                "cursor" => {
                    !value.is_empty()
                        && value.len() <= 2048
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"-_=".contains(&b))
                }
                _ => false,
            };
            if !valid || !query_keys.insert(key.to_string()) {
                return Err("Invalid instruction receipt page query".into());
            }
            continue;
        }
        let preferences = path.starts_with("/hosts/") && path.ends_with("/preferences");
        let skills = path.starts_with("/projects/") && path.ends_with("/skills");
        if preferences || skills {
            let valid = match key.as_ref() {
                "refresh" => matches!(value.as_ref(), "true" | "false" | "1" | "0"),
                "agent" if preferences => matches!(value.as_ref(), "claude" | "codex"),
                "session_id" if preferences => !value.is_empty() && value.len() <= 256,
                "host" if skills => {
                    !value.is_empty()
                        && value.len() <= 256
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
                }
                "workspace_id" if skills => !value.is_empty() && value.len() <= 256,
                _ => false,
            };
            if !valid || !query_keys.insert(key.to_string()) {
                return Err("Invalid preferences or skill catalog query".into());
            }
            continue;
        }
        if key == "max_message_chars" {
            if input.method != "GET"
                || !path.starts_with("/sessions/")
                || !path.ends_with("/messages")
                || !value.bytes().all(|byte| byte.is_ascii_digit())
                || !value
                    .parse::<u32>()
                    .is_ok_and(|limit| (1..=60000).contains(&limit))
                || !query_keys.insert(key.to_string())
            {
                return Err("Session message size query is invalid".into());
            }
            continue;
        }
        if path == "/workspaces" {
            let valid = match key.as_ref() {
                "host" => {
                    !value.is_empty()
                        && value.len() <= 256
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
                }
                "limit" => {
                    value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=200).contains(&n))
                }
                _ => false,
            };
            if !valid || !query_keys.insert(key.to_string()) {
                return Err("Workspace query parameter is invalid".into());
            }
            continue;
        }
        if path.starts_with("/repositories/") && path.ends_with("/pulls") {
            let allowed = match key.as_ref() {
                "state" => matches!(value.as_ref(), "open" | "closed" | "all"),
                "page" => {
                    value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=1000).contains(&n))
                }
                _ => false,
            };
            if !allowed || !query_keys.insert(key.to_string()) {
                return Err("PR list query parameter is invalid".into());
            }
            continue;
        }
        if path.ends_with("/result-sources") {
            let valid = match key.as_ref() {
                "limit" => {
                    value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=100).contains(&n))
                }
                "after" => {
                    value.len() == 23
                        && value.starts_with("wi_")
                        && value[3..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                _ => false,
            };
            if !valid || !query_keys.insert(key.to_string()) {
                return Err("Invalid result source page query".into());
            }
            continue;
        }
        if cleanup {
            let allowed = match path {
                "/cleanup-retained" => matches!(
                    key.as_ref(),
                    "host" | "resource_id" | "query" | "cursor" | "limit"
                ),
                "/cleanup-tombstones" => matches!(
                    key.as_ref(),
                    "host" | "kind" | "query" | "original_id" | "work_item_id" | "cursor" | "limit"
                ),
                _ => false,
            };
            if !allowed
                || !query_keys.insert(key.to_string())
                || value.len() > 4096
                || key == "limit" && !value.parse::<u16>().is_ok_and(|n| (1..=200).contains(&n))
            {
                return Err("Cleanup query parameter is invalid".into());
            }
            continue;
        }
        if key == "unread" {
            if input.method != "GET"
                || path != "/work-items"
                || !matches!(value.as_ref(), "true" | "false")
                || !query_keys.insert(key.to_string())
            {
                return Err("Unread filter is only a boolean work-item list query".into());
            }
            continue;
        }
        if matches!(key.as_ref(), "source_kind" | "source_id") {
            let valid = if key == "source_kind" {
                matches!(value.as_ref(), "execution" | "task_command")
            } else {
                !value.is_empty() && value.len() <= 256
            };
            if input.method != "GET"
                || path != "/integrations/candidates"
                || !valid
                || !query_keys.insert(key.to_string())
            {
                return Err("Delivery source selector is invalid".into());
            }
            continue;
        }
        if !matches!(
            key.as_ref(),
            "after"
                | "limit"
                | "cursor"
                | "host"
                | "access"
                | "attention"
                | "status"
                | "pending"
                | "include_archived"
                | "last_n"
                | "method"
                | "from_event"
                | "wait"
                | "resource_type"
                | "resource_id"
                | "session_id"
                | "repository"
                | "recipe"
                | "checkpoint"
                | "project_id"
                | "state"
                | "live"
                | "action"
                | "actor"
                | "before"
                | "discovery"
                | "execution_id"
                | "has_tab"
                | "include_closed"
                | "include_gone"
                | "include_tools"
                | "kind"
                | "lifecycle"
                | "loaded"
                | "max_chars"
                | "offset"
                | "order"
                | "profile_id"
                | "provenance"
                | "provider"
                | "pull_number"
                | "related_resource_id"
                | "related_resource_type"
                | "relation_scope"
                | "since"
                | "stale"
                | "streaming"
                | "until"
                | "work_item_id"
        ) {
            return Err("Central query parameter is not allowed".into());
        }
    }
    if query_keys.contains("source_kind") != query_keys.contains("source_id") {
        return Err("Delivery source selector requires kind and ID".into());
    }
    if repair_evidence {
        let kind = url::form_urlencoded::parse(query.as_bytes())
            .find(|(key, _)| key == "kind")
            .map(|(_, value)| value.into_owned());
        let exact = match kind.as_deref() {
            Some("discovery") => {
                query_keys.len() == 3
                    && query_keys.contains("host")
                    && query_keys.contains("profile_id")
            }
            Some("operation") => query_keys.len() == 2 && query_keys.contains("operation_id"),
            _ => false,
        };
        if !exact {
            return Err("Repair evidence requires one exact discovery or operation source".into());
        }
    }
    if path.ends_with("/skills")
        && !(query_keys.contains("host") && query_keys.contains("workspace_id"))
    {
        return Err("Skill catalog requires host and workspace ID".into());
    }
    if input.method == "GET" && input.body.is_some() {
        return Err("GET requests cannot have a body".into());
    }
    if input
        .body
        .as_ref()
        .is_some_and(|b| b.to_string().len() > MAX_REQUEST)
    {
        return Err("Operation request is too large".into());
    }
    if path == "/cleanup-previews" {
        validate_cleanup_preview(input.body.as_ref())?;
    }
    if let Some(key) = &input.idempotency_key {
        if key.is_empty() || key.len() > 256 || !key.bytes().all(|c| c.is_ascii_graphic()) {
            return Err("Invalid operation idempotency key".into());
        }
    }
    if input.method == "POST" && path == "/operations" && input.idempotency_key.is_none() {
        return Err("Operation creation requires its original idempotency key".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::mpsc,
        thread,
    };
    #[test]
    fn managed_setup_has_only_typed_private_staging_and_fixed_routes() {
        assert!(validate_request(&request("GET", "/managed/setup")).is_ok());
        let mut input = request("POST", "/managed/setup/secrets");
        input.body = Some(serde_json::json!({"kind":"bat", "value":"synthetic-fixture-token"}));
        assert!(validate_request(&input).is_ok());
        for path in [
            "/managed/setup/secrets?",
            "/managed/setup/secrets?token=secret",
            "/managed/stop",
            "/managed/setup/secrets/other",
        ] {
            input.path = path.into();
            assert!(validate_request(&input).is_err());
        }
        input.path = "/managed/setup/secrets".into();
        for body in [
            serde_json::json!({"kind":["bat"],"value":"synthetic-fixture-token"}),
            serde_json::json!({"kind":"github","value":"synthetic-fixture-token","path":"/tmp/token"}),
            serde_json::json!({"kind":"bat","value":"token with whitespace"}),
        ] {
            input.body = Some(body);
            assert!(validate_request(&input).is_err());
        }
        assert!(validate_request(&request("GET", "/managed/setup?host=other")).is_err());
        assert!(validate_request(&request("GET", "/managed/setup/secrets")).is_err());
    }
    #[test]
    fn product_reads_use_only_scoped_bounded_queries() {
        for path in [
            "/hosts/fixture/preferences?agent=claude&session_id=exact&refresh=1",
            "/projects/prj_00000000000000000000/skills?host=fixture&workspace_id=one",
            "/work-items/wi_00000000000000000000/result-sources?limit=100&after=wi_11111111111111111111",
            "/repositories/owner/repo/pulls?state=closed&page=1000",
        ] {
            assert!(validate_request(&get(path)).is_ok(), "{path}");
        }
        for path in [
            "/hosts/fixture/preferences?agent=other",
            "/hosts/fixture/preferences?refresh=1&refresh=0",
            "/projects/prj_00000000000000000000/skills?host=fixture",
            "/projects/prj_00000000000000000000/skills?host=fixture&workspace_id=one&path=/etc",
            "/work-items/wi_00000000000000000000/result-sources?limit=101",
            "/work-items/wi_00000000000000000000/result-sources?after=other",
            "/repositories/owner/repo/pulls?state=merged",
            "/repositories/owner/repo/pulls?page=1001",
            "/repositories/owner/repo/pulls?page=1&page=2",
        ] {
            assert!(validate_request(&get(path)).is_err(), "{path}");
        }
    }

    #[test]
    fn repair_and_instruction_reads_require_exact_bounded_sources() {
        for path in [
            "/projects/prj_00000000000000000000/repair-evidence?kind=discovery&host=fixture&profile_id=default",
            "/projects/prj_00000000000000000000/repair-evidence?kind=operation&operation_id=op_00000000000000000000000000000000",
            "/work-items/wi_00000000000000000000/repair",
            "/sessions/fixture/exact/instructions",
            "/sessions/fixture/exact/instructions?limit=100&cursor=YQ_-",
        ] {
            assert!(validate_request(&get(path)).is_ok(), "{path}");
            assert!(validate_request(&request("POST", path)).is_err(), "{path}");
        }
        for query in ["", "kind=discovery&host=fixture", "kind=discovery&host=fixture&profile_id=",
            "kind=discovery&host=fixture&profile_id=default&host=other",
            "kind=discovery&host=fixture&profile_id=default&operation_id=op_00000000000000000000000000000000",
            "kind=operation&operation_id=bad", "kind=operation&operation_id=op_00000000000000000000000000000000&prompt=override"] {
            assert!(validate_request(&get(&format!("/projects/prj_00000000000000000000/repair-evidence?{query}"))).is_err(), "{query}");
        }
        for query in [
            "limit=0",
            "limit=101",
            "limit=1&limit=2",
            "limit=",
            "cursor=",
            "cursor=a%0Ab",
            "cursor=a&cursor=b",
            "limit=1.0",
            "host=other",
            "cancel=true",
        ] {
            assert!(
                validate_request(&get(&format!(
                    "/sessions/fixture/exact/instructions?{query}"
                )))
                .is_err(),
                "{query}"
            );
        }
        assert!(validate_request(&get(
            "/work-items/wi_00000000000000000000/repair?host=other"
        ))
        .is_err());
        assert!(validate_request(&get(&format!(
            "/sessions/fixture/{}/instructions",
            "s".repeat(257)
        )))
        .is_err());
        assert!(validate_request(&get(&format!(
            "/sessions/fixture/exact/instructions?cursor={}",
            "a".repeat(2049)
        )))
        .is_err());
    }

    #[test]
    fn session_message_character_limit_is_bounded_and_route_specific() {
        assert!(validate_request(&get(
            "/sessions/fixture/session-1/messages?max_message_chars=60000"
        ))
        .is_ok());
        for route in [
            "/sessions?max_message_chars=60000",
            "/sessions/fixture/session-1/messages?max_message_chars=60001",
            "/sessions/fixture/session-1/messages?max_message_chars=0",
            "/sessions/fixture/session-1/messages?max_message_chars=60000&max_message_chars=1",
        ] {
            assert!(validate_request(&get(route)).is_err());
        }
    }

    #[test]
    fn workspace_discovery_has_fixed_bounded_read_contract() {
        for path in [
            "/workspaces",
            "/workspaces?host=build-east&limit=200",
            "/workspaces?limit=1",
        ] {
            assert!(validate_request(&request("GET", path)).is_ok(), "{path}");
            assert!(validate_request(&request("POST", path)).is_err(), "{path}");
        }
        for path in [
            "/workspaces?host=",
            "/workspaces?host=a&host=b",
            "/workspaces?limit=0",
            "/workspaces?limit=201",
            "/workspaces?limit=1&limit=2",
            "/workspaces?limit=abc",
            "/workspaces?limit=%2B1",
            "/workspaces?host=../other",
            "/workspaces?host=https%3A%2F%2Fother",
            "/workspaces?host=x%0Ay",
            "/workspaces?session_id=x",
            "/workspaces?path=x",
            "/%77orkspaces",
            "/workspaces/other",
        ] {
            assert!(validate_request(&request("GET", path)).is_err(), "{path}");
        }
        let mut input = request("GET", "/workspaces?host=demo");
        input.body = Some(serde_json::json!({"host":"other"}));
        assert!(validate_request(&input).is_err());
        input.body = None;
        input.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&input).is_err());
    }

    #[test]
    fn pm_reads_are_exact_scoped_and_bounded() {
        let workspace = "/pm/workspaces/ws_00000000000000000000000000000000";
        let thread = format!("{workspace}/threads/pmt_11111111111111111111111111111111");
        for path in [
            "/pm/profiles".into(),
            "/pm/workspaces".into(),
            workspace.into(),
            format!("{workspace}/threads?limit=100&after=pmt_11111111111111111111111111111111"),
            thread.clone(),
            format!("{thread}?limit=1&before_message=3&before_input=2"),
            format!("{thread}/context?max_chars=64000"),
            format!("{workspace}/capabilities"),
            format!("{workspace}/runs?limit=100&after=pmr_22222222222222222222222222222222&thread_id=pmt_11111111111111111111111111111111"),
            format!("{workspace}/runs/pmr_22222222222222222222222222222222"),
            format!("{workspace}/plans?limit=100&after=0"),
            format!("{workspace}/projects?limit=100&after=0"),
            format!("{workspace}/plan-comparison?from_revision_id=pmrev_33333333333333333333333333333333&to_revision_id=pmrev_44444444444444444444444444444444&binding_ids=pmwb_55555555555555555555555555555555&journal_cursor=0"),
            format!("{workspace}/roadmap"),
            format!("{workspace}/roadmap/revisions?kind=forecast&limit=100&after=0"),
            format!("{workspace}/roadmap/revisions/pmrb_66666666666666666666666666666666"),
            format!("{workspace}/roadmap/revisions/pmrf_66666666666666666666666666666666"),
            format!("{workspace}/roadmap/revisions/pmro_66666666666666666666666666666666"),
            format!("{workspace}/snapshots?limit=100&after=0"),
            format!("{workspace}/snapshots/latest"),
            format!("{workspace}/snapshots/pmsnap_66666666666666666666666666666666"),
            format!("{workspace}/work?limit=100&after=0&project_id=prj_11111111111111111111"),
            format!("{workspace}/work/wi_11111111111111111111/capture"),
            format!("{workspace}/evidence?limit=1&after=0&work_item_id=wi_11111111111111111111"),
            format!("{workspace}/evidence/pme_44444444444444444444444444444444"),
            format!("{workspace}/captures/pmcap_55555555555555555555555555555555"),
            format!("{workspace}/plans/head"),
            format!("{workspace}/plans/accepted"),
            format!("{workspace}/plans/pmrev_33333333333333333333333333333333"),
        ] {
            assert!(validate_request(&get(&path)).is_ok(), "{path}");
        }
        for path in [
            "/pm/workspaces/ws_other".into(),
            "/pm/profiles?limit=1".into(),
            format!("{workspace}?limit=1"),
            format!("{workspace}/threads?limit=101"),
            format!("{workspace}/threads?limit=1&limit=2"),
            format!("{workspace}/threads?after=wrong"),
            format!("{thread}?before_message=0"),
            format!("{thread}?before_message=9999999999999999999"),
            format!("{thread}?before_input=-1"),
            format!("{thread}?actor=other"),
            format!("{thread}/context?max_chars=1999"),
            format!("{thread}/context?max_chars=64001"),
            format!("{thread}/context?limit=1"),
            format!("{thread}/adopt"),
            format!("{workspace}/capabilities?limit=1"),
            format!("{workspace}/runs?limit=101"),
            format!("{workspace}/runs?after=pmt_11111111111111111111111111111111"),
            format!("{workspace}/runs?thread_id=other"),
            format!("{workspace}/runs?limit=1&limit=2"),
            format!("{workspace}/runs?actor=other"),
            format!("{workspace}/runs/pmr_22222222222222222222222222222222?limit=1"),
            format!("{workspace}/runs/pmr_22222222222222222222222222222222/start"),
            format!("{workspace}/plans?limit=101"),
            format!("{workspace}/projects?after=-1"),
            format!("{workspace}/plan-comparison?from_revision_id=other"),
            format!("{workspace}/plan-comparison?to_revision_id=pmrev_33333333333333333333333333333333&to_revision_id=pmrev_44444444444444444444444444444444"),
            format!("{workspace}/plan-comparison?binding_ids=wrong"),
            format!("{workspace}/plan-comparison?journal_cursor=-1"),
            format!("{workspace}/plan-comparison?journal_cursor=1.5"),
            format!("{workspace}/plan-comparison?actor=other"),
            format!("{workspace}/plan-comparison?limit=10"),
            format!("{workspace}/roadmap?kind=forecast"),
            format!("{workspace}/roadmap/revisions?kind=unknown"),
            format!("{workspace}/roadmap/revisions?kind=baseline&kind=forecast"),
            format!("{workspace}/roadmap/revisions/pmrb_66666666666666666666666666666666?limit=1"),
            format!("{workspace}/snapshots?limit=101"),
            format!("{workspace}/snapshots?after=1&after=2"),
            format!("{workspace}/snapshots/latest?actor=owner"),
            format!("{workspace}/snapshots/pmsnap_66666666666666666666666666666666?limit=1"),
            format!("{workspace}/work?project_id=prj_bad"),
            format!("{workspace}/work?project_id=prj_11111111111111111111&project_id=prj_11111111111111111111"),
            format!("{workspace}/work/wi_11111111111111111111/capture?limit=1"),
            format!("{workspace}/evidence?work_item_id=wi_bad"),
            format!("{workspace}/evidence?limit=101"),
            format!("{workspace}/evidence?actor=owner"),
            format!("{workspace}/evidence/pme_44444444444444444444444444444444?after=1"),
            format!("{workspace}/captures/pmcap_55555555555555555555555555555555?work_item_id=wi_11111111111111111111"),
            format!("{workspace}/plans?after=-1"),
            format!("{workspace}/plans?after=pmrev_33333333333333333333333333333333"),
            format!("{workspace}/plans?after=0&after=1"),
            format!("{workspace}/plans?actor=other"),
            format!("{workspace}/plans/head?limit=1"),
            format!("{workspace}/plans/accepted?revision_id=other"),
            format!("{workspace}/plans/pmrev_33333333333333333333333333333333/adopt"),
        ] {
            assert!(validate_request(&get(&path)).is_err(), "{path}");
        }
        let mut keyed = get(workspace);
        keyed.idempotency_key = Some("unexpected".into());
        assert!(validate_request(&keyed).is_err());
        assert!(validate_request(&request("POST", &thread)).is_err());
    }

    fn request(method: &str, path: &str) -> ConnectorRequest {
        ConnectorRequest {
            method: method.into(),
            path: path.into(),
            body: None,
            idempotency_key: None,
        }
    }

    #[test]
    fn unread_filter_is_limited_to_work_item_lists() {
        for path in [
            "/work-items?unread=true&limit=50",
            "/work-items?unread=false&cursor=1%7Cwi_00000000000000000001",
        ] {
            assert!(validate_request(&request("GET", path)).is_ok(), "{path}");
        }
        for path in [
            "/sessions?unread=true",
            "/work-items?unread=1",
            "/work-items?unread=",
            "/work-items?unread=true&unread=false",
            "/work-items/wi_00000000000000000001?unread=true",
        ] {
            assert!(validate_request(&request("GET", path)).is_err(), "{path}");
        }
    }

    #[test]
    fn repository_preview_has_only_fixed_logical_selection() {
        let valid = serde_json::json!({"repository":"example/project", "host":"demo", "workspace_id":"ws-1", "source_ref":"refs/heads/topic/version"});
        let mut input = request("POST", "/repository-previews");
        input.body = Some(valid.clone());
        assert!(validate_request(&input).is_ok());
        for (field, value) in [
            (
                "repository",
                serde_json::json!("https://github.com/example/project"),
            ),
            ("host", serde_json::json!("host/other")),
            ("workspace_id", serde_json::json!({"path":"/tmp"})),
            ("source_ref", serde_json::json!("main")),
            ("source_ref", serde_json::json!("refs/heads/../../main")),
            ("source_ref", serde_json::json!("refs/heads/main.lock")),
            ("source_ref", serde_json::json!("refs/heads/.hidden")),
            ("remote_url", serde_json::json!("https://other")),
            ("force", serde_json::json!(true)),
        ] {
            let mut invalid = valid.clone();
            invalid[field] = value;
            input.body = Some(invalid);
            assert!(validate_request(&input).is_err(), "{field}");
        }
        input.body = Some(valid);
        for path in [
            "/repository-previews?",
            "/repository-previews?host=other",
            "/repository-previews/other",
        ] {
            input.path = path.into();
            assert!(validate_request(&input).is_err());
        }
        input.path = "/repository-previews".into();
        input.idempotency_key = Some("unexpected".into());
        assert!(validate_request(&input).is_err());
        assert!(validate_request(&request("GET", "/repository-previews")).is_err());
    }

    #[test]
    fn approval_preview_has_a_fixed_typed_route_and_scope() {
        let mut input = request("POST", "/approval-previews");
        for body in [
            serde_json::json!({"host": "demo"}),
            serde_json::json!({"host": "demo", "workspace": "工作區 100%"}),
        ] {
            input.body = Some(body);
            assert!(validate_request(&input).is_ok());
        }
        for body in [
            serde_json::json!({}),
            serde_json::json!({"host": "https://other"}),
            serde_json::json!({"host": "demo", "workspace": 1}),
            serde_json::json!({"host": "demo", "workspace": "\n"}),
            serde_json::json!({"host": "demo", "workspace": ""}),
            serde_json::json!({"host": "demo", "force": true}),
        ] {
            input.body = Some(body);
            assert!(validate_request(&input).is_err());
        }
        input.body = Some(serde_json::json!({"host": "demo"}));
        for path in [
            "/approval-previews?",
            "/approval-previews?host=other",
            "/approval-previews/other",
        ] {
            input.path = path.into();
            assert!(validate_request(&input).is_err());
        }
        input.path = "/approval-previews".into();
        input.idempotency_key = Some("operation-key".into());
        assert!(validate_request(&input).is_err());
        assert!(validate_request(&request("GET", "/approval-previews")).is_err());
    }
    fn config(endpoint: &str) -> Config {
        Config {
            endpoint: endpoint.into(),
            expected_actor: "fixture-operator".into(),
            contract_version: "2026-10-08".into(),
        }
    }
    fn server(responses: Vec<String>) -> (String, mpsc::Receiver<String>, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let (send, receive) = mpsc::channel();
        let handle = thread::spawn(move || {
            for response in responses {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut raw = Vec::new();
                let mut buf = [0; 4096];
                loop {
                    let n = socket.read(&mut buf).unwrap();
                    if n == 0 {
                        break;
                    }
                    raw.extend_from_slice(&buf[..n]);
                    let text = String::from_utf8_lossy(&raw);
                    if let Some((headers, body)) = text.split_once("\r\n\r\n") {
                        let length = headers
                            .lines()
                            .find_map(|l| {
                                l.to_lowercase()
                                    .strip_prefix("content-length: ")
                                    .and_then(|s| s.parse::<usize>().ok())
                            })
                            .unwrap_or(0);
                        if body.len() >= length {
                            break;
                        }
                    }
                }
                let _ = send.send(String::from_utf8(raw).unwrap());
                socket.write_all(response.as_bytes()).unwrap();
            }
        });
        (endpoint, receive, handle)
    }
    fn json_response(body: &str) -> String {
        format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body)
    }
    const CAPS: &str = r#"{"actor":"fixture-operator","api_version":1,"contract_version":"2026-10-08","scopes":["observe","operate"]}"#;

    fn bootstrap_response(server_id: &str, principal_id: &str) -> String {
        json_response(
            &serde_json::json!({"capabilities": serde_json::from_str::<Value>(CAPS).unwrap(),
            "sync": {"version": 1, "server_id":server_id, "principal_id":principal_id}})
            .to_string(),
        )
    }

    #[tokio::test]
    async fn native_file_content_routes_refuse_queries_paths_and_redirects() {
        let bridge = Bridge::file_fixture("http://127.0.0.1:9/", "secret");
        let scope = bridge.file_scope().unwrap();
        for path in [
            "https://example.invalid/content",
            "/artifacts/uploads/op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/content?token=x",
            "/artifacts/art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/revisions/1/../content",
            "/artifacts/art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/revisions/0/content",
        ] {
            assert!(bridge.file_content(&scope, path, None).await.is_err());
        }
        let target = TcpListener::bind("127.0.0.1:0").unwrap();
        target.set_nonblocking(true).unwrap();
        let redirect=format!("HTTP/1.1 302 Found\r\nLocation: http://{}/never\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",target.local_addr().unwrap());
        let (endpoint, received, server) = server(vec![
            json_response(CAPS),
            bootstrap_response("fixture-server", "fixture-principal"),
            redirect,
        ]);
        let bridge = Bridge::file_fixture(&endpoint, "file-secret");
        let scope = bridge.file_scope().unwrap();
        assert!(bridge
            .file_content(
                &scope,
                "/artifacts/art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/revisions/1/content",
                None
            )
            .await
            .err()
            .unwrap()
            .contains("redirect"));
        assert!(target.accept().is_err());
        for _ in 0..3 {
            assert!(received
                .recv()
                .unwrap()
                .to_lowercase()
                .contains("authorization: bearer file-secret"));
        }
        server.join().unwrap();
    }
    #[test]
    fn artifact_upload_boundaries_and_read_routes_are_fixed() {
        let operation = format!("op_{}", "a".repeat(32));
        assert!(validate_artifact_upload(&operation, 0).is_ok());
        assert!(validate_artifact_upload(&operation, MAX_ARTIFACT_BYTES).is_ok());
        assert!(validate_artifact_upload(&operation, MAX_ARTIFACT_BYTES + 1).is_err());
        for id in [
            "https://other.example/op",
            "../op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "op_bad",
            "op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?token=x",
        ] {
            assert!(validate_artifact_upload(id, 1).is_err());
        }
        for path in [
            "/artifacts?limit=200",
            "/artifacts/art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/revisions/1",
        ] {
            assert!(validate_request(&request("GET", path)).is_ok());
        }
        for path in [
            "/artifacts/art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/revisions/0",
            "/artifacts/art_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/revisions/1/content",
            "/artifacts/uploads/op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/content",
        ] {
            assert!(validate_request(&request("GET", path)).is_err());
            assert!(validate_request(&request("POST", path)).is_err());
        }
    }

    #[tokio::test]
    async fn artifact_binary_reaches_only_its_operation_bound_central_route() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let (send, receive) = mpsc::channel();
        let worker = thread::spawn(move || {
            for reply in [
                json_response(CAPS),
                bootstrap_response("fixture", "principal"),
                json_response(CAPS),
                bootstrap_response("fixture", "principal"),
                json_response(r#"{"operation":{"status":"running"}}"#),
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut raw = Vec::new();
                let mut buf = [0; 4096];
                loop {
                    let count = socket.read(&mut buf).unwrap();
                    raw.extend_from_slice(&buf[..count]);
                    if let Some(position) = raw.windows(4).position(|part| part == b"\r\n\r\n") {
                        let headers = String::from_utf8_lossy(&raw[..position]);
                        let length = headers
                            .lines()
                            .find_map(|line| {
                                line.to_ascii_lowercase()
                                    .strip_prefix("content-length: ")
                                    .and_then(|n| n.parse::<usize>().ok())
                            })
                            .unwrap_or(0);
                        if raw.len() >= position + 4 + length {
                            break;
                        }
                    }
                    if count == 0 {
                        break;
                    }
                }
                send.send(raw).unwrap();
                socket.write_all(reply.as_bytes()).unwrap();
            }
        });
        let bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("native-fixture-token".into()),
        );
        bridge.connect().await.unwrap();
        let bytes = [0, 255, 128, 13, 10, 60, 38, 34, 195, 169];
        bridge
            .upload_artifact("op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", &bytes)
            .await
            .unwrap();
        for _ in 0..4 {
            receive.recv().unwrap();
        }
        let raw = receive.recv().unwrap();
        let split = raw.windows(4).position(|part| part == b"\r\n\r\n").unwrap();
        let headers = String::from_utf8_lossy(&raw[..split]);
        assert!(headers.starts_with(
            "POST /api/v1/artifacts/uploads/op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/content HTTP/1.1"
        ));
        assert!(headers
            .to_ascii_lowercase()
            .contains("authorization: bearer native-fixture-token"));
        assert!(headers
            .to_ascii_lowercase()
            .contains("content-type: application/octet-stream"));
        assert_eq!(&raw[split + 4..], &bytes);
        worker.join().unwrap();
    }

    #[tokio::test]
    async fn binary_upload_redirect_does_not_forward_credentials() {
        let target = TcpListener::bind("127.0.0.1:0").unwrap();
        target.set_nonblocking(true).unwrap();
        let redirect = format!("HTTP/1.1 307 Temporary Redirect\r\nLocation: http://{}/capture\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", target.local_addr().unwrap());
        let (endpoint, requests, worker) = server(vec![
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
            redirect,
        ]);
        let bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("native-fixture-token".into()),
        );
        assert!(bridge
            .upload_artifact("op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", b"fixture")
            .await
            .is_err());
        bridge.connect().await.unwrap();
        assert!(bridge
            .upload_artifact("op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", b"fixture")
            .await
            .err()
            .unwrap()
            .contains("redirect"));
        assert!(target.accept().is_err());
        assert_eq!(requests.try_iter().count(), 5);
        worker.join().unwrap();
    }

    #[test]
    fn capture_preview_has_one_fixed_route_and_typed_relative_source() {
        let mut input = request("POST", "/artifact-capture-previews");
        for path in [
            "notes/input.txt",
            "資料/輸入 1.txt",
            "odd/%2e%2e.txt",
            "a:b/file.txt",
        ] {
            input.body = Some(
                serde_json::json!({"host":"demo", "session_id":"session-full-id", "relative_path":path}),
            );
            assert!(validate_request(&input).is_ok(), "{path}");
        }
        for path in [
            "",
            "/etc/passwd",
            "../a",
            "a/../b",
            "a//b",
            "a/",
            ".git/config",
            "a/.GIT/x",
            "a\\b",
            "a\u{0085}b",
        ] {
            input.body = Some(
                serde_json::json!({"host":"demo", "session_id":"session-full-id", "relative_path":path}),
            );
            assert!(validate_request(&input).is_err(), "{path}");
        }
        for body in [
            serde_json::json!({"host":"demo", "session_id":"short", "relative_path":"a"}),
            serde_json::json!({"host":"https://example.invalid", "session_id":"session-1", "relative_path":"a"}),
            serde_json::json!({"host":"demo", "session_id":"session-1", "relative_path":"a", "headers":{"Authorization":"fake"}}),
            serde_json::json!({"host":"demo", "session_id":"session-1", "relative_path":"資料".repeat(1366)}),
            serde_json::json!({"host":"demo", "session_id":"session-1", "relative_path":true}),
        ] {
            input.body = Some(body);
            assert!(validate_request(&input).is_err());
        }
        input.body = Some(
            serde_json::json!({"host":"demo", "session_id":"session-1", "relative_path":"notes.txt"}),
        );
        for path in [
            "/artifact-capture-previews?",
            "/artifact-capture-previews?host=other",
            "/artifact-capture-previews/other",
            "/%61rtifact-capture-previews",
        ] {
            input.path = path.into();
            assert!(validate_request(&input).is_err());
        }
        input.path = "/artifact-capture-previews".into();
        input.idempotency_key = Some("wrong-key".into());
        assert!(validate_request(&input).is_err());
        assert!(validate_request(&request("GET", "/artifact-capture-previews")).is_err());
    }

    #[test]
    fn delivery_source_selector_is_scoped_and_complete() {
        for kind in ["execution", "task_command"] {
            assert!(validate_request(&request(
                "GET",
                &format!(
                    "/integrations/candidates?host=demo&source_kind={kind}&source_id=op_{}",
                    "a".repeat(32)
                )
            ))
            .is_ok());
        }
        for path in [
            "/sessions?source_kind=execution&source_id=x",
            "/integrations/candidates?source_kind=branch&source_id=x",
            "/integrations/candidates?source_kind=execution",
            "/integrations/candidates?source_id=x",
            "/integrations/candidates?source_kind=execution&source_id=",
            "/integrations/candidates?source_kind=execution&source_id=x&source_id=y",
            "/integrations/candidates?source_kind=execution&source_kind=task_command&source_id=x",
            "/integrations/candidates?source_kind=execution&source_id=x%0Ay",
        ] {
            assert!(validate_request(&request("GET", path)).is_err(), "{path}");
        }
        assert!(validate_request(&request(
            "GET",
            &format!(
                "/integrations/candidates?source_kind=execution&source_id={}",
                "x".repeat(257)
            )
        ))
        .is_err());
    }

    #[test]
    fn cleanup_routes_and_typed_previews_are_bounded() {
        let rid = format!("wt_{}", "a".repeat(32));
        for path in [
            format!("/cleanup-tombstones/{rid}"),
            "/cleanup-tombstones?query=old%20place&cursor=&limit=100".into(),
            "/cleanup-retained?host=demo&resource_id=cr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&cursor="
                .into(),
        ] {
            assert!(validate_request(&request("GET", &path)).is_ok(), "{path}");
        }
        for path in [
            "/cleanup-tombstones/../../../token",
            "/cleanup-tombstones/cr_bad",
            "/cleanup-retained?query=x&query=y",
            "/cleanup-retained?limit=501",
            "/cleanup-retained?limit=true",
            "/cleanup-retained?url=https%3A%2F%2Fevil.example",
            "/cleanup-retained?original_id=x",
            "/cleanup-tombstones?headers=secret",
        ] {
            assert!(validate_request(&request("GET", path)).is_err(), "{path}");
        }
        let mut preview = request("POST", "/cleanup-previews");
        preview.body = Some(serde_json::json!({"target":{"kind":"host","host":"demo"},
            "choices":{"discard_uncommitted":[],"release_undelivered":[rid]}}));
        assert!(validate_request(&preview).is_ok());
        for body in [
            serde_json::json!({"target":{"kind":"host","host":"../other"}}),
            serde_json::json!({"target":{"kind":"host","host":"demo","include_children":true}}),
            serde_json::json!({"target":{"kind":"work_item","work_item_id":"wi_aaaaaaaaaaaaaaaaaaaa","include_children":"true"}}),
            serde_json::json!({"target":{"kind":"host","host":"demo"},"headers":{"Authorization":"fake"}}),
            serde_json::json!({"target":{"kind":"host","host":"demo"},"choices":{"discard_uncommitted":["/tmp/path"]}}),
            serde_json::json!({"target":{"kind":"host","host":"demo"},"choices":{"release_undelivered":vec![rid;501]}}),
            serde_json::json!({"target":{"kind":"task","task_id":"../file"}}),
            serde_json::json!({"target":{"kind":"task","task_id":"11111111-2222-4333-8444-555555555555","force":true}}),
            serde_json::json!({"target":{"kind":"task","task_id":"11111111-2222-4333-8444-555555555555","include_children":true}}),
            serde_json::json!({"target":{"kind":"task","task_id":"11111111-2222-4333-8444-555555555555"},"origin":"task_lifecycle"}),
        ] {
            preview.body = Some(body);
            assert!(validate_request(&preview).is_err());
        }
        for target in [
            serde_json::json!({"kind":"work_item","work_item_id":"wi_aaaaaaaaaaaaaaaaaaaa","include_children":true}),
            serde_json::json!({"kind":"checkpoint","checkpoint_id":"cp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}),
            serde_json::json!({"kind":"integration","operation_id":"op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}),
            serde_json::json!({"kind":"task","task_id":"11111111-2222-4333-8444-555555555555"}),
        ] {
            preview.body = Some(serde_json::json!({"target":target}));
            assert!(validate_request(&preview).is_ok());
        }
        preview.path = "/cleanup-previews?wait=3".into();
        assert!(validate_request(&preview).is_err());
        assert!(validate_request(&request("POST", "/cleanup-retained")).is_err());
    }

    #[test]
    fn delivery_reads_are_scoped_to_known_routes() {
        for path in [
            "/deployments/preview?recipe=production",
            "/deployments?recipe=production&limit=5&cursor=page-proof",
            "/deployments/dep_0123456789abcdef0123456789abcdef",
            "/deployment-environments?recipe=production",
            "/deployment-environments/history?recipe=production&limit=5&cursor=page-proof",
        ] {
            assert!(validate_request(&request("GET", path)).is_ok(), "{path}");
            assert!(validate_request(&request("POST", path)).is_err(), "{path}");
        }
        for path in [
            "/deployments/dep_untrusted",
            "/deployments/preview?recipe=prod&url=https://other.example",
            "/deployments/%2e%2e/hosts",
            "/deployments/preview?recipe=prod%0aAuthorization%3Abad",
            "/deployment-environments/history/all",
        ] {
            assert!(validate_request(&request("GET", path)).is_err(), "{path}");
        }
    }

    #[test]
    fn endpoints_require_a_trusted_origin() {
        for endpoint in [
            "https://central.example/",
            "http://127.0.0.1:18796/",
            "http://[::1]:18796/",
        ] {
            assert!(config(endpoint).validate().is_ok(), "{endpoint}");
        }
        for endpoint in [
            "http://central.example/",
            "http://localhost/",
            "file:///tmp/central",
            "https://user:secret@central.example/",
            "https://central.example/path",
            "https://central.example/?token=x",
            "https://central.example/#fragment",
        ] {
            assert!(config(endpoint).validate().is_err(), "{endpoint}");
        }
    }

    #[test]
    fn requests_cannot_escape_the_central_contract() {
        for path in [
            "https://other.example/hosts",
            "//other.example/hosts",
            "/../hosts",
            "/sessions/../hosts",
            "/sessions/%2e%2e/hosts",
            "/hosts#x",
            "/hosts?url=https://other.example",
            "/hosts?token=x",
            "/hosts\\anything",
            "/events/stream",
            "/dashboard/",
            "/shell",
            "/hosts\n",
            "/events?after=%zz",
            "/events?checkpoint=%",
            "/events?checkpoint=%0d%0aAuthorization",
        ] {
            assert!(validate_request(&request("GET", path)).is_err(), "{path}");
        }
        assert!(validate_request(&request("DELETE", "/operations")).is_err());
        assert!(validate_request(&request("POST", "/hosts")).is_err());
        assert!(validate_request(&request("POST", "/operations")).is_err());
        let mut input = request("POST", "/operations?wait=3");
        input.idempotency_key = Some("fixture-key".into());
        input.body = Some(
            serde_json::json!({"action":"session.send", "target":{}, "params":{}, "preconditions":{}}),
        );
        assert!(validate_request(&input).is_ok());
        input.idempotency_key = Some("key\r\nAuthorization: unsafe".into());
        assert!(validate_request(&input).is_err());
        assert!(serde_json::from_value::<ConnectorRequest>(
            serde_json::json!({"method":"GET","path":"/hosts",
            "headers":{"Authorization":"unsafe"}})
        )
        .is_err());
    }

    #[test]
    fn external_navigation_is_https_github_without_credentials() {
        assert!(external_url("https://github.com/example/repository/pull/1").is_ok());
        for value in [
            "file:///tmp/secret",
            "javascript:alert(1)",
            "https://other.example/",
            "https://github.com.evil.example/",
            "https://user:secret@github.com/",
            "https://github.com/?token=secret",
            "http://github.com/",
            "https://github.com:8443/",
        ] {
            assert!(external_url(value).is_err(), "{value}");
        }
    }

    #[test]
    fn observation_routes_allow_stable_paging_and_documented_filters() {
        for path in [
            "/sessions?order=id&include_gone=true&provenance=manual",
            "/sessions/h1/fixture-session/history?order=desc&kind=session.updated&since=1&until=2",
            "/hosts/h1/discovery?after=0&limit=50",
            "/worktrees/wt_00000000000000000000000000000000/relations?include_closed=true&execution_id=fixture-execution",
            "/events?after=1&checkpoint=s1.fixture.signature",
        ] {
            assert!(validate_request(&request("GET", path)).is_ok(), "{path}");
        }
    }

    #[tokio::test]
    async fn contract_mismatch_cannot_unlock_operations() {
        let (endpoint, received, handle) = server(vec![json_response(
            &CAPS.replace("2026-10-08", "unsupported"),
        )]);
        let bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("fixture-token".into()),
        );
        assert!(bridge.connect().await.is_err());
        assert!(bridge.request(request("GET", "/hosts")).await.is_err());
        assert!(received
            .recv()
            .unwrap()
            .starts_with("GET /api/v1/capabilities "));
        handle.join().unwrap();
    }

    #[tokio::test]
    async fn wrong_identity_cannot_unlock_operations() {
        let (endpoint, received, handle) = server(vec![json_response(
            &CAPS.replace("fixture-operator", "other-actor"),
        )]);
        let bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("fixture-token".into()),
        );
        assert!(bridge.request(request("GET", "/hosts")).await.is_err());
        assert!(bridge.connect().await.is_err());
        assert!(bridge.request(request("GET", "/hosts")).await.is_err());
        assert!(received
            .recv()
            .unwrap()
            .starts_with("GET /api/v1/capabilities "));
        handle.join().unwrap();
    }

    #[tokio::test]
    async fn native_credentials_and_original_operation_envelope_reach_only_the_fixed_central() {
        let (endpoint, received, handle) = server(vec![
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
            json_response(r#"{"operation":{"operation_id":"op_fixture","status":"accepted"}}"#),
        ]);
        let bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("fixture-token".into()),
        );
        assert_eq!(bridge.connect().await.unwrap()["actor"], "fixture-operator");
        let body = serde_json::json!({"action":"session.send", "target":{"host":"fixture-host","session_id":"fixture-session"},
            "params":{"text":"fixture message"},"preconditions":{"version":7}});
        let mut input = request("POST", "/operations?wait=3");
        input.idempotency_key = Some("original-key".into());
        input.body = Some(body.clone());
        assert_eq!(
            bridge.request(input).await.unwrap().data["operation"]["status"],
            "accepted"
        );
        let first = received.recv().unwrap();
        for _ in 0..3 {
            received.recv().unwrap();
        }
        let second = received.recv().unwrap();
        assert!(first
            .to_lowercase()
            .contains("authorization: bearer fixture-token"));
        assert!(second
            .to_lowercase()
            .contains("idempotency-key: original-key"));
        assert_eq!(
            serde_json::from_str::<Value>(second.split_once("\r\n\r\n").unwrap().1).unwrap(),
            body
        );
        assert!(!serde_json::to_string(&bridge.status())
            .unwrap()
            .contains("fixture-token"));
        bridge.disconnect();
        assert!(bridge.request(request("GET", "/hosts")).await.is_err());
        handle.join().unwrap();
    }

    #[tokio::test]
    async fn redirects_never_receive_a_second_token_bearing_request() {
        let target = TcpListener::bind("127.0.0.1:0").unwrap();
        target.set_nonblocking(true).unwrap();
        let response = format!("HTTP/1.1 302 Found\r\nLocation: http://{}/steal\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", target.local_addr().unwrap());
        let (endpoint, _, handle) = server(vec![response]);
        let bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("fixture-token".into()),
        );
        assert!(bridge.connect().await.unwrap_err().contains("redirects"));
        assert!(target.accept().is_err());
        handle.join().unwrap();
    }
    #[derive(Default)]
    struct MockVault {
        records: Mutex<std::collections::HashMap<String, Vec<u8>>>,
        answer: Mutex<Option<String>>,
        fail_save: std::sync::atomic::AtomicBool,
        fail_remove: std::sync::atomic::AtomicBool,
        prompt_release: Mutex<Option<mpsc::Receiver<()>>>,
        prompt_started: Mutex<Option<mpsc::Sender<()>>>,
    }
    impl Vault for MockVault {
        fn source(&self) -> &'static str {
            "windows_credential_manager"
        }
        fn supported(&self) -> bool {
            true
        }
        fn read(&self, key: &str) -> Result<Option<Zeroizing<Vec<u8>>>, String> {
            Ok(self
                .records
                .lock()
                .unwrap()
                .get(key)
                .cloned()
                .map(Zeroizing::new))
        }
        fn write(&self, key: &str, bytes: &[u8]) -> Result<(), String> {
            if self.fail_save.load(std::sync::atomic::Ordering::Relaxed) {
                return Err("Fixture save refused".into());
            }
            self.records
                .lock()
                .unwrap()
                .insert(key.into(), bytes.to_vec());
            Ok(())
        }
        fn remove(&self, key: &str) -> Result<(), String> {
            if self.fail_remove.load(std::sync::atomic::Ordering::Relaxed) {
                return Err("Fixture remove refused".into());
            }
            self.records.lock().unwrap().remove(key);
            Ok(())
        }
        fn prompt(
            &self,
            actor: &str,
            endpoint: &str,
            _: Locale,
            _: isize,
        ) -> Result<Option<Zeroizing<String>>, String> {
            assert_eq!(actor, "fixture-operator");
            assert!(endpoint.starts_with("http://127.0.0.1:"));
            if let Some(send) = self.prompt_started.lock().unwrap().take() {
                send.send(()).unwrap();
            }
            if let Some(receive) = self.prompt_release.lock().unwrap().take() {
                receive.recv().unwrap();
            }
            Ok(self.answer.lock().unwrap().clone().map(Zeroizing::new))
        }
    }
    fn credential_bridge(endpoint: &str, vault: Arc<MockVault>) -> Bridge {
        let mut bridge = Bridge::new(Ok(config(endpoint)), Zeroizing::new(String::new()));
        bridge.vault = vault;
        bridge
    }
    fn saved(vault: &MockVault, endpoint: &str, token: &str) -> Vec<u8> {
        let key = binding(&config(endpoint));
        let record = Record {
            version: 1,
            binding: key.clone(),
            token: Zeroizing::new(token.into()),
            identity: Identity {
                server_id: "fixture".into(),
                principal_id: "principal".into(),
            },
        };
        let bytes = record.encode().unwrap().to_vec();
        vault.write(&key, &bytes).unwrap();
        bytes
    }

    #[tokio::test]
    async fn enrollment_is_verified_before_persistence_and_survives_native_restart() {
        let (endpoint, requests, server) = server(vec![
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
        ]);
        let vault = Arc::new(MockVault::default());
        *vault.answer.lock().unwrap() = Some("new-synthetic-token".into());
        let bridge = credential_bridge(&endpoint, vault.clone());
        assert!(!bridge.status().credential_available);
        let caps = bridge.enroll(Locale::English, 0).await.unwrap().unwrap();
        assert_eq!(caps["desktop_identity"]["principal_id"], "principal");
        assert!(bridge.status().connected);
        assert_eq!(
            bridge.status().credential_source,
            Some("windows_credential_manager")
        );
        let status = serde_json::to_string(&bridge.status()).unwrap();
        assert!(!status.contains("new-synthetic-token"));
        assert!(!caps.to_string().contains("new-synthetic-token"));
        drop(bridge);
        let restarted = credential_bridge(&endpoint, vault);
        assert!(!restarted.status().connected);
        restarted.connect().await.unwrap();
        assert!(restarted.status().connected);
        for raw in requests.try_iter() {
            assert!(raw
                .to_ascii_lowercase()
                .contains("authorization: bearer new-synthetic-token"));
        }
        server.join().unwrap();
    }

    #[tokio::test]
    async fn cancelled_invalid_or_unsavable_replacement_keeps_old_record_and_connection() {
        let (endpoint, _, server) = server(vec![
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
            json_response(&CAPS.replace("fixture-operator", "wrong-actor")),
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
        ]);
        let vault = Arc::new(MockVault::default());
        let original = saved(&vault, &endpoint, "old-synthetic-token");
        let bridge = credential_bridge(&endpoint, vault.clone());
        bridge.connect().await.unwrap();
        assert!(bridge
            .enroll(Locale::TraditionalChinese, 0)
            .await
            .unwrap()
            .is_none());
        assert!(bridge.status().connected);
        *vault.answer.lock().unwrap() = Some("wrong-synthetic-token".into());
        assert!(bridge.enroll(Locale::English, 0).await.is_err());
        assert!(bridge.status().connected);
        assert_eq!(
            vault
                .read(&binding(&config(&endpoint)))
                .unwrap()
                .unwrap()
                .as_slice(),
            &original
        );
        vault
            .fail_save
            .store(true, std::sync::atomic::Ordering::Relaxed);
        *vault.answer.lock().unwrap() = Some("valid-unsaved-token".into());
        assert!(bridge
            .enroll(Locale::English, 0)
            .await
            .unwrap_err()
            .contains("save refused"));
        assert!(bridge.status().connected);
        assert_eq!(
            bridge.active().unwrap().1.record.token.as_str(),
            "old-synthetic-token"
        );
        assert_eq!(
            vault
                .read(&binding(&config(&endpoint)))
                .unwrap()
                .unwrap()
                .as_slice(),
            &original
        );
        server.join().unwrap();
    }

    #[tokio::test]
    async fn saved_identity_change_refuses_reconnect_and_mutation_without_fallback() {
        for on_reconnect in [true, false] {
            let mut replies = vec![];
            if !on_reconnect {
                replies.extend([
                    json_response(CAPS),
                    bootstrap_response("fixture", "principal"),
                ]);
            }
            replies.extend([
                json_response(CAPS),
                bootstrap_response("replacement", "principal"),
            ]);
            let (endpoint, requests, server) = server(replies);
            let vault = Arc::new(MockVault::default());
            saved(&vault, &endpoint, "bound-token");
            let bridge = credential_bridge(&endpoint, vault);
            if on_reconnect {
                assert!(bridge
                    .connect()
                    .await
                    .unwrap_err()
                    .contains("identity changed"));
            } else {
                bridge.connect().await.unwrap();
                let mut mutation = request("POST", "/operations");
                mutation.body = Some(
                    serde_json::json!({"action":"session.send", "target":{}, "params":{}, "preconditions":{}}),
                );
                mutation.idempotency_key = Some("original-intent".into());
                assert!(bridge
                    .request(mutation)
                    .await
                    .err()
                    .unwrap()
                    .contains("identity changed"));
            }
            assert!(!bridge.status().connected);
            assert!(requests.try_iter().all(|raw| raw.starts_with("GET ")));
            server.join().unwrap();
        }
    }

    #[tokio::test]
    async fn bootstrap_principal_mismatch_and_contract_missing_cannot_enroll() {
        for response in [
            bootstrap_response("fixture", "principal").replace("fixture-operator", "wrong-actor"),
            json_response(r#"{"capabilities":{},"sync":{"version":1}}"#),
        ] {
            let (endpoint, _, server) = server(vec![json_response(CAPS), response]);
            let vault = Arc::new(MockVault::default());
            *vault.answer.lock().unwrap() = Some("synthetic-token".into());
            let bridge = credential_bridge(&endpoint, vault.clone());
            assert!(bridge.enroll(Locale::English, 0).await.is_err());
            assert!(vault.records.lock().unwrap().is_empty());
            assert!(!bridge.status().connected);
            server.join().unwrap();
        }
    }

    #[tokio::test]
    async fn disconnect_during_native_prompt_prevents_network_save_and_activation() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let vault = Arc::new(MockVault::default());
        *vault.answer.lock().unwrap() = Some("synthetic-token".into());
        let (release, held) = mpsc::channel();
        let (started, receive) = mpsc::channel();
        *vault.prompt_release.lock().unwrap() = Some(held);
        *vault.prompt_started.lock().unwrap() = Some(started);
        let bridge = Arc::new(credential_bridge(&endpoint, vault.clone()));
        let enrolling = bridge.clone();
        let task = tokio::spawn(async move { enrolling.enroll(Locale::English, 0).await });
        tokio::task::spawn_blocking(move || receive.recv_timeout(Duration::from_secs(5)).unwrap())
            .await
            .unwrap();
        assert!(bridge
            .connect()
            .await
            .unwrap_err()
            .contains("already in progress"));
        bridge.disconnect();
        release.send(()).unwrap();
        assert!(task.await.unwrap().is_err());
        assert!(vault.records.lock().unwrap().is_empty());
        assert!(!bridge.status().connected);
        assert!(listener.accept().is_err());
    }

    #[tokio::test]
    async fn forget_failure_preserves_connection_and_success_cannot_fall_back_to_environment() {
        let (endpoint, _, server) = server(vec![
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
        ]);
        let vault = Arc::new(MockVault::default());
        saved(&vault, &endpoint, "stored-token");
        let mut bridge = Bridge::new(Ok(config(&endpoint)), Zeroizing::new("launch-token".into()));
        bridge.vault = vault.clone();
        bridge.connect().await.unwrap();
        vault
            .fail_remove
            .store(true, std::sync::atomic::Ordering::Relaxed);
        assert!(bridge.forget_credential().is_err());
        assert!(bridge.status().connected);
        vault
            .fail_remove
            .store(false, std::sync::atomic::Ordering::Relaxed);
        bridge.forget_credential().unwrap();
        assert!(!bridge.status().connected);
        assert!(!bridge.status().credential_available);
        assert!(bridge.connect().await.is_err());
        server.join().unwrap();
    }

    #[test]
    fn trusted_configuration_reload_cannot_forward_a_launch_token_to_another_endpoint() {
        let root = std::env::temp_dir().join(format!(
            "batc-credential-config-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        let path = root.join("central.json");
        let write = |endpoint: &str| {
            std::fs::write(&path, format!(r#"{{"endpoint":"{endpoint}","expected_actor":"fixture-operator","contract_version":"2026-10-08"}}"#)).unwrap()
        };
        write("https://first.example/");
        let mut bridge = Bridge::load(&root, Zeroizing::new("synthetic-launch-token".into()));
        bridge.vault = Arc::new(MockVault::default());
        assert!(bridge.status().credential_available);
        bridge.reload_configuration().unwrap();
        assert!(bridge.status().credential_available);
        write("https://second.example/");
        let status = bridge.reload_configuration().unwrap();
        assert_eq!(status.endpoint.as_deref(), Some("https://second.example/"));
        assert!(!status.credential_available);
        assert!(!status.connected);
        std::fs::write(&path, vec![b'x'; 16_385]).unwrap();
        assert!(bridge
            .reload_configuration()
            .unwrap()
            .error
            .unwrap()
            .contains("bound"));
        std::fs::remove_dir_all(root).unwrap();
    }
    #[tokio::test]
    async fn explicit_existing_central_switch_preserves_managed_binding_until_native_review() {
        let root = tempfile::tempdir().unwrap();
        let identity = Identity {
            server_id: "managed-fixture-server".into(),
            principal_id: "managed-fixture-principal".into(),
        };
        let mut bridge = Bridge::managed(
            root.path(),
            config("http://127.0.0.1:23456"),
            Zeroizing::new("synthetic-managed-fixture-token".into()),
            identity.clone(),
        );
        bridge.vault = Arc::new(MockVault::default());
        {
            let mut state = bridge.state.lock().unwrap();
            let config = state.config.as_ref().unwrap().clone();
            state.active = Some(Active {
                config: config.clone(),
                record: Record {
                    version: 1,
                    binding: binding(&config),
                    token: Zeroizing::new("synthetic-managed-fixture-token".into()),
                    identity: identity.clone(),
                },
            });
        }
        let original = bridge.status();
        assert!(original.configuration_setup && !original.configuration_reload);
        assert_eq!(original.configuration_source, "managed");
        assert!(bridge.reload_configuration().is_err());
        assert!(bridge
            .setup_configuration(config("https://central.example"), |_| false)
            .await
            .unwrap()
            .is_none());
        assert_eq!(bridge.status().endpoint, original.endpoint);
        assert!(bridge.status().credential_available && bridge.status().connected);
        assert!(bridge.state.lock().unwrap().environment_identity.as_ref() == Some(&identity));
        assert!(!root.path().join("central.json").exists());
        let status = bridge
            .setup_configuration(config("https://central.example"), |_| true)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(status.endpoint.as_deref(), Some("https://central.example/"));
        assert_eq!(status.configuration_source, "external");
        assert!(!status.credential_available && !status.connected && status.configuration_reload);
        assert!(bridge.state.lock().unwrap().environment_identity.is_none());
        assert!(bridge.state.lock().unwrap().environment.is_none());
        let saved = std::fs::read(root.path().join("central.json")).unwrap();
        assert!(!String::from_utf8_lossy(&saved).contains("synthetic-managed"));
        assert!(bridge
            .setup_configuration(config("https://other.example"), |_| panic!(
                "must not overwrite"
            ))
            .await
            .is_err());
    }

    #[tokio::test]
    async fn existing_central_switch_conflict_keeps_managed_credential_and_original_file() {
        let root = tempfile::tempdir().unwrap();
        let mut bridge = Bridge::managed(
            root.path(),
            config("http://127.0.0.1:23456"),
            Zeroizing::new("synthetic-managed-fixture-token".into()),
            Identity {
                server_id: "managed-fixture-server".into(),
                principal_id: "managed-fixture-principal".into(),
            },
        );
        bridge.vault = Arc::new(MockVault::default());
        let path = root.path().join("central.json");
        let competing = path.clone();
        assert!(bridge
            .setup_configuration(config("https://central.example"), move |_| {
                std::fs::write(competing, b"operator configuration appeared during review")
                    .unwrap();
                true
            })
            .await
            .is_err());
        assert_eq!(bridge.status().configuration_source, "managed");
        assert!(bridge.status().credential_available);
        assert_eq!(
            std::fs::read(path).unwrap(),
            b"operator configuration appeared during review"
        );
    }

    #[tokio::test]
    async fn failed_managed_initialization_requires_explicit_existing_central_selection() {
        let root = tempfile::tempdir().unwrap();
        let mut bridge = Bridge::managed_failure(root.path(), "MANAGED_RUNTIME_UNAVAILABLE".into());
        bridge.vault = Arc::new(MockVault::default());
        assert_eq!(bridge.status().configuration_source, "managed");
        assert!(bridge.status().configuration_setup && !bridge.status().credential_available);
        assert!(bridge.reload_configuration().is_err());
        let status = bridge
            .setup_configuration(config("https://central.example"), |_| true)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(status.configuration_source, "external");
        assert!(!status.credential_available);
    }

    #[tokio::test]
    async fn first_configuration_needs_native_review_and_never_forwards_a_launch_token() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("central.json");
        let mut bridge = Bridge::load(root.path(), Zeroizing::new("discarded-launch-token".into()));
        bridge.vault = Arc::new(MockVault::default());
        assert!(bridge.status().configuration_setup);
        assert!(bridge
            .setup_configuration(config("https://central.example"), |_| false)
            .await
            .unwrap()
            .is_none());
        assert!(!path.exists());
        let status = bridge
            .setup_configuration(config("https://central.example"), |candidate| {
                assert_eq!(candidate.endpoint, "https://central.example/");
                assert_eq!(candidate.expected_actor, "fixture-operator");
                true
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(status.endpoint.as_deref(), Some("https://central.example/"));
        assert!(!status.configuration_setup && !status.credential_available && !status.connected);
        let bytes = std::fs::read(&path).unwrap();
        assert!(!String::from_utf8_lossy(&bytes).contains("token"));
        assert_eq!(
            read_configuration(&path).unwrap().expected_actor,
            "fixture-operator"
        );
        assert!(bridge
            .setup_configuration(config("https://other.example"), |_| panic!(
                "must not ask again"
            ))
            .await
            .is_err());
        assert_eq!(std::fs::read(&path).unwrap(), bytes);
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
    }
    #[tokio::test]
    async fn first_configuration_refuses_invalid_input_and_files_created_during_review() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("central.json");
        let mut bridge = Bridge::load(root.path(), Zeroizing::new(String::new()));
        bridge.vault = Arc::new(MockVault::default());
        for endpoint in [
            "http://remote.example",
            "https://central.example/path",
            "https://user:pass@central.example",
        ] {
            assert!(bridge
                .setup_configuration(config(endpoint), |_| panic!(
                    "invalid origin reached native confirmation"
                ))
                .await
                .is_err());
            assert!(!path.exists());
        }
        let competing = path.clone();
        assert!(bridge
            .setup_configuration(config("https://central.example"), move |_| {
                std::fs::write(competing, b"existing configuration").unwrap();
                true
            })
            .await
            .is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"existing configuration");
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
        assert!(!bridge.status().credential_available);
    }
    #[tokio::test]
    async fn disconnect_during_first_configuration_refuses_the_pending_save() {
        let root = tempfile::tempdir().unwrap();
        let mut bridge = Bridge::load(root.path(), Zeroizing::new(String::new()));
        bridge.vault = Arc::new(MockVault::default());
        let bridge = Arc::new(bridge);
        let second = bridge.clone();
        assert!(bridge
            .setup_configuration(config("https://central.example"), move |_| {
                second.disconnect();
                true
            })
            .await
            .err()
            .unwrap()
            .contains("changed"));
        assert!(!root.path().join("central.json").exists());
    }
    #[tokio::test]
    async fn launch_credential_keeps_first_backend_identity_across_disconnect() {
        let (endpoint, requests, server) = server(vec![
            json_response(CAPS),
            bootstrap_response("fixture", "principal"),
            json_response(CAPS),
            bootstrap_response("different-server", "principal"),
        ]);
        let mut bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("launch-fixture".into()),
        );
        bridge.vault = Arc::new(MockVault::default());
        bridge.connect().await.unwrap();
        bridge.disconnect();
        assert!(bridge
            .connect()
            .await
            .unwrap_err()
            .contains("identity changed"));
        assert!(!bridge.status().connected);
        assert!(requests.try_iter().all(|raw| raw.starts_with("GET ")));
        server.join().unwrap();
    }

    #[tokio::test]
    async fn disconnect_during_verification_prevents_bootstrap_and_activation() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let (started, received) = mpsc::channel();
        let (release, wait) = mpsc::channel();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = [0; 4096];
            let count = socket.read(&mut request).unwrap();
            assert!(count > 0);
            started.send(()).unwrap();
            wait.recv_timeout(Duration::from_secs(5)).unwrap();
            socket.write_all(json_response(CAPS).as_bytes()).unwrap();
            listener.set_nonblocking(true).unwrap();
            listener
        });
        let mut bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("launch-fixture".into()),
        );
        bridge.vault = Arc::new(MockVault::default());
        let bridge = Arc::new(bridge);
        let pending = bridge.clone();
        let connect = tokio::spawn(async move { pending.connect().await });
        tokio::task::spawn_blocking(move || received.recv_timeout(Duration::from_secs(5)).unwrap())
            .await
            .unwrap();
        bridge.disconnect();
        release.send(()).unwrap();
        assert!(connect.await.unwrap().is_err());
        assert!(!bridge.status().connected);
        assert!(server.join().unwrap().accept().is_err());
    }

    #[tokio::test]
    async fn protected_record_damage_is_refused_before_any_network_request() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let vault = Arc::new(MockVault::default());
        let wrong = saved(&vault, "https://other.example/", "synthetic-token");
        vault
            .records
            .lock()
            .unwrap()
            .insert(binding(&config(&endpoint)), wrong);
        let bridge = credential_bridge(&endpoint, vault.clone());
        assert!(bridge
            .status()
            .error
            .unwrap()
            .contains("another configuration"));
        assert!(bridge.connect().await.is_err());
        vault
            .records
            .lock()
            .unwrap()
            .insert(binding(&config(&endpoint)), b"broken".to_vec());
        assert!(bridge.connect().await.is_err());
        assert!(listener.accept().is_err());
        assert!(!bridge.status().connected);
    }
    #[test]
    fn managed_capture_preview_keeps_exact_selector_and_read_boundary() {
        let mut input = request("POST", "/artifact-managed-capture-previews");
        let operation = serde_json::json!({"host":"demo", "session_id":"managed-session",
                "relative_path":"result/report.txt", "execution_operation_id":"op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"});
        let command = serde_json::json!({"host":"demo", "session_id":"managed-session",
                "relative_path":"result/report.txt", "task_id":"11111111-2222-4333-8444-555555555555", "command_id":"cmd-original"});
        for value in [&operation, &command] {
            input.body = Some(value.clone());
            assert!(validate_request(&input).is_ok());
        }
        for (field, value) in [
            ("relative_path", serde_json::json!("../outside")),
            ("relative_path", serde_json::json!(".git/config")),
            ("execution_operation_id", serde_json::json!(null)),
            ("execution_operation_id", serde_json::json!("short")),
            ("task_id", serde_json::json!("11111111")),
            ("command_id", serde_json::json!(null)),
            ("lineage", serde_json::json!({"kind":"execution_operation"})),
            ("force", serde_json::json!(true)),
            ("headers", serde_json::json!({"Authorization":"fixture"})),
        ] {
            let mut bad = operation.clone();
            bad[field] = value;
            input.body = Some(bad);
            assert!(validate_request(&input).is_err());
        }
        input.body = Some(operation);
        for path in [
            "/artifact-managed-capture-previews?",
            "/artifact-managed-capture-previews?host=demo",
            "/artifact-managed-capture-previews/other",
        ] {
            input.path = path.into();
            assert!(validate_request(&input).is_err());
        }
        input.path = "/artifact-managed-capture-previews".into();
        input.idempotency_key = Some("not-an-operation".into());
        assert!(validate_request(&input).is_err());
        assert!(validate_request(&request("GET", "/artifact-managed-capture-previews")).is_err());
    }
}
