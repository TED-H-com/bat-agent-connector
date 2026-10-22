use super::*;
use bat_fleet_core::connection_publish::{
    AbandonDecision, ArtifactState, AuthorizationPoint, DecisionBoundary, FROZEN_PLAN_LIMIT,
};

fn capture(f: &mut Fixture) {
    for a in [
        Artifact::Inventory,
        Artifact::ProfileIndex,
        Artifact::SshReference,
        Artifact::Installation,
    ] {
        if let Ok(bytes) = std::fs::read(f.path(a)) {
            let sha256 = digest(&bytes);
            f.input.existing.insert(
                a,
                ExistingFile {
                    bytes,
                    sha256: sha256.clone(),
                    ownership: FileOwnership::Owned {
                        owner: f.input.owner.clone(),
                        artifact: a,
                        sha256,
                    },
                },
            );
        }
    }
}
fn partial(f: &mut Fixture, plan: &PublicationPlan) {
    assert_eq!(
        f.publisher().publish(plan, &mut f.native, &mut |b, a| {
            if b == Boundary::AfterReadBack && a == Artifact::Inventory {
                Err("CRASH")
            } else {
                Ok(())
            }
        }),
        Err(Error::Gate("CRASH"))
    );
}
fn decision() -> AbandonDecision {
    AbandonDecision {
        reference: "opaque-operator-decision".into(),
        recorded_at_unix_ms: 1_800_000_000_000,
    }
}
fn snapshots(
    f: &Fixture,
    plan: &PublicationPlan,
) -> Vec<(Option<Vec<u8>>, Option<std::time::SystemTime>)> {
    plan.outputs
        .iter()
        .map(|o| {
            let path = f.path(o.artifact);
            (
                std::fs::read(&path).ok(),
                std::fs::symlink_metadata(path)
                    .ok()
                    .and_then(|m| m.modified().ok()),
            )
        })
        .collect()
}
fn decisions(f: &Fixture, plan: &PublicationPlan) -> PathBuf {
    f.root
        .join("native/fleet-publication-decisions")
        .join(&plan.review_digest)
}

#[test]
fn authorization_refuses_directory_artifact_and_both_replacement_guards() {
    for stop in 0..4 {
        for (point, occurrence) in [
            (AuthorizationPoint::BeforeDirectory, 1),
            (AuthorizationPoint::BeforeArtifact, 1),
            (AuthorizationPoint::ReplacementGuard, 1),
            (AuthorizationPoint::ReplacementGuard, 2),
        ] {
            let mut f = Fixture::new();
            let plan = build(&f.input).unwrap();
            if stop == 0 {
                std::fs::remove_dir_all(f.input.root.join("client")).unwrap();
            }
            if stop == 1 {
                std::fs::remove_dir(f.input.root.join("client/bat-profiles")).unwrap();
            }
            let launcher = f.native.launcher.clone();
            let monitor = f.native.monitor.clone();
            let target = f.path(plan.steps[stop].artifact);
            let mut seen = 0;
            let mut writes = Vec::new();
            let result = f.publisher().publish_authorized(
                &plan,
                &mut f.native,
                &mut |at, a| {
                    assert!(launcher.get() && monitor.get());
                    if at == point && a == plan.steps[stop].artifact {
                        seen += 1;
                        if seen == occurrence {
                            if point == AuthorizationPoint::ReplacementGuard && occurrence == 2 {
                                assert!(std::fs::read_dir(target.parent().unwrap()).unwrap().any(
                                    |entry| {
                                        entry
                                            .unwrap()
                                            .file_name()
                                            .to_string_lossy()
                                            .starts_with(".fleet-publication-")
                                    }
                                ));
                            }
                            return Err("AUTHORITY_EXPIRED");
                        }
                    }
                    Ok(())
                },
                &mut |b, a| {
                    if b == Boundary::AfterReplace {
                        writes.push(a);
                    }
                    Ok(())
                },
            );
            assert_eq!(
                result,
                Err(Error::Authorization {
                    code: "AUTHORITY_EXPIRED"
                })
            );
            assert_eq!(
                writes,
                plan.steps[..stop]
                    .iter()
                    .map(|s| s.artifact)
                    .collect::<Vec<_>>()
            );
            for (i, out) in plan.outputs.iter().enumerate() {
                assert_eq!(
                    std::fs::read(f.path(out.artifact)).ok(),
                    (i < stop).then(|| out.bytes.clone())
                );
            }
            if point == AuthorizationPoint::BeforeDirectory && stop < 2 {
                assert!(!f.path(plan.steps[stop].artifact).parent().unwrap().exists());
            }
            let before = snapshots(&f, &plan);
            let inspection = f.publisher().inspect(&plan, &mut f.native).unwrap();
            assert_eq!(snapshots(&f, &plan), before);
            assert_eq!(
                inspection
                    .artifacts
                    .iter()
                    .filter(|o| o.state == ArtifactState::Published)
                    .count(),
                stop
            );
        }
    }
}

