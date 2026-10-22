//! Resolve only native inventory references in the reviewed Fleet Kit formats.
//! No IPC, environment lookup, desktop-vault fallback, writes or credential logging.
use crate::{inventory::Inventory, probe::ProbeCredential, strict_json, Result};
use serde_json::Value;
use std::{
    fs::{File, Metadata, OpenOptions},
    io::Read,
    path::Path,
};
#[cfg(windows)]
use zeroize::Zeroize;
use zeroize::Zeroizing;

const MISSING: &str = "CREDENTIAL_MISSING";
const REFERENCE: &str = "CREDENTIAL_REFERENCE_INVALID";
pub const STORE_BOUND: usize = 1024 * 1024;
const PROTECTED_BOUND: usize = 256 * 1024;
const TOKEN_BOUND: usize = 64 * 1024;

/// Shared BAT admission version. Absent references retain legacy Fleet semantics.
pub const BAT_SOURCE_POLICY: u64 = 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize)]
pub struct TokenStatus {
    pub state: &'static str,
    pub reason: &'static str,
}
impl TokenStatus {
    fn new(state: &'static str, reason: &'static str) -> Self {
        Self { state, reason }
    }
}

/// The secret tree deliberately has no Debug or serialization implementation.
/// Only fixed status codes leave lookup; resolution copies one zeroizing token.
pub struct AdmissionStore {
    status: TokenStatus,
    inner: Option<SecretJson>,
}
impl AdmissionStore {
    fn status(state: &'static str, reason: &'static str) -> Self {
        Self {
            status: TokenStatus::new(state, reason),
            inner: None,
        }
    }
    pub fn unreadable() -> Self {
        Self::status("unavailable", "TOKEN_STORE_UNREADABLE")
    }
    pub fn parse(bytes: &[u8]) -> Self {
        if bytes.len() > STORE_BOUND || std::str::from_utf8(bytes).is_err() {
            return Self::unreadable();
        }
        match Self::decode(bytes) {
            Ok(store) => store,
            Err(reason) => Self::status("unsupported", reason),
        }
    }
    fn decode(bytes: &[u8]) -> Result<Self> {
        fn decode(bytes: &[u8]) -> Result<SecretJson> {
            strict_json::parse_classified(bytes, STORE_BOUND)
                .map(SecretJson)
                .map_err(|reason| {
                    if reason == "JSON_AMBIGUOUS" {
                        "TOKEN_STORE_AMBIGUOUS"
                    } else {
                        "TOKEN_STORE_FORMAT"
                    }
                })
        }
        // Unlike legacy Fleet, admission does not remove an outer or inner BOM.
        let outer = decode(bytes)?;
        if !outer.0.is_object() {
            return Err("TOKEN_STORE_FORMAT");
        }
        if outer.0.get("enc") != Some(&Value::Bool(false)) {
            return Ok(Self::status("encrypted", "TOKEN_STORE_ENCRYPTED"));
        }
        let data = outer.0["data"].as_str().ok_or("TOKEN_DATA_NOT_STRING")?;
        let inner = decode(data.as_bytes())?;
        if !inner.0.is_object() && !inner.0.is_null() {
            return Err("TOKEN_STORE_FORMAT");
        }
        if let Some(tokens) = inner.0.get("tokens") {
            if !tokens.is_object() && !tokens.is_null() {
                return Err("TOKEN_STORE_FORMAT");
            }
        }
        Ok(Self {
            status: TokenStatus::new("available", "TOKEN_STORE_AVAILABLE"),
            inner: Some(inner),
        })
    }
    fn tokens(&self) -> Option<&serde_json::Map<String, Value>> {
        self.inner.as_ref()?.0.get("tokens")?.as_object()
    }
    pub fn lookup(&self, profile_id: &str) -> TokenStatus {
        if self.status.state != "available" {
            return self.status;
        }
        let Some(tokens) = self.tokens() else {
            return TokenStatus::new("unavailable", "TOKEN_MISSING");
        };
        let Some(token) = tokens.get(profile_id) else {
            let reason = if tokens
                .keys()
                .any(|key| key.to_lowercase() == profile_id.to_lowercase())
            {
                "TOKEN_KEY_MISMATCH"
            } else {
                "TOKEN_MISSING"
            };
            return TokenStatus::new("unavailable", reason);
        };
        let Some(token) = token.as_str().filter(|token| !token.is_empty()) else {
            return TokenStatus::new("unavailable", "TOKEN_INVALID");
        };
        if token.len() > TOKEN_BOUND || token.contains('\0') {
            return TokenStatus::new("unsupported", "TOKEN_INVALID");
        }
        TokenStatus::new("available", "TOKEN_AVAILABLE")
    }
    fn token(&self, profile_id: &str) -> Result<Zeroizing<String>> {
        let status = self.lookup(profile_id);
        if status.state != "available" {
            return Err(status.reason);
        }
        let token = self
            .tokens()
            .and_then(|tokens| tokens.get(profile_id))
            .and_then(Value::as_str)
            .ok_or("TOKEN_MISSING")?;
        Ok(Zeroizing::new(token.to_owned()))
    }
}

