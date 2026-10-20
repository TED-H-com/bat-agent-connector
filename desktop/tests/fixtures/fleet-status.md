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
