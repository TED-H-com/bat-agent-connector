use bat_fleet_core::connection_setup::{
    build, Artifact, ArtifactLocation, EndpointChoice, ExistingFile, FileOwnership, OwnerEvidence,
    PublicationPlan, ReviewedSetup, RouteKind,
};
use bat_fleet_core::{
    connection_setup::{
        allocate_loopback, read_ssh, AddressUse, Gap, ProvenAssociation, SelectedProfile,
        SSH_BYTE_LIMIT, SSH_LINE_LENGTH, SSH_LINE_LIMIT,
    },
    credential::AdmissionStore,
    digest, source_catalog,
};
use serde_json::{json, Value};
use std::path::PathBuf;
#[path = "support/connection_setup.rs"]
mod fixture;
use fixture::{profile_index, select, setup, source};

fn apply(input: &mut ReviewedSetup, plan: &PublicationPlan) {
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
}

fn document(plan: &PublicationPlan, artifact: Artifact) -> Value {
    serde_json::from_slice(&plan.output(artifact).bytes).unwrap()
}

#[test]
fn clean_plan_has_four_bounded_outputs_pointer_last_and_no_implicit_preferences() {
    let input = setup();
    let plan = build(&input).unwrap();
    assert_eq!(plan.outputs.len(), 4);
    assert_eq!(plan.steps.len(), 4);
    assert_eq!(plan.steps.last().unwrap().artifact, Artifact::Installation);
    for output in &plan.outputs {
        assert_eq!(output.sha256, digest(&output.bytes));
        assert!(output.bytes.len() <= 1024 * 1024);
    }
    for step in &plan.steps {
        assert_eq!(step.before_digest, None);
        assert_eq!(step.after_digest, plan.output(step.artifact).sha256);
        assert_eq!(step.owner, input.owner);
    }
    let inv = document(&plan, Artifact::Inventory);
    assert_eq!(inv["hosts"][0]["target"], "127.0.0.1:9000");
    assert_eq!(
        inv["hosts"][0]["bat"]["remote_profile_id"],
        "workspace-profile"
    );
    assert_eq!(
        inv["credentials"][input.selected.credential_ref()]["source_policy"],
        1
    );
    assert_eq!(
        inv["hosts"][0]["workspace_bindings"][0]["id"],
        "binding-stable"
    );
    assert_eq!(inv["hosts"][0]["connector_endpoint"], Value::Null);
    assert_eq!(inv["connector"]["ssh_alias"], Value::Null);
    assert_eq!(
        document(&plan, Artifact::ProfileIndex)["activeProfileIds"],
        json!([])
    );
    assert_eq!(plan.preferences, input.preferences);
    assert!(plan.reference_route_only);
    let ssh = String::from_utf8(plan.output(Artifact::SshReference).bytes.clone()).unwrap();
    assert!(ssh.contains("Host bat-example\n"));
    assert!(!ssh.contains("HostName"));
    assert!(!ssh.contains("LocalForward"));
    assert_eq!(plan, build(&input).unwrap());
}

#[test]
fn installation_pointer_is_separate_from_kit_relative_artifacts() {
    assert_eq!(
        Artifact::Installation.location(),
        ArtifactLocation::InstallationPointer
    );
    for (artifact, relative) in [
        (Artifact::Inventory, "client/fleet-inventory.json"),
        (Artifact::ProfileIndex, "client/bat-profiles/index.json"),
        (Artifact::SshReference, "client/ssh-config"),
    ] {
        assert_eq!(artifact.location(), ArtifactLocation::KitRelative(relative));
    }
}

struct DiskFixture(PathBuf);
impl Drop for DiskFixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn generated_bytes_round_trip_through_real_installation_and_configuration_loaders() {
    assert_round_trip(setup());
}

