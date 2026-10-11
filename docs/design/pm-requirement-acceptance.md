# Original requirement acceptance dependencies

`accepted_requirements_v1` is a separate result condition with
`kind: requirement_acceptance`. Existing fixed revision verification profiles
retain their original meaning and cannot approve a requirement. Quality review
criterion policies still accept only fixed verification profiles, so acceptance
reviews cannot recursively cite another acceptance review as independent proof.

The condition freezes `package_ref`, the original plan's `revision_id`,
`document_digest` and `scope_revision`, and a bounded nonempty `requirements`
list. Each row contains an original `requirement_id` and the complete sorted
`acceptance_input_ids`. Server admission compares this whole condition with the
original immutable upstream package. It never invents acceptance text or drops
unmet criteria.

An explicit batch evidence record selects one exact original quality assessment
reference (`review_id`, assessment `operation_id`, `version`, `document_digest`,
`candidate_commit`). Its original child, package and plan must match. The whole
review, including original DoD, must currently qualify and still be the selected
original conclusion. Reviewer responsibility, source access, current work binding,
adopted scope and every independent original verification are rechecked. An old
historical accepted outcome remains readable but cannot release new work after
revocation, supersession, a new assessment, proof loss or scope drift.

The existing batch proof ledger records this reference. The existing child
reservation and every unsent child frame recheck exact prerequisite proofs.
An unknown sent effect retains its original recovery identity. No new queue,
execution engine or implicit latest-review lookup is introduced.

## Shared readiness hook

`pm_requirement_acceptance.require_dependencies(ops, principal,
downstream_package, references)` returns only the proven original prerequisite
requirement IDs. `None`/missing entries remain unassessed. `references` is the
optional `pm_requirement_quality_refs` request map from requirement ID to
`{condition, reference, execution_operation_id}`. It contains identities only.
The condition must name the same original accepted plan and cover that exact
prerequisite requirement. Other readiness checks remain independent.

The repository request accepts optional `pm_requirement_quality_refs`; package
readiness reads accept the same map as `quality_refs` across HTTP, RPC and MCP.
HTTP uses one JSON-encoded query value with a 16 KiB decoded UTF-8 bound, a
64 KiB encoded request-head bound on this exact read route, and the unchanged
16 KiB header-field bound. The native bridge admits only this exact bounded GET.
Reads execute no verification command and write no journal records.
The exact map is frozen at original child admission and rechecked before every
new frame. Historical children retain original-source visibility after later
revocation; their old identity cannot authorize a new start. For intra-batch dependencies, the batch derives
this map only from the explicitly recorded original upstream proof and stores it
in the existing immutable child request. Reading a historical child uses that
stored request; later evidence never rewrites its provenance.
