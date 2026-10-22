use bat_fleet_core::{
    connection_setup::{build, OwnerEvidence},
    digest,
};

#[path = "support/connection_setup.rs"]
mod fixture;

#[test]
fn creation_reference_is_accepted_by_the_generator() {
    let mut input = fixture::setup();
    let fleet_creation_id = "11111111111111111111111111111111";
    let receipt_sha256 = digest(b"synthetic native provisioning receipt");
    input.owner = OwnerEvidence {
        installation_id: fleet_creation_id.into(),
        owner_sid: "S-1-5-21-123-456-789-1001".into(),
        creation_receipt: format!("fleet-creation:v1:{fleet_creation_id}:{receipt_sha256}"),
    };

    // Syntax acceptance is necessary for the generator to carry a receipt
    // reference. It does not establish receipt provenance or file ownership.
    let plan = build(&input);
    assert!(plan.is_ok(), "creation reference rejected: {plan:?}");
}
