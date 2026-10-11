# Fixed plan differences and work-impact candidates

This is the first W11 domain from [PM v1.2](../product/pm-v1.2-plan.md), sections
7.1, 8.2 and 10. `pm_plan_diff.py` compares two explicit immutable plan documents
and selected immutable work associations. It does not adopt a direction, evaluate
delivery, update work/spec/attempts, create issues, dispatch or call a model.

## Read interface

```python
compare(
    ops, principal, workspace_id, from_revision_id, to_revision_id,
    binding_ids=(), journal_cursor=None,
)
```

The workspace and revision IDs are strings. Both revisions must belong to that
workspace; either may be a draft, an old adopted revision, or the same revision.
No current accepted pointer or later adoption is substituted for an endpoint.
There is no mutation action, installer or new table in this module. API and MCP
entry points can share this read without granting execution or manage authority.

Every read requires `observe` and current workspace membership, including reads
of previously compared history. Owner, contributor and reader membership can
read the same shared PM documents. Cross-workspace revision/binding IDs return
not found after checking the requested workspace's membership.

`binding_ids` is a list or tuple of at most 100 distinct `pmwb_<32 hex>` strings;
the default is empty. It selects those exact historical bindings, including a
binding later superseded by another. No latest-binding query, project-name
matching, mutable work query or provider call expands this selection. ID order
does not affect the result.

`journal_cursor` is a nonnegative integer no greater than the central journal
head; booleans are not integers for this contract. Omitting it selects the current
head once inside the read transaction. The result returns that cursor for an
exact repeat. Each revision and selected binding must have one retained creation
event for its original operation, workspace and identity at or before this cursor.
Later sources produce `PM_DIFF_SOURCE_AFTER_CURSOR`; absent or ambiguous creation
evidence produces `PM_DIFF_CURSOR_UNPROVEN`, both HTTP 409 domain errors.

This is a creation boundary, not replay of mutable historical work. It uses
durable event order rather than wall-clock timestamps. Event retention can make
an earlier cursor unprovable; the reader reports that limitation and does not
guess from today's work version or a creation timestamp. The original immutable
documents and bindings remain untouched.

## Result and fixed comparisons

The result is `{version: 1, workspace_id, comparison}`. `comparison` contains:

| Field | Meaning |
| --- | --- |
| `from_revision`, `to_revision` | Fixed IDs, document/structured-scope digests, source thread/draft, parent/base revision, creator, time, original operation and creation-event sequence. These are creation facts, not current acceptance status. |
| `changes.requirements` | Added, removed and changed stable requirement IDs, exact before/after records, changed field names, unchanged IDs and original ordering. Modified requirements include their acceptance-source differences. |
| `changes.sources` | Exact selected InputRecord additions/removals/changes and order, plus complete `unchanged` records needed to attribute acceptance and unmapped sources. Original status, wording, source reference, confirmer and supersession identity remain intact. |
| `changes.domains` | All eight domains, each with a changed flag and both exact attributed sections, including unresolved coverage. |
| `changes.title` | Exact before/after title and changed flag. |
| `changes.markdown` | Content digests and one exact replacement span; changed prose is always unclassified. |
| `changes.unclassified_sources` | Original unmapped source IDs on each side, without fabricating a domain or requirement. |
| `scope` | Boolean document identity, structured digest and recorded-content changes; semantic impact remains unassessed when content changes. |
| `impact_candidates` | One assessment for each explicitly selected binding, as described below. |
| `context` | Server ID, exact journal cursor, selected binding IDs and selection basis. |
| `comparison_digest`, `context_digest` | Canonical SHA-256 identities for fixed plan comparison and the complete returned comparison context. |
| `coverage`, `execution_authorized` | Explicit partial coverage and `false`; no decisions or execution follow from this read. |

Collection differences use stable IDs, not guessed text similarity. A renamed
requirement is an addition/removal; a newly confirmed input retains its explicit
`supersedes_input_id` rather than rewriting the old assumption. Acceptance rows
retain source IDs and original status; the shared `sources` comparison supplies
their complete original InputRecords. Absence of acceptance criteria is not a
passed criterion, and unchanged assumptions do not become confirmed facts.

