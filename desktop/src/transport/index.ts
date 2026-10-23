import { invoke, isTauri } from "@tauri-apps/api/core";
import {locale, t} from "../i18n.js";
import {browserCredentialRefusals} from "../credential-refusals.js";

export const nativeDesktop = isTauri();
export interface ConnectorResponse { status: number; data: any }
export type StartupRole = "client" | "provider" | "both";
export interface StartupRoleStatus {role: StartupRole | null; generation: number; installation_ref: string | null}
export interface NativeStatus {
  startup_role?: StartupRoleStatus | null;
  configuration_source?: "managed" | "external";
  managed_recovery?: string | null;
  managed_rebind?: number | null;
  updates?: boolean; diagnostics_save?: boolean; app_build?: {version: string; commit: string | null};
  endpoint: string | null; error: string | null; credential_available: boolean;
  expected_actor?: string; credential_source?: "launch_environment" | "windows_credential_manager" | null;
  credential_saved?: boolean; enrollment_supported?: boolean; configuration_reload?: boolean; configuration_setup?: boolean;
  configuration_file?: string; connected?: boolean; file_transfers?: boolean; brief_exports?: boolean;
}
export let nativeFileSupport = false;
export let nativeBriefExportSupport = false;
export const browserSessionToken = "managed-browser-session";
let browserCsrf: string | null = null;
export async function restoreBrowserSession(): Promise<boolean> {
  if (nativeDesktop) return false;
  const response = await fetch("/api/v1/browser-session", {credentials: "same-origin", mode: "same-origin",
    cache: "no-store", redirect: "error"});
  if (!response.ok) return false;
  const data = await response.json();
  if (typeof data.csrf !== "string" || data.csrf.length < 32) return false;
  browserCsrf = data.csrf;
  return true;
}
export function browserCredentialError(refusal: {status: number; code: string; credentialRefused?: boolean}) {
  return Object.assign(new Error(t(refusal.status === 401 || refusal.credentialRefused ? 'global_token_rejected' : 'error_code_FORBIDDEN')),
    refusal, {localized: true});
}
export function browserAuthHeaders(token: string): Record<string, string> {
  if (token !== browserSessionToken) {
    const refusal = browserCredentialRefusals.read(token);
    if (refusal) throw browserCredentialError(refusal);
    return {Authorization: `Bearer ${token}`};
  }
  if (!browserCsrf) throw new Error("Browser session needs to be restored");
  return {"X-Batc-CSRF": browserCsrf};
}
export async function forgetBrowserSession() {
  if (!browserCsrf) return;
  const headers = {...browserAuthHeaders(browserSessionToken), "Content-Type": "application/json"};
  browserCsrf = null;
  return fetch("/api/v1/browser-session/logout", {method: "POST", credentials: "same-origin",
    mode: "same-origin", redirect: "error", headers, body: "{}"});
}
export async function nativeStatus() {
  const status = await invoke<NativeStatus>("native_status");
  nativeFileSupport = status.file_transfers === true;
  nativeBriefExportSupport = status.brief_exports === true;
  return status;
}
export const nativeDiagnosticsSave = (input: {content: string; filename: string; locale: string}) =>
  invoke<{display_name: string} | null>("native_diagnostics_save", {input});
export const nativeFilesStatus = () => invoke<any>("native_files_status");
export const nativeFilesPick = (draftId: string) => invoke<any[]>("native_files_pick", {draftId});
export const nativeFilesPasteArm = (draftId: string) => invoke<void>("native_files_paste_arm", {draftId});
export const nativeFilesPaste = (draftId: string) => invoke<any>("native_files_paste", {draftId});
export const nativeFilesUpload = (handleId: string) => invoke<any>("native_files_upload", {handleId});
export const nativeFilesDropTarget = (draftId: string, enabled: boolean) => invoke<void>("native_files_drop_target", {draftId, enabled});
export const nativeFilesControl = (transferId: string, action: string) => invoke<void>("native_files_control", {transferId, action});
export const nativeFilesSave = (reference: unknown) => invoke<any>("native_files_save", {reference});
export interface BriefExportReference {
  workspace_id: string; snapshot_id: string; format: "markdown" | "html" | "svg"; locale: "en" | "zh-TW";
  expected_view_digest: string; expected_output_digest: string;
}
export interface BriefExportReceipt {
  stage: "saved"; workspace_id: string; snapshot_id: string; format: "markdown" | "html" | "svg";
  locale: "en" | "zh-TW"; view_digest: string; output_digest: string; byte_length: number; display_name: string;
}
export const nativeBriefSave = (reference: BriefExportReference) =>
  invoke<BriefExportReceipt | null>("native_brief_save", {reference});
export const nativeFilesPreview = (reference: unknown) => invoke<any>("native_files_preview", {reference});
export const nativeSelectRole = (input: {role: StartupRole; expected_generation: number}) =>
  invoke<NativeStatus>("managed_select_role", {input});
