use bat_fleet_core::{
    configuration::{Configuration, Paths},
    connection_publish::{Boundary, Error, Platform, Publisher},
    connection_setup::{
        build, Artifact, ArtifactLocation, ExistingFile, FileOwnership, OwnerEvidence,
        PublicationPlan, ReviewedSetup,
    },
    digest,
    discovery::{Backend, MonitorIdentity},
    migration::{self, Owner},
    process_adapter::LoginIdentity,
    Result,
};
use serde_json::{json, Value};
use std::{
    cell::Cell,
    collections::BTreeMap,
    path::{Path, PathBuf},
    rc::Rc,
};
#[path = "support/connection_setup.rs"]
mod fixture;

#[path = "connection_publish/recovery.rs"]
mod recovery;

struct Guard(Rc<Cell<bool>>);
impl Drop for Guard {
    fn drop(&mut self) {
        self.0.set(false);
    }
}
struct Native {
    owner: OwnerEvidence,
    launcher: Rc<Cell<bool>>,
    monitor: Rc<Cell<bool>>,
    refused: Option<(Artifact, FileOwnership)>,
    require_monitor: bool,
    authority: Rc<Cell<bool>>,
}
impl Native {
    fn held(&self) {
        assert!(self.launcher.get() && (!self.require_monitor || self.monitor.get()));
    }
}
impl migration::Platform for Native {
    type LauncherGuard = Guard;
    type MonitorGuard = Guard;
    fn launcher_guard(&mut self) -> Result<Guard> {
        if self.launcher.replace(true) {
            return Err("LAUNCHER_BUSY");
        }
        Ok(Guard(self.launcher.clone()))
    }
    fn monitor_guard(&mut self) -> Result<Guard> {
        assert!(self.launcher.get());
        if self.monitor.replace(true) {
            return Err("MONITOR_ALREADY_RUNNING");
        }
        Ok(Guard(self.monitor.clone()))
    }
    fn login(&self) -> Result<LoginIdentity> {
        self.held();
        Ok(LoginIdentity {
            owner_sid: self.owner.owner_sid.clone(),
            session_id: 7,
        })
    }
    fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
        self.held();
        Ok(None)
    }
    fn request_quit(&mut self, _: &Owner) -> Result<()> {
        panic!("no process effects")
    }
    fn launch(&mut self, _: Backend) -> Result<()> {
        panic!("no process effects")
    }
    fn validate_config(&self, bytes: &[u8], backend: Backend) -> Result<()> {
        self.held();
        let doc: Value = serde_json::from_slice(bytes).map_err(|_| "INSTALLATION_INVALID")?;
        if doc["backend"] != serde_json::to_value(backend).unwrap() {
            return Err("INSTALLATION_INVALID");
        }
        Ok(())
    }
    fn classify_shortcut(&self, _: &[u8]) -> Result<Backend> {
        panic!("no startup effects")
    }
    fn shortcut(&self, _: Backend) -> Result<Vec<u8>> {
        panic!("no startup effects")
    }
}
impl Platform for Native {
    fn ownership(
        &self,
        owner: &OwnerEvidence,
        artifact: Artifact,
        _: &Path,
        current: Option<&[u8]>,
    ) -> Result<FileOwnership> {
        self.held();
        // Independent synthetic creation authority, not a path test.
        if owner != &self.owner || !self.authority.get() {
            return Ok(FileOwnership::Unknown);
        }
        if let Some((a, refused)) = &self.refused {
            if *a == artifact {
                return Ok(refused.clone());
            }
        }
        Ok(FileOwnership::Owned {
            owner: self.owner.clone(),
            artifact,
            sha256: digest(current.unwrap_or_default()),
        })
    }
    fn verify_private_state(&self, _: &Path, owner: &OwnerEvidence) -> Result<()> {
        self.held();
        assert_eq!(owner, &self.owner);
        Ok(())
    }
}
struct Fixture {
    root: PathBuf,
    input: ReviewedSetup,
    native: Native,
}
impl Fixture {
    fn new() -> Self {
        // macOS /var and caller-supplied TMPDIR may alias a real directory.
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("fleet-publish-{:032x}", rand::random::<u128>()));
        for relative in ["kit/client/bat-profiles", "native", "Startup", "user/.ssh"] {
            std::fs::create_dir_all(root.join(relative)).unwrap();
        }
        let mut input = fixture::setup();
        input.root = root.join("kit");
        std::fs::write(root.join("user/.ssh/config"), &input.ssh_source).unwrap();
        let native = Native {
            owner: input.owner.clone(),
            launcher: Rc::new(Cell::new(false)),
            monitor: Rc::new(Cell::new(false)),
            refused: None,
            require_monitor: true,
            authority: Rc::new(Cell::new(true)),
        };
        Self {
            root,
            input,
            native,
        }
    }
    fn publisher(&self) -> Publisher {
        Publisher::new(
            &self.root.join("native/fleet.json"),
            &self.root.join("Startup"),
            &self.root.join("user"),
        )
        .unwrap()
    }
    fn path(&self, a: Artifact) -> PathBuf {
        match a.location() {
            ArtifactLocation::KitRelative(p) => self.input.root.join(p),
            ArtifactLocation::InstallationPointer => self.root.join("native/fleet.json"),
        }
    }
    fn journal(&self, plan: &PublicationPlan) -> PathBuf {
        self.root
            .join("native/fleet-publications")
            .join(format!("{}.json", plan.review_digest))
    }
    fn publish(&mut self, plan: &PublicationPlan) -> std::result::Result<(), Error> {
        self.publisher()
            .publish(plan, &mut self.native, &mut |_, _| Ok(()))
    }
    fn finished(&self, plan: &PublicationPlan) {
        for output in &plan.outputs {
            assert_eq!(
                std::fs::read(self.path(output.artifact)).unwrap(),
                output.bytes
            );
        }
        let installation =
            bat_fleet_core::installation::Snapshot::load(&self.path(Artifact::Installation))
                .unwrap();
        let config = Configuration::load(
            Paths::new(
                installation.client_root(),
                &self.root.join("user"),
                None,
                None,
            )
            .unwrap(),
        )
        .unwrap();
        assert!(config.issues.is_empty());
        config.verify_current().unwrap();
        let journal: Value =
            serde_json::from_slice(&std::fs::read(self.journal(plan)).unwrap()).unwrap();
        let receipts = journal["receipts"].as_array().unwrap();
        assert_eq!(receipts.len(), plan.steps.len());
        for (receipt, step) in receipts.iter().zip(&plan.steps) {
            assert_eq!(receipt["version"], 1);
            assert_eq!(receipt["review_digest"], plan.review_digest);
            assert_eq!(receipt["generator_version"], plan.generator_version);
            assert_eq!(
                receipt["artifact"],
                serde_json::to_value(step.artifact).unwrap()
            );
            assert_eq!(receipt["before_digest"], json!(step.before_digest));
            assert_eq!(receipt["after_digest"], step.after_digest);
            assert_eq!(receipt["owner"], serde_json::to_value(&step.owner).unwrap());
            assert_eq!(receipt["state"], "published");
        }
        assert_eq!(
            std::fs::read(self.root.join("user/.ssh/config")).unwrap(),
            self.input.ssh_source
        );
        assert!(!self.input.root.join("fleet.json").exists());
        assert!(!self.native.launcher.get() && !self.native.monitor.get());
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

#[test]
fn clean_installation_publishes_in_order_pointer_last_with_private_receipts() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    let mut writes = vec![];
    f.publisher()
        .publish(&plan, &mut f.native, &mut |boundary, artifact| {
            if boundary == Boundary::AfterReplace {
                writes.push(artifact);
            }
            Ok(())
        })
        .unwrap();
    assert_eq!(
        writes,
        plan.steps.iter().map(|s| s.artifact).collect::<Vec<_>>()
    );
    assert_eq!(writes.last(), Some(&Artifact::Installation));
    f.finished(&plan);
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        assert_eq!(
            std::fs::metadata(f.journal(&plan)).unwrap().mode() & 0o777,
            0o600
        );
        assert_eq!(
            std::fs::metadata(f.journal(&plan).parent().unwrap())
                .unwrap()
                .mode()
                & 0o777,
            0o700
        );
    }
}

