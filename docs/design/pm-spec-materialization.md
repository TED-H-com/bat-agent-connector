# Owned specification materialization (W37)

`pm.spec.materialize` freezes one exact engineering package in the original
workspace-owned PM carrier. It uses the existing PM writer reservation, sanitized
Git runner, inode locks, append-only revision manifest and original-operation
reconciliation. It never edits the source repository's specifications, manual
checkouts, or another task's index. It does not start implementation or adopt scope.

The target is `{workspace_id, package_id}`, params are empty, and preconditions
are `{expected_package_digest, expected_scope_revision}`. Observe, manage and
start scopes plus current writable workspace membership are required. The selected
package must still describe the adopted scope and current work association. Every
new effect retains the original lifecycle epoch and source barriers. Receipt-only
reconciliation remains possible when membership or investment authority is withdrawn.

## Immutable adapter layout

The first package in a revision chain defines `pm-<root package hex>`. Each fixed
package has an append-only directory:

`openspec/changes/pm-<root>/revisions/pmwp_<package>/{package.json,proposal.md,requirements.md,design.md,tasks.md,manifest.json}`

`docs/pm/specs/pm-<root>/revisions/pmwp_<package>.json` contains only the fixed
paths, digests and artifact reference. It is an index, not a second editable spec.
These are Connector-owned adapter artifacts; revision nesting is not claimed to
be an OpenSpec CLI-compatible change directory. The pinned upstream
[spec-driven schema](https://github.com/Fission-AI/OpenSpec/blob/9111a7654d7800391459431fff4eaf66e33a3d2e/schemas/spec-driven/schema.yaml)
and [task progress reader](https://github.com/Fission-AI/OpenSpec/blob/9111a7654d7800391459431fff4eaf66e33a3d2e/src/utils/task-progress.ts)
inform the readable artifact names and checkbox syntax. No CLI is invoked, no
upstream raw file is rewritten, and checkboxes never prove implementation quality.

Task identities derive from workspace, root package and the explicitly authored
task key. Reordering or editing prose preserves identity; renaming a key creates a
different identity. All package revisions participate, including revisions not yet
materialized. Removed keys are tombstoned with their history, and reintroducing the
same explicit key preserves its original ID. No prose/line matching, issue creation,
or automatic task completion occurs. Whole-package ancestral access is retained.

## Artifact and Git recovery

The full canonical package and task/provenance manifest first become one ordinary
immutable JSON artifact through the existing reserve/receive/verify/publish steps.
Its source root is the original materialization operation from admission onward.
That artifact is a specification snapshot, not evidence of a completed Git write.
The same bytes are `package.json` in the owned Git revision. Only after the original
Git transaction is verified does a receipt-only journal transaction record the
combined materialization and release the PM writer. An interrupted Git result keeps
the writer until original readback proves it; replay never creates a second commit
or artifact. A failed pre-Git operation may leave a valid ordinary snapshot artifact,
but no completed materialization is claimed. Unknown files, symlinks, changed index,
different parent, and any existing destination are refused without overwriting.

Plan and spec commits share one append-only chain. The next parent is the unique
tip of recorded original commit/parent pairs, not wall clock or an independently
maintained spec branch. Historical plan adoption proofs remain historical and cannot
move the next writer's parent backwards.

The complete generated file set is bounded to 512,000 UTF-8 bytes and the existing
compressed host command to 120,000 bytes. Exceeding either bound refuses the whole
materialization; no requirement or source is truncated. Artifact store quotas also
apply. These bounds support packages larger than the inline first-prompt limit,
without promising arbitrary-size specification transfer.

## Fixed engineering handoff

`repository.continue` may select
`pm_spec_ref={workspace_id,materialization_id,document_digest}` together with the
existing exact package/code-basis selection. Its existing `artifacts` list must
explicitly include the materialization's `{artifact_id,revision,digest}`. Admission
checks the canonical bytes, original package, completed Git receipt and source
access. Trusted admission retains every ancestral package operation ID, so earlier
execution/artifact roots remain attached to the new ordinary artifact. The original operation still stores the complete engineering contract;
only its first prompt uses a bounded instruction with the server-selected
`.batc-inputs/<artifact>-r<revision>/<safe filename>` and immutable digests.

The established ordinary artifact materialization and final-frame verification
rehash those bytes in the new managed worktree before any new BAT start/send. No
unpublished PM Git fetch, mutable path reference, second executor, or filesystem
shortcut is used. The original inline path remains available for small packages.
Materialization history, operations, events, artifacts and derived execution all
retain their original full source lineage; readers never receive a partial package
when some source has been withdrawn.
