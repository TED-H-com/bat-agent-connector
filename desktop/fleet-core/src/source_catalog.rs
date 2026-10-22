//! Bounded current-account BAT listing. No IPC, locks, writes or BAT contact.
//! This is deliberately separate from the Kit's reviewed-copy drift validator.
use crate::{
    configuration,
    credential::{read_fixed, AdmissionStore, TokenStatus},
    strict_json,
};
use serde::Serialize;
use serde_json::Value;
use std::{collections::HashMap, net::Ipv6Addr, path::Path};

pub const INDEX_BOUND: usize = 1024 * 1024;
pub const PROFILE_BOUND: usize = 200;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Endpoint {
    pub host: String,
    pub port: u16,
    pub url: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RemoteProfile {
    pub id: String,
    pub name: String,
    pub endpoint: Endpoint,
    pub fingerprint: String,
    pub remote_profile_id: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SkippedProfile {
    pub entry: usize,
    pub reason: &'static str,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ProfileListing {
    pub profiles: Vec<RemoteProfile>,
    pub reason: &'static str,
    pub skipped: Vec<SkippedProfile>,
}
impl ProfileListing {
    fn unavailable(reason: &'static str) -> Self {
        Self {
            profiles: vec![],
            reason,
            skipped: vec![],
        }
    }
}
fn text(value: &Value, maximum: usize) -> Option<&str> {
    value.as_str().filter(|s| {
        !s.is_empty()
            && s.trim() == *s
            && s.chars().count() <= maximum
            && !s.chars().any(char::is_control)
    })
}
fn default_text<'a>(value: &'a Value, default: &'a str) -> Option<&'a str> {
    // Match the listing's historical absent/false/empty default, not coercion.
    match value {
        Value::Null | Value::Bool(false) => Some(default),
        Value::String(s) if s.is_empty() => Some(default),
        Value::Number(n) if n.as_f64() == Some(0.0) => Some(default),
        Value::Array(a) if a.is_empty() => Some(default),
        Value::Object(o) if o.is_empty() => Some(default),
        _ => text(value, 256),
    }
}
fn remote_profile(profile: &Value) -> Option<RemoteProfile> {
    let id = text(&profile["id"], 256)?;
    let host = text(&profile["remoteHost"], 256)?;
    let host = if let Some(inner) = host.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
        if !inner.contains(':') {
            return None;
        }
        inner
    } else {
        host
    };
    if host.is_empty()
        || host
            .chars()
            .any(|c| c.is_whitespace() || "/\\@?#[]%".contains(c))
    {
        return None;
    }
    let url_host = if host.contains(':') {
        host.parse::<Ipv6Addr>().ok()?;
        format!("[{host}]")
    } else {
        host.to_owned()
    };
    let port = match &profile["remotePort"] {
        Value::String(s)
            if text(&profile["remotePort"], 256).is_some()
                && s.bytes().all(|b| b.is_ascii_digit()) =>
        {
            s.parse::<u16>().ok()?
        }
        value => value.as_u64()?.try_into().ok()?,
    };
    if port == 0 {
        return None;
    }
    let fp = text(&profile["remoteFingerprint"], 200)?.to_uppercase();
    let fp = fp
        .strip_prefix("SHA256:")
        .unwrap_or(&fp)
        .replace([':', ' '], "");
    if fp.len() != 64 || !fp.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    Some(RemoteProfile {
        id: id.into(),
        name: default_text(&profile["name"], id)?.into(),
        endpoint: Endpoint {
            host: host.into(),
            port,
            url: format!("wss://{url_host}:{port}/"),
        },
        fingerprint: fp,
        remote_profile_id: default_text(&profile["remoteProfileId"], "default")?.into(),
    })
}

/// Every accepted item has the complete endpoint and pin. Unknown additive fields
/// are ignored; bad items have bounded positional diagnostics with no raw input.
pub fn parse_profile_index(bytes: &[u8]) -> ProfileListing {
    let invalid = || ProfileListing::unavailable("PROFILE_INDEX_INVALID");
    let Ok(index) = strict_json::parse(bytes, INDEX_BOUND) else {
        return invalid();
    };
    let Some(root) = index.as_object() else {
        return invalid();
    };
    let empty = vec![];
    let profiles = match root.get("profiles") {
        None => &empty,
        Some(value) => match value.as_array().filter(|p| p.len() <= PROFILE_BOUND) {
            Some(profiles) => profiles,
            None => return invalid(),
        },
    };
    let mut ids = HashMap::<String, usize>::new();
    for profile in profiles {
        if let Some(id) = profile.get("id").and_then(Value::as_str) {
            *ids.entry(id.to_lowercase()).or_default() += 1;
        }
    }
    let mut out = ProfileListing::unavailable("NO_COMPLETE_PROFILES");
    for (entry, profile) in profiles.iter().enumerate() {
        let reason = if profile.get("type").and_then(Value::as_str) != Some("remote") {
            "PROFILE_NOT_REMOTE"
        } else if profile
            .get("id")
            .and_then(Value::as_str)
            .is_some_and(|id| ids[&id.to_lowercase()] > 1)
        {
            "PROFILE_ID_AMBIGUOUS"
        } else if let Some(profile) = remote_profile(profile) {
            out.profiles.push(profile);
            continue;
        } else {
            "PROFILE_INCOMPLETE"
        };
        out.skipped.push(SkippedProfile { entry, reason });
    }
    if !out.profiles.is_empty() {
        out.reason = if out.skipped.is_empty() {
            "PROFILE_CATALOG_AVAILABLE"
        } else {
            "PROFILE_INDEX_PARTIAL"
        };
    }
    out
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CatalogProfile {
    #[serde(flatten)]
    pub profile: RemoteProfile,
    pub token: TokenStatus,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Catalog {
    pub profiles: Vec<CatalogProfile>,
    pub reason: &'static str,
    pub skipped: Vec<SkippedProfile>,
}
impl Catalog {
    fn unavailable(reason: &'static str) -> Self {
        Self {
            profiles: vec![],
            reason,
            skipped: vec![],
        }
    }
}
pub fn parse(index: &[u8], store: &AdmissionStore) -> Catalog {
    let listing = parse_profile_index(index);
    Catalog {
        profiles: listing
            .profiles
            .into_iter()
            .map(|profile| CatalogProfile {
                token: store.lookup(&profile.id),
                profile,
            })
            .collect(),
        reason: listing.reason,
        skipped: listing.skipped,
    }
}

/// Select new then legacy exactly once. Never fall back after selecting a root.
/// Caller runs blocking IO off the UI thread; errors contain no paths/OS text.
pub fn read(roaming: &Path) -> Catalog {
    let directory = match configuration::data_directory(roaming) {
        Ok(directory) => directory,
        Err(reason) => return Catalog::unavailable(reason),
    };
    match std::fs::read_dir(&directory) {
        Ok(_) => (),
        Err(error) => {
            return Catalog::unavailable(if error.kind() == std::io::ErrorKind::NotFound {
                "DATA_DIRECTORY_UNAVAILABLE"
            } else {
                "DATA_DIRECTORY_UNREADABLE"
            })
        }
    }
    let index = match read_fixed(&directory, "profiles", "index.json", INDEX_BOUND) {
        Ok(index) => index,
        Err(_) => return Catalog::unavailable("PROFILE_INDEX_INVALID"),
    };
    let store = match read_fixed(
        &directory,
        "profiles",
        "remote-tokens.enc.json",
        INDEX_BOUND,
    ) {
        Ok(bytes) => AdmissionStore::parse(&bytes),
        Err(_) => AdmissionStore::unreadable(),
    };
    parse(&index, &store)
}

#[cfg(test)]
#[path = "source_catalog_tests.rs"]
mod tests;
