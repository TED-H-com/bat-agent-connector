use super::*;
use bat_fleet_core::connection_participant::{request, PostTarget as T};

fn fixture() -> Fixture {
    let mut f = Fixture::new();
    f.intent = Intent::fleet_post(f.intent.accepted().clone(), &f.frozen).unwrap();
    f
}
fn fence(f: &Fixture, store: &mut Store<Disk>, target: T, slot: u8) -> Record {
    let body = store.load(&f.intent).unwrap().request(target).unwrap();
    f.save(
        store,
        Part::Post(target, slot),
        &json!({"at_ms":NOW,"body":String::from_utf8(body).unwrap()}),
    )
}
fn claimed(f: &Fixture, store: &mut Store<Disk>) {
    store.begin(&f.intent).unwrap();
    let body = request::handoff(&f.intent, request::Mode::Claim).unwrap();
    f.save(
        store,
        Part::Claim,
        &json!({"requested_at_ms":NOW,"body":String::from_utf8(body).unwrap()}),
    );
    fence(f, store, T::Claim, 0);
    f.save(store, Part::Envelope, &f.wire["refused"]["claim"]);
}
fn receipt(f: &Fixture, store: &mut Store<Disk>, outcome: &str) -> Vec<u8> {
    let body = request::receipt(
        &f.intent,
        &f.wire[outcome]["claim"],
        request::Report {
            outcome: outcome.into(),
            phase: f.wire[outcome]["wire"]["phase"].as_str().unwrap().into(),
            code: serde_json::from_value(f.wire[outcome]["wire"]["code"].clone()).unwrap(),
            roles: f.wire[outcome]["wire"]["roles"].as_array().unwrap().clone(),
        },
    )
    .unwrap();
    store.append(&f.intent, Part::Receipt(0), &body).unwrap();
    body
}
#[test]
fn legacy_bytes_never_gain_post_history_or_replay_permission() {
    let f = Fixture::new();
    let mut store = f.store();
    f.claimed(&mut store);
    let mut disk = Disk {
        root: f.root.clone(),
        fail: None,
    };
    let original = disk.snapshot(&f.intent.namespace());
    assert_eq!(
        serde_json::from_slice::<Value>(&original[0].1).unwrap()["format_version"],
        1
    );
    let newer = Intent::fleet_post(f.intent.accepted().clone(), &f.frozen).unwrap();
    assert_eq!(store.begin(&newer).err(), Some(Error::Conflict));
    assert!(store
        .append(
            &f.intent,
            Part::NotSent(T::Claim, 0),
            br#"{"before_sender":true}"#
        )
        .is_err());
    assert_eq!(disk.snapshot(&f.intent.namespace()), original);
    assert_eq!(
        store.load(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Readback
    );
}
#[test]
fn claim_fence_crashes_read_back_and_only_durable_not_sent_can_replay() {
    let f = fixture();
    let mut store = f.store();
    assert_eq!(
        store.begin(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Claim
    );
    let body = request::handoff(&f.intent, request::Mode::Claim).unwrap();
    f.save(
        &mut store,
        Part::Claim,
        &json!({"requested_at_ms":NOW,"body":String::from_utf8(body).unwrap()}),
    );
    assert_eq!(
        store.load(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Claim
    );
    assert!(store
        .append(
            &f.intent,
            Part::Envelope,
            &serde_json::to_vec(&f.wire["refused"]["claim"]).unwrap()
        )
        .is_err());
    for slot in 0..3 {
        fence(&f, &mut store, T::Claim, slot);
        assert_eq!(
            f.store().load(&f.intent).unwrap().recover(NOW + 1).unwrap(),
            Next::Readback
        );
        let mut faulted = Store::new(Disk {
            root: f.root.clone(),
            fail: Some((Part::NotSent(T::Claim, slot), false)),
        });
        assert_eq!(
            faulted
                .append(
                    &f.intent,
                    Part::NotSent(T::Claim, slot),
                    br#"{"before_sender":true}"#
                )
                .err(),
            Some(Error::Unavailable)
        );
        assert_eq!(
            f.store().load(&f.intent).unwrap().recover(NOW + 1).unwrap(),
            Next::Readback
        );
        assert!(store
            .append(&f.intent, Part::Post(T::Claim, slot + 1), br#"{}"#)
            .is_err());
        f.save(
            &mut store,
            Part::NotSent(T::Claim, slot),
            &json!({"before_sender":true}),
        );
        let next = if slot == 2 {
            Next::RetryExhausted
        } else {
            Next::ReplayClaim
        };
        assert_eq!(
            f.store().load(&f.intent).unwrap().recover(NOW + 1).unwrap(),
            next
        );
    }
    assert_eq!(
        store
            .load(&f.intent)
            .unwrap()
            .recover(NOW + 300000)
            .unwrap(),
        Next::Readback
    );
    assert!(store
        .append(&f.intent, Part::Post(T::Claim, 3), b"{}")
        .is_err());
}
#[test]
fn delivery_fences_preserve_exact_bytes_and_exhaust_without_deleting() {
    let f = fixture();
    let mut store = f.store();
    claimed(&f, &mut store);
    assert_eq!(
        store.load(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Executor { read_only: false }
    );
    assert!(store
        .load(&f.intent)
        .unwrap()
        .request(T::Receipt(0))
        .is_err());
    assert_eq!(
        store
            .load(&f.intent)
            .unwrap()
            .recover(NOW + 300000)
            .unwrap(),
        Next::Executor { read_only: true }
    );
    let sentinels = ["artifact.json", "catalog.json", "selection.json"];
    for name in sentinels {
        fs::write(f.root.join(name), b"independent bytes").unwrap();
    }
    let body = receipt(&f, &mut store, "refused");
    assert_eq!(
        store.load(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Redeliver {
            index: 0,
            body: body.clone()
        }
    );
    let wrong = json!({"at_ms":NOW,"body":format!("{} ",String::from_utf8(body.clone()).unwrap())});
    assert!(store
        .append(
            &f.intent,
            Part::Post(T::Receipt(0), 0),
            &serde_json::to_vec(&wrong).unwrap()
        )
        .is_err());
    for slot in 0..3 {
        fence(&f, &mut store, T::Receipt(0), slot);
        assert_eq!(
            f.store().load(&f.intent).unwrap().recover(NOW).unwrap(),
            Next::Readback
        );
        f.save(
            &mut store,
            Part::NotSent(T::Receipt(0), slot),
            &json!({"before_sender":true}),
        );
        assert_eq!(
            store.load(&f.intent).unwrap().recover(NOW).unwrap(),
            if slot == 2 {
                Next::RetryExhausted
            } else {
                Next::RetryDelivery { index: 0 }
            }
        );
        assert_eq!(
            store
                .load(&f.intent)
                .unwrap()
                .request(T::Receipt(0))
                .unwrap(),
            body
        );
    }
    assert!(!store
        .load(&f.intent)
        .unwrap()
        .part(Part::Post(T::Receipt(0), 0))
        .unwrap()
        .is_empty());
    for name in sentinels {
        assert_eq!(fs::read(f.root.join(name)).unwrap(), b"independent bytes");
    }
}
#[test]
fn acknowledgements_keep_the_real_source_and_missing_receipts_do_not_ack() {
    for source in ["direct", "readback"] {
        let f = fixture();
        let mut store = f.store();
        claimed(&f, &mut store);
        receipt(&f, &mut store, "refused");
        let ack = json!({"source":source,"response":f.wire["refused"][if source=="direct" {"ack"} else {"settled_readback"}]});
        if source == "direct" {
            assert!(store
                .append(
                    &f.intent,
                    Part::Acknowledgement(0),
                    &serde_json::to_vec(&ack).unwrap()
                )
                .is_err());
            fence(&f, &mut store, T::Receipt(0), 0);
        }
        for response in [
            &f.wire["refused"]["readback"],
            &f.wire["refused"][if source == "direct" {
                "settled_readback"
            } else {
                "ack"
            }],
        ] {
            assert!(store
                .append(
                    &f.intent,
                    Part::Acknowledgement(0),
                    &serde_json::to_vec(&json!({"source":source,"response":response})).unwrap()
                )
                .is_err());
        }
        let mut wrong = ack.clone();
        let receipt = if source == "direct" {
            &mut wrong["response"]["receipt"]
        } else {
            &mut wrong["response"]["native_handoff"]["receipt"]
        };
        receipt["receipt_ref"] = json!(format!("receipt_{}", "0".repeat(64)));
        assert!(store
            .append(
                &f.intent,
                Part::Acknowledgement(0),
                &serde_json::to_vec(&wrong).unwrap()
            )
            .is_err());
        f.save(&mut store, Part::Acknowledgement(0), &ack);
        assert_eq!(
            f.store().load(&f.intent).unwrap().recover(NOW).unwrap(),
            Next::Done
        );
        assert_eq!(
            store
                .load(&f.intent)
                .unwrap()
                .part(Part::Acknowledgement(0))
                .unwrap(),
            serde_json::to_vec(&ack).unwrap()
        );
    }
}
#[test]
fn uncertain_has_only_one_definitive_successor_with_source_aware_ack() {
    let f = fixture();
    let mut store = f.store();
    claimed(&f, &mut store);
    receipt(&f, &mut store, "uncertain");
    f.save(
        &mut store,
        Part::Acknowledgement(0),
        &json!({"source":"readback","response":f.wire["uncertain"]["settled_readback"]}),
    );
    assert_eq!(
        store.load(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Executor { read_only: true }
    );
    assert!(store
        .append(
            &f.intent,
            Part::Receipt(1),
            &serde_json::to_vec(&f.wire["uncertain"]["wire"]).unwrap()
        )
        .is_err());
    f.save(&mut store,Part::Attempt(0),&json!({"abandonment":{"reference":"original-decision","recorded_at_unix_ms":NOW},"journal_digest":"d".repeat(64)}));
    f.save(
        &mut store,
        Part::Receipt(1),
        &f.wire["uncertain"]["definitive"],
    );
    fence(&f, &mut store, T::Receipt(1), 0);
    f.save(
        &mut store,
        Part::Acknowledgement(1),
        &json!({"source":"direct","response":f.wire["uncertain"]["definitive_ack"]}),
    );
    assert_eq!(
        store.load(&f.intent).unwrap().recover(NOW).unwrap(),
        Next::Done
    );
}
#[test]
fn production_request_bytes_match_checked_in_fixture() {
    let f = fixture();
    let mut rows = serde_json::Map::new();
    rows.insert(
        "claim".into(),
        json!(
            String::from_utf8(request::handoff(&f.intent, request::Mode::Claim).unwrap()).unwrap()
        ),
    );
    rows.insert(
        "readback".into(),
        json!(
            String::from_utf8(request::handoff(&f.intent, request::Mode::Readback).unwrap())
                .unwrap()
        ),
    );
    let mut store = f.store();
    claimed(&f, &mut store);
    let body = receipt(&f, &mut store, "refused");
    rows.insert(
        "receipt".into(),
        json!(String::from_utf8(body.clone()).unwrap()),
    );
    let pretty = serde_json::to_vec_pretty(&f.wire["refused"]["wire"]).unwrap();
    assert_eq!(request::stored_receipt(&pretty).unwrap(), pretty);
    assert_eq!(
        store
            .load(&f.intent)
            .unwrap()
            .request(T::Receipt(0))
            .unwrap(),
        body
    );
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/participant-requests.json");
    let bytes = serde_json::to_vec_pretty(&rows).unwrap();
    if std::env::var("BATC_UPDATE_PARTICIPANT_REQUESTS").as_deref() == Ok("1") {
        fs::write(&path, &bytes).unwrap();
    }
    assert_eq!(fs::read(path).unwrap(), bytes);
}
