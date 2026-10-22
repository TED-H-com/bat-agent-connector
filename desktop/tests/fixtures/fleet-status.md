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
synthetic inventory and injected existing probes, then `Entry::observed`. Four hosts
choose direct, node-4 chooses Cloudflare, and Connector chooses configured SSH.
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
