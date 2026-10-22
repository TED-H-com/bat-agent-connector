//! Native file capabilities and local transfer receipts. This is not a central operation journal.
use crate::bridge::{Bridge, ConnectorRequest, FileScope, MAX_ARTIFACT_BYTES};
use base64::Engine;
use cap_std::{
    ambient_authority,
    fs::{Dir, OpenOptions},
};
use futures_util::TryStreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    fs::File,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::io::AsyncWriteExt;
use tokio_util::io::ReaderStream;
use uuid::Uuid;

const MAX_SPOOL: u64 = 64 * 1024 * 1024;
const MAX_FILES: usize = 20;
const MAX_RECORDS: usize = 1000;
const MAX_PREVIEW: u64 = 256 * 1024;
const STOPPED: &str = "File transfer stopped; the original operation is retained";
fn io_error(_: impl std::fmt::Display) -> String {
    "Native file storage is unavailable".into()
}
fn id() -> String {
    format!("file_{}", Uuid::new_v4().simple())
}
fn valid_id(value: &str) -> bool {
    value.len() == 37
        && value.starts_with("file_")
        && value[5..]
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
}
fn hex(value: &str, len: usize) -> bool {
    value.len() == len
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
pub(crate) fn validate_draft(draft: &str) -> Result<(), String> {
    if draft.is_empty() || draft.len() > 240 || draft.chars().any(char::is_control) {
        return Err("Invalid attachment draft".into());
    }
    Ok(())
}
fn safe_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 255
        && !matches!(name, "." | ".." | ".git")
        && !name
            .chars()
            .any(|c| c.is_control() || matches!(c, '/' | '\\' | ':'))
}
fn artifact_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 240
        && name.chars().count() <= 200
        && !matches!(name, "." | ".." | ".git")
        && !name
            .chars()
            .any(|c| c.is_control() || matches!(c, '/' | '\\'))
}
fn mime(name: &str) -> &'static str {
    match name
        .rsplit('.')
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "txt" | "md" | "log" | "csv" | "json" | "html" | "svg" => "text/plain",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        _ => "application/octet-stream",
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ArtifactRef {
    pub artifact_id: String,
    pub revision: u64,
    pub digest: String,
}
impl ArtifactRef {
    fn validate(&self) -> Result<(), String> {
        if !self.artifact_id.starts_with("art_")
            || !hex(self.artifact_id.get(4..).unwrap_or(""), 32)
            || !(1..=999_999_999).contains(&self.revision)
            || !hex(&self.digest, 64)
        {
            return Err("Invalid immutable artifact reference".into());
        }
        Ok(())
    }
    fn route(&self) -> String {
        format!(
            "/artifacts/{}/revisions/{}",
            self.artifact_id, self.revision
        )
    }
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub transfer_id: String,
    pub direction: String,
    pub draft_id: String,
    pub display_name: String,
    pub size_bytes: u64,
    pub digest: String,
    pub media_type: String,
    pub stage: String,
    pub transferred_bytes: u64,
    pub error: Option<String>,
    pub operation_id: Option<String>,
    pub operation_status: Option<String>,
    pub artifact: Option<ArtifactRef>,
    pub intent_key: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    version: u8,
    binding: String,
    actor: String,
    receipt: Receipt,
    request: Option<Value>,
}
impl Record {
    fn validate(&self) -> Result<(), String> {
        let r = &self.receipt;
        if self.version != 1
            || !hex(&self.binding, 64)
            || self.actor.is_empty()
            || self.actor.len() > 200
            || self.actor.chars().any(char::is_control)
            || !valid_id(&r.transfer_id)
            || !artifact_name(&r.display_name)
            || !hex(&r.digest, 64)
            || r.size_bytes > MAX_ARTIFACT_BYTES as u64
            || r.transferred_bytes > r.size_bytes
            || r.media_type.len() > 200
            || r.media_type.chars().any(char::is_control)
            || r.operation_id
                .as_ref()
                .is_some_and(|id| !id.starts_with("op_") || !hex(id.get(3..).unwrap_or(""), 32))
            || !matches!(
                r.stage.as_str(),
                "selected"
                    | "checking"
                    | "uploading"
                    | "verifying"
                    | "downloading"
                    | "saving"
                    | "ready"
                    | "saved"
                    | "stopped"
                    | "failed"
                    | "cancelled"
            )
        {
            return Err("Native transfer record is invalid".into());
        }
        if let Some(reference) = &r.artifact {
            reference.validate()?;
            if reference.digest != r.digest {
                return Err("Native artifact digest is invalid".into());
            }
        }
        match r.direction.as_str() {
            "upload" => {
                validate_draft(&r.draft_id)?;
                if Uuid::parse_str(&r.intent_key).is_err()
                    || r.operation_id.is_some() && self.request.is_none()
                    || self
                        .request
                        .as_ref()
                        .is_some_and(|value| value != &upload_intent(r))
                {
                    return Err(
                        "Native upload intent is damaged; original files were retained".into(),
                    );
                }
            }
            "download" => {
                if self.request.is_some() || r.artifact.is_none() || !r.intent_key.is_empty() {
                    return Err("Native download record is invalid".into());
                }
            }
            _ => return Err("Invalid native transfer direction".into()),
        }
        Ok(())
    }
}
fn upload_intent(r: &Receipt) -> Value {
    json!({"action":"artifact.upload","target":{},"params":{"display_name":r.display_name,"media_type":r.media_type,"size_bytes":r.size_bytes,"expected_digest":r.digest},"preconditions":{}})
}
#[derive(Default)]
struct State {
    records: HashMap<String, Record>,
    busy: HashSet<String>,
    stopped: HashSet<String>,
    drop_target: Option<(FileScope, String, std::time::Instant)>,
    paste_lease: Option<(FileScope, String, std::time::Instant)>,
    drop_error: Option<(String, String)>,
}
pub struct Files {
    pub dialog: tokio::sync::Mutex<()>,
    bridge: Arc<Bridge>,
    root: Dir,
    state: Mutex<State>,
}
pub struct Destination {
    directory: Dir,
    name: String,
}
#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Control {
    Stop,
    Retry,
    Check,
    CancelUpload,
    DiscardLocal,
}
#[derive(Serialize)]
pub struct Status {
    pub supported: bool,
    pub transfers: Vec<Receipt>,
    pub max_file_bytes: usize,
    pub max_spool_bytes: u64,
    pub drop_error: Option<String>,
}
#[derive(Serialize)]
pub struct Preview {
    pub media_type: String,
    pub text: Option<String>,
    pub base64: Option<String>,
}

