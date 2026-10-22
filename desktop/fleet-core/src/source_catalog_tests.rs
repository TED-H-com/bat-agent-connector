use super::*;
use serde_json::json;
use std::{fs, path::PathBuf};

struct Temporary(PathBuf);
impl Temporary {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "bat-source-catalog-{}-{:032x}",
            std::process::id(),
            rand::random::<u128>()
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn write(&self, relative: &str, bytes: &[u8]) -> PathBuf {
        let path = self.0.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, bytes).unwrap();
        path
    }
}
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn index() -> Vec<u8> {
    serde_json::to_vec(&json!({"profiles":[{
        "id":"remote-1","name":"Fixture BAT","type":"remote","remoteHost":"example.test",
        "remotePort":9876,"remoteFingerprint":"AA".repeat(32)
    }]}))
    .unwrap()
}
fn store(id: &str) -> Vec<u8> {
    serde_json::to_vec(
        &json!({"enc":false,"data":json!({"tokens":{id:"synthetic-sensitive-value"}}).to_string()}),
    )
    .unwrap()
}

#[test]
fn shared_profile_index_vectors_through_listing_and_catalog() {
    let vectors: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/bat-source-policy-v1.json"
    ))
    .unwrap();
    assert!(!vectors["profile_indexes"].as_array().unwrap().is_empty());
    let store = AdmissionStore::parse(&store("remote-1"));
    for row in vectors["profile_indexes"].as_array().unwrap() {
        let bytes = row["index"].as_str().unwrap().as_bytes();
        let listing = parse_profile_index(bytes);
        assert_eq!(
            serde_json::to_value(&listing).unwrap(),
            row["expected"],
            "{}",
            row["id"]
        );
        let catalog = parse(bytes, &store);
        assert_eq!(catalog.reason, listing.reason);
        assert_eq!(catalog.skipped, listing.skipped);
        assert_eq!(
            catalog
                .profiles
                .iter()
                .map(|p| p.profile.clone())
                .collect::<Vec<_>>(),
            listing.profiles
        );
        for entry in &catalog.profiles {
            assert_eq!(entry.token, store.lookup(&entry.profile.id));
        }
        let output = format!("{catalog:?}{}", serde_json::to_string(&catalog).unwrap());
        assert!(!output.contains("synthetic-sensitive-value"));
    }
}

#[test]
fn every_store_vector_is_projected_only_as_admission_status() {
    let vectors: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/bat-source-policy-v1.json"
    ))
    .unwrap();
    for row in vectors["token_stores"].as_array().unwrap() {
        let mut value: Value = serde_json::from_slice(&index()).unwrap();
        value["profiles"][0]["id"] = row["profile_id"].clone();
        let store = AdmissionStore::parse(row["store"].as_str().unwrap().as_bytes());
        let result = parse(&serde_json::to_vec(&value).unwrap(), &store);
        assert_eq!(
            serde_json::to_value(result.profiles[0].token).unwrap(),
            row["expected"]["admission"],
            "{}",
            row["id"]
        );
        assert!(
            !format!("{result:?}{}", serde_json::to_string(&result).unwrap())
                .contains("synthetic-token")
        );
    }
}

