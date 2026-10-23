use bat_fleet_core::{
    connection_publish::{AbandonDecision, Boundary, Error},
    connection_setup::{Artifact, FileOwnership},
    creation_evidence::*,
    digest,
    process_adapter::LoginIdentity,
};
#[path = "support/creation_evidence.rs"]
mod support;
use support::Fixture;

fn owned(value: FileOwnership) -> bool {
    matches!(value, FileOwnership::Owned { .. })
}
fn interrupt(
    f: &mut Fixture,
    plan: &bat_fleet_core::connection_setup::PublicationPlan,
    point: Boundary,
    artifact: Artifact,
) {
    let result = f.native.publisher().publish_authorized(
        plan,
        &mut f.native,
        &mut |_, _| Ok(()),
        &mut |p, a| {
            if p == point && a == artifact {
                Err("INTERRUPTED")
            } else {
                Ok(())
            }
        },
    );
    assert_eq!(result, Err(Error::Gate("INTERRUPTED")));
}
fn abandon(f: &mut Fixture, plan: &bat_fleet_core::connection_setup::PublicationPlan) {
    f.native
        .publisher()
        .abandon_remaining(
            plan,
            &AbandonDecision {
                reference: "native-abandon".into(),
                recorded_at_unix_ms: 1,
            },
            &mut f.native,
            &mut |_| Ok(()),
        )
        .unwrap();
}
fn raw_record(f: &Fixture, record: &Record) {
    let path = f.native.evidence_path(&record.key());
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, record.encode().unwrap()).unwrap();
}

