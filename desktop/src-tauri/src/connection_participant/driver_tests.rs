use super::{
    driver::*,
    review::*,
    tests::{binding, golden},
    transport::*,
    Error, Result,
};
use bat_fleet_core::{
    connection_participant::{self as core, *},
    connection_publish::{FrozenHistory, FrozenPlanRef},
    digest, publication_state,
};
use serde_json::{json, Value};
use std::{
    cell::{Cell, RefCell},
    fs,
    io::Write,
    path::PathBuf,
};
#[allow(dead_code)]
#[path = "../../../fleet-core/tests/support/creation_evidence.rs"]
mod creation;
const NOW: u64 = 1800000000000;
struct Disk {
    root: PathBuf,
    fail: Option<Part>,
}
impl Backend for Disk {
    fn available(&mut self) -> core::Result<()> {
        Ok(())
    }
    fn entries(&mut self, n: &Namespace) -> core::Result<Vec<Part>> {
        let path = self.root.join(n.relative_path());
        if !path.exists() {
            return Ok(vec![]);
        }
        fs::read_dir(path)
            .unwrap()
            .take(31)
            .map(|e| Part::from_filename(e.unwrap().file_name().to_str().unwrap()))
            .collect()
    }
    fn read(&mut self, n: &Namespace, p: Part, b: usize) -> core::Result<Option<Vec<u8>>> {
        publication_state::read_private(&self.root.join(n.relative_path()).join(p.filename()?), b)
            .map_err(|_| core::Error::Invalid)
    }
    fn create_only(&mut self, n: &Namespace, p: Part, b: &[u8]) -> core::Result<()> {
        if self.fail == Some(p) {
            return Err(core::Error::Unavailable);
        }
        let mut directory = self.root.clone();
        for component in n.relative_path().split('/') {
            directory.push(component);
            if !directory.exists() {
                publication_state::create_new_directory(&directory).unwrap();
            }
        }
        let mut file = publication_state::create_new_file(&directory.join(p.filename()?))
            .map_err(|_| core::Error::Conflict)?;
        file.write_all(b).unwrap();
        file.sync_all().unwrap();
        #[cfg(unix)]
        fs::File::open(directory).unwrap().sync_all().unwrap();
        Ok(())
    }
}
struct Fixture {
    creation: creation::Fixture,
    intent: Intent,
    current: CurrentBinding,
    op: Value,
    envelope: Value,
    review: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let mut creation = creation::Fixture::new();
        let plan = creation.plan();
        let reference = creation
            .native
            .publisher()
            .export_plan(&plan, &mut creation.native)
            .unwrap();
        let bytes = fs::read(creation.native.frozen_path(&reference)).unwrap();
        let frozen = FrozenHistory::decode(&reference, &bytes).unwrap();
        let manifest = serde_json::to_value(
            bat_fleet_core::connection_contract::native_plan_manifest(&plan).unwrap(),
        )
        .unwrap();
        let mut op = golden()["operation"]["operation"].clone();
        op["params"]["native_plan"] = manifest.clone();
        op["external_refs"]["admission_binding"]["native_plan"] = manifest.clone();
        let intent = Intent::fleet_post(
            Accepted {
                scope: binding(),
                action: FLEET_ACTION.into(),
                contract_version: 1,
                kind: None,
                operation_id: op["operation_id"].as_str().unwrap().into(),
                operation_key: "original-key".into(),
                target: Target {
                    installation_ref: "desktop-a".into(),
                    connection_id: None,
                },
                expected_revision: 0,
                review_digest: "b".repeat(64),
                credential_generation: "original-generation".into(),
            },
            &frozen,
        )
        .unwrap();
        let current = CurrentBinding {
            scope: binding(),
            credential_generation: "original-generation".into(),
            configuration_ref: ConfigurationRef {
                id: "central-original".into(),
                sha256: digest(b"original configuration"),
            },
        };
        let association = AssociationContext {
            configuration_ref: current.configuration_ref.clone(),
            source_descriptor_sha256: digest(
                &serde_json::to_vec(&op["external_refs"]["admission_binding"]["source_descriptor"])
                    .unwrap(),
            ),
        };
        let review = creation.root.join("protected-review.json");
        let raw = json!({"format_version":1,"intent":String::from_utf8(intent.bytes().unwrap()).unwrap(),"frozen":reference,"association":association});
        let mut file = publication_state::create_new_file(&review).unwrap();
        file.write_all(&serde_json::to_vec(&raw).unwrap()).unwrap();
        file.sync_all().unwrap();
        let mut envelope: Value = serde_json::from_str(include_str!(
            "../../../fleet-core/tests/fixtures/connection-participant-wire.json"
        ))
        .unwrap();
        envelope = envelope["refused"]["claim"].clone();
        envelope["native_handoff"]["native_plan"] = manifest;
        Self {
            creation,
            intent,
            current,
            op,
            envelope,
            review,
        }
    }
    fn store(&self) -> Store<Disk> {
        Store::new(Disk {
            root: self.creation.root.clone(),
            fail: None,
        })
    }
    fn peer(&self) -> Peer {
        Peer {
            op: self.op.clone(),
            envelope: self.envelope.clone(),
            mode: Cell::new(0),
            calls: RefCell::new(vec![]),
        }
    }
    fn change_review(&self, key: &str, value: Value) {
        let mut raw: Value = serde_json::from_slice(&fs::read(&self.review).unwrap()).unwrap();
        raw[key] = value;
        fs::write(&self.review, serde_json::to_vec(&raw).unwrap()).unwrap();
    }
}
impl Reviews for Fixture {
    fn original(&self, scope: &Scope, _: &str) -> Result<OriginalReview> {
        OriginalReview::decode(&fs::read(&self.review).unwrap(), scope, |r| self.history(r))
    }
    fn history(&self, r: &FrozenPlanRef) -> core::Result<FrozenHistory> {
        FrozenHistory::decode(r, &fs::read(self.creation.native.frozen_path(r)).unwrap())
            .map_err(|_| core::Error::Invalid)
    }
    fn current(&self) -> Result<CurrentBinding> {
        Ok(self.current.clone())
    }
}
struct Peer {
    op: Value,
    envelope: Value,
    mode: Cell<u8>,
    calls: RefCell<Vec<String>>,
}
impl Transport for Peer {
    fn check(&self, s: &Scope) -> Result<()> {
        if s == &binding() {
            Ok(())
        } else {
            Err(Error::Changed)
        }
    }
    async fn read(&self, q: Query) -> Result<Reply> {
        assert!(matches!(q, Query::Operation(_)));
        self.calls.borrow_mut().push("get".into());
        Ok(Reply {
            status: 200,
            document: json!({"operation":self.op}),
        })
    }
}
impl ClaimTransport for Peer {
    fn check_original(&self, a: &Accepted) -> Result<()> {
        self.check(&a.scope)?;
        if a.credential_generation == "original-generation" {
            Ok(())
        } else {
            Err(Error::Changed)
        }
    }
    async fn handoff(&self, _: &Intent, body: &[u8]) -> PostResult {
        let mode: Value = serde_json::from_slice(body).unwrap();
        let readback = mode["mode"] == "readback";
        self.calls
            .borrow_mut()
            .push(if readback { "readback" } else { "claim" }.into());
        if !readback {
            match self.mode.get() {
                1 => return Err(PostFailure::BeforeSender(Error::Changed)),
                2 => return Err(PostFailure::PossiblySent(Error::Changed)),
                3 => return Err(PostFailure::PossiblySent(Error::Unavailable)),
                _ => (),
            }
        }
        let mut document = self.envelope.clone();
        document["read_only"] = json!(readback);
        Ok(Reply {
            status: 200,
            document,
        })
    }
}
fn driver() -> Driver<impl Fn() -> u64> {
    Driver::test(true, || NOW)
}
#[tokio::test]
async fn gates_refuse_unavailable_review_platform_and_association_before_claim() {
    for case in [
        "store",
        "platform",
        "association",
        "review",
        "native",
        "configuration",
        "source",
        "credential",
    ] {
        let mut f = Fixture::new();
        let d = Driver::test(case != "platform", || NOW);
        if case == "association" {
            f.change_review("association", Value::Null)
        }
        if case == "review" {
            let mut raw: Value = serde_json::from_slice(&f.intent.bytes().unwrap()).unwrap();
            raw["accepted"]["review_digest"] = json!("d".repeat(64));
            f.change_review("intent", json!(raw.to_string()));
        }
        match case {
            "native" => {
                f.current.scope.native_installation = "33333333-3333-4333-8333-333333333333".into()
            }
            "configuration" => f.current.configuration_ref.sha256 = "0".repeat(64),
            "source" => {
                f.op["external_refs"]["admission_binding"]["source_descriptor"]["reference"] =
                    json!("changed")
            }
            "credential" => f.current.credential_generation = "changed".into(),
            _ => (),
        }
        let peer = f.peer();
        let result = if case == "store" {
            d.advance(
                &peer,
                &f,
                &mut Store::new(Unavailable),
                &binding(),
                f.intent.accepted().operation_id.as_str(),
            )
            .await
        } else {
            d.advance(
                &peer,
                &f,
                &mut f.store(),
                &binding(),
                f.intent.accepted().operation_id.as_str(),
            )
            .await
        };
        assert_ne!(result.state, super::discovery::State::Claimed, "{case}");
        if case == "association" {
            assert_eq!(result.refusal, Some(Refusal::SourceAssociationUnproven));
        }
        assert!(!peer.calls.borrow().iter().any(|s| s == "claim"), "{case}");
    }
}
#[tokio::test]
async fn intent_crash_rechecks_gates_and_claims_once_with_original_context() {
    let f = Fixture::new();
    let peer = f.peer();
    f.store().begin(&f.intent).unwrap();
    let outcome = driver()
        .advance(
            &peer,
            &f,
            &mut f.store(),
            &binding(),
            &f.intent.accepted().operation_id,
        )
        .await;
    assert_eq!(outcome.state, super::discovery::State::Claimed);
    let invocation = outcome.invocation.unwrap();
    assert!(!invocation.read_only());
    assert_eq!(
        invocation.association().configuration_ref,
        f.current.configuration_ref
    );
    assert_eq!(
        invocation.record().part(Part::Envelope).unwrap(),
        serde_json::to_vec(&f.envelope).unwrap()
    );
    assert_eq!(&*peer.calls.borrow(), &["get", "claim"]);
}
#[tokio::test]
async fn sent_claim_crash_and_ambiguous_errors_only_read_back_even_before_expiry() {
    for mode in [0, 2, 3] {
        let f = Fixture::new();
        let peer = f.peer();
        peer.mode.set(mode);
        let mut store = Store::new(Disk {
            root: f.creation.root.clone(),
            fail: Some(Part::Envelope),
        });
        assert_eq!(
            driver()
                .advance(
                    &peer,
                    &f,
                    &mut store,
                    &binding(),
                    &f.intent.accepted().operation_id
                )
                .await
                .state,
            super::discovery::State::NeedsAttention
        );
        let outcome = Driver::test(false, || NOW)
            .advance(
                &peer,
                &f,
                &mut f.store(),
                &binding(),
                &f.intent.accepted().operation_id,
            )
            .await;
        assert!(outcome.invocation.unwrap().read_only());
        assert_eq!(&*peer.calls.borrow(), &["get", "claim", "get", "readback"]);
        assert!(f
            .store()
            .load(&f.intent)
            .unwrap()
            .part(Part::NotSent(PostTarget::Claim, 0))
            .is_none());
    }
}
#[tokio::test]
async fn not_sent_can_replay_only_within_original_ttl_and_expiry_is_read_only() {
    for expired in [false, true] {
        let f = Fixture::new();
        let peer = f.peer();
        peer.mode.set(1);
        driver()
            .advance(
                &peer,
                &f,
                &mut f.store(),
                &binding(),
                &f.intent.accepted().operation_id,
            )
            .await;
        peer.mode.set(0);
        let d = Driver::test(true, || NOW + if expired { 300000 } else { 1 });
        let outcome = d
            .advance(
                &peer,
                &f,
                &mut f.store(),
                &binding(),
                &f.intent.accepted().operation_id,
            )
            .await;
        assert_eq!(outcome.invocation.unwrap().read_only(), expired);
        assert_eq!(
            peer.calls.borrow().last().unwrap(),
            if expired { "readback" } else { "claim" }
        );
    }
}