fn assert_round_trip(mut input: ReviewedSetup) {
    // Only tests publish. TMPDIR is set to a unique disk directory by the test command.
    let directory = std::env::temp_dir().join(format!(
        "fleet-generated-{}-{:032x}",
        std::process::id(),
        rand::random::<u128>()
    ));
    std::fs::create_dir(&directory).unwrap();
    let fixture = DiskFixture(directory);
    input.root = fixture.0.join("installation");
    let plan = build(&input).unwrap();
    assert!(!input.root.exists());
    for output in &plan.outputs {
        let path = match output.artifact.location() {
            ArtifactLocation::InstallationPointer => fixture.0.join("fleet.json"),
            ArtifactLocation::KitRelative(relative) => input.root.join(relative),
        };
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, &output.bytes).unwrap();
    }
    let user = fixture.0.join("user");
    std::fs::create_dir_all(user.join(".ssh")).unwrap();
    std::fs::write(user.join(".ssh/config"), &input.ssh_source).unwrap();
    let installation =
        bat_fleet_core::installation::Snapshot::load(&fixture.0.join("fleet.json")).unwrap();
    assert_eq!(
        installation.backend(),
        bat_fleet_core::discovery::Backend::Rust
    );
    let paths =
        bat_fleet_core::configuration::Paths::new(installation.client_root(), &user, None, None)
            .unwrap();
    let configuration = bat_fleet_core::configuration::Configuration::load(paths).unwrap();
    assert_eq!(configuration.issues, vec![]);
    configuration.verify_current().unwrap();
    assert_eq!(
        std::fs::read(user.join(".ssh/config")).unwrap(),
        input.ssh_source
    );
    // The Tauri sanitizer (src-tauri/src/fleet.rs) is outside fleet-core.
    // Status-golden verification of this inventory waits for native wiring.
}

#[test]
fn listener_ports_do_not_reserve_unrelated_services() {
    let mut input = setup();
    for socket in [
        "0.0.0.0:135",
        "0.0.0.0:445",
        "127.0.0.2:19002",
        "127.0.0.9:19001",
    ] {
        input.addresses.listening.insert(socket.parse().unwrap());
    }
    assert!(build(&input).is_ok());
    input.endpoint_choice = EndpointChoice::Allocate;
    let plan = build(&input).unwrap();
    let allocation = plan.allocation.as_ref().unwrap();
    assert_eq!(allocation.local.to_string(), "127.0.0.2:19001");
    assert_eq!(allocation.recheck(&input.addresses), Ok(()));
    assert_eq!(plan.recheck(&input), Ok(()));
}

#[test]
fn wildcard_listener_on_the_service_port_blocks_build_allocation_and_recheck() {
    let mut input = setup();
    let proven = ProvenAssociation::new(&input.selected, input.forward.as_ref()).unwrap();
    let allocation = allocate_loopback(&proven, &input.addresses).unwrap();
    input
        .addresses
        .listening
        .insert("0.0.0.0:19001".parse().unwrap());
    assert_eq!(build(&input), Err(Gap::OccupiedEndpoint));
    assert_eq!(
        allocate_loopback(&proven, &input.addresses),
        Err(Gap::PoolExhausted)
    );
    assert_eq!(
        allocation.recheck(&input.addresses),
        Err(Gap::ReviewChanged)
    );
    input.endpoint_choice = EndpointChoice::Allocate;
    assert_eq!(build(&input), Err(Gap::PoolExhausted));
}

fn setup_at(address: &str, port: u16) -> ReviewedSetup {
    let mut input = setup();
    let mut index: Value =
        serde_json::from_slice(&profile_index("example-profile", address)).unwrap();
    index["profiles"][0]["remotePort"] = json!(port);
    input.selected = select(&serde_json::to_vec(&index).unwrap(), "example-profile").unwrap();
    input.ssh_source = source(address);
    input.ssh_source = String::from_utf8(input.ssh_source)
        .unwrap()
        .replace(":19001 ", &format!(":{port} "))
        .into_bytes();
    input.forward = Some(
        read_ssh(&input.ssh_source)
            .unwrap()
            .association("bat-example")
            .unwrap(),
    );
    input
}

#[test]
fn a_validated_first_loopback_forward_is_preserved_and_loads() {
    let input = setup_at("127.0.0.1", 19001);
    let plan = build(&input).unwrap();
    assert_eq!(
        document(&plan, Artifact::Inventory)["hosts"][0]["local"],
        "127.0.0.1:19001"
    );
    assert_round_trip(input);
}

#[test]
fn connector_reserves_its_exact_socket_while_bat_hosts_reserve_their_address() {
    assert_eq!(
        build(&setup_at("127.0.0.1", 19796)),
        Err(Gap::OccupiedEndpoint)
    );
    let mut input = setup_at("127.0.0.2", 19002);
    input
        .addresses
        .configured
        .insert("127.0.0.2".parse().unwrap());
    assert_eq!(build(&input), Err(Gap::OccupiedEndpoint));
}