#[test]
fn existing_owned_files_preserve_unrelated_profiles_metadata_and_external_ssh() {
    let mut f = Fixture::new();
    let first = build(&f.input).unwrap();
    f.publish(&first).unwrap();
    for output in &first.outputs {
        let mut bytes = output.bytes.clone();
        if output.artifact == Artifact::ProfileIndex {
            let mut doc: Value = serde_json::from_slice(&bytes).unwrap();
            doc["metadata"] = json!({"retained":["exact",123]});
            doc["profiles"]
                .as_array_mut()
                .unwrap()
                .push(json!({"id":"unrelated","type":"local","createdAt":42}));
            bytes = serde_json::to_vec(&doc).unwrap();
        }
        let sha256 = digest(&bytes);
        std::fs::write(f.path(output.artifact), &bytes).unwrap();
        f.input.existing.insert(
            output.artifact,
            ExistingFile {
                bytes,
                sha256: sha256.clone(),
                ownership: FileOwnership::Owned {
                    owner: f.input.owner.clone(),
                    artifact: output.artifact,
                    sha256,
                },
            },
        );
    }
    let before: Value =
        serde_json::from_slice(&f.input.existing[&Artifact::ProfileIndex].bytes).unwrap();
    f.input.label = "Updated label".into();
    f.input.preferences.bat_windows = true;
    let plan = build(&f.input).unwrap();
    f.publish(&plan).unwrap();
    f.finished(&plan);
    let after: Value =
        serde_json::from_slice(&std::fs::read(f.path(Artifact::ProfileIndex)).unwrap()).unwrap();
    assert_eq!(before["metadata"], after["metadata"]);
    assert_eq!(before["profiles"], after["profiles"]);
    f.publish(&plan).unwrap();
    f.finished(&plan);
}

