# Fixed owned execution verification

`pm.execution.verify` runs the existing administrator-configured `ObservedVerifier`
against one successful original `repository.continue` with an immutable adopted PM
package. It creates no Task, session or new worktree and changes no ownership record.
The original `execution_sources.resolve` / `artifact_managed` checks remain required.
This supplies a trusted verification receipt for standalone PM work and maintenance
repairs without attaching an unrelated task to their sessions.

## Fixed action and authority

The action requires observe and operate, current workspace writer membership and
the same original controller as the execution. Each new process frame verifies
one current nonrevoked, nonexpired credential covering both scopes, the original
source/controller/package and current lifecycle epoch. Repair descendants also
retain their original policy owner, budget, path and absolute deadline guards.
A successful sent process receipt may reconcile after stop, revocation or config
change; these circumstances never authorize a new process or source read.

Target: `{workspace_id, execution_operation_id}`. Params:
`{candidate_commit, tree_hash, command_digest}`. Preconditions:
`{handoff_contract_digest}`. All identities are exact; extra fields are rejected.
The stable idempotency key identifies one process attempt. Neither argv, exit code,
log content nor a caller-authored verifier result can be submitted.

The trusted command comes from the exact stable central project ID in existing
verification settings, or its explicit stored `task_project` mapping. Similar
project names never select commands. A remote BAT host needs its configured SSH
verification mapping; only explicitly loopback BAT endpoints may execute locally.
The admitted command and transport configuration digests must still match before
execution. The reusable runner compares the expected commit/tree with its clean
Git identity before running and again checks unchanged clean identity afterward.

The entire observation has the configured bounded timeout, reduced by the original
repair deadline. Existing process group termination/reaping remains in use. A
nonzero exit, timeout, changed/dirty source or unavailable identity cannot pass.
No provider billing or semantic acceptance claim follows from an exit code.

## Reads and original receipts

`configuration` is async: GET
`/pm/workspaces/{wid}/executions/{execution_operation_id}/verification-config`.
It returns availability/reason, configured command digest, handoff contract digest,
and bounded read-only current candidate/tree identity. Dirty or unavailable Git
identity returns null hashes and `candidate_reason`. It never runs the command.
Original source and configuration are revalidated after the read. It exposes no argv.

`get_verification`: GET
`/pm/workspaces/{wid}/execution-verifications/{verification_operation_id}` returns
operation status and the original verification projection, or null before a receipt.
The fixed proof reference is
`{verification_operation_id, candidate_commit, tree_hash}` under the existing
`observed_runner_v1` condition with its exact configured command digest. Legacy
TaskService observed-verification references remain supported independently.
Batch prerequisites, quality assessments and maintenance evidence consume this
reference only for the exact original execution and all original source roots.

The durable OperationService process step preserves its original outcome through a
separate local receipt step before outer completion. It records only original
observed metadata; command output stays in the existing private verifier log.
A lost process outcome with no saved receipt stays unknown and is never rerun.
A saved receipt settles the same outer step without executing a replacement.
Historical read access always rechecks the full original source chain.

Shared action installation, lifecycle investment classification, API/RPC/MCP/native
routes, operation visibility and capability advertisement remain integrator hooks.
