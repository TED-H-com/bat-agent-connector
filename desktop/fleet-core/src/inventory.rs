//! Native port of the reviewed Fleet Kit inventory/profile contract. No secrets in errors.
use crate::{credential::BAT_SOURCE_POLICY, strict_json, Result};
use regex::Regex;
use serde_json::{Map, Value};
use std::{collections::HashSet, net::Ipv4Addr};

const MAX_DOCUMENT: usize = 1_048_576;
const INVALID: &str = "INVENTORY_INVALID";

#[derive(Clone)]
pub struct Inventory {
    document: Value,
}

fn fields<'a>(
    v: &'a Value,
    required: &[&str],
    optional: &[&str],
) -> Result<&'a Map<String, Value>> {
    let map = v.as_object().ok_or(INVALID)?;
    if required.iter().any(|key| !map.contains_key(*key))
        || map
            .keys()
            .any(|key| !required.contains(&key.as_str()) && !optional.contains(&key.as_str()))
    {
        return Err(INVALID);
    }
    Ok(map)
}
fn array(v: &Value, nonempty: bool) -> Result<&Vec<Value>> {
    v.as_array()
        .filter(|a| a.len() <= 1000 && (!nonempty || !a.is_empty()))
        .ok_or(INVALID)
}
fn text(v: &Value) -> Result<&str> {
    v.as_str()
        .filter(|s| {
            !s.is_empty()
                && s.trim() == *s
                && s.encode_utf16().count() <= 256
                && !s.chars().any(char::is_control)
        })
        .ok_or(INVALID)
}
fn identifier<'a>(v: &'a Value, max: usize, punctuation: &[u8]) -> Result<&'a str> {
    text(v).and_then(|s| {
        if s.len() <= max
            && s.as_bytes()[0].is_ascii_alphanumeric()
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || punctuation.contains(&b))
        {
            Ok(s)
        } else {
            Err(INVALID)
        }
    })
}
fn unique(set: &mut HashSet<String>, value: &str) -> Result<()> {
    if set.insert(value.to_lowercase()) {
        Ok(())
    } else {
        Err(INVALID)
    }
}
pub fn version(value: &str) -> Option<Vec<u32>> {
    let parts: Vec<_> = value.split('.').collect();
    if !(3..=4).contains(&parts.len())
        || parts
            .iter()
            .any(|p| p.is_empty() || !p.bytes().all(|b| b.is_ascii_digit()))
    {
        return None;
    }
    parts
        .iter()
        .map(|p| p.parse::<u32>().ok().filter(|n| *n <= i32::MAX as u32))
        .collect()
}
pub fn contract_date(value: &str) -> bool {
    if value.len() != 10
        || !value.bytes().enumerate().all(|(i, b)| {
            if i == 4 || i == 7 {
                b == b'-'
            } else {
                b.is_ascii_digit()
            }
        })
    {
        return false;
    }
    let year = value[..4].parse::<u32>().unwrap();
    let month = value[5..7].parse::<usize>().unwrap();
    let day = value[8..].parse::<u32>().unwrap();
    let leap = year.is_multiple_of(4) && (!year.is_multiple_of(100) || year.is_multiple_of(400));
    let days = [
        0,
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    year > 0 && (1..=12).contains(&month) && day > 0 && day <= days[month]
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Endpoint {
    pub address: String,
    pub port: u16,
}
pub fn endpoint(value: &str, loopback: bool) -> Result<Endpoint> {
    let (address, port) = value.rsplit_once(':').ok_or(INVALID)?;
    if address.is_empty()
        || !address.as_bytes()[0].is_ascii_alphanumeric()
        || !address
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
        || port.is_empty()
        || port.len() > 5
        || !port.bytes().all(|b| b.is_ascii_digit())
    {
        return Err(INVALID);
    }
    let port = port
        .parse::<u16>()
        .ok()
        .filter(|p| *p != 0)
        .ok_or(INVALID)?;
    if loopback {
        let ip = address.parse::<Ipv4Addr>().map_err(|_| INVALID)?;
        if !ip.is_loopback() || ip.to_string() != address {
            return Err(INVALID);
        }
    }
    Ok(Endpoint {
        address: address.into(),
        port,
    })
}
fn reference<'a>(table: &'a Value, key: &Value) -> Result<&'a Value> {
    let key = identifier(key, 64, b"_-")?;
    table
        .as_object()
        .ok_or(INVALID)?
        .iter()
        .find(|(k, _)| k.eq_ignore_ascii_case(key))
        .map(|(_, v)| v)
        .ok_or(INVALID)
}
pub fn pin(value: &str) -> Option<String> {
    let plain = value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit());
    let colon = value.len() == 95
        && value.bytes().enumerate().all(|(i, b)| {
            if i % 3 == 2 {
                b == b':'
            } else {
                b.is_ascii_hexdigit()
            }
        });
    (plain || colon).then(|| value.replace(':', "").to_uppercase())
}

