//! Real catalog, selection and frozen Fleet producers; only native OS evidence is synthetic.
use bat_fleet_core::{
    connection_catalog::{
        self as catalog, Change, ConfigurationRef, Entry, ReadOutcome, State, Transition,
    },
    connection_contract::reference::Sha256,
    connection_participant::Scope,
    connection_publish::FrozenHistory,
    connection_reference::*,
    digest,
    selection::{launch_plan, Choices, Preferences},
    selection_io::Store,
};
use std::{fs, path::Path};
#[allow(dead_code)]
#[path = "support/creation_evidence.rs"]
mod creation;
mod support;

const INSTALLATION: &str = "00000000-0000-4000-8000-000000000001";
struct CapturedPublications<'a>(&'a [&'a FrozenHistory]);
impl AssociationResolver for CapturedPublications<'_> {
    fn resolve(
        &self,
        catalog: &catalog::Catalog,
        configuration: &bat_fleet_core::configuration::Configuration,
    ) -> bat_fleet_core::Result<AssociationCoverage> {
        AssociationCoverage::unproven(catalog, configuration, self.0)
    }
}
fn entry(n: usize) -> Entry {
    Entry {
        id: format!("entry_{n:032x}"),
        server_id: "synthetic-server".into(),
        principal_id: "a".repeat(64),
        configuration: ConfigurationRef {
            id: format!("config_{n:032x}"),
            sha256: digest(b"synthetic central configuration"),
        },
        connection_id: Some(format!("conn_{n:032x}")),
        state: State::Attached,
    }
}
fn change(raw: Option<&[u8]>, change: Change) -> Vec<u8> {
    match catalog::transition(&catalog::load(raw), change).unwrap() {
        Transition::Changed { bytes, .. } => bytes,
        Transition::Unchanged => raw.unwrap().to_vec(),
    }
}
fn populated() -> Vec<u8> {
    let mut raw = change(
        None,
        Change::Create {
            installation_ref: INSTALLATION.into(),
        },
    );
    for n in 1..=2 {
        raw = change(Some(&raw), Change::Add(entry(n)));
    }
    change(Some(&raw), Change::Select(Some(entry(1).id)))
}
fn loaded(raw: &[u8]) -> catalog::Catalog {
    let ReadOutcome::Present { catalog, .. } = catalog::load(Some(raw)) else {
        panic!("producer fixture")
    };
    catalog
}
fn binding() -> ReferenceBinding {
    ReferenceBinding {
        scope: Scope {
            server_id: entry(1).server_id,
            principal_id: entry(1).principal_id,
            native_installation: INSTALLATION.into(),
            installation_ref: INSTALLATION.into(),
        },
        connection_id: entry(1).connection_id.unwrap(),
        entry_id: entry(1).id,
        configuration: entry(1).configuration,
        expected_revision: 1,
        source_digest: Sha256::new(&digest(b"verified server source descriptor")).unwrap(),
    }
}
fn review(
    f: &support::Fixture,
    raw: &[u8],
    binding: &ReferenceBinding,
    records: &[&FrozenHistory],
    identities: SlotIdentities<'_>,
) -> DetachReview {
    let configuration = f.load();
    let observation = Store::new(f.0.clone()).observe(&configuration).unwrap();
    let snapshot = observation.snapshot(&configuration).unwrap();
    let coverage = CapturedPublications(records)
        .resolve(&loaded(raw), &configuration)
        .unwrap();
    review_detach(DetachInput {
        reference: binding,
        catalog: Some(raw),
        configuration: &configuration,
        observation: &observation,
        snapshot: &snapshot,
        associations: &coverage,
        slot_identities: identities,
    })
    .unwrap()
}
fn tree(path: &Path) -> Vec<(std::path::PathBuf, Option<Vec<u8>>)> {
    let mut out = vec![(path.into(), path.is_file().then(|| fs::read(path).unwrap()))];
    if path.is_dir() {
        for e in fs::read_dir(path).unwrap() {
            out.extend(tree(&e.unwrap().path()));
        }
    }
    out.sort();
    out
}

