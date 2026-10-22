use super::*;
use serde_json::json;
use std::path::PathBuf;

#[test]
fn store_bound_is_named_independently_from_index_bound() {
    assert_eq!(crate::source_catalog::STORE_BOUND, STORE_BOUND);
}

#[test]
fn format_errors_precede_ambiguity_after_a_complete_parse() {
    let vectors: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/bat-source-policy-v1.json"
    ))
    .unwrap();
    let mut failures = vec![];
    for row in vectors["token_stores"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| r["id"].as_str().unwrap().starts_with("format-precedence-"))
    {
        let result =
            AdmissionStore::parse(row["store"].as_str().unwrap().as_bytes()).lookup("remote-1");
        eprintln!("{}: {} / {}", row["id"], result.state, result.reason);
        if result != TokenStatus::new("unsupported", "TOKEN_STORE_FORMAT") {
            failures.push(row["id"].clone());
        }
    }
    assert!(
        failures.is_empty(),
        "format precedence failures: {failures:?}"
    );
}

#[test]
fn file_buffer_is_allocated_once_from_bounded_metadata() {
    let temp = Temporary::new();
    for length in [0, 1000, STORE_BOUND] {
        let path = temp.write("profiles/buffer", &vec![b'x'; length]);
        let bytes = read_regular(File::open(path).unwrap(), STORE_BOUND).unwrap();
        assert_eq!(bytes.len(), length);
        assert_eq!(
            bytes.capacity(),
            length + 1,
            "secret buffers must not grow by reallocation"
        );
    }
}

#[cfg(unix)]
#[test]
fn policy_file_modes_are_private_while_legacy_stays_compatible() {
    use std::os::unix::fs::PermissionsExt;
    let temp = Temporary::new();
    let path = temp.write(
        "profiles/remote-tokens.enc.json",
        &profile_store(json!({"tokens":{"profile-1":"fixture-secret"}})),
    );
    let legacy_inv = inventory();
    let legacy = CredentialRef::for_bat(&legacy_inv, "node-1").unwrap();
    let mut doc = legacy_inv.document().clone();
    doc["credentials"]["bat-node-1"]["source_policy"] = json!(1);
    let inv = Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap();
    let policy = CredentialRef::for_bat(&inv, "node-1").unwrap();
    let mut failures = vec![];
    for mode in [0o600, 0o644, 0o640, 0o604] {
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
        assert!(resolve(&temp.0, &legacy).is_ok());
        let result = resolve(&temp.0, &policy);
        eprintln!(
            "policy mode {mode:04o}: {}",
            result
                .as_ref()
                .map(|_| "TOKEN_AVAILABLE")
                .unwrap_or_else(|e| *e)
        );
        if mode == 0o600 {
            assert!(result.is_ok());
        } else if !matches!(result, Err("TOKEN_STORE_UNREADABLE")) {
            failures.push(mode);
        }
    }
    assert!(failures.is_empty(), "unsafe policy modes: {failures:?}");
}

#[cfg(unix)]
#[test]
fn foreign_owner_metadata_is_refused_against_real_uid() {
    use std::os::unix::fs::PermissionsExt;
    let temp = Temporary::new();
    let path = temp.write("profiles/remote-tokens.enc.json", b"{}");
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
    let metadata = File::open(&path).unwrap().metadata().unwrap();
    let uid = unsafe { libc::getuid() };
    assert!(private_owner_mode(&metadata, uid));
    // The actual held-file predicate is tested against a different owner ID,
    // without a privileged chown or replacing any stdlib metadata class.
    assert!(!private_owner_mode(&metadata, uid.wrapping_add(1)));
}

struct Temporary(PathBuf);
impl Temporary {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "bat-fleet-credential-{}-{:032x}",
            std::process::id(),
            rand::random::<u128>()
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn write(&self, relative: &str, bytes: &[u8]) -> PathBuf {
        let path = self.0.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, bytes).unwrap();
        #[cfg(unix)]
        if relative.ends_with("remote-tokens.enc.json") {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        }
        path
    }
}
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn inventory() -> Inventory {
    Inventory::parse(include_bytes!("../tests/fixtures/inventory.json")).unwrap()
}
fn profile_store(inner: Value) -> Vec<u8> {
    serde_json::to_vec(&json!({"enc":false,"data":inner.to_string()})).unwrap()
}
fn missing<T>(value: Result<T>) {
    assert!(matches!(value, Err("CREDENTIAL_MISSING")));
}

