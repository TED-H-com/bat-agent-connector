#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod bridge;
mod brief_files;
mod clipboard;
mod credentials;
mod desktop_preferences;
mod diagnostics_files;
mod files;
mod fleet;
mod fleet_bootstrap;
mod fleet_control;
mod fleet_lifecycle;
#[cfg(windows)]
mod fleet_native;
mod fleet_readiness;
mod managed;
mod managed_login;
mod managed_recovery;
mod startup_role;
mod tailscale_control;
mod updates;
mod workflow_version;

use bridge::{Bridge, ConnectorRequest, ConnectorResponse, NativeStatus};
use std::sync::Arc;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{TrayIconBuilder, TrayIconEvent},
    Manager, State, WebviewWindow,
};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;
use zeroize::Zeroizing;

#[derive(serde::Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
enum ManagedRequest {
    Status,
    SetLogin { enabled: bool },
    OpenBrowser,
    Stop,
    Start,
}

struct ManagedBrowserMenu(MenuItem<tauri::Wry>);

// One snapshot path, also exercised without constructing a platform WebView.
fn managed_status(state: &managed::Managed) -> Result<managed::Status, String> {
    state.read_status()
}

#[tauri::command]
async fn managed_control(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Arc<managed::Managed>>,
    bridge: State<'_, Arc<Bridge>>,
    input: ManagedRequest,
) -> Result<managed::Status, String> {
    local_main(&window)?;
    let state = state.inner().clone();
    if matches!(input, ManagedRequest::Status) {
        return managed_status(&state);
    }
    if matches!(input, ManagedRequest::Stop | ManagedRequest::Start) {
        let result = bridge
            .managed_lifecycle(state.clone(), matches!(input, ManagedRequest::Start))
            .await;
        if let Some(browser) = app.try_state::<ManagedBrowserMenu>() {
            let _ = browser.0.set_enabled(
                state
                    .status()
                    .lifecycle
                    .as_ref()
                    .is_some_and(|v| v.state == "running"),
            );
        }
        return result;
    }
    tokio::task::spawn_blocking(move || {
        let _serial = state
            .serial
            .try_lock()
            .map_err(|_| "MANAGED_SERVICE_BUSY")?;
        match input {
            ManagedRequest::Stop | ManagedRequest::Start => unreachable!(),
            ManagedRequest::Status => unreachable!(),
            ManagedRequest::SetLogin { enabled } => state.set_login(enabled),
            ManagedRequest::OpenBrowser => {
                let path = state.browser_file()?;
                app.opener()
                    .open_path(path.to_string_lossy(), None::<&str>)
                    .map_err(|_| "MANAGED_BROWSER_OPEN_FAILED")?;
                Ok(state.status())
            }
        }
    })
    .await
    .map_err(|_| "MANAGED_SERVICE_UNAVAILABLE".to_string())?
}

fn open_managed_browser(app: &tauri::AppHandle) {
    let app = app.clone();
    let state = app.state::<Arc<managed::Managed>>().inner().clone();
    tauri::async_runtime::spawn(async move {
        let browser_app = app.clone();
        let result = tokio::task::spawn_blocking(move || {
            let _serial = state
                .serial
                .try_lock()
                .map_err(|_| "MANAGED_SERVICE_BUSY")?;
            let path = state.browser_file()?;
            browser_app
                .opener()
                .open_path(path.to_string_lossy(), None::<&str>)
                .map_err(|_| "MANAGED_BROWSER_OPEN_FAILED".to_string())
        })
        .await
        .unwrap_or_else(|_| Err("MANAGED_SERVICE_UNAVAILABLE".into()));
        if let Err(code) = result {
            show(&app);
            app.dialog()
                .message(format!(
                    "Browser entry needs attention ({code}). / 瀏覽器入口需要檢查設定。"
                ))
                .title("Better Agent Dashboard")
                .show(|_| {});
        }
    });
}

