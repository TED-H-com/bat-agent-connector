use super::*;
use bat_fleet_core::{
    discovery::Backend,
    migration::{self, Platform},
};
use support::MigrationCase;

fn terminal(c: &MigrationCase, outcome: MigrationOutcome) -> Record {
    Record::MigrationTermination(MigrationTermination {
        binding: c.intent.binding.clone(),
        intent_sha256: c.intent_hash.clone(),
        saved_sha256: c
            .store
            .observe_history(&c.intent.migration_id)
            .unwrap()
            .saved()
            .map(|s| s.sha256().into()),
        outcome,
        decision_reference: "native-end-migration".into(),
    })
}
fn answers(c: &mut MigrationCase) -> Vec<FileOwnership> {
    let r = c.fixture.native.resolution().unwrap();
    ARTIFACTS
        .iter()
        .map(|a| {
            r.ownership(
                &c.fixture.input.owner,
                *a,
                &c.fixture.path(*a),
                Some(&std::fs::read(c.fixture.path(*a)).unwrap()),
            )
        })
        .collect()
}
fn assert_ended(c: &mut MigrationCase, record: &Record, before: &[FileOwnership]) {
    assert_eq!(answers(c), before);
    let r = c.fixture.native.resolution().unwrap();
    assert!(r.settled());
    assert!(r.baseline().is_err());
    let saved = c.store.read_history(&c.intent.migration_id).unwrap();
    assert!(c
        .fixture
        .native
        .append(&Record::MigrationCompletion(MigrationCompletion {
            binding: c.intent.binding.clone(),
            intent_sha256: c.intent_hash.clone(),
            saved_sha256: saved.sha256().into(),
            after_sha256: digest(&saved.config(true).unwrap()),
        }))
        .is_err());
    // Identical durable outcomes remain idempotent after the Store moves on.
    assert_eq!(
        c.fixture.native.append(record).unwrap(),
        digest(&record.encode().unwrap())
    );
    assert!(c
        .fixture
        .native
        .append(&Record::MigrationIntent(c.intent.clone()))
        .is_err());
}
fn launch(c: &mut MigrationCase) {
    assert_eq!(
        c.store.advance(&mut c.producer, &c.intent.migration_id),
        Ok(migration::Progress::WaitingLaunch)
    );
}
fn restore(c: &mut MigrationCase, complete: bool) -> MigrationOutcome {
    *c.fixture.native.running.borrow_mut() = None;
    let id = "8".repeat(32);
    c.store
        .restore(&mut c.producer, &c.intent.migration_id, &id)
        .unwrap();
    assert_eq!(
        c.store.advance(&mut c.producer, &id),
        Ok(migration::Progress::WaitingLaunch)
    );
    if complete {
        assert_eq!(
            c.store.advance(&mut c.producer, &id),
            Ok(migration::Progress::Complete)
        );
    }
    MigrationOutcome::Restored {
        migration_id: id.clone(),
        saved_sha256: c.store.read_history(&id).unwrap().sha256().into(),
    }
}
#[test]
fn abandoned_launch_later_completes_without_losing_other_slots() {
    let mut c = MigrationCase::new();
    c.begin();
    launch(&mut c);
    let monitor = c.fixture.native.running.borrow_mut().take();
    assert_eq!(
        c.store.advance(&mut c.producer, &c.intent.migration_id),
        Err("MIGRATION_LAUNCH_UNKNOWN")
    );
    let record = terminal(&c, MigrationOutcome::Abandoned);
    c.fixture.native.append(&record).unwrap();
    c.assert_only_installation_unknown();
    let before = answers(&mut c);
    *c.fixture.native.running.borrow_mut() = monitor;
    assert_eq!(
        c.store.advance(&mut c.producer, &c.intent.migration_id),
        Ok(migration::Progress::Complete)
    );
    assert_ended(&mut c, &record, &before);
}
#[test]
fn refused_migration_later_begins_without_losing_other_slots() {
    let mut c = MigrationCase::new();
    let record = terminal(&c, MigrationOutcome::Refused);
    c.fixture.native.append(&record).unwrap();
    c.assert_only_installation_unknown();
    let before = answers(&mut c);
    c.begin();
    assert_ended(&mut c, &record, &before);
}
#[test]
fn restored_prepared_source_reactivates_without_losing_other_slots() {
    let mut c = MigrationCase::new();
    c.begin();
    let outcome = restore(&mut c, true);
    let record = terminal(&c, outcome);
    c.fixture.native.append(&record).unwrap();
    c.assert_only_installation_unknown();
    let before = answers(&mut c);
    *c.fixture.native.running.borrow_mut() = None;
    c.begin();
    launch(&mut c);
    assert_eq!(
        c.store.advance(&mut c.producer, &c.intent.migration_id),
        Ok(migration::Progress::Complete)
    );
    assert_ended(&mut c, &record, &before);
}
#[test]
fn other_session_termination_bounds_the_original_sessions_completion() {
    let mut c = MigrationCase::new();
    c.begin();
    launch(&mut c);
    c.fixture.native.session.set(8);
    let record = terminal(&c, MigrationOutcome::Abandoned);
    c.fixture.native.append(&record).unwrap();
    c.assert_only_installation_unknown();
    let before = answers(&mut c);
    c.fixture.native.session.set(7);
    assert_eq!(
        c.store.advance(&mut c.producer, &c.intent.migration_id),
        Ok(migration::Progress::Complete)
    );
    assert_ended(&mut c, &record, &before);
}
fn begin_unbound(c: &mut MigrationCase) {
    // A separate native boundary injects a real Store transition during a fence.
    // It has its own synthetic guard flags; Store validation and IO stay real.
    let mut p = support::MigrationNative {
        native: c.producer.native.clone(),
    };
    p.native.launcher = std::rc::Rc::new(std::cell::Cell::new(false));
    p.native.monitor = std::rc::Rc::new(std::cell::Cell::new(false));
    c.store
        .begin(
            &mut p,
            &"1".repeat(32),
            &c.preview,
            Backend::Powershell,
            &c.after,
            false,
        )
        .unwrap();
}
fn pending_admission(in_callback: bool) {
    let mut c = MigrationCase::unadmitted();
    let record = Record::MigrationIntent(c.intent.clone());
    if !in_callback {
        begin_unbound(&mut c);
    }
    let mut io = c.fixture.native.clone();
    let _guards = io.guards();
    let entries = io.entries().unwrap();
    let histories = io.histories();
    let views = io.migrations(Some(&record)).unwrap();
    let mut calls = 0;
    let result = append(
        &mut io,
        &c.fixture.input.owner.clone(),
        &record,
        &histories,
        &views,
        &mut || {
            calls += 1;
            if calls == 1 {
                begin_unbound(&mut c);
            }
            Ok(())
        },
    );
    assert_eq!(result, Err("MIGRATION_PENDING"));
    assert_eq!(calls, usize::from(in_callback));
    assert!(!io.evidence_path(&record.key()).exists());
    assert_eq!(io.entries().unwrap(), entries);
}
#[test]
fn migration_intent_refuses_unbound_pending_store_before_authorization() {
    pending_admission(false);
}
#[test]
fn migration_intent_fence_refuses_store_pending_during_authorization() {
    pending_admission(true);
}
#[test]
fn migration_intent_admits_when_store_has_nothing_pending() {
    let mut c = MigrationCase::unadmitted();
    assert!(c.store.pending().unwrap().is_none());
    c.fixture
        .native
        .append(&Record::MigrationIntent(c.intent.clone()))
        .unwrap();
}

