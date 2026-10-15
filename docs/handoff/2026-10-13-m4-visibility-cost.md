# M4 integration: synchronous source-read cost

This records local validation on `ux/m4-refresh`. It does not claim CI, native,
installed or live-provider acceptance. The orchestrator owns publication and
merging. No timeout, settle bound, lost-reply simulation, durable operation,
receipt, schema version or original recovery key was changed.

The sections below preserve the previous session's evidence through
`ace46c87`. Its last affected Python 3.14 selection ended without a summary or
JUnit report; it is not a completed validation. Reconstructing its unchanged
collection identifies the error as
`tests/test_pm_quality_reviews.py::test_invalid_reviewer_types_never_reach_sql_or_create_an_operation[reviewer2]`
and the failure as
`tests/test_pm_repair_observer_source_reads.py::test_original_observer_frame_bounds_repeated_sources_and_rechecks_authority`.
The continuation below records their head/base comparisons and the separately
captured failures from the completed rerun.

The exact [129-file test inventory](2026-10-13-m4-visibility-cost-tests.txt)
is preserved with this note. The affected runs select its 127 files other than
the joined lifecycle and operation helpers, which are measured separately.

## Continuation (2026-10-14): identifying the interrupted failures

The first complete continuation run used that same ordered 127-file affected
selection on Python 3.14.5, without stopping at the first failure. It records
**2,165 passed / 4 failed / 3 errors in 10,668.96 seconds**, exit 1.
`resume1-affected-py314.log` and `.xml` retain every fresh traceback. Both cases
from the interrupted run **passed** in this complete rerun. Their original
tracebacks were never emitted and cannot be recovered from the incomplete log.

Each original case was also run alone on the candidate and on an export of
`2d9a2deb`, made with `git archive` rather than another worktree. Each invocation
used `heavy pytest-disktmp`, `-rfE` and its own JUnit file. Both environments
used Python **3.14.5**, pytest **9.1.1** and pytest-asyncio **1.4.0**; imports
were verified to come from their respective source trees. Neither original test
file differs from `2d9a2deb`.

| Original case | Candidate | `2d9a2deb` |
| --- | ---: | ---: |
| `tests/test_pm_quality_reviews.py::test_invalid_reviewer_types_never_reach_sql_or_create_an_operation[reviewer2]` | 1 passed, 2.70 s | 1 passed, 3.06 s |
| `tests/test_pm_repair_observer_source_reads.py::test_original_observer_frame_bounds_repeated_sources_and_rechecks_authority` | 1 passed, 33.54 s | 1 passed, 54.73 s |

The interrupted run's surviving journals were inspected read-only, without
replaying operations. Neither had a pending WAL file. The invalid-reviewer
fixture had not reached reviewer validation: its original `pm.plan.adopt` was
still running with `pm.revision.commit` started and no receipt. The observer
case had not reached the observer frame assertions: the original work batch
was waiting on its recovery `repository.continue`, whose `send` step was started
without a receipt. Its preceding session start had taken 24.63 seconds.

My assessment is that neither is a reproduced branch regression. The retained
stages and the completed comparisons support transient fixture/worker stalls,
rather than an invalid-reviewer SQL defect or a failed observer query ratio.
The exact exceptions and individual triggering delays in the interrupted run
remain unknown; journal stages do not reconstruct a lost traceback.

The completed rerun preserves the observer test's unchanged strict comparisons:
the original/scoped frame uses **3,618 / 1,554 SELECTs**, and the original/scoped
policy read uses **742 / 212 SELECTs**. Withdrawal, credential, deadline and zero
write assertions also pass in that test.

### New failures in the complete rerun

The complete rerun's seven problems are different cases. Their tracebacks
identify preparation failures before their target assertions:

| Case | Captured failure or setup error |
| --- | --- |
| `tests/test_pm_maintenance_repair_baseline.py::test_private_baseline_uses_bounded_http_and_current_token[True]` | `finish_deployment` finds no private delivery child. The original observation is incomplete with `observation_time_limit` after five pages; the parent correctly waits for the adopted observation interval. |
| `tests/test_pm_maintenance_repair_retries.py::test_same_transition_is_idempotent_and_later_restart_consumes_next_slot` | Preparing the original transition finds an incomplete observation with `observation_time_limit`, after eleven pages. It does not reach retry reservation assertions. |
| `tests/test_pm_plan_operations.py::test_owner_revoked_during_reuse_read_records_proof_without_new_decision` | The unchanged 60-second settle expires during `pm.plan.adopt`, at `pm.revision.verify` while its authority guard awaits the workspace response. |
| `tests/test_pm_publication_source_reads.py::test_saved_publication_reuses_original_verifier_sources_without_losing_privacy` | The unchanged settle expires during original engineering, at `pm.execution.runtime.observe` awaiting runtime synchronization, before publication comparisons. |
| `tests/test_pm_maintenance_repair_runtime.py::test_actual_original_cursor_native_completion_and_quality_release` | Fixture setup exceeds the unchanged settle bound in `pm.fixture.prepare`, at repository fetch awaiting host capture. |
| `tests/test_pm_maintenance_repair_runtime.py::test_idle_abort_gap_later_input_or_changed_identity_never_release[idle]` | Fixture setup exceeds the unchanged settle bound in the original `repository.continue` send while awaiting runtime synchronization. |
| `tests/test_pm_maintenance_repair_workload.py::test_queued_input_counter_keeps_original_capacity` | Fixture setup exceeds the unchanged settle bound in `pm.fixture.prepare`, at snapshot context while its guard awaits the workspace response. |

Each of these seven cases was then run alone on both source trees, in sequence:

| Case, in the order above | Candidate | `2d9a2deb` |
| --- | ---: | ---: |
| Private baseline | 1 passed, 8.13 s | 1 passed, 11.64 s |
| Retry transition | 1 passed, 3.02 s | 1 passed, 3.06 s |
| Revoked plan owner | 1 passed, 1.34 s | 1 passed, 1.35 s |
| Saved publication | 1 passed, 34.25 s | 1 passed, 60.42 s |
| Native completion | 1 passed, 4.06 s | 1 passed, 5.16 s |
| Idle runtime | 1 passed, 3.98 s | 1 passed, 5.18 s |
| Queued input | 1 passed, 4.04 s | 1 passed, 5.12 s |