#[test]
fn existing_metadata_profiles_backend_and_ssh_bytes_survive_and_reapply_is_noop() {
    let mut input = setup();
    let first = build(&input).unwrap();
    apply(&mut input, &first);
    assert!(build(&input).unwrap().steps.is_empty());
    let mut index = document(&first, Artifact::ProfileIndex);
    index["metadata"] = json!({"extension":[1,"unchanged"]});
    index["profiles"]
        .as_array_mut()
        .unwrap()
        .push(json!({"id":"other-profile","type":"local","createdAt":77}));
    index["activeProfileIds"] = json!(["other-profile"]);
    replace_existing(
        &mut input,
        Artifact::ProfileIndex,
        serde_json::to_vec(&index).unwrap(),
    );
    let ssh = b"# do not normalize\r\nInclude other.conf\r\nHost bat-example\r\n Port 2222\r\n ProxyCommand helper %h\r\n".to_vec();
    replace_existing(&mut input, Artifact::SshReference, ssh.clone());
    let pointer =
        serde_json::to_vec(&json!({"kit_root":input.root,"backend":"powershell"})).unwrap();
    replace_existing(&mut input, Artifact::Installation, pointer.clone());
    input.preferences.bat_windows = true;
    input.preferences.background_connections = false;
    input.preferences.dashboard = true;
    input.preferences.login = true;
    let plan = build(&input).unwrap();
    assert_eq!(plan.output(Artifact::Installation).bytes, pointer);
    assert_eq!(plan.output(Artifact::SshReference).bytes, ssh);
    let after = document(&plan, Artifact::ProfileIndex);
    assert_eq!(after["metadata"], index["metadata"]);
    assert_eq!(after["profiles"], index["profiles"]);
    assert_eq!(
        after["activeProfileIds"],
        json!(["other-profile", "example-profile"])
    );
    assert_eq!(plan.preferences, input.preferences);
    assert_eq!(plan, build(&input).unwrap());
    apply(&mut input, &plan);
    assert!(build(&input).unwrap().steps.is_empty());
}

fn replace_existing(input: &mut ReviewedSetup, artifact: Artifact, bytes: Vec<u8>) {
    let sha256 = digest(&bytes);
    input.existing.insert(
        artifact,
        ExistingFile {
            bytes,
            sha256: sha256.clone(),
            ownership: FileOwnership::Owned {
                owner: input.owner.clone(),
                artifact,
                sha256,
            },
        },
    );
}

#[test]
fn required_gaps_return_errors_instead_of_partial_steps() {
    let mut input = setup();
    input.forward = None;
    assert_eq!(build(&input), Err(Gap::MissingMapping));
    let mut input = setup();
    input
        .addresses
        .listening
        .insert("127.0.0.2:19001".parse().unwrap());
    assert_eq!(build(&input), Err(Gap::OccupiedEndpoint));
    let mut input = setup();
    input
        .ssh_source
        .extend_from_slice(b" LocalForward 127.0.0.2:19001 127.0.0.1:9000\n");
    assert_eq!(build(&input), Err(Gap::DuplicateForwards));
    let mut input = setup();
    input.ssh_source.extend_from_slice(b"Include config\n");
    assert_eq!(build(&input), Err(Gap::UnsupportedInclude));
    let mut input = setup();
    let first = build(&input).unwrap();
    apply(&mut input, &first);
    input
        .existing
        .get_mut(&Artifact::Inventory)
        .unwrap()
        .ownership = FileOwnership::Unknown;
    assert_eq!(build(&input), Err(Gap::UnknownOwner));
    input
        .existing
        .get_mut(&Artifact::Inventory)
        .unwrap()
        .ownership = FileOwnership::ReferenceOnly;
    input.label = "Changed label".into();
    assert_eq!(build(&input), Err(Gap::ReferenceOnly(Artifact::Inventory)));
}

