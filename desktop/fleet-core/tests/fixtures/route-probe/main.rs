//! Standalone synthetic executable double. Never links or invokes real Tailscale.
use std::{
    fs,
    io::{self, Write},
    time::Duration,
};
fn main() {
    let directory = std::env::current_exe()
        .unwrap()
        .parent()
        .unwrap()
        .to_owned();
    let args: Vec<_> = std::env::args().skip(1).collect();
    let allowed = std::env::vars_os().all(|(key, _)| {
        ["SystemRoot", "WINDIR"]
            .iter()
            .any(|name| key.to_string_lossy().eq_ignore_ascii_case(name))
    });
    if args.first().is_some_and(|arg| arg == "ping") {
        let valid = args.len() == 6 && args[1..4] == ["--c", "3", "--timeout"]
            && args[4] == "2s"
            && args[5] == "100.64.0.1" && allowed;
        fs::write(directory.join("ping-ok"), if valid { "yes" } else { "no" }).unwrap();
        fs::OpenOptions::new().create(true).append(true).open(directory.join("ping-count"))
            .unwrap().write_all(b"ping\n").unwrap();
        fs::write(directory.join("started"), "yes").unwrap();
        let mode = fs::read_to_string(directory.join("mode")).unwrap();
        if mode == "ping_hang" {
            std::thread::sleep(Duration::from_millis(800));
            fs::write(directory.join("finished"), "bad").unwrap();
        }
        fs::write(directory.join("awakened"), "yes").unwrap();
        return;
    }
    fs::OpenOptions::new().create(true).append(true).open(directory.join("status-count"))
        .unwrap().write_all(b"status\n").unwrap();
    fs::write(
        directory.join("argv-ok"),
        if args == ["status", "--json"] && allowed {
            "yes"
        } else {
            "no"
        },
    )
    .unwrap();
    fs::write(directory.join("started"), "yes").unwrap();
    let mode = fs::read_to_string(directory.join("mode")).unwrap();
    match mode.as_str() {
        "hang" => {
            std::thread::sleep(Duration::from_millis(800));
            fs::write(directory.join("finished"), "bad").unwrap();
        }
        "oversized" => {
            let mut out = io::stdout().lock();
            for _ in 0..300 {
                if out.write_all(&[b'x'; 8192]).is_err() {
                    return;
                }
            }
            return;
        }
        "invalid_utf8" => {
            let _ = io::stdout().write_all(&[255]);
            return;
        }
        "missing" => {
            println!("{{\"Peer\":{{}}}}");
            return;
        }
        "invalid_json" => {
            println!("fixture-private-malformed-text");
            return;
        }
        "exit_nonzero" => std::process::exit(3),
        _ => (),
    }
    if mode == "idle" && !directory.join("awakened").exists() {
        println!("{{\"Peer\":{{\"fixture\":{{\"TailscaleIPs\":[\"100.64.0.1\"],\"HostName\":\"old-name\",\"Online\":true,\"CurAddr\":\"\"}}}}}}");
        return;
    }
    println!("{{\"Peer\":{{\"fixture\":{{\"TailscaleIPs\":[\"100.64.0.1\"],\"Online\":true,\"CurAddr\":\"203.0.113.1:1234\"}}}}}}");
}