Markdown replacement uses Unicode code-point offsets `prefix_chars` and
`suffix_chars`, with exact `removed` and `added` strings. Applying that single
replacement to the old fixed text reproduces the new fixed text. The linear-time
method intentionally does not parse prose, produce a semantic requirement, or
claim an optimal multi-hunk diff. The digests hash canonical JSON string bytes,
using UTF-8, sorted object keys and no insignificant separators.

The partial structured-scope digest covers the existing governance/scope/quality
and requirements model. Differences in title or Markdown remain
`prose_scope_impact: unknown`; even apparently contradictory scope text or a
claim that work is complete is preserved without automatic adoption, backfill
or verification. Schedule, cost, resource or risk statements may also matter to
a decision; all eight domain differences stay visible.

Different document revisions can have identical recorded content despite
different document digests, because fixed files contain revision identity and
creation context. Their comparison reports no recorded content change. The
comparison does not infer a new scope revision from that identity difference.

## Explicit work-impact candidates

Each candidate includes the original binding, source work version, project/work
IDs, bound plan revision, requirement IDs, recording actor/operation/reason and
creation cursor. The binding did not save a title or work state: `title` is null,
`title_status` is `not_saved_in_binding`, and `work_state` is `unknown`. Later work
titles, state, attempts or verification do not enter this historical comparison.

A binding to either comparison endpoint can identify directly changed
requirements. Explicit `depends_on` edges in either fixed plan identify
transitive dependent requirements as review candidates. Changed domains,
source selection, ordering or prose can also warrant review, with their effect
unclassified. No dependency walk estimates effort, delivery dates or failure.

A binding to a different plan, or without a plan/requirement association, has
insufficient association coverage. A matching requirement string in another
revision does not silently rebind it. An old binding itself is not scope drift
and cannot prove which spec an execution used. Every binding retains
`execution_spec: not_assigned` from the association contract.

`assessment` is `review_candidate` when reasons exist, otherwise
`no_identified_change`. Both retain `semantic_impact: unassessed`; absence of an
identified change is not proof of unaffected work. Suggested action is review
of the existing work, with `work_update_authorized: false`. A later explicit
decision and existing operation must handle any actual re-association or work
update without rewriting old attempts or producing duplicate issues.

## Stability, bounds and incomplete coverage

The same two revisions, binding IDs and cursor return the same comparison while
membership and retained creation evidence permit the read. Advancing accepted
heads, adding discussion notes, updating work, or creating newer bindings cannot
change it. `comparison_digest` excludes cursor/work context, so fixed plan
differences remain identical even when a new comparison context is selected.
`context_digest` covers the chosen bindings and watermark as well.

Only selected bindings are covered; unselected work is not scanned or counted.
Delivery, acceptance verification, semantic scope impact and cost are not inferred.
The module verifies immutable document bytes and structured digest before use.
The complete response is limited to 2,000,000 UTF-8 bytes; an oversized comparison
fails with `PM_DIFF_TOO_LARGE` rather than returning a truncated change list.
This domain is a source for subsequent PM discussion and UI, not the complete
W11 decision/follow-up workflow.

## Shared client entry points

HTTP GET `/api/v1/pm/workspaces/{workspace_id}/plan-comparison` and MCP/RPC
`pm_plan_compare` invoke the same read. Both revision IDs are required; HTTP
encodes optional binding IDs as one comma-separated query value and a fixed
integer journal cursor. Unknown/duplicate query keys and unbounded lists fail.
The Tauri central bridge permits only these exact paths and bounded parameters.

Web and desktop show comparison below the plan versions in the same discussion.
The reader explicitly selects endpoints and optional work associations. Rechecking
preserves the original cursor; changing a selection clears the previous result.
Plain text rendering preserves source content without interpreting embedded HTML.
