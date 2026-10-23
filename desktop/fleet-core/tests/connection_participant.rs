use bat_fleet_core::{
    connection_participant::*,
    connection_publish::{AbandonDecision, Boundary, FrozenHistory, Publisher},
    connection_setup::{build, Artifact, FileOwnership, OwnerEvidence},
    digest, migration, publication_state, Result as NativeResult,
};
use serde_json::{json, Value};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
};
#[path = "support/connection_setup.rs"]
mod setup;

struct Native(OwnerEvidence);
impl migration::Platform for Native {
    type LauncherGuard = ();
    type MonitorGuard = ();
    fn launcher_guard(&mut self) -> NativeResult<()> {
        Ok(())
    }
    fn monitor_guard(&mut self) -> NativeResult<()> {
        Ok(())
    }
    fn login(&self) -> NativeResult<bat_fleet_core::process_adapter::LoginIdentity> {
        Ok(bat_fleet_core::process_adapter::LoginIdentity {
            owner_sid: self.0.owner_sid.clone(),
            session_id: 1,
        })
    }
    fn discover(&mut self) -> NativeResult<Option<bat_fleet_core::discovery::MonitorIdentity>> {
        Ok(None)
    }
    fn request_quit(&mut self, _: &migration::Owner) -> NativeResult<()> {
        panic!("no process effects")
    }
    fn launch(&mut self, _: bat_fleet_core::discovery::Backend) -> NativeResult<()> {
        panic!("no process effects")
    }
    fn validate_config(&self, _: &[u8], _: bat_fleet_core::discovery::Backend) -> NativeResult<()> {
        unreachable!()
    }
    fn classify_shortcut(&self, _: &[u8]) -> NativeResult<bat_fleet_core::discovery::Backend> {
        unreachable!()
    }
    fn shortcut(&self, _: bat_fleet_core::discovery::Backend) -> NativeResult<Vec<u8>> {
        unreachable!()
    }
}
impl bat_fleet_core::connection_publish::Platform for Native {
    fn ownership(
        &self,
        owner: &OwnerEvidence,
        artifact: Artifact,
        _: &Path,
        current: Option<&[u8]>,
    ) -> NativeResult<FileOwnership> {
        assert_eq!(owner, &self.0);
        Ok(FileOwnership::Owned {
            owner: owner.clone(),
            artifact,
            sha256: digest(current.unwrap_or_default()),
        })
    }
    fn verify_private_state(&self, _: &Path, _: &OwnerEvidence) -> NativeResult<()> {
        Ok(())
    }
}

