use super::*;

fn binding(f: &Fixture) -> Binding {
    f.native
        .receipt
        .binding(
            parse_owner_reference(&f.native.owner).unwrap(),
            f.native.session.get(),
        )
        .unwrap()
}
fn managed(f: &Fixture, id: char) -> Record {
    let id = id.to_string().repeat(32);
    Record::Managed(ManagedBinding {
        binding: binding(f),
        managed: ManagedIdentity {
            actor: format!("desktop-{id}"),
            installation_id: id,
            manifest_sha256: digest(b"verified manifest"),
            server_id: "server".into(),
            principal_id: "principal".into(),
        },
    })
}
#[test]
fn capture_refuses_well_formed_reference_with_wrong_receipt_hash() {
    let mut f = Fixture::new();
    let _guards = f.native.guards();
    let mut owner = f.native.owner.clone();
    owner.creation_receipt =
        format_reference(&owner.installation_id, &digest(b"another receipt")).unwrap();
    assert!(parse_owner_reference(&owner).is_ok());
    assert!(capture(&f.native, &owner).is_err());
}
#[test]
fn capture_refuses_a_different_current_account_sid() {
    let mut f = Fixture::new();
    *f.native.login_sid.borrow_mut() = "S-1-5-21-123-456-789-1002".into();
    let _guards = f.native.guards();
    assert!(capture(&f.native, &f.native.owner).is_err());
}
#[test]
fn resolution_does_not_answer_for_a_foreign_owner() {
    let mut f = Fixture::new();
    let resolution = f.native.resolution().unwrap();
    let mut foreign = f.native.owner.clone();
    foreign.creation_receipt =
        format_reference(&foreign.installation_id, &digest(b"foreign receipt")).unwrap();
    assert_eq!(
        resolution.ownership(
            &foreign,
            Artifact::Inventory,
            &f.path(Artifact::Inventory),
            None
        ),
        FileOwnership::Unknown
    );
}
#[test]
fn capture_refuses_auxiliary_binding_from_another_receipt() {
    let mut f = Fixture::new();
    let mut record = managed(&f, 'c');
    if let Record::Managed(r) = &mut record {
        r.binding.provisioning_sha256 = digest(b"other root");
    }
    raw_record(&f, &record);
    let _guards = f.native.guards();
    assert!(capture(&f.native, &f.native.owner).is_err());
}
#[test]
fn capture_requires_native_record_attestation() {
    let mut f = Fixture::new();
    let record = managed(&f, 'c');
    f.native.append(&record).unwrap();
    *f.native.refused_record.borrow_mut() = Some(record.key());
    let _guards = f.native.guards();
    assert!(capture(&f.native, &f.native.owner).is_err());
}
#[test]
fn append_requires_native_attestation_before_any_effect() {
    let mut f = Fixture::new();
    let record = managed(&f, 'c');
    *f.native.refused_record.borrow_mut() = Some(record.key());
    let _guards = f.native.guards();
    let mut calls = 0;
    assert!(append(
        &mut f.native,
        &f.input.owner,
        &record,
        &[],
        &[],
        &mut || {
            calls += 1;
            Ok(())
        }
    )
    .is_err());
    assert_eq!(calls, 0);
    assert!(!f.native.evidence_path(&record.key()).exists());
}
#[test]
fn capture_compares_record_bytes_across_its_two_reads() {
    let mut f = Fixture::new();
    let record = managed(&f, 'c');
    f.native.append(&record).unwrap();
    let mut changed = record.clone();
    if let Record::Managed(r) = &mut changed {
        r.managed.manifest_sha256 = digest(b"changed manifest");
    }
    *f.native.record_drift.borrow_mut() = Some((record.key(), changed.encode().unwrap()));
    let _guards = f.native.guards();
    assert!(capture(&f.native, &f.native.owner).is_err());
}
#[test]
fn capture_compares_slots_across_its_two_reads() {
    let mut f = Fixture::new();
    f.native.observations_seen.set(0);
    f.native.slot_drift_after.set(Some(4));
    let _guards = f.native.guards();
    assert!(capture(&f.native, &f.native.owner).is_err());
}
#[test]
fn append_fence_refuses_new_record_before_final_create() {
    let mut f = Fixture::new();
    let plan = f.plan();
    let record = Record::Publication(f.prepare(&plan));
    let injected = managed(&f, 'c');
    let path = f.native.evidence_path(&injected.key());
    let _guards = f.native.guards();
    let histories = match &record {
        Record::Publication(link) => vec![f.native.history(&link.frozen)],
        _ => unreachable!(),
    };
    let result = append(
        &mut f.native,
        &f.input.owner,
        &record,
        &histories,
        &[],
        &mut || {
            std::fs::write(&path, injected.encode().unwrap()).unwrap();
            Ok(())
        },
    );
    assert!(result.is_err());
    assert!(!f.native.evidence_path(&record.key()).exists());
}
#[test]
fn reservation_retry_revalidates_exact_durable_receipt() {
    let mut f = Fixture::new();
    let mut changed = f.native.receipt.clone();
    changed.creator.reservation_id = "d".repeat(32);
    let _guards = f.native.guards();
    assert!(f.native.verify_context(&changed).is_ok());
    assert!(reserve(&mut f.native, &changed, &mut || panic!(
        "no effect on mismatched retry"
    ))
    .is_err());
}
#[test]
fn reservation_residue_is_refused_before_authorization() {
    let mut f = Fixture::unreserved();
    raw_record(&f, &managed(&f, 'c'));
    let receipt = f.native.receipt.clone();
    let _guards = f.native.guards();
    assert_eq!(f.native.entries().unwrap(), vec![EvidenceKey::Managed]);
    let mut calls = 0;
    assert!(reserve(&mut f.native, &receipt, &mut || {
        calls += 1;
        Ok(())
    })
    .is_err());
    assert_eq!(calls, 0);
}
#[test]
fn reservation_occupied_slot_is_refused_before_authorization() {
    let mut f = Fixture::unreserved();
    std::fs::write(f.path(Artifact::Inventory), b"external").unwrap();
    let receipt = f.native.receipt.clone();
    let _guards = f.native.guards();
    let mut calls = 0;
    assert!(reserve(&mut f.native, &receipt, &mut || {
        calls += 1;
        Ok(())
    })
    .is_err());
    assert_eq!(calls, 0);
}
#[test]
fn append_new_admission_requires_its_current_session() {
    let mut f = Fixture::new();
    let plan = f.plan();
    let link = f.prepare(&plan);
    f.native.session.set(8);
    assert!(f.native.append(&Record::Publication(link.clone())).is_err());
    assert!(!f
        .native
        .evidence_path(&Record::Publication(link).key())
        .exists());
}
#[test]
fn managed_binding_cannot_replace_receipt_identity() {
    let mut f = Fixture::unreserved();
    if let Record::Managed(m) = managed(&f, 'c') {
        f.native.receipt.managed_connector = Some(m.managed);
    }
    let bytes = Record::Provisioning(Box::new(f.native.receipt.clone()))
        .encode()
        .unwrap();
    f.native.owner = f.native.receipt.owner(&bytes).unwrap();
    f.input.owner = f.native.owner.clone();
    f.reserve().unwrap();
    let wrong = managed(&f, 'd');
    assert!(f.native.append(&wrong).is_err());
}
#[test]
fn lineage_seals_the_exact_predecessor_terminal() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    f.refresh();
    f.input.label = "Next review".into();
    let next = f.plan();
    let mut link = f.prepare(&next);
    link.predecessor_terminal.as_mut().unwrap().journal_sha256 = Some(digest(b"wrong journal"));
    raw_record(&f, &Record::Publication(link));
    assert!(f.native.resolution().is_err());
}
#[test]
fn earlier_reached_step_cannot_remain_intended() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    let journal = f.root.join(format!(
        "native/fleet-publications/{}.json",
        plan.review_digest
    ));
    let mut value: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&journal).unwrap()).unwrap();
    value["receipts"][0]["state"] = "intended".into();
    std::fs::write(journal, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(f.native.resolution().is_err());
}
#[test]
fn decision_taint_survives_reappearance_of_previously_owned_bytes() {
    for unreadable in [false, true] {
        let mut f = Fixture::new();
        let plan = f.plan();
        f.admit(&plan);
        interrupt(&mut f, &plan, Boundary::AfterReplace, Artifact::Inventory);
        let path = f.path(Artifact::Inventory);
        let reached = std::fs::read(&path).unwrap();
        if unreadable {
            std::fs::remove_file(&path).unwrap();
            std::fs::create_dir(&path).unwrap();
        } else {
            std::fs::write(&path, b"third bytes").unwrap();
        }
        abandon(&mut f, &plan);
        if unreadable {
            std::fs::remove_dir(&path).unwrap();
        }
        std::fs::write(&path, reached).unwrap();
        assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
        assert!(f.native.resolution().unwrap().baseline().is_err());
    }
}
#[test]
fn journaled_review_cannot_be_admitted_again() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    interrupt(&mut f, &plan, Boundary::AfterIntended, Artifact::Inventory);
    abandon(&mut f, &plan);
    let replay = f.prepare(&plan);
    assert!(replay.baseline.iter().all(|slot| slot.sha256.is_none()));
    assert!(f.native.append(&Record::Publication(replay)).is_err());
}
#[test]
fn step_before_digest_must_equal_rooted_baseline() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    f.refresh();
    f.input.label = "Changed".into();
    let file = f.input.existing.get_mut(&Artifact::Inventory).unwrap();
    file.bytes.push(b' ');
    file.sha256 = digest(&file.bytes);
    file.ownership = FileOwnership::Owned {
        owner: f.input.owner.clone(),
        artifact: Artifact::Inventory,
        sha256: file.sha256.clone(),
    };
    let next = f.plan();
    let link = f.prepare(&next);
    assert!(f.native.append(&Record::Publication(link)).is_err());
}
#[test]
fn unchanged_artifact_output_must_equal_rooted_baseline() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    f.refresh();
    let old = f.input.clone();
    f.input.label = "Changed".into();
    let changed = f.plan();
    f.admit(&changed);
    f.publish(&changed);
    f.input = old;
    let noop = f.plan();
    assert!(noop.steps.is_empty());
    let link = f.prepare(&noop);
    assert!(f.native.append(&Record::Publication(link)).is_err());
}
#[test]
fn completion_is_bound_to_exact_saved_bytes() {
    let mut case = support::MigrationCase::new();
    let mut completion = case.complete();
    completion.saved_sha256 = digest(b"other Saved bytes");
    assert!(case
        .fixture
        .native
        .append(&Record::MigrationCompletion(completion))
        .is_err());
}
#[test]
fn completion_requires_complete_saved_phase() {
    use bat_fleet_core::migration;
    let mut case = support::MigrationCase::new();
    case.begin();
    assert_eq!(
        case.store
            .advance(&mut case.producer, &case.intent.migration_id),
        Ok(migration::Progress::WaitingLaunch)
    );
    let completion = MigrationCompletion {
        binding: case.intent.binding.clone(),
        intent_sha256: case.intent_hash.clone(),
        saved_sha256: case
            .store
            .read_history(&case.intent.migration_id)
            .unwrap()
            .sha256()
            .into(),
        after_sha256: digest(&case.after),
    };
    assert!(case
        .fixture
        .native
        .append(&Record::MigrationCompletion(completion))
        .is_err());
}
#[test]
fn completion_keeps_the_intents_historical_binding() {
    let mut case = support::MigrationCase::new();
    let mut completion = case.complete();
    case.fixture.native.session.set(8);
    completion.binding.session_id = 8;
    assert!(case
        .fixture
        .native
        .append(&Record::MigrationCompletion(completion))
        .is_err());
}

