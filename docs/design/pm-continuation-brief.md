# Discussion continuation briefs

This EC-1.1 T6/E09 increment adds a persistent continuation to the existing PM
discussion. It uses the registered `pm.continuation.prepare` action and existing
native/BAT analysis adapters. It creates no executor, assessment database,
conversation replacement, automatic model call or plan adoption.

The explicit action targets one `workspace_id, thread_id`. Parameters are
`purpose`, optional `model`, `timeout_s`, `max_chars`, `adapter` and
`previous_brief_ref: {run_id, document_digest}`. Preconditions freeze
`expected_version`, `expected_plan_version` and `expected_accepted_revision_id`.
The original observe/manage/start scopes, workspace membership, lifecycle,
controller, source visibility and final-frame checks still apply. Only Idea and
Planning currently have an executable PM runtime. Capability eligibility alone
is not proof of a successful model turn.

Admission freezes current decision inputs, the accepted plan and adoption,
readable bound discussion sources, and the newest 100 messages after the selected
previous brief's high-water mark. Current inputs must fit the existing 100-input
bound. Indivisible decision facts and the prior brief must fit the selected
2,000–64,000-character budget. Recent message text fills the remaining budget,
newest first; the coverage preserves omitted loaded IDs, an older-history flag,
prior omissions, the high-water mark and as-of time. A partial prior brief cannot
silently become complete. Old transcript text is not copied into each new model
prompt: the previous document retains only the cited source identities/digests,
while original records and source-access checks remain available.

The output contract is JSON version 1: `goal` (nullable sourced text), `status`,
`candidates`, `completed_evidence`, `open_questions`, `remaining_work` and
`next_steps`. Every entry is `{text, source_refs}`. Next steps are limited to two;
other arrays to twelve, each text to 1,600 characters, the original reply to
16,000 characters. Status distinguishes a next step, a needed decision, no
remaining work in this context, and insufficient information. Partial coverage
or unresolved work cannot claim no remaining work. Completion interpretations
require a fixed quality-assessment source; citing it does not verify the claim.

Adopted and excluded dispositions are server-owned facts, copied from accepted
selected inputs and original owner review dispositions. The model cannot supply
or rewrite `adopted_decisions` or `rejected_directions`. Citing an excluded or
superseded input as active work fails validation. These structural checks do not
prove arbitrary prose is semantically correct; all interpretations remain
unverified, with original source links available for review.

The original `pm_runs.run_id` identifies the version. The immutable
`pm_run_receipts` import/confirmed receipt retains the document, raw reply,
digests, validation and selected adapter's verified completion provenance.
Invalid bounded replies remain `needs_review`, without a usable document.
Oversized original output stays in original runtime events; the review receipt
marks its retained excerpt as truncated. Receipt import and writer release occur
in one transaction. Recovery uses the original operation and process/session;
it does not send a replacement turn. The ordinary assistant-import paths refuse
this structured destination. The only schema addition is an idempotent partial
index for the latest brief; no data step or `user_version` change is allocated.

The existing thread read exposes one newest completed brief, its fixed source
metadata and coverage; raw reply/full context remain on the existing run detail.
Unavailable latest sources produce an unavailable marker, never an older cached
success. Run, operation, event and derived-source reads share the original
visibility gates and bounded traversal. General PM analysis can include the
latest valid brief as a prior interpretation plus messages after its high-water
mark, within the existing context budget. If it does not fit, the context marks
the omission and retains ordinary recent-message handling. Current inputs keep
their original dispositions. Model interpretation never acquires decision
authority by appearing in a later context.

The shared Discussion view uses existing disclosures, source links and durable
forms. The brief is available in the transcript; Context provides the explicit
summary request and adapter choice. Original composer text is untouched. A lost
receipt locks the original previous-version reference and adapter across reload.
Refresh performs reads only. New discussion changes update the stale indication
without replacing an open, unchanged brief. English and zh-TW use the same nodes.

Synthetic comparison on the same example discussion: English desktop
[before](workbench-evidence/ec11-continuation/before-en-1440.png) /
[after](workbench-evidence/ec11-continuation/after-en-1440.png), zh-TW phone
[before](workbench-evidence/ec11-continuation/before-zh-TW-390.png) /
[after](workbench-evidence/ec11-continuation/after-zh-TW-390.png).
Before assets are fixed at `e78e978e`. These are Chromium fixtures; real model
judgment, installed desktop, physical keyboards, Safari and Android acceptance
remain separate from automated evidence.
