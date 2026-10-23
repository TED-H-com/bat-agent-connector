use bat_fleet_core::{connection_catalog::*, digest};
use serde_json::{json, Value};

const INSTALLATION: &str = "00000000-0000-4000-8000-000000000001";
fn entry(n: usize) -> Entry {
    Entry {
        id: format!("entry_{n:032x}"),
        server_id: "synthetic-server".into(),
        principal_id: "synthetic-principal".into(),
        configuration: ConfigurationRef {
            id: format!("config_{n:032x}"),
            sha256: "a".repeat(64),
        },
        connection_id: Some(format!("conn_{n:032x}")),
        state: State::Attached,
        receipt_refs: vec!["b".repeat(64)],
    }
}
fn changed(current: &ReadOutcome, change: Change) -> ReadOutcome {
    let Transition::Changed {
        catalog,
        bytes,
        sha256,
    } = transition(current, change).unwrap()
    else {
        panic!("expected change");
    };
    let previous = match current {
        ReadOutcome::Present { catalog, .. } => catalog.revision,
        _ => 0,
    };
    assert_eq!(catalog.revision, previous + 1);
    assert_eq!(sha256, digest(&bytes));
    assert_eq!(serialize(&catalog).unwrap(), bytes);
    let parsed = load(Some(&bytes));
    assert_eq!(self::catalog(&parsed), &catalog);
    assert!(matches!(
        parsed,
        ReadOutcome::Present {
            canonical: true,
            ..
        }
    ));
    parsed
}
fn catalog(current: &ReadOutcome) -> &Catalog {
    let ReadOutcome::Present { catalog, .. } = current else {
        panic!("expected present")
    };
    catalog
}
fn create() -> Change {
    Change::Create {
        installation_ref: INSTALLATION.into(),
    }
}
fn append(id: String, hash: char) -> Change {
    Change::AppendReceipts {
        id,
        receipts: vec![hash.to_string().repeat(64)],
    }
}
fn unchanged(current: &ReadOutcome, change: Change) {
    assert_eq!(transition(current, change), Ok(Transition::Unchanged));
}
fn refused(current: &ReadOutcome, change: Change, code: &'static str) {
    assert_eq!(transition(current, change), Err(code));
}
fn initial() -> ReadOutcome {
    changed(&ReadOutcome::Absent, create())
}
fn populated() -> ReadOutcome {
    changed(&initial(), Change::Add(entry(1)))
}
fn raw(current: &ReadOutcome) -> Value {
    serde_json::to_value(catalog(current)).unwrap()
}
fn read(value: &Value) -> ReadOutcome {
    load(Some(&serde_json::to_vec(value).unwrap()))
}

#[test]
fn read_outcomes_and_canonical_bytes() {
    assert_eq!(load(None), ReadOutcome::Absent);
    let present = initial();
    assert_eq!(catalog(&present).revision, 1);
    assert_eq!(catalog(&present).selected, None);
    let pretty = serde_json::to_vec_pretty(catalog(&present)).unwrap();
    assert_eq!(
        load(Some(&pretty)),
        ReadOutcome::Present {
            catalog: catalog(&present).clone(),
            canonical: false
        }
    );
    for field in ["format_version", "minimum_reader_version"] {
        let mut future = raw(&present);
        future[field] = json!(2);
        future["extra_future_field"] = json!({"new_shape": true});
        future["entries"] = json!("future shape");
        let unsupported = read(&future);
        assert_eq!(unsupported, ReadOutcome::Unsupported);
        refused(&unsupported, create(), "CATALOG_UNSUPPORTED");
    }
    for bytes in [b"".as_slice(), b"null", b"{}", b"{", b"\xff"] {
        assert!(matches!(load(Some(bytes)), ReadOutcome::Invalid(_)));
    }
}

