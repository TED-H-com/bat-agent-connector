# Contributing

Thanks for helping! This is an unofficial companion to Better Agent Terminal; please keep it small and safe.

* Open an issue before large changes, especially anything that adds a protocol channel.
* New channels must be classified (read / write / orchestrate / never) in `channels.py`, with a test proving they are
  blocked when their tier is off. Destructive channels (stop/reset/kill, file ops, settings, installs, account changes)
  are not accepted.
* Every write-capable tool needs `confirm=true`, rate limiting, and an audit record without message bodies.
* Tests: `uv run ruff check . && uv run pytest`. Unit tests must run against the mock server in `tests/mockbat.py`.
  Never write tests that send write channels to real hosts.
* Never commit real hostnames, IPs, fingerprints, tokens, session ids, workspace names or emails. CI runs a secret
  scan; please also run `gitleaks detect` locally.
* Protocol behaviour should cite the BAT source file/version it was read from.

Use `tests.operation_helpers.settle_operations` to wait for durable test outcomes.
Its 60-second deadline covers scheduling, persisted status and worker completion;
it is not a per-handler runtime measurement. On timeout it reports each worker's
action, steps and suspended await chain without request bodies or frame locals.
On failure or caller cancellation it cancels and joins this service's in-memory
workers before the fixture can close their journal. This leaves original durable
operation IDs, keys and uncertain step receipts available for normal reconciliation;
it does not call the product's operation-cancel action or rerun a test.

Keep test journals on disk with normal durability settings. Slow synchronous
storage can exhaust the settling deadline and block the event loop itself; an
async timeout cannot preempt an SQLite commit. Diagnose storage latency and worker
stacks before changing a handler. Do not increase deadlines or disable durability
to hide a failed settling assertion.

Maintenance-repair fixtures use the production lineage, new-effect and retry
hooks installed by TaskDaemon and OperationService. Do not wrap those hooks with
old integration stand-ins: doing so repeats source validation before each step
and frame, consuming the settling budget even when provider replies are prompt.
Keep original-key, withdrawal, write-count and retry-budget assertions against
the production hooks; mock only the external providers and managed test resources.

Public documentation, screenshots, examples and issue/PR text must work without a
maintainer's private Fleet inventory or deployment. Use synthetic host names, reserved
example addresses and generic user paths. Keep private installation evidence, operator
conversation links and machine-specific incident logs outside the repository. Public
author and license attribution should remain intact.

Run `python scripts/check_public_content.py` before publishing. It checks tracked files
for private configuration filenames, shared conversation links and non-example home
paths, and prints locations without matched values. It cannot recognize every private
hostname or deployment narrative: review those manually, including images and GitHub
text. Changing current files does not remove information from Git history or old copies.

## Continuous integration

Pull requests run what the change can affect; every workflow triggered by a push
to `main` runs at full coverage. `desktop.yml` retains its existing PR/push path
filters, so a change to only `README.md` and `docs/` triggers `ci.yml`, not desktop CI.
Both workflows use the same standard-library `scripts/ci_scope.py` policy in a
small `scope` job. Checkout uses `fetch-depth: 2`; the default PR checkout is its
merge commit, and `git diff --no-renames --name-only HEAD^1 HEAD` compares the merged
result with the target branch tip. This measures the PR's net change without
including changes made only on `main` since the branch point. Disabling rename
detection includes both old and new paths. A non-PR event, unavailable diff, empty
or ambiguous path list selects full coverage. The `ci.yml` summary explains only
pytest versions; the `desktop.yml` summary explains only native builds.

| Workflow | Job | Checks |
| --- | --- | --- |
| `ci.yml` | `scope` | Select Python versions and explain why |
| `desktop.yml` | `scope` | Select native build level/entries and explain why |
| `ci.yml` | `lint`, `secrets-scan` | Static checks once on Python 3.13; secret scan |
| `ci.yml` | `pytest <version> (i/5)` | Five runners per selected version: 3.10 for PRs without Python source/tooling changes; 3.10–3.13 otherwise and on `main` |
| `ci.yml` | `pytest-complete` | Successful shards and exact collection coverage for exactly the selected versions |
| `desktop.yml` | `frontend` | Regenerate/check browser assets and run Node tests |
| `desktop.yml` | `browser-ui (i/3)` | Three Playwright shards on committed browser assets |
| `desktop.yml` | `central-ui` | Nineteen named central-service UI script steps |
| `desktop.yml` | `build` | PRs select no builds, Linux only, or all four builds; triggered `main` runs always select all four. Selected entries retain every native/packaged fixture |
| `desktop.yml` | `windows-endpoint-oracle` | Windows endpoint fixtures on every run |

Read `pytest-complete` for the combined pytest check: every shard must succeed and
the manifests must prove complete coverage without duplicates for each selected
version. No tests are removed or deselected by scope selection; the five shards
still execute the whole suite once per version. `central-ui` also runs the Connector
on Python 3.13. Frontend, browser UI, central UI and Windows endpoint jobs are not
gated by scope.

