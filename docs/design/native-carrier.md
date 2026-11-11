# Managed native carrier preparation

`native_carrier` is an internal preparation seam, with no action, route,
API/MCP surface, launcher, capability or credential policy. A future caller
must supply the configured host/repository binding and current authority guard.
The existing SSH Git runner remains required, including on the same bound host.
Local Git transport remains unavailable; no native model call is introduced.

`prepare_published` and `prepare_pm` hold the extracted BAT and PM preparation
steps. Their original guards, step names, requests, reconciliation, errors and
PM draft/revision behavior remain unchanged. The shared locked host executor
still owns clone validation, fixed commit pinning and worktree creation.
Its optional internal observer adds native physical receipts under those locks.

`prepare` requires an internal `FixedSource` with a full commit SHA. Published
sources retain the existing fixed-ref fetch check. Checkpoints use read-only
Git inspection and copy committed objects from a directory on the selected host;
no source index, branch, stash or working file is written. Checkpoint sources
with alternates, replacement refs, partial-clone or promisor configuration are
refused, including configuration includes that could hide those settings.

Each creation operation and Attempt determines a separate clone, branch and
worktree. Fetch copies objects; there is no shared object-cache mode. Clone
validation refuses alternates, replacement refs, lazy-fetch configuration,
symlinks and hardlinks in Git metadata. The worktree's Git directory stays
inside its own clone, and additional worktrees are refused before handoff.

`CarrierReceipt` is immutable and minted only after preparation and readback.
It cannot be constructed from a request dictionary. It records the creation
operation, Attempt, host, repository, fixed base, branch, clone/worktree/Git-dir
paths and device/inode identities, plus the artifact manifest digest.
Only the empty artifact manifest is supported by this preparation slice.
`reference()` returns a detached canonical projection and creation digest for
`native_journal.LaunchIntent.carrier_receipt`; it grants no launch authority.
The journal's existing schema and evidence gate remain unchanged.

Before use, `readback` compares original physical identities, pinned base and
branch through the shared host checks. A matching path or marker is insufficient:
an existing clone or worktree needs its original creation receipt. No cleanup
or adoption is attempted. Lost creation responses remain uncertain, and the
native step reconciler never requests another effect. Recovery is future work.

New refusal codes are `NATIVE_CARRIER_CREATION_UNPROVEN`,
`NATIVE_CARRIER_CHANGED` and `NATIVE_SOURCE_UNPROVEN`. Existing
`GIT_RUNNER_UNAVAILABLE` and `REPOSITORY_*` refusals pass through.
Tests use real disk Git with the mock SSH transport, never a model or live host.

原生工作只使用選定主機上的獨立 managed clone、worktree 與 branch，來源固定為完整
commit SHA。人工 checkout 永遠唯讀；沒有原建立收據的既存路徑不得接管或清理。
交給 launcher 前必須查回實體身分、固定來源與 branch；失聯不授權重送。
此 seam 沒有新增路由、權限、憑證、模型呼叫或產品可用性宣告。
