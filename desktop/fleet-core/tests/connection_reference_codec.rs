use bat_fleet_core::connection_contract::{reference::*, vocabulary};
use serde_json::{json, Value};

const ACTION: &str = "connection.reference.detach";
fn rows() -> Vec<Value> {
    include_str!("../../../tests/fixtures/reference-codec-python.jsonl")
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}
fn bytes(v: &Value) -> Vec<u8> {
    serde_json::to_vec(v).unwrap()
}
fn plan() -> Value {
    serde_json::from_str(rows()[0]["wire"].as_str().unwrap()).unwrap()
}
fn encoded(spec: &EffectSpec, v: &Value) -> Vec<u8> {
    let roles: Vec<_> = [ReferenceRole::Catalog, ReferenceRole::Selection]
        .into_iter()
        .zip(v["artifacts"].as_array().unwrap())
        .map(|(role, a)| PlannedRole {
            role,
            before: ArtifactState::from_value(&a["before"]).unwrap(),
            after: ArtifactState::from_value(&a["after"]).unwrap(),
        })
        .collect();
    encode_plan(
        spec,
        1,
        &Sha256::new(v["review_digest"].as_str().unwrap()).unwrap(),
        &roles,
    )
    .unwrap()
}

#[test]
fn python_vectors_roundtrip_and_rust_golden() {
    let spec = for_action(ACTION).unwrap();
    let mut golden = String::new();
    for mut row in rows() {
        let wire = row["wire"].as_str().unwrap().as_bytes();
        let result = if row["plan"].is_null() {
            decode_plan(&spec, wire).map(|p| encoded(&spec, p.value()))
        } else {
            let plan = decode_plan(&spec, &bytes(&row["plan"])).unwrap();
            decode_receipt(&spec, &plan, wire).map(|r| encode_receipt(&spec, &plan, &r).unwrap())
        };
        assert_eq!(
            result.is_ok(),
            row["valid"].as_bool().unwrap(),
            "{}",
            row["name"]
        );
        if let Ok(output) = result {
            assert_eq!(output, wire, "Python canonical bytes");
            if row["name"] == "absent" {
                assert_eq!(output.len(), 477);
            }
            if row["name"] == "present" {
                assert_eq!(output.len(), 631);
            }
            row["wire"] = String::from_utf8(output).unwrap().into();
            let mut invalid = row.clone();
            let mut body: Value = serde_json::from_str(row["wire"].as_str().unwrap()).unwrap();
            let field = if row["plan"].is_null() {
                "artifacts"
            } else {
                "roles"
            };
            body[field].as_array_mut().unwrap().reverse();
            invalid["wire"] = serde_json::to_string(&body).unwrap().into();
            invalid["valid"] = false.into();
            golden.push_str(&serde_json::to_string(&invalid).unwrap());
            golden.push('\n');
        }
        golden.push_str(&serde_json::to_string(&row).unwrap());
        golden.push('\n');
    }
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/reference-codec-rust.jsonl");
    if std::env::var("BATC_UPDATE_REFERENCE_RUST").as_deref() == Ok("1") {
        std::fs::write(&path, &golden).unwrap();
    }
    assert_eq!(std::fs::read_to_string(path).unwrap(), golden);
}