#[test]
fn migration_ids_are_unique_in_append_and_replay() {
    use bat_fleet_core::discovery::Backend;
    let mut case = support::MigrationCase::new();
    let completion = case.complete();
    case.fixture
        .native
        .append(&Record::MigrationCompletion(completion))
        .unwrap();
    *case.fixture.native.running.borrow_mut() = None;
    let resolution = case.fixture.native.resolution().unwrap();
    let preview = case
        .store
        .preview(&mut case.producer, Backend::Powershell)
        .unwrap();
    let mut duplicate = case.intent.clone();
    duplicate.predecessor_sha256 = resolution.head().into();
    duplicate.predecessor_terminal = resolution.terminal().unwrap().clone();
    duplicate.before_sha256 = digest(&case.after);
    duplicate.review_fingerprint = preview.fingerprint().into();
    assert!(case
        .fixture
        .native
        .append(&Record::MigrationIntent(duplicate.clone()))
        .is_err());
    raw_record(&case.fixture, &Record::MigrationIntent(duplicate));
    assert!(case.fixture.native.resolution().is_err());
}
#[test]
fn duplicate_migration_views_are_rejected_even_before_an_intent() {
    let mut f = Fixture::new();
    let store = bat_fleet_core::migration::Store::new(
        &f.path(Artifact::Installation),
        &f.root.join("Startup"),
    )
    .unwrap();
    let id = "9".repeat(32);
    let _guards = f.native.guards();
    assert!(capture(&f.native, &f.input.owner)
        .unwrap()
        .resolve(
            &[],
            &[
                store.observe_history(&id).unwrap(),
                store.observe_history(&id).unwrap()
            ]
        )
        .is_err());
}
#[test]
fn restore_completion_requires_the_exact_reversed_source_pair() {
    use base64::Engine;
    use bat_fleet_core::{discovery::Backend, migration};
    let mut case = support::MigrationCase::new();
    let first = case.complete();
    case.fixture
        .native
        .append(&Record::MigrationCompletion(first))
        .unwrap();
    *case.fixture.native.running.borrow_mut() = None;
    let current = case.fixture.native.resolution().unwrap();
    let preview = case
        .store
        .preview(&mut case.producer, Backend::Powershell)
        .unwrap();
    let intent = MigrationIntent {
        binding: case.intent.binding.clone(),
        predecessor_sha256: current.head().into(),
        predecessor_terminal: current.terminal().unwrap().clone(),
        migration_id: "8".repeat(32),
        startup_path: case.intent.startup_path.clone(),
        review_fingerprint: preview.fingerprint().into(),
        before_sha256: digest(&case.after),
        restores: Some(case.intent.migration_id.clone()),
        acceptance_reference: "native-restore-review".into(),
    };
    let intent_hash = case
        .fixture
        .native
        .append(&Record::MigrationIntent(intent.clone()))
        .unwrap();
    case.store
        .restore(
            &mut case.producer,
            &case.intent.migration_id,
            &intent.migration_id,
        )
        .unwrap();
    assert_eq!(
        case.store.advance(&mut case.producer, &intent.migration_id),
        Ok(migration::Progress::WaitingLaunch)
    );
    assert_eq!(
        case.store.advance(&mut case.producer, &intent.migration_id),
        Ok(migration::Progress::Complete)
    );
    let saved = case.store.read_history(&intent.migration_id).unwrap();
    let valid_after = saved.config(true).unwrap();
    let saved_path = case.fixture.root.join(format!(
        "native/fleet-migrations/{}.json",
        intent.migration_id
    ));
    let original = std::fs::read(&saved_path).unwrap();
    let mut forged: serde_json::Value = serde_json::from_slice(&original).unwrap();
    let mut altered = valid_after.clone();
    altered.push(b' ');
    forged["next_config"] = base64::engine::general_purpose::STANDARD
        .encode(&altered)
        .into();
    std::fs::write(&saved_path, serde_json::to_vec(&forged).unwrap()).unwrap();
    std::fs::write(case.fixture.path(Artifact::Installation), &altered).unwrap();
    let completion = MigrationCompletion {
        binding: intent.binding.clone(),
        intent_sha256: intent_hash,
        saved_sha256: case
            .store
            .read_history(&intent.migration_id)
            .unwrap()
            .sha256()
            .into(),
        after_sha256: digest(&altered),
    };
    assert!(case
        .fixture
        .native
        .append(&Record::MigrationCompletion(completion.clone()))
        .is_err());
    std::fs::write(saved_path, original).unwrap();
    std::fs::write(case.fixture.path(Artifact::Installation), &valid_after).unwrap();
    case.fixture
        .native
        .append(&Record::MigrationCompletion(MigrationCompletion {
            saved_sha256: saved.sha256().into(),
            after_sha256: digest(&valid_after),
            ..completion
        }))
        .unwrap();
    assert!(owned(case.fixture.ownership(Artifact::Installation)));
}
#[test]
fn zero_step_plan_owner_must_match_the_rooted_creation() {
    use bat_fleet_core::{connection_publish::FrozenHistory, connection_setup};
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    f.refresh();
    let current = f.native.resolution().unwrap();
    let mut input = f.input.clone();
    input.owner.installation_id = "f".repeat(32);
    input.owner.creation_receipt =
        format_reference(&input.owner.installation_id, &digest(b"foreign creation")).unwrap();
    for (artifact, existing) in &mut input.existing {
        existing.ownership = FileOwnership::Owned {
            owner: input.owner.clone(),
            artifact: *artifact,
            sha256: existing.sha256.clone(),
        };
    }
    let foreign = connection_setup::build(&input).unwrap();
    assert!(foreign.steps.is_empty());
    let mut foreign_native = f.native.clone();
    foreign_native.owner = input.owner;
    let reference = foreign_native
        .publisher()
        .export_plan(&foreign, &mut foreign_native)
        .unwrap();
    let link = PublicationLink {
        binding: binding(&f),
        predecessor_sha256: current.head().into(),
        predecessor_terminal: current.terminal().cloned(),
        frozen: reference.clone(),
        generator_version: foreign.generator_version,
        baseline: current.baseline().unwrap(),
        acceptance_reference: "foreign-native-review".into(),
    };
    let _guards = f.native.guards();
    let mut histories = f.native.histories();
    let bytes = std::fs::read(f.native.frozen_path(&reference)).unwrap();
    histories.push(
        f.native
            .publisher()
            .read_evidence_history(
                FrozenHistory::decode(&reference, &bytes).unwrap(),
                &foreign_native,
            )
            .unwrap(),
    );
    assert!(append(
        &mut f.native,
        &f.input.owner,
        &Record::Publication(link),
        &histories,
        &[],
        &mut || Ok(())
    )
    .is_err());
}
#[test]
fn historical_reader_does_not_weaken_publication_effect_gates() {
    use bat_fleet_core::connection_publish::{Error, FrozenHistory};
    let mut case = support::MigrationCase::new();
    let completion = case.complete();
    let _guards = case.fixture.native.evidence_guard();
    let histories = case.fixture.native.histories();
    let reference = histories[0].frozen().reference();
    let bytes = std::fs::read(case.fixture.native.frozen_path(reference)).unwrap();
    let result = case.fixture.native.publisher().read_history(
        FrozenHistory::decode(reference, &bytes).unwrap(),
        &mut case.fixture.native,
    );
    assert!(matches!(
        result,
        Err(Error::Gate("MONITOR_ALREADY_RUNNING"))
    ));
    drop(_guards);
    case.fixture
        .native
        .append(&Record::MigrationCompletion(completion))
        .unwrap();
    case.fixture.refresh();
    case.fixture.input.label = "new review".into();
    let plan = case.fixture.plan();
    assert_eq!(
        case.fixture.native.publisher().publish(
            &plan,
            &mut case.fixture.native,
            &mut |_, _| Ok(())
        ),
        Err(Error::Gate("MONITOR_ALREADY_RUNNING"))
    );
}
#[test]
fn terminal_requires_exact_saved_hash_and_cannot_hide_a_completed_migration() {
    let mut case = support::MigrationCase::new();
    case.begin();
    let record = Record::MigrationTermination(MigrationTermination {
        binding: case.intent.binding.clone(),
        intent_sha256: case.intent_hash.clone(),
        saved_sha256: Some(digest(b"foreign Saved")),
        outcome: MigrationOutcome::Abandoned,
        decision_reference: "native-stop".into(),
    });
    assert!(case.fixture.native.append(&record).is_err());
    let mut complete = support::MigrationCase::new();
    let completion = complete.complete();
    let record = Record::MigrationTermination(MigrationTermination {
        binding: complete.intent.binding.clone(),
        intent_sha256: complete.intent_hash.clone(),
        saved_sha256: Some(completion.saved_sha256),
        outcome: MigrationOutcome::Abandoned,
        decision_reference: "native-stop".into(),
    });
    assert!(complete.fixture.native.append(&record).is_err());
}
#[test]
fn refused_terminal_requires_original_readers_proven_absence() {
    let mut case = support::MigrationCase::new();
    case.begin();
    let record = Record::MigrationTermination(MigrationTermination {
        binding: case.intent.binding.clone(),
        intent_sha256: case.intent_hash.clone(),
        saved_sha256: None,
        outcome: MigrationOutcome::Refused,
        decision_reference: "native-refusal".into(),
    });
    assert!(case.fixture.native.append(&record).is_err());
}