// Neither the reference (private profile/name) nor resolved credential implements
// Debug/Serialize. A caller can obtain references only from a validated inventory.
enum Source {
    BatProfile {
        profile_id: String,
        source_policy: Option<u64>,
    },
    Dpapi {
        name: String,
    },
}
pub struct CredentialRef {
    source: Source,
}
impl CredentialRef {
    pub fn for_bat(inventory: &Inventory, host: &str) -> Result<Self> {
        let doc = inventory.document();
        let entry = doc["hosts"]
            .as_array()
            .ok_or(REFERENCE)?
            .iter()
            .find(|h| h["name"].as_str() == Some(host))
            .ok_or(REFERENCE)?;
        let value = lookup(doc, &entry["bat"]["credential_ref"])?;
        let profile = value["profile_id"]
            .as_str()
            .filter(|p| !p.is_empty())
            .ok_or(REFERENCE)?;
        if value["kind"] != "bat-profile-token" || entry["profile"] != profile {
            return Err(REFERENCE);
        }
        Ok(Self {
            source: Source::BatProfile {
                profile_id: profile.into(),
                source_policy: value
                    .get("source_policy")
                    .map(|v| {
                        v.as_u64()
                            .filter(|v| *v == BAT_SOURCE_POLICY)
                            .ok_or(REFERENCE)
                    })
                    .transpose()?,
            },
        })
    }
    /// This is Fleet's own observe credential reference, not the authenticated
    /// desktop user's token. Readiness must still prove exactly observe scope.
    pub fn for_connector(inventory: &Inventory) -> Result<Self> {
        let doc = inventory.document();
        let value = lookup(doc, &doc["connector"]["credential_ref"])?;
        let name = value["name"]
            .as_str()
            .filter(|n| valid_name(n))
            .ok_or(REFERENCE)?;
        if value["kind"] != "windows-dpapi" {
            return Err(REFERENCE);
        }
        Ok(Self {
            source: Source::Dpapi { name: name.into() },
        })
    }
}
fn lookup<'a>(doc: &'a Value, key: &Value) -> Result<&'a Value> {
    let key = key.as_str().ok_or(REFERENCE)?;
    doc["credentials"]
        .as_object()
        .ok_or(REFERENCE)?
        .iter()
        .find(|(name, _)| name.eq_ignore_ascii_case(key))
        .map(|(_, v)| v)
        .ok_or(REFERENCE)
}
fn valid_name(name: &str) -> bool {
    (1..=64).contains(&name.len())
        && name.as_bytes()[0].is_ascii_alphanumeric()
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
}