#[test]
fn shared_source_policy_vectors_describe_legacy_fleet_runtime() {
    let vectors: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/bat-source-policy-v1.json"
    ))
    .unwrap();
    assert_eq!(vectors["policy"], 1);
    for row in vectors["token_stores"].as_array().unwrap() {
        let result = profile_token(
            row["store"].as_str().unwrap().as_bytes(),
            row["profile_id"].as_str().unwrap(),
        );
        let expected = &row["expected"]["rust_legacy_fleet"];
        assert_eq!(
            result.is_ok(),
            expected["state"] == "available",
            "{}: legacy Fleet runtime (not admission)",
            row["id"]
        );
        match result {
            Ok(token) => assert_eq!(&*token, "synthetic-token"),
            Err(reason) => assert_eq!(reason, expected["reason"].as_str().unwrap()),
        }
    }
}

#[test]
fn native_admission_matches_shared_token_vectors() {
    let vectors: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/bat-source-policy-v1.json"
    ))
    .unwrap();
    for row in vectors["token_stores"].as_array().unwrap() {
        let store = AdmissionStore::parse(row["store"].as_str().unwrap().as_bytes());
        let result = store.lookup(row["profile_id"].as_str().unwrap());
        assert_eq!(
            serde_json::to_value(result).unwrap(),
            row["expected"]["admission"],
            "{}",
            row["id"]
        );
    }
}

#[test]
fn listing_accepts_additive_bat_fields_without_relaxing_kit_drift() {
    let bytes = br#"{"profiles":[{"id":"remote-1","name":"Fixture","type":"remote","remoteHost":"example.test","remotePort":9876,"remoteFingerprint":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","futureDisplayOption":true}],"activeProfileIds":[]}"#;
    let result = crate::inventory::ProfileIndex::parse(bytes, &[]);
    eprintln!(
        "baseline native listing via kit reader: {:?}",
        result
            .as_ref()
            .map(|index| index.document())
            .map_err(|reason| *reason)
    );
    assert!(result.is_err(), "Kit drift validation must remain strict");
    let listing = crate::source_catalog::parse_profile_index(bytes);
    assert_eq!(listing.profiles.len(), 1);
    assert_eq!(listing.reason, "PROFILE_CATALOG_AVAILABLE");
}

#[test]
fn policy_bound_reference_refuses_case_only_store_replacement() {
    let bytes = include_bytes!("../tests/fixtures/inventory.json");
    let mut doc: Value = serde_json::from_slice(bytes).unwrap();
    let key = doc["hosts"][0]["bat"]["credential_ref"]
        .as_str()
        .unwrap()
        .to_owned();
    doc["credentials"][&key]["source_policy"] = json!(1);
    let inv = Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap();
    let reference = CredentialRef::for_bat(&inv, "node-1").unwrap();
    let temp = Temporary::new();
    let path = temp.write(
        "profiles/remote-tokens.enc.json",
        &profile_store(json!({"tokens":{"profile-1":"fixture-secret"}})),
    );
    assert!(resolve(&temp.0, &reference).is_ok());
    std::fs::write(
        &path,
        profile_store(json!({"tokens":{"PROFILE-1":"fixture-secret"}})),
    )
    .unwrap();
    let result = resolve(&temp.0, &reference);
    eprintln!("case-only replacement resolves: {}", result.is_ok());
    assert!(matches!(result, Err("TOKEN_KEY_MISMATCH")));
    std::fs::remove_file(path).unwrap();
    assert!(matches!(
        resolve(&temp.0, &reference),
        Err("TOKEN_STORE_UNREADABLE")
    ));
}

#[test]
fn optional_policy_preserves_old_inventory_bytes_and_legacy_reference_behavior() {
    let bytes = include_bytes!("../tests/fixtures/inventory.json");
    let original: Value = serde_json::from_slice(bytes).unwrap();
    let inv = Inventory::parse(bytes).unwrap();
    assert_eq!(inv.document(), &original);
    assert!(inv.document()["credentials"]
        .as_object()
        .unwrap()
        .values()
        .all(|v| v.get("source_policy").is_none()));
    let temp = Temporary::new();
    let inventory_path = temp.write("inventory.json", bytes);
    let mut body = vec![0xef, 0xbb, 0xbf];
    body.extend(profile_store(
        json!({"tokens":{"PROFILE-1":"legacy-case-compatible"}}),
    ));
    let path = temp.write("profiles/remote-tokens.enc.json", &body);
    let reference = CredentialRef::for_bat(&inv, "node-1").unwrap();
    assert_eq!(
        resolve(&temp.0, &reference).unwrap().fixture_value(),
        "legacy-case-compatible"
    );
    assert_eq!(std::fs::read(&path).unwrap(), body);
    assert_eq!(std::fs::read(inventory_path).unwrap(), bytes);
    std::fs::remove_file(path).unwrap();
    missing(resolve(&temp.0, &reference));
    let key = original["hosts"][0]["bat"]["credential_ref"]
        .as_str()
        .unwrap();
    for policy in [
        json!(0),
        json!(2),
        json!(-1),
        json!(true),
        json!(null),
        json!("1"),
        json!(1.0),
    ] {
        let mut doc = original.clone();
        doc["credentials"][key]["source_policy"] = policy;
        assert!(Inventory::parse(&serde_json::to_vec(&doc).unwrap()).is_err());
    }
    let mut doc = original;
    doc["credentials"]["connector-observe"]["source_policy"] = json!(1);
    assert!(Inventory::parse(&serde_json::to_vec(&doc).unwrap()).is_err());
}

