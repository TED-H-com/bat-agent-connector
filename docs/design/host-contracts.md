# Host contract handshake and reads

This is the Connector side of the P1 host-contract slices in the
[single-product direction](../product/suite-direction-2026-11-13.md). It covers
capability detection, connection negotiation and two reads that a negotiated
connection may send: `bac:workspace-read` and `bac:session-locate`. Python
Connector / Task Service remains the authority for policy, operations and
scheduling. No product path calls the reads yet, and no API, MCP, RPC, CLI,
Dashboard field or schema is added.

Authentication advertises only a protocol/minor pair; identity, build, features
and limits arrive in hello. The host implementation is maintained separately in
`TED-H-com/better-agent-terminal`. Its host contract at
`src-tauri/crates/bac-host-contracts/CONTRACT.md` is the wire authority. This
page and the mock follow that contract; no test in this repository runs against
a real host.
The BAT transport reference is `81655b483ba6f2f6ce1f2158870613d8ec0215d4`,
`src-tauri/src/remote_server.rs` (v2 auth and invoke dispatch). This reference
identifies the transport, not a claim that the baseline already implements BAC.

## Advertisement and request

A successful `bat-remote/v2` auth result may contain:

```json
{"capabilities":{"bac":{"protocol":"bac/1","minor":0}}}
```

Recognition requires an object `capabilities`, an object `bac`, the exact string
`bac/1`, and integer `minor` equal to zero. A boolean, floating-point zero or
string zero is not an integer for this contract. Unknown members are ignored.
A present but incompatible or malformed `bac` member is unsupported. Missing
`bac` (including a non-object capability container) is absent.

Only a supported advertisement triggers this frame, immediately after auth is
accepted and the socket reader starts:

```json
{"type":"invoke","id":"bac-hello-1","channel":"bac:hello","params":{"protocol":"bac/1","minor":0}}
```

On subsequent connections of the same client object, `params` also contains
`expectedHostId`, the first successfully negotiated host identity. No `args` or
`contextId` is sent. The request uses `_roundtrip` directly, inside connect's
socket-cleanup scope: no semaphore, allow-list lookup, recursive `connect`,
guard or retry. Its deadline is `min(connect_timeout, 10 seconds)`.
`bac-hello-1` does not consume the ordinary `batc-N`/ping ID counter. Concurrent
connect callers share the existing connection lock and one hello.

Host validation order is frame/params shape, compact decoded request size,
`contextId`, protocol/minor, identity storage, then `expectedHostId`. Request
`minor` must be an unsigned 64-bit JSON integer: booleans, strings, negative
numbers, floating-point zero and overflow are invalid requests. Unsupported
versions never touch the identity store. The size bound is 65536 bytes of compact
UTF-8 serialization of the decoded frame, not the bytes received on the socket.

The client waits for the hello reply before any subsequent BAC frame. Host state
changes are serialized but not ordered by arrival, so arrival order alone is not
a negotiation barrier. This client awaits hello inside connect before sending
any other BAC channel. On the host a refused hello preserves an already
negotiated connection except for `HOST_MISMATCH`, which revokes it. Every refusal
on a fresh connection leaves it unnegotiated.

A host without the advertisement receives exactly the existing client frames,
including IDs and ordering. An unsupported advertisement also causes zero BAC
frames. Existing resource grants, final-frame fences, operation keys, retries
and read/write behavior are unchanged. `host_status` continues passing through
the original host `capabilities` object; it does not expose negotiation state.

## Reply and internal state

An ordinary `invoke-result.result` contains one envelope:

```typescript
type Reply<T> = {ok: true; value: T} | {ok: false; error: {
  code: string; message: string; retryable: boolean;
  details: Record<string, unknown>;
}};
```

Hello's success value contains `protocol: "bac/1"`, integer `minor: 0`, a
`hostId` matching `h_` followed by 32 lowercase hex digits, and:

- `build`: `product: "bac"`; `revision` is 40 lowercase hexadecimal digits or
  `unknown`; `release` is 1–64 printable ASCII bytes; `dirty` is a boolean or
  null. All four known members are required.
