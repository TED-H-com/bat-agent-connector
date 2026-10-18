# PM assessment on the live Overview

Date: 2026-10-14. **Proposed contract; design only.** Backend inspection uses
`f5dfa7ad`, the starting main revision of this work. Frontend inspection uses the
read-only #133 integration candidate `b8eb10de01adf16d211872a110d8e9339f120c45`.
References prefixed `#133:` refer to that candidate, not this checkout. Its
publication, merge, installation and live acceptance were not verified here.
All unqualified `file:line` references below refer to the backend baseline.

The owner review asks for a short PM interpretation of the current goal:
what matters most, what fits existing arrangements, what needs watching, and
what needs the owner's decision. Add one compact assessment below the factual
status strip. Register `pm.overview.assess` as a structured sink over the existing
analysis executor and an Overview-only frozen bundle. Store each verified result
as immutable history. Opening or refreshing factual Overview data never spends
model budget. The original design-only document did not authorize implementation or adopt a policy; the later EC-1.1 authorization and implementation scope are recorded below.

## EC-1.1 implementation, October 18

The October 14 inspection and line references below remain historical. The
owner subsequently authorized continuing the full EC-1.1 plan and shipping
reviewed increments. The backend increment now implements this contract on the
integrated Workbench baseline; the card and explicit refresh/history interface
are subsequent increments under the same issue #143.

Version 1 uses the proposed deterministic templates, a visible ordinary carrier,
Live-only placement, one turn, 300 seconds, 32,000 context characters / 256 KiB,
and workspace admission caps of 1/minute, 6/hour and 24/day. These are conservative
implementation defaults under that authorization, not a claim that the owner
separately approved each proposed setting. There is no automatic model work,
measured dollar cap or real model acceptance evidence.

The new `pm.overview.assess` action freezes its selector inside the intent INSERT
transaction, uses the existing structured adapters, and atomically saves its
immutable assessment with the original verified import receipt. New admissions
are counted from original operations across actors. Replay and recovery keep the
original key, adapter and source bundle. Invalid output remains review-only.

HTTP `/api/v1/pm/workspaces/{workspace_id}/overview-assessments` and its exact
`/{assessment_id}` detail route share `pm_overview_assessments_list` /
`pm_overview_assessment_get` with RPC/MCP and the typed desktop allowlist. The
list accepts an opaque workspace-bound `before` cursor and `limit` 1–50
(default 10), returns newest-first metadata and explicit partial/filter flags.
Detail returns the original context and centrally checked freshness.
`pm_context_bundle_preview` accepts `kind=overview_assessment`; the default
accepted-document read stays unchanged. Neither read creates a carrier or calls
a model. Capability and preview report enforced limits and a central retry time.

DDL adds an append-only result table and indexes without a data migration or
`user_version` change. Original source withdrawal applies to operation/event/run
reads and whole assessments, including uncited evidence. Model output cannot
adopt a plan, append assistant prose, change the thread version or start Work.


The registry computes these version-1 template prerequisites centrally; the
provider can only select from the saved `eligible_templates` of each subject:

| Template | Frozen prerequisite |
| --- | --- |
| `goal_blocker` | Visible blocker with explicit current adopted-plan requirement edges and a confirmed goal; basis must contain that goal and one affected requirement. |
| `decision_first`, `prepare_options` | Pending decision in the visible Overview. |
| `confirm_direction` / `review_proposal` / `choose_response` | Pending decision whose original type is plan revision / proposal / governance. |
| `watch_exposure` | Recorded risk or deviation; original severity stays unchanged. |
| `watch_recorded_risk` | Recorded risk in the projection. |
| `watch_recorded_forecast` | A milestone with a recorded forecast, or its explicit later-than-baseline deviation. |
| `clarify_dependency` | Open blocker from a governance record whose actual kind is dependency. |
| `follow_recorded_action` | Governance record with response and assigned owner, affecting requirements explicitly blocked in the current plan. No authority is inferred. |
| `review_evidence` | Visible attention item, milestone or delivery with a resolved original typed source. |
| `insufficient_basis` | Projection/coverage subject; mandatory when no goal is confirmed. |
| `no_priority_identified` | Confirmed goal, no missing/truncated/incomplete coverage and no visible unresolved attention. |
| `await_recorded_evidence` | Reserved vocabulary; no current Overview field proves this state, so version 1 never advertises it for a source. |

Every statement cites 1–4 distinct original registry tokens including its subject.
All computed coverage limitations must be returned; invalid/extra text and
invented quantities are rejected. Decision/handled/watch categories cannot
classify the same subject twice. Format-valid output is still unadopted analysis.


## Verified foundations and gaps