#[tauri::command]
async fn desktop_update(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Arc<updates::Updates>>,
    control: State<'_, Arc<fleet_control::Control>>,
    input: updates::Request,
) -> Result<updates::Status, String> {
    local_main(&window)?;
    if matches!(input, updates::Request::Status {}) {
        return state.status();
    }
    let _serial = state.serial.try_lock().map_err(|_| "UPDATE_BUSY")?;
    match input {
        updates::Request::Status {} => state.status(),
        updates::Request::Check {} => state.check(&app).await,
        updates::Request::Download { candidate_id } => state.download(&candidate_id).await,
        updates::Request::Install { candidate_id } => {
            use std::sync::atomic::Ordering;
            // Validate signed bytes before any local connection shutdown.
            state.begin_install(&candidate_id)?;
            if app
                .state::<QuitState>()
                .0
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_err()
            {
                state.install_returned()?;
                return Err("FLEET_STOP_REQUESTED".into());
            }
            control.set_stopping(true);
            let updates = state.inner().clone();
            let path = control.config.clone();
            let result = tokio::task::spawn_blocking(move || {
                #[cfg(windows)]
                {
                    match path.try_exists() {
                        Ok(false) => fleet_native::with_unconfigured_fleet_absent(&path, || {
                            updates.install(&candidate_id)
                        }),
                        Ok(true) => fleet_native::with_stopped_fleet(&path, || {
                            updates.install(&candidate_id)
                        }),
                        Err(_) => Err("FLEET_CONFIGURATION_UNPROVEN".into()),
                    }
                }
                #[cfg(not(windows))]
                {
                    let _ = path;
                    updates.install(&candidate_id)
                }
            })
            .await
            .unwrap_or_else(|_| Err("UPDATE_INSTALLATION_UNSETTLED".into()));
            // A returned failure before the durable intent is safe to review again. Once
            // the installer may have run, keep local launches fenced until restart/readback.
            let _ = state.install_returned();
            restore_fleet_after_refusal(&state, &control);
            app.state::<QuitState>().0.store(false, Ordering::SeqCst);
            result?;
            state.status()
        }
    }
}

#[tauri::command]
async fn fleet_bootstrap(
    window: WebviewWindow,
    state: State<'_, Arc<fleet_bootstrap::Bootstrap>>,
    control: State<'_, Arc<fleet_control::Control>>,
    input: fleet_bootstrap::Request,
) -> Result<serde_json::Value, String> {
    local_main(&window)?;
    state
        .inner()
        .clone()
        .request(control.inner().clone(), input)
        .await
}

#[tauri::command]
async fn tailscale_control(
    window: WebviewWindow,
    control: State<'_, Arc<fleet_control::Control>>,
    input: tailscale_control::Request,
) -> Result<serde_json::Value, String> {
    local_main(&window)?;
    tailscale_control::request(control.inner().clone(), input).await
}

#[tauri::command]
async fn fleet_control(
    window: WebviewWindow,
    state: State<'_, Arc<fleet_control::Control>>,
    input: fleet_control::Request,
) -> Result<serde_json::Value, String> {
    local_main(&window)?;
    let result = state.inner().clone().request(input).await?;
    if result
        .get("dashboard")
        .or_else(|| result.get("summary").and_then(|v| v.get("dashboard")))
        .and_then(serde_json::Value::as_bool)
        == Some(true)
    {
        show(window.app_handle());
    }
    Ok(result)
}

#[tauri::command]
fn fleet_availability(
    window: WebviewWindow,
    state: State<'_, fleet::FleetBridge>,
) -> Result<fleet::FleetAvailability, String> {
    local_main(&window)?;
    Ok(state.availability())
}

#[tauri::command]
async fn fleet_request(
    window: WebviewWindow,
    state: State<'_, fleet::FleetBridge>,
    control: State<'_, Arc<fleet_control::Control>>,
    input: fleet::FleetRequest,
) -> Result<serde_json::Value, String> {
    local_main(&window)?;
    let ticket = control.ticket();
    if control.is_stopping()
        && !matches!(
            input,
            fleet::FleetRequest::Status {}
                | fleet::FleetRequest::Contract {}
                | fleet::FleetRequest::ValidateConfiguration {}
        )
    {
        return Err("FLEET_STOP_REQUESTED".into());
    }
    if matches!(input, fleet::FleetRequest::QuitOwned { .. }) {
        control.cancel_login();
    }
    state.request(input, ticket).await
}

