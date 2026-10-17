# Managed artifact review in the shared Dashboard

Extends [managed capture and acceptance](artifacts-managed.md). Central remains the
only source/execution/credential authority. This page reuses the Dashboard panels,
form controls, evidence lists and English/zh-TW copy; it adds no alternate store.

Session and task links open a fixed source context. The execution selector loads
supported succeeded central operations and current accepted send commands. Standalone
`session.start` appears only with positive start and initial-prompt result evidence;
prompt-free and uncertain starts are excluded. Central rechecks the original start,
reservation, send receipts and incarnation during preview. An
explicit older command/operation ID is a selector only; the signed preview must
prove it. The preview displays one relative file, source folder, HEAD, size and
digest. It is not a dirty-worktree snapshot or an authorship claim.

Capture freezes the complete public envelope and key before POST. Recovery validates
actor/action/key/target/params/preconditions and the original accepted ID. Once an
ID is known, reload, events and polling only GET that operation and exact artifact
revision. The ready ArtifactRef must match the saved digest, source fingerprint,
capture operation and central source proof before review becomes available.

Review is a separate `artifact.accept` operation with explicit bounded text and
`approve` scope. The page can also open an existing exact managed ArtifactRef from
the central catalog, allowing a different reviewer to accept it. Acceptance records
only review of that revision; it does not finish tasks, verify tests, merge or deploy.
It neither attaches the ref automatically nor changes the existing attachment draft.

Drafts and both intents use endpoint/server/principal/actor storage namespaces.
Bootstrap identity does not distinguish credentials with the same actor/scopes.
Therefore a generic authorization failure preserves the original capture token/key;
it cannot create a replacement capture or adopt another credential. Central's
stronger original-credential check remains authoritative. Known accepted IDs are
read back under existing observe policy. No credential material enters this module.

Only a matching terminal receipt or an explicit pre-admission refusal permits a new
intent. Uncertain outcomes retain their IDs and links to operation controls. Failed
event reads block acknowledgment; focused text and fixed previews survive refreshes.
While a preview, save, review, execution page, source reload, catalog page, or
file read is in progress, the pressed control stays in the tab order and looks
unavailable (`aria-disabled`); a second press does nothing. A control that
cannot be used stays `disabled` and out of the tab order for the whole time,
including while another read is in progress. The region that reports the work
is `aria-busy` only while one of its shown controls is in that in-flight
state. When the pressed control closes, focus moves to Check original
operations, Capture another file, Record another review, the execution list,
or the review text, and only if focus is still on the pressed control or has
fallen back to the page. A reply that arrives after the view changes does not
move focus.

The native bridge permits only the fixed managed-preview route and typed source,
relative file and exactly one central selector; no path transport, force or supplied
lineage is accepted. Existing manual capture remains separate.

Validation uses mock browser/native transports and an actual central temporary Git /
MockBat fixture for capture, lost replies, ready readback and acceptance. Screenshots
check 390/768/1440 widths. Builds and fixtures do not claim installed/live acceptance.


The exact revision panel exposes Preview and Download / native Save As to observe
callers, including approve-only reviewers. It checks fresh ready metadata against the
original ref before reading. Desktop uses the existing Rust file manager for byte,
digest, path and credential ownership. Only matching download receipts appear here;
stop/retry/reload keep the original native handle and never cancel an upload.
Browser uses the fixed same-origin authenticated content route, refuses redirects,
reads at most the declared 16 MiB bound and verifies SHA-256 before a download or
preview. Credentials never appear in URLs. Text is literal UTF-8 (256 KiB); static
PNG is bounded to 2 MiB and one megapixel and displayed only as decoded canvas pixels. No
HTML/SVG rendering, OS opening, source-path reads or acceptance POST is needed.

Artifact events refresh the catalog from its first cursor through the number of pages
already loaded, publishing the refreshed rows only after every page settles. Exact
revision keys remove duplicates across pages; this is not an atomic multi-page
snapshot or a completeness claim. Failed reads retain the old rows and withhold the
event checkpoint. A fixed revision route also matches events while its initial read
is unavailable, preserving the review draft during recovery.
