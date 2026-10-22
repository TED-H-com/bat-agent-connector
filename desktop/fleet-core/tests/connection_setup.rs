use bat_fleet_core::{
    connection_setup::{
        allocate_loopback, read_ssh, AddressUse, Gap, ProvenAssociation, SelectedProfile,
        SSH_BYTE_LIMIT, SSH_LINE_LENGTH, SSH_LINE_LIMIT,
    },
    credential::AdmissionStore,
    digest, source_catalog,
};
use serde_json::{json, Value};

fn profile_index(id: &str, address: &str) -> Vec<u8> {
    serde_json::to_vec(&json!({"profiles":[{
        "id":id,"type":"remote","name":"Example BAT","remoteHost":address,
        "remotePort":19001,"remoteProfileId":"workspace-profile","remoteProfileName":"Workspace profile",
        "remoteFingerprint":"AB".repeat(32),"createdAt":123,"updatedAt":456
    }],"activeProfileIds":[],"metadata":{"retained":true}})).unwrap()
}

fn select(index: &[u8], id: &str) -> Result<SelectedProfile, Gap> {
    let tokens = serde_json::to_vec(
        &json!({"enc":false,"data":json!({"tokens":{id:"synthetic-bat-token"}}).to_string()}),
    )
    .unwrap();
    let catalog = source_catalog::parse(index, &AdmissionStore::parse(&tokens));
    SelectedProfile::from_catalog(&catalog, index, id)
}

fn source(address: &str) -> Vec<u8> {
    format!("# source authority\nHost bat-example\n HostName example.test\n Port 2222\n ProxyCommand helper %h\n LocalForward {address}:19001 127.0.0.1:9000\n").into_bytes()
}

#[test]
fn selection_reuses_catalog_admission_and_rejects_ambiguity_or_drift() {
    let index = profile_index("example-profile", "127.0.0.2");
    let selected = select(&index, "example-profile").unwrap();
    assert_eq!(selected.profile().remote_profile_id, "workspace-profile");
    let mut duplicate: Value = serde_json::from_slice(&index).unwrap();
    let mut other = duplicate["profiles"][0].clone();
    other["id"] = json!("EXAMPLE-PROFILE");
    duplicate["profiles"].as_array_mut().unwrap().push(other);
    assert_eq!(
        select(&serde_json::to_vec(&duplicate).unwrap(), "example-profile"),
        Err(Gap::AmbiguousProfile)
    );
    let unavailable = source_catalog::parse(&index, &AdmissionStore::unreadable());
    assert_eq!(
        SelectedProfile::from_catalog(&unavailable, &index, "example-profile"),
        Err(Gap::CredentialUnavailable)
    );
    let changed = profile_index("example-profile", "127.0.0.3");
    assert_eq!(
        SelectedProfile::from_catalog(&unavailable, &changed, "example-profile"),
        Err(Gap::SourceChanged)
    );
}

#[test]
fn allocation_is_bounded_deterministic_and_never_reselects_at_recheck() {
    let selected = select(
        &profile_index("example-profile", "127.0.0.4"),
        "example-profile",
    )
    .unwrap();
    let forward = read_ssh(&source("127.0.0.4"))
        .unwrap()
        .association("bat-example")
        .unwrap();
    let proven = ProvenAssociation::new(&selected, Some(&forward)).unwrap();
    let mut addresses = AddressUse::default();
    addresses.configured.insert("127.0.0.2".parse().unwrap());
    addresses.listening.insert("127.0.0.3".parse().unwrap());
    let allocation = allocate_loopback(&proven, &addresses).unwrap();
    assert_eq!(allocation.local.to_string(), "127.0.0.4:19001");
    assert_eq!(allocation, allocate_loopback(&proven, &addresses).unwrap());
    addresses.listening.insert(*allocation.local.ip());
    assert_eq!(allocation.recheck(&addresses), Err(Gap::ReviewChanged));
    assert_eq!(allocation.local.to_string(), "127.0.0.4:19001");
    let empty = AddressUse::default();
    assert_eq!(
        allocate_loopback(&proven, &empty)
            .unwrap()
            .local
            .ip()
            .to_string(),
        "127.0.0.2"
    );
    addresses
        .configured
        .extend((2..=254).map(|last| std::net::Ipv4Addr::new(127, 0, 0, last)));
    assert_eq!(
        allocate_loopback(&proven, &addresses),
        Err(Gap::PoolExhausted)
    );
    assert_eq!(
        ProvenAssociation::new(&selected, None),
        Err(Gap::MissingMapping)
    );
    let mismatched = read_ssh(&source("127.0.0.9"))
        .unwrap()
        .association("bat-example")
        .unwrap();
    assert_eq!(
        ProvenAssociation::new(&selected, Some(&mismatched)),
        Err(Gap::ProfileEndpointMismatch)
    );
}

#[test]
fn reader_binds_complete_literal_forward_and_original_bytes() {
    let bytes = b"# retained\r\nHost bat-example\r\n  HostName example.test\r\n  Port 2222\r\n  ProxyCommand helper %h\r\n  LocalForward 127.0.0.2:19001 127.0.0.1:9000\r\n";
    let parsed = read_ssh(bytes).unwrap();
    let forward = parsed.association("bat-example").unwrap();
    assert_eq!(forward.source_digest(), digest(bytes));
    assert_eq!(forward.local().to_string(), "127.0.0.2:19001");
    assert_eq!(forward.target(), "127.0.0.1:9000");
    assert_eq!(parsed.association("absent"), Err(Gap::MissingMapping));
}

#[test]
fn duplicate_forwards_are_not_reduced_to_one_mapping() {
    let bytes = b"Host bat-example\n LocalForward 127.0.0.2:19001 127.0.0.1:9000\n LocalForward 127.0.0.2:19001 127.0.0.1:9000\n";
    assert_eq!(
        read_ssh(bytes).unwrap().association("bat-example"),
        Err(Gap::DuplicateForwards)
    );
}

#[test]
fn reader_bounds_and_unrepresentable_topology_are_reference_only() {
    for (bytes, gap) in [
        (vec![b'a'; SSH_BYTE_LIMIT + 1], Gap::SshTooLarge),
        (vec![b'\n'; SSH_LINE_LIMIT + 1], Gap::SshTooManyLines),
        (vec![b'a'; SSH_LINE_LENGTH + 1], Gap::SshLineTooLong),
        (vec![0xff], Gap::SshEncoding),
        (b"Match exec touch-never\n".to_vec(), Gap::UnsupportedMatch),
        (b"Include config\n".to_vec(), Gap::UnsupportedInclude),
        (
            b"Include child.conf\n# child.conf includes config\n".to_vec(),
            Gap::UnsupportedInclude,
        ),
        (b"Host *.test\n".to_vec(), Gap::UnsupportedSshSyntax),
        (
            b"Host example\n LocalForward ${PORT} %h:9000\n".to_vec(),
            Gap::UnsupportedSshSyntax,
        ),
    ] {
        assert_eq!(read_ssh(&bytes), Err(gap));
    }
}
