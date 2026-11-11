use super::*;
use crate::discovery::Ownership;
use std::{cell::Cell, path::PathBuf};

fn login() -> LoginIdentity {
    LoginIdentity {
        owner_sid: "S-1-5-21-123-456-789-1001".into(),
        session_id: 7,
    }
}
fn process() -> ProcessSnapshot {
    ProcessSnapshot {
        pid: 42,
        created_filetime: 123000000,
        executable: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe".into(),
        arguments: vec![
            "-NoProfile".into(),
            "-NonInteractive".into(),
            "-WindowStyle".into(),
            "Hidden".into(),
            "-File".into(),
            "C:\\Kit\\bat-connect.ps1".into(),
        ],
        login: login(),
    }
}
fn owner() -> MonitorIdentity {
    MonitorIdentity {
        process: process(),
        backend: Backend::Powershell,
        instance: Some("a".repeat(32)),
        ownership: Ownership::CurrentLogin,
        legacy: false,
        directories: vec![],
        record: None,
    }
}
#[test]
fn fixed_powershell_arguments_preserve_paths_as_individual_arguments() {
    let root = std::env::temp_dir().join("fixture ; $(never executed) ' 繁體");
    let script = root.join("client/bat-connect.ps1");
    let inventory = root.join("client/fleet-inventory.json");
    let index = root.join("client/bat-profiles/index.json");
    assert_eq!(
        ps_arguments(&script, &inventory, &index).unwrap(),
        vec![
            OsString::from("-ExecutionPolicy"),
            "Bypass".into(),
            "-NoProfile".into(),
            "-NonInteractive".into(),
            "-WindowStyle".into(),
            "Hidden".into(),
            "-File".into(),
            script.into(),
            "-InventoryPath".into(),
            inventory.into(),
            "-ProfileIndexPath".into(),
            index.into(),
        ]
    );
    assert!(ps_arguments(Path::new("relative.ps1"), &root, &root).is_err());
}
#[test]
fn powershell_environment_keeps_routing_but_drops_credentials_and_user_module_paths() {
    let system = PathBuf::from("/trusted/Windows/System32");
    let selected = ps_environment(
        &system,
        [
            ("Path", "routing-helpers"),
            ("APPDATA", "synthetic-roaming"),
            ("TEMP", "synthetic-temp"),
            ("BATC_API_TOKEN", "secret-placeholder"),
            ("BAT_TOKEN", "secret-placeholder"),
            ("HTTPS_PROXY", "secret-placeholder"),
            ("PSModulePath", "untrusted-modules"),
            ("SystemRoot", "untrusted-windows"),
            ("WINDIR", "untrusted-windows"),
            ("OTHER", "value"),
        ]
        .map(|(k, v)| (k.into(), v.into())),
    );
    assert_eq!(
        selected,
        vec![
            ("Path".into(), "routing-helpers".into()),
            ("APPDATA".into(), "synthetic-roaming".into()),
            ("TEMP".into(), "synthetic-temp".into()),
            (
                "PSModulePath".into(),
                system.join("WindowsPowerShell/v1.0/Modules").into()
            ),
            ("SystemRoot".into(), system.parent().unwrap().into()),
            ("WINDIR".into(), system.parent().unwrap().into()),
        ]
    );
}
#[test]
fn readback_waits_for_exact_born_child_not_a_different_owner() {
    for kind in 0..7 {
        let mut current = owner();
        match kind {
            0 => current.process.pid += 1,
            1 => current.process.created_filetime += 1,
            2 => current.process.arguments.push("--unexpected".into()),
            3 => current.backend = Backend::Rust,
            4 => current.process.login.session_id += 1,
            5 => current.legacy = true,
            _ => current.instance = None,
        }
        assert!(wait_for_owner(
            &process(),
            Backend::Powershell,
            &login(),
            || Ok(Some(current.clone())),
            || Duration::ZERO,
            |_| panic!("conflicting owner cannot retry readback")
        )
        .is_err());
    }
    let calls = Cell::new(0);
    let time = Cell::new(Duration::ZERO);
    assert_eq!(
        wait_for_owner(
            &process(),
            Backend::Powershell,
            &login(),
            || {
                calls.set(calls.get() + 1);
                Ok((calls.get() == 3).then(owner))
            },
            || time.get(),
            |d| time.set(time.get() + d)
        ),
        Ok(())
    );
    assert_eq!(calls.get(), 3);
}
#[test]
fn missing_owner_readback_is_bounded_and_unknown_observation_does_not_retry() {
    let time = Cell::new(Duration::ZERO);
    let calls = Cell::new(0);
    assert_eq!(
        wait_for_owner(
            &process(),
            Backend::Powershell,
            &login(),
            || {
                calls.set(calls.get() + 1);
                Ok(None)
            },
            || time.get(),
            |d| time.set(time.get() + d)
        ),
        Err("MIGRATION_LAUNCH_UNKNOWN")
    );
    assert_eq!(time.get(), Duration::from_secs(5));
    assert_eq!(calls.get(), 101);
    assert_eq!(
        wait_for_owner(
            &process(),
            Backend::Powershell,
            &login(),
            || Err("OWNER_UNPROVEN"),
            || Duration::ZERO,
            |_| panic!("unknown query cannot be retried here")
        ),
        Err("OWNER_UNPROVEN")
    );
}

#[test]
fn intended_backend_replacement_uses_new_bytes_without_adopting_a_new_installation() {
    struct Temp(PathBuf);
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    let temp = Temp(std::env::temp_dir().join(format!(
        "bac-native-migration-{:032x}",
        rand::random::<u128>()
    )));
    for root in ["original", "other"] {
        let client = temp.0.join(root).join("client");
        std::fs::create_dir_all(&client).unwrap();
        std::fs::write(client.join("fleet-desktop.ps1"), b"# fixture only").unwrap();
    }
    let path = temp.0.join("fleet.json");
    let original =
        serde_json::to_vec(&serde_json::json!({"kit_root":temp.0.join("original")})).unwrap();
    std::fs::write(&path, &original).unwrap();
    let captured = Snapshot::load(&path).unwrap();
    let next = captured.backend_payload(Backend::Rust).unwrap();
    std::fs::write(&path, &next).unwrap();
    assert_eq!(captured.verify_current(), Err("INSTALLATION_CHANGED"));
    let fresh = current_installation(&captured).unwrap();
    assert_eq!(fresh.backend(), Backend::Rust);
    fresh.verify_current().unwrap();
    captured
        .validate_payload(&original, Backend::Powershell)
        .unwrap();
    captured.validate_payload(&next, Backend::Rust).unwrap();
    std::fs::write(
        &path,
        serde_json::to_vec(&serde_json::json!({"kit_root":temp.0.join("other"),"backend":"rust"}))
            .unwrap(),
    )
    .unwrap();
    assert!(matches!(
        current_installation(&captured),
        Err("INSTALLATION_CHANGED")
    ));
}
