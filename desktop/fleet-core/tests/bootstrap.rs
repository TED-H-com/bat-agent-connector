mod support;
use bat_fleet_core::{
    bootstrap::{
        Action, Exchange, Phase, Platform, Recipe, Request, ServiceState, Store, RECEIPT_FILE,
        RECIPE_FILE,
    },
    configuration::Configuration,
    ownership::ProbeGeneration,
    Result,
};
use serde_json::{json, Value};
use std::{collections::VecDeque, path::PathBuf, sync::Arc, time::Duration};
use support::Fixture;
use tokio::time::Instant;

fn setup() -> (Fixture, Configuration, Recipe, ProbeGeneration) {
    let f = Fixture::new();
    let path = f.0.join("kit/fleet-inventory.json");
    let mut doc: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    doc["connector"]["ssh_alias"] = json!("fixture-connector");
    std::fs::write(&path, serde_json::to_vec(&doc).unwrap()).unwrap();
    let ssh = f.0.join("kit/ssh-config");
    let mut text = std::fs::read_to_string(&ssh).unwrap();
    text.push_str("\nHost fixture-connector\n HostName example.invalid\n");
    std::fs::write(ssh, text).unwrap();
    std::fs::write(f.0.join("kit").join(RECIPE_FILE),serde_json::to_vec(&json!({"schema_version":1,"allow_ensure":true,
        "ssh_alias":"fixture-connector","service_id":"synthetic-connector","state_directory":"/synthetic/state",
        "journal_path":"/synthetic/state/tasks.sqlite3","owner_uid":1000,"server_recipe_sha256":"a".repeat(64)})).unwrap()).unwrap();
    let config = f.load();
    let recipe = Recipe::load(&f.0.join("kit"), &config).unwrap().unwrap();
    let generation = ProbeGeneration {
        epoch: "b".repeat(32),
        configuration_binding: config.binding().into(),
        selection_revision: "c".repeat(64),
        generation: 1,
    };
    (f, config, recipe, generation)
}
#[derive(Clone, Copy)]
enum Reply {
    Stopped,
    Running,
    Transitioning,
    Owner,
    Lost,
    WrongId,
    WrongGeneration,
    WrongAttempt,
    WrongRecipe,
    WrongAction,
    Crash,
}
struct Mock<'a> {
    config: &'a Configuration,
    path: PathBuf,
    replies: VecDeque<Reply>,
    calls: Vec<Action>,
    selected: bool,
    entered: Option<Arc<tokio::sync::Notify>>,
}
impl<'a> Mock<'a> {
    fn new(f: &Fixture, c: &'a Configuration, replies: impl IntoIterator<Item = Reply>) -> Self {
        Self {
            config: c,
            path: f.0.join(RECEIPT_FILE),
            replies: replies.into_iter().collect(),
            calls: vec![],
            selected: true,
            entered: None,
        }
    }
}
impl Platform for Mock<'_> {
    fn verify(&self, recipe: &Recipe, generation: &ProbeGeneration) -> Result<()> {
        recipe.verify_current(self.config)?;
        if !self.selected || generation.configuration_binding != self.config.binding() {
            return Err("CONNECTION_NOT_SELECTED");
        }
        Ok(())
    }
    fn exchange<'a>(&'a mut self, _: &'a Recipe, request: &'a Request, _: Instant) -> Exchange<'a> {
        Box::pin(async move {
            let journal: Value =
                serde_json::from_slice(&std::fs::read(&self.path).unwrap()).unwrap();
            assert!(journal["records"][0]["queries"].as_u64().unwrap() > 0);
            if request.action() == Action::Ensure {
                assert_eq!(journal["records"][0]["ensure_requested"], true);
            }
            self.calls.push(request.action());
            let reply = self.replies.pop_front().unwrap_or(Reply::Lost);
            if matches!(reply, Reply::Lost) {
                return Err("BOOTSTRAP_TRANSPORT_UNKNOWN");
            }
            if matches!(reply, Reply::Crash) {
                if let Some(entered) = &self.entered {
                    entered.notify_one();
                }
                std::future::pending::<()>().await;
            }
            let mut echo: Value = serde_json::from_slice(&request.bytes()?).unwrap();
            let (state, owner) = match reply {
                Reply::Stopped => ("stopped", "absent"),
                Reply::Running => ("running", "service"),
                Reply::Owner => ("owner_present", "unknown"),
                _ => ("transitioning", "unknown"),
            };
            echo["state"] = json!(state);
            echo["owner"] = json!(owner);
            echo["ensure_accepted"] =
                json!(request.action() == Action::Ensure && state == "transitioning");
            match reply {
                Reply::WrongId => echo["request_id"] = json!("f".repeat(32)),
                Reply::WrongGeneration => echo["generation"] = json!("f".repeat(64)),
                Reply::WrongAttempt => echo["attempt"] = json!(0),
                Reply::WrongRecipe => echo["recipe_sha256"] = json!("f".repeat(64)),
                Reply::WrongAction => echo["action"] = json!("ensure"),
                _ => (),
            }
            Ok(serde_json::to_vec(&echo).unwrap())
        })
    }
}
const ID: &str = "11111111111111111111111111111111";
#[tokio::test]
async fn fixed_query_ensure_query_saves_receipts_before_io_and_never_claims_api_ready() {
    let (f, c, r, g) = setup();
    let store = Store::new(&f.0).unwrap();
    let mut m = Mock::new(
        &f,
        &c,
        [Reply::Stopped, Reply::Transitioning, Reply::Running],
    );
    let status = store.advance(&r, &g, ID, &mut m).await.unwrap();
    assert_eq!(status.phase, Phase::ServiceRunning);
    assert!(status.ensure_accepted);
    assert_eq!(m.calls.len(), 3);
    let stored: Value =
        serde_json::from_slice(&std::fs::read(f.0.join(RECEIPT_FILE)).unwrap()).unwrap();
    let exchanges = stored["records"][0]["exchanges"].as_array().unwrap();
    assert_eq!(exchanges.len(), 3);
    assert_eq!(exchanges[1]["request"]["action"], "ensure");
    assert_eq!(exchanges[1]["request"]["request_id"], ID);
    assert_eq!(
        exchanges[1]["request"]["journal_path"],
        "/synthetic/state/tasks.sqlite3"
    );
    assert_eq!(exchanges[1]["response"]["ensure_accepted"], true);
    assert_eq!(m.calls.iter().filter(|a| **a == Action::Ensure).count(), 1);
    let projection = serde_json::to_string(&status).unwrap();
    for private in [
        "/synthetic",
        "fixture-connector",
        "recipe_sha256",
        "token",
        "ready",
    ] {
        assert!(!projection.contains(private));
    }
    let replay = store.advance(&r, &g, ID, &mut m).await.unwrap();
    assert_eq!(replay.queries, 2);
    assert_eq!(m.calls.len(), 3);
}
#[tokio::test]
async fn lost_ensure_reply_reconciles_without_resend() {
    let (f, c, r, g) = setup();
    let mut m = Mock::new(&f, &c, [Reply::Stopped, Reply::Lost, Reply::Running]);
    let status = Store::new(&f.0)
        .unwrap()
        .advance(&r, &g, ID, &mut m)
        .await
        .unwrap();
    assert_eq!(status.phase, Phase::ServiceRunning);
    assert!(!status.ensure_accepted);
    assert_eq!(m.calls.iter().filter(|a| **a == Action::Ensure).count(), 1);
}
#[tokio::test]
async fn process_loss_after_durable_ensure_reopens_as_queries_only_under_new_generation() {
    let (f, c, r, mut g) = setup();
    let store = Store::new(&f.0).unwrap();
    let mut m = Mock::new(&f, &c, [Reply::Stopped, Reply::Crash]);
    let entered = Arc::new(tokio::sync::Notify::new());
    m.entered = Some(entered.clone());
    {
        let attempt = store.advance(&r, &g, ID, &mut m);
        tokio::pin!(attempt);
        tokio::time::timeout(Duration::from_secs(5), async {
            tokio::select! {
                _ = entered.notified() => (),
                result = &mut attempt => panic!("ensure did not reach transport: {result:?}"),
            }
        })
        .await
        .expect("synthetic ensure entered");
        // Drop exactly after the durable ensure fence and transport entry,
        // independent of fixture disk speed or an arbitrary cancellation timer.
    }
    assert_eq!(m.calls.iter().filter(|a| **a == Action::Ensure).count(), 1);
    let raw: Value =
        serde_json::from_slice(&std::fs::read(f.0.join(RECEIPT_FILE)).unwrap()).unwrap();
    assert_eq!(raw["records"][0]["ensure_requested"], true);
    g.generation += 1;
    g.epoch = "e".repeat(32);
    let mut fresh = Mock::new(&f, &c, [Reply::Running]);
    let status = Store::new(&f.0)
        .unwrap()
        .advance(&r, &g, ID, &mut fresh)
        .await
        .unwrap();
    assert_eq!(status.phase, Phase::ServiceRunning);
    assert!(fresh.calls.iter().all(|a| *a == Action::Query));
}
#[tokio::test]
async fn activating_foreign_owner_and_invalid_echo_never_authorize_start() {
    for reply in [
        Reply::Transitioning,
        Reply::Owner,
        Reply::WrongId,
        Reply::WrongGeneration,
        Reply::WrongAttempt,
        Reply::WrongRecipe,
        Reply::WrongAction,
        Reply::Lost,
    ] {
        let (f, c, r, g) = setup();
        let mut m = Mock::new(&f, &c, [reply; 4]);
        let status = Store::new(&f.0)
            .unwrap()
            .advance(&r, &g, ID, &mut m)
            .await
            .unwrap();
        assert_eq!(status.phase, Phase::NeedsAttention);
        assert_eq!(status.queries, 4);
        assert!(m.calls.iter().all(|a| *a == Action::Query));
    }
}
#[tokio::test]
async fn stopped_after_unknown_ensure_exhausts_queries_and_new_key_cannot_reset() {
    let (f, c, r, g) = setup();
    let mut m = Mock::new(
        &f,
        &c,
        [
            Reply::Stopped,
            Reply::Lost,
            Reply::Stopped,
            Reply::Stopped,
            Reply::Stopped,
        ],
    );
    let store = Store::new(&f.0).unwrap();
    let status = store.advance(&r, &g, ID, &mut m).await.unwrap();
    assert_eq!(status.phase, Phase::NeedsAttention);
    assert_eq!(status.last_state, Some(ServiceState::Stopped));
    assert_eq!(m.calls.iter().filter(|a| **a == Action::Ensure).count(), 1);
    assert!(matches!(
        store.advance(&r, &g, &"2".repeat(32), &mut m).await,
        Err("BOOTSTRAP_UNRESOLVED")
    ));
    let count = m.calls.len();
    store.advance(&r, &g, ID, &mut m).await.unwrap();
    assert_eq!(m.calls.len(), count);
}
#[tokio::test]
async fn selection_config_and_recipe_drift_prevent_all_transport() {
    for drift in 0..3 {
        let (f, c, r, g) = setup();
        let mut m = Mock::new(&f, &c, [Reply::Stopped]);
        match drift {
            0 => m.selected = false,
            1 => std::fs::write(f.0.join("kit").join(RECIPE_FILE), b"{}").unwrap(),
            _ => std::fs::write(f.0.join("kit/ssh-config"), b"#changed").unwrap(),
        }
        assert!(Store::new(&f.0)
            .unwrap()
            .advance(&r, &g, ID, &mut m)
            .await
            .is_err());
        assert!(m.calls.is_empty());
        assert!(!f.0.join(RECEIPT_FILE).exists());
    }
}
#[test]
fn optional_recipe_is_strict_and_ssh_command_has_no_service_paths_or_shell_options() {
    let (f, c, r, _) = setup();
    let args = r.arguments();
    assert_eq!(
        args.last().unwrap(),
        "/usr/local/libexec/bat-connector-bootstrap-v1"
    );
    assert_eq!(args[args.len() - 2], "fixture-connector");
    assert!(!args
        .iter()
        .any(|a| a.contains("synthetic") || a.contains("token")));
    let path = f.0.join("kit").join(RECIPE_FILE);
    let original = std::fs::read(&path).unwrap();
    for (field, value) in [
        ("ssh_alias", json!("-option")),
        ("service_id", json!({})),
        ("command", json!("bad")),
        ("journal_path", json!("/different/tasks.sqlite3")),
        ("schema_version", json!(true)),
    ] {
        let mut doc: Value = serde_json::from_slice(&original).unwrap();
        doc[field] = value;
        std::fs::write(&path, serde_json::to_vec(&doc).unwrap()).unwrap();
        assert!(Recipe::load(&f.0.join("kit"), &c).is_err());
    }
    std::fs::remove_file(path).unwrap();
    assert!(Recipe::load(&f.0.join("kit"), &c).unwrap().is_none());
}

