//! Reference commitments using the shared connection_contract.json vocabulary.
use super::vocabulary;
use serde::Serialize;
use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CodecError {
    UnsupportedContract,
    UnsupportedGenerator,
    Invalid,
    Bound,
    ReviewChanged,
}
type Result<T> = std::result::Result<T, CodecError>;
#[derive(Debug)]
pub struct EffectSpec {
    action: &'static str,
    kind: &'static str,
    definition: &'static Value,
}
pub fn for_action(action: &str) -> Result<EffectSpec> {
    vocabulary()["v2"]["effect_kinds"]
        .as_object()
        .unwrap()
        .iter()
        .find(|(_, definition)| definition["action"] == action)
        .map(|(kind, definition)| EffectSpec {
            action: definition["action"].as_str().unwrap(),
            kind,
            definition,
        })
        .ok_or(CodecError::UnsupportedContract)
}
fn require(ok: bool) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(CodecError::Invalid)
    }
}
fn keys(v: &Value, names: &[&str]) -> bool {
    v.as_object()
        .is_some_and(|o| o.len() == names.len() && names.iter().all(|k| o.contains_key(*k)))
}
fn hex(v: &Value, size: usize) -> bool {
    v.as_str().is_some_and(|s| {
        s.len() == size
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
fn bound(name: &str) -> usize {
    vocabulary()["bounds"][name].as_u64().unwrap() as usize
}
fn digest(v: &Value) -> bool {
    hex(v, bound("digest_hex_chars"))
}
fn integer(v: &Value, minimum: u64) -> bool {
    v.as_u64()
        .is_some_and(|n| n >= minimum && n <= vocabulary()["v2"]["integer_max"].as_u64().unwrap())
}
fn canonical(v: &Value, limit: &str) -> Result<Vec<u8>> {
    // Explicit recursive sorting survives serde_json feature unification.
    let mut sorted = v.clone();
    sorted.sort_all_objects();
    let bytes = serde_json::to_vec(&sorted).map_err(|_| CodecError::Invalid)?;
    if bytes.len() > bound(limit) {
        Err(CodecError::Bound)
    } else {
        Ok(bytes)
    }
}
fn parse(raw: &[u8], limit: &str) -> Result<Value> {
    let v = crate::strict_json::parse(raw, bound(limit)).map_err(|e| {
        if e == "JSON_BOUND" {
            CodecError::Bound
        } else {
            CodecError::Invalid
        }
    })?;
    canonical(&v, limit)?;
    Ok(v)
}
fn contract(spec: &EffectSpec, v: &Value) -> Result<()> {
    if v["contract_version"].as_u64() != vocabulary()["v2"]["contract_version"].as_u64()
        || v["kind"] != spec.kind
    {
        return Err(CodecError::UnsupportedContract);
    }
    Ok(())
}
#[derive(Debug, Clone, Serialize)]
pub struct Sha256(String);
impl Sha256 {
    pub fn new(value: &str) -> Result<Self> {
        require(digest(&json!(value)))?;
        Ok(Self(value.into()))
    }
}
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "state", rename_all = "lowercase")]
pub enum ArtifactState {
    Absent,
    Present { digest: Sha256 },
}
impl ArtifactState {
    pub fn from_value(v: &Value) -> Result<Self> {
        if v == &json!({"state":"absent"}) {
            return Ok(Self::Absent);
        }
        require(keys(v, &["state", "digest"]) && v["state"] == "present" && digest(&v["digest"]))?;
        Ok(Self::Present {
            digest: Sha256::new(v["digest"].as_str().unwrap())?,
        })
    }
}
#[derive(Debug, Clone, Copy)]
pub enum ReferenceRole {
    Catalog,
    Selection,
}
impl ReferenceRole {
    fn wire(self, spec: &EffectSpec) -> &Value {
        &spec.definition["roles"][match self {
            Self::Catalog => 0,
            Self::Selection => 1,
        }]
    }
}
pub struct PlannedRole {
    pub role: ReferenceRole,
    pub before: ArtifactState,
    pub after: ArtifactState,
}
#[derive(Debug)]
pub struct NativePlanV2(Value);
impl NativePlanV2 {
    pub fn value(&self) -> &Value {
        &self.0
    }
}
#[derive(Debug)]
pub struct NativeReceiptV2(Value);
impl NativeReceiptV2 {
    /// Validate participant-built fields before retaining a receipt. The challenge is opaque.
    pub fn from_value(spec: &EffectSpec, plan: &NativePlanV2, value: Value) -> Result<Self> {
        decode_receipt(spec, plan, &canonical(&value, "receipt_bytes")?)
    }
    pub fn value(&self) -> &Value {
        &self.0
    }
}
pub fn decode_plan(spec: &EffectSpec, raw: &[u8]) -> Result<NativePlanV2> {
    let v = parse(raw, "native_plan_bytes")?;
    contract(spec, &v)?;
    require(keys(
        &v,
        &[
            "contract_version",
            "kind",
            "generator_version",
            "review_digest",
            "artifacts",
        ],
    ))?;
    require(integer(&v["generator_version"], 0))?;
    if !vocabulary()["v2"]["generator_versions"]
        .as_array()
        .unwrap()
        .contains(&v["generator_version"])
    {
        return Err(CodecError::UnsupportedGenerator);
    }
    require(digest(&v["review_digest"]))?;
    let roles = spec.definition["roles"].as_array().unwrap();
    let artifacts = v["artifacts"].as_array().ok_or(CodecError::Invalid)?;
    require(artifacts.len() == roles.len())?;
    for (a, name) in artifacts.iter().zip(roles) {
        require(keys(a, &["role", "before", "after"]) && &a["role"] == name)?;
        ArtifactState::from_value(&a["before"])?;
        ArtifactState::from_value(&a["after"])?;
        require(
            spec.definition["after_states"][name.as_str().unwrap()]
                .as_array()
                .unwrap()
                .contains(&a["after"]["state"]),
        )?;
        require(
            a["after"]["state"] != "absent"
                || spec.definition["absent_after_requires_unchanged"] != true
                || a["before"] == a["after"],
        )?;
    }
    Ok(NativePlanV2(v))
}
pub fn encode_plan(
    spec: &EffectSpec,
    generator: u32,
    review: &Sha256,
    roles: &[PlannedRole],
) -> Result<Vec<u8>> {
    let artifacts: Vec<_> = roles
        .iter()
        .map(|r| json!({"role":r.role.wire(spec),"before":r.before,"after":r.after}))
        .collect();
    let v = json!({"contract_version":vocabulary()["v2"]["contract_version"],"kind":spec.kind,
        "generator_version":generator,"review_digest":review,"artifacts":artifacts});
    let bytes = canonical(&v, "native_plan_bytes")?;
    decode_plan(spec, &bytes)?;
    Ok(bytes)
}
pub fn decode_receipt(
    spec: &EffectSpec,
    plan: &NativePlanV2,
    raw: &[u8],
) -> Result<NativeReceiptV2> {
    contract(spec, plan.value())?;
    let v = parse(raw, "receipt_bytes")?;
    contract(spec, &v)?;
    require(keys(
        &v,
        &[
            "contract_version",
            "action",
            "kind",
            "connection_id",
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
    ))?;
    require(v["action"] == spec.action && v["step"] == "native_effects")?;
    for (field, prefix, size) in [
        ("operation_id", "op_", 32),
        ("connection_id", "conn_", 32),
        ("effect_id", "effect_", bound("digest_hex_chars")),
    ] {
        require(
            v[field]
                .as_str()
                .and_then(|s| s.strip_prefix(prefix))
                .is_some_and(|s| hex(&json!(s), size)),
        )?;
    }
    let installation = v["installation_ref"].as_str().ok_or(CodecError::Invalid)?;
    require(
        !installation.is_empty()
            && installation.chars().count() <= bound("installation_ref_chars")
            && installation.trim() == installation
            && installation.chars().all(|c| c >= ' '),
    )?;
    require(integer(&v["expected_revision"], 0) && integer(&v["epoch"], 1))?;
    require(
        ["review_digest", "challenge", "native_review_digest"]
            .iter()
            .all(|k| digest(&v[*k])),
    )?;
    let outcome = v["outcome"].as_str().ok_or(CodecError::Invalid)?;
    let phase = v["phase"].as_str().ok_or(CodecError::Invalid)?;
    require(
        spec.definition["matrix"][outcome][phase]
            .as_array()
            .is_some_and(|codes| codes.contains(&v["code"])),
    )?;
    let roles = v["roles"].as_array().ok_or(CodecError::Invalid)?;
    let artifacts = plan.value()["artifacts"].as_array().unwrap();
    require(roles.len() == artifacts.len())?;
    let success = outcome == "applied" || outcome == "unchanged";
    let mut matches = v["native_review_digest"] == plan.value()["review_digest"];
    for (r, a) in roles.iter().zip(artifacts) {
        let readable = r["observation"] == "present" || r["observation"] == "absent";
        require(
            keys(
                r,
                if readable {
                    &["role", "before", "observation", "after"]
                } else {
                    &["role", "before", "observation"]
                },
            ) && r["role"] == a["role"]
                && vocabulary()["observations"]
                    .as_array()
                    .unwrap()
                    .contains(&r["observation"]),
        )?;
        ArtifactState::from_value(&r["before"])?;
        if readable {
            ArtifactState::from_value(&r["after"])?;
            require(r["after"]["state"] == r["observation"])?;
        }
        matches &= r["before"] == a["before"] && (!success || readable && r["after"] == a["after"]);
    }
    matches &=
        !success || artifacts.iter().any(|a| a["before"] != a["after"]) == (outcome == "applied");
    if !matches {
        return Err(CodecError::ReviewChanged);
    }
    Ok(NativeReceiptV2(v))
}
pub fn encode_receipt(
    spec: &EffectSpec,
    plan: &NativePlanV2,
    receipt: &NativeReceiptV2,
) -> Result<Vec<u8>> {
    let bytes = canonical(receipt.value(), "receipt_bytes")?;
    decode_receipt(spec, plan, &bytes)?;
    Ok(bytes)
}