| Verified fact | Evidence and consequence |
| --- | --- |
| Overview must answer what needs a decision; the harness organizes deviations and suggests decisions. | `docs/product/pm-v1.2-plan.md:511`, `docs/product/pm-v1.2-plan.md:811`. This is part of the existing PM product. |
| Outputs use a fixed snapshot; code owns numbers, and model suggestions cannot supply human confirmation. | `docs/product/pm-v1.2-plan.md:629`, `docs/product/pm-v1.2-plan.md:823`. Assessment is interpretation, never a new factual ledger. |
| `pm_overview_get` is an observe/member read of visible central records, without a provider call. | `src/bat_agent_connector/pm_reads.py:123`, `src/bat_agent_connector/pm_overview.py:603`; [Overview contract](pm-overview-discussion-context.md). |
| Overview already supplies goal, acceptance classes, stable attention keys/refs, milestones, recent decisions/deliveries, unknown reasons and per-source coverage. | `src/bat_agent_connector/pm_overview.py:559`, `docs/design/pm-overview-discussion-context.md:13`. Model output must not replace any of these. |
| `as_of` is wall-clock read time, not an event cursor or data revision. Even an unchanged read advances it. | `src/bat_agent_connector/pm_overview.py:572`; `#133:desktop/src/pm-overview.js:348` deliberately excludes it from the render signature. A timestamp comparison alone cannot establish staleness. |
| Source scans and displayed rows have separate bounds; source loss, scan limits and omitted lineage can make coverage incomplete. | `src/bat_agent_connector/pm_overview.py:34`, `src/bat_agent_connector/pm_overview.py:529`, `docs/design/pm-overview-discussion-context.md:123`. Displayed rows are not project totals. |
| The candidate UI separates live facts from saved reports, holds reading position/focus and clears the reader on withdrawal. | `#133:desktop/src/pm-overview.js:17`, `#133:desktop/src/pm-overview.js:327`, `#133:desktop/src/pm-overview.js:393`. Assessment must join these lifecycle hooks. |
| `pm.run.analyze` requires observe/manage/start, owner or contributor membership, a thread and exact thread version. Its ordinary validators have a closed parameter set. | `src/bat_agent_connector/pm_native_operations.py:21`, `src/bat_agent_connector/pm_native_operations.py:26`, `src/bat_agent_connector/pm_native_capabilities.py:7`. Borrow this authority contract for the new action; leave ordinary analysis unchanged. |
| Native analysis already has trusted prepare/import callbacks, original-process reconciliation and a source check before send and import. Its ordinary sink imports prose into a discussion. | `src/bat_agent_connector/pm_native_operations.py:94`, `src/bat_agent_connector/pm_native_operations.py:180`, `src/bat_agent_connector/pm_native_operations.py:220`, `src/bat_agent_connector/pm_native_journal.py:219`. Use a trusted assessment sink instead. |
| `bat_analysis` advertises the separate registered `pm.run.bat.analyze` action; reading eligibility launches nothing. BAT also supports the trusted structured sink callbacks. | `src/bat_agent_connector/pm_discussions.py:391`, `src/bat_agent_connector/pm_bat_operations.py:132`, `src/bat_agent_connector/pm_bat_operations.py:410`, `src/bat_agent_connector/pm_bat_operations.py:513`. There is no arbitrary adapter parameter on ordinary native analysis today. |
| Both existing structured sinks have their own registered manage action, `pm.follow_up.analyze` and `pm.conversation.propose`, and select an advertised backend through `params.adapter`. | `src/bat_agent_connector/pm_follow_up_analysis.py:21`, `src/bat_agent_connector/pm_follow_up_analysis.py:53`, `src/bat_agent_connector/pm_conversation_proposals.py:23`, `src/bat_agent_connector/pm_conversation_proposals.py:59`, `src/bat_agent_connector/pm_analysis_adapters.py:18`. Follow this precedent for `pm.overview.assess`. |
| Structured sinks share trusted admission/prepare/execution/reply verification through `pm_analysis_adapters`; `pm_bat_structured_analysis` is a test module, not another production engine. | `src/bat_agent_connector/pm_analysis_adapters.py:30`, `src/bat_agent_connector/pm_analysis_adapters.py:52`, `src/bat_agent_connector/pm_analysis_adapters.py:66`, `src/bat_agent_connector/pm_analysis_adapters.py:74`, `tests/test_pm_bat_structured_analysis.py:1`, `tests/test_pm_bat_structured_analysis.py:102`. Add one destination mapping, not another executor. |
| Accepted-document bundles already preserve original file digests, adoption/Git evidence, source gates and a 256 KiB ceiling. They still include bounded discussion messages/inputs. | `src/bat_agent_connector/pm_context_bundles.py:11`, `src/bat_agent_connector/pm_context_bundles.py:112`, `src/bat_agent_connector/pm_context_bundles.py:207`; [bundle contract](pm-context-bundle.md). Reusing that context unchanged would import material outside Overview. |
| A fixed context factory is currently allowed only when the action, admission, destination, adapter and digest agree. Runs reserve one workspace writer. | `src/bat_agent_connector/pm_runs.py:296`, `src/bat_agent_connector/pm_analysis_adapters.py:37`. Keep these checks; do not make arbitrary contexts public. |
| Every run needs a carrier thread: `pm_runs.thread_id` is NOT NULL and references `pm_threads`. | `src/bat_agent_connector/pm_runs.py:48`. Reuse its explicit ID from central assessment history; client cache/title matching cannot establish it. |
| Structured-output access and cancellation already dispatch on action names at the event, run, workspace, lineage and operation boundaries. | `src/bat_agent_connector/pm_access.py:152`, `src/bat_agent_connector/pm_runs.py:190`, `src/bat_agent_connector/pm_workspace.py:193`, `src/bat_agent_connector/pm_source_lineage.py:207`, `src/bat_agent_connector/operations.py:677`. Add the new action at each site without making ordinary-analysis gates parse a mode parameter. |
| Native tools/configuration are constrained and checked by the runtime; BAT requires an attested fresh bounded profile. JSON alone is not completion proof. | `src/bat_agent_connector/pm_native_runtime.py:130`, `src/bat_agent_connector/pm_native_runtime.py:504`, `src/bat_agent_connector/pm_native_journal.py:196`, `src/bat_agent_connector/pm_bat_runtime.py:36`. Preserve the limits and their recorded protocol limitations. |
| Follow-up assessments demonstrate a small immutable table and atomic verified-output import. Snapshots preserve an as-of cursor, but Brief facts and filtered history differ from the live Overview projection. | `src/bat_agent_connector/pm_follow_up_analysis.py:31`, `src/bat_agent_connector/pm_follow_up_analysis.py:171`, `src/bat_agent_connector/pm_snapshots.py:37`, `src/bat_agent_connector/pm_snapshots.py:267`, `src/bat_agent_connector/pm_snapshots.py:410`; [Brief exports](pm-brief-exports.md). Reuse the pattern, not the Follow-up intake schema or a reconstructed Brief. |
| Discuss already binds original item refs; proposal review and adoption are separate authorized operations. | `#133:desktop/src/pm-overview.js:77`, `docs/design/pm-conversation-proposals.md:50`, `docs/design/pm-conversation-review-ui.md:7`. The assessment offers Discuss, never Adopt or Close. |
| Operations hash action/target/params/preconditions and check actor/workspace key replay before fresh admission. Uncertain effects reconcile their original receipts. | `src/bat_agent_connector/operations.py:505`, `docs/design/operations-unification.md:130`, `docs/design/operations-unification.md:251`. Action, adapter, bundle selector and limits belong to the immutable request. |

The main-tree `desktop/DESIGN.md` still describes the earlier shell. Placement
below follows the task's fully reviewed Design v2 brief and the inspected #133
candidate. This document changes neither shared tokens nor the other worker's
Settings implementation. Product status remains the recorded baseline in
`docs/product/pm-v1.2-status.md:7`, not a claim about newer GitHub state.

## Reuse and operation contract

Register **one new action**, `pm.overview.assess`, like `pm.follow_up.analyze`.
Its `ActionDef` uses the manage scope with admission/replay/control checks for
observe/manage/start and current owner/contributor membership, plus the existing
authenticated administrator override and original-requester/controller rules.
Both `pm.run.analyze` and `pm.run.bat.analyze`, including their validators and
request contracts, remain byte-for-byte unchanged. No automatic fallback, new
task engine, queue, provider shell or workspace-wide phase switch.

The action has the following closed request contract:

```text
action: pm.overview.assess
target: {workspace_id, thread_id}
preconditions: {expected_version}
params:
  adapter: <explicit value from the advertised structured-analysis set>
  context_bundle_ref: <pm_overview_bundle_v1 selector below>
  model: <optional supported model>
  max_chars: <optional, within the assessment ceiling>
  timeout_s: <optional, within the assessment ceiling>
```

The required Overview selector is separate from accepted-document selection;
the existing accepted-document selector and ordinary analysis remain unchanged:

```text
context_bundle_ref:
  schema: pm_overview_bundle_v1
  projection_digest: <digest returned by central preview>
```

Purpose is fixed by central, stored in the frozen context/run, and **not a request
parameter**. Reject a caller-supplied purpose. Model selection must match the
explicitly chosen supported configuration, and limits cannot exceed the ceilings
below. Adapter is bound in the original request/admission and never substituted
on recovery. Ordinary discussion analysis retains its free-form bounded purpose.

