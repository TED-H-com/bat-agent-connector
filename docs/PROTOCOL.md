# BAT remote protocol notes (`bat-remote/v2`)

Read from the MIT-licensed upstream source of [Better Agent Terminal](https://github.com/tony1223/better-agent-terminal)
(tag **v3.2.12**: `src-tauri/src/remote_server.rs`, `remote_core.rs`, `node-sidecar/src/handlers/*`) and checked
read-only against headless `bat-server` 3.2.12 hosts. Unofficial; may change with BAT releases.

> BAT is a **Tauri/Rust** app. The remote server is Rust (tungstenite + rustls). Claude sessions run in a Node
> **sidecar**; Codex sessions run in a Rust-owned **codex app-server** runtime. Headless hosts run the standalone
> `bat-server` bundle.


## 1. Transport and handshake

| Step | Detail |
|---|---|
| TCP | Client dials the host's `bat-server` (default port `9876`), directly or through an ssh `-L` tunnel. |
| TLS | Self-signed cert. **Pin the SHA-256 DER fingerprint** (`remoteFingerprint` in `profiles/index.json`). Skip CA and hostname checks and compare the fingerprint yourself. The connector refuses to continue on a mismatch, before the token is sent. |
| WS upgrade | Plain RFC 6455 `GET /` with `Upgrade`, `Connection: Upgrade`, `Sec-WebSocket-Version: 13`, `Sec-WebSocket-Key`. The server **ignores** `Authorization:` and `Sec-WebSocket-Protocol:`, so auth happens only in the first frame. |
| Deadlines | TLS + upgrade must finish in 10 s. The `auth` frame must arrive within 10 s after that, or the socket closes. |

### Auth frame (client → server, first text frame)
```json
{ "type": "auth", "id": "auth-1", "token": "<TOKEN>",
  "protocols": ["bat-remote/v2", "bat-remote/legacy-v1"],
  "compression": ["none"],
  "clientInfo": { "appName": "bat-agent-connector", "appVersion": "0.1.0",
                  "label": "BAT Agent Connector", "deviceId": "<STABLE-UUID>",
                  "platform": "linux" } }
```
* `protocols`: if you omit it, the server silently negotiates **legacy-v1** (positional `args`). The connector
  only sends `bat-remote/v2` and refuses a downgrade.
* `compression: ["gzip"]` switches every later frame, in both directions, to **binary** frames
  `b"BATGZIP1\0" + gzip(json)`. Limit is 64 MiB after inflation. Use `none` for simplicity.
* `clientInfo.deviceId`: the host posts a **"remote client connected" notification** the first time it
  sees a deviceId. Without a deviceId it dedups in memory on (windowId, label). **Always send one stable
  deviceId** or every reconnect spams the host UI.
* Token check is an exact string compare against the host's current token. A wrong token gets an
  `auth-result` error and then a close.

### Auth result
```json
{ "type":"auth-result","id":"auth-1","result":true,"protocol":"bat-remote/v2",
  "compression":"none","serverVersion":"3.2.12",
  "capabilities":{"profileContext":1,"remoteAuth":{"claude":"paste-code-v1","codex":"device-code-v1"}} }
```
Errors: `{"type":"auth-result","id":…,"error":"Invalid token" | "Unsupported remote protocol"}`.
Use `serverVersion` for version detection. `app:get-version` returns the same value.

## 2. Frame types (after auth)

| Direction | Frame |
|---|---|
| C→S | `{"type":"ping","id":X}` → S→C `{"type":"pong","id":X}` (app-level RTT probe) |
| C→S | `{"type":"invoke","id":"<unique>","channel":"<ns:verb>","params":{…}}`. v2 uses a named `params` object. Legacy-v1 uses positional `"args":[…]`. If `params` is missing, the server maps `args` → params with a per-channel key list (`legacy_v1_param_keys`). A single-object `args:[{…}]` is also accepted as params. Optional `"contextId"` routes the call through a profile context (§7). |
| S→C | `{"type":"invoke-result","id":…,"result":<any>}` or `{"type":"invoke-error","id":…,"error":"<string>"}`. Also `"Remote server is busy; retry this request shortly"` when the host is over its invoke cap. |
| S→C | `{"type":"event","channel":"agent:stream","params":{…},"args":[…]}`: broadcast to **every** authenticated client. Server-side `claude:*` channels are renamed `agent:*` on the wire. |

Channel aliasing: the client may send `agent:<verb>` or `claude:<verb>`. The server folds `agent:` → `claude:`
except for `agent:list-presets`, `agent:get-supported-session-types`, `agent:usage`,
`agent:usage-snapshot` and `agent:latency-samples`. Channels without a Rust handler fall through to the
Node sidecar as `ns.camelCase` (e.g. `claude:interrupt-turn` → `claude.interruptTurn`).

### Limits (server constants)
64 concurrent connections · 128 concurrent invokes · per-client outbound queue of 256 frames. **A client
that stops reading events gets revoked and disconnected.** A long-lived connector must drain the socket
all the time (or connect per call). Default invoke timeout is 15 s. `start/resume/client-resume/send-message/fork`
and auth-login calls allow 300 s. `runtime:get-status` allows 30 s.

## 3. Data model

* **Profiles**: `profile:list` → `{profiles:[{id,name,type:"local"|"remote",…}], activeProfileIds:[…]}`.
  Every headless host has a single `default` profile.
* **Workspaces**: `workspace:load {profileId:"default"}` → a **JSON string**. Parse it to get:
  ```
  { activeWorkspaceId, activeTerminalId, activeGroup,
    workspaces: [{ id, name, folderPath, createdAt, focusedTerminalId }],
    terminals:  [{ id, workspaceId, title, type:"terminal", cwd, agentPreset?, sdkSessionId?,
                   model?, permissionMode?, agentParams?, historyKey?, sessionMeta? }] }
  ```
  * `agentPreset` ∈ `claude-code`, `codex-agent`, `codex-agent-worktree`, … (see `agent:list-presets`).
    If there's no `agentPreset`, the terminal is a plain PTY shell.
  * **An agent session's `sessionId` is the terminal `id`.** `sdkSessionId` is the underlying
    Claude/Codex conversation id, needed for resume.
* **Session liveness**: `claude:get-session-meta {sessionId}` returns an object when the host runtime has the
  session loaded and `null` when it's only persisted in workspace state. After a host restart, sessions are
  `null` until some client resumes them.
* **Meta** (cheap): `model, effort, permissionMode, cwd, sdkSessionId, contextTokens, contextWindow,
  numTurns, durationMs, lastTurnDurationMs, totalCost, isStreaming, runtimeStatus
  ("starting"|null), runtimeStatusStartedAt, lastDataAt (epoch ms of last output observed since the host process
  started; 3.2.11+; often absent for Claude sessions), codexSandboxMode,
  codexApprovalPolicy, …`.
* **State** (heavy, up to MBs): `claude:get-session-state {sessionId}` →
  `{ messages[≤300], isStreaming, streamingText, streamingThinking, pendingAskUser, pendingPermission,
     meta, model, effort, permissionMode, active, … }`. Messages look like `{id, sessionId, role, content,
  timestamp}` or tool items `{id, toolName, input, result, status, timestamp, completedAt, parentToolUseId}`.
  Only about the last 300 items stay in memory. Warning: for a Claude record with no `cwd` (a phantom), the
  sidecar **deletes** the record and returns `null`.
* **Archive** (paged, cheap): `claude:load-archived {sessionId, offset, limit}` reads the host's
  `message-archives/<sessionId>.jsonl`. **`offset` counts back from the newest line**, so `offset:0,limit:N` = the
  last N archived items. Returns `{messages, total, hasMore}`. The archive holds messages that clients
  **evicted** from memory (`claude:archive-messages`), so it trails live state. For "last activity", use
  the newest timestamp in get-session-state, and fall back to archive/meta.
* **History list**: `claude:list-sessions {cwd, agentKind}` lists the SDK conversation history for a folder
  (`sdkSessionId`, `timestamp` = transcript file mtime, `messageCount`). Cheap for Claude; for `agentKind:"codex"`
  the host scans every Codex rollout file, which can take minutes, so the connector does not use it for Codex.

## 4. Sending, continuing, interrupting (write operations)

| Intent | Channel / params | Semantics |
|---|---|---|
| Send / continue | `claude:send-message {sessionId, prompt, clientMessageId, images?, displayPrompt?, suppressUserEcho?, autoCompactWindow?}` | Resolves as soon as the turn is **accepted**: `{ok:true, accepted:true, queued:bool}`. If a turn is running, the message queues behind it. `clientMessageId` is **idempotent**: resending the same id returns the first result. The host emits an `agent:message` user echo (Claude) and then streams events. Codex sessions owned by the runtime are routed natively. |
| Ensure loaded (before send when meta is `null`) | `claude:client-resume {sessionId, sdkSessionId, options:{cwd, agentPreset, model, permissionMode, effort, workspaceId, workspaceName, codexSandboxMode, codexApprovalPolicy}}` | Rebuilds the runtime session from the persisted transcript. Codex presets route to the codex app-server. A send into an unloaded Claude session fails with a "session has no cwd" error. |
| Soft interrupt (1× Esc) | `claude:interrupt-turn {sessionId}` | Claude SDK: ends the current turn but keeps the subprocess and background subagents. Not intercepted for Codex, so use abort there. |
| Hard stop (2× Esc / `/abort`) | `claude:abort-session {sessionId}` | Claude: kills the running query loop, session stays. Codex: turn interrupt, session stays. |
| Stop a background task | `claude:stop-task {sessionId, taskId}` | Subagent/background task only. |
| Answer blocked prompts | `claude:resolve-ask-user {sessionId, toolUseId, answers}`, `claude:resolve-permission {sessionId, toolUseId, result}` | Unblocks `pendingAskUser` / `pendingPermission`. `result.dontAskAgain = true`: Codex → accept for the session (that command); Claude `ExitPlanMode` → acceptEdits. |
| Permission mode | `claude:set-permission-mode {sessionId, mode}` (Claude; returns `false` for Codex), `claude:set-codex-sandbox-mode {sessionId, mode}`, `claude:set-codex-approval-policy {sessionId, policy}` | The GUI's "allow bypass" default starts Claude with `permissionMode: bypassPermissions` and Codex with `codexSandboxMode: danger-full-access`, `codexApprovalPolicy: never`; omitted options retain BAT/CLI defaults and inherited approval rules. Raising a Claude query that was not launched with bypass fails in the SDK and the sidecar closes the live query, **ending a running turn**; switch only while idle. Codex re-applies via `thread/resume` on its next turn. |
| Unload a session | `claude:stop-session {sessionId}` | Unloads the runtime session; the transcript and tab stay and it can be resumed. The connector exposes it only in the orchestrate tier, used by `session_cleanup` on idle, finished sessions. |
| ⚠ Tear down | `claude:reset-session`, `claude:rest-session`, `pty:kill`, `worktree:remove/merge`, `fs:delete-path`, `settings:save`, `workspace:save`, `runtime:install`, `app:install-update`/`app:relaunch` (desktop hosts only) | **Never expose these** in a connector, except behind an explicit "admin" flag. |

## 5. Events (S→C, `type:"event"`)
`agent:message`, `agent:stream` (`{sessionId,data:{text|thinking}}`), `agent:status` (`{sessionId,meta}`),
`agent:tool-use`, `agent:tool-result`, `agent:result`, `agent:turn-end`, `agent:error`,
`agent:permission-request`, `agent:ask-user`, `agent:history`, `agent:usage`, `pty:output`, `pty:exit`,
`notification:update`, `sidecar:metric`, `workspace:reload`, `profile:changed`, `fs:changed`, `runtime:changed`.
Stream events are coalesced per session on the host. Filter by `params.sessionId`.

## 6. Channel catalog (v3.2.12) by risk

* **Read-only, safe**: `app:get-version`, `profile:list`, `profile:get-active-ids`, `workspace:load`,
  `claude:get-session-meta`, `claude:get-session-state`, `claude:load-archived`, `claude:list-sessions`,
  `claude:is-resting`, `claude:get-context-usage`, `claude:get-supported-models|efforts|commands`,
  `agent:list-presets`, `agent:get-supported-session-types`, `agent:usage-snapshot`, `agent:latency-samples`,
  `notification:list`, `pty:read-buffer {id}`, `pty:get-cwd`, `git:status|log|diff|branch`, `fs:readdir`,
  `fs:search`, `runtime:get-status`, `claude:auth-status`, `claude:account-list`, `codex:account-list`.
* **Session write (explicit opt-in)**: `claude:send-message`, `claude:client-resume`/`resume-session`/`start-session`,
  `claude:interrupt-turn`, `claude:abort-session`, `claude:stop-task`, `claude:resolve-ask-user`,
  `claude:resolve-permission`, `claude:set-model|effort|permission-mode`, `pty:write`.
* **Usage / quota**: BAT has no "quota exhausted" status. Claude's limit text arrives as an assistant message
  (e.g. "You've hit your … limit · … resets <time>"); the SDK `rate_limit_event` is forwarded as `claude:rate-limit`
  (`rateLimitType`, `resetsAt`, `utilization`) and host-wide usage as `agent:usage` / `agent:usage-snapshot`.
* **No push / tag / PR-create channel** exists in v3.2.12 (`github:pr-list|pr-view|pr-comment` only).
* **Destructive / admin (do not expose)**: see the ⚠ row in §4, plus `fs:upload-*`, `github:*-comment`,
  `claude:account-switch|remove`, auth-login flows, `snippet:*` writes, `notification:clear`.

## 7. Profile contexts (chained hosts, v3.2.12)
`profile:open {profileId}` → `{contextId,…}`. After that, send `invoke` frames with top-level `"contextId"` and
the host proxies them to a downstream remote profile. `profile:status {contextId}`, `profile:close {contextId}`.
v2 only. The connector does not use profile contexts; it dials each host directly.

## 8. Worktrees and orchestration

See [ORCHESTRATE.md](ORCHESTRATE.md).

### Managed execution limits (A10)

Pinned BAT source `b7419892fbc9946799b64cca24c2ec8c7fa15c42` (v3.2.12 notes above):
`node-sidecar/src/handlers/claude-permission.mjs` allows Write/Edit/NotebookEdit/Read/Glob/Grep under
`acceptEdits` without checking paths. ExitPlanMode allow with dontAskAgain also sets acceptEdits (ordinary allow
sets default). `claude-send.mjs` hardcodes `settingSources=['user','project','local']`; `claude-session.mjs` stores
permissionMode but cold/client-resume without remembered mode can fall back to bypassPermissions. A loaded send
preserves its live mode.

`src-tauri/src/remote_server.rs` routes Codex to `src-tauri/src/codex_app_server.rs`. Start/resume and setters accept
only `codexSandboxMode` and `codexApprovalPolicy`; the native turn policy has a sandbox type, with no connector
pass-through for writable roots or network access. Claude SDK sandbox/settings overrides are also unreachable.
See the [source table and limitations](design/confinement.md).

General managed starts preserve `default`/`allow_all`; optional `confined` uses Claude default (acceptEdits only with
a read-only verified host account), or Codex workspace-write/on-request. Permission modes do not prove OS enforcement;
Codex is at most options_confirmed until W12. Confined sessions reject raises, persistent approvals and mode-widening
ExitPlanMode answers. Cwd is not evidence of protection. A10 is not proven until the W12 live run.

## 9. Network and Fleet Internals

* **Websocket frame format:** The protocol uses JSON for message payloads. Frames can be sent as plain text frames or compressed binary frames. If compressed, the binary frame starts with `b"BATGZIP1\0"` followed by the `gzip(json)` payload.
* **Reconnect backoff strategies:**
  * **Websocket client:** Uses bounded per-subscriber event queues and a reconnect with backoff strategy.
  * **Task Push:** Failed milestone pushes retry with a capped exponential backoff. The max backoff is `300` seconds (`max_backoff_s = 300`).
  * **Inventory:** Failed requests use an exponential backoff. The max backoff is `600` seconds (`max_backoff_s = 600.0`).
* **Fleet heartbeat intervals:** The `TaskDaemon` worker writes a heartbeat timestamp to the `daemon_owner` table every 2 seconds (`asyncio.sleep(2)`) for visibility. However, actual mutual exclusion for the singleton worker relies on OS-level file locks (`fcntl.flock`), not on this heartbeat.
