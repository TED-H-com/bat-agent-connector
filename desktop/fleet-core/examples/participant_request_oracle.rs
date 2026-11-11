//! Synthetic request oracle. No production participant or local effects.
use bat_fleet_core::{
    connection_contract::native_plan_manifest,
    connection_participant::{request, Accepted, Intent, Scope, Target, FLEET_ACTION},
    connection_publish::FrozenHistory,
    strict_json,
};
use serde_json::{json, Value};
use std::io::{Read, Write};
#[allow(dead_code)]
#[path = "../tests/support/creation_evidence.rs"]
mod creation;

fn main() {
    let command = std::env::args().nth(1).expect("prepare or requests");
    let output = if command == "prepare" {
        let mut fixture = creation::Fixture::new();
        let plan = fixture.plan();
        let reference = fixture
            .native
            .publisher()
            .export_plan(&plan, &mut fixture.native)
            .unwrap();
        let snapshot = std::fs::read(fixture.native.frozen_path(&reference)).unwrap();
        FrozenHistory::decode(&reference, &snapshot).unwrap();
        json!({"frozen":reference,"snapshot":String::from_utf8(snapshot).unwrap(),"manifest":native_plan_manifest(&plan).unwrap()})
    } else {
        assert_eq!(command, "requests");
        let mut bytes = Vec::new();
        std::io::stdin()
            .take(34 * 1024 * 1024)
            .read_to_end(&mut bytes)
            .unwrap();
        let input = strict_json::parse(&bytes, 34 * 1024 * 1024).unwrap();
        let reference = serde_json::from_value(input["plan"]["frozen"].clone()).unwrap();
        let frozen = FrozenHistory::decode(
            &reference,
            input["plan"]["snapshot"].as_str().unwrap().as_bytes(),
        )
        .unwrap();
        let op = &input["operation"];
        let binding = &op["external_refs"]["admission_binding"];
        let text = |v: &Value| v.as_str().unwrap().to_owned();
        let intent = Intent::fleet_post(
            Accepted {
                scope: Scope {
                    server_id: text(&binding["server_id"]),
                    principal_id: text(&binding["principal_id"]),
                    installation_ref: text(&binding["installation_ref"]),
                    native_installation: "22222222-2222-4222-8222-222222222222".into(),
                },
                action: FLEET_ACTION.into(),
                contract_version: 1,
                kind: None,
                operation_id: text(&op["operation_id"]),
                operation_key: text(&op["idempotency_key"]),
                expected_revision: op["preconditions"]["expected_revision"].as_u64().unwrap(),
                review_digest: text(&binding["review_digest"]),
                credential_generation: "synthetic-original-generation".into(),
                target: Target {
                    installation_ref: text(&op["target"]["installation_ref"]),
                    connection_id: serde_json::from_value(op["target"]["connection_id"].clone())
                        .unwrap(),
                },
            },
            &frozen,
        )
        .unwrap();
        let mut output = json!({
            "claim":String::from_utf8(request::handoff(&intent,request::Mode::Claim).unwrap()).unwrap(),
            "readback":String::from_utf8(request::handoff(&intent,request::Mode::Readback).unwrap()).unwrap()
        });
        if !input["envelope"].is_null() {
            for (name, outcome, phase, code) in [
                ("refused", "refused", "preflight", "LOCAL_GATE_UNPROVEN"),
                (
                    "uncertain",
                    "uncertain",
                    "publishing",
                    "LOCAL_GATE_UNPROVEN",
                ),
                (
                    "uncertain_other",
                    "uncertain",
                    "publishing",
                    "LOCAL_OWNERSHIP_UNPROVEN",
                ),
                (
                    "abandoned",
                    "abandoned",
                    "abandonment",
                    "LOCAL_PLAN_ABANDONED",
                ),
            ] {
                let roles = input["envelope"]["native_handoff"]["native_plan"]["artifacts"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|a| {
                        let observation = if a["before_digest"].is_null() {
                            "absent"
                        } else {
                            "present"
                        };
                        json!({
                            "role": a["role"],
                            "before_digest": a["before_digest"],
                            "after_digest": a["before_digest"],
                            "observation": observation
                        })
                    })
                    .collect();
                output[name] = json!(String::from_utf8(
                    request::receipt(
                        &intent,
                        &input["envelope"],
                        request::Report {
                            outcome: outcome.into(),
                            phase: phase.into(),
                            code: Some(code.into()),
                            roles
                        }
                    )
                    .unwrap()
                )
                .unwrap());
            }
        }
        output
    };
    std::io::stdout()
        .write_all(&serde_json::to_vec(&output).unwrap())
        .unwrap();
}