Use `pm_analysis_adapters.admission` for backend authority/binding. The trusted
assessment admitter first validates/freezes the Overview selector, then passes
that helper an internal ordinary-parameter view containing the fixed purpose,
adapter, model and bounds. The Overview selector stays in the assessment binding;
it is not forwarded to the unchanged accepted-document selector validator. This
normalization is internal to the new action, never a client bypass or rewrite
of the saved operation envelope. Recheck the same authority/version on freeze
without rebuilding the admitted bundle. The existing helper's ordinary-parameter
filter is at `src/bat_agent_connector/pm_analysis_adapters.py:25`.

Add exactly this trusted mapping:

```python
DESTINATIONS['pm.overview.assess'] = ('overview_assessment', 'overview_assessment')
```

Use `pm_analysis_adapters.prepare`, `run_analysis` and `verified_reply` with the
assessment's trusted prepare/import callbacks. Only this action can select the
`overview_assessment` admission binding and output destination. The registered
handler cannot fall through to the ordinary discussion sink.

Assessment requires the Overview selector; it cannot be combined with accepted-file
selection. Caller-supplied context bytes, output destinations, source lists,
cursor values or unexpected parameters are rejected. The digest must match a fresh
central projection/registry in the admission transaction; a changed projection
returns 409 before a model launch. Central freezes the exact context, actual
as-of time and cursor in the original admission binding. Execution loads those
bytes; it does not rebuild them from a later Overview. The expected thread
version still participates in existing admission and run creation.

Extend the existing context-bundle preview read with an explicit
`kind=overview_assessment` selector on HTTP/RPC/MCP and the fixed Tauri allowlist.
The default read stays unchanged. This preview is read-only and supplies the
selector, coverage and limits; it does not save a snapshot, reserve a model or
start work. Advertise a versioned `overview_assessment` capability with supported
action `pm.overview.assess`, advertised adapters, bundle/output versions,
eligible/authorized flags and enforced limits.
Old clients/servers retain ordinary analysis; the new card requires this capability.

The UI uses an ordinary Idea/Planning discussion as the carrier because current
`pm_runs.create` requires one. **Reuse comes from central:** read the latest-first
assessment list under the caller's current authority and take `thread_id` from
the newest visible assessment row, resolving its get read if needed. This applies
across clients/reloads and includes a visible needs-review record. Read the current
thread version for the new request. A client cache may hold an in-flight envelope,
but cannot be the only reuse record; never match a discussion by title or path.

Only when the central lookup establishes that no visible assessment row exists
does the first explicit Assess use the existing contextual thread-create flow,
with visible plan/item refs and the fixed localized creation title
**「PM 重點判讀」 / "PM assessment"**. An empty page with remaining or unknown
coverage does not establish absence: follow the cursor or complete the central
lookup; unavailable lookup cannot create another carrier. A complete empty
visible list does allow creation, even if inaccessible records were filtered out.
Thread creation has its own saved key and must finish or recover
before analysis submission; no analysis is sent while creation is uncertain.
The imported assessment row then records the explicit thread ID for later reuse.
Do not create a thread on page load. Read authorization/configuration eligibility
first; runtime preflight remains inside the accepted analysis operation. If later
admission fails, the created discussion remains ordinary history.

Two clients racing before either assessment is recorded may each create a
carrier. This is acceptable and bounded to one thread creation per explicit
first-assessment intent; recovery retains each creation key, and existing writer/
admission limits still bound model work. No auto-cleanup or hidden-thread merge.
Later fresh requests reuse the carrier from the newest visible assessment row.
History selection and a concurrently newer row never retarget an already saved
or uncertain request: it retains its original carrier/key/version.

The carrier stays visible as an ordinary discussion in this contract. A person
can open it and post messages, but **none enters the assessment bundle**.
Thread ID/version are coordination metadata. Thread mode and messages are not
changed by assessment import. Use an output-profile overlay within this operation,
preserving the carrier's existing Idea/Planning mode and runtime restrictions;
do not mark currently contract-only `follow_up` executable. Decisions are then
discussed in the existing item-bound discussion flow.

Central integration work adds the ActionDef/domain admission and sink, preview
selection, one `pm_analysis_adapters.DESTINATIONS` entry, the trusted fixed-context
factory and capability/API generation together. Extend all five action-name
branches cited in the foundations table for the complete source gates and existing
PM cancellation/reconciliation path. Do not add parameter-mode parsing
to these gates or broaden ordinary analysis validators. In particular,
`pm_native_journal.import_reply` must reject this destination rather than letting
it fall through to prose, as defense in depth beyond registered-handler routing.

Cost: one registered action/domain sink, one destination entry and action-name
branches at the five existing gates, with canonical capability/API generation
and focused regressions. The bundle/table/read work was already required. Central
carrier lookup adds a bounded read; first use may add a thread-create operation.
This introduces no executor, schema data step, carrier table or background model
spending, and does not increase the assessment's per-run budget.

## Frozen Overview bundle

Build `pm_overview_bundle_v1` from one consistent central read snapshot, reusing
the Overview projection and its visibility/lineage walk. Add additive
`projection_digest` and `journal_cursor` metadata to Overview/preview so the UI
can compare the same central basis. The digest covers factual payload, coverage,
unknowns and original source identities/versions, excluding read time and
assessment bookkeeping. The cursor records the central event head at capture;
it does not attest remote freshness or order separate servers.

```text
schema, version, workspace_id, thread_id, thread_version
profile                    frozen bounded output-profile version
purpose                    fixed central assessment purpose
as_of, journal_cursor, projection_digest
projection                 exactly the visible Overview payload at capture
source_references          closed registry of original typed refs and digests
source_operation_ids       private central lineage, not default UI copy
coverage                   per-source completeness/cursors/status and bundle omissions
unknown                    original machine-readable reasons
output_destination         overview_assessment (central only)
authority                  context_only
execution_authorized       false
adoption_authorized        false
```

Registry entries are tokens issued by central, not free-form URLs. They map to
attention keys and their original refs, the selected goal input, adopted
requirements/acceptance inputs, milestones with revision IDs, deliveries with
work/binding/receipt identity, and evidence IDs with original fact digests.
Include a projection-root token and coverage tokens so a judgment about missing
information also has a basis. Preserve existing IDs; do not derive relationships
from titles, paths, timestamps or model citations.

Overview does not currently enumerate all evidence IDs or requirement text.
Resolve only the minimal, visible source fields backing its displayed rows and
adopted goal/progress, inside the same snapshot and source gates. Record this
allowlisted enrichment separately; no arbitrary evidence search or full transcript
expansion. Use central relation edges to connect attention → requirement → goal
and delivery → evidence. A fact excluded by a projection/source bound cannot be
cited just because its ID exists elsewhere. Existing root/size limits still apply.

No private discussion messages, unselected inputs, draft body text, session logs,
filesystem discovery, remote fetches, credentials or other-workspace material
enter the bundle. Even an owner with broader access gets this same content
selection rule. Accepted source text is literal evidence, never instructions.
If an accepted goal itself depends on a restricted source, access to the entire
assessment retains that source boundary; membership does not declassify it.

