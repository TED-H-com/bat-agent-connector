# Bounded native PM analysis

This optional adapter provides one fresh, bounded, read-only PM analysis turn. It
implements the native-adapter portion of the [v1.2 plan](../product/pm-v1.2-plan.md)
alongside the [BAT observation contract](pm-runtime.md). It does not adopt plans,
dispatch coding work, operate Git, or supply a second operation queue. The
workspace command service remains responsible for authorization, frozen context,
writer admission, durable receipts, reply import and recovery.

The process primitives live in `src/bat_agent_connector/native_process.py`:
`BoundedNativeProcess` owns bounded stream IO, fsynced spool receipts, close
escalation and evidence retention; `inspect_process` supplies read-only Linux
death proofs. The PM adapter inherits these primitives and retains its launch
policy, sandbox, environment, tools refusal, single-turn protocol and assessment
in `pm_native_runtime.py`. Existing receipt bytes and `PM_NATIVE_*` errors remain
unchanged. A stored PID is only an inspection input; signalling requires the
owned subprocess object retained by the launching adapter. This extraction adds
no route, credentials, permissions or API surface.

## Discussion composer

An explicit [continuation brief](pm-continuation-brief.md) preserves a versioned
summary in the existing run receipts. Later ordinary analysis can reuse its
readable interpretation and newer messages within the context budget; current
input dispositions, original references, as-of time and omissions remain clear.
Preparing or reading the brief does not adopt a plan or authorize coding work.

An existing discussion offers **Send to PM** for ordinary analysis and **Save
note only** for `pm.message.append`. **Make a plan**, when advertised, retains
`pm.conversation.propose`; sending an ordinary request does not adopt a plan or
start coding. The analysis adapter and context options remain in Context.

Send to PM reuses `pm.run.analyze` or `pm.run.bat.analyze`, the current thread
version, preflight eligibility and the existing durable form. The request is the
central run's `purpose`, limited to 4,000 characters. The transcript shows that
saved request beside its imported reply, using the run and operation identities.
It does not invent an additional user message or source reference. The bounded
run list retains the server's partial-history indication and visibility checks.
Each analysis still consumes the existing frozen context; this UI does not add a
persistent model session or change which sources a later analysis receives.

An uncertain response retains the original request key and receipt. Completing
that request clears the composer only when it still contains the original text;
a newer draft survives recovery. A new discussion first saves its initial note
through the existing create-and-append flow, then offers Send to PM. Starting a
new discussion and asking the PM in one compound action remains follow-up work.

## Supported boundary

