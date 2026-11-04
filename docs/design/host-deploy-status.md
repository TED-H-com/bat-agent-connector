# Host deployment status

An explicit read-only observation within E04/E11 and the existing workbench direction.
It does not close E00–E11, R0–R5, A01–A40 or outstanding PM v1.2 work. Fixtures are
not installed/native, real BAT/model, SSH or physical-mobile acceptance evidence.

Configure `[[deploy_status.hosts]]` with a unique configured Fleet `host`, up to
20 absolute `deploy_dirs` and up to 20 additional `unit_patterns` (service-name
globs). The host must have an alias in `[managed_setup.ssh_hosts]`. See
[the example](../../examples/hosts.example.toml). At most 64 hosts are configured.

Use `batc hosts deploy-status [--host NAME] [--json]`, or open a host from the
Dashboard session list and choose **Host deployment status (read-only)**.
Collection is explicit, without background polling or persistence. CLI collects
using its local configuration; the authenticated Task Service serves Dashboard
reads using its configuration. Unknown/unconfigured hosts are errors.

The self-contained helper follows the unmerged remote-usage proposal's pull
pattern without importing any of its modules. Central runs `ssh -o BatchMode=yes
-o StrictHostKeyChecking=yes ALIAS` with a fixed, quoted `python3 -I -S -B -c`
in-memory source bundle. Aliases allow only letters, numbers, dot, underscore and
hyphen, beginning with a letter or number. No SSH trust enrollment, installation,
remote source file, writable cache or provider/BAT invocation occurs.

The helper's subprocess boundary accepts only fixed docker/podman `ps`, systemctl
`list-units` (system/user, JSON with text fallback), and configured-directory Git
`rev-parse`, `log`, `status` argv. It uses shell=False and the shared hidden-process
helper. Git disables optional locks, fsmonitor and untracked-cache use. There is
no index refresh, discovery, mutation, executable supplied by request, or inspect
of container environment. Container health comes from status text. Only compose
project/service and directory/config basenames survive label projection; raw
labels, environment and stderr are discarded. Commit subjects and other strings
are printable and truncated to 256 characters. Deploy rows carry configuration
index and basename, never the configured absolute path.

Version 1 returns collected_at, hostname, containers, compose_projects (state
counts), systemd, deploys and errors. Bounds: 500 containers/projects total,
200 units total, 20 deploy rows, four engine/systemd errors. Per-command output
is at most 2 MiB with 8 KiB discarded stderr and a three-second timeout. Central
caps replies at 2 MiB and stderr at 8 KiB, with a 30-second SSH deadline; overflow,
timeout and cancellation kill and join the local SSH process and cancel/join
readers. One daemon collector serializes requests to bound concurrent SSH load.
A multi-host request collects sequentially and can take 30 seconds per host.

Fixed per-source codes: missing, permission_denied, command_failed, timeout,
output_limit, invalid_output. Central failures: host_unreachable or invalid_reply;
SSH diagnostics are never logged or returned. An absent engine or inaccessible
user service manager is partial evidence, not an empty successful host. The
central validator rejects extra keys, wrong types, excessive counts, unknown
codes and mismatched deployment indices before returning observations.

Names, image references, descriptions and commit subjects are operator-visible
metadata. Do not put credentials into those fields. This observer never reads
container environment, Git remotes/config values or secret files. It grants no
management ownership or authority to start/restart/update anything.
