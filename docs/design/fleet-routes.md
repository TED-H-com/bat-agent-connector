# Native Fleet route selection and recovery

This ports the local route policy from reviewed Kit `bat-servers.ps1`
(`Test-TailscaleDirect`, `Test-TcpPort`, `Get-ServerAlias`) and `bat-connect.ps1`
(`Select-Alias`, `Register-TunnelExit`, `Ensure-Tunnel`). It does not spawn SSH,
change central tasks, declare BAT ready, or query installed/live Tailscale in tests.

Routes come only from the validated inventory. By default, Direct is eligible only when the
configured Tailscale IP occurs in exactly one status peer with literal `Online: true`
and a nonempty string `CurAddr`, followed by a successful bounded TCP connection to
the configured probe endpoint. DERP relay information or a fast TCP connection alone
is insufficient without the opt-in below. LAN needs a successful TCP probe. Cloudflare remains a configured
fallback; selection does not prove that its future tunnel or BAT protocol is ready.
The Connector's single configured SSH alias is an explicitly `configured` route;
no alias means no tunnel route, rather than inventing one.

Inventory hosts and individual `routes` entries accept optional policy fields:

- `allow_tailscale_relay`: boolean, default `false`. A route override takes precedence
  over its host, including explicit `false`. With `true`, Direct also accepts a unique
  peer with literal `Online: true`, `Active: true`, a nonempty string `Relay`, and absent
  or empty-string `CurAddr`. A successful TCP probe is still required. Unknown,
  ambiguous, offline or malformed evidence never grants relay eligibility.
- `direct_probe_ms`: integer **1–5000**, overriding the host and then the required
  global value. It bounds each status/TCP call (including LAN TCP); the total native
  selection deadline remains two seconds and may shorten a call's budget. A home LAN can use **1500** ms while its Tailscale route uses **600** ms.

There is no separate location-profile table. A host's ordered route entries provide
route-specific overrides. Unknown/duplicate keys, nulls, wrong types, fractional or
out-of-range budgets refuse through the existing strict inventory parser. These keys
belong in `client/fleet-inventory.json`, not the native `fleet.json` installation pointer
or `central.json` API identity configuration.

Home hosts can intentionally use a dual-WAN load-balancing router. Public addresses
and ports changing between connections are an expected home condition.
Selection prefers LAN, then Tailscale direct, then an opted-in Tailscale relay,
then configured Cloudflare. LAN has its own looser budget so a loaded host can
finish its probe before a relay is selected. Route budgets override host budgets,
which override the global budget.

For example, retain the other required host fields and configure:

```json
{
  "direct_probe_ms": 600,
  "routes": [
    {"kind": "lan", "alias": "bat-home-lan", "probe": "192.0.2.10:22",
     "direct_probe_ms": 1500},
    {"kind": "direct", "alias": "bat-home-direct", "probe": "100.64.0.10:22",
     "allow_tailscale_relay": true, "direct_probe_ms": 600},
    {"kind": "cf", "alias": "bat-home-cf", "access_host": "home.example.invalid"}
  ]
}
```

Also include `home.example.invalid` in `cloudflare_access_hosts`.
繁體中文：家中刻意使用雙 WAN 負載平衡時，每次連線的公開位址及連接埠改變是正常情況。
優先使用 LAN（例如 1500 毫秒），其次為 Tailscale 直連、明確允許的中繼，最後為已設定的
Cloudflare。路線預算優先於主機及全域預算；LAN 探測完成前不會選用中繼。

If status identifies exactly one peer at the configured tailnet IP but direct proof
is missing, selection reserves a wake and returns without waiting for it. The native
supervisor starts an owned background `tailscale ping --c 3 --timeout 2s <IP>` sequence
with a separate five-second absolute deadline, independent of `direct_probe_ms` and
the two-second selection window. MagicDNS names are never used. Unknown, ambiguous
or malformed status never authorizes wake traffic. Ping success alone never authorizes a route. Without relay opt-in, DERP success
alone never authorizes a direct route: completion notifies the supervisor to re-run selection
immediately, using fresh status and TCP evidence instead of waiting fifteen seconds.

The per-host policy retains one wake reservation/task across supervisor cycles, and
an exponential cooldown after each sequence: 5, 10, 20, 40 and at most 60 seconds.
A direct observation resets the failure exponent. Selection/configuration generation
changes and supervisor shutdown abort the wake; shutdown joins it. Policy drop also
aborts it. Cancellation preserves the charged cooldown and stale selection workers
cannot queue a wake after invalidation. No policy lock is held across a probe await,
and independent hosts do not share a serial wake queue. The shared native adapter
coalesces simultaneous aliases for the same IP into one ping process; each alias
waits for completion and then checks its own fresh status/TCP evidence. Pending/reserved and running
wakes publish `TAILSCALE_WAKING`; wake transitions interrupt the regular one-second
native idle wait. Final unavailability remains `ROUTE_UNAVAILABLE`.

Two intentional corrections to historical Kit behavior are part of this port:

- Direct-only hosts and no-Cloudflare fallback require direct proof or explicit
  relay opt-in with fresh relay and TCP evidence.
  No eligible route returns `ROUTE_UNAVAILABLE`.
- Fast-failure demotion only ranks routes whose eligibility was checked. A demoted
  preferred route cannot cause an unproven direct/LAN tier to be selected.

Within eligible routes, preference remains direct, LAN, then Cloudflare. Skip aliases
blocked by fast failures when another eligible route is available. If every eligible
route is blocked, retain the best eligible route, as in the Kit's last-resort policy.
Three observed failures lasting less than 15 seconds demote that alias for ten minutes
and reset its fast-failure counter. A run lasting at least 15 seconds clears its counter.
Deliberate stops do not count as failures. Exact active run identity makes repeated or
stale exit reports no-ops. The caller must prove the process exited; unknown ownership
or uncertain stop results are not exit evidence.

