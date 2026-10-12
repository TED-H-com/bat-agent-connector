# Explicit maintenance candidate sources for Planning

M4 step 13 needs an explicit, source-preserving path from a saved Maintenance
Review candidate to an Idea discussion. This extends `pm.input.record`; it does
not complete a Review, adopt scope, start analysis or dispatch work.

The new `source_ref` has exactly `kind: maintenance_review_candidate`, `review_id`,
`event_id`, `document_digest` and `operation_id`. The event must be an immutable
`candidate.record` from the same PM workspace, with those original identities
and digest. The central reads the original Review snapshot and candidate source
binding under current authority at admission and again inside the final local
transaction. It never substitutes a newer candidate, completion or snapshot.

The destination must be an existing Idea discussion. The writer chooses the
existing PM input domain. The input value is exactly the original candidate
title, its initial status is `unresolved`, and replacement of another input is
not supported by this bridge. Reads include the full original candidate document
(proposal disposition, problem, rationale, options and original sources); a title
is not presented as the complete requirement. Ordinary owner confirmation,
Planning selection, proposal review and adoption remain separate registered
actions. Confirmation never rewrites the original candidate's proposal status.

The original input operation receives a private, server-generated source root
containing the fixed candidate selector. Existing operation lineage propagates it
to confirmations, drafts, analysis, proposals, plans and subsequent work. Root
visibility rechecks the original candidate and complete Review source chain.
Source withdrawal hides derived content; saved local receipts remain immutable
and internal recovery checks the applied receipt before attempting another write.
No new table or migration step is needed; old inputs and Review hashes are
unchanged.

The existing Review candidate view offers an explicit “Add to Idea discussion”
form with a same-workspace discussion selector and PM domain selector. Candidate
identity, quote and rationale are read-only. The durable form freezes the selected
thread/version and original candidate. It retains the original key for lost-reply
recovery and never changes the selection to a newer discussion version silently.
Both locales reuse the compact shared forms and source details.

Validation covers complete source withdrawal before admission/commit and after
derived Planning, wrong workspace/kind/digest/operation, attempted confirmation
or edited quote, current member authority, paused record behavior, original
receipt replay, explicit subsequent owner confirmation, and unchanged unrelated
Idea/work/Sprint state. Browser checks cover explicit selection, frozen requests,
lost replies, stale target versions, hidden sources and EN/zh-TW responsive views.
Offline evidence is separate from BAT installation and human/live acceptance.