#[test]
fn allocation_never_rewrites_a_bat_profile_to_fit_a_new_address() {
    let mut input = setup();
    input.endpoint_choice = EndpointChoice::Allocate;
    let plan = build(&input).unwrap();
    assert_eq!(
        plan.allocation.unwrap().local.to_string(),
        "127.0.0.2:19001"
    );
    input
        .addresses
        .configured
        .insert("127.0.0.2".parse().unwrap());
    assert_eq!(build(&input), Err(Gap::AllocationRequiresSourcedMapping));
}

#[test]
fn allocation_proposal_requires_sourced_endpoint_changes() {
    let mut input = setup();
    input.endpoint_choice = EndpointChoice::Allocate;
    input
        .addresses
        .configured
        .insert("127.0.0.2".parse().unwrap());
    let proven = ProvenAssociation::new(&input.selected, input.forward.as_ref()).unwrap();
    let proposal = allocate_loopback(&proven, &input.addresses).unwrap();
    assert_eq!(proposal.local.to_string(), "127.0.0.3:19001");
    assert_eq!(build(&input), Err(Gap::AllocationRequiresSourcedMapping));
    assert_eq!(
        input.forward.as_ref().unwrap().local().to_string(),
        "127.0.0.2:19001"
    );
    assert_eq!(input.selected.profile().endpoint.host, "127.0.0.2");
}

#[test]
fn new_connection_preserves_unrelated_host_profile_and_owned_ssh_prefix() {
    let mut input = setup();
    let first = build(&input).unwrap();
    apply(&mut input, &first);
    let old_host = document(&first, Artifact::Inventory)["hosts"][0].clone();
    let old_profile = document(&first, Artifact::ProfileIndex)["profiles"][0].clone();
    let old_ssh = first.output(Artifact::SshReference).bytes.clone();
    input.selected = select(
        &profile_index("second-profile", "127.0.0.3"),
        "second-profile",
    )
    .unwrap();
    input.ssh_source.extend_from_slice(b"Host second-example\n HostName second.example.test\n LocalForward 127.0.0.3:19001 127.0.0.1:9000\n");
    input.forward = Some(
        read_ssh(&input.ssh_source)
            .unwrap()
            .association("second-example")
            .unwrap(),
    );
    let plan = build(&input).unwrap();
    assert_eq!(document(&plan, Artifact::Inventory)["hosts"][0], old_host);
    assert_eq!(
        document(&plan, Artifact::ProfileIndex)["profiles"][0],
        old_profile
    );
    assert!(plan
        .output(Artifact::SshReference)
        .bytes
        .starts_with(&old_ssh));
    assert_eq!(plan.steps.len(), 3);
    assert!(!plan
        .steps
        .iter()
        .any(|step| step.artifact == Artifact::Installation));
    for step in &plan.steps {
        assert_eq!(
            step.before_digest.as_ref(),
            Some(&input.existing[&step.artifact].sha256)
        );
    }
    apply(&mut input, &plan);
    assert!(build(&input).unwrap().steps.is_empty());
}

#[test]
fn review_binds_all_sources_preferences_and_owner_evidence() {
    let input = setup();
    let plan = build(&input).unwrap();
    assert_eq!(plan.recheck(&input), Ok(()));
    let mut changed = input.clone();
    changed
        .addresses
        .listening
        .insert("127.0.0.2:19001".parse().unwrap());
    assert_eq!(plan.recheck(&changed), Err(Gap::ReviewChanged));
    let mut changed = input.clone();
    changed.preferences.login = true;
    assert_eq!(plan.recheck(&changed), Err(Gap::ReviewChanged));
    let mut changed = input.clone();
    changed.route.source.sha256 = digest(b"changed-authority");
    assert_eq!(plan.recheck(&changed), Err(Gap::ReviewChanged));
    let mut changed = input.clone();
    changed
        .ssh_source
        .extend_from_slice(b"# changed external bytes\n");
    assert_eq!(build(&changed), Err(Gap::SourceChanged));
    let mut changed = input;
    apply(&mut changed, &plan);
    changed
        .existing
        .get_mut(&Artifact::Inventory)
        .unwrap()
        .sha256 = digest(b"third bytes");
    assert_eq!(build(&changed), Err(Gap::SourceChanged));
}