#[test]
fn provisioning_receipt_precedes_generator_and_binds_all_four_absences() {
    let mut f = Fixture::unreserved();
    for a in ARTIFACTS {
        assert_eq!(f.ownership(a), FileOwnership::Unknown);
    }
    let owner = f.reserve().unwrap();
    assert_eq!(owner.installation_id, f.native.receipt.fleet_creation_id);
    for a in ARTIFACTS {
        assert_eq!(
            f.ownership(a),
            FileOwnership::Owned {
                owner: owner.clone(),
                artifact: a,
                sha256: digest(b"")
            }
        );
        assert!(!f.path(a).exists());
    }
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    for a in ARTIFACTS {
        assert!(owned(f.ownership(a)));
    }
}
#[test]
fn provisioning_receipt_is_bounded_strict_and_versioned() {
    let f = Fixture::new();
    let bytes = Record::Provisioning(Box::new(f.native.receipt.clone()))
        .encode()
        .unwrap();
    let doc: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    for field in ["version", "minimum_reader_version"] {
        let mut v = doc.clone();
        v[field] = 2.into();
        assert!(
            Record::decode(&EvidenceKey::Provisioning, &serde_json::to_vec(&v).unwrap()).is_err()
        );
    }
    let mut missing = doc.clone();
    missing["record"]["body"]
        .as_object_mut()
        .unwrap()
        .remove("managed_connector");
    assert!(Record::decode(
        &EvidenceKey::Provisioning,
        &serde_json::to_vec(&missing).unwrap()
    )
    .is_err());
    let mut missing = doc.clone();
    missing["record"]["body"]["slots"][0]
        .as_object_mut()
        .unwrap()
        .remove("before_sha256");
    assert!(Record::decode(
        &EvidenceKey::Provisioning,
        &serde_json::to_vec(&missing).unwrap()
    )
    .is_err());
    let mut v = doc.clone();
    v["unknown"] = true.into();
    assert!(Record::decode(&EvidenceKey::Provisioning, &serde_json::to_vec(&v).unwrap()).is_err());
    let duplicate = String::from_utf8(bytes.clone()).unwrap().replacen(
        "\"version\":1",
        "\"version\":1,\"VERSION\":1",
        1,
    );
    assert!(Record::decode(&EvidenceKey::Provisioning, duplicate.as_bytes()).is_err());
    let mut over = bytes.clone();
    over.resize(RECEIPT_LIMIT + 1, b' ');
    assert!(Record::decode(&EvidenceKey::Provisioning, &over).is_err());
    let mut r = f.native.receipt.clone();
    r.slots.swap(0, 1);
    assert!(Record::Provisioning(Box::new(r)).encode().is_err());
    let mut r = f.native.receipt.clone();
    r.slots[0].before_sha256 = Some(digest(b""));
    assert!(Record::Provisioning(Box::new(r)).encode().is_err());
    let mut r = f.native.receipt.clone();
    r.scope.pointer = "relative/fleet.json".into();
    assert!(Record::Provisioning(Box::new(r)).encode().is_err());
    let mut r = f.native.receipt.clone();
    r.startup.installation_ref = "b".repeat(32);
    assert!(Record::Provisioning(Box::new(r)).encode().is_err());
    let mut r = f.native.receipt.clone();
    r.directories = vec![r.directories[0].clone(); 9];
    assert!(Record::Provisioning(Box::new(r)).encode().is_err());
    let mut r = f.native.receipt.clone();
    r.slots[0].missing_components = vec!["..".into()];
    assert!(Record::Provisioning(Box::new(r)).encode().is_err());
    let mut r = f.native.receipt.clone();
    r.scope.kit_root = format!("/{}", "x".repeat(4097)).into();
    assert!(Record::Provisioning(Box::new(r)).encode().is_err());
    let mut owner = f.native.owner.clone();
    owner.creation_receipt.push('/');
    assert!(parse_owner_reference(&owner).is_err());
    // No valid typed document exceeds eight containers. This malformed shape
    // is also rejected by Document deserialization; it is not independent depth coverage.
    let deep = format!("{}0{}", "[".repeat(9), "]".repeat(9));
    assert!(Record::decode(&EvidenceKey::Provisioning, deep.as_bytes()).is_err());
    let mut padded = bytes;
    padded.extend(vec![b' '; RECEIPT_LIMIT - padded.len()]);
    assert!(Record::decode(&EvidenceKey::Provisioning, &padded).is_ok());
}
#[test]
fn provisioning_create_only_race_and_crash_keep_original_identity() {
    let mut f = Fixture::unreserved();
    f.native.create_race.set(true);
    assert!(f.reserve().is_err());
    assert_eq!(std::fs::read(f.path(Artifact::Inventory)).unwrap(), b"race");
    let mut f = Fixture::unreserved();
    f.native.fail_after_create.set(true);
    assert!(f.reserve().is_err());
    let path = f.native.evidence_path(&EvidenceKey::Provisioning);
    let original = std::fs::read(&path).unwrap();
    let owner = f.reserve().unwrap();
    assert_eq!(owner, f.native.owner);
    assert_eq!(std::fs::read(&path).unwrap(), original);
    let mut different = f.native.receipt.clone();
    different.fleet_creation_id = "c".repeat(32);
    let _guards = f.native.guards();
    assert!(reserve(&mut f.native, &different, &mut || Ok(())).is_err());
    assert_eq!(std::fs::read(path).unwrap(), original);
    let mut orphan = Fixture::unreserved();
    std::fs::create_dir(orphan.root.join("native/fleet-creation")).unwrap();
    assert!(orphan.reserve().is_err());
}
#[test]
fn provisioning_external_appearance_never_adopts_matching_output() {
    let mut raced = Fixture::new();
    let plan = raced.plan();
    let link = raced.prepare(&plan);
    raced.native.create_race.set(true);
    assert!(raced.native.append(&Record::Publication(link)).is_err());
    for a in ARTIFACTS {
        let mut f = Fixture::new();
        let plan = f.plan();
        let link = f.prepare(&plan);
        for bytes in [b"arbitrary".as_slice(), b"", &plan.output(a).bytes] {
            std::fs::write(f.path(a), bytes).unwrap();
            assert_eq!(f.ownership(a), FileOwnership::Unknown);
            assert!(f.native.append(&Record::Publication(link.clone())).is_err());
            assert_eq!(std::fs::read(f.path(a)).unwrap(), bytes);
            f.native.external.set(Some(a));
            assert_eq!(f.ownership(a), FileOwnership::ReferenceOnly);
            f.native.external.set(None);
        }
    }
}
#[test]
fn resolver_missing_or_foreign_authority_is_unknown() {
    let mut f = Fixture::unreserved();
    std::fs::write(f.path(Artifact::Inventory), b"copied external bytes").unwrap();
    assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
    f.native.external.set(Some(Artifact::Inventory));
    assert_eq!(
        f.ownership(Artifact::Inventory),
        FileOwnership::ReferenceOnly
    );
    let mut f = Fixture::new();
    let _guards = f.native.guards();
    let mut owner = f.native.owner.clone();
    owner.owner_sid = "S-1-5-21-999".into();
    assert!(capture(&f.native, &owner).is_err());
    let mut owner = f.native.owner.clone();
    owner.creation_receipt = "synthetic-creation-authority".into();
    assert!(capture(&f.native, &owner).is_err());
    let mut owner = f.native.owner.clone();
    owner.installation_id = "1".repeat(32);
    assert!(capture(&f.native, &owner).is_err());
}
#[test]
fn resolver_requires_unique_rooted_publication_links() {
    let mut f = Fixture::new();
    let plan = f.plan();
    let link = f.prepare(&plan);
    let mut wrong = link.clone();
    wrong.predecessor_sha256 = digest(b"unrooted");
    raw_record(&f, &Record::Publication(wrong));
    assert!(f.native.resolution().is_err());
    std::fs::remove_dir_all(f.root.join("native/fleet-creation/lineage")).unwrap();
    f.native.append(&Record::Publication(link.clone())).unwrap();
    let mut competitor = link.clone();
    competitor.acceptance_reference = "other acceptance".into();
    assert!(f.native.append(&Record::Publication(competitor)).is_err());
    let mut wrong = link;
    wrong.frozen.sha256 = digest(b"different snapshot");
    assert!(f.native.append(&Record::Publication(wrong)).is_err());

    let mut f = Fixture::new();
    let plan = f.plan();
    let (_, hash) = f.admit(&plan);
    interrupt(&mut f, &plan, Boundary::AfterReplace, Artifact::Inventory);
    let link_path = f.native.evidence_path(&EvidenceKey::Lineage(
        parse_owner_reference(&f.native.owner).unwrap().into(),
    ));
    let bytes = std::fs::read(&link_path).unwrap();
    assert_eq!(digest(&bytes), hash);
    std::fs::remove_file(&link_path).unwrap();
    assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
    std::fs::write(link_path, bytes).unwrap();
    f.publish(&plan);
    f.refresh();
    let noop = f.plan();
    let mut next = f.prepare(&noop);
    assert!(noop.steps.is_empty());
    for i in 1..=HISTORY_LIMIT {
        next.acceptance_reference = format!("native-noop-{i}");
        let link = Record::Publication(next.clone());
        let hash = digest(&link.encode().unwrap());
        raw_record(&f, &link);
        let close = Record::Closure(AdmissionClosure {
            binding: next.binding.clone(),
            link_sha256: hash.clone(),
            reason: CloseReason::Unchanged,
            decision_reference: next.acceptance_reference.clone(),
            baseline: next.baseline.clone(),
            journal_absent: true,
        });
        raw_record(&f, &close);
        next.predecessor_sha256 = hash;
        next.predecessor_terminal = Some(Terminal {
            journal_sha256: None,
            decision_sha256: None,
            closure_sha256: Some(digest(&close.encode().unwrap())),
            migration_sha256: None,
        });
        if i + 1 == HISTORY_LIMIT {
            assert!(f.native.resolution().unwrap().baseline().is_ok());
        }
    }
    assert!(f.native.resolution().is_err());
}
#[test]
fn resolver_reached_intent_recovers_after_but_unreached_output_does_not() {
    for a in ARTIFACTS {
        for point in [
            Boundary::BeforeIntended,
            Boundary::AfterIntended,
            Boundary::AfterReplace,
            Boundary::AfterReadBack,
            Boundary::BeforePointer,
            Boundary::AfterPointer,
        ] {
            if matches!(point, Boundary::BeforePointer | Boundary::AfterPointer)
                && a != Artifact::Installation
            {
                continue;
            }
            let mut f = Fixture::new();
            let plan = f.plan();
            f.admit(&plan);
            interrupt(&mut f, &plan, point, a);
            for artifact in ARTIFACTS {
                assert!(owned(f.ownership(artifact)), "{point:?} {a:?} {artifact:?}");
            }
            let preserved: Vec<_> = ARTIFACTS
                .into_iter()
                .filter_map(|artifact| {
                    std::fs::metadata(f.path(artifact))
                        .ok()
                        .map(|metadata| (artifact, metadata.modified().unwrap()))
                })
                .collect();
            f.publish(&plan);
            for (artifact, modified) in preserved {
                assert_eq!(
                    std::fs::metadata(f.path(artifact))
                        .unwrap()
                        .modified()
                        .unwrap(),
                    modified
                );
            }
        }
    }
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    interrupt(&mut f, &plan, Boundary::AfterReplace, Artifact::Inventory);
    assert!(owned(f.ownership(Artifact::Inventory)));
    let unreached = Artifact::ProfileIndex;
    std::fs::write(f.path(unreached), &plan.output(unreached).bytes).unwrap();
    assert_eq!(f.ownership(unreached), FileOwnership::Unknown);
    std::fs::remove_file(f.path(unreached)).unwrap();
    assert!(owned(f.ownership(unreached)));
    f.publish(&plan);
    for a in ARTIFACTS {
        assert!(owned(f.ownership(a)));
    }
}
#[test]
fn resolver_old_digest_and_deleted_published_slot_do_not_rewind() {
    let mut f = Fixture::new();
    let original_label = f.input.label.clone();
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    let a = Artifact::Inventory;
    let old = std::fs::read(f.path(a)).unwrap();
    f.refresh();
    f.input.label = "Changed display name".into();
    let next = f.plan();
    assert_ne!(next.output(a).sha256, plan.output(a).sha256);
    f.admit(&next);
    f.publish(&next);
    std::fs::write(f.path(a), &old).unwrap();
    assert_eq!(f.ownership(a), FileOwnership::Unknown);
    std::fs::remove_file(f.path(a)).unwrap();
    assert_eq!(f.ownership(a), FileOwnership::Unknown);
    std::fs::write(f.path(a), &next.output(a).bytes).unwrap();
    f.refresh();
    f.input.label = original_label;
    let repeated = f.plan();
    assert_eq!(repeated.output(a).sha256, plan.output(a).sha256);
    f.admit(&repeated);
    f.publish(&repeated);
    assert!(owned(f.ownership(a)));
}
#[test]
fn resolver_abandonment_preserves_prefix_and_requires_fresh_review() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    interrupt(&mut f, &plan, Boundary::AfterReplace, Artifact::Inventory);
    let journal = f.root.join(format!(
        "native/fleet-publications/{}.json",
        plan.review_digest
    ));
    let original = std::fs::read(&journal).unwrap();
    abandon(&mut f, &plan);
    assert_eq!(std::fs::read(journal).unwrap(), original);
    assert!(owned(f.ownership(Artifact::Inventory)));
    let resolution = f.native.resolution().unwrap();
    assert!(resolution.settled());
    assert!(resolution
        .require_effect_session(
            &LoginIdentity {
                owner_sid: f.native.owner.owner_sid.clone(),
                session_id: 7
            },
            &f.reference(&plan)
        )
        .is_err());
    f.refresh();
    f.input.label = "Fresh review".into();
    let next = f.plan();
    f.admit(&next);
    f.publish(&next);
    for a in ARTIFACTS {
        assert!(owned(f.ownership(a)));
    }
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    interrupt(&mut f, &plan, Boundary::AfterIntended, Artifact::Inventory);
    std::fs::write(
        f.path(Artifact::ProfileIndex),
        &plan.output(Artifact::ProfileIndex).bytes,
    )
    .unwrap();
    abandon(&mut f, &plan);
    assert_eq!(f.ownership(Artifact::ProfileIndex), FileOwnership::Unknown);
}
#[test]
fn resolver_noop_and_unchanged_fourth_slot_require_ownership() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    f.publish(&plan);
    f.refresh();
    let noop = f.plan();
    assert!(noop.steps.is_empty());
    let (link, hash) = f.admit(&noop);
    let closure = AdmissionClosure {
        binding: link.binding.clone(),
        link_sha256: hash,
        reason: CloseReason::Unchanged,
        decision_reference: link.acceptance_reference.clone(),
        baseline: link.baseline.clone(),
        journal_absent: true,
    };
    f.native.append(&Record::Closure(closure)).unwrap();
    assert!(f.native.resolution().unwrap().settled());
    f.native.external.set(Some(Artifact::Installation));
    assert!(f.native.resolution().unwrap().baseline().is_err());
    assert_eq!(
        f.ownership(Artifact::Installation),
        FileOwnership::ReferenceOnly
    );
}
#[test]
fn admission_without_journal_closes_only_with_unchanged_baseline() {
    let mut f = Fixture::new();
    let plan = f.plan();
    let (link, hash) = f.admit(&plan);
    let mut closure = AdmissionClosure {
        binding: link.binding.clone(),
        link_sha256: hash,
        reason: CloseReason::Unchanged,
        decision_reference: link.acceptance_reference.clone(),
        baseline: link.baseline.clone(),
        journal_absent: true,
    };
    assert!(f.native.append(&Record::Closure(closure.clone())).is_err());
    closure.reason = CloseReason::AbandonedBeforeEffect;
    closure.decision_reference = "explicit-native-abandon".into();
    std::fs::write(f.path(Artifact::Inventory), b"changed").unwrap();
    assert!(f.native.append(&Record::Closure(closure.clone())).is_err());
    std::fs::remove_file(f.path(Artifact::Inventory)).unwrap();
    f.native.append(&Record::Closure(closure)).unwrap();
    assert!(f.native.resolution().unwrap().settled());
    let mut f = Fixture::new();
    let plan = f.plan();
    let (link, hash) = f.admit(&plan);
    interrupt(&mut f, &plan, Boundary::AfterIntended, Artifact::Inventory);
    let closure = AdmissionClosure {
        binding: link.binding,
        link_sha256: hash,
        reason: CloseReason::AbandonedBeforeEffect,
        decision_reference: "abandon".into(),
        baseline: link.baseline,
        journal_absent: true,
    };
    assert!(f.native.append(&Record::Closure(closure)).is_err());
}
#[test]
fn migration_binding_anchors_only_exact_pointer_transition() {
    support::migration_case();
}
#[test]
fn identities_survive_upgrade_without_uuid_hex_conversion() {
    let mut f = Fixture::new();
    let original = std::fs::read(f.native.evidence_path(&EvidenceKey::Provisioning)).unwrap();
    let managed = ManagedIdentity {
        installation_id: "c".repeat(32),
        manifest_sha256: digest(b"verified managed manifest"),
        actor: format!("desktop-{}", "c".repeat(32)),
        server_id: "synthetic-server".into(),
        principal_id: "synthetic-principal".into(),
    };
    let binding = f
        .native
        .receipt
        .binding(parse_owner_reference(&f.native.owner).unwrap(), 7)
        .unwrap();
    f.native
        .append(&Record::Managed(ManagedBinding {
            binding: binding.clone(),
            managed: managed.clone(),
        }))
        .unwrap();
    assert_ne!(binding.startup_installation_ref, binding.fleet_creation_id);
    assert_ne!(managed.installation_id, binding.fleet_creation_id);
    assert_eq!(
        std::fs::read(f.native.evidence_path(&EvidenceKey::Provisioning)).unwrap(),
        original
    );
    let mut changed = managed;
    changed.installation_id = "d".repeat(32);
    changed.actor = format!("desktop-{}", changed.installation_id);
    assert!(f
        .native
        .append(&Record::Managed(ManagedBinding {
            binding,
            managed: changed
        }))
        .is_err());
    let mut future: serde_json::Value = serde_json::from_slice(&original).unwrap();
    future["minimum_reader_version"] = 2.into();
    std::fs::write(
        f.native.evidence_path(&EvidenceKey::Provisioning),
        serde_json::to_vec(&future).unwrap(),
    )
    .unwrap();
    assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
}
#[test]
fn session_change_allows_new_review_but_not_old_effect() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    f.native.session.set(8);
    let resolution = f.native.resolution().unwrap();
    let login = LoginIdentity {
        owner_sid: f.native.owner.owner_sid.clone(),
        session_id: 8,
    };
    assert!(resolution
        .require_effect_session(&login, &f.reference(&plan))
        .is_err());
    let old_login = LoginIdentity {
        owner_sid: f.native.owner.owner_sid.clone(),
        session_id: 7,
    };
    assert!(resolution
        .require_effect_session(&old_login, &f.reference(&plan))
        .is_err());
    f.native.session.set(7);
    f.publish(&plan);
    f.native.session.set(8);
    f.refresh();
    f.input.label = "New session".into();
    let next = f.plan();
    f.admit(&next);
    assert!(f
        .native
        .resolution()
        .unwrap()
        .require_effect_session(&login, &f.reference(&next))
        .is_ok());
}
#[test]
fn guarded_reads_reject_links_inaccessible_and_changed_evidence() {
    let mut f = Fixture::new();
    f.native.unavailable.set(Some(Artifact::Inventory));
    assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
    f.native.unavailable.set(None);
    let path = f.path(Artifact::Inventory);
    std::fs::create_dir(&path).unwrap();
    assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
    std::fs::remove_dir(&path).unwrap();
    f.native.context.set(false);
    assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
    f.native.context.set(true);
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(f.root.join("missing"), &path).unwrap();
        assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
        std::fs::remove_file(&path).unwrap();
    }
    f.native.drift.set(true);
    assert_eq!(f.ownership(Artifact::Inventory), FileOwnership::Unknown);
    let _guards = f.native.guards();
    let captured = capture(&f.native, &f.native.owner).unwrap();
    let resolution = captured.resolve(&f.native.histories(), &[]).unwrap();
    assert_eq!(
        resolution.ownership(
            &f.native.owner,
            Artifact::Inventory,
            &path,
            Some(b"caller-stale")
        ),
        FileOwnership::Unknown
    );
}
#[test]
fn authorization_expiry_never_completes_remaining_artifacts() {
    let mut f = Fixture::new();
    let plan = f.plan();
    f.admit(&plan);
    let result = f.native.publisher().publish_authorized(
        &plan,
        &mut f.native,
        &mut |_, a| {
            if a == Artifact::ProfileIndex {
                Err("NATIVE_EFFECT_EXPIRED")
            } else {
                Ok(())
            }
        },
        &mut |_, _| Ok(()),
    );
    assert_eq!(
        result,
        Err(Error::Authorization {
            code: "NATIVE_EFFECT_EXPIRED"
        })
    );
    assert!(f.path(Artifact::Inventory).exists());
    assert!(!f.path(Artifact::ProfileIndex).exists());
    abandon(&mut f, &plan);
    assert!(owned(f.ownership(Artifact::Inventory)));
    assert!(!f.path(Artifact::ProfileIndex).exists());
    assert!(f
        .native
        .resolution()
        .unwrap()
        .require_effect_session(
            &LoginIdentity {
                owner_sid: f.native.owner.owner_sid.clone(),
                session_id: 7
            },
            &f.reference(&plan)
        )
        .is_err());
    let mut f = Fixture::unreserved();
    let receipt = f.native.receipt.clone();
    let _guards = f.native.guards();
    assert!(reserve(&mut f.native, &receipt, &mut || Err("EXPIRED")).is_err());
    assert!(!f.native.evidence_path(&EvidenceKey::Provisioning).exists());
}

