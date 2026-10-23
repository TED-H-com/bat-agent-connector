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

#[path = "connection_participant/mod.rs"]
mod connection_participant;

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

// Start preview creates no operation, write frame, or database row. The unique-candidate
// path may read BAT `workspace:load` and GitHub; those reads are not writes.
fn validate_start_preview(body: Option<&Value>) -> Result<(), String> {
    let doc = match body {
        None => return Err("Start preview body is required".into()),
        Some(value) => value
            .as_object()
            .ok_or("Invalid typed start preview request")?,
    };
    if doc.keys().any(|key| {
        !matches!(
            key.as_str(),
            "code_target"
                | "delivery"
                | "requirement_ids"
                | "instruction"
                | "profile_id"
                | "profile_revision"
        )
    }) {
        return Err("Invalid typed start preview request".into());
    }
    match (doc.get("profile_id"), doc.get("profile_revision")) {
        (None, None) => (),
        (Some(id), Some(revision)) => {
            if !id
                .as_str()
                .is_some_and(|id| Regex::new(r"^wpf_[0-9a-f]{32}$").unwrap().is_match(id))
                || !revision.as_i64().is_some_and(|n| n > 0)
                || doc.get("code_target").is_none()
            {
                return Err("Invalid typed start profile pin".into());
            }
        }
        _ => return Err("Supply both profile identity and revision".into()),
    }
    if let Some(delivery) = doc.get("delivery") {
        if !matches!(
            delivery.as_str(),
            Some("pull_request" | "reviewable_changes")
        ) {
            return Err("Invalid typed start preview request".into());
        }
    }
    if let Some(instruction) = doc.get("instruction") {
        let text = instruction
            .as_str()
            .filter(|text| !text.trim().is_empty() && text.chars().count() <= 4000)
            .ok_or("Invalid typed start preview request")?;
        let collapsed: String = text
            .chars()
            .map(|ch| if ch.is_control() { ' ' } else { ch })
            .collect();
        if collapsed.trim().is_empty() {
            return Err("Invalid typed start preview request".into());
        }
    }
    if let Some(ids) = doc.get("requirement_ids") {
        let list = ids
            .as_array()
            .ok_or("Invalid typed start preview request")?;
        if !(1..=50).contains(&list.len()) {
            return Err("Invalid typed start preview request".into());
        }
        let mut seen = std::collections::HashSet::new();
        let item = Regex::new(r"^[a-z][a-z0-9_.-]{0,79}$").unwrap();
        for id in list {
            let text = id.as_str().ok_or("Invalid typed start preview request")?;
            if !item.is_match(text) || !seen.insert(text.to_string()) {
                return Err("Invalid typed start preview request".into());
            }
        }
    }
    if let Some(target) = doc.get("code_target") {
        let fields = target
            .as_object()
            .ok_or("Invalid typed start preview request")?;
        if fields.keys().any(|key| {
            !matches!(
                key.as_str(),
                "repository" | "host" | "workspace_id" | "source_ref"
            )
        }) {
            return Err("Invalid typed start preview request".into());
        }
        let logical = |value: &str| {
            !value.trim().is_empty() && value.len() <= 256 && !value.chars().any(char::is_control)
        };
        if let Some(repository) = fields.get("repository") {
            let repository = repository
                .as_str()
                .ok_or("Invalid typed start preview request")?;
            if !logical(repository)
                || !Regex::new(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
                    .unwrap()
                    .is_match(repository)
            {
                return Err("Invalid repository, workspace or published branch identity".into());
            }
        }
        if let Some(host) = fields.get("host") {
            let host = host.as_str().ok_or("Invalid typed start preview request")?;
            if !logical(host)
                || !host
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"_.-".contains(&byte))
            {
                return Err("Invalid repository, workspace or published branch identity".into());
            }
        }
        if let Some(workspace) = fields.get("workspace_id") {
            let workspace = workspace
                .as_str()
                .ok_or("Invalid typed start preview request")?;
            if !logical(workspace) {
                return Err("Invalid repository, workspace or published branch identity".into());
            }
        }
        if let Some(source_ref) = fields.get("source_ref") {
            let source_ref = source_ref
                .as_str()
                .ok_or("Invalid typed start preview request")?
                .trim();
            let branch = source_ref.strip_prefix("refs/heads/").unwrap_or("");
            if branch.is_empty()
                || branch.len() > 200
                || branch.starts_with('-')
                || branch.ends_with(['.', '/'])
                || branch.contains("..")
                || branch.contains("//")
                || branch
                    .split('/')
                    .any(|part| part.starts_with('.') || part.ends_with(".lock"))
                || !branch
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"._/-".contains(&byte))
            {
                return Err("Invalid repository, workspace or published branch identity".into());
            }
        }
    }
    Ok(())
}