Copy every source incompleteness marker: null counts, missing goal, partial
pages, omitted refs, `attention_truncated`, `milestones_truncated`, delivery and
decision completeness, and unknown ages/dates. Distinguish not-configured or
not-applicable sources from inaccessible sources. Whole optional enrichment
records may be omitted to fit the bundle, with a public reason and completeness
flag; omit their registry tokens too. Essential projection/lineage exceeding
the budget rejects admission with 413. Never clip a source into an apparently
complete quote or disclose hidden source IDs in omission metadata.

## Output contract and validation

Use **structured slots plus central validation**, with deterministic bilingual
templates for version 1. A digits-only blacklist cannot catch “three”, “一半”
or “nearly all”; prompts cannot enforce this rule. There is no model-authored
prose field in a valid assessment. The PM chooses priority, a qualitative reason,
the action/question template and original basis tokens. The UI renders the one
judgment sentence and suggested actions from those slots.

```text
version: 1
judgment: Claim                         exactly one
recommendations: [Claim]                zero to three, priority order
needs_decision: [Claim]                 zero to two, priority order
can_be_handled: [Claim]                 zero to three
watch: [Claim]                          zero to three
limitations: [coverage_token]          exact centrally required limitation set

Claim:
  key                                  closed slot key (j, r.a–r.c, d.a–d.b, h.a–h.c, w.a–w.c)
  template                             enum allowed for this list
  subject_ref                          registry token
  basis_refs                           one to four distinct registry tokens
```

All slots and templates are versioned. Keys must match their group and ordered
position; arbitrary identifiers or prose in keys are rejected. Subject must be among the basis refs.
Model rank means order within a list, never a numeric risk/confidence score.
No counts, percentages, amounts, dates, deltas, duration, titles, quotes, links,
HTML/Markdown, `confirmed_by`, status mutations or execution parameters are
model-selectable text. Stable tokens may contain digits because they identify
sources; they are never parsed or displayed as quantities.

The initial template vocabulary is deliberately small:

| Group | Allowed meaning and central prerequisite | EN / zh-TW template rule |
| --- | --- | --- |
| Judgment | `goal_blocker`: cited open blocker has explicit current requirement/goal edges. | “Prioritize resolving {subject} to protect the current goal.” / 「建議優先排除{subject}，以推進本輪目標。」 |
| Judgment | `decision_first`: cited unresolved decision/change/proposal is still pending in the frozen projection. | “Clarify {subject} before choosing the next step.” / 「建議先釐清{subject}，再安排下一步。」 |
| Judgment | `watch_exposure`: cited recorded risk/deviation; no model escalation of severity. | “Keep {subject} in view when setting the next priority.” / 「安排下一個優先事項時，建議留意{subject}。」 |
| Judgment | `insufficient_basis`: projection/coverage subject, required if no confirmed goal or relevant sources cannot support a goal-impact claim. | “The available evidence cannot establish the main impact on the goal.” / 「目前依據不足，尚無法判斷哪件事最影響本輪目標。」 |
| Judgment | `no_priority_identified`: only complete relevant coverage and no eligible unresolved attention. | “No immediate priority stands out in the available evidence.” / 「目前可見依據未顯示需要立即處理的重點。」 Never “Everything is on track.” |
| Recommendation | `review_evidence`, `clarify_dependency`, `prepare_options`, `follow_recorded_action`; ref type/status must fit the named action. | Begin with “Review / Clarify / Prepare / Follow up” / 「檢視／釐清／整理／追蹤」 and link the subject. No fabricated owner, deadline or promise. |
| Needs decision | `confirm_direction`, `choose_response`, `review_proposal`; cited original decision/change/proposal must have an existing Discuss destination. | A short question: “Which response should we choose for {subject}?” / 「{subject}要採取哪種處置？」 No preselected adoption or claimed approval. |
| Can be handled | `follow_recorded_action`; registry must include an adopted arrangement/assigned action within recorded scope. | “Follow the recorded arrangement for {subject}.” / 「依既有安排處理{subject}。」 It is a suggestion, not evidence of permission, dispatch or completion. |
| Watch | `watch_recorded_risk`, `watch_recorded_forecast`, `await_recorded_evidence`; require the corresponding original fact. | “Watch / Await” / 「持續觀察／等待」 with a linked subject. No new risk, forecast, polling job or alert trigger. |

The implementation must publish the exact template-to-ref-type/status predicate
table in the central contract; unknown templates are rejected. Goal-related
templates require the goal and affected requirement in `basis_refs`, plus their
recorded edges. Missing relation evidence selects the narrower template; it
cannot claim goal impact. Missing existing action/arrangement means the item
cannot enter `can_be_handled`. Watch items remain in the factual attention list
with their original severity; the PM cannot suppress a central blocker.

Central import validates exact keys/types/versions, UTF-8, size, duplicate JSON
keys, cardinalities, unique claim keys, templates, all registry refs, relation
predicates and exact limitations. Reject invented, foreign-workspace, omitted,
mutable-latest or wrong-version refs, and any unexpected prose/count slot.
Require no repeated subject across decision/handled/watch classifications;
recommendations may point to one of those subjects. Model conclusions remain
`conclusions_verified: false` even when the structure is valid.

Numbers and factual labels come only from the projection or registry source
fields. In a current view that is the current matching projection; in history
it is the saved projection. Numeric source titles may appear as literal,
attributed subject chips, not interpolated into a model assertion. Do not append
live counts to an old judgment. Keep factual risk wording/status unchanged.

The server derives a limitation sentence from the required coverage tokens, such
as “This read excludes unavailable delivery evidence.” / 「本次判讀未涵蓋無法取得的交付依據。」
Show that sentence in Current read instead of a sweeping conclusion when its
scope is materially limited; the selected narrower judgment remains in Details.
List the exact visible missing source categories in Details. A valid limited
assessment can still recommend reviewing an item it did see. Empty lists mean
no highlighted suggestion, never zero underlying problems.

Invalid output retains bounded raw reply/digest and verified runtime provenance
as `validation.status = needs_review`, `document = null`, not a rendered
assessment. Read raw output only through the original source-gated run/details
path as literal text. No automatic repair turn. A verified runtime can complete
successfully while its assessment is unusable; the card must distinguish those
states. Unproven completion creates no imported assessment at all.

## Facts, suggestions and decision paths

The existing Goal/Progress/Blockers/Decisions strip stays first and factual.
The assessment has a neutral surface and explicit “PM assessment” / 「PM 重點判讀」
heading plus “Suggestion” / 「建議」 attribution. It does not change metric colors,
counts, attention sorting, status or severity. Avoid a green overall-health badge.

Show adapter and actual reported model in the compact header attribution; unknown
model displays “Model not reported” / 「未回報模型」, not the requested model as
an observed fact. Capture time is available in its accessible description and
Details. Details must show generated-at separately from projection as-of,
as-of cursor, profile/output versions, bundle/result digests, source coverage and
original operation/run IDs. Adapter/model identities come from verified central
provenance, not the reply. Protocol eligibility alone is not verified execution.