#[test]
fn strict_keys_fields_syntax_selection_and_uniqueness() {
    let present = populated();
    let base = raw(&present);
    let bytes = serialize(catalog(&present)).unwrap();
    let duplicate = String::from_utf8(bytes)
        .unwrap()
        .replacen('{', "{\"format_version\":1,", 1);
    assert!(matches!(
        load(Some(duplicate.as_bytes())),
        ReadOutcome::Invalid(_)
    ));
    let future_duplicate = duplicate.replace("\"format_version\":1", "\"format_version\":2");
    assert!(matches!(
        load(Some(future_duplicate.as_bytes())),
        ReadOutcome::Invalid(_)
    ));
    for pointer in [
        "/extra",
        "/entries/0/extra",
        "/entries/0/configuration/extra",
    ] {
        let mut value = base.clone();
        let (parent, key) = pointer.rsplit_once('/').unwrap();
        value.pointer_mut(parent).unwrap()[key] = json!(true);
        assert!(matches!(read(&value), ReadOutcome::Invalid(_)), "{pointer}");
    }
    for (pointer, bad) in [
        ("/installation_ref", json!("not-a-uuid")),
        ("/entries/0/id", json!("entry_bad")),
        ("/entries/0/configuration/id", json!("../config")),
        ("/entries/0/configuration/sha256", json!("A".repeat(64))),
        ("/entries/0/connection_id", json!("conn_bad")),
        ("/entries/0/receipt_refs/0", json!("b".repeat(63))),
        ("/entries/0/server_id", json!("x".repeat(257))),
        ("/entries/0/principal_id", json!("bad\nidentity")),
        ("/selected", json!(entry(2).id)),
        ("/revision", json!(0)),
    ] {
        let mut value = base.clone();
        *value.pointer_mut(pointer).unwrap() = bad;
        assert!(matches!(read(&value), ReadOutcome::Invalid(_)), "{pointer}");
    }
    let mut value = base.clone();
    value.as_object_mut().unwrap().remove("selected");
    assert!(matches!(read(&value), ReadOutcome::Invalid(_)));
    value = base.clone();
    value["selected"] = json!(entry(1).id);
    value["entries"][0]["state"] = json!("detached");
    assert!(matches!(read(&value), ReadOutcome::Invalid(_)));
    for duplicate_id in [true, false] {
        let mut other = entry(2);
        if duplicate_id {
            other.id = entry(1).id;
        } else {
            other.connection_id = entry(1).connection_id;
        }
        value = base.clone();
        value["entries"]
            .as_array_mut()
            .unwrap()
            .push(json!(other.clone()));
        assert!(matches!(read(&value), ReadOutcome::Invalid(_)));
        refused(&present, Change::Add(other), "CATALOG_CONFLICT");
    }
    let mut other = entry(2);
    other.connection_id = entry(1).connection_id;
    other.principal_id = "other-principal".into();
    changed(&present, Change::Add(other));
}

#[test]
fn transitions_preserve_inactive_history_and_report_noops() {
    let mut current = populated();
    let id = entry(1).id;
    unchanged(&current, Change::Select(None));
    unchanged(&current, Change::Reattach(id.clone()));
    current = changed(&current, Change::Select(Some(id.clone())));
    unchanged(&current, Change::Select(Some(id.clone())));
    let retained = catalog(&current).entries[0].clone();
    current = changed(&current, Change::Detach(id.clone()));
    assert_eq!(catalog(&current).selected, None);
    assert_eq!(catalog(&current).entries.len(), 1);
    let mut expected = retained.clone();
    expected.state = State::Detached;
    assert_eq!(catalog(&current).entries[0], expected);
    unchanged(&current, Change::Detach(id.clone()));
    refused(
        &current,
        Change::Select(Some(id.clone())),
        "CATALOG_INVALID",
    );
    current = changed(&current, Change::Reattach(id.clone()));
    assert_eq!(catalog(&current).entries[0], retained);
    current = changed(&current, append(id.clone(), 'c'));
    assert_eq!(
        catalog(&current).entries[0].receipt_refs,
        vec!["b".repeat(64), "c".repeat(64)]
    );
    unchanged(&current, append(id, 'b'));
    let mut central = entry(2);
    central.connection_id = None;
    current = changed(&current, Change::Add(central));
    assert_eq!(catalog(&current).entries[1].connection_id, None);
    current = changed(&current, Change::Select(Some(entry(2).id)));
    current = changed(&current, Change::Select(None));
    assert!(catalog(&current)
        .entries
        .iter()
        .all(|e| e.state == State::Attached));
}

