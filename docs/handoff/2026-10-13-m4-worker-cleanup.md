# M4 main refresh and maintenance-wave worker cleanup

This follow-up preserves the [earlier M4 failure and validation evidence](2026-10-13-m4-refresh.md).
It adds test infrastructure only, after merging the requested main commit.

## Merge

Merge commit `30cf73b7d1e9155543372e6b5e0ef0fcca894a40` has parents
`156e00ef5532c3dbd04ef1eacef2c3cab4fc07ce` and
`e7711948dab487de9b336cad3bceef220fbb0e9f`. No M4 commit was rewritten.
This brings in #124 Fleet startup/UIA changes, #126 settling diagnostics, and
#128 demo-project fixtures without additional native or UI behavior changes.

`CHANGELOG.md` was the only conflict. The two M4 entries remain unchanged at the
top, followed by main's Windows, PM-label and settling-diagnostics entries in
main's order. A byte-for-byte comparison verified that removing those two M4
entries yielded main's complete changelog, with each M4 block appearing once.
The later helper commit adds a separate maintenance-wave entry.

The browser build was regenerated from source before the merge commit and was
identical to the merged assets. No generated file was hand-edited.
`npm run check:browser` passed after the merge commit.

Versions remain core `0.2.6` / desktop `0.1.2`. The merged main is still core
`0.2.5` / desktop `0.1.1`, so the upgrade fixture's previous-release start remains
`50dbb7ef98240a3067d048a58e95c746d15f0f00`; no newer release version was introduced
by this merge. No installed upgrade result is claimed here.

## Worker cleanup

Commit `ba9e55d3` changes:

- `tests/operation_helpers.py`: expose `worker_details` and `stop_workers`, retain
  `_worker_details` as an alias, and use the public helpers in `settle_operations`.
- `tests/pm_joined_maintenance_helpers.py`: include worker/action/status/step/await
  details in an expired `advance` wave, then cancel and join this service's pending
  workers before propagating any failure or caller cancellation.
- `tests/test_operation_helpers.py`: exercise the actual `advance` helper alongside
  `settle_operations`, with one and two stuck receipt workers and caller cancellation.
  A module-local fixture clock expires the unchanged 60-second wave after workers
  enter their await. Assertions retain started receipts and running durable status,
  verify cleanup can still read the open journal, require no pending workers, and
  recover the original keys without a second send. The existing completed-receipt
  worker test remains in place. Fixture teardown also uses `stop_workers`.
- `CONTRIBUTING.md` and `CHANGELOG.md`: document the shared diagnostic/cleanup rule.

There are no product-operation, receipt, deadline, forced-child or schema changes.
This closes the test-only worker leak after a failed captured-wave wait; it does
not establish a new cause for the historical Python 3.10 observation timeout.

## Other worker waits audited

Search: `rg -n '\._active|asyncio\.wait\(' tests --glob '*.py'`.

- `operation_helpers.settle_operations` and `pm_joined_maintenance_helpers.advance`
  are the only shared helpers directly waiting on operation workers. Both now use
  the same diagnostics and cleanup.
- `test_operation_helpers.ops` already cancels/joins at teardown; it now reuses
  the public helper.
- `test_checkpoints.py` and `test_integration.py` directly capture a worker in
  explicit pre/post-frame cancellation tests. Both already cancel and join it in
  `finally`; they are not additional settling helpers.
- `test_pm_native_operations.py` explicitly cancels and gathers a worker to test
  native-runtime shutdown and original-operation recovery. Its fixture and those
  in `test_pm_conversation_proposals.py` / `test_pm_follow_up_analysis.py` only
  assert worker completion; they do not implement independent waits.
- `test_confinement_recovery.py` waits on synthetic start/readiness tasks, not
  `OperationService` workers. Both tasks already have cancellation/join cleanup,
  and the readiness assertion includes the suspended stack.

## Validation

All requested checks run sequentially. Test environments are Python `3.14.5` and
`3.10.20`, prepared with:

```sh
uv sync --python 3.14 --extra dev
UV_PROJECT_ENVIRONMENT="$TMPDIR/py310" uv sync --python 3.10 --extra dev
```

Results:

| Check | Result |
| --- | --- |
| `uv run ruff check .` | Pass |
| `cd desktop && npm run build:browser` (before merge commit) | Pass; generated content unchanged |
| `cd desktop && npm run check:browser` (after merge commit) | Pass |
| Python 3.14 targeted selection below | 8 passed in 246.56 s |
| Python 3.10 targeted selection below | 8 passed in 268.43 s |
| Full Python 3.10 suite below, under `heavy` | 5,821 passed, 48 skipped in 7,711.46 s; no failures or warnings |
| `cd desktop && npm run build` | Pass (TypeScript and Vite) |
| `cd desktop && npm run check:browser` (final) | Pass |
| `cd desktop && npm test` | 37 passed, 0 failed, 0 skipped |
| `python3 scripts/generate_agent_skills.py --check` | Pass |
| `python3 scripts/check_public_content.py` | Pass, including the staged follow-up report |

Both targeted runs passed all 7 cases in `tests/test_operation_helpers.py` and the
1 case in `tests/test_pm_joined_lifecycle.py`. Each produced one warning about
`record_property` with JUnit's default `xunit2` format; neither had a test failure.
Exact targeted commands:

```sh
pytest-disktmp uv run pytest -q tests/test_operation_helpers.py tests/test_pm_joined_*.py --junitxml="$TMPDIR/follow-up/targeted-py314.xml"
UV_PROJECT_ENVIRONMENT="$TMPDIR/py310" pytest-disktmp uv run --python 3.10 --extra dev pytest -q tests/test_operation_helpers.py tests/test_pm_joined_*.py --junitxml="$TMPDIR/follow-up/targeted-py310.xml"
```

Additional check invocations, in order:

```sh
UV_PROJECT_ENVIRONMENT="$TMPDIR/py310" heavy pytest-disktmp uv run --python 3.10 --extra dev pytest -q -p no:cacheprovider
cd desktop && npm run build && npm run check:browser && npm test
# From the repository root:
python3 scripts/generate_agent_skills.py --check
python3 scripts/check_public_content.py
```

The full suite ran once. There were no failed tests to retry or failure traces to
replace. Its log has no unhandled-task/closed-journal diagnostics. The two targeted
runs also ran once each. Existing skip conditions and safety assertions remain
unchanged. All pytest temporary journals and repositories used `pytest-disktmp`.

Logs remain under `$TMPDIR/follow-up/`: `ruff.log`, `targeted-py314.log`,
`targeted-py310.log`, `full-py310.log`, `merge-build-browser.log`,
`post-merge-check-browser.log`, `desktop-build.log`, `final-check-browser.log`,
`node-tests.log`, `skills-check.log` and `public-content-check.log`. The two targeted
JUnit XML files are alongside those logs. This follow-up did not overwrite the
first refresh's logs or failure evidence.

## Boundaries

The branch is a local candidate; pushing, PR updates, merging to main and checking
CI for the final head remain with the orchestrator. No installed, native Windows,
live BAT/provider or four-platform upgrade acceptance is claimed. This follow-up
changed no shared frontend source or generated content and took no new screenshots;
the earlier UI evidence remains linked from the first refresh record.