/// data_dir is the native supervisor's already-selected BAT data directory; the
/// resolver does not search another directory or another credential on failure.
/// Held regular files have strict size bounds. Descendant links/reparse points
/// are refused. This is not a transaction against an editor replacing ancestors.
pub fn resolve(data_dir: &Path, reference: &CredentialRef) -> Result<ProbeCredential> {
    if !cfg!(windows) && matches!(&reference.source, Source::Dpapi { .. }) {
        return Err(MISSING);
    }
    let token = match &reference.source {
        Source::BatProfile {
            profile_id,
            source_policy,
        } => {
            let reader = if source_policy.is_some() {
                read_private_fixed
            } else {
                read_fixed
            };
            let bytes = reader(data_dir, "profiles", "remote-tokens.enc.json", STORE_BOUND)
                .map_err(|reason| {
                    if source_policy.is_some() {
                        "TOKEN_STORE_UNREADABLE"
                    } else {
                        reason
                    }
                })?;
            if source_policy.is_some() {
                AdmissionStore::parse(&bytes).token(profile_id)?
            } else {
                profile_token(&bytes, profile_id)?
            }
        }
        Source::Dpapi { name } => {
            if !valid_name(name) {
                return Err(REFERENCE);
            }
            let bytes = read_fixed(
                data_dir,
                "fleet-credentials",
                &format!("{name}.dpapi"),
                PROTECTED_BOUND,
            )?;
            let protected = decode_hex(&bytes)?;
            let plaintext = unprotect(&protected)?;
            decode_utf16(&plaintext)?
        }
    };
    checked_token(token).map(ProbeCredential::new_zeroizing)
}
fn checked_token(token: Zeroizing<String>) -> Result<Zeroizing<String>> {
    if token.is_empty() || token.len() > TOKEN_BOUND || token.contains('\0') {
        return Err(MISSING);
    }
    Ok(token)
}
fn no_link(metadata: &Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return false;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        // Includes junctions and other reparse forms, not just symbolic links.
        if metadata.file_attributes() & 0x400 != 0 {
            return false;
        }
    }
    true
}
pub(crate) fn read_fixed(
    data_dir: &Path,
    directory: &str,
    name: &str,
    bound: usize,
) -> Result<Zeroizing<Vec<u8>>> {
    read_fixed_policy(data_dir, directory, name, bound, false)
}
pub(crate) fn read_private_fixed(
    data_dir: &Path,
    directory: &str,
    name: &str,
    bound: usize,
) -> Result<Zeroizing<Vec<u8>>> {
    read_fixed_policy(data_dir, directory, name, bound, true)
}
fn read_fixed_policy(
    data_dir: &Path,
    directory: &str,
    name: &str,
    bound: usize,
    private: bool,
) -> Result<Zeroizing<Vec<u8>>> {
    if !data_dir.is_absolute() {
        return Err(MISSING);
    }
    let root = data_dir.canonicalize().map_err(|_| MISSING)?;
    if !root.is_dir() {
        return Err(MISSING);
    }
    let parent = root.join(directory);
    let metadata = parent.symlink_metadata().map_err(|_| MISSING)?;
    if !metadata.is_dir() || !no_link(&metadata) {
        return Err(MISSING);
    }
    let path = parent.join(name);
    let metadata = path.symlink_metadata().map_err(|_| MISSING)?;
    if !metadata.is_file() || !no_link(&metadata) || private && !private_metadata(&metadata) {
        return Err(MISSING);
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // A replaced FIFO must not block the caller; a replaced symlink is not followed.
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x0020_0000); // FILE_FLAG_OPEN_REPARSE_POINT
    }
    let file = options.open(path).map_err(|_| MISSING)?;
    if private {
        read_regular_policy(file, bound, true)
    } else {
        read_regular(file, bound)
    }
}
fn read_regular(file: File, bound: usize) -> Result<Zeroizing<Vec<u8>>> {
    read_regular_policy(file, bound, false)
}
#[cfg(unix)]
fn private_owner_mode(metadata: &Metadata, uid: u32) -> bool {
    use std::os::unix::fs::MetadataExt;
    metadata.uid() == uid && metadata.mode() & 0o077 == 0
}
fn private_metadata(metadata: &Metadata) -> bool {
    #[cfg(unix)]
    {
        private_owner_mode(metadata, unsafe { libc::getuid() })
    }
    #[cfg(not(unix))]
    {
        let _ = metadata;
        true
    }
}
fn read_regular_policy(mut file: File, bound: usize, private: bool) -> Result<Zeroizing<Vec<u8>>> {
    let meta = file.metadata().map_err(|_| MISSING)?;
    if !meta.is_file()
        || !no_link(&meta)
        || meta.len() > bound as u64
        || private && !private_metadata(&meta)
    {
        return Err(MISSING);
    }
    let length = meta.len() as usize;
    let mut out = Zeroizing::new(Vec::with_capacity(length + 1));
    out.resize(length + 1, 0);
    // Fixed slice reads cannot reallocate. Refuse growth past the observed size
    // rather than leaving old copies of a growing secret buffer behind.
    let mut used = 0;
    while used < out.len() {
        let read = match file.read(&mut out[used..]) {
            Ok(read) => read,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(MISSING),
        };
        if read == 0 {
            break;
        }
        used += read;
    }
    if used > length {
        return Err(MISSING);
    }
    out.truncate(used);
    Ok(out)
}
/// Successful parsed secret trees are scrubbed, including other profiles. Parser
/// failure internals and transport/OS temporary copies are outside this guarantee.
struct SecretJson(Value);
impl Drop for SecretJson {
    fn drop(&mut self) {
        scrub(&mut self.0);
    }
}
fn scrub(value: &mut Value) {
    strict_json::scrub(value);
}
fn profile_token(bytes: &[u8], profile_id: &str) -> Result<Zeroizing<String>> {
    // UTF8 BOM matches .NET ReadAllText; Kit writes UTF8 without a BOM.
    let bytes = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(bytes);
    let outer = SecretJson(strict_json::parse(bytes, STORE_BOUND).map_err(|_| MISSING)?);
    // Do not coerce null, numeric zero, missing enc, or string "false" to false.
    if outer.0.get("enc") != Some(&Value::Bool(false)) {
        return Err(MISSING);
    }
    let data = outer.0["data"].as_str().ok_or(MISSING)?;
    let inner = SecretJson(strict_json::parse(data.as_bytes(), STORE_BOUND).map_err(|_| MISSING)?);
    let tokens = inner.0["tokens"].as_object().ok_or(MISSING)?;
    // PowerShell property lookup ignores case; strict_json already rejects any
    // competing decoded case/escape spelling, so identity remains unambiguous.
    let token = tokens
        .iter()
        .find(|(id, _)| id.eq_ignore_ascii_case(profile_id))
        .and_then(|(_, token)| token.as_str())
        .ok_or(MISSING)?;
    checked_token(Zeroizing::new(token.to_owned()))
}
fn decode_hex(bytes: &[u8]) -> Result<Zeroizing<Vec<u8>>> {
    let bytes = bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(bytes);
    if bytes.is_empty()
        || bytes.len() > PROTECTED_BOUND
        || !bytes.len().is_multiple_of(2)
        || !bytes.iter().all(u8::is_ascii_hexdigit)
    {
        return Err(MISSING);
    }
    fn nibble(b: u8) -> u8 {
        if b <= b'9' {
            b - b'0'
        } else {
            b.to_ascii_lowercase() - b'a' + 10
        }
    }
    Ok(Zeroizing::new(
        bytes
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| nibble(pair[0]) * 16 + nibble(pair[1]))
            .collect(),
    ))
}
fn decode_utf16(bytes: &[u8]) -> Result<Zeroizing<String>> {
    if bytes.is_empty() || bytes.len() > TOKEN_BOUND * 2 || !bytes.len().is_multiple_of(2) {
        return Err(MISSING);
    }
    let words = Zeroizing::new(
        bytes
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect::<Vec<_>>(),
    );
    let mut text = Zeroizing::new(String::new());
    for ch in char::decode_utf16(words.iter().copied()) {
        text.push(ch.map_err(|_| MISSING)?);
    }
    checked_token(text)
}
#[cfg(not(windows))]
fn unprotect(_: &[u8]) -> Result<Zeroizing<Vec<u8>>> {
    Err(MISSING)
}
#[cfg(windows)]
fn unprotect(bytes: &[u8]) -> Result<Zeroizing<Vec<u8>>> {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::Cryptography::{
            CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
        },
    };
    struct Output(CRYPT_INTEGER_BLOB);
    impl Drop for Output {
        fn drop(&mut self) {
            unsafe {
                if !self.0.pbData.is_null() {
                    // Windows allocated this buffer. Zero even an oversized successful output.
                    std::slice::from_raw_parts_mut(self.0.pbData, self.0.cbData as usize).zeroize();
                    LocalFree(self.0.pbData.cast());
                }
            }
        }
    }
    let input = CRYPT_INTEGER_BLOB {
        cbData: bytes.len().try_into().map_err(|_| MISSING)?,
        pbData: bytes.as_ptr().cast_mut(),
    };
    let mut output = Output(CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: null_mut(),
    });
    let ok = unsafe {
        CryptUnprotectData(
            &input,
            null_mut(),
            null(),
            null(),
            null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output.0,
        )
    };
    if ok == 0
        || output.0.pbData.is_null()
        || output.0.cbData == 0
        || output.0.cbData as usize > TOKEN_BOUND * 2
    {
        return Err(MISSING);
    }
    Ok(Zeroizing::new(
        unsafe { std::slice::from_raw_parts(output.0.pbData, output.0.cbData as usize) }.to_vec(),
    ))
}

#[cfg(test)]
#[path = "credential_tests.rs"]
mod tests;
