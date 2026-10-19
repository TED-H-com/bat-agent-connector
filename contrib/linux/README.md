# Linux SSH tunnels and token provisioning

These optional operator tools use existing trusted SSH aliases and Connector
`file:` token references. They do not discover credentials or manage BAT sessions.
Review local configuration and command hooks before running them; keep token
sources and tunnel configuration outside the repository.

## User tunnel service

From the repository root:

```sh
install -d -m 700 ~/.local/bin ~/.config/systemd/user ~/.config/bat-agent-connector/tunnels
install -m 755 contrib/linux/batc-tunnel-check ~/.local/bin/
install -m 644 contrib/linux/systemd/bat-connector-tunnel@.service ~/.config/systemd/user/
install -m 600 contrib/linux/systemd/tunnel.env.example ~/.config/bat-agent-connector/tunnels/example-host.env
```

Edit the environment file: use an existing trusted SSH alias, a unique loopback
address/port and the BAT endpoint as seen from the SSH server. Only bare
`KEY=value` lines and whole-line comments are supported. IPv6 uses brackets,
for example `[::1]:9002`. Never use a wildcard or public local bind address.
The validator rejects missing/duplicate keys, invalid aliases and nonloopback binds.

Verify the SSH host key through your normal trusted process first and configure
noninteractive authentication (an available agent or a protected SSH key).
`BatchMode=yes` prevents password prompts. The service keeps `~/.ssh` readable;
private temporary storage may require an SSH agent socket outside `/tmp`.

```sh
~/.local/bin/batc-tunnel-check ~/.config/bat-agent-connector/tunnels/example-host.env
systemctl --user daemon-reload
systemctl --user enable --now bat-connector-tunnel@example-host
systemctl --user status bat-connector-tunnel@example-host
```

Substitute your instance name for `example-host` (the template is
`bat-connector-tunnel@<name>`). Failed connections retry with a fixed 15-second
backoff. On headless machines, a user manager must remain running after logout;
ask the administrator to enable linger for the intended account if needed.
After editing an env file, restart that instance. Stop/disable it with
`systemctl --user disable --now bat-connector-tunnel@example-host`.

## Token files

`batc-provision-tokens` requires Python 3.11+ and only the standard library.
Install it into `~/.local/bin`, copy `token-sources.example.toml` to
`~/.config/bat-agent-connector/token-sources.toml` with mode 0600, and edit the
host tables and target paths. The table names match the intended hosts.toml names.

- `bitwarden`: install `bw`, unlock it, and supply `BW_SESSION` in the environment.
  The helper runs `bw get password <item>`; the session never appears on argv.
- `bws`: install `bws` and supply `BWS_ACCESS_TOKEN` in the environment.
  The helper runs `bws secret get <id> --output json` and selects `value`.
- `command`: supply an explicit argv array for a trusted local hook. No shell runs;
  the hook must print only the UTF-8 token and optionally one trailing newline.

Obtain credentials using the chosen tool's secure interactive workflow; never put
session/access tokens in the TOML file, command arguments or shell history.
Backend output is captured and never displayed, including on failure.

```sh
batc-provision-tokens --dry-run
batc-provision-tokens --only example-host
batc-provision-tokens --spec ~/.config/bat-agent-connector/token-sources.toml
```

Dry run lists destinations without executing backends or creating files. Normal
runs remove one trailing newline, reject empty or larger-than-64-KiB tokens, and
atomically replace regular target files with mode 0600. Missing parent directories
are created with mode 0700; existing directory permissions are left unchanged.
Symlink targets are refused. Use private, user-owned parent directories and trusted
specs/hooks; do not let another user modify the directory tree during provisioning.
Failures return nonzero; other selected hosts still run. Rotate tokens by rerunning.

Pair each tunnel and provisioned token in `hosts.toml`:

```toml
[hosts.example-host]
url = "wss://127.0.0.2:9002/"
token_ref = "file:~/.config/bat-agent-connector/tokens/example-host.token"
# Also set fingerprint to the BAT certificate SHA-256 verified out of band.
```

Use a distinct `127.0.0.N:PORT` for each tunnel. SSH forwards TLS unchanged, so
retain the BAT server's trusted certificate pin. See
[hosts.example.toml](../../examples/hosts.example.toml) for the complete configuration.