impl Destination {
    pub(crate) fn display_name(&self) -> &str {
        &self.name
    }
    pub fn from_selection(path: &Path) -> Result<Self, String> {
        let name = path
            .file_name()
            .and_then(|x| x.to_str())
            .filter(|name| safe_name(name))
            .ok_or("Choose a regular filename")?
            .to_owned();
        let parent = path.parent().ok_or("Choose a destination directory")?;
        let directory = Dir::open_ambient_dir(parent, ambient_authority()).map_err(io_error)?;
        if directory.symlink_metadata(&name).is_ok() {
            return Err(
                "DESTINATION_EXISTS: Choose another filename; the existing file was preserved"
                    .into(),
            );
        }
        Ok(Self { directory, name })
    }
    fn publish(
        &self,
        source: File,
        expected: &Receipt,
        guard: impl Fn() -> Result<(), String>,
    ) -> Result<(), String> {
        self.publish_checked(source, expected.size_bytes, &expected.digest, guard)
    }
    pub(crate) fn publish_bytes(
        &self,
        bytes: &[u8],
        size_bytes: u64,
        digest: &str,
        guard: impl Fn() -> Result<(), String>,
    ) -> Result<(), String> {
        self.publish_checked(std::io::Cursor::new(bytes), size_bytes, digest, guard)
    }
    fn publish_checked(
        &self,
        mut source: impl Read,
        size_bytes: u64,
        expected_digest: &str,
        guard: impl Fn() -> Result<(), String>,
    ) -> Result<(), String> {
        let temporary = format!(".batc-{}.part", Uuid::new_v4().simple());
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        let mut file = self
            .directory
            .open_with(&temporary, &options)
            .map_err(io_error)?;
        let result = (|| {
            let mut digest = Sha256::new();
            let mut size = 0;
            let mut buffer = [0u8; 65536];
            loop {
                guard()?;
                let n = source.read(&mut buffer).map_err(io_error)?;
                if n == 0 {
                    break;
                }
                size += n as u64;
                if size > size_bytes {
                    return Err("File changed before saving".into());
                }
                digest.update(&buffer[..n]);
                file.write_all(&buffer[..n]).map_err(io_error)?;
            }
            if size != size_bytes || format!("{:x}", digest.finalize()) != expected_digest {
                return Err("Artifact digest does not match".into());
            }
            file.sync_all().map_err(io_error)?;
            guard()?;
            // Linking is atomic create-new: a destination that appeared meanwhile is never replaced.
            self.directory
                .hard_link(&temporary, &self.directory, &self.name)
                .map_err(|error| {
                    if error.kind()==std::io::ErrorKind::AlreadyExists {"DESTINATION_EXISTS: Destination was not replaced; choose another filename".to_string()}
                    else {"DESTINATION_UNAVAILABLE: Safe publication is unavailable here; choose another folder".to_string()}
                })?;
            Ok(())
        })();
        drop(file);
        let _ = self.directory.remove_file(&temporary);
        result
    }
}
impl Files {
    pub fn open(path: &Path, bridge: Arc<Bridge>) -> Result<Arc<Self>, String> {
        std::fs::create_dir_all(path).map_err(io_error)?;
        let directory = std::fs::symlink_metadata(path).map_err(io_error)?;
        if !directory.is_dir() || directory.file_type().is_symlink() {
            return Err("Native file cache must be a private directory".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
                .map_err(io_error)?;
        }
        let root = Dir::open_ambient_dir(path, ambient_authority()).map_err(io_error)?;
        // Cleanup is permitted only inside a positively identified, app-owned spool directory.
        const MARKER: &str = ".batc-native-files-v1";
        match root.symlink_metadata(MARKER) {
            Ok(meta) if meta.is_file() && !meta.file_type().is_symlink() => {
                if root.read(MARKER).map_err(io_error)? != b"BAT native transfers 1\n" {
                    return Err("Unrecognized native file cache".into());
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                if root.entries().map_err(io_error)?.next().is_some() {
                    return Err("Native cache directory is not empty".into());
                }
                let mut options = OpenOptions::new();
                options.write(true).create_new(true);
                let mut file = root.open_with(MARKER, &options).map_err(io_error)?;
                file.write_all(b"BAT native transfers 1\n")
                    .map_err(io_error)?;
                file.sync_all().map_err(io_error)?;
            }
            _ => return Err("Unrecognized native file cache".into()),
        }
        let mut state = State::default();
        for entry in root.entries().map_err(io_error)? {
            let entry = entry.map_err(io_error)?;
            let name = entry.file_name().to_string_lossy().into_owned();
            let Some(key) = name.strip_suffix(".json") else {
                continue;
            };
            if !valid_id(key) || !entry.file_type().map_err(io_error)?.is_file() {
                continue;
            }
            if state.records.len() >= MAX_RECORDS {
                return Err("Native transfer record limit reached".into());
            }
            let mut bytes = Vec::new();
            root.open(&name)
                .map_err(io_error)?
                .take(65537)
                .read_to_end(&mut bytes)
                .map_err(io_error)?;
            if bytes.len() > 65536 {
                return Err("Native transfer record is too large".into());
            }
            let mut record: Record =
                serde_json::from_slice(&bytes).map_err(|_| "Native transfer record is damaged")?;
            record.validate()?;
            if record.receipt.transfer_id != key {
                return Err("Native transfer handle is invalid".into());
            }
            if !matches!(
                record.receipt.stage.as_str(),
                "ready" | "saved" | "failed" | "cancelled" | "selected"
            ) {
                record.receipt.stage = "stopped".into();
            }
            state.records.insert(key.into(), record);
        }
        // Interrupted download fragments and uncommitted selections are ours, never source files.
        for entry in root.entries().map_err(io_error)? {
            let entry = entry.map_err(io_error)?;
            let name = entry.file_name().to_string_lossy().into_owned();
            let key = name.split('.').next().unwrap_or("");
            if valid_id(key)
                && (name.ends_with(".part")
                    || name.ends_with(".json.tmp")
                    || name.ends_with(".bin")
                        && state.records.get(key).is_none_or(|r| {
                            matches!(r.receipt.stage.as_str(), "ready" | "saved" | "cancelled")
                        }))
            {
                root.remove_file(&name).map_err(io_error)?;
            }
        }
        Ok(Arc::new(Self {
            dialog: tokio::sync::Mutex::new(()),
            bridge,
            root,
            state: Mutex::new(state),
        }))
    }
    fn persist(&self, record: &Record) -> Result<(), String> {
        record.validate()?;
        let temporary = format!(
            "{}.{}.json.tmp",
            record.receipt.transfer_id,
            Uuid::new_v4().simple()
        );
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        let mut file = self
            .root
            .open_with(&temporary, &options)
            .map_err(io_error)?;
        #[cfg(unix)]
        {
            use cap_std::fs::PermissionsExt;
            file.set_permissions(cap_std::fs::Permissions::from_mode(0o600))
                .map_err(io_error)?;
        }
        file.write_all(&serde_json::to_vec(record).map_err(io_error)?)
            .map_err(io_error)?;
        file.sync_all().map_err(io_error)?;
        drop(file);
        self.root
            .rename(
                &temporary,
                &self.root,
                format!("{}.json", record.receipt.transfer_id),
            )
            .map_err(io_error)
    }
    fn update(&self, key: &str, change: impl FnOnce(&mut Record)) -> Result<(), String> {
        let mut state = self.state.lock().unwrap();
        let mut record = state
            .records
            .get(key)
            .cloned()
            .ok_or("Unknown file handle")?;
        change(&mut record);
        self.persist(&record)?;
        state.records.insert(key.into(), record);
        Ok(())
    }

    fn progress(&self, key: &str, n: u64) {
        if let Some(record) = self.state.lock().unwrap().records.get_mut(key) {
            record.receipt.transferred_bytes = n;
        }
    }
    fn record(&self, key: &str, scope: &FileScope) -> Result<Record, String> {
        if !valid_id(key) {
            return Err("Invalid native file handle".into());
        }
        self.bridge.check_file_scope(scope)?;
        let record = self
            .state
            .lock()
            .unwrap()
            .records
            .get(key)
            .cloned()
            .ok_or("Unknown file handle")?;
        record.validate()?;
        if record.binding != scope.binding {
            return Err("File belongs to another backend or credential".into());
        }
        Ok(record)
    }
    pub fn scope(&self) -> Result<FileScope, String> {
        self.bridge.file_scope()
    }
    pub fn status(&self) -> Result<Status, String> {
        let scope = self.bridge.file_scope()?;
        let mut transfers: Vec<_> = self
            .state
            .lock()
            .unwrap()
            .records
            .values()
            .filter(|r| r.binding == scope.binding)
            .map(|r| r.receipt.clone())
            .collect();
        transfers.sort_by(|a, b| a.transfer_id.cmp(&b.transfer_id));
        Ok(Status {
            supported: true,
            transfers,
            max_file_bytes: MAX_ARTIFACT_BYTES,
            max_spool_bytes: MAX_SPOOL,
            drop_error: self
                .state
                .lock()
                .unwrap()
                .drop_error
                .as_ref()
                .filter(|(binding, _)| binding == &scope.binding)
                .map(|(_, error)| error.clone()),
        })
    }
    async fn request(
        &self,
        scope: &FileScope,
        method: &str,
        path: String,
        body: Option<Value>,
        key: Option<String>,
    ) -> Result<Value, String> {
        let response = self
            .bridge
            .file_request(
                scope,
                ConnectorRequest {
                    method: method.into(),
                    path,
                    body,
                    idempotency_key: key,
                },
            )
            .await?;
        if !(200..300).contains(&response.status) {
            return Err(format!(
                "{}: {}",
                response.data["error"]["code"]
                    .as_str()
                    .unwrap_or("CENTRAL_ERROR"),
                response.data["error"]["message"]
                    .as_str()
                    .unwrap_or("File request was refused")
            ));
        }
        Ok(response.data)
    }
    async fn limits(&self, scope: &FileScope) -> Result<(u64, usize, u64), String> {
        let caps = self
            .request(scope, "GET", "/capabilities".into(), None, None)
            .await?;
        if !caps["actions"].as_array().is_some_and(|a| {
            a.iter()
                .any(|x| x["action"] == "artifact.upload" && x["allowed"] == true)
        }) {
            return Err("Artifact upload needs manage permission".into());
        }
        let limits = &caps["artifacts"]["limits"];
        Ok((
            limits["max_file_bytes"]
                .as_u64()
                .ok_or("Artifact limits unavailable")?
                .min(MAX_ARTIFACT_BYTES as u64),
            limits["max_selection_count"]
                .as_u64()
                .ok_or("Artifact limits unavailable")?
                .min(MAX_FILES as u64) as usize,
            limits["max_selection_bytes"]
                .as_u64()
                .ok_or("Artifact selection limit unavailable")?
                .min(MAX_SPOOL),
        ))
    }
    pub fn drop_target(&self, draft: String, enabled: bool) -> Result<(), String> {
        validate_draft(&draft)?;
        let scope = self.bridge.file_scope()?;
        let mut state = self.state.lock().unwrap();
        if enabled {
            state.drop_target = Some((scope, draft, std::time::Instant::now()));
        } else if state
            .drop_target
            .as_ref()
            .is_some_and(|(owner, id, _)| owner == &scope && id == &draft)
        {
            state.drop_target = None;
        }
        Ok(())
    }
    pub fn paste_arm(
        &self,
        draft: String,
        focused: bool,
    ) -> Result<(), crate::clipboard::ClipboardError> {
        let mut state = self.state.lock().unwrap();
        state.paste_lease = None;
        validate_draft(&draft)?;
        if !focused {
            return Err(crate::clipboard::ClipboardError::refused(
                "window_not_focused",
            ));
        }
        state.paste_lease = Some((self.scope()?, draft, std::time::Instant::now()));
        Ok(())
    }
    pub(crate) fn paste_with(
        &self,
        draft: &str,
        focused: bool,
        reader: &mut impl crate::clipboard::ClipboardReader,
        limit: u64,
    ) -> Result<Receipt, crate::clipboard::ClipboardError> {
        use crate::clipboard::{encode_image, ClipboardError};
        // Consume even an invalid attempt, before invoking any clipboard API.
        let lease = self.state.lock().unwrap().paste_lease.take();
        let (scope, owner, armed) =
            lease.ok_or_else(|| ClipboardError::refused("paste_lease_required"))?;
        validate_draft(draft)?;
        if !focused || owner != draft || armed.elapsed() >= Duration::from_secs(2) {
            return Err(ClipboardError::refused("paste_lease_invalid"));
        }
        self.bridge.check_file_scope(&scope)?;
        let bytes = reader.read(|image| encode_image(image, limit as usize))?;
        self.bridge.check_file_scope(&scope)?;
        // UTC Unix milliseconds; no local timezone or source filename enters the receipt.
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| ClipboardError::refused("clock_unavailable"))?
            .as_millis();
        let name = format!("pasted-image-{timestamp}Z.png");
        let receipt = self.spool_source(
            &scope,
            draft,
            &name,
            bytes.len() as u64,
            limit,
            std::io::Cursor::new(bytes),
            |_| Ok(()),
        )?;
        self.bridge.check_file_scope(&scope)?;
        Ok(receipt)
    }
    pub async fn paste_limit(&self) -> Result<u64, crate::clipboard::ClipboardError> {
        let scope = self.scope()?;
        let (limit, count, total) = self.limits(&scope).await?;
        if count == 0 {
            return Err(crate::clipboard::ClipboardError::refused(
                "selection_not_allowed",
            ));
        }
        self.bridge.check_file_scope(&scope)?;
        Ok(limit.min(total))
    }
    pub async fn dropped(self: Arc<Self>, paths: Vec<PathBuf>) {
        let target = self.state.lock().unwrap().drop_target.take();
        if let Some((scope, draft, armed)) = target {
            if armed.elapsed() < Duration::from_secs(2)
                && self.bridge.check_file_scope(&scope).is_ok()
            {
                let binding = scope.binding.clone();
                if let Err(error) = self.pick_paths(scope, &draft, paths).await {
                    self.state.lock().unwrap().drop_error = Some((binding, error));
                }
            }
        }
    }
    pub async fn pick_paths(
        self: &Arc<Self>,
        scope: FileScope,
        draft: &str,
        paths: Vec<PathBuf>,
    ) -> Result<Vec<Receipt>, String> {
        validate_draft(draft)?;
        self.bridge.check_file_scope(&scope)?;
        let (limit, count, total) = self.limits(&scope).await?;
        if paths.len() > count || paths.is_empty() {
            return Err("Too many selected files".into());
        }
        let this = self.clone();
        let draft = draft.to_owned();
        let final_scope = scope.clone();
        let receipts = tokio::task::spawn_blocking(move || -> Result<Vec<Receipt>, String> {
            let mut output = Vec::new();
            let selected = paths
                .iter()
                .try_fold(0u64, |sum, path| {
                    std::fs::symlink_metadata(path).map(|m| sum.saturating_add(m.len()))
                })
                .map_err(io_error)?;
            if selected > total {
                return Err("Selection exceeds the total size limit".into());
            }
            for path in paths {
                this.bridge.check_file_scope(&scope)?;
                output.push(this.spool_file(&scope, &draft, &path, limit)?);
            }
            Ok(output)
        })
        .await
        .map_err(|_| "File selection interrupted")??;
        self.bridge.check_file_scope(&final_scope)?;
        Ok(receipts)
    }
    fn spool_file(
        &self,
        scope: &FileScope,
        draft: &str,
        path: &Path,
        limit: u64,
    ) -> Result<Receipt, String> {
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .filter(|s| safe_name(s) && artifact_name(s))
            .ok_or("Choose a regular filename")?
            .to_owned();
        let before = std::fs::symlink_metadata(path).map_err(io_error)?;
        if !before.is_file() || before.file_type().is_symlink() || before.len() > limit {
            return Err("Choose a regular file within the size limit".into());
        }
        let mut options = std::fs::OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            options.share_mode(1).custom_flags(0x00200000); /* read sharing, OPEN_REPARSE_POINT */
        }
        let source = options.open(path).map_err(io_error)?;
        let metadata = source.metadata().map_err(io_error)?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Err("Reparse files are not supported".into());
            }
        }
        if !metadata.is_file()
            || metadata.len() != before.len()
            || metadata.modified().ok() != before.modified().ok()
        {
            return Err("Selected file changed".into());
        }
        self.spool_source(
            scope,
            draft,
            &name,
            metadata.len(),
            limit,
            source,
            |source| {
                let after = source.metadata().map_err(io_error)?;
                if after.len() != metadata.len()
                    || after.modified().ok() != metadata.modified().ok()
                {
                    return Err("Selected file changed during selection".into());
                }
                Ok(())
            },
        )
    }
    // Picker and clipboard share capacity accounting, hashing, persistence and cleanup.
    #[allow(clippy::too_many_arguments)]
    fn spool_source<R: Read>(
        &self,
        scope: &FileScope,
        draft: &str,
        name: &str,
        expected_size: u64,
        limit: u64,
        mut source: R,
        verify: impl FnOnce(&R) -> Result<(), String>,
    ) -> Result<Receipt, String> {
        validate_draft(draft)?;
        self.bridge.check_file_scope(scope)?;
        if expected_size > limit {
            return Err("Selected file exceeded its limit".into());
        }
        let mut state = self.state.lock().unwrap();
        let reserved: u64 = state
            .records
            .values()
            .filter(|r| {
                r.receipt.direction == "download"
                    && matches!(r.receipt.stage.as_str(), "downloading" | "saving")
            })
            .map(|r| r.receipt.size_bytes)
            .sum();
        let used = self.cache_bytes()?.saturating_add(reserved);
        if used.saturating_add(expected_size) > MAX_SPOOL || state.records.len() >= MAX_RECORDS {
            return Err("Native file cache is full; finish or discard local transfers".into());
        }
        let key = id();
        let mut target_options = OpenOptions::new();
        target_options.write(true).create_new(true);
        let mut target = self
            .root
            .open_with(format!("{key}.bin"), &target_options)
            .map_err(io_error)?;
        #[cfg(unix)]
        {
            use cap_std::fs::PermissionsExt;
            target
                .set_permissions(cap_std::fs::Permissions::from_mode(0o600))
                .map_err(io_error)?;
        }
        let result = (|| {
            let mut buffer = [0u8; 65536];
            let mut digest = Sha256::new();
            let mut size = 0;
            loop {
                self.bridge.check_file_scope(scope)?;
                let n = source.read(&mut buffer).map_err(io_error)?;
                if n == 0 {
                    break;
                }
                size += n as u64;
                if size > limit || size > expected_size {
                    return Err("Selected file changed or exceeded its limit".into());
                }
                digest.update(&buffer[..n]);
                target.write_all(&buffer[..n]).map_err(io_error)?;
            }
            verify(&source)?;
            if size != expected_size {
                return Err("Selected file changed during selection".into());
            }
            self.bridge.check_file_scope(scope)?;
            target.sync_all().map_err(io_error)?;
            let receipt = Receipt {
                transfer_id: key.clone(),
                direction: "upload".into(),
                draft_id: draft.into(),
                display_name: name.to_owned(),
                size_bytes: size,
                digest: format!("{:x}", digest.finalize()),
                media_type: mime(name).into(),
                stage: "selected".into(),
                transferred_bytes: 0,
                error: None,
                operation_id: None,
                operation_status: None,
                artifact: None,
                intent_key: Uuid::new_v4().to_string(),
            };
            let record = Record {
                version: 1,
                binding: scope.binding.clone(),
                actor: scope.actor.clone(),
                receipt: receipt.clone(),
                request: None,
            };
            self.persist(&record)?;
            state.records.insert(key.clone(), record);
            Ok(receipt)
        })();
        drop(target);
        if result.is_err() {
            let _ = self.root.remove_file(format!("{key}.bin"));
        }
        result
    }
    fn cache_bytes(&self) -> Result<u64, String> {
        let mut used = 0u64;
        for entry in self.root.entries().map_err(io_error)? {
            let entry = entry.map_err(io_error)?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.ends_with(".bin") || name.ends_with(".part") {
                used = used.saturating_add(entry.metadata().map_err(io_error)?.len());
            }
        }
        Ok(used)
    }
    fn checked_spool(&self, record: &Record) -> Result<File, String> {
        let mut file = self
            .root
            .open(format!("{}.bin", record.receipt.transfer_id))
            .map_err(|_| "Select the original file again; native bytes are unavailable")?
            .into_std();
        let mut digest = Sha256::new();
        let mut size = 0;
        let mut buffer = [0u8; 65536];
        loop {
            let n = file.read(&mut buffer).map_err(io_error)?;
            if n == 0 {
                break;
            }
            size += n as u64;
            if size > record.receipt.size_bytes {
                return Err("Native cached file changed".into());
            }
            digest.update(&buffer[..n]);
        }
        if size != record.receipt.size_bytes
            || format!("{:x}", digest.finalize()) != record.receipt.digest
        {
            return Err("Native cached file changed".into());
        }
        use std::io::{Seek, SeekFrom};
        file.seek(SeekFrom::Start(0)).map_err(io_error)?;
        Ok(file)
    }
    fn guard(&self, key: &str, scope: &FileScope) -> Result<(), String> {
        self.bridge.check_file_scope(scope)?;
        if self.state.lock().unwrap().stopped.contains(key) {
            return Err(STOPPED.into());
        }
        Ok(())
    }
    pub fn start_upload(self: &Arc<Self>, key: &str) -> Result<Receipt, String> {
        let scope = self.bridge.file_scope()?;
        let record = self.record(key, &scope)?;
        if record.receipt.direction != "upload" {
            return Err("This handle is not an upload".into());
        }
        if matches!(
            record.receipt.operation_status.as_deref(),
            Some("failed" | "cancelled")
        ) {
            return Err(
                "Original upload ended; explicitly choose a new file to create a new upload".into(),
            );
        }
        self.start(key, scope, None, false)?;
        Ok(record.receipt)
    }
    fn start(
        self: &Arc<Self>,
        key: &str,
        scope: FileScope,
        destination: Option<Destination>,
        check_only: bool,
    ) -> Result<(), String> {
        {
            let mut state = self.state.lock().unwrap();
            if !state.busy.insert(key.into()) {
                return Ok(());
            }
            state.stopped.remove(key);
        }
        let this = self.clone();
        let key = key.to_owned();
        tauri::async_runtime::spawn(async move {
            let result = {
                let job = async {
                    if let Some(destination) = destination {
                        this.download(&key, &scope, destination).await
                    } else {
                        this.upload(&key, &scope, check_only).await
                    }
                };
                tokio::pin!(job);
                loop {
                    tokio::select! {result=&mut job=>break result,_=tokio::time::sleep(Duration::from_millis(100))=>{if let Err(error)=this.guard(&key,&scope){let saving=this.state.lock().unwrap().records.get(&key).is_some_and(|r|r.receipt.stage=="saving");if !saving {break Err(error);}}}}
                }
            };
            if let Err(error) = result {
                let _ = this.root.remove_file(format!("{key}.part"));
                let _ = this.update(&key, |r| {
                    r.receipt.stage = "stopped".into();
                    r.receipt.error = Some(error);
                });
            }
            this.state.lock().unwrap().busy.remove(&key);
        });
        Ok(())
    }
    fn accept_operation(
        &self,
        key: &str,
        scope: &FileScope,
        operation: &Value,
    ) -> Result<(), String> {
        let record = self.record(key, scope)?;
        verify_operation(&record, operation)?;
        self.update(key, |r| {
            r.receipt.operation_id = operation["operation_id"].as_str().map(str::to_owned);
            r.receipt.operation_status = operation["status"].as_str().map(str::to_owned);
        })
    }
    async fn operation(&self, key: &str, scope: &FileScope) -> Result<Value, String> {
        let record = self.record(key, scope)?;
        let doc = if let Some(op) = &record.receipt.operation_id {
            self.request(scope, "GET", format!("/operations/{op}"), None, None)
                .await?
        } else {
            self.request(
                scope,
                "POST",
                "/operations?wait=0".into(),
                record.request.clone(),
                Some(record.receipt.intent_key.clone()),
            )
            .await?
        };
        let op = doc.get("operation").ok_or("Missing upload operation")?;
        self.accept_operation(key, scope, op)?;
        Ok(op.clone())
    }
    async fn upload(
        self: &Arc<Self>,
        key: &str,
        scope: &FileScope,
        check_only: bool,
    ) -> Result<(), String> {
        let record = self.record(key, scope)?;
        if record.receipt.artifact.is_some() {
            return Ok(());
        }
        if record.request.is_none() {
            let request = upload_intent(&record.receipt);
            self.update(key, |r| {
                r.request = Some(request);
                r.receipt.stage = "checking".into();
                r.receipt.error = None;
            })?;
        }
        self.update(key, |r| {
            r.receipt.stage = "checking".into();
            r.receipt.error = None;
        })?;
        let mut op = self.operation(key, scope).await?;
        let deadline = tokio::time::Instant::now() + Duration::from_secs(300);
        let mut sent = false;
        loop {
            self.guard(key, scope)?;
            match op["status"].as_str().unwrap_or("") {
                "succeeded" => {
                    let reference:ArtifactRef=serde_json::from_value(json!({"artifact_id":op["result"]["artifact_id"],"revision":op["result"]["revision"],"digest":op["result"]["digest"]})).map_err(|_|"Invalid upload result")?;
                    let artifact = self.metadata(scope, &reference).await?;
                    let current = self.record(key, scope)?;
                    if artifact["operation_id"] != op["operation_id"]
                        || artifact["size_bytes"] != current.receipt.size_bytes
                        || reference.digest != current.receipt.digest
                        || artifact["display_name"] != current.receipt.display_name
                        || artifact["media_type"] != current.receipt.media_type
                    {
                        return Err("Upload result does not match the selected file".into());
                    }
                    self.update(key, |r| {
                        r.receipt.artifact = Some(reference);
                        r.receipt.stage = "ready".into();
                        r.receipt.error = None;
                    })?;
                    let _ = self.root.remove_file(format!("{key}.bin"));
                    return Ok(());
                }
                "failed" | "cancelled" => {
                    self.update(key, |r| {
                        r.receipt.stage = op["status"].as_str().unwrap().into();
                        r.receipt.error = Some(
                            op["status_reason"]
                                .as_str()
                                .unwrap_or("Original upload ended")
                                .into(),
                        );
                    })?;
                    return Ok(());
                }
                "waiting_external"
                    if !sent
                        && !check_only
                        && op["cancel_requested"] != true
                        && op["cancel_requested"] != 1 =>
                {
                    let current = self.record(key, scope)?;
                    let this = self.clone();
                    let copy = current.clone();
                    let file = tokio::task::spawn_blocking(move || this.checked_spool(&copy))
                        .await
                        .map_err(|_| "Native file verification interrupted")??;
                    self.update(key, |r| {
                        r.receipt.stage = "uploading".into();
                        r.receipt.transferred_bytes = 0;
                        r.receipt.error = None;
                    })?;
                    let this = self.clone();
                    let stream_key = key.to_owned();
                    let stream_scope = scope.clone();
                    let mut count = 0;
                    let stream =
                        ReaderStream::with_capacity(tokio::fs::File::from_std(file), 65536).map_ok(
                            move |chunk| {
                                count += chunk.len() as u64;
                                this.progress(&stream_key, count);
                                chunk
                            },
                        );
                    let response = self
                        .bridge
                        .file_content(
                            &stream_scope,
                            &format!(
                                "/artifacts/uploads/{}/content",
                                op["operation_id"].as_str().unwrap()
                            ),
                            Some((
                                reqwest::Body::wrap_stream(stream),
                                current.receipt.size_bytes,
                            )),
                        )
                        .await?;
                    let response = Bridge::read_response(response).await?;
                    if !(200..300).contains(&response.status) {
                        return Err(format!(
                            "{}: check the original upload before retry",
                            response.data["error"]["code"]
                                .as_str()
                                .unwrap_or("UPLOAD_INTERRUPTED")
                        ));
                    }
                    self.accept_operation(key, scope, &response.data["operation"])?;
                    sent = true;
                    self.update(key, |r| r.receipt.stage = "verifying".into())?;
                }
                "accepted" | "running" | "waiting_external" => {
                    if check_only {
                        self.update(key, |r| {
                            r.receipt.stage = "stopped".into();
                            r.receipt.error = None;
                        })?;
                        return Ok(());
                    }
                }
                _ => {
                    return Err("Upload outcome requires its operation details; the original intent was retained".into());
                }
            }
            if tokio::time::Instant::now() > deadline {
                return Err("Upload verification timed out; check the original operation".into());
            }
            tokio::time::sleep(Duration::from_millis(400)).await;
            op = self.operation(key, scope).await?;
        }
    }
    async fn metadata(&self, scope: &FileScope, reference: &ArtifactRef) -> Result<Value, String> {
        reference.validate()?;
        let doc = self
            .request(scope, "GET", reference.route(), None, None)
            .await?;
        let row = doc.get("artifact").ok_or("Artifact metadata unavailable")?;
        if row["artifact_id"] != reference.artifact_id
            || row["revision"] != reference.revision
            || row["digest"] != reference.digest
            || row["state"] != "ready"
            || row["size_bytes"]
                .as_u64()
                .is_none_or(|n| n > MAX_ARTIFACT_BYTES as u64)
        {
            return Err("Artifact revision is unavailable or does not match".into());
        }
        Ok(row.clone())
    }
    pub async fn save_metadata(
        &self,
        scope: &FileScope,
        reference: &ArtifactRef,
    ) -> Result<Value, String> {
        self.metadata(scope, reference).await
    }
    pub fn save(
        self: &Arc<Self>,
        scope: FileScope,
        reference: ArtifactRef,
        metadata: Value,
        destination: Destination,
        existing: Option<String>,
    ) -> Result<Receipt, String> {
        self.bridge.check_file_scope(&scope)?;
        reference.validate()?;
        let key = existing.unwrap_or_else(id);
        if !valid_id(&key) {
            return Err("Invalid transfer handle".into());
        }
        let receipt = Receipt {
            transfer_id: key.clone(),
            direction: "download".into(),
            draft_id: String::new(),
            display_name: metadata["display_name"]
                .as_str()
                .unwrap_or("artifact")
                .into(),
            size_bytes: metadata["size_bytes"]
                .as_u64()
                .ok_or("Artifact size unavailable")?,
            digest: reference.digest.clone(),
            media_type: metadata["media_type"]
                .as_str()
                .unwrap_or("application/octet-stream")
                .into(),
            stage: "downloading".into(),
            transferred_bytes: 0,
            error: None,
            operation_id: None,
            operation_status: None,
            artifact: Some(reference),
            intent_key: String::new(),
        };
        {
            let mut state = self.state.lock().unwrap();
            if state.busy.contains(&key) {
                return Err("Stop the active transfer first".into());
            }
            if !state.records.contains_key(&key) && state.records.len() >= MAX_RECORDS {
                return Err("Native transfer record limit reached".into());
            }
            let reserved: u64 = state
                .records
                .values()
                .filter(|r| {
                    r.receipt.direction == "download"
                        && matches!(r.receipt.stage.as_str(), "downloading" | "saving")
                        && r.receipt.transfer_id != key
                })
                .map(|r| r.receipt.size_bytes)
                .sum();
            if self
                .cache_bytes()?
                .saturating_add(reserved)
                .saturating_add(receipt.size_bytes)
                > MAX_SPOOL
            {
                return Err("Native file cache is full".into());
            }
            if let Some(old) = state.records.get(&key) {
                if old.binding != scope.binding || old.receipt.artifact != receipt.artifact {
                    return Err("Download identity changed".into());
                }
            }
            let record = Record {
                version: 1,
                binding: scope.binding.clone(),
                actor: scope.actor.clone(),
                receipt: receipt.clone(),
                request: None,
            };
            self.persist(&record)?;
            state.records.insert(key.clone(), record);
        }
        self.start(&key, scope, Some(destination), false)?;
        Ok(receipt)
    }
    async fn download(
        self: &Arc<Self>,
        key: &str,
        scope: &FileScope,
        destination: Destination,
    ) -> Result<(), String> {
        let record = self.record(key, scope)?;
        let reference = record
            .receipt
            .artifact
            .as_ref()
            .ok_or("Missing artifact revision")?;
        self.metadata(scope, reference).await?;
        let partial = format!("{key}.part");
        let _ = self.root.remove_file(&partial);
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        let mut file = tokio::fs::File::from_std(
            self.root
                .open_with(&partial, &options)
                .map_err(io_error)?
                .into_std(),
        );
        let result = async {
            let mut response = self
                .bridge
                .file_content(scope, &format!("{}/content", reference.route()), None)
                .await?;
            if response.status() != 200
                || response.content_length() != Some(record.receipt.size_bytes)
            {
                return Err("Artifact response size or status does not match".into());
            }
            let mut digest = Sha256::new();
            let mut size = 0;
            while let Some(chunk) = response.chunk().await.map_err(|_| "Download interrupted")? {
                self.guard(key, scope)?;
                size += chunk.len() as u64;
                if size > record.receipt.size_bytes {
                    return Err("Artifact exceeded its fixed size".into());
                }
                digest.update(&chunk);
                file.write_all(&chunk).await.map_err(io_error)?;
                self.progress(key, size);
            }
            if size != record.receipt.size_bytes
                || format!("{:x}", digest.finalize()) != reference.digest
            {
                return Err("Artifact digest does not match".into());
            }
            file.sync_all().await.map_err(io_error)?;
            Ok(())
        }
        .await;
        drop(file);
        if let Err(error) = result {
            let _ = self.root.remove_file(&partial);
            return Err(error);
        }
        self.guard(key, scope)?;
        self.update(key, |r| r.receipt.stage = "saving".into())?;
        let source = self.root.open(&partial).map_err(io_error)?.into_std();
        let expected = record.receipt.clone();
        let this = self.clone();
        let save_key = key.to_owned();
        let save_scope = scope.clone();
        let result = tokio::task::spawn_blocking(move || {
            destination.publish(source, &expected, || this.guard(&save_key, &save_scope))
        })
        .await
        .map_err(|_| "Native save interrupted")?;
        let _ = self.root.remove_file(&partial);
        result?;
        self.update(key, |r| {
            r.receipt.stage = "saved".into();
            r.receipt.error = None;
        })?;
        Ok(())
    }
    pub fn download_reference(&self, key: &str) -> Result<ArtifactRef, String> {
        let record = self.record(key, &self.bridge.file_scope()?)?;
        if record.receipt.direction != "download" {
            return Err("Not a download".into());
        }
        record
            .receipt
            .artifact
            .ok_or("Missing download reference".into())
    }
    pub async fn control(self: &Arc<Self>, key: &str, action: Control) -> Result<(), String> {
        let scope = self.bridge.file_scope()?;
        let record = self.record(key, &scope)?;
        if matches!(action, Control::Stop) {
            self.state.lock().unwrap().stopped.insert(key.into());
            return Ok(());
        }
        if self.state.lock().unwrap().busy.contains(key) {
            return Err("Stop the active transfer first".into());
        }
        match action {
            Control::Retry => {
                self.start_upload(key)?;
            }
            Control::Check => {
                if record.request.is_none() {
                    return Ok(());
                }
                self.start(key, scope, None, true)?;
            }
            Control::CancelUpload => {
                if record.receipt.operation_id.is_none() {
                    return Err("Check the original upload before cancellation".into());
                }
                // Re-prove the exact cached operation binding before sending a control effect.
                let current = self.operation(key, &scope).await?;
                let operation = current["operation_id"]
                    .as_str()
                    .ok_or("Missing upload operation")?;
                let op = self
                    .request(
                        &scope,
                        "POST",
                        format!("/operations/{operation}/cancel"),
                        Some(json!({})),
                        None,
                    )
                    .await?;
                self.accept_operation(key, &scope, &op["operation"])?;
                // Follow cancellation through readback. A cancel-requested operation must never
                // receive another byte body, including when its terminal transition is delayed.
                self.start(key, scope, None, false)?;
            }
            Control::DiscardLocal => {
                if record.request.is_some()
                    && !matches!(
                        record.receipt.operation_status.as_deref(),
                        Some("succeeded" | "failed" | "cancelled")
                    )
                {
                    return Err(
                        "Resolve the original upload before discarding its local receipt".into(),
                    );
                }
                self.root
                    .remove_file(format!("{key}.json"))
                    .map_err(io_error)?;
                let _ = self.root.remove_file(format!("{key}.bin"));
                let _ = self.root.remove_file(format!("{key}.part"));
                self.state.lock().unwrap().records.remove(key);
            }
            Control::Stop => unreachable!(),
        }
        Ok(())
    }
    pub async fn preview(&self, reference: &ArtifactRef) -> Result<Preview, String> {
        let scope = self.bridge.file_scope()?;
        let metadata = self.metadata(&scope, reference).await?;
        let size = metadata["size_bytes"].as_u64().unwrap();
        if size > 2 * 1024 * 1024 {
            return Err("Preview is limited to small text or PNG files; use Save As".into());
        }
        let mut response = self
            .bridge
            .file_content(&scope, &format!("{}/content", reference.route()), None)
            .await?;
        if response.status() != 200 || response.content_length() != Some(size) {
            return Err("Artifact response does not match".into());
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| "Preview interrupted")? {
            self.bridge.check_file_scope(&scope)?;
            if bytes.len() + chunk.len() > size as usize {
                return Err("Preview exceeds its bound".into());
            }
            bytes.extend_from_slice(&chunk);
        }
        if bytes.len() != size as usize
            || format!("{:x}", Sha256::digest(&bytes)) != reference.digest
        {
            return Err("Preview digest mismatch".into());
        }
        let preview = tokio::task::spawn_blocking(move || decode_preview(bytes))
            .await
            .map_err(|_| "Preview interrupted")??;
        self.bridge.check_file_scope(&scope)?;
        Ok(preview)
    }
}
fn verify_operation(record: &Record, op: &Value) -> Result<(), String> {
    let request = record
        .request
        .as_ref()
        .ok_or("Missing original upload intent")?;
    let oid = op["operation_id"].as_str().ok_or("Missing operation ID")?;
    if !oid.starts_with("op_")
        || !hex(oid.get(3..).unwrap_or(""), 32)
        || record
            .receipt
            .operation_id
            .as_ref()
            .is_some_and(|id| id != oid)
        || op["idempotency_key"] != record.receipt.intent_key
        || op["actor"] != record.actor
        || ["action", "target", "params", "preconditions"]
            .iter()
            .any(|field| op[field] != request[field])
    {
        return Err("Operation receipt does not match the original upload".into());
    }
    Ok(())
}