#[test]
fn formatted_reference_passes_generator_and_colon_reference_does_not() {
    let mut input = support::generator::setup();
    let hash = digest(b"synthetic native provisioning receipt");
    input.owner.creation_receipt = format_reference(&input.owner.installation_id, &hash).unwrap();
    assert_eq!(input.owner.creation_receipt.len(), 115);
    assert_eq!(parse_owner_reference(&input.owner), Ok(hash.as_str()));
    assert!(bat_fleet_core::connection_setup::build(&input).is_ok());
    input.owner.creation_receipt =
        format!("fleet-creation:v1:{}:{hash}", input.owner.installation_id);
    assert_eq!(
        bat_fleet_core::connection_setup::build(&input),
        Err(bat_fleet_core::connection_setup::Gap::UnknownOwner)
    );
    assert!(parse_owner_reference(&input.owner).is_err());
    for value in [
        format!("fleet-creation.v2.{}.{hash}", input.owner.installation_id),
        format!("Fleet-creation.v1.{}.{hash}", input.owner.installation_id),
        format!("fleet-creation_v1.{}.{hash}", input.owner.installation_id),
        format!(
            "fleet-creation.v1.{}.{}",
            input.owner.installation_id,
            hash.to_uppercase()
        ),
        format!(
            "fleet-creation.v1.{}.{hash}.extra",
            input.owner.installation_id
        ),
    ] {
        input.owner.creation_receipt = value;
        assert!(parse_owner_reference(&input.owner).is_err());
    }
    assert!(format_reference(&"a".repeat(31), &hash).is_err());
}