Recovery reuses the core's existing bounded schedule: wait five seconds after failure,
then at most three recovery attempts with 15/45/45-second next-attempt spacing. A
positively observed owned tunnel alive for at least 60 seconds resets recovery. This
does not clear a ten-minute alias demotion early. Initial launch, explicit selection
changes and configuration-generation changes remain supervisor decisions; this module
does not silently reconnect an intentionally disconnected host.

A selection captures configuration/selection/epoch/generation plus the per-host policy
revision and a random policy-instance identity. `SelectionRequest::new` freezes an owned,
immutable input for an independent background worker; it does not borrow or clone mutable
policy. Its native alias accessor refuses changed state, future timestamps and evidence
older than 60 seconds. Configuration must still be revalidated by the supervisor before
passing the alias to `tunnel::Plan`; that planner independently accepts only a configured
alias. An active run prevents selecting or consuming another recovery attempt.
`update_selection_generation(expected, next)` is a compare-and-set for preference/probe
changes within the same monitor epoch and configuration binding; the generation must
increase. It invalidates pending choices but retains the exact active `Run`, recovery
budget, fast-failure count and demotion deadlines. Source/credential/configuration changes
require a new policy after the old owned process is stopped. A no-op identical generation
preserves choices; stale writers and backwards generation changes refuse.

At launch, the supervisor checks current configuration/selection and choice, consumes a due
recovery attempt, then records `started` only for a process bound to a retained handle.
A positively proven launch failure with no remaining child uses `launch_failed`; uncertain
launch/stop evidence must not be counted as an exit or trigger another launch.
No alias, endpoint, Tailscale payload or process output is serialized as status.

The injectable probe boundary has no global lock or serial host queue. Independent host
selection futures can run concurrently. Direct and LAN checks share a finite total
selection deadline; each TCP or local status call is also bounded by the inventory's
probe budget. Wakes have a separate five-second limit. Cancelling a selection drops
its TCP sockets and status child; cancelling the policy-owned wake drops its ping
child. Termination uses only the retained process object, never a PID lookup or
process-tree search.
The native status adapter uses a fixed absolute, canonical trusted Tailscale executable
with fixed `status --json` or IP-only ping arguments, cleared environment, null
stdin/stderr and bounded status stdout (ping stdout is discarded). Windows retains
only SystemRoot/WINDIR and uses CREATE_NO_WINDOW for both commands.
It never searches PATH or accepts a command from the WebView. Runtime installation-path
trust and configuration revalidation remain prerequisites; path validation is not a
binary-signature attestation or an atomic defense against an installer replacing files.

Tests use synthetic inventory/status documents, injectable reachability and temporary
loopback listeners. A tiny independently compiled executable double exercises argv,
environment, output, timeout and cancellation contracts; it does not call Tailscale or
SSH. Linux/Windows CI fixtures do not establish installed routing, Cloudflare, account
login, cross-login ownership, or packaged Fleet acceptance.

## Connector client recovery

The Python sidecar uses `bat-remote/v2` auth plus request correlation; this repository
contains no BAT `clientResume` RPC or BAT desktop attach implementation. Browser/Tauri
views use Connector HTTP/events and share the sidecar rather than owning BAT sockets.
An upstream BAT desktop `clientResume` implementation must be configured or fixed in
BAT itself; these changes do not modify that external application.

In `hosts.toml`, `[client]` accepts `connect_timeout_s` (default 30),
`invoke_timeout_s` (default 60), and `reconnect_grace_s` (default 120). Each host can
provide the same keys to override those defaults. Values must be finite numbers in
**1–600 seconds**; strings, booleans and nulls refuse. The connect setting bounds the
WebSocket opening and authentication waits; the grace bounds the complete recovery.
The invoke setting is a minimum for channel-specific defaults, so longer existing
channel deadlines remain intact. An explicit call timeout takes precedence and bounds
both sending and waiting for the reply. See [hosts.example.toml](../../examples/hosts.example.toml).

Transient connection/auth-transport failures retry within the original outage's grace
window. Full jitter chooses uniformly from zero to the exponential ceiling: 1, 2, 4,
8, 16, 32, then 60 seconds. Short reconnects retain failure history; a connection lasting
60 seconds resets it. Authentication refusal, pin mismatch and unavailable credentials
fail immediately. Explicit close cancels recovery and closes local subscriptions.

A single failed recovery attempt retains the same event subscriptions and queues.
They close only when recovery expires or a definitive trust/auth/configuration error
occurs. Transport loss still fails in-flight requests immediately: uncertain writes
retain their existing reconciliation and final-frame gates, and receive no new replay
permission. Recovery retains only existing subscriptions and does not enumerate or rehydrate sessions.
Each subscription retains a bounded LRU of 2048 recent event/message IDs across reconnects
to suppress replay duplicates; events without IDs remain deliverable. Events during a
gap still require upstream replay support. No remote session is stopped or recreated by this recovery.

繁體中文：`[client]` 與各主機可設定連線、呼叫與復原寬限秒數；預設分別為
30、60、120 秒，合法範圍為 1–600。暫時斷線採含隨機延遲的指數退避，
單次復原失敗會保留訂閱，直到寬限期結束或信任／驗證確定失敗。
復原保留現有訂閱，不會自動列出或重建所有 session。每個訂閱以最多 2048 個事件／訊息 ID
去除重播；缺少 ID 的事件照常傳遞。不會重送結果不明的寫入，也不會停止遠端 session。
