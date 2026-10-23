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

fn panic_message(run: impl FnOnce()) -> String {
    let error = std::panic::catch_unwind(std::panic::AssertUnwindSafe(run))
        .expect_err("the original deadline must still fail");
    error
        .downcast_ref::<String>()
        .cloned()
        .or_else(|| error.downcast_ref::<&str>().map(|s| s.to_string()))
        .expect("fixture panic must contain the diagnostic message")
}

#[test]
fn deadline_diagnostics_distinguish_running_child_and_closed_stdout() {
    let fixture = Fixture::new();
    for closed in [false, true] {
        let temp = Temp::new();
        let code = format!(
            "{}{}time.sleep(30)",
            ready(),
            if closed { "os.close(1)\n" } else { "" }
        );
        let report = panic_message(|| {
            fixture.run(&mut temp.command(&code), Duration::from_millis(300));
        });
        temp.assert_reaped();
        assert!(report.contains("PowerShell fixture exceeded its work deadline"));
        assert!(report.contains("READY=Some("), "{report}");
        assert!(
            report.contains(if closed {
                "stdout_EOF=Some("
            } else {
                "stdout_EOF=None"
            }),
            "{report}"
        );
        assert!(report.contains("process_exit=None"), "{report}");
        assert!(
            report.contains("child_cpu=unavailable (non-Windows)"),
            "{report}"
        );
    }
}

#[test]
fn deadline_diagnostics_report_exit_without_stdout_eof_and_stop_the_reader() {
    let fixture = Fixture::new();
    let temp = Temp::new();
    // Transfer a duplicate stdout writer to another owned synthetic process.
    // This models an inherited pipe without leaving an orphaned grandchild.
    let mut holder = Owned {
        child: Command::new("python3")
            .args(["-c", "import array,os,select,socket,sys\ns=socket.socket(socket.AF_UNIX)\ns.bind('s');s.listen(1);s.settimeout(5)\nopen('holder-ready','w').close()\nc,_=s.accept()\n_,ancillary,_,_=c.recvmsg(1,socket.CMSG_SPACE(array.array('i').itemsize))\nfds=array.array('i');fds.frombytes(ancillary[0][2][:fds.itemsize])\nc.sendall(b'k')\nselect.select([sys.stdin],[],[],10)\nos.close(fds[0])"])
            .current_dir(&temp.0)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap(),
        reader: None,
    };
    let began = Instant::now();
    while !temp.0.join("holder-ready").exists() {
        assert!(began.elapsed() < Duration::from_secs(5));
        thread::sleep(Duration::from_millis(10));
    }
    let mut command = temp.command(&format!(
        "{}import socket,array\ns=socket.socket(socket.AF_UNIX);s.connect('s')\ns.sendmsg([b'x'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[1]))])\nassert s.recv(1)==b'k'\ns.close()",
        ready()
    ));
    command.current_dir(&temp.0);
    let report = panic_message(|| {
        fixture.run(&mut command, Duration::from_millis(300));
    });
    let holder_still_live = holder.child.try_wait().unwrap().is_none();
    drop(holder); // Always kill/reap the writer holder before assertions.
    temp.assert_reaped();
    assert!(report.contains("READY=Some("), "{report}");
    assert!(report.contains("process_exit=Some("), "{report}");
    assert!(report.contains("stdout_EOF=None"), "{report}");
    assert!(
        holder_still_live,
        "runner waited for the unrelated pipe holder"
    );
}

#[test]
fn startup_breach_is_captured_before_cleanup_and_cleared_before_the_next_run() {
    let fixture = Fixture::new();
    let temp = Temp::new();
    let error = fixture
        .run_with_deadlines(
            &mut temp.command("time.sleep(30)"),
            Duration::from_millis(300),
            Duration::from_secs(5),
        )
        .unwrap_err();
    let report = fixture.failure_message(error);
    assert!(report.contains("PowerShell fixture did not start within 60 s"));
    for point in ["READY=None", "stdout_EOF=None", "process_exit=None"] {
        assert!(report.contains(point), "{report}");
    }
    temp.assert_reaped();
    let next = Temp::new();
    let error = fixture
        .run_with_deadlines(
            &mut Command::new(next.0.join("missing")),
            Duration::from_secs(5),
            Duration::from_secs(5),
        )
        .unwrap_err();
    assert_eq!(
        fixture.failure_message(error),
        "PowerShell fixture spawn failed"
    );
}

#[test]
fn lifecycle_observation_preserves_binary_output_exactly() {
    let fixture = Fixture::new();
    let temp = Temp::new();
    let expected: Vec<u8> = (0..=255).cycle().take(768).collect();
    let code = format!(
        "{}sys.stdout.buffer.write(bytes(range(256))*3);sys.stdout.buffer.flush()",
        ready()
    );
    assert_eq!(
        fixture.run(&mut temp.command(&code), Duration::from_secs(5)),
        expected
    );
    temp.assert_reaped();
}

#[test]
fn stderr_phases_arrive_without_changing_ready_or_stdout() {
    let fixture = Fixture::new();
    let temp = Temp::new();
    let code = format!(
        "{}sys.stderr.write('BATC_PHASE hash-before\\n');sys.stderr.flush()\ntime.sleep(0.05)\nsys.stderr.write('BATC_PHASE hash-after\\n');sys.stderr.flush()\nprint('unchanged')",
        ready()
    );
    assert_eq!(
        fixture.run(&mut temp.command(&code), Duration::from_secs(5)),
        b"unchanged\n"
    );
    temp.assert_reaped();
    let report = fixture.failure_message("successful fixture");
    assert!(report.contains("hash-before@"), "{report}");
    assert!(report.contains("hash-after@"), "{report}");
    assert!(report.find("hash-before@").unwrap() < report.find("hash-after@").unwrap());
}

#[test]
fn stderr_flood_is_drained_with_bounded_tail_and_retained_markers() {
    let fixture = Fixture::new();
    let temp = Temp::new();
    let code = format!(
        "{}sys.stderr.write('BATC_PHASE before-flood\\n');sys.stderr.flush()\nsys.stderr.write('x'*1048576+'\\nerror-tail\\nBATC_PHASE script-end\\n');sys.stderr.flush()\nprint('finished')",
        ready()
    );
    assert_eq!(
        fixture.run(&mut temp.command(&code), Duration::from_secs(5)),
        b"finished\n"
    );
    temp.assert_reaped();
    let report = fixture.failure_message("successful fixture");
    assert!(report.contains("before-flood@"), "{report}");
    assert!(report.contains("script-end@"), "{report}");
    assert!(report.contains("error-tail"), "{report}");
    assert!(report.contains("stderr_dropped="), "{report}");
    assert!(
        report.len() < 4096,
        "stderr retention must be bounded: {}",
        report.len()
    );
}

#[test]
fn stderr_last_phase_identifies_shutdown_stall_before_cleanup() {
    let fixture = Fixture::new();
    let temp = Temp::new();
    let code = format!(
        "{}sys.stderr.write('BATC_PHASE output-after\\nBATC_PHASE script-end\\n');sys.stderr.flush()\ntime.sleep(30)", ready()
    );
    let report = panic_message(|| {
        fixture.run(&mut temp.command(&code), Duration::from_millis(300));
    });
    temp.assert_reaped();
    assert!(report.contains("script-end@"), "{report}");
    assert!(
        report.contains("stall=shutdown (script-end reached, process exit not observed)"),
        "{report}"
    );
    assert!(report.contains("process_exit=None"), "{report}");
}