#[test]
fn ownership_is_bound_to_file_digest_installation_and_principal() {
    let mut input = setup();
    let plan = build(&input).unwrap();
    apply(&mut input, &plan);
    for ownership in [
        FileOwnership::Owned {
            owner: input.owner.clone(),
            artifact: Artifact::ProfileIndex,
            sha256: input.existing[&Artifact::Inventory].sha256.clone(),
        },
        FileOwnership::Owned {
            owner: input.owner.clone(),
            artifact: Artifact::Inventory,
            sha256: digest(b"other bytes"),
        },
        FileOwnership::Owned {
            owner: OwnerEvidence {
                owner_sid: "S-1-5-21-9876".into(),
                ..input.owner.clone()
            },
            artifact: Artifact::Inventory,
            sha256: input.existing[&Artifact::Inventory].sha256.clone(),
        },
    ] {
        input
            .existing
            .get_mut(&Artifact::Inventory)
            .unwrap()
            .ownership = ownership;
        assert_eq!(build(&input), Err(Gap::UnknownOwner));
    }
}

#[test]
fn unowned_identical_bytes_stay_references_and_never_acquire_an_owner() {
    let mut input = setup();
    let plan = build(&input).unwrap();
    apply(&mut input, &plan);
    for file in input.existing.values_mut() {
        file.ownership = FileOwnership::ReferenceOnly;
    }
    let noop = build(&input).unwrap();
    assert!(noop.steps.is_empty());
    for output in noop.outputs {
        assert_eq!(output.bytes, input.existing[&output.artifact].bytes);
    }
}

#[test]
fn route_kinds_are_only_explicit_authorized_routes_and_keep_policy_defaults() {
    for kind in [
        RouteKind::Direct {
            probe: bat_fleet_core::inventory::endpoint("100.64.0.2:22", false).unwrap(),
        },
        RouteKind::Cloudflare {
            access_host: "access.example.test".into(),
        },
    ] {
        let mut input = setup();
        input.route.kind = kind;
        let plan = build(&input).unwrap();
        let inv = document(&plan, Artifact::Inventory);
        assert_eq!(inv["hosts"][0]["routes"].as_array().unwrap().len(), 1);
        assert_eq!(inv["hosts"][0]["bat"]["min_version"], "3.2.5");
        assert_eq!(inv["direct_probe_ms"], 600);
        if inv["hosts"][0]["provision_route"] == "cf" {
            assert_eq!(
                inv["cloudflare_access_hosts"],
                json!(["access.example.test"])
            );
        } else {
            assert_eq!(inv["hosts"][0]["probe"], "100.64.0.2:22");
        }
        let profile = &document(&plan, Artifact::ProfileIndex)["profiles"][0];
        assert_eq!(profile["createdAt"], 123);
        assert_eq!(profile["updatedAt"], 456);
        assert_eq!(profile["remoteProfileName"], "Workspace profile");
        assert!(plan
            .outputs
            .iter()
            .all(|out| !String::from_utf8_lossy(&out.bytes).contains("synthetic-bat-token")));
    }
}

fn compatibility_values(inventory: &Value) -> Value {
    let mut values = json!({"fleet_version":inventory["fleet_version"],"direct_probe_ms":inventory["direct_probe_ms"],
        "protocol":inventory["hosts"][0]["bat"]["protocol"],"min_version":inventory["hosts"][0]["bat"]["min_version"]});
    for field in [
        "local",
        "target",
        "dashboard_path",
        "supported_api_versions",
        "min_contract_version",
        "required_features",
    ] {
        values[field] = inventory["connector"][field].clone();
    }
    values
}

#[test]
fn generated_compatibility_follows_the_shipped_policy_without_importing_example_identity() {
    let example: Value = serde_json::from_slice(include_bytes!(
        "../../fleet.example/client/fleet-inventory.json"
    ))
    .unwrap();
    let mut input = setup();
    input.ssh_source = String::from_utf8(input.ssh_source)
        .unwrap()
        .replace("bat-example", "reviewed-example")
        .into_bytes();
    input.forward = Some(
        read_ssh(&input.ssh_source)
            .unwrap()
            .association("reviewed-example")
            .unwrap(),
    );
    let plan = build(&input).unwrap();
    let inventory = document(&plan, Artifact::Inventory);
    assert_eq!(
        compatibility_values(&inventory),
        compatibility_values(&example)
    );
    assert_ne!(inventory["hosts"][0]["name"], example["hosts"][0]["name"]);
    assert_ne!(inventory["credentials"], example["credentials"]);
    assert_ne!(inventory["fingerprints"], example["fingerprints"]);
    assert_eq!(
        inventory["hosts"][0]["routes"][0]["alias"],
        "reviewed-example"
    );
    assert_eq!(inventory["connector"]["ssh_alias"], Value::Null);
    assert_eq!(
        inventory["connector"]["credential_ref"],
        "inactive-connector-observe"
    );
    assert_eq!(inventory["cloudflare_access_hosts"], json!([]));
}

