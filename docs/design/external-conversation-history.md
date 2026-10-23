# Explicit external conversation history

This is an E08 / EC-1.1 T3–T5 read-only increment. It retains the Connector as
collection and authorization authority. It does not complete native execution,
source discovery, cross-adapter aliasing, Work derivation or real-device acceptance.

## Grant and identity

`[session_history]` is separate from `[usage]`. Usage counters never authorize
transcript content. Each source names one absolute local `.jsonl` file, one native
session ID, one workspace, one format profile and explicit reader actor IDs.
The current workspace membership rules and the reader grant both apply. Even an
administrator needs a named reader grant to see transcript content. No directory
search, home scan, subprocess, CLI resume, network request or model input occurs.

```toml
[session_history]
interval_s = 60

[[session_history.sources]]
source_id = "review-history"
workspace_id = "ws_11111111111111111111111111111111"
adapter = "codex-history-jsonl-v1"
path = "/srv/example/shared/review.jsonl"
session_id = "11111111-1111-4111-8111-111111111111111"
readers = ["reviewer"]
```

This example is synthetic. There are at most 16 sources, 64 distinct reader IDs
per source and a 10–3600-second collection interval. An empty reader list withdraws
access. Config activation replaces the collector's current grant immediately;
a pending read rechecks that grant and the daemon owner lease before commit.
Removing a source or changing its readers hides prior derived content immediately
on the next authorized API read, without waiting for file collection.

A binding hashes the source ID, workspace, adapter, path and native session ID.
Reader changes do not change content identity. Reusing a source ID with another
binding never transfers its old history. A new binding needs a new source ID.
Neither a matching title nor a matching checkout proves identity or ownership.
Host identity is the existing Connector server ID. Work/Attempt remain null,
execution status remains unknown and ownership is `external_read_only`.

## Current grants on each journal connection

`HistoryCollector.config` activates the reader projection inside a journal
transaction. Two TEMP tables hold the activation generation and the granted
source/workspace/binding/actor pairs, with bounded display metadata. They contain
no paths or transcript bodies and are not persisted with the journal. The
constructor starts without an active generation, then installs the current config
before the daemon creates its API. The existing managed config assignment uses
this setter; it does not maintain a second reader grant list.

The shared `current_grants` DB helper is used by both existing history reads and
the internal fixed-reference resolver. It requires observe scope, current workspace
membership and an explicit reader grant, then checks generation and membership
again before returning. Positive grants are not cached across activation. An
uninitialized connection, missing TEMP projection, inactive or mismatched generation
refuses access, including for administrators. A second connection must activate its
own current config; a persistent table with the same name cannot substitute.

The generation is also compared with the collector's connection-local activation
callback. A failed setter transaction retains the previous config and grants. If an
outer transaction subsequently rolls back an activation, reads fail closed until a
new activation succeeds. Withdrawing and regranting the same source still changes
generation and invalidates an in-progress read. No new durable table, data step or
`user_version` is allocated by this projection.

## Fixed retained references

`pm_external_history.preview` is an internal read-only resolver, without an HTTP,
RPC/MCP route or UI action. It accepts a workspace, source ID, entry ID, expected
retained content digest and one selection. `resolve` verifies a previously returned
reference. Neither function collects, opens the original file, starts a session or
writes a record. History entry reads now also expose their retained `content_digest`.

The `external_history_ref/v1` object contains `schema`, `server_id`, `workspace_id`,
`source_id`, `entry_id`, `source_digest`, `content_digest`, `selection` and
`excerpt_digest`. Its three SHA-256 digests have distinct meanings:

- `source_digest` covers the original JSONL line bytes, including the newline.
- `content_digest` covers the retained decoded entry excluding `source_offset`.
- `excerpt_digest` covers `{"ref": <reference without excerpt_digest>,
  "content": <selected content>}`. Selected content contains `role`, `parts`,
  `clipped` and `omitted_parts`.

Both structured digests use UTF-8 JSON with sorted keys, no ASCII escaping, no NaN,
and comma/colon separators without spaces. No trimming or Unicode normalization
occurs. A whole-entry selection is exactly `{"kind":"entry"}`. A text selection
has exactly `kind: "text"`, `part_index`, `start_codepoint` and `end_codepoint`.
The range is half-open and counts Unicode codepoints, not UTF-16 units. Empty,
negative, boolean, out-of-range and non-text selections are refused. Extra fields,
surrogates and malformed references are refused as well.

A reference covers at most one retained entry, 128 parts and 16,000 text codepoints.
The entire preview must fit 128 KiB even with ASCII-escaped JSON. An oversized
preview is refused, never shortened to fit. Existing collector clipping and omitted
parts remain explicit; the resolver cannot restore omitted material. Its response
always has `history_complete=false` and `execution_status="unknown"`. Tool markers
remain markers, not proof of execution. Missing grants or unreadable/rebound sources
refuse access; an authorized digest mismatch refuses the changed reference instead
of retargeting it. Source paths and native session identifiers are not returned.

Refusals are `PM_HISTORY_SOURCE_UNAVAILABLE` (404),
`PM_HISTORY_REFERENCE_CHANGED` (409) and `PM_HISTORY_REFERENCE_INVALID` (422);
a caller without observe scope receives `FORBIDDEN` (403). Unauthorized sources
never receive a digest comparison that would disclose their contents.

