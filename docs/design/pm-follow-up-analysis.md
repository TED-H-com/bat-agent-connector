# Fixed Follow-up PM assessment

This slice extends the [fixed Follow-up intake](pm-follow-up.md) with one bounded
PM analysis on the explicitly selected runtime. It supports v1.2 W33 and W41: use the information already
collected, explain material problems and deviations, compare tradeoffs, and ask
zero to three consequential questions. This is an unadopted PM assessment. It
does not confirm inputs, change scope, adopt a plan, complete a Review, dispatch
coding work, or verify a delivery.

The behavioral boundary supports A33 (quoted material cannot dispatch), A35
(unadopted product suggestions remain suggestions), A43 (original information
status and provenance survive a new PM turn), and the analysis portion of A44/A48
(conflicts and proposed next owners remain visible without replacing a decision).
It does not alone complete those broader lifecycle acceptance scenarios.

## Original intent and context

Registered action: `pm.follow_up.analyze`. It requires `observe`, `manage`,
`start`, and current workspace writer membership. The original caller and the
workspace PM execution controller remain different identities.

```json
{
  "action": "pm.follow_up.analyze",
  "target": {"workspace_id": "…", "thread_id": "pmt_…"},
  "params": {
    "intake_id": "pmfu_…",
    "purpose": "Compare the fixed outcomes with the adopted commitments",
    "max_chars": 64000,
    "timeout_s": 300
  },
  "preconditions": {
    "expected_version": 7,
    "expected_intake_digest": "full original intake SHA256"
  },
  "idempotency_key": "original-intent-key"
}
```

`expected_version` is the selected discussion's version, not the Follow-up head.
Only an existing Idea or Planning discussion in the same workspace may be
selected. When the intake already names a discussion, the target must match.
Otherwise any currently writable discussion in that workspace can be selected.
This association neither changes its mode nor grants control of another session.
Workspace members retain the existing shared discussion authorization rules.

Purpose is 1–4,000 characters. Optional `model` is 1–128 characters. Optional
`max_chars` is an integer 2,000–64,000, default 64,000; `timeout_s` is 1–3,600,
default 300. Optional `adapter` is exactly `codex-native-linux` or
`bat-pm-linux-v1`; omission keeps the existing native behavior and request bytes.
Unknown fields, extra preconditions, paths, auth sources, tools,
runtime profiles and caller-supplied output are rejected.

Admission freezes the complete intake document, original source access refs,
reference registry, purpose and Follow-up PhaseProfile in private operation
metadata. It never adds server fields to the original public request. The full
context must fit its budget; otherwise admission returns 413. There is no silent
omission of an existing answer. Current thread version is rechecked before the
first run is created; later notes, new intakes, latest plan heads or a changed
profile cannot replace an admitted context. No latest thread message history is
included. The Follow-up profile remains contract-only in the ordinary discussion
mode registry; this action supplies the bounded analysis implementation without
adding `follow_up` to `EXECUTABLE_MODES`.

## Selected execution and receipt recovery

Workspace `structured_analysis.version: 1` exposes per-action native and BAT
descriptors, separately from the retained native-only `follow_up_analysis`
capability. Selection is explicit and saved with the original operation. For
BAT, admission also freezes the existing PM host/workspace/repository binding.
The [BAT PM executor](pm-bat-analysis.md) uses its existing managed start, send
fence, replay, native completion and original process-death proof. It receives
only trusted prepare/import callbacks for this fixed assessment destination.
No raw caller output, caller-selected sink, second queue or automatic fallback
is introduced. The original operation key cannot change its selected runtime.

For native selection, the action uses the existing native PM execution, generation writer, durable
operation steps, process identity and native journal. There is one original
launch intent and at most one native send. It requires the pinned runtime's
isolated context-only capability with no external tools, MCP servers, skills,
repository reads or environment access. Raw native events are stored unchanged;
they are never parsed as BAT events. No second engine or background queue is
created.

The selected runtime's reducer must prove the original user echo, request/client IDs,
native thread/turn/message, terminal assistant content and complete event stream.
The original process must also be proven dead before releasing the writer or
importing a reply. ACK, process exit and valid JSON alone are insufficient.
Cancellation closes only the original owned process and waits for its exit;
restart recovery inspects its exact identity and never kills or relaunches an
unknown PID. Uncertain execution retains the original operation and writer.

The freeze and import are local journal transactions with durable receipts.
Recovery of a committed import remains possible after source or caller authority
changes, without creating another assessment. No new import may occur after
cancellation or source/write authority loss. Proven process death then permits an
aborted run without a fabricated response. An aborted run stays aborted after
later reducer or format changes. Native failures do not automatically send a
second request or reopen a terminal operation.

## Separate raw reply and structured assessment

Output goes to append-only `pm_follow_up_assessments`, with one row per original
operation/run. The ordinary chat importer explicitly refuses this destination.
Nothing is inserted into `pm_messages`, so later unrelated chat contexts cannot
lose the original private source boundary. The assessment keeps the raw native
reply unchanged and its SHA256 even when the PM format is invalid.

The PM-only envelope has these exact fields; coding-agent output formats are
unaffected:

