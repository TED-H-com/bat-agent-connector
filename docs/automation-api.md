# Automation API

External housekeeping scripts should import only `bat_agent_connector.automation`
instead of Connector implementation modules. `API_VERSION = 1` identifies the
facade contract. Exported names and signatures change only with a CHANGELOG entry
and a deprecation period.

Exports:

- Configuration and lifetime: `load_config`, `open_fleet(config=None)`.
- Optional judgment: `jev_for(fleet)`, returning the configured Jev or `None`
  when disabled or no provider key is available. Use `await jev.ask(state,
  questions)` on the returned object; unavailable or invalid judgments return `None`.
- Observation: `sessions_list`, `session_read`, `list_entries`, `sessions_triage`.
- Existing actions: `session_answer`, `session_send`, `session_set_permissions`,
  `session_failover`, `session_cleanup`.
- Helpers: `QUOTA_PATTERNS`, `parse_status`, `status_footer`, `BatError`, `API_VERSION`.

Re-exports retain their original signatures, defaults, return values and errors.
Existing confirmation, ownership, writes/orchestrate flags, rate limits, audit and
coordinator gates still apply. This facade introduces no new write paths.
`session_cleanup` remains a legacy read-only evaluation (`dry_run=True`); applying
cleanup uses the existing resource-cleanup preview/apply workflow.
`open_fleet` closes its Fleet on normal exit and exceptions. Supply an already
loaded config or omit it to use `load_config()`.

Example skeleton, invoked once an hour by an external scheduler:

```python
import asyncio

from bat_agent_connector.automation import BatError, open_fleet, sessions_triage


async def sweep():
    async with open_fleet() as fleet:
        # example-host is a configured synthetic host name.
        report = await sessions_triage(fleet, host="example-host", use_jev="never")
        # Inspect report and select any authorized follow-up through this API.
        return report


if __name__ == "__main__":
    try:
        asyncio.run(sweep())
    except BatError:
        # Record a sanitized failure and let the next scheduled run retry reads.
        raise SystemExit("Sweep failed") from None
```
