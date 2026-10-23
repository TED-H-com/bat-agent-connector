# Standalone Rust Fleet example

Follow the [public setup guide](../../docs/design/fleet-configuration.md) before use.
Copy this directory's `client` files into your own private configuration root:

- [Inventory](client/fleet-inventory.json)
- [BAT profile index](client/bat-profiles/index.json)
- [SSH aliases](client/ssh-config)

Use [fleet.example.json](../fleet.example.json) for the native app configuration.
Replace the documentation address, SSH user and fingerprint placeholder with your own
reviewed values. The placeholder deliberately fails trust validation. No credentials
or live deployment settings are included; legacy PowerShell/VBS scripts are not required.

The home example explicitly sets host `prefer_lan: true` and uses LAN first with a 1500 ms budget, then Tailscale with a
600 ms budget and `allow_tailscale_relay: true`, then a configured Cloudflare fallback.
Dual-WAN load balancing with a different public address/port per connection is an
expected home condition. Without this opt-in, order and preference remain direct, LAN, then Cloudflare.
See [route settings and Python recovery](../../docs/design/fleet-routes.md) for
precedence, validation, and `hosts.toml` timeout examples. The native
[fleet.example.json](../fleet.example.json) remains an installation pointer, and
[central.example.json](../central.example.json) configures the central API identity;
neither accepts these inventory or Python client settings.
