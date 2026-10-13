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

Captured-wave helpers such as joined maintenance's `advance` keep their existing
per-wave deadlines. Use `tests.operation_helpers.worker_details` before stopping
workers to preserve the suspended await chain, and `stop_workers` on failure or
caller cancellation to join all pending workers belonging to that service while
its journal is still open. Workers admitted during a wave also need this cleanup.

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

## Working after the October 10, 2026 history rewrite

Start new contributions from the cleaned public `main`, preferably in a fresh clone.
Rebase or cherry-pick only reviewed work onto that history; do not merge old ancestry
back into the repository. Review the resulting diff for private material before pushing.
Historical source references in documentation use retained rewritten commits. Historical
CI results still describe their original runs; remapped references do not claim reruns
or validate new packages. Use current `main` checks and matching desktop artifacts.