fn decode_preview(bytes: Vec<u8>) -> Result<Preview, String> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        let mut decoder = png::Decoder::new_with_limits(
            std::io::Cursor::new(&bytes),
            png::Limits {
                bytes: 8 * 1024 * 1024,
            },
        );
        decoder.set_ignore_text_chunk(true);
        decoder.set_ignore_iccp_chunk(true);
        decoder.set_transformations(png::Transformations::normalize_to_color8());
        let header = decoder
            .read_header_info()
            .map_err(|_| "Invalid PNG preview")?;
        if u64::from(header.width) * u64::from(header.height) > 1_048_576 {
            return Err("PNG preview exceeds one megapixel; use Save As".into());
        }
        let mut reader = decoder.read_info().map_err(|_| "Invalid PNG preview")?;
        if reader.info().animation_control.is_some() {
            return Err("Animated PNG preview is unsupported; use Save As".into());
        }
        let capacity = reader
            .output_buffer_size()
            .filter(|n| *n <= 4 * 1024 * 1024)
            .ok_or("PNG preview is too large")?;
        let mut pixels = vec![0; capacity];
        let info = reader
            .next_frame(&mut pixels)
            .map_err(|_| "Invalid PNG preview")?;
        let mut encoded = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut encoded, info.width, info.height);
            encoder.set_color(info.color_type);
            encoder.set_depth(info.bit_depth);
            let mut writer = encoder
                .write_header()
                .map_err(|_| "PNG preview unavailable")?;
            writer
                .write_image_data(&pixels[..info.buffer_size()])
                .map_err(|_| "PNG preview unavailable")?;
        }
        if encoded.len() > 2 * 1024 * 1024 {
            return Err("PNG preview is too large; use Save As".into());
        }
        return Ok(Preview {
            media_type: "image/png".into(),
            text: None,
            base64: Some(base64::engine::general_purpose::STANDARD.encode(encoded)),
        });
    }
    if bytes.len() > MAX_PREVIEW as usize {
        return Err("Text preview exceeds 256 KiB; use Save As".into());
    }
    let text = String::from_utf8(bytes)
        .map_err(|_| "Preview supports UTF-8 text and PNG; use Save As for this file")?;
    if text.contains('\0') {
        return Err("Binary content has no text preview".into());
    }
    Ok(Preview {
        media_type: "text/plain".into(),
        text: Some(text),
        base64: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    include!("clipboard_tests.rs");
    fn fixture() -> (tempfile::TempDir, Arc<Bridge>, Arc<Files>) {
        let root = tempfile::tempdir().unwrap();
        let bridge = Arc::new(Bridge::file_fixture(
            "http://127.0.0.1:9/",
            "fixture-secret",
        ));
        let files = Files::open(&root.path().join("cache"), bridge.clone()).unwrap();
        (root, bridge, files)
    }
    fn select(root: &Path, files: &Files, bytes: &[u8]) -> Receipt {
        let source = root.join("example.txt");
        std::fs::write(&source, bytes).unwrap();
        files
            .spool_file(
                &files.scope().unwrap(),
                "draft",
                &source,
                MAX_ARTIFACT_BYTES as u64,
            )
            .unwrap()
    }
    fn op_record(files: &Files, receipt: &Receipt) -> (Record, Value) {
        files.update(&receipt.transfer_id,|r|r.request=Some(json!({"action":"artifact.upload","target":{},"params":{"display_name":r.receipt.display_name,"media_type":r.receipt.media_type,"size_bytes":r.receipt.size_bytes,"expected_digest":r.receipt.digest},"preconditions":{}}))).unwrap();
        let record = files
            .record(&receipt.transfer_id, &files.scope().unwrap())
            .unwrap();
        let mut op = record.request.clone().unwrap();
        op["operation_id"] = json!(format!("op_{}", "a".repeat(32)));
        op["idempotency_key"] = json!(receipt.intent_key);
        op["actor"] = json!(record.actor);
        op["status"] = json!("waiting_external");
        (record, op)
    }
    #[test]
    fn selection_is_an_immutable_copy_without_source_writes() {
        let (root, _, files) = fixture();
        let bytes = [0, 255, 128, 10, 0, 200];
        let receipt = select(root.path(), &files, &bytes);
        assert_eq!(
            std::fs::read(root.path().join("example.txt")).unwrap(),
            bytes
        );
        std::fs::write(root.path().join("example.txt"), b"later source edit").unwrap();
        let record = files
            .record(&receipt.transfer_id, &files.scope().unwrap())
            .unwrap();
        let mut copied = Vec::new();
        files
            .checked_spool(&record)
            .unwrap()
            .read_to_end(&mut copied)
            .unwrap();
        assert_eq!(copied, bytes);
        assert_eq!(receipt.digest, format!("{:x}", Sha256::digest(bytes)));
        let json = serde_json::to_string(&files.status().unwrap()).unwrap();
        assert!(!json.contains("fixture-secret"));
        assert!(!json.contains(&root.path().to_string_lossy().to_string()));
        assert!(!json.contains(&files.scope().unwrap().binding));
    }
    #[test]
    fn changed_native_spool_cannot_be_uploaded() {
        let (root, _, files) = fixture();
        let r = select(root.path(), &files, b"original");
        files
            .root
            .write(format!("{}.bin", r.transfer_id), b"modified")
            .unwrap();
        assert!(files
            .checked_spool(
                &files
                    .record(&r.transfer_id, &files.scope().unwrap())
                    .unwrap()
            )
            .is_err());
    }
    #[test]
    fn selection_refuses_directories_oversize_and_invalid_names() {
        let (root, _, files) = fixture();
        let scope = files.scope().unwrap();
        assert!(files.spool_file(&scope, "draft", root.path(), 1).is_err());
        let path = root.path().join("big.dat");
        File::create(&path).unwrap().set_len(1024).unwrap();
        assert!(files.spool_file(&scope, "draft", &path, 100).is_err());
        for name in ["..", ".git", "a/b", "a\\b", "a:b", "a\n"] {
            assert!(!safe_name(name));
        }
        assert!(files.status().unwrap().transfers.is_empty());
    }
    #[cfg(unix)]
    #[test]
    fn symlink_selection_is_refused_without_touching_the_target() {
        let (root, _, files) = fixture();
        let source = root.path().join("real");
        std::fs::write(&source, b"source").unwrap();
        let link = root.path().join("link");
        std::os::unix::fs::symlink(&source, &link).unwrap();
        assert!(files
            .spool_file(&files.scope().unwrap(), "draft", &link, 100)
            .is_err());
        assert_eq!(std::fs::read(&source).unwrap(), b"source");
    }
    #[test]
    fn native_cache_is_bounded_by_actual_files() {
        let (root, _, files) = fixture();
        let orphan = id();
        files
            .root
            .create(format!("{orphan}.bin"))
            .unwrap()
            .set_len(MAX_SPOOL)
            .unwrap();
        let source = root.path().join("small");
        std::fs::write(&source, b"x").unwrap();
        assert!(files
            .spool_file(&files.scope().unwrap(), "draft", &source, 100)
            .err()
            .unwrap()
            .contains("full"));
        assert_eq!(std::fs::read(source).unwrap(), b"x");
    }
    #[test]
    fn restart_retains_exact_intent_and_does_not_replay_it() {
        let (root, bridge, files) = fixture();
        let r = select(root.path(), &files, b"persist");
        let (_, op) = op_record(&files, &r);
        files
            .accept_operation(&r.transfer_id, &files.scope().unwrap(), &op)
            .unwrap();
        files
            .update(&r.transfer_id, |r| r.receipt.stage = "uploading".into())
            .unwrap();
        let snapshot = files
            .record(&r.transfer_id, &files.scope().unwrap())
            .unwrap();
        drop(files);
        let recovered = Files::open(&root.path().join("cache"), bridge).unwrap();
        let actual = recovered
            .record(&r.transfer_id, &recovered.scope().unwrap())
            .unwrap();
        assert_eq!(actual.request, snapshot.request);
        assert_eq!(actual.receipt.intent_key, r.intent_key);
        assert_eq!(actual.receipt.operation_id, snapshot.receipt.operation_id);
        assert_eq!(actual.receipt.stage, "stopped");
        assert!(recovered.state.lock().unwrap().busy.is_empty());
        assert!(recovered.checked_spool(&actual).is_ok());
    }
    #[test]
    fn same_claims_different_credential_cannot_recover_original_transfer() {
        let (root, _, files) = fixture();
        let receipt = select(root.path(), &files, b"private");
        let replacement = Arc::new(Bridge::file_fixture(
            "http://127.0.0.1:9/",
            "different-secret",
        ));
        let reopened = Files::open(&root.path().join("cache"), replacement).unwrap();
        assert!(reopened.status().unwrap().transfers.is_empty());
        assert!(reopened.start_upload(&receipt.transfer_id).is_err());
        assert!(reopened
            .record(&receipt.transfer_id, &reopened.scope().unwrap())
            .is_err());
    }
    #[test]
    fn disconnect_invalidates_source_and_destination_capabilities() {
        let (root, bridge, files) = fixture();
        let scope = files.scope().unwrap();
        bridge.disconnect();
        let source = root.path().join("file");
        std::fs::write(&source, b"untouched").unwrap();
        assert!(files.spool_file(&scope, "draft", &source, 100).is_err());
        assert!(bridge.check_file_scope(&scope).is_err());
        assert_eq!(std::fs::read(source).unwrap(), b"untouched");
    }
    #[test]
    fn every_operation_receipt_field_is_bound_to_original_intent() {
        let (root, _, files) = fixture();
        let r = select(root.path(), &files, b"content");
        let (record, op) = op_record(&files, &r);
        assert!(verify_operation(&record, &op).is_ok());
        for field in [
            "idempotency_key",
            "actor",
            "action",
            "target",
            "params",
            "preconditions",
            "operation_id",
        ] {
            let mut wrong = op.clone();
            wrong[field] = json!("wrong");
            assert!(verify_operation(&record, &wrong).is_err(), "{field}");
        }
        files
            .accept_operation(&r.transfer_id, &files.scope().unwrap(), &op)
            .unwrap();
        let record = files
            .record(&r.transfer_id, &files.scope().unwrap())
            .unwrap();
        let mut other = op;
        other["operation_id"] = json!(format!("op_{}", "b".repeat(32)));
        assert!(verify_operation(&record, &other).is_err());
    }
    #[test]
    fn save_never_replaces_a_destination_that_appears_after_dialog() {
        let (root, _, files) = fixture();
        let r = select(root.path(), &files, b"artifact");
        let path = root.path().join("destination");
        let destination = Destination::from_selection(&path).unwrap();
        std::fs::write(&path, b"editor contents").unwrap();
        assert!(destination
            .publish(
                files
                    .root
                    .open(format!("{}.bin", r.transfer_id))
                    .unwrap()
                    .into_std(),
                &r,
                || Ok(())
            )
            .is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"editor contents");
        assert!(Destination::from_selection(&path).is_err());
        assert!(!std::fs::read_dir(root.path()).unwrap().any(|e| e
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".batc-")));
    }
    #[test]
    fn save_requires_matching_digest_and_live_permission() {
        let (root, _, files) = fixture();
        let r = select(root.path(), &files, b"artifact");
        let path = root.path().join("destination");
        let destination = Destination::from_selection(&path).unwrap();
        let mut wrong = r.clone();
        wrong.digest = "0".repeat(64);
        assert!(destination
            .publish(
                files
                    .root
                    .open(format!("{}.bin", r.transfer_id))
                    .unwrap()
                    .into_std(),
                &wrong,
                || Ok(())
            )
            .is_err());
        assert!(!path.exists());
        assert!(destination
            .publish(
                files
                    .root
                    .open(format!("{}.bin", r.transfer_id))
                    .unwrap()
                    .into_std(),
                &r,
                || Err(STOPPED.into())
            )
            .is_err());
        assert!(!path.exists());
        destination
            .publish(
                files
                    .root
                    .open(format!("{}.bin", r.transfer_id))
                    .unwrap()
                    .into_std(),
                &r,
                || Ok(()),
            )
            .unwrap();
        assert_eq!(std::fs::read(path).unwrap(), b"artifact");
    }
    #[test]
    fn restart_cleans_only_owned_uncommitted_cache_files() {
        let (root, bridge, files) = fixture();
        let receipt = select(root.path(), &files, b"keep");
        let orphan = id();
        files
            .root
            .write(format!("{orphan}.bin"), b"orphan")
            .unwrap();
        files
            .root
            .write(format!("{}.part", receipt.transfer_id), b"interrupted")
            .unwrap();
        drop(files);
        let recovered = Files::open(&root.path().join("cache"), bridge.clone()).unwrap();
        assert!(!recovered.root.exists(format!("{orphan}.bin")));
        assert!(!recovered
            .root
            .exists(format!("{}.part", receipt.transfer_id)));
        assert!(recovered
            .root
            .exists(format!("{}.bin", receipt.transfer_id)));
        let other = root.path().join("unclaimed");
        std::fs::create_dir(&other).unwrap();
        std::fs::write(other.join("user-file"), b"untouched").unwrap();
        assert!(Files::open(&other, bridge).is_err());
        assert_eq!(
            std::fs::read(other.join("user-file")).unwrap(),
            b"untouched"
        );
    }
    #[test]
    fn previews_are_literal_text_or_bounded_reencoded_png() {
        let literal = "<script>alert(1)</script><svg onload='x'/># markdown";
        let text = decode_preview(literal.as_bytes().to_vec()).unwrap();
        assert_eq!(text.text.as_deref(), Some(literal));
        assert!(text.base64.is_none());
        assert!(decode_preview(vec![b'x'; MAX_PREVIEW as usize + 1]).is_err());
        assert!(decode_preview(vec![0, 255]).is_err());
        let mut image = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut image, 1, 1);
            encoder.set_color(png::ColorType::Rgb);
            encoder.set_depth(png::BitDepth::Eight);
            encoder
                .add_text_chunk("comment".into(), "private-metadata".into())
                .unwrap();
            let mut writer = encoder.write_header().unwrap();
            writer.write_image_data(&[255, 0, 0]).unwrap();
        }
        let preview = decode_preview(image).unwrap();
        assert_eq!(preview.media_type, "image/png");
        let sanitized = base64::engine::general_purpose::STANDARD
            .decode(preview.base64.unwrap())
            .unwrap();
        assert!(!String::from_utf8_lossy(&sanitized).contains("private-metadata"));
        let mut large = Vec::new();
        {
            let encoder = png::Encoder::new(&mut large, 100_000, 100_000);
            let _writer = encoder.write_header().unwrap();
        }
        assert!(decode_preview(large).is_err());
    }
    #[test]
    fn damaged_intent_cannot_be_repurposed_or_replace_durable_state() {
        let (root, _, files) = fixture();
        let r = select(root.path(), &files, b"original");
        assert!(files
            .update(&r.transfer_id, |record| record.request = Some(
                json!({"action":"task.run","target":{},"params":{},"preconditions":{}})
            ))
            .is_err());
        assert!(files
            .record(&r.transfer_id, &files.scope().unwrap())
            .unwrap()
            .request
            .is_none());
        let path = root
            .path()
            .join("cache")
            .join(format!("{}.json", r.transfer_id));
        let mut corrupt: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        corrupt["request"] = json!({"action":"task.run"});
        std::fs::write(&path, corrupt.to_string()).unwrap();
        assert!(Files::open(&root.path().join("cache"), files.bridge.clone()).is_err());
        assert!(root
            .path()
            .join("cache")
            .join(format!("{}.bin", r.transfer_id))
            .exists());
    }
    #[tokio::test]
    async fn stale_or_other_draft_drop_arm_cannot_grant_selection() {
        let (_, _, files) = fixture();
        files.drop_target("first".into(), true).unwrap();
        files.drop_target("second".into(), true).unwrap();
        files.drop_target("first".into(), false).unwrap();
        assert_eq!(
            files.state.lock().unwrap().drop_target.as_ref().unwrap().1,
            "second"
        );
        files.state.lock().unwrap().drop_target.as_mut().unwrap().2 =
            std::time::Instant::now() - Duration::from_secs(3);
        files
            .clone()
            .dropped(vec![PathBuf::from("does-not-exist")])
            .await;
        assert!(files.state.lock().unwrap().drop_target.is_none());
        assert!(files.state.lock().unwrap().drop_error.is_none());
        assert!(files.status().unwrap().transfers.is_empty());
    }
    #[test]
    fn packaged_files_have_no_generic_fs_dialog_event_or_raw_byte_permission() {
        let capability: Value =
            serde_json::from_str(include_str!("../capabilities/dashboard.json")).unwrap();
        let permissions = capability["permissions"].as_array().unwrap();
        for permission in permissions {
            let p = permission.as_str().unwrap();
            assert!(
                !p.starts_with("fs:")
                    && !p.starts_with("dialog:")
                    && !p.starts_with("core:event:")
                    && p != "allow-connector-upload-artifact"
            );
        }
        assert!(permissions.contains(&json!("allow-native-files-pick")));
        assert!(permissions.contains(&json!("allow-native-files-save")));
    }
    #[test]
    fn artifact_reference_is_exact_not_a_content_url_or_path() {
        let reference = ArtifactRef {
            artifact_id: format!("art_{}", "a".repeat(32)),
            revision: 1,
            digest: "b".repeat(64),
        };
        assert!(reference.validate().is_ok());
        for field in ["content_url", "path", "token"] {
            let mut value = serde_json::to_value(&reference).unwrap();
            value[field] = json!("forbidden");
            assert!(serde_json::from_value::<ArtifactRef>(value).is_err());
        }
        let mut wrong = reference;
        wrong.artifact_id = "../secret".into();
        assert!(wrong.validate().is_err());
    }
}

