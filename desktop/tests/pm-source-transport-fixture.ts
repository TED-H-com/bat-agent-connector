import type {Page} from '@playwright/test';

// Source-only component harnesses import the shared durable form and its module
// graph, but do not mount Brief exports. Keep that optional native dependency
// explicit; shared-home tests still exercise the real browser/native transport.
export async function unavailableBriefTransport(page:Page,sourcePrefix:string) {
  await page.route(`**/${sourcePrefix}/transport/index.ts`,route=>route.fulfill({contentType:'text/javascript',body:
    "export const nativeDesktop=false,nativeBriefExportSupport=false;export function nativeBriefSave(){throw Error('Brief saving is not available in this component harness');}"}));
}