// This portable backend exists only in this test binary. It is no Windows authority.
struct Disk {
    root: PathBuf,
    fail: Option<(Part, bool)>,
}
impl Disk {
    fn path(&self, namespace: &Namespace, part: Part) -> PathBuf {
        self.root
            .join(namespace.relative_path())
            .join(part.filename().unwrap())
    }
}
impl Backend for Disk {
    fn available(&mut self) -> Result<()> {
        Ok(())
    }
    fn entries(&mut self, namespace: &Namespace) -> Result<Vec<Part>> {
        let path = self.root.join(namespace.relative_path());
        match fs::read_dir(path) {
            Ok(entries) => entries
                .take(13)
                .map(|e| {
                    Part::from_filename(
                        e.map_err(|_| Error::Unavailable)?
                            .file_name()
                            .to_str()
                            .ok_or(Error::Invalid)?,
                    )
                })
                .collect(),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(vec![]),
            Err(_) => Err(Error::Unavailable),
        }
    }
    fn read(&mut self, namespace: &Namespace, part: Part, bound: usize) -> Result<Option<Vec<u8>>> {
        publication_state::read_private(&self.path(namespace, part), bound)
            .map_err(|_| Error::Invalid)
    }
    fn create_only(&mut self, namespace: &Namespace, part: Part, bytes: &[u8]) -> Result<()> {
        if self.fail == Some((part, false)) {
            return Err(Error::Unavailable);
        }
        let path = self.path(namespace, part);
        let mut missing = Vec::new();
        let mut parent = path.parent().unwrap();
        while !parent.exists() {
            missing.push(parent);
            parent = parent.parent().unwrap();
        }
        for dir in missing.into_iter().rev() {
            publication_state::create_new_directory(dir).map_err(|_| Error::Unavailable)?;
        }
        let stage = path.with_extension("staging");
        let mut file = publication_state::create_new_file(&stage).map_err(|_| Error::Conflict)?;
        file.write_all(bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| Error::Unavailable)?;
        drop(file);
        fs::hard_link(&stage, &path).map_err(|_| Error::Conflict)?;
        fs::remove_file(stage).map_err(|_| Error::Unavailable)?;
        #[cfg(unix)]
        fs::File::open(path.parent().unwrap())
            .unwrap()
            .sync_all()
            .unwrap();
        if self.fail == Some((part, true)) {
            return Err(Error::Unavailable);
        }
        Ok(())
    }
}
struct Fixture {
    root: PathBuf,
    intent: Intent,
    frozen: FrozenHistory,
    wire: Value,
}
impl Fixture {
    fn new() -> Self {
        Self::with_local_plan(false)
    }
    fn with_local_plan(local: bool) -> Self {
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("participant-{:032x}", rand::random::<u128>()));
        publication_state::create_new_directory(&root).unwrap();
        let mut input = setup::setup();
        input.root = if local {
            root.join("kit")
        } else {
            "//example.invalid/synthetic-fleet".into()
        };
        let plan = build(&input).unwrap();
        let publisher = Publisher::new(
            &root.join("fleet.json"),
            &root.join("startup"),
            &root.join("user"),
        )
        .unwrap();
        let reference = publisher
            .export_plan(&plan, &mut Native(input.owner))
            .unwrap();
        let bytes = fs::read(
            root.join("fleet-publication-plans")
                .join(format!("{}.json", plan.review_digest)),
        )
        .unwrap();
        let frozen = FrozenHistory::decode(&reference, &bytes).unwrap();
        let mut wire: Value =
            serde_json::from_str(include_str!("fixtures/connection-participant-wire.json"))
                .unwrap();
        if local {
            // Preserve the producer shape while binding this real local generator output.
            let manifest = serde_json::to_value(
                bat_fleet_core::connection_contract::native_plan_manifest(&plan).unwrap(),
            )
            .unwrap();
            for case in wire.as_object_mut().unwrap().values_mut() {
                for mode in ["claim", "readback"] {
                    case[mode]["native_handoff"]["native_plan"] = manifest.clone();
                }
            }
        }
        let native = &wire["applied"]["claim"]["native_handoff"];
        let scope = Scope {
            server_id: native["server_id"].as_str().unwrap().into(),
            principal_id: native["principal_id"].as_str().unwrap().into(),
            installation_ref: native["installation_ref"].as_str().unwrap().into(),
            native_installation: "22222222-2222-4222-8222-222222222222".into(),
        };
        let intent = Intent::fleet(
            Accepted {
                scope,
                action: "setup.connection.apply".into(),
                contract_version: 1,
                kind: None,
                operation_id: native["operation_id"].as_str().unwrap().into(),
                operation_key: "original-key".into(),
                target: Target {
                    installation_ref: "desktop-a".into(),
                    connection_id: None,
                },
                expected_revision: 0,
                review_digest: native["review_digest"].as_str().unwrap().into(),
                credential_generation: "credential-generation-1".into(),
            },
            &frozen,
        )
        .unwrap();
        Self {
            root,
            intent,
            frozen,
            wire,
        }
    }
    fn store(&self) -> Store<Disk> {
        Store::new(Disk {
            root: self.root.clone(),
            fail: None,
        })
    }
    fn save(&self, store: &mut Store<Disk>, part: Part, value: &Value) -> Record {
        store
            .append(&self.intent, part, &serde_json::to_vec(value).unwrap())
            .unwrap()
    }
    fn claimed(&self, store: &mut Store<Disk>) -> Record {
        store.begin(&self.intent).unwrap();
        self.save(
            store,
            Part::Claim,
            &json!({"requested_at_ms":1800000000000_u64}),
        );
        self.save(store, Part::Envelope, &self.wire["applied"]["claim"])
    }
    fn effects(&self, store: &mut Store<Disk>) {
        for (i, a) in ["Inventory", "ProfileIndex", "SshReference", "Installation"]
            .iter()
            .enumerate()
        {
            self.save(
                store,
                Part::Attempt(i as u8),
                &json!({"effect":{"artifact":a}}),
            );
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}
const NOW: u64 = 1800000000000;

#[test]
fn production_without_a_backend_is_unavailable() {
    let mut store = Store::new(Unavailable);
    assert!(store.available().is_err());
    assert_ne!(Part::Claim, Part::Envelope);
    assert_eq!(Next::Claim, Next::Claim);
}

#[test]
fn restart_loads_the_original_intent_and_frozen_reference_without_a_new_plan() {
    let f = Fixture::new();
    f.claimed(&mut f.store());
    let record = f
        .store()
        .open(
            &f.intent.accepted().scope,
            &f.intent.accepted().operation_id,
            |reference| {
                assert_eq!(reference, f.frozen.reference());
                let bytes = fs::read(
                    f.root
                        .join("fleet-publication-plans")
                        .join(format!("{}.json", reference.review_digest)),
                )
                .unwrap();
                FrozenHistory::decode(reference, &bytes).map_err(|_| Error::Invalid)
            },
        )
        .unwrap()
        .unwrap();
    assert_eq!(record.intent().bytes().unwrap(), f.intent.bytes().unwrap());
    assert_eq!(
        record.recover(NOW).unwrap(),
        Next::Executor { read_only: false }
    );
    let mut wrong = f.intent.accepted().scope.clone();
    wrong.principal_id = "2".repeat(64);
    assert!(f
        .store()
        .open(&wrong, &f.intent.accepted().operation_id, |_| panic!(
            "foreign namespace"
        ))
        .unwrap()
        .is_none());
}

#[test]
fn installation_reference_uses_the_shared_character_bound() {
    let f = Fixture::new();
    let mut accepted = f.intent.accepted().clone();
    accepted.scope.installation_ref = "機".repeat(128);
    accepted.target.installation_ref = accepted.scope.installation_ref.clone();
    accepted.operation_key = "原本的操作識別".into();
    assert!(Intent::fleet(accepted.clone(), &f.frozen).is_ok());
    accepted.scope.installation_ref.push('機');
    accepted.target.installation_ref = accepted.scope.installation_ref.clone();
    assert!(Intent::fleet(accepted, &f.frozen).is_err());
}

#[test]
fn real_manifest_and_producer_wires_bind_the_original_scope_and_frozen_snapshot() {
    let f = Fixture::new();
    let golden: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/native-plan-manifest.json"
    ))
    .unwrap();
    assert_eq!(
        golden["create"],
        f.wire["applied"]["claim"]["native_handoff"]["native_plan"]
    );
    let original = f.intent.bytes().unwrap();
    assert!(Intent::decode(&original, &f.intent.accepted().scope, &f.frozen).is_ok());
    let namespace = f.intent.namespace();
    assert!(namespace
        .relative_path()
        .starts_with("connections-v1/participant/"));
    assert!(namespace
        .relative_path()
        .ends_with(&f.intent.accepted().operation_id));
    assert!(!namespace.relative_path().contains("desktop-a"));
    for (field, value) in [
        ("action", json!("connection.reference.apply")),
        ("contract_version", json!(2)),
        ("kind", json!("reference_detach")),
    ] {
        let mut body: Value = serde_json::from_slice(&original).unwrap();
        body["accepted"][field] = value;
        assert_eq!(
            Intent::decode(
                &serde_json::to_vec(&body).unwrap(),
                &f.intent.accepted().scope,
                &f.frozen
            )
            .err(),
            Some(Error::Unsupported)
        );
    }
    let mut wrong: Value = serde_json::from_slice(&original).unwrap();
    wrong["frozen"]["sha256"] = json!("0".repeat(64));
    assert!(Intent::decode(
        &serde_json::to_vec(&wrong).unwrap(),
        &f.intent.accepted().scope,
        &f.frozen
    )
    .is_err());
    let mut scope = f.intent.accepted().scope.clone();
    scope.principal_id = "2".repeat(64);
    assert!(Intent::decode(&original, &scope, &f.frozen).is_err());
    let duplicate = String::from_utf8(original)
        .unwrap()
        .replacen('{', "{\"format_version\":1,", 1);
    assert!(Intent::decode(duplicate.as_bytes(), &f.intent.accepted().scope, &f.frozen).is_err());
    assert!(Intent::decode(&vec![b' '; 20000], &f.intent.accepted().scope, &f.frozen).is_err());
}