#[tauri::command]
fn open_external(window: WebviewWindow, app: tauri::AppHandle, url: String) -> Result<(), String> {
    local_main(&window)?;
    let target = bridge::external_url(&url)?;
    app.opener()
        .open_url(target.as_str(), None::<&str>)
        .map_err(|_| "Unable to open the system browser".into())
}

fn local_main(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Only the main Dashboard may use this command".into());
    }
    let url = window.url().map_err(|_| "Window origin unavailable")?;
    let bundled = url.scheme() == "tauri" && url.host_str() == Some("localhost")
        || matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost");
    let dev = cfg!(debug_assertions)
        && url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port() == Some(1420);
    if !bundled && !dev {
        return Err("Remote content cannot use native commands".into());
    }
    Ok(())
}

#[tauri::command]
fn native_status(
    window: WebviewWindow,
    state: State<'_, Arc<Bridge>>,
    managed: State<'_, Arc<managed::Managed>>,
) -> Result<NativeStatus, String> {
    local_main(&window)?;
    let mut status = state.status();
    status.startup_role = managed.startup_role();
    Ok(status)
}

#[tauri::command]
async fn managed_select_role(
    window: WebviewWindow,
    state: State<'_, Arc<Bridge>>,
    managed: State<'_, Arc<managed::Managed>>,
    browser: State<'_, ManagedBrowserMenu>,
    input: startup_role::Selection,
) -> Result<NativeStatus, String> {
    local_main(&window)?;
    state
        .select_startup_role(managed.inner().clone(), input)
        .await?;
    // The menu was built while a clean installation was still role pending.
    let _ = browser.0.set_enabled(managed.status().ready);
    let mut status = state.status();
    status.startup_role = managed.startup_role();
    Ok(status)
}

#[tauri::command]
async fn connector_connect(
    window: WebviewWindow,
    state: State<'_, Arc<Bridge>>,
) -> Result<serde_json::Value, String> {
    local_main(&window)?;
    state.connect().await
}

#[tauri::command]
fn connector_disconnect(
    window: WebviewWindow,
    state: State<'_, Arc<Bridge>>,
) -> Result<(), String> {
    local_main(&window)?;
    state.disconnect();
    Ok(())
}

#[tauri::command]
fn connector_reload_configuration(
    window: WebviewWindow,
    state: State<'_, Arc<Bridge>>,
) -> Result<NativeStatus, String> {
    local_main(&window)?;
    state.reload_configuration()
}

#[tauri::command]
async fn connector_setup_configuration(
    window: WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, Arc<Bridge>>,
    config: bridge::Config,
    locale: credentials::Locale,
) -> Result<Option<NativeStatus>, String> {
    local_main(&window)?;
    state.setup_configuration(config, move |candidate| {
        let (message, save, cancel) = match locale {
            credentials::Locale::English => (
                format!("Connect this Dashboard to:\n{}\n\nExpected account: {}\n\nSave these settings? Add and verify a credential in the next step.", candidate.endpoint, candidate.expected_actor),
                "Save configuration", "Cancel"),
            credentials::Locale::TraditionalChinese => (
                format!("將此 Dashboard 連接至：\n{}\n\n預期帳號：{}\n\n儲存此設定？下一步再新增並驗證憑證。", candidate.endpoint, candidate.expected_actor),
                "儲存連線設定", "取消"),
        };
        app.dialog().message(message).title("Better Agent Dashboard")
            .buttons(tauri_plugin_dialog::MessageDialogButtons::OkCancelCustom(save.into(), cancel.into()))
            .blocking_show()
    }).await
}

#[tauri::command]
fn connector_forget_credential(
    window: WebviewWindow,
    state: State<'_, Arc<Bridge>>,
) -> Result<(), String> {
    local_main(&window)?;
    state.forget_credential()
}