Each decision row links to the original item inspector and has **Discuss / 討論**.
Use the existing contextual Discuss link, original refs and plan revision, with
the Overview as return destination and focus in the composer. Recheck access
and explain when the item's current version differs from the frozen basis.
Opening a discussion never starts another model turn. The user may subsequently
request `pm.conversation.propose`, review the resulting proposal, and explicitly
use `pm.conversation.adopt` as owner. Governance decisions retain their existing
decision path when applicable. Assessment is not itself a proposal or an
adoptable input; it cannot auto-confirm, adopt, close, resolve, assign, dispatch,
revise scope, create risks or alter accepted work.

## Storage and history

Use a small new `pm_overview_assessments` table, following the existing Follow-up
sink. A discussion message mixes interpretation into later conversation context;
the existing DocumentSnapshot is a factual Brief/report object. Neither should
be overloaded with this new output schema.

Store insertion sequence, stable assessment ID derived from original operation
ID, workspace/thread/operation/run IDs, bundle and projection digests, as-of and
journal cursor, output version, validated document/digest or null, raw reply/digest,
validation, projected safe provenance, requester and generated-at. Exact bundle
bytes remain in the immutable `pm_runs.context`; reference that original run.
Do not store another mutable copy of the current Overview. Unique operation and
run constraints make the sink insert idempotent.

Insert result, import receipt, terminal run state and event in the same existing
journal transaction after original-turn/death/source verification. A committed
receipt wins internal recovery even after later versions/cancellation; public
reads still require current access. Do not increment the carrier thread version
or add an assistant message. “Accepted result” here means a verified import,
never owner adoption of its recommendations.

DDL uses `CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS`, rejects UPDATE/DELETE on
assessment rows and leaves `PRAGMA user_version` unchanged. No backfill or new
data step is proposed. Any future data step belongs to the integrator; existing
steps 1–3 remain reserved. Derive latest/history from insertion sequence, not a
mutable latest pointer. Preserve failed-validation records with their real state.

Add bounded member/source-gated assessment get/list reads to the existing read
registry and transports; creation uses only `pm.overview.assess`. Initial metadata
page size is ten, maximum fifty, newest insertion sequence first, with an opaque
continuation cursor and explicit
filtering/partial flags. Do not report loaded history as a total. History uses
the saved templates/version and original frozen labels/numbers; keep old renderer
versions readable. New locale selection re-renders the same slots without a
model call. Select history by exact assessment ID, never by a rebuilt latest
projection. No live assessment appears inside an existing saved report: version 1
offers the card in Live only; history is its own inspector linked from the card.

## Staleness, refresh, recovery and cost

An assessment is current only while its centrally computed projection digest
and original source visibility match. When relevant facts, source versions or
coverage advance beyond the frozen basis, show **Out of date / 已過期** and
**Refresh / 重新整理**. A changed global event head alone, assessment imports,
carrier housekeeping, elapsed read time or another workspace's event must not
expire it. A newer `as_of` with the same projection digest stays current. A
central cursor reset/server identity change requires a fresh authorized read;
never compare unrelated cursor sequences. Withdrawal takes precedence over
staleness. This is the proposed resolution of the current timestamp-only gap.

Retain old, still-readable suggestions visibly marked Out of date, with their
frozen chips and provenance. Do not relabel them with newer titles/counts. A
completed run based on a projection that moved during execution imports as
historical/out of date; it cannot replace the current basis. Offline/unverifiable
freshness says “Freshness unknown” / 「無法確認是否為最新」, not Current.

Assessment Refresh explicitly previews the new bundle, then submits the selected
`pm.overview.assess` action with **a new key for that new intent**. Save the exact
envelope before POST. Factual page-refresh remains a read; give the two refresh
icons distinct accessible names and scope. A stale-preview 409 asks the user to
refresh/review again and does not silently submit a rewritten request.

While any request is pending/uncertain, disable fresh Assess/Refresh and retain
its original key, selector, adapter, thread ID/version and operation ID. Recover
by reading the known operation; if the POST response/ID was lost, use the existing
same-envelope/same-key operation lookup/replay path. This may repeat central
admission lookup, **never a runtime start/send**. Do not refresh its bundle, change
its adapter, clear locks or create a replacement turn. Cancellation uses existing
PM cancellation and original-effect reconciliation. A second client is bound
by the central workspace writer too. “Check request” is recovery, not Refresh.

Proposed conservative limits, enforced centrally and advertised in capability:

| Resource | Version 1 limit |
| --- | --- |
| Model work | One existing isolated turn per request; no tools, follow-up/repair calls, fallback or parallel reviewer. |
| Context | Default/hard assessment ceiling 32,000 canonical context characters and the existing 256 KiB UTF-8 ceiling, including metadata/purpose. Keep legacy analysis ranges unchanged. |
| Time | Default/hard assessment deadline 300 seconds. Expiry triggers original-process reconciliation, not an assumption that spending stopped. |
| Valid output | At most 16 KiB canonical structured output. Raw invalid reply/proof stays bounded by the existing runtime/journal limits, never default UI prose. |
| Concurrency | Existing one PM workspace writer, including ordinary analysis/proposal/Follow-up work. Uncertain process death keeps it occupied. |
| New admissions | At most one assessment per minute, six per rolling hour and twenty-four per rolling day per workspace across actors, plus existing provider/host limits. |
| Retries | Zero automatic new model attempts. Original-key replay/reconciliation is exempt from new-admission counters and cannot buy another turn. |

Reserve admission allowance atomically with the durable intent; replays neither
reserve again nor bypass a pending writer. Accepted requests consume allowance
even if they later fail; rejected pre-admission requests do not. Count from the
original durable operations, not a browser timer or new scheduler. Rate rejection
returns a retry time from central. No background analysis on navigation, polling,
source events, locale changes or staleness. Automatic spending requires an
explicitly adopted, bounded policy and a separate reviewed implementation.

These are turn/time/input/admission budgets, **not a proven token or monetary
ceiling**. The current native turn request has no hard output-token/cost parameter
(`src/bat_agent_connector/pm_native_runtime.py:577`). Reply-size validation does
not retroactively limit provider spend. Report actual usage/cost only if central
has provider evidence; otherwise Unknown, never zero or an invented estimate.
If the owner requires a hard token/dollar ceiling, keep the feature unavailable
until the selected runtime can enforce and attest it. A policy may lower these
limits; increasing them needs an explicit product decision.

## Permissions, privacy and withdrawal

Requesters retain observe/manage/start and current owner/contributor membership,
or the existing authenticated administrator override. Original requester/controller
rules govern cancellation/resume. Readers require observe, current workspace
membership **and current access to every original bundle source**. A reader may
read but cannot launch Refresh. Use current caller authority on every get/list,
raw run, operation and event read; never borrow the analyst's scopes. Preserve
lifecycle gates, including paused/closed restrictions on fresh investment.

Register the assessment boundary by `pm.overview.assess` action name in
`pm_runs.source_visible`, `pm_workspace.operation_visible` and event/lineage
traversal, using its original Overview admission/source gate before
advertising the capability. Existing membership-only success must not expose a
more privileged requester's raw context. Any original included source withdrawal
withdraws the entire assessment: even uncited material could have influenced
ranking. Do not selectively trim prose into a seemingly valid conclusion.

