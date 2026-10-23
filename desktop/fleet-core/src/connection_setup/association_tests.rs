use super::*;
use crate::{
    connection_publish::{FrozenHistory, FrozenPlanRef},
    creation_evidence::*,
};
mod generator {
    use crate as bat_fleet_core;
    include!("../../tests/support/connection_setup.rs");
}

fn original(input: &ReviewedSetup) -> (FrozenHistory, Vec<u8>) {
    let plan = build(input).unwrap();
    let bytes = frozen::encode(&plan).unwrap();
    let reference = FrozenPlanRef {
        review_digest: plan.review_digest.clone(),
        sha256: digest(&bytes),
    };
    (FrozenHistory::decode(&reference, &bytes).unwrap(), bytes)
}
fn association() -> Record {
    let mut input = generator::setup();
    input.owner.creation_receipt =
        format_reference(&input.owner.installation_id, &"a".repeat(64)).unwrap();
    let (history, _) = original(&input);
    let binding = Binding {
        provisioning_sha256: "a".repeat(64),
        startup_installation_ref: "12345678-1234-1234-1234-123456789abc".into(),
        fleet_creation_id: "a".repeat(32),
        owner_sid: "S-1-5-21-1234".into(),
        session_id: 1,
        pointer: std::env::current_dir().unwrap().join("native/fleet.json"),
        kit_root: history.plan().root.clone(),
    };
    let accepted = AcceptedTarget::new(
        &format!("op_{}", "b".repeat(32)),
        &json!({"installation_ref":"desktop-a"}),
    )
    .unwrap();
    Record::ConnectionAssociation(Box::new(
        ConnectionAssociation::new(
            binding,
            accepted,
            AssociationContext {
                server_id: "server-example".into(),
                principal_id: "c".repeat(64),
                expected_revision: 0,
                outer_review_digest: "d".repeat(64),
                effect_id: format!("effect_{}", "e".repeat(64)),
                configuration_id: "config-example".into(),
                configuration_sha256: "f".repeat(64),
                source_descriptor_sha256: "1".repeat(64),
                accepted_context_sha256: "2".repeat(64),
                publication_link_sha256: "3".repeat(64),
                predecessor_association_sha256: None,
            },
            &history,
        )
        .unwrap(),
    ))
}
fn document(record: &Record) -> Value {
    serde_json::from_slice(&record.encode().unwrap()).unwrap()
}
fn rejects(record: &Record, value: &Value) {
    assert!(Record::decode(&record.key(), &serde_json::to_vec(value).unwrap()).is_err());
}

#[test]
fn association_projection_preserves_original_golden_and_frozen_bytes() {
    let mut input = generator::setup();
    input.root = "//example.invalid/synthetic-fleet".into();
    let (history, bytes) = original(&input);
    let golden: Value = serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/native-plan-manifest.json"
    ))
    .unwrap();
    assert_eq!(
        serde_json::to_value(
            crate::connection_contract::native_plan_manifest(history.plan()).unwrap()
        )
        .unwrap(),
        golden["create"]
    );
    let projection = history.association_projection().unwrap();
    assert_eq!(projection.profile_key, "example-profile");
    assert_eq!(projection.remote_profile_key, "workspace-profile");
    assert_eq!(
        projection.host_key,
        format!("bat-{}", &digest(b"example-profile")[..24])
    );
    assert_eq!(projection.route_key, "bat-example");
    assert_eq!(projection.route_source_key, input.route.source.key);
    assert_eq!(projection.user_ssh_sha256, digest(&input.ssh_source));
    assert!(projection.connector_route_key.is_none() && projection.dashboard_route_key.is_none());
    assert_eq!(frozen::encode(history.plan()).unwrap(), bytes);
    assert_eq!(
        history.reference().review_digest,
        golden["create"]["review_digest"]
    );
}