impl Inventory {
    pub fn parse(bytes: &[u8]) -> Result<Self> {
        let document = strict_json::parse(bytes, MAX_DOCUMENT)?;
        let result = Self { document };
        result.validate()?;
        Ok(result)
    }
    /// Native-only document: never serialize this across the WebView bridge.
    pub fn document(&self) -> &Value {
        &self.document
    }
    pub fn hosts(&self) -> &[Value] {
        self.document["hosts"].as_array().unwrap()
    }
    pub fn connector(&self) -> &Value {
        &self.document["connector"]
    }
    fn validate(&self) -> Result<()> {
        let doc = &self.document;
        fields(
            doc,
            &[
                "schema_version",
                "fleet_version",
                "direct_probe_ms",
                "cloudflare_access_hosts",
                "credentials",
                "fingerprints",
                "hosts",
                "connector",
                "provisioning_profiles",
            ],
            &[],
        )?;
        if doc["schema_version"].as_u64() != Some(1) {
            return Err("INVENTORY_SCHEMA_UNSUPPORTED");
        }
        if version(text(&doc["fleet_version"])?).is_none()
            || !doc["direct_probe_ms"]
                .as_u64()
                .is_some_and(|n| (1..=5000).contains(&n))
        {
            return Err(INVALID);
        }
        for host in array(&doc["cloudflare_access_hosts"], false)? {
            identifier(host, 256, b".-")?;
        }
        let credentials = doc["credentials"].as_object().ok_or(INVALID)?;
        let pins = doc["fingerprints"].as_object().ok_or(INVALID)?;
        for (key, value) in credentials {
            identifier(&Value::String(key.clone()), 64, b"_-")?;
            match value["kind"].as_str() {
                Some("bat-profile-token") => {
                    fields(value, &["kind", "profile_id"], &["source_policy"])?;
                    text(&value["profile_id"])?;
                    if value
                        .get("source_policy")
                        .is_some_and(|v| v.as_u64() != Some(BAT_SOURCE_POLICY))
                    {
                        return Err(INVALID);
                    }
                }
                Some("windows-dpapi") => {
                    fields(value, &["kind", "name"], &[])?;
                    identifier(&value["name"], 64, b"_-")?;
                }
                _ => return Err(INVALID),
            }
        }
        for (key, value) in pins {
            identifier(&Value::String(key.clone()), 64, b"_-")?;
            fields(value, &["kind", "profile_id"], &[])?;
            if value["kind"] != "bat-profile-pin" {
                return Err(INVALID);
            }
            text(&value["profile_id"])?;
        }
        let mut names = HashSet::new();
        let mut profiles = HashSet::new();
        let mut ips = HashSet::new();
        let mut endpoints = HashSet::new();
        for host in array(&doc["hosts"], true)? {
            fields(
                host,
                &[
                    "name",
                    "profile",
                    "label",
                    "local",
                    "target",
                    "probe",
                    "routes",
                    "provision_route",
                    "bat",
                    "workspace_bindings",
                    "connector_endpoint",
                ],
                &["allow_tailscale_relay", "direct_probe_ms"],
            )?;
            route_options(host)?;
            let name = identifier(&host["name"], 64, b"_.-")?;
            let profile = identifier(&host["profile"], 64, b"_.-")?;
            if name.eq_ignore_ascii_case("connector") || profile.eq_ignore_ascii_case("default") {
                return Err(INVALID);
            }
            unique(&mut names, name)?;
            unique(&mut profiles, profile)?;
            text(&host["label"])?;
            let local = endpoint(text(&host["local"])?, true)?;
            unique(&mut ips, &local.address)?;
            endpoints.insert((local.address, local.port));
            endpoint(text(&host["target"])?, false)?;
            if !host["probe"].is_null() {
                endpoint(text(&host["probe"])?, false)?;
            }
            let mut last = None;
            let mut kinds = HashSet::new();
            for route in array(&host["routes"], true)? {
                fields(
                    route,
                    &["kind", "alias"],
                    &[
                        "probe",
                        "access_host",
                        "allow_tailscale_relay",
                        "direct_probe_ms",
                    ],
                )?;
                route_options(route)?;
                let kind = text(&route["kind"])?;
                let tier = ["direct", "lan", "cf"]
                    .iter()
                    .position(|v| *v == kind)
                    .ok_or(INVALID)?;
                if last.is_some_and(|v| v >= tier) {
                    return Err(INVALID);
                }
                last = Some(tier);
                kinds.insert(kind);
                identifier(&route["alias"], 128, b"_.-")?;
                if !route["probe"].is_null() {
                    endpoint(text(&route["probe"])?, false)?;
                }
                match kind {
                    "direct" => {
                        let ep = endpoint(text(&route["probe"])?, false)?;
                        let ip = ep.address.parse::<Ipv4Addr>().map_err(|_| INVALID)?;
                        let octets = ip.octets();
                        if ip.to_string() != ep.address
                            || octets[0] != 100
                            || !(64..=127).contains(&octets[1])
                            || host["probe"] != route["probe"]
                        {
                            return Err(INVALID);
                        }
                    }
                    "lan" => {
                        endpoint(text(&route["probe"])?, false)?;
                    }
                    "cf" => {
                        let access = identifier(&route["access_host"], 256, b".-")?;
                        if !doc["cloudflare_access_hosts"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .any(|v| v.as_str().is_some_and(|s| s.eq_ignore_ascii_case(access)))
                        {
                            return Err(INVALID);
                        }
                    }
                    _ => unreachable!(),
                }
            }
            if !kinds.contains(text(&host["provision_route"])?) {
                return Err(INVALID);
            }
            let bat = &host["bat"];
            fields(
                bat,
                &[
                    "remote_profile_id",
                    "credential_ref",
                    "fingerprint_ref",
                    "protocol",
                    "min_version",
                ],
                &[],
            )?;
            text(&bat["remote_profile_id"])?;
            if bat["protocol"] != "bat-remote/v2" || version(text(&bat["min_version"])?).is_none() {
                return Err(INVALID);
            }
            let credential = reference(&doc["credentials"], &bat["credential_ref"])?;
            let pin = reference(&doc["fingerprints"], &bat["fingerprint_ref"])?;
            if credential["kind"] != "bat-profile-token"
                || credential["profile_id"] != host["profile"]
                || pin["profile_id"] != host["profile"]
            {
                return Err(INVALID);
            }
            let mut bindings = HashSet::new();
            let mut workspaces = HashSet::new();
            for binding in array(&host["workspace_bindings"], false)? {
                fields(binding, &["id", "workspace_id", "required"], &[])?;
                unique(&mut bindings, text(&binding["id"])?)?;
                unique(&mut workspaces, text(&binding["workspace_id"])?)?;
                if !binding["required"].is_boolean() {
                    return Err(INVALID);
                }
            }
            let central = &host["connector_endpoint"];
            if !central.is_null() {
                fields(
                    central,
                    &[
                        "endpoint",
                        "remote_profile_id",
                        "credential_ref",
                        "fingerprint_ref",
                    ],
                    &[],
                )?;
                endpoint(text(&central["endpoint"])?, false)?;
                text(&central["remote_profile_id"])?;
                reference(&doc["credentials"], &central["credential_ref"])?;
                reference(&doc["fingerprints"], &central["fingerprint_ref"])?;
            }
        }
        let mut provisioned = HashSet::new();
        for profile in array(&doc["provisioning_profiles"], true)? {
            let id = text(profile)?;
            unique(&mut provisioned, id)?;
            if self.hosts().iter().filter(|h| h["profile"] == id).count() != 1 {
                return Err(INVALID);
            }
        }
        if provisioned.len() != profiles.len() {
            return Err(INVALID);
        }
        let central = self.connector();
        fields(
            central,
            &[
                "name",
                "label",
                "local",
                "target",
                "ssh_alias",
                "dashboard_path",
                "credential_ref",
                "supported_api_versions",
                "min_contract_version",
                "required_features",
            ],
            &[],
        )?;
        if central["name"] != "connector" {
            return Err(INVALID);
        }
        text(&central["label"])?;
        let ep = endpoint(text(&central["local"])?, true)?;
        endpoint(text(&central["target"])?, false)?;
        if endpoints.contains(&(ep.address, ep.port)) {
            return Err(INVALID);
        }
        if !central["ssh_alias"].is_null() {
            identifier(&central["ssh_alias"], 128, b"_.-")?;
        }
        let path = text(&central["dashboard_path"])?;
        if !path.starts_with('/')
            || path.starts_with("//")
            || !path
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"/_-".contains(&b))
        {
            return Err(INVALID);
        }
        if reference(&doc["credentials"], &central["credential_ref"])?["kind"] != "windows-dpapi" {
            return Err(INVALID);
        }
        if array(&central["supported_api_versions"], true)?
            .iter()
            .any(|v| v.as_u64() != Some(1))
            || !contract_date(text(&central["min_contract_version"])?)
        {
            return Err(INVALID);
        }
        for feature in array(&central["required_features"], true)? {
            if !text(feature)?
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_')
            {
                return Err(INVALID);
            }
        }
        Ok(())
    }
}

