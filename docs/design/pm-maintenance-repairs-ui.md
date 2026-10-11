# Maintenance repair decisions in the shared client

`workspaceMaintenanceRepairs` mounts the source-gated repair collection beside the
maintenance policy. It returns `element`, `refresh`, and `withdraw`; the shared
workspace owns event refresh and full source withdrawal. Both locales share this
component and the existing durable form implementation.

A new request requires an explicit observed Issue and adopted rule. The request
contains the original policy ID, rule key and observation/finding reference; limits
come from that adopted policy. The form preserves the baseline/policy/lifecycle CAS.
Before the first submission, the client rereads the current sources and refuses a
changed context. Recovery uses the saved exact envelope and key even after a stage
change. An unknown PR effect exposes the original publication operation; it never
offers a second publication request.

Repair history and evidence selectors await bounded pages, including empty filtered
pages. Revocation or a malformed page clears source labels, receipts and selectable
references; a late response cannot restore them. Drafts and pending operation keys
remain private to the existing actor namespace. A selected historical repair keeps
its original source IDs and does not automatically follow a newer repair.

The embedded `workspaceExecutionVerification` component uses the original owned
execution and the central's source-derived candidate/tree/command configuration.
Its durable form requires the established `operate` scope. A completed original
verification is selectable only when its recorded target is the repair's exact child
and the configured command digest matches the rule. A recorded reference contains
`verification_operation_id`, `candidate_commit`, and `tree_hash`; it never infers
ownership from session names or paths.

Quality selections bind the saved assessment's review ID, operation ID, version and
document digest. Required-CI selections bind the original observation and exact
native PR identity from the saved creation receipt. These are record operations,
not automatic assessment adoption or release approval. The central independently
rechecks current source access, exact relations and delivery gates.

The policy editor accepts `reviewedHandoff: () => handoff.batchSelection()` and
returns `update()` for handoff changes. Adding a rule captures that exact envelope,
release predicate, narrow Issue labels/paths and a digest of the explicitly entered
configured command. Later previews cannot replace a saved rule. Required native CI
observation is an explicit optional selection. Untouched optional policy fields,
including nested fields unknown to this editor, remain in the original policy;
legacy drafts are upgraded only at matching CAS or explicit version review.

Integration dependencies: shared durable forms with `requiredScope`, the generic
execution-verification component and its quality strings, plus repair/verification
read routes and capabilities. The shared workspace mounts the panel, includes
`pmMaintenanceRepairsStrings` in both locales, imports the CSS, and refreshes it for
repair, policy, observation, quality, lifecycle and relevant operation events.

A saved `outcome=published_for_review` is shown as a PR review outcome. It does
not imply repair acceptance, release publication or runtime termination. Capacity
is projected separately; `PM_REPAIR_RUNTIME_TERMINATION_UNPROVEN` explains why a
successful parent can retain its reservation. A succeeded child alone does not
invent this outcome, and source withdrawal clears the saved result as usual.
