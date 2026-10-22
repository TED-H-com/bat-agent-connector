// Status is the native producer shape; runtime observations use Python's golden.
import {readFileSync} from 'node:fs';
const runtime = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-runtime-identity.json', import.meta.url), 'utf8')).same;
export function managedStatusFixture(background = true) {
  return {mode: background ? 'managed' : 'external', ready: background, background,
    runtime_version: background ? runtime.runtime_version : null,
    server_id: background ? runtime.server_id : null, principal_id: background ? runtime.principal_id : null,
    error: null, login_enabled: false, bundled_runtime: background ? runtime.bundled_runtime : null,
    running_runtime: background ? runtime.running_runtime : null, runtime_difference: background ? runtime.runtime_difference : null,
    lifecycle: background ? {state: 'running', requested_at: null as number | null, stopped_at: null as number | null} : null};
}