Together with the originals, this is **18 separate invocations, 18 passed,
zero failures/errors/skips**. All nine head/base pairs have separate
`resume-isolate-N-{head,base}-py314.log` and `.xml` files. The candidate's product
source, test assertions, timeouts, settle bounds and provider simulations
remained unchanged from the broad failure through these comparisons. The base
was always the unmodified `2d9a2deb` export.

The long broad run also supplied direct storage-wait evidence. At 131 minutes
elapsed its pytest process had used less than ten minutes of CPU and was
observed in `jbd2_log_wait_commit`; other samples showed `submit_bio_wait`.
One device sample reported approximately 559 ms average write await. These
observations support storage contention as a contributor, without proving the
individual cause of every timeout. No unrelated process was stopped, and no
shared lock was cleared or reconfigured. Task-owned heavy commands were
sequential, including temporary-directory cleanup.

During the ordered repeat, the same private-baseline preparation failure
recurred. Its original observation again reports `observation_time_limit`,
this time after 24 pages and one traversal. The original parent retains its
required-CI observation-interval wait; its frozen fixture clock has not advanced
to that interval. All recorded page steps succeeded. This is not evidence that
the private-token assertions failed.

A scratch monitor sampled only the pytest process's progress log and `/proc`
CPU counters. Sampled portions of three error intervals took
**62.89 / 0.05**, **91.55 / 0.83** and
**71.55 / 0.62 seconds wall / CPU**, respectively. The first and third ended
with the process sampled in disk wait. A separate sample at 1:56:28 elapsed
showed 10:50 CPU, about 181 MiB resident memory, and
`jbd2_log_wait_commit`. These measurements show that main-process visibility
CPU did not dominate the sampled intervals; they do not identify every
underlying delay or supply whole-case CPU measurements. Reports are
`resume-repeat-resource-intervals.json`,
`resume-resource-monitor.log` and `resume-repeat-io-samples.log`.

### Fresh joined measurements

The product source remains identical to the profiled implementation at
`9449be4a`; the earlier profile therefore still describes this implementation.
Fresh unprofiled runs used the same scratch timing plugin, forwarding every
settle argument unchanged. The joined lifecycle and helper selection passed
once on each interpreter:

| Measurement | Python 3.14.5 | Python 3.10.20 |
| --- | ---: | ---: |
| Pytest result | 8 passed, 84.68 s | 8 passed, 102.55 s |
| Session wall / CPU | 84.74 / 76.13 s | 102.67 / 94.44 s |
| Joined body wall / CPU | 82.12 / 75.31 s | 99.27 / 93.34 s |
| Measured settles, all passed | 110 | 109 |
| Slowest settle wall / CPU | 9.70 / 9.22 s | 11.87 / 11.38 s |

Both slowest settles are in `reconnect_and_replan`. Every measured settle in
these fresh runs is well below 60 seconds. Against the original unprofiled
post-merge body (**195.97 s wall / 187.74 s CPU**), the fresh Python 3.14 body is
**2.39 times faster in wall time** and uses **2.49 times less CPU**. These are
the measured reductions; a larger several-fold improvement is not established.
The earlier 53.56-second successful settle and all failed runs remain recorded
below, rather than being replaced by the quieter run's measurements.

Artifacts are `resume-joined-{py314,py310}.log` / `.xml` and
`resume-native-{py314,py310}-timing.json`, under `$TMPDIR/visibility-cost/`.

The complete Python 3.10 affected selection records **2,172 passed in
2,774.48 seconds**, with zero failures, errors or skips
(`resume-affected-py310.log` / `.xml`).

A single optional Python 3.14 repeat of the same ordered selection was
interrupted intentionally after it confirmed the recurring baseline
preparation failure and three other setup stalls. The required complete rerun
above remains the broad Python 3.14 result. The optional repeat is **partial**:
its JUnit report and progress markers agree on **1,385 passed / 1 failed /
3 errors**, zero skips, over **7,381.234 seconds**. It did not reach either
original interrupted case. It is not a passing or completed affected selection.

`resume2-affected-py314.xml` preserves all four problem tracebacks. The
interruption also triggered a pytest `tmp_path` teardown `KeyError` in
`_pytest.stash`; the wrapper exited 1. The text log has this infrastructure
traceback instead of a normal final failure summary. The JUnit report remains
parseable and includes every reported failure and setup error:

| Case | Captured result |
| --- | --- |
| `tests/test_pm_maintenance_repair_baseline.py::test_private_baseline_uses_bounded_http_and_current_token[True]` | Same preparation failure as above, before private-token assertions; incomplete observation after 24 pages. |
| `tests/test_pm_maintenance_repair_guards.py::test_rule_does_not_gain_unproven_version_paths_or_execution_fields[release]` | Setup expects a complete original observation but gets `observation_time_limit`, after five pages and zero completed traversals. |
| `tests/test_pm_maintenance_repair_quality.py::test_publication_requires_exact_original_revision_and_coverage[candidate]` | Setup exceeds the unchanged settle bound in the original `repository.continue`, at `session.start` awaiting a workspace response. |
| `tests/test_pm_quality_projections.py::test_brief_deduplicates_requirements_and_unreviewed_contributions_remain_unknown` | Setup exceeds the unchanged settle bound in the original `repository.continue`, at `verify.start` awaiting client connection. |

The three sampled error intervals above correspond to these three setup errors
in order. The first sample began partway through setup and must not be read as
its entire duration. No source, assertion, budget or simulation was changed to
make these outcomes disappear.

The subsequent complete baseline-file candidate run records **6 passed /
1 failed in 428.82 seconds**. The failure is
`tests/test_pm_maintenance_repair_baseline.py::test_stop_before_selection_preserves_delivery_and_original_baseline[pause]`,
at the same `finish_deployment` child-preparation assertion, before it applies
pause. The private-baseline cases both pass in this complete file.
`resume-baseline-file-head-py314-timing.json` records **428.96 s wall /
43.10 s CPU** for the session; the failing body is **65.11 / 3.13 s**.
All 245 measured settles return successfully, but the slowest takes
**64.57 s wall / 2.94 s CPU**. The unchanged asynchronous settle bound cannot
preempt synchronous storage waits. This is a separate baseline-file diagnostic,
not the successful joined-lifecycle measurement above, and it demonstrates that
host stalls remain relevant even after the visibility CPU reduction.