#[test]
fn bounds_refuse_without_pruning_and_overflow_is_closed() {
    let mut value = raw(&initial());
    value["entries"] = json!((0..ENTRY_BOUND).map(entry).collect::<Vec<_>>());
    let full = read(&value);
    assert_eq!(catalog(&full).entries.len(), 1000);
    refused(&full, Change::Add(entry(1000)), "CATALOG_BOUND");
    assert_eq!(catalog(&full).entries.len(), 1000);
    value["entries"]
        .as_array_mut()
        .unwrap()
        .push(json!(entry(1000)));
    assert_eq!(read(&value), ReadOutcome::Invalid("CATALOG_BOUND"));
    let mut padded = serialize(catalog(&initial())).unwrap();
    padded.resize(DOCUMENT_BOUND, b' ');
    assert!(matches!(
        load(Some(&padded)),
        ReadOutcome::Present {
            canonical: false,
            ..
        }
    ));
    padded.push(b' ');
    assert_eq!(load(Some(&padded)), ReadOutcome::Invalid("CATALOG_BOUND"));
    let mut receipts = entry(1);
    receipts.receipt_refs = (0..RECEIPT_BOUND).map(|i| format!("{i:064x}")).collect();
    let full_receipts = changed(&initial(), Change::Add(receipts));
    refused(&full_receipts, append(entry(1).id, 'f'), "CATALOG_BOUND");
    value = raw(&full);
    for row in value["entries"].as_array_mut().unwrap() {
        row["receipt_refs"] = json!(catalog(&full_receipts).entries[0].receipt_refs);
    }
    let oversized: Catalog = serde_json::from_value(value).unwrap();
    assert_eq!(serialize(&oversized), Err("CATALOG_BOUND"));
    value = raw(&populated());
    value["revision"] = json!(u64::MAX);
    let exhausted = read(&value);
    refused(
        &exhausted,
        Change::Detach(entry(1).id),
        "CATALOG_REVISION_EXHAUSTED",
    );
    unchanged(&exhausted, Change::Select(None));
    refused(
        &ReadOutcome::Absent,
        Change::Select(None),
        "CATALOG_CONFLICT",
    );
    refused(
        &ReadOutcome::Invalid("CATALOG_INVALID"),
        Change::Select(None),
        "CATALOG_INVALID",
    );
}

#[test]
fn seeded_transitions_never_remove_history_or_skip_revisions() {
    let mut current = initial();
    let mut seed = 0x9e3779b97f4a7c15u64;
    for i in 0..600 {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        let before = catalog(&current).clone();
        let id = entry((seed as usize >> 8) % 40).id;
        let change = match seed % 6 {
            0 => Change::Add(entry((seed as usize >> 8) % 40)),
            1 => Change::Detach(id),
            2 => Change::Reattach(id),
            3 => Change::Select(Some(id)),
            4 => Change::AppendReceipts {
                id,
                receipts: vec![format!("{i:064x}")],
            },
            _ => Change::Select(None),
        };
        match transition(&current, change) {
            Ok(Transition::Changed {
                catalog: after,
                bytes,
                sha256,
            }) => {
                assert_eq!(after.revision, before.revision + 1);
                assert!(after.entries.len() >= before.entries.len());
                for (old, new) in before.entries.iter().zip(&after.entries) {
                    assert_eq!(old.id, new.id);
                    assert_eq!(old.configuration, new.configuration);
                    assert!(new.receipt_refs.starts_with(&old.receipt_refs));
                }
                assert_eq!(digest(&bytes), sha256);
                current = load(Some(&bytes));
                assert_eq!(catalog(&current), &after);
            }
            Ok(Transition::Unchanged) | Err(_) => assert_eq!(catalog(&current), &before),
        }
    }
    assert!(catalog(&current).revision > 100);
}
