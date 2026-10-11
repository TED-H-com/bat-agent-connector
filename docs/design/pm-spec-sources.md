# Fixed OpenSpec source reads (W36, first carrier)

This is the first read-only SpecArtifactAdapter slice of
[PM v1.2 §12.11–12.12 and W35–W38](../product/pm-v1.2-plan.md).
It supplies versioned repository documents for a later engineering work package.
It does not adopt a spec, produce an execution batch, reconcile tasks into issues,
or complete W36's CLI JSON/custom-schema integration.

## Entry points and authority

`pm_spec_sources.capabilities(ops, principal, workspace_id)` is a cheap eligibility
read. It requires current observe scope and workspace membership. `eligible` means
an existing durable PM repository binding and configured transport are available;
`git_verified` remains false until an actual read. It never launches a CLI.

```python
await pm_spec_sources.read_sources(
    ops, principal, workspace_id,
    repository="example/project",
    commit_sha="<full original 40-hex source commit>",
    paths=["openspec/changes/account-recovery/tasks.md"],  # optional exact selection
)
```

The caller supplies logical repository identity, commit and optional standard
artifact paths. A caller cannot supply a clone path, executable, shell fragment,
Git ref, remote URL, arbitrary command or alternative source root. Membership,
observe scope, repository configuration and the exact BAT workspace folder are
checked before the asynchronous read and again before returning document bytes.
Removing membership during the read discards its result.

The first path uses `pm_runs.workspace_worktree`'s original durable binding and
the existing SSH Git runner. It reads only the original already-pinned code source
SHA in that PM-owned clone. Workspace repository labels are not filesystem
authorization. An absent binding, unprepared clone, missing original pin, changed
repository identity or any other commit returns an explicit unavailable error.
It does not automatically bootstrap, fetch a newly published head, or follow a
moving branch. Existing PM revision commits are not yet selectable source commits.

The host reuses the original clone's creation markers and inode locks. It rejects
unproven metadata links, alternates, replace refs, dangerous local Git settings and
wrong repository markers. It invokes only fixed local Git read arguments with
hooks, optional locks, fsmonitor, global config, maintenance and lazy/network fetch
disabled. It never invokes an installed OpenSpec executable or a repository script.

Document bytes come from commit/tree/blob objects, not the worktree. Ordinary
uncommitted human or PM edits neither alter the returned documents nor get cleaned,
staged or reset. `working_tree_included=false` and `worktree_clean=not_checked`
make that distinction explicit. Reading needs no PM writer, session or new journal
record. Original Git refs and index bytes remain unchanged.

## Supported layout and format identification