The unmodified `2d9a2deb` export's complete baseline file also records
**6 passed / 1 failed in 390.68 seconds**, at
`tests/test_pm_maintenance_repair_baseline.py::test_private_baseline_uses_bounded_http_and_current_token[False]`.
Its traceback ends at the same `finish_deployment` assertion, before any code
depending on `token_missing`. The session measures **390.77 s wall / 65.29 s
CPU**, with 249 successful settles and a maximum of **85.98 s wall / 4.04 s
CPU**. This reproduces the preparation-failure class on the published base,
independently of the candidate's visibility optimization. The different
per-case outcomes and variable storage waits prevent treating these two
failed-file durations as a performance comparison.

The candidate and base share the same baseline assertion and scenario. The
candidate adds assertion diagnostics and the previously documented controlled
credential-clock alignment in `test_pm_maintenance_repairs.py`; the base export
retains its original fixture. Neither was edited for these comparisons. The
base failure occurs before token handling, just as the candidate's pause case
fails before applying pause. These failures do not demonstrate a pause-policy
or private-token defect. The base's failed journal had already been removed by
the wrapper before an additional read-only capture was attempted; its JUnit
traceback is retained, and its exact observation document is unavailable.

All five newly exposed cases were then checked alone on both source trees:

| Case | Candidate | `2d9a2deb` |
| --- | ---: | ---: |
| `tests/test_pm_maintenance_repair_guards.py::test_rule_does_not_gain_unproven_version_paths_or_execution_fields[release]` | 1 passed, 60.52 s | 1 passed, 47.23 s |
| `tests/test_pm_maintenance_repair_quality.py::test_publication_requires_exact_original_revision_and_coverage[candidate]` | 1 passed, 55.55 s | 1 passed, 35.21 s |
| `tests/test_pm_quality_projections.py::test_brief_deduplicates_requirements_and_unreviewed_contributions_remain_unknown` | 1 passed, 22.49 s | 1 passed, 3.93 s |
| `tests/test_pm_maintenance_repair_baseline.py::test_stop_before_selection_preserves_delivery_and_original_baseline[pause]` | 1 passed, 19.99 s | 1 passed, 42.86 s |
| `tests/test_pm_maintenance_repair_baseline.py::test_private_baseline_uses_bounded_http_and_current_token[False]` | 1 passed, 36.14 s | 1 passed, 20.51 s |

These durations are each invocation's complete JUnit session time, rounded to
two decimals, not an individual settle. The ten invocations record **10 passed,
zero failures/errors/skips**. Reports are
`resume-repeat-isolate-N-{head,base}-py314.log` / `.xml`; exact commands and
statuses are in `resume-repeat-isolates.json`. With the first nine pairs, this
continuation records **28 passed individual invocations across 14 head/base
pairs**, without changing product source or assertions. None of the original
two failures or the subsequently exposed problems reproduced in these
individual comparisons. This supports the preparation-stall assessment while
leaving the original lost exceptions and each specific external delay unknown.

### Continuation command forms

The first affected run loaded the scratch `affected-files.txt`; it is
byte-for-byte identical to the committed inventory. The same ordered 127
arguments are reproduced by:

```bash
out="$TMPDIR/visibility-cost"
mapfile -t m4_test_files < <(rg -v '^(tests/test_pm_joined_lifecycle.py|tests/test_operation_helpers.py)$' docs/handoff/2026-10-13-m4-visibility-cost-tests.txt)
test "${#m4_test_files[@]}" -eq 127
```

All following commands ran from the repository root, except the base
invocations, whose working directory was `$TMPDIR/base`, and npm commands,
whose working directory was `desktop`.

| Selection | Command; each invocation used a distinct report path |
| --- | --- |
| Affected Python 3.14, first and repeat | `heavy pytest-disktmp uv run pytest -q -rfE "${m4_test_files[@]}" -o junit_family=legacy --junitxml="$report"` |
| Affected Python 3.10 | `UV_PROJECT_ENVIRONMENT="$TMPDIR/py310" heavy pytest-disktmp uv run --python 3.10 --extra dev pytest -q -rfE "${m4_test_files[@]}" -o junit_family=legacy --junitxml="$report"` |
| Joined/helper Python 3.14 | `M4_PROFILE_LABEL=resume-native-py314 PYTHONPATH="$out:$PWD" heavy pytest-disktmp uv run pytest -q -rfE -p profile_joined tests/test_pm_joined_lifecycle.py tests/test_operation_helpers.py -o junit_family=legacy --junitxml="$report"` |
| Joined/helper Python 3.10 | `UV_PROJECT_ENVIRONMENT="$TMPDIR/py310" M4_PROFILE_LABEL=resume-native-py310 PYTHONPATH="$out:$PWD" heavy pytest-disktmp uv run --python 3.10 --extra dev pytest -q -rfE -p profile_joined tests/test_pm_joined_lifecycle.py tests/test_operation_helpers.py -o junit_family=legacy --junitxml="$report"` |
| Each individual candidate case | `heavy pytest-disktmp uv run pytest -q -rfE "$node" -o junit_family=legacy --junitxml="$report"` |
| Each individual base case | `UV_PROJECT_ENVIRONMENT="$TMPDIR/base-py314" heavy pytest-disktmp uv run pytest -q -rfE "$node" -o junit_family=legacy --junitxml="$report"` |
| Complete candidate baseline file | `M4_PROFILE_LABEL=resume-baseline-file-head-py314 PYTHONPATH="$out:$PWD" heavy pytest-disktmp uv run pytest -q -rfE -p profile_joined tests/test_pm_maintenance_repair_baseline.py -o junit_family=legacy --junitxml="$report"` |
| Complete base baseline file | `UV_PROJECT_ENVIRONMENT="$TMPDIR/base-py314" M4_PROFILE_LABEL=resume-baseline-file-base-py314 PYTHONPATH="$out:$PWD" heavy pytest-disktmp uv run pytest -q -rfE -p profile_joined tests/test_pm_maintenance_repair_baseline.py -o junit_family=legacy --junitxml="$report"` |

