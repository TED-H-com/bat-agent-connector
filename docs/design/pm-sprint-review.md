# Guided Sprint Review

Status: backend and shared Dashboard increment. `sprint_review` remains a contract-only
PhaseProfile. Facilitation is deterministic and human-led; no model or quality
runtime is enabled. Existing `review` meetings remain compatible without the
optional fields below. Daily, Retrospective, reviewers and exports are out of scope.

## Fixed evidence

Create a `review` meeting through `pm.meeting.create`, then use optional
`review_basis` on `pm.meeting.draft`. Existing context pins Sprint Goal, DoD,
selection, snapshot and adopted baseline. A new draft never refreshes that context.
Use `pm.meeting.context.revise` explicitly before drafting against newer context.
Completed records are immutable.

`review_basis` has exactly `version: 1`, `results`, `feedback_items`,
`governance_revision_ids`, and nullable `follow_up_ref`.
Each Result selects `{attempt_id,result_id,digest,assessment_ref}`. The nullable
assessment reference uses the existing exact quality reference. The internal
`pm_attempt_result_reads.read_fixed_receipt` checks original Attempt/Work authority,
receipt identity and operation provenance without a new public read endpoint.
It retains source generation/position, turn digest, candidate commit, package
and original assessment/proof. It never substitutes the newest Result.
Feedback selects `{id,source_ref}`: either `{kind:meeting_note,note_id}` in the
fixed meeting context, or `{kind:discussion_input,thread_id,input_id,operation_id}`.
The server preserves original text including whitespace and Unicode. Arbitrary
quoted feedback in `output.feedback` is forbidden for a guided draft.
Each selected discussion input freezes a boolean `superseded_at_selection`.
An existing successor marks that exact input as historical without replacing it;
later supersession never changes a frozen draft or completed Review. The bounded
`SELECT 1 FROM pm_inputs WHERE supersedes_input_id=? LIMIT 1` uses the existing
unique index. It reads no successor ID, author or content and grants no access
to that successor. Read-time source gates do not refresh the frozen flag.
`follow_up_ref` selects `{intake_id,document_digest,issue_ids}`; governance uses
exact immutable revision IDs. Original IDs/digests and source times are retained.

The immutable `guided_review` draft document records the context digest, freeze
time and journal cursor, resolved sources, requirement contributions and gaps.
Reported completion, verification, requirement acceptance and DoD are distinct.
No Result, missing acceptance/DoD, incomplete coverage, or an inapplicable
assessment produces an unresolved item. Multiple Works for one requirement must
all qualify. A completed review never changes because of a later assessment.
Access to original evidence is still rechecked on every read.

A Result's binding, plan and requirement IDs come only from its original readable
package context and selected package requirements. Rebinding the same Work never
relabels those facts. A historical Result for a different Sprint binding remains
selectable and adds `original_binding_differs`; it contributes neither coverage
nor acceptance to that binding, which retains `result_not_selected` when uncovered.
Aggregation uses the original binding/plan relation and only its represented
requirements. An unproven original relation remains unresolved or unavailable;
Work identity alone is never a substitute.

## Decisions and explicit Planning

Guided draft decisions additionally require an `owner_actor` and `follow_up_ids`.
Owners are current workspace writers; follow-ups use the existing meeting fields.
Each selected decision must reference a fixed governance decision revision at
completion. `pm.governance.record` accepts optional `review_proposal_ref` selecting
`{meeting_id,draft_revision_id,draft_digest,decision_id}`. It must be a `change`
proposal for the same snapshot, decision and owner. Revisions retain this original
source. Typed records/revisions may omit `description`; the server derives the
exact original proposal text without copying source text into a durable request.
Replacing that text or owner requires a new explicit Review draft/proposal.
The actual workspace owner records direction via `pm.governance.decide`.
`pm.meeting.complete` accepts `governance_decisions`, one exact
`{decision_id,revision_id,document_digest}` for every selected decision. The
completion preserves the governance actor, responsible owner and follow-ups;
its recorder does not impersonate the governance actor. Unselected proposals
remain proposals. Follow-up reports use `pm.meeting.follow_up.record`, with the
existing `pm.follow_up` intake path available for later review.