#[test]
fn bound_references_report_every_shared_admission_reason() {
    let vectors: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/bat-source-policy-v1.json"
    ))
    .unwrap();
    for row in vectors["token_stores"].as_array().unwrap() {
        // Inventory host IDs use the Kit's ASCII identifier contract. All store
        // bytes remain unchanged, including escaped competing token keys.
        if row["profile_id"] != "remote-1" {
            continue;
        }
        let mut doc: Value =
            serde_json::from_slice(include_bytes!("../tests/fixtures/inventory.json")).unwrap();
        let key = doc["hosts"][0]["bat"]["credential_ref"]
            .as_str()
            .unwrap()
            .to_owned();
        doc["credentials"][&key]["source_policy"] = json!(1);
        doc["credentials"][&key]["profile_id"] = json!("remote-1");
        let pin = doc["hosts"][0]["bat"]["fingerprint_ref"]
            .as_str()
            .unwrap()
            .to_owned();
        doc["fingerprints"][&pin]["profile_id"] = json!("remote-1");
        doc["hosts"][0]["profile"] = json!("remote-1");
        for profile in doc["provisioning_profiles"].as_array_mut().unwrap() {
            if profile == "profile-1" {
                *profile = json!("remote-1");
            }
        }
        let inv = Inventory::parse(&serde_json::to_vec(&doc).unwrap()).unwrap();
        let reference = CredentialRef::for_bat(&inv, "node-1").unwrap();
        let temp = Temporary::new();
        temp.write(
            "profiles/remote-tokens.enc.json",
            row["store"].as_str().unwrap().as_bytes(),
        );
        let result = resolve(&temp.0, &reference);
        let expected = &row["expected"]["admission"];
        assert_eq!(
            result.is_ok(),
            expected["state"] == "available",
            "{}",
            row["id"]
        );
        if let Err(reason) = result {
            assert_eq!(
                reason,
                expected["reason"].as_str().unwrap(),
                "{}",
                row["id"]
            );
        }
    }
}

