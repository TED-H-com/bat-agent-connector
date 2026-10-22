// Inventory.hosts output from a temporary production Journal with synthetic observations.
// Times match tests/fixtures/work-profile-readiness.json; no BAT/network calls.
export const hostGolden = {
  "host_not_refreshed": {
    "hosts": [
      {
        "host": "fixture-host",
        "reachable": true,
        "error": null,
        "server_version": null,
        "sessions": null,
        "last_attempt_at": "2026-09-21T14:13:20Z",
        "last_success_at": "2026-09-21T14:13:20Z",
        "valid_until": "2026-09-21T14:16:20Z",
        "consecutive_failures": 0,
        "stale": true,
        "stale_reason": "host_not_refreshed",
        "confinement": {
          "policy": "default",
          "host_account": {
            "declared": false,
            "status": "unknown",
            "reason": "unchecked_or_stale",
            "protected_roots": [],
            "checked_at": null,
            "config_sha256": "e56647a84e1c82b505f98f29cfa166190b8446f78faecd34b3da6ac981cabe4a",
            "start_effect": "fallback_default"
          },
          "network_configurable": false,
          "writable_roots_configurable": false,
          "agents": {
            "claude": {
              "requested_level": "prompt_gated",
              "verified_level": "none",
              "reachable_options": [
                "permissionMode"
              ],
              "limits": [
                "preapproved_rules_and_shell_can_write_outside",
                "host_account_declared_roots_only"
              ],
              "gap": "plain_default_requires_approvals"
            },
            "codex": {
              "requested_level": "os_sandbox",
              "verified_level": "none",
              "reachable_options": [
                "codexSandboxMode",
                "codexApprovalPolicy"
              ],
              "limits": [
                "individual_approval_can_escape",
                "writable_roots_not_configurable",
                "network_not_configurable"
              ],
              "gap": "sandbox_enforcement_unverified"
            }
          }
        },
        "discovery": []
      }
    ]
  },
  "host_unreachable": {
    "hosts": [
      {
        "host": "fixture-host",
        "reachable": false,
        "error": "CONNECTION_FAILED",
        "server_version": null,
        "sessions": null,
        "last_attempt_at": "2026-09-21T14:27:20Z",
        "last_success_at": "2026-09-21T14:22:20Z",
        "valid_until": "2026-09-21T14:25:20Z",
        "consecutive_failures": 0,
        "stale": true,
        "stale_reason": "host_unreachable",
        "confinement": {
          "policy": "default",
          "host_account": {
            "declared": false,
            "status": "unknown",
            "reason": "unchecked_or_stale",
            "protected_roots": [],
            "checked_at": null,
            "config_sha256": "e56647a84e1c82b505f98f29cfa166190b8446f78faecd34b3da6ac981cabe4a",
            "start_effect": "fallback_default"
          },
          "network_configurable": false,
          "writable_roots_configurable": false,
          "agents": {
            "claude": {
              "requested_level": "prompt_gated",
              "verified_level": "none",
              "reachable_options": [
                "permissionMode"
              ],
              "limits": [
                "preapproved_rules_and_shell_can_write_outside",
                "host_account_declared_roots_only"
              ],
              "gap": "plain_default_requires_approvals"
            },
            "codex": {
              "requested_level": "os_sandbox",
              "verified_level": "none",
              "reachable_options": [
                "codexSandboxMode",
                "codexApprovalPolicy"
              ],
              "limits": [
                "individual_approval_can_escape",
                "writable_roots_not_configurable",
                "network_not_configurable"
              ],
              "gap": "sandbox_enforcement_unverified"
            }
          }
        },
        "discovery": []
      }
    ]
  }
};