The initial contract is `codex-pm-linux-v1`, for Linux, bubblewrap and exactly
`codex-cli 0.160.0`. Source review uses OpenAI's
[pinned release commit](https://github.com/openai/codex/tree/a956835d020762cb2b570053af06f643a11c0ecc).
Other versions and operating systems report unsupported. Installed CLI discovery
alone does not make an adapter eligible to run a PM turn.

Each invocation creates its own disk directory, synthetic Codex profile and empty
workspace. Bubblewrap mounts the host filesystem read-only, binds only that owned
directory writable, starts a private PID namespace and enables parent-death
cleanup. The synthetic profile is mounted over the inherited Codex home inside
the child namespace; host `HOME` and `CODEX_HOME` values are not reassigned. Host
`.agents` and alternate `.codex` customization directories are hidden inside that
namespace. No user configuration, session, checkout or daemon is changed.

An existing auth file may be opened with `O_RDONLY|O_NOFOLLOW` and bind-mounted
read-only through its file descriptor. Its bytes are never copied into the
profile or recorded in a receipt. The parent closes the descriptor after spawn.
Only a small environment allowlist reaches the child; API-key environment values,
configuration overrides and inherited runtime flags are excluded. This first
path uses the runtime's default provider; custom provider configuration is not
imported. File readability does not establish valid authentication.

The adapter refuses to launch when `/etc/codex/config.toml`, `managed_config.toml`
or `requirements.toml` exists. It does not remove or bypass administrator policy.
Supporting managed installations requires a separate reviewed contract.
`project_root_markers=[]` restricts project discovery to the new empty workspace;
ancestor repositories cannot contribute project settings. These settings and
the actual session boundary are checked before any model input is sent.

The synthetic configuration disables shell/unified execution, subagents, hooks,
apps, plugins, browser/image/computer tools, code mode, memories, goals, skill
installation and external web search. Bundled/cloud/host skills are disabled.
`thread/start` supplies `environments=[]`, no dynamic tools, no selected capability
roots, read-only sandbox and approval policy `never`. The pinned
[tool construction](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/core/src/tools/spec_plan.rs)
omits shell, patch and image-file tools when no environment exists. MCP tool
construction additionally requires configured servers, whose absence is checked
in the actual fresh thread.

This is a source-backed effectful-tool restriction, not an observed empty tool
catalog: the app-server protocol exposes no complete tool-list endpoint. Its
public configuration response also omits the internal `update_plan` and
`request_user_input` switches, although the pinned input configuration disables
them. Capability reports preserve both limitations. Unexpected tool items or
server requests cause the turn to fail closed.

## Preflight and capability evidence

`installation_eligibility()` is a cheap filesystem/platform hint used by capability
reads. It never spawns a process or reads credential bytes. Finding executable
paths and a readable auth file only makes the installation a preflight candidate;
version, options, authentication and model response remain unverified. Workspace
and thread reads advertise `native_analysis` separately from the legacy BAT
`runtime:false` field, which is explicitly marked with `runtime_adapter=bat` and
`runtime_legacy=true`. Every client uses the same central `pm.run.analyze` action
and original operation key; refreshing a page does not launch an agent.

`await probe(scratch_root, run_id, ...)` launches and closes an isolated process
without sending `turn/start`. `use_auth=False, offline=True` also omits auth and
isolates the network namespace, allowing a no-provider test.

The sequence is initialize, effective configuration readback, skills inventory,
fresh ephemeral thread creation, then that thread's MCP inventory. Configuration
validation occurs before thread creation. Readback must confirm all observable
restrictions, no skills/errors, no MCP servers, no prior turns, no instruction
sources, no environment access, the requested model if specified, and the exact
read-only sandbox. A failed check prevents any model turn.

Reports preserve the contract, runtime version and reviewed source, run/thread
IDs, actual selected model, configuration digest, confirmed options and isolation,
auth-file readability and unsupported observations. `model_execution_verified`
remains false after preflight. `automatic_pm_turn` means the local boundary is
eligible and an auth file is bound; authentication and provider response remain
unverified until an actual authorized turn. No live model completion is claimed
by the no-model tests.

Failed preflights also report a fixed diagnostic stage: installation/version,
isolation setup, process spawn/identity/receipt, initialization, configuration,
skills, fresh thread or MCP inventory. `PM_NATIVE_VERSION_TIMEOUT`,
`PM_NATIVE_LAUNCH_TIMEOUT` and `PM_NATIVE_PREFLIGHT_TIMEOUT` distinguish elapsed
bounds from `PM_NATIVE_BOUNDARY_UNVERIFIED`. Elapsed boundary and stage times are
captured before process cleanup; they identify the suspended phase, not its cause.
The version check remains bounded at 5 seconds and launch/preflight each at 20
seconds. No retry or model input follows a failed probe. Cancellation still
propagates as cancellation. Diagnostics contain only fixed labels and timings,
never configuration values, paths, credentials or stderr. After process launch,
the original exit receipt retains the same diagnostic beside its independent
process-death evidence. Real offline tests print the complete report on failure;
a focused pass does not replace a prior loaded-host failure.

## One turn and original evidence

The callable interface is:

```python
async with NativePmRuntime(
    scratch_root, run_id, model=model, record_lifecycle=save_receipt,
) as runtime:
    capability = await runtime.preflight()
    assessment = await runtime.run_turn(
        frozen_prompt,
        context_digest=frozen_context_digest,
        before_send=save_send_intent,
        record_event=save_native_event,
        timeout=300,
    )
```

Callbacks may be synchronous or async and must durably finish before returning.
The send-intent callback runs before the only `turn/start` write. It contains the
original run and thread, process identity, SHA-256 of the exact prompt, optional
frozen-context digest, deterministic client-message UUID, request ID and capability
report. Failed persistence does not trigger another attempt.

`record_event` receives `{run_id, seq, event}`, where `event` is the original JSON
object from app-server, including ACKs, failures, notifications and terminal
items. `seq` is the adapter's contiguous observation order, not an invented native
server replay cursor. The parent must store the immutable original objects; this
adapter has no reconnect/resume or replay substitute for a broken pipe.

`assess_turn` requires a single matching native ACK, turn-start and completed
terminal event. It supports two explicit item views:

- `full`: the terminal contains exactly the original user input and client-message
  ID, together with the nonempty native final assistant message.
- `summary`: the live terminal contains only the final assistant item. The
  original stream must have matched `item/started` and `item/completed` records
  for every item, one original user echo matching the sent input digest and
  client-message ID, and a final completed assistant item exactly equal to the
  terminal summary. A summary alone cannot replace missing input or item evidence.

The pinned server's
[completion emitter](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/app-server/src/bespoke_event_handling.rs#L1327)
emits a singleton summary from
[the last nonempty final completed agent item](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/app-server/src/thread_state.rs#L201).
This live notification differs from a historical summary projection; the adapter
does not silently treat every summary format as complete. `notLoaded` is rejected.

Both paths reject observation gaps, foreign runs/threads/turns, duplicate IDs,
tool items, errors, partial items and mismatched input. ACK, process exit, idle
state or streamed text alone never prove completion. Output retains native
turn/message IDs, actual model and source event sequences; the summary path
includes ACK, turn-start, item lifecycle and terminal evidence. The raw native
terminal error remains stored even when the assessment has no assistant content.
The synthetic summary fixture models an authorized one-turn smoke's event shapes;
all IDs and text are replaced, unrelated notifications omitted and sequence
renumbered. It contains no original provider, account or usage data.

The limit is one MiB per input/JSON line, eight MiB total stdout, 64 KiB stderr,
128,000 output characters and at most one hour per turn (five minutes by default).
No model-controlled command is executed by this module. Input is a bounded frozen
text snapshot; the adapter does not expose the managed repository as a runtime
environment or grant permission to inspect a person's native session.

## Exit, cancellation and recovery

Lifecycle callbacks receive `process_started`, `preflight`, `turn_assessment` and
`process_exited` receipts. Process identity contains PID, Linux start ticks and a
digest of the boot ID. Exit receipts contain the same identity, actual exit code,
signal, termination action, sent flag, native-event count and confirmed-death
boolean. A cancelled async context first closes stdin, then bounds waits before
signalling only its own newly created process group. If SIGKILL still cannot be
confirmed, it records `process_exit_unknown` and retains local evidence.

`process_launch_attempted` starts false and becomes true immediately before the
app-server spawn await; the version probe does not count. False on the same
instance proves a prelaunch rejection. True with no process object is an uncertain
launch window, not proof that nothing started; local metadata is retained. After
a server restart, lack of a launch receipt alone never establishes no launch.

`inspect_process(identity)` is read-only. It returns `alive` for a matching
identity; `dead` with proof for an absent original PID, host reboot, reused PID,
or exited/zombie original process; and `unknown` for invalid or unreadable
identity. It sends no signal. Callers must never kill a PID merely because it
appears in an old record. Checking death does not establish native completion.

After a lost stream, the operation service can use confirmed death to mark this
bounded analysis aborted without an assistant reply and release only its own PM
writer. An unconfirmed process keeps the original operation unresolved; it must
not send another turn or clear unrelated locks. Any recovered completed reply
must still pass original-event assessment and normal workspace provenance checks.

Every receipt/event is also fsynced to the owned private `events.ndjson` before
its durable callback. Successful callbacks permit cleanup after process exit.
Any callback failure or unconfirmed death retains `runtime.retained_directory`,
including the sole surviving original transcript. The caller owns subsequent
reconciliation and removal of that specific directory. There is no blanket temp
cleanup and no deletion of another runtime's files.

Tests use mock app-server streams for model behavior, failure and cancellation.
Optional real Linux checks perform no model calls: offline preflight, hostile
ancestor configuration isolation, read-only canary enforcement and process
identity checks. These are local boundary evidence, not end-to-end PM/model or
Windows/macOS acceptance.

## Bounded model smoke evidence

On 2026-10-11, an independent synthetic discussion ran one model turn through
`pm.run.analyze` using CLI 0.160.0 and reported model `gpt-6.1-sol`. The corrected
summary reducer imported one assistant message with original provenance, matched
the journal assessment, confirmed process death and released the writer. The
response kept confirmed login/password-reset requirements separate from an
unadopted referral/discount proposal. No repository or Fleet data was supplied.

An earlier independent smoke had conservatively aborted because the initial
reducer did not support the protocol's summary terminal. That terminal outcome
was retained; the successful smoke was a new explicit operation, not a replay
that changed the failed history. Synthetic scratch data was removed after an
exact process-identity death recheck. This evidence covers one Linux analysis
path, not all models, platforms, BAT execution or PM worktree document writing.


### Settings installation read

`GET /api/v1/pm/installation-capabilities` requires `observe` and no workspace. It
projects `pm_native_capabilities.installation()` from the same cheap
`installation_eligibility()` used by workspace capabilities, plus action registration.
It returns `workspace_required: true` and no workspace authorization. This read never
spawns a process, reads authentication bytes or verifies a model; existing workspace
membership, scopes and per-run gates remain authoritative. The capability feature
`pm_workspaces.installation_eligibility` advertises the read. Settings entered from a
discussion instead uses the existing workspace capabilities read, including `bat_analysis`.