## Narrow format profiles

`codex-history-jsonl-v1` reads the SessionMeta/ResponseItem JSONL shapes inspected
at [Codex 4a93bd249a4e73e24c3cc889053c947c6986362a](https://github.com/openai/codex/tree/4a93bd249a4e73e24c3cc889053c947c6986362a/codex-rs/protocol/src).
A matching SessionMeta must precede content. Fork, parent and inherited-history
markers are refused. Only user/assistant response-item plaintext and function or
custom-tool request/result markers are projected. Reasoning, system/developer
messages, base instructions, event-message duplicates, arguments and tool bodies
are omitted. A recorded CLI version is provenance, not blanket compatibility.

`claude-history-jsonl-v1` accepts explicit user/assistant records with matching
`sessionId`, a `uuid`, a matching inner role, plaintext and tool markers. Sidechain
records are refused. [Claude Code hooks](https://code.claude.com/docs/en/hooks)
document `transcript_path`; that is not a promise of a stable, universal transcript
schema. Unknown shapes remain omitted or unavailable under this named profile.
Neither adapter infers completion from the last message or a tool request.

A stable entry ID combines the binding with the native record ID where present;
otherwise Codex uses the original byte offset. Original-line digests remain in
entry provenance. The same native ID and exact original bytes deduplicate; changed
content under the same ID withdraws the projection. Offset identity makes no claim
about semantic duplicates. Cross-file and cross-adapter aliasing remain unavailable.

## Bounded collection and retained records

The collector reuses the bounded regular-file reader with a separate decoder.
Symlinks, devices and pipes are refused. File identity, size, prefix and cursor
anchor checks detect replacement, truncation and observed changes; this is not a
cryptographic audit of every already-consumed byte on every poll. Each poll reads
at most 2 MiB plus one bounded line, 2,000 lines, with a 1 MiB line maximum.
Unterminated records wait for their newline. Source cursor, parser state and
entries commit in one journal transaction. Loss of owner lease or grant commits
nothing. Failed transactions replay the same page without gaps or duplicates.

Each entry is capped at 16,000 text characters and 128 parts. Each source stores
at most 20,000 entries; the limit retains the previously collected readable portion
and still checks source availability on later cycles. Collection errors hide old
plaintext while retaining its records and original cursor. Grant withdrawal is
not deletion. Existing data-retention administration remains separate; this reader
adds no transcript purge action or retention policy. Tables are idempotent and use
no new `user_version` or data-migration step. Normalized entries are append-only.

Coverage includes collection/as-of times, source status, stale state, omitted
records/parts and clipped records. EOF means only the selected file was caught up
at that read. `history_complete` is always false. Missing content, reasoning and
unknown execution remain visibly distinct from a completed Work Result.

## Shared reads and UI

- HTTP `GET /api/v1/pm/workspaces/{workspace_id}/history` lists up to 16 currently
  visible source summaries; it does not load bodies or scan original files.
- HTTP `GET .../history/{source_id}?after=0&limit=50` returns chronological entries.
  `after` is a source-local integer up to 2^53−1, and `limit` is 1–100.
  Pages additionally cap escaped entry payload at 512 KiB;
  `next_after` continues the same source without skipping the first omitted row. Global sequence gaps disclose no other
  source's activity.
- RPC/MCP `pm_history_sources_list` and `pm_history_get` share those reads and the
  `observe` scope. Tauri permits only the exact read paths and bounded parameters.
- `features.external_history` advertises this separate capability. The older
  `features.session_history` remains the BAT journal feature and is unchanged.

Resources → External conversations reuses shared cards, buttons and the inspector.
English and Traditional Chinese expose collection state and partial coverage.
Bodies load only after selection; more/new entries require an explicit read.
The client polls only source summaries while that view is active. Unchanged
refreshes preserve text nodes, reading position and focus. Grant or source failure
removes old plaintext, and late responses cannot reopen a closed or withdrawn
reader. Browser Back and the sheet's close control return to its source list.
This view caps retained rendered entries at 500. Source text is literal, not HTML.

There is no Send, resume, edit, automatic PM attachment or runtime control here.
A source grant cannot become creation evidence. Linking a fixed excerpt into a new
owned Work/PM context, trusted aliases across adapters, broader tool-content
readers and native full work routes remain E06–E08 work.

## Verification boundary

Real disk journals and TEMP schemas also cover connection isolation, atomic grant
activation, response-boundary withdrawal, fixed digests, Unicode selection and
preview size refusals using synthetic Codex/Claude records. These internal readers
add no linking, derivation or suggestion capability.

Synthetic local transcripts cover grant withdrawal, config activation during
collection, owner-lease loss, cursor replay, conflict/replace/truncate failures,
limits, omissions, stable pagination and shared transport behavior. Browser tests
cover HTTP/IPC, refresh/read preservation, source withdrawal, Back, late replies,
locales and 390/768/1440 layouts. These are fixture results, not evidence of reading
an owner's real CLI history, running their models or testing physical mobile IMEs.

Synthetic shared-UI captures: [desktop source list](../review/ec11-external-history/list-en-1440.png),
[desktop reader](../review/ec11-external-history/detail-en-1440.png), and
[Traditional Chinese phone reader](../review/ec11-external-history/detail-zh-TW-390.png).
These contain fixture content only.