On assessment/bundle read 403/404, remove judgment, recommendations, decision/
handled/watch lists, all basis chips, attribution, cached history entries and
assessment inspector details together. No hidden item names, counts or refs
remain. Parent workspace/Overview source loss also clears dependent factual
rows/counts/inspectors through existing withdrawal hooks. A successful partial
Overview read can retain its other visible facts, while an assessment using the
lost root is withdrawn. 409/413 coverage errors are partial/unavailable evidence,
not proof that permission was revoked.

Increment request generation on withdrawal, workspace/principal/connection
changes and disposal; cancel or ignore prior fetches. A late assessment or
projection reply cannot revive withdrawn data. Restoration requires a fresh
authorized read, never cache fallback. A denied write does not prove denial of
all reads: clear actionable state, then re-read under current authority before
showing any retained assessment as read-only. Do not automatically substitute
an older assessment when the selected one is withdrawn.

Keep unresolved operation envelopes/keys in the existing principal/connection
durable intent store for recovery, separately from renderable content. Hide
their source-bearing fields while access is absent. Permission loss cannot erase
an uncertain external effect or release its writer. Later access restoration
reconciles the original operation; it does not resend a model prompt.

## Card layout, states and copy

Placement: directly after the live four-metric status strip, before the attention/
milestone/delivery columns and Charter. Use one quiet region with shared neutral
surface, hairline border and existing tokens. No additional metric grid, nested
cards, decorative icon, generated illustration or independent theme. Keep
Discuss with PM as the page's primary action; Assess is secondary and Refresh
is an icon with a localized tooltip/accessibility name.

| Viewport | Reading order and size |
| --- | --- |
| 1440 | Full available content width below the status strip. Label/value grid with a compact label column; body uses the shared 14px scale, metadata 13px. Attention and right column remain below. |
| 1000 | Same position and order within the narrower shell. Values and chips wrap; do not put assessment beside the metrics or squeeze decision buttons. Inspector uses the existing drawer behavior. |
| 390 | Same order, labels above their values; decision text then Discuss within each row. Chips wrap, touch controls have at least 44px hit areas. No horizontal document overflow or fixed card height. Details uses the existing mobile sheet. |

Default body has **at most four labelled groups**, in this order. “Four lines”
means four logical groups; readable wrapping and the requested two decision
rows are allowed, not truncation or tiny text to force four physical lines.

| Label EN / zh-TW | Default content |
| --- | --- |
| Current read / 目前判斷 | One templated sentence, or the central limitation sentence. |
| This week / 本週建議 | Highest-priority recommendation only, with its subject link. Other recommendations, Can be handled / 可依既有安排處理 and Watch / 持續觀察 are in Details, preserving their ordered lists and per-claim bases. “This week” is a suggested focus, not an invented Sprint/week commitment or due date. |
| Needs your decision / 需要你決定 | At most two linked rows, each with Discuss. Omit this group if no decision is highlighted; do not imply there are no pending decisions. |
| Based on / 依據 | Deduplicated factual subject chips for visible claims, with Details for full per-claim bases, limitations and provenance. Chips use literal source labels; IDs appear only in Details. |

Header attribution, one status and inline recovery controls are chrome, not more
interpretation groups. Expand Details in the existing inspector rather than
adding more default card sections. Every claim in the inspector also has its
own basis links. No tooltip-only caveat or model text hidden by ellipsis.

| State | EN / zh-TW copy and action |
| --- | --- |
| Empty, configured/authorized | “Not assessed” / 「尚未判讀」; one sentence “Ask PM to review the current overview.” / 「請 PM 判讀目前總覽。」 with Assess / 請 PM 判讀. No call until clicked. |
| No PM agent/configuration | One sentence “PM agent is not connected.” / 「尚未連接 PM。」 and Set up PM / 設定 PM. Link to `#/settings?section=agents&from=<encoded internal Overview route>`. Returning restores the workspace; no automatic assessment. |
| Unsupported central | Hide the new region if its versioned capability is absent. Existing factual Overview and Discuss remain usable. No unsupported write. |
| Unauthorized requester | Show an accessible read-only existing result, if available; request-control explanation “Assessment requires management access.” / 「判讀需要管理權限。」 No misleading Set up PM action for a scope failure. |
| Loading reads | Static skeleton; accessible Loading assessment / 載入判讀中. No fake judgment or provider call. |
| Accepted/pending | Queued / 等待判讀; operation ID/key are already saved. Disable a new request. Do not claim the runtime has started. |
| Running | “PM is assessing” / 「PM 正在判讀」 only from the original operation state. No looping progress or invented percentage. Keep readable prior result if authorized, labelled Previous assessment / 上次判讀. |
| Ready | Four-group contract above; Suggestion / 建議 attribution. Completed operation is not adopted scope or task completion. |
| Partial/unknown | Partial / 依據不完整 plus central limitation sentence and Details naming the visible missing categories. No “all clear,” missing-as-zero, or whole-project ranking. |
| Failed, known terminal | “Assessment failed” / 「判讀失敗」 beside the control; Details has the mapped reason. A fresh explicit Assess is available only after central proves the original effect terminal/writer released. |
| Verified turn, invalid output | “Assessment needs review” / 「判讀內容需要檢查」; Details for literal original reply and reason, no decision rows or automatic repair call. |
| Uncertain | “Request outcome unknown” / 「請求結果尚未確認」; Check request / 查回請求 reads the original operation. Disable Refresh/Assess while unproven. |
| Out of date | Out of date / 已過期 with Refresh / 重新整理; retain only readable frozen content, without mixing new facts. |
| Offline/freshness unknown | “Freshness unknown” / 「無法確認是否為最新」; read recovery on reconnection, no automatic model request. |
| Withdrawn | “Assessment unavailable” / 「無法讀取判讀」; remove content and inspector together. Generic wording conceals whether the source was private, removed or elsewhere. |
| Budget/rate blocked | “Assessment limit reached” / 「已達判讀上限」 with the server's retry time in Details. Refresh disabled until permitted; no timer-triggered submission. |
| Paused/closed | Workspace paused / 工作空間已暫停 or Workspace closed / 工作空間已結案 from central. History is readable when authorized; no new assessment bypass. |

Copy rules: sentence case EN, natural Taiwan Traditional Chinese, headings at
most four words and actions at most three words in EN. Product/adapter/model
proper names can remain literal; translate all surrounding status, labels,
errors, accessible names and templates through `pm-overview-assessment-strings.js`
merged into the existing i18n pattern. Do not translate user-authored source
quotes or pretend those quotes were written in the selected locale. Never render
raw keys, missing values, `undefined` or `null`. Speak as an attributed suggestion;
never “you approved,” “I completed,” “will deliver,” or a fabricated critical path.
Do not pad empty recommendation lists with invented work. Keep all quantities
outside recommendation prose and render dates/time from central metadata only.

