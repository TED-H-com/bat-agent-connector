# Remote host-local usage collection

This addendum extends [agent resource observations](agent-resource-observation.md)
within the existing E/UX/R/V/K backlog. It does not establish R0–R5 acceptance,
complete W30 or close the open v1.2 attribution and forecasting work.

The central Python daemon **pulls** metrics over the existing restricted SSH
host-helper transport. Each source optionally names a configured Fleet host;
its SSH alias comes from the existing `[managed_setup.ssh_hosts]` mapping
(or the existing verification SSH-host settings). SSH uses `BatchMode=yes` and
`StrictHostKeyChecking=yes`, reusing administrator provisioned keys and trusted
known-host entries. There is no new secret, listener, BAT channel, account login
flow or provider request. The Task Service binds loopback; push would require a
new listener and authentication provisioning. The existing code supports the
proposed pull design without changing that direction.

```toml
# The Fleet host example-worker must already be configured in this document.
[managed_setup.ssh_hosts]
example-worker = "example-worker-ssh"

[usage]
interval_s = 60

[[usage.sources]]
source_id = "worker-coding"
workspace_id = "ws_11111111111111111111111111111111"
host = "example-worker"
adapter = "codex-rollout-v1"
path = "/srv/example-observations/coding.jsonl"
session_id = "example-session"
role = "implementer"
```

Omitting `host` preserves central-local collection and its existing binding hash.
Unknown host names are configuration errors. Host name, endpoint, pinned BAT
identity and profile enter the remote source binding hash; changing that identity
produces `binding_changed`, withholding history until a new reviewed source ID
is configured. Paths and session dedupe are scoped by host. The configured SSH alias also enters the persisted binding; changing it requires
a new reviewed source ID and fences an in-flight read. No URL or credential appears in the projection.

## Helper, durability and privacy

The fixed version-1 helper runs with `python3 -I -S -B -c`. Central ships the exact
`read_page`, usage adapters and ISO timestamp parser source in an isolated
in-memory package; there is no second parser, remote installation or file write.
The source grant (adapter, absolute path and original session identity) is
embedded in this fixed helper code. Stdin accepts only version, cursor and
allowlisted parser state; it cannot choose another path or executable. The helper
requires Python 3.10+ and POSIX no-follow directory/file opens. Every parent and
leaf is opened without following symlinks; only regular files are read. There is
no glob, home scan or session discovery. Sources remain read-only grants, never
proof of management ownership.

Only normalized sample identity, timestamps, bounded token counters, allowlisted
model/version context, parser state, cursor digests/file identity and allowlisted
quota windows return. Conversation content, prompts, tool arguments, cwd, raw
paths, session identity, account data and credentials do not return. Central
validates the exact response shape before any journal write. Diagnostics use
fixed codes and never log SSH stderr, request bodies or helper source.

The host's append-only JSONL is the offline buffer. Central retains the cursor
on `host_unreachable`, malformed/oversized replies and parse failures. Reconnection
continues from the committed offset. Rotation, truncation and detected rewrites
remain `source_changed`; they do not trigger an implicit reset. Samples keep the
existing SHA-256 adapter-key identities scoped by source ID and existing conflict
semantics, so replay does not double count. Cursor, parser, quota and samples
commit together under the collector lock, with owner lease and binding checks
before commit. Schema is unchanged: no `user_version` allocation or data step.

Each cycle reads one page per configured source (at most 64 sources fleet-wide,
hence at most 64 pages per host). Existing page limits remain 2,000 lines,
approximately 2 MiB plus one bounded 1 MiB line. Requests are at most 16 KiB,
replies at most 2 MiB and discarded stderr at most 8 KiB; each SSH exchange has
a 30-second deadline. Timeout/cancellation kills and joins its local SSH process.
No automatic retry occurs inside the cycle. A helper error retains all prior
facts but withdraws their current projection.

## Read model and UI

`features.pm_resources.version` stays 1; `remote_collection=true` and
`remote_hosts={transport:"ssh_pull",helper_version:1}` advertise support.
Rows expose `host` (configured Fleet name, null for central-local), `host_id`
(Fleet name or durable central server ID), and `host_kind` (`fleet_host` or
`central_local`). Daily series include `host`. Sources across hosts share the
existing workspace resource response; clients may group by `host` without an
additional aggregate that could hide incomplete metrics. Dashboard source labels
include the configured host in both languages; unreachable is localized as
unavailable. Shared browser assets are regenerated from `desktop/src`.

Unavailable/unknown usage is null, never zero. `host_unreachable` contributes no
totals or daily series and supplies no fresh quota. Existing complete-day rules
are unchanged. `coverage_at` uses the **central clock immediately before the
remote request starts**, not the remote clock or response/commit time. This is
conservative for network delay and avoids trusting host clock skew for coverage.
The source log's original observation timestamps still determine sample dates.

Remaining work: graphical source binding/discovery, additional named adapters,
account/pool federation, accepted-delivery attribution, capacity, forecasts and
policy execution remain open. Real SSH installation, real BAT/model acceptance
and physical-mobile acceptance are not established by the offline fixtures.