#[test]
fn authority_lapsing_after_each_kit_file_leaves_only_that_prefix() {
    for count in 1..=3 {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        let written = Cell::new(0);
        assert_eq!(
            f.publisher().publish_authorized(
                &plan,
                &mut f.native,
                &mut |_, _| if written.get() >= count {
                    Err("FENCE_WITHDRAWN")
                } else {
                    Ok(())
                },
                &mut |b, _| {
                    if b == Boundary::AfterReplace {
                        written.set(written.get() + 1);
                    }
                    Ok(())
                }
            ),
            Err(Error::Authorization {
                code: "FENCE_WITHDRAWN"
            })
        );
        assert_eq!(written.get(), count);
        let receipts: Value =
            serde_json::from_slice(&std::fs::read(f.journal(&plan)).unwrap()).unwrap();
        for receipt in &receipts["receipts"].as_array().unwrap()[..count] {
            assert_eq!(
                receipt["state"], "published",
                "evidence may finish after expiry"
            );
        }
        for (i, out) in plan.outputs.iter().enumerate() {
            assert_eq!(
                std::fs::read(f.path(out.artifact)).ok(),
                (i < count).then(|| out.bytes.clone())
            );
        }
    }
}

#[test]
fn expiry_after_pointer_allows_final_evidence_without_more_effects() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    let expired = Cell::new(false);
    f.publisher()
        .publish_authorized(
            &plan,
            &mut f.native,
            &mut |_, _| {
                if expired.get() {
                    Err("EXPIRED")
                } else {
                    Ok(())
                }
            },
            &mut |b, _| {
                if b == Boundary::AfterPointer {
                    expired.set(true);
                }
                Ok(())
            },
        )
        .unwrap();
    assert!(expired.get());
    f.finished(&plan);
}

#[test]
fn inspect_reports_all_five_states_without_artifact_or_journal_writes() {
    for state in [
        ArtifactState::Published,
        ArtifactState::Before,
        ArtifactState::ThirdBytes,
        ArtifactState::Absent,
        ArtifactState::Unreadable,
    ] {
        let mut f = Fixture::new();
        let baseline = build(&f.input).unwrap();
        f.publish(&baseline).unwrap();
        capture(&mut f);
        f.input.label = "Changed review".into();
        let plan = build(&f.input).unwrap();
        let path = f.path(Artifact::Inventory);
        match state {
            ArtifactState::Published => {
                std::fs::write(&path, &plan.output(Artifact::Inventory).bytes).unwrap()
            }
            ArtifactState::Before => (),
            ArtifactState::ThirdBytes => std::fs::write(&path, b"human third bytes").unwrap(),
            ArtifactState::Absent => std::fs::remove_file(&path).unwrap(),
            ArtifactState::Unreadable => {
                std::fs::remove_file(&path).unwrap();
                std::fs::create_dir(&path).unwrap();
            }
        }
        let old_journal = std::fs::read(f.journal(&baseline)).unwrap();
        let before = snapshots(&f, &plan);
        let inspection = f.publisher().inspect(&plan, &mut f.native).unwrap();
        assert_eq!(inspection.artifacts[0].state, state);
        assert_eq!(
            inspection.configuration_valid,
            state == ArtifactState::Published
        );
        assert_eq!(snapshots(&f, &plan), before);
        assert_eq!(std::fs::read(f.journal(&baseline)).unwrap(), old_journal);
        assert!(!f.journal(&plan).exists());
    }
}

#[test]
fn inspect_uses_readback_when_the_real_journal_lags() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    partial(&mut f, &plan);
    let journal = std::fs::read(f.journal(&plan)).unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&journal).unwrap()["receipts"][0]["state"],
        "intended"
    );
    let before = snapshots(&f, &plan);
    let inspected = f.publisher().inspect(&plan, &mut f.native).unwrap();
    assert_eq!(inspected.artifacts[0].state, ArtifactState::Published);
    assert!(inspected.artifacts[1..]
        .iter()
        .all(|o| o.state == ArtifactState::Absent));
    assert_eq!(snapshots(&f, &plan), before);
    assert_eq!(std::fs::read(f.journal(&plan)).unwrap(), journal);
}