#[test]
fn recovery_never_delivers_an_unpersisted_receipt_or_renews_a_sent_claim() {
    let f = Fixture::new();
    let mut store = f.store();
    assert_eq!(
        store.begin(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Claim
    );
    let claimed = f.save(&mut store, Part::Claim, &json!({"requested_at_ms":NOW}));
    assert_eq!(claimed.recover(NOW + 1).unwrap(), Next::ReplayClaim);
    assert_eq!(claimed.recover(NOW + 300000).unwrap(), Next::Readback);
    let record = f.save(&mut store, Part::Envelope, &f.wire["applied"]["claim"]);
    assert_eq!(
        record.recover(NOW).unwrap(),
        Next::Executor { read_only: false }
    );
    assert_eq!(
        record.recover(NOW + 300000).unwrap(),
        Next::Executor { read_only: true }
    );
    let bytes = serde_json::to_vec_pretty(&f.wire["applied"]["wire"]).unwrap();
    assert!(store.append(&f.intent, Part::Receipt(0), &bytes).is_err());
    f.effects(&mut store);
    let record = store.append(&f.intent, Part::Receipt(0), &bytes).unwrap();
    assert_eq!(
        record.recover(NOW).unwrap(),
        Next::Redeliver {
            index: 0,
            body: bytes.clone()
        }
    );
    assert_eq!(
        store
            .load(&f.intent)
            .unwrap()
            .recover(NOW + 900000)
            .unwrap(),
        Next::Redeliver {
            index: 0,
            body: bytes
        }
    );
    let record = f.save(
        &mut store,
        Part::Acknowledgement(0),
        &f.wire["applied"]["ack"],
    );
    assert_eq!(record.recover(NOW).unwrap(), Next::Done);
}

#[test]
fn only_one_uncertain_and_one_acknowledged_definitive_successor_are_retained() {
    let f = Fixture::new();
    let mut store = f.store();
    f.claimed(&mut store);
    f.save(&mut store, Part::Receipt(0), &f.wire["uncertain"]["wire"]);
    assert!(store
        .append(
            &f.intent,
            Part::Receipt(1),
            &serde_json::to_vec(&f.wire["uncertain"]["wire"]).unwrap()
        )
        .is_err());
    let record = f.save(
        &mut store,
        Part::Acknowledgement(0),
        &f.wire["uncertain"]["ack"],
    );
    assert_eq!(
        record.recover(NOW).unwrap(),
        Next::Executor { read_only: true }
    );
    assert!(store
        .append(
            &f.intent,
            Part::Receipt(1),
            &serde_json::to_vec(&f.wire["uncertain"]["wire"]).unwrap()
        )
        .is_err());
    // An immutable decision fence survives a crash before or after its sidecar.
    let decision = json!({"abandonment":{"reference":"original-human-decision","recorded_at_unix_ms":NOW},"journal_digest":"d".repeat(64)});
    f.save(&mut store, Part::Attempt(0), &decision);
    let recovered = store.load(&f.intent).unwrap();
    assert_eq!(
        recovered.abandonment().unwrap().reference,
        "original-human-decision"
    );
    f.save(
        &mut store,
        Part::Receipt(1),
        &f.wire["uncertain"]["definitive"],
    );
    let record = f.save(
        &mut store,
        Part::Acknowledgement(1),
        &f.wire["uncertain"]["definitive_ack"],
    );
    assert_eq!(record.recover(NOW).unwrap(), Next::Done);
    assert!(store.append(&f.intent, Part::Receipt(2), b"{}").is_err());
}

#[test]
fn readback_authority_never_creates_an_effect_fence() {
    let f = Fixture::new();
    let mut store = f.store();
    store.begin(&f.intent).unwrap();
    f.save(&mut store, Part::Claim, &json!({"requested_at_ms":NOW}));
    let record = f.save(&mut store, Part::Envelope, &f.wire["applied"]["readback"]);
    assert_eq!(
        record.recover(NOW).unwrap(),
        Next::Executor { read_only: true }
    );
    assert!(store
        .append(
            &f.intent,
            Part::Attempt(0),
            br#"{"effect":{"artifact":"Inventory"}}"#
        )
        .is_err());
}

#[test]
fn readback_receipt_history_allows_only_an_exact_replay_or_definitive_successor() {
    for outcome in ["uncertain", "refused"] {
        let f = Fixture::new();
        let mut store = f.store();
        store.begin(&f.intent).unwrap();
        f.save(&mut store, Part::Claim, &json!({"requested_at_ms":NOW}));
        f.save(
            &mut store,
            Part::Envelope,
            &f.wire[outcome]["settled_readback"],
        );
        let mut changed = f.wire[outcome]["wire"].clone();
        changed["code"] = json!("LOCAL_THIRD_BYTES");
        assert!(store
            .append(
                &f.intent,
                Part::Receipt(0),
                &serde_json::to_vec(&changed).unwrap()
            )
            .is_err());
        f.save(&mut store, Part::Receipt(0), &f.wire[outcome]["wire"]);
        f.save(
            &mut store,
            Part::Acknowledgement(0),
            &f.wire[outcome]["ack"],
        );
    }
    let f = Fixture::new();
    let mut store = f.store();
    store.begin(&f.intent).unwrap();
    f.save(&mut store, Part::Claim, &json!({"requested_at_ms":NOW}));
    f.save(
        &mut store,
        Part::Envelope,
        &f.wire["uncertain"]["settled_readback"],
    );
    f.save(&mut store, Part::Attempt(0), &json!({"abandonment":{"reference":"original-decision","recorded_at_unix_ms":NOW},"journal_digest":"d".repeat(64)}));
    f.save(
        &mut store,
        Part::Receipt(0),
        &f.wire["uncertain"]["definitive"],
    );
    let record = f.save(
        &mut store,
        Part::Acknowledgement(0),
        &f.wire["uncertain"]["definitive_ack"],
    );
    assert_eq!(record.recover(NOW).unwrap(), Next::Done);
}

#[test]
fn foreign_or_malformed_envelopes_receipts_and_acknowledgements_refuse_without_writes() {
    let f = Fixture::new();
    let mut store = f.store();
    store.begin(&f.intent).unwrap();
    f.save(&mut store, Part::Claim, &json!({"requested_at_ms":NOW}));
    for field in [
        "server_id",
        "principal_id",
        "operation_id",
        "review_digest",
        "challenge",
        "native_plan",
        "epoch",
    ] {
        let mut value = f.wire["applied"]["claim"].clone();
        value["native_handoff"][field] = json!("wrong");
        assert!(
            store
                .append(
                    &f.intent,
                    Part::Envelope,
                    &serde_json::to_vec(&value).unwrap()
                )
                .is_err(),
            "{field}"
        );
    }
    let raw = serde_json::to_string(&f.wire["applied"]["claim"])
        .unwrap()
        .replacen('{', "{\"read_only\":false,", 1);
    assert!(store
        .append(&f.intent, Part::Envelope, raw.as_bytes())
        .is_err());
    f.save(&mut store, Part::Envelope, &f.wire["applied"]["claim"]);
    f.effects(&mut store);
    for field in [
        "contract_version",
        "native_review_digest",
        "epoch",
        "roles",
        "code",
        "phase",
        "challenge",
    ] {
        let mut value = f.wire["applied"]["wire"].clone();
        value[field] = json!("wrong");
        assert!(
            store
                .append(
                    &f.intent,
                    Part::Receipt(0),
                    &serde_json::to_vec(&value).unwrap()
                )
                .is_err(),
            "{field}"
        );
    }
    assert!(store
        .append(&f.intent, Part::Receipt(0), &vec![b' '; 8193])
        .is_err());
    f.save(&mut store, Part::Receipt(0), &f.wire["applied"]["wire"]);
    let mut reply = f.wire["applied"]["ack"].clone();
    reply["receipt"]["receipt_ref"] = json!(format!("receipt_{}", "0".repeat(64)));
    assert!(store
        .append(
            &f.intent,
            Part::Acknowledgement(0),
            &serde_json::to_vec(&reply).unwrap()
        )
        .is_err());
    assert!(store.append(&f.intent, Part::Envelope, b"{}").is_err());
    assert_eq!(
        store.load(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Redeliver {
            index: 0,
            body: serde_json::to_vec(&f.wire["applied"]["wire"]).unwrap()
        }
    );
}

#[test]
fn a_crash_after_the_real_publisher_decision_keeps_the_original_decision() {
    let f = Fixture::with_local_plan(true);
    let mut store = f.store();
    f.claimed(&mut store);
    let mut native = Native(setup::setup().owner);
    let publisher = Publisher::new(
        &f.root.join("fleet.json"),
        &f.root.join("startup"),
        &f.root.join("user"),
    )
    .unwrap();
    assert!(publisher
        .publish(f.frozen.plan(), &mut native, &mut |boundary, artifact| {
            if boundary == Boundary::AfterReadBack && artifact == Artifact::Inventory {
                Err("INTERRUPTED")
            } else {
                Ok(())
            }
        })
        .is_err());
    let journal = f
        .root
        .join("fleet-publications")
        .join(format!("{}.json", f.frozen.plan().review_digest));
    let original = fs::read(&journal).unwrap();
    let decision = AbandonDecision {
        reference: "persisted-human-decision".into(),
        recorded_at_unix_ms: NOW,
    };
    f.save(
        &mut store,
        Part::Attempt(0),
        &json!({"abandonment":decision,"journal_digest":digest(&original)}),
    );
    let sidecar = publisher
        .abandon_remaining(f.frozen.plan(), &decision, &mut native, &mut |_| Ok(()))
        .unwrap();
    let before = fs::read(&sidecar).unwrap();
    let recovered = store.load(&f.intent).unwrap();
    assert_eq!(
        recovered.recover(NOW + 900000).unwrap(),
        Next::Executor { read_only: true }
    );
    let replay = publisher
        .abandon_remaining(
            f.frozen.plan(),
            recovered.abandonment().unwrap(),
            &mut native,
            &mut |_| panic!("must not create another decision"),
        )
        .unwrap();
    assert_eq!(sidecar, replay);
    assert_eq!(fs::read(sidecar).unwrap(), before);
    assert_eq!(fs::read(journal).unwrap(), original);
}

#[test]
fn every_create_boundary_recovers_only_durable_parts_and_leaves_business_files_alone() {
    for scenario in 0..21 {
        let uncertain = scenario >= 9;
        let target = if uncertain { scenario - 9 } else { scenario };
        for after in [false, true] {
            let f = Fixture::new();
            let mut steps = vec![
                (Part::Intent, f.intent.bytes().unwrap()),
                (
                    Part::Claim,
                    serde_json::to_vec(&json!({"requested_at_ms":NOW})).unwrap(),
                ),
                (
                    Part::Envelope,
                    serde_json::to_vec(&f.wire["applied"]["claim"]).unwrap(),
                ),
            ];
            for (i, a) in ["Inventory", "ProfileIndex", "SshReference", "Installation"]
                .iter()
                .enumerate()
            {
                steps.push((
                    Part::Attempt(i as u8),
                    serde_json::to_vec(&json!({"effect":{"artifact":a}})).unwrap(),
                ));
            }
            let outcome = if uncertain { "uncertain" } else { "applied" };
            steps.push((
                Part::Receipt(0),
                serde_json::to_vec(&f.wire[outcome]["wire"]).unwrap(),
            ));
            steps.push((
                Part::Acknowledgement(0),
                serde_json::to_vec(&f.wire[outcome]["ack"]).unwrap(),
            ));
            if uncertain {
                steps.extend([
                    (Part::Attempt(4), serde_json::to_vec(&json!({"abandonment":{"reference":"original-decision","recorded_at_unix_ms":NOW},"journal_digest":"d".repeat(64)})).unwrap()),
                    (Part::Receipt(1), serde_json::to_vec(&f.wire["uncertain"]["definitive"]).unwrap()),
                    (Part::Acknowledgement(1), serde_json::to_vec(&f.wire["uncertain"]["definitive_ack"]).unwrap()),
                ]);
            }
            // Include the intent fence itself as well as every later part.
            let sentinels = [
                "fleet.json",
                "fleet-inventory.json",
                "index.json",
                "ssh-config",
                "catalog.json",
                "selection.json",
            ];
            for name in sentinels {
                fs::write(f.root.join(name), b"independent business bytes").unwrap();
            }
            let frozen_path = f
                .root
                .join("fleet-publication-plans")
                .join(format!("{}.json", f.frozen.reference().review_digest));
            let original_frozen = fs::read(&frozen_path).unwrap();
            let mut store = f.store();
            for (part, bytes) in &steps[..target] {
                if *part == Part::Intent {
                    store.begin(&f.intent).unwrap();
                } else {
                    store.append(&f.intent, *part, bytes).unwrap();
                }
            }
            let (part, bytes) = &steps[target];
            let mut faulted = Store::new(Disk {
                root: f.root.clone(),
                fail: Some((*part, after)),
            });
            let failed = if *part == Part::Intent {
                faulted.begin(&f.intent)
            } else {
                faulted.append(&f.intent, *part, bytes)
            };
            assert_eq!(failed.err(), Some(Error::Unavailable), "{part:?} {after}");
            let record = f.store().begin(&f.intent).unwrap();
            let durable = target + usize::from(after);
            let next = record.recover(NOW).unwrap();
            match durable {
                0 | 1 => assert_eq!(next, Next::Claim),
                2 => assert_eq!(next, Next::ReplayClaim),
                3 => assert_eq!(next, Next::Executor { read_only: false }),
                4..=7 => assert_eq!(next, Next::Executor { read_only: true }),
                8 => assert_eq!(
                    next,
                    Next::Redeliver {
                        index: 0,
                        body: steps[7].1.clone()
                    }
                ),
                9 | 10 if uncertain => assert_eq!(next, Next::Executor { read_only: true }),
                11 if uncertain => assert_eq!(
                    next,
                    Next::Redeliver {
                        index: 1,
                        body: steps[10].1.clone()
                    }
                ),
                _ => assert_eq!(next, Next::Done),
            }
            for name in sentinels {
                assert_eq!(
                    fs::read(f.root.join(name)).unwrap(),
                    b"independent business bytes"
                );
            }
            assert_eq!(fs::read(frozen_path).unwrap(), original_frozen);
        }
    }
}

#[test]
fn request_text_cannot_choose_a_namespace_and_existing_parts_cannot_be_replaced() {
    let f = Fixture::new();
    let mut accepted = f.intent.accepted().clone();
    accepted.scope.installation_ref = "../../request-path".into();
    accepted.target.installation_ref = accepted.scope.installation_ref.clone();
    let intent = Intent::fleet(accepted, &f.frozen).unwrap();
    let expected = format!(
        "connections-v1/participant/{}/{}",
        digest(&serde_json::to_vec(&intent.accepted().scope).unwrap()),
        intent.accepted().operation_id
    );
    assert_eq!(intent.namespace().relative_path(), expected);
    assert!(!expected.contains("request-path"));
    let mut disk = Disk {
        root: f.root.clone(),
        fail: None,
    };
    disk.create_only(&intent.namespace(), Part::Intent, &intent.bytes().unwrap())
        .unwrap();
    let original = fs::read(disk.path(&intent.namespace(), Part::Intent)).unwrap();
    assert!(disk
        .create_only(&intent.namespace(), Part::Intent, b"replacement")
        .is_err());
    assert_eq!(
        fs::read(disk.path(&intent.namespace(), Part::Intent)).unwrap(),
        original
    );
}

#[test]
fn corrupt_foreign_oversized_or_linked_parts_fail_closed() {
    for fault in 0..5 {
        let f = Fixture::new();
        let mut store = f.store();
        f.claimed(&mut store);
        let disk = Disk {
            root: f.root.clone(),
            fail: None,
        };
        let path = disk.path(&f.intent.namespace(), Part::Envelope);
        match fault {
            0 => fs::write(&path, b"not json").unwrap(),
            1 => {
                let mut v: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
                v["intent_digest"] = json!("0".repeat(64));
                fs::write(&path, serde_json::to_vec(&v).unwrap()).unwrap();
            }
            2 => fs::write(&path, vec![b' '; 50000]).unwrap(),
            3 => {
                let path = disk.path(&f.intent.namespace(), Part::Intent);
                let mut v: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
                v["accepted"]["kind"] = json!("reference_detach");
                fs::write(&path, serde_json::to_vec(&v).unwrap()).unwrap();
            }
            _ => {
                fs::write(path.parent().unwrap().join("unexpected.json"), b"{}").unwrap();
            }
        }
        assert!(f.store().load(&f.intent).is_err());
    }
    #[cfg(unix)]
    {
        let f = Fixture::new();
        let mut store = f.store();
        f.claimed(&mut store);
        let disk = Disk {
            root: f.root.clone(),
            fail: None,
        };
        let path = disk.path(&f.intent.namespace(), Part::Envelope);
        let target = f.root.join("foreign");
        fs::rename(&path, &target).unwrap();
        std::os::unix::fs::symlink(target, &path).unwrap();
        assert!(store.load(&f.intent).is_err());
    }
}
