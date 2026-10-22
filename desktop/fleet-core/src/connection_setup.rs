//! Pure, native-only configuration proposals. No filesystem, environment, clock,
//! credential resolution, process or network calls. Callers supply read evidence;
//! a plan is not ownership attestation or permission to publish/start anything.
use crate::{
    digest,
    inventory::endpoint,
    source_catalog::{self, Catalog, RemoteProfile},
    strict_json,
};
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    net::{Ipv4Addr, SocketAddrV4},
};

pub const GENERATOR_VERSION: u32 = 1;
pub const ALLOCATION_VERSION: u32 = 1;
pub const SSH_BYTE_LIMIT: usize = 1024 * 1024;
pub const SSH_LINE_LIMIT: usize = 8192;
pub const SSH_LINE_LENGTH: usize = 4096;

/// Fixed reasons only: raw paths, directives and credential material never enter errors.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Gap {
    MissingMapping,
    DuplicateForwards,
    UnsupportedInclude,
    UnsupportedMatch,
    UnsupportedSshSyntax,
    SshTooLarge,
    SshTooManyLines,
    SshLineTooLong,
    SshEncoding,
    AmbiguousProfile,
    ProfileUnavailable,
    CredentialUnavailable,
    ProfileEndpointMismatch,
    SourceChanged,
    OccupiedEndpoint,
    PoolExhausted,
    ReviewChanged,
    UnknownOwner,
    ReferenceOnly(Artifact),
    InvalidExisting(Artifact),
    InvalidOutput(Artifact),
    InvalidInput,
    IdentityChanged,
}
pub type SetupResult<T> = std::result::Result<T, Gap>;