#[test]
fn abandonment_crashes_preserve_third_bytes_and_the_exact_journal() {
    for stop in [
        DecisionBoundary::BeforeRecord,
        DecisionBoundary::AfterRecord,
    ] {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        partial(&mut f, &plan);
        std::fs::write(f.path(Artifact::ProfileIndex), b"human third bytes").unwrap();
        let old_journal = std::fs::read(f.journal(&plan)).unwrap();
        let before = snapshots(&f, &plan);
        assert_eq!(
            f.publisher()
                .abandon_remaining(&plan, &decision(), &mut f.native, &mut |b| {
                    if b == stop {
                        Err("CRASH")
                    } else {
                        Ok(())
                    }
                }),
            Err(Error::Gate("CRASH"))
        );
        let files = || {
            std::fs::read_dir(decisions(&f, &plan))
                .map(|e| e.count())
                .unwrap_or(0)
        };
        assert_eq!(files(), usize::from(stop == DecisionBoundary::AfterRecord));
        let saved = f
            .publisher()
            .abandon_remaining(&plan, &decision(), &mut f.native, &mut |_| Ok(()))
            .unwrap();
        let bytes = std::fs::read(&saved).unwrap();
        let record: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(record["journal_digest"], digest(&old_journal));
        assert_eq!(record["review_digest"], plan.review_digest);
        assert_eq!(record["decision"]["reference"], decision().reference);
        assert_eq!(
            record["decision"]["recorded_at_unix_ms"],
            decision().recorded_at_unix_ms
        );
        assert_eq!(record["observations"].as_array().unwrap().len(), 4);
        assert_eq!(
            f.publisher()
                .abandon_remaining(&plan, &decision(), &mut f.native, &mut |_| panic!(
                    "immutable replay"
                ))
                .unwrap(),
            saved
        );
        assert_eq!(std::fs::read(saved).unwrap(), bytes);
        assert_eq!(snapshots(&f, &plan), before);
        assert_eq!(std::fs::read(f.journal(&plan)).unwrap(), old_journal);
        assert_eq!(
            f.publish(&plan),
            Err(Error::AbandonedPlan {
                review_digest: plan.review_digest.clone()
            })
        );
    }
}

#[test]
fn a_new_review_needs_a_valid_abandonment_and_still_proves_ownership() {
    let mut f = Fixture::new();
    let original = build(&f.input).unwrap();
    partial(&mut f, &original);
    capture(&mut f);
    f.input.label = "New review".into();
    let next = build(&f.input).unwrap();
    assert_eq!(
        f.publish(&next),
        Err(Error::UnfinishedPlan {
            review_digest: original.review_digest.clone()
        })
    );
    let journal = std::fs::read(f.journal(&original)).unwrap();
    f.publisher()
        .abandon_remaining(&original, &decision(), &mut f.native, &mut |_| Ok(()))
        .unwrap();
    f.native.refused = Some((Artifact::Inventory, FileOwnership::Unknown));
    assert_eq!(
        f.publish(&next),
        Err(Error::Ownership {
            artifact: Artifact::Inventory
        })
    );
    f.native.refused = None;
    f.publish(&next).unwrap();
    f.finished(&next);
    assert_eq!(std::fs::read(f.journal(&original)).unwrap(), journal);
}

