use super::*;
use crate::{
    connection_contract as contract, connection_publish::FrozenHistory, connection_setup::Artifact,
    digest, strict_json,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    Unsupported,
    Invalid,
    Conflict,
    Unavailable,
}
pub(super) fn ensure(value: bool) -> Result<()> {
    if value {
        Ok(())
    } else {
        Err(Error::Invalid)
    }
}
pub(super) fn bound(name: &str) -> usize {
    contract::vocabulary()["bounds"][name]
        .as_u64()
        .expect("shipped bound") as usize
}
pub(super) fn text(value: &str, max: usize) -> bool {
    !value.is_empty()
        && value.chars().count() <= max
        && value.trim() == value
        && !value.chars().any(char::is_control)
}
pub(super) fn hex(value: &str, prefix: &str, count: usize) -> bool {
    value.strip_prefix(prefix).is_some_and(|s| {
        s.len() == count
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
pub(super) fn hash(value: &str) -> bool {
    hex(value, "", bound("digest_hex_chars"))
}
pub(super) fn uuid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
            }
        })
}
pub(super) fn parse(bytes: &[u8], limit: usize) -> Result<Value> {
    strict_json::parse(bytes, limit).map_err(|_| Error::Invalid)
}
pub(super) fn encode(value: &impl Serialize) -> Result<Vec<u8>> {
    serde_json::to_vec(value).map_err(|_| Error::Invalid)
}
pub(super) fn typed<T: for<'a> Deserialize<'a> + Serialize>(value: Value) -> Result<T> {
    let result: T = serde_json::from_value(value.clone()).map_err(|_| Error::Invalid)?;
    ensure(serde_json::to_value(&result).map_err(|_| Error::Invalid)? == value)?;
    Ok(result)
}
pub const FLEET_ACTION: &str = "setup.connection.apply";

/// Classify dispatch before decoding today's fields: future variants are not corruption.
pub fn validate_variant(value: &Value) -> Result<()> {
    if value["format_version"] != 1
        || value["accepted"]["action"] != FLEET_ACTION
        || value["accepted"]["contract_version"] != contract::vocabulary()["contract_version"]
        || !value["accepted"]["kind"].is_null()
    {
        return Err(Error::Unsupported);
    }
    Ok(())
}