#[tauri::command]
async fn connector_enroll(
    window: WebviewWindow,
    state: State<'_, Arc<Bridge>>,
    locale: credentials::Locale,
) -> Result<Option<serde_json::Value>, String> {
    local_main(&window)?;
    #[cfg(windows)]
    let parent = window.hwnd().map_err(|_| "Native window unavailable")?.0 as isize;
    #[cfg(not(windows))]
    let parent = 0;
    state.enroll(locale, parent).await
}

#[tauri::command]
async fn connector_request(
    window: WebviewWindow,
    state: State<'_, Arc<Bridge>>,
    input: ConnectorRequest,
) -> Result<ConnectorResponse, String> {
    local_main(&window)?;
    state.request(input).await
}

struct NativeFiles(Result<Arc<files::Files>, String>);
impl NativeFiles {
    fn get(&self) -> Result<Arc<files::Files>, String> {
        self.0.clone()
    }
}

#[tauri::command]
fn native_files_status(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
) -> Result<files::Status, String> {
    local_main(&window)?;
    state.get()?.status()
}
#[tauri::command]
async fn native_files_pick(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    draft_id: String,
) -> Result<Vec<files::Receipt>, String> {
    local_main(&window)?;
    files::validate_draft(&draft_id)?;
    let files = state.get()?;
    let _dialog = files
        .dialog
        .try_lock()
        .map_err(|_| "A native file dialog is already open")?;
    let scope = files.scope()?;
    let (tx, rx) = tokio::sync::oneshot::channel();
    window
        .dialog()
        .file()
        .set_parent(&window)
        .pick_files(move |paths| {
            let _ = tx.send(paths);
        });
    let Some(paths) = rx.await.map_err(|_| "File dialog interrupted")? else {
        return Ok(Vec::new());
    };
    let paths = paths
        .into_iter()
        .map(|p| {
            p.into_path()
                .map_err(|_| "Only local regular files are supported".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    files.pick_paths(scope, &draft_id, paths).await
}
#[tauri::command]
fn native_files_paste_arm(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    draft_id: String,
) -> Result<(), clipboard::ClipboardError> {
    local_main(&window)?;
    state
        .get()?
        .paste_arm(draft_id, window.is_focused().unwrap_or(false))
}
#[tauri::command]
async fn native_files_paste(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    draft_id: String,
) -> Result<files::Receipt, clipboard::ClipboardError> {
    local_main(&window)?;
    let files = state.get()?;
    let limit = files.paste_limit().await?;
    // Only copy OS bytes on the window thread; decode/encode and spool on a blocking worker.
    let (tx, rx) = tokio::sync::oneshot::channel();
    let owner = window.clone();
    let reader = files.clone();
    window
        .run_on_main_thread(move || {
            let result = reader.read_clipboard(
                &draft_id,
                owner.is_focused().unwrap_or(false),
                &mut clipboard::SystemClipboard,
            );
            let _ = tx.send(result);
        })
        .map_err(|_| clipboard::ClipboardError::refused("window_unavailable"))?;
    let selected = rx
        .await
        .map_err(|_| clipboard::ClipboardError::refused("paste_interrupted"))??;
    files.spool_clipboard(selected, limit).await
}
#[tauri::command]
fn native_files_drop_target(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    draft_id: String,
    enabled: bool,
) -> Result<(), String> {
    local_main(&window)?;
    state.get()?.drop_target(draft_id, enabled)
}
#[tauri::command]
fn native_files_upload(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    handle_id: String,
) -> Result<files::Receipt, String> {
    local_main(&window)?;
    state.get()?.start_upload(&handle_id)
}
async fn save_file(
    window: &WebviewWindow,
    files: Arc<files::Files>,
    reference: files::ArtifactRef,
    existing: Option<String>,
) -> Result<Option<files::Receipt>, String> {
    let _dialog = files
        .dialog
        .try_lock()
        .map_err(|_| "A native file dialog is already open")?;
    let scope = files.scope()?;
    let metadata = files.save_metadata(&scope, &reference).await?;
    let (tx, rx) = tokio::sync::oneshot::channel();
    let name = metadata["display_name"]
        .as_str()
        .filter(|name| !name.contains(['/', '\\', ':']))
        .unwrap_or("artifact");
    window
        .dialog()
        .file()
        .set_parent(window)
        .set_file_name(name)
        .save_file(move |path| {
            let _ = tx.send(path);
        });
    let Some(path) = rx.await.map_err(|_| "Save dialog interrupted")? else {
        return Ok(None);
    };
    let path = path
        .into_path()
        .map_err(|_| "Only local destinations are supported")?;
    let destination = files::Destination::from_selection(&path)?;
    files
        .save(scope, reference, metadata, destination, existing)
        .map(Some)
}
#[tauri::command]
async fn native_files_save(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    reference: files::ArtifactRef,
) -> Result<Option<files::Receipt>, String> {
    local_main(&window)?;
    save_file(&window, state.get()?, reference, None).await
}

#[tauri::command]
async fn native_brief_save(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    bridge: State<'_, Arc<Bridge>>,
    reference: brief_files::Reference,
) -> Result<Option<brief_files::Receipt>, String> {
    local_main(&window)?;
    reference.validate()?;
    let files = state.get()?;
    let _dialog = files
        .dialog
        .try_lock()
        .map_err(|_| "A native file dialog is already open")?;
    let bridge = bridge.inner().clone();
    let scope = bridge.file_scope()?;
    // Refuse invalid, unavailable or changed reports before asking for a destination.
    brief_files::read(&bridge, &scope, &reference).await?;
    let (tx, rx) = tokio::sync::oneshot::channel();
    window
        .dialog()
        .file()
        .set_parent(&window)
        .set_title(reference.dialog_title())
        .set_file_name(reference.filename())
        .add_filter(reference.filter_label(), &[reference.extension()])
        .save_file(move |path| {
            let _ = tx.send(path);
        });
    let Some(path) = rx.await.map_err(|_| "Save dialog interrupted")? else {
        return Ok(None);
    };
    let path = path
        .into_path()
        .map_err(|_| "Only local destinations are supported")?;
    let destination = files::Destination::from_selection(&path)?;
    // Recheck original source access after an arbitrarily long user dialog.
    brief_files::save(bridge, scope, reference, destination)
        .await
        .map(Some)
}
#[tauri::command]
async fn native_diagnostics_save(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    input: diagnostics_files::Input,
) -> Result<Option<diagnostics_files::Receipt>, String> {
    local_main(&window)?;
    input.validate()?;
    let files = state.get()?;
    let _dialog = files
        .dialog
        .try_lock()
        .map_err(|_| "DIAGNOSTICS_DIALOG_BUSY")?;
    let (tx, rx) = tokio::sync::oneshot::channel();
    window
        .dialog()
        .file()
        .set_parent(&window)
        .set_title(if input.locale == "zh-TW" {
            "儲存診斷資料"
        } else {
            "Save diagnostics"
        })
        .set_file_name(&input.filename)
        .add_filter("JSON", &["json"])
        .save_file(move |path| {
            let _ = tx.send(path);
        });
    let Some(path) = rx.await.map_err(|_| "DIAGNOSTICS_SAVE_INTERRUPTED")? else {
        return Ok(None);
    };
    let path = path
        .into_path()
        .map_err(|_| "DIAGNOSTICS_DESTINATION_INVALID")?;
    input
        .save(files::Destination::from_selection(&path)?)
        .map(Some)
}
#[tauri::command]
async fn native_files_control(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    transfer_id: String,
    action: files::Control,
) -> Result<(), String> {
    local_main(&window)?;
    let files = state.get()?;
    if matches!(action, files::Control::Retry) {
        if let Ok(reference) = files.download_reference(&transfer_id) {
            save_file(&window, files, reference, Some(transfer_id)).await?;
            return Ok(());
        }
    }
    files.control(&transfer_id, action).await
}
#[tauri::command]
async fn native_files_preview(
    window: WebviewWindow,
    state: State<'_, NativeFiles>,
    reference: files::ArtifactRef,
) -> Result<files::Preview, String> {
    local_main(&window)?;
    state.get()?.preview(&reference).await
}

fn show(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

#[derive(Default)]
struct QuitState(std::sync::atomic::AtomicBool);
fn restore_fleet_after_refusal(updates: &updates::Updates, control: &fleet_control::Control) {
    control.set_stopping(updates.keep_fleet_stopped());
}
fn quit(app: &tauri::AppHandle) {
    use std::sync::atomic::Ordering;
    if app.state::<QuitState>().0.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    let control = app.state::<Arc<fleet_control::Control>>().inner().clone();
    control.set_stopping(true);
    tauri::async_runtime::spawn(async move {
        let path = control.config.clone();
        let exit_app = app.clone();
        let result = tokio::task::spawn_blocking(move || -> Result<(), String> {
            #[cfg(windows)]
            {
                match path.try_exists() {
                    Ok(false) => fleet_native::with_unconfigured_fleet_absent(&path, || {
                        exit_app.exit(0);
                        Ok(())
                    }),
                    Ok(true) => fleet_native::with_stopped_fleet(&path, || {
                        exit_app.exit(0);
                        Ok(())
                    }),
                    Err(_) => Err("FLEET_CONFIGURATION_UNPROVEN".into()),
                }
            }
            #[cfg(not(windows))]
            {
                let _ = path;
                exit_app.exit(0);
                Ok(())
            }
        })
        .await
        .unwrap_or_else(|_| Err("FLEET_STOP_UNCONFIRMED".into()));
        match result {
            Ok(()) => {}
            Err(code) => {
                restore_fleet_after_refusal(&app.state::<Arc<updates::Updates>>(), &control);
                app.state::<QuitState>().0.store(false, Ordering::SeqCst);
                show(&app);
                app.dialog().message(format!("Fleet shutdown was not confirmed ({code}). Dashboard remains open; read Fleet status before retrying. / 尚未確認 Fleet 已停止，程式仍保持開啟。請先檢視 Fleet 狀態。"))
                    .title("Better Agent Dashboard").show(|_| {});
            }
        }
    });
}
#[cfg(windows)]
fn login_existing(app: tauri::AppHandle, path: std::path::PathBuf) {
    tauri::async_runtime::spawn(async move {
        let control = app.state::<Arc<fleet_control::Control>>().inner().clone();
        let expected = control.config.clone();
        let result = tokio::task::spawn_blocking(move || {
            let path = bat_fleet_core::installation::Snapshot::load(&path)?;
            if path.path() != bat_fleet_core::installation::canonical_local(&expected)? {
                return Err("INSTALLATION_CHANGED");
            }
            fleet_native::login_options(path.path())
        })
        .await
        .unwrap_or(Err("LOGIN_UNPROVEN"));
        match result {
            Ok((picker, dashboard)) => {
                if picker {
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.eval("location.hash = '/settings'");
                    }
                }
                if picker || dashboard {
                    show(&app);
                }
                if !picker {
                    control.start_login();
                }
            }
            Err(code) => {
                show(&app);
                app.dialog()
                    .message(format!(
                        "Fleet login requires attention ({code}). / Fleet 登入啟動需要檢查設定。"
                    ))
                    .show(|_| {});
            }
        }
    });
}

fn main() {
    // Read/remove before GTK, WebView or async runtime starts any helper threads/processes.
    let token = Zeroizing::new(std::env::var("BATC_DESKTOP_TOKEN").unwrap_or_default());
    std::env::remove_var("BATC_DESKTOP_TOKEN");
    use bat_fleet_core::installation::Entry;
    let mut arguments: Vec<_> = std::env::args_os().skip(1).collect();
    let managed_login = arguments.len() == 1 && arguments[0] == "--managed-login";
    if managed_login {
        arguments.clear();
    }
    let entry = Entry::parse(&arguments).unwrap_or_else(|_| std::process::exit(2));
    if let Entry::Supervisor(path) = &entry {
        drop(token);
        #[cfg(windows)]
        std::process::exit(if fleet_native::run_supervisor(path).is_ok() {
            0
        } else {
            1
        });
        #[cfg(not(windows))]
        {
            let _ = path;
            std::process::exit(2);
        }
    }
    let login_path = match entry {
        Entry::Login(path) => Some(path),
        _ => None,
    };
    #[cfg(windows)]
    let login_options = login_path.as_ref().map(|p| fleet_native::login_options(p));
    #[cfg(not(windows))]
    let login_options: Option<Result<(bool, bool), &str>> =
        login_path.as_ref().map(|_| Err("WINDOWS_REQUIRED"));
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _| {
            let arguments: Vec<_> = argv
                .into_iter()
                .skip(1)
                .map(std::ffi::OsString::from)
                .collect();
            match Entry::parse(&arguments) {
                Ok(Entry::Dashboard) => show(app),
                #[cfg(windows)]
                Ok(Entry::Login(path)) => login_existing(app.clone(), path),
                _ => {}
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(move |app| {
            let (managed, launch) = managed::Managed::prepare(
                &app.path().resource_dir()?,
                &app.path().app_local_data_dir()?,
                &app.path().app_config_dir()?,
            );
            let managed_status = managed.status();
            let bridge = Arc::new(match launch {
                _ if managed_status.mode == "external" => {
                    Bridge::load(&app.path().app_config_dir()?, token)
                }
                Some(launch) => Bridge::managed(
                    &app.path().app_config_dir()?,
                    launch.config(),
                    launch.token.clone(),
                    launch.identity(),
                ),
                None if managed_status
                    .lifecycle
                    .as_ref()
                    .is_some_and(|v| v.state != "running") =>
                {
                    Bridge::managed_failure(&app.path().app_config_dir()?, "SERVICE_STOPPED".into())
                }
                None if managed_status.mode == "role_pending" && managed_status.error.is_none() => {
                    Bridge::role_pending(&app.path().app_config_dir()?)
                }
                None => Bridge::managed_failure(
                    &app.path().app_config_dir()?,
                    managed_status
                        .error
                        .clone()
                        .unwrap_or_else(|| "MANAGED_SERVICE_UNAVAILABLE".into()),
                ),
            });
            let managed = Arc::new(managed);
            managed_recovery::start(bridge.clone(), managed.clone());
            app.manage(managed);
            app.manage(NativeFiles(files::Files::open(
                &app.path().app_local_data_dir()?.join("file-transfers"),
                bridge.clone(),
            )));
            app.manage(bridge);
            let updates = Arc::new(updates::Updates::new(
                app.path().app_local_data_dir()?.join("update-intents"),
                app.package_info().version.to_string(),
                app.config()
                    .plugins
                    .0
                    .get("updater")
                    .cloned()
                    .unwrap_or_default(),
            ));
            let update_pending = updates.keep_fleet_stopped();
            app.manage(updates);
            let fleet_path = login_path
                .clone()
                .unwrap_or(app.path().app_config_dir()?.join("fleet.json"));
            app.manage(fleet::FleetBridge::load_path(fleet_path.clone()));
            let control = Arc::new(fleet_control::Control::new(fleet_path));
            control.set_stopping(update_pending);
            app.manage(control.clone());
            let bootstrap = Arc::new(fleet_bootstrap::Bootstrap::default());
            app.manage(bootstrap.clone());
            #[cfg(windows)]
            bootstrap.start_auto(control.clone());
            app.manage(QuitState::default());
            let mut config = app.config().app.windows[0].clone();
            config.visible = managed::startup_visible(
                &managed_status,
                managed_login,
                update_pending,
                !matches!(login_options, Some(Ok((false, false)))),
            );
            let window = tauri::WebviewWindowBuilder::from_config(app, &config)?
                .initialization_script(
                    if login_path.is_some()
                        || update_pending
                        || managed_status
                            .lifecycle
                            .as_ref()
                            .is_some_and(|v| v.state != "running")
                    {
                        "location.hash = '/settings';"
                    } else {
                        ""
                    },
                )
                .on_navigation(|url| {
                    url.scheme() == "tauri" && url.host_str() == Some("localhost")
                        || matches!(url.scheme(), "http" | "https")
                            && url.host_str() == Some("tauri.localhost")
                        || cfg!(debug_assertions)
                            && url.scheme() == "http"
                            && url.host_str() == Some("127.0.0.1")
                            && url.port() == Some(1420)
                })
                .build()?;
            if let Some(Err(code)) = login_options {
                window
                    .dialog()
                    .message(format!(
                        "Fleet login requires attention ({code}). / Fleet 登入啟動需要檢查設定。"
                    ))
                    .show(|_| {});
            }
            #[cfg(windows)]
            if !update_pending
                && (matches!(login_options, Some(Ok((false, _))))
                    || managed_login && managed_status.ready)
            {
                control.start_login();
            }
            let open = MenuItem::with_id(app, "open", "Open Dashboard", true, None::<&str>)?;
            let browser = MenuItem::with_id(
                app,
                "browser",
                "Open in Browser",
                managed_status.ready,
                None::<&str>,
            )?;
            app.manage(ManagedBrowserMenu(browser.clone()));
            let settings =
                MenuItem::with_id(app, "settings", "Background Settings", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit Dashboard", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &browser, &settings, &quit_item])?;
            let mut tray = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("Better Agent Dashboard")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show(app),
                    "browser" => open_managed_browser(app),
                    "settings" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.eval("location.hash = '/settings';");
                        }
                        show(app);
                    }
                    "quit" => quit(app),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if matches!(event, TrayIconEvent::DoubleClick { .. }) {
                        show(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .on_webview_event(|webview, event| {
            if webview.label() == "main" {
                if let tauri::WebviewEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) =
                    event
                {
                    if let Ok(files) = webview.state::<NativeFiles>().get() {
                        let paths = paths.clone();
                        tauri::async_runtime::spawn(files.dropped(paths));
                    }
                }
            }
        })
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) =
                    event
                {
                    if let Ok(files) = window.state::<NativeFiles>().get() {
                        let paths = paths.clone();
                        tauri::async_runtime::spawn(files.dropped(paths));
                    }
                }
            }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Closing hides only. Explicit Quit uses exact-owner normal Fleet shutdown.
                if window.hide().is_ok() {
                    api.prevent_close();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            managed_control,
            managed_select_role,
            desktop_update,
            native_files_status,
            native_files_pick,
            native_files_paste_arm,
            native_files_paste,
            native_files_drop_target,
            native_files_upload,
            native_files_save,
            native_brief_save,
            native_diagnostics_save,
            native_files_control,
            native_files_preview,
            native_status,
            connector_connect,
            connector_disconnect,
            connector_reload_configuration,
            connector_setup_configuration,
            connector_forget_credential,
            connector_enroll,
            connector_request,
            fleet_availability,
            fleet_request,
            fleet_control,
            fleet_bootstrap,
            tailscale_control,
            open_external
        ])
        .build(tauri::generate_context!())
        .expect("desktop application startup")
        .run(|_app, _event| {
            #[cfg(target_os = "macos")]
            match _event {
                // Finder/Dock activation reaches the existing process without spawning
                // another executable, so the single-instance callback alone is insufficient.
                tauri::RunEvent::Reopen { .. } => show(_app),
                // Cmd+Q and the system Quit Apple event use the same shutdown path as tray Quit.
                tauri::RunEvent::ExitRequested {
                    code: None, api, ..
                } => {
                    api.prevent_exit();
                    quit(_app);
                }
                _ => {}
            }
        });
}

#[cfg(test)]
mod managed_stop_tests {
    use super::*;
    #[test]
    fn existing_managed_command_accepts_stop_and_start() {
        for action in ["stop", "start"] {
            assert!(serde_json::from_value::<ManagedRequest>(
                serde_json::json!({"action": action})
            )
            .is_ok());
        }
    }
}