#[test]
fn directory_records_bind_reserved_path_and_exact_parent_identity() {
    let mut f = Fixture::new();
    let binding = f
        .native
        .receipt
        .binding(parse_owner_reference(&f.native.owner).unwrap(), 7)
        .unwrap();
    let mut record = DirectoryCreated {
        binding,
        canonical_path: f.root.join("native/fleet-creation"),
        parent_identity: ObjectIdentity {
            volume: 99,
            file_id: "0".repeat(32),
        },
        identity: ObjectIdentity {
            volume: 9,
            file_id: "8".repeat(32),
        },
    };
    assert!(f.native.append(&Record::Directory(record.clone())).is_err());
    record.parent_identity = f.native.receipt.scope.state_identity.clone();
    f.native.append(&Record::Directory(record.clone())).unwrap();
    let mut child = record.clone();
    child.canonical_path = record.canonical_path.join("directories");
    child.parent_identity = record.identity.clone();
    child.identity.file_id = "7".repeat(32);
    f.native.append(&Record::Directory(child.clone())).unwrap();
    child.canonical_path = record.canonical_path.join("arbitrary");
    assert!(f.native.append(&Record::Directory(child)).is_err());
    record.canonical_path = f.root.join("unreserved");
    assert!(f.native.append(&Record::Directory(record)).is_err());
}