#[test]
fn admission_enforces_byte_bounds_bom_and_nested_secret_shapes() {
    let check = |bytes: &[u8]| AdmissionStore::parse(bytes).lookup("profile-1");
    assert_eq!(
        check(&profile_store(
            json!({"tokens":{"profile-1":"t".repeat(TOKEN_BOUND)}})
        ))
        .reason,
        "TOKEN_AVAILABLE"
    );
    let valid = profile_store(json!({"tokens":{"profile-1":"繁".repeat(TOKEN_BOUND / 3)}}));
    assert_eq!(check(&valid).reason, "TOKEN_AVAILABLE");
    let over = profile_store(json!({"tokens":{"profile-1":"繁".repeat(TOKEN_BOUND / 3 + 1)}}));
    assert_eq!(
        check(&over),
        TokenStatus::new("unsupported", "TOKEN_INVALID")
    );
    let mut bytes = vec![b' '; STORE_BOUND - valid.len()];
    bytes.extend(valid);
    assert_eq!(check(&bytes).reason, "TOKEN_AVAILABLE");
    bytes.push(b' ');
    assert_eq!(
        check(&bytes),
        TokenStatus::new("unavailable", "TOKEN_STORE_UNREADABLE")
    );
    for data in ["null", "{}", "{\"tokens\":null}"] {
        assert_eq!(
            check(&serde_json::to_vec(&json!({"enc":false,"data":data})).unwrap()).reason,
            "TOKEN_MISSING"
        );
    }
    assert_eq!(
        check(&serde_json::to_vec(&json!({"enc":false,"data":"\u{feff}{}"})).unwrap()).reason,
        "TOKEN_STORE_FORMAT"
    );
    for depth in [126, 127] {
        let data = format!(
            "{{\"tokens\":{{\"profile-1\":\"fixture\"}},\"extra\":{}0{}}}",
            "[".repeat(depth),
            "]".repeat(depth)
        );
        let result = check(&serde_json::to_vec(&json!({"enc":false,"data":data})).unwrap());
        assert_eq!(
            result.reason,
            if depth == 126 {
                "TOKEN_AVAILABLE"
            } else {
                "TOKEN_STORE_FORMAT"
            }
        );
    }
}
#[test]
fn fixed_profile_reference_reads_exact_kit_format_without_changing_bytes() {
    let temp = Temporary::new();
    let body =
        profile_store(json!({"tokens":{"profile-1":"fixture-one","profile-2":"fixture-two"}}));
    let path = temp.write("profiles/remote-tokens.enc.json", &body);
    let inv = inventory();
    let first = CredentialRef::for_bat(&inv, "node-1").unwrap();
    let second = CredentialRef::for_bat(&inv, "node-2").unwrap();
    assert_eq!(
        resolve(&temp.0, &first).unwrap().fixture_value(),
        "fixture-one"
    );
    assert_eq!(
        resolve(&temp.0, &second).unwrap().fixture_value(),
        "fixture-two"
    );
    assert_eq!(std::fs::read(&path).unwrap(), body);
    assert!(CredentialRef::for_bat(&inv, "missing").is_err());
    let new = profile_store(json!({"tokens":{"profile-1":"replacement"}}));
    std::fs::write(&path, &new).unwrap();
    assert_eq!(
        resolve(&temp.0, &first).unwrap().fixture_value(),
        "replacement"
    );
    missing(resolve(&temp.0, &second)); // No fallback to another profile.
    std::fs::remove_file(&path).unwrap();
    missing(resolve(&temp.0, &first));
}
#[test]
fn connector_does_not_adopt_bat_tokens_or_desktop_credential_record() {
    let temp = Temporary::new();
    let inv = inventory();
    let connector = CredentialRef::for_connector(&inv).unwrap();
    temp.write(
        "profiles/remote-tokens.enc.json",
        &profile_store(
            json!({"tokens":{"profile-1":"operate-secret","connector-observe":"operate-secret"}}),
        ),
    );
    temp.write(
        "desktop-credential.json",
        b"fixture-desktop-mutation-secret",
    );
    missing(resolve(&temp.0, &connector));
    temp.write(
        "fleet-credentials/connector-observe.dpapi",
        b"not-a-protected-token",
    );
    missing(resolve(&temp.0, &connector));
}
#[test]
fn unknown_environment_kind_and_path_names_are_refused_at_inventory_boundary() {
    let base: Value =
        serde_json::from_slice(include_bytes!("../tests/fixtures/inventory.json")).unwrap();
    for bad in [
        json!({"kind":"env","name":"BAT_TOKEN"}),
        json!({"kind":"windows-dpapi","name":"../desktop"}),
        json!({"kind":"windows-dpapi","name":"C:\\other"}),
        json!({"kind":"windows-dpapi","name":"fixture","path":"other"}),
    ] {
        let mut value = base.clone();
        value["credentials"]["connector-observe"] = bad;
        assert!(Inventory::parse(&serde_json::to_vec(&value).unwrap()).is_err());
    }
}
#[test]
fn profile_store_requires_literal_false_and_unambiguous_secret_json() {
    for enc in [Value::Null, json!(true), json!(0), json!("false")] {
        missing(profile_token(
            &serde_json::to_vec(
                &json!({"enc":enc,"data":"{\"tokens\":{\"profile-1\":\"secret\"}}"}),
            )
            .unwrap(),
            "profile-1",
        ));
    }
    for bytes in [
        b"{\"data\":\"{}\"}".as_slice(),
        b"{\"enc\":false,\"Enc\":false,\"data\":\"{}\"}",
        b"{\"enc\":false,\"data\":{\"tokens\":{}}}",
        b"null",
        &[0xff],
    ] {
        missing(profile_token(bytes, "profile-1"));
    }
    for data in [
        "{\"tokens\":{\"profile-1\":\"one\",\"PROFILE-1\":\"two\"}}",
        "{\"tokens\":{\"profile-1\":\"one\",\"profile-\\u0031\":\"two\"}}",
        "{\"tokens\":null}",
        "[]",
    ] {
        missing(profile_token(
            &serde_json::to_vec(&json!({"enc":false,"data":data})).unwrap(),
            "profile-1",
        ));
    }
    let body = profile_store(json!({"tokens":{"PROFILE-1":"case-compatible"}}));
    assert_eq!(
        &*profile_token(&body, "profile-1").unwrap(),
        "case-compatible"
    );
    let mut bom = vec![0xef, 0xbb, 0xbf];
    bom.extend(body);
    assert_eq!(
        &*profile_token(&bom, "profile-1").unwrap(),
        "case-compatible"
    );
}
#[test]
fn token_bounds_types_unicode_and_whitespace_are_explicit() {
    for value in [
        Value::Null,
        json!(true),
        json!(""),
        json!("embedded\0null"),
        json!("x".repeat(TOKEN_BOUND + 1)),
    ] {
        missing(profile_token(
            &profile_store(json!({"tokens":{"profile-1":value}})),
            "profile-1",
        ));
    }
    let value = "  fixture-繁體-🔐  ";
    assert_eq!(
        &*profile_token(
            &profile_store(json!({"tokens":{"profile-1":value}})),
            "profile-1"
        )
        .unwrap(),
        value
    );
    assert_eq!(
        profile_token(
            &profile_store(json!({"tokens":{"profile-1":"x".repeat(TOKEN_BOUND)}})),
            "profile-1"
        )
        .unwrap()
        .len(),
        TOKEN_BOUND
    );
}
#[test]
fn only_bounded_regular_files_under_fixed_directories_are_read() {
    let temp = Temporary::new();
    let reference = CredentialRef::for_bat(&inventory(), "node-1").unwrap();
    missing(resolve(Path::new("relative"), &reference));
    let path = temp.write(
        "profiles/remote-tokens.enc.json",
        &vec![b' '; STORE_BOUND + 1],
    );
    missing(resolve(&temp.0, &reference));
    std::fs::remove_file(&path).unwrap();
    std::fs::create_dir(&path).unwrap();
    missing(resolve(&temp.0, &reference));
}
#[cfg(unix)]
#[test]
fn linked_file_directory_and_fifo_are_refused_without_reading_or_blocking() {
    use std::os::unix::fs::symlink;
    let temp = Temporary::new();
    let outside = Temporary::new();
    let target = outside.write(
        "profiles/remote-tokens.enc.json",
        &profile_store(json!({"tokens":{"profile-1":"outside-secret"}})),
    );
    std::fs::create_dir(temp.0.join("profiles")).unwrap();
    let file = temp.0.join("profiles/remote-tokens.enc.json");
    let reference = CredentialRef::for_bat(&inventory(), "node-1").unwrap();
    symlink(&target, &file).unwrap();
    missing(resolve(&temp.0, &reference));
    std::fs::remove_file(&file).unwrap();
    let c = std::ffi::CString::new(file.as_os_str().as_encoded_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
    missing(resolve(&temp.0, &reference));
    std::fs::remove_file(&file).unwrap();
    std::fs::remove_dir(temp.0.join("profiles")).unwrap();
    symlink(outside.0.join("profiles"), temp.0.join("profiles")).unwrap();
    missing(resolve(&temp.0, &reference));
}
#[test]
fn protected_format_and_plaintext_are_strict_and_bounded() {
    assert_eq!(&*decode_hex(b"0001aAFF").unwrap(), &[0, 1, 170, 255]);
    for bad in [
        b"".as_slice(),
        b"0",
        b"00\n",
        b"gg",
        b"76492d1116743f0423413b16050a5345:other",
    ] {
        missing(decode_hex(bad));
    }
    missing(decode_hex(&vec![b'0'; PROTECTED_BOUND + 2]));
    let value = "fixture-繁體-🔐";
    let bytes: Vec<_> = value.encode_utf16().flat_map(u16::to_le_bytes).collect();
    assert_eq!(&*decode_utf16(&bytes).unwrap(), value);
    for bad in [&[][..], &[0u8][..], &[0, 0][..], &[0, 0xd8][..]] {
        missing(decode_utf16(bad));
    }
    missing(decode_utf16(&vec![b'a'; TOKEN_BOUND * 2 + 2]));
}
#[cfg(not(windows))]
#[test]
fn non_windows_never_decodes_dpapi_as_plain_hex_utf16() {
    let temp = Temporary::new();
    let reference = CredentialRef::for_connector(&inventory()).unwrap();
    temp.write(
        "fleet-credentials/connector-observe.dpapi",
        b"6600690078007400750072006500",
    );
    missing(resolve(&temp.0, &reference));
}
#[test]
fn successful_secret_trees_are_scrubbed_recursively() {
    let mut value = json!({"private-key":{"nested":["fixture-secret",{"other":"fixture-other"}]}});
    scrub(&mut value);
    assert_eq!(value, json!({}));
    let mut array = json!(["fixture-secret", ["other"]]);
    scrub(&mut array);
    assert_eq!(array, json!(["", [""]]));
}

#[cfg(windows)]
#[path = "credential_windows_tests.rs"]
mod windows;
