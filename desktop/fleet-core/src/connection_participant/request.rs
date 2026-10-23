//! Private wire bytes only; constructing a request grants no HTTP or effect authority.
use super::{model::*, wire, *};
use serde_json::{json, Value};

pub enum Mode {
    Claim,
    Readback,
}
pub fn handoff(intent: &Intent, mode: Mode) -> Result<Vec<u8>> {
    let a = intent.accepted();
    let bytes = encode(&json!({"contract_version":a.contract_version,
        "installation_ref":a.target.installation_ref,"operation_id":a.operation_id,
        "mode":match mode {Mode::Claim=>"claim",Mode::Readback=>"readback"}}))?;
    ensure(bytes.len() <= bound("handoff_bytes"))?;
    Ok(bytes)
}
pub struct Report {
    pub outcome: String,
    pub phase: String,
    pub code: Option<String>,
    pub roles: Vec<Value>,
}
pub fn receipt(intent: &Intent, envelope: &Value, report: Report) -> Result<Vec<u8>> {
    wire::envelope(intent, envelope)?;
    let native = &envelope["native_handoff"];
    let mut body = json!({"outcome":report.outcome,"phase":report.phase,"code":report.code,
        "roles":report.roles,"native_review_digest":intent.native_plan()["review_digest"]});
    for key in [
        "contract_version",
        "installation_ref",
        "operation_id",
        "step",
        "review_digest",
        "expected_revision",
        "epoch",
        "effect_id",
        "challenge",
    ] {
        body[key] = native[key].clone();
    }
    wire::receipt(intent, native, &body)?;
    stored_receipt(&encode(&body)?)
}
/// Replay the original bytes, including whitespace; never serialize a stored receipt again.
pub fn stored_receipt(bytes: &[u8]) -> Result<Vec<u8>> {
    let body = parse(bytes, bound("receipt_bytes"))?;
    ensure(
        body["operation_id"]
            .as_str()
            .is_some_and(|id| hex(id, "op_", 32)),
    )?;
    Ok(bytes.to_vec())
}
