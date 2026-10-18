# EC-1.1: retain an unsent Work instruction as an Idea

This P2 increment extends T2/T6 and K01/K06. It does not gate the existing
continuity candidate or replace the P5 send fence.

An explicit `pm.work_input.defer` operation targets the original workspace,
Work and `source_operation_id`. Admission requires observe/manage/operate,
writer membership, the original input actor/controller and a currently queued,
proven-unsent Work input. The reason is user text. The operation never sends a
model prompt, adopts scope, starts another attempt or touches a manual session.

The handler records two separate central effects:

1. Recheck the original queue receipt, then call the existing operation cancellation.
   Keep its original ID, key, send fence and history. Wait for a terminal,
   proven-unsent cancellation before saving an Idea. A crossed fence, unknown
   result or another controller refuses instead of being cleared.
2. Create an Idea discussion using the existing PM thread/message/context records.
   Its stable identity derives from this original deferral receipt. Bind the
   original Work and cancelled input; keep the complete original words through
   the fixed source reference, including inputs longer than one PM message.
   Store the reason as a note, without sending it to PM.

A saved cancellation is not undone when the draft save fails. The original
operation enters needs_attention with a recoverable receipt; resume replays the
completed local step and only retries the draft save. Lost replies, service
restart and repeated original keys must not send, cancel again or create another
Idea. Concurrent new deferral requests cannot both claim the same queued input:
its cancellation request and the first deferral step commit in the same journal
transaction, and the second must recheck that original state.

Sources and writer membership are rechecked before each new effect. Withdrawal
also hides the derived Idea and its text through the existing source lineage.
The read-only draft link conveys no execution authority. Stopping the deferral
itself follows ordinary operation cancellation and retains its original input
and completed-step receipts.

Web and desktop expose the same action only from a currently authorized queued
input. Existing durable form keys preserve intent during failed readback. The
result opens its exact Idea; it does not send to PM or mark requirements accepted.
Tests must separately prove cancel-before-save, partial-save recovery, restart,
original key replay, long-source preservation, send-race refusal, controller and
source withdrawal, plus English/zh-TW HTTP/IPC behavior.

## Review evidence

The baseline uses the exact `8c069f4d` shared browser bundle and the same synthetic
queued input. These screenshots cover browser layout only, not physical keyboards,
real BAT sessions, model use or Fleet acceptance.

| View | Before | Draft open |
| --- | --- | --- |
| English, 1440px | [Baseline](workbench-evidence/ec11-input-idea/before-en-1440.png) | [Candidate](workbench-evidence/ec11-input-idea/after-en-1440.png) |
| zh-TW, 390px | [Baseline](workbench-evidence/ec11-input-idea/before-zh-TW-390.png) | [Candidate](workbench-evidence/ec11-input-idea/after-zh-TW-390.png) |
| zh-TW, 768px | — | [Candidate](workbench-evidence/ec11-input-idea/after-zh-TW-768.png) |

The reason form stays outside the refreshing input list, preserving its node,
selection and draft during an unrelated read. It uses the existing secondary
button, disclosure, field and durable-operation controls. Its explicit save
cancels the queue and saves a draft; opening the returned link remains separate.
The original input receipt links its committed draft-save operation and, after
success, the exact discussion. A partial save remains visible after reload.