#[test]
fn association_same_display_names_keep_distinct_original_keys_and_sources() {
    let mut a = generator::setup();
    let mut b = a.clone();
    let mut index: Value =
        serde_json::from_slice(&generator::profile_index("second-id", "127.0.0.3")).unwrap();
    let first: Value =
        serde_json::from_slice(&generator::profile_index("example-profile", "127.0.0.2")).unwrap();
    index["profiles"]
        .as_array_mut()
        .unwrap()
        .push(first["profiles"][0].clone());
    let index = serde_json::to_vec(&index).unwrap();
    a.selected = generator::select(&index, "example-profile").unwrap();
    b.selected = generator::select(&index, "second-id").unwrap();
    b.ssh_source = String::from_utf8(generator::source("127.0.0.3"))
        .unwrap()
        .replace("bat-example", "bat-second")
        .into_bytes();
    b.forward = Some(
        read_ssh(&b.ssh_source)
            .unwrap()
            .association("bat-second")
            .unwrap(),
    );
    b.route.source = SourceReference {
        key: "second-reviewed-route".into(),
        sha256: digest(b"second route"),
    };
    assert_eq!(a.selected.profile().name, b.selected.profile().name);
    assert_eq!(a.label, b.label);
    let left = original(&a).0.association_projection().unwrap();
    let right = original(&b).0.association_projection().unwrap();
    assert_eq!(left.profile_key, "example-profile");
    assert_eq!(right.profile_key, "second-id");
    assert_ne!(left.host_key, right.host_key);
    assert_ne!(left.route_key, right.route_key);
    assert_ne!(left.route_source_sha256, right.route_source_sha256);
    assert_ne!(left.user_ssh_sha256, right.user_ssh_sha256);
    assert_eq!(left.selected_profile_source_sha256, digest(&index));
}

#[test]
fn association_uses_retained_inventory_key_not_a_name_based_reconstruction() {
    let mut input = generator::setup();
    let plan = build(&input).unwrap();
    let mut inventory: Value =
        serde_json::from_slice(&plan.output(Artifact::Inventory).bytes).unwrap();
    inventory["hosts"][0]["name"] = json!("retained-native-key");
    let bytes = serde_json::to_vec(&inventory).unwrap();
    input.existing.insert(
        Artifact::Inventory,
        ExistingFile {
            sha256: digest(&bytes),
            bytes: bytes.clone(),
            ownership: FileOwnership::Owned {
                owner: input.owner.clone(),
                artifact: Artifact::Inventory,
                sha256: digest(&bytes),
            },
        },
    );
    assert_eq!(
        original(&input)
            .0
            .association_projection()
            .unwrap()
            .host_key,
        "retained-native-key"
    );
}

#[test]
fn association_versions_are_closed_and_old_records_remain_byte_identical() {
    let record = association();
    let bytes = record.encode().unwrap();
    assert_eq!(document(&record)["version"], 2);
    assert_eq!(Record::decode(&record.key(), &bytes).unwrap(), record);
    assert!(Record::decode_for_reader(&record.key(), &bytes, 1).is_err());
    let Record::ConnectionAssociation(r) = &record else {
        unreachable!()
    };
    let old = Record::Directory(DirectoryCreated {
        binding: r.binding.clone(),
        canonical_path: r.binding.kit_root.clone(),
        parent_identity: ObjectIdentity {
            volume: 1,
            file_id: "1".repeat(32),
        },
        identity: ObjectIdentity {
            volume: 1,
            file_id: "2".repeat(32),
        },
    });
    let old_bytes = old.encode().unwrap();
    for reader in [1, 2] {
        assert_eq!(
            Record::decode_for_reader(&old.key(), &old_bytes, reader)
                .unwrap()
                .encode()
                .unwrap(),
            old_bytes
        );
    }
    for (version, minimum) in [(1, 1), (1, 2), (2, 1), (3, 3)] {
        let mut value = document(&record);
        value["version"] = version.into();
        value["minimum_reader_version"] = minimum.into();
        rejects(&record, &value);
    }
    let mut future = document(&record);
    future["record"]["kind"] = json!("unknown-family");
    rejects(&record, &future);
    assert!(
        Record::decode_for_reader(&record.key(), &serde_json::to_vec(&future).unwrap(), 1).is_err()
    );
    let mut promoted = document(&old);
    promoted["version"] = 2.into();
    promoted["minimum_reader_version"] = 2.into();
    rejects(&old, &promoted);
}

#[test]
fn association_requires_every_nullable_route_and_context_field() {
    let record = association();
    for parent in ["projection", "context", "accepted", "binding"] {
        let value = document(&record);
        for key in value["record"]["body"][parent].as_object().unwrap().keys() {
            let mut missing = value.clone();
            missing["record"]["body"][parent]
                .as_object_mut()
                .unwrap()
                .remove(key);
            rejects(&record, &missing);
        }
    }
    for key in document(&record)["record"]["body"]
        .as_object()
        .unwrap()
        .keys()
    {
        let mut missing = document(&record);
        missing["record"]["body"]
            .as_object_mut()
            .unwrap()
            .remove(key);
        rejects(&record, &missing);
    }
}