// Read-only compound preview. Execution still goes through typed operations;
// this route accepts the reviewed document and a bounded selection only.
fn validate_adopt_start_preview(body: Option<&Value>) -> Result<(), String> {
    let fail = || "Invalid typed adopt-and-start preview request".to_string();
    let doc = body.and_then(Value::as_object).ok_or_else(fail)?;
    if doc.keys().any(|key| {
        !matches!(
            key.as_str(),
            "proposal_id" | "review" | "rationale" | "works"
        )
    }) || !Regex::new(r"^pmcp_[0-9a-f]{32}$")
        .unwrap()
        .is_match(doc.get("proposal_id").and_then(Value::as_str).unwrap_or(""))
    {
        return Err(fail());
    }
    let text = |value: &Value, max: usize| {
        value
            .as_str()
            .is_some_and(|s| !s.trim().is_empty() && s.chars().count() <= max && !s.contains('\0'))
    };
    if doc.get("rationale").is_some_and(|value| !text(value, 4000)) {
        return Err(fail());
    }
    let review = doc
        .get("review")
        .and_then(Value::as_object)
        .ok_or_else(fail)?;
    if review.len() != 4
        || review.keys().any(|key| {
            !matches!(
                key.as_str(),
                "title" | "rows" | "requirements" | "removed_requirement_ids"
            )
        })
        || !review.get("title").is_some_and(|value| text(value, 160))
    {
        return Err(fail());
    }
    let rows = review
        .get("rows")
        .and_then(Value::as_array)
        .ok_or_else(fail)?;
    let requirements = review
        .get("requirements")
        .and_then(Value::as_array)
        .ok_or_else(fail)?;
    let removed = review
        .get("removed_requirement_ids")
        .and_then(Value::as_array)
        .ok_or_else(fail)?;
    if !(1..=20).contains(&rows.len()) || requirements.len() > 20 || removed.len() > 20 {
        return Err(fail());
    }
    let item = Regex::new(r"^[a-z][a-z0-9_.-]{0,79}$").unwrap();
    let ids = |value: &Value, max: usize| {
        value.as_array().is_some_and(|list| {
            list.len() <= max
                && list
                    .iter()
                    .all(|id| id.as_str().is_some_and(|id| item.is_match(id)))
        })
    };
    for row in rows {
        let row = row.as_object().ok_or_else(fail)?;
        if row.len() != 4
            || row
                .keys()
                .any(|key| !matches!(key.as_str(), "key" | "text" | "status" | "include"))
            || !row.get("key").is_some_and(|value| text(value, 60))
            || !row.get("text").is_some_and(|value| text(value, 4000))
            || !matches!(
                row.get("status").and_then(Value::as_str),
                Some(
                    "user_confirmed"
                        | "agent_assumption"
                        | "unresolved"
                        | "conflicting"
                        | "not_applicable"
                )
            )
            || !row.get("include").is_some_and(Value::is_boolean)
        {
            return Err(fail());
        }
    }
    for requirement in requirements {
        let row = requirement.as_object().ok_or_else(fail)?;
        if row.len() != 5
            || row.keys().any(|key| {
                !matches!(
                    key.as_str(),
                    "id" | "source_key" | "acceptance_keys" | "priority" | "depends_on"
                )
            })
            || !row
                .get("id")
                .and_then(Value::as_str)
                .is_some_and(|value| item.is_match(value))
            || !row.get("source_key").is_some_and(|value| text(value, 60))
            || !row
                .get("acceptance_keys")
                .is_some_and(|value| ids(value, 20))
            || !row.get("depends_on").is_some_and(|value| ids(value, 20))
            || !matches!(
                row.get("priority").and_then(Value::as_str),
                Some("must" | "should" | "could" | "unresolved")
            )
        {
            return Err(fail());
        }
    }
    if removed
        .iter()
        .any(|value| !value.as_str().is_some_and(|id| item.is_match(id)))
    {
        return Err(fail());
    }
    let works = doc
        .get("works")
        .and_then(Value::as_array)
        .ok_or_else(fail)?;
    if !(1..=10).contains(&works.len()) {
        return Err(fail());
    }
    let mut seen = std::collections::HashSet::new();
    let work_id = Regex::new(r"^wi_[0-9a-f]{20}$").unwrap();
    for work in works {
        let mut work = work.as_object().ok_or_else(fail)?.clone();
        let id = work
            .remove("work_item_id")
            .and_then(|v| v.as_str().map(str::to_owned))
            .ok_or_else(fail)?;
        if !work_id.is_match(&id) || !seen.insert(id) || !work.contains_key("requirement_ids") {
            return Err(fail());
        }
        if work
            .remove("agent")
            .is_some_and(|value| !matches!(value.as_str(), Some("claude" | "codex")))
            || work
                .remove("model")
                .is_some_and(|value| !value.is_null() && !text(&value, 128))
        {
            return Err(fail());
        }
        validate_start_preview(Some(&Value::Object(work)))?;
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
    pub brief_exports: bool,
    pub diagnostics_save: bool,
    pub app_build: crate::diagnostics_files::AppBuild,
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
    pub managed_recovery: Option<String>,
    pub managed_rebind: Option<u64>,
    pub startup_role: Option<crate::startup_role::Snapshot>,
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
    recovery_enabled: bool,
    startup_pending: bool,
    managed_rebind: u64,
    recovery_error: Option<String>,
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
            state.recovery_enabled = true;
        }
        bridge
    }
    pub fn role_pending(config_dir: &Path) -> Self {
        let mut bridge = Self::new(
            Err("STARTUP_ROLE_PENDING".into()),
            Zeroizing::new(String::new()),
        );
        bridge.configuration_file = Some(config_dir.join("central.json"));
        bridge.state.lock().unwrap().startup_pending = true;
        bridge
    }

    pub async fn select_startup_role(
        &self,
        managed: Arc<crate::managed::Managed>,
        input: crate::startup_role::Selection,
    ) -> Result<(), String> {
        let _guard = self
            .connecting
            .try_lock()
            .map_err(|_| "MANAGED_SERVICE_BUSY")?;
        let generation = {
            let state = self.state.lock().unwrap();
            if !state.startup_pending {
                return Err("STARTUP_ROLE_CHANGED".into());
            }
            state.generation
        };
        let result = tauri::async_runtime::spawn_blocking(move || {
            managed.select_role(input.role, input.expected_generation)
        })
        .await
        .map_err(|_| "MANAGED_SERVICE_UNAVAILABLE")?;
        match result {
            Ok(launch) => self.install_startup_launch(generation, launch).await,
            Err(error) => {
                let mut state = self.state.lock().unwrap();
                if state.generation == generation && state.startup_pending {
                    // Keep failures visible; a persisted provider resumes via its owner on reopen.
                    // Do not offer a second choice after an uncertain ensure.
                    state.config = Err(error.clone());
                }
                Err(error)
            }
        }
    }

    async fn install_startup_launch(
        &self,
        generation: u64,
        launch: Option<crate::managed::Launch>,
    ) -> Result<(), String> {
        let check = |state: &ConnectionState| -> Result<(), String> {
            if !state.startup_pending || state.generation != generation || self.configuration_file.as_ref().is_none_or(|path|
                !matches!(path.symlink_metadata(), Err(error) if error.kind() == std::io::ErrorKind::NotFound)) {
                return Err("STARTUP_ROLE_CHANGED".into());
            }
            Ok(())
        };
        check(&self.state.lock().unwrap())?;
        let verified = if let Some(launch) = launch {
            let config = normalize(launch.config())?;
            let (_, identity) = self
                .verify(&config, &launch.token, Some(&launch.identity()), generation)
                .await?;
            Some((config, identity, launch.token))
        } else {
            None
        };
        let mut state = self.state.lock().unwrap();
        check(&state)?;
        state.generation += 1;
        state.startup_pending = false;
        state.recovery_error = None;
        if let Some((config, identity, token)) = verified {
            state.managed = true;
            state.recovery_enabled = true;
            state.managed_rebind += 1;
            state.config = Ok(config.clone());
            state.environment = Some(token.clone());
            state.environment_identity = Some(identity.clone());
            state.active = Some(Active {
                config: config.clone(),
                record: Record {
                    version: 1,
                    binding: binding(&config),
                    identity,
                    token,
                },
            });
        } else {
            state.managed = false;
            state.recovery_enabled = false;
            state.config = Err("Create central.json in the app configuration directory".into());
            state.environment = None;
            state.environment_identity = None;
            state.active = None;
        }
        Ok(())
    }

    pub fn managed_hold(&self, expected_generation: Option<u64>) {
        let mut state = self.state.lock().unwrap();
        // Both-role external connections retain their binding and credential.
        if state.managed
            && expected_generation.is_none_or(|generation| generation == state.generation)
        {
            state.active = None;
            state.environment = None;
            state.config = Err("SERVICE_STOPPED".into());
            state.recovery_enabled = false;
            state.recovery_error = None;
            state.generation += 1;
        }
    }

    pub async fn managed_lifecycle(
        &self,
        managed: Arc<crate::managed::Managed>,
        start: bool,
    ) -> Result<crate::managed::Status, String> {
        let _guard = self
            .connecting
            .try_lock()
            .map_err(|_| "MANAGED_SERVICE_BUSY")?;
        let owner = managed.clone();
        let result = tauri::async_runtime::spawn_blocking(move || {
            let _serial = owner
                .serial
                .try_lock()
                .map_err(|_| "MANAGED_SERVICE_BUSY")?;
            if start {
                owner.start().map(Some)
            } else {
                owner.stop().map(|_| None)
            }
        })
        .await
        .map_err(|_| "MANAGED_SERVICE_UNAVAILABLE")?;
        let launch = result?;
        if let Some(launch) = launch {
            let (local, generation, expected) = {
                let state = self.state.lock().unwrap();
                (
                    state.managed,
                    state.generation,
                    state.environment_identity.clone(),
                )
            };
            if local {
                if expected
                    .as_ref()
                    .is_some_and(|identity| identity != &launch.identity())
                {
                    return Err("MANAGED_RECOVERY_IDENTITY_CHANGED".into());
                }
                let config = normalize(launch.config())?;
                let (_, identity) = self
                    .verify(&config, &launch.token, Some(&launch.identity()), generation)
                    .await?;
                let mut state = self.state.lock().unwrap();
                if state.generation != generation {
                    return Err("MANAGED_RECOVERY_CANCELLED".into());
                }
                state.config = Ok(config.clone());
                state.environment = Some(launch.token.clone());
                state.environment_identity = Some(identity.clone());
                state.active = Some(Active {
                    config: config.clone(),
                    record: Record {
                        version: 1,
                        binding: binding(&config),
                        identity,
                        token: launch.token,
                    },
                });
                state.recovery_enabled = true;
                state.recovery_error = None;
                state.generation += 1;
                state.managed_rebind += 1;
            }
        } else {
            self.managed_hold(None);
        }
        Ok(managed.status())
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
                recovery_enabled: false,
                startup_pending: false,
                managed_rebind: 0,
                recovery_error: None,
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
            brief_exports: true,
            diagnostics_save: true,
            app_build: crate::diagnostics_files::app_build(),
            endpoint: config.map(|c| c.endpoint.clone()),
            expected_actor: config.map(|c| c.expected_actor.clone()),
            error: state
                .config
                .as_ref()
                .err()
                .filter(|error| !crate::managed::held(error))
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
            configuration_setup: !state.startup_pending && (state.managed || state.config.is_err()) && self.configuration_file.as_ref().is_some_and(|path| {
                matches!(path.symlink_metadata(), Err(error) if error.kind() == std::io::ErrorKind::NotFound)
            }),
            configuration_source: if state.managed { "managed" } else { "external" },
            configuration_file: self
                .configuration_file
                .as_ref()
                .map(|p| p.to_string_lossy().into_owned()),
            connected: state.active.is_some(),
            managed_recovery: state.recovery_error.clone(),
            managed_rebind: state.managed.then_some(state.managed_rebind),
            startup_role: None,
        }
    }
    // No bearer on the liveness read. Any HTTP response is reachable: authentication,
    // protocol and identity failures must not provision a service.
    pub async fn managed_probe(&self) -> Option<(u64, bool)> {
        let (generation, config) = {
            let state = self.state.lock().unwrap();
            if !state.managed
                || !state.recovery_enabled
                || state.environment_identity.is_none()
                || state.environment.is_none()
            {
                return None;
            }
            (state.generation, state.config.as_ref().ok()?.clone())
        };
        let url = config.validate().ok()?.join("api/v1/capabilities").ok()?;
        let reachable = self
            .client
            .get(url)
            .timeout(Duration::from_secs(2))
            .send()
            .await
            .is_ok();
        self.unchanged(generation).ok()?;
        Some((generation, reachable))
    }

    pub fn recovery_state(&self, generation: u64, error: Option<String>) {
        let mut state = self.state.lock().unwrap();
        if state.generation == generation && state.managed && state.recovery_enabled {
            if error.as_deref() == Some("MANAGED_RECOVERY_IDENTITY_CHANGED") {
                state.active = None;
                state.generation += 1;
                state.recovery_enabled = false;
            }
            state.recovery_error = error;
        }
    }

    pub async fn recover_managed(
        &self,
        generation: u64,
        managed: Arc<crate::managed::Managed>,
    ) -> Result<(), String> {
        // Keep configuration setup/enrollment serialized with ensure and verification.
        let _guard = self
            .connecting
            .try_lock()
            .map_err(|_| "MANAGED_RECOVERY_BUSY")?;
        {
            let state = self.state.lock().unwrap();
            if !state.managed || !state.recovery_enabled || state.generation != generation
                || self.configuration_file.as_ref().is_some_and(|path|
                    !matches!(path.symlink_metadata(), Err(error) if error.kind() == std::io::ErrorKind::NotFound)) {
                return Err("MANAGED_RECOVERY_CANCELLED".into());
            }
        }
        let launch = tauri::async_runtime::spawn_blocking(move || managed.recover())
            .await
            .map_err(|_| "MANAGED_RECOVERY_FAILED")??;
        self.rebind_managed(generation, launch).await
    }

    pub async fn rebind_managed(
        &self,
        generation: u64,
        launch: crate::managed::Launch,
    ) -> Result<(), String> {
        let config = normalize(launch.config()).map_err(|_| "MANAGED_RUNTIME_ENDPOINT_INVALID")?;
        let expected = {
            let state = self.state.lock().unwrap();
            if !state.managed || !state.recovery_enabled || state.generation != generation {
                return Err("MANAGED_RECOVERY_CANCELLED".into());
            }
            let identity = state
                .environment_identity
                .clone()
                .ok_or("MANAGED_RECOVERY_IDENTITY_CHANGED")?;
            if identity != launch.identity()
                || state.config.as_ref().ok().map(|c| &c.expected_actor)
                    != Some(&config.expected_actor)
            {
                return Err("MANAGED_RECOVERY_IDENTITY_CHANGED".into());
            }
            identity
        };
        // Same bootstrap/capabilities verification as connect; do not publish a candidate
        // endpoint or credential until both reads prove the original personal identity.
        let (_, identity) = self
            .verify(&config, &launch.token, Some(&expected), generation)
            .await
            .map_err(|error| {
                if error.contains("Saved backend or principal identity changed") {
                    "MANAGED_RECOVERY_IDENTITY_CHANGED"
                } else {
                    "MANAGED_RECOVERY_VERIFICATION_FAILED"
                }
            })?;
        let mut state = self.state.lock().unwrap();
        if !state.managed || !state.recovery_enabled || state.generation != generation {
            return Err("MANAGED_RECOVERY_CANCELLED".into());
        }
        state.generation += 1;
        state.managed_rebind += 1;
        state.config = Ok(config.clone());
        state.environment = Some(launch.token.clone());
        state.active = Some(Active {
            config: config.clone(),
            record: Record {
                version: 1,
                binding: binding(&config),
                identity,
                token: launch.token,
            },
        });
        state.recovery_error = None;
        Ok(())
    }

    pub fn disconnect(&self) {
        let mut state = self.state.lock().unwrap();
        state.recovery_enabled = false;
        state.recovery_error = None;
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
            if state.startup_pending {
                return Err("STARTUP_ROLE_PENDING".into());
            }
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
            state.recovery_enabled = false;
            state.recovery_error = None;
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
        state.recovery_enabled = false;
        state.recovery_error = None;
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
        state.recovery_enabled = state.managed;
        state.recovery_error = None;
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
        self.send_encoded(config, token, input, None).await
    }
    async fn send_encoded(
        &self,
        config: &Config,
        token: &str,
        input: &ConnectorRequest,
        encoded: Option<&[u8]>,
    ) -> Result<ConnectorResponse, String> {
        let url = config
            .validate()?
            .join(&format!("api/v1{}", input.path))
            .map_err(|_| "Invalid central route")?;
        let method = Method::from_bytes(input.method.as_bytes()).map_err(|_| "Invalid method")?;
        let mut request = self.client.request(method, url).bearer_auth(token);
        if let Some(encoded) = encoded {
            request = request
                .header(reqwest::header::CONTENT_TYPE, "application/json")
                .body(encoded.to_vec());
        } else if let Some(body) = &input.body {
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

// Same conservative artifact paths as the central reader; never local filesystem paths.
fn valid_spec_source_path(path: &str) -> bool {
    if path.len() > 800 {
        return false;
    }
    if path == "openspec/config.yaml" {
        return true;
    }
    let component = |part: &str| {
        !part.is_empty()
            && part.len() <= 100
            && part.as_bytes()[0].is_ascii_alphanumeric()
            && part
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
    };
    let parts: Vec<_> = path.split('/').collect();
    let tail = if parts.starts_with(&["openspec", "specs"]) {
        &parts[2..]
    } else if parts.starts_with(&["openspec", "changes"]) && parts.len() >= 4 {
        let offset = if parts[2] == "archive" { 3 } else { 2 };
        if parts.len() <= offset + 1 || !component(parts[offset]) {
            return false;
        }
        let artifact = &parts[offset + 1..];
        if artifact.len() == 1
            && matches!(
                artifact[0],
                "proposal.md" | "design.md" | "tasks.md" | ".openspec.yaml"
            )
        {
            return true;
        }
        if artifact[0] != "specs" {
            return false;
        }
        &artifact[1..]
    } else {
        return false;
    };
    (2..=9).contains(&tail.len())
        && tail.last() == Some(&"spec.md")
        && tail[..tail.len() - 1].iter().all(|part| component(part))
}

pub fn validate_request(input: &ConnectorRequest) -> Result<(), String> {
    // Match raw paths before URL parsing so traversal cannot be normalized into an allowed route.
    let (path, query) = input.path.split_once('?').unwrap_or((&input.path, ""));
    let package_readiness = input.method == "GET"
        && path.starts_with("/pm/workspaces/")
        && path.contains("/work-packages/")
        && path.ends_with("/readiness");
    if input.path.len() > if package_readiness { 65_536 } else { 4096 }
        || input.path.contains(['\\', '#', '\r', '\n'])
        || input.path.chars().any(char::is_control)
    {
        return Err("Invalid central route".into());
    }
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
            r"pm/installation-capabilities|pm/profiles|pm/workspaces(?:/ws_[0-9a-f]{32}(?:/threads(?:/pmt_[0-9a-f]{32}(?:/context(?:-bundle)?)?)?|/runs(?:/pmr_[0-9a-f]{32})?|/lifecycle(?:/(?:transitions(?:/pmlt_[0-9a-f]{32})?|inflight))?|/plans(?:/(?:head|accepted|pmrev_[0-9a-f]{32}))?|/resources|/history(?:/[A-Za-z0-9][A-Za-z0-9_.:-]{0,127})?|/overview|/schedule|/register(?:/(?:governance|finding|repair|follow_up|proposal|issue_mapping|meeting|plan_revision)\.[A-Za-z0-9._-]{1,180})?|/dependencies(?:/(?:revisions|impact))?|/projects|/kanban(?:/wi_[0-9a-f]{20})?|/governance(?:/pmgov_[0-9a-f]{32}(?:/history)?)?|/issue-mappings(?:/pmim_[0-9a-f]{32})?|/release-baseline-sources(?:/(?:github-releases|deployments))?|/release-baseline-preview|/release-baselines(?:/(?:current|pmrb_[0-9a-f]{32}))?|/maintenance-policies(?:/(?:current|pmmp_[0-9a-f]{32}))?|/workload-snapshots(?:/(?:current|pmwl_[0-9a-f]{32}))?|/maintenance-checks(?:/(?:current|pmck_[0-9a-f]{32}))?|/maintenance-reviews(?:/(?:current|pmrevw_[0-9a-f]{32}(?:/events)?))?|/maintenance-schedule|/ahp-adapters|/ahp-observations(?:/(?:current|pmah_[0-9a-f]{32}))?|/github-observations(?:/(?:current|pmgh_[0-9a-f]{32}))?|/maintenance-repairs(?:/(?:current|pmrp_[0-9a-f]{32}))?|/executions/op_[0-9a-f]{32}/verification-config|/execution-verifications/op_[0-9a-f]{32}|/pr-publications/op_[0-9a-f]{32}|/quality-reviews(?:/pmqr_[0-9a-f]{32})?|/work-batches(?:/pmbt_[0-9a-f]{32})?|/maintenance-findings/current|/spec-materializations(?:/pmsp_[0-9a-f]{32})?|/work-profiles(?:/wpf_[0-9a-f]{32}(?:/readiness)?)?|/work-packages(?:/pmwp_[0-9a-f]{32}(?:/readiness)?)?|/conversation-proposals(?:/pmcp_[0-9a-f]{32})?|/overview-assessments(?:/pmoa_[0-9a-f]{32})?|/follow-up-assessments(?:/pmfa_[0-9a-f]{32})?|/follow-ups(?:/(?:latest|pmfu_[0-9a-f]{32}))?|/meetings(?:/pmmeet_[0-9a-f]{32}(?:/history/(?:contexts|notes|drafts|follow_ups))?)?|/sprint-artifacts(?:/(?:product_goal(?:/pmgoal_[0-9a-f]{32})?|dod(?:/pmdod_[0-9a-f]{32})?|backlog(?:/pmbkl_[0-9a-f]{32})?))?|/sprints(?:/pms_[0-9a-f]{32}(?:/history/(?:goals|selections|events))?)?|/spec-sources(?:/capabilities)?|/plan-comparison|/roadmap(?:/revisions(?:/pmr[bfo]_[0-9a-f]{32})?)?|/snapshots(?:/(?:latest|pmsnap_[0-9a-f]{32}(?:/brief-export)?))?|/work(?:/wi_[0-9a-f]{20}/(?:capture|deliveries|quality|attempts|inputs))?|/work-attempts/att_[0-9a-f]{32}(?:/(?:result|changes|diff))?|/evidence(?:/pme_[0-9a-f]{32})?|/captures/pmcap_[0-9a-f]{32}|/capabilities)?)?|",
            r"cleanup-retained|cleanup-tombstones(?:/(?:cr|wt)_[0-9a-f]{32})?|artifacts(?:/art_[0-9a-f]{32}/revisions/[1-9][0-9]{0,8})?|",
            r"sessions/[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+(?:/(?:messages|checkpoint-preview|history|relations|instructions))?|",
            r"operations/op_[0-9a-f]{32}|tasks/[0-9a-f-]{8,64}(?:/(?:history|sessions))?|checkpoints/cp_[0-9a-f]{32}|",
            r"hosts/[A-Za-z0-9_.-]+/(?:discovery|preferences)|worktrees/wt_[0-9a-f]{32}(?:/(?:history|relations))?|",
            r"projects/prj_[0-9a-f]{20}(?:/(?:skills|repair-evidence))?|work-items/wi_[0-9a-f]{20}(?:/(?:result-sources|repair))?|repositories/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pulls(?:/[0-9]{1,9})?|",
            r"deployments(?:/(?:preview|dep_[0-9a-f]{32}))?|deployment-environments(?:/history)?|",
            r"delivery/previews/mpv_[0-9a-f]{32}|integrations/previews/ipv_[0-9a-f]{32}|integrations/op_[0-9a-f]{32})$")).unwrap())
    } else if input.method == "POST" {
        POST.get_or_init(|| {
            Regex::new(r"^/(?:managed/setup/(?:secrets|preview|discover)|repository-previews|approval-previews|artifact-capture-previews|artifact-managed-capture-previews|cleanup-previews|operations(?:/op_[0-9a-f]{32}/(?:cancel|resume))?|pm/workspaces/ws_[0-9a-f]{32}/(?:work/wi_[0-9a-f]{20}/start-preview|threads/pmt_[0-9a-f]{32}/adopt-and-start-preview))$")
                .unwrap()
        })
    } else {
        return Err("Only defined central GET and operation POST requests are allowed".into());
    };
    if !pattern.is_match(path) {
        return Err("Central route is not allowed".into());
    }
    if matches!(
        path,
        "/managed/setup"
            | "/managed/setup/secrets"
            | "/managed/setup/preview"
            | "/managed/setup/discover"
    ) {
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
    if path.ends_with("/adopt-and-start-preview") {
        if input.path != path || input.idempotency_key.is_some() {
            return Err("Adopt-and-start preview accepts no query or operation key".into());
        }
        validate_adopt_start_preview(input.body.as_ref())?;
    }
    if path.ends_with("/start-preview") {
        if input.path != path || input.idempotency_key.is_some() {
            return Err("Start preview accepts no query or operation key".into());
        }
        validate_start_preview(input.body.as_ref())?;
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
    let resource_timestamp = if pm && path.ends_with("/resources") {
        Some(Regex::new(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$").unwrap())
    } else {
        None
    };
    let schedule = pm && path.ends_with("/schedule");
    let mut query_keys = std::collections::HashSet::new();
    for (key, value) in url::form_urlencoded::parse(query.as_bytes()) {
        if key.chars().chain(value.chars()).any(char::is_control) {
            return Err("Control characters in central query are refused".into());
        }
        if pm {
            let profile_list = path.ends_with("/work-profiles");
            let thread_list = path.ends_with("/threads");
            let run_list = path.ends_with("/runs");
            let plan_list = path.ends_with("/plans");
            let project_list = path.ends_with("/projects");
            let work_list = path.ends_with("/work");
            let evidence_list = path.ends_with("/evidence");
            let snapshot_list = path.ends_with("/snapshots");
            let brief_export = path.ends_with("/brief-export");
            let lifecycle_inflight = path.ends_with("/lifecycle/inflight");
            let lifecycle_list = lifecycle_inflight || path.ends_with("/lifecycle/transitions");
            let comparison = path.ends_with("/plan-comparison");
            let spec_sources = path.ends_with("/spec-sources");
            let roadmap_list = path.ends_with("/roadmap/revisions");
            let governance_list = path.ends_with("/governance");
            let register_list = path.ends_with("/register");
            let external_history =
                path.split('/').nth(4) == Some("history") && path.split('/').count() == 6;
            let follow_up_list = path.ends_with("/follow-ups");
            let assessment_list = path.ends_with("/follow-up-assessments");
            let overview_assessment_list = path.ends_with("/overview-assessments");
            let conversation_list = path.ends_with("/conversation-proposals");
            let work_package_list = path.ends_with("/work-packages");
            let issue_mapping_list = path.ends_with("/issue-mappings");
            let maintenance_review_events =
                path.contains("/maintenance-reviews/") && path.ends_with("/events");
            let maintenance_review_detail = path.split('/').nth(4) == Some("maintenance-reviews")
                && path.split('/').count() == 6;
            let spec_materialization_list = path.ends_with("/spec-materializations");
            let repair_list = path.ends_with("/maintenance-repairs");
            let workload_list = path.ends_with("/dependencies/revisions")
                || conversation_list
                || path.ends_with("/deliveries")
                || repair_list
                || path.ends_with("/quality-reviews")
                || path.ends_with("/quality")
                || path.ends_with("/work-batches")
                || spec_materialization_list
                || maintenance_review_events
                || path.ends_with("/maintenance-reviews")
                || path.ends_with("/workload-snapshots")
                || path.ends_with("/ahp-observations")
                || path.ends_with("/github-observations")
                || path.ends_with("/maintenance-checks");
            let release_baseline_list =
                path.ends_with("/release-baselines") || path.ends_with("/maintenance-policies");
            let baseline_catalog = path.ends_with("/release-baseline-sources");
            let baseline_releases = path.ends_with("/release-baseline-sources/github-releases");
            let baseline_deployments = path.ends_with("/release-baseline-sources/deployments");
            let baseline_preview = path.ends_with("/release-baseline-preview");
            let baseline_source = baseline_releases || baseline_deployments || baseline_preview;
            let governance_history = path.contains("/governance/") && path.ends_with("/history");
            let kanban_list = path.ends_with("/kanban");
            let meeting_list = path.ends_with("/meetings");
            let meeting_history = path.contains("/meetings/") && path.contains("/history/");
            let meeting_detail =
                path.split('/').nth(4) == Some("meetings") && path.split('/').count() == 6;
            let sprint_list = path.ends_with("/sprints");
            let sprint_history = path.contains("/sprints/") && path.contains("/history/");
            let artifact_list = path.contains("/sprint-artifacts/")
                && matches!(
                    path.rsplit('/').next(),
                    Some("product_goal" | "dod" | "backlog")
                );
            let sprint_detail =
                path.split('/').nth(4) == Some("sprints") && path.split('/').count() == 6;
            let sequence_list = lifecycle_list
                || plan_list
                || project_list
                || work_list
                || evidence_list
                || snapshot_list
                || roadmap_list
                || sprint_list
                || sprint_history
                || artifact_list
                || governance_list
                || follow_up_list
                || assessment_list
                || work_package_list
                || issue_mapping_list
                || workload_list
                || release_baseline_list
                || baseline_catalog
                || baseline_deployments
                || governance_history
                || kanban_list
                || meeting_list
                || meeting_history;
            let thread_detail = path
                .split('/')
                .nth(5)
                .is_some_and(|id| id.starts_with("pmt_"))
                && !path.ends_with("/context")
                && !path.ends_with("/context-bundle");
            let attempt_list = path.contains("/work/") && path.ends_with("/attempts");
            let input_list = path.contains("/work/") && path.ends_with("/inputs");
            let attempt_result = path.contains("/work-attempts/") && path.ends_with("/result");
            let attempt_changes = path.contains("/work-attempts/") && path.ends_with("/changes");
            let attempt_diff = path.contains("/work-attempts/") && path.ends_with("/diff");
            let valid = match key.as_ref() {
                "kind" if path.ends_with("/context-bundle") => value == "overview_assessment",
                "before" if overview_assessment_list => {
                    !value.is_empty()
                        && value.len() <= 256
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
                }
                "limit" if overview_assessment_list => {
                    !value.is_empty()
                        && value.len() <= 2
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=50).contains(&n))
                }
                "dependency" if path.ends_with("/dependencies/impact") => {
                    value.len() == 41
                        && value.starts_with("pmdepkey_")
                        && value[9..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "since" | "until" if path.ends_with("/resources") => {
                    value.len() <= 40
                        && resource_timestamp
                            .as_ref()
                            .is_some_and(|pattern| pattern.is_match(&value))
                }
                "buckets" if path.ends_with("/resources") => value == "day",
                "tz" if path.ends_with("/resources") => {
                    (1..=64).contains(&value.len())
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"/_+-".contains(&b))
                        && !value.contains("//")
                        && !value.contains("..")
                        && !value.starts_with('/')
                        && !value.ends_with('/')
                }
                "after" if external_history => {
                    !value.is_empty()
                        && value.len() <= 16
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value
                            .parse::<u64>()
                            .is_ok_and(|n| n <= 9_007_199_254_740_991)
                }
                "limit" if external_history => {
                    !value.is_empty()
                        && value.len() <= 3
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=100).contains(&n))
                }
                "limit" if profile_list => {
                    !value.is_empty()
                        && value.len() <= 3
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=100).contains(&n))
                }
                "cursor" if profile_list => {
                    !value.is_empty()
                        && value.len() <= 19
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| n >= 0)
                }
                "limit" if schedule => {
                    !value.is_empty()
                        && value.len() <= 3
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=100).contains(&n))
                }
                "after" if schedule => {
                    let bytes = value.as_bytes();
                    (1..=2048).contains(&bytes.len())
                        && bytes
                            .iter()
                            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
                }
                "type" if register_list => {
                    matches!(
                        value.as_ref(),
                        "risk" | "issue" | "action" | "change" | "decision"
                    )
                }
                "status" if register_list => {
                    !value.is_empty()
                        && value.len() <= 64
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
                }
                "owner" | "q" if register_list => !value.is_empty() && value.chars().count() <= 200,
                "after" if register_list => {
                    !value.is_empty()
                        && value.len() <= 400
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
                }
                "limit" if register_list => {
                    !value.is_empty()
                        && value.len() <= 3
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=100).contains(&n))
                }
                "quality_refs" if package_readiness => {
                    value.len() <= 16_384
                        && serde_json::from_str::<serde_json::Value>(&value)
                            .is_ok_and(|selected| selected.is_object())
                }
                "after" if workload_list => {
                    !value.is_empty()
                        && value.len() <= 19
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| n >= 0)
                }
                "package_id" if spec_materialization_list => {
                    value.len() == 37
                        && value.starts_with("pmwp_")
                        && value[5..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "limit"
                    if spec_materialization_list
                        || repair_list
                        || path.ends_with("/ahp-observations") =>
                {
                    !value.is_empty()
                        && value.len() <= 2
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=50).contains(&n))
                }
                "project_binding_id" if baseline_source => {
                    value.len() == 37
                        && value.starts_with("pmpb_")
                        && value[5..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "kind" if baseline_preview => {
                    matches!(value.as_ref(), "github_release" | "deployment")
                }
                "release_id" if baseline_preview => {
                    !value.is_empty()
                        && value.len() <= 19
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| n > 0)
                }
                "deployment_id" if baseline_preview => {
                    value.len() == 36
                        && value.starts_with("dep_")
                        && value[4..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "maintenance_branch" if baseline_preview => {
                    !value.is_empty()
                        && value.len() <= 256
                        && !value.chars().any(char::is_whitespace)
                }
                "page" if baseline_releases => {
                    !value.is_empty()
                        && value.len() <= 5
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u32>().is_ok_and(|n| (1..=10000).contains(&n))
                }
                "limit" if baseline_catalog || baseline_releases || baseline_deployments => {
                    !value.is_empty()
                        && value.len() <= 2
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=20).contains(&n))
                }
                "format" if brief_export => matches!(value.as_ref(), "markdown" | "html" | "svg"),
                "locale" if brief_export => matches!(value.as_ref(), "en" | "zh-TW"),
                "repository" if spec_sources || baseline_source => {
                    value.len() <= 256
                        && value.split('/').count() == 2
                        && value.split('/').all(|part| {
                            !part.is_empty()
                                && part != "."
                                && part != ".."
                                && part
                                    .bytes()
                                    .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
                        })
                }
                "commit_sha" if spec_sources => {
                    value.len() == 40
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "paths" if spec_sources => {
                    let mut selected = std::collections::HashSet::new();
                    !value.is_empty()
                        && value.split(',').count() <= 100
                        && value
                            .split(',')
                            .all(|path| valid_spec_source_path(path) && selected.insert(path))
                }
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
                "history_limit" if sprint_detail || meeting_detail || maintenance_review_detail => {
                    !value.is_empty()
                        && value.len() <= 3
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u16>().is_ok_and(|n| (1..=100).contains(&n))
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
                "after" if lifecycle_list => {
                    !value.is_empty()
                        && value.len() <= 19
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| n >= 0)
                }
                "transition_id" if lifecycle_inflight => {
                    value.len() == 37
                        && value.starts_with("pmlt_")
                        && value[5..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "after" if sequence_list => {
                    !value.is_empty()
                        && value.len() <= 9
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<u32>().is_ok()
                }
                "intake_id" if assessment_list => {
                    value.len() == 37
                        && value.starts_with("pmfu_")
                        && value[5..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "project_id" if work_list || kanban_list => {
                    value.len() == 24
                        && value.starts_with("prj_")
                        && value[4..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "work_item_id" if evidence_list || work_package_list => {
                    value.len() == 23
                        && value.starts_with("wi_")
                        && value[3..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "kind" if governance_list => matches!(
                    value.as_ref(),
                    "risk" | "assumption" | "issue" | "dependency" | "drift" | "change"
                ),
                "sprint_id" if meeting_list => {
                    value.len() == 36
                        && value.starts_with("pms_")
                        && value[4..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "thread_id" if run_list || conversation_list => {
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
                "max_chars" if path.ends_with("/context") || path.ends_with("/context-bundle") => {
                    !value.is_empty()
                        && value.len() <= 5
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value
                            .parse::<u32>()
                            .is_ok_and(|n| (2000..=64000).contains(&n))
                }
                "after" if attempt_list || input_list => {
                    !value.is_empty()
                        && value.len() <= 19
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| n >= 0)
                }
                "limit" if attempt_list || input_list => {
                    !value.is_empty()
                        && value.len() <= 9
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| (1..=100).contains(&n))
                }
                "attempt_id" if input_list => {
                    value.len() == 36
                        && value.starts_with("att_")
                        && value[4..]
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "cursor" if attempt_result => {
                    !value.is_empty()
                        && value.len() <= 19
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| n >= 0)
                }
                "wait_ms" if attempt_result => {
                    !value.is_empty()
                        && value.len() <= 9
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value
                            .parse::<i64>()
                            .is_ok_and(|n| (0..=10_000).contains(&n))
                }
                "max_commits" if attempt_changes => {
                    !value.is_empty()
                        && value.len() <= 9
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| (1..=30).contains(&n))
                }
                "max_files" if attempt_changes => {
                    !value.is_empty()
                        && value.len() <= 9
                        && value.bytes().all(|b| b.is_ascii_digit())
                        && value.parse::<i64>().is_ok_and(|n| (1..=80).contains(&n))
                }
                "commit" if attempt_diff => {
                    value.len() == 40
                        && value
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                }
                "path" if attempt_diff => {
                    value.len() <= 2048
                        && !value.trim().is_empty()
                        && !value.starts_with('/')
                        && !value.split('/').any(|part| part == "..")
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
        if key == "projection" {
            if input.method != "GET"
                || !path.starts_with("/projects/prj_")
                || path.split('/').count() != 3
                || !matches!(value.as_ref(), "full" | "navigation")
                || !query_keys.insert(key.to_string())
            {
                return Err("Invalid project read projection".into());
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
    if pm && path.ends_with("/dependencies/impact") && !query_keys.contains("dependency") {
        return Err(
            "Dependency impact requires a captured dependency key / 相依影響需要已擷取的相依識別碼"
                .into(),
        );
    }
    if pm
        && path.ends_with("/spec-sources")
        && !(query_keys.contains("repository") && query_keys.contains("commit_sha"))
    {
        return Err("Fixed specification reads require repository and commit".into());
    }
    if pm
        && path.ends_with("/resources")
        && query_keys.contains("tz")
        && !query_keys.contains("buckets")
    {
        return Err("Resource time zone requires daily buckets".into());
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
    fn setup_discovery_uses_the_existing_fixed_http_forwarder() {
        let mut input = request("POST", "/managed/setup/discover");
        input.body =
            Some(serde_json::json!({"revision": "a".repeat(64), "import_profile_id": "profile-a"}));
        assert!(validate_request(&input).is_ok());
        for path in [
            "/managed/setup/discover?",
            "/managed/setup/discover?host=other",
            "/managed/setup/discover/other",
        ] {
            input.path = path.into();
            assert!(validate_request(&input).is_err());
        }
        input.path = "/managed/setup/discover".into();
        input.idempotency_key = Some("discovery-is-a-read".into());
        assert!(validate_request(&input).is_err());
        assert!(validate_request(&request("GET", "/managed/setup/discover")).is_err());
    }
    #[test]
    fn setup_preview_uses_the_existing_fixed_http_forwarder() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/managed-setup-preview.json"
        ))
        .unwrap();
        let mut input = request("POST", "/managed/setup/preview");
        input.body = Some(fixture["profile"]["request"].clone());
        assert!(validate_request(&input).is_ok());
        for path in [
            "/managed/setup/preview?",
            "/managed/setup/preview?host=other",
            "/managed/setup/preview/other",
        ] {
            input.path = path.into();
            assert!(validate_request(&input).is_err());
        }
        input.path = "/managed/setup/preview".into();
        input.idempotency_key = Some("preview-must-not-create-an-intent".into());
        assert!(validate_request(&input).is_err());
        assert!(validate_request(&request("GET", "/managed/setup/preview")).is_err());
    }
    #[test]
    fn pm_installation_eligibility_is_an_exact_read_only_route() {
        let root = "/pm/installation-capabilities";
        assert!(validate_request(&get(root)).is_ok());
        for suffix in [
            "/refresh",
            "?workspace_id=other",
            "?path=fixture",
            "?token=fixture",
        ] {
            assert!(validate_request(&get(&format!("{root}{suffix}"))).is_err());
        }
        let mut keyed = get(root);
        keyed.idempotency_key = Some("fixture-original-key".into());
        assert!(validate_request(&keyed).is_err());
        let mut write = get(root);
        write.method = "POST".into();
        assert!(validate_request(&write).is_err());
    }
    #[test]
    fn navigation_projection_stays_on_project_reads() {
        let root = "/projects/prj_11111111111111111111";
        for value in ["full", "navigation"] {
            assert!(validate_request(&get(&format!("{root}?projection={value}"))).is_ok());
        }
        for suffix in [
            "?projection=other",
            "?projection=",
            "?projection=navigation&projection=full",
            "/skills?projection=navigation",
        ] {
            assert!(validate_request(&get(&format!("{root}{suffix}"))).is_err());
        }
        assert!(validate_request(&get("/sessions?projection=navigation")).is_err());
    }

    #[test]
    fn project_overview_is_an_exact_read_only_route() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/overview";
        assert!(validate_request(&get(root)).is_ok());
        assert!(validate_request(&get(&format!("{root}/refresh"))).is_err());
        assert!(validate_request(&get(&format!("{root}?path=fixture"))).is_err());
        let mut write = get(root);
        write.method = "POST".into();
        assert!(validate_request(&write).is_err());
    }

    #[test]
    fn dependencies_are_exact_read_routes_without_capture_commands() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/dependencies";
        for suffix in [
            "",
            "/revisions?after=0&limit=100",
            "/impact?dependency=pmdepkey_22222222222222222222222222222222",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "/impact",
            "/capture",
            "/declare",
            "/retract",
            "?path=package.json",
            "?limit=1",
            "/revisions?limit=101",
            "/revisions?limit=0100",
            "/revisions?after=-1",
            "/revisions?limit=1&limit=2",
            "/impact?dependency=name",
            "/impact?dependency=",
            "/impact?dependency=pmdepkey_22222222222222222222222222222222&dependency=pmdepkey_33333333333333333333333333333333",
            "/impact?dependency=pmdepkey_22222222222222222222222222222222&host=fixture",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        let mut write = get(root);
        write.method = "POST".into();
        assert!(validate_request(&write).is_err());
    }

    #[test]
    fn external_history_accepts_only_bounded_read_pages() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/history";
        for suffix in [
            "",
            "/example",
            "/example?after=0&limit=100",
            "/example?after=9007199254740991",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=0",
            "/example?path=/private",
            "/example?refresh=1",
            "/example?limit=101",
            "/example?after=9007199254740992",
            "/example?after=-1",
            "/example?after=1&after=2",
            "/example?before=1",
            "/example/messages",
            "/example?limit=0",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        let mut write = get(&format!("{root}/example"));
        write.method = "POST".into();
        assert!(validate_request(&write).is_err());
    }

    #[test]
    fn resource_usage_is_a_scoped_read_without_paths_or_collection_commands() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/resources";
        assert!(validate_request(&get(root)).is_ok());
        assert!(validate_request(&get(&format!(
            "{root}?since=2026-10-01T00%3A00%3A00Z&until=2026-10-12T12%3A00%3A00.000Z"
        )))
        .is_ok());
        assert!(validate_request(&get(&format!(
            "{root}?since=2026-10-01T00%3A00%3A00Z&until=2026-10-08T00%3A00%3A00Z&buckets=day&tz=Asia%2FTaipei"
        )))
        .is_ok());
        assert!(validate_request(&get(&format!("{root}?buckets=day"))).is_ok());
        assert!(validate_request(&get(&format!("{root}?buckets=day&tz=UTC"))).is_ok());
        assert!(validate_request(&get(&format!("{root}?buckets=day&tz=Etc%2FGMT%2B8"))).is_ok());
        assert!(validate_request(&get(&format!(
            "{root}?buckets=day&tz=America%2FArgentina%2FBuenos_Aires"
        )))
        .is_ok());
        assert!(validate_request(&get(
            "/pm/workspaces/ws_11111111111111111111111111111111/plans?buckets=day"
        ))
        .is_err());
        assert!(validate_request(&get(
            "/pm/workspaces/ws_11111111111111111111111111111111/plans?buckets=day&tz=UTC"
        ))
        .is_err());
        for query in [
            "path=/private/session.jsonl",
            "refresh=1",
            "host=fixture",
            "since=yesterday",
            "since=2026-10-01",
            "since=2026-10-01T00:00:00Z&since=2026-10-02T00:00:00Z",
            "buckets=week",
            "buckets=",
            "tz=UTC",
            "tz=",
            "buckets=day&tz=",
            "buckets=day&tz=../UTC",
            "buckets=day&tz=/UTC",
            "buckets=day&tz=UTC/",
            "buckets=day&tz=America//New_York",
            "buckets=day&tz=Not%20A%20Zone",
            "buckets=day&tz=America.New_York",
            "buckets=day&tz=UTC&tz=Asia%2FTaipei",
        ] {
            assert!(
                validate_request(&get(&format!("{root}?{query}"))).is_err(),
                "{query}"
            );
        }
        assert!(validate_request(&get(&format!("{root}/collect"))).is_err());
        let mut write = get(root);
        write.method = "POST".into();
        assert!(validate_request(&write).is_err());
    }

    #[test]
    fn project_schedule_is_a_scoped_read_without_collection_commands() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/schedule";
        assert!(validate_request(&get(root)).is_ok());
        assert!(validate_request(&get(&format!(
            "{root}?limit=50&after=bWlsZXN0b25lOnJlbGVhc2U"
        )))
        .is_ok());
        assert!(validate_request(&get(&format!(
            "{root}?limit=1&after=YXR0ZW1wdDpzZXNzaW9uOndpXzExMTExMTExMTExMTExMTExMTExOm1vY2staG9zdC9tb2NrLXNlc3Npb24"
        )))
        .is_ok());
        for query in [
            "path=/private/session.jsonl",
            "refresh=1",
            "limit=101",
            "limit=0",
            "limit=0050",
            "after=milestone:release",
            "after=phase:a",
            "after=../x",
            "after=bWls+",
            "after=bWls/zx",
            "after=bWls=",
            "limit=50&limit=1",
        ] {
            assert!(
                validate_request(&get(&format!("{root}?{query}"))).is_err(),
                "{query}"
            );
        }
        assert!(validate_request(&get(&format!("{root}/collect"))).is_err());
        let mut write = get(root);
        write.method = "POST".into();
        assert!(validate_request(&write).is_err());
        let maintenance = "/pm/workspaces/ws_11111111111111111111111111111111/maintenance-schedule";
        assert!(validate_request(&get(maintenance)).is_ok());
        assert!(validate_request(&get(&format!("{maintenance}?limit=1"))).is_err());
    }

    #[test]
    fn schedule_cursor_matches_the_http_query_length_bound() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/schedule";
        for (length, allowed) in [(2048, true), (2049, false)] {
            let path = format!("{root}?after={}", "a".repeat(length));
            assert_eq!(validate_request(&get(&path)).is_ok(), allowed, "{length}");
        }
    }

    #[test]
    fn register_is_a_scoped_read_without_writes() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/register";
        assert!(validate_request(&get(root)).is_ok());
        // Unpadded base64url of "{sequence}\n{row_id}". The bridge checks the alphabet only.
        let cursor = "MQpnb3Zlcm5hbmNlLnBtZ292X2FhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh";
        assert!(validate_request(&get(&format!(
            "{root}?type=risk&status=open&owner=project-owner&q=login&limit=50&after={cursor}"
        )))
        .is_ok());
        assert!(validate_request(&get(&format!("{root}?q=%E7%99%BB%E5%85%A5"))).is_ok());
        assert!(validate_request(&get(&format!("{root}?after=nocursor"))).is_ok());
        let detail = format!("{root}/governance.pmgov_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assert!(validate_request(&get(&detail)).is_ok());
        let meeting = format!(
            "{root}/meeting.pmmeet_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.follow_up.verify_login"
        );
        assert!(validate_request(&get(&meeting)).is_ok());
        // Item ids follow pm_sprints.ITEM_ID, so '.' is part of the id.
        let dotted_follow = format!(
            "{root}/meeting.pmmeet_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.follow_up.check.quality"
        );
        let dotted_decision = format!(
            "{root}/meeting.pmmeet_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.decision.retain.direction"
        );
        assert!(validate_request(&get(&dotted_follow)).is_ok());
        assert!(validate_request(&get(&dotted_decision)).is_ok());
        assert!(validate_request(&get(&format!("{detail}?type=risk"))).is_err());
        assert!(validate_request(&get(&format!(
            "{root}/governance:pmgov_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )))
        .is_err());
        for query in [
            "type=story",
            "type=issue&type=risk",
            "q=one&q=two",
            "q=",
            "owner=",
            "q=a%01b",
            "q=a%C2%85b",
            "owner=a%01b",
            "owner=a%C2%85b",
            "status=Open",
            "limit=0",
            "limit=101",
            "limit=0100",
            "after=abc.def",
            "after=~governance.pmgov_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "after=abc+def",
            "after=abc/def",
            "after=abc=",
            "path=/tmp",
        ] {
            assert!(
                validate_request(&get(&format!("{root}?{query}"))).is_err(),
                "{query}"
            );
        }
        let mut write = get(root);
        write.method = "POST".into();
        assert!(validate_request(&write).is_err());
    }

    #[test]
    fn register_query_text_and_cursor_boundaries_are_exact() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/register";
        for key in ["q", "owner"] {
            for (length, allowed) in [(200, true), (201, false)] {
                // Count decoded characters, including multi-byte Traditional Chinese text.
                let query: String = url::form_urlencoded::Serializer::new(String::new())
                    .append_pair(key, &"字".repeat(length))
                    .finish();
                assert_eq!(
                    validate_request(&get(&format!("{root}?{query}"))).is_ok(),
                    allowed,
                    "{key}: {length}"
                );
            }
        }
        for (length, allowed) in [(400, true), (401, false)] {
            let path = format!("{root}?after={}", "a".repeat(length));
            assert_eq!(validate_request(&get(&path)).is_ok(), allowed, "{length}");
        }
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
    fn work_profiles_use_read_only_routes_and_exact_start_pins() {
        let root = "/pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work-profiles";
        let profile = "wpf_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        for path in [
            root.to_string(),
            format!("{root}?limit=100&cursor=0"),
            format!("{root}/{profile}"),
            format!("{root}/{profile}/readiness"),
        ] {
            assert!(validate_request(&get(&path)).is_ok(), "{path}");
            assert!(validate_request(&request("POST", &path)).is_err(), "{path}");
        }
        for path in [
            format!("{root}?cursor=-1"),
            format!("{root}?limit=101"),
            format!("{root}?cursor=9223372036854775808"),
            format!("{root}?cursor=1&cursor=2"),
            format!("{root}?after=0"),
            format!("{root}/{profile}?limit=1"),
            format!("{root}/{profile}/readiness?probe=true"),
            format!("{root}/wpf_invalid/readiness"),
        ] {
            assert!(validate_request(&get(&path)).is_err(), "{path}");
        }
        let body = serde_json::json!({"profile_id":profile,"profile_revision":1,
            "code_target":{"repository":"example/library","host":"fixture","workspace_id":"ws-1"}});
        assert!(validate_start_preview(Some(&body)).is_ok());
        for field in ["profile_id", "profile_revision", "code_target"] {
            let mut invalid = body.clone();
            invalid.as_object_mut().unwrap().remove(field);
            assert!(validate_start_preview(Some(&invalid)).is_err(), "{field}");
        }
        for revision in [
            serde_json::json!(0),
            serde_json::json!(-1),
            serde_json::json!(true),
            serde_json::json!(1.5),
            serde_json::json!("1"),
        ] {
            let mut invalid = body.clone();
            invalid["profile_revision"] = revision;
            assert!(validate_start_preview(Some(&invalid)).is_err());
        }
    }

    #[test]
    fn work_attempt_reads_and_start_preview_use_exact_routes() {
        let ws = "ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let wi = "wi_bbbbbbbbbbbbbbbbbbbb";
        let att = "att_cccccccccccccccccccccccccccccccc";
        let attempts = format!("/pm/workspaces/{ws}/work/{wi}/attempts");
        let attempt = format!("/pm/workspaces/{ws}/work-attempts/{att}");
        let result = format!("/pm/workspaces/{ws}/work-attempts/{att}/result");
        let changes = format!("/pm/workspaces/{ws}/work-attempts/{att}/changes");
        let diff = format!("/pm/workspaces/{ws}/work-attempts/{att}/diff");
        let preview = format!("/pm/workspaces/{ws}/work/{wi}/start-preview");
        let sha = "0123456789abcdef0123456789abcdef01234567";
        for path in [
            attempts.clone(),
            format!("{attempts}?limit=100"),
            format!("{attempts}?after=0&limit=1"),
            format!("{attempts}?after=0100&limit=000000100"),
            attempt.clone(),
            result.clone(),
            format!("{result}?cursor=0&wait_ms=0"),
            format!("{result}?cursor=1&wait_ms=10000"),
            format!("{result}?wait_ms=000000000"),
            changes.clone(),
            format!("{changes}?max_commits=1&max_files=1"),
            format!("{changes}?max_commits=30&max_files=80"),
            diff.clone(),
            format!("{diff}?commit={sha}&path=src/main.txt"),
            format!("{diff}?commit={sha}&path=src/./nested.txt"),
            format!("{diff}?path=README.md"),
        ] {
            assert!(validate_request(&get(&path)).is_ok(), "{path}");
            assert!(validate_request(&request("POST", &path)).is_err(), "{path}");
        }
        let mut keyed = get(&attempts);
        keyed.idempotency_key = Some("unexpected".into());
        assert!(validate_request(&keyed).is_err());
        for path in [
            format!("/pm/workspaces/wk_{}/work/{wi}/attempts", "a".repeat(32)),
            format!(
                "/pm/workspaces/{}/work/{wi}/attempts",
                "ws_".to_string() + &"a".repeat(31)
            ),
            format!("/pm/workspaces/{ws}/work/wj_{}/attempts", "b".repeat(20)),
            format!(
                "/pm/workspaces/{ws}/work/{}/attempts",
                "wi_".to_string() + &"b".repeat(19)
            ),
            format!("/pm/workspaces/{ws}/work/{wi}/attempts/extra"),
            format!("/pm/workspaces/{ws}/work/attempts"),
            format!("/pm/workspaces/{ws}/../work/{wi}/attempts"),
            format!("/pm/workspaces/{ws}/work/%77i_{}/attempts", "b".repeat(20)),
            format!("{attempts}?cursor=1"),
            format!("{attempts}?limit=0"),
            format!("{attempts}?limit=101"),
            format!("{attempts}?limit=100&limit=100"),
            format!("{attempts}?after=9223372036854775808"),
            format!("{attempts}?after=-1"),
            format!("{attempts}?unknown=1"),
            format!(
                "/pm/workspaces/{ws}/work-attempts/at_{}/result",
                "c".repeat(32)
            ),
            format!(
                "/pm/workspaces/{ws}/work-attempts/{}/result",
                "att_".to_string() + &"c".repeat(31)
            ),
            format!("{result}/extra"),
            format!("/pm/workspaces/{ws}/work-attempts/result"),
            format!("{result}?limit=1"),
            format!("{result}?wait_ms=10001"),
            format!("{result}?cursor=-1"),
            format!("{result}?wait_ms=0000000000"),
            format!("{attempt}?limit=1"),
            format!("{attempt}?wait_ms=0"),
            format!("{attempt}/extra"),
            format!("/pm/workspaces/{ws}/work-attempts/{att}/result-sources"),
            format!("{changes}/extra"),
            format!("{changes}?max_commits=0"),
            format!("{changes}?max_commits=31"),
            format!("{changes}?max_files=0"),
            format!("{changes}?max_files=81"),
            format!("{changes}?after=1"),
            format!("{changes}?max_commits=30&max_commits=1"),
            format!("{diff}/extra"),
            format!("{diff}?commit={}", "A".repeat(40)),
            format!("{diff}?commit={}", "a".repeat(39)),
            format!("{diff}?commit={sha}&path=/etc/passwd"),
            format!("{diff}?commit={sha}&path=src/../../secret"),
            format!("{diff}?commit={sha}&path="),
            format!("{diff}?limit=1"),
            format!("{preview}?limit=1"),
        ] {
            assert!(validate_request(&get(&path)).is_err(), "{path}");
        }
        assert!(validate_request(&request("GET", &preview)).is_err());
        let mut posted = request("POST", &preview);
        for body in [
            serde_json::json!({}),
            serde_json::json!({"instruction": "Ship the login fix\nwith tests", "delivery": "pull_request"}),
            serde_json::json!({"instruction": "Ship it", "delivery": "reviewable_changes", "requirement_ids": ["login"], "code_target": {"repository": "octo/repo", "host": "fixture", "workspace_id": "folder"}}),
            serde_json::json!({"code_target": {"source_ref": "refs/heads/main"}}),
        ] {
            posted.body = Some(body);
            posted.idempotency_key = None;
            posted.path = preview.clone();
            assert!(
                validate_request(&posted).is_ok(),
                "{}",
                posted.body.as_ref().unwrap()
            );
        }
        for body in [
            serde_json::json!({"agent": "claude"}),
            serde_json::json!({"delivery": "branch"}),
            serde_json::json!({"delivery": 1}),
            serde_json::json!({"instruction": ""}),
            serde_json::json!({"instruction": "\n"}),
            serde_json::json!({"instruction": "x".repeat(4001)}),
            serde_json::json!({"instruction": 1}),
            serde_json::json!({"instruction": null}),
            serde_json::json!({"requirement_ids": []}),
            serde_json::json!({"requirement_ids": ["Login"]}),
            serde_json::json!({"requirement_ids": ["login", "login"]}),
            serde_json::json!({"requirement_ids": (0..51).map(|n| format!("item-{n}")).collect::<Vec<_>>()}),
            serde_json::json!({"code_target": {"repository": "https://github.com/octo/repo"}}),
            serde_json::json!({"code_target": {"host": "bad/host"}}),
            serde_json::json!({"code_target": {"workspace_id": ""}}),
            serde_json::json!({"code_target": {"source_ref": "main"}}),
            serde_json::json!({"code_target": {"source_ref": "refs/heads/../main"}}),
            serde_json::json!({"code_target": {"extra": "no"}}),
            serde_json::json!([]),
        ] {
            posted.body = Some(body);
            assert!(
                validate_request(&posted).is_err(),
                "{}",
                posted.body.as_ref().unwrap()
            );
        }
        posted.body = None;
        assert!(validate_request(&posted).is_err());
        posted.body = Some(serde_json::json!({}));
        posted.idempotency_key = Some("unexpected".into());
        assert!(validate_request(&posted).is_err());
        posted.idempotency_key = None;
        posted.path = format!("{preview}?limit=1");
        assert!(validate_request(&posted).is_err());
    }

    #[test]
    fn adopt_and_start_preview_has_a_bounded_read_only_contract() {
        let ws = format!("ws_{}", "a".repeat(32));
        let thread = format!("pmt_{}", "b".repeat(32));
        let path = format!("/pm/workspaces/{ws}/threads/{thread}/adopt-and-start-preview");
        let body = serde_json::json!({
            "proposal_id": format!("pmcp_{}", "c".repeat(32)),
            "review": {"title":"Login repair", "rows":[{"key":"login","text":"Prevent duplicate login","status":"user_confirmed","include":true}],
                "requirements":[{"id":"login","source_key":"login","acceptance_keys":["login"],"priority":"must","depends_on":[]}], "removed_requirement_ids":[]},
            "rationale":"Adopt the reviewed repair",
            "works":[{"work_item_id":format!("wi_{}", "d".repeat(20)),"requirement_ids":["login"]}]
        });
        let mut input = request("POST", &path);
        input.body = Some(body.clone());
        assert!(validate_request(&input).is_ok());
        for bad_path in [
            format!("{path}?limit=1"),
            path.replace(&thread, "pmt_bad"),
            format!("{path}/extra"),
        ] {
            input.path = bad_path;
            assert!(validate_request(&input).is_err());
        }
        input.path = path.clone();
        input.idempotency_key = Some("preview-is-not-an-operation".into());
        assert!(validate_request(&input).is_err());
        input.idempotency_key = None;
        for bad in [
            serde_json::json!({}),
            serde_json::json!({"url":"https://example.invalid"}),
        ] {
            input.body = Some(bad);
            assert!(validate_request(&input).is_err());
        }
        for field in ["command", "headers", "profile_id", "preconditions"] {
            let mut changed = body.clone();
            changed["works"][0][field] = serde_json::json!("forbidden");
            input.body = Some(changed);
            assert!(validate_request(&input).is_err());
        }
        for count in [0, 2, 11] {
            let mut changed = body.clone();
            changed["works"] = serde_json::json!(vec![body["works"][0].clone(); count]);
            input.body = Some(changed);
            assert!(validate_request(&input).is_err());
        }
        let mut changed = body.clone();
        changed["review"]["rows"][0]["text"] = serde_json::json!("x".repeat(4001));
        input.body = Some(changed);
        assert!(validate_request(&input).is_err());
        assert!(validate_request(&request("GET", &path)).is_err());
    }

    #[test]
    fn work_follow_up_inputs_are_bounded_reads_only() {
        let route = "/pm/workspaces/ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/work/wi_bbbbbbbbbbbbbbbbbbbb/inputs";
        for suffix in [
            "",
            "?limit=1",
            "?limit=100&after=0",
            "?after=9223372036854775807",
            "?limit=50&attempt_id=att_cccccccccccccccccccccccccccccccc",
        ] {
            let path = format!("{route}{suffix}");
            assert!(validate_request(&get(&path)).is_ok(), "{path}");
            assert!(validate_request(&request("POST", &path)).is_err(), "{path}");
        }
        for suffix in [
            "/extra",
            "?limit=0",
            "?limit=101",
            "?limit=1&limit=2",
            "?after=-1",
            "?after=9223372036854775808",
            "?cursor=1",
            "?wait_ms=0",
            "?after=1.5",
            "?after=",
            "?attempt_id=",
            "?attempt_id=att_short",
            "?attempt_id=att_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
            "?attempt_id=att_cccccccccccccccccccccccccccccccc&attempt_id=att_dddddddddddddddddddddddddddddddd",
        ] {
            let path = format!("{route}{suffix}");
            assert!(validate_request(&get(&path)).is_err(), "{path}");
        }
        for path in [
            route.replace("ws_", "wk_"),
            route.replace("wi_", "wj_"),
            route.replace("/inputs", "/input"),
            route.replace("/inputs", "/inputs/"),
            route.replace("/work/", "/work/../"),
        ] {
            assert!(validate_request(&get(&path)).is_err(), "{path}");
        }
        let mut keyed = get(route);
        keyed.idempotency_key = Some("not-an-operation".into());
        assert!(validate_request(&keyed).is_err());
        let outside_inputs = format!(
            "{}?attempt_id=att_cccccccccccccccccccccccccccccccc",
            route.replace("/inputs", "/attempts")
        );
        assert!(validate_request(&get(&outside_inputs)).is_err());
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
    fn release_baseline_sources_require_explicit_bounded_queries() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111";
        let chosen =
            "project_binding_id=pmpb_22222222222222222222222222222222&repository=example%2Fproduct";
        for suffix in [
            "/release-baseline-sources?after=0&limit=20".to_string(),
            format!("/release-baseline-sources/github-releases?{chosen}&page=1&limit=20"),
            format!("/release-baseline-sources/deployments?{chosen}&after=0&limit=20"),
            format!("/release-baseline-preview?{chosen}&kind=github_release&release_id=41&maintenance_branch=support%2F1.x"),
            format!("/release-baseline-preview?{chosen}&kind=deployment&deployment_id=dep_33333333333333333333333333333333"),
        ] {
            assert!(validate_request(&get(&format!("{root}{suffix}"))).is_ok(), "{suffix}");
        }
        for suffix in [
            "/release-baseline-sources?limit=21".to_string(),
            "/release-baseline-sources?after=".into(),
            format!("/release-baseline-sources/github-releases?{chosen}&page=0"),
            format!("/release-baseline-sources/github-releases?{chosen}&page=10001"),
            format!("/release-baseline-sources/github-releases?{chosen}&after=0"),
            format!("/release-baseline-sources/deployments?{chosen}&page=1"),
            format!("/release-baseline-preview?{chosen}&kind=github_release&release_id="),
            format!("/release-baseline-preview?{chosen}&kind=github_release&release_id=9223372036854775808"),
            format!("/release-baseline-preview?{chosen}&kind=github_release&release_id=1&release_id=2"),
            format!("/release-baseline-preview?{chosen}&kind=package_registry"),
            format!("/release-baseline-preview?{chosen}&kind=github_release&release_id=41&channel=stable"),
            format!("/release-baseline-preview?{chosen}&kind=github_release&release_id=41&maintenance_branch="),
            "/release-baseline-preview?project_binding_id=pmpb_bad".into(),
        ] {
            assert!(validate_request(&get(&format!("{root}{suffix}"))).is_err(), "{suffix}");
        }
    }

    #[test]
    fn release_baseline_reads_only_allow_exact_queries() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/release-baselines";
        let baseline = "pmrb_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=0&limit=100".into(),
            "/current".into(),
            format!("/{baseline}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=",
            "?after=-1",
            "?after=1000000000",
            "?after=0&after=1",
            "?limit=",
            "?limit=0",
            "?limit=101",
            "?limit=1&limit=2",
            "?actor=",
            "?publish=true",
            "/latest",
            "/pmrb_bad",
            "/current?after=0",
            "/current?actor=",
            "/current/publish",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        for suffix in ["?limit=1", "?actor=", "/publish", "/maintenance"] {
            assert!(
                validate_request(&get(&format!("{root}/{baseline}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        let mut keyed = get(root);
        keyed.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&keyed).is_err());
    }

    #[test]
    fn workload_reads_allow_only_fixed_ids_and_bounded_64_bit_cursors() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/workload-snapshots";
        let snapshot = "pmwl_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=0&limit=100".into(),
            "?after=1000000000".into(),
            "?after=9223372036854775807".into(),
            "/current".into(),
            format!("/{snapshot}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=",
            "?after=-1",
            "?after=9223372036854775808",
            "?after=0&after=1",
            "?limit=",
            "?limit=0",
            "?limit=101",
            "?limit=1&limit=2",
            "?actor=",
            "?idle=true",
            "/latest",
            "/pmwl_bad",
            "/current?after=0",
            "/current/capture",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        for suffix in ["?limit=1", "?actor=", "/capture", "/maintenance"] {
            assert!(
                validate_request(&get(&format!("{root}/{snapshot}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&request("POST", root)).is_err());
        let mut keyed = get(root);
        keyed.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&keyed).is_err());
    }

    #[test]
    fn work_delivery_reads_are_fixed_bounded_and_read_only() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/work/wi_22222222222222222222/deliveries";
        for suffix in ["", "?after=9223372036854775807&limit=100"] {
            assert!(validate_request(&get(&format!("{root}{suffix}"))).is_ok());
        }
        for suffix in [
            "?after=9223372036854775808",
            "?after=-1",
            "?after=",
            "?after=1&after=2",
            "?limit=0",
            "?limit=101",
            "?work_item_id=other",
            "?refresh=true",
            "/start",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&request("POST", root)).is_err());
        let mut keyed = get(root);
        keyed.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&keyed).is_err());
    }

    #[test]
    fn batch_and_finding_reads_never_allow_dispatch_or_provider_refresh() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111";
        let batch = "pmbt_22222222222222222222222222222222";
        for suffix in [
            "/work-batches".into(),
            "/work-batches?after=9223372036854775807&limit=100".into(),
            format!("/work-batches/{batch}"),
            "/maintenance-findings/current".into(),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
            assert!(validate_request(&request("POST", &format!("{root}{suffix}"))).is_err());
        }
        for suffix in [
            "/work-batches?after=9223372036854775808".into(),
            "/work-batches?limit=101".into(),
            "/work-batches?after=0&after=1".into(),
            "/work-batches?start=true".into(),
            "/work-batches/current".into(),
            format!("/work-batches/{batch}?limit=1"),
            "/maintenance-findings".into(),
            "/maintenance-findings/current?refresh=true".into(),
            "/maintenance-findings/current?limit=1".into(),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
    }

    #[test]
    fn spec_materialization_reads_are_fixed_bounded_and_read_only() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/spec-materializations";
        let id = "pmsp_22222222222222222222222222222222";
        for suffix in [
            "".into(),
            "?after=9223372036854775807&limit=50".into(),
            "?package_id=pmwp_33333333333333333333333333333333".into(),
            format!("/{id}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=9223372036854775808".into(),
            "?limit=51".into(),
            "?after=".into(),
            "?package_id=bad".into(),
            "?package_id=".into(),
            "?materialize=true".into(),
            "?limit=1&limit=2".into(),
            "/current".into(),
            format!("/{id}?package_id=pmwp_33333333333333333333333333333333"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&request("POST", root)).is_err());
    }

    #[test]
    fn maintenance_review_reads_keep_current_detail_and_event_queries_separate() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/maintenance-reviews";
        let review = "pmrevw_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=9223372036854775807&limit=100".into(),
            "/current?history_limit=1".into(),
            format!("/{review}?history_limit=100"),
            format!("/{review}/events?after=9223372036854775807&limit=100"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=9223372036854775808".to_string(),
            "?limit=101".into(),
            "?prepare=true".into(),
            "/current?history_limit=0".into(),
            "/current?after=0".into(),
            "/current/events".into(),
            "/pmrevw_bad".into(),
            format!("/{review}?history_limit=1&history_limit=2"),
            format!("/{review}/events?history_limit=1"),
            format!("/{review}/complete"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&request("POST", root)).is_err());
    }

    #[test]
    fn maintenance_check_reads_preserve_exact_routes_and_scope() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111";
        for suffix in [
            "/maintenance-checks",
            "/maintenance-checks/current",
            "/maintenance-checks/pmck_22222222222222222222222222222222",
            "/maintenance-checks?after=9223372036854775807&limit=100",
            "/maintenance-schedule",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "/maintenance-checks?after=9223372036854775808",
            "/maintenance-checks?after=0&after=1",
            "/maintenance-checks?limit=101",
            "/maintenance-checks?run=true",
            "/maintenance-checks/current?limit=1",
            "/maintenance-checks/pmck_bad",
            "/maintenance-checks/pmck_22222222222222222222222222222222?after=0",
            "/maintenance-schedule?refresh=true",
            "/maintenance-schedule/run",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(
            validate_request(&request("POST", &format!("{root}/maintenance-schedule"))).is_err()
        );
    }

    #[test]
    fn github_observation_reads_allow_only_fixed_ids_and_bounded_64_bit_cursors() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/github-observations";
        let snapshot = "pmgh_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=0&limit=100".into(),
            "?after=1000000000".into(),
            "?after=9223372036854775807".into(),
            "/current".into(),
            format!("/{snapshot}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=",
            "?after=-1",
            "?after=9223372036854775808",
            "?after=0&after=1",
            "?limit=",
            "?limit=0",
            "?limit=101",
            "?limit=1&limit=2",
            "?actor=",
            "?idle=true",
            "/latest",
            "/pmgh_bad",
            "/current?after=0",
            "/current/capture",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        for suffix in ["?limit=1", "?actor=", "/capture", "/maintenance"] {
            assert!(
                validate_request(&get(&format!("{root}/{snapshot}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&request("POST", root)).is_err());
        let mut keyed = get(root);
        keyed.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&keyed).is_err());
    }

    #[test]
    fn maintenance_policy_reads_only_allow_exact_queries() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/maintenance-policies";
        let baseline = "pmmp_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=0&limit=100".into(),
            "/current".into(),
            format!("/{baseline}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=",
            "?after=-1",
            "?after=1000000000",
            "?after=0&after=1",
            "?limit=",
            "?limit=0",
            "?limit=101",
            "?limit=1&limit=2",
            "?actor=",
            "?publish=true",
            "/latest",
            "/pmmp_bad",
            "/current?after=0",
            "/current?actor=",
            "/current/publish",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        for suffix in ["?limit=1", "?actor=", "/publish", "/maintenance"] {
            assert!(
                validate_request(&get(&format!("{root}/{baseline}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        let mut keyed = get(root);
        keyed.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&keyed).is_err());
    }

    #[test]
    fn issue_mapping_reads_only_allow_exact_queries() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/issue-mappings";
        let mapping = "pmim_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=0&limit=100".into(),
            format!("/{mapping}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=",
            "?limit=",
            "?actor=",
            "?after=0&after=1",
            "?limit=&limit=1",
            "?after=-1",
            "?after=1000000000",
            "?limit=101",
            "?work_item_id=wi_33333333333333333333",
            "?execute=true",
            "?mapping_id=pmim_22222222222222222222222222222222",
            "/pmim_bad",
            "/latest",
            "/publish",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        for suffix in ["?after=0", "?limit=1", "?actor=", "/history", "/cancel"] {
            assert!(
                validate_request(&get(&format!("{root}/{mapping}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&request("POST", root)).is_err());
        let mut input = get(root);
        input.body = Some(serde_json::json!({"public_body":"not a read"}));
        assert!(validate_request(&input).is_err());
    }

    #[test]
    fn work_package_reads_only_allow_exact_queries() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/work-packages";
        let package = "pmwp_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=0&limit=100&work_item_id=wi_33333333333333333333".into(),
            format!("/{package}"),
            format!("/{package}/readiness"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=",
            "?limit=",
            "?actor=",
            "?work_item_id=",
            "?work_item_id=wi_bad",
            "?after=0&after=1",
            "?limit=&limit=1",
            "?after=-1",
            "?after=1000000000",
            "?limit=101",
            "?work_item_id=wi_33333333333333333333&work_item_id=wi_33333333333333333333",
            "?execute=true",
            "?package_id=pmwp_22222222222222222222222222222222",
            "/pmwp_bad",
            "/latest",
            "/readiness",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        for suffix in [
            "?limit=1",
            "?actor=",
            "/readiness?after=0",
            "/readiness?execute=true",
            "/dispatch",
        ] {
            assert!(
                validate_request(&get(&format!("{root}/{package}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&request("POST", root)).is_err());
        let mut input = get(root);
        input.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&input).is_err());
    }

    #[test]
    fn lifecycle_reads_have_exact_routes_and_bounded_cursors() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/lifecycle";
        let id = "pmlt_22222222222222222222222222222222";
        for path in [root.to_string(), format!("{root}/transitions/{id}")] {
            assert!(validate_request(&get(&path)).is_ok());
            assert!(validate_request(&get(&format!("{path}?limit=1"))).is_err());
        }
        for suffix in ["transitions", "inflight"] {
            let path = format!("{root}/{suffix}");
            for query in [
                "",
                "?after=0&limit=1",
                "?after=9223372036854775807&limit=100",
            ] {
                assert!(validate_request(&get(&format!("{path}{query}"))).is_ok());
            }
            for query in [
                "?after=",
                "?after=-1",
                "?after=1.0",
                "?after=9223372036854775808",
                "?after=0&after=1",
                "?limit=",
                "?limit=0",
                "?limit=101",
                "?limit=1&limit=2",
                "?actor=owner",
                "?stage=closed",
                "?workspace_id=foreign",
            ] {
                assert!(
                    validate_request(&get(&format!("{path}{query}"))).is_err(),
                    "{query}"
                );
            }
        }
        assert!(validate_request(&get(&format!("{root}/inflight?transition_id={id}"))).is_ok());
        for path in [
            format!("{root}/transitions?transition_id={id}"),
            format!("{root}/inflight?transition_id="),
            format!("{root}/inflight?transition_id=pmlt_bad"),
            format!("{root}/inflight?transition_id={id}&transition_id={id}"),
            format!("{root}/transitions/latest"),
            format!("{root}/transitions/pmlt_bad"),
            format!("{root}/transitions/{id}/inflight"),
            format!("{root}/pause"),
        ] {
            assert!(validate_request(&get(&path)).is_err(), "{path}");
        }
        assert!(validate_request(&request("POST", root)).is_err());
        let mut input = get(root);
        input.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&input).is_err());
        input.idempotency_key = None;
        input.body = Some(serde_json::json!({"stage":"closed"}));
        assert!(validate_request(&input).is_err());
    }

    #[test]
    fn brief_export_reads_only_allow_fixed_snapshot_formats() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/snapshots";
        let fixed = format!("{root}/pmsnap_22222222222222222222222222222222/brief-export");
        assert!(validate_request(&get(&fixed)).is_ok());
        for format in ["markdown", "html", "svg"] {
            for locale in ["en", "zh-TW"] {
                assert!(validate_request(&get(&format!(
                    "{fixed}?format={format}&locale={locale}"
                )))
                .is_ok());
            }
        }
        for query in [
            "?format=",
            "?locale=",
            "?format=pdf",
            "?format=HTML",
            "?locale=zh",
            "?format=html&format=svg",
            "?locale=&locale=en",
            "?actor=",
            "?limit=1",
            "?format=html&path=report.html",
            "?url=https://example.invalid",
            "?content=untrusted",
        ] {
            assert!(
                validate_request(&get(&format!("{fixed}{query}"))).is_err(),
                "{query}"
            );
        }
        for path in [
            format!("{root}/latest/brief-export"),
            format!("{root}/brief-export"),
            format!("{root}/pmsnap_bad/brief-export"),
            format!("{fixed}/content"),
            format!("{root}/pmsnap_22222222222222222222222222222222?format=html"),
        ] {
            assert!(validate_request(&get(&path)).is_err(), "{path}");
        }
        assert!(validate_request(&request("POST", &fixed)).is_err());
        let mut input = get(&fixed);
        input.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&input).is_err());
        input.idempotency_key = None;
        input.body = Some(serde_json::json!({"format": "html"}));
        assert!(validate_request(&input).is_err());
    }

    #[test]
    fn conversation_proposals_keep_exact_read_queries() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/conversation-proposals";
        let proposal = "pmcp_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=9223372036854775807&limit=100&thread_id=pmt_33333333333333333333333333333333"
                .into(),
            format!("/{proposal}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?thread_id=pmt_bad",
            "?thread_id=",
            "?after=9223372036854775808",
            "?after=0&after=1",
            "?actor=x",
            "?execute=true",
            "/latest",
            "/pmcp_bad",
            "?limit=101",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&get(&format!("{root}/{proposal}?limit=1"))).is_err());
        assert!(validate_request(&request("POST", root)).is_err());
    }

    #[test]
    fn follow_up_assessment_reads_only_allow_exact_queries() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/follow-up-assessments";
        let assessment = "pmfa_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=0&limit=100&intake_id=pmfu_33333333333333333333333333333333".into(),
            format!("/{assessment}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=", "?limit=", "?intake_id=", "?intake_id=pmfu_bad", "?actor=",
            "?after=0&after=1", "?limit=&limit=1", "?after=-1", "?after=1000000000", "?limit=101",
            "?intake_id=pmfu_33333333333333333333333333333333&intake_id=pmfu_33333333333333333333333333333333",
            "?assessment_id=pmfa_22222222222222222222222222222222", "?execute=true",
            "/pmfa_bad", "/latest", "/analyze",
        ] {
            assert!(validate_request(&get(&format!("{root}{suffix}"))).is_err(), "{suffix}");
        }
        for suffix in [
            "?after=0", "?limit=1", "?actor=", "/history", "/raw", "/adopt",
        ] {
            assert!(
                validate_request(&get(&format!("{root}/{assessment}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&request("POST", root)).is_err());
        let mut input = get(root);
        input.idempotency_key = Some("not-a-write".into());
        assert!(validate_request(&input).is_err());
        input.idempotency_key = None;
        input.body = Some(serde_json::json!({"intake_id": "untrusted"}));
        assert!(validate_request(&input).is_err());
    }

    #[test]
    fn follow_up_reads_only_allow_exact_queries() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/follow-ups";
        let intake = "pmfu_22222222222222222222222222222222";
        for suffix in [
            "".to_string(),
            "?after=0&limit=100".into(),
            "/latest".into(),
            format!("/{intake}"),
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?after=",
            "?limit=",
            "?actor=",
            "?after=0&after=1",
            "?limit=&limit=1",
            "?after=-1",
            "?limit=101",
            "?thread_id=pmt_1",
            "/latest?after=0",
            "/latest?actor=",
            "/pmfu_bad",
            "/latest/history",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&get(&format!("{root}/{intake}?limit=1"))).is_err());
        assert!(validate_request(&request("POST", root)).is_err());
    }

    #[test]
    fn specification_reads_only_allow_exact_bounded_source_queries() {
        let workspace = "/pm/workspaces/ws_00000000000000000000000000000000";
        let source = format!(
            "{workspace}/spec-sources?repository=example/project&commit_sha={}",
            "a".repeat(40)
        );
        for path in [format!("{workspace}/spec-sources/capabilities"), source.clone(),
            format!("{source}&paths=openspec/config.yaml,openspec/changes/archive/specs/tasks.md,openspec/specs/identity/login/spec.md")] {
            assert!(validate_request(&get(&path)).is_ok(), "{path}");
        }
        for path in [
            format!("{workspace}/spec-sources"),
            format!("{workspace}/spec-sources/capabilities?repository=example/project"),
            format!("{source}&repository=other/project"),
            format!("{source}&actor=other"),
            format!("{source}&paths="),
            format!("{source}&paths=../secret"),
            format!("{source}&paths=openspec/../config.yaml"),
            format!("{source}&paths=openspec/config.yaml,openspec/config.yaml"),
            format!("{source}&paths=openspec/changes/archive/tasks.md"),
            format!("{source}&paths=openspec/changes/archive/specs/spec.md"),
            format!("{source}&commit_sha=HEAD"),
            format!("{source}&paths=openspec%5Cconfig.yaml"),
            format!("{workspace}/spec-sources/apply"),
            format!(
                "{source}&paths={}",
                (0..101)
                    .map(|i| format!("openspec/specs/c{i}/spec.md"))
                    .collect::<Vec<_>>()
                    .join(",")
            ),
        ] {
            assert!(validate_request(&get(&path)).is_err(), "{path}");
        }
        let mut write = get(&source);
        write.method = "POST".into();
        assert!(validate_request(&write).is_err());
    }

    #[test]
    fn overview_assessment_reads_are_exact_and_preview_is_read_only() {
        let root = "/pm/workspaces/ws_00000000000000000000000000000000/overview-assessments";
        let id = "pmoa_11111111111111111111111111111111";
        let preview = "/pm/workspaces/ws_00000000000000000000000000000000/threads/pmt_22222222222222222222222222222222/context-bundle";
        for suffix in ["".into(), "?limit=10&before=YWJj".into(), format!("/{id}")] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_ok(),
                "{suffix}"
            );
        }
        for suffix in [
            "?limit=0",
            "?limit=51",
            "?before=",
            "?before=x&before=y",
            "?after=1",
            "?actor=owner",
            "/latest",
            "/pmoa_bad",
        ] {
            assert!(
                validate_request(&get(&format!("{root}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&get(&format!("{root}/{id}?limit=1"))).is_err());
        assert!(validate_request(&request("POST", root)).is_err());
        assert!(validate_request(&get(&format!(
            "{preview}?kind=overview_assessment&max_chars=32000"
        )))
        .is_ok());
        for suffix in [
            "?kind=other",
            "?kind=",
            "?kind=overview_assessment&kind=overview_assessment",
            "?kind=overview_assessment&execute=true",
        ] {
            assert!(
                validate_request(&get(&format!("{preview}{suffix}"))).is_err(),
                "{suffix}"
            );
        }
        assert!(validate_request(&get(&format!("{root}?kind=overview_assessment"))).is_err());
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
            format!("{thread}/context-bundle?max_chars=64000"),
            format!("{thread}/context-bundle"),
            format!("{workspace}/capabilities"),
            format!("{workspace}/runs?limit=100&after=pmr_22222222222222222222222222222222&thread_id=pmt_11111111111111111111111111111111"),
            format!("{workspace}/runs/pmr_22222222222222222222222222222222"),
            format!("{workspace}/plans?limit=100&after=0"),
            format!("{workspace}/projects?limit=100&after=0"),
            format!("{workspace}/plan-comparison?from_revision_id=pmrev_33333333333333333333333333333333&to_revision_id=pmrev_44444444444444444444444444444444&binding_ids=pmwb_55555555555555555555555555555555&journal_cursor=0"),
            format!("{workspace}/governance?limit=100&after=0&kind=drift"),
            format!("{workspace}/governance/pmgov_88888888888888888888888888888888"),
            format!("{workspace}/governance/pmgov_88888888888888888888888888888888/history?limit=1&after=0"),
            format!("{workspace}/kanban?limit=100&after=0&project_id=prj_55555555555555555555"),
            format!("{workspace}/kanban/wi_55555555555555555555"),
            format!("{workspace}/meetings?limit=1&after=0&sprint_id=pms_88888888888888888888888888888888"),
            format!("{workspace}/meetings/pmmeet_88888888888888888888888888888888?history_limit=1"),
            format!("{workspace}/meetings/pmmeet_88888888888888888888888888888888/history/contexts?limit=1&after=0"),
            format!("{workspace}/meetings/pmmeet_88888888888888888888888888888888/history/follow_ups?limit=1&after=0"),
            format!("{workspace}/sprint-artifacts"),
            format!("{workspace}/sprint-artifacts/product_goal?limit=100&after=0"),
            format!("{workspace}/sprint-artifacts/product_goal/pmgoal_88888888888888888888888888888888"),
            format!("{workspace}/sprint-artifacts/dod/pmdod_88888888888888888888888888888888"),
            format!("{workspace}/sprint-artifacts/backlog/pmbkl_88888888888888888888888888888888"),
            format!("{workspace}/sprints?after=0&limit=100"),
            format!("{workspace}/sprints/pms_88888888888888888888888888888888?history_limit=1"),
            format!("{workspace}/sprints/pms_88888888888888888888888888888888/history/events?limit=1&after=0"),
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
            format!("{thread}/context-bundle?limit=1"),
            format!("{thread}/context-bundle?max_chars=1999"),
            format!("{thread}/context-bundle?max_chars=64001"),
            format!("{thread}/context-bundle?max_chars=32000&max_chars=2000"),
            format!("{thread}/context-bundle?before_message=1"),
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
            format!("{workspace}/governance?actor=other"),
            format!("{workspace}/governance?kind=unknown"),
            format!("{workspace}/governance?kind=risk&kind=change"),
            format!("{workspace}/governance/pmgov_88888888888888888888888888888888?history_limit=1"),
            format!("{workspace}/governance/pmgov_88888888888888888888888888888888/history?kind=risk"),
            format!("{workspace}/kanban?actor=other"),
            format!("{workspace}/kanban?limit=0"),
            format!("{workspace}/kanban?project_id=../../etc"),
            format!("{workspace}/kanban/wi_55555555555555555555?limit=1"),
            format!("{workspace}/meetings?kind=review"),
            format!("{workspace}/meetings?sprint_id=pmmeet_88888888888888888888888888888888"),
            format!("{workspace}/meetings?after=0&after=1"),
            format!("{workspace}/meetings/pmmeet_88888888888888888888888888888888?history_limit=0"),
            format!("{workspace}/meetings/pmmeet_88888888888888888888888888888888/history/unknown"),
            format!("{workspace}/meetings/pmmeet_88888888888888888888888888888888/history/notes?history_limit=1"),
            format!("{workspace}/sprint-artifacts?limit=1"),
            format!("{workspace}/sprint-artifacts/unknown"),
            format!("{workspace}/sprint-artifacts/product_goal/pmdod_88888888888888888888888888888888"),
            format!("{workspace}/sprint-artifacts/dod?after=-1"),
            format!("{workspace}/sprint-artifacts/backlog?limit=0"),
            format!("{workspace}/sprints?history_limit=1"),
            format!("{workspace}/sprints/pms_88888888888888888888888888888888?limit=1"),
            format!("{workspace}/sprints/pms_88888888888888888888888888888888?history_limit=101"),
            format!("{workspace}/sprints/pms_88888888888888888888888888888888?actor=other"),
            format!("{workspace}/sprints/pms_88888888888888888888888888888888/history/unknown"),
            format!("{workspace}/sprints/pms_88888888888888888888888888888888/history/events?after=0&after=1"),
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
    fn recovery_launch(
        endpoint: &str,
        server_id: &str,
        principal_id: &str,
    ) -> crate::managed::Launch {
        serde_json::from_value(serde_json::json!({
            "protocol": 1, "runtime_version": "fixture-1", "endpoint": endpoint,
            "actor": "fixture-operator", "token": "replacement-fixture-token",
            "server_id": server_id, "principal_id": principal_id,
        }))
        .unwrap()
    }
    fn recovery_bridge() -> Bridge {
        Bridge::managed(
            Path::new("unused-fixture-config"),
            config("http://127.0.0.1:9/"),
            Zeroizing::new("old-fixture-token".into()),
            Identity {
                server_id: "fixture-server".into(),
                principal_id: "fixture-principal".into(),
            },
        )
    }
    #[tokio::test]
    async fn startup_launch_is_verified_and_installed_in_the_same_bridge() {
        let root = tempfile::tempdir().unwrap();
        let bridge = Arc::new(Bridge::role_pending(root.path()));
        let reader = bridge.clone();
        assert!(!reader.status().configuration_setup);
        let (endpoint, requests, handle) = server(vec![
            json_response(&capabilities("fixture-server", "fixture-principal", true)),
            managed_bootstrap_response("fixture-server", "fixture-principal"),
        ]);
        bridge
            .install_startup_launch(
                0,
                Some(recovery_launch(
                    &endpoint,
                    "fixture-server",
                    "fixture-principal",
                )),
            )
            .await
            .unwrap();
        assert!(reader.status().connected);
        assert_eq!(reader.status().configuration_source, "managed");
        assert_eq!(reader.status().endpoint.as_deref(), Some(endpoint.as_str()));
        assert!(reader.state.lock().unwrap().recovery_enabled);
        for _ in 0..2 {
            assert!(requests.recv().unwrap().starts_with("GET "));
        }
        handle.join().unwrap();
    }
    #[tokio::test]
    async fn startup_launch_refuses_bootstrap_identity_mismatch_without_publishing() {
        let root = tempfile::tempdir().unwrap();
        let bridge = Bridge::role_pending(root.path());
        let (endpoint, requests, handle) = server(vec![
            json_response(&capabilities("fixture-server", "fixture-principal", true)),
            managed_bootstrap_response("fixture-server", "another-principal"),
        ]);
        assert!(bridge
            .install_startup_launch(
                0,
                Some(recovery_launch(
                    &endpoint,
                    "fixture-server",
                    "fixture-principal"
                ))
            )
            .await
            .is_err());
        assert!(!bridge.status().connected);
        assert!(bridge.status().endpoint.is_none());
        assert!(!bridge.state.lock().unwrap().recovery_enabled);
        for _ in 0..2 {
            assert!(requests.recv().unwrap().starts_with("GET "));
        }
        handle.join().unwrap();
    }

    #[tokio::test]
    async fn startup_transition_refuses_stale_generation_and_appearing_external_configuration() {
        let root = tempfile::tempdir().unwrap();
        let bridge = Bridge::role_pending(root.path());
        bridge.disconnect();
        assert!(bridge.install_startup_launch(0, None).await.is_err());
        assert!(!bridge.status().configuration_setup);
        let bridge = Bridge::role_pending(root.path());
        std::fs::write(
            root.path().join("central.json"),
            b"preserve this configuration",
        )
        .unwrap();
        assert!(bridge
            .install_startup_launch(
                0,
                Some(recovery_launch(
                    "http://127.0.0.1:1/",
                    "fixture-server",
                    "fixture-principal"
                ))
            )
            .await
            .is_err());
        assert_eq!(
            std::fs::read(root.path().join("central.json")).unwrap(),
            b"preserve this configuration"
        );
    }
    #[tokio::test]
    async fn startup_client_transition_only_enables_the_existing_configuration_entry() {
        let root = tempfile::tempdir().unwrap();
        let bridge = Bridge::role_pending(root.path());
        bridge.install_startup_launch(0, None).await.unwrap();
        assert_eq!(bridge.status().configuration_source, "external");
        assert!(bridge.status().configuration_setup);
        assert!(!bridge.status().credential_available);
        assert!(!bridge.state.lock().unwrap().recovery_enabled);
        assert!(!root.path().join("central.json").exists());
    }
    #[tokio::test]
    async fn managed_recovery_rebinds_endpoint_and_token_only_after_identity_verification() {
        let (endpoint, requests, handle) = server(vec![
            json_response(&capabilities("fixture-server", "fixture-principal", true)),
            managed_bootstrap_response("fixture-server", "fixture-principal"),
            json_response("{\"events\":[]}"),
        ]);
        let bridge = recovery_bridge();
        let old_generation = bridge.state.lock().unwrap().generation;
        bridge
            .rebind_managed(
                old_generation,
                recovery_launch(&endpoint, "fixture-server", "fixture-principal"),
            )
            .await
            .unwrap();
        assert_eq!(bridge.status().endpoint.as_deref(), Some(endpoint.as_str()));
        assert!(bridge.status().connected);
        assert_eq!(bridge.status().managed_rebind, Some(1));
        assert!(bridge.state.lock().unwrap().generation > old_generation);
        bridge
            .request(get("/events?after=0&limit=100"))
            .await
            .unwrap();
        for _ in 0..3 {
            let request = requests.recv().unwrap();
            assert!(request.contains("Bearer replacement-fixture-token"));
            assert!(!request.contains("old-fixture-token"));
            assert!(request.starts_with("GET "));
        }
        handle.join().unwrap();
    }
    #[tokio::test]
    async fn managed_recovery_refuses_changed_launch_identity_before_any_network() {
        for (server_id, principal_id) in [
            ("another-server", "fixture-principal"),
            ("fixture-server", "another-principal"),
        ] {
            let bridge = recovery_bridge();
            let error = bridge
                .rebind_managed(
                    0,
                    recovery_launch("http://127.0.0.1:1/", server_id, principal_id),
                )
                .await
                .unwrap_err();
            assert_eq!(error, "MANAGED_RECOVERY_IDENTITY_CHANGED");
            assert_eq!(
                bridge.status().endpoint.as_deref(),
                Some("http://127.0.0.1:9/")
            );
            assert!(!bridge.status().connected);
        }
    }
    #[tokio::test]
    async fn managed_recovery_refuses_changed_live_bootstrap_identity() {
        let (endpoint, _, handle) = server(vec![
            json_response(&capabilities("another-server", "fixture-principal", true)),
            managed_bootstrap_response("another-server", "fixture-principal"),
        ]);
        let bridge = recovery_bridge();
        assert_eq!(
            bridge
                .rebind_managed(
                    0,
                    recovery_launch(&endpoint, "fixture-server", "fixture-principal")
                )
                .await
                .unwrap_err(),
            "MANAGED_RECOVERY_IDENTITY_CHANGED"
        );
        assert_eq!(
            bridge.status().endpoint.as_deref(),
            Some("http://127.0.0.1:9/")
        );
        assert!(!bridge.status().connected);
        handle.join().unwrap();
    }
    #[test]
    fn stale_recovery_hold_cannot_clear_a_new_binding() {
        let bridge = recovery_bridge();
        bridge.state.lock().unwrap().generation = 1;
        let before = serde_json::to_value(bridge.status()).unwrap();
        bridge.managed_hold(Some(0));
        assert_eq!(serde_json::to_value(bridge.status()).unwrap(), before);
        assert!(bridge.state.lock().unwrap().recovery_enabled);
    }
    #[test]
    fn holding_the_local_service_preserves_both_role_external_binding() {
        let external = Bridge::new(
            Ok(config("http://127.0.0.1:9/")),
            Zeroizing::new("fixture-token".into()),
        );
        let before = serde_json::to_value(external.status()).unwrap();
        external.managed_hold(None);
        assert_eq!(serde_json::to_value(external.status()).unwrap(), before);
        let local = recovery_bridge();
        local.managed_hold(None);
        let status = local.status();
        assert!(!status.connected);
        assert!(status.error.is_none());
        assert!(status.managed_recovery.is_none());
        assert!(!local.state.lock().unwrap().recovery_enabled);
    }
    #[tokio::test]
    async fn external_mode_and_explicit_disconnect_never_probe_or_rebind() {
        let external = Bridge::new(
            Ok(config("http://127.0.0.1:9/")),
            Zeroizing::new("fixture-token".into()),
        );
        assert!(external.managed_probe().await.is_none());
        assert_eq!(
            external
                .rebind_managed(
                    0,
                    recovery_launch("http://127.0.0.1:1/", "fixture-server", "fixture-principal")
                )
                .await
                .unwrap_err(),
            "MANAGED_RECOVERY_CANCELLED"
        );
        let managed = recovery_bridge();
        managed.disconnect();
        assert!(managed.managed_probe().await.is_none());
        assert_eq!(
            managed
                .rebind_managed(
                    0,
                    recovery_launch("http://127.0.0.1:1/", "fixture-server", "fixture-principal")
                )
                .await
                .unwrap_err(),
            "MANAGED_RECOVERY_CANCELLED"
        );
    }
    #[tokio::test]
    async fn identity_failure_stops_probes_and_fences_in_flight_reads() {
        let bridge = recovery_bridge();
        bridge.recovery_state(0, Some("MANAGED_RECOVERY_IDENTITY_CHANGED".into()));
        assert!(bridge.managed_probe().await.is_none());
        assert!(bridge.unchanged(0).is_err());
        assert!(!bridge.status().connected);
        assert_eq!(
            bridge.status().managed_recovery.as_deref(),
            Some("MANAGED_RECOVERY_IDENTITY_CHANGED")
        );
    }

    #[tokio::test]
    async fn failed_recovery_candidate_preserves_original_binding_and_reports_bounded_details() {
        let bridge = recovery_bridge();
        assert_eq!(
            bridge
                .rebind_managed(
                    0,
                    recovery_launch("http://127.0.0.1:1/", "fixture-server", "fixture-principal")
                )
                .await
                .unwrap_err(),
            "MANAGED_RECOVERY_VERIFICATION_FAILED"
        );
        bridge.recovery_state(0, Some("MANAGED_RECOVERY_VERIFICATION_FAILED".into()));
        assert_eq!(
            bridge.status().managed_recovery.as_deref(),
            Some("MANAGED_RECOVERY_VERIFICATION_FAILED")
        );
        assert_eq!(
            bridge.status().endpoint.as_deref(),
            Some("http://127.0.0.1:9/")
        );
    }

    // Producer-pinned by tests/test_api_v1_bootstrap_golden.py. Capabilities
    // uses the same handler as bootstrap.capabilities (ApiV1.bootstrap).
    fn bootstrap_document(server_id: &str, principal_id: &str, managed: bool) -> Value {
        let mut value: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/managed-bootstrap.json"
        ))
        .unwrap();
        let identity = serde_json::json!({"server_id": server_id, "principal_id": principal_id});
        value["sync"]["server_id"] = identity["server_id"].clone();
        value["sync"]["principal_id"] = identity["principal_id"].clone();
        value["capabilities"]["identity"] = identity.clone();
        value["capabilities"]["desktop_identity"] = identity;
        if !managed {
            // Exactly the external derivation in the Python producer golden test.
            let caps = value["capabilities"].as_object_mut().unwrap();
            caps.remove("desktop_identity");
            caps.remove("managed_installation");
        }
        value
    }

    fn capabilities(server_id: &str, principal_id: &str, managed: bool) -> String {
        bootstrap_document(server_id, principal_id, managed)["capabilities"].to_string()
    }

    fn actor_document(actor: &str) -> Value {
        let mut value = bootstrap_document("fixture", "other-principal", false);
        value["capabilities"]["actor"] = Value::String(actor.into());
        value
    }

    fn bootstrap_response(server_id: &str, principal_id: &str) -> String {
        json_response(&bootstrap_document(server_id, principal_id, false).to_string())
    }

    fn managed_bootstrap_response(server_id: &str, principal_id: &str) -> String {
        json_response(&bootstrap_document(server_id, principal_id, true).to_string())
    }

    #[test]
    fn identity_variants_retain_the_complete_producer_contract() {
        for managed in [true, false] {
            let value = bootstrap_document("changed-server", "changed-principal", managed);
            let caps = &value["capabilities"];
            assert_eq!(value["sync"]["server_id"], caps["identity"]["server_id"]);
            assert_eq!(
                value["sync"]["principal_id"],
                caps["identity"]["principal_id"]
            );
            assert_eq!(caps.get("desktop_identity").is_some(), managed);
            assert_eq!(caps.get("managed_installation").is_some(), managed);
            if managed {
                assert_eq!(caps["desktop_identity"], caps["identity"]);
            }
            assert!(value["sync"]["checkpoint"]["token"].is_string());
            assert!(value["snapshot"].is_object());
            assert!(caps["features"].is_object());
        }
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
            json_response(&capabilities("fixture-server", "fixture-principal", false)),
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
                json_response(&capabilities("fixture", "principal", false)),
                bootstrap_response("fixture", "principal"),
                json_response(&capabilities("fixture", "principal", false)),
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
            json_response(&capabilities("fixture", "principal", false)),
            bootstrap_response("fixture", "principal"),
            json_response(&capabilities("fixture", "principal", false)),
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
    fn ahp_reads_use_only_fixed_routes_and_bounded_saved_selectors() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111";
        let observation = format!("pmah_{}", "2".repeat(32));
        for suffix in [
            "/ahp-adapters".into(),
            "/ahp-observations?after=9223372036854775807&limit=50".into(),
            "/ahp-observations/current".into(),
            format!("/ahp-observations/{observation}"),
        ] {
            let path = format!("{root}{suffix}");
            assert!(validate_request(&request("GET", &path)).is_ok(), "{path}");
            assert!(validate_request(&request("POST", &path)).is_err(), "{path}");
        }
        for suffix in [
            "/ahp-adapters?endpoint=ws://127.0.0.1".into(),
            "/ahp-adapters?adapter_id=local".into(),
            "/ahp-observations?limit=51".into(),
            "/ahp-observations?after=9223372036854775808".into(),
            "/ahp-observations?after=0&after=1".into(),
            "/ahp-observations/current?refresh=true".into(),
            "/ahp-observations/latest".into(),
            format!("/ahp-observations/{observation}?limit=1"),
            format!("/ahp-observations/{observation}/refresh"),
        ] {
            let path = format!("{root}{suffix}");
            assert!(validate_request(&request("GET", &path)).is_err(), "{path}");
        }
    }

    #[test]
    fn repair_and_quality_reads_have_exact_original_routes_and_bounds() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111";
        let repair = format!("pmrp_{}", "2".repeat(32));
        let review = format!("pmqr_{}", "3".repeat(32));
        for suffix in [
            "/maintenance-repairs?after=9223372036854775807&limit=50".into(),
            "/maintenance-repairs/current".into(),
            format!("/maintenance-repairs/{repair}"),
            "/quality-reviews?after=9223372036854775807&limit=100".into(),
            format!("/quality-reviews/{review}"),
            format!("/pr-publications/op_{}", "5".repeat(32)),
            format!("/executions/op_{}/verification-config", "5".repeat(32)),
            format!("/execution-verifications/op_{}", "5".repeat(32)),
            "/work/wi_44444444444444444444/quality?after=0&limit=100".into(),
        ] {
            let path = format!("{root}{suffix}");
            assert!(validate_request(&request("GET", &path)).is_ok(), "{path}");
            assert!(validate_request(&request("POST", &path)).is_err(), "{path}");
        }
        for suffix in [
            "/maintenance-repairs?limit=51".into(),
            "/maintenance-repairs?after=9223372036854775808".into(),
            "/quality-reviews?after=0&after=1".into(),
            "/quality-reviews?limit=101".into(),
            "/quality-reviews/current".into(),
            "/quality-reviews?qualified=true".into(),
            format!("/quality-reviews/{review}?limit=1"),
            format!("/pr-publications/op_{}?refresh=true", "5".repeat(32)),
            "/pr-publications".into(),
            "/execution-verifications".into(),
            format!(
                "/executions/op_{}/verification-config?refresh=true",
                "5".repeat(32)
            ),
            format!("/execution-verifications/op_{}?run=true", "5".repeat(32)),
            format!("/maintenance-repairs/{repair}?start=true"),
            "/work/wi_44444444444444444444/quality?candidate=latest".into(),
        ] {
            let path = format!("{root}{suffix}");
            assert!(validate_request(&request("GET", &path)).is_err(), "{path}");
        }
    }

    #[test]
    fn package_readiness_accepts_only_bounded_original_quality_selector_objects() {
        let root = "/pm/workspaces/ws_11111111111111111111111111111111/work-packages/pmwp_22222222222222222222222222222222";
        let query = |value: &str| {
            url::form_urlencoded::Serializer::new(String::new())
                .append_pair("quality_refs", value)
                .finish()
        };
        let selected = serde_json::json!({"requirement": "x".repeat(5000)}).to_string();
        let accepted = format!("{root}/readiness?{}", query(&selected));
        assert!(accepted.len() > 4096);
        assert!(validate_request(&request("GET", &accepted)).is_ok());
        assert!(validate_request(&request("POST", &accepted)).is_err());
        for value in [
            "null",
            "[]",
            "1",
            "{",
            &serde_json::json!({"r": "x".repeat(16_384)}).to_string(),
        ] {
            let path = format!("{root}/readiness?{}", query(value));
            assert!(validate_request(&request("GET", &path)).is_err());
        }
        for path in [
            format!("{root}/readiness?quality_refs=%7B%7D&quality_refs=%7B%7D"),
            format!("{root}?{}", query(&selected)),
            format!("{root}/readiness?quality_refs=%7B%7D&passed=true"),
        ] {
            assert!(validate_request(&request("GET", &path)).is_err());
        }
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
        // An unsupported contract must be refused before any operation is unlocked.
        let (endpoint, received, handle) = server(vec![json_response(
            &capabilities("fixture", "principal", false).replace("2026-10-08", "unsupported"),
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
        // A valid document for another actor must not unlock this binding.
        let (endpoint, received, handle) = server(vec![json_response(
            &actor_document("other-actor")["capabilities"].to_string(),
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
            json_response(&capabilities("fixture", "principal", false)),
            bootstrap_response("fixture", "principal"),
            json_response(&capabilities("fixture", "principal", false)),
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
    async fn native_work_reads_and_next_input_keep_the_exact_central_identity() {
        let ws = "ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        let wi = "wi_bbbbbbbbbbbbbbbbbbbb";
        let att = "att_cccccccccccccccccccccccccccccccc";
        let reads = [
            format!("/pm/workspaces/{ws}/work-attempts/{att}"),
            format!("/pm/workspaces/{ws}/work/{wi}/inputs?attempt_id={att}&limit=50"),
            format!("/pm/workspaces/{ws}/work-attempts/{att}/result"),
        ];
        let views = [
            serde_json::json!({"workspace_id":ws,"attempt":{"attempt_id":att,"work_item_id":wi}}),
            serde_json::json!({"workspace_id":ws,"work_item_id":wi,"attempt_id":att,"items":[]}),
            serde_json::json!({"contract":"pm-work-attempt-result/v1","workspace_id":ws,"attempt_id":att,"identity":{"work_item_id":wi}}),
        ];
        let mut responses = vec![
            json_response(&capabilities("fixture", "principal", false)),
            bootstrap_response("fixture", "principal"),
        ];
        responses.extend(views.iter().map(|view| json_response(&view.to_string())));
        responses.extend([
            json_response(&capabilities("fixture", "principal", false)),
            bootstrap_response("fixture", "principal"),
            json_response(r#"{"operation":{"operation_id":"op_fixture","status":"queued"}}"#),
        ]);
        let (endpoint, received, handle) = server(responses);
        let bridge = Bridge::new(
            Ok(config(&endpoint)),
            Zeroizing::new("fixture-token".into()),
        );
        bridge.connect().await.unwrap();
        for (path, view) in reads.iter().zip(&views) {
            assert_eq!(bridge.request(get(path)).await.unwrap().data, *view);
        }
        let body = serde_json::json!({"action":"session.send", "target":{"workspace_id":ws,"work_item_id":wi},
            "params":{"text":"補上重新送出的案例","schedule":"after_current"},"preconditions":{"expected_attempt_id":att}});
        let mut input = request("POST", "/operations");
        input.idempotency_key = Some("original-work-input-key".into());
        input.body = Some(body.clone());
        assert_eq!(
            bridge.request(input).await.unwrap().data["operation"]["status"],
            "queued"
        );
        handle.join().unwrap();
        let requests: Vec<_> = received.iter().collect();
        assert_eq!(requests.len(), 8);
        for (raw, path) in requests[2..5].iter().zip(&reads) {
            assert!(raw.starts_with(&format!("GET /api/v1{path} ")));
            assert!(!raw.to_lowercase().contains("idempotency-key:"));
        }
        assert!(requests[5].starts_with("GET /api/v1/capabilities "));
        assert!(requests[6].starts_with("GET /api/v1/bootstrap "));
        let sent = &requests[7];
        assert!(sent.starts_with("POST /api/v1/operations "));
        assert!(sent
            .to_lowercase()
            .contains("idempotency-key: original-work-input-key"));
        assert_eq!(
            serde_json::from_str::<Value>(sent.split_once("\r\n\r\n").unwrap().1).unwrap(),
            body
        );
        assert!(requests.iter().all(|raw| raw
            .to_lowercase()
            .contains("authorization: bearer fixture-token")));
        assert!(!serde_json::to_string(&bridge.status())
            .unwrap()
            .contains("fixture-token"));
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

    #[cfg(target_os = "linux")]
    #[tokio::test]
    async fn saved_vault_record_authenticates_after_real_updater_rollback() {
        // Same data handlers/epoch, real updater pointers + real authenticated API.
        // Only the OS vault is doubled; no native-vault acceptance is claimed.
        struct Peer(std::process::Child);
        impl Drop for Peer {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let scratch = std::env::var_os("RUNNER_TEMP")
            .map(PathBuf::from)
            .unwrap_or_else(|| repository.join(".test-tmp"));
        std::fs::create_dir_all(&scratch).unwrap();
        let directory = tempfile::Builder::new()
            .prefix("updater-vault-")
            .tempdir_in(scratch)
            .unwrap();
        let python = repository.join(".venv/bin/python");
        assert!(
            python.is_file(),
            "Prepare the dev Python environment before native integration tests"
        );
        let mut peer = Peer(
            std::process::Command::new(python)
                .arg(repository.join("desktop/tests/updater-credentials-fixture.py"))
                .arg("--data-dir")
                .arg(directory.path())
                .env("TMPDIR", directory.path())
                .env("PYTHONPATH", &repository)
                .current_dir(&repository)
                .stdin(std::process::Stdio::piped())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::inherit())
                .spawn()
                .unwrap(),
        );
        let output = peer.0.stdout.take().unwrap();
        let (send, receive) = mpsc::channel();
        let reader = thread::spawn(move || {
            for line in std::io::BufRead::lines(std::io::BufReader::new(output)) {
                if send.send(line.unwrap()).is_err() {
                    break;
                }
            }
        });
        let response = || {
            let line = receive.recv_timeout(Duration::from_secs(20)).unwrap();
            serde_json::from_str::<serde_json::Value>(&line).unwrap()
        };
        let hello = response();
        let endpoint = hello["endpoint"].as_str().unwrap();
        let vault = Arc::new(MockVault::default());
        *vault.answer.lock().unwrap() = Some(hello["token"].as_str().unwrap().into());
        let bridge = credential_bridge(endpoint, vault.clone());
        let first = bridge.enroll(Locale::English, 0).await.unwrap().unwrap();
        let original = vault.records.lock().unwrap().clone();
        assert_eq!(original.len(), 1);
        drop(bridge);
        for (action, commit) in [
            ("apply", "b".repeat(40)),
            ("rollback", "a".repeat(40)),
            ("apply", "b".repeat(40)),
        ] {
            writeln!(
                peer.0.stdin.as_mut().unwrap(),
                "{}",
                serde_json::json!({"action": action})
            )
            .unwrap();
            let switched = response();
            assert_eq!(switched["commit"], commit);
            assert_eq!(switched["credential_unchanged"], true);
            *vault.answer.lock().unwrap() = None; // Reopen must read, never prompt/re-enroll.
            let restarted = credential_bridge(endpoint, vault.clone());
            let current = restarted.connect().await.unwrap();
            assert_eq!(current["desktop_identity"], first["desktop_identity"]);
            assert!(restarted.status().connected);
            assert!(*vault.records.lock().unwrap() == original);
        }
        writeln!(peer.0.stdin.as_mut().unwrap(), "{{\"action\":\"stop\"}}").unwrap();
        assert_eq!(response()["stopped"], true);
        assert!(peer.0.wait().unwrap().success());
        reader.join().unwrap();
    }

    #[tokio::test]
    async fn enrollment_is_verified_before_persistence_and_survives_native_restart() {
        let (endpoint, requests, server) = server(vec![
            json_response(&capabilities("fixture", "principal", false)),
            bootstrap_response("fixture", "principal"),
            json_response(&capabilities("fixture", "principal", false)),
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
        // A rejected replacement identity must preserve the original credential and connection.
        let (endpoint, _, server) = server(vec![
            json_response(&capabilities("fixture", "principal", false)),
            bootstrap_response("fixture", "principal"),
            json_response(&actor_document("wrong-actor")["capabilities"].to_string()),
            json_response(&capabilities("fixture", "principal", false)),
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
                    json_response(&capabilities("fixture", "principal", false)),
                    bootstrap_response("fixture", "principal"),
                ]);
            }
            replies.extend([
                json_response(&capabilities("replacement", "principal", false)),
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
            json_response(&actor_document("wrong-actor").to_string()),
            // Keep this document minimal to exercise missing contract and identity fields.
            json_response(r#"{"capabilities":{},"sync":{"version":1}}"#),
        ] {
            let (endpoint, _, server) = server(vec![
                json_response(&capabilities("fixture", "principal", false)),
                response,
            ]);
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
            json_response(&capabilities("fixture", "principal", false)),
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
            json_response(&capabilities("fixture", "principal", false)),
            bootstrap_response("fixture", "principal"),
            json_response(&capabilities("different-server", "principal", false)),
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
            socket
                .write_all(json_response(&capabilities("fixture", "principal", false)).as_bytes())
                .unwrap();
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
