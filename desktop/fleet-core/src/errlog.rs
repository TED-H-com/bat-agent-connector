//! Best-effort local structured error spool; no transport or upload side effects.
use regex::Regex;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::Mutex,
};
static LOCK: Mutex<()> = Mutex::new(());
const LIMIT: u64 = 10 * 1024 * 1024;
fn patterns() -> &'static Value {
    static PATTERNS: std::sync::OnceLock<Value> = std::sync::OnceLock::new();
    PATTERNS.get_or_init(|| {
        serde_json::from_str(include_str!(
            "../../../src/bat_agent_connector/errlog_patterns.json"
        ))
        .expect("static patterns")
    })
}
pub fn redact(text: &str) -> String {
    let mut result = text.to_owned();
    static REDACTION: std::sync::OnceLock<Vec<Regex>> = std::sync::OnceLock::new();
    let rules = REDACTION.get_or_init(|| {
        patterns()["redaction"].as_array().unwrap().iter()
            .map(|p| Regex::new(p.as_str().unwrap()).expect("static redaction"))
            .collect()
    });
    for pattern in rules {
        result = pattern.replace_all(&result, "[REDACTED]").into_owned();
    }
    result
}
pub fn fingerprint(component: &str, code: &str, msg: &str) -> String {
    let mut msg = redact(msg);
    for pair in patterns()["normalization"].as_array().unwrap() {
        msg = Regex::new(pair[0].as_str().unwrap())
            .unwrap()
            .replace_all(&msg, pair[1].as_str().unwrap())
            .into_owned();
    }
    format!(
        "{:x}",
        Sha256::digest(format!("{component}|{code}|{msg}").as_bytes())
    )[..16]
        .into()
}
fn clean(value: &Value) -> Value {
    match value {
        Value::String(s) => json!(redact(s)),
        Value::Array(a) => Value::Array(a.iter().map(clean).collect()),
        Value::Object(o) => Value::Object(
            o.iter().map(|(k, v)| {
                let sensitive = Regex::new("(?i)token|password|passwd|secret|authorization|api.?key")
                    .unwrap().is_match(k);
                (redact(k), if sensitive { json!("[REDACTED]") } else { clean(v) })
            }).collect(),
        ),
        other => other.clone(),
    }
}
fn path() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("BAT_ERROR_LOG") {
        return Some(p.into());
    }
    #[cfg(windows)]
    let base = PathBuf::from(std::env::var_os("LOCALAPPDATA")?).join("BAT");
    #[cfg(not(windows))]
    let base = PathBuf::from(std::env::var_os("HOME")?).join(".bat");
    Some(base.join("logs/errors.jsonl"))
}
fn hostname() -> String {
    #[cfg(unix)]
    {
        let mut buffer = [0u8; 256];
        // gethostname writes at most the provided buffer length.
        if unsafe { libc::gethostname(buffer.as_mut_ptr().cast(), buffer.len()) } == 0 {
            let end = buffer.iter().position(|b| *b == 0).unwrap_or(buffer.len());
            return String::from_utf8_lossy(&buffer[..end]).into_owned();
        }
    }
    std::env::var("COMPUTERNAME").unwrap_or_else(|_| "unknown".into())
}
pub fn event(component: &str, code: &str, msg: &str, context: Value) {
    if let (Ok(_guard), Some(path)) = (LOCK.lock(), path()) {
        let Ok(_file_lock) = file_lock(&path) else {
            return;
        };
        let now = time::OffsetDateTime::now_utc();
        let record = json!({"ts": now.format(&time::format_description::well_known::Rfc3339).unwrap_or_default(),
            "machine_id": hostname(), "component": redact(component), "version": env!("CARGO_PKG_VERSION"),
            "level": "error", "code": redact(code), "msg": redact(msg),
            "fingerprint": fingerprint(component,code,msg), "count": 1, "context": clean(&context)});
        let _ = append(&path, record, now, LIMIT);
    }
}