`$node` is one of the full node IDs listed above. Reports use the matching
artifact stem documented in each results section. The base export command was
`git archive 2d9a2deb | tar -x -C "$TMPDIR/base"`; its separate environment was
prepared with `UV_PROJECT_ENVIRONMENT="$TMPDIR/base-py314" uv sync --directory
"$TMPDIR/base" --python 3.14 --extra dev`. The scratch timing plugin is the same
one used for the original measurements and is not a repository test change.

### Continuation static and frontend checks

The following checks completed with exit 0 during this continuation. The
`resume-final-checks.json` manifest records each exact command, status and wall
time; separate `resume-final-*.log` files retain their output.

| Command | Result |
| --- | --- |
| `uv run ruff check .` | All checks passed. |
| `python3 scripts/check_public_content.py` | Passed. |
| `python3 scripts/generate_agent_skills.py --check` | Passed; generated skills are current. |
| `git diff --check` | Passed. |
| `git diff --check 2d9a2deb..HEAD` | Passed. |
| `cd desktop && npm run build` | Passed, including TypeScript checking. |
| `cd desktop && npm run build:browser` | Passed. |
| `cd desktop && npm run check:browser` | Passed. |
| `git diff --exit-code HEAD -- src/bat_agent_connector/dashboard` | Passed; regeneration leaves the browser assets unchanged. |
| `cd desktop && npm test` | 37 passed, zero failed, cancelled or skipped (`resume-node.log`). |

No source, test, generated asset or product behavior changed in this
continuation. No full Python suite, Playwright, native build, installation or
live-provider test was run. Screenshots are not applicable to these diagnostic
and documentation changes.

All fresh pytest commands used the disk-temp wrapper. The original interrupted
run's orphaned basetemp was removed after verifying byte-for-byte backups of
its two relevant journals under `$TMPDIR/visibility-cost/original-journals/`.
Only this task's original temporary directory was removed. The raw journals,
JUnit reports, profiles and machine-specific scratch output are not committed.

## Backend merge

Merge `358226810f22d6ad4f4c0edcdc15e8c4876affd9` joins the previous M4 integration
`2d9a2deb` and the exact final backend candidate
`0201c22d257b829453b44291c6145d41ddd61902`. Its message is
“Merge the final backend into the M4 integration”. Existing commits were not
rebased or rewritten.

| Conflicted file | Resolution |
| --- | --- |
| `CHANGELOG.md` | Keep M4's entries in place and all incoming backend entries unchanged, once each, before the existing main entries. |
| `desktop/DESIGN.md` | Retain M4's compact boards, completion evidence, manual read-only observation and withdrawal rules; also retain the backend's opt-in daily resource buckets. |
| `docs/product/implementation-status.md` | Retain the historical M4 head and handoff; keep the incoming current backend status and distinguish historical dates. |
| `docs/product/pm-v1.2-status.md` | Keep the final backend candidate description and the M4 refresh evidence link. |
| `skills/grokbot/bat-agent-connector/SKILL.md` | Regenerate from the merged canonical skill using the repository generator. |
| `skills/hermes/bat-agent-connector/SKILL.md` | Regenerate from the merged canonical skill using the repository generator. |
| `src/bat_agent_connector/pm_conversation_proposals.py` | Keep M4's full-principal, bounded source-reader gate and the backend's guard against storing traversal-dependent request answers. |
| `src/bat_agent_connector/pm_lifecycle_gates.py` | Union the action sets: execution/runtime and quiescence observation plus thread rename. |
| `src/bat_agent_connector/pm_maintenance_repairs.py` | Keep M4's canonical historical source-read scope and the backend's guarded repair traversal. |

The browser dashboard was regenerated from the merged `desktop/src`; only
`dashboard/app.js` changed in the merge. Generated skills and dashboard files
were not hand-merged.

Core remains **0.2.6**, desktop **0.1.2**. The incoming backend remains core
0.2.5, so it does not introduce a subsequent release version. The four-platform
upgrade fixtures retain the previous-release checkpoint
`50dbb7ef98240a3067d048a58e95c746d15f0f00` (core 0.2.5).

## Source-read behavior

The failure's pending BAT reply was downstream of repeated synchronous source
authorization on the event-loop thread. The initial profile after the merge
still spent most CPU re-decoding canonical records and expanding the same
visibility graph. Request memoization alone did not cover operation pre-step
and final-frame validation, and traversal guards prevented completed nested
source answers from being shared.

Each synchronous validation now holds a SQLite read snapshot and joins the
request read memo. Completed positive source decisions use the full principal
(including credential ID), source type and source identity. Whole operation
visibility uses its actual input fields: operation ID, action, target and actor;
domain readers still reload canonical records. No permission depends on caller
results or contexts.

Cycle and budget refusals taint enclosing loads, even if a parent ignores a
refusal and returns true. Traversal-dependent answers remain local to the
original source reader. Active repair backedges and proposal/spec guards cannot
populate the wider visibility memo. The subsequent fix replaces the blanket
proposal guard with tracking of actual source-reader refusals. Completed nested
positives are also looked up without adding recursive loader frames. Negative
decisions and exceptions are not
stored by the new visibility readers. Supported typed depth and node bounds are
unchanged.

Raw canonical operation columns, decoded operation records, action/table
metadata and complete immutable lineage-root sets are independent of these
traversal guards. Their copies may be reused within the same snapshot; callers
still authorize them. Decoded records and fixed plan revisions use immutable
JSON envelopes with fresh parsing for each caller. Root sets and fixed source-ID
lists use immutable collections with fresh mutable copies. None supplies a
permission grant. Every live, credential, lifecycle, deadline and final-frame check
continues to run. Memos close before awaiting or writing, and any write disables
them permanently for that scope, including after rollback.

An operation step that writes between reads cannot carry its earlier grant
forward. It falls back to the original readers for the rest of that memo scope;
the next synchronous validation opens a new snapshot. There is no operation-wide
or cross-step permission cache.

