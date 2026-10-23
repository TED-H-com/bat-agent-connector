use super::{
    discovery::Discovery,
    tests::{binding, bootstrap, golden},
    transport::*,
    Error,
};
use crate::{bridge::Bridge, credentials::Identity};
use bat_fleet_core::connection_participant::FLEET_ACTION;
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    net::TcpListener,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};

type Intercept = Arc<Mutex<Option<Box<dyn FnOnce() + Send>>>>;
struct HttpPeer {
    endpoint: String,
    requests: Arc<Mutex<Vec<String>>>,
    stop: Arc<AtomicBool>,
    unauthorized: Arc<AtomicBool>,
    foreign: Arc<AtomicBool>,
    intercept: Intercept,
    worker: Option<thread::JoinHandle<()>>,
}
impl HttpPeer {
    fn new() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let stop = Arc::new(AtomicBool::new(false));
        let unauthorized = Arc::new(AtomicBool::new(false));
        let foreign = Arc::new(AtomicBool::new(false));
        let intercept: Intercept = Arc::new(Mutex::new(None));
        let wrong_identity = foreign.clone();
        let (log, stopped, refuse, hook) = (
            requests.clone(),
            stop.clone(),
            unauthorized.clone(),
            intercept.clone(),
        );
        let worker = thread::spawn(move || {
            while !stopped.load(Ordering::SeqCst) {
                let (mut socket, _) = match listener.accept() {
                    Ok(pair) => pair,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(e) => panic!("fixture accept failed: {e}"),
                };
                // Accepted sockets inherit nonblocking mode on macOS and Windows.
                // Only accept is polled; the bounded HTTP read below is blocking.
                socket.set_nonblocking(false).unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut raw = Vec::new();
                let mut bytes = [0; 4096];
                loop {
                    let n = socket.read(&mut bytes).unwrap();
                    assert!(n > 0);
                    raw.extend_from_slice(&bytes[..n]);
                    if let Some((headers, body)) =
                        String::from_utf8_lossy(&raw).split_once("\r\n\r\n")
                    {
                        let len = headers
                            .lines()
                            .find_map(|l| {
                                l.to_ascii_lowercase()
                                    .strip_prefix("content-length: ")
                                    .and_then(|s| s.parse::<usize>().ok())
                            })
                            .unwrap_or(0);
                        if body.len() >= len {
                            break;
                        }
                    }
                }
                let request = String::from_utf8(raw).unwrap();
                let path = request.split_whitespace().nth(1).unwrap().to_string();
                log.lock().unwrap().push(request);
                if let Some(hook) = hook.lock().unwrap().take() {
                    hook();
                }
                let mut boot = bootstrap();
                if wrong_identity.load(Ordering::SeqCst) {
                    boot["sync"]["principal_id"] = json!("2".repeat(64));
                }
                let query: std::collections::BTreeMap<_, _> =
                    url::Url::parse(&format!("http://fixture.invalid{path}"))
                        .unwrap()
                        .query_pairs()
                        .into_owned()
                        .collect();
                let unfiltered = path.split('?').next() == Some("/api/v1/operations")
                    && query.get("action").is_none_or(String::is_empty);
                let connections = path.split('?').next() == Some("/api/v1/connections");
                let (status, document) = if unfiltered || connections {
                    (422, json!({"error":{"code":"UNFILTERED_OPERATIONS"}}))
                } else if refuse.load(Ordering::SeqCst) {
                    (401, json!({"error":{"code":"UNAUTHORIZED"}}))
                } else {
                    (
                        200,
                        match path.split('?').next().unwrap() {
                            "/api/v1/capabilities" => boot["capabilities"].clone(),
                            "/api/v1/bootstrap" => boot,
                            "/api/v1/operations" => {
                                if query.get("action").map(String::as_str) == Some(FLEET_ACTION)
                                    && !query.contains_key("before")
                                {
                                    assert_eq!(
                                        path,
                                        golden()["operations_route"].as_str().unwrap()
                                    );
                                    golden()["operations"].clone()
                                } else {
                                    let mut page = golden()["operations"].clone();
                                    page["operations"] = json!([]);
                                    page
                                }
                            }
                            "/api/v1/events" => golden()["events"].clone(),
                            "/api/v1/connections/handoff" => wire()["refused"]["claim"].clone(),
                            "/api/v1/connections/receipt" => wire()["refused"]["ack"].clone(),
                            _ if path.starts_with("/api/v1/operations/op_") => {
                                golden()["operation"].clone()
                            }
                            _ => panic!("unexpected route or business mutation"),
                        },
                    )
                };
                let body = document.to_string();
                let response=format!("HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len());
                socket.write_all(response.as_bytes()).unwrap();
            }
        });
        Self {
            endpoint,
            requests,
            stop,
            unauthorized,
            foreign,
            intercept,
            worker: Some(worker),
        }
    }
    fn bridge(&self) -> Arc<Bridge> {
        let bridge = Bridge::file_fixture(&self.endpoint, "synthetic-participant-secret");
        bridge
            .state
            .lock()
            .unwrap()
            .active
            .as_mut()
            .unwrap()
            .record
            .identity = Identity {
            server_id: binding().server_id,
            principal_id: binding().principal_id,
        };
        Arc::new(bridge)
    }
}
impl Drop for HttpPeer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.worker.take().unwrap().join().unwrap();
    }
}
fn wire() -> Value {
    serde_json::from_str(include_str!(
        "../../../fleet-core/tests/fixtures/connection-participant-wire.json"
    ))
    .unwrap()
}