#[test]
fn catalog_fidelity_and_raw_noop_without_any_authority_or_writes() {
    let f = support::Fixture::new();
    let raw = populated();
    let before = tree(&f.0);
    let review = review(&f, &raw, &binding(), &[], SlotIdentities::default());
    assert_eq!(
        review.catalog_after(),
        change(Some(&raw), Change::Detach(entry(1).id))
    );
    let after = loaded(review.catalog_after());
    assert_eq!(after.revision, loaded(&raw).revision + 1);
    assert_eq!(after.selected, None);
    assert_eq!(after.entries.len(), 2);
    assert_eq!(after.entries[0].configuration, entry(1).configuration);
    assert_eq!(after.entries[1], entry(2));
    assert!(review
        .requirements()
        .iter()
        .any(|r| r.code == "LOCAL_SOURCE_ASSOCIATION_UNPROVEN"));
    assert_eq!(
        review
            .requirements()
            .iter()
            .filter(|r| r.code == "LOCAL_OWNERSHIP_UNPROVEN")
            .count(),
        2
    );
    assert!(review
        .requirements()
        .iter()
        .any(|r| r.code == "LOCAL_GATE_UNPROVEN"));
    assert_eq!(review.selection_after(), SelectionCandidate::Unproven);
    assert_eq!(tree(&f.0), before);
    let detached = serde_json::to_vec_pretty(&after).unwrap();
    let noop = self::review(&f, &detached, &binding(), &[], SlotIdentities::default());
    assert_eq!(noop.catalog_after(), detached);
    let mut unselected = binding();
    unselected.connection_id = entry(2).connection_id.unwrap();
    unselected.entry_id = entry(2).id;
    unselected.configuration = entry(2).configuration;
    assert_eq!(
        loaded(self::review(&f, &raw, &unselected, &[], SlotIdentities::default()).catalog_after())
            .selected,
        Some(entry(1).id)
    );
}

#[test]
fn missing_mapping_keeps_last_reference_and_implied_choices_unproven() {
    let f = support::Fixture::new();
    let configuration = f.load();
    let directory = f.0.join("BetterAgentTerminal");
    fs::create_dir(&directory).unwrap();
    let raw = change(
        Some(&change(
            None,
            Change::Create {
                installation_ref: INSTALLATION.into(),
            },
        )),
        Change::Add(entry(1)),
    );
    for choices in [
        Choices {
            connections: vec![],
            profiles: vec!["profile-2".into()],
            dashboard: false,
        },
        Choices {
            connections: vec!["node-3".into()],
            profiles: vec!["default".into()],
            dashboard: false,
        },
    ] {
        let prefs = Preferences::choose(&configuration.inventory, &choices).unwrap();
        let bytes = prefs.persisted(&configuration.inventory).unwrap();
        fs::write(directory.join("fleet-client.json"), &bytes).unwrap();
        let before = tree(&f.0);
        let reviewed = review(&f, &raw, &binding(), &[], SlotIdentities::default());
        assert_eq!(
            reviewed.launch_before(),
            &launch_plan(&configuration.inventory, &prefs)
        );
        assert_eq!(reviewed.selection_after(), SelectionCandidate::Unproven);
        assert_eq!(reviewed.saved_before(), &prefs);
        assert_eq!(tree(&f.0), before);
    }
}

#[test]
fn full_commitment_covers_legacy_scope_catalog_and_slot_identity() {
    let f = support::Fixture::new();
    let raw = populated();
    let baseline = review(&f, &raw, &binding(), &[], SlotIdentities::default());
    let same = review(&f, &raw, &binding(), &[], SlotIdentities::default());
    assert_eq!(baseline.digest(), same.digest());
    let directory = f.0.join("BetterAgentTerminal");
    fs::create_dir(&directory).unwrap();
    fs::write(directory.join("open-bat-selection.json"), b"{}").unwrap();
    assert_ne!(
        baseline.digest(),
        review(&f, &raw, &binding(), &[], SlotIdentities::default()).digest()
    );
    let legacy_before = review(&f, &raw, &binding(), &[], SlotIdentities::default());
    fs::write(directory.join("open-bat-selection.json"), b"{ }").unwrap();
    assert_ne!(
        legacy_before.digest(),
        review(&f, &raw, &binding(), &[], SlotIdentities::default()).digest()
    );
    let mut other = binding();
    other.expected_revision += 1;
    assert_ne!(
        review(&f, &raw, &binding(), &[], SlotIdentities::default()).digest(),
        review(&f, &raw, &other, &[], SlotIdentities::default()).digest()
    );
    let raw2 = change(Some(&raw), Change::Add(entry(3)));
    assert_ne!(
        review(&f, &raw, &binding(), &[], SlotIdentities::default()).digest(),
        review(&f, &raw2, &binding(), &[], SlotIdentities::default()).digest()
    );
    let identity = Sha256::new(&digest(b"observed slot identity, not authority")).unwrap();
    let with_identity = review(
        &f,
        &raw,
        &binding(),
        &[],
        SlotIdentities {
            catalog: Some(&identity),
            selection: None,
        },
    );
    assert_ne!(
        with_identity.digest(),
        review(&f, &raw, &binding(), &[], SlotIdentities::default()).digest()
    );
    assert_eq!(with_identity.requirements(), baseline.requirements());
    let before = review(&f, &raw, &binding(), &[], SlotIdentities::default());
    let ssh = f.0.join("kit/ssh-config");
    let mut bytes = fs::read(&ssh).unwrap();
    bytes.extend(b"\n# synthetic observation generation\n");
    fs::write(&ssh, bytes).unwrap();
    assert_ne!(
        before.digest(),
        review(&f, &raw, &binding(), &[], SlotIdentities::default()).digest()
    );
}