#[test]
fn pending_intent_does_not_own_its_before_bytes() {
    let mut c = MigrationCase::new();
    assert_eq!(
        digest(&std::fs::read(c.fixture.path(Artifact::Installation)).unwrap()),
        c.intent.before_sha256
    );
    c.assert_only_installation_unknown();
    assert!(!c.fixture.native.resolution().unwrap().settled());
}
#[test]
fn termination_binding_must_equal_its_intent() {
    let mut c = MigrationCase::new();
    c.begin();
    let mut record = terminal(&c, MigrationOutcome::Abandoned);
    if let Record::MigrationTermination(t) = &mut record {
        t.binding.session_id += 1;
    }
    assert!(c.fixture.native.append(&record).is_err());
}
fn edit_saved(c: &MigrationCase, id: &str, edit: impl FnOnce(&mut serde_json::Value)) -> String {
    let path = c
        .fixture
        .root
        .join(format!("native/fleet-migrations/{id}.json"));
    let mut value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    edit(&mut value);
    std::fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
    c.store.read_history(id).unwrap().sha256().into()
}
#[test]
fn termination_saved_must_match_its_intents_review() {
    let mut c = MigrationCase::new();
    c.begin();
    edit_saved(&c, &c.intent.migration_id, |v| {
        v["review_fingerprint"] = digest(b"other review").into()
    });
    let record = terminal(&c, MigrationOutcome::Abandoned);
    assert!(c.fixture.native.append(&record).is_err());
}
#[test]
fn termination_restore_requires_its_exact_saved_hash() {
    let mut c = MigrationCase::new();
    c.begin();
    let mut outcome = restore(&mut c, true);
    if let MigrationOutcome::Restored { saved_sha256, .. } = &mut outcome {
        *saved_sha256 = digest(b"wrong restore");
    }
    let record = terminal(&c, outcome);
    assert!(c.fixture.native.append(&record).is_err());
}
#[test]
fn termination_restore_requires_complete_phase() {
    let mut c = MigrationCase::new();
    c.begin();
    let outcome = restore(&mut c, false);
    let record = terminal(&c, outcome);
    assert!(c.fixture.native.append(&record).is_err());
}
fn invalid_restore(edit: impl FnOnce(&mut serde_json::Value)) {
    let mut c = MigrationCase::new();
    c.begin();
    let mut outcome = restore(&mut c, true);
    if let MigrationOutcome::Restored {
        migration_id,
        saved_sha256,
    } = &mut outcome
    {
        *saved_sha256 = edit_saved(&c, migration_id, edit);
    }
    let record = terminal(&c, outcome);
    assert!(c.fixture.native.append(&record).is_err());
}
#[test]
fn termination_restore_requires_source_lineage() {
    invalid_restore(|v| v["restores"] = "7".repeat(32).into());
}
#[test]
fn termination_restore_requires_the_same_sid() {
    invalid_restore(|v| {
        v["sid"] = "S-1-5-21-123-456-789-1002".into();
        v["accepted_owner"]["sid"] = v["sid"].clone();
    });
}
fn pad_config(v: &mut serde_json::Value, field: &str) {
    use base64::{engine::general_purpose::STANDARD, Engine};
    let mut b = STANDARD.decode(v[field].as_str().unwrap()).unwrap();
    b.push(b' ');
    v[field] = STANDARD.encode(b).into();
}
#[test]
fn termination_restore_after_bytes_equal_source_original() {
    invalid_restore(|v| pad_config(v, "next_config"));
}
#[test]
fn termination_restore_before_bytes_match_a_source_version() {
    invalid_restore(|v| pad_config(v, "original_config"));
}
#[test]
fn extra_termination_is_a_record_conflict_not_store_progress() {
    let mut c = MigrationCase::new();
    c.begin();
    let record = terminal(&c, MigrationOutcome::Abandoned);
    c.fixture.native.append(&record).unwrap();
    let mut other = record.clone();
    if let Record::MigrationTermination(t) = &mut other {
        t.decision_reference = "another native decision".into();
    }
    let path = c
        .fixture
        .native
        .evidence_path(&EvidenceKey::MigrationCompletion(digest(b"second outcome")));
    std::fs::write(path, other.encode().unwrap()).unwrap();
    assert!(c.fixture.native.resolution().is_err());
}
#[test]
fn changed_saved_scope_is_still_a_record_conflict() {
    let mut c = MigrationCase::new();
    c.begin();
    let record = terminal(&c, MigrationOutcome::Abandoned);
    c.fixture.native.append(&record).unwrap();
    edit_saved(&c, &c.intent.migration_id, |v| {
        v["review_fingerprint"] = digest(b"other review").into()
    });
    assert!(c.fixture.native.resolution().is_err());
}
#[test]
fn context_refuses_another_receipt_scope_or_startup() {
    for startup in [false, true] {
        let mut f = Fixture::unreserved();
        let _guards = f.native.guards();
        let mut receipt = f.native.receipt.clone();
        if startup {
            receipt.startup.generation += 1;
        } else {
            receipt.scope.state_identity.volume += 1;
        }
        let mut calls = 0;
        assert_eq!(
            reserve(&mut f.native, &receipt, &mut || {
                calls += 1;
                Ok(())
            }),
            Err("CONTEXT_CHANGED")
        );
        assert_eq!(calls, 0);
        assert!(!f.native.evidence_path(&EvidenceKey::Provisioning).exists());
    }
}
fn effect_context(reservation: bool, in_callback: bool) {
    let mut f = if reservation {
        Fixture::unreserved()
    } else {
        Fixture::new()
    };
    let record = if reservation {
        Record::Provisioning(Box::new(f.native.receipt.clone()))
    } else {
        let plan = f.plan();
        Record::Publication(f.prepare(&plan))
    };
    let _guards = f.native.guards();
    let entries = f.native.entries().unwrap();
    let mut producer = support::MigrationNative {
        native: f.native.clone(),
    };
    if !in_callback {
        producer.launch(Backend::Rust).unwrap();
    }
    let owner = f.input.owner.clone();
    let receipt = f.native.receipt.clone();
    let histories = if let Record::Publication(link) = &record {
        vec![f.native.history(&link.frozen)]
    } else {
        vec![]
    };
    let mut calls = 0;
    let mut authorize = || {
        calls += 1;
        if calls == 1 {
            producer.launch(Backend::Rust).unwrap();
        }
        Ok(())
    };
    let result = if reservation {
        reserve(&mut f.native, &receipt, &mut authorize).map(|_| ())
    } else {
        append(
            &mut f.native,
            &owner,
            &record,
            &histories,
            &[],
            &mut authorize,
        )
        .map(|_| ())
    };
    assert_eq!(result, Err("MONITOR_ALREADY_RUNNING"));
    assert_eq!(calls, usize::from(in_callback));
    assert!(!f.native.evidence_path(&record.key()).exists());
    assert_eq!(f.native.entries().unwrap(), entries);
}
#[test]
fn reserve_directly_checks_running_monitor_before_authorization() {
    effect_context(true, false);
}
#[test]
fn append_directly_checks_running_monitor_before_authorization() {
    effect_context(false, false);
}
#[test]
fn reserve_fence_checks_monitor_appearing_during_authorization() {
    effect_context(true, true);
}
#[test]
fn append_fence_checks_monitor_appearing_during_authorization() {
    effect_context(false, true);
}

