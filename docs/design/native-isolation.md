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
| Original carrier path | Read-write | Receipt's isolated clone, worktree and own Git metadata, created on the bounded volume |
| `/scratch` (also HOME/TMPDIR), `/state` | Read-write | Separate Attempt directories on that same bounded disk volume |
| `/proc` | Private procfs | New PID namespace; never host procfs |

The root is empty except these entries and read-only namespace scaffolding.
Carrier, scratch and state are never tmpfs. After receipt readback, `build_plan`
requires the clone, worktree and Git directory to share the device of scratch/state.
Total filesystem capacity, not free space, must fit the declared quota; 1 GiB is
the provisional ceiling pending owner policy. Every writable mount records its
device and capacity once in the plan's `writable_storage` manifest. A missing or
inconsistent capacity observation refuses with `NATIVE_ISOLATION_QUOTA_UNPROVEN`.
Nested mounts inside the clone remain refused, even on the same device.

The operator provisions the volume, not this policy. The existing
`native_carrier.prepare(..., managed_root=...)` creates the carrier directly on it;
no carrier is moved or copied, and no configuration setting is added. The volume
enforces one aggregate ceiling, not a per-file limit or polling-based directory
quota. Spool remains outside the sandbox and requires disk.

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
The canary fills a file in the carrier worktree until `ENOSPC`, checks the actual
target and bytes written against the observed capacity, removes the fill file,
and checks byte-identical sentinels outside the volume. Its numeric observations
are printed in the test output. A provisioned volume and permitted namespaces are
required for this real-kernel check; a policy fixture is not equivalent evidence.
See [bubblewrap](https://github.com/containers/bubblewrap/blob/v0.9.0/bwrap.xml)
and [Ubuntu's release notes](https://documentation.ubuntu.com/release-notes/24.04/).

這是獨立 coding policy；PM 合約不變。僅明列的唯讀工具鏈與本 Attempt 的磁碟區可見。
人工工作區、憑證、資料庫、spool 與 host sockets 不掛入；無網路、無繼承 descriptor。
實機 canary 是必要 CI gate；本機 skip 不代表隔離通過，模型／實機產品驗收仍未完成。

Git control overlays follow the writable carrier bind: `.git/config`, `hooks/`,
`info/`, `objects/info/`, the worktree's `.git` pointer, and its gitdir's `gitdir`,
`commondir` and existing `config.worktree`. Missing mandatory paths refuse; only
managed preparation creates the empty control directories, never the policy.
Objects (except `info/`), refs, logs, packed-refs, HEAD and index remain writable.
Host Git treats the carrier as untrusted and only uses the hardened runner;
checkpoint sources and carrier readback share the hazard check. Delivery must
never run porcelain in the carrier; delivery is not implemented here.

For passwordless test runs, an operator can provision a 64 MiB disk volume once:
```sh
(
set -e
mkdir -p "/path/to/isolation-volume"
test ! -e "/path/to/isolation-volume.img"
truncate -s 64M "/path/to/isolation-volume.img"
mkfs.ext4 -q -F "/path/to/isolation-volume.img"
sudo mount -o loop,nodev,nosuid,noexec "/path/to/isolation-volume.img" "/path/to/isolation-volume"
sudo chown "$(id -u):$(id -g)" "/path/to/isolation-volume"
BAC_ISOLATION_QUOTA_ROOT="/path/to/isolation-volume" bash scripts/check_native_isolation.sh
)
```
With that variable set, the script neither creates nor mounts/unmounts a volume.
Policy checks still apply. Carrier capacity now shares the bounded volume with
scratch/state. Coding readiness stays off: model access, network, credentials,
native routes, the product launcher, API and UI integration are not implemented.
控制檔以唯讀 overlays 保護；一般物件、refs 與 index 仍可寫。預先配置的磁碟區不需
每次 sudo。carrier 由既有 prepare 直接建於該磁碟區，不搬移、不複製；readback 後核對
clone、worktree、Git directory 與 scratch/state 同裝置，共用總容量上限，並拒絕巢狀掛載。
manifest 為每個可寫掛載記錄一次 device 與 capacity；觀測不可得即拒絕，不退回無上限。
1 GiB 仍是待 owner 確認的暫定政策。真實 canary 須取得 ENOSPC，且磁碟區外 sentinel
位元組不變；本機仍需預先配置磁碟區與可用 namespaces。模型存取、網路、憑證、原生
路由、產品 launcher、API 與 UI 尚未接上，coding readiness 保持關閉。
