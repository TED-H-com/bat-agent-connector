# W22-A: bounded AHP observation

Connector implements explicit read profiles for Agent Host Protocol 1.0.0 and
the VS Code 1.141.0 distribution's 0.10.0 wire contract. The upstream 1.0 profile is
pinned to `microsoft/agent-host-protocol` tag `spec/v1.0.0`, commit
`5f16d81bb7045b66d7bc768d244feab75002d943`. Primary contracts:
[transport](https://microsoft.github.io/agent-host-protocol/specification/transport.html),
[lifecycle and reconnect](https://microsoft.github.io/agent-host-protocol/specification/lifecycle.html),
[session channels](https://microsoft.github.io/agent-host-protocol/specification/session-channel.html),
and [VS Code local endpoint](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/LOCAL_ENDPOINT.md).
This is AHP, not ACP or MCP. Product names and a negotiated version do not prove
execution/tool restrictions. No live host version has been accepted by this slice.

## Administrator binding

The daemon's TOML supports up to 32 explicit adapters:

```toml
[ahp.adapters.editor]
workspace_ids = ["ws_11111111111111111111111111111111"]
endpoint = "ws://127.0.0.1:4567/"
credential_ref = "env:AHP_EDITOR_TOKEN"
credential_transport = "connection_token_query"
```

Each binding explicitly grants observe-scoped members of the named PM workspaces
read access to the configured endpoint's advertised catalog. List only workspaces
whose members may see that catalog. This is not a Git/BAT/Task ownership grant.
The server credential must itself permit the requested AHP reads. No automatic
endpoint discovery, host launch, provider sign-in or credential collection occurs.
`credential_ref` accepts an environment variable or an absolute private file;
inline tokens and BAT credential reuse are not supported. File resolution retains
Connector's private regular-file permissions and byte limit.

Only `wss://` with normal certificate verification, or `ws://` to a literal loopback
IP, is accepted. This registered observer requires an already running TCP endpoint;
the separate Linux local reader below does not accept TCP configuration URLs. URLs
cannot contain userinfo, query or fragment. The VS Code connection-token mode adds
`tkn` privately to the WebSocket upgrade query. `bearer_header` is an explicit
alternative for deployments using HTTP bearer authentication. AHP `authenticate`
is never called because it can resume blocked agent work. Redirects, system proxies,
compression, provider configs and insecure TLS overrides are disabled.

Normal configuration changes take effect on daemon restart. Installation calls
`sync_config` before serving reads, replacing the nonsecret current grant projection.
Any future hot-reload path must call the same synchronizer before serving requests.
The projection stores adapter/workspace IDs and a binding digest, never endpoint or
credential reference. Original frames and final publication independently compare
actual current in-memory configuration, not just this projection.

## Registered action and shared reads

`pm.ahp.observe` uses the established `observe` scope and original operation key:

```json
{
  "action": "pm.ahp.observe",
  "target": {"workspace_id": "ws_11111111111111111111111111111111", "adapter_id": "editor"},
  "params": {"session_uris": ["ahp-session:/original-native-id"], "include_changesets": true},
  "preconditions": {}
}
```

The caller can select only an administrator-configured adapter ID. Endpoint, token,
provider commands, arbitrary resource reads and ownership claims are rejected.
Select at most eight distinct original session URIs; each must be positively advertised by
the fresh catalog before a subscription is sent. Empty selection reads only the
catalog. The 1.0 profile accepts `ahp-session:` identities; the VS Code 0.10 profile
accepts provider session identities such as `copilotcli:/<native-id>` only when the
advertised provider matches that scheme. This syntax is not an ownership claim.
The pinned stable distribution's built-in IDs are `copilotcli`, `claude` and
`codex` (`common/agent.ts:305–311`, registered in `node/agentHostMain.ts`). They
produce lowercase schemes. This profile preserves exact opaque URI/provider
strings; it does not claim generic URI case-folding support or normalize a provider
ID into another identity.
Neither profile rewrites native identities into aliases. File/network resource
URIs cannot be selected as sessions. Changesets are at most sixteen exact static
URIs advertised by a selected session: the nested `<original-session>/changeset/<id>`
shape used by VS Code, plus 1.0 `ahp-changeset:` advertisements. Their source kind
and parent are fixed from the original advertisement, not inferred from a URI prefix.
Dynamic templates, content bytes, review mutations and
changeset operations are not implemented.

Every original application frame and the final local record check cancellation,
the original authenticated credential's current observe authority, current PM
membership, unchanged adapter binding, and original lifecycle revision/epoch.
A new explicitly requested capture is a record action and may start while paused
or closed. A lifecycle change during capture refuses its remaining frames and
publication. Historical reads and original successful receipt recovery remain
available after lifecycle changes under current source access. This never grants
execution permission or wakes, creates, resumes, sends to, interrupts or disposes
a host session. All observed manual/foreign sessions remain read-only.

The existing API, RPC, MCP and native bridge use these exact reads:

| HTTP suffix under `/api/v1/pm/workspaces/{workspace_id}` | RPC / MCP |
| --- | --- |
| `/ahp-adapters` | `pm_ahp_adapters_list` |
| `/ahp-observations?after=0&limit=20` | `pm_ahp_observations_list` |
| `/ahp-observations/current` | `pm_ahp_observation_current_get` |
| `/ahp-observations/{pmah_id}` | `pm_ahp_observation_get` |

GET never refreshes the host. History returns summaries, a signed-64-bit
`next_cursor`, and `visibility_filtered`; preserve cursors even for empty filtered
pages. Limit is 1–50. Detail returns the original immutable document and digest.
The complete public response is bounded to 2,000,000 UTF-8 bytes using the actual
HTTP JSON spacing. Before publishing a new receipt or source-access projection,
admission checks the larger current-read envelope, including all public metadata
and room for the maximum signed-64-bit SQLite sequence. An accepted document can
therefore be returned by both detail and current reads under that same bound.
Oversized captures fail without a new observation or access change; content is not
truncated and historical document bytes are not rewritten.
`features.pm_workspaces.ahp_observation` describes the implemented subset. Adapter
listing returns configured IDs and that contract without connection secrets.
There is no second daemon, queue, journal, model engine or new authority tier.

## Read protocol and bounds

The sole outbound application methods are `initialize`, `reconnect`, `listSessions`
and `subscribe`. WebSocket protocol close is transport cleanup only. Initialization
offers exactly `1.0.0` and `0.10.0` and subscribes only to the root. The returned
version must map to `ahp_1_0_observer_v1` or `vscode_0_10_observer_v1`; other versions
fail before catalog reads. Every receipt saves `negotiated_version` and
`decoder_profile` (null when negotiation never succeeded). `serverInfo` is retained
as informational metadata and never selects a decoder or proves host capabilities.
Reconnect retains the original version/profile and client ID; it cannot negotiate
a replacement version or fall back to a second initialize. Session catalog pagination
is connection-local; every reconnect refreshes the catalog without reusing cursors.
There is at most one reconnect using the original client ID, subscribed native URIs
and highest observed server sequence. RPC refusal does not invoke sign-in, session
creation, initialize fallback or any write. Replay deduplicates identical original
sequence/envelope digests and rejects conflicting identities. Sequences can have
gaps due to channel filtering; the observer never invents contiguous event history.
Snapshot fallback records an explicit gap, not a replay claim.

Reconnect snapshots, replay actions and actions interleaved while rereading the
catalog are staged within the original byte/frame bounds. They cannot become
receipts until that fresh catalog proves every previously subscribed session's
original URI, provider and native creation identity. A failed/empty catalog or
reused URI never attaches new metadata to the old identity. The capture preserves
already proved receipts and a `reconnect_source_unverified` gap, and marks
`reconnect_evidence` as `unverified`; source withdrawal still hides unavailable
original facts. A fully verified reconnect applies the staged receipts and marks
it `applied`. This does not substitute later data into an older saved document.

VS Code 1.141.0 ignores the `listSessions` limit/cursor hint and returns the whole
catalog. Its profile accepts one page up to the unchanged 200-session total limit;
an oversized result remains incomplete and establishes no absence. The 1.0 profile
retains its 50-item page bound. Both retain byte/frame/page limits and cursor-cycle
checks. Root catalog notifications invalidate completeness; reconnect rereads the
catalog because those notifications are not replayed.

Limits: 30 seconds total from the first durable start receipt (not reset by retry),
1 MiB per frame, 4 MiB total, 512 frames, eight catalog pages, 200 catalog sessions,
eight selected sessions, sixteen static changesets and 500 files per changeset.
At most sixteen observers run per service, two per actor and four per adapter.
Cancellation/deadline closes only this observer's socket. Exceptions and WebSocket
logging cannot expose the private credential URL. Display titles, labels,
working-directory text and informational server metadata redact both raw transport
tokens and the actual query-encoded token used for the upgrade. Native identities,
reference fields, digests, structural keys and enums are never rewritten: a token
collision there refuses publication with `AHP_PRIVATE_IDENTITY_REFUSED`, without
creating an observation or changing source access. This also applies to short
tokens; no altered URI can become positive native evidence. Original envelope
digests remain available in accepted captures.

Each snapshot retains original URI, sequence, envelope digest and receipt time,
plus selected metadata. Event receipts retain original channel, sequence, action
type and envelope digest; arbitrary action payloads are not copied or interpreted.
Provider configuration, customizations, authentication challenges, tool definitions,
transcripts and resource contents are omitted. Later observed events flag a snapshot
as dated; this slice does not pretend to implement the full native state reducer.
Catalog completeness means bounded pagination completed, not an atomic host snapshot,
whole-host coverage, all VS Code extension-host sessions, completion, cost or idleness.
Changeset status and diff counts are observations, not semantic acceptance evidence.

A disconnected read may reconnect because every callable method is read-only.
A recovered unfinished operation may repeat these bounded reads only inside its
original deadline. Successful local receipt plus immutable document commit in one
transaction; the same original key then returns that receipt without another socket.
No uncertain external write exists in this slice and no write resend is offered.

## Current original-source visibility

Workspace membership and the exact current adapter binding gate every read, global
operation and event. Current peer source evidence adds an independent gate. A complete
successful current catalog can mark absent original native resources unavailable;
an explicit reconnect `missing` or `root/sessionRemoved` can withdraw that original URI. Failed/truncated
catalogs and pre-reconnect cursors never establish absence. Positive evidence is
tracked separately from previously retained snapshots.

Session identity uses original URI, provider and native `createdAt`, not title or
working directory. Changeset identity includes its native URI and parent session
identity. Each retained snapshot and event metadata receipt keeps its own original
source references, even when reconnect fails before a fresh catalog is available.
SessionState need not carry the catalog-only timestamps: its provider and any
timestamp it does supply must match the original catalog identity. Reconnect may
not rebind retained content to a newer incarnation of the same URI. A reused URI
with a different native creation identity cannot revive the
old receipt. Restoring access requires positive native evidence for that same
original identity, including a fresh changeset snapshot when relevant. If any
original source is unavailable, its entire saved observation is hidden, including
counts, titles, operation results and events. Documents remain immutable; historical
GET performs no network calls. Missing-source meaning remains disposed-or-withdrawn,
never "finished". An endpoint grant does not bypass existing Connector source lineage;
this slice does not attach AHP resources to BAT/Task/PM writer ownership at all.

Every historical read, global operation/event projection and original-key lookup
checks that the saved document's reference list completely covers its own catalog,
snapshots and event receipts. Empty or partial references cannot orphan retained
facts, even if a later observation makes the native resources accessible again.
This check uses only the original document, before current source access is checked.

The mutable access projection stores explicit `source_kind` (`session` or
`changeset`). Complete catalogs withdraw all missing session identities by this
kind, including provider-scheme identities. Upgrade adds the column idempotently
and reconstructs old kinds from the original immutable catalogs and receipt parent
relations. It does not rewrite document bytes/digests, allocate a schema
`user_version`, identify resources by filesystem path, or increase an existing
row's `available` value. Unproven old projection rows remain unavailable.

Compatibility is explicit: pre-profile `7a56732` records already have per-receipt
`original_sources` and retain those fixed references. Earlier `40f609b` records
may be reconstructed only when they report a complete, observed 1.0 capture with
no reconnect, gap, missing source or catalog notification; every retained non-root
receipt must have its original session in that same saved catalog, and a saved
session snapshot must independently contain the identical provider and `createdAt`.
Changeset receipts also require the saved session's exact static advertisement and
saved changeset snapshot. Reconstructed references must exactly match the document's
original reference list. A timestamp-free early snapshot, inconsistent identity,
incomplete capture or orphaned reference fails closed. Later snapshots and current
catalogs cannot repair these historical proof gaps. The old bytes, IDs and digests
remain intact in both the restored and hidden cases; this is not a claim that all
legacy history can be made readable.

## Pinned wire evidence and validation boundary

The offline socket fixtures use synthetic data shaped from the following fixed
official sources. This is source-level compatibility evidence, not installation
or live-host acceptance; `tested_host_versions` remains empty and
`live_host_verified` remains false.

| Contract | Pinned source |
| --- | --- |
| Upstream 1.0.0 | `microsoft/agent-host-protocol`, `spec/v1.0.0`, `5f16d81bb7045b66d7bc768d244feab75002d943`; local research checkout `ahp-source-v1` |
| VS Code stable distribution | [1.141.0](https://github.com/microsoft/vscode/releases/tag/1.141.0), `2a59476c9bfcb90b3ddc372c36762471b7dfad1c`; [version registry](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/version/registry.ts#L19) advertises 0.10.0 |
| initialize, reconnect, subscribe, pagination envelopes | [common/commands.ts](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/common/commands.ts), interfaces at lines 151/228, 422/462, 481/539, 75/99 |
| Snapshot, ContentRef, FileEdit, ActionEnvelope | [common/state.ts](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/common/state.ts), [common/actions.ts](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/common/actions.ts) |
| listSessions, SessionSummary, SessionState | [channels-root/commands.ts](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/channels-root/commands.ts), [channels-session/state.ts](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/channels-session/state.ts) |
| root session notifications | [channels-root/notifications.ts](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/channels-root/notifications.ts) |
| Changeset catalogue, state and file records | [channels-changeset/state.ts](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/channels-changeset/state.ts) |
| Actual provider session URI, whole-catalog result, static nested changeset | [common/agent.ts:1137](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/agent.ts#L1137), [node/protocolServerHandler.ts:1725](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/node/protocolServerHandler.ts#L1725), [common/changesetUri.ts:417](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/changesetUri.ts#L417) |

These shared wire declarations match the upstream 1.0 read subset after removing
comments/imports. The 0.10 optional `SessionChatSummary.archived` field is not
retained, like other chat details. Declaration equality does not erase the actual
URI and pagination implementation differences above.

For later validation, fixed VS Code main source
`80101bda7057c6f2081cc64f510b6fa3cbf2de5b` (package version 1.142.0) advertises
1.1.0 with a 1.0.0 compatibility baseline and an explicit 0.10 shim in
[agentHostProtocolCompatibility.ts](https://github.com/microsoft/vscode/blob/80101bda7057c6f2081cc64f510b6fa3cbf2de5b/src/vs/platform/agentHost/common/agentHostProtocolCompatibility.ts).
It is a research source option, not a supported or verified installed distribution.
Its `protocolServerHandler.ts:1870` still returns original provider-scheme sessions
alongside canonical sessions and ignores catalog page hints; `:3092` does not
exclude legacy sessions for this observer's unknown client type. The current
`ahp_1_0_observer_v1` therefore covers the upstream canonical-session subset only,
not this main distribution's complete mixed catalog. It does not silently switch
to 0.10 decoding after negotiating 1.0. A future 1.1 decoder is not enabled by that
version string. Editor socket/named-pipe transport,
headless configuration, credentials and real provider behavior remain outside the
current explicitly configured TCP fixture acceptance.

## Owned-work wire pin (disabled)

[`ahp_protocol.py`](../../src/bat_agent_connector/ahp_protocol.py) adds two offline
decoders for the same fixed commits above: upstream **1.0.0** and VS Code **0.10.0**.
The selected wire version is exact; a distribution version or `serverInfo` cannot
select a decoder, and unknown versions refuse. Canonical `ahp-session:` identities
remain distinct from the stable distribution's provider-scheme identities.
The module has only the bounded local read caller below, with no write transport,
registered action or enable switch.
`require_owned_write()` always refuses. The existing observer still permits only
`initialize`, `reconnect`, `listSessions` and `subscribe`; its bounds and capability
projection are unchanged. All manual and foreign sessions remain read-only.

| Pinned subset | Wire shape and refusal boundary |
| --- | --- |
| Create | `createSession` and `createChat` are RPC requests with numeric IDs and `null` success results. A null result proves neither native identity nor completion. |
| Turn | `dispatchAction` is a notification with `channel`, `clientSeq`, and `chat/turnStarted` (`turnId`, `startedAt`, `message.text`, `message.origin.kind`). Client messages must have origin `user`. |
| Cancel | `chat/turnCancelled` is dispatched as a notification with `turnId` and producer-supplied `duration`; no `cancelTurn` RPC is introduced. |
| Echo / terminal events | Server `action` carries `channel`, `serverSeq`, `action`, optional `origin` (`clientId`, `clientSeq`) and `rejectionReason`. `chat/turnComplete`, `chat/turnCancelled`, and `chat/error` carry producer duration. No timestamp subtraction or success inference occurs. |
| Readback | `fetchTurns` accepts a chat channel and optional cursor and returns `{}`; the host loads history through `chat/turnsLoaded`. This module does not decode history or a Result envelope. |
| Busy | `SessionStatus` is a bitset: Idle=1, Error=2, InProgress=8, InputNeeded=24, IsRead=32, IsArchived=64. InputNeeded is busy; metadata bits do not erase Idle. Busy wins if both Idle and InProgress are present. |
| Refusal | Standard JSON-RPC and pinned AHP errors retain their original code/message/data; version refusal requires `supportedVersions`, authentication refusal requires `resources`. No authentication, permission request or retry follows. Initialization error envelopes can be decoded, but successful initialization remains outside this subset. |

These fields come from the two pins' `common/messages.ts`, `common/commands.ts`,
`common/actions.ts`, `common/errors.ts`, `channels-session/{commands,state}.ts`, and
`channels-chat/{commands,actions,state}.ts`. The upstream paths begin with `types/`;
the VS Code paths begin with `src/vs/platform/agentHost/common/state/protocol/`.
See [notification routing](https://github.com/microsoft/agent-host-protocol/blob/5f16d81bb7045b66d7bc768d244feab75002d943/types/common/messages.ts),
[turn actions](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/channels-chat/actions.ts),
and [busy bits](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/common/state/protocol/channels-session/state.ts).
The conservative subset supports text messages and empty active-client tools;
attachments, model/agent selections, contributed tools, customizations, forks and
side chats refuse. Provider config and metadata objects remain opaque. Shape decoding
does not validate provider policy, correlate replies to durable operations, establish
creation evidence, or replace controller admission and the final frame guard.

[`pins.json`](../../tests/fixtures/ahp-owned/pins.json) records the upstream repository,
commit and paths. Every row in [`protocol-shapes.jsonl`](../../tests/fixtures/ahp-owned/protocol-shapes.jsonl)
is labeled **synthetic** and includes its source repository, commit and file path.
These are hand-written offline protocol examples, never de-identified real traces.
Golden byte comparisons use LF on every platform. They prove no installed host or
provider behavior; `tested_host_versions` stays empty.

Two current blockers keep owned writes unavailable. First,
[`host_git.py`](../../src/bat_agent_connector/host_git.py) disables local Git. A
local-only profile cannot create owned work unless it has an explicitly bound SSH
Git route to the same host; an editor endpoint does not establish that binding.
Second, neither pin provides an atomic conditional-turn RPC. Observing Idle and the
existence of `TurnInProgress` (-32004) do not prove the host refuses a concurrent turn.
A write cell stays off until a real trace proves that profile's busy refusal.
There is no invented result, busy-query or conditional-turn RPC. This is an E07
contract increment, not R3/E11 real-device acceptance or completion of A12/A21/A22/A26.

繁體中文：本增量只加入兩個固定版本的離線 wire decoder，未接上正式執行路徑，
所有自有工作寫入仍停用；既有 observer 的四個唯讀方法、限制及人工 session 保護不變。
建立成功回覆是 `null`；turn／cancel 是 notification，不可當成有 result 的 RPC。
busy 必須按 bit 判讀，InputNeeded 仍算忙碌；idle 或回覆成功都不是完成或寫入許可。
fixtures 每筆標示 synthetic 並附公開來源，沒有真實 editor trace，也不代表 R3／E11 驗收。
目前本機 Git 停用，local-only profile 除非明確綁定同主機 SSH Git 路線，否則不能建立自有工作。
pin 沒有原子條件式 turn；在真實 trace 證明該 profile 的 busy refusal 前，寫入能力保持關閉。

## Selected Linux editor verification

[`ahp_local_endpoint.py`](../../src/bat_agent_connector/ahp_local_endpoint.py) provides
an internal central reader for one explicitly bound `LocalAhpProfile`. The binding
names its VS Code user-data root, selected entry identity, resources/app installation
directory, provider and optional session URIs. It carries no token. This increment
does not register a new API action or UI; callers must supply the existing authority
check, which runs before every application frame and before retaining a receipt.
Windows named pipes and macOS refuse. No account selection, sign-in, provider probe,
dummy session, process launch or stale-file removal occurs.

Discovery follows the pinned [LOCAL_ENDPOINT.md](https://github.com/microsoft/vscode/blob/2a59476c9bfcb90b3ddc372c36762471b7dfad1c/src/vs/platform/agentHost/LOCAL_ENDPOINT.md):
only the selected root's `agent-host/local-endpoint/entries` and read-only legacy
`metadata.json` are inspected. Metadata directories require owner-only 0700 and
regular metadata files 0600; descriptor walks reject symlinks in every component.
Unknown schemas and malformed entries are excluded individually. Canonical entry
filenames hash `(type, pid, instanceId)`; new files win legacy collisions. Unknown
PID liveness stays unknown, and an absent selected identity never selects another
window. Enumeration is bounded to 32 entries (including fallback candidates),
64 KiB per file; limit failures never establish an empty or complete catalog.

The reader connects to the exact published Unix socket path. Before sending the
upgrade token it checks Linux peer UID/PID against the same-user entry. The token
is read into temporary backend memory and sent only as `?tkn=...`; socket logging
is disabled and transport errors become fixed local reasons. HTTP 403 reports
`connection_token_unavailable`. Token-source, metadata, socket or installation
changes invalidate the previous proposal. Peer UID/PID is not instance ownership
or running-binary attestation; all writes remain disabled.

One connection offers only the selected `0.10.0` wire version. Initialization reads
root provider/capability/model/config observations, then the bounded catalog and explicitly
selected session/chat snapshots. The shared wire decoder checks native identities,
status bits and supported chat action envelopes. Unknown action shapes refuse.
The observer's unchanged 30 s / 4 MiB / 512-frame / eight-page / 200-session bounds
apply, with 1 MiB frames and at most eight selected sessions and eight chats. There
is no reconnect; at most one verification per profile and sixteen per process run.

Selected installation files (`package.json` version and `product.json` commit) must
be regular, trusted-owner, non-writable-by-others readbacks of the fixed 1.141.0 pin.
`serverInfo` only cross-checks that evidence. A private process-local receipt retains
binding and installation digests, endpoint incarnation, negotiated version, decoder,
provider/config/model digest, observation time and coverage. Provider details remain
opaque observations, not model or tool-policy validation. It contains no token or
transcript and expires on restart. Public projection is limited to profile ID, fixed
reason and validity window; its 60-second TTL is a proposal, never a write grant.
Synthetic disk-backed Unix-socket tests do not establish R3/E11 live acceptance.

繁體中文：新增明確綁定 Linux editor instance 的中央唯讀驗證器；不自動選窗、啟動、
登入或清理。只讀選定 user-data root 的安全 metadata，沿公告 socket 原路徑連線；
token 僅短暫留在後端並用於 upgrade，不進入回執、公開資料或紀錄。
安裝版本須由選定安裝檔案讀回，不能只相信 serverInfo。回執只在程序內私存，
公開僅有 profile ID、原因與期限；60 秒不是授權。來源變更即失效，寫入仍停用。

## Remaining M5 work

W22-B must use fixed checkpoint continuation into newly created managed resources
and original native creation receipts through existing action/controller policy.
A URI, cwd, native session number or subscription is not creation ownership.
W23 still needs a general first native coding CLI adapter; the existing frozen-
context discussion turn is not that adapter. W24 still needs a persisted workspace
working-adapter transition with PM document continuity and the original writer queue;
the native/BAT discussion selector does not establish that contract. These execution,
create-own and adapter-switch capabilities remain explicitly unsupported here.
