# Fixed engineering package handoff (W19 / W38)

This bounded slice extends `repository.continue`; it does not create another
queue, task recipe, agent session controller, or delivery engine. It assigns one
complete immutable engineering package to one existing Connector execution.
The contract is `pm-engineering-handoff/v1`.

## Explicit admission

The existing target, source preview, WorkItem/project preconditions, scopes and
controller authority remain mandatory. Add these optional parameters together:

```json
{
  "pm_package_ref": {
    "workspace_id": "ws_<32hex>",
    "package_id": "pmwp_<32hex>",
    "document_digest": "<64hex>"
  },
  "pm_code_basis": {
    "mode": "implement_fixed_spec_from_selected_base",
    "rationale": "Why this fixed published code baseline is selected"
  }
}
```

The existing linked-work `start` and `manage` scopes, `observe`, current workspace
write membership, original sources and current adopted scope are checked. The
engineering base choice does not acquire scope adoption authority or require a
second owner approval. It belongs to this same explicit dispatch request.

The package's WorkItem, WorkBinding, requirement identities, original work
fingerprint, project and configured repository must match the selected execution.
The numeric repository identity, published ref, full code SHA and binding digest
must match exactly. There is no `latest` resolution and no live PM file lookup.

Specification commits and the implementation starting commit remain distinct.
Existing package readiness calls different SHAs unassessed because it has no
alignment decision. Different SHAs are not evidence of incompatibility. This
request records a caller-selected implementation base with
`compatibility: unverified`; it authorizes implementing the fixed requirements
from that base, not a claim that the base already satisfies the specification.
All other unresolved readiness checks continue to block admission.

The complete original package, source bytes, source identities, requirements,
acceptance conditions, task relationships, design, proposed verification and
delivery descriptions are preserved in the original admission binding and first
prompt. The contract includes its canonical UTF-8 SHA-256. The first prompt must
fit both the existing 20,000-character BAT limit and a 64,000-byte UTF-8 bound,
including user instructions and ordinary artifact instructions. Oversized
contracts are refused whole. Truncating requirements or replacing them with a
mutable filename is not supported.

## Execution and original reconciliation

`source.resolve`, fixed fetch, owned carrier preparation, `session.start` and
`send` remain the existing durable steps. No person's checkout is changed. The
session ID is the existing operation-derived UUID. Its source association is
stored in the operation INSERT transaction before any external effect.

Every newly admitted or proven-unsent retry checks current source access. The
published handoff additionally checks adopted scope, WorkBinding, work
fingerprint and configuration immediately before each unsent BAT frame and owned
Git mutation. The existing client grant/controller/confinement checks remain.
The generic BAT frame hook identifies the original worker; it grants no write.

Lifecycle epochs remain governed by `ctx.step` admission and RERUN restart.
Already admitted bounded steps may finish after a lifecycle stop. The final
frame source check does not change that lifecycle contract.

An uncertain original start/send is reconciled using its original session,
message ID, receipts and transcript. Source withdrawal does not require resending
or preventing internal read-back of an already sent effect. Original local
receipts may be recorded internally while public reads become unavailable. A
successful start is not a verified requirement, Done, merge or deployment.

Readiness keeps `ready_for: engineering_handoff_review`, `execution_available:
false` and `execution_authorized: false`: that read performs no execution.
`dispatch_readiness: separate_fixed_handoff_admission` names the separate command
boundary, with its action, versioned contract, required code-basis decision and
`bounded_full_prompt` materialization disclosed in `handoff` metadata.

## Original-source read inventory

`pm_source_lineage` is an append-only association of central identities to the
original handoff operation. It does not allocate a schema data-step version.
The original fixed package's access policy is evaluated for the current caller;
the original creator's identity is never borrowed for a public read.

| Surface | Authoritative identity and gate |
| --- | --- |
| Operations: get/list, same-key replay, cancel/resume, waited results | Original operation association; shared `pm_access` and final HTTP/RPC response gate |
| API event pages, SSE, Dashboard bootstrap | Event resource and original operation associations; whole inaccessible rows are omitted without resetting scan cursors |
| Session inventory, details/live reads, reading state and instructions | Exact pre-bound `host/session_id`; prefix observations cannot bypass an exact protected session |
| Session read/wait | Source check before observation and again before returning its result, including delayed responses |
| Observation resource/history/relations and worktree views | Exact session/worktree relationship records; no folder/name inference |
| WorkItem, project work lists, result sources and execution candidates | Bound WorkItem and original execution references; inherited source rows are filtered or singular reads refused |
| Integration/checkpoint/repair/cleanup read projections | Explicit original operations, session/relationship identities and named source references |
| Artifact metadata/list/content and native download | Original capture operation through each artifact revision; the whole artifact identity retains all prior source roots |
| Capture previews and captured artifacts | Exact managed creation/accepted execution evidence plus current original-source access, rechecked after awaited reads |
| Artifact acceptance/materialization receipts | Inherited original operation and artifact identities; ordinary upload/download does not create a PM bypass |
| PM evidence of an execution/session | The same source gate in the existing evidence collector, before downstream projections |

Source associations are independent of mutable display names and filesystem
paths. Named central identifier fields and existing typed source selectors are
the only relationships used by the final shared response barrier. It does not
parse prose or infer ownership from UUID-looking text. Only established
collection fields may omit whole rows; an unknown or singular shape containing
hidden sources is refused whole. Fixed PM documents are never silently rewritten
into partial specifications. Whole-row filtering keeps the upstream pagination
cursor and explicitly marks when source visibility filtered the response.

The WorkItem used for an execution becomes a source-bound management projection;
it can no longer expose the derived execution's work summary to callers who
cannot read its original package. Unrelated WorkItems, ordinary artifacts and
legacy sessions keep their previous rules.

## Supported derivatives and limits

Existing session send/answer/interrupt and Connector metadata operations inherit
the original source association. They retain the original session controller and
incarnation checks. `session.read` and `session.position` only save personal
observation state, so trusted source-bound requests are lifecycle records and
remain usable while paused. Original source checks still apply; an external step
cannot use this local record exception. Managed artifact capture/acceptance and local WorkItem
metadata similarly keep the original source roots. New publication, integration,
checkpoint continuation, cross-work links, repair or cleanup writes without a
complete fixed PM handoff contract are refused with
`PM_DERIVED_ACTION_UNAVAILABLE`. Their existence in the older product does not
make this first PM dispatch slice a complete M4 delivery implementation.

Normal PM-domain commands keep their own adopted-scope/source contracts; when
they refer to a source-bound execution they also inherit its read policy and must
belong to the same PM workspace. A caller cannot supply trusted lineage fields.

Next slices still need owned immutable specification materialization for larger
packages, explicit provenance across integration/delivery derivatives, and an
owned published specification carrier for later commits. W36 currently reads the
PM carrier's original source commit. This handoff does not rebase or overwrite
that carrier, fetch missing latest specs, or relabel a PM plan commit as code.

Tests use real disk Git fixtures and mock BAT/provider APIs. No live hosts,
production GitHub writes, model calls or native OS acceptance are implied.