#[test]
fn trusted_dispatch_exact_keys_states_and_raw_bounds() {
    assert!(for_action("setup.connection.apply").is_err());
    assert!(for_action("unknown").is_err());
    let spec = for_action(ACTION).unwrap();
    let old: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/native-plan-manifest.json"
    ))
    .unwrap();
    assert!(decode_plan(&spec, &bytes(&old["create"])).is_err());
    let raw = bytes(&plan());
    for extra in [
        "\"kind\":\"reference_detach\",",
        "\"Kind\":\"reference_detach\",",
    ] {
        let duplicate = format!("{{{extra}{}", std::str::from_utf8(&raw[1..]).unwrap());
        assert!(decode_plan(&spec, duplicate.as_bytes()).is_err());
    }
    for key in plan().as_object().unwrap().keys() {
        let mut v = plan();
        v.as_object_mut().unwrap().remove(key);
        assert!(decode_plan(&spec, &bytes(&v)).is_err());
    }
    let mut swapped = plan();
    swapped["artifacts"].as_array_mut().unwrap().reverse();
    assert!(decode_plan(&spec, &bytes(&swapped)).is_err());
    let mut empty = plan();
    empty["artifacts"][0]["before"]["digest"] = bat_fleet_core::digest(b"").into();
    assert!(decode_plan(&spec, &bytes(&empty)).is_ok());
    let mut padded = raw;
    padded.resize(
        vocabulary()["bounds"]["native_plan_bytes"]
            .as_u64()
            .unwrap() as usize,
        b' ',
    );
    assert!(decode_plan(&spec, &padded).is_ok());
    padded.push(b' ');
    assert_eq!(decode_plan(&spec, &padded).unwrap_err(), CodecError::Bound);
}

#[test]
fn receipt_matrix_evidence_and_bounds() {
    let spec = for_action(ACTION).unwrap();
    for row in rows()
        .into_iter()
        .filter(|r| !r["plan"].is_null() && r["valid"] == true)
    {
        let plan = decode_plan(&spec, &bytes(&row["plan"])).unwrap();
        let body: Value = serde_json::from_str(row["wire"].as_str().unwrap()).unwrap();
        for key in body.as_object().unwrap().keys() {
            let mut v = body.clone();
            v.as_object_mut().unwrap().remove(key);
            assert!(decode_receipt(&spec, &plan, &bytes(&v)).is_err());
        }
        for key in ["epoch", "Epoch"] {
            let raw = format!(
                "{{\"{key}\":1,{}",
                &serde_json::to_string(&body).unwrap()[1..]
            );
            assert!(decode_receipt(&spec, &plan, raw.as_bytes()).is_err());
        }
        for phase in vocabulary()["phases"].as_array().unwrap() {
            for code in std::iter::once(Value::Null).chain(
                vocabulary()["codes"]
                    .as_object()
                    .unwrap()
                    .keys()
                    .chain(vocabulary()["v2"]["codes"].as_object().unwrap().keys())
                    .map(|k| json!(k)),
            ) {
                let mut v = body.clone();
                v["phase"] = phase.clone();
                v["code"] = code.clone();
                let allowed = vocabulary()["v2"]["effect_kinds"]["reference_detach"]["matrix"]
                    [body["outcome"].as_str().unwrap()][phase.as_str().unwrap()]
                .as_array()
                .is_some_and(|codes| codes.contains(&code));
                assert_eq!(decode_receipt(&spec, &plan, &bytes(&v)).is_ok(), allowed);
            }
        }
        for key in ["before", "after"] {
            let mut v = body.clone();
            v["roles"][0][key] = if key == "after" && v["roles"][0]["observation"] == "absent" {
                json!({"state":"present", "digest":"a".repeat(64)})
            } else {
                json!({"state":"absent"})
            };
            assert!(decode_receipt(&spec, &plan, &bytes(&v)).is_err());
        }
        let mut raw = bytes(&body);
        raw.resize(
            vocabulary()["bounds"]["receipt_bytes"].as_u64().unwrap() as usize,
            b' ',
        );
        assert!(decode_receipt(&spec, &plan, &raw).is_ok());
        raw.push(b' ');
        assert_eq!(
            decode_receipt(&spec, &plan, &raw).unwrap_err(),
            CodecError::Bound
        );
        let mut oversized = body.clone();
        oversized["installation_ref"] = "x".repeat(8193).into();
        assert_eq!(
            NativeReceiptV2::from_value(&spec, &plan, oversized).unwrap_err(),
            CodecError::Bound
        );
    }
}
