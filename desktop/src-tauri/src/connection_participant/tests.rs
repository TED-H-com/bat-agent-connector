use super::{discovery::*, transport::*, *};
use bat_fleet_core::connection_participant::{Scope, FLEET_ACTION};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Mutex,
    },
};

pub(super) fn golden() -> Value {
    serde_json::from_str(include_str!(
        "../../tests/fixtures/connection-discovery.json"
    ))
    .unwrap()
}
pub(super) fn binding() -> Scope {
    Scope {
        server_id: "11111111-1111-4111-8111-111111111111".into(),
        principal_id: "1".repeat(64),
        native_installation: "22222222-2222-4222-8222-222222222222".into(),
        installation_ref: "desktop-a".into(),
    }
}
pub(super) fn bootstrap() -> Value {
    let mut value: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/managed-bootstrap.json"
    ))
    .unwrap();
    for identity in [
        "sync",
        "capabilities/identity",
        "capabilities/desktop_identity",
    ] {
        let path = format!("/{identity}");
        value.pointer_mut(&path).unwrap()["server_id"] = json!(binding().server_id);
        value.pointer_mut(&path).unwrap()["principal_id"] = json!(binding().principal_id);
    }
    value["snapshot"]["operations"] = golden()["bootstrap_operations"].clone();
    value["sync"] = golden()["replay_sync"].clone();
    value
}
fn id(n: usize) -> String {
    format!("op_{n:032x}")
}
struct Peer {
    originals: BTreeMap<String, Value>,
    pages: Vec<Value>,
    boots: AtomicUsize,
    gets: AtomicUsize,
    cursors: Mutex<Vec<(String, String)>>,
    gap: AtomicBool,
    changed: AtomicBool,
    event_id: Option<String>,
    actions: Mutex<Vec<String>>,
}
impl Peer {
    fn new() -> Self {
        let value = golden();
        let mut op = value["operation"]["operation"].clone();
        op["created_at"] = json!(1.0);
        Self {
            originals: BTreeMap::from([(op["operation_id"].as_str().unwrap().into(), op)]),
            pages: vec![value["operations"].clone()],
            boots: AtomicUsize::new(0),
            gets: AtomicUsize::new(0),
            cursors: Mutex::new(vec![]),
            gap: AtomicBool::new(false),
            changed: AtomicBool::new(false),
            event_id: None,
            actions: Mutex::new(vec![]),
        }
    }
}
impl Transport for Peer {
    fn check(&self, scope: &Scope) -> Result<()> {
        if self.changed.load(Ordering::SeqCst) || scope != &binding() {
            Err(Error::Changed)
        } else {
            Ok(())
        }
    }
    async fn read(&self, query: Query) -> Result<Reply> {
        let document = match query {
            Query::Bootstrap => {
                self.boots.fetch_add(1, Ordering::SeqCst);
                bootstrap()
            }
            Query::Operations { action, cursor } => {
                assert!(!action.is_empty(), "unfiltered operations are refused");
                self.actions.lock().unwrap().push(action.clone());
                if action != FLEET_ACTION {
                    let mut page = golden()["operations"].clone();
                    page["operations"] = json!([]);
                    return Ok(Reply {
                        status: 200,
                        document: page,
                    });
                }
                let mut cursors = self.cursors.lock().unwrap();
                let index = cursor.as_ref().map_or(0, |c| {
                    cursors.push((c.before.to_string(), c.before_id.clone()));
                    self.pages
                        .iter()
                        .position(|p| p["next_before_id"] == c.before_id)
                        .unwrap()
                        + 1
                });
                self.pages[index].clone()
            }
            Query::Operation(id) => {
                self.gets.fetch_add(1, Ordering::SeqCst);
                let mut reply = golden()["operation"].clone();
                reply["operation"] = self.originals.get(&id).unwrap().clone();
                reply
            }
            Query::Events(checkpoint) => {
                assert_eq!(checkpoint.cursor, 4);
                if self.gap.swap(false, Ordering::SeqCst) {
                    let mut gap = golden()["gap"].clone();
                    gap["error"]["reason"] = json!("cursor_expired");
                    return Ok(Reply {
                        status: 409,
                        document: gap,
                    });
                }
                let mut page = golden()["events"].clone();
                page["events"][0]["resource_id"] = json!(if self
                    .originals
                    .contains_key("op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
                {
                    "op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                } else {
                    self.originals.keys().next().unwrap()
                });
                if let Some(id) = &self.event_id {
                    page["events"][0]["resource_id"] = json!(id);
                }
                page
            }
        };
        Ok(Reply {
            status: 200,
            document,
        })
    }
}

#[tokio::test]
async fn discovery_reopens_cold_and_serializes_duplicate_wakes_without_a_claim() {
    let peer = Peer::new();
    assert!(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs_f64()
            > peer.originals.values().next().unwrap()["created_at"]
                .as_f64()
                .unwrap()
                + 300.0
    );
    let discovery = Discovery::default();
    let first = discovery.refresh(&peer, &binding(), None).await.unwrap();
    assert_eq!(first.operations.len(), 1);
    assert_eq!(first.operations[0].state, State::Unavailable);
    assert_eq!(
        first.operations[0].observed_revision,
        peer.originals.values().next().unwrap()["version"]
            .as_u64()
            .unwrap()
    );
    let wake = peer.originals.keys().next().unwrap();
    let scope = binding();
    let (a, b) = tokio::join!(
        discovery.refresh(&peer, &scope, Some(wake)),
        discovery.refresh(&peer, &scope, Some(wake))
    );
    assert_eq!(a.unwrap().operations.len(), 1);
    assert_eq!(b.unwrap().operations.len(), 1);
    assert_eq!(peer.gets.load(Ordering::SeqCst), 3);
    // The driver receives only a read transport, with no mutation method.
    let serialized = serde_json::to_value(first).unwrap();
    let op = serialized["operations"][0].as_object().unwrap();
    assert_eq!(
        op.keys().map(String::as_str).collect::<Vec<_>>(),
        ["handle", "observed_revision", "operation_id", "state"]
    );
    assert!(op["handle"]
        .as_str()
        .unwrap()
        .bytes()
        .all(|b| b.is_ascii_hexdigit()));
}

#[tokio::test]
async fn discovery_traverses_ties_and_a_filtered_empty_page() {
    let mut peer = Peer::new();
    let template = peer.originals.values().next().unwrap().clone();
    for n in (101..=300).chain(95..=99) {
        let mut op = template.clone();
        op["operation_id"] = json!(id(n));
        op["created_at"] = json!(1.0);
        peer.originals.insert(id(n), op);
    }
    let operations = |range: std::ops::RangeInclusive<usize>| {
        range
            .rev()
            .map(|n| peer.originals[&id(n)].clone())
            .collect::<Vec<_>>()
    };
    peer.pages = vec![
        json!({"operations":operations(101..=300),"next_before":1.0,"next_before_id":id(101)}),
        json!({"operations":[],"next_before":1.0,"next_before_id":id(100)}),
        json!({"operations":operations(95..=99),"next_before":null,"next_before_id":null}),
    ];
    let snapshot = Discovery::default()
        .refresh(&peer, &binding(), None)
        .await
        .unwrap();
    assert_eq!(snapshot.operations.len(), 206); // Bootstrap's original plus the 205 paged rows.
    assert_eq!(peer.gets.load(Ordering::SeqCst), 206);
    assert_eq!(
        *peer.cursors.lock().unwrap(),
        vec![("1.0".into(), id(101)), ("1.0".into(), id(100))]
    );
}

#[tokio::test]
async fn discovery_resnapshots_a_gap_and_filters_foreign_bindings() {
    let mut peer = Peer::new();
    peer.gap.store(true, Ordering::SeqCst);
    let mut wrong_install = peer.originals.values().next().unwrap().clone();
    wrong_install["target"]["installation_ref"] = json!("desktop-b");
    wrong_install["operation_id"] = json!(id(1));
    let mut wrong_principal = wrong_install.clone();
    wrong_principal["operation_id"] = json!(id(2));
    wrong_principal["target"]["installation_ref"] = json!("desktop-a");
    wrong_principal["external_refs"]["admission_binding"]["principal_id"] = json!("2".repeat(64));
    peer.originals.insert(id(1), wrong_install.clone());
    peer.originals.insert(id(2), wrong_principal.clone());
    peer.pages[0]["operations"]
        .as_array_mut()
        .unwrap()
        .extend([wrong_install, wrong_principal]);
    let snapshot = Discovery::default()
        .refresh(&peer, &binding(), None)
        .await
        .unwrap();
    assert_eq!(snapshot.operations.len(), 1);
    assert_eq!(peer.boots.load(Ordering::SeqCst), 2);
    assert_eq!(peer.gets.load(Ordering::SeqCst), 1);
    peer.changed.store(true, Ordering::SeqCst);
    assert_eq!(
        Discovery::default()
            .refresh(&peer, &binding(), None)
            .await
            .err(),
        Some(Error::Changed)
    );
}

#[tokio::test]
async fn discovery_uses_the_persisted_action_and_explicit_variant_not_artifact_shape() {
    for (field, value) in [
        ("kind", json!("reference_detach")),
        ("contract_version", json!(2)),
        ("action", json!("connection.reference.apply")),
    ] {
        let mut peer = Peer::new();
        for op in peer.originals.values_mut() {
            if field == "action" {
                op[field] = value.clone();
            } else {
                op["external_refs"]["admission_binding"]["native_plan"][field] = value.clone();
                op["params"]["native_plan"][field] = value.clone();
            }
        }
        let snapshot = Discovery::default()
            .refresh(&peer, &binding(), None)
            .await
            .unwrap();
        assert_eq!(snapshot.operations[0].state, State::Unsupported);
    }
}

#[tokio::test]
async fn discovery_reads_every_action_in_the_vocabulary() {
    let peer = Peer::new();
    let mut vocabulary = bat_fleet_core::connection_contract::vocabulary().clone();
    if vocabulary["v2"].is_null() {
        // Action-only projection of connection_contract.json's real v2 reference kind.
        // Exercise forward integration while the stacked source tree still has v1 only.
        vocabulary["v2"] =
            json!({"effect_kinds":{"reference_detach":{"action":"connection.reference.detach"}}});
    }
    assert!(Discovery::snapshot(&peer, &binding(), None, &vocabulary)
        .await
        .is_ok());
    let actions = peer.actions.lock().unwrap();
    assert!(actions.iter().any(|a| a == FLEET_ACTION));
    for kind in vocabulary["v2"]["effect_kinds"]
        .as_object()
        .unwrap()
        .values()
    {
        assert!(
            actions
                .iter()
                .any(|a| a == kind["action"].as_str().unwrap()),
            "every vocabulary action must be paged"
        );
    }
}

#[tokio::test]
async fn action_pages_refuse_cycles_and_more_than_200_rows() {
    for over_bound in [false, true] {
        let mut peer = Peer::new();
        if over_bound {
            peer.pages[0]["operations"] = json!(vec![peer.pages[0]["operations"][0].clone(); 201]);
        } else {
            peer.pages[0]["next_before"] = json!(1.0);
            peer.pages[0]["next_before_id"] = json!(id(1));
            peer.pages.push(peer.pages[0].clone());
        }
        assert_eq!(
            Discovery::default()
                .refresh(&peer, &binding(), None)
                .await
                .err(),
            Some(Error::Invalid)
        );
    }
}

#[tokio::test]
async fn discovery_reports_a_nonparticipant_action_seen_only_in_events_as_unsupported() {
    let mut peer = Peer::new();
    let mut op = peer.originals.values().next().unwrap().clone();
    op["operation_id"] = json!(id(77));
    op["action"] = json!("connection.reference.apply");
    peer.originals.insert(id(77), op);
    peer.event_id = Some(id(77));
    let snapshot = Discovery::default()
        .refresh(&peer, &binding(), None)
        .await
        .unwrap();
    assert_eq!(
        snapshot
            .operations
            .iter()
            .find(|op| op.operation_id == id(77))
            .unwrap()
            .state,
        State::Unsupported
    );
    assert_eq!(peer.gets.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn webview_refuses_all_connection_routes_even_after_native_transport_is_added() {
    let bridge = super::super::Bridge::file_fixture("http://127.0.0.1:9/", "synthetic-secret");
    for (method, path) in [
        ("GET", "/connections"),
        ("GET", "/connections?installation_ref=desktop-a"),
        ("GET", "/connections/conn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        ("POST", "/connections/preview"),
        ("POST", "/connections/handoff"),
        ("POST", "/connections/receipt"),
    ] {
        let request = super::super::ConnectorRequest {
            method: method.into(),
            path: path.into(),
            body: None,
            idempotency_key: None,
        };
        assert!(
            super::super::validate_request(&request).is_err(),
            "connection routes must fail before transport"
        );
        assert!(
            bridge.request(request).await.is_err(),
            "WebView connection routes remain private"
        );
    }
}
