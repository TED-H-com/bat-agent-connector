import {type Page} from '@playwright/test';

// Hold verification and the optional rescan separately so assertions cannot race
// the bootstrap request counter or the subsequent Settings re-render.
export async function holdRecoveryReset(page: Page, reason: string, changed?: 'principal' | 'server' | 'unavailable') {
  await page.evaluate(({reason, changed}) => {
    const state = (window as any).__recoveryReset = {
      panel: document.querySelector('[data-earlier-drafts]'),
      slot: document.querySelector('[data-earlier-drafts-slot]'),
      focus: document.activeElement, scroll: document.getElementById('main')!.scrollTop,
      markers: Object.entries(localStorage).filter(([key]) => key.startsWith('batc.earlier-drafts.')).sort(),
      buttons: [...document.querySelectorAll('[data-earlier-group="draft"] button')],
      verificationWaiting: false, scanWaiting: false, bootstrapReturned: false,
    };
    const bridge = (window as any).__TAURI_INTERNALS__, invoke = bridge.invoke;
    let reset = true, verify = false, scan = false;
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    crypto.subtle.digest = async (algorithm: any, bytes: any) => {
      if (scan && new TextDecoder().decode(bytes).startsWith('["batc.')) {
        scan = false; state.scanWaiting = true;
        await new Promise(resolve => {state.releaseScan = resolve;});
      }
      return digest(algorithm, bytes);
    };
    bridge.invoke = async (command: string, args: any) => {
      if (command === 'connector_request' && args.input.path.startsWith('/events') && reset) {
        reset = false; verify = true;
        return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason, resnapshot: true, preserve_drafts: true}}};
      }
      if (command === 'connector_request' && args.input.path === '/bootstrap' && verify) {
        verify = false; state.verificationWaiting = true;
        await new Promise(resolve => {state.releaseVerification = resolve;});
        if (changed && changed !== 'unavailable') (window as any).__storage[changed] = 'different-' + changed;
        const response = changed === 'unavailable'
          ? {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: 'identity_unavailable', resnapshot: true, preserve_drafts: true}}}
          : await invoke(command, args);
        scan = !changed; state.bootstrapReturned = true;
        return response;
      }
      return invoke(command, args);
    };
  }, {reason, changed});
}

export async function pressRetainedRecoveryControls(page: Page) {
  await page.evaluate(async () => {
    const state = (window as any).__recoveryReset;
    // Exercise even retained callbacks on detached nodes after an authority reset.
    state.buttons[0].click(); state.buttons[1].click();
    await new Promise(resolve => setTimeout(resolve, 0));
  });
}
