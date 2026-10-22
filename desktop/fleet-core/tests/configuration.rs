#[cfg(windows)]
#[path = "support/powershell.rs"]
mod powershell_fixture;
use bat_fleet_core::{
    configuration::{data_directory, Configuration, Paths},
    digest,
};
use std::path::Path;
mod support;
use support::Fixture;

#[test]
fn shared_source_policy_directory_vectors() {
    let vectors: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/bat-source-policy-v1.json"
    ))
    .unwrap();
    assert_eq!(vectors["policy"], 1);
    for row in vectors["directories"].as_array().unwrap() {
        let expected = &row["expected"]["rust_legacy_fleet"];
        // Python-only explicit paths/macOS/Linux have no native Fleet selector.
        if expected.is_null() {
            continue;
        }
        // chmod cannot model a Windows ACL denial; Windows is covered by CI's
        // actual platform fixtures, not a fabricated Unix permission result.
        if cfg!(windows) && row["unreadable"].is_string() {
            continue;
        }
        let fixture = Fixture::new();
        for (relative, kind) in row["entries"].as_object().unwrap() {
            let path = fixture.0.join(relative);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            if kind == "directory" {
                std::fs::create_dir_all(path).unwrap();
            } else {
                std::fs::write(path, "synthetic non-directory").unwrap();
            }
        }
        #[cfg(unix)]
        let denied = row["unreadable"].as_str().map(|relative| {
            use std::os::unix::fs::PermissionsExt;
            let path = fixture.0.join(relative);
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o0)).unwrap();
            path
        });
        let actual = data_directory(&fixture.0.join("roaming"));
        // The native selector returns a data root; admission binds its profiles
        // child. Assert the shared admission entry as well as legacy compatibility.
        let admission = &row["expected"]["admission"];
        if let Some(reason) = admission["reason"].as_str() {
            assert_eq!(
                actual.as_ref().map(|_| ()).map_err(|e| *e),
                Err(reason),
                "{}",
                row["id"]
            );
        } else {
            assert_eq!(
                actual.as_ref().unwrap().join("profiles"),
                expected_root(&fixture).join(admission["path"].as_str().unwrap()),
                "{}",
                row["id"]
            );
        }
        #[cfg(unix)]
        if let Some(path) = denied {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        if let Some(reason) = expected["reason"].as_str() {
            assert_eq!(
                actual.as_ref().map(|_| ()).map_err(|e| *e),
                Err(reason),
                "{}",
                row["id"]
            );
        } else {
            assert_eq!(
                actual.unwrap(),
                expected_root(&fixture).join(expected["path"].as_str().unwrap()),
                "{}",
                row["id"]
            );
        }
    }
}

