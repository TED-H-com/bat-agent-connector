# Conversation proposals and one owner review

This closes the natural-discussion input gap without creating another plan model.
An explicit `pm.conversation.propose` uses the selected existing bounded analysis
runtime. Ordinary Idea messages remain freeform and never trigger adoption.

The action targets `workspace_id, thread_id`, accepts the ordinary bounded
`purpose, model?, max_chars?, timeout_s?, adapter?`, and requires exact `expected_version`,
`expected_plan_version`, and `expected_accepted_revision_id`. Admission freezes
the complete selected discussion (at most 100 messages/current inputs), selected
draft sources, accepted document and adoption, original source-operation IDs and
quoted reference registry. The complete context must fit 2,000–64,000 characters;
omission is an explicit error, never an apparently complete plan. Accepted plans
with more than 20 requirements or 20 selected inputs retain the existing plan
workflow and are refused before launching this bounded proposal.

Optional `adapter` is exactly `codex-native-linux` or `bat-pm-linux-v1`;
omission preserves the existing native request. Workspace capability
`structured_analysis.version: 1` advertises eligible adapters separately for
this action and `pm.follow_up.analyze`. An explicit BAT selection uses the
[existing BAT PM executor](pm-bat-analysis.md), fixed selected host/workspace
binding, original owned session and original send/readback receipts. No new
executor, automatic selection, fallback or caller-selected output sink exists.
The UI saves this choice inside the existing durable intent and locks it while
recovering that intent. An older server receives the original absent-adapter
request; withdrawing a BAT capability does not convert a saved BAT request to
native. Eligibility is not runtime attestation or successful model execution.

Admission freezes the adapter and source context together. Only the registered
action's trusted callback may create the fixed `conversation_proposal` output
destination. Import recomputes the selected runtime's original completion proof
inside the same transaction that saves the proposal. BAT replay assistant IDs
remain distinct from native app-server item IDs; BAT provenance includes the
original runtime observation digest, terminal and process-death references.
Public proof projects explicit fields, while complete raw records remain in
the private journal. Unknown start, send or death retains the original writer;
valid JSON or an ACK cannot release it.

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

Original-source booleans may be reused only within one synchronous SQLite read
snapshot, keyed by connection, actor, scopes, admin authority and credential ID.
Public metadata with an empty original-source set does not create an authority
scope. An anonymous caller cannot read a nonempty original-source set, including
inside another caller's active scope; ordinary route authentication remains in force.
No parsed document, live eligibility, freshness or final-effect decision is
cached. A write, transaction replacement, event-loop yield or foreign task
invalidates the scope with `PM_SOURCE_READ_CHANGED`; callers must start a fresh
read. Closed or copied scopes cannot retain permission. Original-source and
conversation recursion have separate 64-node depth limits, with a shared
2,048-node traversal budget and bounded nested principal scopes; cycles and
exhausted bounds fail closed. Each later independent read rechecks withdrawal,
and checks after awaits or at final effect frames use fresh scopes. Existing
saved receipts and their whole original source boundary remain unchanged.
Maintenance repair roots retain their existing bounded full-source traversal:
saved quality/delivery evidence may refer back through the same owned repair
child. That domain guard includes the complete principal and credential identity;
only that exact in-progress same-parent backedge bypasses the generic root memo,
without caching a partial authorization. Completed outer repair authorizations
and all other original roots keep the shared memo and read-scope bounds.
The repair's complete historical source reader supplies one such scope even
when called directly by an execution guard. Current credentials, policy,
lifecycle, deadline and effect eligibility remain outside that reader; subsequent
frames and independent snapshots never reuse its completed permissions.
Completed positive original-source and whole operation-visibility decisions also
join the shared read memo within that snapshot, keyed by the full principal and
arguments. Synchronous pre-step and final-frame validation open their own read
memo and close it before any write or await; live credentials, policy, lifecycle
and deadline checks still execute on every invocation. A write disables the memo,
including after rollback. Traversal-local refusals, exceptions and partial repair
backedges never populate it, and nested checks retain their existing depth bounds.
Completed nested positives can be looked up without adding recursive loader
frames. Actual cycle and budget refusals mark every enclosing load; a blanket
conversation guard no longer prevents unrelated completed reads from being
shared. Traversal-dependent local answers also mark subsequent enclosing loads,
so ignoring a refusal cannot turn it into a request-wide grant or denial.
Raw canonical operation columns, action/table metadata and complete immutable
lineage roots may be reused even inside a traversal guard: none depends on a
partial source grant. Callers still decode and authorize the original records;
parsed source documents and live/effect decisions are not stored by these readers.
The original execution-verifier visibility reader also reuses only a completed
boolean for its canonical saved operation ID within that same scope. It has its
own 64-node depth allowance so verifier wrappers do not shorten supported root
or conversation histories; the shared 2,048-node budget still applies. Publication
quality-source and visibility reads open one synchronous scope for the complete
original read. They do not cache assessment documents, current acceptance,
credentials, eligibility, or any decision across a provider await or effect.
If a direct verifier read revisits that same verifier through its original
repair's saved quality receipt, only an exact active repair source guard for the
canonical verifier's immutable lineage may terminate the edge. The complete
principal and credential must match; this partial verifier grant is not memoized.
The outer verifier read must still finish every original source check before
its completed boolean can be reused.
Quality history access follows the same rule: reload the canonical review and
its bounded original history on every call, retain the history-depth return
value, and reuse only the completed source/proof boolean. Caller projections
cannot supply a different context for the same review ID. Quality source nodes
have an independent 64-node depth allowance within the shared traversal budget;
the exact active repair backedge remains uncached. Current qualification,
responsibility, revocation and effect eligibility are not memoized.

No automatic execution, maintenance dispatch, scope adoption from model output,
schema data step, live model acceptance or hardware acceptance is implied.
HTTP, RPC, MCP and native reads expose the same central operation/read model.
