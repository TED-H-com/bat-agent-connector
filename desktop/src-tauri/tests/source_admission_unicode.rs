//! Record the pinned native runner's Unicode limit using synthetic IDs only.
use bat_fleet_core::credential::AdmissionStore;

#[test]
fn admission_unicode_table_measurement() {
    let compiler = std::process::Command::new("rustc")
        .arg("--version")
        .output()
        .expect("the native test runner provides its compiler");
    assert!(compiler.status.success());
    eprintln!(
        "admission compiler: {}",
        String::from_utf8_lossy(&compiler.stdout).trim()
    );
    eprintln!("admission Unicode tables: {:?}", std::char::UNICODE_VERSION);
    // Cyrillic Tje case pairing entered Unicode 16. Older Python tables may
    // treat these as distinct keys. This records a documented limit, not a
    // replacement Unicode table or a relaxation of admission.
    let bytes = br#"{"enc":false,"data":"{\"tokens\":{\"\u1c89\":\"fixture-one\",\"\u1c8a\":\"fixture-two\"}}"}"#;
    let status = AdmissionStore::parse(bytes).lookup("\u{1c8a}");
    eprintln!(
        "admission Unicode-16 competition: {} / {}",
        status.state, status.reason
    );
    if std::char::UNICODE_VERSION >= (16, 0, 0) {
        assert_eq!(status.reason, "TOKEN_STORE_AMBIGUOUS");
    } else {
        assert_eq!(status.reason, "TOKEN_AVAILABLE");
    }
}

// POSIX ownership is not a Windows ACL fixture. The rootless Linux check-service
// container can chown its own temporary file; ordinary unprivileged CI reports
// the skipped privileged subcase instead of pretending to exercise another UID.
#[cfg(unix)]
#[test]
fn foreign_owned_store_is_refused_by_both_readers_while_legacy_resolves() {
    use bat_fleet_core::{
        credential::{resolve, CredentialRef},
        inventory::Inventory,
        source_catalog,
    };
    use serde_json::{json, Value};
    use std::{
        fs,
        os::unix::fs::{MetadataExt, PermissionsExt},
        path::PathBuf,
        process::Command,
    };

    struct Temporary(PathBuf);
    impl Drop for Temporary {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    // RUNNER_TEMP is the service's disk mount, never /tmp or a tmpfs fixture.
    let Some(root) = std::env::var_os("RUNNER_TEMP") else {
        eprintln!("SKIP foreign-owned file: requires disk RUNNER_TEMP and chown privileges; metadata predicates remain covered in fleet-core");
        return;
    };
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let temp = Temporary(
        PathBuf::from(root).join(format!("admission-owner-{}-{nonce}", std::process::id())),
    );
    let profiles = temp.0.join("BetterAgentTerminal/profiles");
    fs::create_dir_all(&profiles).unwrap();
    let store = profiles.join("remote-tokens.enc.json");
    fs::write(&store, serde_json::to_vec(&json!({"enc":false,"data":json!({"tokens":{"profile-1":"synthetic-owner-fixture"}}).to_string()})).unwrap()).unwrap();
    fs::set_permissions(&store, fs::Permissions::from_mode(0o600)).unwrap();
    let original_uid = store.metadata().unwrap().uid();
    if original_uid != 0 {
        eprintln!("SKIP foreign-owned file: runner lacks chown privileges; held-file uid predicates remain covered in fleet-core");
        return;
    }
    let changed = Command::new("chown")
        .arg(original_uid.wrapping_add(1).to_string())
        .arg(&store)
        .output()
        .unwrap();
    assert!(
        changed.status.success(),
        "the root native check runner must chown its own fixture"
    );
    assert_ne!(store.metadata().unwrap().uid(), original_uid);
    fs::write(profiles.join("index.json"), serde_json::to_vec(&json!({"profiles":[{"id":"profile-1","type":"remote","remoteHost":"example.test","remotePort":9876,"remoteFingerprint":"AA".repeat(32)}]})).unwrap()).unwrap();
    let mut doc: Value = serde_json::from_slice(include_bytes!(
        "../../fleet-core/tests/fixtures/inventory.json"
    ))
    .unwrap();
    let legacy_inventory = Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap();
    let legacy = CredentialRef::for_bat(&legacy_inventory, "node-1").unwrap();
    doc["credentials"]["bat-node-1"]["source_policy"] = json!(1);
    let policy_inventory = Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap();
    let policy = CredentialRef::for_bat(&policy_inventory, "node-1").unwrap();
    let selected = temp.0.join("BetterAgentTerminal");
    assert!(resolve(&selected, &legacy).is_ok());
    assert!(matches!(
        resolve(&selected, &policy),
        Err("TOKEN_STORE_UNREADABLE")
    ));
    let catalog = source_catalog::read(&temp.0);
    assert_eq!(catalog.profiles[0].token.reason, "TOKEN_STORE_UNREADABLE");
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_owned();
    let python = repo.join(".venv/bin/python");
    let result = Command::new(python).args(["-c", "from pathlib import Path; import sys; from bat_agent_connector.connection_sources import read_token_store,TokenMode; r=read_token_store(Path(sys.argv[1]),mode=TokenMode.ADMISSION).lookup('profile-1'); print(r.state+'/'+r.reason)"]).arg(&profiles).output().unwrap();
    assert!(result.status.success());
    assert_eq!(result.stdout, b"unavailable/TOKEN_STORE_UNREADABLE\n");
    eprintln!("foreign-owned disk store: Python unavailable/TOKEN_STORE_UNREADABLE; native catalog and policy reference TOKEN_STORE_UNREADABLE; legacy resolved");
}