Only an explicit request records a new Idea input with
`source_ref.kind=sprint_review_decision`. Its exact reference selects
`{kind,meeting_id,completion_id,completion_digest,decision_id}`. The input is
unresolved; `value` is the caller’s authored intent, while `source_decision` is
resolved on the server from exact IDs/digests. It retains original sources and propagates
lineage through the existing `pm.thread.plan` path. The output is a `pmd_`
discussion draft. It neither adopts scope nor starts execution.

## Authority, disclosure and replay

Use existing registered actions, observe/manage scopes, resource policy,
coordinator gates and atomic durable operation effects. Generic workspace
membership retains today's administrator override. Governance direction requires
an actual current workspace owner; meeting completion requires the designated
Sprint owner. No membership policy or reviewer assignment changes here.
Original-key replay returns the original operation and resource IDs after restart,
subject to current source access; it does not rerun a start or bypass stale CAS.

All source authority is checked on admission and effect, direct get/list/history,
operation GET/list/replay and event publication. Immutable guided-draft roots
extend existing source lineage. Withdrawal hides the whole derived narrative,
including quotes, decisions, Idea inputs and Planning drafts; stored records,
receipts and keys remain. A principal missing any input sees unavailable/filtered
records, never an excerpt, hidden title, count or an inferred pass.

Refusal order preserves existing transport authentication, scope, parsing and
lineage barriers. Existing action-specific shape, membership and CAS order is
unchanged. For guided sources, validate bounded shape, resolve source access and
integrity, then check exact relationships, semantic completion and ownership,
then whole-document size. Original-key authorization precedes fresh-action CAS.
Every new typed selector is a bounded canonical string: producer-specific IDs
use their exact prefix and 32 lowercase hex digits; document digests use 64 hex
digits, Result digests `sha256:` plus 64, and candidate commits 40. Feedback,
decision and follow-up keys retain the existing stable item-key syntax. Assessment
versions are integers within the existing quality-history bound; actor names keep
the existing bounded text/role policy. Malformed selectors return central
`INVALID_PARAMS` (422) before their cache keys, SQL or source resolution, after
the existing authorization/CAS barriers. Validate complete selector collections
before resolving any member. This refusal is shared by OperationService, HTTP,
RPC and MCP; it never falls through as SQLite ProgrammingError or HTTP 500.
Repeat authoritative checks at effect time and before publication.
`PM_SPRINT_REVIEW_REFERENCE_MISMATCH` is 409 for readable mismatched tuples;
`PM_SPRINT_REVIEW_DECISION_REQUIRED` is 409 for absent governance proof;
`PM_SPRINT_REVIEW_SOURCE_LIMIT` is 413 for resolved-source overflow. Existing
FORBIDDEN, source-unavailable, version-conflict, meeting completeness and
lineage-limit codes keep their existing semantics and transport status mappings.
HTTP uses `OperationError.status`; RPC retains its existing transport error wrapper
and domain code, and MCP carries that code in `ToolError`. No new error registry
or global status remapping is needed for these existing generic adapters.

## Bounds and storage

Hard limits: 20 Results, 20 feedback items, 20 governance revisions, one Follow-up
intake selecting at most 20 issues, 20 authored questions, 10 decisions and
10 follow-ups. Drafts retain the existing 200,000-byte bound; resolved sources
have a 1,000,000 UTF-8 byte bound and wire reads a 2,000,000-byte bound. No silent
trimming, even for CJK text. Narrow selection explicitly after overflow.
Existing lineage limits remain in force. Indexed exact selectors and bounded
history reads avoid transcript scans. Reads perform no writes/provider calls.
Use existing immutable JSON documents, contexts, completion and lineage tables.
The sole new DDL is idempotent:
`CREATE INDEX IF NOT EXISTS pm_meeting_drafts_by_meeting ON pm_meeting_drafts(meeting_id, sequence)`.
No new table, column, trigger, data step or `user_version` is introduced.

