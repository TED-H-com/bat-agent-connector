# Explicit external conversation history

This E08 / EC-1.1 T3–T5 increment reads external histories and explicitly relates
fixed entries to Work. Original CLI sessions stay read-only, with the Connector
as collection and authorization authority. It does not complete native execution,
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

The existing observation memo tracks writes to these two TEMP tables as well as
its main tables. Grant changes invalidate its historical proofs. Unknown TEMP
tables, or a grant table that shadows a main object, still disable memo reuse.

## Fixed retained references

`pm_external_history.preview` is a read-only resolver shared by HTTP, daemon and
MCP reads. It accepts a workspace, source ID, entry ID, expected
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

## Explicit relationships to existing Work

`pm.history.link` and `pm.history.unlink` are registered record actions submitted
through `POST /api/v1/operations` or MCP `operation_submit`, with the original
idempotency key. Link requires manage/observe, owner or contributor membership,
an explicit source grant and the current Work binding/version. An optional Attempt
must pass its existing reader and belong to that Work. Administrators have no
source or membership override. Admission and the local effect recheck access.

Link targets exactly `workspace_id` and `work_item_id`; params contain `fixed_ref`
and optionally `attempt_id`. Preconditions are `expected_work_version` and
`expected_binding_id`. Unlink adds `link_id` to the same target, takes no params,
and requires `expected_link_version`. Replace adds `replaces_link_id` and
`expected_old_link_version` to the link params; both Works must be in that workspace.

Excerpts store only the canonical reference, digests and original source binding.
Links have an immutable purpose, an active-tuple uniqueness constraint and CAS
versions. Append-only events and the local receipt commit with the relationship
in `ctx.effect`. A different key for the same active tuple returns its existing
receipt without another event. `replaces_link_id` with `expected_old_link_version`
withdraws the old relation and creates or reuses the new one atomically. Creators
and workspace owners can withdraw known opaque links after source revocation;
that receipt contains only `link_id`, `state` and `version`.

All paths below are relative to `/api/v1/pm/workspaces/{workspace_id}`. Reads use
observe and the same current-grant decision; list pages accept `after` and `limit`
(1–50), retain their cursor across hidden rows, and never return source text.

| HTTP read | Daemon / MCP |
|---|---|
| POST `/history-excerpt-preview` (pure read selection body) | `pm_history_excerpt_preview` |
| GET `/history-excerpts/{excerpt_id}` | `pm_history_excerpt_get` |
| GET `/work/{work_item_id}/external-links` | `pm_history_links_list` |
| GET `/external-links/{link_id}` | `pm_history_link_get` |
| GET `/history/{source_id}/associations?entry_id=...` | `pm_history_associations_list` |

`PM_HISTORY_LINK_CHANGED` and `PM_HISTORY_ATTEMPT_MISMATCH` are 409 refusals only
for visible records. Unavailable and unauthorized material share the source 404.
Global operation/event reads also enforce source access. Relationships add
reviewable material; they change no Work/Attempt state, approval, ownership,
fingerprint or Overview progress. The source remains `external_read_only`.
`features.external_history.relationships` is additive and has `derive_ready=false`;
it grants no admission authority. The shared relationship UI below is a branch
candidate; derive, search, automatic suggestion and native execution remain open.

### Desktop transport parity

The Tauri `connector_request` bridge admits the five existing relationship reads
above, including the pure-read POST preview. It passes them to the same central
service with the current principal; Rust does not resolve source files, maintain
grants or store relationships. Link/unlink continue through the existing
`/operations` path with their original idempotency key.

Preview accepts only `source_id`, `entry_id`, `expected_content_digest` and one
typed `selection`. It refuses queries, operation keys, paths, caller identity
overrides and extra fields. A text selection uses integer Unicode codepoint
offsets, a part index below 128 and `0 <= start < end <= 16000`; central still
checks the actual retained part, content digest and current access. Whole-entry
selection accepts only `kind: "entry"`.

The two list routes accept only `after` (0 through 2^63−1) and `limit` (1–50).
Source associations additionally require one exact `shi_` entry ID. Duplicate
or unrelated parameters and malformed IDs are refused. Excerpt and link detail
routes accept no query fields. This transport increment does not add
native CLI execution or installed-device acceptance. The subsequent shared UI
candidate uses these same routes.

### Shared relationship UI candidate

The English/zh-TW history reader exposes an on-demand **Related Work** disclosure
per retained entry when the server advertises relationship version 1. Opening it
reads current associations in pages of 20, follows advancing cursors even across
hidden rows and resolves current Work titles. Each relationship distinguishes a
whole entry from selected text and identifies an optional Attempt association.
Opening the Work uses its existing route. Merely reading creates no operation.

An owner or contributor with the advertised link action and manage/observe can
choose an existing Work. The client requests the whole-entry preview, verifies
its server/workspace/source/entry/digest identity and renders returned plaintext,
clipping and omissions. It sends the returned canonical reference unchanged.
The existing workspace board supplies paginated Work choices; selecting a Work
reads its current card and checks the binding, project and version. No Work,
Attempt, source grant or ownership is inferred or created by this picker.

The existing durable form submits `pm.history.link` with the displayed binding
and Work version. Its namespace also includes source and entry IDs so two entries
linked to the same Work cannot share an intent. Only the chosen Work ID and the
form's fixed reference/request persist locally; preview text and Work titles do
not. A lost response restores the original target, preconditions and key after
reload. Selection and refresh controls stay locked while that intent is unresolved.
After an explicit failed-request reset, **Refresh selected Work** obtains a new
version before a new submission. No retry silently retargets the original request.

