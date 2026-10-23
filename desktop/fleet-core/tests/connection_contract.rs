use bat_fleet_core::{
    connection_contract::{artifact_role, native_plan_manifest, vocabulary},
    connection_setup::{build, Artifact, ExistingFile, FileOwnership},
};
use serde_json::{json, Value};
#[path = "support/connection_setup.rs"]
mod fixture;

#[test]
fn every_artifact_maps_exhaustively_to_the_shared_wire_order() {
    let artifacts = [
        Artifact::Inventory,
        Artifact::ProfileIndex,
        Artifact::SshReference,
        Artifact::Installation,
    ];
    let actual: Vec<_> = artifacts.into_iter().map(artifact_role).collect();
    assert_eq!(json!(actual), vocabulary()["roles"]);
}

#[test]
fn generator_manifests_match_the_cross_language_golden() {
    let mut input = fixture::setup();
    // Lexically absolute on Windows and POSIX; the builder never touches this path.
    input.root = "//example.invalid/synthetic-fleet".into();
    let plan = build(&input).unwrap();
    let created = native_plan_manifest(&plan).unwrap();
    for output in &plan.outputs {
        input.existing.insert(
            output.artifact,
            ExistingFile {
                bytes: output.bytes.clone(),
                sha256: output.sha256.clone(),
                ownership: FileOwnership::Owned {
                    owner: input.owner.clone(),
                    artifact: output.artifact,
                    sha256: output.sha256.clone(),
                },
            },
        );
    }
    let unchanged = native_plan_manifest(&build(&input).unwrap()).unwrap();
    input.existing.clear();
    input.existing.insert(
        Artifact::SshReference,
        ExistingFile {
            bytes: vec![],
            sha256: bat_fleet_core::digest(b""),
            ownership: FileOwnership::Owned {
                owner: input.owner.clone(),
                artifact: Artifact::SshReference,
                sha256: bat_fleet_core::digest(b""),
            },
        },
    );
    let empty_file = native_plan_manifest(&build(&input).unwrap()).unwrap();
    let actual = serde_json::to_string_pretty(
        &json!({"create":created,"unchanged":unchanged,"empty_file":empty_file}),
    )
    .unwrap()
        + "\n";
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/native-plan-manifest.json");
    if std::env::var("BATC_UPDATE_NATIVE_PLAN_GOLDEN").as_deref() == Ok("1") {
        std::fs::write(&path, &actual).unwrap();
    }
    assert_eq!(std::fs::read_to_string(path).unwrap(), actual);
    let value: Value = serde_json::from_str(&actual).unwrap();
    assert!(value["create"]["artifacts"]
        .as_array()
        .unwrap()
        .iter()
        .all(|a| a["before_digest"].is_null()));
    assert!(value["unchanged"]["artifacts"]
        .as_array()
        .unwrap()
        .iter()
        .all(|a| a["before_digest"] == a["after_digest"]));
    assert_eq!(
        value["empty_file"]["artifacts"][2]["before_digest"],
        bat_fleet_core::digest(b"")
    );
    let mut altered = plan.clone();
    altered.review_digest = "0".repeat(64);
    assert!(native_plan_manifest(&altered).is_err());
}
