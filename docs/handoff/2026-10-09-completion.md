# Shared product continuation after PR #60

Historical source references below use their retained equivalents in the rewritten public history. Linked CI runs describe their original executions; remapping a source reference does not rerun CI or validate a new package. See the [current validation and download guidance](../product/implementation-status.md#目前續作).

PR #60 merged as `da8f9bb5729b3575de417db19b87dd7e2a510ca4`. Its reviewed head is
`adfe704e531e6c18083c8677ee1ee9ce87c5a4dc`. At the original merge, the two source trees were verified equal; this historical check is not a rebuilt-package receipt.
This adds organized sessions, native Windows credential enrollment, reviewed bulk approval backend,
managed artifact capture and exact-revision acceptance. It does not complete issue #59 or M1–M3.

- [Python CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37879651946): 3.10–3.13 each 2568 passed/33 skipped, Ruff, generated skills and secret scan passed.
- [Desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/runs/37879651932): 179 shared UI, 12 state, 33 Windows/32 Linux Rust; Windows NSIS/Linux deb unsigned packages and central fixtures passed.
- Local frozen backend `2efe362`: Python 3.13 2568/33 in 1017.37s, Python 3.10 2568/33 in 993.88s. UI-only descendants are covered by exact candidate CI, not retroactively by those local runs.

The owner wants the same product with a Tauri UI and Project Hub's orderly sessions. Work stays on the
selected BAT host/workspace. Cross-host code synchronization uses explicit GitHub bindings and published
commits; unpublished direct Git-pack transport remains excluded and its experiment stays unmerged.
Manual resources are readonly. Python remains the central authority. See the product decisions, not an
older handoff, for current scope.

Active continuation contracts: `bulk-approval.md` shared UI, new `session-start.md` and task coordinator
cleanup on their feature branches, native files in Rust and existing attachment rows. Integrate only
frozen reviewed commits, preserve all start/cleanup/confinement gates and run required full checks in
sequence. Current root integration branch is `integrate/completion-next`; original runtime checkout stays untouched.

Remaining product work includes B2 UI, other durable orchestration, explicit published-repository sync,
Rust Fleet parity/migration/bootstrap, signing/update and the same-candidate installed/live matrix.
Issue #53 remains the separate Linux GLib release gate. No live BAT/provider effects were performed.
