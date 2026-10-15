# Repository guidance

The adopted [PM v1.2 plan](docs/product/pm-v1.2-plan.md) is the current product
direction. Professional project management is its core: requirements, scope,
decisions, delivery evidence and maintenance across the project lifecycle.
Earlier UI/engine decisions remain foundations; do not reduce PM to session
summaries or treat the first implementation increment as the full plan.
Read [PM implementation status](docs/product/pm-v1.2-status.md) for remaining work.

The [v1.3 closed-loop plan](docs/product/v1.3-closed-loop-plan.md) revises that
direction for direct work in the Dashboard, cross-CLI session results and one API/MCP
path for people and agents. Unrevised v1.2 lifecycle, retention and safety requirements
still apply. Start from its [agent handoff](docs/product/v1.3-agent-handoff.md):
E00 (baseline, reuse map and a clickable flow built from the shared components) and the
owner's R0 confirmation of that interaction come before E01–E11 implementation.
Report against E00–E11, R0–R5 and A01–A40; they do not replace open v1.2 items.

Read [the shared product decisions](docs/product/realignment-v2.md),
[implementation status](docs/product/implementation-status.md), and the applicable
`docs/design/` contract before changing behavior. The owner clarified that the two
plans describe one product with revised UI direction, not separate feature releases.
Use the October 8 Tauri plan for the shared frontend/native direction and its explicit
architecture decisions; preserve the shared feature backlog. Historical handoffs
describe their own commits, not current requirements.

The owner's later clarification and original conversation are summarized in
`docs/product/realignment-v2.md`: work remains on the selected BAT host/workspace;
code synchronization between hosts uses the explicitly bound GitHub repository and
published commits. Direct transport of unpublished Git objects/workspaces is not a
delivery requirement. Preserve ordinary artifact upload/download and same-host
read-only checkpoint continuation. Do not revive excluded work from the older plan.

- Keep Python Connector / Task Service as the central authority. Tauri is the desktop
  client; native code owns local windows, credentials and Fleet transport only.
- Manual BAT sessions and worktrees stay read-only. Unknown resources require existing
  creation evidence before managed recovery. Continue from a fixed checkpoint in new
  managed resources; never modify a person's checkout or infer ownership from its path.
- Reuse registered actions, scopes, resource policy, coordinator gates and durable
  operations. Preserve final-frame checks and sent-effect reconciliation when integrating
  branches. Never repair an uncertain result by resending a start or clearing all locks.
- Project Hub code/rules/UI may be selectively reused with attribution. Do not implement
  Hub snapshot import, source mappings or an importer dependency. B05 now means preservation
  of Connector's own IDs and history during schema upgrades.
- `desktop/src` becomes the shared browser/desktop frontend. Once generated assets are
  introduced, edit their source and regenerate; do not maintain a second Dashboard.
- Inspect current branch/worker ownership before edits. Use isolated worktrees, leave
  unrelated work intact, and assign one integrator for shared core files. Agent delegation
  is allowed when the user requests it; this file does not require delegation.
- Keep real hostnames, credentials, fingerprints and private deployment configuration out
  of commits. Tests use mock BAT/providers and temporary repositories, never live writes.
- Run relevant checks, then required full checks sequentially: `uv run ruff check .`,
  `uv run pytest -q`, and the Python 3.10 suite for backend changes. Do not globally
  monkeypatch stdlib classes. Use `settle_operations` when available for durable results.
- New schema DDL is idempotent and does not allocate `user_version`. Data steps: 1 existing
  event copy; 2 observation history; 3 deployment history. Coordinate any new data step.
- Preserve English and zh-TW user flows, drafts, stable IDs and operation keys. Report
  branch, merged, installed and live-tested status separately. CI is not native or live
  acceptance evidence.

`CONTRIBUTING.md` still applies to protocol changes and secret handling. Existing product
actions use their established operation/scopes authorization; do not add redundant chat
confirmation to an already authorized UI action.

## Local test temporary data

- Never put pytest basetemps, fixtures, SQLite/Git scratch or other test data in
  `/dev/shm` or any tmpfs. Leftover RAM-backed fixtures contributed to a host-wide OOM.
- Run local pytest through `pytest-disktmp uv run pytest -q ...` (also supports
  `uv run --python 3.10 pytest ...`). The wrapper creates a unique disk basetemp under
  `~/agent-work/tmp/pytest/` and removes it on exit.
- If a basetemp must be supplied directly, use a unique directory under
  `~/agent-work/tmp/pytest/` and remove that directory when the run finishes.
- Stop and remove task-owned test databases and containers after the task; do not leave
  them running or remove another task's resources.
