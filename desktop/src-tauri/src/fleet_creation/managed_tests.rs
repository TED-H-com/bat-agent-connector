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
        let _guard = self.f.security.launcher_guard(&|| Ok(()))?;
        let scope = self.bridge.creation_scope()?;
        self.managed.creation_binding(&scope, |path, limit| {
            CreationPlatform::read_private_attestation(&self.f.security, path, limit)
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
    let _guard = f.f.security.launcher_guard(&|| Ok(())).unwrap();
    let scope = f.bridge.creation_scope().unwrap();
    let mut reads = 0;
    assert!(f
        .managed
        .creation_binding(&scope, |path, limit| {
            let bytes = CreationPlatform::read_private_attestation(&f.f.security, path, limit)?;
            reads += 1;
            if reads == 1 {
                std::fs::write(path, b"changed").unwrap();
            }
            Ok(bytes)
        })
        .is_err());
    assert_eq!(reads, 2);
}

impl ManagedFixture {
    fn security<'a>(
        &'a self,
        ticket: &'a Ticket,
    ) -> crate::fleet_guarded::Guarded<'a, binding::Attested<'a, SecurityFixture>> {
        crate::fleet_guarded::Guarded {
            ticket,
            inner: binding::Attested {
                inner: self.f.security.clone(),
                binding: binding::Binding {
                    managed: &self.managed,
                    bridge: &self.bridge,
                    original: RefCell::default(),
                },
            },
        }
    }
    fn platform<'a>(
        &'a self,
        ticket: &'a Ticket,
        mint: &mut dyn FnMut() -> [String; 2],
    ) -> CreationPlatform<crate::fleet_guarded::Guarded<'a, binding::Attested<'a, SecurityFixture>>>
    {
        CreationPlatform::from_native_input(
            self.f.input(),
            self.security(ticket),
            &|| ticket.verify(),
            mint,
        )
        .unwrap()
    }
}
#[test]
fn managed_context_reserves_reopens_and_withdraws_changed_manifest_or_bridge() {
    let f = ManagedFixture::new();
    let tickets = Arc::new(crate::fleet_lifecycle::Tickets::default());
    let ticket = tickets.capture();
    let mut p = f.platform(&ticket, &mut || ["a".repeat(32), "b".repeat(32)]);
    let prepared = prepare(&mut p, &ticket).unwrap();
    let binding = p.receipt().managed_connector.clone().unwrap();
    assert_eq!(binding.installation_id, "c".repeat(32));
    assert_ne!(binding.installation_id, prepared.owner.installation_id);
    let role = startup_role::evidence(&f.f.data).unwrap();
    assert_eq!(
        p.receipt().startup.installation_ref,
        role.snapshot.installation_ref.unwrap()
    );
    let receipt_path = f.f.state.join("fleet-creation/provision.json");
    let original = std::fs::read(&receipt_path).unwrap();
    drop(p);
    let mut p = f.platform(&ticket, &mut || {
        panic!("existing managed receipt must never mint")
    });
    assert_eq!(prepare(&mut p, &ticket).unwrap().owner, prepared.owner);
    assert_eq!(std::fs::read(&receipt_path).unwrap(), original);
    let mut changed = f.document.clone();
    changed["runtime_version"] = "0.1.10".into();
    f.write(&changed);
    assert!(prepare(&mut p, &ticket).is_err());
    assert_eq!(std::fs::read(&receipt_path).unwrap(), original);
    f.write(&f.document);
    intended_publication(&mut p, &f.f, &prepared.owner, prepared.baseline, &ticket);
    {
        let _guard = p.launcher_guard().unwrap();
        let artifact = bat_fleet_core::connection_setup::Artifact::Inventory;
        assert!(matches!(
            p.ownership(&prepared.owner, artifact, p.receipt().path(artifact), None)
                .unwrap(),
            bat_fleet_core::connection_setup::FileOwnership::Owned { .. }
        ));
    }
    f.bridge.managed_hold(None);
    assert!(p.launcher_guard().is_err());
}
#[test]
fn managed_binding_requires_the_original_ticket_even_before_a_read() {
    let f = ManagedFixture::new();
    let tickets = Arc::new(crate::fleet_lifecycle::Tickets::default());
    let ticket = tickets.capture();
    let security = f.security(&ticket);
    let _guard = security.launcher_guard(&|| Ok(())).unwrap();
    assert!(security.managed_identity().unwrap().is_some());
    tickets.set_stopping(true);
    tickets.set_stopping(false);
    assert_eq!(security.managed_identity(), Err("FLEET_STOP_REQUESTED"));
}
#[test]
fn provider_without_a_proven_manifest_is_never_downgraded_to_no_binding() {
    let f = ManagedFixture::new();
    std::fs::remove_file(f.path()).unwrap();
    std::fs::remove_dir(f.path().parent().unwrap()).unwrap();
    assert!(f.observe().is_err());
}