#[derive(Clone)]
pub struct ProfileIndex {
    document: Value,
}
impl ProfileIndex {
    pub fn parse(bytes: &[u8], allowed_root_fields: &[&str]) -> Result<Self> {
        let document =
            strict_json::parse(bytes, MAX_DOCUMENT).map_err(|_| "PROFILE_INDEX_INVALID")?;
        let validate = || -> Result<()> {
            fields(
                &document,
                &["profiles", "activeProfileIds"],
                allowed_root_fields,
            )?;
            let mut ids = HashSet::new();
            for profile in array(&document["profiles"], false)? {
                // Reviewed Kit/BAT profile record schema. Root extension fields do not
                // grant permission to introduce new per-profile behavior.
                fields(
                    profile,
                    &["id"],
                    &[
                        "name",
                        "type",
                        "createdAt",
                        "updatedAt",
                        "remoteHost",
                        "remotePort",
                        "remoteProfileId",
                        "remoteProfileName",
                        "remoteFingerprint",
                    ],
                )?;
                unique(&mut ids, text(&profile["id"])?)?;
            }
            for active in array(&document["activeProfileIds"], false)? {
                if !document["profiles"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|p| p["id"] == *active)
                {
                    return Err(INVALID);
                }
                text(active)?;
            }
            Ok(())
        };
        validate().map_err(|_| "PROFILE_INDEX_INVALID")?;
        Ok(Self { document })
    }
    pub fn document(&self) -> &Value {
        &self.document
    }
    pub fn profile(&self, id: &str) -> Option<&Value> {
        self.document["profiles"]
            .as_array()?
            .iter()
            .find(|p| p["id"] == id)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Issue {
    pub code: &'static str,
    pub host: String,
}
pub fn configuration_issues(
    inv: &Inventory,
    index: &ProfileIndex,
    trusted: &ProfileIndex,
    ssh: &str,
) -> Vec<Issue> {
    let mut issues = Vec::new();
    for host in inv.hosts() {
        let ep = endpoint(host["local"].as_str().unwrap(), true).unwrap();
        let id = host["profile"].as_str().unwrap();
        let check = |index: &ProfileIndex| -> Option<String> {
            let profile = index.profile(id)?;
            let remote_profile = match profile.get("remoteProfileId") {
                Some(value) => value.as_str()?,
                None => "default",
            };
            if profile["type"] != "remote"
                || profile["remoteHost"] != ep.address
                || profile["remotePort"].as_u64() != Some(u64::from(ep.port))
                || remote_profile != host["bat"]["remote_profile_id"].as_str()?
            {
                return None;
            }
            pin(profile["remoteFingerprint"].as_str()?)
        };
        let actual = check(index);
        let expected = check(trusted);
        if actual.is_none() || actual != expected {
            issues.push(Issue {
                code: "PROFILE_DRIFT",
                host: host["name"].as_str().unwrap().into(),
            });
        }
    }
    // Preserve reviewed Kit alias semantics. Includes are separately bound by the native loader;
    // this pure check never executes ssh or expands an arbitrary shell directive.
    let host_line = Regex::new(r"(?i)^\s*Host\s+([^#]+)").unwrap();
    let aliases: HashSet<_> = ssh
        .lines()
        .filter_map(|line| host_line.captures(line))
        .flat_map(|c| {
            c[1].split_whitespace()
                .map(str::to_lowercase)
                .collect::<Vec<_>>()
        })
        .collect();
    for host in inv.hosts() {
        for route in host["routes"].as_array().unwrap() {
            if !aliases.contains(&route["alias"].as_str().unwrap().to_lowercase()) {
                issues.push(Issue {
                    code: "SSH_ALIAS_MISSING",
                    host: host["name"].as_str().unwrap().into(),
                });
            }
        }
    }
    if inv.connector()["ssh_alias"]
        .as_str()
        .is_some_and(|a| !aliases.contains(&a.to_lowercase()))
    {
        issues.push(Issue {
            code: "SSH_ALIAS_MISSING",
            host: "connector".into(),
        });
    }
    issues
}

fn route_options(value: &Value) -> Result<()> {
    if value
        .get("allow_tailscale_relay")
        .is_some_and(|v| !v.is_boolean())
        || value
            .get("direct_probe_ms")
            .is_some_and(|v| !v.as_u64().is_some_and(|n| (1..=5000).contains(&n)))
    {
        return Err(INVALID);
    }
    Ok(())
}
