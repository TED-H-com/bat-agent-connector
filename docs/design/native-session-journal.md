# Native session evidence and identity

`native_journal.schema(journal)` explicitly installs four additive tables.
It runs only `CREATE TABLE IF NOT EXISTS`, with primary-key uniqueness;
it allocates no data step or `user_version` and changes no PM table or history.
There is no daemon-startup installation. A future route registration owns it.

| Table | Facts and identity |
| --- | --- |
| `native_session_creations` | Unique server, adapter instance, native session ID and incarnation |
| `native_process_receipts` | Launch intent, spawn/create fences and process observations |
| `native_turn_receipts` | Input send fences, ACKs and terminal observations |
| `native_event_records` | Original ordered events, including partial evidence |

All rows carry operation, Attempt, session key, incarnation, turn ordinal,
input ID, sequence, canonical JSON and SHA-256 digest. The three receipt/event
tables identify a fact by that context and sequence. Creation JSON retains
the original start operation and Work/Attempt, controller binding, managed
carrier receipt, host binding, source SHA, CLI/version/artifact/protocol,
profile revision, nonce, process identity, created-event digest and timestamps.

An unknown native ID is an empty string on pre-creation launch facts only;
the creation receipt binds their original context to the observed nonempty ID.
A reserved ID is not proof that a session exists. Canonical replay returns
the original row without writing; a changed digest is a typed conflict.
There are no fact updates, deletes, replacements or upserts. Readers rebuild
state from facts; no stored status means an uncertain effect may be resent.

`LaunchIntent` and `CreatedSession` are internal typed producer boundaries,
never request deserializers. The latter requires the committed original intent,
matching nonce, cwd/profile/policy readback, full process identity and event
digest. A create reply also needs `create_sent`; process init needs the prior
`spawn_attempted` fence. Dictionary payloads, history/Work links, native files,
preallocated IDs and exit zero cannot mint a creation receipt.

Each append commits synchronously before returning. Nested transactions are
refused so a fence cannot be mistaken for a committed effect boundary.
Process facts hold launch/spawn/create fences; turn facts hold `turn_sent`.
Only a matching process-init launch's first input can precede creation.
Every subsequent input needs the committed creation receipt.

`native_identity.require_session` only reads. Call it at admission and again
before the final transport write. It requires the exact creation key and
the full `(boot_id_digest, pid, start_ticks)` produced by the shared process
helpers, matching the recorded creation process. It performs no OS inspection.
It projects controller authority/incarnation into the shared `require_entry`
gate; shared refusal codes pass through. A PM-run authority is unsupported.

Native refusal codes (defined once in `native_journal`):
`NATIVE_CREATION_UNPROVEN`, `NATIVE_IDENTITY_MISMATCH`,
`NATIVE_CONTROLLER_UNSUPPORTED`, `NATIVE_PROCESS_IDENTITY_UNPROVEN`,
`NATIVE_RECORD_CONFLICT`, `NATIVE_EVIDENCE_INVALID`, `NATIVE_FENCE_UNPROVEN`.
Shared codes include `FOREIGN_CONTROLLER`, `CONTROLLER_AUTHORITY_REQUIRED`,
`CONTROLLER_MEMBERSHIP_CHANGED` and `CONTROLLER_INCARNATION_CHANGED`.

No action, API/MCP route, capability, UI, adapter, credential, carrier,
isolation policy or recovery decision is introduced by these modules.