#[test]
fn reference_only_and_unknown_refuse_before_any_write() {
    for a in [
        Artifact::Inventory,
        Artifact::ProfileIndex,
        Artifact::SshReference,
        Artifact::Installation,
    ] {
        for refused in [FileOwnership::ReferenceOnly, FileOwnership::Unknown] {
            let mut f = Fixture::new();
            let plan = build(&f.input).unwrap();
            f.native.refused = Some((a, refused));
            assert_eq!(f.publish(&plan), Err(Error::Ownership { artifact: a }));
            for out in &plan.outputs {
                assert!(!f.path(out.artifact).exists());
            }
            assert!(!f.journal(&plan).exists());
        }
    }
}

#[test]
fn third_bytes_at_each_step_stop_without_any_later_write() {
    for (index, a) in [
        Artifact::Inventory,
        Artifact::ProfileIndex,
        Artifact::SshReference,
        Artifact::Installation,
    ]
    .into_iter()
    .enumerate()
    {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        std::fs::write(f.path(a), b"third bytes").unwrap();
        assert_eq!(f.publish(&plan), Err(Error::ThirdBytes { artifact: a }));
        assert_eq!(std::fs::read(f.path(a)).unwrap(), b"third bytes");
        for out in plan.outputs.iter().take(index) {
            assert_eq!(std::fs::read(f.path(out.artifact)).unwrap(), out.bytes);
        }
        for out in plan.outputs.iter().skip(index + 1) {
            assert!(!f.path(out.artifact).exists());
        }
        if f.journal(&plan).exists() {
            let journal: Value =
                serde_json::from_slice(&std::fs::read(f.journal(&plan)).unwrap()).unwrap();
            for receipt in journal["receipts"].as_array().unwrap() {
                assert_eq!(receipt["state"], "published");
            }
        }
    }
}

#[test]
fn every_boundary_recovers_same_plan_without_replacing_a_file_twice() {
    for a in [
        Artifact::Inventory,
        Artifact::ProfileIndex,
        Artifact::SshReference,
        Artifact::Installation,
    ] {
        let mut boundaries = vec![
            Boundary::BeforeIntended,
            Boundary::AfterIntended,
            Boundary::AfterReplace,
            Boundary::AfterReadBack,
        ];
        if a == Artifact::Installation {
            boundaries.extend([Boundary::BeforePointer, Boundary::AfterPointer]);
        }
        for stop in boundaries {
            let mut f = Fixture::new();
            let plan = build(&f.input).unwrap();
            let mut writes = BTreeMap::new();
            let mut hit = false;
            let result = f
                .publisher()
                .publish(&plan, &mut f.native, &mut |b, artifact| {
                    if b == Boundary::AfterReplace {
                        *writes.entry(artifact).or_insert(0) += 1;
                    }
                    if b == stop && artifact == a {
                        hit = true;
                        return Err("INJECTED_IO");
                    }
                    Ok(())
                });
            assert!(hit, "{a:?} {stop:?}");
            assert_eq!(result, Err(Error::Gate("INJECTED_IO")));
            f.publisher()
                .publish(&plan, &mut f.native, &mut |b, artifact| {
                    if b == Boundary::AfterReplace {
                        *writes.entry(artifact).or_insert(0) += 1;
                    }
                    Ok(())
                })
                .unwrap();
            assert_eq!(
                writes.values().copied().collect::<Vec<_>>(),
                vec![1; 4],
                "{a:?} {stop:?}"
            );
            f.finished(&plan);
            f.publisher()
                .publish(&plan, &mut f.native, &mut |b, _| {
                    assert_ne!(b, Boundary::AfterReplace);
                    Ok(())
                })
                .unwrap();
        }
    }
}