#[test]
fn real_frozen_publication_does_not_supply_an_original_connection_association() {
    let f = support::Fixture::new();
    let mut source = creation::Fixture::new();
    let first = source.plan();
    let first_ref = source
        .native
        .publisher()
        .export_plan(&first, &mut source.native.clone())
        .unwrap();
    let first = FrozenHistory::decode(
        &first_ref,
        &fs::read(source.native.frozen_path(&first_ref)).unwrap(),
    )
    .unwrap();
    source.input.label.push_str(" changed");
    let second = source.plan();
    let second_ref = source
        .native
        .publisher()
        .export_plan(&second, &mut source.native.clone())
        .unwrap();
    let second = FrozenHistory::decode(
        &second_ref,
        &fs::read(source.native.frozen_path(&second_ref)).unwrap(),
    )
    .unwrap();
    let raw = populated();
    let config = f.load();
    assert!(AssociationCoverage::unproven(&loaded(&raw), &config, &[&first, &first]).is_err());
    assert!(AssociationCoverage::unproven(
        &loaded(&raw),
        &config,
        &vec![&first; catalog::ENTRY_BOUND + 1]
    )
    .is_err());
    let one = review(&f, &raw, &binding(), &[&first], SlotIdentities::default());
    let two = review(
        &f,
        &raw,
        &binding(),
        &[&first, &second],
        SlotIdentities::default(),
    );
    let reordered = review(
        &f,
        &raw,
        &binding(),
        &[&second, &first],
        SlotIdentities::default(),
    );
    assert_ne!(one.digest(), two.digest());
    assert_eq!(two.digest(), reordered.digest());
    assert_eq!(one.selection_after(), SelectionCandidate::Unproven);
    assert!(one
        .requirements()
        .iter()
        .any(|r| r.code == "LOCAL_SOURCE_ASSOCIATION_UNPROVEN"));
}

#[test]
fn invalid_foreign_stale_and_missing_inputs_refuse_without_fallback() {
    let f = support::Fixture::new();
    let config = f.load();
    let raw = populated();
    let observation = Store::new(f.0.clone()).observe(&config).unwrap();
    let snapshot = observation.snapshot(&config).unwrap();
    let coverage = AssociationCoverage::unproven(&loaded(&raw), &config, &[]).unwrap();
    let check = |catalog: Option<&[u8]>,
                 reference: &ReferenceBinding,
                 associations: &AssociationCoverage| {
        review_detach(DetachInput {
            reference,
            catalog,
            configuration: &config,
            observation: &observation,
            snapshot: &snapshot,
            associations,
            slot_identities: SlotIdentities::default(),
        })
    };
    assert!(matches!(
        check(None, &binding(), &coverage),
        Err("LOCAL_PLAN_UNAVAILABLE")
    ));
    assert!(matches!(
        check(Some(b"{}"), &binding(), &coverage),
        Err("LOCAL_INVALID_STATE")
    ));
    assert!(check(
        Some(&vec![b' '; catalog::DOCUMENT_BOUND + 1]),
        &binding(),
        &coverage
    )
    .is_err());
    let mut future = loaded(&raw);
    future.format_version += 1;
    assert!(matches!(
        check(
            Some(&serde_json::to_vec(&future).unwrap()),
            &binding(),
            &coverage
        ),
        Err("LOCAL_GENERATOR_UNSUPPORTED")
    ));
    for field in ["principal", "installation", "connection", "configuration"] {
        let mut other = binding();
        match field {
            "principal" => other.scope.principal_id = "b".repeat(64),
            "installation" => {
                other.scope.native_installation = "00000000-0000-4000-8000-000000000002".into()
            }
            "connection" => other.connection_id = entry(2).connection_id.unwrap(),
            _ => other.configuration = entry(2).configuration,
        }
        assert!(matches!(
            check(Some(&raw), &other, &coverage),
            Err("LOCAL_SOURCE_ASSOCIATION_UNPROVEN")
        ));
    }
    let changed = change(Some(&raw), Change::Add(entry(3)));
    assert!(matches!(
        check(Some(&changed), &binding(), &coverage),
        Err("LOCAL_REVIEW_CHANGED")
    ));
    let mut exhausted = loaded(&raw);
    exhausted.revision = u64::MAX;
    let exhausted_raw = catalog::serialize(&exhausted).unwrap();
    let exhausted_coverage = AssociationCoverage::unproven(&exhausted, &config, &[]).unwrap();
    assert!(matches!(
        check(Some(&exhausted_raw), &binding(), &exhausted_coverage),
        Err("LOCAL_INVALID_STATE")
    ));
    let original = check(Some(&raw), &binding(), &coverage).unwrap();
    fs::write(f.0.join("kit/ssh-config"), b"changed after capture").unwrap();
    let before = tree(&f.0);
    assert_eq!(
        check(Some(&raw), &binding(), &coverage).unwrap().digest(),
        original.digest()
    );
    assert_eq!(tree(&f.0), before); // Pure review uses captured inputs; executor must recheck freshness.
}
