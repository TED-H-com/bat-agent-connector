# Native coding boundary experiment

This is an **unregistered, offline Linux spike** for W23. It does not enable a
product capability, run a model, obtain authentication, create an operation or
claim a completed coding turn. Existing `codex-pm-linux-v1` analysis remains
unchanged. `eligibility()` always returns `supported: false` and
`product_execution_authorized: false`, including after a successful probe.

The fixed source is OpenAI Codex CLI **0.160.0**, commit
[`a956835d020762cb2b570053af06f643a11c0ecc`](https://github.com/openai/codex/tree/a956835d020762cb2b570053af06f643a11c0ecc).
The offline executable version is checked inside the outer namespace. A version
string is not a source-build attestation or evidence of model authentication.

## Source-backed seam

The pinned [`codex sandbox linux --permission-profile NAME --cd DIR -- COMMAND`](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/cli/src/lib.rs#L105)
uses the same compiled permission profile and Linux sandbox machinery as tool
execution. A custom profile without `extends` begins with an empty restricted
filesystem and restricted networking
([compiler](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/core/src/config/permissions.rs#L436)).
The experiment does not use `:workspace` or `:minimal`: legacy workspace-write
allows broad reads, and Linux `:minimal` includes all of `/etc` and `/usr`.

The restricted-read implementation builds an empty filesystem and mounts only
explicit roots
([bubblewrap builder](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/linux-sandbox/src/bwrap.rs#L525)).
The named profile permits writes to `/workspace` and disk-backed `/tmp`, read-only
access to explicit toolchain roots, and read-only `.git`. Explicit `none` entries
hide controller state/auth canaries, foreign checkout canaries and customization
directories. An explicit denied-read policy also prevents the upstream tool
executor from converting an approved command into unsandboxed execution
([guard](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/core/src/tools/sandboxing.rs#L239)).
This guard is source evidence; the offline command does not exercise a model's
approval request or the app-server tool catalog.

`features.use_legacy_landlock=false` is fixed. The legacy implementation grants
root reads and cannot substitute for this boundary. Network restrictions apply
to tool subprocesses: IP sockets, connect/bind/listen, ptrace, process-vm access
and io_uring are filtered
([seccomp implementation](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/linux-sandbox/src/landlock.rs#L180)).
The entire **probe** also has an outer network namespace, so no provider request
is possible. A future authenticated parent would need a separately reviewed
network boundary; this spike does not prove that arrangement.

## Outer containment and inputs

`boundary_argv()` produces a fresh bubblewrap root with private user/PID/network/
IPC namespaces, parent-death cleanup, and no host-root bind. Only the exact Codex
binary, its bundled bubblewrap, an explicit read-only toolchain closure, and
synthetic probe directories are mounted. The test uses a newly initialized disk
Git worktree and a reviewed Python distribution plus exact ELF dependencies.
It does not mount another checkout, an entire home, `/etc`, `/usr`, or a shared
Git clone. The namespace skeleton is temporary; all fixture bytes, worktree,
profile, logs and tool scratch are on the caller's disk-backed test directory.

The controller's real configuration and credentials are never read. Original
HOME/CODEX_HOME strings are retained only as child-namespace mount locations for
the private synthetic profile. Parent environment variables are unchanged; the
child receives an explicit small map, and tools use `inherit="none"` with a
separate fixed environment. Login shells are disabled. Parent file descriptors
are closed, except standard streams, and `/proc` belongs to the new PID namespace.
The fake auth canary is deliberately visible to the outer CLI but denied to the
inner tool. Real credentials are not part of this fixture.

In-project `.codex` and `.agents` directories are masked before CLI configuration
loading. MCP configuration is empty, and plugin/skill/hook/subagent/web features
are disabled. The probe does not start an app-server or MCP runtime, so absence of
an inherited MCP launch is not advertised as a complete runtime tool inventory.
Managed `/etc/codex` configurations cause refusal through the existing check;
operators must not remove administrator policy to make this probe eligible.

The module accepts only already explicit probe paths. Validation rejects
overlapping writable/controller/foreign directories and obvious broad toolchain
mounts; it is **not** a central ownership/adoption check. There is no public API
for a client or model to supply mounts or commands.

## Evidence and limits

The optional real-kernel test edits the owned source, runs a controlled child
shell, and writes private disk scratch. Negative checks cover synthetic foreign
and auth reads/writes, Git metadata/toolchain writes, symlink and `/proc/1/root`
aliases, inherited environment/config and descriptors, and network/ptrace calls.
The original host-side canary bytes and Git configuration are checked afterward.
Unsupported CLI/kernel installations skip the optional native fixture explicitly;
they never turn eligibility true. Unit profile tests do not prove kernel isolation.

`offline_probe()` bounds each captured stream to 64 KiB and the command to at most
30 seconds. It retains the original Linux PID/start-ticks/boot identity and reports
death separately from command success. Cancellation, time or byte limits terminate
only its newly created process group; unknown death requires retaining probe data.
No successful shell exit is a task verification, model completion or delivery
receipt. No thread/turn IDs exist because no model turn was sent.

Initial local native evidence: the empty-root outer namespace successfully ran
the installed `codex-cli 0.160.0 --version`. Both its bundled bubblewrap and an
explicit mount of the system `/usr/bin/bwrap` then refused the nested tool
namespace with `No permissions to create a new namespace`. The system binary
uses its own exact ELF dependencies, not a broader root mount. The host reports
`unprivileged_userns_clone=1` and `apparmor_restrict_unprivileged_userns=1`; that
correlation is not proof of the precise kernel rejection mechanism. No host
policy, sysctl, AppArmor profile or installation was changed.

Consequently the real **coding/negative-canary profile remains unverified here**.
The optional fixture reports an explicit skip after preserving the refused
result and verifying original process death. Separate real outer-namespace
fixtures exercise capture timeout/output bounds; those do not prove the inner
named profile. A compatible installation can rerun the same fixed canary suite.

Final focused local checks on Python 3.12 and 3.10 each report **6 passed, 1
explicitly skipped**, with warnings treated as errors. The skip is the refused
inner coding namespace, not a passed escape test. Full Ruff and diff checks pass.
The two real outer-process tests verify timeout termination and output limiting
including bounded pipe drain after process death. Reproduce with the repository's
disk-temporary wrapper, under the shared native-test lock:

```sh
flock ~/agent-work/tmp/pm-v12-check.lock \
  pytest-disktmp uv run --python 3.12 --extra dev pytest -q -rs -W error \
  tests/test_pm_native_coding_sandbox.py
```

The lock path is a development coordination example; the test does not need a
configured central, API key, BAT host, provider or model. Use an equivalent disk
path when reproducing outside this development workspace.

## Remaining production work

- A linked Git worktree's `.git` points outside the checkout. This probe uses its
  own standalone `.git`; allowing a shared clone would expose other objects and
  worktrees. A frozen, source-gated read-only metadata view or dedicated clone
  contract is still required. Git mutations remain central registered actions.
- The CLI auth store loads and may refresh/write credentials. No-auth offline
  containment does not resolve authenticated parent renewal, safe descriptor
  handling during refresh, or model endpoint access. Tools must never receive
  that material through environment, mounts, descriptors or parent `/proc`.
- Toolchain/package closure is explicit here, not a general dependency installer.
  Network package installation, host hooks and arbitrary external tools remain
  outside the experiment. Real product toolchain manifests need source/digest,
  authority and final-frame validation.
- A future coding adapter must use a fresh owned app-server thread/turn without
  `outputSchema`, preserve original command/file-change item lifecycles, exact
  prompt/context/candidate identities and confirmed process death, and integrate
  with existing durable operation/source/writer gates. The strict PM read-only
  reducer must not be relaxed. Tool execution is not model-task acceptance.
- CPU, memory, disk and process-count limits, full syscall escape analysis and
  kernel/platform support need additional work. Time/output bounds and these
  targeted canaries are not a general sandbox certification.
