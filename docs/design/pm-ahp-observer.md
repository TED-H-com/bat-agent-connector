# W22-A: bounded AHP observation

Connector implements a read subset of the official Agent Host Protocol 1.0.0,
pinned to `microsoft/agent-host-protocol` tag `spec/v1.0.0`, commit
`5f16d81bb7045b66d7bc768d244feab75002d943`. Primary contracts:
[transport](https://microsoft.github.io/agent-host-protocol/specification/transport.html),
[lifecycle and reconnect](https://microsoft.github.io/agent-host-protocol/specification/lifecycle.html),
[session channels](https://microsoft.github.io/agent-host-protocol/specification/session-channel.html),
and [VS Code local endpoint](https://github.com/microsoft/vscode/blob/main/src/vs/platform/agentHost/LOCAL_ENDPOINT.md).
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
IP, is accepted. Unix-socket editor endpoints are currently unsupported; an operator
must explicitly configure an already running TCP AHP endpoint. Configuration URLs
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
Select at most eight distinct session URIs; each must be positively advertised by
the fresh catalog before a subscription is sent. Empty selection reads only the
catalog. Changesets are at most sixteen exact static `ahp-changeset:` URIs advertised
by a selected session. Dynamic templates, content bytes, review mutations and
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
`features.pm_workspaces.ahp_observation` describes the implemented subset. Adapter
listing returns configured IDs and that contract without connection secrets.
There is no second daemon, queue, journal, model engine or new authority tier.

## Read protocol and bounds

The sole outbound application methods are `initialize`, `reconnect`, `listSessions`
and `subscribe`. WebSocket protocol close is transport cleanup only. Initialization
negotiates exactly 1.0.0 and subscribes only to the root. Session catalog pagination
is connection-local; every reconnect refreshes the catalog without reusing cursors.
There is at most one reconnect using the original client ID, subscribed native URIs
and highest observed server sequence. RPC refusal does not invoke sign-in, session
creation, initialize fallback or any write. Replay deduplicates identical original
sequence/envelope digests and rejects conflicting identities. Sequences can have
gaps due to channel filtering; the observer never invents contiguous event history.
Snapshot fallback records an explicit gap, not a replay claim.

Limits: 30 seconds total from the first durable start receipt (not reset by retry),
1 MiB per frame, 4 MiB total, 512 frames, eight catalog pages, 200 catalog sessions,
eight selected sessions, sixteen static changesets and 500 files per changeset.
At most sixteen observers run per service, two per actor and four per adapter.
Cancellation/deadline closes only this observer's socket. Exceptions and WebSocket
logging cannot expose the private credential URL. Echoed transport tokens are
removed from retained text; raw envelope digests remain available.

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

## Remaining M5 work

W22-B must use fixed checkpoint continuation into newly created managed resources
and original native creation receipts through existing action/controller policy.
A URI, cwd, native session number or subscription is not creation ownership.
W23 still needs a general first native coding CLI adapter; the existing frozen-
context discussion turn is not that adapter. W24 still needs a persisted workspace
working-adapter transition with PM document continuity and the original writer queue;
the native/BAT discussion selector does not establish that contract. These execution,
create-own and adapter-switch capabilities remain explicitly unsupported here.
