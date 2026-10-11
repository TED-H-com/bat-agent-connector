//! One fixed central report saved to a user-selected destination, never an artifact.
use crate::bridge::{Bridge, ConnectorRequest, FileScope};
use crate::files::Destination;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::sync::Arc;

const MAX_CONTENT_BYTES: usize = 1_500_000;
const MAX_RESPONSE_BYTES: usize = 2_000_000;
const INVALID: &str = "BRIEF_EXPORT_INVALID: The complete fixed report could not be verified";
const CHANGED: &str = "BRIEF_EXPORT_CHANGED: Read the selected snapshot again before saving";

fn hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Reference {
    pub workspace_id: String,
    pub snapshot_id: String,
    pub format: String,
    pub locale: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub view: Option<String>,
    pub expected_view_digest: String,
    pub expected_output_digest: String,
}

impl Reference {
    pub fn validate(&self) -> Result<(), String> {
        if !self.workspace_id.starts_with("ws_")
            || !hex(self.workspace_id.get(3..).unwrap_or(""), 32)
            || !self.snapshot_id.starts_with("pmsnap_")
            || !hex(self.snapshot_id.get(7..).unwrap_or(""), 32)
            || !matches!(self.format.as_str(), "markdown" | "html" | "svg")
            || self.view.as_ref().is_some_and(|view| {
                !matches!(
                    view.as_str(),
                    "roadmap" | "kanban" | "sprint" | "governance"
                ) || self.format == "markdown"
            })
            || !matches!(self.locale.as_str(), "en" | "zh-TW")
            || !hex(&self.expected_view_digest, 64)
            || !hex(&self.expected_output_digest, 64)
        {
            return Err(
                "BRIEF_EXPORT_REFERENCE_INVALID: Select one fixed report and its exact digests"
                    .into(),
            );
        }
        Ok(())
    }
    fn route(&self) -> String {
        if let Some(view) = &self.view {
            return format!(
                "/pm/workspaces/{}/snapshots/{}/view-export?view={}&format={}&locale={}",
                self.workspace_id, self.snapshot_id, view, self.format, self.locale
            );
        }
        format!(
            "/pm/workspaces/{}/snapshots/{}/brief-export?format={}&locale={}",
            self.workspace_id, self.snapshot_id, self.format, self.locale
        )
    }
    pub fn extension(&self) -> &str {
        if self.format == "markdown" {
            "md"
        } else {
            &self.format
        }
    }
    pub fn filename(&self) -> String {
        if let Some(view) = &self.view {
            return format!(
                "pm-{}-{}-{}.{}",
                view,
                self.snapshot_id,
                self.locale,
                self.extension()
            );
        }
        format!(
            "pm-brief-{}-{}.{}",
            self.snapshot_id,
            self.locale,
            self.extension()
        )
    }
    pub fn dialog_title(&self) -> &'static str {
        if self.view.is_some() {
            return if self.locale == "zh-TW" {
                "另存固定專案視圖"
            } else {
                "Save fixed project view"
            };
        }
        if self.locale == "zh-TW" {
            "另存專案摘要"
        } else {
            "Save Project Brief"
        }
    }
    pub fn filter_label(&self) -> &str {
        match self.format.as_str() {
            "markdown" => "Markdown",
            "html" => "HTML",
            _ => "SVG",
        }
    }
    fn media_type(&self) -> &'static str {
        match self.format.as_str() {
            "markdown" => "text/markdown; charset=utf-8",
            "html" => "text/html; charset=utf-8",
            _ => "image/svg+xml; charset=utf-8",
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Coverage {
    #[serde(default)]
    view: Option<Value>,
    snapshot: Value,
    visibility: Value,
    projection: String,
    other_management_views: String,
    referenced_sources: String,
    unreferenced_evidence: String,
    content_truncated: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    version: u64,
    workspace_id: String,
    snapshot_id: String,
    snapshot_digest: String,
    view_digest: String,
    template_version: u64,
    renderer_version: u64,
    #[serde(default)]
    view: Option<String>,
    #[serde(default)]
    view_template_version: Option<u64>,
    locale: String,
    format: String,
    media_type: String,
    content: String,
    byte_length: u64,
    output_digest: String,
    page_count: Option<u64>,
    coverage: Coverage,
    execution_authorized: bool,
    review_completed: bool,
    published: bool,
}

pub(crate) struct Verified {
    content: String,
    document: Document,
}

fn verify(value: Value, reference: &Reference) -> Result<Verified, String> {
    reference.validate()?;
    if value.get("page_count").is_none()
        || serde_json::to_vec(&value).map_err(|_| INVALID)?.len() > MAX_RESPONSE_BYTES
    {
        return Err(INVALID.into());
    }
    let mut doc: Document = serde_json::from_value(value).map_err(|_| INVALID)?;
    let raw = doc.content.as_bytes();
    let renderer_matches = if let Some(view) = &reference.view {
        doc.view.as_ref() == Some(view)
            && doc.view_template_version == Some(1)
            && doc.renderer_version == 1
            && doc.coverage.projection == *view
            && doc.coverage.view.as_ref().is_some_and(Value::is_object)
    } else {
        doc.view.is_none()
            && doc.view_template_version.is_none()
            && doc.coverage.view.is_none()
            && matches!(
                (doc.renderer_version, doc.coverage.projection.as_str()),
                (1 | 3, "project_brief_only") | (2 | 3, "project_brief_with_maintenance_review")
            )
    };
    if doc.version != 1
        || doc.template_version != 1
        || !renderer_matches
        || doc.workspace_id != reference.workspace_id
        || doc.snapshot_id != reference.snapshot_id
        || doc.format != reference.format
        || doc.locale != reference.locale
        || !hex(&doc.snapshot_digest, 64)
        || !hex(&doc.view_digest, 64)
        || !hex(&doc.output_digest, 64)
        || doc.media_type != reference.media_type()
        || raw.is_empty()
        || raw.len() > MAX_CONTENT_BYTES
        || raw.len() as u64 != doc.byte_length
        || format!("{:x}", Sha256::digest(raw)) != doc.output_digest
        || (doc.format == "svg"
            && !doc
                .page_count
                .is_some_and(|count| (1..=32).contains(&count)))
        || (doc.format != "svg" && doc.page_count.is_some())
        || !doc.coverage.snapshot.is_object()
        || !doc.coverage.visibility.is_object()
        || !doc.coverage.visibility["filtered"].is_boolean()
        || doc.coverage.other_management_views != "not_rendered"
        || doc.coverage.referenced_sources != "fixed_authorized_view"
        || doc.coverage.unreferenced_evidence != "not_expanded"
        || doc.coverage.content_truncated
        || doc.execution_authorized
        || doc.review_completed
        || doc.published
    {
        return Err(INVALID.into());
    }
    if doc.view_digest != reference.expected_view_digest
        || doc.output_digest != reference.expected_output_digest
    {
        return Err(CHANGED.into());
    }
    Ok(Verified {
        content: std::mem::take(&mut doc.content),
        document: doc,
    })
}

pub(crate) async fn read(
    bridge: &Bridge,
    scope: &FileScope,
    reference: &Reference,
) -> Result<Verified, String> {
    reference.validate()?;
    let response = bridge
        .file_request(
            scope,
            ConnectorRequest {
                method: "GET".into(),
                path: reference.route(),
                body: None,
                idempotency_key: None,
            },
        )
        .await?;
    if response.status != 200 {
        return Err(format!(
            "BRIEF_EXPORT_UNAVAILABLE: The selected report cannot currently be read (HTTP {})",
            response.status
        ));
    }
    verify(response.data, reference)
}

#[derive(Serialize)]
pub struct Receipt {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view: Option<String>,
    pub stage: &'static str,
    pub workspace_id: String,
    pub snapshot_id: String,
    pub format: String,
    pub locale: String,
    pub view_digest: String,
    pub output_digest: String,
    pub byte_length: u64,
    pub display_name: String,
}

pub(crate) async fn save(
    bridge: Arc<Bridge>,
    scope: FileScope,
    reference: Reference,
    destination: Destination,
) -> Result<Receipt, String> {
    let verified = read(&bridge, &scope, &reference).await?;
    tokio::task::spawn_blocking(move || {
        destination.publish_bytes(
            verified.content.as_bytes(),
            verified.document.byte_length,
            &verified.document.output_digest,
            || bridge.check_file_scope(&scope),
        )?;
        let doc = verified.document;
        Ok(Receipt {
            view: doc.view,
            stage: "saved",
            workspace_id: doc.workspace_id,
            snapshot_id: doc.snapshot_id,
            format: doc.format,
            locale: doc.locale,
            view_digest: doc.view_digest,
            output_digest: doc.output_digest,
            byte_length: doc.byte_length,
            display_name: destination.display_name().into(),
        })
    })
    .await
    .map_err(|_| {
        "BRIEF_EXPORT_SAVE_INTERRUPTED: Check the selected destination before saving again"
            .to_string()
    })?
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::Mutex,
        thread,
        time::{Duration, Instant},
    };

    fn fixture(format: &str, locale: &str) -> (Reference, Value) {
        let content = match format {
            "html" => "<!doctype html><html><body><pre>固定來源 &lt;script&gt; · unchanged</pre></body></html>\n",
            "svg" => "<svg xmlns=\"http://www.w3.org/2000/svg\"><text>固定來源 &lt;script&gt;</text></svg>\n",
            _ => "# Project Brief\n\n```text\n固定來源 <script> · unchanged\n```\n",
        };
        let reference = Reference {
            workspace_id: format!("ws_{}", "1".repeat(32)),
            snapshot_id: format!("pmsnap_{}", "2".repeat(32)),
            format: format.into(),
            locale: locale.into(),
            view: None,
            expected_view_digest: "3".repeat(64),
            expected_output_digest: format!("{:x}", Sha256::digest(content.as_bytes())),
        };
        let document = json!({"version":1,"workspace_id":reference.workspace_id,"snapshot_id":reference.snapshot_id,
            "snapshot_digest":"4".repeat(64),"view_digest":reference.expected_view_digest,"template_version":1,"renderer_version":1,
            "locale":locale,"format":format,"media_type":reference.media_type(),"content":content,
            "byte_length":content.len(),"output_digest":reference.expected_output_digest,
            "page_count":if format=="svg" {Some(1)} else {None},
            "coverage":{"snapshot":{},"visibility":{"filtered":false},"projection":"project_brief_only",
                "other_management_views":"not_rendered","referenced_sources":"fixed_authorized_view",
                "unreferenced_evidence":"not_expanded","content_truncated":false},
            "execution_authorized":false,"review_completed":false,"published":false});
        (reference, document)
    }

    #[test]
    fn reference_accepts_only_fixed_identity_not_paths_urls_or_bytes() {
        for format in ["markdown", "html", "svg"] {
            for locale in ["en", "zh-TW"] {
                let (reference, _) = fixture(format, locale);
                reference.validate().unwrap();
                assert!(reference.filename().ends_with(reference.extension()));
                assert!(reference.filename().is_ascii());
                assert_eq!(
                    reference.dialog_title(),
                    if locale == "en" {
                        "Save Project Brief"
                    } else {
                        "另存專案摘要"
                    }
                );
                crate::bridge::validate_request(&ConnectorRequest {
                    method: "GET".into(),
                    path: reference.route(),
                    body: None,
                    idempotency_key: None,
                })
                .unwrap();
            }
        }
        let (reference, _) = fixture("html", "en");
        for field in [
            "path",
            "url",
            "content",
            "content_url",
            "token",
            "force",
            "overwrite",
            "artifact_id",
            "filename",
        ] {
            let mut value = serde_json::to_value(&reference).unwrap();
            value[field] = json!("not a capability");
            assert!(
                serde_json::from_value::<Reference>(value).is_err(),
                "{field}"
            );
        }
        for (field, value) in [
            ("workspace_id", "../escape"),
            ("snapshot_id", "latest"),
            ("format", "html&url=x"),
            ("locale", "zh"),
            ("expected_view_digest", ""),
            ("expected_output_digest", &"A".repeat(64)),
        ] {
            let mut input = serde_json::to_value(&reference).unwrap();
            input[field] = json!(value);
            assert!(
                serde_json::from_value::<Reference>(input)
                    .unwrap()
                    .validate()
                    .is_err(),
                "{field}"
            );
        }
    }

    #[test]
    fn maintenance_report_requires_matching_renderer_and_projection() {
        let (reference, mut doc) = fixture("html", "zh-TW");
        doc["coverage"]["projection"] = json!("project_brief_with_maintenance_review");
        assert!(verify(doc.clone(), &reference).is_err());
        doc["renderer_version"] = json!(2);
        assert_eq!(
            verify(doc.clone(), &reference).unwrap().content,
            doc["content"]
        );
        doc["renderer_version"] = json!(4);
        assert!(verify(doc, &reference).is_err());
    }

    #[test]
    fn quality_renderer_preserves_fixed_report_verification_for_both_projections() {
        for format in ["markdown", "html", "svg"] {
            for locale in ["en", "zh-TW"] {
                for projection in [
                    "project_brief_only",
                    "project_brief_with_maintenance_review",
                ] {
                    let (reference, mut doc) = fixture(format, locale);
                    doc["renderer_version"] = json!(3);
                    doc["coverage"]["projection"] = json!(projection);
                    assert_eq!(
                        verify(doc.clone(), &reference).unwrap().content,
                        doc["content"]
                    );
                    let mut changed = doc.clone();
                    changed["output_digest"] = json!("0".repeat(64));
                    assert!(verify(changed, &reference).is_err());
                    doc["coverage"]["projection"] = json!("unknown_quality_view");
                    assert!(verify(doc, &reference).is_err());
                }
            }
        }
    }

    #[test]
    fn verification_requires_complete_matching_utf8_report_and_semantics() {
        for format in ["markdown", "html", "svg"] {
            for locale in ["en", "zh-TW"] {
                let (reference, doc) = fixture(format, locale);
                assert_eq!(
                    verify(doc.clone(), &reference).unwrap().content,
                    doc["content"]
                );
            }
        }
        let (reference, doc) = fixture("html", "en");
        for (field, value) in [
            ("version", json!(2)),
            ("template_version", json!(2)),
            ("renderer_version", json!(2)),
            ("workspace_id", json!(format!("ws_{}", "9".repeat(32)))),
            ("snapshot_id", json!("latest")),
            ("format", json!("svg")),
            ("locale", json!("zh-TW")),
            ("media_type", json!("application/javascript")),
            ("content", json!("changed")),
            (
                "byte_length",
                json!(doc["content"].as_str().unwrap().chars().count()),
            ),
            ("byte_length", json!(true)),
            ("page_count", json!(1)),
            ("snapshot_digest", json!("bad")),
            ("execution_authorized", json!(true)),
            ("review_completed", json!(true)),
            ("published", json!(true)),
            ("unknown_field", json!("unexpected")),
        ] {
            let mut wrong = doc.clone();
            wrong[field] = value;
            assert!(verify(wrong, &reference).is_err(), "{field}");
        }
        for field in ["content", "page_count", "coverage"] {
            let mut wrong = doc.clone();
            wrong.as_object_mut().unwrap().remove(field);
            assert!(verify(wrong, &reference).is_err(), "missing {field}");
        }
        let mut changed = doc.clone();
        changed["view_digest"] = json!("8".repeat(64));
        assert_eq!(verify(changed, &reference).err().unwrap(), CHANGED);
        let mut changed_ref = reference.clone();
        changed_ref.expected_output_digest = "8".repeat(64);
        assert_eq!(verify(doc.clone(), &changed_ref).err().unwrap(), CHANGED);
        for (field, value) in [
            ("content_truncated", json!(true)),
            ("projection", json!("other")),
            ("visibility", json!({"filtered":"false"})),
            ("snapshot", json!([])),
        ] {
            let mut wrong = doc.clone();
            wrong["coverage"][field] = value;
            assert!(verify(wrong, &reference).is_err(), "coverage {field}");
        }
        let mut huge = doc.clone();
        let content = "字".repeat(MAX_CONTENT_BYTES / 3 + 1);
        huge["content"] = json!(content);
        huge["byte_length"] = json!(content.len());
        huge["output_digest"] = json!(format!("{:x}", Sha256::digest(content.as_bytes())));
        let mut huge_ref = reference;
        huge_ref.expected_output_digest = huge["output_digest"].as_str().unwrap().into();
        assert!(verify(huge, &huge_ref).is_err());
        let mut wire = doc;
        wire["coverage"]["snapshot"] = json!({"too_big":"x".repeat(MAX_RESPONSE_BYTES)});
        assert!(verify(wire, &huge_ref).is_err());
    }

    struct Peer {
        endpoint: String,
        requests: Arc<Mutex<Vec<String>>>,
        thread: Option<thread::JoinHandle<()>>,
    }
    impl Drop for Peer {
        fn drop(&mut self) {
            if let Some(worker) = self.thread.take() {
                let result = worker.join();
                if !thread::panicking() {
                    result.expect("Brief fixture peer failed");
                }
            }
        }
    }
    fn peer(documents: Vec<(u16, Value)>) -> Peer {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let endpoint = format!("http://{}/", listener.local_addr().unwrap());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let saved = requests.clone();
        let caps = json!({"actor":"fixture-operator","api_version":1,"contract_version":"2026-10-08","scopes":["observe"]});
        let bootstrap = json!({"capabilities":caps,"sync":{"version":1,"server_id":"fixture-server","principal_id":"fixture-principal"}});
        let responses: Vec<_> = documents
            .into_iter()
            .flat_map(|reply| [(200, caps.clone()), (200, bootstrap.clone()), reply])
            .collect();
        let worker = thread::spawn(move || {
            for (status, doc) in responses {
                let deadline = Instant::now() + Duration::from_secs(5);
                let mut socket = loop {
                    match listener.accept() {
                        Ok((socket, _)) => break socket,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            if Instant::now() >= deadline {
                                return;
                            }
                            thread::sleep(Duration::from_millis(5));
                        }
                        Err(error) => panic!("{error}"),
                    }
                };
                // Accepted sockets inherit nonblocking mode on Windows. Only
                // accept is polled; the bounded HTTP read below is blocking.
                socket.set_nonblocking(false).unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut raw = Vec::new();
                let mut buf = [0; 2048];
                loop {
                    let count = socket.read(&mut buf).unwrap();
                    if count == 0 {
                        break;
                    }
                    raw.extend_from_slice(&buf[..count]);
                    if raw.windows(4).any(|v| v == b"\r\n\r\n") {
                        break;
                    }
                }
                saved.lock().unwrap().push(String::from_utf8(raw).unwrap());
                let body = doc.to_string();
                let response=format!("HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len());
                let _ = socket.write_all(response.as_bytes());
            }
        });
        Peer {
            endpoint,
            requests,
            thread: Some(worker),
        }
    }

    #[tokio::test]
    async fn selected_destination_saves_fresh_report_bytes_only_without_artifacts() {
        let root = tempfile::tempdir().unwrap();
        let (reference, doc) = fixture("html", "zh-TW");
        let peer = peer(vec![(200, doc.clone()), (200, doc.clone())]);
        let bridge = Arc::new(Bridge::file_fixture(&peer.endpoint, "fixture-secret"));
        let scope = bridge.file_scope().unwrap();
        read(&bridge, &scope, &reference).await.unwrap();
        let path = root.path().join("selected-report.html");
        let receipt = save(
            bridge,
            scope,
            reference.clone(),
            Destination::from_selection(&path).unwrap(),
        )
        .await
        .unwrap();
        assert_eq!(
            std::fs::read(&path).unwrap(),
            doc["content"].as_str().unwrap().as_bytes()
        );
        assert_eq!(receipt.stage, "saved");
        assert_eq!(receipt.display_name, "selected-report.html");
        assert_eq!(receipt.output_digest, reference.expected_output_digest);
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
        assert!(Destination::from_selection(&path)
            .err()
            .unwrap()
            .starts_with("DESTINATION_EXISTS"));
        let requests = peer.requests.lock().unwrap();
        assert_eq!(requests.len(), 6);
        for (index, request) in requests.iter().enumerate() {
            let path = match index % 3 {
                0 => "/capabilities".into(),
                1 => "/bootstrap".into(),
                _ => reference.route(),
            };
            assert!(request.starts_with(&format!("GET /api/v1{path} HTTP/1.1\r\n")));
        }
        let encoded = serde_json::to_value(receipt).unwrap();
        assert!(
            encoded.get("path").is_none()
                && encoded.get("content").is_none()
                && encoded.get("artifact").is_none()
        );
    }

    #[tokio::test]
    async fn post_dialog_revocation_or_projection_change_cannot_save_stale_bytes() {
        for withdrawn in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let (reference, doc) = fixture("markdown", "en");
            let mut next = doc.clone();
            next["view_digest"] = json!("9".repeat(64));
            let peer = peer(vec![
                (200, doc),
                if withdrawn {
                    (404, json!({"error":{"code":"NOT_FOUND"}}))
                } else {
                    (200, next)
                },
            ]);
            let bridge = Arc::new(Bridge::file_fixture(&peer.endpoint, "fixture-secret"));
            let scope = bridge.file_scope().unwrap();
            read(&bridge, &scope, &reference).await.unwrap();
            let path = root.path().join("not-saved.md");
            let error = save(
                bridge,
                scope,
                reference,
                Destination::from_selection(&path).unwrap(),
            )
            .await
            .err()
            .unwrap();
            assert!(error.starts_with(if withdrawn {
                "BRIEF_EXPORT_UNAVAILABLE"
            } else {
                "BRIEF_EXPORT_CHANGED"
            }));
            assert!(!path.exists());
            assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
            assert_eq!(peer.requests.lock().unwrap().len(), 6);
        }
    }

    #[tokio::test]
    async fn changed_native_connection_cannot_write_or_fetch_again() {
        let root = tempfile::tempdir().unwrap();
        let (reference, doc) = fixture("svg", "en");
        let peer = peer(vec![(200, doc)]);
        let bridge = Arc::new(Bridge::file_fixture(&peer.endpoint, "fixture-secret"));
        let scope = bridge.file_scope().unwrap();
        read(&bridge, &scope, &reference).await.unwrap();
        bridge.disconnect();
        let path = root.path().join("not-saved.svg");
        assert!(save(
            bridge,
            scope,
            reference,
            Destination::from_selection(&path).unwrap()
        )
        .await
        .is_err());
        assert!(!path.exists());
        assert_eq!(peer.requests.lock().unwrap().len(), 3);
    }

    #[test]
    fn report_publication_retains_existing_files_and_cleans_only_own_fragment() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("report.md");
        let destination = Destination::from_selection(&path).unwrap();
        std::fs::write(&path, b"existing editor contents").unwrap();
        let content = b"selected report";
        let digest = format!("{:x}", Sha256::digest(content));
        assert!(destination
            .publish_bytes(content, content.len() as u64, &digest, || Ok(()))
            .is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"existing editor contents");
        let next = root.path().join("next.md");
        let destination = Destination::from_selection(&next).unwrap();
        let calls = std::cell::Cell::new(0);
        assert!(destination
            .publish_bytes(content, content.len() as u64, &digest, || {
                calls.set(calls.get() + 1);
                if calls.get() >= 3 {
                    Err("connection changed".into())
                } else {
                    Ok(())
                }
            })
            .is_err());
        assert!(!next.exists());
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
        assert!(destination
            .publish_bytes(content, content.len() as u64, &"0".repeat(64), || Ok(()))
            .is_err());
        assert!(!next.exists());
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
    }

    #[test]
    fn main_only_permission_is_narrow_and_does_not_grant_generic_file_access() {
        let capability: Value =
            serde_json::from_str(include_str!("../capabilities/dashboard.json")).unwrap();
        assert_eq!(capability["windows"], json!(["main"]));
        let permissions = capability["permissions"].as_array().unwrap();
        assert!(permissions.contains(&json!("allow-native-brief-save")));
        assert!(!permissions.iter().any(|entry| ["fs:", "dialog:"]
            .iter()
            .any(|prefix| entry.as_str().unwrap().starts_with(prefix))));
        assert!(include_str!("../build.rs").contains("\"native_brief_save\""));
    }
}