Regression coverage preserves withdrawal after membership changes, scope and
credential isolation, copied-context/thread/task isolation, snapshot boundaries,
rollback invalidation, partial-repair grants, ignored cycles, traversal budgets,
supported typed depths and original-receipt recovery.

Repair traversal is a special case: answers can depend on the repair's own
active backedge. A separate bounded positive boolean map reuses operation
visibility only inside the same live guard token and exact active repair set,
within the same full-principal source snapshot. It never contributes entries to
the ordinary or completed request visibility memo. Switching repairs, exiting a
guard, closing the read or writing prevents reuse; cycles and budget refusals
also prevent storage there. This bounds repeated work without promoting a
partial repair grant into independent authority.

Source predicates also retain a bounded stash of their positive partial proof
when a nested guard temporarily replaces the caller's exact guard/repair
context. Returning to that same context can reuse the caller's proof; a
different guard must recompute it. Stashed answers never enter the ordinary
request memo, and the original node budget remains enforced.

The canonical outer repair gate supplies an explicit completion boundary.
Only when every entered repair walk has completed, no guard/repair remains
active and no traversal refusal occurred since the DAG began can its positive
source and operation booleans join the ordinary memo. Any failed or provisional
walk prevents release. Partial source entries carry their exact guard and repair
context and recompute on a context change, so a failed repair cannot authorize a
different repair. Completed proofs remain limited to the same snapshot and
full principal; a write still disables their reuse.

The GitHub observation callback previously opened several separate source
reads while validating one page or receipt. Its complete synchronous body now
shares one snapshot and completed-read memo. The memo ends on return, before
the provider GET or receipt write. Current authority and original parent
budgets are still checked each time, and no result or budget survives an await.

Historical public snapshot readers hold one read memo across their sibling
source checks, including the evidence loop in snapshot output. Previously each
evidence item opened and closed its own memo despite sharing the same SQLite
snapshot. Authorized evidence/snapshot projections are saved as immutable JSON
and parsed fresh for each caller, avoiding recursive copying of large documents.
Raw native SQLite columns use independent copied envelopes; their scalar values
are immutable. The general cache still deep-copies other value types. Guard,
owner, principal and write invalidation rules apply to every representation.

## Original-session measurements

The before profile used the exact merged source at `35822681`; the after profile
used the final product implementation at `9449be4a`. Both used Python 3.14.5 and cProfile
around the unchanged joined lifecycle file. A scratch pytest plugin recorded
process CPU and wall time, and timed the original settle helper without changing
its bound or behavior. The profile's cumulative entries overlap and must not be
added together. Asynchronous worker frames also include suspension time.

| Measurement | After merge, before optimization | Final implementation |
| --- | ---: | ---: |
| Profiled pytest result | 1 passed, 389.33 s | 1 passed, 265.10 s |
| Profiled session wall / CPU | 389.51 / 285.73 s | 265.26 / 128.14 s |
| Profiled test-body wall / CPU | 340.82 / 284.15 s | 230.19 / 126.87 s |
| Profiled slowest settle | 36.98 s | 20.14 s |
| Unprofiled pytest result | 1 passed, 200.71 s | 8 passed, 250.28 s (joined file plus helper tests) |
| Unprofiled joined test-body wall / CPU | 195.97 / 187.74 s | 151.71 / 80.29 s |
| Unprofiled slowest measured settle | 22.91 s | 53.56 s (0.21 s CPU) |

The unprofiled joined body is **1.29 times faster in wall time** and uses
**2.34 times less CPU**. Profiled session CPU falls **2.23 times**. The profiled
slowest settle is 20.14 seconds, but the native selection includes one successful
53.56-second settle in `test_pm_conversation_proposals.proposed` with only
0.21 seconds CPU. At that checkpoint the requested several-fold wall reduction and
every-settle-far-below-60 target are **not fully demonstrated** on this host.
The final successful profile still spends 64.29 seconds in SQLite execute and
substantial time waiting for subprocesses. All settle bounds remain 60 seconds;
no slower run was hidden or retried until green.

On Python 3.10.20 the final joined/helper selection records **8 passed in
266.69 seconds**. The joined body uses **236.15 seconds wall / 98.85 seconds
CPU**; all 107 measured settles pass, and the slowest is **21.13 seconds**.
Its environment is separate from the worktree's Python 3.14 environment.

| Cumulative profile entry | Before calls / cumulative seconds | After calls / cumulative seconds |
| --- | ---: | ---: |
| `operations._execute` (includes suspension) | 853 / 266.597 | 856 / 213.767 |
| `operations._execute_bound` (includes suspension) | 852 / 265.891 | 855 / 213.274 |
| `pm_source_reads.check` | 2,611,848 / 260.309 | 357,234 / 110.448 |
| `pm_source_lineage.visible_roots` | 1,581,082 / 225.524 | 192,336 / 92.015 |
| `pm_source_lineage._visible_roots` | 66,900 / 218.825 | 23,943 / 89.513 |
| `pm_source_lineage.visible` | 1,580,486 / 228.837 | 191,740 / 84.737 |
| `pm_access.visible` | 370,607 / 172.643 | 177,480 / 66.070 |
| `pm_read_cache.read` | 5,675,101 / 241.571 | 1,921,089 / 96.992 |
| `pm_read_cache.remember` | Not called in this operation path | 1,985,671 / 116.061 |
| SQLite `Connection.execute` | 14,152,851 / 73.452 | 3,078,531 / 64.289 |
| JSON `decode` | 10,273,810 / 63.942 | 3,265,684 / 28.955 |

Source checks decrease **7.31 times**, lineage-root visibility calls **8.22
times**, SQLite calls **4.60 times**, and JSON decodes **3.15 times**. Remaining
cost includes genuine validation of different historical sources, fresh reads
after writes, and copying authorized documents. Those boundaries cannot share a
previous source grant.

The earlier CI traceback is evidence of where the worker was suspended, not
proof that the mocked BAT reply itself consumed the deadline. The merged
baseline reproduces the synchronous CPU cost on Python 3.14; this supports a
shared event-loop starvation cause rather than a Python 3.10-specific SQLite,
lease or cancellation defect. No lease, retry policy, provider latency,
pagination fixture or lost-original-reply simulation was changed.