export const nativeConnect = () => invoke<any>("connector_connect");
export const managedControl = (input: {action: "status" | "open_browser" | "stop" | "start"} | {action: "set_login"; enabled: boolean}) =>
  invoke<any>("managed_control", {input});
export const nativeDisconnect = () => invoke<void>("connector_disconnect");
const nativeDialogLocale = () => locale === "zh-TW" ? "zh-TW" : "en-US";
export const nativeEnroll = () => invoke<any | null>("connector_enroll", {
  locale: nativeDialogLocale()
});
export const nativeReloadConfiguration = () => invoke<NativeStatus>("connector_reload_configuration");
export const nativeSetupConfiguration = (config: {endpoint: string; expected_actor: string; contract_version: string}) =>
  invoke<NativeStatus | null>("connector_setup_configuration", {config,
    locale: nativeDialogLocale()});
export const nativeForgetCredential = () => invoke<void>("connector_forget_credential");
export const openExternal = (url: string) => invoke<void>("open_external", { url });
export const fleetAvailability = () => invoke<{configured: boolean; platform_supported: boolean; native_controls?: boolean; bootstrap_controls?: boolean; error?: string}>("fleet_availability");
export type FleetRequest = {action: "status" | "contract" | "validate_configuration"}
  | {action: "set_connections"; connections: string[]; expected_configuration_binding: string; expected_selection_revision: string; expected_monitor_epoch: string | null}
  | {action: "ensure_monitor"; expected_configuration_binding: string}
  | {action: "quit_owned"; expected_configuration_binding: string; expected_monitor_epoch: string};
export type FleetControlRequest = {action: "overview" | "preview_launch"}
  | {action: "preview_profile"; profile_id: string}
  | {action: "preview_choices"; choices: {connections: string[]; profiles: string[]; dashboard: boolean}; configuration_binding: string; selection_revision: string; monitor_epoch: string | null}
  | {action: "apply_choices" | "launch" | "discard"; preview_id: string}
  | {action: "launch_status"; launch_id: string}
  | {action: "preview_migration"; backend: "rust" | "powershell"; autostart: boolean}
  | {action: "apply_migration"; preview_id: string; fingerprint: string}
  | {action: "advance_migration" | "migration_status"; migration_id: string}
  | {action: "restore_migration"; source_id: string; restore_id: string}
  | {action: "save_login"; expected_revision: string; show_picker: boolean};
export type FleetBootstrapRequest = {action: "overview"}
  | {action: "prepare"; recipe_binding: string}
  | {action: "advance"; recipe_binding: string; request_id: string}
  | {action: "receipt"; request_id: string};
export const fleetBootstrap = (input: FleetBootstrapRequest) => invoke<any>("fleet_bootstrap", {input});
export type TailscaleRequest = {action: "status"} | {action: "open" | "receipt"; request_id: string};
export const tailscaleControl = (input: TailscaleRequest) => invoke<any>("tailscale_control", {input});
export const fleetControl = (input: FleetControlRequest) => invoke<any>("fleet_control", {input});
export const fleetRequest = (input: FleetRequest) => invoke<any>("fleet_request", {input});
export type UpdateRequest = {action: "status" | "check"} | {action: "download" | "install"; candidate_id: string};
export const updateRequest = (input: UpdateRequest) => invoke<any>("desktop_update", {input});

export async function connectorRequest(method: string, path: string, body: unknown, key: string | undefined,
  browserToken: string, signal?: AbortSignal): Promise<ConnectorResponse> {
  if (nativeDesktop) {
    return invoke<ConnectorResponse>("connector_request", { input: {
      method, path, body: body ?? null, idempotency_key: key ?? null
    } });
  }
  const headers = browserAuthHeaders(browserToken);
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  const res = await fetch(`/api/v1${path}`, { method, headers, credentials: "same-origin", redirect: "error",
    body: body === undefined ? undefined : JSON.stringify(body), signal });
  if (browserToken !== browserSessionToken) browserCredentialRefusals.refuse(browserToken, res.status, method, path);
  return { status: res.status, data: await res.json().catch(() => ({})) };
}

export async function connectorUploadArtifact(operationId: string, bytes: ArrayBuffer,
  browserToken: string): Promise<ConnectorResponse> {
  if (nativeDesktop) throw new Error("Native uploads require the native file manager.");
  if (!/^op_[0-9a-f]{32}$/.test(operationId)) throw new Error("Invalid artifact upload operation ID");
  // Ignore content_url from operation metadata. Tokens only reach this same-origin fixed route.
  const res = await fetch(`/api/v1/artifacts/uploads/${operationId}/content`, {method: "POST", redirect: "error",
    credentials: "same-origin", headers: {...browserAuthHeaders(browserToken), "Content-Type": "application/octet-stream"}, body: bytes});
  if (browserToken !== browserSessionToken) browserCredentialRefusals.refuse(browserToken, res.status, 'POST', `/artifacts/uploads/${operationId}/content`);
  return {status: res.status, data: await res.json().catch(() => ({}))};
}