#[test]
fn replay_keeps_termination_binding_checks_after_store_progress() {
    let mut c = MigrationCase::new();
    c.begin();
    let mut record = terminal(&c, MigrationOutcome::Abandoned);
    c.fixture.native.append(&record).unwrap();
    launch(&mut c);
    if let Record::MigrationTermination(t) = &mut record {
        t.binding.session_id += 1;
    }
    raw_record(&c.fixture, &record);
    assert!(c.fixture.native.resolution().is_err());
}
#[test]
fn replay_bounds_restore_hash_and_phase_changes_but_requires_saved() {
    let mut c = MigrationCase::new();
    c.begin();
    let outcome = restore(&mut c, false);
    let id = match &outcome {
        MigrationOutcome::Restored { migration_id, .. } => migration_id.clone(),
        _ => unreachable!(),
    };
    let path = c
        .fixture
        .root
        .join(format!("native/fleet-migrations/{id}.json"));
    // Both byte strings come from the real Store, not a copied Saved schema.
    let pending_bytes = std::fs::read(&path).unwrap();
    c.store.advance(&mut c.producer, &id).unwrap();
    let outcome = MigrationOutcome::Restored {
        migration_id: id.clone(),
        saved_sha256: c.store.read_history(&id).unwrap().sha256().into(),
    };
    let record = terminal(&c, outcome);
    c.fixture.native.append(&record).unwrap();
    let before = answers(&mut c);
    std::fs::write(&path, pending_bytes).unwrap();
    assert_eq!(answers(&mut c), before);
    assert_eq!(
        c.fixture.native.append(&record).unwrap(),
        digest(&record.encode().unwrap())
    );
    std::fs::remove_file(path).unwrap();
    assert!(c.fixture.native.resolution().is_err());
}
#[test]
fn raw_successor_cannot_follow_an_unsettled_link() {
    let mut f = Fixture::new();
    let plan = f.plan();
    let (link, hash) = f.admit(&plan);
    let mut next = link;
    next.predecessor_sha256 = hash;
    raw_record(&f, &Record::Publication(next));
    assert!(f.native.resolution().is_err());
}
#[test]
fn raw_migration_intent_must_start_from_the_rooted_pointer_version() {
    let mut c = MigrationCase::unadmitted();
    c.intent.before_sha256 = digest(b"unrelated pointer");
    raw_record(&c.fixture, &Record::MigrationIntent(c.intent.clone()));
    assert!(c.fixture.native.resolution().is_err());
}

