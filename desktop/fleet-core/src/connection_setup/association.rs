//! Original native keys only. A projection is neither ownership nor effect authority.
use super::*;
use crate::connection_publish::FrozenHistory;
use serde::{Deserialize, Serialize};

const INVALID: &str = "CONNECTION_ASSOCIATION_UNPROVEN";
fn required_route<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    Option::<String>::deserialize(d)
}
pub(crate) fn key_valid(s: &str) -> bool {
    !s.is_empty() && s.len() <= 256 && !s.chars().any(char::is_control)
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AssociationRouteKind {
    Direct,
    Lan,
    Cloudflare,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenAssociation {
    pub host_key: String,
    pub profile_key: String,
    pub remote_profile_key: String,
    pub route_key: String,
    pub route_kind: AssociationRouteKind,
    pub route_source_key: String,
    pub route_source_sha256: String,
    pub selected_profile_source_sha256: String,
    pub user_ssh_sha256: String,
    #[serde(deserialize_with = "required_route")]
    pub connector_route_key: Option<String>,
    #[serde(deserialize_with = "required_route")]
    pub dashboard_route_key: Option<String>,
}
impl FrozenAssociation {
    pub(crate) fn validate(&self) -> crate::Result<()> {
        if [
            &self.host_key,
            &self.profile_key,
            &self.remote_profile_key,
            &self.route_key,
            &self.route_source_key,
        ]
        .into_iter()
        .chain(self.connector_route_key.iter())
        .chain(self.dashboard_route_key.iter())
        .all(|s| key_valid(s))
            && [
                &self.route_source_sha256,
                &self.selected_profile_source_sha256,
                &self.user_ssh_sha256,
            ]
            .into_iter()
            .all(|s| valid_digest(s))
        {
            Ok(())
        } else {
            Err(INVALID)
        }
    }
}
impl FrozenHistory {
    /// Project only after the original decoder has rebuilt and validated the plan.
    /// Schema-validated inventory/profile IDs select entries; display names never do.
    pub fn association_projection(&self) -> crate::Result<FrozenAssociation> {
        let plan = self.plan();
        let input = &plan.original_input.0;
        let proven =
            ProvenAssociation::new(&input.selected, input.forward.as_ref()).map_err(|_| INVALID)?;
        let inventory = Inventory::parse(&plan.output(Artifact::Inventory).bytes)?;
        let index = index_parse(&plan.output(Artifact::ProfileIndex).bytes).map_err(|_| INVALID)?;
        let profile = index.profile(&input.selected.profile.id).ok_or(INVALID)?;
        let host = inventory
            .hosts()
            .iter()
            .find(|h| h["profile"] == profile["id"])
            .ok_or(INVALID)?;
        let route = input.route.document(&proven.forward.alias);
        if !host["routes"].as_array().ok_or(INVALID)?.contains(&route)
            || host["local"] != proven.forward.local.to_string()
            || host["target"] != proven.forward.target
        {
            return Err(INVALID);
        }
        let text = |value: &Value| value.as_str().map(str::to_owned).ok_or(INVALID);
        let projection = FrozenAssociation {
            host_key: text(&host["name"])?,
            profile_key: text(&profile["id"])?,
            remote_profile_key: text(&profile["remoteProfileId"])?,
            route_key: text(&route["alias"])?,
            route_kind: match input.route.kind {
                RouteKind::Direct { .. } => AssociationRouteKind::Direct,
                RouteKind::Lan { .. } => AssociationRouteKind::Lan,
                RouteKind::Cloudflare { .. } => AssociationRouteKind::Cloudflare,
            },
            route_source_key: input.route.source.key.clone(),
            route_source_sha256: input.route.source.sha256.clone(),
            selected_profile_source_sha256: input.selected.index_digest.clone(),
            user_ssh_sha256: proven.forward.source_digest.clone(),
            // This generator reviews a BAT route, not a Connector or Dashboard route.
            connector_route_key: None,
            dashboard_route_key: None,
        };
        projection.validate()?;
        Ok(projection)
    }
}
