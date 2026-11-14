import {invoke as coreInvoke, isTauri as coreIsTauri} from '@tauri-apps/api/core';
import type {InvokeArgs, InvokeOptions} from '@tauri-apps/api/core';

export const isTauri = coreIsTauri;

export async function invoke<T>(command: string, args?: InvokeArgs, options?: InvokeOptions): Promise<T> {
  if (command === 'native_status') return coreInvoke<T>('plugin:bac|native_status', args, options);
  const input = args && !Array.isArray(args) && !(args instanceof ArrayBuffer) && !(args instanceof Uint8Array)
    ? args.input as {action?: unknown} | undefined : undefined;
  if (command === 'managed_control' && input?.action === 'status') {
    return {background: false, lifecycle: null} as T;
  }
  if (command === 'tailscale_control' && input?.action === 'status') {
    return {version: 1, supported: false, can_open: false, installation: 'unknown', login: 'unknown', latest: null} as T;
  }
  throw Object.assign(new Error('This operation is unavailable in the Dashboard preview.'),
    {code: 'BAC_PREVIEW_UNSUPPORTED'});
}
