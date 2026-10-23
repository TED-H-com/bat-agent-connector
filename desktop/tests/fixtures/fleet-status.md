# Fleet status contract fixtures

`fleet-status.json` is a de-identified native status response before IPC
sanitization. `supervisor_status::contract_tests::observed_rows_match_native_status_fixture`
constructs its readiness rows with `Entry::observed`, the same producer used by
the supervisor and read by `fleet_native::Context::status`.

`fleet-status.golden.json` is the expected `sanitize_result(..., "status")`
output. Every Fleet JS fixture reads this file, rather than the raw response.
`fleet::tests::sanitized_status_matches_shared_frontend_golden` compares the
Rust sanitizer output to this exact file. It runs in native desktop CI; a local
Fleet core test alone does not execute or verify the Tauri sanitizer.

In particular, IPC replaces configuration `issues` with `issue_count`, drops
null layer values, and preserves Connector's explicit false `tls`, `bat` and
`workspace` flags alongside `tunnel` and `auth`.

Browser failure scenarios retain the producer's per-row `selected`, `level`,
`blocking`, `code` and layer semantics. Monitor shutdown uses unavailable or
stale observations; selection revisions are null or 64-digit hex digests.
Unknown codes and missing layer fields in unit tests exercise defensive reader
behavior and sparse legacy status; they are not native readiness evidence.

Route observations are generated from real `SelectionRequest` choices using a
synthetic inventory and injected existing probes, then `Entry::observed`. Three hosts
choose direct, node-4 chooses Cloudflare, node-5 prefers LAN over its eligible direct
route, and Connector chooses configured SSH.
Each choice is five minutes older than the snapshot. The same confirmation helper
used by supervisor ownership reconciliation dates its last verified use at the
snapshot time; this keeps the long-running route current. No latency is measured.
The golden test passes those typed rows through
`fleet::native_readiness`, the exact projection called by `fleet_native::Context::status`,
then `sanitize_result`. Platform discovery and ownership use synthetic fixtures;
this does not claim a live Windows Context read.

Regenerate the raw fixture with the ignored producer test, then consume its output:

```sh
cargo test --locked --manifest-path desktop/fleet-core/Cargo.toml generate_route_status_fixture -- --ignored --nocapture > raw.log
node desktop/scripts/regenerate-fleet-status.mjs raw raw.log
```

On a native build host, regenerate the golden from the real sanitizer:

```sh
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml generate_route_status_golden -- --ignored --nocapture > golden.log
node desktop/scripts/regenerate-fleet-status.mjs golden golden.log
```

The log writer only preserves JSON key order; every value comes from Rust output.
Never edit expected values by hand. The regular core and native golden tests check
both boundaries, including absence of alias/address/payload fields. Browser and Node
route tests consume the same golden with literal English and zh-TW expectations.

`fleet-requirement.json` comes from
`supervisor_status::contract_tests::generate_requirement_fixture`: real peer
classification and route selection feed `Entry::observed` for missing, unreadable,
idle and direct-with-failed-TCP evidence, plus exhausted configured routes. The
other hosts retain their real successful producer rows. `fleet-stop.json` comes
from `supervisor::generate_unresolved_stop_fixture`, using the real supervisor's
unknown-stop fixture, disk publication, `Snapshot::parse` after 60 seconds and
separately after deselection, then another parse. It checks unchanged original
owner/stop receipts and monitor record, with no additional launches or stops.
Only nondeterministic binding metadata is de-identified.

The requirement fixture's `aged` case writes those real producer rows to a status
file and calls `Snapshot::parse` at publication time plus 60,001 ms. Its `now` is
the reader clock used by native projection; the other cases use publication time.
Expiry replaces the route failure with `PROBE_STALE` and withdraws its explanation.

The matching `.golden.json` files pass those rows through `native_readiness` and
`sanitize_result` in `fleet.rs`; both Node and Playwright consume that output.
Regenerate with the ignored core tests above and `regenerate-fleet-status.mjs`
using `requirement` / `stop`. On the native build host, run the ignored
`fleet::tests::requirement_generate_golden` / `stop_generate_golden`, then use
`requirement-golden` / `stop-golden`. Regular producer and sanitizer tests compare
all values to the checked-in files. These fixtures exercise real production code
with synthetic processes, peers, inventory and local files, not live BAT,
Tailscale, SSH or installed Windows process ownership.

`fleet-pending.json` comes from the real supervisor's
`generate_pending_requirement_fixture` test: unknown-peer, failed-TCP and exhausted
selections are harvested, subsequent ticks publish `PROBE_PENDING`, and the actual
status file is parsed both immediately and after 60,001 ms. It checks attempt
counts, no target launches or stops, and the healthy Connector row. Only binding
metadata is de-identified, as in the stop fixture. Native projection and sanitizer
produce `fleet-pending.golden.json`; Node and Playwright consume it in both locales.

```sh
cargo test --locked --manifest-path desktop/fleet-core/Cargo.toml --test supervisor generate_pending_requirement_fixture -- --ignored --nocapture > pending.log
node desktop/scripts/regenerate-fleet-status.mjs pending pending.log
# On the native build host (use the native service locally):
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml pending_generate_golden -- --ignored --nocapture > pending-golden.log
node desktop/scripts/regenerate-fleet-status.mjs pending-golden pending-golden.log
```
