//! Original protected review reads. No association writer or current-settings repair.
use super::{Error, Result};
use bat_fleet_core::{
    connection_participant::{self as core, Intent, Scope},
    connection_publish::{FrozenHistory, FrozenPlanRef},
    digest, strict_json,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ConfigurationRef {
    pub id: String,
    pub sha256: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AssociationContext {
    pub configuration_ref: ConfigurationRef,
    pub source_descriptor_sha256: String,
}
#[derive(Clone)]
pub(crate) struct CurrentBinding {
    pub scope: Scope,
    pub credential_generation: String,
    pub configuration_ref: ConfigurationRef,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StoredReview {
    format_version: u32,
    intent: String,
    frozen: FrozenPlanRef,
    association: Option<AssociationContext>,
}
pub(crate) struct OriginalReview {
    pub intent: Intent,
    context: AssociationContext,
}
fn require(ok: bool) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(Error::ReviewChanged)
    }
}
fn hash(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
impl OriginalReview {
    /// The caller proves protected, bounded no-follow provenance before decoding.
    pub(crate) fn decode(
        bytes: &[u8],
        scope: &Scope,
        history: impl FnOnce(&FrozenPlanRef) -> core::Result<FrozenHistory>,
    ) -> Result<Self> {
        let bounds = bat_fleet_core::connection_contract::vocabulary();
        let limit = 2
            * (bounds["bounds"]["native_plan_bytes"].as_u64().unwrap()
                + bounds["bounds"]["receipt_bytes"].as_u64().unwrap()) as usize
            + 4096;
        let value = strict_json::parse(bytes, limit).map_err(|_| Error::ReviewChanged)?;
        if value["format_version"] != 1 {
            return Err(Error::Unsupported);
        }
        let stored: StoredReview =
            serde_json::from_value(value).map_err(|_| Error::ReviewChanged)?;
        let context = stored.association.ok_or(Error::SourceAssociationUnproven)?;
        require(
            hash(&context.source_descriptor_sha256)
                && hash(&context.configuration_ref.sha256)
                && !context.configuration_ref.id.is_empty()
                && context.configuration_ref.id.len() <= 128
                && !context.configuration_ref.id.chars().any(char::is_control),
        )?;
        require(hash(&stored.frozen.review_digest) && hash(&stored.frozen.sha256))?;
        let frozen = history(&stored.frozen).map_err(|_| Error::ReviewChanged)?;
        let intent = Intent::decode(stored.intent.as_bytes(), scope, &frozen).map_err(|error| {
            if error == core::Error::Unsupported {
                Error::Unsupported
            } else {
                Error::ReviewChanged
            }
        })?;
        Ok(Self { intent, context })
    }
    pub(crate) fn association(&self) -> &AssociationContext {
        &self.context
    }
    pub(crate) fn validate(&self, operation: &Value, current: &CurrentBinding) -> Result<()> {
        let a = self.intent.accepted();
        let admitted = &operation["external_refs"]["admission_binding"];
        let native = &admitted["native_plan"];
        let variant = json!({
            "format_version": 2,
            "accepted": {
                "action": operation["action"],
                "contract_version": admitted.get("contract_version").unwrap_or(&native["contract_version"]),
                "kind": admitted.get("kind").or_else(|| native.get("kind")).unwrap_or(&Value::Null)
            }
        });
        core::validate_variant(&variant).map_err(|_| Error::Unsupported)?;
        let intent: Value =
            serde_json::from_slice(&self.intent.bytes().map_err(|_| Error::ReviewChanged)?)
                .map_err(|_| Error::ReviewChanged)?;
        require(
            a.scope == current.scope
                && a.credential_generation == current.credential_generation
                && operation["operation_id"] == a.operation_id
                && operation["idempotency_key"] == a.operation_key
                && operation["preconditions"]["expected_revision"] == a.expected_revision
                && operation["preconditions"]["review_digest"] == a.review_digest
                && admitted["review_digest"] == a.review_digest
                && admitted["server_id"] == a.scope.server_id
                && admitted["principal_id"] == a.scope.principal_id
                && admitted["installation_ref"] == a.scope.installation_ref
                && operation["target"]["installation_ref"] == a.target.installation_ref
                && operation["target"]["connection_id"]
                    == serde_json::to_value(&a.target.connection_id).unwrap()
                && native == &intent["native_plan"]
                && native == &operation["params"]["native_plan"],
        )?;
        if self.context.configuration_ref != current.configuration_ref
            || !admitted["source_descriptor"].is_object()
            || self.context.source_descriptor_sha256
                != digest(
                    &serde_json::to_vec(&admitted["source_descriptor"])
                        .map_err(|_| Error::SourceAssociationUnproven)?,
                )
        {
            return Err(Error::SourceAssociationUnproven);
        }
        Ok(())
    }
}
/// Rooted native authority supplies current identity/configuration and immutable review
/// reads independently. Neither paths nor the review's own fields prove current identity.
pub(crate) trait Reviews {
    fn original(&self, scope: &Scope, operation: &str) -> Result<OriginalReview>;
    fn history(&self, reference: &FrozenPlanRef) -> core::Result<FrozenHistory>;
    fn current(&self) -> Result<CurrentBinding>;
}