#[test]
fn rebinds_and_occupied_configured_addresses_do_not_generate_steps() {
    let mut input = setup();
    input
        .addresses
        .configured
        .insert("127.0.0.2".parse().unwrap());
    assert_eq!(build(&input), Err(Gap::OccupiedEndpoint));
    let mut input = setup();
    let plan = build(&input).unwrap();
    apply(&mut input, &plan);
    input.root = input.root.join("moved");
    assert_eq!(build(&input), Err(Gap::IdentityChanged));
    let mut input = setup();
    apply(&mut input, &plan);
    input.ssh_source = source("127.0.0.3");
    input.forward = Some(
        read_ssh(&input.ssh_source)
            .unwrap()
            .association("bat-example")
            .unwrap(),
    );
    input.selected = select(
        &profile_index("example-profile", "127.0.0.3"),
        "example-profile",
    )
    .unwrap();
    assert_eq!(build(&input), Err(Gap::IdentityChanged));
}

#[test]
fn existing_routes_connector_metadata_and_compatibility_policy_are_preserved() {
    let mut input = setup();
    let plan = build(&input).unwrap();
    apply(&mut input, &plan);
    let mut inv = document(&plan, Artifact::Inventory);
    inv["fleet_version"] = json!("3.2.6");
    inv["direct_probe_ms"] = json!(700);
    inv["hosts"][0]["direct_probe_ms"] = json!(1500);
    inv["hosts"][0]["allow_tailscale_relay"] = json!(true);
    inv["hosts"][0]["routes"][0]["direct_probe_ms"] = json!(600);
    inv["hosts"][0]["routes"][0]["allow_tailscale_relay"] = json!(false);
    inv["hosts"][0]["bat"]["min_version"] = json!("3.2.6");
    inv["hosts"][0]["probe"] = json!("100.64.0.2:22");
    inv["hosts"][0]["routes"].as_array_mut().unwrap().insert(
        0,
        json!({"kind":"direct","alias":"bat-direct","probe":"100.64.0.2:22"}),
    );
    inv["hosts"][0]["connector_endpoint"] = json!({"endpoint":"192.0.2.20:9000","remote_profile_id":"metadata-profile","credential_ref":input.selected.credential_ref(),"fingerprint_ref":input.selected.fingerprint_ref()});
    replace_existing(
        &mut input,
        Artifact::Inventory,
        serde_json::to_vec(&inv).unwrap(),
    );
    let mut ssh = plan.output(Artifact::SshReference).bytes.clone();
    ssh.extend_from_slice(b"Host bat-direct\n");
    replace_existing(&mut input, Artifact::SshReference, ssh);
    input.label = "Renamed machine".into();
    let changed = build(&input).unwrap();
    let after = document(&changed, Artifact::Inventory);
    assert_eq!(after["hosts"][0]["routes"], inv["hosts"][0]["routes"]);
    assert_eq!(
        after["hosts"][0]["connector_endpoint"],
        inv["hosts"][0]["connector_endpoint"]
    );
    assert_eq!(after["hosts"][0]["bat"], inv["hosts"][0]["bat"]);
    assert_eq!(after["fleet_version"], inv["fleet_version"]);
    assert_eq!(after["direct_probe_ms"], inv["direct_probe_ms"]);
    assert_eq!(
        after["hosts"][0]["direct_probe_ms"],
        inv["hosts"][0]["direct_probe_ms"]
    );
    assert_eq!(
        after["hosts"][0]["allow_tailscale_relay"],
        inv["hosts"][0]["allow_tailscale_relay"]
    );
}

