# Agent resource observations: local collection v1

This is the first resource-data increment of the [six-view workbench](pm-project-control-center.md#3-agent-資源與效能以可交付成果評估資源).
It adds recurring collection in the Python central daemon and an English/zh-TW shared
browser/Tauri resource view. It does not complete W30, remote host collection, delivery
performance attribution, capacity reservations or forecasts.

## Authority and configuration

The central administrator explicitly connects one local JSONL file, original CLI session
identity and existing PM workspace. No home-directory discovery, path-based project
matching, transcript upload, credential reading, subprocess or provider request occurs.
Manual sessions remain read-only. A source is a read grant, never management ownership.
The host shown is this central's durable server identity, not a guessed BAT host alias.

Configuration is loaded with the existing central configuration at service startup:

```toml
[usage]
interval_s = 60

[[usage.sources]]
source_id = "implementation"
workspace_id = "ws_11111111111111111111111111111111"
adapter = "codex-rollout-v1"
path = "/srv/example-observations/session.jsonl"
session_id = "11111111-1111-4111-8111-111111111111111"
role = "implementer"
# Optional administrator assertion of shared account/pool identity; never an email/token.
quota_pool = "pool_22222222222222222222222222222222"
```

`claude-transcript-v1` uses the same fields without `quota_pool`. IDs in this example are
synthetic and must be replaced with explicitly selected identities. Configure the actual
source file; copying a transcript is not required. Windows uses an absolute local path.
At most 64 files and one binding per CLI/session/path are accepted; glob patterns and
unknown adapters are rejected. The poll interval is 10–3,600 seconds. No source is enabled
by default. This increment does not add a graphical source-connection flow.

Changing an existing source's workspace, path, adapter, session, role or pool cannot
reassign its stored history: its projection becomes `binding_changed`. A new reviewed
source ID is needed for a different binding. Removed configuration immediately disappears
from the current projection when configuration is activated (including service restart).
An in-flight read rechecks the configured binding before committing. Historic rows are retained.
Current workspace membership and observe scope guard HTTP, RPC and MCP reads. The UI clears
counts, details and quota on failed reads; late replies cannot restore a withdrawn view.

## Collector and durable evidence

`TaskDaemon.serve` owns one collector task, independent of open windows. It checks the
existing daemon lease before reads and again before committing. One collector lock serializes
cycles. File reading/parsing runs outside the event loop; SQLite writes remain with its owner.
Shutdown cancels the loop; an already-running file worker has no database or external writer.
Deleted workspaces are not scanned. No model is awakened by usage events.

Each cycle reads at most 2,000 lines per file, stopping after approximately 2 MiB (at most
one additional 1 MiB bounded line). Incomplete trailing lines stay at the original cursor.
Regular files only: symlinks, pipes and devices are refused. File identity, size, a prefix
digest and cursor-adjacent digest detect replacement, truncation and observed rewrites.
This is an append-only log contract, not a cryptographic audit of every historical byte.
Detected changes do not reset the cursor or silently count a second copy.

Cursor, allowlisted parser state, normalized observations and rate-limit snapshot commit
in one transaction. A failed parse or conflicting receipt retains the original cursor and
withdraws the source's derived values. Retry resumes only from that cursor. Two idempotent
tables and a time-window index add no `user_version` or new historical data migration step.
Prompt text, tool arguments, cwd, account metadata, raw transcript IDs and raw paths are
not stored in these tables or returned by the API. Private binding and cursor fingerprints
remain local. Diagnostics contain fixed codes, never the offending line or path.

## Versioned adapters and accounting

These are narrow structural input profiles, not a declaration that every CLI version or
provider is supported. Original CLI version is retained when present; absent fields remain
unknown. Unexpected counter shapes fail closed. Tests use synthetic records only.

| Profile | Identity and counter handling | Inclusion / limits |
| --- | --- | --- |
| `codex-rollout-v1` | Explicit `session_meta.payload.id`, `turn_context`, `event_msg/token_count.info.total_token_usage`; consecutive cumulative differences, never a sum of `last_token_usage`. Equal cumulative values do not create calls. Counter regression or synthetic context-fill mismatch withdraws the source. Forked histories are refused because parent usage attribution is unproved. | Input includes cached tokens; output includes reasoning; total adds input/output once. Optional cache-write stays unknown when absent. Turn context is the **requested** model/effort; token-count events do not prove the actual model. Call count remains unknown. |
| `claude-transcript-v1` | Explicit `sessionId`, assistant `message.id`, `message.model`, `message.usage`; one receipt per original message. Identical fragments deduplicate; monotone refinements with unchanged attribution replace the same receipt. Conflicting attribution/counters withdraw the source. | Native input excludes cache. Total adds input, cache read, cache creation and output only when all are present. Cache subcategories are not added again. Reasoning is unknown in this profile. Distinct reported messages are not a claim of all API calls/retries. |

Codex source contract: [protocol.rs at 4a93bd2](https://github.com/openai/codex/blob/4a93bd249a4e73e24c3cc889053c947c6986362a/codex-rs/protocol/src/protocol.rs),
including `TokenUsage`, `TokenUsageInfo`, `TokenCountEvent`, `SessionMeta`, and `TurnContextItem`.
The cumulative counter also has synthetic context-window fill paths; those cannot establish
consumption. Claude references: [documented transcript location/identity](https://code.claude.com/docs/en/hooks#common-input-fields)
and [API cache accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
The local transcript profile is narrower than the full Claude API and is not a promised stable
upstream transcript schema; format changes require adapter/fixture review.

Observations are assigned their source timestamp. An interval includes observed events, not
a precise distribution of consumption between cumulative reports. Missing counters preserve
both known subtotal and the number of covered observations. Empty coverage is unknown, not
zero consumption. Aggregate values are capped before exceeding browser-safe precision.
Token volume is separate from money, list-price estimates and subscription quota.

## Quota, read model and presentation

Only a source-provided Codex `rate_limits` snapshot with a named `limit_id`, numeric used
percentage, duration and reset time is eligible. It also requires an explicit opaque pool
binding. Private plan/account/credit metadata is discarded. Group by pool **and limit ID**;
choose the newest currently visible report, never add balances from different sessions.
Conflicting simultaneous reports yield unknown. Reports older than five minutes, future
reports, expired windows and sources still catching up do not supply current balances.
Pool identity is an administrator assertion, not an inferred account match. Cross-central
pool federation and Claude quota signals are not implemented.

`GET /api/v1/pm/workspaces/{workspace_id}/resources` and RPC/MCP
`pm_resources_get(workspace_id, since?, until?, buckets?, tz?)` read this same projection.
They do not collect files or dispatch work. Tauri allows only this exact read. On the
resources path the bridge also allows bounded timestamps, `buckets` equal to `day`, and one
time-zone token of letters, digits, `/`, `_`, `+` and `-` (no `.`). A time zone without
`buckets=day` is refused before the request is forwarded. Paths, refresh commands and POST
are refused. Default window: seven days. Every read rejects a duration over 366 days,
inclusive start and exclusive end. A 366-day window that does not start at local midnight
can touch 367 local dates; that plain read is accepted. Local dates are counted only when
daily buckets were requested.

HTTP query-shape failures and projection failures both use status 422 and stay distinct.
An empty value, a repeated key, an unknown key or another malformed query is HTTP
`INVALID_REQUEST` and never reaches the projection. The same values passed as RPC
parameters are `INVALID_PARAMS` (`Unknown PM read parameters` for an unknown key). A
`buckets`, `tz` or window value that does reach the projection is `INVALID_PARAMS` on both
HTTP and RPC:

- `tz` is not an exact cached IANA name: `tz must be an IANA time zone name`
- `tz` without `buckets=day`: `tz requires buckets=day`
- `buckets` is not exactly `day`: `buckets must be day`
- the duration is over 366 days, or the zoned window cannot be represented: `Select an aware time window of at most 366 days`
- `buckets=day` spans more than 92 local dates: `Daily buckets cover at most 92 local dates`

`tz` is accepted only together with `buckets=day`. It must be an exact key from the
process-cached `zoneinfo.available_timezones()` set, excluding `localtime`, `posixrules`
and `Factory`. Matching is case-sensitive. The response echoes that requested key. When
buckets are requested and `tz` is omitted, the zone is `UTC`.
The selected window applies to token observations; the separate quota table always shows
currently fresh reports. Resource configuration detail is capped at 20 groups with an
explicit partial marker. Per-source counts are not summed into a project-wide cost claim.

## Daily buckets

Daily buckets are opt-in, so `features.pm_resources.version` stays 1. Clients detect chart
support from `features.pm_resources.buckets` (`{granularity: "day"}`), not from a version
bump, and request the rows with `buckets=day`. Without that parameter the response has no
`buckets` key, and the read does not resolve a zone, list local dates or scan samples into
days. MCP omits `buckets` and `tz` unless the caller set them, so a newer client can still
read a daemon that does not know those keys.

When `buckets=day` is set, the object is `{granularity: "day", tz, series}` and the window
may span at most 92 local dates. A window of 93–366 days returns the 92-date error; a
longer window returns the 366-day error first. Each series is one source that still
contributes totals: `source_id`, `adapter`,
`cli` (`codex` or `claude`, the adapter name before the first hyphen) and `days`. A day is
`{date, tokens, complete}`. `date` is the local calendar day. `tokens` has `input`,
`cache_read`, `cache_write`, `output`, `reasoning` and `total`, each a number or null.

The last local date is the zone's date of the instant 1µs before the exclusive window end,
so a repeated or missing local midnight follows that instant. An ambiguous local midnight
uses its earlier occurrence. Samples are assigned by comparing the stored `observed_at`
string with the precomputed millisecond `+00:00` start of each local day. The read does
not parse every row. Daily values sum the metrics already stored on samples inside both
that local day and the requested window. They do not add cache into input or reasoning
into output again, and they do not recompute Claude's total. A metric is present only when
every sample that day has it. A missing counter stays null. A field seen on only some
samples is null, not a partial sum presented as the day's total.

`complete` is true only when all of these hold: the stored source status is exactly
`current` (that cycle committed a cursor at EOF together with the samples), the cursor's
`coverage_at` is an aware timestamp at or after the local day's exclusive end plus a settle
margin, and the requested window contains the whole local day. The settle margin is
`max(15 minutes, 3 × interval_s)`. `coverage_at` is the clock when that file read started.
It is written on the cursor in the same journal transaction as the samples. It is not
`successful_at`. `successful_at` remains the commit clock and is used only to project the
`stale` label, which is `max(180 seconds, 3 × interval_s)` after the last success. A
projected `stale` label does not erase a settled read: a day whose `coverage_at` is past
that day's end plus the settle margin stays complete. `catching_up` is not proof. A cursor
written before `coverage_at` existed is never complete until the next successful collect;
the read does not fall back to `successful_at`.

`complete` means the read started at or after the day's end plus the settle margin,
reached EOF and covered the whole requested day. It does not freeze counters. Claude monotone refinements
keep the first fragment's timestamp, so a fragment that arrives later than the settle
margin still lands on that first day and can replace a day already reported complete. A
line appended between the EOF check and the commit can also be missed when the read had
already started past the settled end. A clipped first or last day is not complete. Its
tokens are the samples inside the window. A day with no samples and without that coverage
has null tokens, not zeros. A settled day with no samples is zero for every metric: the
file was read and contained no usage. That zero is not a substitute for an omitted counter
on a real sample.

Withdrawn, unavailable, binding-changed, pending and removed sources are omitted from
`series` in the same cases where their totals are withheld. The bucket scan uses the
existing `(source_id, observed_at)` index. It does not open log files, move a cursor or
collect. At most 92 local days are returned per visible source. Quota snapshots stay
separate and are not split by day.

The shared UI shows source/CLI, recorded model context, role, observed tokens and collection
state, with details for counters and their inclusion rules. Source filters preserve row order;
opening another view stops its polling. A pause control freezes view updates, not collection.
There are no decorative progress animations. Missing quota, capacity and comparable accepted
work remain unknown/insufficient; no savings recommendation or automatic model downgrade follows.

## Remaining work

Remote host-local collectors and authenticated metadata transport; safe graphical source
binding and session discovery; more named CLI adapters; account/pool federation; complete
work/attempt/verification attribution and rework classification; capacity observations;
comparable accepted-work distributions and range forecasts; the shared usage-chart UI over
these daily buckets; scheduling reservations and policy execution remain on the adopted backlog.
No owner installation or live BAT/provider acceptance is established by offline fixtures.