fn literal(value: &str, maximum: usize) -> bool {
    !value.is_empty()
        && value.len() <= maximum
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ForwardAssociation {
    alias: String,
    local: SocketAddrV4,
    target: String,
    source_digest: String,
}
impl ForwardAssociation {
    pub fn alias(&self) -> &str {
        &self.alias
    }
    pub fn local(&self) -> SocketAddrV4 {
        self.local
    }
    pub fn target(&self) -> &str {
        &self.target
    }
    pub fn source_digest(&self) -> &str {
        &self.source_digest
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SshReferences {
    source_digest: String,
    blocks: Vec<(String, Vec<(SocketAddrV4, String)>)>,
}
impl SshReferences {
    /// Multiple blocks/forwards are not deduplicated: OpenSSH may bind both.
    pub fn association(&self, alias: &str) -> SetupResult<ForwardAssociation> {
        let blocks: Vec<_> = self
            .blocks
            .iter()
            .filter(|(name, _)| name.eq_ignore_ascii_case(alias))
            .collect();
        if blocks.len() > 1 || blocks.iter().any(|(_, forwards)| forwards.len() > 1) {
            return Err(Gap::DuplicateForwards);
        }
        let (name, forwards) = blocks.first().ok_or(Gap::MissingMapping)?;
        let (local, target) = forwards.first().ok_or(Gap::MissingMapping)?;
        Ok(ForwardAssociation {
            alias: name.clone(),
            local: *local,
            target: target.clone(),
            source_digest: self.source_digest.clone(),
        })
    }
}

/// A conservative subset, not an OpenSSH evaluator. Includes (including cycles)
/// and Match are refused without opening another file or executing a directive.
/// Other directives remain solely in the original authority's exact bytes.
pub fn read_ssh(bytes: &[u8]) -> SetupResult<SshReferences> {
    if bytes.len() > SSH_BYTE_LIMIT {
        return Err(Gap::SshTooLarge);
    }
    let text = std::str::from_utf8(bytes).map_err(|_| Gap::SshEncoding)?;
    let mut out = SshReferences {
        source_digest: digest(bytes),
        blocks: vec![],
    };
    for (number, line) in text.lines().enumerate() {
        if number >= SSH_LINE_LIMIT {
            return Err(Gap::SshTooManyLines);
        }
        if line.len() > SSH_LINE_LENGTH {
            return Err(Gap::SshLineTooLong);
        }
        if line.chars().any(|c| c.is_control() && c != '\t') {
            return Err(Gap::UnsupportedSshSyntax);
        }
        let line = line.split('#').next().unwrap().trim();
        if line.is_empty() {
            continue;
        }
        let mut parts = line.splitn(2, |c: char| c.is_ascii_whitespace() || c == '=');
        let key = parts.next().unwrap().to_ascii_lowercase();
        let value = parts
            .next()
            .unwrap_or("")
            .trim()
            .trim_start_matches('=')
            .trim();
        match key.as_str() {
            "include" => return Err(Gap::UnsupportedInclude),
            "match" => return Err(Gap::UnsupportedMatch),
            "host" => {
                if !literal(value, 128) {
                    return Err(Gap::UnsupportedSshSyntax);
                }
                out.blocks.push((value.into(), vec![]));
            }
            "localforward" => {
                let parts: Vec<_> = value.split_whitespace().collect();
                if parts.len() != 2 {
                    return Err(Gap::UnsupportedSshSyntax);
                }
                let local = endpoint(parts[0], true).map_err(|_| Gap::UnsupportedSshSyntax)?;
                endpoint(parts[1], false).map_err(|_| Gap::UnsupportedSshSyntax)?;
                let block = out.blocks.last_mut().ok_or(Gap::UnsupportedSshSyntax)?;
                block.1.push((
                    SocketAddrV4::new(local.address.parse().unwrap(), local.port),
                    parts[1].into(),
                ));
            }
            _ => (),
        }
    }
    Ok(out)
}

/// A selected catalog record plus supported BAT metadata and reference keys.
/// Deliberately has no token field and does not keep the raw profile index.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SelectedProfile {
    profile: RemoteProfile,
    record: Value,
    index_digest: String,
    credential_ref: String,
    fingerprint_ref: String,
}
impl SelectedProfile {
    pub fn from_catalog(catalog: &Catalog, index: &[u8], id: &str) -> SetupResult<Self> {
        let raw = strict_json::parse(index, source_catalog::INDEX_BOUND)
            .map_err(|_| Gap::ProfileUnavailable)?;
        let records = raw["profiles"].as_array().ok_or(Gap::ProfileUnavailable)?;
        if records
            .iter()
            .filter(|r| r["id"].as_str().is_some_and(|p| p.eq_ignore_ascii_case(id)))
            .count()
            != 1
            || catalog
                .profiles
                .iter()
                .filter(|p| p.profile.id.eq_ignore_ascii_case(id))
                .count()
                > 1
        {
            return Err(Gap::AmbiguousProfile);
        }
        let selected = catalog
            .profiles
            .iter()
            .find(|p| p.profile.id == id)
            .ok_or(Gap::ProfileUnavailable)?;
        let listing = source_catalog::parse_profile_index(index);
        if !listing.profiles.contains(&selected.profile) {
            return Err(Gap::SourceChanged);
        }
        if selected.token.state != "available" {
            return Err(Gap::CredentialUnavailable);
        }
        let p = &selected.profile;
        if !literal(&p.id, 64) || p.id.eq_ignore_ascii_case("default") {
            return Err(Gap::ProfileUnavailable);
        }
        let source = records.iter().find(|v| v["id"] == id).unwrap();
        let mut record = json!({"id":p.id,"name":p.name,"type":"remote", "remoteHost":p.endpoint.host,
            "remotePort":p.endpoint.port,"remoteProfileId":p.remote_profile_id,"remoteFingerprint":p.fingerprint});
        for field in ["createdAt", "updatedAt", "remoteProfileName"] {
            if let Some(value) = source.get(field) {
                record[field] = value.clone();
            }
        }
        let suffix = &digest(id.as_bytes())[..24];
        Ok(Self {
            profile: p.clone(),
            record,
            index_digest: digest(index),
            credential_ref: format!("bat-{suffix}"),
            fingerprint_ref: format!("pin-{suffix}"),
        })
    }
    pub fn profile(&self) -> &RemoteProfile {
        &self.profile
    }
    pub fn credential_ref(&self) -> &str {
        &self.credential_ref
    }
    pub fn fingerprint_ref(&self) -> &str {
        &self.fingerprint_ref
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProvenAssociation {
    forward: ForwardAssociation,
}
impl ProvenAssociation {
    pub fn new(
        profile: &SelectedProfile,
        forward: Option<&ForwardAssociation>,
    ) -> SetupResult<Self> {
        let forward = forward.ok_or(Gap::MissingMapping)?;
        if profile.profile.endpoint.host != forward.local.ip().to_string()
            || profile.profile.endpoint.port != forward.local.port()
        {
            return Err(Gap::ProfileEndpointMismatch);
        }
        Ok(Self {
            forward: forward.clone(),
        })
    }
}

/// Addresses, not just ports: inventory requires distinct BAT loopback addresses.
/// Include wildcard listeners as 0.0.0.0; adapters must also map dual-stack binds.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AddressUse {
    pub configured: BTreeSet<Ipv4Addr>,
    pub listening: BTreeSet<Ipv4Addr>,
}
impl AddressUse {
    fn occupied(&self, address: &Ipv4Addr) -> bool {
        self.configured.contains(address)
            || self.listening.contains(address)
            || self.listening.contains(&Ipv4Addr::UNSPECIFIED)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Allocation {
    pub version: u32,
    pub local: SocketAddrV4,
}
impl Allocation {
    /// Preview binds this choice. Recheck cannot choose a different address.
    pub fn recheck(&self, addresses: &AddressUse) -> SetupResult<()> {
        if addresses.occupied(self.local.ip()) {
            Err(Gap::ReviewChanged)
        } else {
            Ok(())
        }
    }
}
/// Only callable with a complete, matching BAT/forward association. This proposes
/// an address, never creates a BAT profile or changes the original SSH mapping.
pub fn allocate_loopback(
    proven: &ProvenAssociation,
    addresses: &AddressUse,
) -> SetupResult<Allocation> {
    (2..=254)
        .map(|last| Ipv4Addr::new(127, 0, 0, last))
        .find(|ip| !addresses.occupied(ip))
        .map(|ip| Allocation {
            version: ALLOCATION_VERSION,
            local: SocketAddrV4::new(ip, proven.forward.local.port()),
        })
        .ok_or(Gap::PoolExhausted)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Artifact {
    Inventory,
    ProfileIndex,
    SshReference,
    Installation,
}
impl Artifact {
    pub fn relative_path(self) -> &'static str {
        match self {
            Self::Inventory => "client/fleet-inventory.json",
            Self::ProfileIndex => "client/bat-profiles/index.json",
            Self::SshReference => "client/ssh-config",
            Self::Installation => "fleet.json",
        }
    }
}
