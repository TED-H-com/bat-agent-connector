//! Resolve only native inventory references in the reviewed Fleet Kit formats.
//! No IPC, environment lookup, desktop-vault fallback, writes or credential logging.
use crate::{inventory::Inventory, probe::ProbeCredential, strict_json, Result};
use serde_json::Value;
use std::{
    fs::{File, Metadata, OpenOptions},
    io::Read,
    path::Path,
};
use zeroize::{Zeroize, Zeroizing};

const MISSING: &str = "CREDENTIAL_MISSING";
const REFERENCE: &str = "CREDENTIAL_REFERENCE_INVALID";
const STORE_BOUND: usize = 1024 * 1024;
const PROTECTED_BOUND: usize = 256 * 1024;
const TOKEN_BOUND: usize = 64 * 1024;

// Red baseline adapter: this demonstrates why the existing runtime is not admission.
#[cfg(test)]
struct AdmissionStore(Vec<u8>);
#[cfg(test)]
impl AdmissionStore {
    fn parse(bytes: &[u8]) -> Self { Self(bytes.to_vec()) }
    fn lookup(&self, id: &str) -> serde_json::Value {
        match profile_token(&self.0, id) {
            Ok(_) => serde_json::json!({"state":"available","reason":"TOKEN_AVAILABLE"}),
            Err(reason) => serde_json::json!({"state":"unavailable","reason":reason}),
        }
    }
}

// Neither the reference (private profile/name) nor resolved credential implements
// Debug/Serialize. A caller can obtain references only from a validated inventory.
enum Source {
    BatProfile { profile_id: String },
    Dpapi { name: String },
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
        Source::BatProfile { profile_id } => {
            let bytes = read_fixed(data_dir, "profiles", "remote-tokens.enc.json", STORE_BOUND)?;
            profile_token(&bytes, profile_id)?
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
    if !metadata.is_file() || !no_link(&metadata) {
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
    read_regular(file, bound)
}
fn read_regular(file: File, bound: usize) -> Result<Zeroizing<Vec<u8>>> {
    let meta = file.metadata().map_err(|_| MISSING)?;
    if !meta.is_file() || !no_link(&meta) || meta.len() > bound as u64 {
        return Err(MISSING);
    }
    let mut out = Zeroizing::new(Vec::new());
    file.take(bound as u64 + 1)
        .read_to_end(&mut out)
        .map_err(|_| MISSING)?;
    if out.len() > bound {
        return Err(MISSING);
    }
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
    match value {
        Value::String(s) => s.zeroize(),
        Value::Array(values) => values.iter_mut().for_each(scrub),
        Value::Object(values) => {
            for (mut key, mut value) in std::mem::take(values) {
                key.zeroize();
                scrub(&mut value);
            }
        }
        _ => (),
    }
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