fn file_lock(path: &Path) -> std::io::Result<std::fs::File> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let name = path.file_name()
        .ok_or(std::io::ErrorKind::InvalidInput)?
        .to_string_lossy();
    let mut options = OpenOptions::new();
    options.create(true).read(true).write(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path.with_file_name(format!(".{name}.lock")))?;
    file.lock()?;
    Ok(file)
}
fn replace(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let name = path.file_name()
        .ok_or(std::io::ErrorKind::InvalidInput)?
        .to_string_lossy();
    let temporary = path.with_file_name(format!(".{name}.{:032x}", rand::random::<u128>()));
    let result = (|| {
        let mut options = OpenOptions::new();
        options.create_new(true).write(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        options.open(&temporary)?.write_all(bytes)?;
        fs::rename(&temporary, path)
    })();
    let _ = fs::remove_file(&temporary);
    result
}

fn append(path: &Path, record: Value, now: time::OffsetDateTime, limit: u64) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    if let Ok(meta) = fs::symlink_metadata(path) {
        if !meta.is_file() || meta.file_type().is_symlink() {
            return Ok(());
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if meta.file_attributes() & 0x400 != 0 {
                return Ok(());
            }
        }
    }
    let mut lines: Vec<String> = if fs::metadata(path).map(|m| m.len() <= limit).unwrap_or(false) {
        fs::read_to_string(path)?.lines().map(str::to_owned).collect()
    } else {
        vec![]
    };
    for index in (0..lines.len()).rev() {
        let Ok(mut old) = serde_json::from_str::<Value>(&lines[index]) else {
            continue;
        };
        if let Some(ts) = old["ts"].as_str().and_then(|s| {
            time::OffsetDateTime::parse(s, &time::format_description::well_known::Rfc3339).ok()
        }) {
            let age = now - ts;
            if age >= time::Duration::ZERO
                && age < time::Duration::seconds(60)
                && old["fingerprint"] == record["fingerprint"]
            {
                old["count"] = json!(old["count"].as_u64().unwrap_or(1).saturating_add(1));
                lines[index] = serde_json::to_string(&old)?;
                replace(path, (lines.join("\n") + "\n").as_bytes())?;
                return Ok(());
            }
        }
    }
    let mut bytes = serde_json::to_vec(&record)?;
    bytes.push(b'\n');
    if fs::metadata(path)
        .map(|m| m.len() + bytes.len() as u64 > limit)
        .unwrap_or(false)
    {
        for i in (1..=4).rev() {
            let source = if i == 1 {
                path.to_path_buf()
            } else {
                PathBuf::from(format!("{}.{}", path.display(), i - 1))
            };
            let dest = PathBuf::from(format!("{}.{}", path.display(), i));
            if source.exists() {
                if dest.exists() {
                    fs::remove_file(&dest)?;
                }
                fs::rename(source, dest)?;
            }
        }
    }
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    options.open(path)?.write_all(&bytes)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_contract() {
        let fixture: Value = serde_json::from_str(include_str!("../../../tests/fixtures/errlog.json")).unwrap();
        for secret in fixture["secrets"].as_array().unwrap() {
            let s = secret.as_str().unwrap();
            assert_ne!(redact(s), s);
        }
        for case in fixture["redaction_cases"].as_array().unwrap() {
            assert_eq!(redact(case["msg"].as_str().unwrap()), case["expected"].as_str().unwrap());
        }
        for case in fixture["fingerprints"].as_array().unwrap() {
            assert_eq!(fingerprint(case["component"].as_str().unwrap(),case["code"].as_str().unwrap(),case["msg"].as_str().unwrap()),case["fingerprint"].as_str().unwrap());
        }
        assert_eq!(clean(&json!({"password":"tiny"}))["password"], "[REDACTED]");
    }
    #[test]
    fn collapse_rotate_and_io_errors() {
        let dir = std::env::temp_dir().join(format!("errlog-{:032x}", rand::random::<u128>()));
        fs::create_dir(&dir).unwrap();
        let path = dir.join("errors.jsonl");
        let now = time::OffsetDateTime::now_utc();
        let record = json!({"ts":now.format(&time::format_description::well_known::Rfc3339).unwrap(),"fingerprint":"example","count":1});
        append(&path, record.clone(), now, 1024).unwrap();
        append(&path, record.clone(), now, 1024).unwrap();
        let old: Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(old["count"], 2);
        append(&path, record, now + time::Duration::seconds(60), 1).unwrap();
        assert!(dir.join("errors.jsonl.1").exists());
        for i in 0..7 {
            append(&path, json!({"fingerprint": format!("case-{i}"), "count": 1}), now, 1).unwrap();
        }
        assert!(dir.join("errors.jsonl.4").exists());
        assert!(!dir.join("errors.jsonl.5").exists());
        assert!(append(&dir, json!({}), now, 1).is_ok());
        fs::remove_dir_all(dir).unwrap();
    }
}
