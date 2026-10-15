"""Read-only native analysis eligibility; per-run evidence is a separate contract."""
from __future__ import annotations

from . import pm_native_runtime


def installation(ops):
    """Workspace-free installation hints, never run authorization or model readiness."""
    result = pm_native_runtime.installation_eligibility()
    result["action_registered"] = "pm.run.analyze" in ops.actions
    return result


def read(ops, principal, workspace):
    # The caller must already have checked workspace membership. No runtime is launched here.
    result = installation(ops)
    result["authorized"] = (result["action_registered"]
        and (principal.admin or workspace["membership"]["role"] in {"owner", "contributor"})
        and all(principal.allows(scope) for scope in ("observe", "manage", "start")))
    return result