Build with existing `h()`/DOM helpers and `createElementNS` icons, literal
`textContent`, external CSS and strict CSP. No markup from provider/source
strings, inline stylesheets/style attributes or `innerHTML`. Apply shared focus,
drawer/sheet, reduced-motion and light/dark tokens. Preserve drafts, selected
history, focus, reading order and scroll during ordinary read refreshes. Announce
state transitions once in a polite live region without stealing focus.

## Safety review

| Repository/task rule | Review of this contract |
| --- | --- |
| Adopted PM plan and revised shared product direction | Adds the decision interpretation layer to existing W10/W12/W32 foundations; does not replace lifecycle management with session summaries or call this the full PM plan. |
| Python central authority; native responsibility | Context, validation, limits, source access, storage and effects remain central. Tauri only transports the same operations/reads and manages existing local UI/credentials/Fleet. |
| Manual resources read-only; unknown requires creation evidence | No takeover or inferred ownership. BAT retains the original proven PM carrier/session/resource-policy checks. No manual checkout/worktree writes or new checkpoint transport. |
| Registered scopes/actions/coordinator/durable operations | One registered `pm.overview.assess` structured sink reuses existing adapter admission/execution and authority; ordinary analysis actions stay unchanged. Preserves original request keys, final-frame/source/controller/lifecycle checks and sent-effect reconciliation. |
| No unknown-result resend or lock clearing | Uncertain UI offers original request readback only; writer retained until original terminal/death evidence. |
| No Hub importer; Connector identity/history | New native assessment IDs reference original Connector runs and stable source IDs. No Hub parser, mappings, importer dependency or ID rewrite. |
| Shared frontend/generated assets | Future UI changes only `desktop/src`, followed by browser regeneration and build. This document touches no source or generated assets. |
| Worker ownership and integrator | This branch owns this one design document. The integrator owns shared registry/routes/gates; #133 and Settings are read-only dependencies. No delegation or other-worker edits. |
| Public-content/secret handling | Examples use symbolic central tokens and public source filenames only. No real deployments, private paths, hostnames, credentials, fingerprints or live writes. |
| DDL/data steps | Immutable idempotent DDL, untouched user_version, no backfill. Any future data step requires integrator coordination, not a new worker allocation. |
| EN/zh-TW, drafts, stable IDs/keys | Deterministic bilingual templates, literal source text, preserved durable envelopes and existing contextual decision/adoption flow. |
| Honest facts and withdrawal | Numeric-free model slots, projection-only numbers, explicit partial/unknown/stale states; all derived assessment content withdrawn together, with late-response guards. |
| CSP and provider material | Existing DOM helpers/literal text; no injected markup or provider URLs. Runtime restriction is enforced/attested, not assumed from a prompt. |
| Existing operation authorization | Assess/Refresh clicks authorize that bounded operation under existing scopes. No redundant chat confirmation; Discuss/adopt remain distinct intentional actions. |
| Tests and disk temporary data | Mock providers/BAT and disk-backed Git/journals only; pytest-disktmp and shared heavy lock, no tmpfs, stdlib-wide monkeypatch or leftover containers. Safety assertions stay intact. |
| Documentation and release evidence | This authorized output is one document. Implementation PRs update DESIGN/contracts/Unreleased and generate applicable contracts. Branch, merged, installed and live-tested remain separate; CI/screenshots do not establish native/live acceptance. |
| Branch/commits and delivery ownership | Commit on the assigned worktree branch with the required trailer. Orchestrator alone pushes/opens PRs/merges; no outside checkout/system/config writes. |

## Risks and owner decisions

The main implementation risk is a source-gate omission on raw run, operation,
event or history reads, especially when an owner can see more than a reader.
The gate must cover the entire bundle, not only visible basis chips. A second
risk is admitting caller contexts by loosening the existing fixed-context allowlist.
Extend only the exact `pm.overview.assess` action/destination/admission combination
and test rejection. A separate action follows the structured-sink precedent and
keeps ordinary gates independent of parameters; routing plus the destination
guard must both prevent an assessment from entering the prose sink.

Structured slots prevent model quantities and forged confirmation; they cannot
prove that the chosen priority is wise. Templates constrain nuance. Use mock
scenarios with goal blockers, unrelated severe risks and missing coverage to
evaluate useful ranking; reserve actual PM quality acceptance for a separate
owner exercise. Do not claim a goal relationship where source edges are missing.

Projection digests must include relevant source identity/coverage changes without
expiring on unrelated journal noise. Cross-read enrichment or new history caching
could violate the frozen boundary. Carrier discussions may clutter the thread
list, including the bounded two-client race. Central newest-visible-row reuse
prevents a carrier per reload; partial lookup must not be mistaken for no prior
assessment. Whether to hide carriers remains an owner decision; no hidden-thread
semantics are introduced here. Old template renderers and whole-bundle withdrawal
add maintenance cost.
Settings agents deep-link support is an external UI dependency; coordinate its
return route and workspace restoration with that PR, without implementing it here.

Owner decisions before implementing the affected PR:

1. Accept deterministic templates/structured slots for v1, or require a later,
   separately validated narrative contract? This proposal recommends templates.
2. Accept one turn, 300 seconds and the proposed admission caps as the initial
   spending budget, or require an enforceable token/dollar cap? A hard monetary
   requirement blocks launch on today's unproven runtime capability.
3. Confirm Live-only placement with separate assessment history and decide whether
   carriers should be hidden from the ordinary discussion list in a later change.
   This contract keeps the centrally reused carrier ordinary and visible;
   saved-report attachment and hidden-thread semantics remain outside it.

## Verification plan for implementation

Run focused central suites on Python **3.10, 3.11, 3.12 and 3.13**, using mock
native/BAT providers, `tests/mockbat.py`, temporary disk Git/journals and
`settle_operations`. Proposed new focused file: `tests/test_pm_overview_assessment.py`.
Retain the existing context, native/BAT, proposal, privacy, Overview and snapshot
assertions; do not reduce write counts or weaken recovery/withdrawal guarantees.

Central acceptance scenarios:

1. `pm.overview.assess` alone admits the exact selector and source snapshot with
   either advertised adapter; native and BAT import the same valid structure
   through the shared helper/executor. The unchanged ordinary analysis validators
   still reject assessment-only adapter/new parameter fields and the Overview
   selector. The new action rejects caller purpose, arbitrary destinations and
   unadvertised adapters; central injects the fixed purpose only internally.
   All five gate branches recognize the new action, and ordinary prose import
   rejects the assessment destination even if handler routing is bypassed.
2. Reject foreign/unknown/omitted/wrong-version refs, duplicate keys, wrong shapes,
   oversized context/output, every forbidden prose/count slot, forged actor/
   confirmation fields and arbitrary output destinations before usable import.
   “three”, “半數”, non-ASCII digits and percentage strings cannot pass via text slots
   because such slots do not exist. Digits inside valid opaque IDs still work.
3. Every claim has its own basis; relation predicates reject invented goal impact,
   decision targets and autonomous authority. Partial coverage requires exact
   limitations; complete empty and unavailable empty are different. No source
   mutation, adoption/closure/dispatch, plan/head change or human-checkout write.