```json
{
  "version": 1,
  "summary": {"text": "…", "source_refs": ["knowledge.scope"]},
  "findings": [{"kind": "assumption", "text": "…", "impact": "…", "source_refs": ["knowledge.finance"]}],
  "tradeoffs": [{"decision": "…", "options": [{"label": "A", "consequence": "…"}, {"label": "B", "consequence": "…"}], "source_refs": ["knowledge.scope"]}],
  "questions": [],
  "recommendations": [{"text": "…", "proposed_owner": null, "source_refs": ["knowledge.quality"]}]
}
```

Findings are `problem`, `deviation`, `assumption`, `unknown` or `decision_effect`.
Limits are 20 findings, 10 tradeoffs, zero to three questions, and 10
recommendations. Each question contains `question`, `why_now`, `options` and
`source_refs`; each option has `label` and `consequence`, with two to four
choices. Empty arrays are valid. Every statement cites 1–20 distinct entries in
the original server-owned reference registry. The registry maps a name to an
original intake JSON pointer and subdocument digest; model-supplied URLs or IDs
cannot become trusted citations. An owner is a proposal, not an assignment.

Text and list bounds, exact fields, valid Unicode, duplicate JSON keys, finite
values, reference membership and question counts are checked in code. A format
failure preserves the raw reply and returns `validation.status: needs_review`
with a bounded error; it does not trigger a repair turn. Valid structure is
`format_valid`, with `conclusions_verified: false`. Reference existence is not
proof that a conclusion follows from that reference. The assessment never
invents confirmed requirements or turns recommendations into accepted SHALLs.

Provenance includes the fixed intake/context digests, original native event
identities and digests, actual model, profile, prompt digest, and original process
proof. Public process identity is only PID, start ticks and a boot identity
digest; config is only its digest. Public process proof selects explicit receipt
fields and excludes future diagnostic paths, environment, stderr or spool data.
The untouched native records remain in the internal journal.
BAT proof separately retains replay message and native app-server item identity,
the original send/observed cursor, runtime observation sequence/digest, and
source/binary identities. Its nested process-completion proof is also projected
onto explicit public fields; diagnostic paths and environment fields from raw
runtime observations cannot enter the assessment or public run provenance.

## Read and authorization boundaries

Internal reads are:

- `get_assessment(ops, principal, workspace_id, assessment_id)` returns
  `{version, workspace_id, assessment}` with IDs, raw reply, optional structured
  document, validation, provenance, actor/time and explicit unadopted semantics.
- `list_assessments(..., intake_id=None, after=0, limit=50)` returns
  `{version, workspace_id, assessments, next_cursor, coverage}`. Entries are
  bounded metadata, excluding raw reply, document and provenance. Cursors use
  durable insertion sequence, including visibility-filtered rows, rather than a
  wall-clock timestamp. Filters never skip the next unconsumed row.

Every public assessment, run detail/list, original operation read/replay and
derived event checks current membership and the original intake's complete source
visibility, including queued operations with no run yet. A later public intake
or a new plan head cannot substitute for a withdrawn source. The whole derived
result is hidden when any original source is unavailable; no partial raw reply is
returned. Operation control still requires the original requester and existing
write scopes. Internal already-committed receipt readback is separate from these
public visibility checks.

Responses are capped at 2,000,000 actual UTF-8 JSON wire bytes. Assessment detail
is complete or returns 413. Metadata pagination is bounded by record count and
the wire cap. Run lists preserve whole-record prefixes and advancing cursors;
a single record that cannot fit returns 413. All new DDL is idempotent, append-only
and allocates no `user_version` or data migration step.

## Shared discussion interface

The workspace displays saved assessments inside the selected Follow-up
preparation. It does not add another dashboard tab. Selection reads one exact
assessment; the source disclosure resolves references against that same intake,
and raw native content is rendered literally. Format validation never appears as
verified conclusions or adopted scope. An inaccessible selection is cleared and
is not silently replaced with another assessment.

The optional analysis form fixes the intake and its digest, an existing writable
Idea/Planning discussion, and that discussion's version. An already bound
discussion cannot be changed. Unbound intake selection uses the discussion API's
string cursors. Draft storage includes the original actor namespace, intake and
thread target. A new discussion version requires an explicit draft rebase;
ambiguous or acknowledged operations keep their original body, key and receipt.
Analysis submission shares the same durable form used by other PM commands.
With the versioned structured capability, this form also saves the selected
adapter. Recovery locks the original selection; capability withdrawal leaves
the draft blocked and does not silently switch it to another runtime. An older
server retains the absent-adapter wire format.

Source or workspace authority withdrawal observed by either child clears the
parent's quoted intake facts and both children. Drafts and original operation
keys remain saved. A late response from an earlier intake cannot clear or restore
the currently selected preparation. New assessment events reread the shared
workspace view through the normal event handler.

## Verification scope

Tests use the real operation service and disk-backed journal with a fake native
process, including full and summary terminal protocols. They cover the frozen
source boundary, raw/structured separation, zero-to-three questions, invented
source references, invalid format, original controller, no chat/adoption/dispatch,
source/member withdrawal, concurrent writers, cancellation, unknown process,
restart, lost receipts, atomic import rollback, exact replay, UTF-8 read budgets
and durable list ordering. This implementation and mocked verification do not
claim a new live model smoke or semantic PM quality acceptance.
