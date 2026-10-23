use super::*;
use crate::bridge::connection_participant::{delivery, discovery::State};
struct DeliveryPeer {
    peer: Peer,
    evidence: RefCell<Value>,
    bodies: RefCell<Vec<Vec<u8>>>,
    failure: Cell<u8>,
}
impl Transport for DeliveryPeer {
    fn check(&self, s: &Scope) -> Result<()> {
        self.peer.check(s)
    }
    async fn read(&self, q: Query) -> Result<Reply> {
        self.peer.read(q).await
    }
}
impl ClaimTransport for DeliveryPeer {
    fn check_original(&self, a: &Accepted) -> Result<()> {
        self.peer.check_original(a)
    }
    async fn handoff(&self, i: &Intent, body: &[u8]) -> PostResult {
        let mut r = self.peer.handoff(i, body).await?;
        r.document["native_handoff"]["receipt"] = self.evidence.borrow().clone();
        Ok(r)
    }
}
impl ReceiptTransport for DeliveryPeer {
    async fn receipt(&self, _: &Intent, body: &[u8]) -> PostResult {
        if self.failure.get() == 1 {
            return Err(PostFailure::BeforeSender(Error::Changed));
        }
        self.bodies.borrow_mut().push(body.to_vec());
        let mut receipt: Value = serde_json::from_slice(body).unwrap();
        receipt.as_object_mut().unwrap().remove("challenge");
        receipt["receipt_ref"] = json!(format!(
            "receipt_{}",
            digest(&serde_json::to_vec(&receipt).unwrap())
        ));
        *self.evidence.borrow_mut() = receipt.clone();
        match self.failure.get() {
            2 => Err(PostFailure::PossiblySent(Error::Changed)),
            3 => Err(PostFailure::PossiblySent(Error::Unavailable)),
            _ => Ok(Reply {
                status: 200,
                document: json!({"receipt":receipt,"replayed":false}),
            }),
        }
    }
}
impl Fixture {
    async fn claimed_delivery(&self) -> DeliveryPeer {
        let peer = DeliveryPeer {
            peer: self.peer(),
            evidence: RefCell::new(Value::Null),
            bodies: RefCell::new(vec![]),
            failure: Cell::new(0),
        };
        assert_eq!(
            driver()
                .advance(
                    &peer.peer,
                    self,
                    &mut self.store(),
                    &binding(),
                    &self.intent.accepted().operation_id
                )
                .await
                .state,
            State::Claimed
        );
        peer
    }
    fn receipt(&self, outcome: &str) -> Vec<u8> {
        let roles = self.envelope["native_handoff"]["native_plan"]["artifacts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| {
                let observation = if a["before_digest"].is_null() {
                    "absent"
                } else {
                    "present"
                };
                json!({
                    "role": a["role"],
                    "before_digest": a["before_digest"],
                    "after_digest": a["before_digest"],
                    "observation": observation
                })
            })
            .collect();
        let body = core::request::receipt(
            &self.intent,
            &self.envelope,
            core::request::Report {
                outcome: outcome.into(),
                phase: if outcome == "uncertain" {
                    "publishing"
                } else {
                    "preflight"
                }
                .into(),
                code: Some("LOCAL_GATE_UNPROVEN".into()),
                roles,
            },
        )
        .unwrap();
        // Whitespace belongs to the original immutable bytes, too.
        serde_json::to_vec_pretty(&serde_json::from_slice::<Value>(&body).unwrap()).unwrap()
    }
}
#[tokio::test]
async fn receipt_must_be_durable_and_first_delivery_preserves_original_bytes() {
    let f = Fixture::new();
    let peer = f.claimed_delivery().await;
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::Claimed
    );
    assert!(peer.bodies.borrow().is_empty());
    assert_eq!(
        delivery::advance(&peer, &mut Store::new(Unavailable), &f.intent, NOW).await,
        State::Unavailable
    );
    let business: Vec<_> = f
        .creation
        .native
        .receipt
        .slots
        .iter()
        .map(|s| (s.canonical_path.clone(), fs::read(&s.canonical_path).ok()))
        .collect();
    let body = f.receipt("refused");
    f.store()
        .append(&f.intent, Part::Receipt(0), &body)
        .unwrap();
    let wrong = [body.clone(), b" ".to_vec()].concat();
    assert!(f
        .store()
        .append(&f.intent, Part::Receipt(0), &wrong)
        .is_err());
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::Delivered
    );
    assert_eq!(*peer.bodies.borrow(), vec![body]);
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::Delivered
    );
    assert_eq!(peer.bodies.borrow().len(), 1);
    for (path, bytes) in business {
        assert_eq!(fs::read(path).ok(), bytes);
    }
}
#[tokio::test]
async fn delivery_fence_precedes_sender_and_only_not_sent_retries() {
    let f = Fixture::new();
    let peer = f.claimed_delivery().await;
    let body = f.receipt("refused");
    f.store()
        .append(&f.intent, Part::Receipt(0), &body)
        .unwrap();
    let target = PostTarget::Receipt(0);
    let mut fault = Store::new(Disk {
        root: f.creation.root.clone(),
        fail: Some(Part::Post(target, 0)),
    });
    assert_eq!(
        delivery::advance(&peer, &mut fault, &f.intent, NOW).await,
        State::Unavailable
    );
    assert!(peer.bodies.borrow().is_empty());
    peer.failure.set(1);
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::NeedsAttention
    );
    assert!(f
        .store()
        .load(&f.intent)
        .unwrap()
        .part(Part::NotSent(target, 0))
        .is_some());
    peer.failure.set(0);
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::Delivered
    );
    assert_eq!(*peer.bodies.borrow(), vec![body]);
}
#[tokio::test]
async fn accepted_delivery_ack_crash_and_ambiguous_errors_read_back_without_resend() {
    for failure in [0, 2, 3] {
        let f = Fixture::new();
        let peer = f.claimed_delivery().await;
        peer.failure.set(failure);
        let body = f.receipt("refused");
        f.store()
            .append(&f.intent, Part::Receipt(0), &body)
            .unwrap();
        let mut fault = Store::new(Disk {
            root: f.creation.root.clone(),
            fail: Some(Part::Acknowledgement(0)),
        });
        assert_ne!(
            delivery::advance(&peer, &mut fault, &f.intent, NOW).await,
            State::Delivered
        );
        assert_eq!(
            delivery::advance(&peer, &mut f.store(), &f.intent, NOW + 300001).await,
            State::Delivered
        );
        assert_eq!(*peer.bodies.borrow(), vec![body]);
        assert_eq!(
            &*peer.peer.calls.borrow(),
            &["get", "claim", "get", "readback"]
        );
        let record = f.store().load(&f.intent).unwrap();
        let ack: Value =
            serde_json::from_slice(record.part(Part::Acknowledgement(0)).unwrap()).unwrap();
        assert_eq!(ack["source"], "readback");
        assert!(ack["response"].get("replayed").is_none());
        assert_eq!(
            ack["response"]["native_handoff"]["receipt"],
            *peer.evidence.borrow()
        );
    }
}
#[tokio::test]
async fn fence_without_not_sent_and_missing_or_mismatched_readback_stay_uncertain() {
    let f = Fixture::new();
    let peer = f.claimed_delivery().await;
    let body = f.receipt("refused");
    f.store()
        .append(&f.intent, Part::Receipt(0), &body)
        .unwrap();
    // Crash after fence, before sender or a positive NotSent observation.
    f.store()
        .append(
            &f.intent,
            Part::Post(PostTarget::Receipt(0), 0),
            &serde_json::to_vec(&json!({"at_ms":NOW,"body":String::from_utf8(body).unwrap()}))
                .unwrap(),
        )
        .unwrap();
    for evidence in [Value::Null, json!({"receipt_ref":"foreign"})] {
        *peer.evidence.borrow_mut() = evidence;
        assert_eq!(
            delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
            State::NeedsAttention
        );
        assert!(f
            .store()
            .load(&f.intent)
            .unwrap()
            .part(Part::Acknowledgement(0))
            .is_none());
    }
    assert!(peer.bodies.borrow().is_empty());
}
#[tokio::test]
async fn uncertain_ack_is_read_only_and_has_only_one_definitive_successor() {
    let f = Fixture::new();
    let peer = f.claimed_delivery().await;
    let body = f.receipt("uncertain");
    f.store()
        .append(&f.intent, Part::Receipt(0), &body)
        .unwrap();
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::NeedsAttention
    );
    assert!(f
        .store()
        .append(&f.intent, Part::Receipt(1), &body)
        .is_err());
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::NeedsAttention
    );
    let definitive = f.receipt("refused");
    f.store()
        .append(&f.intent, Part::Receipt(1), &definitive)
        .unwrap();
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::Delivered
    );
    assert!(f
        .store()
        .append(&f.intent, Part::Receipt(1), &body)
        .is_err());
    assert!(f
        .store()
        .append(&f.intent, Part::Receipt(2), &definitive)
        .is_err());
    assert_eq!(peer.bodies.borrow().len(), 2);
}
#[tokio::test]
async fn delivery_not_sent_observation_crash_never_permits_another_post() {
    let f = Fixture::new();
    let peer = f.claimed_delivery().await;
    peer.failure.set(1);
    f.store()
        .append(&f.intent, Part::Receipt(0), &f.receipt("refused"))
        .unwrap();
    let mut fault = Store::new(Disk {
        root: f.creation.root.clone(),
        fail: Some(Part::NotSent(PostTarget::Receipt(0), 0)),
    });
    assert_eq!(
        delivery::advance(&peer, &mut fault, &f.intent, NOW).await,
        State::Unavailable
    );
    peer.failure.set(0);
    assert_eq!(
        delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
        State::NeedsAttention
    );
    assert!(peer.bodies.borrow().is_empty());
    assert_eq!(peer.peer.calls.borrow().last().unwrap(), "readback");
}
#[tokio::test]
async fn driver_composes_delivery_status_without_a_second_handoff() {
    let f = Fixture::new();
    let peer = f.claimed_delivery().await;
    f.store()
        .append(&f.intent, Part::Receipt(0), &f.receipt("refused"))
        .unwrap();
    let outcome = driver()
        .advance(
            &peer,
            &f,
            &mut f.store(),
            &binding(),
            &f.intent.accepted().operation_id,
        )
        .await;
    assert_eq!(outcome.state, State::Delivered);
    assert!(outcome.invocation.is_none());
    assert_eq!(&*peer.peer.calls.borrow(), &["get", "claim", "get"]);
}
#[tokio::test]
async fn delivery_preserves_unsupported_and_corrupt_store_classification_without_sending() {
    for future in [false, true] {
        let f = Fixture::new();
        let peer = f.claimed_delivery().await;
        let path = f
            .creation
            .root
            .join(f.intent.namespace().relative_path())
            .join(Part::Intent.filename().unwrap());
        let bytes = if future {
            let mut intent: Value = serde_json::from_slice(&f.intent.bytes().unwrap()).unwrap();
            intent["accepted"]["kind"] = json!("reference_detach");
            serde_json::to_vec(&intent).unwrap()
        } else {
            b"corrupt".to_vec()
        };
        fs::write(path, bytes).unwrap();
        assert_eq!(
            delivery::advance(&peer, &mut f.store(), &f.intent, NOW).await,
            if future {
                State::Unsupported
            } else {
                State::NeedsAttention
            }
        );
        assert!(peer.bodies.borrow().is_empty());
    }
}