#[test]
fn io_selects_once_rechecks_next_call_and_never_mutates_or_falls_back() {
    let temp = Temporary::new();
    assert_eq!(read(&temp.0).reason, "DATA_DIRECTORY_UNAVAILABLE");
    let old_index = temp.write(
        "org.tonyq.better-agent-terminal/profiles/index.json",
        &index(),
    );
    let old_store = temp.write(
        "org.tonyq.better-agent-terminal/profiles/remote-tokens.enc.json",
        &store("remote-1"),
    );
    let before = [fs::read(&old_index).unwrap(), fs::read(&old_store).unwrap()];
    let times = [
        fs::metadata(&old_index).unwrap().modified().unwrap(),
        fs::metadata(&old_store).unwrap().modified().unwrap(),
    ];
    let catalog = read(&temp.0);
    assert_eq!(catalog.profiles[0].token.reason, "TOKEN_AVAILABLE");
    assert_eq!(catalog.profiles[0].profile.remote_profile_id, "default");
    assert!(!format!("{catalog:?}").contains(temp.0.to_str().unwrap()));
    temp.write("BetterAgentTerminal/profiles/index.json", &index());
    assert_eq!(
        read(&temp.0).profiles[0].token.reason,
        "TOKEN_STORE_UNREADABLE"
    );
    let new_store = temp.write(
        "BetterAgentTerminal/profiles/remote-tokens.enc.json",
        &store("REMOTE-1"),
    );
    assert_eq!(read(&temp.0).profiles[0].token.reason, "TOKEN_KEY_MISMATCH");
    fs::write(
        &new_store,
        br#"{"enc":true,"data":"synthetic-sensitive-value"}"#,
    )
    .unwrap();
    assert_eq!(
        read(&temp.0).profiles[0].token.reason,
        "TOKEN_STORE_ENCRYPTED"
    );
    fs::write(temp.0.join("BetterAgentTerminal/profiles/index.json"), b"{").unwrap();
    assert_eq!(read(&temp.0).reason, "PROFILE_INDEX_INVALID");
    assert_eq!(
        before,
        [fs::read(&old_index).unwrap(), fs::read(&old_store).unwrap()]
    );
    assert_eq!(
        times,
        [
            fs::metadata(&old_index).unwrap().modified().unwrap(),
            fs::metadata(&old_store).unwrap().modified().unwrap()
        ]
    );
    assert_eq!(
        fs::read_dir(temp.0.join("BetterAgentTerminal/profiles"))
            .unwrap()
            .count(),
        2
    );
}

#[test]
fn invalid_directory_and_index_bounds_have_fixed_reasons() {
    let temp = Temporary::new();
    let file = temp.write("BetterAgentTerminal", b"not a directory");
    assert_eq!(read(&temp.0).reason, "DATA_DIRECTORY_INVALID");
    fs::remove_file(file).unwrap();
    temp.write(
        "BetterAgentTerminal/profiles/index.json",
        &vec![b' '; INDEX_BOUND + 1],
    );
    assert_eq!(read(&temp.0).reason, "PROFILE_INDEX_INVALID");
    let mut bytes = vec![b' '; INDEX_BOUND - 2];
    bytes.extend_from_slice(b"{}");
    assert_eq!(parse_profile_index(&bytes).reason, "NO_COMPLETE_PROFILES");
    bytes.push(b' ');
    assert_eq!(parse_profile_index(&bytes).reason, "PROFILE_INDEX_INVALID");
    temp.write("BetterAgentTerminal/profiles/index.json", &index());
    temp.write(
        "BetterAgentTerminal/profiles/remote-tokens.enc.json",
        &vec![b' '; INDEX_BOUND + 1],
    );
    assert_eq!(
        read(&temp.0).profiles[0].token.reason,
        "TOKEN_STORE_UNREADABLE"
    );
}

#[cfg(unix)]
#[test]
fn catalog_refuses_linked_files_and_profile_directories() {
    use std::os::unix::fs::symlink;
    let temp = Temporary::new();
    let outside = Temporary::new();
    let target = outside.write("profiles/index.json", &index());
    outside.write("profiles/remote-tokens.enc.json", &store("remote-1"));
    fs::create_dir_all(temp.0.join("BetterAgentTerminal/profiles")).unwrap();
    let link = temp.0.join("BetterAgentTerminal/profiles/index.json");
    symlink(&target, &link).unwrap();
    assert_eq!(read(&temp.0).reason, "PROFILE_INDEX_INVALID");
    fs::remove_file(&link).unwrap();
    fs::write(&link, index()).unwrap();
    symlink(
        outside.0.join("profiles/remote-tokens.enc.json"),
        temp.0
            .join("BetterAgentTerminal/profiles/remote-tokens.enc.json"),
    )
    .unwrap();
    assert_eq!(
        read(&temp.0).profiles[0].token.reason,
        "TOKEN_STORE_UNREADABLE"
    );
    fs::remove_dir_all(temp.0.join("BetterAgentTerminal/profiles")).unwrap();
    symlink(
        outside.0.join("profiles"),
        temp.0.join("BetterAgentTerminal/profiles"),
    )
    .unwrap();
    assert_eq!(read(&temp.0).reason, "PROFILE_INDEX_INVALID");
}