Profiles and complete failed-attempt traces remain in
`$TMPDIR/visibility-cost/`; they were not overwritten with successful output.
The unprofiled baseline temporarily restored only this worktree's source bytes
from `35822681` and then restored the candidate in a `finally` block. It did not
rewrite commits or use another checkout.

## Files changed after the merge

| File | Purpose |
| --- | --- |
| `pm_read_cache.py` | Full-owner snapshot memo, refusal tracking, completed-read lookup, canonical decoded records and isolated copy strategies. |
| `pm_source_reads.py` | Share completed positive source booleans; retain typed traversal bounds and isolate partial repair contexts. |
| `pm_access.py` | Reuse actual visibility inputs; bound guard-local repair operation booleans without granting wider authority. |
| `pm_source_lineage.py` | Reuse immutable metadata and hold a memo during synchronous source/final-frame validation. |
| `pm_conversation_proposals.py` | Track actual cycle/budget refusal instead of suppressing every completed proposal source answer. |
| `pm_maintenance_repairs.py` | Reload canonical repair context and explicitly distinguish partial traversals from a complete repair DAG proof. |
| `pm_github_observation.py` | Give each complete synchronous authority callback one read snapshot, ending before a GET or receipt write. |
| `pm_release_baselines.py` | Share the memo across sibling checks in existing synchronous snapshot readers. |
| `pm_snapshots.py` | Keep snapshot evidence reads in the same memo; return fresh authorized JSON projections. |
| `pm_evidence.py` | Copy existing authorized JSON projections through immutable saved envelopes. |
| `pm_quality_reviews.py`, `pm_workload.py` | Reuse canonical decoded operation records while retaining existing authorization. |
| `pm_plans.py` | Copy fixed revisions, fixed source IDs and authorized projections through isolated immutable envelopes. |
| `pm_maintenance_policy.py` | Reuse verified raw fixed policy records within a snapshot; still perform source authorization and current-policy checks. |
| `pm_spec_materialization.py` | Mark traversal refusals, always reload canonical admission evidence, and include credential ID in its local traversal key. |
| `tests/test_pm_read_cache.py` | Exercise owner/principal/write isolation, guard contexts, completed DAG proofs and fresh mutable copies. |
| `tests/test_pm_source_reads.py` | Exercise shared nested sources, full scope identity, withdrawal and unchanged traversal limits. |
| `tests/test_dashboard_sync_pm_access.py` | Prove canonical visibility reuse and immediate membership withdrawal across shared read surfaces. |
| `tests/test_pm_maintenance_repairs.py` | Align credential issuance with the fixture's controlled policy clock. |
| `tests/test_pm_maintenance_repair_scheduling.py` | Assert clock alignment and retain rotation/write-count checks; prove future credentials cannot reserve or start a repair. |
| `tests/test_pm_publication_source_reads.py` | Keep the legacy comparison independent of the new memo, retain every strict threshold and privacy assertion, and add zero-write/same-read reuse checks. |
| `tests/test_pm_spec_materialization.py` | Keep original-key replay and owned Git assertions; cover compact/forged inputs, canonical admission evidence, immediate withdrawal and zero read writes. |
| `tests/test_pm_maintenance_repair_baseline.py` | Add parent/child/operation diagnostics to the existing delivery assertion without changing its condition. |
| `docs/design/pm-conversation-proposals.md`, `docs/design/pm-spec-materialization.md`, `CHANGELOG.md` | Record the new reuse boundary and canonical spec-reader input while preserving existing entries. |

