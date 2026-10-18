# Durable workspace PM analysis choice

This W24 slice selects between the existing `codex-native-linux` and
`bat-pm-linux-v1` bounded discussion runtimes. It does not enable native coding,
AHP writes, direct model file writes or automatic execution. The existing PM
carrier, accepted revisions and context bundles remain the document authority.

`pm.workspace.adapter.select` is a local registered operation with target
`{workspace_id}`, params `{adapter,rationale}`, and preconditions
`{expected_selection_version,expected_writer_generation,expected_plan_version,
expected_accepted_revision_id,expected_continuity_digest}`. Current owners need
observe/manage/start. The existing workspace capabilities read supplies this exact
CAS and the current receipt; callers never enter internal identities manually.
Public writer status contains only `generation` and `occupied`; membership alone
does not reveal a run ID whose original discussion sources may be private.
The selected runtime can be unavailable for preflight: saving a choice does not
prove installation, authentication, model availability or runtime confinement.

Admission and final local publication compare the same choice revision, original
writer generation, accepted plan and owned carrier. An occupied writer slot always
refuses, including unknown, cancelled-but-unreconciled and old-epoch work. Selection
never stops a process, clears a slot or increments a writer generation. The existing
PM run's original completion/death or proven-unsent contract must release it first.
Concurrent choices use CAS; each successful choice has one immutable original
operation receipt. Replaying that receipt precedes later versions and readiness.
The choice may be saved while paused/closed, but does not reopen or launch work.

Ordinary discussion analysis and all registered structured analysis destinations
(plan proposals, Follow-up, continuation briefs and Overview assessments) keep their
exact legacy wire forms. Once a workspace
has a choice, a new request must explicitly target that adapter; there is no native
to BAT fallback. Admission freezes the choice revision. The original writer
transaction rechecks it before creating a run, so an earlier queued request cannot
start under a later choice. Existing saved runs/receipts are recovered first. An
unconfigured workspace retains legacy per-operation selection and wire behavior.
Every new run still obtains its own writer generation and frozen discussion/
accepted-document context through the existing registered action. A switch receipt
is not a claim that a successor turn completed or that it read mutable host files.

The receipt retains fixed accepted-document/Git references where present, original
carrier identity and original source operations. Original-source withdrawal hides
the dependent receipt, operation/events and derived runs; newer documents never
replace those original references. Legacy document hashes remain unchanged.

The shared UI keeps the choice in the existing analysis area, displays preflight
limits, and uses the ordinary durable form with original key recovery. Pending
selection and analysis drafts remain distinct; switching cannot rewrite a queued
analysis request or select another backend to recover an unknown result.

Acceptance includes owner/CAS conflicts, active/unknown writer retention, queued
old-choice refusal, restart and original-key replay, original-source withdrawal,
BAT/native fixed-context continuation with new generations, and EN/zh-TW Web/Tauri
draft/recovery behavior. Mock/runtime fixtures are separate from installed/live
adapter acceptance; this slice alone does not close the whole M5 milestone.