#[test]
fn different_review_refuses_even_between_completed_steps() {
    for stop in [Boundary::AfterIntended, Boundary::BeforeIntended] {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        let a = if stop == Boundary::AfterIntended {
            Artifact::Inventory
        } else {
            Artifact::ProfileIndex
        };
        assert!(f
            .publisher()
            .publish(&plan, &mut f.native, &mut |b, artifact| {
                if b == stop && artifact == a {
                    Err("INJECTED_IO")
                } else {
                    Ok(())
                }
            })
            .is_err());
        f.input.label = "Other reviewed label".into();
        let other = build(&f.input).unwrap();
        assert_eq!(
            f.publish(&other),
            Err(Error::UnfinishedPlan {
                review_digest: plan.review_digest.clone()
            })
        );
        f.publish(&plan).unwrap();
        f.finished(&plan);
    }
}

#[test]
fn third_bytes_after_intent_survive_and_completed_prefix_remains_recorded() {
    for a in [
        Artifact::Inventory,
        Artifact::ProfileIndex,
        Artifact::SshReference,
        Artifact::Installation,
    ] {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        let path = f.path(a);
        let mut later = false;
        let result = f
            .publisher()
            .publish(&plan, &mut f.native, &mut |b, artifact| {
                if b == Boundary::AfterIntended && artifact == a {
                    std::fs::write(&path, b"editor").unwrap();
                    later = true;
                }
                if later {
                    assert_ne!(b, Boundary::AfterReplace);
                }
                Ok(())
            });
        assert_eq!(result, Err(Error::ThirdBytes { artifact: a }));
        assert_eq!(std::fs::read(&path).unwrap(), b"editor");
        let receipt = std::fs::read(f.journal(&plan)).unwrap();
        assert_eq!(f.publish(&plan), Err(Error::ThirdBytes { artifact: a }));
        assert_eq!(std::fs::read(f.journal(&plan)).unwrap(), receipt);
    }
}

#[test]
fn unavailable_existing_locks_have_no_publication_effect() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    f.native.launcher.set(true);
    assert_eq!(f.publish(&plan), Err(Error::Gate("LAUNCHER_BUSY")));
    f.native.launcher.set(false);
    f.native.monitor.set(true);
    assert_eq!(
        f.publish(&plan),
        Err(Error::Gate("MONITOR_ALREADY_RUNNING"))
    );
    f.native.monitor.set(false);
    for output in &plan.outputs {
        assert!(!f.path(output.artifact).exists());
    }
    assert!(!f.journal(&plan).exists());
}

#[cfg(unix)]
#[test]
fn symlinked_kit_ancestor_refuses_before_writes_or_receipt_directory_creation() {
    let mut f = Fixture::new();
    let ancestor = f.root.join("linked-parent");
    std::os::unix::fs::symlink(&f.root, &ancestor).unwrap();
    f.input.root = ancestor.join("kit");
    // The kit root itself is a directory; only its ancestor is a symlink.
    assert!(std::fs::symlink_metadata(&f.input.root).unwrap().is_dir());
    let plan = build(&f.input).unwrap();
    assert_eq!(
        f.publisher().publish(&plan, &mut f.native, &mut |_, _| {
            panic!("publication must refuse before reaching a write boundary")
        }),
        Err(Error::Gate("MIGRATION_INVALID_PATH"))
    );
    for output in &plan.outputs {
        assert!(!f.path(output.artifact).exists());
    }
    assert!(!f.root.join("native/fleet-publications").exists());
}

#[cfg(unix)]
#[test]
fn linked_artifacts_and_nonprivate_receipts_refuse_without_following_links() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    let human = f.root.join("human");
    std::fs::write(&human, b"human").unwrap();
    symlink(&human, f.path(Artifact::Inventory)).unwrap();
    assert!(f.publish(&plan).is_err());
    assert_eq!(std::fs::read(&human).unwrap(), b"human");
    std::fs::remove_file(f.path(Artifact::Inventory)).unwrap();
    f.publish(&plan).unwrap();
    std::fs::set_permissions(f.journal(&plan), std::fs::Permissions::from_mode(0o644)).unwrap();
    assert!(f.publish(&plan).is_err());
}

#[test]
fn a_mutated_review_cannot_publish_even_with_self_consistent_output_digests() {
    let mut f = Fixture::new();
    let mut plan = build(&f.input).unwrap();
    let output = plan
        .outputs
        .iter_mut()
        .find(|o| o.artifact == Artifact::Inventory)
        .unwrap();
    let mut document: Value = serde_json::from_slice(&output.bytes).unwrap();
    document["hosts"][0]["label"] = json!("Unreviewed change");
    output.bytes = serde_json::to_vec(&document).unwrap();
    output.sha256 = digest(&output.bytes);
    plan.steps[0].after_digest = output.sha256.clone();
    assert_eq!(f.publish(&plan), Err(Error::InvalidPlan));
    for output in &plan.outputs {
        assert!(!f.path(output.artifact).exists());
    }
    assert!(!f.journal(&plan).exists());
}