#[test]
fn effect_session_is_bound_to_the_exact_frozen_review() {
    let mut f = Fixture::new();
    let plan = f.plan();
    let (link, _) = f.admit(&plan);
    let resolution = f.native.resolution().unwrap();
    let login = LoginIdentity {
        owner_sid: f.input.owner.owner_sid.clone(),
        session_id: 7,
    };
    assert!(resolution
        .require_effect_session(&login, &link.frozen)
        .is_ok());
    let mut foreign = link.frozen.clone();
    foreign.sha256 = digest(b"another frozen review");
    assert!(resolution.require_effect_session(&login, &foreign).is_err());
}

#[test]
fn artifact_observation_receives_and_enforces_its_read_bound() {
    let mut f = Fixture::new();
    for a in ARTIFACTS {
        let bound = if a == Artifact::Installation {
            16_384
        } else {
            1_048_576
        };
        let file = std::fs::File::create(f.path(a)).unwrap();
        file.set_len(bound as u64 + 1).unwrap();
        drop(file);
    }
    f.native.observed_limits.borrow_mut().clear();
    let resolution = f.native.resolution().unwrap();
    for (a, bound) in f.native.observed_limits.borrow().iter() {
        assert_eq!(
            *bound,
            if *a == Artifact::Installation {
                16_384
            } else {
                1_048_576
            }
        );
    }
    for a in ARTIFACTS {
        assert_eq!(
            resolution.ownership(&f.input.owner, a, &f.path(a), None),
            FileOwnership::Unknown
        );
    }
}
#[test]
fn storage_key_selects_record_bound_and_rejects_another_kind() {
    let f = Fixture::new();
    let record = managed(&f, 'c');
    let mut bytes = record.encode().unwrap();
    assert!(Record::decode(&EvidenceKey::Provisioning, &bytes).is_err());
    bytes.resize(RECORD_LIMIT, b' ');
    assert_eq!(Record::decode(&record.key(), &bytes).unwrap(), record);
    bytes.push(b' ');
    assert!(Record::decode(&record.key(), &bytes).is_err());
}
#[test]
fn pointer_validation_reuses_legacy_default_and_strict_schema() {
    use bat_fleet_core::{discovery::Backend, installation::validate_pointer};
    let f = Fixture::new();
    let root = &f.input.root;
    let legacy = serde_json::to_vec(&serde_json::json!({"kit_root":root})).unwrap();
    validate_pointer(&legacy, root, Backend::Powershell).unwrap();
    assert!(validate_pointer(&legacy, root, Backend::Rust).is_err());
    let mut bytes =
        serde_json::to_vec(&serde_json::json!({"kit_root":root,"backend":"rust"})).unwrap();
    validate_pointer(&bytes, root, Backend::Rust).unwrap();
    assert!(validate_pointer(&bytes, &root.join("other"), Backend::Rust).is_err());
    bytes.resize(16_385, b' ');
    assert!(validate_pointer(&bytes, root, Backend::Rust).is_err());
    let invalid = serde_json::to_vec(&serde_json::json!({"kit_root":root,"unknown":true})).unwrap();
    assert!(validate_pointer(&invalid, root, Backend::Powershell).is_err());
}
#[cfg(windows)]
#[test]
fn directory_keys_normalize_windows_case_and_separators() {
    let f = Fixture::new();
    let directory = DirectoryCreated {
        binding: binding(&f),
        canonical_path: "C:\\Synthetic\\Native\\fleet-creation".into(),
        parent_identity: f.native.receipt.scope.state_identity.clone(),
        identity: ObjectIdentity {
            volume: 9,
            file_id: "c".repeat(32),
        },
    };
    let alternate = DirectoryCreated {
        canonical_path: "c:/synthetic/native/fleet-creation/".into(),
        ..directory.clone()
    };
    assert_eq!(
        Record::Directory(directory).key(),
        Record::Directory(alternate).key()
    );
}