// Use paused Tokio time so synchronous admission and durable receipt I/O
// cannot spend the watchdog margin. The unchanged 25-second guard is below
// two 20-second deadlines and also catches an exchange with no timeout at all.
#[tokio::test(start_paused = true)]
async fn controlled_pending_transport_obeys_total_deadline_without_ensure() {
    let (f, c, r, g) = setup();
    // A regressed second query must remain pending for the watchdog to catch it.
    let mut m = Mock::new(&f, &c, [Reply::Crash; 2]);
    let start = Instant::now();
    let status = tokio::time::timeout(
        Duration::from_secs(25),
        Store::new(&f.0).unwrap().advance(&r, &g, ID, &mut m),
    )
    .await
    .expect("production total deadline must bound a pending peer")
    .unwrap();
    assert!(start.elapsed() >= Duration::from_secs(20));
    assert_eq!(status.phase, Phase::NeedsAttention);
    assert_eq!(status.queries, 1);
    assert!(!status.ensure_requested);
    assert_eq!(m.calls.len(), 1);
    assert!(m.calls.iter().all(|a| *a == Action::Query));
    let raw: Value =
        serde_json::from_slice(&std::fs::read(f.0.join(RECEIPT_FILE)).unwrap()).unwrap();
    assert!(raw["records"][0]["exchanges"][0]["response"].is_null());
}