#[test]
fn disk_bytes_override_published_receipts_and_missing_receipts_are_repaired() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    f.publish(&plan).unwrap();
    // An external restore to the original absent state must not be called applied
    // merely because a published receipt remains. Crash immediately after intent.
    std::fs::remove_file(f.path(Artifact::Inventory)).unwrap();
    assert_eq!(
        f.publisher().publish(&plan, &mut f.native, &mut |b, a| {
            if b == Boundary::AfterIntended && a == Artifact::Inventory {
                Err("INJECTED_IO")
            } else {
                Ok(())
            }
        }),
        Err(Error::Gate("INJECTED_IO"))
    );
    f.publish(&plan).unwrap();
    f.finished(&plan);
    std::fs::remove_file(f.journal(&plan)).unwrap();
    f.publisher()
        .publish(&plan, &mut f.native, &mut |b, _| {
            assert_ne!(b, Boundary::AfterReplace);
            Ok(())
        })
        .unwrap();
    f.finished(&plan);
}

#[test]
fn pointer_rechecks_kit_after_the_before_pointer_boundary() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    let inventory = f.path(Artifact::Inventory);
    assert_eq!(
        f.publisher().publish(&plan, &mut f.native, &mut |b, _| {
            if b == Boundary::BeforePointer {
                std::fs::write(&inventory, b"third bytes").unwrap();
            }
            Ok(())
        }),
        Err(Error::ThirdBytes {
            artifact: Artifact::Inventory
        })
    );
    assert!(!f.path(Artifact::Installation).exists());
    assert_eq!(std::fs::read(inventory).unwrap(), b"third bytes");
}

#[test]
fn ownership_is_reproved_after_intended_before_replacing() {
    let mut f = Fixture::new();
    let plan = build(&f.input).unwrap();
    let authority = f.native.authority.clone();
    assert_eq!(
        f.publisher().publish(&plan, &mut f.native, &mut |b, _| {
            if b == Boundary::AfterIntended {
                authority.set(false);
            }
            Ok(())
        }),
        Err(Error::Ownership {
            artifact: Artifact::Inventory
        })
    );
    for output in &plan.outputs {
        assert!(!f.path(output.artifact).exists());
    }
    authority.set(true);
    f.publish(&plan).unwrap();
    f.finished(&plan);
}

#[test]
fn existing_migration_journal_gate_blocks_publication() {
    let mut f = Fixture::new();
    let first = build(&f.input).unwrap();
    f.publish(&first).unwrap();
    let store =
        migration::Store::new(&f.path(Artifact::Installation), &f.root.join("Startup")).unwrap();
    f.native.require_monitor = false;
    let preview = store.preview(&mut f.native, Backend::Rust).unwrap();
    // Use migration's real producer, not a hand-written pending journal.
    store
        .begin(
            &mut f.native,
            &"b".repeat(32),
            &preview,
            Backend::Rust,
            &first.output(Artifact::Installation).bytes,
            false,
        )
        .unwrap();
    f.native.require_monitor = true;
    assert!(store.pending().unwrap().is_some());
    let receipt = std::fs::read(f.journal(&first)).unwrap();
    assert_eq!(f.publish(&first), Err(Error::Gate("MIGRATION_PENDING")));
    assert_eq!(std::fs::read(f.journal(&first)).unwrap(), receipt);
}

#[test]
fn altered_receipt_fields_refuse_without_republishing() {
    for field in [
        "version",
        "review_digest",
        "generator_version",
        "artifact",
        "before_digest",
        "after_digest",
        "owner",
        "state",
    ] {
        let mut f = Fixture::new();
        let plan = build(&f.input).unwrap();
        f.publish(&plan).unwrap();
        let mut journal: Value =
            serde_json::from_slice(&std::fs::read(f.journal(&plan)).unwrap()).unwrap();
        journal["receipts"][0][field] = json!("tampered");
        let changed = serde_json::to_vec(&journal).unwrap();
        std::fs::write(f.journal(&plan), &changed).unwrap();
        assert_eq!(f.publish(&plan), Err(Error::ReceiptConflict));
        assert_eq!(std::fs::read(f.journal(&plan)).unwrap(), changed);
        for output in &plan.outputs {
            assert_eq!(
                std::fs::read(f.path(output.artifact)).unwrap(),
                output.bytes
            );
        }
    }
}