#[test]
fn applied_prefixes_need_only_remaining_files_and_pointer_is_always_last() {
    let original = setup();
    let plan = build(&original).unwrap();
    for count in 0..=4 {
        let mut current = original.clone();
        for output in plan.outputs.iter().take(count) {
            replace_existing(&mut current, output.artifact, output.bytes.clone());
        }
        let remaining = build(&current).unwrap();
        assert_eq!(remaining.outputs, plan.outputs);
        assert_eq!(remaining.steps.len(), 4 - count);
        if count < 4 {
            assert_eq!(
                remaining.steps.last().unwrap().artifact,
                Artifact::Installation
            );
        }
    }
}

#[test]
fn selection_reuses_catalog_admission_and_rejects_ambiguity_or_drift() {
    let index = profile_index("example-profile", "127.0.0.2");
    assert_eq!(
        select(&index, "absent-profile"),
        Err(Gap::ProfileUnavailable)
    );
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
fn catalog_selection_requires_an_exact_source_record_without_panicking() {
    let original = profile_index("example-profile", "127.0.0.2");
    let tokens = serde_json::to_vec(&json!({"enc":false,"data":json!({"tokens":{"example-profile":"synthetic-bat-token"}}).to_string()})).unwrap();
    let catalog = source_catalog::parse(&original, &AdmissionStore::parse(&tokens));
    // A caller-held catalog can outlive a case-only edit to the source index.
    let changed = profile_index("EXAMPLE-PROFILE", "127.0.0.2");
    let result = std::panic::catch_unwind(|| {
        SelectedProfile::from_catalog(&catalog, &changed, "example-profile")
    });
    assert_eq!(
        result.expect("source changes must return typed gaps"),
        Err(Gap::ProfileUnavailable)
    );
    for malformed in [
        b"{}".as_slice(),
        b"{\"profiles\":[null]}",
        b"{\"profiles\":false}",
        b"not json",
    ] {
        let result = std::panic::catch_unwind(|| {
            SelectedProfile::from_catalog(&catalog, malformed, "example-profile")
        });
        assert_eq!(
            result.expect("malformed input must return typed gaps"),
            Err(Gap::ProfileUnavailable)
        );
    }
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
    addresses
        .listening
        .insert("127.0.0.3:19001".parse().unwrap());
    let allocation = allocate_loopback(&proven, &addresses).unwrap();
    assert_eq!(allocation.local.to_string(), "127.0.0.4:19001");
    assert_eq!(allocation, allocate_loopback(&proven, &addresses).unwrap());
    addresses.listening.insert(allocation.local);
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
fn ordinary_unrelated_ssh_blocks_remain_references_while_the_selected_route_builds() {
    let mut input = setup();
    input.ssh_source = b"# ordinary account defaults\nHost *\n ServerAliveInterval 30\n IdentityAgent ~/.ssh/agent.sock\nHost storage storage.example.test\n Port 2222\nHost legacy\n LocalForward 19002 other.example.test:9000\nHost ipv6-only\n LocalForward [::1]:19003 [::1]:9000\nHost sockets\n LocalForward /example/socket /example/target\n".to_vec();
    input.ssh_source.extend_from_slice(&source("127.0.0.2"));
    input.forward = Some(
        read_ssh(&input.ssh_source)
            .unwrap()
            .association("bat-example")
            .unwrap(),
    );
    let plan = build(&input).unwrap();
    assert_eq!(
        document(&plan, Artifact::Inventory)["hosts"][0]["local"],
        "127.0.0.2:19001"
    );
    assert_round_trip(input);
}

#[test]
fn literal_alias_lists_and_repeated_blocks_collect_only_their_forwards() {
    let bytes = b"Host bat-example bat-other # literal names\n IdentityAgent agent.sock\nHost bat-other\n LocalForward 127.0.0.2:19001 127.0.0.1:9000\n";
    let parsed = read_ssh(bytes).unwrap();
    assert_eq!(parsed.association("bat-example"), Err(Gap::MissingMapping));
    assert_eq!(
        parsed.association("bat-other").unwrap().local().to_string(),
        "127.0.0.2:19001"
    );
    let shared = b"Host bat-example bat-other\n LocalForward 127.0.0.2:19001 127.0.0.1:9000\n";
    let parsed = read_ssh(shared).unwrap();
    assert_eq!(
        parsed.association("bat-example").unwrap().local(),
        parsed.association("bat-other").unwrap().local()
    );
    let duplicate = [
        shared.as_slice(),
        b"Host BAT-EXAMPLE\n LocalForward 127.0.0.2:19001 127.0.0.1:9000\n",
    ]
    .concat();
    assert_eq!(
        read_ssh(&duplicate).unwrap().association("bat-example"),
        Err(Gap::DuplicateForwards)
    );
}

#[test]
fn malformed_host_separators_cannot_become_literal_associations() {
    for host in ["Host==bat-example", "Host = =bat-example"] {
        let bytes = format!("{host}\n LocalForward 127.0.0.2:19001 127.0.0.1:9000\n");
        assert_eq!(
            read_ssh(bytes.as_bytes())
                .unwrap()
                .association("bat-example"),
            Err(Gap::ForwardCompletenessUnproven)
        );
    }
    for host in ["Host=bat-example", "Host = bat-example"] {
        let bytes = format!("{host}\n LocalForward=127.0.0.2:19001 127.0.0.1:9000\n");
        assert!(read_ssh(bytes.as_bytes())
            .unwrap()
            .association("bat-example")
            .is_ok());
    }
}

#[test]
fn inline_hash_is_not_a_host_name_prefix_or_forward_comment() {
    assert_eq!(
        read_ssh(b"Host bat-example#x\n")
            .unwrap()
            .association("bat-example"),
        Err(Gap::MissingMapping)
    );
    let valid = b"# comment\nHost bat-example # comment\n LocalForward 127.0.0.2:19001 127.0.0.1:9000\t# comment\n";
    assert!(read_ssh(valid).unwrap().association("bat-example").is_ok());
    let invalid = b"Host bat-example\n LocalForward 127.0.0.2:19001 127.0.0.1:9000#x\n";
    assert_eq!(
        read_ssh(invalid).unwrap().association("bat-example"),
        Err(Gap::UnsupportedSshSyntax)
    );
}

#[test]
fn global_forward_and_topology_directives_still_refuse_the_file() {
    for (bytes, gap) in [
        (
            b"LocalForward 127.0.0.2:19001 127.0.0.1:9000\n".as_slice(),
            Gap::UnsupportedSshSyntax,
        ),
        (
            b"Host unrelated\n Include config\n",
            Gap::UnsupportedInclude,
        ),
        (
            b"Host unrelated\n Match exec never-execute\n",
            Gap::UnsupportedMatch,
        ),
    ] {
        assert_eq!(read_ssh(bytes), Err(gap));
    }
}

#[test]
fn forwards_in_unevaluated_host_patterns_make_every_association_unproven() {
    for host in [
        "*",
        "*.example.test",
        "!bat-example",
        "\"bat-example\"",
        "bat-example#x",
        "literal *",
    ] {
        for forward in ["127.0.0.2:19001 127.0.0.1:9000", "19001 example.test:9000"] {
            let bytes = [
                format!("Host {host}\n LocalForward {forward}\n").into_bytes(),
                source("127.0.0.2"),
            ]
            .concat();
            let parsed = read_ssh(&bytes).unwrap();
            for alias in ["bat-example", "absent"] {
                assert_eq!(
                    parsed.association(alias),
                    Err(Gap::ForwardCompletenessUnproven)
                );
            }
            let mut input = setup();
            input.ssh_source = bytes;
            assert_eq!(build(&input), Err(Gap::ForwardCompletenessUnproven));
        }
    }
}

#[test]
fn unsupported_selected_forwards_are_gaps_without_rejecting_unrelated_aliases() {
    for forward in [
        "19001 example.test:9000",
        "[::1]:19001 [::1]:9000",
        "/example/socket /example/target",
        "${PORT} %h:9000",
    ] {
        let bytes = [
            source("127.0.0.2"),
            format!("Host other\n LocalForward {forward}\n").into_bytes(),
        ]
        .concat();
        let parsed = read_ssh(&bytes).unwrap();
        assert!(parsed.association("bat-example").is_ok());
        assert_eq!(parsed.association("other"), Err(Gap::UnsupportedSshSyntax));
        let repeated = [
            bytes,
            b"Host other\n LocalForward 127.0.0.3:19001 127.0.0.1:9000\n".to_vec(),
        ]
        .concat();
        assert_eq!(
            read_ssh(&repeated).unwrap().association("other"),
            Err(Gap::UnsupportedSshSyntax)
        );
    }
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
    ] {
        assert_eq!(read_ssh(&bytes), Err(gap));
    }
}