/// Supplied by the verified native session, never by a request or its paths.
/// Decoding proves consistency only; authentication/provenance remain caller duties.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    pub server_id: String,
    pub principal_id: String,
    pub native_installation: String,
    pub installation_ref: String,
}
impl Scope {
    fn validate(&self) -> Result<()> {
        ensure(
            text(&self.server_id, 128)
                && hash(&self.principal_id)
                && uuid(&self.native_installation)
                && text(&self.installation_ref, bound("installation_ref_chars")),
        )
    }
}
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Target {
    pub installation_ref: String,
    pub connection_id: Option<String>,
}
/// Original persisted operation and an opaque credential generation reference, not a secret.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Accepted {
    pub scope: Scope,
    pub action: String,
    pub contract_version: u64,
    pub kind: Option<String>,
    pub operation_id: String,
    pub operation_key: String,
    pub target: Target,
    pub expected_revision: u64,
    pub review_digest: String,
    pub credential_generation: String,
}
impl Accepted {
    fn validate(&self) -> Result<()> {
        validate_variant(&serde_json::json!({"format_version": 1, "accepted": self}))?;
        self.scope.validate()?;
        ensure(
            self.target.installation_ref == self.scope.installation_ref
                && self
                    .target
                    .connection_id
                    .as_ref()
                    .is_none_or(|s| hex(s, "conn_", 32))
                && hex(&self.operation_id, "op_", 32)
                && text(&self.operation_key, 256)
                && self.expected_revision <= i64::MAX as u64
                && hash(&self.review_digest)
                && text(&self.credential_generation, 256),
        )
    }
}
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct FleetIntent {
    format_version: u32,
    accepted: Accepted,
    frozen: crate::connection_publish::FrozenPlanRef,
    native_plan: Value,
    role_mapping: Vec<(String, Artifact)>,
}
/// Private dispatch can grow without changing Fleet's stored bytes.
/// Construction must go through the validated codecs.
/// ```compile_fail
/// use bat_fleet_core::connection_participant::Intent;
/// let forged = Intent::Fleet(serde_json::from_str("{}").unwrap());
/// ```
#[derive(Clone, PartialEq, Eq)]
pub struct Intent(Dispatch);
#[derive(Clone, PartialEq, Eq)]
enum Dispatch {
    Fleet(FleetIntent),
}
impl Intent {
    pub fn fleet(accepted: Accepted, frozen: &FrozenHistory) -> Result<Self> {
        accepted.validate()?;
        let native_plan = serde_json::to_value(
            contract::native_plan_manifest(frozen.plan()).map_err(|_| Error::Invalid)?,
        )
        .map_err(|_| Error::Invalid)?;
        ensure(encode(&native_plan)?.len() <= bound("native_plan_bytes"))?;
        Ok(Self(Dispatch::Fleet(FleetIntent {
            format_version: 1,
            accepted,
            frozen: frozen.reference().clone(),
            native_plan,
            role_mapping: frozen
                .plan()
                .outputs
                .iter()
                .map(|o| (contract::artifact_role(o.artifact).into(), o.artifact))
                .collect(),
        })))
    }
    pub fn decode(bytes: &[u8], scope: &Scope, frozen: &FrozenHistory) -> Result<Self> {
        let stored = Self::stored(bytes, scope)?;
        let expected = Self::fleet(stored.accepted.clone(), frozen)?;
        ensure(Self(Dispatch::Fleet(stored)) == expected)?;
        Ok(expected)
    }
    pub(super) fn reference(
        bytes: &[u8],
        scope: &Scope,
        operation_id: &str,
    ) -> Result<crate::connection_publish::FrozenPlanRef> {
        let stored = Self::stored(bytes, scope)?;
        ensure(stored.accepted.operation_id == operation_id)?;
        Ok(stored.frozen)
    }
    fn stored(bytes: &[u8], scope: &Scope) -> Result<FleetIntent> {
        let value = parse(bytes, Self::limit())?;
        validate_variant(&value)?;
        let stored: FleetIntent = typed(value)?;
        stored.accepted.validate()?;
        ensure(
            &stored.accepted.scope == scope
                && hash(&stored.frozen.review_digest)
                && hash(&stored.frozen.sha256),
        )?;
        Ok(stored)
    }
    pub fn bytes(&self) -> Result<Vec<u8>> {
        let bytes = encode(self.fleet_record())?;
        ensure(bytes.len() <= Self::limit())?;
        Ok(bytes)
    }
    pub fn accepted(&self) -> &Accepted {
        &self.fleet_record().accepted
    }
    pub fn namespace(&self) -> Namespace {
        Namespace::for_operation(&self.accepted().scope, &self.accepted().operation_id)
            .expect("validated intent")
    }
    pub(super) fn native_plan(&self) -> &Value {
        &self.fleet_record().native_plan
    }
    pub(super) fn limit() -> usize {
        bound("native_plan_bytes") + bound("receipt_bytes")
    }
    fn fleet_record(&self) -> &FleetIntent {
        match &self.0 {
            Dispatch::Fleet(value) => value,
        }
    }
}
/// Native-derived, opaque path components; never deserialized from a request.
pub struct Namespace {
    scope: String,
    operation: String,
}
impl Namespace {
    pub fn for_operation(scope: &Scope, operation_id: &str) -> Result<Self> {
        scope.validate()?;
        ensure(hex(operation_id, "op_", 32))?;
        Ok(Self {
            scope: digest(&encode(scope)?),
            operation: operation_id.into(),
        })
    }
    pub fn relative_path(&self) -> String {
        format!(
            "connections-v1/participant/{}/{}",
            self.scope, self.operation
        )
    }
}
