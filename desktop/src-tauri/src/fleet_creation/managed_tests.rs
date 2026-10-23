use super::*;
use bat_fleet_core::{creation_evidence::ManagedIdentity, publication_state};
use std::io::Write;

struct ManagedFixture {
    f: Fixture,
    managed: crate::managed::Managed,
    bridge: crate::bridge::Bridge,
    document: serde_json::Value,
}
impl ManagedFixture {
    fn new() -> Self {
        let f = Fixture::with_role(startup_role::Role::Provider);
        let root = f.data.join("managed-central");
        publication_state::create_new_directory(&root).unwrap();
        let id = "c".repeat(32);
        let document = serde_json::json!({
            "protocol": 1, "installation_id": id, "actor": format!("desktop-{id}"),
            "data_dir": root, "runtime_version": "0.1.9", "state": "ready",
            "server_id": "fixture-server", "principal_id": "fixture-principal"
        });
        // Exact installation manifest fields emitted by managed_runtime's ready transition.
        let launch: crate::managed::Launch = serde_json::from_value(serde_json::json!({
            "protocol": 1, "endpoint": "http://127.0.0.1:41001/", "actor": document["actor"],
            "server_id": document["server_id"], "principal_id": document["principal_id"],
            "runtime_version": "0.1.9", "token": "fixture-secret-token"
        }))
        .unwrap();
        let managed = crate::managed::Managed::creation_fixture(&f.data, &f.state, &launch);
        let bridge = crate::bridge::Bridge::creation_fixture(&launch);
        let this = Self {
            f,
            managed,
            bridge,
            document,
        };
        this.write(&this.document);
        this
    }
    fn path(&self) -> PathBuf {
        self.f.data.join("managed-central/installation.json")
    }
    fn write(&self, document: &serde_json::Value) {
        let path = self.path();
        let bytes = serde_json::to_vec(document).unwrap();
        if path.exists() {
            std::fs::write(path, bytes).unwrap();
        } else {
            publication_state::create_new_file(&path)
                .unwrap()
                .write_all(&bytes)
                .unwrap();
        }
    }
    fn observe(&self) -> Result<Option<ManagedIdentity>> {
        let scope = self.bridge.creation_scope()?;
        self.managed.creation_binding(&scope, |path, limit| {
            publication_state::verify_ancestors(path, path.parent().unwrap())?;
            publication_state::read_private(path, limit)?.ok_or("CREATION_EVIDENCE_UNPROVEN")
        })
    }
}
#[test]
fn managed_binding_reads_the_manifest_id_and_rejects_actor_identity_substitution() {
    let f = ManagedFixture::new();
    let binding = f.observe().unwrap().unwrap();
    assert_eq!(binding.installation_id, "c".repeat(32));
    assert_eq!(
        binding.manifest_sha256,
        bat_fleet_core::digest(&std::fs::read(f.path()).unwrap())
    );
    let view = serde_json::to_string(&binding).unwrap();
    assert!(!view.contains("token"));
    assert!(!view.contains("fixture-secret"));
    for (field, value) in [
        ("installation_id", serde_json::json!("d".repeat(32))),
        ("state", serde_json::json!("initializing")),
        ("actor", serde_json::json!("desktop-wrong")),
        ("server_id", serde_json::json!("changed-server")),
        ("principal_id", serde_json::json!("changed-principal")),
        ("data_dir", serde_json::json!(f.f.data)),
    ] {
        let mut changed = f.document.clone();
        changed[field] = value;
        f.write(&changed);
        assert!(f.observe().is_err(), "must refuse changed {field}");
    }
    f.write(&f.document);
    let scope = f.bridge.creation_scope().unwrap();
    let mut reads = 0;
    assert!(f
        .managed
        .creation_binding(&scope, |path, limit| {
            let bytes = publication_state::read_private(path, limit)?.unwrap();
            reads += 1;
            if reads == 1 {
                std::fs::write(path, b"changed").unwrap();
            }
            Ok(bytes)
        })
        .is_err());
    assert_eq!(reads, 2);
}