#[tokio::test]
async fn damaged_receipt_or_missing_positive_service_proof_never_reopens_transport() {
    let (f, c, r, g) = setup();
    let store = Store::new(&f.0).unwrap();
    let mut first = Mock::new(&f, &c, [Reply::Running]);
    store.advance(&r, &g, ID, &mut first).await.unwrap();
    let path = f.0.join(RECEIPT_FILE);
    let original: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    for fault in 0..5 {
        let mut changed = original.clone();
        match fault {
            0 => changed["records"][0]["exchanges"][0]["response"] = Value::Null,
            1 => {
                changed["records"][0]["exchanges"][0]["response"]["request_id"] =
                    json!("f".repeat(32))
            }
            2 => changed["records"][0]["exchanges"][0]["response"]["owner"] = json!("absent"),
            3 => changed["records"][0]["queries"] = json!(0),
            _ => {
                let duplicate = changed["records"][0].clone();
                changed["records"].as_array_mut().unwrap().push(duplicate);
            }
        }
        std::fs::write(&path, serde_json::to_vec(&changed).unwrap()).unwrap();
        let mut next = Mock::new(&f, &c, [Reply::Stopped]);
        assert!(store
            .advance(&r, &g, &"2".repeat(32), &mut next)
            .await
            .is_err());
        assert!(next.calls.is_empty());
    }
}