4. Freeze while the projection changes, then complete: preserve original digest,
   counts and refs, mark stale. Unchanged later read/global unrelated event is
   current; changed coverage/goal/evidence is stale. Reset/server change does not
   compare unrelated cursors. Unknown totals remain null, loaded pages never totals.
5. Owner/contributor scopes, reader reads, nonmember/wrong-workspace denial,
   original-requester controls and lifecycle gates across HTTP/RPC/MCP/native.
   Plant unique private-message/source sentinels; none enters the provider prompt
   or member reads. Broader requester authority is never borrowed for a reader.
6. Withdraw a source before launch, immediately before each send/import boundary
   and after import. Raw context, structured record, provenance, history, operation
   and events all retain the same source gate. Source loss can never reopen a writer.
7. Same-key replay, cross-actor/workspace namespace, changed-envelope conflict,
   double submit, second-client writer conflict, lost POST/start/send ACK, missing
   death proof, cancel and restart. Assert exactly the original runtime start/send
   counts and original IDs. Proven committed import survives later cancellation
   internally; inaccessible public reads do not reveal it.
8. Inject import transaction/receipt failure and recover without another model turn
   or duplicate result. Invalid JSON saves needs_review only after proven completion;
   ACK-only, gap and unattested tools/profile never enter the sink. No fallback call.
9. DDL can run repeatedly; UPDATE/DELETE rejected; user_version and all existing
   schema-step data unchanged. History pagination/filtering exposes no hidden
   row metadata or fabricated totals. Old template versions remain readable.
10. Atomic admission caps under concurrent actors/restarts; replay consumes no new
    allowance, new failures do. Time/context/output limits and provider refusal
    never cause auto-repair/spend; unknown monetary usage is not zero.
11. Across reloads/two clients, carrier reuse comes from the central newest visible
    assessment row, not cached state or title matches. A visible invalid-output
    row still supplies its thread ID; withdrawal hides inaccessible carrier refs.
    Empty partial pages do not create a thread. Complete empty lookup allows one
    keyed create per explicit intent; two racing creates are acceptable and later
    reuse selects the newest visible row. Posting in either ordinary carrier
    advances its thread version but never enters the frozen provider bundle.

Example focused invocation (repeat with each version; filenames are planned
implementation targets, not tests executed for this design):

```sh
pytest-disktmp uv run --python 3.10 pytest -q tests/test_pm_overview_assessment.py
pytest-disktmp uv run pytest -q tests/test_pm_context_bundles.py tests/test_pm_native_operations.py tests/test_pm_bat_structured_analysis.py tests/test_pm_conversation_source_privacy.py tests/test_pm_overview.py
uv run ruff check .
```

Playwright experience coverage belongs in a proposed
`desktop/tests/pm-overview-assessment.spec.ts`, plus adapted stable hooks in the
existing workbench/context/conversation specs. Use the mock workbench and a
real-central/mock-runtime integration fixture for complete operation recovery:

- Both locales at 1440×900, 1000×900 and 390×844; also 768 and a dark-theme check.
  Inspect screenshots manually: metrics precede assessment, four logical groups,
  decision links/Discuss readable, wrapping/touch targets, no horizontal overflow,
  extra primary button or fabricated totals. No console errors/raw keys/null text.
- Opening Overview, source polling, page Refresh, locale changes, history and
  Settings navigation make **zero model writes**. Explicit Assess submits once;
  admission/queued/running/ready states follow original central evidence.
- Reload/new client reads the newest visible assessment's carrier ID from central.
  First use creates a discussion with the exact localized title only after a
  complete empty lookup; two racing clients can each create once, then converge
  on central's newest visible assessment row for future requests. A person can
  post in the ordinary carrier without leaking messages into a subsequent bundle.
- Discuss from either decision binds the original context, preserves return to
  Overview and focuses the composer. Proposal review/adoption stays a separate
  owner action; reading a suggestion cannot change a decision or acceptance count.
- Distinguish factual page Refresh from assessment Refresh. Out-of-date data keeps
  frozen bases; Refresh creates a new intent once. Uncertain recovery/reload uses
  the original envelope/key/adapter and cannot send again. Include lost operation ID.
- Source/parent/scope withdrawal removes every derived chip/list/inspector/history
  entry; delay replies before/after withdrawal to prove they cannot revive content.
  Private sentinel strings never reach browser text or provider input.
- Test empty/no-agent/deep-link return, absent capability, reader, loading, queued,
  running, invalid, failed, partial, unknown, stale, offline, rate and lifecycle
  states. No-agent action reaches `section=agents` with an encoded internal return
  route; no assumed success from merely opening Settings.
- Keyboard/focus/selection/draft/history stability through reads; accessible names
  distinguish both refresh icons, Esc closes Details and focus returns. Exercise
  malicious literal source labels under the actual strict CSP.

Proposed stable hooks: `data-pm-assessment`, `data-assessment-state`,
`data-assessment-claim`, `data-assessment-decision`, `data-assessment-basis`,
`data-assessment-refresh`, `data-assessment-recover`. Prefer accessible roles/names
for actions; IDs supplement selectors without exposing raw IDs as default copy.

```sh
cd desktop
npx playwright test tests/pm-overview-assessment.spec.ts
npm test
npm run build:browser
npm run build
```

Use the provided `BATC_UI_TEST_PORT`, without overriding it. Heavy full checks
run sequentially under the shared lock, only when the implementation brief
authorizes them; the orchestrator owns full Python suites. No full suite,
provider, native or live acceptance was executed for this documentation change.

## Small staged pull requests

| Stage | Scope and exit evidence |
| --- | --- |
| 1. Backend contract | Register `pm.overview.assess`, add its exact DESTINATIONS entry and use shared adapter admission/prepare/run_analysis/verified_reply. Add frozen preview/context/output validation, digest/cursor, immutable sink, central limits and all five action-name source/cancellation gate extensions. Add newest-first get/list carrier reuse, read registry/capability and generated protocol contracts together. Focused Python 3.10–3.13 tests prove both adapters, unchanged legacy validator rejection, no domain mutations, original-key recovery, privacy, carrier selection/races and idempotent DDL. Record owner budget/template decisions. No card or automatic spending. |
| 2. UI card | Shared `desktop/src` card/strings and existing Overview hooks; explicit initial Assess, provenance/Details, Discuss, honest states and Settings deep link. Regenerate browser assets, pass Node/tsc/Vite and focused Playwright, inspect bilingual viewport screenshots. Require #133 integration and Settings route contracts; no fallback to unsupported APIs. |
| 3. Refresh/history | Add explicit new-bundle Refresh, selected history inspector/pagination, durable reload recovery and stale/withdrawal races in shared UI. Core keys, immutable records and source gates already land in stage 1; they are not deferred safety work. Keep factual page Refresh read-only; history uses original templates/bases. Regenerate and verify assets/tests again. Saved-report attachment and automatic policies remain separate proposals. |

Each implementation PR updates the applicable design/API docs and Unreleased;
the UI PR also updates `desktop/DESIGN.md`. Report branch, merge, installed/native
and live-tested status separately. This document's completion evidence is its
source-citation check, public-content check and clean diff, not product acceptance.
