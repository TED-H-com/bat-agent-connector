# One review for a sourced conversation update

The shared `planProposals` component lives in the existing PM discussion. It uses
the workspace's ordinary forms, typography, spacing and source disclosures; no
new tab, raw JSON editor, model runner or delivery engine is introduced.

`pm.conversation.propose` freezes the current discussion and plan head. Its result
is selected by the original proposal ID in the saved run outcome. Invalid
structured output retains its raw reply with null document and digest; it has no
adoption form. A reviewed update contains every
original candidate key, the owner's edited text/status/inclusion, structured
requirements and explicit removed requirement IDs. Source references remain
fixed and display their original literal quote. The owner submits the whole
review with one rationale and one `pm.conversation.adopt` operation.

The UI shows requirements removed from the original accepted document by ID and
text, and recalculates that list when a requirement is deleted or renamed.
Missing removal coverage fails closed. The review requires included information,
at least one acceptance criterion per requirement and acyclic dependencies.
Title/rationale and stable ID bounds match the central contract. Suggestions default to confirmation only
as part of the explicit owner decision; assumptions, unknowns, conflicts and
excluded candidates retain distinct defaults. No repository-verification status
can be entered. Adoption remains separate from execution authorization.

The ordinary durable form preserves the original envelope/key across an unknown
reply. Known IDs are read back. New discussion/plan versions disable a fresh
adoption and retain the edited draft; they never rewrite an original request.
Editing another proposal preserves the prior draft and rationale. Source read
failure removes the whole review, including summaries, quotes, requirements and
counts. Original request recovery stays in the principal/connection form store;
a late read cannot repopulate a withdrawn view.

## Mount contract

Current PM availability and the last run outcome are independent facts. Composer
feedback labels a settled outcome as this discussion's last PM run. When the
operation supplies `updated_at`, show its timestamp as "status updated", never
as an inferred completion time. Missing timestamps stay absent. Active runs keep
their existing running state and elapsed clock; neither outcome nor timestamp
establishes current PM availability.

```js
planProposals({h, t, api, alive, form, input, labelled, caps,
  workspaceId, threadId, thread, permitted, refresh, errorBox})
// => {box, update({head, capabilities, page}), refresh(), withdraw(), dispose()}
```

`thread()` returns the current ordinary discussion result and its write/decide
capabilities. `permitted(true)` is the existing owner decision gate. `caps()`
contains the registered actions/scopes and
`features.pm_workspaces.conversation_proposals.version === 1`.
`update` receives the same workspace capability descriptor as native analysis;
preparation requires `native_analysis.eligible_for_preflight`, and a false
`plan_adoption_available` explains the missing PM worktree. Both operations
require manage/start. The component reads bounded proposal pages and the current
plan head, then rechecks the selected original proposal before displaying it.

Merge `pmPlanProposalStrings` into the existing English/zh-TW translations.
Refresh on the central proposal event and call `withdraw` at the same source or
workspace access boundary as the existing PM views. The root integrator owns
shared mounting, API/MCP/native routes and generated frontend assets.

Mounted offline fixtures cover natural-purpose preparation, one complete review,
ordinary requirement selectors, explicit exclusions/removals, original-key
recovery, stale versions, draft restoration, source withdrawal, invalid output,
read-only access, pagination and older-central compatibility. Screenshots use
the real PM shell at 390/768/1440 widths in both locales. These fixtures do not
claim a live model or installed native flow; backend composition and full
workspace integration are verified separately.
