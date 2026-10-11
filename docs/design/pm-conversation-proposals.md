# Conversation proposals and one owner review

This closes the natural-discussion input gap without creating another plan model.
An explicit `pm.conversation.propose` uses the existing bounded native analysis
runtime. Ordinary Idea messages remain freeform and never trigger adoption.

The action targets `workspace_id, thread_id`, accepts the ordinary bounded
`purpose, model?, max_chars?, timeout_s?`, and requires exact `expected_version`,
`expected_plan_version`, and `expected_accepted_revision_id`. Admission freezes
the complete selected discussion (at most 100 messages/current inputs), selected
draft sources, accepted document and adoption, original source-operation IDs and
quoted reference registry. The complete context must fit 2,000–64,000 characters;
omission is an explicit error, never an apparently complete plan. Accepted plans
with more than 20 requirements or 20 selected inputs retain the existing plan
workflow and are refused before launching this bounded proposal.

The model returns version 1, title, summary, candidates, requirements,
removed_requirement_ids, and zero to three questions. Each of at most 20
candidates has a stable key, one of the eight PM domains, text, kind
(`proposal, assumption, unknown, conflict, excluded`) and original source refs.
Requirements retain stable IDs, source/acceptance candidate keys, priority and
acyclic dependency IDs. Every removed accepted requirement must be listed
explicitly. Unknown keys, invented citations, duplicate JSON keys and invalid
Unicode fail format validation. Raw text and original native proof remain saved;
invalid proposals have `needs_review` and cannot be adopted. Valid structure
does not verify the model's conclusions.

`pm.conversation.adopt` requires a current owner with observe/manage/start.
It targets the same workspace/thread, with `proposal_id`, `review`, `rationale`,
and the original discussion/plan/accepted/proposal-digest preconditions. Review
contains title, every original candidate exactly once as
`{key,text,status,include}`, requirements and explicit removed_requirement_ids.
Status uses the existing input values except `repo_evidence`: owner edits cannot
invent repository verification. Source refs cannot be replaced. The whole
review is one explicit decision; new confirmed_by fields identify that owner,
never the model or an inherited prior confirmation.

The registered parent composes existing registered `pm.input.record`,
`pm.thread.plan` or `pm.thread.plan.revise`, `pm.plan.draft`, and `pm.plan.adopt`
children. Each has one private immutable relation, request digest and original
key. Included rows populate the existing eight-domain plan and requirements;
excluded rows remain attributed candidates with typed review disposition. A
later PM context therefore retains the explicit exclusion and its rationale.

Every new child, local mutation and final host frame checks the original owner,
current credential/controller, original source visibility, lifecycle epoch,
whole review digest, owned Git binding and expected thread/plan progression.
Unrelated intervening changes stop new work. Already saved inputs/drafts remain
unadopted history if the final adoption fails. Git uncertainty retains the
original operation/writer and exact readback; no replacement key or blind resend
repairs it. Original committed adoption receipt recovery precedes new-effect
checks, including after later withdrawal or cancellation.

Proposal detail/list, raw run/operation/event reads and derived inputs, plans and
snapshots retain the whole original source boundary. Access traversal is bounded,
memoized and cycle rejecting. Shared UI renders original quotes literally,
allows editing/exclusion and requirement choices, shows removals and uncertainty,
and provides one final adoption action. Conflicts preserve drafts and original
operation keys; no automatic rebase or latest-baseline substitution occurs.

No automatic execution, maintenance dispatch, scope adoption from model output,
schema data step, live model acceptance or hardware acceptance is implied.
HTTP, RPC, MCP and native reads expose the same central operation/read model.
