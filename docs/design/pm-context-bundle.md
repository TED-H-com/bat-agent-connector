# Fixed accepted-document context

The existing `pm.run.analyze` and `pm.run.bat.analyze` actions may explicitly
select a saved accepted plan with `params.context_bundle_ref`. The target,
original operation key, thread-version precondition, requester scopes and
workspace writer remain the existing analysis contract. No new runtime, model
tool, automatic decision or file-write authority is introduced.

The optional selector contains exactly `schema='pm_context_bundle_v1'`,
`revision_id`, `document_digest`, `adoption_operation_id` and
`git_receipt_digest`. The service derives these values from one actual adopted
revision and its original verified Git creation receipt. A draft, caller-supplied
file text, mutable path, latest-file lookup or copied receipt cannot select a
different document. Preview uses the current accepted revision; admission fixes
that revision, decision and original source identities. Later history reads do
not substitute a new accepted document.

`pm_context_bundles.preview(ops, principal, workspace_id, thread_id,
max_chars=32000)` is read-only. It returns the exact selector, adopted scope and
decision references, original Git commit/tree/binding, fixed file manifest and
coverage. `observe` and current workspace membership are required, along with
current access to all original plan/adoption/carrier sources. The analysis action
continues to require `observe`, `manage`, `start` and a current owner or
contributor; the model's output never provides that authority.

For a new selected analysis, the service builds context schema 2 from the current
bounded discussion and the exact `pm_plans.revision_files` bytes. It preserves
original requirement/acceptance identities and source statuses, plus the adopted
decision and Git evidence. The entire canonical UTF-8 context, including its
purpose, fits within 256 KiB. Each accepted file is included in full or recorded
as omitted with its original digest and byte length. It is never silently
truncated or replaced with a summary. Omitted acceptance metadata makes accepted
requirements coverage explicitly incomplete; context completeness is not
adoption or execution permission even when every file fits.
Preview reserves the maximum JSON size of a valid 4,000-character purpose, so
adding that purpose cannot silently change which complete accepted files fit.

The existing run context/digest is the immutable bundle snapshot. No migration
number, mutable global context pointer or additional writer is allocated.
Omitting `context_bundle_ref` uses schema 1 unchanged, including its canonical
bytes and hashes. Follow-up and conversation-proposal destinations keep their
own exact admission checks; this feature does not broaden the internal callback
allowlist to caller-provided contexts.

Every fresh native/BAT frame rechecks original bundle visibility through the
run's source gate. A withdrawn repository binding, changed source identity or
lost current access blocks new effects. Saved replies, run reads and operation
history retain those same original source gates. Unknown process effects keep
their original run, key and writer, and a restarted service reads the stored
bundle instead of rebuilding it from current documents. A successor uses a new
own session and explicitly selected revision under the existing writer rules.

This slice supports accepted plan documents in bounded analysis. It does not
claim native coding, arbitrary PM directory access, a durable workspace adapter
selection or automatic cross-tool handoff. Those use separate contracts.
