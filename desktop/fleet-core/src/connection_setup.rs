//! Pure, native-only configuration proposals. No filesystem, environment, clock,
//! credential resolution, process or network calls. Callers supply read evidence;
//! a plan is not ownership attestation or permission to publish/start anything.
use crate::{
    credential::BAT_SOURCE_POLICY,
    digest,
    inventory::{configuration_issues, endpoint, Endpoint, Inventory, ProfileIndex},
    ownership::{epoch_valid, sid_valid},
    source_catalog::{self, Catalog, RemoteProfile},
    strict_json,
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    net::{Ipv4Addr, SocketAddrV4},
    path::{Component, PathBuf},
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
        let matches = records
            .iter()
            .filter(|r| r["id"].as_str().is_some_and(|p| p.eq_ignore_ascii_case(id)))
            .count();
        if matches == 0 {
            return Err(Gap::ProfileUnavailable);
        }
        if matches > 1
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

/// Configured BAT hosts reserve addresses, as required by inventory uniqueness.
/// Listeners reserve sockets: 0.0.0.0 conflicts with all addresses on that port
/// only. Adapters map a dual-stack [::]:port listener to 0.0.0.0:port.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AddressUse {
    pub configured: BTreeSet<Ipv4Addr>,
    pub listening: BTreeSet<SocketAddrV4>,
}
impl AddressUse {
    fn occupied(&self, local: &SocketAddrV4) -> bool {
        self.configured.contains(local.ip())
            || self.listening.contains(local)
            || self
                .listening
                .contains(&SocketAddrV4::new(Ipv4Addr::UNSPECIFIED, local.port()))
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
        if addresses.occupied(&self.local) {
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
        .map(|last| SocketAddrV4::new(Ipv4Addr::new(127, 0, 0, last), proven.forward.local.port()))
        .find(|local| !addresses.occupied(local))
        .map(|local| Allocation {
            version: ALLOCATION_VERSION,
            local,
        })
        .ok_or(Gap::PoolExhausted)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, serde::Serialize)]
pub enum Artifact {
    Inventory,
    ProfileIndex,
    SshReference,
    Installation,
}

/// Native caller's existing creation evidence, never inferred from a path.
/// Publication must re-prove this under the installation lock, with the existing
/// installation/migration ownership gates. These are references, not new receipts.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct OwnerEvidence {
    pub installation_id: String,
    pub owner_sid: String,
    pub creation_receipt: String,
}
impl OwnerEvidence {
    fn valid(&self) -> bool {
        epoch_valid(&self.installation_id)
            && sid_valid(&self.owner_sid)
            && literal(&self.creation_receipt, 256)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub enum FileOwnership {
    Owned {
        owner: OwnerEvidence,
        artifact: Artifact,
        sha256: String,
    },
    ReferenceOnly,
    Unknown,
}

/// Exact bytes as read by the caller, including its independently recorded digest
/// and ownership evidence. Absence is represented by no entry in `existing`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExistingFile {
    pub bytes: Vec<u8>,
    pub sha256: String,
    pub ownership: FileOwnership,
}

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct SourceReference {
    pub key: String,
    pub sha256: String,
}

/// Reference to a separately enrolled exact-observe DPAPI handle. Enrollment,
/// principal verification and enabling a new external Connector are separate
/// native steps; the generator never accepts its token body.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ObserveHandleReference {
    pub name: String,
    pub principal: SourceReference,
    pub enrollment_receipt: SourceReference,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RouteKind {
    Direct { probe: Endpoint },
    Lan { probe: Endpoint },
    Cloudflare { access_host: String },
}
/// The caller supplies an already authorized route reference, not an editor's
/// host/port text. Kind/probe/access-host provenance is bound into the preview.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReviewedRoute {
    pub source: SourceReference,
    pub kind: RouteKind,
}
impl ReviewedRoute {
    fn document(&self, alias: &str) -> Value {
        let ep = |p: &Endpoint| format!("{}:{}", p.address, p.port);
        match &self.kind {
            RouteKind::Direct { probe } => json!({"kind":"direct","alias":alias,"probe":ep(probe)}),
            RouteKind::Lan { probe } => json!({"kind":"lan","alias":alias,"probe":ep(probe)}),
            RouteKind::Cloudflare { access_host } => {
                json!({"kind":"cf","alias":alias,"access_host":access_host})
            }
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct WorkspaceBinding {
    pub id: String,
    pub workspace_id: String,
    pub required: bool,
}

/// These four choices stay independent. They are returned for the existing
/// selection/startup owners; only BAT windows affect this profile index.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct Preferences {
    pub background_connections: bool,
    pub bat_windows: bool,
    pub dashboard: bool,
    pub login: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EndpointChoice {
    Preserve,
    Allocate,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReviewedSetup {
    /// Protected root chosen/read by native; never a WebView-supplied path.
    pub root: PathBuf,
    pub owner: OwnerEvidence,
    pub selected: SelectedProfile,
    pub forward: Option<ForwardAssociation>,
    pub ssh_source: Vec<u8>,
    pub route: ReviewedRoute,
    pub bindings: Vec<WorkspaceBinding>,
    pub label: String,
    pub preferences: Preferences,
    pub endpoint_choice: EndpointChoice,
    /// Additional configured addresses outside the retained connection, plus
    /// all current listener sockets. Existing BAT addresses and the Connector's
    /// exact socket are also reserved by the builder.
    pub addresses: AddressUse,
    pub existing: BTreeMap<Artifact, ExistingFile>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Output {
    pub artifact: Artifact,
    pub bytes: Vec<u8>,
    pub sha256: String,
}
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct PublicationStep {
    pub artifact: Artifact,
    pub before_digest: Option<String>,
    pub after_digest: String,
    pub owner: OwnerEvidence,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PublicationPlan {
    pub generator_version: u32,
    pub root: PathBuf,
    pub outputs: Vec<Output>,
    pub steps: Vec<PublicationStep>,
    pub sources: Vec<SourceReference>,
    pub preferences: Preferences,
    pub allocation: Option<Allocation>,
    /// A reference artifact cannot authorize the current tunnel launcher, which
    /// adds -L while system SSH may already have the same LocalForward.
    pub reference_route_only: bool,
    pub review_digest: String,
}
impl PublicationPlan {
    pub fn output(&self, artifact: Artifact) -> &Output {
        self.outputs
            .iter()
            .find(|output| output.artifact == artifact)
            .unwrap()
    }
    /// Read-only preview check. Never returns a replacement plan/address on
    /// conflict. Applied-step reconciliation belongs to the durable publisher.
    pub fn recheck(&self, current: &ReviewedSetup) -> SetupResult<()> {
        let candidate = build(current).map_err(|_| Gap::ReviewChanged)?;
        if candidate.review_digest == self.review_digest {
            Ok(())
        } else {
            Err(Gap::ReviewChanged)
        }
    }
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn limit(artifact: Artifact) -> usize {
    if artifact == Artifact::Installation {
        16384
    } else {
        crate::profile_files::LIMIT
    }
}
fn encode(value: &Value, artifact: Artifact) -> SetupResult<Vec<u8>> {
    let bytes = serde_json::to_vec_pretty(value).map_err(|_| Gap::InvalidOutput(artifact))?;
    if bytes.len() > limit(artifact) {
        return Err(Gap::InvalidOutput(artifact));
    }
    Ok(bytes)
}
fn current_json(input: &ReviewedSetup, artifact: Artifact) -> SetupResult<Option<Value>> {
    input
        .existing
        .get(&artifact)
        .map(|file| {
            strict_json::parse(&file.bytes, limit(artifact))
                .map_err(|_| Gap::InvalidExisting(artifact))
        })
        .transpose()
}
fn index_parse(bytes: &[u8]) -> SetupResult<ProfileIndex> {
    let value = strict_json::parse(bytes, source_catalog::INDEX_BOUND)
        .map_err(|_| Gap::InvalidExisting(Artifact::ProfileIndex))?;
    let fields: Vec<_> = value
        .as_object()
        .ok_or(Gap::InvalidExisting(Artifact::ProfileIndex))?
        .keys()
        .map(String::as_str)
        .collect();
    ProfileIndex::parse(bytes, &fields).map_err(|_| Gap::InvalidExisting(Artifact::ProfileIndex))
}

// Versioned generator policy, matching the existing public inventory contract.
// Inactive values are parser-required placeholders, never advertised live routes.
fn initial_inventory() -> Value {
    json!({"schema_version":1,"fleet_version":"3.2.5","direct_probe_ms":600,
        "cloudflare_access_hosts":[],"credentials":{"inactive-connector-observe":{"kind":"windows-dpapi","name":"inactive-connector-observe"}},
        "fingerprints":{},"hosts":[],"provisioning_profiles":[],
        "connector":{"name":"connector","label":"Connector","local":"127.0.0.1:19796","target":"127.0.0.1:18796",
        "ssh_alias":null,"dashboard_path":"/dashboard/","credential_ref":"inactive-connector-observe",
        "supported_api_versions":[1],"min_contract_version":"2026-10-08","required_features":["inventory","events_stream"]}})
}

fn validate_inputs(input: &ReviewedSetup) -> SetupResult<()> {
    if !input.owner.valid() {
        return Err(Gap::UnknownOwner);
    }
    // Lexical only. No canonicalize, drive query or filesystem call in a builder.
    if !input.root.is_absolute()
        || input.root.to_str().is_none()
        || input
            .root
            .components()
            .any(|p| matches!(p, Component::ParentDir | Component::CurDir))
        || input.root.to_string_lossy().contains('\0')
        || !literal(&input.route.source.key, 256)
        || !valid_digest(&input.route.source.sha256)
    {
        return Err(Gap::InvalidInput);
    }
    for (artifact, file) in &input.existing {
        if file.bytes.len() > limit(*artifact) || digest(&file.bytes) != file.sha256 {
            return Err(Gap::SourceChanged);
        }
        match &file.ownership {
            FileOwnership::Owned {
                owner,
                artifact: bound,
                sha256,
            } if owner == &input.owner && bound == artifact && sha256 == &file.sha256 => (),
            FileOwnership::ReferenceOnly => (),
            _ => return Err(Gap::UnknownOwner),
        }
    }
    Ok(())
}

fn installation_document(input: &ReviewedSetup) -> SetupResult<Value> {
    if let Some(value) = current_json(input, Artifact::Installation)? {
        let map = value
            .as_object()
            .ok_or(Gap::InvalidExisting(Artifact::Installation))?;
        if map.keys().any(|k| k != "kit_root" && k != "backend")
            || !matches!(
                value.get("backend").and_then(Value::as_str),
                None | Some("rust" | "powershell")
            )
            || value.get("backend").is_some_and(|b| !b.is_string())
        {
            return Err(Gap::InvalidExisting(Artifact::Installation));
        }
        if value["kit_root"] != json!(input.root) {
            return Err(Gap::IdentityChanged);
        }
        Ok(value)
    } else {
        Ok(json!({"kit_root":input.root,"backend":"rust"}))
    }
}

fn insert_reference(table: &mut Value, key: &str, value: Value) -> SetupResult<()> {
    let table = table.as_object_mut().ok_or(Gap::InvalidInput)?;
    if let Some((existing, old)) = table.iter().find(|(k, _)| k.eq_ignore_ascii_case(key)) {
        if existing != key || old != &value {
            return Err(Gap::IdentityChanged);
        }
    } else {
        table.insert(key.into(), value);
    }
    Ok(())
}

fn update_inventory(
    input: &ReviewedSetup,
    inventory: &mut Value,
    forward: &ForwardAssociation,
) -> SetupResult<()> {
    let p = &input.selected;
    let route = input.route.document(&forward.alias);
    let kind = route["kind"].clone();
    let top_probe = if kind == "direct" {
        route["probe"].clone()
    } else {
        Value::Null
    };
    let old = inventory["hosts"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|h| {
            h["profile"]
                .as_str()
                .is_some_and(|id| id.eq_ignore_ascii_case(&p.profile.id))
        });
    if let Some(host) = old {
        // Preserve IDs, references, compatibility policy and Connector metadata;
        // choose among existing routes without discarding their other references.
        if host["profile"] != p.profile.id
            || host["local"] != forward.local.to_string()
            || host["target"] != forward.target
            || host["bat"]["remote_profile_id"] != p.profile.remote_profile_id
            || !host["routes"].as_array().unwrap().contains(&route)
        {
            return Err(Gap::IdentityChanged);
        }
        host["label"] = json!(input.label);
        host["workspace_bindings"] = json!(input.bindings);
        host["provision_route"] = kind;
    } else {
        insert_reference(
            &mut inventory["credentials"],
            &p.credential_ref,
            json!({"kind":"bat-profile-token","profile_id":p.profile.id,"source_policy":BAT_SOURCE_POLICY}),
        )?;
        insert_reference(
            &mut inventory["fingerprints"],
            &p.fingerprint_ref,
            json!({"kind":"bat-profile-pin","profile_id":p.profile.id}),
        )?;
        let name = format!("bat-{}", &digest(p.profile.id.as_bytes())[..24]);
        inventory["hosts"].as_array_mut().unwrap().push(json!({"name":name,"profile":p.profile.id,"label":input.label,
            "local":forward.local.to_string(),"target":forward.target,"probe":top_probe,"routes":[route],"provision_route":kind,
            "bat":{"remote_profile_id":p.profile.remote_profile_id,"credential_ref":p.credential_ref,"fingerprint_ref":p.fingerprint_ref,
            "protocol":"bat-remote/v2","min_version":"3.2.5"},"workspace_bindings":input.bindings,"connector_endpoint":null}));
        inventory["provisioning_profiles"]
            .as_array_mut()
            .unwrap()
            .push(json!(p.profile.id));
        if let RouteKind::Cloudflare { access_host } = &input.route.kind {
            let hosts = inventory["cloudflare_access_hosts"].as_array_mut().unwrap();
            if !hosts.iter().any(|h| {
                h.as_str()
                    .is_some_and(|h| h.eq_ignore_ascii_case(access_host))
            }) {
                hosts.push(json!(access_host));
            }
        }
    }
    Ok(())
}

fn update_index(input: &ReviewedSetup, index: &mut Value) -> SetupResult<()> {
    let p = &input.selected;
    let profiles = index["profiles"].as_array_mut().unwrap();
    if let Some(old) = profiles.iter().find(|v| {
        v["id"]
            .as_str()
            .is_some_and(|id| id.eq_ignore_ascii_case(&p.profile.id))
    }) {
        let listing = source_catalog::parse_profile_index(&encode(
            &json!({"profiles":[old]}),
            Artifact::ProfileIndex,
        )?);
        let found = listing.profiles.first().ok_or(Gap::AmbiguousProfile)?;
        if found.id != p.profile.id
            || found.endpoint != p.profile.endpoint
            || found.remote_profile_id != p.profile.remote_profile_id
            || found.fingerprint != p.profile.fingerprint
        {
            return Err(Gap::IdentityChanged);
        }
    } else {
        profiles.push(p.record.clone());
    }
    let active = index["activeProfileIds"].as_array_mut().unwrap();
    if input.preferences.bat_windows {
        if !active.iter().any(|id| id == &p.profile.id) {
            active.push(json!(p.profile.id));
        }
    } else {
        active.retain(|id| id != &p.profile.id);
    }
    Ok(())
}

/// Build the complete, validated candidate before returning any publication step.
/// Each step is one bounded file with exact before/after digests. A later native
/// publisher must use compare/readback patterns like profile_files::replace;
/// this is intentionally not a cross-file transaction or a durable operation.
pub fn build(input: &ReviewedSetup) -> SetupResult<PublicationPlan> {
    validate_inputs(input)?;
    let forward = input.forward.as_ref().ok_or(Gap::MissingMapping)?;
    let readback = read_ssh(&input.ssh_source)?.association(&forward.alias)?;
    if &readback != forward {
        return Err(Gap::SourceChanged);
    }
    let proven = ProvenAssociation::new(&input.selected, Some(forward))?;
    let installation = installation_document(input)?;
    let mut inventory = match input.existing.get(&Artifact::Inventory) {
        Some(file) => Inventory::parse(&file.bytes)
            .map_err(|_| Gap::InvalidExisting(Artifact::Inventory))?
            .document()
            .clone(),
        None => initial_inventory(),
    };
    let mut index = match input.existing.get(&Artifact::ProfileIndex) {
        Some(file) => index_parse(&file.bytes)?.document().clone(),
        None => json!({"profiles":[],"activeProfileIds":[]}),
    };
    let retained = inventory["hosts"].as_array().unwrap().iter().any(|h| {
        h["profile"] == input.selected.profile.id && h["local"] == forward.local.to_string()
    });
    let mut addresses = input.addresses.clone();
    for host in inventory["hosts"].as_array().unwrap() {
        if host["profile"] != input.selected.profile.id {
            addresses.configured.insert(
                endpoint(host["local"].as_str().unwrap(), true)
                    .unwrap()
                    .address
                    .parse()
                    .unwrap(),
            );
        }
    }
    // Unlike BAT hosts, the Connector reserves only its exact endpoint, even
    // when its required inactive template is present. It is not a live listener.
    let connector = endpoint(inventory["connector"]["local"].as_str().unwrap(), true).unwrap();
    addresses.listening.insert(SocketAddrV4::new(
        connector.address.parse().unwrap(),
        connector.port,
    ));
    let allocation = if input.endpoint_choice == EndpointChoice::Allocate && !retained {
        let allocation = allocate_loopback(&proven, &addresses)?;
        // Allocating a new address cannot rewrite the catalog or source SSH. The
        // selected BAT profile and explicit forwarding association must match it.
        if allocation.local != forward.local {
            return Err(Gap::ProfileEndpointMismatch);
        }
        Some(allocation)
    } else {
        None
    };
    if !retained && addresses.occupied(&forward.local) {
        return Err(Gap::OccupiedEndpoint);
    }
    update_inventory(input, &mut inventory, forward)?;
    update_index(input, &mut index)?;
    let inventory_bytes = encode(&inventory, Artifact::Inventory)?;
    let inventory =
        Inventory::parse(&inventory_bytes).map_err(|_| Gap::InvalidOutput(Artifact::Inventory))?;
    let index_bytes = encode(&index, Artifact::ProfileIndex)?;
    let parsed_index =
        index_parse(&index_bytes).map_err(|_| Gap::InvalidOutput(Artifact::ProfileIndex))?;
    let mut ssh = input
        .existing
        .get(&Artifact::SshReference)
        .map(|file| file.bytes.clone())
        .unwrap_or_default();
    let text =
        std::str::from_utf8(&ssh).map_err(|_| Gap::InvalidExisting(Artifact::SshReference))?;
    let issues = configuration_issues(&inventory, &parsed_index, &parsed_index, text);
    if issues.iter().any(|issue| issue.code == "SSH_ALIAS_MISSING") {
        if !ssh.is_empty() && !ssh.ends_with(b"\n") {
            ssh.push(b'\n');
        }
        ssh.extend_from_slice(
            format!(
                "# Fleet reference only; system SSH remains authority.\nHost {}\n",
                forward.alias
            )
            .as_bytes(),
        );
    }
    if ssh.len() > limit(Artifact::SshReference)
        || !configuration_issues(
            &inventory,
            &parsed_index,
            &parsed_index,
            std::str::from_utf8(&ssh).unwrap(),
        )
        .is_empty()
    {
        return Err(Gap::InvalidOutput(Artifact::SshReference));
    }
    let mut outputs = vec![];
    let mut steps = vec![];
    for (artifact, mut bytes) in [
        (Artifact::Inventory, inventory_bytes),
        (Artifact::ProfileIndex, index_bytes),
        (Artifact::SshReference, ssh),
        (
            Artifact::Installation,
            encode(&installation, Artifact::Installation)?,
        ),
    ] {
        let old = input.existing.get(&artifact);
        // Semantic no-op retains even formatting of an existing JSON document.
        if artifact != Artifact::SshReference {
            if let Some(old) = old {
                if strict_json::parse(&old.bytes, limit(artifact)).ok()
                    == strict_json::parse(&bytes, limit(artifact)).ok()
                {
                    bytes = old.bytes.clone();
                }
            }
        }
        let sha256 = digest(&bytes);
        if old.is_none_or(|old| old.bytes != bytes) {
            if old.is_some_and(|old| old.ownership == FileOwnership::ReferenceOnly) {
                return Err(Gap::ReferenceOnly(artifact));
            }
            steps.push(PublicationStep {
                artifact,
                before_digest: old.map(|file| file.sha256.clone()),
                after_digest: sha256.clone(),
                owner: input.owner.clone(),
            });
        }
        outputs.push(Output {
            artifact,
            bytes,
            sha256,
        });
    }
    let sources = vec![
        SourceReference {
            key: "bat-profile-index".into(),
            sha256: input.selected.index_digest.clone(),
        },
        SourceReference {
            key: "user-ssh".into(),
            sha256: forward.source_digest.clone(),
        },
        input.route.source.clone(),
    ];
    let before: Vec<_> = input
        .existing
        .iter()
        .map(|(file, state)| json!({"file":file,"sha256":state.sha256,"ownership":state.ownership}))
        .collect();
    let after: Vec<_> = outputs
        .iter()
        .map(|out| json!({"file":out.artifact,"sha256":out.sha256}))
        .collect();
    let review = json!({"generator":GENERATOR_VERSION,"root":input.root,"owner":input.owner,"before":before,"after":after,
        "sources":sources,"preferences":input.preferences,"steps":steps,"reference_route_only":true,
        "allocation":allocation.as_ref().map(|a|json!({"version":a.version,"local":a.local.to_string()}))});
    Ok(PublicationPlan {
        generator_version: GENERATOR_VERSION,
        root: input.root.clone(),
        outputs,
        steps,
        sources,
        preferences: input.preferences.clone(),
        allocation,
        reference_route_only: true,
        review_digest: digest(&serde_json::to_vec(&review).map_err(|_| Gap::InvalidInput)?),
    })
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
