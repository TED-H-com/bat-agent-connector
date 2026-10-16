import {demoFixture} from '../tests/demo/index.ts';
import {locale} from './i18n.js';
// Keep native declarations compatible with the shared app. Native controls are
// unavailable in this static browser build; there is no network fallback.
export * from '../src/transport/index.ts';
export const nativeDesktop = false;
export const nativeFileSupport = false;
export const nativeBriefExportSupport = false;
export const browserSessionToken = 'public-demo';
export const restoreBrowserSession = async () => true;
export const forgetBrowserSession = async () => undefined;
export const browserAuthHeaders = () => ({});
export const demo = demoFixture({locale});
export const demoServer = 'public-demo';
export async function connectorRequest(method: string, path: string) {
  if (method !== 'GET') return {status: 403, data: {error: {code: 'DEMO_READ_ONLY',
    message: locale === 'zh-TW' ? '展示資料供瀏覽；請在自己的 Dashboard 執行操作。' : 'Explore the sample data here. Run actions in your own Dashboard.'}}};
  try {
    const response = await demo.dispatch({method, path});
    // A dedicated identity keeps tour preferences separate from other previews.
    if (path === '/bootstrap') {
      response.data.sync.server_id = demoServer;
      response.data.sync.principal_id = 'public-demo-reader';
    }
    const caps = path === '/bootstrap' ? response.data.capabilities : path === '/capabilities' ? response.data : null;
    if (caps) {
      caps.scopes = ['observe'];
      caps.actions = caps.actions.map((action: any) => ({...action, allowed: false}));
    }
    return response;
  } catch {
    return {status: 404, data: {error: {code: 'DEMO_DATA_UNAVAILABLE',
      message: locale === 'zh-TW' ? '這份展示沒有此項資料。' : 'This sample does not contain that record.'}}};
  }
}
export const connectorUploadArtifact = async () => ({status: 403, data: {error: {code: 'DEMO_READ_ONLY'}}});
