# Host contract handshake

This is the Connector side of the first P1 host-contract slice in the
[single-product direction](../product/suite-direction-2026-11-13.md). It adds
capability detection and connection negotiation only. Python Connector / Task
Service remains the authority for policy, operations and scheduling. No BAC
feature, API, MCP, RPC, CLI, Dashboard field or schema is added.

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
a negotiation barrier. This client awaits hello inside connect and sends no
other BAC channel in this slice. On the host a refused hello preserves an already
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
  The first host slice reports all four false; no flag authorizes a call here.
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
| `negotiated` | Validated identity/build/features/limits for this connection; this slice still exposes no feature calls |
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

`channels.HANDSHAKE_CHANNELS` lists only `bac:hello`. `check_allowed`, `invoke`
and `guard_read` refuse it and all other `bac:*` names in every tier. No BAC
channel is authorized by prefix. No feature channel is added to an allow-list.
The host's pre-hello `NOT_OPTED_IN`, post-hello `UNSUPPORTED_FEATURE` and
`UNKNOWN_CHANNEL` refusals are exercised solely through raw mock-wire tests.

## Tests and open questions

`tests/fixtures/bac/hello-cases.json` contains language-independent hello examples,
including validation precedence and connection situations. It documents whole
JSON-value placeholders `$HOST_ID`, `$BUILD`, `$STORAGE_REASON`, `$FEATURES` and
`$LIMITS`. Successful cases therefore survive hosts enabling features or raising
limits. Optional `after` checks send `bac:workspace-read` with empty params after
the reply: only `NOT_OPTED_IN` means unnegotiated, independent of feature support.
One conditional case asserts the exact `UNSUPPORTED_FEATURE` reply only when
`workspaceRead` is false. `tests/test_mockbac.py` replays the cases over mock TLS,
both with features off and with raised limits/features on (without implementing
the features); `tests/test_bac.py` covers client negotiation, failures,
identity memory, logs, isolation and unchanged guarded paths. Existing legacy
tests retain their original expectations.

Before the first feature uses the negotiated state, decide:

- Where a durable identity pin lives and how it follows client/configuration
  lifetimes, including client-object replacement.
- How an operator reconciles a mismatch and explicitly accepts a new identity.
- What a feature caller does when state is `unavailable`: expose a coded refusal,
  defer, or use an explicitly reviewed legacy route. This handshake does not
  authorize a new fallback after an uncertain feature effect.
- How each exact feature channel binds existing scopes, grants, final-frame
  checks and durable operation reconciliation. Hello itself grants none of them.

This is a P1 handshake increment only. The product roadmap and lifecycle
requirements retain their separate acceptance gates. No installation, native
build or live test is claimed.
