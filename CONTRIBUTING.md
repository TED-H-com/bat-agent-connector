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

`ci.yml` runs static checks once on Python 3.13 (`lint`), the secret scan, and five
separate pytest runners for each of Python 3.10–3.13. Read `pytest-complete` for the
combined pytest result: every runner must succeed, and downloaded manifests must
prove an exact, disjoint partition of each version's full collection. Manifests and
JUnit XML are retained for seven days, including on failure. The manifest hashes the
sorted full node-ID list as compact ASCII JSON with SHA-256; parameter IDs are included.
Files stay together to preserve module fixtures. Assignment is longest-first, with
path ordering for ties and the table median for new files. No xdist processes share a runner.

After `uv sync --locked --extra dev`, run one shard from the repository root:

```sh
pytest-disktmp uv run pytest -q -p tests.ci_shard_plugin \
  --ci-shard-index=1 --ci-shard-count=5 \
  --ci-shard-manifest="$TMPDIR/shard-1/manifest.json" \
  --junitxml="$TMPDIR/shard-1/junit.xml" -o junit_family=xunit1 --durations=15
```

Use a disk-backed scratch directory for `TMPDIR`. Add `--collect-only` to inspect
the selection without running tests. The plugin is opt-in; existing pytest commands
retain their usual behavior. The required local full checks are unchanged: ruff,
the full Python suite, and Python 3.10 for backend changes. Use `pytest-disktmp` for
local pytest and the shared `heavy` lock when running full suites on a shared host.

The initial `tests/ci_durations.json` explicitly uses collected test counts as weights,
not measured seconds. After a successful GitHub run, download all `pytest-*` artifacts
from that single run into a directory, preserving their individual subdirectories:

```sh
uv run python -m scripts.ci_complete "$TMPDIR/pytest-artifacts" \
  --python 3.10 3.11 3.12 3.13 --shards 5
uv run python -m scripts.ci_durations "$TMPDIR/pytest-artifacts"
```

Commit the refreshed table. The refresh sums setup/call/teardown seconds per file in
each JUnit report and takes the median across Python versions. Do not mix runs or
partial artifacts. If a test only fails in a shard, fix its isolation rather than
changing order, excluding it, or extending its deadline.

`desktop.yml` runs these independent jobs with the existing path filters:

| Job | Coverage |
| --- | --- |
| `frontend` | Regenerate browser assets, check for drift, run Node unit tests once |
| `browser-ui (1/3)` through `(3/3)` | Entire Playwright suite, on committed generated assets, with Chromium cached by Playwright version; UTC and DejaVu Sans |
| `central-ui` | Nineteen named central-service UI script steps sharing the original ten-minute test budget, plus setup; fixture evidence retained |
| `build` (all four existing OS runners) | Native frontend build, managed runtime/upgrade/setup, Rust/GLib, native files, packaging and installed smoke tests |
| `windows-endpoint-oracle` | Existing read-only endpoint and Fleet fixtures |

Browser jobs need Node and Chromium; central UI also needs the locked Python dev
environment. Neither needs Rust or a packaged runtime. Native validation retains
those dependencies and uses the committed browser assets in its managed core.
Browser failures retain the HTML report and test results for seven days. Signed
workflows and Pages are unchanged. Superseded PR runs are cancelled per workflow;
main pushes have unique concurrency groups so pending and running main checks survive.

## Working after the October 10, 2026 history rewrite

Start new contributions from the cleaned public `main`, preferably in a fresh clone.
Rebase or cherry-pick only reviewed work onto that history; do not merge old ancestry
back into the repository. Review the resulting diff for private material before pushing.
Historical source references in documentation use retained rewritten commits. Historical
CI results still describe their original runs; remapped references do not claim reruns
or validate new packages. Use current `main` checks and matching desktop artifacts.