Creators and workspace owners can submit `pm.history.unlink` through the same
form. Before submission a small index saves only the known link/Work IDs, version
and creator. If the withdrawal succeeds but its response is lost, the link may
disappear from the active-only list; its original stored intent remains reachable
through a recovery control. The index alone cannot create a new withdrawal. A
verified success removes the index entry. Current scopes, membership, action
authorization and the central existing-operation checks continue to apply.

Source withdrawal, failed source reads, reader close and workspace departure
dispose relationship forms, hide preview/plaintext and fence late responses.
Administrators do not bypass source grants or workspace roles. Older servers
retain the original read-only reader. The initial increment supplies whole-entry
links and withdrawal; the text-range and Work-side reader candidates below extend
it. Attempt selection, replacement, cleanup after source access is lost, derive
and real-device acceptance remain open. Linking
adds reference material without approving, completing or taking over any work.

### Text-range selection candidate

After requesting the whole-entry preview, **Choose a text range** exposes each
nonempty retained text part in a labelled, read-only field. Tool markers have no
range control. The user explicitly selects a range and chooses **Preview selected
text**; **Use entire entry** returns to the full retained entry. The preview shows
which selection is active, preserving clipping, omissions and the partial-history
notice. Nothing is submitted merely by selecting or previewing text.

Textarea UTF-16 positions are mapped to the original Unicode codepoint offsets,
including CRLF/CR normalization by the browser. Empty selections, split surrogate
pairs, invalid parts and out-of-bounds ranges are refused. No Unicode normalization,
trimming or client digest calculation occurs. Read-only fields support keyboard
selection with Shift plus Left/Right/Home/End, including backward selections;
grapheme navigation uses `Intl.Segmenter` when available and complete codepoints
otherwise. Mouse and native touch selection use the same preview path. These
browser fixtures do not establish physical mobile or native WebView acceptance.

The preview response must match the requested selection, exact reference schema,
source identity and retained content. Text excerpts contain only that exact slice;
role, clipping and omitted-part counts must still match the entry. The returned
canonical reference is sent through the existing `pm.history.link` action without
alteration. Pending previews clear the previous excerpt and disable sending or
changing targets. Source refusal, close and disposal clear plaintext and fence
late replies; focus returns to the initiating control when it is still available.

Only fixed-reference metadata is retained in the existing Work-specific durable
form draft, never the text field or preview body. Reloading or changing Work
re-previews that saved range and compares its original reference before showing
it. A saved operation's immutable request takes precedence over draft fields,
including older whole-entry intents without range metadata. Its recovery key,
target, parameters and preconditions remain unchanged. The shared durable form
exposes a cloned request for this display purpose; callers cannot mutate the
stored original through that accessor. An unresolved request locks range controls,
and a failed request requires the existing explicit **New request** action.

This adds no API, schema, source takeover, automatic PM input or model call.
Optional Attempt selection, relationship replacement and revoked-source cleanup
remain separate UI work within E08/E10 and EC-1.1 T5.

### Work-side reader candidate

The shared Board Work inspector now offers **View linked excerpts** in English
and zh-TW. It uses the existing selected Work and workspace membership; opening a
Work alone does not read external histories. The disclosure appears only for
relationship version 1 with the fixed-reference schema, observe scope and an
explicit workspace role. Older servers keep their existing inspector structure.

Opening the disclosure reads active, currently visible relationships in pages of
20. **Read more relationships** rereads earlier pages before advancing, including
empty pages with hidden rows. Cursors must advance, identities must match the
current workspace/Work/server and duplicate links are refused. The reader caps a
view at 25 pages and labels that limit without claiming complete coverage. Each
row shows the source ID, relationship time, whole-entry/text selection and any
explicit Attempt association. It does not infer a title, session or execution.

Selecting an excerpt reads the exact relationship, resolves its retained excerpt
and reads the relationship again before displaying plaintext. Both relationship
reads must match its active state, version, Work, Attempt, excerpt and canonical
reference. This preserves the Work/Attempt visibility check around the source
read. The server remains responsible for current grants and digest verification;
the frontend compares the canonical fields without calculating replacement hashes.
Reordered object fields do not change reference identity.

The reader renders literal text and tool markers, with shortened/omitted content,
partial history and unknown execution stated explicitly. **Source and coverage**
shows the fixed reference and association IDs. Text, references and reading
selection are not written to local storage. Reading creates no operation, Work,
Attempt, approval, PM context or model request, and leaves original CLI sessions
read-only. The source reader's text-range picker above creates text selections;
this Work-side panel only reads them.

An open reader rechecks its loaded pages and selected excerpt every 60 seconds.
Refresh, pagination and pending parent Work reads clear previous content before
publishing a new result. Withdrawal, denial, changed identity or failed reads hide
all loaded relationships and plaintext; the user can explicitly retry. Closing
the disclosure/inspector, changing Work, leaving the board or disposing the view
cancels its timer and fences late responses. A revoked relationship cannot be
restored by a still-pending excerpt response. This is Board-inspector coverage;
the standalone Work route and requirement inspector are not new reader surfaces.

HTTP and injected desktop IPC regressions exercise this shared frontend. They do
not establish native WebView, real BAT/model or physical-mobile acceptance. Work
derivation, native execution, Attempt selection, replacement and cleanup after
source revocation remain open alongside the unrevised PM lifecycle requirements.

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
preview size refusals using synthetic Codex/Claude records. Relationship tests use
the real operation service, producer payloads and Attempt reader, including local
rollback, committed receipt recovery, grant withdrawal and HTTP/daemon/MCP parity.
They do not prove a relationship UI, derivation or suggestion capability.

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