#[tokio::test]
async fn prepared_native_id_survives_reopen_and_recipe_loss_without_any_transport() {
    let (f, c, r, g) = setup();
    let mut m = Mock::new(&f, &c, [Reply::Running]);
    let first = Store::new(&f.0).unwrap().prepare(&r, &g, ID, &m).unwrap();
    assert_eq!(first.status.queries, 0);
    assert!(!first.status.ensure_requested);
    assert!(m.calls.is_empty());
    let reopened = Store::new(&f.0).unwrap();
    assert_eq!(reopened.latest().unwrap().unwrap().status.request_id, ID);
    assert!(reopened.prepare(&r, &g, &"2".repeat(32), &m).is_err());
    reopened.advance(&r, &g, ID, &mut m).await.unwrap();
    std::fs::remove_file(f.0.join("kit").join(RECIPE_FILE)).unwrap();
    let receipt = reopened.status(ID).unwrap().unwrap();
    assert_eq!(receipt.status.phase, Phase::ServiceRunning);
    assert_eq!(receipt.recipe_binding, r.binding());
    assert_eq!(m.calls.len(), 1);
}
#[test]
fn automatic_ensure_requires_explicit_trusted_opt_in() {
    let (f, c, r, _) = setup();
    assert!(!r.auto_ensure());
    let path = f.0.join("kit").join(RECIPE_FILE);
    let mut value: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    value["auto_ensure"] = json!(true);
    std::fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    let opted = Recipe::load(&f.0.join("kit"), &c).unwrap().unwrap();
    assert!(opted.auto_ensure());
    assert_ne!(opted.binding(), r.binding());
    value["auto_ensure"] = json!(1);
    std::fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(Recipe::load(&f.0.join("kit"), &c).is_err());
}