#[cfg(test)]
mod actual_central {
    use super::*;
    use std::{
        io::{BufRead, BufReader},
        process::{Command, Stdio},
    };
    struct Fixture(std::process::Child);
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    #[ignore = "run with BATC_NATIVE_TEST_PYTHON using the documented temporary fixture"]
    async fn native_transfer_real_central() {
        let python = std::env::var("BATC_NATIVE_TEST_PYTHON")
            .expect("BATC_NATIVE_TEST_PYTHON must identify a dev-dependencies interpreter");
        let repo = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap();
        let mut fixture = Fixture(
            Command::new(python)
                .arg(repo.join("desktop/tests/native-files-fixture.py"))
                .current_dir(repo)
                .env(
                    "PYTHONPATH",
                    format!(
                        "{}{}{}",
                        repo.join("src").display(),
                        if cfg!(windows) { ";" } else { ":" },
                        repo.display()
                    ),
                )
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit())
                .spawn()
                .unwrap(),
        );
        let mut output = BufReader::new(fixture.0.stdout.take().unwrap());
        let mut line = String::new();
        output.read_line(&mut line).unwrap();
        let configuration: Value =
            serde_json::from_str(&line).expect("temporary central did not start");
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("central.json"),json!({"endpoint":configuration["endpoint"],"expected_actor":"native-file-fixture","contract_version":"2026-10-08"}).to_string()).unwrap();
        let bridge = Arc::new(Bridge::load(
            root.path(),
            zeroize::Zeroizing::new(configuration["token"].as_str().unwrap().into()),
        ));
        bridge.connect().await.unwrap();
        let files = Files::open(&root.path().join("cache"), bridge.clone()).unwrap();
        let scope = files.scope().unwrap();
        let bytes = [0, 255, 128, 13, 10, 60, 38, 34, 195, 169];
        let source = root.path().join("binary.dat");
        std::fs::write(&source, bytes).unwrap();
        let receipts = files
            .pick_paths(scope.clone(), "fixture-draft", vec![source.clone()])
            .await
            .unwrap();
        let receipt = &receipts[0];
        // Admission succeeded but its reply was lost before the client could persist an ID.
        let request = json!({"action":"artifact.upload","target":{},"params":{"display_name":receipt.display_name,"media_type":receipt.media_type,"size_bytes":receipt.size_bytes,"expected_digest":receipt.digest},"preconditions":{}});
        files
            .update(&receipt.transfer_id, |r| r.request = Some(request.clone()))
            .unwrap();
        let accepted = files
            .request(
                &scope,
                "POST",
                "/operations?wait=0".into(),
                Some(request),
                Some(receipt.intent_key.clone()),
            )
            .await
            .unwrap();
        assert!(files
            .record(&receipt.transfer_id, &scope)
            .unwrap()
            .receipt
            .operation_id
            .is_none());
        tokio::time::timeout(
            Duration::from_secs(30),
            files.upload(&receipt.transfer_id, &scope, false),
        )
        .await
        .unwrap()
        .unwrap();
        let complete = files.record(&receipt.transfer_id, &scope).unwrap().receipt;
        assert_eq!(complete.stage, "ready");
        assert_eq!(
            complete.operation_id.as_deref(),
            accepted["operation"]["operation_id"].as_str()
        );
        assert_eq!(complete.intent_key, receipt.intent_key);
        let reference = complete.artifact.unwrap();
        let metadata = files.metadata(&scope, &reference).await.unwrap();
        let destination = root.path().join("saved.dat");
        let download = files
            .save(
                scope.clone(),
                reference.clone(),
                metadata,
                Destination::from_selection(&destination).unwrap(),
                None,
            )
            .unwrap();
        tokio::time::timeout(Duration::from_secs(20), async {
            loop {
                let current = files.record(&download.transfer_id, &scope).unwrap().receipt;
                if current.stage == "saved" {
                    break;
                }
                assert_ne!(current.stage, "stopped", "{:?}", current.error);
                tokio::time::sleep(Duration::from_millis(25)).await;
            }
        })
        .await
        .unwrap();
        assert_eq!(std::fs::read(&destination).unwrap(), bytes);
        assert_eq!(std::fs::read(&source).unwrap(), bytes);
        assert!(Destination::from_selection(&destination).is_err());
        // Binary content is never interpreted as an active document.
        assert!(files.preview(&reference).await.is_err());
        // Bytes arrived, but the content reply was lost. A restarted adapter reads back the
        // accepted ID and never sends the immutable bytes a second time.
        let text = b"<script>not executable</script>";
        let text_source = root.path().join("preview.html");
        std::fs::write(&text_source, text).unwrap();
        let second = files
            .pick_paths(scope.clone(), "fixture-second", vec![text_source.clone()])
            .await
            .unwrap()
            .remove(0);
        files.update(&second.transfer_id,|r|r.request=Some(json!({"action":"artifact.upload","target":{},"params":{"display_name":r.receipt.display_name,"media_type":r.receipt.media_type,"size_bytes":r.receipt.size_bytes,"expected_digest":r.receipt.digest},"preconditions":{}}))).unwrap();
        let accepted = tokio::time::timeout(Duration::from_secs(15), async {
            loop {
                let op = files.operation(&second.transfer_id, &scope).await.unwrap();
                if op["status"] == "waiting_external" {
                    break op;
                }
                tokio::time::sleep(Duration::from_millis(25)).await;
            }
        })
        .await
        .unwrap();
        let response = bridge
            .file_content(
                &scope,
                &format!(
                    "/artifacts/uploads/{}/content",
                    accepted["operation_id"].as_str().unwrap()
                ),
                Some((reqwest::Body::from(text.to_vec()), text.len() as u64)),
            )
            .await
            .unwrap();
        assert!(response.status().is_success());
        drop(response);
        let recovered = Files::open(&root.path().join("cache"), bridge.clone()).unwrap();
        tokio::time::timeout(
            Duration::from_secs(30),
            recovered.upload(&second.transfer_id, &scope, false),
        )
        .await
        .unwrap()
        .unwrap();
        let second_done = recovered
            .record(&second.transfer_id, &scope)
            .unwrap()
            .receipt;
        assert_eq!(second_done.intent_key, second.intent_key);
        assert_eq!(
            second_done.operation_id.as_deref(),
            accepted["operation_id"].as_str()
        );
        let preview = recovered
            .preview(second_done.artifact.as_ref().unwrap())
            .await
            .unwrap();
        assert_eq!(
            preview.text.as_deref(),
            Some(std::str::from_utf8(text).unwrap())
        );
        assert_eq!(std::fs::read(text_source).unwrap(), text);
        let cancelled = files
            .pick_paths(scope.clone(), "fixture-cancel", vec![source.clone()])
            .await
            .unwrap()
            .remove(0);
        files
            .update(&cancelled.transfer_id, |r| {
                r.request = Some(upload_intent(&r.receipt))
            })
            .unwrap();
        tokio::time::timeout(Duration::from_secs(15), async {
            loop {
                let op = files
                    .operation(&cancelled.transfer_id, &scope)
                    .await
                    .unwrap();
                if op["status"] == "waiting_external" {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(25)).await;
            }
        })
        .await
        .unwrap();
        files
            .control(&cancelled.transfer_id, Control::CancelUpload)
            .await
            .unwrap();
        tokio::time::timeout(Duration::from_secs(15), async {
            loop {
                let receipt = files
                    .record(&cancelled.transfer_id, &scope)
                    .unwrap()
                    .receipt;
                if receipt.stage == "cancelled" {
                    break;
                }
                assert_ne!(receipt.stage, "stopped", "{:?}", receipt.error);
                tokio::time::sleep(Duration::from_millis(25)).await;
            }
        })
        .await
        .unwrap();
        fixture
            .0
            .stdin
            .as_mut()
            .unwrap()
            .write_all(b"{\"action\":\"proof\"}\n")
            .unwrap();
        line.clear();
        output.read_line(&mut line).unwrap();
        let proof: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(proof["source_unchanged"], true);
        assert_eq!(proof["operations"].as_array().unwrap().len(), 3);
        let mut attempts = proof["upload_attempts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_u64().unwrap())
            .collect::<Vec<_>>();
        attempts.sort();
        assert_eq!(attempts, vec![0, 1, 1]);
        assert_eq!(proof["operations"][0]["status"], "succeeded");
        assert_eq!(proof["operations"][1]["status"], "succeeded");
        assert_eq!(proof["operations"][2]["status"], "cancelled");
        fixture
            .0
            .stdin
            .as_mut()
            .unwrap()
            .write_all(b"{\"action\":\"stop\"}\n")
            .unwrap();
        assert!(fixture.0.wait().unwrap().success());
    }
}
