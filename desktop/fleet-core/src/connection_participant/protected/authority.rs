use super::*;
use crate::creation_evidence::{Creator, StartupBinding};
use serde::{de::DeserializeOwned, Deserialize, Serialize};

pub const ROOT_LIMIT: usize = 16 * 1024;
pub const LEVEL_LIMIT: usize = 8 * 1024;
/// Native-resolved state and startup record; decoding never establishes provenance.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub state: PathBuf,
    pub state_identity: ObjectIdentity,
    pub startup_record: PathBuf,
    pub startup: StartupBinding,
}
fn path_valid(path: &Path) -> bool {
    path.is_absolute()
        && path.to_str().is_some_and(|s| {
            s.len() <= 4096
                && s.encode_utf16().count() <= 4096
                && !s.contains('\0')
                && !s.split(['/', '\\']).any(|s| s == "." || s == "..")
        })
}
fn identity_valid(id: &ObjectIdentity) -> bool {
    hex(&id.file_id, "", 32)
}
impl Context {
    pub(super) fn validate(&self) -> Result<()> {
        ensure(
            path_valid(&self.state)
                && path_valid(&self.startup_record)
                && identity_valid(&self.state_identity)
                && uuid(&self.startup.installation_ref)
                && hex(&self.startup.role_record_sha256, "", 64)
                && self.startup.generation > 0,
        )
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Level(String);
impl Level {
    pub fn participant() -> Self {
        Self("participant".into())
    }
    pub fn scope(scope: &str) -> Result<Self> {
        Self::parse(&format!("participant/{scope}"))
    }
    pub fn operation(scope: &str, operation: &str) -> Result<Self> {
        Self::parse(&format!("participant/{scope}/{operation}"))
    }
    fn parse(path: &str) -> Result<Self> {
        let parts: Vec<_> = path.split('/').collect();
        ensure(match parts.as_slice() {
            ["participant"] => true,
            ["participant", s] => hex(s, "", 64),
            ["participant", s, o] => hex(s, "", 64) && hex(o, "op_", 32),
            _ => false,
        })?;
        Ok(Self(path.into()))
    }
    pub fn relative(&self) -> &str {
        &self.0
    }
    pub fn record(&self, root: &Path) -> PathBuf {
        root.join(&self.0)
            .parent()
            .unwrap()
            .join(format!(".directory-{}.json", digest(self.0.as_bytes())))
    }
    pub fn stage(&self, root: &Path) -> PathBuf {
        self.record(root).with_extension("stage")
    }
    pub(super) fn parent(&self) -> Option<Self> {
        self.0.rsplit_once('/').map(|(p, _)| Self(p.into()))
    }
}
pub(super) trait Document: Serialize + DeserializeOwned {
    fn validate(&self) -> Result<()>;
}
pub(super) fn decode<T: Document>(bytes: &[u8], bound: usize) -> Result<T> {
    let value = crate::strict_json::parse(bytes, bound).map_err(|_| Error::Invalid)?;
    let version = value["format_version"].as_u64().ok_or(Error::Invalid)?;
    let reader = value["minimum_reader_version"]
        .as_u64()
        .ok_or(Error::Invalid)?;
    ensure(version > 0 && reader > 0)?;
    if version != 1 || reader > 1 {
        return Err(Error::Unsupported);
    }
    let result: T = serde_json::from_value(value).map_err(|_| Error::Invalid)?;
    result.validate()?;
    Ok(result)
}
pub(super) fn encode(value: &impl Document, bound: usize) -> Result<Vec<u8>> {
    value.validate()?;
    let bytes = serde_json::to_vec(value).map_err(|_| Error::Invalid)?;
    ensure(bytes.len() <= bound)?;
    Ok(bytes)
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RootReceipt {
    pub format_version: u32,
    pub minimum_reader_version: u32,
    pub context: Context,
    pub creator: Creator,
    pub root: PathBuf,
    pub identity: ObjectIdentity,
}
impl Document for RootReceipt {
    fn validate(&self) -> Result<()> {
        self.context.validate()?;
        ensure(
            self.format_version == 1
                && self.minimum_reader_version == 1
                && self.root == self.context.state.join("connections-v1")
                && path_valid(&self.root)
                && identity_valid(&self.identity)
                && crate::ownership::sid_valid(&self.creator.owner_sid)
                && hex(&self.creator.reservation_id, "", 32),
        )
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct DirectoryRecord {
    pub format_version: u32,
    pub minimum_reader_version: u32,
    pub root_digest: String,
    pub root_id: String,
    pub relative_path: String,
    pub parent_identity: ObjectIdentity,
    pub identity: ObjectIdentity,
    pub owner_sid: String,
    pub session_id: u32,
}
impl Document for DirectoryRecord {
    fn validate(&self) -> Result<()> {
        Level::parse(&self.relative_path)?;
        ensure(
            self.format_version == 1
                && self.minimum_reader_version == 1
                && hex(&self.root_digest, "", 64)
                && hex(&self.root_id, "", 32)
                && identity_valid(&self.parent_identity)
                && identity_valid(&self.identity)
                && crate::ownership::sid_valid(&self.owner_sid),
        )
    }
}