#[tokio::test]
async fn native_discovery_queries_never_post_or_start_a_service() {
    let peer = HttpPeer::new();
    let bridge = peer.bridge();
    let transport = NativeTransport {
        bridge: &bridge,
        scope: bridge.participant_scope().unwrap(),
    };
    let result = Discovery::default()
        .refresh(&transport, &binding(), None)
        .await;
    assert!(
        peer.requests
            .lock()
            .unwrap()
            .iter()
            .all(|r| r.starts_with("GET ")),
        "a discovery read must never issue a claim or business mutation"
    );
    assert!(
        result.is_ok(),
        "native discovery must use action-filtered operation pages"
    );
    assert_eq!(result.unwrap().operations.len(), 1);
    assert!(peer
        .requests
        .lock()
        .unwrap()
        .iter()
        .any(|r| r.starts_with(&format!(
            "GET {} ",
            golden()["operations_route"].as_str().unwrap()
        ))));
    let cursor = Cursor {
        before: json!(1.0).as_number().unwrap().clone(),
        before_id: format!("op_{:032x}", 1),
    };
    bridge
        .participant_operations(&transport.scope, FLEET_ACTION, Some(&cursor))
        .await
        .unwrap();
    assert!(peer
        .requests
        .lock()
        .unwrap()
        .last()
        .unwrap()
        .contains("before=1.0&before_id=op_"));
}

#[tokio::test]
async fn discovery_does_not_read_connections() {
    let peer = HttpPeer::new();
    let bridge = peer.bridge();
    let transport = NativeTransport {
        bridge: &bridge,
        scope: bridge.participant_scope().unwrap(),
    };
    let _ = Discovery::default()
        .refresh(&transport, &binding(), None)
        .await;
    assert!(
        peer.requests
            .lock()
            .unwrap()
            .iter()
            .all(|r| !r.starts_with("GET /api/v1/connections")),
        "discovery must not traverse unused connections"
    );
}