#[test]
fn history_views_use_original_validators_without_writes() {
    use bat_fleet_core::connection_publish::FrozenHistory;
    #[derive(Debug, PartialEq, Eq)]
    struct SnapshotEntry {
        modified: std::time::SystemTime,
        sha256: Option<String>,
        children: Option<Vec<std::ffi::OsString>>,
    }
    fn snapshot(
        path: &std::path::Path,
    ) -> std::collections::BTreeMap<std::path::PathBuf, SnapshotEntry> {
        // Windows directory enumeration can return stale NTFS timestamps.
        // Query the opened object instead, retaining exact mtime comparison.
        let mut options = std::fs::OpenOptions::new();
        options.read(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            use windows_sys::Win32::Storage::FileSystem::{
                FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
            };
            options.custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let mut file = options.open(path).unwrap();
        let metadata = file.metadata().unwrap();
        assert!(!metadata.file_type().is_symlink());
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            assert_eq!(metadata.file_attributes() & 0x400, 0);
        }
        let mut result = std::collections::BTreeMap::new();
        if metadata.is_dir() {
            let mut children = std::fs::read_dir(path)
                .unwrap()
                .map(|entry| entry.unwrap().file_name())
                .collect::<Vec<_>>();
            children.sort();
            for child in &children {
                result.extend(snapshot(&path.join(child)));
            }
            result.insert(
                path.to_path_buf(),
                SnapshotEntry {
                    modified: metadata.modified().unwrap(),
                    sha256: None,
                    children: Some(children),
                },
            );
        } else {
            assert!(metadata.is_file());
            use std::io::Read;
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).unwrap();
            result.insert(
                path.to_path_buf(),
                SnapshotEntry {
                    modified: metadata.modified().unwrap(),
                    sha256: Some(digest(&bytes)),
                    children: None,
                },
            );
        }
        result
    }
    let mut f = Fixture::new();
    let plan = f.plan();
    let (link, _) = f.admit(&plan);
    interrupt(&mut f, &plan, Boundary::AfterIntended, Artifact::Inventory);
    abandon(&mut f, &plan);
    let before = snapshot(&f.root);
    {
        let _guards = f.native.guards();
        let history = f.native.try_history(&link.frozen).unwrap();
        assert_eq!(history.frozen().plan(), &plan);
        assert_eq!(history.reached(Artifact::Inventory), Some(false));
        assert_eq!(history.reached(Artifact::ProfileIndex), None);
        assert!(!history.complete());
        assert!(history.journal_sha256().is_some());
        assert_eq!(history.decision().unwrap().1.len(), 4);
    }
    assert_eq!(snapshot(&f.root), before);
    let frozen = std::fs::read(f.native.frozen_path(&link.frozen)).unwrap();
    let mut oversized = frozen.clone();
    oversized.resize(
        bat_fleet_core::connection_publish::FROZEN_PLAN_LIMIT + 1,
        b' ',
    );
    let mut oversized_reference = link.frozen.clone();
    oversized_reference.sha256 = digest(&oversized);
    assert!(FrozenHistory::decode(&oversized_reference, &oversized).is_err());
    let mut invalid: serde_json::Value = serde_json::from_slice(&frozen).unwrap();
    invalid["version"] = 2.into();
    let bytes = serde_json::to_vec(&invalid).unwrap();
    let mut reference = link.frozen.clone();
    reference.sha256 = digest(&bytes);
    assert!(FrozenHistory::decode(&reference, &bytes).is_err());
    let journal = f.root.join(format!(
        "native/fleet-publications/{}.json",
        plan.review_digest
    ));
    let original = std::fs::read(&journal).unwrap();
    let mut value: serde_json::Value = serde_json::from_slice(&original).unwrap();
    value["unknown"] = true.into();
    std::fs::write(&journal, serde_json::to_vec(&value).unwrap()).unwrap();
    {
        let _guards = f.native.guards();
        assert!(f.native.try_history(&link.frozen).is_err());
    }
    std::fs::write(&journal, original).unwrap();
    let decision = std::fs::read_dir(f.root.join(format!(
        "native/fleet-publication-decisions/{}",
        plan.review_digest
    )))
    .unwrap()
    .next()
    .unwrap()
    .unwrap()
    .path();
    let mut bytes = std::fs::read(&decision).unwrap();
    bytes.push(b' ');
    std::fs::write(decision, bytes).unwrap();
    {
        let _guards = f.native.guards();
        assert!(f.native.try_history(&link.frozen).is_err());
    }
}