The full Python matrix is required by any `.py`/`.pyi` file, Python environment or
test configuration (`pyproject.toml`, `uv.lock`, `uv.toml`, `.python-version`,
`pytest.ini`, `tox.ini`, `setup.cfg`, `.coveragerc`, `requirements*.txt`), or anything
under `tests/`, `scripts/`, `desktop/scripts/` or `.github/workflows/`. This includes
conftests, the shard plugin, measured weights, fixture data and Python invocation
scripts. Other PR changes use the oldest supported Python, 3.10.

Native level `none` skips builds when **every** changed path matches this allowlist:

| Allowed path | Why it can skip native builds |
| --- | --- |
| `desktop/src/**` | Shared frontend source, rebuilt by Vite; frontend/browser/central jobs cover it |
| `desktop/tests/**/*.ts` (including direct children and `demo/`) | Browser/Node tests and their TypeScript helpers run independently; no native build step or its relative import closure loads them |
| `docs/**` | Documentation is not read by native build or packaging steps |
| `AGENTS.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `README.zh-TW.md`, `desktop/DESIGN.md` | Named documentation files are not native package resources or build inputs |

Native level `linux` runs only `ubuntu-24.04`/deb when every path is on that
allowlist or under `src/bat_agent_connector/dashboard/`, with at least one generated
asset change. PyInstaller collects these assets into the managed runtime. The Linux
packaged managed-setup fixture exercises the served dashboard, and the deb fixture
exercises the installed WebView. Windows/macOS only copy these browser assets;
the Tauri WebView uses a separate Vite build. Linux keeps all its existing steps.

Native level `all` runs all four builds for anything else, including unknown paths,
native code, lockfiles, build configuration and non-TypeScript fixture helpers.
`desktop/tests/**` is not blanket-allowed: `.mjs`, `.py`, `.swift`, `.ps1`, JSON and
other scripts/data still require all native builds. `README.md` is Python package
metadata;
`THIRD_PARTY_NOTICES.md` is both package metadata and an explicit Tauri resource.
Neither is on the allowlist, though a README/docs-only change does not trigger
desktop CI. Non-PR events and all fallback cases also select `all`.

The four build entries have one production definition, `NATIVE_BUILDS` in
`scripts/ci_scope.py`. Its `builds` JSON output feeds an `include`-only matrix via
`fromJSON(needs.scope.outputs.builds)`, with no axes that could create extra
combinations. GitHub documents [one job per entry when only `include` is
specified](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstrategymatrixinclude)
and [job conditions before matrix expansion](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idif).
The build job skips `none` before evaluating its empty list. Explicit names retain
`build (ubuntu-24.04, deb, desktop-Linux-unsigned)` and the corresponding OS entries.
Changes to build inputs or resource declarations must also update this policy and
its tests.

`tests/test_ci_scope_native_guard.py` parses the actual native `build` steps,
including `npm run` entrypoints, and follows their relative imports transitively.
If a native step or dependency loads TypeScript, its failure instructs maintainers
to remove `desktop/tests/**/*.ts` from the allowlist. Untraceable computed imports
also fail the guard. PyYAML is a locked dev-only dependency for this guard; the
scope classifier uses only the standard library.

After `uv sync --locked --extra dev`, run one shard from the repository root.
Use a disk-backed `TMPDIR`:

```sh
pytest-disktmp uv run pytest -q -p tests.ci_shard_plugin \
  --ci-shard-index=1 --ci-shard-count=5 \
  --ci-shard-manifest="$TMPDIR/shard-1/manifest.json"
```

To refresh the measured per-file durations, download all
`pytest-*` artifacts from one successful GitHub run, preserving subdirectories:

```sh
uv run python -m scripts.ci_complete "$TMPDIR/pytest-artifacts" \
  --python 3.10 3.11 3.12 3.13 --shards 5
uv run python -m scripts.ci_durations "$TMPDIR/pytest-artifacts"
```

The example verifies a full-matrix run from `main`; for a scoped PR run, pass only
the Python versions listed in that run's scope summary to `--python`.

Commit the refreshed `tests/ci_durations.json`. Required local full checks are unchanged:
ruff, the full Python suite, and Python 3.10 for backend changes. Use `pytest-disktmp`
(the development host wrapper) or a unique disk-backed `--basetemp`, and remove the
temporary directory after the run. Never use `/dev/shm` or another tmpfs. Run full
suites one at a time.

## Working after the October 10, 2026 history rewrite

Start new contributions from the cleaned public `main`, preferably in a fresh clone.
Rebase or cherry-pick only reviewed work onto that history; do not merge old ancestry
back into the repository. Review the resulting diff for private material before pushing.
Historical source references in documentation use retained rewritten commits. Historical
CI results still describe their original runs; remapped references do not claim reruns
or validate new packages. Use current `main` checks and matching desktop artifacts.
