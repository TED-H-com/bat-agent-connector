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
