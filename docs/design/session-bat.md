# Explicit session → BAT profile handoff

A session detail page keeps its complete title and session ID available to copy in
both browser and desktop. The native desktop additionally offers **Open in BAT**:
the user chooses one configured logical BAT profile, reviews it, and explicitly
launches it. Labels, host names and workspace paths never infer that choice. This
opens a profile; no verified session deep-link contract exists, so the user then
searches in BAT with the copied title or full ID. Manual sessions remain read-only.

`fleet_control.preview_profile {profile_id}` captures the original installation,
configuration, complete private selection snapshot, local login, executable and
live profile-index bytes. Only trusted inventory profile IDs (or the existing
local `default` profile) are accepted. A remote profile requires its connection
already selected in the original effective Fleet selection; otherwise the user
must explicitly configure that connection in settings. Only the chosen remote
profile needs fresh authenticated readiness. No saved Fleet connection, window,
dashboard or login preference is changed, and no central session mutation occurs.

The existing `launch {preview_id}` / `launch_status {launch_id}` protocol retains
its Launcher lock, lifecycle Ticket, installation/selection checks, final readiness
proof, exact process ownership checks and durable no-resend receipt. The reviewed
BAT launch effect updates only the profile index's `activeProfileIds` using the
original-byte compare-and-replace, then invokes the fixed BAT executable with
empty arguments. BAT can also open a local anchor window. An already running BAT
process prevents both the index write and another launch; this does not prove the
chosen profile or session is visible. Missing installation/profile/readiness and
unknown ownership are visible refusals, never alternate shell/deep-link launches.

The shared UI stores the exact preview ID, chosen profile, configuration binding
and launch summary before any launch request. The storage namespace includes the
central backend/principal and complete session identity. Initial and read-back
receipts must match that preview ID and exact one-profile/no-dashboard request.
Reload and lost replies read the original receipt only. An unsubmitted preview
restored from storage remains cancellable but cannot launch until the user explicitly
reviews the original profile again, obtaining a fresh native handle. This does not
release attempted requests whose receipt is absent or unknown; `PREVIEW_EXPIRED`
never authorizes a replacement for those requests. Unknown/prepared receipts
remain frozen; there is no automatic new preview, relaunch or selection change.
A new choice requires an explicit action after a definite terminal outcome, or
cancelling a preview which has never been submitted. Storage failure prevents the
launch. A stale view/account switch cannot issue a follow-up effect. Keyboard focus stays
on the pressed handoff action while its read, review, or launch runs. Review
moves focus to the launch button; a finished launch or retry moves to the next
choice, or to Read when that choice is hidden; cancel and a new choice return
to the profile selector.

Validation uses temporary profile-index/configuration fixtures, injected process
observations and browser/native IPC doubles. It does not claim installed BAT
launch, Windows interactive acceptance, or an exact session-focus capability.
