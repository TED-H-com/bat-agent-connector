use super::*;
use crate::powershell_fixture::{self, Fixture};
use std::process::{Child, Command, Stdio};
struct ChildGuard(Child);
impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn child() -> ChildGuard {
    ChildGuard(
        Command::new(std::env::current_exe().unwrap())
            .args([
                "--ignored",
                "--exact",
                "windows::tests::owned_child_fixture",
                "--nocapture",
            ])
            .env("BAT_FLEET_OWNED_FIXTURE", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    )
}
#[test]
#[ignore = "runs only as a bounded child of owned handle tests"]
fn owned_child_fixture() {
    if std::env::var("BAT_FLEET_OWNED_FIXTURE").as_deref() == Ok("1") {
        // The CIM oracle can spend 60s starting PowerShell, then 15s working.
        std::thread::sleep(Duration::from_secs(90));
    }
}
#[test]
fn held_child_identity_and_termination() {
    let mut child = child();
    let mut held = WindowsProcess::from_child(&child.0).unwrap();
    let snapshot = held.snapshot().unwrap();
    assert_eq!(snapshot.pid, child.0.id());
    assert_eq!(snapshot.login, current_login().unwrap());
    assert_eq!(
        snapshot.arguments,
        [
            "--ignored",
            "--exact",
            "windows::tests::owned_child_fixture",
            "--nocapture"
        ]
    );
    assert!(snapshot
        .executable
        .eq_ignore_ascii_case(std::env::current_exe().unwrap().to_str().unwrap()));
    assert_eq!(
        process_state(snapshot.pid),
        ProcessState::Live {
            created: process_adapter::legacy_created(snapshot.created_filetime).unwrap()
        }
    );
    held.terminate_and_wait().unwrap();
    assert!(!child.0.wait().unwrap().success());
    assert!(!running(held.handle.0).unwrap());
}
#[test]
fn cim_creation_ticks_match_legacy_records() {
    let fixture = Fixture::new(); // Do not age the observed child while queued for PowerShell.
    let child = child();
    let held = WindowsProcess::from_child(&child.0).unwrap();
    let snapshot = held.snapshot().unwrap();
    // Test-only system PowerShell observes this test's own child. Production never shells out.
    let script=format!("$ErrorActionPreference='Stop'; (Get-CimInstance Win32_Process -Filter 'ProcessId={}').CreationDate.ToUniversalTime().Ticks.ToString()",child.0.id());
    assert_eq!(
        powershell(&fixture, &script, &[]).trim(),
        process_adapter::legacy_created(snapshot.created_filetime).unwrap()
    );
}
#[test]
fn named_monitor_mutex_blocks_another_thread_and_releases() {
    // Synthetic SID, never the actual account's Fleet mutex or a real monitor.
    let sid = format!(
        "S-1-5-21-424242-{}-{}-1001",
        std::process::id(),
        Instant::now().elapsed().as_nanos()
    );
    let name = monitor_mutex(&sid).unwrap();
    let login = LoginIdentity {
        owner_sid: sid,
        session_id: 123,
    };
    let guard = MonitorMutex::named(&name, login.clone()).unwrap().unwrap();
    assert!(MonitorMutex::named(&name, login.clone()).unwrap().is_none());
    assert_eq!(powershell(&Fixture::new(), "$m=[Threading.Mutex]::new($false,$env:BAT_FLEET_FIXTURE_MUTEX);try{$owned=$m.WaitOne(0); if($owned){$m.ReleaseMutex()};$owned.ToString()}finally{$m.Dispose()}", &[("BAT_FLEET_FIXTURE_MUTEX",&name)]).trim(),"False");
    let next = name.clone();
    let next_login = login.clone();
    assert!(
        !std::thread::spawn(move || MonitorMutex::named(&next, next_login).unwrap().is_some())
            .join()
            .unwrap()
    );
    drop(guard);
    assert!(MonitorMutex::named(&name, login).unwrap().is_some());
}
#[test]
fn shared_preference_lock_preserves_bytes_and_never_unlinks() {
    let dir = std::env::temp_dir().join(format!("bat-fleet-lock-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("fleet-client.json");
    std::fs::write(&path, b"original").unwrap();
    let held = PreferenceLock::acquire(&path).unwrap();
    assert!(PreferenceLock::try_acquire(&path).unwrap().is_none());
    assert_eq!(std::fs::read(&path).unwrap(), b"original");
    assert_eq!(powershell(&Fixture::new(), r#"[Console]::Error.Write("BATC_PHASE file-open-before`n");[Console]::Error.Flush()
try {
  $f=[IO.File]::Open($env:BAT_FLEET_FIXTURE_LOCK,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None);
  [Console]::Error.Write("BATC_PHASE file-open-after`n");[Console]::Error.Flush()
  $f.Dispose();
  [Console]::Error.Write("BATC_PHASE output-before`n");[Console]::Error.Flush()
  'unexpected'
  [Console]::Error.Write("BATC_PHASE output-after`n");[Console]::Error.Flush()
} catch [IO.IOException] {
  [Console]::Error.Write("BATC_PHASE file-open-refused`n");[Console]::Error.Flush()
  [Console]::Error.Write("BATC_PHASE output-before`n");[Console]::Error.Flush()
  'blocked'
  [Console]::Error.Write("BATC_PHASE output-after`n");[Console]::Error.Flush()
}
[Console]::Error.Write("BATC_PHASE script-end`n");[Console]::Error.Flush()"#, &[("BAT_FLEET_FIXTURE_LOCK",dir.join("fleet-client.json.lock").to_str().unwrap())]).trim(),"blocked");
    drop(held);
    assert!(dir.join("fleet-client.json.lock").exists());
    drop(PreferenceLock::acquire(&path).unwrap());
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn tunnel_stop_refuses_changed_record_then_uses_held_handle() {
    let epoch = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    let marker = format!("SetEnv=BAT_FLEET_MONITOR={epoch}");
    let mut child = ChildGuard(
        Command::new(std::env::current_exe().unwrap())
            .args([
                "--ignored",
                "--exact",
                "windows::tests::owned_child_fixture",
                "--nocapture",
                "--skip",
                &marker,
            ])
            .env("BAT_FLEET_OWNED_FIXTURE", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let held = WindowsProcess::from_child(&child.0).unwrap();
    let actual = held.snapshot().unwrap();
    let record = TunnelRecord {
        process: actual.tunnel_evidence().unwrap(),
        created_filetime: Some(actual.created_filetime),
        origin: None,
    };
    let mut wrong = record.clone();
    wrong.process.arguments.push("not-this-process".into());
    assert_eq!(
        process_adapter::stop_tunnel(
            &WindowsAccess,
            &wrong,
            StopMode::Owned {
                current_epoch: epoch
            }
        ),
        Err("OWNER_UNPROVEN")
    );
    assert!(child.0.try_wait().unwrap().is_none());
    assert_eq!(
        process_adapter::stop_tunnel(
            &WindowsAccess,
            &record,
            StopMode::Owned {
                current_epoch: epoch
            }
        ),
        Ok(StopOutcome::Stopped)
    );
    assert!(!child.0.wait().unwrap().success());
}

// Bounded fixture helper; system executable and module search only, no user profile.
fn powershell(fixture: &Fixture, script: &str, env: &[(&str, &str)]) -> String {
    let mut command = powershell_fixture::command();
    command
        .args([
            "-Command",
            &format!("{}{script}", powershell_fixture::READY_SCRIPT),
        ])
        .envs(env.iter().copied());
    String::from_utf8(fixture.run(&mut command, Duration::from_secs(15))).unwrap()
}
