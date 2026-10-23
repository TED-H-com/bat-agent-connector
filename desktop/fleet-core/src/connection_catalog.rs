//! Pure native association format. No persistence, ownership proof or central-config validation.
//! Candidate bytes depend only on the current document and Change. The catalog
//! records no receipt or operation IDs, so a review can predict its after bytes.
use crate::{digest, strict_json, Result};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

pub const DOCUMENT_BOUND: usize = 1024 * 1024;
pub const ENTRY_BOUND: usize = 1000;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    pub format_version: u64,
    pub minimum_reader_version: u64,
    pub revision: u64,
    /// Exact startup-role installation UUID, also bound by creation evidence.
    pub installation_ref: String,
    pub entries: Vec<Entry>,
    #[serde(deserialize_with = "required_option")]
    pub selected: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConfigurationRef {
    pub id: String,
    pub sha256: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub id: String,
    pub server_id: String,
    pub principal_id: String,
    pub configuration: ConfigurationRef,
    /// Association inherits this entry's central identity and the catalog installation.
    #[serde(deserialize_with = "required_option")]
    pub connection_id: Option<String>,
    pub state: State,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Attached,
    Detached,
}
fn required_option<'de, D: serde::Deserializer<'de>, T: Deserialize<'de>>(
    deserializer: D,
) -> std::result::Result<Option<T>, D::Error> {
    Option::deserialize(deserializer)
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReadOutcome {
    Absent,
    Present { catalog: Catalog, canonical: bool },
    Unsupported,
    Invalid(&'static str),
}
/// Only Absent permits creation. Unsupported and Invalid must never enable legacy fallback.
pub fn load(bytes: Option<&[u8]>) -> ReadOutcome {
    let Some(bytes) = bytes else {
        return ReadOutcome::Absent;
    };
    let value = match strict_json::parse(bytes, DOCUMENT_BOUND) {
        Ok(value) => value,
        Err(reason) => {
            return ReadOutcome::Invalid(if reason == "JSON_BOUND" {
                "CATALOG_BOUND"
            } else {
                "CATALOG_INVALID"
            })
        }
    };
    let versions = (
        value["format_version"].as_u64(),
        value["minimum_reader_version"].as_u64(),
    );
    match versions {
        (Some(1), Some(1)) => (),
        (Some(_), Some(_)) => return ReadOutcome::Unsupported,
        _ => return ReadOutcome::Invalid("CATALOG_INVALID"),
    }
    let catalog: Catalog = match serde_json::from_value(value) {
        Ok(catalog) => catalog,
        Err(_) => return ReadOutcome::Invalid("CATALOG_INVALID"),
    };
    match serialize(&catalog) {
        Ok(canonical) => ReadOutcome::Present {
            catalog,
            canonical: canonical == bytes,
        },
        Err(reason) => ReadOutcome::Invalid(reason),
    }
}
fn hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn id(value: &str, prefix: &str) -> bool {
    value.strip_prefix(prefix).is_some_and(|tail| hex(tail, 32))
}
fn uuid(value: &str) -> bool {
    value.len() == 36
        && value.split('-').map(str::len).eq([8, 4, 4, 4, 12])
        && value.split('-').all(|part| hex(part, part.len()))
}
fn opaque(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value.trim() == value
        && !value.chars().any(char::is_control)
}
fn require(valid: bool, reason: &'static str) -> Result<()> {
    valid.then_some(()).ok_or(reason)
}
impl Catalog {
    fn validate(&self) -> Result<()> {
        require(
            self.format_version == 1 && self.minimum_reader_version == 1,
            "CATALOG_UNSUPPORTED",
        )?;
        require(self.entries.len() <= ENTRY_BOUND, "CATALOG_BOUND")?;
        require(
            self.revision > 0 && uuid(&self.installation_ref),
            "CATALOG_INVALID",
        )?;
        let mut ids = HashSet::new();
        let mut connections = HashSet::new();
        for entry in &self.entries {
            if !id(&entry.id, "entry_")
                || !opaque(&entry.server_id)
                || !opaque(&entry.principal_id)
                || !id(&entry.configuration.id, "config_")
                || !hex(&entry.configuration.sha256, 64)
                || entry
                    .connection_id
                    .as_ref()
                    .is_some_and(|c| !id(c, "conn_"))
            {
                return Err("CATALOG_INVALID");
            }
            if !ids.insert(&entry.id)
                || entry.connection_id.as_ref().is_some_and(|c| {
                    !connections.insert((&entry.server_id, &entry.principal_id, c))
                })
            {
                return Err("CATALOG_CONFLICT");
            }
        }
        if self.selected.as_ref().is_some_and(|selected| {
            !self
                .entries
                .iter()
                .any(|e| &e.id == selected && e.state == State::Attached)
        }) {
            return Err("CATALOG_INVALID");
        }
        Ok(())
    }
}
/// The sole canonical encoder: compact UTF-8 JSON in declaration order, no newline.
/// Entry order is history; it is never sorted or normalized.
pub fn serialize(catalog: &Catalog) -> Result<Vec<u8>> {
    catalog.validate()?;
    let bytes = serde_json::to_vec(catalog).map_err(|_| "CATALOG_INVALID")?;
    if bytes.len() > DOCUMENT_BOUND {
        return Err("CATALOG_BOUND");
    }
    Ok(bytes)
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Change {
    Create { installation_ref: String },
    Add(Entry),
    Detach(String),
    Reattach(String),
    Select(Option<String>),
}
#[derive(Debug, PartialEq, Eq)]
pub enum Transition {
    Unchanged,
    Changed {
        catalog: Catalog,
        bytes: Vec<u8>,
        sha256: String,
    },
}
/// Pure candidate generation; a Changed result confers no authority to publish it.
/// Refusals and Unchanged return no candidate bytes and leave all history intact.
pub fn transition(current: &ReadOutcome, change: Change) -> Result<Transition> {
    let next = match current {
        ReadOutcome::Absent => match change {
            Change::Create { installation_ref } => Catalog {
                format_version: 1,
                minimum_reader_version: 1,
                revision: 1,
                installation_ref,
                entries: Vec::new(),
                selected: None,
            },
            _ => return Err("CATALOG_CONFLICT"),
        },
        ReadOutcome::Unsupported => return Err("CATALOG_UNSUPPORTED"),
        ReadOutcome::Invalid(_) => return Err("CATALOG_INVALID"),
        ReadOutcome::Present { catalog, .. } => {
            serialize(catalog)?;
            let mut next = catalog.clone();
            match change {
                Change::Create { .. } => return Err("CATALOG_CONFLICT"),
                Change::Add(entry) => {
                    require(entry.state == State::Attached, "CATALOG_INVALID")?;
                    if next.entries.len() == ENTRY_BOUND {
                        return Err("CATALOG_BOUND");
                    }
                    next.entries.push(entry);
                }
                Change::Select(selected) => next.selected = selected,
                change => {
                    let (id, state) = match change {
                        Change::Detach(id) => (id, State::Detached),
                        Change::Reattach(id) => (id, State::Attached),
                        _ => unreachable!(),
                    };
                    let entry = next
                        .entries
                        .iter_mut()
                        .find(|e| e.id == id)
                        .ok_or("CATALOG_CONFLICT")?;
                    entry.state = state;
                    if state == State::Detached && next.selected.as_ref() == Some(&id) {
                        next.selected = None;
                    }
                }
            }
            if &next == catalog {
                return Ok(Transition::Unchanged);
            }
            next.revision = next
                .revision
                .checked_add(1)
                .ok_or("CATALOG_REVISION_EXHAUSTED")?;
            next
        }
    };
    // Creation and every changed candidate share validation and encoding.
    let bytes = serialize(&next)?;
    let sha256 = digest(&bytes);
    Ok(Transition::Changed {
        catalog: next,
        bytes,
        sha256,
    })
}
