use bat_fleet_core::{
    discovery::*,
    ownership::ProcessState,
    process_adapter::{legacy_created, LoginIdentity, ProcessSnapshot},
    Result,
};
use serde_json::{json, Value};
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    path::PathBuf,
};
const KIT: &str = r"C:\Fixture Kit";
const INV: &str = r"C:\Fixture Kit\fleet-inventory.json";
const INDEX: &str = r"C:\Fixture Kit\bat-profiles\index.json";
const SCRIPT: &str = r"C:\Fixture Kit\bat-connect.ps1";
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "bac-discovery-{}-{:032x}",
            std::process::id(),
            rand::random::<u128>()
        ));
        std::fs::create_dir(&p).unwrap();
        Self(p)
    }
    fn config(&self) -> DiscoveryConfig {
        DiscoveryConfig::new(KIT, INV, INDEX, &self.0, Some(native())).unwrap()
    }
    fn record(&self, old: bool, doc: &Value) -> PathBuf {
        let dir = self.0.join(if old {
            "org.tonyq.better-agent-terminal"
        } else {
            "BetterAgentTerminal"
        });
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("fleet-monitor.json");
        std::fs::write(&path, serde_json::to_vec(doc).unwrap()).unwrap();
        path
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn native() -> NativeIdentity {
    NativeIdentity::new(r"C:\Apps\Dashboard.exe", r"C:\Settings\fleet.json").unwrap()
}
fn login() -> LoginIdentity {
    LoginIdentity {
        owner_sid: "S-1-5-21-1-2-3-1001".into(),
        session_id: 7,
    }
}
fn ps() -> ProcessSnapshot {
    ProcessSnapshot {
        pid: 42,
        created_filetime: 133_456_789_012_345_678,
        executable: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe".into(),
        arguments: vec![
            "-NoProfile".into(),
            "-ExecutionPolicy".into(),
            "Bypass".into(),
            "-File".into(),
            SCRIPT.into(),
            "-InventoryPath".into(),
            INV.into(),
            "-ProfileIndexPath".into(),
            INDEX.into(),
        ],
        login: login(),
    }
}
fn rust() -> ProcessSnapshot {
    let n = native();
    ProcessSnapshot {
        executable: n.executable().into(),
        arguments: n.arguments().to_vec(),
        ..ps()
    }
}
fn doc(p: &ProcessSnapshot, native: bool) -> Value {
    let mut value = json!({"pid":p.pid,"created":legacy_created(p.created_filetime).unwrap(),"executable":p.executable,"owner_sid":p.login.owner_sid,"session_id":p.login.session_id,"instance":"a".repeat(32),"arguments":p.arguments,"inventory_path":INV,"profile_index_path":INDEX});
    if native {
        value["backend"] = "rust".into();
        value["created_filetime"] = p.created_filetime.to_string().into();
    } else {
        value["script"] = SCRIPT.into();
    }
    value
}
struct Fake {
    processes: BTreeMap<u32, ProcessSnapshot>,
    states: BTreeMap<u32, ProcessState>,
    candidates: Vec<ProcessSnapshot>,
    enumeration_error: bool,
    hook: RefCell<Option<Box<dyn FnOnce()>>>,
    reads: Cell<usize>,
    mutate_final: bool,
}
impl Fake {
    fn new(p: ProcessSnapshot) -> Self {
        Self {
            processes: [(p.pid, p)].into(),
            states: BTreeMap::new(),
            candidates: vec![],
            enumeration_error: false,
            hook: RefCell::new(None),
            reads: Cell::new(0),
            mutate_final: false,
        }
    }
}
impl Observation for Fake {
    fn current_login(&self) -> Result<LoginIdentity> {
        Ok(login())
    }
    fn state(&self, pid: u32) -> ProcessState {
        self.states.get(&pid).cloned().unwrap_or_else(|| {
            self.processes
                .get(&pid)
                .map_or(ProcessState::Dead, |p| ProcessState::Live {
                    created: legacy_created(p.created_filetime).unwrap(),
                })
        })
    }
    fn observe(&self, pid: u32) -> Result<Option<ProcessSnapshot>> {
        self.reads.set(self.reads.get() + 1);
        let mut p = self.processes.get(&pid).cloned();
        if self.mutate_final && self.reads.get() > 1 {
            if let Some(p) = &mut p {
                p.arguments.push("changed".into());
            }
        }
        Ok(p)
    }
    fn legacy_candidates(&self) -> Result<Vec<ProcessSnapshot>> {
        if let Some(hook) = self.hook.borrow_mut().take() {
            hook();
        }
        if self.enumeration_error {
            Err("OWNER_UNPROVEN")
        } else {
            Ok(self.candidates.clone())
        }
    }
}
fn refusal<T>(result: Result<T>, code: &str) {
    assert!(matches!(result,Err(actual) if actual==code));
}
#[test]
fn absent_records_and_complete_empty_scan_are_readonly() {
    let fixture = Fixture::new();
    let fake = Fake::new(ps());
    assert!(discover(&fixture.config(), &fake).unwrap().is_none());
    assert_eq!(std::fs::read_dir(&fixture.0).unwrap().count(), 0);
}
#[test]
fn exact_record_preserves_identity_and_both_directory_bytes() {
    let fixture = Fixture::new();
    let process = ps();
    let path = fixture.record(false, &doc(&process, false));
    let before = std::fs::read(&path).unwrap();
    let owner = discover(&fixture.config(), &Fake::new(process.clone()))
        .unwrap()
        .unwrap();
    assert_eq!(owner.process.pid, 42);
    assert_eq!(owner.ownership, Ownership::CurrentLogin);
    assert_eq!(owner.backend, Backend::Powershell);
    assert!(!owner.legacy);
    assert_eq!(owner.instance, Some("a".repeat(32)));
    assert_eq!(std::fs::read(path).unwrap(), before);
}
#[test]
fn old_directory_owner_in_another_login_is_visible_readonly() {
    let fixture = Fixture::new();
    let mut p = ps();
    p.login.session_id = 9;
    fixture.record(true, &doc(&p, false));
    let owner = discover(&fixture.config(), &Fake::new(p)).unwrap().unwrap();
    assert_eq!(owner.ownership, Ownership::OtherLogin);
    assert!(owner.directories[0].ends_with("org.tonyq.better-agent-terminal"));
}
#[test]
fn every_record_identity_dimension_is_required() {
    for field in [
        "pid",
        "created",
        "executable",
        "owner_sid",
        "session_id",
        "arguments",
        "instance",
        "script",
    ] {
        let fixture = Fixture::new();
        let p = ps();
        let mut record = doc(&p, false);
        match field {
            "pid" => record[field] = 0.into(),
            "created" => record[field] = "1".into(),
            "executable" => record[field] = r"C:\wrong\powershell.exe".into(),
            "owner_sid" => record[field] = "S-1-5-21-1-2-3-1002".into(),
            "session_id" => record[field] = 8.into(),
            "arguments" => record[field] = json!(["-File", SCRIPT]),
            "instance" => record[field] = "".into(),
            _ => record[field] = r"C:\other\bat-connect.ps1".into(),
        };
        fixture.record(false, &record);
        refusal(discover(&fixture.config(), &Fake::new(p)), "OWNER_UNPROVEN");
    }
}
#[test]
fn malformed_old_record_blocks_even_after_valid_new_owner() {
    for malformed in [b"{broken".as_slice(), b"[]", br#"{"pid":42,"PID":43}"#] {
        let fixture = Fixture::new();
        fixture.record(false, &doc(&ps(), false));
        let old = fixture.record(true, &doc(&ps(), false));
        std::fs::write(old, malformed).unwrap();
        assert!(discover(&fixture.config(), &Fake::new(ps())).is_err());
    }
}
#[test]
fn unknown_incarnation_is_skipped_but_incomplete_enumeration_is_retried() {
    let fixture = Fixture::new();
    fixture.record(false, &doc(&ps(), false));
    let mut fake = Fake::new(ps());
    fake.states.insert(42, ProcessState::Unknown);
    assert!(discover(&fixture.config(), &fake).unwrap().is_none());
    fake.states.clear();
    fake.enumeration_error = true;
    refusal(discover(&fixture.config(), &fake), "OWNER_UNPROVEN");
}
#[test]
fn dead_and_reused_recorded_incarnations_do_not_own_new_processes() {
    for raw_delta in [0, 1, 10] {
        let fixture = Fixture::new();
        let p = rust();
        fixture.record(false, &doc(&p, true));
        let mut fake = Fake::new(p.clone());
        if raw_delta == 0 {
            fake.processes.clear();
        } else {
            fake.processes.get_mut(&42).unwrap().created_filetime += raw_delta;
        }
        assert!(discover(&fixture.config(), &fake).unwrap().is_none());
    }
}
#[test]
fn duplicate_matching_records_are_one_owner_but_conflicts_refuse() {
    let fixture = Fixture::new();
    fixture.record(false, &doc(&ps(), false));
    fixture.record(true, &doc(&ps(), false));
    let mut fake = Fake::new(ps());
    let owner = discover(&fixture.config(), &fake).unwrap().unwrap();
    assert_eq!(owner.directories.len(), 2);
    let mut other = ps();
    other.pid = 43;
    fixture.record(true, &doc(&other, false));
    fake.processes.insert(43, other);
    refusal(discover(&fixture.config(), &fake), "MONITOR_CONFLICT");
    let mut epoch = doc(&ps(), false);
    epoch["instance"] = "b".repeat(32).into();
    fixture.record(true, &epoch);
    refusal(discover(&fixture.config(), &fake), "MONITOR_CONFLICT");
}
#[test]
fn record_change_or_final_process_change_invalidates_discovery() {
    for bytes_changed in [false, true] {
        let fixture = Fixture::new();
        let path = fixture.record(false, &doc(&ps(), false));
        let mut fake = Fake::new(ps());
        if bytes_changed {
            *fake.hook.borrow_mut() =
                Some(Box::new(move || std::fs::write(path, b"changed").unwrap()));
        } else {
            fake.mutate_final = true;
        }
        refusal(discover(&fixture.config(), &fake), "OWNER_UNPROVEN");
    }
}
#[test]
fn unrecorded_exact_legacy_has_no_invented_epoch() {
    let fixture = Fixture::new();
    let mut fake = Fake::new(ps());
    fake.candidates.push(ps());
    let owner = discover(&fixture.config(), &fake).unwrap().unwrap();
    assert!(owner.legacy);
    assert!(owner.instance.is_none());
    assert!(owner.record.is_none());
    assert!(owner.directories.is_empty());
}
#[test]
fn unrecorded_other_script_or_account_is_excluded_but_another_monitor_conflicts() {
    let fixture = Fixture::new();
    let mut ordinary = ps();
    ordinary.arguments[4] = r"C:\Other\script.ps1".into();
    let mut fake = Fake::new(ordinary.clone());
    fake.candidates.push(ordinary);
    assert!(discover(&fixture.config(), &fake).unwrap().is_none());
    let mut other = ps();
    other.login.owner_sid = "S-1-5-21-1-2-3-1002".into();
    fake.candidates = vec![other];
    assert!(discover(&fixture.config(), &fake).unwrap().is_none());
    fixture.record(false, &doc(&ps(), false));
    fake.processes.insert(42, ps());
    let mut second = ps();
    second.pid = 43;
    fake.candidates = vec![second];
    refusal(discover(&fixture.config(), &fake), "MONITOR_CONFLICT");
}
#[test]
fn ps_named_colon_and_positional_forms_resolve_exact_pair() {
    for tail in [
        vec![
            format!("-InventoryPath:{INV}"),
            format!("-ProfileIndexPath:{INDEX}"),
        ],
        vec![INV.into(), INDEX.into()],
        vec![
            "-ProfileIndexPath".into(),
            INDEX.into(),
            "-InventoryPath".into(),
            INV.into(),
        ],
    ] {
        let fixture = Fixture::new();
        let mut p = ps();
        p.arguments.truncate(5);
        p.arguments.extend(tail);
        fixture.record(false, &doc(&p, false));
        assert!(discover(&fixture.config(), &Fake::new(p))
            .unwrap()
            .is_some());
    }
}
#[test]
fn relative_script_and_configs_need_exact_recorded_argv_and_absolute_saved_paths() {
    for saved in [false, true] {
        let fixture = Fixture::new();
        let mut p = ps();
        p.arguments[4] = "bat-connect.ps1".into();
        p.arguments[6] = "fleet-inventory.json".into();
        p.arguments[8] = "bat-profiles/index.json".into();
        let mut record = doc(&p, false);
        if !saved {
            record.as_object_mut().unwrap().remove("arguments");
        }
        fixture.record(false, &record);
        let result = discover(&fixture.config(), &Fake::new(p));
        if saved {
            assert!(result.unwrap().is_some());
        } else {
            refusal(result, "OWNER_UNPROVEN");
        }
    }
}
#[test]
fn legacy_missing_argv_supports_only_observed_absolute_source() {
    let fixture = Fixture::new();
    let mut record = doc(&ps(), false);
    for key in ["arguments", "profile_index_path"] {
        record.as_object_mut().unwrap().remove(key);
    }
    fixture.record(false, &record);
    assert!(discover(&fixture.config(), &Fake::new(ps()))
        .unwrap()
        .is_some());
}
#[test]
fn alternate_pair_requires_explicit_index_proof_and_matches_stored_paths() {
    let fixture = Fixture::new();
    let inv = r"C:\Config\inventory.json";
    let index = r"C:\Config\profiles.json";
    let config = DiscoveryConfig::new(KIT, inv, index, &fixture.0, None).unwrap();
    let mut p = ps();
    p.arguments[6] = inv.into();
    p.arguments[8] = index.into();
    let mut record = doc(&p, false);
    record["inventory_path"] = inv.into();
    record["profile_index_path"] = index.into();
    fixture.record(false, &record);
    assert!(discover(&config, &Fake::new(p.clone())).unwrap().is_some());
    record.as_object_mut().unwrap().remove("profile_index_path");
    fixture.record(false, &record);
    refusal(discover(&config, &Fake::new(p)), "OWNER_UNPROVEN");
}
#[test]
fn unexpected_switches_duplicates_or_wrong_pair_never_match() {
    for tail in [
        vec!["-Unknown".into(), "x".into()],
        vec![
            "-InventoryPath".into(),
            INV.into(),
            "-InventoryPath".into(),
            INV.into(),
        ],
        vec![
            "-InventoryPath".into(),
            r"C:\Other\inventory.json".into(),
            "-ProfileIndexPath".into(),
            INDEX.into(),
        ],
    ] {
        let fixture = Fixture::new();
        let mut p = ps();
        p.arguments.truncate(5);
        p.arguments.extend(tail);
        fixture.record(false, &doc(&p, false));
        assert!(discover(&fixture.config(), &Fake::new(p)).is_err());
    }
}
#[test]
fn command_text_or_relative_unrecorded_source_never_proves_legacy_monitor() {
    for args in [
        vec![
            "-Command".into(),
            "echo".into(),
            "-File".into(),
            SCRIPT.into(),
        ],
        vec!["-File".into(), "bat-connect.ps1".into()],
    ] {
        let fixture = Fixture::new();
        let mut p = ps();
        p.arguments = args;
        let mut fake = Fake::new(p.clone());
        fake.candidates.push(p);
        refusal(discover(&fixture.config(), &fake), "OWNER_UNPROVEN");
    }
}
#[test]
fn exact_native_record_has_no_script_and_binds_fixed_fleet_config_argv() {
    let fixture = Fixture::new();
    let p = rust();
    fixture.record(false, &doc(&p, true));
    let owner = discover(&fixture.config(), &Fake::new(p.clone()))
        .unwrap()
        .unwrap();
    assert_eq!(owner.backend, Backend::Rust);
    for delta in ["path", "args", "script", "birth"] {
        let mut wrong = doc(&p, true);
        match delta {
            "path" => wrong["inventory_path"] = r"C:\Other\inventory.json".into(),
            "args" => {
                wrong["arguments"] = json!([
                    "--fleet-supervisor",
                    "--fleet-config",
                    r"C:\Other\fleet.json"
                ])
            }
            "script" => wrong["script"] = SCRIPT.into(),
            _ => {
                wrong.as_object_mut().unwrap().remove("created_filetime");
            }
        }
        fixture.record(false, &wrong);
        refusal(
            discover(&fixture.config(), &Fake::new(p.clone())),
            "OWNER_UNPROVEN",
        );
    }
}
#[test]
fn win32_paths_refuse_ambiguous_names_and_do_not_infer_cwd() {
    assert_eq!(
        windows_path(r"C:/Kit/one/../bat-connect.ps1").unwrap(),
        r"C:\Kit\bat-connect.ps1"
    );
    assert_eq!(
        windows_path(r"\\server\share\one\..\kit").unwrap(),
        r"\\server\share\kit"
    );
    for p in [
        "relative",
        r"C:relative",
        r"\rooted",
        r"\\?\C:\Kit",
        r"\\.\pipe\anything",
        r"C:\path:stream",
        r"C:\trailing.\x",
        r"C:\..\outside",
        r"\\server\share\..\outside",
    ] {
        assert!(windows_path(p).is_err(), "{p}");
    }
}
#[test]
fn nonregular_and_oversized_records_are_not_missing() {
    let fixture = Fixture::new();
    let p = fixture.record(false, &doc(&ps(), false));
    std::fs::write(&p, vec![b' '; 262145]).unwrap();
    refusal(
        discover(&fixture.config(), &Fake::new(ps())),
        "OWNER_UNPROVEN",
    );
    std::fs::remove_file(&p).unwrap();
    std::fs::create_dir(p).unwrap();
    refusal(
        discover(&fixture.config(), &Fake::new(ps())),
        "OWNER_UNPROVEN",
    );
}

#[test]
fn present_null_identity_fields_cannot_be_downgraded_to_legacy_absence() {
    for field in [
        "backend",
        "script",
        "arguments",
        "inventory_path",
        "profile_index_path",
        "created_filetime",
    ] {
        let fixture = Fixture::new();
        let mut record = doc(&ps(), false);
        record[field] = Value::Null;
        fixture.record(false, &record);
        refusal(
            discover(&fixture.config(), &Fake::new(ps())),
            "OWNER_UNPROVEN",
        );
    }
}

#[test]
fn consistent_but_different_inventory_pair_returns_explicit_mismatch() {
    let fixture = Fixture::new();
    let mut process = ps();
    process.arguments[6] = r"C:\Other\inventory.json".into();
    let mut record = doc(&process, false);
    record["inventory_path"] = process.arguments[6].clone().into();
    fixture.record(false, &record);
    refusal(
        discover(&fixture.config(), &Fake::new(process)),
        "MONITOR_INVENTORY_MISMATCH",
    );
}

#[test]
fn access_denied_candidate_does_not_hide_a_proven_monitor() {
    assert!(candidate(Err("OWNER_UNPROVEN")).is_none());
    let fixture = Fixture::new();
    let process = rust();
    fixture.record(false, &doc(&process, true));
    let mut fake = Fake::new(process);
    if let Some(process) = candidate(Err("OWNER_UNPROVEN")) {
        fake.candidates.push(process);
    }
    assert!(discover(&fixture.config(), &fake).unwrap().is_some());
}
