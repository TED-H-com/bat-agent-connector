use super::{model::*, *};
use crate::{connection_contract::vocabulary, digest};
use serde_json::{json, Value};

fn exact(value: &Value, fields: &[&str]) -> Result<()> {
    ensure(
        value
            .as_object()
            .is_some_and(|o| o.len() == fields.len() && fields.iter().all(|k| o.contains_key(*k))),
    )
}
fn identity(intent: &Intent, value: &Value) -> Result<()> {
    let a = intent.accepted();
    ensure(
        value["contract_version"] == a.contract_version
            && value["operation_id"] == a.operation_id
            && value["installation_ref"] == a.target.installation_ref
            && value["step"] == "native_effects"
            && value["review_digest"] == a.review_digest
            && value["expected_revision"] == a.expected_revision,
    )
}
pub(super) fn envelope(intent: &Intent, value: &Value) -> Result<()> {
    exact(value, &["native_handoff", "read_only"])?;
    ensure(value["read_only"].is_boolean())?;
    let n = &value["native_handoff"];
    exact(
        n,
        &[
            "contract_version",
            "server_id",
            "principal_id",
            "installation_ref",
            "operation_id",
            "step",
            "review_digest",
            "expected_revision",
            "epoch",
            "effect_id",
            "expires_at",
            "native_plan",
            "challenge",
            "receipt",
        ],
    )?;
    identity(intent, n)?;
    ensure(
        n["server_id"] == intent.accepted().scope.server_id
            && n["principal_id"] == intent.accepted().scope.principal_id
            && &n["native_plan"] == intent.native_plan()
            && n["epoch"].as_u64().is_some_and(|v| v > 0)
            && n["challenge"].as_str().is_some_and(hash)
            && n["expires_at"]
                .as_f64()
                .is_some_and(|v| v.is_finite() && v > 0.0)
            && n["effect_id"]
                == format!(
                    "effect_{}",
                    digest(&encode(&json!([
                        intent.accepted().operation_id,
                        "native_effects"
                    ]))?)
                ),
    )?;
    if !n["receipt"].is_null() {
        let mut body = n["receipt"].clone();
        let reference = body
            .as_object_mut()
            .ok_or(Error::Invalid)?
            .remove("receipt_ref")
            .ok_or(Error::Invalid)?;
        ensure(reference == format!("receipt_{}", digest(&encode(&body)?)))?;
        body["challenge"] = n["challenge"].clone();
        receipt(intent, n, &body)?;
    }
    Ok(())
}
pub(super) fn receipt(intent: &Intent, envelope: &Value, body: &Value) -> Result<()> {
    exact(
        body,
        &[
            "contract_version",
            "installation_ref",
            "operation_id",
            "step",
            "review_digest",
            "expected_revision",
            "epoch",
            "effect_id",
            "challenge",
            "outcome",
            "roles",
            "code",
            "native_review_digest",
            "phase",
        ],
    )?;
    identity(intent, body)?;
    for key in ["epoch", "effect_id", "challenge"] {
        ensure(body[key] == envelope[key])?;
    }
    ensure(body["native_review_digest"] == intent.native_plan()["review_digest"])?;
    let outcome = body["outcome"].as_str().ok_or(Error::Invalid)?;
    let phase = body["phase"].as_str().ok_or(Error::Invalid)?;
    ensure(
        vocabulary()["matrix"][outcome][phase]
            .as_array()
            .is_some_and(|codes| codes.contains(&body["code"])),
    )?;
    let roles = body["roles"].as_array().ok_or(Error::Invalid)?;
    let artifacts = intent.native_plan()["artifacts"]
        .as_array()
        .ok_or(Error::Invalid)?;
    ensure(roles.len() == artifacts.len())?;
    for (role, artifact) in roles.iter().zip(artifacts) {
        exact(
            role,
            &["role", "before_digest", "after_digest", "observation"],
        )?;
        ensure(
            role["role"] == artifact["role"]
                && role["before_digest"] == artifact["before_digest"]
                && vocabulary()["observations"]
                    .as_array()
                    .is_some_and(|o| o.contains(&role["observation"]))
                && if role["observation"] == "present" {
                    role["after_digest"].as_str().is_some_and(hash)
                } else {
                    role["after_digest"].is_null()
                },
        )?;
        if ["applied", "unchanged"].contains(&outcome) {
            ensure(role["after_digest"] == artifact["after_digest"])?;
        }
    }
    if ["applied", "unchanged"].contains(&outcome) {
        ensure(
            (outcome == "applied")
                == artifacts
                    .iter()
                    .any(|a| a["before_digest"] != a["after_digest"]),
        )?;
    }
    Ok(())
}
pub(super) fn acknowledgement(wire: &[u8], reply: &Value) -> Result<()> {
    exact(reply, &["receipt", "replayed"])?;
    ensure(reply["replayed"].is_boolean())?;
    let mut evidence = parse(wire, bound("receipt_bytes"))?;
    evidence
        .as_object_mut()
        .ok_or(Error::Invalid)?
        .remove("challenge");
    evidence["receipt_ref"] = json!(format!("receipt_{}", digest(&encode(&evidence)?)));
    ensure(reply["receipt"] == evidence)
}
