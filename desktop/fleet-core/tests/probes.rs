//! Independent temporary TLS/WebSocket/HTTP peers; never installed/live Fleet IO.
use base64::{engine::general_purpose::STANDARD, Engine};
use bat_fleet_core::{digest, ownership::ProbeGeneration, probe::*};
use serde_json::{json, Value};
use sha1::{Digest, Sha1};
use std::{
    net::SocketAddrV4,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt},
    net::TcpListener,
    task::JoinHandle,
};

const TOKEN: &str = "fixture-token-never-log";
// macOS assigns 127.0.0.1, not every address in 127/8. Ephemeral ports isolate peers.
const LOOPBACK: &str = "127.0.0.1:0";
fn generation() -> ProbeGeneration {
    ProbeGeneration {
        epoch: "test-epoch".into(),
        configuration_binding: "test-binding".into(),
        selection_revision: "test-selection".into(),
        generation: 7,
    }
}
fn endpoint(listener: &TcpListener) -> SocketAddrV4 {
    match listener.local_addr().unwrap() {
        std::net::SocketAddr::V4(a) => a,
        _ => unreachable!(),
    }
}
fn bat_config(endpoint: SocketAddrV4, pin: String) -> BatProbeConfig {
    BatProbeConfig {
        endpoint,
        certificate_pin: pin,
        remote_profile_id: "exact-profile".into(),
        required_workspace_ids: vec!["工程-ws".into()],
        minimum_version: "0.3.10".into(),
        client_info: json!({"name":"fleet-native-fixture"}),
    }
}
fn connector_config(endpoint: SocketAddrV4) -> ConnectorProbeConfig {
    ConnectorProbeConfig {
        endpoint,
        supported_api_versions: vec![1],
        minimum_contract_version: "2026-10-08".into(),
        required_features: vec!["operations".into()],
    }
}
async fn header<S: AsyncRead + Unpin>(s: &mut S) -> std::io::Result<String> {
    let mut bytes = Vec::new();
    while !bytes.ends_with(b"\r\n\r\n") {
        let mut b = [0];
        s.read_exact(&mut b).await?;
        bytes.push(b[0]);
        assert!(bytes.len() <= 65536);
    }
    Ok(String::from_utf8(bytes).unwrap())
}
async fn client_frame<S: AsyncRead + Unpin>(s: &mut S) -> std::io::Result<(u8, Vec<u8>)> {
    let mut h = [0; 2];
    s.read_exact(&mut h).await?;
    assert_eq!(h[0] & 128, 128);
    assert_eq!(h[1] & 128, 128, "client frames MUST be masked");
    let len = match h[1] & 127 {
        126 => s.read_u16().await? as usize,
        127 => s.read_u64().await? as usize,
        n => n as usize,
    };
    assert!(len <= 16 * 1024 * 1024);
    let mut mask = [0; 4];
    s.read_exact(&mut mask).await?;
    let mut bytes = vec![0; len];
    s.read_exact(&mut bytes).await?;
    for (i, b) in bytes.iter_mut().enumerate() {
        *b ^= mask[i % 4];
    }
    Ok((h[0] & 15, bytes))
}
fn server_frame(fin: bool, op: u8, bytes: &[u8]) -> Vec<u8> {
    let mut out = vec![(if fin { 128 } else { 0 }) | op];
    match bytes.len() {
        0..=125 => out.push(bytes.len() as u8),
        126..=65535 => {
            out.push(126);
            out.extend((bytes.len() as u16).to_be_bytes());
        }
        _ => {
            out.push(127);
            out.extend((bytes.len() as u64).to_be_bytes());
        }
    }
    out.extend_from_slice(bytes);
    out
}
async fn send<S: AsyncWrite + Unpin>(s: &mut S, value: &Value) -> std::io::Result<()> {
    s.write_all(&server_frame(true, 1, &serde_json::to_vec(value).unwrap()))
        .await?;
    s.flush().await
}
struct Peer {
    config: BatProbeConfig,
    frames: Arc<Mutex<Vec<Value>>>,
    task: JoinHandle<()>,
}
#[derive(Debug)]
struct FixtureCertificate(Arc<rustls::sign::CertifiedKey>);
impl rustls::server::ResolvesServerCert for FixtureCertificate {
    fn resolve(
        &self,
        _: rustls::server::ClientHello<'_>,
    ) -> Option<Arc<rustls::sign::CertifiedKey>> {
        Some(self.0.clone())
    }
}
impl Peer {
    async fn finish(self) -> Vec<Value> {
        tokio::time::timeout(Duration::from_secs(2), self.task)
            .await
            .unwrap()
            .unwrap();
        Arc::try_unwrap(self.frames).unwrap().into_inner().unwrap()
    }
}
async fn peer(mode: &'static str) -> Peer {
    peer_with_workspace_signal(mode, None).await
}
async fn peer_with_workspace_signal(
    mode: &'static str,
    workspace_signal: Option<tokio::sync::oneshot::Sender<()>>,
) -> Peer {
    let listener = TcpListener::bind(LOOPBACK).await.unwrap();
    let addr = endpoint(&listener);
    let cert = rcgen::generate_simple_self_signed(vec!["fixture.invalid".into()]).unwrap();
    let pin = digest(cert.cert.der());
    let tls = rustls::ServerConfig::builder_with_provider(Arc::new(
        rustls::crypto::ring::default_provider(),
    ))
    .with_protocol_versions(&[&rustls::version::TLS12])
    .unwrap()
    .with_no_client_auth();
    let key = if mode == "wrong_key" {
        rcgen::KeyPair::generate().unwrap().serialize_der()
    } else {
        cert.signing_key.serialize_der()
    };
    // Bypass the server builder's cert/key consistency check only in this
    // independent fixture: the client must verify the TLS handshake signature.
    let signing_key = rustls::crypto::ring::sign::any_supported_type(
        &rustls::pki_types::PrivatePkcs8KeyDer::from(key).into(),
    )
    .unwrap();
    let tls = tls.with_cert_resolver(Arc::new(FixtureCertificate(Arc::new(
        rustls::sign::CertifiedKey::new(vec![cert.cert.der().clone()], signing_key),
    ))));
    let frames = Arc::new(Mutex::new(Vec::new()));
    let captured = frames.clone();
    let task = tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        tcp.set_nodelay(true).unwrap();
        if mode == "silent_tls" {
            tokio::time::sleep(Duration::from_millis(400)).await;
            return;
        }
        if mode == "plain_tcp" {
            drop(tcp);
            return;
        }
        let Ok(mut s) = tokio_rustls::TlsAcceptor::from(Arc::new(tls))
            .accept(tcp)
            .await
        else {
            return;
        };
        let Ok(request) = header(&mut s).await else {
            return;
        };
        assert!(request.starts_with("GET / HTTP/1.1\r\n"));
        let key = request
            .lines()
            .find_map(|line| line.strip_prefix("Sec-WebSocket-Key: "))
            .unwrap()
            .trim();
        let accept = STANDARD.encode(Sha1::digest(format!(
            "{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        )));
        let upgrade=match mode { "bad_upgrade"=>"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: wrong\r\n\r\n".into(), "long_upgrade"=>format!("HTTP/1.1 101 Switching Protocols\r\nX-Pad: {}", "X".repeat(17000)), _=>format!("HTTP/1.1 101 Switching Protocols\r\nUpgrade: WebSocket\r\nConnection: keep-alive, Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n") };
        if s.write_all(upgrade.as_bytes()).await.is_err() {
            return;
        }
        s.flush().await.unwrap();
        let Ok((op, bytes)) = client_frame(&mut s).await else {
            return;
        };
        assert_eq!(op, 1);
        let auth: Value = serde_json::from_slice(&bytes).unwrap();
        captured.lock().unwrap().push(auth.clone());
        assert_eq!(auth["type"], "auth");
        assert!(auth["token"].as_str().unwrap().starts_with(TOKEN));
        assert_eq!(auth["protocols"], json!(["bat-remote/v2"]));
        assert_eq!(auth["compression"], json!(["none"]));
        if mode == "silent_auth" {
            tokio::time::sleep(Duration::from_millis(400)).await;
            return;
        }
        if mode == "secret_invalid_json" {
            s.write_all(&server_frame(true, 1, b"fixture-secret-invalid-json"))
                .await
                .unwrap();
            return;
        }
        if mode == "binary" {
            s.write_all(&server_frame(true, 2, b"not-text"))
                .await
                .unwrap();
            return;
        }
        if mode == "oversized" {
            s.write_all(&[0x81, 127, 0, 0, 0, 0, 1, 0, 0, 1])
                .await
                .unwrap();
            return;
        }
        if mode == "auth_event_limit" {
            for _ in 0..100 {
                send(&mut s, &json!({"type":"event", "id":"other"}))
                    .await
                    .unwrap();
            }
            return;
        }
        let mut reply = json!({"type":"auth-result","id":auth["id"],"result":true,"protocol":"bat-remote/v2","compression":"none","serverVersion":"0.3.10"});
        match mode {
            "auth_refused" => reply["result"] = json!(false),
            "auth_error" => reply["error"] = json!("private auth detail"),
            "auth_string" => reply["result"] = json!("true"),
            "old_version" => reply["serverVersion"] = json!("0.3.9"),
            "bad_version" => reply["serverVersion"] = json!("fixture-secret-version"),
            "padded_version" => {
                reply["serverVersion"] = json!(format!("{}.3.10", "0".repeat(50000)))
            }
            "wrong_protocol" => reply["protocol"] = json!("bat-remote/v1"),
            "compression" => reply["compression"] = json!("gzip"),
            "wrong_id" => reply["id"] = json!("wrong"),
            _ => (),
        }
        if mode == "events" {
            send(&mut s, &json!({"type":"event","id":"other"}))
                .await
                .unwrap();
        }
        if mode == "total_deadline" {
            // This case uses a paused clock: real TLS/auth setup consumes no
            // test budget. Spend 90 ms before acknowledging authentication.
            tokio::time::advance(Duration::from_millis(90)).await;
        }
        send(&mut s, &reply).await.unwrap();
        let Ok((op, bytes)) = client_frame(&mut s).await else {
            return;
        };
        assert_eq!(op, 1);
        let invoke: Value = serde_json::from_slice(&bytes).unwrap();
        captured.lock().unwrap().push(invoke.clone());
        assert_eq!(invoke["type"], "invoke");
        assert_eq!(invoke["channel"], "workspace:load");
        assert_eq!(invoke["params"], json!({"profileId":"exact-profile"}));
        assert!(invoke.get("contextId").is_none());
        assert!(invoke.get("windowId").is_none());
        if mode == "total_deadline" {
            workspace_signal.unwrap().send(()).unwrap();
            tokio::time::sleep(Duration::from_millis(90)).await;
        }
        let doc = match mode {
            "duplicate_ws" => json!({"workspaces":[{"id":"工程-ws"},{"id":"工程-ws"}]}),
            "wrong_case" => json!({"workspaces":[{"id":"工程-WS"}]}),
            "empty_ws" => json!({"workspaces":[]}),
            "wrong_shape" => json!({"workspaces":{}}),
            "large" => json!({"workspaces":[{"id":"工程-ws","pad":"x".repeat(700000)}]}),
            _ => json!({"workspaces":[{"id":"工程-ws"}]}),
        };
        let reply = match mode {
            "invoke_error" => {
                json!({"type":"invoke-error","id":invoke["id"],"error":"secret failure"})
            }
            "null_workspace" => json!({"type":"invoke-result","id":invoke["id"],"result":null}),
            "bad_workspace_json" => {
                json!({"type":"invoke-result","id":invoke["id"],"result":"fixture-secret-json"})
            }
            _ => json!({"type":"invoke-result","id":invoke["id"],"result":doc.to_string()}),
        };
        if mode == "fragmented" {
            let bytes = serde_json::to_vec(&reply).unwrap();
            for (index, byte) in bytes.iter().enumerate() {
                // Every UTF8 byte is its own fragment, with an intervening ping.
                s.write_all(&server_frame(
                    index == bytes.len() - 1,
                    if index == 0 { 1 } else { 0 },
                    &[*byte],
                ))
                .await
                .unwrap();
                if index != bytes.len() - 1 {
                    s.write_all(&server_frame(true, 9, b"p")).await.unwrap();
                    s.flush().await.unwrap();
                    let (op, payload) = client_frame(&mut s).await.unwrap();
                    assert_eq!((op, payload), (10, b"p".to_vec()));
                }
            }
            s.flush().await.unwrap();
        } else {
            let _ = send(&mut s, &reply).await;
        }
    });
    Peer {
        config: bat_config(addr, pin),
        frames,
        task,
    }
}
async fn run(peer: &Peer, token: String, budget: Duration) -> ProbeObservation {
    bat_probe(
        &peer.config,
        ProbeCredential::new(token),
        generation(),
        1000,
        budget,
    )
    .await
}
#[tokio::test]
async fn pinned_self_signed_tls_exact_auth_profile_workspace_and_fragmented_unicode() {
    for mode in ["ok", "events", "fragmented", "large", "padded_version"] {
        let peer = peer(mode).await;
        let out = run(&peer, TOKEN.into(), Duration::from_secs(3)).await;
        assert_eq!(out.code, None, "{mode}: {out:?}");
        assert_eq!(out.version.as_deref(), Some("0.3.10"));
        assert_eq!(
            readiness(true, ProbeKind::Bat, Some(&out), &generation(), 1000).level,
            Level::Ready
        );
        assert_eq!(peer.finish().await.len(), 2);
    }
}
#[tokio::test]
async fn auth_negotiation_and_workspace_failures_never_become_ready_or_leak_remote_text() {
    for (mode, code, frames) in [
        ("auth_refused", "BAT_AUTH_REFUSED", 1),
        ("auth_error", "BAT_AUTH_REFUSED", 1),
        ("auth_string", "BAT_PROTOCOL_INVALID", 1),
        ("old_version", "BAT_VERSION_UNSUPPORTED", 1),
        ("bad_version", "BAT_PROTOCOL_INVALID", 1),
        ("wrong_protocol", "BAT_PROTOCOL_INVALID", 1),
        ("compression", "BAT_PROTOCOL_INVALID", 1),
        ("secret_invalid_json", "BAT_PROTOCOL_INVALID", 1),
        ("binary", "BAT_PROTOCOL_INVALID", 1),
        ("oversized", "BAT_PROTOCOL_INVALID", 1),
        ("auth_event_limit", "BAT_PROTOCOL_INVALID", 1),
        ("bad_upgrade", "BAT_PROTOCOL_INVALID", 0),
        ("long_upgrade", "BAT_PROTOCOL_INVALID", 0),
        ("duplicate_ws", "WORKSPACE_BINDING_MISMATCH", 2),
        ("wrong_case", "WORKSPACE_BINDING_MISMATCH", 2),
        ("empty_ws", "WORKSPACE_BINDING_MISMATCH", 2),
        ("wrong_shape", "WORKSPACE_INVALID", 2),
        ("null_workspace", "WORKSPACE_INVALID", 2),
        ("bad_workspace_json", "WORKSPACE_INVALID", 2),
        ("invoke_error", "WORKSPACE_UNREADABLE", 2),
    ] {
        let peer = peer(mode).await;
        let out = run(&peer, TOKEN.into(), Duration::from_secs(2)).await;
        assert_eq!(out.code, Some(code), "{mode}: {out:?}");
        assert_ne!(
            readiness(true, ProbeKind::Bat, Some(&out), &generation(), 1000).level,
            Level::Ready
        );
        let printed = format!("{out:?}");
        for secret in [
            TOKEN,
            "fixture-secret",
            "private auth detail",
            "secret failure",
        ] {
            assert!(!printed.contains(secret));
        }
        assert_eq!(peer.finish().await.len(), frames, "{mode}");
    }
}
#[tokio::test]
async fn pin_failure_and_missing_credentials_send_no_auth() {
    for mode in [
        "wrong_pin",
        "missing_pin",
        "missing_credential",
        "plain_tcp",
        "wrong_key",
    ] {
        let mut peer = peer(if ["plain_tcp", "wrong_key"].contains(&mode) {
            mode
        } else {
            "ok"
        })
        .await;
        let expected = match mode {
            "wrong_pin" => {
                peer.config.certificate_pin = "00".repeat(32);
                "TLS_IDENTITY_MISMATCH"
            }
            "missing_pin" => {
                peer.config.certificate_pin.clear();
                "TLS_PIN_MISSING"
            }
            "missing_credential" => "CREDENTIAL_MISSING",
            _ => "TLS_FAILED",
        };
        let out = run(
            &peer,
            if mode == "missing_credential" {
                String::new()
            } else {
                TOKEN.into()
            },
            Duration::from_secs(2),
        )
        .await;
        assert_eq!(out.code, Some(expected));
        assert!(peer.finish().await.is_empty());
    }
}
#[tokio::test]
async fn bat_deadline_is_total_and_wrong_id_is_not_authentication() {
    for mode in ["silent_tls", "silent_auth", "wrong_id"] {
        let peer = peer(mode).await;
        let start = Instant::now();
        let out = run(&peer, TOKEN.into(), Duration::from_millis(150)).await;
        assert_eq!(out.code, Some("PROBE_TIMEOUT"), "{mode}: {out:?}");
        assert!(start.elapsed() < Duration::from_secs(1));
        assert_eq!(out.auth, Authentication::Unknown);
        peer.finish().await;
    }
}
#[tokio::test]
async fn bat_deadline_does_not_restart_at_workspace_and_cancel_drops_socket() {
    let (workspace_signal, workspace_received) = tokio::sync::oneshot::channel();
    let peer = peer_with_workspace_signal("total_deadline", Some(workspace_signal)).await;
    tokio::time::pause();
    // Keep the runtime runnable while real socket IO establishes TLS/auth so
    // paused time cannot auto-advance to the probe deadline. Release it only
    // after the peer receives workspace:load (proof auth completed). Retain the
    // fixture's 2 s wall-clock bound if that IO itself stalls.
    let drive_io = tokio::spawn(async move {
        tokio::pin!(workspace_received);
        let end = Instant::now() + Duration::from_secs(2);
        while Instant::now() < end {
            tokio::select! {
                _ = &mut workspace_received => return,
                _ = tokio::task::yield_now() => {}
            }
        }
    });
    let out = run(&peer, TOKEN.into(), Duration::from_millis(150)).await;
    drive_io.abort();
    tokio::time::resume();
    assert_eq!(out.code, Some("PROBE_TIMEOUT"));
    assert_eq!(out.auth, Authentication::Ok);
    assert!(!out.workspace);
    peer.finish().await;

    let listener = TcpListener::bind(LOOPBACK).await.unwrap();
    let config = bat_config(endpoint(&listener), "00".repeat(32));
    let (accepted, connected) = tokio::sync::oneshot::channel();
    let task = tokio::spawn(async move {
        let (mut tcp, _) = listener.accept().await.unwrap();
        accepted.send(()).unwrap();
        let mut hello = Vec::new();
        // Client cancellation closes the socket even while TLS is awaiting this peer.
        tokio::time::timeout(Duration::from_secs(1), tcp.read_to_end(&mut hello))
            .await
            .unwrap()
            .unwrap();
        assert!(!hello.is_empty());
    });
    let attempt = tokio::spawn(async move {
        bat_probe(
            &config,
            ProbeCredential::new(TOKEN.into()),
            generation(),
            1000,
            Duration::from_secs(5),
        )
        .await
    });
    connected.await.unwrap();
    attempt.abort();
    assert!(attempt.await.unwrap_err().is_cancelled());
    task.await.unwrap();
}
#[tokio::test]
async fn client_masking_covers_both_extended_lengths_and_optional_empty_workspaces() {
    for size in [300, 70000] {
        let peer = peer("ok").await;
        let out = run(
            &peer,
            format!("{TOKEN}{}", "x".repeat(size)),
            Duration::from_secs(2),
        )
        .await;
        assert!(out.workspace);
        peer.finish().await;
    }
    let mut peer = peer("empty_ws").await;
    peer.config.required_workspace_ids.clear();
    let out = run(&peer, TOKEN.into(), Duration::from_secs(2)).await;
    assert!(out.workspace);
    peer.finish().await;
}

fn capabilities() -> Value {
    json!({"connector":"fixture","api_version":1,"contract_version":"2026-10-08","scopes":["observe"],"features":{"operations":true},"hosts":[{"secret":"fixture-host-secret"}],"actions":[{"secret":"fixture-action-secret"}],"actor":"fixture-actor-secret"})
}
async fn http_peer(
    status: u16,
    body: Vec<u8>,
    extra: String,
    drip: bool,
) -> (ConnectorProbeConfig, JoinHandle<String>) {
    let listener = TcpListener::bind(LOOPBACK).await.unwrap();
    let config = connector_config(endpoint(&listener));
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let request = header(&mut stream).await.unwrap();
        stream.write_all(format!("HTTP/1.1 {status} Fixture\r\nContent-Length: {}\r\nConnection: close\r\n{extra}\r\n",body.len()).as_bytes()).await.unwrap();
        if drip {
            for byte in body {
                if stream.write_all(&[byte]).await.is_err() {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(30)).await;
            }
        } else {
            let _ = stream.write_all(&body).await;
        }
        request
    });
    (config, task)
}
async fn http_run(config: &ConnectorProbeConfig, budget: Duration) -> ProbeObservation {
    connector_probe(
        config,
        ProbeCredential::new(TOKEN.into()),
        generation(),
        1000,
        budget,
    )
    .await
}
#[tokio::test]
async fn connector_requires_exact_observe_scope_contract_shape_and_true_features() {
    for (mode, code) in [
        ("ok", None),
        ("new_date", None),
        ("extra_scope", Some("CONNECTOR_SCOPE_MISSING")),
        ("admin", Some("CONNECTOR_SCOPE_MISSING")),
        ("bool_api", Some("CONNECTOR_CONTRACT_UNSUPPORTED")),
        ("float_api", Some("CONNECTOR_CONTRACT_UNSUPPORTED")),
        ("api2", Some("CONNECTOR_CONTRACT_UNSUPPORTED")),
        ("old_date", Some("CONNECTOR_CONTRACT_UNSUPPORTED")),
        ("bad_date", Some("CONNECTOR_CONTRACT_UNSUPPORTED")),
        ("false_feature", Some("CONNECTOR_CAPABILITY_MISSING")),
        ("string_feature", Some("CONNECTOR_CAPABILITY_MISSING")),
        ("no_hosts", Some("CONNECTOR_CAPABILITY_MISSING")),
        ("no_actions", Some("CONNECTOR_CAPABILITY_MISSING")),
        ("no_name", Some("CONNECTOR_CAPABILITY_MISSING")),
    ] {
        let mut value = capabilities();
        match mode {
            "new_date" => value["contract_version"] = json!("2027-01-01"),
            "extra_scope" => value["scopes"] = json!(["observe", "operate"]),
            "admin" => value["scopes"] = json!(["admin"]),
            "bool_api" => value["api_version"] = json!(true),
            "float_api" => value["api_version"] = json!(1.0),
            "api2" => value["api_version"] = json!(2),
            "old_date" => value["contract_version"] = json!("2026-01-01"),
            "bad_date" => value["contract_version"] = json!("2026-02-30"),
            "false_feature" => value["features"]["operations"] = json!(false),
            "string_feature" => value["features"]["operations"] = json!("true"),
            "no_hosts" => value["hosts"] = Value::Null,
            "no_actions" => value["actions"] = Value::Null,
            "no_name" => value["connector"] = json!(""),
            _ => (),
        }
        let (config, task) = http_peer(
            200,
            serde_json::to_vec(&value).unwrap(),
            String::new(),
            false,
        )
        .await;
        let out = http_run(&config, Duration::from_secs(2)).await;
        assert_eq!(out.code, code, "{mode}: {out:?}");
        assert_eq!(
            readiness(true, ProbeKind::Connector, Some(&out), &generation(), 1000).level
                == Level::Ready,
            code.is_none()
        );
        let printed = format!("{out:?}");
        for secret in [
            TOKEN,
            "fixture-host-secret",
            "fixture-action-secret",
            "fixture-actor-secret",
        ] {
            assert!(!printed.contains(secret));
        }
        let request = task.await.unwrap();
        assert!(request.starts_with("GET /api/v1/capabilities HTTP/1.1\r\n"));
        assert!(request.contains(&format!("authorization: Bearer {TOKEN}\r\n")));
    }
}
#[tokio::test]
async fn connector_http_failures_duplicate_json_utf8_and_body_bounds_are_closed() {
    for (status, body, code) in [
        (401, vec![], "CONNECTOR_AUTH_REFUSED"),
        (403, vec![], "CONNECTOR_SCOPE_MISSING"),
        (404, vec![], "CONNECTOR_UNAVAILABLE"),
        (
            200,
            b"secret invalid JSON".to_vec(),
            "CONNECTOR_UNAVAILABLE",
        ),
        (
            200,
            b"{\"hosts\":[],\"HOSTS\":[]}".to_vec(),
            "CONNECTOR_UNAVAILABLE",
        ),
        (200, vec![0xff], "CONNECTOR_UNAVAILABLE"),
        (200, vec![b' '; 1024 * 1024 + 1], "CONNECTOR_UNAVAILABLE"),
    ] {
        let (config, task) = http_peer(status, body, String::new(), false).await;
        let out = http_run(&config, Duration::from_secs(2)).await;
        assert_eq!(out.code, Some(code));
        task.await.unwrap();
    }
}
#[tokio::test]
async fn connector_does_not_follow_redirect_or_forward_observe_token() {
    let destination = TcpListener::bind(LOOPBACK).await.unwrap();
    let (config, task) = http_peer(
        302,
        vec![],
        format!("Location: http://{}/steal\r\n", endpoint(&destination)),
        false,
    )
    .await;
    let out = http_run(&config, Duration::from_secs(1)).await;
    assert_eq!(out.code, Some("CONNECTOR_UNAVAILABLE"));
    task.await.unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(50), destination.accept())
            .await
            .is_err()
    );
}
#[tokio::test]
async fn connector_total_body_deadline_and_missing_credential_never_fallback() {
    let (config, task) = http_peer(
        200,
        serde_json::to_vec(&capabilities()).unwrap(),
        String::new(),
        true,
    )
    .await;
    let start = Instant::now();
    let out = http_run(&config, Duration::from_millis(120)).await;
    assert_eq!(out.code, Some("PROBE_TIMEOUT"));
    assert!(start.elapsed() < Duration::from_secs(1));
    task.await.unwrap();
    let listener = TcpListener::bind(LOOPBACK).await.unwrap();
    let config = connector_config(endpoint(&listener));
    let out = connector_probe(
        &config,
        ProbeCredential::new(String::new()),
        generation(),
        1000,
        Duration::from_secs(1),
    )
    .await;
    assert_eq!(out.code, Some("CREDENTIAL_MISSING"));
    assert!(
        tokio::time::timeout(Duration::from_millis(30), listener.accept())
            .await
            .is_err()
    );
}
#[tokio::test]
async fn configuration_bounds_precede_network_and_readiness_cannot_relabel_old_results() {
    let mut config = bat_config("192.0.2.1:1234".parse().unwrap(), "00".repeat(32));
    let out = bat_probe(
        &config,
        ProbeCredential::new(TOKEN.into()),
        generation(),
        1000,
        Duration::from_secs(1),
    )
    .await;
    assert_eq!(out.code, Some("INVENTORY_INVALID"));
    let closed = TcpListener::bind(LOOPBACK).await.unwrap();
    config.endpoint = endpoint(&closed);
    drop(closed);
    let down = bat_probe(
        &config,
        ProbeCredential::new(TOKEN.into()),
        generation(),
        1000,
        Duration::from_secs(1),
    )
    .await;
    // Windows can finish the one-second probe budget before TCP reports a
    // refused connection. Either result positively denies tunnel readiness;
    // this test does not require an OS-specific refusal latency.
    assert!(matches!(down.code, Some("TUNNEL_DOWN" | "PROBE_TIMEOUT")));
    assert!(!down.tunnel);
    for duration in [Duration::ZERO, Duration::from_secs(31)] {
        let out = bat_probe(
            &config,
            ProbeCredential::new(TOKEN.into()),
            generation(),
            1000,
            duration,
        )
        .await;
        assert_eq!(out.code, Some("INVENTORY_INVALID"));
    }
    let peer = peer("ok").await;
    let out = run(&peer, TOKEN.into(), Duration::from_secs(2)).await;
    peer.finish().await;
    for now in [999, 61001] {
        assert_eq!(
            readiness(true, ProbeKind::Bat, Some(&out), &generation(), now).code,
            Some("PROBE_STALE")
        );
    }
    assert_eq!(
        readiness(true, ProbeKind::Bat, Some(&out), &generation(), 61000).level,
        Level::Ready
    );
    for field in 0..4 {
        let mut current = generation();
        match field {
            0 => current.epoch.push('x'),
            1 => current.configuration_binding.push('x'),
            2 => current.selection_revision.push('x'),
            _ => current.generation += 1,
        };
        assert_eq!(
            readiness(true, ProbeKind::Bat, Some(&out), &current, 1000).code,
            Some("PROBE_STALE")
        );
    }
    assert_eq!(
        readiness(true, ProbeKind::Connector, Some(&out), &generation(), 1000).code,
        Some("PROBE_STALE")
    );
    let ready = readiness(true, ProbeKind::Bat, Some(&out), &generation(), 1000);
    let off = readiness(false, ProbeKind::Bat, None, &generation(), 1000);
    let pending = readiness(true, ProbeKind::Bat, None, &generation(), 1000);
    assert_eq!(
        tray_summary([&ready, &off, &pending]),
        TraySummary {
            up: 1,
            total: 2,
            down: 0,
            warn: 1
        }
    );
}