- `features`: an object with the known boolean flags `workspaceRead`,
  `workspaceAddTerminal`, `sessionLocate`, `gitObserve`. A missing known flag
  becomes false; a present non-boolean is invalid. Unknown flags are ignored.
  The first host slice reports all four false; only `workspaceRead` and
  `sessionLocate` authorize the reads below.
- `limits`: an object whose values are non-negative integers, excluding booleans.
  Limit names are retained, without assuming which limits a future feature uses.

The current host and mock default limits are `requestBytes=65536`,
`resultBytes=524288`, `registrationBytes=16384`, `profiles=256`, `terminals=4096`,
`locationMatches=100`, `gitStatusBytes=4194304`, `gitIndexBytes=16777216`,
`gitPaths=10000`, `gitTimeoutMs=5000`, `gitAttempts=2`. Client validation accepts
any map of non-negative integers; these defaults do not pin future host limits.

The host reply table below uses `retryable: false`. Diagnostics never select
client behavior; error envelopes still require the general types above.

| Code | Message | Details |
| --- | --- | --- |
| `INVALID_REQUEST` | `Invalid BAC request` | `{"field": F}`; F is `frame`, `type`, `id`, `channel`, `params`, `params.protocol`, `params.minor` or `params.expectedHostId` |
| `PAYLOAD_TOO_LARGE` | `BAC request exceeds limit` | `{"maxBytes": 65536}` |
| `RESPONSE_TOO_LARGE` | `BAC result exceeds limit` | `{"maxBytes": 524288}` |
| `CONTEXT_NOT_SUPPORTED` | `BAC requires a direct host connection` | `{}` |
| `UNSUPPORTED_VERSION` | `Unsupported BAC protocol version` | `{"protocol": "bac/1", "minor": 0}` |
| `HOST_MISMATCH` | `Host identity changed` | `{"hostId": <host identity>}` |
| `STORAGE_UNAVAILABLE` | `BAC identity storage is unavailable` | `{"reason": <fixed lower-case name>}` |
| `STORAGE_CORRUPT` | `BAC identity storage is invalid` | `{"reason": <fixed lower-case name>}` |
| `NOT_OPTED_IN` | `Call bac:hello first` | `{}` |
| `UNSUPPORTED_FEATURE` | `BAC feature is unavailable` | `{"feature": <flag name>}` |
| `UNKNOWN_CHANNEL` | `Unknown BAC channel` | `{}` |
| `INTERNAL_ERROR` | `BAC request failed` | `{}` |

Unknown envelope, success and build members are ignored. `bac.parse_envelope`
is the single envelope parser. A valid error raises `BacReplyError`, carrying
`code`, redacted `message`, `retryable` and diagnostic `details`. Invalid wire
raises `ValueError("invalid_reply")`. Message text and details never determine
negotiation behavior. Exception text uses the existing secret redactor and
masks host identities; messages are truncated to 300 characters after redaction.
Details are copied and never interpolated into exception text or logs.

`client.bac` is an immutable `BacState` after a completed connect. Nested build,
features and limits are immutable copies. Before connect and after explicit or
remote close it is `None`, so a closed connection has no usable negotiated
features. Every reconnect starts fresh. The four completed states are:

| State | Meaning for internal callers |
| --- | --- |
| `absent` | Reason `not_advertised`; ordinary host behavior only |
| `unsupported` | Reason `unsupported_advertisement`; ordinary behavior, zero BAC requests |
| `negotiated` | Validated identity/build/features/limits for this connection; a read below may be sent when its feature is `true` |
| `unavailable` | Hello failed; reason is the error code, with an optional bounded storage diagnostic; no usable negotiated identity/features; ordinary paths remain available |

## Outcomes

