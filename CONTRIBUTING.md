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

Pull requests run what the change can affect; pushes to `main` run everything.
Both workflows use the same standard-library `scripts/ci_scope.py` policy in a
small `scope` job. Checkout fetches the PR base/head commits and compares them with
`git diff --no-renames --name-only` (both old and new paths for renames); a non-PR
event, unavailable diff, empty or ambiguous path list selects full coverage.
Each scope job explains its selection in its summary.
The workflows have no event path filters, so an unknown path cannot bypass the
native allowlist and every push to `main` retains full coverage.

| Workflow | Job | Checks |
| --- | --- | --- |
| Both | `scope` | Select Python versions/native builds and explain why |
| `ci.yml` | `lint`, `secrets-scan` | Static checks once on Python 3.13; secret scan |
| `ci.yml` | `pytest <version> (i/5)` | Five runners per selected version: 3.10 for PRs without Python source/tooling changes; 3.10–3.13 otherwise and on `main` |
| `ci.yml` | `pytest-complete` | Successful shards and exact collection coverage for exactly the selected versions |
| `desktop.yml` | `frontend` | Regenerate/check browser assets and run Node tests |
| `desktop.yml` | `browser-ui (i/3)` | Three Playwright shards on committed browser assets |
| `desktop.yml` | `central-ui` | Nineteen named central-service UI script steps |
| `desktop.yml` | `build` | Four native OS builds, packaged runtime/upgrades, native checks, packaging and installed smoke tests; skipped only for PR paths on the native allowlist |
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

Native builds skip only when **every** changed path matches this allowlist:

| Allowed path | Why it can skip native builds |
| --- | --- |
| `desktop/src/**` | Shared frontend source, rebuilt by Vite; frontend/browser/central jobs cover it |
| `desktop/tests/**/*.spec.ts`, `desktop/tests/**/*.test.ts` (including direct children) | Browser/Node tests run independently; these files are not packaging inputs or native fixture scripts |
| `docs/**` | Documentation is not read by native build or packaging steps |
| `AGENTS.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `README.zh-TW.md`, `desktop/DESIGN.md` | Named documentation files are not native package resources or build inputs |

Everything else runs all four builds, including unknown paths, native code,
lockfiles, build configuration and fixture helpers. `desktop/tests/**` is not
blanket-allowed: its Python, JavaScript and PowerShell scripts/data exercise native
and installed packages. Generated `src/bat_agent_connector/dashboard/` assets also
require native builds: PyInstaller collects the entire Python package into the
managed runtime, Tauri bundles that runtime, and the Linux packaged managed-setup
fixture checks its served dashboard. They can affect package validation even
though the Tauri WebView uses a separate Vite build. `README.md` is Python package metadata;
`THIRD_PARTY_NOTICES.md` is both package metadata and an explicit Tauri resource.
Neither is on the allowlist. Changes to build inputs or resource declarations must
also update this policy and its tests.

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