The named reference for this adapter is OpenSpec source
[`9111a7654d7800391459431fff4eaf66e33a3d2e`](https://github.com/Fission-AI/OpenSpec/tree/9111a7654d7800391459431fff4eaf66e33a3d2e),
whose package declares 1.14.1. Its
[spec-driven schema](https://github.com/Fission-AI/OpenSpec/blob/9111a7654d7800391459431fff4eaf66e33a3d2e/schemas/spec-driven/schema.yaml)
provides the standard artifact layout. This is a compatibility reference, not a
claim that the user's installed CLI or repository uses that release.

Supported repository paths are:

- `openspec/config.yaml`: project metadata.
- `openspec/specs/<capability path>/spec.md`: main specification documents.
- `openspec/changes/<change>/proposal.md`, `design.md`, `tasks.md`, `.openspec.yaml`.
- `openspec/changes/<change>/specs/<capability path>/spec.md`: change delta documents.
- The corresponding artifacts under `openspec/changes/archive/<archive folder>/`.

Capability paths can contain up to eight conservative name components. Existing
paths stay in place; nothing is copied into `docs/pm/specs`. Archive folder names
are preserved literally, without guessing the original change ID from a date.
Other layouts and custom artifacts are counted as ignored, not silently mapped.
Symlinks, traversal, special modes, non-UTF-8 and binary documents are not followed
or decoded permissively. Missing selected files receive `missing_at_commit`.

Metadata retains its original UTF-8 text. A small recognizer observes a single
top-level scalar `schema` declaration, including the name `spec-driven`; it is not
a YAML loader or effective schema resolver. Complex YAML, duplicate declarations,
custom names, missing metadata and runtime-installed overrides stay unknown.
`cli_version=null`, `validation=not_run` and `custom_artifacts=not_resolved` remain
explicit even when a standard name is observed.

## Result and engineering-package references

The result contains the exact workspace/server, repository numeric ID, original
binding digest, full commit and tree IDs, ordered documents, coverage, schema
limitations, unknowns and a deterministic `document_digest`. No wall-clock field
changes an otherwise identical fixed read.

Every document contains:

- `file_id`: stable logical file identity across revisions, scoped to the original
  workspace/server/repository/binding/path; no cross-workspace merging.
- `source_id`: identity of this file at the exact commit and blob.
- `path`, `commit_sha`, `blob_sha`, exact byte size and SHA-256 digest.
- `role`, `location` (main/change/archive), change key and capability path.
- Exact UTF-8 content, or `status=unavailable` with a reason and no substitute text.
- `source_status=repository_document`, accepted scope unknown, and no inferred
  implementation verification, merge or deployment result.

A W35 package can freeze these references and its own requirement/acceptance
mapping. `file_id` is not a permanent requirement or task ID. Markdown headings
and task lines are revision-local observations. Line positions must not create
new issues after a reorder; cross-revision reconciliation remains W37 work.

Checkbox observations preserve their marker, original line and textual claim.
An `x` marker means only `reported_checked`; unknown markers remain unchecked.
Fenced examples are explicitly labelled. This conservative lexical subset is not
advertised as complete parity with OpenSpec's
[task parser](https://github.com/Fission-AI/OpenSpec/blob/9111a7654d7800391459431fff4eaf66e33a3d2e/src/utils/task-progress.ts).
Every row keeps `verification=unknown` and `counts_as_done=false`. Archive location
has the same non-delivery semantics. No `apply`, `sync`, `verify` or `archive`
command runs; words in a document cannot authorize operations.

## Bounds and unavailable results

Each document is limited to 128,000 bytes, selected content to 384,000 bytes, and
a response to 100 files and 1,800,000 JSON wire bytes, including escaping. Size is
read before the Git blob. Individual oversized/nontext files return unavailable
records; aggregate content limits preserve the unread file identity with
`selection_byte_budget`, allowing a narrower retry. The response is never silently
truncated. Tree depth/count/size and structural observation counts are bounded.
The Git object read uses per-command and transport timeouts.

`catalog_complete` refers only to the requested standard path catalog, not all
possible custom OpenSpec artifacts. `content_complete` is false when any selected
document could not be read. Format recognition and schema validation are separate.
Unknown host results, excessive catalogs or response size return explicit errors;
raw Git stderr, private host paths and credentials are not exposed.

Fixtures use temporary disk Git repositories and mock BAT/GitHub. They cover
source identity and authority, dirty worktrees, untrusted paths and metadata,
document bounds, archive/task semantics and asynchronous revocation. They are not
installed or live OpenSpec/BAT acceptance evidence.

## Transport and shared frontend

HTTP exposes observe-only reads at:

- `GET /api/v1/pm/workspaces/{workspace_id}/spec-sources/capabilities` (no query).
- `GET /api/v1/pm/workspaces/{workspace_id}/spec-sources?repository=example/project&commit_sha=<SHA>`.
  Optional `paths` is one comma-separated selection of distinct exact artifact paths.

Unknown or repeated query fields are rejected. Repository and commit are required;
they are never defaulted from a moving branch. RPC and read-only MCP expose
`pm_spec_source_capabilities_get(workspace_id)` and
`pm_spec_sources_get(workspace_id, repository, commit_sha, paths=None)`. They share
the asynchronous `pm_spec_reads.read` dispatcher with HTTP. Blocking Git work is
not inserted into the synchronous PM read dispatcher. MCP uses the caller's token;
there is no local-admin fallback for these reads.

`features.pm_workspaces.spec_sources=true` advertises the reader, while the
workspace capability determines source eligibility. The native bridge allows only
the exact GET routes and bounded selectors, including rejecting traversal,
duplicate paths/query fields and unsupported artifacts. It retains its existing
4,096-character request-route limit. The shared component checks this limit before
submitting an optional path selection.

`specSources({h,t,api,alive,workspaceId,errorBox})` returns `{element,refresh}`.
The parent mounts it under that feature gate. `refresh` checks cheap eligibility
and current membership; it never automatically reads Git. Only the explicit read
button requests document bytes. An unchanged eligible binding preserves expanded
source details and the path draft. Source changes, lost access and failed identity
checks clear old source facts; late responses cannot repopulate them. Refresh hides
existing documents until its current capability check succeeds. Source text is
rendered as text nodes, including markup and commands appearing inside documents.
The component has no operation keys or write actions because both calls are reads.