#[test]
fn read_only_evidence_adapter_defaults_to_refusing_migration_admission() {
    struct ReadOnly;
    impl EvidenceIo for ReadOnly {
        fn login(&self) -> bat_fleet_core::Result<LoginIdentity> {
            unreachable!()
        }
        fn verify_context(&self, _: &ProvisioningReceipt) -> bat_fleet_core::Result<()> {
            unreachable!()
        }
        fn verify_effect_context(&self) -> bat_fleet_core::Result<()> {
            unreachable!()
        }
        fn verify_record(&self, _: &Record) -> bat_fleet_core::Result<()> {
            unreachable!()
        }
        fn entries(&self) -> bat_fleet_core::Result<Vec<EvidenceKey>> {
            unreachable!()
        }
        fn read_private(
            &self,
            _: &EvidenceKey,
            _: usize,
        ) -> bat_fleet_core::Result<Option<Vec<u8>>> {
            unreachable!()
        }
        fn observe(&self, _: &SlotReservation, _: usize) -> bat_fleet_core::Result<ObservedSlot> {
            unreachable!()
        }
        fn create_only(
            &mut self,
            _: &ProvisioningReceipt,
            _: &EvidenceKey,
            _: &[u8],
            _: &WriteFence,
            _: &mut dyn FnMut() -> bat_fleet_core::Result<()>,
        ) -> bat_fleet_core::Result<()> {
            unreachable!()
        }
    }
    assert_eq!(
        ReadOnly.verify_no_pending_migration(),
        Err("MIGRATION_PENDING")
    );
}