#[tokio::test]
async fn automatic_policy_resumes_original_and_never_replaces_exhaustion_or_success() {
    use bat_fleet_core::bootstrap_policy::{automatic, Automatic};
    for success in [false, true] {
        let (f, c, manual, g) = setup();
        assert_eq!(automatic(&manual, None).unwrap(), Automatic::Hold);
        let path = f.0.join("kit").join(RECIPE_FILE);
        let mut value: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        value["auto_ensure"] = json!(true);
        std::fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
        let opted = Recipe::load(&f.0.join("kit"), &c).unwrap().unwrap();
        assert_eq!(automatic(&opted, None).unwrap(), Automatic::Prepare);
        let store = Store::new(&f.0).unwrap();
        let mut peer = Mock::new(
            &f,
            &c,
            [if success { Reply::Running } else { Reply::Lost }; 4],
        );
        store.prepare(&opted, &g, ID, &peer).unwrap();
        let saved = Store::new(&f.0).unwrap().latest().unwrap().unwrap();
        assert_eq!(
            automatic(&opted, Some(&saved)).unwrap(),
            Automatic::Resume(ID.into())
        );
        let mut changed = saved.clone();
        changed.recipe_binding = "e".repeat(64);
        assert_eq!(
            automatic(&opted, Some(&changed)),
            Err("BOOTSTRAP_UNRESOLVED")
        );
        store.advance(&opted, &g, ID, &mut peer).await.unwrap();
        let terminal = Store::new(&f.0).unwrap().latest().unwrap().unwrap();
        assert_eq!(automatic(&opted, Some(&terminal)).unwrap(), Automatic::Hold);
        assert_eq!(terminal.status.request_id, ID);
        assert!(peer.calls.iter().all(|action| *action == Action::Query));
    }
}
