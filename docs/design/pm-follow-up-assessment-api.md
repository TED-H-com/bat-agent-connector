# Follow-up assessment entrypoints

The existing `pm.follow_up.analyze` action submits one bounded native analysis of
one fixed intake. It uses the ordinary operation endpoint, original request key
and current principal. This layer exposes the core assessment without adding a
second runner, state machine, chat message or scope-adoption action.

| HTTP GET under `/api/v1` | RPC / MCP |
| --- | --- |
| `/pm/workspaces/{workspace_id}/follow-up-assessments?intake_id&after&limit` | `pm_follow_up_assessments_list` |
| `/pm/workspaces/{workspace_id}/follow-up-assessments/{assessment_id}` | `pm_follow_up_assessment_get` |

Workspace IDs are exact `ws_<32 lowercase hex>`; assessment IDs are exact
`pmfa_<32 lowercase hex>`. The optional `intake_id` is an exact existing
`pmfu_<32 lowercase hex>`. `after` defaults to zero and `limit` to 50 (1–100).
HTTP sequence queries accept at most nine decimal digits. Unknown, repeated or
blank query values are rejected; detail accepts no selectors. RPC preserves the
domain's type validation and does not coerce strings or booleans into numbers.
Native routing admits only these GET paths and matching query keys, with no
request body or operation key. All processing stays in the Python authority.

Lists return immutable assessment metadata, validation state and a cursor; detail
returns the original raw output, optional validated document, their digests and
native provenance. Format-valid citations establish references, not verified
conclusions. Invalid structured output remains explicitly invalid. Neither read
changes an accepted plan, completes a meeting or grants execution authority.

All reads require `observe`, current workspace membership and current access to
the **original** intake's sources. An explicit intake filter also checks that
intake. Unfiltered lists may return an empty page with a forward cursor. Consumers
must follow every non-null cursor and must not replace a withdrawn original with
a newer readable intake. Core responses retain their bounded UTF-8 contract.

The shared operation filter routes the exact `pm.follow_up.analyze` action before
the older preparation prefix. It reads durable admission references even before
any run or assessment exists. Assessment events resolve the original operation;
ordinary operation GET/list, waited submission, exact-key replay and event feeds
retain the same source gate. Withdrawal cannot turn an admitted request into a
new unsent request: a refused replay keeps its original key and internal receipt.

`features.pm_workspaces.follow_up_analysis` means the action is registered.
`pm_capabilities_get.follow_up_analysis` adds current caller authorization and the
existing cheap native installation eligibility. Capability reads spawn no process
and do not verify authentication, model execution or runtime restrictions. Actual
verification belongs to each bounded run. This capability is `context_only` with
`output_destination=follow_up_assessment`; the generic Follow-up PhaseProfile does
not become an executable discussion mode.

The canonical Connector skill documents the fixed-intake action and both read
tools. Hermes and Grokbot bundles are generated from that same source and retain
the canonical digest and version metadata.
