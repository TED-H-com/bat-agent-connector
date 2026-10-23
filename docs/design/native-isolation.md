# Offline coding isolation

`native_isolation` is a separate internal policy; no route or adapter calls it.
PM keeps its existing read-only, single-turn policy unchanged. Coding reuses
`BoundedNativeProcess` for pipes, bounded reads, fsynced private spool, owned
process-group termination and confirmed death. The consumer has a bounded deadline.

The trusted operator supplies a fixed read-only toolchain manifest, protected
paths and pre-provisioned disk areas. None is deserialized from an action request.
`build_plan` reads back the typed carrier receipt, checks the local physical
identities on that same host, and emits comparable plan data.

| Mount | Access | Source |
| --- | --- | --- |
| `/runtime`, optional `/toolchain` | Read-only | Explicit fixed runtime/toolchain directories |
| `/lib/x86_64-linux-gnu`, `/lib64/ld-linux-x86-64.so.2` | Read-only | Explicit libraries and ELF loader |
| `/etc/ssl/certs/ca-certificates.crt` | Read-only | Explicit CA bundle |
| Original carrier path | Read-write | Receipt's isolated clone, worktree and own Git metadata |
| `/scratch` (also HOME/TMPDIR), `/state` | Read-write | Separate Attempt directories on a bounded disk filesystem |
| `/proc` | Private procfs | New PID namespace; never host procfs |

The root is empty except these entries and read-only namespace scaffolding.
Scratch/state are never tmpfs: total filesystem capacity must fit the declared
quota (at most 1 GiB); both share that kernel-enforced aggregate storage ceiling.
The operator provisions the volume, not this policy. No per-file limit or polling
loop is represented as a directory quota. Carrier and spool also require disk.

Overlapping protected paths, symlink mount roots, escaping carrier links,
pre-existing hardlinks, sockets/devices and nested host mounts are refused.
Manual/sibling checkouts, credentials, Connector journal, host sockets and outer
home/config are not mounted. The launcher spool stays outside every mount;
only stdout/stderr pipes cross the boundary, with all other descriptors closed.
Private user/PID/network/IPC/UTS namespaces, disabled nested user namespaces,
dropped capabilities, a new session and die-with-parent are mandatory. No network
is available. The environment is cleared and rebuilt from fixed values.

A zero-workload namespace probe precedes payload launch; carrier and filesystem
checks repeat afterwards. Missing bubblewrap or unavailable namespaces refuse.
Codes: `NATIVE_ISOLATION_MOUNT_UNPROVEN`, `NATIVE_ISOLATION_PATH_UNPROVEN`,
`NATIVE_ISOLATION_QUOTA_UNPROVEN`, `NATIVE_ISOLATION_UNAVAILABLE`,
`NATIVE_ISOLATION_NAMESPACE_UNAVAILABLE`; carrier refusals pass through.

Run `sudo -v && bash scripts/check_native_isolation.sh` on a Linux x86_64 host
with bubblewrap, e2fsprogs, sudo and the development environment. It creates a
64 MiB disk image, mounts it only for the test, requires the canary, then cleans up.
No model runs. The dedicated CI job temporarily disables Ubuntu's AppArmor
user-namespace restriction on its disposable runner only; the host script does
not change that setting. `BAC_REQUIRE_BWRAP=1` makes missing prerequisites fail.
See [bubblewrap](https://github.com/containers/bubblewrap/blob/v0.9.0/bwrap.xml)
and [Ubuntu's release notes](https://documentation.ubuntu.com/release-notes/24.04/).

這是獨立 coding policy；PM 合約不變。僅明列的唯讀工具鏈與本 Attempt 的磁碟區可見。
人工工作區、憑證、資料庫、spool 與 host sockets 不掛入；無網路、無繼承 descriptor。
實機 canary 是必要 CI gate；本機 skip 不代表隔離通過，模型／實機產品驗收仍未完成。