| Observation | State / connect result | Further behavior on this connection |
| --- | --- | --- |
| No BAC advertisement | `absent` | No hello; existing frames unchanged |
| Unsupported/malformed advertisement | `unsupported` | No hello or other BAC frame |
| Valid hello success | `negotiated` | Store validated immutable value; no second hello |
| Valid success naming a different host from the remembered `expectedHostId` | `unavailable`, `HOST_MISMATCH` | Client detects the mismatch itself, retains the original ID and emits the same single warning as an error envelope |
| Error envelope: `INVALID_REQUEST`, `PAYLOAD_TOO_LARGE`, `RESPONSE_TOO_LARGE`, `CONTEXT_NOT_SUPPORTED`, `UNSUPPORTED_VERSION`, `HOST_MISMATCH`, `STORAGE_UNAVAILABLE`, `STORAGE_CORRUPT`, `INTERNAL_ERROR` | `unavailable`, reason is the code | Keep connection and existing read/guarded-write paths; no hello retry |
| Other well-formed error code matching `[A-Z][A-Z_]{0,63}` | `unavailable`, preserve code | Same behavior; message/details do not select an outcome |
| Malformed envelope, invalid error fields/code, invalid success value or unexpected reply type | `unavailable`, `invalid_reply` | Same behavior |
| `invoke-error` | `unavailable`, `invoke_error` | Ignore raw host text for decisions and logging |
| Hello timeout | `unavailable`, `hello_timeout` | Pending ID removed; reader drops late reply as an unknown ID; no retry |
| Socket closes during hello | Connect raises `ConnectionLost`; state cleared | Normal connection-loss behavior; a later connection negotiates anew |
| Connect is cancelled during hello | Cancellation propagates; socket closed and state cleared | A later connect can negotiate normally |

The reader continues draining events while hello is pending. Failed or cancelled
connect cannot leave a connected client with undecided BAC state.

Every unavailable hello records one warning with code
`connector.bac.hello_unavailable`, except `HOST_MISMATCH`, which uses
`connector.bac.host_mismatch`. Both use fixed text and the validated error code
as context `reason`. Only for `STORAGE_UNAVAILABLE` and `STORAGE_CORRUPT`, a
`details.reason` matching `[a-z][a-z0-9_]{0,63}` is retained as diagnostic-only
`BacState.storage_reason` and warning context `storage_reason`. Unknown names are
uninterpreted (unspecified for behavior), not another outcome or retry policy.
Paths, overlong strings, non-strings and recognizable host identities are dropped
silently; no identity, token, raw host message or raw details is logged. The existing
transport timeout log may additionally record its generic timeout; connection
loss retains existing connection diagnostics.

## Identity and authorization boundaries

The client remembers its first negotiated host ID across closes and reconnects,
including connections with absent/unsupported capabilities or unsuccessful hello.
It sends that ID on every later hello. `HOST_MISMATCH` leaves the remembered ID
unchanged, records the warning above and keeps existing host paths available.
The client independently compares a successful hello's validated ID against the
remembered one; it does not rely on the host to send a mismatch error.
No in-process action accepts a replacement ID for that client. The memory belongs
to the client object, is not persisted, and is lost on process restart. A new
client object starts without a remembered ID.

This identity names the host data directory, not a physical machine. A copied
data directory can have the same identity; it is not an ownership grant or a
replacement for the existing TLS pin and resource policy.

`channels.HANDSHAKE_CHANNELS` lists only `bac:hello`, and
`channels.BAC_READ_CHANNELS` lists the two reads. `check_allowed`, `invoke` and
`guard_read` refuse these and all other `bac:*` names in every tier. No BAC
channel is authorized by prefix or added to a tier's allow-list: the reads leave
the process only through `BatClient.bac_workspace_read` and
`BatClient.bac_session_locate`. The host's pre-hello `NOT_OPTED_IN`, post-hello
`UNSUPPORTED_FEATURE` and `UNKNOWN_CHANNEL` refusals are exercised through raw
mock-wire tests; the client sends no feature frame that would receive them.

## Workspace read and session location

A connection whose hello negotiated the feature may send two reads. Only
dedicated client methods send them, and nothing in the product calls those
methods yet.

| Method | Channel and params | Feature | Validated result members |
| --- | --- | --- | --- |
| `bac_workspace_read(profile_id)` | `bac:workspace-read`, `{ "profileId" }` | `workspaceRead` | `hostId`, `profileId`, `source` (`live` or `persisted`), `windowId` (string or null), `revision`, `document`, `appendTargetEligible` |
| `bac_session_locate(session_id)` | `bac:session-locate`, `{ "sessionId" }` | `sessionLocate` | `hostId`, `locationRevision`, `disposition` (`unregistered`, `located` or `ambiguous`), `consistency` (`sequential`), `atomic` (`false`), `locations` |