#[test]
fn changed_journal_or_tampered_decision_never_settles_a_plan() {
    for tamper in [
        "content",
        "scope",
        "journal_digest",
        "review_digest",
        "journal",
        "name",
    ] {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        partial(&mut f, &plan);
        let path = f
            .publisher()
            .abandon_remaining(&plan, &decision(), &mut f.native, &mut |_| Ok(()))
            .unwrap();
        let mut record: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        match tamper {
            "journal" => {
                let mut bytes = std::fs::read(f.journal(&plan)).unwrap();
                bytes.push(b' ');
                std::fs::write(f.journal(&plan), bytes).unwrap();
            }
            "name" => std::fs::rename(
                &path,
                path.with_file_name(format!("abandon-{}.json", digest(b"wrong"))),
            )
            .unwrap(),
            _ => {
                match tamper {
                    "scope" => record["scope"]["pointer"] = json!(f.root.join("other/fleet.json")),
                    "journal_digest" => record["journal_digest"] = json!(digest(b"wrong")),
                    "review_digest" => record["review_digest"] = json!(digest(b"wrong")),
                    _ => record["decision"]["reference"] = json!("tampered"),
                }
                let bytes = serde_json::to_vec(&record).unwrap();
                std::fs::write(&path, &bytes).unwrap();
                if tamper != "content" {
                    std::fs::rename(
                        &path,
                        path.with_file_name(format!("abandon-{}.json", digest(&bytes))),
                    )
                    .unwrap();
                }
            }
        }
        let journal = std::fs::read(f.journal(&plan)).unwrap();
        let before = snapshots(&f, &plan);
        capture(&mut f);
        f.input.label = "New review".into();
        let next = build(&f.input).unwrap();
        assert_eq!(f.publish(&next), Err(Error::DecisionConflict), "{tamper}");
        assert!(f
            .publisher()
            .abandon_remaining(&plan, &decision(), &mut f.native, &mut |_| Ok(()))
            .is_err());
        assert_eq!(std::fs::read(f.journal(&plan)).unwrap(), journal);
        assert_eq!(snapshots(&f, &plan), before);
    }
}

#[test]
fn abandonment_rechecks_journal_after_the_before_record_boundary() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    partial(&mut f, &plan);
    let journal = f.journal(&plan);
    let original = std::fs::read(&journal).unwrap();
    let mut changed = original.clone();
    changed.push(b' ');
    assert_eq!(
        f.publisher()
            .abandon_remaining(&plan, &decision(), &mut f.native, &mut |b| {
                if b == DecisionBoundary::BeforeRecord {
                    std::fs::write(&journal, &changed).unwrap();
                }
                Ok(())
            }),
        Err(Error::DecisionConflict)
    );
    assert_eq!(std::fs::read(journal).unwrap(), changed);
    assert_eq!(std::fs::read_dir(decisions(&f, &plan)).unwrap().count(), 0);
}

#[test]
fn frozen_plan_reloads_original_inputs_even_after_partial_publication() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    let frozen = f.publisher().export_plan(&plan, &mut f.native).unwrap();
    let owner = f.input.owner.clone();
    assert_eq!(
        f.publisher()
            .load_plan(&frozen, &owner, &mut f.native)
            .unwrap(),
        plan
    );
    partial(&mut f, &plan);
    capture(&mut f);
    assert_ne!(build(&f.input).unwrap().review_digest, plan.review_digest);
    let recovered = f
        .publisher()
        .load_plan(&frozen, &owner, &mut f.native)
        .unwrap();
    assert_eq!(recovered, plan);
    f.publisher()
        .publish_authorized(
            &recovered,
            &mut f.native,
            &mut |_, a| {
                assert_ne!(a, Artifact::Inventory);
                Ok(())
            },
            &mut |b, a| {
                if b == Boundary::AfterReplace {
                    assert_ne!(a, Artifact::Inventory);
                }
                Ok(())
            },
        )
        .unwrap();
    f.finished(&plan);
}

#[test]
fn frozen_plan_preserves_owned_before_bytes_and_is_create_only() {
    let mut f = Fixture::new();
    let first = build(&f.input).unwrap();
    f.publish(&first).unwrap();
    capture(&mut f);
    f.input.label = "Next owned review".into();
    f.input.preferences.bat_windows = true;
    let plan = build(&f.input).unwrap();
    let frozen = f.publisher().export_plan(&plan, &mut f.native).unwrap();
    let path = f
        .root
        .join("native/fleet-publication-plans")
        .join(format!("{}.json", frozen.review_digest));
    let before = std::fs::read(&path).unwrap();
    let modified = std::fs::metadata(&path).unwrap().modified().unwrap();
    let document: Value = serde_json::from_slice(&before).unwrap();
    assert_eq!(document["input"]["existing"].as_object().unwrap().len(), 4);
    assert_eq!(document["input"]["ssh_source"], json!(f.input.ssh_source));
    let recovered = f
        .publisher()
        .load_plan(&frozen, &f.input.owner, &mut f.native)
        .unwrap();
    assert_eq!(recovered, plan);
    assert_eq!(
        f.publisher().export_plan(&plan, &mut f.native).unwrap(),
        frozen
    );
    assert_eq!(
        std::fs::metadata(&path).unwrap().modified().unwrap(),
        modified
    );
    let mut changed = before.clone();
    changed.push(b' ');
    std::fs::write(&path, &changed).unwrap();
    assert_eq!(
        f.publisher()
            .load_plan(&frozen, &f.input.owner, &mut f.native),
        Err(Error::InvalidPlan)
    );
    assert_eq!(
        f.publisher().export_plan(&plan, &mut f.native),
        Err(Error::ReceiptConflict)
    );
    assert_eq!(std::fs::read(&path).unwrap(), changed);
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = std::fs::metadata(&path).unwrap();
        assert_eq!(metadata.mode() & 0o777, 0o600);
        assert_eq!(metadata.uid(), unsafe { libc::geteuid() });
        assert_eq!(
            std::fs::metadata(path.parent().unwrap()).unwrap().mode() & 0o777,
            0o700
        );
    }
}