The prior helper fix remains intact: `settle_operations` and joined-maintenance
`advance` use public `worker_details` and `stop_workers`, with `_worker_details`
retained as a compatibility alias. Both cancel and join only pending in-memory
workers before journal teardown; they do not force uncertain children or mutate
durable receipts. The other direct worker references found in tests are
completion assertions, individual cancellation/recovery tests, or unrelated
`asyncio.wait` synchronization, rather than another operation-settle helper.
The file-by-file audit is retained in the
[worker-cleanup record](2026-10-13-m4-worker-cleanup.md#other-worker-waits-audited).

## Preserved development evidence

Every profile rerun below followed a code change. No unchanged failing run was
retried until it passed. The original CI log and earlier integration reports
remain unchanged; the following scratch logs preserve new failures as well.
CPU numbers for failed profiles cover incomplete tests and are not speedup
measurements.

| Profile log | Result | Session CPU | Change or remaining finding |
| --- | --- | ---: | --- |
| `attempt1-after-profile.log` | 1 failed, 219.13 s | 149.02 s | A 62.849-second observation wave left the original repair waiting and no delivery child; key construction traversed unused large operation documents. |
| `attempt2-after-profile.log` | 1 passed, 490.36 s | 284.19 s | Normalize visibility inputs and inspect remaining canonical reads. |
| `attempt3-after-profile.log` | 1 passed, 294.67 s | 283.22 s | Share only guard-independent immutable metadata. |
| `attempt4-after-profile.log` | 1 passed, 314.77 s | 304.26 s | Track actual refusals and publish completed nested positive source reads. |
| `attempt5-after-profile.log` | 1 passed, 333.92 s | 317.99 s | Look up nested completed reads without extra recursive loader frames. |
| `attempt6-after-profile.log` | 1 failed, 159.46 s | 93.04 s | `advance` expired with `pm.github.observe` waiting on `bounded_get`; its page callback still reopened independent authority reads. |
| `attempt7-after-profile.log` | 1 passed, 327.07 s | 287.06 s | Share the complete callback read and retain ignored-refusal tracking. |
| `attempt8-after-profile.log` | 1 passed, 280.23 s | 245.62 s | Release only completed canonical DAG proofs and isolate changed guard contexts. |
| `attempt9-after-profile.log` | 1 passed, 168.54 s | 153.07 s | Share existing historical snapshot readers across sibling evidence checks. |
| `after-profile.log` | 1 passed, 163.92 s | 151.68 s | Isolated copy strategies; subsequent statement-bound coverage found redundant visibility savepoints. |
| `final-after-profile.log` | 1 passed, 158.31 s | 150.46 s | Validate borrowed state and serve completed visibility before opening another source savepoint. |
| `final3-after-profile.log` | 1 passed, 156.95 s | 150.01 s | Preceding canonical spec-reader compatibility checkpoint. |
| `final4-after-profile.log` | 1 failed, 235.86 s | 47.87 s | Incomplete private observation after 14 pages; retained wall-wait evidence. |
| `final5-after-profile.log` | 1 passed, 265.10 s | 128.14 s | Final product at `9449be4a`, including isolated decoded and fixed records. |

The failed wave's diagnostic trace is retained verbatim in
`attempt6-after-profile.log` (`tests/pm_joined_maintenance_helpers.py:66`). It
records a running `pm.github.observe`, successful earlier page receipts, and an
await chain ending in `github.bounded_get` / `asyncio.wait_for`. Existing
failure cleanup cancelled and joined its pending workers before teardown.

| Focused regression log | Passed / failed | Change after a failure |
| --- | ---: | --- |
| `first-safety.log` | 66 / 2 | Move an incoming synthetic repair-budget test seam to the canonical source body; keep nested traversal flat so the supported typed depth is unchanged. |
| `second-safety.log` | 71 / 0 | |
| `third-safety.log` | 81 / 1 | The new visibility-reuse test must allow the exact SQLite WAL anchor read, rather than require zero SQL; withdrawal and write assertions stay intact. |
| `fourth-safety.log` | 57 / 0 | |
| `fifth-safety.log` | 83 / 0 | |
| `metadata-safety.log` | 1 / 0 | |
| `sixth-safety.log` | 85 / 0 | |
| `seventh-safety.log` | 86 / 0 | |
| `eighth-safety.log` | 90 / 0 | |
| `ninth-safety.log` | 92 / 0 | |
| `tenth-safety.log` | 139 / 1 | The new write/withdrawal fixture must own the transaction so source-reader failure does not roll its deletion back. It now checks the persisted withdrawal, invalidated map and fresh false result. |
| `withdrawal-safety.log` | 2 / 0 | |
| `eleventh-safety.log` | 95 / 0 | |
| `twelfth-safety.log` | 162 / 0 | |
| `thirteenth-safety.log` | 206 / 0 | |

The intermediate Ruff runs also remain in `first-ruff.log` through
`thirteenth-ruff.log`: eleven passed; the sixth found one unused import after
removing the blanket proposal guard, and the tenth found one import-order
error in a new test. Both were corrected before the subsequent checks.

`interrupted1-affected-py314.log` records an intentionally interrupted broad
selection: 293 passed, no reported test failure, exit 2 after 348.99 seconds.
It preceded the isolated-copy change and is not claimed as a completed check.
`joined-py314.log` records the preceding 8-pass native run in 92.89 seconds;
the final native result above is from the final implementation.

The first final affected selection exposed a separate fixture-clock defect in
`test_current_owner_credential_rotation_preserves_same_controller`:
`get_repair` found no reservation and raised `[NOT_FOUND] Repair unavailable` at
`pm_maintenance_repairs.py:119`. Credential issuance used real wall time while
eligibility used a frozen policy clock and advanced it by only one second. A
slower setup/admission could put the new credential's `created_at` beyond that
clock; `_current_principal` correctly rejects future credentials.

`failed1-affected-py314.log` and its JUnit file preserve **1 failed / 1,104
passed**, exit 2 after 1,774.52 seconds. The run was interrupted after the failure
so its captured traceback could be inspected before a correction, rather than
finish a selection that needed revalidation. A new exact issuance-clock
assertion reproduced the mismatch (`clock-regression-before.log`: **1 failed,
2.19 seconds**), even on a faster isolated run. After patching only the fixture's
`api_auth.time` namespace alongside its other module clocks,
`clock-regression-after.log` records **10 passed in 22.53 seconds**.

The negative regression deliberately issues a future credential and retains the
product's `PM_MAINTENANCE_AUTHORITY_UNAVAILABLE` result, zero repair rows, zero
child rows and zero BAT writes. The fixture fix does not loosen authorization,
move a deadline or change a settle bound. The joined lifecycle test does not use
this fixture, so its measured implementation and scenario remain unchanged.

The corrected broad selection then exposed a legacy-reference problem in
`test_saved_publication_reuses_original_verifier_sources_without_losing_privacy`:
its older quality predicate called current readers, which now inherited the new
memo. The unchanged assertion failed as `(1139 * 3) < (1589 * 2)` at line 71;
it was comparing two optimized paths rather than the original reader and the
optimized reader.

`failed2-affected-py314.xml` preserves that full traceback: **1 failed / 1,365
passed**, 2,184.342 seconds. Interruption also exposed a pytest `tmp_path`
teardown `KeyError` in `_pytest.stash`, so the text log ended with an
infrastructure traceback and exit 1 rather than a normal summary. The JUnit
failure and completed-case results remain intact.

The legacy comparison now bypasses only the newly added request memo and
operation-visibility reuse inside its existing monkeypatch contexts. It still
runs the original source predicates and local traversal bounds. Every original
strict ratio, output equality, credential-denial, read-order and withdrawal
assertion is unchanged. New checks require zero writes and reuse on a second
full repair read within the same snapshot. `publication-reference.log` records
**1 passed in 34.00 seconds**:

| SELECT queries | Legacy reference | Optimized read |
| --- | ---: | ---: |
| Saved publication | 137,239 | 1,139 |
| Full repair sources | 13,396 | 1,139 |
| Full repair before quality-source reuse | 5,306 | 1,139 |
| First and repeated repair read in one scope | 1,138 (first) | 3 (repeated) |

These comparator changes affect tests only. At that point the earlier completed
cases still used the current product code, so the corrected publication file
and remaining files were checked separately. The later statement-count fix
below changes shared product code and supersedes those partial runs with fresh
complete affected selections.

The remaining selection found a genuine statement-count regression in
`test_schedule_page_walk_stays_under_the_statement_bound`: five pages used
`[334, 334, 334, 334, 334]`, totaling 1,670 statements against the unchanged
1,600 bound. `failed3-remaining-py314.log` records **1 failed / 452 passed in
471.76 seconds** and the complete traceback at `test_pm_schedule.py:1699`.

The cause was opening a source savepoint and WAL anchor even when a completed
visibility grant already belonged to the caller's read snapshot. The optimized
reader now validates borrowed source state before consulting the same-principal
completed memo. A hit needs no additional SQL; a miss, active guard or partial
repair still enters the original source scope and bounds. Validation retains
owner, loop-turn, mutation and nested-depth rejection, so an inherited scope
cannot bypass those checks. The existing membership-withdrawal test now requires
zero SQL for the repeated visibility read, and new same-task/copy-task tests
require rejection after a loop turn even when a completed grant exists.

`completed-visibility-safety.log` records **101 passed in 12.94 seconds**,
including the unchanged schedule bound. This final product change requires a
fresh complete affected selection on both interpreters; the earlier partial
selections are retained as development evidence rather than claimed as final
validation. `final-after-profile.log` and `final2-joined-py314.log` provide that
checkpoint's measurements. Those and the preceding successful measurements
(`after-profile.log`, `final-joined-py314.log`) remain preserved; the final-source
profile and native measurements above use `final5-after-profile.log` and
`final5-joined-py314.log`.

`failed4-affected-py314.log` and its JUnit file preserve **1 failed / 1,902
passed in 3,642.36 seconds**, exit 1. The existing owned-spec test's original-key
replay raised `[NOT_FOUND] operation not found` at `pm_access.py:120` through
`operations._prepare_create`. The compact visibility projection contains the
operation's identity, action, target and actor. Spec materialization previously
reloaded its record only when `action` was missing, then expected the omitted
`external_refs.admission_binding` and returned false on `KeyError`.

The spec reader now always loads the canonical operation via the raw-column
memo before proving its original package and digest. Its local traversal key
also includes credential ID. Caller projections cannot supply admission
authority. This changes only the spec-materialization reader; other actions
continue through their existing readers. The new regression checks compact and
forged admission inputs, zero writes during reads, immediate membership
withdrawal, unchanged human checkout and zero BAT writes. The existing replay,
immutable original receipts and seven-file owned commit assertions remain.
`spec-canonical-py314.log` records **4 passed in 15.75 seconds** for the canonical
reload change; the complete consumer selection also checks the subsequent
credential-key addition.

All eight spec-materialization consumer files found by `rg`, including the
previously unselected API file, are rechecked alongside the untouched remainder
of the stopped selection: 20 files. Completed nonconsumer files from the prior
selection remain valid because this localized fix does not affect their action
readers. The final inventory is 129 files, not the full backend suite.
`final-remaining-py314.log` records **352 passed in 555.64 seconds**. The fix is
commit `f5bfe449`; its joined/profile checks remain a preceding checkpoint.
The subsequent shared optimization supersedes that checkpoint's broad validation.

### Additional cost and diagnostic evidence

The first Python 3.10 affected selection at `f5bfe449` stopped with **1 failed /
902 passed in 1,479.06 seconds** (`failed1-affected-py310.log` / `.xml`). The
existing baseline fixture expected a private delivery child after ten original
repair attempts, but the parent remained `waiting_external`. The assertion now
prints the parent, child and operation statuses; its condition is unchanged.

`baseline-diagnostic-py310.log` records **1 failed in 167.24 seconds**. A scratch
failure hook then captured original journal receipts before teardown, and
isolated instrumentation runs recorded **1 passed in 28.77 seconds**
(`baseline-capture-py310.log`) and **1 passed in 36.55 seconds**
(`baseline-profile-py310.log`). These diagnostic passes are not claimed as a
fix or final validation. No timeout, provider latency or fixture scenario was
changed between them.

A new exact-guard reuse test first failed (**1 failed in 4.38 seconds**,
`guard-context-before.log`): entering a nested guard evicted its caller's
positive proof, causing three predicate calls instead of two. The bounded stash
now restores only that caller's exact live guard proof. The following selection
recorded **102 passed / 1 failed in 425.32 seconds**
(`guard-context-safety-py314.log`); its joined observer fixture failed before
entering a repair, while its original maintenance transition remained blocked.

After the decoded-record optimization, `raw-decode-safety-py314.log` records
**127 passed in 228.56 seconds**. The Python 3.10 selection records **127 passed /
1 failed in 160.73 seconds** (`raw-decode-safety-py310.log`): again the baseline
fixture had no private delivery child. The scratch capture established that the
owned GitHub observation was incomplete with `reason=observation_time_limit`,
13 successful pages and no second traversal. The parent correctly retained
“Waiting for the adopted required-CI observation interval”; the frozen fixture
clock had not advanced to that interval. No uncertain child was forced.

The full page-check diagnostic (`page-checks-py310.log`) records **1 failed in
71.34 seconds**, later in the original `delivery.merge_and_deploy` worker.
All 232 observed authorization callbacks had active, same-task, same-thread,
unchanged-transaction memos. Their total wall/CPU cost was **3.404 / 3.358
seconds**, with the slowest callback **0.049 seconds**. This rules out a disabled
or inherited memo in that diagnostic run. Its retained settle traceback ends
in `pm_maintenance_repair_delivery.bounded_run` / `asyncio.wait_for` after
`deploy.select` and `merge.submit` succeeded. Workers were cancelled and joined
by the unchanged failure cleanup.

`final4-after-profile.log` records **1 failed in 235.86 seconds**, after
85 successful measured settles. The joined body took **216.68 seconds wall /
46.43 seconds CPU**, then found no private delivery child. The original observer
had again exhausted its unchanged time budget, this time after 14 pages. The
profile's largest self entries were subprocess `select.poll` (**125.097
seconds**) and SQLite execute (**57.844 seconds**); session CPU was **47.87
seconds** over **236.10 seconds** wall. This incomplete run is not a speedup
measurement and is retained separately from the successful preceding profile.

The last shared change also memoizes verified raw fixed policy/baseline records,
retaining separate authorization and invalidating on any write. Its new tests
check fresh nested copies, one raw SELECT, zero read writes and immediate
corruption detection after a write. `fixed-record-safety-py314.log` records
**164 passed in 172.50 seconds**. `fixed-record-ruff.log` passes; the preceding
raw-decoder Ruff failure found four obsolete imports, which were removed before
`raw-decode-fixed-ruff.log` passed.
