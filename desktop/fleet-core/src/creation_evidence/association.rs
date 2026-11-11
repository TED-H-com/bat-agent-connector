//! Pure original-Apply record codec. No persistence, replay admission or detach authority.
use super::*;
use crate::{
    connection_publish::FrozenHistory,
    connection_setup::association::{key_valid, FrozenAssociation},
};
use serde_json::Value;

/// Validated identity fields from an accepted server operation, not an authorization token.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AcceptedTarget {
    operation_id: String,
    installation_ref: String,
    #[serde(deserialize_with = "required_option")]
    target_connection_id: Option<String>,
}
fn id(s: &str, prefix: &str, length: usize) -> bool {
    s.strip_prefix(prefix).is_some_and(|s| hex(s, length))
}
fn text(s: &str, bound: usize) -> bool {
    !s.is_empty() && s.chars().count() <= bound && s.trim() == s && !s.chars().any(char::is_control)
}
impl AcceptedTarget {
    /// Accept the server target shape; an absent connection_id means creation.
    pub fn new(operation_id: &str, target: &Value) -> Result<Self> {
        let target = target.as_object().ok_or(INVALID)?;
        ensure(
            target
                .keys()
                .all(|k| ["installation_ref", "connection_id"].contains(&k.as_str())),
        )?;
        let result = Self {
            operation_id: operation_id.into(),
            installation_ref: target
                .get("installation_ref")
                .and_then(Value::as_str)
                .ok_or(INVALID)?
                .into(),
            target_connection_id: target
                .get("connection_id")
                .map(|v| v.as_str().map(str::to_owned).ok_or(INVALID))
                .transpose()?,
        };
        result.validate()?;
        Ok(result)
    }
    fn validate(&self) -> Result<()> {
        ensure(
            id(&self.operation_id, "op_", 32)
                && text(&self.installation_ref, 128)
                && self
                    .target_connection_id
                    .as_ref()
                    .is_none_or(|s| id(s, "conn_", 32)),
        )
    }
    pub fn operation_id(&self) -> &str {
        &self.operation_id
    }
    pub fn installation_ref(&self) -> &str {
        &self.installation_ref
    }
    pub fn connection_id(&self) -> Result<String> {
        self.validate()?;
        Ok(self
            .target_connection_id
            .clone()
            .unwrap_or_else(|| format!("conn_{}", &self.operation_id[3..])))
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AssociationContext {
    pub server_id: String,
    pub principal_id: String,
    pub expected_revision: u64,
    pub outer_review_digest: String,
    pub effect_id: String,
    pub configuration_id: String,
    pub configuration_sha256: String,
    pub source_descriptor_sha256: String,
    pub accepted_context_sha256: String,
    pub publication_link_sha256: String,
    #[serde(deserialize_with = "required_option")]
    pub predecessor_association_sha256: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConnectionAssociation {
    pub binding: Binding,
    pub accepted: AcceptedTarget,
    pub context: AssociationContext,
    pub connection_id: String,
    pub frozen: FrozenPlanRef,
    pub projection: FrozenAssociation,
}
impl ConnectionAssociation {
    pub fn new(
        binding: Binding,
        accepted: AcceptedTarget,
        context: AssociationContext,
        frozen: &FrozenHistory,
    ) -> Result<Self> {
        let result = Self {
            binding,
            connection_id: accepted.connection_id()?,
            accepted,
            context,
            frozen: frozen.reference().clone(),
            projection: frozen.association_projection()?,
        };
        result.validate()?;
        let owner = crate::connection_setup::frozen::owner(frozen.plan());
        ensure(
            result.binding.kit_root == frozen.plan().root
                && result.binding.fleet_creation_id == owner.installation_id
                && result.binding.owner_sid == owner.owner_sid
                && format_reference(
                    &result.binding.fleet_creation_id,
                    &result.binding.provisioning_sha256,
                )? == owner.creation_receipt,
        )?;
        Ok(result)
    }
    pub(super) fn key(&self) -> EvidenceKey {
        let tuple = (
            &self.context.server_id,
            &self.context.principal_id,
            &self.binding.startup_installation_ref,
            self.accepted.installation_ref(),
            &self.connection_id,
            self.accepted.operation_id(),
        );
        EvidenceKey::ConnectionAssociation(digest(
            &serde_json::to_vec(&tuple).expect("string tuple"),
        ))
    }
    pub(super) fn validate(&self) -> Result<()> {
        self.binding.validate()?;
        self.accepted.validate()?;
        self.projection.validate()?;
        let c = &self.context;
        ensure(
            self.connection_id == self.accepted.connection_id()?
                && text(&c.server_id, 128)
                && key_valid(&c.configuration_id)
                && id(&c.effect_id, "effect_", 64)
                && c.expected_revision <= i64::MAX as u64
                && (c.expected_revision == 0) == self.accepted.target_connection_id.is_none()
                && [
                    &c.principal_id,
                    &c.outer_review_digest,
                    &c.configuration_sha256,
                    &c.source_descriptor_sha256,
                    &c.accepted_context_sha256,
                    &c.publication_link_sha256,
                    &self.frozen.review_digest,
                    &self.frozen.sha256,
                ]
                .into_iter()
                .all(|s| hex(s, 64))
                && c.predecessor_association_sha256
                    .as_ref()
                    .is_none_or(|s| hex(s, 64)),
        )
    }
}
