use bat_fleet_core::connection_setup::{
    read_ssh, AddressUse, EndpointChoice, Gap, OwnerEvidence, Preferences, ReviewedRoute,
    ReviewedSetup, RouteKind, SelectedProfile, SourceReference, WorkspaceBinding,
};
use bat_fleet_core::{credential::AdmissionStore, digest, source_catalog};
use serde_json::json;
use std::collections::BTreeMap;

pub fn setup() -> ReviewedSetup {
    let bytes = source("127.0.0.2");
    ReviewedSetup {
        root: std::env::current_dir().unwrap().join("synthetic-fleet"),
        owner: OwnerEvidence {
            installation_id: "a".repeat(32),
            owner_sid: "S-1-5-21-1234".into(),
            creation_receipt: "synthetic-installation-receipt".into(),
        },
        selected: select(
            &profile_index("example-profile", "127.0.0.2"),
            "example-profile",
        )
        .unwrap(),
        forward: Some(
            read_ssh(&bytes)
                .unwrap()
                .association("bat-example")
                .unwrap(),
        ),
        ssh_source: bytes,
        route: ReviewedRoute {
            source: SourceReference {
                key: "authorized-lan-route".into(),
                sha256: digest(b"synthetic-route"),
            },
            kind: RouteKind::Lan {
                probe: bat_fleet_core::inventory::endpoint("192.0.2.10:22", false).unwrap(),
            },
        },
        bindings: vec![WorkspaceBinding {
            id: "binding-stable".into(),
            workspace_id: "server-workspace".into(),
            required: true,
        }],
        label: "Example machine / 範例機器".into(),
        preferences: Preferences {
            background_connections: true,
            bat_windows: false,
            dashboard: false,
            login: false,
        },
        endpoint_choice: EndpointChoice::Preserve,
        addresses: AddressUse::default(),
        existing: BTreeMap::new(),
    }
}

pub fn profile_index(id: &str, address: &str) -> Vec<u8> {
    serde_json::to_vec(&json!({"profiles":[{
        "id":id,"type":"remote","name":"Example BAT","remoteHost":address,
        "remotePort":19001,"remoteProfileId":"workspace-profile","remoteProfileName":"Workspace profile",
        "remoteFingerprint":"AB".repeat(32),"createdAt":123,"updatedAt":456
    }],"activeProfileIds":[],"metadata":{"retained":true}})).unwrap()
}

pub fn select(index: &[u8], id: &str) -> Result<SelectedProfile, Gap> {
    let tokens = serde_json::to_vec(
        &json!({"enc":false,"data":json!({"tokens":{id:"synthetic-bat-token"}}).to_string()}),
    )
    .unwrap();
    let catalog = source_catalog::parse(index, &AdmissionStore::parse(&tokens));
    SelectedProfile::from_catalog(&catalog, index, id)
}

pub fn source(address: &str) -> Vec<u8> {
    format!("# source authority\nHost bat-example\n HostName example.test\n Port 2222\n ProxyCommand helper %h\n LocalForward {address}:19001 127.0.0.1:9000\n").into_bytes()
}