#[test]
fn frozen_plan_tampering_version_missing_input_and_size_refuse() {
    for tamper in ["input", "version", "missing", "outputs", "size", "digest"] {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        let mut frozen = f.publisher().export_plan(&plan, &mut f.native).unwrap();
        let path = f
            .root
            .join("native/fleet-publication-plans")
            .join(format!("{}.json", plan.review_digest));
        let mut document: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        match tamper {
            "input" => document["input"]["label"] = json!("tampered"),
            "version" => document["generator_version"] = json!(999),
            "missing" => {
                document.as_object_mut().unwrap().remove("input");
            }
            "outputs" => document["outputs"][0]["bytes"] = json!([1, 2, 3]),
            "digest" => document["review_digest"] = json!(digest(b"wrong")),
            _ => (),
        }
        let bytes = serde_json::to_vec(&document).unwrap();
        std::fs::write(&path, &bytes).unwrap();
        // Even a caller providing the changed file hash cannot skip rebuilding.
        frozen.sha256 = digest(&bytes);
        if tamper == "size" {
            std::fs::OpenOptions::new()
                .write(true)
                .open(&path)
                .unwrap()
                .set_len(FROZEN_PLAN_LIMIT as u64 + 1)
                .unwrap();
        }
        assert!(
            f.publisher()
                .load_plan(&frozen, &f.input.owner, &mut f.native)
                .is_err(),
            "{tamper}"
        );
        assert!(plan.outputs.iter().all(|o| !f.path(o.artifact).exists()));
    }
}

#[cfg(unix)]
#[test]
fn recovery_storage_refuses_public_modes_and_linked_ancestors() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    for kind in ["plan", "decision", "linked-plan", "linked-decision"] {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        let frozen = f.publisher().export_plan(&plan, &mut f.native).unwrap();
        partial(&mut f, &plan);
        let saved = f
            .publisher()
            .abandon_remaining(&plan, &decision(), &mut f.native, &mut |_| Ok(()))
            .unwrap();
        let path = if kind.ends_with("plan") {
            f.root.join("native/fleet-publication-plans")
        } else {
            saved.parent().unwrap().to_owned()
        };
        if kind.starts_with("linked-") {
            let real = path.with_file_name("moved-state");
            std::fs::rename(&path, &real).unwrap();
            symlink(real, &path).unwrap();
        } else {
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        if kind.ends_with("plan") {
            assert!(f
                .publisher()
                .load_plan(&frozen, &f.input.owner, &mut f.native)
                .is_err());
        } else {
            assert!(f
                .publisher()
                .abandon_remaining(&plan, &decision(), &mut f.native, &mut |_| Ok(()))
                .is_err());
        }
    }
}

#[cfg(windows)]
#[test]
fn windows_abandonment_directories_have_protected_dacl() {
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::{
            Authorization::{GetNamedSecurityInfoW, SE_FILE_OBJECT},
            GetSecurityDescriptorControl, DACL_SECURITY_INFORMATION, SE_DACL_PROTECTED,
        },
    };
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    partial(&mut f, &plan);
    let path = f
        .publisher()
        .abandon_remaining(&plan, &decision(), &mut f.native, &mut |_| Ok(()))
        .unwrap();
    for path in [
        path.parent().unwrap(),
        path.parent().unwrap().parent().unwrap(),
        path.as_path(),
    ] {
        let name: Vec<_> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut descriptor = ptr::null_mut();
        assert_eq!(
            unsafe {
                GetNamedSecurityInfoW(
                    name.as_ptr(),
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    &mut descriptor,
                )
            },
            0
        );
        let mut control = 0;
        let mut revision = 0;
        assert_ne!(
            unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) },
            0
        );
        assert_ne!(control & SE_DACL_PROTECTED, 0);
        unsafe {
            LocalFree(descriptor);
        }
    }
}