#[tokio::test]
async fn receipt_transport_reverifies_each_post_and_preserves_the_stored_bytes() {
    let peer = HttpPeer::new();
    let bridge = peer.bridge();
    let scope = bridge.participant_scope().unwrap();
    let body = serde_json::to_vec_pretty(&wire()["refused"]["wire"]).unwrap();
    let mut file = tempfile::NamedTempFile::new().unwrap();
    file.write_all(&body).unwrap();
    file.as_file().sync_all().unwrap();
    for _ in 0..2 {
        let persisted = std::fs::read(file.path()).unwrap();
        let reply = bridge
            .participant_receipt(&scope, &persisted)
            .await
            .unwrap();
        assert_eq!(reply.status, 200);
    }
    let requests = peer.requests.lock().unwrap();
    assert_eq!(requests.len(), 6);
    for cycle in requests.as_chunks::<3>().0 {
        assert!(cycle[0].starts_with("GET /api/v1/capabilities "));
        assert!(cycle[1].starts_with("GET /api/v1/bootstrap "));
        assert!(cycle[2].starts_with("POST /api/v1/connections/receipt "));
        let delivered = cycle[2].split_once("\r\n\r\n").unwrap().1.as_bytes();
        assert_eq!(
            bat_fleet_core::digest(delivered),
            bat_fleet_core::digest(&body)
        );
    }
}

#[tokio::test]
async fn native_transport_refuses_changed_scope_token_and_stopped_local_service() {
    for change in ["generation", "token", "stop"] {
        let peer = HttpPeer::new();
        let bridge = peer.bridge();
        let scope = bridge.participant_scope().unwrap();
        let changed = bridge.clone();
        *peer.intercept.lock().unwrap() = Some(Box::new(move || match change {
            "generation" => changed.disconnect(),
            "token" => {
                changed
                    .state
                    .lock()
                    .unwrap()
                    .active
                    .as_mut()
                    .unwrap()
                    .record
                    .token = zeroize::Zeroizing::new("changed-synthetic-token".into())
            }
            _ => {
                changed.state.lock().unwrap().managed = true;
                changed.managed_hold(None);
            }
        }));
        assert!(matches!(
            bridge
                .participant_exchange_attempt(
                    &scope,
                    "/operations/op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
                    None
                )
                .await
                .err(),
            Some(PostFailure::PossiblySent(Error::Changed))
        ));
        assert!(matches!(
            bridge
                .participant_exchange_attempt(
                    &scope,
                    "/operations/op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
                    None
                )
                .await
                .err(),
            Some(PostFailure::BeforeSender(_))
        ));
        assert_eq!(peer.requests.lock().unwrap().len(), 1);
        if change == "stop" {
            assert_eq!(
                bridge.participant_scope().err().map(|_| Error::Unavailable),
                Some(Error::Unavailable)
            );
            assert!(bridge
                .participant_operation(&scope, "op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
                .await
                .is_err());
            assert_eq!(peer.requests.lock().unwrap().len(), 1);
        }
    }
    let peer = HttpPeer::new();
    let bridge = peer.bridge();
    let transport = NativeTransport {
        bridge: &bridge,
        scope: bridge.participant_scope().unwrap(),
    };
    let mut other = binding();
    other.principal_id = "2".repeat(64);
    assert_eq!(
        Discovery::default()
            .refresh(&transport, &other, None)
            .await
            .err(),
        Some(Error::Changed)
    );
    assert!(peer.requests.lock().unwrap().is_empty());
}

#[tokio::test]
async fn native_unauthorized_response_invalidates_the_existing_connection() {
    let peer = HttpPeer::new();
    peer.unauthorized.store(true, Ordering::SeqCst);
    let bridge = peer.bridge();
    let scope = bridge.participant_scope().unwrap();
    assert_eq!(
        bridge.participant_bootstrap(&scope).await.err(),
        Some(Error::Unavailable)
    );
    assert_eq!(bridge.participant_scope().err(), Some(Error::Unavailable));
}

#[tokio::test]
async fn native_bootstrap_rejects_a_different_live_principal() {
    let peer = HttpPeer::new();
    peer.foreign.store(true, Ordering::SeqCst);
    let bridge = peer.bridge();
    let transport = NativeTransport {
        bridge: &bridge,
        scope: bridge.participant_scope().unwrap(),
    };
    assert_eq!(
        Discovery::default()
            .refresh(&transport, &binding(), None)
            .await
            .err(),
        Some(Error::Changed)
    );
    assert_eq!(peer.requests.lock().unwrap().len(), 1);
    assert_eq!(bridge.participant_scope().err(), Some(Error::Unavailable));
}