#[test]
fn external_client_absence_is_distinct_from_unverified_managed_state() {
    let f = Fixture::new();
    let (managed, _) = crate::managed::Managed::prepare(&f.data.join("missing"), &f.data, &f.state);
    let bridge = crate::bridge::Bridge::file_fixture("http://127.0.0.1:41001/", "fixture-token");
    let security = binding::Attested {
        inner: f.security.clone(),
        binding: binding::Binding {
            managed: &managed,
            bridge: &bridge,
            original: RefCell::default(),
        },
    };
    let _guard = security.launcher_guard(&|| Ok(())).unwrap();
    assert_eq!(security.managed_identity(), Ok(None));
    publication_state::create_new_directory(&f.data.join("managed-central")).unwrap();
    assert!(security.managed_identity().is_err());
}
#[test]
fn managed_reader_refuses_missing_or_mismatched_attestation_and_unverified_bridge() {
    let f = ManagedFixture::new();
    let tickets = Arc::new(crate::fleet_lifecycle::Tickets::default());
    let ticket = tickets.capture();
    let p = f.platform(&ticket, &mut || ["a".repeat(32), "b".repeat(32)]);
    let mut receipt = p.receipt().clone();
    drop(p);
    let make = |receipt, security| {
        CreationPlatform::with_security(
            receipt,
            f.f.data.clone(),
            &f.f.data.join("Startup"),
            &f.f.data.join("user"),
            security,
        )
    };
    assert!(make(receipt.clone(), f.f.security.clone()).is_err());
    let security = f.security(&ticket);
    let _guard = security.launcher_guard(&|| Ok(())).unwrap();
    let mut unbound = receipt.clone();
    unbound.managed_connector = None;
    assert!(
        CreationPlatform::with_security(
            unbound,
            f.f.data.clone(),
            &f.f.data.join("Startup"),
            &f.f.data.join("user"),
            f.security(&ticket),
        )
        .is_err(),
        "a later managed binding cannot attach to an unbound receipt"
    );
    receipt.managed_connector.as_mut().unwrap().manifest_sha256 = "0".repeat(64);
    assert!(CreationPlatform::with_security(
        receipt,
        f.f.data.clone(),
        &f.f.data.join("Startup"),
        &f.f.data.join("user"),
        security
    )
    .is_err());
    let scope = f.bridge.creation_scope().unwrap();
    for field in 0..4 {
        let mut changed = scope.clone();
        match field {
            0 => changed.managed = false,
            1 => changed.actor = "changed-actor".into(),
            2 => changed.identity.server_id = "changed-server".into(),
            _ => changed.identity.principal_id = "changed-principal".into(),
        }
        assert!(f
            .managed
            .creation_binding(&changed, |_, _| panic!(
                "identity mismatch precedes manifest reads"
            ))
            .is_err());
    }
    f.bridge.disconnect();
    assert!(f.bridge.creation_scope().is_err());
}
#[cfg(windows)]
#[test]
fn windows_managed_binding_refuses_broad_manifest_or_root_without_repair() {
    for leaf in [false, true] {
        let f = ManagedFixture::new();
        let target = if leaf {
            f.path()
        } else {
            f.path().parent().unwrap().to_path_buf()
        };
        super::windows::set_dacl(&target, true);
        let bytes = std::fs::read(f.path()).unwrap();
        let acl = super::windows::dacl(&target);
        assert!(f
            .managed
            .creation_binding(&f.bridge.creation_scope().unwrap(), |path, limit| {
                publication_state::verify_ancestors(path, path.parent().unwrap())?;
                publication_state::read_private(path, limit)?.ok_or("CREATION_EVIDENCE_UNPROVEN")
            })
            .is_err());
        assert_eq!(std::fs::read(f.path()).unwrap(), bytes);
        assert_eq!(super::windows::dacl(&target), acl);
    }
}
