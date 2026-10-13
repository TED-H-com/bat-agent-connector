use super::*;
use std::path::PathBuf;

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "bat-powershell-fixture-{:032x}",
            rand::random::<u128>()
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn command(&self, code: &str) -> Command {
        let mut command = Command::new("python3");
        command.args([
            "-c",
            &format!(
                "import os,sys,time\nwith open(sys.argv[1],'a') as f: f.write(str(os.getpid())+'\\n')\n{code}"
            ),
        ]);
        command.arg(self.0.join("launches"));
        command
    }
    fn assert_reaped(&self) {
        let launches = std::fs::read_to_string(self.0.join("launches")).unwrap();
        let pids: Vec<_> = launches.lines().collect();
        assert_eq!(pids.len(), 1, "fixture must never retry a launch");
        let pid = pids[0].parse::<i32>().unwrap();
        let mut status = 0;
        assert_eq!(
            unsafe { libc::waitpid(pid, &mut status, libc::WNOHANG) },
            -1
        );
        assert_eq!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::ECHILD)
        );
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1, "child still running");
        assert_eq!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::ESRCH)
        );
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn ready() -> String {
    format!(
        "sys.stdout.write({:?});sys.stdout.flush()\n",
        std::str::from_utf8(READY).unwrap()
    )
}

#[test]
fn cold_start_has_its_own_budget_and_marker_is_removed_from_output() {
    let fixture = Fixture::new();
    let temp = Temp::new();
    let mut command = temp.command(&format!(
        "time.sleep(0.7)\n{}print('original-output')",
        ready()
    ));
    let began = Instant::now();
    assert_eq!(
        fixture.run_with_deadlines(
            &mut command,
            Duration::from_secs(5),
            Duration::from_millis(300)
        ),
        Ok(b"original-output\n".to_vec())
    );
    assert!(began.elapsed() >= Duration::from_millis(700));
    temp.assert_reaped();
}

#[test]
fn startup_and_work_timeouts_are_distinct_and_kill_only_the_one_owned_child() {
    let fixture = Fixture::new();
    let mut unrelated = Owned {
        child: Command::new("python3")
            .args(["-c", "import time;time.sleep(30)"])
            .spawn()
            .unwrap(),
        reader: None,
    };
    for started in [false, true] {
        let temp = Temp::new();
        let mut command = temp.command(&format!(
            "{}time.sleep(30)",
            if started { ready() } else { String::new() }
        ));
        let began = Instant::now();
        let error = fixture
            .run_with_deadlines(
                &mut command,
                if started {
                    Duration::from_secs(5)
                } else {
                    Duration::from_millis(300)
                },
                if started {
                    Duration::from_millis(300)
                } else {
                    Duration::from_secs(5)
                },
            )
            .unwrap_err();
        assert_eq!(
            error,
            if started {
                "PowerShell fixture exceeded its work deadline"
            } else {
                "PowerShell fixture did not start within 60 s"
            }
        );
        assert!(
            began.elapsed() < Duration::from_secs(3),
            "one phase borrowed the other's budget"
        );
        temp.assert_reaped();
        assert!(unrelated.child.try_wait().unwrap().is_none());
    }
}

#[test]
fn invalid_missing_or_partial_markers_nonzero_exit_and_output_overflow_fail_and_reap() {
    let fixture = Fixture::new();
    for (code, expected) in [
        (
            "print('wrong marker')".to_string(),
            "PowerShell fixture readiness marker missing or invalid",
        ),
        (
            "pass".to_string(),
            "PowerShell fixture readiness marker missing or invalid",
        ),
        (
            "print('BATC_')".to_string(),
            "PowerShell fixture readiness marker missing or invalid",
        ),
        (
            format!("{}sys.exit(3)", ready()),
            "PowerShell fixture failed",
        ),
        (
            format!(
                "{}sys.stdout.write('x'*32769);sys.stdout.flush();time.sleep(30)",
                ready()
            ),
            "PowerShell fixture output exceeded its bound",
        ),
    ] {
        let temp = Temp::new();
        assert_eq!(
            fixture.run_with_deadlines(
                &mut temp.command(&code),
                Duration::from_secs(5),
                Duration::from_secs(5)
            ),
            Err(expected)
        );
        temp.assert_reaped();
    }
    let temp = Temp::new();
    assert_eq!(
        fixture.run_with_deadlines(
            &mut Command::new(temp.0.join("missing")),
            Duration::from_secs(5),
            Duration::from_secs(5)
        ),
        Err("PowerShell fixture spawn failed")
    );
}

#[test]
fn closed_stdout_does_not_make_a_live_child_successful() {
    let fixture = Fixture::new();
    let temp = Temp::new();
    let code = format!("{}os.close(1);time.sleep(30)", ready());
    assert_eq!(
        fixture.run_with_deadlines(
            &mut temp.command(&code),
            Duration::from_secs(5),
            Duration::from_millis(300)
        ),
        Err("PowerShell fixture exceeded its work deadline")
    );
    temp.assert_reaped();
}

#[test]
fn fixture_mutex_excludes_other_threads_before_spawn() {
    let fixture = Fixture::new();
    let (attempt_tx, attempt_rx) = mpsc::channel();
    let (acquired_tx, acquired_rx) = mpsc::channel();
    let worker = thread::spawn(move || {
        attempt_tx.send(()).unwrap();
        let _fixture = Fixture::new();
        acquired_tx.send(()).unwrap();
    });
    attempt_rx.recv().unwrap();
    assert_eq!(
        acquired_rx.recv_timeout(Duration::from_millis(100)),
        Err(mpsc::RecvTimeoutError::Timeout)
    );
    drop(fixture);
    acquired_rx.recv_timeout(Duration::from_secs(5)).unwrap();
    worker.join().unwrap();
}
