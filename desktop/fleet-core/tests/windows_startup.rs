#![cfg(windows)]
use bat_fleet_core::{discovery::Backend, installation::canonical_local, windows_startup::Codec};
use std::{
    path::PathBuf,
    time::{Duration, Instant},
};
#[path = "support/powershell.rs"]
mod powershell_fixture;
#[path = "support/public_installation.rs"]
mod public_installation;
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "bac-startup-{}-{:032x}",
            std::process::id(),
            rand::random::<u128>()
        ));
        for d in ["kit/client", "app", "system", "other"] {
            std::fs::create_dir_all(p.join(d)).unwrap();
        }
        for f in [
            "kit/client/Open BAT.vbs",
            "app/dashboard.exe",
            "system/wscript.exe",
            "fleet.json",
            "other/foreign.exe",
        ] {
            std::fs::write(p.join(f), b"synthetic file, never executed").unwrap();
        }
        Self(p)
    }
    fn codec(&self) -> Codec {
        Codec::new(
            &self.0.join("kit/client"),
            &self.0.join("app/dashboard.exe"),
            &self.0.join("fleet.json"),
            &self.0.join("system"),
        )
        .unwrap()
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
#[test]
fn temporary_links_round_trip_exact_fixed_commands_and_reject_foreign_sources() {
    let t = Temp::new();
    let c = t.codec();
    for backend in [Backend::Powershell, Backend::Rust] {
        let b = c.render(backend).unwrap();
        assert_eq!(c.classify(&b), Ok(backend));
        let mut elevated = b.clone();
        let flags = u32::from_le_bytes(elevated[20..24].try_into().unwrap()) | 0x2000;
        elevated[20..24].copy_from_slice(&flags.to_le_bytes());
        assert_eq!(c.classify(&elevated), Err("STARTUP_UNOWNED"));
        let other = Temp::new();
        assert!(other.codec().classify(&b).is_err());
    }
    for b in [vec![], vec![0; 64], vec![0; 1_048_577]] {
        assert!(c.classify(&b).is_err());
    }
}

#[test]
fn public_rust_layout_previews_and_applies_startup_without_legacy_files() {
    use bat_fleet_core::{
        configuration::Paths,
        discovery::MonitorIdentity,
        installation::Snapshot,
        migration::{Owner, Phase, Platform, Store},
        process_adapter::LoginIdentity,
        windows_migration::WindowsMigration,
        Result,
    };
    // All real native validation, locks and shortcut effects use temporary paths.
    // Intercept only the final spawn: this test never starts a monitor or tunnel.
    struct NoLaunch {
        inner: WindowsMigration,
        launches: usize,
    }
    impl Platform for NoLaunch {
        type LauncherGuard = <WindowsMigration as Platform>::LauncherGuard;
        type MonitorGuard = <WindowsMigration as Platform>::MonitorGuard;
        fn launcher_guard(&mut self) -> Result<Self::LauncherGuard> {
            self.inner.launcher_guard()
        }
        fn monitor_guard(&mut self) -> Result<Self::MonitorGuard> {
            self.inner.monitor_guard()
        }
        fn login(&self) -> Result<LoginIdentity> {
            self.inner.login()
        }
        fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
            self.inner.discover()
        }
        fn request_quit(&mut self, _: &Owner) -> Result<()> {
            panic!("no fixture owner to stop")
        }
        fn launch(&mut self, backend: Backend) -> Result<()> {
            assert_eq!(backend, Backend::Rust);
            self.launches += 1;
            Err("FIXTURE_NO_SPAWN")
        }
        fn validate_config(&self, bytes: &[u8], backend: Backend) -> Result<()> {
            self.inner.validate_config(bytes, backend)
        }
        fn classify_shortcut(&self, bytes: &[u8]) -> Result<Backend> {
            self.inner.classify_shortcut(bytes)
        }
        fn shortcut(&self, backend: Backend) -> Result<Vec<u8>> {
            self.inner.shortcut(backend)
        }
    }
    fn wait_for_fixture_mutex<T>(mut action: impl FnMut() -> Result<T>) -> Result<T> {
        let until = Instant::now() + Duration::from_secs(5);
        loop {
            match action() {
                Err("LAUNCHER_BUSY" | "MONITOR_ALREADY_RUNNING") if Instant::now() < until => {
                    std::thread::sleep(Duration::from_millis(20));
                }
                result => return result,
            }
        }
    }
    let t = Temp::new();
    let root = t.0.join("public");
    public_installation::write(&root, true);
    let config = root.join("fleet.json");
    let snapshot = Snapshot::load(&config).unwrap();
    for name in ["fleet-desktop.ps1", "bat-connect.ps1", "Open BAT.vbs"] {
        assert!(!snapshot.client_root().join(name).exists());
    }
    let startup = t.0.join("isolated-startup");
    let roaming = t.0.join("isolated-roaming");
    let system = t.0.join("no-legacy-system");
    for dir in [&startup, &roaming, &system] {
        std::fs::create_dir(dir).unwrap();
    }
    std::fs::write(startup.join("Unrelated.lnk"), b"untouched").unwrap();
    let native = t.0.join("app/dashboard.exe");
    let codec = Codec::new(snapshot.client_root(), &native, &config, &system).unwrap();
    assert!(codec.render(Backend::Powershell).is_err());
    let paths = Paths::new(snapshot.client_root(), &t.0, None, None).unwrap();
    let inner = WindowsMigration::new(
        snapshot.clone(),
        paths,
        roaming,
        t.0.join("fixture.quit"),
        native,
        system,
    )
    .unwrap();
    let mut platform = NoLaunch { inner, launches: 0 };
    let store = Store::new(&config, &startup).unwrap();
    let preview = wait_for_fixture_mutex(|| store.preview(&mut platform, Backend::Rust)).unwrap();
    assert!(!preview.autostart_entry_present());
    let original = std::fs::read(&config).unwrap();
    let mut legacy: serde_json::Value = serde_json::from_slice(&original).unwrap();
    legacy["backend"] = serde_json::json!("powershell");
    assert_eq!(
        wait_for_fixture_mutex(|| store.begin(
            &mut platform,
            "44444444444444444444444444444444",
            &preview,
            Backend::Powershell,
            &serde_json::to_vec(&legacy).unwrap(),
            false,
        )),
        Err("POWERSHELL_ADAPTER_UNAVAILABLE")
    );
    assert_eq!(std::fs::read(&config).unwrap(), original);
    assert!(!startup.join("Open BAT.lnk").exists());
    assert!(store.pending().unwrap().is_none());
    assert_eq!(platform.launches, 0);
    let next = snapshot.backend_payload(Backend::Rust).unwrap();
    let id = "33333333333333333333333333333333";
    assert_eq!(
        wait_for_fixture_mutex(|| store.begin(
            &mut platform,
            id,
            &preview,
            Backend::Rust,
            &next,
            true
        )),
        Ok(Phase::Prepared)
    );
    assert_eq!(
        wait_for_fixture_mutex(|| store.advance(&mut platform, id)),
        Err("FIXTURE_NO_SPAWN")
    );
    assert_eq!(store.status(id).unwrap().phase, Phase::LaunchRequested);
    assert_eq!(std::fs::read(&config).unwrap(), next);
    assert_eq!(
        codec.classify(&std::fs::read(startup.join("Open BAT.lnk")).unwrap()),
        Ok(Backend::Rust)
    );
    assert_eq!(
        std::fs::read(startup.join("Unrelated.lnk")).unwrap(),
        b"untouched"
    );
    assert_eq!(
        wait_for_fixture_mutex(|| store.advance(&mut platform, id)),
        Err("MIGRATION_LAUNCH_UNKNOWN")
    );
    assert_eq!(platform.launches, 1);
}
#[test]
fn real_wscript_shell_shortcut_uses_kit_format_but_modified_arguments_refuse() {
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;
    let t = Temp::new();
    let c = t.codec();
    // Read-only relative to the installed system: all shortcuts/targets live in this temp fixture.
    let script = t.0.join("write-fixture.ps1");
    std::fs::write(
        &script,
        [powershell_fixture::READY_SCRIPT, r#"$ErrorActionPreference='Stop'
$r=$env:BAT_FLEET_STARTUP_FIXTURE
$ws=New-Object -ComObject WScript.Shell
$link=Join-Path $r 'fixture.lnk'
$sc=$ws.CreateShortcut($link)
$case=$env:BAT_FLEET_STARTUP_CASE
if ($env:BAT_FLEET_STARTUP_BACKEND -eq 'powershell') {
    $sc.TargetPath=Join-Path $r 'system\wscript.exe'
    $argument=Join-Path $r 'kit\client\Open BAT.vbs'
    $working=Join-Path $r 'kit\client'
    if ($case -eq 'reparse') {
        $alias=Join-Path $r 'client-alias'
        if (-not (Test-Path $alias)) {New-Item -ItemType Junction -Path $alias -Target $working | Out-Null}
        $working=$alias
        $argument=Join-Path $alias 'Open BAT.vbs'
    }
    $prefix=''
} else {
    $sc.TargetPath=Join-Path $r 'app\dashboard.exe'
    $argument=Join-Path $r 'fleet.json'
    $working=Join-Path $r 'app'
    $prefix='--fleet-login --fleet-config '
}
if ($case -eq 'case') {$argument=$argument.ToUpperInvariant();$working=$working.ToUpperInvariant()}
$sc.Arguments=$prefix+'"'+$argument+'"'
if ($case -eq 'extra') {$sc.Arguments+=' --unexpected'}
if ($case -eq 'foreign-target') {$sc.TargetPath=Join-Path $r 'other\foreign.exe'}
if ($case -eq 'foreign-cwd') {$working=Join-Path $r 'other'}
$sc.WorkingDirectory=$working
$sc.Description='Synthetic Kit startup, never executed'
$sc.Save()
$read=$ws.CreateShortcut($link)
$diagnostic=@{target=$read.TargetPath;arguments=$read.Arguments;working=$read.WorkingDirectory} | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $r 'fixture-diagnostic.json'),$diagnostic)
"#].concat(),
    )
    .unwrap();
    let long_root = canonical_local(&t.0).unwrap();
    let input: Vec<u16> = long_root.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut output = vec![0u16; 32768];
    let count =
        unsafe { GetShortPathNameW(input.as_ptr(), output.as_mut_ptr(), output.len() as u32) }
            as usize;
    assert!(count > 0 && count < output.len());
    let short_root = PathBuf::from(std::ffi::OsString::from_wide(&output[..count]));
    for backend in [Backend::Powershell, Backend::Rust] {
        for (spelling, root) in [("long", &long_root), ("short", &short_root)] {
            for case in [
                "exact",
                "case",
                "extra",
                "foreign-target",
                "foreign-cwd",
                "reparse",
            ] {
                if backend == Backend::Rust && case == "reparse" {
                    continue; // the PS case proves directory aliases are not resolved into ownership
                }
                let process = powershell_fixture::Fixture::new();
                let mut command = powershell_fixture::command();
                command
                    .args(["-ExecutionPolicy", "Bypass", "-File"])
                    .arg(&script)
                    .env("BAT_FLEET_STARTUP_FIXTURE", root)
                    .env("BAT_FLEET_STARTUP_CASE", case)
                    .env(
                        "BAT_FLEET_STARTUP_BACKEND",
                        if backend == Backend::Powershell {
                            "powershell"
                        } else {
                            "rust"
                        },
                    );
                process.run(&mut command, Duration::from_secs(30));
                let bytes = std::fs::read(t.0.join("fixture.lnk")).unwrap();
                let actual = c.classify(&bytes);
                let expected = if matches!(case, "exact" | "case") {
                    Ok(backend)
                } else {
                    Err("STARTUP_UNOWNED")
                };
                assert_eq!(actual, expected,
                    "synthetic fixture {backend:?}/{spelling}/{case}, flags={:#x}, WScript fields={}",
                    u32::from_le_bytes(bytes[20..24].try_into().unwrap()),
                    std::fs::read_to_string(t.0.join("fixture-diagnostic.json")).unwrap());
            }
        }
    }
}
