# Canonical agent skill bundles

Edit [`skills/bat-agent-connector/SKILL.md`](../skills/bat-agent-connector/SKILL.md).
It remains the generic skill and the wheel's `bat_agent_connector/skill/SKILL.md`.
Hermes and Grokbot receive self-contained generated copies of that workflow, with
only a small transport/configuration introduction. No runtime include support is
required. Do not edit generated adapters or overlay an older fleet workflow.

```sh
python3 scripts/generate_agent_skills.py
python3 scripts/generate_agent_skills.py --check
```

Generation is deterministic and needs only Python 3.10+. The check changes no files
and fails on a missing adapter, content drift, or a canonical package/API/contract
version mismatch. CI runs it. Metadata records the canonical path, its exact-byte
SHA-256, workflow revision, package version, API/contract versions and generator
format. Increment `metadata.workflow_version` when changing workflow behavior;
regenerate after integrating backend workers' canonical updates. Tool discovery
and caller capabilities remain the authority for actual availability.

Keep the historical top-level `version` for installer compatibility; it must match
`pyproject.toml` and `__version__`. This is a legacy extension to skill-creator's
current frontmatter schema, which otherwise accepts these files. The workflow
revision is bundle metadata, not an invented server `skill_version` API field.

## Installation and diagnosis

Both MCP entry points, `bat` and task-local `bat-task`, initialize with the loaded
product version and expose the read-only `batc://identity` resource. `mcp.build`
is the process's startup build snapshot (version, commit and source), with
`mcp.started_at` recording identity initialization during startup; unknown provenance
stays unknown. `central` separately reports an authorized capabilities read's build,
source and observation time, or an unavailable/unknown reason. A task capability
alone does not grant capabilities access. These facts do not prove an on-disk
update is running, and do not restart either process.

Use the generic bundle, [`Hermes`](../skills/hermes/bat-agent-connector/SKILL.md),
or [`Grokbot`](../skills/grokbot/bat-agent-connector/SKILL.md) from the same verified
Connector checkout/release. Place its `bat-agent-connector` directory in the
runtime's configured skill location. Grokbot-specific paths and commands are not
defined here. Register the executable from that same installation through the
runtime's standard MCP client settings; for clients using `mcpServers`:

```json
{"mcpServers":{"bat":{"command":"bat-agent-connector-mcp","args":["--principal-only"]}}}
```

`--principal-only` exposes central reads, durable operations and task adapters.
Every call requires `BATC_API_TOKEN`; missing credentials never fall back to the
local admin file or a task capability. The daemon checks the caller's action
scopes and its own host configuration. Direct Fleet tools such as `session_send`,
legacy permission tools and cleanup are omitted even when the MCP host configuration enables
writes. Use advertised central actions through `operation_submit`; `session_start`, relay,
fan-out and standalone failover have central principal adapters. `workspaces_list`,
`session_read` and `session_wait` also use the central owner. Task status/result/events and
session transcript/wait reads require `observe` and carry the same token. These two session
reads remain available with `--read-only`; CLI read/wait likewise require `BATC_API_TOKEN`
and never fall back to local admin or direct Fleet. External verification testimony has a central
`session_record_verification` adapter; it cannot create trusted task verification. Connector-only
labels use the advertised `session.labels.set` action and an explicit metadata version. Both keep
the original key after reply loss. `worktree_merge` requires integrate scope and the configured
verifier SSH host mapping; it fixes both managed carriers and retains their reservations after
an unknown ACK. Its central adapter has no direct BAT fallback. Unavailable legacy behavior remains unavailable.

For an observation-only installation, use
`["--principal-only", "--read-only"]`: write tools are omitted entirely, so adding
token scopes cannot enable them. The default profile without `--principal-only`
retains operator compatibility, including direct Fleet tools and local-admin read
fallback. It is not a scope-isolated agent endpoint. A shared HTTP MCP endpoint
must already run this profile with the intended agent's server-side token; clients
cannot select another principal by passing tool arguments.

The operator supplies this agent's `BATC_API_TOKEN` through the MCP server's
environment/secret configuration, and `BATC_TASK_URL` when the daemon endpoint
differs. See [MCP setup](getting-started.md#cli-and-agent-access) for stdio/HTTP
configuration. Changes to write configuration follow the existing host tiers and
principal scopes; changing the agent name grants nothing. Standard MCP clients
need neither Hermes nor an open Tauri window.

Distinguish checkout SHA (`git rev-parse HEAD`), installed executable version
(`batc --version`), MCP initialization `serverInfo`, daemon
`GET /api/v1/version`, caller `capabilities_get()`, and bundle metadata/digest.
The dashboard bootstrap also exposes server/principal identity; the version endpoint alone does not.
There is no skill-version endpoint. A matching
release number does not prove the checkout is installed; confirm the executable
path and deployment pin independently. This change does not update a fleet
installer pin or certify a live installation.

## Behavioral forward checks

For each generic/Hermes/Grokbot bundle, start from the fixture information below
and choose the next action using only its instructions and discovered tool schema.
These are skill review scenarios, not claims of model or live-host execution.

| Situation | Expected next action | Refused shortcut |
| --- | --- | --- |
| Reconnect with a saved task/work-item/operation and a stale, absent session tab | Verify endpoint/actor/capabilities, read the saved records and original operation; preserve IDs | New task/session, treating absence as death, clearing claims |
| Write reply lost; known operation is `uncertain` | `operation_get(operation_id)`; retain key/request/refs while daemon reconciles | New key or direct BAT resend |
| Managed worktree merge has an unknown ACK | Read the original operation and preserve both carrier reservations; report uncertainty | Resending merge/rehydrate, clearing reservations or assuming a changed HEAD proves success |
| Reply lost before receiving an operation ID | Inspect `operations_list`; recover with identical request/key/actor if needed | Changed preconditions under old key or a fresh key |
| Start the published version on another host | Choose an explicit repository/host/workspace binding, preview the branch head, retain its SHA/repository ID/binding digest and use one saved start key | Implicit takeover, pulling a human checkout, direct unpublished Git transport |
| Continue a manual session that has uncommitted edits | Read checkpoint preview/runs; have the person commit required edits; continue the fixed checkpoint into new managed work within existing authorization | Writing/interrupting/cleaning the human source or treating its path as ownership |
| Cleanup capability says legacy apply unavailable, despite old cron permission | Keep legacy evaluation read-only; use only advertised reviewed cleanup within its exact scope | Hourly apply sweep, CLI/raw/shell bypass |
| Capabilities return unexpected `local-admin` or lack the requested action | Resolve the caller identity/availability before writes | Borrowing the local admin token or another agent's credentials |

On the R09 source change, all three bundles were manually forward-checked against
these cases. Automated checks prove byte-identical canonical bodies, provenance,
version alignment, drift detection and the referenced discovery/recovery tool
schemas. They do not prove an LLM will follow the workflow or constitute R10 live
acceptance.