Each location row has `profileId`, `workspaceId`, `terminalId`, `windowId`,
`source` and `profileRevision`. The client returns a copy with exactly these
members and ignores unknown ones. It accepts a reply only when the reply names
the negotiated host ID and the requested profile or session, every revision is
`sha256:` plus 64 lowercase hexadecimal digits and, for locate, the disposition
matches the number of rows and there are no more rows than the negotiated
`locationMatches` limit (100 when the hello does not state it).

A revision is an opaque token: the client checks its syntax and never
recomputes or interprets it. A locate result is a sequential, non-atomic
observation of the host's sources. `unregistered` and `ambiguous` are results,
not errors, and the client does not choose among rows.

The client refuses with `BacUnavailable` before any frame is sent:

- an ID outside the host's request grammar, without connecting (profile: 1–128
  UTF-8 bytes of ASCII letters, digits, `_`, `.`, `-` or `U+4E00`–`U+9FFF`;
  session: 1–128 characters of `[A-Za-z0-9_.:-]`);
- a connection whose state is `absent`, `unsupported` or `unavailable`, or whose
  negotiated features do not include the one the channel needs.

The client has no fallback of its own: a caller that cannot use a read gets the
refusal and decides what to do. Both calls are reads. They need no grant and no
tier, and they work with `writes = false`.

| Observation | Result | Sent again |
| --- | --- | --- |
| Success envelope with a valid value | The validated copy | — |
| Error envelope | `BacReplyError` with the host's code; message and details are diagnostics | No |
| Invalid envelope or value, or an unexpected reply type | `InvokeError` ("invalid BAC reply"), without reply content or host ID | No |
| `invoke-error` reporting a busy remote server | The read is repeated after a pause | Up to three attempts in total |
| Any other `invoke-error` | `InvokeError` with the redacted text | No |
| Connection lost | Reconnect with a new hello, then the read is repeated | Up to three attempts in total |
| Timeout (15 s for the workspace read, 30 s for locate) | `InvokeTimeout` | No |
| The reconnect negotiates another host identity or no longer offers the feature | `BacUnavailable` | No |

The feature check and the frame use the same connection: the client reads the
negotiated state and hands the frame to the socket without waiting in between.
When another caller is reconnecting and its hello has not finished, the read
waits for that hello instead of closing the connection.

`bac.py` also holds pure helpers for a later change that appends a terminal
registration through the host (`registration_error`,
`registration_effectively_equal`, `parse_append_result`). Nothing calls them,
and `bac:workspace-add-terminal` is never sent.

## Tests and open questions

`tests/fixtures/bac/hello-cases.json` contains language-independent hello examples,
including validation precedence and connection situations. It documents whole
JSON-value placeholders `$HOST_ID`, `$BUILD`, `$STORAGE_REASON`, `$FEATURES` and
`$LIMITS`. Successful cases therefore survive hosts enabling features or raising
limits. Optional `after` checks send `bac:workspace-read` with empty params after
the reply: only `NOT_OPTED_IN` means unnegotiated, independent of feature support.
One conditional case asserts the exact `UNSUPPORTED_FEATURE` reply only when
`workspaceRead` is false. `tests/test_mockbac.py` replays the cases over mock TLS,
both with features off and with raised limits and features switched on by hand (a
feature switched on that way still answers `INTERNAL_ERROR`), and tests the
mock's own answers for the two reads, which a test enables with
`MockBac.enable_workspace`; `tests/test_bac.py` covers client negotiation,
failures, identity memory, logs, isolation and unchanged guarded paths;
`tests/test_bac_workspace_values.py` covers the pure grammar and reply
validators; `tests/test_bac_reads.py` covers the client reads against the mock.
Existing legacy tests retain their original expectations.

Decided for the reads: a caller that cannot use a read gets a coded refusal
(`BacUnavailable`), and the client has no fallback of its own. Still open
before a feature writes through the host contract:

- Where a durable identity pin lives and how it follows client/configuration
  lifetimes, including client-object replacement.
- How an operator reconciles a mismatch and explicitly accepts a new identity.
- How each exact write channel binds existing scopes, grants, final-frame
  checks and durable operation reconciliation. Hello itself grants none of them,
  and no fallback may follow an uncertain feature effect.

These are P1 handshake and read increments only. The product roadmap and lifecycle
requirements retain their separate acceptance gates. No installation, native
build or live test is claimed.