fn expected_root(fixture: &Fixture) -> std::path::PathBuf {
    // Independent expected existing root: the final path of this synthetic
    // directory expands 8.3 aliases. Production deliberately does not use
    // canonicalize because arbitrary configured paths can contain reparse points.
    #[cfg(windows)]
    let root = {
        let canonical = fixture.0.canonicalize().unwrap();
        std::path::PathBuf::from(canonical.to_str().unwrap().strip_prefix(r"\\?\").unwrap())
    };
    #[cfg(not(windows))]
    let root = fixture.0.clone();
    root
}

fn binding_preimage(fixture: &Fixture) -> String {
    let root = expected_root(fixture);
    let mut expected = String::from("desktop-configuration-v1\n");
    for relative in [
        "kit/fleet-inventory.json",
        "kit/bat-profiles/index.json",
        "kit/ssh-config",
        "使用者🦀/.ssh/config",
    ] {
        // Windows PathBuf::join preserves embedded forward slashes. Normalize
        // lexical components like .NET GetFullPath, without canonicalizing the
        // deliberately absent user SSH path (or adding a verbatim prefix).
        let path: std::path::PathBuf = root.join(relative).components().collect();
        let full = path.to_str().unwrap();
        expected.push_str(&format!("{}:{full}:", full.encode_utf16().count()));
        if path.exists() {
            expected.push_str(&format!(
                "present:{}\n",
                digest(&std::fs::read(path).unwrap())
            ));
        } else {
            expected.push_str("absent\n");
        }
    }
    expected
}

#[test]
fn default_binding_matches_powershell_bytes_and_utf16_path_lengths_without_writes() {
    let fixture = Fixture::new();
    let expected = binding_preimage(&fixture);
    let before = std::fs::read(fixture.0.join("kit/fleet-inventory.json")).unwrap();
    let configuration = fixture.load();
    assert_eq!(configuration.binding(), digest(expected.as_bytes()));
    assert!(configuration.issues.is_empty());
    assert_eq!(configuration.inventory.hosts().len(), 5);
    configuration.verify_current().unwrap();
    assert!(!fixture.0.join("使用者🦀/.ssh").exists());
    assert_eq!(
        before,
        std::fs::read(fixture.0.join("kit/fleet-inventory.json")).unwrap()
    );
}

#[test]
fn changing_any_bound_bytes_or_creating_user_ssh_invalidates_old_results() {
    for relative in [
        "kit/fleet-inventory.json",
        "kit/bat-profiles/index.json",
        "kit/ssh-config",
        "使用者🦀/.ssh/config",
    ] {
        let fixture = Fixture::new();
        let configuration = fixture.load();
        let file = fixture.0.join(relative);
        let mut bytes = std::fs::read(&file).unwrap_or_default();
        bytes.extend_from_slice(b"\n");
        std::fs::create_dir_all(file.parent().unwrap()).unwrap();
        std::fs::write(file, bytes).unwrap();
        assert_eq!(configuration.verify_current(), Err("CONFIGURATION_CHANGED"));
        assert_ne!(configuration.binding(), fixture.load().binding());
    }
}

#[test]
fn alternate_inventory_requires_paired_index_and_binds_trusted_schema_and_pins() {
    let fixture = Fixture::new();
    let kit = fixture.0.join("kit");
    let user = fixture.0.join("使用者🦀");
    let alternate = fixture.0.join("other.json");
    std::fs::copy(kit.join("fleet-inventory.json"), &alternate).unwrap();
    assert!(matches!(
        Paths::new(&kit, &user, Some(&alternate), None),
        Err("PROFILE_INDEX_REQUIRED")
    ));
    let index = fixture.0.join("other-index.json");
    std::fs::copy(kit.join("bat-profiles/index.json"), &index).unwrap();
    let configuration =
        Configuration::load(Paths::new(&kit, &user, Some(&alternate), Some(&index)).unwrap())
            .unwrap();
    assert!(configuration.issues.is_empty());
    let trusted = kit.join("bat-profiles/index.json");
    let changed = std::fs::read_to_string(&trusted)
        .unwrap()
        .replace(&"AB".repeat(32), &"CD".repeat(32));
    std::fs::write(trusted, changed).unwrap();
    assert_eq!(configuration.verify_current(), Err("CONFIGURATION_CHANGED"));
    let fresh =
        Configuration::load(Paths::new(&kit, &user, Some(&alternate), Some(&index)).unwrap())
            .unwrap();
    assert!(fresh.issues.iter().any(|i| i.code == "PROFILE_DRIFT"));
}

#[test]
fn bom_is_parsed_but_still_changes_binding_and_oversized_or_malformed_data_refuses() {
    let fixture = Fixture::new();
    let previous = fixture.load();
    let path = fixture.0.join("kit/fleet-inventory.json");
    let mut bytes = b"\xef\xbb\xbf".to_vec();
    bytes.extend_from_slice(include_bytes!("fixtures/inventory.json"));
    std::fs::write(&path, bytes).unwrap();
    assert_ne!(previous.binding(), fixture.load().binding());
    assert_eq!(previous.verify_current(), Err("CONFIGURATION_CHANGED"));
    for bad in [
        vec![b' '; 1_048_577],
        br#"{"schema_version":1,"SCHEMA_VERSION":1}"#.to_vec(),
        vec![0xff],
    ] {
        std::fs::write(&path, bad).unwrap();
        assert!(Configuration::load(fixture.paths()).is_err());
    }
}

#[test]
fn data_directory_rechecks_migration_and_refuses_file_instead_of_directory() {
    let fixture = Fixture::new();
    let root = expected_root(&fixture);
    let old = root.join("org.tonyq.better-agent-terminal");
    let new = root.join("BetterAgentTerminal");
    assert_eq!(data_directory(&fixture.0).unwrap(), new);
    assert!(!new.exists());
    std::fs::create_dir(&old).unwrap();
    assert_eq!(data_directory(&fixture.0).unwrap(), old);
    std::fs::create_dir(&new).unwrap();
    assert_eq!(data_directory(&fixture.0).unwrap(), new);
    std::fs::remove_dir(&new).unwrap();
    std::fs::write(&new, b"not a directory").unwrap();
    assert_eq!(data_directory(&fixture.0), Err("DATA_DIRECTORY_INVALID"));
}

#[test]
fn missing_required_files_refuse_and_normalized_default_path_needs_no_alternate_pair() {
    let fixture = Fixture::new();
    let kit = fixture.0.join("kit");
    let alias = kit.join(Path::new("bat-profiles/../fleet-inventory.json"));
    let paths = Paths::new(&kit, &fixture.0.join("使用者🦀"), Some(&alias), None).unwrap();
    assert_eq!(
        Configuration::load(paths).unwrap().binding(),
        fixture.load().binding()
    );
    std::fs::remove_file(kit.join("ssh-config")).unwrap();
    assert!(matches!(
        Configuration::load(fixture.paths()),
        Err("CONFIGURATION_UNREADABLE")
    ));
}

#[cfg(windows)]
#[test]
fn actual_system_powershell_binding_matches_normalized_native_paths() {
    use base64::{engine::general_purpose::STANDARD, Engine};
    use powershell_fixture::Fixture as PowerShellFixture;
    use std::time::Duration;
    let fixture = Fixture::new();
    let short_root = {
        use std::os::windows::ffi::{OsStrExt, OsStringExt};
        use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;
        let input: Vec<u16> = fixture.0.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut output = vec![0u16; 32768];
        let count =
            unsafe { GetShortPathNameW(input.as_ptr(), output.as_mut_ptr(), output.len() as u32) }
                as usize;
        assert!(count > 0 && count < output.len());
        std::path::PathBuf::from(std::ffi::OsString::from_wide(&output[..count]))
    };
    let script = fixture.0.join("configuration-binding.ps1");
    std::fs::write(
        &script,
        format!(
            "{}{}",
            powershell_fixture::READY_SCRIPT,
            include_str!("fixtures/configuration-binding.ps1")
        ),
    )
    .unwrap();
    for present in [false, true] {
        if present {
            let path = fixture.0.join("使用者🦀/.ssh/config");
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, b"# synthetic user configuration\r\n").unwrap();
        }
        let configuration = Configuration::load(
            Paths::new(
                &short_root.join("kit"),
                &short_root.join("使用者🦀"),
                None,
                None,
            )
            .unwrap(),
        )
        .unwrap();
        assert_eq!(configuration.binding(), fixture.load().binding());
        let process = PowerShellFixture::new();
        let mut command = powershell_fixture::command();
        command
            .args(["-ExecutionPolicy", "Bypass", "-File"])
            .arg(&script)
            .env("BAT_FLEET_BINDING_FIXTURE", &short_root);
        let output = process.run(&mut command, Duration::from_secs(30));
        assert!(output.len() <= 32_768);
        let response: serde_json::Value = serde_json::from_slice(&output).unwrap();
        let preimage = STANDARD
            .decode(response["preimage_base64"].as_str().unwrap())
            .unwrap();
        let preimage = String::from_utf8(preimage).unwrap();
        assert_eq!(digest(preimage.as_bytes()), response["binding"]);
        // Diagnostic input is exclusively this temporary fixture. A failed oracle
        // identifies the path/length/content divergence rather than just two hashes.
        assert_eq!(preimage, binding_preimage(&fixture));
        assert_eq!(
            response["binding"].as_str().unwrap(),
            configuration.binding()
        );
        configuration.verify_current().unwrap();
    }
}

#[test]
fn alternate_profile_index_cannot_extend_the_profile_record_schema() {
    let fixture = Fixture::new();
    let alternate = fixture.0.join("alternate-index.json");
    let mut document: serde_json::Value = serde_json::from_slice(
        &std::fs::read(fixture.0.join("kit/bat-profiles/index.json")).unwrap(),
    )
    .unwrap();
    document["profiles"][0]["command"] = serde_json::json!("unexpected");
    std::fs::write(&alternate, serde_json::to_vec(&document).unwrap()).unwrap();
    let paths = Paths::new(
        &fixture.0.join("kit"),
        &fixture.0.join("使用者🦀"),
        None,
        Some(&alternate),
    )
    .unwrap();
    assert!(matches!(
        Configuration::load(paths),
        Err("PROFILE_INDEX_INVALID")
    ));
}