## Transports and Dashboard

Use HTTP POST `/api/v1/operations`, RPC `op_submit`, MCP `operation_submit`, and
existing meeting/governance/thread reads. All adapters share central validation;
no new action name, route or native domain authority is introduced. Capability
`features.pm_workspaces.guided_sprint_review` version 1 advertises the complete
fixed-input, governance and Planning path only when its validators/gates exist.

The Dashboard increment reuses shared meeting, governance, Idea and Planning
components and English/zh-TW string tables. It shows fixed context, reported versus
accepted outcomes, exact feedback, unresolved items, owner decisions, and finally
an optional Planning action. It adds no alternate scheduler or automatic send.
Use `durableForm` unchanged: persist only its user-authored field values,
idempotency key, original request (exact source IDs and digests only), and operation
ID. Never persist source-derived quoted feedback, Result, assessment, verification
or governance text, counts, labels or titles; reread those from the server and
clear them on withdrawal. No new selection, step-progress or convenience storage
keys. Derive the next step from server records. This is the accepted L7 resolution,
not the earlier round-1 persistence blocker.

Backend fixtures must prove fixed receipts and quality separation, exact feedback,
missing-evidence gaps, actual-owner asymmetry, explicit Planning without adoption,
source withdrawal at every derived stage, original-key restart replay, read-only
paths, exact/+1 count and CJK-byte limits, and unchanged legacy request hashes,
events and responses. Mock BAT/providers and temporary disk SQLite/Git only.
Dashboard journeys exercise HTTP and injected IPC, English and zh-TW, owner and
contributor views, withdrawal, original-key recovery, and all localStorage entries.

The selected Sprint offers “Guide Sprint Review” only with capability version 1.
Discussions/Meetings resumes the same meeting using existing navigation. Reentering
from a Sprint preserves its explicitly selected Review when a newer meeting exists. Six
sections fix context, inspect outcomes, hear exact feedback, choose direction,
record the Review, and optionally request Planning. The next available section is
projected from context, frozen draft, governance revisions and completion; there
is no persisted client progress. Up to three facilitation questions reflect missing evidence, feedback and
decisions; remaining gaps have a count and full source disclosure. Saved narrative is read-only,
never an editable default. Selection reads existing bounded catalogs; incomplete
catalogs are disclosed and selections are never silently trimmed.

Proposal links open the existing governance form with fixed IDs and digests,
assigned actor and snapshot; the server supplies the original description. Actual
owners decide and the designated Sprint owner completes. Completed and read-only
Reviews omit unavailable editing actions and prompts; completed decision
attribution uses the frozen completion without fetching later governance revisions.
Legacy drafts need an explicit guided revision before typed governance/completion.
Reference selection compares scalar identities independently of JSON property order;
this does not rewrite existing durable requests or operation keys. Contributors see an
awaiting-owner state. Completed, governed decisions are explicit source options
in existing Idea forms; their original proof remains visible in the pmd_ draft.
“Draft only; scope not adopted; work not started” marks the Planning boundary.
No adoption, execution, model invocation or extra confirmation is introduced.

Source refusals clear the whole guided narrative and derived editor disclosures,
including other mounted Review/governance panels and derived thread content.
This also applies to source or permission refusals returned by an explicit action;
the unchanged durable form retains the original request/key for reconciliation.
Request generations and selected identities reject late responses. Reflow retains
editor nodes and composition; original durable receipts remain the replay authority.
Legacy meetings without this capability retain their current UI and request shapes.
Older Result catalog paging, model facilitation and the other Scrum events remain
outside this increment. Physical native and real-host acceptance are separate.