#[test]
fn termination_selects_its_own_intent_after_an_earlier_completed_migration() {
    let mut f = Fixture::new();
    let plan = f.plan();
    let mut link = f.prepare(&plan);
    // Choose a bounded synthetic acceptance reference that orders this key
    // before the subsequent intent. Every candidate is a valid native shape.
    let key_ordered = (0..1024).any(|n| {
        link.acceptance_reference = format!("native-review-{n}");
        digest(&Record::Publication(link.clone()).encode().unwrap()).starts_with('0')
    });
    assert!(key_ordered);
    f.native.append(&Record::Publication(link)).unwrap();
    f.publish(&plan);
    let mut c = MigrationCase::from_published_fixture(f);
    let key_ordered = (0..1024).any(|n| {
        c.intent.acceptance_reference = format!("native-migration-{n}");
        c.intent_hash = digest(&Record::MigrationIntent(c.intent.clone()).encode().unwrap());
        c.intent_hash > c.intent.predecessor_sha256
    });
    assert!(key_ordered);
    c.fixture
        .native
        .append(&Record::MigrationIntent(c.intent.clone()))
        .unwrap();
    let completion = c.complete();
    c.fixture
        .native
        .append(&Record::MigrationCompletion(completion))
        .unwrap();
    *c.fixture.native.running.borrow_mut() = None;
    let r = c.fixture.native.resolution().unwrap();
    let preview = c
        .store
        .preview(&mut c.producer, Backend::Powershell)
        .unwrap();
    let mut intent = c.intent.clone();
    intent.predecessor_sha256 = r.head().into();
    intent.predecessor_terminal = r.terminal().unwrap().clone();
    intent.migration_id = "6".repeat(32);
    intent.review_fingerprint = preview.fingerprint().into();
    intent.before_sha256 = digest(&c.after);
    // Put the new lineage key after the earlier one in iteration order. This
    // ensures a lookup of any intent cannot accidentally select the right one.
    assert!(intent.predecessor_sha256 > c.intent.predecessor_sha256);
    let intent_hash = c
        .fixture
        .native
        .append(&Record::MigrationIntent(intent.clone()))
        .unwrap();
    let original = c
        .store
        .read_history(&c.intent.migration_id)
        .unwrap()
        .config(false)
        .unwrap();
    c.store
        .begin(
            &mut c.producer,
            &intent.migration_id,
            &preview,
            Backend::Rust,
            &original,
            false,
        )
        .unwrap();
    let record = Record::MigrationTermination(MigrationTermination {
        binding: intent.binding,
        intent_sha256: intent_hash,
        saved_sha256: Some(
            c.store
                .read_history(&intent.migration_id)
                .unwrap()
                .sha256()
                .into(),
        ),
        outcome: MigrationOutcome::Abandoned,
        decision_reference: "native-second-migration-end".into(),
    });
    c.fixture.native.append(&record).unwrap();
    c.assert_only_installation_unknown();
}
