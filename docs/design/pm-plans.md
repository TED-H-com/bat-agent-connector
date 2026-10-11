# Immutable plans and recoverable adoption

[Issue #91](https://github.com/teddashh/bat-agent-connector/issues/91) implements
the plan revision domain in `pm_plans.py`, following the adopted v1.2 sections
7.1 and 8.2. The existing workspace journal and OperationService remain the
authority. Additive idempotent tables do not allocate a migration version.

## Preparing a professional plan

`install(ops)` registers `pm.plan.draft` with `manage` scope. Owners and
contributors can prepare drafts; readers cannot. The target contains the exact
`workspace_id` and `thread_id`. Preconditions carry `expected_version` for the
discussion and `expected_plan_version` for its workspace plan head. Parameters
include `previous_revision_id` (explicitly null for the first draft), optional
`title`, `markdown`, `sections`, and `requirements`.

An explicit Planning discussion must already exist. Its originally selected
InputRecords are frozen into the revision, including their source, original
status, recorder, confirmer and applicable version. Later source confirmation
does not rewrite this historical snapshot. New unselected proposals cannot be
smuggled into a requirement through a source ID. Selecting or revising the
Planning source set remains a separate direction decision.

The structured document always covers governance, scope, schedule, finance,
stakeholders, resources, risk and quality. Each section maps selected input IDs
to attributed statements. Uncovered domains are unresolved; absence never means
zero cost, no risk or complete verification. Optional Markdown is preserved
exactly. When omitted, a readable draft is generated from the same statements
and their statuses.

Requirements have stable caller-selected keys, a selected source input, optional
acceptance source IDs, proposed priority and local dependency IDs. Requirement
and acceptance text comes from the frozen sources. The caller cannot replace
that text while claiming its existing confirmation. Unknown dependency IDs and
cycles are rejected. Drafting does not confirm sources, adopt a baseline,
create work items, dispatch, merge or deploy.

Drafts are append-only. Their parent document revision, source discussion
version, base accepted revision, author, operation ID and content digest are
recorded. Simultaneous drafts must satisfy the plan version comparison; a losing
writer cannot overwrite the winning revision. Multiple discussions share the
workspace plan head while retaining their own discussion histories.

## Fixed files and Git evidence

`revision_files(db, workspace_id, revision_id)` returns exact UTF-8 file text and
digests, plus both IDs. Revision IDs match `pmrev_[0-9a-f]{32}`. The only paths are:

- `docs/pm/revisions/<revision_id>/plan.md`
- `docs/pm/revisions/<revision_id>/plan.json`

The metadata JSON excludes Markdown, serializes with sorted keys, UTF-8,
`ensure_ascii=False` and separators `(',', ':')`, then appends a newline. Markdown
is the original exact text. Each file digest is SHA-256 of its UTF-8 bytes. The
document digest is SHA-256 of the sorted canonical JSON map of path to file
digest, without a trailing newline. Documents do not embed their own Git commit
or content digest. Existing repository specs and `docs/pm/plan.md` are preserved.

The managed Git integration owns commit creation, its original operation marker,
parent comparison, repository binding, fixed-path readback and recovery. The
plan module does not implement another Git engine. `latest_git_receipt` includes
verified Git commits whose journal adoption has not happened yet, so Git and
accepted-plan pointers are not conflated.

## Adopting the whole fixed revision

The module exposes internal helpers; it does **not** register a public adoption
action by itself. The integration layer registers `pm.plan.adopt` only with its
real managed Git implementation:

1. `prepare_adoption(ops, principal, target, params, preconditions)` checks current
   owner authority, the fixed revision, a nonempty decision rationale and
   `expected_accepted_revision_id`. The revision must have been prepared against
   that accepted baseline. The integration admission adds `git_binding_id` from
   the configured PM worktree, never from a caller's asserted receipt.
2. Managed Git commits and reads back the two exact files in the PM-owned
   repository, using the existing operation's identity and recovery guards.
3. `record_git_receipt(ctx, receipt)` durably records that occurred effect. Its
   receipt contains exactly `operation_id`, `workspace_id`, `revision_id`,
   `commit_sha`, `tree_sha`, `document_digest`, `file_digests`, `binding_id` and
   `status='verified'`. Full commit/tree IDs must have the same supported Git hash
   length. The binding ID must match the operation's admission binding.
4. `adopt_revision(ctx)` rechecks current owner membership and expected accepted
   revision, then updates the accepted pointer and decision atomically in SQLite.

A Git-only result leaves the former accepted plan valid. A response lost after
adoption is recovered from the original durable operation receipt before any
new effect. Membership loss can prevent an unapplied adoption while still
allowing an already occurred Git effect to be recorded. It does not falsify a
previously completed adoption. Competing adoptions cannot overwrite each other,
and a new operation cannot borrow another operation's Git receipt.

An owner may explicitly adopt a proposal, including transparent assumptions and
unknowns. That decision adopts the **whole fixed document**, including its
Markdown; it does not rewrite original source statuses or establish delivery
evidence. Execution remains separately authorized.

## Scope and document identity

`document_digest` covers the complete fixed files. `structured_scope_digest`
covers governance/scope/quality statements and requirement/acceptance structure.
Its coverage is intentionally partial: meaningful requirements may also appear
in the owner's prose.

If structured scope changes, adoption creates a new scope revision. If only
Markdown/title changes, the effect on scope is `unknown`, with `scope_changed`
set to null and a new conservative scope revision. It is never automatically
classified as progress backfill. If both prose and structured scope are equal,
the document revision can advance while retaining the prior scope revision.
No parser claims that a prose statement of completed verification is evidence.

`get_plan`, `accepted_plan`, and paginated `list_plans` require `observe` and
current workspace membership. They expose immutable content and adoption
evidence, distinguish current acceptance from historical adoption, and report
document validation failures without replacing the accepted pointer. Full
work-impact mapping, richer requirement confirmation and delivery projections
remain subsequent PM work.
