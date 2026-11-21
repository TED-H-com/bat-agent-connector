# Session registration locations

Status: implementation design, 2026-11-21. No route, tool or Dashboard panel
described below is implemented by this document. Tracking: [#545](https://github.com/TED-H-com/bat-agent-connector/issues/545),
P1 session-location reads and P2 presentation; E05 BAT reads and E10 API parity. This does
not close P1/P2, E00–E11, R0–R5, A01–A40 or the remaining PM v1.2 work.

The session detail view will let a reader explicitly check where BAT recorded
that session in the connection's configured profile. It will distinguish a
registration in memory, a saved registration, multiple registrations, and no
registration found. The result does not establish that a window is open, an
agent is running, or the Connector may control that session.

繁體中文：會話詳情可手動「查詢註冊位置」，區分記憶中的註冊、已儲存的註冊、
多個位置與查無註冊。查詢限目前連線設定的 BAT profile；結果不代表視窗仍開著、
Agent 正在執行，也不會取得人工會話的控制權。本頁是待實作設計。

## Existing behavior and source of truth

The wire contract is the fork's
[`bac-host-contracts/CONTRACT.md`](https://github.com/TED-H-com/better-agent-terminal/blob/dc023ff2f7305657589d8aeee8fa70c036730afa/src-tauri/crates/bac-host-contracts/CONTRACT.md),
at fork main after #25 (upstream baseline v3.2.18). Connector's
[host contract](host-contracts.md#workspace-read-and-session-location) and
`BatClient.bac_session_locate` already validate the reply against the negotiated
host identity, exact session ID, disposition, revisions and advertised limits.
No product caller currently uses that method.

`service._host_sessions` discovers sessions through `workspace:load` and the
Connector's creation registry. `Inventory` retains that observation and derives
`has_tab`, `state.tab`, workspace grouping and freshness from it. Those fields
have existing consumers beyond presentation, including resource policy and
recovery. A locate lookup must not overwrite them or redirect a Work binding.

The host scans its registry and local saved profiles sequentially. It may
return several windows and profiles for one terminal ID. A regular live row
suppresses its matching saved row; a detached window suppresses no saved row.
The host owns sorting and deduplication. `source: live` names the in-memory
source, not runtime liveness. `profileRevision` describes the document that a
workspace read would select, which can differ from the window in the row.
Neither revision is a focus command, ownership proof or mutation precondition
for a different operation.

## Scope and authority

Introduce an explicit central read for an **exact, already observed**
`(host, session_id)`. The configured host must still exist and the exact row
must exist in `sessions_observed`; prefixes, path guesses and registry-only
synthetic targets do not select a session. Retained/gone observations may be
queried: their existence is an identity anchor, not proof of current presence.

The caller needs `observe` and current source-lineage visibility through
`pm_source_lineage.require_session`. Manual and unknown sessions stay read-only
and may be inspected on the same basis as other visible sessions. Admin status
does not bypass source visibility. No `manage`, `operate`, write grant,
confirmation, operation ID or idempotency key is required for this read.

Before connecting, bind the configured profile ID, client/configuration
generation, principal and exact session row. Reject unauthorized or unknown
targets before a host frame. While waiting, recheck credentials at the existing
observation reauthorization interval. Before returning, revalidate the
credential, `observe`, source visibility, configured
host/profile/client identity and the exact row. Follow the cancellable
authorization pattern in `session_observation.authorizer`; repeat lineage
checks in a fresh read-cache scope so revocation is not masked by memoization.
A changed target is discarded; do not silently query a replacement connection.

Use the inventory's read-only Fleet and existing client negotiation. Do not
replace the host-read client with a write-enabled Fleet or invoke `bac:*`
through the generic channel path. A complete read has a 30-second deadline,
including connection, hello and the client's bounded reconnect/busy attempts;
the central caller transport allows a five-second margin. Cancellation joins
only this lookup; it does not close the shared inventory Fleet or cancel other
observations. Reuse the observation concurrency limits (32 total, four per actor,
eight per host) across observation and placement reads, rather than allocating
another independent budget. The UI allows one request at a time and does not
poll or auto-retry.

Select failure reasons from typed errors and the negotiated connection state,
never by parsing exception messages or host diagnostic text.

Only rows whose `profileId` exactly equals the bound configured profile are
returned. Recompute the disposition from those rows; it describes this scope
only. Do not expose other profiles, their count, the host-wide disposition,
`hostId`, `locationRevision`, `profileRevision`, raw host messages or diagnostic
`details.profileIds`. The host still performs its bounded scan: a failure in
another profile is unavailable, not partial success or proof of no location.
Changing the configured profile requires the existing connection workflow.

## Central read and adapters

The implementation will add one service function, shared by these adapters:

| Entry | Request |
| --- | --- |
| HTTP | `GET /api/v1/sessions/{host}/{session_id}/placement`, no query or body |
| Daemon RPC | `session_placement_get`, exactly `{host, session_id}` |
| MCP, including principal-only and read-only profiles | `session_placement_get(host, session_id)`, forwarding the caller's credential |

The MCP adapter uses the central path; it cannot fall back to direct BAT or a
local admin token. No new BAT protocol channel is needed. Central capabilities
will advertise `features.session_placement = {version: 1, scope:
"configured_profile", focus: false}` only when the central read and adapters
exist. This advertises a central reader, not that every host supports locate.
The browser and native transport use the same HTTP contract. Native validation
allows only the exact GET route, two encoded target segments, and no query/body;
encoded separators, traversal and extra segments remain refused.

The version 1 response has these members:

| Member | Meaning |
| --- | --- |
| `version`, `host`, `session_id`, `profile_id` | Version 1 and exact bound target |
| `scope` | Always `configured_profile` |
| `status` | `available`, `unsupported`, `unaddressable` or `unavailable` |
| `reason` | Fixed central reason below, or null on success |
| `observed_at` | UTC response-completion timestamp on success; null otherwise |
| `consistency`, `atomic` | `sequential` and false on success; null otherwise |
| `disposition` | `unregistered` for zero visible rows, `located` for one, `ambiguous` for more; null without a successful read |
| `locations` | Array of `{profile_id, workspace_id, terminal_id, window_id, source}` on success; null otherwise |

Rows retain the host's order and identities without normalization or additional
deduplication. A nullable window ID stays null. Display values are text, never
HTML, filesystem paths to open, routes to follow or locally executable commands.
Do not join workspace names by ID across profiles. Show IDs in the initial panel;
names can be added later with an independently checked profile-scoped source.

| Observation | Response |
| --- | --- |
| Valid locate result, after profile filtering | `available`; null reason; an empty array is a positive scoped observation |
| Known exact session/profile cannot be addressed by the BAC grammar | `unaddressable`, `identifier_not_supported`; no connection attempt |
| Auth lacks BAC or advertises an unsupported BAC version | `unsupported`, `host_contract_unsupported`; no locate frame |
| Negotiated hello disables `sessionLocate` | `unsupported`, `session_locate_unsupported`; no locate frame |
| Hello refused, malformed, or host identity mismatch | `unavailable`, `negotiation_unavailable` |
| Host error envelope, including scan/size errors | `unavailable`, `host_read_failed` |
| Malformed locate reply, including wrong identity or terminal ID | `unavailable`, `invalid_reply` |
| Overall deadline or read timeout | `unavailable`, `timeout` |
| Transport failure after existing bounded client handling | `unavailable`, `connection_failed` |

Central target fields must be nonempty strings of at most 512 characters with
no control characters; do not trim or resolve a prefix. Syntax-invalid central
requests return 422; unknown hosts or unobserved exact sessions return 404;
unauthenticated requests return 401; missing `observe` or a credential revoked
while the read waits returns 403, following the existing observation adapter.
Hidden lineage retains the existing 404 behavior. Authorization
failure after the host reply takes precedence over every data response. A
changed configuration or removed exact row returns a fixed 409
`PLACEMENT_TARGET_CHANGED`, without host data. Existing observation busy-limit
refusals apply before another lookup is admitted. Unexpected programming errors
remain errors, not successful empty results.

No outcome falls back to `workspace:load`, chooses the first global match,
starts an agent, focuses/opens a window, writes a workspace or alters a durable
operation. Existing session listing, policy, recovery and write paths retain
their behavior on both fork and upstream hosts.

## Dashboard behavior

Add a collapsed registration-location section to the shared session details
surface, next to the existing session evidence. Its explicit check button
starts the read; expanding alone does not. Do not add host-wide locate calls
to inventory polling, navigation, list rendering or session refresh.

Use these English / Traditional Chinese meanings:

| English | 繁體中文 |
| --- | --- |
| Check registration locations | 查詢註冊位置 |
| Registration locations in this profile | 此連線設定中的註冊位置 |
| In-memory registration | 記憶中的註冊 |
| Saved registration | 已儲存的註冊 |
| Multiple registrations; no location selected | 找到多個註冊位置；尚未選定位置 |
| No registration found in this profile | 此連線設定中查無註冊 |
| This host does not support location lookup | 此主機不支援位置查詢 |
| This identifier cannot be used for location lookup | 此識別碼無法用於位置查詢 |
| Locations could not be checked | 無法查詢註冊位置 |

Show the configured profile, observation time and a short explanation that this
is a sequential registration read, not a current window or execution status.
All matches are presented with equal weight, including an ambiguous result;
there is no default selection or focus button. The existing “Open in BAT”
profile-launch workflow and its saved intent remain independent.

Keep the result only in component memory. Starting another lookup clears the
old result; a failure displays the reason, not old rows as current data. Closing
the section, leaving the session, withdrawing parent access, switching
account/connection or disposing the view clears rows and invalidates pending
responses. Use the existing view guard plus a request generation; an old reply
cannot repaint a reopened panel. Reopening requires another explicit check.
No localStorage, draft, journal/event, schema or data migration is added.

## Implementation and verification sequence

1. Add the central reader and shared concurrency/authorization integration,
   HTTP/RPC/MCP adapters and capability declaration. Validate exact target
   existence separately from BAC ID grammar. Test with mock hosts only.
2. Add the exact native route and shared bilingual panel. Regenerate browser
   assets from `desktop/src`; update canonical agent documentation and generated
   adapters when the read is available.
3. Verify the following matrix, then update implementation status with actual
   branch, merge, release, installation and live-test evidence separately.

| Boundary | Required evidence |
| --- | --- |
| Scope | Same-profile persisted/live/detached rows; zero/one/many rows; a foreign-profile row disappears without leaking counts, revisions or error details |
| Identity | Exact observed manual/managed/unknown/gone sessions; unobserved IDs and prefixes refused before frames; invalid BAC grammar gives unaddressable |
| Authorization | HTTP bearer, managed-browser credential and MCP caller; no observe; hidden lineage; revocation, row deletion and profile/client replacement while the reply is delayed |
| Negotiation | Upstream and unsupported hosts emit no locate frame; disabled feature; failed hello; reconnect identity mismatch; malformed reply and negotiated row limits |
| Failure | Host-wide scan failure even if the configured profile could be read; timeout; disconnect; cancellation; shared concurrency limits; no fallback or write frame |
| Isolation | Inventory body, grouping, policy, journal, original operation keys and existing BAT launch intent unchanged by every read outcome |
| UI | English/zh-TW; HTTP/injected IPC; 390px; keyboard access; no automatic query; multiple locations; late replies after close/reopen, withdrawal and connection switches |
| Native | Only the exact GET path; query/body, malformed encoding, traversal and other methods rejected |

Existing `tests/test_bac_reads.py` and `tests/test_bac_workspace_values.py`
cover the underlying transport/parser, not these proposed central and UI
behaviors. Their passing results cannot stand in for the new matrix.