#[test]
fn association_bounds_refuse_without_truncation_or_private_fields() {
    let record = association();
    for key in [
        "host_key",
        "profile_key",
        "remote_profile_key",
        "route_key",
        "route_source_key",
        "connector_route_key",
        "dashboard_route_key",
    ] {
        let mut value = document(&record);
        value["record"]["body"]["projection"][key] = json!("é".repeat(128));
        assert!(Record::decode(&record.key(), &serde_json::to_vec(&value).unwrap()).is_ok());
        value["record"]["body"]["projection"][key] = json!("é".repeat(129));
        rejects(&record, &value);
        value["record"]["body"]["projection"][key] = json!("x".repeat(257));
        rejects(&record, &value);
    }
    for field in ["challenge", "credential", "token", "endpoint", "diagnostic"] {
        let mut value = document(&record);
        value["record"]["body"][field] = json!("not permitted");
        rejects(&record, &value);
    }
    for parent in ["projection", "context", "accepted", "binding", "frozen"] {
        let mut value = document(&record);
        value["record"]["body"][parent]["unknown"] = json!(true);
        rejects(&record, &value);
    }
    let bytes = record.encode().unwrap();
    let duplicate = String::from_utf8(bytes.clone()).unwrap().replacen(
        "\"version\":2",
        "\"version\":2,\"version\":2",
        1,
    );
    assert!(Record::decode(&record.key(), duplicate.as_bytes()).is_err());
    let mut padded = bytes.clone();
    padded.resize(RECORD_LIMIT + 1, b' ');
    assert!(Record::decode(&record.key(), &padded).is_err());
    let mut deep = document(&record);
    deep["record"]["body"]["projection"]["host_key"] = json!([[[[[[[[["x"]]]]]]]]]);
    rejects(&record, &deep);
    for hash in ["A".repeat(64), "a".repeat(63), "g".repeat(64)] {
        let mut value = document(&record);
        value["record"]["body"]["projection"]["user_ssh_sha256"] = json!(hash);
        rejects(&record, &value);
    }
}

#[test]
fn association_key_is_a_canonical_tuple_hash_and_not_a_path_from_input() {
    let record = association();
    let Record::ConnectionAssociation(r) = &record else {
        unreachable!()
    };
    let tuple = json!([
        r.context.server_id,
        r.context.principal_id,
        r.binding.startup_installation_ref,
        r.accepted.installation_ref(),
        r.connection_id,
        r.accepted.operation_id()
    ]);
    assert_eq!(
        record.key().relative_path().unwrap(),
        PathBuf::from(format!(
            "association-{}.json",
            digest(&serde_json::to_vec(&tuple).unwrap())
        ))
    );
    assert!(Record::decode(
        &EvidenceKey::ConnectionAssociation("0".repeat(64)),
        &record.encode().unwrap()
    )
    .is_err());
    let mut value = document(&record);
    value["record"]["body"]["context"]["server_id"] = json!("../server/範例");
    let modified: Record = serde_json::from_value(value["record"].clone()).unwrap();
    assert_eq!(
        modified.key().relative_path().unwrap().components().count(),
        1
    );
}

#[test]
fn association_server_identity_matches_real_python_fixture() {
    let fixture: Value = serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/connection-association-targets.json"
    ))
    .unwrap();
    for row in fixture.as_array().unwrap() {
        let accepted =
            AcceptedTarget::new(row["operation_id"].as_str().unwrap(), &row["target"]).unwrap();
        assert_eq!(accepted.connection_id().unwrap(), row["connection_id"]);
    }
    for operation in ["op_ABC", "op_", "not-an-operation"] {
        assert!(AcceptedTarget::new(operation, &json!({"installation_ref":"desktop-a"})).is_err());
    }
    assert!(AcceptedTarget::new(
        &format!("op_{}", "b".repeat(32)),
        &json!({"installation_ref":"desktop-a", "connection_id":"arbitrary"})
    )
    .is_err());
    let record = association();
    let mut value = document(&record);
    value["record"]["body"]["connection_id"] = json!(format!("conn_{}", "f".repeat(32)));
    rejects(&record, &value);
}

#[test]
fn association_server_scope_uses_character_bounds_and_refuses_padding() {
    let operation = format!("op_{}", "b".repeat(32));
    let installation = "機".repeat(128);
    let accepted =
        AcceptedTarget::new(&operation, &json!({"installation_ref":installation})).unwrap();
    assert_eq!(accepted.installation_ref(), installation);
    for invalid in ["機".repeat(129), " desktop-a".into(), "desktop-a ".into()] {
        assert!(AcceptedTarget::new(&operation, &json!({"installation_ref":invalid})).is_err());
    }
    let record = association();
    let mut value = document(&record);
    value["record"]["body"]["context"]["server_id"] = json!("機".repeat(128));
    let parsed: Record = serde_json::from_value(value["record"].clone()).unwrap();
    assert!(Record::decode(&parsed.key(), &serde_json::to_vec(&value).unwrap()).is_ok());
}
