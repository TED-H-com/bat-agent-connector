use super::*;
use serde_json::json;
use std::path::PathBuf;

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
