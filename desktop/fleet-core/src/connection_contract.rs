//! Sanitized native commitments. The shipped JSON owns the wire vocabulary.
use crate::connection_setup::{Artifact, PublicationPlan};
use serde::Serialize;
use serde_json::Value;
use std::sync::OnceLock;

pub mod reference;

pub fn vocabulary() -> &'static Value {
    static CONTRACT: OnceLock<Value> = OnceLock::new();
    CONTRACT.get_or_init(|| {
        serde_json::from_str(include_str!(
            "../../../src/bat_agent_connector/connection_contract.json"
        ))
        .expect("shipped connection contract")
    })
}

/// The match is exhaustive; wire spelling and order come from the shared file.
pub fn artifact_role(artifact: Artifact) -> &'static str {
    let index = match artifact {
        Artifact::Inventory => 0,
        Artifact::ProfileIndex => 1,
        Artifact::SshReference => 2,
        Artifact::Installation => 3,
    };
    vocabulary()["roles"][index]
        .as_str()
        .expect("artifact role")
}

#[derive(Debug, Serialize)]
pub struct ManifestArtifact {
    pub role: &'static str,
    pub before_digest: Option<String>,
    pub after_digest: String,
}

#[derive(Debug, Serialize)]
pub struct NativePlanManifest {
    pub contract_version: u64,
    pub generator_version: u32,
    pub review_digest: String,
    pub artifacts: Vec<ManifestArtifact>,
}

/// Commit to all four outputs, including unchanged files, without local secrets.
/// Absence is None; an unchanged present file retains its actual output digest.
pub fn native_plan_manifest(plan: &PublicationPlan) -> crate::Result<NativePlanManifest> {
    if !plan.matches_review() {
        return Err("INVALID_NATIVE_PLAN");
    }
    let artifacts = [
        Artifact::Inventory,
        Artifact::ProfileIndex,
        Artifact::SshReference,
        Artifact::Installation,
    ]
    .into_iter()
    .map(|artifact| {
        let output = plan.output(artifact);
        let before_digest = plan
            .steps
            .iter()
            .find(|step| step.artifact == artifact)
            .map_or_else(
                || Some(output.sha256.clone()),
                |step| step.before_digest.clone(),
            );
        ManifestArtifact {
            role: artifact_role(artifact),
            before_digest,
            after_digest: output.sha256.clone(),
        }
    })
    .collect();
    Ok(NativePlanManifest {
        contract_version: vocabulary()["contract_version"]
            .as_u64()
            .expect("contract version"),
        generator_version: plan.generator_version,
        review_digest: plan.review_digest.clone(),
        artifacts,
    })
}