#[test]
fn auxiliary_records_are_strict_bounded_and_cannot_rebind_ids() {
    let mut f = Fixture::new();
    let plan = f.plan();
    let link = f.prepare(&plan);
    let managed = ManagedIdentity {
        installation_id: "c".repeat(32),
        manifest_sha256: digest(b"manifest"),
        actor: format!("desktop-{}", "c".repeat(32)),
        server_id: "server".into(),
        principal_id: "principal".into(),
    };
    let records = [
        Record::Publication(link.clone()),
        Record::Closure(AdmissionClosure {
            binding: link.binding.clone(),
            link_sha256: digest(b"link"),
            reason: CloseReason::AbandonedBeforeEffect,
            decision_reference: "decision".into(),
            baseline: link.baseline.clone(),
            journal_absent: true,
        }),
        Record::Directory(DirectoryCreated {
            binding: link.binding.clone(),
            canonical_path: f.root.join("native/fleet-creation"),
            parent_identity: f.native.receipt.scope.state_identity.clone(),
            identity: ObjectIdentity {
                volume: 9,
                file_id: "e".repeat(32),
            },
        }),
        Record::Managed(ManagedBinding {
            binding: link.binding.clone(),
            managed,
        }),
        Record::MigrationIntent(MigrationIntent {
            binding: link.binding.clone(),
            predecessor_sha256: digest(b"predecessor"),
            predecessor_terminal: Terminal {
                journal_sha256: Some(digest(b"journal")),
                decision_sha256: None,
                closure_sha256: None,
                migration_sha256: None,
            },
            migration_id: "d".repeat(32),
            startup_path: f.root.join("Startup/Open BAT.lnk"),
            review_fingerprint: digest(b"review"),
            before_sha256: digest(b"pointer"),
            restores: None,
            acceptance_reference: "native-migration".into(),
        }),
        Record::MigrationCompletion(MigrationCompletion {
            binding: link.binding.clone(),
            intent_sha256: digest(b"intent"),
            saved_sha256: digest(b"saved"),
            after_sha256: digest(b"after"),
        }),
    ];
    for record in records {
        let bytes = record.encode().unwrap();
        assert_eq!(Record::decode(&record.key(), &bytes).unwrap(), record);
        let mut over = bytes.clone();
        over.resize(RECORD_LIMIT + 1, b' ');
        assert!(Record::decode(&record.key(), &over).is_err());
        let mut value: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        value["record"]["body"]["unknown"] = true.into();
        assert!(Record::decode(&record.key(), &serde_json::to_vec(&value).unwrap()).is_err());
        let mut value: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        value["record"]["body"]["binding"]["fleet_creation_id"] = "a".repeat(32).into();
        let foreign = Record::decode(&record.key(), &serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(f.native.append(&foreign).is_err());
    }
    let mut over = link;
    over.acceptance_reference = "a".repeat(257);
    assert!(Record::Publication(over).encode().is_err());
}

#[test]
fn migration_completion_survives_session_change_with_launched_monitor() {
    use bat_fleet_core::{
        discovery::Backend,
        migration::{self, Platform},
    };
    let mut f = Fixture::new();
    let plan = f.plan();
    let (link, _) = f.admit(&plan);
    f.publish(&plan);
    let resolution = f.native.resolution().unwrap();
    let before = std::fs::read(f.path(Artifact::Installation)).unwrap();
    let store =
        migration::Store::new(&f.path(Artifact::Installation), &f.root.join("Startup")).unwrap();
    let mut producer = support::MigrationNative {
        native: f.native.clone(),
    };
    let preview = store.preview(&mut producer, Backend::Rust).unwrap();
    let intent = MigrationIntent {
        binding: f
            .native
            .receipt
            .binding(parse_owner_reference(&f.native.owner).unwrap(), 7)
            .unwrap(),
        predecessor_sha256: resolution.head().into(),
        predecessor_terminal: resolution.terminal().unwrap().clone(),
        migration_id: "9".repeat(32),
        startup_path: f.root.join("Startup/Open BAT.lnk"),
        review_fingerprint: preview.fingerprint().into(),
        before_sha256: digest(&before),
        restores: None,
        acceptance_reference: "native-migration-review".into(),
    };
    let intent_sha256 = f
        .native
        .append(&Record::MigrationIntent(intent.clone()))
        .unwrap();
    let after = serde_json::to_vec(
        &serde_json::json!({"kit_root": f.input.root, "backend": Backend::Powershell}),
    )
    .unwrap();
    store
        .begin(
            &mut producer,
            &intent.migration_id,
            &preview,
            Backend::Powershell,
            &after,
            false,
        )
        .unwrap();
    assert_eq!(
        store.advance(&mut producer, &intent.migration_id),
        Ok(migration::Progress::WaitingLaunch)
    );
    assert_eq!(
        store.advance(&mut producer, &intent.migration_id),
        Ok(migration::Progress::Complete)
    );
    let completion = Record::MigrationCompletion(MigrationCompletion {
        binding: intent.binding,
        intent_sha256,
        saved_sha256: store
            .read_history(&intent.migration_id)
            .unwrap()
            .sha256()
            .into(),
        after_sha256: digest(&after),
    });
    f.native.session.set(8);
    let _launcher = migration::Platform::launcher_guard(&mut f.native).unwrap();
    assert!(producer.discover().unwrap().is_some());
    let history = f
        .native
        .try_history(&link.frozen)
        .expect("historical views while Monitor runs");
    append(
        &mut f.native,
        &f.input.owner,
        &completion,
        &[history],
        &[store.observe_history(&intent.migration_id).unwrap()],
        &mut || Ok(()),
    )
    .unwrap();
}

fn terminate_migration(case: &mut support::MigrationCase, outcome: MigrationOutcome) {
    let saved = case
        .store
        .observe_history(&case.intent.migration_id)
        .unwrap();
    let termination = Record::MigrationTermination(MigrationTermination {
        binding: case.intent.binding.clone(),
        intent_sha256: case.intent_hash.clone(),
        saved_sha256: saved.saved().map(|s| s.sha256().to_owned()),
        outcome,
        decision_reference: "native-abandon-migration".into(),
    });
    case.fixture.native.session.set(8);
    case.fixture.native.append(&termination).unwrap();
    case.assert_only_installation_unknown();
    assert!(case.fixture.native.resolution().unwrap().settled());
    assert!(case
        .fixture
        .native
        .resolution()
        .unwrap()
        .baseline()
        .is_err());
}
#[test]
fn migration_refused_begin_has_a_terminal_without_saved_bytes() {
    use bat_fleet_core::discovery::Backend;
    let mut case = support::MigrationCase::new();
    case.fixture.native.session.set(8);
    assert!(case
        .store
        .begin(
            &mut case.producer,
            &case.intent.migration_id,
            &case.preview,
            Backend::Powershell,
            &case.after,
            false
        )
        .is_err());
    assert!(case
        .store
        .observe_history(&case.intent.migration_id)
        .unwrap()
        .saved()
        .is_none());
    terminate_migration(&mut case, MigrationOutcome::Refused);
}
#[test]
fn migration_abandoned_before_effect_has_an_exact_saved_terminal() {
    let mut case = support::MigrationCase::new();
    case.begin();
    terminate_migration(&mut case, MigrationOutcome::Abandoned);
}
#[test]
fn migration_unconfirmed_launch_has_an_exact_saved_terminal() {
    use bat_fleet_core::migration;
    let mut case = support::MigrationCase::new();
    case.begin();
    assert_eq!(
        case.store
            .advance(&mut case.producer, &case.intent.migration_id),
        Ok(migration::Progress::WaitingLaunch)
    );
    *case.fixture.native.running.borrow_mut() = None;
    assert_eq!(
        case.store
            .advance(&mut case.producer, &case.intent.migration_id),
        Err("MIGRATION_LAUNCH_UNKNOWN")
    );
    terminate_migration(&mut case, MigrationOutcome::Abandoned);
}
#[test]
fn migration_restored_before_complete_has_a_terminal_bound_to_reverse_bytes() {
    use bat_fleet_core::migration;
    let mut case = support::MigrationCase::new();
    case.begin();
    assert_eq!(
        case.store
            .advance(&mut case.producer, &case.intent.migration_id),
        Ok(migration::Progress::WaitingLaunch)
    );
    *case.fixture.native.running.borrow_mut() = None;
    let id = "8".repeat(32);
    case.store
        .restore(&mut case.producer, &case.intent.migration_id, &id)
        .unwrap();
    assert_eq!(
        case.store.advance(&mut case.producer, &id),
        Ok(migration::Progress::WaitingLaunch)
    );
    assert_eq!(
        case.store.advance(&mut case.producer, &id),
        Ok(migration::Progress::Complete)
    );
    let saved_sha256 = case.store.read_history(&id).unwrap().sha256().into();
    terminate_migration(
        &mut case,
        MigrationOutcome::Restored {
            migration_id: id,
            saved_sha256,
        },
    );
}

#[path = "creation_evidence/negative.rs"]
mod negative;

#[path = "creation_evidence/terminations.rs"]
mod terminations;
